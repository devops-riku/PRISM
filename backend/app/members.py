"""Who belongs to a workspace, and who has been asked to.

A workspace was a folder anyone signed in could open. This makes it a team: it
has members, one of whom administers it, and an outsider gets a 403 rather
than a polite absence from a list. The roster lives in the workspace's own folder for
the same reason everything else does - a workspace is a thing you can copy,
back up or delete as one piece, and a membership table in some other system
would be the one part that did not travel with it.

Two rules are worth stating plainly, because both are choices:

  * **An empty roster means unclaimed, not open.** The first signed-in person to
    open a workspace with no members becomes its admin. That is how the
    workspaces that existed before teams did get an owner without anybody
    editing a file by hand, and it is why nobody is locked out of their own
    work by this feature arriving.
  * **The last admin cannot be removed or demoted.** A workspace with no admin
    is one nobody can invite into, configure or delete - a locked room with the
    work still inside.

Invitations are links with a token, not accounts. Nothing is created for an
invited person until they sign in and accept; until then the invite is a row
saying "this email may join", and it expires.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import List

from pydantic import BaseModel, Field

from app import workspaces

logger = logging.getLogger("prism.members")

__all__ = [
    "Member",
    "Invite",
    "Roster",
    "load",
    "listing",
    "invites",
    "is_member",
    "role_of",
    "claim",
    "invite",
    "accept",
    "remove",
    "find_invite",
    "MemberError",
]

FILENAME = "members.json"

#: Two roles, because two is what the difference is actually about: whether you
#: can change what the studio charges and throw work away, or not. An admin sees
#: every screen; a member works in the app but cannot reach Settings and cannot
#: delete anything - not a quotation, not a proposal, not the workspace.
ADMIN = "admin"
MEMBER = "member"
ROLES = (ADMIN, MEMBER)

#: How long an invitation is good for. Long enough to survive a weekend, short
#: enough that a forwarded link from March is not a way in.
INVITE_DAYS = 14

_lock = threading.RLock()


class MemberError(RuntimeError):
    """The change asked for would leave the workspace in a state nobody wants."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _later(days: int) -> str:
    when = datetime.now(timezone.utc) + timedelta(days=days)
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _expired(stamp: str) -> bool:
    try:
        when = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return when < datetime.now(timezone.utc)


class Member(BaseModel):
    """One person on a workspace."""

    email: str = Field(default="", description="What they signed in as. The identity that counts.")
    user_id: str = Field(default="", description="Their Supabase id, once they have signed in.")
    role: str = Field(default=MEMBER, description="admin | member")
    added_at: str = ""


class Invite(BaseModel):
    """A standing offer for one email address to join."""

    token: str = ""
    email: str = ""
    role: str = MEMBER
    invited_by: str = ""
    created_at: str = ""
    expires_at: str = ""

    @property
    def spent(self) -> bool:
        return _expired(self.expires_at)


class Roster(BaseModel):
    members: List[Member] = Field(default_factory=list)
    invites: List[Invite] = Field(default_factory=list)


def _path():
    return workspaces.root() / FILENAME


def load() -> Roster:
    """This workspace's roster. A missing file is an unclaimed workspace."""
    path = _path()
    if not path.is_file():
        return Roster()
    try:
        return Roster.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable roster at %s: %s", path, exc)
        return Roster()


def _save(roster: Roster) -> Roster:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(roster.model_dump_json(indent=2), encoding="utf-8")
    return roster


def listing() -> List[Member]:
    return load().members


def invites() -> List[Invite]:
    """Invitations still worth showing: unexpired, and not already members."""
    roster = load()
    emails = {member.email.lower() for member in roster.members}
    return [
        entry
        for entry in roster.invites
        if not entry.spent and entry.email.lower() not in emails
    ]


def _match(member: Member, email: str, user_id: str) -> bool:
    if user_id and member.user_id and member.user_id == user_id:
        return True
    return bool(email) and member.email.lower() == email.lower()


def is_member(email: str, user_id: str = "") -> bool:
    return any(_match(member, email, user_id) for member in load().members)


def role_of(email: str, user_id: str = "") -> str:
    for member in load().members:
        if _match(member, email, user_id):
            return member.role
    return ""


def claim(email: str, user_id: str = "") -> Member | None:
    """Make this person the admin, if the workspace has no members yet.

    Returns the new admin, or None when somebody already runs it. Idempotent:
    two tabs opening the same unclaimed workspace do not produce two admins.
    """
    if not email and not user_id:
        return None

    with _lock:
        roster = load()
        if roster.members:
            return None
        admin = Member(email=email, user_id=user_id, role=ADMIN, added_at=_now())
        roster.members = [admin]
        _save(roster)

    logger.info("Workspace %s claimed by %s", workspaces.current(), email or user_id)
    return admin


def remember_id(email: str, user_id: str) -> None:
    """Record the Supabase id against an invited email, once they sign in.

    An invitation names an email because that is all the inviter knows. The id
    is what survives a change of address, so it is written down the first time
    the two are seen together.
    """
    if not (email and user_id):
        return
    with _lock:
        roster = load()
        changed = False
        for member in roster.members:
            if member.email.lower() == email.lower() and not member.user_id:
                member.user_id = user_id
                changed = True
        if changed:
            _save(roster)


def invite(email: str, role: str, invited_by: str) -> Invite:
    """Offer one email address a place. Replaces any outstanding offer to them."""
    address = (email or "").strip().lower()
    if "@" not in address:
        raise MemberError(f"{email!r} is not an email address.")
    if role not in ROLES:
        role = MEMBER

    with _lock:
        roster = load()
        if any(member.email.lower() == address for member in roster.members):
            raise MemberError(f"{address} is already on this team.")

        entry = Invite(
            token=secrets.token_urlsafe(24),
            email=address,
            role=role,
            invited_by=invited_by,
            created_at=_now(),
            expires_at=_later(INVITE_DAYS),
        )
        roster.invites = [
            item for item in roster.invites if item.email.lower() != address and not item.spent
        ] + [entry]
        _save(roster)

    logger.info("Invited %s to workspace %s as %s", address, workspaces.current(), role)
    return entry


def find_invite(token: str) -> tuple[str, Invite] | None:
    """Which workspace an invitation is for, searched across all of them.

    An invitation link carries a token and nothing else - deliberately, since a
    workspace id in a URL would tell an outsider what exists here. So accepting
    one means looking for it, which is a scan of one small file per workspace.
    """
    wanted = (token or "").strip()
    if not wanted:
        return None

    previous = workspaces.borrow(workspaces.current())
    try:
        for workspace in workspaces.listing():
            workspaces.borrow(workspace.id)
            for entry in load().invites:
                if secrets.compare_digest(entry.token, wanted):
                    return workspace.id, entry
    finally:
        workspaces.give_back(previous)
    return None


def accept(token: str, email: str, user_id: str) -> Member:
    """Take up an invitation in the workspace it belongs to.

    The caller is expected to have switched to that workspace already - see
    `find_invite`, which says which one it is.
    """
    with _lock:
        roster = load()
        for entry in roster.invites:
            if not secrets.compare_digest(entry.token, (token or "").strip()):
                continue
            if entry.spent:
                raise MemberError("That invitation has expired. Ask for another.")

            # The email on the invitation is the offer. Signing in as somebody
            # else and using their link would be a different person joining.
            if email and entry.email.lower() != email.lower():
                raise MemberError(
                    f"That invitation was sent to {entry.email}. Sign in as them to accept it."
                )

            member = Member(
                email=entry.email, user_id=user_id, role=entry.role, added_at=_now()
            )
            roster.members = [
                item for item in roster.members if item.email.lower() != entry.email.lower()
            ] + [member]
            roster.invites = [item for item in roster.invites if item.token != entry.token]
            _save(roster)
            logger.info("%s joined workspace %s", entry.email, workspaces.current())
            return member

    raise MemberError("That invitation is not valid here.")


def revoke(token: str) -> bool:
    """Withdraw an invitation that has not been taken up."""
    with _lock:
        roster = load()
        remaining = [item for item in roster.invites if item.token != (token or "").strip()]
        if len(remaining) == len(roster.invites):
            return False
        roster.invites = remaining
        _save(roster)
    return True


def remove(email: str) -> bool:
    """Take somebody off the team. The last admin stays."""
    address = (email or "").strip().lower()
    with _lock:
        roster = load()
        going = [member for member in roster.members if member.email.lower() == address]
        if not going:
            return False
        if going[0].role == ADMIN:
            owners = [member for member in roster.members if member.role == ADMIN]
            if len(owners) <= 1:
                raise MemberError(
                    "That is the only admin. Make somebody else an admin first."
                )
        roster.members = [
            member for member in roster.members if member.email.lower() != address
        ]
        _save(roster)
    logger.info("Removed %s from workspace %s", address, workspaces.current())
    return True


def set_role(email: str, role: str) -> Member:
    """Change what somebody may do. The last admin cannot demote themselves."""
    address = (email or "").strip().lower()
    if role not in ROLES:
        raise MemberError(f"{role!r} is not a role.")

    with _lock:
        roster = load()
        found = None
        owners = [member for member in roster.members if member.role == ADMIN]
        for member in roster.members:
            if member.email.lower() != address:
                continue
            if member.role == ADMIN and role != ADMIN and len(owners) <= 1:
                raise MemberError("That is the only admin. Make somebody else an admin first.")
            member.role = role
            found = member
        if found is None:
            raise MemberError(f"{email} is not on this team.")
        _save(roster)
    return found
