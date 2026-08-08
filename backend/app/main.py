"""PRISM HTTP surface - the exact endpoints in docs/CONTRACT.md section 4.

One submission in, one generation call, two documents out. There is no chat
loop or conversational session; durable application state is stored in SQL.

This file is the composition root and nothing else. It builds the application,
installs the middleware, runs the three things that must be true before the
first request is answered, and includes routers from `app/features/`. It defines
no routes of its own, and that is the point: it used to
define fifty-three, and a file that holds every endpoint in a system is a file
nobody can hold in their head.

Where the routes went:

    features/quotations/   briefs, pricing, revisions, generated files
    features/documents/    proposals built from a quotation
    features/intakes/      studio intake and the unauthenticated client door
    features/team/         accounts, membership, and invitations
    features/platform/     reference data, notifications, and health

Run it any of these ways:

    py -m uvicorn app.main:app --reload --no-access-log --port 8000
    py -m app.main                                      # from backend/
    py backend/app/main.py                              # from anywhere
"""

from __future__ import annotations

# Allows `py backend/app/main.py` to work from any cwd by putting `backend/` on
# the path before the package imports below resolve. A no-op under uvicorn.
if __package__ in (None, ""):  # pragma: no cover - entry-point convenience only
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.features.documents.presentation import routes as document_routes
from app.features.intakes.infrastructure import tokens
from app.features.intakes.presentation import client_routes, studio_routes as intake_routes
from app.features.jobs.application import service as jobs
from app.features.platform.presentation import routes as platform_routes
from app.features.quotations.presentation import routes as quotation_routes
from app.features.team.infrastructure import auth
from app.features.team.presentation import routes as team_routes
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import config
from app.shared.infrastructure import database
from app.shared.presentation.http import deps, middleware

# `documents`, `intakes`, `client`, `platform` and `team` are also the names of
# domain modules under `app/`. The routers are aliased on the way in so that a
# reader - and the interpreter - can always tell which of the two a name means.
logger = deps.logger

app = FastAPI(
    title="PRISM",
    version="1.0.0",
    summary="One brief in, two documents out.",
    description=(
        "Submit a client brief with optional reference images and receive a priced "
        "client proposal and a requirements specification shaped by the kind of work "
        "being quoted, both rendered from a single structured estimate."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    # So the browser can read the filename off a download response.
    expose_headers=["Content-Disposition"],
)


# Registered AFTER CORS, and the order is deliberate: Starlette wraps the
# last-registered middleware outermost, so the gate ends up outside
# `CORSMiddleware` exactly as it did when both lived in this file. See
# `middleware.install` for what that costs and why it is preserved rather than
# quietly corrected.
middleware.install(app)


@app.on_event("startup")
async def _say_whether_anybody_has_to_sign_in() -> None:
    if auth.required():
        logger.info("Sign-in required: access tokens are verified on every call.")
    else:
        logger.warning(
            "No sign-in configured: this API answers anyone who can reach it. Set "
            "SUPABASE_URL (and SUPABASE_ANON_KEY) in backend/.env to require one."
        )


@app.on_event("startup")
async def _prepare_workspaces_and_bury_dead_jobs() -> None:
    # SQL is ready before the first repository call. On an existing install,
    # the former JSON records are imported once and retained as a migration archive.
    database.initialize()
    workspaces.ensure_ready()
    from app.features.platform.infrastructure import legacy

    legacy.migrate()
    jobs.restore()


@app.on_event("startup")
async def _build_the_client_token_index() -> None:
    # After workspaces exist (the walk below reads `workspaces.listing()`),
    # and before this server answers its first request: see
    # `tokens.build_index()`'s own docstring for what this is paying for up
    # front, deliberately, rather than letting `/api/client/<token>`'s first
    # caller - who could be a stranger's first guess, since that route needs
    # no token of the studio's own to reach - pay for it instead.
    tokens.build_index()


# --- The routers -------------------------------------------------------------
#
# Order is not load-bearing here, and it is worth saying why, because route
# registration order usually is: FastAPI matches in registration order, so a
# `{param}` route registered ahead of a literal sibling shadows it. Every one
# of these six owns a disjoint URL prefix, so no path can match two routers and
# no ordering between them can change which handler answers. Order WITHIN each
# router is load-bearing, and each was extracted with its relative order intact.
app.include_router(quotation_routes.router)
app.include_router(document_routes.router)
app.include_router(intake_routes.router)
app.include_router(client_routes.router)
app.include_router(team_routes.router)
app.include_router(platform_routes.router)


if __name__ == "__main__":
    import uvicorn

    if not config.key_configured():
        logger.warning(
            "Starting without GEMINI_API_KEY - /api/proposals will answer 503 until "
            "it is set in %s",
            config.ENV_FILE,
        )

    # Client intake bearer tokens are part of public URL paths, so ordinary
    # access logs would persist credentials. Application logs remain enabled.
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        access_log=False,
    )
