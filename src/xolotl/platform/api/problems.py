"""RFC 9457 problem responses with stable codes (spec §7)."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from xolotl.platform.errors import XolotlError

logger = logging.getLogger(__name__)

PROBLEM_TYPE_BASE = "https://xolotl.dev/problems/"
MEDIA_TYPE = "application/problem+json"
_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    410: "Gone",
    422: "Unprocessable Content",
    500: "Internal Server Error",
    503: "Service Unavailable",
}
_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


class ProblemError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    errors: list[ProblemError] | None = None


def _request_id(request: Request) -> str | None:
    # Starlette's outermost ServerErrorMiddleware runs the 500 handler after
    # RequestIdMiddleware has unwound: its send wrapper is bypassed and
    # request_id_var is already reset. The id still travels in the scope state.
    state = cast("dict[str, object]", request.scope.get("state", {}))
    request_id = state.get("request_id")
    return request_id if isinstance(request_id, str) else None


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    errors: list[ProblemError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        type=PROBLEM_TYPE_BASE + code,
        title=_TITLES.get(status, "Error"),
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        errors=errors,
    )
    response_headers = dict(headers) if headers else {}
    request_id = _request_id(request)
    if request_id is not None:
        response_headers["X-Request-Id"] = request_id
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=MEDIA_TYPE,
        headers=response_headers,
    )


def _expect[E: Exception](exc: Exception, cls: type[E]) -> E:
    if not isinstance(exc, cls):  # Starlette types handlers over plain Exception
        msg = f"handler for {cls.__name__} received {type(exc).__name__}"
        raise TypeError(msg)
    return exc


def _xolotl_error(request: Request, exc: Exception) -> Response:
    error = _expect(exc, XolotlError)
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if error.status == HTTPStatus.UNAUTHORIZED
        else None
    )
    return problem(
        request,
        status=error.status,
        code=error.code,
        detail=error.detail,
        headers=headers,
    )


def _validation_error(request: Request, exc: Exception) -> Response:
    error = _expect(exc, RequestValidationError)
    raw = cast("list[dict[str, object]]", list(error.errors()))
    errors = [
        ProblemError(
            loc=[
                part if isinstance(part, int) else str(part)
                for part in cast("tuple[object, ...]", item.get("loc", ()))
            ],
            msg=str(item.get("msg", "")),
            type=str(item.get("type", "")),
        )
        for item in raw
    ]
    return problem(
        request,
        status=422,
        code="VALIDATION_FAILED",
        detail="request validation failed",
        errors=errors,
    )


def _http_exception(request: Request, exc: Exception) -> Response:
    error = _expect(exc, HTTPException)
    code = _HTTP_CODES.get(error.status_code, f"HTTP_{error.status_code}")
    return problem(
        request,
        status=error.status_code,
        code=code,
        detail=str(error.detail),
        headers=error.headers,
    )


def _unhandled(request: Request, exc: Exception) -> Response:
    # The exception is logged whole; RedactionFilter scrubs database errors on the way
    # out, for this logger and every other one.
    request_id = _request_id(request)
    extra = {} if request_id is None else {"request_id": request_id}
    logger.error("unhandled error", exc_info=exc, extra=extra)
    return problem(request, status=500, code="INTERNAL", detail="internal error")


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(XolotlError, _xolotl_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_exception)
    app.add_exception_handler(Exception, _unhandled)
