"""Unit conversions that record the ``unit_value`` row they used (program spec D5).

A rule picks the date whose value a conversion reads; a missing value raises
``UnitValueMissingError`` and nothing is silently reused from another day.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from xolotl.platform.calendar import Holidays, load_holidays, prev_business_day
from xolotl.platform.enums import HolidayScope
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.units.catalogue import UNIT_OF, unit_spec
from xolotl.platform.units.errors import UnitValueMissingError
from xolotl.platform.units.models import UnitValueRow
from xolotl.platform.units.quantity import Quantity
from xolotl.platform.units.rules import ConversionRule
from xolotl.platform.units.store import current_row

CCY_PLACES = Decimal("0.01")
UNIT_PLACES = Decimal("0.000001")
_QUOTE_CCY = "MXN"


class Conversion(BaseModel):
    """A converted amount and the dated value it came from."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    amount: Decimal
    unit_value_id: int | None
    rule: ConversionRule
    on: date
    resolved_date: date
    unit: str
    currency: str
    rate: Decimal


def resolve_date(rule: ConversionRule, on: date, *, holidays: Holidays) -> date:
    """Pick the date whose value a conversion on ``on`` reads.

    Returns:
        ``on`` itself, the prior banking day, the last day of the previous month
        or the last half-year end strictly before ``on``.

    Raises:
        ValidationFailedError: For ``PREV_MONTH_AVG``, which SP-02 does not support.

    """
    match rule:
        case ConversionRule.SAME_DAY:
            return on
        case ConversionRule.PRIOR_BANKING_DAY:
            return prev_business_day(on, holidays=holidays)
        case ConversionRule.LAST_DAY_PREV_MONTH:
            return on.replace(day=1) - timedelta(days=1)
        case ConversionRule.SEMIANNUAL_SNAPSHOT:
            mid_year = date(on.year, 6, 30)
            return mid_year if on > mid_year else date(on.year - 1, 12, 31)
        case ConversionRule.PREV_MONTH_AVG:
            msg = "PREV_MONTH_AVG is not supported in SP-02"
            raise ValidationFailedError(msg)


def _check_currency(currency: str) -> None:
    if currency != _QUOTE_CCY:
        msg = "only MXN conversions are supported"
        raise ValidationFailedError(msg)


def _unit_code_of(unit: str) -> str:
    unit_code = UNIT_OF.get(unit)
    if unit_code is None:
        msg = f"unknown unit {unit}"
        raise ValidationFailedError(msg)
    return unit_code


# The id and value of the row in force for a unit code on a resolved date.
type UnitRates = dict[tuple[str, date], tuple[int, Decimal]]


class UnitService:
    """Looks up dated unit values and converts amounts with them.

    The holiday calendars a conversion needs are read once and kept for the
    service's life; a caller that holds one already passes it in ``holidays``.
    A caller converting many amounts in a short span (a page of transactions,
    each in a transaction of its own) passes the same ``rates`` to each
    service: a conversion then reads a unit value once and remembers its id and
    value there. Only values found are kept, so one missing now is read again;
    a correction made meanwhile is seen once the caller starts a new dict.
    """

    def __init__(
        self,
        session: Session,
        *,
        holidays: Iterable[Holidays] = (),
        rates: UnitRates | None = None,
    ) -> None:
        """Read through ``session``, starting from ``holidays`` and ``rates``."""
        self._session: Session = session
        self._calendars: dict[tuple[str, HolidayScope], Holidays] = {
            (calendar.country, calendar.scope): calendar for calendar in holidays
        }
        self._rates: UnitRates = {} if rates is None else rates

    def _holidays(self, country: str, scope: HolidayScope) -> Holidays:
        calendar = self._calendars.get((country, scope))
        if calendar is None:
            calendar = load_holidays(self._session, country=country, scope=scope)
            self._calendars[country, scope] = calendar
        return calendar

    def lookup(
        self,
        unit_code: str,
        on: date,
        rule: ConversionRule,
        *,
        country: str = "MX",
        scope: HolidayScope = HolidayScope.BANXICO,
    ) -> UnitValueRow:
        """Find the row a conversion on ``on`` under ``rule`` reads.

        A date no current row covers raises ``UnitValueMissingError`` (503).

        Returns:
            The current row in force on the resolved date.

        """
        row, _ = self._resolve(unit_code, on, rule, country=country, scope=scope)
        return row

    def _resolve(
        self,
        unit_code: str,
        on: date,
        rule: ConversionRule,
        *,
        country: str = "MX",
        scope: HolidayScope = HolidayScope.BANXICO,
    ) -> tuple[UnitValueRow, date]:
        _ = unit_spec(unit_code)
        resolved = resolve_date(rule, on, holidays=self._holidays(country, scope))
        row = current_row(self._session, unit_code, resolved)
        if row is None:
            msg = f"no {unit_code} value for {resolved.isoformat()}"
            raise UnitValueMissingError(msg)
        return row, resolved

    def _rate(
        self, unit_code: str, on: date, rule: ConversionRule
    ) -> tuple[int, Decimal, date]:
        """Resolve the date and read its value through ``rates``.

        Returns:
            The row's id, its value and the resolved date.

        """
        _ = unit_spec(unit_code)
        resolved = resolve_date(
            rule, on, holidays=self._holidays("MX", HolidayScope.BANXICO)
        )
        known = self._rates.get((unit_code, resolved))
        if known is None:
            row, _ = self._resolve(unit_code, on, rule)
            known = self._rates[unit_code, resolved] = (row.id, row.value)
        row_id, value = known
        return row_id, value, resolved

    def to_unit(
        self, amount: Decimal, currency: str, unit: str, on: date, rule: ConversionRule
    ) -> Conversion:
        """Convert an amount in ``currency`` into ``unit``.

        Returns:
            The amount in the unit, to six places, with the row it used.

        """
        _check_currency(currency)
        if unit == currency:
            return self._identity(amount, unit, currency, on, rule, UNIT_PLACES)
        row_id, value, resolved = self._rate(_unit_code_of(unit), on, rule)
        return Conversion(
            amount=(amount / value).quantize(UNIT_PLACES, rounding=ROUND_HALF_UP),
            unit_value_id=row_id,
            rule=rule,
            on=on,
            resolved_date=resolved,
            unit=unit,
            currency=currency,
            rate=value,
        )

    def threshold_in_ccy(
        self, threshold: Quantity, currency: str, on: date, rule: ConversionRule
    ) -> Conversion:
        """Convert a threshold written in a unit into ``currency``.

        Returns:
            The amount in the currency, to two places, with the row it used.

        """
        _check_currency(currency)
        if threshold.unit == currency:
            return self._identity(
                threshold.amount, threshold.unit, currency, on, rule, CCY_PLACES
            )
        row_id, value, resolved = self._rate(_unit_code_of(threshold.unit), on, rule)
        return Conversion(
            amount=(threshold.amount * value).quantize(
                CCY_PLACES, rounding=ROUND_HALF_UP
            ),
            unit_value_id=row_id,
            rule=rule,
            on=on,
            resolved_date=resolved,
            unit=threshold.unit,
            currency=currency,
            rate=value,
        )

    @staticmethod
    def _identity(
        amount: Decimal,
        unit: str,
        currency: str,
        on: date,
        rule: ConversionRule,
        places: Decimal,
    ) -> Conversion:
        return Conversion(
            amount=amount.quantize(places, rounding=ROUND_HALF_UP),
            unit_value_id=None,
            rule=rule,
            on=on,
            resolved_date=on,
            unit=unit,
            currency=currency,
            rate=Decimal(1),
        )
