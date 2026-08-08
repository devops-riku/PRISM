"""Portable SQL persistence for PRISM aggregate snapshots.

The domain stays Pydantic-first: feature repositories hand this module plain
JSON-compatible dictionaries and receive them back unchanged. SQLAlchemy owns
only the database boundary. PostgreSQL stores payloads as JSONB; SQLite uses
its native JSON representation through the same SQLAlchemy type.

Local development needs no configuration. With no ``DATABASE_URL`` the
database is ``GENERATED_DIR/prism.db``. Production supplies a
``postgresql+psycopg://`` URL (plain ``postgres://`` and ``postgresql://`` are
normalised to Psycopg 3 as a convenience).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    func,
    inspect,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url

from app.shared.infrastructure import config

GLOBAL_SCOPE = "__global__"
WORKSPACE_KIND = "workspaces"

# The SQL row prevents a completed migration being repeated against the same
# database. The external marker prevents an empty replacement database from
# silently re-importing stale JSON retained as a non-authoritative archive.
LEGACY_MIGRATION_KIND = "migration"
LEGACY_MIGRATION_ID = "legacy-json-v1"
LEGACY_MANIFEST_FILENAME = ".legacy-sql-migration.json"

_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=_NAMING_CONVENTION)
PortableJSON = JSON().with_variant(JSONB(), "postgresql")

records = Table(
    "aggregate_records",
    metadata,
    Column("scope", String(64), primary_key=True),
    Column("kind", String(48), primary_key=True),
    Column("record_id", String(255), primary_key=True),
    Column("payload", PortableJSON, nullable=False),
    Column("sort_key", String(64), nullable=False, default=""),
    Column("lookup_key", String(255), nullable=True),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
    UniqueConstraint("kind", "lookup_key", name="uq_aggregate_records_kind_lookup_key"),
)
Index("ix_aggregate_records_scope_kind_sort", records.c.scope, records.c.kind, records.c.sort_key)

counters = Table(
    "reference_counters",
    metadata,
    Column("scope", String(64), primary_key=True),
    Column("series", String(32), primary_key=True),
    Column("next_value", BigInteger, nullable=False),
)

legacy_imports = Table(
    "legacy_imports",
    metadata,
    Column("scope", String(64), primary_key=True),
    Column("kind", String(48), primary_key=True),
    Column("record_id", String(255), primary_key=True),
    Column("imported_at", String(32), nullable=False),
)


@dataclass(frozen=True)
class Record:
    """One stored aggregate, including its portable query metadata."""

    scope: str
    kind: str
    record_id: str
    payload: dict[str, Any]
    sort_key: str = ""
    lookup_key: str | None = None


_lock = threading.RLock()
_engines: dict[str, Engine] = {}
_ready: set[str] = set()
_legacy_complete: set[tuple[str, str]] = set()


class DatabaseSchemaError(RuntimeError):
    """The configured production database has not been migrated."""


class LegacyMigrationStateError(RuntimeError):
    """The SQL and external legacy-migration sentinels disagree."""


class MissingWorkspaceError(RuntimeError):
    """A write named a workspace that no longer exists."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def url() -> str:
    """The effective SQLAlchemy URL, resolved without exposing it in logs."""
    raw = (config.DATABASE_URL or "").strip()
    if raw:
        parsed = make_url(raw)
        if parsed.drivername in {"postgres", "postgresql"}:
            parsed = parsed.set(drivername="postgresql+psycopg")
        return parsed.render_as_string(hide_password=False)

    path = (Path(config.GENERATED_DIR) / "prism.db").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{path.as_posix()}"


def dialect_name() -> str:
    """``sqlite`` locally or ``postgresql`` when configured for production."""
    return make_url(url()).get_backend_name()


def _new_engine(target: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "hide_parameters": True}
    if make_url(target).get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}

    created = create_engine(target, **kwargs)
    if created.dialect.name == "sqlite":

        @event.listens_for(created, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return created


def engine() -> Engine:
    """Return one pooled engine per effective URL.

    SQLite is the zero-configuration local store, so its schema is created on
    first use. PostgreSQL is production state and is changed only by Alembic.
    """
    target = url()
    with _lock:
        current = _engines.get(target)
        if current is None:
            current = _new_engine(target)
            _engines[target] = current
        if target not in _ready and current.dialect.name == "sqlite":
            metadata.create_all(current)
            _ready.add(target)
        return current


def initialize() -> None:
    """Create the local schema or verify the migrated production schema."""
    current = engine()
    if current.dialect.name == "sqlite":
        return

    target = url()
    with _lock:
        if target in _ready:
            return
        required = {
            "alembic_version",
            records.name,
            counters.name,
            legacy_imports.name,
        }
        present = set(inspect(current).get_table_names())
        missing = sorted(required - present)
        if missing:
            raise DatabaseSchemaError(
                "The PostgreSQL schema is not ready (missing: "
                + ", ".join(missing)
                + "). Run `alembic upgrade head` before starting PRISM."
            )
        _ready.add(target)


def ping() -> bool:
    try:
        with engine().connect() as connection:
            connection.execute(select(1)).scalar_one()
        return True
    except Exception:
        return False


def dispose() -> None:
    """Release pooled connections, primarily for isolated checks and shutdown."""
    with _lock:
        for current in _engines.values():
            current.dispose()
        _engines.clear()
        _ready.clear()
        _legacy_complete.clear()


def _insert(table: Table):
    current = engine()
    if current.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)


def _record_values(
    scope: str,
    kind: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    sort_key: str = "",
    lookup_key: str | None = None,
) -> dict[str, Any]:
    stamp = _now()
    return {
        "scope": scope,
        "kind": kind,
        "record_id": record_id,
        "payload": payload,
        "sort_key": sort_key or "",
        "lookup_key": lookup_key or None,
        "created_at": stamp,
        "updated_at": stamp,
    }


def _workspace_row_locked(
    connection, scope: str, workspace_kind: str = WORKSPACE_KIND
) -> bool:
    """Lock/check the global registry row that authorises a scoped write.

    PostgreSQL has a row lock. SQLite serialises writers at database level, so
    a no-op update acquires its write lock before checking the matched row.
    Keeping this in the caller's transaction makes a write and workspace
    deletion mutually exclusive without adding a foreign-key-shaped schema.
    """
    statement = select(records.c.record_id).where(
        records.c.scope == GLOBAL_SCOPE,
        records.c.kind == workspace_kind,
        records.c.record_id == scope,
    )
    if connection.dialect.name == "postgresql":
        return connection.execute(statement.with_for_update()).first() is not None
    if connection.dialect.name == "sqlite":
        result = connection.execute(
            update(records)
            .where(
                records.c.scope == GLOBAL_SCOPE,
                records.c.kind == workspace_kind,
                records.c.record_id == scope,
            )
            .values(updated_at=records.c.updated_at)
        )
        return bool(result.rowcount)
    return connection.execute(statement).first() is not None


def _require_workspace(connection, scope: str) -> None:
    if scope == GLOBAL_SCOPE:
        return
    if not _workspace_row_locked(connection, scope):
        raise MissingWorkspaceError(f"Workspace {scope!r} does not exist.")


def put(
    scope: str,
    kind: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    sort_key: str = "",
    lookup_key: str | None = None,
) -> None:
    """Insert or replace one aggregate atomically."""
    values = _record_values(
        scope, kind, record_id, payload, sort_key=sort_key, lookup_key=lookup_key
    )
    statement = _insert(records).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[records.c.scope, records.c.kind, records.c.record_id],
        set_={
            "payload": statement.excluded.payload,
            "sort_key": statement.excluded.sort_key,
            "lookup_key": statement.excluded.lookup_key,
            "updated_at": statement.excluded.updated_at,
        },
    )
    with engine().begin() as connection:
        _require_workspace(connection, scope)
        connection.execute(statement)


def get(scope: str, kind: str, record_id: str) -> Record | None:
    statement = select(records).where(
        records.c.scope == scope,
        records.c.kind == kind,
        records.c.record_id == record_id,
    )
    with engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return _as_record(row) if row else None


def exists(scope: str, kind: str, record_id: str) -> bool:
    statement = select(records.c.record_id).where(
        records.c.scope == scope,
        records.c.kind == kind,
        records.c.record_id == record_id,
    )
    with engine().connect() as connection:
        return connection.execute(statement).first() is not None


def listing(
    scope: str | None,
    kind: str,
    *,
    newest_first: bool = False,
    limit: int | None = None,
) -> list[Record]:
    statement = select(records).where(records.c.kind == kind)
    if scope is not None:
        statement = statement.where(records.c.scope == scope)
    order = records.c.sort_key.desc() if newest_first else records.c.sort_key.asc()
    statement = statement.order_by(order, records.c.record_id.desc() if newest_first else records.c.record_id)
    if limit is not None:
        statement = statement.limit(max(0, limit))
    with engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_as_record(row) for row in rows]


def count(scope: str, kind: str) -> int:
    statement = select(func.count()).select_from(records).where(
        records.c.scope == scope, records.c.kind == kind
    )
    with engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


def find_by_lookup(kind: str, lookup_key: str) -> Record | None:
    wanted = (lookup_key or "").strip()
    if not wanted:
        return None
    statement = select(records).where(
        records.c.kind == kind, records.c.lookup_key == wanted
    )
    with engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return _as_record(row) if row else None


def remove(scope: str, kind: str, record_id: str) -> bool:
    with engine().begin() as connection:
        result = connection.execute(
            delete(records).where(
                records.c.scope == scope,
                records.c.kind == kind,
                records.c.record_id == record_id,
            )
        )
        _mark_legacy(connection, scope, kind, record_id)
    return bool(result.rowcount)


def remove_scope(scope: str) -> None:
    """Delete all structured state in a workspace; external assets are separate."""
    with engine().begin() as connection:
        connection.execute(delete(records).where(records.c.scope == scope))
        connection.execute(delete(counters).where(counters.c.scope == scope))


def remove_workspace(scope: str, workspace_kind: str = "workspaces") -> bool:
    """Atomically remove a workspace registry row and all of its SQL state."""
    with engine().begin() as connection:
        if not _workspace_row_locked(connection, scope, workspace_kind):
            return False
        connection.execute(delete(records).where(records.c.scope == scope))
        connection.execute(delete(counters).where(counters.c.scope == scope))
        removed = connection.execute(
            delete(records).where(
                records.c.scope == GLOBAL_SCOPE,
                records.c.kind == workspace_kind,
                records.c.record_id == scope,
            )
        )
        _mark_legacy(connection, GLOBAL_SCOPE, workspace_kind, scope)
    return bool(removed.rowcount)


def _as_record(row) -> Record:
    return Record(
        scope=str(row["scope"]),
        kind=str(row["kind"]),
        record_id=str(row["record_id"]),
        payload=dict(row["payload"] or {}),
        sort_key=str(row["sort_key"] or ""),
        lookup_key=str(row["lookup_key"]) if row["lookup_key"] is not None else None,
    )


def _mark_legacy(connection, scope: str, kind: str, record_id: str) -> None:
    statement = _insert(legacy_imports).values(
        scope=scope, kind=kind, record_id=record_id, imported_at=_now()
    )
    statement = statement.on_conflict_do_nothing(
        index_elements=[legacy_imports.c.scope, legacy_imports.c.kind, legacy_imports.c.record_id]
    )
    connection.execute(statement)


def import_legacy(
    scope: str,
    kind: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    sort_key: str = "",
    lookup_key: str | None = None,
) -> bool:
    """Import one JSON record at most once, without replacing SQL changes."""
    values = _record_values(
        scope, kind, record_id, payload, sort_key=sort_key, lookup_key=lookup_key
    )
    with engine().begin() as connection:
        _require_workspace(connection, scope)
        processed = connection.execute(
            select(legacy_imports.c.record_id).where(
                legacy_imports.c.scope == scope,
                legacy_imports.c.kind == kind,
                legacy_imports.c.record_id == record_id,
            )
        ).first()
        if processed:
            return False

        statement = _insert(records).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[records.c.scope, records.c.kind, records.c.record_id]
        )
        result = connection.execute(statement)
        _mark_legacy(connection, scope, kind, record_id)
        return bool(result.rowcount)


def import_counter(scope: str, series: str, next_value: int) -> bool:
    marker_kind = "reference_counter"
    with engine().begin() as connection:
        _require_workspace(connection, scope)
        processed = connection.execute(
            select(legacy_imports.c.record_id).where(
                legacy_imports.c.scope == scope,
                legacy_imports.c.kind == marker_kind,
                legacy_imports.c.record_id == series,
            )
        ).first()
        if processed:
            return False
        statement = _insert(counters).values(
            scope=scope, series=series, next_value=max(1, int(next_value))
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=[counters.c.scope, counters.c.series]
        )
        result = connection.execute(statement)
        _mark_legacy(connection, scope, marker_kind, series)
        return bool(result.rowcount)


def peek_counter(scope: str, series: str) -> int:
    statement = select(counters.c.next_value).where(
        counters.c.scope == scope, counters.c.series == series
    )
    with engine().connect() as connection:
        value = connection.execute(statement).scalar_one_or_none()
    return max(1, int(value or 1))


def take_counter(scope: str, series: str) -> int:
    """Atomically reserve and return one sequence number on both databases."""
    statement = _insert(counters).values(scope=scope, series=series, next_value=2)
    statement = statement.on_conflict_do_update(
        index_elements=[counters.c.scope, counters.c.series],
        set_={"next_value": counters.c.next_value + 1},
    ).returning(counters.c.next_value)
    with engine().begin() as connection:
        _require_workspace(connection, scope)
        next_value = int(connection.execute(statement).scalar_one())
    return next_value - 1


def legacy_manifest_path() -> Path:
    """External half of the one-time JSON-to-SQL migration sentinel."""
    return Path(config.GENERATED_DIR) / LEGACY_MANIFEST_FILENAME


def _legacy_sql_complete() -> bool:
    statement = select(legacy_imports.c.record_id).where(
        legacy_imports.c.scope == GLOBAL_SCOPE,
        legacy_imports.c.kind == LEGACY_MIGRATION_KIND,
        legacy_imports.c.record_id == LEGACY_MIGRATION_ID,
    )
    with engine().connect() as connection:
        return connection.execute(statement).first() is not None


def _read_legacy_manifest(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LegacyMigrationStateError(
            f"Legacy migration marker {path} is unreadable: {exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("migration") != LEGACY_MIGRATION_ID:
        raise LegacyMigrationStateError(
            f"Legacy migration marker {path} is not a recognised PRISM marker."
        )


def _write_legacy_manifest() -> None:
    path = legacy_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "migration": LEGACY_MIGRATION_ID,
        "completed_at": _now(),
    }
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def legacy_scan_required() -> bool:
    """Return whether retained JSON may be scanned on this installation.

    A SQL-only sentinel is the recoverable crash window between committing the
    database marker and replacing the manifest. An external-only sentinel is
    different: it means the database was replaced or lost, and importing an
    old archive automatically could resurrect data that had since been deleted.
    """
    path = legacy_manifest_path()
    cache_key = (url(), str(path.resolve()))
    with _lock:
        if cache_key in _legacy_complete:
            return False

    external_complete = path.is_file()
    if external_complete:
        _read_legacy_manifest(path)
    sql_complete = _legacy_sql_complete()

    if external_complete and not sql_complete:
        raise LegacyMigrationStateError(
            "The external legacy-migration marker exists but its SQL sentinel "
            "is missing. The database may have been replaced or its volume "
            "lost; restore the intended database instead of re-importing stale JSON."
        )
    if sql_complete and not external_complete:
        _write_legacy_manifest()
        with _lock:
            _legacy_complete.add(cache_key)
        return False
    complete = external_complete and sql_complete
    if complete:
        with _lock:
            _legacy_complete.add(cache_key)
    return not complete


def mark_legacy_migration_complete() -> None:
    """Commit the SQL sentinel, then atomically publish its external peer."""
    with engine().begin() as connection:
        _mark_legacy(
            connection,
            GLOBAL_SCOPE,
            LEGACY_MIGRATION_KIND,
            LEGACY_MIGRATION_ID,
        )
    _write_legacy_manifest()
    cache_key = (url(), str(legacy_manifest_path().resolve()))
    with _lock:
        _legacy_complete.add(cache_key)


def import_many(items: Iterable[Record]) -> int:
    """Convenience for migration code; every item remains independently idempotent."""
    imported = 0
    for item in items:
        imported += int(
            import_legacy(
                item.scope,
                item.kind,
                item.record_id,
                item.payload,
                sort_key=item.sort_key,
                lookup_key=item.lookup_key,
            )
        )
    return imported
