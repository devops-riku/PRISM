"""Telling one person what happened while they were not looking.

A quotation takes ninety seconds and the request that started it returns
immediately - that is the whole point of `jobs`, and it leaves an obvious hole:
if you close the laptop, nothing ever tells you it landed. This is that
telling. It is also how somebody learns they were invited, promoted, removed,
or that a job died with the process.

**Addressed at write time, not filtered at read time.** When something happens
the audience is resolved right there against the roster, and one note is written
into each person's own file, in words chosen for them: an admin is told "Marco
changed the rate card" with a link to Settings; a member is told what the rates
now are, and no link to a door that would refuse them. Reading is then one file
read with no filtering, and read state is per person for free.

The cost of that choice, stated plainly: the words are frozen when they are
written. Somebody demoted tomorrow keeps yesterday's sentences. They are
sentences, not live data, and they age out.

One SQL aggregate per person and workspace keeps reads narrow and read state
private to its recipient. Workspace deletion removes those aggregates with the
rest of that workspace's structured state.

Nothing here raises into a request. A notification that fails to write must
never be the reason a quotation is lost.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.features.notifications.infrastructure import hub
from app.features.team.infrastructure import members
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import database

logger = logging.getLogger("prism.inbox")

__all__ = [
    "Note",
    "notify",
    "deliver",
    "forget",
    "listing",
    "unread",
    "mark_read",
    "clear_read",
    "use_identity",
    "current_key",
    "key_for",
    "ACTOR",
    "ADMINS",
    "TEAM",
    "OTHERS",
]

DIRNAME = "_inbox"
RECORD_KIND = "inbox"

#: Audiences. Resolved against the roster at the moment the event happens.
ACTOR = "actor"
ADMINS = "admins"
TEAM = "team"
OTHERS = "others"

#: Kept small on purpose. The jobs page is the durable record of work; this is
#: only what you have not seen yet, and a feed nobody can reach the bottom of is
#: a feed nobody reads.
KEEP_DAYS = 30
KEEP_MOST = 200

_lock = threading.RLock()
_files: dict[str, dict] = {}

#: Who is asking, for the length of one request - and for anything that request
#: starts, since `asyncio.create_task` copies the context. That is what lets a
#: job finishing ninety seconds later still know whose mail it is.
_identity: ContextVar[tuple] = ContextVar("prism_identity", default=("", ""))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Note(BaseModel):
    """One thing that happened, told to one person."""

    id: str = ""
    kind: str = Field(default="", description="quotation_ready, member_joined, ...")
    at: str = ""
    title: str = ""
    body: str = ""
    href: str = Field(default="", description="Where to go to act on it. Empty means nowhere.")
    read_at: str = ""


class Mail(BaseModel):
    """One person's file."""

    person: str = ""
    notes: List[Note] = Field(default_factory=list)


# --- who is asking -------------------------------------------------------------


def use_identity(email: str, user_id: str) -> None:
    """Name the person this request - and its background work - belongs to."""
    _identity.set(((email or "").strip().lower(), (user_id or "").strip()))


def identity() -> tuple:
    return _identity.get()


def key_for(email: str, user_id: str = "") -> str:
    """A filename for a person: readable, safe, and stable for one address.

    The email is the identity that counts - it is what the roster keys on and
    what an invitation names. The hash tail keeps two addresses that slugify the
    same apart. Somebody with a token and no email is keyed by their id instead;
    an install with no accounts has exactly one person, and calls them `local`.
    """
    address = (email or "").strip().lower()
    if address:
        slug = re.sub(r"[^a-z0-9]+", "-", address).strip("-")[:40] or "person"
        tail = hashlib.sha256(address.encode("utf-8")).hexdigest()[:8]
        return f"{slug}-{tail}"
    if user_id:
        return f"user-{re.sub(r'[^a-z0-9]+', '', user_id.lower())[:12]}"
    return "local"


def current_key() -> str:
    email, user_id = identity()
    return key_for(email, user_id)


# --- storage -------------------------------------------------------------------


def _directory():
    return workspaces.root() / DIRNAME


def _path(person: str):
    return _directory() / f"{person}.json"


def _cache_key(person: str) -> str:
    return f"{workspaces.current()}/{person}"


def _read(person: str) -> tuple:
    """This person's mail, and whether it is trustworthy.

    Untrustworthy means the file exists and could not be read - a lock from the
    sync client, a half-typed hand edit, a schema that moved. The empty mailbox
    that comes back is a placeholder for this one call and nothing more: it is
    never cached, and never written back, because a transient read error must
    not be how thirty days of somebody's mail gets deleted.
    """
    try:
        found = database.get(workspaces.current(), RECORD_KIND, person)
        mail = Mail.model_validate(found.payload) if found is not None else Mail(person=person)
    except (SQLAlchemyError, ValueError) as exc:
        logger.warning(
            "Unreadable inbox for %s in workspace %s - leaving it alone: %s",
            person,
            workspaces.current(),
            exc,
        )
        return Mail(person=person), False

    with _lock:
        _files[_cache_key(person)] = mail.model_dump()
    return mail, True


def _load(person: str) -> Mail:
    return _read(person)[0]


def forget(workspace_id: str) -> None:
    """Drop a workspace's cached mail, because that workspace is gone.

    Ids are reusable: delete `studio` and the next workspace called Studio gets
    the same id. Without this, its notes would be served - and then written -
    into a workspace that has nothing to do with them.
    """
    prefix = f"{(workspace_id or '').strip().lower()}/"
    with _lock:
        for key in [name for name in _files if name.startswith(prefix)]:
            _files.pop(key, None)


def _save(mail: Mail) -> None:
    """Trim, then write. Trimming happens here so there is no second code path.

    A write that fails is logged and dropped: the in-memory copy is still right
    for this process, and a full disk must not turn a delivered quotation into
    an error.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    mail.notes = [note for note in mail.notes if (note.at or "9999") >= cutoff[:19]][:KEEP_MOST]

    with _lock:
        _files[_cache_key(mail.person)] = mail.model_dump()

    try:
        database.put(
            workspaces.current(),
            RECORD_KIND,
            mail.person,
            mail.model_dump(mode="json"),
        )
    except SQLAlchemyError as exc:  # pragma: no cover - notification failure stays non-fatal
        logger.warning("Could not persist the inbox for %s: %s", mail.person, exc)


# --- who gets told -------------------------------------------------------------


def _people(audience, actor_email: str) -> List[tuple]:
    """Resolve an audience into (person key, email, role) right now.

    On an install with no accounts there is one person and they are an admin,
    which is exactly what "unconfigured means open" already means everywhere
    else in this app.
    """
    roster = members.listing()
    actor = (actor_email or "").strip().lower()

    if not roster:
        email = actor or ""
        return [(key_for(email), email, members.ADMIN)]

    def entry(member):
        return (key_for(member.email, member.user_id), member.email, member.role)

    if isinstance(audience, (list, tuple, set)) or isinstance(audience, Iterable) and not isinstance(
        audience, str
    ):
        wanted = {str(name).strip().lower() for name in audience}
        return [entry(member) for member in roster if member.email.lower() in wanted]

    if audience == ACTOR:
        found = [member for member in roster if member.email.lower() == actor]
        return [entry(member) for member in found] or (
            [(key_for(actor), actor, "")] if actor else []
        )
    if audience == ADMINS:
        return [entry(member) for member in roster if member.role == members.ADMIN]
    if audience == OTHERS:
        return [entry(member) for member in roster if member.email.lower() != actor]
    return [entry(member) for member in roster]


def deliver(person: str, kind: str, words: dict) -> bool:
    """Write one note straight into a named person's file.

    For the cases where the audience is a key rather than a role - the restart
    sweep, which knows whose job died and must still reach them if they have
    since left the team.
    """
    if not person or not words.get("title"):
        return False
    try:
        mail, trusted = _read(person)
        if not trusted:
            # The file is there and unreadable. Writing now would replace it
            # with a mailbox holding one note. Losing this note beats losing
            # everything behind it.
            logger.warning("Not delivering %s to %s: their inbox could not be read", kind, person)
            return False
        note = Note(
            id=uuid4().hex[:12],
            kind=kind,
            at=_now(),
            title=str(words.get("title", ""))[:140],
            body=str(words.get("body", ""))[:400],
            href=str(words.get("href", ""))[:200],
        )
        mail.notes.insert(0, note)
        _save(mail)

        # Disk first, socket second. A push that arrived where the write failed
        # would announce something that does not exist.
        hub.publish(
            workspaces.current(),
            person,
            {
                "unread": sum(1 for item in mail.notes if not item.read_at),
                "note": note.model_dump(),
            },
        )
        return True
    except Exception:  # noqa: BLE001 - never the reason a request fails
        logger.exception("Could not deliver a %s notification to %s", kind, person)
        return False


def notify(
    kind: str,
    audience,
    render: Callable[[str, bool], dict] | dict,
    *,
    actor_email: str | None = None,
) -> int:
    """Write one event into every recipient's own file. Never raises.

    `render` is either a dict - the same words for everyone - or a function
    taking `(role, is_you)` and returning `{title, body, href}`, which is how an
    admin and a member get different sentences about the same event.
    """
    try:
        actor = (
            (actor_email if actor_email is not None else identity()[0]) or ""
        ).strip().lower()
        recipients = _people(audience, actor)
        stamp = _now()
        written = 0

        for person, email, role in recipients:
            if not person:
                continue
            try:
                words = render(role, email.lower() == actor) if callable(render) else dict(render)
            except Exception:  # noqa: BLE001 - a copy bug must not lose the event
                logger.exception("Notification copy failed for %s", kind)
                continue
            if not words.get("title"):
                continue

            if deliver(person, kind, {**words, "at": stamp}):
                written += 1

        return written
    except Exception:  # noqa: BLE001 - notifications are never the reason a request fails
        logger.exception("Could not deliver a %s notification", kind)
        return 0


# --- reading -------------------------------------------------------------------


def listing(limit: int = 30, person: str = "") -> List[Note]:
    mail = _load(person or current_key())
    return mail.notes[: max(1, min(limit, KEEP_MOST))]


def unread(person: str = "") -> int:
    return sum(1 for note in _load(person or current_key()).notes if not note.read_at)


def mark_read(through: str = "", ids: Iterable[str] = (), person: str = "") -> int:
    """Mark notes read. `through` takes everything at or older than a stamp."""
    who = person or current_key()
    wanted = {str(item) for item in ids or ()}
    stamp = _now()

    with _lock:
        mail, trusted = _read(who)
        if not trusted:
            return 0
        for note in mail.notes:
            if note.read_at:
                continue
            if wanted and note.id in wanted:
                note.read_at = stamp
            elif not wanted and (not through or note.at <= through):
                note.read_at = stamp
        _save(mail)

    return unread(who)


def clear_read(person: str = "") -> int:
    """Drop what has been read. Anything unread survives, because it has not
    been seen and clearing is not the same act as reading."""
    who = person or current_key()
    with _lock:
        mail, trusted = _read(who)
        if not trusted:
            return 0
        mail.notes = [note for note in mail.notes if not note.read_at]
        _save(mail)
    return unread(who)
