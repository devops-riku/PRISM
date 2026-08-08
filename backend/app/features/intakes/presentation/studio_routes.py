"""The studio's half of a client request: mint it, watch the queue, send it.

Eight routes that all sit behind the gate, because every one of them is the
studio acting on a request - issuing a link, reading the queue, opening a file
the client attached, closing a request, handing over a prepared quotation, or
recovering the link when somebody lost it. Four of the eight are admin-only,
and each says in its own docstring which side of that line it is on and why.

The client's own door is not here. `/api/client/{token}` is reachable with no
`Authorization` header at all, is driven by a bearer token rather than an
account, and answers a deliberately opaque "gone" to everything it refuses -
a different set of rules for a different caller, and so a different module.
The two contexts meet only at the record they share.

The name to be careful with: this module is `app.presentation.api.intakes` and
the service it drives is `app.application.intakes`, imported as `intakes_module`
throughout, so that `intakes` never means two things one screen apart.
"""

from __future__ import annotations

import asyncio
import re
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.features.intakes.application import service as intakes_module
from app.features.intakes.infrastructure import files as intakefiles
from app.features.quotations.infrastructure import repository as storage
from app.features.team.infrastructure import mailer
from app.features.workspaces.application import settings
from app.shared.infrastructure import config
from app.shared.presentation.http import deps

router = APIRouter()


class IntakeRequest(BaseModel):
    """The PAD settings this intake will be quoted under - kind, currency,
    market region, tax basis, payment terms, tiers. No client words: from
    Stage 2 onward those arrive through the client's own link
    (`POST /api/client/{token}/submit`), never typed in by the studio.

    `preset` is a loose dict, deliberately not a typed sub-model - it mirrors
    `Intake.preset`, which is itself opaque storage for exactly this reason
    (see its own docstring), and the one field inside it this route actually
    reasons about (`currency`) is normalised below the same way `/api/proposals`
    normalises it. Nothing here validates the rest, because nothing downstream
    needs it validated yet: it is read back only to prefill the pad, by a
    later task, and revalidated in full - `kind`, `market_region`, the payment
    terms, `tiers` - the moment it is actually used to generate a quotation.
    """

    preset: dict = Field(default_factory=dict)


#: Where a client's link points. `#/c/<token>` is the hash route Stage 2's
#: client shell resolves ahead of the signed-in app - see
#: `docs/superpowers/plans/2026-08-03-client-intake-stage-2.md`'s Task 7. The
#: same shape `_invite_link` already uses for a teammate's invitation.
def _client_link(token: str) -> str:
    return f"{config.APP_ORIGIN.rstrip('/')}/#/c/{token}"


class IntakeIssued(intakes_module.Intake):
    """`Intake`'s own wire shape, plus the one thing `Intake.token`'s own
    `exclude=True` deliberately drops: a link the studio can actually hand to
    a client. Returned only by the two calls that mint a fresh token under an
    admin's own hand - `create_intake` and `relink_intake` - never by
    `list_intakes` or `read_intake`, which stay exactly as `Intake.token`'s
    docstring requires: any member may read the queue, so the credential that
    gates an unauthenticated route must not ride along with it.

    This is Task 1's deferred boundary (see `intakes.py`, `Intake.token`'s
    docstring, and the `RULING` in this branch's progress ledger): carry the
    storage shape as-is, and let Task 6 design the split with the split's own
    shape known. `IntakeIssued` inherits `token`'s exclusion unchanged rather
    than reintroducing it in cleartext - `link` is derived, additive, and
    built from the same field the wire has always hidden, not a second name
    for the secret itself.

    A dedicated `GET /api/intakes/{id}/link` was considered here and refused,
    on the grounds that `intakes.relink` already answers "the studio lost the
    link". IT WAS BUILT LATER ANYWAY, and the reasoning above was wrong in a
    way worth leaving visible rather than quietly editing out: relink does not
    RECOVER a link, it REPLACES one. That is the same thing only while nobody
    holds the old link. Once it has been sent - and sending it is the entire
    point of minting it - reissuing to get a copy silently breaks the link the
    client already has, possibly after they have opened it. "Recover" and
    "resend to a second contact" are different needs and only one of them is
    destructive. See `read_intake_link` below.
    """

    link: str = ""


def _issued(entry: intakes_module.Intake) -> IntakeIssued:
    """Wrap a just-minted or just-reissued intake with its link. `entry`'s own
    `token` never reaches `model_dump()` (`exclude=True`), so this cannot
    accidentally leak it under its own name - only `link`, built from it by
    hand, crosses the wire."""
    return IntakeIssued(**entry.model_dump(), link=_client_link(entry.token))


@router.post("/api/intakes", response_model=IntakeIssued, status_code=201, tags=["intakes"])
async def create_intake(request: Request, body: IntakeRequest) -> IntakeIssued:
    """Mint a client request from the studio's own PAD preset. Admin-only: an
    intake is the start of a price, and issuing one is nearer to inviting
    somebody than to drafting a quotation. This is deliberate rather than
    incidental - the gate's member/admin split (`_gate`, in
    `app/presentation/api/middleware.py`) only blocks a member's POST to
    `/api/settings` and `/api/team`, so
    without this call a member's POST here would go through unchecked.

    Starts `issued` with a token already minted (`intakes.create` does both),
    so the response can hand the studio a working link in the same call that
    creates the record - there is no second step to reach it, and no window
    where the intake exists but nothing points at it.
    """
    deps.require_admin(request, "Only an admin of this workspace can record a client request.")
    preset = dict(body.preset or {})
    if "currency" in preset:
        preset["currency"] = deps.normalise_currency(preset.get("currency", ""))
    try:
        entry = intakes_module.create(
            client_email="",
            client_phone="",
            scope="",
            budget_text="",
            preset=preset,
            created_by=deps.current_email(request),
        )
    except intakes_module.IntakeError as exc:
        # A brand-new intake has no existing state to conflict with - the only
        # way `create()` raises is that the record could not be written (a
        # full disk, a permissions problem). That is ours to report, not a
        # 409 implying the caller sent something to fix and retry.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _issued(entry)


@router.get("/api/intakes", response_model=List[intakes_module.Intake], tags=["intakes"])
async def list_intakes() -> List[intakes_module.Intake]:
    """The queue, newest first. Scoped to this workspace like everything else.

    No admin check: a member is exactly who turns a request into a
    quotation, so seeing the queue is theirs by default like the rest of
    `/api/`, and only admitting a new one is set apart above."""
    return intakes_module.listing()


@router.get("/api/intakes/{intake_id}", response_model=intakes_module.Intake, tags=["intakes"])
async def read_intake(intake_id: str) -> intakes_module.Intake:
    """One request. Open to members for the same reason the queue is; a
    malformed or unknown id reads as a plain 404 because `intakes.get`
    validates the id itself before any path is built."""
    entry = intakes_module.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    return entry


def _owned_attachment(entry: intakes_module.Intake, file_id: str) -> dict | None:
    """The manifest entry `file_id` names on `entry`, or `None`.

    Said plainly rather than left to be inferred: the *primary* cross-intake
    gate on this route is `intakefiles.read`'s own structural binding, below -
    it ties `(intake_id, file_id)` together via the actual storage key or
    local path, not by a comparison that could be written wrong, and
    check_intakefiles.py proves it exhaustively. This function is the
    route's own **second, independent** layer, sourced from this intake's own
    manifest rather than from storage: a file id belonging to a different
    intake, in this workspace or another, is simply not a member of *this*
    intake's own `attachments`, so it comes back `None` here before storage
    is ever asked a question - structurally, the same way `_quoted_bundle`
    ties a bundle id to the intake that may send it, rather than by a
    comparison a route could get subtly wrong.

    Its own name rather than inlined into the route, for the same reason
    `_quoted_bundle` has one: a check that can be disabled on its own is a
    check a test can prove is doing real work. See check_intakes_api.py's
    mutation proof, which simulates `intakefiles.read`'s own binding being
    absent before trusting a flip from 404 to 200 to mean anything about this
    function specifically - disabling only this function, with that binding
    intact, would still 404 on it and prove nothing about this route at all.
    """
    key = (file_id or "").strip().lower()
    if not key:
        return None
    for item in entry.attachments:
        if isinstance(item, dict) and str(item.get("id", "")).strip().lower() == key:
            return item
    return None


def _attachment_disposition(kind: str, name: str) -> str:
    """`inline` for the raster allowlist, `attachment` for everything else -
    `intakefiles.disposition_for` decides which, off the one content type
    this route already resolved from the manifest, so `INLINE_TYPES` is
    consulted in exactly one place rather than restated here.

    The filename is the one string in this response a client chose, not this
    studio. `intakefiles.clean_name` strips separators and control characters
    on the way onto the record but keeps `"`, so a file called `sco"pe.pdf`
    would close a naive `filename="..."` early and corrupt the header. Two
    forms rather than one: `filename=` is a plain-ASCII fallback for the RFC
    6266 grammar every client understands, with every `"`, backslash and
    control byte stripped rather than escaped - a fallback name only has to
    be recognisable, not exact, and escaping inside this particular header is
    its own can of worms. `filename*=UTF-8''...` (RFC 5987) is the real name,
    percent-encoded, and what every current browser actually opens or saves
    the file under.

    A name with no ASCII characters at all reduces the fallback to whatever
    ASCII is left over - `提案書.pdf` keeps only its extension and falls back
    to `.pdf`, and `提案書` alone (no extension survives to keep) falls back
    to the bare word `attachment`. Both are deliberate, not a gap: `filename*`
    still carries the real name correctly encoded, which is what every
    current browser actually reads, and the fallback's only job is to be a
    legal RFC 6266 token for whatever does not - never to be recognisable in
    every case.
    """
    disposition = intakefiles.disposition_for(kind)
    original = name or "attachment"
    ascii_only = original.encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r'[\\"\r\n]', "", ascii_only).strip() or "attachment"
    encoded = quote(original, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


@router.get("/api/intakes/{intake_id}/files/{file_id}", tags=["intakes"])
async def read_intake_file(intake_id: str, file_id: str) -> Response:
    """One file a client attached to their request - the half of this
    feature the studio asked for by name.

    No admin check: reading the queue is any member's, and `list_intakes`'s
    own docstring already settles that this is the same class of read.

    There is no presigned redirect here, on either backend - see
    `intakefiles.py`'s own docstring and the plan's "The revisit" section.
    A presigned GET answers no `Access-Control-Allow-Origin` (the bucket has
    no CORS rule, which is the default for every Space) and, carrying an
    `Authorization` header across the redirect, a `400` instead of a file on
    a browser that does not strip it - so a `307` here would be a response
    the studio's own browser can neither `fetch` nor navigate to. The bytes
    are read through `intakefiles.read` instead, in a thread: that call is a
    network round trip once Spaces is configured, this is `async def`, and a
    blocking socket call in it would park every request this worker holds,
    including an anonymous client's `/submit` - `asyncio.to_thread` runs it
    in a copy of the current context, so the workspace `_gate` already set
    for this request carries into the thread rather than being silently lost.

    The id pair is checked together, twice, independently, before a byte
    leaves storage: `_owned_attachment` against this intake's own manifest
    first, `intakefiles.read` second - see that function's own docstring for
    why one alone is not trusted to be enough.

    Every response sets `Cache-Control: no-store`, an explicit `Content-Type`
    read off the manifest rather than off the object, `Content-Disposition:
    inline` for `intakefiles.INLINE_TYPES` and `attachment` for everything
    else, and `X-Content-Type-Options: nosniff` on both branches - the
    exception that used to keep `nosniff` off the Spaces branch is retired
    along with the presigned redirect it existed for.
    """
    entry = intakes_module.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")

    record = _owned_attachment(entry, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="That file does not exist.")

    found = await asyncio.to_thread(intakefiles.read, intake_id, file_id)
    if found is None:
        # Covers a race with another request, and the ordinary case this
        # feature has to answer for on purpose: `close()` deletes the objects
        # but leaves `Intake.attachments` populated (see `intakes.py` and
        # Task 2 Step 6), so a closed intake's own manifest still names this
        # file right up until this line - and finds nothing behind it.
        raise HTTPException(status_code=404, detail="That file does not exist.")

    data, stored_type = found
    # The manifest's own kind wins over what storage reports. It is the value
    # `/submit` resolved through `intakefiles.resolve_type` in the first
    # place and the one the studio's queue already shows this file as;
    # `stored_type` is kept only as a fallback for an entry that somehow
    # lacks one, so this response is never inconsistent with the record that
    # named the file.
    #
    # Clamped against `intakefiles.CONTENT_TYPES` rather than trusted outright.
    # `attachments` is a bare `List[dict]` in `ADVANCE_FIELDS` with no
    # per-entry model (`Intake.attachments`'s own docstring says so), and
    # `/submit` is its only writer *today* - but "today" is a convention, not
    # a check, and `advance()` validates nothing about a dict's contents. An
    # entry carrying `{"kind": "text/html"}` is accepted by `advance()` right
    # now, and unclamped this route would answer `Content-Type: text/html` on
    # the studio's own origin for it. Still `attachment` for anything outside
    # `INLINE_TYPES`, and still `nosniff` either way, so that is not a live
    # hole - but the type actually served should be one this module chose to
    # store things as, not whatever a dict happens to say, on either branch:
    # `stored_type` is exactly as unclamped when it comes from Spaces' own
    # reported `ContentType`, which nothing here has verified either.
    content_type = str(record.get("kind") or stored_type or intakefiles.FALLBACK_TYPE)
    if content_type not in intakefiles.CONTENT_TYPES:
        content_type = intakefiles.FALLBACK_TYPE
    name = str(record.get("name") or "attachment")

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": _attachment_disposition(content_type, name),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/intakes/{intake_id}/close", response_model=intakes_module.Intake, tags=["intakes"])
async def close_intake(request: Request, intake_id: str) -> intakes_module.Intake:
    """Not going ahead. Reversible only by making a new request. Admin-only,
    the same side of the line as issuing one in the first place."""
    deps.require_admin(request, "Only an admin of this workspace can close a client request.")
    # Checked here rather than folded into the `except` below: `close()` raises
    # `IntakeError` both for an id that does not exist and for a write that
    # failed after it found one, and those are not the same answer. Deciding
    # "not found" here first means anything `close()` still raises has to be
    # the second kind - a real failure to save, which is a 500 that says so
    # rather than a 404 that sends someone looking for a request that is
    # actually still sitting there, just unmodified.
    if intakes_module.get(intake_id) is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    try:
        return intakes_module.close(intake_id, deps.current_email(request))
    except intakes_module.IntakeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _quoted_bundle(entry: intakes_module.Intake, bundle_id: str) -> bool:
    """Whether `bundle_id` is one this intake was actually quoted with, and
    that bundle still exists. Membership in `entry.bundle_ids` answers "did
    PRISM quote this for *this* request" - a real bundle id that belongs to
    a different intake must be refused exactly like one that was simply
    guessed - but membership alone is not sufficient: `DELETE
    /api/proposals/{id}` does not prune `bundle_ids`, so an id recorded at
    quote time can already be gone by the time a studio tries to send it.
    Sending a dangling id would move the intake to `sent` with
    `sent_bundle_id` naming nothing, and the client's own door would answer
    its opaque "gone" response forever - a state this intake can never
    escape by itself, since `relink` reissues a link, not a bundle. Checked
    by `storage.get`, not by re-validating anything about the bundle's own
    content - the only question here is whether there is still something to
    send. Its own name, rather than inlined into the route, so a test can
    disable it on its own to prove the route's refusal actually depends on
    it."""
    return bundle_id in entry.bundle_ids and storage.get(bundle_id) is not None


class IntakeSendRequest(BaseModel):
    """Which of a re-quotable intake's `bundle_ids` is the one being shown to
    the client, and the email that carries it.

    `bundle_id` is explicit rather than assumed (`bundle_ids[0]`, say): a second
    Generate pass replaces `bundle_ids` wholesale (see `intakes.ALLOWED`'s own
    docstring on `QUOTED: {PREPARING, ...}`), and by the time a studio is
    ready to send, more than one candidate can be on file.

    `subject` and `message` are what the studio read in the compose window
    before pressing Send, passed up rather than composed here so that the words
    on their screen and the words in the client's inbox cannot drift apart.
    Both optional: left empty, `_default_draft` writes them, which is what every
    caller that is not the compose window does.

    `notify` false sends nothing and only advances the state - the studio is
    delivering the link themselves. It is how an install with mail configured
    still gets the behaviour an install without it gets for free.
    """

    bundle_id: str = ""
    subject: str = ""
    message: str = ""
    notify: bool = True


def _default_draft(entry: intakes_module.Intake, link: str, studio: str) -> tuple[str, str]:
    """The subject and body used when a caller did not bring their own.

    The compose window always brings its own, so this is the fallback for
    everything else - a script, a future integration, a studio hitting the API
    directly. It is deliberately the same shape as the draft the window opens
    with, so the two do not read as coming from different products.
    """
    who = (entry.client_email or "").split("@")[0].strip()
    greeting = f"Hi {who}," if who else "Hello,"
    house = studio or "our studio"
    return (
        f"Your quotation from {house}",
        f"{greeting}\n\n"
        f"Your quotation is ready. You can read it here:\n\n"
        f"{link}\n\n"
        f"The link is yours alone - please do not forward it.\n\n"
        f"Thank you,\n{house}",
    )


@router.post("/api/intakes/{intake_id}/send", response_model=intakes_module.Intake, tags=["intakes"])
async def send_intake(request: Request, intake_id: str, body: IntakeSendRequest) -> intakes_module.Intake:
    """Hand a prepared quotation to the client: `quoted -> sent`, with
    `sent_bundle_id` naming which bundle and `sent_at` stamping when.
    Admin-only, the same side of the line as issuing and closing a request -
    this is the call that starts the client's own clock (`clientview.of`
    reads `sent_at`, never `bundle.created_at`, to tell them when they were
    actually shown something), so a member should not be able to trigger it
    alone.

    `bundle_id` is required and must already be one of this intake's own
    `bundle_ids`, AND still exist in `storage` - see `_quoted_bundle`'s own
    docstring, which is the actual contract this route enforces. A bundle id
    that is real but belongs to a different intake, or was simply guessed,
    or once belonged here but was since deleted (`DELETE /api/proposals/{id}`
    does not prune `bundle_ids`), is refused exactly like one that names
    nothing.
    """
    deps.require_admin(request, "Only an admin of this workspace can send a quotation to a client.")
    bundle_id = (body.bundle_id or "").strip()
    if not bundle_id:
        raise HTTPException(status_code=400, detail="Say which bundle to send.")

    entry = intakes_module.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    if not _quoted_bundle(entry, bundle_id):
        raise HTTPException(
            status_code=400,
            detail="That bundle was not quoted for this request.",
        )

    # The email goes FIRST, and the state only moves once Resend has taken it.
    # The other order is tempting - advance, then mail, so a mail outage cannot
    # strand a studio mid-queue - and it is wrong: it makes `sent` mean "we
    # tried", and a client who received nothing then looks exactly like one who
    # is reading their quotation. Everything downstream trusts `sent`
    # (`clientview.of` starts the client's clock from `sent_at`), so it has to
    # be true. A failure here leaves the row `quoted`, which is recoverable by
    # pressing the button again; the reverse is not recoverable at all.
    #
    # Skipped entirely when this install has no mail configured, which is the
    # behaviour every install had before this route could send anything: the
    # link is the record, the email is only how it travels, and a studio
    # without a Resend key still has a queue that moves.
    if body.notify and mailer.configured():
        if not (entry.client_email or "").strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "This request has no client email address, so there is nowhere to "
                    "send it. Copy the link and send it yourself."
                ),
            )
        studio = settings.load().studio_name
        subject = (body.subject or "").strip()
        message = (body.message or "").strip()
        if not subject or not message:
            fallback_subject, fallback_message = _default_draft(
                entry, _client_link(entry.token), studio
            )
            subject = subject or fallback_subject
            message = message or fallback_message
        try:
            mailer.send_quotation(
                to=entry.client_email.strip(),
                subject=subject,
                message=message,
                studio=studio,
            )
        except mailer.MailError as exc:
            # 502 rather than 500: nothing in PRISM is broken - a service it
            # depends on would not take the message. The studio's own words are
            # still in the compose window, and Copy is still beside the button
            # that failed.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        return intakes_module.advance(
            intake_id,
            intakes_module.SENT,
            sent_bundle_id=bundle_id,
            sent_at=storage.utc_now_iso(),
        )
    except intakes_module.IntakeWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except intakes_module.IntakeError as exc:
        # Not a brand-new record like `create_intake`'s catch above - this one
        # already exists, so anything `advance` still refuses is a real state
        # conflict (not `quoted`, most likely a second send) rather than a
        # write failure, and 409 says that plainly instead of implying a 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class IntakeLink(BaseModel):
    """Just the link. Not `IntakeIssued`, which carries a whole intake record
    the caller is already holding - and carrying the record again would make
    this route look like a way to read an intake, which it is not. It is a way
    to read one secret, and the shape says so."""

    link: str = ""


@router.get("/api/intakes/{intake_id}/link", response_model=IntakeLink, tags=["intakes"])
async def read_intake_link(request: Request, intake_id: str) -> IntakeLink:
    """The client's CURRENT link, without minting a new one.

    Admin-only, the same side of the line as `relink_intake` - this hands back
    the bearer credential that opens an unauthenticated route, and the fact
    that reading is less destructive than reissuing does not make it less
    sensitive. If anything the gate matters more here: `relink` at least leaves
    evidence, because the old link stops working and somebody notices.

    WHY THIS EXISTS WHEN `relink` LOOKED LIKE ENOUGH. Reissuing is how a studio
    was meant to recover a link, and it works exactly once - the first time,
    before the client has it. After that, pressing Reissue to get a copy of the
    link kills the copy the client is holding. A studio wanting to forward the
    same link to a second person at the client had no way to do it that did not
    break the first person's.

    THE QUEUE STILL CARRIES NO LINKS. That property is `Intake.token`'s
    `exclude=True` and it is untouched: `list_intakes` and `read_intake` return
    exactly what they always did, so a screenshot of the queue, an export, or a
    member reading the list still discloses nothing. This is a separate,
    deliberate, single-purpose call that an admin has to make on purpose, for
    one intake at a time. That is a different posture from putting the token in
    the list, and it is the one that keeps the original reasoning intact.

    404 rather than 403 for a closed or expired intake, and rather than
    answering an empty string: `close()` blanks the token on the record, so
    there is genuinely nothing to hand back, and a route that answers `{"link":
    ""}` invites a caller to paste an empty string into an email.
    """
    deps.require_admin(request, "Only an admin of this workspace can read a client's link.")
    entry = intakes_module.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    if not entry.token:
        raise HTTPException(
            status_code=404,
            detail="That request has no live link. Closing one ends its link for good.",
        )
    # Never logged, at any level. The one rule this whole feature has about
    # tokens, and a `logger.info("handed back %s", link)` here would undo the
    # `exclude=True` that every other route relies on.
    return IntakeLink(link=_client_link(entry.token))


@router.post("/api/intakes/{intake_id}/relink", response_model=IntakeIssued, tags=["intakes"])
async def relink_intake(request: Request, intake_id: str) -> IntakeIssued:
    """Reissue a client's link, killing the one before it outright - a link a
    client lost, or one a studio wants to resend once `intakes.LIFETIME_DAYS`
    is getting close. Admin-only, the same side of the line as minting the
    first link in the first place.

    Existence is checked here, ahead of the call, for the same reason
    `close_intake` checks it first: `intakes.relink` raises plain
    `IntakeError` for "does not exist" and for "closed, nothing to reissue"
    alike, and only the second of those is this route's business to report
    once existence is no longer in question. `IntakeWriteError` - `_write`'s
    own wrapped `OSError` - is caught ahead of that generic case for the same
    reason `_client_advance` catches it first: a failed save is not a refusal
    and must not be reported as one.
    """
    deps.require_admin(request, "Only an admin of this workspace can reissue a client's link.")
    if intakes_module.get(intake_id) is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    try:
        reissued = intakes_module.relink(intake_id)
    except intakes_module.IntakeWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except intakes_module.IntakeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _issued(reissued)
