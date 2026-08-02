"""The studio's standard payment terms, and the code that imposes them.

Left alone the model proposes a schedule per quotation, which is right when
nobody has said otherwise and wrong the moment a studio has terms it always
quotes. Two clients comparing notes should not find they were offered different
payment structures because a language model felt differently on a Tuesday.

Two shapes, because quotations come in both:

  * **a deposit, then the balance in equal payments** - the common case, and the
    one where the server owns the arithmetic. 30% then three payments divides
    into 23.33/23.33/23.34, and getting that remainder right is not the sender's
    problem.
  * **a schedule written out payment by payment** - 40/30/20/10, or anything
    else the client negotiated. It wins over the equal split when present, and
    it is *checked* rather than corrected: a schedule that totals 95% is a typo
    worth being told about, not a number to quietly move.

Neither is set by default. With no terms at all the model proposes a schedule,
which is right when nobody has said otherwise.
"""

from __future__ import annotations

import logging
from typing import List

from pydantic import BaseModel, Field

from app.schemas import Estimate, PaymentMilestone

logger = logging.getLogger("prism.payments")

__all__ = ["PaymentTerms", "ScheduleRow", "TermsError", "apply", "describe_for_prompt"]

_PCT_DP = 2

#: What the balance payments are pegged to. Wording only - the split is equal
#: either way - but the wording is what the client actually reads.
CADENCES = ("monthly", "phase", "milestone")


class ScheduleRow(BaseModel):
    """One payment in a hand-written schedule."""

    percent: float = Field(default=0.0, description="Share of the total, e.g. 40 for 40%.")
    trigger: str = Field(default="", description="What makes this payment payable.")

    def normalised(self) -> "ScheduleRow":
        return ScheduleRow(
            percent=max(0.0, round(float(self.percent or 0.0), _PCT_DP)),
            trigger=(self.trigger or "").strip()[:120],
        )


class TermsError(ValueError):
    """A schedule that does not add up. Surfaces to the caller as a 400."""


class PaymentTerms(BaseModel):
    """A deposit and equal instalments, or a schedule written out payment by payment."""

    deposit_pct: float = Field(
        default=0.0,
        description="Percent payable up front. 0 disables these terms and the model proposes its own.",
    )
    instalments: int = Field(
        default=3,
        description="How many equal payments follow the deposit.",
    )
    cadence: str = Field(
        default="monthly",
        description="What the balance payments are pegged to: monthly | phase | milestone.",
    )
    deposit_trigger: str = Field(
        default="Signed statement of work",
        description="What makes the deposit payable.",
    )
    schedule: List[ScheduleRow] = Field(
        default_factory=list,
        description=(
            "A schedule written out payment by payment. When present it wins over "
            "`deposit_pct` and `instalments`, and its percentages must total 100."
        ),
    )

    def normalised(self) -> "PaymentTerms":
        cadence = (self.cadence or "monthly").strip().lower()
        rows = [row.normalised() for row in (self.schedule or [])]
        rows = [row for row in rows if row.percent > 0]
        return PaymentTerms(
            # A deposit above 90 leaves nothing to instal, and a negative one is
            # not a deposit. Both are clamped rather than rejected - unlike the
            # written schedule below, where a wrong number is a mistake to point
            # at rather than one to quietly fix.
            deposit_pct=min(90.0, max(0.0, float(self.deposit_pct or 0.0))),
            instalments=min(24, max(1, int(self.instalments or 1))),
            cadence=cadence if cadence in CADENCES else "monthly",
            deposit_trigger=(self.deposit_trigger or "").strip()[:120] or "Signed statement of work",
            schedule=rows[:24],
        )

    def validated(self) -> "PaymentTerms":
        """Normalise, and refuse a written schedule that does not total 100.

        Deliberately not silently corrected. The equal split is arithmetic the
        server owns and can round on its own authority; a schedule somebody
        typed out is a decision, and 95% of a quotation is a typo worth being
        told about rather than a number to quietly move.
        """
        terms = self.normalised()
        if not terms.schedule:
            return terms

        total = round(sum(row.percent for row in terms.schedule), _PCT_DP)
        if abs(total - 100.0) >= 0.01:
            short = 100.0 - total
            direction = "short of" if short > 0 else "over"
            raise TermsError(
                f"The payment schedule adds up to {total:g}%, {abs(short):g}% {direction} 100%. "
                f"Adjust the {len(terms.schedule)} payments so they total exactly 100."
            )
        return terms

    @property
    def active(self) -> bool:
        return bool(self.schedule) or self.deposit_pct > 0

    def describe(self) -> str:
        if self.schedule:
            shares = " · ".join(f"{row.percent:g}%" for row in self.schedule)
            return f"{len(self.schedule)} payments of {shares}"
        payments = "payment" if self.instalments == 1 else "equal payments"
        return (
            f"{self.deposit_pct:g}% on signing, then the balance in "
            f"{self.instalments} {payments} ({self.cadence})"
        )


def describe_for_prompt(terms: PaymentTerms) -> str:
    """What the model is told, so it does not propose a schedule that gets replaced."""
    if not terms.active:
        return ""
    if terms.schedule:
        rows = "; ".join(
            f"{row.percent:g}% on {row.trigger or 'a trigger to be agreed'}"
            for row in terms.schedule
        )
        return (
            f"The payment schedule for this quotation is fixed and is not yours to change: "
            f"{rows}. Put exactly those milestones in cost.payment_milestones, in that order. "
            f"The server rebuilds the schedule regardless, so any other split you write is "
            f"discarded; matching it keeps your prose and the figures consistent."
        )

    return (
        f"This quotation has fixed payment terms and they are not yours to change: "
        f"{terms.describe()}. Put exactly that in cost.payment_milestones - a first "
        f"milestone of {terms.deposit_pct:g}% triggered by "
        f'"{terms.deposit_trigger}", then {terms.instalments} equal milestones for the '
        f"balance. The server rebuilds this schedule regardless, so any other split you "
        f"write is discarded; matching it keeps your prose and the figures consistent."
    )


def _triggers(terms: PaymentTerms, estimate: Estimate) -> List[str]:
    """A payable trigger per instalment, in the studio's chosen cadence."""
    count = terms.instalments

    if terms.cadence == "phase":
        # Real phase names beat "Payment 2" every time, but there is rarely one
        # phase per instalment - fall back for any that run out.
        names = [str(phase.name).strip() for phase in (estimate.phases or []) if str(phase.name).strip()]
        return [
            f"Acceptance of {names[index]}" if index < len(names) else f"Payment {index + 2} of {count + 1}"
            for index in range(count)
        ]

    if terms.cadence == "milestone":
        return [f"Agreed milestone {index + 1} accepted" for index in range(count)]

    return [f"End of month {index + 1}" for index in range(count)]


def apply(estimate: Estimate, terms: PaymentTerms) -> tuple[Estimate, bool]:
    """Replace the schedule with the studio's terms. Returns (estimate, applied).

    The input is not mutated. Amounts are left at zero: `costing.recompute` owns
    every figure in this system and derives them from these percentages and the
    total, which is what keeps the schedule summing to the total exactly.
    """
    terms = terms.normalised()
    if not terms.active:
        return estimate, False

    corrected = estimate.model_copy(deep=True)

    # A written schedule is used exactly as typed. It has already been checked
    # to total 100, so there is no remainder to absorb and nothing to round.
    if terms.schedule:
        count = len(terms.schedule)
        corrected.cost.payment_milestones = [
            PaymentMilestone(
                label="Deposit" if index == 1 and count > 1 else f"Payment {index} of {count}",
                percent=row.percent,
                trigger=row.trigger or f"Payment {index} becomes due",
            )
            for index, row in enumerate(terms.schedule, start=1)
        ]
        logger.info("Payment schedule applied: %s", terms.describe())
        return corrected, True

    balance = 100.0 - terms.deposit_pct
    share = round(balance / terms.instalments, _PCT_DP)
    percents = [share] * terms.instalments
    # The last instalment absorbs the rounding so the schedule sums to exactly
    # 100 rather than to 99.99.
    percents[-1] = round(balance - share * (terms.instalments - 1), _PCT_DP)

    milestones = [
        PaymentMilestone(
            label="Deposit",
            percent=round(terms.deposit_pct, _PCT_DP),
            trigger=terms.deposit_trigger,
        )
    ]
    for index, (percent, trigger) in enumerate(zip(percents, _triggers(terms, corrected)), start=1):
        milestones.append(
            PaymentMilestone(
                label=f"Payment {index} of {terms.instalments}",
                percent=percent,
                trigger=trigger,
            )
        )

    corrected.cost.payment_milestones = milestones
    logger.info(
        "Payment terms applied: %s -> %d milestones", terms.describe(), len(milestones)
    )
    return corrected, True
