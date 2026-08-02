/**
 * The Clarity Kit progress bar: an 8px track, a pine fill, a 450ms ease.
 *
 * The value is always something the server has actually reported. A job
 * declares its steps up front and marks them as each one genuinely comes back,
 * so a three-tier quotation moves in thirds. Nothing here animates on a timer —
 * a bar that fills smoothly while nothing has happened is a lie told in CSS,
 * and once one of them stalls at 90% nobody believes any of them again.
 *
 * A queued job has no percentage to give. It reports no value at all rather
 * than reporting zero, so a screen reader says "busy" instead of "0%".
 */

type ProgressBarProps = {
  value?: number
  label?: string
  tone?: 'accent' | 'alert'
  live?: boolean
}

export default function ProgressBar({
  value,
  label,
  tone = 'accent',
  live = false,
}: ProgressBarProps) {
  const known = typeof value === 'number' && Number.isFinite(value)
  const percent = known ? Math.round(Math.max(0, Math.min(1, value)) * 100) : 0

  return (
    <div
      className="h-2 overflow-hidden rounded-full bg-hairline"
      role="progressbar"
      aria-label={label || 'Progress'}
      {...(known ? { 'aria-valuenow': percent, 'aria-valuemin': 0, 'aria-valuemax': 100 } : {})}
    >
      {/* `live` puts a sheen on the fill, not on the width. The bar still only
          moves when a step genuinely finishes; the sheen is what distinguishes
          a job working through a forty-second tier from one that has stalled at
          the same number. */}
      <div
        className={`h-2 rounded-full transition-[width] duration-[450ms] ease-press ${
          tone === 'alert' ? 'bg-alert' : 'bg-ballpoint'
        } ${live ? 'bar-live' : ''}`}
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}

type ProgressRingProps = {
  value?: number
  label?: string
  size?: number
}

/**
 * The kit's second shape for the same value — "use the ring when space is
 * tight". Used beside a running job on the pad, where a full-width bar under
 * the submit row would push the form around every time the stage changed.
 */
export function ProgressRing({ value, label, size = 44 }: ProgressRingProps) {
  const known = typeof value === 'number' && Number.isFinite(value)
  const fraction = known ? Math.max(0, Math.min(1, value)) : 0
  const radius = size / 2 - 4
  const circumference = 2 * Math.PI * radius

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className="-rotate-90 flex-none"
      role="img"
      aria-label={label || `${Math.round(fraction * 100)}% done`}
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--color-hairline)"
        strokeWidth="5"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--color-ballpoint)"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={`${(circumference * fraction).toFixed(1)} 999`}
        className="transition-[stroke-dasharray] duration-[450ms] ease-press"
      />
    </svg>
  )
}
