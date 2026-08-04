"""Single source of truth for the proposal data contract.

Every other module in this project is a renderer of `Estimate`:
the Gemini call fills it, the costing pass corrects its arithmetic, the
markdown/HTML renderers format it, and the React client displays it.

Design constraints (do not relax without changing every consumer):
  * No `Optional[...]` unions and no bare `dict` / `Any` fields. The Gemini
    structured-output layer converts this model to an OpenAPI-subset schema and
    both constructs degrade the conversion.
  * Every field has a default so a partial model response still validates.
  * Monetary fields are plain floats in the *selected currency*. There is no FX
    conversion anywhere in this system - the model is asked to price directly in
    the requested currency for the requested market.
"""

from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, Field, model_validator

# Storage-side only: `ProposalDocument` is written to disk and read by the
# client, never handed to Gemini, so it may carry a nested model the structured
# -output layer never sees.
from app.design import ProposalDesign


class Priority(str, Enum):
    must = "must"
    should = "should"
    could = "could"
    wont = "wont"


class RequirementType(str, Enum):
    functional = "functional"
    non_functional = "non_functional"


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class UnitKind(str, Enum):
    hour = "hour"
    day = "day"
    week = "week"
    month = "month"
    item = "item"
    lump_sum = "lump_sum"


class Requirement(BaseModel):
    id: str = Field(default="", description="Stable ID, e.g. FR-01 or NFR-03.")
    title: str = ""
    description: str = ""
    type: RequirementType = RequirementType.functional
    priority: Priority = Priority.must
    acceptance_criteria: List[str] = Field(default_factory=list)


class LineItem(BaseModel):
    id: str = Field(default="", description="Stable ID, e.g. LI-01.")
    category: str = Field(default="", description="Design, Frontend, Backend, QA, PM, Infra, Third-party.")
    description: str = ""
    role: str = Field(default="", description="Who performs it, e.g. Senior Backend Engineer.")
    quantity: float = 0.0
    unit: UnitKind = UnitKind.hour
    unit_rate: float = Field(default=0.0, description="Rate per unit in the selected currency.")
    subtotal: float = Field(default=0.0, description="Advisory only - recomputed server-side as quantity * unit_rate.")
    requirement_ids: List[str] = Field(default_factory=list)
    notes: str = ""


class Phase(BaseModel):
    name: str = ""
    objective: str = ""
    deliverables: List[str] = Field(default_factory=list)
    duration_weeks: float = 0.0
    line_item_ids: List[str] = Field(default_factory=list)


class PaymentMilestone(BaseModel):
    label: str = ""
    percent: float = 0.0
    amount: float = Field(default=0.0, description="Advisory only - recomputed server-side from percent * total.")
    trigger: str = Field(default="", description="What must happen for this to become payable.")


class CostSummary(BaseModel):
    subtotal: float = 0.0
    contingency_pct: float = Field(default=0.0, description="Percent, e.g. 10 means 10%.")
    contingency_amount: float = 0.0
    discount_amount: float = 0.0
    tax_label: str = Field(default="", description="e.g. VAT. Empty string means no tax line.")
    tax_pct: float = 0.0
    tax_inclusive: bool = Field(
        default=False,
        description=(
            "False: the tax is added on top of the priced work, and `total` is larger than "
            "`subtotal` by it. True: the line item rates already contain the tax, `total` is "
            "the priced work itself, and `tax_amount` is the portion extracted from it. This "
            "is an input to the costing, never derived from it."
        ),
    )
    tax_amount: float = Field(
        default=0.0,
        description="Advisory only - recomputed server-side, added on top or extracted per `tax_inclusive`.",
    )
    contingency_absorbed_pct: float = Field(
        default=0.0,
        description=(
            "Server-set, never by the model. When the studio does not itemise contingency to "
            "the client, the buffer is folded into the line item quantities and the percentage "
            "it came from is recorded here so the developer sheet can still state it."
        ),
    )
    total: float = 0.0
    payment_milestones: List[PaymentMilestone] = Field(default_factory=list)
    rate_basis: str = Field(default="", description="One sentence on how the rates were derived for this market.")


class Risk(BaseModel):
    description: str = ""
    impact: str = ""
    likelihood: str = ""
    mitigation: str = ""


class TechStackItem(BaseModel):
    layer: str = Field(default="", description="e.g. Frontend, API, Datastore, Hosting.")
    choice: str = ""
    rationale: str = ""


class ApiEndpoint(BaseModel):
    method: str = ""
    path: str = ""
    purpose: str = ""
    request_notes: str = ""
    response_notes: str = ""


class DevOpsPlan(BaseModel):
    """How the work gets from a developer's machine to production, and stays there.

    A spec that stops at "deployment notes" leaves the most expensive surprises
    unwritten: who owns the environments, what happens on a bad release, where
    the secrets live. Each field is one short paragraph, or a list of one-line
    entries for `environments`.
    """

    environments: List[str] = Field(
        default_factory=list,
        description="One line each, e.g. 'Staging - client acceptance, AWS ap-southeast-1, restored weekly from production'.",
    )
    ci_cd: str = Field(default="", description="What runs on a push, what gates a merge, what triggers a deploy.")
    infrastructure: str = Field(default="", description="Where it runs and how it is provisioned.")
    observability: str = Field(default="", description="Logs, metrics, traces, alerts, and who is paged.")
    release_and_rollback: str = Field(default="", description="How a release ships and how it is undone.")
    backup_and_recovery: str = Field(default="", description="What is backed up, how often, and the restore drill.")
    secrets_and_access: str = Field(default="", description="Where credentials live and who can reach production.")


class SpecSection(BaseModel):
    """One part of a requirements document, for a discipline that is not software.

    A small model in a list rather than a bare dict: this file's own constraint,
    and the reason is the structured-output layer rather than taste.
    """

    heading: str = ""
    body: str = ""
    points: List[str] = Field(default_factory=list)


class ClientNarrative(BaseModel):
    """Prose for the client-facing proposal. Numbers never live here."""

    title: str = ""
    executive_summary: str = ""
    understanding: str = Field(default="", description="Restate the client's situation in their own terms.")
    proposed_solution: str = ""
    scope_inclusions: List[str] = Field(default_factory=list)
    scope_exclusions: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    timeline_summary: str = ""
    next_steps: List[str] = Field(default_factory=list)
    validity_days: int = 30


class DeveloperSpec(BaseModel):
    """Prose for the handoff document.

    The typed fields below describe software, because that is what PRISM was
    built to quote. Every other discipline fills `sections` instead - see
    app/kinds.py. Nothing reads both: the renderer branches on the kind once,
    at the top.
    """

    overview: str = ""
    architecture_summary: str = ""
    tech_stack: List[TechStackItem] = Field(default_factory=list)
    data_model_notes: str = ""
    api_surface: List[ApiEndpoint] = Field(default_factory=list)
    integrations: List[str] = Field(default_factory=list)
    non_functional: List[str] = Field(default_factory=list)
    testing_strategy: str = ""
    devops: DevOpsPlan = Field(
        default_factory=DevOpsPlan,
        description="Environments, pipeline, infrastructure, observability, release, backup, secrets.",
    )
    open_questions: List[str] = Field(default_factory=list)

    sections: List[SpecSection] = Field(
        default_factory=list,
        description=(
            "The document's parts, for every discipline except software - one entry per "
            "section that kind declares, in order. Software leaves this empty and fills the "
            "typed fields above instead."
        ),
    )


class Estimate(BaseModel):
    """The complete Gemini response. Both output documents render from this."""

    project_name: str = ""
    client_name: str = ""
    kind: str = Field(
        default="software",
        description=(
            "The discipline this work belongs to - one of the ids in app/kinds.py. Defaults "
            "to software because every quotation prepared before kinds existed is one, and "
            "an absent value must keep printing what it has always printed."
        ),
    )
    kind_label: str = Field(
        default="",
        description="What the studio called the discipline, when `kind` is 'other'. Empty otherwise.",
    )
    currency: str = Field(default="PHP", description="ISO 4217 code the line items are priced in.")
    market_region: str = Field(default="Philippines", description="Market whose going rates were used.")
    confidence: Confidence = Confidence.medium
    image_observations: List[str] = Field(
        default_factory=list,
        description="What each uploaded image showed and how it changed the scope. Empty when no images were sent.",
    )
    requirements: List[Requirement] = Field(default_factory=list)
    phases: List[Phase] = Field(default_factory=list)
    line_items: List[LineItem] = Field(default_factory=list)
    cost: CostSummary = Field(default_factory=CostSummary)
    risks: List[Risk] = Field(default_factory=list)
    client: ClientNarrative = Field(default_factory=ClientNarrative)
    developer: DeveloperSpec = Field(default_factory=DeveloperSpec)
    quotation_ref: str = Field(
        default="",
        description=(
            "Server-set, never by the model. The reference the documents print, fixed once at "
            "creation from the studio's prefix and numbering. Anything written here is "
            "overwritten - a quotation number the model invented is not a quotation number."
        ),
    )


class ProposalNarrative(BaseModel):
    """The persuasive half of a proposal - the only half a model writes.

    There is deliberately no field here for terms, conditions, warranty,
    ownership or payment wording. Those are the studio's, inserted verbatim by
    the renderer from `settings.policies`. A schema field for them would be
    filled in, and prose nobody can be asked about would end up in a document
    somebody signs.

    Figures are also absent, for the same reason they are absent from
    `ClientNarrative`: every number in the document is printed from the
    quotation it was built on.
    """

    title: str = Field(default="", description="The proposal's own title, e.g. 'Customer ID Portal'.")
    cover_letter: str = Field(
        default="",
        description=(
            "Three or four short paragraphs addressed to the client, in the second person. "
            "What they asked for, what is being proposed, what happens next."
        ),
    )
    executive_summary: str = Field(default="", description="One paragraph a decision-maker can read alone.")
    understanding: str = Field(default="", description="Their situation in their own vocabulary.")
    scope_overview: str = Field(
        default="",
        description=(
            "One paragraph introducing the scope table: what the engagement covers, in plain "
            "terms. It leads the table; it does not list what is in it."
        ),
    )
    approach: str = Field(default="", description="How the work will be run, in plain terms.")
    why_us: List[str] = Field(
        default_factory=list,
        description="3 to 5 specific reasons this studio, drawn from the scope. No boasting without a reason attached.",
    )
    deliverables: List[str] = Field(
        default_factory=list,
        description="What the client ends up holding, in their words rather than the engineering ones.",
    )
    risks_addressed: List[str] = Field(
        default_factory=list,
        description="The obvious worries, each answered in one line.",
    )
    next_steps: List[str] = Field(default_factory=list, description="What happens after they say yes.")


# --- API request/response envelopes -----------------------------------------


class ProposalRequest(BaseModel):
    """Mirrors the multipart form fields accepted by POST /api/proposals."""

    brief: str = ""
    currency: str = "PHP"
    client_name: str = ""
    project_name: str = ""
    market_region: str = "Philippines"
    budget_hint: str = Field(default="", description="Free text, e.g. 'under 500k' or 'no ceiling'.")
    timeline_hint: str = Field(default="", description="Free text, e.g. 'live before Q4'.")
    target_total: float = Field(
        default=0.0,
        description=(
            "Exact grand total to land on, in `currency`, tax included. 0 means none. "
            "Distinct from `budget_hint`: the hint is guidance the model reasons about, "
            "this is a figure the server solves for."
        ),
    )
    tax_mode: str = Field(
        default="exclusive",
        description=(
            "exclusive: tax added on top of the priced work. inclusive: the rates already "
            "contain it. none: no tax at all - no label, no percentage, no line. `none` is a "
            "studio decision the model does not get to overrule; a zero-rated or exempt "
            "engagement priced with 12% VAT on it is a wrong invoice."
        ),
    )
    tax_inclusive: bool = Field(
        default=False,
        description=(
            "Derived from `tax_mode` and kept because the estimate carries the same flag. "
            "True to quote rates that already contain the tax. See CostSummary.tax_inclusive."
        ),
    )

    @model_validator(mode="after")
    def _reconcile_tax(self) -> "ProposalRequest":
        """Keep `tax_mode` and `tax_inclusive` from disagreeing.

        Both are on the wire: the pad sends the mode, and anything older sends
        only the flag. The mode wins when it was actually supplied and is one of
        the three - otherwise the flag is the instruction. Letting the mode's
        own default win would silently turn `tax_inclusive=True` from an older
        caller into an exclusive quotation, which is the same figure meaning
        something different.
        """
        supplied = (self.tax_mode or "").strip().lower()
        stated = "tax_mode" in self.model_fields_set and supplied in {
            "exclusive",
            "inclusive",
            "none",
        }
        mode = supplied if stated else ("inclusive" if self.tax_inclusive else "exclusive")
        self.tax_mode = mode
        self.tax_inclusive = mode == "inclusive"
        return self

    @property
    def taxed(self) -> bool:
        """False when this quotation carries no tax line at all."""
        return self.tax_mode != "none"
    deposit_pct: float = Field(
        default=0.0,
        description="Percent payable up front. 0 leaves the model to propose a schedule.",
    )
    instalments: int = Field(default=3, description="Equal payments that follow the deposit.")
    payment_cadence: str = Field(default="monthly", description="monthly | phase | milestone.")
    deposit_trigger: str = Field(
        default="Signed statement of work",
        description="What makes the deposit payable.",
    )
    tier_ceiling: float = Field(
        default=0.0,
        description=(
            "A price no quotation may exceed, in `currency`, tax included. 0 means none. "
            "Unlike `target_total` this is a maximum rather than a figure to land on: a "
            "quotation already under it is left alone, and one above it is brought down."
        ),
    )
    pricing_basis: str = Field(
        default="rate_card",
        description=(
            "'rate_card' prices from the studio card when one is configured. 'requirements' "
            "ignores the card for this quotation and prices from the work at market rates."
        ),
    )
    tiers: str = Field(
        default="",
        description=(
            "Comma-separated tier names, e.g. 'Basic, Standard, Extended'. One quotation is "
            "prepared per tier from the same brief. Empty means a single quotation."
        ),
    )
    payment_schedule: str = Field(
        default="",
        description=(
            'A written schedule as JSON, e.g. [{"percent":40,"trigger":"Signing"}]. When '
            "present it wins over the deposit and instalment fields. Multipart has no nested "
            "types, so it arrives as a string and is parsed at the edge."
        ),
    )


class RevisionRequest(BaseModel):
    """Mirrors the form fields accepted by POST /api/proposals/{id}/revise.

    Currency is deliberately absent. A revision inherits the parent's currency;
    re-pricing the same work in another one would be an exchange rate by
    another name, and this system does not convert.
    """

    instruction: str = Field(
        default="",
        description="What to change, in the sender's words. e.g. 'drop the SMS work, add training'.",
    )
    target_total: float = Field(
        default=0.0,
        description="Exact grand total to land on, in the parent's currency. 0 means no target.",
    )


class GeneratedFile(BaseModel):
    kind: str = Field(description="'proposal' or 'requirements'.")
    filename: str
    markdown: str
    download_url: str
    print_url: str
    pdf_url: str = Field(default="", description="Downloadable PDF of the same document.")


class ScheduleRowRecord(BaseModel):
    """One payment of a written schedule, as recorded on a bundle."""

    percent: float = 0.0
    trigger: str = ""


class PaymentTermsRecord(BaseModel):
    """The payment terms a quotation was prepared under.

    A copy of `app.payments.PaymentTerms`, kept in the schema module because
    `ProposalBundle` lives here and importing the policy module would point the
    data contract at the logic that consumes it.
    """

    deposit_pct: float = 0.0
    instalments: int = 3
    cadence: str = "monthly"
    deposit_trigger: str = "Signed statement of work"
    schedule: List[ScheduleRowRecord] = Field(default_factory=list)


class TierSibling(BaseModel):
    """One of the other tiers quoted from the same brief."""

    id: str = ""
    tier_name: str = ""
    tier_index: int = 0
    total: float = 0.0
    currency: str = "PHP"


class ProposalDocument(BaseModel):
    """A proposal built from one quotation, exactly as that quotation stood.

    One bundle in, one document out. Tiers are not merged and revisions are not
    followed: the studio picks the quotation it is selling, and this carries
    that one. Rebuilding produces a new document with its own id rather than
    editing this one, for the same reason a revision does - a document that has
    been sent must not change afterwards.

    `policies`, `sections` and `design` are snapshots. A proposal sent in March
    says what it said in March and looks the way it looked in March, whatever
    Settings looks like in April.
    """

    id: str
    created_at: str
    quotation_id: str = Field(description="The bundle this was built from.")
    quotation_ref: str = Field(default="", description="That quotation's printed reference.")
    reference: str = Field(
        default="",
        description=(
            "This proposal's own number, from the studio's proposal series - P-0000041. Empty "
            "on a document built before proposals were numbered, which then prints the "
            "quotation's reference as it always did."
        ),
    )
    quotation_issued_at: str = Field(
        default="",
        description=(
            "When the quotation was issued. Validity is counted from here, not from the day the "
            "proposal was built - otherwise the two documents state different expiry dates for "
            "the same offer."
        ),
    )
    title: str = Field(default="")
    client_name: str = Field(default="")
    project_name: str = Field(default="")
    currency: str = Field(default="PHP")
    total: float = Field(default=0.0)
    studio_name: str = Field(default="")
    signatory: str = Field(default="", description="Who signs for the studio, from settings.")
    signatory_title: str = Field(default="", description="Their title, from settings.")
    narrative: ProposalNarrative = Field(default_factory=ProposalNarrative)
    policies: List["PolicyRecord"] = Field(default_factory=list)
    sections: List["SectionRecord"] = Field(
        default_factory=list,
        description=(
            "The template this was built with, snapshotted. Empty means the shipped order - a "
            "document keeps the shape it had when it was sent, whatever Settings looks like "
            "afterwards."
        ),
    )
    design: ProposalDesign = Field(
        default_factory=ProposalDesign,
        description=(
            "The look this was built with, snapshotted. A document written before the design "
            "existed reads back as the shipped look, which is what it was built with. A studio "
            "that rebrands does not restyle the proposals it has already sent."
        ),
    )
    files: List[GeneratedFile] = Field(default_factory=list)


class SectionRecord(BaseModel):
    """One section of the proposal as the template had it when it was built."""

    id: str = ""
    heading: str = ""


class PolicyRecord(BaseModel):
    """One clause as it stood when the proposal was built."""

    id: str = ""
    title: str = ""
    body: str = ""


class ProposalDocumentSummary(BaseModel):
    """A row in the list of proposals already built."""

    id: str
    created_at: str
    quotation_id: str = ""
    quotation_ref: str = ""
    reference: str = Field(default="", description="The proposal's own number.")
    title: str = ""
    client_name: str = ""
    project_name: str = ""
    currency: str = "PHP"
    total: float = 0.0
    policy_count: int = 0


class ProposalSummary(BaseModel):
    """One row in the admin list.

    Deliberately not a `ProposalBundle`: a bundle carries both rendered
    documents and runs to tens of kilobytes, so a hundred of them would be a
    multi-megabyte response for a screen that only shows a total and a date.
    """

    id: str
    created_at: str
    quotation_ref: str = Field(
        default="",
        description=(
            "The reference this quotation prints - the studio's prefix and number, e.g. "
            "ABC-0002187. This is what a client quotes back at you; the id above is storage."
        ),
    )
    project_name: str = ""
    client_name: str = ""
    currency: str = "PHP"
    total: float = 0.0
    line_items: int = 0
    revision: int = 1
    parent_id: str = ""
    root_id: str = ""
    target_total: float = 0.0
    hit_target: bool = True
    tax_label: str = ""
    tax_pct: float = 0.0
    tax_inclusive: bool = False
    tier_group_id: str = ""
    tier_name: str = ""
    proposal_url: str = ""
    requirements_url: str = ""


class ProposalBundle(BaseModel):
    """What POST /api/proposals returns.

    Revision metadata lives here rather than on `Estimate` on purpose: `Estimate`
    is the Gemini response schema, and every field added to it is a field the
    model has to fill. Provenance is the server's business, not the model's.
    """

    id: str
    created_at: str = Field(description="ISO-8601 UTC timestamp.")
    estimate: Estimate
    files: List[GeneratedFile]

    revision: int = Field(default=1, description="1 for an original, 2 for its first revision.")
    parent_id: str = Field(default="", description="Bundle this was revised from. Empty for an original.")
    parent_ref: str = Field(
        default="",
        description=(
            "The printed reference of the bundle this was revised from, e.g. ABC-0002187. "
            "Resolved on read rather than stored - a copy written at revision time would be "
            "one more thing to migrate, and every bundle prepared before this field existed "
            "would have it empty. Empty when the parent has since been deleted."
        ),
    )
    root_id: str = Field(default="", description="The original bundle at the head of this chain.")
    revision_instruction: str = Field(default="", description="What was asked for. Empty for an original.")

    # A target total can be set on a first quotation as well as on a revision,
    # so these describe the bundle rather than the revision that produced it.
    target_total: float = Field(default=0.0, description="Requested total. 0 when none was set.")
    hit_target: bool = Field(
        default=True,
        description="False when the requested total is unreachable and the nearest was used instead.",
    )
    target_note: str = Field(
        default="",
        description="Why the requested total was not hit exactly. Empty when it was.",
    )

    tier_group_id: str = Field(
        default="",
        description="Shared by every tier prepared from one brief. Empty for a single quotation.",
    )
    tier_name: str = Field(default="", description="Which tier this quotation prices.")
    tier_index: int = Field(default=0, description="Position in the tier order, from 0.")
    tier_siblings: List[TierSibling] = Field(
        default_factory=list,
        description="The other tiers in this group, filled in when a quotation is read.",
    )

    rate_card_bound: int = Field(
        default=0,
        description="Line items priced from the studio rate card. 0 when no card is configured.",
    )
    rate_card_removed: List[str] = Field(
        default_factory=list,
        description=(
            "Line items deleted because the configured rate card does not cover them. A card "
            "is a closed list: work priced against a role nobody agreed a rate for never "
            "reaches the client. Reported rather than silent, because vanishing scope is the "
            "worse failure."
        ),
    )
    payment_terms: "PaymentTermsRecord" = Field(
        default_factory=lambda: PaymentTermsRecord(),
        description=(
            "The terms this quotation was prepared under. Recorded so a revision inherits the "
            "schedule the client already saw rather than reverting to whatever the model likes."
        ),
    )
    tier_ceiling: float = Field(default=0.0, description="The ceiling this quotation was held to. 0 when none.")
    ceiling_applied: bool = Field(
        default=False,
        description="True when the quotation came in over the ceiling and was brought down to it.",
    )
    tier_cap: float = Field(
        default=0.0,
        description=(
            "The maximum this tier was actually held to. For the top tier that is the studio "
            "ceiling; for every tier below it, the total the tier above came back with. 0 when "
            "nothing capped it."
        ),
    )
    tier_order_enforced: bool = Field(
        default=False,
        description=(
            "True when this tier came in at or above the tier above and was brought down to sit "
            "below it. The scope moves, never the rates - a tier that contains less work than "
            "the one above it cannot cost more, and a ladder a client cannot read in order is "
            "not a ladder."
        ),
    )
    tier_cap_note: str = Field(
        default="",
        description="Why a cap could not be applied exactly. Empty when it was, or when none applied.",
    )
    priced_from_rate_card: bool = Field(
        default=False,
        description="True when the studio rate card bound this quotation's rates.",
    )

    payment_terms_applied: bool = Field(
        default=False,
        description="True when the studio standard payment schedule replaced the one the model proposed.",
    )
    rate_card_removed_value: float = Field(
        default=0.0,
        description="Money those removals took off the quotation, before contingency and tax.",
    )


class BriefCheck(BaseModel):
    """Is this text a description of work somebody wants done?

    Its own model rather than a field on `Estimate`, and the difference is what
    it costs to be wrong. A field on `Estimate` can only be filled by the
    generation itself - so the model has already priced the thing before it can
    say the thing was not priceable, and every tier has been paid for. This is
    asked first, on its own, of a few hundred characters.

    BOTH DEFAULTS ARE THE ACCEPTING ANSWER, and that is deliberate. This is a
    quality gate, not a security boundary: a false refusal stops a studio
    quoting at all, while a false accept is one quotation somebody deletes. A
    partial or malformed response therefore has to mean "carry on", and putting
    that in the type is stronger than remembering it at every call site.
    """

    is_brief: bool = Field(
        default=True,
        description=(
            "True if this text describes work to be done - even roughly, even in "
            "one line, even in a language other than English. False ONLY for text "
            "that describes no work at all: keyboard mashing, random letters, "
            "lorem ipsum, a single unrelated word, or test input."
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "When is_brief is false, one short sentence for the person who typed "
            "it, saying what is missing rather than what rule was broken. Empty "
            "when is_brief is true."
        ),
    )
