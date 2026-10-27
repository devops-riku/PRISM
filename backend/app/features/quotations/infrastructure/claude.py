"""The single model call, made to Claude through the Anthropic SDK.

Package is `anthropic`. The client is built lazily inside this module rather
than at import time: a missing key has to surface as a clean 503, and
`/api/health` has to answer while it is missing.

Two shapes of call, chosen by schema size rather than by taste:

  * `BriefCheck` and `ProposalNarrative` are small enough for structured
    outputs, so the API itself guarantees the JSON matches the model.
  * `Estimate` is not. Its schema compiles to a grammar the API refuses as too
    large, so the schema goes in the system prompt instead and the reply is
    validated here. Every field on `Estimate` has a default, which is what
    makes that safe: a partial object still validates, and a reply that is not
    an object at all is a clear 502.

Retries are the SDK's - it retries connection errors, 408, 409, 429 and 5xx
with backoff before any of that reaches this module.
"""

from __future__ import annotations

import base64
import functools
import io
import json
import logging
import re
import threading
import time
from typing import Any, Sequence

import anthropic
import anyio
import anyio.to_thread  # explicit: `anyio.to_thread` is a submodule, not re-exported
from PIL import Image

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

logger = logging.getLogger("prism.claude")

_SNIPPET_LIMIT = 600

#: Room for the thinking and the whole JSON object. An estimate with a full
#: developer spec runs to tens of thousands of tokens, and running out of room
#: mid-object is a failed generation, so this is generous and always streamed.
_ESTIMATE_MAX_TOKENS = 64_000
_PROPOSAL_MAX_TOKENS = 32_000
_BRIEF_CHECK_MAX_TOKENS = 4_000

#: Server-side refusal fallback: if a safety classifier declines the request on
#: the configured model, the API re-runs it on a model chosen by refusal
#: category, inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: An advisory ceiling on one estimate's thinking plus writing - see
#: `_stream_estimate`. Roughly three times the JSON itself.
_TASK_BUDGET_BETA = "task-budgets-2026-03-13"
_ESTIMATE_TASK_BUDGET = 32_000


class ModelError(RuntimeError):
    """Base class for every failure of the generation step."""


class ModelConfigError(ModelError):
    """The key is missing or rejected. Surfaces as 503 - the operator must act."""


class ModelResponseError(ModelError):
    """The model answered, but not with a usable Estimate. Surfaces as 502."""

    def __init__(self, message: str, snippet: str = "") -> None:
        super().__init__(message)
        self.snippet = snippet


# --- Client ------------------------------------------------------------------

_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()


def _get_client() -> anthropic.Anthropic:
    """Build the client once, on first use, never at import time."""
    global _client
    api_key = config.ANTHROPIC_API_KEY.strip()
    if not api_key:
        raise ModelConfigError(config.MISSING_KEY_MESSAGE)

    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    _client = anthropic.Anthropic(api_key=api_key, max_retries=3)
                except Exception as exc:  # malformed key, bad transport options
                    raise ModelConfigError(
                        "Could not initialise the Anthropic client "
                        f"({exc.__class__.__name__}). Check ANTHROPIC_API_KEY in backend/.env "
                        "and restart the API."
                    ) from exc
    return _client


# --- Errors ------------------------------------------------------------------


def _describe(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}".lower()


def _snippet(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + " ... [truncated]"


def _translate(exc: Exception, what: str) -> ModelError:
    """One SDK exception, as the error class the routes know how to answer.

    Most specific first. By the time a transient failure (429, 5xx, a dropped
    connection) lands here the SDK has already retried it, so it is reported,
    not retried again.
    """
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ModelConfigError(
            "Anthropic rejected the configured API key. Check ANTHROPIC_API_KEY in "
            f"backend/.env, confirm the key has access to {config.CLAUDE_MODEL}, and "
            "restart the API."
        )
    if isinstance(exc, anthropic.NotFoundError):
        return ModelResponseError(
            f"{what} could not be generated: the model {config.CLAUDE_MODEL!r} was not "
            "found. Check CLAUDE_MODEL in backend/.env.",
            snippet=_snippet(str(exc)),
        )
    if isinstance(exc, anthropic.APIStatusError):
        return ModelResponseError(
            f"{what} could not be generated (status {exc.status_code}). Try again in a "
            "moment; if it keeps happening, check the model id in backend/.env.",
            snippet=_snippet(str(exc)),
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return ModelResponseError(
            f"{what} could not be generated: the Anthropic API could not be reached "
            f"({exc.__class__.__name__}). Try again in a moment.",
            snippet=_snippet(str(exc)),
        )
    return ModelResponseError(
        f"{what} could not be generated: {exc.__class__.__name__}.",
        snippet=_snippet(str(exc)),
    )


def _check_stop(message: Any, what: str) -> None:
    """A reply that stopped for any reason but finishing is not parsed."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        raise ModelResponseError(
            f"The model declined to write {what}"
            + (f" ({category})" if category else "")
            + ". Rephrase the brief or remove the attached images and try again."
        )
    if stop == "max_tokens":
        raise ModelResponseError(
            f"The model ran out of room before finishing {what}. Try again; if it "
            "repeats, shorten the brief or split the work into two quotations."
        )


def _text_of(message: Any) -> str:
    return "".join(
        getattr(block, "text", "") for block in getattr(message, "content", []) or []
        if getattr(block, "type", "") == "text"
    )


def _log_usage(label: str, message: Any) -> None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    logger.info(
        "%s usage: model=%s input=%s cache_read=%s output=%s",
        label,
        getattr(message, "model", config.CLAUDE_MODEL),
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        getattr(usage, "output_tokens", "?"),
    )


# --- Images ------------------------------------------------------------------

#: What the Messages API reads. Anything else is re-encoded or left out.
_CLAUDE_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

#: The long edge Claude reads at full resolution. A larger image is downscaled
#: by the API anyway, so it is downscaled here first and costs less to send.
_MAX_LONG_EDGE = 2576

#: The largest image sent as uploaded. The API takes 10 MB of base64 per image
#: and 32 MB for the whole request; eight images at this size stay under both.
_PASSTHROUGH_BYTES = 2 * 1024 * 1024


def _prepare_image(data: bytes, mime_type: str) -> tuple[bytes, str] | None:
    """The image as Claude can read it, or None when it cannot be made readable.

    Sent untouched when it is already a type Claude reads and small enough.
    Otherwise downscaled and re-encoded as JPEG. None for anything Pillow
    cannot open - HEIC, most often, which phones send and Claude does not read.
    """
    mime = (mime_type or "").strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    try:
        with Image.open(io.BytesIO(data)) as img:
            if (
                mime in _CLAUDE_IMAGE_TYPES
                and len(data) <= _PASSTHROUGH_BYTES
                and max(img.size) <= _MAX_LONG_EDGE
            ):
                return data, mime
            img.thumbnail((_MAX_LONG_EDGE, _MAX_LONG_EDGE))
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.split()[-1])
            else:
                flat = img.convert("RGB")
            for quality in (85, 70, 55):
                buffer = io.BytesIO()
                flat.save(buffer, "JPEG", quality=quality, optimize=True)
                if buffer.tell() <= _PASSTHROUGH_BYTES:
                    return buffer.getvalue(), "image/jpeg"
    except Exception as exc:  # noqa: BLE001 - an unreadable image is left out, not fatal
        logger.warning("Image (%s) could not be prepared: %s", mime or "unknown type", _describe(exc))
    return None


def _skipped_note(mime_type: str) -> str:
    label = (mime_type or "").split("/")[-1].upper() or "unrecognised"
    return (
        f"One attached image ({label}) was not read: the model accepts JPEG, PNG, GIF "
        "and WebP. Re-attach it in one of those formats to have it priced."
    )


def _prepare_images(
    images: Sequence[tuple[bytes, str]],
) -> tuple[list[tuple[bytes, str]], list[str]]:
    """The images that can be sent, and one note per image that could not."""
    sent: list[tuple[bytes, str]] = []
    notes: list[str] = []
    for data, mime_type in images:
        prepared = _prepare_image(data, mime_type)
        if prepared is None:
            notes.append(_skipped_note(mime_type))
        else:
            sent.append(prepared)
    return sent, notes


# --- Generation --------------------------------------------------------------

#: The schema the estimate prompts promise ("a single JSON object conforming to
#: the supplied schema"). Built once: it is the same bytes on every call, which
#: keeps the system prompt a stable prefix for prompt caching.
_ESTIMATE_SCHEMA = json.dumps(Estimate.model_json_schema(), ensure_ascii=False, separators=(",", ":"))


def _estimate_system(instruction: str) -> list[dict[str, Any]]:
    text = f"{instruction}\n\nJSON SCHEMA FOR THE ESTIMATE OBJECT\n{_ESTIMATE_SCHEMA}"
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


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
    """A flat list: the brief text first, then one image block per attachment."""
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
        contents.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
            },
        })
    return contents


def _as_user_content(contents: Sequence[Any]) -> list[dict[str, Any]]:
    return [{"type": "text", "text": part} if isinstance(part, str) else part for part in contents]


def _stream_estimate(contents: Sequence[Any], instruction: str) -> Any:
    """Blocking: one streamed call, returning the final message.

    The task budget is what keeps this to minutes. Thinking cannot be switched
    off on the current Opus, and without a budget it varied run to run from
    nothing to fifty thousand tokens before the first character of JSON - the
    object itself is about eleven thousand. The budget is advisory: the model
    sees it count down and paces itself to finish, where `max_tokens` would
    simply cut it off mid-object.
    """
    client = _get_client()
    started = time.monotonic()
    first_text: float | None = None
    text_chars = 0
    with client.beta.messages.stream(
        model=config.CLAUDE_MODEL,
        max_tokens=_ESTIMATE_MAX_TOKENS,
        system=_estimate_system(instruction),
        messages=[{"role": "user", "content": _as_user_content(contents)}],
        output_config={
            "effort": config.CLAUDE_EFFORT,
            "task_budget": {"type": "tokens", "total": _ESTIMATE_TASK_BUDGET},
        },
        betas=[_FALLBACK_BETA, _TASK_BUDGET_BETA],
        fallbacks="default",
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                if first_text is None:
                    first_text = time.monotonic() - started
                text_chars += len(event.delta.text)
        message = stream.get_final_message()
    logger.info(
        "Estimate stream: stop=%s elapsed=%.1fs first_text=%s text_chars=%d",
        message.stop_reason,
        time.monotonic() - started,
        f"{first_text:.1f}s" if first_text is not None else "never",
        text_chars,
    )
    return message


async def _generate(contents: Sequence[Any], instruction: str = "") -> Any:
    call = functools.partial(_stream_estimate, contents, instruction or SYSTEM_INSTRUCTION)
    try:
        # `anyio.to_thread.run_sync` forwards positional arguments only, hence the partial.
        message = await anyio.to_thread.run_sync(call)
    except ModelError:
        raise
    except Exception as exc:
        raise _translate(exc, "The estimate") from exc
    _log_usage("Estimate", message)
    return message


def _coerce_estimate(message: Any) -> Estimate:
    """The reply's JSON object as an `Estimate`, or a clear error."""
    _check_stop(message, "the estimate")
    raw = _text_of(message).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ModelResponseError(
            "The model returned no estimate. Try again; if it repeats, rephrase the brief.",
            snippet=_snippet(raw),
        )
    try:
        return Estimate.model_validate_json(raw[start : end + 1])
    except Exception as exc:
        raise ModelResponseError(
            "The model returned a response that is not a valid estimate. Try again.",
            snippet=_snippet(raw),
        ) from exc


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

    An image Claude cannot read (HEIC, a corrupt file) is left out rather than
    failing the run, and says so in `image_observations` - the brief is told
    how many images actually follow, so the model never describes one it was
    not given.

    Raises:
        ModelConfigError: no key, or the key was rejected.
        ModelResponseError: the model answered with something unusable.
    """
    images, skipped = _prepare_images(list(images or []))
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
        "Generating estimate: model=%s kind=%s currency=%s region=%s images=%d skipped=%d brief_chars=%d",
        config.CLAUDE_MODEL,
        kind or "software",
        req.currency,
        req.market_region,
        len(images),
        len(skipped),
        len(req.brief or ""),
    )

    response = await _generate(contents)
    estimate = _coerce_estimate(response)
    estimate = _apply_request_identity(estimate, req)
    if skipped:
        estimate.image_observations = [*estimate.image_observations, *skipped]

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
        ModelConfigError: no key, or the key was rejected.
        ModelResponseError: the model answered with something unusable.
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
        config.CLAUDE_MODEL,
        currency,
        prior.cost.total,
        f"{target_total:.2f}" if target_total is not None else "none",
        len(instruction or ""),
    )

    response = await _generate(contents, REVISION_SYSTEM_INSTRUCTION)
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
        ModelConfigError: no key, or the key was rejected.
        ModelResponseError: the model answered with something unusable.
    """
    brief = build_proposal_brief(estimate, studio_name, policy_titles or [])

    logger.info(
        "Writing a proposal: model=%s client=%r project=%r",
        config.CLAUDE_MODEL,
        estimate.client_name,
        estimate.project_name,
    )

    def _stream_proposal() -> Any:
        client = _get_client()
        with client.beta.messages.stream(
            model=config.CLAUDE_MODEL,
            max_tokens=_PROPOSAL_MAX_TOKENS,
            system=PROPOSAL_SYSTEM_INSTRUCTION,
            messages=[{"role": "user", "content": brief}],
            output_config={"effort": config.CLAUDE_EFFORT},
            output_format=ProposalNarrative,
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            return stream.get_final_message()

    try:
        message = await anyio.to_thread.run_sync(_stream_proposal)
    except ModelError:
        raise
    except Exception as exc:
        raise _translate(exc, "The proposal") from exc
    _log_usage("Proposal", message)
    _check_stop(message, "the proposal")

    narrative = getattr(message, "parsed_output", None)
    if not isinstance(narrative, ProposalNarrative):
        text = _text_of(message)
        try:
            narrative = ProposalNarrative.model_validate_json(text)
        except ValueError as exc:
            raise ModelResponseError(
                "The model's proposal did not match the expected shape.", _snippet(text)
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
            client.beta.messages.parse,
            model=config.CLAUDE_MODEL,
            max_tokens=_BRIEF_CHECK_MAX_TOKENS,
            system=BRIEF_CHECK_INSTRUCTION,
            messages=[{"role": "user", "content": BRIEF_CHECK_FRAME.format(body=body)}],
            # Low effort: a yes/no on a few hundred characters, run before every
            # generation. No sampling knob to pin - the current models take
            # none - so the instruction carries the consistency instead.
            output_config={"effort": "low"},
            output_format=BriefCheck,
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        )
        response = await anyio.to_thread.run_sync(call)
    except Exception as exc:  # noqa: BLE001 - see the docstring: this fails open
        logger.warning("brief check could not run, proceeding: %s", _describe(exc))
        return BriefCheck()

    parsed = getattr(response, "parsed_output", None)
    if isinstance(parsed, BriefCheck):
        return parsed
    raw = _text_of(response)
    try:
        return BriefCheck.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001 - a body that will not parse is not a refusal
        logger.warning("brief check returned an unreadable body, proceeding: %s", _snippet(raw))
        return BriefCheck()
