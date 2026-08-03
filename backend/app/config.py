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


#: DigitalOcean Spaces, where a client's own uploaded files go when there is
#: somewhere to put them. Four of these or none: `app.intakefiles.configured()`
#: is the predicate, and with any of the four missing the same bytes go to the
#: workspace's own `_intake_files/` instead. That fallback is not a stub - it is
#: what a studio running PRISM on one machine should be using, and it is what
#: keeps every `check_*.py` offline and network-free.
#:
#: Named `DO_SPACES_*` because that is what is already in this install's `.env`
#: and because it says whose object store this is, which `SPACES_KEY` did not.
#:
#: Server-side only, exactly as `GEMINI_API_KEY` above is. Nothing reads these
#: onto the wire: `/api/health` may say *whether* Spaces is configured, the same
#: way it already reports `key_configured` for Gemini, and never more than that.
DO_SPACES_ACCESS_KEY: str = _env_str("DO_SPACES_ACCESS_KEY", "")
DO_SPACES_SECRET_KEY: str = _env_str("DO_SPACES_SECRET_KEY", "")

#: The Space's region slug - `sgp1`, `nyc3`, `fra1` - and its bucket. These two
#: with the two credentials above are the four `configured()` requires.
DO_SPACES_REGION: str = _env_str("DO_SPACES_REGION", "")
DO_SPACES_BUCKET: str = _env_str("DO_SPACES_BUCKET", "")

#: The API endpoint, and the one of the five that is genuinely optional.
#: `https://<region>.digitaloceanspaces.com` is the correct answer for every
#: Space there is, so `app.intakefiles.endpoint()` derives exactly that when this
#: is empty and there is nothing to configure in the ordinary case. It is still
#: read rather than only derived, for the cases the derivation cannot reach: a
#: CDN edge hostname, a region DigitalOcean adds after this line was written, or
#: an S3-compatible store that is not DigitalOcean at all being pointed at for a
#: dry run. `configured()` deliberately does not require it - a studio who set
#: the four that matter and not this one should get Spaces, not the local
#: fallback.
DO_SPACES_ENDPOINT: str = _env_str("DO_SPACES_ENDPOINT", "")


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

#: Everything a client may attach to one intake, added together. Deliberately
#: its own name rather than a second use of `MAX_DOCUMENT_BYTES` above: those
#: are the studio's own limits on the studio's own uploads, and a studio
#: raising one for itself must not thereby raise it for a stranger holding a
#: link. Read by `_gate` (app/main.py) today, where it sizes the body cap on
#: an anonymous multipart write and so bounds what arrives on the wire; the
#: `/submit` handler reads it again once that route accepts files, to bound the
#: files themselves once they have been parsed out. Two different things
#: measured against one number, deliberately: a caller must not be able to get
#: past the second by arranging the first. Twenty megabytes is a scope in Word,
#: a spreadsheet and a few photographs of a site; it is not a video.
MAX_CLIENT_UPLOAD_TOTAL_BYTES: int = _env_int(
    "MAX_CLIENT_UPLOAD_TOTAL_BYTES", 20 * 1024 * 1024
)

#: How many files a client may attach to one intake, and how large any one of
#: them may be. Their own names rather than a second reading of `MAX_IMAGES`,
#: `MAX_DOCUMENTS` and `MAX_DOCUMENT_BYTES` above, and deliberately tighter than
#: all three: those are a studio's limits on a studio's own uploads to its own
#: pad, and a studio raising one of them for itself must not thereby raise it
#: for a stranger holding a link. Six files covers a scope, a spreadsheet and
#: four photographs of a site, which is what a real enquiry looks like; six
#: megabytes each is a large PDF and a phone photograph, and is under both
#: `MAX_IMAGE_BYTES` and `MAX_DOCUMENT_BYTES` on purpose so that neither of
#: those two being raised can widen this one by accident. The aggregate ceiling
#: is `MAX_CLIENT_UPLOAD_TOTAL_BYTES` above - there is one of those and this is
#: not a second.
MAX_CLIENT_FILES: int = _env_int("MAX_CLIENT_FILES", 6)
MAX_CLIENT_FILE_BYTES: int = _env_int("MAX_CLIENT_FILE_BYTES", 6 * 1024 * 1024)

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
