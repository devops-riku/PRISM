import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createIntake } from '../lib/api'
import { formatDate } from '../lib/format'
import { useRole } from '../lib/role'
import { padDefaults } from './BriefForm'
import CurrencySelect from './CurrencySelect'
import Dropdown from './Dropdown'
import { FieldLabel } from './FieldRow'
import { ACTION, ACTION_PRIMARY, CARD, DISPLAY, MONO_LABEL, WELL } from './tokens'
import type {
  IntakeIssued,
  IntakePreset,
  PaymentCadence,
  PricingBasis,
  StudioDefaults,
  TaxMode,
} from '../types'

/**
 * Setting up a client request, and handing over the link it lives behind.
 *
 * This screen used to type the client's four fields on their behalf. It does
 * not any more: from Stage 2 the client writes their own scope and their own
 * budget through the link generated here, so what is left is everything about
 * the quotation that is the studio's to decide before anybody has said
 * anything - what currency and market it is priced in, how tax is handled,
 * what the payment terms are, and whether it is quoted at tiers.
 *
 * What kind of work it is used to be asked here too, and is not any more: that
 * one is the client's answer, given on their own form, because a client
 * commissioning an audit knows they are commissioning an audit and a studio
 * holding a paragraph is guessing. The preset still carries a kind as the
 * opening for an intake nobody has filled in yet; `App.tsx`'s `readPreset`
 * prefers the client's the moment there is one.
 *
 * That configuration is stored on the request as its preset and read back the
 * moment somebody opens the pad from the queue, so a studio that sets its
 * terms here does not set them twice. The exact key set is `IntakePreset` in
 * `types.ts`, and `App.tsx`'s `readPreset` is the other half of it - a key
 * written here that nothing reads back is a control that silently does
 * nothing. Both halves are compiler-checked (the preset built below is a whole
 * `IntakePreset`; `readPreset` returns a `PresetRead` requiring every key), but
 * the last step - `BriefForm` actually calling a setter for what it receives -
 * is a rule rather than a type, so a new key has to be followed through all
 * three files by hand.
 *
 * Every field is written on every Generate, empty or not, because empty is a
 * configuration: clearing the instalment box says something different from
 * leaving it at three, and the reader carries a present-but-empty string
 * verbatim so the pad opens on what was actually set.
 *
 * **The link is shown once.** `Intake.token` is excluded from the wire on
 * purpose, so the queue cannot look one up and there is no route that will;
 * `createIntake` and `relinkIntake` are the only two calls that ever carry a
 * link, and each carries it exactly once, in the response that mints it. So
 * generating does not navigate anywhere - it stays here and shows the link,
 * because leaving would throw away the only copy of it there will ever be.
 *
 * Admin-only, like `createIntake` itself: generating a link opens the queue
 * whoever prices it next works from, which is nearer to inviting somebody than
 * to drafting a quotation. A member is shown why instead of a form the server
 * would refuse.
 */

type IntakeScreenProps = {
  /** The studio's saved defaults. The preset opens on them for the same
   *  reason the pad does - a studio that quotes in USD should not have to
   *  correct a form that guessed PHP - and `App.tsx` gates this screen on
   *  them having arrived, so there is no window where they land late and the
   *  fields below have already been read. */
  defaults: Partial<StudioDefaults>
}

export default function IntakeScreen({ defaults }: IntakeScreenProps) {
  const { isAdmin } = useRole()

  // Every field below is one key of `IntakePreset`, named the way the pad
  // names it rather than the way the wire does - `submit` is the one place the
  // two vocabularies meet.
  //
  // Opened from `padDefaults`, which is the pad's own answer to the same
  // question, called here rather than restated. Two copies of these eleven
  // values would agree on the day they were written and then drift, and this
  // screen writes every key unconditionally - so a stale default here would be
  // pinned into every new intake permanently and would then override the pad
  // that had stopped believing it.
  const opening = padDefaults(defaults)

  // No state for `kind` and `kind_label`: this screen no longer asks. They are
  // still written below, straight off `padDefaults`, as the opening the pad
  // gets for an intake whose client has not answered yet - a value nobody has
  // chosen yet rather than one this screen pretends to have chosen.
  const [currency, setCurrency] = useState(opening.currency)
  const [marketRegion, setMarketRegion] = useState(opening.market_region)
  const [taxMode, setTaxMode] = useState<TaxMode>(opening.tax_mode)
  const [pricingBasis, setPricingBasis] = useState<PricingBasis>(opening.pricing_basis)
  const [depositPct, setDepositPct] = useState(opening.deposit_pct)
  const [instalments, setInstalments] = useState(opening.instalments)
  const [cadence, setCadence] = useState<PaymentCadence>(opening.payment_cadence)
  const [depositTrigger, setDepositTrigger] = useState(opening.deposit_trigger)
  const [tiers, setTiers] = useState(opening.tiers)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // The whole reason this screen does not navigate on success. Non-null means
  // a link exists on screen and nowhere else.
  const [issued, setIssued] = useState<IntakeIssued | null>(null)
  const [copyNote, setCopyNote] = useState('')

  /**
   * Generating swaps the form out for the link panel, and swapping back is the
   * same move in reverse. React gives no signal of its own that either
   * happened: focus was on a button that no longer exists, so it falls to
   * `<body>`, and nothing announces the new content.
   *
   * The same treatment Task 8's four client faces give themselves, and for a
   * sharper reason here - `role="status"` on the panel so it is announced as
   * soon as it appears whether or not anything is focused, plus moving focus
   * to its heading, which is the more reliable of the two on assistive tech
   * that reads focus moves better than live regions. Belt and suspenders, as
   * `ClientWaiting`'s own docstring puts it. This is the one screen in the app
   * where a missed announcement is unrecoverable: what appears is the only
   * copy of a link that will ever exist.
   *
   * Known and accepted: `role="status"` implies `aria-atomic="true"`, so a
   * reader announcing the panel on arrival reads the token aloud with it.
   * That is the house pattern, and a link nobody is told about is worse.
   *
   * The first run is skipped. Arriving at this screen is a navigation, not a
   * swap, and stealing focus from the top of a form somebody just opened is a
   * different and unwelcome behaviour.
   */
  const issuedHeadingRef = useRef<HTMLHeadingElement | null>(null)
  const formHeadingRef = useRef<HTMLHeadingElement | null>(null)
  const swapped = useRef(false)
  useEffect(() => {
    if (!swapped.current) {
      swapped.current = true
      return
    }
    const landing = issued ? issuedHeadingRef : formHeadingRef
    landing.current?.focus()
  }, [issued])

  // Distinct, non-empty names. One name is a label, not a tier - and the pad
  // refuses to prepare a quotation from one, so a preset carrying one would
  // generate a link whose pad opens already blocked.
  const tierNames = [
    ...new Set(
      tiers
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean),
    ),
  ]
  const blocker =
    tierNames.length === 1 ? 'Name a second tier, or clear the field to quote one price.' : ''

  const deposit = Number(depositPct) || 0

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy || blocker) return
    setBusy(true)
    setError('')
    setCopyNote('')

    // Typed as `IntakePreset` rather than assembled loosely: this object and
    // `App.tsx`'s `readPreset` are the two ends of one agreement, and the type
    // is what keeps a renamed key from quietly becoming a setting nobody
    // applies. Deliberately absent - the target cost, the tier cap, and a
    // written per-payment schedule - because each depends on what the client
    // actually asked for, and at this moment they have not asked yet.
    const preset: IntakePreset = {
      kind: opening.kind,
      kind_label: opening.kind_label,
      currency,
      market_region: marketRegion.trim(),
      tax_mode: taxMode,
      pricing_basis: pricingBasis,
      deposit_pct: depositPct.trim(),
      instalments: instalments.trim(),
      payment_cadence: cadence,
      deposit_trigger: depositTrigger.trim(),
      tiers: tiers.trim(),
    }

    createIntake({ preset })
      .then((made) => setIssued(made))
      .catch((failure) => setError(failure?.message || 'That link was not generated.'))
      .finally(() => setBusy(false))
  }

  /**
   * Put the link on the clipboard, and say so in words.
   *
   * `navigator.clipboard` needs a secure context, so it is simply absent over
   * plain HTTP on anything but localhost. Checked rather than optional-chained:
   * `clipboard?.writeText(...).then(...)` short-circuits the whole chain, which
   * means a studio on a LAN address would press Copy, see nothing happen, and
   * have no idea why. The field above is readable and selectable either way -
   * that is the fallback, and this says so.
   */
  const copy = (link: string) => {
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
    <div className="flex min-h-0 flex-1 flex-col">
      <section className={`${CARD} flex min-h-0 flex-1 flex-col`}>
        <div className="shrink-0 border-b border-rule px-5 py-3 sm:px-6">
          <h2 className={`${DISPLAY} text-[20px]`}>New client request</h2>
          <p className="mt-2 max-w-[64ch] font-body text-[13px] leading-[1.6] text-void">
            {issued
              ? 'The request is on the queue. Send the client their link.'
              : 'Set how this one will be priced, then generate a link for the client to fill in.'}
          </p>
        </div>

        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          {!isAdmin ? (
            <div className="max-w-[60ch]">
              <p className={MONO_LABEL}>Not yours to generate</p>
              <p className="mt-2 font-body text-[15px] leading-[1.6] text-void">
                A client link opens the queue whoever prices it next works from, so the server keeps
                this to this workspace&rsquo;s admins. Ask one of them to generate it, or open the
                queue to see what has already come in.
              </p>
              <a href="#/intakes" className="mt-4 inline-block font-body text-[14px] text-ballpoint">
                Open the queue
              </a>
            </div>
          ) : issued ? (
            <div role="status" className="max-w-[40rem]">
              <p className={MONO_LABEL}>Link generated</p>
              <h3
                ref={issuedHeadingRef}
                tabIndex={-1}
                className={`${DISPLAY} focus-landing mt-2 text-[17px]`}
              >
                Send this link to the client.
              </h3>
              <p className="mt-2 max-w-[58ch] font-body text-[15px] leading-[1.6] text-ink">
                They open it with no account, describe what they need in their own words, and the
                request comes back to the queue priced the way you just set it.
              </p>

              <div className="mt-4">
                <FieldLabel htmlFor="intake_link">The client&rsquo;s link</FieldLabel>
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    id="intake_link"
                    type="text"
                    readOnly
                    value={issued.link}
                    onFocus={(event) => event.currentTarget.select()}
                    className={`${WELL} min-w-[18rem] flex-1 font-label text-[13px]`}
                  />
                  <button type="button" className={ACTION} onClick={() => copy(issued.link)}>
                    Copy link
                  </button>
                </div>
                {/* Its own live region inside the panel's, deliberately: a
                    nested one owns its own subtree, so pressing Copy announces
                    this line alone rather than re-reading the whole atomic
                    panel - token included - every time. */}
                <p role="status" className="mt-2 font-body text-[13px] leading-[1.6] text-ballpoint">
                  {copyNote}
                </p>
              </div>

              <p className="mt-3 max-w-[58ch] font-body text-[13px] leading-[1.6] text-void">
                Copy it now: this is the only time it is shown. The queue does not carry client
                links and nothing can look one up, so a lost link is replaced by reissuing it &mdash;
                which stops the link you just generated from working.
                {issued.token_expires_at
                  ? ` This one works until ${formatDate(issued.token_expires_at)}.`
                  : ''}
              </p>

              <div className="mt-5 flex flex-wrap items-center gap-3">
                <a href="#/intakes" className={ACTION_PRIMARY}>
                  Open the queue
                </a>
                <button
                  type="button"
                  className={ACTION}
                  onClick={() => {
                    setIssued(null)
                    setCopyNote('')
                  }}
                >
                  Generate another
                </button>
              </div>
              <p className="mt-2 font-body text-[12.5px] leading-[1.6] text-faint">
                Generating another keeps this configuration and leaves this link behind. Copy it
                first.
              </p>
            </div>
          ) : (
            <form onSubmit={submit} className="max-w-[46rem]">
              {/* "Kind of work" used to be the first thing asked here, on the
                  reasoning that it was the studio's reading of the job rather
                  than the client's answer. It is the client's question and it
                  now lives on their form - see `ClientForm`'s own docstring.
                  The preset still carries a kind, because a studio can open
                  the pad on an `issued` intake before anybody has filled that
                  form in and the studio's default is a better opening than
                  nothing; `App.tsx`'s `readPreset` prefers the client's answer
                  the moment there is one. */}
              <section>
                {/* Where focus lands coming back from the link panel, so
                    "Generate another" is as announced as generating was. */}
                <h3
                  ref={formHeadingRef}
                  tabIndex={-1}
                  className={`${DISPLAY} focus-landing text-[17px]`}
                >
                  How it is priced
                </h3>
                <p className="mt-1 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                  Rates are set for the market below and quoted straight into the currency. Nothing
                  is converted.
                </p>
                <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                  <div>
                    <FieldLabel htmlFor="intake_currency">Currency</FieldLabel>
                    <CurrencySelect
                      id="intake_currency"
                      value={currency}
                      onChange={setCurrency}
                      disabled={busy}
                    />
                  </div>
                  <div>
                    <FieldLabel htmlFor="intake_market">Market</FieldLabel>
                    <input
                      id="intake_market"
                      type="text"
                      value={marketRegion}
                      disabled={busy}
                      onChange={(event) => setMarketRegion(event.target.value)}
                      placeholder="Philippines"
                      className={WELL}
                    />
                  </div>
                  <div>
                    <FieldLabel htmlFor="intake_tax">Tax basis</FieldLabel>
                    <Dropdown
                      id="intake_tax"
                      value={taxMode}
                      disabled={busy}
                      onChange={setTaxMode}
                      options={[
                        { value: 'exclusive', label: 'Exclusive', hint: 'Tax added on top' },
                        { value: 'inclusive', label: 'Inclusive', hint: 'Tax already in the rates' },
                        { value: 'none', label: 'No tax', hint: 'Zero-rated or exempt' },
                      ]}
                    />
                  </div>
                  <div>
                    <FieldLabel htmlFor="intake_basis">Quoted from</FieldLabel>
                    <Dropdown
                      id="intake_basis"
                      value={pricingBasis}
                      disabled={busy}
                      onChange={setPricingBasis}
                      options={[
                        { value: 'rate_card', label: 'Your rate card' },
                        { value: 'requirements', label: 'The requirements, at market rates' },
                      ]}
                    />
                  </div>
                </div>
                <p className="mt-3 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                  {pricingBasis === 'rate_card'
                    ? 'Roles and rates come from the card in Settings, and are enforced. With no card configured this falls back to market rates.'
                    : 'The card is ignored. PRISM scopes the roles it needs and quotes them at market rates for the chosen market.'}
                </p>
              </section>

              <section className="mt-6 border-t border-hairline pt-5">
                <h3 className={`${DISPLAY} text-[17px]`}>Payment terms</h3>
                <p className="mt-1 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                  Your standard terms, which the client reads on the quotation. A schedule written
                  payment by payment is set on the pad instead, once the phases are known.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 font-body text-[15px]">
                  <label htmlFor="intake_deposit" className="sr-only">
                    Deposit percentage
                  </label>
                  <input
                    id="intake_deposit"
                    inputMode="decimal"
                    autoComplete="off"
                    value={depositPct}
                    disabled={busy}
                    onChange={(event) => setDepositPct(event.target.value.replace(/[^\d.]/g, ''))}
                    placeholder="0"
                    className={`${WELL} w-[104px] text-right tabular-nums`}
                  />
                  <span>% on signing, then the balance in</span>
                  <label htmlFor="intake_instalments" className="sr-only">
                    Number of equal payments
                  </label>
                  <input
                    id="intake_instalments"
                    inputMode="numeric"
                    autoComplete="off"
                    value={instalments}
                    disabled={busy}
                    onChange={(event) => setInstalments(event.target.value.replace(/[^\d]/g, ''))}
                    className={`${WELL} w-[104px] text-right tabular-nums`}
                  />
                  <span>equal payments, due</span>
                  <Dropdown
                    id="intake_cadence"
                    aria-label="When each payment falls due"
                    value={cadence}
                    disabled={busy}
                    onChange={setCadence}
                    className="w-[200px]"
                    options={[
                      { value: 'monthly', label: 'monthly' },
                      { value: 'phase', label: 'on each phase' },
                      { value: 'milestone', label: 'on agreed milestones' },
                    ]}
                  />
                </div>

                {deposit > 0 ? (
                  <div className="mt-4">
                    <FieldLabel htmlFor="intake_trigger">
                      The deposit becomes payable on
                    </FieldLabel>
                    <input
                      id="intake_trigger"
                      type="text"
                      value={depositTrigger}
                      disabled={busy}
                      onChange={(event) => setDepositTrigger(event.target.value)}
                      placeholder="Signed statement of work"
                      className={`${WELL} max-w-[420px]`}
                    />
                  </div>
                ) : null}

                <p className="mt-3 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                  {deposit > 0
                    ? `${deposit}% on signing, then the balance in ${instalments || 1} equal payments.`
                    : 'Leave the deposit at 0 and PRISM proposes a schedule for this quotation.'}
                </p>
              </section>

              <section className="mt-6 border-t border-hairline pt-5">
                <h3 className={`${DISPLAY} text-[17px]`}>Tiers</h3>
                <p className="mt-1 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                  Optional. Name two or more levels and PRISM quotes the same scope at each, as
                  separate quotations. The cap that holds them down is set on the pad, where the
                  scope is on screen beside it.
                </p>
                <div className="mt-3 max-w-[420px]">
                  <label htmlFor="intake_tiers" className="sr-only">
                    Tier names, separated by commas
                  </label>
                  <input
                    id="intake_tiers"
                    type="text"
                    value={tiers}
                    disabled={busy}
                    onChange={(event) => setTiers(event.target.value)}
                    placeholder="Basic, Standard, Extended"
                    aria-describedby="intake_tiers_help"
                    className={WELL}
                  />
                </div>
                <p
                  id="intake_tiers_help"
                  className={`mt-2 max-w-[62ch] font-body text-[13px] leading-[1.6] ${
                    blocker ? 'text-alert' : 'text-void'
                  }`}
                >
                  {blocker ||
                    (tierNames.length > 1
                      ? `${tierNames.length} quotations from one scope — ${tierNames.join(', ')} — each held below the tier above it.`
                      : 'Leave it empty for one quotation.')}
                </p>
              </section>

              <div className="mt-6 border-t border-hairline pt-5">
                <button
                  type="submit"
                  disabled={busy || Boolean(blocker)}
                  aria-disabled={busy || Boolean(blocker)}
                  className={ACTION_PRIMARY}
                >
                  {busy ? 'Generating' : 'Generate link'}
                </button>
                <p className="mt-2 max-w-[58ch] font-body text-[13px] leading-[1.6] text-void">
                  The link appears here, once. Nothing is emailed &mdash; you send it to the client
                  yourself.
                </p>
              </div>

              {error ? (
                <p role="alert" className="mt-4 font-body text-[14px] text-alert">
                  {error}
                </p>
              ) : null}
            </form>
          )}
        </div>
      </section>
    </div>
  )
}
