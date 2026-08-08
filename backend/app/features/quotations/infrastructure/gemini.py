"""The single Gemini call.

Package is `google-genai`. There is no `genai.configure()` and no
`GenerativeModel`; the call shape below is the verified one from
docs/CONTRACT.md section 3 - `client.models.generate_content(...)` with the
schema in `config` and the images in `contents`.

One deliberate departure from the snippet in the contract: the client is built
lazily inside this module rather than at import time. The snippet's
`genai.Client(api_key=os.environ["GEMINI_API_KEY"])` at module scope would raise
`KeyError` on import when no key is configured, and the contract also requires
that a missing key surface as a clean 503. The API *shape* is unchanged; only
the moment of construction moved.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import threading
from typing import Any, Sequence

import anyio
import anyio.to_thread  # explicit: `anyio.to_thread` is a submodule, not re-exported

from google import genai
from google.genai import types

from app.features.quotations.application.prompts import (
    PROPOSAL_SYSTEM_INSTRUCTION,
    REVISION_SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    build_brief,
    build_proposal_brief,
    build_revision,
    strip_sentinels,
)
from app.features.quotations.domain.costing import gross_for_target
from app.features.quotations.domain.models import BriefCheck, Estimate, ProposalNarrative, ProposalRequest
from app.shared.infrastructure import config

logger = logging.getLogger("prism.gemini")

_SNIPPET_LIMIT = 600
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_AUTH_STATUS = {401, 403}


class GeminiError(RuntimeError):
    """Base class for every failure of the generation step."""


class GeminiConfigError(GeminiError):
    """The key is missing or rejected. Surfaces as 503 - the operator must act."""


class GeminiResponseError(GeminiError):
    """Gemini answered, but not with a usable Estimate. Surfaces as 502."""

    def __init__(self, message: str, snippet: str = "") -> None:
        super().__init__(message)
        self.snippet = snippet


# --- Client ------------------------------------------------------------------

_client: genai.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> genai.Client:
    """Build the client once, on first use, never at import time."""
    global _client
    api_key = config.GEMINI_API_KEY.strip()
    if not api_key:
        raise GeminiConfigError(config.MISSING_KEY_MESSAGE)

    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    _client = genai.Client(api_key=api_key)
                except Exception as exc:  # malformed key, bad transport options
                    raise GeminiConfigError(
                        "Could not initialise the Gemini client "
                        f"({exc.__class__.__name__}). Check GEMINI_API_KEY in backend/.env "
                        "and restart the API."
                    ) from exc
    return _client


# --- Error classification ----------------------------------------------------


def _status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status for an SDK exception, without betting on a class name."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    return None


def _describe(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}".lower()


def _is_auth_failure(exc: BaseException) -> bool:
    code = _status_code(exc)
    if code in _AUTH_STATUS:
        return True
    text = _describe(exc)
    return any(
        marker in text
        for marker in (
            "api key not valid",
            "api_key_invalid",
            "invalid api key",
            "unauthenticated",
            "permission_denied",
            "permission denied",
            "missing credentials",
        )
    )


def _is_transient(exc: BaseException) -> bool:
    """429 / 5xx / timeouts / dropped connections. A 4xx that is not 429 is not transient."""
    if _is_auth_failure(exc):
        return False
    code = _status_code(exc)
    if code is not None:
        return code in _TRANSIENT_STATUS
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    text = _describe(exc)
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "deadline",
            "temporarily",
            "unavailable",
            "overloaded",
            "resource_exhausted",
            "rate limit",
            "too many requests",
            "connection reset",
            "connection aborted",
            "server error",
        )
    )


def _snippet(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + " ... [truncated]"


def _blocked_reason(response: Any) -> str:
    """Extract a human reason when the model returned no text (safety block, token cap)."""
    reasons: list[str] = []
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        reasons.append(f"prompt blocked ({getattr(block_reason, 'name', block_reason)})")
    for candidate in getattr(response, "candidates", None) or []:
        finish = getattr(candidate, "finish_reason", None)
        if finish and str(getattr(finish, "name", finish)).upper() not in {"STOP", "FINISH_REASON_STOP"}:
            reasons.append(f"generation stopped early ({getattr(finish, 'name', finish)})")
    return "; ".join(dict.fromkeys(reasons))


# --- Generation --------------------------------------------------------------


def _build_contents(
    req: ProposalRequest,
    images: Sequence[tuple[bytes, str]],
    rate_card_text: str = "",
    unit_basis_text: str = "",
    payment_terms_text: str = "",
    contingency_hidden: bool = False,
    tier=None,
    ceiling: float = 0.0,
    kind: str = "software",
    kind_label: str = "",
    documents_text: str = "",
) -> list[Any]:
    """A flat list: the brief text first, then one image part per attachment."""
    contents: list[Any] = [build_brief(
        req,
        len(images),
        rate_card_text,
        unit_basis_text,
        payment_terms_text,
        contingency_hidden,
        tier,
        ceiling,
        kind=kind,
        kind_label=kind_label,
        documents_text=documents_text,
    )]
    for image_bytes, mime_type in images:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    return contents


async def _generate_once(contents: list[Any], system_instruction: str = "") -> Any:
    client = _get_client()
    call = functools.partial(
        client.models.generate_content,
        model=config.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Estimate,
            temperature=config.GEMINI_TEMPERATURE,
            system_instruction=system_instruction or SYSTEM_INSTRUCTION,
        ),
    )
    # `anyio.to_thread.run_sync` forwards positional arguments only, hence the partial.
    return await anyio.to_thread.run_sync(call)


async def _generate_with_retry(contents: list[Any], system_instruction: str = "") -> Any:
    """One call, plus a single retry when - and only when - the failure is transient."""
    for attempt in (1, 2):
        try:
            return await _generate_once(contents, system_instruction)
        except GeminiError:
            raise
        except Exception as exc:
            if _is_auth_failure(exc):
                raise GeminiConfigError(
                    "Gemini rejected the configured API key. Check GEMINI_API_KEY in "
                    "backend/.env, confirm the key has access to "
                    f"{config.GEMINI_MODEL}, and restart the API."
                ) from exc
            if attempt == 2 or not _is_transient(exc):
                status = _status_code(exc)
                detail = f" (status {status})" if status is not None else ""
                raise GeminiResponseError(
                    f"The estimate could not be generated{detail}: "
                    f"{exc.__class__.__name__}. Try again in a moment; if it keeps "
                    "happening, check the model id in backend/.env.",
                    snippet=_snippet(str(exc)),
                ) from exc
            logger.warning(
                "Transient Gemini failure (%s), retrying once in %.1fs",
                exc.__class__.__name__,
                config.GEMINI_RETRY_DELAY_SECONDS,
            )
            await anyio.sleep(config.GEMINI_RETRY_DELAY_SECONDS)

    # Unreachable: the loop either returns or raises.
    raise GeminiResponseError("The estimate could not be generated.")


def _coerce_estimate(response: Any) -> Estimate:
    """`response.parsed` first, then the raw JSON text, then a clear error."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, Estimate):
        return parsed
    if isinstance(parsed, dict):
        try:
            return Estimate.model_validate(parsed)
        except Exception:  # fall through to the text path
            logger.warning("response.parsed was a dict that failed validation; trying response.text")

    raw = getattr(response, "text", None) or ""
    if raw.strip():
        try:
            return Estimate.model_validate_json(raw)
        except Exception as exc:
            raise GeminiResponseError(
                "Gemini returned a response that is not a valid estimate. Try again; "
                "if it repeats, the configured model may not support structured output.",
                snippet=_snippet(raw),
            ) from exc

    reason = _blocked_reason(response)
    raise GeminiResponseError(
        "Gemini returned an empty response"
        + (f" - {reason}. " if reason else ". ")
        + "Rephrase the brief or remove the attached images and try again.",
        snippet=_snippet(str(getattr(response, "candidates", "") or "")),
    )


#: "Proposal for the Acme Portal", "Proposal: Acme Portal", "Project Proposal".
#: Only at the ends of the title - see `_as_quotation` for why.
_SEPARATORS = " -–—:·|,"
_PROPOSAL_PREFIX = re.compile(
    r"^\s*(?:draft\s+)?proposals?\b"
    # Punctuation or a preposition has to follow, so "Proposal: Acme" and
    # "Proposal for Acme" are caught while "Proposal management system" - a
    # product that manages proposals - is left as the client named it.
    r"(?:\s*[:,\-–—·|]+\s*|\s+(?:for|of|to)\b[\s:,\-–—·|]*)"
    r"(?:(?:the|a|an)\s+)?",
    re.IGNORECASE,
)
_PROPOSAL_SUFFIX = re.compile(r"[\s:,\-–—·|]*\b(?:draft\s+)?proposals?\s*$", re.IGNORECASE)


def _as_quotation(title: str) -> str:
    """Take the word "proposal" off the document's own title.

    The prompt asks for it and the model usually complies, but a title is the
    largest text on the page and the first line a client reads - so it is
    checked rather than trusted, the same way the tax basis is.

    Two deliberate limits. Only the title: the prose underneath is left alone,
    because a client whose own product manages proposals would find their
    vocabulary quietly rewritten, and corrupting the brief is worse than an
    unwanted word. And only at the ends of it: "Proposal for the Acme Portal"
    and "Acme Portal Proposal" are this document calling itself the wrong thing,
    while "Proposal management system" is what the client sells.
    """
    cleaned = (title or "").strip()
    if not cleaned:
        return cleaned

    stripped = _PROPOSAL_PREFIX.sub("", cleaned)
    stripped = _PROPOSAL_SUFFIX.sub("", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(_SEPARATORS)

    if stripped == cleaned:
        return cleaned
    if not stripped:
        return "Quotation"

    logger.info("Retitled %r as %r: the client document is a quotation", cleaned, stripped)
    return stripped


def _apply_request_identity(estimate: Estimate, req: ProposalRequest) -> Estimate:
    """The request, not the model, is authoritative about currency, market and names.

    The renderers format money with `estimate.currency`; if the model echoed a
    different code the documents would be labelled wrong. Same for the region.
    Names supplied by the user always win over anything the model invented.
    """
    estimate.currency = (req.currency or "").strip().upper() or estimate.currency or "PHP"
    estimate.market_region = (req.market_region or "").strip() or estimate.market_region or "Philippines"

    # The tax basis decides what every figure in the document means, so it is the
    # caller's to set, not the model's. The prompt tells the model which basis to
    # price on; this makes sure the costing agrees whatever came back.
    estimate.cost.tax_inclusive = bool(req.tax_inclusive)

    # "No tax" is enforced here rather than trusted to the prompt. A model that
    # has read a thousand Philippine quotations will add 12% VAT out of habit,
    # and a zero-rated or exempt engagement that ships with VAT on it is a wrong
    # invoice, not a wrong sentence. `recompute` reads these two fields and
    # nothing else, so clearing them is the whole enforcement.
    if not req.taxed:
        estimate.cost.tax_label = ""
        estimate.cost.tax_pct = 0.0
        estimate.cost.tax_inclusive = False

    # `strip_sentinels`, not a bare `.strip()`: `estimate.client_name` is
    # serialised whole into `build_revision`'s `prior_json` (`prompts.py`),
    # and `req.client_name` can carry the same anonymously-submitted text
    # `build_brief` already has to defend against - see `strip_sentinels`'s
    # own docstring for why this call exists in this file at all.
    client_name = strip_sentinels(req.client_name)
    if client_name:
        estimate.client_name = client_name

    project_name = (req.project_name or "").strip()
    if project_name:
        estimate.project_name = project_name
    elif not estimate.project_name.strip():
        estimate.project_name = (estimate.client.title or "Untitled project").strip()

    if not estimate.client.title.strip():
        estimate.client.title = estimate.project_name

    estimate.client.title = _as_quotation(estimate.client.title)

    return estimate


async def generate_estimate(
    req: ProposalRequest,
    images: list[tuple[bytes, str]],
    rate_card_text: str = "",
    unit_basis_text: str = "",
    payment_terms_text: str = "",
    contingency_hidden: bool = False,
    tier=None,
    ceiling: float = 0.0,
    kind: str = "software",
    kind_label: str = "",
    documents_text: str = "",
) -> Estimate:
    """Run the one generation call and return a validated `Estimate`.

    `images` is a list of `(bytes, mime_type)` pairs, already validated by the
    caller. Arithmetic is *not* corrected here - `app.domain.costing.recompute` owns it.

    `kind` is a discipline id from the quotation domain's `kinds` module and
    decides the shape of the
    second document only - the quotation is the same whatever the work is. It
    sits last because every existing caller passes the arguments before it
    positionally, and it defaults to software because everything quoted before
    disciplines existed was.

    Raises:
        GeminiConfigError: no key, or the key was rejected.
        GeminiResponseError: the model answered with something unusable.
    """
    images = list(images or [])
    contents = _build_contents(
        req,
        images,
        rate_card_text,
        unit_basis_text,
        payment_terms_text,
        contingency_hidden,
        tier,
        ceiling,
        kind=kind,
        kind_label=kind_label,
        documents_text=documents_text,
    )

    logger.info(
        "Generating estimate: model=%s kind=%s currency=%s region=%s images=%d brief_chars=%d",
        config.GEMINI_MODEL,
        kind or "software",
        req.currency,
        req.market_region,
        len(images),
        len(req.brief or ""),
    )

    response = await _generate_with_retry(contents)
    estimate = _coerce_estimate(response)
    estimate = _apply_request_identity(estimate, req)

    logger.info(
        "Estimate received: %d requirements, %d line items, %d phases, confidence=%s",
        len(estimate.requirements),
        len(estimate.line_items),
        len(estimate.phases),
        estimate.confidence.value,
    )
    return estimate


def _strip_for_prompt(estimate: Estimate) -> str:
    """Serialise the prior estimate for the revision prompt.

    Derived money is dropped: subtotals, the contingency and tax amounts, the
    total and the milestone amounts are all recomputed server-side, so sending
    them back would only invite the model to treat stale arithmetic as fact.
    The percentages and rates stay, because those are its own judgement and it
    needs them to revise coherently.
    """
    payload = estimate.model_dump(mode="json")

    for item in payload.get("line_items", []):
        item.pop("subtotal", None)

    cost = payload.get("cost", {})
    for derived in ("subtotal", "contingency_amount", "tax_amount", "total"):
        cost.pop(derived, None)
    for milestone in cost.get("payment_milestones", []):
        milestone.pop("amount", None)

    return json.dumps(payload, ensure_ascii=False, indent=1)


async def revise_estimate(
    prior: Estimate,
    instruction: str,
    target_total: float | None = None,
    rate_card_text: str = "",
    unit_basis_text: str = "",
    payment_terms_text: str = "",
    kind: str = "software",
    kind_label: str = "",
) -> Estimate:
    """Re-scope an existing estimate and return the revised `Estimate`.

    The currency and market are inherited from `prior` and are not negotiable -
    a revision that re-prices in another currency is a conversion, and this
    system never converts.

    Arithmetic is not settled here. `app.domain.costing.recompute` corrects it,
    and `app.domain.costing.snap_to_total` lands it on `target_total` exactly. The model's
    job is that the scope justifies the number, not that the number adds up.

    Raises:
        GeminiConfigError: no key, or the key was rejected.
        GeminiResponseError: the model answered with something unusable.
    """
    currency = (prior.currency or "PHP").strip().upper()
    region = (prior.market_region or "Philippines").strip()

    # The studio types a target on the same basis as the rates: exclusive of
    # tax, it is the price of the work and the tax goes on top. The model works
    # in cost.total, which is gross, so it is handed the gross figure - and
    # `_apply_target` converts the same typed number the same way afterwards,
    # so prompt and arithmetic aim at one figure rather than two.
    prompt_target = gross_for_target(prior, target_total) if target_total else target_total

    contents: list[Any] = [
        build_revision(
            _strip_for_prompt(prior),
            instruction,
            kind=kind,
            kind_label=kind_label,
            currency=currency,
            region=region,
            target_total=prompt_target,
            prior_total=prior.cost.total,
            rate_card_text=rate_card_text,
            unit_basis_text=unit_basis_text,
            payment_terms_text=payment_terms_text,
        )
    ]

    logger.info(
        "Revising estimate: model=%s currency=%s prior_total=%.2f target=%s instruction_chars=%d",
        config.GEMINI_MODEL,
        currency,
        prior.cost.total,
        f"{target_total:.2f}" if target_total is not None else "none",
        len(instruction or ""),
    )

    response = await _generate_with_retry(contents, REVISION_SYSTEM_INSTRUCTION)
    revised = _coerce_estimate(response)

    # The request, not the model, is authoritative about identity - same rule as
    # a first-time generation, with the parent standing in for the request.
    revised.currency = currency
    revised.market_region = region
    # Inherited like the currency, and for the same reason: flipping the tax
    # basis mid-revision changes what the number means to the client. A
    # quotation sent with no tax line stays that way - a revision that quietly
    # introduces VAT is a different offer, not a revised one.
    revised.cost.tax_inclusive = prior.cost.tax_inclusive
    if not (prior.cost.tax_label or "").strip() or prior.cost.tax_pct <= 0:
        revised.cost.tax_label = ""
        revised.cost.tax_pct = 0.0
    if not revised.client_name.strip():
        revised.client_name = prior.client_name
    if not revised.project_name.strip():
        revised.project_name = prior.project_name
    if not revised.client.title.strip():
        revised.client.title = prior.client.title or revised.project_name

    logger.info(
        "Revision received: %d requirements, %d line items, confidence=%s",
        len(revised.requirements),
        len(revised.line_items),
        revised.confidence.value,
    )
    return revised

async def generate_proposal(
    estimate: Estimate,
    studio_name: str = "",
    policy_titles: list[str] | None = None,
) -> ProposalNarrative:
    """Write the persuasive half of a proposal for a settled quotation.

    A different schema from the estimate, and that is the point: `ProposalNarrative`
    has no field for a figure and none for a term, so the model cannot return
    either. The terms are inserted afterwards from the studio's own settings and
    the figures are printed from the quotation.

    Raises:
        GeminiConfigError: no key, or the key was rejected.
        GeminiResponseError: the model answered with something unusable.
    """
    contents = [build_proposal_brief(estimate, studio_name, policy_titles or [])]

    logger.info(
        "Writing a proposal: model=%s client=%r project=%r",
        config.GEMINI_MODEL,
        estimate.client_name,
        estimate.project_name,
    )

    client = _get_client()
    call = functools.partial(
        client.models.generate_content,
        model=config.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ProposalNarrative,
            temperature=config.GEMINI_TEMPERATURE,
            system_instruction=PROPOSAL_SYSTEM_INSTRUCTION,
        ),
    )

    for attempt in (1, 2):
        try:
            response = await anyio.to_thread.run_sync(call)
            break
        except Exception as exc:
            if _is_auth_failure(exc):
                raise GeminiConfigError(
                    "Gemini rejected the API key. Check GEMINI_API_KEY in backend/.env."
                ) from exc
            if attempt == 2 or not _is_transient(exc):
                raise GeminiResponseError(
                    f"The proposal could not be written ({exc.__class__.__name__}).",
                    _snippet(str(exc)),
                ) from exc
            logger.warning("Transient failure writing a proposal, retrying once: %s", exc)

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ProposalNarrative):
        narrative = parsed
    else:
        text = getattr(response, "text", "") or ""
        if not text.strip():
            raise GeminiResponseError(
                "Gemini returned nothing for the proposal. "
                + (_blocked_reason(response) or "No reason was given."),
                "",
            )
        try:
            narrative = ProposalNarrative.model_validate_json(text)
        except ValueError as exc:
            raise GeminiResponseError(
                "Gemini's proposal did not match the expected shape.", _snippet(text)
            ) from exc

    logger.info(
        "Proposal written: %d reasons, %d deliverables, %d next steps",
        len(narrative.why_us),
        len(narrative.deliverables),
        len(narrative.next_steps),
    )
    return narrative


# --- Is this a brief at all? -------------------------------------------------

BRIEF_CHECK_INSTRUCTION = """You judge one thing: does this text describe work somebody wants done?

Say yes to anything that names work, however roughly - one line, a fragment, a list of features, a job in any language, a job described badly. A studio typing "logo and business cards" is briefing you. So is "kailangan namin ng website para sa aming tindahan". So is a single sentence with a spelling mistake in it.

Say no only when there is no work in the text at all: keyboard mashing, random letters or syllables, repeated characters, lorem ipsum, a word or two with no job attached, or something that is plainly a test.

You are not judging whether the brief is GOOD, whether it is detailed enough to price, or whether the work is sensible. A one-line brief is a brief. Vagueness is the studio's problem to solve with their client, not yours to refuse.

When you say no, give one short sentence addressed to the person who typed it, saying what is missing. Never quote the text back at them."""


#: The same fencing `build_brief` uses, for the same reason: the text inside is
#: somebody's typing, and a prompt that does not say where it ends can be closed
#: early by the text itself. `strip_sentinels` below removes any the input
#: already contained, so these two lines are the only ones the model sees.
BRIEF_CHECK_FRAME = """<<<BRIEF
{body}
BRIEF>>>"""


def _brief_check_snippet(text: str, limit: int = 1200) -> str:
    """Enough to judge, and no more.

    Meaning shows up in the first paragraph; a 20,000-character brief costs
    twenty times the tokens to reach the same answer. Sentinels are stripped
    for the reason `build_brief` strips them - this text is untrusted input
    being placed inside a prompt, and the studio's own pad is not exempt from
    that just because a studio typed it.
    """
    return strip_sentinels(text.strip())[:limit]


async def check_brief_is_real(text: str) -> BriefCheck:
    """Ask whether `text` describes work, before anything is priced from it.

    One call, one small schema, a truncated brief - deliberately cheap, because
    it runs before every generation. It is asked BEFORE the tiers rather than
    answered as a field on `Estimate`, since a field on the estimate can only
    be filled by a generation that has already happened and been paid for.

    RAISES NOTHING. Every failure - no key, a transport error, a blocked
    response, a malformed body - returns the accepting default. That inverts
    this codebase's usual rule and it is meant to: this is a quality gate, not
    a security boundary. A false refusal stops a studio from quoting at all,
    while a false accept is one quotation somebody deletes. The caller cannot
    tell "the model said yes" from "the check could not run", and does not need
    to - both mean carry on. What the caller must never do is treat silence as
    a refusal.
    """
    body = _brief_check_snippet(text)
    if not body:
        return BriefCheck()
    try:
        client = _get_client()
        call = functools.partial(
            client.models.generate_content,
            model=config.GEMINI_MODEL,
            contents=[BRIEF_CHECK_FRAME.format(body=body)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BriefCheck,
                # Zero, not GEMINI_TEMPERATURE. This is a judgement that should
                # not change between two runs of the same text - a studio told
                # their brief is gibberish, who presses Generate again and gets
                # a quotation, has learned that the check is noise.
                temperature=0.0,
                system_instruction=BRIEF_CHECK_INSTRUCTION,
            ),
        )
        response = await anyio.to_thread.run_sync(call)
    except Exception as exc:  # noqa: BLE001 - see the docstring: this fails open
        logger.warning("brief check could not run, proceeding: %s", _describe(exc))
        return BriefCheck()

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BriefCheck):
        return parsed
    raw = getattr(response, "text", "") or ""
    try:
        return BriefCheck.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001 - a body that will not parse is not a refusal
        logger.warning("brief check returned an unreadable body, proceeding: %s", _snippet(raw))
        return BriefCheck()
