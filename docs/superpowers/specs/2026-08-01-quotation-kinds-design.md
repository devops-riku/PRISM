# Quotation kinds, and a pad that asks one thing at a time

**Date:** 2026-08-01
**Status:** approved

## The problem

PRISM produces two documents from one brief: a quotation for the client and a
requirements specification for whoever builds the work. The second one is shaped
entirely for software — its fields are `tech_stack`, `api_surface`, `devops`,
`data_model_notes` — and it is titled "Developer requirements" everywhere it
appears.

That is correct for a web build and wrong for everything else a studio quotes.
An accounting engagement has no API surface. An engineering scope has no
deployment pipeline. Today the model is asked for those fields anyway, and it
answers, because a model asked for a tech stack will invent one.

Two changes, one feature:

1. A quotation carries a **kind** — the discipline the work belongs to — and the
   requirements document takes its shape and its title from that kind.
2. **Create PAD** is rebuilt on Clarity Kit section 01: one question on screen at
   a time, the answers accumulating beside it, and no step counters.

## Decisions taken

| Question | Decision | Why |
|---|---|---|
| How deep does the kind go? | **Sections per discipline.** Each kind defines what its requirements sheet contains. | A rename alone would still hand an accountant a tech stack. Letting the model choose the sections per project would mean two accounting quotations came back with different section sets and nothing on the page could be relied on to exist. |
| Which kinds ship? | **Six: Web / Software Development, Accounting & Finance, Engineering, Design, Marketing, and "Something else"** (typed name, generic sections). | Predictable output for the five PRISM can write well, and nobody is blocked by a missing option. Studio-defined kinds are a second template editor; not now. |
| The 159 quotations already on file | **They read as software.** `kind` defaults to `software`; nothing on disk is rewritten. | Every one of them *is* software — their sheets already carry tech stacks. A backfill would touch 159 files to record something their contents already imply, and this install has lost data to a bulk operation once already. |
| Where the kind is asked | **First question, its own step.** | It shapes the brief's prompt, the roles suggested and the second document. Asking it first is honest about that. |
| Pad layout | **Split canvas** — question left, answers right. | Closest to the existing `StepRail`, keeps the one-screen-no-scrolling rule, and the kit's "no step counters" rule becomes a deletion rather than a rebuild. |

## Architecture

### `backend/app/kinds.py` (new)

One module, one source of truth, shaped like `template.py`:

```python
class Kind(NamedTuple):
    id: str            # "software" | "accounting" | ... | "other"
    label: str         # "Web / Software Development"
    doc_title: str     # "Web Development Requirements"
    sections: tuple    # (SectionSpec(id, heading), ...) for non-software kinds
    guidance: str      # one paragraph handed to the model
    roles: tuple       # example roles, used only as prompt colour

KINDS: tuple[Kind, ...]
def resolve(kind_id: str, custom_label: str = "") -> Kind
def title_for(estimate) -> str      # "Accounting Requirements — Ledger cleanup"
```

`resolve` never raises: an unknown id resolves to `software`, which is what every
existing quotation is. `"other"` takes the typed label and uses the generic
section set (scope, approach, deliverables, acceptance, assumptions, risks).

The title `"other"` prints is the typed label plus "Requirements" — *Legal
Requirements*, *Training Requirements* — capped at 40 characters and falling back
to "Project Requirements" when nothing was typed. A heading is a place a stray
paste would otherwise be very visible.

### Schema (`backend/app/schemas.py`)

```python
class SpecSection(BaseModel):
    heading: str = ""
    body: str = ""
    points: List[str] = Field(default_factory=list)

class DeveloperSpec(BaseModel):
    ...existing software fields, unchanged...
    sections: List[SpecSection] = Field(default_factory=list)

class Estimate(BaseModel):
    kind: str = Field(default="software", ...)
    kind_label: str = Field(default="", ...)   # only for "other"
```

No `Optional`, no bare `dict` — `SpecSection` is a small model in a list, which
is what the file's own constraints require for the Gemini structured-output
layer.

Software fills the typed fields as it does today. Every other kind fills
`sections`, one entry per section its kind declares. The typed fields stay empty
for those kinds, and the renderer never reads them.

### Prompt (`backend/app/prompts.py`)

One kind-specific paragraph: what discipline this is, which sections to fill, and
the vocabulary to use. Same single call, same schema, different instruction. The
software path keeps today's wording exactly, so nothing about existing behaviour
moves.

### Renderers

`render_developer_requirements` branches once, at the top:

- **software** — today's renderer, untouched.
- **anything else** — heading, then one block per `SpecSection`; requirements,
  line items, phases, assumptions and acceptance criteria print as they already
  do, because those are not discipline-specific.

The title comes from `kinds.title_for(estimate)` in all six places it is
currently hardcoded:

| File | What changes |
|---|---|
| `renderers/markdown.py:542` | `"Developer requirements — {project}"` → `kinds.title_for(estimate)` |
| `renderers/html.py:855` | page title fallback |
| `renderers/pdf.py:647` | running page label |
| `main.py:357` | `_document_title` |
| `main.py:118` | the API's own description |
| Frontend `ResultSheets` / `SheetHeader` | the sheet's on-screen label |

The filename stem stays `-requirements.md`. It is in downloads folders and in
links already sent; the label on the page is what a reader sees.

### Frontend

`BriefForm` gains a first step: **What kind of work is this?** A row of cards,
one per kind, plus "Something else" which reveals a single text field. The
answer lands in the rail as the kind's label.

Per the kit's rule, `StepRail` loses its numbers: progress is the thread and the
answers beside it, never "3 of 5".

`lib/api.js` sends `kind` and, when it is `other`, `kind_label`.

## Data flow

```
pad: kind chosen ─┐
                  ├─► POST /api/proposals (kind, kind_label, brief, …)
brief typed ──────┘            │
                               ▼
                    prompts.build(kind) ── one Gemini call ──► Estimate
                               │                                  │
                               │                    kind stored on the estimate
                               ▼                                  ▼
                    costing.recompute()                renderers read kinds.resolve()
                                                                  │
                                          ┌───────────────────────┴──────────┐
                                          ▼                                  ▼
                              client quotation (unchanged)      "<Kind> Requirements"
```

## Failure modes

| What goes wrong | What happens |
|---|---|
| An unknown or empty `kind` arrives (an old bundle, a hand-edited file, a stale client) | `resolve()` returns software. Prints exactly what it printed before. |
| `"other"` with no typed label | Falls back to the generic sections and the plain title "Project Requirements". Never an empty heading. |
| The model returns no `sections` for a non-software kind | The sheet prints its other parts — requirements, line items, phases, acceptance — and the missing block is reported on the quotation the way every other gap already is, rather than rendering an empty heading. |
| The model fills software fields for an accounting kind | The renderer does not read them. They are stored and ignored. |
| A studio changes kinds between quotations | Nothing shared: the kind is on the estimate, snapshotted like everything else. An old quotation keeps its shape. |

## Testing

- `kinds.resolve()` — every id, an unknown id, an empty id, `"other"` with and
  without a label.
- Render each of the six kinds from one fixture estimate; assert the title, and
  that a non-software sheet contains none of "tech stack", "API", "deployment".
- Render a bundle with **no** `kind` field at all (as every existing one is) and
  assert the output is byte-identical to today's.
- The pad: the kind reaches the API, and the rail shows its label.
- `scripts/smoke.py` end to end, which already exercises both documents.

## Deliberately not in scope

- Studio-defined kinds in Settings. A second template editor; a later piece of
  work if a studio asks for one.
- Backfilling the 159 existing quotations. The default covers them.
- Per-kind rate card roles. The rate card is already per-studio and enforced;
  suggesting roles by discipline is prompt colour, not a feature.
- Changing the client-facing quotation. It is the same document whatever the
  discipline: scope, costing, terms.
