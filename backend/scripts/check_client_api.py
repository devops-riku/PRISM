"""`GET /api/client/{token}` - the first unauthenticated route in this codebase.

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

    cd backend
    .venv/Scripts/python.exe scripts/check_client_api.py
"""

from __future__ import annotations

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

from app import auth as auth_module  # noqa: E402
from app import config  # noqa: E402
from app import intakes  # noqa: E402
from app import main as main_module  # noqa: E402
from app import members  # noqa: E402
from app import settings  # noqa: E402
from app import tokens  # noqa: E402
from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

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

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
