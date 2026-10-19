"""Staff login, TOTP and password routes (spec §6.1). No Idempotency-Key here."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from xolotl.modules.tenants import service
from xolotl.modules.tenants.schemas import (
    LoginRequest,
    MeResponse,
    MfaRequired,
    PasswordChangeRequest,
    TokenResponse,
    TotpConfirmRequest,
    TotpEnrollResponse,
    TotpLoginRequest,
)
from xolotl.platform.api.app import app_state
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.principal import SessionDep, StaffDep
from xolotl.platform.db import apply_context

router = v1_router(prefix="/auth", tags=["auth"])


def auth_session(request: Request) -> Iterator[Session]:
    """Yield a platform-scoped session for the steps before authentication.

    Commits on success, rolls back on error and always closes the session.

    Yields:
        The session, bound to the platform auth context.

    """
    session = app_state(request).session_factory()
    try:
        apply_context(session, service.auth_context())
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


# Function scope, like SessionDep: commit before the response is sent.
AuthSessionDep = Annotated[Session, Depends(auth_session, scope="function")]


def _token_response(outcome: service.LoginOutcome) -> TokenResponse:
    return TokenResponse(
        access_token=outcome.token,
        expires_in=outcome.expires_in,
        must_change_password=outcome.must_change_password,
    )


def _to_response(outcome: service.LoginOutcome) -> TokenResponse | MfaRequired:
    if outcome.mfa_required:
        return MfaRequired(mfa_token=outcome.token)
    return _token_response(outcome)


@router.post("/login")
def login(body: LoginRequest, session: AuthSessionDep) -> TokenResponse | MfaRequired:
    return _to_response(
        service.login(
            session,
            tenant_slug=body.tenant_slug,
            email=body.email,
            password=body.password,
        )
    )


@router.post("/login/totp")
def login_totp(
    body: TotpLoginRequest, session: AuthSessionDep
) -> TokenResponse | MfaRequired:
    return _to_response(
        service.login_totp(session, mfa_token=body.mfa_token, code=body.code)
    )


@router.post("/password")
def change_password(
    body: PasswordChangeRequest, _staff: StaffDep, session: SessionDep
) -> TokenResponse:
    # The change ends every earlier session, the caller's included: the response
    # carries the access token to continue with.
    return _token_response(
        service.change_password(
            session,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    )


@router.post("/totp/enroll")
def totp_enroll(_staff: StaffDep, session: SessionDep) -> TotpEnrollResponse:
    return service.enroll_totp(session)


@router.post("/totp/confirm", status_code=204)
def totp_confirm(
    body: TotpConfirmRequest, _staff: StaffDep, session: SessionDep
) -> None:
    service.confirm_totp(session, code=body.code)


@router.get("/me")
def me(_staff: StaffDep, session: SessionDep) -> MeResponse:
    return service.me(session)
