import { useEffect, useRef, useState } from 'react'
import type { MouseEvent, ReactNode } from 'react'
import { closeIntake, intakeFileUrl, listIntakes, openFile, relinkIntake, sendIntake } from '../lib/api'
import { formatBytes, formatDate } from '../lib/format'
import RowMenu from './RowMenu'
import { useRole } from '../lib/role'
import { ACTION, ACTION_PRIMARY, CARD, DISPLAY, MONO_LABEL, WELL } from './tokens'
import { INLINE_KINDS } from '../types'
import type { Intake, IntakeAttachment, IntakeRevision, IntakeState } from '../types'

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
 * than beside it: a bare id would have opened Close's confirm the moment
 * somebody pressed Reissue.
 *
 * One confirm open and one request in flight at a time, and both halves of
 * that are enforced rather than assumed - see `act`'s `inFlight` guard, which
 * exists because the second half used to be an assertion in this very comment
 * and nothing else.
 */
type RowAction = 'close' | 'send' | 'reissue'

type Pending = { id: string; action: RowAction }

/** Which of one row's actions a screen-wide `Pending` is about, if any. */
function forRow(pending: Pending | null, id: string): RowAction | '' {
  return pending && pending.id === id ? pending.action : ''
}

/**
 * What reissuing costs the client - which is a different thing in each of
 * five situations, and every one of them is read off what the client's own
 * side actually renders rather than off what the lifecycle name suggests.
 *
 * That distinction is the whole reason this is five branches and not two. The
 * obvious split is "before a quotation" and "after one", and it is wrong at
 * both ends. Before: only `issued` still has a form behind the link -
 * `clientview._WAITING` is `{submitted, preparing, quoted, quote_failed}` and
 * collapses all four to one waiting page, so telling an admin they are
 * costing a client a form is false in four states out of five, and false in
 * the most damaging direction: the client filled that form in yesterday and
 * would be left staring at a dead page. After: `clientview.of` sets both
 * `can_revise` and `can_finalize` to `intake.state == SENT`, so the two
 * buttons exist in exactly one state, not three - `ClientQuotation` suppresses
 * the whole panel under `revision_requested`, and by `finalized` they are
 * spent.
 *
 * So each branch says what that client would actually lose sight of, and none
 * of them claims a control the client does not have.
 */
function reissueCost(state: IntakeState): string {
  if (state === 'issued') {
    return 'The client has not filled in their form yet, and the link they hold is the only way to it. Reissuing kills that one, so they cannot open the form until you send them the new one.'
  }
  if (state === 'sent') {
    return 'The client reads their quotation through the link they already have. Reissuing kills it, so until you send them the new one they lose the page - and with it the two buttons they ask for a change or finalize with.'
  }
  if (state === 'revision_requested') {
    return 'The client has asked for a change and is waiting on it, and reads the quotation through the link they already have. Reissuing kills it, so until you send them the new one they lose sight of the very thing they asked you to change.'
  }
  if (state === 'finalized' || state === 'proposal_sent') {
    return 'The client has accepted this quotation, and the link is their only copy of what they agreed to. Reissuing kills it, so they cannot open it again until you send them the new one.'
  }
  return 'The client has already sent you their scope and is waiting to hear back - there is no form left for them to fill in. Reissuing kills the link that waiting page is behind, so until you send them the new one they have nowhere to check.'
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
 *
 * `link` is only a sound key because the row this hangs off no longer
 * remounts when it changes section - see the flat list in the render below,
 * which is there for exactly this. While each section had its own wrapper
 * element, sending a row that was showing a link remounted the panel and this
 * effect fired again on a link that had not changed: the token re-announced
 * itself aloud, focus was yanked back here, and `copyNote` was reset so a
 * link the studio had already copied said nothing about it.
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

      {/* "Leaving this screen takes it with you" is not padding. The queue is
          covered in links that replace it - Price this, View quotation, the
          empty-state link - and this panel lives in `IntakeListScreen`'s own
          state, so any of them drops it. Those links are deliberately not
          given `target="_blank"` the way the send confirm's are: the confirm
          owns the two links inside it and can reason about them, whereas
          turning every navigation on a busy queue into a new tab because one
          row might be holding a panel is a screen-wide surprise for a rare
          state. So the hazard is said out loud instead, where the studio is
          already being told to copy it now, and the recovery is the one the
          next sentence describes. */}
      <p className="mt-2 max-w-[58ch] font-body text-[13px] leading-[1.6] text-void">
        Copy it now: this is the only time it is shown, and leaving this screen takes it with you.
        The queue does not carry client links and nothing can look one up, so a lost link is
        replaced by reissuing it again &mdash; which stops this one from working in its turn.
        {expiresAt ? ` This link works until ${formatDate(expiresAt)}.` : ''}
      </p>

      <button type="button" className={`${ACTION} mt-3`} onClick={onDone}>
        I have copied it
      </button>
    </div>
  )
}

/**
 * Fetch one of a client's own files with the session in a header, rather than
 * letting the browser follow the link on its own.
 *
 * The same shape `ProposalView.tsx`'s and `SheetHeader.tsx`'s own `take`
 * helpers already are, copied rather than imported: this file has no
 * dependency on either component, and reaching into one for a six-line
 * closure would be a stranger coupling than repeating it. The href stays
 * real underneath - worth being able to copy or open in a new tab - but a
 * plain navigation cannot carry the `Authorization` header this route needs
 * once accounts are configured, so the click is taken here and the file
 * arrives as a blob, exactly as every other authed file in this app already
 * does.
 *
 * `download` mirrors the server's own `Content-Disposition` choice rather
 * than forcing a save regardless of kind: a raster image opens in a new tab,
 * the same thing `inline` on the response is for, and a document downloads,
 * matching `attachment`. `openFile` never reads the response header itself
 * - it cannot, the header describes bytes already in a blob by the time this
 * runs - so this is a second, independent read of the same fact from the one
 * place the frontend already has it: the manifest's own `kind`.
 */
function openAttachment(
  url: string,
  name: string,
  download: boolean,
): (event: MouseEvent<HTMLAnchorElement>) => void {
  return (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
    event.preventDefault()
    openFile(url, download ? { download: name } : {}).catch((failure) => {
      window.alert(failure?.message || 'That file could not be opened.')
    })
  }
}

type AttachmentsProps = {
  intakeId: string
  attachments: IntakeAttachment[]
  /**
   * A closed intake's own manifest still names every file it ever held, and
   * every one of them is gone: `close()` deletes the stored objects and
   * leaves `Intake.attachments` populated, per plan - the record is the
   * history of what was sent. Rendered as links regardless, a closed row
   * would offer a full set of addresses that all 404 the moment anyone
   * clicked one. So a closed row shows the same names and sizes as plain,
   * non-interactive text instead, with a line saying why: the history stays
   * readable, and nothing on it looks like a control that still works.
   */
  closed: boolean
}

/**
 * What a client attached, by name and size, each one a link to
 * `GET /api/intakes/{intake_id}/files/{file_id}` - the half of this feature
 * the studio asked for by name.
 *
 * A count line rather than a bare list: a queue row is scanned, not read,
 * and "3 files attached" answers "did they send anything" before anyone has
 * to look further. It sits **outside** the scrolling region below it, on
 * purpose - it is the thing this whole block exists to answer at a glance,
 * and a row with enough files to need the scrollbar is exactly the row where
 * it would otherwise scroll out of view first. The list itself is never cut
 * to the first few to make it fit - only the list, not the count, is bounded
 * by height and scrolls instead, the same treatment this file already gives
 * a long revision request (`asked.asked`, in `IntakeRow` below) - so a
 * request with more files than fit on screen still shows every one of them,
 * just not all at once.
 */
function AttachmentsBlock({ intakeId, attachments, closed }: AttachmentsProps) {
  if (!attachments.length) return null

  return (
    <div className="mt-1 max-w-[52ch] border-l-2 border-l-hairline pl-2">
      <p className={MONO_LABEL}>
        {attachments.length === 1 ? '1 file attached' : `${attachments.length} files attached`}
      </p>
      <div className="mt-0.5 max-h-[9rem] overflow-y-auto">
        {closed ? (
          <p className="font-body text-[12.5px] leading-[1.5] text-faint">
            Removed when this request was closed.
          </p>
        ) : null}
        <ul className="mt-0.5 flex flex-col gap-0.5">
          {attachments.map((file) => {
            const label = file.name || 'Attachment'
            const size = formatBytes(file.bytes)
            if (closed) {
              return (
                <li key={file.id} className="truncate font-body text-[13px] text-faint">
                  {label} <span className={MONO_LABEL}>{size}</span>
                </li>
              )
            }
            const url = intakeFileUrl(intakeId, file.id)
            // `intakefiles.INLINE_TYPES` is the plan's single source of
            // truth for the raster allowlist - mirrored once, in
            // `types.ts`'s `INLINE_KINDS`, and read here rather than
            // restated as a shape of its own (`kind.startsWith('image/')`
            // only coincides with the real set today and is exactly the
            // drift the plan warns about).
            const isRaster = INLINE_KINDS.has(file.kind)
            return (
              <li key={file.id} className="truncate">
                <a
                  href={url}
                  className="font-body text-[13px] text-ballpoint underline underline-offset-[3px]"
                  onClick={openAttachment(url, label, !isRaster)}
                >
                  {label}
                </a>{' '}
                <span className={MONO_LABEL}>{size}</span>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

type IntakeRowProps = {
  row: Intake
  isAdmin: boolean
  /** Closed rows read as done-with. Carried per row since the flat list has
   *  no per-section wrapper to hold it. */
  muted: boolean
  /** Which of this row's actions is waiting on a confirm, if any. */
  confirming: RowAction | ''
  /** Which of this row's actions has a request in flight, if any - the label
   *  on the button that started it. */
  busy: RowAction | ''
  /** Whether a request is in flight **anywhere** on this screen. Every action
   *  button reads this rather than `busy`, because the lock is screen-wide:
   *  a control that stays live on row B while row A is mid-request is a
   *  control that does nothing when pressed. */
  locked: boolean
  /** Bumped each time an action lands on this row; 0 when none has. */
  landedSeq: number
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
  muted,
  confirming,
  busy,
  locked,
  landedSeq,
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
  //
  // **The `||` chain must not fall through a deleted bundle, and it does not.**
  // `DELETE /api/proposals/{id}` does not prune `bundle_ids` (`main.py`'s
  // `_quoted_bundle` says so in as many words), so a `sent_bundle_id` can name
  // a bundle that no longer exists and this will hand back a dead `#/q/<id>`.
  // Falling back to `bundle_ids[0]` there looks like the fix and is the bug:
  // it would quietly open a *different* document, which is the precise thing
  // this whole rule exists to prevent, and it would do it at the moment the
  // studio is least able to notice. A link that goes nowhere is a failure the
  // studio can see and act on; a link to the wrong quotation is one they
  // cannot. Left as it is on purpose - do not "fix" it by widening the chain.
  const bundleId =
    row.state === 'quoted'
      ? row.bundle_ids[0] || ''
      : row.sent_bundle_id || row.bundle_ids[0] || ''

  // The last round only. The earlier ones were answered by the quotation the
  // client is looking at now; this is the one nobody has acted on yet.
  const asked = row.state === 'revision_requested' ? row.revisions.at(-1) : undefined
  const when = stateDate(row, asked)

  // Two guards, both against the same server.
  //
  // `isAdmin`, because `send_intake` calls `_require_admin` exactly as `close`
  // and `relink` do - a member who pressed this would read the whole "you
  // cannot unsend it" confirm, press Send it, and be told only an admin can.
  // The same reasoning the row menu below already applies to Close and Reissue
  // link; sending is on that side of the line too, and the server is the
  // boundary either way.
  //
  // And a non-empty `bundle_ids`, because `send_intake` refuses a bundle that
  // is not this request's own - so a `quoted` row with an empty list has
  // nothing to offer.
  const canSend = isAdmin && row.state === 'quoted' && row.bundle_ids.length > 0

  /**
   * Where focus goes when an action on this row finishes.
   *
   * Every one of the three unmounts the control that started it - the confirm
   * closes, and on `send` and `close` the row changes section on top of that -
   * so without this, focus falls to `<body>` and a keyboard user is returned
   * to the top of the document after every send. `ConfirmPanel` solves the
   * opening half of this; this is the closing half, and leaving one done and
   * the other not is worse than doing neither.
   *
   * The row itself rather than a control inside it, because the row is the
   * answer: an `<article>` taking focus is read out with its content, so what
   * gets announced is the request and the state it has just become. Keyed on a
   * counter rather than a boolean-and-a-callback, so reissuing the same row
   * twice lands twice, and no callback identity ends up in a dependency list.
   *
   * No `focus-landing` here, deliberately, and it is the one place in this app
   * that wants the ring. `index.css` takes the outline off headings that only
   * ever receive focus programmatically, on the grounds that there is nothing
   * to act on and nowhere for a ring to send a sighted user next. A row is the
   * opposite case on both counts: View quotation and the row menu are inside
   * it, so the ring is doing exactly what the base layer intends - saying
   * "here, and your next Tab goes in here".
   */
  const rowRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!landedSeq) return
    rowRef.current?.focus()
  }, [landedSeq])

  return (
    <article
      ref={rowRef}
      tabIndex={-1}
      className={`row-touch flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hairline px-5 py-3 last:border-b-0 sm:px-6 ${
        muted ? 'opacity-60' : ''
      }`}
    >
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
        <AttachmentsBlock
          intakeId={row.id}
          attachments={row.attachments}
          closed={row.state === 'closed'}
        />
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
              disabled={locked}
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
                      disabled={locked}
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
                  disabled={locked}
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
              disabled={locked}
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
  /**
   * What went wrong, and whether it was this screen arriving or somebody
   * acting.
   *
   * The heading travels with the message rather than being a constant above
   * it, because one banner now serves two entirely different failures. A
   * queue that would not load is "Not loaded"; a send or a reissue that was
   * refused is "Not done". Either word over the other one's message is a lie
   * of exactly the kind this codebase spends its comments avoiding - "Not
   * done - The request queue did not load" blames an action nobody took.
   */
  const [problem, setProblem] = useState<{ heading: string; detail: string } | null>(null)
  const [confirming, setConfirming] = useState<Pending | null>(null)
  const [busy, setBusy] = useState<Pending | null>(null)
  /**
   * Which row an action has just landed on, and a sequence number so the same
   * action landing twice on the same row is still two events.
   *
   * Read by the row to take focus, and never cleared: a landing that has
   * already been consumed is indistinguishable from one that has not if the
   * value stops changing, and clearing it from the child would mean passing a
   * callback whose identity changes every render straight into an effect's
   * dependencies. The counter sidesteps both.
   */
  const [landed, setLanded] = useState<{ id: string; seq: number } | null>(null)
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
        setProblem(null)
      })
      .catch((failure) => {
        if (live) {
          setProblem({
            heading: 'Not loaded',
            detail: failure?.message || 'The request queue did not load.',
          })
        }
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
   * True from the moment a row action's request leaves until it comes back.
   *
   * A ref as well as `busy` state, not instead of it: `busy` is what the rows
   * render from, and a state variable read inside an event handler is the
   * value from the last render, so two handlers running before React has
   * re-rendered would both see `null` and both start. The ref is written
   * synchronously and read synchronously, which is what a mutual exclusion
   * actually needs. All three calls are `async` functions, so `call()` always
   * hands back a promise and `finally` always runs - the ref cannot be
   * stranded true by a synchronous throw.
   */
  const inFlight = useRef(false)

  /**
   * The one shape all three row actions share.
   *
   * `call` is a thunk rather than a promise so the request genuinely leaves
   * after the latch below is set, and so the two actions that need to do
   * something with their own answer on the way through can.
   *
   * **One request at a time, screen-wide, and that is enforced rather than
   * merely intended.** `busy` was a single slot any row could overwrite, so
   * starting an action on row B while row A's was in flight left row A
   * rendering as idle - its "Reissuing" label gone, its buttons re-enabled -
   * and invited a second reissue of the same row. Two relinks in flight is the
   * one genuinely silent failure this screen can produce: each mints a token
   * and kills the one before it (`intakes.relink`), both resolve into `links`,
   * last writer wins, and if the responses land out of order the panel shows a
   * token the second call already revoked. The studio copies it, sends it, and
   * the client is told the link is not valid. Every other double-action fails
   * loudly - a second `quoted -> sent` is a move `ALLOWED` refuses and comes
   * back a visible 409 - so this one is worth a lock rather than a comment.
   */
  const act = (id: string, action: RowAction, call: () => Promise<Intake>, failed: string) => {
    if (inFlight.current) return
    inFlight.current = true
    setProblem(null)
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
        // The row is about to change, and on `send` and `close` it changes
        // section too. Say where it went - see the row's own landing effect.
        setLanded((current) => ({ id, seq: (current ? current.seq : 0) + 1 }))
      })
      .catch((failure) => setProblem({ heading: 'Not done', detail: failure?.message || failed }))
      .finally(() => {
        inFlight.current = false
        setBusy((current) => (current && current.id === id ? null : current))
      })
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

        {problem ? (
          <p role="alert" className="border-b border-rule px-5 py-3 font-body text-[15px] sm:px-6">
            <span className="block font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              {problem.heading}
            </span>
            <span className="mt-1 block">{problem.detail}</span>
          </p>
        ) : null}

        {!loading && rows.length === 0 && !problem ? (
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
            {/*
              One flat list of keyed children, not a wrapper element per
              section. This is load-bearing rather than tidiness: React
              reconciles by key **within one parent**, so a row wrapped in its
              own section element is a different child the moment it changes
              section - unmounted from the old wrapper, mounted into the new
              one, and every piece of state it was holding thrown away.

              That is not hypothetical. Sending a `quoted` row moves it from
              "Quoted" into "With the client"; if that row was showing a
              reissued link, the panel remounted, and three things happened at
              once: an atomic `role="status"` announced itself again with the
              token read aloud in the middle of the studio's next task, focus
              was pulled back to its heading from wherever they had put it, and
              the "Copied" line was blanked so an already-copied link read as
              though it never had been. Flat, the row keeps its identity, React
              moves the node, and the panel simply stays where it was.

              `muted` moves onto the heading and the rows themselves, since
              there is no longer a wrapper to carry it.
            */}
            {sections.flatMap((section) => [
              <h3
                key={`heading-${section.key}`}
                className={`${MONO_LABEL} border-b border-rule bg-duplicate px-5 py-2 sm:px-6 ${
                  section.muted ? 'opacity-60' : ''
                }`}
              >
                {section.heading} · {section.rows.length}
              </h3>,
              ...section.rows.map((row) => (
                <IntakeRow
                  key={row.id}
                  row={row}
                  isAdmin={isAdmin}
                  muted={Boolean(section.muted)}
                  confirming={forRow(confirming, row.id)}
                  busy={forRow(busy, row.id)}
                  locked={Boolean(busy)}
                  landedSeq={landed && landed.id === row.id ? landed.seq : 0}
                  link={links.get(row.id) || ''}
                  onRequest={(id, action) => setConfirming({ id, action })}
                  onCancel={() => setConfirming(null)}
                  onClose={handleClose}
                  onSend={handleSend}
                  onReissue={handleReissue}
                  onDismissLink={forget}
                />
              )),
            ])}
          </div>
        ) : null}
      </section>
    </div>
  )
}
