"""SQLite-local repository lifecycle and workspace deletion safety.

Runs offline in a fresh temporary directory. PostgreSQL is exercised by the
Docker smoke test; this check proves both modes share the same URL boundary and
that no process cache can resurrect records after a workspace slug is reused.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-database-")
os.environ["DATABASE_URL"] = ""

from app.features.documents.application import service as documents  # noqa: E402
from app.features.intakes.application import service as intakes  # noqa: E402
from app.features.intakes.infrastructure import tokens  # noqa: E402
from app.features.jobs.application import service as jobs  # noqa: E402
from app.features.platform.infrastructure import legacy  # noqa: E402
from app.features.quotations.domain.models import (  # noqa: E402
    Estimate,
    ProposalBundle,
    ProposalDocument,
)
from app.features.quotations.infrastructure import repository as quotations  # noqa: E402
from app.features.workspaces.application import settings  # noqa: E402
from app.features.workspaces.infrastructure import reference, repository as workspaces  # noqa: E402
from app.shared.infrastructure import config, database  # noqa: E402


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        raise AssertionError(label)


database.initialize()
ok("an unset DATABASE_URL selects SQLite", database.dialect_name() == "sqlite")
ok("the local database lives under GENERATED_DIR", Path(config.GENERATED_DIR) in Path(database.url().split("///", 1)[1]).parents)
ok("SQL parameters are hidden from engine logs", database.engine().hide_parameters is True)

missing_write_checks = []
for operation in (
    lambda: database.put("missing-scope", "test", "one", {}),
    lambda: database.import_legacy("missing-scope", "test", "two", {}),
    lambda: database.import_counter("missing-scope", "test", 1),
    lambda: database.take_counter("missing-scope", "test"),
):
    try:
        operation()
    except database.MissingWorkspaceError:
        missing_write_checks.append(True)
    else:
        missing_write_checks.append(False)
ok(
    "all scoped record and counter writes require a live workspace row",
    all(missing_write_checks)
    and database.count("missing-scope", "test") == 0
    and database.peek_counter("missing-scope", "test") == 1,
)

first_migration = legacy.migrate()
second_migration = legacy.migrate()
ok(
    "a valid legacy scan records both completion sentinels and is not repeated",
    first_migration.skipped is False
    and database.legacy_manifest_path().is_file()
    and database.legacy_scan_required() is False
    and second_migration.skipped is True,
)

workspace = workspaces.create("Cache Reuse")
ok("an unset workspace context resolves the default", workspaces.current() == workspace.id)
workspaces.use(workspace.id)

saved_settings = settings.StudioDefaults(studio_name="Never Resurrect This")
settings.save(saved_settings)

intake = intakes.create(
    client_email="client@example.com",
    client_phone="",
    scope="Prepare a complete database lifecycle check.",
    budget_text="PHP 50,000",
    preset={},
    created_by="owner@example.com",
)
job = jobs.create("quotation", "Database check", "", ["Persist"])
bundle = ProposalBundle(
    id=quotations.new_id(),
    created_at=quotations.utc_now_iso(),
    estimate=Estimate(),
    files=[],
)
quotations.save(bundle)
document = ProposalDocument(
    id=quotations.new_id(),
    created_at=quotations.utc_now_iso(),
    quotation_id=bundle.id,
)
documents.save(document)

# A completion is authoritative only when every result id already has a SQL
# aggregate.  The rejected transition must not leak into the process cache.
try:
    jobs.finish(job.id, [])
except jobs.JobWriteError as exc:
    empty_result_error = str(exc)
else:
    empty_result_error = ""
try:
    jobs.finish(job.id, ["f" * quotations.ID_LENGTH])
except jobs.JobWriteError as exc:
    missing_result_error = str(exc)
else:
    missing_result_error = ""
ok(
    "a job cannot finish with an uncommitted result",
    empty_result_error == "A job cannot finish before its result is saved."
    and missing_result_error == "A job cannot finish before its result is saved."
    and jobs.get(job.id) is not None
    and jobs.get(job.id).state == "queued",
)

# Force the authoritative write boundary to fail.  Each feature must surface a
# stable application error, and neither its cache nor SQL may claim success.
real_put = database.put


def rejected_put(*args, **kwargs):
    raise RuntimeError("forced database failure with details that must stay internal")


database.put = rejected_put
failed_bundle = ProposalBundle(
    id=quotations.new_id(),
    created_at=quotations.utc_now_iso(),
    estimate=Estimate(),
    files=[],
)
failed_document = ProposalDocument(
    id=quotations.new_id(),
    created_at=quotations.utc_now_iso(),
    quotation_id=bundle.id,
)
try:
    try:
        quotations.save(failed_bundle)
    except quotations.StorageError as exc:
        quotation_error = str(exc)
    else:
        quotation_error = ""

    try:
        documents.save(failed_document)
    except documents.DocumentStorageError as exc:
        document_error = str(exc)
    else:
        document_error = ""

    try:
        jobs.finish(job.id, [bundle.id])
    except jobs.JobWriteError as exc:
        job_error = str(exc)
    else:
        job_error = ""
finally:
    database.put = real_put

ok(
    "a failed quotation commit raises a stable error and is not cached",
    quotation_error == "That quotation could not be saved."
    and quotations.get(failed_bundle.id) is None,
)
ok(
    "a failed document commit raises a stable error and is not cached",
    document_error == "That proposal document could not be saved."
    and documents.get(failed_document.id) is None,
)
persisted_job = database.get(workspace.id, "job", job.id)
ok(
    "a failed job commit cannot leave the job marked done",
    job_error == "That job state could not be saved."
    and jobs.get(job.id) is not None
    and jobs.get(job.id).state == "queued"
    and persisted_job is not None
    and persisted_job.payload.get("state") == "queued",
)

ok("intakes are stored as SQL aggregates", database.count(workspace.id, "intake") == 1)
ok("jobs are stored as SQL aggregates", database.count(workspace.id, "job") == 1)
ok("quotations are stored as SQL aggregates", database.count(workspace.id, "quotation_bundle") == 1)
ok("documents are stored as SQL aggregates", database.count(workspace.id, "proposal_document") == 1)
ok("reference allocation is durable and sequential", (reference.next_sequence(), reference.next_sequence()) == (1, 2))

# Prove reloads do not depend on the process caches populated above.
quotations.forget(workspace.id)
documents.forget(workspace.id)
jobs.forget(workspace.id)
ok("a quotation reloads from SQL", quotations.get(bundle.id) is not None)
ok("a proposal document reloads from SQL", documents.get(document.id) is not None)
ok("a job reloads from SQL", jobs.get(job.id) is not None)

token = intake.token
fallback = workspaces.create("Fallback")
ok("workspace deletion succeeds", workspaces.delete(workspace.id))
try:
    workspaces.current()
except workspaces.NoWorkspace as exc:
    deleted_selection_error = str(exc)
else:
    deleted_selection_error = ""
ok(
    "an explicitly selected deleted workspace never falls through to another",
    workspace.id in deleted_selection_error and workspaces.default_id() == fallback.id,
)
replacement = workspaces.create("Cache Reuse")
workspaces.use(replacement.id)
ok("the deleted slug is reusable", replacement.id == workspace.id)
ok("deleted quotation cache cannot cross into the replacement", quotations.get(bundle.id) is None)
ok("deleted document cache cannot cross into the replacement", documents.get(document.id) is None)
ok("deleted job cache cannot cross into the replacement", jobs.get(job.id) is None)
ok("deleted intakes cannot cross into the replacement", intakes.get(intake.id) is None)
ok("deleted tokens cannot resolve into the replacement", tokens.resolve(token) is None)
ok("deleted settings cannot cross into the replacement", settings.load().studio_name != "Never Resurrect This")
ok("the replacement starts with no structured feature records", sum(database.count(replacement.id, kind) for kind in ("intake", "job", "quotation_bundle", "proposal_document")) == 0)

original_url = config.DATABASE_URL
try:
    config.DATABASE_URL = "postgresql://prism:secret@postgres:5432/prism"
    ok(
        "plain PostgreSQL URLs select the Psycopg 3 driver",
        database.url().startswith("postgresql+psycopg://"),
    )
finally:
    config.DATABASE_URL = original_url

# The pre-workspace asset move is journalled before either the registry row or
# the first move. A failure leaves enough information for the next startup to
# finish instead of returning early merely because the row now exists.
database.dispose()
resume_root = Path(tempfile.mkdtemp(prefix="prism-layout-resume-"))
config.GENERATED_DIR = resume_root
config.DATABASE_URL = ""
database.initialize()
(resume_root / "settings.json").write_text(
    json.dumps({"studio_name": "Resume Studio"}), encoding="utf-8"
)
(resume_root / "first.txt").write_text("first", encoding="utf-8")
(resume_root / "second.txt").write_text("second", encoding="utf-8")

real_move = workspaces.shutil.move
move_attempts = 0


def interrupted_move(source, target):
    global move_attempts
    move_attempts += 1
    if move_attempts == 2:
        raise OSError("simulated interruption")
    return real_move(source, target)


workspaces.shutil.move = interrupted_move
try:
    try:
        workspaces.ensure_ready()
    except workspaces.WorkspaceMigrationError:
        interrupted = True
    else:
        interrupted = False
finally:
    workspaces.shutil.move = real_move

ok(
    "an interrupted root-layout move retains its migration journal",
    interrupted and workspaces._layout_migration_path().is_file(),
)
resumed = workspaces.ensure_ready()
ok(
    "the next startup resumes and completes the root-layout move",
    resumed is not None
    and not workspaces._layout_migration_path().exists()
    and all(
        (workspaces.dir_for(resumed.id) / name).is_file()
        for name in ("settings.json", "first.txt", "second.txt")
    )
    and not any((resume_root / name).exists() for name in ("settings.json", "first.txt", "second.txt")),
)

# A committed SQL sentinel with a missing external file is the safe crash
# window and recreates the manifest. The opposite combination signals a lost
# or replaced database and must stop before stale JSON is scanned.
database.mark_legacy_migration_complete()
database.legacy_manifest_path().unlink()
database.dispose()
database.initialize()
ok(
    "a SQL-only migration sentinel recreates its external manifest",
    database.legacy_scan_required() is False and database.legacy_manifest_path().is_file(),
)

database.dispose()
lost_database_root = Path(tempfile.mkdtemp(prefix="prism-lost-database-"))
config.GENERATED_DIR = lost_database_root
config.DATABASE_URL = ""
database.initialize()
database.legacy_manifest_path().write_text(
    json.dumps({"migration": database.LEGACY_MIGRATION_ID}), encoding="utf-8"
)
try:
    database.legacy_scan_required()
except database.LegacyMigrationStateError as exc:
    lost_database_error = str(exc)
else:
    lost_database_error = ""
ok(
    "an external-only sentinel fails instead of re-importing stale JSON",
    "SQL sentinel is missing" in lost_database_error,
)

# Exercise the dialect branch without needing a PostgreSQL service: engine()
# may construct the production engine, but only initialize()/Alembic may touch
# its schema.
database.dispose()
real_new_engine = database._new_engine
real_create_all = database.metadata.create_all
create_all_calls = []


class PostgreSQLEngineStub:
    hide_parameters = True

    class dialect:
        name = "postgresql"

    def dispose(self):
        pass


try:
    config.DATABASE_URL = "postgresql://prism:secret@postgres:5432/prism"
    database._new_engine = lambda _target: PostgreSQLEngineStub()
    database.metadata.create_all = lambda _engine: create_all_calls.append(_engine)
    database.engine()
    ok("PostgreSQL startup never calls metadata.create_all", not create_all_calls)
finally:
    database.dispose()
    database._new_engine = real_new_engine
    database.metadata.create_all = real_create_all

print("database check passed")
