"""Formato 41 ``DELIMITED`` serializer (FR-RR-002, FR-RR-018; D14).

One function renders the four report kinds from the shared layout: the definition
says which report type it is, the layout says which columns that type fills, how
wide each is, which are required and which code table each code column uses.
The inputs are plain frozen dataclasses that SP-05 fills from its rows.

Rows: every operation is one record with consecutivo ``00``; for inusuales and
preocupantes each related account or person is a further record ``01..n`` that
repeats columns 1-33 and 40-41 and varies 34-39, as the Anexo 1 preamble says.
The column 34 note says only 1-5 and 34-39 need filling for related accounts;
repeating everything satisfies both readings. All rows of one operation share
its folio.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from xolotl.modules.reporting.serializers import normalizers as norm
from xolotl.modules.reporting.serializers.definitions import (
    Column,
    ColumnKind,
    DelimitedDefinition,
    DelimitedLayout,
    Overflow,
    ZeroReportRule,
    load_code_table,
    load_layout,
)
from xolotl.modules.reporting.serializers.errors import (
    ReportFieldError,
    ZeroReportNotAllowedError,
)
from xolotl.modules.reporting.serializers.result import (
    SerializedReport,
    SerializerWarning,
)
from xolotl.platform.enums import Confidence

RELEVANTE = "1"
PREOCUPANTE = "3"
_PERIOD_MONTHLY_TYPES = frozenset({RELEVANTE})
_NO_SURNAME = "XXXX"
_UNKNOWN_NATIONALITY = "0"
_NO_NEIGHBOURHOOD = "0"
_HEAD_OFFICE = "0"
_MAIN_ROW = 0
_MAX_ROWS_PER_OPERATION = 99


class PersonType(StrEnum):
    FISICA = "1"
    MORAL = "2"


class Legend(StrEnum):
    """The 24-hour report legend that heads column 40 (D14)."""

    LISTA_PERSONAS_BLOQUEADAS = "LISTA DE PERSONAS BLOQUEADAS"
    REPORTE_24_HORAS = "REPORTE DE 24 HORAS"


@dataclass(frozen=True, slots=True)
class F41Header:
    """Data that identifies the report and its filer.

    ``period`` is any day of the reported month for relevantes and the report
    date for inusuales and preocupantes. CASFIM keys may carry the hyphen.
    """

    period: date
    supervisor_key: str
    entity_key: str


@dataclass(frozen=True, slots=True)
class F41Subject:
    """The account holder or the person who carried out the operation."""

    person_type: PersonType
    # ISO 3166 alpha-2; None means unknown, allowed for relevantes only.
    nationality: str | None
    address: str
    city_code: str
    company_name: str | None = None
    first_names: str | None = None
    paternal_surname: str | None = None
    maternal_surname: str | None = None
    rfc: str | None = None
    curp: str | None = None
    birth_or_incorporation: date | None = None
    neighbourhood: str | None = None
    # An occasional user who holds no account may have no colonia; it is then
    # written 0 (column 25). Everyone else must give one.
    occasional_user: bool = False
    phone: str | None = None
    economic_activity: str | None = None


@dataclass(frozen=True, slots=True)
class F41Related:
    """A related account or person of an inusual or preocupante operation."""

    account: str | None = None
    entity_key: str | None = None
    first_names: str | None = None
    paternal_surname: str | None = None
    maternal_surname: str | None = None


@dataclass(frozen=True, slots=True)
class F41Operation:
    """One reported operation (or conduct, with a zero amount)."""

    locality: str
    operation_type: str
    instrument: str
    account: str
    amount: Decimal
    currency: str
    operation_date: date
    subject: F41Subject
    branch: str = _HEAD_OFFICE
    detection_date: date | None = None
    description: str | None = None
    reasons: str | None = None
    related: tuple[F41Related, ...] = field(default_factory=tuple[F41Related, ...])


def _surnames(
    paternal: str | None, maternal: str | None, where: str
) -> tuple[str, str]:
    # The XXXX rule of columns 19-20: a missing surname is written XXXX, and
    # when the paternal one is missing the maternal one becomes mandatory, so
    # both missing is refused.
    paternal_text = norm.f41_text(paternal)
    maternal_text = norm.f41_text(maternal)
    if not paternal_text and not maternal_text:
        raise ReportFieldError(where, "a persona fisica needs at least one surname")
    return paternal_text or _NO_SURNAME, maternal_text or _NO_SURNAME


def _subject_fields(subject: F41Subject, line: int) -> dict[str, str | None]:
    paternal: str | None = subject.paternal_surname
    maternal: str | None = subject.maternal_surname
    if subject.person_type is PersonType.FISICA:
        paternal, maternal = _surnames(paternal, maternal, f"line {line}")
    neighbourhood = subject.neighbourhood
    if not norm.f41_text(neighbourhood) and subject.occasional_user:
        neighbourhood = _NO_NEIGHBOURHOOD
    return {
        "nacionalidad": subject.nationality or _UNKNOWN_NATIONALITY,
        "tipo_persona": subject.person_type.value,
        "razon_social": subject.company_name,
        "nombre": subject.first_names,
        "apellido_paterno": paternal,
        "apellido_materno": maternal,
        "rfc": norm.rfc(subject.rfc, field="rfc") if subject.rfc else None,
        "curp": norm.curp(subject.curp, field="curp") if subject.curp else None,
        "fecha_nacimiento_constitucion": (
            norm.yyyymmdd(subject.birth_or_incorporation)
            if subject.birth_or_incorporation
            else None
        ),
        "domicilio": subject.address,
        "colonia": neighbourhood,
        "ciudad_poblacion": subject.city_code,
        "telefono": subject.phone,
        "actividad_economica": subject.economic_activity,
    }


def _related_key(value: str | None) -> str | None:
    # A CASFIM key loses its hyphen as in columns 4-5; anything else is an
    # SAT-assigned key and passes through the column's own checks.
    if value and norm.CASFIM_KEY.fullmatch(value.strip()):
        return norm.casfim_key(value, field="clave_sujeto_obligado")
    return value


def _check_related(related: F41Related, line: int) -> None:
    where = f"line {line}"
    has_person = any(
        norm.f41_text(value)
        for value in (
            related.first_names,
            related.paternal_surname,
            related.maternal_surname,
        )
    )
    if not norm.f41_text(related.account) and not has_person:
        raise ReportFieldError(where, "a related row needs an account or a person")
    if has_person and not norm.f41_text(related.first_names):
        raise ReportFieldError(where, "a related person needs column 37")


def _related_fields(index: int, related: F41Related | None) -> dict[str, str | None]:
    if related is None:
        return {"consecutivo": f"{index:02d}"}
    return {
        "consecutivo": f"{index:02d}",
        "cuenta_relacionada": related.account,
        "clave_sujeto_obligado_relacionado": _related_key(related.entity_key),
        "nombre_relacionado": related.first_names,
        "apellido_paterno_relacionado": (
            _NO_SURNAME
            if norm.f41_text(related.first_names)
            and not norm.f41_text(related.paternal_surname)
            else related.paternal_surname
        ),
        "apellido_materno_relacionado": related.maternal_surname,
    }


def _description(operation: F41Operation, legend: Legend | None) -> str | None:
    if legend is None:
        return operation.description
    body = norm.f41_text(operation.description)
    if body.startswith(legend.value):
        return body
    # Without a description column 40 stays empty and fails as required: the
    # legend alone does not describe the operation.
    return f"{legend.value}. {body}" if body else None


@dataclass(slots=True)
class _Renderer:
    definition: DelimitedDefinition
    layout: DelimitedLayout
    code_tables: Mapping[str, frozenset[str]]
    legend: Legend | None
    warnings: list[SerializerWarning] = field(default_factory=list[SerializerWarning])

    def cell(self, column: Column, raw: str | None, line: int) -> str:
        if self.definition.report_type not in column.report_types:
            return ""
        value = norm.f41_text(raw)
        where = f"line {line} column {column.number} {column.name}"
        if not value:
            if self.definition.report_type in column.required_for:
                raise ReportFieldError(where, "is required")
            return ""
        self._check_kind(column, value, where)
        if column.width is not None:
            value, cut = norm.fit(value, column.width)
            if cut and column.overflow is Overflow.ERROR:
                raise ReportFieldError(where, f"longer than {column.width}")
            if cut:
                self.warnings.append(
                    SerializerWarning("FIELD_TRUNCATED", line, column.number)
                )
        if column.code_table and value not in self.code_tables[column.code_table]:
            raise ReportFieldError(where, f"not in {column.code_table}")
        return value

    @staticmethod
    def _check_kind(column: Column, value: str, where: str) -> None:
        if column.kind is ColumnKind.NUMERIC and not value.isdigit():
            raise ReportFieldError(where, "must be digits")
        if column.kind is ColumnKind.CODE and " " in value:
            raise ReportFieldError(where, "must not contain spaces")

    def record(self, fields: Mapping[str, str | None], line: int) -> str:
        cells = [
            self.cell(column, fields.get(column.field), line)
            for column in self.layout.columns
        ]
        text = self.layout.separator.join(cells)
        if self.layout.trailing_separator:
            text += self.layout.separator
        return text + self.layout.line_ending.chars

    def empty_record(self, header_fields: Mapping[str, str | None]) -> str:
        """Render the reporte vacío: only the layout's empty-report columns.

        Returns:
            One record, terminated.

        """
        keep = set(self.layout.empty_report_columns)
        cells = [
            norm.f41_text(header_fields.get(column.field))
            if column.number in keep
            else ""
            for column in self.layout.columns
        ]
        text = self.layout.separator.join(cells)
        if self.layout.trailing_separator:
            text += self.layout.separator
        return text + self.layout.line_ending.chars


def _check_subject(subject: F41Subject, report_type: str, line: int) -> None:
    where = f"line {line}"
    if subject.person_type is PersonType.MORAL and not norm.f41_text(
        subject.company_name
    ):
        raise ReportFieldError(where, "a persona moral needs column 17")
    if subject.person_type is PersonType.FISICA and not norm.f41_text(
        subject.first_names
    ):
        raise ReportFieldError(where, "a persona fisica needs column 18")
    if report_type == PREOCUPANTE and subject.person_type is not PersonType.FISICA:
        raise ReportFieldError(where, "a preocupante reports a natural person")
    if subject.nationality is None and report_type != RELEVANTE:
        raise ReportFieldError(where, "nationality is unknown only for relevantes")


def _check_applicable(operation: F41Operation, report_type: str, line: int) -> None:
    if report_type != RELEVANTE:
        return
    where = f"line {line}"
    if operation.related:
        raise ReportFieldError(where, "related rows are for inusuales and preocupantes")
    if operation.detection_date is not None:
        raise ReportFieldError(where, "a detection date is for inusuales only")


def _operation_rows(
    renderer: _Renderer,
    header_fields: Mapping[str, str | None],
    operation: F41Operation,
    folio: int,
    first_line: int,
) -> list[str]:
    report_type = renderer.definition.report_type
    _check_subject(operation.subject, report_type, first_line)
    _check_applicable(operation, report_type, first_line)
    base: dict[str, str | None] = {
        **header_fields,
        "folio": f"{folio:06d}",
        "localidad": operation.locality,
        "sucursal": operation.branch,
        "tipo_operacion": operation.operation_type,
        "instrumento_monetario": operation.instrument,
        "numero_cuenta": operation.account,
        "monto": norm.amount(operation.amount, field="monto"),
        "moneda": operation.currency,
        "fecha_operacion": norm.yyyymmdd(operation.operation_date),
        "fecha_deteccion": (
            norm.yyyymmdd(operation.detection_date)
            if operation.detection_date
            else None
        ),
        **_subject_fields(operation.subject, first_line),
        "descripcion_operacion": _description(operation, renderer.legend),
        "razones": operation.reasons,
    }
    related: list[F41Related | None] = [None]
    if report_type != RELEVANTE:
        related.extend(operation.related)
    for offset, item in enumerate(operation.related, start=1):
        _check_related(item, first_line + offset)
    if len(related) > _MAX_ROWS_PER_OPERATION + 1:
        msg = f"line {first_line}"
        raise ReportFieldError(msg, "more than 99 related rows")
    return [
        renderer.record({**base, **_related_fields(index, item)}, first_line + index)
        for index, item in enumerate(related)
    ]


def serialize_f41(
    defs_dir: Path,
    definition: DelimitedDefinition,
    header: F41Header,
    operations: Sequence[F41Operation],
    *,
    legend: Legend | None = None,
) -> SerializedReport:
    """Render one Formato 41 file.

    An empty ``operations`` renders the reporte vacío when the definition requires
    one and is refused otherwise.

    Returns:
        One ASCII file part, its warnings and ``encoding_confidence``.

    Raises:
        ReportFieldError: A value is missing, malformed, outside its code table or
            too long for a column that must not be truncated, or the legend is
            missing for the 24-hour report or given for another kind.
        ZeroReportNotAllowedError: ``operations`` is empty for a definition
            without a zero report.

    """
    if definition.legend_required != (legend is not None):
        reason = "needs a legend" if definition.legend_required else "takes no legend"
        raise ReportFieldError(definition.code, reason)
    layout = load_layout(defs_dir, definition.layout)
    tables = {
        column.code_table: frozenset(
            load_code_table(defs_dir, column.code_table).entries
        )
        for column in layout.columns
        if column.code_table
    }
    renderer = _Renderer(definition, layout, tables, legend)
    report_type = definition.report_type
    header_fields: dict[str, str | None] = {
        "tipo_reporte": report_type,
        "periodo": (
            norm.yyyymm(header.period)
            if report_type in _PERIOD_MONTHLY_TYPES
            else norm.yyyymmdd(header.period)
        ),
        "organo_supervisor": norm.casfim_key(header.supervisor_key, field="organo"),
        "clave_sujeto_obligado": norm.casfim_key(header.entity_key, field="entidad"),
    }
    lines: list[str] = []
    if not operations:
        if definition.zero_report_rule is not ZeroReportRule.REQUIRED:
            raise ZeroReportNotAllowedError(definition.code)
        lines.append(renderer.empty_record(header_fields))
    for folio, operation in enumerate(operations, start=1):
        lines.extend(
            _operation_rows(renderer, header_fields, operation, folio, len(lines) + 1)
        )
    confidence = (
        Confidence.UNVERIFIED
        if Confidence.UNVERIFIED
        in {definition.encoding_confidence, layout.layout_confidence}
        else Confidence.VERIFIED
    )
    return SerializedReport(
        code=definition.code,
        version=definition.version,
        parts=("".join(lines).encode(definition.encoding),),
        record_count=len(lines),
        warnings=tuple(renderer.warnings),
        confidence=confidence,
    )
