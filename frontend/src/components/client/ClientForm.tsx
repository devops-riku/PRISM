import { useState } from 'react'
import type { FormEvent } from 'react'
import { FieldLabel } from '../FieldRow'
import ErrorNotice from '../ErrorNotice'
import { ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL, WELL_TEXTAREA } from '../tokens'
import { ClientApiError, submitClientIntake } from '../../lib/clientApi'
import type { ClientSubmitBody } from '../../lib/clientApi'
import type { ClientIntakeView, ClientIssuedView } from '../../types'

/**
 * Face 1: `issued`. A stranger's first look at this studio - four fields,
 * nothing else, the studio's own name at the top of the page rather than
 * PRISM's.
 *
 * Every field here is length-bounded and stored verbatim by `/submit`
 * (`_normalise_client_email`/`_normalise_client_phone`/`_normalise_scope`/
 * `_normalise_budget_text` in `main.py`) - none of it is validated for shape
 * server-side beyond a length ceiling, so the `required`/`type="email"`
 * attributes here are for this screen's own sake, not a contract the API
 * enforces.
 */

/** Turn a failed write into words safe to show a stranger, and whether the
 * link itself is gone rather than merely this one call. Mirrors the rule
 * `ClientShell`'s own read path settled on (Task 7, `progress.md`): the
 * token's own 404 and the rate limiter's 429 each get an honest sentence of
 * their own, and every other HTTP failure gets one fixed, client-authored
 * line rather than whatever `detail` the server - or a proxy in front of it
 * - happened to send. Only `ClientApiError`'s own network/timeout/parse
 * messages are safe to print verbatim, because `clientApi.ts` guarantees
 * those are never server text. */
function describeWriteFailure(failure: unknown): { headline: string; next?: string; gone: boolean } {
  if (failure instanceof ClientApiError) {
    if (failure.isGone) return { headline: '', gone: true }
    if (failure.isRateLimited) {
      return {
        headline: 'That was sent too many times in a row.',
        next: 'The link still works - wait a minute and try again.',
        gone: false,
      }
    }
    if (failure.kind === 'http') {
      return { headline: 'That could not be sent just now.', next: 'Try again in a moment.', gone: false }
    }
    // network / timeout / parse / validation - `clientApi.ts` guarantees these
    // are its own generic sentences, never server text, so printing the
    // message itself is safe here in a way it is not for `kind === 'http'`.
    return { headline: failure.message || 'That could not be sent.', gone: false }
  }
  return { headline: 'That could not be sent.', gone: false }
}

export default function ClientForm({
  view,
  token,
  onUpdate,
  onGone,
}: {
  view: ClientIssuedView
  token: string
  onUpdate: (next: ClientIntakeView) => void
  onGone: () => void
}) {
  const studio = view.studio_name || 'This studio'

  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [scope, setScope] = useState('')
  const [budget, setBudget] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<{ headline: string; next?: string } | null>(null)

  const trimmedScope = scope.trim()
  const ready = Boolean(email.trim()) && Boolean(trimmedScope) && Boolean(budget.trim())

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!ready || pending) return

    setPending(true)
    setError(null)

    const body: ClientSubmitBody = {
      client_email: email.trim(),
      client_phone: phone.trim(),
      scope: trimmedScope,
      budget_text: budget.trim(),
    }

    submitClientIntake(token, body)
      .then((next) => onUpdate(next))
      .catch((failure: unknown) => {
        const described = describeWriteFailure(failure)
        if (described.gone) {
          onGone()
          return
        }
        setError(described)
      })
      .finally(() => setPending(false))
  }

  return (
    <div className="rounded-[18px] border border-rule bg-paper p-6 shadow-raised sm:p-8">
      <p className={MONO_LABEL}>{studio}</p>
      <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>
        Tell {studio} about the work
      </h1>
      <p className="mt-2 max-w-[46ch] font-body text-[14.5px] leading-[1.6] text-void">
        Four things, and {studio} can start putting a quotation together.
      </p>

      <form onSubmit={handleSubmit} noValidate className="mt-6 flex flex-col gap-5">
        <div>
          <FieldLabel htmlFor="client-email">Email</FieldLabel>
          <input
            id="client-email"
            name="email"
            type="email"
            required
            autoComplete="email"
            maxLength={254}
            value={email}
            disabled={pending}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
            className={WELL}
          />
          <p className="mt-1.5 font-body text-[13px] leading-[1.5] text-void">
            Where {studio} sends the quotation.
          </p>
        </div>

        <div>
          <FieldLabel htmlFor="client-phone">
            Contact no. <span className="normal-case tracking-normal text-faint">(optional)</span>
          </FieldLabel>
          <input
            id="client-phone"
            name="phone"
            type="tel"
            autoComplete="tel"
            maxLength={254}
            value={phone}
            disabled={pending}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="Leave it if a quick call would help"
            className={WELL}
          />
        </div>

        <div>
          <FieldLabel htmlFor="client-scope">Scope</FieldLabel>
          <textarea
            id="client-scope"
            name="scope"
            required
            maxLength={20000}
            value={scope}
            disabled={pending}
            onChange={(event) => setScope(event.target.value)}
            placeholder="What you need built, who it's for, and anything that matters about how it should work."
            className={`${WELL_TEXTAREA} pad-brief`}
          />
          {trimmedScope ? (
            <p className="mt-1.5 font-label text-[12px] uppercase tracking-[0.14em] tabular-nums text-void">
              {trimmedScope.length} character{trimmedScope.length === 1 ? '' : 's'}
            </p>
          ) : null}
        </div>

        <div>
          <FieldLabel htmlFor="client-budget">Budget</FieldLabel>
          <input
            id="client-budget"
            name="budget"
            type="text"
            required
            maxLength={20000}
            value={budget}
            disabled={pending}
            onChange={(event) => setBudget(event.target.value)}
            placeholder="Around ₱300,000, or “under 500k”"
            className={WELL}
          />
          <p className="mt-1.5 font-body text-[13px] leading-[1.5] text-void">
            This helps {studio} shape the quotation to fit what you have in mind. It doesn&rsquo;t
            set your price, and you&rsquo;re not held to it.
          </p>
        </div>

        {error ? (
          <ErrorNotice headline={error.headline} next={error.next} onDismiss={() => setError(null)} />
        ) : null}

        <button
          type="submit"
          disabled={!ready || pending}
          aria-disabled={!ready || pending}
          className={`${ACTION_PRIMARY} mt-1 w-full justify-center ${pending ? 'cursor-wait' : ''}`}
        >
          {pending ? 'Sending' : `Send to ${studio}`}
        </button>
      </form>
    </div>
  )
}
