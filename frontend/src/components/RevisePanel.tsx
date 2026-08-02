import { useMemo, useState } from 'react'
import { formatMoney, groupedInputHandler } from '../lib/format'
import SubmitTicker, { useElapsed } from './SubmitTicker'
import JobStrip from './JobStrip'
import { ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL, WELL_TEXTAREA } from './tokens'
import type { FormEvent } from 'react'
import type { ProposalChanges } from '../lib/api'
import type { Job, ProposalBundle } from '../types'

/**
 * The revision slip.
 *
 * On a quotation pad a revision is a new sheet, not an edit to the one already
 * with the client — so this sits below the pair of sheets, is written in the
 * same form vocabulary as the brief above it, and produces a fresh quotation
 * with its own reference. The original stays exactly as it was sent.
 *
 * Two ways to ask for a change, usable together:
 *   - say what should move, in your own words
 *   - name the total it has to come to
 *
 * The number is a separate field rather than something to bury in a sentence,
 * because the server solves for it exactly and prose is a bad place to keep an
 * arithmetic constraint.
 */

/** Read a total the way a person typed it: symbols, separators and spaces all fine. */
export function parseTarget(text: string): number | null {
  const cleaned = String(text ?? '').replace(/[^\d.]/g, '')
  if (!cleaned) return null
  const value = Number(cleaned)
  return Number.isFinite(value) && value > 0 ? value : null
}

/** What App's `describeError` produced, as this slip prints it. */
export type ReviseError = {
  code: number | null
  headline: string
  next: string
}

type RevisePanelProps = {
  bundle: ProposalBundle
  pending: boolean
  job: Job | null
  error: ReviseError | null
  onRevise: (changes: ProposalChanges) => void
}

export default function RevisePanel({ bundle, pending, job, error, onRevise }: RevisePanelProps) {
  const [instruction, setInstruction] = useState('')
  const [target, setTarget] = useState('')
  const clock = useElapsed(pending)

  const currency = bundle?.estimate?.currency || 'PHP'
  const currentTotal = Number(bundle?.estimate?.cost?.total) || 0
  const parsedTarget = useMemo(() => parseTarget(target), [target])
  const ready = Boolean(instruction.trim()) || parsedTarget !== null

  // Show the movement being asked for while it is being typed. A revision is a
  // decision about money, and the size of the change is the thing to see before
  // committing to a second Gemini pass.
  const movement = useMemo(() => {
    if (parsedTarget === null || !currentTotal) return null
    const delta = parsedTarget - currentTotal
    if (Math.abs(delta) < 0.005) return { text: 'No change to the total', delta: 0 }
    const percent = (delta / currentTotal) * 100
    const sign = delta > 0 ? '+' : '−'
    return {
      delta,
      text: `${sign}${formatMoney(Math.abs(delta), currency)} (${sign}${Math.abs(percent).toFixed(1)}%)`,
    }
  }, [parsedTarget, currentTotal, currency])

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!ready || pending) return
    onRevise({ instruction: instruction.trim(), targetTotal: target.trim() })
  }

  return (
    <section
      aria-labelledby="revise-heading"
      className="mx-auto mt-12 w-full max-w-sheet rounded-xl border border-rule bg-paper"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule px-6 py-5 sm:px-8">
        <h2 id="revise-heading" className={`${DISPLAY} text-[18px]`}>
          Revise this quotation
        </h2>
        <p className="font-label text-[12px] tracking-[0.14em] text-void">
          <span className="uppercase">Current total</span>{' '}
          <span className="tabular-nums text-ink">{formatMoney(currentTotal, currency)}</span>
        </p>
      </div>

      <form onSubmit={handleSubmit} className="px-6 py-6 sm:px-8">
        <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_minmax(0,260px)]">
          <div>
            <label htmlFor="revise-instruction" className={`${MONO_LABEL} block`}>
              What should change
            </label>
            <textarea
              id="revise-instruction"
              name="instruction"
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              disabled={pending}
              placeholder="Drop the SMS notifications, add two days of staff training, and keep the offline manifest."
              className={`${WELL_TEXTAREA} mt-2`}
            />
            <p className="mt-2 font-label text-[12px] text-void">
              Scope moves, not rates. Rates carry the market-rate basis a client can check.
            </p>
          </div>

          <div>
            <label htmlFor="revise-target" className={`${MONO_LABEL} block`}>
              Target total ({currency})
            </label>
            <input
              id="revise-target"
              name="target_total"
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={target}
              onChange={groupedInputHandler(setTarget)}
              disabled={pending}
              placeholder="3,000,000"
              className={`${WELL} mt-2 font-label tabular-nums`}
            />
            <p className="mt-2 min-h-[2.4em] font-label text-[12px] text-void">
              {movement ? (
                <>
                  <span className="uppercase tracking-[0.14em]">Movement</span>{' '}
                  <span className="tabular-nums text-ink">{movement.text}</span>
                </>
              ) : (
                'Optional. The breakdown is re-scoped to land on this figure exactly.'
              )}
            </p>
          </div>
        </div>

        {error ? (
          <p role="alert" className="mt-6 border-l-2 border-l-ballpoint pl-4 font-body text-[15px]">
            <span className="block font-label text-[12px] uppercase tracking-[0.14em] text-ballpoint">
              Revision not prepared
            </span>
            <span className="mt-1 block">{error.headline}</span>
            {error.next ? <span className="mt-1 block text-void">{error.next}</span> : null}
          </p>
        ) : null}

        {pending ? (
          <div className="mt-7">
            <JobStrip job={job} pending={pending} verb="revision" />
          </div>
        ) : null}

        <div className="mt-7 flex flex-wrap items-center justify-end gap-4">
          <p className="font-label text-[12px] text-void" aria-hidden="true">
            {ready
              ? 'A revision is a new sheet. This one stays as it is.'
              : 'Describe a change or set a target total.'}
          </p>

          <p className="sr-only" aria-live="polite">
            {pending && job ? `${job.stage}.` : ''}
          </p>

          <button
            type="submit"
            disabled={!ready}
            aria-disabled={!ready || pending}
            aria-label="Prepare revision"
            className={`${ACTION_PRIMARY} ${pending ? 'cursor-wait' : ''}`}
          >
            <SubmitTicker
              pending={pending}
              clock={clock}
              label="Revising"
              idleLabel="Prepare revision"
            />
          </button>
        </div>
      </form>
    </section>
  )
}
