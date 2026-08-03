import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { closeIntake, listIntakes, relinkIntake, sendIntake } from '../lib/api'
import { formatDate } from '../lib/format'
import RowMenu from './RowMenu'
import { useRole } from '../lib/role'
import { ACTION, ACTION_PRIMARY, CARD, DISPLAY, MONO_LABEL, WELL } from './tokens'
import type { Intake, IntakeRevision, IntakeState } from '../types'

/**
 * Every client request, grouped by what is waiting on whom rather than by
 * when it arrived.
 *
 * A plain newest-first list would answer "what came in" - this answers "what
 * do I do next", which is the question that actually gets this screen opened.
 * A request waiting on the studio to price it and one already quoted are not
 * the same kind of row, and sorting them together would bury the one that
 * needs a click under the ones that don't.
 *
 * Stage 2 gave the studio three more moves from here, and each is guarded the
 * same way: the row asks before it acts. Sending hands a client a price they
 * cannot be shown twice; reissuing kills a link somebody may be reading their
 * quotation through right now; closing ends the request outright. None of the
 * three has a move back in `intakes.ALLOWED`, so none of them fires on one
 * click.
 */

/** What each state reads as on the chip. All ten are here, including
 * `proposal_sent`, which Stage 3 owns and nothing advances to yet - the
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

/**
 * The states whose whole subject is the quotation the client is reading.
 *
 * Used for how the row *reads*, not for what it opens: these are the rows
 * whose useful date is `sent_at`, and the rows where reissuing the link cuts
 * a client off from a document mid-conversation rather than merely from a
 * form. Which quotation a row opens is a different question with a different
 * answer - see `bundleId` below, which reaches for `sent_bundle_id` in more
 * states than these, because a client goes on holding what they were sent
 * while the studio's next pass is still running or has just failed.
 */
const CLIENT_HAS_QUOTATION = new Set<IntakeState>([
  'sent',
  'revision_requested',
  'finalized',
  'proposal_sent',
])

/** The client's own first line, not the whole scope - a queue is scanned, not
 * read. Falls back silently when a request somehow carries an empty one. */
function firstLine(scope: string): string {
  const trimmed = String(scope || '').trim()
  return trimmed.split('\n')[0] || ''
}

/**
 * The one date this row is actually about, beside the date it arrived.
 *
 * Every row already shows `created_at`, which answers "how long has this been
 * here". It is not the useful date in every state. An `issued` row has no
 * email, no scope and nothing else to tell it apart from the one above it -
 * what it has is a link on a sixty-day clock, and that clock is the thing the
 * studio is racing. A row the client is holding a quotation through is asked
 * about differently again: "when did they get it", which is `sent_at` and
 * never the bundle's own `created_at` - a finished quotation can sit for days
 * before anybody hands it over.
 *
 * A row where the client has asked for a change is checked first and answers
 * with when they asked, not when they were sent the thing they are asking
 * about. Both dates are true, but only one of them is the clock the studio is
 * now on.
 */
function stateDate(row: Intake, asked: IntakeRevision | undefined): string {
  if (row.state === 'issued') {
    return row.token_expires_at ? `Link works until ${formatDate(row.token_expires_at)}` : ''
  }
  if (asked && asked.at) return `Asked ${formatDate(asked.at)}`
  if (CLIENT_HAS_QUOTATION.has(row.state) && row.sent_at) return `Sent ${formatDate(row.sent_at)}`
  return ''
}

type Section = {
  key: string
  heading: string
  rows: Intake[]
  /** The closed section reads as done-with, not as something to act on. */
  muted?: boolean
}

/**
 * The named groups, in the order the studio works through them, then anything
 * left over.
 *
 * Built by elimination rather than by matching each state to a bucket by
 * name: a state this screen has never heard of still lands somewhere instead
 * of vanishing between the cracks, which is what a `switch` with no default
 * would have done to it. **New sections go inside the array literal below,
 * above the `closed` take and the `rest` filter** - both of those run against
 * whatever is still unplaced, so a section appended after them would never
 * see a row.
 *
 * `revision_requested` sits with `submitted` and `quote_failed` under a
 * checkable rule rather than a feeling: those three are exactly the states
 * whose only forward move in `intakes.ALLOWED` is `preparing`. Each of them
 * is a request that will not move again until the studio prices it, which is
 * what "waiting on you" means.
 *
 * `proposal_sent` is deliberately not placed. It is Stage 3's, nothing
 * advances to it, and until something does there is no honest answer to which
 * of these groups it belongs in - so it falls to the catch-all, which is what
 * the catch-all is for.
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
      rows: take(
        (row) =>
          row.state === 'submitted' ||
          row.state === 'quote_failed' ||
          row.state === 'revision_requested',
      ),
    },
    { key: 'preparing', heading: 'Being prepared', rows: take((row) => row.state === 'preparing') },
    { key: 'quoted', heading: 'Quoted', rows: take((row) => row.state === 'quoted') },
    {
      key: 'client',
      heading: 'With the client',
      rows: take((row) => row.state === 'issued' || row.state === 'sent'),
    },
    { key: 'finalized', heading: 'Finalized', rows: take((row) => row.state === 'finalized') },
  ]

  const closed = take((row) => row.state === 'closed')
  const rest = rows.filter((row) => !placed.has(row.id))
  if (rest.length) sections.push({ key: 'further', heading: 'Further along', rows: rest })
  sections.push({ key: 'closed', heading: 'Closed', rows: closed, muted: true })

  return sections
}

/**
 * The three row actions that ask before they act.
 *
 * One value rather than three booleans, and carried with the row id rather
 * than beside it: this screen can only have one confirm open and one request
 * in flight at a time, and a bare id would have opened Close's confirm the
 * moment somebody pressed Reissue.
 */
type RowAction = 'close' | 'send' | 'reissue'

type Pending = { id: string; action: RowAction }

/** Which of one row's actions a screen-wide `Pending` is about, if any. */
function forRow(pending: Pending | null, id: string): RowAction | '' {
  return pending && pending.id === id ? pending.action : ''
}

/**
 * What reissuing costs the client, which is a different thing in each of
 * three situations rather than one warning worn thin over all of them.
 *
 * Before anything has been sent, the link is a form they have not filled in
 * and losing it costs them nothing they have done. Once a quotation is on the
 * other side of it, it is the document they are deciding on and the only two
 * buttons they can answer with. Once they have accepted, those buttons are
 * already spent (`clientview.of` sends `can_revise: false, can_finalize:
 * false` on a finalized intake) and what the link is now is their only copy
 * of what they agreed to - which is a real cost, and a different one.
 */
function reissueCost(state: IntakeState): string {
  if (state === 'sent' || state === 'revision_requested') {
    return 'The client reads their quotation through the link they already have. Reissuing kills it, so until you send them the new one they lose the page - and with it the two buttons they ask for a change or finalize with.'
  }
  if (state === 'finalized' || state === 'proposal_sent') {
    return 'The client has accepted this quotation, and the link is their only copy of what they agreed to. Reissuing kills it, so they cannot open it again until you send them the new one.'
  }
  return 'The link the client already has stops working, so they cannot open their form until you send them the new one.'
}

type ConfirmPanelProps = {
  /** The eyebrow, which is also where focus lands. */
  label: string
  /** The alert rule, for the one of these that destroys something. */
  danger?: boolean
  children: ReactNode
}

/**
 * A question that needs a sentence, asked on its own line under the row.
 *
 * Send and Reissue both owe the reader an explanation before they act, and
 * the row's action strip is a few buttons wide - so they take the full width
 * below it instead. Close does not use this: it needs no sentence, because
 * the menu item already said the word, and it keeps the terse inline confirm
 * it has had since Stage 1.
 *
 * The panel takes focus on mount, and that is the reason it is a component
 * rather than inline JSX. Opening either confirm unmounts whatever was
 * focused - the `Send to client` button, or, for Reissue, the `RowMenu`
 * button Headless UI was about to restore focus to - so without this, focus
 * falls to `<body>` and a keyboard user has to tab from the top of the
 * document to answer a question the page just asked them. Mount-scoped rather
 * than latched behind a ref, for the reason `IssuedLink` gives: this exists
 * only while the question is open, so "arrived" and "opened" are one event,
 * and a StrictMode double-run focuses a node that is already focused.
 */
function ConfirmPanel({ label, danger, children }: ConfirmPanelProps) {
  const labelRef = useRef<HTMLParagraphElement | null>(null)
  useEffect(() => {
    labelRef.current?.focus()
  }, [])

  return (
    <div
      className={`mt-1 w-full border-l-2 pl-3 ${danger ? 'border-l-alert' : 'border-l-ballpoint'}`}
    >
      <p ref={labelRef} tabIndex={-1} className={`${MONO_LABEL} focus-landing`}>
        {label}
      </p>
      {children}
    </div>
  )
}

type IssuedLinkProps = {
  /** The intake this link belongs to, for the field's own label. */
  intakeId: string
  link: string
  /** The new sixty days the reissue bought, off the record it came back with. */
  expiresAt: string
  onDone: () => void
}

/**
 * A reissued link, on the row that reissued it.
 *
 * The same panel `IntakeScreen` shows after Generate, for the same reason and
 * with the same treatment: `role="status"` so it is announced the moment it
 * appears whether or not anything is focused, **and** focus moved into it,
 * which is the more reliable of the two on assistive tech that reads focus
 * moves better than live regions. This is the second of the only two places
 * in the app where a missed announcement is unrecoverable - what appears here
 * is the only copy of this link that will ever exist, and no route will hand
 * it over again.
 *
 * Known and accepted, exactly as it is there: `role="status"` implies
 * `aria-atomic="true"`, so a reader announcing the panel reads the token
 * aloud with it. A link nobody is told about is worse.
 *
 * The effect is keyed on `link` rather than latched behind a ref. A ref latch
 * survives StrictMode's simulated remount, so the second pass would find it
 * already set; this component only exists while there is a link to show, so
 * "arrived" and "changed" are the same event, a double-run focuses a node
 * that is already focused, and there is nothing for a latch to guard.
 */
function IssuedLink({ intakeId, link, expiresAt, onDone }: IssuedLinkProps) {
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const [copyNote, setCopyNote] = useState('')

  useEffect(() => {
    setCopyNote('')
    headingRef.current?.focus()
  }, [link])

  /**
   * Put the link on the clipboard, and say so in words. Lifted from
   * `IntakeScreen`'s own copy control rather than reinvented, including why
   * the check is a check: `navigator.clipboard` needs a secure context, so it
   * is simply absent over plain HTTP on anything but localhost, and
   * `clipboard?.writeText(...).then(...)` would short-circuit the whole chain -
   * a studio on a LAN address pressing Copy, seeing nothing happen, and having
   * no idea why. The field is readable and selectable either way; that is the
   * fallback, and this says so.
   */
  const copy = () => {
    const board = navigator.clipboard
    if (!board) {
      setCopyNote('This browser will not let a page copy for you. Select the link and copy it.')
      return
    }
    board
      .writeText(link)
      .then(() => setCopyNote('Copied. The link is on your clipboard.'))
      .catch(() => setCopyNote('That link could not be copied. Select it and copy it by hand.'))
  }

  return (
    <div
      role="status"
      className="mt-1 w-full rounded-lg border border-rule bg-duplicate px-4 py-3"
    >
      <p className={MONO_LABEL}>New link</p>
      <h4 ref={headingRef} tabIndex={-1} className={`${DISPLAY} focus-landing mt-1 text-[15px]`}>
        Send this link to the client. The one before it has stopped working.
      </h4>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={`intake_link_${intakeId}`} className="sr-only">
          The client&rsquo;s new link
        </label>
        <input
          id={`intake_link_${intakeId}`}
          type="text"
          readOnly
          value={link}
          onFocus={(event) => event.currentTarget.select()}
          className={`${WELL} min-w-[16rem] flex-1 font-label text-[13px]`}
        />
        <button type="button" className={ACTION} onClick={copy}>
          Copy link
        </button>
      </div>
      {/* Its own live region inside the panel's, deliberately: a nested one
          owns its own subtree, so pressing Copy announces this line alone
          rather than re-reading the whole atomic panel - token included -
          every time. */}
      <p role="status" className="mt-2 font-body text-[13px] leading-[1.6] text-ballpoint">
        {copyNote}
      </p>

      <p className="mt-2 max-w-[58ch] font-body text-[13px] leading-[1.6] text-void">
        Copy it now: this is the only time it is shown. The queue does not carry client links and
        nothing can look one up, so a lost link is replaced by reissuing it again &mdash; which
        stops this one from working in its turn.
        {expiresAt ? ` This link works until ${formatDate(expiresAt)}.` : ''}
      </p>

      <button type="button" className={`${ACTION} mt-3`} onClick={onDone}>
        I have copied it
      </button>
    </div>
  )
}

type IntakeRowProps = {
  row: Intake
  isAdmin: boolean
  /** Which of this row's actions is waiting on a confirm, if any. */
  confirming: RowAction | ''
  /** Which of this row's actions has a request in flight, if any. */
  busy: RowAction | ''
  /** The link a reissue on this row put on screen. Empty until one does. */
  link: string
  onRequest: (id: string, action: RowAction) => void
  onCancel: () => void
  onClose: (id: string) => void
  onSend: (id: string, bundleId: string) => void
  onReissue: (id: string) => void
  onDismissLink: (id: string) => void
}

function IntakeRow({
  row,
  isAdmin,
  confirming,
  busy,
  link,
  onRequest,
  onCancel,
  onClose,
  onSend,
  onReissue,
  onDismissLink,
}: IntakeRowProps) {
  const scopeLine = firstLine(row.scope)
  // The three states whose only forward move is `preparing`, per
  // `intakes.ALLOWED` - a fresh request, one whose pass failed, and one the
  // client has asked to change. All three are priced through the same door,
  // and leaving `revision_requested` without one would make a client's own
  // words a dead end in the queue that recorded them.
  const canPrice =
    row.state === 'submitted' ||
    row.state === 'quote_failed' ||
    row.state === 'revision_requested'

  // Which quotation this row opens.
  //
  // Two halves of one rule: **if the client is holding a quotation, that is
  // the one this row opens**, and only a pass that has just succeeded
  // overrides it.
  //
  // `quoted` is that override, and the only one. It is the single state where
  // a pricing pass has just finished and replaced `bundle_ids` wholesale (see
  // `intakes.ALLOWED` on `QUOTED: {PREPARING, ...}`), so the row is about the
  // new document the studio is deciding whether to send - which the Send panel
  // below lets them pick from by name when there is more than one.
  //
  // Everywhere else `sent_bundle_id` wins whenever it is set. That is not
  // only the `sent` / `revision_requested` / `finalized` family: a re-quote
  // that is still running or has just failed leaves the record in `preparing`
  // or `quote_failed` **with the client still reading the bundle that was
  // sent**, and `bundle_ids[0]` on a tiered request is a sibling tier they
  // have never seen - a cheaper one, since the tiers are prepared in order.
  // Opening the entry-level quotation while the client reads the top one, and
  // talking to them about "the quotation", is the failure this whole rule
  // exists to prevent, and it does not become acceptable just because the pass
  // that was meant to replace what they hold happened to fall over.
  //
  // The fallback to `bundle_ids[0]` is the parked Stage 1 finding: a second
  // pass that fails before the client was ever sent anything still has the
  // first pass's good quotation on file, because `intakes.advance` merges the
  // fields it is handed rather than replacing the record (`main.py` stamps
  // `PREPARING` with a job id and `QUOTE_FAILED` with an error, and neither
  // touches `bundle_ids`). Without this the row shows an error and a retry
  // with no hint that a finished quotation is sitting at `#/q/<id>`.
  const bundleId =
    row.state === 'quoted'
      ? row.bundle_ids[0] || ''
      : row.sent_bundle_id || row.bundle_ids[0] || ''

  // The last round only. The earlier ones were answered by the quotation the
  // client is looking at now; this is the one nobody has acted on yet.
  const asked = row.state === 'revision_requested' ? row.revisions.at(-1) : undefined
  const when = stateDate(row, asked)

  // Guarded rather than assumed: `send_intake` refuses a bundle that is not
  // this request's own, so a `quoted` row with an empty list has nothing to
  // offer and a Send button on it would be a door that only looks open.
  const canSend = row.state === 'quoted' && row.bundle_ids.length > 0

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
        {asked && asked.asked ? (
          // Not truncated, unlike the scope line above it, because this is the
          // instruction for the work rather than a label for it: the pad's
          // prefill does not carry the ask, so this row is the only place the
          // studio can read what the client actually wants changed. Bounded by
          // height instead - `MAX_BRIEF_CHARS` lets a client write twenty
          // thousand characters, and one determined row must not become the
          // whole screen.
          <div className="mt-1 max-h-[9rem] max-w-[52ch] overflow-y-auto border-l-2 border-l-ballpoint pl-2">
            <p className={MONO_LABEL}>They asked for</p>
            <p className="mt-0.5 whitespace-pre-wrap font-body text-[13px] leading-[1.6] text-ink">
              {asked.asked}
            </p>
          </div>
        ) : null}
        <p className="mt-2 flex flex-wrap items-center gap-2">
          <span className={row.state === 'quote_failed' ? 'chip chip--alert' : 'chip'}>
            {STATE_LABEL[row.state] || row.state}
          </span>
          <span className={MONO_LABEL}>{formatDate(row.created_at)}</span>
          {when ? <span className={MONO_LABEL}>{when}</span> : null}
        </p>
      </div>

      <span className="flex flex-none flex-wrap items-center gap-2">
        {confirming === 'close' ? (
          // Closing is not going ahead, and there is no move back from it in
          // `intakes.ALLOWED` - the same weight `QuotationList.tsx` and
          // `ProposalList.tsx` give their own irreversible row actions, so the
          // menu gives way to an inline confirm rather than firing on one
          // click. Still inline while the other two confirms are not: this one
          // needs no sentence, because the menu item already said the word.
          <span className="inline-flex gap-2">
            <button
              type="button"
              disabled={Boolean(busy)}
              className={`${ACTION} border-alert text-alert`}
              onClick={() => onClose(row.id)}
            >
              {busy === 'close' ? 'Closing' : 'Close it'}
            </button>
            <button type="button" className={ACTION} onClick={onCancel}>
              Keep it
            </button>
          </span>
        ) : confirming ? (
          // Send and Reissue ask their question on its own line below, where
          // there is room for the sentence each of them owes the reader.
          // Nothing here while one is open, so there is one way to answer it.
          null
        ) : (
          <>
            {canPrice ? (
              <a href={`#/pad/${row.id}`} className={ACTION_PRIMARY}>
                Price this
              </a>
            ) : null}
            {canSend ? (
              <button
                type="button"
                className={ACTION_PRIMARY}
                onClick={() => onRequest(row.id, 'send')}
              >
                Send to client
              </button>
            ) : null}
            {/* Quiet, where it used to be primary. The filled button on a row
                is the move that changes what state the request is in; opening
                a document to read it is not one, and two filled buttons beside
                each other on a `quoted` row would have made neither read as
                the thing to press. */}
            {bundleId ? (
              <a href={`#/q/${bundleId}`} className={ACTION}>
                View quotation
              </a>
            ) : null}
            {/* Both of these are an admin's call, like recording one - the
                server refuses a member either way, and offering a control it
                would be refused for is a door that only looks open.

                Reissue is not marked `danger` even though it destroys a live
                link. `RowMenu`'s red is for the thing you cannot undo *and*
                would not normally do; on an `issued` row, reissuing is the
                recovery, and painting the only way back to a lost link as a
                hazard would be the wrong warning in the state that needs it
                most. The weight lives in the confirm below, which words
                itself differently depending on what the client currently has
                behind the link. Close stays the one red item: it ends the
                request. */}
            {isAdmin && row.state !== 'closed' ? (
              <RowMenu
                label={`Actions for ${row.client_email || 'this request'}`}
                items={[
                  { label: 'Reissue link', onSelect: () => onRequest(row.id, 'reissue') },
                  { label: 'Close', danger: true, onSelect: () => onRequest(row.id, 'close') },
                ]}
              />
            ) : null}
          </>
        )}
      </span>

      {confirming === 'send' ? (
        <ConfirmPanel label="Send to client">
          {busy === 'send' ? (
            <p className="mt-1 font-body text-[13.5px] leading-[1.6] text-void">
              Sending it to the client.
            </p>
          ) : row.bundle_ids.length > 1 ? (
            // More than one quotation on file, so which one the client sees is
            // a decision rather than an accident of ordering - `send_intake`
            // takes the id explicitly for exactly this reason. They are
            // numbered rather than named because nothing on the record names
            // them: the intake carries bare ids, and the preset's tier names
            // are what the studio *asked* for before the pad had its say, not
            // a description of what came back. So each one opens instead.
            <>
              <p className="mt-1 max-w-[62ch] font-body text-[13.5px] leading-[1.6] text-body">
                This request has {row.bundle_ids.length} quotations on file and the client sees one
                of them. Open them to see which is which, then send that one. You cannot unsend it.
              </p>
              <ul className="mt-2 flex flex-col gap-2">
                {row.bundle_ids.map((id, index) => (
                  <li key={id} className="flex flex-wrap items-center gap-2">
                    <span className={MONO_LABEL}>Quotation {index + 1}</span>
                    {/* A new tab, and the only place in this file that opens
                        one. Every other link here replaces the queue happily,
                        but this confirm is component state: navigating away
                        and coming back with Back would leave the panel closed,
                        so "open them, then send that one" would be two passes
                        and the sentence above would be a lie. A hash href
                        resolves against this document, so the new tab boots
                        the app straight at the quotation. */}
                    <a
                      href={`#/q/${id}`}
                      target="_blank"
                      rel="noopener"
                      className={ACTION}
                      aria-label={`Open quotation ${index + 1} in a new tab`}
                    >
                      Open it
                    </a>
                    <button
                      type="button"
                      className={ACTION_PRIMARY}
                      onClick={() => onSend(row.id, id)}
                    >
                      Send this one
                    </button>
                  </li>
                ))}
              </ul>
              <div className="mt-2">
                <button type="button" className={ACTION} onClick={onCancel}>
                  Not yet
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="mt-1 max-w-[62ch] font-body text-[13.5px] leading-[1.6] text-body">
                The client opens their own link and reads this quotation there, and can ask for a
                change or finalize it from the same page. You cannot unsend it.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={ACTION_PRIMARY}
                  onClick={() => onSend(row.id, row.bundle_ids[0] || '')}
                >
                  Send it
                </button>
                {/* A new tab for the same reason the numbered ones above take
                    one: reading it first must not close the confirm that
                    offered it. */}
                <a
                  href={`#/q/${row.bundle_ids[0] || ''}`}
                  target="_blank"
                  rel="noopener"
                  aria-label="Read this quotation first, in a new tab"
                  className={ACTION}
                >
                  Read it first
                </a>
                <button type="button" className={ACTION} onClick={onCancel}>
                  Not yet
                </button>
              </div>
            </>
          )}
        </ConfirmPanel>
      ) : null}

      {confirming === 'reissue' ? (
        <ConfirmPanel label="Reissue link" danger>
          <p className="mt-1 max-w-[62ch] font-body text-[13.5px] leading-[1.6] text-body">
            {reissueCost(row.state)} The new link is shown here once, and nothing can look it up
            afterwards.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={Boolean(busy)}
              className={`${ACTION} border-alert text-alert`}
              onClick={() => onReissue(row.id)}
            >
              {busy === 'reissue' ? 'Reissuing' : 'Reissue it'}
            </button>
            <button type="button" className={ACTION} onClick={onCancel}>
              Keep the current link
            </button>
          </div>
        </ConfirmPanel>
      ) : null}

      {link ? (
        <IssuedLink
          intakeId={row.id}
          link={link}
          expiresAt={row.token_expires_at}
          onDone={() => onDismissLink(row.id)}
        />
      ) : null}
    </article>
  )
}

export default function IntakeListScreen() {
  const { isAdmin } = useRole()
  const [rows, setRows] = useState<Intake[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState<Pending | null>(null)
  const [busy, setBusy] = useState<Pending | null>(null)
  /**
   * The links reissues have put on screen, by row.
   *
   * Kept here and **not** merged into `rows`, which is the whole point: a
   * link is the one thing an `Intake` never carries (see the type's own
   * docstring on `token`), and folding `IntakeIssued` into the queue's state
   * would put a live client credential into the list every screen reads from.
   *
   * A map rather than one link at a time because every one of them is
   * unrecoverable - reissuing row B must not silently take row A's only copy
   * off the screen. A `Map` rather than a record for the same reason the rest
   * of this file guards its indexing: `get` answers `undefined` for a row
   * that has no link, which is the truth.
   */
  const [links, setLinks] = useState<Map<string, string>>(new Map())

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

  const forget = (id: string) =>
    setLinks((current) => {
      const next = new Map(current)
      next.delete(id)
      return next
    })

  /**
   * The one shape all three row actions share.
   *
   * `call` is a thunk rather than a promise so the request genuinely leaves
   * after the latch below is set, and so the two actions that need to do
   * something with their own answer on the way through can.
   */
  const act = (id: string, action: RowAction, call: () => Promise<Intake>, failed: string) => {
    setError('')
    // Set synchronously before the request leaves, not when it comes back: a
    // second click inside the round trip would otherwise reach the server as a
    // move `ALLOWED` refuses - advance(closed -> closed), or a second
    // quoted -> sent - and come back as a visible failure over a row that
    // plainly did the thing.
    setBusy({ id, action })
    call()
      .then((updated) => {
        // Conditional, not a bare clear: resolving row A must not wipe out a
        // confirm somebody opened on row B while A's request was in flight.
        setConfirming((current) => (current && current.id === id ? null : current))
        setRows((current) => current.map((row) => (row.id === id ? updated : row)))
      })
      .catch((failure) => setError(failure?.message || failed))
      .finally(() => setBusy((current) => (current && current.id === id ? null : current)))
  }

  const handleClose = (id: string) =>
    act(
      id,
      'close',
      () =>
        closeIntake(id).then((closed) => {
          // Closing blanks the token server-side (`intakes._write` does it in
          // one place, keyed on the state it is writing), so a link still on
          // screen for this row is already dead. Take it down rather than
          // leave somebody copying something that will not open.
          forget(id)
          return closed
        }),
      'That request was not closed.',
    )

  const handleSend = (id: string, bundleId: string) =>
    act(id, 'send', () => sendIntake(id, bundleId), 'That quotation was not sent to the client.')

  const handleReissue = (id: string) =>
    act(
      id,
      'reissue',
      () =>
        relinkIntake(id).then((issued) => {
          // Where the link stops travelling with the record. Everything past
          // this line is a plain `Intake` and joins `rows` as one - which it
          // must, and not only for the link's sake: `relink` buys another
          // sixty days, so a row that kept its old copy would go on showing an
          // expiry that has just been replaced.
          const { link, ...record } = issued
          setLinks((current) => new Map(current).set(id, link))
          return record
        }),
      "That request's link was not reissued.",
    )

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
              Not done
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
                    confirming={forRow(confirming, row.id)}
                    busy={forRow(busy, row.id)}
                    link={links.get(row.id) || ''}
                    onRequest={(id, action) => setConfirming({ id, action })}
                    onCancel={() => setConfirming(null)}
                    onClose={handleClose}
                    onSend={handleSend}
                    onReissue={handleReissue}
                    onDismissLink={forget}
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
