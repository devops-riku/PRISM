"""PRISM offline smoke test - the whole render path, no network, no API key.

Builds a fully populated `Estimate` by hand, runs it through
quotation-domain `costing.recompute`, asserts the arithmetic is internally consistent, then
renders both markdown deliverables and both printable HTML pages and writes
everything to `backend/generated/_smoke/`.

This is the project's offline test. It must pass with no `GEMINI_API_KEY`, no
network and no running server:

    cd backend
    .venv/Scripts/python.exe scripts/smoke.py        # Windows
    .venv/bin/python scripts/smoke.py                # macOS / Linux

Exit code 0 means the render path is sound. Any assertion failure is a real bug
in `costing.py` or a renderer, not a rounding artefact - the tolerances below are
one cent, which is the unit the documents are printed in.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `backend/` on the path so `app.*` resolves however this file is invoked.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# The peso sign, the em dash and the middle dot do not survive the Windows
# console codepage. Reconfigure before anything prints.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):  # pragma: no cover - non-standard stream
        pass

from app.features.quotations.domain.costing import CostingError, money_decimals, recompute, snap_to_total  # noqa: E402
from app.features.rendering.presentation import (  # noqa: E402
    render_client_proposal,
    render_developer_requirements,
    render_print_html,
)
from app.features.rendering.presentation.money import currency_decimals, format_amount  # noqa: E402
from app.features.quotations.domain.models import (  # noqa: E402
    ApiEndpoint,
    ClientNarrative,
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
    TechStackItem,
    UnitKind,
)

OUT_DIR = BACKEND_DIR / "generated" / "_smoke"
CENT = 0.01

CURRENCY = "PHP"
CONTINGENCY_PCT = 10.0
TAX_LABEL = "VAT"
TAX_PCT = 12.0


# --- the fabricated estimate -------------------------------------------------


def build_estimate() -> Estimate:
    """A complete, deliberately awkward estimate.

    Nothing here is round: fractional quantities, five different unit rates and
    three unit kinds, so the rounding path is genuinely exercised rather than
    stepped over. The advisory `subtotal` / `amount` / `total` fields are
    deliberately WRONG - `recompute` has to overwrite every one of them.
    """
    requirements = [
        Requirement(
            id="FR-01",
            title="Guests book a dive slot and pay a deposit",
            description=(
                "A guest chooses a date and a boat, sees remaining capacity, and pays a "
                "deposit online. The booking is only held once the deposit clears."
            ),
            type=RequirementType.functional,
            priority=Priority.must,
            acceptance_criteria=[
                "A guest can complete a booking end to end on a 360px viewport without "
                "leaving the flow.",
                "A booking whose deposit fails is released within 15 minutes and its seat "
                "returns to the available pool.",
                "The confirmation email arrives within 60 seconds and names the boat, the "
                "date and the balance due.",
            ],
        ),
        Requirement(
            id="FR-02",
            title="Shop staff see tomorrow's manifest on a phone",
            description=(
                "A staff member opens the manifest for a chosen day and sees every guest, "
                "their certification level and whether the balance is settled."
            ),
            type=RequirementType.functional,
            priority=Priority.should,
            acceptance_criteria=[
                "The manifest for any day loads in under 2 seconds on a 4G connection.",
                "A staff member can mark a guest as checked in and the change is visible to "
                "a second device within 5 seconds.",
            ],
        ),
        Requirement(
            id="NFR-01",
            title="Payment data never touches the application database",
            description=(
                "Card details are captured by the processor's hosted fields. The application "
                "stores only the processor's token and the last four digits."
            ),
            type=RequirementType.non_functional,
            priority=Priority.must,
            acceptance_criteria=[
                "No column in any table can hold a PAN; a schema review confirms this before "
                "go-live.",
                "Application logs are scanned in CI and a build fails if a card-shaped number "
                "appears in one.",
            ],
        ),
    ]

    line_items = [
        LineItem(
            id="LI-01",
            category="Discovery",
            description="Requirements workshops, booking-rules capture and written scope",
            role="Principal consultant",
            quantity=6.5,
            unit=UnitKind.day,
            unit_rate=18_500.0,
            subtotal=1.0,  # advisory nonsense - recompute must overwrite it
            requirement_ids=["FR-01", "FR-02"],
            notes="Two half-day sessions with the shop plus written follow-up.",
        ),
        LineItem(
            id="LI-02",
            category="Design",
            description="Booking flow and manifest UI, mobile first, one revision round",
            role="Senior product designer",
            quantity=12.5,
            unit=UnitKind.day,
            unit_rate=12_750.0,
            subtotal=2.0,
            requirement_ids=["FR-01", "FR-02"],
        ),
        LineItem(
            id="LI-03",
            category="Engineering",
            description="Guest-facing booking flow, capacity display and deposit checkout",
            role="Senior frontend engineer",
            quantity=34.0,
            unit=UnitKind.hour,
            unit_rate=2_350.0,
            subtotal=3.0,
            requirement_ids=["FR-01"],
        ),
        LineItem(
            id="LI-04",
            category="Engineering",
            description="Booking, capacity and manifest APIs with hold-and-release semantics",
            role="Senior backend engineer",
            quantity=46.5,
            unit=UnitKind.hour,
            unit_rate=2_675.0,
            subtotal=4.0,
            requirement_ids=["FR-01", "FR-02", "NFR-01"],
        ),
        LineItem(
            id="LI-05",
            category="Engineering",
            description="Payment processor integration with hosted fields and webhook handling",
            role="Senior backend engineer",
            quantity=1.0,
            unit=UnitKind.lump_sum,
            unit_rate=87_450.75,
            subtotal=5.0,
            requirement_ids=["NFR-01"],
            notes="Fixed scope: one processor, one currency, deposits only.",
        ),
    ]

    phases = [
        Phase(
            name="Discovery and design",
            objective="Agree the booking rules and the two screens they hang off.",
            deliverables=[
                "Written scope with the booking and cancellation rules",
                "Mobile-first designs for the guest flow and the manifest",
            ],
            duration_weeks=3.5,
            line_item_ids=["LI-01", "LI-02"],
        ),
        Phase(
            name="Build and integration",
            objective="Ship the booking flow, the manifest and the deposit path.",
            deliverables=[
                "Guest booking flow on staging",
                "Manifest view on staging",
                "Payment integration passing the processor's test suite",
            ],
            duration_weeks=6.0,
            line_item_ids=["LI-03", "LI-04", "LI-05"],
        ),
    ]

    milestones = [
        PaymentMilestone(
            label="On signature",
            percent=30.0,
            amount=999.0,  # advisory nonsense
            trigger="Signed statement of work received.",
        ),
        PaymentMilestone(
            label="On design sign-off",
            percent=35.0,
            amount=999.0,
            trigger="The shop accepts the guest flow and manifest designs in writing.",
        ),
        PaymentMilestone(
            label="On go-live",
            percent=35.0,
            amount=999.0,
            trigger="The booking site is taking live deposits in production.",
        ),
    ]

    cost = CostSummary(
        subtotal=1.0,  # advisory nonsense
        contingency_pct=CONTINGENCY_PCT,
        contingency_amount=1.0,
        discount_amount=0.0,
        tax_label=TAX_LABEL,
        tax_pct=TAX_PCT,
        tax_amount=1.0,
        total=1.0,
        payment_milestones=milestones,
        rate_basis=(
            "Metro Manila senior contractor day rates, 2026, blended across a "
            "three-person squad, exclusive of VAT."
        ),
    )

    return Estimate(
        project_name="Dive shop booking platform",
        client_name="Blue Water Divers",
        currency=CURRENCY,
        market_region="Philippines",
        confidence=Confidence.medium,
        image_observations=[],
        requirements=requirements,
        phases=phases,
        line_items=line_items,
        cost=cost,
        risks=[
            Risk(
                description="The processor's Philippine deposit flow may require a live "
                "merchant account before integration testing can finish.",
                impact="Up to two weeks of idle integration time near go-live.",
                likelihood="Medium",
                mitigation="Open the merchant application in week one and gate the build "
                "phase on sandbox credentials, not on the live account.",
            ),
            Risk(
                description="Boat capacity rules differ per boat and per season and were "
                "described verbally.",
                impact="Rework of the capacity model after design sign-off.",
                likelihood="Medium",
                mitigation="Capture the rules as a written table in discovery and have the "
                "shop owner sign it before design starts.",
            ),
        ],
        client=ClientNarrative(
            title="Booking platform for Blue Water Divers",
            executive_summary=(
                "You want guests to book a boat and a date themselves, pay a deposit on the "
                "spot, and stop the phone ringing. This quotation covers the guest booking "
                "site, the deposit payment, and the manifest your staff open on a phone the "
                "morning of a dive."
            ),
            understanding=(
                "Right now a booking is a message, a reply, and a note in a book. That works "
                "until two staff answer the same enquiry, or a guest turns up for a boat that "
                "was already full. You do not want a dive-shop management system. You want "
                "the booking to be certain and the morning to be calm."
            ),
            proposed_solution=(
                "A booking site where a guest picks a date and a boat, sees what is left, and "
                "pays a deposit through your payment processor. The seat is only held once "
                "that deposit clears. Your staff get one screen, built for a phone, showing "
                "who is on which boat and who still owes a balance."
            ),
            scope_inclusions=[
                "Guest booking flow for dates, boats and remaining capacity",
                "Deposit payment through one payment processor",
                "Staff manifest view built for a phone",
                "Confirmation and reminder emails",
                "One revision round on the designs",
                "Deployment to production and a handover session",
            ],
            scope_exclusions=[
                "Native iPhone or Android apps",
                "Equipment rental, servicing or stock tracking",
                "Accounting or payroll integration",
                "Content writing and photography",
                "Ongoing hosting and processor fees",
                "Support after the handover session",
            ],
            assumptions=[
                "You have, or can open, a merchant account with a processor that operates in "
                "the Philippines.",
                "Boat capacities and cancellation rules can be written down and agreed during "
                "discovery.",
                "Your team supplies boat names, photographs and descriptions.",
            ],
            timeline_summary=(
                "Discovery starts on signature and runs alongside design. Build begins once "
                "the booking rules are agreed. The last stage is your own acceptance testing "
                "on staging, before we go live."
            ),
            next_steps=[
                "Confirm which payment processor you want to use.",
                "Send us the boat list with capacities.",
                "Pick a week for the discovery sessions.",
            ],
            validity_days=30,
        ),
        developer=DeveloperSpec(
            overview=(
                "A booking and manifest application for a single dive shop. One tenant, one "
                "currency, deposits only. The interesting part is the seat-hold lifecycle: a "
                "seat is reserved optimistically, confirmed on webhook, and released on "
                "timeout."
            ),
            architecture_summary=(
                "React SPA over a FastAPI service and PostgreSQL. Payment capture happens in "
                "the processor's hosted fields; the application only ever sees a token. A "
                "background worker releases expired holds."
            ),
            tech_stack=[
                TechStackItem(
                    layer="Frontend",
                    choice="React 18 + Vite",
                    rationale="One interactive flow and one dense table; a framework with a "
                    "router and SSR would be weight with no payoff here.",
                ),
                TechStackItem(
                    layer="API",
                    choice="FastAPI",
                    rationale="Pydantic models are already the contract between the booking "
                    "rules and the client, so the schema is not written twice.",
                ),
                TechStackItem(
                    layer="Datastore",
                    choice="PostgreSQL 16",
                    rationale="Seat holds need a real transaction and an exclusion "
                    "constraint; SQLite cannot express the concurrency this needs.",
                ),
                TechStackItem(
                    layer="Hosting",
                    choice="A single small VM with managed Postgres",
                    rationale="One shop, predictable load. Container orchestration would "
                    "cost more to operate than it saves.",
                ),
            ],
            data_model_notes=(
                "Boat, Departure, Seat, Booking, Guest, Payment. A Departure is a boat on a "
                "date; capacity lives on the Departure rather than the Boat so a short-crewed "
                "day can be capped without editing the boat. Seat holds carry an expiry and "
                "are enforced with a database constraint, not application logic."
            ),
            api_surface=[
                ApiEndpoint(
                    method="GET",
                    path="/api/v1/departures",
                    purpose="List departures with remaining capacity for a date range.",
                    request_notes="Query: from, to (ISO dates), boat_id optional.",
                    response_notes="Returns capacity_remaining computed live; never cached.",
                ),
                ApiEndpoint(
                    method="POST",
                    path="/api/v1/bookings",
                    purpose="Create a booking and hold seats for 15 minutes.",
                    request_notes="Body: departure_id, guests[], contact. Idempotency-Key "
                    "header required.",
                    response_notes="201 with a hold expiry. 409 when capacity went in the "
                    "moment between read and write.",
                ),
                ApiEndpoint(
                    method="POST",
                    path="/api/v1/webhooks/payments",
                    purpose="Confirm or fail a deposit.",
                    request_notes="Processor signature is verified before the body is parsed.",
                    response_notes="200 on every replay; the handler is idempotent by "
                    "payment reference.",
                ),
            ],
            integrations=[
                "Payment processor with hosted card fields and signed webhooks",
                "Transactional email provider for confirmations and reminders",
            ],
            non_functional=[
                "p95 API latency under 300ms at 50 concurrent users",
                "Nightly database backups with 30-day retention and a restore rehearsal "
                "before go-live",
                "WCAG 2.2 AA on the guest booking flow",
            ],
            testing_strategy=(
                "Unit tests on the seat-hold state machine, integration tests against a real "
                "Postgres in CI, and one end-to-end booking run against the processor's "
                "sandbox on every deploy to staging."
            ),
            devops=DevOpsPlan(
                environments=[
                    "Local - developer machines, Docker Compose, seeded with a fixture dive schedule",
                    "Staging - client acceptance, same image as production, weekly restore from "
                    "production with guest details masked",
                    "Production - Singapore region, single region with nightly off-region backups",
                ],
                ci_cd=(
                    "Every push runs lint, unit tests and the integration suite against a real "
                    "Postgres service container. A merge to main is blocked unless all three are "
                    "green and one review is approved, and it builds and pushes a tagged image."
                ),
                infrastructure=(
                    "Managed container hosting with a managed Postgres instance, both declared "
                    "in Terraform in the same repository. Nothing is provisioned by hand."
                ),
                observability=(
                    "Structured JSON logs shipped to the hosting provider's log store with 30 "
                    "day retention, an uptime check on the booking endpoint every minute, and a "
                    "payment-webhook failure alert to the shop owner's phone and the on-call "
                    "engineer's email."
                ),
                release_and_rollback=(
                    "A release is a tagged image promoted from staging to production. Rollback "
                    "is promoting the previous tag. Migrations run as a separate step and must "
                    "stay backwards compatible for one release, so a rollback never needs a "
                    "down migration."
                ),
                backup_and_recovery=(
                    "Nightly automated database snapshots retained 30 days, plus a weekly "
                    "off-region copy retained 90 days. Recovery point objective 24 hours, "
                    "recovery time objective 4 hours, rehearsed once before go-live."
                ),
                secrets_and_access=(
                    "Payment processor keys and the database URL live in the hosting provider's "
                    "secret store and reach the app as environment variables; none are in the "
                    "repository. Production console access is limited to the lead engineer and "
                    "the shop owner's own account."
                ),
            ),
            open_questions=[
                "Which payment processor is already in use, and is the merchant account live?",
                "Does a deposit ever get refunded automatically, or is every refund manual?",
                "How far ahead can a guest book, and is there a same-day cutoff?",
                "Do staff need to create a booking on a guest's behalf in version one?",
            ],
        ),
    )


# --- assertions --------------------------------------------------------------


def _close(left: float, right: float, tolerance: float = CENT) -> bool:
    return abs(left - right) < tolerance


def check(estimate: Estimate) -> list[str]:
    """Every invariant `costing.recompute` promises. Returns the lines it proved."""
    cost = estimate.cost
    proved: list[str] = []

    # 1. Every line item subtotal is quantity * unit_rate to the cent.
    for item in estimate.line_items:
        expected = round(item.quantity * item.unit_rate, 2)
        assert _close(item.subtotal, expected), (
            f"{item.id}: subtotal {item.subtotal} != quantity * unit_rate {expected}"
        )
    proved.append(f"{len(estimate.line_items)} line item subtotals == quantity * unit_rate")

    # 2. The line item subtotals sum to cost.subtotal.
    summed = sum(item.subtotal for item in estimate.line_items)
    assert _close(summed, cost.subtotal), (
        f"line items sum to {summed} but cost.subtotal is {cost.subtotal}"
    )
    proved.append(f"sum(line item subtotals) == cost.subtotal ({cost.subtotal:,.2f})")

    # 3. The cost summary adds up.
    expected_contingency = round(cost.subtotal * cost.contingency_pct / 100.0, 2)
    assert _close(cost.contingency_amount, expected_contingency), (
        f"contingency {cost.contingency_amount} != {cost.contingency_pct}% of "
        f"{cost.subtotal} ({expected_contingency})"
    )
    taxable = cost.subtotal + cost.contingency_amount - cost.discount_amount
    expected_tax = round(taxable * cost.tax_pct / 100.0, 2)
    assert _close(cost.tax_amount, expected_tax), (
        f"tax {cost.tax_amount} != {cost.tax_pct}% of {taxable} ({expected_tax})"
    )
    assert _close(taxable + cost.tax_amount, cost.total), (
        f"subtotal + contingency - discount + tax = {taxable + cost.tax_amount} "
        f"but cost.total is {cost.total}"
    )
    proved.append(
        f"subtotal + contingency - discount + {cost.tax_label} == total ({cost.total:,.2f})"
    )

    # 4. The tax the fabricated estimate asked for actually survived. An empty
    #    tax_label makes costing zero the percentage, so a silent blank here
    #    would leave the VAT path untested while every other assert still passed.
    assert cost.tax_label == TAX_LABEL, f"tax_label was dropped: {cost.tax_label!r}"
    assert _close(cost.tax_pct, TAX_PCT), f"tax_pct became {cost.tax_pct}"
    assert cost.tax_amount > 0, "tax_amount is zero - the VAT path never ran"
    assert _close(cost.contingency_pct, CONTINGENCY_PCT), (
        f"contingency_pct became {cost.contingency_pct}"
    )
    proved.append(
        f"contingency {cost.contingency_pct:g}% and {cost.tax_label} "
        f"{cost.tax_pct:g}% both applied"
    )

    # 5. The payment schedule pays out exactly the total, and only the total.
    milestones = cost.payment_milestones
    assert milestones, "the payment schedule is empty"
    percent_sum = sum(milestone.percent for milestone in milestones)
    assert _close(percent_sum, 100.0), f"milestone percents sum to {percent_sum}, not 100"
    amount_sum = sum(milestone.amount for milestone in milestones)
    assert _close(amount_sum, cost.total), (
        f"milestones sum to {amount_sum} but the total is {cost.total}"
    )
    assert all(milestone.amount >= 0 for milestone in milestones), "a milestone is negative"
    proved.append(
        f"{len(milestones)} milestone percents == 100 and amounts == total "
        f"({amount_sum:,.2f})"
    )

    # 6. No negative money anywhere, and no dangling cross-references.
    assert cost.subtotal >= 0 and cost.total >= 0, "negative money in the cost summary"
    requirement_ids = {req.id for req in estimate.requirements}
    line_item_ids = {item.id for item in estimate.line_items}
    for item in estimate.line_items:
        assert item.id, "a line item has no id"
        unknown = set(item.requirement_ids) - requirement_ids
        assert not unknown, f"{item.id} references unknown requirements {sorted(unknown)}"
    for phase in estimate.phases:
        unknown = set(phase.line_item_ids) - line_item_ids
        assert not unknown, f"phase {phase.name!r} references unknown line items {sorted(unknown)}"
    proved.append("every id present, no dangling cross-reference, no negative money")

    return proved


def check_effort_allocation() -> list[str]:
    """The effort table must sum to the subtotal when the split is uneven.

    The main fixture prices in PHP with figures that divide cleanly, so it
    cannot see this. A cost shared across three requirements is the case that
    breaks: rounded independently, 100.00 becomes 33.33 x 3 = 99.99 under a
    100.00 total. Zero-decimal currencies are checked too, because the
    per-currency rounding precision is what makes them agree.
    """
    import re

    proved: list[str] = []
    money = re.compile(r"[-+]?[\d,]*\d(?:\.\d+)?")

    def rows_of(document: str) -> list[list[str]]:
        lines = document.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("## Effort allocation"))
        found: list[list[str]] = []
        for line in lines[start:]:
            if not line.startswith("|"):
                if found:
                    break
                continue
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                continue
            found.append(cells)
        return found[1:]  # drop the header row

    def figure(cell: str) -> float:
        match = money.search(cell.replace("**", ""))
        assert match, f"no figure in effort cell {cell!r}"
        return float(match.group(0).replace(",", ""))

    for currency, shared_cost in (("PHP", 100.00), ("JPY", 1000.0), ("KRW", 100_000.0)):
        estimate = recompute(
            Estimate(
                project_name="Uneven split",
                currency=currency,
                requirements=[
                    Requirement(id=f"FR-0{n}", title=f"Shared requirement {n}") for n in (1, 2, 3)
                ],
                line_items=[
                    LineItem(
                        id="LI-01",
                        category="Backend",
                        description="One item three requirements all depend on",
                        role="Engineer",
                        quantity=1,
                        unit=UnitKind.item,
                        unit_rate=shared_cost,
                        requirement_ids=["FR-01", "FR-02", "FR-03"],
                    )
                ],
            )
        )
        rows = rows_of(render_developer_requirements(estimate))
        assert rows, f"{currency}: no effort allocation table rendered"
        assert "Total" in rows[-1][0], f"{currency}: last effort row is not the total"

        decimals = currency_decimals(currency)
        tolerance = 10**-decimals / 2
        allocated = [figure(row[-1]) for row in rows[:-1]]
        total = figure(rows[-1][-1])

        assert abs(sum(allocated) - total) < tolerance, (
            f"{currency}: effort rows sum to {sum(allocated)} under a total of {total}"
        )
        assert abs(total - estimate.cost.subtotal) < tolerance, (
            f"{currency}: effort total {total} != cost.subtotal {estimate.cost.subtotal}"
        )
        if decimals == 0:
            for value in allocated + [total]:
                assert value == int(value), f"{currency}: {value} is not a whole minor unit"
        proved.append(
            f"{currency}: {shared_cost:,.0f} split 3 ways sums back to the total "
            f"({' + '.join(f'{v:,.{decimals}f}' for v in allocated)})"
        )

    # A raw pipe in a model-supplied id must not shift the table's columns.
    piped = recompute(
        Estimate(
            currency=CURRENCY,
            requirements=[Requirement(id="FR|01", title="Id containing a pipe")],
            line_items=[
                LineItem(
                    id="LI-01",
                    description="Item",
                    quantity=1,
                    unit=UnitKind.item,
                    unit_rate=100.0,
                    requirement_ids=["FR|01"],
                )
            ],
        )
    )
    widths = {len(row) for row in rows_of(render_developer_requirements(piped))}
    assert widths == {4}, f"a piped requirement id made the table ragged, row widths {widths}"
    proved.append("a pipe in a requirement id is escaped, every effort row keeps 4 cells")

    return proved


def check_devops_reaches_the_developer(estimate: Estimate) -> list[str]:
    """The handoff has to carry the engagement past the last commit.

    A spec that stops at "we will deploy it" hands the client every expensive
    surprise on day one, so every populated part of the DevOps plan must reach
    the document - and it must reach the *developer's* copy only, because none
    of it is something the client is buying.
    """
    proved: list[str] = []
    plan = estimate.developer.devops
    requirements = render_developer_requirements(estimate)
    proposal = render_client_proposal(estimate)

    assert "## DevOps" in requirements, "the developer sheet has no DevOps section"

    headings = [
        ("### Environments", plan.environments),
        ("### Build and deployment pipeline", plan.ci_cd),
        ("### Infrastructure", plan.infrastructure),
        ("### Observability", plan.observability),
        ("### Release and rollback", plan.release_and_rollback),
        ("### Backup and recovery", plan.backup_and_recovery),
        ("### Secrets and access", plan.secrets_and_access),
    ]
    missing = [title for title, value in headings if value and title not in requirements]
    assert not missing, f"the DevOps section dropped {missing}"
    proved.append(f"all {len([v for _, v in headings if v])} DevOps subsections render")

    for line in plan.environments:
        assert line in requirements, f"environment missing from the document: {line}"
    proved.append(f"{len(plan.environments)} environments listed")

    # An empty field must not leave a heading with nothing under it.
    bare = estimate.model_copy(deep=True)
    bare.developer.devops.observability = ""
    bare.developer.devops.environments = []
    thinner = render_developer_requirements(bare)
    assert "### Observability" not in thinner, "an empty field still printed its heading"
    assert "### Environments" not in thinner, "an empty environment list still printed its heading"
    assert "## DevOps" in thinner, "the section vanished when one field was emptied"
    proved.append("empty fields are omitted, not printed as bare headings")

    # None of it belongs in the client's copy.
    assert "## DevOps" not in proposal, "DevOps reached the client proposal"
    leaked = [line for line in plan.environments if line in proposal]
    assert not leaked, f"environments reached the client proposal: {leaked}"
    proved.append("none of it appears in the client proposal")

    return proved


def check_roles_stay_out_of_the_investment(estimate: Estimate) -> list[str]:
    """The client's Investment table names the work, never who performs it.

    A job title printed beside a rate invites a negotiation about seniority
    instead of about scope. The role is still a real part of the engagement, so
    it stays in the developer sheet's work breakdown where it is a staffing
    decision rather than a price.
    """
    proved: list[str] = []
    roles = sorted({(item.role or "").strip() for item in estimate.line_items if (item.role or "").strip()})
    assert roles, "the fixture has no roles, so this check would prove nothing"

    proposal = render_client_proposal(estimate)
    lines = proposal.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Investment"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    investment = "\n".join(lines[start:end])

    leaked = [role for role in roles if role.lower() in investment.lower()]
    assert not leaked, (
        f"the client's Investment table names {leaked} - it should describe the work only"
    )
    proved.append(f"none of {len(roles)} roles appear in the client Investment table")

    # Categories are departments, and a department is a role by another name.
    # The table is one unbroken list, so neither a heading nor a per-group
    # subtotal may appear in it.
    categories = sorted(
        {(item.category or "").strip() for item in estimate.line_items if (item.category or "").strip()}
    )
    assert categories, "the fixture has no categories, so this check would prove nothing"
    grouped = [c for c in categories if f"**{c}**".lower() in investment.lower()]
    assert not grouped, f"the Investment table still groups by {grouped}"
    assert "Subtotal —" not in investment, "a per-category subtotal row survived"
    proved.append(
        f"no category heading or group subtotal among {len(categories)} categories - one flat list"
    )

    # How the rates were derived is an answer to a question the client has not
    # asked, and printing it invites a negotiation about the derivation.
    basis = (estimate.cost.rate_basis or "").strip()
    assert basis, "the fixture has no rate basis, so this check would prove nothing"
    assert basis not in proposal, "the rate basis is stated in the client proposal"
    proved.append("the rate basis is nowhere in the client proposal")

    # The other half of the rule: both must still be somewhere useful.
    requirements = render_developer_requirements(estimate)
    assert basis in requirements, "the developer sheet lost the rate basis"
    missing = [role for role in roles if role.lower() not in requirements.lower()]
    assert not missing, f"the developer sheet lost the roles {missing}"
    absent = [c for c in categories if c.lower() not in requirements.lower()]
    assert not absent, f"the developer sheet lost the categories {absent}"
    proved.append(
        f"all {len(roles)} roles, {len(categories)} categories and the rate basis are "
        f"still in the developer sheet"
    )

    return proved


def _investment_rows(document: str) -> list[list[str]]:
    """Every row of the client Investment table, split into its cells.

    Splits on unescaped pipes only, exactly as `_cell` escapes them, and drops
    the two empty strings a leading and trailing pipe produce. The alignment
    row (`|---|---:|`) is dropped; the header row is kept as row 0, because the
    caller below is checking the header.
    """
    import re

    lines = document.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Investment"))
    rows: list[list[str]] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
        if cells and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def check_rates_stay_out_of_the_investment(estimate: Estimate) -> list[str]:
    """The client's Investment table prices the work, never the hour.

    A rate printed beside a quantity turns the quotation into a timesheet to be
    argued down line by line - "why is this nine hours" - when what is being
    bought is the delivered thing. The quantity and the unit stay, because the
    shape of the effort is part of what the client is agreeing to. Only the
    price-per-unit goes, and it goes structurally: the column is not rendered,
    so there is no formatting path that can put it back.

    This is a shape check on the rendered document, not on the object, because
    that is where the column would reappear.
    """
    proved: list[str] = []

    proposal = render_client_proposal(estimate)
    rows = _investment_rows(proposal)
    assert rows, "no Investment table found in the client proposal"

    header, body = rows[0], rows[1:]
    assert len(header) == 4, f"the Investment table has {len(header)} columns, expected 4: {header}"
    assert header[:3] == ["Description", "Qty", "Unit"], f"unexpected leading columns: {header[:3]}"
    assert header[3].startswith("Amount ("), f"the last column is {header[3]!r}, not an Amount"
    assert not any(cell.startswith("Rate") for cell in header), f"a Rate column survived: {header}"
    proved.append(f"the Investment table is exactly {' / '.join(header)}")

    ragged = [row for row in body if len(row) != 4]
    assert not ragged, f"{len(ragged)} Investment rows are not 4 cells wide, e.g. {ragged[0]}"
    proved.append(f"all {len(body)} rows carry 4 cells - no column was dropped from the header only")

    # The leak check. A rate whose quantity is 1 is indistinguishable from that
    # line's own amount, and a rate that happens to format identically to some
    # figure the table legitimately prints would fail this for the wrong reason.
    # Both are skipped, and the fixture is asserted to still leave a candidate -
    # otherwise this check would pass while proving nothing.
    currency = (estimate.currency or CURRENCY).strip().upper()
    legitimate = {format_amount(item.subtotal, currency) for item in estimate.line_items}
    legitimate |= {
        format_amount(value, currency)
        for value in (
            estimate.cost.subtotal,
            estimate.cost.total,
            estimate.cost.tax_amount,
            estimate.cost.contingency_amount,
            -abs(estimate.cost.discount_amount),
        )
    }
    printed = {cell.replace("**", "").strip() for row in rows for cell in row}
    candidates = [
        item
        for item in estimate.line_items
        if abs(item.quantity - 1.0) > 1e-9
        and item.unit_rate > 0
        and format_amount(item.unit_rate, currency) not in legitimate
    ]
    assert candidates, (
        "every line item in the fixture either has quantity 1 or a rate that formats like a "
        "figure the table legitimately prints - this check would prove nothing"
    )
    leaked = [
        (item.id, format_amount(item.unit_rate, currency))
        for item in candidates
        if format_amount(item.unit_rate, currency) in printed
    ]
    assert not leaked, f"unit rates reached the client Investment table: {leaked}"
    proved.append(f"none of {len(candidates)} distinguishable unit rates appear anywhere in it")

    return proved


def check_printed_cost_reconciles(estimate: Estimate) -> list[str]:
    """The Investment table's own rows must add up to the total printed under them.

    This reads the rendered document rather than the object, because the defect
    it guards against lives in the renderer: in tax-inclusive mode the tax is
    already inside the rates, so printing it as a row in the addition would make
    the column overshoot its own total while every figure remained individually
    correct. Both modes are rendered and both are parsed.
    """
    import re

    proved: list[str] = []
    money = re.compile(r"-?[\d,]+(?:\.\d+)?")

    def cost_rows(document: str) -> list[tuple[str, float]]:
        lines = document.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("## Investment"))
        found: list[tuple[str, float]] = []
        seen_subtotal = False
        for line in lines[start:]:
            if not line.startswith("|"):
                if found:
                    break
                continue
            cells = [c.strip().replace("**", "") for c in re.split(r"(?<!\\)\|", line)[1:-1]]
            if len(cells) < 2 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            label, last = cells[0], cells[-1]
            # The summary block opens with a bare "Subtotal". Per-category rows
            # are "Subtotal - Design" and belong to the itemisation above it.
            if label == "Subtotal":
                seen_subtotal = True
            if not seen_subtotal:
                continue
            match = money.search(last)
            if not match:
                continue
            found.append((label, float(match.group(0).replace(",", ""))))
        return found

    for inclusive in (False, True):
        priced = estimate.model_copy(deep=True)
        priced.cost.tax_inclusive = inclusive
        priced = recompute(priced)
        document = render_client_proposal(priced)
        rows = cost_rows(document)

        assert rows, f"inclusive={inclusive}: no cost rows found in the Investment table"
        labels = [label for label, _ in rows]
        assert labels[0].startswith("Subtotal"), f"first cost row is {labels[0]!r}"
        assert labels[-1].startswith("Total"), f"last cost row is {labels[-1]!r}"

        printed_total = rows[-1][1]
        added = sum(value for _, value in rows[:-1])
        assert abs(added - printed_total) < CENT, (
            f"inclusive={inclusive}: the printed cost rows add to {added:,.2f} under a printed "
            f"total of {printed_total:,.2f} - the table does not sum to itself\n"
            + "\n".join(f"    {label}: {value:,.2f}" for label, value in rows)
        )
        assert abs(printed_total - priced.cost.total) < CENT, (
            f"inclusive={inclusive}: the document prints {printed_total:,.2f} but the estimate "
            f"holds {priced.cost.total:,.2f}"
        )

        tax_rows = [label for label in labels if TAX_LABEL in label]
        if inclusive:
            assert not tax_rows, (
                f"inclusive={inclusive}: {TAX_LABEL} is a row in the addition; it belongs in a "
                "memo below the total because it is already inside the rates"
            )
            assert "inclusive of" in document, "the inclusive memo is missing from the document"
            assert priced.cost.tax_amount > 0, "no tax was extracted from an inclusive total"
            assert abs(priced.cost.total - priced.cost.subtotal
                       - priced.cost.contingency_amount + priced.cost.discount_amount) < CENT, (
                "inclusive total is not subtotal + contingency - discount"
            )
        else:
            assert tax_rows, f"exclusive={not inclusive}: {TAX_LABEL} is missing from the addition"
            assert priced.cost.total > priced.cost.subtotal, "exclusive tax did not add anything"

        mode = "inclusive" if inclusive else "exclusive"
        proved.append(
            f"{mode}: {len(rows) - 1} printed rows sum to the printed total "
            f"({printed_total:,.2f}), {TAX_LABEL} "
            f"{'held out as a memo' if inclusive else 'added as a row'}"
        )

    return proved


def check_snap_to_total(estimate: Estimate) -> list[str]:
    """A revision asked for an exact total must land on it - and stay there.

    The correction has to sit on an input field. `recompute` derives every
    subtotal, the contingency, the tax, the total and the milestone amounts
    from quantities and rates, so a residual parked on any of those would be
    erased the next time the estimate was recosted. Recomputing twice more
    after snapping is what proves it was not.
    """
    proved: list[str] = []
    natural = estimate.cost.total

    for currency, targets in (
        (CURRENCY, [3_000_000.00, 500_000.00, round(natural, 2)]),
        ("JPY", [3_000_000.0]),
        ("KRW", [500_000_000.0]),
    ):
        priced = estimate.model_copy(deep=True)
        priced.currency = currency
        priced = recompute(priced)
        dp = money_decimals(currency)
        tolerance = 10.0**-dp / 2

        for target in targets:
            result = snap_to_total(priced, target)
            snapped = result.estimate
            landed = snapped.cost.total

            assert (abs(landed - target) < tolerance) == result.exact, (
                f"{currency} {target}: exact={result.exact} but landed on {landed}"
            )
            if not result.exact:
                # Unreachable by construction: with a tax percentage applied the
                # reachable totals step by more than one minor unit. It must
                # still be the nearest, and it must still explain itself.
                assert abs(landed - target) <= 10.0**-dp + 1e-9, (
                    f"{currency} {target}: {landed} is not the nearest reachable total"
                )
                assert result.note, "an inexact snap has to say why"

            rows = round(sum(item.subtotal for item in snapped.line_items), dp + 2)
            assert abs(rows - snapped.cost.subtotal) < tolerance, (
                f"{currency} {target}: rows {rows} != subtotal {snapped.cost.subtotal}"
            )
            for item in snapped.line_items:
                assert item.quantity >= 0, f"{currency} {target}: {item.id} went negative"
                assert abs(item.subtotal - round(item.quantity * item.unit_rate, dp)) < tolerance, (
                    f"{currency} {target}: {item.id} subtotal is not quantity x rate"
                )
            milestones = round(sum(m.amount for m in snapped.cost.payment_milestones), dp + 2)
            assert abs(milestones - landed) < tolerance, (
                f"{currency} {target}: milestones {milestones} != total {landed}"
            )

            # Quantities print at two decimals. Exactly one row - the one
            # carrying the correction - may have a printed effort that does not
            # multiply up to its printed amount. More than one means the
            # proportional scaling stopped rounding to what it displays.
            ragged = [
                item.id
                for item in snapped.line_items
                if item.unit_rate > 0
                and abs(round(round(item.quantity, 2) * item.unit_rate, dp) - item.subtotal)
                >= tolerance
            ]
            assert len(ragged) <= 1, (
                f"{currency} {target}: {len(ragged)} rows print an effort that does not "
                f"multiply up to their amount ({', '.join(ragged)}) - at most one is allowed"
            )

            stable = recompute(recompute(recompute(snapped)))
            assert abs(stable.cost.total - landed) < tolerance, (
                f"{currency} {target}: drifted to {stable.cost.total} after recosting - the "
                "correction is sitting on a derived field, not on a quantity"
            )

            verdict = "exact" if result.exact else f"nearest {landed:,.{dp}f}"
            proved.append(
                f"{currency} target {target:,.{dp}f}: {verdict}, rows and milestones "
                f"reconcile, stable over 3 further recomputes"
            )

    for bad in (0.0, -1.0):
        try:
            snap_to_total(estimate, bad)
        except CostingError:
            pass
        else:
            raise AssertionError(f"a target of {bad} was accepted")
    proved.append("a target of zero or less is refused")

    return proved


def check_the_word_proposal_is_absent(
    proposal_markdown: str, requirements_markdown: str
) -> list[str]:
    """Neither document calls itself a proposal.

    A quotation names a price the studio will stand behind. A proposal is a
    pitch, and they are different documents at different stages - a client who
    reads "proposal" over a figure with payment terms under it has been told
    the number is still open for discussion.

    The word is ruled out in the prompt and stripped from the title
    server-side; this is the check that says both are still working.
    """
    import re

    proved: list[str] = []
    pattern = re.compile(r"(?i)proposals?")

    for label, document in (
        ("client", proposal_markdown),
        ("developer", requirements_markdown),
    ):
        found = [line.strip() for line in document.splitlines() if pattern.search(line)]
        assert not found, f"the {label} document says 'proposal': {found[:2]}"
        proved.append(f"the {label} document never says 'proposal'")

    return proved


def check_money_formatting(proposal_markdown: str) -> list[str]:
    """The documents must print money as '₱1,234.50' - symbol and separators."""
    proved: list[str] = []

    assert "₱" in proposal_markdown, "no peso sign in the client proposal"
    proved.append("peso sign present")

    import re

    figures = re.findall(r"₱[\d,]+\.\d{2}", proposal_markdown)
    assert figures, "no '₱1,234.56'-shaped figure in the client proposal"
    separated = [figure for figure in figures if "," in figure]
    assert separated, f"no thousands separator in any figure (saw {figures[:5]})"
    proved.append(
        f"{len(figures)} formatted figures, {len(separated)} with thousands separators, "
        f"e.g. {separated[0]}"
    )

    assert "₱nan" not in proposal_markdown.lower(), "a NaN reached the document"
    proved.append("no NaN or unformatted float in the document")

    return proved


# --- entry point -------------------------------------------------------------


def main() -> int:
    print("PRISM smoke test - offline, no key, no network")
    print("=" * 78)

    estimate = recompute(build_estimate())

    print("\nArithmetic")
    for line in check(estimate):
        print(f"  ok  {line}")

    proposal_md = render_client_proposal(estimate)
    requirements_md = render_developer_requirements(estimate)
    proposal_html = render_print_html(
        proposal_md, "Booking platform for Blue Water Divers - Quotation", estimate, kind="proposal"
    )
    requirements_html = render_print_html(
        requirements_md,
        "Booking platform for Blue Water Divers - Developer requirements",
        estimate,
        kind="requirements",
    )

    print("\nUneven splits and zero-decimal currencies")
    for line in check_effort_allocation():
        print(f"  ok  {line}")

    print("\nDevelopment through to DevOps")
    for line in check_devops_reaches_the_developer(estimate):
        print(f"  ok  {line}")

    print("\nThe Investment table names work, not people")
    for line in check_roles_stay_out_of_the_investment(estimate):
        print(f"  ok  {line}")

    print("\nThe Investment table prices the work, not the hour")
    for line in check_rates_stay_out_of_the_investment(estimate):
        print(f"  ok  {line}")

    print("\nTax exclusive and inclusive")
    for line in check_printed_cost_reconciles(estimate):
        print(f"  ok  {line}")

    print("\nRevising onto an exact total")
    for line in check_snap_to_total(estimate):
        print(f"  ok  {line}")

    print("\nMoney formatting")
    for line in check_money_formatting(proposal_md):
        print(f"  ok  {line}")

    print("It is a quotation, not a proposal")
    for line in check_the_word_proposal_is_absent(proposal_md, requirements_md):
        print(f"  ok  {line}")

    assert "# " in proposal_md and "## Investment" in proposal_md, "the proposal lost its body"
    assert "## Functional requirements" in requirements_md, "the spec lost its requirements"
    assert proposal_html.startswith("<!doctype html>"), "the proposal HTML is not a document"
    assert "<table" in proposal_html, "the proposal HTML lost its line item table"
    assert "@page" in proposal_html, "the print stylesheet is missing"
    # Assert on the body class, not the bare string: `doc--duplicate` also
    # appears in the embedded stylesheet of every sheet, so a substring check
    # passes on the client proposal too and proves nothing.
    assert '<body class="doc doc--duplicate"' in requirements_html, (
        "the developer sheet lost its duplicate band"
    )
    assert '<body class="doc doc--original"' in proposal_html, (
        "the client sheet is rendering as the developer duplicate"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = {
        "proposal.md": proposal_md,
        "requirements.md": requirements_md,
        "proposal.html": proposal_html,
        "requirements.html": requirements_html,
    }
    print("\nWritten")
    for name, content in written.items():
        path = OUT_DIR / name
        path.write_text(content, encoding="utf-8")
        print(f"  ok  {path}  ({len(content):,} chars)")

    print("\nClient proposal - first 40 lines")
    print("-" * 78)
    for line in proposal_md.splitlines()[:40]:
        print(line)
    print("-" * 78)

    print("\nSMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
