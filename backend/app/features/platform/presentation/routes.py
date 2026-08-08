"""The leftovers - reference data, the notification mailbox, the health probe.

These three had nowhere better to go. The currency list is a constant the
front end reads once; the mailbox is where every other context posts what it
just did; `/api/health` answers a load balancer. Nothing here is a domain, and
putting them in one file is an admission of that rather than a claim about it.

If a fourth thing arrives that also has nowhere to go, it can live here too -
but two of these growing into something with rules of its own is the signal to
give it its own module and leave the rest behind.
"""

from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.features.notifications.infrastructure import hub, inbox
from app.features.team.infrastructure import auth, mailer, members
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import config
from app.shared.presentation.http.deps import CURRENCIES, CurrencyOption

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    model: str
    key_configured: bool
    #: Whether this install can send email at all. Beside `key_configured`
    #: because it is the same kind of fact - a capability this server either
    #: has or does not - and the compose window needs it before it can decide
    #: whether to offer Send or only Copy. Like `key_configured` it says
    #: whether a key exists and never anything about the key itself.
    mail_configured: bool = False


@router.get("/api/currencies", response_model=List[CurrencyOption], tags=["reference"])
async def list_currencies() -> List[CurrencyOption]:
    return CURRENCIES


class NoteView(BaseModel):
    """One thing that happened, as told to you."""

    id: str = ""
    kind: str = ""
    at: str = ""
    title: str = ""
    body: str = ""
    href: str = ""
    read_at: str = ""


class Mailbox(BaseModel):
    unread: int = 0
    notes: List[NoteView] = Field(default_factory=list)


class ReadRequest(BaseModel):
    through: str = Field(default="", description="Mark everything at or older than this stamp.")
    ids: List[str] = Field(default_factory=list, description="Or mark exactly these.")


@router.get("/api/notifications", response_model=Mailbox, tags=["notifications"])
async def read_notifications(limit: int = 30) -> Mailbox:
    """Your own mail in the workspace the header names, newest first.

    No filtering happens here: the audience was resolved when each note was
    written, so what comes back is already only what you are meant to know.
    """
    return Mailbox(
        unread=inbox.unread(),
        notes=[NoteView(**note.model_dump()) for note in inbox.listing(limit)],
    )


@router.post("/api/notifications/read", response_model=Mailbox, tags=["notifications"])
async def mark_notifications_read(body: ReadRequest) -> Mailbox:
    """Mark mail read. Idempotent, and there is no way back to unread."""
    inbox.mark_read(through=body.through, ids=body.ids)
    return Mailbox(
        unread=inbox.unread(),
        notes=[NoteView(**note.model_dump()) for note in inbox.listing(30)],
    )


@router.delete("/api/notifications", response_model=Mailbox, tags=["notifications"])
async def clear_notifications() -> Mailbox:
    """Drop what you have read. Unread notes stay - clearing is not reading."""
    inbox.clear_read()
    return Mailbox(
        unread=inbox.unread(),
        notes=[NoteView(**note.model_dump()) for note in inbox.listing(30)],
    )


#: How long a socket waits for its first frame before giving up. A connection
#: that never says who it is has nothing to be told.
HANDSHAKE_SECONDS = 10
#: A frame every half minute keeps intermediaries from tidying an idle socket
#: away, and tells the client the line is still live.
HEARTBEAT_SECONDS = 30


@router.websocket("/api/notifications/stream")
async def notification_stream(socket: WebSocket) -> None:
    """Push notifications as they are written, rather than up to 20s later.

    The token arrives in the FIRST FRAME, not the query string. A browser
    cannot set headers on a WebSocket, and the obvious alternative - putting the
    access token in the URL - writes a live session into the server's access log
    and the browser's history, which is the trade this app already refused for
    file downloads.

    Nothing here is a delivery guarantee. The client keeps a slow poll, so a
    dropped socket costs latency rather than a lost notification.
    """
    await socket.accept()

    try:
        hello = await asyncio.wait_for(socket.receive_json(), timeout=HANDSHAKE_SECONDS)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await socket.close(code=1008)
        return

    workspace = str(hello.get("workspace", "") or "")
    token = str(hello.get("token", "") or "")

    if auth.required():
        try:
            user = auth.verify(token)
        except auth.AuthError as exc:
            await socket.send_json({"error": str(exc)})
            await socket.close(code=1008)
            return
        email, user_id = user.email, user.id
    else:
        email, user_id = "", ""

    # Same scoping as every other call: the workspace named, the person on the
    # token, and the roster consulted - a socket must not be a way around a
    # membership check.
    workspaces.use(workspace)
    inbox.use_identity(email, user_id)
    if auth.required():
        roster = members.listing()
        if roster and not members.is_member(email, user_id):
            await socket.send_json({"error": "You are not on this workspace's team."})
            await socket.close(code=1008)
            return

    room = workspaces.current()
    person = inbox.current_key()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    hub.subscribe(room, person, queue)

    # What is already waiting, so a fresh socket does not have to wait for the
    # next event to know where it stands.
    await socket.send_json({"ready": True, "unread": inbox.unread()})

    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await socket.send_json({"beat": True})
                continue
            await socket.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(room, person, queue)


@router.get("/api/health", response_model=HealthResponse, tags=["reference"])
async def health() -> HealthResponse:
    """Never returns the key itself - only whether one is present."""
    return HealthResponse(
        status="ok",
        model=config.GEMINI_MODEL,
        key_configured=config.key_configured(),
        mail_configured=mailer.configured(),
    )
