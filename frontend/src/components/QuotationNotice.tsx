import { formatMoney } from '../lib/format'
import type { CostSummary, ProposalBundle } from '../types'

type QuotationNoticeProps = {
  bundle: ProposalBundle
}

/**
 * Provenance strip above the sheets.
 *
 * Two things a reader needs before trusting a figure, and either can apply on
 * its own: where this quotation came from, and — when a target cost was set —
 * whether the number typed is the number that came out.
 *
 * The second is the honest part. With a tax percentage applied the reachable
 * totals step by more than one minor unit, so a small share of figures cannot
 * be hit exactly. When that happens the server lands on the nearest and says so
 * here rather than rounding quietly.
 *
 * Renders nothing for an original quotation with no target, which is the common
 * case and needs no explanation.
 */
export default function QuotationNotice({ bundle }: QuotationNoticeProps) {
  if (!bundle) return null

  const isRevision = Number(bundle.revision) >= 2
  const asked = Number(bundle.target_total) || 0
  const bound = Number(bundle.rate_card_bound) || 0
  const removed = Array.isArray(bundle.rate_card_removed) ? bundle.rate_card_removed : []
  const removedValue = Number(bundle.rate_card_removed_value) || 0
  if (!isRevision && asked <= 0 && bound === 0 && removed.length === 0) return null

  const currency = bundle.estimate?.currency || 'PHP'
  // `Partial` rather than `CostSummary`, because `|| {}` really can be the empty
  // object here and every read below already defaults for itself.
  const cost: Partial<CostSummary> = bundle.estimate?.cost || {}
  const landed = Number(cost.total) || 0
  const missed = asked > 0 && bundle.hit_target === false
  // Quoted tax-exclusive, the target is the price of the work and the total
  // carries the tax on top — so the two figures on this line are meant to
  // differ. Saying which is which is the difference between an explanation and
  // an apparent contradiction.
  const targetIsNet = !cost.tax_inclusive && Number(cost.tax_pct) > 0

  return (
    <aside
      aria-label={isRevision ? 'Revision details' : 'Target cost'}
      className="mx-auto mb-8 w-full max-w-sheet rounded-xl border border-rule bg-duplicate px-6 py-5 sm:px-8"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <p className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-ballpoint">
          {isRevision ? `Revision ${bundle.revision}` : 'Quoted to a target'}
        </p>
        {isRevision && bundle.parent_id ? (
          <p className="font-label text-[12px] tracking-[0.14em] text-void">
            <span className="uppercase">Revised from</span>{' '}
            {/* The parent's printed reference, which is the number the client
                already has. The id is storage and reads as noise; it is still
                what the link goes to, and it is what shows if the parent has
                been deleted and there is no reference left to name. */}
            <a
              href={`#/q/${bundle.parent_id}`}
              className="tabular-nums text-ballpoint underline underline-offset-[3px]"
            >
              {bundle.parent_ref || bundle.parent_id}
            </a>
          </p>
        ) : null}
      </div>

      {bundle.revision_instruction ? (
        <p className="mt-3 font-body text-[15px] text-ink">
          <span className="font-label text-[12px] uppercase tracking-[0.14em] text-void">
            Asked for
          </span>
          <span className="mt-1 block">{bundle.revision_instruction}</span>
        </p>
      ) : null}

      {asked > 0 ? (
        <p className="mt-3 font-label text-[13px] tabular-nums text-ink">
          <span className="text-[12px] uppercase tracking-[0.14em] text-void">Target</span>{' '}
          {formatMoney(asked, currency)}
          {targetIsNet ? (
            <span className="text-[12px] uppercase tracking-[0.14em] text-void">
              {' '}
              before {cost.tax_label || 'tax'}
            </span>
          ) : null}
          <span className="text-[12px] uppercase tracking-[0.14em] text-void">
            {missed ? ' · Landed on ' : ' · Met exactly at '}
          </span>
          {formatMoney(landed, currency)}
          {targetIsNet ? (
            <span className="text-[12px] uppercase tracking-[0.14em] text-void"> with tax</span>
          ) : null}
        </p>
      ) : null}

      {bound > 0 ? (
        <p className="mt-3 font-body text-[15px] text-ink">
          <span className="font-label text-[12px] uppercase tracking-[0.14em] text-void">
            Rate card
          </span>
          <span className="mt-1 block">
            {bound} line {bound === 1 ? 'item is' : 'items are'} quoted from your rate card.
          </span>
        </p>
      ) : null}

      {/* Removed scope is the loudest thing on this strip. The card is a closed
          list, so work quoted against a role nobody agreed a rate for is deleted
          — and a quotation that quietly got smaller is worse than a wrong rate. */}
      {removed.length > 0 ? (
        <div className="mt-4 rounded-lg border border-alert/30 bg-alert-soft px-4 py-3">
          <p className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-alert">
            {removed.length} {removed.length === 1 ? 'line' : 'lines'} removed
            {removedValue > 0 ? ` · ${formatMoney(removedValue, currency)}` : ''}
          </p>
          <p className="mt-1 font-body text-[15px] text-ink">
            Your rate card is a closed list, so these were dropped rather than quoted at a rate
            nobody agreed:
          </p>
          <ul className="mt-2 space-y-1 font-body text-[15px] text-body">
            {removed.map((reason) => (
              <li key={reason}>— {reason}</li>
            ))}
          </ul>
          <p className="mt-2 font-body text-[15px] text-void">
            Add the role in the admin panel and prepare the quotation again to quote that work.
          </p>
        </div>
      ) : null}

      {/* Shown whether or not the target was hit. It used to appear only on a
          miss, from when the only thing it could say was which figure the
          arithmetic settled for. It now also explains a target that was met
          exactly and still produced a larger total, because the tax went on top
          of it — which is the case a reader is most likely to query. */}
      {bundle.target_note ? (
        <p className="mt-3 border-l-2 border-l-ballpoint pl-4 font-body text-[15px] text-ink">
          {bundle.target_note}
        </p>
      ) : null}

      {/* A cap that could not be met. The server used to refuse the whole
          request; it now reports, because one tier that will not fit is no
          reason to throw away the other two. That only works if the report is
          actually shown — a quotation silently over its cap is the failure this
          project refuses everywhere else. */}
      {bundle.tier_cap_note ? (
        <div className="mt-4 rounded-lg border border-alert/30 bg-alert-soft px-4 py-3">
          <p className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-alert">
            Over the cap
            {bundle.tier_cap > 0 ? ` · ${formatMoney(bundle.tier_cap, currency)}` : ''}
          </p>
          <p className="mt-1 font-body text-[15px] text-ink">{bundle.tier_cap_note}</p>
        </div>
      ) : null}
    </aside>
  )
}
