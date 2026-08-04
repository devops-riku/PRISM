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
# Off, because every check in this project runs OFFLINE. The brief check is a
# real Gemini call on the generation path; left on, these scripts would reach
# the network, cost money, and fail on a machine with no key. `app/main.py`
# reads the flag at call time, so this line is the whole of the opt-out.
os.environ["CHECK_BRIEF_IS_REAL"] = "0"
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
from app import intakefiles  # noqa: E402
from app import intakes as intakes_module  # noqa: E402
from app import main as main_module  # noqa: E402
from app import storage  # noqa: E402
from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import (  # noqa: E402
    ClientNarrative,
    CostSummary,
    Estimate,
    LineItem,
    PaymentMilestone,
    ProposalBundle,
    UnitKind,
)

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def _token_from_link(link: str) -> str:
    return link.rsplit("/", 1)[-1]


#: A minimal but real `Estimate`, shared by every fixture bundle below - same
#: idiom `check_client_api.py`'s own `FIXTURE_ESTIMATE` uses, for the same
#: reason: `send_intake`'s `_quoted_bundle` guard now checks existence in
#: `storage`, not only membership in `bundle_ids`, so a fabricated hex string
#: is no longer enough for anything a test expects `/send` to actually accept.
FIXTURE_ESTIMATE = Estimate(
    project_name="Task 6 Fixture",
    client_name="Fixture Client Co.",
    currency="PHP",
    client=ClientNarrative(
        title="Task 6 Fixture",
        executive_summary="A fixture proposal for the intake routes.",
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
    quotation_ref="TASK6-0000001",
)


def _real_bundle() -> str:
    """A bundle that actually exists in `storage`, in whichever workspace is
    currently ambient - not a fabricated hex string. Returns its id."""
    bundle_id = storage.new_id()
    bundle = ProposalBundle(
        id=bundle_id,
        created_at=storage.utc_now_iso(),
        estimate=FIXTURE_ESTIMATE,
        files=main_module._build_files(bundle_id, FIXTURE_ESTIMATE),  # noqa: SLF001
        revision=1,
        root_id=bundle_id,
    )
    storage.save(bundle)
    return bundle_id


def _quoted(bundle_ids: list) -> intakes_module.Intake:
    """An intake walked straight to `quoted`, with `bundle_ids` set exactly as
    given - through the module, not the API, since there is no model here to
    actually produce a quotation. Assumes the caller has already pointed
    `workspaces` at the workspace this should live in.

    `bundle_ids` should generally be ids from `_real_bundle()`, not fabricated
    strings: `send_intake`'s guard now checks existence in `storage` as well
    as membership here, so a fabricated id is only useful for proving a
    refusal - either because it names nothing at all, or because it is a
    dangling id (a member of `bundle_ids` whose actual bundle was never
    saved, or has since been deleted) - never for proving a send succeeds.
    """
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

# --- Reading a client's own file back, and the cross-intake refusal ---------
#
# `GET /api/intakes/{intake_id}/files/{file_id}` is the half of this feature
# the studio asked for by name. `intakefiles.save()` writes the bytes and the
# manifest entry directly - there is no anonymous multipart route exercised
# here, exactly as `check_client_upload.py` owns proving `/submit` itself and
# this file owns proving the routes that read back what it wrote.
workspaces.use(made.id)

FILE_A_BYTES = b"%PDF-1.4 fixture A - a client's own scope document\n"
FILE_B_BYTES = b"\x89PNG\r\n\x1a\nfixture B - a client's own screenshot\n"

intake_a = intakes_module.create(
    client_email="", client_phone="", scope="", budget_text="", preset={}, created_by="",
)
# The quote in this name is the point: `intakefiles.clean_name` keeps it, so
# the header-encoding assertions below are exercised against a real one
# rather than a filename this suite chose to be convenient.
entry_a = intakefiles.save(intake_a.id, 'sco"pe.pdf', FILE_A_BYTES, "application/pdf")
intakes_module.advance(intake_a.id, intakes_module.SUBMITTED, attachments=[entry_a])

intake_b = intakes_module.create(
    client_email="", client_phone="", scope="", budget_text="", preset={}, created_by="",
)
entry_b = intakefiles.save(intake_b.id, "shot.png", FILE_B_BYTES, "image/png")
intakes_module.advance(intake_b.id, intakes_module.SUBMITTED, attachments=[entry_b])

own_a = client.get(f"/api/intakes/{intake_a.id}/files/{entry_a['id']}", headers=headers)
ok(
    "a studio can read a file that actually belongs to this intake",
    own_a.status_code == 200 and own_a.content == FILE_A_BYTES,
)
ok("never cached", own_a.headers.get("cache-control") == "no-store")
ok(
    "an explicit content type read off the manifest",
    own_a.headers.get("content-type", "").split(";")[0] == "application/pdf",
)
ok(
    "a document outside the raster allowlist is attachment, not inline",
    own_a.headers.get("content-disposition", "").startswith("attachment;"),
)
ok("nosniff on the document branch", own_a.headers.get("x-content-type-options") == "nosniff")
ok(
    "the quote in the client's own filename cannot break the header - the ASCII "
    "fallback strips it rather than passing it through raw",
    '"sco"pe.pdf"' not in own_a.headers.get("content-disposition", "")
    and 'filename="scope.pdf"' in own_a.headers.get("content-disposition", ""),
)
ok(
    "and the real name, quote included, survives as an RFC 5987 filename*",
    "filename*=UTF-8''sco%22pe.pdf" in own_a.headers.get("content-disposition", ""),
)

own_b = client.get(f"/api/intakes/{intake_b.id}/files/{entry_b['id']}", headers=headers)
ok(
    "a raster image is inline, not attachment - the allowlist Task 3 enforced on the way in",
    own_b.status_code == 200
    and own_b.headers.get("content-disposition", "").startswith("inline;"),
)
ok(
    "nosniff on the image branch too - both branches now, not local-only, since "
    "the presigned redirect that exception existed for is gone",
    own_b.headers.get("x-content-type-options") == "nosniff",
)

cross_a_to_b = client.get(f"/api/intakes/{intake_a.id}/files/{entry_b['id']}", headers=headers)
ok(
    "a file id belonging to a different intake, fetched through this intake's "
    "own route, is 404",
    cross_a_to_b.status_code == 404,
)
cross_b_to_a = client.get(f"/api/intakes/{intake_b.id}/files/{entry_a['id']}", headers=headers)
ok("and the same refusal the other way round", cross_b_to_a.status_code == 404)

ok(
    "an unknown intake id is 404",
    client.get(f"/api/intakes/000000000000/files/{entry_a['id']}", headers=headers).status_code
    == 404,
)
ok(
    "an unknown file id is 404",
    client.get(f"/api/intakes/{intake_a.id}/files/000000000000", headers=headers).status_code
    == 404,
)
ok(
    "another workspace cannot read this intake's file either - the same "
    "isolation reading the intake itself already has",
    client.get(
        f"/api/intakes/{intake_a.id}/files/{entry_a['id']}", headers={"X-Workspace": other.id}
    ).status_code
    == 404,
)

# The case above is "this workspace's intake, asked from another workspace" -
# not the same thing as "a file id that belongs to an intake in *another*
# workspace, asked through *this* workspace's own intake", which is the pair
# the brief actually named. `intakes.get(intake_a.id)` already 404s from
# `other`'s workspace (the assertion above), so that half is covered; this is
# the other half - `intake_a` resolves fine under `headers`, and the foreign
# id is refused by `_owned_attachment` failing to find it in `intake_a`'s own
# manifest, not by `intake_a` itself being unreadable.
workspaces.use(other.id)
foreign_ws_intake = intakes_module.create(
    client_email="", client_phone="", scope="", budget_text="", preset={}, created_by="",
)
foreign_ws_entry = intakefiles.save(
    foreign_ws_intake.id, "other-workspace.pdf", FILE_A_BYTES, "application/pdf"
)
intakes_module.advance(
    foreign_ws_intake.id, intakes_module.SUBMITTED, attachments=[foreign_ws_entry]
)
workspaces.use(made.id)

ok(
    "a file id belonging to an intake in a *different workspace*, asked "
    "through this workspace's own intake, is 404 too - not merely a file id "
    "from a different intake in the same workspace",
    client.get(
        f"/api/intakes/{intake_a.id}/files/{foreign_ws_entry['id']}", headers=headers
    ).status_code
    == 404,
)

# --- A malformed manifest cannot make this route serve an arbitrary type ----
#
# `attachments` is a bare `List[dict]` in `intakes.ADVANCE_FIELDS` with no
# per-entry model - `advance()` validates nothing about a dict's contents, so
# `kind` being `intakefiles.resolve_type`'s own output is a convention `/submit`
# happens to follow, not something enforced at the write. Proven reachable
# directly through `advance()`, the same door `/submit` itself uses.
mistyped_target = intakes_module.create(
    client_email="", client_phone="", scope="", budget_text="", preset={}, created_by="",
)
mistyped_entry = intakefiles.save(mistyped_target.id, "innocuous.pdf", FILE_A_BYTES, "application/pdf")
# A `kind` `intakefiles.save()` would never itself produce - stored fine,
# since storage never re-checks a manifest string, and `advance()` has no
# model behind `attachments` to refuse it either.
intakes_module.advance(
    mistyped_target.id,
    intakes_module.SUBMITTED,
    attachments=[dict(mistyped_entry, kind="text/html")],
)
mistyped_response = client.get(
    f"/api/intakes/{mistyped_target.id}/files/{mistyped_entry['id']}", headers=headers
)
ok(
    "a manifest kind outside intakefiles.CONTENT_TYPES is clamped to the "
    "fallback type, not served as whatever the dict happens to say",
    mistyped_response.status_code == 200
    and mistyped_response.headers.get("content-type", "").split(";")[0]
    == intakefiles.FALLBACK_TYPE,
)
ok(
    "...and still attachment, not inline, for the same reason - a clamped "
    "type can never land inside INLINE_TYPES",
    mistyped_response.headers.get("content-disposition", "").startswith("attachment;"),
)

# --- Mutation proof: which layer is the gate, said plainly rather than implied
#
# Be honest about what this proves. The *primary* cross-intake gate on this
# route is `intakefiles.read`'s own structural binding - it ties
# `(intake_id, file_id)` together via the actual storage key or path, not by a
# comparison a route could get wrong, and check_intakefiles.py already proves
# it exhaustively. Disabling only this route's own `_owned_attachment` check
# would still 404 on that binding and would prove nothing about this route at
# all - exactly the trap Task 2's own review named: most of a traversal suite
# passes with no gate at all, because the paths involved simply do not exist.
#
# So the mutation below simulates storage's binding being *absent* first (a
# permissive stand-in for `intakefiles.read`, not a claim that the real one is
# weak), with `_owned_attachment` left real, to show that check alone still
# refuses a wrong-intake file id when nothing else would. Only then is
# `_owned_attachment` defeated too, to show it is genuinely a second,
# independent layer - not one incidentally covering for the other - rather
# than to claim either check alone is "the" gate this route depends on.
_real_read = intakefiles.read
_real_owned = main_module._owned_attachment


def _permissive_read(_intake_id, requested_file_id):
    """Stands in for a storage layer with no cross-intake gate of its own:
    answers for `entry_b`'s file - and for the *other workspace's* file -
    regardless of which intake asked, which is the bug `_owned_attachment`
    exists to survive.

    The foreign-workspace id is in here for a specific reason. Storage scopes
    its own prefix by workspace, so that fetch is 404 on the real functions
    whether `_owned_attachment` runs or not: the ungated assertion above proves
    the *behaviour* and nothing about *which layer* delivers it. Answering for
    it here removes storage from the question so the route's own check is the
    only thing left standing."""
    if requested_file_id in (entry_b["id"], foreign_ws_entry["id"]):
        return FILE_B_BYTES, "image/png"
    return _real_read(_intake_id, requested_file_id)


def _always_owned(_entry, _file_id):
    return {"name": "whatever.bin", "kind": "application/octet-stream", "bytes": 0, "note": ""}


try:
    intakefiles.read = _permissive_read
    still_refused = client.get(
        f"/api/intakes/{intake_a.id}/files/{entry_b['id']}", headers=headers
    )
    ok(
        "with storage's own binding simulated away, the route's own manifest "
        "check (_owned_attachment) still refuses a file id that is not this "
        "intake's - a second, independent layer, not merely along for the ride",
        still_refused.status_code == 404,
    )

    foreign_still_refused = client.get(
        f"/api/intakes/{intake_a.id}/files/{foreign_ws_entry['id']}", headers=headers
    )
    ok(
        "the same is true for the *other workspace's* file id: with storage's "
        "workspace-scoped prefix simulated away, _owned_attachment is what "
        "refuses it - the assertion earlier in this file passes either way, so "
        "this is the one that names the layer",
        foreign_still_refused.status_code == 404,
    )

    main_module._owned_attachment = _always_owned
    both_defeated = client.get(
        f"/api/intakes/{intake_a.id}/files/{entry_b['id']}", headers=headers
    )
    ok(
        "mutation: with the route's own check disabled as well, on top of "
        "storage's binding already being simulated away, the wrong intake's "
        "file is served - proving `_owned_attachment` was doing real work "
        "above, not that it is the only thing preventing this in production "
        "(intakefiles.read's own structural binding, proven in "
        "check_intakefiles.py, is the primary gate and was never actually "
        "removed there)",
        both_defeated.status_code == 200 and both_defeated.content == FILE_B_BYTES,
    )
finally:
    intakefiles.read = _real_read
    main_module._owned_attachment = _real_owned

ok(
    "restored: the same cross-intake fetch is refused again, on the real "
    "functions rather than the mutated ones",
    client.get(f"/api/intakes/{intake_a.id}/files/{entry_b['id']}", headers=headers).status_code
    == 404,
)

# --- A closed intake's manifest still names its files, and every one is gone -
#
# `close()` deletes the objects but leaves `Intake.attachments` populated -
# per plan, the record is the history of what was sent - so this route has to
# answer 404 for a file its own intake still lists, not raise and not serve a
# stale copy.
closed_with_files = intakes_module.close(intake_a.id, "studio@neptune.ph")
ok(
    "closing an intake does not touch its manifest",
    len(closed_with_files.attachments) == 1
    and closed_with_files.attachments[0]["id"] == entry_a["id"],
)
ok(
    "but the file itself is gone - close() deleted the object, and this route "
    "404s for a file its own manifest still names",
    client.get(f"/api/intakes/{intake_a.id}/files/{entry_a['id']}", headers=headers).status_code
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
send_bundle_a = _real_bundle()
send_bundle_b = _real_bundle()
send_target = _quoted([send_bundle_a, send_bundle_b])

missing_bundle = client.post(f"/api/intakes/{send_target.id}/send", headers=headers, json={})
ok("sending with no bundle_id is refused: 400", missing_bundle.status_code == 400)

wrong_bundle = client.post(
    f"/api/intakes/{send_target.id}/send",
    headers=headers,
    json={"bundle_id": "cccccccccccc"},  # not a member of bundle_ids at all
)
ok(
    "a bundle_id that was not quoted for this request is refused: 400",
    wrong_bundle.status_code == 400,
)
ok(
    "...and the intake was not moved by the refused attempt",
    intakes_module.get(send_target.id).state == intakes_module.QUOTED,
)

# A bundle_id that *is* a member of bundle_ids but whose bundle was never
# actually saved (the same shape `DELETE /api/proposals/{id}` leaves behind,
# since it does not prune `bundle_ids`) must be refused too - membership
# alone is not sufficient, only necessary.
dangling_target = _quoted(["dddddddddddd"])
dangling_send = client.post(
    f"/api/intakes/{dangling_target.id}/send",
    headers=headers,
    json={"bundle_id": "dddddddddddd"},
)
ok(
    "a bundle_id that is a member of bundle_ids but was never actually saved is refused too: 400",
    dangling_send.status_code == 400,
)
ok(
    "...and the dangling intake was not moved either",
    intakes_module.get(dangling_target.id).state == intakes_module.QUOTED,
)

sent = client.post(
    f"/api/intakes/{send_target.id}/send",
    headers=headers,
    json={"bundle_id": send_bundle_b},
)
ok("sending a bundle that was actually quoted and still exists answers 200", sent.status_code == 200)
sent_body = sent.json()
ok("the state moves quoted -> sent", sent_body["state"] == "sent")
ok("sent_bundle_id names the one actually sent, not bundle_ids[0]", sent_body["sent_bundle_id"] == send_bundle_b)
ok("sent_at is stamped - this is the gap Task 2 left open", bool(sent_body.get("sent_at")))

# Joined end to end: the date /send stamped is the same date the client's
# own door actually shows - not merely present on the studio's own response.
# `send_target.token` is read straight off the module-level object (a plain
# Python attribute, not a wire dump), since Task 6's own create/relink routes
# are not involved in building this particular fixture.
client_view_after_send = client.get(f"/api/client/{send_target.token}")
ok(
    "the client's own door shows the same sent_at /send just stamped",
    client_view_after_send.status_code == 200
    and client_view_after_send.json().get("sent_at") == sent_body["sent_at"],
)

ok(
    "sending an already-sent request is refused: 409, not a silent re-send",
    client.post(
        f"/api/intakes/{send_target.id}/send",
        headers=headers,
        json={"bundle_id": send_bundle_a},
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

# Re-asserted rather than assumed: every TestClient call between here and
# `send_target`'s own `workspaces.use(made.id)` above carried its own
# `X-Workspace` header and re-points the ambient context on the way through
# `_gate` - `_quoted()` itself has no workspace argument and relies entirely
# on this being set correctly first.
workspaces.use(made.id)
mutation_target = _quoted(["111111111111"])  # its own fixture, distinct from dangling_target's
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

# And a member can read a client's own attached file too - no admin check on
# `read_intake_file` either, by the same reasoning as the queue itself.
attachment_target = client.post(
    "/api/intakes", headers=admin_headers, json={"preset": {}}
).json()
workspaces.use(secured.id)
attachment_entry = intakefiles.save(
    attachment_target["id"], "brief.pdf", b"%PDF-1.4 secured fixture\n", "application/pdf"
)
intakes_module.advance(
    attachment_target["id"], intakes_module.SUBMITTED, attachments=[attachment_entry]
)
ok(
    "a member can read a client's attached file: 200",
    client.get(
        f"/api/intakes/{attachment_target['id']}/files/{attachment_entry['id']}",
        headers=member_headers,
    ).status_code
    == 200,
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

# --- GET .../link: the same secret, read rather than replaced ---------------
#
# The route exists because `relink` is not a way to COPY a link - it is a way
# to replace one, and once the client is holding the old link, replacing it to
# get a copy breaks theirs. Admin-only all the same: reading the credential
# that opens an unauthenticated route is no less sensitive than reissuing it,
# and arguably needs the gate more, since a reissue at least leaves evidence
# when the old link stops working.
ok(
    "a member cannot read a client's link: 403",
    client.get(f"/api/intakes/{relink_target['id']}/link", headers=member_headers).status_code
    == 403,
)
admin_link = client.get(f"/api/intakes/{relink_target['id']}/link", headers=admin_headers)
ok("an admin can read the same one: 200", admin_link.status_code == 200)
ok("...and it is a real link, not an empty string", bool(admin_link.json().get("link")))
ok(
    "the link it reads is the CURRENT one - the same string the last relink "
    "handed back, not a new token minted by the act of looking",
    admin_link.json()["link"] == admin_relinked.json()["link"],
)
ok(
    "and reading it twice changes nothing - this is the non-destructive door",
    client.get(f"/api/intakes/{relink_target['id']}/link", headers=admin_headers).json()["link"]
    == admin_link.json()["link"],
)
ok(
    "the link actually works, which is the only test of it that matters",
    client.get(f"/api/client/{_token_from_link(admin_link.json()['link'])}").status_code == 200,
)
ok(
    "an id that names nothing is 404, not an empty link",
    client.get("/api/intakes/{}/link".format("0" * 12), headers=admin_headers).status_code == 404,
)

#: A closed intake has no link to hand back - `close()` blanks the token on the
#: record - and the route must say so rather than answer `{"link": ""}`, which
#: is a string a studio can paste into an email.
link_closed = client.post("/api/intakes", headers=admin_headers, json={"preset": {}}).json()
client.post(f"/api/intakes/{link_closed['id']}/close", headers=admin_headers)
closed_link = client.get(f"/api/intakes/{link_closed['id']}/link", headers=admin_headers)
ok("a closed request has no link to read: 404", closed_link.status_code == 404)
ok(
    "...and says why, so nobody goes looking for a link that was ended on purpose",
    "closing" in closed_link.json()["detail"].lower(),
)

#: The property this route was most likely to break. The queue itself still
#: carries no links: putting the token in the list is a different posture from
#: a single deliberate call per request, and it is the one this route was
#: designed NOT to take.
queue_after = client.get("/api/intakes", headers=admin_headers).json()
ok(
    "the queue still carries no token and no link, even for an admin who can "
    "read one on request - a screenshot of this list still discloses nothing",
    all("token" not in row and "link" not in row for row in queue_after),
)
one_after = client.get(f"/api/intakes/{relink_target['id']}", headers=admin_headers).json()
ok(
    "nor does reading one intake by id",
    "token" not in one_after and "link" not in one_after,
)

workspaces.use(secured.id)
secured_bundle = _real_bundle()
secured_quoted = _quoted([secured_bundle])

ok(
    "a member cannot send a quotation: 403",
    client.post(
        f"/api/intakes/{secured_quoted.id}/send",
        headers=member_headers,
        json={"bundle_id": secured_bundle},
    ).status_code
    == 403,
)
admin_sent = client.post(
    f"/api/intakes/{secured_quoted.id}/send",
    headers=admin_headers,
    json={"bundle_id": secured_bundle},
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
    "no Authorization header: reading its attached file is 401 too - `_gate` "
    "refuses before the route's own checks ever run",
    client.get(
        f"/api/intakes/{attachment_target['id']}/files/{attachment_entry['id']}",
        headers=no_token_headers,
    ).status_code
    == 401,
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
        json={"bundle_id": secured_bundle},
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
