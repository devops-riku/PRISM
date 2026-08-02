"""The kind arrives with the brief and survives to the stored quotation.

POST /api/proposals takes `kind` and `kind_label` as form fields. This asserts
the three answers that matter and no others:

    kind=accounting  -> the stored bundle's estimate.kind is "accounting"
    no kind at all   -> "software", which is what every quotation was before
                        this field existed and must stay
    kind=nonsense    -> "software", because `kinds.resolve` never raises and a
                        picker sending an id nobody knows is not a 400

Gemini is not called: `app.main.generate_estimate` is replaced with a stub that
answers `kind="software"` whatever it is asked for. That is deliberate - the
endpoint has to stamp the kind onto the estimate itself, and a stub that
politely echoed the request back would prove nothing. The stub also records the
keyword arguments it was handed, so the `kind=` the prompt layer is given is
checked as well as the one the bundle ends up with.

Runs offline against a scratch `generated/` directory, so it neither reads nor
writes the studio's real work:

    cd backend
    .venv/Scripts/python.exe scripts/check_kind_api.py        # Windows
    .venv/bin/python scripts/check_kind_api.py                # macOS / Linux

Exit code 0 means the kind survives the round trip.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from tempfile import mkdtemp

# `backend/` on the path so `app.*` resolves however this file is invoked.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# The em dash and the peso sign do not survive the Windows console codepage.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):  # pragma: no cover - non-standard stream
        pass

# Set before `app.config` is imported, because it reads the environment once at
# import time. A real variable wins over backend/.env, so blanking the Supabase
# settings is what lets this call the API without a token, and GENERATED_DIR
# keeps every file this writes out of the studio's own generated/ folder.
SCRATCH = Path(mkdtemp(prefix="prism-kind-check-"))
os.environ["GENERATED_DIR"] = str(SCRATCH)
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import main as api  # noqa: E402

# `app.main` configures the root logger on import. Four quotations' worth of
# progress logging would bury the three lines this script exists to print, and
# a real failure is logged at ERROR and still gets through.
logging.getLogger().setLevel(logging.WARNING)

from app import workspaces  # noqa: E402
from app.schemas import (  # noqa: E402
    ClientNarrative,
    CostSummary,
    Estimate,
    LineItem,
    UnitKind,
)

BRIEF = (
    "Two years of books to clean up for a trading company, then monthly "
    "bookkeeping and the annual statutory filing."
)

#: Every keyword argument the stub was called with, in order.
CALLS: list[dict] = []


async def _stubbed_gemini(*args, **kwargs) -> Estimate:
    """A small estimate, always claiming to be software.

    Whatever discipline was asked for, this answers `kind="software"` - the
    value the schema defaults to and the one a model that ignored the
    instruction would produce. Anything the assertions below see is therefore
    the endpoint's doing, not the stub's.
    """
    CALLS.append(kwargs)
    return Estimate(
        project_name="Ledger cleanup",
        client_name="Northwind Trading",
        kind="software",
        currency="PHP",
        line_items=[
            LineItem(
                id="LI-01",
                category="Engagement",
                description="Two years of ledgers reconciled and closed",
                role="Senior accountant",
                quantity=6.0,
                unit=UnitKind.day,
                unit_rate=14_500.0,
            )
        ],
        cost=CostSummary(contingency_pct=10.0, tax_label="VAT", tax_pct=12.0),
        client=ClientNarrative(title="Ledger cleanup"),
    )


def _quotation_for(client: TestClient, **fields: str) -> dict:
    """Post a brief, wait for the job, and read the bundle back off the API."""
    response = client.post("/api/proposals", data={"brief": BRIEF, **fields})
    assert response.status_code == 202, f"{response.status_code}: {response.text}"
    job_id = response.json()["id"]

    # The generation runs behind the request, so the job has to be waited on.
    # Bounded, and a failure is read out rather than timing out silently - a
    # signature mismatch inside the worker would otherwise surface as nothing
    # more than "it never finished".
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] == "done":
            break
        if job["state"] == "failed":
            raise AssertionError(f"the job failed: {job['error']}")
        time.sleep(0.05)
    else:
        raise AssertionError(f"job {job_id} never finished")

    assert job["result_ids"], "the job finished without producing a quotation"
    stored = client.get(f"/api/proposals/{job['result_ids'][0]}")
    assert stored.status_code == 200, f"{stored.status_code}: {stored.text}"
    return stored.json()


def main() -> int:
    print("PRISM kind check - offline, no key, no network")
    print("=" * 78)
    print(f"  scratch  {SCRATCH}")

    api.generate_estimate = _stubbed_gemini  # type: ignore[assignment]

    with TestClient(api.app) as client:
        # A scratch generated/ has no workspace, and every handler that files
        # anything answers 409 until one exists.
        workspace = workspaces.create("Kind check")
        print(f"  workspace  {workspace.id}")

        cases = [
            ("kind=accounting", {"kind": "accounting"}, "accounting", "accounting"),
            ("no kind sent", {}, "software", "software"),
            ("kind=nonsense", {"kind": "nonsense"}, "software", "nonsense"),
        ]

        print("\nThe kind on the stored quotation")
        for label, fields, expected, sent in cases:
            before = len(CALLS)
            bundle = _quotation_for(client, **fields)
            stored = bundle["estimate"]["kind"]
            assert stored == expected, f"{label}: stored kind is {stored!r}, expected {expected!r}"

            # `kind=` reaches the generation call as the resolved id, so the
            # prompt is written for the same discipline the document is.
            passed = CALLS[before].get("kind", "<not passed>")
            assert passed == expected, (
                f"{label}: generate_estimate was given kind={passed!r}, expected {expected!r}"
            )
            print(f"  ok  {label:<16} -> sent {sent!r}, stored {stored!r}, prompt {passed!r}")

        # `other` is the one kind that carries a name of its own, and it is the
        # only reason kind_label exists.
        print("\nThe typed label")
        bundle = _quotation_for(client, kind="other", kind_label="Marine survey")
        estimate = bundle["estimate"]
        assert estimate["kind"] == "other", f"kind is {estimate['kind']!r}"
        assert estimate["kind_label"] == "Marine survey", f"label is {estimate['kind_label']!r}"
        print(f"  ok  kind=other      -> stored label {estimate['kind_label']!r}")

        # Every tier of one brief is one discipline.
        print("\nA ladder")
        response = client.post(
            "/api/proposals",
            data={"brief": BRIEF, "kind": "accounting", "tiers": "Basic, Standard"},
        )
        assert response.status_code == 202, f"{response.status_code}: {response.text}"
        job_id = response.json()["id"]
        for _ in range(200):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["state"] == "done":
                break
            if job["state"] == "failed":
                raise AssertionError(f"the tier job failed: {job['error']}")
            time.sleep(0.05)
        else:
            raise AssertionError(f"job {job_id} never finished")

        assert len(job["result_ids"]) == 2, f"{len(job['result_ids'])} tiers came back, expected 2"
        for quotation_id in job["result_ids"]:
            tier = client.get(f"/api/proposals/{quotation_id}").json()
            kind = tier["estimate"]["kind"]
            name = tier["tier_name"]
            assert kind == "accounting", f"tier {name!r} is {kind!r}, not accounting"
        print("  ok  both tiers stored kind 'accounting'")

    print("\nKIND CHECK PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
