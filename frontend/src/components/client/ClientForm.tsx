import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { FieldLabel } from '../FieldRow'
import ErrorNotice from '../ErrorNotice'
import KindPicker from '../KindPicker'
import ClientDropzone from './ClientDropzone'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL, WELL_TEXTAREA } from '../tokens'
import { ClientApiError, submitClientIntake } from '../../lib/clientApi'
import type { ClientSubmitBody, ClientSubmitFiles } from '../../lib/clientApi'
import type { ClientIntakeView, ClientIssuedView, QuotationKind } from '../../types'

/**
 * Face 1: `issued`. A stranger's first look at this studio - what kind of
 * work it is and four fields under it, nothing else, the studio's own name at
 * the top of the page rather than PRISM's.
 *
 * Asked one step at a time, and that is the only structural change from the
 * single scrolling form this used to be. The order is unchanged and each
 * placement below still carries the reasoning that put it there; the steps
 * partition that sequence, they do not re-decide it.
 *
 * WHY A STEPPER AND NOT A TALLER CARD. The single form was measured at 985px
 * of content in the 1000px a 1080px screen offers, and 1093px with "Something
 * else" open - so `overflow-y-auto` engaged and the Send button left the
 * screen on the one path that adds a field. Every fix available inside one
 * page trades against the same budget: shorten the copy, shrink the picker,
 * drop a hint. Four steps spend that budget four times instead of once, and
 * the tallest of them is comfortably inside a 768px screen.
 *
 * EVERY PANEL STAYS MOUNTED, hidden with `display:none` rather than unmounted.
 * This is a correctness requirement, not a transition nicety: `ClientDropzone`
 * owns the picked-file list and its own refusals, and this component holds only
 * the already-split result. Unmounting step 4 would throw that list away while
 * `files` here still carried the files, so a client who stepped back to fix
 * their email would return to an empty picker that was still going to send
 * three attachments. Hidden panels keep every field's value, the picker's list,
 * and each textarea's scroll position, at the cost of a form element the user
 * cannot currently see - which submits nothing, because this form never uses
 * native submission.
 *
 * The kind of work is asked here rather than on the studio's own Generate
 * screen, where it used to live. The reasoning that put it there was that the
 * kind is the studio's reading of the job, and that is true of what the answer
 * *does* - it picks the second document and the words the quotation is written
 * in - and false of who knows it. A client commissioning an audit knows they
 * are commissioning an audit; a studio holding a paragraph is guessing. So the
 * question moves and the consequences do not: `App.tsx`'s `readPreset` still
 * hands the pad a kind, it is now the client's answer rather than the studio's
 * default.
 *
 * Every field here is length-bounded and stored verbatim by `/submit`
 * (`_normalise_client_email`/`_normalise_client_phone`/`_normalise_scope`/
 * `_normalise_budget_text` in `main.py`) - none of it is validated for shape
 * server-side beyond a length ceiling, so the `required`/`type="email"`
 * attributes here are for this screen's own sake, not a contract the API
 * enforces. `EMAIL_SHAPE` below is the one exception worth the trouble:
 * this address is the studio's only route back to whoever filled this in,
 * and the server only strips and bounds it, so a shape check has to happen
 * here or nowhere.
 *
 * No button here is ever `disabled` - not the step's own forward button for
 * incomplete fields, and, since this form grew a file picker, not the last
 * one while a request is in flight either. A grey, unclickable button with no
 * reachable explanation is a dead end for a user who filled in one of two
 * fields and cannot tell which one is missing; each step instead lets the
 * click happen and answers it with a summary naming exactly what is left on
 * *that step*, each entry wired to the field it names via `aria-describedby`.
 *
 * The in-flight half of that used to be a real `disabled`, and it was a defect
 * rather than a nicety. Disabling the focused element removes it from the
 * accessibility tree, and the browser drops focus to `<body>` - so a client on
 * a screen reader pressed Send and landed nowhere, with nothing announced for
 * the whole width of the request. That was survivable when the request was a
 * few hundred bytes of JSON and lasted a moment. With up to 20 MiB of
 * attachments behind it the window is now long enough to read as broken, so
 * `pending` is carried by `aria-disabled` plus the `if (pending) return` guard
 * in `handleSubmit`: the button stays focusable and stays the thing focus is
 * on, and the live region below it says what is happening. Assistive tech is
 * told the control is unavailable; nothing is told to throw focus away.
 */

/** A shape check, not a deliverability one: local part, an `@`, a domain
 * with a dot. Permissive on purpose - this only has to catch `asdf`, not
 * reject every legal address a strict RFC 5322 pattern would. */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** The four steps, in the order the single form already asked them.
 *
 * `short` is what the progress rail prints, which has to survive four across
 * on a narrow card; the panel heading below says the same thing at length.
 * Nothing about the sequence is derived - a client moves through it one way
 * and the studio reads the answers in the order they were asked. */
const STEPS = [
  { id: 'kind', short: 'Work' },
  { id: 'contact', short: 'Contact' },
  { id: 'brief', short: 'Brief' },
  { id: 'files', short: 'Files' },
] as const

const LAST_STEP = STEPS.length - 1

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
  // Asked first, because it decides the vocabulary the whole quotation is
  // written in and what the studio's second document turns out to be. It used
  // to be asked on the studio's own Generate screen, on the reasoning that it
  // was the studio's reading of the job rather than the client's answer -
  // which is true of the consequences and false of the question. The client
  // is the one who knows whether they are commissioning a website or an audit,
  // and asking them is cheaper than a studio guessing from a paragraph.
  //
  // `software` opens selected because it is what PRISM has always quoted, so
  // a client whose work is a website answers nothing at all.
  const [kind, setKind] = useState<QuotationKind>('software')
  const [kindLabel, setKindLabel] = useState('')
  // Already split into the server's two file fields by `ClientDropzone`, which
  // is the only thing in the browser that decides what a file is. This
  // component never inspects a `File`: it holds what the picker handed it and
  // passes it on, so there is exactly one place the image/document rule lives.
  const [files, setFiles] = useState<ClientSubmitFiles>({ images: [], documents: [] })
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<{ headline: string; next?: string } | null>(null)

  const [step, setStep] = useState(0)
  // The furthest step reached, so the rail can send a client back to fix an
  // answer and forward again in one click each, rather than making them walk
  // every panel between. It only ever grows.
  const [furthest, setFurthest] = useState(0)
  // Which steps have had a forward press that found something missing. Per
  // step, not one flag: a client who has not yet pressed anything on step 3
  // should not arrive to find it already marked wrong because step 2 was.
  // Once a step is in here it stays, so its per-field messages keep tracking
  // progress live as each one is fixed rather than vanishing and reappearing
  // only on the next press.
  const [attemptedSteps, setAttemptedSteps] = useState<ReadonlySet<number>>(() => new Set())

  const trimmedEmail = email.trim()
  const trimmedScope = scope.trim()
  const trimmedBudget = budget.trim()
  const trimmedKindLabel = kindLabel.trim()

  const emailMissing = !trimmedEmail
  const emailInvalid = !emailMissing && !EMAIL_SHAPE.test(trimmedEmail)
  const scopeMissing = !trimmedScope
  const budgetMissing = !trimmedBudget
  // Only for `other`, and only because the whole point of picking it is the
  // word that comes with it: `prompts.kind_block` writes that word into the
  // heading the model works under, and an `other` with nothing typed falls
  // back to a generic heading that says what the work is *not*. Every other
  // kind carries its own name already - which is why step 1 can only fail for
  // one of the six choices, and passes untouched for the other five.
  const kindLabelMissing = kind === 'other' && !trimmedKindLabel

  /** What each step is still waiting for, in the words the summary prints.
   * The last step has nothing of its own - attachments are optional - so it
   * carries the whole form's remainder instead, which is what makes Send
   * unable to fire on an answer a client skipped past on an earlier panel. */
  const missingByStep = useMemo<readonly string[][]>(() => {
    const kindMissing = kindLabelMissing ? ['Kind of work - name the discipline'] : []
    const contactMissing = emailMissing
      ? ['Email']
      : emailInvalid
        ? ['Email - that doesn’t look like a full address']
        : []
    const briefMissing = [
      ...(scopeMissing ? ['Scope'] : []),
      ...(budgetMissing ? ['Budget'] : []),
    ]
    return [
      kindMissing,
      contactMissing,
      briefMissing,
      [...kindMissing, ...contactMissing, ...briefMissing],
    ]
  }, [kindLabelMissing, emailMissing, emailInvalid, scopeMissing, budgetMissing])

  const stepMissing = missingByStep[step]
  const showSummary = attemptedSteps.has(step) && stepMissing.length > 0

  // Stable, so the picker's own `useCallback`s are not rebuilt on every
  // keystroke in the fields on the steps before it.
  const handleFiles = useCallback((picked: ClientSubmitFiles) => setFiles(picked), [])

  const attachedCount = files.images.length + files.documents.length

  const summaryRef = useRef<HTMLDivElement | null>(null)
  const headingRef = useRef<HTMLHeadingElement | null>(null)

  // The summary box only enters the DOM once this step's press first finds
  // something missing - at the moment `handleSubmit` runs, `summaryRef.current`
  // is still `null`, because this render hasn't committed the box yet. This
  // effect catches that case once the DOM has it, and also catches a client
  // stepping back to a panel they already know is incomplete.
  useEffect(() => {
    if (attemptedSteps.has(step) && missingByStep[step].length > 0) summaryRef.current?.focus()
  }, [attemptedSteps, step, missingByStep])

  // Move focus to the new panel's heading when the step actually changes.
  //
  // Keyed on the step VALUE rather than a "have we run yet" boolean, and that
  // is deliberate: StrictMode tears an effect down and re-runs it on mount, so
  // a boolean latch set on the first run reads as "not the first run" on the
  // second and steals focus onto a heading nobody navigated to. Comparing
  // against the last step seen is immune to that - the value is the same both
  // times, so both runs return early.
  const lastStepSeen = useRef(step)
  useEffect(() => {
    if (lastStepSeen.current === step) return
    lastStepSeen.current = step
    headingRef.current?.focus()
  }, [step])

  /** Walk forward from `step`, stopping at the first panel that is still
   * missing something. Returns where the client ended up. */
  const advanceTo = (target: number): number => {
    for (let at = step; at < target; at += 1) {
      if (missingByStep[at].length > 0) {
        setAttemptedSteps((was) => new Set(was).add(at))
        setStep(at)
        return at
      }
    }
    setStep(target)
    setFurthest((was) => Math.max(was, target))
    return target
  }

  /** The rail's own navigation. Backwards is always free - nothing is lost and
   * every answer is still in state. Forwards runs the same gate the primary
   * button does, so the rail cannot be used to walk past an unanswered
   * question. */
  const goTo = (target: number) => {
    if (pending || target === step) return
    if (target < step) {
      setStep(target)
      return
    }
    advanceTo(target)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending) return

    if (step < LAST_STEP) {
      advanceTo(step + 1)
      return
    }

    if (stepMissing.length > 0) {
      // The last panel carries the whole form's remainder, so this is the
      // guard that stops a skipped answer from being sent. Mark it and send
      // the client back to the first panel that is actually incomplete.
      setAttemptedSteps((was) => new Set(was).add(LAST_STEP))
      const firstBad = missingByStep.findIndex((entries, at) => at < LAST_STEP && entries.length > 0)
      if (firstBad >= 0) {
        setAttemptedSteps((was) => new Set(was).add(firstBad))
        setStep(firstBad)
      }
      return
    }

    setPending(true)
    setError(null)

    const body: ClientSubmitBody = {
      client_email: trimmedEmail,
      client_phone: phone.trim(),
      scope: trimmedScope,
      budget_text: trimmedBudget,
      client_kind: kind,
      // Sent only where it means something. `kind_block` reads this field for
      // `other` alone, so shipping a label the client typed before changing
      // their mind back to `software` would put a word on the record that
      // nothing will ever read and that a studio would reasonably believe was
      // chosen.
      client_kind_label: kind === 'other' ? trimmedKindLabel : '',
    }

    submitClientIntake(token, body, files)
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

  const HEADINGS = [
    `Tell ${studio} what kind of work this is`,
    `How ${studio} reaches you`,
    'The work, and what you have in mind',
    'Anything worth attaching',
  ]
  // One line each, at this card's width, on purpose - see the reserve on the
  // element itself. Two lines on three steps and one on the fourth was 23px of
  // the height that put a scrollbar on a 125%-scaled 1080 screen.
  const BLURBS = [
    'It decides how the quotation is written.',
    'Where the quotation comes back to.',
    'What you need, and roughly what you had in mind.',
    'Optional - a brief, a drawing, a photo of a whiteboard.',
  ]

  return (
    <div className="rounded-[18px] border border-rule bg-paper p-5 shadow-raised sm:p-6">
      <p className={MONO_LABEL}>{studio}</p>

      {/* The rail. An `<ol>` because the steps are ordered and a screen reader
          should say how many there are; `aria-current="step"` marks where the
          client is. A step at or before the furthest reached is a real button
          - going back costs nothing and loses nothing - and everything past it
          is a plain span rather than a disabled button, so the tab order never
          stops on a control that refuses to do anything. */}
      <ol className="mt-3 flex gap-1.5">
        {STEPS.map((entry, index) => {
          const reachable = index <= furthest
          const current = index === step
          const bar = `block h-[3px] w-full rounded-full transition-colors ${
            current ? 'bg-ballpoint' : index < step ? 'bg-ballpoint/40' : 'bg-rule'
          }`
          const word = `mt-2 block font-label text-[11px] uppercase tracking-[0.14em] ${
            current ? 'text-ink' : 'text-faint'
          }`
          return (
            <li key={entry.id} className="flex-1">
              {reachable ? (
                <button
                  type="button"
                  onClick={() => goTo(index)}
                  aria-current={current ? 'step' : undefined}
                  className="block w-full rounded-sm text-left"
                >
                  <span className={bar} />
                  <span className={word}>{entry.short}</span>
                </button>
              ) : (
                <span className="block w-full text-left">
                  <span className={bar} />
                  <span className={word}>{entry.short}</span>
                </span>
              )}
            </li>
          )
        })}
      </ol>

      <h1
        ref={headingRef}
        tabIndex={-1}
        className={`${DISPLAY} focus-landing mt-4 text-[20px] leading-[1.2] text-ink`}
      >
        {HEADINGS[step]}
      </h1>
      {/* One line, reserved, so a blurb that happens to wrap on a narrow window
          cannot move the buttons under it. Every string above is written to fit
          one line at this card's width; the reserve is the floor for the one
          that does not. */}
      <p className="mt-1.5 min-h-[23px] max-w-[52ch] font-body text-[14.5px] leading-[1.6] text-void">
        {BLURBS[step]}
      </p>

      <form onSubmit={handleSubmit} noValidate className="mt-4 flex flex-col gap-3.5">
        {/* The reserved panel area, and the reason it is a fixed floor rather
            than whatever the current step needs.
            Measured at 1920x1080: the four panels are 191, 101, 260 and 77px,
            and step 1 with "Something else" open is 299. Left to size itself
            the card walked 404 -> 625px between steps, so Next moved out from
            under the cursor on every press and the whole page re-centred each
            time. Reserving the tallest costs blank space under the shortest
            panel - which is what a wizard looks like - and buys a card that
            never moves, including on the one path that used to overflow it.
            The floor is a minimum, not a cap: a browser wrapping this wider
            still grows rather than clipping. */}
        <div className="min-h-[299px]">
        {/* Step 1. First, and on a panel of its own rather than among the
            fields: it is the only question here whose answer changes how the
            others are read. `KindPicker` is the studio's own component, used
            unchanged - the same disciplines in the same order, so a client and
            the studio pricing them are looking at one vocabulary rather than
            two that have to be kept in step. */}
        <div className={step === 0 ? '' : 'hidden'}>
          <FieldLabel htmlFor="client-kind">Kind of work</FieldLabel>
          <div className="mt-3">
            <KindPicker
              value={kind}
              label={kindLabel}
              onChange={setKind}
              onLabel={setKindLabel}
              disabled={pending}
            />
          </div>
          {attemptedSteps.has(0) && kindLabelMissing ? (
            <p className="mt-2 font-body text-[13px] leading-[1.6] text-alert">
              Name the discipline so {studio} can write in its language.
            </p>
          ) : null}
        </div>

        {/* Step 2. Paired, because they are one question - how to reach you -
            and because two full-width rows for a 30-character address and an
            optional phone number was most of the height that pushed the Send
            button off the screen back when all four steps were one page. */}
        <div className={step === 1 ? '' : 'hidden'}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
                aria-invalid={attemptedSteps.has(1) && (emailMissing || emailInvalid)}
                aria-describedby={
                  attemptedSteps.has(1) && (emailMissing || emailInvalid)
                    ? 'client-email-hint client-email-error'
                    : 'client-email-hint'
                }
                className={WELL}
              />
              <p id="client-email-hint" className="mt-1.5 font-body text-[13px] leading-[1.5] text-void">
                Where {studio} sends the quotation.
              </p>
              {attemptedSteps.has(1) && emailMissing ? (
                <p id="client-email-error" className="mt-1 font-body text-[13px] text-alert">
                  Enter your email address.
                </p>
              ) : null}
              {attemptedSteps.has(1) && !emailMissing && emailInvalid ? (
                <p id="client-email-error" className="mt-1 font-body text-[13px] text-alert">
                  That doesn&rsquo;t look like a full email address.
                </p>
              ) : null}
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
          </div>
        </div>

        {/* Step 3. The two answers the quotation is actually built from, on
            one panel because a budget read apart from the scope it belongs to
            is a number with nothing to mean. */}
        <div className={step === 2 ? '' : 'hidden'}>
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
              aria-invalid={attemptedSteps.has(2) && scopeMissing}
              // The character-count hint only ever renders once there is text,
              // and the error only ever renders while there is none - the two
              // are mutually exclusive by construction, never both at once.
              aria-describedby={
                trimmedScope
                  ? 'client-scope-hint'
                  : attemptedSteps.has(2) && scopeMissing
                    ? 'client-scope-error'
                    : undefined
              }
              // Shorter than `WELL_TEXTAREA`'s own 184px floor, which is sized
              // for the pad's brief - a studio writing a full scope from notes.
              // A client writing a paragraph needs less. It still grows: the
              // box is resizable and scrolls past this.
              //
              // The `!` is a SUFFIX. Tailwind v4 moved the important modifier to
              // the end of the class, and `!min-h-[116px]` is not a v3 spelling
              // that still works - it matches no utility at all, so it compiles
              // to nothing and leaves the 184px floor in place with no warning.
              className={`${WELL_TEXTAREA} pad-brief min-h-[116px]!`}
            />
            {trimmedScope ? (
              <p
                id="client-scope-hint"
                className="mt-1.5 font-label text-[12px] uppercase tracking-[0.14em] tabular-nums text-void"
              >
                {trimmedScope.length} character{trimmedScope.length === 1 ? '' : 's'}
              </p>
            ) : null}
            {attemptedSteps.has(2) && scopeMissing ? (
              <p id="client-scope-error" className="mt-1.5 font-body text-[13px] text-alert">
                Describe the work before sending.
              </p>
            ) : null}
          </div>

          <div className="mt-4">
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
              aria-invalid={attemptedSteps.has(2) && budgetMissing}
              aria-describedby={
                attemptedSteps.has(2) && budgetMissing
                  ? 'client-budget-hint client-budget-error'
                  : 'client-budget-hint'
              }
              className={WELL}
            />
            <p id="client-budget-hint" className="mt-1.5 font-body text-[13px] leading-[1.5] text-void">
              This helps {studio} shape the quotation to fit what you have in mind. It doesn&rsquo;t
              set your price, and you&rsquo;re not held to it.
            </p>
            {attemptedSteps.has(2) && budgetMissing ? (
              <p id="client-budget-error" className="mt-1 font-body text-[13px] text-alert">
                Give a rough budget before sending.
              </p>
            ) : null}
          </div>
        </div>

        {/* Step 4. Last, and optional, which is why it is here rather than
            beside the scope it belongs to conceptually: a client with nothing
            to attach walks past it, and a client who has a brief on disk finds
            it at the moment they have finished describing the work and are
            looking for the Send button. Its own refusals render inside it,
            under the plate - a file this door will not take never touches the
            four answers before it, which is the whole reason the picker owns
            that state instead of raising it to this form. */}
        <div className={step === LAST_STEP ? '' : 'hidden'}>
          <FieldLabel htmlFor="client-files">
            Attachments <span className="normal-case tracking-normal text-faint">(optional)</span>
          </FieldLabel>
          <ClientDropzone id="client-files" onChange={handleFiles} disabled={pending} />
        </div>
        </div>

        {showSummary ? (
          // `role="status"` (polite), not `role="alert"` (implicitly
          // assertive) - this box stays mounted for as long as anything is
          // still missing and its text is recomputed on every render, so an
          // assertive region here re-announces the whole remaining list on
          // every keystroke that changes it, forcibly interrupting a screen
          // reader's own character echo of what is currently being typed.
          // `role="alert"` was right for the per-attempt announcement this
          // is meant to be, wrong for a region that keeps mutating live
          // while the user is still typing - the two are the same box, but
          // not the same lifetime.
          <div
            ref={summaryRef}
            tabIndex={-1}
            role="status"
            className="focus-landing rounded-lg border border-alert/40 bg-paper px-4 py-3"
          >
            <p className="font-body text-[14px] leading-[1.5] text-ink">
              {step === LAST_STEP
                ? `Before this can be sent: ${stepMissing.join(', ')}.`
                : `Before the next step: ${stepMissing.join(', ')}.`}
            </p>
          </div>
        ) : null}

        {error ? (
          <ErrorNotice headline={error.headline} next={error.next} onDismiss={() => setError(null)} />
        ) : null}

        <div className="mt-1 flex items-center gap-3">
          {step > 0 ? (
            <button
              type="button"
              onClick={() => goTo(step - 1)}
              aria-disabled={pending}
              className={`${ACTION} ${pending ? 'cursor-wait opacity-70' : ''}`}
            >
              Back
            </button>
          ) : null}

          {/* One submit button for both jobs, and that is what keeps the
              Enter key working: a form with a single submit control fires it
              on Enter from any input, so `handleSubmit` deciding between
              "advance" and "send" means Enter does on each step exactly what
              the button on it does. A `type="button"` Next would have left
              Enter firing the real submit from step one.

              No `disabled` - see this file's own docstring. `aria-disabled`
              says the same thing to assistive tech without taking the element
              out of the accessibility tree, `handleSubmit`'s `if (pending)
              return` is what actually refuses the second press, and the
              opacity is a plain utility rather than `.action-primary:disabled`,
              which no longer matches. */}
          <button
            type="submit"
            aria-disabled={pending}
            className={`${ACTION_PRIMARY} flex-1 justify-center ${
              pending ? 'cursor-wait opacity-70' : ''
            }`}
          >
            {step < LAST_STEP ? 'Next' : pending ? 'Sending' : `Send to ${studio}`}
          </button>
        </div>

        {/* Mounted always, with text that changes - not mounted when the
            upload starts. The distinction is the difference between a region
            that announces and one that does not: this form already knows
            that its validation summary "announces only because focus moves
            to it," and there is no focus move to spare here, because the
            whole point is that focus stays on the button. A live region that
            is already in the document when its content changes is the case
            every screen reader actually handles.

            The margin is conditional rather than the element, so the empty
            paragraph contributes no height on a screen this form is measured
            to fit, while still being present in the DOM to be announced. */}
        <p
          role="status"
          className={`font-body text-[13px] leading-[1.5] text-void ${pending ? 'mt-1' : ''}`}
        >
          {pending
            ? attachedCount > 0
              ? `Sending your answers and ${attachedCount} file${
                  attachedCount === 1 ? '' : 's'
                } to ${studio}. Files can take a minute on a slow connection — this page will move on by itself.`
              : `Sending your answers to ${studio}.`
            : ''}
        </p>
      </form>
    </div>
  )
}
