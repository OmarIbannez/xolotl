"""The ``banxico_sie`` connector over plan 2a's unit-value sources."""

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import Engine

from tests.helpers import make_tenant_with_keys, staff_session, system_ctx
from tests.platform.connectors_support import evidence_of, make_deps
from xolotl.platform.config import get_settings
from xolotl.platform.connectors import (
    BanxicoSieConnector,
    CallOutcome,
    CheckType,
    CircuitState,
    ConnectorDeps,
    ConnectorMode,
    ConnectorRequest,
    ConnectorRoute,
    CredentialVault,
    InterpretationError,
    VerificationOutcome,
    default_router,
    run_check,
)
from xolotl.platform.connectors.banxico import ObservationPairs, interpret_body
from xolotl.platform.connectors.defaults import rows_to_pairs, seed_lookup_from
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Confidence, TenantEnv
from xolotl.platform.ids import uuid7
from xolotl.platform.storage.local import LocalFsObjectStore
from xolotl.platform.units import (
    SeedFileSource,
    UnitSourceAuthError,
    UnitSourceError,
    UnitValue,
)

REPO = Path(__file__).resolve().parents[2]
ON = "2026-10-19"
PLATFORM = "platform-token"
SEED: Mapping[tuple[str, date], Decimal] = {
    ("UDI", date(2026, 10, 19)): Decimal("8.700000")
}


def seed_lookup(unit_code: str, on: date) -> Decimal | None:
    return SEED.get((unit_code, on))


class FakeFetch:
    """The fetch callable the connector consumes."""

    def __init__(
        self, rows: ObservationPairs = (), raises: Exception | None = None
    ) -> None:
        self.rows: ObservationPairs = rows
        self.raises: Exception | None = raises
        self.calls: list[tuple[str, date, date]] = []

    def __call__(self, unit_code: str, start: date, end: date) -> ObservationPairs:
        self.calls.append((unit_code, start, end))
        if self.raises is not None:
            raise self.raises
        return self.rows


class RecordingFactory:
    """The fetch factory; it records the token it is built with."""

    def __init__(self, fetch: FakeFetch) -> None:
        self.fetch: FakeFetch = fetch
        self.tokens: list[str] = []

    def __call__(self, token: str) -> FakeFetch:
        self.tokens.append(token)
        return self.fetch


@pytest.fixture
def prod(engine: Engine) -> UUID:
    return make_tenant_with_keys("prod", env=TenantEnv.PROD)


@pytest.fixture
def sandbox(engine: Engine) -> UUID:
    return make_tenant_with_keys("sbx", env=TenantEnv.SANDBOX)


@pytest.fixture
def deps(tmp_path: Path) -> ConnectorDeps:
    built, _, _ = make_deps(tmp_path)
    return built


def run_unit(
    connector: BanxicoSieConnector,
    tenant_id: UUID,
    *,
    params: Mapping[str, str] | None = None,
    mode: ConnectorMode = ConnectorMode.LIVE,
) -> VerificationOutcome:
    with tenant_session(system_ctx(tenant_id)) as s:
        return connector.run(
            s,
            ConnectorRequest(
                CheckType.UNIT_VALUE, None, params or {"unit_code": "UDI", "on": ON}
            ),
            mode=mode,
        )


def build(
    deps: ConnectorDeps,
    factory: RecordingFactory,
    *,
    platform_token: str | None = PLATFORM,
    prod: bool = False,
) -> BanxicoSieConnector:
    return BanxicoSieConnector(
        deps,
        fetch_factory=factory,
        seed_lookup=seed_lookup,
        platform_token=platform_token,
        prod=prod,
    )


def test_a_vault_token_wins_over_the_platform_token_and_the_latest_prior_value_is_taken(
    tmp_path: Path, prod: UUID, deps: ConnectorDeps
):
    fetch = FakeFetch(
        rows=[
            (date(2026, 10, 16), Decimal("8.650000")),
            (date(2026, 10, 15), Decimal("8.600000")),
        ]
    )
    factory = RecordingFactory(fetch)
    connector = build(deps, factory)
    with staff_session(prod, uuid7()) as s:
        _ = CredentialVault().set(
            s, "banxico_sie", ConnectorMode.LIVE, {"token": "tenant-token"}
        )
    out = run_unit(connector, prod)
    assert factory.tokens == ["tenant-token"]
    assert fetch.calls == [("UDI", date(2026, 10, 9), date(2026, 10, 19))]
    assert (out.outcome, out.route, out.attempts) == (
        CallOutcome.PASS,
        ConnectorRoute.PRIMARY,
        1,
    )
    assert out.details == {
        "unit_code": "UDI",
        "on": ON,
        "value": "8.650000",
        "value_date": "2026-10-16",
        "source": "banxico_sie",
    }
    assert json.loads(evidence_of(tmp_path / "objects", prod, out)) == {
        "observations": [
            {"date": "2026-10-15", "value": "8.600000"},
            {"date": "2026-10-16", "value": "8.650000"},
        ],
        "on": ON,
        "unit_code": "UDI",
    }


def test_the_platform_token_is_used_when_the_vault_is_empty(
    prod: UUID, deps: ConnectorDeps
):
    factory = RecordingFactory(
        FakeFetch(rows=[(date(2026, 10, 19), Decimal("8.660000"))])
    )
    out = run_unit(build(deps, factory), prod)
    assert factory.tokens == ["platform-token"]
    assert (out.outcome, out.details["value"]) == (CallOutcome.PASS, "8.660000")


@pytest.mark.parametrize(
    ("unit_code", "value"), [("UDI", "8.700000"), ("USD_MXN_FIX", "18.500000")]
)
def test_no_token_outside_prod_answers_from_the_demo_values(
    prod: UUID, deps: ConnectorDeps, unit_code: str, value: str
):
    factory = RecordingFactory(FakeFetch())
    connector = build(deps, factory, platform_token=None)
    out = run_unit(connector, prod, params={"unit_code": unit_code, "on": ON})
    assert (
        out.outcome,
        out.route,
        out.attempts,
        out.details["value"],
        out.details["value_date"],
    ) == (CallOutcome.PASS, ConnectorRoute.PRIMARY, 1, value, ON)
    assert factory.tokens == []


def test_no_token_in_prod_is_refused(prod: UUID, deps: ConnectorDeps):
    factory = RecordingFactory(FakeFetch())
    connector = build(deps, factory, platform_token=None, prod=True)
    out = run_unit(connector, prod)
    assert out.outcome is CallOutcome.ERROR
    assert "CredentialMissingError" in (out.error or "")
    assert factory.tokens == []


def test_no_observation_is_inconclusive(prod: UUID, deps: ConnectorDeps):
    out = run_unit(build(deps, RecordingFactory(FakeFetch(rows=[]))), prod)
    assert (out.outcome, out.details["reason"]) == (
        CallOutcome.INCONCLUSIVE,
        "NO_OBSERVATION",
    )


@pytest.mark.parametrize(
    ("params", "key"),
    [
        ({"unit_code": "UMA", "on": ON}, "unit_code"),
        ({"unit_code": "UDI", "on": "yesterday"}, "on"),
        ({"unit_code": "UDI"}, "on"),
    ],
)
def test_bad_params_are_errors(
    prod: UUID, deps: ConnectorDeps, params: Mapping[str, str], key: str
):
    factory = RecordingFactory(FakeFetch())
    out = run_unit(build(deps, factory), prod, params=params)
    assert (out.outcome, out.attempts) == (CallOutcome.ERROR, 1)
    assert "PermanentCallError" in (out.error or "")
    assert key in (out.error or "")
    assert factory.tokens == []


def test_a_fetch_transport_error_is_retried_and_a_platform_error_is_wrapped_as_transient(  # ruff: ignore[line-too-long]
    tmp_path: Path, prod: UUID
):
    out: VerificationOutcome | None = None
    raised: Exception
    for raised in (
        httpx.ConnectError("down"),
        UnitSourceError("Banxico SIE is unreachable, try again shortly"),
    ):
        factory = RecordingFactory(FakeFetch(raises=raised))
        deps, _, sleep = make_deps(tmp_path / raised.__class__.__name__)
        out = run_unit(build(deps, factory), prod)
        assert (out.outcome, out.attempts, sleep.calls) == (
            CallOutcome.ERROR,
            3,
            [0.2, 0.5],
        )
    assert out is not None
    assert "TransientCallError" in (out.error or "")


def test_a_rejected_token_is_a_permanent_error_without_retry(
    tmp_path: Path, prod: UUID
):
    factory = RecordingFactory(
        FakeFetch(
            raises=UnitSourceAuthError(
                "Banxico rejected the API token (check XOLOTL_BANXICO_TOKEN)"
            )
        )
    )
    deps, _, sleep = make_deps(tmp_path)
    connector = build(deps, factory)
    out = run_unit(connector, prod)
    assert (out.outcome, out.attempts, sleep.calls) == (CallOutcome.ERROR, 1, [])
    assert "PermanentCallError" in (out.error or "")
    assert "token" in (out.error or "")
    assert connector.circuit_state("mx-1") is CircuitState.CLOSED


@pytest.mark.parametrize(
    "body", [b"not json", b'{"unit_code": "UDI", "on": "2026-10-19"}']
)
def test_unreadable_observations_are_interpretation_errors(body: bytes):
    with pytest.raises(InterpretationError):
        _ = interpret_body(body)


def test_rows_to_pairs_reads_plan_2a_rows():
    row = UnitValue(
        unit_code="UDI",
        valid_from=date(2026, 10, 16),
        valid_to=date(2026, 10, 16),
        value=Decimal("8.650000"),
        quote_ccy="MXN",
        source="BANXICO_SIE:SP68257",
        confidence=Confidence.VERIFIED,
    )
    assert rows_to_pairs([row]) == [(date(2026, 10, 16), Decimal("8.650000"))]


def test_seed_lookup_from_reads_plan_2a_seed_files():
    lookup = seed_lookup_from(
        SeedFileSource(REPO / "policy-packs" / "_units", allow_unverified=True)
    )
    seeded = lookup("UDI", date(2026, 1, 20))
    assert seeded is not None
    assert seeded > 0
    assert lookup("USD_MXN_FIX", date(2026, 1, 20)) is not None
    assert lookup("UDI", date(2026, 10, 19)) is None


@pytest.mark.parametrize(
    ("unit_code", "value"), [("UDI", "8.700000"), ("USD_MXN_FIX", "18.500000")]
)
def test_a_sandbox_tenant_gets_the_mock_values(
    sandbox: UUID, deps: ConnectorDeps, unit_code: str, value: str
):
    factory = RecordingFactory(FakeFetch())
    out = run_unit(
        build(deps, factory), sandbox, params={"unit_code": unit_code, "on": ON}
    )
    assert (
        out.mode,
        out.route,
        out.attempts,
        out.cost_micros,
        out.details["value"],
        out.details["value_date"],
    ) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0, 0, value, ON)
    assert factory.tokens == []


def test_run_check_through_the_default_router_gives_a_sandbox_tenant_the_seed_value(
    tmp_path: Path, sandbox: UUID
):
    # The sandbox UNIT_VALUE check and GET /v1/units/UDI?on= read the same seed file.
    settings = get_settings()
    expected = (
        SeedFileSource(settings.packs_dir / "_units", allow_unverified=True)
        .fetch("UDI", date(2026, 1, 20), date(2026, 1, 20))[0]
        .value
    )
    router = default_router(settings, store=LocalFsObjectStore(tmp_path / "objects"))
    with tenant_session(system_ctx(sandbox)) as s:
        out = run_check(
            s,
            ConnectorRequest(
                CheckType.UNIT_VALUE, None, {"unit_code": "UDI", "on": "2026-01-20"}
            ),
            router=router,
        )
    assert (out.connector, out.outcome, out.mode, out.route) == (
        "banxico_sie",
        CallOutcome.PASS,
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
    )
    assert (out.details["value"], out.details["value_date"]) == (
        str(expected),
        "2026-01-20",
    )
