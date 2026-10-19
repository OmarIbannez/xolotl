"""The ``sat_rfc`` connector: the ported SAT functions, live calls and the mock."""

from collections.abc import Generator, Mapping
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import Engine

from tests.helpers import make_tenant_with_keys, system_ctx
from tests.platform.connectors_support import calls_of, evidence_of, make_deps
from xolotl.platform.config import get_settings
from xolotl.platform.connectors import (
    CallOutcome,
    CheckType,
    ConnectorDeps,
    ConnectorMode,
    ConnectorRequest,
    ConnectorRoute,
    InterpretationError,
    PermanentCallError,
    SatRfcConnector,
    VerificationOutcome,
    default_router,
    run_check,
)
from xolotl.platform.connectors.sat import (
    SAT_URL,
    SOAP_ACTION,
    cfdi_query_from,
    envelope,
    expression,
    format_total,
    interpret_fields,
    mock_envelope,
    mock_fields,
    parse_response,
)
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import TenantEnv
from xolotl.platform.storage.local import LocalFsObjectStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors"
QUERY = {
    "rfc": "AAA010101AAA",
    "cfdi_uuid": "6128396f-c09b-4ec6-8699-43c5f7e3b230",
    "issuer_rfc": "AAA010101AAA",
    "receiver_rfc": "XAXX010101000",
    "total": "1160.00",
}
EMPTY_ENVELOPE = (
    b"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'>"
    b"<s:Body/></s:Envelope>"
)


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


@pytest.fixture
def connector(deps: ConnectorDeps) -> SatRfcConnector:
    return SatRfcConnector(deps)


@pytest.fixture
def sat() -> Generator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def run_rfc(
    connector: SatRfcConnector,
    tenant_id: UUID,
    params: Mapping[str, str],
    *,
    mode: ConnectorMode = ConnectorMode.LIVE,
) -> VerificationOutcome:
    with tenant_session(system_ctx(tenant_id)) as s:
        return connector.run(
            s, ConnectorRequest(CheckType.RFC_VALIDATION, None, params), mode=mode
        )


@pytest.mark.parametrize(
    ("total", "text"),
    [
        ("1160.00", "1160.0"),
        ("100", "100.0"),
        ("0.123456", "0.123456"),
        ("12.5", "12.5"),
    ],
)
def test_format_total_follows_the_cfdi_qr_rule(total: str, text: str):
    assert format_total(Decimal(total)) == text


def test_expression_and_envelope_match_the_qr_payload():
    query = cfdi_query_from(QUERY)
    assert expression(query) == (
        "?re=AAA010101AAA&rr=XAXX010101000&tt=1160.0"
        "&id=6128396F-C09B-4EC6-8699-43C5F7E3B230"
    )
    sealed = cfdi_query_from({**QUERY, "seal_last8": "AbCd1234"})
    assert expression(sealed).endswith("&fe=AbCd1234")
    assert "<![CDATA[?re=AAA010101AAA" in envelope(query)
    assert 'xmlns:tem="http://tempuri.org/"' in envelope(query)


def test_the_query_strips_and_upper_cases_the_rfcs():
    query = cfdi_query_from({
        **QUERY,
        "rfc": " aaa010101aaa ",
        "receiver_rfc": "xaxx010101000",
    })
    assert (query.rfc, query.issuer_rfc, query.receiver_rfc) == (
        "AAA010101AAA",
        "AAA010101AAA",
        "XAXX010101000",
    )
    assert query.seal_last8 is None


def test_an_ampersand_in_an_rfc_is_escaped_in_the_expression_but_matched_as_the_subject():  # ruff: ignore[line-too-long]
    query = cfdi_query_from({
        **QUERY,
        "rfc": "A&A010101AAA",
        "issuer_rfc": "A&A010101AAA",
    })
    assert query.rfc == "A&A010101AAA"
    assert expression(query).startswith("?re=A&amp;A010101AAA&rr=")


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({k: v for k, v in QUERY.items() if k != "cfdi_uuid"}, "cfdi_uuid"),
        ({**QUERY, "cfdi_uuid": "nope"}, "cfdi_uuid"),
        ({**QUERY, "rfc": "ABC"}, "rfc"),
        ({**QUERY, "issuer_rfc": "ABC"}, "issuer_rfc"),
        ({**QUERY, "total": "-1"}, "total"),
        ({**QUERY, "total": "ten"}, "total"),
        ({**QUERY, "total": "NaN"}, "total"),
        ({**QUERY, "seal_last8": "x"}, "seal_last8"),
        ({**QUERY, "rfc": "ZZZ010101ZZZ"}, "issuer or receiver"),
    ],
)
def test_a_bad_query_is_a_permanent_error(params: dict[str, str], match: str):
    with pytest.raises(PermanentCallError, match=match):
        _ = cfdi_query_from(params)


@pytest.mark.parametrize(
    ("fixture", "outcome", "reason"),
    [
        ("sat_vigente.xml", "PASS", "VIGENTE"),
        ("sat_efos.xml", "FAIL", "EFOS_LISTED"),
        ("sat_cancelado.xml", "FAIL", "CFDI_CANCELLED"),
        ("sat_no_encontrado.xml", "INCONCLUSIVE", "CFDI_NOT_FOUND"),
    ],
)
def test_recorded_answers_interpret_as_documented(
    fixture: str, outcome: str, reason: str
):
    result = interpret_fields(parse_response((FIXTURES / fixture).read_bytes()))
    assert (result.outcome, result.details["reason"]) == (outcome, reason)
    assert result.details["estado"] in {"Vigente", "Cancelado", "No Encontrado"}


def test_the_details_carry_every_field_as_a_string():
    fields = parse_response((FIXTURES / "sat_no_encontrado.xml").read_bytes())
    assert interpret_fields(fields).details == {
        "estado": "No Encontrado",
        "codigo_estatus": "N - 602: Comprobante no encontrado.",
        "es_cancelable": "",
        "estatus_cancelacion": "",
        "validacion_efos": "",
        "reason": "CFDI_NOT_FOUND",
    }


def test_an_unknown_state_is_inconclusive():
    result = interpret_fields({"Estado": "Extraño"})
    assert (result.outcome, result.details["reason"]) == (
        "INCONCLUSIVE",
        "UNKNOWN_STATE",
    )


@pytest.mark.parametrize("payload", [b"<html>503</html>", b"\x00\x01", EMPTY_ENVELOPE])
def test_unreadable_answers_are_interpretation_errors(payload: bytes):
    with pytest.raises(InterpretationError):
        _ = parse_response(payload)


@pytest.mark.parametrize(
    ("rfc", "outcome", "reason"),
    [
        ("XAXX010101000", "PASS", "VIGENTE"),
        ("ABC010101AAA", "PASS", "VIGENTE"),
        ("ABC010101AA7", "FAIL", "EFOS_LISTED"),
        ("ABC010101AA8", "FAIL", "CFDI_CANCELLED"),
        ("ABC010101AA9", "INCONCLUSIVE", "CFDI_NOT_FOUND"),
    ],
)
def test_the_mock_is_keyed_by_the_last_character(rfc: str, outcome: str, reason: str):
    result = interpret_fields(parse_response(mock_envelope(mock_fields(rfc))))
    assert (result.outcome, result.details["reason"]) == (outcome, reason)


def test_a_live_call_posts_the_envelope_and_passes(
    tmp_path: Path, prod: UUID, connector: SatRfcConnector, sat: respx.MockRouter
):
    body = (FIXTURES / "sat_vigente.xml").read_bytes()
    route = sat.post(SAT_URL).mock(
        return_value=httpx.Response(
            200, content=body, headers={"content-type": "text/xml; charset=utf-8"}
        )
    )
    out = run_rfc(connector, prod, QUERY)
    assert (out.outcome, out.attempts, out.details["reason"]) == (
        CallOutcome.PASS,
        1,
        "VIGENTE",
    )
    assert (out.route, out.mode, out.cost_micros) == (
        ConnectorRoute.PRIMARY,
        ConnectorMode.LIVE,
        0,
    )
    sent = route.calls.last.request
    assert sent.content == envelope(cfdi_query_from(QUERY)).encode()
    assert (sent.headers["content-type"], sent.headers["soapaction"]) == (
        "text/xml; charset=utf-8",
        f'"{SOAP_ACTION}"',
    )
    assert evidence_of(tmp_path / "objects", prod, out) == body


def test_a_500_is_retried_three_times_then_an_error(
    prod: UUID, connector: SatRfcConnector, sat: respx.MockRouter
):
    route = sat.post(SAT_URL).mock(
        return_value=httpx.Response(500, content=b"<html>down</html>")
    )
    out = run_rfc(connector, prod, QUERY)
    assert (out.outcome, out.attempts, out.error) == (CallOutcome.ERROR, 3, "HTTP 500")
    assert route.call_count == 3


def test_a_timeout_is_a_timeout_outcome(
    prod: UUID, connector: SatRfcConnector, sat: respx.MockRouter
):
    route = sat.post(SAT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    out = run_rfc(connector, prod, QUERY)
    assert (out.outcome, out.attempts) == (CallOutcome.TIMEOUT, 3)
    assert route.call_count == 3


def test_a_bad_live_query_makes_no_request(
    prod: UUID, connector: SatRfcConnector, sat: respx.MockRouter
):
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=b""))
    out = run_rfc(connector, prod, {**QUERY, "rfc": "ZZZ010101ZZZ"})
    assert (out.outcome, out.attempts) == (CallOutcome.ERROR, 1)
    assert "PermanentCallError" in (out.error or "")
    assert route.call_count == 0


@pytest.mark.parametrize("payload", [b"<html>maintenance</html>", EMPTY_ENVELOPE])
def test_a_200_with_an_unreadable_body_is_an_error_outcome(
    tmp_path: Path,
    prod: UUID,
    connector: SatRfcConnector,
    sat: respx.MockRouter,
    payload: bytes,
):
    _ = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=payload))
    out = run_rfc(connector, prod, QUERY)
    assert (out.outcome, out.attempts) == (CallOutcome.ERROR, 1)
    assert "InterpretationError" in (out.error or "")
    assert evidence_of(tmp_path / "objects", prod, out) == payload


def test_a_sandbox_tenant_never_reaches_the_sat_service(
    tmp_path: Path, sandbox: UUID, connector: SatRfcConnector, sat: respx.MockRouter
):
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=b""))
    out = run_rfc(connector, sandbox, {"rfc": "ABC010101AA8"}, mode=ConnectorMode.LIVE)
    assert (out.outcome, out.mode, out.route, out.details["reason"]) == (
        CallOutcome.FAIL,
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        "CFDI_CANCELLED",
    )
    assert route.call_count == 0
    (row,) = calls_of(sandbox)
    assert (row.mode, row.route, row.cost_micros, row.evidence_key) == (
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        0,
        f"connectors/sat_rfc/{out.call_id}",
    )
    assert evidence_of(tmp_path / "objects", sandbox, out) == mock_envelope(
        mock_fields("ABC010101AA8")
    )


def test_run_check_through_the_default_router_gives_a_sandbox_tenant_the_mock_and_a_metering_row(  # ruff: ignore[line-too-long]
    tmp_path: Path, sandbox: UUID, sat: respx.MockRouter
):
    # The exit criterion of spec §13, through the router the composition root builds.
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=b""))
    router = default_router(
        get_settings(), store=LocalFsObjectStore(tmp_path / "objects")
    )
    with tenant_session(system_ctx(sandbox)) as s:
        out = run_check(
            s,
            ConnectorRequest(CheckType.RFC_VALIDATION, None, {"rfc": "ABC010101AA8"}),
            router=router,
            mode=ConnectorMode.LIVE,
        )
    assert (out.connector, out.outcome, out.mode, out.route, out.details["reason"]) == (
        "sat_rfc",
        CallOutcome.FAIL,
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        "CFDI_CANCELLED",
    )
    (row,) = calls_of(sandbox)
    assert (
        row.id,
        row.mode,
        row.route,
        row.outcome,
        row.cost_micros,
        row.evidence_key,
    ) == (
        out.call_id,
        ConnectorMode.SANDBOX,
        ConnectorRoute.MOCK,
        CallOutcome.FAIL,
        0,
        f"connectors/sat_rfc/{out.call_id}",
    )
    assert evidence_of(tmp_path / "objects", sandbox, out) == mock_envelope(
        mock_fields("ABC010101AA8")
    )
    assert route.call_count == 0


@pytest.mark.parametrize("params", [{"rfc": "nope"}, {}])
def test_a_malformed_rfc_is_an_error_in_sandbox_too(
    sandbox: UUID, connector: SatRfcConnector, params: dict[str, str]
):
    out = run_rfc(connector, sandbox, params, mode=ConnectorMode.SANDBOX)
    assert (out.outcome, out.route) == (CallOutcome.ERROR, ConnectorRoute.MOCK)
    assert "PermanentCallError" in (out.error or "")
    assert out.evidence_key is None
