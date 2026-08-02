"""The client's own view of an intake - the security boundary of Stage 2.

Every other module that touches an intake or a quotation speaks the studio's
language: line items, unit rates, the rate card and what it removed, tier
siblings, the note explaining why a target could not be hit, whether a model
is mid-run or has already failed once. None of that is for the person on the
other end of a link the studio sent them. `of()` is the one place a
client-facing dict is ever built, and it is built the slow way on purpose:
one dict literal per state, every field written out by its own name, never a
`model_dump()`, never `dict(bundle)`, never a comprehension that copies an
upstream object's own keys forward. A handler that instead filtered
`ProposalBundle` or `Intake` would leak the day somebody added a field to
either for a completely unrelated reason - that is exactly how a margin or a
rate card ends up in front of a client, and it is the one mistake this file
exists to make structurally impossible.

Nothing here resolves a token, borrows a workspace, or reads `bundle_ids` to
find which bundle to use - the caller (Task 3's anonymous route) has already
done that by the time `of()` runs. This module is a pure projection of the
two objects it is handed.
"""

from __future__ import annotations

from typing import Optional

from app import settings, storage
from app.intakes import (
    CLOSED,
    FINALIZED,
    Intake,
    ISSUED,
    PREPARING,
    QUOTED,
    QUOTE_FAILED,
    REVISION_REQUESTED,
    SENT,
    SUBMITTED,
)
from app.schemas import ProposalBundle

#: The four studio-side states between "the client opened the link" and "the
#: studio sent a quotation". One identical face for all four, by design: a
#: client must never be able to tell a model is currently running from one
#: that has already failed once, so `of()` hands back the same shape whichever
#: of the four it actually is.
_WAITING = {SUBMITTED, PREPARING, QUOTED, QUOTE_FAILED}

#: States in which there is a quotation to show. `sent`, `revision_requested`
#: and `finalized` all read the *current* quotation - `bundle_ids` replaces
#: rather than appends on a second Generate, so there is only ever one to show
#: regardless of which of the three states this is.
_QUOTED_FACE = {SENT, REVISION_REQUESTED, FINALIZED}


def _masked(email: str) -> str:
    """The first letter and the domain - enough for a client to recognise
    their own address, not enough to publish it to whoever else a forwarded
    link reaches.

    A direct port of `frontend/src/components/InviteScreen.tsx`'s `masked()`,
    kept byte-for-byte the same in behaviour so a client's own address is
    masked identically whether they are looking at an invitation or their own
    intake.
    """
    text = email or ""
    name, sep, domain = text.partition("@")
    if not sep:
        return text
    head = name[:1]
    dots = "•" * max(3, min(len(name) - 1, 6))
    return f"{head}{dots}@{domain}"


def of(intake: Intake, bundle: Optional[ProposalBundle] = None) -> dict:
    """The dict a client is allowed to see of one intake, and nothing else.

    `bundle` is read only for the three quotation states below; every other
    branch returns before touching it at all, so passing `None` is correct
    and expected for `issued`, the four waiting states, and `closed`.

    Each state gets its own dict literal rather than one shared skeleton
    patched per branch: sharing a base would let a field added for one state
    silently widen another's, which is exactly the kind of leak this module
    exists to prevent.
    """
    if intake.state == ISSUED:
        # The form has not been submitted. Nothing about the client's own
        # words exists yet to leak, so the studio's name is the whole face.
        return {
            "state": intake.state,
            "studio_name": settings.load().studio_name,
        }

    if intake.state in _WAITING:
        # One honest sentence's worth of material: who has it, when they sent
        # it, their own address (masked), and how much they wrote. Never the
        # budget, and never anything that would tell a client a model is
        # running right now or that a pass already failed - `preparing` and
        # `quote_failed` read exactly like `submitted` and `quoted` from here.
        return {
            "state": intake.state,
            "studio_name": settings.load().studio_name,
            "sent_at": intake.created_at,
            "email": _masked(intake.client_email),
            "scope_length": len(intake.scope),
        }

    if intake.state in _QUOTED_FACE:
        if bundle is None:
            # A caller error, not a client-facing one: whatever resolved this
            # intake to a state that promises a quotation was supposed to
            # resolve the quotation too, via `sent_bundle_id`. Failing loudly
            # here is better than this function inventing zeros for a
            # quotation that was never actually attached.
            raise ValueError(f"A {intake.state} intake must carry its quotation bundle.")

        estimate = bundle.estimate

        # Built by hand from `PaymentMilestone`'s own four fields - not
        # `milestone.model_dump()` - so a field added to that model later does
        # not ride along into a client's browser unreviewed.
        payment_schedule = [
            {
                "label": milestone.label,
                "percent": milestone.percent,
                "amount": milestone.amount,
                "trigger": milestone.trigger,
            }
            for milestone in estimate.cost.payment_milestones
        ]

        # Same reasoning for the revision log: each entry is already only
        # ever written as `{"asked": ..., "at": ...}` by `advance()`, but
        # naming the two keys explicitly here means a third key could never
        # reach a client even if something upstream started writing one.
        revisions = [
            {"asked": entry.get("asked", ""), "at": entry.get("at", "")}
            for entry in intake.revisions
        ]

        return {
            "state": intake.state,
            "studio_name": settings.load().studio_name,
            "reference": estimate.quotation_ref,
            "total": estimate.cost.total,
            "currency": estimate.currency,
            "validity": estimate.client.validity_days,
            "payment_schedule": payment_schedule,
            # The client-facing markdown that already exists on the bundle -
            # the requirements sheet (the developer spec) is never referenced
            # anywhere in this module.
            "narrative": storage.markdown_for(bundle, "proposal") or "",
            "sent_at": intake.sent_at,
            "revisions": revisions,
            "can_revise": intake.state == SENT,
            "can_finalize": intake.state == SENT,
        }

    if intake.state == CLOSED:
        # Nothing further to show - a withdrawn request. Task 3 makes this
        # indistinguishable from a token that never resolved at all; until
        # then, this is simply the truth.
        return {"state": intake.state}

    # Reachable today only by `proposal_sent`, which is in `intakes.ALLOWED`
    # but which nothing advances an intake to until Stage 3 builds the actor
    # that does. Failing loudly here, rather than falling through to
    # `{"state": intake.state}`, is deliberate: an unhandled state echoed
    # straight onto the wire is exactly the kind of silent, un-reviewed leak
    # this module exists to prevent, even though the leaked value here is
    # only the state name itself. Whoever wires `proposal_sent` into the
    # client's lifecycle has to decide - and write - what that state shows,
    # not inherit whatever this fallthrough would have done by accident.
    raise ValueError(f"clientview.of does not know how to show state {intake.state!r}.")
