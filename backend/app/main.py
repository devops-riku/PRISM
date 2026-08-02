"""PRISM HTTP surface - the exact endpoints in docs/CONTRACT.md section 4.

One submission in, one generation call, two documents out. There is no chat
loop, no session, no database.

Run it any of these ways:

    py -m uvicorn app.main:app --reload --port 8000     # from backend/
    py -m app.main                                      # from backend/
    py backend/app/main.py                              # from anywhere
"""

from __future__ import annotations

# Allows `py backend/app/main.py` to work from any cwd by putting `backend/` on
# the path before the package imports below resolve. A no-op under uvicorn.
if __package__ in (None, ""):  # pragma: no cover - entry-point convenience only
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import asyncio
import json
import logging
import math
import re
import threading
import time
from collections import defaultdict, deque
from typing import List, Union

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

# `fastapi.UploadFile` subclasses this but is not the same class. Matching on the
# Starlette base accepts whichever one the running FastAPI hands back.
from starlette.datastructures import UploadFile as BaseUploadFile

from app import (
    attachments as attachments_module,
    auth,
    clientview,
    config,
    hub,
    inbox,
    intakes,
    mailer,
    members,
    design,
    documents,
    jobs,
    kinds,
    payments,
    policies,
    prompts,
    ratecard,
    reference,
    template,
    settings,
    storage,
    tokens,
    workspaces,
)
from app.costing import (
    CostingError,
    absorb_contingency,
    gross_for_target,
    money_decimals,
    recompute,
    snap_to_total,
)
from app.gemini_service import (
    GeminiConfigError,
    GeminiResponseError,
    generate_estimate,
    generate_proposal,
    revise_estimate,
)
from app.renderers import (
    format_money,
    quotation_reference,
    render_client_proposal,
    render_developer_requirements,
    render_pdf,
    render_print_html,
    render_proposal,
)
from app.schemas import (
    Estimate,
    GeneratedFile,
    PolicyRecord,
    ProposalBundle,
    ProposalDocument,
    ProposalDocumentSummary,
    ProposalRequest,
    PaymentTermsRecord,
    ProposalSummary,
    SectionRecord,
    TierSibling,
    RevisionRequest,
)

if not logging.getLogger().handlers:  # uvicorn does not configure the root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    )

logger = logging.getLogger("prism.api")

KINDS = ("proposal", "requirements")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_SLUG_STRIP = re.compile(r"[^A-Za-z0-9]+")


app = FastAPI(
    title="PRISM",
    version="1.0.0",
    summary="One brief in, two documents out.",
    description=(
        "Submit a client brief with optional reference images and receive a priced "
        "client proposal and a requirements specification shaped by the kind of work "
        "being quoted, both rendered from a single structured estimate."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    # So the browser can read the filename off a download response.
    expose_headers=["Content-Disposition"],
)


# --- Response models unique to the HTTP layer --------------------------------


class CurrencyOption(BaseModel):
    code: str
    name: str
    symbol: str


class HealthResponse(BaseModel):
    status: str
    model: str
    key_configured: bool


#: Curated, not exhaustive. PHP first because that is the home market; the rest
#: cover the currencies a small consultancy is realistically asked to quote in.
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

_CURRENCY_CODES = {option.code for option in CURRENCIES}

#: Strong references to in-flight background work. asyncio only keeps a weak
#: reference to a task, so a job with nothing holding it can be collected
#: half-finished. Each task removes itself when it is done.
_BACKGROUND: set = set()


#: The header the client names its workspace in. A header rather than a path
#: segment so every existing URL - and every link already sent to somebody - is
#: unchanged by workspaces existing.
WORKSPACE_HEADER = "X-Workspace"
#: The same name on a link, where a header cannot go.
WORKSPACE_PARAM = "workspace"


@app.on_event("startup")
async def _say_whether_anybody_has_to_sign_in() -> None:
    if auth.required():
        logger.info("Sign-in required: access tokens are verified on every call.")
    else:
        logger.warning(
            "No sign-in configured: this API answers anyone who can reach it. Set "
            "SUPABASE_URL (and SUPABASE_ANON_KEY) in backend/.env to require one."
        )


@app.on_event("startup")
async def _prepare_workspaces_and_bury_dead_jobs() -> None:
    # First, because everything else is stored inside a workspace: on an install
    # from before they existed this moves the whole of `generated/` into one.
    workspaces.ensure_ready()
    jobs.restore()


@app.on_event("startup")
async def _build_the_client_token_index() -> None:
    # After workspaces exist (the walk below reads `workspaces.listing()`),
    # and before this server answers its first request: see
    # `tokens.build_index()`'s own docstring for what this is paying for up
    # front, deliberately, rather than letting `/api/client/<token>`'s first
    # caller - who could be a stranger's first guess, since that route needs
    # no token of the studio's own to reach - pay for it instead.
    tokens.build_index()


@app.exception_handler(workspaces.NoWorkspace)
async def _nowhere_to_file_it(request, exc: workspaces.NoWorkspace) -> JSONResponse:
    """Answer plainly when there is no workspace yet.

    409 rather than 500: nothing is broken, the app simply has not been told
    whose work this is. Every screen that reads or writes anything gets this
    until a workspace exists, and the client turns it into the one thing worth
    doing - naming one.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.middleware("http")
async def _gate(request, call_next):
    """Who is asking, which workspace they mean, and whether they may.

    All three in one place and in this order, because each answer depends on the
    one before it. Two middlewares got this wrong: Starlette runs the last one
    registered first, so the workspace was being resolved before the token was
    checked, and the membership test read a user nobody had established yet.

    1. **The token.** Verified when this install has accounts, and skipped
       entirely when it does not - see app/auth.py for why unconfigured means
       open rather than closed.
    2. **The workspace.** From a header, or from the address for the links a
       browser opens by itself. Everything a handler touches resolves through
       `workspaces.root()`, so this one line keeps two studios' books apart.
    3. **The team.** An unclaimed workspace takes its first visitor as admin,
       which is how the workspaces that existed before teams did get an owner.
       Anyone not on the roster gets 403 rather than a quiet empty page, and a
       member is stopped from the two things a member may not do: change what
       the studio charges, and delete anything.
    """
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path.rstrip("/") or "/"
    # Reading an invitation is open, because the token in the link is the
    # secret: somebody deciding whether to make an account should be able to see
    # what they are being asked to join first. Accepting it is not - that needs
    # to know who is joining.
    #
    # `/api/client/` is open on every method, not just GET - the only prefix in
    # this expression for which that is true, and so the only one able to admit
    # a POST without a token at all. Stage 2 Task 3 adds the GET beneath it;
    # Task 4 adds POSTs beside it (`/submit`, `/revise`, `/finalize`). What
    # makes this prefix safe to leave open is three things, all load-bearing:
    # the token itself is the credential - unguessable, minted one per intake,
    # living nowhere but the link the studio sent - and `tokens.resolve` treats
    # an unknown, expired, relinked-away or closed one identically, so a
    # stranger probing this prefix cannot even learn which guesses ever meant
    # anything; the handler behind each write route re-checks the intake's own
    # state before acting (via `intakes.advance`'s own transition table), so a
    # token that is real but wrong for the write attempted is refused, not
    # merely authenticated; and, since Task 4, a per-IP-and-route rate limit
    # (`_enforce_rate_limit`, beside the three write routes below) on the
    # write routes - a courtesy control against a script trying every token it
    # can generate or double-submitting by accident, not a defence against a
    # determined or distributed attacker. Say that last part plainly, because
    # it is the one of the three that is not actually load-bearing security:
    # anyone who can send from more than one address, or who simply waits out
    # the window, is unaffected by it.
    #
    # `path == "/api/client"` (no trailing token at all, with or without a
    # trailing slash - `path` above is already `rstrip("/")`-normalised) is
    # listed on its own rather than folded into the prefix test: nothing is
    # ever registered at exactly that path, so opening it changes nothing
    # about what is servable, and *not* opening it was the one place this
    # door answered 401 instead of the 404 every other malformed attempt at
    # it gets - the one inconsistent answer on an otherwise uniform surface.
    open_path = (
        path in auth.OPEN_PATHS
        or not path.startswith("/api/")
        or (request.method == "GET" and path.startswith("/api/invites/"))
        or path == "/api/client"
        or path.startswith("/api/client/")
    )

    if auth.required() and not open_path:
        try:
            request.state.user = auth.verify(request.headers.get("Authorization", ""))
        except auth.AuthError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})

    workspaces.use(
        request.headers.get(WORKSPACE_HEADER, "")
        or request.query_params.get(WORKSPACE_PARAM, "")
    )

    # Whose request this is, for as long as it lives - and for whatever it
    # starts, since a task copies the context it was created in. That is what
    # lets a quotation finishing ninety seconds later still know whose news it
    # is.
    signed_in = getattr(request.state, "user", None)
    inbox.use_identity(signed_in.email if signed_in else "", signed_in.id if signed_in else "")

    user = getattr(request.state, "user", None)
    if user is None or not workspaces.current():
        return await call_next(request)

    members.remember_id(user.email, user.id)

    roster = members.listing()
    if not roster:
        # Nobody administers this workspace yet - the state every workspace
        # made before teams existed is in. It stays open, exactly as it was
        # before this feature, and claiming it is a deliberate act on the Teams
        # page rather than something a passing GET does silently.
        request.state.role = members.ADMIN
        return await call_next(request)

    role = members.role_of(user.email, user.id)
    if not role:
        # Not on this team. The workspace list answers with the ones they are on,
        # so this is only reachable by naming a workspace directly.
        if (
            path.startswith("/api/workspaces")
            or path.startswith("/api/invites")
            # A person just removed from a team still has mail here, and
            # reading it should include being able to put it down. All three
            # verbs touch only the caller's own file and nothing else.
            or path.startswith("/api/notifications")
        ):
            return await call_next(request)
        return JSONResponse(
            status_code=403, content={"detail": "You are not on this workspace's team."}
        )

    request.state.role = role

    if role != members.ADMIN:
        # Creating a workspace of your own is not an admin act - you become its
        # admin. Renaming or deleting somebody else's is.
        forbidden = (
            request.method == "DELETE"
            or (request.method in {"PUT", "PATCH", "POST"} and path.startswith("/api/settings"))
            or (request.method == "PATCH" and path.startswith("/api/workspaces"))
            or (request.method == "POST" and path.startswith("/api/team"))
        )
        if forbidden:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Members can prepare quotations and proposals. Changing the studio's "
                        "settings or deleting anything is an admin's to do."
                    )
                },
            )

    return await call_next(request)


# --- Helpers -----------------------------------------------------------------


def _slug(value: str, fallback: str) -> str:
    """ASCII-safe filename stem. Content-Disposition is not a place for surprises."""
    cleaned = _SLUG_STRIP.sub("-", (value or "")).strip("-").lower()
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return (cleaned[:48].strip("-") or fallback)


def _filename(estimate: Estimate, kind: str, revision: int = 1) -> str:
    stem = _slug(estimate.project_name or estimate.client_name, "prism-quotation")
    # The client document is a quotation. `kind` stays "proposal" because it
    # is a URL segment other people have bookmarked; what lands in a downloads
    # folder is what the reader calls it.
    suffix = "quotation" if kind == "proposal" else "requirements"
    # A revision carries its number in the filename so two versions of the same
    # quotation do not land in a downloads folder under one name.
    version = f"-r{revision}" if revision > 1 else ""
    return f"{stem}-{suffix}{version}.md"


def _document_title(estimate: Estimate, kind: str) -> str:
    subject = (estimate.client.title or estimate.project_name or "Quotation").strip()
    if kind == "proposal":
        return f"{subject} · Quotation"
    # `subject` already names the project, so this is the short form of the
    # title; `kinds.title_for` would print the project a second time. A
    # quotation with no kind is software, and says what it has always said.
    if kinds.is_software(estimate):
        return f"{subject} · Developer requirements"
    return f"{subject} · {kinds.noun_for(estimate)} Requirements"


def _build_files(proposal_id: str, estimate: Estimate, revision: int = 1) -> List[GeneratedFile]:
    markdown_by_kind = {
        "proposal": render_client_proposal(estimate),
        "requirements": render_developer_requirements(estimate),
    }
    base = f"/api/proposals/{proposal_id}/files"
    return [
        GeneratedFile(
            kind=kind,
            filename=_filename(estimate, kind, revision),
            markdown=markdown_by_kind[kind],
            download_url=f"{base}/{kind}.md",
            print_url=f"{base}/{kind}.html",
            pdf_url=f"{base}/{kind}.pdf",
        )
        for kind in KINDS
    ]


def _require_bundle(proposal_id: str) -> ProposalBundle:
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


def _require_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown document '{kind}'. Valid documents are 'proposal' and 'requirements'.",
        )
    return kind


async def _read_documents(
    uploads: List[Union[UploadFile, str]] | None,
) -> List[attachments_module.Attachment]:
    """Read what the client sent as a document, and never fail the request for it.

    A quotation whose PDF could not be opened is still a quotation. Every file
    that could not be read comes back as an `Attachment` carrying the reason, and
    those reasons are reported on the finished quotation rather than raised here
    - the same rule the rate card follows when a role is missing.

    The loose typing on `uploads` is for the same reason as `_read_images`: a
    browser posts an empty file input as a part with no filename, and declaring
    `List[UploadFile]` makes FastAPI reject the whole request before any of this
    runs.
    """
    candidates = [
        upload
        for upload in (uploads or [])
        if isinstance(upload, BaseUploadFile) and (upload.filename or "").strip()
    ]
    if not candidates:
        return []

    if len(candidates) > config.MAX_DOCUMENTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(candidates)} documents attached. PRISM reads up to "
                f"{config.MAX_DOCUMENTS} - remove the rest and submit again."
            ),
        )

    read: List[attachments_module.Attachment] = []
    limit_mb = config.MAX_DOCUMENT_BYTES / (1024 * 1024)

    for upload in candidates:
        name = (upload.filename or "document").strip()
        declared = getattr(upload, "size", None)
        if declared is not None and declared > config.MAX_DOCUMENT_BYTES:
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {declared / (1024 * 1024):.1f} MB. The limit is "
                    f"{limit_mb:.0f} MB per document."
                ),
            )

        try:
            data = await upload.read(config.MAX_DOCUMENT_BYTES + 1)
            if len(data) > config.MAX_DOCUMENT_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{name}' is larger than the {limit_mb:.0f} MB limit.",
                )
        finally:
            await upload.close()

        read.append(attachments_module.read(name, data))

    return read


async def _read_images(
    uploads: List[Union[UploadFile, str]] | None,
) -> List[tuple[bytes, str]]:
    """Validate and read the attachments. Every rejection is a 400 that names the file.

    Two shapes of "no file was chosen" reach this function and both must be
    ignored rather than rejected:
      * a browser's empty `<input type=file>` posts a part with `filename=""`,
        which Starlette turns into an UploadFile with an empty name;
      * a part with no filename header at all arrives as a plain `str`, which is
        why the parameter is typed loosely - declaring `List[UploadFile]` makes
        FastAPI reject the whole request with an unreadable 422 before any of
        this validation runs.
    """
    candidates = [
        upload
        for upload in (uploads or [])
        if isinstance(upload, BaseUploadFile) and (upload.filename or "").strip()
    ]
    if not candidates:
        return []

    if len(candidates) > config.MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(candidates)} images attached. PRISM reads up to "
                f"{config.MAX_IMAGES} - remove the rest and submit again."
            ),
        )

    images: List[tuple[bytes, str]] = []
    limit_mb = config.MAX_IMAGE_BYTES / (1024 * 1024)

    for upload in candidates:
        name = (upload.filename or "attachment").strip()
        mime = (upload.content_type or "").split(";")[0].strip().lower()

        if not mime.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {mime or 'of an unknown type'}. Attach screenshots, "
                    "sketches or photos only - PNG, JPEG, WebP, HEIC or GIF."
                ),
            )

        # Reject on the declared size before allocating anything. Without this,
        # an oversized attachment is fully materialised in memory and only then
        # refused - and MAX_IMAGES of them can arrive in one request.
        declared = getattr(upload, "size", None)
        if declared is not None and declared > config.MAX_IMAGE_BYTES:
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {declared / (1024 * 1024):.1f} MB. The limit is "
                    f"{limit_mb:.0f} MB per image - resize it or attach a screenshot instead."
                ),
            )

        try:
            # Bounded on purpose: a part with no declared size, or one whose
            # size understates the body, must not be allowed to read past the
            # limit. One byte over is all the check below needs.
            data = await upload.read(config.MAX_IMAGE_BYTES + 1)
        except Exception as exc:
            logger.warning("Could not read upload %s: %s", name, exc)
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' could not be read. Re-attach it and submit again.",
            ) from exc
        finally:
            await upload.close()

        if not data:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is empty. Re-attach it and submit again.",
            )
        if len(data) > config.MAX_IMAGE_BYTES:
            # The read stopped one byte past the limit, so the true size is not
            # known here - say so rather than quote the cut-off as a measurement.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is larger than the {limit_mb:.0f} MB limit per image "
                    "- resize it or attach a screenshot instead."
                ),
            )

        images.append((data, mime))

    return images


def _normalise_currency(raw: str) -> str:
    code = (raw or "").strip().upper() or "PHP"
    if not _CURRENCY_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail=f"'{raw}' is not a currency code. Use a three-letter ISO 4217 code such as PHP or USD.",
        )
    return code


def _normalise_brief(raw: str) -> str:
    brief = (raw or "").strip()
    if not brief:
        raise HTTPException(
            status_code=400,
            detail="Describe the job before submitting - PRISM prices what the brief says.",
        )
    if len(brief) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The brief is {len(brief):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it or attach the detail as images."
            ),
        )
    return brief


def _normalise_instruction(raw: str) -> str:
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


def _normalise_budget_hint(raw: str) -> str:
    """`budget_hint` is free text the model reasons about, same as `brief` -
    it just never had `brief`'s ceiling. Bounded here with the same constant
    and the same shape of error, not a second convention, because a client's
    words reaching a prompt need one length rule, not one per field."""
    hint = (raw or "").strip()
    if len(hint) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The budget note is {len(hint):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it rather than pasting the whole thread."
            ),
        )
    return hint


def _normalise_scope(raw: str) -> str:
    """An intake's `scope` reaches the same prompt a brief does - Stage 1 has
    no anonymous write to it, but Stage 2 will, and the ceiling is cheap to
    have in place before that matters."""
    scope = (raw or "").strip()
    if len(scope) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The scope is {len(scope):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it or attach the detail separately."
            ),
        )
    return scope


def _normalise_budget_text(raw: str) -> str:
    """The client's own budget words, bounded the same way `scope` is - see
    `_normalise_scope`."""
    text = (raw or "").strip()
    if len(text) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The budget note is {len(text):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it rather than pasting the whole thread."
            ),
        )
    return text


def _apply_ceiling(estimate: Estimate, ceiling: float) -> tuple[Estimate, bool, str]:
    """Hold a quotation to a price it must not exceed.

    A ceiling is not a target. A quotation that already fits is returned
    untouched - padding scope up to a budget is how a client ends up paying for
    work nobody chose - and only one that came in over is brought down.

    Bringing it down uses the same solver a target total does, so the figure is
    exact and the rates survive: the effort moves, not the price of a day.

    Returns `(estimate, applied, note)`. It reports rather than raises: this now
    runs once per tier inside a background job, and one tier that cannot be
    capped is a note on that tier, not a reason to throw away the other two.
    """
    if ceiling <= 0 or estimate.cost.total <= ceiling:
        return estimate, False, ""

    over = estimate.cost.total - ceiling
    step = 10.0 ** -money_decimals(estimate.currency)

    # Not every figure is reachable - with a tax percentage applied the totals
    # a quotation can produce skip roughly one value in nine - so `snap_to_total`
    # returns the nearest, which may be a centavo *above*. A target may round
    # either way; a maximum may not. Walk down until it is genuinely under.
    aim = ceiling
    for _ in range(4):
        try:
            snapped = snap_to_total(estimate, aim)
        except CostingError as exc:
            logger.warning("Could not cap a quotation at %.2f: %s", ceiling, exc)
            return estimate, False, str(exc)
        if snapped.achieved <= ceiling + step / 2:
            break
        # Step down from the aim, not from what it achieved. Deriving the next
        # aim from the result lands on the same unreachable figure again and the
        # loop makes no progress.
        aim -= step
    else:  # pragma: no cover - four steps is far more than the lattice needs
        note = (
            f"This quotation could not be brought under {ceiling:,.2f}. Raise the cap or cut "
            "scope in the brief."
        )
        logger.warning(note)
        return estimate, False, note

    logger.warning(
        "Quotation came in %.2f over the %.2f cap and was brought down to %.2f",
        over,
        ceiling,
        snapped.achieved,
    )
    return snapped.estimate, True, ""


def _normalise_tiers(raw: str) -> List[str]:
    """Parse 'Basic, Standard, Extended' into distinct tier names.

    One name is not a tier - it is a single quotation with a label - so it is
    treated as none at all. The cap is the point past which somebody has pasted
    the wrong thing into the field.
    """
    names: List[str] = []
    for part in (raw or "").replace("\n", ",").split(","):
        name = " ".join(part.split())[:40]
        if name and name.casefold() not in {existing.casefold() for existing in names}:
            names.append(name)

    if len(names) > 6:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That is {len(names)} tiers. Six is the most PRISM will price in one pass - "
                "each is a full quotation and a separate call."
            ),
        )
    return names if len(names) > 1 else []


def _with_parent_ref(bundle: ProposalBundle) -> ProposalBundle:
    """Attach the printed reference of the bundle this one was revised from.

    `parent_id` is storage - a hex string nobody has ever said out loud. What a
    reader needs is the number on the quotation it came from, which is the thing
    the client already has in their inbox.

    Resolved here rather than stored so the quotations prepared before this
    existed show it too, and left empty when the parent has been deleted: the
    id still links, because a dead link is a better answer than inventing a
    reference for a document that is gone.
    """
    if not bundle.parent_id:
        return bundle

    parent = storage.get(bundle.parent_id)
    if parent is None:
        return bundle

    filled = bundle.model_copy(deep=True)
    filled.parent_ref = (parent.estimate.quotation_ref or "").strip()
    return filled


def _with_siblings(bundle: ProposalBundle) -> ProposalBundle:
    """Attach the other tiers quoted from the same brief.

    Resolved on read rather than stored, because a sibling's total changes when
    it is revised and a copy written at creation time would go stale silently.
    """
    if not bundle.tier_group_id:
        return bundle

    siblings = [
        TierSibling(
            id=other.id,
            tier_name=other.tier_name,
            tier_index=other.tier_index,
            total=other.estimate.cost.total,
            currency=other.estimate.currency,
        )
        for other in storage.all_bundles()
        if other.tier_group_id == bundle.tier_group_id
    ]
    siblings.sort(key=lambda item: (item.tier_index, item.tier_name))

    filled = bundle.model_copy(deep=True)
    filled.tier_siblings = siblings
    return filled


def _payment_terms(request: ProposalRequest) -> payments.PaymentTerms:
    """Build the terms from the form, and refuse a schedule that does not total 100.

    A written schedule arrives as a JSON string because multipart has no nested
    types. Both failure modes - malformed JSON and a total that is not 100 - are
    400s naming what is wrong, because both are something the sender can fix.
    """
    rows: List[payments.ScheduleRow] = []
    raw = (request.payment_schedule or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="The payment schedule was not readable. Send it as a JSON array of "
                '{"percent": number, "trigger": text} objects.',
            ) from exc
        if not isinstance(parsed, list):
            raise HTTPException(
                status_code=400,
                detail="The payment schedule must be a list of payments.",
            )
        for entry in parsed:
            if not isinstance(entry, dict):
                raise HTTPException(
                    status_code=400,
                    detail="Every payment in the schedule must name a percent and a trigger.",
                )
            rows.append(
                payments.ScheduleRow(
                    percent=float(entry.get("percent") or 0.0),
                    trigger=str(entry.get("trigger") or ""),
                )
            )

    try:
        return payments.PaymentTerms(
            deposit_pct=request.deposit_pct,
            instalments=request.instalments,
            cadence=request.payment_cadence,
            deposit_trigger=request.deposit_trigger or "Signed statement of work",
            schedule=rows,
        ).validated()
    except payments.TermsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _normalise_flag(raw: str) -> bool:
    """A checkbox arrives as whatever the client felt like sending."""
    return (raw or "").strip().lower() in {"1", "true", "yes", "on", "inclusive"}


def _normalise_target(raw: str, currency: str) -> float:
    """Parse a target total typed by a human.

    People type money the way they read it, so thousands separators, a currency
    symbol and stray spaces are all accepted. An empty field means no target,
    which is a valid revision - "drop the SMS work" needs no number.
    """
    text = (raw or "").strip()
    if not text:
        return 0.0

    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    try:
        value = float(cleaned)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{text}' is not a number. Enter the total you want in {currency}, "
                "for example 3000000 or 3,000,000."
            ),
        ) from None

    if value <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"A target total has to be greater than zero. Got '{text}'.",
        )
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail=f"'{text}' is not a usable number.")
    return value


def _apply_target(estimate: Estimate, target: float | None) -> tuple[Estimate, bool, str]:
    """Land `estimate` on `target`, and report honestly what it hit.

    Shared by both endpoints: a target total means the same thing whether it was
    set on the first quotation or on a revision. The model re-scopes to roughly
    the figure and this settles the arithmetic exactly.

    The typed figure is read on the same basis as the rates. Tax-exclusive, it
    is the price of the work and the tax is added on top of it; inclusive, or
    with no tax at all, it is the total itself. Solving a net target as though
    it were the gross total quietly delivers the client less work than they
    asked to buy, by exactly the tax rate.

    Returns the estimate, whether the target was hit exactly, and a note that is
    empty unless something needs saying.
    """
    if target is None:
        return estimate, True, ""

    model_total = estimate.cost.total
    goal = gross_for_target(estimate, target)
    try:
        snapped = snap_to_total(estimate, goal)
    except CostingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    note = snapped.note

    # When tax was added on top, say so: the figure on the documents is not the
    # figure that was typed, and a reader deserves to see the arithmetic rather
    # than wonder which of the two numbers is wrong.
    cost = snapped.estimate.cost
    if goal != target and cost.tax_pct > 0:
        label = cost.tax_label or "tax"
        added = (
            f"The target of {target:,.2f} is the price of the work. With {label} at "
            f"{cost.tax_pct:g}% on top, the quotation totals {goal:,.2f}."
        )
        note = f"{added} {note}".strip() if note else added

    # A model that ignored the target leaves the arithmetic stretching a scope
    # that was never costed for the figure. Say so rather than shipping quietly.
    if model_total > 0:
        drift = goal / model_total
        if drift < 0.7 or drift > 1.4:
            stretch = (
                f"The scoped work came to {model_total:,.2f} and was scaled by {drift:.2f}x to "
                f"reach {goal:,.2f}. Check that the line items still describe work worth "
                f"that figure."
            )
            note = f"{note} {stretch}".strip() if note else stretch
            logger.warning("Estimate scaled by %.2fx to meet a target of %.2f", drift, goal)

    return snapped.estimate, snapped.exact, note


# --- Endpoints ---------------------------------------------------------------


@app.post("/api/proposals", response_model=jobs.JobView, status_code=202, tags=["proposals"])
async def create_proposal(
    brief: str = Form(...),
    kind: str = Form("software"),
    kind_label: str = Form(""),
    currency: str = Form("PHP"),
    client_name: str = Form(""),
    project_name: str = Form(""),
    market_region: str = Form("Philippines"),
    budget_hint: str = Form(""),
    timeline_hint: str = Form(""),
    target_total: str = Form(""),
    tax_inclusive: str = Form(""),
    tax_mode: str = Form(""),
    deposit_pct: str = Form(""),
    instalments: str = Form(""),
    payment_cadence: str = Form(""),
    deposit_trigger: str = Form(""),
    payment_schedule: str = Form(""),
    tiers: str = Form(""),
    tier_ceiling: str = Form(""),
    pricing_basis: str = Form(""),
    #: Which client intake this quotation is being prepared for, if any. Blank
    #: for every caller that predates intakes - the pad included, until Stage 2
    #: wires it through - and for the pad's own "quick quote" path, which has
    #: no client request behind it at all.
    intake_id: str = Form(""),
    images: List[Union[UploadFile, str]] = File(default=[]),
    documents: List[Union[UploadFile, str]] = File(default=[]),
) -> jobs.JobView:
    """One brief in, one job back.

    Preparing a quotation takes the better part of a minute and three tiers take
    a minute and a half, so the work happens behind the request. Everything that
    can be rejected - a bad currency, a schedule that does not total 100, a
    target above its ceiling - is still rejected here, synchronously, before a
    job exists. Only the part that takes time is deferred.

    `target_total` is optional and binding: the scope is written towards it and
    the arithmetic is then solved onto it exactly. `budget_hint` stays what it
    was - free text the model reasons about, with no arithmetic attached.
    """
    code = _normalise_currency(currency)
    request = ProposalRequest(
        brief=_normalise_brief(brief),
        currency=code,
        client_name=(client_name or "").strip(),
        project_name=(project_name or "").strip(),
        market_region=(market_region or "").strip() or "Philippines",
        budget_hint=_normalise_budget_hint(budget_hint),
        timeline_hint=(timeline_hint or "").strip(),
        target_total=_normalise_target(target_total, code),
        # Both are accepted: the pad sends the mode, and `tax_inclusive` is what
        # every earlier caller sends. ProposalRequest reconciles them, with the
        # mode winning when it is one of the three it knows.
        tax_mode=(tax_mode or "").strip().lower(),
        tax_inclusive=_normalise_flag(tax_inclusive),
        deposit_pct=_normalise_target(deposit_pct, code) if deposit_pct.strip() else 0.0,
        instalments=int(_normalise_target(instalments, code)) if instalments.strip() else 3,
        payment_cadence=(payment_cadence or "monthly").strip().lower(),
        deposit_trigger=(deposit_trigger or "").strip(),
        payment_schedule=(payment_schedule or "").strip(),
        tiers=(tiers or "").strip(),
        tier_ceiling=_normalise_target(tier_ceiling, code) if tier_ceiling.strip() else 0.0,
        pricing_basis=(pricing_basis or "rate_card").strip().lower(),
    )
    # The kind travels beside the request rather than on it: `ProposalRequest`
    # is what the work is priced from, and the discipline decides what the
    # second document says. `resolve` never raises, so an id nobody knows - or
    # none at all, from a caller older than kinds - takes the software path this
    # endpoint has always taken, rather than a 400 on a field nobody sent.
    kind_id = kinds.resolve(kind).id
    typed_kind = (kind_label or "").strip()
    target = request.target_total if request.target_total > 0 else None
    ceiling = request.tier_ceiling if request.tier_ceiling > 0 else 0.0
    if target and ceiling and target > ceiling:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The target cost of {target:,.2f} is above the {ceiling:,.2f} ceiling. "
                "A quotation cannot land on a figure it is not allowed to reach - raise the "
                "ceiling or lower the target."
            ),
        )
    studio = settings.load()
    # The card is the studio's default, but a quotation can be priced from the
    # work instead - a client outside the usual bands, a fixed-price bid, a job
    # somebody else is delivering. Choosing "requirements" takes the card out of
    # play entirely rather than half-applying it.
    card = studio.rate_card if request.pricing_basis != "requirements" else []
    card_text = ratecard.describe_for_prompt(card, request.currency)
    # The working day is stated per role inside the card itself now, so there
    # is no studio-wide sentence left to send.
    basis_text = ""
    terms = _payment_terms(request)
    terms_text = payments.describe_for_prompt(terms)
    attachments = await _read_images(images)
    # Read to text here, once, before any tier is generated: five tiers must
    # not mean opening the same PDF five times.
    papers = await _read_documents(documents)
    papers_text = attachments_module.describe_for_prompt(papers)

    tiers = _normalise_tiers(request.tiers)

    async def prepare(
        tier: str, index: int, above_total: float = 0.0, above_name: str = ""
    ) -> Estimate:
        # `_apply_request_identity` makes the request authoritative over the
        # project name, so asking the model to append the tier is not enough -
        # it would be overwritten. The name is built per tier here instead, and
        # only when the caller supplied one to build on.
        per_tier = request
        if tier and request.project_name.strip():
            per_tier = request.model_copy(
                update={"project_name": f"{request.project_name.strip()} - {tier}"}
            )
        spec = (
            prompts.TierSpec(
                name=tier,
                index=index,
                names=tiers,
                above_total=above_total,
                above_name=above_name,
            )
            if tiers
            else None
        )
        estimate = await generate_estimate(
            per_tier,
            attachments,
            card_text,
            basis_text,
            terms_text,
            not studio.show_contingency_to_client,
            spec,
            ceiling,
            documents_text=papers_text,
            kind=kind_id,
            # The studio's own word for the discipline. Without it, "something
            # else" is guidance telling the model to use a name it was never
            # given.
            kind_label=typed_kind,
        )
        # Stamped the moment it comes back, before the costing and the renderers
        # see it: the kind is what the studio chose, not something the model is
        # asked to agree with, and a model that answered "software" to an
        # accounting brief would otherwise decide which document gets written.
        # Every tier of a ladder is the same discipline - one brief, one kind.
        estimate.kind = kind_id
        estimate.kind_label = typed_kind
        return estimate

    job = jobs.create(
        kind="quotation",
        title=request.project_name or request.client_name or "Quotation",
        detail=" · ".join(
            part
            for part in (
                request.client_name if request.project_name else "",
                f"{len(tiers)} tiers: {', '.join(tiers)}" if tiers else "",
                f"Ceiling {ceiling:,.0f} {code}" if ceiling else "",
            )
            if part
        ),
        # In execution order, which is top-down: each tier is priced against the
        # figure the one above came back with, so the top is priced first. A
        # step list in ladder order would tick "Pricing Basic" while Extended
        # was the thing actually running.
        steps=[f"Pricing {tier}" for tier in reversed(tiers)] or ["Pricing the work"],
    )

    def stamp(to: str, **fields) -> None:
        """Move the intake this quotation came from, if it came from one.

        Never raises: a quotation is the thing being prepared, and losing the
        bookkeeping around it must not lose the quotation. An intake_id that
        does not resolve is a stale form, not a reason to refuse the work.
        Caught broadly rather than just `intakes.IntakeError`, because
        `intakes.advance` reaches `workspaces.root()` on its way to a file on
        disk and that can raise `NoWorkspace` or a bare `OSError` - neither is
        an `IntakeError`. A narrower catch would let either escape `stamp`
        itself, and the two call sites fail differently if it does: at the
        PREPARING call there is no enclosing `try` in `run()` yet, so `run()`
        would die before `jobs.start` ever executes and the job would sit at
        `queued` forever with no `jobs.fail` and no notification; at the
        QUOTED call, which runs after `jobs.finish` has already recorded
        success, it would fall into `run()`'s own `except Exception` and
        report a quotation that was saved as one that failed. `IntakeError`'s
        message is self-describing and logged as-is; anything else is logged
        with its type and stack, since a plain %s of those loses both.

        A move to `QUOTE_FAILED` is also told to the admins - a request that
        failed silently is one nobody comes back to. That announcement sits
        after this `try`, not inside it, and carries its own `except`: the
        client asked, and the quotation failed, whether or not the move above
        also landed, so it must not be skipped just because `advance` raised;
        and `intakes.get` below reaches `workspaces.root()` exactly as
        `advance` does, so it is exactly as capable of raising `NoWorkspace`
        - `inbox.notify` itself is verified never to (everything in it runs
        inside one broad `except Exception` that returns 0 rather than
        raise), but the lookup that builds its words is not, and this is
        still `stamp`: nothing past this line may reach `run()`.
        """
        if not intake_id:
            return
        try:
            intakes.advance(intake_id, to, **fields)
        except intakes.IntakeError as exc:
            logger.warning("Intake %s not moved to %s: %s", intake_id, to, exc)
        except Exception:
            logger.exception("Intake %s could not be moved to %s", intake_id, to)

        if to == intakes.QUOTE_FAILED:
            try:
                entry = intakes.get(intake_id)
                inbox.notify(
                    "intake.quote_failed",
                    inbox.ADMINS,
                    {
                        "title": "A client request could not be quoted",
                        "body": " - ".join(
                            part
                            for part in (
                                (entry.client_email if entry else ""),
                                str(fields.get("error", "")),
                            )
                            if part
                        ),
                        "href": "#/intakes",
                    },
                )
            except Exception:
                logger.exception(
                    "Intake %s: could not notify admins of a failed quote", intake_id
                )

    async def run() -> None:
        stamp(intakes.PREPARING, job_id=job.id)
        jobs.start(job.id, "Reading the brief")
        try:
            if tiers:
                # One call per tier, top down, one after another. They used to
                # run together - three tiers in the time of the slowest - and
                # the ladder came back in whatever order the model happened to
                # land in, because no call could see what any other had priced.
                # Twice in a row the middle tier undercut the entry tier.
                #
                # Each tier is now told what the tier above actually cost, which
                # only exists if the tier above has already been priced. That
                # costs the concurrency; background jobs are what makes the
                # trade affordable, and it is why they were built first.
                estimates = [None] * len(tiers)
                above_total = 0.0
                above_name = ""

                for position, index in enumerate(reversed(range(len(tiers)))):
                    tier = tiers[index]
                    estimate = await prepare(tier, index, above_total, above_name)
                    estimates[index] = estimate
                    # The anchor for the next tier down is what this one came
                    # back with. It is a raw figure - the rate card and the
                    # payment terms have not run yet - so it guides the model
                    # rather than binding it; `_finalise` does the binding, on
                    # the finished totals.
                    above_total = estimate.cost.total
                    above_name = tier
                    # Marked when the tier is genuinely back, so the bar reports
                    # work done rather than time passed.
                    jobs.step(job.id, position, f"{tier} priced")
            else:
                estimates = [await prepare("", 0)]
                jobs.step(job.id, 0, "Costing")

            jobs.stage(job.id, "Costing and writing the documents")
            bundles = _finalise(
                estimates, request, tiers, target, ceiling, studio, card, terms
            )
            jobs.finish(job.id, [bundle.id for bundle in bundles])
            stamp(
                intakes.QUOTED,
                bundle_ids=[bundle.id for bundle in bundles],
                priced_scope=brief,
                priced_budget=budget_hint,
            )
            first = bundles[0]
            # A quotation that had to be stretched, held down or could not
            # reach its target is still ready - and saying only "ready" would
            # be the silent correction this whole system exists to avoid.
            # These live on the bundle, not the estimate: they describe what the
            # server had to do to the quotation, not what it says.
            caveats = [note for note in (first.target_note, first.tier_cap_note) if note]
            if first.target_total and not first.hit_target and not caveats:
                caveats.append("the target total could not be reached exactly")
            inbox.notify(
                "quotation_ready",
                inbox.ACTOR,
                {
                    "title": (
                        ("Quotation ready" if len(bundles) == 1 else f"{len(bundles)} tiers ready")
                        + (" - worth a look" if caveats else "")
                    ),
                    "body": " · ".join(
                        part
                        for part in (
                            first.estimate.project_name,
                            first.estimate.client_name,
                            format_money(first.estimate.cost.total, first.estimate.currency),
                        )
                        if part
                    )
                    + (f" - {caveats[0]}" if caveats else ""),
                    "href": f"#/q/{first.id}",
                },
            )
        except GeminiConfigError as exc:
            logger.error("Generation blocked by configuration: %s", exc)
            jobs.fail(job.id, str(exc))
            stamp(intakes.QUOTE_FAILED, error=str(exc))
        except GeminiResponseError as exc:
            logger.error("Unusable Gemini response: %s | snippet=%s", exc, exc.snippet)
            jobs.fail(job.id, str(exc))
            stamp(intakes.QUOTE_FAILED, error=str(exc))
            inbox.notify(
                "quotation_failed",
                inbox.ACTOR,
                {
                    "title": "That quotation did not come back",
                    "body": f"{exc} Rephrase the brief and try again.",
                    "href": "#/pad",
                },
            )
        except HTTPException as exc:
            jobs.fail(job.id, str(exc.detail))
            stamp(intakes.QUOTE_FAILED, error=str(exc.detail))
            # Late by nature: the costing solver only runs after the model call,
            # so an unreachable target is a ninety-second answer to a number
            # somebody typed. Say which number, and where to change it.
            inbox.notify(
                "quotation_rejected",
                inbox.ACTOR,
                {
                    "title": "That quotation could not be priced as asked",
                    "body": str(exc.detail),
                    "href": "#/pad",
                },
            )
        except Exception:  # pragma: no cover - genuinely unexpected
            logger.exception("Unhandled failure while generating an estimate")
            jobs.fail(
                job.id,
                "The quotation could not be prepared. The error is in the API log.",
            )
            stamp(
                intakes.QUOTE_FAILED,
                error="The quotation could not be prepared. The error is in the API log.",
            )
            # Both, and in different words. The person waiting needs to know
            # their work is gone and that somebody has been told; the admins
            # need the part only they can act on.
            inbox.notify(
                "quotation_crashed",
                inbox.ACTOR,
                {
                    "title": "That quotation failed unexpectedly",
                    "body": "Nothing was saved. The admins have been told - try again.",
                    "href": "#/pad",
                },
            )
            inbox.notify(
                "quotation_crashed",
                inbox.ADMINS,
                {
                    "title": "A quotation failed unexpectedly",
                    "body": "The reason is in the API log. Nothing was saved.",
                    "href": "#/jobs",
                },
            )

    # Held so the loop cannot garbage-collect a running task out from under us.
    task = asyncio.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)

    return jobs.view(job)


def _finalise(
    estimates: List[Estimate],
    request: ProposalRequest,
    tiers: List[str],
    target: float | None,
    ceiling: float,
    studio,
    card,
    terms,
) -> List[ProposalBundle]:
    """Turn raw estimates into saved, costed, document-bearing bundles.

    Split out of the endpoint when generation moved to the background: the HTTP
    handler now returns a job before any of this runs, so everything that used
    to live after the Gemini call had to become something the worker can call.

    Tiers are finalised from the top down, and that order is the point. Every
    other pass - the rate card, the payment terms, the contingency, the target -
    moves the total, so the only figure a tier can honestly be held below is the
    *finished* total of the tier above it. The top tier is held to the studio
    cap; each tier under it is held below whatever the one above ended up at.
    The bundles still come back in ladder order.
    """
    group_id = storage.new_id() if tiers else ""
    prepared: dict[int, ProposalBundle] = {}

    # Top tier first when there is a ladder; a single quotation is its own top.
    order = list(reversed(range(len(estimates)))) if tiers else list(range(len(estimates)))
    # References are drawn in ladder order even though the tiers are finalised
    # top down, so Basic is the lower number. A client reading "Standard is
    # 0000001 and Basic is 0000002" would reasonably assume they arrived in that
    # order, and they did not.
    references = [
        reference.build(studio.reference_prefix, studio.reference_mode) for _ in estimates
    ]
    # The studio cap opens the chain. With no cap the top tier is unconstrained
    # and still sets the maximum for everything below it, which is how the order
    # holds even when nobody named a budget.
    cap = ceiling

    for index in order:
        estimate = estimates[index]
        # The card binds before costing: `recompute` derives every subtotal from
        # quantity x unit_rate, and `snap_to_total` only ever moves quantities,
        # so a rate set here is the rate the documents print.
        estimate, removed, bound, removed_value = ratecard.apply(estimate, card)
        estimate, terms_applied = payments.apply(estimate, terms)
        if not studio.show_contingency_to_client:
            estimate = absorb_contingency(estimate)
        estimate = recompute(estimate)
        estimate, hit_target, note = _apply_target(estimate, target)
        # The cap is enforced last, after every other pass has had its say. An
        # exact target is a stronger instruction than a maximum, so a quotation
        # with both is already at its target and left alone - and a ladder built
        # onto one target total is a ladder of identical figures, which is why a
        # target switches the chain off rather than fighting it.
        is_top = not tiers or index == len(estimates) - 1
        # The studio's cap is typed on the same basis as the rates, so it needs
        # the same reading as a target: exclusive of tax, the tax goes on top of
        # it. The caps further down the ladder are finished totals of the tier
        # above and are already gross - converting those would raise every lower
        # tier by the tax rate and break the order this chain exists to keep.
        effective_cap = 0.0 if target else (gross_for_target(estimate, cap) if is_top else cap)
        estimate, cap_applied, cap_note = _apply_ceiling(estimate, effective_cap)

        # For the top tier the cap is the studio ceiling and being brought down
        # to it is the ceiling doing its job. For the others it is the tier
        # above, and being brought down to it is the ladder being put in order.
        ceiling_applied = cap_applied and is_top
        order_enforced = cap_applied and not is_top

        if tiers and not target:
            step = 10.0 ** -money_decimals(estimate.currency)
            # Strictly below, by one minor unit. No invented margin: a
            # percentage gap would be money removed from a client's quotation on
            # a rule nobody agreed to. Separation between tiers is the model's
            # job, done through scope; this only guarantees the order.
            cap = max(estimate.cost.total - step, step)

        # The reference the documents print. Taken once and stored on the
        # estimate: a number that changed between the markdown and the PDF would
        # not be a reference at all. Tiers each take their own, so three
        # quotations from one brief are three numbers rather than one repeated.
        estimate.quotation_ref = references[index]

        proposal_id = storage.new_id()
        bundle = ProposalBundle(
            id=proposal_id,
            created_at=storage.utc_now_iso(),
            estimate=estimate,
            files=_build_files(proposal_id, estimate),
            revision=1,
            root_id=proposal_id,
            target_total=target or 0.0,
            hit_target=hit_target,
            target_note=note,
            rate_card_bound=bound,
            rate_card_removed=removed,
            rate_card_removed_value=removed_value,
            payment_terms=PaymentTermsRecord(**terms.model_dump()),
            payment_terms_applied=terms_applied,
            tier_ceiling=ceiling,
            ceiling_applied=ceiling_applied,
            tier_cap=effective_cap,
            tier_order_enforced=order_enforced,
            tier_cap_note=cap_note,
            priced_from_rate_card=bool(card),
            tier_group_id=group_id,
            tier_name=tiers[index] if tiers else "",
            tier_index=index,
        )
        storage.save(bundle)
        prepared[index] = bundle

        logger.info(
            "Quotation %s prepared%s: %s %.2f across %d line items, target=%s, exact=%s, "
            "cap=%s, order_enforced=%s",
            proposal_id,
            f" [{bundle.tier_name}]" if bundle.tier_name else "",
            estimate.currency,
            estimate.cost.total,
            len(estimate.line_items),
            f"{target:.2f}" if target is not None else "none",
            hit_target,
            f"{effective_cap:.2f}" if effective_cap else "none",
            order_enforced,
        )

    # Finalised top-down, returned in ladder order: everything downstream - the
    # tier switcher, the sibling list, the job's result_ids - reads left to
    # right the way the client does.
    return [prepared[index] for index in sorted(prepared)]


@app.post(
    "/api/proposals/{proposal_id}/revise",
    response_model=jobs.JobView,
    status_code=202,
    tags=["proposals"],
)
async def revise_proposal(
    proposal_id: str,
    instruction: str = Form(""),
    target_total: str = Form(""),
) -> jobs.JobView:
    """Re-scope an existing quotation, optionally onto an exact total.

    A revision is a new bundle with a new id, not an edit. The parent is left
    untouched so a sent quotation stays exactly as it was sent, and the two can
    be compared row by row.

    The currency is inherited from the parent. There is no way to change it
    here, because re-pricing the same work in another currency is an exchange
    rate by another name and this system never converts.
    """
    parent = _require_bundle(proposal_id)
    request = RevisionRequest(
        instruction=_normalise_instruction(instruction),
        target_total=_normalise_target(target_total, parent.estimate.currency),
    )
    target = request.target_total if request.target_total > 0 else None

    if not request.instruction and target is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Say what to change, or set a target total. A revision with neither "
                "would just reprice the same brief."
            ),
        )

    job = jobs.create(
        kind="revision",
        title=f"Revision of {parent.estimate.project_name or parent.id}",
        detail=request.instruction[:120] or (f"Target {target:,.2f}" if target else ""),
        steps=["Re-scoping the work", "Costing and writing the documents"],
    )

    async def run() -> None:
        jobs.start(job.id, "Reading the quotation")
        try:
            studio = settings.load()
            card_text = ratecard.describe_for_prompt(studio.rate_card, parent.estimate.currency)
            basis_text = ""
            terms = payments.PaymentTerms(**parent.payment_terms.model_dump())
            terms_text = payments.describe_for_prompt(terms)

            revised = await revise_estimate(
                parent.estimate,
                request.instruction,
                target,
                card_text,
                basis_text,
                terms_text,
                # The discipline goes to the model as well as onto the estimate.
                # Set only afterwards, it would re-scope an accounting engagement
                # as software and hand back a document with its sections empty.
                kind=parent.estimate.kind,
                kind_label=parent.estimate.kind_label,
            )
            jobs.step(job.id, 0, "Costing and writing the documents")

            # The kind is inherited and cannot be changed by a revision. It is
            # what the document was written as, and re-scoping is an argument
            # about scope and price - an instruction that turned an accounting
            # engagement into a software one would be a different quotation
            # wearing the same reference.
            revised.kind = parent.estimate.kind
            revised.kind_label = parent.estimate.kind_label

            revised, removed, bound, removed_value = ratecard.apply(revised, studio.rate_card)
            # Terms are inherited, not re-asked: a revision that quietly changed
            # the payment schedule the client has already seen would be a
            # different offer.
            revised, terms_applied = payments.apply(revised, terms)
            if not studio.show_contingency_to_client:
                revised = absorb_contingency(revised)
            revised = recompute(revised)
            revised, hit_target, note = _apply_target(revised, target)

            revision_id = storage.new_id()
            revision_number = parent.revision + 1
            # A revision keeps the number the client already has, marked as a
            # later version of it. Issuing a fresh reference would leave two
            # unrelated-looking quotations for one conversation.
            parent_ref = (parent.estimate.quotation_ref or "").split(" R")[0].strip()
            revised.quotation_ref = (
                f"{parent_ref} R{revision_number}"
                if parent_ref
                else reference.build(studio.reference_prefix, studio.reference_mode)
            )
            bundle = ProposalBundle(
                id=revision_id,
                created_at=storage.utc_now_iso(),
                estimate=revised,
                files=_build_files(revision_id, revised, revision_number),
                revision=revision_number,
                parent_id=parent.id,
                root_id=parent.root_id or parent.id,
                revision_instruction=request.instruction,
                target_total=target or 0.0,
                hit_target=hit_target,
                target_note=note,
                rate_card_bound=bound,
                rate_card_removed=removed,
                rate_card_removed_value=removed_value,
                payment_terms=PaymentTermsRecord(**terms.model_dump()),
                payment_terms_applied=terms_applied,
                # A revision stays in whatever tier group its parent belongs to.
                tier_group_id=parent.tier_group_id,
                tier_name=parent.tier_name,
                tier_index=parent.tier_index,
            )
            storage.save(bundle)

            logger.info(
                "Revision %s of %s (r%d) prepared: %s %.2f, target=%s, exact=%s",
                revision_id,
                parent.id,
                revision_number,
                revised.currency,
                revised.cost.total,
                f"{target:.2f}" if target is not None else "none",
                hit_target,
            )
            jobs.finish(job.id, [bundle.id])
            inbox.notify(
                "revision_ready",
                inbox.ACTOR,
                {
                    "title": "Revision ready",
                    "body": " · ".join(
                        part
                        for part in (
                            bundle.estimate.quotation_ref,
                            bundle.estimate.project_name,
                            format_money(bundle.estimate.cost.total, bundle.estimate.currency),
                        )
                        if part
                    ),
                    "href": f"#/q/{bundle.id}",
                },
            )
        except GeminiConfigError as exc:
            logger.error("Revision blocked by configuration: %s", exc)
            jobs.fail(job.id, str(exc))
        except GeminiResponseError as exc:
            logger.error("Unusable Gemini revision: %s | snippet=%s", exc, exc.snippet)
            jobs.fail(job.id, str(exc))
            # Worth saying plainly: the original is deliberately untouched, so
            # silence would leave somebody believing a revised version exists
            # when only the original does.
            inbox.notify(
                "revision_failed",
                inbox.ACTOR,
                {
                    "title": "That revision did not come back",
                    "body": f"{exc} The original quotation is untouched.",
                    "href": "#/quotations",
                },
            )
        except HTTPException as exc:
            jobs.fail(job.id, str(exc.detail))
        except Exception:  # pragma: no cover - genuinely unexpected
            logger.exception("Unhandled failure while revising an estimate")
            jobs.fail(job.id, "The revision could not be prepared. The error is in the API log.")

    task = asyncio.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)

    return jobs.view(job)


def _summarise(bundle: ProposalBundle) -> ProposalSummary:
    estimate = bundle.estimate
    base = f"/api/proposals/{bundle.id}/files"
    return ProposalSummary(
        id=bundle.id,
        created_at=bundle.created_at,
        quotation_ref=quotation_reference(estimate),
        project_name=estimate.project_name,
        client_name=estimate.client_name,
        currency=estimate.currency,
        total=estimate.cost.total,
        line_items=len(estimate.line_items),
        revision=bundle.revision,
        parent_id=bundle.parent_id,
        root_id=bundle.root_id or bundle.id,
        target_total=bundle.target_total,
        hit_target=bundle.hit_target,
        tax_label=estimate.cost.tax_label,
        tax_pct=estimate.cost.tax_pct,
        tax_inclusive=estimate.cost.tax_inclusive,
        tier_group_id=bundle.tier_group_id,
        tier_name=bundle.tier_name,
        proposal_url=f"{base}/proposal.md",
        requirements_url=f"{base}/requirements.md",
    )


@app.get("/api/proposals", response_model=List[ProposalSummary], tags=["admin"])
async def list_proposals(
    q: str = "",
    limit: int = 200,
) -> List[ProposalSummary]:
    """Every quotation on disk, newest first.

    `q` matches the project, the client, the printed reference or the id,
    case-insensitively - enough to find one quotation among a few hundred
    without a search index. The reference is included because it is the number
    on the document, and so the one somebody is holding when they search.
    """
    needle = (q or "").strip().lower()
    bounded = max(1, min(int(limit or 200), 1000))

    rows = [_summarise(bundle) for bundle in storage.all_bundles()]
    if needle:
        rows = [
            row
            for row in rows
            if needle in row.project_name.lower()
            or needle in row.client_name.lower()
            or needle in row.quotation_ref.lower()
            or needle in row.id.lower()
        ]
    return rows[:bounded]


@app.get("/api/proposals/{proposal_id}", response_model=ProposalBundle, tags=["proposals"])
async def get_proposal(proposal_id: str) -> ProposalBundle:
    return _with_parent_ref(_with_siblings(_require_bundle(proposal_id)))


@app.delete("/api/proposals/{proposal_id}", tags=["admin"])
async def delete_proposal(proposal_id: str) -> Response:
    """Delete one quotation and both of its documents. Not recoverable.

    Revisions are independent bundles, so deleting a parent leaves its revisions
    intact and their `parent_id` pointing at an id that no longer resolves. That
    is deliberate - a sent revision should not disappear because the draft it
    came from was tidied away.
    """
    if not storage.is_valid_id(proposal_id):
        raise HTTPException(status_code=404, detail=f"No quotation with id {proposal_id!r}.")
    if not storage.delete(proposal_id):
        raise HTTPException(status_code=404, detail=f"No quotation with id {proposal_id!r}.")
    return Response(status_code=204)


@app.get("/api/proposals/{proposal_id}/files/{kind}.md", tags=["files"])
async def download_markdown(proposal_id: str, kind: str) -> Response:
    bundle = _require_bundle(proposal_id)
    generated = storage.file_for(bundle, _require_kind(kind))
    if generated is None:
        raise HTTPException(status_code=404, detail=f"This quotation has no '{kind}' document.")

    return Response(
        content=generated.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{generated.filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/proposals/{proposal_id}/files/{kind}.html", tags=["files"])
async def printable_html(proposal_id: str, kind: str) -> HTMLResponse:
    """Print-ready HTML. The browser's own print dialog is the PDF exporter."""
    bundle = _require_bundle(proposal_id)
    kind = _require_kind(kind)
    markdown = storage.markdown_for(bundle, kind)
    if markdown is None:
        raise HTTPException(status_code=404, detail=f"This quotation has no '{kind}' document.")

    # `kind` is authoritative. Left to infer the sheet from the title, the
    # renderer matches on words like "developer" or "requirement", so a project
    # merely *named* one of those would print the client proposal as the
    # developer duplicate.
    html = render_print_html(
        markdown,
        _document_title(bundle.estimate, kind),
        bundle.estimate,
        kind=kind,
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/api/proposals/{proposal_id}/files/{kind}.pdf", tags=["files"])
async def download_pdf(proposal_id: str, kind: str) -> Response:
    """The document as a PDF file, for attaching to an email.

    Rendered from the same markdown as every other version of it, so there is
    one source of content and no chance of the PDF saying something the web
    page does not.
    """
    bundle = _require_bundle(proposal_id)
    kind = _require_kind(kind)
    markdown = storage.markdown_for(bundle, kind)
    if markdown is None:
        raise HTTPException(status_code=404, detail=f"This quotation has no '{kind}' document.")

    try:
        data = render_pdf(
            markdown,
            _document_title(bundle.estimate, kind),
            bundle.estimate,
            kind=kind,
        )
    except Exception as exc:  # pragma: no cover - a renderer bug, not user input
        logger.exception("PDF rendering failed for %s/%s", proposal_id, kind)
        raise HTTPException(
            status_code=500,
            detail=(
                "The PDF could not be produced. The error is in the API log; the "
                "markdown and print views of this quotation still work."
            ),
        ) from exc

    filename = _filename(bundle.estimate, kind, bundle.revision).removesuffix(".md") + ".pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/jobs", response_model=List[jobs.JobView], tags=["jobs"])
async def list_jobs(limit: int = 50) -> List[jobs.JobView]:
    """Everything prepared or preparing, newest first."""
    return jobs.listing(limit)


@app.get("/api/jobs/{job_id}", response_model=jobs.JobView, tags=["jobs"])
async def get_job(job_id: str) -> jobs.JobView:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"No job with the reference {job_id!r}.",
        )
    return jobs.view(job)


def _with_reference_preview(defaults: settings.StudioDefaults) -> settings.StudioDefaults:
    """Attach what the next quotation number will look like.

    Read-only and computed on the way out, so the panel can show the studio its
    own numbering without taking a number from the counter to do it.
    """
    return defaults.model_copy(
        update={
            "reference_preview": reference.preview(
                defaults.reference_prefix, defaults.reference_mode
            ),
            "proposal_reference_preview": reference.preview(
                defaults.proposal_prefix,
                defaults.proposal_reference_mode,
                series=reference.PROPOSALS,
            )
        }
    )


# --- Proposals built from a quotation ----------------------------------------


def _document_files(document: "ProposalDocument", markdown: str) -> List[GeneratedFile]:
    base = f"/api/documents/{document.id}/files"
    stem = _slug(document.project_name or document.id, "proposal")
    return [
        GeneratedFile(
            # Not "proposal": that token already means "the client half of a
            # quotation" throughout this API, down to the filename helper that
            # turns it into "-quotation.md".
            kind="proposal-document",
            filename=f"{stem}-proposal.md",
            markdown=markdown,
            download_url=f"{base}/proposal.md",
            print_url=f"{base}/proposal.html",
            pdf_url=f"{base}/proposal.pdf",
        )
    ]


def _summarise_document(document: ProposalDocument) -> ProposalDocumentSummary:
    return ProposalDocumentSummary(
        id=document.id,
        created_at=document.created_at,
        quotation_id=document.quotation_id,
        quotation_ref=document.quotation_ref,
        reference=document.reference,
        title=document.title,
        client_name=document.client_name,
        project_name=document.project_name,
        currency=document.currency,
        total=document.total,
        policy_count=len(document.policies),
    )


def _require_document(document_id: str) -> ProposalDocument:
    document = documents.get(document_id)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=f"No proposal with the reference {document_id!r}.",
        )
    return document


def _document_markdown(document: ProposalDocument) -> str:
    """The proposal's markdown, re-rendered from its own snapshot if need be."""
    for generated in document.files:
        if generated.kind == "proposal-document" and generated.markdown.strip():
            return generated.markdown

    bundle = storage.get(document.quotation_id)
    if bundle is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This proposal has no stored markdown and the quotation it was built from is "
                "no longer on file, so it cannot be re-rendered. Build it again."
            ),
        )
    return render_proposal(document, bundle.estimate)


def _document_estimate(document: ProposalDocument) -> Estimate:
    """The quotation this was built from, or an empty one if it has been deleted.

    The markdown is already written and carries every figure, so a missing
    quotation costs the print view its letterhead detail rather than the
    document itself.
    """
    bundle = storage.get(document.quotation_id)
    return bundle.estimate if bundle is not None else Estimate()


@app.post("/api/documents", response_model=jobs.JobView, status_code=202, tags=["documents"])
async def build_document(quotation_id: str = Form(...)) -> jobs.JobView:
    """Build a proposal from one quotation, exactly as that quotation stands.

    Tiers are not merged and revisions are not followed: whichever bundle id is
    sent is the one written up. A studio selling the Standard tier sends
    Standard. Rebuilding produces a new document with its own id rather than
    editing this one - a proposal that has been sent must not change afterwards.

    Answers with a job, like every other Gemini pass in this API.
    """
    bundle = _require_bundle(quotation_id)
    studio = settings.load()
    clauses = policies.resolve(studio.policies)
    sections = template.resolve(studio.proposal_sections)
    look = design.resolve(studio.proposal_design)

    job = jobs.create(
        kind="proposal",
        title=f"Proposal for {bundle.estimate.project_name or bundle.id}",
        detail=" \u00b7 ".join(
            part
            for part in (
                bundle.estimate.client_name,
                bundle.estimate.quotation_ref,
                f"{len(clauses)} clauses",
            )
            if part
        ),
        steps=["Writing the proposal", "Assembling the document"],
    )

    async def run() -> None:
        jobs.start(job.id, "Reading the quotation")
        try:
            narrative = await generate_proposal(
                bundle.estimate,
                studio.studio_name,
                [clause.title for clause in clauses],
            )
            jobs.step(job.id, 0, "Assembling the document")

            document = ProposalDocument(
                id=storage.new_id(),
                created_at=storage.utc_now_iso(),
                quotation_id=bundle.id,
                quotation_ref=bundle.estimate.quotation_ref,
                # Taken from the studio's proposal series the moment the
                # document exists. A number recomputed at render time would
                # change every time somebody opened the thing they had sent.
                reference=reference.build(
                    studio.proposal_prefix,
                    studio.proposal_reference_mode,
                    series=reference.PROPOSALS,
                ),
                quotation_issued_at=bundle.created_at,
                title=narrative.title or bundle.estimate.project_name,
                client_name=bundle.estimate.client_name,
                project_name=bundle.estimate.project_name,
                currency=bundle.estimate.currency,
                total=bundle.estimate.cost.total,
                studio_name=studio.studio_name,
                signatory=studio.proposal_signatory,
                signatory_title=studio.proposal_signatory_title,
                narrative=narrative,
                # A snapshot, not a reference. A proposal sent in March says
                # what it said in March, whatever Settings looks like in April.
                policies=[
                    PolicyRecord(id=clause.id, title=clause.title, body=clause.body)
                    for clause in clauses
                ],
                # The template is snapshotted for the same reason the terms are:
                # a document that has been sent keeps the shape it was sent in.
                sections=[
                    SectionRecord(id=section.id, heading=section.heading)
                    for section in sections
                ],
                # And the look, for the third time the same reason: a studio
                # that rebrands in April has not restyled what it sent in March.
                design=look,
            )
            document.files = _document_files(
                document, render_proposal(document, bundle.estimate)
            )
            documents.save(document)
            jobs.step(job.id, 1, "Ready")
            jobs.finish(job.id, [document.id])
            inbox.notify(
                "proposal_ready",
                inbox.ACTOR,
                {
                    "title": "Proposal ready to send",
                    "body": " · ".join(
                        part
                        for part in (
                            document.title or document.project_name,
                            document.client_name,
                            f"{len(document.policies)} clauses",
                        )
                        if part
                    ),
                    "href": f"#/p/{document.id}",
                },
            )
        except GeminiConfigError as exc:
            logger.error("Proposal blocked by configuration: %s", exc)
            jobs.fail(job.id, str(exc))
        except GeminiResponseError as exc:
            logger.error("Unusable proposal response: %s | snippet=%s", exc, exc.snippet)
            jobs.fail(job.id, str(exc))
            inbox.notify(
                "proposal_failed",
                inbox.ACTOR,
                {
                    "title": "That proposal was not built",
                    "body": f"{exc} The quotation behind it is unchanged.",
                    "href": "#/proposals",
                },
            )
        except Exception:  # pragma: no cover - genuinely unexpected
            logger.exception("Unhandled failure while building a proposal")
            jobs.fail(job.id, "The proposal could not be built. The error is in the API log.")

    task = asyncio.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)

    return jobs.view(job)


@app.get("/api/documents", response_model=List[ProposalDocumentSummary], tags=["documents"])
async def list_documents(limit: int = 100) -> List[ProposalDocumentSummary]:
    """Every proposal built, newest first."""
    return [_summarise_document(document) for document in documents.listing(limit)]


@app.get("/api/documents/{document_id}", response_model=ProposalDocument, tags=["documents"])
async def read_document(document_id: str) -> ProposalDocument:
    return _require_document(document_id)


@app.delete("/api/documents/{document_id}", status_code=204, tags=["documents"])
async def delete_document(document_id: str) -> Response:
    if not documents.delete(document_id):
        raise HTTPException(
            status_code=404, detail=f"No proposal with the reference {document_id!r}."
        )
    return Response(status_code=204)


@app.get("/api/documents/{document_id}/files/proposal.md", tags=["documents"])
async def download_document_markdown(document_id: str) -> Response:
    document = _require_document(document_id)
    stem = _slug(document.project_name or document.id, "proposal")
    return Response(
        content=_document_markdown(document),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-proposal.md"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/documents/{document_id}/files/proposal.html", tags=["documents"])
async def print_document(document_id: str) -> HTMLResponse:
    document = _require_document(document_id)
    html = render_print_html(
        _document_markdown(document),
        f"{document.title or document.project_name} \u00b7 Proposal",
        _document_estimate(document),
        kind="proposal",
        # The client signs this page. It carries the studio's name, not the name
        # of the tool that produced it, and it is not titled as a quotation.
        brand=document.studio_name or "",
        doc_label="Proposal",
        reference_label="Proposal no." if document.reference else "Quotation ref.",
        reference_text=document.reference,
        cover_break=True,
        # The look it was built with, not the look Settings has today.
        design=document.design,
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/api/documents/{document_id}/files/proposal.pdf", tags=["documents"])
async def download_document_pdf(document_id: str) -> Response:
    document = _require_document(document_id)
    try:
        data = render_pdf(
            _document_markdown(document),
            f"{document.title or document.project_name} \u00b7 Proposal",
            _document_estimate(document),
            kind="proposal",
            doc_label="Proposal",
            cover_break=True,
            design=document.design,
        )
    except Exception as exc:  # pragma: no cover - a renderer bug, not user input
        logger.exception("PDF rendering failed for proposal %s", document_id)
        raise HTTPException(
            status_code=500,
            detail=(
                "The PDF could not be produced. The error is in the API log; the markdown and "
                "print views of this proposal still work."
            ),
        ) from exc

    stem = _slug(document.project_name or document.id, "proposal")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-proposal.pdf"',
            "Cache-Control": "no-store",
        },
    )


def _tell_the_team_what_changed(was, now) -> None:
    """Say what a save actually changed, to the people it changes things for.

    Only the settings that reach a document: rates bind and are enforced,
    numbering is what a client quotes back, terms are printed word for word.
    The rest - a market region, a contingency preference - is somebody tidying,
    and telling a team about tidying is how a feed stops being read.

    An admin gets a link to Settings. A member gets the same news and no link,
    because that door would refuse them.
    """
    changes: List[str] = []

    def card(defaults):
        return [
            (entry.role.strip().lower(), entry.unit, round(entry.rate, 2))
            for entry in defaults.rate_card or []
        ]

    if card(was) != card(now):
        count = len(now.rate_card or [])
        changes.append(
            f"the rate card ({count} {'role' if count == 1 else 'roles'} in {now.currency})"
        )
    if len(was.policies or []) != len(now.policies or []) or [
        (clause.title, clause.body) for clause in was.policies or []
    ] != [(clause.title, clause.body) for clause in now.policies or []]:
        changes.append("the proposal terms")
    if (was.reference_prefix, was.reference_mode) != (now.reference_prefix, now.reference_mode):
        changes.append("how quotations are numbered")
    if (was.tax_mode, was.tax_inclusive) != (now.tax_mode, now.tax_inclusive):
        changes.append("the tax basis")

    if not changes:
        return

    what = changes[0] if len(changes) == 1 else ", ".join(changes[:-1]) + " and " + changes[-1]
    who = (inbox.identity()[0] or "Somebody").split("@")[0]

    inbox.notify(
        "settings_changed",
        inbox.TEAM,
        lambda role, you: {
            "title": (
                f"You changed {what}" if you else f"{who} changed {what}"
            ),
            "body": "Quotations prepared from now on use it. Anything already on file is unchanged.",
            "href": "#/settings" if role == members.ADMIN else "",
        },
    )


@app.get("/api/settings", response_model=settings.StudioDefaults, tags=["admin"])
async def get_settings() -> settings.StudioDefaults:
    return _with_reference_preview(settings.load())


@app.put("/api/settings", response_model=settings.StudioDefaults, tags=["admin"])
async def put_settings(defaults: settings.StudioDefaults) -> settings.StudioDefaults:
    """Set what a new brief form opens with.

    These prefill the form and nothing else. They are not sent to the model and
    they do not override its judgement - see the module docstring in
    app/settings.py for what is deliberately absent and why.
    """
    code = _normalise_currency(defaults.currency)
    if code not in _CURRENCY_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{code}' is not one of the currencies PRISM offers. "
                f"Pick one of: {', '.join(sorted(_CURRENCY_CODES))}."
            ),
        )
    defaults.currency = code

    # Read before the save, because save() replaces the cache first and the
    # question worth answering is what CHANGED - "settings were saved" is a
    # notification about a button press, which nobody needs.
    was = settings.load()

    try:
        saved = settings.save(defaults)
    except OSError as exc:
        logger.error("Could not write studio defaults: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "The defaults could not be written to disk. Check that "
                "backend/generated is writable, then save again."
            ),
        ) from exc

    _tell_the_team_what_changed(was, saved)
    return _with_reference_preview(saved)





@app.get("/api/currencies", response_model=List[CurrencyOption], tags=["reference"])
async def list_currencies() -> List[CurrencyOption]:
    return CURRENCIES


class WorkspaceView(BaseModel):
    """One workspace, with enough about it to choose between two."""

    id: str
    name: str = ""
    created_at: str = ""
    quotations: int = 0
    proposals: int = 0
    studio_name: str = Field(
        default="", description="What this workspace's own settings call the studio."
    )


class WorkspaceRequest(BaseModel):
    name: str = ""


def _require_admin_of(request: Request, workspace_id: str) -> None:
    """Refuse anyone who is not an admin of *that* workspace.

    Deliberately not the role in whichever workspace happens to be open: being
    an admin of your own book says nothing about somebody else's.
    """
    if not auth.required():
        return
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")

    borrowed = workspaces.borrow(workspace_id)
    try:
        roster = members.listing()
        role = members.role_of(user.email, user.id)
    finally:
        workspaces.give_back(borrowed)

    if roster and role != members.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only an admin of that workspace can rename or delete it.",
        )


def _view_workspace(workspace: workspaces.Workspace) -> WorkspaceView:
    """Count what is in a workspace by looking, without loading any of it.

    Borrowed rather than switched: this runs inside a request that has already
    named its own workspace, and putting the context back exactly as it was is
    the only way that request survives being listed against.
    """
    token = workspaces.borrow(workspace.id)
    try:
        root = workspaces.root()
        quotations = sum(
            1 for child in root.iterdir() if child.is_dir() and storage.is_valid_id(child.name)
        )
        built = root / documents.DIRNAME
        proposals = len(list(built.glob("*.json"))) if built.is_dir() else 0
        studio = settings.load().studio_name
    finally:
        workspaces.give_back(token)

    return WorkspaceView(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        quotations=quotations,
        proposals=proposals,
        studio_name=studio,
    )


@app.get("/api/workspaces", response_model=List[WorkspaceView], tags=["workspaces"])
async def list_workspaces(request: Request) -> List[WorkspaceView]:
    """The workspaces you are on, oldest first.

    Not every workspace on the install: a team you have not been invited to is
    not a locked door you can see, it is a door you cannot see. An unclaimed
    workspace is listed for everyone, because it belongs to nobody until its
    first visitor claims it - that is what makes a fresh install usable.
    """
    user = getattr(request.state, "user", None)
    found = []
    for workspace in workspaces.listing():
        if user is not None:
            borrowed = workspaces.borrow(workspace.id)
            try:
                roster = members.listing()
                yours = not roster or members.is_member(user.email, user.id)
            finally:
                workspaces.give_back(borrowed)
            if not yours:
                continue
        found.append(_view_workspace(workspace))
    return found


@app.post("/api/workspaces", response_model=WorkspaceView, status_code=201, tags=["workspaces"])
async def create_workspace(request: Request, body: WorkspaceRequest) -> WorkspaceView:
    """Start a new book: its own settings, rates, terms, numbering and files.

    Whoever makes it administers it. Anyone signed in may make one - it is their
    own team, not a change to somebody else's.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A workspace needs a name.")

    made = workspaces.create(name)
    user = getattr(request.state, "user", None)
    # No notification here: you are the only person in a workspace you just
    # made, and telling somebody what they did one second ago is noise.
    if user is not None:
        borrowed = workspaces.borrow(made.id)
        try:
            members.claim(user.email, user.id)
        finally:
            workspaces.give_back(borrowed)
    return _view_workspace(made)


@app.patch("/api/workspaces/{workspace_id}", response_model=WorkspaceView, tags=["workspaces"])
async def rename_workspace(
    request: Request, workspace_id: str, body: WorkspaceRequest
) -> WorkspaceView:
    """Rename a workspace. Its id, and so everything filed under it, is unchanged."""
    _require_admin_of(request, workspace_id)
    renamed = workspaces.rename(workspace_id, body.name)
    if renamed is None:
        raise HTTPException(status_code=404, detail=f"No workspace called {workspace_id!r}.")
    return _view_workspace(renamed)


@app.delete("/api/workspaces/{workspace_id}", status_code=204, tags=["workspaces"])
async def delete_workspace(request: Request, workspace_id: str) -> Response:
    """Delete a workspace and every quotation, proposal and setting in it.

    Not recoverable, and the last one goes the same way as the rest: an install
    with no workspace is a valid state, and the client asks for a name before
    anything can be filed again.
    """
    if not workspaces.exists(workspace_id):
        raise HTTPException(status_code=404, detail=f"No workspace called {workspace_id!r}.")
    _require_admin_of(request, workspace_id)

    # Work in flight is written when it finishes, not now. Deleting the folder
    # under a running job leaves it to recreate the directory on its way out and
    # file a quotation into a workspace that no longer exists - visible to
    # nobody, deletable by nobody.
    token = workspaces.borrow(workspace_id)
    try:
        busy = [job for job in jobs.listing(200) if job.state in {"queued", "running"}]
    finally:
        workspaces.give_back(token)
    if busy:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(busy)} job(s) are still running in this workspace. Wait for them to "
                "finish, then delete it."
            ),
        )

    if not workspaces.delete(workspace_id):
        raise HTTPException(status_code=404, detail=f"No workspace called {workspace_id!r}.")
    return Response(status_code=204)


class IntakeRequest(BaseModel):
    """What the studio heard from the client, plus how it should be quoted."""

    client_email: str = ""
    client_phone: str = ""
    scope: str = ""
    budget_text: str = ""
    preset: dict = Field(default_factory=dict)


@app.post("/api/intakes", response_model=intakes.Intake, status_code=201, tags=["intakes"])
async def create_intake(request: Request, body: IntakeRequest) -> intakes.Intake:
    """Record a client request. Admin-only: an intake is the start of a price,
    and issuing one is nearer to inviting somebody than to drafting a
    quotation. This is deliberate rather than incidental - the gate's
    member/admin split (`_gate`, main.py:311-319) only blocks a member's
    POST to `/api/settings` and `/api/team`, so without this call a member's
    POST here would go through unchecked."""
    _require_admin(request, "Only an admin of this workspace can record a client request.")
    if not body.scope.strip():
        raise HTTPException(status_code=422, detail="A request needs a scope.")
    scope = _normalise_scope(body.scope)
    budget_text = _normalise_budget_text(body.budget_text)
    try:
        return intakes.create(
            client_email=body.client_email,
            client_phone=body.client_phone,
            scope=scope,
            budget_text=budget_text,
            preset=body.preset,
            created_by=_who_email(request),
        )
    except intakes.IntakeError as exc:
        # A brand-new intake has no existing state to conflict with - the only
        # way `create()` raises is that the record could not be written (a
        # full disk, a permissions problem). That is ours to report, not a
        # 409 implying the caller sent something to fix and retry.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intakes", response_model=List[intakes.Intake], tags=["intakes"])
async def list_intakes() -> List[intakes.Intake]:
    """The queue, newest first. Scoped to this workspace like everything else.

    No admin check: a member is exactly who turns a request into a
    quotation, so seeing the queue is theirs by default like the rest of
    `/api/`, and only admitting a new one is set apart above."""
    return intakes.listing()


@app.get("/api/intakes/{intake_id}", response_model=intakes.Intake, tags=["intakes"])
async def read_intake(intake_id: str) -> intakes.Intake:
    """One request. Open to members for the same reason the queue is; a
    malformed or unknown id reads as a plain 404 because `intakes.get`
    validates the id itself before any path is built."""
    entry = intakes.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    return entry


@app.post("/api/intakes/{intake_id}/close", response_model=intakes.Intake, tags=["intakes"])
async def close_intake(request: Request, intake_id: str) -> intakes.Intake:
    """Not going ahead. Reversible only by making a new request. Admin-only,
    the same side of the line as issuing one in the first place."""
    _require_admin(request, "Only an admin of this workspace can close a client request.")
    # Checked here rather than folded into the `except` below: `close()` raises
    # `IntakeError` both for an id that does not exist and for a write that
    # failed after it found one, and those are not the same answer. Deciding
    # "not found" here first means anything `close()` still raises has to be
    # the second kind - a real failure to save, which is a 500 that says so
    # rather than a 404 that sends someone looking for a request that is
    # actually still sitting there, just unmodified.
    if intakes.get(intake_id) is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    try:
        return intakes.close(intake_id, _who_email(request))
    except intakes.IntakeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# --- The client's own door (Stage 2 Tasks 3-4) --------------------------------
#
# Everything below this line, until the next section, is reachable with no
# `Authorization` header at all - see the `open_path` comment in `_gate` for
# what makes that safe. Nothing above this line changes: every route already
# defined still runs behind the gate exactly as it did before this file grew
# a second kind of caller.

#: What `/api/client/{token}` answers when the token does not currently work -
#: never minted, expired, relinked away, or naming an intake that is now
#: `closed`. One literal, reused for all four, because they are supposed to be
#: indistinguishable to whoever is holding the link: see `tokens.resolve`'s and
#: `clientview.of`'s own docstrings for why a stranger must not be able to tell
#: "never existed" from "used to work" by the wording of the answer.
_CLIENT_LINK_GONE = "That link is not valid, or has expired."


@app.get("/api/client/{token}", response_model=None, tags=["client"])
async def read_client_view(token: str) -> dict:
    """What a client sees of their own request - answered to whoever holds the
    link, which is the point, exactly as `read_invite` answers to whoever
    holds an invitation.

    `response_model=None`, set explicitly rather than left to the bare `-> dict`
    annotation: since FastAPI 0.89, a return annotation *is* the response
    model unless told otherwise, and a bare `dict` response model round-trips
    through FastAPI's own validation/serialisation pass rather than being
    returned exactly as `clientview.of` built it - the one boundary this
    route exists to keep a raw, hand-filtered dict on the safe side of. What
    `clientview.of` returns depends on the intake's state - `issued` carries
    two fields, a sent quotation carries a dozen - and `app/schemas.py` is
    deliberately not extended to describe a client's view (see
    `clientview.py`'s own docstring), so there is no single shape to declare
    here even if this route wanted one.

    `X-Workspace` is never read here. `tokens.resolve` is the only thing
    that says which workspace a link belongs to; trusting a header instead
    would let anybody hand in a token of their own alongside a workspace id
    that is not theirs and read whichever one they named.
    """
    found = tokens.resolve(token)
    if found is None:
        raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        entry = intakes.get(intake_id)
        if entry is None:
            # `tokens.resolve` already re-read the intake once before handing
            # this back, so reaching `None` here is the same transient-read
            # race its own docstring describes, not a case it failed to rule
            # out - and it answers exactly as an unknown token would either
            # way.
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)
        if entry.state == intakes.CLOSED:
            # Cannot be delegated to the token having gone blank on disk -
            # `tokens.resolve` validated against *its own* read of this same
            # record, moments earlier and inside its own `borrow`/`give_back`,
            # not this one. The two reads can straddle a `close()` landing in
            # between: `resolve()` sees a live, unexpired token and hands back
            # a match; by the time this handler's own `intakes.get()` runs,
            # `close()` has already flipped the state and blanked the token
            # via `intakes._write`. Nothing upstream of this line has checked
            # `entry.state` at all. A second, independent reason this cannot
            # be delegated: `tokens._build_locked` re-indexes any intake with
            # a non-empty token with no state check, by its own docstring's
            # admission - so a restored backup, a hand-edit, or any future
            # writer that bypasses `intakes._write` would resolve a live
            # token on a closed intake forever, not just across one race
            # window. Refused here, explicitly, with the same body every
            # other kind of "gone" gets - `clientview.of` itself would answer
            # `{"state": "closed"}` for this, correctly, for the *studio's*
            # reading of a closed intake; a client holding the link is not
            # the studio, and Task 3's promise is that this and "never
            # existed" are the same answer.
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)
        bundle = storage.get(entry.sent_bundle_id) if entry.sent_bundle_id else None
        # `clientview.of` runs inside this `try`, still borrowed - not after
        # `give_back` - because it calls `settings.load()`, which reads
        # `workspaces.current()`. Built outside the borrow, this would render
        # with whatever workspace `_gate`'s own `workspaces.use(X-Workspace)`
        # left the context pointed at, not the one the token actually names.
        try:
            return clientview.of(entry, bundle)
        except ValueError:
            # `clientview.of` raises rather than show a state it does not
            # recognise (`proposal_sent`, today - unreachable until Stage 3)
            # or a quoted-face state with no bundle attached. That is a bug on
            # this server's side of the line, not a client's business to see
            # the traceback of: it still answers with the one body a bad
            # token gets, not a 500 that would at least confirm the token
            # named something real.
            logger.exception("clientview.of could not show intake %s", intake_id)
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE) from None
    finally:
        workspaces.give_back(borrowed)


# --- The client writes (Stage 2 Task 4) ---------------------------------------
#
# Three routes, and every one of them is a stranger's POST answered with no
# `Authorization` header - see the `open_path` comment in `_gate` for the three
# things that make that safe. This section is the second of them: each
# handler below enforces its own state check rather than trusting the gate,
# which admits every method under this prefix and could not tell a legal
# write from an illegal one if it tried.


#: Per-IP, per-route courtesy limiter for the three routes below. What this is
#: **not**: a defence against a determined or distributed attacker. It is a
#: bare dict living in this one worker process's memory - gone the moment the
#: process restarts, blind to any request handled by a different worker or
#: machine, and trivially sidestepped by anyone who can send from more than
#: one address or is simply willing to wait out the window. What it *is*: a
#: courtesy control against a double-clicked form and a casual script trying
#: tokens in a loop from one machine. Keyed on `(ip, route)` rather than `ip`
#: alone so a client who burns their `/submit` budget retrying a typo cannot
#: also find `/finalize` refusing them for the same reason on the same visit.
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_MAX_REQUESTS = 20
_rate_limit_lock = threading.Lock()
_rate_limit_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    """The address Starlette itself resolved from the socket - `request.client`,
    never a header. `X-Forwarded-For` and its relatives are exactly what a
    caller trying to defeat this limit would set to whatever suits them;
    Starlette only ever populates `request.client` from the actual transport
    connection. It is `None` only for an ASGI transport with no notion of a
    peer address at all, which is treated as one shared, unrateable caller
    rather than raised on.
    """
    peer = request.client
    return peer.host if peer is not None else "unknown"


def _enforce_rate_limit(request: Request, route: str) -> None:
    """Refuse a caller's 21st write to `route` from one address inside a
    minute. See `_rate_limit_hits`'s own comment for what this is not."""
    now = time.monotonic()
    key = (_client_ip(request), route)
    with _rate_limit_lock:
        hits = _rate_limit_hits[key]
        while hits and now - hits[0] > _RATE_LIMIT_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= _RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts from this address. Wait a minute and try again.",
            )
        hits.append(now)


def _client_write_refused() -> HTTPException:
    """The one answer every refusal on these three routes gives - see
    `_CLIENT_LINK_GONE`'s own docstring. A token that never resolved, an
    intake that has since vanished, a move `intakes.advance` will not make
    from the state the record is actually in: all of them come back exactly
    like this, because a stranger holding a wrong-state link must learn
    nothing more than "no"."""
    return HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)


def _client_advance(intake_id: str, to: str, **fields) -> intakes.Intake:
    """Move a client-writable intake, or answer this door's one refusal.

    Deliberately not preceded by this handler's own `entry.state != ...`
    check: `intakes.advance` already makes that check, atomically, under its
    own lock - a second copy here would be exactly the kind of duplicated
    invariant that let `close()` and `advance(..., CLOSED)` drift apart in
    Task 1 (see `_write`'s own docstring). Relying on it here means a
    double-submit racing itself is refused by the same mechanism that refuses
    a submit from the wrong state, not a second one that could disagree with
    it.

    `intakes.advance` raises `IntakeError` for two entirely different
    reasons, distinguished only by the message: "that move is not legal"
    (unknown id, wrong state, an unknown field) and "the write itself
    failed" (`_write`'s own wrapped `OSError` - a full disk, a sync client
    holding a lock). Those are not the same answer. The first is a refusal,
    and every refusal on this door is the identical opaque 404. The second is
    not a refusal at all - it is this server failing to save a client's
    words - and folding it into "that link is gone" would tell somebody
    their submission was rejected when it was actually lost. `_write`'s
    wrapped message is the only raise site in `advance()` that names the
    save rather than the move, so it is what is matched on below, rather
    than adding a pre-read of `entry.state` here purely to classify an
    exception after the fact - which would reintroduce the duplicated-check
    problem this function exists to avoid, for a distinction that does not
    need it.
    """
    try:
        return intakes.advance(intake_id, to, **fields)
    except intakes.IntakeError as exc:
        if "could not be saved" in str(exc):
            logger.exception("Could not save a client write to intake %s (-> %s)", intake_id, to)
            raise HTTPException(
                status_code=500,
                detail="That could not be saved. Wait a moment and try again.",
            ) from exc
        raise _client_write_refused() from None


class ClientSubmitRequest(BaseModel):
    """The client's own four fields, filled in once from `issued`."""

    client_email: str = ""
    client_phone: str = ""
    scope: str = ""
    budget_text: str = ""


class ClientReviseRequest(BaseModel):
    """What the client asked to change, in their own words."""

    asked: str = ""


@app.post("/api/client/{token}/submit", response_model=None, tags=["client"])
async def submit_client_intake(
    token: str, request: Request, body: ClientSubmitRequest
) -> dict:
    """The client's own words, written once, from `issued` alone.

    There is no studio identity behind this call - just whoever is holding
    the link - so `intakes.advance`'s transition table is the entire abuse
    control: a second call, from `submitted` or anywhere past it, is refused
    exactly as a call against a token that never resolved is. Length is
    bounded with the same `_normalise_scope`/`_normalise_budget_text` the
    studio's own `/api/intakes` route uses, rather than a second convention
    for the same two fields.
    """
    _enforce_rate_limit(request, "submit")

    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    scope = _normalise_scope(body.scope)
    budget_text = _normalise_budget_text(body.budget_text)

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        moved = _client_advance(
            intake_id,
            intakes.SUBMITTED,
            client_email=(body.client_email or "").strip(),
            client_phone=(body.client_phone or "").strip(),
            scope=scope,
            budget_text=budget_text,
        )
        # `submitted` is one of `clientview.of`'s waiting states, which needs
        # no bundle - passing none is correct, exactly as it is for `issued`.
        return clientview.of(moved)
    finally:
        workspaces.give_back(borrowed)


@app.post("/api/client/{token}/revise", response_model=None, tags=["client"])
async def revise_client_intake(
    token: str, request: Request, body: ClientReviseRequest
) -> dict:
    """Ask for a change. Accepted only from `sent`, and only once at a time -
    a second ask before the studio has re-quoted moves the record to
    `revision_requested`, which is not `sent`, so `intakes.advance` refuses
    it the same as any other wrong-state call.

    `revisions` is a log `advance()` overwrites wholesale rather than appends
    to (see `ADVANCE_FIELDS`'s own docstring), so the full, updated list is
    built here from the record's current one and handed in whole.
    """
    _enforce_rate_limit(request, "revise")

    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    asked = _normalise_instruction(body.asked)
    if not asked:
        raise HTTPException(
            status_code=400,
            detail="Say what you would like changed before asking for a revision.",
        )

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        # A best-effort read, not a check: if the intake has vanished or
        # moved on since this line, `existing` may be stale, but
        # `_client_advance` below re-reads the record fresh, inside
        # `advance`'s own lock, and refuses correctly off that read
        # regardless of what this one saw.
        current = intakes.get(intake_id)
        existing = list(current.revisions) if current is not None else []
        moved = _client_advance(
            intake_id,
            intakes.REVISION_REQUESTED,
            revisions=existing + [{"asked": asked, "at": storage.utc_now_iso()}],
        )
        bundle = storage.get(moved.sent_bundle_id) if moved.sent_bundle_id else None
        return clientview.of(moved, bundle)
    finally:
        workspaces.give_back(borrowed)


@app.post("/api/client/{token}/finalize", response_model=None, tags=["client"])
async def finalize_client_intake(token: str, request: Request) -> dict:
    """The client accepts what was sent. Not a signature and nothing is
    charged - that framing lives on the client's own screen (Task 8), not
    here. Accepted only from `sent`. Tells the intake's own author and every
    admin, by an explicit recipient list rather than a role alone, since
    whoever issued this particular request should hear about it even if they
    do not administer the workspace.
    """
    _enforce_rate_limit(request, "finalize")

    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        moved = _client_advance(intake_id, intakes.FINALIZED)
        bundle = storage.get(moved.sent_bundle_id) if moved.sent_bundle_id else None

        # Resolved against the roster at write time, the same as every other
        # notification in this file - see `inbox.py`'s own docstring on why.
        # A plain union of two sets rather than two separate `notify()` calls,
        # so an admin who also happens to be `created_by` is told once, not
        # twice.
        recipients = {
            (member.email or "").strip().lower()
            for member in members.listing()
            if member.role == members.ADMIN
        }
        created_by = (moved.created_by or "").strip().lower()
        if created_by:
            recipients.add(created_by)
        if recipients:
            inbox.notify(
                "intake.finalized",
                list(recipients),
                {
                    "title": "A client finalised their quotation",
                    "body": " · ".join(
                        part
                        for part in (
                            bundle.estimate.quotation_ref if bundle else "",
                            bundle.estimate.client_name if bundle else "",
                            format_money(bundle.estimate.cost.total, bundle.estimate.currency)
                            if bundle
                            else "",
                        )
                        if part
                    ),
                    "href": "#/intakes",
                },
            )

        return clientview.of(moved, bundle)
    finally:
        workspaces.give_back(borrowed)


class AuthConfig(BaseModel):
    """What the client needs before it can show a sign-in screen."""

    required: bool = Field(
        description="False means this install has no accounts and answers everyone."
    )
    url: str = Field(default="", description="The Supabase project URL, or empty.")
    anon_key: str = Field(
        default="",
        description="The publishable key. Public by design; the secret is never sent.",
    )


@app.get("/api/auth/config", response_model=AuthConfig, tags=["accounts"])
async def auth_config() -> AuthConfig:
    """Whether anybody has to sign in here, and which project to sign in to.

    Answered without a token, because it is what tells a client whether to ask
    for one. It carries the publishable key and never the secret.
    """
    return AuthConfig(**auth.describe())


@app.get("/api/auth/me", tags=["accounts"])
async def auth_me(request: Request) -> dict:
    """Who the server thinks you are - the token's own claims, verified.

    On an install that requires a sign-in this is only ever reached with a
    verified token, so it answers with the email that token carries. On one
    without accounts it says so plainly rather than inventing a user.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return {"signed_in": False, "required": auth.required()}
    return {"signed_in": True, "required": True, "id": user.id, "email": user.email}


class MemberView(BaseModel):
    email: str = ""
    role: str = "member"
    added_at: str = ""
    you: bool = Field(default=False, description="True for the person asking.")


class InviteView(BaseModel):
    email: str = ""
    role: str = "member"
    invited_by: str = ""
    expires_at: str = ""
    link: str = Field(description="Where the invitation points. Send it however you like.")
    emailed: bool = Field(default=False, description="Whether Resend accepted the message.")
    problem: str = Field(default="", description="Why it was not emailed, if it was not.")


class TeamView(BaseModel):
    """One workspace's people, and what the person asking may do."""

    workspace: str = ""
    name: str = ""
    your_role: str = ""
    members: List[MemberView] = Field(default_factory=list)
    invites: List[InviteView] = Field(default_factory=list)
    email_configured: bool = False


class InviteRequest(BaseModel):
    email: str = ""
    role: str = "member"


class RoleRequest(BaseModel):
    role: str = "member"


def _invite_link(token: str) -> str:
    return f"{config.APP_ORIGIN.rstrip('/')}/#/invite/{token}"


def _who(request: Request) -> auth.User | None:
    return getattr(request.state, "user", None)


def _who_email(request: Request) -> str:
    """The signed-in email, or '' on an install with no accounts."""
    user = _who(request)
    return user.email if user else ""


def _require_admin(
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


def _team(request: Request) -> TeamView:
    user = _who(request)
    email = (user.email if user else "").lower()
    workspace = workspaces.current()
    named = next((item for item in workspaces.listing() if item.id == workspace), None)

    return TeamView(
        workspace=workspace,
        name=named.name if named else workspace,
        your_role=getattr(request.state, "role", "") or (members.ADMIN if not auth.required() else ""),
        members=[
            MemberView(
                email=member.email,
                role=member.role,
                added_at=member.added_at,
                you=member.email.lower() == email,
            )
            for member in members.listing()
        ],
        invites=[
            InviteView(
                email=entry.email,
                role=entry.role,
                invited_by=entry.invited_by,
                expires_at=entry.expires_at,
                link=_invite_link(entry.token),
            )
            for entry in members.invites()
        ],
        email_configured=mailer.configured(),
    )


@app.get("/api/team", response_model=TeamView, tags=["team"])
async def read_team(request: Request) -> TeamView:
    """Who is on the workspace you are in, and what you may do in it."""
    return _team(request)


@app.post("/api/team/claim", response_model=TeamView, tags=["team"])
async def claim_workspace(request: Request) -> TeamView:
    """Take charge of a workspace nobody administers yet.

    Only ever succeeds on an empty roster, which is the state a workspace made
    before teams existed is in. It is a button somebody presses rather than
    something that happens because they opened a page - the difference between
    a studio claiming its own book and the first passer-by inheriting it.
    """
    user = _who(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    if members.listing():
        raise HTTPException(
            status_code=409,
            detail="This workspace already has a team. Ask one of its admins for an invitation.",
        )

    members.claim(user.email, user.id)
    request.state.role = members.role_of(user.email, user.id)
    return _team(request)


@app.post("/api/team/invites", response_model=InviteView, status_code=201, tags=["team"])
async def invite_member(request: Request, body: InviteRequest) -> InviteView:
    """Offer somebody a place, and email them the link.

    The invitation is the record; the email is only how it travels. If Resend is
    not configured, or refuses, the invitation still exists and its link comes
    back with the reason - so an invite is never lost to a mail problem.
    """
    _require_admin(request)
    user = _who(request)

    try:
        entry = members.invite(body.email, body.role, user.email if user else "")
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    link = _invite_link(entry.token)
    studio = settings.load().studio_name
    named = next(
        (item for item in workspaces.listing() if item.id == workspaces.current()), None
    )

    emailed, problem = False, ""
    if mailer.configured():
        try:
            await asyncio.to_thread(
                mailer.send_invite,
                to=entry.email,
                studio=studio,
                workspace=named.name if named else workspaces.current(),
                inviter=user.email if user else "",
                role=entry.role,
                link=link,
            )
            emailed = True
        except mailer.MailError as exc:
            problem = str(exc)
            logger.warning("Invitation to %s was not emailed: %s", entry.email, exc)
    else:
        problem = "No email is configured, so send the link yourself."

    inbox.notify(
        "member_invited",
        inbox.ADMINS,
        lambda role, you: {
            "title": f"{entry.email} was invited",
            "body": (
                f"As {'an admin' if entry.role == members.ADMIN else 'a member'}"
                + (", and the email was sent." if emailed else ", but the email was not sent.")
            ),
            "href": "#/workspaces",
        },
    )

    return InviteView(
        email=entry.email,
        role=entry.role,
        invited_by=entry.invited_by,
        expires_at=entry.expires_at,
        link=link,
        emailed=emailed,
        problem=problem,
    )


@app.delete("/api/team/invites/{token}", status_code=204, tags=["team"])
async def revoke_invite(request: Request, token: str) -> Response:
    """Withdraw an invitation that has not been taken up."""
    _require_admin(request)
    if not members.revoke(token):
        raise HTTPException(status_code=404, detail="No such invitation.")
    return Response(status_code=204)


@app.patch("/api/team/members/{email}", response_model=MemberView, tags=["team"])
async def change_role(request: Request, email: str, body: RoleRequest) -> MemberView:
    """Make somebody an admin, or take it back."""
    _require_admin(request)
    try:
        changed = members.set_role(email, body.role)
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    named = next(
        (item for item in workspaces.listing() if item.id == workspaces.current()), None
    )
    where = named.name if named else workspaces.current()
    inbox.notify(
        "role_changed",
        [changed.email],
        {
            "title": (
                f"You are now an admin of {where}"
                if changed.role == members.ADMIN
                else f"Your role in {where} is now member"
            ),
            "body": (
                "You can change the studio's settings and delete work."
                if changed.role == members.ADMIN
                else "You can prepare quotations and proposals. Settings and deleting are an admin's."
            ),
            "href": "#/profile",
        },
    )
    return MemberView(email=changed.email, role=changed.role, added_at=changed.added_at)


@app.delete("/api/team/members/{email}", status_code=204, tags=["team"])
async def remove_member(request: Request, email: str) -> Response:
    """Take somebody off the team. Their work stays; the workspace is not theirs."""
    _require_admin(request)
    named = next(
        (item for item in workspaces.listing() if item.id == workspaces.current()), None
    )
    where = named.name if named else workspaces.current()

    try:
        if not members.remove(email):
            raise HTTPException(status_code=404, detail=f"{email} is not on this team.")
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Told after the fact, and told to them: somebody who can no longer open a
    # workspace should learn it from a sentence rather than from a 403.
    inbox.deliver(
        inbox.key_for(email),
        "removed_from_team",
        {
            "title": f"You were removed from {where}",
            "body": "Your own work is untouched. Ask an admin there if this was not intended.",
            "href": "#/workspaces",
        },
    )
    inbox.notify(
        "member_removed",
        inbox.ADMINS,
        {"title": f"{email} was removed from {where}", "body": "", "href": "#/workspaces"},
    )
    return Response(status_code=204)


class InvitePreview(BaseModel):
    """What an invitation says, before anybody accepts it."""

    workspace: str = ""
    name: str = ""
    email: str = ""
    role: str = "member"
    invited_by: str = ""
    expires_at: str = ""
    valid: bool = True
    problem: str = ""


@app.get("/api/invites/{token}", response_model=InvitePreview, tags=["team"])
async def read_invite(token: str) -> InvitePreview:
    """What this link is for. Answered to anyone holding it, which is the point."""
    found = members.find_invite(token)
    if found is None:
        return InvitePreview(valid=False, problem="That invitation is not valid, or has been used.")

    workspace_id, entry = found
    named = next((item for item in workspaces.listing() if item.id == workspace_id), None)
    return InvitePreview(
        workspace=workspace_id,
        name=named.name if named else workspace_id,
        email=entry.email,
        role=entry.role,
        invited_by=entry.invited_by,
        expires_at=entry.expires_at,
        valid=not entry.spent,
        problem="That invitation has expired." if entry.spent else "",
    )


@app.post("/api/invites/{token}/accept", response_model=TeamView, tags=["team"])
async def accept_invite(request: Request, token: str) -> TeamView:
    """Join the workspace this invitation is for."""
    user = _who(request)
    if auth.required() and user is None:
        raise HTTPException(status_code=401, detail="Sign in first, then accept the invitation.")

    found = members.find_invite(token)
    if found is None:
        raise HTTPException(status_code=404, detail="That invitation is not valid, or has been used.")

    workspace_id, _entry = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        members.accept(token, user.email if user else "", user.id if user else "")
        joined = user.email if user else "Somebody"
        inbox.notify(
            "member_joined",
            inbox.OTHERS,
            {
                "title": f"{joined} joined the team",
                "body": "",
                "href": "#/workspaces",
            },
            actor_email=user.email if user else "",
        )
        request.state.role = members.role_of(user.email if user else "", user.id if user else "")
        return _team(request)
    except members.MemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        workspaces.give_back(borrowed)


class NoteView(BaseModel):
    """One thing that happened, as told to you."""

    id: str = ""
    kind: str = ""
    at: str = ""
    title: str = ""
    body: str = ""
    href: str = ""
    read_at: str = ""


class Mailbox(BaseModel):
    unread: int = 0
    notes: List[NoteView] = Field(default_factory=list)


class ReadRequest(BaseModel):
    through: str = Field(default="", description="Mark everything at or older than this stamp.")
    ids: List[str] = Field(default_factory=list, description="Or mark exactly these.")


@app.get("/api/notifications", response_model=Mailbox, tags=["notifications"])
async def read_notifications(limit: int = 30) -> Mailbox:
    """Your own mail in the workspace the header names, newest first.

    No filtering happens here: the audience was resolved when each note was
    written, so what comes back is already only what you are meant to know.
    """
    return Mailbox(
        unread=inbox.unread(),
        notes=[NoteView(**note.model_dump()) for note in inbox.listing(limit)],
    )


@app.post("/api/notifications/read", response_model=Mailbox, tags=["notifications"])
async def mark_notifications_read(body: ReadRequest) -> Mailbox:
    """Mark mail read. Idempotent, and there is no way back to unread."""
    inbox.mark_read(through=body.through, ids=body.ids)
    return Mailbox(
        unread=inbox.unread(),
        notes=[NoteView(**note.model_dump()) for note in inbox.listing(30)],
    )


@app.delete("/api/notifications", response_model=Mailbox, tags=["notifications"])
async def clear_notifications() -> Mailbox:
    """Drop what you have read. Unread notes stay - clearing is not reading."""
    inbox.clear_read()
    return Mailbox(
        unread=inbox.unread(),
        notes=[NoteView(**note.model_dump()) for note in inbox.listing(30)],
    )


#: How long a socket waits for its first frame before giving up. A connection
#: that never says who it is has nothing to be told.
HANDSHAKE_SECONDS = 10
#: A frame every half minute keeps intermediaries from tidying an idle socket
#: away, and tells the client the line is still live.
HEARTBEAT_SECONDS = 30


@app.websocket("/api/notifications/stream")
async def notification_stream(socket: WebSocket) -> None:
    """Push notifications as they are written, rather than up to 20s later.

    The token arrives in the FIRST FRAME, not the query string. A browser
    cannot set headers on a WebSocket, and the obvious alternative - putting the
    access token in the URL - writes a live session into the server's access log
    and the browser's history, which is the trade this app already refused for
    file downloads.

    Nothing here is a delivery guarantee. The client keeps a slow poll, so a
    dropped socket costs latency rather than a lost notification.
    """
    await socket.accept()

    try:
        hello = await asyncio.wait_for(socket.receive_json(), timeout=HANDSHAKE_SECONDS)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await socket.close(code=1008)
        return

    workspace = str(hello.get("workspace", "") or "")
    token = str(hello.get("token", "") or "")

    if auth.required():
        try:
            user = auth.verify(token)
        except auth.AuthError as exc:
            await socket.send_json({"error": str(exc)})
            await socket.close(code=1008)
            return
        email, user_id = user.email, user.id
    else:
        email, user_id = "", ""

    # Same scoping as every other call: the workspace named, the person on the
    # token, and the roster consulted - a socket must not be a way around a
    # membership check.
    workspaces.use(workspace)
    inbox.use_identity(email, user_id)
    if auth.required():
        roster = members.listing()
        if roster and not members.is_member(email, user_id):
            await socket.send_json({"error": "You are not on this workspace's team."})
            await socket.close(code=1008)
            return

    room = workspaces.current()
    person = inbox.current_key()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    hub.subscribe(room, person, queue)

    # What is already waiting, so a fresh socket does not have to wait for the
    # next event to know where it stands.
    await socket.send_json({"ready": True, "unread": inbox.unread()})

    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await socket.send_json({"beat": True})
                continue
            await socket.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(room, person, queue)


@app.get("/api/health", response_model=HealthResponse, tags=["reference"])
async def health() -> HealthResponse:
    """Never returns the key itself - only whether one is present."""
    return HealthResponse(
        status="ok",
        model=config.GEMINI_MODEL,
        key_configured=config.key_configured(),
    )


if __name__ == "__main__":
    import uvicorn

    if not config.key_configured():
        logger.warning(
            "Starting without GEMINI_API_KEY - /api/proposals will answer 503 until "
            "it is set in %s",
            config.ENV_FILE,
        )

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
