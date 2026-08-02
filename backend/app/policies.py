"""The terms a proposal carries, and who is allowed to write them.

A proposal is two documents wearing one cover. The first is an argument - why
this work, why this studio, why now - and a model is good at it. The second is a
contract in waiting: validity, ownership, confidentiality, what happens when the
scope moves. A model must never write that. Not because it writes badly, but
because nobody signs prose whose author cannot be asked what it meant.

So the split is absolute and it is enforced by shape rather than by instruction:
the clauses live here, the studio edits them in Settings, the renderer inserts
them verbatim, and the model's response schema has no field that could carry
one. There is nothing for it to fill in.

Clauses are snapshotted onto each proposal when it is built. A proposal sent in
March says what it said in March, whatever the studio changes in April.
"""

from __future__ import annotations

from typing import Iterable, List

from pydantic import BaseModel, Field

__all__ = ["PolicyClause", "DEFAULT_POLICIES", "resolve", "normalise"]


class PolicyClause(BaseModel):
    """One numbered term in the proposal's conditions."""

    id: str = Field(default="", description="Stable key, e.g. 'validity'. Used to match on edit.")
    title: str = Field(default="", description="The heading a client reads, e.g. 'Validity'.")
    body: str = Field(default="", description="The clause itself, printed exactly as written.")
    enabled: bool = Field(default=True, description="False leaves it out of the document entirely.")

    def normalised(self) -> "PolicyClause":
        return PolicyClause(
            id=(self.id or "").strip()[:40],
            title=(self.title or "").strip()[:80],
            body=(self.body or "").strip()[:2000],
            enabled=bool(self.enabled),
        )

    @property
    def usable(self) -> bool:
        return bool(self.title.strip() and self.body.strip())


#: What a small studio in this market would actually put in front of a client.
#: Recommended rather than imposed: every one can be edited, disabled or
#: replaced, and the wording is deliberately plain so it can be read once and
#: understood. Philippine defaults where the law is named, because that is the
#: home market - a studio elsewhere edits three of these and is done.
DEFAULT_POLICIES: List[PolicyClause] = [
    PolicyClause(
        id="validity",
        title="Validity",
        body=(
            "This proposal and the pricing in it are valid for the period stated on the "
            "quotation. After that date the scope stands but the figures may be re-quoted, "
            "since rates and third-party costs move."
        ),
    ),
    PolicyClause(
        id="payment",
        title="Payment and invoicing",
        body=(
            "Payments follow the schedule set out in this document. Each becomes payable when "
            "its stated trigger is met, and invoices are due within 15 days of issue. Work on "
            "the next stage begins once the preceding payment has cleared."
        ),
    ),
    PolicyClause(
        id="scope-changes",
        title="Changes to scope",
        body=(
            "Anything not written into the scope of this proposal is a change request. We will "
            "price it in writing before starting it, and you decide whether it proceeds. No "
            "change is carried out - and nothing is billed for one - without that written "
            "agreement."
        ),
    ),
    PolicyClause(
        id="client-responsibilities",
        title="Delays and dependencies",
        body=(
            "Timely decisions, access to the people and systems named in the requirements, and "
            "content in the agreed formats. Where a dependency on your side is late, the "
            "affected dates move by the same period rather than the work being compressed."
        ),
    ),
    PolicyClause(
        id="ip",
        title="Ownership of the work",
        body=(
            "On final payment, ownership of the delivered source code, designs and documents "
            "produced for this engagement passes to you. We keep ownership of our pre-existing "
            "tools, libraries and internal frameworks, and grant you a perpetual licence to use "
            "them as part of the delivered work."
        ),
    ),
    PolicyClause(
        id="confidentiality",
        title="Confidentiality and data",
        body=(
            "Anything you share for this engagement is treated as confidential and used only to "
            "deliver it. Personal data is processed in line with the Data Privacy Act of 2012 "
            "(RA 10173), kept only as long as the work requires, and returned or deleted on "
            "request at the end of the engagement."
        ),
    ),
    PolicyClause(
        id="warranty",
        title="Warranty",
        body=(
            "Defects reported within 30 days of go-live - where the delivered work does not "
            "behave as the accepted requirements describe - are fixed at no charge. The warranty "
            "does not cover new requirements, changes made by others, or failures in third-party "
            "services outside our control."
        ),
    ),
    PolicyClause(
        id="support",
        title="Support after handover",
        body=(
            "Ongoing support and maintenance are not included unless a line for them appears in "
            "the costing. We are happy to quote a support arrangement separately, and can do so "
            "before go-live so there is no gap."
        ),
    ),
    PolicyClause(
        id="acceptance",
        title="Acceptance",
        body=(
            "Each stage is accepted against the acceptance criteria in the requirements "
            "specification. Where feedback is not received within 10 working days of a stage "
            "being submitted for review, that stage is treated as accepted so the schedule can "
            "continue."
        ),
    ),
    PolicyClause(
        id="third-party",
        title="Third-party services and licences",
        body=(
            "Subscriptions, licences and infrastructure billed by third parties are yours to "
            "hold and pay for, in your own accounts, unless a line for them appears in the "
            "costing. We will name what is needed before it is required."
        ),
    ),
    PolicyClause(
        id="termination",
        title="Ending the engagement",
        body=(
            "Either side may end the engagement with 14 days written notice. Work completed and "
            "in progress up to that date is payable, and everything produced to that point is "
            "handed over once it is settled."
        ),
    ),
    PolicyClause(
        id="force-majeure",
        title="Events outside anyone's control",
        body=(
            "Neither side is liable for delay or failure caused by events beyond reasonable "
            "control - natural disaster, outage of a public utility or network, civil "
            "disturbance, or an act of government. Where one occurs we tell you promptly, and "
            "the affected dates move by the length of the disruption."
        ),
    ),
    PolicyClause(
        id="regulatory-change",
        title="Changes in law or regulation",
        body=(
            "Where a change in law, regulation or a platform's published rules makes agreed work "
            "unlawful, impossible or materially different, we will say so in writing and price "
            "the change before doing it. Work already delivered is unaffected."
        ),
    ),
    PolicyClause(
        id="client-information",
        title="Information you supply",
        body=(
            "We rely on the accuracy of the information, content and access you provide. Where "
            "something turns out to be materially different from what was described, the work "
            "needed to accommodate it is a change request rather than a defect."
        ),
    ),
    PolicyClause(
        id="proposal-acceptance",
        title="Accepting this proposal",
        body=(
            "Signing this proposal accepts the scope described in it, the pricing and payment "
            "schedule set out in it, and these terms. It does not create an obligation to "
            "proceed beyond the work described here."
        ),
    ),
    PolicyClause(
        id="governing-law",
        title="Governing law",
        body=(
            "This proposal and any agreement arising from it are governed by the laws of the "
            "Republic of the Philippines."
        ),
    ),
]


def normalise(clauses: Iterable[PolicyClause]) -> List[PolicyClause]:
    """Clean a set of clauses and drop the ones with nothing in them."""
    seen: set[str] = set()
    out: List[PolicyClause] = []
    for clause in clauses or []:
        cleaned = clause.normalised()
        if not cleaned.usable:
            continue
        key = cleaned.id or cleaned.title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out[:40]


def resolve(clauses: Iterable[PolicyClause]) -> List[PolicyClause]:
    """The clauses a proposal being built right now should carry.

    Enabled only, in the studio's own order. Configured with nothing at all, the
    recommended set stands in - a proposal with no terms is not a document
    anybody should be sending.
    """
    configured = normalise(clauses)
    if not configured:
        return [clause.model_copy(deep=True) for clause in DEFAULT_POLICIES]
    return [clause for clause in configured if clause.enabled]
