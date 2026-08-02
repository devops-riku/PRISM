"""The studio's own rates, and the code that makes them binding.

The problem this solves: left alone, the model invents a plausible market rate
for every role it names. That is the right behaviour when nobody has said what
the studio actually charges - and it is the wrong behaviour the moment somebody
has. A quotation that prices a senior engineer at a number the studio does not
bill is worse than useless.

So: an empty card changes nothing, and the model keeps pricing from the
requirements exactly as before. A card with entries in it becomes the rate
list, injected into the prompt so the model scopes against real numbers, and
then enforced here so a rate that drifted anyway is corrected before it can
reach a document.

**Matching is the hard part**, and the reason this module exists rather than a
dict lookup at the call site. Left to itself the model writes free text -
"Senior Backend Engineer", "Sr. Backend Developer", "Backend Engineer (Senior)"
- so exact string equality would miss most lines and enforcement would silently
do nothing while the admin panel claimed otherwise. Two defences:

  1. The prompt hands the model the exact role names and tells it to use them
     verbatim. That makes most lines match on the nose.
  2. Anything that still misses is *reported*, not swallowed. `apply` returns
     the unmatched roles and the caller puts them on the bundle, so a line
     priced outside the card is visible rather than assumed.

Rates are never touched by anything else in the system. `snap_to_total` moves
quantities precisely so the rate story stays true, which is what makes applying
the card once, before costing, safe and stable.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Tuple

from pydantic import BaseModel, Field

from app.schemas import Estimate

logger = logging.getLogger("prism.ratecard")

__all__ = ["RoleRate", "UnitBasis", "apply", "normalise_role", "describe_for_prompt"]

_QUANTITY_DP = 2

_NOISE = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")

#: Words that carry no meaning when comparing two role names. "Senior Backend
#: Engineer" and "Sr Backend Engineer (Senior)" are the same job.
_FILLER = frozenset({"a", "an", "the", "of", "and", "level"})

#: Common ways of writing the same seniority or discipline, folded so a card
#: entry still binds when the model reaches for a synonym.
_SYNONYMS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "jnr": "junior",
    "mid": "midlevel",
    "midlevel": "midlevel",
    "intermediate": "midlevel",
    "lead": "principal",
    "staff": "principal",
    "architect": "principal",
    "dev": "engineer",
    "developer": "engineer",
    "programmer": "engineer",
    "engineering": "engineer",
    "designer": "design",
    "ux": "design",
    "ui": "design",
    "uiux": "design",
    "qa": "qa",
    "quality": "qa",
    "tester": "qa",
    "test": "qa",
    "pm": "projectmanager",
    "manager": "manager",
    "analyst": "analyst",
}


class UnitBasis(BaseModel):
    """One studio-wide working day. Kept only to read settings written before
    the basis moved onto each role - `StudioDefaults` folds it into the card and
    drops it. Nothing new should use this.
    """

    hours_per_day: float = Field(default=8.0, description="Billable hours in one working day.")
    days_per_week: float = Field(default=5.0, description="Working days in one week.")


class RoleRate(BaseModel):
    """One line of the studio's rate card, including what its unit means.

    "Per day" is meaningless until somebody says how long that day is, and one
    studio-wide answer was the wrong shape: a lead engineer on a monthly
    retainer and a designer on a day rate do not share a working month, and
    forcing them to made every month-priced role unconvertible - which meant
    silently dropped from the quotation. Each role now carries its own, so the
    conversion uses the definition the studio actually agreed for that role.

    Only time converts. An `item` or a `lump_sum` is a thing, not a duration.
    """

    role: str = Field(default="", description="How the studio names the job, e.g. 'Senior Backend Engineer'.")
    unit: str = Field(
        default="day",
        description=(
            "The period the rate buys: hour | day | week | month | item | lump_sum. "
            "`rate` is the charge for exactly one of these."
        ),
    )
    rate: float = Field(default=0.0, description="Charge for one `unit`, in the studio's default currency.")
    hours_per_day: float = Field(default=8.0, description="Billable hours in one day of this role.")
    days_per_week: float = Field(default=5.0, description="Working days in one week of this role.")
    days_per_month: float = Field(
        default=22.0,
        description="Working days in one month of this role. A retainer month is not 30 days.",
    )

    def normalised(self) -> "RoleRate":
        return RoleRate(
            role=(self.role or "").strip()[:80],
            unit=(self.unit or "day").strip().lower() or "day",
            rate=max(0.0, float(self.rate or 0.0)),
            hours_per_day=min(24.0, max(0.5, float(self.hours_per_day or 8.0))),
            days_per_week=min(7.0, max(1.0, float(self.days_per_week or 5.0))),
            days_per_month=min(31.0, max(1.0, float(self.days_per_month or 22.0))),
        )

    def hours_in(self, unit: str) -> float:
        """Hours in one `unit` of this role's time, or 0 when it is not a duration."""
        day = max(0.0, float(self.hours_per_day or 0.0))
        return {
            "hour": 1.0,
            "day": day,
            "week": day * max(0.0, float(self.days_per_week or 0.0)),
            "month": day * max(0.0, float(self.days_per_month or 0.0)),
        }.get((unit or "").lower(), 0.0)

    def describe_basis(self) -> str:
        """The working time this role's rate is quoted against, in words."""
        unit = (self.unit or "day").lower()
        day = f"{self.hours_per_day:g}"
        if unit == "week":
            return f"{day}h day, {self.days_per_week:g}-day week"
        if unit == "month":
            return f"{day}h day, {self.days_per_month:g}-day month"
        if unit in {"hour", "day"}:
            return f"{day}h day"
        return ""

    @property
    def usable(self) -> bool:
        return bool(self.role.strip()) and self.rate > 0


def normalise_role(value: str) -> str:
    """Fold a role name to a comparable key.

    Lowercases, drops punctuation and filler words, and maps the common
    synonyms onto one spelling, so "Sr. Backend Developer" and "Senior Backend
    Engineer" arrive at the same key. Word order is not preserved - the words
    are sorted - because "Backend Engineer (Senior)" is the same job written
    backwards.
    """
    text = _NOISE.sub(" ", (value or "").lower())
    words = [word for word in _SPACES.split(text) if word and word not in _FILLER]
    folded = sorted({_SYNONYMS.get(word, word) for word in words})
    return " ".join(folded)


def describe_for_prompt(card: Iterable[RoleRate], currency: str) -> str:
    """The rate list as the model should see it: exact names, units and rates."""
    usable = [entry for entry in card if entry.usable]
    if not usable:
        return ""

    lines = [
        f"{'ROLE':<40} {'UNIT':<8} {'RATE (' + currency + ')':>16}  WORKING TIME",
        f"{'-' * 40} {'-' * 8} {'-' * 16}  {'-' * 26}",
    ]
    for entry in usable:
        lines.append(
            f"{entry.role[:40]:<40} {entry.unit:<8} {entry.rate:>16,.2f}  {entry.describe_basis()}"
        )
    return "\n".join(lines)


def apply(
    estimate: Estimate,
    card: Iterable[RoleRate],
) -> Tuple[Estimate, List[str], int, float]:
    """Price the quotation from the card, and remove anything the card does not cover.

    Returns the corrected estimate, a description of every line that was
    removed, how many were bound, and the money those removals took off the
    quotation. The input is not mutated.

    **A configured card is a closed list.** A line item naming a role that is
    not on it is deleted - not repriced, not left alone. That is the studio's
    instruction: if the card lists no Principal Solutions Architect, no
    quotation may offer one at a number nobody agreed to. The prompt tells the
    model the same thing, so this should almost never fire; it is the guarantee
    behind the instruction rather than the normal path.

    An empty card disables all of it and the model's own pricing stands.

    Removal is deliberately loud. Every dropped line is described and its value
    returned, because scope quietly vanishing from a quotation is a far worse
    failure than a rate that needed correcting.
    """
    usable = [entry.normalised() for entry in card]
    usable = [entry for entry in usable if entry.usable]
    if not usable:
        return estimate, [], 0, 0.0

    by_key: dict[str, RoleRate] = {}
    for entry in usable:
        by_key.setdefault(normalise_role(entry.role), entry)

    corrected = estimate.model_copy(deep=True)
    removed: list[str] = []
    seen_removed: set[str] = set()
    removed_value = 0.0
    converted: list[str] = []
    kept = []
    bound = 0

    for item in corrected.line_items:
        label = (item.role or "").strip()
        entry = by_key.get(normalise_role(label)) if label else None
        item_unit = str(getattr(item.unit, "value", item.unit) or "").lower()

        # The unit has to agree, or the rate means something different. A day is
        # not reliably eight billable hours, so converting would be inventing a
        # number; the line goes instead.
        reason = ""
        if entry is None:
            reason = f"{label or 'a line with no role'} is not on the rate card"
        elif item_unit != entry.unit:
            # Both durations? Then the working day defines the exchange and the
            # line is re-expressed in the card's own unit rather than dropped.
            # 12 hours at a 24,500 day rate becomes 1.5 days at 24,500, which is
            # the same money and the unit the studio actually bills in.
            # The card entry's own working time defines the exchange, because it
            # is the definition the studio agreed for this role. A retainer
            # month of 22 days converts differently from one of 20, and using a
            # single studio-wide number for both would put a figure in a
            # quotation that nobody agreed to.
            from_hours = entry.hours_in(item_unit)
            to_hours = entry.hours_in(entry.unit)
            if from_hours > 0 and to_hours > 0:
                quantity = max(0.0, float(item.quantity or 0.0))
                item.quantity = round(quantity * from_hours / to_hours, _QUANTITY_DP)
                item.unit = entry.unit
                converted.append(
                    f"{entry.role}: {item_unit}s converted to {entry.unit}s at "
                    f"{entry.describe_basis()}"
                )
            else:
                reason = (
                    f"{entry.role} was quoted per {item_unit}, the card is per {entry.unit}, "
                    f"and neither is a length of time that can be converted"
                )

        if reason:
            value = float(item.quantity or 0.0) * float(item.unit_rate or 0.0)
            removed_value += value
            if reason.lower() not in seen_removed:
                seen_removed.add(reason.lower())
                removed.append(reason)
            logger.warning(
                "Rate card: dropped %s (%s), %.2f removed from the quotation",
                item.id,
                reason,
                value,
            )
            continue

        if abs(item.unit_rate - entry.rate) >= 0.005:
            logger.info(
                "Rate card: %s %s %.2f -> %.2f", item.id, label, item.unit_rate, entry.rate
            )
        item.unit_rate = entry.rate
        item.role = entry.role  # the studio's spelling wins in the document
        kept.append(item)
        bound += 1

    corrected.line_items = kept
    return corrected, removed, bound, round(removed_value, 2)
