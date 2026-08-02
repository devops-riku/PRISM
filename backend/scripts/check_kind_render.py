"""The requirements document takes its shape from the kind - the check for it.

Two things have to hold at once.

A quotation with no kind is a software quotation, and it must print exactly
what it printed before kinds existed. The hash below is that whole document,
byte for byte, so any reformatting of the software path fails here rather than
in front of a client.

A quotation of any other discipline must print that discipline's sections and
none of the vocabulary the software sheet is built from. The fixture used for
that is deliberately poisoned: every typed field on `DeveloperSpec` is filled
with software prose, so the check proves the renderer ignores those fields
rather than proving the fixture happened to be empty.

    cd backend
    .venv/Scripts/python.exe scripts/check_kind_render.py        # Windows
    .venv/bin/python scripts/check_kind_render.py                # macOS / Linux

The baseline hash covers the document's date of issue, and that is today's
date. It was taken on 1 August 2026; run on a later day the document says a
different date and the hash has to be recomputed. That is only safe when the
software path itself has not been touched.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# The em dash in every title does not survive the Windows console codepage.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):  # pragma: no cover - non-standard stream
        pass

from app import kinds  # noqa: E402
from app.costing import recompute  # noqa: E402
from app.renderers.markdown import render_developer_requirements  # noqa: E402
from app.schemas import (  # noqa: E402
    ApiEndpoint,
    Confidence,
    CostSummary,
    DeveloperSpec,
    DevOpsPlan,
    Estimate,
    LineItem,
    PaymentMilestone,
    Phase,
    Priority,
    Requirement,
    RequirementType,
    Risk,
    SpecSection,
    TechStackItem,
    UnitKind,
)
from scripts.smoke import build_estimate  # noqa: E402

BASELINE_SHA16 = "f8fdd8bbedee3ba1"
BASELINE_LENGTH = 11742

#: The words a document for any other discipline must not contain. "api" is
#: matched on word boundaries because "capital" is ordinary accounting prose.
FORBIDDEN = (
    re.compile(r"tech stack", re.I),
    re.compile(r"\bapi\b", re.I),
    re.compile(r"deployment", re.I),
    re.compile(r"endpoint", re.I),
)

#: Filled into every typed field on `DeveloperSpec`. None of it may reach a
#: document that is not a software document.
SOFTWARE_SPEC = dict(
    overview="A FastAPI service behind a React front end, deployed from one repository.",
    architecture_summary="Three tiers behind a load balancer, with a worker for long jobs.",
    tech_stack=[
        TechStackItem(
            layer="API",
            choice="FastAPI",
            rationale="The tech stack the team already runs.",
        )
    ],
    data_model_notes="A Postgres schema with a ledger table and an audit trail table.",
    api_surface=[
        ApiEndpoint(
            method="GET",
            path="/api/v1/ledger",
            purpose="An endpoint listing ledger entries.",
            request_notes="Query: from, to.",
            response_notes="200 with a page of entries.",
        )
    ],
    integrations=["A banking API with signed webhooks"],
    non_functional=["p95 API latency under 300ms at 50 concurrent users"],
    testing_strategy="Unit tests and one end-to-end run before every deployment.",
    devops=DevOpsPlan(
        environments=["Production - one region, one endpoint, nightly snapshots"],
        ci_cd="Every push runs the suite; a green build triggers a deployment.",
        infrastructure="Managed container hosting declared in Terraform.",
        observability="Structured logs with a 30 day retention and an uptime check.",
        release_and_rollback="A release promotes a tagged image; rollback promotes the previous one.",
        backup_and_recovery="Nightly snapshots retained 30 days, restore rehearsed quarterly.",
        secrets_and_access="Keys live in the hosting provider's secret store.",
    ),
    open_questions=["Which deployment target does the client already pay for?"],
)


# --- fixtures ----------------------------------------------------------------


def accounting_sections() -> list[SpecSection]:
    """The seven accounting sections, written the way the model is asked to.

    Deliberately out of the declared order, because the renderer matches them
    by heading rather than by position and a fixture in order would not prove
    that. The prose is watched as carefully as the renderer is: one casual
    "endpoint" in here would fail the vocabulary check for the wrong reason.
    """
    written = {
        "The engagement": (
            "A clean-up of two financial years of the general ledger, ending in a trial "
            "balance the external auditor can work from.",
            [
                "Two years brought to a reviewed trial balance",
                "One partner review before anything is issued",
            ],
        ),
        "Periods and volumes": (
            "FY2024 and FY2025, twenty-four monthly periods in all.",
            [
                "Roughly 4,200 bank lines across three accounts",
                "About 900 supplier invoices and 1,400 sales invoices",
            ],
        ),
        "Standards and compliance basis": (
            "PFRS for SMEs, with the statutory filing calendar of the Bureau of Internal "
            "Revenue setting the dates every deliverable is worked back from.",
            ["Prior year comparatives restated where the clean-up moves them"],
        ),
        "Records and access needed": (
            "Nothing starts until the records are complete: a reconciliation cannot be "
            "finished around a missing statement.",
            [
                "Bank statements for all three accounts, both years",
                "Read access to the bookkeeping file, with the prior accountant's journals",
                "Supplier and customer ageing as at each year end",
            ],
        ),
        "Statements and schedules delivered": (
            "A signed trial balance, the supporting lead schedules, and a written note of "
            "every adjustment made and why.",
            [
                "Trial balance per period, in a workbook",
                "Lead schedules for cash, receivables, payables and fixed assets",
                "Adjusting journal register",
            ],
        ),
        "Handover and filing": (
            "The working papers are handed to the client's own file, and the firm walks the "
            "bookkeeper through the adjustments in one session.",
            ["One handover session, recorded", "Working papers retained for seven years"],
        ),
        "Assumptions": (
            "The figures below assume the ledger is complete as delivered.",
            [
                "No prior year audit adjustments are outstanding",
                "The bookkeeper is available for questions during the clean-up",
            ],
        ),
    }
    shuffled = [
        "Records and access needed",
        "The engagement",
        "Assumptions",
        "Periods and volumes",
        "Handover and filing",
        "Standards and compliance basis",
        "Statements and schedules delivered",
    ]
    sections = [
        SpecSection(heading=heading, body=written[heading][0], points=list(written[heading][1]))
        for heading in shuffled
    ]
    # A section nobody asked for. A model handed a set of headings will now and
    # then write an eighth of its own, and the kind decides what this document
    # contains - so it is dropped rather than printed at the end.
    sections.append(
        SpecSection(
            heading="Deployment notes",
            body="A pipeline runs on merge and promotes the build.",
            points=["Rolled back by promoting the previous tag"],
        )
    )
    return sections


def build_accounting_estimate() -> Estimate:
    """A ledger clean-up, priced, with a software spec attached to it."""
    return recompute(
        Estimate(
            project_name="Ledger cleanup",
            client_name="Verde Trading Corporation",
            kind="accounting",
            currency="PHP",
            market_region="Philippines",
            confidence=Confidence.medium,
            requirements=[
                Requirement(
                    id="FR-01",
                    title="Two financial years brought to a reviewed trial balance",
                    description=(
                        "Every account is reconciled to a supporting schedule and the "
                        "adjustments are journalled, per period, for FY2024 and FY2025."
                    ),
                    type=RequirementType.functional,
                    priority=Priority.must,
                    acceptance_criteria=[
                        "Each of the twenty-four periods closes to a balanced trial balance.",
                        "Every adjusting journal names the document it was raised from.",
                    ],
                ),
                Requirement(
                    id="FR-02",
                    title="Bank accounts reconciled month by month",
                    description=(
                        "All three accounts are reconciled to the statement for every month "
                        "in the two years, with unreconciled items listed and aged."
                    ),
                    type=RequirementType.functional,
                    priority=Priority.must,
                    acceptance_criteria=[
                        "No reconciling item older than 90 days is left unexplained.",
                    ],
                ),
                Requirement(
                    id="NFR-01",
                    title="Client records stay inside the firm's own file",
                    description=(
                        "Records are held under the engagement's confidentiality terms and "
                        "are not copied outside the firm."
                    ),
                    type=RequirementType.non_functional,
                    priority=Priority.must,
                    acceptance_criteria=[
                        "Access is limited to the two staff named in the engagement letter.",
                    ],
                ),
            ],
            line_items=[
                LineItem(
                    id="LI-01",
                    category="Bookkeeping",
                    description="Posting clean-up and reconstruction of the two years",
                    role="Senior accountant",
                    quantity=18.0,
                    unit=UnitKind.day,
                    unit_rate=9_500.0,
                    requirement_ids=["FR-01"],
                    notes="Includes rebuilding the fixed asset register.",
                ),
                LineItem(
                    id="LI-02",
                    category="Reconciliation",
                    description="Monthly bank reconciliations across three accounts",
                    role="Accounting associate",
                    quantity=11.5,
                    unit=UnitKind.day,
                    unit_rate=6_250.0,
                    requirement_ids=["FR-02"],
                ),
                LineItem(
                    id="LI-03",
                    category="Review",
                    description="Partner review, adjusting journals and the signed trial balance",
                    role="Engagement partner",
                    quantity=3.5,
                    unit=UnitKind.day,
                    unit_rate=21_000.0,
                    requirement_ids=["FR-01", "FR-02", "NFR-01"],
                ),
            ],
            phases=[
                Phase(
                    name="Records and reconstruction",
                    objective="Get the ledger complete before anything is reconciled.",
                    deliverables=["Complete record set", "Reconstructed postings for both years"],
                    duration_weeks=4.0,
                    line_item_ids=["LI-01"],
                ),
                Phase(
                    name="Reconciliation and review",
                    objective="Close every period and have the partner sign it.",
                    deliverables=["Monthly reconciliations", "Signed trial balance"],
                    duration_weeks=3.0,
                    line_item_ids=["LI-02", "LI-03"],
                ),
            ],
            risks=[
                Risk(
                    description="Statements for one of the three bank accounts may be incomplete.",
                    impact="The affected months cannot be closed until they arrive.",
                    likelihood="Medium",
                    mitigation="Request the full statement set from the bank in week one.",
                ),
            ],
            cost=CostSummary(
                contingency_pct=0.0,
                tax_label="VAT",
                tax_pct=12.0,
                payment_milestones=[
                    PaymentMilestone(
                        label="On signature",
                        percent=50.0,
                        trigger="Signed engagement letter received.",
                    ),
                    PaymentMilestone(
                        label="On issue",
                        percent=50.0,
                        trigger="The signed trial balance is issued.",
                    ),
                ],
                rate_basis="Metro Manila mid-tier practice day rates, 2026, exclusive of VAT.",
            ),
            developer=DeveloperSpec(sections=accounting_sections(), **SOFTWARE_SPEC),
        )
    )


# --- checks ------------------------------------------------------------------


def check_software_is_byte_identical() -> list[str]:
    """The gate. A quotation with no kind renders exactly what it always has."""
    estimate = recompute(build_estimate())
    assert estimate.kind == "software", f"the fixture carries a kind: {estimate.kind!r}"

    document = render_developer_requirements(estimate)
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()[:16]

    assert len(document) == BASELINE_LENGTH, (
        f"the software document is {len(document)} characters, not {BASELINE_LENGTH} - "
        "the software path moved"
    )
    assert digest == BASELINE_SHA16, (
        f"the software document hashes to {digest}, not {BASELINE_SHA16} - the software "
        "path moved. Fix the renderer, not this number."
    )
    return [
        f"an estimate with no kind renders {BASELINE_LENGTH:,} bytes hashing to "
        f"{BASELINE_SHA16}"
    ]


def _forbidden_lines(document: str) -> list[str]:
    return [
        line.strip()
        for line in document.splitlines()
        if any(pattern.search(line) for pattern in FORBIDDEN)
    ]


def check_accounting_says_nothing_about_software() -> list[str]:
    """The accounting document, from a spec carrying a full software one."""
    proved: list[str] = []
    estimate = build_accounting_estimate()
    document = render_developer_requirements(estimate)

    leaked = _forbidden_lines(document)
    assert not leaked, f"software vocabulary reached the accounting document: {leaked[:3]}"
    proved.append(
        f"none of {', '.join(p.pattern for p in FORBIDDEN)} appears, from a spec that "
        f"names all four"
    )

    assert document.startswith("# Accounting Requirements — "), (
        f"the title is not the kind's: {document.splitlines()[0]!r}"
    )
    proved.append(f"the title is {document.splitlines()[0]!r}")

    kind = kinds.resolve("accounting")
    positions = []
    for spec in kind.sections:
        heading = f"## {spec.heading}"
        assert heading in document, f"the accounting document has no {heading!r}"
        positions.append(document.index(heading))
    assert positions == sorted(positions), (
        "the sections are not in the order the kind declares them"
    )
    proved.append(f"all {len(kind.sections)} declared headings appear, in declared order")

    declared = {spec.heading for spec in kind.sections}
    written = [s for s in estimate.developer.sections if s.heading in declared]
    for section in written:
        assert section.body in document, f"the body of {section.heading!r} is missing"
        for point in section.points:
            assert point in document, f"a point of {section.heading!r} is missing: {point!r}"
    proved.append(f"every body and all {sum(len(s.points) for s in written)} points render")

    # The kind decides what this document contains, so a section it never asked
    # for goes nowhere - not even at the end, under its own heading.
    undeclared = [s for s in estimate.developer.sections if s.heading not in declared]
    assert undeclared, "the fixture has no undeclared section, so this check proves nothing"
    for section in undeclared:
        assert f"## {section.heading}" not in document, (
            f"an undeclared section printed its heading: {section.heading!r}"
        )
        assert section.body not in document, f"an undeclared section printed its body"
    proved.append(
        f"{len(undeclared)} section the kind never declared is dropped, heading and body"
    )

    for heading in ("## Functional requirements", "## Effort allocation", "## Phase plan", "## Risks"):
        assert heading in document, f"the shared section {heading!r} is missing"
    assert "### Work breakdown" in document, "the work breakdown is missing"
    assert "- [ ] Each of the twenty-four periods" in document, "acceptance criteria are missing"
    proved.append("requirements, acceptance criteria, line items, phases and risks all print")

    return proved


def check_an_empty_section_prints_no_heading() -> list[str]:
    """A section the model left empty is dropped, not printed bare."""
    estimate = build_accounting_estimate()
    emptied = estimate.model_copy(deep=True)
    target = next(s for s in emptied.developer.sections if s.heading == "Periods and volumes")
    target.body = ""
    target.points = []

    document = render_developer_requirements(emptied)
    assert "## Periods and volumes" not in document, "an empty section still printed its heading"

    still_there = [
        spec.heading
        for spec in kinds.resolve("accounting").sections
        if spec.heading != "Periods and volumes"
    ]
    missing = [heading for heading in still_there if f"## {heading}" not in document]
    assert not missing, f"emptying one section dropped {missing}"
    return [f"an empty section prints no heading; the other {len(still_there)} still do"]


def check_every_other_kind_takes_its_own_shape() -> list[str]:
    """The branch is generic. Every non-software kind prints its own sections."""
    proved: list[str] = []
    base = build_accounting_estimate()

    for kind in kinds.KINDS:
        if kind.id == kinds.DEFAULT.id:
            continue
        estimate = base.model_copy(deep=True)
        estimate.kind = kind.id
        estimate.kind_label = "Ecology survey" if kind.id == kinds.OTHER.id else ""
        estimate.developer.sections = [
            SpecSection(
                heading=spec.heading,
                body=f"What this engagement says under {spec.heading.lower()}.",
                points=[f"One line under {spec.heading.lower()}"],
            )
            for spec in kind.sections
        ]

        document = render_developer_requirements(estimate)
        noun = "Ecology survey" if kind.id == kinds.OTHER.id else kind.noun
        expected = f"# {noun} Requirements — Ledger cleanup"
        assert document.startswith(expected), (
            f"{kind.id}: title is {document.splitlines()[0]!r}, expected {expected!r}"
        )

        positions = []
        for spec in kind.sections:
            heading = f"## {spec.heading}"
            assert heading in document, f"{kind.id}: no {heading!r}"
            positions.append(document.index(heading))
        assert positions == sorted(positions), f"{kind.id}: sections out of declared order"

        leaked = _forbidden_lines(document)
        assert not leaked, f"{kind.id}: software vocabulary reached the document: {leaked[:3]}"

        proved.append(
            f"{kind.id}: {len(kind.sections)} declared headings in order under "
            f"{noun!r}, no software vocabulary"
        )

    return proved


# --- entry point -------------------------------------------------------------


def main() -> int:
    print("PRISM kind render check - the document takes its shape from the kind")
    print("=" * 78)

    print("\nThe gate: a quotation with no kind")
    for line in check_software_is_byte_identical():
        print(f"  ok  {line}")

    print("\nAccounting")
    for line in check_accounting_says_nothing_about_software():
        print(f"  ok  {line}")

    print("\nEmpty sections")
    for line in check_an_empty_section_prints_no_heading():
        print(f"  ok  {line}")

    print("\nEvery other kind")
    for line in check_every_other_kind_takes_its_own_shape():
        print(f"  ok  {line}")

    print("\nKIND RENDER CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
