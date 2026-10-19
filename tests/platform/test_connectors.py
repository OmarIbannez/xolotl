"""The ``Connector`` base: retries, the breaker, evidence, metering and redaction."""

import json
import logging
from pathlib import Path
from typing import override
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.helpers import (
    everything_stored,
    leaked,
    make_tenant_with_keys,
    staff_session,
    system_ctx,
)
from tests.platform.connectors_support import (
    FakeConnector,
    FakeMonotonic,
    calls_of,
    evidence_of,
    make_deps,
    ok,
    request,
)
from xolotl.platform.connectors import (
    CallOutcome,
    CheckResult,
    CheckType,
    CircuitState,
    ConnectorMode,
    ConnectorRequest,
    ConnectorRoute,
    CredentialMissingError,
    CredentialVault,
    RawResponse,
    redact,
)
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import TenantEnv
from xolotl.platform.ids import uuid7

LIVE = ConnectorMode.LIVE


@pytest.fixture
def prod(engine: Engine) -> UUID:
    return make_tenant_with_keys("prod", env=TenantEnv.PROD)


@pytest.fixture
def sandbox(engine: Engine) -> UUID:
    return make_tenant_with_keys("sbx", env=TenantEnv.SANDBOX)


def test_a_live_call_passes_stores_evidence_and_meters(tmp_path: Path, prod: UUID):
    deps, _, sleep = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ok()])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (
        out.outcome,
        out.attempts,
        out.route,
        out.mode,
        out.cost_micros,
        out.error,
    ) == (CallOutcome.PASS, 1, ConnectorRoute.PRIMARY, LIVE, 7, None)
    assert out.connector == "fake"
    assert out.check_type is CheckType.RFC_VALIDATION
    assert out.details == {"status": 200}
    assert out.evidence_key == f"connectors/fake/{out.call_id}"
    assert evidence_of(tmp_path / "objects", prod, out) == b'{"ok": 1}'
    (row,) = calls_of(prod)
    assert (
        row.id,
        row.connector,
        row.check_type,
        row.mode,
        row.route,
        row.outcome,
        row.cost_micros,
        row.evidence_key,
    ) == (
        out.call_id,
        "fake",
        CheckType.RFC_VALIDATION,
        LIVE,
        ConnectorRoute.PRIMARY,
        CallOutcome.PASS,
        7,
        out.evidence_key,
    )
    assert row.duration_ms >= 0
    assert row.error is None
    assert sleep.calls == []


def test_transient_failures_are_retried_with_backoff(tmp_path: Path, prod: UUID):
    deps, _, sleep = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[httpx.ConnectError("down"), ok(status=503), ok()])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.error) == (CallOutcome.PASS, 3, None)
    assert sleep.calls == [0.2, 0.5]


def test_three_timeouts_end_as_timeout(tmp_path: Path, prod: UUID):
    deps, _, sleep = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 3)
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.evidence_key) == (
        CallOutcome.TIMEOUT,
        3,
        None,
    )
    assert "ReadTimeout" in (out.error or "")
    assert sleep.calls == [0.2, 0.5]
    (row,) = calls_of(prod)
    assert (row.outcome, row.evidence_key, row.error) == (
        CallOutcome.TIMEOUT,
        None,
        out.error,
    )


def test_three_5xx_answers_end_as_an_error_keeping_the_last_body(
    tmp_path: Path, prod: UUID
):
    deps, _, sleep = make_deps(tmp_path)
    fake = FakeConnector(
        deps, calls=[ok(b"one", status=500), ok(b"two", status=502), ok(b"3", 503)]
    )
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.error) == (CallOutcome.ERROR, 3, "HTTP 503")
    assert evidence_of(tmp_path / "objects", prod, out) == b"3"
    assert sleep.calls == [0.2, 0.5]


def test_a_4xx_is_an_error_without_retry_and_keeps_the_body_as_evidence(
    tmp_path: Path, prod: UUID
):
    deps, _, sleep = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[RawResponse(401, b"bad token", {}, 1)])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.error) == (CallOutcome.ERROR, 1, "HTTP 401")
    assert evidence_of(tmp_path / "objects", prod, out) == b"bad token"
    assert fake.circuit_state("mx-1") is CircuitState.CLOSED
    assert sleep.calls == []


def test_a_permanent_error_stops_at_once_without_counting_a_failure(
    tmp_path: Path, prod: UUID
):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[CredentialMissingError("no token")])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.evidence_key) == (CallOutcome.ERROR, 1, None)
    assert "CredentialMissingError" in (out.error or "")
    assert fake.circuit_state("mx-1") is CircuitState.CLOSED


def test_an_unexpected_exception_is_an_error_without_retry(tmp_path: Path, prod: UUID):
    deps, _, sleep = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ValueError("bug")])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.error) == (
        CallOutcome.ERROR,
        1,
        "ValueError: bug",
    )
    assert sleep.calls == []
    assert fake.circuit_state("mx-1") is CircuitState.CLOSED


def test_an_interpreter_crash_is_an_error_outcome_not_an_exception(
    tmp_path: Path, prod: UUID
):
    def boom(raw: RawResponse) -> CheckResult:
        raise KeyError(raw.status)

    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ok()], interpret=boom)
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert out.outcome is CallOutcome.ERROR
    assert out.evidence_key is not None
    assert out.details == {}
    assert out.cost_micros == 0
    assert "KeyError" in (out.error or "")


def test_a_store_failure_is_an_error_outcome_without_evidence(
    tmp_path: Path, prod: UUID
):
    deps, _, _ = make_deps(tmp_path)
    # A connector name that is not a valid key segment makes the store refuse the put.
    fake = FakeConnector(deps, name="..", calls=[ok()])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.evidence_key) == (CallOutcome.ERROR, None)
    assert "InvalidObjectKeyError" in (out.error or "")
    (row,) = calls_of(prod)
    assert (row.outcome, row.evidence_key) == (CallOutcome.ERROR, None)


def test_five_failures_in_a_minute_open_the_circuit_and_the_next_call_is_refused(
    tmp_path: Path, prod: UUID
):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 6)
    with tenant_session(system_ctx(prod)) as s:
        for _ in range(2):
            _ = fake.run(s, request(), mode=LIVE)
        assert fake.circuit_state("mx-1") is CircuitState.OPEN
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.error, out.evidence_key) == (
        CallOutcome.CIRCUIT_OPEN,
        0,
        "circuit open",
        None,
    )
    assert fake.call_count == 6
    assert [r.outcome for r in calls_of(prod)] == [
        CallOutcome.TIMEOUT,
        CallOutcome.TIMEOUT,
        CallOutcome.CIRCUIT_OPEN,
    ]


def test_the_circuit_half_opens_after_thirty_seconds_and_a_success_closes_it(
    tmp_path: Path, prod: UUID
):
    clock = FakeMonotonic()
    deps, _, _ = make_deps(tmp_path, clock=clock)
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 6)
    with tenant_session(system_ctx(prod)) as s:
        for _ in range(2):
            _ = fake.run(s, request(), mode=LIVE)
        clock.advance(29)
        refused = fake.run(s, request(), mode=LIVE)
        assert refused.outcome is CallOutcome.CIRCUIT_OPEN
        clock.advance(1)
        assert fake.circuit_state("mx-1") is CircuitState.HALF_OPEN
        fake.calls.extend([ok()])
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, fake.call_count) == (CallOutcome.PASS, 1, 7)
    assert fake.circuit_state("mx-1") is CircuitState.CLOSED
    assert [r.outcome for r in calls_of(prod)] == [
        CallOutcome.TIMEOUT,
        CallOutcome.TIMEOUT,
        CallOutcome.CIRCUIT_OPEN,
        CallOutcome.PASS,
    ]


def test_a_failed_trial_reopens_the_circuit_for_another_thirty_seconds(
    tmp_path: Path, prod: UUID
):
    clock = FakeMonotonic()
    deps, _, _ = make_deps(tmp_path, clock=clock)
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 6)
    with tenant_session(system_ctx(prod)) as s:
        for _ in range(2):
            _ = fake.run(s, request(), mode=LIVE)
        clock.advance(30)
        assert fake.circuit_state("mx-1") is CircuitState.HALF_OPEN
        fake.calls.extend([httpx.ReadTimeout("slow")] * 3)
        out = fake.run(s, request(), mode=LIVE)
        assert (out.outcome, out.attempts, fake.circuit_state("mx-1")) == (
            CallOutcome.TIMEOUT,
            3,
            CircuitState.OPEN,
        )
        clock.advance(29)
        refused = fake.run(s, request(), mode=LIVE)
        assert refused.outcome is CallOutcome.CIRCUIT_OPEN
        clock.advance(1)
        assert fake.circuit_state("mx-1") is CircuitState.HALF_OPEN
    assert fake.call_count == 9


def test_a_breaker_is_kept_per_cell(tmp_path: Path, admin_engine: Engine, prod: UUID):
    other = make_tenant_with_keys("prod-2", env=TenantEnv.PROD)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("UPDATE tenant SET cell = 'mx-2' WHERE id = :id"), {"id": other}
        )
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 6)
    assert fake.circuit_state("mx-2") is CircuitState.CLOSED
    with tenant_session(system_ctx(prod)) as s:
        for _ in range(2):
            _ = fake.run(s, request(), mode=LIVE)
    assert fake.circuit_state("mx-1") is CircuitState.OPEN
    assert fake.circuit_state("mx-2") is CircuitState.CLOSED
    fake.calls.extend([ok()])
    with tenant_session(system_ctx(other)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert out.outcome is CallOutcome.PASS
    assert fake.circuit_state("mx-1") is CircuitState.OPEN


def test_a_sandbox_tenant_always_gets_the_mock(tmp_path: Path, sandbox: UUID):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ok()])
    with tenant_session(system_ctx(sandbox)) as s:
        out = fake.run(s, request(rfc="ABC010101AAA"), mode=LIVE)
    assert (out.outcome, out.mode, out.route, out.attempts, out.cost_micros) == (
        CallOutcome.PASS,
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        0,
        0,
    )
    assert fake.call_count == 0
    assert (
        evidence_of(tmp_path / "objects", sandbox, out)
        == b'{"mock": true, "rfc": "ABC010101AAA"}'
    )
    (row,) = calls_of(sandbox)
    assert (row.mode, row.route, row.cost_micros) == (
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        0,
    )


def test_a_sandbox_mode_call_of_a_prod_tenant_is_a_mock_on_route_mock(
    tmp_path: Path, prod: UUID
):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ok()])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(
            s,
            request(rfc="ABC010101AAA"),
            mode=ConnectorMode.SANDBOX,
            route=ConnectorRoute.FALLBACK,
        )
    assert (out.outcome, out.mode, out.route, out.attempts, out.cost_micros) == (
        CallOutcome.PASS,
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        0,
        0,
    )
    assert fake.call_count == 0
    assert fake.seen_credentials == []
    (row,) = calls_of(prod)
    assert (row.mode, row.route, row.cost_micros) == (
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        0,
    )


class BrokenMock(FakeConnector):
    @override
    def _mock(self, request: ConnectorRequest) -> RawResponse:
        msg = "no mock"
        raise RuntimeError(msg)


def test_a_failing_mock_is_an_error_with_no_attempt(tmp_path: Path, sandbox: UUID):
    deps, _, _ = make_deps(tmp_path)
    fake = BrokenMock(deps)
    with tenant_session(system_ctx(sandbox)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert (out.outcome, out.attempts, out.evidence_key, out.route) == (
        CallOutcome.ERROR,
        0,
        None,
        ConnectorRoute.MOCK,
    )
    assert out.error == "RuntimeError: no mock"


def test_credentials_reach_the_call_and_never_the_evidence_or_logs(
    tmp_path: Path,
    prod: UUID,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    token = f"tok-{uuid4().hex}"
    with staff_session(prod, uuid7()) as s:
        _ = CredentialVault().set(s, "fake", LIVE, {"token": token})

    def echo(raw: RawResponse) -> CheckResult:
        return CheckResult(
            outcome="PASS", details={"echo": json.loads(raw.body)["echo"]}
        )

    echoed = f'{{"echo": "{token}"}}'.encode()
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(
        deps, calls=[RawResponse(500, echoed, {}, 1), ok(echoed)], interpret=echo
    )
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert out.outcome is CallOutcome.PASS
    assert fake.seen_credentials == [{"token": token}] * 2
    assert evidence_of(tmp_path / "objects", prod, out) == b'{"echo": "[REDACTED]"}'
    assert out.details == {"echo": "[REDACTED]"}
    assert token not in caplog.text
    assert leaked(caplog.text, token.encode()) == []
    assert leaked(everything_stored(admin_engine), token.encode()) == []


def test_an_error_text_never_carries_a_credential(tmp_path: Path, prod: UUID):
    token = f"tok-{uuid4().hex}"
    with staff_session(prod, uuid7()) as s:
        _ = CredentialVault().set(s, "fake", LIVE, {"token": token})
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ValueError(f"rejected {token}")])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=LIVE)
    assert out.error == "ValueError: rejected [REDACTED]"
    (row,) = calls_of(prod)
    assert row.error == out.error


def test_redact_replaces_every_secret_longest_first():
    assert (
        redact("a=tok-12 b=tok-1 c=", ["tok-1", "tok-12", ""])
        == "a=[REDACTED] b=[REDACTED] c="
    )
