"""What kind of work is being quoted, and what its second document looks like.

PRISM produces two documents: a quotation for the client, and a specification
for whoever does the work. The first is the same shape whatever the discipline -
scope, costing, terms. The second is not. It was built for software, down to its
fields: tech stack, API surface, deployment. Hand that to an accountant and the
model will invent a tech stack, because a model asked for one always does.

So a quotation carries the discipline it belongs to, and the discipline decides
what the second document contains. Six ship. Five of them PRISM can write well
because it knows what their sections are called; the sixth is "something else",
which takes a name and a set of headings that suit any professional engagement.

`software` is the default, and the reason is history rather than preference:
every quotation prepared before this module existed is a software quotation, and
its stored estimate has no `kind` field at all. Resolving an absent kind to
software means those documents print exactly what they have always printed.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = [
    "Kind",
    "SectionSpec",
    "KINDS",
    "DEFAULT",
    "OTHER",
    "resolve",
    "for_estimate",
    "title_for",
    "noun_for",
    "is_software",
]


class SectionSpec(NamedTuple):
    """One part of a requirements document."""

    id: str
    heading: str


class Kind(NamedTuple):
    """One discipline: what it is called, and what its document says."""

    id: str
    label: str
    #: What the picker says under the label. One line, no marketing.
    hint: str
    #: The noun the document is titled with: "Accounting Requirements".
    noun: str
    #: The document's parts, in order. Empty for software, which fills the
    #: typed fields on `DeveloperSpec` instead - see app/renderers/markdown.py.
    sections: tuple
    #: One paragraph handed to the model. Names the vocabulary of the
    #: discipline, and what it must not reach for.
    guidance: str


#: The set every professional engagement can be described with when PRISM does
#: not know the discipline by name. Deliberately generic and deliberately
#: complete: a document with these six parts is a document somebody can work to.
GENERIC = (
    SectionSpec("scope", "What is included"),
    SectionSpec("approach", "How the work will be done"),
    SectionSpec("deliverables", "What is handed over"),
    SectionSpec("acceptance", "How the work is accepted"),
    SectionSpec("assumptions", "What is assumed"),
    SectionSpec("risks", "What could go wrong"),
)


KINDS: tuple = (
    Kind(
        id="software",
        label="Web / Software Development",
        hint="Sites, apps, integrations",
        noun="Developer",
        sections=(),
        guidance="",
    ),
    Kind(
        id="accounting",
        label="Accounting & Finance",
        hint="Bookkeeping, audit, compliance",
        noun="Accounting",
        sections=(
            SectionSpec("engagement", "The engagement"),
            SectionSpec("periods", "Periods and volumes"),
            SectionSpec("basis", "Standards and compliance basis"),
            SectionSpec("records", "Records and access needed"),
            SectionSpec("deliverables", "Statements and schedules delivered"),
            SectionSpec("handover", "Handover and filing"),
            SectionSpec("assumptions", "Assumptions"),
        ),
        guidance=(
            "This is an accounting or finance engagement. Write in the vocabulary of the "
            "profession: periods, ledgers, reconciliations, trial balances, statutory "
            "deadlines, the reporting framework in use, who signs what. Say which records "
            "the client must provide and by when, because that is what an engagement runs "
            "aground on. Never write about a tech stack, an API, a deployment or a "
            "repository - this work has none."
        ),
    ),
    Kind(
        id="engineering",
        label="Engineering",
        hint="Civil, mechanical, electrical",
        noun="Engineering",
        sections=(
            SectionSpec("brief", "Design brief"),
            SectionSpec("standards", "Codes and standards"),
            SectionSpec("surveys", "Surveys and site information"),
            SectionSpec("deliverables", "Drawings and documents issued"),
            SectionSpec("approvals", "Approvals and submissions"),
            SectionSpec("acceptance", "How the work is accepted"),
            SectionSpec("assumptions", "Assumptions and exclusions"),
        ),
        guidance=(
            "This is an engineering engagement. Write in the vocabulary of the discipline: "
            "design brief, loadings, codes and standards by name where the brief allows, "
            "site survey, drawing registers, issue stages, submissions to an authority "
            "having jurisdiction. Be explicit about what is excluded - the cost of an "
            "engineering dispute is almost always in what nobody wrote down. Never write "
            "about a tech stack, an API or a deployment."
        ),
    ),
    Kind(
        id="design",
        label="Design",
        hint="Brand, product, interiors",
        noun="Design",
        sections=(
            SectionSpec("brief", "The brief"),
            SectionSpec("research", "Research and inputs"),
            SectionSpec("route", "Direction and how it is chosen"),
            SectionSpec("deliverables", "What is handed over, and in what formats"),
            SectionSpec("revisions", "Revision rounds"),
            SectionSpec("rights", "Ownership and rights"),
            SectionSpec("assumptions", "Assumptions"),
        ),
        guidance=(
            "This is a design engagement. Write in the vocabulary of the studio: brief, "
            "direction, concepts, revision rounds, source files and the formats they are "
            "delivered in, licensing and ownership of the work. Be exact about how many "
            "rounds are included, because that is the line every design engagement is "
            "argued over. Never write about a tech stack, an API or a deployment unless "
            "the brief itself asks for a build."
        ),
    ),
    Kind(
        id="marketing",
        label="Marketing",
        hint="Campaigns, content, media",
        noun="Marketing",
        sections=(
            SectionSpec("objective", "The objective"),
            SectionSpec("audience", "Audience and channels"),
            SectionSpec("plan", "What runs, and when"),
            SectionSpec("deliverables", "What is produced"),
            SectionSpec("measurement", "How it is measured"),
            SectionSpec("spend", "Media spend and who holds it"),
            SectionSpec("assumptions", "Assumptions"),
        ),
        guidance=(
            "This is a marketing engagement. Write in the vocabulary of the work: "
            "objective, audience, channels, calendar, assets produced, the measures that "
            "decide whether it worked. Say plainly whether media spend is included in the "
            "fee or billed by the client, because that is the single most common "
            "misunderstanding in this kind of engagement. Never write about a tech stack, "
            "an API or a deployment."
        ),
    ),
    Kind(
        id="other",
        label="Something else",
        hint="Anything you name yourself",
        noun="Project",
        sections=GENERIC,
        guidance=(
            "The studio named this discipline themselves. Use their words for it "
            "throughout, write in the register of that profession, and keep to the six "
            "sections given. Do not reach for software vocabulary - no tech stack, no "
            "API, no deployment - unless the brief itself describes building software."
        ),
    ),
)

BY_ID = {kind.id: kind for kind in KINDS}
DEFAULT = BY_ID["software"]
OTHER = BY_ID["other"]

#: A typed label is printed in a heading, so it is capped. Forty characters is a
#: long discipline name and a short accident.
MAX_LABEL = 40


def resolve(kind_id: str) -> Kind:
    """The kind for an id. Never raises; an unknown or absent id is software."""
    return BY_ID.get((kind_id or "").strip().lower(), DEFAULT)


def for_estimate(estimate) -> Kind:
    return resolve(getattr(estimate, "kind", "") or "")


def is_software(estimate) -> bool:
    return for_estimate(estimate).id == DEFAULT.id


def noun_for(estimate) -> str:
    """The word the document is titled with, e.g. "Accounting"."""
    kind = for_estimate(estimate)
    if kind.id != OTHER.id:
        return kind.noun

    typed = str(getattr(estimate, "kind_label", "") or "").strip()[:MAX_LABEL]
    return typed or kind.noun


def title_for(estimate) -> str:
    """What the requirements document is called, project included.

    "Accounting Requirements - Ledger cleanup". A quotation with no kind gets
    "Developer requirements - ...", which is the string PRISM has always
    printed, because that is what those documents are.
    """
    project = str(getattr(estimate, "project_name", "") or "").strip() or "Project"
    if is_software(estimate):
        return f"Developer requirements — {project}"
    return f"{noun_for(estimate)} Requirements — {project}"
