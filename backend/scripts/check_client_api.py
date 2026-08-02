"""`GET /api/client/{token}` - the first unauthenticated route in this codebase.

Runs with real auth ON throughout, not merely available: `SUPABASE_JWT_SECRET`
is set on `config` directly, right after import, exactly the way
`check_intakes_api.py` turns HS256 verification on for its own permission-model
section - `app.auth` reads that module attribute live on every call, so
assigning it after import is what actually takes. This matters more here than
anywhere else in the suite: an anonymous surface proven against a server that
never checks tokens at all proves nothing, because every route - including the
ones this file exists to show still refuse a stranger - would answer anyone
either way.

    cd backend
    .venv/Scripts/python.exe scripts/check_client_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-client-api-")
# Blanked first, same fix as every other API-level check script: `app.config`
# reads these once at import time via `load_dotenv(..., override=False)`, and
# a real `backend/.env` (this repo's names an actual Supabase project) would
# otherwise win. Turned back on deliberately, below, once `config` exists to
# assign onto.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth as auth_module  # noqa: E402
from app import config  # noqa: E402
from app import intakes  # noqa: E402
from app import main as main_module  # noqa: E402
from app import members  # noqa: E402
from app import settings  # noqa: E402
from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


TEST_JWT_SECRET = "check-client-api-test-secret-do-not-reuse-32byte"
config.SUPABASE_JWT_SECRET = TEST_JWT_SECRET
ok("real auth is actually on before anything else runs", auth_module.required())


def _token(sub: str, email: str) -> str:
    """A signed HS256 access token shaped like the ones Supabase issues -
    just `sub` and `email`, which is all `auth.User` reads."""
    return jwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


workspaces.ensure_ready()
client = TestClient(app)

# --- Two workspaces, each with its own name and its own intake --------------
#
# The two studio names have to actually differ. An `issued` intake's whole
# client-facing body is `{"state": ..., "studio_name": ...}` - if both
# workspaces happened to share a name, the cross-workspace test below would
# pass whether or not the handler used the token to pick the workspace at
# all.

home = workspaces.create("Alpha Studio")
workspaces.use(home.id)
settings.save(settings.load().model_copy(update={"studio_name": "Alpha Studio Name"}))
home_intake = intakes.create(
    client_email="buyer@alpha-client.com",
    client_phone="",
    scope="A booking site for a two-branch clinic.",
    budget_text="around 300k",
    preset={},
    created_by="admin@neptune.ph",
)
home_token = home_intake.token
ok("the home fixture starts issued", home_intake.state == intakes.ISSUED)

other = workspaces.create("Bravo Studio")
workspaces.use(other.id)
settings.save(settings.load().model_copy(update={"studio_name": "Bravo Studio Name"}))
intakes.create(
    client_email="buyer@bravo-client.com",
    client_phone="",
    scope="A different request entirely, in a different workspace.",
    budget_text="",
    preset={},
    created_by="admin@neptune.ph",
)

# --- The one door opens, with no Authorization header at all ----------------

opened = client.get(f"/api/client/{home_token}")
ok(
    "GET /api/client/<token> answers 200 with no Authorization header",
    opened.status_code == 200,
)
ok(
    "it is exactly the issued intake's own view - two fields, nothing else",
    opened.json() == {"state": "issued", "studio_name": "Alpha Studio Name"},
)

# --- X-Workspace is ignored: the token alone decides the workspace ----------
#
# Adversarial by construction: `home_token` was minted in `home`, and this
# request names `other` in the one header a client could otherwise use to lie
# about it. `_gate` calls `workspaces.use(other.id)` on every request, open
# path or not, before this route ever runs - so a handler that read the
# ambient "current" workspace instead of resolving strictly off the token
# would build this response from `other`'s settings and answer "Bravo Studio
# Name" here instead.

crossed = client.get(f"/api/client/{home_token}", headers={"X-Workspace": other.id})
ok(
    "a token resolves the same way regardless of X-Workspace: 200",
    crossed.status_code == 200,
)
ok(
    "and it is still home's own view, not the workspace named in the header",
    crossed.json() == {"state": "issued", "studio_name": "Alpha Studio Name"},
)

# --- Every existing studio route still refuses an anonymous caller ----------

STUDIO_PATHS = [
    "/api/intakes",
    "/api/proposals",
    "/api/settings",
    "/api/team",
    "/api/workspaces",
    "/api/jobs",
    "/api/notifications",
]

for path in STUDIO_PATHS:
    resp = client.get(path, headers={"X-Workspace": home.id})
    ok(f"{path} still 401s with no Authorization header", resp.status_code == 401)

# That list is only a regression test if the paths are real, not merely
# spelled correctly - proven by showing the same caller, with a real admin
# token, gets past the gate on every one of them.
workspaces.use(home.id)
members.claim("admin@neptune.ph", "admin-uid")
admin_token = _token("admin-uid", "admin@neptune.ph")
admin_headers = {"X-Workspace": home.id, "Authorization": f"Bearer {admin_token}"}

for path in STUDIO_PATHS:
    resp = client.get(path, headers=admin_headers)
    ok(
        f"{path} is not 401 for a real admin - the 401 above is the gate, not a dead route",
        resp.status_code != 401,
    )

# --- Unknown, expired and closed are indistinguishable ----------------------

unknown = client.get("/api/client/not-a-real-token-at-all")
ok("an unknown token is 404", unknown.status_code == 404)

workspaces.use(other.id)
closable = intakes.create(
    client_email="withdrawn@client.com",
    client_phone="",
    scope="Withdrawn before it went anywhere.",
    budget_text="",
    preset={},
    created_by="admin@neptune.ph",
)
closable_token = closable.token  # captured before close() blanks it on disk
intakes.close(closable.id, "admin@neptune.ph")
closed = client.get(f"/api/client/{closable_token}")
ok("a closed intake's token is 404, the same as unknown", closed.status_code == 404)

expiring = intakes.create(
    client_email="expired@client.com",
    client_phone="",
    scope="Left too long.",
    budget_text="",
    preset={},
    created_by="admin@neptune.ph",
)
expired_token = expiring.token
stale = intakes.get(expiring.id)
stale.token_expires_at = "2000-01-01T00:00:00Z"
intakes._write(stale)  # noqa: SLF001 - planting an expired stamp past the public API
expired = client.get(f"/api/client/{expired_token}")
ok("an expired token is 404, the same as unknown", expired.status_code == 404)

ok(
    "unknown, closed and expired share one status code",
    unknown.status_code == closed.status_code == expired.status_code == 404,
)
ok(
    "and byte-identical bodies naming the same reason - a stranger cannot tell "
    "which kind of gone this is",
    unknown.json() == closed.json() == expired.json() == {"detail": main_module._CLIENT_LINK_GONE},
)
ok("that body carries no clue beyond 'gone'", set(unknown.json()) == {"detail"})

# --- A state clientview.of cannot show must not become a traceback ----------
#
# `clientview.of` raises `ValueError` for a state it does not recognise, or a
# quoted-face state with no bundle attached - see its own docstring.
# `tokens.py`'s module docstring names this exact case: an unknown token has
# to answer 404, not a 500 with a stack trace, even when the failure is on
# this server's own side of the line. `home_token` is still good at this
# point in the run - forcing `clientview.of` itself to fail is the only way
# to reach this branch without a real state clientview cannot show.
_real_of = main_module.clientview.of


def _boom_of(*_args, **_kwargs):
    raise ValueError("a state clientview does not know how to show")


try:
    main_module.clientview.of = _boom_of
    broken = client.get(f"/api/client/{home_token}")
finally:
    main_module.clientview.of = _real_of

ok(
    "a clientview failure answers the same 404 body, not a 500 with a traceback",
    broken.status_code == 404 and broken.json() == {"detail": main_module._CLIENT_LINK_GONE},
)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
