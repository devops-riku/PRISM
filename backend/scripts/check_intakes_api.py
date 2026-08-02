"""The intake routes: what they return, and who is allowed to call them.

Offline. No model, no network:

    cd backend
    .venv/Scripts/python.exe scripts/check_intakes_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intake-api-")
# `app.config` reads these once at import time via `load_dotenv(..., override=False)`,
# so a real backend/.env (this repo's has a Supabase project configured) would
# otherwise win and turn on token verification - every request below is
# headerless, so the gate would 401 all of them before a single route ran.
# See scripts/check_kind_api.py for the same fix.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app)
headers = {"X-Workspace": made.id}

created = client.post(
    "/api/intakes",
    headers=headers,
    json={
        "client_email": "buyer@client.com",
        "client_phone": "+63 917 000 0000",
        "scope": "A booking site for two clinics.",
        "budget_text": "around 300k",
        "preset": {"kind": "software", "currency": "PHP"},
    },
)
ok("creating an intake answers 201", created.status_code == 201)
body = created.json()
ok("it comes back submitted", body["state"] == "submitted")
ok("with the client's words", body["scope"] == "A booking site for two clinics.")

listed = client.get("/api/intakes", headers=headers)
ok("the queue lists it", listed.status_code == 200 and len(listed.json()) == 1)

read = client.get(f"/api/intakes/{body['id']}", headers=headers)
ok("it reads back by id", read.status_code == 200 and read.json()["id"] == body["id"])

ok(
    "an unknown id is 404, not 500",
    client.get("/api/intakes/000000000000", headers=headers).status_code == 404,
)
ok(
    "a malformed (non-hex) id is 404, not 500 - intakes.get()'s own guard, "
    "not anything the route layer re-checks",
    client.get("/api/intakes/zzzzzzzzzzzz", headers=headers).status_code == 404,
)
ok(
    "closing a malformed id is 404, not 500",
    client.post("/api/intakes/zzzzzzzzzzzz/close", headers=headers).status_code == 404,
)

closed = client.post(f"/api/intakes/{body['id']}/close", headers=headers)
ok("closing answers 200", closed.status_code == 200)
ok("and the state says so", closed.json()["state"] == "closed")

# An intake belongs to its workspace and must not be visible from another.
other = workspaces.create("Someone Else")
ok(
    "another workspace sees none of them",
    client.get("/api/intakes", headers={"X-Workspace": other.id}).json() == [],
)
ok(
    "and cannot read one by id",
    client.get(f"/api/intakes/{body['id']}", headers={"X-Workspace": other.id}).status_code == 404,
)
ok(
    "nor close one by id - isolation holds on a write, not just on reads",
    client.post(
        f"/api/intakes/{body['id']}/close", headers={"X-Workspace": other.id}
    ).status_code
    == 404,
)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
