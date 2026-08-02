import { useEffect, useState } from 'react'
import { closeIntake, listIntakes } from '../lib/api'
import { formatDate } from '../lib/format'
import RowMenu from './RowMenu'
import { useRole } from '../lib/role'
import { ACTION, ACTION_PRIMARY, CARD, DISPLAY, MONO_LABEL } from './tokens'
import type { Intake, IntakeState } from '../types'

/**
 * Every client request, grouped by what is waiting on whom rather than by
 * when it arrived.
 *
 * A plain newest-first list would answer "what came in" - this answers "what
 * do I do next", which is the question that actually gets this screen opened.
 * A request waiting on the studio to price it and one already quoted are not
 * the same kind of row, and sorting them together would bury the one that
 * needs a click under the ones that don't.
 */

/** What each state reads as on the chip. Stage 2's states are here too, even
 * though `intakes.advance` refuses them until the client link ships - the
 * server can in principle send any of them, and an unrecognised chip is worse
 * than a label for a state this screen does not otherwise act on. */
const STATE_LABEL: Partial<Record<IntakeState, string>> = {
  submitted: 'Submitted',
  preparing: 'Preparing',
  quoted: 'Quoted',
  quote_failed: 'Quote failed',
  closed: 'Closed',
  issued: 'Issued',
  sent: 'Sent',
  revision_requested: 'Revision requested',
  finalized: 'Finalized',
  proposal_sent: 'Proposal sent',
}

/** The client's own first line, not the whole scope - a queue is scanned, not
 * read. Falls back silently when a request somehow carries an empty one. */
function firstLine(scope: string): string {
  const trimmed = String(scope || '').trim()
  return trimmed.split('\n')[0] || ''
}

type Section = {
  key: string
  heading: string
  rows: Intake[]
  /** The closed section reads as done-with, not as something to act on. */
  muted?: boolean
}

/**
 * The three named groups, in the order the brief fixes them, then anything
 * left over.
 *
 * Built by elimination rather than by matching each state to a bucket by
 * name: a state this screen has never heard of still lands somewhere instead
 * of vanishing between the cracks, which is what a `switch` with no default
 * would have done to it.
 */
function buildSections(rows: Intake[]): Section[] {
  const placed = new Set<string>()
  const take = (match: (row: Intake) => boolean) => {
    const found = rows.filter((row) => match(row) && !placed.has(row.id))
    found.forEach((row) => placed.add(row.id))
    return found
  }

  const sections: Section[] = [
    {
      key: 'waiting',
      heading: 'Waiting on you',
      rows: take((row) => row.state === 'submitted' || row.state === 'quote_failed'),
    },
    { key: 'preparing', heading: 'Being prepared', rows: take((row) => row.state === 'preparing') },
    { key: 'quoted', heading: 'Quoted', rows: take((row) => row.state === 'quoted') },
  ]

  const closed = take((row) => row.state === 'closed')
  const rest = rows.filter((row) => !placed.has(row.id))
  if (rest.length) sections.push({ key: 'further', heading: 'Further along', rows: rest })
  sections.push({ key: 'closed', heading: 'Closed', rows: closed, muted: true })

  return sections
}

type IntakeRowProps = {
  row: Intake
  isAdmin: boolean
  confirming: boolean
  closing: boolean
  onRequestClose: (id: string) => void
  onConfirmClose: (id: string) => void
  onCancelClose: () => void
}

function IntakeRow({
  row,
  isAdmin,
  confirming,
  closing,
  onRequestClose,
  onConfirmClose,
  onCancelClose,
}: IntakeRowProps) {
  const scopeLine = firstLine(row.scope)
  // `preparing` failed once; `ALLOWED` still permits it back into `preparing`,
  // so pricing it again is a legal move, not a dead end - the row gets the
  // same door a fresh `submitted` request does.
  const canPrice = row.state === 'submitted' || row.state === 'quote_failed'
  // `bundle_ids` is the only way from a quoted row back to what it produced -
  // without this, "quoted" is a dead end even though the quotation itself is
  // sitting right there at `#/q/<id>`. Guarded rather than assumed: nothing
  // in `ALLOWED` can produce a `quoted` row with an empty list, but a row this
  // screen has never heard of is exactly the case worth not crashing on.
  const firstBundleId = row.state === 'quoted' ? row.bundle_ids[0] || '' : ''

  return (
    <article className="row-touch flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hairline px-5 py-3 last:border-b-0 sm:px-6">
      <div className="min-w-[16rem] flex-1">
        <p className="font-body text-[15px] text-ink">{row.client_email || 'No email on file'}</p>
        {scopeLine ? (
          <p className="mt-1 max-w-[52ch] truncate font-body text-[13.5px] text-void">
            {scopeLine}
          </p>
        ) : null}
        {row.state === 'quote_failed' && row.error ? (
          <p className="mt-1 max-w-[52ch] truncate border-l-2 border-l-alert pl-2 font-body text-[13px] text-alert">
            {row.error}
          </p>
        ) : null}
        <p className="mt-2 flex flex-wrap items-center gap-2">
          <span className={row.state === 'quote_failed' ? 'chip chip--alert' : 'chip'}>
            {STATE_LABEL[row.state] || row.state}
          </span>
          <span className={MONO_LABEL}>{formatDate(row.created_at)}</span>
        </p>
      </div>

      <span className="flex flex-none flex-wrap items-center gap-2">
        {confirming ? (
          // Closing is not going ahead, and there is no move back from it in
          // `intakes.ALLOWED` - the same weight `QuotationList.tsx` and
          // `ProposalList.tsx` give their own irreversible row actions, so the
          // menu gives way to an inline confirm rather than firing on one
          // click.
          <span className="inline-flex gap-2">
            <button
              type="button"
              disabled={closing}
              className={`${ACTION} border-alert text-alert`}
              onClick={() => onConfirmClose(row.id)}
            >
              {closing ? 'Closing' : 'Close it'}
            </button>
            <button type="button" className={ACTION} onClick={onCancelClose}>
              Keep it
            </button>
          </span>
        ) : (
          <>
            {canPrice ? (
              <a href={`#/pad/${row.id}`} className={ACTION_PRIMARY}>
                Price this
              </a>
            ) : null}
            {firstBundleId ? (
              <a href={`#/q/${firstBundleId}`} className={ACTION_PRIMARY}>
                View quotation
              </a>
            ) : null}
            {/* Closing is an admin's call, like recording one - the server
                refuses a member either way, and offering it to someone it
                would be refused for is a door that only looks open. */}
            {isAdmin && row.state !== 'closed' ? (
              <RowMenu
                label={`Actions for ${row.client_email || 'this request'}`}
                items={[
                  { label: 'Close', danger: true, onSelect: () => onRequestClose(row.id) },
                ]}
              />
            ) : null}
          </>
        )}
      </span>
    </article>
  )
}

export default function IntakeListScreen() {
  const { isAdmin } = useRole()
  const [rows, setRows] = useState<Intake[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmingId, setConfirmingId] = useState('')
  // Set synchronously before the request leaves, not when it comes back: a
  // second click inside the round trip would otherwise reach the server as
  // advance(closed -> closed), which `ALLOWED[CLOSED]` refuses - a visible
  // "not closed" over a row that plainly is.
  const [closingId, setClosingId] = useState('')

  useEffect(() => {
    let live = true
    listIntakes()
      .then((found) => {
        if (!live) return
        setRows(found)
        setError('')
      })
      .catch((failure) => {
        if (live) setError(failure?.message || 'The request queue did not load.')
      })
      .finally(() => {
        if (live) setLoading(false)
      })
    return () => {
      live = false
    }
  }, [])

  const handleClose = (id: string) => {
    setError('')
    setClosingId(id)
    closeIntake(id)
      .then((updated) => {
        // Conditional, not a bare clear: resolving row A must not wipe out a
        // confirm somebody opened on row B while A's request was in flight.
        setConfirmingId((current) => (current === id ? '' : current))
        setRows((current) => current.map((row) => (row.id === id ? updated : row)))
      })
      .catch((failure) => setError(failure?.message || 'That request was not closed.'))
      .finally(() => setClosingId((current) => (current === id ? '' : current)))
  }

  const sections = buildSections(rows).filter((section) => section.rows.length > 0)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className={`${CARD} flex min-h-0 flex-1 flex-col`}>
        <div className="flex shrink-0 items-baseline gap-3 border-b border-rule px-5 py-3 sm:px-6">
          <h2 className={`${DISPLAY} text-[20px]`}>Client requests</h2>
          <p className={MONO_LABEL}>{loading ? 'Reading' : `${rows.length} on file`}</p>
        </div>

        {error ? (
          <p role="alert" className="border-b border-rule px-5 py-3 font-body text-[15px] sm:px-6">
            <span className="block font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              Not loaded
            </span>
            <span className="mt-1 block">{error}</span>
          </p>
        ) : null}

        {!loading && rows.length === 0 && !error ? (
          <p className="px-5 py-12 text-center font-body text-[15px] text-void sm:px-6">
            No client requests yet. Start one from{' '}
            <a href="#/" className="text-ballpoint underline underline-offset-[3px]">
              the front page
            </a>
            .
          </p>
        ) : null}

        {sections.length ? (
          <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto pb-2">
            {sections.map((section) => (
              <div key={section.key} className={section.muted ? 'opacity-60' : ''}>
                <h3
                  className={`${MONO_LABEL} border-b border-rule bg-duplicate px-5 py-2 sm:px-6`}
                >
                  {section.heading} · {section.rows.length}
                </h3>
                {section.rows.map((row) => (
                  <IntakeRow
                    key={row.id}
                    row={row}
                    isAdmin={isAdmin}
                    confirming={confirmingId === row.id}
                    closing={closingId === row.id}
                    onRequestClose={setConfirmingId}
                    onConfirmClose={handleClose}
                    onCancelClose={() => setConfirmingId('')}
                  />
                ))}
              </div>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  )
}
