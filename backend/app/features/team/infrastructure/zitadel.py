"""The slice of Zitadel's API that PRISM's own sign-in screen needs.

Zitadel keeps the people - their email, their password, whether the address is
proven, which Google or Facebook account is theirs - and sends every email.
PRISM never sees a password hash and never sends a code itself. What it does is
drive the sign-in from its own screen: this module asks Zitadel to check a
password, to email a code, to check the code that was typed, and reports who
that proved. `app.features.team.application.signin` decides what to do with the
answer and issues PRISM's own tokens.

It is called as PRISM's service account (`ZITADEL_TOKEN`, a personal access
token for a machine user with the instance role `IAM_LOGIN_CLIENT`). That role
can start a sign-in for anybody on the instance, which is why the token is kept
as secret as a database password and never leaves this process.

THREE THINGS ZITADEL DOES NOT DO, each of which the caller has to:

  * It does not limit wrong guesses, of a password or of a code. The sign-in
    service counts them.
  * It accepts a password for an address that was never proven. A password set
    at sign-up by somebody who does not own the inbox would open the account,
    so password sign-in checks `email_verified` before it checks the password.
  * It does not scope a lookup to an organization unless asked. PRISM's people
    live in their own organization (`ZITADEL_ORG_ID`); every search and every
    new person names it, so an instance shared with other apps never answers
    with somebody else's user.

Codes are always delivered by email (`sendCode`). Zitadel can also hand a code
back to the caller (`returnCode`), which would let this server skip the inbox;
nothing here ever asks for that.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.shared.infrastructure import config

logger = logging.getLogger("prism.zitadel")
# httpx logs every request at INFO, user ids in the path included. The calls
# that fail are logged here, with the reason; the ones that work are not news.
logging.getLogger("httpx").setLevel(logging.WARNING)

#: Checks swap in an `httpx.MockTransport`.
_transport: httpx.AsyncBaseTransport | None = None

# gRPC status codes, as they appear in Zitadel's JSON errors.
_INVALID_ARGUMENT, _NOT_FOUND, _ALREADY_EXISTS, _FAILED_PRECONDITION = 3, 5, 6, 9
_PERMISSION_DENIED, _UNAUTHENTICATED = 7, 16


class ZitadelError(Exception):
    """Zitadel refused, or could not be reached.

    `code` is one of a fixed set of reasons - `unavailable`, `not_found`,
    `exists`, `ambiguous`, or whatever the caller named for a refusal of what
    the person typed (`wrong_code`, `wrong_password`, `weak_password`, ...).
    Zitadel's own sentence is logged, never shown: it is written for whoever
    runs the instance, and some of it names internals.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Person:
    id: str
    email: str
    email_verified: bool


@dataclass(frozen=True)
class Provider:
    """A way in that is not a password, as switched on in Zitadel.

    `kind` is PRISM's short name for the provider type (`google`, `facebook`,
    or `other`), which is what decides the button's mark.
    """

    id: str
    name: str
    kind: str


@dataclass(frozen=True)
class ProviderReturn:
    """What Google or Facebook said about the person, via Zitadel.

    `user_id` is the Zitadel person already linked to that account, or ''.
    `new_person` is the request Zitadel prepared for making one from it, with
    the provider link and the email (and whether the provider proved it).
    """

    idp_id: str
    external_id: str
    external_name: str
    email: str
    email_verified: bool
    user_id: str = ""
    new_person: dict[str, Any] = field(default_factory=dict)


def configured() -> bool:
    return bool(config.ZITADEL_URL.strip() and config.ZITADEL_TOKEN.strip())


def _org() -> str:
    return config.ZITADEL_ORG_ID.strip()


def _client() -> httpx.AsyncClient:
    public = config.ZITADEL_URL.strip().rstrip("/")
    internal = config.ZITADEL_INTERNAL_URL.strip().rstrip("/")
    headers = {"Authorization": f"Bearer {config.ZITADEL_TOKEN.strip()}"}
    if internal:
        # Reached by another address, Zitadel still decides which instance is
        # being asked by the public host name.
        headers["Host"] = urlsplit(public).netloc
    return httpx.AsyncClient(
        base_url=internal or public, headers=headers, transport=_transport, timeout=10
    )


async def _call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    invalid: str = "unavailable",
    spent: str = "unavailable",
) -> dict[str, Any]:
    """One request. A refusal of what the person typed maps to `invalid`
    (Zitadel's invalid argument: a wrong code or password, a password too
    weak) or `spent` (failed precondition: a used or expired code)."""
    if not configured():
        raise ZitadelError("unavailable", "Zitadel is not configured.")
    try:
        async with _client() as client:
            response = await client.request(method, path, json=body)
    except httpx.HTTPError as exc:
        logger.warning("Zitadel %s %s failed: %s", method, path, exc)
        raise ZitadelError("unavailable", str(exc)) from exc
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.is_success:
        return data if isinstance(data, dict) else {}

    status = data.get("code") if isinstance(data, dict) else None
    detail = str(data.get("message", "")) if isinstance(data, dict) else ""
    if status in (_PERMISSION_DENIED, _UNAUTHENTICATED) or response.status_code in (401, 403):
        # The service account itself was refused - a revoked token or a lost
        # role. Nothing the person typed can fix that, so it is "unavailable"
        # to them and loud in the log for whoever runs the server.
        logger.error("Zitadel refused PRISM's service account on %s %s: %s", method, path, detail)
        raise ZitadelError("unavailable", detail)
    if response.status_code == 404 or status == _NOT_FOUND:
        raise ZitadelError("not_found", detail)
    if status == _INVALID_ARGUMENT:
        raise ZitadelError(invalid, detail)
    if status == _FAILED_PRECONDITION:
        raise ZitadelError(spent, detail)
    if status == _ALREADY_EXISTS:
        raise ZitadelError("exists", detail)
    logger.warning("Zitadel %s %s answered %s: %s", method, path, response.status_code, detail)
    raise ZitadelError("unavailable", detail)


def _url_template() -> str:
    """Where the button in Zitadel's emails goes.

    PRISM's own sign-in screen, where the code is typed. Left to its default,
    the button opens Zitadel's hosted pages, which can spend the code there and
    leave the screen that asked for it holding a code that no longer works.
    """
    return config.APP_ORIGIN.rstrip("/") + "/#/"


def _person(user: dict[str, Any]) -> Person | None:
    human = user.get("human")
    if not isinstance(human, dict) or user.get("state") in ("USER_STATE_INACTIVE", "USER_STATE_LOCKED"):
        return None
    email = human.get("email") or {}
    return Person(
        id=str(user.get("userId", "")),
        email=str(email.get("email", "")).strip().lower(),
        email_verified=email.get("isVerified") is True,
    )


# --- People ------------------------------------------------------------------


async def find_by_email(email: str) -> Person | None:
    """The person in PRISM's organization with this email, or None.

    Zitadel lets several people share one address. Then only the one whose
    username is the address counts, because that is the one PRISM made; any
    other is ambiguous and refused rather than guessed at.
    """
    address = email.strip().lower()
    queries: list[dict[str, Any]] = [
        {"emailQuery": {"emailAddress": address, "method": "TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE"}},
        {"typeQuery": {"type": "TYPE_HUMAN"}},
    ]
    if _org():
        queries.append({"organizationIdQuery": {"organizationId": _org()}})
    data = await _call("POST", "/v2/users", {"query": {"limit": 10}, "queries": queries})
    results = [user for user in data.get("result", []) if isinstance(user, dict)]
    people = [person for person in map(_person, results) if person]
    if len(people) > 1:
        named = {
            str(user.get("userId", ""))
            for user in results
            if str(user.get("username", "")).lower() == address
        }
        people = [person for person in people if person.id in named]
        if len(people) != 1:
            raise ZitadelError("ambiguous")
    return people[0] if people else None


async def get_person(user_id: str) -> Person | None:
    """The person with this id, when they still exist and are not deactivated."""
    try:
        data = await _call("GET", f"/v2/users/{user_id}")
    except ZitadelError as exc:
        if exc.code == "not_found":
            return None
        raise
    user = data.get("user") or {}
    if _org() and (user.get("details") or {}).get("resourceOwner") not in ("", None, _org()):
        return None
    return _person(user)


def _names(email: str) -> dict[str, str]:
    # Zitadel requires both names and PRISM asks for neither: an account here is
    # an email and a way to prove it. The part before the @ is what the console
    # will show; nothing in PRISM reads these back.
    local = email.split("@", 1)[0] or email
    return {"givenName": local[:200], "familyName": "-"}


async def create_person(email: str) -> str:
    """A new person in PRISM's organization, unproven and without a password.

    Zitadel emails them a code that proves the address. A password is only ever
    set after that (`set_password`), so an account somebody made with another
    person's address cannot be opened with the password they chose.
    """
    address = email.strip().lower()
    body: dict[str, Any] = {
        "username": address,
        "profile": _names(address),
        "email": {"email": address, "sendCode": {"urlTemplate": _url_template()}},
    }
    if _org():
        body["organization"] = {"orgId": _org()}
    data = await _call("POST", "/v2/users/human", body)
    return str(data["userId"])


async def send_email_code(user_id: str) -> None:
    """A fresh code that proves the address (any earlier one stops working)."""
    await _call("POST", f"/v2/users/{user_id}/email/send", {"sendCode": {"urlTemplate": _url_template()}})


async def verify_email(user_id: str, code: str) -> None:
    await _call(
        "POST",
        f"/v2/users/{user_id}/email/verify",
        {"verificationCode": code},
        invalid="wrong_code",
        spent="code_expired",
    )


async def allow_email_codes(user_id: str) -> None:
    """Lets later sign-ins use a code by email. Zitadel allows it only once the
    address is proven."""
    try:
        await _call("POST", f"/v2/users/{user_id}/otp_email", {})
    except ZitadelError as exc:
        if exc.code != "exists":
            raise


# --- Sessions: proving it is them --------------------------------------------


#: Session deletes still in flight, held so the event loop does not drop them.
_dropping: set[asyncio.Task] = set()


def _drop_session(session_id: str, session_token: str) -> None:
    """Delete a spent Zitadel session without making the person wait for it."""

    async def drop() -> None:
        try:
            await _call("DELETE", f"/v2/sessions/{session_id}", {"sessionToken": session_token})
        except ZitadelError:
            pass

    task = asyncio.get_running_loop().create_task(drop())
    _dropping.add(task)
    task.add_done_callback(_dropping.discard)


async def _verified_factors(session_id: str, session_token: str) -> dict[str, Any]:
    """The factors Zitadel says a session proved, after which it is deleted.

    PRISM issues its own tokens, so a Zitadel session is only ever a question
    asked once. The delete happens in the background - the answer is already
    in hand - and a leftover session expires by itself if it fails.
    """
    factors = (await _call("GET", f"/v2/sessions/{session_id}")).get("session", {}).get("factors", {})
    _drop_session(session_id, session_token)
    return factors if isinstance(factors, dict) else {}


async def start_code_session(user_id: str) -> tuple[str, str]:
    """A sign-in session for this person; Zitadel emails them a one-time code."""
    data = await _call(
        "POST",
        "/v2/sessions",
        {
            "checks": {"user": {"userId": user_id}},
            "challenges": {"otpEmail": {"sendCode": {"urlTemplate": _url_template()}}},
        },
    )
    return str(data["sessionId"]), str(data["sessionToken"])


async def check_session_code(session_id: str, session_token: str, code: str) -> str:
    """Checks the emailed code, then asks Zitadel whose session it proved.
    Returns that user id."""
    data = await _call(
        "PATCH",
        f"/v2/sessions/{session_id}",
        {"sessionToken": session_token, "checks": {"otpEmail": {"code": code}}},
        invalid="wrong_code",
        spent="code_expired",
    )
    factors = await _verified_factors(session_id, str(data.get("sessionToken") or session_token))
    if not (factors.get("otpEmail") or {}).get("verifiedAt"):
        raise ZitadelError("wrong_code")
    return str((factors.get("user") or {}).get("id", ""))


async def check_password(user_id: str, password: str) -> None:
    """Passes when this is the person's password, as confirmed by the session
    Zitadel verified with it."""
    data = await _call(
        "POST",
        "/v2/sessions",
        {"checks": {"user": {"userId": user_id}, "password": {"password": password}}},
        invalid="wrong_password",
        spent="wrong_password",  # wrong, or no password set at all
    )
    factors = await _verified_factors(str(data["sessionId"]), str(data["sessionToken"]))
    if (factors.get("user") or {}).get("id") != user_id or not (factors.get("password") or {}).get("verifiedAt"):
        raise ZitadelError("wrong_password")


# --- Passwords ---------------------------------------------------------------


@dataclass(frozen=True)
class PasswordRules:
    min_length: int = 8
    upper: bool = True
    lower: bool = True
    number: bool = True
    symbol: bool = True

    def broken_by(self, password: str) -> str | None:
        """What the password is missing, as one sentence, or None when it passes."""
        missing = [
            what
            for what, needed, ok in (
                ("an uppercase letter", self.upper, any(c.isupper() for c in password)),
                ("a lowercase letter", self.lower, any(c.islower() for c in password)),
                ("a number", self.number, any(c.isdigit() for c in password)),
                ("a symbol", self.symbol, any(not c.isalnum() for c in password)),
            )
            if needed and not ok
        ]
        if len(password) >= self.min_length and not missing:
            return None
        parts = ([f"at least {self.min_length} characters"] if len(password) < self.min_length else []) + missing
        joined = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
        return f"The password needs {joined}."

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_length": self.min_length,
            "upper": self.upper,
            "lower": self.lower,
            "number": self.number,
            "symbol": self.symbol,
        }


_lock = threading.Lock()
_rules: tuple[float, PasswordRules] | None = None
_providers: tuple[float, list[Provider]] | None = None
#: How long Zitadel's settings are trusted before being asked again. A provider
#: switched on in the console shows its button within this long.
_SETTINGS_SECONDS = 60.0


def _ctx() -> str:
    return f"ctx.orgId={_org()}" if _org() else "ctx.instance=true"


async def password_rules() -> PasswordRules:
    """Zitadel's password complexity settings (cached briefly; its defaults when
    it cannot be asked)."""
    global _rules
    with _lock:
        if _rules and time.monotonic() - _rules[0] < _SETTINGS_SECONDS:
            return _rules[1]
    try:
        found = (await _call("GET", f"/v2/settings/password/complexity?{_ctx()}")).get("settings", {})
        rules = PasswordRules(
            min_length=int(found.get("minLength", 8)),
            upper=found.get("requiresUppercase") is True,
            lower=found.get("requiresLowercase") is True,
            number=found.get("requiresNumber") is True,
            symbol=found.get("requiresSymbol") is True,
        )
    except ZitadelError:
        return PasswordRules()
    with _lock:
        _rules = (time.monotonic(), rules)
    return rules


async def has_password(user_id: str) -> bool:
    data = await _call("GET", f"/v2/users/{user_id}/authentication_methods")
    return "AUTHENTICATION_METHOD_TYPE_PASSWORD" in data.get("authMethodTypes", [])


async def set_password(user_id: str, password: str) -> None:
    """Replaces the person's password. Zitadel lets this account do it without
    the old one, so callers must have proved it is them first (an emailed code)."""
    await _call(
        "POST",
        f"/v2/users/{user_id}/password",
        {"newPassword": {"password": password, "changeRequired": False}},
        invalid="weak_password",
    )


async def clear_password(user_id: str) -> None:
    """Makes any password on the person useless: one set before their address
    was proven may be a stranger's."""
    if await has_password(user_id):
        await set_password(user_id, secrets.token_urlsafe(24) + "Aa1!")


# --- Google, Facebook --------------------------------------------------------

_KINDS = {
    "IDENTITY_PROVIDER_TYPE_GOOGLE": "google",
    "IDENTITY_PROVIDER_TYPE_FACEBOOK": "facebook",
}


async def active_providers() -> list[Provider]:
    """The providers switched on for PRISM's organization, cached briefly.

    This is what decides whether a Google or Facebook button exists at all: a
    provider with no credentials in Zitadel is not active, so it is not here,
    so the sign-in screen does not draw it. Unreachable means none - a button
    that cannot work is worse than no button.
    """
    global _providers
    with _lock:
        if _providers and time.monotonic() - _providers[0] < _SETTINGS_SECONDS:
            return list(_providers[1])
    try:
        data = await _call("GET", f"/v2/settings/login/idps?{_ctx()}")
    except ZitadelError:
        return []
    found = [
        Provider(
            id=str(item.get("id", "")),
            name=str(item.get("name", "") or ""),
            kind=_KINDS.get(str(item.get("type", "")), "other"),
        )
        for item in data.get("identityProviders", [])
        if isinstance(item, dict) and item.get("id")
    ]
    with _lock:
        _providers = (time.monotonic(), found)
    return list(found)


async def start_provider(idp_id: str, success_url: str, failure_url: str) -> str:
    """Where to send the browser so the person can sign in with this provider.

    Zitadel sends them back to `success_url` with `id` and `token` added to the
    query string (and `user` when the account is already linked), or to
    `failure_url` when the provider said no.
    """
    data = await _call(
        "POST",
        "/v2/idp_intents",
        {"idpId": idp_id, "urls": {"successUrl": success_url, "failureUrl": failure_url}},
        invalid="unknown_provider",
    )
    url = str(data.get("authUrl", "") or "")
    if not url:
        raise ZitadelError("unavailable", "Zitadel returned no provider URL.")
    return url


async def retrieve_provider(intent_id: str, intent_token: str) -> ProviderReturn:
    """What the provider said, for a return that carried this id and token."""
    data = await _call(
        "POST",
        f"/v2/idp_intents/{intent_id}",
        {"idpIntentToken": intent_token},
        invalid="provider_failed",
        spent="provider_failed",
    )
    info = data.get("idpInformation") or {}
    new_person = data.get("addHumanUser") or {}
    email = (new_person.get("email") or {}) if isinstance(new_person, dict) else {}
    return ProviderReturn(
        idp_id=str(info.get("idpId", "")),
        external_id=str(info.get("userId", "")),
        external_name=str(info.get("userName", "") or ""),
        email=str(email.get("email", "")).strip().lower(),
        email_verified=email.get("isVerified") is True,
        user_id=str(data.get("userId", "") or ""),
        new_person=new_person if isinstance(new_person, dict) else {},
    )


async def link_provider(user_id: str, found: ProviderReturn) -> None:
    try:
        await _call(
            "POST",
            f"/v2/users/{user_id}/links",
            {
                "idpLink": {
                    "idpId": found.idp_id,
                    "userId": found.external_id,
                    "userName": found.external_name or found.email,
                }
            },
        )
    except ZitadelError as exc:
        if exc.code != "exists":
            raise


async def create_from_provider(found: ProviderReturn) -> str:
    """A new person made from what the provider said, already linked to it."""
    body = dict(found.new_person)
    body["username"] = found.email
    if not (body.get("profile") or {}).get("givenName") or not (body.get("profile") or {}).get("familyName"):
        body["profile"] = {**_names(found.email), **{k: v for k, v in (body.get("profile") or {}).items() if v}}
    body["email"] = {"email": found.email, "isVerified": True}
    body.setdefault(
        "idpLinks",
        [{"idpId": found.idp_id, "userId": found.external_id, "userName": found.external_name or found.email}],
    )
    if _org():
        body["organization"] = {"orgId": _org()}
    data = await _call("POST", "/v2/users/human", body)
    return str(data["userId"])


async def check_provider_session(user_id: str, intent_id: str, intent_token: str) -> None:
    """Passes when Zitadel confirms the provider sign-in proved this person."""
    data = await _call(
        "POST",
        "/v2/sessions",
        {
            "checks": {
                "user": {"userId": user_id},
                "idpIntent": {"idpIntentId": intent_id, "idpIntentToken": intent_token},
            }
        },
        invalid="provider_failed",
        spent="provider_failed",
    )
    factors = await _verified_factors(str(data["sessionId"]), str(data["sessionToken"]))
    if (factors.get("user") or {}).get("id") != user_id or not (factors.get("intent") or {}).get("verifiedAt"):
        raise ZitadelError("provider_failed")
