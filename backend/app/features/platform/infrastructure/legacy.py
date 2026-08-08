"""Idempotently import PRISM's former JSON record store into SQL.

Source files are deliberately retained as a human-readable migration archive. Every
source record receives its own marker in ``legacy_imports`` in the same
transaction as the insert, so a later startup cannot overwrite a record that
has changed—or resurrect one that has been deleted—in PostgreSQL or SQLite.
Generated Markdown and uploaded bytes are assets, not records, and remain in
the workspace directory.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import database

logger = logging.getLogger("prism.legacy")

_ID = re.compile(r"^[0-9a-f]{12}$")


@dataclass
class MigrationReport:
    imported: int = 0
    already_present: int = 0
    invalid: int = 0
    skipped: bool = False


class LegacyMigrationError(RuntimeError):
    """At least one retained JSON record could not be validated and imported."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("the JSON root is not an object")
    return value


def _validated(
    path: Path,
    model: type[BaseModel],
    *,
    restore: tuple[str, ...] = (),
) -> dict[str, Any]:
    source = _json(path)
    clean = model.model_validate(source).model_dump(mode="json")
    for field in restore:
        clean[field] = source.get(field, "")
    return clean


def _one(
    report: MigrationReport,
    path: Path,
    scope: str,
    kind: str,
    record_id: str,
    read: Callable[[Path], dict[str, Any]],
    *,
    sort_field: str = "",
    lookup_field: str = "",
) -> None:
    if not path.is_file():
        return
    try:
        payload = read(path)
        imported = database.import_legacy(
            scope,
            kind,
            record_id,
            payload,
            sort_key=str(payload.get(sort_field, "")) if sort_field else "",
            lookup_key=str(payload.get(lookup_field, "")) if lookup_field else None,
        )
        if imported:
            report.imported += 1
        else:
            report.already_present += 1
    except (OSError, ValueError, TypeError) as exc:
        report.invalid += 1
        logger.warning("Could not import legacy record %s: %s", path, exc)


def migrate() -> MigrationReport:
    """Import every legacy aggregate after the workspace registry is ready."""
    if not database.legacy_scan_required():
        return MigrationReport(skipped=True)

    # Imported locally to keep startup dependency order explicit and avoid
    # making this platform adapter part of any feature's normal import graph.
    from app.features.documents.application.service import ProposalDocument
    from app.features.intakes.application.service import Intake
    from app.features.jobs.application.service import Job
    from app.features.notifications.infrastructure.inbox import Mail
    from app.features.quotations.domain.models import ProposalBundle
    from app.features.team.infrastructure.members import Roster
    from app.features.workspaces.application.settings import StudioDefaults

    report = MigrationReport()
    for workspace in workspaces.listing():
        scope = workspace.id
        root = workspaces.dir_for(scope)

        _one(
            report,
            root / "settings.json",
            scope,
            "settings",
            "defaults",
            lambda path: _validated(path, StudioDefaults),
        )
        _one(
            report,
            root / "members.json",
            scope,
            "team_roster",
            "roster",
            lambda path: _validated(path, Roster),
        )

        counter_path = root / "_reference.json"
        if counter_path.is_file():
            try:
                values = _json(counter_path)
                quotation_next = values.get("quotations", values.get("next", 1))
                for series, next_value in (
                    ("quotations", quotation_next),
                    ("proposals", values.get("proposals", 1)),
                ):
                    if database.import_counter(scope, series, max(1, int(next_value or 1))):
                        report.imported += 1
                    else:
                        report.already_present += 1
            except (OSError, ValueError, TypeError) as exc:
                report.invalid += 1
                logger.warning("Could not import legacy counters %s: %s", counter_path, exc)

        if root.is_dir():
            for directory in root.iterdir():
                if directory.is_dir() and _ID.fullmatch(directory.name):
                    _one(
                        report,
                        directory / "bundle.json",
                        scope,
                        "quotation_bundle",
                        directory.name,
                        lambda path: _validated(path, ProposalBundle),
                        sort_field="created_at",
                    )

        documents = root / "_documents"
        if documents.is_dir():
            for path in documents.glob("*.json"):
                if _ID.fullmatch(path.stem):
                    _one(
                        report,
                        path,
                        scope,
                        "proposal_document",
                        path.stem,
                        lambda item: _validated(item, ProposalDocument),
                        sort_field="created_at",
                    )

        intakes = root / "_intakes"
        if intakes.is_dir():
            for path in intakes.glob("*.json"):
                if _ID.fullmatch(path.stem):
                    _one(
                        report,
                        path,
                        scope,
                        "intake",
                        path.stem,
                        lambda item: _validated(item, Intake, restore=("token",)),
                        sort_field="created_at",
                        lookup_field="token",
                    )

        jobs = root / "_jobs"
        if jobs.is_dir():
            for path in jobs.glob("*.json"):
                if _ID.fullmatch(path.stem):
                    _one(
                        report,
                        path,
                        scope,
                        "job",
                        path.stem,
                        lambda item: _validated(item, Job, restore=("owner",)),
                        sort_field="created_at",
                    )

        inbox = root / "_inbox"
        if inbox.is_dir():
            for path in inbox.glob("*.json"):
                _one(
                    report,
                    path,
                    scope,
                    "inbox",
                    path.stem,
                    lambda item: _validated(item, Mail),
                )

    if report.imported or report.invalid:
        logger.info(
            "Legacy JSON migration: %d imported, %d already present, %d invalid",
            report.imported,
            report.already_present,
            report.invalid,
        )
    if report.invalid:
        raise LegacyMigrationError(
            f"Legacy JSON migration found {report.invalid} invalid record(s). "
            "Fix or remove those records and restart; migration was not marked complete."
        )
    database.mark_legacy_migration_complete()
    return report
