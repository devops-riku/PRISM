# PRISM — build contract

Read this file completely before writing any code. Everything here is verified
against current documentation. Do not substitute remembered API shapes for what
is written below.

Project root: `PRISM-/`. Product name: **PRISM** — one brief in, two documents out.

---

## 1. What the product does

One submission. The user types a client brief, optionally attaches screenshots /
sketches / photos of whiteboards, picks a currency and a market region, and
submits. One Gemini call returns a complete structured `Estimate`. The server
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
Gemini returns. Both documents and the whole UI are renderings of that one object.

Key invariants:

- **No FX conversion anywhere.** Gemini prices line items directly in the
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

## 3. Gemini SDK — verified call shape

Package is **`google-genai`** (NOT the retired `google-generativeai`). There is no
`genai.configure()` and no `GenerativeModel` class. Use exactly this:

```python
import os
from google import genai
from google.genai import types

from app.features.quotations.domain.models import Estimate

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# contents is a flat list: text parts and image parts side by side.
contents = [system_and_brief_text]
for image_bytes, mime_type in images:
    contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

response = client.models.generate_content(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    contents=contents,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=Estimate,
        temperature=0.4,
        system_instruction=SYSTEM_INSTRUCTION,
    ),
)

estimate: Estimate = response.parsed   # already an Estimate instance
```

- Structured output composes with multimodal input — schema in `config`, images in
  `contents`, they are orthogonal.
- `response.parsed` yields the Pydantic instance. If it is `None`, fall back to
  `Estimate.model_validate_json(response.text)` and raise a clear error if that
  also fails.
- Verified model IDs: `gemini-3.5-flash` (default), `gemini-3.1-pro-preview`,
  `gemini-2.5-pro`, `gemini-2.5-flash`, and the aliases `gemini-pro-latest` /
  `gemini-flash-latest`. Read the id from `GEMINI_MODEL` so it is swappable.
- The key lives only in `backend/.env`, server-side. The browser never sees it.

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
  database. Supabase authentication remains optional. Frontend state uses plain
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
