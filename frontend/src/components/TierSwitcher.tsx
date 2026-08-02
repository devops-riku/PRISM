import { formatMoney } from '../lib/format'
import { MONO_LABEL } from './tokens'
import type { ProposalBundle, TierSibling } from '../types'

/** Two tiers this close read as the same price to anyone comparing them. */
const INDISTINGUISHABLE = 0.02

/** A tier and the one below it, with the fraction between their totals. */
type CrowdedPair = {
  tier: TierSibling
  below: TierSibling
  gap: number
}

type TierSwitcherProps = {
  bundle: ProposalBundle
}

/**
 * The other tiers quoted from the same brief.
 *
 * A three-tier submission is three complete quotations, not one document with
 * options in it — the client compares them side by side and signs one. So each
 * is its own page, and this is how you get between them without going back to
 * the list.
 *
 * Totals are shown because comparing them is the entire point of quoting tiers.
 * They are resolved when the quotation is read rather than copied at creation,
 * so revising one tier updates the figure everywhere it appears.
 */
export default function TierSwitcher({ bundle }: TierSwitcherProps) {
  const unsorted = Array.isArray(bundle?.tier_siblings) ? bundle.tier_siblings : []
  if (unsorted.length < 2) return null
  const tiers = [...unsorted].sort((a, b) => a.tier_index - b.tier_index)

  // Tiers are priced top down now, each against the figure the one above came
  // back with, and the server holds every tier below the one above it. This is
  // the backstop: if it ever fires, something upstream stopped working.
  const inverted = tiers
    .slice(1)
    .map((tier, index) => (tier.total > 0 && tier.total <= tiers[index].total ? tier : null))
    .filter((entry): entry is TierSibling => Boolean(entry))

  // Ordered but too close to tell apart. The server guarantees the order; only
  // the scope can create a gap worth paying for, so a ladder that arrives with
  // no daylight between its steps is a scoping problem to look at, not an
  // arithmetic one to fix.
  const crowded = tiers
    .slice(1)
    .map((tier, index) => {
      const below = tiers[index]
      if (!(tier.total > 0 && below.total > 0)) return null
      const gap = (tier.total - below.total) / below.total
      return gap > 0 && gap < INDISTINGUISHABLE ? { tier, below, gap } : null
    })
    .filter((entry): entry is CrowdedPair => Boolean(entry))

  return (
    <nav
      aria-label="Tiers quoted from this brief"
      className="mx-auto mb-8 w-full max-w-sheet rounded-xl border border-rule bg-paper shadow-sheet px-6 py-5 sm:px-8"
    >
      <p className={MONO_LABEL}>Quoted at {tiers.length} tiers</p>

      <ul className="mt-3 grid gap-3 sm:grid-cols-3">
        {tiers.map((tier) => {
          const current = tier.id === bundle.id
          return (
            <li key={tier.id}>
              <a
                href={`#/q/${tier.id}`}
                aria-current={current ? 'page' : undefined}
                className={`block rounded-lg border px-4 py-3 no-underline ${
                  current
                    ? 'border-ballpoint bg-accent-soft'
                    : 'border-rule bg-paper hover:border-ballpoint'
                }`}
              >
                <span
                  className={`block font-label text-[12px] uppercase tracking-[0.14em] ${
                    current ? 'text-ballpoint' : 'text-faint'
                  }`}
                >
                  {tier.tier_name || `Tier ${tier.tier_index + 1}`}
                </span>
                <span className="mt-1 block font-label text-[18px] tabular-nums text-ink">
                  {formatMoney(tier.total, tier.currency)}
                </span>
              </a>
            </li>
          )
        })}
      </ul>

      {inverted.length > 0 ? (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-alert/30 bg-alert-soft px-4 py-3 font-body text-[15px] text-ink"
        >
          <span className="block font-label text-[12px] font-medium uppercase tracking-[0.14em] text-alert">
            Tiers are not quoted in order
          </span>
          <span className="mt-1 block">
            {inverted.map((tier) => tier.tier_name).join(' and ')}{' '}
            {inverted.length === 1 ? 'costs' : 'cost'} no more than the tier below. The server holds
            each tier under the one above it, so this should not be possible — check the API log
            before sending these together.
          </span>
        </p>
      ) : null}

      {bundle.tier_order_enforced ? (
        <p className="mt-4 rounded-lg border border-rule bg-duplicate/60 px-4 py-3 font-body text-[15px] text-body">
          <span className={MONO_LABEL}>Held under the tier above</span>
          <span className="mt-1 block">
            This tier came back quoted at or above the one above it, so its effort was brought down
            until it sat below. The rates did not move. Read the scope of both before sending them
            together — a tier that costs less should visibly contain less.
          </span>
        </p>
      ) : null}

      {crowded.length > 0 && !inverted.length ? (
        <p className="mt-4 rounded-lg border border-rule bg-duplicate/60 px-4 py-3 font-body text-[15px] text-body">
          <span className={MONO_LABEL}>Tiers are close together</span>
          <span className="mt-1 block">
            {crowded
              .map(
                (pair) =>
                  `${pair.tier.tier_name} is ${(pair.gap * 100).toFixed(1)}% above ${pair.below.tier_name}`,
              )
              .join(', ')}
            . A client who cannot tell two tiers apart by price will read them as one offer quoted
            twice.
          </span>
        </p>
      ) : null}
    </nav>
  )
}
