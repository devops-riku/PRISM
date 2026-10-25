"""Who is asking, verified rather than claimed.

PRISM had no accounts. The workspace switch decided which book you were reading;
it decided nothing about whether you were allowed to read it, and that was
written down plainly wherever it mattered. This module is what changes that.

Zitadel keeps the people and confirms who somebody is (see `zitadel.py` and
`app.features.team.application.signin`). Once it has, this module issues
PRISM's own tokens, and on every API call it checks one: the signature and the
expiry, before a handler runs - because a token the server does not verify is a
claim, not a credential, and an app that trusts one has a login screen rather
than a login.

Two tokens, both HS256 with `AUTH_TOKEN_SECRET`:

  * **access** - an hour. Sent on every call; checked here without a network
    call.
  * **refresh** - thirty days. Traded at `/api/auth/refresh` for a new pair,
    and only while Zitadel still knows the person, so deleting or deactivating
    somebody in the console ends their sign-in within the hour.

**Unconfigured means open.** With none of `ZITADEL_URL`, `ZITADEL_TOKEN` and
`AUTH_TOKEN_SECRET` set, every request is let through and a warning is logged
at start-up. That is deliberate and it is the honest default for a tool
somebody runs on their own machine: turning the API off the moment auth code
lands would break a working install that never asked for accounts.
`/api/auth/config` reports which state the server is in, so the client can say
so rather than guess.

**Half-configured means closed.** Any one of the three set turns verification
on. A server with a Zitadel URL and no token secret refuses every call and
says which setting is missing, rather than quietly answering everybody.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import jwt

from app.shared.infrastructure import config

__all__ = ["User", "verify", "verify_refresh", "issue", "required", "ready", "missing", "AuthError"]

#: Paths that answer without a token. Health is how you find out the API is up -
#: needing a session to ask would make a monitoring check a login problem. The
#: auth config endpoint has to be readable before sign-in, since it is what tells
#: the client whether to ask for one, and the sign-in endpoints are how somebody
#: without a token gets one.
#:
#: This is not the whole open surface. Two more are shaped as a path *prefix*
#: rather than one exact path - `GET /api/invites/<token>`, and since Stage 2
#: Task 3, `/api/client/<token>` on every method - so they live in `_gate`'s
#: `open_path` expression in `app/presentation/api/middleware.py`, beside this set, rather
#: than in it: a
#: frozenset of exact paths has no way to say "this path, and everything under
#: it" without becoming a different kind of thing entirely. See the comment on
#: that expression for what makes the `/api/client/` prefix safe to leave open.
OPEN_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/config",
        "/api/auth/sign-in",
        "/api/auth/sign-up",
        "/api/auth/code",
        "/api/auth/code/resend",
        "/api/auth/code/verify",
        "/api/auth/reset",
        "/api/auth/refresh",
        "/api/auth/provider",
        "/api/auth/provider/finish",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)

ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 24 * 3600
_ISSUER = "prism"
_ALGORITHM = "HS256"


class AuthError(Exception):
    """The token is missing, malformed, expired or not signed by this server."""


class User:
    """Who the token says is asking. Read-only, and only what is needed.

    `id` is the person's Zitadel id; `provider` is how they signed in
    (`email`, `google`, `facebook`).
    """

    __slots__ = ("id", "email", "provider")

    def __init__(self, claims: dict[str, Any]) -> None:
        self.id = str(claims.get("sub", "") or "")
        self.email = str(claims.get("email", "") or "").lower()
        self.provider = str(claims.get("prv", "") or "email")

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        return f"User({self.email or self.id!r})"


def _settings() -> dict[str, str]:
    return {
        "ZITADEL_URL": config.ZITADEL_URL.strip(),
        "ZITADEL_TOKEN": config.ZITADEL_TOKEN.strip(),
        "AUTH_TOKEN_SECRET": config.AUTH_TOKEN_SECRET.strip(),
    }


def required() -> bool:
    """True when this install verifies tokens at all - any sign-in setting is set."""
    return any(_settings().values())


def missing() -> list[str]:
    """The sign-in settings still empty, once any of them is set."""
    found = _settings()
    return [name for name, value in found.items() if not value] if any(found.values()) else []


def ready() -> bool:
    """True when sign-in works end to end: required, and nothing missing."""
    return required() and not missing()


def _secret() -> str:
    secret = config.AUTH_TOKEN_SECRET.strip()
    if not secret:
        raise AuthError(
            "Sign-in is misconfigured on this server: AUTH_TOKEN_SECRET is not set."
        )
    return secret


def _encode(user_id: str, email: str, provider: str, kind: str, seconds: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": _ISSUER,
            "aud": _ISSUER,
            "sub": user_id,
            "email": (email or "").lower(),
            "prv": provider or "email",
            "typ": kind,
            "iat": now,
            "exp": now + seconds,
            "jti": secrets.token_urlsafe(12),
        },
        _secret(),
        algorithm=_ALGORITHM,
    )


def issue(user_id: str, email: str, provider: str = "email") -> dict[str, Any]:
    """A fresh access and refresh token for somebody Zitadel has just confirmed."""
    return {
        "access_token": _encode(user_id, email, provider, "access", ACCESS_SECONDS),
        "refresh_token": _encode(user_id, email, provider, "refresh", REFRESH_SECONDS),
        "token_type": "bearer",
        "expires_in": ACCESS_SECONDS,
        "user": {"id": user_id, "email": (email or "").lower(), "provider": provider or "email"},
    }


def _decode(token: str, kind: str) -> dict[str, Any]:
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise AuthError("No access token was sent.")
    try:
        claims = jwt.decode(
            raw,
            _secret(),
            algorithms=[_ALGORITHM],
            audience=_ISSUER,
            issuer=_ISSUER,
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("That session has expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("That sign-in could not be verified.") from exc
    if claims.get("typ") != kind:
        # A refresh token sent as an access token, or the reverse. Both are
        # signed by this server; only one of them is for this.
        raise AuthError("That sign-in could not be verified.")
    if not str(claims.get("sub", "") or ""):
        raise AuthError("That token names nobody.")
    return claims


def verify(token: str) -> User:
    """The user this access token proves, or `AuthError`."""
    if missing():
        raise AuthError(
            "Sign-in is misconfigured on this server: "
            + ", ".join(missing())
            + " not set."
        )
    return User(_decode(token, "access"))


def verify_refresh(token: str) -> dict[str, Any]:
    """The claims of a refresh token, or `AuthError`."""
    return _decode(token, "refresh")
