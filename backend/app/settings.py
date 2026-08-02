"""Studio defaults: what the brief form starts with.

Deliberately narrow. These values prefill the form and nothing else - they are
not injected into the prompt and they do not override anything the model
decides. A shop that quotes in PHP for the Philippine market on a VAT-inclusive
basis should not have to set those three controls every time, and that is the
whole of the problem this solves.

What is NOT here, on purpose:

  * A contingency percentage. `SYSTEM_INSTRUCTION` asks the model to pick 8-15
    from the actual risk in the brief - thin requirements and unproven
    integrations earn a higher number. Pinning it to one value would replace a
    judgement with a constant and the documents would stop reflecting risk.
  * A tax rate. The rate follows from the market, and the model already carries
    a table of them. A studio-wide override would be wrong the first time
    someone quotes across a border.
  * A tax rate override. See above.

What IS here beyond prefills: the rate card. It is the one setting that is not
merely a prefill - when it has entries they are injected into the prompt and
then enforced by `app.ratecard`, so the studio's own rates bind rather than the
model's guess at the market. An empty card changes nothing at all, and the model
prices from the requirements exactly as it did before.

Stored as one small JSON file beside the generated quotations. No database, in
keeping with the rest of the system.
"""

from __future__ import annotations

import json
import logging
import threading

from typing import List

from pydantic import BaseModel, Field

from app import design as design_module
from app import policies as policy_module
from app import template as template_module
from app import reference
from app.design import ProposalDesign
from app.policies import PolicyClause
from app.template import SectionSpec
from app.ratecard import RoleRate, UnitBasis, normalise_role

from app import workspaces

logger = logging.getLogger("prism.settings")

SETTINGS_FILENAME = "settings.json"

_lock = threading.RLock()
#: One set of defaults per workspace, cached. Each workspace is its own studio -
#: its own rates, terms, numbering and look - so a single cache would hand one
#: studio's rate card to another's quotation.
_cached: dict[str, "StudioDefaults"] = {}


class StudioDefaults(BaseModel):
    """The values a new brief form opens with."""

    studio_name: str = Field(
        default="",
        description=(
            "Who is quoting. Shown in the app header and used for its initials. Empty falls "
            "back to PRISM."
        ),
    )
    currency: str = Field(default="PHP", description="ISO 4217 code.")
    market_region: str = Field(default="Philippines", description="Market whose rates are used.")
    tax_mode: str = Field(
        default="exclusive",
        description="What the form opens on: exclusive | inclusive | none.",
    )
    tax_inclusive: bool = Field(
        default=False,
        description="Derived from `tax_mode`. Kept so an older client still reads the default.",
    )
    unit_basis: UnitBasis | None = Field(
        default=None,
        description=(
            "Deprecated: one working day for the whole studio. Each role on the card now "
            "carries its own, because a monthly retainer and a day rate do not share a "
            "working month. Read only to migrate a settings file written before the move, "
            "and dropped once it has been."
        ),
    )
    reference_prefix: str = Field(
        default="",
        description=(
            "Up to four letters in front of every quotation number, e.g. ABC-0002001. "
            "Empty falls back to Q."
        ),
    )
    reference_mode: str = Field(
        default="random",
        description="incremental: 0000001, 0000002, ... | random: a short base-36 draw.",
    )
    reference_preview: str = Field(
        default="",
        description="Read-only. What the next reference will look like; ignored on write.",
    )
    proposal_prefix: str = Field(
        default="",
        description=(
            "Up to four letters in front of every proposal number, e.g. ABC-0000041. Empty "
            "falls back to P. A separate series from the quotations - a proposal is a "
            "different document, and sharing a counter would make proposal 41 the "
            "forty-first quotation."
        ),
    )
    proposal_reference_mode: str = Field(
        default="incremental",
        description="incremental: 0000001, 0000002, ... | random: a short base-36 draw.",
    )
    proposal_reference_preview: str = Field(
        default="",
        description="Read-only. What the next proposal number will look like; ignored on write.",
    )
    show_contingency_to_client: bool = Field(
        default=False,
        description=(
            "False - the default - folds the contingency into the priced work so the client "
            "sees no line for it. The buffer is still there and the total is unchanged; it is "
            "carried in the quantities rather than itemised."
        ),
    )
    proposal_signatory: str = Field(
        default="",
        description="Who signs the proposal, e.g. 'Maria Santos'. Empty leaves the line blank.",
    )
    proposal_signatory_title: str = Field(
        default="",
        description="Their title, e.g. 'Managing Director'.",
    )
    proposal_sections: List[SectionSpec] = Field(
        default_factory=list,
        description=(
            "The proposal's sections, in order, with the headings this studio uses. Empty means "
            "the shipped document. Every id must be one the renderer knows - see app/template.py."
        ),
    )
    proposal_design: ProposalDesign = Field(
        default_factory=ProposalDesign,
        description=(
            "How the proposal looks: logo, colour, type, margins, table ruling, footer. "
            "Nothing here can reach the content, which is why a studio may edit it freely."
        ),
    )
    policies: List[PolicyClause] = Field(
        default_factory=list,
        description=(
            "The terms every proposal carries, printed verbatim. Empty means the recommended "
            "set in app/policies.py is used - a proposal with no terms is not a document worth "
            "sending. The model never writes these and has no field to put one in."
        ),
    )
    rate_card: List[RoleRate] = Field(
        default_factory=list,
        description=(
            "The studio's charge-out rates, in `currency`. Empty means the model prices from "
            "the requirements at market rates, which is the default behaviour."
        ),
    )

    def normalised(self) -> "StudioDefaults":
        card: List[RoleRate] = []
        seen: set[str] = set()
        # A settings file from before the basis moved onto each role still says
        # what the studio meant, so its one working day is copied onto every
        # role that has not been given its own rather than silently replaced by
        # the 8/5 default.
        legacy = self.unit_basis
        for entry in self.rate_card or []:
            if legacy is not None and "hours_per_day" not in entry.model_fields_set:
                entry = entry.model_copy(
                    update={
                        "hours_per_day": legacy.hours_per_day,
                        "days_per_week": legacy.days_per_week,
                    }
                )
            cleaned = entry.normalised()
            if not cleaned.usable:
                continue
            # One rate per role. A duplicate would make enforcement depend on
            # list order, which is not something an admin can see.
            key = normalise_role(cleaned.role)
            if key in seen:
                continue
            seen.add(key)
            card.append(cleaned)

        # The mode is authoritative; the boolean follows it. A settings file
        # written before the mode existed still says what it meant, so the
        # boolean is what the mode falls back to rather than the other way round.
        mode = (self.tax_mode or "").strip().lower()
        if mode not in {"exclusive", "inclusive", "none"}:
            mode = "inclusive" if self.tax_inclusive else "exclusive"

        return StudioDefaults(
            studio_name=(self.studio_name or "").strip()[:60],
            currency=(self.currency or "PHP").strip().upper()[:3] or "PHP",
            market_region=(self.market_region or "").strip() or "Philippines",
            tax_mode=mode,
            tax_inclusive=mode == "inclusive",
            # Not carried forward: the migration above has already put it where
            # it belongs, and writing it back would migrate it again next time.
            unit_basis=None,
            reference_prefix=reference.normalise_prefix(self.reference_prefix),
            reference_mode=(
                "incremental"
                if (self.reference_mode or "").strip().lower() == "incremental"
                else "random"
            ),
            proposal_prefix=reference.normalise_prefix(self.proposal_prefix),
            proposal_reference_mode=(
                "random"
                if (self.proposal_reference_mode or "").strip().lower() == "random"
                else "incremental"
            ),
            show_contingency_to_client=bool(self.show_contingency_to_client),
            proposal_signatory=(self.proposal_signatory or "").strip()[:80],
            proposal_signatory_title=(self.proposal_signatory_title or "").strip()[:80],
            proposal_sections=template_module.normalise(self.proposal_sections),
            proposal_design=design_module.resolve(self.proposal_design),
            policies=policy_module.normalise(self.policies),
            rate_card=card[:60],
        )


def _path():
    return workspaces.root() / SETTINGS_FILENAME


def load() -> StudioDefaults:
    """Current defaults. A missing or unreadable file is not an error."""
    key = workspaces.current()
    with _lock:
        found = _cached.get(key)
        if found is not None:
            return found

        path = _path()
        defaults = StudioDefaults()
        if path.is_file():
            try:
                defaults = StudioDefaults.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Ignoring unreadable %s (%s: %s) - falling back to built-in defaults",
                    path,
                    exc.__class__.__name__,
                    exc,
                )
        _cached[key] = defaults.normalised()
        return _cached[key]


def save(defaults: StudioDefaults) -> StudioDefaults:
    """Persist and return the normalised defaults."""
    cleaned = defaults.normalised()
    key = workspaces.current()

    with _lock:
        _cached[key] = cleaned
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cleaned.model_dump_json(indent=2), encoding="utf-8")

    logger.info(
        "Studio defaults saved: %s / %s / tax %s",
        cleaned.currency,
        cleaned.market_region,
        cleaned.tax_mode,
    )
    return cleaned
