import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { FieldLabel } from './FieldRow'
import ImageDropzone from './ImageDropzone'
import CurrencySelect from './CurrencySelect'
import Dropdown from './Dropdown'
import KindPicker, { KINDS } from './KindPicker'
import StepRail from './StepRail'
import type { RailStep } from './StepRail'
import SubmitTicker, { useElapsed } from './SubmitTicker'
import JobStrip from './JobStrip'
import { ACTION, ACTION_PRIMARY, WELL, WELL_TEXTAREA } from './tokens'
import { groupedInputHandler } from '../lib/format'
import type {
  Job,
  PaymentCadence,
  PricingBasis,
  QuotationKind,
  StudioDefaults,
  TaxMode,
} from '../types'

/**
 * The pad: everything the API accepts, five steps, one screen.
 *
 * It was one long column of numbered rows, which meant the scope and the button
 * that prices it were never visible at the same time — you filled a field, lost
 * your place, and scrolled to find out whether anything was still missing. The
 * same fields are now grouped into steps with the rail beside them, so the form
 * ends where the viewport does and nothing is more than one click away.
 *
 * The order is a suggestion, not a gate. Only the scope is required, so every
 * step stays reachable from the rail at any point — including after you have
 * moved past it, which is the whole reason the rail carries the answers.
 */

const STEP_IDS = ['kind', 'brief', 'client', 'pricing', 'payments', 'tiers', 'prepare'] as const
const LAST = STEP_IDS.length - 1

/** The seven above, as a type: the rail, the panel and the recap all name them. */
type StepId = (typeof STEP_IDS)[number]

/** One step as the pad builds it - the rail's shape, carrying this pad's own ids. */
type PadStep = RailStep & { id: StepId }

/**
 * One row of a written schedule while it is being typed. The share is text
 * rather than a number so a field can be emptied mid-edit; `submit` is where it
 * becomes the number the server records.
 */
type PaymentRow = {
  percent: string
  trigger: string
}

/**
 * What the schedule currently says, and whether it can be sent. `total` is
 * counted for a written schedule only - the equal-payment branches have nothing
 * to add up, and leave it absent rather than reporting a zero.
 */
type SchedulePreview = {
  active: boolean
  blocks: boolean
  total?: number
  text: string
}

type BriefFormProps = {
  /** The studio's saved defaults, or `{}` when they could not be read. */
  defaults?: Partial<StudioDefaults>
  pending: boolean
  job: Job | null
  onSubmit: (form: FormData) => void
}

export default function BriefForm({ defaults = {}, pending, job, onSubmit }: BriefFormProps) {
  // Asked first because it shapes everything after it - the words the model
  // writes in, and the whole second document. Software is the default because
  // it is what PRISM has always quoted, so a studio that only does one thing
  // never has to think about this step.
  const [kind, setKind] = useState<QuotationKind>('software')
  const [kindLabel, setKindLabel] = useState('')
  const [brief, setBrief] = useState('')
  const [currency, setCurrency] = useState(defaults.currency || 'PHP')
  const [marketRegion, setMarketRegion] = useState(defaults.market_region || 'Philippines')
  const [clientName, setClientName] = useState('')
  const [projectName, setProjectName] = useState('')
  const [budgetHint, setBudgetHint] = useState('')
  const [timelineHint, setTimelineHint] = useState('')
  const [targetTotal, setTargetTotal] = useState('')
  // exclusive | inclusive | none. The studio default may still be the older
  // boolean, so the mode falls back to it rather than to a hard-coded guess.
  const [taxMode, setTaxMode] = useState<TaxMode>(
    defaults.tax_mode || (defaults.tax_inclusive ? 'inclusive' : 'exclusive'),
  )
  const [depositPct, setDepositPct] = useState('')
  const [instalments, setInstalments] = useState('3')
  const [cadence, setCadence] = useState<PaymentCadence>('monthly')
  const [depositTrigger, setDepositTrigger] = useState('Signed statement of work')
  const [termsMode, setTermsMode] = useState<'equal' | 'custom'>('equal')
  const [rows, setRows] = useState<PaymentRow[]>([
    { percent: '40', trigger: 'Signed statement of work' },
    { percent: '30', trigger: 'Staging accepted' },
    { percent: '30', trigger: 'Production go-live' },
  ])
  const [tiers, setTiers] = useState('')
  const [ceiling, setCeiling] = useState('')
  const [pricingBasis, setPricingBasis] = useState<PricingBasis>('rate_card')
  const [images, setImages] = useState<File[]>([])
  const [step, setStep] = useState(0)

  const clock = useElapsed(pending)
  const trimmedBrief = brief.trim()

  // Pictures and documents arrive on one plate and leave on two fields: an
  // image is sent to the model as an image, a document is read to text on the
  // server and quoted into the brief. Splitting them here keeps that difference
  // where the file is chosen rather than three layers in.
  const handleImages = useCallback((files: File[]) => setImages(files), [])
  const documentish = (file: File) =>
    !(file.type || '').startsWith('image/') &&
    /\.(pdf|docx|xlsx|xlsm|csv|txt|md)$/i.test(file.name)

  const setRow = (index: number, patch: Partial<PaymentRow>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  // A percentage split is the kind of input that looks obviously right and
  // divides into 33.33, 33.33, 33.33 — so the pad does the arithmetic before the
  // client ever sees it. A written schedule is checked rather than corrected:
  // 95% is a typo to point at, not a number to quietly move.
  const schedulePreview: SchedulePreview = (() => {
    if (termsMode === 'custom') {
      const shares = rows.map((row) => Number(row.percent) || 0)
      const total = Math.round(shares.reduce((sum, value) => sum + value, 0) * 100) / 100
      const exact = Math.abs(total - 100) < 0.01
      return {
        active: false,
        blocks: !exact,
        total,
        text: exact
          ? `${shares.length} payments of ${shares.map((v) => `${v}%`).join(' · ')} — totalling 100%.`
          : `These payments total ${total}%. Adjust them to 100% before preparing the quotation.`,
      }
    }

    const deposit = Number(depositPct) || 0
    const count = Math.max(1, Math.min(24, Math.round(Number(instalments) || 1)))
    if (deposit <= 0) {
      return {
        active: false,
        blocks: false,
        text: 'Leave the deposit at 0 and PRISM proposes a schedule for this quotation.',
      }
    }
    const balance = 100 - deposit
    const share = Math.round((balance / count) * 100) / 100
    const last = Math.round((balance - share * (count - 1)) * 100) / 100
    const parts = [`${deposit}%`]
    for (let i = 0; i < count; i += 1) parts.push(`${i === count - 1 ? last : share}%`)
    return {
      active: true,
      blocks: false,
      text: `Scheduled as ${parts.join(' · ')} — ${parts.length} payments summing to 100%.`,
    }
  })()

  // Distinct, non-empty names. One name is a label, not a tier.
  const tierNames = [
    ...new Set(
      tiers
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean),
    ),
  ]

  // A ceiling below the target asks for a figure the quotation is not allowed
  // to reach. The server refuses it too; catching it here costs no Gemini call.
  const ceilingConflict = (() => {
    const cap = Number(ceiling.replace(/[^\d.]/g, '')) || 0
    const goal = Number(targetTotal.replace(/[^\d.]/g, '')) || 0
    return cap > 0 && goal > 0 && goal > cap
  })()

  // Every figure the studio types is read on the same basis as the rates. Quote
  // exclusive and the tax lands on top of the target and the cap alike, which is
  // what "exclusive" means everywhere else on the quotation - so the fields say
  // it rather than leaving somebody to discover it in the total.
  const taxNote =
    taxMode === 'exclusive' ? 'before tax' : taxMode === 'inclusive' ? 'tax included' : 'no tax'

  // What the cap actually does, said in terms of the tiers on screen. It is a
  // maximum and never a target: a quotation that already fits is left where it
  // is, because padding scope up to a budget is how a client ends up paying for
  // work nobody chose.
  const ceilingHint = (() => {
    const cap = ceiling.trim()
    if (!cap) {
      return tierNames.length > 1
        ? 'Optional. Set a cap and the top tier lands at or under it, with every tier below quoted under the one above.'
        : 'Optional. A price this quotation may not exceed. It only ever brings a quotation down.'
    }
    if (ceilingConflict) {
      return `The cap of ${cap} ${currency} is below the target cost. Raise the cap or lower the target.`
    }
    if (tierNames.length > 1) {
      const top = tierNames[tierNames.length - 1]
      return `${top} lands at or under ${cap} ${currency} ${taxNote}. ${tierNames.slice(0, -1).join(' and ')} are quoted under it, in order. Nothing is padded up to the cap.`
    }
    return `This quotation lands at or under ${cap} ${currency} ${taxNote}. If it comes in below, it stays there.`
  })()

  const ready =
    trimmedBrief.length > 0 && !schedulePreview.blocks && tierNames.length !== 1 && !ceilingConflict

  // What the rail says under each step. The answer, not the question — a rail
  // that repeats its own labels tells the reader nothing they cannot already
  // see.
  const chosenKind = KINDS.find((entry) => entry.id === kind) || KINDS[0]

  const steps: PadStep[] = [
    {
      id: 'kind',
      label: 'Kind of work',
      placeholder: '',
      answer:
        kind === 'other' ? kindLabel.trim() || 'Something else' : chosenKind.label,
    },
    {
      id: 'brief',
      label: 'Scope',
      placeholder: 'Nothing yet — the one thing required',
      answer: trimmedBrief
        ? `${trimmedBrief.length} characters${images.length ? ` · ${images.length} file${images.length === 1 ? '' : 's'}` : ''}`
        : '',
    },
    {
      id: 'client',
      label: 'Who it is for',
      placeholder: 'Optional',
      answer: [clientName.trim(), projectName.trim()].filter(Boolean).join(' · '),
    },
    {
      id: 'pricing',
      label: 'Target Cost',
      placeholder: '',
      answer: [
        currency,
        taxMode === 'none' ? 'no tax' : taxMode,
        targetTotal.trim() ? `target ${targetTotal.trim()}` : '',
      ]
        .filter(Boolean)
        .join(' · '),
    },
    {
      id: 'payments',
      label: 'Payment terms',
      placeholder: '',
      answer: schedulePreview.blocks
        ? `${schedulePreview.total}% — does not total 100`
        : termsMode === 'custom'
          ? `${rows.length} written payments`
          : Number(depositPct) > 0
            ? `${Number(depositPct)}% deposit, then ${instalments || 1} ${cadence === 'monthly' ? 'monthly' : cadence}`
            : 'PRISM proposes a schedule',
    },
    {
      id: 'tiers',
      label: 'Tiers and cap',
      placeholder: 'One quotation, no cap',
      answer: [
        tierNames.length > 1 ? tierNames.join(', ') : '',
        ceiling.trim() ? `cap ${ceiling.trim()}` : '',
      ]
        .filter(Boolean)
        .join(' · '),
    },
    {
      id: 'prepare',
      label: 'Prepare quotation',
      // No answer and no placeholder: it is where the flow ends, not another
      // question with something to remember about it.
      placeholder: '',
      answer: '',
    },
  ]

  const blocker = schedulePreview.blocks
    ? 'The payment schedule has to total 100% before this can be prepared.'
    : ceilingConflict
      ? 'The cap is below the target cost. Raise one or lower the other.'
      : tierNames.length === 1
        ? 'Name a second tier, or clear the field to prepare one quotation.'
        : !trimmedBrief
          ? 'A scope is required before anything can be quoted.'
          : ''

  const submit = () => {
    if (!ready || pending) return

    const form = new FormData()
    form.append('kind', kind)
    // Only meaningful for 'other', and the server ignores it otherwise.
    form.append('kind_label', kindLabel.trim())
    form.append('brief', trimmedBrief)
    form.append('currency', currency)
    form.append('client_name', clientName.trim())
    form.append('project_name', projectName.trim())
    form.append('market_region', marketRegion.trim())
    form.append('budget_hint', budgetHint.trim())
    form.append('timeline_hint', timelineHint.trim())
    form.append('target_total', targetTotal.trim())
    form.append('tax_mode', taxMode)
    // Sent as well as the mode: the server reconciles the two, and the flag is
    // what the estimate itself carries.
    form.append('tax_inclusive', taxMode === 'inclusive' ? 'true' : 'false')
    form.append('deposit_pct', depositPct.trim())
    form.append('instalments', instalments.trim())
    form.append('payment_cadence', cadence)
    form.append('deposit_trigger', depositTrigger.trim())
    form.append(
      'payment_schedule',
      termsMode === 'custom'
        ? JSON.stringify(
            rows
              .map((row) => ({
                percent: Number(row.percent) || 0,
                trigger: row.trigger.trim(),
              }))
              .filter((row) => row.percent > 0),
          )
        : '',
    )
    form.append('tiers', tiers.trim())
    form.append('tier_ceiling', ceiling.trim())
    form.append('pricing_basis', pricingBasis)
    images.forEach((file) =>
      form.append(documentish(file) ? 'documents' : 'images', file, file.name),
    )

    onSubmit(form)
  }

  // Enter inside a field advances rather than submits. Pricing a quotation from
  // step two because somebody pressed Enter in the client name would be a
  // Gemini pass nobody asked for.
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending) return
    if (step < LAST) {
      setStep(step + 1)
      return
    }
    submit()
  }

  // What the last step reads back. The rail's own answers, minus the step the
  // reader is standing on - a summary that includes "Prepare" tells nobody
  // anything.
  const recap = steps.filter((entry) => entry.id !== 'prepare' && entry.answer)

  return (
    <form onSubmit={handleSubmit} noValidate className="flex min-h-0 flex-1 flex-col">
      <div className="grid min-h-0 flex-1 grid-cols-1 sm:grid-cols-[minmax(0,204px)_minmax(0,1fr)]">
        <div className="no-scrollbar min-h-0 overflow-y-auto border-b border-rule bg-duplicate/40 px-3 py-4 sm:border-b-0 sm:border-r sm:px-3">
          <StepRail steps={steps} current={step} onGo={setStep} disabled={pending} />
        </div>

        {/* The card is a fixed height, so this panel is the only thing that can
            overflow — and it does so without a bar. A scrollbar appearing on
            one step and not another is the card changing shape by another name.
            Wheel, trackpad, arrow keys and Tab all still reach the bottom of a
            long payment schedule. */}
        <div
          key={STEP_IDS[step]}
          className="step-panel no-scrollbar min-h-0 overflow-y-auto px-4 py-4 sm:px-6"
        >
          {step === 0 ? (
            <>
              <p className="pad-question">
                What kind of work is this?
              </p>
              <p className="mt-1 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                It decides what the second document is: a developer&rsquo;s specification, an
                accounting engagement letter, an engineering brief. Pick the closest one &mdash;
                everything after this is shaped around it.
              </p>
              <div className="mt-3">
                <KindPicker
                  value={kind}
                  label={kindLabel}
                  onChange={setKind}
                  onLabel={setKindLabel}
                  disabled={pending}
                />
              </div>
            </>
          ) : null}

          {step === 1 ? (
            <>
              <label htmlFor="brief" className="pad-question">
                What is in scope
              </label>
              <p className="mt-1 font-body text-[13px] leading-[1.6] text-void">
                What the client needs, who it is for, and the constraints around it. The more
                concrete the scope, the tighter the quote.
              </p>
              <textarea
                id="brief"
                name="brief"
                required
                value={brief}
                disabled={pending}
                onChange={(event) => setBrief(event.target.value)}
                placeholder="A booking site for a dive shop in Cebu. Guests pick a date and a boat, pay a deposit online, and the shop sees tomorrow's manifest on a phone."
                className={`${WELL_TEXTAREA} pad-brief mt-2`}
              />
              {trimmedBrief.length > 0 ? (
                <p className="mt-2 font-label text-[12px] uppercase tracking-[0.14em] tabular-nums text-void">
                  {trimmedBrief.length} characters
                </p>
              ) : null}

              <div className="mt-3">
                <FieldLabel htmlFor="images">Reference material</FieldLabel>
                <ImageDropzone id="images" onChange={handleImages} disabled={pending} />
                <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                  Screenshots, or the client&rsquo;s own scope as a PDF, Word or Excel file &mdash;
                  PRISM reads them into the quotation.
                </p>
              </div>
            </>
          ) : null}

          {step === 2 ? (
            <>
              <p className="pad-question">Who it is for</p>
              <p className="mt-1 font-body text-[13px] leading-[1.6] text-void">
                All optional. Names go on the documents; the hints are read by the model and carry
                no arithmetic.
              </p>
              <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                <div>
                  <FieldLabel htmlFor="client_name">Client</FieldLabel>
                  <input
                    id="client_name"
                    name="client_name"
                    type="text"
                    value={clientName}
                    disabled={pending}
                    onChange={(event) => setClientName(event.target.value)}
                    placeholder="Blue Water Divers"
                    className={WELL}
                  />
                </div>
                <div>
                  <FieldLabel htmlFor="project_name">Project</FieldLabel>
                  <input
                    id="project_name"
                    name="project_name"
                    type="text"
                    value={projectName}
                    disabled={pending}
                    onChange={(event) => setProjectName(event.target.value)}
                    placeholder="Boat booking platform"
                    className={WELL}
                  />
                </div>
                <div>
                  <FieldLabel htmlFor="budget_hint">Budget hint</FieldLabel>
                  <input
                    id="budget_hint"
                    name="budget_hint"
                    type="text"
                    value={budgetHint}
                    disabled={pending}
                    onChange={(event) => setBudgetHint(event.target.value)}
                    placeholder="Under 500k, or no ceiling"
                    className={WELL}
                  />
                </div>
                <div>
                  <FieldLabel htmlFor="timeline_hint">Timeline hint</FieldLabel>
                  <input
                    id="timeline_hint"
                    name="timeline_hint"
                    type="text"
                    value={timelineHint}
                    disabled={pending}
                    onChange={(event) => setTimelineHint(event.target.value)}
                    placeholder="Live before Q4"
                    className={WELL}
                  />
                </div>
              </div>
            </>
          ) : null}

          {step === 3 ? (
            <>
              <p className="pad-question">Target Cost</p>
              <p className="mt-1 font-body text-[13px] leading-[1.6] text-void">
                Rates are set for the market below and quoted straight into the currency. Nothing is
                converted.
              </p>
              <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                <div>
                  <FieldLabel htmlFor="currency">Currency</FieldLabel>
                  <CurrencySelect
                    id="currency"
                    value={currency}
                    onChange={setCurrency}
                    disabled={pending}
                  />
                </div>
                <div>
                  <FieldLabel htmlFor="market_region">Market</FieldLabel>
                  <input
                    id="market_region"
                    name="market_region"
                    type="text"
                    value={marketRegion}
                    disabled={pending}
                    onChange={(event) => setMarketRegion(event.target.value)}
                    placeholder="Philippines"
                    className={WELL}
                  />
                </div>
                <div>
                  <FieldLabel htmlFor="tax_basis">Tax basis</FieldLabel>
                  <Dropdown
                    id="tax_basis"
                    value={taxMode}
                    disabled={pending}
                    onChange={setTaxMode}
                    options={[
                      { value: 'exclusive', label: 'Exclusive', hint: 'Tax added on top' },
                      { value: 'inclusive', label: 'Inclusive', hint: 'Tax already in the rates' },
                      { value: 'none', label: 'No tax', hint: 'Zero-rated or exempt' },
                    ]}
                  />
                  <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                    {taxMode === 'inclusive'
                      ? 'Every rate already contains the tax, and the total is what the client pays. The tax is stated as a memo, not added again.'
                      : taxMode === 'none'
                        ? 'No tax line anywhere: the total is the quoted work. The server clears any tax the model tries to add.'
                        : 'Rates are quoted net and the tax is added as its own line to reach the total.'}
                  </p>
                </div>
                <div>
                  <FieldLabel htmlFor="pricing_basis">Quoted from</FieldLabel>
                  <Dropdown
                    id="pricing_basis"
                    value={pricingBasis}
                    disabled={pending}
                    onChange={setPricingBasis}
                    options={[
                      { value: 'rate_card', label: 'Your rate card' },
                      { value: 'requirements', label: 'The requirements, at market rates' },
                    ]}
                  />
                  <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                    {pricingBasis === 'rate_card'
                      ? 'Roles and rates come from the card in Settings, and are enforced. With no card configured this falls back to market rates.'
                      : 'The card is ignored. PRISM scopes the roles it needs and quotes them at market rates for the chosen market.'}
                  </p>
                </div>
                <div className="sm:col-span-2">
                  <FieldLabel htmlFor="target_total">
                    Target cost ({currency}, {taxNote})
                  </FieldLabel>
                  <div className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
                    <input
                      id="target_total"
                      name="target_total"
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      value={targetTotal}
                      disabled={pending}
                      onChange={groupedInputHandler(setTargetTotal)}
                      placeholder="3,000,000"
                      aria-describedby="target_total_help"
                      className={`${WELL} font-label tabular-nums`}
                    />
                    <p
                      id="target_total_help"
                      className="font-body text-[13px] leading-[1.6] text-void sm:self-center"
                    >
                      Optional, and binding. The breakdown is solved onto this figure exactly.
                      {taxMode === 'exclusive'
                        ? ' It is the price of the work; the tax is added on top of it.'
                        : ''}{' '}
                      Leave it empty to be quoted what the work costs.
                    </p>
                  </div>
                </div>
              </div>
            </>
          ) : null}

          {step === 4 ? (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <p className="pad-question">Payment terms</p>
                <div className="flex gap-1 rounded-lg bg-duplicate p-1">
                  {(
                    [
                      ['equal', 'Deposit and equal payments'],
                      ['custom', 'Write each payment'],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={termsMode === value}
                      disabled={pending}
                      onClick={() => setTermsMode(value)}
                      className={`rounded-xs px-3 py-1.5 font-label text-[12px] uppercase tracking-[0.14em] ${
                        termsMode === value ? 'bg-paper text-ink shadow-sheet' : 'text-void'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {termsMode === 'custom' ? (
                <div className="mt-3">
                  <table className="w-full border-collapse text-[15px]">
                    <thead>
                      <tr>
                        {['Share', 'Becomes payable when', ''].map((head, index) => (
                          <th
                            key={head || index}
                            scope="col"
                            className="border-b border-rule pb-2 text-left font-label text-[12px] font-medium uppercase tracking-[0.14em] text-faint"
                          >
                            {head}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, index) => (
                        <tr key={index} className="border-b border-hairline last:border-b-0">
                          <td className="w-[120px] py-2 pr-3">
                            <input
                              aria-label={`Share for payment ${index + 1}`}
                              inputMode="decimal"
                              value={row.percent}
                              disabled={pending}
                              onChange={(event) =>
                                setRow(index, {
                                  percent: event.target.value.replace(/[^\d.]/g, ''),
                                })
                              }
                              className={`${WELL} text-right tabular-nums`}
                            />
                          </td>
                          <td className="py-2 pr-3">
                            <input
                              aria-label={`Trigger for payment ${index + 1}`}
                              value={row.trigger}
                              disabled={pending}
                              onChange={(event) => setRow(index, { trigger: event.target.value })}
                              placeholder="Staging accepted by the client"
                              className={WELL}
                            />
                          </td>
                          <td className="w-[110px] py-2 text-right">
                            <button
                              type="button"
                              disabled={pending || rows.length <= 1}
                              onClick={() => setRows(rows.filter((_, i) => i !== index))}
                              className={ACTION}
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                    <button
                      type="button"
                      disabled={pending || rows.length >= 24}
                      onClick={() => setRows([...rows, { percent: '', trigger: '' }])}
                      className={ACTION}
                    >
                      Add a payment
                    </button>
                    <p
                      aria-live="polite"
                      className={`font-label text-[13px] tabular-nums ${
                        schedulePreview.blocks ? 'text-alert' : 'text-ballpoint'
                      }`}
                    >
                      {schedulePreview.total}% of 100%
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 font-body text-[15px]">
                    <input
                      id="deposit_pct"
                      name="deposit_pct"
                      inputMode="decimal"
                      autoComplete="off"
                      value={depositPct}
                      disabled={pending}
                      onChange={(event) => setDepositPct(event.target.value.replace(/[^\d.]/g, ''))}
                      placeholder="0"
                      className={`${WELL} w-[104px] text-right tabular-nums`}
                    />
                    <span>% on signing, then the balance in</span>
                    <input
                      id="instalments"
                      name="instalments"
                      inputMode="numeric"
                      autoComplete="off"
                      value={instalments}
                      disabled={pending}
                      onChange={(event) => setInstalments(event.target.value.replace(/[^\d]/g, ''))}
                      className={`${WELL} w-[104px] text-right tabular-nums`}
                    />
                    <span>equal payments, due</span>
                    <Dropdown
                      id="payment_cadence"
                      value={cadence}
                      disabled={pending}
                      onChange={setCadence}
                      className="w-[200px]"
                      options={[
                        { value: 'monthly', label: 'monthly' },
                        { value: 'phase', label: 'on each phase' },
                        { value: 'milestone', label: 'on agreed milestones' },
                      ]}
                    />
                  </div>

                  {schedulePreview.active ? (
                    <div className="mt-4">
                      <FieldLabel htmlFor="deposit_trigger">
                        The deposit becomes payable on
                      </FieldLabel>
                      <input
                        id="deposit_trigger"
                        name="deposit_trigger"
                        type="text"
                        value={depositTrigger}
                        disabled={pending}
                        onChange={(event) => setDepositTrigger(event.target.value)}
                        placeholder="Signed statement of work"
                        className={`${WELL} max-w-[420px]`}
                      />
                    </div>
                  ) : null}
                </>
              )}

              <p
                aria-live="polite"
                className={`mt-3 max-w-[62ch] font-body text-[13px] leading-[1.6] ${
                  schedulePreview.blocks ? 'text-alert' : 'text-void'
                }`}
              >
                {schedulePreview.text}
              </p>
            </>
          ) : null}

          {step === 5 ? (
            <>
              <p className="pad-question">Tiers and cap</p>
              <p className="mt-1 font-body text-[13px] leading-[1.6] text-void">
                Both optional. Leave them empty for a single quotation quoted at what the work
                costs.
              </p>
              <div className="mt-3 grid gap-6 sm:grid-cols-[minmax(0,1fr)_minmax(0,240px)]">
                <div>
                  <FieldLabel htmlFor="tiers">Tiers</FieldLabel>
                  <input
                    id="tiers"
                    name="tiers"
                    type="text"
                    value={tiers}
                    disabled={pending}
                    onChange={(event) => setTiers(event.target.value)}
                    placeholder="Basic, Standard, Extended"
                    aria-describedby="tiers_help"
                    className={WELL}
                  />
                </div>

                <div>
                  <FieldLabel htmlFor="tier_ceiling">
                    Cap price ({currency}, {taxNote})
                  </FieldLabel>
                  <input
                    id="tier_ceiling"
                    name="tier_ceiling"
                    type="text"
                    inputMode="decimal"
                    autoComplete="off"
                    value={ceiling}
                    disabled={pending}
                    onChange={groupedInputHandler(setCeiling)}
                    placeholder="1,500,000"
                    aria-describedby="tiers_help"
                    className={`${WELL} font-label tabular-nums`}
                  />
                </div>
              </div>

              <p
                id="tiers_help"
                className="mt-3 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void"
              >
                {tierNames.length > 1
                  ? `${tierNames.length} complete quotations from this one brief — ${tierNames.join(', ')} — quoted from the top down, each one held below the tier above it.`
                  : 'Name two or more levels and PRISM quotes the same scope at each, as separate quotations.'}
              </p>
              <p className="mt-1 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                {ceilingHint}
              </p>
            </>
          ) : null}

          {step === 6 ? (
            <div className="flex min-h-full flex-col justify-center">
              <p className="pad-question">
                {ready ? 'Ready to quote it' : 'Almost — one thing missing'}
              </p>
              <p className="mt-1 max-w-[58ch] font-body text-[13px] leading-[1.6] text-void">
                {blocker ||
                  'One pass produces both documents: the quotation for the client, and the requirements for whoever does the work.'}
              </p>

              {recap.length ? (
                <dl className="mt-4 max-w-[46rem] divide-y divide-hairline border-y border-hairline">
                  {recap.map((entry) => (
                    <div
                      key={entry.id}
                      className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-2"
                    >
                      <dt className="font-label text-[12px] uppercase tracking-[0.12em] text-faint">
                        {entry.label}
                      </dt>
                      <dd className="min-w-0 max-w-[34rem] truncate font-body text-[13.5px] text-ink">
                        {entry.answer}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}

              {/* The action belongs here, at the end of the thing it finishes -
                  not in the corner of a footer that looked the same on every
                  other step. */}
              <button
                type="submit"
                disabled={!ready || pending}
                aria-disabled={!ready || pending}
                aria-label="Prepare quotation"
                className={`${ACTION_PRIMARY} mt-5 w-full max-w-[22rem] justify-center ${
                  pending ? 'cursor-wait' : ''
                }`}
              >
                <SubmitTicker pending={pending} clock={clock} label="Preparing" />
              </button>

              <p className="mt-2 font-body text-[12.5px] leading-[1.6] text-faint">
                Anything above can still be changed — the rail takes you back to it.
              </p>
            </div>
          ) : null}
        </div>
      </div>

      {pending ? (
        <div className="flex-none border-t border-rule px-4 py-4 sm:px-8">
          <JobStrip job={job} pending={pending} verb="quotation" />
        </div>
      ) : null}

      {/* One footer in both states. Swapping it out while pending would move
          the button the moment it was pressed. */}
      <div className="flex flex-none flex-wrap items-center justify-between gap-4 border-t border-rule px-4 py-4 sm:px-8">
        <p className="font-body text-[13px] leading-[1.6] text-void" aria-live="polite">
          {pending
            ? ''
            : blocker ||
              'Takes about a minute. You can change anything before you finish — one pass produces both documents.'}
        </p>

        <div className="flex items-center gap-3">
          {step > 0 && !pending ? (
            <button type="button" onClick={() => setStep(step - 1)} className={ACTION}>
              Back
            </button>
          ) : null}

          {/* Continue, and only Continue. The action that prices a quotation
              lives on the step that asks for it, where the answers it is about
              to act on are on screen - not in a corner that looked identical on
              every other step. */}
          {step < LAST && !pending ? (
            <button type="submit" className={ACTION_PRIMARY}>
              Continue
            </button>
          ) : null}
        </div>
      </div>

      <p className="sr-only" aria-live="polite">
        {pending && job ? `${job.stage}.` : ''}
      </p>
    </form>
  )
}
