"""Everything that runs before a route does, and the state it keeps.

Three concerns that all have to happen ahead of routing, which is the only
thing they have in common: who is asking (`_gate`), how much they may send
(`_client_body_limit`), and how often (`_enforce_rate_limit`). They sit
together here because two of them are *called* from the third, and because
the rate limiter owns module-level mutable state - a dict of buckets - that
must exist exactly once in the process. Two copies of that dict is two
independent limits, each admitting the full budget, and nothing anywhere
would report the discrepancy.

Lifted out of `main.py` unchanged when the routes moved into `app/presentation/api/`.
`main.py` calls `install(app)` and otherwise no longer knows any of this is
here.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# Raised out of `request.stream()` when the caller hangs up mid-body. `_gate`
# reads the stream itself now, so it is the first thing in this app that can
# see one.
from starlette.requests import ClientDisconnect

from app.features.notifications.infrastructure import inbox
from app.features.team.infrastructure import auth, members
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import config

#: The header the client names its workspace in. A header rather than a path
#: segment so every existing URL - and every link already sent to somebody - is
#: unchanged by workspaces existing.
WORKSPACE_HEADER = "X-Workspace"


#: The same name on a link, where a header cannot go.
WORKSPACE_PARAM = "workspace"


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


async def _nowhere_to_file_it(request, exc: workspaces.NoWorkspace) -> JSONResponse:
    """Answer plainly when there is no workspace yet.

    409 rather than 500: nothing is broken, the app simply has not been told
    whose work this is. Every screen that reads or writes anything gets this
    until a workspace exists, and the client turns it into the one thing worth
    doing - naming one.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})


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


def install(app: FastAPI) -> None:
    """Register the gate and the no-workspace answer on the application.

    Called by the composition root, and the ORDER of the call matters: CORS is
    added first there, this second. Starlette wraps the last-registered
    middleware outermost, so `_gate` ends up outside `CORSMiddleware` - which
    means a 401 or 403 this function refuses with carries no CORS headers. That
    is exactly the behaviour this app shipped with before the split, preserved
    deliberately rather than quietly corrected, because changing it changes what
    a browser can read off a refusal and that is a decision to make on purpose.
    """
    app.add_exception_handler(workspaces.NoWorkspace, _nowhere_to_file_it)
    app.middleware("http")(_gate)
