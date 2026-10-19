"""Holidays per country and scope: the YAML file, the seed and the loaded value.

``policy-packs/_holidays/<CC>.yaml`` lists the non-working days of each scope with
the publication each comes from. ``seed_holidays`` writes them to the shared
``holiday`` table through the owner engine; ``load_holidays`` reads one scope back
as a ``Holidays`` value that the business-day helpers take, so they stay pure.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.platform.calendar.models import Holiday
from xolotl.platform.enums import HolidayScope
from xolotl.platform.errors import ValidationFailedError


@dataclass(frozen=True, slots=True)
class HolidayRow:
    """One day of the file; ``verify`` marks it for the compliance review."""

    country_code: str
    scope: HolidayScope
    day: date
    name: str
    source: str
    verify: bool


@dataclass(frozen=True, slots=True)
class Holidays:
    """The non-working days of one country and scope."""

    country: str
    scope: HolidayScope
    days: frozenset[date]


class _Day(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    day: date
    name: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    verify: bool = False


class _Scope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    days: list[_Day]


class _File(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    country: str = Field(pattern=r"^[A-Z]{2}$")
    years: list[int]
    scopes: dict[HolidayScope, _Scope]


def _rows_of(parsed: _File) -> list[HolidayRow]:
    rows: list[HolidayRow] = []
    seen: set[tuple[HolidayScope, date]] = set()
    for scope, block in parsed.scopes.items():
        for entry in block.days:
            if entry.day.year not in parsed.years:
                msg = f"{scope.value} {entry.day} is outside the years {parsed.years}"
                raise ValueError(msg)
            if (scope, entry.day) in seen:
                msg = f"{scope.value} {entry.day} is listed twice"
                raise ValueError(msg)
            seen.add((scope, entry.day))
            rows.append(
                HolidayRow(
                    country_code=parsed.country,
                    scope=scope,
                    day=entry.day,
                    name=entry.name,
                    source=entry.basis,
                    verify=entry.verify,
                )
            )
    return rows


def read_holiday_file(path: Path) -> list[HolidayRow]:
    """Read and validate a holidays file.

    Returns:
        Every row of the file, scope by scope, in file order.

    Raises:
        ValidationFailedError: When the file is missing, is not YAML, does not
            follow the schema, lists a day twice or a day outside its years.

    """
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
        return _rows_of(_File.model_validate(raw))
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        msg = f"{path}: {exc}"
        raise ValidationFailedError(msg) from exc


def holidays_from_rows(
    rows: Iterable[HolidayRow], *, country: str, scope: HolidayScope
) -> Holidays:
    """Keep the rows of one country and scope.

    Returns:
        The days of those rows as a ``Holidays`` value.

    """
    days = frozenset(
        row.day for row in rows if row.country_code == country and row.scope is scope
    )
    return Holidays(country=country, scope=scope, days=days)


def load_holidays(
    session: Session,
    *,
    country: str = "MX",
    scope: HolidayScope = HolidayScope.NATIONAL,
) -> Holidays:
    """Read one country and scope from the ``holiday`` table.

    Returns:
        The days the table holds for them (empty when nothing was seeded).

    """
    days = session.scalars(
        select(Holiday.day).where(
            Holiday.country_code == country, Holiday.scope == scope
        )
    ).all()
    return Holidays(country=country, scope=scope, days=frozenset(days))


def seed_holidays(session: Session, rows: Iterable[HolidayRow]) -> int:
    """Upsert the rows by ``(country_code, scope, day)``; needs the owner's session.

    The app role only reads ``holiday``; a second seed of the same file changes
    nothing, and a renamed day or a new basis replaces the stored one.

    Returns:
        The number of rows given.

    """
    values = [
        {
            "country_code": row.country_code,
            "scope": row.scope,
            "day": row.day,
            "name": row.name,
            "source": row.source,
        }
        for row in rows
    ]
    if not values:
        return 0
    stmt = insert(Holiday).values(values)
    _ = session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Holiday.country_code, Holiday.scope, Holiday.day],
            set_={"name": stmt.excluded.name, "source": stmt.excluded.source},
        )
    )
    return len(values)
