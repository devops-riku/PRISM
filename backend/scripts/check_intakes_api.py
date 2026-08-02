"""The intake routes: what they return, and who is allowed to call them.

Offline. No model, no network:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intake-api-")
# `app.config` reads these once at import time via `load_dotenv(..., override=False)`,
# so a real backend/.env (this repo's has a Supabase project configured) would
# otherwise win and turn on token verification - every request below is
# headerless, so the gate would 401 all of them before a single route ran.
# See scripts/check_kind_api.py for the same fix.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app import intakes as intakes_module  # noqa: E402
from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app)
headers = {"X-Workspace": made.id}

created = client.post(
    "/api/intakes",
    headers=headers,
    json={
        "client_email": "buyer@client.com",
        "client_phone": "+63 917 000 0000",
        "scope": "A booking site for two clinics.",
        "budget_text": "around 300k",
        "preset": {"kind": "software", "currency": "PHP"},
    },
)
ok("creating an intake answers 201", created.status_code == 201)
body = created.json()
# Stage 2: `intakes.create` now starts every intake `issued`, with a link
# already minted, rather than `submitted` - this route still collects the
# client's words directly (that does not change until a later task rewires
# it), but the record it produces is issued from the moment it exists.
ok("it comes back issued, not submitted", body["state"] == "issued")
ok("and carries a client link already", bool(body.get("token")))
ok("with the client's words", body["scope"] == "A booking site for two clinics.")

# A client's words are unbounded text reaching a prompt PRISM has always
# trusted - `scope` and `budget_text` need the same ceiling `brief` has
# always had (main.py's `_normalise_brief`), before any public route can
# reach them. Rejected outright, not silently truncated, and neither creates
# a row - the count check below still expects exactly one.
over_scope = client.post(
    "/api/intakes",
    headers=headers,
    json={"scope": "x" * (config.MAX_BRIEF_CHARS + 1)},
)
ok("an over-length scope is refused, not truncated silently", over_scope.status_code == 400)

over_budget = client.post(
    "/api/intakes",
    headers=headers,
    json={"scope": "Fine.", "budget_text": "x" * (config.MAX_BRIEF_CHARS + 1)},
)
ok("an over-length budget_text is refused the same way", over_budget.status_code == 400)

listed = client.get("/api/intakes", headers=headers)
ok("the queue lists it", listed.status_code == 200 and len(listed.json()) == 1)

read = client.get(f"/api/intakes/{body['id']}", headers=headers)
ok("it reads back by id", read.status_code == 200 and read.json()["id"] == body["id"])

ok(
    "an unknown id is 404, not 500",
    client.get("/api/intakes/000000000000", headers=headers).status_code == 404,
)
ok(
    "a malformed (non-hex) id is 404, not 500 - intakes.get()'s own guard, "
    "not anything the route layer re-checks",
    client.get("/api/intakes/zzzzzzzzzzzz", headers=headers).status_code == 404,
)
ok(
    "closing a malformed id is 404, not 500",
    client.post("/api/intakes/zzzzzzzzzzzz/close", headers=headers).status_code == 404,
)

closed = client.post(f"/api/intakes/{body['id']}/close", headers=headers)
ok("closing answers 200", closed.status_code == 200)
ok("and the state says so", closed.json()["state"] == "closed")

# An intake belongs to its workspace and must not be visible from another.
other = workspaces.create("Someone Else")
ok(
    "another workspace sees none of them",
    client.get("/api/intakes", headers={"X-Workspace": other.id}).json() == [],
)
ok(
    "and cannot read one by id",
    client.get(f"/api/intakes/{body['id']}", headers={"X-Workspace": other.id}).status_code == 404,
)
ok(
    "nor close one by id - isolation holds on a write, not just on reads",
    client.post(
        f"/api/intakes/{body['id']}/close", headers={"X-Workspace": other.id}
    ).status_code
    == 404,
)

# --- The permission model - the thing this task actually adds --------------
#
# Every request above ran with SUPABASE_JWT_SECRET blank, which makes
# `auth.required()` False - and the first line of `_require_admin` is
# `if not auth.required(): return`. None of the assertions above, including
# the ones on the admin-gated create and close routes, exercise the admin
# check at all: they would pass identically if both `_require_admin(...)`
# calls in main.py were deleted. That is the gap a member/admin split exists
# to close, so it needs its own section with auth actually turned on.
#
# `app.config`'s Supabase settings are plain module attributes that
# `app.auth` reads live on every call (`config.SUPABASE_JWT_SECRET.strip()`),
# not values captured once when `app.auth` was imported - so setting the
# attribute directly, after `app.main` has already imported everything,
# is enough to turn HS256 verification on for the rest of this process.
# No second interpreter and no real Supabase project needed.
import time  # noqa: E402

import jwt  # noqa: E402

from app import auth as auth_module  # noqa: E402
from app import members  # noqa: E402

TEST_JWT_SECRET = "check-intakes-api-test-secret-do-not-reuse-32bytes"
config.SUPABASE_JWT_SECRET = TEST_JWT_SECRET
ok("flipping SUPABASE_JWT_SECRET turns auth.required() on", auth_module.required())


def _token(sub: str, email: str) -> str:
    """A signed HS256 access token shaped like the ones Supabase issues -
    just `sub` and `email`, which is all `auth.User` reads."""
    return jwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


# A fresh workspace with a real two-person roster: an admin and a member,
# seeded through members.py's own API (claim, then invite-and-accept) so this
# exercises the same roster shape a real signed-in team has, not a hand-built
# roster file that happens to parse.
secured = workspaces.create("Secured Co")
workspaces.use(secured.id)
members.claim("admin@neptune.ph", "admin-uid")
offer = members.invite("member@neptune.ph", members.MEMBER, "admin@neptune.ph")
members.accept(offer.token, "member@neptune.ph", "member-uid")

admin_token = _token("admin-uid", "admin@neptune.ph")
member_token = _token("member-uid", "member@neptune.ph")


def _as(token: str) -> dict:
    return {"X-Workspace": secured.id, "Authorization": f"Bearer {token}"}


admin_headers = _as(admin_token)
member_headers = _as(member_token)
no_token_headers = {"X-Workspace": secured.id}  # workspace named, nobody signed in

ok(
    "a member cannot create an intake: 403",
    client.post(
        "/api/intakes", headers=member_headers, json={"scope": "A member's attempt."}
    ).status_code
    == 403,
)

admin_created = client.post(
    "/api/intakes", headers=admin_headers, json={"scope": "An admin's request."}
)
ok("an admin can create one: 201", admin_created.status_code == 201)
secured_id = admin_created.json()["id"]

ok(
    "a member cannot close an intake: 403",
    client.post(f"/api/intakes/{secured_id}/close", headers=member_headers).status_code == 403,
)
ok(
    "an admin can close the same one: 200",
    client.post(f"/api/intakes/{secured_id}/close", headers=admin_headers).status_code == 200,
)

# Reading isn't gated - only issuing and closing are - so a member sees both.
ok(
    "a member can list the queue: 200",
    client.get("/api/intakes", headers=member_headers).status_code == 200,
)
ok(
    "a member can read one by id: 200",
    client.get(f"/api/intakes/{secured_id}", headers=member_headers).status_code == 200,
)

# No token at all is refused by the gate before any route runs - true for
# routes a member may call as much as ones only an admin may.
ok(
    "no Authorization header: creating is 401",
    client.post(
        "/api/intakes", headers=no_token_headers, json={"scope": "No token."}
    ).status_code
    == 401,
)
ok(
    "no Authorization header: listing is 401",
    client.get("/api/intakes", headers=no_token_headers).status_code == 401,
)
ok(
    "no Authorization header: reading one is 401",
    client.get(f"/api/intakes/{secured_id}", headers=no_token_headers).status_code == 401,
)
ok(
    "no Authorization header: closing is 401",
    client.post(f"/api/intakes/{secured_id}/close", headers=no_token_headers).status_code
    == 401,
)

# --- A write failure must not be disguised as a missing record -------------
#
# intakes.close() raises IntakeError both when an id does not exist and when
# a write failed after it found one, and the route has to tell those apart:
# 404 for the first, 500 for the second. Nothing else in this suite can make
# a real disk write fail on demand, so the failure is forced directly on the
# module the route calls - proving the route's own branch, not the
# filesystem's mood.
_real_close = intakes_module.close
_real_create = intakes_module.create


def _boom_close(*_args, **_kwargs):
    raise intakes_module.IntakeError("disk exploded")


def _boom_create(*_args, **_kwargs):
    raise intakes_module.IntakeError("disk exploded")


try:
    intakes_module.close = _boom_close
    broken_close = client.post(f"/api/intakes/{secured_id}/close", headers=admin_headers)
finally:
    intakes_module.close = _real_close
ok(
    "a write failure closing an id that does exist is 500, not 404",
    broken_close.status_code == 500,
)

try:
    intakes_module.create = _boom_create
    broken_create = client.post(
        "/api/intakes", headers=admin_headers, json={"scope": "Also a regression fixture."}
    )
finally:
    intakes_module.create = _real_create
ok("a write failure creating one is 500, not 409", broken_create.status_code == 500)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
