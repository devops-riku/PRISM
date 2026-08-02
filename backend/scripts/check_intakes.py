"""The intake record: what it stores, and which moves it refuses.

Runs offline against a scratch `generated/` directory:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes.py

Exit code 0 means the state machine only allows what Stage 1 allows.
"""

from __future__ import annotations

import json
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

# An id is a caller-supplied string before it is anything else. Planted three
# directories above `_intakes/` - exactly where `../../../secret` and its
# backslash and drive-absolute forms all land - so the check proves the
# traversal is refused as a matter of id validation, not because nothing
# happens to be sitting at the far end of it by chance.
SECRET = Path(os.environ["GENERATED_DIR"]) / "secret.json"
SECRET.write_text(
    json.dumps({"scope": "TOP SECRET", "client_email": "leak@example.com"}),
    encoding="utf-8",
)
ok(
    "a relative path segment cannot read a file outside this workspace",
    intakes.get("../../../secret") is None,
)
ok(
    "a backslash relative path segment cannot read a file outside this workspace",
    intakes.get("..\\..\\..\\secret") is None,
)
ok(
    "a drive-absolute id cannot replace the workspace root",
    intakes.get(str(SECRET)[: -len(".json")]) is None,
)
ok("a 12-character id that is not hex is refused", intakes.get("zzzzzzzzzzzz") is None)

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

# advance() must not let a caller write a field outside its allowlist - `id`
# above all, since setting it would fork one intake into two files instead of
# moving the one the caller named.
third = intakes.create(
    client_email="three@client.com",
    client_phone="",
    scope="A pilot survey.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
STOLEN_ID = "abcabcabcabc"  # a valid 12-char hex id, just not third's own
before_fork = len(intakes.listing())
refuses(
    "advance() refuses to set id, rather than forking a new file",
    lambda: intakes.advance(third.id, intakes.PREPARING, id=STOLEN_ID),
)
ok("the forking attempt created no new intake", len(intakes.listing()) == before_fork)
ok("and the original is untouched", intakes.get(third.id).state == intakes.SUBMITTED)
ok("the id it tried to steal was never written", intakes.get(STOLEN_ID) is None)

# A wrong-shaped field must be refused outright, not accepted and left to
# corrupt the record on the next read.
intakes.advance(third.id, intakes.PREPARING, job_id="j-third")
refuses(
    "a wrong-shaped field is refused rather than corrupting the record",
    lambda: intakes.advance(third.id, intakes.QUOTED, bundle_ids="not-a-list"),
)
ok("the record still reads back after the bad move was refused", intakes.get(third.id) is not None)
ok(
    "and it is still where it was before the bad move",
    intakes.get(third.id).state == intakes.PREPARING,
)

# Every state's -> CLOSED edge is in the table, but only close() exercised it
# before - advance() has to honour the same column.
fourth = intakes.create(
    client_email="four@client.com",
    client_phone="",
    scope="A rebrand.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(fourth.id, intakes.CLOSED)
ok(
    "submitted -> closed works through advance(), not just close()",
    intakes.get(fourth.id).state == intakes.CLOSED,
)

# close() reaches a 404 in Task 2, which means it has to raise the same way
# advance() does for an intake that is not there.
refuses(
    "close() on an unknown intake is refused",
    lambda: intakes.close("0" * 12, "riku@neptune.ph"),
)

# A file that landed in `_intakes/` without going through `_write()` - by hand,
# or by some other process - must not surface as a queue row just because its
# name happens to end in `.json`. `listing()` globs the directory and hands
# every stem to `get()`, so this is a direct check that `get()`'s id
# validation is what keeps it out, not an accident of what glob happens to see.
(intakes._directory() / "notes.json").write_text(
    json.dumps({"scope": "TOP SECRET"}), encoding="utf-8"
)
ok(
    "a foreign file in _intakes/ is not a queue row",
    all(row.scope != "TOP SECRET" for row in intakes.listing()),
)

# Workspace ids are reusable, so a deleted workspace must take its intakes.
workspaces.delete(made.id)
again = workspaces.create("Neptune Labs")
workspaces.use(again.id)
ok("a deleted workspace takes its intakes with it", intakes.listing() == [])

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
