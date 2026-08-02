"""The intake record: what it stores, and which moves it refuses.

Runs offline against a scratch `generated/` directory:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes.py

Exit code 0 means the state machine only allows what this table allows -
Stage 1's states, plus `issued`, `sent`, `revision_requested`, `finalized`
and `proposal_sent`, now open.
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

ok("an intake starts at issued, not submitted", entry.state == intakes.ISSUED)
ok("it carries the client's words verbatim", entry.scope == "A booking site for two clinics.")
ok("and their budget as text, not a number", entry.budget_text == "around 300k")
ok("it has a 12-character id", len(entry.id) == 12)
ok("it is readable again", intakes.get(entry.id) is not None)
ok("and it is in the listing", [row.id for row in intakes.listing()] == [entry.id])
ok("it is minted with a client link", bool(entry.token))
ok("and an expiry for that link", bool(entry.token_expires_at))

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

# `issued -> submitted` is the hop Stage 2 put in front of Stage 1's path.
# The client's own submit route performs it for real once it ships; here it
# stands in for a studio that already has everything it needs to move on,
# exactly as Stage 1's flow always did the moment an intake was created.
intakes.advance(entry.id, intakes.SUBMITTED)
ok("issued -> submitted", intakes.get(entry.id).state == intakes.SUBMITTED)

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

# A second Generate on an already-quoted intake is reachable in the shipped
# UI (Price this -> pad -> Generate -> browser Back -> Generate again), so
# `quoted -> preparing` has to be a legal move rather than a dead end that
# leaves the record pointing at a bundle nobody is looking at anymore.
intakes.advance(entry.id, intakes.PREPARING, job_id="j1-again")
ok("quoted -> preparing is now legal", intakes.get(entry.id).state == intakes.PREPARING)

# Illegal moves, forever.
refuses("an unknown state is refused", lambda: intakes.advance(entry.id, "nonsense"))
refuses("an unknown intake is refused", lambda: intakes.advance("0" * 12, intakes.CLOSED))

closed = intakes.close(entry.id, "riku@neptune.ph")
ok("anything -> closed", closed.state == intakes.CLOSED)
ok("closed records who", closed.closed_by == "riku@neptune.ph")
refuses("closed is terminal", lambda: intakes.advance(entry.id, intakes.PREPARING))

# --- The states Stage 2 opens: written since Stage 1, refused until now. A
#     fresh intake, walked start to finish, so a wrong ALLOWED entry shows up
#     as a state the record could not reach, not as a refusal nobody
#     expected to see refused. ------------------------------------------

opened = intakes.create(
    client_email="opened@client.com",
    client_phone="",
    scope="A full walk through the states Stage 2 opens.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(opened.id, intakes.SUBMITTED)
intakes.advance(opened.id, intakes.PREPARING, job_id="jo1")
intakes.advance(
    opened.id, intakes.QUOTED, bundle_ids=["aaaaaaaaaaaa"], priced_scope="x", priced_budget="y"
)

sent = intakes.advance(opened.id, intakes.SENT, sent_bundle_id="aaaaaaaaaaaa")
ok("quoted -> sent is reachable now the client link ships", sent.state == intakes.SENT)
ok("and records which bundle was actually sent", sent.sent_bundle_id == "aaaaaaaaaaaa")

revised = intakes.advance(
    opened.id,
    intakes.REVISION_REQUESTED,
    revisions=[{"asked": "Please add a second location.", "at": "2026-08-03T00:00:00Z"}],
)
ok("sent -> revision_requested is reachable", revised.state == intakes.REVISION_REQUESTED)
ok(
    "and keeps what the client asked for, not just that they asked",
    revised.revisions[-1]["asked"] == "Please add a second location.",
)

intakes.advance(opened.id, intakes.PREPARING, job_id="jo2")
ok("revision_requested -> preparing is reachable", intakes.get(opened.id).state == intakes.PREPARING)
intakes.advance(
    opened.id, intakes.QUOTED, bundle_ids=["bbbbbbbbbbbb"], priced_scope="x2", priced_budget="y2"
)
intakes.advance(opened.id, intakes.SENT, sent_bundle_id="bbbbbbbbbbbb")
finalized = intakes.advance(opened.id, intakes.FINALIZED)
ok("sent -> finalized is reachable", finalized.state == intakes.FINALIZED)

proposal_sent = intakes.advance(opened.id, intakes.PROPOSAL_SENT)
ok("finalized -> proposal_sent is reachable", proposal_sent.state == intakes.PROPOSAL_SENT)

refuses(
    "proposal_sent only goes forward to closed",
    lambda: intakes.advance(opened.id, intakes.SUBMITTED),
)
intakes.advance(opened.id, intakes.CLOSED)
ok("proposal_sent -> closed still works", intakes.get(opened.id).state == intakes.CLOSED)
refuses(
    "closed is terminal here too, reached the long way round",
    lambda: intakes.advance(opened.id, intakes.PREPARING),
)

# proposal_sent is reachable only from finalized - not straight from quoted
# and not straight from sent. Separate, fresh intakes for each: a refusal is
# a no-op, but sharing one fixture across two refusal checks would make the
# second one prove nothing if the first move had silently gone through.
from_quoted = intakes.create(
    client_email="from-quoted@client.com",
    client_phone="",
    scope="Reaches quoted and stops.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(from_quoted.id, intakes.SUBMITTED)
intakes.advance(from_quoted.id, intakes.PREPARING, job_id="jq")
intakes.advance(
    from_quoted.id, intakes.QUOTED, bundle_ids=["cccccccccccc"], priced_scope="x", priced_budget="y"
)
refuses(
    "proposal_sent is reachable only from finalized, not straight from quoted",
    lambda: intakes.advance(from_quoted.id, intakes.PROPOSAL_SENT),
)
# `finalized` only follows `sent` - a quotation the client has not been sent
# yet cannot be finalized just because it exists. `from_quoted` is still
# sitting at `quoted` (the refusal above is a no-op), so it doubles for this.
refuses(
    "quoted -> finalized is refused - finalized only follows sent",
    lambda: intakes.advance(from_quoted.id, intakes.FINALIZED),
)

from_sent = intakes.create(
    client_email="from-sent@client.com",
    client_phone="",
    scope="Reaches sent and stops.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(from_sent.id, intakes.SUBMITTED)
intakes.advance(from_sent.id, intakes.PREPARING, job_id="js")
intakes.advance(
    from_sent.id, intakes.QUOTED, bundle_ids=["dddddddddddd"], priced_scope="x", priced_budget="y"
)
intakes.advance(from_sent.id, intakes.SENT, sent_bundle_id="dddddddddddd")
refuses(
    "proposal_sent is reachable only from finalized, not straight from sent",
    lambda: intakes.advance(from_sent.id, intakes.PROPOSAL_SENT),
)

# `sent` only follows `quoted` - a request cannot be sent to a client before
# it has ever been priced, whether it is still waiting to be submitted or is
# already being prepared. Two more fixtures, each stopped one state short of
# `quoted`, for the same reason the pair above got their own: a refusal is a
# no-op, and reusing one fixture across two checks would let the first
# silently succeeding hide behind the second.
from_submitted = intakes.create(
    client_email="from-submitted@client.com",
    client_phone="",
    scope="Reaches submitted and stops.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(from_submitted.id, intakes.SUBMITTED)
refuses(
    "submitted -> sent is refused - sent only follows quoted",
    lambda: intakes.advance(from_submitted.id, intakes.SENT),
)

from_preparing = intakes.create(
    client_email="from-preparing@client.com",
    client_phone="",
    scope="Reaches preparing and stops.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(from_preparing.id, intakes.SUBMITTED)
intakes.advance(from_preparing.id, intakes.PREPARING, job_id="jp")
refuses(
    "preparing -> sent is refused - sent only follows quoted",
    lambda: intakes.advance(from_preparing.id, intakes.SENT),
)

# A failure has somewhere to go.
second = intakes.create(
    client_email="two@client.com",
    client_phone="",
    scope="A stock take.",
    budget_text="",
    preset={},
    created_by="riku@neptune.ph",
)
intakes.advance(second.id, intakes.SUBMITTED)
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
ok("and the original is untouched", intakes.get(third.id).state == intakes.ISSUED)
ok("the id it tried to steal was never written", intakes.get(STOLEN_ID) is None)

# A wrong-shaped field must be refused outright, not accepted and left to
# corrupt the record on the next read.
intakes.advance(third.id, intakes.SUBMITTED)
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
# before - advance() has to honour the same column. `fourth` is left at
# `issued` on purpose: `issued -> closed` is the edge this now proves - a
# client link can be withdrawn before anybody has ever opened it.
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
    "issued -> closed works through advance(), not just close()",
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
