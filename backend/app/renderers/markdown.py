"""Markdown renderers for the two PRISM deliverables.

``render_client_proposal``  - the document the client receives.
``render_developer_requirements`` - the engineering handoff.

Both emit GitHub-flavoured markdown with no HTML tags, a stable heading
hierarchy starting at ``#``, and tables padded so the columns still line up in a
plain text editor.

Money discipline (see docs/CONTRACT.md section 2): every figure printed here is
read straight off the ``Estimate``. ``costing.py`` owns the arithmetic; the only
aggregation performed in this module is the even split of a line item's cost
across the requirements it names, in the developer sheet. Nothing else is
derived, and no figure is ever recomputed from ``quantity * unit_rate``.

The client's Investment table is one unbroken list of work. It carries neither
the role that performs a line, nor the category it belongs to, nor the rate the
line was priced at: the first two read as a staffing plan and the third as a
timesheet, and the client is buying an outcome. The role and the category stay
in the developer sheet, where they are engineering decisions; the rate stays in
the studio's own screen, where it is a pricing decision.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Dict, Iterable, List, Sequence, Tuple

from app import kinds
from app.schemas import (
    ApiEndpoint,
    Estimate,
    LineItem,
    Phase,
    Requirement,
    Risk,
    SpecSection,
    TechStackItem,
)

from .money import (
    currency_decimals,
    format_amount,
    format_money,
    format_pct,
    format_qty,
    format_quantity_with_unit,
    format_unit,
    normalise_code,
)

__all__ = [
    "render_client_proposal",
    "render_developer_requirements",
    "quotation_reference",
]


# --- text primitives ---------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_RUN = re.compile(r"\n{3,}")
#: A "<" that begins something a markdown renderer would treat as a raw HTML
#: tag. Model prose occasionally contains one; these documents contain no HTML,
#: so it is escaped to a literal. A "<" used as less-than is left alone, and an
#: already-escaped "<" is not escaped twice - these helpers must be idempotent
#: because a value can pass through _clean() and then _cell().
_TAG_OPEN = re.compile(r"(?<!\\)<(?=[!/?a-zA-Z])")
_BARE_PIPE = re.compile(r"(?<!\\)\|")

LEFT, RIGHT, CENTRE = "left", "right", "centre"

PRIORITY_LABELS = {
    "must": "Must have",
    "should": "Should have",
    "could": "Could have",
    "wont": "Out of scope for this phase",
}

TYPE_LABELS = {
    "functional": "Functional",
    "non_functional": "Non-functional",
}

CONFIDENCE_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}

_UNIT_ORDER = ["hour", "day", "week", "month", "item", "lump_sum"]


def _clean(value: object) -> str:
    """Collapse a model string to safe single-spaced prose. Never returns None."""
    if value is None:
        return ""
    text = getattr(value, "value", value)
    text = _CONTROL.sub(" ", str(text))
    text = _TAG_OPEN.sub("\\\\<", text)
    return _WHITESPACE.sub(" ", text).strip()


def _prose(value: object) -> str:
    """Multi-paragraph prose: keep paragraph breaks, drop stray indentation."""
    if value is None:
        return ""
    text = _CONTROL.sub(" ", str(getattr(value, "value", value)))
    text = _TAG_OPEN.sub("\\\\<", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    joined = "\n".join(lines).strip()
    return _BLANK_RUN.sub("\n\n", joined)


def _cell(value: object) -> str:
    """Make any model string safe to drop into a markdown table cell.

    Newlines become spaces and pipes are escaped - one raw pipe destroys a table.
    """
    return _BARE_PIPE.sub("\\\\|", _clean(value))


def _enum(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _display_width(text: str) -> int:
    return len(text)


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str] = (),
    max_pad: int = 60,
) -> List[str]:
    """Render a padded GFM table. Returns [] when there are no rows."""
    if not rows:
        return []

    columns = max(len(headers), max(len(row) for row in rows))
    headers = list(headers) + [""] * (columns - len(headers))
    aligns = list(aligns) + [LEFT] * (columns - len(aligns))

    grid: List[List[str]] = []
    for row in rows:
        padded = list(row) + [""] * (columns - len(row))
        grid.append([str(cell) for cell in padded[:columns]])

    widths: List[int] = []
    for index in range(columns):
        longest = _display_width(headers[index])
        for row in grid:
            longest = max(longest, _display_width(row[index]))
        widths.append(min(max(longest, 3), max_pad))

    def pad(text: str, index: int) -> str:
        width = widths[index]
        gap = width - _display_width(text)
        if gap <= 0:
            return text
        if aligns[index] == RIGHT:
            return " " * gap + text
        if aligns[index] == CENTRE:
            left = gap // 2
            return " " * left + text + " " * (gap - left)
        return text + " " * gap

    lines = ["| " + " | ".join(pad(headers[i], i) for i in range(columns)) + " |"]

    rule_cells = []
    for index in range(columns):
        width = max(widths[index], 3)
        if aligns[index] == RIGHT:
            rule_cells.append("-" * (width - 1) + ":")
        elif aligns[index] == CENTRE:
            rule_cells.append(":" + "-" * (width - 2) + ":")
        else:
            rule_cells.append(":" + "-" * (width - 1))
    lines.append("| " + " | ".join(rule_cells) + " |")

    for row in grid:
        lines.append("| " + " | ".join(pad(row[i], i) for i in range(columns)) + " |")

    return lines


def _bullets(items: Iterable[object]) -> List[str]:
    return [f"- {_clean(item)}" for item in items if _clean(item)]


def _numbered(items: Iterable[object]) -> List[str]:
    cleaned = [_clean(item) for item in items]
    return [f"{i}. {text}" for i, text in enumerate([c for c in cleaned if c], start=1)]


def _checklist(items: Iterable[object]) -> List[str]:
    return [f"- [ ] {_clean(item)}" for item in items if _clean(item)]


def _section(title: str, body: object) -> str:
    """A heading plus its body, or "" when the body is empty."""
    if isinstance(body, (list, tuple)):
        content = "\n".join(str(line) for line in body if str(line).strip())
    else:
        content = str(body or "").strip()
    if not content:
        return ""
    return f"{title}\n\n{content}"


def _join(blocks: Iterable[str]) -> str:
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


# --- shared derivations ------------------------------------------------------


def quotation_reference(estimate: Estimate) -> str:
    """The reference this quotation prints.

    The server sets `estimate.quotation_ref` once, from the studio's prefix and
    numbering, and that is what every document shows. The content-derived
    fallback below is for an estimate prepared before references were a setting,
    and for the smoke fixtures.

    Deterministic either way, on purpose: the markdown is written to disk when
    the bundle is created and the printable HTML is rendered later, so a
    reference recomputed at render time would make the two disagree.
    """
    stored = str(getattr(estimate, "quotation_ref", "") or "").strip()
    if stored:
        return stored

    seed = "|".join(
        [
            _clean(getattr(estimate, "client_name", "")),
            _clean(getattr(estimate, "project_name", "")),
            normalise_code(getattr(estimate, "currency", "")),
            _clean(getattr(estimate, "market_region", "")),
            f"{float(getattr(getattr(estimate, 'cost', None), 'total', 0.0) or 0.0):.2f}",
            str(len(getattr(estimate, "line_items", []) or [])),
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6].upper()
    return f"Q-{digest}"


def _long_date(value: date) -> str:
    return f"{value.day} {value:%B %Y}"


def _currency(estimate: Estimate) -> str:
    return normalise_code(getattr(estimate, "currency", "")) or "PHP"


def _project_name(estimate: Estimate) -> str:
    return _clean(estimate.project_name) or _clean(estimate.client.title) or "Untitled project"


def _confidence(estimate: Estimate) -> str:
    key = _enum(getattr(estimate, "confidence", ""))
    return CONFIDENCE_LABELS.get(key, key.title())


def _meta_table(rows: Sequence[Tuple[str, str]]) -> List[str]:
    """The title block: a two-column table with no header row."""
    body = [[f"**{label}**", _cell(value)] for label, value in rows if _clean(value)]
    return _table(["", ""], body, [LEFT, LEFT])


def _effort_by_unit(units: Dict[str, float]) -> str:
    """'40 hours, 2 weeks' - never a single number summed across unit kinds."""
    parts = []
    known = [key for key in _UNIT_ORDER if key in units]
    extra = sorted(key for key in units if key not in _UNIT_ORDER)
    for key in known + extra:
        quantity = units[key]
        if not quantity:
            continue
        parts.append(f"{format_qty(quantity)} {format_unit(key, quantity)}")
    return ", ".join(parts)


# --- client proposal ---------------------------------------------------------


def render_client_proposal(estimate: Estimate) -> str:
    """Render the client-facing proposal as GitHub-flavoured markdown."""
    if estimate is None:
        raise ValueError("render_client_proposal() requires an Estimate instance")

    narrative = estimate.client
    cost = estimate.cost
    currency = _currency(estimate)
    today = date.today()

    title = _clean(narrative.title) or f"Quotation — {_project_name(estimate)}"

    validity_days = int(getattr(narrative, "validity_days", 0) or 0)
    meta: List[Tuple[str, str]] = [
        ("Client", _clean(estimate.client_name)),
        ("Project", _project_name(estimate)),
        ("Quotation ref.", quotation_reference(estimate)),
        ("Date of issue", _long_date(today)),
    ]
    if validity_days > 0:
        meta.append(("Valid until", _long_date(today + timedelta(days=validity_days))))
        meta.append(("Validity", f"{format_qty(validity_days)} days"))
    meta.extend(
        [
            ("Currency", currency),
            ("Market basis", _clean(estimate.market_region)),
            ("Estimate confidence", _confidence(estimate)),
        ]
    )

    blocks: List[str] = [f"# {title}", "\n".join(_meta_table(meta))]

    blocks.append(_section("## Executive summary", _prose(narrative.executive_summary)))
    blocks.append(_section("## Our understanding", _prose(narrative.understanding)))
    blocks.append(_section("## Proposed solution", _prose(narrative.proposed_solution)))
    blocks.append(_section("## What is included", _bullets(narrative.scope_inclusions)))
    blocks.append(_section("## What is not included", _bullets(narrative.scope_exclusions)))
    blocks.append(_phases_section(estimate))
    blocks.append(_investment_section(estimate, currency))
    blocks.append(_payment_section(estimate, currency))
    blocks.append(_section("## Assumptions", _bullets(narrative.assumptions)))
    blocks.append(_section("## Next steps", _numbered(narrative.next_steps)))

    body = _join(blocks)

    if _has_body(body):
        closing = _closing_note(estimate, currency, validity_days)
        document = _join([body, "---", closing]) if closing else body
    else:
        document = _join(
            [
                body,
                "---",
                "This quotation was prepared from a brief that did not carry enough "
                "detail to scope the work. Send through the missing information and "
                "PRISM will price it.",
            ]
        )

    return document.rstrip() + "\n"


def _has_body(document: str) -> bool:
    """True when the document carries more than its title block."""
    return "## " in document


def _phases_section(estimate: Estimate, *, include_summary: bool = True) -> str:
    """The phase table, optionally without its prose lead-in.

    The proposal reuses this table and drops the summary: that paragraph is
    written in the quotation pass, and the proposal already carries its own
    "how we will work" section written for a different reader.
    """
    phases: List[Phase] = list(estimate.phases or [])
    summary = _prose(estimate.client.timeline_summary) if include_summary else ""

    rows = []
    for phase in phases:
        name = _cell(phase.name)
        objective = _cell(phase.objective)
        deliverables = "; ".join(_cell(item) for item in phase.deliverables if _clean(item))
        weeks = float(phase.duration_weeks or 0.0)
        duration = format_quantity_with_unit(weeks, "week") if weeks else ""
        if not any([name, objective, deliverables, duration]):
            continue
        rows.append([name or "—", objective, deliverables, duration])

    table = _table(
        ["Phase", "Objective", "Deliverables", "Duration"],
        rows,
        [LEFT, LEFT, LEFT, RIGHT],
    )

    body = _join([summary, "\n".join(table)])
    return _section("## Phases and timeline", body)


def _investment_section(estimate: Estimate, currency: str) -> str:
    cost = estimate.cost
    items = list(estimate.line_items or [])

    has_figures = bool(items) or bool(cost.total) or bool(cost.subtotal)
    if not has_figures:
        return ""

    # The currency is named once, in the Amount heading, and the figures under
    # it are bare. Twenty rows each carrying the same peso sign is twenty
    # repetitions of one fact; the symbol earns its place on the rows a reader
    # actually stops at, which are the totals below.
    #
    # There is no rate column. A rate printed beside a quantity turns the
    # document into a timesheet to be argued down line by line - "why is this
    # nine hours" - when what is being bought is the delivered thing. The
    # quantity and the unit stay, because the shape of the effort is part of
    # what the client is agreeing to; only the price-per-unit goes. The rate is
    # still on the studio's own screen (`LineItemTable` in the frontend) and
    # still what every amount here was computed from - it is simply not in the
    # document that leaves the building.
    headers = [
        "Description",
        "Qty",
        "Unit",
        f"Amount ({currency})",
    ]
    aligns = [LEFT, RIGHT, LEFT, RIGHT]
    rows: List[List[str]] = []

    # One unified list of work, with no headings above it and no per-group
    # subtotals inside it.
    #
    # The table used to be grouped by category - QA, PM, Infra, Design - which
    # reads as a list of departments, and a department is a role by another
    # name. The client is buying an outcome, not a staffing plan, and every
    # heading of that kind is an invitation to argue about who does the work
    # rather than about what the work is. Both the role and the category stay
    # in the developer sheet, where they are engineering decisions.
    for item in items:
        rows.append(
            [
                _cell(item.description) or _cell(item.id) or "Work item",
                format_qty(item.quantity),
                _cell(format_unit(item.unit, item.quantity)),
                format_amount(item.subtotal, currency),
            ]
        )

    if rows:
        rows.append(["", "", "", ""])

    rows.append(["**Subtotal**", "", "", f"**{format_money(cost.subtotal, currency)}**"])

    if cost.contingency_amount or cost.contingency_pct:
        label = "Contingency"
        if cost.contingency_pct:
            label = f"Contingency ({format_pct(cost.contingency_pct)})"
        rows.append([label, "", "", format_amount(cost.contingency_amount, currency)])

    if cost.discount_amount:
        rows.append(["Discount", "", "", format_amount(-abs(cost.discount_amount), currency)])

    tax_label = _cell(cost.tax_label)
    has_tax = bool(cost.tax_amount or cost.tax_pct or tax_label)
    label = tax_label or "Tax"
    if cost.tax_pct:
        label = f"{label} ({format_pct(cost.tax_pct)})"

    # Inclusive pricing puts the tax *inside* the amounts, so adding it as a row
    # here would make the column stop summing to the total it sits under. It
    # becomes a memo below the table instead: still stated, never added twice.
    if has_tax and not cost.tax_inclusive:
        rows.append([label, "", "", format_amount(cost.tax_amount, currency)])

    rows.append(["**Total**", "", "", f"**{format_money(cost.total, currency)}**"])

    if has_tax and cost.tax_inclusive:
        basis = (
            f"The total is inclusive of {label}, which accounts for "
            f"{format_money(cost.tax_amount, currency)} of it. Every amount above already "
            f"contains the tax; nothing further is added at invoicing."
        )
    elif has_tax:
        basis = f"{label} is added to the priced work and is shown as its own line above."
    else:
        basis = "No consumption tax is applied to this quotation."

    # `cost.rate_basis` is deliberately absent, and now doubly so: it explains
    # where the rates came from - a published card, a market band, a blended
    # squad - and this table no longer prints a rate for it to explain. Stating
    # it invites a negotiation about the derivation instead of a decision about
    # the work. It is in the developer sheet, where it is useful context rather
    # than an opening bid.
    body = [
        "\n".join(_table(headers, rows, aligns)),
        f"All amounts are stated in {currency}. {basis}",
    ]
    return _section("## Investment", _join(body))


def _payment_section(estimate: Estimate, currency: str) -> str:
    milestones = list(estimate.cost.payment_milestones or [])
    rows = []
    for milestone in milestones:
        label = _cell(milestone.label)
        trigger = _cell(milestone.trigger)
        if not any([label, trigger, milestone.percent, milestone.amount]):
            continue
        rows.append(
            [
                label or "Milestone",
                trigger,
                format_pct(milestone.percent) if milestone.percent else "",
                format_amount(milestone.amount, currency),
            ]
        )

    if not rows:
        return ""

    rows.append(["**Total**", "", "", f"**{format_money(estimate.cost.total, currency)}**"])

    table = _table(
        ["Milestone", "Becomes payable when", "Share", f"Amount ({currency})"],
        rows,
        [LEFT, LEFT, RIGHT, RIGHT],
    )
    return _section("## Payment schedule", "\n".join(table))


def _closing_note(estimate: Estimate, currency: str, validity_days: int) -> str:
    parts = []
    if validity_days > 0:
        parts.append(
            f"This quotation is valid for {format_qty(validity_days)} days from the date of issue."
        )
    parts.append(f"Figures are quoted in {currency}")
    market = _clean(estimate.market_region)
    if market:
        parts[-1] += f" at {market} market rates"
    parts[-1] += "."
    if estimate.client.scope_exclusions:
        parts.append(
            "Work outside this scope — including everything listed under *What is "
            "not included* — is quoted separately."
        )
    parts.append(f"Quote our reference {quotation_reference(estimate)} on any correspondence.")
    return " ".join(parts)


# --- developer requirements --------------------------------------------------


def render_developer_requirements(estimate: Estimate) -> str:
    """Render the engineering handoff as GitHub-flavoured markdown."""
    if estimate is None:
        raise ValueError("render_developer_requirements() requires an Estimate instance")

    # Everything below this line is written from the typed fields on
    # `DeveloperSpec`, and those describe software. A quotation of any other
    # discipline is written from its own sections instead, and reads none of
    # them - see app/kinds.py.
    if not kinds.is_software(estimate):
        return _render_kind_requirements(estimate)

    spec = estimate.developer
    currency = _currency(estimate)
    today = date.today()

    title = f"Developer requirements — {_project_name(estimate)}"

    meta: List[Tuple[str, str]] = [
        ("Project", _project_name(estimate)),
        ("Client", _clean(estimate.client_name)),
        ("Quotation ref.", quotation_reference(estimate)),
        ("Date of issue", _long_date(today)),
        ("Costing currency", currency),
        ("Market basis", _clean(estimate.market_region)),
        ("Estimate confidence", _confidence(estimate)),
        ("Requirements", str(len(estimate.requirements or [])) if estimate.requirements else ""),
    ]

    blocks: List[str] = [f"# {title}", "\n".join(_meta_table(meta))]

    blocks.append(_section("## Overview", _prose(spec.overview)))
    blocks.append(
        _section("## Reference material observations", _bullets(estimate.image_observations))
    )
    blocks.append(_section("## Architecture", _prose(spec.architecture_summary)))
    blocks.append(_tech_stack_section(spec.tech_stack))
    blocks.append(_functional_section(estimate))
    blocks.append(_non_functional_section(estimate))
    blocks.append(_section("## Data model notes", _prose(spec.data_model_notes)))
    blocks.append(_api_section(spec.api_surface))
    blocks.append(_section("## Integrations", _bullets(spec.integrations)))
    blocks.append(_effort_section(estimate, currency))
    blocks.append(_work_breakdown_section(estimate))
    blocks.append(_phase_plan_section(estimate))
    blocks.append(_risks_section(estimate.risks))
    blocks.append(_section("## Testing strategy", _prose(spec.testing_strategy)))
    blocks.append(_devops_section(spec.devops))
    blocks.append(_section("## Open questions", _numbered(spec.open_questions)))

    document = _join(blocks)

    if not _has_body(document):
        document = _join(
            [
                document,
                "The estimate returned no engineering detail for this brief. Re-run "
                "PRISM with a fuller description of the work before starting build.",
            ]
        )

    return document.rstrip() + "\n"


def _tech_stack_section(stack: Sequence[TechStackItem]) -> str:
    rows = []
    for entry in stack or []:
        layer = _cell(entry.layer)
        choice = _cell(entry.choice)
        if not layer and not choice:
            continue
        rows.append([layer or "—", choice, _cell(entry.rationale)])
    table = _table(["Layer", "Choice", "Why"], rows, [LEFT, LEFT, LEFT])
    return _section("## Technology stack", "\n".join(table))


def _requirement_display_id(requirement: Requirement, index: int, kind: str) -> str:
    explicit = _clean(requirement.id)
    if explicit:
        return explicit
    prefix = "NFR" if kind == "non_functional" else "FR"
    return f"{prefix}-{index:02d}"


def _requirement_block(requirement: Requirement, display_id: str) -> str:
    title = _clean(requirement.title) or "Untitled requirement"
    lines = [f"### {display_id} — {title}"]

    facets = [
        TYPE_LABELS.get(_enum(requirement.type), _enum(requirement.type).replace("_", "-").title()),
        PRIORITY_LABELS.get(_enum(requirement.priority), _enum(requirement.priority).title()),
    ]
    facets = [facet for facet in facets if facet]
    if facets:
        lines.append("")
        lines.append("*" + " · ".join(facets) + "*")

    description = _prose(requirement.description)
    if description:
        lines.append("")
        lines.append(description)

    criteria = _checklist(requirement.acceptance_criteria)
    if criteria:
        lines.append("")
        lines.append("**Acceptance criteria**")
        lines.append("")
        lines.extend(criteria)

    return "\n".join(lines)


def _split_requirements(estimate: Estimate) -> Tuple[List[Tuple[Requirement, str]], List[Tuple[Requirement, str]]]:
    functional: List[Tuple[Requirement, str]] = []
    non_functional: List[Tuple[Requirement, str]] = []
    for requirement in estimate.requirements or []:
        kind = _enum(requirement.type) or "functional"
        bucket = non_functional if kind == "non_functional" else functional
        display_id = _requirement_display_id(requirement, len(bucket) + 1, kind)
        bucket.append((requirement, display_id))
    return functional, non_functional


def _functional_section(estimate: Estimate) -> str:
    functional, _ = _split_requirements(estimate)
    blocks = [_requirement_block(requirement, display_id) for requirement, display_id in functional]
    return _section("## Functional requirements", "\n\n".join(blocks))


def _non_functional_section(estimate: Estimate) -> str:
    _, non_functional = _split_requirements(estimate)
    blocks = [
        _requirement_block(requirement, display_id) for requirement, display_id in non_functional
    ]

    constraints = _bullets(estimate.developer.non_functional)
    if constraints:
        blocks.append("\n".join(["### Additional constraints", ""] + constraints))

    return _section("## Non-functional requirements", "\n\n".join(blocks))


def _api_section(endpoints: Sequence[ApiEndpoint]) -> str:
    rows = []
    for endpoint in endpoints or []:
        method = _cell(endpoint.method).upper()
        path = _cell(endpoint.path)
        if not method and not path:
            continue
        rows.append(
            [
                method or "—",
                f"`{path}`" if path else "",
                _cell(endpoint.purpose),
                _cell(endpoint.request_notes),
                _cell(endpoint.response_notes),
            ]
        )
    table = _table(
        ["Method", "Path", "Purpose", "Request", "Response"],
        rows,
        [LEFT, LEFT, LEFT, LEFT, LEFT],
    )
    return _section("## API surface", "\n".join(table))


def _effort_section(estimate: Estimate, currency: str) -> str:
    line_items = list(estimate.line_items or [])
    if not line_items:
        return ""

    titles: Dict[str, str] = {}
    order: List[str] = []
    functional, non_functional = _split_requirements(estimate)
    for requirement, display_id in functional + non_functional:
        key = _clean(requirement.id) or display_id
        titles[key] = _clean(requirement.title) or "Untitled requirement"
        if key not in order:
            order.append(key)

    costs: Dict[str, float] = {}
    units: Dict[str, Dict[str, float]] = {}
    counts: Dict[str, int] = {}
    unattributed_cost = 0.0
    unattributed_units: Dict[str, float] = {}
    unattributed_count = 0

    for item in line_items:
        ids = [_clean(rid) for rid in (item.requirement_ids or []) if _clean(rid)]
        unit_key = _enum(item.unit) or "hour"
        amount = float(item.subtotal or 0.0)
        quantity = float(item.quantity or 0.0)

        if not ids:
            unattributed_cost += amount
            unattributed_units[unit_key] = unattributed_units.get(unit_key, 0.0) + quantity
            unattributed_count += 1
            continue

        share_cost = amount / len(ids)
        share_qty = quantity / len(ids)
        for rid in ids:
            if rid not in costs:
                costs[rid] = 0.0
                units[rid] = {}
                counts[rid] = 0
                if rid not in order:
                    order.append(rid)
            costs[rid] += share_cost
            units[rid][unit_key] = units[rid].get(unit_key, 0.0) + share_qty
            counts[rid] += 1

    allocations: List[Tuple[str, str, Dict[str, float], float]] = []
    for key in order:
        if key not in costs:
            continue
        allocations.append(
            (
                _cell(key),
                _cell(titles.get(key, "Not listed in the requirements above")),
                units[key],
                costs[key],
            )
        )

    if unattributed_count:
        allocations.append(
            (
                "Unattributed",
                f"Line items that name no requirement ({unattributed_count})",
                unattributed_units,
                unattributed_cost,
            )
        )

    if not allocations:
        return ""

    # An even split rarely divides cleanly, so round cumulatively rather than
    # per row: each row shows the difference between the rounded running total
    # and what has already been shown. The residual then lands on the last row
    # and the column sums to cost.subtotal exactly, which is the invariant every
    # document in this project has to hold.
    decimals = currency_decimals(currency)
    running_raw = 0.0
    running_shown = 0.0
    shares: List[float] = []
    for _, _, _, raw in allocations:
        running_raw += raw
        share = round(round(running_raw, decimals) - running_shown, decimals)
        running_shown = round(running_shown + share, decimals)
        shares.append(share)

    residual = round(round(float(estimate.cost.subtotal or 0.0), decimals) - running_shown, decimals)
    if residual:
        shares[-1] = round(shares[-1] + residual, decimals)

    rows = [
        [label, title, _effort_by_unit(unit_map), format_money(share, currency)]
        for (label, title, unit_map, _), share in zip(allocations, shares)
    ]
    rows.append(["**Total**", "", "", f"**{format_money(estimate.cost.subtotal, currency)}**"])

    table = _table(
        ["Requirement", "Title", "Effort", "Allocated cost"],
        rows,
        [LEFT, LEFT, LEFT, RIGHT],
    )

    # Where the rates came from. The client's copy does not carry this - it
    # answers a question they have not asked and invites a negotiation about
    # the derivation - but the engineer reading this sheet needs to know whether
    # the numbers are a published card or a market estimate.
    basis = _prose(estimate.cost.rate_basis)
    basis_note = f"**Rate basis.** {basis}" if basis else ""

    buffer = max(0.0, float(estimate.cost.contingency_absorbed_pct or 0.0))
    if buffer:
        rows_note = (
            f"Every effort figure above already carries the {format_pct(buffer)} delivery "
            f"buffer for this engagement. It is folded into the quantities rather than "
            f"itemised, so the client's copy shows no contingency line - the padding is real "
            f"and it is in these numbers."
        )
    else:
        rows_note = ""

    note = (
        "Where one line item serves several requirements its cost and effort are "
        "split evenly between them. Effort is listed per unit kind and never summed "
        "across kinds. Allocation is stated before contingency, discount and tax."
    )
    return _section(
        "## Effort allocation", _join(["\n".join(table), basis_note, rows_note, note])
    )


def _devops_section(plan) -> str:
    """The DevOps half of the handoff: everything after the code is written.

    Rendered as named subsections rather than one prose block, so a reader
    looking for the rollback story can find it without reading the rest. Any
    field the model left empty is omitted rather than printed as a heading with
    nothing under it.
    """
    parts: List[str] = []

    environments = _bullets(plan.environments)
    if environments:
        parts.append(_section("### Environments", environments))

    for title, value in (
        ("### Build and deployment pipeline", plan.ci_cd),
        ("### Infrastructure", plan.infrastructure),
        ("### Observability", plan.observability),
        ("### Release and rollback", plan.release_and_rollback),
        ("### Backup and recovery", plan.backup_and_recovery),
        ("### Secrets and access", plan.secrets_and_access),
    ):
        parts.append(_section(title, _prose(value)))

    return _section("## DevOps", _join(parts))


def _work_breakdown_section(estimate: Estimate) -> str:
    rows = []
    for index, item in enumerate(estimate.line_items or [], start=1):
        work = _cell(item.description) or "Work item"
        notes = _cell(item.notes)
        if notes:
            work = f"{work} — {notes}"
        requirement_ids = ", ".join(_cell(rid) for rid in (item.requirement_ids or []) if _clean(rid))
        rows.append(
            [
                _cell(item.id) or f"LI-{index:02d}",
                _cell(item.category),
                work,
                _cell(item.role),
                format_quantity_with_unit(item.quantity, item.unit),
                requirement_ids,
            ]
        )
    table = _table(
        ["ID", "Category", "Work", "Role", "Effort", "Requirements"],
        rows,
        [LEFT, LEFT, LEFT, LEFT, RIGHT, LEFT],
    )
    return _section("### Work breakdown", "\n".join(table))


def _phase_plan_section(estimate: Estimate) -> str:
    rows = []
    for phase in estimate.phases or []:
        name = _cell(phase.name)
        objective = _cell(phase.objective)
        weeks = float(phase.duration_weeks or 0.0)
        duration = format_quantity_with_unit(weeks, "week") if weeks else ""
        deliverables = "; ".join(_cell(item) for item in phase.deliverables if _clean(item))
        line_items = ", ".join(_cell(item) for item in phase.line_item_ids if _clean(item))
        if not any([name, objective, duration, deliverables, line_items]):
            continue
        rows.append([name or "—", objective, duration, deliverables, line_items])

    table = _table(
        ["Phase", "Objective", "Duration", "Deliverables", "Line items"],
        rows,
        [LEFT, LEFT, RIGHT, LEFT, LEFT],
    )
    return _section("## Phase plan", "\n".join(table))


def _risks_section(risks: Sequence[Risk]) -> str:
    rows = []
    for risk in risks or []:
        description = _cell(risk.description)
        if not description:
            continue
        rows.append(
            [
                description,
                _cell(risk.impact),
                _cell(risk.likelihood),
                _cell(risk.mitigation),
            ]
        )
    table = _table(
        ["Risk", "Impact", "Likelihood", "Mitigation"],
        rows,
        [LEFT, LEFT, LEFT, LEFT],
    )
    return _section("## Risks", "\n".join(table))


# --- requirements for every other discipline ---------------------------------


def _heading_key(heading: object) -> str:
    """A heading reduced to what it says, for matching one against another."""
    text = _clean(heading).lower().lstrip("#").strip()
    return text.rstrip(".:;,!?").strip()


def _kind_sections(estimate: Estimate, kind: kinds.Kind) -> List[str]:
    """The discipline's own parts, in the order it declares them.

    Matched by heading rather than by position: the model is asked for these
    sections by name but writes them in whatever order it thinks in, and prose
    printed under the wrong heading is worse than prose missing. A section it
    left empty is dropped - a heading with nothing under it reads as work
    nobody did. Anything it returned that no section asked for is dropped for
    the same reason the order is fixed: the kind decides what this document
    contains.
    """
    written: Dict[str, SpecSection] = {}
    for section in estimate.developer.sections or []:
        key = _heading_key(section.heading)
        if key and key not in written:
            written[key] = section

    blocks: List[str] = []
    for spec in kind.sections:
        section = written.get(_heading_key(spec.heading))
        if section is None:
            continue
        body = _join([_prose(section.body), "\n".join(_bullets(section.points))])
        blocks.append(_section(f"## {spec.heading}", body))
    return blocks


def _kind_non_functional_section(estimate: Estimate) -> str:
    """The non-functional requirements, without the developer's constraints list.

    `DeveloperSpec.non_functional` is one of the typed software fields, so it
    is not read here even though the requirements themselves are shared.
    """
    _, non_functional = _split_requirements(estimate)
    blocks = [
        _requirement_block(requirement, display_id) for requirement, display_id in non_functional
    ]
    return _section("## Non-functional requirements", "\n\n".join(blocks))


def _render_kind_requirements(estimate: Estimate) -> str:
    """The requirements document for a discipline that is not software.

    The same spine as the software sheet - who it is for, what was asked for,
    what it costs and when it happens - with the discipline's own sections
    where the architecture, the stack and the API surface sit in that one.
    """
    kind = kinds.for_estimate(estimate)
    currency = _currency(estimate)
    today = date.today()

    meta: List[Tuple[str, str]] = [
        ("Project", _project_name(estimate)),
        ("Client", _clean(estimate.client_name)),
        ("Quotation ref.", quotation_reference(estimate)),
        ("Date of issue", _long_date(today)),
        ("Costing currency", currency),
        ("Market basis", _clean(estimate.market_region)),
        ("Estimate confidence", _confidence(estimate)),
        ("Requirements", str(len(estimate.requirements or [])) if estimate.requirements else ""),
    ]

    blocks: List[str] = [f"# {kinds.title_for(estimate)}", "\n".join(_meta_table(meta))]

    blocks.append(
        _section("## Reference material observations", _bullets(estimate.image_observations))
    )
    blocks.extend(_kind_sections(estimate, kind))
    blocks.append(_functional_section(estimate))
    blocks.append(_kind_non_functional_section(estimate))
    blocks.append(_effort_section(estimate, currency))
    blocks.append(_work_breakdown_section(estimate))
    blocks.append(_phase_plan_section(estimate))
    blocks.append(_risks_section(estimate.risks))

    document = _join(blocks)

    if not _has_body(document):
        document = _join(
            [
                document,
                "The estimate returned no detail for this engagement. Re-run PRISM "
                "with a fuller description of the work before it starts.",
            ]
        )

    return document.rstrip() + "\n"
