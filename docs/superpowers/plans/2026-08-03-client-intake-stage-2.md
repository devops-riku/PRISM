# Client Intake, Stage 2 — the client's link

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The studio sets the PAD configuration and generates a link; the client opens it with no account, submits four fields, watches one honest waiting face, then reads their quotation and either asks for a change or finalises it — which tells the studio.

**Architecture:** The intake record built in Stage 1 gains a token and the five states that were written but refused. One new route prefix, `/api/client/`, is the only anonymous surface in the app; the token resolves the workspace, so no id ever appears in a URL. The client's React shell is resolved in `main.tsx` *before* `AuthGate`, and fetches through a separate module that carries neither a session nor a workspace header.

**Tech Stack:** FastAPI + Pydantic v2, React 18 + TypeScript strict, Tailwind v4. No new dependencies.

## Global Constraints

- **This opens the first anonymous write endpoint in this codebase.** Every route under `/api/client/` is unauthenticated by design. Nothing else moves: `auth.OPEN_PATHS` gains one *method-aware clause*, not a blanket entry, and no existing route changes.
- **The token resolves the workspace.** Anonymous handlers ignore `X-Workspace` entirely, resolve through the token index, then `workspaces.borrow()` / `give_back()` in a `finally`. A workspace id must never appear in a client URL.
- **What the client is served is a named allowlist of fields, never a filtered bundle.** No object of the shape that carries internal document URLs, unit rates, day rates, tier siblings, the rate card, what the rate card removed, or the note explaining how scope moved to reach a number.
- **The client's file route pins `kind` to `proposal` inside the handler — a parameter that does not exist, not one that is validated.** The existing file routes serve `requirements`, the internal developer sheet. This is the single most likely real bug in the feature.
- **Each write is accepted only from the one state it belongs to.** Submit only from `issued`; revise and finalize only from `sent`. That state check is the abuse control on an unauthenticated POST.
- **A closed, expired, wrong and never-existed token return the identical response.** The endpoint must not be an oracle for which links exist.
- **The client's words are material, never instruction.** Scope and budget reach the prompt with the same framing `attachments.py` already applies, and with the length bounds Stage 1 added.
- **No mail.** Stage 3 owns email. The studio sends the link by hand.
- `backend/app/schemas.py` is not modified. TypeScript strict, zero errors. Tailwind v4 CSS-first. No test framework may be added; backend checks are standalone scripts under `backend/scripts/` that exit 0.
- Branch from `41c99c9` on a new branch. Baseline is Stage 1, complete and reviewed.

**Interpreter:** `backend/.venv/Scripts/python.exe`, run from `backend`.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/app/tokens.py` | Mint, index and resolve client tokens across workspaces |
| `backend/app/clientview.py` | The allowlisted projection of an intake and its quotation |
| `backend/scripts/check_tokens.py` | Index, expiry, relink, cross-workspace resolution |
| `backend/scripts/check_client_api.py` | The whole anonymous surface, including what it refuses |
| `frontend/src/lib/clientApi.ts` | Bare `fetch`, no session, no workspace header |
| `frontend/src/components/client/ClientShell.tsx` | Route on `#/c/<token>`, pick the face |
| `frontend/src/components/client/ClientForm.tsx` | Email, Contact no., Scope, Budget |
| `frontend/src/components/client/ClientWaiting.tsx` | The one waiting face for four studio states |
| `frontend/src/components/client/ClientQuotation.tsx` | The quotation, Ask for a change, Finalize |
| `frontend/src/components/client/ClientClosed.tsx` | The identical page for closed, expired, wrong, never-existed |

**Modified**

| File | Change |
|---|---|
| `backend/app/intakes.py` | Token fields; the five Stage 2 states become reachable; `relink`; index maintenance |
| `backend/app/auth.py` | One method-aware clause for `/api/client/` |
| `backend/app/main.py` | The client routes; `send`, `relink`; intake creation takes a real preset |
| `backend/app/prompts.py` | Framing for client-authored scope |
| `frontend/src/main.tsx` | Resolve `ClientShell` before `AuthGate` |
| `frontend/src/components/IntakeScreen.tsx` | Config + Generate link, replacing the four typed fields |
| `frontend/src/components/IntakeListScreen.tsx` | Send to client, Reissue link; the new states |
| `frontend/src/types.ts` | Token fields; the client-facing view types |
| `frontend/src/lib/api.ts` | `createIntake` returns the link; `sendIntake`, `relinkIntake` |
| `frontend/src/App.tsx` | The intake's preset reaches the pad's prefill |
| `frontend/src/components/BriefForm.tsx` | `BriefPrefill` carries the preset, seeded in one pass |

The last three were absent from this table when the plan was written and are named in the Task 9 amendment below. `api.ts` was a plain omission — nothing could call the new routes without it. `App.tsx` and `BriefForm.tsx` are where the preset is read back, which no task originally claimed.

---

## Task 1: Tokens, and the states that were waiting

**Files:**
- Create: `backend/app/tokens.py`
- Modify: `backend/app/intakes.py`
- Test: `backend/scripts/check_tokens.py`

**Interfaces:**
- Consumes: `intakes.Intake`, `workspaces.borrow/give_back/listing`, `storage.utc_now_iso`
- Produces:
  - `tokens.mint() -> str`
  - `tokens.resolve(token: str) -> tuple | None` — `(workspace_id, intake_id)` or `None`
  - `tokens.remember(token, workspace_id, intake_id) -> None`
  - `tokens.forget_token(token) -> None`
  - `tokens.forget_workspace(workspace_id) -> None`
  - `intakes.relink(intake_id) -> Intake`
  - `intakes.LIFETIME_DAYS = 60`

- [ ] **Step 1: Write the failing test**

Create `backend/scripts/check_tokens.py`. It must assert, each independently:

- `mint()` returns a distinct value each call, at least 32 characters, URL-safe (`^[A-Za-z0-9_-]+$`).
- An intake created with a token resolves to `(its workspace, its id)` **from a different current workspace** — this is the whole point of the index, so set the current workspace to a second one before resolving.
- An unknown token resolves to `None`.
- A token whose intake has expired (set `token_expires_at` into the past) resolves to `None`.
- `relink` issues a different token, and **the old one stops resolving** — assert both.
- Deleting a workspace makes its tokens stop resolving (`forget_workspace` wired into `workspaces.delete`).
- Resolution is **not** a directory scan per call: build 50 intakes across 2 workspaces, resolve a known token 200 times, and assert the whole loop takes under 0.5s. A scan would not.
- Comparison is constant-time: assert `tokens.resolve` uses `secrets.compare_digest` by reading the module source for that symbol. (Crude, and honest: it asserts the property is *implemented*, not timed.)

- [ ] **Step 2: Run it and watch it fail**

```
cd backend
.venv/Scripts/python.exe scripts/check_tokens.py
```
Expected: `ModuleNotFoundError: No module named 'app.tokens'`.

- [ ] **Step 3: Write `backend/app/tokens.py`**

The index is a module-level dict `token -> (workspace_id, intake_id)`, guarded by an `RLock`. It is built lazily on the first miss by walking `workspaces.listing()` with `borrow()`/`give_back()`, reading each intake once, and is maintained on every mint, relink and delete so the walk happens at most once per process.

Why not the `members.find_invite` pattern: that scans one small roster file per workspace. Intakes are one file each, so scanning per request would read every intake on the install for every wrong guess — which is exactly what an unauthenticated endpoint must not do.

Expiry is checked at resolve time against the intake's own `token_expires_at`, not held in the index, so a relink cannot leave a stale entry authoritative.

- [ ] **Step 4: Extend `backend/app/intakes.py`**

Add to `Intake`: `token: str = ""`, `token_expires_at: str = ""`.

Open the transition table to the states already defined:

```python
ALLOWED: dict = {
    ISSUED: {SUBMITTED, CLOSED},
    SUBMITTED: {PREPARING, CLOSED},
    PREPARING: {QUOTED, QUOTE_FAILED, CLOSED},
    QUOTED: {PREPARING, SENT, CLOSED},
    QUOTE_FAILED: {PREPARING, CLOSED},
    SENT: {REVISION_REQUESTED, FINALIZED, CLOSED},
    REVISION_REQUESTED: {PREPARING, CLOSED},
    FINALIZED: {PROPOSAL_SENT, CLOSED},
    PROPOSAL_SENT: {CLOSED},
    CLOSED: set(),
}
```

Delete the `STAGE_TWO` refusal in `advance` — it exists to refuse these, and they are now the feature. Keep `PROPOSAL_SENT` in the table but note in a comment that nothing reaches it until Stage 3.

Widen `ADVANCE_FIELDS` to include `revisions` (a list of `{asked, at}`) and `sent_bundle_id`.

`create` gains `state=ISSUED` and mints a token. `relink(intake_id)` mints a new token, forgets the old one, and resets `token_expires_at`.

Add `LIFETIME_DAYS = 60` with a comment saying why 60 and not 14 like an invitation: a client deciding on a quotation is on a slower clock than someone accepting a team invite.

- [ ] **Step 5: Wire `forget_workspace`**

In `backend/app/workspaces.py`'s `delete()`, beside the existing `intakes.forget(key)`.

- [ ] **Step 6: Run the test, then the Stage 1 suite**

```
cd backend
.venv/Scripts/python.exe scripts/check_tokens.py
.venv/Scripts/python.exe scripts/check_intakes.py
.venv/Scripts/python.exe scripts/check_intakes_api.py
.venv/Scripts/python.exe scripts/check_intake_gate.py
.venv/Scripts/python.exe scripts/smoke.py
```

All exit 0. `check_intakes.py` asserts Stage 2 states are *refused*; those assertions are now wrong and must be rewritten to assert the new table instead. Say so in your report rather than deleting them quietly.

- [ ] **Step 7: Commit**

---

## Task 2: The client's view of an intake

**Files:**
- Create: `backend/app/clientview.py`
- Test: extend `backend/scripts/check_tokens.py` with a `clientview` section

**Interfaces:**
- Produces: `clientview.of(intake, bundle) -> dict`

This module is the security boundary. It exists so that no handler ever hands a client an object it filtered — it builds a new one from named fields.

- [ ] **Step 1: Write the failing test**

Assert, on a real `ProposalBundle` built the way `check_intake_gate.py` builds one:

- The returned dict's keys are **exactly** the expected set — compare with `==`, not `<=`, so a future field cannot leak by being added upstream.
- For a `sent` intake it carries: `state`, `studio_name`, `reference`, `total`, `currency`, `validity`, `payment_schedule`, `narrative`, `sent_at`, `revisions` (each `{asked, at}`), `can_revise`, `can_finalize`.
- It carries **none** of: `line_items`, `rate_card_bound`, `rate_card_removed`, `target_note`, `tier_cap_note`, `tier_siblings`, `files`, `id`, `estimate`, `priced_scope`, `priced_budget`, `client_email`, `client_phone`, `job_id`, `bundle_ids`.
- Assert the last group by iterating the **serialised JSON string** and failing on any of those substrings — a nested leak is what this catches.
- For `issued` it carries only `state` and `studio_name`. For the four waiting states it carries `state`, `studio_name`, `sent_at` of nothing, and the masked email — never the budget.
- `can_revise` and `can_finalize` are true only in `sent`.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Write `clientview.py`**

One function, one dict literal per state, built from named fields. No `model_dump`, no `dict(bundle)`, no comprehension over an upstream object — those are the shapes that leak when somebody adds a field upstream.

The narrative is the client-facing markdown that already exists on the bundle; the requirements sheet is never referenced here.

- [ ] **Step 4: Run the test**

- [ ] **Step 5: Commit**

---

## Task 3: The gate opens one door

**Files:**
- Modify: `backend/app/auth.py`, `backend/app/main.py`
- Test: `backend/scripts/check_client_api.py`

- [ ] **Step 1: Write the failing test**

`check_client_api.py`, with **real auth on** (a fixed `SUPABASE_JWT_SECRET`, as `check_intakes_api.py` does — an anonymous surface tested with auth disabled proves nothing). Assert:

- `GET /api/client/<token>` answers 200 **with no Authorization header**.
- Every existing studio route still answers 401 without a token — assert at least `/api/intakes`, `/api/proposals`, `/api/settings`, `/api/team`.
- `GET /api/client/<unknown>` and `GET /api/client/<closed>` return the **same status and the same body**. Compare both.
- An expired token behaves identically.
- `X-Workspace` naming a different workspace is ignored: create intakes in two workspaces, resolve one's token while sending the other's header, assert you get the first.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Add the clause**

In `_gate`'s `open_path` expression, beside the existing `GET /api/invites/` clause:

```python
        or path.startswith("/api/client/")
```

It is method-aware in the sense that it is the *only* prefix admitting a POST without a token — say so in a comment, and say what makes that safe: the token, the state check, and the rate limit.

- [ ] **Step 4: Add `GET /api/client/{token}`**

Resolve → borrow → build via `clientview.of` → give back in `finally`. Unknown, expired and closed all return the same 404 body.

- [ ] **Step 5: Run the test**

- [ ] **Step 6: Commit**

---

## Task 4: The client writes

**Files:**
- Modify: `backend/app/main.py`
- Test: extend `backend/scripts/check_client_api.py`

**Interfaces:**
- Produces: `POST /api/client/{token}/submit`, `/revise`, `/finalize`

- [ ] **Step 1: Write the failing test**

Assert:

- Submit from `issued` → 200, intake becomes `submitted`, and the four fields are stored verbatim.
- **Submit twice → the second is refused**, and the intake is unchanged. This is the abuse control; test it explicitly.
- Submit from any other state → refused.
- Revise and finalize from `sent` → 200 and the right state. From any other state → refused.
- Finalize writes a notification addressed to the intake's `created_by` **and** to the admins — read it back through `inbox.listing()` with a seeded roster.
- Over-length scope and budget are refused with the same shape of error the studio's route uses.
- The rate limit: the 21st submit attempt from one IP within a minute is refused with 429, and a different IP is unaffected.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Implement**

Rate limiting is a small in-memory `deque` per IP, in `main.py` beside the routes. Say in a comment that it is per-process and resets on restart, and that it is a courtesy control rather than a defence against a determined attacker — an honest comment beats a false sense of protection.

Finalize's notification reuses `inbox.notify` with an explicit recipient list, exactly as Stage 1's failure note does.

- [ ] **Step 4: Run the test**

- [ ] **Step 5: Commit**

---

## Task 5: The client reads the quotation

**Files:**
- Modify: `backend/app/main.py`
- Test: extend `backend/scripts/check_client_api.py`

- [ ] **Step 1: Write the failing test**

**This is the most important test in the plan.** Assert:

- `GET /api/client/{token}/quotation.html` for a `sent` intake returns the **proposal** document.
- The returned bytes **do not contain** the requirements sheet's title. Build a bundle whose requirements sheet is distinctive, and assert its heading is absent from the client's HTML.
- There is **no** path, query or body parameter by which a client can ask for `requirements` — assert that adding `?kind=requirements`, `/quotation.requirements.html` and a JSON body with `kind` all either 404 or return the proposal unchanged.
- The route refuses every state except `sent`, `revision_requested`, `finalized` — a client must not fetch a quotation before it was sent to them.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Implement**

`kind` is a local constant inside the handler. The word `requirements` must not appear in the function.

- [ ] **Step 4: Run the test**

- [ ] **Step 5: Commit**

---

## Task 6: The studio mints, sends and relinks

**Files:**
- Modify: `backend/app/main.py`, `backend/app/prompts.py`
- Test: extend `backend/scripts/check_intakes_api.py`

- [ ] **Step 1: Write the failing test**

- `POST /api/intakes` now takes a **preset** (kind, currency, market region, tax basis, payment terms, tiers) and no client words, returns the intake with a token, and the intake starts `issued`.
- `POST /api/intakes/{id}/send` requires an explicit `bundle_id`, refuses a bundle that is not in `bundle_ids`, moves `quoted → sent`, and records **both `sent_bundle_id` and `sent_at`**. Task 2 added `Intake.sent_at` and nothing writes it — until this route does, every client sees a blank sent date and no test fails.
- `POST /api/intakes/{id}/relink` issues a new token and kills the old.
- All three are admin-only; a member gets 403 under real auth.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Implement, including the prompt framing**

In `prompts.py`, the client-authored scope is framed the way `attachments.describe_for_prompt` frames a document: material to quote from, never an instruction, whatever it appears to say. Read that function and match its wording rather than inventing a second phrasing.

- [ ] **Step 4: Run the test**

- [ ] **Step 5: Commit**

---

## Task 7: The client's shell

**Files:**
- Create: `frontend/src/lib/clientApi.ts`, `frontend/src/components/client/ClientShell.tsx`
- Modify: `frontend/src/main.tsx`, `frontend/src/types.ts`

- [ ] **Step 1: Resolve the shell before `AuthGate`**

In `main.tsx`, on a hash matching `#/c/<token>`, render `<ClientShell token={...} />` **instead of** `<AuthGate><App/></AuthGate>`. Not an allowlist inside `AuthGate`: `App.tsx` fires `listWorkspaces()` and `fetchSettings()` unconditionally on mount and both 401 anonymously.

- [ ] **Step 2: Write `clientApi.ts`**

A bare `fetch`. It must import neither `currentWorkspace` nor `accessToken`. Add a comment saying that is the point, and that importing `lib/api` here would attach a session to an anonymous request.

- [ ] **Step 3: Add the types** to `types.ts`, mirroring `clientview.of`'s output exactly.

- [ ] **Step 4: Typecheck and build**

```
cd frontend
npm run typecheck
npx vite build
```

- [ ] **Step 5: Commit**

---

## Task 8: The four faces

**Files:**
- Create: `ClientForm.tsx`, `ClientWaiting.tsx`, `ClientQuotation.tsx`, `ClientClosed.tsx`

The studio's name is the headline on every one of them; PRISM is fine print. This is the client's first impression of a studio they are considering hiring.

- [ ] **Step 1: The form** — Email, Contact no. (optional), Scope, Budget. The budget's hint says their figure guides the quotation and does not set the price, in the client's language rather than the studio's.

- [ ] **Step 2: The waiting face** — one face for `submitted`, `preparing`, `quoted` and `quote_failed`. It must never regress, never leak that a model is running or that a pass errored, and never invent an ETA. Copy: *"<Studio> has your scope. Nobody has replied yet."* plus sent date, masked email, and scope length. **Never the budget.**

- [ ] **Step 3: The quotation** — reference, total, validity, payment schedule, the narrative. Two buttons: **Ask for a change** (a textarea, then the honest round counter read off `revisions`) and **Finalize**. Finalize must say plainly what it is not: not a signature, no payment due.

- [ ] **Step 4: Closed** — the identical page for closed, expired, wrong and never-existed. One sentence, no detail, no hint that a link ever existed.

- [ ] **Step 5: Typecheck, build, and exercise it**

Start the API and a dev server, create an intake, copy the link, open it in a private window, submit, and walk the whole path to Finalize. Quote what you observed. **Do not run against `backend/generated/` — use a scratch `GENERATED_DIR`.**

- [ ] **Step 6: Commit**

---

## Task 9 — amended before dispatch

The original Task 9 was one task of four steps. Two of its instructions were wrong and one whole requirement was missing; it is replaced by **9a** and **9b** below. What changed, and why, so the amendment is auditable rather than silent:

**"`issued` shows Copy link" was impossible as written.** `Intake.token` carries `exclude=True`, and `list_intakes` / `read_intake` both serialise a bare `Intake` — the token never crosses the wire on a read. Only `create_intake` and `relink_intake` return `IntakeIssued`, and `IntakeIssued`'s own docstring records the ruling *against* adding a `GET /api/intakes/{id}/link`. So the link is shown **once**, at Generate, and thereafter is recoverable only by minting a new one. An implementer handed "Copy link" in the queue would either invent the rejected route or copy an empty string.

**Nothing reads `preset` back.** `IntakeScreen` writes `preset: {}`; `api.ts` and `types.ts` only type the field; `App.tsx`'s prefill passes `scope`, `budget` and `clientName` and nothing else. `IntakeRequest`'s docstring defers the read-back to "a later task", and this is the last task. Without it the configuration the studio sets at Generate is decorative. It is 9a's job, and the spec already anticipated the shape: *"`BriefForm.tsx` gains one optional prefill prop beside the defaults it already takes."*

**Step 3's fix was aimed at the wrong field for half the states it covers.** See 9b Step 3.

---

## Task 9a: Configure, generate, and land it on the pad

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/components/IntakeScreen.tsx`, `frontend/src/App.tsx`, `frontend/src/components/BriefForm.tsx`

One walkable story: configure a request, generate its link, copy the link, press Price this, and find the configuration already on the pad.

- [ ] **Step 1: The wire catches up with the record.** `types.ts`'s `Intake` is missing four fields the server has been sending since Task 1: `token_expires_at`, `revisions`, `sent_bundle_id`, `sent_at`. `revisions` is `List[dict]` server-side with entries of exactly `{"asked", "at"}` — give it a named type, not `unknown[]`, and coerce at the boundary. Add `IntakeIssued = Intake & { link: string }`. `token` is **not** on either type: it is excluded server-side on purpose and must not acquire a name here.

- [ ] **Step 2: `api.ts` gains the two calls and loses a wrong return type.** `createIntake` returns `IntakeIssued`, not `Intake` — today the link it already receives is unreachable from the UI. Its body drops the four client fields and sends `{ preset }` alone, matching `IntakeRequest`. Add `sendIntake(id, bundleId)` → `Intake` and `relinkIntake(id)` → `IntakeIssued`. Both are admin-only server-side; follow `closeIntake`'s existing shape for the empty-id guard and error kinds.

- [ ] **Step 3: `IntakeScreen` becomes config + link.** The four typed fields go — the client's words are no longer the studio's to type. In their place: the PAD preset (kind, currency, market region, tax basis, payment terms, tiers, per the spec) and a **Generate link** button.

  **On success this screen must not navigate away.** The current `.then()` sets `window.location.hash = '#/intakes'`, which would throw away the only copy of the link that will ever exist. Success stays here and renders the link with a copy control and one sentence saying plainly that it is shown once and reissuing replaces it.

  Rewrite the file's docstring: delete the claim that this screen writes down what the client said.

- [ ] **Step 4: The preset reaches the pad.** `App.tsx` already fetches the intake for `#/pad/<id>` and hands `BriefForm` a `prefill`. Extend `BriefPrefill` with the preset fields and seed them.

  Two traps. **`preset` is `Record<string, unknown>` off the wire**, and `kind`, `taxMode`, `cadence` and `pricingBasis` are typed unions — seed them by membership check against the known set with fallback to the current default, never by cast. This is the same "coerced rather than trusted" discipline the existing `String(intake.scope ?? '')` prefill documents, extended to unions. **The `seeded` latch fires once**: every preset-derived field must be set in that one pass, or the form is left half-configured with no second chance. The `key={intake.id}` remount guarantees `prefill` is present at mount, so a single atomic seed is achievable.

- [ ] **Step 5: Typecheck, build, and walk it.** Configure a request, generate, copy the link, press Price this from the queue, and confirm the configuration is on the pad. Quote what you observed. **Do not run against `backend/generated/` — use a scratch `GENERATED_DIR`.**

- [ ] **Step 6: Commit**

---

## Task 9b: The queue catches up

**Files:**
- Modify: `frontend/src/components/IntakeListScreen.tsx`

- [ ] **Step 1: The new states get sections and chips.** `STATE_LABEL` already carries all ten. `buildSections` places rows **by elimination** specifically so a state this screen has never heard of still lands somewhere — new sections go *before* the `rest` catch-all, and that invariant is documented in the file. Keep it.

- [ ] **Step 2: The actions each state actually supports.** `quoted` gains **Send to client**, which must name which bundle it is sending — `send_intake` requires an explicit `bundle_id` and refuses one that is not this intake's own. A `revision_requested` row shows the client's words, read off the last `revisions` entry. **Reissue link** replaces the impossible "Copy link": it is destructive — it kills a link the client may be holding right now, and on `sent` / `revision_requested` / `finalized` it kills their access to their own quotation — so it takes the same inline-confirm weight `Close` already has in `IntakeRow`, and you decide which states offer it at all rather than offering it on everything non-closed. The reissued link is shown on the row, once, the same way `IntakeScreen` shows the first one.

- [ ] **Step 3: Fix the parked Stage 1 finding — and aim it at the right field.** `firstBundleId` computes only for `quoted`, so a failed *second* pass leaves a row showing an error and a retry with no hint that a good quotation already exists. Extend it, but **not to `bundle_ids[0]` everywhere**: for `sent`, `revision_requested` and `finalized` the correct target is `sent_bundle_id`, because `bundle_ids[0]` on a re-quoted intake can be a candidate the client never saw. Opening "View quotation" on a sent row and getting a different document than the client is looking at is the same class of bug the parked finding names.

- [ ] **Step 4: Typecheck, build, and walk the queue** through `issued → sent → revision_requested → finalized`. Quote what you observed.

- [ ] **Step 5: Commit**

---

## Self-review

**Spec coverage.** Stage 2's spec paragraph names: the token and its index (Task 1), the gate clause (Task 3), the client shell (Task 7), the form, the waiting face, the quotation view, Revise and Finalize (Tasks 4, 5, 8), the finalize notification (Task 4), per-IP rate limiting (Task 4), Relink and Close (Tasks 1, 6, 9). The lifecycle table's `sent`, `revision_requested` and `finalized` rows are Tasks 4 and 8. `proposal_sent` is Stage 3 and is deliberately unreachable.

**Carried from Stage 1's ledger:** the parked re-Generate finding is Task 9 Step 3. The duplicate admin notification, `client_phone` being write-only, and the client's email landing in the quotation's client-name field are **not** in this plan — the last one now matters more, because the client will read that document. Flag it to the studio rather than fixing it silently.

**Type consistency.** `clientview.of`'s output (Task 2) is mirrored by `types.ts` (Task 7) and consumed by the four faces (Task 8). `ADVANCE_FIELDS` gains `revisions` and `sent_bundle_id` in Task 1, which Tasks 4 and 6 write.

**The three tests that carry this plan**, and each is a whole task's justification: Task 3's "every studio route still 401s", Task 5's "the requirements sheet is unreachable by any parameter", and Task 2's "the serialised client view contains none of these substrings". If those three pass and nothing else did, the security posture would still hold.
