"""Separate books, one app.

A workspace is a studio's whole world: its settings, its rate card, its terms,
its proposal template and design, its quotation numbering, and every quotation
and proposal it has ever produced. Two workspaces share nothing but the running
process - which is the point. A holding company quoting for two subsidiaries
must not have one subsidiary's rate card price the other's work, and a
consultancy running its own book alongside a client's must not have the two
numbering sequences interleave.

How it is done: every path that used to be `generated/<thing>` is now
`generated/w/<workspace>/<thing>`. There is exactly one place that decides which
workspace that is - `root()` - and it reads a context variable set once per
request. Modules that persist anything call `root()` instead of naming
`config.GENERATED_DIR`, so nothing has to be told which workspace it is in, and
nothing can accidentally write into the wrong one.

There is no login. The client says which workspace it is looking at, in a
header, and the server checks the name is one it knows. That matches what PRISM
already is - a tool a studio runs for itself - and it is stated plainly here so
nobody later mistakes the switch for a security boundary. Anyone who can reach
the API can read every workspace on it.

In-memory caches live in the modules that own them and are keyed by workspace
for the same reason the directories are: a bundle read into memory under one
workspace must never be served under another.
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

from pydantic import BaseModel, Field

from app import config

logger = logging.getLogger("prism.workspaces")

__all__ = [
    "Workspace",
    "NoWorkspace",
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

#: Ids come off a request header and become a path segment, so they are matched
#: against this before touching the filesystem.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: Things at the root of `generated/` that belong to the app rather than to any
#: one workspace, and so are never migrated into one. `_smoke` is the smoke
#: script's scratch folder; dotfiles - `.gitkeep` among them - are the
#: repository's, and moving one would quietly stop tracking the directory.
NOT_A_WORKSPACE = {CONTAINER, "_smoke"}

_lock = threading.RLock()
_current: ContextVar[str] = ContextVar("prism_workspace", default="")


class NoWorkspace(RuntimeError):
    """There is nowhere to file this yet.

    Raised rather than quietly inventing a workspace. A studio's first act
    should be naming the book its work goes in - a folder called "workspace"
    that appeared on its own is a thing nobody chose and nobody recognises.
    """


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


def _read() -> List[Workspace]:
    path = _registry_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("workspaces", []) if isinstance(raw, dict) else raw
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable workspace registry at %s: %s", path, exc)
        return []

    found: List[Workspace] = []
    for entry in entries or []:
        try:
            workspace = Workspace.model_validate(entry)
        except ValueError:
            continue
        if ID_PATTERN.match(workspace.id):
            found.append(workspace)
    return found


def _write(workspaces: List[Workspace]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"workspaces": [workspace.model_dump() for workspace in workspaces]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
    """Add a workspace. Its folder is made now so it is real before it is used."""
    with _lock:
        found = _read()
        workspace = Workspace(
            id=slugify(name, {item.id for item in found}),
            name=(name or "").strip()[:60] or "Workspace",
            created_at=_now(),
        )
        found.append(workspace)
        dir_for(workspace.id).mkdir(parents=True, exist_ok=True)
        _write(found)

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
                _write(found)
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
        found = _read()
        remaining = [workspace for workspace in found if workspace.id != key]
        if len(remaining) == len(found):
            return False

        directory = dir_for(key)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
        _write(remaining)

    # Ids are reusable, so anything cached under this one has to go with it.
    # Imported here rather than at the top: inbox reads workspaces, not the
    # other way round, and a module-level import would be a cycle.
    from app import inbox

    inbox.forget(key)
    # Imported here rather than at module scope: `intakes` imports this module.
    from app import intakes

    intakes.forget(key)
    # Imported here for the same reason: `tokens` imports this module too,
    # and a top-level import here would be the same cycle from the other
    # side. Workspace ids are reusable, so a token left in the index would
    # otherwise resolve into whichever new workspace is later made with the
    # same id.
    from app import tokens

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
    """The workspace in play, falling back to the first on file."""
    key = _current.get()
    if key and exists(key):
        return key
    return default_id()


def root() -> Path:
    """The directory everything in this workspace is stored under.

    Every persisting module calls this instead of naming `GENERATED_DIR`. It
    creates the folder rather than assuming it: a workspace made in one process
    and used in another must not depend on which of them ran first.
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
        found = _read()

    if found:
        return found[0]

    legacy = _legacy_children()
    if not legacy:
        return None

    workspace = create(_legacy_studio_name() or "Studio")

    destination = dir_for(workspace.id)
    moved = 0
    for child in legacy:
        target = destination / child.name
        try:
            if target.exists():
                logger.warning("Not moving %s: %s already exists", child.name, target)
                continue
            shutil.move(str(child), str(target))
            moved += 1
        except OSError as exc:
            logger.error("Could not move %s into workspace %s: %s", child.name, workspace.id, exc)

    logger.info(
        "Moved %d item(s) from the old layout into workspace %s (%s)",
        moved,
        workspace.id,
        workspace.name,
    )
    return workspace
