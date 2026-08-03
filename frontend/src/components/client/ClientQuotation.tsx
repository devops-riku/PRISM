import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import MarkdownView from '../MarkdownView'
import StampTotal from '../StampTotal'
import ErrorNotice from '../ErrorNotice'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL_TEXTAREA } from '../tokens'
import { formatDate, formatMoney, formatPct } from '../../lib/format'
import { ClientApiError, finalizeClientIntake, reviseClientIntake } from '../../lib/clientApi'
import type { ClientIntakeView, ClientQuotationView } from '../../types'

/**
 * Face 3: `sent`, `revision_requested` and `finalized` - one shape,
 * `clientview._QUOTED_FACE`, told apart only by `view.state` and by
 * `can_revise`/`can_finalize`, which are both true only in `sent`.
 *
 * `view.narrative` is the *entire* rendered proposal document
 * (`storage.markdown_for(bundle, "proposal")`), not just the model's prose -
 * it already carries its own "Valid until" line, computed from the
 * quotation's own issue date. `reference`, `total`, `validity` and
 * `payment_schedule` below are separate, hand-built fields read straight off
 * the bundle (see `clientview.of`'s own docstring on why: never a filtered
 * `ProposalBundle`). Nothing here recomputes a date from `sent_at` to sit
 * beside the narrative's own - two "valid until" dates on one page, from two
 * different clocks, is worse than showing the day count once and leaving
 * the narrative to say the rest.
 */

/** See `ClientForm.tsx`'s identical function for what this does and does
 * not print, and why. Duplicated rather than shared: the two files have no
 * other reason to import from each other, and importing one from the other
 * across `ClientShell.tsx` would create a needless cycle. */
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
    return { headline: failure.message || 'That could not be sent.', gone: false }
  }
  return { headline: 'That could not be sent.', gone: false }
}

export default function ClientQuotation({
  view,
  token,
  onUpdate,
  onGone,
}: {
  view: ClientQuotationView
  token: string
  onUpdate: (next: ClientIntakeView) => void
  onGone: () => void
}) {
  const studio = view.studio_name || 'This studio'

  const [asking, setAsking] = useState(false)
  const [note, setNote] = useState('')
  const [revisePending, setRevisePending] = useState(false)
  const [reviseError, setReviseError] = useState<{ headline: string; next?: string } | null>(null)

  const [finalizePending, setFinalizePending] = useState(false)
  const [finalizeError, setFinalizeError] = useState<{ headline: string; next?: string } | null>(null)

  const noteRef = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => {
    if (asking) noteRef.current?.focus()
  }, [asking])

  const trimmedNote = note.trim()
  const rounds = view.revisions.length

  const submitRevision = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!trimmedNote || revisePending) return

    setRevisePending(true)
    setReviseError(null)

    reviseClientIntake(token, trimmedNote)
      .then((next) => {
        onUpdate(next)
        setNote('')
        setAsking(false)
      })
      .catch((failure: unknown) => {
        const described = describeWriteFailure(failure)
        if (described.gone) {
          onGone()
          return
        }
        setReviseError(described)
      })
      .finally(() => setRevisePending(false))
  }

  const cancelAsking = () => {
    if (revisePending) return
    setAsking(false)
    setNote('')
    setReviseError(null)
  }

  const submitFinalize = () => {
    if (finalizePending) return

    setFinalizePending(true)
    setFinalizeError(null)

    finalizeClientIntake(token)
      .then((next) => onUpdate(next))
      .catch((failure: unknown) => {
        const described = describeWriteFailure(failure)
        if (described.gone) {
          onGone()
          return
        }
        setFinalizeError(described)
      })
      .finally(() => setFinalizePending(false))
  }

  return (
    <div className="rounded-[18px] border border-rule bg-paper p-6 shadow-raised sm:p-9">
      <p className={MONO_LABEL}>{studio}</p>
      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1 className={`${DISPLAY} text-[24px] leading-[1.2] text-ink`}>
          {view.state === 'finalized' ? 'Your quotation' : 'Here is your quotation'}
        </h1>
        {view.reference ? (
          <p className="font-label text-[12px] uppercase tracking-[0.14em] text-void">
            Ref. <span className="text-ink">{view.reference}</span>
          </p>
        ) : null}
      </div>

      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-1.5 font-label text-[12.5px] text-void">
        <div className="flex items-baseline gap-1.5">
          <dt className="uppercase tracking-[0.12em]">Sent</dt>
          <dd className="text-ink">{view.sent_at ? formatDate(view.sent_at) : '—'}</dd>
        </div>
        {view.validity > 0 ? (
          <div className="flex items-baseline gap-1.5">
            <dt className="uppercase tracking-[0.12em]">Valid for</dt>
            <dd className="text-ink">
              {view.validity} day{view.validity === 1 ? '' : 's'}
            </dd>
          </div>
        ) : null}
      </dl>

      <div className="mt-5">
        <StampTotal total={view.total} currency={view.currency} />
      </div>

      {view.payment_schedule.length > 0 ? (
        <div className="mt-7">
          <p className={MONO_LABEL}>Payment schedule</p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-[14px]">
              <thead>
                <tr>
                  <th
                    scope="col"
                    className="border-b border-rule py-1.5 text-left font-label text-[11.5px] font-medium uppercase tracking-[0.12em] text-faint"
                  >
                    Milestone
                  </th>
                  <th
                    scope="col"
                    className="border-b border-rule py-1.5 text-right font-label text-[11.5px] font-medium uppercase tracking-[0.12em] text-faint"
                  >
                    Share
                  </th>
                  <th
                    scope="col"
                    className="border-b border-rule py-1.5 text-right font-label text-[11.5px] font-medium uppercase tracking-[0.12em] text-faint"
                  >
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {view.payment_schedule.map((row, index) => (
                  <tr key={index} className="border-b border-hairline last:border-b-0">
                    <td className="py-2 pr-3 align-top text-ink">
                      <span className="block">{row.label}</span>
                      {row.trigger ? (
                        <span className="mt-0.5 block text-[12.5px] text-void">{row.trigger}</span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3 text-right align-top font-label tabular-nums text-ink">
                      {formatPct(row.percent)}
                    </td>
                    <td className="py-2 text-right align-top font-label tabular-nums text-ink">
                      {formatMoney(row.amount, view.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {view.narrative.trim() ? (
        <div className="mt-8 border-t border-rule pt-6">
          <MarkdownView markdown={view.narrative} />
        </div>
      ) : null}

      {rounds > 0 ? (
        <div className="mt-8 border-t border-rule pt-6">
          <p className={MONO_LABEL}>
            Change{rounds === 1 ? '' : 's'} asked
          </p>
          <ul className="mt-3 space-y-3">
            {view.revisions.map((entry, index) => (
              <li key={index} className="border-l-2 border-l-rule pl-3">
                <p className="font-label text-[11.5px] uppercase tracking-[0.14em] text-faint">
                  {entry.at ? formatDate(entry.at, { withTime: true }) : `Round ${index + 1}`}
                </p>
                <p className="mt-0.5 font-body text-[14px] leading-[1.5] text-ink">{entry.asked}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {view.state === 'revision_requested' ? (
        <div className="mt-8 rounded-lg border border-rule bg-duplicate/50 px-4 py-3">
          <p className="font-body text-[14px] leading-[1.6] text-ink">
            {studio} is preparing a new quotation from the change you asked for.
          </p>
        </div>
      ) : null}

      {view.state === 'finalized' ? (
        <div className="mt-8 rounded-lg border border-ballpoint/25 bg-accent-soft px-4 py-3">
          <p className="font-body text-[14px] leading-[1.6] text-ink">
            {studio} has been told you&rsquo;re ready to go ahead.
          </p>
        </div>
      ) : null}

      {view.can_revise ? (
        <div className="mt-8 border-t border-rule pt-6">
          <p className={MONO_LABEL}>Ask for a change</p>

          {!asking ? (
            <>
              {rounds > 0 ? (
                <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                  You&rsquo;ve asked for a change {rounds} time{rounds === 1 ? '' : 's'} already.
                </p>
              ) : null}
              <button type="button" onClick={() => setAsking(true)} className={`${ACTION} mt-3`}>
                Ask for a change
              </button>
            </>
          ) : (
            <form onSubmit={submitRevision} className="mt-3">
              <label htmlFor="revise-note" className="sr-only">
                What would you like changed
              </label>
              <textarea
                id="revise-note"
                ref={noteRef}
                required
                maxLength={20000}
                value={note}
                disabled={revisePending}
                onChange={(event) => setNote(event.target.value)}
                placeholder="What should change? Be as specific as you can - scope moves, not rates."
                className={`${WELL_TEXTAREA} pad-brief`}
              />

              {reviseError ? (
                <div className="mt-3">
                  <ErrorNotice
                    headline={reviseError.headline}
                    next={reviseError.next}
                    onDismiss={() => setReviseError(null)}
                  />
                </div>
              ) : null}

              <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={cancelAsking}
                  disabled={revisePending}
                  className={ACTION}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!trimmedNote || revisePending}
                  aria-disabled={!trimmedNote || revisePending}
                  className={`${ACTION_PRIMARY} ${revisePending ? 'cursor-wait' : ''}`}
                >
                  {revisePending ? 'Sending' : 'Send this note'}
                </button>
              </div>
            </form>
          )}
        </div>
      ) : null}

      {view.can_finalize ? (
        <div className="mt-8 border-t border-rule pt-6">
          <p className={MONO_LABEL}>Finalize</p>
          <p className="mt-2 max-w-[54ch] font-body text-[14px] leading-[1.6] text-void">
            This tells {studio} you&rsquo;re ready to go ahead. It is not a signature and creates no
            contract - nothing is charged, and no payment is due by clicking it. {studio} will follow
            up about what happens next.
          </p>

          {finalizeError ? (
            <div className="mt-3">
              <ErrorNotice
                headline={finalizeError.headline}
                next={finalizeError.next}
                onDismiss={() => setFinalizeError(null)}
              />
            </div>
          ) : null}

          <button
            type="button"
            onClick={submitFinalize}
            disabled={finalizePending}
            aria-disabled={finalizePending}
            className={`${ACTION_PRIMARY} mt-4 ${finalizePending ? 'cursor-wait' : ''}`}
          >
            {finalizePending ? 'Sending' : 'Finalize'}
          </button>
        </div>
      ) : null}
    </div>
  )
}
