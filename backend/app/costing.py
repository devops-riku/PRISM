"""Server-side arithmetic. The model's numbers are advisory; these are binding.

`recompute` is the only place in the system allowed to decide what a document's
totals are. A rendered proposal must never show a total that differs from the
sum of its rows, and the milestone amounts must sum to the total exactly - not
to within a rounding cent.

What this module deliberately does NOT do: author. The 8-15 percent contingency
band, the choice of tax, the number of milestones and their weighting are the
model's judgement, expressed through the prompt. Costing corrects arithmetic,
normalises self-contradictions, clamps nonsense to zero, and fills in missing
ids. It never invents a line, a tax, or a percentage the model did not choose.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.schemas import Estimate

#: ISO 4217 currencies with zero minor units.
#:
#: Costing has to round to the precision the documents are *printed* at. A yen
#: figure rounded to the cent and then printed at zero decimals gives a total
#: that disagrees with the sum of its own rows, which is exactly what this
#: module exists to prevent.
#:
#: Deliberately duplicated from `app.renderers.money.ZERO_DECIMAL_CURRENCIES`
#: rather than imported: the renderers are pure functions *downstream* of a
#: corrected Estimate, and costing must not depend on them. Keep the two lists
#: in step - a code added to one belongs in the other.
_ZERO_DECIMAL_CURRENCIES = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG",
        "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)

_DEFAULT_MONEY_DP = 2
_PCT_DP = 2

#: Decimals a quantity is printed at, matching `renderers.money.format_qty`.
#: Snapping rounds to this so a printed effort multiplies up to its printed
#: amount on every row but the one carrying the correction.
_QUANTITY_DP = 2

_NON_ALPHA = re.compile(r"[^A-Za-z]+")


def money_decimals(currency: object) -> int:
    """Fractional digits this currency is written with - and rounded to.

    An unrecognised or missing code falls back to two, which is right for every
    currency PRISM offers that is not on the zero-decimal list.
    """
    letters = _NON_ALPHA.sub("", "" if currency is None else str(currency)).upper()
    return 0 if letters[:3] in _ZERO_DECIMAL_CURRENCIES else _DEFAULT_MONEY_DP


# --- Small numeric helpers ---------------------------------------------------


def _finite(value: float) -> float:
    """NaN and infinity survive JSON parsing in some encoders. They stop here."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _non_negative(value: float) -> float:
    number = _finite(value)
    return number if number > 0 else 0.0


def _money(value: float, dp: int = _DEFAULT_MONEY_DP) -> float:
    return round(_finite(value), dp)


# --- Identifier backfill -----------------------------------------------------


def _next_free(prefix: str, used: set[str], counter: int) -> tuple[str, int]:
    while True:
        candidate = f"{prefix}-{counter:02d}"
        counter += 1
        if candidate not in used:
            return candidate, counter


def _assign_line_item_ids(estimate: Estimate) -> None:
    """Fill blank line item ids with LI-nn. Never renumber an id that already exists.

    `LineItem.requirement_ids` and `Phase.line_item_ids` are strings pointing at
    these values; reassigning one silently breaks every cross-reference in both
    documents.
    """
    used = {item.id.strip() for item in estimate.line_items if item.id.strip()}
    counter = 1
    for item in estimate.line_items:
        existing = item.id.strip()
        if existing:
            item.id = existing
            continue
        item.id, counter = _next_free("LI", used, counter)
        used.add(item.id)


def _assign_requirement_ids(estimate: Estimate) -> None:
    """Fill blank requirement ids with FR-nn / NFR-nn, series chosen by `type`."""
    used = {req.id.strip() for req in estimate.requirements if req.id.strip()}
    counters = {"FR": 1, "NFR": 1}
    for req in estimate.requirements:
        existing = req.id.strip()
        if existing:
            req.id = existing
            continue
        prefix = "FR" if req.type.value == "functional" else "NFR"
        req.id, counters[prefix] = _next_free(prefix, used, counters[prefix])
        used.add(req.id)


def _dedupe_known(references: list[str], known: set[str]) -> list[str]:
    """Keep order, drop duplicates, drop references to ids that do not exist."""
    seen: set[str] = set()
    kept: list[str] = []
    for ref in references:
        value = (ref or "").strip()
        if value and value in known and value not in seen:
            seen.add(value)
            kept.append(value)
    return kept


def _prune_dangling_references(estimate: Estimate) -> None:
    """A reference to a requirement or line item that does not exist renders as a dead id."""
    requirement_ids = {req.id for req in estimate.requirements if req.id}
    line_item_ids = {item.id for item in estimate.line_items if item.id}

    for item in estimate.line_items:
        item.requirement_ids = _dedupe_known(item.requirement_ids, requirement_ids)
    for phase in estimate.phases:
        phase.line_item_ids = _dedupe_known(phase.line_item_ids, line_item_ids)


# --- Money -------------------------------------------------------------------


def _recompute_line_items(estimate: Estimate, dp: int) -> float:
    subtotal = 0.0
    for item in estimate.line_items:
        item.quantity = _non_negative(item.quantity)
        item.unit_rate = _non_negative(item.unit_rate)
        item.subtotal = _money(item.quantity * item.unit_rate, dp)
        subtotal += item.subtotal
    return _money(subtotal, dp)


def _recompute_cost(estimate: Estimate, dp: int) -> None:
    cost = estimate.cost

    cost.subtotal = _recompute_line_items(estimate, dp)

    cost.contingency_pct = _non_negative(cost.contingency_pct)
    cost.contingency_amount = _money(cost.subtotal * cost.contingency_pct / 100.0, dp)

    gross = _money(cost.subtotal + cost.contingency_amount, dp)

    # A discount can reduce the bill to zero but never below it. It is rounded
    # like every other figure here: it prints as its own row in the cost table,
    # so an unrounded value would make that table stop adding up.
    cost.discount_amount = _money(min(_non_negative(cost.discount_amount), gross), dp)

    net = _money(gross - cost.discount_amount, dp)
    if net < 0:
        net = 0.0

    # The schema states that an empty `tax_label` means "no tax line". Honour that
    # in both directions so a renderer never has to print an unlabelled tax row
    # or a zero-rated one: label and percent are either both meaningful or both empty.
    cost.tax_label = (cost.tax_label or "").strip()
    cost.tax_pct = _non_negative(cost.tax_pct)
    if not cost.tax_label:
        cost.tax_pct = 0.0
    elif cost.tax_pct == 0.0:
        cost.tax_label = ""

    rate = cost.tax_pct / 100.0
    if cost.tax_inclusive:
        # The rates already contain the tax, so the priced work IS the total and
        # the tax is a portion of it rather than an addition to it. Everything a
        # document prints in its cost column still sums to `total`; the tax is a
        # memo below the line, not a row in the addition.
        cost.total = net
        cost.tax_amount = _money(net - net / (1.0 + rate), dp) if rate else 0.0
    else:
        cost.tax_amount = _money(net * rate, dp)
        cost.total = _money(net + cost.tax_amount, dp)


def _recompute_milestones(estimate: Estimate, dp: int) -> None:
    """Normalise percents to sum to 100 and derive amounts that sum to the total exactly.

    Amounts are produced by cumulative rounding: each milestone's amount is the
    difference between the rounded running total at this milestone and at the
    previous one. That guarantees three things at once - every amount is within
    one minor unit of its exact share, no amount is negative, and the sum equals
    `cost.total` exactly, with the remainder landing on the last milestone.

    `dp` is the currency's number of minor-unit digits, so a schedule quoted in
    yen produces whole-yen milestones that still sum to a whole-yen total.
    """
    milestones = estimate.cost.payment_milestones
    if not milestones:
        return

    total = estimate.cost.total
    percents = [_non_negative(milestone.percent) for milestone in milestones]
    percent_sum = sum(percents)

    if percent_sum <= 0:
        # Every percent was missing or zero. An even split is the only reading
        # that keeps the schedule payable; anything else would drop the money.
        percents = [100.0 / len(milestones)] * len(milestones)
    else:
        percents = [value * 100.0 / percent_sum for value in percents]

    # Round for display, then put the remainder on the last one so the printed
    # percentages themselves sum to exactly 100.
    percents = [round(value, _PCT_DP) for value in percents]
    percents[-1] = round(100.0 - sum(percents[:-1]), _PCT_DP)
    if percents[-1] < 0:
        percents[-1] = 0.0

    running = 0.0
    cumulative_pct = 0.0
    last_index = len(milestones) - 1
    for index, (milestone, percent) in enumerate(zip(milestones, percents)):
        cumulative_pct = min(cumulative_pct + percent, 100.0)
        cumulative_amount = (
            total if index == last_index else _money(total * cumulative_pct / 100.0, dp)
        )
        amount = _money(cumulative_amount - running, dp)
        if amount < 0:
            amount = 0.0
        milestone.percent = percent
        milestone.amount = amount
        running = _money(running + amount, dp)


# --- Public entry point ------------------------------------------------------


def recompute(estimate: Estimate) -> Estimate:
    """Return a corrected copy of `estimate`. The input is not mutated.

    Every money figure is rounded to `estimate.currency`'s own number of minor
    units - two for most currencies, zero for yen, won, dong and the rest of the
    ISO 4217 zero-decimal list. That is the precision the renderers print at, so
    the printed rows and the printed total are arithmetic on the same numbers.

    Guarantees on the result, where `dp = money_decimals(estimate.currency)`:
      * every `LineItem.subtotal == round(quantity * unit_rate, dp)`
      * `cost.subtotal == sum(line item subtotals)`
      * `cost.total == subtotal + contingency - discount + tax`, all at `dp`
      * `sum(milestone.percent) == 100` and `sum(milestone.amount) == cost.total`
      * every line item and requirement has an id, and no cross-reference is dangling
      * no negative money anywhere
    """
    corrected = estimate.model_copy(deep=True)

    _assign_requirement_ids(corrected)
    _assign_line_item_ids(corrected)
    _prune_dangling_references(corrected)

    dp = money_decimals(corrected.currency)
    _recompute_cost(corrected, dp)
    _recompute_milestones(corrected, dp)

    for phase in corrected.phases:
        phase.duration_weeks = _non_negative(phase.duration_weeks)

    if corrected.client.validity_days <= 0:
        corrected.client.validity_days = 30

    return corrected


# --- Snapping a revision to an exact total ------------------------------------


class CostingError(ValueError):
    """A target total that no adjustment of this estimate can reach."""


@dataclass(frozen=True)
class SnapResult:
    """What snapping to a target actually achieved.

    `exact` is False only when the target is unreachable by construction rather
    than by any failure to try - see `snap_to_total`. `estimate` is always the
    closest reachable quotation, so a caller can ship it and report the miss.
    """

    estimate: Estimate
    target: float
    achieved: float
    exact: bool
    note: str = ""


#: Units whose quantities are naturally fractional. An anchor measured in these
#: hides the correction: 16.46 hours reads as an estimate, 16.46 days reads as
#: arithmetic showing through.
_FRACTIONAL_UNITS = frozenset({"hour", "item"})

#: An anchor has to be big enough that the residual disappears into it. A line
#: worth less than this share of the quotation is not worth the tidier unit.
_ANCHOR_MIN_SHARE = 0.1

#: How much of the anchor's own value it will absorb before the correction is
#: spread across every line instead. A shortfall inside this share is a rounding
#: correction and belongs on one row; anything larger is a real re-scope and
#: should move the whole quotation.
_ANCHOR_ABSORB_SHARE = 0.25


def _tax_multiplier(cost) -> float:
    """How much the tax multiplies the priced work by, on the way to the total.

    Inclusive pricing does not multiply anything - the tax is already inside the
    rates - so the factor is 1. Exclusive pricing adds the tax on top.
    """
    if cost.tax_inclusive:
        return 1.0
    return 1.0 + _non_negative(cost.tax_pct) / 100.0


def gross_for_target(estimate: Estimate, target: float) -> float:
    """The printed total a typed figure is asking for, under this tax basis.

    A figure the studio types - a target cost, a cap - means the same thing the
    rates beside it mean. Quoting tax-exclusive, every rate is net and so is the
    target: type 3,000,000 with VAT at 12% and the client is being quoted
    3,000,000 for the work plus 360,000 of VAT, which is what "exclusive" says
    on the form. Quoting inclusive, the tax is already inside every rate, so the
    typed figure is the total itself and nothing is added.

    Solving a net target as though it were the total is the same arithmetic
    error as quoting VAT-inclusive rates and calling them net: it silently
    shrinks the work by the tax rate. That was the bug this exists to close.
    """
    goal = _non_negative(target)
    if goal <= 0:
        return 0.0
    return _money(goal * _tax_multiplier(estimate.cost), money_decimals(estimate.currency))


def _anchor_id(estimate: Estimate) -> str:
    """Id of the line item that carries the last few units of the correction.

    Only one line can hold the correction, and its quantity is the one figure in
    the document that will not multiply cleanly against its own printed amount.
    So the choice is about which row wears that best.

    Prefer a line measured in hours or items, where a fractional quantity is
    what a reader expects anyway, provided it is a big enough share of the
    quotation to absorb the residual without distorting it. Otherwise take the
    largest line, which has the most room.
    """
    priced = [
        item
        for item in estimate.line_items
        if _non_negative(item.unit_rate) > 0 and _non_negative(item.quantity) > 0
    ]
    if not priced:
        raise CostingError(
            "This quotation has no priced line item to adjust, so its total cannot be moved."
        )

    largest = max(priced, key=lambda item: item.subtotal)
    threshold = largest.subtotal * _ANCHOR_MIN_SHARE

    fractional = [
        item
        for item in priced
        if str(getattr(item.unit, "value", item.unit)).lower() in _FRACTIONAL_UNITS
        and item.subtotal >= threshold
    ]
    if fractional:
        return max(fractional, key=lambda item: item.subtotal).id
    return largest.id


def _find(estimate: Estimate, item_id: str):
    for item in estimate.line_items:
        if item.id == item_id:
            return item
    raise CostingError("The line item being adjusted disappeared during recosting.")


def snap_to_total(estimate: Estimate, target_total: float, *, max_passes: int = 8) -> SnapResult:
    """Move an estimate onto `target_total` and report exactly what it hit.

    Asking a language model for a quotation that lands on a round number gets
    you close and never exact - it is writing prose, not solving for a figure.
    So the model re-scopes and this function does the arithmetic.

    The correction is applied to line item **quantities**, which is the only
    place it can survive. `recompute` treats quantity, unit_rate, the two
    percentages and the discount as inputs, and derives every subtotal, the
    contingency, the tax, the total and the milestone amounts from them. A
    residual parked on any derived field would be erased the next time an
    estimate was recosted - on save, on reload, on the next revision.

    Rates are never touched. They carry the market-rate story in
    `cost.rate_basis`, and a quotation that quietly re-prices a senior engineer
    to make a number work is lying about something a client can check.

    How it lands exactly:
      1. Invert the forward calculation to get the subtotal the target implies,
         then scale every priced quantity toward it. That spreads the change
         proportionally instead of dumping it on one line.
      2. Recost, measure what is still missing, and move the largest line's
         quantity by that much. Repeat - two passes is typical, because each
         pass only has to correct the previous pass's rounding.
      3. Keep the closest quotation seen, and report whether it is exact.

    Not every figure is reachable, and that is arithmetic rather than effort.
    With VAT at 12 percent the total is `round(taxable * 1.12, 2)`, and taxable
    only moves in whole cents, so consecutive totals step by about 1.12 cents
    and roughly one cent value in nine is skipped entirely. Round targets - the
    ones people actually ask for - are almost always reachable. When one is not,
    this returns the nearest reachable total with `exact=False` and a note
    saying so, because shipping a quotation one centavo out beats refusing to
    produce one.

    Raises `CostingError` only when no adjustment could work at all: a target
    of zero or less, an estimate with nothing priced to scale, or a correction
    that would take a line item below zero.
    """
    corrected = recompute(estimate)
    dp = money_decimals(corrected.currency)
    tolerance = 10.0**-dp / 2.0
    target = _money(target_total, dp)

    if target <= 0:
        raise CostingError("A target total has to be greater than zero.")

    anchor_id = _anchor_id(corrected)

    # 1. Invert `_recompute_cost`, which has two forms:
    #      exclusive: total = ((subtotal * (1 + c)) - discount) * (1 + t)
    #      inclusive: total =  (subtotal * (1 + c)) - discount
    #    Getting this wrong does not fail loudly - the loop below measures the
    #    real gap each pass and would still converge, just slowly - so it is
    #    tested directly rather than only through the end result.
    cost = corrected.cost
    contingency = 1.0 + _non_negative(cost.contingency_pct) / 100.0
    tax = _tax_multiplier(cost)
    required_subtotal = (target / tax + cost.discount_amount) / contingency

    # A discount raises the work a target implies rather than lowering it, and
    # both percentages are clamped non-negative, so this cannot go negative on
    # any well-formed estimate. It stays as a guard against a future change to
    # the forward calculation that this inversion was not updated for.
    if required_subtotal <= 0:
        raise CostingError(
            f"A total of {target:,.{dp}f} implies no priced work at all on this quotation."
        )
    if cost.subtotal <= 0:
        raise CostingError("This quotation prices no work, so its total cannot be scaled.")

    # Only spread the change when it is big enough to be a genuine re-scope. A
    # small correction scaled across every line turns the model's considered
    # quantities - 8.5 days, 13 days - into 8.49 and 12.99, which reads as
    # arithmetic residue rather than judgement. Below the threshold the anchor
    # absorbs the whole thing and every other row keeps the number the estimator
    # actually chose.
    shortfall = required_subtotal - cost.subtotal
    anchor_subtotal = _find(corrected, anchor_id).subtotal
    if abs(shortfall) > _ANCHOR_ABSORB_SHARE * anchor_subtotal:
        scale = required_subtotal / cost.subtotal
        for item in corrected.line_items:
            if _non_negative(item.unit_rate) <= 0:
                continue
            scaled = _non_negative(item.quantity) * scale
            # Quantities print at two decimals, so one carrying more precision
            # than that gives a row whose printed effort does not multiply up to
            # its printed amount. Round every line to what it will show; the
            # anchor keeps full precision because it has to land the total.
            item.quantity = scaled if item.id == anchor_id else round(scaled, _QUANTITY_DP)
        corrected = recompute(corrected)

    # 2. Close the remaining gap on one line. Each pass corrects the rounding
    #    the pass before it introduced, so the gap shrinks by orders of magnitude.
    #    Keep the best quotation seen - the search can step past an unreachable
    #    target and oscillate around it, and the closest pass is the answer.
    best = corrected
    best_gap = abs(target - corrected.cost.total)

    for _ in range(max_passes):
        gap = target - corrected.cost.total
        if abs(gap) < tolerance:
            break

        cost = corrected.cost
        contingency = 1.0 + _non_negative(cost.contingency_pct) / 100.0
        tax = _tax_multiplier(cost)

        # Mutate a copy, never `corrected` itself: `best` may still be pointing
        # at it from an earlier pass, and editing a quantity in place there
        # would leave that estimate holding a quantity its own subtotal was
        # never derived from.
        working = corrected.model_copy(deep=True)
        anchor = _find(working, anchor_id)
        if anchor.unit_rate <= 0:
            raise CostingError("The line item being adjusted has no rate to adjust against.")

        adjusted = anchor.quantity + (gap / tax / contingency) / anchor.unit_rate
        if adjusted < 0:
            raise CostingError(
                f"A total of {target:,.{dp}f} is too low for this scope - it would take "
                f"{anchor.description or anchor.id!r} below zero. Cut scope in the "
                "instruction instead of forcing the number."
            )
        anchor.quantity = adjusted
        corrected = recompute(working)

        if abs(target - corrected.cost.total) < best_gap:
            best = corrected
            best_gap = abs(target - corrected.cost.total)

    if best_gap < tolerance:
        return SnapResult(
            estimate=best, target=target, achieved=_money(best.cost.total, dp), exact=True
        )

    # The target is unreachable, so "closest" has to be earned rather than
    # assumed. The gap-driven search can settle on the far side of an
    # unreachable value; probe the neighbouring quantities directly, one
    # minor unit of total at a time, and keep whichever is genuinely nearest.
    settled = _find(best, anchor_id)
    base_quantity = settled.quantity
    cost = best.cost
    contingency = 1.0 + _non_negative(cost.contingency_pct) / 100.0
    tax = _tax_multiplier(cost)
    quantity_step = (10.0**-dp) / (tax * contingency * settled.unit_rate)

    for offset in range(-4, 5):
        if offset == 0:
            continue
        probe = best.model_copy(deep=True)
        probe_anchor = _find(probe, anchor_id)
        probe_anchor.quantity = base_quantity + offset * quantity_step
        if probe_anchor.quantity < 0:
            continue
        probe = recompute(probe)
        gap = abs(target - probe.cost.total)
        if gap < best_gap:
            best, best_gap = probe, gap

    if best_gap < tolerance:
        return SnapResult(
            estimate=best, target=target, achieved=_money(best.cost.total, dp), exact=True
        )

    achieved = _money(best.cost.total, dp)

    # Name whatever is actually creating the lattice. The subtotal moves in whole
    # minor units, and every percentage applied on the way to the total widens
    # that step past one - so some totals cannot be produced at all. Contingency
    # does this as much as tax does, and in inclusive mode it is the only one.
    contingency_pct = _non_negative(best.cost.contingency_pct)
    tax_pct = 0.0 if best.cost.tax_inclusive else _non_negative(best.cost.tax_pct)
    step = (1.0 + contingency_pct / 100.0) * (1.0 + tax_pct / 100.0)

    causes = []
    if contingency_pct:
        causes.append(f"contingency at {contingency_pct:g}%")
    if tax_pct:
        causes.append(f"{best.cost.tax_label or 'tax'} at {tax_pct:g}%")
    because = " and ".join(causes) if causes else "the rounding on this quotation"
    unit = "whole-unit" if dp == 0 else "whole-cent"

    note = (
        f"{target:,.{dp}f} is not a reachable total for this quotation. Applying {because} "
        f"multiplies a {unit} figure by {step:g}, so the totals it can produce skip some "
        f"values. The closest is {achieved:,.{dp}f}."
    )
    return SnapResult(estimate=best, target=target, achieved=achieved, exact=False, note=note)


# --- Absorbing the contingency ------------------------------------------------


def absorb_contingency(estimate: Estimate) -> Estimate:
    """Fold the contingency into the priced work so the client never sees a line for it.

    Hiding the row is not enough on its own. Every document in this system holds
    to one rule - the printed rows add up to the printed total - so a row that
    simply disappears would leave the arithmetic visibly wrong. The buffer has
    to go somewhere.

    It goes into the **quantities**, which is what a contingency actually is: an
    estimator's judgement that the work will take longer than the ideal case. A
    line of 6.5 days at 10 percent becomes 7.15 days at the same rate. Three
    things follow, all of them wanted:

      * the rates are untouched, so a rate card the client can check still holds
      * quantity x rate still equals the printed amount, row by row
      * the total does not move by a centavo

    The alternative - scaling the amounts and leaving quantities alone - would
    print rows a client could multiply and find wrong, and the alternative to
    that - raising the rates - would contradict the rate card outright.

    The percentage is recorded in `cost.contingency_absorbed_pct` so the
    developer sheet can still say the effort carries a buffer, and so this is
    idempotent: once absorbed, `contingency_pct` is zero and a second call does
    nothing.
    """
    corrected = estimate.model_copy(deep=True)
    cost = corrected.cost

    percent = _non_negative(cost.contingency_pct)
    if percent <= 0:
        return corrected

    target = cost.total
    factor = 1.0 + percent / 100.0
    for item in corrected.line_items:
        if _non_negative(item.unit_rate) > 0:
            item.quantity = round(_non_negative(item.quantity) * factor, _QUANTITY_DP)

    # Keep a running total of what has been folded in, so absorbing twice on a
    # revision reports the whole buffer rather than only the last slice.
    cost.contingency_absorbed_pct = round(
        _non_negative(cost.contingency_absorbed_pct) + percent, _PCT_DP
    )
    cost.contingency_pct = 0.0
    cost.contingency_amount = 0.0
    corrected = recompute(corrected)

    # Rounding every scaled quantity to the two decimals it prints at moves the
    # total by a centavo or two. The client must not pay a different number
    # because of a presentation choice, so it is put back exactly - by the same
    # solver a target total uses, which corrects one line's quantity rather than
    # inventing a second mechanism.
    if target > 0 and abs(corrected.cost.total - target) >= 10.0 ** -money_decimals(
        corrected.currency
    ) / 2.0:
        try:
            corrected = snap_to_total(corrected, target).estimate
        except CostingError:  # pragma: no cover - nothing priced to adjust
            pass

    return corrected
