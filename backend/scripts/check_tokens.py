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
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-tokens-")

from app import intakes, tokens, workspaces  # noqa: E402

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

# --- deleting a workspace makes its tokens stop resolving ------------------

doomed = make(home.id, "doomed")
doomed_token = doomed.token
workspaces.delete(home.id)

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

perf_a = workspaces.create("Perf Bucket A")
perf_b = workspaces.create("Perf Bucket B")

target = None
for index in range(50):
    fixture = make(perf_a.id if index % 2 == 0 else perf_b.id, f"perf-{index}")
    if index == 0:
        target = fixture

workspaces.use(perf_b.id)
primed = tokens.resolve(target.token)
ok("the fixture used for timing actually resolves", primed == (perf_a.id, target.id))

started = time.perf_counter()
for _ in range(200):
    tokens.resolve(target.token)
elapsed = time.perf_counter() - started
ok(f"200 resolves of a known token take under 0.5s (took {elapsed:.3f}s)", elapsed < 0.5)

# --- comparison is constant-time -------------------------------------------
#
# Crude, and honest: this asserts the property is implemented, not timed -
# reading the module source for the symbol `members.find_invite` and
# `members.accept` are also checked against.

source = inspect.getsource(tokens)
ok(
    "resolve() compares tokens with secrets.compare_digest, not ==",
    "secrets.compare_digest" in source,
)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
