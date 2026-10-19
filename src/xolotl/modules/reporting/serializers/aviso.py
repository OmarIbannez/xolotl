"""LFPIORPI aviso ``XML_XSD`` serializer for virtual-asset activity (fr. XVI).

The input types mirror the official ``avi.xsd``: one ``AvisoInforme`` per month
and filer holds zero or more ``Aviso``; with none it is the informe en ceros.
Text is upper-cased with Ñ kept and other diacritics stripped, amounts are
``Decimal``, instants are rendered in ``America/Mexico_City``. Every file part is
validated against the vendored XSD, whose SHA-256 is checked first, and nothing
is returned that does not validate (FR-RR-002, FR-RR-004, FR-RR-018).

Parts: SAT accepts XML files of at most 2 MB (``inst_excel.pdf``), so a month
whose avisos do not fit is split at aviso boundaries into several complete
files, each with the same ``mes_reportado`` and ``sujeto_obligado``.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from lxml import etree

from xolotl.modules.reporting.serializers import normalizers as norm
from xolotl.modules.reporting.serializers.definitions import (
    XmlDefinition,
    ZeroReportRule,
)
from xolotl.modules.reporting.serializers.errors import (
    ReportFieldError,
    ZeroReportNotAllowedError,
)
from xolotl.modules.reporting.serializers.result import SerializedReport
from xolotl.platform.errors import ValidationFailedError

_MAX_ERRORS = 10


class SchemaInvalidError(ValidationFailedError):
    """The rendered XML does not validate against the official schema.

    The detail lists line, element path and error kind only, never the value.
    """

    default_code: ClassVar[str] = "REPORT_SCHEMA_INVALID"
    default_detail: ClassVar[str] = "the report does not validate against its schema"


class SchemaUnavailableError(ValidationFailedError):
    default_code: ClassVar[str] = "REPORT_SCHEMA_UNAVAILABLE"
    default_detail: ClassVar[str] = "the official schema for this version is missing"


class ReportTooLargeError(ValidationFailedError):
    default_code: ClassVar[str] = "REPORT_TOO_LARGE"
    default_detail: ClassVar[str] = "one aviso is larger than the file size limit"


class Priority(StrEnum):
    NORMAL = "1"
    URGENTE = "2"


# ---- People -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdDocument:
    kind: str
    number: str


@dataclass(frozen=True, slots=True)
class Representative:
    """The legal representative of a company or the delegate of a trust."""

    first_names: str
    paternal_surname: str
    maternal_surname: str
    document: IdDocument
    birth_date: date | None = None
    rfc: str | None = None
    curp: str | None = None


@dataclass(frozen=True, slots=True)
class NaturalPerson:
    """A persona física.

    ``economic_activity`` and ``document`` are required for the reported client
    and omitted for a beneficial owner, as the XSD has it.
    """

    first_names: str
    paternal_surname: str
    maternal_surname: str
    nationality: str
    birth_date: date | None = None
    rfc: str | None = None
    curp: str | None = None
    economic_activity: str | None = None
    document: IdDocument | None = None


@dataclass(frozen=True, slots=True)
class LegalPerson:
    """A persona moral; ``business_activity`` and ``representative`` as above."""

    name: str
    nationality: str
    incorporation_date: date | None = None
    rfc: str | None = None
    business_activity: str | None = None
    representative: Representative | None = None


@dataclass(frozen=True, slots=True)
class Trust:
    """A fideicomiso; ``delegate`` is required for the reported client."""

    name: str
    rfc: str | None = None
    trust_id: str | None = None
    delegate: Representative | None = None


type Person = NaturalPerson | LegalPerson | Trust


@dataclass(frozen=True, slots=True)
class Counterparty:
    """The owner of the bank account funds come from or go to."""

    name: str
    paternal_surname: str | None = None
    maternal_surname: str | None = None

    @property
    def is_natural(self) -> bool:
        return self.paternal_surname is not None


# ---- Contact ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MexicanAddress:
    neighbourhood: str
    street: str
    exterior_number: str
    postal_code: str
    interior_number: str | None = None


@dataclass(frozen=True, slots=True)
class ForeignAddress:
    country: str
    state: str
    city: str
    neighbourhood: str
    street: str
    exterior_number: str
    postal_code: str
    interior_number: str | None = None


@dataclass(frozen=True, slots=True)
class Phone:
    country: str
    number: str
    email: str


@dataclass(frozen=True, slots=True)
class PlatformAccount:
    user_id: str
    related_account: str
    currency: str
    clabe: str | None = None


@dataclass(frozen=True, slots=True)
class Client:
    """The person the aviso is about, with their platform account."""

    account: PlatformAccount
    person: Person
    address: MexicanAddress | ForeignAddress | None = None
    phone: Phone | None = None


# ---- Operations -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VirtualAsset:
    code: str
    rate_mxn: Decimal
    quantity: Decimal
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    """A purchase or a sale of a virtual asset for fiat."""

    at: datetime
    currency: str
    amount: Decimal
    asset: VirtualAsset
    tx_hash: str


@dataclass(frozen=True, slots=True)
class Exchange:
    at: datetime
    sent: VirtualAsset
    sent_mxn: Decimal
    received: VirtualAsset
    received_mxn: Decimal
    tx_hash: str


@dataclass(frozen=True, slots=True)
class Transfer:
    at: datetime
    amount_mxn: Decimal
    asset: VirtualAsset
    tx_hash: str


@dataclass(frozen=True, slots=True)
class MexicanBankAccount:
    clabe: str
    institution: str


@dataclass(frozen=True, slots=True)
class ForeignBankAccount:
    number: str
    bank: str


@dataclass(frozen=True, slots=True)
class FundsMovement:
    """A fiat withdrawal to, or deposit from, a bank account."""

    at: datetime
    instrument: str
    currency: str
    amount: Decimal
    counterparty: Counterparty
    bank_account: MexicanBankAccount | ForeignBankAccount


@dataclass(frozen=True, slots=True)
class Operations:
    purchases: tuple[Trade, ...] = ()
    sales: tuple[Trade, ...] = ()
    exchanges: tuple[Exchange, ...] = ()
    transfers_sent: tuple[Transfer, ...] = ()
    transfers_received: tuple[Transfer, ...] = ()
    withdrawals: tuple[FundsMovement, ...] = ()
    deposits: tuple[FundsMovement, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.purchases
            or self.sales
            or self.exchanges
            or self.transfers_sent
            or self.transfers_received
            or self.withdrawals
            or self.deposits
        )


# ---- Report -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Aviso:
    reference: str
    alert_type: str
    client: Client
    operations: Operations
    priority: Priority = Priority.NORMAL
    alert_description: str | None = None
    beneficial_owners: tuple[Person, ...] = ()


@dataclass(frozen=True, slots=True)
class ObligatedSubject:
    """The filer: its RFC and platform domain, and its entidad colegiada if any."""

    rfc: str
    platform_domain: str
    collegiate_entity_rfc: str | None = None


@dataclass(frozen=True, slots=True)
class AvisoInforme:
    month: date
    subject: ObligatedSubject
    avisos: tuple[Aviso, ...] = field(default_factory=tuple[Aviso, ...])


# ---- Rendering --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Node:
    """An XML element before it becomes lxml: a tag, its text and children."""

    tag: str
    text: str | None = None
    children: tuple["Node", ...] = ()


def _n(tag: str, *children: "Node | None") -> Node:
    return Node(tag, None, tuple(child for child in children if child is not None))


def _leaf(tag: str, value: str | None) -> Node | None:
    return None if value is None else Node(tag, value)


def _t(value: str | None) -> str | None:
    return None if value is None else norm.aviso_text(value)


def _date(value: date | None) -> str | None:
    return None if value is None else norm.yyyymmdd(value)


def _rfc(value: str | None, where: str) -> str | None:
    return norm.rfc(value, field=where) if value else None


def _curp(value: str | None, where: str) -> str | None:
    return norm.curp(value, field=where) if value else None


def _when(value: datetime) -> str:
    return norm.mx_datetime(value, field="fecha_hora_operacion")


def _money(value: Decimal, where: str) -> str:
    return norm.amount(value, field=where)


def _document(document: IdDocument | None) -> Node | None:
    if document is None:
        return None
    return _n(
        "documento_identificacion",
        _leaf("tipo_identificacion", document.kind),
        _leaf("numero_identificacion", _t(document.number)),
    )


def _representative(tag: str, rep: Representative | None) -> Node | None:
    if rep is None:
        return None
    return _n(
        tag,
        _leaf("nombre", _t(rep.first_names)),
        _leaf("apellido_paterno", _t(rep.paternal_surname)),
        _leaf("apellido_materno", _t(rep.maternal_surname)),
        _leaf("fecha_nacimiento", _date(rep.birth_date)),
        _leaf("rfc", _rfc(rep.rfc, f"{tag}.rfc")),
        _leaf("curp", _curp(rep.curp, f"{tag}.curp")),
        _document(rep.document),
    )


def _person(person: Person, *, full: bool) -> Node:
    # ``full`` renders the client; an owner gets the XSD's simple person.
    match person:
        case NaturalPerson():
            body = _n(
                "persona_fisica",
                _leaf("nombre", _t(person.first_names)),
                _leaf("apellido_paterno", _t(person.paternal_surname)),
                _leaf("apellido_materno", _t(person.maternal_surname)),
                _leaf("fecha_nacimiento", _date(person.birth_date)),
                _leaf("rfc", _rfc(person.rfc, "persona_fisica.rfc")),
                _leaf("curp", _curp(person.curp, "persona_fisica.curp")),
                _leaf("pais_nacionalidad", person.nationality),
                _leaf("actividad_economica", person.economic_activity)
                if full
                else None,
                _document(person.document) if full else None,
            )
        case LegalPerson():
            body = _n(
                "persona_moral",
                _leaf("denominacion_razon", _t(person.name)),
                _leaf("fecha_constitucion", _date(person.incorporation_date)),
                _leaf("rfc", _rfc(person.rfc, "persona_moral.rfc")),
                _leaf("pais_nacionalidad", person.nationality),
                _leaf("giro_mercantil", person.business_activity) if full else None,
                _representative("representante_apoderado", person.representative)
                if full
                else None,
            )
        case Trust():
            body = _n(
                "fideicomiso",
                _leaf("denominacion_razon", _t(person.name)),
                _leaf("rfc", _rfc(person.rfc, "fideicomiso.rfc")),
                _leaf("identificador_fideicomiso", _t(person.trust_id)),
                _representative("apoderado_delegado", person.delegate)
                if full
                else None,
            )
    return _n("tipo_persona", body)


def _address(address: MexicanAddress | ForeignAddress | None) -> Node | None:
    match address:
        case None:
            return None
        case MexicanAddress():
            body = _n(
                "nacional",
                _leaf("colonia", _t(address.neighbourhood)),
                _leaf("calle", _t(address.street)),
                _leaf("numero_exterior", _t(address.exterior_number)),
                _leaf("numero_interior", _t(address.interior_number)),
                _leaf("codigo_postal", address.postal_code),
            )
        case ForeignAddress():
            body = _n(
                "extranjero",
                _leaf("pais", address.country),
                _leaf("estado_provincia", _t(address.state)),
                _leaf("ciudad_poblacion", _t(address.city)),
                _leaf("colonia", _t(address.neighbourhood)),
                _leaf("calle", _t(address.street)),
                _leaf("numero_exterior", _t(address.exterior_number)),
                _leaf("numero_interior", _t(address.interior_number)),
                _leaf("codigo_postal", _t(address.postal_code)),
            )
    return _n("tipo_domicilio", body)


def _phone(phone: Phone | None) -> Node | None:
    if phone is None:
        return None
    return _n(
        "telefono",
        _leaf("clave_pais", phone.country),
        _leaf("numero_telefono", phone.number),
        _leaf("correo_electronico", _t(phone.email)),
    )


def _client(client: Client) -> Node:
    account = client.account
    return _n(
        "persona_aviso",
        _n(
            "datos_cuenta_plataforma",
            _leaf("id_usuario", _t(account.user_id)),
            _leaf("cuenta_relacionada", _t(account.related_account)),
            _leaf("clabe_interbancaria", account.clabe),
            _leaf("moneda_cuenta", account.currency),
        ),
        _person(client.person, full=True),
        _address(client.address),
        _phone(client.phone),
    )


def _asset(asset: VirtualAsset) -> Node:
    return _n(
        "activo_virtual",
        _leaf("activo_virtual_operado", asset.code),
        _leaf("descripcion_activo_virtual", _t(asset.description)),
        _leaf("tipo_cambio_mn", _money(asset.rate_mxn, "tipo_cambio_mn")),
        _leaf(
            "cantidad_activo_virtual",
            norm.quantity(asset.quantity, field="cantidad_activo_virtual"),
        ),
    )


def _group(tag: str, items: Sequence[Node]) -> Node | None:
    return _n(tag, *items) if items else None


def _trade(tag: str, trade: Trade) -> Node:
    return _n(
        tag,
        _leaf("fecha_hora_operacion", _when(trade.at)),
        _leaf("moneda_operacion", trade.currency),
        _leaf("monto_operacion", _money(trade.amount, "monto_operacion")),
        _asset(trade.asset),
        _leaf("hash_operacion", _t(trade.tx_hash)),
    )


def _exchange(exchange: Exchange) -> Node:
    return _n(
        "intercambio",
        _leaf("fecha_hora_operacion", _when(exchange.at)),
        _n(
            "activo_virtual_enviado",
            _asset(exchange.sent),
            _leaf("monto_operacion_mn", _money(exchange.sent_mxn, "monto_mn")),
        ),
        _n(
            "activo_virtual_recibido",
            _asset(exchange.received),
            _leaf("monto_operacion_mn", _money(exchange.received_mxn, "monto_mn")),
        ),
        _leaf("hash_operacion", _t(exchange.tx_hash)),
    )


def _transfer(tag: str, transfer: Transfer) -> Node:
    return _n(
        tag,
        _leaf("fecha_hora_operacion", _when(transfer.at)),
        _leaf("monto_operacion_mn", _money(transfer.amount_mxn, "monto_mn")),
        _asset(transfer.asset),
        _leaf("hash_operacion", _t(transfer.tx_hash)),
    )


def _counterparty(who: Counterparty) -> Node:
    if who.is_natural:
        body = _n(
            "persona_fisica",
            _leaf("nombre", _t(who.name)),
            _leaf("apellido_paterno", _t(who.paternal_surname)),
            _leaf("apellido_materno", _t(who.maternal_surname)),
        )
    else:
        body = _n("persona_moral", _leaf("denominacion_razon", _t(who.name)))
    return _n("tipo_persona", body)


def _bank_account(account: MexicanBankAccount | ForeignBankAccount) -> Node:
    match account:
        case MexicanBankAccount():
            body = _n(
                "nacional",
                _leaf("clabe_destino", account.clabe),
                _leaf("clave_institucion_financiera", account.institution),
            )
        case ForeignBankAccount():
            body = _n(
                "extranjero",
                _leaf("numero_cuenta", _t(account.number)),
                _leaf("nombre_banco", _t(account.bank)),
            )
    return _n("nacionalidad_cuenta", body)


def _movement(tag: str, party: str, movement: FundsMovement) -> Node:
    return _n(
        tag,
        _leaf("fecha_hora_operacion", _when(movement.at)),
        _leaf("instrumento_monetario", movement.instrument),
        _leaf("moneda_operacion", movement.currency),
        _leaf("monto_operacion", _money(movement.amount, "monto_operacion")),
        _n(
            party,
            _counterparty(movement.counterparty),
            _bank_account(movement.bank_account),
        ),
    )


def _operations(ops: Operations) -> Node:
    transfers = (
        _group(
            "transferencias_enviadas",
            [_transfer("envio", t) for t in ops.transfers_sent],
        ),
        _group(
            "transferencias_recibidas",
            [_transfer("recepcion", t) for t in ops.transfers_received],
        ),
    )
    funds = (
        _group(
            "fondos_retirados",
            [_movement("retiro", "datos_beneficiario", m) for m in ops.withdrawals],
        ),
        _group(
            "fondos_depositados",
            [_movement("deposito", "datos_ordenante", m) for m in ops.deposits],
        ),
    )
    return _n(
        "detalle_operaciones",
        _group("operaciones_compra", [_trade("compra", t) for t in ops.purchases]),
        _group("operaciones_venta", [_trade("venta", t) for t in ops.sales]),
        _group("operaciones_intercambio", [_exchange(e) for e in ops.exchanges]),
        _n("operaciones_transferencia", *transfers) if any(transfers) else None,
        _n("operaciones_fondos", *funds) if any(funds) else None,
    )


def _aviso(aviso: Aviso) -> Node:
    if aviso.operations.is_empty:
        where = f"aviso {aviso.reference}"
        raise ReportFieldError(where, "has no operation")
    return _n(
        "aviso",
        _leaf("referencia_aviso", _t(aviso.reference)),
        _leaf("prioridad", aviso.priority.value),
        _n(
            "alerta",
            _leaf("tipo_alerta", aviso.alert_type),
            _leaf("descripcion_alerta", _t(aviso.alert_description)),
        ),
        _n(
            "operaciones_persona",
            _client(aviso.client),
            *(
                _n("dueno_beneficiario", _person(owner, full=False))
                for owner in aviso.beneficial_owners
            ),
            _operations(aviso.operations),
        ),
    )


def _archivo(
    definition: XmlDefinition, informe: AvisoInforme, avisos: Sequence[Node]
) -> Node:
    subject = informe.subject
    return _n(
        "archivo",
        _n(
            "informe",
            _leaf("mes_reportado", norm.yyyymm(informe.month)),
            _n(
                "sujeto_obligado",
                _leaf(
                    "clave_entidad_colegiada",
                    _rfc(subject.collegiate_entity_rfc, "clave_entidad_colegiada"),
                ),
                _leaf("clave_sujeto_obligado", norm.rfc(subject.rfc)),
                _leaf("clave_actividad", definition.activity),
                _leaf("dominio_plataforma", _t(subject.platform_domain)),
            ),
            *avisos,
        ),
    )


def to_xml(namespace: str, root: Node) -> bytes:
    """Serialize a node tree in ``namespace`` as the default namespace.

    UTF-8 with an XML declaration, two-space indentation and a final newline.

    Returns:
        The document bytes.

    """
    element = etree.Element(f"{{{namespace}}}{root.tag}", nsmap={None: namespace})
    element.text = root.text
    pending = [(element, child) for child in root.children]
    while pending:
        parent, node = pending.pop(0)
        child = etree.SubElement(parent, f"{{{namespace}}}{node.tag}")
        child.text = node.text
        pending.extend((child, grandchild) for grandchild in node.children)
    etree.indent(element, space="  ")
    return etree.tostring(element, xml_declaration=True, encoding="UTF-8") + b"\n"


def load_schema(defs_dir: Path, definition: XmlDefinition) -> etree.XMLSchema:
    """Load the definition's vendored XSD after checking its SHA-256.

    Returns:
        The compiled schema.

    Raises:
        SchemaUnavailableError: The definition has no published schema yet, or
            the file is missing or does not match its recorded hash.

    """
    label = f"{definition.code} v{definition.version}"
    if definition.xsd is None or definition.xsd_sha256 is None:
        msg = f"{label}: no official schema yet"
        raise SchemaUnavailableError(msg)
    path = definition_dir(defs_dir, definition) / definition.xsd
    try:
        data = path.read_bytes()
    except OSError as exc:
        msg = f"{label}: schema file missing"
        raise SchemaUnavailableError(msg) from exc
    if hashlib.sha256(data).hexdigest() != definition.xsd_sha256:
        msg = f"{label}: schema hash mismatch"
        raise SchemaUnavailableError(msg)
    return etree.XMLSchema(etree.fromstring(data, _parser()))


def definition_dir(defs_dir: Path, definition: XmlDefinition) -> Path:
    """Where a definition's own files (its XSD) live.

    Returns:
        ``<defs_dir>/<code>``.

    """
    return defs_dir / definition.code


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True)


def _name(names: dict[str, str], path: str | None) -> str:
    return names.get(path or "", path or "?")


def _validate(schema: etree.XMLSchema, data: bytes, part: int) -> None:
    root = etree.fromstring(data, _parser())
    if schema.validate(root):
        return
    tree = root.getroottree()
    # lxml reports positional paths (/*/*[2]); name the elements instead, and
    # never quote the offending value.
    names = {
        tree.getpath(element): "/".join(
            etree.QName(node).localname
            for node in [*reversed(list(element.iterancestors())), element]
        )
        for element in root.iter()
    }
    problems = [
        f"line {error.line} {_name(names, error.path)} {error.type_name}"
        for error in list(schema.error_log)[:_MAX_ERRORS]
    ]
    msg = f"part {part}: " + "; ".join(problems)
    raise SchemaInvalidError(msg)


def _parts(
    definition: XmlDefinition, informe: AvisoInforme, avisos: Sequence[Node]
) -> list[bytes]:
    def render(batch: Sequence[Node]) -> bytes:
        return to_xml(definition.namespace, _archivo(definition, informe, batch))

    whole = render(avisos)
    if len(whole) <= definition.size_limit_bytes:
        return [whole]
    parts: list[bytes] = []
    batch: list[Node] = []
    current = b""
    for aviso in avisos:
        candidate = render([*batch, aviso])
        if len(candidate) <= definition.size_limit_bytes:
            batch.append(aviso)
            current = candidate
            continue
        if not batch:
            raise ReportTooLargeError
        parts.append(current)
        batch = [aviso]
        current = render(batch)
        if len(current) > definition.size_limit_bytes:
            raise ReportTooLargeError
    parts.append(current)
    return parts


def serialize_aviso(
    defs_dir: Path, definition: XmlDefinition, informe: AvisoInforme
) -> SerializedReport:
    """Render and validate one month of avisos for one filer.

    A value that cannot be normalized, or an aviso without operations, raises
    ``ReportFieldError``; a missing schema ``SchemaUnavailableError``; a part
    that does not validate ``SchemaInvalidError``; a single aviso over the size
    limit ``ReportTooLargeError``.

    Returns:
        One or more UTF-8 XML parts, each a complete ``archivo`` within the size
        limit and valid against the official schema.

    Raises:
        ZeroReportNotAllowedError: There are no avisos and the definition has no
            zero report.

    """
    schema = load_schema(defs_dir, definition)
    if (
        not informe.avisos
        and definition.zero_report_rule is not ZeroReportRule.REQUIRED
    ):
        raise ZeroReportNotAllowedError(definition.code)
    parts = _parts(definition, informe, [_aviso(aviso) for aviso in informe.avisos])
    for number, part in enumerate(parts, start=1):
        _validate(schema, part, number)
    return SerializedReport(
        code=definition.code,
        version=definition.version,
        parts=tuple(parts),
        record_count=len(informe.avisos),
        warnings=(),
        confidence=definition.encoding_confidence,
    )
