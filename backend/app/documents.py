"""Where built proposals live.

Separate from `storage`, which owns quotations, because the two artefacts have
different lifetimes and different rules. A quotation is regenerated from a
brief; a proposal is built from a quotation that already exists and is then
fixed - its terms, its figures and its prose are a snapshot of one moment, and
rebuilding produces a new document with a new id rather than editing this one.

One file per document under the workspace's own `_documents/`, mirroring how
jobs are kept. The directory name starts with an underscore so
`storage.all_bundles()`, which walks the workspace looking for 12-hex quotation
directories, steps over it.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from app import storage, workspaces
from app.schemas import ProposalDocument

logger = logging.getLogger("prism.documents")

__all__ = ["save", "get", "listing", "delete", "DIRNAME"]

DIRNAME = "_documents"

#: Reentrant: the per-workspace index is fetched under this lock, and the
#: functions that use it hold the lock too. A plain Lock made that a deadlock.
_lock = threading.RLock()
#: One index per workspace, for the same reason the folders are separate.
_indexes: Dict[str, Dict[str, ProposalDocument]] = {}


def _index_for() -> Dict[str, ProposalDocument]:
    with _lock:
        return _indexes.setdefault(workspaces.current(), {})


def _directory():
    return workspaces.root() / DIRNAME


def _path(document_id: str):
    key = (document_id or "").strip().lower()
    if not storage.is_valid_id(key):
        return None
    return _directory() / f"{key}.json"


def save(document: ProposalDocument) -> ProposalDocument:
    """Write the document and keep it in memory for the rest of the process."""
    path = _path(document.id)
    if path is None:
        raise ValueError(f"{document.id!r} is not a usable document id")

    index = _index_for()
    with _lock:
        index[document.id] = document

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - a full disk must not lose the work
        logger.warning("Could not write proposal %s to disk: %s", document.id, exc)

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

    path = _path(key)
    if path is None or not path.is_file():
        return None
    try:
        recovered = ProposalDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Proposal %s on disk could not be read: %s", key, exc)
        return None
    with _lock:
        index.setdefault(recovered.id, recovered)
    return recovered


def listing(limit: int = 100) -> List[ProposalDocument]:
    """Everything on disk, newest first."""
    directory = _directory()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            get(path.stem)

    index = _index_for()
    with _lock:
        documents = list(index.values())
    documents.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return documents[: max(1, min(limit, 500))]


def delete(document_id: str) -> bool:
    """Remove one proposal. Returns False if it was not there."""
    key = (document_id or "").strip().lower()
    path = _path(key)

    index = _index_for()
    with _lock:
        existed = index.pop(key, None) is not None

    if path is not None and path.is_file():
        try:
            path.unlink()
            return True
        except OSError as exc:
            logger.warning("Could not delete proposal %s: %s", key, exc)
            return False
    return existed
