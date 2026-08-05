/**
 * The app's mark: a prism drawn as an outlined triangle on a rounded tile,
 * with the spectrum it produces as three dashes beneath it.
 *
 * ONE DEFINITION, used everywhere in the React app - the landing page, the
 * sign-in card, and anywhere else a logo is wanted. The two copies that
 * cannot import it are `index.html`'s favicon and its boot splash, because
 * both paint before this bundle exists; each says so in its own comment and
 * names this file.
 *
 * The tile and the triangle take `--logo-tile` and `--logo-mark`, so the mark
 * follows the theme. THE THREE DASHES NEVER DO. They are the spectrum, they
 * are the reason the product is called PRISM, and a prism that splits white
 * light into three shades of the same colour is not a prism. They are brand,
 * not palette, and no colour sweep should touch them.
 */

/** The spectrum, in the order light leaves the glass. */
const SPECTRUM = ['#1b98a8', '#e3ae3c', '#d9645e'] as const

type PrismMarkProps = {
  /** Rendered size in px. The artwork is a square, so one number does. */
  size?: number
  /** A mark inside a labelled control is decoration; standalone it is not. */
  title?: string
  className?: string
}

export default function PrismMark({ size = 32, title, className = '' }: PrismMarkProps) {
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={`flex-none ${className}`}
      role={title ? 'img' : undefined}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
    >
      <rect width="64" height="64" rx="18" fill="var(--logo-tile)" />
      {/* Apex up, corners mitred rather than rounded off - the artwork's own
          joins are square, and rounding them turns the prism into a tent. */}
      <path
        d="M32 15.5 L47.5 43 H16.5 Z"
        fill="none"
        stroke="var(--logo-mark)"
        strokeWidth="3.1"
        strokeLinejoin="round"
      />
      {SPECTRUM.map((colour, index) => (
        <line
          key={colour}
          x1={18.5 + index * 9.6}
          y1="51.5"
          x2={25.5 + index * 9.6}
          y2="51.5"
          stroke={colour}
          strokeWidth="2.8"
          strokeLinecap="round"
        />
      ))}
    </svg>
  )
}
