"""Sending a quotation to a client: what leaves, and what happens when it cannot.

The property this file exists for: **a row that reads `sent` means a client was
actually emailed.** `send_intake` mails first and advances second, so a refusal
from Resend has to leave the intake exactly where it was. Getting that backwards
is not a cosmetic bug - `clientview.of` starts the client's own clock from
`sent_at`, so a `sent` that never reached anybody is a quotation the studio
believes is being read and nobody has.

Offline. No model, no network, no Resend: every send below goes to a stub.

    cd backend
    .venv/Scripts/python.exe scripts/check_send_email.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-send-email-")
os.environ["DATABASE_URL"] = ""
os.environ["CHECK_BRIEF_IS_REAL"] = "0"
# See check_intakes_api.py for why these three are blanked: a real backend/.env
# would turn on token verification and 401 every headerless request below.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""
# Configured by default so the sending path is the one under test. Individual
# checks below blank `config.RESEND_*` at runtime instead of re-importing -
# `mailer.configured()` reads both attributes on every call, which is what
# makes that possible.
os.environ["RESEND_API_KEY"] = "re_test_not_a_real_key"
os.environ["RESEND_FROM"] = "studio@example.test"

from fastapi.testclient import TestClient  # noqa: E402

from app.features.intakes.application import service as intakes_module  # noqa: E402
from app.features.quotations.infrastructure import repository as storage  # noqa: E402
from app.features.quotations.presentation import routes as quotation_routes  # noqa: E402
from app.features.team.infrastructure import mailer  # noqa: E402
from app.features.workspaces.application import settings  # noqa: E402
from app.features.workspaces.infrastructure import repository as workspaces  # noqa: E402
from app.main import app  # noqa: E402
from app.shared.infrastructure import config  # noqa: E402
from app.features.quotations.domain.models import (  # noqa: E402
    ClientNarrative,
    CostSummary,
    Estimate,
    LineItem,
    PaymentMilestone,
    ProposalBundle,
    UnitKind,
)

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


FIXTURE_ESTIMATE = Estimate(
    project_name="Harbour Rebuild",
    client_name="Acme Co.",
    currency="PHP",
    client=ClientNarrative(
        title="Harbour Rebuild",
        executive_summary="A fixture quotation for the send path.",
        validity_days=30,
    ),
    line_items=[
        LineItem(
            id="LI-01",
            category="Backend",
            description="Build the thing",
            role="Senior Backend Engineer",
            quantity=5,
            unit=UnitKind.hour,
            unit_rate=1000.0,
            subtotal=5000.0,
        ),
    ],
    cost=CostSummary(
        subtotal=5000.0,
        total=5000.0,
        payment_milestones=[
            PaymentMilestone(label="Deposit", percent=50.0, amount=2500.0, trigger="Signing"),
            PaymentMilestone(label="Delivery", percent=50.0, amount=2500.0, trigger="Delivery"),
        ],
    ),
    quotation_ref="SEND-0000001",
)


def _real_bundle() -> str:
    bundle_id = storage.new_id()
    storage.save(
        ProposalBundle(
            id=bundle_id,
            created_at=storage.utc_now_iso(),
            estimate=FIXTURE_ESTIMATE,
            files=quotation_routes._build_files(bundle_id, FIXTURE_ESTIMATE),  # noqa: SLF001
            revision=1,
            root_id=bundle_id,
        )
    )
    return bundle_id


def _quoted(client_email: str = "maria@acme.test") -> tuple[str, str]:
    """An intake walked to `quoted` with one real bundle. Returns (id, bundle)."""
    bundle_id = _real_bundle()
    entry = intakes_module.create(
        client_email=client_email,
        client_phone="",
        scope="",
        budget_text="",
        preset={},
        created_by="admin@example.test",
    )
    intakes_module.advance(entry.id, intakes_module.SUBMITTED)
    intakes_module.advance(entry.id, intakes_module.PREPARING, job_id="job-fixture")
    intakes_module.advance(entry.id, intakes_module.QUOTED, bundle_ids=[bundle_id])
    return entry.id, bundle_id


#: Every message the stub was handed, in order. The assertions below care about
#: this as much as about the status code: a stub that never fired and a stub
#: that fired correctly both leave a 200 behind on the happy path, and only one
#: of those means an email was composed.
SENT: list[dict] = []


def _stub_send(*, to: str, subject: str, message: str, studio: str) -> None:
    SENT.append({"to": to, "subject": subject, "message": message, "studio": studio})


def _stub_refuse(*, to: str, subject: str, message: str, studio: str) -> None:
    raise mailer.MailError("Resend answered 422.")


# Patched on the `mailer` MODULE, which is what `app/api/intakes.py` resolves
# through - it does `from app import mailer` and then `mailer.send_quotation(...)`,
# an attribute lookup on the shared module object at call time. Had the route
# imported `send_quotation` directly, that name would live in the
# route module's own globals and this line would patch nothing while every
# assertion still passed. That is not hypothetical: it is exactly how two
# renderer assertions in check_client_api.py came to pass for the wrong reason.
_REAL_SEND = mailer.send_quotation

workspaces.ensure_ready()
made = workspaces.create("Neptune Labs")
client = TestClient(app)
headers = {"X-Workspace": made.id}

studio_name = settings.load().studio_name


# --- The email actually goes, and the state follows it ------------------------

mailer.send_quotation = _stub_send
SENT.clear()

intake_id, bundle_id = _quoted()
sent = client.post(
    f"/api/intakes/{intake_id}/send",
    headers=headers,
    json={
        "bundle_id": bundle_id,
        "subject": "Your quotation for Harbour Rebuild",
        "message": "Hi Maria,\n\nHere it is: https://example.test/#/c/abc\n\nThanks,",
    },
)
ok("sending answers 200", sent.status_code == 200)
ok("the intake is now sent", sent.json()["state"] == "sent")
ok("and records which bundle the client was shown", sent.json()["sent_bundle_id"] == bundle_id)
ok("with the moment it happened", bool(sent.json()["sent_at"]))
ok("exactly one email was composed", len(SENT) == 1)
ok("addressed to the intake's own client", SENT and SENT[0]["to"] == "maria@acme.test")
ok(
    "carrying the studio's subject verbatim - not one this route invented",
    SENT and SENT[0]["subject"] == "Your quotation for Harbour Rebuild",
)
ok(
    "and the studio's own words verbatim, newlines and link intact",
    SENT and SENT[0]["message"].startswith("Hi Maria,\n\nHere it is: https://example.test/#/c/abc"),
)
ok("signed with the studio's name from settings", SENT and SENT[0]["studio"] == studio_name)


# --- A caller that brings no words still sends something readable -------------

SENT.clear()
intake_id, bundle_id = _quoted()
defaulted = client.post(
    f"/api/intakes/{intake_id}/send", headers=headers, json={"bundle_id": bundle_id}
)
ok("a send with no subject or message still succeeds", defaulted.status_code == 200)
ok("and still emails once", len(SENT) == 1)
ok("with a subject that names the studio", SENT and studio_name in SENT[0]["subject"])
ok(
    "and a body carrying the client's own link, not a bare template",
    SENT and "/#/c/" in SENT[0]["message"],
)


# --- Resend refuses: nothing moves -------------------------------------------

mailer.send_quotation = _stub_refuse
SENT.clear()

intake_id, bundle_id = _quoted()
refused = client.post(
    f"/api/intakes/{intake_id}/send",
    headers=headers,
    json={"bundle_id": bundle_id, "subject": "s", "message": "m"},
)
ok("a refusal from Resend answers 502, not 500", refused.status_code == 502)
ok(
    "and says what the mail service said, so the studio can act on it",
    "422" in refused.json()["detail"],
)
after = intakes_module.get(intake_id)
ok("THE INTAKE IS STILL QUOTED - the state did not move ahead of the email", after.state == "quoted")
ok("nothing was stamped on it", not after.sent_at and not after.sent_bundle_id)
ok(
    "and pressing send again is all it takes to recover - the bundle is untouched",
    bundle_id in after.bundle_ids,
)


# --- No client address: refused before Resend is ever called ------------------

mailer.send_quotation = _stub_send
SENT.clear()

intake_id, bundle_id = _quoted(client_email="")
nowhere = client.post(
    f"/api/intakes/{intake_id}/send",
    headers=headers,
    json={"bundle_id": bundle_id, "subject": "s", "message": "m"},
)
ok("an intake with no client address is refused with 400", nowhere.status_code == 400)
ok(
    "and the message says what to do instead rather than naming a field",
    "Copy the link" in nowhere.json()["detail"],
)
ok("no email was attempted", len(SENT) == 0)
ok("and the intake stayed quoted", intakes_module.get(intake_id).state == "quoted")


# --- notify=false: the studio is sending it themselves ------------------------

SENT.clear()
intake_id, bundle_id = _quoted()
quiet = client.post(
    f"/api/intakes/{intake_id}/send",
    headers=headers,
    json={"bundle_id": bundle_id, "notify": False},
)
ok("notify=false still advances the intake", quiet.status_code == 200)
ok("to sent", quiet.json()["state"] == "sent")
ok("and emails nobody", len(SENT) == 0)


# --- No mail configured: exactly the behaviour this route always had ----------
#
# The regression this guards: making the email mandatory would leave an install
# with no Resend key unable to move its queue at all. Every check script in this
# repo runs in that state, which is why they all keep passing unchanged.

real_key, real_from = config.RESEND_API_KEY, config.RESEND_FROM
config.RESEND_API_KEY = ""
config.RESEND_FROM = ""
try:
    ok("with no key, mailer reports itself unconfigured", not mailer.configured())
    SENT.clear()
    intake_id, bundle_id = _quoted()
    unconfigured = client.post(
        f"/api/intakes/{intake_id}/send", headers=headers, json={"bundle_id": bundle_id}
    )
    ok("sending still answers 200", unconfigured.status_code == 200)
    ok("the intake still reaches sent", unconfigured.json()["state"] == "sent")
    ok("and no email was attempted", len(SENT) == 0)

    # The address check lives inside the mailing branch, so an install with no
    # mail is not suddenly refused for a field it was never going to use.
    SENT.clear()
    intake_id, bundle_id = _quoted(client_email="")
    no_address = client.post(
        f"/api/intakes/{intake_id}/send", headers=headers, json={"bundle_id": bundle_id}
    )
    ok(
        "and an intake with no client address is NOT refused when there is no "
        "mail to send anyway",
        no_address.status_code == 200,
    )
finally:
    config.RESEND_API_KEY, config.RESEND_FROM = real_key, real_from
    mailer.send_quotation = _REAL_SEND


# --- The body a client actually receives -------------------------------------

body = mailer._quotation_body("Ridge & Co", "Line one\nLine two <b>x</b>")  # noqa: SLF001
ok("the studio name is escaped into the wrapper", "Ridge &amp; Co" in body)
ok("newlines the studio typed survive as line breaks", "<br>" in body)
ok(
    "and anything tag-shaped in the message is inert by the time it is markup",
    "&lt;b&gt;" in body and "<b>x</b>" not in body,
)
ok(
    "no invented call-to-action button - the link is wherever the studio put it",
    "Join the workspace" not in body and "View your quotation" not in body,
)


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)
print("all checks passed")
