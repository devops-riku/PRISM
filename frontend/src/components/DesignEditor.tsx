import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { HexColorPicker } from 'react-colorful'
import Dropdown from './Dropdown'
import { ACTION, MONO_LABEL, WELL } from './tokens'
import type {
  DesignCover,
  DesignFont,
  DesignMargins,
  DesignTables,
  ProposalDesign,
} from '../types'

/**
 * How a proposal looks: logo, colour, type, margins, ruling, footer.
 *
 * What this editor deliberately cannot touch is a word of the content. Figures
 * are printed from the quotation and terms from the studio's own clauses, and
 * the one guarantee PRISM makes is that neither can be typed over. So the
 * editable surface is the dress: nothing here can put a number on a page.
 *
 * The preview beside the controls is a page, not a swatch board. A colour on a
 * chip tells you nothing about whether the totals row still reads at arm's
 * length — so every change lands on a sheet with a letterhead, a table and a
 * total, at the margins it will actually be printed at.
 */

/** A face in the menu, carrying the CSS stack the preview draws it with. */
type FontOption = {
  value: DesignFont
  label: string
  hint: string
  css: string
}

//: Mirrors app/design.py. Each face has to exist in both renderers, which is
//: why the menu is short: a webfont the PDF cannot draw would give a studio a
//: design that changes shape the moment they export it.
const FONTS: FontOption[] = [
  { value: 'sans', label: 'Figtree', hint: 'The app’s own', css: '"Figtree", ui-sans-serif, sans-serif' },
  {
    value: 'grotesque',
    label: 'Segoe UI / Arial',
    hint: 'Neutral, wide',
    css: '"Segoe UI", Arial, Helvetica, sans-serif',
  },
  {
    value: 'serif',
    label: 'Georgia / Times',
    hint: 'Bookish',
    css: 'Georgia, "Times New Roman", serif',
  },
]

const COVERS: SegmentedOption<DesignCover>[] = [
  { value: 'centred', label: 'Centred', hint: 'Title in the middle of the page' },
  { value: 'left', label: 'Left', hint: 'Ranged left, like a letter' },
  { value: 'banner', label: 'Banner', hint: 'Title on a block of accent' },
]

/** A margin choice also carries the millimetres the preview pads its page by. */
type MarginOption = SegmentedOption<DesignMargins> & { mm: number }

const MARGINS: MarginOption[] = [
  { value: 'compact', label: 'Compact', mm: 14 },
  { value: 'standard', label: 'Standard', mm: 18 },
  { value: 'roomy', label: 'Roomy', mm: 24 },
]

const TABLES: SegmentedOption<DesignTables>[] = [
  { value: 'ruled', label: 'Ruled', hint: 'A hairline under every row' },
  { value: 'zebra', label: 'Zebra', hint: 'Alternating tint, no rules' },
  { value: 'plain', label: 'Plain', hint: 'Nothing but the header rule' },
]

const PALETTE = { brand: '#1D1B17', accent: '#35655A' }

//: 400 KB decoded, matching MAX_LOGO_BYTES. The logo is embedded in every
//: export, so an eleven-megabyte PNG would be carried into every PDF the studio
//: ever sends.
const MAX_LOGO_BYTES = 400_000

const DEFAULTS: ProposalDesign = {
  logo: '',
  brand_colour: PALETTE.brand,
  accent_colour: PALETTE.accent,
  heading_font: 'sans',
  body_font: 'sans',
  cover: 'centred',
  margins: 'standard',
  tables: 'ruled',
  page_numbers: true,
  footer_note: '',
}

const stack = (key: DesignFont) => (FONTS.find((font) => font.value === key) || FONTS[0]).css
const millimetres = (key: DesignMargins) => (MARGINS.find((m) => m.value === key) || MARGINS[1]).mm

type SegmentedOption<T extends string> = {
  value: T
  label: string
  hint?: string
}

type SegmentedProps<T extends string> = {
  label: string
  value: T
  options: SegmentedOption<T>[]
  onChange: (value: T) => void
  name: string
  note?: string
}

/** A row of mutually exclusive choices, shown rather than described. */
function Segmented<T extends string>({ label, value, options, onChange, name, note }: SegmentedProps<T>) {
  return (
    <div>
      <span className={MONO_LABEL}>{label}</span>
      <div className="mt-2 flex gap-1 rounded-lg bg-duplicate p-1" role="group" aria-label={label}>
        {options.map((option) => {
          const active = value === option.value
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              title={option.hint || ''}
              onClick={() => onChange(option.value)}
              className={`min-w-0 flex-1 truncate rounded-xs px-2 py-1.5 font-label text-[12px] uppercase tracking-[0.1em] transition-colors duration-150 ${
                active ? 'bg-paper text-ink shadow-sheet' : 'text-void hover:text-ink'
              }`}
              name={name}
            >
              {option.label}
            </button>
          )
        })}
      </div>
      {note ? (
        <p className="mt-1.5 font-body text-[12px] leading-[1.5] text-faint">{note}</p>
      ) : null}
    </div>
  )
}

type ColourFieldProps = {
  id: string
  label: string
  value: string
  fallback: string
  onChange: (value: string) => void
}

/** A colour, editable as a swatch you can point at or a hex you can paste. */
function ColourField({ id, label, value, fallback, onChange }: ColourFieldProps) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return undefined
    const away = (event: MouseEvent) => {
      // `event.target` is an EventTarget, and `contains` takes `Node | null`.
      // Narrowing with `instanceof Node` instead would skip the close when the
      // target is null, where `contains(null)` returns false and it closes.
      if (box.current && !box.current.contains(event.target as Node | null)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  const hex = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value) ? value : fallback

  return (
    <div ref={box} className="relative">
      <label htmlFor={id} className={MONO_LABEL}>
        {label}
      </label>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          aria-label={`Pick ${label.toLowerCase()}`}
          aria-expanded={open}
          onClick={() => setOpen((was) => !was)}
          style={{ background: hex }}
          className="h-9 w-9 shrink-0 rounded-md border border-rule shadow-sheet transition-transform duration-150 ease-press hover:scale-105 active:scale-95"
        />
        <input
          id={id}
          value={value || ''}
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
          onBlur={() => onChange(hex)}
          placeholder={fallback}
          className={`${WELL} font-label uppercase tracking-[0.08em]`}
        />
      </div>
      {open ? (
        <div className="absolute left-0 z-30 mt-2 rounded-lg border border-rule bg-paper p-3 shadow-raised">
          <HexColorPicker color={hex} onChange={onChange} />
          <button
            type="button"
            className={`${ACTION} mt-3 w-full`}
            onClick={() => {
              onChange(fallback)
              setOpen(false)
            }}
          >
            Back to PRISM’s
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** What a sheet hands the contents it wraps: the two stacks, and the cover mode. */
type SheetContext = {
  heading: string
  body: string
  banner: boolean
}

type SheetProps = {
  look: ProposalDesign
  studioName?: string
  children: (context: SheetContext) => ReactNode
}

/** One sheet of paper, at the settings you are currently looking at. */
function Sheet({ look, studioName, children }: SheetProps) {
  const mm = millimetres(look.margins)
  const heading = stack(look.heading_font)
  const body = stack(look.body_font)
  const banner = look.cover === 'banner'

  return (
    <div
      className="mx-auto w-full max-w-[300px] overflow-hidden rounded-md border border-rule bg-white shadow-raised"
      style={{ aspectRatio: '210 / 297' }}
      aria-hidden="true"
    >
      <div
        className="flex h-full flex-col"
        style={{
          padding: `${(mm / 210) * 100}%`,
          fontFamily: body,
          color: look.brand_colour,
        }}
      >
        <div className="flex items-center justify-between" style={{ fontSize: '5px' }}>
          {look.logo ? (
            <img src={look.logo} alt="" className="max-h-[14px] w-auto object-contain" />
          ) : (
            <span
              style={{
                fontFamily: heading,
                fontWeight: 600,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
              }}
            >
              {studioName || 'Your studio'}
            </span>
          )}
          <span style={{ opacity: 0.5 }}>PROPOSAL · Q-0002187</span>
        </div>
        <div style={{ borderTop: '0.5px solid rgb(0 0 0 / 0.18)', marginTop: '4px' }} />

        {children({ heading, body, banner })}

        <div
          className="mt-auto flex items-end justify-between"
          style={{ fontSize: '4.5px', opacity: 0.55 }}
        >
          <span>{look.footer_note}</span>
          <span>{look.page_numbers ? 'Page 1' : ''}</span>
        </div>
      </div>
    </div>
  )
}

type CoverProps = {
  look: ProposalDesign
  studioName?: string
}

/**
 * The cover, which is a whole page and nothing else.
 *
 * That is what a cover is for - it says what the document is and who it is
 * for, and the work starts overleaf. Showing it stacked above the investment
 * table described a document PRISM does not produce.
 */
function Cover({ look, studioName }: CoverProps) {
  return (
    <Sheet look={look} studioName={studioName}>
      {({ heading, banner }) =>
        banner ? (
          <div
            style={{
              background: look.accent_colour,
              color: '#fff',
              fontFamily: heading,
              fontWeight: 600,
              fontSize: '15px',
              lineHeight: 1.15,
              padding: '30px 12px',
              margin: '10px -5% 0',
            }}
          >
            Customer portal
            <br />
            rebuild
            <p style={{ fontSize: '6px', fontWeight: 400, opacity: 0.85, marginTop: '8px' }}>
              For Northwind Retail Group
            </p>
          </div>
        ) : (
          <div
            style={{
              textAlign: look.cover === 'left' ? 'left' : 'center',
              marginTop: '32%',
            }}
          >
            <p style={{ fontFamily: heading, fontWeight: 600, fontSize: '15px', lineHeight: 1.15 }}>
              Customer portal rebuild
            </p>
            <p style={{ fontSize: '6.5px', opacity: 0.65, marginTop: '7px' }}>
              For Northwind Retail Group
            </p>
            <p style={{ fontSize: '6px', opacity: 0.55, marginTop: '4px' }}>
              Prepared by {studioName || 'your studio'}
            </p>
          </div>
        )
      }
    </Sheet>
  )
}

type ContentsProps = {
  look: ProposalDesign
  studioName?: string
}

/** The page after it: where the ruling and the accent actually do their work. */
function Contents({ look, studioName }: ContentsProps) {
  const rows = [
    ['Discovery and specification', '1', '180,000.00'],
    ['Design system and key screens', '1', '265,000.00'],
    ['Build, integration and QA', '1', '640,000.00'],
  ]
  const rule = look.tables === 'ruled' ? '1px solid rgb(0 0 0 / 0.10)' : 'none'

  return (
    <Sheet look={look} studioName={studioName}>
      {({ heading }) => (
        <>
          <p
            style={{
              fontFamily: heading,
              fontWeight: 600,
              fontSize: '7px',
              marginTop: '12px',
              letterSpacing: '0.01em',
            }}
          >
            Investment
          </p>

          <table
            style={{ width: '100%', borderCollapse: 'collapse', fontSize: '5px', marginTop: '5px' }}
          >
            <thead>
              <tr style={{ textAlign: 'left' }}>
                {['Item', 'Qty', 'Amount'].map((head, index) => (
                  <th
                    key={head}
                    style={{
                      borderBottom: '0.6px solid rgb(0 0 0 / 0.35)',
                      padding: '3px 2px',
                      fontWeight: 600,
                      opacity: 0.7,
                      textAlign: index === 0 ? 'left' : 'right',
                    }}
                  >
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(([item, qty, amount], index) => (
                <tr
                  key={item}
                  style={{
                    background:
                      look.tables === 'zebra' && index % 2 === 1
                        ? 'rgb(0 0 0 / 0.045)'
                        : 'transparent',
                  }}
                >
                  <td style={{ borderBottom: rule, padding: '3px 2px' }}>{item}</td>
                  <td style={{ borderBottom: rule, padding: '3px 2px', textAlign: 'right' }}>
                    {qty}
                  </td>
                  <td
                    style={{
                      borderBottom: rule,
                      padding: '3px 2px',
                      textAlign: 'right',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {amount}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginTop: '7px',
              paddingTop: '4px',
              borderTop: `1.2px solid ${look.accent_colour}`,
            }}
          >
            <span style={{ fontSize: '5px', letterSpacing: '0.1em', opacity: 0.7 }}>TOTAL</span>
            <span
              style={{
                fontFamily: stack(look.heading_font),
                fontWeight: 600,
                fontSize: '10px',
                color: look.accent_colour,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              ₱1,215,600.00
            </span>
          </div>
        </>
      )}
    </Sheet>
  )
}

type DesignEditorProps = {
  design?: ProposalDesign | null
  studioName?: string
  onChange: (design: ProposalDesign) => void
}

export default function DesignEditor({ design, studioName, onChange }: DesignEditorProps) {
  const [problem, setProblem] = useState('')
  const fileRef = useRef<HTMLInputElement | null>(null)
  const look: ProposalDesign = { ...DEFAULTS, ...(design || {}) }

  const set = (patch: Partial<ProposalDesign>) => onChange({ ...look, ...patch })

  const takeLogo = (file: File | undefined) => {
    setProblem('')
    if (!file) return
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) {
      setProblem('That file is not a PNG, JPEG, GIF or WebP.')
      return
    }
    if (file.size > MAX_LOGO_BYTES) {
      setProblem(
        `That logo is ${Math.round(file.size / 1024)} KB. It rides in every export, so the cap is 400 KB.`,
      )
      return
    }
    const reader = new FileReader()
    reader.onload = () => set({ logo: String(reader.result || '') })
    reader.onerror = () => setProblem('That file could not be read.')
    reader.readAsDataURL(file)
  }

  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
      <div className="flex flex-col gap-5">
        <p className="max-w-[70ch] font-body text-[13px] leading-[1.6] text-void">
          How a proposal looks. Nothing here can reach what it says — figures come from the
          quotation and terms from your clauses, which is exactly why the dress is safe to edit.
          Proposals already built keep the look they were built with.
        </p>

        <div>
          <span className={MONO_LABEL}>Logo</span>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <div className="flex h-[52px] w-[120px] items-center justify-center rounded-md border border-rule bg-duplicate px-2">
              {look.logo ? (
                <img src={look.logo} alt="Your logo" className="max-h-[40px] w-auto object-contain" />
              ) : (
                <span className="font-label text-[11px] uppercase tracking-[0.14em] text-faint">
                  None
                </span>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              className="sr-only"
              onChange={(event) => takeLogo(event.target.files?.[0])}
            />
            <button type="button" className={ACTION} onClick={() => fileRef.current?.click()}>
              {look.logo ? 'Replace' : 'Choose a file'}
            </button>
            {look.logo ? (
              <button
                type="button"
                className={ACTION}
                onClick={() => {
                  set({ logo: '' })
                  if (fileRef.current) fileRef.current.value = ''
                }}
              >
                Remove
              </button>
            ) : null}
          </div>
          <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
            Printed at the top of every page in place of the studio name. PNG, JPEG, GIF or WebP, up
            to 400 KB.
          </p>
          {problem ? (
            <p role="alert" className="mt-2 font-body text-[13px] text-alert">
              {problem}
            </p>
          ) : null}
        </div>

        <div className="grid grid-cols-1 gap-x-6 gap-y-5 sm:grid-cols-2">
          <ColourField
            id="brand_colour"
            label="Brand colour"
            value={look.brand_colour}
            fallback={PALETTE.brand}
            onChange={(brand_colour) => set({ brand_colour })}
          />
          <ColourField
            id="accent_colour"
            label="Accent colour"
            value={look.accent_colour}
            fallback={PALETTE.accent}
            onChange={(accent_colour) => set({ accent_colour })}
          />

          <div>
            <label htmlFor="heading_font" className={MONO_LABEL}>
              Headings
            </label>
            <Dropdown
              id="heading_font"
              className="mt-2"
              value={look.heading_font}
              onChange={(heading_font) => set({ heading_font })}
              options={FONTS}
            />
          </div>
          <div>
            <label htmlFor="body_font" className={MONO_LABEL}>
              Body text
            </label>
            <Dropdown
              id="body_font"
              className="mt-2"
              value={look.body_font}
              onChange={(body_font) => set({ body_font })}
              options={FONTS}
            />
            <p className="mt-1.5 font-body text-[12px] leading-[1.5] text-faint">
              The PDF embeds the closest face installed on the server. If it cannot draw your
              currency symbol, amounts print as ISO codes — PHP 1,500,000.00.
            </p>
          </div>

          {/* Two of these are stylesheet work in the print view and structural
              work in the PDF, so they are honest about where they land rather
              than promising a PDF that would come out looking different. */}
          <Segmented
            label="Cover"
            name="cover"
            value={look.cover}
            options={COVERS}
            onChange={(cover) => set({ cover })}
            note="Print view only. The PDF keeps its own cover."
          />
          <Segmented
            label="Margins"
            name="margins"
            value={look.margins}
            options={MARGINS.map((m) => ({ ...m, hint: `${m.mm} mm` }))}
            onChange={(margins) => set({ margins })}
          />
          <Segmented
            label="Tables"
            name="tables"
            value={look.tables}
            options={TABLES}
            onChange={(tables) => set({ tables })}
            note="Print view only. The PDF rules its tables the same way throughout."
          />

          <div>
            <label htmlFor="footer_note" className={MONO_LABEL}>
              Footer line
            </label>
            <input
              id="footer_note"
              value={look.footer_note || ''}
              maxLength={120}
              onChange={(event) => set({ footer_note: event.target.value })}
              placeholder="TIN 000-000-000-000"
              className={`${WELL} mt-2`}
            />
          </div>

          <div className="self-end">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={look.page_numbers !== false}
                onChange={(event) => set({ page_numbers: event.target.checked })}
                className="h-4 w-4 accent-ballpoint"
              />
              <span className="font-body text-[14px] text-void">Number the pages</span>
            </label>
            <p className="mt-1.5 font-body text-[12px] leading-[1.5] text-faint">
              PDF only. A browser prints its own page footer and no page can switch that off.
            </p>
          </div>
        </div>

        <div>
          <button type="button" className={ACTION} onClick={() => onChange({ ...DEFAULTS })}>
            Reset to the shipped look
          </button>
        </div>
      </div>

      {/* The preview stays put while the controls scroll: a change you cannot
          see the effect of is a change you have to guess at. */}
      <div className="xl:sticky xl:top-0 xl:self-start">
        <p className={`${MONO_LABEL} mb-2 text-center`}>Preview</p>
        <div className="flex flex-col gap-3">
          <Cover look={look} studioName={studioName} />
          <Contents look={look} studioName={studioName} />
        </div>
        <p className="mt-2 text-center font-body text-[12px] leading-[1.5] text-faint">
          The cover and the page after it, at these settings. The words are a sample; the figures on
          a real proposal come from its quotation.
        </p>
      </div>
    </div>
  )
}
