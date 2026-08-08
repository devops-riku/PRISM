"""The two emails PRISM sends: the invitation, and a quotation to a client.

Resend, over its HTTP API, with `urllib` rather than a new dependency - one POST
with a JSON body does not need a client library.

**Nothing here is ever sent by PRISM on its own.** That rule used to read
"nothing else in PRISM sends mail, and nothing else should", written when the
invitation was the only message and worth keeping in spirit now that it is not.
What it was protecting against is an app that quietly emails people - a
background job, a state transition, a scheduler, anything that puts a message in
front of a stranger without a person having decided to. Neither function below
can be reached that way: `send_invite` runs when somebody fills in an invitation,
and `send_quotation` runs when a studio has opened a compose window, read the
exact words that will be sent, and pressed Send. If a third caller ever appears
that does not have a human at the other end of it, that is the line being
crossed, not the count going from two to three.

Unconfigured is not a failure. With no key the invitation is still created and
the API answers with its link, and a quotation still advances to `sent` with the
studio delivering the link themselves. That is the honest fallback: the record is
the thing, and the email is only how it travels.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request

from app.shared.infrastructure import config

logger = logging.getLogger("prism.mail")

__all__ = ["configured", "send_invite", "send_quotation", "MailError"]

ENDPOINT = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 12


class MailError(RuntimeError):
    """Resend refused the message, or could not be reached."""


def configured() -> bool:
    return bool(config.RESEND_API_KEY.strip() and config.RESEND_FROM.strip())


def _body(studio: str, workspace: str, inviter: str, role: str, link: str) -> str:
    """The message, in the app's own voice: what, from whom, and one link.

    THE COLOURS BELOW ARE THE DOCUMENT'S, NOT THE APP'S, and that is the whole
    rule for this file. An email is read in someone else's client, on a white
    background, next to other mail - the same conditions a PRISM quotation is
    read in and nothing like the studio's dark screen. So it takes the printed
    document's brand and accent (the document domain's `PALETTE`), which is the
    other thing this recipient gets from this studio.

    A sixth hand-maintained copy of a palette, and it is worth saying why it
    cannot simply import one: an email client strips `<style>` and does not
    resolve custom properties, so every value has to be an inline literal.
    This file was pine (`#35655A`) and warm brown (`#1D1B17`) for two re-skins
    because nothing pointed at it. If `PALETTE` moves, move these by hand.
    """
    who = f"{inviter} invited you" if inviter else "You have been invited"
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            color:#343148;background:#F4F4F5;padding:32px">
  <div style="max-width:520px;margin:0 auto;background:#FFFFFF;border:1px solid #DCDCE0;
              border-radius:18px;padding:28px">
    <p style="margin:0 0 18px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
              color:#6E6E78">{studio or 'PRISM'}</p>
    <h1 style="margin:0 0 10px;font-size:22px;font-weight:600;letter-spacing:-.02em">
      {who} to {workspace}
    </h1>
    <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:#35353C">
      You will join as {'an admin' if role == 'admin' else 'a member'}
      {'- you can change anything in this workspace.' if role == 'admin'
       else '- you can prepare quotations and proposals, but not change the studio settings or delete anything.'}
    </p>
    <a href="{link}"
       style="display:inline-block;background:#6D57E8;color:#FFFFFF;text-decoration:none;
              padding:12px 20px;border-radius:11px;font-size:14px">Join the workspace</a>
    <p style="margin:22px 0 0;font-size:13px;line-height:1.6;color:#56565F">
      Or paste this into your browser:<br>
      <span style="color:#6D57E8;word-break:break-all">{link}</span>
    </p>
    <p style="margin:18px 0 0;font-size:12px;color:#6E6E78">
      The link works for 14 days. If you were not expecting it, ignore it - nothing happens
      until you sign in and accept.
    </p>
  </div>
</div>"""


def _post(*, to: str, subject: str, body: str) -> str:
    """One message to Resend. Returns its id, or raises `MailError`.

    Extracted when the quotation email arrived and needed every line of it -
    the WAF-shaped `User-Agent` in particular is a fact about Resend that was
    learned once and must not have to be learned twice.
    """
    payload = json.dumps(
        {
            "from": config.RESEND_FROM.strip(),
            "to": [to],
            "subject": subject,
            "html": body,
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

    return str(answer.get("id", "") or "no id")


def send_invite(
    *, to: str, studio: str, workspace: str, inviter: str, role: str, link: str
) -> None:
    """Send one invitation. Raises `MailError` if Resend would not take it."""
    if not configured():
        raise MailError("No email is configured. Send the link yourself.")

    sent = _post(
        to=to,
        subject=f"{inviter or studio or 'PRISM'} invited you to {workspace}",
        body=_body(studio, workspace, inviter, role, link),
    )
    logger.info("Invitation emailed to %s (%s)", to, sent)


def _quotation_body(studio: str, message: str) -> str:
    """The studio's own words, escaped, in the document's colours.

    ESCAPED, unlike `_body` above, and the difference is not an inconsistency.
    Everything `_body` interpolates is a studio's own name or a role this file
    chose; `message` is free text a studio typed into a compose window seconds
    ago. Dropping that into HTML unescaped means an `&` in "Ridge & Co" silently
    breaks the markup at best, and at worst lets whatever is pasted in from
    somewhere else carry its own tags into a stranger's inbox.

    Newlines become `<br>` because the studio wrote the message in a textarea
    and pressed Enter expecting it to mean something. HTML would otherwise
    collapse every one of them and deliver a single paragraph nobody wrote.

    No button, no call to action, no "view your quotation" chrome. The link
    lives inside the message where the studio put it, because this email is
    theirs and the whole point of showing them the words first is that what
    they read is what is sent.
    """
    safe = html.escape(message).replace("\r\n", "\n").replace("\n", "<br>")
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            color:#343148;background:#F4F4F5;padding:32px">
  <div style="max-width:520px;margin:0 auto;background:#FFFFFF;border:1px solid #DCDCE0;
              border-radius:18px;padding:28px">
    <p style="margin:0 0 18px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
              color:#6E6E78">{html.escape(studio or 'PRISM')}</p>
    <div style="margin:0;font-size:15px;line-height:1.65;color:#35353C">{safe}</div>
  </div>
</div>"""


def send_quotation(*, to: str, subject: str, message: str, studio: str) -> None:
    """Send one quotation to one client. Raises `MailError` if Resend refuses.

    `subject` and `message` are the studio's, verbatim - this function adds a
    wrapper and a studio name and decides nothing else. A caller that wants
    different words changes the words.
    """
    if not configured():
        raise MailError("No email is configured. Send the link yourself.")

    sent = _post(to=to, subject=subject, body=_quotation_body(studio, message))
    logger.info("Quotation emailed to %s (%s)", to, sent)
