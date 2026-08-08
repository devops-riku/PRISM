"""Workspaces, accounts and the people on a team.

A workspace is a whole book - its own settings, rates, terms, numbering and
files - and the routes here are the ones that make books, name them, throw them
away, and decide who may open which. Everything else in the API works inside
whichever workspace the middleware has already opened; this is the only module
that reaches across them, which is why `_require_admin_of` asks about a named
workspace rather than the current one.

The sign-in routes sit here too. `/api/auth/config` is the one endpoint that
answers before anybody has a token, because it is what tells a client whether
to ask for one.
"""

from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.features.jobs.application import service as jobs
from app.features.notifications.infrastructure import inbox
from app.features.team.infrastructure import auth, mailer, members
from app.features.workspaces.application import settings
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import config, database
from app.shared.presentation.http import deps

router = APIRouter()


class WorkspaceView(BaseModel):
    """One workspace, with enough about it to choose between two."""

    id: str
    name: str = ""
    created_at: str = ""
    quotations: int = 0
    proposals: int = 0
    studio_name: str = Field(
        default="", description="What this workspace's own settings call the studio."
    )


class WorkspaceRequest(BaseModel):
    name: str = ""


def _require_admin_of(request: Request, workspace_id: str) -> None:
    """Refuse anyone who is not an admin of *that* workspace.

    Deliberately not the role in whichever workspace happens to be open: being
    an admin of your own book says nothing about somebody else's.
    """
    if not auth.required():
        return
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")

    borrowed = workspaces.borrow(workspace_id)
    try:
        roster = members.listing()
        role = members.role_of(user.email, user.id)
    finally:
        workspaces.give_back(borrowed)

    if roster and role != members.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only an admin of that workspace can rename or delete it.",
        )


def _view_workspace(workspace: workspaces.Workspace) -> WorkspaceView:
    """Count SQL records without loading their aggregate payloads.

    Borrowed rather than switched: this runs inside a request that has already
    named its own workspace, and putting the context back exactly as it was is
    the only way that request survives being listed against.
    """
    token = workspaces.borrow(workspace.id)
    try:
        quotations = database.count(workspace.id, "quotation_bundle")
        proposals = database.count(workspace.id, "proposal_document")
        studio = settings.load().studio_name
    finally:
        workspaces.give_back(token)

    return WorkspaceView(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        quotations=quotations,
        proposals=proposals,
        studio_name=studio,
    )


@router.get("/api/workspaces", response_model=List[WorkspaceView], tags=["workspaces"])
async def list_workspaces(request: Request) -> List[WorkspaceView]:
    """The workspaces you are on, oldest first.

    Not every workspace on the install: a team you have not been invited to is
    not a locked door you can see, it is a door you cannot see. An unclaimed
    workspace is listed for everyone, because it belongs to nobody until its
    first visitor claims it - that is what makes a fresh install usable.
    """
    user = getattr(request.state, "user", None)
    found = []
    for workspace in workspaces.listing():
        if user is not None:
            borrowed = workspaces.borrow(workspace.id)
            try:
                roster = members.listing()
                yours = not roster or members.is_member(user.email, user.id)
            finally:
                workspaces.give_back(borrowed)
            if not yours:
                continue
        found.append(_view_workspace(workspace))
    return found


@router.post("/api/workspaces", response_model=WorkspaceView, status_code=201, tags=["workspaces"])
async def create_workspace(request: Request, body: WorkspaceRequest) -> WorkspaceView:
    """Start a new book: its own settings, rates, terms, numbering and files.

    Whoever makes it administers it. Anyone signed in may make one - it is their
    own team, not a change to somebody else's.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A workspace needs a name.")

    made = workspaces.create(name)
    user = getattr(request.state, "user", None)
    # No notification here: you are the only person in a workspace you just
    # made, and telling somebody what they did one second ago is noise.
    if user is not None:
        borrowed = workspaces.borrow(made.id)
        try:
            members.claim(user.email, user.id)
        finally:
            workspaces.give_back(borrowed)
    return _view_workspace(made)


@router.patch("/api/workspaces/{workspace_id}", response_model=WorkspaceView, tags=["workspaces"])
async def rename_workspace(
    request: Request, workspace_id: str, body: WorkspaceRequest
) -> WorkspaceView:
    """Rename a workspace. Its id, and so everything filed under it, is unchanged."""
    _require_admin_of(request, workspace_id)
    renamed = workspaces.rename(workspace_id, body.name)
    if renamed is None:
        raise HTTPException(status_code=404, detail=f"No workspace called {workspace_id!r}.")
    return _view_workspace(renamed)


@router.delete("/api/workspaces/{workspace_id}", status_code=204, tags=["workspaces"])
async def delete_workspace(request: Request, workspace_id: str) -> Response:
    """Delete a workspace and every quotation, proposal and setting in it.

    Not recoverable, and the last one goes the same way as the rest: an install
    with no workspace is a valid state, and the client asks for a name before
    anything can be filed again.
    """
    if not workspaces.exists(workspace_id):
        raise HTTPException(status_code=404, detail=f"No workspace called {workspace_id!r}.")
    _require_admin_of(request, workspace_id)

    # Work in flight is written when it finishes, not now. Deleting the folder
    # under a running job leaves it to recreate the directory on its way out and
    # file a quotation into a workspace that no longer exists - visible to
    # nobody, deletable by nobody.
    token = workspaces.borrow(workspace_id)
    try:
        busy = [job for job in jobs.listing(200) if job.state in {"queued", "running"}]
    finally:
        workspaces.give_back(token)
    if busy:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(busy)} job(s) are still running in this workspace. Wait for them to "
                "finish, then delete it."
            ),
        )

    if not workspaces.delete(workspace_id):
        raise HTTPException(status_code=404, detail=f"No workspace called {workspace_id!r}.")
    return Response(status_code=204)


class AuthConfig(BaseModel):
    """What the client needs before it can show a sign-in screen."""

    required: bool = Field(
        description="False means this install has no accounts and answers everyone."
    )
    url: str = Field(default="", description="The Supabase project URL, or empty.")
    anon_key: str = Field(
        default="",
        description="The publishable key. Public by design; the secret is never sent.",
    )


@router.get("/api/auth/config", response_model=AuthConfig, tags=["accounts"])
async def auth_config() -> AuthConfig:
    """Whether anybody has to sign in here, and which project to sign in to.

    Answered without a token, because it is what tells a client whether to ask
    for one. It carries the publishable key and never the secret.
    """
    return AuthConfig(**auth.describe())


@router.get("/api/auth/me", tags=["accounts"])
async def auth_me(request: Request) -> dict:
    """Who the server thinks you are - the token's own claims, verified.

    On an install that requires a sign-in this is only ever reached with a
    verified token, so it answers with the email that token carries. On one
    without accounts it says so plainly rather than inventing a user.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return {"signed_in": False, "required": auth.required()}
    return {"signed_in": True, "required": True, "id": user.id, "email": user.email}


class MemberView(BaseModel):
    email: str = ""
    role: str = "member"
    added_at: str = ""
    you: bool = Field(default=False, description="True for the person asking.")


class InviteView(BaseModel):
    email: str = ""
    role: str = "member"
    invited_by: str = ""
    expires_at: str = ""
    link: str = Field(description="Where the invitation points. Send it however you like.")
    emailed: bool = Field(default=False, description="Whether Resend accepted the message.")
    problem: str = Field(default="", description="Why it was not emailed, if it was not.")


class TeamView(BaseModel):
    """One workspace's people, and what the person asking may do."""

    workspace: str = ""
    name: str = ""
    your_role: str = ""
    members: List[MemberView] = Field(default_factory=list)
    invites: List[InviteView] = Field(default_factory=list)
    email_configured: bool = False


class InviteRequest(BaseModel):
    email: str = ""
    role: str = "member"


class RoleRequest(BaseModel):
    role: str = "member"


def _invite_link(token: str) -> str:
    return f"{config.APP_ORIGIN.rstrip('/')}/#/invite/{token}"


def _team(request: Request) -> TeamView:
    user = deps.current_user(request)
    email = (user.email if user else "").lower()
    workspace = workspaces.current()
    named = next((item for item in workspaces.listing() if item.id == workspace), None)

    return TeamView(
        workspace=workspace,
        name=named.name if named else workspace,
        your_role=getattr(request.state, "role", "") or (members.ADMIN if not auth.required() else ""),
        members=[
            MemberView(
                email=member.email,
                role=member.role,
                added_at=member.added_at,
                you=member.email.lower() == email,
            )
            for member in members.listing()
        ],
        invites=[
            InviteView(
                email=entry.email,
                role=entry.role,
                invited_by=entry.invited_by,
                expires_at=entry.expires_at,
                link=_invite_link(entry.token),
            )
            for entry in members.invites()
        ],
        email_configured=mailer.configured(),
    )


@router.get("/api/team", response_model=TeamView, tags=["team"])
async def read_team(request: Request) -> TeamView:
    """Who is on the workspace you are in, and what you may do in it."""
    return _team(request)


@router.post("/api/team/claim", response_model=TeamView, tags=["team"])
async def claim_workspace(request: Request) -> TeamView:
    """Take charge of a workspace nobody administers yet.

    Only ever succeeds on an empty roster, which is the state a workspace made
    before teams existed is in. It is a button somebody presses rather than
    something that happens because they opened a page - the difference between
    a studio claiming its own book and the first passer-by inheriting it.
    """
    user = deps.current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    if members.listing():
        raise HTTPException(
            status_code=409,
            detail="This workspace already has a team. Ask one of its admins for an invitation.",
        )

    members.claim(user.email, user.id)
    request.state.role = members.role_of(user.email, user.id)
    return _team(request)


@router.post("/api/team/invites", response_model=InviteView, status_code=201, tags=["team"])
async def invite_member(request: Request, body: InviteRequest) -> InviteView:
    """Offer somebody a place, and email them the link.

    The invitation is the record; the email is only how it travels. If Resend is
    not configured, or refuses, the invitation still exists and its link comes
    back with the reason - so an invite is never lost to a mail problem.
    """
    deps.require_admin(request)
    user = deps.current_user(request)

    try:
        entry = members.invite(body.email, body.role, user.email if user else "")
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    link = _invite_link(entry.token)
    studio = settings.load().studio_name
    named = next(
        (item for item in workspaces.listing() if item.id == workspaces.current()), None
    )

    emailed, problem = False, ""
    if mailer.configured():
        try:
            await asyncio.to_thread(
                mailer.send_invite,
                to=entry.email,
                studio=studio,
                workspace=named.name if named else workspaces.current(),
                inviter=user.email if user else "",
                role=entry.role,
                link=link,
            )
            emailed = True
        except mailer.MailError as exc:
            problem = str(exc)
            deps.logger.warning("Invitation to %s was not emailed: %s", entry.email, exc)
    else:
        problem = "No email is configured, so send the link yourself."

    inbox.notify(
        "member_invited",
        inbox.ADMINS,
        lambda role, you: {
            "title": f"{entry.email} was invited",
            "body": (
                f"As {'an admin' if entry.role == members.ADMIN else 'a member'}"
                + (", and the email was sent." if emailed else ", but the email was not sent.")
            ),
            "href": "#/workspaces",
        },
    )

    return InviteView(
        email=entry.email,
        role=entry.role,
        invited_by=entry.invited_by,
        expires_at=entry.expires_at,
        link=link,
        emailed=emailed,
        problem=problem,
    )


@router.delete("/api/team/invites/{token}", status_code=204, tags=["team"])
async def revoke_invite(request: Request, token: str) -> Response:
    """Withdraw an invitation that has not been taken up."""
    deps.require_admin(request)
    if not members.revoke(token):
        raise HTTPException(status_code=404, detail="No such invitation.")
    return Response(status_code=204)


@router.patch("/api/team/members/{email}", response_model=MemberView, tags=["team"])
async def change_role(request: Request, email: str, body: RoleRequest) -> MemberView:
    """Make somebody an admin, or take it back."""
    deps.require_admin(request)
    try:
        changed = members.set_role(email, body.role)
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    named = next(
        (item for item in workspaces.listing() if item.id == workspaces.current()), None
    )
    where = named.name if named else workspaces.current()
    inbox.notify(
        "role_changed",
        [changed.email],
        {
            "title": (
                f"You are now an admin of {where}"
                if changed.role == members.ADMIN
                else f"Your role in {where} is now member"
            ),
            "body": (
                "You can change the studio's settings and delete work."
                if changed.role == members.ADMIN
                else "You can prepare quotations and proposals. Settings and deleting are an admin's."
            ),
            "href": "#/profile",
        },
    )
    return MemberView(email=changed.email, role=changed.role, added_at=changed.added_at)


@router.delete("/api/team/members/{email}", status_code=204, tags=["team"])
async def remove_member(request: Request, email: str) -> Response:
    """Take somebody off the team. Their work stays; the workspace is not theirs."""
    deps.require_admin(request)
    named = next(
        (item for item in workspaces.listing() if item.id == workspaces.current()), None
    )
    where = named.name if named else workspaces.current()

    try:
        if not members.remove(email):
            raise HTTPException(status_code=404, detail=f"{email} is not on this team.")
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Told after the fact, and told to them: somebody who can no longer open a
    # workspace should learn it from a sentence rather than from a 403.
    inbox.deliver(
        inbox.key_for(email),
        "removed_from_team",
        {
            "title": f"You were removed from {where}",
            "body": "Your own work is untouched. Ask an admin there if this was not intended.",
            "href": "#/workspaces",
        },
    )
    inbox.notify(
        "member_removed",
        inbox.ADMINS,
        {"title": f"{email} was removed from {where}", "body": "", "href": "#/workspaces"},
    )
    return Response(status_code=204)


class InvitePreview(BaseModel):
    """What an invitation says, before anybody accepts it."""

    workspace: str = ""
    name: str = ""
    email: str = ""
    role: str = "member"
    invited_by: str = ""
    expires_at: str = ""
    valid: bool = True
    problem: str = ""


@router.get("/api/invites/{token}", response_model=InvitePreview, tags=["team"])
async def read_invite(token: str) -> InvitePreview:
    """What this link is for. Answered to anyone holding it, which is the point."""
    found = members.find_invite(token)
    if found is None:
        return InvitePreview(valid=False, problem="That invitation is not valid, or has been used.")

    workspace_id, entry = found
    named = next((item for item in workspaces.listing() if item.id == workspace_id), None)
    return InvitePreview(
        workspace=workspace_id,
        name=named.name if named else workspace_id,
        email=entry.email,
        role=entry.role,
        invited_by=entry.invited_by,
        expires_at=entry.expires_at,
        valid=not entry.spent,
        problem="That invitation has expired." if entry.spent else "",
    )


@router.post("/api/invites/{token}/accept", response_model=TeamView, tags=["team"])
async def accept_invite(request: Request, token: str) -> TeamView:
    """Join the workspace this invitation is for."""
    user = deps.current_user(request)
    if auth.required() and user is None:
        raise HTTPException(status_code=401, detail="Sign in first, then accept the invitation.")

    found = members.find_invite(token)
    if found is None:
        raise HTTPException(status_code=404, detail="That invitation is not valid, or has been used.")

    workspace_id, _entry = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        members.accept(token, user.email if user else "", user.id if user else "")
        joined = user.email if user else "Somebody"
        inbox.notify(
            "member_joined",
            inbox.OTHERS,
            {
                "title": f"{joined} joined the team",
                "body": "",
                "href": "#/workspaces",
            },
            actor_email=user.email if user else "",
        )
        request.state.role = members.role_of(user.email if user else "", user.id if user else "")
        return _team(request)
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        workspaces.give_back(borrowed)
