"""The router and ``run_check``: modes, primary then fallbacks, definitive outcomes."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from tests.helpers import make_tenant_with_keys, platform_ctx, system_ctx
from tests.platform.connectors_support import (
    FakeConnector,
    calls_of,
    make_deps,
    ok,
    request,
)
from xolotl.platform.connectors import (
    CallOutcome,
    CheckResult,
    CheckType,
    ConnectorMode,
    ConnectorRoute,
    ConnectorRouter,
    NoRouteError,
    RawResponse,
    run_check,
)
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import TenantEnv
from xolotl.platform.errors import ContextMissingError

RFC_MX = (CheckType.RFC_VALIDATION, "MX")


@pytest.fixture
def prod(engine: Engine) -> UUID:
    return make_tenant_with_keys("prod", env=TenantEnv.PROD)


@pytest.fixture
def sandbox(engine: Engine) -> UUID:
    return make_tenant_with_keys("sbx", env=TenantEnv.SANDBOX)


def failing(_raw: RawResponse) -> CheckResult:
    return CheckResult(outcome="FAIL", details={"reason": "NO"}, cost_micros=3)


def two_step(
    tmp_path: Path,
    *,
    primary: FakeConnector | None = None,
    fallback: FakeConnector | None = None,
) -> tuple[ConnectorRouter, FakeConnector, FakeConnector]:
    deps, _, _ = make_deps(tmp_path)
    first = primary or FakeConnector(deps, name="primary")
    second = fallback or FakeConnector(deps, name="fallback")
    router = ConnectorRouter(
        [first, second],
        {RFC_MX: ("primary", "fallback")},
        default_mode=ConnectorMode.LIVE,
    )
    return router, first, second


def test_run_check_uses_the_default_mode_for_a_prod_tenant_and_the_explicit_one_when_given(  # ruff: ignore[line-too-long]
    tmp_path: Path, prod: UUID
):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ok()])
    router = ConnectorRouter(
        [fake], {RFC_MX: ("fake",)}, default_mode=ConnectorMode.SANDBOX
    )
    with tenant_session(system_ctx(prod)) as s:
        by_default = run_check(s, request(), router=router)
        explicit = run_check(s, request(), router=router, mode=ConnectorMode.LIVE)
    # A SANDBOX call is a mock on route MOCK.
    assert (by_default.mode, by_default.route, by_default.attempts) == (
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        0,
    )
    assert (explicit.mode, explicit.route, explicit.attempts) == (
        ConnectorMode.LIVE,
        ConnectorRoute.PRIMARY,
        1,
    )
    assert fake.call_count == 1


def test_run_check_forces_sandbox_for_a_sandbox_tenant_even_when_live_is_asked(
    tmp_path: Path, sandbox: UUID
):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps, calls=[ok()])
    router = ConnectorRouter(
        [fake], {RFC_MX: ("fake",)}, default_mode=ConnectorMode.LIVE
    )
    with tenant_session(system_ctx(sandbox)) as s:
        out = run_check(s, request(), router=router, mode=ConnectorMode.LIVE)
    assert (out.mode, out.route) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK)
    assert fake.call_count == 0
    (row,) = calls_of(sandbox)
    assert (row.mode, row.route) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK)


def test_the_router_falls_back_after_a_primary_error_and_records_both_routes(
    tmp_path: Path, prod: UUID
):
    deps, _, _ = make_deps(tmp_path)
    router, _, _ = two_step(
        tmp_path,
        primary=FakeConnector(deps, name="primary", calls=[ok(status=500)] * 3),
        fallback=FakeConnector(deps, name="fallback", calls=[ok()]),
    )
    with tenant_session(system_ctx(prod)) as s:
        out = run_check(s, request(), router=router)
    assert (out.connector, out.route, out.outcome) == (
        "fallback",
        ConnectorRoute.FALLBACK,
        CallOutcome.PASS,
    )
    assert [(r.connector, r.route, r.outcome) for r in calls_of(prod)] == [
        ("primary", ConnectorRoute.PRIMARY, CallOutcome.ERROR),
        ("fallback", ConnectorRoute.FALLBACK, CallOutcome.PASS),
    ]


def test_a_definitive_outcome_stops_the_route(tmp_path: Path, prod: UUID):
    deps, _, _ = make_deps(tmp_path)
    router, _, fallback = two_step(
        tmp_path,
        primary=FakeConnector(deps, name="primary", calls=[ok()], interpret=failing),
        fallback=FakeConnector(deps, name="fallback", calls=[ok()]),
    )
    with tenant_session(system_ctx(prod)) as s:
        out = run_check(s, request(), router=router)
    assert (out.connector, out.outcome) == ("primary", CallOutcome.FAIL)
    assert fallback.call_count == 0
    assert len(calls_of(prod)) == 1


def test_every_connector_failing_returns_the_last_outcome(tmp_path: Path, prod: UUID):
    deps, _, _ = make_deps(tmp_path)
    router, _, _ = two_step(
        tmp_path,
        primary=FakeConnector(deps, name="primary", calls=[ok(status=400)]),
        fallback=FakeConnector(deps, name="fallback", calls=[ok(status=404)]),
    )
    with tenant_session(system_ctx(prod)) as s:
        out = run_check(s, request(), router=router)
    assert (out.connector, out.route, out.outcome) == (
        "fallback",
        ConnectorRoute.FALLBACK,
        CallOutcome.ERROR,
    )
    assert len(calls_of(prod)) == 2


def test_sandbox_routes_only_the_primary(tmp_path: Path):
    router, _, _ = two_step(tmp_path)
    sandboxed = router.route(
        CheckType.RFC_VALIDATION, country="MX", tenant_mode=ConnectorMode.SANDBOX
    )
    live = router.route(
        CheckType.RFC_VALIDATION, country="MX", tenant_mode=ConnectorMode.LIVE
    )
    assert [c.name for c in sandboxed] == ["primary"]
    assert [c.name for c in live] == ["primary", "fallback"]


def test_no_route_is_a_409_conflict_with_a_stable_code(tmp_path: Path):
    router, _, _ = two_step(tmp_path)
    with pytest.raises(NoRouteError) as refused:
        _ = router.route(
            CheckType.CURP_VALIDATION, country="MX", tenant_mode=ConnectorMode.LIVE
        )
    assert (refused.value.status, refused.value.code) == (409, "CONNECTOR_NO_ROUTE")
    with pytest.raises(NoRouteError):
        _ = router.route(
            CheckType.RFC_VALIDATION, country="US", tenant_mode=ConnectorMode.LIVE
        )


@pytest.mark.parametrize(
    "routes",
    [
        {(CheckType.RFC_VALIDATION, "MX"): ("nobody",)},
        {(CheckType.UNIT_VALUE, "MX"): ("fake",)},
    ],
)
def test_the_router_refuses_unknown_names_and_unsupported_check_types(
    tmp_path: Path, routes: dict[tuple[CheckType, str], tuple[str, ...]]
):
    deps, _, _ = make_deps(tmp_path)
    fake = FakeConnector(deps)
    with pytest.raises(ValueError, match=r"fake|nobody"):
        _ = ConnectorRouter([fake], routes, default_mode=ConnectorMode.LIVE)


def test_the_router_refuses_two_connectors_of_one_name(tmp_path: Path):
    deps, _, _ = make_deps(tmp_path)
    with pytest.raises(ValueError, match="fake"):
        _ = ConnectorRouter(
            [FakeConnector(deps), FakeConnector(deps)],
            {},
            default_mode=ConnectorMode.LIVE,
        )


def test_the_router_exposes_its_connectors_by_name(tmp_path: Path):
    router, primary, fallback = two_step(tmp_path)
    assert list(router.connectors) == [primary, fallback]
    assert router.default_mode is ConnectorMode.LIVE
    assert router.connector("fallback") is fallback
    with pytest.raises(KeyError):
        _ = router.connector("nobody")


def test_positions_list_every_route_of_a_connector(tmp_path: Path):
    router, _, _ = two_step(tmp_path)
    assert router.positions("fallback") == [
        (CheckType.RFC_VALIDATION, "MX", ConnectorRoute.FALLBACK)
    ]
    assert router.positions("primary") == [
        (CheckType.RFC_VALIDATION, "MX", ConnectorRoute.PRIMARY)
    ]
    assert router.positions("nobody") == []


def test_run_check_needs_a_tenant_bound_session(tmp_path: Path, engine: Engine):
    router, _, _ = two_step(tmp_path)
    with (
        tenant_session(platform_ctx()) as s,
        pytest.raises(ContextMissingError),
    ):
        _ = run_check(s, request(), router=router)
