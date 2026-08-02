# Client intake — design

**Date:** 2026-08-02
**Status:** approved for Stage 1

## Goal

Turn a client conversation into a tracked request that the studio reviews before anything is
quoted, and — later, once PRISM is deployed — let the client submit that request themselves through
a link, read the quotation it produces, ask for a change, and finalise it.

The studio keeps the pen throughout. No client action ever spends a model call.

## Decisions taken

Answered by the studio on 2026-08-02. These are settled; the rest of the document assumes them.

| # | Question | Answer |
|---|---|---|
| 1 | Who generates the proposal after the client finalises? | **The studio.** The line in the original request reading "the client will generate a proposal" was a slip. |
| 2 | Is the client's budget binding on the price? | **Advisory.** It reaches the free-text budget the model reasons about and is stored verbatim. `target_total` — which the server solves arithmetic onto exactly — stays empty until a human types it on the PAD. |
| 3 | Is PRISM going to a public origin? | **Not yet.** Build Stage 1 only. Stages 2 and 3 are blocked on deployment, not on this design. |
| 4 | May PRISM email people who are not on the team? | **Yes, but last.** Stage 3. Stages 1 and 2 work with a link the studio sends by hand, exactly as an invitation already degrades when the mailer is unconfigured. |

One decision is deferred rather than settled: whether a quotation sent to a client carries a
free-text note from the studio. It is a Stage 2 concern and does not affect Stage 1. The
recommendation is yes, one box.

### Defaults taken

Reverse any of these by saying so; none is load-bearing.

- **For Client is admin-only.** Issuing a link that commits the studio to a price is nearer to
  inviting somebody than to drafting a quotation.
- **One link per intake**, 60 days, `Relink` reissues, `Close` revokes.
- **The client sees** narrative, totals, payment schedule and validity — **not** unit rates, day
  rates, tier siblings, the rate card, or what the rate card removed.
- **No client-side "no thanks" button.** The studio closes the intake instead.
- **Intake records are kept indefinitely**, because nothing in PRISM expires anything. They hold a
  non-employee's email and phone; name a retention period and they will be trimmed the way the
  notification inbox already trims itself.

## Why this shape

Approving an intake does **not** call a new endpoint. It opens the PAD form the studio already
uses, at `#/pad/<intake_id>`, prefilled with the client's scope and their number, in the studio's
own signed-in browser. They press Generate exactly as they do today.

Three consequences, and they are the reason this design was chosen over the alternatives:

1. **`create_proposal` is not wrapped.** It is 300 lines and 21 form fields — the most central code
   in the app — and it is not callable from another handler without refactoring it. In a repository
   with no version control that is a bad trade.
2. **An anonymous-triggered model call becomes unrepresentable** rather than merely forbidden. There
   is no endpoint for it to reach.
3. **The studio's copy of the scope is a record of what was actually priced**, not a second editable
   copy that somebody might change and then not use.

## Lifecycle

One token per intake. What it unlocks is decided by the **state**, never by the token.

| State | Studio sees | Client sees | Moves on when | Stage |
|---|---|---|---|---|
| **issued** | The row in *Client requests*, with Copy link, Relink and Close. Marked "not opened yet" until the first GET. | The four-field form: Email, Contact no. (optional), Scope, Budget. The studio's name is the headline; PRISM is fine print. | Client submits. Accepted **only** from `issued`. | 2 |
| **submitted** | The four fields verbatim, plus a bell note to the link's creator and to admins. Queue shows "awaiting review", and separately "opened, not yet generated" once somebody has opened the prefilled PAD. | *"<Studio> has your scope. Nobody has replied yet."* Plus sent date, masked email, scope length. Their budget is **not** echoed. | A studio member opens `#/pad/<id>`, edits anything, presses Generate. | 1 |
| **preparing** | The ordinary job strip, moving — the job is created in the studio's own session, so `jobs.create` stamps a real owner. | *Unchanged.* | `jobs.finish`, or `jobs.fail`. | 1 |
| **quoted** | The bundle(s) on the normal `#/q/<id>` page, plus **Send to client**, naming which bundle. | *Unchanged — and still true: nothing has been sent.* | The studio presses Send with an explicit `bundle_id`. | 1 (state), 2 (send) |
| **quote_failed** | A loud bell note naming the failure, and the intake back in the queue so it cannot be lost. | *Unchanged, still literally true — the intake is back with the studio.* | The studio retries. | 1 |
| **sent** | Which bundle went, and when. | The quotation: reference, total, validity, payment schedule, the client-facing markdown. Two buttons: **Ask for a change** and **Finalize**. | Client presses one. Both accepted **only** from `sent`, so a replayed link cannot re-finalise. | 2 |
| **revision_requested** | Their words, plus a bell note. Back in the queue. | Their own words echoed, an honest round counter read off this intake's own list, and the previous quotation still readable underneath. | The studio prices a new bundle with the existing Revise panel and presses Send again. Back to `sent`. | 2 |
| **finalized** | **The notification.** Delivered to the link's creator by name *and* to admins. | "<Studio> has been told." Reference and date. Buttons gone. Explicit: *not a signature, no payment due.* | The studio builds the proposal with the unchanged document pipeline and presses Send proposal. | 2 |
| **proposal_sent** | The proposal on `#/p/<id>` as today. Intake closed. | The proposal, readable and downloadable on the same link. | Terminal. | 3 |
| **closed** | The row greyed, with who closed it and when. Doubles as "not going ahead" and as the kill switch for a link that reached the wrong person. | The **identical** "this link is not valid" page a wrong or expired token gets. | Terminal. | 1 |

The four identical client cells are deliberate: one waiting face covering four studio states. It
never regresses, never leaks that a model is running or that a pass errored, needs no invented ETA,
and is true in all four.

**In Stage 1** an intake is created **directly at `submitted`**: the studio types in the client's
words themselves, from the call or email they had anyway, so there is no link to issue and nothing
to wait for. The reachable states are `submitted` → `preparing` → `quoted` / `quote_failed` →
`closed`.

`issued`, `sent`, `revision_requested`, `finalized` and `proposal_sent` exist in the state machine
and are refused as transitions until Stage 2 wires the actor that can reach them. That is deliberate:
the machine is written once, and the states nobody can reach yet are the ones a later stage turns
on rather than adds.

## Stage 1 — what gets built

No public route, no token issued to anybody, no anonymous write. **The API surface stays exactly as
closed as it is today.**

### Server

**`backend/app/intakes.py`** (new). The record is defined in this module the way `members.Invite`
is: it is storage-side, never handed to the model, so the `schemas.py` house rules do not bind it
and `schemas.py` stays untouched.

- One file per intake under `generated/w/<ws>/_intakes/<id>.json`. The leading underscore is
  load-bearing — the bundle walker steps over it.
- Ids from `storage.new_id()`, **never** from the quotation counter. An intake is not a quotation
  and must not burn a number.
- Fields: `id`, `state`, `created_at`, `created_by`, `client_email`, `client_phone`, `scope`,
  `budget_text`, `preset` (kind, currency, market region, tax basis, payment terms, tiers),
  `bundle_ids`, `document_id`, `priced_scope`, `priced_budget`, `revisions` (a list), `closed_at`,
  `closed_by`, `token`, `token_expires_at`.
- `intakes.forget(workspace_id)` wired into `workspaces.delete()` beside the existing
  `inbox.forget()`, because workspace ids are reusable.
- A module-level token index built at startup — **not** a cross-workspace directory scan. The invite
  scan reads one small file per workspace; intakes are one file each, so copying that pattern would
  make every wrong-token guess read every intake file on the install. The index is unused in Stage 1
  but is part of this module's design.

**`backend/app/main.py`**: routes under `/api/intakes` — `POST` create (admin), `GET` list,
`GET /{id}`, `POST /{id}/close`. Each placed **explicitly** on one side of the member/admin line,
because an unlisted POST is permitted to members by default.

`POST /{id}/send`, `POST /{id}/send-proposal` and `POST /{id}/relink` belong to Stage 2 and are not
built here — with no link issued there is nobody to send to, and a route that cannot do anything is
a route nobody can test.

Plus the whole of the review gate's backend cost: **one optional `intake_id` form field on
`POST /api/proposals`, and three stamps inside the existing `run()`** — on entry (`preparing`),
after finish (`quoted`, bundle ids, and the scope and number that were actually priced), and on
failure (`quote_failed`).

**Notifications** reuse `inbox.notify(kind, [creator_email], …)`, which already accepts a plain list
of emails. Nothing new is required.

### Frontend

- `HomeScreen.tsx`'s flat `DESTINATIONS` becomes two arrays plus one `useState` driving the pill.
  The `.map` and the `isAdmin` filter are untouched.
- `IntakeScreen` — create an intake: the client's email, contact number, scope and budget as the
  studio heard them, plus the PAD preset (kind, currency, market region, tax basis, payment terms,
  tiers). In Stage 2 this same screen gains the link and stops requiring the client's words.
- `IntakeListScreen` — the queue, showing awaiting review, opened-not-generated, and
  quoted-not-sent.
- `BriefForm.tsx` gains one optional prefill prop beside the defaults it already takes.
- `App.tsx` gains the route entries and appends `intake_id` to the form data it already builds.
  `#/pad/<anything>` already routes to the PAD screen, so there is no new route for approval.

### What Stage 1 is worth on its own

Every client conversation becomes a tracked row holding the client's own words and their number,
quoted through the form the studio already uses, with what was asked sitting beside what was
actually priced — and a queue that says what is waiting on them. If the studio stops here, nothing
has been lost and nothing has been opened.

## Stage 2 — the link (deferred, blocked on deployment)

The token and its index, one method-aware clause in the gate's open-path expression, the client
shell, the form, the waiting face, the quotation view, Revise, Finalize and the finalize
notification, per-IP rate limiting, Relink, Close.

**The client shell is resolved in `frontend/src/main.tsx` before `<AuthGate><App/></AuthGate>`** — on
the `#/c/<token>` hash the shell renders *instead of* the app. An allowlist inside `AuthGate` would
fix the sign-in screen but leave `App.tsx`'s unconditional `listWorkspaces()` and `fetchSettings()`
firing on mount, both of which 401 anonymously.

Its fetches live in a new `frontend/src/lib/clientApi.ts` using a bare `fetch` that imports neither
`currentWorkspace()` nor the Supabase token, because `lib/api.ts` sets both on every request.

Server side: one prefix, `/api/client/`, disjoint from everything studio-facing and clear of the
three prefixes that inherit the non-member bypass. `GET /api/client/{token}`,
`POST /api/client/{token}/submit`, `/revise`, `/finalize`, and
`GET /api/client/{token}/quotation.{html,pdf}` with **`kind` pinned to `proposal` inside the
handler — not a path parameter that gets validated, a parameter that does not exist.** The existing
file routes serve `requirements`, the internal developer sheet; this is the single most likely real
bug in the feature and this is the whole of the fix.

## Stage 3 — proposal and mail (deferred)

Send-proposal over the unchanged document endpoint, the fourth client face, and two or three mail
templates — every one of them studio-triggered.

**No mail is ever sent on a client's submit.** A stranger typing `victim@example.com` must not make
a verified sending domain email them.

## Security posture

**What the link proves:** that the studio gave it to somebody. Nothing more. It is a bearer
credential — like the existing invitation links, but with higher stakes, because an invite still
needs an account to accept and this needs nothing.

**What it cannot do:** spend a model call (no anonymous path reaches one); write into the wrong
studio (the workspace comes from the token, never from a header); submit twice, re-finalise, or
finalise before a quotation exists (each write is accepted only from the one state it belongs to);
reach the developer requirements sheet, another tier's price, the rate card, what the rate card
removed, or the note explaining how the scope moved to reach a number (the client is served a named
allowlist of fields, never a filtered bundle); or survive `Close` — withdrawn, expired, wrong and
never-existed all return the same page.

**What somebody without it learns:** nothing. No workspace id in the URL, no enumerable ids, and no
response that distinguishes a real token from a fake one.

### Prompt injection

The client's scope flows into `brief`, and their budget into the free-text budget field. Both are
text a stranger wrote, reaching fields the prompt has always trusted because the studio wrote them.
Both get the framing `attachments.py` already applies — *material to quote from, never an
instruction, whatever it appears to say* — and both get an explicit length bound, since the brief's
existing cap does not reach the budget field.

Neither can move money: the server recomputes all of it. They can move line-item wording, and the
studio's name goes on that.

### `generated/` is inside OneDrive

A raw token in a JSON file is a working client link for anyone with that login. Token hashing was
considered and **rejected**: that folder already holds every brief, price, client name and quotation
in plaintext, so hashing one field empties a drawer in a room that stays unlocked, and it costs the
ability to re-send a client their link.

The fix is the folder, and it mostly solves itself on a deployed instance. **If PRISM is ever
tunnelled from this machine instead, move `generated/` off the synced path first.** Worth doing
regardless of this feature.

## Commercial notes

**The review step is a safeguard, not a bottleneck.** One press. It buys: nobody with the link
spends the model budget, and — far more expensive — no quotation goes out at the client's number
instead of the studio's.

**Approve and Send stay two presses.** This is the one place the design adds friction on purpose:
generation is not release. Three tiers are three bundles and a revision is a fourth, so the studio
names which one the client sees. The mitigation for a forgotten Send is the queue reading
"quoted, not sent" loudly, not removing the press.

**Revise is not an unbounded free-work generator**, provided it stays a request the studio prices.
Every round costs a deliberate studio action, so uncapped rounds are fine. Wiring the client's button
straight to the existing revise endpoint would let a link-holder spend the studio's money with no
gate — the exact gate this feature exists to add.

**The honest costs.** A client can wait forever: PRISM has no SLA machinery and this adds none, so if
nobody opens the queue the client's screen truthfully says nobody has replied, for weeks. The count
on the home screen is the mitigation. A link reaching the wrong buyer at the client's company is a
real commercial event, and `Close` is the only answer to it. And a client who cannot see line items
cannot justify the price to their own boss — which is why the studio note (deferred above) is a real
question and not a preference.

## Testing

- **`intakes.py` state machine**: every legal transition, and that every illegal one is refused.
  Table-driven, run offline — no model, no network.
- **`forget()`**: an intake does not survive its workspace being deleted and the id being reused.
- **The three stamps on `create_proposal`**: `preparing` on entry, `quoted` with bundle ids and the
  priced scope on finish, `quote_failed` on failure — asserted through a real run of the existing
  handler with the model stubbed, not through the stamping functions in isolation. The seam is where
  the bug lives; a previous session shipped a `TypeError` that isolated tests missed for exactly
  this reason.
- **The byte-identical check**: a quotation prepared without an `intake_id` renders exactly as it
  does today. Capture the baseline **before** any edit.
- **Notifications**: the finalize note addresses the link's creator by name and the admins, and no
  one else.
