"""`/api/client/{token}` - the first unauthenticated surface in this codebase,
GET (Task 3) and its three writes (Task 4: `/submit`, `/revise`, `/finalize`).

Runs with real auth ON throughout, not merely available: `SUPABASE_JWT_SECRET`
is set on `config` directly, right after import, exactly the way
`check_intakes_api.py` turns HS256 verification on for its own permission-model
section - `app.auth` reads that module attribute live on every call, so
assigning it after import is what actually takes. This matters more here than
anywhere else in the suite: an anonymous surface proven against a server that
never checks tokens at all proves nothing, because every route - including the
ones this file exists to show still refuse a stranger - would answer anyone
either way.

`TestClient` is entered as a context manager (`with TestClient(app) as
client:`), not constructed bare - the ASGI lifespan (`@app.on_event("startup")`
in `main.py`, including `tokens.build_index()`) only runs that way. A bare
`TestClient(app)` never fires it, which is exactly how the startup-built token
index shipped with zero regression coverage the first time: every assertion in
this file passed identically whether or not that hook existed.

The Task 4 section below drives several extra `TestClient` instances, one per
IP address it needs to simulate - `client=(ip, port)` at construction is the
only place Starlette's `TestClient` lets a caller's address be set, and it is
fixed for that instance's whole lifetime, not per request. Each of those extra
clients is built bare, without `with`. That is safe here specifically: the ASGI
lifespan already ran once, at module scope, via the outer `with` below, and
routing an ordinary request through the same `app` object does not depend on
running that lifespan a second time - only a *fresh* `app` built without ever
entering a `with TestClient(...)` would need it, which is exactly the failure
mode this file's opening section already proves does not apply here.

    cd backend
    .venv/Scripts/python.exe scripts/check_client_api.py
"""

from __future__ import annotations

import inspect
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-client-api-")
# Blanked first, same fix as every other API-level check script: `app.config`
# reads these once at import time via `load_dotenv(..., override=False)`, and
# a real `backend/.env` (this repo's names an actual Supabase project) would
# otherwise win. Turned back on deliberately, below, once `config` exists to
# assign onto.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from app import auth as auth_module  # noqa: E402
from app import config  # noqa: E402
from app import inbox  # noqa: E402
from app import intakes  # noqa: E402
from app import main as main_module  # noqa: E402
from app import members  # noqa: E402
from app import settings  # noqa: E402
from app import storage  # noqa: E402
from app import tokens  # noqa: E402
from app import workspaces  # noqa: E402
from app.design import ProposalDesign  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import (  # noqa: E402
    ClientNarrative,
    CostSummary,
    DeveloperSpec,
    Estimate,
    LineItem,
    PaymentMilestone,
    ProposalBundle,
    UnitKind,
)


def pdf_text(data: bytes) -> str:
    """Extractable text from a rendered PDF - the same idiom
    `check_kind_titles.py` already uses, needed here because reportlab
    compresses its content streams: a raw substring test against `data`
    itself would pass or fail independently of what the PDF actually says."""
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


TEST_JWT_SECRET = "check-client-api-test-secret-do-not-reuse-32byte"
config.SUPABASE_JWT_SECRET = TEST_JWT_SECRET
ok("real auth is actually on before anything else runs", auth_module.required())


def _token(sub: str, email: str) -> str:
    """A signed HS256 access token shaped like the ones Supabase issues -
    just `sub` and `email`, which is all `auth.User` reads."""
    return jwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


workspaces.ensure_ready()

with TestClient(app) as client:
    # --- The startup hook actually ran, and nothing after it pays for it -----
    #
    # `TestClient` entered as a context manager runs the ASGI lifespan before
    # this block's body starts, so by this line `main.py`'s startup events -
    # including `_build_the_client_token_index`, which calls
    # `tokens.build_index()` - have already completed. Checked directly,
    # before a single fixture exists and before any `resolve()` has run:
    # this is not "the index happens to be built by the time we get around to
    # asking", it is "the index was built before this test touched anything."
    ok("the startup hook built the token index before this test did anything", tokens._built)  # noqa: SLF001

    # Proven the stronger way, not just observed: patch the walk itself to
    # count calls, then make the very first request this `TestClient` ever
    # sends. If the startup hook had not run - or had run and left `_built`
    # False - `resolve()`'s lazy path would call this on that first request,
    # since `not _built` would still be true. It must not.
    _walk_calls = {"count": 0}
    _real_build_locked = tokens._build_locked

    def _counting_build_locked(*args, **kwargs):
        _walk_calls["count"] += 1
        return _real_build_locked(*args, **kwargs)

    tokens._build_locked = _counting_build_locked
    try:
        first_ever_request = client.get("/api/client/probe-before-any-fixture-exists")
    finally:
        tokens._build_locked = _real_build_locked
    ok("and the first request this client ever sends does not trigger it", first_ever_request.status_code == 404)
    ok("(the walk was never called at all)", _walk_calls["count"] == 0)

    # --- Two workspaces, each with its own name and its own intake ----------
    #
    # The two studio names have to actually differ. An `issued` intake's
    # whole client-facing body is `{"state": ..., "studio_name": ...}` - if
    # both workspaces happened to share a name, the cross-workspace test
    # below would pass whether or not the handler used the token to pick the
    # workspace at all.

    home = workspaces.create("Alpha Studio")
    workspaces.use(home.id)
    settings.save(settings.load().model_copy(update={"studio_name": "Alpha Studio Name"}))
    home_intake = intakes.create(
        client_email="buyer@alpha-client.com",
        client_phone="",
        scope="A booking site for a two-branch clinic.",
        budget_text="around 300k",
        preset={},
        created_by="admin@neptune.ph",
    )
    home_token = home_intake.token
    ok("the home fixture starts issued", home_intake.state == intakes.ISSUED)

    other = workspaces.create("Bravo Studio")
    workspaces.use(other.id)
    settings.save(settings.load().model_copy(update={"studio_name": "Bravo Studio Name"}))
    intakes.create(
        client_email="buyer@bravo-client.com",
        client_phone="",
        scope="A different request entirely, in a different workspace.",
        budget_text="",
        preset={},
        created_by="admin@neptune.ph",
    )

    # --- The one door opens, with no Authorization header at all ------------

    opened = client.get(f"/api/client/{home_token}")
    ok(
        "GET /api/client/<token> answers 200 with no Authorization header",
        opened.status_code == 200,
    )
    ok(
        "it is exactly the issued intake's own view - two fields, nothing else",
        opened.json() == {"state": "issued", "studio_name": "Alpha Studio Name"},
    )

    # --- X-Workspace is ignored: the token alone decides the workspace ------
    #
    # Adversarial by construction: `home_token` was minted in `home`, and this
    # request names `other` in the one header a client could otherwise use to
    # lie about it. `_gate` calls `workspaces.use(other.id)` on every request,
    # open path or not, before this route ever runs - so a handler that read
    # the ambient "current" workspace instead of resolving strictly off the
    # token would build this response from `other`'s settings and answer
    # "Bravo Studio Name" here instead.

    crossed = client.get(f"/api/client/{home_token}", headers={"X-Workspace": other.id})
    ok(
        "a token resolves the same way regardless of X-Workspace: 200",
        crossed.status_code == 200,
    )
    ok(
        "and it is still home's own view, not the workspace named in the header",
        crossed.json() == {"state": "issued", "studio_name": "Alpha Studio Name"},
    )

    # --- The bare path (no token at all) is 404, not the one 401 on this door
    #
    # `/api/client/{token}` requires a segment after the slash - Starlette's
    # default converter does not match an empty one - so nothing is ever
    # routed at exactly `/api/client` or `/api/client/`. Before `_gate` names
    # that path open too, it falls through to the ordinary `/api/` branch,
    # which demands a token this caller never sent: the one place this door
    # answered 401 instead of the 404 every other malformed attempt at it
    # gets.
    for bare in ("/api/client", "/api/client/"):
        resp = client.get(bare)
        ok(f"{bare} is 404, the same as every other bad shape on this door", resp.status_code == 404)

    # And the fix must not become a prefix over-match: a path that merely
    # starts with the same letters is not this door and must still demand a
    # token.
    for near_miss in ("/api/clients", "/api/clientele/x", "/api/client-something"):
        resp = client.get(near_miss)
        ok(f"{near_miss} is not mistaken for the client door: still 401", resp.status_code == 401)

    # --- Every existing studio route still refuses an anonymous caller ------

    STUDIO_PATHS = [
        "/api/intakes",
        "/api/proposals",
        "/api/settings",
        "/api/team",
        "/api/workspaces",
        "/api/jobs",
        "/api/notifications",
    ]

    for path in STUDIO_PATHS:
        resp = client.get(path, headers={"X-Workspace": home.id})
        ok(f"{path} still 401s with no Authorization header", resp.status_code == 401)

    # That list is only a regression test if the paths are real, not merely
    # spelled correctly - proven by showing the same caller, with a real
    # admin token, gets past the gate on every one of them.
    workspaces.use(home.id)
    members.claim("admin@neptune.ph", "admin-uid")
    admin_token = _token("admin-uid", "admin@neptune.ph")
    admin_headers = {"X-Workspace": home.id, "Authorization": f"Bearer {admin_token}"}

    for path in STUDIO_PATHS:
        resp = client.get(path, headers=admin_headers)
        ok(
            f"{path} is not 401 for a real admin - the 401 above is the gate, not a dead route",
            resp.status_code != 401,
        )

    # --- Unknown, expired and closed are indistinguishable -------------------

    unknown = client.get("/api/client/not-a-real-token-at-all")
    ok("an unknown token is 404", unknown.status_code == 404)

    workspaces.use(other.id)
    closable = intakes.create(
        client_email="withdrawn@client.com",
        client_phone="",
        scope="Withdrawn before it went anywhere.",
        budget_text="",
        preset={},
        created_by="admin@neptune.ph",
    )
    closable_token = closable.token  # captured before close() blanks it on disk
    intakes.close(closable.id, "admin@neptune.ph")
    closed = client.get(f"/api/client/{closable_token}")
    ok("a closed intake's token is 404, the same as unknown", closed.status_code == 404)

    expiring = intakes.create(
        client_email="expired@client.com",
        client_phone="",
        scope="Left too long.",
        budget_text="",
        preset={},
        created_by="admin@neptune.ph",
    )
    expired_token = expiring.token
    stale = intakes.get(expiring.id)
    stale.token_expires_at = "2000-01-01T00:00:00Z"
    intakes._write(stale)  # noqa: SLF001 - planting an expired stamp past the public API
    expired = client.get(f"/api/client/{expired_token}")
    ok("an expired token is 404, the same as unknown", expired.status_code == 404)

    ok(
        "unknown, closed and expired share one status code",
        unknown.status_code == closed.status_code == expired.status_code == 404,
    )
    ok(
        "and byte-identical bodies naming the same reason - a stranger cannot tell "
        "which kind of gone this is",
        unknown.json() == closed.json() == expired.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )
    ok("that body carries no clue beyond 'gone'", set(unknown.json()) == {"detail"})

    # --- A close() that lands mid-request must not leak "closed" as 200 -----
    #
    # `tokens.resolve` validates a token against its own read of the intake,
    # inside its own `borrow`/`give_back`. The handler then reads the same
    # intake again, independently, to build the response. Those two reads
    # are not atomic with each other - a `close()` can land in the gap
    # between them. Reproduced directly rather than argued: patch
    # `tokens.resolve` to do exactly what it always does, and then run
    # `intakes.close()` on the intake it just resolved, before returning -
    # the same interleaving a real concurrent request could produce, made
    # deterministic.
    race_intake = intakes.create(
        client_email="race@client.com",
        client_phone="",
        scope="Closed while a client's own request for it was in flight.",
        budget_text="",
        preset={},
        created_by="admin@neptune.ph",
    )
    race_token = race_intake.token
    _real_resolve = tokens.resolve

    def _resolve_then_close_underneath_it(token: str):
        found = _real_resolve(token)
        if found is not None:
            # A real concurrent close would run in its own request, borrowed
            # into the right workspace on its own terms - mirrored here
            # rather than assumed, since the ambient workspace at this point
            # in the handler is whatever `_gate` set from this request's own
            # (absent) `X-Workspace` header, not necessarily `race_intake`'s.
            workspace_id, intake_id = found
            borrowed = workspaces.borrow(workspace_id)
            try:
                intakes.close(intake_id, "admin@neptune.ph")
            finally:
                workspaces.give_back(borrowed)
        return found

    try:
        main_module.tokens.resolve = _resolve_then_close_underneath_it
        interleaved = client.get(f"/api/client/{race_token}")
    finally:
        main_module.tokens.resolve = _real_resolve

    ok(
        "a close() landing between resolve's read and the handler's own is still "
        "404, not 200 {'state': 'closed'}",
        interleaved.status_code == 404
        and interleaved.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # --- A closed intake with a live token on disk must still be refused ----
    #
    # The second, independent path the interleaving test above does not
    # cover: `tokens._build_locked` re-indexes any intake with a non-empty
    # token, with no state check, by its own docstring's admission - so a
    # restored backup, a hand-edit, or any future writer that bypasses
    # `intakes._write` (the one place that blanks a closed intake's token)
    # leaves exactly this shape of record: `state == "closed"`, `token` still
    # populated and unexpired. Built by writing the file directly, past
    # `intakes._write`, which would immediately re-blank the token the
    # moment it saw `state == CLOSED` - that guard is precisely what this
    # fixture has to bypass to prove the *handler* still refuses it
    # independently, not merely trust that guard held.
    zombie = intakes.create(
        client_email="zombie@client.com",
        client_phone="",
        scope="Closed, but a live token still sits on disk from before that guard.",
        budget_text="",
        preset={},
        created_by="admin@neptune.ph",
    )
    zombie_token = zombie.token
    intakes.close(zombie.id, "admin@neptune.ph")

    zombie_path = intakes._path(zombie.id)  # noqa: SLF001 - reaching past the public API on purpose
    zombie_payload = json.loads(zombie_path.read_text(encoding="utf-8"))
    ok(
        "close() did blank the token on disk, before this fixture undoes it",
        zombie_payload["token"] == "",
    )
    zombie_payload["token"] = zombie_token
    zombie_payload["token_expires_at"] = intakes._later(60)  # noqa: SLF001 - fresh, unexpired
    zombie_path.write_text(json.dumps(zombie_payload), encoding="utf-8")
    # What `tokens._build_locked`'s walk would do on finding this record - no
    # state check, by design - reproduced directly rather than waiting for a
    # process restart to trigger the real walk.
    tokens.remember(zombie_token, other.id, zombie.id)

    zombie_resp = client.get(f"/api/client/{zombie_token}")
    ok(
        "a closed intake with a live token restored on disk is still 404, not "
        "200 {'state': 'closed'}",
        zombie_resp.status_code == 404
        and zombie_resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # --- A state clientview.of cannot show must not become a traceback ------
    #
    # `clientview.of` raises `ValueError` for a state it does not recognise,
    # or a quoted-face state with no bundle attached - see its own
    # docstring. `tokens.py`'s module docstring names this exact case: an
    # unknown token has to answer 404, not a 500 with a stack trace, even
    # when the failure is on this server's own side of the line. `home_token`
    # is still good at this point in the run - forcing `clientview.of` itself
    # to fail is the only way to reach this branch without a real state
    # clientview cannot show.
    _real_of = main_module.clientview.of

    def _boom_of(*_args, **_kwargs):
        raise ValueError("a state clientview does not know how to show")

    try:
        main_module.clientview.of = _boom_of
        broken = client.get(f"/api/client/{home_token}")
    finally:
        main_module.clientview.of = _real_of

    ok(
        "a clientview failure answers the same 404 body, not a 500 with a traceback",
        broken.status_code == 404 and broken.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # ==========================================================================
    # Task 4: the client writes - POST /submit, /revise, /finalize
    # ==========================================================================
    #
    # Every group below - submit, revise, finalize, the rate limit - is driven
    # through its own `TestClient` bound to its own IP address, so this file's
    # own POSTs can never share a rate-limit bucket with each other by
    # accident, and so the rate-limit section itself can count its own
    # requests exactly.

    def _client_for(ip: str) -> TestClient:
        return TestClient(app, client=(ip, 443))

    def _walk(intake_id: str, *steps: tuple) -> None:
        """Advance straight through `(state, fields)` pairs, bypassing any
        HTTP route - the same shorthand `check_tokens.py`'s `clientview`
        fixture and `check_intake_gate.py` both use to reach a state nothing
        in this plan yet drives through a request of its own."""
        for state, fields in steps:
            intakes.advance(intake_id, state, **fields)

    LONG_TEXT = "x" * (config.MAX_BRIEF_CHARS + 1)

    FIXTURE_ESTIMATE = Estimate(
        project_name="Task 4 Fixture",
        client_name="Fixture Client Co.",
        currency="PHP",
        client=ClientNarrative(
            title="Task 4 Fixture",
            executive_summary="A fixture proposal for the client-write routes.",
            validity_days=30,
        ),
        line_items=[
            LineItem(
                id="LI-01",
                category="Backend",
                description="Build the thing",
                role="Senior Backend Engineer",
                quantity=5,
                unit=UnitKind.hour,
                unit_rate=1000.0,
                subtotal=5000.0,
            ),
        ],
        cost=CostSummary(
            subtotal=5000.0,
            total=5000.0,
            payment_milestones=[
                PaymentMilestone(label="Deposit", percent=50.0, amount=2500.0, trigger="Signing"),
                PaymentMilestone(label="Delivery", percent=50.0, amount=2500.0, trigger="Delivery"),
            ],
        ),
        quotation_ref="TASK4-0000001",
    )

    def _sent_intake(
        workspace,
        created_by: str,
        scope: str = "Original scope, before any change.",
        estimate: Estimate = FIXTURE_ESTIMATE,
    ):
        """A real intake moved by hand straight to `sent`, with a real bundle
        attached - the only shape `/revise` and `/finalize` are ever reachable
        from. Built directly rather than by driving a Gemini-stubbed pass
        through `/api/proposals` the way `check_intake_gate.py` and
        `check_tokens.py`'s `clientview` section do: those two already prove
        `clientview.of` renders a real bundle correctly end to end, so this
        file's fixtures only need a bundle real enough to be read - proving
        that rendering a second time is not this file's job.

        `estimate` defaults to the shared fixture every earlier section of
        this file already uses; Task 5's section below passes its own, built
        with a distinctive marker on the internal document, so the bundle
        this call attaches carries content that can actually prove the leak
        test bites.
        """
        workspaces.use(workspace.id)
        entry = intakes.create(
            client_email="quoted@client.com",
            client_phone="",
            scope=scope,
            budget_text="around 100k",
            preset={},
            created_by=created_by,
        )
        bundle_id = storage.new_id()
        bundle = ProposalBundle(
            id=bundle_id,
            created_at=storage.utc_now_iso(),
            estimate=estimate,
            files=main_module._build_files(bundle_id, estimate),  # noqa: SLF001
            revision=1,
            root_id=bundle_id,
        )
        storage.save(bundle)
        _walk(
            entry.id,
            (intakes.SUBMITTED, {}),
            (intakes.PREPARING, {"job_id": "fixture-job"}),
            (intakes.QUOTED, {"bundle_ids": [bundle_id]}),
            (intakes.SENT, {"sent_bundle_id": bundle_id, "sent_at": "2026-08-01T00:00:00Z"}),
        )
        return intakes.get(entry.id), bundle

    # --- /submit: only from `issued`, and only once --------------------------

    submit_client = _client_for("203.0.113.11")

    workspaces.use(home.id)
    fresh = intakes.create(
        client_email="",
        client_phone="",
        scope="",
        budget_text="",
        preset={},
        created_by="admin@neptune.ph",
    )
    ok(
        "submit fixture starts issued, with no client words yet",
        fresh.state == intakes.ISSUED and not fresh.scope,
    )

    submitted = submit_client.post(
        f"/api/client/{fresh.token}/submit",
        json={
            "client_email": "real@client.com",
            "client_phone": "+63 917 000 0000",
            "scope": "A booking site for three branches.",
            "budget_text": "150,000 - 200,000 PHP",
        },
    )
    ok("submit from issued: 200", submitted.status_code == 200)
    ok(
        "submit from issued: the response already reflects the new (waiting) state",
        submitted.json()["state"] == "waiting",
    )

    on_disk = intakes.get(fresh.id)
    ok("submit from issued: the intake itself moved to submitted", on_disk.state == intakes.SUBMITTED)
    ok(
        "submit from issued: all four fields are stored verbatim",
        on_disk.client_email == "real@client.com"
        and on_disk.client_phone == "+63 917 000 0000"
        and on_disk.scope == "A booking site for three branches."
        and on_disk.budget_text == "150,000 - 200,000 PHP",
    )

    # --- Submit twice: the second is refused, and the record does not move ---
    #
    # The second attempt sends deliberately different words - if the state
    # check were missing, this would overwrite the first submission, which is
    # exactly what the "record is unchanged" assertion below would then catch.

    second = submit_client.post(
        f"/api/client/{fresh.token}/submit",
        json={
            "client_email": "attacker@evil.example",
            "client_phone": "000",
            "scope": "OVERWRITE ATTEMPT",
            "budget_text": "OVERWRITE ATTEMPT",
        },
    )
    ok(
        "submit twice: the second attempt is refused with the same opaque body an unknown "
        "token gets",
        second.status_code == 404 and second.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )
    unchanged = intakes.get(fresh.id)
    ok("submit twice: the record on disk is still submitted, not re-issued or re-submitted", unchanged.state == intakes.SUBMITTED)
    ok(
        "submit twice: and it still carries the FIRST submission's own four fields, not the "
        "second attempt's",
        unchanged.client_email == "real@client.com"
        and unchanged.client_phone == "+63 917 000 0000"
        and unchanged.scope == "A booking site for three branches."
        and unchanged.budget_text == "150,000 - 200,000 PHP",
    )

    # --- Submit from every other state is refused, identically ---------------
    #
    # The brief says "any other state", and the table below is what that
    # claim is actually asserting - every non-`issued`, non-`closed` row in
    # `intakes.ALLOWED`, not just the two states nearest to `issued`.
    # `submitted` itself is covered separately, above, by the double-submit
    # test (a fresh `issued` fixture would only prove submit works twice in a
    # row from `issued`, which the state-check design makes impossible - the
    # double-submit test already reaches `submitted` for real).

    _QUOTED_STEPS = (
        (intakes.SUBMITTED, {}),
        (intakes.PREPARING, {"job_id": "x"}),
        (intakes.QUOTED, {"bundle_ids": ["a" * 12]}),
    )
    _SENT_STEPS = _QUOTED_STEPS + (
        (intakes.SENT, {"sent_bundle_id": "a" * 12, "sent_at": "2026-01-01T00:00:00Z"}),
    )

    SUBMIT_REFUSAL_FIXTURES = (
        ("preparing", ((intakes.SUBMITTED, {}), (intakes.PREPARING, {"job_id": "x"}))),
        (
            "quote_failed",
            (
                (intakes.SUBMITTED, {}),
                (intakes.PREPARING, {"job_id": "x"}),
                (intakes.QUOTE_FAILED, {"error": "simulated failure"}),
            ),
        ),
        ("quoted", _QUOTED_STEPS),
        ("sent", _SENT_STEPS),
        (
            "revision_requested",
            _SENT_STEPS
            + ((intakes.REVISION_REQUESTED, {"revisions": [{"asked": "x", "at": "2026-01-01T00:00:00Z"}]}),),
        ),
        ("finalized", _SENT_STEPS + ((intakes.FINALIZED, {}),)),
    )
    for label, steps in SUBMIT_REFUSAL_FIXTURES:
        workspaces.use(home.id)
        candidate = intakes.create(
            client_email="", client_phone="", scope="", budget_text="", preset={},
            created_by="admin@neptune.ph",
        )
        _walk(candidate.id, *steps)
        resp = submit_client.post(
            f"/api/client/{candidate.token}/submit", json={"scope": "x", "budget_text": "y"}
        )
        ok(
            f"submit from {label}: refused with the same opaque body as an unknown token",
            resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
        )

    workspaces.use(home.id)
    closed_candidate = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    intakes.close(closed_candidate.id, "admin@neptune.ph")
    resp = submit_client.post(
        f"/api/client/{closed_candidate.token}/submit", json={"scope": "x", "budget_text": "y"}
    )
    ok(
        "submit from closed: refused with the same opaque body as an unknown token",
        resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # --- Over-length scope/budget: the studio's own convention, not a second -

    workspaces.use(home.id)
    length_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    try:
        main_module._normalise_scope(LONG_TEXT)  # noqa: SLF001
        expected_scope_detail = None
    except Exception as exc:  # noqa: BLE001 - capturing FastAPI's own HTTPException
        expected_scope_detail = exc.detail

    over_scope = submit_client.post(
        f"/api/client/{length_fixture.token}/submit",
        json={"client_email": "x@y.com", "scope": LONG_TEXT, "budget_text": "fine"},
    )
    ok("submit: an over-length scope is refused with 400", over_scope.status_code == 400)
    ok(
        "submit: with the exact detail `_normalise_scope` itself raises - reusing the "
        "studio's own route's error, not a second convention",
        over_scope.json()["detail"] == expected_scope_detail,
    )
    ok(
        "submit: refusing on length did not move the intake off issued",
        intakes.get(length_fixture.id).state == intakes.ISSUED,
    )

    try:
        main_module._normalise_budget_text(LONG_TEXT)  # noqa: SLF001
        expected_budget_detail = None
    except Exception as exc:  # noqa: BLE001
        expected_budget_detail = exc.detail

    over_budget = submit_client.post(
        f"/api/client/{length_fixture.token}/submit",
        json={"client_email": "x@y.com", "scope": "fine", "budget_text": LONG_TEXT},
    )
    ok("submit: an over-length budget is refused with 400", over_budget.status_code == 400)
    ok(
        "submit: with the exact detail `_normalise_budget_text` itself raises",
        over_budget.json()["detail"] == expected_budget_detail,
    )
    ok(
        "submit: refusing on length still did not move the intake off issued",
        intakes.get(length_fixture.id).state == intakes.ISSUED,
    )

    # --- client_email/client_phone are bounded too, not just scope/budget ---
    #
    # Before this fix these two fields got only a bare `.strip()` - unbounded
    # on the first anonymous write in this codebase, and persisted verbatim
    # into the studio's own `GET /api/intakes`. Same idiom as scope/budget:
    # the exact detail is captured live from the normaliser itself.
    LONG_CONTACT = "y" * (main_module._MAX_CONTACT_CHARS + 1)  # noqa: SLF001

    try:
        main_module._normalise_client_email(LONG_CONTACT)  # noqa: SLF001
        expected_email_detail = None
    except Exception as exc:  # noqa: BLE001
        expected_email_detail = exc.detail

    over_email = submit_client.post(
        f"/api/client/{length_fixture.token}/submit",
        json={"client_email": LONG_CONTACT, "client_phone": "fine", "scope": "fine", "budget_text": "fine"},
    )
    ok("submit: an over-length email is refused with 400", over_email.status_code == 400)
    ok(
        "submit: with the exact detail `_normalise_client_email` itself raises",
        over_email.json()["detail"] == expected_email_detail,
    )

    try:
        main_module._normalise_client_phone(LONG_CONTACT)  # noqa: SLF001
        expected_phone_detail = None
    except Exception as exc:  # noqa: BLE001
        expected_phone_detail = exc.detail

    over_phone = submit_client.post(
        f"/api/client/{length_fixture.token}/submit",
        json={"client_email": "fine", "client_phone": LONG_CONTACT, "scope": "fine", "budget_text": "fine"},
    )
    ok("submit: an over-length phone number is refused with 400", over_phone.status_code == 400)
    ok(
        "submit: with the exact detail `_normalise_client_phone` itself raises",
        over_phone.json()["detail"] == expected_phone_detail,
    )
    ok(
        "submit: refusing on contact-field length still did not move the intake off issued",
        intakes.get(length_fixture.id).state == intakes.ISSUED,
    )

    # A control character is scrubbed rather than rejected - proven directly
    # against the normaliser, including a tab and a newline (not just a
    # bare C0 control), and end to end through a real submit.
    ok(
        "_normalise_client_email strips control characters rather than storing them, "
        "including a tab/newline in the middle - not only what .strip() alone would catch "
        "at the two ends",
        main_module._normalise_client_email("a\x00b\x1fc\td\ne@example.com")  # noqa: SLF001
        == "abcde@example.com",
    )
    workspaces.use(home.id)
    control_char_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    scrubbed = submit_client.post(
        f"/api/client/{control_char_fixture.token}/submit",
        json={
            "client_email": "weird\x07@client.com",
            "client_phone": "09\x0017\x08000",
            "scope": "fine",
            "budget_text": "fine",
        },
    )
    ok("submit with control characters in the contact fields: still 200", scrubbed.status_code == 200)
    scrubbed_on_disk = intakes.get(control_char_fixture.id)
    ok(
        "and the control characters did not reach disk",
        scrubbed_on_disk.client_email == "weird@client.com"
        and scrubbed_on_disk.client_phone == "0917000",
    )

    # --- A genuine save failure is not folded into the opaque "gone" body ----
    #
    # `_client_advance` (main.py) distinguishes `intakes.advance` raising
    # because the move is illegal from `intakes.advance` raising because
    # `_write` itself could not save - by exception *type*
    # (`intakes.IntakeWriteError`, a dedicated subtype of `IntakeError`), not
    # by reading the exception's own message. Proven twice, the same way
    # this file already forces `clientview.of` to raise for the "state
    # clientview cannot show" case above: patch `intakes.advance` (through
    # `main_module.intakes`, the attribute the handler actually resolves
    # through) to raise `IntakeWriteError`, and confirm the door answers
    # 500 - a real error - rather than quietly claiming the link is gone.
    # The second patch uses a message that shares not one word with
    # `_write`'s own - proof this is a type dispatch, not a resurrected
    # string match: if `intakes.py`'s wording ever changes, this test would
    # still pass and `_client_advance` would still classify it correctly,
    # which is exactly what a substring match could not promise.
    workspaces.use(home.id)
    save_failure_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )

    def _advance_that_cannot_save(*_args, **_kwargs):
        raise intakes.IntakeWriteError(
            "That intake could not be saved: [Errno 28] No space left on device"
        )

    _real_advance = main_module.intakes.advance
    try:
        main_module.intakes.advance = _advance_that_cannot_save
        save_failed = submit_client.post(
            f"/api/client/{save_failure_fixture.token}/submit",
            json={"client_email": "x@y.com", "scope": "fine", "budget_text": "fine"},
        )
    finally:
        main_module.intakes.advance = _real_advance

    ok("a genuine save failure answers 500, not this door's usual refusal", save_failed.status_code == 500)
    ok(
        "and its body is not the opaque 'gone' text - a lost submission must not read as a "
        "dead link",
        save_failed.json() != {"detail": main_module._CLIENT_LINK_GONE},
    )

    def _advance_that_cannot_save_reworded(*_args, **_kwargs):
        raise intakes.IntakeWriteError("Disk quota exceeded on volume PRISM-DATA")

    workspaces.use(home.id)
    reworded_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    try:
        main_module.intakes.advance = _advance_that_cannot_save_reworded
        save_failed_reworded = submit_client.post(
            f"/api/client/{reworded_fixture.token}/submit",
            json={"client_email": "x@y.com", "scope": "fine", "budget_text": "fine"},
        )
    finally:
        main_module.intakes.advance = _real_advance

    ok(
        "a save failure is still classified correctly even with a completely different "
        "message - this is a type dispatch on IntakeWriteError, not a resurrected string match",
        save_failed_reworded.status_code == 500
        and save_failed_reworded.json() != {"detail": main_module._CLIENT_LINK_GONE},
    )

    # And the ordinary refusal path is untouched: a plain `IntakeError` (not
    # the `IntakeWriteError` subtype) for a state the transition table
    # refuses still answers this door's usual opaque 404, not a 500 - proving
    # `except intakes.IntakeWriteError` catching first does not accidentally
    # widen what counts as a save failure.
    workspaces.use(home.id)
    ordinary_refusal_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    intakes.close(ordinary_refusal_fixture.id, "admin@neptune.ph")
    ordinary_refusal = submit_client.post(
        f"/api/client/{ordinary_refusal_fixture.token}/submit",
        json={"client_email": "x@y.com", "scope": "fine", "budget_text": "fine"},
    )
    ok(
        "a plain IntakeError (illegal move) still answers the ordinary opaque 404, not 500",
        ordinary_refusal.status_code == 404
        and ordinary_refusal.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # --- /revise: only from `sent` --------------------------------------------

    revise_client = _client_for("203.0.113.12")

    revise_sent, revise_bundle = _sent_intake(home, "author@neptune.ph")
    revised = revise_client.post(
        f"/api/client/{revise_sent.token}/revise",
        json={"asked": "Please drop the SMS module and add a waitlist."},
    )
    ok("revise from sent: 200", revised.status_code == 200)
    ok(
        "revise from sent: the response already shows revision_requested",
        revised.json()["state"] == intakes.REVISION_REQUESTED,
    )
    ok(
        "revise from sent: the response's revisions carry what was asked, and nothing else",
        [item["asked"] for item in revised.json()["revisions"]]
        == ["Please drop the SMS module and add a waitlist."],
    )

    on_disk_revised = intakes.get(revise_sent.id)
    ok(
        "revise from sent: the intake itself moved to revision_requested",
        on_disk_revised.state == intakes.REVISION_REQUESTED,
    )
    ok(
        "revise from sent: the revisions log on disk carries the same asked/at pair",
        len(on_disk_revised.revisions) == 1
        and on_disk_revised.revisions[0]["asked"] == "Please drop the SMS module and add a waitlist.",
    )
    ok(
        "revise never touches the client's own scope/email/budget - ADVANCE_FIELDS is not "
        "scoped per transition, but only /submit's handler ever passes these (see "
        "ADVANCE_FIELDS's own docstring in intakes.py)",
        on_disk_revised.scope == "Original scope, before any change."
        and on_disk_revised.client_email == "quoted@client.com"
        and on_disk_revised.budget_text == "around 100k",
    )

    # A second revise, before the studio has re-quoted, is refused - the
    # record is now revision_requested, not sent.
    second_revise = revise_client.post(
        f"/api/client/{revise_sent.token}/revise", json={"asked": "One more thing."}
    )
    ok(
        "revise twice before a re-quote: refused with the same opaque body",
        second_revise.status_code == 404
        and second_revise.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )
    ok(
        "revise twice: the revisions log did not gain a second entry",
        len(intakes.get(revise_sent.id).revisions) == 1,
    )

    # --- A sent intake whose bundle has since been deleted is refused, not
    #     a 500 traceback - `clientview.of` raises `ValueError` for a
    #     quoted-face state with no bundle, and nothing in `intakes.py`
    #     keeps `sent_bundle_id` pointing at a real bundle:
    #     `DELETE /api/proposals/{id}` (main.py) can remove one out from
    #     under a `sent` intake today, with nothing to stop it.
    dangling_revise_sent, dangling_revise_bundle = _sent_intake(home, "author-dangling@neptune.ph")
    ok(
        "dangling-bundle fixture: the bundle exists before it is deleted",
        storage.get(dangling_revise_bundle.id) is not None,
    )
    storage.delete(dangling_revise_bundle.id)
    ok(
        "dangling-bundle fixture: and is genuinely gone afterwards",
        storage.get(dangling_revise_bundle.id) is None,
    )
    dangling_revise = revise_client.post(
        f"/api/client/{dangling_revise_sent.token}/revise", json={"asked": "anything"}
    )
    ok(
        "revise against a sent intake whose bundle was deleted answers this door's usual "
        "opaque refusal, not a 500 with a traceback",
        dangling_revise.status_code == 404
        and dangling_revise.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    REVISE_REFUSAL_FIXTURES = (
        ("issued", ()),
        ("submitted", ((intakes.SUBMITTED, {}),)),
        ("quoted", ((intakes.SUBMITTED, {}), (intakes.PREPARING, {"job_id": "x"}), (intakes.QUOTED, {"bundle_ids": ["a" * 12]}))),
    )
    for label, steps in REVISE_REFUSAL_FIXTURES:
        workspaces.use(home.id)
        candidate = intakes.create(
            client_email="", client_phone="", scope="", budget_text="", preset={},
            created_by="admin@neptune.ph",
        )
        _walk(candidate.id, *steps)
        resp = revise_client.post(f"/api/client/{candidate.token}/revise", json={"asked": "change it"})
        ok(
            f"revise from {label}: refused with the same opaque body as an unknown token",
            resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
        )

    workspaces.use(home.id)
    revise_closed = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    intakes.close(revise_closed.id, "admin@neptune.ph")
    resp = revise_client.post(f"/api/client/{revise_closed.token}/revise", json={"asked": "change it"})
    ok(
        "revise from closed: refused with the same opaque body as an unknown token",
        resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    revise_len_sent, _ = _sent_intake(home, "author2@neptune.ph")
    try:
        main_module._normalise_instruction(LONG_TEXT)  # noqa: SLF001
        expected_instruction_detail = None
    except Exception as exc:  # noqa: BLE001
        expected_instruction_detail = exc.detail

    over_len_revise = revise_client.post(
        f"/api/client/{revise_len_sent.token}/revise", json={"asked": LONG_TEXT}
    )
    ok("revise: an over-length ask is refused with 400", over_len_revise.status_code == 400)
    ok(
        "revise: with the exact detail `_normalise_instruction` itself raises - the same "
        "normaliser the studio's own proposal-revise route already uses",
        over_len_revise.json()["detail"] == expected_instruction_detail,
    )
    ok(
        "revise: refusing on length did not move the intake off sent",
        intakes.get(revise_len_sent.id).state == intakes.SENT,
    )

    empty_revise = revise_client.post(f"/api/client/{revise_len_sent.token}/revise", json={"asked": "   "})
    ok("revise: an empty ask is refused with 400, not silently accepted", empty_revise.status_code == 400)
    ok(
        "revise: refusing an empty ask did not move the intake off sent either",
        intakes.get(revise_len_sent.id).state == intakes.SENT,
    )

    # --- /finalize: only from `sent`, and it tells created_by + every admin --

    finalize_client = _client_for("203.0.113.13")

    # A dedicated workspace and roster: `home`'s own roster (claimed earlier,
    # for the STUDIO_PATHS section) already has meaning for an assertion
    # above, and this needs a roster shaped a specific way - two admins and a
    # separate, non-admin `created_by` - to prove "created_by and the
    # admins" means all of that and nothing more.
    notif_ws = workspaces.create("Finalize Notification Studio")
    workspaces.use(notif_ws.id)
    members.claim("owner-admin@neptune.ph", "owner-uid")
    second_admin_invite = members.invite("second-admin@neptune.ph", members.ADMIN, "owner-admin@neptune.ph")
    members.accept(second_admin_invite.token, "second-admin@neptune.ph", "second-admin-uid")
    author_invite = members.invite("author@neptune.ph", members.MEMBER, "owner-admin@neptune.ph")
    members.accept(author_invite.token, "author@neptune.ph", "author-uid")
    bystander_invite = members.invite("bystander@neptune.ph", members.MEMBER, "owner-admin@neptune.ph")
    members.accept(bystander_invite.token, "bystander@neptune.ph", "bystander-uid")

    finalize_sent, finalize_bundle = _sent_intake(notif_ws, "author@neptune.ph")

    finalized = finalize_client.post(f"/api/client/{finalize_sent.token}/finalize")
    ok("finalize from sent: 200", finalized.status_code == 200)
    ok(
        "finalize from sent: the response already shows finalized",
        finalized.json()["state"] == intakes.FINALIZED,
    )

    on_disk_finalized = intakes.get(finalize_sent.id)
    ok("finalize from sent: the intake itself moved to finalized", on_disk_finalized.state == intakes.FINALIZED)
    ok(
        "finalize never touches the client's own scope/email/budget either",
        on_disk_finalized.scope == finalize_sent.scope
        and on_disk_finalized.client_email == finalize_sent.client_email
        and on_disk_finalized.budget_text == finalize_sent.budget_text,
    )

    owner_mail = inbox.listing(person=inbox.key_for("owner-admin@neptune.ph"))
    second_admin_mail = inbox.listing(person=inbox.key_for("second-admin@neptune.ph"))
    author_mail = inbox.listing(person=inbox.key_for("author@neptune.ph"))
    bystander_mail = inbox.listing(person=inbox.key_for("bystander@neptune.ph"))

    ok("finalize: the first admin was told", any(note.kind == "intake.finalized" for note in owner_mail))
    ok(
        "finalize: the second admin was told too - every admin, not just the first one found",
        any(note.kind == "intake.finalized" for note in second_admin_mail),
    )
    ok(
        "finalize: the intake's own created_by was told, even though they are not an admin",
        any(note.kind == "intake.finalized" for note in author_mail),
    )
    ok(
        "finalize: a member who is neither an admin nor created_by hears nothing about it",
        not any(note.kind == "intake.finalized" for note in bystander_mail),
    )
    owner_note = next(note for note in owner_mail if note.kind == "intake.finalized")
    ok(
        "finalize: the note itself names the actual quotation, not a generic sentence",
        finalize_bundle.estimate.quotation_ref in owner_note.body,
    )

    # --- created_by who is not on this workspace's roster still hears about
    #     it - `inbox.notify`'s list-audience path only ever matches names
    #     still on the roster, so a plain `notify()` call silently drops
    #     anyone who has left the team (or, as here, was never on it despite
    #     having created the request). `inbox.deliver` is the door that
    #     writes to a named person regardless.
    ghost_sent, ghost_bundle = _sent_intake(notif_ws, "ghost@neptune.ph")
    ok(
        "ghost fixture: created_by is not on this workspace's roster at all",
        "ghost@neptune.ph" not in {m.email.lower() for m in members.listing()},
    )
    ghost_finalized = finalize_client.post(f"/api/client/{ghost_sent.token}/finalize")
    ok("finalize with an off-roster created_by: still 200", ghost_finalized.status_code == 200)
    ghost_mail = inbox.listing(person=inbox.key_for("ghost@neptune.ph"))
    ok(
        "finalize: created_by who is not on the roster is still told - this route's own "
        "promise ('even if they do not administer the workspace') would otherwise quietly "
        "break for exactly this person",
        any(note.kind == "intake.finalized" for note in ghost_mail),
    )

    # --- A sent intake whose bundle has since been deleted is refused, not a
    #     500 traceback - and, the sharper case Task 4's review actually
    #     found: the ordering fix that stops the studio being told a
    #     quotation was accepted while the client who "accepted" it sees an
    #     error. `_client_advance` already persisted `finalized` by the time
    #     `clientview.of` fails - that transition cannot be undone - so what
    #     is being proven here is narrower and more important than "no
    #     traceback": that `inbox.notify` never ran for this attempt at all.
    dangling_finalize_sent, dangling_finalize_bundle = _sent_intake(notif_ws, "author@neptune.ph")
    storage.delete(dangling_finalize_bundle.id)
    ok(
        "dangling-bundle finalize fixture: the bundle is genuinely gone",
        storage.get(dangling_finalize_bundle.id) is None,
    )
    owner_finalized_notes_before_dangling = sum(
        1
        for note in inbox.listing(person=inbox.key_for("owner-admin@neptune.ph"))
        if note.kind == "intake.finalized"
    )
    dangling_finalize = finalize_client.post(f"/api/client/{dangling_finalize_sent.token}/finalize")
    ok(
        "finalize against a sent intake whose bundle was deleted answers this door's usual "
        "opaque refusal, not a 500 with a traceback",
        dangling_finalize.status_code == 404
        and dangling_finalize.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )
    ok(
        "the intake still genuinely moved to finalized - _client_advance's write cannot be "
        "undone once it has happened, only what runs after it can be gated",
        intakes.get(dangling_finalize_sent.id).state == intakes.FINALIZED,
    )
    owner_finalized_notes_after_dangling = sum(
        1
        for note in inbox.listing(person=inbox.key_for("owner-admin@neptune.ph"))
        if note.kind == "intake.finalized"
    )
    ok(
        "and this is the ordering fix itself: the admins were NOT told a quotation was "
        "accepted while the client who 'accepted' it is looking at an error - clientview.of "
        "is built and validated before inbox.notify runs, never after",
        owner_finalized_notes_after_dangling == owner_finalized_notes_before_dangling,
    )

    FINALIZE_REFUSAL_FIXTURES = (
        ("issued", ()),
        ("submitted", ((intakes.SUBMITTED, {}),)),
        ("quoted", ((intakes.SUBMITTED, {}), (intakes.PREPARING, {"job_id": "x"}), (intakes.QUOTED, {"bundle_ids": ["a" * 12]}))),
    )
    for label, steps in FINALIZE_REFUSAL_FIXTURES:
        workspaces.use(notif_ws.id)
        candidate = intakes.create(
            client_email="", client_phone="", scope="", budget_text="", preset={},
            created_by="owner-admin@neptune.ph",
        )
        _walk(candidate.id, *steps)
        resp = finalize_client.post(f"/api/client/{candidate.token}/finalize")
        ok(
            f"finalize from {label}: refused with the same opaque body as an unknown token",
            resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
        )

    workspaces.use(notif_ws.id)
    finalize_closed = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="owner-admin@neptune.ph",
    )
    intakes.close(finalize_closed.id, "owner-admin@neptune.ph")
    resp = finalize_client.post(f"/api/client/{finalize_closed.token}/finalize")
    ok(
        "finalize from closed: refused with the same opaque body as an unknown token",
        resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # A second finalize, now that the intake is already finalized, is refused
    # the same way - and does not re-notify anybody. Counted by kind, not by
    # mailbox length: `inbox.listing()` defaults to the newest 30 and `_save`
    # trims old notes, so a raw length comparison would still pass if a note
    # were both added and trimmed - unlikely with one note in a fresh
    # mailbox, but not what "no second notification" actually claims. Read
    # fresh here, not from the `owner_mail` snapshot taken earlier - the
    # ghost and dangling-bundle fixtures above have each sent owner-admin
    # a further `intake.finalized` note of their own in the meantime, and a
    # stale count would compare against a number that no longer reflects
    # what is actually on disk.
    owner_finalized_notes_before = sum(
        1
        for note in inbox.listing(person=inbox.key_for("owner-admin@neptune.ph"))
        if note.kind == "intake.finalized"
    )
    refinalize = finalize_client.post(f"/api/client/{finalize_sent.token}/finalize")
    ok(
        "finalize twice: refused with the same opaque body",
        refinalize.status_code == 404
        and refinalize.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )
    owner_finalized_notes_after = sum(
        1
        for note in inbox.listing(person=inbox.key_for("owner-admin@neptune.ph"))
        if note.kind == "intake.finalized"
    )
    ok(
        "finalize twice: no second notification was written",
        owner_finalized_notes_after == owner_finalized_notes_before,
    )

    # --- The rate limit: the 21st write from one IP inside a minute is 429 ---

    rate_ip_a = _client_for("203.0.113.21")
    rate_ip_b = _client_for("203.0.113.22")

    workspaces.use(home.id)
    rate_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    rate_body = {"client_email": "rate@client.com", "scope": "x", "budget_text": "y"}

    rate_statuses = [
        rate_ip_a.post(f"/api/client/{rate_fixture.token}/submit", json=rate_body).status_code
        for _ in range(20)
    ]
    ok(
        "the rate limit: 20 attempts from one IP inside a minute are each judged on their own "
        "merits, never 429 - the first succeeds (200) and the rest are refused for having "
        "already submitted (404), not for the rate itself",
        rate_statuses[0] == 200 and all(status == 404 for status in rate_statuses[1:]),
    )

    rate_21st = rate_ip_a.post(f"/api/client/{rate_fixture.token}/submit", json=rate_body)
    ok("the rate limit: the 21st attempt from that same IP is refused with 429", rate_21st.status_code == 429)

    rate_other_ip = rate_ip_b.post(f"/api/client/{rate_fixture.token}/submit", json=rate_body)
    ok(
        "the rate limit: a different IP hitting the same token right after is unaffected - "
        "still judged on the merits (404, already submitted), not swept into the first "
        "address's limit",
        rate_other_ip.status_code == 404
        and rate_other_ip.json() == {"detail": main_module._CLIENT_LINK_GONE},
    )

    # --- The rate limit runs before FastAPI ever parses the body -------------
    #
    # `rate_ip_a` is already over budget on `submit` from the block above.
    # Sent a deliberately malformed body - not valid JSON at all - and if the
    # limiter genuinely runs first, the answer is still 429; if it ran from
    # inside the handler (where it used to live, behind `body:
    # ClientSubmitRequest` as a parameter), FastAPI would have already
    # rejected the malformed body with a 422 before the handler - and this
    # check - ever ran.
    malformed_but_over_budget = rate_ip_a.post(
        f"/api/client/{rate_fixture.token}/submit",
        content=b"{this is not valid json at all",
        headers={"Content-Type": "application/json"},
    )
    ok(
        "a malformed body from an address already over its rate-limit budget still gets "
        "429, not 422 - proof the check runs ahead of FastAPI's own body parsing",
        malformed_but_over_budget.status_code == 429,
    )

    # --- An oversized declared body is refused before a byte of it is read ---

    oversized_client = _client_for("203.0.113.40")
    oversized_body = {
        "client_email": "x@y.com",
        "client_phone": "",
        "scope": "x" * (main_module._MAX_CLIENT_BODY_BYTES + 1000),  # noqa: SLF001
        "budget_text": "y",
    }
    oversized_resp = oversized_client.post(
        f"/api/client/{rate_fixture.token}/submit", json=oversized_body
    )
    ok(
        "a body whose declared Content-Length exceeds the cap is refused with 413",
        oversized_resp.status_code == 413,
    )

    # A body comfortably inside the cap is not swept up by it - proven with a
    # fresh token so this does not collide with `rate_fixture`'s own already-
    # submitted state and read as a false pass.
    workspaces.use(home.id)
    under_cap_fixture = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    under_cap_resp = oversized_client.post(
        f"/api/client/{under_cap_fixture.token}/submit",
        json={"client_email": "x@y.com", "scope": "fine", "budget_text": "fine"},
    )
    ok(
        "a body comfortably inside the cap is unaffected by it - the same address's "
        "oversized attempt above did not poison this one",
        under_cap_resp.status_code == 200,
    )

    # --- The rate limiter's own dict never grows without bound ---------------
    #
    # `_RATE_LIMIT_MAX_TRACKED_KEYS` is patched down to a small number so this
    # does not require 5,000 real HTTP requests to observe - the mechanism
    # being proven (evict the single oldest bucket once the cap is reached) is
    # the same at any size. Both the dict and the cap are swapped back in a
    # `finally`, the same discipline this file already uses for `generate_estimate`,
    # `tokens.resolve` and `clientview.of`.
    _real_rate_limit_hits = main_module._rate_limit_hits
    _real_max_tracked_keys = main_module._RATE_LIMIT_MAX_TRACKED_KEYS
    try:
        main_module._rate_limit_hits = {}
        main_module._RATE_LIMIT_MAX_TRACKED_KEYS = 3
        eviction_clients = [_client_for(f"203.0.113.{50 + i}") for i in range(5)]
        workspaces.use(home.id)
        eviction_fixture = intakes.create(
            client_email="", client_phone="", scope="", budget_text="", preset={},
            created_by="admin@neptune.ph",
        )
        for eviction_client in eviction_clients:
            eviction_client.post(
                f"/api/client/{eviction_fixture.token}/submit",
                json={"scope": "x", "budget_text": "y"},
            )
        ok(
            "the rate limiter's dict reached its tracked-key cap rather than growing to one "
            "entry per distinct address",
            len(main_module._rate_limit_hits) == 3,
        )
        ok(
            "and it holds the three most recently first-seen addresses, not an arbitrary "
            "three - the two earliest callers were evicted, the three latest were not",
            {key[0] for key in main_module._rate_limit_hits}
            == {"203.0.113.52", "203.0.113.53", "203.0.113.54"},
        )
    finally:
        main_module._rate_limit_hits = _real_rate_limit_hits
        main_module._RATE_LIMIT_MAX_TRACKED_KEYS = _real_max_tracked_keys

    # ==========================================================================
    # Task 5: the client reads the quotation - GET .../quotation.html and .pdf
    # ==========================================================================
    #
    # The plan calls this the single most important test in it, and this route
    # the single most likely real bug in the feature: PRISM writes two
    # documents from one estimate, the proposal a client is meant to read and
    # the studio's own internal one beside it, and the existing file routes
    # (`printable_html`, `download_pdf`) take which of the two to serve as a
    # `{kind}` path segment. If `kind` were ever reachable from anything this
    # anonymous door accepts, a client could read the internal document. The
    # fix is that `kind` is not a parameter anywhere on this route at all - see
    # `_client_quotation`'s own docstring in main.py - and this section is
    # built to prove that claim rather than merely exercise the happy path.

    quotation_client = _client_for("203.0.113.60")

    # --- The plan's own rule, enforced rather than attested. A `grep` in a
    #     report is a point-in-time check of a file that can drift the very
    #     next commit; this is the same check, run every time this script
    #     runs, against the actual source of the three functions this task's
    #     whole fix depends on - `_client_quotation` and the two routes that
    #     call it. Reads live source via `inspect.getsource`, not a copy, so
    #     it cannot go stale relative to what is actually deployed.
    _GUARDED_FUNCTIONS = (
        main_module._client_quotation,  # noqa: SLF001
        main_module.client_quotation_html,
        main_module.client_quotation_pdf,
    )
    _guarded_source = "\n".join(inspect.getsource(fn) for fn in _GUARDED_FUNCTIONS)
    ok(
        "the banned word is enforced by this test, not merely attested in a report: "
        "'requirement' does not appear anywhere in _client_quotation, "
        "client_quotation_html or client_quotation_pdf",
        "requirement" not in _guarded_source.lower(),
    )

    # A marker with no spaces or hyphens and a modest length: long enough to
    # be unmistakable, short enough that reportlab's paragraph layout has no
    # reason to break it across a line - the fixture-control checks just below
    # prove directly that it survives markdown, HTML and PDF-plus-text-
    # extraction intact, so a failure later can never be blamed on the marker
    # itself going missing along the way.
    QUOTATION_MARKER = "PRISMSECRETSHEETZQ9"
    QUOTATION_TITLE = "Task 5 Client-Facing Title"

    QUOTATION_ESTIMATE = FIXTURE_ESTIMATE.model_copy(
        update={
            "project_name": "Task 5 Fixture",
            "client": FIXTURE_ESTIMATE.client.model_copy(update={"title": QUOTATION_TITLE}),
            # The internal document's only distinctive content. `developer`
            # is read exclusively by `render_developer_requirements` (see
            # `renderers/markdown.py`) - `render_client_proposal` never opens
            # this field - so the marker landing in the client's response
            # would mean the wrong document was served, not a stray field
            # leaking through the right one.
            "developer": DeveloperSpec(
                overview=(
                    f"Internal engineering notes only, never for a client. "
                    f"Marker {QUOTATION_MARKER}."
                )
            ),
        }
    )

    # --- Fixture control: prove the marker actually lands where it should,
    #     and only there, before a single HTTP request runs. Every assertion
    #     below this point that checks the marker is *absent* from a client
    #     response is only meaningful because these checks first establish
    #     that the marker is *present* somewhere real - without this, a typo
    #     that dropped the marker from the estimate entirely would make every
    #     "absent" assertion pass for the wrong reason.
    _internal_markdown = main_module.render_developer_requirements(QUOTATION_ESTIMATE)
    ok(
        "fixture control: the internal document's own markdown carries the marker",
        QUOTATION_MARKER in _internal_markdown,
    )
    _proposal_markdown = main_module.render_client_proposal(QUOTATION_ESTIMATE)
    ok(
        "fixture control: the proposal's own markdown does not carry the marker - it was "
        "never written into a field that renderer reads",
        QUOTATION_MARKER not in _proposal_markdown,
    )
    _internal_pdf_direct = main_module.render_pdf(
        _internal_markdown, "Fixture control", QUOTATION_ESTIMATE, kind="requirements"
    )
    _internal_pdf_text_direct = pdf_text(_internal_pdf_direct)
    ok(
        "fixture control: the marker survives PDF rendering and text extraction intact - "
        "the PDF leak assertions below can actually catch a leak, not just pass vacuously",
        QUOTATION_MARKER in _internal_pdf_text_direct,
    )

    # `home`'s `studio_name` is already distinctive ("Alpha Studio Name",
    # set at the top of this file) - reused here rather than duplicated,
    # since it is the plainest proof the letterhead reads the studio's own
    # name and not the tool's ("PRISM", `render_print_html`'s bare default
    # when nothing is passed). A footer note on the studio's saved design is
    # added here as a second, independent marker: colour, font and margin
    # choices do not survive text extraction the way a literal string does,
    # so this is what actually proves `design=` itself reaches the PDF
    # renderer too, not just the HTML one.
    workspaces.use(home.id)
    _home_settings = settings.load()
    settings.save(
        _home_settings.model_copy(
            update={
                "proposal_design": _home_settings.proposal_design.model_copy(
                    update={"footer_note": "ALPHA-FOOTER-MARK-7"}
                )
            }
        )
    )

    quotation_sent, quotation_bundle = _sent_intake(
        home, "author-q5@neptune.ph", estimate=QUOTATION_ESTIMATE
    )

    # --- .html for a sent intake: the proposal, not the internal document ---

    baseline_html = quotation_client.get(f"/api/client/{quotation_sent.token}/quotation.html")
    ok("quotation.html for a sent intake: 200", baseline_html.status_code == 200)
    ok(
        "quotation.html: it is the proposal - the client's own title is on the page",
        QUOTATION_TITLE in baseline_html.text,
    )
    ok(
        "quotation.html: the internal document's marker is not on the page - the leak "
        "test itself",
        QUOTATION_MARKER not in baseline_html.text,
    )
    # A route-level drift - the content stays the proposal but the *label*
    # passed to the renderer says otherwise - is not a content leak, but it
    # hands the buyer a page that presents itself as the studio's own
    # internal duplicate. `renderers/html.py`'s `_is_duplicate` is what
    # decides the letterhead and the body class; checked directly against
    # both tells, and their opposites' absence, so a partial fix (say, the
    # label but not the class) cannot pass by accident. The body class is
    # matched as the full `class="doc doc--original"` attribute, not a bare
    # substring: the embedded stylesheet unconditionally defines *both*
    # `.doc--original` and `.doc--duplicate` CSS selectors regardless of
    # which document is served, so a bare `"doc--duplicate" in text` check
    # would always be true and this assertion would never be able to fail.
    ok(
        "quotation.html: presents itself as the original, for the client - not the "
        "studio's own internal duplicate, on both of the renderer's tells (body class, "
        "letterhead label) and their opposites, both absent",
        'class="doc doc--original"' in baseline_html.text
        and "Original · for the client" in baseline_html.text
        and 'class="doc doc--duplicate"' not in baseline_html.text
        and "Duplicate · for the developer" not in baseline_html.text,
    )
    ok(
        "quotation.html: the letterhead carries the studio's own name, not the tool's "
        "('PRISM' is render_print_html's bare default when no brand is passed)",
        "Alpha Studio Name" in baseline_html.text,
    )
    ok(
        "quotation.html: the studio's saved design actually reached the renderer - its "
        "footer note is on the page",
        "ALPHA-FOOTER-MARK-7" in baseline_html.text,
    )

    # --- .pdf for the same intake: same proof, through the PDF renderer -----

    baseline_pdf = quotation_client.get(f"/api/client/{quotation_sent.token}/quotation.pdf")
    ok("quotation.pdf for a sent intake: 200", baseline_pdf.status_code == 200)
    ok(
        "quotation.pdf: content-type is application/pdf",
        baseline_pdf.headers.get("content-type", "").startswith("application/pdf"),
    )
    baseline_pdf_text = pdf_text(baseline_pdf.content)
    ok(
        "quotation.pdf: it is the proposal - the client's own title is in the extracted text",
        QUOTATION_TITLE in baseline_pdf_text,
    )
    ok(
        "quotation.pdf: the internal document's marker is not in the extracted text - the "
        "leak test itself, through the PDF path",
        QUOTATION_MARKER not in baseline_pdf_text,
    )
    # Same "original, not the duplicate" property as .html above, checked the
    # way a PDF actually exposes it: the downloaded filename. `_filename`
    # picks "quotation" or "requirements" off the same route-level `kind`
    # `renderers/html.py`'s label reads - a drift there mislabels the
    # download exactly as it mislabels the printed page.
    baseline_pdf_disposition = baseline_pdf.headers.get("content-disposition", "")
    ok(
        "quotation.pdf: the downloaded filename says quotation, not the internal "
        "document's own name",
        "quotation" in baseline_pdf_disposition and "requirements" not in baseline_pdf_disposition,
    )
    ok(
        "quotation.pdf: the studio's saved design reached the PDF renderer too - its "
        "footer note is in the extracted text",
        "ALPHA-FOOTER-MARK-7" in baseline_pdf_text,
    )

    # --- X-Workspace is ignored here too - the token alone decides the ------
    #     workspace, exactly as `read_client_view` promises, and this route's
    #     own render step runs entirely inside the borrowed workspace to
    #     guarantee it (see `_client_quotation`'s own docstring).
    crossed_quotation = quotation_client.get(
        f"/api/client/{quotation_sent.token}/quotation.html", headers={"X-Workspace": other.id}
    )
    ok(
        "quotation.html ignores X-Workspace: still 200 with home's own document",
        crossed_quotation.status_code == 200 and crossed_quotation.content == baseline_html.content,
    )

    # --- There is no parameter, anywhere, by which a client can ask for the -
    #     internal document. Each attempt below either 404s outright or comes
    #     back byte-identical (PDF: text-identical, since reportlab stamps a
    #     fresh `/CreationDate` into every render) to the untampered baseline
    #     - "ignored, not honoured" means the second document never renders
    #     AND the first one renders exactly as it would have without the
    #     tampering, not merely "some 200 that happens to lack the marker".

    TAMPER_HTML_ATTEMPTS = [
        ("?kind=requirements query param", f"/api/client/{quotation_sent.token}/quotation.html?kind=requirements"),
        (
            "?kind=proposal query param (must be ignored, not specially honoured either)",
            f"/api/client/{quotation_sent.token}/quotation.html?kind=proposal",
        ),
        (
            "a differently-shaped path naming the internal document directly",
            f"/api/client/{quotation_sent.token}/quotation.requirements.html",
        ),
        (
            "a path-traversal attempt inside the extension segment (percent-encoded)",
            f"/api/client/{quotation_sent.token}/quotation.%2e%2e%2Frequirements.html",
        ),
        (
            "a second, literal-dots traversal spelling",
            f"/api/client/{quotation_sent.token}/quotation.html/../quotation.requirements.html",
        ),
    ]
    for label, path in TAMPER_HTML_ATTEMPTS:
        resp = quotation_client.get(path)
        ok(
            f"quotation.html tamper - {label}: either 404 or byte-identical to the "
            "untampered proposal",
            resp.status_code == 404 or resp.content == baseline_html.content,
        )
        ok(
            f"quotation.html tamper - {label}: the internal document's marker never "
            "appears either way",
            QUOTATION_MARKER.encode() not in resp.content,
        )

    # A `kind` smuggled in a JSON body on the GET itself - nothing on this
    # route declares a body parameter, so FastAPI never even parses it.
    body_attempt = quotation_client.request(
        "GET",
        f"/api/client/{quotation_sent.token}/quotation.html",
        json={"kind": "requirements"},
    )
    ok(
        "quotation.html tamper - kind in a JSON body: either 404 or byte-identical to the "
        "untampered proposal",
        body_attempt.status_code == 404 or body_attempt.content == baseline_html.content,
    )
    ok(
        "quotation.html tamper - kind in a JSON body: the internal document's marker "
        "never appears",
        QUOTATION_MARKER.encode() not in body_attempt.content,
    )

    # The same tampering shapes against .pdf - text-compared, since two PDF
    # renders of the same markdown are not byte-identical (reportlab writes a
    # fresh `/CreationDate` on every call).
    TAMPER_PDF_ATTEMPTS = [
        ("?kind=requirements query param", f"/api/client/{quotation_sent.token}/quotation.pdf?kind=requirements"),
        (
            "a differently-shaped path naming the internal document directly",
            f"/api/client/{quotation_sent.token}/quotation.requirements.pdf",
        ),
    ]
    for label, path in TAMPER_PDF_ATTEMPTS:
        resp = quotation_client.get(path)
        if resp.status_code == 404:
            ok(f"quotation.pdf tamper - {label}: refused with 404", True)
        else:
            text = pdf_text(resp.content)
            ok(
                f"quotation.pdf tamper - {label}: 200 still means the untampered proposal "
                "text",
                text == baseline_pdf_text,
            )
            ok(
                f"quotation.pdf tamper - {label}: the internal document's marker never "
                "appears",
                QUOTATION_MARKER not in text,
            )

    # --- All three states a client may actually fetch from - not just `sent`,
    #     the state every check above already covers. A typo that dropped
    #     `REVISION_REQUESTED` or `FINALIZED` from the route's own allow-list
    #     would pass every assertion above and be caught only here.

    revreq_sent, _ = _sent_intake(home, "author-q5-revreq@neptune.ph", estimate=QUOTATION_ESTIMATE)
    intakes.advance(
        revreq_sent.id,
        intakes.REVISION_REQUESTED,
        revisions=[{"asked": "Please reconsider the timeline.", "at": "2026-01-01T00:00:00Z"}],
    )
    revreq_html = quotation_client.get(f"/api/client/{revreq_sent.token}/quotation.html")
    ok("quotation.html from revision_requested: 200", revreq_html.status_code == 200)
    ok(
        "quotation.html from revision_requested: still the proposal, marker still absent",
        QUOTATION_TITLE in revreq_html.text and QUOTATION_MARKER not in revreq_html.text,
    )

    finalized_sent, _ = _sent_intake(home, "author-q5-final@neptune.ph", estimate=QUOTATION_ESTIMATE)
    intakes.advance(finalized_sent.id, intakes.FINALIZED)
    finalized_html = quotation_client.get(f"/api/client/{finalized_sent.token}/quotation.html")
    ok("quotation.html from finalized: 200", finalized_html.status_code == 200)
    ok(
        "quotation.html from finalized: still the proposal, marker still absent",
        QUOTATION_TITLE in finalized_html.text and QUOTATION_MARKER not in finalized_html.text,
    )

    # --- Every other state is refused, with the same opaque body an unknown -
    #     token gets - `issued`, `submitted`, `preparing`, `quote_failed` and
    #     `quoted` all come before a quotation was ever sent, and `closed`
    #     never appears in the route's own allow-list at all.

    QUOTATION_REFUSAL_FIXTURES = (
        ("issued", ()),
        ("submitted", ((intakes.SUBMITTED, {}),)),
        ("preparing", ((intakes.SUBMITTED, {}), (intakes.PREPARING, {"job_id": "x"}))),
        (
            "quote_failed",
            (
                (intakes.SUBMITTED, {}),
                (intakes.PREPARING, {"job_id": "x"}),
                (intakes.QUOTE_FAILED, {"error": "simulated failure"}),
            ),
        ),
        ("quoted", _QUOTED_STEPS),
    )
    for label, steps in QUOTATION_REFUSAL_FIXTURES:
        workspaces.use(home.id)
        candidate = intakes.create(
            client_email="", client_phone="", scope="", budget_text="", preset={},
            created_by="admin@neptune.ph",
        )
        _walk(candidate.id, *steps)
        for suffix in ("quotation.html", "quotation.pdf"):
            resp = quotation_client.get(f"/api/client/{candidate.token}/{suffix}")
            ok(
                f"{suffix} from {label}: refused with the same opaque body as an unknown token",
                resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
            )

    workspaces.use(home.id)
    quotation_closed = intakes.create(
        client_email="", client_phone="", scope="", budget_text="", preset={},
        created_by="admin@neptune.ph",
    )
    intakes.close(quotation_closed.id, "admin@neptune.ph")
    for suffix in ("quotation.html", "quotation.pdf"):
        resp = quotation_client.get(f"/api/client/{quotation_closed.token}/{suffix}")
        ok(
            f"{suffix} from closed: refused with the same opaque body as an unknown token",
            resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
        )

    for suffix in ("quotation.html", "quotation.pdf"):
        resp = quotation_client.get(f"/api/client/not-a-real-token-at-all/{suffix}")
        ok(
            f"{suffix} for an unknown token: refused with the same opaque body",
            resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
        )

    # --- A sent intake whose bundle has since been deleted is refused, not a
    #     500 - the same dangling-bundle case `/revise` and `/finalize`
    #     already guard, reached here through a third door.
    dangling_q_sent, dangling_q_bundle = _sent_intake(
        home, "author-q5-dangling@neptune.ph", estimate=QUOTATION_ESTIMATE
    )
    storage.delete(dangling_q_bundle.id)
    ok(
        "dangling-bundle quotation fixture: the bundle is genuinely gone",
        storage.get(dangling_q_bundle.id) is None,
    )
    for suffix in ("quotation.html", "quotation.pdf"):
        resp = quotation_client.get(f"/api/client/{dangling_q_sent.token}/{suffix}")
        ok(
            f"{suffix} against a sent intake whose bundle was deleted: opaque refusal, "
            "not a 500 with a traceback",
            resp.status_code == 404 and resp.json() == {"detail": main_module._CLIENT_LINK_GONE},
        )

    # --- Both routes answer the same way for a renderer bug ------------------
    #
    # `.pdf` always wrapped `render_pdf` in a try/except; `.html` did not wrap
    # `render_print_html` the same way until this round of review found the
    # asymmetry. Proven directly rather than assumed symmetric: patch each
    # renderer in turn to raise, and confirm both routes answer 500 with a
    # real body - not a traceback, and not this door's opaque "gone" text
    # either (a renderer bug is not the same thing as "that link is dead").

    def _boom_render(*_args, **_kwargs):
        raise RuntimeError("simulated renderer bug")

    _real_render_print_html = main_module.render_print_html
    try:
        main_module.render_print_html = _boom_render
        html_boom = quotation_client.get(f"/api/client/{quotation_sent.token}/quotation.html")
    finally:
        main_module.render_print_html = _real_render_print_html
    ok(
        "quotation.html: a renderer bug answers 500 with a real body, not a traceback and "
        "not this door's opaque 'gone' text",
        html_boom.status_code == 500
        and html_boom.json() != {"detail": main_module._CLIENT_LINK_GONE},
    )

    _real_render_pdf = main_module.render_pdf
    try:
        main_module.render_pdf = _boom_render
        pdf_boom = quotation_client.get(f"/api/client/{quotation_sent.token}/quotation.pdf")
    finally:
        main_module.render_pdf = _real_render_pdf
    ok(
        "quotation.pdf: a renderer bug answers 500 with a real body too - the same shape "
        "quotation.html now answers with, not merely a coincidence of two separate "
        "code paths",
        pdf_boom.status_code == 500 and pdf_boom.json() != {"detail": main_module._CLIENT_LINK_GONE},
    )

    # --- .pdf runs off the event loop; .html does not, on purpose ------------
    #
    # `client_quotation_pdf` is a plain `def` (FastAPI runs it on its
    # threadpool) because building the PDF is real, measurable CPU work on an
    # anonymous, unrated-limited door. `client_quotation_html` stays
    # `async def` - it does no comparable CPU work. A regression guard, not a
    # behavioural test: this is what stops someone "helpfully" making the PDF
    # route `async def` again, which `inspect` can tell apart from the
    # outside without needing to measure latency to prove it.
    ok(
        "client_quotation_pdf is a plain `def` - FastAPI runs it on its threadpool, off "
        "the event loop, since reportlab's layout pass is real CPU work on a door with no "
        "rate limit of its own",
        not inspect.iscoroutinefunction(main_module.client_quotation_pdf),
    )
    ok(
        "client_quotation_html stays `async def` - it does no comparable CPU work, so "
        "this is a deliberate asymmetry with .pdf, not an oversight",
        inspect.iscoroutinefunction(main_module.client_quotation_html),
    )

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
