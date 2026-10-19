from typing import cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from xolotl.platform.api.problems import problem
from xolotl.platform.db import RoleBypassError, assert_app_role_safe, get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _embedded_worker_stopped(request: Request) -> bool:
    # AppState.embedded_worker_alive, read by name: AppState lives in app.py, which
    # mounts this router, so even a typing-only import of it would be a cycle.
    state = cast("object", cast("FastAPI", request.app).state.xolotl)
    alive = cast("object", getattr(state, "embedded_worker_alive", None))
    return callable(alive) and not alive()


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
    # An API whose embedded worker died still serves requests but runs no event,
    # job or schedule (in one container it is the only loop): not ready, so the
    # orchestrator restarts it.
    if _embedded_worker_stopped(request):
        return problem(
            request, status=503, code="NOT_READY", detail="embedded worker stopped"
        )
    return JSONResponse({"status": "ok", "database": "ok"})
