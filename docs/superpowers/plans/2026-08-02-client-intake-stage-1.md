# Client Intake, Stage 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every client conversation becomes a tracked request the studio reviews before anything is quoted, with what the client asked sitting beside what was actually priced.

**Architecture:** One new storage-side record (`backend/app/intakes.py`) owns a state machine. Approving an intake does not call a new endpoint — it opens the PAD form the studio already uses, prefilled, at `#/pad/<intake_id>`. `POST /api/proposals` gains one optional form field and three state stamps inside its existing `run()`. Nothing anonymous, no token, no public route.

**Tech Stack:** FastAPI + Pydantic v2 (backend), React 18 + TypeScript strict + Tailwind v4 (frontend). No new dependencies.

## Global Constraints

- **No public route, no token issued, no anonymous write.** The API surface stays exactly as closed as it is today. `auth.OPEN_PATHS` is not touched.
- **`backend/app/schemas.py` is not modified.** The intake record is storage-side and never handed to the model, so it is defined in `intakes.py` the way `members.Invite` is defined in `members.py`.
- **No new model-facing fields.** The prompt and the `Estimate` schema are untouched.
- **The client's budget is advisory.** It goes to the existing `budget_hint` form field. `target_total` — which the server solves arithmetic onto exactly — stays empty unless a human types it on the PAD.
- **Ids come from `storage.new_id()`**, never from `reference.next_sequence()`. An intake must not burn a quotation number.
- **Intakes live under `generated/w/<ws>/_intakes/`.** The leading underscore is load-bearing: `storage.all_bundles()` walks that directory and must step over it.
- **The frontend is TypeScript strict.** `npm run typecheck` must report zero errors at every checkpoint.
- **Tailwind v4 CSS-first.** No config files. Hand-written classes go in `@layer components` in `frontend/src/index.css`.
- **This repository is not under version control.** There is no `git commit` step. Each task ends with a verification checkpoint instead. Before starting Task 1, copy `backend/app/main.py` to `backend/app/main.py.bak` as a manual baseline; delete it when the plan is done.
- **Tests are standalone check scripts**, matching `backend/scripts/check_kind_api.py`. They run offline against a scratch `GENERATED_DIR`, call no model and touch no real work. Exit code 0 means pass. There is no pytest in this project; do not add one.

**Windows note:** the interpreter is `backend/.venv/Scripts/python.exe`. On macOS/Linux it is `backend/.venv/bin/python`. Commands below use the Windows path.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/app/intakes.py` | The record, the state machine, disk storage, `forget()` |
| `backend/scripts/check_intakes.py` | State machine and storage, offline |
| `backend/scripts/check_intakes_api.py` | Routes and permissions, offline |
| `backend/scripts/check_intake_gate.py` | The three stamps on `POST /api/proposals`, model stubbed |
| `frontend/src/components/IntakeScreen.tsx` | Create an intake |
| `frontend/src/components/IntakeListScreen.tsx` | The queue |

**Modified**

| File | Change |
|---|---|
| `backend/app/workspaces.py:253` | `intakes.forget(key)` beside the existing `inbox.forget(key)` |
| `backend/app/main.py` | Four `/api/intakes` routes; one `intake_id` form field and three stamps in `create_proposal` |
| `frontend/src/types.ts` | `Intake`, `IntakeState` |
| `frontend/src/lib/api.ts` | Five calls |
| `frontend/src/components/HomeScreen.tsx` | Two destination arrays and the pill |
| `frontend/src/components/BriefForm.tsx` | One optional `prefill` prop |
| `frontend/src/App.tsx` | Two routes; read `/pad/<id>`; pass `intake_id` |
| `frontend/src/index.css` | `.pill` component classes |

---

## Task 1: The intake record and its state machine

**Files:**
- Create: `backend/app/intakes.py`
- Modify: `backend/app/workspaces.py:253`
- Test: `backend/scripts/check_intakes.py`

**Interfaces:**
- Consumes: `storage.new_id()`, `storage.utc_now_iso()`, `workspaces.root()`
- Produces:
  - `SUBMITTED, PREPARING, QUOTED, QUOTE_FAILED, CLOSED, ISSUED, SENT, REVISION_REQUESTED, FINALIZED, PROPOSAL_SENT: str`
  - `Intake` (pydantic `BaseModel`)
  - `create(*, client_email: str, client_phone: str, scope: str, budget_text: str, preset: dict, created_by: str) -> Intake`
  - `get(intake_id: str) -> Intake | None`
  - `listing() -> list[Intake]`
  - `advance(intake_id: str, to: str, **fields) -> Intake` — raises `IntakeError` on an illegal move
  - `close(intake_id: str, by: str) -> Intake`
  - `forget(workspace_id: str) -> None`
  - `IntakeError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `backend/scripts/check_intakes.py`:

```python
"""The intake record: what it stores, and which moves it refuses.

Runs offline against a scratch `generated/` directory:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes.py

Exit code 0 means the state machine only allows what Stage 1 allows.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intakes-")

from app import intakes, workspaces  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def refuses(label: str, call) -> None:
    try:
        call()
    except intakes.IntakeError:
        ok(label, True)
        return
    ok(label, False)


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
workspaces.use(made.id)

entry = intakes.create(
    client_email="buyer@client.com",
    client_phone="+63 917 000 0000",
    scope="A booking site for two clinics.",
    budget_text="around 300k",
    preset={"kind": "software", "currency": "PHP"},
    created_by="riku@neptune.ph",
)

ok("an intake starts at submitted", entry.state == intakes.SUBMITTED)
ok("it carries the client's words verbatim", entry.scope == "A booking site for two clinics.")
ok("and their budget as text, not a number", entry.budget_text == "around 300k")
ok("it has a 12-character id", len(entry.id) == 12)
ok("it is readable again", intakes.get(entry.id) is not None)
ok("and it is in the listing", [row.id for row in intakes.listing()] == [entry.id])

# The Stage 1 path.
intakes.advance(entry.id, intakes.PREPARING, job_id="j1")
ok("submitted -> preparing", intakes.get(entry.id).state == intakes.PREPARING)

intakes.advance(
    entry.id,
    intakes.QUOTED,
    bundle_ids=["abc123def456"],
    priced_scope="A booking site for two clinics, two locations.",
    priced_budget="300000",
)
quoted = intakes.get(entry.id)
ok("preparing -> quoted", quoted.state == intakes.QUOTED)
ok("the bundle is recorded", quoted.bundle_ids == ["abc123def456"])
ok("what was actually priced is kept apart from what was asked", quoted.priced_scope != quoted.scope)

# The states Stage 2 turns on are refused now.
refuses("quoted -> sent is refused in stage 1", lambda: intakes.advance(entry.id, intakes.SENT))
refuses(
    "quoted -> finalized is refused in stage 1",
    lambda: intakes.advance(entry.id, intakes.FINALIZED),
)

# Illegal moves, forever.
refuses("quoted -> preparing is refused", lambda: intakes.advance(entry.id, intakes.PREPARING))
refuses("an unknown state is refused", lambda: intakes.advance(entry.id, "nonsense"))
refuses("an unknown intake is refused", lambda: intakes.advance("0" * 12, intakes.CLOSED))

closed = intakes.close(entry.id, "riku@neptune.ph")
ok("anything -> closed", closed.state == intakes.CLOSED)
ok("closed records who", closed.closed_by == "riku@neptune.ph")
refuses("closed is terminal", lambda: intakes.advance(entry.id, intakes.PREPARING))

# A failure has somewhere to go.
second = intakes.create(
    client_email="two@client.com",
    client_phone="",
    scope="A stock take.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(second.id, intakes.PREPARING, job_id="j2")
intakes.advance(second.id, intakes.QUOTE_FAILED, error="Gemini answered with no usable estimate.")
ok("preparing -> quote_failed", intakes.get(second.id).state == intakes.QUOTE_FAILED)
intakes.advance(second.id, intakes.PREPARING, job_id="j3")
ok("and a failed intake can be retried", intakes.get(second.id).state == intakes.PREPARING)

# Workspace ids are reusable, so a deleted workspace must take its intakes.
workspaces.delete(made.id)
again = workspaces.create("Neptune Labs")
workspaces.use(again.id)
ok("a deleted workspace takes its intakes with it", intakes.listing() == [])

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
```

- [ ] **Step 2: Run it and watch it fail**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes.py
```

Expected: `ModuleNotFoundError: No module named 'app.intakes'`.

- [ ] **Step 3: Write `backend/app/intakes.py`**

```python
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
```

- [ ] **Step 4: Wire `forget` into workspace deletion**

In `backend/app/workspaces.py`, find `inbox.forget(key)` (line 253) and add the intake line beneath it. Import at the point of use to avoid an import cycle — `intakes` imports `workspaces`:

```python
    inbox.forget(key)
    # Imported here rather than at module scope: `intakes` imports this module.
    from app import intakes

    intakes.forget(key)
```

- [ ] **Step 5: Run the test**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes.py
```

Expected: every line `ok`, then `all pass`, exit 0.

- [ ] **Step 6: Checkpoint**

Confirm no other behaviour moved:

```
cd backend
.venv/Scripts/python.exe scripts/smoke.py
```

Expected: exit 0.

---

## Task 2: The intake routes

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/scripts/check_intakes_api.py`

**Interfaces:**
- Consumes: `intakes.create/get/listing/close`, `intakes.IntakeError`, `_who(request)`, `_require_admin_of` (existing helpers in `main.py`)
- Produces: `POST /api/intakes`, `GET /api/intakes`, `GET /api/intakes/{intake_id}`, `POST /api/intakes/{intake_id}/close`

- [ ] **Step 1: Write the failing test**

Create `backend/scripts/check_intakes_api.py`:

```python
"""The intake routes: what they return, and who is allowed to call them.

Offline. No model, no network:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intake-api-")

from fastapi.testclient import TestClient  # noqa: E402

from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app)
headers = {"X-Workspace": made.id}

created = client.post(
    "/api/intakes",
    headers=headers,
    json={
        "client_email": "buyer@client.com",
        "client_phone": "+63 917 000 0000",
        "scope": "A booking site for two clinics.",
        "budget_text": "around 300k",
        "preset": {"kind": "software", "currency": "PHP"},
    },
)
ok("creating an intake answers 201", created.status_code == 201)
body = created.json()
ok("it comes back submitted", body["state"] == "submitted")
ok("with the client's words", body["scope"] == "A booking site for two clinics.")

listed = client.get("/api/intakes", headers=headers)
ok("the queue lists it", listed.status_code == 200 and len(listed.json()) == 1)

read = client.get(f"/api/intakes/{body['id']}", headers=headers)
ok("it reads back by id", read.status_code == 200 and read.json()["id"] == body["id"])

ok(
    "an unknown id is 404, not 500",
    client.get("/api/intakes/000000000000", headers=headers).status_code == 404,
)

closed = client.post(f"/api/intakes/{body['id']}/close", headers=headers)
ok("closing answers 200", closed.status_code == 200)
ok("and the state says so", closed.json()["state"] == "closed")

# An intake belongs to its workspace and must not be visible from another.
other = workspaces.create("Someone Else")
ok(
    "another workspace sees none of them",
    client.get("/api/intakes", headers={"X-Workspace": other.id}).json() == [],
)
ok(
    "and cannot read one by id",
    client.get(f"/api/intakes/{body['id']}", headers={"X-Workspace": other.id}).status_code == 404,
)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
```

- [ ] **Step 2: Run it and watch it fail**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes_api.py
```

Expected: the first assertion fails with 404 — the route does not exist.

- [ ] **Step 3: Add the request model and the routes**

In `backend/app/main.py`, beside the other request models, add:

```python
class IntakeRequest(BaseModel):
    """What the studio heard from the client, plus how it should be quoted."""

    client_email: str = ""
    client_phone: str = ""
    scope: str = ""
    budget_text: str = ""
    preset: dict = Field(default_factory=dict)
```

Then the routes, next to the workspace routes:

```python
@app.post("/api/intakes", response_model=intakes.Intake, status_code=201, tags=["intakes"])
async def create_intake(request: Request, body: IntakeRequest) -> intakes.Intake:
    """Record a client request. Admin-only: an intake is the start of a price,
    and issuing one is nearer to inviting somebody than to drafting a quotation."""
    _require_admin_of(request)
    if not body.scope.strip():
        raise HTTPException(status_code=422, detail="A request needs a scope.")
    try:
        return intakes.create(
            client_email=body.client_email,
            client_phone=body.client_phone,
            scope=body.scope,
            budget_text=body.budget_text,
            preset=body.preset,
            created_by=_who_email(request),
        )
    except intakes.IntakeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/intakes", response_model=List[intakes.Intake], tags=["intakes"])
async def list_intakes() -> List[intakes.Intake]:
    """The queue, newest first. Scoped to this workspace like everything else."""
    return intakes.listing()


@app.get("/api/intakes/{intake_id}", response_model=intakes.Intake, tags=["intakes"])
async def read_intake(intake_id: str) -> intakes.Intake:
    entry = intakes.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    return entry


@app.post("/api/intakes/{intake_id}/close", response_model=intakes.Intake, tags=["intakes"])
async def close_intake(request: Request, intake_id: str) -> intakes.Intake:
    """Not going ahead. Reversible only by making a new request."""
    _require_admin_of(request)
    try:
        return intakes.close(intake_id, _who_email(request))
    except intakes.IntakeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

Add `intakes` to the `from app import ...` line at the top of `main.py`.

Neither `_require_admin` nor `_who_email` exists yet. Add both beside the existing `_who`
(`main.py:2338`).

Do **not** reuse `_require_admin_of(request, workspace_id)` (`main.py:2109`) — it asks whether you
administer *another named* workspace, which is a different question. The role for the workspace
already open is put on the request by the gate at `main.py:308`.

```python
def _require_admin(request: Request) -> None:
    """Refuse a member on the workspace that is currently open.

    The gate has already resolved the role and put it on the request
    (main.py:308). On an install with no accounts it is ADMIN, so this stays
    open exactly as the rest of the app does when auth is unconfigured.
    """
    if getattr(request.state, "role", members.ADMIN) != members.ADMIN:
        raise HTTPException(status_code=403, detail="That is an admin's to do.")


def _who_email(request: Request) -> str:
    """The signed-in email, or '' on an install with no accounts."""
    user = getattr(request.state, "user", None)
    return user.email if user else ""
```

- [ ] **Step 4: Run the test**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes_api.py
```

Expected: `all pass`, exit 0.

- [ ] **Step 5: Checkpoint**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes.py
.venv/Scripts/python.exe scripts/smoke.py
```

Both exit 0.

---

## Task 3: The review gate on `POST /api/proposals`

**Files:**
- Modify: `backend/app/main.py` — `create_proposal`
- Test: `backend/scripts/check_intake_gate.py`

**Interfaces:**
- Consumes: `intakes.advance`, `intakes.PREPARING/QUOTED/QUOTE_FAILED`
- Produces: `POST /api/proposals` accepts `intake_id: str = Form("")`

This is the whole backend cost of the review gate: one field and three stamps.

- [ ] **Step 1: Capture the baseline that protects existing quotations**

Before any edit, record what a quotation with no `intake_id` renders as today:

```
cd backend
.venv/Scripts/python.exe scripts/check_kind_render.py
```

Expected: exit 0. This is the guard that the stamps change nothing for quotations prepared without an intake. Run it again in Step 6.

- [ ] **Step 2: Write the failing test**

Create `backend/scripts/check_intake_gate.py`:

```python
"""The three stamps: preparing on entry, quoted on finish, quote_failed on error.

Gemini is stubbed. The point is the seam - that the real handler, run end to
end, moves the intake - so the stub replaces only the model call and nothing
else. A previous session shipped a TypeError that isolated tests missed for
exactly this reason.

    cd backend
    .venv/Scripts/python.exe scripts/check_intake_gate.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intake-gate-")

from fastapi.testclient import TestClient  # noqa: E402

from app import intakes, main, workspaces  # noqa: E402
from app.schemas import Estimate  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def settle(intake_id: str, want: str, seconds: float = 20.0) -> str:
    """Wait for the background job to land, then report where it got to."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = intakes.get(intake_id).state
        if state == want:
            return state
        time.sleep(0.25)
    return intakes.get(intake_id).state


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app=main.app)
headers = {"X-Workspace": made.id}


def stub_estimate(*args, **kwargs):
    return Estimate(project_name="Booking platform", currency="PHP")


def stub_failure(*args, **kwargs):
    raise RuntimeError("Gemini answered with no usable estimate.")


entry = intakes.create(
    client_email="buyer@client.com",
    client_phone="",
    scope="A booking site.",
    budget_text="around 300k",
    preset={},
    created_by="riku@neptune.ph",
)

main.generate_estimate = stub_estimate
response = client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A booking site.", "intake_id": entry.id},
)
ok("the pad still answers 202", response.status_code == 202)
ok("the intake reaches quoted", settle(entry.id, intakes.QUOTED) == intakes.QUOTED)

done = intakes.get(entry.id)
ok("with the bundle recorded", len(done.bundle_ids) == 1)
ok("and what was actually priced", done.priced_scope == "A booking site.")
ok("the client's own words are untouched", done.scope == "A booking site.")

# A failure must leave the intake findable, not stuck at preparing.
second = intakes.create(
    client_email="two@client.com",
    client_phone="",
    scope="A stock take.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
main.generate_estimate = stub_failure
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A stock take.", "intake_id": second.id},
)
ok(
    "a failed pass reaches quote_failed",
    settle(second.id, intakes.QUOTE_FAILED) == intakes.QUOTE_FAILED,
)
ok("and says why", bool(intakes.get(second.id).error))

# The field is optional, and a quotation without one must behave exactly as before.
main.generate_estimate = stub_estimate
plain = client.post("/api/proposals", headers=headers, data={"brief": "No intake here."})
ok("a quotation with no intake_id still answers 202", plain.status_code == 202)

# An unknown intake id must not take the quotation down with it.
odd = client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "Unknown intake.", "intake_id": "0" * 12},
)
ok("an unknown intake_id does not fail the request", odd.status_code == 202)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
```

- [ ] **Step 3: Run it and watch it fail**

```
cd backend
.venv/Scripts/python.exe scripts/check_intake_gate.py
```

Expected: the intake never leaves `submitted` — nothing stamps it.

- [ ] **Step 4: Add the form field**

In `create_proposal`, beside the other `Form(...)` parameters (around `backend/app/main.py:884`):

```python
    intake_id: str = Form(""),
```

- [ ] **Step 5: Add the three stamps**

Inside the existing `run()` in `create_proposal`. A helper first, defined just above `run()`:

```python
    def stamp(to: str, **fields) -> None:
        """Move the intake this quotation came from, if it came from one.

        Never raises: a quotation is the thing being prepared, and losing the
        bookkeeping around it must not lose the quotation. An intake_id that
        does not resolve is a stale form, not a reason to refuse the work.
        """
        if not intake_id:
            return
        try:
            intakes.advance(intake_id, to, **fields)
        except intakes.IntakeError as exc:
            logger.warning("Intake %s not moved to %s: %s", intake_id, to, exc)
```

Then three calls:

1. Immediately after `job = jobs.create(...)` returns, before the work starts:

```python
        stamp(intakes.PREPARING, job_id=job.id)
```

2. Immediately after `jobs.finish(job.id, [bundle.id for bundle in bundles])`:

```python
            stamp(
                intakes.QUOTED,
                bundle_ids=[bundle.id for bundle in bundles],
                priced_scope=brief,
                priced_budget=budget_hint,
            )
```

3. In each `jobs.fail(...)` branch of the same handler, directly after the `jobs.fail` call:

```python
            stamp(intakes.QUOTE_FAILED, error=str(exc))
```

For the final branch whose message is a fixed string rather than an exception, use that string.

- [ ] **Step 6: Run the tests**

```
cd backend
.venv/Scripts/python.exe scripts/check_intake_gate.py
.venv/Scripts/python.exe scripts/check_kind_render.py
.venv/Scripts/python.exe scripts/check_kind_api.py
.venv/Scripts/python.exe scripts/smoke.py
```

All four exit 0. The render check is the one that matters most: it proves a quotation prepared without an intake renders exactly as it did before this task.

---

## Task 4: The notifications

**Files:**
- Modify: `backend/app/main.py` — the `stamp` helper from Task 3
- Test: extend `backend/scripts/check_intake_gate.py`

**Interfaces:**
- Consumes: `inbox.notify(kind, audience, render, *, actor_email=None)`; `inbox.ADMINS`

- [ ] **Step 1: Add the assertions to the existing test**

In `check_intake_gate.py`, after the `quote_failed` assertions, add:

```python
from app import inbox  # noqa: E402  (add beside the other imports at the top)

inbox.use_identity("riku@neptune.ph", "u1")
kinds = [note.kind for note in inbox.listing()]
ok("a failed pass raises a note", "intake.quote_failed" in kinds)
```

`inbox.listing(limit=30, person="")` (`inbox.py:355`) is the reader; it answers for whoever
`use_identity` last named. The roster must have an admin for `inbox.ADMINS` to resolve to anybody —
add `members.claim("riku@neptune.ph", "u1")` after the workspace is created, following
`backend/app/members.py`.

- [ ] **Step 2: Run it and watch the new assertion fail**

```
cd backend
.venv/Scripts/python.exe scripts/check_intake_gate.py
```

Expected: `FAIL  a failed pass raises a note`.

- [ ] **Step 3: Notify on failure**

Extend the `stamp` helper so a failure is announced. A quotation that failed silently is an intake nobody comes back to:

```python
        if to == intakes.QUOTE_FAILED:
            entry = intakes.get(intake_id)
            inbox.notify(
                "intake.quote_failed",
                inbox.ADMINS,
                {
                    "title": "A client request could not be quoted",
                    "body": (entry.client_email if entry else "") + " — " + str(fields.get("error", "")),
                    "href": "#/intakes",
                },
            )
```

- [ ] **Step 4: Run the test**

```
cd backend
.venv/Scripts/python.exe scripts/check_intake_gate.py
```

Expected: `all pass`.

- [ ] **Step 5: Checkpoint**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes.py
.venv/Scripts/python.exe scripts/check_intakes_api.py
.venv/Scripts/python.exe scripts/smoke.py
```

All exit 0.

---

## Task 5: Types and the client calls

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `Intake`, `IntakeState`; `listIntakes()`, `fetchIntake(id)`, `createIntake(body)`, `closeIntake(id)`

- [ ] **Step 1: Add the types**

In `frontend/src/types.ts`, mirroring `backend/app/intakes.py` exactly:

```ts
/** Where a client request has got to. The server refuses any other value. */
export type IntakeState =
  | 'submitted'
  | 'preparing'
  | 'quoted'
  | 'quote_failed'
  | 'closed'
  // Defined in the machine, unreachable until the client link ships.
  | 'issued'
  | 'sent'
  | 'revision_requested'
  | 'finalized'
  | 'proposal_sent'

export type Intake = {
  id: string
  state: IntakeState
  created_at: string
  created_by: string
  client_email: string
  client_phone: string
  scope: string
  budget_text: string
  preset: Record<string, string>
  job_id: string
  bundle_ids: string[]
  document_id: string
  priced_scope: string
  priced_budget: string
  error: string
  closed_at: string
  closed_by: string
}
```

- [ ] **Step 2: Add the calls**

In `frontend/src/lib/api.ts`, following the shape of the existing workspace calls:

```ts
export function listIntakes(options: CallOptions = {}): Promise<Intake[]> {
  return call<Intake[]>('/api/intakes', {}, options)
}

export function fetchIntake(id: string, options: CallOptions = {}): Promise<Intake> {
  return call<Intake>(`/api/intakes/${encodeURIComponent(id)}`, {}, options)
}

export function createIntake(
  body: {
    client_email: string
    client_phone: string
    scope: string
    budget_text: string
    preset: Record<string, string>
  },
  options: CallOptions = {},
): Promise<Intake> {
  return call<Intake>('/api/intakes', { method: 'POST', json: body }, options)
}

export function closeIntake(id: string, options: CallOptions = {}): Promise<Intake> {
  return call<Intake>(`/api/intakes/${encodeURIComponent(id)}/close`, { method: 'POST' }, options)
}
```

Read the existing `call` helper before writing these and match its real signature — the option names above are the ones used by the neighbouring workspace functions, and if they differ, the neighbours win.

- [ ] **Step 3: Typecheck**

```
cd frontend
npm run typecheck
```

Expected: zero errors.

---

## Task 6: The For You / For Client toggle

**Files:**
- Modify: `frontend/src/components/HomeScreen.tsx`, `frontend/src/index.css`

- [ ] **Step 1: Split the destinations**

In `HomeScreen.tsx`, rename the existing `DESTINATIONS` to `FOR_YOU` and add a second array beneath it, keeping the same object shape and the same icon style (stroked paths, no fills):

```tsx
//: What the studio does for itself. The existing four, unchanged.
const FOR_YOU = [
  /* the current DESTINATIONS entries, moved here verbatim */
]

//: What the studio does with a client in the room. Two for now; the client link
//: and the sent-quotation queue join them when Stage 2 ships.
const FOR_CLIENT = [
  {
    href: '#/intakes/new',
    label: 'New client request',
    detail: 'What they asked for, in their words',
    icon: (
      <>
        <path d="M4 6.5h16v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
        <path d="M4 7l8 6 8-6" />
      </>
    ),
  },
  {
    href: '#/intakes',
    label: 'Client requests',
    detail: 'What is waiting on you',
    icon: (
      <>
        <path d="M4.5 5.5h15M4.5 12h15M4.5 18.5h9" />
      </>
    ),
  },
]
```

- [ ] **Step 2: Add the pill**

Inside the component, above the grid:

```tsx
const [side, setSide] = useState<'you' | 'client'>('you')
const destinations = side === 'you' ? FOR_YOU : FOR_CLIENT
```

```tsx
<div className="mb-6 flex justify-center">
  <div className="pill" role="tablist" aria-label="Whose work">
    {([
      ['you', 'For You'],
      ['client', 'For Client'],
    ] as const).map(([id, label]) => (
      <button
        key={id}
        type="button"
        role="tab"
        aria-selected={side === id}
        onClick={() => setSide(id)}
        className={`pill__tab ${side === id ? 'pill__tab--on' : ''}`}
      >
        {label}
      </button>
    ))}
  </div>
</div>
```

Then change the `.map` to iterate `destinations` instead of `DESTINATIONS`. Leave the `isAdmin` filter exactly as it is.

- [ ] **Step 3: Add the pill styles**

In `frontend/src/index.css`, inside `@layer components`:

```css
  /* A two-way switch, not a nav. Used where one screen answers the same
     question for two different audiences. */
  .pill {
    display: inline-flex;
    gap: 2px;
    padding: 3px;
    border-radius: var(--radius-pill);
    background-color: var(--color-duplicate);
    border: 1px solid var(--color-rule);
  }

  .pill__tab {
    padding: 0.45rem 1.1rem;
    border-radius: var(--radius-pill);
    font-family: var(--font-label);
    font-size: 13px;
    color: var(--color-void);
    transition: background-color 150ms var(--ease-press), color 150ms var(--ease-press);
  }

  .pill__tab--on {
    background-color: var(--color-paper);
    color: var(--color-ink);
    box-shadow: var(--shadow-sheet);
  }
```

Check the real names of `--radius-pill`, `--ease-press` and `--shadow-sheet` in the `@theme` block before writing this; use whatever is actually defined.

- [ ] **Step 4: Typecheck and build**

```
cd frontend
npm run typecheck
npx vite build
```

Both clean.

---

## Task 7: The intake screen and the queue

**Files:**
- Create: `frontend/src/components/IntakeScreen.tsx`, `frontend/src/components/IntakeListScreen.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `createIntake`, `listIntakes`, `closeIntake` from Task 5; `CARD`, `DISPLAY`, `MONO_LABEL`, `WELL`, `ACTION`, `ACTION_PRIMARY` from `./tokens`

- [ ] **Step 1: Build the create screen**

`IntakeScreen.tsx`: a card titled **New client request**, four fields — Email, Contact no. (optional), Scope (a textarea using the existing `.pad-brief` class), Budget — and a submit that calls `createIntake` then sets `window.location.hash = '#/intakes'`.

The blurb under the title, verbatim:

> What the client told you, in their words. You price it on the next screen.

Field labels: `Client email`, `Contact no.`, `Scope`, `Budget`. Under Budget, the hint that makes the advisory decision visible:

> Their figure, as they said it. It guides the quotation; it does not set the price.

Follow `WorkspacesScreen.tsx` for the error and busy patterns.

- [ ] **Step 2: Build the queue**

`IntakeListScreen.tsx`: `listIntakes()` on mount, then rows grouped by what is waiting on whom. Each row shows the client email, the first line of the scope, the created date, and a state chip using the existing `.chip` / `.chip--alert` classes.

The three groups, in this order and with these headings:

- **Waiting on you** — `submitted` and `quote_failed`
- **Being prepared** — `preparing`
- **Quoted** — `quoted`

Closed intakes go under a **Closed** heading at the bottom, greyed.

Each `submitted` row carries one primary action, `Price this`, an `<a href={`#/pad/${row.id}`}>`. Each open row carries `Close` in a `RowMenu`, matching `WorkspacesScreen.tsx`.

Empty state: *"No client requests yet. Start one from the front page."*

- [ ] **Step 3: Route them**

In `App.tsx`: add `'intakes'` and `'intakeNew'` to the `Route` union; in `routeFor`, match `/intakes/new` **before** `/intakes` (the specific path first, or `/intakes/new` resolves to the list); add both to `SCREEN_NAME` as `Client requests` and `New client request`; add both to the shell route list and render them.

- [ ] **Step 4: Typecheck and build**

```
cd frontend
npm run typecheck
npx vite build
```

Both clean.

- [ ] **Step 5: Check it by hand**

Start the API and the web client, then: front page → **For Client** → **New client request** → fill four fields → submit → the queue shows one row under **Waiting on you**.

---

## Task 8: Prefilling the pad

**Files:**
- Modify: `frontend/src/components/BriefForm.tsx`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `fetchIntake` from Task 5
- Produces: `BriefForm` accepts `prefill?: { scope: string; budget: string; clientName: string }` and `intakeId?: string`

- [ ] **Step 1: Take the id off the address**

In `App.tsx`, `routeFor` already sends `#/pad/<anything>` to the pad. Read the id where the pad renders:

```tsx
const padIntakeId = (window.location.hash || '').replace(/^#\/pad\/?/, '')
```

- [ ] **Step 2: Fetch it**

```tsx
const [intake, setIntake] = useState<Intake | null>(null)

useEffect(() => {
  if (!padIntakeId) {
    setIntake(null)
    return
  }
  let live = true
  fetchIntake(padIntakeId)
    .then((found) => live && setIntake(found))
    // A stale link is not a reason to refuse the pad. It opens empty, which is
    // the screen the studio would otherwise have gone to anyway.
    .catch(() => live && setIntake(null))
  return () => {
    live = false
  }
}, [padIntakeId])
```

- [ ] **Step 3: Pass it down**

```tsx
<BriefForm
  defaults={defaults}
  pending={pending}
  job={creation.job}
  onSubmit={handleSubmit}
  intakeId={intake ? intake.id : ''}
  prefill={
    intake
      ? { scope: intake.scope, budget: intake.budget_text, clientName: intake.client_email }
      : undefined
  }
/>
```

- [ ] **Step 4: Seed the form once**

In `BriefForm.tsx`, accept the two props and seed the existing form state from `prefill` when it first arrives — once only, so a studio edit is never overwritten by a re-render:

`BriefForm` holds one `useState` per field rather than a form object — `setBrief`
(`BriefForm.tsx:83`), `setClientName` (`:86`) and `setBudgetHint` (`:88`). Seed those three, once:

```tsx
const seeded = useRef(false)

useEffect(() => {
  if (!prefill || seeded.current) return
  seeded.current = true
  setBrief(prefill.scope)
  setBudgetHint(prefill.budget)
  setClientName(prefill.clientName)
}, [prefill])
```

`useRef` and `useEffect` must be added to the React import on line 1, which currently brings in only
`useCallback` and `useState`.

Note where the budget lands: `setBudgetHint` feeds `budget_hint` (`BriefForm.tsx:310`), the field the
model reasons about. It is **not** `target_total`, which is the binding figure the server solves
arithmetic onto. That is decision 2 in the spec, and it is enforced here by which setter this line
calls.

- [ ] **Step 5: Send the id**

Where `BriefForm` builds its `FormData`, append the intake id when there is one:

```tsx
if (intakeId) data.append('intake_id', intakeId)
```

- [ ] **Step 6: Typecheck and build**

```
cd frontend
npm run typecheck
npx vite build
```

- [ ] **Step 7: The end-to-end check**

Start both servers. Front page → For Client → New client request → fill it in → submit → queue → **Price this** → the pad opens with the scope and the budget already in it → press Generate → the quotation prepares as normal → the queue moves the row from **Waiting on you** to **Quoted**.

Confirm the two things this feature exists for:

1. `GET /api/intakes/<id>` shows `scope` (what they asked) and `priced_scope` (what you generated from) as separate fields.
2. `target_total` is empty on the resulting bundle unless you typed one yourself.

- [ ] **Step 8: Final checkpoint**

```
cd backend
.venv/Scripts/python.exe scripts/check_intakes.py
.venv/Scripts/python.exe scripts/check_intakes_api.py
.venv/Scripts/python.exe scripts/check_intake_gate.py
.venv/Scripts/python.exe scripts/check_kind_render.py
.venv/Scripts/python.exe scripts/smoke.py
cd ../frontend
npm run typecheck
npx vite build
```

All exit 0. Delete `backend/app/main.py.bak`.

---

## Self-review

**Spec coverage.** Stage 1 of the spec asks for: `intakes.py` with the record and the machine (Task 1), `forget()` wired into workspace deletion (Task 1 Step 4), the four `/api/intakes` routes with explicit permissions (Task 2), the `intake_id` field and three stamps (Task 3), notifications (Task 4), the toggle (Task 6), the create screen and queue (Task 7), the `BriefForm` prefill prop and `App.tsx` passing `intake_id` (Task 8). The module-level token index is **not** built: it is unused in Stage 1, and a cache with no reader is a cache that rots. It is named in the spec as part of `intakes.py`'s design and belongs in the Stage 2 plan.

**Retention** is unresolved in the spec and unimplemented here. Intakes are kept indefinitely. When a period is chosen it is one function in `intakes.py` and one call site.

**Type consistency.** `IntakeState` in `types.ts` lists exactly the ten constants in `intakes.py`. `Intake` in `types.ts` carries exactly the seventeen fields on the Pydantic model. `advance(intake_id, to, **fields)` is called with `job_id`, `bundle_ids`, `priced_scope`, `priced_budget` and `error` — all of which are fields on the model, which `advance` enforces with its `hasattr` check.

**Corrected during self-review.** Two signatures were wrong on the first pass and are worth knowing
about, because both would have compiled and then behaved wrongly:

- `_require_admin_of(request, workspace_id)` (`main.py:2109`) takes two arguments and asks about
  *another named* workspace. The intake routes need the role for the workspace already open, which
  the gate puts on `request.state.role` (`main.py:308`). Task 2 now defines `_require_admin(request)`
  for that.
- The inbox's reader is `inbox.listing()` (`inbox.py:355`), not `inbox.read()`.

- Task 8 first assumed `BriefForm` held a single form object. It holds one `useState` per field, so
  the seeding step now calls `setBrief`, `setBudgetHint` and `setClientName` by name, and says which
  React hooks the file's import line is missing.

Every symbol this plan names has been checked against the file it lives in.
