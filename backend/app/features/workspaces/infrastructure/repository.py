"""Separate books, one app.

A workspace is a studio's whole world: its settings, its rate card, its terms,
its proposal template and design, its quotation numbering, and every quotation
and proposal it has ever produced. Two workspaces share nothing but the running
process - which is the point. A holding company quoting for two subsidiaries
must not have one subsidiary's rate card price the other's work, and a
consultancy running its own book alongside a client's must not have the two
numbering sequences interleave.

Structured workspace records live in SQL. `root()` still resolves
`generated/w/<workspace>/`, but that directory is now only the local external-
asset boundary. It reads a context variable set once per request, so an intake
file written locally still lands in the workspace that owns it.

There is no login. The client says which workspace it is looking at, in a
header, and the server checks the name is one it knows. That matches what PRISM
already is - a tool a studio runs for itself - and it is stated plainly here so
nobody later mistakes the switch for a security boundary. Anyone who can reach
the API can read every workspace on it.

An existing `generated/workspaces.json` is imported once. It remains untouched
as a non-authoritative migration archive, while SQL is authoritative and no new
code writes the registry file.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from uuid import uuid4

from pydantic import BaseModel, Field

from app.shared.infrastructure import config, database

logger = logging.getLogger("prism.workspaces")

__all__ = [
    "Workspace",
    "NoWorkspace",
    "WorkspaceMigrationError",
    "root",
    "current",
    "use",
    "borrow",
    "give_back",
    "listing",
    "create",
    "rename",
    "delete",
    "exists",
    "default_id",
    "ensure_ready",
]

#: Where every workspace lives. One level of nesting, so `generated/` still
#: reads at a glance and a workspace is a folder somebody can back up.
CONTAINER = "w"
REGISTRY_FILENAME = "workspaces.json"
LAYOUT_MIGRATION_FILENAME = ".workspace-layout-migration.json"

#: Ids come off a request header and become a path segment, so they are matched
#: against this before touching the filesystem.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: Things at the root of `generated/` that belong to the app rather than to any
#: one workspace, and so are never migrated into one. `_smoke` is the smoke
#: script's scratch folder; dotfiles - `.gitkeep` among them - are the
#: repository's, and moving one would quietly stop tracking the directory.
NOT_A_WORKSPACE = {CONTAINER, "_runtime", "_smoke"}

# The zero-configuration SQLite database and its sidecars belong to the app,
# not to a legacy workspace being moved under `w/<workspace>/` on first boot.
DATABASE_FILENAMES = {"prism.db", "prism.db-shm", "prism.db-wal", "prism.db-journal"}

KIND = "workspaces"

_lock = threading.RLock()
_current: ContextVar[str | None] = ContextVar("prism_workspace", default=None)


class NoWorkspace(RuntimeError):
    """There is nowhere to file this yet.

    Raised rather than quietly inventing a workspace. A studio's first act
    should be naming the book its work goes in - a folder called "workspace"
    that appeared on its own is a thing nobody chose and nobody recognises.
    """


class WorkspaceMigrationError(RuntimeError):
    """The retained registry or pre-workspace asset move needs intervention."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Workspace(BaseModel):
    """One separate book of work."""

    id: str = Field(description="Slug, and the folder name under generated/w/.")
    name: str = Field(default="", description="What the studio calls it.")
    created_at: str = Field(default="")


def slugify(name: str, taken: set[str] | None = None) -> str:
    """A name as an id: lower case, letters, digits and single hyphens.

    A name that slugifies to nothing still gets a workspace - "工作室" is a
    perfectly good name and a studio using it should not be told to rename
    itself to satisfy a path.
    """
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")[:32]
    base = base.strip("-") or "workspace"
    if not ID_PATTERN.match(base):
        base = "workspace"

    if taken is None:
        return base

    candidate = base
    suffix = 2
    while candidate in taken:
        tail = f"-{suffix}"
        candidate = f"{base[: 32 - len(tail)]}{tail}"
        suffix += 1
    return candidate


# --- the registry -------------------------------------------------------------


def _registry_path() -> Path:
    return config.GENERATED_DIR / REGISTRY_FILENAME


def _legacy_read() -> List[Workspace]:
    """Read the former registry without making it authoritative again."""
    path = _registry_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            entries = raw.get("workspaces", [])
        elif isinstance(raw, list):
            entries = raw
        else:
            raise ValueError("the registry root is not an object or list")
        if not isinstance(entries, list):
            raise ValueError("the workspaces value is not a list")
    except (OSError, ValueError) as exc:
        raise WorkspaceMigrationError(
            f"Legacy workspace registry {path} is unreadable: {exc}"
        ) from exc

    found: List[Workspace] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries):
        try:
            workspace = Workspace.model_validate(entry)
        except ValueError as exc:
            raise WorkspaceMigrationError(
                f"Legacy workspace registry {path} has an invalid row at index {position}: {exc}"
            ) from exc
        if not ID_PATTERN.match(workspace.id):
            raise WorkspaceMigrationError(
                f"Legacy workspace registry {path} has an invalid id at index {position}."
            )
        if workspace.id in seen:
            raise WorkspaceMigrationError(
                f"Legacy workspace registry {path} repeats workspace id {workspace.id!r}."
            )
        seen.add(workspace.id)
        found.append(workspace)
    return found


def _import_legacy_registry() -> None:
    """Import each old registry row once, without ever overwriting SQL.

    The old position is appended to the timestamp so entries created inside
    the same second keep the registry's stable oldest-first order.
    """
    if not database.legacy_scan_required():
        return
    for position, workspace in enumerate(_legacy_read()):
        order = workspace.created_at or "0000-00-00T00:00:00Z"
        database.import_legacy(
            database.GLOBAL_SCOPE,
            KIND,
            workspace.id,
            workspace.model_dump(mode="json"),
            sort_key=f"{order}|{position:08d}",
        )


def _read() -> List[Workspace]:
    _import_legacy_registry()
    found: List[Workspace] = []
    for record in database.listing(database.GLOBAL_SCOPE, KIND):
        try:
            workspace = Workspace.model_validate(record.payload)
        except ValueError:
            logger.warning("Ignoring invalid workspace record %s in SQL", record.record_id)
            continue
        if ID_PATTERN.match(workspace.id):
            found.append(workspace)
    return found


def listing() -> List[Workspace]:
    """Every workspace, oldest first - the order they were made in."""
    with _lock:
        return _read()


def exists(workspace_id: str) -> bool:
    key = (workspace_id or "").strip().lower()
    return any(workspace.id == key for workspace in listing())


def default_id() -> str:
    """The workspace a request that names none is answered from."""
    found = listing()
    return found[0].id if found else ""


def dir_for(workspace_id: str) -> Path:
    key = (workspace_id or "").strip().lower()
    if not ID_PATTERN.match(key):
        raise ValueError(f"Not a usable workspace id: {workspace_id!r}")
    return config.GENERATED_DIR / CONTAINER / key


def create(name: str) -> Workspace:
    """Add a workspace and make its local external-asset directory."""
    with _lock:
        found = _read()
        workspace = Workspace(
            id=slugify(name, {item.id for item in found}),
            name=(name or "").strip()[:60] or "Workspace",
            created_at=_now(),
        )
        dir_for(workspace.id).mkdir(parents=True, exist_ok=True)
        database.put(
            database.GLOBAL_SCOPE,
            KIND,
            workspace.id,
            workspace.model_dump(mode="json"),
            sort_key=f"{workspace.created_at}|{workspace.id}",
        )

    logger.info("Workspace %s created (%s)", workspace.id, workspace.name)
    return workspace


def rename(workspace_id: str, name: str) -> Workspace | None:
    """Change what a workspace is called. The id, and so the folder, is fixed.

    Deliberately: the id is in every client's stored preference and in the paths
    of everything already filed. A rename is a label change, not a move.
    """
    key = (workspace_id or "").strip().lower()
    with _lock:
        found = _read()
        for workspace in found:
            if workspace.id == key:
                workspace.name = (name or "").strip()[:60] or workspace.name
                stored = database.get(database.GLOBAL_SCOPE, KIND, workspace.id)
                database.put(
                    database.GLOBAL_SCOPE,
                    KIND,
                    workspace.id,
                    workspace.model_dump(mode="json"),
                    sort_key=(
                        stored.sort_key
                        if stored is not None
                        else f"{workspace.created_at}|{workspace.id}"
                    ),
                )
                return workspace
    return None


def delete(workspace_id: str) -> bool:
    """Remove a workspace and everything in it. Not recoverable.

    Including the last one. An install with no workspace is a valid state - it
    is what a fresh one is - and the client asks for a name rather than being
    handed a book somebody else called "Studio". Refusing to delete the last
    would mean a studio winding down could never clear its own machine.
    """
    key = (workspace_id or "").strip().lower()
    with _lock:
        # Give an existing JSON registry its one idempotent import opportunity
        # before deciding that this id is absent.
        _import_legacy_registry()
        if not database.exists(database.GLOBAL_SCOPE, KIND, key):
            return False

        # Structured state and the registry row leave in one SQL transaction.
        # External assets are cleaned afterwards: a failed database operation
        # must never erase files while leaving a live workspace behind.
        database.remove_workspace(key, KIND)
        directory = dir_for(key)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)

    # Ids are reusable, so anything cached under this one has to go with it.
    # Imported here rather than at the top: inbox reads workspaces, not the
    # other way round, and a module-level import would be a cycle.
    from app.features.notifications.infrastructure import inbox

    inbox.forget(key)
    from app.features.quotations.infrastructure import repository as quotations

    quotations.forget(key)
    from app.features.documents.application import service as documents

    documents.forget(key)
    from app.features.jobs.application import service as jobs

    jobs.forget(key)
    # Imported here rather than at module scope: `intakes` imports this module.
    from app.features.intakes.application import service as intakes

    intakes.forget(key)
    # Imported here for the same reason: `tokens` imports this module too,
    # and a top-level import here would be the same cycle from the other
    # side. Workspace ids are reusable, so a token left in the index would
    # otherwise resolve into whichever new workspace is later made with the
    # same id.
    from app.features.intakes.infrastructure import tokens

    tokens.forget_workspace(key)

    logger.info("Workspace %s deleted with everything in it", key)
    return True


# --- which one this request is about ------------------------------------------


def use(workspace_id: str) -> str:
    """Point this request - and anything it starts - at one workspace.

    Background jobs inherit the context they were created in, so a quotation
    started under one workspace finishes there even though the request that
    asked for it is long gone.
    """
    key = (workspace_id or "").strip().lower()
    if not key or not exists(key):
        key = default_id()
    _current.set(key)
    return key


def borrow(workspace_id: str):
    """Look inside another workspace briefly, and give the token back to undo it.

    For code that has to read across workspaces - listing them all, counting what
    is in each - rather than for serving a request. Returns a token for `give_back`.

    It exists because `use()` cannot undo itself: `current()` resolves an unset
    context to the first workspace, so "what was it before" read through `use`
    would answer "the default" and quietly move a request that had named another
    workspace into that one.
    """
    token = _current.set((workspace_id or "").strip().lower())
    return token


def give_back(token) -> None:
    """Put the context back exactly as `borrow` found it, set or unset."""
    try:
        _current.reset(token)
    except ValueError:  # pragma: no cover - a token from another context
        pass


def current() -> str:
    """Resolve an unset context, but fail closed if its selected row vanished."""
    key = _current.get()
    if key is None or key == "":
        return default_id()
    if exists(key):
        return key
    raise NoWorkspace(f"The selected workspace {key!r} no longer exists.")


def root() -> Path:
    """The directory this workspace's local external assets are stored under.

    Structured aggregates live in SQL. Asset adapters call this instead of
    naming `GENERATED_DIR`; it creates the folder rather than assuming it so a
    workspace made in one process and used in another is immediately usable.
    """
    key = current()
    if not key:
        raise NoWorkspace(
            "There is no workspace yet. Create one and this will have somewhere to live."
        )
    directory = dir_for(key)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# --- first run, and the move off the old layout --------------------------------


def _legacy_children() -> List[Path]:
    """What is sitting in `generated/` from before workspaces existed."""
    root_dir = config.GENERATED_DIR
    if not root_dir.is_dir():
        return []
    return [
        child
        for child in root_dir.iterdir()
        if child.name not in NOT_A_WORKSPACE
        and child.name not in DATABASE_FILENAMES
        and child.name != REGISTRY_FILENAME
        and not child.name.startswith(".")
    ]


def _legacy_studio_name() -> str:
    """What the studio called itself, to name the workspace its work moves into."""
    path = config.GENERATED_DIR / "settings.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("studio_name", "") or "").strip()[:60]


def _layout_migration_path() -> Path:
    return config.GENERATED_DIR / LAYOUT_MIGRATION_FILENAME


def _write_layout_journal(workspace: Workspace) -> None:
    """Publish the move plan before creating a row or moving any asset."""
    path = _layout_migration_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    payload = {
        "version": 1,
        "workspace": workspace.model_dump(mode="json"),
    }
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


def _read_layout_journal() -> Workspace | None:
    path = _layout_migration_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("unsupported migration-journal version")
        workspace = Workspace.model_validate(raw.get("workspace"))
        if not ID_PATTERN.match(workspace.id):
            raise ValueError("invalid target workspace id")
    except (OSError, ValueError) as exc:
        raise WorkspaceMigrationError(
            f"Workspace layout migration journal {path} is unreadable: {exc}"
        ) from exc
    return workspace


def _ensure_layout_workspace(planned: Workspace) -> Workspace:
    stored = database.get(database.GLOBAL_SCOPE, KIND, planned.id)
    if stored is not None:
        try:
            current = Workspace.model_validate(stored.payload)
        except ValueError as exc:
            raise WorkspaceMigrationError(
                f"The layout migration target {planned.id!r} is invalid in SQL: {exc}"
            ) from exc
        if current != planned:
            raise WorkspaceMigrationError(
                f"The layout migration target {planned.id!r} now names a different workspace."
            )
        return current

    dir_for(planned.id).mkdir(parents=True, exist_ok=True)
    database.put(
        database.GLOBAL_SCOPE,
        KIND,
        planned.id,
        planned.model_dump(mode="json"),
        sort_key=f"{planned.created_at}|{planned.id}",
    )
    logger.info("Workspace %s created (%s)", planned.id, planned.name)
    return planned


def _resume_layout_migration(planned: Workspace) -> Workspace:
    workspace = _ensure_layout_workspace(planned)
    destination = dir_for(workspace.id)
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for child in _legacy_children():
        target = destination / child.name
        if target.exists():
            raise WorkspaceMigrationError(
                f"Cannot resume workspace migration: both {child} and {target} exist."
            )
        try:
            shutil.move(str(child), str(target))
        except OSError as exc:
            raise WorkspaceMigrationError(
                f"Could not move {child} into workspace {workspace.id}: {exc}"
            ) from exc
        moved += 1

    try:
        _layout_migration_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise WorkspaceMigrationError(
            f"Could not finish workspace layout migration: {exc}"
        ) from exc

    logger.info(
        "Moved %d item(s) from the old layout into workspace %s (%s)",
        moved,
        workspace.id,
        workspace.name,
    )
    return workspace


def ensure_ready() -> "Workspace | None":
    """Move any pre-workspace work into a workspace of its own.

    Everything that was in `generated/` - the quotations, the proposals, the
    settings, the numbering counter, the jobs - is moved wholesale into the
    first workspace. It is a move, not a copy: two copies of a numbering counter
    is how two quotations end up with one reference.

    The workspace is named after the studio, because that is what the work in it
    belongs to, and it can be renamed afterwards.

    A clean install gets nothing. `None` means exactly that, and the client asks
    for a name rather than being handed a book somebody else called "Studio".
    """
    with _lock:
        # Check the two migration sentinels before even resuming an old-layout
        # journal. An external-only marker means the SQL volume was replaced;
        # no retained registry or root asset should influence that empty DB.
        database.legacy_scan_required()
        journal = _read_layout_journal()
        if journal is not None:
            return _resume_layout_migration(journal)

        found = _read()
        if found:
            return found[0]

        legacy = _legacy_children()
        if not legacy:
            return None

        name = _legacy_studio_name() or "Studio"
        workspace = Workspace(id=slugify(name, set()), name=name, created_at=_now())
        _write_layout_journal(workspace)
        return _resume_layout_migration(workspace)
