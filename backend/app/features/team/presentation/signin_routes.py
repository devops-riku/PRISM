"""The sign-in endpoints: every way in, answered without a token.

Each answers with an `AuthAnswer`: `step: "done"` carries PRISM's tokens and
who they belong to; `step: "code"` carries the flow a code is now waited for in,
which `/api/auth/code/verify` finishes. The rules - what is checked, what is
counted, what is never revealed - are in `app.features.team.application.signin`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.features.team.application import signin

router = APIRouter(tags=["accounts"])


class SignedInUser(BaseModel):
    id: str
    email: str
    provider: str = Field(default="email", description="email, google, facebook or other.")


class AuthAnswer(BaseModel):
    step: str = Field(description="`done`: signed in, tokens below. `code`: a code was emailed; finish with the flow.")
    flow: str = Field(default="", description="With `code`: what /api/auth/code/verify is sent.")
    email: str = Field(default="", description="With `code`: where the code went.")
    purpose: str = Field(default="", description="With `code`: code, signup, reset, confirm or provider.")
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = ""
    expires_in: int = 0
    user: SignedInUser | None = None


class EmailPassword(BaseModel):
    email: str = ""
    password: str = ""


class EmailOnly(BaseModel):
    email: str = ""


class FlowOnly(BaseModel):
    flow: str = ""


class CodeCheck(BaseModel):
    flow: str = ""
    code: str = ""
    password: str = Field(default="", description="The new password, when the flow is a reset.")


class RefreshRequest(BaseModel):
    refresh_token: str = ""


class ProviderRequest(BaseModel):
    provider: str = Field(default="", description="The provider id from /api/auth/config.")


class ProviderStart(BaseModel):
    url: str


class ProviderReturn(BaseModel):
    id: str = ""
    token: str = ""


def _ip(request: Request) -> str:
    # Cloudflare sits in front of Traefik on Neptune and names the visitor in
    # this header; elsewhere it is the peer address uvicorn resolved.
    return request.headers.get("CF-Connecting-IP", "") or (request.client.host if request.client else "unknown")


def _answer(answer: signin.Answer) -> AuthAnswer:
    if answer.tokens:
        return AuthAnswer(step=answer.step, **answer.tokens)
    return AuthAnswer(step=answer.step, flow=answer.flow, email=answer.email, purpose=answer.purpose)


def _refused(error: signin.SignInError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content={"detail": error.message, "code": error.code})


async def _run(work) -> AuthAnswer | JSONResponse:
    try:
        return _answer(await work)
    except signin.SignInError as error:
        return _refused(error)


@router.post("/api/auth/sign-in", response_model=AuthAnswer)
async def sign_in(body: EmailPassword, request: Request):
    """Email and password. An address never proven gets a code first (`step: code`)."""
    return await _run(signin.sign_in(body.email, body.password, _ip(request)))


@router.post("/api/auth/sign-up", response_model=AuthAnswer)
async def sign_up(body: EmailPassword, request: Request):
    """A new account. Always answers `step: code`; the password is set once the code is right."""
    return await _run(signin.sign_up(body.email, body.password, _ip(request)))


@router.post("/api/auth/code", response_model=AuthAnswer)
async def send_code(body: EmailOnly, request: Request):
    """Email a sign-in code. An address with no account gets one made."""
    return await _run(signin.send_code(body.email, _ip(request)))


@router.post("/api/auth/reset", response_model=AuthAnswer)
async def start_reset(body: EmailOnly, request: Request):
    """Email a code to set a new password with. Answers the same for any address."""
    return await _run(signin.start_reset(body.email, _ip(request)))


@router.post("/api/auth/code/resend", response_model=AuthAnswer)
async def resend(body: FlowOnly, request: Request):
    return await _run(signin.resend(body.flow, _ip(request)))


@router.post("/api/auth/code/verify", response_model=AuthAnswer)
async def verify(body: CodeCheck):
    """The emailed code (and, on a reset, the new password). Signed in on success."""
    return await _run(signin.verify(body.flow, body.code, body.password))


@router.post("/api/auth/refresh", response_model=AuthAnswer)
async def refresh(body: RefreshRequest):
    return await _run(signin.refresh(body.refresh_token))


@router.post("/api/auth/provider", response_model=ProviderStart)
async def provider_start(body: ProviderRequest, request: Request):
    """Where to send the browser to sign in with Google or Facebook."""
    try:
        return ProviderStart(url=await signin.provider_start(body.provider, _ip(request)))
    except signin.SignInError as error:
        return _refused(error)


@router.post("/api/auth/provider/finish", response_model=AuthAnswer)
async def provider_finish(body: ProviderReturn, request: Request):
    """The `id` and `token` the provider return carried."""
    return await _run(signin.provider_finish(body.id, body.token, _ip(request)))
