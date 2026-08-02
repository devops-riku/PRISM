"""The three stamps: preparing on entry, quoted on finish, quote_failed on error.

Gemini is stubbed. The point is the seam - that the real handler, run end to
end, moves the intake - so the stub replaces only the model call and nothing
else. A previous session shipped a TypeError that isolated tests missed for
exactly this reason.

`run()` inside `create_proposal` has four ways to reach `jobs.fail(...)` -
`GeminiConfigError`, `GeminiResponseError`, `HTTPException` (raised from
`_apply_target` when a target total cannot be solved onto the estimate) and a
generic `except Exception` with a fixed message - and every one of them has to
stamp `QUOTE_FAILED` with the same string `jobs.fail` got. This exercises all
four, not just the generic one, plus `priced_budget` and a tiered pass that
produces more than one bundle.

It also checks that a `QUOTE_FAILED` stamp tells somebody: `inbox.ADMINS`
resolves against the roster at write time, so the workspace here claims an
admin before any of that runs, and the check reads that admin's own inbox
rather than trusting that the write merely didn't crash.

    cd backend
    .venv/Scripts/python.exe scripts/check_intake_gate.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intake-gate-")
# `app.config` reads these once at import time via `load_dotenv(..., override=False)`,
# so a real backend/.env (this repo's has a Supabase project configured) would
# otherwise win and turn on token verification - every request below is
# headerless, so the gate would 401 all of them before a single route ran.
# See scripts/check_kind_api.py and scripts/check_intakes_api.py for the same fix.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import inbox, intakes, jobs, main, members, workspaces  # noqa: E402
from app.gemini_service import GeminiConfigError, GeminiResponseError  # noqa: E402
from app.schemas import Estimate  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def settle(intake_id: str, want: str, seconds: float = 20.0) -> str:
    """Wait for the background job to land, then report where it got to."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = intakes.get(intake_id).state
        if state == want:
            return state
        time.sleep(0.25)
    return intakes.get(intake_id).state


def settle_job(job_id: str, want: str, seconds: float = 20.0) -> str:
    """Same wait, for the job itself rather than the intake it is stamping."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = jobs.get(job_id)
        if found is not None and found.state == want:
            return found.state
        time.sleep(0.25)
    found = jobs.get(job_id)
    return found.state if found is not None else "missing"


def settle_change(intake_id: str, before: list[str], seconds: float = 20.0):
    """Wait until `bundle_ids` differs from `before`, then return the intake.

    Used where the intake is already sitting in the state being waited for
    before the request under test is even sent - `settle(id, QUOTED)` on an
    intake that is already `quoted` would return true instantly, proving
    nothing about whether the second pass actually landed. Polling for the
    field to change is the only way to tell "still the first pass" from "the
    second pass finished" apart.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = intakes.get(intake_id)
        if found.bundle_ids != before:
            return found
        time.sleep(0.25)
    return intakes.get(intake_id)


def settle_note(kind: str, seconds: float = 5.0) -> bool:
    """Wait for a note of this kind to land, rather than reading once.

    `intakes.get(...).state` flips to `quote_failed` the moment `advance`'s
    own write returns - which is *before* `stamp`'s separate, independently
    guarded notify call runs. A single read of `inbox.listing()` taken right
    after `settle()` sees the new state can land in that gap and find nothing
    there yet, so this polls the inbox exactly the way `settle` polls the
    intake.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if any(note.kind == kind for note in inbox.listing()):
            return True
        time.sleep(0.25)
    return False


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
# `inbox.ADMINS` is resolved against the roster at write time - an empty
# roster delivers to nobody. This claims the workspace for an admin so the
# quote_failed notifications below have somewhere to land.
members.claim("riku@neptune.ph", "u1")
client = TestClient(app=main.app)
headers = {"X-Workspace": made.id}


async def stub_estimate(*args, **kwargs):
    return Estimate(project_name="Booking platform", currency="PHP")


def stub_failure(*args, **kwargs):
    raise RuntimeError("Gemini answered with no usable estimate.")


def stub_config_error(*args, **kwargs):
    raise GeminiConfigError("No Gemini API key configured for this workspace.")


def stub_response_error(*args, **kwargs):
    raise GeminiResponseError("Gemini answered with no usable JSON.", snippet="not json")


# --- The success path: PREPARING then QUOTED, with what was actually priced --

entry = intakes.create(
    client_email="buyer@client.com",
    client_phone="",
    scope="A booking site.",
    budget_text="around 300k",
    preset={},
    created_by="riku@neptune.ph",
)
# Stage 1 built this pipeline against intakes that started `submitted`.
# Stage 2's `intakes.create` now starts every intake `issued` instead - the
# client's own submit route is what performs this hop for real once it
# ships, and until `/api/intakes` stops taking client words at all, this
# fixture stands in for it. Every intake below that goes through
# `/api/proposals` needs the same hop, for the same reason.
intakes.advance(entry.id, intakes.SUBMITTED)

main.generate_estimate = stub_estimate
response = client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A booking site.", "intake_id": entry.id, "budget_hint": "around 300k"},
)
ok("the pad still answers 202", response.status_code == 202)
ok("the intake reaches quoted", settle(entry.id, intakes.QUOTED) == intakes.QUOTED)

done = intakes.get(entry.id)
ok("with the bundle recorded", len(done.bundle_ids) == 1)
ok("and what was actually priced", done.priced_scope == "A booking site.")
ok("and what they hoped to spend", done.priced_budget == "around 300k")
ok("the client's own words are untouched", done.scope == "A booking site.")

# --- A second Generate on an already-quoted intake replaces, not appends ----
#
# Reachable in the shipped UI: Price this -> pad -> Generate -> `#/q/<id>` ->
# browser Back -> the pad again, still prefilled with the same `intake_id` ->
# Generate. `QUOTED: {PREPARING, ...}` exists so this run through the real
# handler - not just `intakes.advance` in isolation - leaves the intake
# pointing at what is now on screen, not at both bundles or, worse, still the
# first one.
first_bundle_ids = list(done.bundle_ids)
main.generate_estimate = stub_estimate
second_pass = client.post(
    "/api/proposals",
    headers=headers,
    data={
        "brief": "A booking site, revised scope.",
        "intake_id": entry.id,
        "budget_hint": "around 350k",
    },
)
ok("Generate again on an already-quoted intake still answers 202", second_pass.status_code == 202)

requoted = settle_change(entry.id, first_bundle_ids)
ok("the intake reaches quoted again", requoted.state == intakes.QUOTED)
ok(
    "the second pass replaces bundle_ids rather than keeping the first",
    requoted.bundle_ids != first_bundle_ids and len(requoted.bundle_ids) == 1,
)
ok(
    "and replaces priced_scope with what is now on screen, not the first brief",
    requoted.priced_scope == "A booking site, revised scope.",
)
ok(
    "and replaces priced_budget the same way",
    requoted.priced_budget == "around 350k",
)

# --- A ladder produces more than one bundle, and all of them are recorded ----

tiered = intakes.create(
    client_email="ladder@client.com",
    client_phone="",
    scope="A booking site, three ways.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(tiered.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_estimate
client.post(
    "/api/proposals",
    headers=headers,
    data={
        "brief": "A booking site, three ways.",
        "intake_id": tiered.id,
        "tiers": "Basic, Standard",
    },
)
ok(
    "a ladder also reaches quoted",
    settle(tiered.id, intakes.QUOTED) == intakes.QUOTED,
)
ok("with every tier's bundle recorded, not just one", len(intakes.get(tiered.id).bundle_ids) == 2)

# --- Four ways to fail, each with its own message, stamped exactly ----------

# 1. The generic `except Exception` branch - the only one with a fixed
#    message rather than the exception's own, since it deliberately does not
#    repeat whatever an unexpected error said onto a client-facing job.
second = intakes.create(
    client_email="two@client.com",
    client_phone="",
    scope="A stock take.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(second.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_failure
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A stock take.", "intake_id": second.id},
)
ok(
    "a failed pass reaches quote_failed",
    settle(second.id, intakes.QUOTE_FAILED) == intakes.QUOTE_FAILED,
)
ok(
    "and stamps the same fixed message jobs.fail got, not the RuntimeError's own",
    intakes.get(second.id).error
    == "The quotation could not be prepared. The error is in the API log.",
)

# 2. GeminiConfigError - the key is missing or rejected.
config_broken = intakes.create(
    client_email="three@client.com",
    client_phone="",
    scope="A missing key.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(config_broken.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_config_error
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A missing key.", "intake_id": config_broken.id},
)
ok(
    "a config error also reaches quote_failed",
    settle(config_broken.id, intakes.QUOTE_FAILED) == intakes.QUOTE_FAILED,
)
ok(
    "with GeminiConfigError's own message",
    intakes.get(config_broken.id).error
    == "No Gemini API key configured for this workspace.",
)

# 3. GeminiResponseError - Gemini answered, but not with a usable Estimate.
response_broken = intakes.create(
    client_email="four@client.com",
    client_phone="",
    scope="An unusable answer.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(response_broken.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_response_error
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "An unusable answer.", "intake_id": response_broken.id},
)
ok(
    "an unusable response also reaches quote_failed",
    settle(response_broken.id, intakes.QUOTE_FAILED) == intakes.QUOTE_FAILED,
)
ok(
    "with GeminiResponseError's own message",
    intakes.get(response_broken.id).error == "Gemini answered with no usable JSON.",
)

# 4. HTTPException - `_apply_target` raises when `snap_to_total` cannot land
#    on the typed target at all. The stub estimate has no line items, so a
#    target total of any size is unreachable and this fires from inside
#    `_finalise`, deep in `run()`'s own try block, not from the synchronous
#    checks before the job exists.
target_broken = intakes.create(
    client_email="five@client.com",
    client_phone="",
    scope="An unreachable target.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(target_broken.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_estimate
client.post(
    "/api/proposals",
    headers=headers,
    data={
        "brief": "An unreachable target.",
        "intake_id": target_broken.id,
        "target_total": "500000",
    },
)
ok(
    "an unreachable target also reaches quote_failed",
    settle(target_broken.id, intakes.QUOTE_FAILED) == intakes.QUOTE_FAILED,
)
ok(
    "with the costing solver's own reason, not a generic one",
    intakes.get(target_broken.id).error
    == "This quotation has no priced line item to adjust, so its total cannot be moved.",
)

# --- A failed pass is announced, not just recorded --------------------------
#
# Any of the four quote_failed passes above could have raised this note -
# `settle_note` only asks whether one is there, not which pass wrote it. It
# polls rather than reading once because the intake's state flips to
# quote_failed the moment `advance`'s write returns, which is before
# `stamp`'s separate, independently guarded notify call runs.
inbox.use_identity("riku@neptune.ph", "u1")
ok("a failed pass raises a note", settle_note("intake.quote_failed"))

# --- A non-IntakeError escaping intakes.advance must not take the job with it
#
# stamp() catches Exception broadly, not just IntakeError, because
# intakes.advance reaches workspaces.root() on its way to a file on disk, and
# that raises NoWorkspace (workspaces.py:320) or a bare OSError - neither is
# an IntakeError. Forcing intakes.advance itself to raise something else
# entirely (an AttributeError; the point is only that it is not an
# IntakeError) is the same technique check_intakes_api.py uses to force a
# write failure on demand, applied to the module main.py actually calls
# through, so the patch is visible to the running handler.
_real_advance = intakes.advance


def _boom_advance(*args, **kwargs):
    raise AttributeError("synthetic non-IntakeError, to prove stamp()'s broad catch")


broken_stamp = intakes.create(
    client_email="six@client.com",
    client_phone="",
    scope="A broken stamp.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
# Advanced for real, before `intakes.advance` gets monkeypatched below - the
# assertion after this block expects the intake to still be `submitted`
# because the stamp genuinely never landed, and that is only true if it
# reached `submitted` before the patch was in place to swallow it.
intakes.advance(broken_stamp.id, intakes.SUBMITTED)
main.generate_estimate = stub_estimate
try:
    intakes.advance = _boom_advance
    stamp_broken = client.post(
        "/api/proposals",
        headers=headers,
        data={"brief": "A broken stamp.", "intake_id": broken_stamp.id},
    )
    ok(
        "a non-IntakeError escaping the stamp still answers 202",
        stamp_broken.status_code == 202,
    )
    stamp_broken_job_id = stamp_broken.json()["id"]
    ok(
        "and the job itself still reaches done, not stuck at queued forever",
        settle_job(stamp_broken_job_id, "done") == "done",
    )
finally:
    intakes.advance = _real_advance

ok(
    "the intake itself never moved - the stamp genuinely failed, it just did "
    "not take the quotation down with it",
    intakes.get(broken_stamp.id).state == intakes.SUBMITTED,
)

# --- The field is optional, and a quotation without one must behave exactly
#     as before. ----------------------------------------------------------
main.generate_estimate = stub_estimate
plain = client.post("/api/proposals", headers=headers, data={"brief": "No intake here."})
ok("a quotation with no intake_id still answers 202", plain.status_code == 202)

# An unknown intake id must not take the quotation down with it.
odd = client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "Unknown intake.", "intake_id": "0" * 12},
)
ok("an unknown intake_id does not fail the request", odd.status_code == 202)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
