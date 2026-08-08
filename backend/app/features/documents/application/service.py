"""SQL persistence for built proposal snapshots.

Separate from `storage`, which owns quotations, because the two artefacts have
different lifetimes and different rules. A quotation is regenerated from a
brief; a proposal is built from a quotation that already exists and is then
fixed - its terms, its figures and its prose are a snapshot of one moment, and
rebuilding produces a new document with a new id rather than editing this one.

Each document is stored as one JSON aggregate in the portable database.  Its
Pydantic model remains the data contract, while SQLite locally and PostgreSQL
in production share the same repository behaviour.  The old `_documents/`
path helpers remain for legacy import and compatibility, but document JSON is
neither read nor written here.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from app.features.quotations.domain.models import ProposalDocument
from app.features.quotations.infrastructure import repository as storage
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import database

logger = logging.getLogger("prism.documents")

__all__ = ["DocumentStorageError", "save", "get", "listing", "delete", "DIRNAME"]

DIRNAME = "_documents"
KIND = "proposal_document"

#: Reentrant: the per-workspace index is fetched under this lock, and the
#: functions that use it hold the lock too. A plain Lock made that a deadlock.
_lock = threading.RLock()
#: One index per workspace, for the same reason the folders are separate.
_indexes: Dict[str, Dict[str, ProposalDocument]] = {}


class DocumentStorageError(RuntimeError):
    """The proposal document's authoritative SQL write did not commit."""


def _index_for() -> Dict[str, ProposalDocument]:
    with _lock:
        return _indexes.setdefault(workspaces.current(), {})


def forget(workspace_id: str) -> None:
    """Drop cached documents when a workspace id is deleted and may be reused."""
    with _lock:
        _indexes.pop((workspace_id or "").strip().lower(), None)


def _directory():
    return workspaces.root() / DIRNAME


def _path(document_id: str):
    key = (document_id or "").strip().lower()
    if not storage.is_valid_id(key):
        return None
    return _directory() / f"{key}.json"


def save(document: ProposalDocument) -> ProposalDocument:
    """Commit the document before exposing it through the process cache."""
    path = _path(document.id)
    if path is None:
        raise ValueError(f"{document.id!r} is not a usable document id")

    try:
        database.put(
            workspaces.current(),
            KIND,
            document.id,
            document.model_dump(mode="json"),
            sort_key=document.created_at,
        )
    except Exception as exc:  # noqa: BLE001 - all failed authoritative writes are fatal
        raise DocumentStorageError("That proposal document could not be saved.") from exc

    index = _index_for()
    with _lock:
        index[document.id] = document

    logger.info(
        "Proposal %s built from quotation %s (%d clauses)",
        document.id,
        document.quotation_id,
        len(document.policies),
    )
    return document


def get(document_id: str) -> ProposalDocument | None:
    key = (document_id or "").strip().lower()
    index = _index_for()
    with _lock:
        found = index.get(key)
    if found is not None:
        return found

    if _path(key) is None:
        return None
    try:
        record = database.get(workspaces.current(), KIND, key)
    except Exception as exc:  # noqa: BLE001 - preserve the old clean-miss read behaviour
        logger.warning(
            "Proposal %s could not be read from the database (%s: %s)",
            key,
            exc.__class__.__name__,
            exc,
        )
        return None
    if record is None:
        return None
    try:
        recovered = ProposalDocument.model_validate(record.payload)
    except ValueError as exc:
        logger.warning("Proposal %s in the database is invalid: %s", key, exc)
        return None
    with _lock:
        index.setdefault(recovered.id, recovered)
    return recovered


def listing(limit: int = 100) -> List[ProposalDocument]:
    """Everything stored in this workspace, newest first."""
    workspaces.root()
    index = _index_for()
    documents: List[ProposalDocument] = []
    for record in database.listing(workspaces.current(), KIND, newest_first=True):
        with _lock:
            document = index.get(record.record_id)
        if document is None:
            try:
                document = ProposalDocument.model_validate(record.payload)
            except ValueError as exc:
                logger.warning(
                    "Proposal %s in the database is invalid: %s", record.record_id, exc
                )
                continue
            with _lock:
                index.setdefault(document.id, document)
        documents.append(document)
    documents.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return documents[: max(1, min(limit, 500))]


def delete(document_id: str) -> bool:
    """Remove one proposal. Returns False if it was not there."""
    key = (document_id or "").strip().lower()
    path = _path(key)

    index = _index_for()
    with _lock:
        existed = index.pop(key, None) is not None

    removed = database.remove(workspaces.current(), KIND, key)

    # A legacy JSON file is not authoritative after import, but removing it
    # here avoids leaving a misleading artefact beside live local assets.
    if path is not None and path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Deleted proposal %s from SQL but not its legacy JSON: %s", key, exc)
    return removed or existed
