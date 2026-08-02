"""The three stamps: preparing on entry, quoted on finish, quote_failed on error.

Gemini is stubbed. The point is the seam - that the real handler, run end to
end, moves the intake - so the stub replaces only the model call and nothing
else. A previous session shipped a TypeError that isolated tests missed for
exactly this reason.

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

from app import intakes, main, workspaces  # noqa: E402
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


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app=main.app)
headers = {"X-Workspace": made.id}


async def stub_estimate(*args, **kwargs):
    return Estimate(project_name="Booking platform", currency="PHP")


def stub_failure(*args, **kwargs):
    raise RuntimeError("Gemini answered with no usable estimate.")


entry = intakes.create(
    client_email="buyer@client.com",
    client_phone="",
    scope="A booking site.",
    budget_text="around 300k",
    preset={},
    created_by="riku@neptune.ph",
)

main.generate_estimate = stub_estimate
response = client.post(
    "/api/proposals",
    headers=headers,
    data={"brief": "A booking site.", "intake_id": entry.id},
)
ok("the pad still answers 202", response.status_code == 202)
ok("the intake reaches quoted", settle(entry.id, intakes.QUOTED) == intakes.QUOTED)

done = intakes.get(entry.id)
ok("with the bundle recorded", len(done.bundle_ids) == 1)
ok("and what was actually priced", done.priced_scope == "A booking site.")
ok("the client's own words are untouched", done.scope == "A booking site.")

# A failure must leave the intake findable, not stuck at preparing.
second = intakes.create(
    client_email="two@client.com",
    client_phone="",
    scope="A stock take.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
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
ok("and says why", bool(intakes.get(second.id).error))

# The field is optional, and a quotation without one must behave exactly as before.
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
