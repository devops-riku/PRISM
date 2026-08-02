import type { TemplateSection } from '../types'
import { ACTION, MONO_LABEL, WELL } from './tokens'

/**
 * The proposal's shape: which sections it has, in what order, called what.
 *
 * What this editor deliberately does not offer is a way to invent a section.
 * Every row maps to a builder that knows where its content comes from — the
 * model, the quotation, or the studio's own clauses — so a heading here always
 * has something real underneath it. A free-text section would be a heading over
 * nothing, or worse, an invitation for the model to fill it.
 *
 * Three sections cannot be switched off. Investment is what the client is being
 * asked to agree to; the terms and the signature block are what make the
 * document something they can act on. A proposal missing any of the three is a
 * letter, and sending one by accident is not a mistake worth allowing.
 */

/** The three places a section's content can come from. */
type SectionSource = 'written' | 'quotation' | 'studio'

const SOURCE_LABEL: Record<SectionSource, string> = {
  written: 'Written for each proposal',
  quotation: 'Printed from the quotation',
  studio: 'Your own words',
}

/** id -> where the content comes from. Mirrors app/template.py. */
const SOURCES: Record<string, SectionSource> = {
  cover_letter: 'written',
  executive_summary: 'written',
  understanding: 'written',
  scope_overview: 'written',
  scope: 'quotation',
  exclusions: 'quotation',
  approach: 'written',
  phases: 'quotation',
  acceptance: 'quotation',
  deliverables: 'written',
  assumptions: 'quotation',
  risks: 'written',
  why_us: 'written',
  investment: 'quotation',
  payment: 'quotation',
  next_steps: 'written',
  terms: 'studio',
  signatures: 'studio',
}

const DEFAULT_HEADINGS: Record<string, string> = {
  cover_letter: '(the covering letter)',
  executive_summary: 'In brief',
  understanding: 'What we understand',
  scope_overview: 'What we propose',
  scope: 'Scope of work',
  exclusions: 'What is not included',
  approach: 'How we will work',
  phases: 'Phases and timeline',
  acceptance: 'How work is accepted',
  deliverables: 'What you will have',
  assumptions: 'Assumptions',
  risks: 'What could go wrong, and what we do about it',
  why_us: 'Why us for this',
  investment: 'Investment',
  payment: 'Payment schedule',
  next_steps: 'Next steps',
  terms: 'Terms',
  signatures: 'Signatures',
}

const REQUIRED = new Set(['investment', 'terms', 'signatures'])
const ORDER = Object.keys(DEFAULT_HEADINGS)

type TemplateEditorProps = {
  sections?: TemplateSection[]
  onChange: (sections: TemplateSection[]) => void
}

export default function TemplateEditor({ sections = [], onChange }: TemplateEditorProps) {
  // An unconfigured studio sees the shipped document, so the editor shows it
  // rather than an empty box that hides what a proposal currently looks like.
  const rows = sections.length ? sections : ORDER.map((id) => ({ id, heading: '', enabled: true }))

  const commit = (next: TemplateSection[]) => onChange(next)

  const set = (index: number, patch: Partial<TemplateSection>) =>
    commit(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const move = (index: number, by: number) => {
    const target = index + by
    if (target < 0 || target >= rows.length) return
    const next = [...rows]
    ;[next[index], next[target]] = [next[target], next[index]]
    commit(next)
  }

  const printed = rows.filter((row) => row.enabled !== false || REQUIRED.has(row.id)).length

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="max-w-[70ch] font-body text-[13px] leading-[1.6] text-void">
          The order every proposal follows, and what each section is called. Sections cannot be
          invented here — each one is a piece PRISM knows how to build, so a heading always has
          something real under it.
        </p>
        <p className={MONO_LABEL}>{printed} sections</p>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr>
              {['', 'Heading', 'Content', 'Printed', ''].map((head, index) => (
                <th
                  key={head || index}
                  scope="col"
                  className="border-b border-rule px-3 py-2 text-left font-label text-[12px] font-medium uppercase tracking-[0.14em] text-faint"
                >
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const required = REQUIRED.has(row.id)
              const off = row.enabled === false && !required
              return (
                <tr
                  key={row.id}
                  className={`row-touch border-b border-hairline last:border-b-0 ${
                    off ? 'opacity-55' : ''
                  }`}
                >
                  <td className="w-[68px] px-3 py-2 align-middle">
                    <span className="flex gap-1">
                      <button
                        type="button"
                        aria-label={`Move ${DEFAULT_HEADINGS[row.id]} earlier`}
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                        className="h-7 w-7 rounded-md border border-rule bg-paper text-void disabled:opacity-40 hover:enabled:border-ballpoint hover:enabled:text-ballpoint"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        aria-label={`Move ${DEFAULT_HEADINGS[row.id]} later`}
                        disabled={index === rows.length - 1}
                        onClick={() => move(index, 1)}
                        className="h-7 w-7 rounded-md border border-rule bg-paper text-void disabled:opacity-40 hover:enabled:border-ballpoint hover:enabled:text-ballpoint"
                      >
                        ↓
                      </button>
                    </span>
                  </td>

                  <td className="px-3 py-2">
                    {row.id === 'cover_letter' ? (
                      <span className="font-body text-[14px] text-void">
                        {DEFAULT_HEADINGS[row.id]}
                      </span>
                    ) : (
                      <input
                        aria-label={`Heading for ${DEFAULT_HEADINGS[row.id]}`}
                        value={row.heading || ''}
                        onChange={(event) => set(index, { heading: event.target.value })}
                        placeholder={DEFAULT_HEADINGS[row.id]}
                        className={`${WELL} py-1.5`}
                      />
                    )}
                  </td>

                  <td className="whitespace-nowrap px-3 py-2 font-label text-[12px] text-void">
                    {SOURCE_LABEL[SOURCES[row.id]] || ''}
                  </td>

                  <td className="px-3 py-2">
                    <label className="flex cursor-pointer items-center gap-2">
                      <input
                        type="checkbox"
                        checked={row.enabled !== false || required}
                        disabled={required}
                        onChange={(event) => set(index, { enabled: event.target.checked })}
                        className="h-4 w-4 accent-ballpoint disabled:opacity-60"
                      />
                      <span className="font-label text-[12px] text-void">
                        {required ? 'Always' : row.enabled === false ? 'No' : 'Yes'}
                      </span>
                    </label>
                  </td>

                  <td className="px-3 py-2 text-right" />
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <button type="button" className={`${ACTION} mt-4`} onClick={() => commit([])}>
        Reset to the shipped order
      </button>
    </div>
  )
}
