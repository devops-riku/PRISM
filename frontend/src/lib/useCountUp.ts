import { useEffect, useRef, useState } from 'react'
import { prefersReducedMotion } from '../components/motion'

/**
 * A figure that counts up to itself.
 *
 * Six moments of motion are spent in CSS (see the MOTION block in
 * `src/index.css`); this is not a seventh. It is the same arrival those rules
 * describe, applied to the one thing CSS cannot animate — the *text* of a
 * number. `@property` can interpolate a custom property and `counter()` can
 * print it, but that route re-renders through generated content, which is not
 * selectable, not readable by a screen reader, and not `tabular-nums`.
 *
 * WHERE THIS BELONGS, and it is a short list. A dashboard figure that says how
 * much work is in flight is a fact about the app, and watching it arrive is
 * pleasant. A figure inside a document is a fact about money somebody is being
 * charged, and a total that spends 600ms reading ₱412,000 before settling on
 * ₱487,500 is a total that was WRONG for 600ms. Nobody who saw the wrong one
 * un-sees it. So: studio dashboards, never a quotation, a proposal, a line
 * item or any total printed on either.
 *
 * Whole numbers only. It rounds every frame, because a count that flickers
 * through 2.7 jobs is a count that reads as a bug.
 *
 * Under `prefers-reduced-motion: reduce` the final value is returned from the
 * FIRST render — not set by an effect a paint later, which would still show a
 * zero to anybody who asked not to be shown movement.
 */

const DURATION_MS = 600

/**
 * Ease-out cubic. It matches the shape of `--ease-press` closely enough that
 * the figure and the card it sits on read as one arrival, and it is arithmetic
 * rather than a second curve in the stylesheet.
 */
function easeOut(t: number): number {
  return 1 - (1 - t) ** 3
}

/**
 * Whether to skip the animation entirely: either the machine asked for less
 * movement, or there is no frame clock to animate against (a non-DOM render).
 * Both answers are "show the number".
 */
function settleAtOnce(): boolean {
  if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
    return true
  }
  return prefersReducedMotion()
}

export function useCountUp(value: number, duration: number = DURATION_MS): number {
  const [shown, setShown] = useState(() => (settleAtOnce() ? value : 0))
  // What the figure reads at this instant, held in a ref rather than read from
  // state inside the effect. Two things depend on that: the animation knows
  // where to start from without listing `shown` as a dependency (which would
  // restart it on every frame), and a re-render that did not change `value`
  // finds `at.current === value` and does nothing at all.
  const at = useRef(shown)

  useEffect(() => {
    if (at.current === value) return undefined

    if (settleAtOnce()) {
      at.current = value
      setShown(value)
      return undefined
    }

    const from = at.current
    const span = value - from
    const started = performance.now()
    let frame = 0

    const step = (now: number) => {
      const t = Math.min((now - started) / duration, 1)
      // The last frame is assigned rather than interpolated: floating point
      // arriving at 99.6% of the way to 12 must still print 12.
      const next = t >= 1 ? value : Math.round(from + span * easeOut(t))
      at.current = next
      setShown(next)
      if (t < 1) frame = window.requestAnimationFrame(step)
    }

    frame = window.requestAnimationFrame(step)
    return () => window.cancelAnimationFrame(frame)
  }, [value, duration])

  return shown
}
