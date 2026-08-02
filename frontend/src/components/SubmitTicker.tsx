import { useEffect, useState } from 'react'
import { DISPLAY } from './tokens'

/**
 * The button label while work is under way.
 *
 * It used to guess: four stage names on a timer, because the request was a
 * closed box and the only honest thing in it was the clock. The server now
 * reports what it is actually doing, so the guessing is gone — the real stage
 * is shown on the job strip beside this button, and the button keeps the one
 * thing it always measured for itself, elapsed time.
 *
 * The clock is deliberately not a percentage. Elapsed seconds are a fact; a
 * percentage derived from them would be the same fiction in a new costume.
 */

export function useElapsed(pending: boolean): string {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!pending) {
      setElapsed(0)
      return undefined
    }
    const started = Date.now()
    setElapsed(0)
    const timer = window.setInterval(() => setElapsed(Date.now() - started), 250)
    return () => window.clearInterval(timer)
  }, [pending])

  const seconds = Math.floor(elapsed / 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(
    2,
    '0',
  )}`
}

type SubmitTickerProps = {
  pending: boolean
  clock: string
  label?: string
  idleLabel?: string
}

export default function SubmitTicker({
  pending,
  clock,
  label = 'Preparing',
  idleLabel = 'Prepare quotation',
}: SubmitTickerProps) {
  if (!pending) {
    return <span className={`${DISPLAY} text-[13px] tracking-[-0.01em]`}>{idleLabel}</span>
  }

  // The clock sits on the accent fill, where the warm grey it used to wear was
  // about 2:1 — barely there. It is paper on a chip of --color-accent-deep now:
  // 10:1, and the chip reads as recessed rather than as a second button.
  //
  // A *lighter* chip was the obvious first move and the wrong one. Lightening
  // the ground behind pale text lowers its contrast; going darker raises it and
  // still separates the clock from the label beside it.
  return (
    <span aria-hidden="true" className="flex items-center justify-center gap-2.5">
      <span className="font-label text-[13px] font-medium uppercase tracking-[0.14em]">
        {label}
      </span>
      <span className="rounded-xs bg-accent-deep px-2 py-0.5 font-label text-[13px] font-medium tabular-nums text-paper">
        {clock}
      </span>
    </span>
  )
}
