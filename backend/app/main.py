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
from collections import deque
from typing import Callable, List, NamedTuple, Optional, Union
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

# `fastapi.UploadFile` subclasses this but is not the same class. Matching on the
# Starlette base accepts whichever one the running FastAPI hands back.
from starlette.datastructures import UploadFile as BaseUploadFile

# Raised out of `request.stream()` when the caller hangs up mid-body. `_gate`
# reads the stream itself now, so it is the first thing in this app that can
# see one.
from starlette.requests import ClientDisconnect

from app import (
    attachments as attachments_module,
    auth,
    clientview,
    config,
    hub,
    inbox,
    intakefiles,
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
    check_brief_is_real,
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


class BriefNotRealError(RuntimeError):
    """The model was asked whether the brief describes work and said it does not.

    Its own type rather than a `GeminiResponseError`, because the two are told
    apart by what the reader should DO. A `GeminiResponseError` means the model
    could not be reached or would not answer, and the fix is to press Generate
    again in a minute. This means the model answered perfectly well and the
    answer was no, and pressing Generate again will produce the same refusal
    for ever. A queue that shows one sentence for both trains a studio to retry
    the one thing retrying cannot fix.

    Never raised when the check itself fails - see `check_brief_is_real`, which
    returns the accepting default for every error it meets rather than raising.
    """


KINDS = ("proposal", "requirements")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_SLUG_STRIP = re.compile(r"[^A-Za-z0-9]+")
#: The full C0 control range plus DEL - deliberately including the tab,
#: line-feed and carriage-return that `.strip()` alone only ever catches at
#: the two ends of a string, not in the middle. None of the seven are
#: legitimate inside an email address or a phone number - unlike `scope` or
#: `budget_text`, free text where a line break is exactly the point, these
#: two fields have no shape a stray newline could ever be part of - and
#: otherwise ride along verbatim into a studio's own screens
#: (`GET /api/intakes`) and, for the email, into `clientview._masked`, which
#: returns a string with no `@` in it back unmasked. Stripped rather than
#: rejected outright: a stray control character pasted in from somewhere is
#: a nuisance to clean up, not intent worth a 400 for.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


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

    # The client's three write routes are rate-limited, and body-capped,
    # right here - ahead of everything else in this function, including
    # whether an `Authorization` header exists, since these routes need
    # none. This is the earliest hook this app's own code has into a
    # request's life: by the time a route function runs, FastAPI has already
    # read the whole body and validated it against the declared Pydantic
    # model, so a check placed inside the handler - where `_enforce_rate_limit`
    # first lived - is never truly first. Every caller, spammer or not, has
    # already paid for that buffering and parsing by then. See
    # `_enforce_rate_limit`'s and `_MAX_CLIENT_BODY_BYTES`'s own comments
    # (beside the three routes, below) for what each check is and is not.
    if request.method == "POST" and path.startswith("/api/client/"):
        write_route = path.rsplit("/", 1)[-1]
        if write_route in _CLIENT_WRITE_ROUTES:
            # How much this particular write is allowed to weigh, chosen from
            # the route and the content type together - see
            # `_client_body_limit` below for why a JSON submit and a file
            # upload cannot share one number, and why both conditions matter.
            body_limit = _client_body_limit(request, write_route)

            # `Content-Length` is what every JSON client this app actually
            # talks to sends (`fetch`, `axios`, `httpx`, this test suite's
            # own `TestClient`) for a body this small, and it is the cheapest
            # possible refusal for an oversized one: rejected before a single
            # byte of the body is read, not after it has already been
            # buffered and hits a validator deep inside a Pydantic model.
            # Kept for exactly that reason. What it is not is *proof* - a
            # caller can omit it by sending the body chunked, or declare a
            # small one and send a large one anyway, since HTTP framing takes
            # `Transfer-Encoding` over `Content-Length` when both are present
            # and the header then describes nothing. So this clause is the
            # fast path and never the bound; the bound is the read below,
            # which runs whatever this header said.
            declared_length = request.headers.get("content-length", "")
            if declared_length.isdigit() and int(declared_length) > body_limit:
                return JSONResponse(status_code=413, content={"detail": _TOO_LARGE})

            try:
                _enforce_rate_limit(request, write_route)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

            # The bound that actually holds, and the reason this middleware
            # touches the request stream at all.
            #
            # Without this, a caller sending `Transfer-Encoding: chunked`
            # skipped the cap entirely: the clause above became a no-op,
            # `_gate` fell through, and Starlette buffered and parsed the
            # whole body before the handler ran - so the eventual 404 for a
            # bogus token was paid for after the damage. Measured against a
            # real uvicorn rather than theorised: a security review streamed
            # 200 MiB into one request, fully buffered, and four concurrent
            # 150 MiB posts took the process from 116 MiB resident to
            # 1,249 MiB. It needs no valid token to do any of it, because
            # `tokens.resolve` is never reached.
            #
            # **Consume and replay**, of the three ways to close that. The
            # body is read here in chunks, counted, and refused the moment
            # the running total passes the limit - so an oversized caller is
            # stopped within one chunk of the cap rather than at the end of
            # whatever they felt like sending. What is read is then handed to
            # the handler by assigning `request._body`: this middleware is a
            # `BaseHTTPMiddleware`, and Starlette's `_CachedRequest` exists
            # precisely for this - its `wrapped_receive` replays a cached
            # `_body` to everything downstream, so `await request.body()`
            # inside FastAPI still sees every byte. That is a supported path,
            # not a reach past a private name for want of a public one.
            #
            # The two alternatives, and why not. Refusing *without*
            # consuming is not available: nothing can know a body is too
            # large without reading enough of it to tell. A pure-ASGI
            # wrapper around `receive` would let the multipart parser spool
            # to disk as it goes instead of holding the body here, which is
            # genuinely better for memory - but it has to signal the refusal
            # by raising through FastAPI's own body parsing and Starlette's
            # `ExceptionMiddleware`, and it would move the client's controls
            # out of this function, which is the one place they are all
            # readable together. Worth revisiting if the upload cap ever
            # grows; at these sizes it is not worth the seams.
            #
            # State the cost plainly, because there is one, and it is not
            # zero. On the path that is *not* refused this holds the body for
            # the length of the request, and FastAPI then makes a second copy
            # of it: `wrapped_receive` hands the cached bytes downstream as a
            # single message, and `Request.body()` joins that with the
            # trailing empty chunk `Request.stream()` always yields - a
            # two-element join, which allocates rather than handing back the
            # object it was given. So a legal client write costs roughly
            # twice its own size resident where it used to cost once.
            # Accepted knowingly: at 200,000 bytes that is nothing, at the
            # upload cap it is tens of megabytes, and the thing being traded
            # away is a path where an *illegal* write cost whatever the
            # caller felt like sending. The improvement is not that nothing
            # is buffered. It is that what is buffered is finally bounded by
            # a number this file chose rather than by one the caller did.
            #
            # Ordered after the rate limit deliberately: a caller already
            # over their budget is refused without this process buffering
            # anything at all for them.
            consumed = 0
            pieces: List[bytes] = []
            try:
                async for piece in request.stream():
                    consumed += len(piece)
                    if consumed > body_limit:
                        return JSONResponse(status_code=413, content={"detail": _TOO_LARGE})
                    pieces.append(piece)
            except ClientDisconnect:
                # The caller hung up mid-body. Answered plainly rather than
                # allowed to propagate: nobody is listening for this
                # response, but letting it escape would file a server error
                # in the log for something that is not one.
                return JSONResponse(
                    status_code=400, content={"detail": "That request ended before it was sent."}
                )
            request._body = b"".join(pieces)  # noqa: SLF001 - see the comment above

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
    # merely authenticated; and a per-IP-and-route rate limit and body-size cap,
    # just above this comment - a courtesy control against a script trying
    # every token it can generate or double-submitting by accident, not a
    # defence against a determined or distributed attacker. Say that last part
    # plainly, because it is the one of the three that is not actually
    # load-bearing security: anyone who can send from more than one address,
    # or who simply waits out the window, is unaffected by it.
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


# --- The client's own files, on their way to the model -----------------------
#
# `_read_images` and `_read_documents` above read what *this request* uploaded.
# The four functions below read what a client already sent through their own
# link, off the manifest on their intake, so that pricing a client's request
# does not mean the studio re-uploading the client's files - they never make a
# second trip through anybody's browser.


def _client_file_kind(record: dict) -> str:
    """The content type of one manifest entry, clamped to what PRISM stores.

    Clamped rather than trusted, for the reason `read_intake_file` spells out
    at length where it does the same thing: `Intake.attachments` is a bare
    `List[dict]` in `ADVANCE_FIELDS` and `advance()` validates nothing about
    the five keys inside an entry, so "`/submit` is its only writer" is a
    convention rather than a check. Here the value decides which lane a file
    takes - handed to the model as image bytes, or opened by a text reader -
    and that decision has to land somewhere this module chose rather than
    somewhere a dict said. Anything unrecognised becomes `FALLBACK_TYPE`,
    which is outside `INLINE_TYPES` and so takes the document lane, where
    `attachments.read` keys on the filename's own suffix and reports whatever
    it finds.
    """
    kind = str(record.get("kind") or "")
    return kind if kind in intakefiles.CONTENT_TYPES else intakefiles.FALLBACK_TYPE


def _client_file_name(record: dict) -> str:
    """What the client called this file, cleaned again on the way back out.

    `/submit` already stored a `clean_name`d string, and this cleans it a
    second time for exactly the reason that function is public and idempotent:
    the value on the record is guaranteed by nothing (see `_client_file_kind`),
    and this one is about to be printed inside
    `attachments.describe_for_prompt`'s `--- BEGIN <name> ---` markers, in a
    prompt. A "filename" carrying a newline could close those markers early and
    write its own; `clean_name` strips control characters, so it cannot.
    """
    return intakefiles.clean_name(str(record.get("name") or ""))


def _client_lanes(records: List[dict]) -> tuple[List[dict], List[dict]]:
    """One intake's manifest, split into the two things the model reads it as.

    Images reach the model as images; everything else is opened by
    `attachments.read` and reaches it as text. `intakefiles.INLINE_TYPES` is
    the same set that decides whether the studio's own download route renders a
    file in place, and it is consulted rather than restated - a sixth raster
    added there is an image here on the same day.
    """
    images: List[dict] = []
    papers: List[dict] = []
    for record in records:
        # An entry that is not a dict, or that addresses nothing, is skipped
        # rather than reasoned about: this list is only as well-formed as
        # whatever wrote it, which is the whole point of `_client_file_kind`.
        if not isinstance(record, dict) or not str(record.get("id") or "").strip():
            continue
        if _client_file_kind(record) in intakefiles.INLINE_TYPES:
            images.append(record)
        else:
            papers.append(record)
    return images, papers


def _fit_under_studio_cap(
    records: List[dict], studio_count: int, cap: int, noun: str
) -> tuple[List[dict], str]:
    """How much of the client's set fits beside the studio's own, under the
    studio's own limit - and what has to be said about the rest.

    The combined set is bounded by `MAX_IMAGES`/`MAX_DOCUMENTS`, which are the
    studio's numbers, and never by `MAX_CLIENT_FILES`: that one is a door
    policy for a stranger holding a link and has nothing to say about what the
    studio may read once the files are on the record.

    Which of the two sets gives way turns on one question - **is there an
    action the person reading the refusal can take?** A studio member whose own
    uploads push the total over can remove one of theirs, so that is a 400 that
    names the cause and the count rather than a bare "too many". A client's
    files that overflow the cap on their own cannot be removed by anybody:
    `/submit` runs once from `issued` and there is no client-side deletion
    after it, by design, so refusing there would be a permanent dead end on a
    legitimate enquiry. That is not hypothetical - `MAX_CLIENT_FILES` is 6 and
    `MAX_DOCUMENTS` is 5, so a client who attaches six documents and no images
    is already one past it, and the studio's very first Generate would be
    refused for something nobody can fix. That case is truncated and reported,
    which is this codebase's standing preference over correcting in silence.

    So: the client's files fill the cap first, the studio's own take what is
    left, and the overflow is a 400 only when the studio's own uploads are what
    does not fit.
    """
    kept = records[:cap]
    room = cap - len(kept)
    if studio_count > room:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This request already has {len(records)} {noun} from the client, and PRISM "
                f"reads {cap} {noun} in one quotation. Remove {studio_count - room} of the "
                f"{studio_count} you attached and generate again."
            ),
        )
    if len(kept) == len(records):
        return kept, ""
    dropped = ", ".join(_client_file_name(record) for record in records[cap:])
    return kept, (
        f"the client attached {len(records)} {noun} and PRISM reads {cap} - "
        f"{dropped} did not reach the quotation"
    )


#: What is said about a file the record still names and storage no longer has.
#: Reachable in the ordinary run of things rather than only under a race:
#: `close()` deletes an intake's objects and deliberately leaves
#: `Intake.attachments` populated - the record is the history of what was sent -
#: so a closed request priced anyway names files that are all gone.
_CLIENT_FILE_MISSING = (
    "{name} is no longer stored with this request - nothing from it reached the quotation"
)

#: And what is said when the read itself came apart. Distinct from the sentence
#: above because they are different facts about the file: one says the record
#: outlived the object, the other says something went wrong reaching for it.
_CLIENT_FILE_UNREADABLE = (
    "{name} could not be read from storage - nothing from it reached the quotation"
)


def _stage_name(name: str, limit: int = 30) -> str:
    """A client's filename, cut to fit a progress line.

    Cut in the MIDDLE rather than the end, because the end is where the
    extension is and "reading site-photo-2026-07-30-13…" tells a studio less
    than "reading site-photo-20…-102.jpg". The stage line is one line on a
    strip, and a name that wraps it moves everything under it.
    """
    clean = " ".join(str(name or "").split()) or "a file"
    if len(clean) <= limit:
        return clean
    keep = limit - 1
    head = keep // 2
    return clean[:head] + "…" + clean[-(keep - head):]


def _load_client_files(
    intake_id: str,
    image_records: List[dict],
    document_records: List[dict],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[List[tuple[bytes, str]], List[attachments_module.Attachment], List[str]]:
    """The bytes behind one intake's manifest. Blocking, and called in a thread.

    Every line of this is blocking and most of it is network: with Spaces
    configured, `intakefiles.read` is a `list_objects_v2` to recover the stored
    extension plus a `get_object` for the bytes, per file, and six files is
    twelve round trips. `attachments.read` then opens a PDF or unpacks a zip,
    which is CPU-bound and can be seconds on a large one. That is why this is a
    plain `def` reached through `asyncio.to_thread` - the same idiom
    `_store_client_files` and `read_intake_file` already use, and for the same
    reason: a blocking socket call on the event loop parks every request this
    worker holds, including an anonymous client's `/submit`, which cannot even
    be routed while it is parked.

    `to_thread` runs the call in a copy of the current context, so the
    workspace `_gate` set for the request - carried into the background task by
    `asyncio.create_task`, which copies the context the same way - is what this
    thread reads under. That matters more than it looks: `workspaces.current()`
    falls back to `default_id()` when it is unset rather than raising, so a
    borrow that failed to carry would read the *first workspace on file*,
    silently, and a single-workspace test could not tell the difference.
    check_intake_gate.py proves it across three, positively and negatively.

    **Nothing here raises, and the per-record guard below is what makes that
    true** rather than a claim about the two calls inside it. This runs inside
    `run()`, whose own `except Exception` fails the job and stamps
    `QUOTE_FAILED` - so a single malformed manifest entry escaping this loop
    would lose the whole quotation, which is the exact opposite of the rule
    this function exists to keep. `attachments.read` is already total by
    contract and `intakefiles.read` catches broadly on both backends, but
    `intakefiles.read`'s local branch reaches `workspaces.root()`, which raises
    `NoWorkspace`, and neither of those contracts is enforced from here. One
    file is what a bad entry may cost.

    Back come the images as `(bytes, content type)` pairs, the documents as
    `Attachment`s - `attachments.read`'s own shape, carrying its own `problem`
    when there was one - and the sentences that need saying to whoever pressed
    Generate.
    """
    images: List[tuple[bytes, str]] = []
    papers: List[attachments_module.Attachment] = []
    problems: List[str] = []

    #: One flat walk over both lanes, so the guard is written once rather than
    #: twice. Images first, matching `create_proposal`'s own reading order.
    lanes = [(record, True) for record in image_records]
    lanes += [(record, False) for record in document_records]

    for position, (record, as_image) in enumerate(lanes, start=1):
        name = _client_file_name(record)
        # Before the read, not after: the point of the line is to say what is
        # being waited ON. With Spaces configured this file is two network
        # round trips away and `attachments.read` may then unpack a zip, which
        # is the second or two the studio is looking at a static strip for.
        #
        # Called from inside the worker thread. `jobs.stage` writes under
        # `jobs._lock`, a module-level RLock, so that is safe - and it is why
        # this is a callback rather than this function importing `jobs` and
        # deciding what to say, which would put wording for a progress strip
        # inside the function that reads bytes.
        if on_progress is not None:
            on_progress(position, len(lanes), name)
        papers_before = len(papers)
        try:
            found = intakefiles.read(intake_id, str(record.get("id") or ""))
            if found is None:
                logger.warning(
                    "Intake %s: %s is named on the record and is not in storage",
                    intake_id,
                    name,
                )
                problem = _CLIENT_FILE_MISSING.format(name=name)
            elif as_image:
                # The manifest's kind rather than what storage reported, for the
                # reason `read_intake_file` gives: it is the value `/submit`
                # resolved through `intakefiles.resolve_type` and the one the
                # studio's queue already shows this file as. It is inside
                # `INLINE_TYPES` or this record would not be in this lane.
                images.append((found[0], _client_file_kind(record)))
                problem = ""
            else:
                # The client's own filename, not the stored `<12-hex>.<ext>`:
                # `attachments.read` picks its reader off the suffix, and the
                # name is what appears between the markers in the prompt and in
                # any problem line the studio reads afterwards.
                #
                # This also settles what happens to the manifest's `note`, which
                # may already hold an extraction warning from upload time: it is
                # not read off the record, because this call *recomputes* it.
                # `_store_client_files` produced that note as
                # `attachments.read(name, data).problem` over the same bytes
                # under the same name, and this is the same call - so for any
                # entry whose `kind` is still the one `/submit` resolved, the
                # warning reaches the quotation by being derived again rather
                # than copied, and the two cannot drift apart. An entry whose
                # `kind` was altered to something `_client_file_kind` has to
                # clamp lands in this lane instead of the image one and is
                # reported on its suffix, which is a different sentence and the
                # safe direction to differ in.
                item = attachments_module.read(name, found[0])
                papers.append(item)
                problem = item.problem
        except Exception:  # noqa: BLE001 - one bad entry must not cost the quotation
            logger.exception(
                "Intake %s: %s could not be read for the quotation", intake_id, name
            )
            # Anything half-appended for this record goes with it, so a partial
            # read cannot reach the model as though it were whole.
            del papers[papers_before:]
            problem = _CLIENT_FILE_UNREADABLE.format(name=name)

        if not problem:
            continue
        problems.append(problem)
        # A document with nothing behind it still gets an `Attachment` carrying
        # the reason - the shape `describe_for_prompt` already knows to leave
        # out, exactly as it leaves out a scan with no text layer - but only
        # when the reader did not already produce one saying the same thing.
        if not as_image and len(papers) == papers_before:
            papers.append(attachments_module.Attachment(name, "", "", problem))

    return images, papers, problems


def _normalise_currency(raw: str) -> str:
    code = (raw or "").strip().upper() or "PHP"
    if not _CURRENCY_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail=f"'{raw}' is not a currency code. Use a three-letter ISO 4217 code such as PHP or USD.",
        )
    return code


def _normalise_brief(raw: str) -> str:
    """The studio's own brief, and it carries the same floor the client's scope
    does - which it did not until now.

    The floor arrived on the client's door first because that door opens once
    and a bad write costs the link. That made it easy to argue the studio
    needed nothing: a studio can read what comes back and try again. But the
    thing it tries again on is a full generation - several model calls, a
    minute of wall time, and a bundle written to disk - and `"a"` bought all of
    it. Free to check, so it is checked.

    What this does NOT catch is prose that is structurally fine and means
    nothing: `erwerasdad dklajdlaksdjacsdasd` is thirty characters, thirteen
    distinct letters and two words. Only meaning separates that from a real
    brief, and meaning is `_brief_is_real`'s job, inside the job where a model
    call belongs. This is the cheap half, and it runs first because it is free
    and it is certain.
    """
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
    shortfall = _scope_shortfall(brief)
    if shortfall:
        raise HTTPException(status_code=400, detail=shortfall)
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


def _scope_shortfall(scope: str) -> str:
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


def _normalise_scope(raw: str) -> str:
    """An intake's `scope` reaches the same prompt a brief does, so it carries
    the same ceiling - and, since Stage 2 made this an anonymous write, a floor
    the studio's own brief does not have.

    The asymmetry was backwards before this. `_normalise_brief` refuses an
    empty brief with an actionable sentence; this, on the LESS trusted door,
    accepted the empty string outright - the browser blocked it and curl did
    not. And `/submit` runs once from `issued` with no move back, so whatever
    arrives here is not a draft, it is the client's entire request. A scope of
    "a" produced a full priced quotation, because there is no path in the
    generation pipeline that can decline: `response_schema=Estimate` is forced
    and `schemas.py` gives the model no field in which to say it cannot price
    something. The refusal has to happen at the door or nowhere.
    """
    scope = (raw or "").strip()
    if len(scope) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The scope is {len(scope):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it or attach the detail separately."
            ),
        )
    shortfall = _scope_shortfall(scope)
    if shortfall:
        raise HTTPException(status_code=400, detail=shortfall)
    return scope


def _normalise_budget_text(raw: str) -> str:
    """The client's own budget words, bounded the same way `scope` is - see
    `_normalise_scope` - and required to carry a number.

    The field is a target cost. "around 300k", "under ₱500,000" and "2.5M" all
    say something a studio can shape a quotation against; "a" does not, and
    neither does "not sure". It was already a required field, so this only
    makes the requirement mean what it says. `str.isdigit()` rather than an
    ASCII test, so Arabic-Indic and full-width digits count as the numbers
    they are.
    """
    text = (raw or "").strip()
    if len(text) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The budget note is {len(text):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it rather than pasting the whole thread."
            ),
        )
    if config.MIN_CLIENT_BUDGET_DIGITS:
        digits = sum(1 for character in text if character.isdigit())
        if digits < config.MIN_CLIENT_BUDGET_DIGITS:
            raise HTTPException(
                status_code=400,
                detail="Put a rough number on it - “around 300k” or “under ₱500,000”.",
            )
    return text


#: `scope` and `budget_text` reach a prompt and so inherited `MAX_BRIEF_CHARS`
#: as their ceiling; an address and a phone number never do; 254 is the
#: practical maximum length of an email address (RFC 5321 §4.5.3.1.3) and
#: comfortably covers a phone number with a country code, extension and
#: punctuation besides.
_MAX_CONTACT_CHARS = 254


def _normalise_client_email(raw: str) -> str:
    """The client's own address, from `/api/client/{token}/submit` - the
    first anonymous write in this codebase, where every other field on the
    same request is length-checked and this one, before this function
    existed, was not. Scrubbed of control characters and bounded the same
    way `scope`/`budget_text` are, not validated as an actual email address:
    `create_intake` (the studio's own route) does not validate the shape
    either, and this function's job is only to make the value safe to store
    and later render on the studio's own screens, not to police what counts
    as an address."""
    email = _CONTROL_CHARS.sub("", raw or "").strip()
    if len(email) > _MAX_CONTACT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That email address is {len(email):,} characters. The limit is "
                f"{_MAX_CONTACT_CHARS}."
            ),
        )
    return email


def _normalise_client_phone(raw: str) -> str:
    """The client's own phone number - see `_normalise_client_email`, which
    this mirrors exactly."""
    phone = _CONTROL_CHARS.sub("", raw or "").strip()
    if len(phone) > _MAX_CONTACT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That phone number is {len(phone):,} characters. The limit is "
                f"{_MAX_CONTACT_CHARS}."
            ),
        )
    return phone


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

    # What the client themselves attached, when this quotation is being
    # prepared for a request that came in through a link. Split here and
    # fetched later, and the split is the cheap half: it reads the manifest on
    # the record, which `intakes.get` has already taken off local disk, so the
    # counts the caps below turn on cost no network at all and the refusal they
    # can produce still happens synchronously, before a job exists, exactly as
    # this endpoint's docstring promises.
    #
    # An `intake_id` naming nothing this workspace holds - a stale form, a
    # request in somebody else's workspace, an id somebody typed - is not an
    # error here, for the same reason `stamp` refuses to make it one. And
    # `intakes.get` is what enforces the workspace rather than a comparison
    # written here: it builds its path under `workspaces.root()`, so an intake
    # belonging to another workspace is simply not a file.
    client_images: List[dict] = []
    client_papers: List[dict] = []
    client_notes: List[str] = []
    if intake_id:
        for_intake = intakes.get(intake_id)
        if for_intake is not None:
            client_images, client_papers = _client_lanes(for_intake.attachments)
            client_images, note = _fit_under_studio_cap(
                client_images, len(attachments), config.MAX_IMAGES, "images"
            )
            if note:
                client_notes.append(note)
            client_papers, note = _fit_under_studio_cap(
                client_papers, len(papers), config.MAX_DOCUMENTS, "documents"
            )
            if note:
                client_notes.append(note)

    #: What `prepare()` actually sends, as distinct from what this request
    #: uploaded. Both are initialised to the studio's own files so that every
    #: path through `run()` produces a valid prompt whether or not there is an
    #: intake behind it; `run()` merges the client's in, in a thread, before the
    #: first tier is priced.
    model_images: List[tuple[bytes, str]] = list(attachments)
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
            model_images,
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
        nonlocal papers_text
        stamp(intakes.PREPARING, job_id=job.id)
        # "Scope", not "brief", because that is what the field is called on
        # both screens this job can start from - the pad's own step is headed
        # "What is in scope" and the client's is "Scope". A stage naming a word
        # that appears nowhere in front of the person reading it makes them
        # wonder which of the things they typed it means.
        jobs.start(job.id, "Reading the Scope")
        try:
            # Before anything is priced. The structural floor in
            # `_normalise_brief` already refused the empty, the too-short and
            # the visibly-mashed; what reaches here and is still not a brief is
            # text that only MEANS nothing - "erwerasdad dklajdlaksdjacsdasd"
            # is thirty characters, thirteen distinct letters and two words,
            # and no rule short of reading it can tell it from a real one.
            #
            # Inside the job rather than on the endpoint, and that is a
            # deliberate reading of this route's own contract two comments
            # below: "everything that can be rejected is rejected
            # synchronously." The rejectable part is the floor, and it IS
            # synchronous. This is not rejectable without a network call, and
            # `POST /api/proposals` makes none today - adding one would mean a
            # Gemini outage stopped a studio from STARTING a quotation rather
            # than failing a job they can retry, on a blocking call in an
            # `async def` handler, which is the shape that parks the event loop
            # for every other request this worker holds.
            #
            # One call, on the brief alone. The client's files are fetched
            # after this, so a refused brief costs no Spaces round trips either.
            if config.CHECK_BRIEF_IS_REAL:
                verdict = await check_brief_is_real(request.brief)
                if not verdict.is_brief:
                    raise BriefNotRealError(
                        verdict.reason
                        or "That does not read as a description of work. Say what needs "
                        "building, who it is for, and anything that shapes it."
                    )
            if client_images or client_papers:
                # Fetched here - inside the job, and inside a thread - rather
                # than before the job was handed off, and the reason is what the
                # alternative costs. With Spaces configured this is two round
                # trips per file against a client bounded at 5s connect / 10s
                # read with two attempts, and botocore makes two TCP connection
                # attempts per attempt: Task 2 measured 18 seconds of wall time
                # for a single operation against a packet-dropping endpoint.
                # Six files is twelve operations. As a wait on the POST that is
                # a Generate button hanging for minutes with no job to look at
                # and nothing to distinguish it from a crash; as a wait inside
                # the job it is a progress bar saying what it is doing. This
                # endpoint's own contract already picks between those two:
                # everything that can be *rejected* is rejected synchronously,
                # and only the part that takes time is deferred. The caps are
                # the part that can be rejected, and they were answered off the
                # manifest above at no network cost at all.
                #
                # `asyncio.to_thread` in either case, because this is `async
                # def` and a blocking socket call on the loop parks every
                # request this worker holds - see `_load_client_files`.
                total_files = len(client_images) + len(client_papers)
                jobs.stage(
                    job.id,
                    f"Reading {total_files} file{'' if total_files == 1 else 's'} from the client",
                )
                found_images, found_papers, problems = await asyncio.to_thread(
                    _load_client_files,
                    intake_id,
                    client_images,
                    client_papers,
                    lambda at, total, name: jobs.stage(
                        job.id, f"Reading {_stage_name(name)} ({at} of {total})"
                    ),
                )
                # The client's files first in both lanes, which is a decision
                # rather than an accident. `attachments.describe_for_prompt`
                # spends one `MAX_TOTAL_CHARS` budget in list order and names
                # whatever it runs out of room for, so first is what is
                # guaranteed to be read: the client's documents are the request
                # being priced, the studio's are reference material added on top
                # of it, and a quotation prepared without the client's own scope
                # is the failure this whole feature exists to prevent. The cost
                # is the reverse of that and is real - a studio attaching a long
                # document to a request already carrying 60,000 characters of
                # client material may see it named in `describe_for_prompt`'s
                # own "was not included" line rather than read.
                model_images[:0] = found_images
                papers_text = attachments_module.describe_for_prompt(found_papers + papers)
                client_notes.extend(problems)
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
                    # Said before the call rather than after it. `jobs.step`
                    # below marks the tier DONE, which is the right thing for a
                    # progress bar and the wrong thing for the line of text
                    # above it - between two steps that line was describing
                    # work that had already finished.
                    jobs.stage(job.id, f"Pricing {tier} ({position + 1} of {len(tiers)})")
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
                # The single-quotation path, and the longest wait in the job:
                # one model call, tens of seconds, previously behind whatever
                # stage happened to be showing when it started - which for a
                # request with attachments was the last filename read.
                jobs.stage(job.id, "Pricing the work")
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
            # A client's file that did not reach the model goes ahead of the
            # arithmetic notes rather than after them, because only `caveats[0]`
            # reaches the body below: a document that was not read changes what
            # was priced, which is worth more of that one line than a note about
            # what the costing had to do to a figure. Placed after the block
            # above, not folded into it, so it cannot suppress the "could not be
            # reached exactly" caveat merely by making the list non-empty.
            #
            # Joined into one entry rather than added as several, for the same
            # reason: six separate entries would name one missing file and drop
            # the other five in silence, which is the failure this whole line
            # exists to avoid. Bounded by `MAX_CLIENT_FILES` plus one truncation
            # note per lane.
            if client_notes:
                caveats.insert(0, "; ".join(client_notes))
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
        except BriefNotRealError as exc:
            # Not `logger.error`: nothing is broken. A studio typed something
            # that is not a brief and was told so, which is this check working.
            logger.info("Brief refused before pricing: %s", exc)
            jobs.fail(job.id, str(exc))
            stamp(intakes.QUOTE_FAILED, error=str(exc))
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
    """The PAD settings this intake will be quoted under - kind, currency,
    market region, tax basis, payment terms, tiers. No client words: from
    Stage 2 onward those arrive through the client's own link
    (`POST /api/client/{token}/submit`), never typed in by the studio.

    `preset` is a loose dict, deliberately not a typed sub-model - it mirrors
    `Intake.preset`, which is itself opaque storage for exactly this reason
    (see its own docstring), and the one field inside it this route actually
    reasons about (`currency`) is normalised below the same way `/api/proposals`
    normalises it. Nothing here validates the rest, because nothing downstream
    needs it validated yet: it is read back only to prefill the pad, by a
    later task, and revalidated in full - `kind`, `market_region`, the payment
    terms, `tiers` - the moment it is actually used to generate a quotation.
    """

    preset: dict = Field(default_factory=dict)


#: Where a client's link points. `#/c/<token>` is the hash route Stage 2's
#: client shell resolves ahead of the signed-in app - see
#: `docs/superpowers/plans/2026-08-03-client-intake-stage-2.md`'s Task 7. The
#: same shape `_invite_link` already uses for a teammate's invitation.
def _client_link(token: str) -> str:
    return f"{config.APP_ORIGIN.rstrip('/')}/#/c/{token}"


class IntakeIssued(intakes.Intake):
    """`Intake`'s own wire shape, plus the one thing `Intake.token`'s own
    `exclude=True` deliberately drops: a link the studio can actually hand to
    a client. Returned only by the two calls that mint a fresh token under an
    admin's own hand - `create_intake` and `relink_intake` - never by
    `list_intakes` or `read_intake`, which stay exactly as `Intake.token`'s
    docstring requires: any member may read the queue, so the credential that
    gates an unauthenticated route must not ride along with it.

    This is Task 1's deferred boundary (see `intakes.py`, `Intake.token`'s
    docstring, and the `RULING` in this branch's progress ledger): carry the
    storage shape as-is, and let Task 6 design the split with the split's own
    shape known. `IntakeIssued` inherits `token`'s exclusion unchanged rather
    than reintroducing it in cleartext - `link` is derived, additive, and
    built from the same field the wire has always hidden, not a second name
    for the secret itself.

    A dedicated `GET /api/intakes/{id}/link` was considered here and refused,
    on the grounds that `intakes.relink` already answers "the studio lost the
    link". IT WAS BUILT LATER ANYWAY, and the reasoning above was wrong in a
    way worth leaving visible rather than quietly editing out: relink does not
    RECOVER a link, it REPLACES one. That is the same thing only while nobody
    holds the old link. Once it has been sent - and sending it is the entire
    point of minting it - reissuing to get a copy silently breaks the link the
    client already has, possibly after they have opened it. "Recover" and
    "resend to a second contact" are different needs and only one of them is
    destructive. See `read_intake_link` below.
    """

    link: str = ""


def _issued(entry: intakes.Intake) -> IntakeIssued:
    """Wrap a just-minted or just-reissued intake with its link. `entry`'s own
    `token` never reaches `model_dump()` (`exclude=True`), so this cannot
    accidentally leak it under its own name - only `link`, built from it by
    hand, crosses the wire."""
    return IntakeIssued(**entry.model_dump(), link=_client_link(entry.token))


@app.post("/api/intakes", response_model=IntakeIssued, status_code=201, tags=["intakes"])
async def create_intake(request: Request, body: IntakeRequest) -> IntakeIssued:
    """Mint a client request from the studio's own PAD preset. Admin-only: an
    intake is the start of a price, and issuing one is nearer to inviting
    somebody than to drafting a quotation. This is deliberate rather than
    incidental - the gate's member/admin split (`_gate`, main.py:311-319)
    only blocks a member's POST to `/api/settings` and `/api/team`, so
    without this call a member's POST here would go through unchecked.

    Starts `issued` with a token already minted (`intakes.create` does both),
    so the response can hand the studio a working link in the same call that
    creates the record - there is no second step to reach it, and no window
    where the intake exists but nothing points at it.
    """
    _require_admin(request, "Only an admin of this workspace can record a client request.")
    preset = dict(body.preset or {})
    if "currency" in preset:
        preset["currency"] = _normalise_currency(preset.get("currency", ""))
    try:
        entry = intakes.create(
            client_email="",
            client_phone="",
            scope="",
            budget_text="",
            preset=preset,
            created_by=_who_email(request),
        )
    except intakes.IntakeError as exc:
        # A brand-new intake has no existing state to conflict with - the only
        # way `create()` raises is that the record could not be written (a
        # full disk, a permissions problem). That is ours to report, not a
        # 409 implying the caller sent something to fix and retry.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _issued(entry)


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


def _owned_attachment(entry: intakes.Intake, file_id: str) -> dict | None:
    """The manifest entry `file_id` names on `entry`, or `None`.

    Said plainly rather than left to be inferred: the *primary* cross-intake
    gate on this route is `intakefiles.read`'s own structural binding, below -
    it ties `(intake_id, file_id)` together via the actual storage key or
    local path, not by a comparison that could be written wrong, and
    check_intakefiles.py proves it exhaustively. This function is the
    route's own **second, independent** layer, sourced from this intake's own
    manifest rather than from storage: a file id belonging to a different
    intake, in this workspace or another, is simply not a member of *this*
    intake's own `attachments`, so it comes back `None` here before storage
    is ever asked a question - structurally, the same way `_quoted_bundle`
    ties a bundle id to the intake that may send it, rather than by a
    comparison a route could get subtly wrong.

    Its own name rather than inlined into the route, for the same reason
    `_quoted_bundle` has one: a check that can be disabled on its own is a
    check a test can prove is doing real work. See check_intakes_api.py's
    mutation proof, which simulates `intakefiles.read`'s own binding being
    absent before trusting a flip from 404 to 200 to mean anything about this
    function specifically - disabling only this function, with that binding
    intact, would still 404 on it and prove nothing about this route at all.
    """
    key = (file_id or "").strip().lower()
    if not key:
        return None
    for item in entry.attachments:
        if isinstance(item, dict) and str(item.get("id", "")).strip().lower() == key:
            return item
    return None


def _attachment_disposition(kind: str, name: str) -> str:
    """`inline` for the raster allowlist, `attachment` for everything else -
    `intakefiles.disposition_for` decides which, off the one content type
    this route already resolved from the manifest, so `INLINE_TYPES` is
    consulted in exactly one place rather than restated here.

    The filename is the one string in this response a client chose, not this
    studio. `intakefiles.clean_name` strips separators and control characters
    on the way onto the record but keeps `"`, so a file called `sco"pe.pdf`
    would close a naive `filename="..."` early and corrupt the header. Two
    forms rather than one: `filename=` is a plain-ASCII fallback for the RFC
    6266 grammar every client understands, with every `"`, backslash and
    control byte stripped rather than escaped - a fallback name only has to
    be recognisable, not exact, and escaping inside this particular header is
    its own can of worms. `filename*=UTF-8''...` (RFC 5987) is the real name,
    percent-encoded, and what every current browser actually opens or saves
    the file under.

    A name with no ASCII characters at all reduces the fallback to whatever
    ASCII is left over - `提案書.pdf` keeps only its extension and falls back
    to `.pdf`, and `提案書` alone (no extension survives to keep) falls back
    to the bare word `attachment`. Both are deliberate, not a gap: `filename*`
    still carries the real name correctly encoded, which is what every
    current browser actually reads, and the fallback's only job is to be a
    legal RFC 6266 token for whatever does not - never to be recognisable in
    every case.
    """
    disposition = intakefiles.disposition_for(kind)
    original = name or "attachment"
    ascii_only = original.encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r'[\\"\r\n]', "", ascii_only).strip() or "attachment"
    encoded = quote(original, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


@app.get("/api/intakes/{intake_id}/files/{file_id}", tags=["intakes"])
async def read_intake_file(intake_id: str, file_id: str) -> Response:
    """One file a client attached to their request - the half of this
    feature the studio asked for by name.

    No admin check: reading the queue is any member's, and `list_intakes`'s
    own docstring already settles that this is the same class of read.

    There is no presigned redirect here, on either backend - see
    `intakefiles.py`'s own docstring and the plan's "The revisit" section.
    A presigned GET answers no `Access-Control-Allow-Origin` (the bucket has
    no CORS rule, which is the default for every Space) and, carrying an
    `Authorization` header across the redirect, a `400` instead of a file on
    a browser that does not strip it - so a `307` here would be a response
    the studio's own browser can neither `fetch` nor navigate to. The bytes
    are read through `intakefiles.read` instead, in a thread: that call is a
    network round trip once Spaces is configured, this is `async def`, and a
    blocking socket call in it would park every request this worker holds,
    including an anonymous client's `/submit` - `asyncio.to_thread` runs it
    in a copy of the current context, so the workspace `_gate` already set
    for this request carries into the thread rather than being silently lost.

    The id pair is checked together, twice, independently, before a byte
    leaves storage: `_owned_attachment` against this intake's own manifest
    first, `intakefiles.read` second - see that function's own docstring for
    why one alone is not trusted to be enough.

    Every response sets `Cache-Control: no-store`, an explicit `Content-Type`
    read off the manifest rather than off the object, `Content-Disposition:
    inline` for `intakefiles.INLINE_TYPES` and `attachment` for everything
    else, and `X-Content-Type-Options: nosniff` on both branches - the
    exception that used to keep `nosniff` off the Spaces branch is retired
    along with the presigned redirect it existed for.
    """
    entry = intakes.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")

    record = _owned_attachment(entry, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="That file does not exist.")

    found = await asyncio.to_thread(intakefiles.read, intake_id, file_id)
    if found is None:
        # Covers a race with another request, and the ordinary case this
        # feature has to answer for on purpose: `close()` deletes the objects
        # but leaves `Intake.attachments` populated (see `intakes.py` and
        # Task 2 Step 6), so a closed intake's own manifest still names this
        # file right up until this line - and finds nothing behind it.
        raise HTTPException(status_code=404, detail="That file does not exist.")

    data, stored_type = found
    # The manifest's own kind wins over what storage reports. It is the value
    # `/submit` resolved through `intakefiles.resolve_type` in the first
    # place and the one the studio's queue already shows this file as;
    # `stored_type` is kept only as a fallback for an entry that somehow
    # lacks one, so this response is never inconsistent with the record that
    # named the file.
    #
    # Clamped against `intakefiles.CONTENT_TYPES` rather than trusted outright.
    # `attachments` is a bare `List[dict]` in `ADVANCE_FIELDS` with no
    # per-entry model (`Intake.attachments`'s own docstring says so), and
    # `/submit` is its only writer *today* - but "today" is a convention, not
    # a check, and `advance()` validates nothing about a dict's contents. An
    # entry carrying `{"kind": "text/html"}` is accepted by `advance()` right
    # now, and unclamped this route would answer `Content-Type: text/html` on
    # the studio's own origin for it. Still `attachment` for anything outside
    # `INLINE_TYPES`, and still `nosniff` either way, so that is not a live
    # hole - but the type actually served should be one this module chose to
    # store things as, not whatever a dict happens to say, on either branch:
    # `stored_type` is exactly as unclamped when it comes from Spaces' own
    # reported `ContentType`, which nothing here has verified either.
    content_type = str(record.get("kind") or stored_type or intakefiles.FALLBACK_TYPE)
    if content_type not in intakefiles.CONTENT_TYPES:
        content_type = intakefiles.FALLBACK_TYPE
    name = str(record.get("name") or "attachment")

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": _attachment_disposition(content_type, name),
            "X-Content-Type-Options": "nosniff",
        },
    )


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


def _quoted_bundle(entry: intakes.Intake, bundle_id: str) -> bool:
    """Whether `bundle_id` is one this intake was actually quoted with, and
    that bundle still exists. Membership in `entry.bundle_ids` answers "did
    PRISM quote this for *this* request" - a real bundle id that belongs to
    a different intake must be refused exactly like one that was simply
    guessed - but membership alone is not sufficient: `DELETE
    /api/proposals/{id}` does not prune `bundle_ids`, so an id recorded at
    quote time can already be gone by the time a studio tries to send it.
    Sending a dangling id would move the intake to `sent` with
    `sent_bundle_id` naming nothing, and the client's own door would answer
    its opaque "gone" response forever - a state this intake can never
    escape by itself, since `relink` reissues a link, not a bundle. Checked
    by `storage.get`, not by re-validating anything about the bundle's own
    content - the only question here is whether there is still something to
    send. Its own name, rather than inlined into the route, so a test can
    disable it on its own to prove the route's refusal actually depends on
    it."""
    return bundle_id in entry.bundle_ids and storage.get(bundle_id) is not None


class IntakeSendRequest(BaseModel):
    """Which of a re-quotable intake's `bundle_ids` is the one being shown to
    the client. Explicit rather than assumed (`bundle_ids[0]`, say): a second
    Generate pass replaces `bundle_ids` wholesale (see `intakes.ALLOWED`'s own
    docstring on `QUOTED: {PREPARING, ...}`), and by the time a studio is
    ready to send, more than one candidate can be on file."""

    bundle_id: str = ""


@app.post("/api/intakes/{intake_id}/send", response_model=intakes.Intake, tags=["intakes"])
async def send_intake(request: Request, intake_id: str, body: IntakeSendRequest) -> intakes.Intake:
    """Hand a prepared quotation to the client: `quoted -> sent`, with
    `sent_bundle_id` naming which bundle and `sent_at` stamping when.
    Admin-only, the same side of the line as issuing and closing a request -
    this is the call that starts the client's own clock (`clientview.of`
    reads `sent_at`, never `bundle.created_at`, to tell them when they were
    actually shown something), so a member should not be able to trigger it
    alone.

    `bundle_id` is required and must already be one of this intake's own
    `bundle_ids`, AND still exist in `storage` - see `_quoted_bundle`'s own
    docstring, which is the actual contract this route enforces. A bundle id
    that is real but belongs to a different intake, or was simply guessed,
    or once belonged here but was since deleted (`DELETE /api/proposals/{id}`
    does not prune `bundle_ids`), is refused exactly like one that names
    nothing.
    """
    _require_admin(request, "Only an admin of this workspace can send a quotation to a client.")
    bundle_id = (body.bundle_id or "").strip()
    if not bundle_id:
        raise HTTPException(status_code=400, detail="Say which bundle to send.")

    entry = intakes.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    if not _quoted_bundle(entry, bundle_id):
        raise HTTPException(
            status_code=400,
            detail="That bundle was not quoted for this request.",
        )
    try:
        return intakes.advance(
            intake_id,
            intakes.SENT,
            sent_bundle_id=bundle_id,
            sent_at=storage.utc_now_iso(),
        )
    except intakes.IntakeWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except intakes.IntakeError as exc:
        # Not a brand-new record like `create_intake`'s catch above - this one
        # already exists, so anything `advance` still refuses is a real state
        # conflict (not `quoted`, most likely a second send) rather than a
        # write failure, and 409 says that plainly instead of implying a 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class IntakeLink(BaseModel):
    """Just the link. Not `IntakeIssued`, which carries a whole intake record
    the caller is already holding - and carrying the record again would make
    this route look like a way to read an intake, which it is not. It is a way
    to read one secret, and the shape says so."""

    link: str = ""


@app.get("/api/intakes/{intake_id}/link", response_model=IntakeLink, tags=["intakes"])
async def read_intake_link(request: Request, intake_id: str) -> IntakeLink:
    """The client's CURRENT link, without minting a new one.

    Admin-only, the same side of the line as `relink_intake` - this hands back
    the bearer credential that opens an unauthenticated route, and the fact
    that reading is less destructive than reissuing does not make it less
    sensitive. If anything the gate matters more here: `relink` at least leaves
    evidence, because the old link stops working and somebody notices.

    WHY THIS EXISTS WHEN `relink` LOOKED LIKE ENOUGH. Reissuing is how a studio
    was meant to recover a link, and it works exactly once - the first time,
    before the client has it. After that, pressing Reissue to get a copy of the
    link kills the copy the client is holding. A studio wanting to forward the
    same link to a second person at the client had no way to do it that did not
    break the first person's.

    THE QUEUE STILL CARRIES NO LINKS. That property is `Intake.token`'s
    `exclude=True` and it is untouched: `list_intakes` and `read_intake` return
    exactly what they always did, so a screenshot of the queue, an export, or a
    member reading the list still discloses nothing. This is a separate,
    deliberate, single-purpose call that an admin has to make on purpose, for
    one intake at a time. That is a different posture from putting the token in
    the list, and it is the one that keeps the original reasoning intact.

    404 rather than 403 for a closed or expired intake, and rather than
    answering an empty string: `close()` blanks the token on the record, so
    there is genuinely nothing to hand back, and a route that answers `{"link":
    ""}` invites a caller to paste an empty string into an email.
    """
    _require_admin(request, "Only an admin of this workspace can read a client's link.")
    entry = intakes.get(intake_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    if not entry.token:
        raise HTTPException(
            status_code=404,
            detail="That request has no live link. Closing one ends its link for good.",
        )
    # Never logged, at any level. The one rule this whole feature has about
    # tokens, and a `logger.info("handed back %s", link)` here would undo the
    # `exclude=True` that every other route relies on.
    return IntakeLink(link=_client_link(entry.token))


@app.post("/api/intakes/{intake_id}/relink", response_model=IntakeIssued, tags=["intakes"])
async def relink_intake(request: Request, intake_id: str) -> IntakeIssued:
    """Reissue a client's link, killing the one before it outright - a link a
    client lost, or one a studio wants to resend once `intakes.LIFETIME_DAYS`
    is getting close. Admin-only, the same side of the line as minting the
    first link in the first place.

    Existence is checked here, ahead of the call, for the same reason
    `close_intake` checks it first: `intakes.relink` raises plain
    `IntakeError` for "does not exist" and for "closed, nothing to reissue"
    alike, and only the second of those is this route's business to report
    once existence is no longer in question. `IntakeWriteError` - `_write`'s
    own wrapped `OSError` - is caught ahead of that generic case for the same
    reason `_client_advance` catches it first: a failed save is not a refusal
    and must not be reported as one.
    """
    _require_admin(request, "Only an admin of this workspace can reissue a client's link.")
    if intakes.get(intake_id) is None:
        raise HTTPException(status_code=404, detail="That request does not exist.")
    try:
        reissued = intakes.relink(intake_id)
    except intakes.IntakeWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except intakes.IntakeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _issued(reissued)


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
#
# The rate limit and the body-size check that guard these three routes are
# defined here, beside the routes they describe, but are *called* from
# `_gate` itself, well above this point in the file - see the comment there.
# That is a forward reference in reading order, not in execution order:
# nothing calls `_gate` until the server is already answering requests, by
# which time this whole module, including every name below, has finished
# executing. Python resolves a function's globals at call time, not at
# `def` time, so this is the same kind of reference `_gate` already makes to
# names in other modules (`auth.required()`, `workspaces.use()`) that are
# equally not defined at the top of this file.

#: The path suffixes `_gate` rate-limits and body-caps. Matched against the
#: URL's own last segment, not against FastAPI's resolved route - `_gate`
#: runs ahead of routing, so it has no resolved route yet to ask.
_CLIENT_WRITE_ROUTES = {"submit", "revise", "finalize"}

#: Comfortably above the worst legal `/submit` body: `scope` and
#: `budget_text` can each run to `config.MAX_BRIEF_CHARS` (20,000)
#: characters, `client_email`/`client_phone` to 254 each - roughly 40,500
#: characters of content before JSON's own quoting and escaping, which for
#: multi-byte UTF-8 could run several bytes per character. 200,000 bytes
#: leaves wide margin for that without coming close to admitting a
#: deliberately oversized body.
_MAX_CLIENT_BODY_BYTES = 200_000

#: What both halves of that cap answer with - the declared-length refusal and
#: the streamed one. One sentence, no number in it: a caller who is being told
#: their body is too large has no business being told exactly where the line
#: is, and a client who hit it by accident is helped by the form's own copy
#: rather than by this.
_TOO_LARGE = "That request is too large."


def _client_body_limit(request: Request, route: str) -> int:
    """How many bytes this client write may weigh on the wire.

    A JSON submit and a file upload cannot share one number. `/submit` carries
    four text fields today and `_MAX_CLIENT_BODY_BYTES` is sized for exactly
    that; the same route carrying a scope in Word and a photograph of a site is
    three orders of magnitude past it. So the wider allowance is granted on two
    conditions together, not one: the content type is `multipart/form-data`,
    *and* the route is `/submit`. `/revise` and `/finalize` will never carry a
    file - one takes a sentence and the other takes nothing at all - and a
    caller who simply declares a multipart content type on either of them must
    not thereby buy a hundredfold more room than the route could ever use.

    The wide allowance is the two numbers added rather than a third constant,
    because a multipart submit is literally the two things added: the same four
    fields a JSON submit sends, plus the files, plus a boundary line and a
    couple of headers per part. `config.MAX_CLIENT_UPLOAD_TOTAL_BYTES` is what
    the files themselves are allowed to be, and the handler checks them against
    it again once `/submit` accepts files - so the slack left here for the
    envelope buys a caller nothing except the right to be refused later by a
    message that names the real reason. Until then this is the only place that
    number is enforced, and it is enforced on the wire, which is the half that
    had to exist first.

    Matched on the type alone, with the parameters cut off first: the boundary
    is a `; boundary=...` parameter on every real multipart request, and a
    naive `==` against the whole header would take the wider allowance away
    from every browser on earth while leaving it available to anyone who sent
    the bare type by hand.
    """
    if route != "submit":
        return _MAX_CLIENT_BODY_BYTES
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "multipart/form-data":
        return config.MAX_CLIENT_UPLOAD_TOTAL_BYTES + _MAX_CLIENT_BODY_BYTES
    return _MAX_CLIENT_BODY_BYTES


#: Per-IP, per-route courtesy limiter for the three routes above. What this is
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
_rate_limit_hits: dict[tuple[str, str], deque] = {}

#: Hard cap on how many distinct `(ip, route)` buckets this dict ever holds
#: at once. The trim inside `_enforce_rate_limit` bounds each *bucket's* own
#: size (at most `_RATE_LIMIT_MAX_REQUESTS` timestamps, and dropped entirely
#: once every timestamp in it has aged out) - it does not bound how many
#: *buckets* accumulate, and a distinct source address is exactly what an
#: unauthenticated caller on this door controls. A bucket only gets trimmed
#: when its own key is looked up again; an address that sends exactly one
#: request and never returns leaves its bucket sitting in this dict,
#: untouched, for the rest of the process's life - nothing else ever
#: revisits it to notice the window has passed. Bounding *bucket count*
#: rather than relying on time-based expiry is what actually stops that:
#: once this many are tracked, the single oldest one (`dict` preserves
#: insertion order since Python 3.7, and updating an existing key's value
#: does not move it, so the first key in iteration order really is the
#: least recently first-seen) is evicted to make room for a new one. A
#: memory bound, not a fairness guarantee - an attacker generating enough
#: distinct addresses can evict a legitimate caller's own bucket early - but
#: it is what keeps this dict's size from growing without limit for the
#: life of the process, which the time-based trim alone does not.
_RATE_LIMIT_MAX_TRACKED_KEYS = 5_000


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
    minute. See `_rate_limit_hits`'s and `_RATE_LIMIT_MAX_TRACKED_KEYS`'s own
    comments for what this is not, and for the two different things it
    bounds - one bucket's own size, and how many buckets exist at all.

    A bucket already emptied by the trim below is dropped from the dict
    outright rather than left behind holding nothing - but that alone does
    *not* bound this dict's total size: this same call immediately
    recreates the bucket to record its own hit, so an address that is
    genuinely still writing never has its bucket disappear, and an address
    that never returns was never going to be looked up again regardless of
    whether its bucket sat there empty or absent. The bound that actually
    matters is `_RATE_LIMIT_MAX_TRACKED_KEYS`, enforced just below: once
    that many distinct `(ip, route)` pairs are tracked, the single oldest
    is evicted before a genuinely new one is added.
    """
    now = time.monotonic()
    key = (_client_ip(request), route)
    with _rate_limit_lock:
        hits = _rate_limit_hits.get(key)
        is_new_key = hits is None
        if hits is not None:
            while hits and now - hits[0] > _RATE_LIMIT_WINDOW_SECONDS:
                hits.popleft()
            if not hits:
                del _rate_limit_hits[key]
                hits = None

        if hits is not None and len(hits) >= _RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts from this address. Wait a minute and try again.",
            )

        if hits is None:
            if is_new_key and len(_rate_limit_hits) >= _RATE_LIMIT_MAX_TRACKED_KEYS:
                oldest_key = next(iter(_rate_limit_hits))
                del _rate_limit_hits[oldest_key]
            hits = _rate_limit_hits[key] = deque()
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
    reasons: "that move is not legal" (unknown id, wrong state, an unknown
    field) and "the write itself failed" (`_write`'s own wrapped `OSError` -
    a full disk, a sync client holding a lock) - the latter is
    `intakes.IntakeWriteError`, a dedicated subtype precisely so this
    function does not have to tell them apart by reading the exception's own
    message. Those are not the same answer. The first is a refusal, and
    every refusal on this door is the identical opaque 404. The second is
    not a refusal at all - it is this server failing to save a client's
    words - and folding it into "that link is gone" would tell somebody
    their submission was rejected when it was actually lost. Caught first,
    and ahead of the plain `IntakeError` below, since it is a subtype of it -
    listing them in the other order would let the broader `except` swallow
    it before this one ever ran. Deliberately not a pre-read of
    `entry.state` here to classify the failure instead: that would
    reintroduce the duplicated-check problem this function exists to avoid,
    for a distinction `intakes.py` can already make structurally.
    """
    try:
        return intakes.advance(intake_id, to, **fields)
    except intakes.IntakeWriteError as exc:
        logger.exception("Could not save a client write to intake %s (-> %s)", intake_id, to)
        raise HTTPException(
            status_code=500,
            detail="That could not be saved. Wait a moment and try again.",
        ) from exc
    except intakes.IntakeError:
        raise _client_write_refused() from None


def _normalise_client_kind(raw: str) -> str:
    """One of `kinds.BY_ID`, or empty for anything else.

    Membership, never a cast, and never trusted from the wire: this arrives
    from a stranger holding a link, and an id outside the set would reach
    `kinds.resolve` - which falls back to the default - and be stored on the
    record as a discipline nobody offers. Empty is the honest answer for "they
    did not choose", and `App.tsx`'s `readPreset` treats it exactly as it
    treats an absent one: the studio's own preset kind stands.
    """
    return (raw or "").strip().lower() if (raw or "").strip().lower() in kinds.BY_ID else ""


def _normalise_client_kind_label(raw: str) -> str:
    """The client's own word for an `other` discipline, bounded at
    `kinds.MAX_LABEL`.

    Bounded here as well as in the browser, because the browser's `maxLength`
    is a courtesy to whoever is typing and not a control on whoever is
    posting. The value is *sanitised* far downstream, in `prompts.kind_block`,
    which is where it meets the model - deliberately not here, so there is one
    place that decides what is safe to put in a prompt rather than two that
    have to agree.
    """
    return _CONTROL_CHARS.sub("", raw or "").strip()[: kinds.MAX_LABEL]


# --- What a stranger may attach, which is not what a studio may -------------
#
# `_read_images` and `_read_documents`, above, are the studio's own readers and
# this route deliberately does not call them. Three reasons, and none of them is
# a preference:
#
#   * `_read_images` hands back `(bytes, mime)` and drops the filename on the
#     floor. A manifest entry is `{id, name, kind, bytes, note}` and the name is
#     the only thing in it a studio can recognise the file by.
#   * `_read_documents` hands back an `Attachment` - name, kind, extracted text
#     - and drops the *bytes*. There is nothing left to store.
#   * `MAX_CLIENT_FILES` is one number across both fields. Neither reader can
#     see the other's list, so a cap that spans them cannot be enforced from
#     inside either, and splitting six files into three and three would buy a
#     caller twelve.
#
# What is reused is every idiom they established, because those were right and
# were arrived at the hard way: the loose `List[Union[UploadFile, str]]` typing
# (see `_read_images`' docstring for the two shapes of "no file was chosen" that
# reach a handler), the `BaseUploadFile` filter, refusing on the declared size
# before allocating anything, then reading one byte past the limit so an
# understated size cannot get past it either, and closing every upload in a
# `finally`.


class _ClientFile(NamedTuple):
    """One file a client attached, as far as this door is concerned: what they
    called it, what PRISM decided it is, and the bytes that arrived."""

    #: Already through `intakefiles.clean_name` - the same string that will be
    #: written onto the record, so a refusal and a manifest entry cannot name
    #: the file differently.
    name: str
    #: The canonical content type from `intakefiles.resolve_type` - never the
    #: raw `Content-Type` the browser sent, and never a bare extension.
    kind: str
    data: bytes


#: A macro-enabled workbook, subtracted below and named here so the subtraction
#: reads as a decision rather than an omission.
#:
#: This is the type that shows what deriving a set costs. Everything else in
#: `CONTENT_TYPES` is a document or a picture; this one is a program with a
#: spreadsheet wrapped round it, and it was in the accepted set purely because
#: nobody had to type its name to put it there. `Content-Disposition:
#: attachment` is no defence at all against it - the download *is* the delivery.
#: A stranger with one issued link sends `requirements.xlsm` with an auto-open
#: macro, the queue lists it as an ordinary attachment, a studio member opens it
#: and clicks Enable Content, and it is their machine.
#:
#: A client with a spreadsheet has `.xlsx`, which carries every cell of the same
#: data and cannot carry the macro. Nothing legitimate is lost, and the studio's
#: own authenticated pad is unaffected - `_read_documents` still takes what it
#: always took. That asymmetry is the point: this door is the one with a
#: stranger on the other side of it.
_MACRO_SPREADSHEET = "application/vnd.ms-excel.sheet.macroEnabled.12"

#: Every content type a client's own file may be stored as. Derived from
#: `intakefiles.CONTENT_TYPES` rather than written out again beside it: that
#: table is what the store knows how to keep, hand back and label, and a second
#: copy here would be a thing to forget. `application/octet-stream` is
#: subtracted because it is not a type - it is `intakefiles`' word for "no
#: idea", the answer `resolve_type` gives when neither the declared type nor
#: the extension said anything it recognised. A file arriving as "no idea" is
#: precisely what an anonymous door should not be storing, so on this path the
#: fallback *is* the refusal. And `_MACRO_SPREADSHEET`, for the reason above.
#:
#: The raster half of the set is `intakefiles.INLINE_TYPES`, imported rather
#: than restated for the same reason, and it is the closed allowlist the plan
#: requires: `_read_images` admits anything matching `image/`, which includes
#: `image/svg+xml`, and an SVG is a script document that the studio will later
#: open. The gate is on the **declared** type and structurally cannot be on a
#: resolved-from-filename one, because `resolve_type` refuses to let a suffix
#: resolve into `INLINE_TYPES` at all - see its docstring. So a file only ever
#: reaches the raster set by being declared one of them, and `_looks_like`
#: below is what decides whether the declaration was true.
_CLIENT_TYPES = frozenset(intakefiles.CONTENT_TYPES) - {
    intakefiles.FALLBACK_TYPE,
    _MACRO_SPREADSHEET,
}

#: Which of `attachments.py`'s readers each accepted document's extension names,
#: and which one its content type names - the same map read from both sides, so
#: the two can be compared.
#:
#: Two things depend on this and they are worth separating. The first is that a
#: document's extension has to *agree* with its declared type rather than merely
#: be recognised: `scope.txt` carrying PDF bytes and declaring
#: `application/pdf` resolves to `application/pdf` correctly, but
#: `attachments.read` keys its reader off the name, so the text reader would run
#: over PDF bytes, decode them as mojibake, find "text" and report no problem at
#: all. The manifest would then assert a clean read of a file nothing read, and
#: Task 6 would feed the model the same noise. Comparing readers rather than
#: strings is what catches that while still allowing the ordinary disagreements
#: - a `.md` a browser declared `text/plain` is a text file either way.
#:
#: The second is that this is derived from `_CLIENT_TYPES`, so subtracting the
#: macro type above closes the extension road too: `.xlsm` is no longer a key
#: here, so a macro workbook cannot get in by declaring the plain spreadsheet
#: type either - which matters because the studio downloads a file under the
#: *client's* name, and `budget.xlsm` opens in Excel as a macro workbook whatever
#: this server stored it as.
#:
#: `SUFFIXES[suffix]` is indexed rather than `.get`, so a document type added to
#: `intakefiles.CONTENT_TYPES` that no reader in `attachments.py` knows about
#: fails here, at import, with a `KeyError` naming the suffix. That is the
#: intended failure and not an oversight: the alternative is that the new type
#: is quietly unacceptable on this route for ever, which nobody would notice
#: until a client's file was refused. Import happens on every start and every
#: check script, so whoever added the type finds out immediately.
_DOCUMENT_READER = {
    suffix: attachments_module.SUFFIXES[suffix]
    for kind, suffix in intakefiles.CONTENT_TYPES.items()
    if kind in _CLIENT_TYPES and kind not in intakefiles.INLINE_TYPES
}

#: What each of those types actually begins with: a tuple of `(offset,
#: alternatives)` pairs, all of which must match.
#:
#: This is the check that refuses an `.exe` renamed `.pdf`, and it is here
#: rather than left to `attachments.py` on purpose. Both the filename and the
#: `Content-Type` on a multipart part are chosen by whoever is uploading -
#: neither is evidence of anything - so without this, the only thing standing
#: between a client and a Windows executable stored as `application/pdf` is that
#: the studio has to double-click it. `attachments.read` would notice the file
#: was unopenable, but it reports rather than refuses (its module docstring is
#: explicit, and that rule is right for a studio's own tender pack), and reading
#: its answer back out of a message string is exactly the string-matching
#: `_client_advance` was careful not to do.
#:
#: Deliberately container markers only - the thing that says "this is a PNG at
#: all", not a survey of encoders. It is a door check, not a validator: what it
#: has to stop is a file whose bytes are a different *kind of thing* from what
#: it claims, and anything subtler than that is the reader's problem downstream
#: where it is already handled.
#:
#: The three text types are absent, and that is the whole rule for them: plain
#: text, CSV and Markdown have no signature to check, so there is nothing here
#: that could be true or false about them. What that lets through is an
#: executable renamed `.txt`, which is a file that downloads as `.txt`, opens in
#: a text editor and runs nowhere - the case this table exists for is the one
#: where the extension makes it double-clickable.
#:
#: `_MACRO_SPREADSHEET` is absent too, for a different reason: it is refused at
#: the type gate, so `_looks_like` can never be called with it, and an entry for
#: it would be a line nothing could ever execute. This table covers exactly what
#: this door accepts, not everything `intakefiles` can store.
_SIGNATURES: dict[str, tuple[tuple[int, tuple[bytes, ...]], ...]] = {
    "image/png": ((0, (b"\x89PNG\r\n\x1a\n",)),),
    "image/jpeg": ((0, (b"\xff\xd8\xff",)),),
    "image/gif": ((0, (b"GIF87a", b"GIF89a")),),
    # RIFF container, with the form type four bytes after the length.
    "image/webp": ((0, (b"RIFF",)), (8, (b"WEBP",))),
    # ISO base media, whose box header puts `ftyp` at offset 4. The brand that
    # follows (`heic`, `heix`, `mif1`) is not checked: the brands are a moving
    # list and the container is the part that decides what the file is.
    "image/heic": ((4, (b"ftyp",)),),
    "application/pdf": ((0, (b"%PDF-",)),),
    # A `.docx`/`.xlsx` is a zip, and a real one always begins with a local file
    # header. The empty-archive and spanned-archive markers are not accepted:
    # neither could carry a document, and `attachments.py`'s own bound is what
    # deals with an archive that is real but hostile.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        (0, (b"PK\x03\x04",)),
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ((0, (b"PK\x03\x04",)),),
}


def _looks_like(kind: str, data: bytes) -> bool:
    """Whether these bytes begin the way this content type has to.

    A type with no entry in `_SIGNATURES` has no signature to disagree with and
    is let through - see that table's own comment for which types those are and
    why that is the right answer for them rather than an omission.
    """
    for offset, markers in _SIGNATURES.get(kind, ()):
        if not any(data[offset : offset + len(marker)] == marker for marker in markers):
            return False
    return True


def _suffix_of(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


#: What a file this route will not take is answered with. It names the file,
#: because a client with four attachments needs to know which one to remove, and
#: it names what would be taken instead rather than only what would not - the
#: person reading it is filling in a form, not debugging a mime type.
_WRONG_KIND = (
    "'{name}' is not a file PRISM can take. Attach a PDF, a Word document, a "
    "spreadsheet, a CSV or a text file - or a photo or screenshot as PNG, JPEG, "
    "WebP, GIF or HEIC."
)

#: The macro workbook's own answer, because the general one above would read as
#: wrong to the only person who ever sees it: it says a spreadsheet is fine, and
#: they are holding a spreadsheet. This is also the one refusal in the set with
#: a remedy the client can carry out in one step, so it says what that is
#: instead of listing everything they could have sent instead.
_MACRO_REFUSED = (
    "'{name}' is a macro-enabled workbook, and PRISM does not take those. Save "
    "it as .xlsx and attach that - it keeps every cell, without the program."
)


async def _read_client_files(
    images: List[Union[UploadFile, str]] | None,
    documents: List[Union[UploadFile, str]] | None,
) -> List[_ClientFile]:
    """Everything a client attached, validated, or the 400 that says why not.

    Both fields are walked as one list. Which input a file arrived in is a hint
    from a picker this server did not write and cannot trust - what decides how
    a file is treated is what it resolves to, and the caps are counted across
    the pair because a cap counted per field is two caps.

    Unlike `_read_documents`, this refuses rather than reports. That is not a
    contradiction of `attachments.py`'s rule and the difference is worth being
    precise about: an unreadable file is still *reported* here, on the manifest
    entry's `note`, exactly as that module requires. What is refused is a file
    of a kind this route will not store at all - which is a decision about the
    door, made while there is still a person standing at it to be told.
    """
    # Images before documents, matching this route's own signature and
    # `create_proposal`'s reading order. It has to be *some* order and it cannot
    # be the client's: a browser sends one interleaved sequence of parts and
    # FastAPI has already split it into two lists by the time a handler sees it,
    # so the order somebody picked their files in is gone before this line.
    candidates = [
        upload
        for field in (images, documents)
        for upload in (field or [])
        if isinstance(upload, BaseUploadFile) and (upload.filename or "").strip()
    ]
    if not candidates:
        return []

    if len(candidates) > config.MAX_CLIENT_FILES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(candidates)} files attached. You can send up to "
                f"{config.MAX_CLIENT_FILES} - remove the rest and submit again."
            ),
        )

    files: List[_ClientFile] = []
    total = 0
    limit_mb = config.MAX_CLIENT_FILE_BYTES / (1024 * 1024)
    total_mb = config.MAX_CLIENT_UPLOAD_TOTAL_BYTES / (1024 * 1024)

    for upload in candidates:
        # Cleaned here rather than left to `save()`, and the cleaned string is
        # what everything downstream uses - the checks below, the refusals that
        # name the file, and the manifest entry. `clean_name` caps at 120
        # characters and strips control characters and separators, so a caller
        # cannot put a multi-kilobyte name with a newline in it into a response
        # body by being refused instead of accepted, and the file is called one
        # thing rather than two.
        name = intakefiles.clean_name(upload.filename or "")
        declared = (upload.content_type or "").split(";")[0].strip().lower()
        # The store's own resolution, not a second copy of it: the declared type
        # wins when PRISM knows it, the extension is consulted only when it said
        # nothing, and an extension can never resolve into the raster allowlist.
        kind, _stored_as = intakefiles.resolve_type(declared, name)

        # Two refusals with one message, because they are one thing to the person
        # reading it: PRISM does not take that. The second clause is the
        # document whose extension and declared type do not name the same
        # reader - including the document with no usable extension at all, which
        # `_DOCUMENT_READER.get` answers `None` for. See that map's own comment
        # for what each half of it is holding up.
        if kind not in _CLIENT_TYPES or (
            kind not in intakefiles.INLINE_TYPES
            and _DOCUMENT_READER.get(_suffix_of(name))
            != _DOCUMENT_READER[intakefiles.CONTENT_TYPES[kind]]
        ):
            await upload.close()
            # Both roads into the macro refusal get the message about macros:
            # the type, when a browser declared it, and the extension, when it
            # declared an ordinary spreadsheet or nothing at all. Which of the
            # two clauses above caught it is not something the person reading
            # the sentence should have to know.
            answer = (
                _MACRO_REFUSED
                if kind == _MACRO_SPREADSHEET or _suffix_of(name) == ".xlsm"
                else _WRONG_KIND
            )
            raise HTTPException(status_code=400, detail=answer.format(name=name))

        # Refused on what the part declares before anything is allocated for it,
        # exactly as `_read_images` does and for the reason its own comment
        # gives.
        declared_size = getattr(upload, "size", None)
        if declared_size is not None and declared_size > config.MAX_CLIENT_FILE_BYTES:
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {declared_size / (1024 * 1024):.1f} MB. The limit is "
                    f"{limit_mb:.0f} MB per file."
                ),
            )

        try:
            # One byte past the limit is all the check below needs, and a part
            # that understated its own size does not get past it either.
            data = await upload.read(config.MAX_CLIENT_FILE_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - whatever the spool did, the client hears one thing
            logger.warning("Could not read the client's upload %s: %s", name, exc)
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' could not be read. Attach it again and submit.",
            ) from exc
        finally:
            await upload.close()

        if not data:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is empty. Attach it again and submit.",
            )
        if len(data) > config.MAX_CLIENT_FILE_BYTES:
            # The read stopped one byte over, so the real size is not known here
            # - say so rather than quote the cut-off as if it were a measurement.
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is larger than the {limit_mb:.0f} MB limit per file.",
            )

        # The aggregate, measured on what actually arrived. `_gate` already
        # bounded the *body* against this same number, and this is deliberately
        # not a duplicate of that check: the body is the envelope and this is
        # what is inside it, so a caller cannot get past the second by arranging
        # the first. See `config.MAX_CLIENT_UPLOAD_TOTAL_BYTES`'s own comment.
        total += len(data)
        if total > config.MAX_CLIENT_UPLOAD_TOTAL_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Those files come to more than {total_mb:.0f} MB together. Send fewer, "
                    "or smaller ones."
                ),
            )

        if not _looks_like(kind, data):
            raise HTTPException(status_code=400, detail=_WRONG_KIND.format(name=name))

        files.append(_ClientFile(name, kind, data))

    return files


def _store_client_files(intake_id: str, files: List[_ClientFile]) -> List[dict]:
    """Extract, save, manifest - in that order, and all of it off the loop.

    Every line of this is blocking, which is why it is a plain `def` called
    through `asyncio.to_thread` (the idiom this file already uses for
    `mailer.send_invite`) rather than inlined into an `async def`.
    `attachments.read` opens a PDF or unpacks a zip, which is CPU-bound and can
    be seconds on a large one; `intakefiles.save` is a network round trip per
    file once Spaces is configured, and six files is six of them. Held on the
    event loop that would park every other request this worker has, including
    another client's `/submit`.

    One hop for the whole batch rather than one per file: the borrow is a
    `ContextVar` and `to_thread` runs the call in a copy of the current context,
    so the workspace this was borrowed under is what the thread saves into.

    Raises `intakefiles.IntakeFileError` - the caller turns it into a 500,
    because a client whose file did not reach the bucket has had their
    submission lost, not refused.
    """
    manifest: List[dict] = []
    for entry in files:
        # The extraction warning, and only for the kinds there is one for.
        # `attachments.read` keys on the suffix and would answer "not a file
        # type PRISM can read" for a photograph, which is true and useless: an
        # image reaches the model as an image, and nothing was ever going to
        # read text out of it.
        note = (
            ""
            if entry.kind in intakefiles.INLINE_TYPES
            else attachments_module.read(entry.name, entry.data).problem
        )
        manifest.append(
            intakefiles.save(intake_id, entry.name, entry.data, entry.kind, note=note)
        )
    return manifest


class ClientReviseRequest(BaseModel):
    """What the client asked to change, in their own words."""

    asked: str = ""


@app.post("/api/client/{token}/submit", response_model=None, tags=["client"])
async def submit_client_intake(
    token: str,
    client_email: str = Form(""),
    client_phone: str = Form(""),
    scope: str = Form(""),
    budget_text: str = Form(""),
    #: Which discipline the client says this is, and - for `other` alone -
    #: their own word for it. Plain `str` for the same reason `Intake.state`
    #: is: the closed set lives in `kinds.BY_ID` and is enforced by
    #: `_normalise_client_kind`, not on the way in, so an unknown id is
    #: answered as the empty string rather than as a 422 naming every kind
    #: this build happens to know.
    client_kind: str = Form(""),
    client_kind_label: str = Form(""),
    #: Typed as loosely as the studio's own upload routes, and for the reason
    #: `_read_images`' docstring gives: a browser posts an empty file input as
    #: a part with no filename, and a strict `List[UploadFile]` makes FastAPI
    #: refuse the whole request with an unreadable 422 before any handler runs
    #: - which on this route would be a client with nothing attached being told
    #: their form is malformed.
    images: List[Union[UploadFile, str]] = File(default=[]),
    documents: List[Union[UploadFile, str]] = File(default=[]),
) -> dict:
    """The client's own words and their own files, written once, from `issued`.

    Multipart rather than JSON since Task 3: the four fields are `Form`, and the
    two file lists are the studio's own field names so one picker's split - what
    goes to the model as an image, what goes as text - reads the same on both
    sides. Which of the two a file arrived in is only a hint, though; see
    `_read_client_files`.

    There is no studio identity behind this call - just whoever is holding the
    link - so `intakes.advance`'s transition table is the entire abuse control:
    a second call, from `submitted` or anywhere past it, is refused exactly as a
    call against a token that never resolved is. All four text fields are
    bounded: `_normalise_scope`/`_normalise_budget_text` are the same the
    studio's own `/api/intakes` route uses, and `_normalise_client_email`/
    `_normalise_client_phone` apply the identical idiom to the two fields that
    used to get only a bare `.strip()` - see their own docstrings.

    No `Request` parameter, and no `_enforce_rate_limit` call here - unlike the
    first cut of this route. `_gate` (main.py, well above this point) now makes
    that check itself, ahead of routing, so it runs before FastAPI reads or
    parses this request's body at all rather than after. See the comment on that
    clause for why a check placed here, inside the handler, can never be truly
    first.
    """
    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    # The words first, and the files after. Deliberately in that order: an
    # over-length scope is refused by a string comparison, and doing it before a
    # single byte is copied out of the multipart spool means the commonest
    # refusal costs nothing and - the part that matters - cannot leave anything
    # behind in storage.
    said_email = _normalise_client_email(client_email)
    said_phone = _normalise_client_phone(client_phone)
    said_scope = _normalise_scope(scope)
    said_budget = _normalise_budget_text(budget_text)
    said_kind = _normalise_client_kind(client_kind)
    # The same question the studio's own pad is asked, on the same door the
    # structural floor above guards - because the floor cannot reach the case
    # that made this necessary. "erwerasdad dklajdlaksdjacsdasd" is thirty
    # characters, thirteen distinct letters and two words; only reading it
    # tells it from a scope.
    #
    # HERE, rather than inside a job, because there is no job: `/submit` is the
    # client's whole interaction and it answers synchronously. The call runs in
    # a worker thread (`anyio.to_thread` inside `check_brief_is_real`), so the
    # event loop is not parked while it waits.
    #
    # BEFORE `_read_client_files` below, which is what makes a refusal free: no
    # part is parsed, no bytes are held, nothing reaches Spaces, and - the half
    # that matters - `advance()` is never called, so the intake stays `issued`
    # and the client's one write is NOT spent. That last point is why this is
    # safe to put on an anonymous door at all. A refused client retypes and
    # sends again on the same link; they do not lose it. Every case in
    # check_client_api.py asserts exactly that, for this refusal as for the
    # structural ones.
    #
    # Fails open like every other caller of this function - see its docstring.
    # A studio would rather read one nonsense enquiry than have a model outage
    # quietly stop their clients submitting anything at all.
    if config.CHECK_BRIEF_IS_REAL:
        verdict = await check_brief_is_real(said_scope)
        if not verdict.is_brief:
            raise HTTPException(
                status_code=400,
                detail=(
                    verdict.reason
                    or "That does not read as a description of work. Say what you need, "
                    "who it is for, and anything that matters about how it should work."
                ),
            )
    # Read for `other` alone, exactly as `prompts.kind_block` reads it: every
    # other kind carries its own name already, so a label sent alongside one
    # of them is a word nothing will ever use, and storing it would put a
    # discipline on the record that the studio would reasonably believe was
    # chosen. Dropped rather than stored-and-ignored.
    said_label = (
        _normalise_client_kind_label(client_kind_label) if said_kind == kinds.OTHER.id else ""
    )

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        files = await _read_client_files(images, documents)
        manifest: List[dict] = []

        if files:
            # An orphan filter, and explicitly **not** a second authority on
            # whether this write may happen. `_client_advance`'s own docstring
            # says why a duplicated state check is a bad idea and it still
            # holds: `intakes.advance` makes that decision atomically under its
            # own lock, this does not, and the refusal below is
            # `_client_write_refused()` - the identical opaque 404 - precisely
            # so no caller can tell which of the two answered.
            #
            # What it buys is the thing storage made new. Bytes are written
            # before the record can be moved (see below for why that order and
            # not the other), so a submit that was always going to be refused -
            # a used link, a closed intake, a second click - would otherwise put
            # up to six files in a bucket with nothing on any record pointing at
            # them, on an anonymous route, as often as they cared to try.
            # Reading the state first turns the *sequential* case of that into
            # no write at all, which is the case that actually happens: a client
            # who clicks twice, a browser that retries, a link forwarded to
            # somebody who tries it after the fact.
            #
            # What it does not bound is concurrency, and the arithmetic is worth
            # writing down rather than being reassured by. There is an `await`
            # between this read and the advance below - the `to_thread` that
            # does the saving - so every request in flight at once passes this
            # check before any of them reaches `advance()`. Twenty simultaneous
            # submits against one live link, which the per-address limiter
            # permits in a minute, is nineteen losers times six files times six
            # megabytes: on the order of 700 MB stored with nothing pointing at
            # it and no per-file delete to take it back. This check is an orphan
            # *filter*, not a bound, and nothing here is a defence against a
            # caller who is trying - see `_rate_limit_hits`' own comment for the
            # same disclaimer about the only other control on this door.
            #
            # Closing the window would mean claiming the intake atomically
            # before the files exist, and the only thing in this codebase that
            # can claim one is `advance()` itself - which is the manifest-first
            # ordering rejected below, for a worse reason than this one.
            entry = intakes.get(intake_id)
            if entry is None or entry.state != intakes.ISSUED:
                raise _client_write_refused()

            try:
                manifest = await asyncio.to_thread(_store_client_files, intake_id, files)
            except intakefiles.IntakeFileError as exc:
                # Not this door's opaque 404. Their submission was lost, not
                # refused, and the two must not read the same - the same
                # distinction `_client_advance` draws for a failed `_write`.
                #
                # A batch that failed halfway leaves whatever landed before it
                # did, and those are orphans too. They are not re-listed here
                # because `intakefiles.save` logs every file it stores, by id
                # and by intake, so the log already has them - and this
                # function never learns their ids, since the exception is what
                # came back instead of the manifest.
                logger.exception("Could not store a client's files for intake %s", intake_id)
                raise HTTPException(
                    status_code=500,
                    detail="That could not be saved. Wait a moment and try again.",
                ) from exc

        try:
            moved = _client_advance(
                intake_id,
                intakes.SUBMITTED,
                client_email=said_email,
                client_phone=said_phone,
                scope=said_scope,
                budget_text=said_budget,
                client_kind=said_kind,
                client_kind_label=said_label,
                attachments=manifest,
                #: The one moment nothing else on the record captures. Every
                #: other timestamp here belongs to the studio - when the link
                #: was minted, sent, closed - and this is the client's.
                submitted_at=storage.utc_now_iso(),
            )
        except HTTPException:
            # Save, then advance - and this is the failure that ordering leaves.
            # The other order is worse: a manifest written first would point at
            # files that then failed to arrive, on a record that can never be
            # re-submitted, and the client would be told everything went fine.
            # An orphan is bytes nobody is looking at; the alternative is a
            # record that lies.
            #
            # Nothing is deleted here, and that is deliberate rather than
            # unfinished. `intakefiles.forget()` is the only removal the store
            # offers and it deletes the intake's **whole prefix** - so calling
            # it on the path that actually reaches this line, which is
            # overwhelmingly "somebody else already submitted", would delete the
            # legitimate submission's files. Cleaning up would need a per-file
            # delete that Task 2's interface does not have; until it does, the
            # ids are logged so a bucket audit has something to match against.
            if manifest:
                logger.error(
                    "Orphaned %d stored file(s) for intake %s - the submission was refused "
                    "after they were saved: %s",
                    len(manifest),
                    intake_id,
                    ", ".join(entry["id"] for entry in manifest),
                )
            raise

        # `submitted` is one of `clientview.of`'s waiting states, which needs
        # no bundle - passing none is correct, exactly as it is for `issued`.
        return clientview.of(moved)
    finally:
        workspaces.give_back(borrowed)


@app.post("/api/client/{token}/revise", response_model=None, tags=["client"])
async def revise_client_intake(token: str, body: ClientReviseRequest) -> dict:
    """Ask for a change. Accepted only from `sent`, and only once at a time -
    a second ask before the studio has re-quoted moves the record to
    `revision_requested`, which is not `sent`, so `intakes.advance` refuses
    it the same as any other wrong-state call.

    `revisions` is a log `advance()` overwrites wholesale rather than appends
    to (see `ADVANCE_FIELDS`'s own docstring), so the full, updated list is
    built here from the record's current one and handed in whole.

    No `Request` parameter and no `_enforce_rate_limit` call - see
    `submit_client_intake`'s docstring for why: `_gate` makes this check now,
    ahead of routing and body parsing.
    """
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
        try:
            return clientview.of(moved, bundle)
        except ValueError:
            # `clientview.of` raises for a quoted-face state with no bundle
            # attached (see its own docstring) - reachable here because
            # nothing in `intakes.py` keeps `sent_bundle_id` pointing at a
            # real bundle: `DELETE /api/proposals/{id}` can remove one out
            # from under a `sent` intake today, and `QUOTED -> SENT` never
            # required the reverse. `moved` is already persisted at this
            # point - the transition itself cannot be undone - so this
            # answers the same opaque refusal `read_client_view` gives for
            # the identical failure, rather than a 500 with a traceback.
            logger.exception(
                "clientview.of could not show intake %s after revise - dangling bundle?",
                intake_id,
            )
            raise _client_write_refused() from None
    finally:
        workspaces.give_back(borrowed)


@app.post("/api/client/{token}/finalize", response_model=None, tags=["client"])
async def finalize_client_intake(token: str) -> dict:
    """The client accepts what was sent. Not a signature and nothing is
    charged - that framing lives on the client's own screen (Task 8), not
    here. Accepted only from `sent`. Tells the intake's own author and every
    admin, by an explicit recipient list rather than a role alone, since
    whoever issued this particular request should hear about it even if they
    do not administer the workspace.

    No `Request` parameter and no `_enforce_rate_limit` call - see
    `submit_client_intake`'s docstring for why: `_gate` makes this check now,
    ahead of routing.
    """
    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        moved = _client_advance(intake_id, intakes.FINALIZED)
        bundle = storage.get(moved.sent_bundle_id) if moved.sent_bundle_id else None

        # Built and validated *before* the notification below runs, on
        # purpose. `moved` is already persisted at this point - the
        # transition itself cannot be undone - but nothing external has been
        # told anything yet. `clientview.of` raises `ValueError` for a
        # quoted-face state with no bundle attached (see its own docstring),
        # reachable here for the same reason `/revise` guards it above:
        # nothing in `intakes.py` keeps `sent_bundle_id` pointing at a real
        # bundle, and `DELETE /api/proposals/{id}` can remove one out from
        # under a `sent` intake today. Failing *here*, ahead of
        # `inbox.notify`, is the point: the worst case is a client seeing
        # this door's ordinary opaque refusal on a state that quietly did
        # move - not the studio being told a quotation was accepted while
        # the client who "accepted" it stares at an error. Calling
        # `clientview.of` bare, or guarding it only after notifying, would
        # produce exactly that inversion.
        try:
            view = clientview.of(moved, bundle)
        except ValueError:
            logger.exception(
                "clientview.of could not show intake %s after finalize - dangling bundle?",
                intake_id,
            )
            raise _client_write_refused() from None

        # Resolved against the roster at write time, the same as every other
        # notification in this file - see `inbox.py`'s own docstring on why.
        # A plain union of two sets rather than two separate `notify()`
        # calls, so an admin who also happens to be `created_by` is told
        # once, not twice.
        roster = members.listing()
        roster_emails = {(member.email or "").strip().lower() for member in roster}
        admin_emails = {
            (member.email or "").strip().lower()
            for member in roster
            if member.role == members.ADMIN
        }
        recipients = set(admin_emails)
        created_by = (moved.created_by or "").strip().lower()
        if created_by and created_by in roster_emails:
            recipients.add(created_by)

        words = {
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
        }
        if recipients:
            inbox.notify("intake.finalized", list(recipients), words)
        if created_by and created_by not in roster_emails:
            # `created_by` is no longer on this workspace's team.
            # `inbox.notify`'s list-audience path (`inbox._people`) only
            # ever matches names against the *current* roster, so a former
            # member would otherwise hear nothing about a request they
            # personally took in - silently breaking this handler's own
            # promise, stated above, that the author is told "even if they
            # do not administer the workspace" (a departed member does not
            # administer it either, and this is the sharper case of the
            # same promise). `inbox.deliver` writes straight to a named
            # person's file with no roster check at all, which is exactly
            # what is needed here - and cannot double-notify: this branch
            # and the `recipients`-based call above are mutually exclusive
            # by construction, since `created_by in roster_emails` decides
            # which one this person can ever reach.
            inbox.deliver(inbox.key_for(created_by), "intake.finalized", words)

        return view
    finally:
        workspaces.give_back(borrowed)


# --- The client reads the quotation (Stage 2 Task 5) ---------------------------
#
# One more door on the same anonymous prefix as Tasks 3 and 4: no
# `Authorization` header, resolved by the token alone. PRISM writes two
# documents from every estimate it prices - the one a client is meant to
# read, and the studio's own internal one beside it, with the stack, the
# API surface, the phases and the open questions written for whoever builds
# the thing, not whoever is buying it. Every file route above this section
# (`download_markdown`, `printable_html`, `download_pdf`) takes which of the
# two to serve as a `{kind}` path segment, because the caller there is a
# signed-in studio member choosing on purpose. A client did not choose
# anything - they were sent one link - and this door must not let them turn
# that link into a way to read the other document. See `_client_quotation`'s
# own docstring for how that is enforced, not merely asserted.

#: The only three states in which a client may read the quotation they were
#: sent. Deliberately not `clientview._QUOTED_FACE`, reused: that set
#: governs what `clientview.of` renders a bundle *for*, and tying this
#: door's own refusal to it would mean a state added there for a reason
#: that has nothing to do with reading a document silently widens what this
#: route serves too. An allow-list, not the denylist `read_client_view`'s
#: own `CLOSED` check is - that route shows *something* for every state but
#: one; this route shows a document for three of the ten states
#: `intakes.ALLOWED` names, so `closed` needs no check of its own here: it
#: was simply never on the list, the identical answer every other unlisted
#: state gets. One of those other seven, `intakes.PROPOSAL_SENT`, is left
#: off on purpose rather than by oversight: nothing advances an intake to it
#: until Stage 3 builds the actor that does (see its own comment in
#: `intakes.py`), and whether a client may still open their quotation once
#: the proposal itself has gone out is a call Stage 3 should make on
#: purpose, not inherit as a 404 nobody actually decided.
_CLIENT_QUOTATION_STATES = {intakes.SENT, intakes.REVISION_REQUESTED, intakes.FINALIZED}


def _client_quotation(token: str, render: Callable[[str, Estimate, int], Response]) -> Response:
    """Resolve a client's own link, refuse everything this door will not
    discuss, and hand back whatever `render` builds from the one document a
    client may ever read here.

    There is no name anywhere in this function - or in either route below
    that calls it - for the document this door refuses to serve: not a
    parameter, not a variable, not a comment. What document gets built is a
    single local constant, set once, on the next line. A value reachable
    from anything a request carries is the one bug this task exists to make
    structurally impossible, not merely rejected after the fact - and the
    surest way to prove nothing reads it off the request is to never spell
    it out at all.

    Everything happens before `give_back` runs, `render` included - the same
    discipline `read_client_view` follows, and for the same reason: nothing
    here may run against whatever workspace `_gate` left the ambient context
    pointed at from this request's own `X-Workspace` header (irrelevant on
    this door - see that route's docstring), only the workspace the token
    itself named.

    Refused with this door's one opaque body, exactly as every other route
    on this prefix refuses: an unknown, expired or relinked-away token
    (`tokens.resolve`); a real token whose intake has moved to a state
    outside `_CLIENT_QUOTATION_STATES`; and a sent-family intake whose
    bundle has since been deleted, or never carried this document at all -
    the same dangling-bundle case `/revise` and `/finalize` above already
    guard, reached here through a different door.
    """
    kind = "proposal"

    found = tokens.resolve(token)
    if found is None:
        raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        entry = intakes.get(intake_id)
        if entry is None or entry.state not in _CLIENT_QUOTATION_STATES:
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

        bundle = storage.get(entry.sent_bundle_id) if entry.sent_bundle_id else None
        if bundle is None:
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

        markdown = storage.markdown_for(bundle, kind)
        if markdown is None:
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

        return render(markdown, bundle.estimate, bundle.revision)
    finally:
        workspaces.give_back(borrowed)


@app.get("/api/client/{token}/quotation.html", tags=["client"])
async def client_quotation_html(token: str) -> HTMLResponse:
    """Print-ready HTML of the document a client is allowed to read.

    No `kind` anywhere - see `_client_quotation`'s own docstring."""
    kind = "proposal"

    def render(markdown: str, estimate: Estimate, revision: int) -> HTMLResponse:
        # Loaded here, inside the borrow `_client_quotation` is still
        # holding while `render` runs - the studio the token itself names,
        # never whatever `_gate` left ambient from this request's own
        # (irrelevant on this door) `X-Workspace` header. Without this, the
        # page a client is asked to read carries "PRISM" - the tool's name -
        # on its letterhead instead of the studio's, and none of a studio's
        # saved colours, fonts or logo. `render_print_html`'s own docstring
        # is explicit that `brand`/`design` exist for exactly this: a
        # document a client reads, not one a studio member is looking at on
        # their own screen (which is what every *other* caller of this
        # renderer in this file is - `printable_html`, on the studio's own
        # authenticated side of the app).
        studio = settings.load()
        try:
            html = render_print_html(
                markdown,
                _document_title(estimate, kind),
                estimate,
                kind=kind,
                brand=studio.studio_name,
                design=studio.proposal_design,
            )
        except Exception as exc:  # pragma: no cover - a renderer bug, not user input
            logger.exception("HTML rendering failed for a client's own quotation")
            raise HTTPException(
                status_code=500,
                detail="The page could not be produced. Try again shortly.",
            ) from exc
        return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

    return _client_quotation(token, render)


@app.get("/api/client/{token}/quotation.pdf", tags=["client"])
def client_quotation_pdf(token: str) -> Response:
    """The same document as a PDF, for whoever would rather attach it to an
    email than open a link. No `kind` anywhere - see `_client_quotation`'s
    own docstring.

    A plain `def`, not `async def` like its `.html` sibling above: building
    this PDF is real CPU work - reportlab's layout pass, not I/O - and this
    door is anonymous with no rate limit of its own (`_gate` only gates the
    three POST routes; a GET here gets neither the limiter nor the body
    cap). Run on the event loop, that cost is paid by every other request
    this worker is holding, not just this one. A synchronous path operation
    is what tells FastAPI to run it on its threadpool instead.
    `download_pdf` (the studio's own file route, same reportlab cost) is
    still `async def` and still pays this - not fixed here, since it sits
    behind auth and is out of this task's scope, but named so the gap is
    not mistaken for solved elsewhere in this file. This route has no such
    gate, which is exactly why leaving it `async def` is the sharper
    version of the same problem.
    """
    kind = "proposal"

    def render(markdown: str, estimate: Estimate, revision: int) -> Response:
        # See `client_quotation_html`'s own `render` for why this is loaded
        # here rather than left at the renderer's bare defaults.
        # `render_pdf` has no separate `brand` text parameter the way
        # `render_print_html` does - a studio's mark reaches the PDF only
        # through `design.logo`, and absent one, the page falls back to the
        # document's own label ("Quotation"), never to the tool's name.
        studio = settings.load()
        try:
            data = render_pdf(
                markdown,
                _document_title(estimate, kind),
                estimate,
                kind=kind,
                design=studio.proposal_design,
            )
        except Exception as exc:  # pragma: no cover - a renderer bug, not user input
            logger.exception("PDF rendering failed for a client's own quotation")
            raise HTTPException(
                status_code=500,
                detail="The PDF could not be produced. Try again shortly.",
            ) from exc

        filename = _filename(estimate, kind, revision).removesuffix(".md") + ".pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    return _client_quotation(token, render)


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
