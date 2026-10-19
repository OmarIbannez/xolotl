"""The ``sat_rfc`` connector: an RFC checked against the SAT's CFDI status service.

The SAT publishes no public RFC lookup, so a live check asks the CFDI status service
(the SOAP service behind the QR code on every CFDI, no credentials) about a CFDI the
subject RFC issued or received. The issuer being on the SAT's 69-B list or the CFDI
being cancelled fails the check. Ported from Xolo's ``providers/sat.py``.

The sandbox mock answers by the RFC's last character with the SOAP body the live
service sends, so live answers and mocks go through one parser.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import ClassVar, override
from uuid import UUID
from xml.sax.saxutils import escape

import httpx
from defusedxml import DefusedXmlException, ElementTree

from xolotl.platform.connectors.base import Connector, ConnectorDeps
from xolotl.platform.connectors.types import (
    CheckResult,
    ConnectorRequest,
    InterpretationError,
    PermanentCallError,
    RawResponse,
)
from xolotl.platform.enums import CheckType
from xolotl.platform.text import RFC_SHAPE

SAT_RFC = "sat_rfc"
SAT_URL = "https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc"
SOAP_ACTION = "http://tempuri.org/IConsultaCFDIService/Consulta"
RESULT_NS = (
    "{http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio}"
)
FIELDS = (
    "CodigoEstatus",
    "Estado",
    "EsCancelable",
    "EstatusCancelacion",
    "ValidacionEFOS",
)
SEAL_TAIL = re.compile(r"^[A-Za-z0-9+/=]{8}$")
EFOS_LISTED = "100"
EFOS_CLEAR = "200"

_FOUND = "S - Comprobante obtenido satisfactoriamente."
_NOT_FOUND = "N - 602: Comprobante no encontrado."
_VIGENTE = "Vigente"
_CANCELADO = "Cancelado"
_NO_ENCONTRADO = "No Encontrado"


@dataclass(frozen=True, slots=True)
class CfdiQuery:
    """A CFDI to look up, and the subject RFC that issued or received it."""

    rfc: str
    uuid: UUID
    issuer_rfc: str
    receiver_rfc: str
    total: Decimal
    seal_last8: str | None = None


def _required(params: Mapping[str, str], key: str) -> str:
    value = params.get(key)
    if value is None or not value.strip():
        msg = f"{key} is missing"
        raise PermanentCallError(msg)
    return value.strip()


def _rfc(params: Mapping[str, str], key: str) -> str:
    value = _required(params, key).upper()
    if RFC_SHAPE.fullmatch(value) is None:
        msg = f"{key} is not shaped like an RFC"
        raise PermanentCallError(msg)
    return value


def _uuid(params: Mapping[str, str]) -> UUID:
    try:
        return UUID(_required(params, "cfdi_uuid"))
    except ValueError as exc:
        msg = "cfdi_uuid is not a UUID"
        raise PermanentCallError(msg) from exc


def _total(params: Mapping[str, str]) -> Decimal:
    try:
        total = Decimal(_required(params, "total"))
    except InvalidOperation as exc:
        msg = "total is not a decimal number"
        raise PermanentCallError(msg) from exc
    if not total.is_finite() or total < 0:
        msg = "total must be a finite number, zero or positive"
        raise PermanentCallError(msg)
    return total


def _seal(params: Mapping[str, str]) -> str | None:
    seal = params.get("seal_last8")
    if seal is None:
        return None
    seal = seal.strip()
    if SEAL_TAIL.fullmatch(seal) is None:
        msg = "seal_last8 must be the last 8 characters of the CFDI's Sello"
        raise PermanentCallError(msg)
    return seal


def cfdi_query_from(params: Mapping[str, str]) -> CfdiQuery:
    """Read and normalise a live request's parameters.

    Returns:
        The query, its RFCs stripped and upper-cased.

    Raises:
        PermanentCallError: When a key is missing or malformed, or the subject RFC
            is neither the CFDI's issuer nor its receiver; the message names the key.

    """
    rfc = _rfc(params, "rfc")
    uuid = _uuid(params)
    issuer = _rfc(params, "issuer_rfc")
    receiver = _rfc(params, "receiver_rfc")
    total = _total(params)
    seal = _seal(params)
    if rfc not in {issuer, receiver}:
        msg = "rfc is not the CFDI's issuer or receiver"
        raise PermanentCallError(msg)
    return CfdiQuery(rfc, uuid, issuer, receiver, total, seal)


def format_total(total: Decimal) -> str:
    """Format a CFDI total the QR way: up to 6 decimals, no trailing zeros.

    Returns:
        The total with at least one decimal.

    """
    text = f"{total:.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def expression(query: CfdiQuery) -> str:
    """Build the ``expresionImpresa`` the SAT service expects (the QR payload).

    Returns:
        The payload; an ``&`` in an RFC, which is the separator here, is ``&amp;``.

    """
    issuer = query.issuer_rfc.replace("&", "&amp;")
    receiver = query.receiver_rfc.replace("&", "&amp;")
    total = format_total(query.total)
    text = f"?re={issuer}&rr={receiver}&tt={total}&id={str(query.uuid).upper()}"
    if query.seal_last8:
        text += f"&fe={query.seal_last8}"
    return text


def envelope(query: CfdiQuery) -> str:
    """Wrap the query's expression in the SOAP 1.1 request envelope.

    Returns:
        The envelope.

    """
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:tem="http://tempuri.org/"><soapenv:Header/><soapenv:Body>'
        "<tem:Consulta><tem:expresionImpresa><![CDATA["
        f"{expression(query)}"
        "]]></tem:expresionImpresa></tem:Consulta></soapenv:Body></soapenv:Envelope>"
    )


def parse_response(payload: bytes) -> dict[str, str]:
    """Read the result fields of a SAT answer.

    Returns:
        Each of ``FIELDS`` found under the result namespace, stripped.

    Raises:
        InterpretationError: When the payload is not XML (or hostile XML) or carries
            no ``Estado``.

    """
    try:
        root = ElementTree.fromstring(payload)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        msg = "the SAT answer is not valid XML"
        raise InterpretationError(msg) from exc
    result = {
        name: (node.text or "").strip()
        for name in FIELDS
        if (node := root.find(f".//{RESULT_NS}{name}")) is not None
    }
    if "Estado" not in result:
        msg = "the SAT answer has no invoice state"
        raise InterpretationError(msg)
    return result


def interpret_fields(fields: Mapping[str, str]) -> CheckResult:
    """Turn the SAT's fields into a check result.

    Returns:
        ``FAIL`` when the issuer is on the 69-B list or the CFDI is cancelled,
        ``PASS`` when it is valid, ``INCONCLUSIVE`` otherwise.

    """
    estado = fields.get("Estado", "")
    efos = fields.get("ValidacionEFOS", "")
    if efos == EFOS_LISTED:
        outcome, reason = "FAIL", "EFOS_LISTED"
    elif estado == _CANCELADO:
        outcome, reason = "FAIL", "CFDI_CANCELLED"
    elif estado == _VIGENTE:
        outcome, reason = "PASS", "VIGENTE"
    elif estado == _NO_ENCONTRADO:
        outcome, reason = "INCONCLUSIVE", "CFDI_NOT_FOUND"
    else:
        outcome, reason = "INCONCLUSIVE", "UNKNOWN_STATE"
    return CheckResult(
        outcome=outcome,
        details={
            "estado": estado,
            "codigo_estatus": fields.get("CodigoEstatus", ""),
            "es_cancelable": fields.get("EsCancelable", ""),
            "estatus_cancelacion": fields.get("EstatusCancelacion", ""),
            "validacion_efos": efos,
            "reason": reason,
        },
    )


def mock_fields(rfc: str) -> dict[str, str]:
    """Pick the mock's answer by the RFC's last character.

    Returns:
        ``7``: on the 69-B list; ``8``: cancelled; ``9``: not found; else valid.

    """
    match rfc[-1:]:
        case "7":
            return {
                "CodigoEstatus": _FOUND,
                "EsCancelable": "Cancelable sin aceptación",
                "Estado": _VIGENTE,
                "EstatusCancelacion": "",
                "ValidacionEFOS": EFOS_LISTED,
            }
        case "8":
            return {
                "CodigoEstatus": _FOUND,
                "EsCancelable": "No cancelable",
                "Estado": _CANCELADO,
                "EstatusCancelacion": "Cancelado con aceptación",
                "ValidacionEFOS": EFOS_CLEAR,
            }
        case "9":
            return {"CodigoEstatus": _NOT_FOUND, "Estado": _NO_ENCONTRADO}
        case _:
            return {
                "CodigoEstatus": _FOUND,
                "EsCancelable": "Cancelable con aceptación",
                "Estado": _VIGENTE,
                "EstatusCancelacion": "",
                "ValidacionEFOS": EFOS_CLEAR,
            }


def mock_envelope(fields: Mapping[str, str]) -> bytes:
    """Build the SOAP answer the live service sends, carrying ``fields``.

    Returns:
        The UTF-8 body.

    """
    result = "".join(
        f"<a:{name}>{escape(fields[name])}</a:{name}>"
        for name in FIELDS
        if name in fields
    )
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        '<ConsultaResponse xmlns="http://tempuri.org/"><ConsultaResult '
        f'xmlns:a="{RESULT_NS[1:-1]}" '
        'xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        f"{result}</ConsultaResult></ConsultaResponse></s:Body></s:Envelope>"
    ).encode()


class SatRfcConnector(Connector):
    """``RFC_VALIDATION`` through the SAT's CFDI status service."""

    evidence_content_type: ClassVar[str] = "text/xml"

    def __init__(
        self, deps: ConnectorDeps, *, client: httpx.Client | None = None
    ) -> None:
        super().__init__(
            deps, name=SAT_RFC, check_types=frozenset({CheckType.RFC_VALIDATION})
        )
        # One client per connector, kept for the life of the process.
        self._client: httpx.Client = client or httpx.Client(
            timeout=httpx.Timeout(self.timeout_s)
        )

    @override
    def _call(
        self, request: ConnectorRequest, credentials: Mapping[str, str]
    ) -> RawResponse:
        # The SAT service takes no credentials.
        query = cfdi_query_from(request.params)
        response = self._client.post(
            SAT_URL,
            content=envelope(query).encode(),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{SOAP_ACTION}"',
            },
        )
        return RawResponse(
            response.status_code,
            response.content,
            dict(response.headers),
            int(response.elapsed.total_seconds() * 1000),
        )

    @override
    def _interpret(self, raw: RawResponse) -> CheckResult:
        return interpret_fields(parse_response(raw.body))

    @override
    def _mock(self, request: ConnectorRequest) -> RawResponse:
        rfc = _rfc(request.params, "rfc")
        return RawResponse(
            200,
            mock_envelope(mock_fields(rfc)),
            {"content-type": "text/xml; charset=utf-8"},
            0,
        )
