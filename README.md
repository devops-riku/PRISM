# PRISM

One brief in, two documents out.

You describe a job in plain language, optionally attach screenshots, sketches or
photos of a whiteboard, choose a currency and a market region, and submit once.
PRISM sends that to Gemini and gets back a single structured estimate, then
renders it as two documents:

| Document | Audience | What it contains |
|---|---|---|
| Client proposal | the person paying | understanding of the job, proposed solution, scope in and out, a priced line-item table, payment milestones, timeline, next steps |
| Developer requirements | the person building | numbered requirements with acceptance criteria, phases, tech stack, API surface, data model notes, testing and deployment notes, open questions |

There is no chat loop and no follow-up turns. One submission produces both
documents.

Everything is priced in the currency you selected, at rates for the market
region you selected. There is no currency conversion anywhere in the system.

The server recomputes all arithmetic after the model responds, so a document can
never show a total that disagrees with the sum of its rows.

---

## 60-second quickstart

You need Python 3.10 or newer, Node 20 or newer, and a Gemini API key.

1. Get a key (about 30 seconds — see below) and put it in `backend/.env`.
2. Start both servers with one command:

   Windows (PowerShell):

   ```
   powershell -ExecutionPolicy Bypass -File .\run.ps1
   ```

   macOS / Linux:

   ```
   chmod +x run.sh
   ./run.sh
   ```

3. Open http://localhost:5173

`run.ps1` and `run.sh` create the virtual environment, install backend and
frontend dependencies the first time, start the API on port 8000 and the web
client on port 5173, print both URLs, and shut both down on Ctrl+C. Later runs
skip the installs. To force a dependency reinstall, delete the marker file
`backend/.venv/.prism-deps-installed`.

If you would rather run the two servers yourself, see
[Running the servers manually](#running-the-servers-manually).

---

## Getting a Gemini API key

1. Go to https://aistudio.google.com/apikey
2. Sign in with a Google account.
3. Click **Create API key**. Pick an existing Google Cloud project or let it
   create one.
4. Copy the key. It starts with `AIza`.

Put it in `backend/.env`. Start from the example file:

Windows:

```
copy backend\.env.example backend\.env
```

macOS / Linux:

```
cp backend/.env.example backend/.env
```

Then edit `backend/.env`:

```
GEMINI_API_KEY=your-key-here
```

`backend/.env` is git-ignored. The key stays on the server — the browser never
receives it and never calls Google directly. Restart the API after changing the
file; `--reload` does not pick up environment changes on its own.

---

## Running the servers manually

Both servers must be running. The web client on 5173 proxies `/api` to the API
on 8000.

### Windows

Python is normally available on Windows only through the `py` launcher, so use
`py` rather than `python`. Run these from the project root:

```
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload --port 8000
```

The `py` launcher uses the interpreter of an activated virtual environment, so
`py -m uvicorn` runs the uvicorn you just installed. If `Activate.ps1` is blocked
by the execution policy, run `powershell -ExecutionPolicy Bypass` first, or use
`.venv\Scripts\activate.bat` from `cmd`.

### macOS / Linux

```
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

### Frontend, both platforms

In a second terminal:

```
cd frontend
npm install
npm run dev
```

Then open http://localhost:5173.

Check the API on its own at http://localhost:8000/api/health. It reports the
model in use and whether a key is configured.

---

## API surface

FastAPI app at `backend/app/main.py`, served on port 8000.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/proposals` | **202** `JobView` — accepts `multipart/form-data`; the quotation is prepared in the background |
| POST | `/api/proposals/{id}/revise` | **202** `JobView` — a revision is a new bundle, the parent is untouched |
| GET | `/api/jobs` | `[JobView]`, newest first |
| GET | `/api/jobs/{job_id}` | `JobView` — poll this until `state` is `done`, then fetch each `result_ids` entry |
| GET | `/api/proposals` | `[ProposalSummary]` |
| GET | `/api/proposals/{id}` | `ProposalBundle` |
| DELETE | `/api/proposals/{id}` | `204` |
| GET | `/api/proposals/{id}/files/{kind}.md` | `text/markdown` attachment |
| GET | `/api/proposals/{id}/files/{kind}.html` | printable HTML (print stylesheet, so the browser makes the PDF) |
| GET | `/api/proposals/{id}/files/{kind}.pdf` | `application/pdf`, rendered server-side by reportlab |
| GET | `/api/settings` · PUT `/api/settings` | `StudioDefaults` — rate card, tax basis, contingency visibility |
| GET | `/api/currencies` | `[{code, name, symbol}]` |
| GET | `/api/health` | `{status, model, key_configured}` |

`{kind}` is `proposal` or `requirements`.

A 202 means the brief was accepted, not that it was priced. Everything that can
be rejected outright — a bad currency, a payment schedule that does not total
100%, a target above its cap — is still rejected synchronously, before a job
exists. Pricing then runs behind the request, reporting each step as it actually
finishes. Jobs are mirrored to `generated/_jobs/`, so they survive a reload;
anything still running when the process dies comes back marked failed rather
than pretending to continue.

Form fields accepted by `POST /api/proposals`:

| Field | Type | Notes |
|---|---|---|
| `brief` | str | required |
| `currency` | str | ISO 4217, default `PHP` |
| `client_name` | str | optional |
| `project_name` | str | optional |
| `market_region` | str | optional |
| `budget_hint` | str | optional, free text |
| `timeline_hint` | str | optional, free text |
| `tax_mode` | str | `exclusive` (default), `inclusive`, or `none` |
| `tiers` | str | optional, e.g. `Basic, Standard, Extended` — one complete quotation each |
| `tier_ceiling` | str | optional, a price the top tier may not exceed |
| `images` | repeated file | optional, `image/*` only, at most 8 files, at most 8 MB each |

### Tiers come out in order

Tiers were priced concurrently once, each call scoping the work alone with no
idea what the others had charged. The ladder came back in whatever order the
model happened to land in — twice in a row the middle tier undercut the entry
tier, which is indefensible in front of a client whose own table reads
"Standard = Basic + SMS OTP".

They are now priced **top down, one after another**. Each tier is told what the
tier above actually cost and prices under it, and `_finalise` then holds every
tier strictly below the finished total of the one above — using the same solver
a target total uses, so effort moves and rates never do. A tier that has to be
brought down records `tier_order_enforced`, and the quotation page says so
rather than hiding it.

`tier_ceiling` is a **maximum, never a target**. The top tier lands at or under
it; every tier below is priced under the tier above. A quotation that already
fits is left where it is — padding scope up to a budget is how a client ends up
paying for work nobody chose.

### A typed figure is read on the same basis as the rates

`target_total` and `tier_ceiling` mean what `tax_mode` says they mean. Quoting
**exclusive**, they are the price of the work and the tax is added on top: a
target of 3,000,000 with VAT at 12% produces 3,000,000 of work, 360,000 of VAT
and a total of 3,360,000. Quoting **inclusive**, or with **no tax**, the typed
figure is the total itself.

Solving a net target as though it were the gross total is the same error as
quoting tax-inclusive rates and calling them net — it silently delivers the
client less work than they asked to buy, by exactly the tax rate.

The cost of the fix is wall clock: three tiers take about three and a half
minutes instead of one. That is what background jobs are for, and why they were
built first.

Errors return `{"detail": "..."}` with a real status code. A missing API key is a
`503` with an actionable message, not a stack trace.

CORS allows `http://localhost:5173` and `http://127.0.0.1:5173`.

Each result is written to `backend/generated/{id}/` as `bundle.json`,
`proposal.md` and `requirements.md`. That directory is git-ignored.

---

## Changing the model

Set `GEMINI_MODEL` in `backend/.env` and restart the API:

```
GEMINI_MODEL=gemini-2.5-pro
```

Verified model ids:

| Id | Notes |
|---|---|
| `gemini-3.5-flash` | default |
| `gemini-3.1-pro-preview` | |
| `gemini-2.5-pro` | |
| `gemini-2.5-flash` | |
| `gemini-pro-latest` | alias |
| `gemini-flash-latest` | alias |

`GET /api/health` echoes the model actually in use, which is the quickest way to
confirm the change took effect.

---

## Other settings

All of these live in `backend/.env` and are optional. `backend/.env.example`
documents them with their defaults.

| Key | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | none | Required to generate anything. |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Which model to call. |
| `GENERATED_DIR` | `generated` | Where bundles are written. Relative paths resolve against `backend/`. |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address and port. |
| `MAX_IMAGES` | `8` | How many images one brief may carry. |
| `MAX_IMAGE_BYTES` | `8388608` | Per-image ceiling, in bytes. |
| `MAX_BRIEF_CHARS` | `20000` | Ceiling on the brief itself. |

The `.env` file is located relative to `backend/`, not to your shell, so it is
found whether you start uvicorn from the project root or from `backend/`. It is
read once at startup — restart the API after editing it.

---

## How currency works

You pick a currency and a market region on the form. Both are passed to the
model, which prices every line item directly in that currency at rates for that
market. A quote in EUR for Germany is priced as a German job in euros — it is not
a Philippine quote converted at an exchange rate.

There is no FX rate table, no conversion step and no third currency anywhere in
the system. Changing the currency means asking for a fresh quote, not converting
an old one.

Money is stored as a plain number in the selected currency. Symbols, separators
and decimal places are applied only at render time, so the stored value is the
same number whichever document you are looking at.

---

## Getting a PDF

There is no PDF library in this project. Both documents produce a PDF through the
browser:

1. Open the document's print view — the print button on each sheet, or
   `GET /api/proposals/{id}/files/proposal.html` (or `requirements.html`)
   directly.
2. Press Ctrl+P (Cmd+P on macOS).
3. Choose **Save as PDF** as the destination, then save.

The print stylesheet handles page setup, so switch off "Headers and footers" in
the browser's print options if you want a clean sheet.

The `.md` endpoints give you the same content as Markdown if you would rather
paste it into your own template.

---

## Project layout

```
PRISM-/
  backend/
    app/
      main.py             FastAPI app and routes
      config.py           environment and settings
      gemini_service.py   the single Gemini call
      prompts.py          system instruction and brief assembly
      costing.py          recomputes all arithmetic server-side
      storage.py          writes and reads generated bundles
      schemas.py          the data contract - every module renders this
      renderers/
        __init__.py
        markdown.py       the two Markdown documents
        html.py           printable HTML with the print stylesheet
        money.py          currency formatting
    scripts/
      smoke.py            offline test of the costing and render path
    generated/            generated bundles, git-ignored
    requirements.txt
    .env.example
    .env                  you create this, git-ignored
  frontend/
    index.html
    package.json
    vite.config.js        dev server on 5173, proxies /api to 8000
    src/
      main.jsx
      App.jsx
      index.css
      components/         form and result sheets
      lib/
        api.js            calls the API
        format.js         number and money formatting
        currencies.js     the currency list
  docs/
    CONTRACT.md           API shape, SDK call, stack decisions
    DESIGN.md             visual direction
  README.md
  run.ps1
  run.sh
```

Stack: FastAPI, uvicorn, `google-genai` and Pydantic on the backend; Vite,
React 18 and Tailwind CSS v4 on the frontend. No database, no auth, no PDF
library.

---

## The offline test

`backend/scripts/smoke.py` exercises everything downstream of the Gemini call
without a key, a network connection or a running server. It builds a complete
`Estimate` by hand, runs it through `costing.recompute`, asserts the arithmetic
is internally consistent — line item subtotals sum to `cost.subtotal`, milestone
amounts sum to `cost.total`, the summary adds up — then renders both Markdown
documents and both printable HTML pages into `backend/generated/_smoke/`.

```
cd backend
.venv\Scripts\python scripts\smoke.py
```

(`.venv/bin/python scripts/smoke.py` on macOS and Linux.) It prints what it
proved, writes the four documents, echoes the first 40 lines of the client
proposal so the money formatting is visible, and exits non-zero on the first
failed assertion. Run it after touching `costing.py` or anything under
`renderers/`.

---

## Troubleshooting

### 503 when you submit a brief

The message reads: "Gemini key not configured. Add GEMINI_API_KEY to
backend/.env and restart the API."

The API started without a key. Check that `backend/.env` exists, that it contains
a `GEMINI_API_KEY=` line with your key after the `=`, and that you restarted
uvicorn afterwards. Confirm with http://localhost:8000/api/health —
`key_configured` must be `true`.

An environment variable already exported in your shell wins over the file, so an
empty `GEMINI_API_KEY` in the shell will mask a correct `.env`.

If the key is present but Google rejects it, the error will say so. Keys are
per-project; a key deleted or regenerated in AI Studio stops working immediately.

### "Form data requires python-multipart to be installed"

The API returns a 500 as soon as you submit, because `POST /api/proposals` is a
multipart upload and FastAPI cannot parse one without that package.

Install the dependencies into the virtual environment you are actually running
uvicorn from:

```
cd backend
.venv\Scripts\python -m pip install -r requirements.txt
```

(`.venv/bin/python -m pip install -r requirements.txt` on macOS and Linux.) The
usual cause is a second Python on the machine — calling the environment's own
interpreter, as above, sidesteps it. Failing that, delete `backend/.venv` and let
`run.ps1` / `run.sh` rebuild it.

### Port already in use

You will see `[Errno 10048]` or `address already in use` from uvicorn, or
`Port 5173 is already in use` from Vite. Vite runs with `strictPort`, so it fails
rather than sliding to 5174 and leaving you with a confusing CORS error later.
`run.ps1` and `run.sh` check both ports before starting anything and tell you
which one is taken.

Prefer freeing the port over changing it. On Windows:

```
netstat -ano | findstr :8000
taskkill /PID <pid> /F
```

On macOS and Linux:

```
lsof -i :8000
kill <pid>
```

The most common cause is an earlier run that was closed without shutting down.

If you genuinely must move a port, change it in both places or the two halves
stop talking to each other:

- API port: `PORT` in `backend/.env` (or the `--port` flag) **and**
  `PRISM_API_TARGET`, which `frontend/vite.config.js` reads to decide where to
  proxy `/api`, for example `PRISM_API_TARGET=http://localhost:8001 npm run dev`.
- Web client port: the `--port` flag **and** `ALLOWED_ORIGINS` in
  `backend/.env`, which otherwise permits only 5173.

`run.ps1` and `run.sh` hard-code 8000 and 5173, so start the servers manually
when you are running on other ports.

### CORS errors in the browser console

"blocked by CORS policy" means the page is not being served from an allowed
origin. The API allows exactly `http://localhost:5173` and
`http://127.0.0.1:5173`.

Use the Vite dev server at http://localhost:5173. Opening `frontend/index.html`
from the filesystem gives a `file://` origin, and hitting the API's own port
directly gives `http://localhost:8000` — both are blocked, and neither goes
through the `/api` proxy that makes the whole app same-origin in development.

If you are deliberately serving the client from somewhere else, add that origin
to `ALLOWED_ORIGINS` in `backend/.env` and restart the API:

```
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173
```

### `py -m uvicorn` says "No module named uvicorn"

Your `py` launcher is older than the virtual environment support it needs, so it
is running the system Python instead of the one in `.venv`. Call the environment's
interpreter directly. From the `backend` directory:

```
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

That is what `run.ps1` does, which is why it never hits this.
