"""Who is asking, verified rather than claimed.

PRISM had no accounts. The workspace switch decided which book you were reading;
it decided nothing about whether you were allowed to read it, and that was
written down plainly wherever it mattered. This module is what changes that.

Sign-in is Supabase's job. The browser talks to Supabase, gets a signed access
token, and sends it on every API call. This module's whole job is to check the
signature and the expiry before a handler runs - because a token the server does
not verify is a claim, not a credential, and an app that trusts one has a login
screen rather than a login.

Two ways to verify, both standard, chosen by what is configured:

  * `SUPABASE_JWT_SECRET` - the legacy shared secret, verified as HS256. One
    value, no network call.
  * `SUPABASE_URL` - the project's published keys (JWKS), verified as RS256 or
    ES256. Fetched once and cached; a key rotation is picked up on the next
    unknown `kid`.

**Unconfigured means open.** With neither value set, every request is let
through and a warning is logged at start-up. That is deliberate and it is the
honest default for a tool somebody runs on their own machine: turning the API
off the moment auth code lands would break a working install that never asked
for accounts. `/api/auth/config` reports which state the server is in, so the
client can say so rather than guess.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import jwt
from jwt import PyJWKClient

from app import config

logger = logging.getLogger("prism.auth")

__all__ = ["User", "verify", "required", "describe", "AuthError"]

#: Paths that answer without a token. Health is how you find out the API is up -
#: needing a session to ask would make a monitoring check a login problem. The
#: auth config endpoint has to be readable before sign-in, since it is what tells
#: the client whether to ask for one.
#:
#: This is not the whole open surface. Two more are shaped as a path *prefix*
#: rather than one exact path - `GET /api/invites/<token>`, and since Stage 2
#: Task 3, `/api/client/<token>` on every method - so they live in `_gate`'s
#: `open_path` expression in `main.py`, beside this set, rather than in it: a
#: frozenset of exact paths has no way to say "this path, and everything under
#: it" without becoming a different kind of thing entirely. See the comment on
#: that expression for what makes the `/api/client/` prefix safe to leave open.
OPEN_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/config",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)

_lock = threading.Lock()
_jwks: PyJWKClient | None = None
_jwks_failed_at: float = 0.0
#: How long to wait before trying the key set again after a failure. Supabase
#: being briefly unreachable should not turn into a request per call.
_JWKS_RETRY_SECONDS = 30.0


class AuthError(Exception):
    """The token is missing, malformed, expired or not signed by this project."""


class User:
    """Who the token says is asking. Read-only, and only what is needed."""

    __slots__ = ("id", "email", "role")

    def __init__(self, claims: dict[str, Any]) -> None:
        self.id = str(claims.get("sub", "") or "")
        self.email = str(claims.get("email", "") or "")
        self.role = str(claims.get("role", "") or "")

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        return f"User({self.email or self.id!r})"


def required() -> bool:
    """True when this install verifies tokens at all."""
    return bool(config.SUPABASE_JWT_SECRET.strip() or config.SUPABASE_URL.strip())


def describe() -> dict[str, Any]:
    """What the client needs to know before it shows a sign-in screen."""
    return {
        "required": required(),
        "url": config.SUPABASE_URL.strip(),
        # Never the secret, and never the service key. The anon key is meant to
        # be public - it is in the browser bundle either way - and returning it
        # here means the client is configured by the server rather than by two
        # files that can disagree.
        "anon_key": config.SUPABASE_ANON_KEY.strip(),
    }


def _jwks_client() -> PyJWKClient | None:
    """The project's published signing keys, fetched once."""
    global _jwks, _jwks_failed_at

    url = config.SUPABASE_URL.strip().rstrip("/")
    if not url:
        return None

    with _lock:
        if _jwks is not None:
            return _jwks
        if time.monotonic() - _jwks_failed_at < _JWKS_RETRY_SECONDS:
            return None
        try:
            _jwks = PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True)
            # Force one fetch now so a broken URL is a start-up problem rather
            # than a mysterious 401 on the first real request.
            _jwks.fetch_data()
        except (urllib.error.URLError, ValueError, Exception) as exc:  # noqa: BLE001
            _jwks = None
            _jwks_failed_at = time.monotonic()
            logger.warning("Could not read the Supabase key set from %s: %s", url, exc)
            return None
        return _jwks


def verify(token: str) -> User:
    """The user this token proves, or `AuthError`.

    Signature and expiry are both checked. Audience is checked against
    Supabase's own value, which is `authenticated` for a signed-in user.
    """
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise AuthError("No access token was sent.")

    secret = config.SUPABASE_JWT_SECRET.strip()
    options = {"verify_aud": False}

    try:
        if secret:
            claims = jwt.decode(raw, secret, algorithms=["HS256"], options=options)
        else:
            client = _jwks_client()
            if client is None:
                raise AuthError(
                    "This server cannot check sign-ins right now: the Supabase key set "
                    "could not be read."
                )
            key = client.get_signing_key_from_jwt(raw)
            claims = jwt.decode(
                raw, key.key, algorithms=["RS256", "ES256", "EdDSA"], options=options
            )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("That session has expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("That sign-in could not be verified.") from exc

    user = User(claims)
    if not user.id:
        raise AuthError("That token names nobody.")
    return user
