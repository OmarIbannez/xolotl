"""Where unit values come from: the ``UnitValueSource`` port and the seed files.

A seed file holds one unit (``policy-packs/_units/<file>``); its ``legal_basis``,
``confidence`` and ``verified_as_of`` are the defaults a row may override. Daily
units omit ``valid_to``, which a daily row closes on its own ``valid_from``.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Protocol, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from xolotl.platform.enums import Confidence
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.units.catalogue import unit_spec
from xolotl.platform.units.errors import UnverifiedValueError

_AMOUNT = r"^[0-9]+(\.[0-9]+)?$"


@dataclass(frozen=True, slots=True)
class UnitValue:
    """One dated value of a unit, as a source reports it."""

    unit_code: str
    valid_from: date
    valid_to: date | None
    value: Decimal
    quote_ccy: str
    source: str
    confidence: Confidence


class UnitValueSource(Protocol):
    """A source of unit values (program spec §4)."""

    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]:
        """Return the rows with ``start <= valid_from <= end``, ascending."""
        ...

    def latest_available(self, unit_code: str) -> UnitValue | None:
        """Return the row with the latest ``valid_from``, or ``None``."""
        ...


class _Row(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    valid_from: date
    valid_to: date | None = None
    value: str = Field(pattern=_AMOUNT)
    legal_basis: str | None = Field(default=None, min_length=1)
    confidence: Confidence | None = None
    verified_as_of: date | None = None


class _File(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    unit_code: str
    quote_ccy: str = Field(pattern=r"^[A-Z]{3}$")
    source: str = Field(min_length=1)
    legal_basis: str = Field(min_length=1)
    confidence: Confidence
    verified_as_of: date
    values: list[_Row] = Field(min_length=1)


def _values_of(parsed: _File) -> list[UnitValue]:
    spec = unit_spec(parsed.unit_code)
    if parsed.quote_ccy != spec.quote_ccy:
        msg = (
            f"{parsed.unit_code} is quoted in {spec.quote_ccy}, not {parsed.quote_ccy}"
        )
        raise ValueError(msg)
    values: list[UnitValue] = []
    seen: set[date] = set()
    for row in parsed.values:
        if row.valid_from in seen:
            msg = f"{row.valid_from} is listed twice"
            raise ValueError(msg)
        seen.add(row.valid_from)
        if spec.daily and row.valid_to not in {None, row.valid_from}:
            msg = f"{parsed.unit_code} is daily: {row.valid_from} cannot carry valid_to"
            raise ValueError(msg)
        valid_to = row.valid_from if spec.daily else row.valid_to
        if valid_to is not None and valid_to < row.valid_from:
            msg = f"{row.valid_from} ends before it starts"
            raise ValueError(msg)
        value = Decimal(row.value)
        if value <= 0:
            msg = f"{row.valid_from} has a value that is not positive"
            raise ValueError(msg)
        values.append(
            UnitValue(
                unit_code=parsed.unit_code,
                valid_from=row.valid_from,
                valid_to=valid_to,
                value=value,
                quote_ccy=parsed.quote_ccy,
                source=parsed.source,
                confidence=row.confidence or parsed.confidence,
            )
        )
    return sorted(values, key=lambda v: v.valid_from)


def read_seed_file(path: Path) -> list[UnitValue]:
    """Read and validate one unit's seed file.

    Returns:
        Its rows, ascending by ``valid_from``.

    Raises:
        ValidationFailedError: When the file is missing, is not YAML, does not
            follow the schema, names an unknown unit or lists a day twice.

    """
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
        return _values_of(_File.model_validate(raw))
    except (
        OSError,
        yaml.YAMLError,
        ValidationError,
        ValueError,
        ValidationFailedError,
    ) as exc:
        msg = f"{path}: {exc}"
        raise ValidationFailedError(msg) from exc


class SeedFileSource:
    """The committed seed files as a source; demo mode reads them for every unit."""

    def __init__(self, seed_dir: Path, *, allow_unverified: bool = False) -> None:
        """Read from ``seed_dir``; ``allow_unverified`` lets UNVERIFIED rows pass."""
        self._seed_dir: Path = seed_dir
        self._allow_unverified: bool = allow_unverified

    def _rows(self, unit_code: str) -> list[UnitValue]:
        rows = read_seed_file(self._seed_dir / unit_spec(unit_code).seed_file)
        if rows[0].unit_code != unit_code:
            msg = f"{unit_spec(unit_code).seed_file} holds {rows[0].unit_code}"
            raise ValidationFailedError(msg)
        return rows

    def _checked(self, rows: list[UnitValue]) -> list[UnitValue]:
        if not self._allow_unverified:
            for row in rows:
                if row.confidence is Confidence.UNVERIFIED:
                    msg = f"{row.unit_code} {row.valid_from.isoformat()}"
                    raise UnverifiedValueError(msg)
        return rows

    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]:
        """Read the unit's rows in the range.

        An UNVERIFIED row raises ``UnverifiedValueError`` unless allowed.

        Returns:
            Rows with ``start <= valid_from <= end``, ascending.

        """
        rows = [r for r in self._rows(unit_code) if start <= r.valid_from <= end]
        return self._checked(rows)

    def latest_available(self, unit_code: str) -> UnitValue | None:
        """Read the unit's latest row.

        An UNVERIFIED row raises ``UnverifiedValueError`` unless allowed.

        Returns:
            The row with the latest ``valid_from``.

        """
        return self._checked(self._rows(unit_code)[-1:])[0]
