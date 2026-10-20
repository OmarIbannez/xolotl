"""The canonical transaction (SP-04 spec §4, plan 4a task A3).

Validation with field errors, the content hash and the enriched view.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    EnrichedTransaction,
    FieldError,
    FieldErrorCode,
    Instrument,
    KeyKind,
    PeriodKeys,
    Rail,
    TransactionInput,
    UnitAmount,
    amount_minor,
    content_hash,
    parse_item,
    validate_batch,
)
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus

NOW = datetime(2026, 12, 13, 12, tzinfo=UTC)
MAX_FUTURE = 10
DROP = object()


def item(**overrides: object) -> dict[str, object]:
    """Build a valid cash deposit with ``overrides`` (``DROP`` removes a key).

    Returns:
        The item as a client would submit it.

    """
    data: dict[str, object] = {
        "external_id": "dep-0001",
        "occurred_at": "2026-12-13T05:15:00-06:00",
        "customer_ref": "CLI-001",
        "direction": "IN",
        "rail": "CASH",
        "instrument": "CASH",
        "amount": "15000.00",
        "currency": "MXN",
        "channel": "BRANCH",
        "raw": {"sucursal": "Centro"},
    }
    for key, value in overrides.items():
        if value is DROP:
            _ = data.pop(key, None)
        else:
            data[key] = value
    return data


def parse(data: dict[str, object]) -> TransactionInput | list[FieldError]:
    return parse_item(0, data, now=NOW, max_future_minutes=MAX_FUTURE)


def codes(result: TransactionInput | list[FieldError]) -> list[tuple[str, str]]:
    assert isinstance(result, list), result
    return [(error.field, error.code) for error in result]


def valid(result: TransactionInput | list[FieldError]) -> TransactionInput:
    assert isinstance(result, TransactionInput), result
    return result


def test_a_valid_item_parses_and_hashes_deterministically() -> None:
    first = valid(parse(item()))
    assert first.direction is Direction.IN
    assert first.rail is Rail.CASH
    assert first.instrument is Instrument.CASH
    assert first.channel is Channel.BRANCH
    assert first.amount == Decimal("15000.00")
    assert first.occurred_at == datetime(2026, 12, 13, 11, 15, tzinfo=UTC)

    again = valid(parse(item()))
    assert content_hash(first) == content_hash(again)
    assert len(content_hash(first)) == 32

    changed = valid(parse(item(raw={"sucursal": "Norte"})))
    assert content_hash(changed) != content_hash(first)


def test_the_hash_ignores_how_an_amount_or_an_instant_is_written() -> None:
    plain = valid(parse(item()))
    spelled = valid(
        parse(item(amount=Decimal(15000), occurred_at="2026-12-13T11:15:00Z"))
    )
    assert content_hash(spelled) == content_hash(plain)


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"external_id": DROP}, "external_id", FieldErrorCode.REQUIRED),
        ({"direction": "SIDEWAYS"}, "direction", FieldErrorCode.INVALID),
        ({"amount": "0"}, "amount", FieldErrorCode.OUT_OF_RANGE),
        ({"amount": "-5.00"}, "amount", FieldErrorCode.OUT_OF_RANGE),
        ({"amount": "10.001"}, "amount", FieldErrorCode.OUT_OF_RANGE),
        ({"amount": "diez"}, "amount", FieldErrorCode.INVALID),
        (
            {"occurred_at": "2026-12-13T12:11:00+00:00"},
            "occurred_at",
            FieldErrorCode.FUTURE,
        ),
        (
            {"occurred_at": "2026-12-13T10:15:00"},
            "occurred_at",
            FieldErrorCode.NAIVE_DATETIME,
        ),
        ({"instrument": "CRYPTO"}, "crypto", FieldErrorCode.CONTEXT_REQUIRED),
        (
            {"counterparty": {"institution": "BBVA"}},
            "counterparty",
            FieldErrorCode.COUNTERPARTY_EMPTY,
        ),
        ({"external_id": "x" * 129}, "external_id", FieldErrorCode.TOO_LONG),
        (
            {"counterparty": {"name": "N" * 301}},
            "counterparty.name",
            FieldErrorCode.TOO_LONG,
        ),
        ({"x": 1}, "x", FieldErrorCode.INVALID),
        ({"currency": "EUR"}, "currency", FieldErrorCode.INVALID),
        ({"currency": "mxn"}, "currency", FieldErrorCode.INVALID),
        (
            {"device": {"geo_country": "MEX"}},
            "device.geo_country",
            FieldErrorCode.INVALID,
        ),
        ({"amount": "1e30"}, "amount", FieldErrorCode.OUT_OF_RANGE),
        ({"amount": "1E+26"}, "amount", FieldErrorCode.OUT_OF_RANGE),
        (
            {"amount": "99999999999999999999999999.99"},
            "amount",
            FieldErrorCode.OUT_OF_RANGE,
        ),
        ({"amount": "10000000000000000.00"}, "amount", FieldErrorCode.OUT_OF_RANGE),
        ({"fee_amount": "1e30"}, "fee_amount", FieldErrorCode.OUT_OF_RANGE),
        ({"fee_amount": "-0.01"}, "fee_amount", FieldErrorCode.OUT_OF_RANGE),
        ({"fee_amount": "1.001"}, "fee_amount", FieldErrorCode.OUT_OF_RANGE),
    ],
)
def test_each_rule_reports_its_field_and_code(
    overrides: dict[str, object], field: str, code: str
) -> None:
    assert codes(parse(item(**overrides))) == [(field, code)]


def test_the_largest_amount_fits_a_bigint_of_minor_units() -> None:
    largest = valid(parse(item(amount="9999999999999999.99")))
    assert amount_minor(largest.amount) == 999_999_999_999_999_999
    assert amount_minor(largest.amount) < 2**63 - 1


def test_the_hash_ignores_how_a_fee_is_written() -> None:
    plain = valid(parse(item(fee_amount="4")))
    spelled = valid(parse(item(fee_amount="4.00")))
    assert plain.fee_amount == Decimal("4.00")
    assert content_hash(spelled) == content_hash(plain)
    assert content_hash(plain) != content_hash(valid(parse(item())))


def test_the_hash_ignores_how_a_crypto_quantity_or_an_fx_rate_is_written() -> None:
    def crypto(qty: str) -> TransactionInput:
        context = {"network": "ETH", "asset_code": "USDT", "asset_qty": qty}
        return valid(parse(item(instrument="CRYPTO", crypto=context)))

    def fx(rate: str) -> TransactionInput:
        context = {
            "from_ccy": "USD",
            "to_ccy": "MXN",
            "rate": rate,
            "from_amount_minor": 10000,
            "to_amount_minor": 185000,
        }
        return valid(parse(item(instrument="FX", fx=context)))

    assert content_hash(crypto("1.5")) == content_hash(crypto("1.50"))
    assert content_hash(crypto("10")) == content_hash(crypto("10.000"))
    assert content_hash(crypto("10")) != content_hash(crypto("1"))
    ten = crypto("1E+1").crypto
    assert ten is not None
    assert str(ten.asset_qty) == "10"
    assert content_hash(fx("18.5")) == content_hash(fx("18.500"))
    assert content_hash(fx("18")) == content_hash(fx("18.00"))
    assert content_hash(fx("18.5")) != content_hash(fx("18.05"))


def test_up_to_the_future_allowance_is_accepted() -> None:
    nine_ahead = (NOW + timedelta(minutes=9)).isoformat()
    _ = valid(parse(item(occurred_at=nine_ahead)))


def test_context_is_required_per_instrument_and_rail() -> None:
    crypto = {"network": "ETH", "asset_code": "USDT", "asset_qty": "100.5"}
    fx = {
        "from_ccy": "USD",
        "to_ccy": "MXN",
        "rate": "18.50",
        "from_amount_minor": 10000,
        "to_amount_minor": 185000,
    }
    assert codes(parse(item(instrument="CRYPTO"))) == [
        ("crypto", FieldErrorCode.CONTEXT_REQUIRED)
    ]
    _ = valid(parse(item(instrument="CRYPTO", crypto=crypto)))
    assert codes(parse(item(instrument="FX"))) == [
        ("fx", FieldErrorCode.CONTEXT_REQUIRED)
    ]
    _ = valid(parse(item(instrument="FX", fx=fx)))
    assert codes(parse(item(rail="CRYPTO_ONCHAIN"))) == [
        ("crypto.network", FieldErrorCode.CONTEXT_REQUIRED)
    ]
    blank_network = {**crypto, "network": " "}
    assert codes(parse(item(rail="CRYPTO_ONCHAIN", crypto=blank_network))) == [
        ("crypto.network", FieldErrorCode.CONTEXT_REQUIRED)
    ]
    _ = valid(parse(item(rail="CRYPTO_ONCHAIN", crypto=crypto)))


def test_a_counterparty_needs_one_of_name_account_wallet_or_identifier() -> None:
    assert codes(parse(item(counterparty={"institution": "BBVA"}))) == [
        ("counterparty", FieldErrorCode.COUNTERPARTY_EMPTY)
    ]
    parsed = valid(parse(item(counterparty={"wallet_address": "0xAbC"})))
    assert parsed.counterparty is not None
    assert parsed.counterparty.wallet_address == "0xAbC"


def test_duplicate_external_ids_in_a_batch_are_reported_on_the_later_index() -> None:
    items = [item(), item(external_id="dep-0002"), item(amount="99.00")]
    accepted, errors = validate_batch(items, now=NOW, max_future_minutes=MAX_FUTURE)
    assert [one.external_id for one in accepted] == ["dep-0001", "dep-0002"]
    assert errors == [
        FieldError(index=2, field="external_id", code=FieldErrorCode.DUPLICATE_IN_BATCH)
    ]


def test_errors_are_sorted_by_index_then_field() -> None:
    items = [
        item(),
        item(external_id="dep-0002", rail="NOPE", amount="0", currency="EUR"),
        item(external_id="dep-0003", direction=DROP, currency="EUR"),
    ]
    accepted, errors = validate_batch(items, now=NOW, max_future_minutes=MAX_FUTURE)
    assert [one.external_id for one in accepted] == ["dep-0001"]
    assert [(error.index, error.field, error.code) for error in errors] == [
        (1, "amount", FieldErrorCode.OUT_OF_RANGE),
        (1, "rail", FieldErrorCode.INVALID),
        (2, "direction", FieldErrorCode.REQUIRED),
    ]


def test_amount_minor_is_exact() -> None:
    assert amount_minor(Decimal("1234.56")) == 123456
    assert amount_minor(Decimal("0.01")) == 1
    assert amount_minor(Decimal(7)) == 700


def enriched(**overrides: object) -> EnrichedTransaction:
    data: dict[str, object] = {
        "id": uuid4(),
        "external_id": "dep-0001",
        "occurred_at": NOW,
        "received_at": NOW,
        "party_id": uuid4(),
        "relationship_id": uuid4(),
        "account_id": uuid4(),
        "product": "CUENTA_N2",
        "direction": Direction.IN,
        "rail": Rail.CASH,
        "instrument": Instrument.CASH,
        "channel": Channel.BRANCH,
        "amount_minor": 1500000,
        "currency": "MXN",
        "fee_minor": None,
        "asset_qty": None,
        "asset_code": None,
        "unit_amounts": {
            "MXN/SAME_DAY": UnitAmount(
                amount=Decimal("15000.00"),
                unit_value_id=None,
                resolved_date=date(2026, 12, 13),
            )
        },
        "counterparty_id": None,
        "counterparty_key": None,
        "counterparty_country": None,
        "counterparty_screening_status": None,
        "device_key": None,
        "geo_country": None,
        "geo_admin_area": None,
        "crypto": None,
        "fx": None,
        "purpose_code": None,
        "period_keys": PeriodKeys(
            DAY="2026-12-13", MONTH="2026-12", QUARTER="2026-Q4", BUSINESS_DAY=False
        ),
        "tier": "N2",
        "risk_rating": "LOW",
        "monitoring_profile": "STD",
        "policy_snapshot_id": uuid4(),
        "policy_country": "MX",
        "late": False,
    }
    data.update(overrides)
    return EnrichedTransaction.model_validate(data)


def test_enriched_transaction_keys_per_kind() -> None:
    txn = enriched()
    assert txn.key_for(KeyKind.PARTY) == str(txn.party_id)
    assert txn.key_for(KeyKind.RELATIONSHIP) == str(txn.relationship_id)
    assert txn.key_for(KeyKind.ACCOUNT) == str(txn.account_id)
    assert txn.key_for(KeyKind.COUNTERPARTY) is None
    assert txn.key_for(KeyKind.DEVICE) is None

    known = enriched(
        account_id=None,
        counterparty_id=uuid4(),
        counterparty_key="ab" * 32,
        counterparty_screening_status=CounterpartyScreeningStatus.CLEAR,
        device_key="cd" * 32,
    )
    assert known.key_for(KeyKind.ACCOUNT) is None
    assert known.key_for(KeyKind.COUNTERPARTY) == "ab" * 32
    assert known.key_for(KeyKind.DEVICE) == "cd" * 32


def test_enriched_transaction_reads_unit_amounts_and_is_frozen() -> None:
    txn = enriched()
    assert txn.unit_amount("MXN/SAME_DAY") == Decimal("15000.00")
    assert txn.unit_amount("UDI/SAME_DAY") is None
    assert txn.period_keys.MONTH == "2026-12"
    attribute = "late"
    with pytest.raises(ValueError, match="frozen"):
        setattr(txn, attribute, True)
