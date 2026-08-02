import { useState } from 'react'
import type { FormEvent } from 'react'
import { createIntake } from '../lib/api'
import { useRole } from '../lib/role'
import { ACTION_PRIMARY, CARD, DISPLAY, MONO_LABEL, WELL, WELL_TEXTAREA } from './tokens'

/**
 * Recording what a client asked for, before anybody prices it.
 *
 * Four fields and nothing else: this screen writes down what the client said,
 * not what the studio thinks it is worth. Pricing happens on the pad, from the
 * queue this feeds - keeping the two apart means the words a client actually
 * used are never quietly edited into a scope that flatters the estimate.
 *
 * Admin-only, like `createIntake` itself: recording a request opens the queue
 * whoever prices it next works from, which is nearer to inviting somebody than
 * to drafting a quotation. A member is shown why instead of a form the server
 * would refuse.
 */
export default function IntakeScreen() {
  const { isAdmin } = useRole()
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [scope, setScope] = useState('')
  const [budget, setBudget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const trimmedScope = scope.trim()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy || !trimmedScope) return
    setBusy(true)
    setError('')
    createIntake({
      client_email: email.trim(),
      client_phone: phone.trim(),
      scope: trimmedScope,
      // Nothing here yet decides currency, market or tiers - that is the
      // pad's job once this request is being priced. An empty preset is
      // exactly what a request recorded from words alone has to say about it.
      budget_text: budget.trim(),
      preset: {},
    })
      .then(() => {
        window.location.hash = '#/intakes'
      })
      .catch((failure) => setError(failure?.message || 'That request was not recorded.'))
      .finally(() => setBusy(false))
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className={`${CARD} flex min-h-0 flex-1 flex-col`}>
        <div className="shrink-0 border-b border-rule px-5 py-3 sm:px-6">
          <h2 className={`${DISPLAY} text-[20px]`}>New client request</h2>
          <p className="mt-2 max-w-[64ch] font-body text-[13px] leading-[1.6] text-void">
            What the client told you, in their words. You price it on the next screen.
          </p>
        </div>

        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          {isAdmin ? (
            <form onSubmit={submit} className="max-w-[40rem]">
              <div>
                <label htmlFor="intake_email" className={MONO_LABEL}>
                  Client email
                </label>
                <input
                  id="intake_email"
                  type="email"
                  required
                  value={email}
                  disabled={busy}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="client@example.com"
                  className={`${WELL} mt-2`}
                />
              </div>

              <div className="mt-4">
                <label htmlFor="intake_phone" className={MONO_LABEL}>
                  Contact no.
                </label>
                <input
                  id="intake_phone"
                  type="tel"
                  value={phone}
                  disabled={busy}
                  onChange={(event) => setPhone(event.target.value)}
                  placeholder="Optional"
                  className={`${WELL} mt-2`}
                />
              </div>

              <div className="mt-4">
                <label htmlFor="intake_scope" className={MONO_LABEL}>
                  Scope
                </label>
                <textarea
                  id="intake_scope"
                  required
                  value={scope}
                  disabled={busy}
                  onChange={(event) => setScope(event.target.value)}
                  placeholder="A booking site for a dive shop in Cebu. Guests pick a date and pay a deposit online."
                  className={`${WELL_TEXTAREA} pad-brief mt-2`}
                />
              </div>

              <div className="mt-4">
                <label htmlFor="intake_budget" className={MONO_LABEL}>
                  Budget
                </label>
                <input
                  id="intake_budget"
                  type="text"
                  value={budget}
                  disabled={busy}
                  onChange={(event) => setBudget(event.target.value)}
                  placeholder="Whatever they said - a figure, a range, or nothing"
                  className={`${WELL} mt-2`}
                />
                <p className="mt-2 max-w-[56ch] font-body text-[13px] leading-[1.6] text-void">
                  Their figure, as they said it. It guides the quotation; it does not set the
                  price.
                </p>
              </div>

              <button
                type="submit"
                disabled={busy || !email.trim() || !trimmedScope}
                className={`${ACTION_PRIMARY} mt-5`}
              >
                {busy ? 'Recording' : 'Record request'}
              </button>

              {error ? (
                <p role="alert" className="mt-4 font-body text-[14px] text-alert">
                  {error}
                </p>
              ) : null}
            </form>
          ) : (
            <div className="max-w-[60ch]">
              <p className="font-label text-[12px] uppercase tracking-[0.14em] text-faint">
                Not yours to record
              </p>
              <p className="mt-2 font-body text-[15px] leading-[1.6] text-void">
                Recording a client request opens the queue whoever prices it next works from, so
                the server keeps this to this workspace&rsquo;s admins. Ask one of them to log it,
                or open the queue to see what has already come in.
              </p>
              <a href="#/intakes" className="mt-4 inline-block font-body text-[14px] text-ballpoint">
                Open the queue
              </a>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
