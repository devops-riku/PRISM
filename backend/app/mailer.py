"""Sending one email: the invitation.

Resend, over its HTTP API, with `urllib` rather than a new dependency - one POST
with a JSON body does not need a client library. Nothing else in PRISM sends
mail, and nothing else should: an app that quietly emails people is one nobody
can predict.

Unconfigured is not a failure. With no key the invitation is still created and
the API answers with its link, so a studio can paste it into whatever they
already use. That is the honest fallback: the invitation is the record, and the
email is only how it travels.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app import config

logger = logging.getLogger("prism.mail")

__all__ = ["configured", "send_invite", "MailError"]

ENDPOINT = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 12


class MailError(RuntimeError):
    """Resend refused the message, or could not be reached."""


def configured() -> bool:
    return bool(config.RESEND_API_KEY.strip() and config.RESEND_FROM.strip())


def _body(studio: str, workspace: str, inviter: str, role: str, link: str) -> str:
    """The message, in the app's own voice: what, from whom, and one link."""
    who = f"{inviter} invited you" if inviter else "You have been invited"
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            color:#1D1B17;background:#F6F3EE;padding:32px">
  <div style="max-width:520px;margin:0 auto;background:#FFFDFA;border:1px solid #E4DED4;
              border-radius:18px;padding:28px">
    <p style="margin:0 0 18px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
              color:#8A8378">{studio or 'PRISM'}</p>
    <h1 style="margin:0 0 10px;font-size:22px;font-weight:600;letter-spacing:-.02em">
      {who} to {workspace}
    </h1>
    <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:#4A443B">
      You will join as {'an admin' if role == 'admin' else 'a member'}
      {'- you can change anything in this workspace.' if role == 'admin'
       else '- you can prepare quotations and proposals, but not change the studio settings or delete anything.'}
    </p>
    <a href="{link}"
       style="display:inline-block;background:#35655A;color:#FFFDFA;text-decoration:none;
              padding:12px 20px;border-radius:11px;font-size:14px">Join the workspace</a>
    <p style="margin:22px 0 0;font-size:13px;line-height:1.6;color:#6B6459">
      Or paste this into your browser:<br>
      <span style="color:#35655A;word-break:break-all">{link}</span>
    </p>
    <p style="margin:18px 0 0;font-size:12px;color:#8A8378">
      The link works for 14 days. If you were not expecting it, ignore it - nothing happens
      until you sign in and accept.
    </p>
  </div>
</div>"""


def send_invite(
    *, to: str, studio: str, workspace: str, inviter: str, role: str, link: str
) -> None:
    """Send one invitation. Raises `MailError` if Resend would not take it."""
    if not configured():
        raise MailError("No email is configured. Send the link yourself.")

    payload = json.dumps(
        {
            "from": config.RESEND_FROM.strip(),
            "to": [to],
            "subject": f"{inviter or studio or 'PRISM'} invited you to {workspace}",
            "html": _body(studio, workspace, inviter, role, link),
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY.strip()}",
            "Content-Type": "application/json",
            # Named on purpose. Resend sits behind a WAF that answers the
            # default `Python-urllib/3.x` agent with a 403 and no explanation,
            # which reads as a rejected key and is not one.
            "User-Agent": "PRISM/1.0 (+https://github.com/prism)",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}").get("message", "")
        except Exception:  # noqa: BLE001 - the status is the useful part either way
            detail = ""
        raise MailError(detail or f"Resend answered {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise MailError("Resend could not be reached.") from exc

    logger.info("Invitation emailed to %s (%s)", to, answer.get("id", "no id"))
