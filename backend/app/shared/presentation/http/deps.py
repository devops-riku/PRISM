"""What more than one router needs.

These are the fourteen names that `main.py` grew as private helpers and that
two or more bounded contexts turned out to reach. They live here because the
alternative - each router keeping its own copy - is how `_slug` in one file
and `_slug` in another quietly stop agreeing about what a filename may
contain.

The rule for adding to this file is narrow: a helper belongs here only when a
SECOND router needs it. One router's helper stays in that router, however
general it looks. A shared module that collects everything general becomes the
new `main.py`, which is the thing this refactor was for.

They lost their leading underscores on the way in. A private name is a promise
that only this module calls it, and the moment `app/presentation/api/client.py` imports
`_slug` that promise is already broken; the underscore then only tells the next
reader something untrue.
"""

from __future__ import annotations

import logging
import re
from typing import List

from fastapi import HTTPException, Request
from pydantic import BaseModel

from app.features.quotations.domain import kinds
from app.features.quotations.domain.models import Estimate, ProposalBundle
from app.features.quotations.infrastructure import repository as storage
from app.features.team.infrastructure import auth, members
from app.shared.infrastructure import config

if not logging.getLogger().handlers:  # uvicorn does not configure the root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    )

#: Every router logs under this one name, so a studio reading the console sees
#: one stream rather than six that have to be correlated by timestamp.
logger = logging.getLogger("prism.api")

KINDS = ("proposal", "requirements")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
SLUG_STRIP = re.compile(r"[^A-Za-z0-9]+")


class CurrencyOption(BaseModel):
    code: str
    name: str
    symbol: str


#: Curated, not exhaustive. PHP first because that is the home market; the rest
#: cover the currencies a small consultancy is realistically asked to quote in.
#:
#: Here rather than beside the route that serves it, because two routers need
#: it and for opposite reasons: `platform.py` publishes the list so the form has
#: something to offer, and `quotations.py` derives the set of acceptable codes
#: from it to refuse everything else. Those two must not be able to disagree -
#: a currency offered in a dropdown and then rejected on submit is a dead end
#: with no message that helps.
CURRENCIES: List[CurrencyOption] = [
    CurrencyOption(code="PHP", name="Philippine Peso", symbol="₱"),
    CurrencyOption(code="USD", name="US Dollar", symbol="$"),
    CurrencyOption(code="EUR", name="Euro", symbol="€"),
    CurrencyOption(code="GBP", name="Pound Sterling", symbol="£"),
    CurrencyOption(code="JPY", name="Japanese Yen", symbol="¥"),
    CurrencyOption(code="AUD", name="Australian Dollar", symbol="A$"),
    CurrencyOption(code="CAD", name="Canadian Dollar", symbol="C$"),
    CurrencyOption(code="SGD", name="Singapore Dollar", symbol="S$"),
    CurrencyOption(code="HKD", name="Hong Kong Dollar", symbol="HK$"),
    CurrencyOption(code="AED", name="UAE Dirham", symbol="د.إ"),
    CurrencyOption(code="INR", name="Indian Rupee", symbol="₹"),
    CurrencyOption(code="IDR", name="Indonesian Rupiah", symbol="Rp"),
    CurrencyOption(code="MYR", name="Malaysian Ringgit", symbol="RM"),
    CurrencyOption(code="THB", name="Thai Baht", symbol="฿"),
    CurrencyOption(code="VND", name="Vietnamese Dong", symbol="₫"),
    CurrencyOption(code="KRW", name="South Korean Won", symbol="₩"),
    CurrencyOption(code="CNY", name="Chinese Yuan", symbol="¥"),
    CurrencyOption(code="NZD", name="New Zealand Dollar", symbol="NZ$"),
    CurrencyOption(code="CHF", name="Swiss Franc", symbol="CHF"),
    CurrencyOption(code="BRL", name="Brazilian Real", symbol="R$"),
]

#: Strong references to in-flight background work. asyncio only keeps a weak
#: reference to a task, so a job with nothing holding it can be collected
#: half-finished. Each task removes itself when it is done.
BACKGROUND: set = set()


def slugify(value: str, fallback: str) -> str:
    """ASCII-safe filename stem. Content-Disposition is not a place for surprises."""
    cleaned = SLUG_STRIP.sub("-", (value or "")).strip("-").lower()
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned[:48].strip("-") or fallback


def download_filename(estimate: Estimate, kind: str, revision: int = 1) -> str:
    stem = slugify(estimate.project_name or estimate.client_name, "prism-quotation")
    # The client document is a quotation. `kind` stays "proposal" because it
    # is a URL segment other people have bookmarked; what lands in a downloads
    # folder is what the reader calls it.
    suffix = "quotation" if kind == "proposal" else "requirements"
    # A revision carries its number in the filename so two versions of the same
    # quotation do not land in a downloads folder under one name.
    version = f"-r{revision}" if revision > 1 else ""
    return f"{stem}-{suffix}{version}.md"


def document_title(estimate: Estimate, kind: str) -> str:
    subject = (estimate.client.title or estimate.project_name or "Quotation").strip()
    if kind == "proposal":
        return f"{subject} · Quotation"
    # `subject` already names the project, so this is the short form of the
    # title; `kinds.title_for` would print the project a second time. A
    # quotation with no kind is software, and says what it has always said.
    if kinds.is_software(estimate):
        return f"{subject} · Developer requirements"
    return f"{subject} · {kinds.noun_for(estimate)} Requirements"


def require_bundle(proposal_id: str) -> ProposalBundle:
    bundle = storage.get(proposal_id)
    if bundle is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No quotation with that reference. It may have been generated by an "
                "earlier run of the API - prepare a new one."
            ),
        )
    return bundle


def normalise_currency(raw: str) -> str:
    code = (raw or "").strip().upper() or "PHP"
    if not CURRENCY_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail=f"'{raw}' is not a currency code. Use a three-letter ISO 4217 code such as PHP or USD.",
        )
    return code


def normalise_instruction(raw: str) -> str:
    instruction = (raw or "").strip()
    if len(instruction) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The revision instruction is {len(instruction):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - say what to change, not the whole brief again."
            ),
        )
    return instruction


def scope_shortfall(scope: str) -> str:
    """Which floor a client's scope failed, in words a stranger can act on, or
    `""` if it passed.

    Three structural facts about the string, checked in the order a person
    would notice them, and NOT a gibberish detector - see the constants'
    own note in `config.py` for why that distinction is deliberate. Each
    message names the remedy rather than the rule: nobody outside this file
    knows what "distinct characters" means, and a refusal a client cannot act
    on is a dead end on a door that only opens once.
    """
    if len(scope) < config.MIN_CLIENT_SCOPE_CHARS:
        return "A bit more detail, please - a sentence or two about the work."
    body = "".join(scope.split())
    if len(set(body)) < config.MIN_CLIENT_SCOPE_DISTINCT:
        return "That reads as placeholder text. Say what you need and who it is for."
    if config.MIN_CLIENT_SCOPE_LETTERS and (
        len({character for character in body if character.isalpha()})
        < config.MIN_CLIENT_SCOPE_LETTERS
    ):
        return "Describe it in words, not only numbers."
    return ""


def current_user(request: Request) -> "auth.User | None":
    return getattr(request.state, "user", None)


def current_email(request: Request) -> str:
    """The signed-in email, or '' on an install with no accounts."""
    user = current_user(request)
    return user.email if user else ""


def require_admin(
    request: Request, detail: str = "Only an admin of this workspace can change its team."
) -> None:
    """Refuse anyone but an admin of the workspace that is currently open, and
    say what the difference is. Predates the intake routes - originally
    written for team management below - and is reused rather than
    re-defined for them; `detail` lets each call site's 403 name the thing it
    is actually refusing instead of always talking about the team."""
    if not auth.required():
        return
    if getattr(request.state, "role", "") != members.ADMIN:
        raise HTTPException(status_code=403, detail=detail)
