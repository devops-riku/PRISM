"""A client request, from the words they said to the quotation it became.

PRISM's two stored records are snapshots on purpose: a `ProposalBundle` is what
was quoted and a `ProposalDocument` is what was sent, and neither is edited
after the fact - rebuilding produces a new one with a new id. That is right for
documents and useless for a conversation, which has a state that changes.

So this is the third kind of record, and the only one that moves: what a client
asked for, where that request has got to, and which bundles came out of it. It
is storage-side and never reaches the model, so it lives here rather than in
`schemas.py`, exactly as `members.Invite` does.

One file per intake under `_intakes/`. The leading underscore matters:
`storage.all_bundles()` walks the workspace directory looking for quotations and
steps over anything starting with one.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import List

from pydantic import BaseModel, Field

from app import storage, workspaces

logger = logging.getLogger("prism.intakes")

DIRNAME = "_intakes"

#: Reachable in Stage 1.
SUBMITTED = "submitted"
PREPARING = "preparing"
QUOTED = "quoted"
QUOTE_FAILED = "quote_failed"
CLOSED = "closed"

#: Written now, refused until Stage 2 wires the actor that can reach them. The
#: machine is defined once; a later stage turns these on rather than adding them.
ISSUED = "issued"
SENT = "sent"
REVISION_REQUESTED = "revision_requested"
FINALIZED = "finalized"
PROPOSAL_SENT = "proposal_sent"

#: What may follow what. A move not listed here is refused, which is what makes
#: this a state machine rather than a string field somebody assigns to.
ALLOWED: dict = {
    SUBMITTED: {PREPARING, CLOSED},
    PREPARING: {QUOTED, QUOTE_FAILED, CLOSED},
    QUOTED: {CLOSED},
    QUOTE_FAILED: {PREPARING, CLOSED},
    CLOSED: set(),
}

#: Defined, and deliberately unreachable until Stage 2.
STAGE_TWO = {ISSUED, SENT, REVISION_REQUESTED, FINALIZED, PROPOSAL_SENT}


class IntakeError(Exception):
    """A move the machine does not allow, or an intake that is not there."""


class Intake(BaseModel):
    """One client request and everything that has happened to it."""

    id: str = ""
    state: str = SUBMITTED
    created_at: str = ""
    created_by: str = ""

    # What the client said. Kept verbatim, never rewritten.
    client_email: str = ""
    client_phone: str = ""
    scope: str = ""
    budget_text: str = ""

    #: The PAD settings this intake will be quoted under - kind, currency,
    #: market region, tax basis, payment terms, tiers.
    preset: dict = Field(default_factory=dict)

    # What actually happened.
    job_id: str = ""
    bundle_ids: List[str] = Field(default_factory=list)
    document_id: str = ""
    #: The scope and budget as they stood when Generate was pressed. Kept apart
    #: from the client's own words so the pair reads as "asked" and "priced".
    priced_scope: str = ""
    priced_budget: str = ""
    error: str = ""

    closed_at: str = ""
    closed_by: str = ""


_lock = threading.RLock()


def _directory():
    return workspaces.root() / DIRNAME


def _path(intake_id: str):
    return _directory() / f"{intake_id}.json"


def _write(entry: Intake) -> Intake:
    path = _path(entry.id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise IntakeError(f"That intake could not be saved: {exc}") from exc
    return entry


def create(
    *,
    client_email: str,
    client_phone: str,
    scope: str,
    budget_text: str,
    preset: dict,
    created_by: str,
) -> Intake:
    """Record a request. Starts at `submitted`: in Stage 1 the studio types in
    what the client told them, so there is no link to issue and nothing to wait
    for."""
    entry = Intake(
        id=storage.new_id(),
        state=SUBMITTED,
        created_at=storage.utc_now_iso(),
        created_by=created_by,
        client_email=client_email.strip(),
        client_phone=client_phone.strip(),
        scope=scope.strip(),
        budget_text=budget_text.strip(),
        preset=dict(preset or {}),
    )
    with _lock:
        return _write(entry)


def get(intake_id: str) -> Intake | None:
    path = _path((intake_id or "").strip().lower())
    if not path.is_file():
        return None
    try:
        return Intake.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable intake at %s - leaving it alone: %s", path, exc)
        return None


def listing() -> List[Intake]:
    """Every intake in this workspace, newest first."""
    directory = _directory()
    if not directory.is_dir():
        return []
    found = []
    for path in directory.glob("*.json"):
        entry = get(path.stem)
        if entry is not None:
            found.append(entry)
    found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return found


def advance(intake_id: str, to: str, **fields) -> Intake:
    """Move one intake, or refuse. Everything a state needs is set in the same
    write, so an intake is never briefly in a state without its own evidence."""
    with _lock:
        entry = get(intake_id)
        if entry is None:
            raise IntakeError("That request does not exist.")
        if to in STAGE_TWO:
            raise IntakeError(f"{to} is not reachable until the client link ships.")
        if to not in ALLOWED:
            raise IntakeError(f"{to} is not a state.")
        if to not in ALLOWED.get(entry.state, set()):
            raise IntakeError(f"A request that is {entry.state} cannot become {to}.")

        for key, value in fields.items():
            if not hasattr(entry, key):
                raise IntakeError(f"An intake has no {key}.")
            setattr(entry, key, value)
        entry.state = to
        return _write(entry)


def close(intake_id: str, by: str) -> Intake:
    """Not going ahead. Allowed from anywhere that is not already closed."""
    with _lock:
        entry = get(intake_id)
        if entry is None:
            raise IntakeError("That request does not exist.")
        if entry.state == CLOSED:
            return entry
        entry.state = CLOSED
        entry.closed_at = storage.utc_now_iso()
        entry.closed_by = by
        return _write(entry)


def forget(workspace_id: str) -> None:
    """Called when a workspace is deleted. Workspace ids are reusable, so an
    intake that outlived its workspace would surface inside somebody else's."""
    # Nothing is cached in memory, and `workspaces.delete` has already removed
    # the folder these live in. This exists so the call site reads completely
    # and so a future cache cannot be added without a place to clear it.
    logger.info("Intakes forgotten with workspace %s", workspace_id)
