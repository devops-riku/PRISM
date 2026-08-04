import { formatMoney, formatNumber, formatPct } from '../lib/format'
import StampTotal from './StampTotal'
import { DISPLAY } from './tokens'
import type { CostSummary, LineItem, UnitKind } from '../types'

const UNIT_LABEL: Record<UnitKind, string> = {
  hour: 'hr',
  day: 'day',
  week: 'wk',
  month: 'mo',
  item: 'item',
  lump_sum: 'lump',
}

const unitOf = (unit: UnitKind): string => UNIT_LABEL[unit] || String(unit || '').replace(/_/g, ' ')

/** One line of the addition beneath the table. `note` carries the percentage. */
type SummaryRow = {
  key: string
  label: string
  note?: string
  value: number
}

type LineItemTableProps = {
  lineItems?: LineItem[]
  cost?: CostSummary
  currency: string
}

/**
 * The heart of the client sheet: ruled rows, description left, every figure in
 * mono and right aligned, a heavier rule above the summary, and the stamped
 * grand total half-overlapping the bottom right corner.
 *
 * Every number here is printed exactly as the server sent it. The server owns
 * the arithmetic — recomputing anything in the browser is how a document ends
 * up showing a total that does not match its rows.
 */
export default function LineItemTable({ lineItems, cost, currency }: LineItemTableProps) {
  const items = Array.isArray(lineItems) ? lineItems : []
  // `Partial` rather than `CostSummary`, because `cost || {}` really can be the
  // empty object here and every read below already defaults for itself.
  const summary: Partial<CostSummary> = cost || {}

  const rows: SummaryRow[] = []
  rows.push({ key: 'subtotal', label: 'Subtotal', value: summary.subtotal || 0 })
  if (summary.contingency_pct || summary.contingency_amount) {
    rows.push({
      key: 'contingency',
      label: 'Contingency',
      note: summary.contingency_pct ? formatPct(summary.contingency_pct) : '',
      value: summary.contingency_amount || 0,
    })
  }
  if (summary.discount_amount) {
    rows.push({
      key: 'discount',
      label: 'Discount',
      value: -Math.abs(summary.discount_amount),
    })
  }
  // Inclusive pricing puts the tax inside the rates, so it is not a row in this
  // addition — adding it would make the column stop summing to the total under
  // it. It becomes a memo beneath the table instead.
  if (summary.tax_label && !summary.tax_inclusive) {
    rows.push({
      key: 'tax',
      label: summary.tax_label,
      note: summary.tax_pct ? formatPct(summary.tax_pct) : '',
      value: summary.tax_amount || 0,
    })
  }

  const taxMemo =
    summary.tax_label && summary.tax_inclusive
      ? `Total is inclusive of ${summary.tax_label}${
          summary.tax_pct ? ` (${formatPct(summary.tax_pct)})` : ''
        }, ${formatMoney(summary.tax_amount || 0, currency)} of it.`
      : ''

  return (
    <section className="mt-12" aria-labelledby="line-items-heading">
      <h2 id="line-items-heading" className={`${DISPLAY} mb-5 text-[24px] text-ink`}>
        Line items
      </h2>

      {items.length === 0 ? (
        <p className="font-body text-[15px] leading-[1.6] text-void">
          No line items came back with this estimate. Prepare the quotation again with a more
          specific brief — scope, platforms and integrations are what the pricing hangs on.
        </p>
      ) : (
        <div className="relative sm:mb-16 sm:pb-[64px]">
          {/* The stamp straddles the bottom edge of this block. The bottom
              padding is what keeps it clear of the summary figures — a
              quotation that hides one of its own numbers is worse than no
              stamp at all. */}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <caption className="sr-only">
                Line items quoted in {currency}. Every figure is in {currency}.
              </caption>
              <thead>
                <tr className="border-b-2 border-ink">
                  <th
                    scope="col"
                    className="hidden py-2 pr-4 text-left font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void sm:table-cell"
                  >
                    Ref
                  </th>
                  <th
                    scope="col"
                    className="py-2 pr-4 text-left font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void"
                  >
                    Description
                  </th>
                  <th
                    scope="col"
                    className="py-2 pr-4 text-right font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void"
                  >
                    Qty
                  </th>
                  {/* No Rate column. It was here, and the studio asked for it
                      gone: a rate beside a quantity turns the screen into a
                      timesheet, and the habit of reading the work that way
                      starts on the screen you check it on. `markdown.py` took
                      the same column out of the document for the same reason
                      and pointed AT this table as the place the rate still
                      lived - that pointer is now stale, and its comment says
                      so.

                      The rate is not lost: `unit_rate` is still on every item,
                      still what `subtotal` was computed from, and still in the
                      developer sheet. It is simply not shown beside the work
                      it prices. */}
                  <th
                    scope="col"
                    className="py-2 text-right font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void"
                  >
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, index) => {
                  // Neither role nor category: one unbroken list of work, named
                  // by what it is. A category like "QA" or "PM" is a
                  // department, and a department is a role by another name —
                  // both live in the developer sheet.
                  //
                  // This said "what the client reads", and that was wrong.
                  // `LineItemTable` is reached only from `ResultSheets`, which
                  // is rendered only at `App.tsx`'s studio route, behind the
                  // auth gate; nothing under `components/client/` imports
                  // either. The client reads the RENDERED DOCUMENT
                  // (`backend/app/renderers/markdown.py`), which builds its own
                  // table. Worth keeping straight, because "the client sees
                  // this" is the argument that decides what may be shown here.
                  const meta = ''
                  return (
                    <tr key={item.id || `li-${index}`} className="border-b border-rule align-top">
                      <td className="hidden py-3 pr-4 font-label text-[13px] tabular-nums text-void sm:table-cell">
                        {item.id}
                      </td>
                      <td className="py-3 pr-4">
                        <p className="font-body text-[15px] leading-[1.6] text-ink">
                          {item.description}
                        </p>
                        {meta ? (
                          <p className="mt-1 font-label text-[12px] uppercase tracking-[0.1em] text-void">
                            {meta}
                          </p>
                        ) : null}
                        {/* The narrow-screen stand-in for the two `sm:`-only
                            columns. It used to carry the rate as well - which
                            would have left the rate visible on a phone after
                            the column was removed on a desktop, the usual way
                            a hidden figure survives its own deletion. Only the
                            ref is left. */}
                        {item.id ? (
                          <p className="mt-1 font-label text-[12px] tabular-nums text-void sm:hidden">
                            {item.id}
                          </p>
                        ) : null}
                      </td>
                      <td className="whitespace-nowrap py-3 pr-4 text-right font-label text-[13px] tabular-nums text-ink">
                        {formatNumber(item.quantity || 0)}{' '}
                        <span className="text-void">{unitOf(item.unit)}</span>
                      </td>
                      <td className="whitespace-nowrap py-3 text-right font-label text-[13px] tabular-nums text-ink">
                        {formatMoney(item.subtotal || 0, currency)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* `rate_basis` is not shown here. Where the rates came from is an
              answer to a question the client has not asked, and printing it
              invites a negotiation about the derivation rather than a decision
              about the work. It lives in the developer sheet instead. */}

          <dl className="ml-auto mt-6 w-full max-w-[360px] border-t-2 border-ink">
            {rows.map((row) => (
              <div
                key={row.key}
                className="flex items-baseline justify-between gap-6 border-b border-rule py-2 last:border-b-0"
              >
                <dt className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void">
                  {row.label}
                  {row.note ? <span className="ml-2 tabular-nums">{row.note}</span> : null}
                </dt>
                <dd className="whitespace-nowrap font-label text-[13px] tabular-nums text-ink">
                  {formatMoney(row.value, currency)}
                </dd>
              </div>
            ))}
          </dl>

          {taxMemo ? (
            <p className="ml-auto mt-2 w-full max-w-[360px] text-right font-label text-[12px] leading-[1.5] text-void">
              {taxMemo}
            </p>
          ) : null}

          <div className="mt-10 flex justify-end sm:absolute sm:bottom-[-30px] sm:right-0 sm:mt-0 sm:block">
            <StampTotal total={summary.total || 0} currency={currency} />
          </div>
        </div>
      )}
    </section>
  )
}
