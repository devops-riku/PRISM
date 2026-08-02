"""The proposal document.

Modelled on a real 27-page corporate proposal, and built from three sources that
never mix:

  * the **narrative** is the model's, and it is prose only - no figure survives
    from it, because it was never asked for one;
  * the **scope and the figures** are the quotation's, printed by the very
    functions the quotation itself calls, so the two documents cannot disagree;
  * the **terms** are the studio's, inserted character for character from the
    snapshot taken when the proposal was built.

Two rules follow, and both are worth stating outright.

A clause is never reworded on the way through. This is the one text in the
system a person may be held to, so it arrives as the studio typed it or it does
not arrive at all.

Nothing here computes a figure, and validity is counted from the quotation's own
issue date rather than from today. The quotation's markdown was rendered on an
earlier day and printed an expiry from it; two documents describing one offer
must not name two different last days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List

from app.renderers.markdown import (
    LEFT,
    _cell,
    _clean,
    _investment_section,
    _long_date,
    _payment_section,
    _phases_section,
    _prose,
    _table,
    quotation_reference,
)
from app.schemas import Estimate, ProposalDocument
from app.template import KNOWN as KNOWN_HEADINGS
from app.template import resolve as resolve_sections

__all__ = ["render_proposal"]

#: The line somebody signs on. Long enough to write across, short enough that
#: it does not run into the margin at the widths these documents print at.
RULE_LINE = "_" * 34

#: Room to sign. A blank markdown line collapses in both renderers - and so
#: does a non-breaking space, because Python counts it as whitespace - so the
#: gap is explicit. `html.py` and `pdf.py` both know this line means "leave a
#: space here", and any other markdown viewer renders it as an empty line.
BLANK_LINE = "&nbsp;"

#: A requirement marked `wont` is scope protection - the thing a client will
#: assume is included and is not. It is printed as an exclusion, never dropped.
_EXCLUDED = "wont"


def _priority(item) -> str:
    value = getattr(item, "priority", "")
    return str(getattr(value, "value", value) or "").lower()


def _kind(item) -> str:
    value = getattr(item, "type", "")
    return str(getattr(value, "value", value) or "").lower()


def _bullets(items, limit: int = 14) -> List[str]:
    out: List[str] = []
    for item in list(items or [])[:limit]:
        text = _clean(str(item))
        if text:
            out.append(f"- {text}")
    return out


def _paragraph(text: str) -> List[str]:
    cleaned = _prose(text)
    return [cleaned, ""] if cleaned else []


def _issued_on(document: ProposalDocument) -> date:
    """The day the quotation was issued, which is what validity runs from."""
    stamp = (document.quotation_issued_at or "").strip()
    if stamp:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()


def _scope_section(estimate: Estimate) -> List[str]:
    """What the engagement covers, from the requirements the quotation priced.

    Led by the quotation's own `scope_inclusions`, so a client holding both
    documents reads one answer to "what am I buying" rather than two in two
    vocabularies.
    """
    requirements = [
        item for item in (estimate.requirements or []) if _priority(item) != _EXCLUDED
    ]
    if not requirements:
        return []

    lines = ["## Scope of work", ""]

    inclusions = _bullets(getattr(estimate.client, "scope_inclusions", []), 12)
    if inclusions:
        lines.extend(inclusions)
        lines.append("")

    for label, wanted in (("Functional", "functional"), ("Non-functional", "non_functional")):
        rows = [
            [_cell(item.title), _cell(item.description)]
            for item in requirements
            if _kind(item) == wanted and _clean(item.title)
        ]
        if not rows:
            continue
        lines.append(f"### {label}")
        lines.append("")
        lines.extend(_table(["Requirement", "What it covers"], rows, [LEFT, LEFT]))
        lines.append("")

    return lines


def _exclusions_section(estimate: Estimate) -> List[str]:
    """Everything a client might assume is included and is not.

    Two sources, one list: what the estimator wrote as an exclusion, and every
    requirement it deliberately marked `wont`. The second half used to reach
    neither document - scope protection written and then thrown away.
    """
    written = [
        _clean(str(item)) for item in (getattr(estimate.client, "scope_exclusions", []) or [])
    ]
    declined = [
        _clean(item.title)
        for item in (estimate.requirements or [])
        if _priority(item) == _EXCLUDED and _clean(item.title)
    ]

    seen: set[str] = set()
    items: List[str] = []
    for text in [*written, *declined]:
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            items.append(text)

    if not items:
        return []
    return ["## What is not included", "", *[f"- {item}" for item in items], ""]


def _acceptance_basis(estimate: Estimate) -> List[str]:
    """How each piece of scope is judged done.

    Every requirement in the scope table that carries criteria, not only the
    must-haves. A partial table hands a client the argument that acceptance was
    limited to whatever happened to be printed.
    """
    rows = []
    for item in estimate.requirements or []:
        if _priority(item) == _EXCLUDED:
            continue
        criteria = [_clean(str(entry)) for entry in (item.acceptance_criteria or [])]
        criteria = [entry for entry in criteria if entry]
        if not criteria or not _clean(item.title):
            continue
        rows.append([_cell(item.title), _cell("; ".join(criteria))])

    if not rows:
        return []
    return [
        "## How work is accepted",
        "",
        *_table(["Requirement", "Accepted when"], rows, [LEFT, LEFT]),
        "",
    ]


def render_proposal(document: ProposalDocument, estimate: Estimate) -> str:
    """The client-facing proposal, as GitHub-flavoured markdown.

    The section order is the studio's, snapshotted onto the document when it was
    built. Every id maps to a builder below; an id with no builder is skipped
    rather than guessed at, because a heading with nothing under it is worse
    than a missing heading.
    """
    narrative = document.narrative
    issued = _issued_on(document)
    validity_days = int(getattr(estimate.client, "validity_days", 0) or 0)

    studio = _clean(document.studio_name)
    client = _clean(document.client_name)
    project = _clean(document.project_name) or "the engagement"
    title = _clean(narrative.title) or project

    # --- cover -------------------------------------------------------------
    # A title, who it is for, and nothing else. The reference opens the same
    # way: one page saying what this is before anybody reads a word of it. The
    # rule is where the page turns - `render_print_html` and `render_pdf` both
    # break on the first one when they are building a proposal.
    lines: List[str] = [f"# {title}", ""]

    if client:
        lines.append(f"### For {client}")
        lines.append("")
    if studio:
        lines.append(f"Prepared by **{studio}**")
        lines.append("")

    lines.append("---")
    lines.append("")

    meta = [
        ("Prepared for", client),
        ("Prepared by", studio),
        # Both, in this order: the proposal is the document being signed, and the
        # quotation is what it is priced from. A document built before proposals
        # were numbered has no reference of its own and simply omits the row.
        ("Proposal no.", getattr(document, "reference", "")),
        ("Quotation ref.", quotation_reference(estimate)),
        ("Date of issue", _long_date(issued)),
    ]
    if validity_days > 0:
        meta.append(("Valid until", _long_date(issued + timedelta(days=validity_days))))

    lines.append("| | |")
    lines.append("|---|---|")
    for label, value in meta:
        if value:
            lines.append(f"| **{_cell(label)}** | {_cell(value)} |")
    lines.append("")

    # --- one builder per section id ----------------------------------------

    def cover_letter(_heading: str) -> List[str]:
        if not _clean(narrative.cover_letter):
            return []
        out: List[str] = []
        if client:
            out.append(f"Dear {client},")
            out.append("")
        for block in str(narrative.cover_letter).split("\n\n"):
            out.extend(_paragraph(block))
        if studio:
            out.append(f"\u2014 {studio}")
            out.append("")
        return out

    def prose(text: str):
        def build(heading: str) -> List[str]:
            if not _clean(text):
                return []
            return [f"## {heading}", "", *_paragraph(text)]

        return build

    def listing(items, limit: int = 14):
        def build(heading: str) -> List[str]:
            bullets = _bullets(items, limit)
            if not bullets:
                return []
            return [f"## {heading}", "", *bullets, ""]

        return build

    def numbered(items, limit: int = 8):
        def build(heading: str) -> List[str]:
            rows = [_clean(str(item)) for item in list(items or [])[:limit]]
            rows = [row for row in rows if row]
            if not rows:
                return []
            return [
                f"## {heading}",
                "",
                *[f"{index}. {row}" for index, row in enumerate(rows, start=1)],
                "",
            ]

        return build

    def phases(heading: str) -> List[str]:
        # The quotation's own phase table, without its timeline lead-in: that
        # paragraph belongs to the quotation pass, and the approach section
        # above already says how the work runs, written for this reader.
        table = _phases_section(estimate, include_summary=False)
        if not table:
            return []
        out = table.splitlines()
        if out and out[0].startswith("## "):
            out[0] = f"## {heading}"
        return [*out, ""]

    def investment(heading: str) -> List[str]:
        # Every figure in the document comes from here and from the payment
        # table below, and both are the functions the quotation itself calls. A
        # proposal that restated the costing in its own words would eventually
        # restate it wrongly.
        table = _investment_section(estimate, estimate.currency)
        if not table:
            return []
        out = table.splitlines()
        if out and out[0].startswith("## "):
            out[0] = f"## {heading}"
        return [*out, ""]

    def payment(heading: str) -> List[str]:
        table = _payment_section(estimate, estimate.currency)
        if not table:
            return []
        out = table.splitlines()
        if out and out[0].startswith("## "):
            out[0] = f"## {heading}"
        return [*out, ""]

    def terms(heading: str) -> List[str]:
        if not document.policies:
            return []
        out = [f"## {heading}", ""]
        for index, clause in enumerate(document.policies, start=1):
            out.append(f"**{index}. {clause.title}**")
            out.append("")
            # Verbatim: not cleaned, not re-wrapped, not summarised.
            out.append(clause.body)
            out.append("")
        return out

    def signatures(heading: str) -> List[str]:
        # The shape is the reference's, line for line.
        #
        # "Signatures", not "Acceptance": a clause in the terms above already
        # governs how delivered work is accepted, and one word cannot mean both.
        # What makes this document binding is itself a clause, so it is not
        # written here in code.
        #
        # The studio's side is typed because it is known, with room above it for
        # a wet signature. The client's side is a ruled line under a bold label
        # saying what belongs on it - the label sits *below* the rule, which is
        # what stops somebody writing their name where the position goes.
        who = _clean(document.signatory)
        role = _clean(document.signatory_title)
        named = ", ".join(part for part in (who, role) if part)

        out = [f"## {heading}", "", "PREPARED BY:", ""]

        # Space for a signature over the printed name. A blank markdown line
        # collapses; a line holding a non-breaking space survives into both
        # exports, which is the only way to leave a gap somebody can sign in.
        out.extend([BLANK_LINE, "", BLANK_LINE, ""])

        # Separate paragraphs, not markdown hard breaks: the PDF joins
        # consecutive lines into one paragraph, which ran the name and the
        # company together on a single line.
        if named:
            out.append(f"**{named}**")
            out.append("")
        if studio:
            out.append(studio)
            out.append("")

        out.append(BLANK_LINE)
        out.append("")
        out.append("**Acknowledgement**")
        out.append("")
        out.append(
            "The terms and conditions of this proposal have been read and understood by "
            f"{client or 'the client'}."
        )
        out.append("")
        out.append("Signed:")
        out.append("")

        for label, second in (
            ("Signature over printed name", f"Authorised representative of {client}" if client else ""),
            ("Position", ""),
            ("Date", ""),
        ):
            out.append(RULE_LINE)
            out.append("")
            out.append(f"**{label}**")
            if second:
                out.append("")
                out.append(f"**{second}**")
            out.append("")
            out.append(BLANK_LINE)
            out.append("")

        return out

    def headed(builder):
        """Wrap a section that builds its own heading-less body."""

        def build(heading: str) -> List[str]:
            body = builder(estimate)
            if not body:
                return []
            if body and body[0].startswith("## "):
                body = [f"## {heading}", *body[1:]]
            return body

        return build

    builders = {
        "cover_letter": cover_letter,
        "executive_summary": prose(narrative.executive_summary),
        "understanding": prose(narrative.understanding),
        "scope_overview": prose(narrative.scope_overview),
        "scope": headed(_scope_section),
        "exclusions": headed(_exclusions_section),
        "approach": prose(narrative.approach),
        "phases": phases,
        "acceptance": headed(_acceptance_basis),
        "deliverables": listing(narrative.deliverables),
        "assumptions": listing(getattr(estimate.client, "assumptions", []), 12),
        "risks": listing(narrative.risks_addressed, 8),
        "why_us": listing(narrative.why_us, 6),
        "investment": investment,
        "payment": payment,
        "next_steps": numbered(narrative.next_steps),
        "terms": terms,
        "signatures": signatures,
    }

    # The snapshot is already resolved - it is what `build_document` worked out
    # from the studio's template on the day this was built. Resolving it again
    # would treat every section the studio had switched OFF as one PRISM had
    # just added, and append it to the end of a document that deliberately did
    # not have it.
    order = document.sections or resolve_sections([])

    for section in order:
        build = builders.get(section.id)
        if build is None:
            continue
        lines.extend(build(section.heading or KNOWN_HEADINGS.get(section.id, section.id)))

    return "\n".join(lines).rstrip() + "\n"
