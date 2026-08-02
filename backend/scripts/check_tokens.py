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

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
