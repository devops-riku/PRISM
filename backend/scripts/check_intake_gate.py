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

The section at the bottom of this file is about the other gate this file is
named for - `_gate`, the HTTP middleware - and specifically about the body cap
it puts on the client's three anonymous write routes. Its declared-length half
is covered in `check_client_api.py`, beside the rest of that door; the half
here is the one that had no cover at all, where the caller declares no length
because they sent the body chunked. See that section's own comment for why the
assertions there are driven at the ASGI layer rather than through `TestClient`.

    cd backend
    .venv/Scripts/python.exe scripts/check_intake_gate.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intake-gate-")
# Off, because every check in this project runs OFFLINE. The brief check is a
# real Gemini call on the generation path; left on, these scripts would reach
# the network, cost money, and fail on a machine with no key. `app/main.py`
# reads the flag at call time, so this line is the whole of the opt-out.
os.environ["CHECK_BRIEF_IS_REAL"] = "0"
# `app.config` reads these once at import time via `load_dotenv(..., override=False)`,
# so a real backend/.env (this repo's has a Supabase project configured) would
# otherwise win and turn on token verification - every request below is
# headerless, so the gate would 401 all of them before a single route ran.
# See scripts/check_kind_api.py and scripts/check_intakes_api.py for the same fix.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""
# And the same for Spaces, for a second reason: this file's own `.env` has live
# DigitalOcean credentials, `intakefiles.configured()` reads them live, and the
# client-files section at the bottom of this script stores and reads real bytes.
# Left set, every check below would write into the user's actual bucket and this
# script would need the network to pass. Blanked, `intakefiles` answers from the
# local backend under the scratch `GENERATED_DIR` above - which is what
# `configured() == False` is for. Same four lines as check_intakefiles.py and
# check_client_upload.py.
os.environ["DO_SPACES_ACCESS_KEY"] = ""
os.environ["DO_SPACES_SECRET_KEY"] = ""
os.environ["DO_SPACES_REGION"] = ""
os.environ["DO_SPACES_BUCKET"] = ""
os.environ["DO_SPACES_ENDPOINT"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import config, inbox, intakefiles, intakes, jobs, main, members, workspaces  # noqa: E402
from app.gemini_service import GeminiConfigError, GeminiResponseError  # noqa: E402
from app.schemas import BriefCheck  # noqa: E402
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


#: Every call `stub_estimate` receives, so a case can assert that pricing did
#: NOT happen. "The intake reached quote_failed" alone cannot tell a refusal
#: that ran before the tier loop from one that ran after it and threw away the
#: result - and the whole point of checking the brief first is that a refused
#: brief costs nothing.
PRICED_CALLS: list[tuple] = []


async def stub_estimate(*args, **kwargs):
    PRICED_CALLS.append((args, kwargs))
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
    scope="A booking site for a two-branch dental clinic in Makati.",
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
    data={"brief": "A booking site for a two-branch dental clinic in Makati.", "intake_id": entry.id, "budget_hint": "around 300k"},
)
ok("the pad still answers 202", response.status_code == 202)
ok("the intake reaches quoted", settle(entry.id, intakes.QUOTED) == intakes.QUOTED)

done = intakes.get(entry.id)
ok("with the bundle recorded", len(done.bundle_ids) == 1)
ok("and what was actually priced", done.priced_scope == "A booking site for a two-branch dental clinic in Makati.")
ok("and what they hoped to spend", done.priced_budget == "around 300k")
ok("the client's own words are untouched", done.scope == "A booking site for a two-branch dental clinic in Makati.")

# --- A second Generate on an already-quoted intake replaces, not appends ----
#
# Reachable in the shipped UI: Create PAD -> pad -> Generate -> `#/q/<id>` ->
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
        "brief": "A booking site for a two-branch dental clinic, revised scope.",
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
    requoted.priced_scope == "A booking site for a two-branch dental clinic, revised scope.",
)
ok(
    "and replaces priced_budget the same way",
    requoted.priced_budget == "around 350k",
)

# --- A ladder produces more than one bundle, and all of them are recorded ----

tiered = intakes.create(
    client_email="ladder@client.com",
    client_phone="",
    scope="A booking site for a two-branch dental clinic, priced three ways.",
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
        "brief": "A booking site for a two-branch dental clinic, priced three ways.",
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
    scope="A stock take across two warehouses, counted and reconciled.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(second.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_failure
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A stock take across two warehouses, counted and reconciled.", "intake_id": second.id},
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

# 1b. The brief check - the model says the text is not a brief at all.
#
# Patched, never called for real: this file is offline and the check is a live
# Gemini call. What is proved here is the WIRING - that a refusal stops the run
# before anything is priced, that it reaches the studio as its own sentence
# rather than the generic failure, and that the flag switches the whole thing
# out. Whether the model's judgement is any good is a different question and
# not one a check script can answer.
import app.config as config_module  # noqa: E402
import app.gemini_service as gemini_module  # noqa: E402

#: The genuine article, captured before any stub replaces it - the fail-open
#: case below needs the REAL function, because what it is testing is that
#: function's own error handling.
REAL_BRIEF_CHECK = main.check_brief_is_real


async def stub_refuses(_text):
    return BriefCheck(is_brief=False, reason="That does not read as a description of work.")


async def stub_accepts(_text):
    return BriefCheck()


not_a_brief = intakes.create(
    client_email="", client_phone="",
    scope="A stock take across two warehouses, counted and reconciled.",
    budget_text="", preset={}, created_by="riku@neptune.ph",
)
intakes.advance(not_a_brief.id, intakes.SUBMITTED)
main.generate_estimate = stub_estimate
main.check_brief_is_real = stub_refuses
config_module.CHECK_BRIEF_IS_REAL = True
priced_before = len(PRICED_CALLS)
client.post(
    "/api/proposals",
    headers=headers,
    data={
        "brief": "A stock take across two warehouses, counted and reconciled.",
        "intake_id": not_a_brief.id,
    },
)
ok(
    "a brief the model says is not a brief reaches quote_failed",
    settle(not_a_brief.id, intakes.QUOTE_FAILED) == intakes.QUOTE_FAILED,
)
ok(
    "with the model's own sentence, not the generic failure line - the studio "
    "has to be able to tell 'retype this' from 'try again in a minute'",
    intakes.get(not_a_brief.id).error == "That does not read as a description of work.",
)
ok(
    "and nothing was priced: the refusal runs before the tier loop, so a "
    "refused brief costs no model calls and no Spaces round trips",
    len(PRICED_CALLS) == priced_before,
)

# The flag really is the whole opt-out - with it off the same refusing stub is
# never consulted and the same brief prices normally.
flag_off = intakes.create(
    client_email="", client_phone="",
    scope="A stock take across two warehouses, counted and reconciled.",
    budget_text="", preset={}, created_by="riku@neptune.ph",
)
intakes.advance(flag_off.id, intakes.SUBMITTED)
config_module.CHECK_BRIEF_IS_REAL = False
client.post(
    "/api/proposals",
    headers=headers,
    data={
        "brief": "A stock take across two warehouses, counted and reconciled.",
        "intake_id": flag_off.id,
    },
)
ok(
    "with CHECK_BRIEF_IS_REAL off, the same brief prices normally - the flag "
    "is what keeps every offline check offline",
    settle(flag_off.id, intakes.QUOTED) == intakes.QUOTED,
)
# And when the check itself falls over, the brief prices anyway. This is the
# one place in this codebase where failing OPEN is correct, and it is worth an
# assertion rather than a comment: a quality gate that goes down must not take
# the studio's ability to quote with it. `check_brief_is_real` swallows its own
# errors, so this stub raising is the closest a test can get to the real thing
# - and if that swallowing is ever removed, this case is what says so.
check_broke = intakes.create(
    client_email="", client_phone="",
    scope="A stock take across two warehouses, counted and reconciled.",
    budget_text="", preset={}, created_by="riku@neptune.ph",
)
intakes.advance(check_broke.id, intakes.SUBMITTED)
# The flag back ON first. The case above left it off, and without this line the
# check never runs at all - the assertion below then passes because nothing was
# checked, which is the shape of a test that proves nothing. Caught by printing
# the state rather than trusting the green.
config_module.CHECK_BRIEF_IS_REAL = True
# The REAL function, with the thing it calls broken underneath it. Replacing
# the function with one that raises would have proved nothing - it bypasses the
# very error handling being tested, and it did: that version failed here, which
# is how the mistake was caught. `_get_client` is what `check_brief_is_real`
# reaches for first, so a client that cannot be built is the closest offline
# stand-in for the API being unreachable.
main.check_brief_is_real = REAL_BRIEF_CHECK
_real_get_client = gemini_module._get_client  # noqa: SLF001


def _broken_client():
    raise RuntimeError("no client today")


gemini_module._get_client = _broken_client  # noqa: SLF001
client.post(
    "/api/proposals",
    headers=headers,
    data={
        "brief": "A stock take across two warehouses, counted and reconciled.",
        "intake_id": check_broke.id,
    },
)
ok(
    "a brief check that raises does NOT stop the quotation - the gate fails "
    "open on purpose, because a false refusal blocks all quoting while a false "
    "accept is one quotation somebody deletes",
    settle(check_broke.id, intakes.QUOTED) == intakes.QUOTED,
)

gemini_module._get_client = _real_get_client  # noqa: SLF001
config_module.CHECK_BRIEF_IS_REAL = True
main.check_brief_is_real = stub_accepts

# 2. GeminiConfigError - the key is missing or rejected.
config_broken = intakes.create(
    client_email="three@client.com",
    client_phone="",
    scope="A missing key, and a booking site that still needs pricing.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(config_broken.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_config_error
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A missing key, and a booking site that still needs pricing.", "intake_id": config_broken.id},
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
    scope="An unusable answer, on a booking site brief that is otherwise fine.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(response_broken.id, intakes.SUBMITTED)  # see the comment above `entry`
main.generate_estimate = stub_response_error
client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "An unusable answer, on a booking site brief that is otherwise fine.", "intake_id": response_broken.id},
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
    scope="An unreachable target on a booking site for two clinics.",
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
        "brief": "An unreachable target on a booking site for two clinics.",
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
    scope="A broken stamp, on a booking site brief that is otherwise fine.",
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
        data={"brief": "A broken stamp, on a booking site brief that is otherwise fine.", "intake_id": broken_stamp.id},
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
plain = client.post("/api/proposals", headers=headers, data={"brief": "No intake here, just a booking site for two dental clinics."})
ok("a quotation with no intake_id still answers 202", plain.status_code == 202)

# An unknown intake id must not take the quotation down with it.
odd = client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "Unknown intake, on a booking site brief that is otherwise fine.", "intake_id": "0" * 12},
)
ok("an unknown intake_id does not fail the request", odd.status_code == 202)

# =============================================================================
# The client's own files reach the model
# =============================================================================
#
# `POST /api/proposals` reads the files a client attached through their own
# link off the intake's manifest and merges them with whatever the pad itself
# uploaded, so that pricing a client's request never means the studio
# re-uploading the client's files.
#
# Everything below drives the real handler and asserts on **what the model was
# actually handed** - the images list and the `documents_text` keyword
# `generate_estimate` receives - rather than on an internal call being made.
# The stub is the only thing replaced.
#
# THREE WORKSPACES, and that is the point of them. `workspaces.current()` falls
# back to `default_id()` when the context is unset rather than raising, so a
# workspace that failed to carry into the thread this fetch runs in would read
# the *first workspace on file*, silently, and a single-workspace check could
# not tell that apart from working. So: the intake and its files live in
# `gamma`, `alpha` (created first, and therefore the default) holds neither,
# and `beta` is a third workspace that asks for the same intake by id.

#: A real 1x1 PNG rather than arbitrary bytes: this is the value asserted to
#: have arrived at the model unchanged, and a file that is what it says it is
#: keeps the assertion honest if anything downstream ever starts looking.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

#: Distinctive enough that finding it in the prompt cannot be a coincidence.
CLIENT_MARKER = "AURORA-CLIENT-SCOPE-MARKER"
CLIENT_SCOPE = f"{CLIENT_MARKER}\nFourteen treatment rooms, two floors, one reception."
STUDIO_MARKER = "AURORA-STUDIO-NOTE-MARKER"

alpha = made  # the first workspace created, and so `default_id()`
gamma = workspaces.create("Gamma Studio")
beta = workspaces.create("Beta Studio")

SEEN: dict = {}
NOTES: list = []
_real_notify = inbox.notify


async def stub_capture(*args, **kwargs):
    """Record what `create_proposal` handed the model, then answer as before.

    Positional, because that is how `prepare()` calls it: the request first,
    the images second. `documents_text` is the keyword carrying
    `attachments.describe_for_prompt`'s block.
    """
    SEEN["images"] = list(args[1])
    SEEN["documents_text"] = kwargs.get("documents_text", "")
    return Estimate(project_name="Booking platform", currency="PHP")


def stub_notify(kind, person, payload):
    """Every notification this pass raised, so the reporting half can be read.

    `inbox.notify` is where a quotation that is ready *and* has something worth
    saying says it - `create_proposal` builds a `caveats` list and puts the
    first of them in the body. Captured rather than read back out of
    `inbox.listing()` because the notify runs inside the background task, under
    whichever workspace and identity that task carried, and the assertion here
    is about the sentence, not about where it was filed.
    """
    NOTES.append((kind, payload))
    return 0


def stock_client_files(workspace_id: str, entries: list) -> str:
    """One submitted intake in `workspace_id`, carrying `entries` as real files.

    `entries` are `(name, bytes, content type)`. Stored through
    `intakefiles.save` - the same call `/submit` makes - so the manifest is the
    real shape rather than a hand-written dict, and the bytes are genuinely on
    the other end of it.
    """
    borrowed = workspaces.borrow(workspace_id)
    try:
        made_intake = intakes.create(
            client_email="scope@client.com",
            client_phone="",
            scope="A clinic fit-out.",
            budget_text="",
            preset={},
            created_by="riku@neptune.ph",
        )
        manifest = [
            intakefiles.save(made_intake.id, name, data, kind)
            for name, data, kind in entries
        ]
        intakes.advance(made_intake.id, intakes.SUBMITTED, attachments=manifest)
        return made_intake.id
    finally:
        workspaces.give_back(borrowed)


#: A second client, **entered as a context manager**, and that is load-bearing
#: rather than tidy. `TestClient` used bare opens a fresh blocking portal - and
#: so a fresh event loop - per request and tears it down when the response
#: comes back. Every background job above survives that only because its every
#: `await` resolves without yielding (the estimate stub is a coroutine that
#: returns immediately), so `run()` completes inside the single step the task is
#: first scheduled in. `create_proposal` now has a real suspension point in that
#: task - `await asyncio.to_thread(...)` for the client's files - and a task
#: suspended there when the loop is torn down never resumes: the job sits at
#: "Reading the client's files" for ever and every assertion below reads an
#: empty capture. Entered, one loop runs in its own thread for the whole
#: section, and the task finishes as it does under uvicorn. This is a fact
#: about the harness, not about the handler.
files_client = TestClient(app=main.app)
files_client.__enter__()


def price(
    workspace_id: str,
    intake_id: str,
    # Past `_normalise_brief`'s floor. These cases are about which FILES
    # reach the model, not about the brief, so the default only has to be
    # long enough to stop being the subject.
    brief: str = "Price the client's request from what they attached.",
    files=None,
):
    """One Generate through the real handler, waited out, with the capture reset."""
    SEEN.clear()
    NOTES.clear()
    main.generate_estimate = stub_capture
    inbox.notify = stub_notify
    try:
        payload = {"brief": brief}
        if intake_id:
            payload["intake_id"] = intake_id
        answer = files_client.post(
            "/api/proposals",
            headers={"X-Workspace": workspace_id},
            data=payload,
            files=files,
        )
        if answer.status_code == 202:
            # Waited out under a borrow of the workspace the request named:
            # `jobs` is per workspace (`jobs._by_workspace`), and read from this
            # thread with nothing set, `jobs.get` would look in `alpha` - the
            # default - find nothing, and return instantly without waiting for
            # anything at all.
            job_id = answer.json()["id"]
            deadline = time.time() + 20.0
            while time.time() < deadline:
                borrowed_for_job = workspaces.borrow(workspace_id)
                try:
                    found = jobs.get(job_id)
                finally:
                    workspaces.give_back(borrowed_for_job)
                if found is not None and found.state in ("done", "failed"):
                    break
                time.sleep(0.25)
    finally:
        inbox.notify = _real_notify
    return answer


def note_bodies() -> str:
    return " ".join(str(payload.get("body", "")) for _kind, payload in NOTES)


# --- The positive: the client's own files reach the model --------------------

both_kinds = stock_client_files(
    gamma.id,
    [
        ("site-photo.png", PNG_1X1, "image/png"),
        ("scope.txt", CLIENT_SCOPE.encode("utf-8"), "text/plain"),
    ],
)
answer = price(gamma.id, both_kinds)
ok("a Generate against an intake with files still answers 202", answer.status_code == 202)
ok(
    "the client's own document reaches the model as text",
    CLIENT_MARKER in SEEN.get("documents_text", ""),
)
ok(
    "and under the client's own filename, not the stored 12-hex one",
    "scope.txt" in SEEN.get("documents_text", ""),
)
ok(
    "the client's own image reaches it as image bytes, with the manifest's type",
    (PNG_1X1, "image/png") in SEEN.get("images", []),
)
ok("and nothing was reported as missing", "no longer stored" not in note_bodies())

# THE GATE ON THAT POSITIVE. If the workspace had failed to carry into the
# thread the fetch runs in, `workspaces.current()` would have answered `alpha`
# - the default - and the read would have found nothing there. These two
# assertions are what make the four above mean "the thread saw gamma" rather
# than "some workspace had the file": the file is readable from gamma and from
# nowhere else.
gamma_manifest = None
borrowed = workspaces.borrow(gamma.id)
try:
    gamma_manifest = list(intakes.get(both_kinds).attachments)
    ok(
        "the file is readable from gamma, where it was stored",
        intakefiles.read(both_kinds, gamma_manifest[0]["id"]) is not None,
    )
finally:
    workspaces.give_back(borrowed)

for label, elsewhere in (("the default workspace", alpha.id), ("a third workspace", beta.id)):
    borrowed = workspaces.borrow(elsewhere)
    try:
        ok(
            f"and from {label} it is not readable at all - so the read above can "
            "only have run under gamma",
            intakefiles.read(both_kinds, gamma_manifest[0]["id"]) is None,
        )
    finally:
        workspaces.give_back(borrowed)

# --- Merge, do not replace ---------------------------------------------------
#
# A studio pricing a client's request while attaching its own reference
# material keeps both sets, in both lanes.
answer = price(
    gamma.id,
    both_kinds,
    files=[
        ("images", ("studio-shot.png", PNG_1X1, "image/png")),
        ("documents", ("studio-note.txt", STUDIO_MARKER.encode("utf-8"), "text/plain")),
    ],
)
ok("a Generate carrying the studio's own files too answers 202", answer.status_code == 202)
ok(
    "both documents reach the model - merged, not replaced",
    CLIENT_MARKER in SEEN.get("documents_text", "")
    and STUDIO_MARKER in SEEN.get("documents_text", ""),
)
ok("and both images do", len(SEEN.get("images", [])) == 2)
ok(
    "with the client's read first, which is what the shared character budget "
    "is spent on first",
    # `0 <=` on purpose: `find` answers -1 for a marker that is not there at
    # all, which would make a bare `<` true for exactly the case this whole
    # section exists to catch - the client's document never having arrived.
    0 <= SEEN.get("documents_text", "").find(CLIENT_MARKER)
    < SEEN.get("documents_text", "").find(STUDIO_MARKER),
)

# --- The negative: another workspace cannot reach them through `intake_id` ---
#
# `intake_id` arrives on a `Form` field, so it is caller-controlled: an id
# belonging to another workspace must read as an id belonging to nobody.
answer = price(beta.id, both_kinds)
ok("a Generate naming a foreign intake still answers 202", answer.status_code == 202)
ok(
    "and not one byte of that intake's files reaches the model",
    not SEEN.get("images", []) and CLIENT_MARKER not in SEEN.get("documents_text", ""),
)
borrowed = workspaces.borrow(beta.id)
try:
    ok(
        "the layer that refused it is `intakes.get`'s own workspace-scoped path "
        "- from beta the record does not exist at all",
        intakes.get(both_kinds) is None,
    )
finally:
    workspaces.give_back(borrowed)

# THE MUTATION, and it earns its keep twice. Asserting the negative above
# proves the behaviour and not the layer: it would pass just as well if the
# manifest had been found and the bytes then refused, or if nothing had been
# looked up at all. So the first layer is removed - beta is given a real intake
# record with the *same id and the same manifest*, copied on disk, exactly as
# though `intakes.get` were not workspace-scoped - and the same request is run
# again. Storage's own workspace-scoped prefix has to refuse it on its own.
#
# It is also the honest version of Step 3: a record that names files storage
# does not have is precisely what a closed intake is, and what must be reported
# rather than raised.
gamma_record = workspaces.dir_for(gamma.id) / intakes.DIRNAME / f"{both_kinds}.json"
beta_records = workspaces.dir_for(beta.id) / intakes.DIRNAME
beta_records.mkdir(parents=True, exist_ok=True)
shutil.copyfile(gamma_record, beta_records / f"{both_kinds}.json")

borrowed = workspaces.borrow(beta.id)
try:
    ok(
        "with the record copied across, beta's own `intakes.get` now finds the "
        "manifest - the first layer is genuinely gone",
        intakes.get(both_kinds) is not None,
    )
finally:
    workspaces.give_back(borrowed)

answer = price(beta.id, both_kinds)
ok("the request with the copied record still answers 202", answer.status_code == 202)
ok(
    "and storage refuses it on its own - still not one byte reaches the model",
    not SEEN.get("images", []) and CLIENT_MARKER not in SEEN.get("documents_text", ""),
)
ok(
    "a file the record names and storage does not have is reported, not raised - "
    "the quotation still finished",
    answer.status_code == 202 and SEEN.get("documents_text", "") == "",
)
ok(
    "and whoever pressed Generate is told, by name",
    "scope.txt is no longer stored with this request" in note_bodies(),
)

# --- The caps are the studio's, and the overflow names its cause -------------
#
# The combined set is bounded by MAX_IMAGES / MAX_DOCUMENTS - the studio's own
# numbers - and never by MAX_CLIENT_FILES, which is a door policy for a
# stranger. Which set gives way turns on whether the person reading the refusal
# can act on it.
full_house = stock_client_files(
    gamma.id,
    [
        (f"scope-{index}.txt", f"{CLIENT_MARKER} part {index}".encode("utf-8"), "text/plain")
        for index in range(config.MAX_DOCUMENTS)
    ],
)
answer = price(gamma.id, full_house)
ok(
    f"{config.MAX_DOCUMENTS} client documents and no studio ones is fine",
    answer.status_code == 202,
)

refused = price(
    gamma.id,
    full_house,
    files=[("documents", ("studio-note.txt", STUDIO_MARKER.encode("utf-8"), "text/plain"))],
)
ok(
    "the studio's own document on top of a full house is refused - it is the one "
    "thing the person reading the message can remove",
    refused.status_code == 400,
)
ok(
    "and the message names the cause rather than saying 'too many documents'",
    str(config.MAX_DOCUMENTS) in refused.json()["detail"]
    and "from the client" in refused.json()["detail"]
    and "Remove 1" in refused.json()["detail"],
)

# The other side of that decision: a client's files that overflow on their own
# are truncated and reported, never refused. Nobody can remove them - `/submit`
# runs once from `issued` and there is no client-side deletion after it - so a
# 400 here would be a permanent dead end on a legitimate enquiry. It is
# reachable today rather than theoretical: MAX_CLIENT_FILES is 6 and
# MAX_DOCUMENTS is 5.
overflowing = stock_client_files(
    gamma.id,
    [
        (f"extra-{index}.txt", f"{CLIENT_MARKER} extra {index}".encode("utf-8"), "text/plain")
        for index in range(config.MAX_DOCUMENTS + 1)
    ],
)
answer = price(gamma.id, overflowing)
ok(
    "a client who sent more documents than the studio reads is not a refusal",
    answer.status_code == 202,
)
ok(
    f"exactly {config.MAX_DOCUMENTS} of them reach the model",
    SEEN.get("documents_text", "").count("--- BEGIN ") == config.MAX_DOCUMENTS,
)
ok(
    "and the one that did not is named to whoever pressed Generate",
    f"extra-{config.MAX_DOCUMENTS}.txt did not reach the quotation" in note_bodies(),
)

# --- A manifest that is not the shape it should be ---------------------------
#
# `Intake.attachments` is a bare `List[dict]` in `ADVANCE_FIELDS`; `advance()`
# enforces "a list, of dicts" and nothing at all about the five keys. An entry
# claiming a content type this app does not store must not decide which lane a
# file takes, and one addressing nothing must not reach storage.
borrowed = workspaces.borrow(gamma.id)
try:
    malformed = intakes.create(
        client_email="odd@client.com",
        client_phone="",
        scope="A malformed manifest.",
        budget_text="",
        preset={},
        created_by="riku@neptune.ph",
    )
    stored = intakefiles.save(
        malformed.id, "scope.txt", CLIENT_SCOPE.encode("utf-8"), "text/plain"
    )
    intakes.advance(
        malformed.id,
        intakes.SUBMITTED,
        attachments=[
            dict(stored, kind="text/html"),
            {"name": "nothing.png", "kind": "image/png", "bytes": 1, "note": ""},
        ],
    )
finally:
    workspaces.give_back(borrowed)

answer = price(gamma.id, malformed.id)
ok("a malformed manifest does not fail the request", answer.status_code == 202)
ok(
    "an unrecognised content type takes the document lane and is read by its "
    "suffix, not served as whatever the dict claimed",
    CLIENT_MARKER in SEEN.get("documents_text", "") and not SEEN.get("images", []),
)

# --- A storage read that comes apart costs that file, not the quotation ------
#
# `_load_client_files` runs inside `run()`, whose own `except Exception` fails
# the job and stamps quote_failed. Without its per-record guard, one manifest
# entry that made storage raise would lose the whole quotation - which is the
# opposite of what "reported, not raised" means. `intakefiles.read` catches
# broadly on both backends today, but its local branch reaches
# `workspaces.root()`, which raises `NoWorkspace`, and nothing enforces that
# contract from `main.py`. Forced here, the same technique this file already
# uses on `intakes.advance` above.
_real_intakefiles_read = intakefiles.read


def _boom_read(intake_id, file_id):
    raise RuntimeError("synthetic storage failure, to prove the per-record guard")


try:
    intakefiles.read = _boom_read
    answer = price(gamma.id, both_kinds)
finally:
    intakefiles.read = _real_intakefiles_read

ok("a storage read that raises still answers 202", answer.status_code == 202)
ok(
    "and the quotation was still generated - the model was reached past the "
    "failed read, rather than the job dying on it",
    "images" in SEEN,
)
ok(
    "with nothing of that file in the prompt",
    not SEEN.get("images", []) and SEEN.get("documents_text", "") == "",
)
ok(
    "and both files named to whoever pressed Generate",
    "site-photo.png could not be read from storage" in note_bodies()
    and "scope.txt could not be read from storage" in note_bodies(),
)

main.generate_estimate = stub_estimate
# The section below drives raw ASGI rather than this client, and nothing after
# it needs a background task to finish, so the long-lived loop is given back
# here rather than left running to the end of the process.
files_client.__exit__(None, None, None)

# =============================================================================
# `_gate`'s body cap, on the half of it that a `Content-Length` header does not
# describe
# =============================================================================
#
# `_gate` refuses an oversized *declared* body before a byte of it is read, and
# `check_client_api.py` proves that. What it could not prove, and what this
# section exists for, is the case where there is nothing to declare: a caller
# sending `Transfer-Encoding: chunked` omits `Content-Length` entirely, the
# declared-length clause becomes a no-op, `_gate` falls through, and Starlette
# buffers and parses the whole body before the handler runs. It needs no valid
# token to do it - `tokens.resolve` is never reached - so a bogus one works,
# and the eventual 404 is paid for after the damage.
#
# Driven at the ASGI layer rather than through `TestClient`, because the claim
# is about *when* the refusal happens and `TestClient` cannot express it:
# httpx's `Request.read()` joins an iterator body into one `bytes` before the
# transport ever sees it (Starlette's own generator branch in
# `testclient.py` is marked `pragma: no cover` for exactly that reason), so
# every `TestClient` request arrives as a single `http.request` message no
# matter how it was written. Calling `main.app(scope, receive, send)` directly
# is the only way in this repo to hand a body over in pieces and count how many
# of them were actually taken. Both instruments are used below - the ASGI one
# for the byte count, `TestClient` for the ordinary surface a real client hits.

_HOST_HEADER = (b"host", b"testserver")


def drive(
    path: str,
    *,
    client_ip: str,
    headers: list[tuple[bytes, bytes]],
    body_bytes: int = 0,
    payload: bytes | None = None,
    chunk_bytes: int = 64 * 1024,
) -> tuple[int, int]:
    """One POST straight at the ASGI app. Returns `(status, bytes_handed_over)`.

    `receive` yields the body a chunk at a time and counts what it gave away,
    so "was this refused before the body was read" is a number rather than an
    argument. Nothing here fabricates a response: the status is whatever the
    real app - every middleware, `_gate` included - actually sent.

    `body_bytes` invents filler of that size, which is all an oversized body
    needs to be. `payload` sends exact bytes instead, for the cases that have to
    reach a handler and be understood by it rather than merely be large.
    """
    if payload is not None:
        body_bytes = len(payload)
    handed = {"count": 0}
    answered: dict[str, object] = {}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [_HOST_HEADER] + headers,
        "client": (client_ip, 443),
        "server": ("testserver", 80),
        "state": {},
    }

    async def receive() -> dict:
        if handed["count"] >= body_bytes:
            return {"type": "http.request", "body": b"", "more_body": False}
        end = min(handed["count"] + chunk_bytes, body_bytes)
        piece = payload[handed["count"] : end] if payload is not None else b"x" * (end - handed["count"])
        handed["count"] = end
        return {"type": "http.request", "body": piece, "more_body": handed["count"] < body_bytes}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            answered["status"] = message["status"]

    asyncio.run(main.app(scope, receive, send))
    return int(answered.get("status", 0)), handed["count"]


JSON_HEADERS = [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")]
JSON_CAP = main._MAX_CLIENT_BODY_BYTES  # noqa: SLF001
BOGUS = "/api/client/not-a-real-token-at-all/submit"

# Twenty times the cap, offered a chunk at a time. A gate that only reads
# `Content-Length` takes every byte of it.
chunked_status, chunked_handed = drive(
    BOGUS, client_ip="203.0.113.71", body_bytes=JSON_CAP * 20, headers=JSON_HEADERS
)
ok(
    "a chunked body past the cap is refused with 413, not answered after it has "
    "already been buffered and parsed",
    chunked_status == 413,
)
ok(
    "and refused before the body was fully read - the caller was stopped near the "
    f"cap, not at the end of what it offered ({chunked_handed:,} of "
    f"{JSON_CAP * 20:,} bytes taken)",
    chunked_handed < JSON_CAP * 20,
)
ok(
    "and stopped within one chunk of the cap, not merely somewhere short of the end",
    chunked_handed <= JSON_CAP + 64 * 1024,
)

# The cheap path, measured the same way: a declared oversized length is refused
# with the body untouched. `check_client_api.py` asserts the 413; this asserts
# the "before a byte of it is read" half of the same claim.
declared_status, declared_handed = drive(
    BOGUS,
    client_ip="203.0.113.72",
    body_bytes=JSON_CAP * 20,
    headers=[(b"content-type", b"application/json"), (b"content-length", str(JSON_CAP * 20).encode())],
)
ok(
    "a declared oversized length is still refused with 413 - the cheap path is "
    "unchanged",
    declared_status == 413,
)
ok(
    "and not one byte of that body was ever handed over",
    declared_handed == 0,
)

# A chunked body comfortably inside the cap is not swept up by any of this. Real
# JSON rather than filler, and handed over in sixteen-byte pieces: this has to
# reach the handler and be *understood* there, which is the same consume-and-
# replay claim the end-to-end section below makes, asserted here at the layer
# where the pieces are visible.
under_payload = json.dumps({"client_email": "x@y.com", "scope": "fine", "budget_text": "fine"}).encode()
under_status, under_handed = drive(
    BOGUS, client_ip="203.0.113.73", payload=under_payload, chunk_bytes=16, headers=JSON_HEADERS
)
ok(
    "a chunked body inside the cap is judged on its merits - a bogus token gets "
    "this door's usual 404, not a 413 and not a 422 for a body nobody could read",
    under_status == 404,
)
ok("and all of it was read, because all of it was allowed", under_handed == len(under_payload))

# --- The larger cap, and only for multipart ---------------------------------
#
# A JSON submit and a file upload cannot share one number: the four text fields
# are bounded by `_MAX_CLIENT_BODY_BYTES` and a document is three orders of
# magnitude past that. The route is still JSON-only today - Task 3 is what makes
# `/submit` multipart - so the assertion a multipart body inside the larger cap
# can carry is "not refused by the *cap*", which is exactly the claim being
# made. What it gets instead is whatever the JSON-only handler makes of it.

ok(
    "config names a total-upload cap of its own, separate from the studio's",
    hasattr(config, "MAX_CLIENT_UPLOAD_TOTAL_BYTES"),
)

if hasattr(config, "MAX_CLIENT_UPLOAD_TOTAL_BYTES"):
    MULTIPART_HEADERS = [
        (b"content-type", b"multipart/form-data; boundary=----PRISMcheckboundary"),
        (b"transfer-encoding", b"chunked"),
    ]
    UPLOAD_CAP = config.MAX_CLIENT_UPLOAD_TOTAL_BYTES

    ok(
        "and it is meaningfully larger than the JSON cap - a document does not fit "
        "in the room four text fields need",
        UPLOAD_CAP > JSON_CAP * 10,
    )

    big_multipart_status, _ = drive(
        BOGUS,
        client_ip="203.0.113.74",
        body_bytes=JSON_CAP * 4,
        headers=MULTIPART_HEADERS,
    )
    ok(
        "a multipart body several times the JSON cap is not refused by the cap - "
        "the limit is chosen from the content type, not applied blind",
        big_multipart_status != 413,
    )

    over_multipart_status, over_multipart_handed = drive(
        BOGUS,
        client_ip="203.0.113.75",
        body_bytes=UPLOAD_CAP + JSON_CAP + 2 * 1024 * 1024,
        headers=MULTIPART_HEADERS,
    )
    ok(
        "but a multipart body past the larger cap is refused too - the larger cap "
        "is a cap, not an exemption",
        over_multipart_status == 413,
    )
    ok(
        "and that one was also stopped early rather than buffered whole",
        over_multipart_handed < UPLOAD_CAP + JSON_CAP + 2 * 1024 * 1024,
    )

    # A JSON body of the same size gets the smaller number - proof the two are
    # actually distinguished rather than the larger one quietly winning for
    # everybody.
    json_at_multipart_size_status, _ = drive(
        BOGUS, client_ip="203.0.113.76", body_bytes=JSON_CAP * 4, headers=JSON_HEADERS
    )
    ok(
        "the same body declared as JSON is refused - the wider allowance belongs to "
        "multipart alone",
        json_at_multipart_size_status == 413,
    )

    # And to `/submit` alone. `/revise` takes a sentence and `/finalize` takes
    # nothing at all; neither will ever carry a file, so declaring a multipart
    # content type on one of them must not buy a hundredfold more room than the
    # route could possibly use.
    for other_route in ("revise", "finalize"):
        other_status, _ = drive(
            f"/api/client/not-a-real-token-at-all/{other_route}",
            client_ip=f"203.0.113.{77 + len(other_route)}",
            body_bytes=JSON_CAP * 4,
            headers=MULTIPART_HEADERS,
        )
        ok(
            f"a multipart body past the JSON cap on /{other_route} is still refused - "
            "the wider allowance is the upload route's, not the content type's",
            other_status == 413,
        )

# --- The regression that matters: a real submit still works ------------------
#
# Refusing an undeclared oversized body means engaging with the request stream,
# and whatever `_gate` consumes has to reach the handler afterwards. This is
# the thing most likely to break silently, so it is proven end to end and both
# ways: with a `Content-Length` (the ordinary browser case) and without one (a
# chunked body small enough to be legal, which is the path that actually has to
# be consumed and replayed).
#
# Form-encoded rather than JSON since Stage 2 Task 3, which made `/submit` take
# `Form` fields so it could carry files. The property under test is unchanged
# and so is the shape of the test: a body with a declared length and a body
# without one, both of which have to arrive at the handler word for word. The
# encoding is only what the route parses.

workspaces.use(made.id)
declared_submit = intakes.create(
    client_email="",
    client_phone="",
    scope="",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
declared_response = client.post(
    f"/api/client/{declared_submit.token}/submit",
    data={
        "client_email": "buyer@client.com",
        "client_phone": "+63 917 000 0000",
        "scope": "An ordinary submission, with a length the client declared.",
        "budget_text": "around 300k",
    },
)
ok("an ordinary submit with a declared length still answers 200", declared_response.status_code == 200)
declared_stored = intakes.get(declared_submit.id)
ok(
    "and every word of it reached disk intact - the body survived the gate",
    declared_stored.state == intakes.SUBMITTED
    and declared_stored.client_email == "buyer@client.com"
    and declared_stored.client_phone == "+63 917 000 0000"
    and declared_stored.scope == "An ordinary submission, with a length the client declared."
    and declared_stored.budget_text == "around 300k",
)

workspaces.use(made.id)
chunked_submit = intakes.create(
    client_email="",
    client_phone="",
    scope="",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)


def _in_pieces(payload: bytes, size: int = 16):
    """A body httpx cannot measure, so it sends it without a `Content-Length`."""
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


chunked_payload = urlencode(
    {
        "client_email": "chunked@client.com",
        "client_phone": "",
        "scope": "A submission sent chunked, small enough to be perfectly legal.",
        "budget_text": "under 100k",
    }
).encode("utf-8")

chunked_response = client.post(
    f"/api/client/{chunked_submit.token}/submit",
    content=_in_pieces(chunked_payload),
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
ok(
    "the chunked submit really did travel without a declared length - otherwise "
    "the cheap path would have judged it and this would prove nothing",
    "content-length" not in chunked_response.request.headers
    and chunked_response.request.headers.get("transfer-encoding") == "chunked",
)
ok("a legal chunked submit still answers 200", chunked_response.status_code == 200)
chunked_stored = intakes.get(chunked_submit.id)
ok(
    "and its words reached disk too - what the gate consumed was replayed to the "
    "handler, not swallowed",
    chunked_stored.state == intakes.SUBMITTED
    and chunked_stored.client_email == "chunked@client.com"
    and chunked_stored.scope == "A submission sent chunked, small enough to be perfectly legal."
    and chunked_stored.budget_text == "under 100k",
)

# The same body sent chunked and oversized, through the ordinary client rather
# than the ASGI driver - the surface a real caller actually reaches.
oversized_chunked = client.post(
    "/api/client/not-a-real-token-at-all/submit",
    content=_in_pieces(b"x" * (JSON_CAP + 50_000), size=8192),
    headers={"Content-Type": "application/json"},
)
ok(
    "and an oversized chunked body through the ordinary client is 413, not the 404 "
    "a bogus token would otherwise earn after the body had already been read",
    oversized_chunked.status_code == 413,
)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
