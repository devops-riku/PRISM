"""Runtime configuration for the PRISM backend.

Everything is resolved from `__file__`, never from the current working
directory: uvicorn can be launched from the repo root, from `backend/`, or from
a shell whose cwd was reset between commands, and all three must find the same
`.env` and the same `generated/` folder.

A missing `GEMINI_API_KEY` is *not* an import-time error. Importing this module
always succeeds; the absence of a key surfaces later as a clean 503 raised by
`app.gemini_service`. That is what lets `/api/health` answer honestly instead of
the whole process failing to boot.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --- Paths -------------------------------------------------------------------

APP_DIR: Path = Path(__file__).resolve().parent          # backend/app
BACKEND_DIR: Path = APP_DIR.parent                       # backend
PROJECT_ROOT: Path = BACKEND_DIR.parent                  # repo root
ENV_FILE: Path = BACKEND_DIR / ".env"

# `load_dotenv` is a no-op when the file is absent, and by default it does not
# clobber variables already present in the real environment - so a value
# exported in the shell still wins over the file.
ENV_FILE_LOADED: bool = load_dotenv(dotenv_path=ENV_FILE, override=False)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().strip('"').strip("'")
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


# --- Gemini ------------------------------------------------------------------

#: Server-side only. The browser never sees this value; it is not exposed by any
#: endpoint (``/api/health`` reports a boolean, never the key itself).
GEMINI_API_KEY: str = _env_str("GEMINI_API_KEY", "")

#: Swappable so the model can be changed without a code edit. Verified ids:
#: gemini-3.5-flash, gemini-3.1-pro-preview, gemini-2.5-pro, gemini-2.5-flash,
#: gemini-pro-latest, gemini-flash-latest.
GEMINI_MODEL: str = _env_str("GEMINI_MODEL", "gemini-3.5-flash")

#: Sampling temperature for the single generation call. Low enough that pricing
#: is stable across runs, high enough that prose does not read as boilerplate.
GEMINI_TEMPERATURE: float = 0.4

#: Seconds to wait before the single retry of a transient Gemini failure.
GEMINI_RETRY_DELAY_SECONDS: float = 2.0


def key_configured() -> bool:
    """True when a usable API key is present. Read live so `/api/health` is honest."""
    return bool(GEMINI_API_KEY.strip())


#: The message every 503 shows. Names the file and the next action, does not apologise.
MISSING_KEY_MESSAGE: str = (
    "Gemini key not configured. Add GEMINI_API_KEY to backend/.env and restart the API."
)


# --- Accounts ----------------------------------------------------------------

#: Sign-in is Supabase's. The browser gets a token from them; this server checks
#: it. All three are optional: with none of them set the API answers everyone,
#: which is what PRISM did before accounts existed and is still the right
#: default for a tool running on one machine. See app/auth.py.
SUPABASE_URL: str = _env_str("SUPABASE_URL", "")

#: The publishable key. It is meant to be public - it ships in the browser
#: bundle - and is served to the client so one file configures both halves.
SUPABASE_ANON_KEY: str = _env_str("SUPABASE_ANON_KEY", "")

#: The legacy shared secret, if the project still uses one. Present means tokens
#: are verified as HS256 without a network call; absent means the project's
#: published keys are fetched from SUPABASE_URL instead. Never leaves this
#: process.
SUPABASE_JWT_SECRET: str = _env_str("SUPABASE_JWT_SECRET", "")


#: Sending the one email PRISM sends: a workspace invitation, through Resend.
#: Without a key the invitation is still created and its link is returned, so a
#: studio can send it however they like. `RESEND_FROM` must be an address on a
#: domain verified in Resend, or Resend refuses the message.
RESEND_API_KEY: str = _env_str("RESEND_API_KEY", "")
RESEND_FROM: str = _env_str("RESEND_FROM", "")

#: Where an invitation link points. The app's own origin - the link is opened by
#: a person in a browser, not by this server.
APP_ORIGIN: str = _env_str("APP_ORIGIN", "http://localhost:5174")


# --- Storage -----------------------------------------------------------------

GENERATED_DIR: Path = Path(
    _env_str("GENERATED_DIR", str(BACKEND_DIR / "generated"))
).expanduser()
if not GENERATED_DIR.is_absolute():
    GENERATED_DIR = (BACKEND_DIR / GENERATED_DIR).resolve()


# --- HTTP --------------------------------------------------------------------

_DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

ALLOWED_ORIGINS: list[str] = [
    origin.strip()
    for origin in _env_str("ALLOWED_ORIGINS", ",".join(_DEFAULT_ORIGINS)).split(",")
    if origin.strip()
] or list(_DEFAULT_ORIGINS)

HOST: str = _env_str("HOST", "127.0.0.1")
PORT: int = _env_int("PORT", 8000)


# --- Upload limits -----------------------------------------------------------

MAX_IMAGES: int = _env_int("MAX_IMAGES", 8)

#: Documents - a scope in Word, a requirement list in Excel, a tender as a PDF.
#: Read as text and added to the brief; see app/attachments.py. Fewer and larger
#: than images, because one of these is usually the whole scope.
MAX_DOCUMENTS: int = _env_int("MAX_DOCUMENTS", 5)
MAX_DOCUMENT_BYTES: int = _env_int("MAX_DOCUMENT_BYTES", 12 * 1024 * 1024)
MAX_IMAGE_BYTES: int = _env_int("MAX_IMAGE_BYTES", 8 * 1024 * 1024)

#: Generous ceiling on the brief. A real brief is a few hundred words; this only
#: exists to stop a pasted database from reaching the model.
MAX_BRIEF_CHARS: int = _env_int("MAX_BRIEF_CHARS", 20_000)

#: Mime types the Gemini vision path handles reliably. Anything else that still
#: claims `image/*` is passed through - this list drives the error message only.
SUPPORTED_IMAGE_TYPES: tuple[str, ...] = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/gif",
)
