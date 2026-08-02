import { formatMoney } from '../lib/format'

type StampTotalProps = {
  total: number
  currency: string
}

/**
 * The signature element: the grand total in a rubber-stamped block. Double
 * ballpoint rule with a 6px gap, mono uppercase currency code above the figure,
 * struck at -2.5deg.
 *
 * `.stamp` in src/index.css owns the look and the press — the resting state is
 * the settled state, and the 180ms press only runs when motion is welcome, so
 * a reduced-motion visitor sees a finished stamp rather than a mid-press frame.
 * Nothing here animates anything itself.
 */
export default function StampTotal({ total, currency }: StampTotalProps) {
  return (
    <div className="stamp">
      <span className="stamp-currency">
        <span className="sr-only">Grand total, </span>
        {currency}
      </span>
      <span className="stamp-figure">{formatMoney(total, currency)}</span>
    </div>
  )
}
