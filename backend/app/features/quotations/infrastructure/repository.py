"""SQL persistence for quotation bundle snapshots.

The complete :class:`ProposalBundle` is stored as one JSON aggregate in the
portable database.  That keeps the Pydantic domain contract authoritative on
both SQLite and PostgreSQL without flattening the estimate's large immutable
object graph into persistence-shaped domain models.

Rendered Markdown remains mirrored beneath the workspace's asset directory so
an operator can inspect or back it up independently.  ``bundle.json`` is a
legacy import source only: this repository neither reads nor writes it.

The process cache is populated only after the SQL transaction commits.  A
rendered asset is a convenience copy and may fail independently, but an
aggregate that was never stored must never look saved to this process.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.features.quotations.domain.models import ProposalBundle
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import database

logger = logging.getLogger("prism.storage")

ID_LENGTH = 12
#: Ids come off the URL, so they are matched against this before touching the
#: filesystem. No traversal, no absolute paths, no surprises.
ID_PATTERN = re.compile(rf"^[0-9a-f]{{{ID_LENGTH}}}$")

BUNDLE_FILENAME = "bundle.json"
MARKDOWN_FILENAMES = {"proposal": "proposal.md", "requirements": "requirements.md"}
KIND = "quotation_bundle"

#: Bundles read into memory, per workspace. Keyed rather than shared: a
#: quotation cached while one workspace was open must never be served to
#: another, and two workspaces can hold the same id only by coincidence of the
#: id space, not by design.
_indexes: dict[str, dict[str, ProposalBundle]] = {}
_lock = threading.RLock()


def _index_for() -> dict[str, ProposalBundle]:
    with _lock:
        return _indexes.setdefault(workspaces.current(), {})


def forget(workspace_id: str) -> None:
    """Drop cached aggregates when a workspace id is deleted and may be reused."""
    with _lock:
        _indexes.pop((workspace_id or "").strip().lower(), None)


class StorageError(RuntimeError):
    """The bundle cannot be addressed or its authoritative SQL write failed."""


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
    """Commit the aggregate before exposing it through the process cache."""
    # Resolve and validate before writing SQL.  In particular, ``root()``
    # retains the old ``NoWorkspace`` failure instead of allowing an aggregate
    # with an empty scope to leak into the database.
    directory = bundle_dir(bundle.id)

    try:
        database.put(
            workspaces.current(),
            KIND,
            bundle.id,
            bundle.model_dump(mode="json"),
            sort_key=bundle.created_at,
        )
    except Exception as exc:  # noqa: BLE001 - all failed authoritative writes are fatal
        raise StorageError("That quotation could not be saved.") from exc

    index = _index_for()
    with _lock:
        index[bundle.id] = bundle

    try:
        directory.mkdir(parents=True, exist_ok=True)
        for generated in bundle.files:
            filename = MARKDOWN_FILENAMES.get(generated.kind)
            if filename:
                (directory / filename).write_text(generated.markdown, encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "Bundle %s stored in SQL but its rendered assets could not be written to %s "
            "(%s: %s)",
            bundle.id,
            workspaces.root(),
            exc.__class__.__name__,
            exc,
        )

    return bundle


def get(proposal_id: str) -> ProposalBundle | None:
    """Look up a bundle: memory first, then SQL. ``None`` when absent."""
    key = (proposal_id or "").strip().lower()
    if not is_valid_id(key):
        return None

    index = _index_for()
    with _lock:
        cached = index.get(key)
    if cached is not None:
        return cached

    loaded = _load_from_database(key)
    if loaded is not None:
        with _lock:
            index.setdefault(key, loaded)
    return loaded


def _load_from_database(proposal_id: str) -> ProposalBundle | None:
    # Calling ``root`` preserves the repository's historic NoWorkspace
    # behaviour while the returned path remains an asset concern only.
    workspaces.root()
    try:
        record = database.get(workspaces.current(), KIND, proposal_id)
    except Exception as exc:  # noqa: BLE001 - a transient read remains a clean miss
        logger.warning(
            "Could not read bundle %s from the database (%s: %s)",
            proposal_id,
            exc.__class__.__name__,
            exc,
        )
        return None
    if record is None:
        return None
    try:
        return ProposalBundle.model_validate(record.payload)
    except ValueError as exc:
        logger.warning("Ignoring invalid bundle %s in the database: %s", proposal_id, exc)
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
    """Every quotation in this workspace, newest first."""
    workspaces.root()
    index = _index_for()
    found: list[ProposalBundle] = []
    records = database.listing(workspaces.current(), KIND, newest_first=True)
    for record in records:
        with _lock:
            cached = index.get(record.record_id)
        if cached is not None:
            found.append(cached)
            continue
        try:
            bundle = ProposalBundle.model_validate(record.payload)
        except ValueError as exc:
            logger.warning("Ignoring invalid bundle %s in the database: %s", record.record_id, exc)
            continue
        with _lock:
            index.setdefault(bundle.id, bundle)
        found.append(bundle)

    # The database supplies the order, but sorting after validation also keeps
    # it exact when a cached object has been returned for a stored row.
    found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return found


def delete(proposal_id: str) -> bool:
    """Remove one quotation and its rendered assets. Returns False if absent.

    Deliberately narrow: it resolves the directory through `bundle_dir`, which
    rejects anything that is not a bare 12-character hex id, so there is no path
    a caller can supply that escapes `GENERATED_DIR`.
    """
    directory = bundle_dir(proposal_id)
    key = proposal_id.strip().lower()

    index = _index_for()
    with _lock:
        existed = index.pop(key, None) is not None

    removed = database.remove(workspaces.current(), KIND, key)

    if directory.is_dir():
        for child in directory.iterdir():
            if child.is_file():
                child.unlink()
        directory.rmdir()

    if removed or existed:
        logger.info("Deleted quotation %s", key)
    return removed or existed
