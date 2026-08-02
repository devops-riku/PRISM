"""Client link tokens: mint them, and find which intake one points to.

An intake link carries a token and nothing else, for the same reason
`members.find_invite`'s docstring gives for a team invitation: a workspace id
sitting in the URL would tell a stranger what exists here. Resolving one
therefore means a search across every workspace, and `find_invite`'s search -
one small roster file read per workspace, on every guess - is fine for a
roster, because nothing reaches it except a route that already knows which
workspace it is calling into.

This route will not. Stage 2 makes `/api/client/<token>` unauthenticated
(Task 3), and an intake is one file each rather than one line in a shared
roster - scanning every intake in every workspace for every wrong guess a
stranger makes is exactly the cost an anonymous endpoint must not carry. So
this module keeps an in-memory index instead: `token -> (workspace_id,
intake_id)`, built once by walking every workspace, and kept current
afterwards by the calls that mint, relink or delete tokens rather than by
walking again.

The index only answers *which intake a token was minted for*. Whether that
token is still good - unexpired, and still the intake's current one - is
decided at resolve time by reading the intake's own record, never by
anything cached here. A relink that somehow left a stale index entry behind
would otherwise leave that entry authoritative for a token that no longer
works.
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timezone

from app import workspaces

logger = logging.getLogger("prism.tokens")

_lock = threading.RLock()

#: token -> (workspace_id, intake_id). Only ever grown or shrunk by
#: `remember`, `forget_token`, `forget_workspace` and the lazy walk below -
#: never by a per-call scan.
_index: dict[str, tuple[str, str]] = {}

#: Whether the lazy walk has run yet. Flips once, for the life of the
#: process: a process that never sees an unresolved token never pays for it
#: at all, and one that does pays for it exactly once.
_built = False


def mint() -> str:
    """A new token. Not good for anything until `remember` files it."""
    return secrets.token_urlsafe(24)


def _expired(stamp: str) -> bool:
    try:
        when = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return when < datetime.now(timezone.utc)


def _build_locked() -> None:
    """Walk every workspace once, reading each intake's own token off disk.

    Called with `_lock` already held, and only on a miss with the walk still
    unrun - a token that was minted and remembered in this same process is
    already in `_index` and never reaches this function at all.
    """
    global _built
    if _built:
        return

    # Imported here rather than at module scope: `intakes` imports this
    # module to mint and remember tokens, so a top-level import here would
    # be a cycle.
    from app import intakes

    try:
        previous = workspaces.borrow(workspaces.current())
        try:
            for workspace in workspaces.listing():
                workspaces.borrow(workspace.id)
                for entry in intakes.listing():
                    if entry.token:
                        _index[entry.token] = (workspace.id, entry.id)
        finally:
            workspaces.give_back(previous)
    except Exception:
        # A walk that dies partway through - `workspaces.root()`'s `mkdir`
        # reaching a permissions error, say - must not become a walk retried
        # on every later miss too: that is a full scan per call again,
        # exactly what this index exists to avoid, and it would surface on
        # Task 3's unauthenticated route as a 500 with a stack trace where an
        # unknown token has to answer 404. `_built` still flips below,
        # accepting whatever partial index this run produced as final for
        # the process; anything the walk never reached is still recoverable
        # the moment its own `create` or `relink` calls `remember` for real.
        logger.exception("Building the token index failed partway through")
    finally:
        _built = True


def build_index() -> None:
    """Force the walk now, synchronously, rather than leaving it for whichever
    request's `resolve()` happens to miss first.

    Called once from `main.py`'s startup event, before the ASGI server begins
    accepting connections - see `_build_locked`'s own comment for the cost
    this is paying up front: a full walk of every workspace's intakes, held
    under `_lock`, measured at ~1.85s for 200 intakes on this repo's
    OneDrive-synced storage. Paid there, it is invisible; paid inside a
    request (the lazy path in `resolve()`, still intact below as a fallback),
    it is a synchronous, lock-held stall on the event loop that every other
    concurrent request queues behind, and on a cold process it takes exactly
    one unauthenticated guess at `/api/client/<token>` to trigger it. Entry
    points that never run `main.py`'s startup event - `check_tokens.py` and
    every other check script that imports this module directly, a future
    admin CLI - still build lazily, correctly, on their own first miss.

    Safe to call more than once, and safe to call from a place that never
    checks whether it already ran: `_build_locked` is itself idempotent once
    `_built` flips, so a second call is a lock acquisition and a flag check,
    nothing more.
    """
    with _lock:
        _build_locked()


def remember(token: str, workspace_id: str, intake_id: str) -> None:
    """File a freshly minted token, so the next resolve finds it without a walk."""
    key = (token or "").strip()
    if not key:
        return
    with _lock:
        _index[key] = (workspace_id, intake_id)


def forget_token(token: str) -> None:
    """Drop one token - what `intakes.relink` does to the one it replaces."""
    key = (token or "").strip()
    if not key:
        return
    with _lock:
        _index.pop(key, None)


def forget_workspace(workspace_id: str) -> None:
    """Drop every token that pointed into a workspace that no longer exists.

    Called from `workspaces.delete`, beside `intakes.forget` - workspace ids
    are reusable, so a token left behind could otherwise resolve into
    whatever new workspace is later created with the same id.
    """
    with _lock:
        stale = [key for key, (ws, _id) in _index.items() if ws == workspace_id]
        for key in stale:
            del _index[key]


def resolve(token: str) -> tuple[str, str] | None:
    """Which workspace and intake a client link belongs to, or `None`.

    The index answers fast for the case that matters most - a token nobody
    has ever minted, which is every guess an attacker makes - costing one
    dict miss and nothing else, *once the index is built*. That is only true
    of every miss but the first: on a process where nothing has called
    `build_index()` yet, the very first miss still pays for the full walk
    below, synchronously, under `_lock`. `main.py`'s startup event calls
    `build_index()` before the server accepts any request specifically so
    that a live server never serves that first miss - see its own docstring
    for the measured cost of not doing that. A token the index does
    recognise is still checked against the intake it names before being
    trusted - expired, relinked away, or the intake itself gone are all read
    from that one file, never assumed from what is cached here.
    """
    wanted = (token or "").strip()
    if not wanted:
        return None

    with _lock:
        found = _index.get(wanted)
        if found is None and not _built:
            _build_locked()
            found = _index.get(wanted)
    if found is None:
        return None

    # Imported here for the same reason `_build_locked` does it.
    from app import intakes

    workspace_id, intake_id = found
    previous = workspaces.borrow(workspace_id)
    try:
        entry = intakes.get(intake_id)
        # Resolved inside the same `borrow` as `get`, not after `give_back` -
        # `intakes.exists` reaches `workspaces.root()` exactly as `get` does,
        # so asking it once the caller's own workspace has been restored
        # would check for the file in the wrong workspace entirely and
        # report "gone" for an intake that is sitting right there in
        # `workspace_id`.
        confirmed_absent = entry is None and not intakes.exists(intake_id)
    finally:
        workspaces.give_back(previous)

    if entry is None:
        # `intakes.get()` answers `None` for two different things: the
        # intake is genuinely gone, or its file exists but could not be read
        # just now (a transient `OSError`, caught and logged inside
        # `intakes.get` itself - this repo lives on a OneDrive-synced path,
        # where a momentary sharing violation on one read is not
        # hypothetical). Evicting is only correct for the first: `_built`
        # never triggers a second walk once it has run, so evicting on a
        # transient failure would kill a live link for the rest of the
        # process's life.
        if confirmed_absent:
            with _lock:
                _index.pop(wanted, None)
        return None

    # The index is a fast path to a candidate, not the authority on whether
    # this token is still good - that authority is the intake's own current
    # token and its own expiry, re-read here rather than trusted from the
    # dict above, so a relink or a deleted-then-recreated workspace cannot
    # leave a stale entry standing in for the real thing. Compared with
    # `secrets.compare_digest(entry.token, wanted)` rather than `==`,
    # matching `members.find_invite` and `members.accept`. Unlike a missing
    # file, a mismatch is unambiguous - the token that once pointed here has
    # been replaced or the record is corrupt either way - so it is evicted
    # outright.
    if not entry.token or not secrets.compare_digest(entry.token, wanted):
        with _lock:
            _index.pop(wanted, None)
        return None
    if _expired(entry.token_expires_at):
        # Evicted for the same reason a mismatch is, just above: a token's
        # expiry only ever moves forward in time, and the one way an intake
        # gets a *different* expiry is `relink()`, which mints a brand new
        # token string - so this exact token value will never resolve again.
        # Left in the index instead, every later probe of the same expired
        # token would keep costing a `borrow` and a file read forever, which
        # is exactly the per-guess cost this index exists to avoid.
        with _lock:
            _index.pop(wanted, None)
        return None
    return workspace_id, intake_id
