"""Units and the regulatory calendar, for staff of any role (SP-02 §9).

The units' catalogue and dated values, the lookup a conversion would make, the
holidays of a scope and the evaluation of a deadline rule. Reads only.
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Query
from pydantic import ValidationError
from sqlalchemy import select

from xolotl.modules.tenants.schemas_policy import (
    DueResponse,
    HolidayResponse,
    HolidaysResponse,
    UnitLookupResponse,
    UnitsResponse,
    UnitSummaryResponse,
    UnitValueResponse,
    UnitValuesResponse,
)
from xolotl.platform import clock
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.calendar import DeadlineRule, Holiday, due_at, load_holidays
from xolotl.platform.enums import HolidayScope
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.units import (
    ConversionRule,
    UnitService,
    history,
    resolve_date,
    unit_spec,
    unit_summaries,
)

router = v1_router(tags=["units"])

StaffReader = Annotated[Actor, Depends(require_access())]

COUNTRY = "MX"
DEFAULT_SPAN = timedelta(days=90)
MAX_SPAN = timedelta(days=366)


@router.get("/units")
def list_units(_reader: StaffReader, session: SessionDep) -> UnitsResponse:
    return UnitsResponse(
        items=[
            UnitSummaryResponse.model_validate(summary)
            for summary in unit_summaries(session)
        ]
    )


@router.get("/units/{code}")
def lookup_unit(
    code: str,
    _reader: StaffReader,
    session: SessionDep,
    on: date | None = None,
    rule: ConversionRule = ConversionRule.SAME_DAY,
) -> UnitLookupResponse:
    day = on or clock.now().date()
    row = UnitService(session).lookup(code, day, rule)
    holidays = load_holidays(session, country=COUNTRY, scope=HolidayScope.BANXICO)
    return UnitLookupResponse(
        unit_code=row.unit_code,
        on=day,
        rule=rule,
        resolved_date=resolve_date(rule, day, holidays=holidays),
        value=row.value,
        quote_ccy=row.quote_ccy,
        unit_value_id=row.id,
        source=row.source,
        fetched_at=row.fetched_at,
    )


@router.get("/units/{code}/values")
def list_unit_values(
    code: str,
    _reader: StaffReader,
    session: SessionDep,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> UnitValuesResponse:
    spec = unit_spec(code)
    end = to or clock.now().date()
    start = from_ or end - DEFAULT_SPAN
    if start > end or end - start > MAX_SPAN:
        msg = "from must not be after to, and the span at most 366 days"
        raise ValidationFailedError(msg)
    rows = history(session, spec.code, start=start, end=end)
    return UnitValuesResponse(
        unit_code=spec.code,
        items=[UnitValueResponse.model_validate(row) for row in rows],
    )


@router.get("/calendar/holidays")
def list_holidays(
    _reader: StaffReader,
    session: SessionDep,
    scope: HolidayScope = HolidayScope.NATIONAL,
    year: Annotated[int | None, Query(ge=1, le=9999)] = None,
) -> HolidaysResponse:
    chosen = year or clock.now().year
    rows = session.scalars(
        select(Holiday)
        .where(
            Holiday.country_code == COUNTRY,
            Holiday.scope == scope,
            Holiday.day >= date(chosen, 1, 1),
            Holiday.day <= date(chosen, 12, 31),
        )
        .order_by(Holiday.day)
    )
    return HolidaysResponse(
        country=COUNTRY,
        scope=scope,
        year=chosen,
        items=[HolidayResponse.model_validate(row) for row in rows],
    )


@router.get("/calendar/due")
def evaluate_deadline(
    _reader: StaffReader,
    session: SessionDep,
    rule: Annotated[str, Query(max_length=1024)],
    anchor: datetime,
    scope: HolidayScope = HolidayScope.NATIONAL,
) -> DueResponse:
    try:
        parsed = DeadlineRule.model_validate_json(rule)
    except ValidationError as exc:
        msg = "invalid rule"
        raise ValidationFailedError(msg) from exc
    if anchor.utcoffset() is None:
        msg = "anchor must carry an offset"
        raise ValidationFailedError(msg)
    holidays = load_holidays(session, country=COUNTRY, scope=scope)
    return DueResponse(
        due_at=due_at(parsed, anchor, holidays=holidays),
        rule=parsed,
        anchor=anchor,
        scope=scope,
    )
