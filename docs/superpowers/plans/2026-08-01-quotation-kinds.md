# Quotation Kinds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A quotation carries the discipline it belongs to, the requirements document takes its shape and title from that discipline, and Create PAD asks for it first — one question on screen at a time.

**Architecture:** One new module (`app/kinds.py`) is the single source of truth for what a kind is: its label, the title its document prints, the sections that document has, and the guidance handed to the model. Everything else reads it. `Estimate` gains `kind` (default `"software"`), so all 159 existing bundles keep printing exactly what they print today. `DeveloperSpec` gains a `sections` list for non-software kinds; its typed software fields stay untouched and unread for those kinds.

**Tech Stack:** FastAPI + pydantic v2 (backend), google-genai structured output, reportlab (PDF), React 18 + Tailwind v4 (frontend). No new dependencies.

## Global Constraints

- `backend/app/schemas.py` forbids `Optional[...]` unions and bare `dict`/`Any` — the Gemini structured-output layer degrades on both. New shapes are small models in lists.
- Every schema field has a default, so a partial model response still validates.
- **This repository is not under git.** Every step that would commit is replaced by "checkpoint" — run the stated check and confirm it passes before moving on.
- There is no test framework. Verification is `backend/scripts/smoke.py` plus small throwaway check scripts run with `backend/.venv/Scripts/python.exe`.
- Existing behaviour for software quotations must be byte-identical. A bundle with no `kind` field renders exactly as it does today.
- The filename stem stays `-requirements.md`. Only the label a reader sees changes.
- Server owns all arithmetic; nothing in this plan touches costing.

---

### Task 1: `kinds.py` — what a discipline is

**Files:**
- Create: `backend/app/kinds.py`
- Modify: `backend/app/schemas.py` (add `SpecSection`, `DeveloperSpec.sections`, `Estimate.kind`, `Estimate.kind_label`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Kind` NamedTuple: `id: str`, `label: str`, `noun: str`, `sections: tuple[SectionSpec, ...]`, `guidance: str`
  - `SectionSpec` NamedTuple: `id: str`, `heading: str`
  - `KINDS: tuple[Kind, ...]` — ids `software`, `accounting`, `engineering`, `design`, `marketing`, `other`
  - `resolve(kind_id: str) -> Kind` — never raises; unknown or empty returns the software kind
  - `title_for(estimate) -> str` — `"Accounting Requirements — Ledger cleanup"`; for `other`, the estimate's `kind_label` capped at 40 characters, falling back to `"Project Requirements"`
  - `is_software(estimate) -> bool`

- [ ] **Step 1: Write the check first**

`backend/scripts/check_kinds.py`:

```python
import sys
sys.path.insert(0, "scripts")
from app import kinds
from app.schemas import Estimate

def main() -> int:
    bad = 0
    def check(label, ok):
        nonlocal bad
        print(("ok    " if ok else "FAIL  ") + label)
        bad += 0 if ok else 1

    check("six kinds ship", len(kinds.KINDS) == 6)
    check("an unknown kind is software", kinds.resolve("nonsense").id == "software")
    check("an empty kind is software", kinds.resolve("").id == "software")
    check("software declares no sections", kinds.resolve("software").sections == ())
    check("accounting declares its own", len(kinds.resolve("accounting").sections) >= 5)

    plain = Estimate(project_name="Ledger cleanup", kind="accounting")
    check("the title names the discipline",
          kinds.title_for(plain) == "Accounting Requirements — Ledger cleanup")

    old = Estimate(project_name="Booking platform")
    check("a quotation with no kind is software", kinds.is_software(old))

    other = Estimate(project_name="Induction", kind="other", kind_label="Training")
    check("other takes the typed label", kinds.title_for(other).startswith("Training Requirements"))

    unnamed = Estimate(project_name="Induction", kind="other")
    check("other with no label is not blank",
          kinds.title_for(unnamed).startswith("Project Requirements"))

    long_label = Estimate(project_name="X", kind="other", kind_label="Q" * 90)
    check("a pasted label cannot run away", len(kinds.title_for(long_label)) < 80)
    return bad

raise SystemExit(main())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && ./.venv/Scripts/python.exe scripts/check_kinds.py`
Expected: `ModuleNotFoundError: No module named 'app.kinds'`

- [ ] **Step 3: Add the schema fields**

In `backend/app/schemas.py`, above `DeveloperSpec`:

```python
class SpecSection(BaseModel):
    """One part of a requirements document, for a discipline that is not software.

    A small model in a list rather than a dict: this file's own constraint, and
    the reason is the structured-output layer rather than taste.
    """

    heading: str = ""
    body: str = ""
    points: List[str] = Field(default_factory=list)
```

Add to `DeveloperSpec`:

```python
    sections: List[SpecSection] = Field(
        default_factory=list,
        description=(
            "The document's parts, for every discipline except software. Software fills the "
            "typed fields above instead; nothing reads both."
        ),
    )
```

Add to `Estimate`:

```python
    kind: str = Field(
        default="software",
        description=(
            "The discipline this work belongs to - see app/kinds.py. Defaults to software "
            "because every quotation prepared before kinds existed is one."
        ),
    )
    kind_label: str = Field(
        default="",
        description="What the studio called it, when the kind is 'other'. Empty otherwise.",
    )
```

- [ ] **Step 4: Write `kinds.py`**

Six kinds. `software` has empty `sections` (it uses the typed fields); the other five each declare 5–7 sections with headings a person in that discipline would recognise. `other` uses the generic set: scope, approach, deliverables, acceptance, assumptions, risks.

`guidance` is one paragraph per kind, naming the vocabulary and what to avoid — for accounting, "periods, ledgers, reconciliations, statutory deadlines; never a tech stack".

- [ ] **Step 5: Run the check**

Run: `cd backend && ./.venv/Scripts/python.exe scripts/check_kinds.py`
Expected: every line `ok`.

- [ ] **Step 6: Checkpoint** — `./.venv/Scripts/python.exe -c "import app.main"` imports clean.

---

### Task 2: the model is told which shape to write

**Files:**
- Modify: `backend/app/prompts.py`
- Modify: `backend/app/gemini_service.py` (pass the kind through to the prompt builder)

**Interfaces:**
- Consumes: `kinds.resolve`, `Kind.guidance`, `Kind.sections`
- Produces: the same `generate_estimate(...)` signature plus a `kind: str = "software"` keyword.

- [ ] **Step 1: Write the check**

`backend/scripts/check_kind_prompt.py` — build the instruction for each kind and assert:
- the software instruction is character-for-character what it is today (capture it before the change),
- an accounting instruction contains the word "ledger" and does **not** contain "tech stack", "API" or "deployment",
- every non-software instruction names each of that kind's section headings.

- [ ] **Step 2: Run it and watch it fail.**

- [ ] **Step 3: Add one kind-specific paragraph to the prompt builder.** Software keeps today's wording exactly — the branch adds text for other kinds rather than rewriting the existing path.

- [ ] **Step 4: Run the check.** Expected: every line `ok`.

- [ ] **Step 5: Checkpoint** — `scripts/smoke.py` still passes (it runs the software path).

---

### Task 3: the requirements document takes its shape from the kind

**Files:**
- Modify: `backend/app/renderers/markdown.py:530-560` (the developer-requirements renderer)

**Interfaces:**
- Consumes: `kinds.resolve`, `kinds.title_for`, `kinds.is_software`, `DeveloperSpec.sections`
- Produces: `render_developer_requirements(estimate) -> str`, unchanged signature.

- [ ] **Step 1: Write the check**

`backend/scripts/check_kind_render.py`: build one fixture estimate; render it as each of the six kinds; assert
- software output is byte-identical to the output before the change (capture first),
- an accounting render contains none of `tech stack`, `API`, `deployment`, `endpoint`,
- every declared section heading appears,
- requirements, line items, phases and acceptance criteria appear for every kind.

- [ ] **Step 2: Run it and watch it fail.**

- [ ] **Step 3: Branch once at the top of the renderer.** Software takes the existing path untouched; every other kind renders `spec.sections` in the order its kind declares, skipping any the model left empty rather than printing a bare heading.

- [ ] **Step 4: Run the check.** Expected: every line `ok`, including the byte-identical software render.

- [ ] **Step 5: Checkpoint** — `scripts/smoke.py` passes.

---

### Task 4: the title follows the kind everywhere it is printed

**Files:**
- Modify: `backend/app/renderers/markdown.py:542`
- Modify: `backend/app/renderers/html.py:855`
- Modify: `backend/app/renderers/pdf.py:647`
- Modify: `backend/app/main.py:357` (`_document_title`) and `:118` (the API description)

**Interfaces:**
- Consumes: `kinds.title_for`
- Produces: nothing new.

- [ ] **Step 1: Write the check** — render an accounting bundle to markdown, print HTML and PDF, and assert each carries "Accounting Requirements" and none carries "Developer requirements". Render a bundle with no kind and assert all three still say "Developer requirements".

- [ ] **Step 2: Run it and watch it fail.**

- [ ] **Step 3: Replace the five hardcoded strings** with `kinds.title_for(estimate)` / the kind's noun. The PDF's running label takes the short form (`"Accounting Requirements"`), not the title with the project name in it — the page furniture already prints the project on the right.

- [ ] **Step 4: Run the check.** Expected: every line `ok`.

- [ ] **Step 5: Checkpoint** — `scripts/smoke.py` passes; a PDF still renders for both kinds.

---

### Task 5: the kind arrives with the brief

**Files:**
- Modify: `backend/app/main.py` (`create_proposal` form fields, and the tier/revision paths that rebuild an estimate)
- Modify: `backend/app/gemini_service.py` call site

**Interfaces:**
- Consumes: Task 1's `kinds.resolve`, Task 2's `kind=` keyword
- Produces: `POST /api/proposals` accepts `kind: str = Form("software")` and `kind_label: str = Form("")`; both are stored on the estimate before costing.

- [ ] **Step 1: Write the check** — post a brief with `kind=accounting` against a stubbed model, and assert the stored bundle's `estimate.kind == "accounting"`; post without a kind and assert it stores `software`; post `kind=nonsense` and assert it stores `software`.

- [ ] **Step 2: Run it and watch it fail.**

- [ ] **Step 3: Add the form fields**, normalise through `kinds.resolve`, and set them on the estimate the moment it comes back — before `recompute`, so everything downstream sees it. A revision inherits its parent's kind and cannot change it.

- [ ] **Step 4: Run the check.**

- [ ] **Step 5: Checkpoint** — `scripts/smoke.py` passes.

---

### Task 6: Create PAD asks the question first

**Files:**
- Modify: `frontend/src/components/BriefForm.jsx` (new first step, the rail entry, the submit payload)
- Modify: `frontend/src/components/StepRail.jsx` (remove the numbers)
- Modify: `frontend/src/lib/api.js` (`createProposal` sends `kind` and `kind_label`)
- Create: `frontend/src/components/KindPicker.jsx`

**Interfaces:**
- Consumes: `POST /api/proposals` from Task 5.
- Produces: nothing other components read.

- [ ] **Step 1: Write the check** — `node -e` over the built bundle asserting `createProposal` appends `kind`; and a DOM-free assertion that `KindPicker` exports six options with ids matching the backend's.

- [ ] **Step 2: Run it and watch it fail.**

- [ ] **Step 3: Build `KindPicker`** — a row of cards, one per kind, each with its label and one line of what it is for. "Something else" reveals a single text field, which is the only free text on the step.

- [ ] **Step 4: Make it the pad's first step.** The rail shows the chosen kind's label as its answer. Per the kit's rule the rail loses its numbers: progress is the thread and the answers beside it, never "3 of 5".

- [ ] **Step 5: Run the check, and `npm run build`.** Expected: build clean, checks `ok`.

- [ ] **Step 6: Checkpoint** — with both servers up, prepare one accounting quotation end to end and read its second sheet.

---

## Self-review

**Spec coverage.** Every section of the spec maps to a task: `kinds.py` and the schema → Task 1; the prompt → Task 2; the document's shape → Task 3; the six naming sites → Task 4; the API → Task 5; the pad and the rail → Task 6. The spec's "deliberately not in scope" list (studio-defined kinds, backfilling, per-kind roles, changing the client quotation) has no tasks, correctly.

**Placeholders.** None. Where a step says "one paragraph per kind" the shape and an example are given; the words themselves are the writing, not a deferred decision.

**Type consistency.** `resolve`, `title_for`, `is_software`, `Kind`, `SectionSpec`, `SpecSection`, `Estimate.kind`, `Estimate.kind_label`, `DeveloperSpec.sections` are named identically in every task that uses them. The frontend's kind ids match `KINDS` exactly: `software`, `accounting`, `engineering`, `design`, `marketing`, `other`.

**Risk noted.** Task 3's "byte-identical software render" check is the one that protects 159 existing quotations. It must be captured **before** any edit, or it proves nothing.
