"""Getting in: PRISM's own sign-in screen, with Zitadel holding the people.

Every way in ends the same way - Zitadel confirms who the person is, and
`auth.issue` hands back PRISM's own access and refresh tokens. What differs is
how they prove it:

  * **A password.** Checked by Zitadel. Only for an address that has been
    proven: Zitadel itself would accept a password set at sign-up by somebody
    who never owned the inbox, so an unproven address is sent a code first.
  * **A code by email.** Zitadel emails it; the person types it here. An
    address PRISM has never seen gets an account on the way, exactly as the
    code sign-in did before.
  * **Making an account, or a new password.** Both are a code plus a
    password: the password is only set once the code has proven the inbox, so
    an account made with somebody else's address never carries a password its
    owner did not choose.
  * **Google or Facebook.** Zitadel talks to the provider; this only asks it
    who came back.

A code is waited for in a *flow*: a random id the browser holds, and
everything else (the Zitadel person, the session Zitadel opened, what to do
once the code is right) held here for `FLOW_SECONDS`. Wrong codes count
against the flow, which ends after `MAX_TRIES` - Zitadel does not limit them.

IN MEMORY, AND THAT IS A DECISION. The API runs as one process (one uvicorn
worker; see `docker/backend.Dockerfile`), so flows and the guess counters live
in this module rather than in a store PRISM does not otherwise have. A restart
drops the sign-ins in progress, and the person asks for a new code. Running
more than one worker would make that wrong - a code sent by one process and
checked by another - and is the point at which these move into SQL.

Answers never say whether an address has an account. Making one, asking for a
code and asking for a new password all reply "sent" either way; a wrong
password and an unknown address get the same sentence.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.features.team.infrastructure import auth, zitadel
from app.shared.infrastructure import config

logger = logging.getLogger("prism.signin")

FLOW_SECONDS = 600
MAX_TRIES = 5

#: (attempts, seconds). Per address is the real guard; per IP is looser
#: because a whole studio may share one address to the internet.
PASSWORD_PER_EMAIL = ((10, 15 * 60), (30, 24 * 3600))
PASSWORD_PER_IP = ((100, 15 * 60),)
CODES_PER_EMAIL = ((5, 15 * 60), (20, 24 * 3600))
CODES_PER_IP = ((50, 15 * 60),)
PROVIDER_PER_IP = ((60, 15 * 60),)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignInError(Exception):
    """A refusal the sign-in screen shows as it is: `message` is for the person,
    `code` is stable for the client to branch on, `status` is the HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _unavailable() -> SignInError:
    return SignInError(503, "SIGN_IN_UNAVAILABLE", "Sign-in is unavailable right now. Try again in a minute.")


def _expired() -> SignInError:
    return SignInError(400, "SIGN_IN_EXPIRED", "This sign-in has expired. Start again for a new code.")


def _wrong_password() -> SignInError:
    return SignInError(400, "WRONG_PASSWORD", "That email and password don’t match.")


def _limited() -> SignInError:
    return SignInError(429, "RATE_LIMITED", "Too many attempts. Wait a few minutes, then try again.")


# --- In-memory state ---------------------------------------------------------


@dataclass
class Flow:
    """A code being waited for."""

    email: str
    #: The Zitadel person, or '' for an address with no account asking for a
    #: new password: the flow exists so the answer looks the same, and no code
    #: ever matches it.
    user_id: str
    #: `code`, `signup`, `reset`, `confirm` (a correct password for an unproven
    #: address) or `provider` (a provider account whose email it did not prove).
    purpose: str
    #: With a sign-in code. Empty when the code proves the address instead.
    session_id: str = ""
    session_token: str = ""
    #: This flow made the person. Any other unproven person may carry a
    #: password a stranger set, so proving their address clears it.
    created_here: bool = False
    #: Set once the code proves the inbox (sign-up; a new password arrives with
    #: the code instead).
    new_password: str = ""
    #: The provider account to link once the inbox is proven.
    link: zitadel.ProviderReturn | None = None
    provider: str = "email"
    expires: float = field(default_factory=lambda: time.monotonic() + FLOW_SECONDS)
    tries: int = 0


_lock = threading.Lock()
_flows: dict[str, Flow] = {}
_hits: dict[str, deque[float]] = {}


def _sweep(now: float) -> None:
    for key in [key for key, flow in _flows.items() if flow.expires <= now]:
        del _flows[key]
    for key in [key for key, hits in _hits.items() if not hits or hits[-1] < now - 24 * 3600]:
        del _hits[key]


def _enforce(key: str, limits: tuple[tuple[int, int], ...]) -> None:
    """Count one attempt against `key`, refusing it when any window is full."""
    now = time.monotonic()
    with _lock:
        _sweep(now)
        hits = _hits.setdefault(key, deque())
        longest = max(seconds for _, seconds in limits)
        while hits and hits[0] < now - longest:
            hits.popleft()
        for allowed, seconds in limits:
            if sum(1 for hit in hits if hit >= now - seconds) >= allowed:
                raise _limited()
        hits.append(now)


def _save(flow: Flow) -> str:
    flow_id = secrets.token_urlsafe(32)
    with _lock:
        _sweep(time.monotonic())
        _flows[flow_id] = flow
    return flow_id


def _load(flow_id: str) -> Flow:
    with _lock:
        flow = _flows.get(flow_id or "")
        if flow is None or flow.expires <= time.monotonic():
            _flows.pop(flow_id or "", None)
            raise _expired()
        return flow


def _finish(flow_id: str) -> None:
    with _lock:
        _flows.pop(flow_id, None)


def _count_try(flow_id: str, flow: Flow) -> int:
    """Tries left after this one, counted before Zitadel sees the code so
    parallel guesses cannot pass the cap."""
    with _lock:
        flow.tries += 1
        used = flow.tries
    if used > MAX_TRIES:
        _finish(flow_id)
        raise SignInError(429, "TOO_MANY_TRIES", "Too many wrong codes. Start again for a new one.")
    return MAX_TRIES - used


# --- Answers -----------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """`done` with tokens, or `code` with the flow the code is waited for in."""

    step: str
    flow: str = ""
    email: str = ""
    purpose: str = ""
    tokens: dict[str, Any] | None = None


def _code_step(flow_id: str, flow: Flow) -> Answer:
    return Answer(step="code", flow=flow_id, email=flow.email, purpose=flow.purpose)


def _done(person: zitadel.Person, provider: str = "email") -> Answer:
    """Tokens for somebody Zitadel has just confirmed - and only with a proven email.

    The email in the token is what membership is decided by, so an unproven one
    is never put in one. Every way in proves the address before it gets here;
    this is the one place that refuses if something did not, including a
    person who changed their address in Zitadel and has not proven the new
    one, whose next refresh would otherwise carry it.
    """
    if not person.email_verified or not person.email:
        raise SignInError(
            401, "SIGNED_OUT", "That account’s email isn’t confirmed. Sign in again with a code to confirm it."
        )
    return Answer(step="done", tokens=auth.issue(person.id, person.email, provider))


# --- Guards ------------------------------------------------------------------


def _ready() -> None:
    if not auth.ready():
        raise SignInError(404, "SIGN_IN_OFF", "This server has no sign-in configured.")


def _address(email: str) -> str:
    address = (email or "").strip().lower()
    if not _EMAIL.match(address) or len(address) > 254:
        raise SignInError(400, "INVALID_EMAIL", "That doesn’t look like an email address.")
    return address


async def _password_ok(password: str) -> None:
    broken = (await zitadel.password_rules()).broken_by(password or "")
    if broken:
        raise SignInError(400, "WEAK_PASSWORD", broken)


async def _find(address: str) -> zitadel.Person | None:
    try:
        return await zitadel.find_by_email(address)
    except zitadel.ZitadelError as exc:
        if exc.code == "ambiguous":
            raise SignInError(
                409,
                "AMBIGUOUS_ACCOUNT",
                "More than one account uses this email, so it can’t sign in here. Ask whoever runs PRISM.",
            ) from exc
        raise _unavailable() from exc


async def _send_proof(flow: Flow, person: zitadel.Person) -> None:
    """Email the code this flow waits for: a sign-in code when the address is
    proven, otherwise one that proves it."""
    if person.email_verified:
        await zitadel.allow_email_codes(person.id)
        flow.session_id, flow.session_token = await zitadel.start_code_session(person.id)
    else:
        flow.session_id = flow.session_token = ""
        await zitadel.send_email_code(person.id)


async def _proof_flow(address: str, purpose: str, *, create: bool, **extra: Any) -> Answer:
    """A flow waiting on a code, with the code sent - or, for an address with no
    account and `create` off, a flow no code will ever match."""
    person = await _find(address)
    try:
        created = False
        if person is None and create:
            # Making the person already emails the code that proves the
            # address. Sending another here would make that one stop working
            # before it arrived.
            person = zitadel.Person(id=await zitadel.create_person(address), email=address, email_verified=False)
            created = True
        flow = Flow(email=address, user_id=person.id if person else "", purpose=purpose, created_here=created, **extra)
        if person is not None and not created:
            await _send_proof(flow, person)
    except zitadel.ZitadelError as exc:
        raise _unavailable() from exc
    return _code_step(_save(flow), flow)


# --- The ways in -------------------------------------------------------------


async def config_payload() -> dict[str, Any]:
    """What the sign-in screen needs before it draws anything."""
    if not auth.required():
        return {"required": False, "ready": False, "providers": [], "password_rules": None}
    if not auth.ready():
        return {"required": True, "ready": False, "providers": [], "password_rules": None}
    providers = await zitadel.active_providers()
    rules = await zitadel.password_rules()
    return {
        "required": True,
        "ready": True,
        "providers": [{"id": p.id, "name": p.name, "kind": p.kind} for p in providers],
        "password_rules": rules.as_dict(),
    }


async def sign_in(email: str, password: str, ip: str) -> Answer:
    """Email and the password Zitadel keeps."""
    _ready()
    address = _address(email)
    for limits, key in ((PASSWORD_PER_EMAIL, f"pw:email:{address}"), (PASSWORD_PER_IP, f"pw:ip:{ip}")):
        _enforce(key, limits)

    person = await _find(address)
    if person is None:
        raise _wrong_password()
    try:
        await zitadel.check_password(person.id, password or "")
    except zitadel.ZitadelError as exc:
        if exc.code == "wrong_password":
            raise _wrong_password() from exc
        raise _unavailable() from exc

    if not person.email_verified:
        # Right password, unproven address: whoever set it has to show the
        # inbox is theirs before it opens anything.
        _enforce(f"code:email:{address}", CODES_PER_EMAIL)
        try:
            flow = Flow(email=address, user_id=person.id, purpose="confirm")
            await _send_proof(flow, person)
        except zitadel.ZitadelError as exc:
            raise _unavailable() from exc
        return _code_step(_save(flow), flow)
    return _done(person)


async def sign_up(email: str, password: str, ip: str) -> Answer:
    """A new account: the code proves the address, then the password is set.

    An address that already has an account gets the same answer and a code;
    the code still proves the inbox, so the password chosen here replaces the
    old one only for whoever owns it.
    """
    _ready()
    address = _address(email)
    await _password_ok(password)
    _enforce(f"code:email:{address}", CODES_PER_EMAIL)
    _enforce(f"code:ip:{ip}", CODES_PER_IP)
    return await _proof_flow(address, "signup", create=True, new_password=password)


async def send_code(email: str, ip: str) -> Answer:
    """A sign-in code by email. An address with no account gets one made."""
    _ready()
    address = _address(email)
    _enforce(f"code:email:{address}", CODES_PER_EMAIL)
    _enforce(f"code:ip:{ip}", CODES_PER_IP)
    return await _proof_flow(address, "code", create=True)


async def start_reset(email: str, ip: str) -> Answer:
    """A code to set a new password with. Nothing is made for an unknown address."""
    _ready()
    address = _address(email)
    _enforce(f"code:email:{address}", CODES_PER_EMAIL)
    _enforce(f"code:ip:{ip}", CODES_PER_IP)
    return await _proof_flow(address, "reset", create=False)


async def resend(flow_id: str, ip: str) -> Answer:
    """Another code for a flow still waiting on one. Earlier codes stop working."""
    _ready()
    flow = _load(flow_id)
    _enforce(f"code:email:{flow.email}", CODES_PER_EMAIL)
    _enforce(f"code:ip:{ip}", CODES_PER_IP)
    if flow.user_id:
        try:
            person = await zitadel.get_person(flow.user_id)
            if person is None:
                raise _expired()
            await _send_proof(flow, person)
        except zitadel.ZitadelError as exc:
            raise _unavailable() from exc
    with _lock:
        flow.tries = 0
        flow.expires = time.monotonic() + FLOW_SECONDS
    return _code_step(flow_id, flow)


async def verify(flow_id: str, code: str, new_password: str = "") -> Answer:
    """The code the person typed. A new password comes with it on `reset`."""
    _ready()
    flow = _load(flow_id)
    if flow.purpose == "reset":
        await _password_ok(new_password)
    left = _count_try(flow_id, flow)
    typed = re.sub(r"\s+", "", code or "")

    try:
        if not flow.user_id or not typed:
            raise zitadel.ZitadelError("wrong_code")
        if flow.session_id:
            if await zitadel.check_session_code(flow.session_id, flow.session_token, typed) != flow.user_id:
                raise zitadel.ZitadelError("wrong_code")
        else:
            await zitadel.verify_email(flow.user_id, typed)
            await zitadel.allow_email_codes(flow.user_id)
            keeps_password = flow.created_here or flow.purpose in ("confirm", "signup", "reset")
            if not keeps_password:
                # Proven just now by somebody who did not choose a password
                # here. Any password already on the account was set before the
                # address was proven, possibly by a stranger: it goes.
                await zitadel.clear_password(flow.user_id)
    except zitadel.ZitadelError as exc:
        if exc.code == "wrong_code":
            tries = "1 try" if left == 1 else f"{left} tries"
            message = f"That code isn’t right. {tries} left." if left else "That code isn’t right."
            raise SignInError(400, "INVALID_CODE", message) from exc
        if exc.code == "code_expired":
            raise SignInError(400, "CODE_EXPIRED", "That code has expired. Send a new one.") from exc
        raise _unavailable() from exc

    # The inbox is proven. Whatever the flow was waiting to do happens now.
    try:
        password = new_password if flow.purpose == "reset" else flow.new_password
        if password:
            await zitadel.set_password(flow.user_id, password)
        if flow.link is not None:
            await zitadel.link_provider(flow.user_id, flow.link)
        person = await zitadel.get_person(flow.user_id)
    except zitadel.ZitadelError as exc:
        if exc.code == "weak_password":
            raise SignInError(400, "WEAK_PASSWORD", "That password isn’t allowed. Choose another.") from exc
        raise _unavailable() from exc
    _finish(flow_id)
    if person is None:
        raise _expired()
    return _done(person, flow.provider)


async def refresh(refresh_token: str) -> Answer:
    """New tokens for a refresh token, while the person still exists in Zitadel.

    Deleting or deactivating somebody in the console ends their PRISM sign-in
    at the next refresh (an hour at most). Zitadel being briefly unreachable
    does not sign everybody out: the claims are trusted until it answers again.
    """
    _ready()
    try:
        claims = auth.verify_refresh(refresh_token)
    except auth.AuthError as exc:
        raise SignInError(401, "SIGNED_OUT", str(exc)) from exc
    user_id, email, provider = str(claims["sub"]), str(claims.get("email", "")), str(claims.get("prv", "email"))
    try:
        person = await zitadel.get_person(user_id)
    except zitadel.ZitadelError:
        logger.warning("Zitadel unreachable on refresh; trusting the refresh token for %s", user_id)
        person = zitadel.Person(id=user_id, email=email, email_verified=True)
    if person is None:
        raise SignInError(401, "SIGNED_OUT", "That account no longer exists. Sign in again.")
    return _done(person, provider)


# --- Google, Facebook --------------------------------------------------------


def _return_urls() -> tuple[str, str]:
    origin = config.APP_ORIGIN.rstrip("/")
    return f"{origin}/?signin=provider", f"{origin}/?signin=provider-failed"


async def provider_start(idp_id: str, ip: str) -> str:
    """Where to send the browser for this provider, when it is switched on."""
    _ready()
    _enforce(f"idp:ip:{ip}", PROVIDER_PER_IP)
    active = {provider.id: provider for provider in await zitadel.active_providers()}
    if idp_id not in active:
        raise SignInError(400, "UNKNOWN_PROVIDER", "That sign-in method isn’t switched on.")
    success, failure = _return_urls()
    try:
        return await zitadel.start_provider(idp_id, success, failure)
    except zitadel.ZitadelError as exc:
        raise _unavailable() from exc


async def provider_finish(intent_id: str, intent_token: str, ip: str) -> Answer:
    """Who came back from Google or Facebook.

    Already linked: signed in. Not linked, and the provider proved the email:
    linked to the account with that email, or a new one made from it. Not
    linked, and the email unproven: the person proves the inbox with a code
    first, and the link is made once they have - otherwise anybody could put
    somebody else's address on a provider account and walk into theirs.
    """
    _ready()
    _enforce(f"idp:ip:{ip}", PROVIDER_PER_IP)
    try:
        found = await zitadel.retrieve_provider(intent_id or "", intent_token or "")
        kind = next((p.kind for p in await zitadel.active_providers() if p.id == found.idp_id), "other")
        user_id = found.user_id
        if not user_id:
            if not found.email:
                raise SignInError(
                    400, "PROVIDER_NO_EMAIL", "That account didn’t share an email address, so it can’t sign in here."
                )
            if not found.email_verified:
                return await _proof_flow(found.email, "provider", create=True, link=found, provider=kind)
            person = await _find(found.email)
            if person is not None:
                await zitadel.link_provider(person.id, found)
                user_id = person.id
            else:
                user_id = await zitadel.create_from_provider(found)
        await zitadel.check_provider_session(user_id, intent_id, intent_token)
        person = await zitadel.get_person(user_id)
    except zitadel.ZitadelError as exc:
        if exc.code == "provider_failed" or exc.code == "not_found":
            raise SignInError(400, "PROVIDER_FAILED", "That sign-in didn’t go through. Try again.") from exc
        raise _unavailable() from exc
    if person is None:
        raise SignInError(400, "PROVIDER_FAILED", "That sign-in didn’t go through. Try again.")
    return _done(person, kind)
