"""Report definitions and code tables, read from ``report-defs/`` (D14, FR-RR-001).

A definition lives at ``<defs_dir>/<CODE>/v<N>.yaml``. A delimited definition names
a shared layout under ``<defs_dir>/_layouts/<name>.yaml`` so the four Formato 41
report kinds share one column table. Code tables live under
``<defs_dir>/codetables/<CC>/<name>.yaml``. Every value the official sources do not
settle is marked ``UNVERIFIED`` in the file, not guessed in code.
"""

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from xolotl.platform.enums import Confidence
from xolotl.platform.errors import ValidationFailedError

_STRICT = ConfigDict(frozen=True, extra="forbid")


class DefinitionError(ValidationFailedError):
    default_code: ClassVar[str] = "REPORT_DEFINITION_INVALID"
    default_detail: ClassVar[str] = "the report definition is invalid"


class SerializerFamily(StrEnum):
    DELIMITED = "DELIMITED"
    XML_XSD = "XML_XSD"


class ZeroReportRule(StrEnum):
    REQUIRED = "REQUIRED"
    NOT_ALLOWED = "NOT_ALLOWED"


class CorrectionRule(StrEnum):
    UNSUPPORTED = "UNSUPPORTED"


class ColumnKind(StrEnum):
    TEXT = "TEXT"
    CODE = "CODE"
    NUMERIC = "NUMERIC"
    DECIMAL = "DECIMAL"


class Overflow(StrEnum):
    TRUNCATE = "TRUNCATE"
    ERROR = "ERROR"


class LineEnding(StrEnum):
    CRLF = "CRLF"
    LF = "LF"

    @property
    def chars(self) -> str:
        return "\r\n" if self is LineEnding.CRLF else "\n"


class Column(BaseModel):
    """One column of a delimited layout."""

    model_config: ClassVar[ConfigDict] = _STRICT

    number: int = Field(ge=1)
    name: str
    field: str
    kind: ColumnKind
    width: int | None = Field(default=None, ge=1)
    overflow: Overflow = Overflow.TRUNCATE
    # Report-type codes the column is filled for; empty for every other type.
    report_types: frozenset[str]
    # Report-type codes for which an empty value is an error.
    required_for: frozenset[str] = frozenset()
    code_table: str | None = None


class DelimitedLayout(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    name: str
    sources: tuple[str, ...]
    separator: str = Field(min_length=1, max_length=1)
    trailing_separator: bool
    line_ending: LineEnding
    layout_confidence: Confidence
    columns: tuple[Column, ...]
    empty_report_columns: tuple[int, ...]


class ReportDefinition(BaseModel):
    """One report code at one version."""

    model_config: ClassVar[ConfigDict] = _STRICT

    code: str = Field(pattern=r"^[A-Z]{2}_[A-Z0-9_]+$")
    version: int = Field(ge=1)
    jurisdiction: str
    regulator: str
    sources: tuple[str, ...]
    serializer: SerializerFamily
    encoding: str
    encoding_confidence: Confidence
    zero_report_rule: ZeroReportRule
    correction_rule: CorrectionRule
    valid_from: date
    valid_to: date | None = None
    report_type: str
    layout: str


class CodeTable(BaseModel):
    model_config: ClassVar[ConfigDict] = _STRICT

    name: str
    version: int = Field(ge=1)
    source: str
    confidence: Confidence
    entries: dict[str, str]


def _read_mapping(path: Path) -> dict[str, object]:
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"{path.name}: unreadable"
        raise DefinitionError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path.name}: not a mapping"
        raise DefinitionError(msg)
    return cast("dict[str, object]", raw)


def _validate[M: BaseModel](model: type[M], path: Path) -> M:
    raw = _read_mapping(path)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        msg = f"{path.name}: {exc.error_count()} validation errors"
        raise DefinitionError(msg) from exc


def load_definition(defs_dir: Path, code: str, version: int) -> ReportDefinition:
    """Read ``<defs_dir>/<code>/v<version>.yaml``.

    Returns:
        The validated definition.

    Raises:
        DefinitionError: The file is missing, malformed, or names another code or
            version than its path.

    """
    path = defs_dir / code / f"v{version}.yaml"
    definition = _validate(ReportDefinition, path)
    if (definition.code, definition.version) != (code, version):
        msg = f"{path.name}: declares {definition.code} v{definition.version}"
        raise DefinitionError(msg)
    return definition


def load_layout(defs_dir: Path, name: str) -> DelimitedLayout:
    """Read the shared layout ``<defs_dir>/_layouts/<name>.yaml``.

    Returns:
        The validated layout, whose columns are numbered 1 to n in order.

    Raises:
        DefinitionError: The file is missing or malformed, or its columns are not
            numbered consecutively from 1.

    """
    path = defs_dir / "_layouts" / f"{name}.yaml"
    layout = _validate(DelimitedLayout, path)
    numbers = [column.number for column in layout.columns]
    if numbers != list(range(1, len(numbers) + 1)) or layout.name != name:
        msg = f"{path.name}: columns must be numbered 1 to n and the name must match"
        raise DefinitionError(msg)
    return layout


def load_code_table(defs_dir: Path, ref: str) -> CodeTable:
    """Read a code table by reference ``<CC>/<name>``.

    A missing or malformed file raises ``DefinitionError``.

    Returns:
        The validated table.

    """
    return _validate(CodeTable, defs_dir / "codetables" / f"{ref}.yaml")
