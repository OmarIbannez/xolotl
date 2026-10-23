"""Units: the store and its corrections, the seed files and UnitService (A4)."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import platform_ctx
from tests.platform.units_support import (
    REPO,
    UNIT_GOLDEN,
    load_unit_values,
    seed_mx_holidays,
)
from xolotl.platform.calendar import Holidays, load_holidays
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Confidence, HolidayScope
from xolotl.platform.errors import ValidationFailedError, XolotlError
from xolotl.platform.units import (
    UNITS,
    ConversionRule,
    Quantity,
    SeedFileSource,
    UnitRates,
    UnitService,
    UnitValue,
    UnitValueRow,
    UnverifiedValueError,
    history,
    latest_valid_from,
    read_seed_file,
    resolve_date,
    unit_spec,
    unit_summaries,
    upsert_value,
)

SEEDS = REPO / "policy-packs/_units"
# PyYAML reads the bare key ``on`` as the boolean true (YAML 1.1); name it back.
CONVERSIONS: list[dict[str, Any]] = [
    {("on" if key is True else key): value for key, value in case.items()}
    for case in yaml.safe_load(
        (UNIT_GOLDEN / "conversions.yaml").read_text(encoding="utf-8")
    )
]
FETCHED_AT = datetime.fromisoformat("2026-01-20T12:00:00+00:00")

Ids = dict[tuple[str, date], int]


@contextmanager
def _session(engine: Engine) -> Generator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=factory) as s:
        yield s


@pytest.fixture
def loaded(engine: Engine, admin_engine: Engine) -> Ids:
    seed_mx_holidays(admin_engine)
    with _session(engine) as s:
        return load_unit_values(s, UNIT_GOLDEN / "values.yaml")


@pytest.mark.parametrize("case", CONVERSIONS, ids=[c["id"] for c in CONVERSIONS])
def test_conversion_goldens(case: dict[str, Any], loaded: Ids, engine: Engine):
    rule = ConversionRule(case["rule"])
    with _session(engine) as s:
        service = UnitService(s)

        def run() -> Any:
            if case["op"] == "to_unit":
                return service.to_unit(
                    Decimal(case["amount"]),
                    case["currency"],
                    case["unit"],
                    case["on"],
                    rule,
                )
            return service.threshold_in_ccy(
                Quantity.model_validate(case["threshold"]),
                case["currency"],
                case["on"],
                rule,
            )

        if "error" in case:
            with pytest.raises(XolotlError) as caught:
                _ = run()
            assert caught.value.code == case["error"]["code"]
            if "detail" in case["error"]:
                assert caught.value.detail == case["error"]["detail"]
            return
        conv = run()
    expected = case["expected"]
    assert conv.amount == Decimal(expected["amount"])
    assert str(conv.amount) == expected["amount"]
    assert conv.resolved_date == expected["resolved_date"]
    assert conv.rate == Decimal(expected["rate"])
    row = expected["row"]
    assert conv.unit_value_id == (loaded[row[0], row[1]] if row else None)
    assert conv.rule is rule
    assert conv.on == case["on"]


@contextmanager
def _holiday_reads(engine: Engine) -> Generator[list[str]]:
    reads: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:
        if "FROM holiday" in statement:
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", keep)
    try:
        yield reads
    finally:
        event.remove(engine, "before_cursor_execute", keep)


def _two_prior_banking_day_conversions(service: UnitService) -> list[date]:
    usd = Quantity(amount=Decimal(5000), unit="USD")
    return [
        service.threshold_in_ccy(
            usd, "MXN", on, ConversionRule.PRIOR_BANKING_DAY
        ).resolved_date
        for on in (date(2025, 12, 15), date(2025, 12, 16))
    ]


def test_a_unit_service_reads_each_calendar_once(loaded: Ids, engine: Engine):
    with _session(engine) as s, _holiday_reads(engine) as reads:
        resolved = _two_prior_banking_day_conversions(UnitService(s))
    # 12 December is a bank holiday: the calendar was read, once for both.
    assert resolved == [date(2025, 12, 11), date(2025, 12, 15)]
    assert len(reads) == 1


def test_a_unit_service_given_the_calendar_reads_none(loaded: Ids, engine: Engine):
    with _session(engine) as s:
        banxico = load_holidays(s, country="MX", scope=HolidayScope.BANXICO)
    with _session(engine) as s, _holiday_reads(engine) as reads:
        resolved = _two_prior_banking_day_conversions(
            UnitService(s, holidays=[banxico])
        )
    assert resolved == [date(2025, 12, 11), date(2025, 12, 15)]
    assert reads == []


@contextmanager
def _unit_value_reads(engine: Engine) -> Generator[list[str]]:
    reads: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:
        if "FROM unit_value" in statement:
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", keep)
    try:
        yield reads
    finally:
        event.remove(engine, "before_cursor_execute", keep)


def test_unit_services_sharing_rates_read_each_value_once(loaded: Ids, engine: Engine):
    rates: UnitRates = {}
    usd = Quantity(amount=Decimal(5000), unit="USD")
    on = date(2025, 12, 16)
    with _unit_value_reads(engine) as reads:
        conversions = []
        for _ in range(2):  # two rows, each in a transaction of its own
            with _session(engine) as s:
                service = UnitService(s, rates=rates)
                conversions.append(
                    service.threshold_in_ccy(
                        usd, "MXN", on, ConversionRule.PRIOR_BANKING_DAY
                    )
                )
    assert len(reads) == 1
    first, second = conversions
    assert first == second
    assert first.unit_value_id == loaded["USD_MXN_FIX", date(2025, 12, 15)]
    assert first.amount == Decimal("92000.00")


def test_a_missing_value_is_not_remembered(loaded: Ids, engine: Engine):
    rates: UnitRates = {}
    usd = Quantity(amount=Decimal(5000), unit="USD")
    far = date(2030, 1, 15)
    with _session(engine) as s, _unit_value_reads(engine) as reads:
        service = UnitService(s, rates=rates)
        for _ in range(2):
            with pytest.raises(XolotlError):
                _ = service.threshold_in_ccy(usd, "MXN", far, ConversionRule.SAME_DAY)
    # Asked again, the store is read again: a value loaded meanwhile is used.
    assert len(reads) == 2
    assert rates == {}


def _fix(valid_from: date, value: str) -> UnitValue:
    return UnitValue(
        unit_code="USD_MXN_FIX",
        valid_from=valid_from,
        valid_to=valid_from,
        value=Decimal(value),
        quote_ccy="MXN",
        source="fixture",
        confidence=Confidence.VERIFIED,
    )


def test_a_correction_supersedes_once(loaded: Ids, engine: Engine):
    day = date(2025, 12, 11)
    current = loaded["USD_MXN_FIX", day]
    with _session(engine) as s:
        rows = history(s, "USD_MXN_FIX", start=day, end=day)
        assert [r.value for r in rows] == [Decimal("18.25"), Decimal("18.20")]
        assert rows[0].id == current
        assert rows[0].superseded_by is None
        assert rows[1].superseded_by == current
        count = s.scalar(select(func.count()).select_from(UnitValueRow))
        assert upsert_value(s, _fix(day, "18.2500"), fetched_at=FETCHED_AT) is None
        assert s.scalar(select(func.count()).select_from(UnitValueRow)) == count
        earlier = FETCHED_AT - timedelta(days=1)
        newer = upsert_value(s, _fix(day, "18.3000"), fetched_at=earlier)
        assert newer is not None
        old = s.get(UnitValueRow, current)
        assert old is not None
        assert old.superseded_by == newer
        row = UnitService(s).lookup("USD_MXN_FIX", day, ConversionRule.SAME_DAY)
        assert row.id == newer
        assert len(history(s, "USD_MXN_FIX", start=day, end=day)) == 3
        assert latest_valid_from(s, "USD_MXN_FIX") == date(2026, 1, 19)
        assert latest_valid_from(s, "NOPE") is None


def test_lookup_rejects_unknown_units(loaded: Ids, engine: Engine):
    with _session(engine) as s, pytest.raises(ValidationFailedError) as caught:
        _ = UnitService(s).lookup("EUR", date(2026, 1, 20), ConversionRule.SAME_DAY)
    assert caught.value.status == 422
    assert caught.value.detail == "unknown unit EUR"
    with pytest.raises(ValidationFailedError, match="unknown unit EUR"):
        _ = unit_spec("EUR")


def test_seed_files_have_the_pinned_shape():
    uma = read_seed_file(SEEDS / "UMA.yaml")
    assert [(r.valid_from, r.valid_to, r.confidence) for r in uma] == [
        (date(2024, 2, 1), date(2025, 1, 31), Confidence.VERIFIED),
        (date(2025, 2, 1), date(2026, 1, 31), Confidence.VERIFIED),
        (date(2026, 2, 1), date(2027, 1, 31), Confidence.VERIFIED),
    ]
    assert [str(r.value) for r in uma] == ["108.57", "113.14", "117.31"]
    assert all(r.value > 0 for r in uma)

    udi = read_seed_file(SEEDS / "UDI.seed.yaml")
    assert [r.valid_from for r in udi] == [
        date(2025, 12, 1) + timedelta(days=i) for i in range(62)
    ]
    assert all(r.valid_to == r.valid_from for r in udi)
    assert all(r.value > 0 for r in udi)
    assert all(int(r.value.as_tuple().exponent) >= -6 for r in udi)
    assert len({r.confidence for r in udi}) == 1

    fix = read_seed_file(SEEDS / "USD_MXN_FIX.seed.yaml")
    closed = {date(2025, 12, 12), date(2025, 12, 25), date(2026, 1, 1)}
    days = [r.valid_from for r in fix]
    assert days
    assert all(date(2025, 12, 1) <= d <= date(2026, 1, 31) for d in days)
    assert not closed & set(days)
    assert all(d.weekday() < 5 for d in days)
    assert all(r.valid_to == r.valid_from for r in fix)
    assert all(r.value > 0 for r in fix)
    assert all(int(r.value.as_tuple().exponent) >= -4 for r in fix)
    assert len({r.confidence for r in fix}) == 1
    for rows, spec in ((uma, "UMA"), (udi, "UDI"), (fix, "USD_MXN_FIX")):
        assert {r.unit_code for r in rows} == {spec}
        assert {r.quote_ccy for r in rows} == {UNITS[spec].quote_ccy}


def _seeds_with_unverified_uma_2026(directory: Path) -> Path:
    """Copy the committed seeds into ``directory``, UMA 2026 marked UNVERIFIED.

    Returns:
        ``directory``, which now holds the copies.

    """
    for seed in SEEDS.glob("*.yaml"):
        text_ = seed.read_text(encoding="utf-8")
        if seed.name == "UMA.yaml":
            row = next(line for line in text_.splitlines() if "2026-02-01" in line)
            flipped = row.replace("confidence: VERIFIED", "confidence: UNVERIFIED")
            text_ = text_.replace(row, flipped)
        _ = (directory / seed.name).write_text(text_, encoding="utf-8")
    return directory


def test_seed_source_refuses_unverified_rows(tmp_path: Path):
    seeds = _seeds_with_unverified_uma_2026(tmp_path)
    with pytest.raises(UnverifiedValueError, match="UMA 2026-02-01") as caught:
        _ = SeedFileSource(seeds).fetch("UMA", date(2024, 1, 1), date(2027, 12, 31))
    assert caught.value.code == "UNVERIFIED_VALUE"
    with pytest.raises(UnverifiedValueError, match="UMA 2026-02-01"):
        _ = SeedFileSource(seeds).latest_available("UMA")
    source = SeedFileSource(seeds, allow_unverified=True)
    assert len(source.fetch("UMA", date(2024, 1, 1), date(2027, 12, 31))) == 3
    latest = source.latest_available("UDI")
    assert latest is not None
    assert latest.valid_from == date(2026, 1, 31)
    window = source.fetch("UDI", date(2026, 1, 10), date(2026, 1, 12))
    assert [r.valid_from for r in window] == [
        date(2026, 1, 10),
        date(2026, 1, 11),
        date(2026, 1, 12),
    ]
    verified = SeedFileSource(seeds).fetch("UMA", date(2024, 1, 1), date(2025, 12, 31))
    assert [r.valid_from for r in verified] == [date(2024, 2, 1), date(2025, 2, 1)]


def test_the_committed_seeds_hold_no_unverified_rows():
    source = SeedFileSource(SEEDS)
    assert len(source.fetch("UMA", date(2024, 1, 1), date(2027, 12, 31))) == 3
    latest = source.latest_available("UMA")
    assert latest is not None
    assert latest.valid_from == date(2026, 2, 1)


_HEADER = (
    "unit_code: {unit}\nquote_ccy: {ccy}\nsource: s\nlegal_basis: b\n"
    "confidence: VERIFIED\nverified_as_of: 2026-01-01\nvalues:\n"
)


@pytest.mark.parametrize(
    ("unit", "ccy", "rows", "match"),
    [
        ("EUR", "MXN", '  - {valid_from: 2026-01-01, value: "1"}\n', "unknown unit"),
        ("UDI", "USD", '  - {valid_from: 2026-01-01, value: "1"}\n', "quoted in"),
        ("UDI", "MXN", "  - {valid_from: 2026-01-01, value: 1.5}\n", "string"),
        ("UDI", "MXN", '  - {valid_from: 2026-01-01, value: "0"}\n', "not positive"),
        (
            "UDI",
            "MXN",
            '  - {valid_from: 2026-01-01, value: "1"}\n' * 2,
            "listed twice",
        ),
        (
            "UDI",
            "MXN",
            '  - {valid_from: 2026-01-01, valid_to: 2026-01-02, value: "1"}\n',
            "is daily",
        ),
        (
            "UMA",
            "MXN",
            '  - {valid_from: 2026-02-01, valid_to: 2026-01-01, value: "1"}\n',
            "ends before",
        ),
    ],
)
def test_read_seed_file_refuses_a_malformed_file(
    tmp_path: Path, unit: str, ccy: str, rows: str, match: str
):
    path = tmp_path / "seed.yaml"
    _ = path.write_text(_HEADER.format(unit=unit, ccy=ccy) + rows, encoding="utf-8")
    with pytest.raises(ValidationFailedError, match=match) as caught:
        _ = read_seed_file(path)
    assert str(path) in caught.value.detail


def test_read_seed_file_refuses_a_missing_or_broken_file(tmp_path: Path):
    with pytest.raises(ValidationFailedError, match=r"missing\.yaml"):
        _ = read_seed_file(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    _ = broken.write_text("values: [", encoding="utf-8")
    with pytest.raises(ValidationFailedError, match=r"broken\.yaml"):
        _ = read_seed_file(broken)


def test_seed_source_refuses_a_file_of_another_unit(tmp_path: Path):
    other = (SEEDS / "UDI.seed.yaml").read_text(encoding="utf-8")
    _ = (tmp_path / "USD_MXN_FIX.seed.yaml").write_text(other, encoding="utf-8")
    with pytest.raises(ValidationFailedError, match="holds UDI"):
        _ = SeedFileSource(tmp_path).latest_available("USD_MXN_FIX")


def test_unit_summaries_report_the_latest_rows(loaded: Ids, engine: Engine):
    with _session(engine) as s:
        summaries = unit_summaries(s)
    assert [x.unit_code for x in summaries] == list(UNITS)
    by_code = {x.unit_code: x for x in summaries}
    assert by_code["UDI"].latest_valid_from == date(2026, 1, 21)
    assert by_code["USD_MXN_FIX"].latest_valid_from == date(2026, 1, 19)
    assert by_code["UMA"].latest_valid_from == date(2025, 2, 1)
    assert all(x.latest_source == "fixture" for x in summaries)
    assert all(x.latest_fetched_at == FETCHED_AT for x in summaries)
    assert by_code["UDI"].series_id == "SP68257"
    assert by_code["UMA"].daily is False


def test_unit_summaries_of_an_empty_store(engine: Engine):
    with _session(engine) as s:
        summaries = unit_summaries(s)
    assert all(x.latest_valid_from is None for x in summaries)
    assert all(x.latest_source is None for x in summaries)


@pytest.mark.parametrize(
    ("rule", "on", "expected"),
    [
        (ConversionRule.SAME_DAY, date(2026, 1, 20), date(2026, 1, 20)),
        (ConversionRule.LAST_DAY_PREV_MONTH, date(2026, 3, 1), date(2026, 2, 28)),
        (ConversionRule.SEMIANNUAL_SNAPSHOT, date(2026, 6, 30), date(2025, 12, 31)),
        (ConversionRule.SEMIANNUAL_SNAPSHOT, date(2026, 7, 1), date(2026, 6, 30)),
        (ConversionRule.PRIOR_BANKING_DAY, date(2026, 1, 5), date(2026, 1, 2)),
    ],
)
def test_resolve_date(rule: ConversionRule, on: date, expected: date):
    holidays = Holidays(country="MX", scope=HolidayScope.BANXICO, days=frozenset())
    assert resolve_date(rule, on, holidays=holidays) == expected
