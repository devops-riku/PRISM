# Send to client, as an email

**Date:** 2026-08-07
**Status:** approved, not yet implemented

## What changes

Pressing **Send to client** on a `quoted` row opens a modal shaped like a
compose window - **To**, **Subject**, **Message** - prefilled with a draft and
editable. From there the studio either sends it through Resend or copies the
text and sends it themselves.

Today that button opens an inline confirm panel and flips the intake to `sent`.
No email is sent to a client anywhere in PRISM; the studio delivers the link by
hand through *Copy link*.

## The rule this overturns, deliberately

`mailer.py` opens with:

> Nothing else in PRISM sends mail, and nothing else should: an app that
> quietly emails people is one nobody can predict.

That stance is now narrower rather than gone, and the distinction is the whole
of why this is acceptable: **PRISM still sends nothing on its own.** An email
leaves only when a studio has opened a compose window, read the exact words,
and pressed Send. Nothing is emailed by a background job, a state transition,
or a scheduled anything. `mailer.py`'s docstring is updated to say that, so the
next reader is not left with a rule the code no longer follows.

## Behaviour

### The draft

Composed client-side so what is on screen is exactly what is sent:

- **To** - `intake.client_email`, fixed and not editable. The intake's address
  is the record; retyping it here would mean the queue and the mail disagree.
- **Subject** - `Quotation for {project}` where a project name is known,
  otherwise `Your quotation from {studio_name}`.
- **Message** - a greeting, one line saying the quotation is ready, the client
  link on its own line, and a sign-off carrying `studio_name`.

Held in component state. Closing the modal discards it. Nothing is persisted,
so there is no new field on the intake and no migration.

### The link

Fetched on open from `GET /api/intakes/{id}/link`, the existing admin-only,
single-purpose call that *Copy link* already makes. The queue keeps carrying no
tokens (`Intake.token` stays `exclude=True`), so a screenshot, an export, or a
member reading the list still discloses nothing.

### Sending

`POST /api/intakes/{id}/send` gains `subject`, `message` and `notify`:

1. Validate the bundle - unchanged, `_quoted_bundle` still the contract.
2. If `notify` is true **and** `mailer.configured()`: send the email. A
   `MailError` answers **502** and the intake **stays `quoted`**.
3. Advance to `sent`.

The ordering is the point: a row reading `sent` always means a client was
actually emailed, or that the studio explicitly chose to send it themselves.

### When Resend is not configured

`send_intake` behaves exactly as it does today - it advances, and emails
nothing. This is what keeps every existing check script passing unchanged, and
it means an install with no mail key is not suddenly unable to move its queue.
The modal reads that state from `mail_configured` and offers *Copy, and mark as
sent* in place of *Send email*.

`notify: false` gives the same escape hatch to an install that **does** have
mail configured but wants to send this one itself.

### Errors

- No client address on the intake -> 400, refused before Resend is called.
- Resend refuses or cannot be reached -> 502, intake untouched, modal shows the
  message and Copy is still there.
- Not an admin -> 403, as now.

## Interfaces

| Unit | Does | Depends on |
|---|---|---|
| `mailer.send_quotation` | One quotation email. Raises `MailError`. | `config.RESEND_*` |
| `mailer._quotation_body` | Escapes the studio's plain text and wraps it in the document-palette shell `_body` already uses. | nothing |
| `POST /api/intakes/{id}/send` | Validate, optionally email, then advance. | `mailer`, `intakes` |
| `GET /api/health` | Gains `mail_configured`, beside the existing `key_configured`. | `mailer.configured` |
| `SendToClientDialog` | The compose window. Owns draft state, no persistence. | `readIntakeLink`, `sendIntake` |

## Testing

`backend/scripts/check_send_email.py`, offline, in the existing style:

- mail stubbed to succeed -> `sent`, right bundle, right `sent_at`
- mail stubbed to raise -> still `quoted`, 502, nothing written
- mail unconfigured -> advances, emails nothing (today's behaviour)
- `notify: false` with mail configured -> advances, emails nothing
- no client address -> 400 before any send is attempted
- a member -> 403

The stub targets `app.api.intakes`, not `app.mailer`: the route does
`from app import mailer` (a module reference, so patching the module attribute
works) - but any function pulled in by `from ... import name` would need
patching on `app.api.intakes` itself. This is the failure mode that made two
assertions in `check_client_api.py` pass for the wrong reason during the DDD
split.

## Out of scope

- No PDF attachment. The client reads the quotation in PRISM, which is what the
  accept/revise flow needs; an attached PDF is a frozen copy that goes stale the
  moment the quotation is revised.
- No saved drafts. Prefilled only.
- No send from the client's own domain, no reply-to threading, no open tracking.

## Operational note

Resend click-tracking rewrites every link to a `resend-clicks` domain. On a
quotation that hides the studio's own domain and reads as phishing to the
client. It should be off for this send - it is the same feature that consumed a
one-time sign-in token earlier in this project's history.
