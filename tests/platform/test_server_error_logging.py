"""A server error answered from an XolotlError is logged with its code (plan 1b, B7).

The request line carries only the status, so a replica started with a stale master key
would show as a bare 503 in the logs and as KEK_MISMATCH only to the client. The line
these tests pin names the code, at ERROR, with the request id, and never carries the
exception's text or a traceback.
"""

import json
import logging

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from xolotl.app import build_app
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KekMismatchError,
    KeyDestroyedError,
)
from xolotl.platform.errors import DomainError, UnauthorizedError, XolotlError
from xolotl.platform.logging import JsonFormatter, RedactionFilter


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/kek")
    def kek() -> dict[str, str]:
        raise KekMismatchError

    @router.get("/probe/decrypt")
    def decrypt() -> dict[str, str]:
        raise DecryptionError

    @router.get("/probe/internal")
    def internal() -> dict[str, str]:
        raise XolotlError

    @router.get("/probe/conflict")
    def conflict() -> dict[str, str]:
        raise DomainError

    @router.get("/probe/gone")
    def gone() -> dict[str, str]:
        raise KeyDestroyedError

    @router.get("/probe/unauth")
    def unauth() -> dict[str, str]:
        raise UnauthorizedError

    return router


@pytest.fixture
def probe_app(engine: Engine) -> FastAPI:
    app = build_app()
    app.include_router(_router(), prefix="/v1")
    return app


@pytest.fixture
def probe(probe_app: FastAPI):
    with TestClient(probe_app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def production_format(caplog: pytest.LogCaptureFixture):
    # Format with the production pair so caplog.text is what stdout would carry. The
    # handler is shared by every later test, so the pair has to come off again.
    handler, previous, redaction = (
        caplog.handler,
        caplog.handler.formatter,
        RedactionFilter(),
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(redaction)
    yield
    handler.removeFilter(redaction)
    handler.setFormatter(previous)


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/v1/probe/kek", 503, "KEK_MISMATCH"),
        ("/v1/probe/decrypt", 500, "DECRYPTION_FAILED"),
        ("/v1/probe/internal", 500, "INTERNAL"),
    ],
)
def test_a_server_error_is_logged_at_error_with_its_code_and_the_request_id(
    probe: TestClient,
    caplog: pytest.LogCaptureFixture,
    path: str,
    status: int,
    code: str,
):
    with caplog.at_level(logging.INFO):
        response = probe.get(path, headers={"X-Request-Id": "srv-1"})
    assert response.status_code == status
    assert response.json()["code"] == code
    (logged,) = [r for r in caplog.records if r.getMessage() == "server error"]
    assert logged.levelno == logging.ERROR
    fields = vars(logged)
    assert (fields["status"], fields["code"], fields["request_id"]) == (
        status,
        code,
        "srv-1",
    )
    assert (fields["method"], fields["path"]) == ("GET", path)


@pytest.mark.parametrize(
    "path",
    ["/v1/probe/conflict", "/v1/probe/gone", "/v1/probe/unauth", "/v1/no/such/route"],
)
def test_a_client_error_is_not_logged_as_a_server_error(
    probe: TestClient, caplog: pytest.LogCaptureFixture, path: str
):
    with caplog.at_level(logging.INFO):
        response = probe.get(path)
    assert 400 <= response.status_code < 500
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert [r for r in caplog.records if r.getMessage() == "server error"] == []


def test_the_server_error_line_is_json_without_the_exception_or_its_text(
    probe: TestClient, caplog: pytest.LogCaptureFixture, production_format: None
):
    with caplog.at_level(logging.INFO):
        _ = probe.get("/v1/probe/kek", headers={"X-Request-Id": "srv-2"})
    lines = [json.loads(line) for line in caplog.text.splitlines()]
    (line,) = [line for line in lines if line["msg"] == "server error"]
    assert (line["level"], line["status"], line["code"]) == (
        "ERROR",
        503,
        "KEK_MISMATCH",
    )
    assert line["request_id"] == "srv-2"
    assert "exc_info" not in line  # the code says why; no traceback rides along
    assert KekMismatchError.default_detail not in caplog.text
