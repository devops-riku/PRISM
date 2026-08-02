"""The intake record: what it stores, and which moves it refuses.

Runs offline against a scratch `generated/` directory:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes.py

Exit code 0 means the state machine only allows what Stage 1 allows.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intakes-")

from app import intakes, workspaces  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def refuses(label: str, call) -> None:
    try:
        call()
    except intakes.IntakeError:
        ok(label, True)
        return
    ok(label, False)


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
workspaces.use(made.id)

entry = intakes.create(
    client_email="buyer@client.com",
    client_phone="+63 917 000 0000",
    scope="A booking site for two clinics.",
    budget_text="around 300k",
    preset={"kind": "software", "currency": "PHP"},
    created_by="riku@neptune.ph",
)

ok("an intake starts at submitted", entry.state == intakes.SUBMITTED)
ok("it carries the client's words verbatim", entry.scope == "A booking site for two clinics.")
ok("and their budget as text, not a number", entry.budget_text == "around 300k")
ok("it has a 12-character id", len(entry.id) == 12)
ok("it is readable again", intakes.get(entry.id) is not None)
ok("and it is in the listing", [row.id for row in intakes.listing()] == [entry.id])

# The Stage 1 path.
intakes.advance(entry.id, intakes.PREPARING, job_id="j1")
ok("submitted -> preparing", intakes.get(entry.id).state == intakes.PREPARING)

intakes.advance(
    entry.id,
    intakes.QUOTED,
    bundle_ids=["abc123def456"],
    priced_scope="A booking site for two clinics, two locations.",
    priced_budget="300000",
)
quoted = intakes.get(entry.id)
ok("preparing -> quoted", quoted.state == intakes.QUOTED)
ok("the bundle is recorded", quoted.bundle_ids == ["abc123def456"])
ok("what was actually priced is kept apart from what was asked", quoted.priced_scope != quoted.scope)

# The states Stage 2 turns on are refused now.
refuses("quoted -> sent is refused in stage 1", lambda: intakes.advance(entry.id, intakes.SENT))
refuses(
    "quoted -> finalized is refused in stage 1",
    lambda: intakes.advance(entry.id, intakes.FINALIZED),
)

# Illegal moves, forever.
refuses("quoted -> preparing is refused", lambda: intakes.advance(entry.id, intakes.PREPARING))
refuses("an unknown state is refused", lambda: intakes.advance(entry.id, "nonsense"))
refuses("an unknown intake is refused", lambda: intakes.advance("0" * 12, intakes.CLOSED))

closed = intakes.close(entry.id, "riku@neptune.ph")
ok("anything -> closed", closed.state == intakes.CLOSED)
ok("closed records who", closed.closed_by == "riku@neptune.ph")
refuses("closed is terminal", lambda: intakes.advance(entry.id, intakes.PREPARING))

# A failure has somewhere to go.
second = intakes.create(
    client_email="two@client.com",
    client_phone="",
    scope="A stock take.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(second.id, intakes.PREPARING, job_id="j2")
intakes.advance(second.id, intakes.QUOTE_FAILED, error="Gemini answered with no usable estimate.")
ok("preparing -> quote_failed", intakes.get(second.id).state == intakes.QUOTE_FAILED)
intakes.advance(second.id, intakes.PREPARING, job_id="j3")
ok("and a failed intake can be retried", intakes.get(second.id).state == intakes.PREPARING)

# Workspace ids are reusable, so a deleted workspace must take its intakes.
workspaces.delete(made.id)
again = workspaces.create("Neptune Labs")
workspaces.use(again.id)
ok("a deleted workspace takes its intakes with it", intakes.listing() == [])

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
