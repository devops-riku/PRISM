# PRISM — build contract

Read this file completely before writing any code. Everything here is verified
against current documentation. Do not substitute remembered API shapes for what
is written below.

Project root: `PRISM-/`. Product name: **PRISM** — one brief in, two documents out.

---

## 1. What the product does

One submission. The user types a client brief, optionally attaches screenshots /
sketches / photos of whiteboards, picks a currency and a market region, and
submits. One Claude call returns a complete structured `Estimate`. The server
fixes the arithmetic and renders two documents:

| Document | Audience | Voice |
|---|---|---|
| **Client proposal** | the person paying | plain, confident, no jargon, costs in their currency |
| **Developer requirements** | the person building | numbered requirements, acceptance criteria, API surface, stack |

No chat loop. No follow-up turns. One shot.

---

## 2. Data contract

`backend/app/features/quotations/domain/models.py` is the canonical model contract. Import from it; never
redefine these models, never fork a second shape. `Estimate` is the only thing
the model returns. Both documents and the whole UI are renderings of that one object.

Key invariants:

- **No FX conversion anywhere.** The model prices line items directly in the
  requested currency for the requested market. Never introduce a rate table or
  ask the model to convert between currencies.
- **Server owns arithmetic.** The model's `subtotal`, `contingency_amount`,
  `tax_amount`, `total`, and `PaymentMilestone.amount` are advisory. `costing.py`
  recomputes all of them from `quantity × unit_rate` and the percentages. A
  document must never show a total that does not equal the sum of its rows. The
  implementation lives in `backend/app/features/quotations/domain/costing.py`.
- Money is a plain float in the selected currency. Formatting happens at render
  time only (`Intl.NumberFormat` on the client, a helper on the server).

---

## 3. Claude SDK — call shape

Package is **`anthropic`** (the official SDK). The adapter is
`backend/app/features/quotations/infrastructure/claude.py`; the client is built
lazily there so a missing key is a clean 503, never an import error.

```python
import anthropic

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=3)

# The Estimate schema is too large for structured outputs (the API refuses its
# compiled grammar), so it rides in the system prompt and the reply is
# validated with pydantic. Every Estimate field has a default.
system = [{
    "type": "text",
    "text": SYSTEM_INSTRUCTION + "

JSON SCHEMA FOR THE ESTIMATE OBJECT
" + schema_json,
    "cache_control": {"type": "ephemeral"},
}]

# The brief first, then one base64 image block per attachment.
content = [{"type": "text", "text": brief}, *image_blocks]

with client.beta.messages.stream(
    model=CLAUDE_MODEL,                      # default claude-opus-5-5
    max_tokens=64_000,
    system=system,
    messages=[{"role": "user", "content": content}],
    output_config={
        "effort": CLAUDE_EFFORT,
        # Advisory: the model paces its thinking to finish inside it. Without
        # it, thinking varied from nothing to ~50k tokens run to run.
        "task_budget": {"type": "tokens", "total": 32_000},
    },
    betas=["server-side-fallback-2026-07-01", "task-budgets-2026-03-13"],
    fallbacks="default",                     # refusal fallback, same call
) as stream:
    message = stream.get_final_message()

# stop_reason "refusal" or "max_tokens" -> 502; otherwise the text block's
# JSON object goes through Estimate.model_validate_json.
```

- `BriefCheck` and `ProposalNarrative` are small enough for structured
  outputs: they pass `output_format=<Model>` and read `parsed_output`.
- No `temperature`: current Claude models reject sampling parameters. Effort is
  the knob (`CLAUDE_EFFORT`, default `low`: `medium` on a full estimate thinks for
  minutes and can exhaust `max_tokens`).
- Images: Claude reads JPEG, PNG, GIF and WebP, 10 MB base64 each, 32 MB per
  request. The adapter downscales anything larger than 2576 px or 2 MB and
  re-encodes it as JPEG; an image it cannot open (HEIC) is left out and noted
  in `image_observations`.
- Model ids: `claude-opus-5-5` (default), `claude-sonnet-5-5`,
  `claude-haiku-5-5`. Read from `CLAUDE_MODEL`, not `ANTHROPIC_MODEL` (Claude
  Code reads that one from the shell).
- The key lives only in `backend/.env` as `ANTHROPIC_API_KEY`, server-side.
  The browser never sees it.

---

## 4. HTTP surface

FastAPI app served on **port 8000**. `backend/app/main.py` is the composition
root; every route below is defined in its owning feature's `presentation/`
package. Shared dependencies and middleware live under
`backend/app/shared/presentation/http/`.

```
POST /api/proposals            multipart/form-data -> 202 JobView
  fields: brief (required, str)
          currency (str, ISO 4217, default "PHP")
          client_name, project_name, market_region, budget_hint, timeline_hint (str)
          target_total, pricing_basis (str)
          tax_mode (str: exclusive | inclusive | none), tax_inclusive (str, legacy)
          deposit_pct, instalments, payment_cadence, deposit_trigger, payment_schedule (str)
          tiers, tier_ceiling (str)
          intake_id (str, optional - the client intake this quotation is being prepared for, if any)
          images (repeated UploadFile, optional, image/* only, <= 8 files, <= 8 MB each)

POST /api/proposals/{id}/revise  instruction, target_total -> 202 JobView

GET  /api/jobs                                -> [JobView], newest first
GET  /api/jobs/{job_id}                       -> JobView

GET  /api/proposals                           -> [ProposalSummary]
GET  /api/proposals/{id}                      -> ProposalBundle
DELETE /api/proposals/{id}                    -> 204
GET  /api/proposals/{id}/files/{kind}.md      -> text/markdown attachment
GET  /api/proposals/{id}/files/{kind}.html    -> printable HTML (print stylesheet -> PDF via browser)
GET  /api/proposals/{id}/files/{kind}.pdf     -> application/pdf (reportlab, server-side)
GET  /api/settings   PUT /api/settings        -> StudioDefaults
GET  /api/currencies                          -> [{code, name, symbol}]
GET  /api/health                              -> {status, model, key_configured}

POST /api/intakes                             application/json -> 201 Intake
  body:   client_email, client_phone (str)
          scope (required, str)
          budget_text (str)
          preset (dict - the pad settings this intake will be quoted under:
                  kind, currency, market region, tax basis, payment terms, tiers)
  Admin-only.

GET  /api/intakes                             -> [Intake], newest first
GET  /api/intakes/{intake_id}                 -> Intake
POST /api/intakes/{intake_id}/close           -> Intake
  Admin-only.
```

`{kind}` is `proposal` or `requirements`.

**JobView** — `backend/app/features/jobs/application/service.py`

```
id            str
kind          "quotation" | "revision"
title, detail str      what the reader recognises it by
state         "queued" | "running" | "done" | "failed"
stage         str      what is happening right now
steps         [{label, done}]
progress      float    0..1, computed from steps actually finished
result_ids    [str]    the quotations this produced; one per tier
error         str      why it stopped, in the reader's terms
created_at, updated_at, finished_at
```

`progress` is derived from completed steps, never from elapsed time. A running
job with no completed step reports 0.08 — enough to read as started, not enough
to claim ground it has not taken. The client polls `GET /api/jobs/{job_id}`
until `state` leaves `queued`/`running`, then fetches each `result_ids` entry.

Errors return `{"detail": "..."}` with a real status code. A missing API key is a
`503` with an actionable message, not a stack trace.

**Intake** — `backend/app/features/intakes/application/service.py`, not the
quotation domain model module: it is storage-side and
never reaches the model, the third kind of record beside a `ProposalBundle`
and a `ProposalDocument`, and the only one that moves.

```
id, state, created_at, created_by             str
client_email, client_phone, scope, budget_text str    what the client said, verbatim
preset                                        dict    the pad settings this will be quoted under
job_id, bundle_ids, document_id               str, [str], str
priced_scope, priced_budget                   str     scope/budget as they stood when Generate ran
error                                         str
closed_at, closed_by                          str
```

`state` is one of `submitted`, `preparing`, `quoted`, `quote_failed`, `closed`
in Stage 1; `issued`, `sent`, `revision_requested`, `finalized`,
`proposal_sent` are defined but refused until Stage 2 wires the actor that can
reach them. `POST /api/proposals`'s `intake_id` moves an intake through this
machine as a side effect of pricing it - see
`app.features.intakes.application.service.ALLOWED`.

CORS: allow `http://localhost:5174` and `http://127.0.0.1:5174`.

Persistence: structured records use the same SQL repositories on both database
modes—SQLite locally and PostgreSQL in production. Aggregate payloads are
validated by their Pydantic models. Generated Markdown and uploaded bytes stay
outside SQL under the workspace asset directory or DigitalOcean Spaces.

`python-multipart` **must** be in requirements.txt or every `UploadFile` request
500s on arrival.

---

## 5. Stack decisions — already made, do not re-litigate

- Backend: FastAPI + uvicorn, Python 3.10+, `google-genai`, `pydantic>=2`,
  SQLAlchemy 2, Alembic, Psycopg 3, `python-multipart`, `python-dotenv`.
- Frontend: Vite + React 18 + **Tailwind CSS v4**.
  - v4 is CSS-first: `@import "tailwindcss";` and a `@theme { ... }` block in
    `src/index.css`. **No `tailwind.config.js`. No `postcss.config.js`.**
  - Vite plugin: `import tailwindcss from '@tailwindcss/vite'` in
    `vite.config.js`. Deps: `tailwindcss@^4`, `@tailwindcss/vite@^4`.
  - Dev server on 5174, proxying `/api` to `http://localhost:8000`.
- PDF downloads are rendered server-side with ReportLab; printable HTML keeps
  the browser print stylesheet path as well. No headless browser is required.
- SQLite is the zero-configuration local database; PostgreSQL is the production
  database. Zitadel authentication remains optional. Frontend state uses plain
  `useState`.

---

## 6. File ownership — strictly disjoint

There is no git worktree isolation here. Write **only** the files listed for your
task. If you need something from another agent's file, code against the contract
in this document; do not create it yourself.

| Owner | Files |
|---|---|
| backend features | `backend/app/features/` — one package per business capability |
| shared backend | `backend/app/shared/` — configuration, attachment parsing, shared HTTP |
| composition | `backend/app/main.py`, `backend/requirements.txt`, `backend/.env.example` |
| frontend-shell | `frontend/package.json`, `vite.config.js`, `index.html`, `frontend/src/main.jsx`, `src/index.css`, `src/lib/api.js`, `src/lib/format.js`, `src/lib/currencies.js` |
| frontend-ui | `frontend/src/App.jsx`, everything under `frontend/src/components/` |
| scaffolding | `README.md`, `run.ps1`, `run.sh`, `.gitignore` |

The `backend/app/` root contains only `features/`, `shared/`, `main.py`, and
`__init__.py`. Use canonical feature imports; do not add flat proxy modules.

Cross-module imports that must line up:

```python
from app.features.quotations.domain.models import Estimate, ProposalBundle, GeneratedFile
from app.features.quotations.domain.costing import recompute
from app.features.rendering.presentation import render_client_proposal, render_developer_requirements, render_print_html
# render_client_proposal(estimate) -> str  (markdown)
# render_developer_requirements(estimate) -> str  (markdown)
# render_print_html(markdown: str, title: str, estimate: Estimate) -> str
from app.features.rendering.presentation.money import format_money
```

```js
// frontend-shell provides, frontend-ui consumes:
import { createProposal, fetchCurrencies, fileUrl } from './lib/api'
import { formatMoney, formatNumber } from './lib/format'
import { CURRENCIES } from './lib/currencies'
```

---

## 7. Quality floor

Responsive to 360px. Visible keyboard focus on every interactive element.
`prefers-reduced-motion` respected. Real empty states and real error states —
an error says what happened and what to do, and never apologises.
