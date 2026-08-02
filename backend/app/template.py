"""Which sections a proposal has, in what order, under what headings.

The renderer knows how to build every section. What it should not decide is
which ones this studio sends and what they are called - a studio that never
quotes fixed phases wants the timeline out, and one whose clients expect
"Investment" to read "Commercials" should not have to ask for a code change.

So the order lives here as data, is edited in Settings, and is snapshotted onto
each proposal alongside its terms. A proposal built in March has March's shape.

What this is NOT: a way to invent a section. Every id below maps to a builder in
`renderers/proposal.py` that knows where its content comes from - the model, the
quotation, or the studio's own clauses. An id with no builder is dropped rather
than guessed at, because a heading with nothing under it is worse than a missing
heading.
"""

from __future__ import annotations

from typing import Iterable, List

from pydantic import BaseModel, Field

__all__ = ["SectionSpec", "DEFAULT_SECTIONS", "KNOWN", "resolve", "normalise"]


class SectionSpec(BaseModel):
    """One section of the proposal, as the studio wants it."""

    id: str = Field(default="", description="Which builder this is. See KNOWN.")
    heading: str = Field(default="", description="The heading printed. Empty uses the default.")
    enabled: bool = Field(default=True, description="False leaves the section out entirely.")

    def normalised(self) -> "SectionSpec":
        return SectionSpec(
            id=(self.id or "").strip().lower()[:40],
            heading=(self.heading or "").strip()[:80],
            enabled=bool(self.enabled),
        )


#: The document as it ships, in the order the reference document uses. `source`
#: is not configuration - it is here so the editor can tell a studio where each
#: section's content comes from, which is the thing that decides whether they
#: are allowed to rewrite it.
DEFAULT_SECTIONS: List[dict] = [
    {"id": "cover_letter", "heading": "", "source": "written"},
    {"id": "executive_summary", "heading": "In brief", "source": "written"},
    {"id": "understanding", "heading": "What we understand", "source": "written"},
    {"id": "scope_overview", "heading": "What we propose", "source": "written"},
    {"id": "scope", "heading": "Scope of work", "source": "quotation"},
    {"id": "exclusions", "heading": "What is not included", "source": "quotation"},
    {"id": "approach", "heading": "How we will work", "source": "written"},
    {"id": "phases", "heading": "Phases and timeline", "source": "quotation"},
    {"id": "acceptance", "heading": "How work is accepted", "source": "quotation"},
    {"id": "deliverables", "heading": "What you will have", "source": "written"},
    {"id": "assumptions", "heading": "Assumptions", "source": "quotation"},
    {"id": "risks", "heading": "What could go wrong, and what we do about it", "source": "written"},
    {"id": "why_us", "heading": "Why us for this", "source": "written"},
    {"id": "investment", "heading": "Investment", "source": "quotation"},
    {"id": "payment", "heading": "Payment schedule", "source": "quotation"},
    {"id": "next_steps", "heading": "Next steps", "source": "written"},
    {"id": "terms", "heading": "Terms", "source": "studio"},
    {"id": "signatures", "heading": "Signatures", "source": "studio"},
]

#: id -> default heading. The renderer refuses anything not in here.
KNOWN = {entry["id"]: entry["heading"] for entry in DEFAULT_SECTIONS}

#: id -> where its content comes from, for the editor to display.
SOURCES = {entry["id"]: entry["source"] for entry in DEFAULT_SECTIONS}

#: Sections a proposal is not a proposal without. They can be renamed and
#: reordered; they cannot be switched off. Terms and signatures are what make
#: the document something a client can act on, and the investment is what they
#: are being asked to agree to - a "proposal" missing any of the three is a
#: letter, and sending one by accident is not a mistake worth allowing.
REQUIRED = frozenset({"investment", "terms", "signatures"})


def normalise(sections: Iterable[object]) -> List[SectionSpec]:
    """Clean a configured order: known ids only, no duplicates, required kept.

    Takes anything carrying `id` and `heading`. The settings model and the
    snapshot stored on a document are different types describing one thing, and
    this is where they meet - asking each to grow the other's methods would put
    the same three lines in two files.
    """
    seen: set[str] = set()
    out: List[SectionSpec] = []
    for section in sections or []:
        cleaned = SectionSpec(
            id=str(getattr(section, "id", "") or ""),
            heading=str(getattr(section, "heading", "") or ""),
            enabled=bool(getattr(section, "enabled", True)),
        ).normalised()
        if cleaned.id not in KNOWN or cleaned.id in seen:
            continue
        seen.add(cleaned.id)
        if cleaned.id in REQUIRED:
            cleaned.enabled = True
        out.append(cleaned)
    return out


def resolve(sections: Iterable[SectionSpec]) -> List[SectionSpec]:
    """The order a proposal being built right now should follow.

    A studio that has configured nothing gets the default document.

    One that has configured something gets exactly that order - their order is
    authoritative, not a set of hints merged back into the default. Any section
    added to PRISM since they last saved is appended at the end and enabled,
    because a document that quietly lost a section is worse than one with an
    unfamiliar heading at the bottom that somebody can then move.
    """
    configured = normalise(sections)
    if not configured:
        return [
            SectionSpec(id=entry["id"], heading=entry["heading"], enabled=True)
            for entry in DEFAULT_SECTIONS
        ]

    known_here = {section.id for section in configured}
    merged = [
        *configured,
        *(
            SectionSpec(id=entry["id"], heading=entry["heading"], enabled=True)
            for entry in DEFAULT_SECTIONS
            if entry["id"] not in known_here
        ),
    ]

    return [section for section in merged if section.enabled or section.id in REQUIRED]
