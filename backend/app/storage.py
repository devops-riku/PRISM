"""Bundle persistence: an in-memory index in front of the workspace's folder.

Each bundle writes three files, inside whichever workspace it belongs to:

    backend/generated/w/{workspace}/{id}/bundle.json        the full ProposalBundle
    backend/generated/w/{workspace}/{id}/proposal.md        the client sheet
    backend/generated/w/{workspace}/{id}/requirements.md    the developer sheet

The index is authoritative while the process lives; a miss falls back to reading
`bundle.json` from disk, so `uvicorn --reload` (or a restart mid-session) does
not turn every previously issued link into a 404.

Disk failure never loses a completed bundle: the in-memory entry is written
first and a write error is logged, not raised. The generation call that produced
it is the expensive part, and it has already succeeded by the time we get here.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app import workspaces
from app.schemas import ProposalBundle

logger = logging.getLogger("prism.storage")

ID_LENGTH = 12
#: Ids come off the URL, so they are matched against this before touching the
#: filesystem. No traversal, no absolute paths, no surprises.
ID_PATTERN = re.compile(rf"^[0-9a-f]{{{ID_LENGTH}}}$")

BUNDLE_FILENAME = "bundle.json"
MARKDOWN_FILENAMES = {"proposal": "proposal.md", "requirements": "requirements.md"}

#: Bundles read into memory, per workspace. Keyed rather than shared: a
#: quotation cached while one workspace was open must never be served to
#: another, and two workspaces can hold the same id only by coincidence of the
#: id space, not by design.
_indexes: dict[str, dict[str, ProposalBundle]] = {}
_lock = threading.RLock()


def _index_for() -> dict[str, ProposalBundle]:
    with _lock:
        return _indexes.setdefault(workspaces.current(), {})


class StorageError(RuntimeError):
    """Raised only when a bundle cannot be produced at all - never for a failed disk write."""


def new_id() -> str:
    """A 12-character hex id. Short enough to read aloud, wide enough not to collide."""
    return uuid4().hex[:ID_LENGTH]


def utc_now_iso() -> str:
    """ISO-8601 UTC, seconds precision, with a trailing Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_valid_id(proposal_id: str) -> bool:
    return bool(ID_PATTERN.match((proposal_id or "").strip().lower()))


def bundle_dir(proposal_id: str) -> Path:
    if not is_valid_id(proposal_id):
        raise StorageError(f"Not a valid proposal id: {proposal_id!r}")
    return workspaces.root() / proposal_id.strip().lower()


def save(bundle: ProposalBundle) -> ProposalBundle:
    """Index the bundle, then persist it. Returns the bundle for chaining."""
    index = _index_for()
    with _lock:
        index[bundle.id] = bundle

    try:
        directory = bundle_dir(bundle.id)
        directory.mkdir(parents=True, exist_ok=True)

        (directory / BUNDLE_FILENAME).write_text(
            bundle.model_dump_json(indent=2),
            encoding="utf-8",
        )
        for generated in bundle.files:
            filename = MARKDOWN_FILENAMES.get(generated.kind)
            if filename:
                (directory / filename).write_text(generated.markdown, encoding="utf-8")
    except (OSError, StorageError) as exc:
        # The estimate is already safe in memory and already on its way to the
        # client. A read-only disk should degrade the restart-safety bonus, not
        # throw away work that cost a model call.
        logger.warning(
            "Bundle %s kept in memory only - could not write to %s (%s: %s)",
            bundle.id,
            workspaces.root(),
            exc.__class__.__name__,
            exc,
        )

    return bundle


def get(proposal_id: str) -> ProposalBundle | None:
    """Look up a bundle: memory first, then disk. `None` when it does not exist."""
    key = (proposal_id or "").strip().lower()
    if not is_valid_id(key):
        return None

    index = _index_for()
    with _lock:
        cached = index.get(key)
    if cached is not None:
        return cached

    loaded = _load_from_disk(key)
    if loaded is not None:
        with _lock:
            index.setdefault(key, loaded)
    return loaded


def _load_from_disk(proposal_id: str) -> ProposalBundle | None:
    path = bundle_dir(proposal_id) / BUNDLE_FILENAME
    if not path.is_file():
        return None
    try:
        return ProposalBundle.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Ignoring unreadable bundle at %s (%s: %s)", path, exc.__class__.__name__, exc
        )
        return None


def markdown_for(bundle: ProposalBundle, kind: str) -> str | None:
    """The stored markdown for `proposal` or `requirements`, or `None` if absent."""
    for generated in bundle.files:
        if generated.kind == kind:
            return generated.markdown
    return None


def file_for(bundle: ProposalBundle, kind: str):
    """The `GeneratedFile` entry for `kind`, or `None`."""
    for generated in bundle.files:
        if generated.kind == kind:
            return generated
    return None


def all_bundles() -> list[ProposalBundle]:
    """Every quotation on disk, newest first.

    Reads the directory rather than the in-memory index so the list survives a
    restart and shows work from earlier sessions. Anything in `GENERATED_DIR`
    that is not a valid id directory is skipped - `_smoke/` lives there too, and
    a half-written or hand-edited bundle should not take the whole list down.
    """
    root = workspaces.root()
    if not root.is_dir():
        return []

    index = _index_for()
    found: list[ProposalBundle] = []
    for directory in root.iterdir():
        if not directory.is_dir() or not is_valid_id(directory.name):
            continue
        with _lock:
            cached = index.get(directory.name)
        bundle = cached or _load_from_disk(directory.name)
        if bundle is None:
            continue
        if cached is None:
            with _lock:
                index.setdefault(bundle.id, bundle)
        found.append(bundle)

    found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return found


def delete(proposal_id: str) -> bool:
    """Remove one quotation and its documents. Returns False if it was not there.

    Deliberately narrow: it resolves the directory through `bundle_dir`, which
    rejects anything that is not a bare 12-character hex id, so there is no path
    a caller can supply that escapes `GENERATED_DIR`.
    """
    directory = bundle_dir(proposal_id)
    key = proposal_id.strip().lower()

    index = _index_for()
    with _lock:
        existed = index.pop(key, None) is not None

    if not directory.is_dir():
        return existed

    for child in directory.iterdir():
        if child.is_file():
            child.unlink()
    directory.rmdir()
    logger.info("Deleted quotation %s", key)
    return True
