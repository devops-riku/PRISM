import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import MarkdownView from '../MarkdownView'
import StampTotal from '../StampTotal'
import ErrorNotice from '../ErrorNotice'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL_TEXTAREA } from '../tokens'
import { formatDate, formatMoney, formatPct } from '../../lib/format'
import {
  ClientApiError,
  clientQuotationUrl,
  finalizeClientIntake,
  reviseClientIntake,
} from '../../lib/clientApi'
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
 *
 * `revision_requested` reads `sent_bundle_id` - the *previous* send, since
 * revising does not touch it (only a fresh Send does). Everything below the
 * header - the total, the schedule, the narrative - is therefore the old
 * numbers, not a preview of what is coming. The notice that a new quotation
 * is being prepared sits above all of it, not buried after it, and the
 * heading itself says "previous" rather than repeating "here is your
 * quotation" over a figure that is about to change - a buyer should never
 * read a total as current directly above a sentence saying it is not.
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

  // The button that opens the revise textarea - focus returns here on
  // Cancel, since it is the one thing left in this section once the
  // textarea is gone. Not used on a *successful* revise: that swaps
  // `view.state` to `revision_requested`, `can_revise` goes false, the
  // whole section (button included) unmounts, and the heading-focus effect
  // below takes over instead - a `null` ref here at that point is a no-op,
  // not a conflict.
  const askTriggerRef = useRef<HTMLButtonElement | null>(null)
  const wasAskingRef = useRef(false)
  useEffect(() => {
    if (wasAskingRef.current && !asking) {
      askTriggerRef.current?.focus()
    }
    wasAskingRef.current = asking
  }, [asking])

  // `sent`, `revision_requested` and `finalized` are one component that
  // never remounts across the transitions between them - unlike
  // `ClientForm` -> `ClientWaiting`, which are different components and get
  // mount-time focus for free. Re-running on every `view.state` change is
  // what gives a revise or a finalize the same "read this again from the
  // top" treatment: the heading text itself changes per state (see below),
  // so refocusing it is also what makes a screen reader announce the new
  // wording rather than staying silent because the same component instance
  // never unmounted.
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => {
    headingRef.current?.focus()
  }, [view.state])

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

  const heading =
    view.state === 'finalized'
      ? 'Your quotation'
      : view.state === 'revision_requested'
        ? 'Your previous quotation'
        : 'Here is your quotation'

  return (
    // A narrower phone spends less of its width on the card edge; the shared
    // desktop padding returns at `sm`.
    <div className="min-w-0 rounded-[18px] border border-rule bg-paper p-5 shadow-raised sm:p-8">
      <p className={MONO_LABEL}>{studio}</p>
      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className={`${DISPLAY} focus-landing text-[24px] leading-[1.2] text-ink`}
        >
          {heading}
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

      {/* The backend built these two files for exactly this face - a client
          forwarding their own link to a boss or a partner needs a copy that
          survives outside the browser tab it arrived in. A plain `<a href>`:
          `clientQuotationUrl`'s own docstring is explicit that this door
          needs no auth header, unlike `lib/api.ts`'s `openFile`.

          Suppressed entirely in `revision_requested`, not merely labelled -
          the document these links produce carries the *previous* bundle's
          total with nothing on the page it prints (no state parameter, no
          watermark, and `_filename`'s own `-r{revision}` suffix only
          appears once `revision > 1`, which is not a qualifier a client
          reading a downloaded PDF would recognise as one). This on-page
          disclaimer, a few lines below, cannot travel with a file once it
          is forwarded: a boss who opens an attached PDF never sees this
          component at all. Suppressing was chosen over relabelling the
          link text ("Download the previous quotation") for exactly that
          reason - a truer link label still only warns whoever is looking
          at *this* page, not whoever the file ends up in front of. The
          links come back once the studio sends a fresh bundle and this
          state moves back to `sent`, or once it's `finalized`. */}
      {view.state !== 'revision_requested' ? (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
          <a
            href={clientQuotationUrl(token, 'pdf')}
            download={`${view.reference || 'quotation'}.pdf`}
            className="font-label text-[12px] uppercase tracking-[0.12em] text-ballpoint underline decoration-1 underline-offset-[3px]"
          >
            Download PDF
          </a>
          <a
            href={clientQuotationUrl(token, 'html')}
            target="_blank"
            rel="noopener noreferrer"
            className="font-label text-[12px] uppercase tracking-[0.12em] text-ballpoint underline decoration-1 underline-offset-[3px]"
          >
            View as a page
            <span className="sr-only"> (opens the printable copy in a new tab)</span>
          </a>
        </div>
      ) : null}

      {view.state === 'revision_requested' ? (
        <div role="status" className="mt-6 rounded-lg border border-rule bg-duplicate/50 px-4 py-3">
          <p className="font-body text-[14px] leading-[1.6] text-ink">
            {studio} is preparing a new quotation from the change you asked for. Everything on this
            page - the total, the validity, the schedule and the document below - is from your{' '}
            <strong>previous</strong> quotation, not the new one.
          </p>
        </div>
      ) : null}

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

      {view.state === 'finalized' ? (
        <div role="status" className="mt-8 rounded-lg border border-ballpoint/25 bg-accent-soft px-4 py-3">
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
              <button
                type="button"
                ref={askTriggerRef}
                onClick={() => setAsking(true)}
                className={`${ACTION} mt-3`}
              >
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
