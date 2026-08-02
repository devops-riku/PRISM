import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { fetchSettings, saveSettings } from '../lib/api'
import { groupDigits, ungroupDigits } from '../lib/format'
import type { ReferenceMode, RoleRate, StudioDefaults } from '../types'
import CurrencySelect from './CurrencySelect'
import DangerZone from './DangerZone'
import DesignEditor from './DesignEditor'
import Dropdown from './Dropdown'
import PolicyEditor from './PolicyEditor'
import TemplateEditor from './TemplateEditor'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL } from './tokens'

const UNITS = ['hour', 'day', 'week', 'month', 'item', 'lump_sum'] as const

//: How many clauses PRISM falls back to when the studio has configured none.
//: Kept in step with app/policies.py by hand - it is one number in one sentence,
//: and fetching the recommended set only to count it would be a request for a
//: number the reader can already see on any proposal.
const RECOMMENDED_CLAUSES = 16

/**
 * One line of the rate card while somebody is typing it.
 *
 * `RoleRate` is the saved shape, and its four figures are numbers because that
 * is what the server sends and stores. A field has to be emptiable mid-edit, so
 * the editor holds each of them as whatever is in the box — the number that was
 * loaded, or the text typed over it — and converts back at the one save
 * boundary in `handleSaveDefaults`.
 */
type RateDraft = Omit<RoleRate, 'rate' | 'hours_per_day' | 'days_per_week' | 'days_per_month'> & {
  rate: string | number
  hours_per_day: string | number
  days_per_week: string | number
  days_per_month: string | number
}

/**
 * The settings as this page holds them: the saved shape, with the rate card in
 * the form the editor keeps it and the rest of it optional.
 *
 * Optional because of the `catch` below. When the settings cannot be read the
 * page opens on five of the nineteen fields, and the other fourteen are
 * genuinely not there — which is why every read of one of them downstream is
 * already written with a fallback beside it.
 */
type SettingsDraft = Pick<
  StudioDefaults,
  'studio_name' | 'currency' | 'market_region' | 'tax_inclusive'
> &
  Partial<
    Omit<
      StudioDefaults,
      'studio_name' | 'currency' | 'market_region' | 'tax_inclusive' | 'rate_card'
    >
  > & { rate_card: RateDraft[] }

/**
 * Settings: one page, not a maze.
 *
 * It was six tabs. Tabs hide what a studio is trying to compare — the rate card
 * and the terms are both answers to "what am I committing to", and reading one
 * meant losing sight of the other. So: one scrolling page, a rail that jumps
 * within it, and each section labelled with what it is for on the left of what
 * it controls.
 *
 * Where this deliberately departs from the kit: the kit's settings apply as you
 * type. These do not. A half-typed rate is a number, and a number here is what
 * every future quotation is priced from — so there is one Save, it stays on
 * screen, and the header says plainly whether there is anything unsaved.
 */

const SECTIONS = [
  { id: 'studio', label: 'Studio' },
  { id: 'numbering', label: 'Custom prefix' },
  { id: 'rates', label: 'Rate card' },
  { id: 'terms', label: 'Proposal terms' },
  { id: 'template', label: 'Proposal template' },
  { id: 'design', label: 'Proposal design' },
  { id: 'danger', label: 'Danger zone', danger: true },
]

type SectionProps = {
  id: string
  title: string
  blurb: string
  children: ReactNode
  last?: boolean
  wide?: boolean
}

/**
 * One setting, or one group of them: what it is on the left, the controls on
 * the right. The label column is what makes a long page scannable — you read
 * down the left until you find the thing you came for.
 */
function Section({ id, title, blurb, children, last = false, wide = false }: SectionProps) {
  return (
    <section
      id={`set-${id}`}
      aria-labelledby={`set-${id}-title`}
      className={`grid scroll-mt-6 grid-cols-1 gap-x-8 gap-y-4 px-5 py-6 sm:px-7 ${
        // A few sections carry an editor that needs the whole width - a table, a
        // page preview. For those the label sits above rather than beside, since
        // squeezing a preview into two thirds of a column to keep a heading
        // company helps nobody.
        wide ? '' : 'lg:grid-cols-[minmax(0,200px)_minmax(0,1fr)]'
      } ${last ? '' : 'border-b border-hairline'}`}
    >
      <div className={wide ? 'max-w-[70ch]' : ''}>
        <h3 id={`set-${id}-title`} className="font-body text-[15px] font-semibold text-ink">
          {title}
        </h3>
        <p className="mt-1 font-body text-[13px] leading-[1.55] text-faint">{blurb}</p>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  )
}

type BasisCellProps = {
  entry: RateDraft
  index: number
  onChange: (index: number, patch: Partial<RateDraft>) => void
}

/**
 * What one unit of this role's time is, asked only where it changes an answer.
 *
 * A rate per item or per lump sum is not a length of time, so nothing here
 * applies to it. A rate per hour or per day needs the working day. A weekly or
 * monthly rate needs the days in that week or month as well — and a retainer
 * month is not thirty days, which is exactly the number a studio-wide setting
 * could not hold.
 */
function BasisCell({ entry, index, onChange }: BasisCellProps) {
  const unit = (entry.unit || 'day').toLowerCase()
  const name = entry.role || `role ${index + 1}`

  if (unit === 'item' || unit === 'lump_sum') {
    return <span className="font-label text-[12px] text-faint">Not a length of time</span>
  }

  const field = (
    key: 'hours_per_day' | 'days_per_week' | 'days_per_month',
    label: string,
    aria: string,
  ) => (
    <label className="flex items-center gap-2">
      <input
        aria-label={`${aria} for ${name}`}
        inputMode="decimal"
        value={entry[key] ?? ''}
        onChange={(event) => onChange(index, { [key]: event.target.value.replace(/[^\d.]/g, '') })}
        className={`${WELL} w-[68px] px-2 py-1.5 text-right tabular-nums`}
      />
      <span className="font-label text-[12px] text-void">{label}</span>
    </label>
  )

  return (
    <div className="flex flex-col gap-1.5">
      {field('hours_per_day', 'hours in a day', 'Billable hours in one day')}
      {unit === 'week' ? field('days_per_week', 'days in a week', 'Working days in one week') : null}
      {unit === 'month'
        ? field('days_per_month', 'days in a month', 'Working days in one month')
        : null}
    </div>
  )
}

export default function SettingsPanel() {
  // `null` is "not read yet": until the settings land, the card below renders
  // nothing but "Reading defaults". Every handler in this component is reached
  // through a control inside that branch, so the `!`s on `defaults` and on the
  // updater's `current` assert what the render already guarantees. None of them
  // adds a runtime check, and none of them can be reached before the load.
  const [defaults, setDefaults] = useState<SettingsDraft | null>(null)
  const [savedAt, setSavedAt] = useState('')
  const [savingError, setSavingError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [here, setHere] = useState('studio')
  const scroller = useRef<HTMLDivElement | null>(null)
  // The section a click is currently scrolling towards, or '' when the reader
  // is driving. A ref rather than state: the observer reads it mid-scroll, and
  // re-rendering the rail on every frame of a scroll is the thing this exists
  // to stop.
  const jumping = useRef('')

  useEffect(() => {
    fetchSettings()
      .then(setDefaults)
      .catch(() =>
        setDefaults({
          studio_name: '',
          currency: 'PHP',
          market_region: 'Philippines',
          tax_inclusive: false,
          rate_card: [],
        }),
      )
  }, [])

  // Which section the reader is in, so the rail says where they are rather than
  // only where they could go.
  useEffect(() => {
    if (!defaults) return undefined
    const root = scroller.current
    if (!root) return undefined

    // The observer only counts a section once it reaches the top fifth of the
    // card. The last section can never get there - there is nothing below it to
    // scroll - so at the end of the page it would hand the rail back to
    // whichever section still owned that band, and clicking "Danger zone" lit
    // up "Proposal design" instead. Reaching the bottom IS being in the last
    // section, so it is read that way.
    const last = SECTIONS[SECTIONS.length - 1].id
    const atBottom = () => root.scrollHeight - root.scrollTop - root.clientHeight <= 2

    const seen = new Map<string, number>()
    const watcher = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => seen.set(entry.target.id, entry.intersectionRatio))
        // A smooth scroll passes through every section between here and there,
        // and each one owns the top band on the way. Reported, that reads as
        // the highlight flicking back to where it came from before arriving —
        // so while a jump is in flight the rail holds the answer the click
        // already gave it.
        if (jumping.current) return
        if (atBottom()) {
          setHere(last)
          return
        }
        const top = [...seen.entries()]
          .filter(([, ratio]) => ratio > 0)
          .sort((a, b) => b[1] - a[1])[0]
        if (top) setHere(top[0].replace('set-', ''))
      },
      { root, rootMargin: '-10% 0px -70% 0px', threshold: [0, 0.25, 0.5, 1] },
    )

    SECTIONS.forEach((section) => {
      const node = root.querySelector(`#set-${section.id}`)
      if (node) watcher.observe(node)
    })

    // A smooth scroll that lands at the bottom crosses no new threshold on its
    // last few pixels, so the observer may never fire again. The scroll itself
    // is the signal.
    //
    // It is also what ends a jump: the scroll stopping is the only reliable
    // sign that a smooth scroll has arrived. `scrollend` says so exactly where
    // it exists; everywhere else, a scroll that has not moved for 120ms has
    // stopped. Both release the rail back to the observer.
    const settle = () => {
      jumping.current = ''
      if (atBottom()) setHere(last)
    }

    // Read once, into a boolean: testing `'onscrollend' in window` inline
    // narrows `window` itself to `never` in the false branch, because the
    // property is declared in lib.dom whether or not the browser has it.
    const hasScrollEnd = 'onscrollend' in window

    let idle = 0
    const onScroll = () => {
      if (atBottom() && !jumping.current) setHere(last)
      if (!hasScrollEnd) {
        window.clearTimeout(idle)
        idle = window.setTimeout(settle, 120)
      }
    }

    root.addEventListener('scroll', onScroll, { passive: true })
    root.addEventListener('scrollend', settle)

    return () => {
      watcher.disconnect()
      window.clearTimeout(idle)
      root.removeEventListener('scroll', onScroll)
      root.removeEventListener('scrollend', settle)
    }
  }, [defaults])

  /**
   * Scroll one section into view **inside the card**, and nowhere else.
   *
   * `scrollIntoView` walks up the tree and scrolls every ancestor that can
   * move, which took the whole page with it and left the card halfway off the
   * screen. Measuring against the container and setting its own `scrollTop`
   * cannot touch anything outside it.
   */
  const jumpTo = (id: string) => {
    const root = scroller.current
    const node = root?.querySelector(`#set-${id}`)
    if (!root || !node) return
    const top = node.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop

    // Clamped, because that is where the container will actually end up: the
    // last section sits well past the furthest this can scroll, and comparing
    // against the unreachable figure would make every jump to it look like a
    // scroll that is still coming.
    const furthest = Math.max(0, root.scrollHeight - root.clientHeight)
    const target = Math.min(furthest, Math.max(0, top - 8))

    // Claimed before the scroll starts, released when it stops. Everything the
    // observer sees in between is scenery passing the window.
    jumping.current = id
    root.scrollTo({ top: target, behavior: 'smooth' })

    // A jump to where you already are scrolls nowhere, fires no scroll event,
    // and would leave the rail held until something else moved it.
    if (Math.abs(root.scrollTop - target) < 2) jumping.current = ''
  }

  const edit = (patch: Partial<SettingsDraft>) => {
    setDirty(true)
    setDefaults((current) => ({ ...current!, ...patch }))
  }

  // The server owns the counter, so its preview is the truthful one - but it
  // only arrives on load and on save. While the prefix is being typed the shape
  // is predicted here so the block moves with the field rather than after it.
  // The server owns both counters, so its previews are the truthful ones - but
  // they only arrive on load and on save. While a prefix is being typed the
  // shape is predicted here so the block moves with the field rather than after
  // it.
  const previewOf = (
    prefix: string | undefined,
    mode: ReferenceMode,
    fromServer: string,
    fallback: string,
  ) => {
    const head = (prefix || '').toUpperCase() || fallback
    if (fromServer && fromServer.startsWith(`${head}-`)) return fromServer
    if (mode !== 'incremental') return `${head}-XXXXXX`
    return `${head}-${fromServer.split('-')[1] ?? '0000001'}`
  }

  const referencePreview = previewOf(
    defaults?.reference_prefix,
    defaults?.reference_mode || 'random',
    defaults?.reference_preview || '',
    'Q',
  )

  const proposalPreview = previewOf(
    defaults?.proposal_prefix,
    defaults?.proposal_reference_mode || 'incremental',
    defaults?.proposal_reference_preview || '',
    'P',
  )

  const setRate = (index: number, patch: Partial<RateDraft>) => {
    setDirty(true)
    setDefaults((current) => ({
      ...current!,
      rate_card: current!.rate_card.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)),
    }))
  }

  const handleSaveDefaults = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSavingError('')
    // Rates are typed as text so the field can be emptied while editing; the
    // server drops any row without a role or a positive rate.
    const payload = {
      ...defaults!,
      // Blank clauses are dropped rather than saved: an empty list means "use
      // the recommended set", and a half-typed clause must not silently switch
      // the studio onto its own.
      // A section with no id is a row the editor never filled; the server would
      // drop it anyway, and sending it makes the saved order look edited when
      // it is not.
      proposal_sections: (defaults!.proposal_sections || []).filter((section) => section.id),
      policies: (defaults!.policies || []).filter(
        (clause) => (clause.title || '').trim() && (clause.body || '').trim(),
      ),
      rate_card: (defaults!.rate_card || [])
        .map((entry) => ({
          ...entry,
          rate: Number(ungroupDigits(entry.rate)) || 0,
          // Typed as text so a field can be emptied mid-edit; the server clamps
          // each one to something a working day can actually be.
          hours_per_day: Number(entry.hours_per_day) || 8,
          days_per_week: Number(entry.days_per_week) || 5,
          days_per_month: Number(entry.days_per_month) || 22,
        }))
        .filter((entry) => entry.role.trim() && entry.rate > 0),
    }
    // The body is the complete saved shape. After a failed load this page is
    // holding five fields of it, so saving sends five - which is what it does
    // today, and means the fourteen it never managed to read are written as the
    // server's own defaults. That is pre-existing behaviour, and the assertion
    // is here to record it rather than to hide it.
    saveSettings(payload as StudioDefaults)
      .then((saved) => {
        setDefaults(saved)
        setDirty(false)
        setSavedAt(new Date().toLocaleTimeString())
      })
      .catch((failure: { message?: string }) =>
        setSavingError(failure?.message || 'The defaults were not saved.'),
      )
  }

  const status = savingError
    ? 'Not saved'
    : dirty
      ? 'Unsaved changes'
      : savedAt
        ? `Saved at ${savedAt}`
        : 'Up to date'

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className="flex min-h-0 flex-1 flex-col overflow-clip rounded-[22px] border border-rule bg-paper shadow-sheet">
        <div className="flex shrink-0 flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-hairline px-5 py-4 sm:px-7">
          <div>
            <h2 className={`${DISPLAY} text-[20px]`}>Settings</h2>
            <p className="mt-1 font-body text-[13.5px] text-faint">
              Everything on one page. Nothing takes effect until you save it.
            </p>
          </div>
          <p
            aria-live="polite"
            className={`font-label text-[12px] uppercase tracking-[0.12em] ${
              savingError ? 'text-alert' : dirty ? 'text-ballpoint' : 'text-faint'
            }`}
          >
            {status}
          </p>
        </div>

        {defaults ? (
          <form onSubmit={handleSaveDefaults} className="flex min-h-0 flex-1 flex-col">
            <div ref={scroller} className="no-scrollbar relative min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <div className="flex items-start">
                {/* The rail scrolls the same page, never opens another one. It
                    stays put while the sections move under it. */}
                <nav
                  aria-label="Settings sections"
                  className="sticky top-0 hidden w-[196px] flex-none flex-col gap-0.5 self-start border-r border-hairline px-3 py-5 lg:flex"
                >
                  <span className="px-3 pb-2 font-label text-[11px] uppercase tracking-[0.12em] text-faint">
                    Jump to
                  </span>
                  {SECTIONS.map((section) => {
                    const active = here === section.id
                    // The danger row keeps its red whatever happens. What it was
                    // missing is the filled background every other row gets when
                    // it is the section you are actually in - its own colour was
                    // tested first, so `active` never reached it.
                    const tone = section.danger
                      ? active
                        ? 'bg-alert/[0.08] text-alert'
                        : 'text-alert hover:bg-alert/[0.06]'
                      : active
                        ? 'bg-duplicate text-ballpoint'
                        : 'text-body hover:bg-duplicate hover:text-ballpoint'
                    return (
                      <a
                        key={section.id}
                        href={`#set-${section.id}`}
                        onClick={(event) => {
                          event.preventDefault()
                          jumpTo(section.id)
                          setHere(section.id)
                        }}
                        aria-current={active ? 'true' : undefined}
                        className={`rounded-md px-3 py-2 font-body text-[14px] no-underline transition-colors duration-150 ${tone}`}
                      >
                        {section.label}
                      </a>
                    )
                  })}
                  <span className="px-3 pt-3 font-body text-[12px] leading-[1.5] text-faint">
                    Scrolls the same page — never a new one.
                  </span>
                </nav>

                <div className="min-w-0 flex-1">
                  <Section
                    id="studio"
                    title="Studio"
                    blurb="Who is quoting, and what a new brief opens with. These prefill the form and nothing else."
                  >
                    <div className="grid max-w-[46rem] grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
                      <div className="sm:col-span-2">
                        <label htmlFor="studio_name" className={MONO_LABEL}>
                          Studio name
                        </label>
                        <input
                          id="studio_name"
                          type="text"
                          value={defaults.studio_name || ''}
                          onChange={(event) => edit({ studio_name: event.target.value })}
                          placeholder="PRISM"
                          className={`${WELL} mt-2 max-w-[420px]`}
                        />
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          It names the profile in the header, gives it its initials, and signs the
                          documents.
                        </p>
                      </div>

                      <div>
                        <label htmlFor="default_currency" className={MONO_LABEL}>
                          Currency
                        </label>
                        <div className="mt-2">
                          <CurrencySelect
                            id="default_currency"
                            value={defaults.currency}
                            onChange={(code) => edit({ currency: code })}
                          />
                        </div>
                      </div>
                      <div>
                        <label htmlFor="default_region" className={MONO_LABEL}>
                          Market
                        </label>
                        <input
                          id="default_region"
                          type="text"
                          value={defaults.market_region}
                          onChange={(event) => edit({ market_region: event.target.value })}
                          className={`${WELL} mt-2`}
                        />
                      </div>
                      <div>
                        <label htmlFor="default_tax" className={MONO_LABEL}>
                          Tax basis
                        </label>
                        <Dropdown
                          id="default_tax"
                          className="mt-2"
                          value={
                            defaults.tax_mode || (defaults.tax_inclusive ? 'inclusive' : 'exclusive')
                          }
                          onChange={(mode) =>
                            edit({ tax_mode: mode, tax_inclusive: mode === 'inclusive' })
                          }
                          options={[
                            { value: 'exclusive', label: 'Exclusive', hint: 'Tax added on top' },
                            {
                              value: 'inclusive',
                              label: 'Inclusive',
                              hint: 'Tax already in the rates',
                            },
                            { value: 'none', label: 'No tax', hint: 'Zero-rated or exempt' },
                          ]}
                        />
                      </div>
                      <div>
                        <label htmlFor="default_contingency" className={MONO_LABEL}>
                          Contingency
                        </label>
                        <Dropdown
                          id="default_contingency"
                          className="mt-2"
                          value={defaults.show_contingency_to_client ? 'show' : 'absorb'}
                          onChange={(mode) =>
                            edit({ show_contingency_to_client: mode === 'show' })
                          }
                          options={[
                            {
                              value: 'absorb',
                              label: 'Absorbed',
                              hint: 'No line the client can see',
                            },
                            { value: 'show', label: 'Itemised', hint: 'Its own row in the costing' },
                          ]}
                        />
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          {defaults.show_contingency_to_client
                            ? 'The client sees a contingency row and can ask you to remove it.'
                            : 'The buffer is folded into the effort on each line. Same total, no row to argue about, and the developer sheet still states it.'}
                        </p>
                      </div>
                    </div>
                  </Section>

                  <Section
                    id="numbering"
                    title="Custom prefix"
                    blurb="The references a client quotes back at you. Two series, because a quotation and a proposal are two documents — sharing a counter would make proposal 41 the forty-first quotation."
                  >
                    <p className={`${MONO_LABEL} mb-3`}>PAD quotation no.</p>
                    <div className="grid max-w-[46rem] grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
                      <div>
                        <label htmlFor="reference_prefix" className={MONO_LABEL}>
                          Prefix
                        </label>
                        <input
                          id="reference_prefix"
                          type="text"
                          value={defaults.reference_prefix || ''}
                          maxLength={4}
                          autoComplete="off"
                          onChange={(event) =>
                            edit({
                              reference_prefix: event.target.value
                                .replace(/[^a-zA-Z]/g, '')
                                .toUpperCase()
                                .slice(0, 4),
                            })
                          }
                          placeholder="Q"
                          className={`${WELL} mt-2 font-label tracking-[0.08em]`}
                        />
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          Up to four letters. Empty uses Q.
                        </p>
                      </div>

                      <div>
                        <span className={MONO_LABEL}>How the number is chosen</span>
                        <div className="mt-2 flex gap-1 rounded-lg bg-duplicate p-1">
                          {(
                            [
                              ['incremental', 'Incremental', 'Counts up: 0000001, 0000002'],
                              ['random', 'Random', 'A short draw, says nothing about volume'],
                            ] as const
                          ).map(([mode, label, hint]) => {
                            const active = (defaults.reference_mode || 'random') === mode
                            return (
                              <button
                                key={mode}
                                type="button"
                                aria-pressed={active}
                                title={hint}
                                onClick={() => edit({ reference_mode: mode })}
                                className={`flex-1 rounded-xs px-3 py-1.5 font-label text-[12px] uppercase tracking-[0.14em] ${
                                  active ? 'bg-paper text-ink shadow-sheet' : 'text-void'
                                }`}
                              >
                                {label}
                              </button>
                            )
                          })}
                        </div>
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          {(defaults.reference_mode || 'random') === 'incremental'
                            ? 'Each quotation takes the next number, and keeps it through every revision.'
                            : 'Each quotation draws its own. Nothing about the number tells a client how many you have sent.'}
                        </p>
                      </div>
                    </div>

                    <div className="mt-4 max-w-[46rem] rounded-lg border border-rule bg-duplicate px-4 py-3 text-center">
                      <p className={MONO_LABEL}>Next quotation number</p>
                      <p className="mt-1.5 font-label text-[26px] tracking-[0.06em] tabular-nums text-ink">
                        {referencePreview}
                      </p>
                      <p className="mt-1 font-body text-[13px] text-void">
                        Seven digits, then base 36 (0-9, A-Z) once they run out — so the reference
                        never widens and never repeats. Revisions add R2, R3.
                      </p>
                    </div>

                    <p className={`${MONO_LABEL} mb-3 mt-6 border-t border-hairline pt-5`}>
                      Proposal no.
                    </p>

                    <div className="grid max-w-[46rem] grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
                      <div>
                        <label htmlFor="proposal_prefix" className={MONO_LABEL}>
                          Prefix
                        </label>
                        <input
                          id="proposal_prefix"
                          type="text"
                          value={defaults.proposal_prefix || ''}
                          maxLength={4}
                          autoComplete="off"
                          onChange={(event) =>
                            edit({
                              proposal_prefix: event.target.value
                                .replace(/[^a-zA-Z]/g, '')
                                .toUpperCase()
                                .slice(0, 4),
                            })
                          }
                          placeholder="P"
                          className={`${WELL} mt-2 font-label tracking-[0.08em]`}
                        />
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          Up to four letters. Empty uses P.
                        </p>
                      </div>

                      <div>
                        <span className={MONO_LABEL}>How the number is chosen</span>
                        <div className="mt-2 flex gap-1 rounded-lg bg-duplicate p-1">
                          {(
                            [
                              ['incremental', 'Incremental', 'Counts up: 0000001, 0000002'],
                              ['random', 'Random', 'A short draw, says nothing about volume'],
                            ] as const
                          ).map(([mode, label, hint]) => {
                            const active =
                              (defaults.proposal_reference_mode || 'incremental') === mode
                            return (
                              <button
                                key={mode}
                                type="button"
                                aria-pressed={active}
                                title={hint}
                                onClick={() => edit({ proposal_reference_mode: mode })}
                                className={`flex-1 rounded-xs px-3 py-1.5 font-label text-[12px] uppercase tracking-[0.14em] ${
                                  active ? 'bg-paper text-ink shadow-sheet' : 'text-void'
                                }`}
                              >
                                {label}
                              </button>
                            )
                          })}
                        </div>
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          Given when the proposal is built, and kept: a document that has been
                          sent does not change its number.
                        </p>
                      </div>
                    </div>

                    <div className="mt-4 max-w-[46rem] rounded-lg border border-rule bg-duplicate px-4 py-3 text-center">
                      <p className={MONO_LABEL}>Next proposal number</p>
                      <p className="mt-1.5 font-label text-[26px] tracking-[0.06em] tabular-nums text-ink">
                        {proposalPreview}
                      </p>
                      <p className="mt-1 font-body text-[13px] text-void">
                        Printed on the proposal beside the quotation it is priced from.
                      </p>
                    </div>
                  </Section>

                  <Section
                    id="rates"
                    title="Rate card"
                    blurb="What the studio charges. Unlike the defaults, these bind: they are sent to the model and enforced on the way back."
                    wide
                  >
                    <p className={MONO_LABEL}>
                      {defaults.rate_card?.length
                        ? `${defaults.rate_card.length} roles · ${defaults.currency}`
                        : 'Not configured — the model quotes from the requirements at market rates'}
                    </p>

                    <p className="mt-2 max-w-[70ch] font-body text-[13px] leading-[1.6] text-void">
                      Working time is set per role, because a monthly retainer and a day rate do not
                      share a working month. It is what makes “per day” mean something: a line quoted
                      in another length of time is converted onto that role’s rate — 16 hours against
                      an 8-hour day rate is 2 days, same money. Per item and per lump sum are not
                      lengths of time, so they are never converted.
                    </p>

                    {defaults.rate_card?.length ? (
                      <div className="mt-4 overflow-x-auto">
                        <table className="w-full border-collapse text-[14px]">
                          <thead>
                            <tr>
                              {[
                                'Role',
                                'Charged per',
                                `Rate for one (${defaults.currency})`,
                                'Working time',
                                '',
                              ].map((head, i) => (
                                <th
                                  key={head || i}
                                  scope="col"
                                  className={`border-b border-rule px-3 py-2 font-label text-[12px] font-medium uppercase tracking-[0.14em] text-faint ${
                                    head.startsWith('Rate') ? 'text-right' : 'text-left'
                                  }`}
                                >
                                  {head}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {defaults.rate_card.map((entry, index) => (
                              <tr
                                key={index}
                                className="row-touch border-b border-hairline last:border-b-0"
                              >
                                <td className="px-3 py-2">
                                  <input
                                    aria-label={`Role ${index + 1}`}
                                    value={entry.role}
                                    onChange={(event) =>
                                      setRate(index, { role: event.target.value })
                                    }
                                    placeholder="Senior Backend Engineer"
                                    className={WELL}
                                  />
                                </td>
                                <td className="px-3 py-2">
                                  <Dropdown
                                    id={`unit-${index}`}
                                    aria-label={`Charged per, for ${entry.role || `role ${index + 1}`}`}
                                    value={entry.unit}
                                    onChange={(unit) => setRate(index, { unit })}
                                    options={UNITS.map((unit) => ({
                                      value: unit,
                                      label: unit.replace('_', ' '),
                                    }))}
                                  />
                                </td>
                                <td className="px-3 py-2">
                                  <input
                                    aria-label={`Rate for ${entry.role || `role ${index + 1}`}`}
                                    inputMode="decimal"
                                    value={groupDigits(entry.rate)}
                                    onChange={(event) =>
                                      setRate(index, { rate: groupDigits(event.target.value) })
                                    }
                                    className={`${WELL} text-right tabular-nums`}
                                  />
                                  <span className="mt-1 block text-right font-label text-[12px] text-faint">
                                    per {(entry.unit || 'day').replace('_', ' ')}
                                  </span>
                                </td>
                                <td className="px-3 py-2">
                                  <BasisCell entry={entry} index={index} onChange={setRate} />
                                </td>
                                <td className="px-3 py-2 text-right">
                                  <button
                                    type="button"
                                    className={ACTION}
                                    onClick={() =>
                                      edit({
                                        rate_card: defaults.rate_card.filter((_, i) => i !== index),
                                      })
                                    }
                                  >
                                    Remove
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}

                    <button
                      type="button"
                      className={`${ACTION} mt-4`}
                      onClick={() =>
                        edit({
                          rate_card: [
                            ...(defaults.rate_card || []),
                            {
                              role: '',
                              unit: 'day',
                              rate: '',
                              hours_per_day: 8,
                              days_per_week: 5,
                              days_per_month: 22,
                            },
                          ],
                        })
                      }
                    >
                      Add a role
                    </button>
                  </Section>

                  <Section
                    id="terms"
                    title="Proposal terms"
                    blurb="The clauses every proposal carries, and who signs it. PRISM writes the argument; it never writes these."
                  >
                    <div className="grid max-w-[46rem] grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
                      <div>
                        <label htmlFor="signatory" className={MONO_LABEL}>
                          Who signs
                        </label>
                        <input
                          id="signatory"
                          type="text"
                          value={defaults.proposal_signatory || ''}
                          onChange={(event) => edit({ proposal_signatory: event.target.value })}
                          placeholder="Maria Santos"
                          className={`${WELL} mt-2`}
                        />
                      </div>
                      <div>
                        <label htmlFor="signatory_title" className={MONO_LABEL}>
                          Their title
                        </label>
                        <input
                          id="signatory_title"
                          type="text"
                          value={defaults.proposal_signatory_title || ''}
                          onChange={(event) =>
                            edit({ proposal_signatory_title: event.target.value })
                          }
                          placeholder="Managing Director"
                          className={`${WELL} mt-2`}
                        />
                        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
                          Printed on the studio's side of the signature block. The client's side is
                          left blank for a pen.
                        </p>
                      </div>
                    </div>

                    <div className="mt-6 border-t border-hairline pt-5">
                      <PolicyEditor
                        clauses={defaults.policies || []}
                        fallbackCount={RECOMMENDED_CLAUSES}
                        onChange={(policies) => edit({ policies })}
                      />
                    </div>
                  </Section>

                  <Section
                    id="template"
                    title="Proposal template"
                    blurb="Which sections a proposal has, in what order, called what. Documents already built keep the shape they were built with."
                    wide
                  >
                    <TemplateEditor
                      sections={defaults.proposal_sections || []}
                      onChange={(proposal_sections) => edit({ proposal_sections })}
                    />
                  </Section>

                  <Section
                    id="design"
                    title="Proposal design"
                    blurb="How it looks — logo, colours, type, margins. Nothing here can touch a figure or a clause."
                    wide
                  >
                    <DesignEditor
                      design={defaults.proposal_design}
                      studioName={defaults.studio_name}
                      onChange={(proposal_design) => edit({ proposal_design })}
                    />
                  </Section>

                  <Section
                    id="danger"
                    title="Danger zone"
                    blurb="Irreversible. Everything above belongs to this workspace, and so does every quotation and proposal in it."
                    last
                  >
                    <DangerZone />
                  </Section>
                </div>
              </div>
            </div>

            {savingError ? (
              <p
                role="alert"
                className="shrink-0 border-t border-rule px-5 py-3 font-body text-[15px] text-alert sm:px-7"
              >
                {savingError}
              </p>
            ) : null}

            {/* Save stays on screen. A button you have to scroll to find is a
                button people stop pressing. */}
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-4 border-t border-rule px-5 py-3 sm:px-7">
              <p aria-live="polite" className="font-label text-[12px] text-void">
                {dirty ? 'Changes are not saved yet' : savedAt ? `Saved at ${savedAt}` : ''}
              </p>
              <button type="submit" disabled={!dirty} className={ACTION_PRIMARY}>
                Save settings
              </button>
            </div>
          </form>
        ) : (
          <p className="px-5 py-8 text-center font-label text-[12px] uppercase tracking-[0.14em] text-void sm:px-7">
            Reading defaults
          </p>
        )}
      </section>
    </div>
  )
}
