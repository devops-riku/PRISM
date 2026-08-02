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
from app import main as main_module  # noqa: E402
from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def _token_from_link(link: str) -> str:
    return link.rsplit("/", 1)[-1]


def _quoted(bundle_ids: list) -> intakes_module.Intake:
    """An intake walked straight to `quoted`, with `bundle_ids` set exactly as
    given - through the module, not the API, since there is no model here to
    actually produce a quotation. Assumes the caller has already pointed
    `workspaces` at the workspace this should live in."""
    entry = intakes_module.create(
        client_email="", client_phone="", scope="", budget_text="", preset={}, created_by="",
    )
    intakes_module.advance(entry.id, intakes_module.SUBMITTED)
    intakes_module.advance(entry.id, intakes_module.PREPARING, job_id="job-fixture")
    return intakes_module.advance(entry.id, intakes_module.QUOTED, bundle_ids=list(bundle_ids))


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app)
headers = {"X-Workspace": made.id}

# --- Creation: a preset, no client words -------------------------------------

created = client.post(
    "/api/intakes",
    headers=headers,
    json={
        "preset": {
            "kind": "software",
            "currency": "php",
            "market_region": "Philippines",
            "tiers": "Basic, Standard",
        }
    },
)
ok("creating an intake answers 201", created.status_code == 201)
body = created.json()
ok("it comes back issued", body["state"] == "issued")
ok("with the preset stored", body["preset"]["kind"] == "software")
ok("currency in the preset is normalised the same way /api/proposals normalises it", body["preset"]["currency"] == "PHP")
ok("no client words were ever collected - scope is empty", body["scope"] == "")
ok("nor budget_text", body["budget_text"] == "")

# The token is a bearer credential, not a field: `GET /api/intakes` and
# `GET /api/intakes/{id}` have no admin check by design (any member may read
# the queue), so the link that gates an unauthenticated route (Task 3) must
# never reach a response that carries it under its own name. `intakes.py`
# marks the field `exclude=True` for exactly this. `POST /api/intakes` is the
# one call that is allowed to hand it back - Task 1's own ruling - and it does
# so as a derived `link`, not as `token` itself.
ok("the raw token never reaches the wire, even here", "token" not in body)
ok("but a link is handed back instead", bool(body.get("link")))
ok("built from this server's own origin", body["link"].startswith(config.APP_ORIGIN))

created_token = _token_from_link(body["link"])
ok(
    "and the link actually works - the client door resolves it",
    client.get(f"/api/client/{created_token}").status_code == 200,
)

# A studio can still send the client's own words in the body (an old caller,
# a copy-pasted request) and they simply never land anywhere - `IntakeRequest`
# has no field for them, so pydantic drops what it does not declare rather
# than rejecting the call outright.
ignored_words = client.post(
    "/api/intakes",
    headers=headers,
    json={
        "scope": "A booking site for two clinics.",
        "budget_text": "around 300k",
        "client_email": "buyer@client.com",
        "preset": {"kind": "software"},
    },
)
ok("a body carrying old client fields still creates: 201", ignored_words.status_code == 201)
ok("...but none of them took", ignored_words.json()["scope"] == "" and ignored_words.json()["client_email"] == "")

bad_currency = client.post(
    "/api/intakes",
    headers=headers,
    json={"preset": {"currency": "not-a-code"}},
)
ok(
    "a bad currency in the preset is refused, not stored silently",
    bad_currency.status_code == 400,
)

listed = client.get("/api/intakes", headers=headers)
ok("the queue lists it", listed.status_code == 200 and len(listed.json()) >= 1)
ok("and the queue never carries a token either", "token" not in listed.json()[0])
ok("nor a link - GET /api/intakes has no admin check by design", "link" not in listed.json()[0])

read = client.get(f"/api/intakes/{body['id']}", headers=headers)
ok("it reads back by id", read.status_code == 200 and read.json()["id"] == body["id"])
ok("nor does reading it back by id carry the token", "token" not in read.json())
ok("nor the link - same reasoning as the queue", "link" not in read.json())

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

# --- Relink: a fresh link, the old one dead ----------------------------------

relinkable = client.post("/api/intakes", headers=headers, json={"preset": {}})
relinkable_id = relinkable.json()["id"]
old_token = _token_from_link(relinkable.json()["link"])

relinked = client.post(f"/api/intakes/{relinkable_id}/relink", headers=headers)
ok("relinking answers 200", relinked.status_code == 200)
relinked_body = relinked.json()
ok("the raw token still never reaches the wire", "token" not in relinked_body)
new_token = _token_from_link(relinked_body["link"])
ok("relinking mints a genuinely different token", new_token != old_token)

# Proven at the client's own door, not by reading `intakes_module.get(...)`
# back - the point of `relink` is what a stranger holding the *old* link can
# and cannot do, and this is the strongest evidence of that.
ok("the old link is dead", client.get(f"/api/client/{old_token}").status_code == 404)
ok("the new link works", client.get(f"/api/client/{new_token}").status_code == 200)

ok(
    "relinking an unknown id is 404",
    client.post("/api/intakes/000000000000/relink", headers=headers).status_code == 404,
)

closed_for_relink = client.post("/api/intakes", headers=headers, json={"preset": {}})
closed_for_relink_id = closed_for_relink.json()["id"]
client.post(f"/api/intakes/{closed_for_relink_id}/close", headers=headers)
ok(
    "relinking a closed request is refused, not a silent no-op token",
    client.post(f"/api/intakes/{closed_for_relink_id}/relink", headers=headers).status_code == 409,
)

# --- Send: bundle_id is required, checked, and stamps sent_at ---------------
#
# Task 2 added `Intake.sent_at` and nothing wrote it - every client would see
# a blank sent date forever, and no assertion anywhere would fail, until this
# route exists and is proven to stamp it.

workspaces.use(made.id)
send_target = _quoted(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])

missing_bundle = client.post(f"/api/intakes/{send_target.id}/send", headers=headers, json={})
ok("sending with no bundle_id is refused: 400", missing_bundle.status_code == 400)

wrong_bundle = client.post(
    f"/api/intakes/{send_target.id}/send",
    headers=headers,
    json={"bundle_id": "cccccccccccc"},
)
ok(
    "a bundle_id that was not quoted for this request is refused: 400",
    wrong_bundle.status_code == 400,
)
ok(
    "...and the intake was not moved by the refused attempt",
    intakes_module.get(send_target.id).state == intakes_module.QUOTED,
)

sent = client.post(
    f"/api/intakes/{send_target.id}/send",
    headers=headers,
    json={"bundle_id": "bbbbbbbbbbbb"},
)
ok("sending a bundle that was actually quoted answers 200", sent.status_code == 200)
sent_body = sent.json()
ok("the state moves quoted -> sent", sent_body["state"] == "sent")
ok("sent_bundle_id names the one actually sent, not bundle_ids[0]", sent_body["sent_bundle_id"] == "bbbbbbbbbbbb")
ok("sent_at is stamped - this is the gap Task 2 left open", bool(sent_body.get("sent_at")))

ok(
    "sending an already-sent request is refused: 409, not a silent re-send",
    client.post(
        f"/api/intakes/{send_target.id}/send",
        headers=headers,
        json={"bundle_id": "aaaaaaaaaaaa"},
    ).status_code
    == 409,
)
ok(
    "sending an unknown id is 404",
    client.post(
        "/api/intakes/000000000000/send", headers=headers, json={"bundle_id": "aaaaaaaaaaaa"}
    ).status_code
    == 404,
)

# --- Mutation proof: the bundle-membership guard actually gates the route ---
#
# A 400 above is only evidence the check works if disabling the check would
# make the assertion fail. Proven by breaking `main_module._quoted_bundle` -
# the one function `send_intake` calls to decide membership - and watching a
# bundle id that was never quoted get accepted anyway.

mutation_target = _quoted(["dddddddddddd"])
_real_quoted_bundle = main_module._quoted_bundle


def _always_quoted(*_args, **_kwargs) -> bool:
    return True


try:
    main_module._quoted_bundle = _always_quoted
    mutated = client.post(
        f"/api/intakes/{mutation_target.id}/send",
        headers=headers,
        json={"bundle_id": "cccccccccccc"},  # not in mutation_target's own bundle_ids
    )
finally:
    main_module._quoted_bundle = _real_quoted_bundle

ok(
    "mutation: with the membership guard disabled, an unquoted bundle id is accepted - "
    "proving the 400 above depends on the real check, not on something else refusing it",
    mutated.status_code == 200,
)
ok(
    "...and it would have recorded exactly the wrong bundle, which is the bug the guard exists to prevent",
    mutated.json().get("sent_bundle_id") == "cccccccccccc",
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
        "/api/intakes", headers=member_headers, json={"preset": {"kind": "software"}}
    ).status_code
    == 403,
)

admin_created = client.post(
    "/api/intakes", headers=admin_headers, json={"preset": {"kind": "software"}}
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

# Reading isn't gated - only issuing, closing, sending and relinking are - so
# a member sees both.
ok(
    "a member can list the queue: 200",
    client.get("/api/intakes", headers=member_headers).status_code == 200,
)
ok(
    "a member can read one by id: 200",
    client.get(f"/api/intakes/{secured_id}", headers=member_headers).status_code == 200,
)

# `secured_id` is closed by this point, so relink/send need fixtures of their
# own - a closed intake refuses both for reasons that have nothing to do with
# the permission model this section exists to test.
relink_target = client.post(
    "/api/intakes", headers=admin_headers, json={"preset": {}}
).json()

ok(
    "a member cannot relink an intake: 403",
    client.post(f"/api/intakes/{relink_target['id']}/relink", headers=member_headers).status_code
    == 403,
)
admin_relinked = client.post(f"/api/intakes/{relink_target['id']}/relink", headers=admin_headers)
ok("an admin can relink the same one: 200", admin_relinked.status_code == 200)
ok("...and gets a working link back, not a bare state change", bool(admin_relinked.json().get("link")))

workspaces.use(secured.id)
secured_quoted = _quoted(["eeeeeeeeeeee"])

ok(
    "a member cannot send a quotation: 403",
    client.post(
        f"/api/intakes/{secured_quoted.id}/send",
        headers=member_headers,
        json={"bundle_id": "eeeeeeeeeeee"},
    ).status_code
    == 403,
)
admin_sent = client.post(
    f"/api/intakes/{secured_quoted.id}/send",
    headers=admin_headers,
    json={"bundle_id": "eeeeeeeeeeee"},
)
ok("an admin can send the same one: 200", admin_sent.status_code == 200)

# No token at all is refused by the gate before any route runs - true for
# routes a member may call as much as ones only an admin may.
ok(
    "no Authorization header: creating is 401",
    client.post(
        "/api/intakes", headers=no_token_headers, json={"preset": {}}
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
ok(
    "no Authorization header: relinking is 401",
    client.post(f"/api/intakes/{relink_target['id']}/relink", headers=no_token_headers).status_code
    == 401,
)
ok(
    "no Authorization header: sending is 401",
    client.post(
        f"/api/intakes/{secured_quoted.id}/send",
        headers=no_token_headers,
        json={"bundle_id": "eeeeeeeeeeee"},
    ).status_code
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
        "/api/intakes", headers=admin_headers, json={"preset": {}}
    )
finally:
    intakes_module.create = _real_create
ok("a write failure creating one is 500, not 409", broken_create.status_code == 500)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
