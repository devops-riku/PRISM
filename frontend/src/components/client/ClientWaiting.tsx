import { useEffect, useRef } from 'react'
import { formatBytes, formatDate } from '../../lib/format'
import { DISPLAY, MONO_LABEL } from '../tokens'
import type { ClientWaitingView } from '../../types'

/**
 * Face 2: `waiting` - the one shape `clientview._WAITING` collapses
 * `submitted`, `preparing`, `quoted` and `quote_failed` into. This component
 * must never try to tell those four apart again: there is nothing in `view`
 * that could, on purpose, and there must never be a stage word, a chip, a
 * spinner or a progress bar here that implies one is closer than another.
 *
 * `view.sent_at` is `intake.created_at` - when this page first became
 * reachable, not when the client actually submitted the form that put them
 * in this state (see `ClientWaitingView`'s own comment in `types.ts`). The
 * label below says "Reachable since" rather than "Submitted" for exactly
 * that reason - it is true of every intake in `_WAITING`, including one
 * whose owner submitted hours after the link was minted.
 *
 * No `.chip--live`, `.orbit`, `.thread--waiting` or `.bar-live` - every one
 * of those says "work is happening right now," which is the one thing this
 * screen is built to never claim.
 *
 * This is the face a `ClientForm` submit lands on, and that transition
 * unmounts one component and mounts this one - React gives no signal of its
 * own that anything happened, so a screen reader user gets nothing unless
 * this component says so itself. Two things do that: `role="status"` on the
 * card (a live region a screen reader announces as soon as it appears,
 * whether or not it happens to be focused), and moving focus to the
 * heading on mount, for the sighted-keyboard case and as a second path to
 * the same announcement on assistive tech that reads focus moves more
 * reliably than live regions. Belt and suspenders on purpose: a stranger on
 * a phone who just pressed "Send" needs to know something happened, and
 * this is the one screen built entirely to reassure them of that.
 */
export default function ClientWaiting({ view }: { view: ClientWaitingView }) {
  const studio = view.studio_name || 'This studio'

  const headingRef = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  return (
    <div
      role="status"
      className="rounded-[18px] border border-rule bg-paper p-6 shadow-raised sm:p-8"
    >
      <p className={MONO_LABEL}>{studio}</p>
      <h1
        ref={headingRef}
        tabIndex={-1}
        className={`${DISPLAY} focus-landing mt-3 text-[22px] leading-[1.2] text-ink`}
      >
        {studio} has your scope.
      </h1>
      <p className="mt-2 font-body text-[15px] leading-[1.6] text-ink">Nobody has replied yet.</p>

      <dl className="mt-6 divide-y divide-hairline border-y border-hairline font-body text-[14px]">
        <div className="flex items-baseline justify-between gap-4 py-2.5">
          <dt className="text-void">Reachable since</dt>
          <dd className="text-ink">{view.sent_at ? formatDate(view.sent_at) : '—'}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4 py-2.5">
          <dt className="text-void">From</dt>
          <dd className="text-ink">{view.email || '—'}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4 py-2.5">
          <dt className="text-void">What you wrote</dt>
          <dd className="text-ink">
            {view.scope_length} character{view.scope_length === 1 ? '' : 's'}
          </dd>
        </div>
        {/* Only when there are any. A row reading "What you sent — nothing" on
            every intake without attachments would turn an optional field into
            an omission, on the one screen whose whole job is reassurance.
            Listed by name and size, which is the entire point: a client who
            attached the wrong version of a document finds out here, and there
            is nothing else on this page or behind it that would ever tell them
            - `clientview.of` sends no id and no link, so the file cannot be
            fetched back, only recognised. */}
        {view.attachments.length > 0 ? (
          <div className="flex items-baseline justify-between gap-4 py-2.5">
            <dt className="shrink-0 text-void">
              What you sent{view.attachments.length > 1 ? ` (${view.attachments.length})` : ''}
            </dt>
            {/* Every file, never a truncated "and 2 more". Six is the ceiling
                (`config.MAX_CLIENT_FILES`), so the longest this can get is six
                short rows, and a list that hides the one file the client is
                worried about is worse than no list. */}
            <dd className="min-w-0 text-ink">
              <ul className="space-y-0.5 text-right">
                {view.attachments.map((file, index) => (
                  <li key={`${index}-${file.name}`} className="truncate">
                    {file.name}{' '}
                    <span className="font-label text-[12px] tabular-nums text-faint">
                      {formatBytes(file.bytes)}
                    </span>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
      </dl>

      <p className="mt-6 font-body text-[13px] leading-[1.6] text-faint">
        There&rsquo;s nothing to do here for now. This page updates when you reload it.
      </p>
    </div>
  )
}
