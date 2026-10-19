from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from xolotl.platform.api.problems import problem
from xolotl.platform.db import RoleBypassError, assert_app_role_safe, get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    # One query as the app role: the database answers, and row-level security
    # binds the role the API connects as.
    try:
        assert_app_role_safe(get_engine())
    except RoleBypassError:
        return problem(
            request,
            status=503,
            code="NOT_READY",
            detail="database role can bypass row-level security",
        )
    except Exception:  # ruff: ignore[blind-except]  # readiness must never raise
        return problem(
            request, status=503, code="NOT_READY", detail="database unreachable"
        )
    return JSONResponse({"status": "ok", "database": "ok"})
