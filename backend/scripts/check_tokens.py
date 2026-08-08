"""The client link token: mint it, index it, and resolve it back without
scanning every intake on the install.

Runs offline against a scratch `generated/` directory:

    cd backend
    .venv/Scripts/python.exe scripts/check_tokens.py

Exit code 0 means a token resolves to the right (workspace, intake) pair from
any current workspace, an unknown or expired one resolves to nothing, and
doing so costs a dict lookup and one file read rather than a walk of every
intake on disk.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-tokens-")
os.environ["DATABASE_URL"] = ""
# Off, because every check in this project runs OFFLINE. The brief check is a
# real Gemini call on the generation path; left on, these scripts would reach
# the network, cost money, and fail on a machine with no key. `app/main.py`
# reads the flag at call time, so this line is the whole of the opt-out.
os.environ["CHECK_BRIEF_IS_REAL"] = "0"
# The `clientview` section below drives a real pass through `/api/proposals`
# via `TestClient` to get a genuine `ProposalBundle` - exactly how
# `check_intake_gate.py` builds one. Shared config reads these three once at
# import time via `load_dotenv(..., override=False)`, so a real backend/.env
# (this repo's has a Supabase project configured) would otherwise win and turn
# on token verification, 401-ing every request below before a single route
# ran. Set before the first `app.*` import - `workspaces` already imports the
# shared config, so that import is what triggers the dotenv read.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.features.intakes.application import client_view as clientview  # noqa: E402
from app.features.intakes.application import service as intakes  # noqa: E402
from app.features.intakes.infrastructure import tokens  # noqa: E402
from app.features.quotations.infrastructure import repository as storage  # noqa: E402
from app.features.quotations.presentation import routes as quotation_routes  # noqa: E402
from app.features.workspaces.application import settings  # noqa: E402
from app.features.workspaces.infrastructure import repository as workspaces  # noqa: E402
from app.shared.infrastructure import database  # noqa: E402
from app.features.quotations.domain.models import (  # noqa: E402
    ClientNarrative,
    CostSummary,
    Estimate,
    LineItem,
    PaymentMilestone,
    TierSibling,
    UnitKind,
)

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def make(workspace_id: str, label: str) -> intakes.Intake:
    """A fresh intake in a named workspace, for a fixture that only cares
    about its token."""
    workspaces.use(workspace_id)
    return intakes.create(
        client_email=f"{label}@client.com",
        client_phone="",
        scope=f"Fixture: {label}.",
        budget_text="",
        preset={},
        created_by="riku@neptune.ph",
    )


workspaces.ensure_ready()

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# --- mint() --------------------------------------------------------------

minted = [tokens.mint() for _ in range(20)]
ok("mint() returns a distinct value each call", len(set(minted)) == len(minted))
ok("every minted token is at least 32 characters", all(len(t) >= 32 for t in minted))
ok("every minted token is URL-safe", all(TOKEN_PATTERN.match(t) for t in minted))

# --- resolves from a different current workspace --------------------------
#
# This is the whole point of the index: a token minted under one workspace
# has to resolve correctly while some *other* workspace is the current one,
# because that is exactly the situation an unauthenticated route is in - it
# has no workspace of its own until the token tells it which one.

home = workspaces.create("Neptune Labs")
elsewhere = workspaces.create("Somewhere Else")

entry = make(home.id, "buyer")
ok("a freshly created intake carries a token", bool(entry.token))
ok("and an expiry for that token", bool(entry.token_expires_at))
ok("and starts issued", entry.state == intakes.ISSUED)

workspaces.use(elsewhere.id)
ok(
    "a token resolves to its own workspace and intake, from a different current one",
    tokens.resolve(entry.token) == (home.id, entry.id),
)

# --- unknown and empty tokens ----------------------------------------------

ok("an unknown token resolves to nothing", tokens.resolve("not-a-real-token") is None)
ok("an empty token resolves to nothing", tokens.resolve("") is None)

# --- an expired token resolves to nothing ----------------------------------
#
# Expiry is planted straight onto the stored record, past the public API -
# there is no route yet that lets time pass - to prove `resolve` checks the
# intake's own `token_expires_at` at read time rather than trusting whatever
# state the index was built in.

expiring = make(home.id, "later")
expired_token = expiring.token
stale = intakes.get(expiring.id)
stale.token_expires_at = "2000-01-01T00:00:00Z"
intakes._write(stale)  # noqa: SLF001 - planting an expired stamp past the public API

workspaces.use(elsewhere.id)
ok(
    "a token whose intake has expired resolves to nothing",
    tokens.resolve(expired_token) is None,
)

# --- relink issues a different token, and the old one stops resolving -----

workspaces.use(home.id)
before_relink = intakes.get(entry.id).token
relinked = intakes.relink(entry.id)
ok("relink issues a different token", relinked.token != before_relink)

workspaces.use(elsewhere.id)
ok(
    "the new token resolves to the same workspace and intake",
    tokens.resolve(relinked.token) == (home.id, entry.id),
)
ok("the old token stops resolving", tokens.resolve(before_relink) is None)

# --- close() forgets its own token - "withdrawn" has to mean something ----
#
# `resolve` only ever checks expiry against the intake's own record, never
# its state - a closed request's token would otherwise go on resolving
# forever, which is not what "withdrawn" is supposed to mean to a route with
# no other gate in front of it.

closable = make(elsewhere.id, "closable")
closable_token = closable.token
intakes.close(closable.id, "riku@neptune.ph")
ok(
    "closing an intake removes its token from the index directly",
    closable_token not in tokens._index,  # noqa: SLF001 - asserting the index directly
)
ok("and it stops resolving", tokens.resolve(closable_token) is None)

# --- deleting a workspace makes its tokens stop resolving ------------------

doomed = make(home.id, "doomed")
doomed_token = doomed.token
workspaces.delete(home.id)

# Checked before any `resolve()` call touches the index, and deliberately in
# this order: `resolve` itself now evicts a token once it can confirm the
# intake behind it is gone (rather than merely unreadable), and
# `workspaces.delete` has already `rmtree`d the folder - so a `resolve()`
# call made first would evict it through that fallback and this assertion
# would pass even with `forget_workspace` stubbed to a no-op. Reading the
# index directly, before `resolve` gets a chance to touch it, is what
# actually proves `forget_workspace` did the work.
ok(
    "deleting a workspace removes its tokens from the index directly",
    doomed_token not in tokens._index,  # noqa: SLF001 - asserting the index directly
)

workspaces.use(elsewhere.id)
ok(
    "deleting a workspace makes its tokens stop resolving",
    tokens.resolve(doomed_token) is None,
)

# --- resolution is a dict lookup, not a directory scan ---------------------
#
# 50 intakes across 2 workspaces, all written to disk first. Then one resolve
# outside the timed loop - the call that may trigger the one-time lazy walk
# of every workspace, if nothing has missed the index yet in this process -
# so the timed loop below measures the steady-state cost of a lookup, not
# the walk. A per-call directory scan would not survive 200 of these under
# half a second; a dict lookup plus one file read does it easily.
#
# The fixture resolved is the *last* one created, not the first: `intakes
# .listing()` returns newest first, so if resolution were secretly a linear
# scan over it rather than a dict lookup, the first-created intake would be
# the *last* one such a scan reaches - worst case, and therefore the one a
# scan would be caught by - while the last-created intake would be found
# almost immediately, making a real scan masquerade as a fast lookup by
# accident of which fixture happened to be chosen. Picking the last-created
# one removes that luck: whichever way a scan might iterate, this fixture is
# not a favourable position to be found at.

perf_a = workspaces.create("Perf Bucket A")
perf_b = workspaces.create("Perf Bucket B")

target = None
for index in range(50):
    target = make(perf_a.id if index % 2 == 0 else perf_b.id, f"perf-{index}")

# index 49 (the last) is odd, so `target` was created in perf_b - resolved
# here from perf_a, keeping the cross-workspace shape every check above uses.
workspaces.use(perf_a.id)
primed = tokens.resolve(target.token)
ok("the fixture used for timing actually resolves", primed == (perf_b.id, target.id))

started = time.perf_counter()
for _ in range(200):
    tokens.resolve(target.token)
elapsed = time.perf_counter() - started
ok(f"200 resolves of a known token take under 0.5s (took {elapsed:.3f}s)", elapsed < 0.5)

# --- comparison is constant-time -------------------------------------------
#
# Crude, and honest: this asserts the property is implemented, not timed -
# the symbol `members.find_invite` and `members.accept` are also checked
# against. Comments are stripped first: `tokens.py` explains the choice in a
# comment that itself contains the string `secrets.compare_digest`, which
# would let this pass even if the real call were deleted. Matching the exact
# call shape - `compare_digest(entry.token` - rather than the bare module
# name closes the same gap from the other side: `import secrets` alone would
# otherwise satisfy a looser check too.

source_without_comments = re.sub(r"#.*", "", inspect.getsource(tokens))
ok(
    "resolve() compares tokens with secrets.compare_digest, not ==",
    "compare_digest(entry.token" in source_without_comments,
)

# --- a transient read failure must not permanently kill a live link -------
#
# `intakes.get()` answers `None` both when an intake is genuinely gone and
# when its file exists but could not be read just now - this repo lives on a
# OneDrive-synced path, where a momentary sharing violation on one JSON read
# is not hypothetical. `resolve` must only evict the first case: evicting on
# a transient failure would be permanent, since `_built` never triggers a
# second walk once it has run. Simulated by stubbing `intakes.get` itself
# rather than deleting anything, so the file backing the fixture is never
# touched - exactly a "could not read it this time" failure, not a "gone".

flaky_home = workspaces.create("Flaky Reads")
flaky = make(flaky_home.id, "flaky")
flaky_token = flaky.token

workspaces.use(elsewhere.id)
real_get = intakes.get
intakes.get = lambda intake_id: None
try:
    ok(
        "a transient read failure resolves to nothing, just this once",
        tokens.resolve(flaky_token) is None,
    )
finally:
    intakes.get = real_get

ok(
    "but does not evict the token - the failure was not confirmed absence",
    flaky_token in tokens._index,  # noqa: SLF001 - asserting the index directly
)
ok(
    "so the same token resolves again once the read succeeds",
    tokens.resolve(flaky_token) == (flaky_home.id, flaky.id),
)

# --- a lazy walk that fails must not raise, and must not retry itself -----
#
# The walk can die partway through - `workspaces.root()`'s own `mkdir`
# hitting a permissions error, reached via `intakes.listing()`, say. Task 3
# hangs an unauthenticated route off `resolve`, which has to answer "not
# found" for an unknown token either way, never a 500 with a stack trace -
# and a walk that failed once must not become a walk retried on every later
# miss too, which would be a full scan per call again, the exact cost this
# module exists to avoid. Forced by breaking `workspaces.listing` itself,
# the function the walk actually calls, after resetting the index to make
# the walk run again as if this were a fresh process.

tokens._built = False  # noqa: SLF001 - forcing the lazy walk to run again
tokens._index.clear()  # noqa: SLF001

real_listing = workspaces.listing
walk_attempts = {"n": 0}


def _boom_listing():
    walk_attempts["n"] += 1
    raise RuntimeError("synthetic failure, mid-walk")


workspaces.listing = _boom_listing
try:
    ok(
        "a lazy walk that fails does not raise out of resolve()",
        tokens.resolve("anything") is None,
    )
    ok(
        "a second miss right after does not raise either",
        tokens.resolve("anything-else") is None,
    )
finally:
    workspaces.listing = real_listing

ok(
    "the failed walk was attempted at most once, not on every later miss",
    walk_attempts["n"] == 1,
)
ok("and _built is set regardless, so a working walk is not retried either", tokens._built)

# --- a boot-time walk that fails must not poison the index forever ---------
#
# `build_index()` is what `main.py`'s startup event calls, once, before the
# server accepts its first connection - and unlike the lazy path just above,
# a failed walk there must NOT be allowed to set `_built` and move on. The
# lazy path accepts that tradeoff because a request-triggered failure has an
# anonymous route's retry-storm to protect against; a boot-time failure has
# no request behind it at all, and the alternative is far worse: every token
# minted before that boot would answer "gone" - the same 404 an unknown
# token gets - until somebody restarts the process, since `resolve`'s lazy
# path is gated on `not _built` and would never fire again. Forced the same
# way as the lazy-walk test above - breaking `workspaces.listing` itself -
# but calling `build_index()` (what boot calls), not letting `resolve`
# trigger the walk.

boot_ws = workspaces.create("Boot Failure")
boot_fixture = make(boot_ws.id, "boot-fixture")
boot_token = boot_fixture.token

tokens._built = False  # noqa: SLF001 - simulating a fresh process, pre-boot
tokens._index.clear()  # noqa: SLF001 - nothing remembered yet, as a real boot starts


def _boom_listing_at_boot():
    raise RuntimeError("synthetic failure, mid-walk, at boot")


workspaces.listing = _boom_listing_at_boot
try:
    tokens.build_index()
finally:
    workspaces.listing = real_listing

ok(
    "a failed boot-time walk leaves _built False, unlike a failed lazy one",
    not tokens._built,  # noqa: SLF001
)
ok(
    "so the very next miss retries the walk for real and finds the token, "
    "once the underlying issue has cleared - no restart required",
    tokens.resolve(boot_token) == (boot_ws.id, boot_fixture.id),
)
ok("and _built is finally True, once a walk actually completed", tokens._built)  # noqa: SLF001

# --- close() and relink() must survive a process restart -------------------
#
# `forget_token`/`forget_workspace` only clear the *in-memory* index. A real
# process boot starts with an empty index and `_built` false, and rebuilds
# by walking every intake's own `token` field off disk - with no state
# check, by `tokens.py`'s own deliberate design. So the only thing that
# actually keeps a withdrawn token withdrawn is whether the *file* still
# carries it, not whether the index happens to remember forgetting it. The
# earlier close() test above never catches this: `_built` was already `True`
# by then (from "an unknown token resolves to nothing", several sections up),
# so the walk that would rediscover a stale token on disk never fires.
# Clearing `_index` and resetting `_built` here is exactly what a real
# restart does to these two module globals - not a proxy for it.

restart_ws = workspaces.create("Restart Proof")

closed_fixture = make(restart_ws.id, "closed-fixture")
closed_fixture_token = closed_fixture.token
intakes.close(closed_fixture.id, "riku@neptune.ph")

relinked_fixture = make(restart_ws.id, "relinked-fixture")
relinked_old_token = relinked_fixture.token
relinked_new = intakes.relink(relinked_fixture.id)

tokens._index.clear()  # noqa: SLF001 - simulating a fresh process's empty index
tokens._built = False  # noqa: SLF001 - simulating a fresh process: walk not yet run

workspaces.use(elsewhere.id)
ok(
    "a closed intake's token does not come back after a simulated restart",
    tokens.resolve(closed_fixture_token) is None,
)
ok(
    "a relinked-away token does not come back either - the contrast",
    tokens.resolve(relinked_old_token) is None,
)
ok(
    "but the current, live token from that same relink still resolves",
    tokens.resolve(relinked_new.token) == (restart_ws.id, relinked_fixture.id),
)

# --- the token must actually survive in SQL through create, advance, relink
#
# `exclude=True` makes any `.model_dump()` on an `Intake` drop `token`
# silently. `_write` and `advance` both restore it by hand today - this
# reads the raw aggregate row, past `intakes.get()`'s own deserialization, which
# is the check that would actually fail the day a third dump site is added
# without the same care: `get()` would keep reporting a token correctly even
# if the file no longer had one, because pydantic fills a missing field from
# its default (`""`) without complaint.


def _stored_token(intake_id: str) -> str:
    record = database.get(workspaces.current(), intakes.RECORD_KIND, intake_id)
    return "" if record is None else str(record.payload.get("token", ""))


durability_ws = workspaces.create("Token Durability")
durable = make(durability_ws.id, "durable")
ok("the token is in SQL right after create()", bool(_stored_token(durable.id)))

intakes.advance(durable.id, intakes.SUBMITTED)
ok("and survives a plain advance() call", bool(_stored_token(durable.id)))

durable_relinked = intakes.relink(durable.id)
ok(
    "and the new token lands in SQL after relink(), matching the return value",
    _stored_token(durable.id) == durable_relinked.token,
)

# --- advance(id, CLOSED) is the second door to the same state, and must ---
#     blank the token exactly the way close() does --------------------------
#
# Every state's entry in `ALLOWED` includes `CLOSED`, so `advance(id,
# CLOSED)` is just as legal a way to withdraw a request as calling
# `close()` directly - and before `_write` centralised the blanking,
# `advance` unconditionally restored the token it had just read (needed for
# every *other* transition), leaving the file - and so a rebuilt index -
# with a live link for a request nothing else marks as withdrawn.

advance_closed_ws = workspaces.create("Advance Closes Too")
advance_closed = make(advance_closed_ws.id, "advance-closed")
advance_closed_token = advance_closed.token
intakes.advance(advance_closed.id, intakes.CLOSED)

ok(
    "advance(id, CLOSED) blanks the token in SQL, same as close()",
    _stored_token(advance_closed.id) == "",
)

tokens._index.clear()  # noqa: SLF001 - simulating a fresh process's empty index
tokens._built = False  # noqa: SLF001 - simulating a fresh process: walk not yet run

workspaces.use(elsewhere.id)
ok(
    "and it does not resolve after a simulated restart either",
    tokens.resolve(advance_closed_token) is None,
)

# --- relink refuses a closed intake outright, rather than degrading -------
#
# Without the refusal, relink would mint a token, write it, and `_write`
# would blank it right back the instant it saw `state == CLOSED` - a silent
# no-op that hands back an `Intake` whose `.token` does not match what is
# actually on disk. An explicit `IntakeError` instead.

relink_closed_ws = workspaces.create("Relink Refuses Closed")
relink_closed = make(relink_closed_ws.id, "relink-closed")
intakes.close(relink_closed.id, "riku@neptune.ph")

try:
    intakes.relink(relink_closed.id)
    ok("relink refuses a closed intake", False)
except intakes.IntakeError:
    ok("relink refuses a closed intake", True)

# ============================================================================
# clientview.of() - the security boundary: what a client is allowed to see
# ============================================================================
#
# The quotation-face fixture is a real `ProposalBundle`, built the way
# `check_intake_gate.py` builds one: a genuine pass through `/api/proposals`,
# Gemini stubbed, everything after it - the rate card, the payment terms, the
# costing, the two rendered documents - running for real. `clientview.of()` is
# then exercised directly off the result, the same way Task 3's anonymous
# route eventually will.

cv_ws = workspaces.create("Clientview Fixture Studio")
workspaces.use(cv_ws.id)
settings.save(settings.load().model_copy(update={"studio_name": "Neptune Labs"}))

api = TestClient(app=main.app)
cv_headers = {"X-Workspace": cv_ws.id}

CV_STUB_ESTIMATE = Estimate(
    project_name="Clientview Fixture",
    currency="PHP",
    client=ClientNarrative(
        title="Clientview Fixture",
        executive_summary="A fixture proposal, built only to exercise the leak test.",
        validity_days=45,
    ),
    line_items=[
        LineItem(
            id="LI-01",
            category="Backend",
            description="Build the booking flow",
            role="Senior Backend Engineer",
            quantity=10,
            unit=UnitKind.hour,
            unit_rate=1000.0,
            subtotal=10000.0,
        ),
    ],
    cost=CostSummary(
        subtotal=10000.0,
        total=10000.0,
        payment_milestones=[
            PaymentMilestone(label="Deposit", percent=50.0, amount=5000.0, trigger="Signing"),
            PaymentMilestone(label="Delivery", percent=50.0, amount=5000.0, trigger="Delivery"),
        ],
    ),
)


async def _cv_stub_estimate(*args, **kwargs):
    return CV_STUB_ESTIMATE


cv_intake = intakes.create(
    client_email="waiting@client.com",
    client_phone="0917-000-0000",
    scope="x" * 137,
    budget_text="BUDGETLEAK-777k",
    preset={},
    created_by="riku@neptune.ph",
)
# See the same hop in `check_intake_gate.py`'s fixtures: Stage 2's
# `intakes.create` now starts every intake `issued`, and nothing stands
# between that and `submitted` until Task 4 builds the client's own submit
# route - so this stands in for it by hand.
intakes.advance(cv_intake.id, intakes.SUBMITTED)

cv_real_generate_estimate = quotation_routes.generate_estimate
quotation_routes.generate_estimate = _cv_stub_estimate
try:
    cv_response = api.post(
        "/api/proposals",
        headers=cv_headers,
        data={"brief": "Clientview fixture - a booking site for a two-branch clinic.", "intake_id": cv_intake.id},
    )
    ok("clientview fixture: the pad answers 202", cv_response.status_code == 202)

    cv_deadline = time.time() + 20.0
    while time.time() < cv_deadline and intakes.get(cv_intake.id).state != intakes.QUOTED:
        time.sleep(0.25)
finally:
    # Nothing in this file runs `/api/proposals` after this section today, so
    # leaving the stub in place would currently be harmless - but the module
    # global is shared process-wide, and a script that monkeypatches it
    # without restoring is exactly the kind of thing that turns into a
    # confusing failure the day something is appended after it.
    quotation_routes.generate_estimate = cv_real_generate_estimate

cv_quoted = intakes.get(cv_intake.id)
ok("clientview fixture: the intake reaches quoted", cv_quoted.state == intakes.QUOTED)

cv_bundle_id = cv_quoted.bundle_ids[0] if cv_quoted.bundle_ids else ""
cv_bundle = storage.get(cv_bundle_id)
ok("clientview fixture: a real bundle exists", cv_bundle is not None)
if cv_bundle is None:
    print()
    print("clientview fixture never quoted - cannot exercise clientview.of at all.")
    print(f"{len(FAILURES)} FAILED")
    sys.exit(1)

# Poisoned past what the real pipeline actually produced: these fields are
# populated here by hand with sentinel strings no legitimate value would ever
# contain, so the sweep below can catch a nested leak by *value* as well as by
# field name. The real pipeline leaves most of these empty for a fixture this
# small (no rate card configured, no target, a single tier) - an empty field
# proves nothing about whether `clientview.of` would have let a *populated*
# one through.
#
# These four sentinels can only ever appear in the *non-narrative* part of the
# view - `rate_card_removed`, `target_note`, `tier_cap_note` and
# `tier_siblings` are `ProposalBundle` fields, and `render_client_proposal`
# (app/renderers/markdown.py), the only thing that ever wrote `files[0]
# .markdown`, takes an `Estimate` and never sees a `ProposalBundle` at all -
# so sweeping them against the narrative would be a check that always passes
# and proves nothing. The narrative's own leak path is different and is
# exercised separately, right below: not "does a bundle-level secret reach
# it" (it structurally cannot) but "does `clientview.of` forward the stored
# file's actual bytes, rather than silently reconstructing something that
# merely looks the same".
CV_SENTINELS = ["RATECARDLEAK", "TARGETNOTELEAK", "TIERCAPLEAK", "TIERSIBLINGLEAK"]

# Planted straight into the stored "proposal" file's markdown, diverging it
# from whatever a fresh `render_client_proposal(cv_bundle.estimate)` would
# produce. Without this, `narrative == storage.markdown_for(cv_poisoned,
# "proposal")` would pass even if `clientview.of` re-rendered the estimate
# itself instead of reading the stored file - for an untouched bundle the two
# happen to produce identical bytes, so the identity check alone would not
# have caught that bug. With the stored file poisoned, the two diverge, and
# the check below only passes if `clientview.of` genuinely forwarded what was
# actually on disk.
CV_NARRATIVE_FLOWTHROUGH_MARKER = "NARRATIVEFLOWTHROUGH-marker-not-a-fresh-render"
cv_poisoned_files = [
    generated.model_copy(update={"markdown": generated.markdown + f"\n\n{CV_NARRATIVE_FLOWTHROUGH_MARKER}"})
    if generated.kind == "proposal"
    else generated
    for generated in cv_bundle.files
]
cv_poisoned = cv_bundle.model_copy(
    update={
        "files": cv_poisoned_files,
        "rate_card_bound": 4,
        "rate_card_removed": ["RATECARDLEAK-role-not-covered"],
        "rate_card_removed_value": 4321.0,
        "target_note": "TARGETNOTELEAK - could not land on the requested figure",
        "tier_cap_note": "TIERCAPLEAK - the cap could not be applied exactly",
        "tier_siblings": [
            TierSibling(
                id="sibling-id",
                tier_name="TIERSIBLINGLEAK-tier",
                tier_index=1,
                total=1.0,
                currency="PHP",
            ),
        ],
    }
)

# --- issued: the studio's name and nothing else - the form is unsubmitted ---

cv_issued = intakes.Intake(
    id="0" * 12,
    state=intakes.ISSUED,
    created_at="2026-01-01T00:00:00Z",
    created_by="riku@neptune.ph",
    client_email="unopened@client.com",
)
cv_issued_view = clientview.of(cv_issued)
ok("issued: exactly state and studio_name", set(cv_issued_view) == {"state", "studio_name"})
ok("issued: state is carried through", cv_issued_view["state"] == intakes.ISSUED)
ok("issued: studio name is carried through", cv_issued_view["studio_name"] == "Neptune Labs")

# --- the forbidden-substring sweep, defined here so both the waiting face
#     and the quotation face below can use it -------------------------------

CV_FORBIDDEN = [
    "line_items",
    "rate_card_bound",
    "rate_card_removed",
    "target_note",
    "tier_cap_note",
    "tier_siblings",
    "files",
    "id",
    "estimate",
    "priced_scope",
    "priced_budget",
    "client_email",
    "client_phone",
    "job_id",
    "bundle_ids",
    "target_total",
    "hit_target",
    "tier_cap",
    "tier_group_id",
    "revision_instruction",
    "created_by",
    "token",
]


def _cv_forbidden_present(term: str, haystack: str) -> bool:
    """A bare substring search for every forbidden name except `id`.

    `id` also occurs harmlessly inside `validity` ("val-id-ity") - the one
    legitimate key in this view that happens to spell it - so a bare search
    for `id` can never pass once `validity` is a key, regardless of what
    `clientview.of` actually does. Searched for as a JSON key instead
    (`"id":`), which is the actual shape a leaked `bundle.id` or `intake.id`
    would take, nested or not, without also flagging the word it sits inside.
    """
    if term == "id":
        return '"id":' in haystack
    return term in haystack


# --- the four waiting states: one identical face ----------------------------
#
# submitted, preparing, quoted and quote_failed must be indistinguishable to
# the client - never a hint that a model is currently running, or that a pass
# already failed once - and never the budget, only the studio's name, when the
# request came in, their own masked address, and how much they wrote.
#
# "Indistinguishable" is asserted literally: four intakes built to differ in
# every way a leak could ride on (different state, only one carrying an
# `error`) are each turned into a view, and the four views must serialise to
# exactly one distinct JSON string. A same-*shape* check (matching keys, or a
# substring sweep for words like "gemini") cannot catch `"state": "quoted"`
# leaking the raw lifecycle token through the one field such a check never
# looks at - which is exactly the bug an earlier version of this test missed,
# because its substring sweep was written against `intake.error`'s contents
# and never against `state` itself.

CV_WAITING_KEYS = {"state", "studio_name", "sent_at", "email", "scope_length", "attachments"}

#: A manifest entry in the shape `intakefiles.save()` actually returns, poisoned
#: in the two fields the waiting face must not forward. The client is shown what
#: they attached so they can tell they sent the wrong file; the id is how the
#: file is *addressed*, which is a different thing and belongs to the studio's
#: own authed route, and the note is `attachments.read`'s warning written in the
#: studio's terms about how the job will be priced.
CV_ATTACHMENT = {
    "id": "abcdef123456",
    "name": "scope of work.pdf",
    "kind": "application/pdf",
    "bytes": 20_481,
    "note": "NOTELEAK - nothing from it reached the quotation.",
}


def _reference_masked(email: str) -> str:
    """An independent re-implementation of `InviteScreen.tsx`'s `masked()`,
    so this proves `clientview.py` matches that behaviour rather than merely
    agreeing with its own private helper."""
    name, sep, domain = email.partition("@")
    if not sep:
        return email
    head = name[:1]
    dots = "•" * max(3, min(len(name) - 1, 6))
    return f"{head}{dots}@{domain}"


CV_WAITING_VIEWS = []
for cv_state in (intakes.SUBMITTED, intakes.PREPARING, intakes.QUOTED, intakes.QUOTE_FAILED):
    cv_waiting = intakes.Intake(
        id="1" * 12,
        state=cv_state,
        created_at="2026-02-03T04:05:06Z",
        created_by="riku@neptune.ph",
        client_email="waiting@client.com",
        client_phone="0917-000-0000",
        scope="x" * 137,
        budget_text="BUDGETLEAK-777k",
        error="Gemini answered with no usable JSON." if cv_state == intakes.QUOTE_FAILED else "",
        attachments=[CV_ATTACHMENT],
    )
    CV_WAITING_VIEWS.append(clientview.of(cv_waiting))

ok(
    "the four waiting states serialise to exactly one distinct payload, not four",
    len({json.dumps(view, sort_keys=True) for view in CV_WAITING_VIEWS}) == 1,
)

cv_waiting_view = CV_WAITING_VIEWS[0]
ok("waiting: exactly the waiting keys", set(cv_waiting_view) == CV_WAITING_KEYS)
ok(
    "waiting: state is the single shared label, not the studio's own lifecycle token",
    cv_waiting_view["state"] == "waiting",
)
ok("waiting: studio name is carried through", cv_waiting_view["studio_name"] == "Neptune Labs")
ok(
    "waiting: sent_at reads when the request came in, not blank",
    cv_waiting_view["sent_at"] == "2026-02-03T04:05:06Z",
)
ok(
    "waiting: email is masked the way InviteScreen.tsx masks it",
    cv_waiting_view["email"] == _reference_masked("waiting@client.com"),
)
ok("waiting: scope_length matches what they wrote", cv_waiting_view["scope_length"] == 137)
ok(
    "waiting: what they attached, by name and size and nothing else",
    cv_waiting_view["attachments"] == [{"name": "scope of work.pdf", "bytes": 20_481}],
)

cv_waiting_dumped = json.dumps(cv_waiting_view)
ok(
    "waiting: never the file id - the client is told their file arrived, not "
    "handed the thing that addresses it",
    CV_ATTACHMENT["id"] not in cv_waiting_dumped,
)
ok(
    "waiting: never the extraction note, which is written to the studio about "
    "how the job will be priced",
    "NOTELEAK" not in cv_waiting_dumped,
)
ok(
    "waiting: never the budget",
    "BUDGETLEAK" not in cv_waiting_dumped and "budget" not in cv_waiting_dumped.lower(),
)
ok(
    "waiting: never a hint that a model is running or a pass already failed",
    "gemini" not in cv_waiting_dumped.lower()
    and "error" not in cv_waiting_dumped.lower()
    and "json" not in cv_waiting_dumped.lower(),
)
ok("waiting: never the client's phone number", "0917-000-0000" not in cv_waiting_dumped)
for cv_term in CV_FORBIDDEN:
    ok(
        f"waiting: never leaks {cv_term!r}",
        not _cv_forbidden_present(cv_term, cv_waiting_dumped),
    )

# --- closed: nothing but the state ------------------------------------------

cv_closed = intakes.Intake(
    id="2" * 12,
    state=intakes.CLOSED,
    created_at="2026-01-01T00:00:00Z",
    created_by="riku@neptune.ph",
    client_email="closed@client.com",
    scope="something",
    budget_text="never shown either",
    closed_at="2026-01-02T00:00:00Z",
    closed_by="riku@neptune.ph",
)
cv_closed_view = clientview.of(cv_closed)
ok("closed: exactly state", set(cv_closed_view) == {"state"})
ok("closed: state is closed", cv_closed_view["state"] == intakes.CLOSED)

# --- sent, revision_requested, finalized: the quotation ---------------------

CV_QUOTED_KEYS = {
    "state",
    "studio_name",
    "reference",
    "total",
    "currency",
    "validity",
    "payment_schedule",
    "narrative",
    "sent_at",
    "revisions",
    "can_revise",
    "can_finalize",
}

cv_sent = intakes.Intake(
    id="3" * 12,
    state=intakes.SENT,
    created_at="2026-01-01T00:00:00Z",
    created_by="riku@neptune.ph",
    client_email="quotation@client.com",
    scope="whatever they originally wrote",
    budget_text="BUDGETLEAK-777k",
    bundle_ids=[cv_bundle_id],
    sent_bundle_id=cv_bundle_id,
    sent_at="2026-03-04T05:06:07Z",
    revisions=[],
)
cv_revreq = cv_sent.model_copy(
    update={
        "state": intakes.REVISION_REQUESTED,
        "revisions": [{"asked": "Please drop the SMS module.", "at": "2026-03-05T00:00:00Z"}],
    }
)
cv_finalized = cv_sent.model_copy(
    update={
        "state": intakes.FINALIZED,
        "revisions": [{"asked": "Please drop the SMS module.", "at": "2026-03-05T00:00:00Z"}],
    }
)

cv_sent_view = clientview.of(cv_sent, cv_poisoned)
cv_revreq_view = clientview.of(cv_revreq, cv_poisoned)
cv_finalized_view = clientview.of(cv_finalized, cv_poisoned)

for cv_label, cv_view, cv_state_const in (
    ("sent", cv_sent_view, intakes.SENT),
    ("revision_requested", cv_revreq_view, intakes.REVISION_REQUESTED),
    ("finalized", cv_finalized_view, intakes.FINALIZED),
):
    ok(f"{cv_label}: exactly the quotation keys", set(cv_view) == CV_QUOTED_KEYS)
    ok(f"{cv_label}: state is carried through", cv_view["state"] == cv_state_const)
    ok(f"{cv_label}: studio name is carried through", cv_view["studio_name"] == "Neptune Labs")
    ok(f"{cv_label}: reference is the estimate's own", cv_view["reference"] == cv_bundle.estimate.quotation_ref)
    ok(f"{cv_label}: total matches the costed estimate", cv_view["total"] == cv_bundle.estimate.cost.total)
    ok(f"{cv_label}: currency matches", cv_view["currency"] == cv_bundle.estimate.currency)
    ok(f"{cv_label}: validity is the narrative's validity_days", cv_view["validity"] == 45)
    ok(
        f"{cv_label}: both payment milestones carried, named fields only",
        len(cv_view["payment_schedule"]) == 2
        and all(set(row) == {"label", "percent", "amount", "trigger"} for row in cv_view["payment_schedule"]),
    )
    ok(
        f"{cv_label}: narrative is exactly the client markdown already on the bundle",
        cv_view["narrative"] == storage.markdown_for(cv_poisoned, "proposal"),
    )
    # Proves the identity check above is exercised, not vacuously true: the
    # stored file was poisoned with a marker no fresh render of the estimate
    # would produce, so this only passes if `clientview.of` genuinely read
    # `files[0].markdown` off the bundle rather than reconstructing something
    # that happens to look the same for an untouched fixture.
    ok(
        f"{cv_label}: narrative genuinely forwards the stored file's bytes, not a fresh re-render",
        CV_NARRATIVE_FLOWTHROUGH_MARKER in cv_view["narrative"],
    )
    ok(
        f"{cv_label}: sent_at is the intake's own timestamp, not the bundle's created_at",
        cv_view["sent_at"] == "2026-03-04T05:06:07Z" and cv_view["sent_at"] != cv_poisoned.created_at,
    )

    # The forbidden-substring sweep runs on the view with `narrative` excluded:
    # `narrative` is real client-facing markdown prose, and a bare search for
    # `id` or `files` inside prose collides with ordinary English ("provide",
    # "identifies", "profiles") - a false failure that would either be ignored
    # or, worse, "fixed" by weakening the forbidden list. `narrative` is
    # instead checked above for exact identity with the trusted markdown
    # already on the bundle, which is the actual guarantee this module can
    # make about it - it forwards that string, it does not rebuild it.
    cv_without_narrative = json.dumps({key: value for key, value in cv_view.items() if key != "narrative"})
    for cv_term in CV_FORBIDDEN:
        ok(
            f"{cv_label}: never leaks {cv_term!r} outside the narrative",
            not _cv_forbidden_present(cv_term, cv_without_narrative),
        )
    # The sentinel sweep also runs on the view *without* `narrative`: these
    # four sentinels live only in `ProposalBundle` fields that
    # `render_client_proposal` (app/renderers/markdown.py) never reads - its
    # signature takes an `Estimate`, not a `ProposalBundle` - so they cannot
    # reach the narrative regardless of what `clientview.of` does, and
    # sweeping them against it would prove nothing. The narrative's own
    # forwarding is exercised above, directly, with a marker planted in the
    # stored file itself.
    for cv_sentinel in CV_SENTINELS:
        ok(f"{cv_label}: never leaks the sentinel {cv_sentinel!r}", cv_sentinel not in cv_without_narrative)

ok(
    "can_revise and can_finalize are true only in sent",
    cv_sent_view["can_revise"] is True and cv_sent_view["can_finalize"] is True,
)
ok(
    "revision_requested carries can_revise/can_finalize false",
    cv_revreq_view["can_revise"] is False and cv_revreq_view["can_finalize"] is False,
)
ok(
    "finalized carries can_revise/can_finalize false",
    cv_finalized_view["can_revise"] is False and cv_finalized_view["can_finalize"] is False,
)
ok(
    "revision_requested's revisions carry asked/at pairs, nothing else",
    cv_revreq_view["revisions"] == [{"asked": "Please drop the SMS module.", "at": "2026-03-05T00:00:00Z"}],
)

try:
    clientview.of(cv_sent, None)
    ok("a quotation-face state with no bundle raises rather than inventing one", False)
except ValueError:
    ok("a quotation-face state with no bundle raises rather than inventing one", True)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
