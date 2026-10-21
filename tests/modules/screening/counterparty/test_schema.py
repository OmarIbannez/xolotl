"""The counterparty input types a transaction carries (SP-04 spec §4, §11.1)."""

import pytest
from pydantic import ValidationError

from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
    CounterpartyKind,
)
from xolotl.modules.screening.engine.queries import AdHocIdentifier


def test_kind_defaults_to_unknown_and_extra_fields_are_refused() -> None:
    counterparty = CounterpartyInput(name="Comercial del Norte SA de CV")
    assert counterparty.kind is CounterpartyKind.UNKNOWN

    with pytest.raises(ValidationError) as caught:
        _ = CounterpartyInput.model_validate({"name": "Ana", "nickname": "Anita"})
    assert caught.value.errors()[0]["type"] == "extra_forbidden"


def test_a_counterparty_with_only_an_institution_is_empty() -> None:
    with pytest.raises(ValidationError, match="COUNTERPARTY_EMPTY"):
        _ = CounterpartyInput(institution="BBVA", country="MX")

    for one in (
        {"name": "Ana"},
        {"account": "012180001234567891"},
        {"wallet_address": "0xAbC123"},
        {"identifier": {"id_type": "RFC", "value": "PEGA760510AB1"}},
    ):
        assert CounterpartyInput.model_validate(one).kind is CounterpartyKind.UNKNOWN


@pytest.mark.parametrize(
    "data",
    [
        {"id_type": "RFC", "value": "PEGA760510AB1"},
        {"id_type": "PASSPORT", "value": "G12345678", "country": "ES"},
        {"id_type": "RFC", "value": ""},
        {"id_type": "RFC", "value": "X" * 65},
        {"id_type": "NOPE", "value": "1"},
        {"id_type": "CURP", "value": "1", "country": "mex"},
        {"id_type": "CURP", "value": "1", "extra": True},
    ],
)
def test_identifier_shape_matches_the_ad_hoc_identifier(
    data: dict[str, object],
) -> None:
    def valid(model: type[CounterpartyIdentifier] | type[AdHocIdentifier]) -> bool:
        try:
            _ = model.model_validate(data)
        except ValidationError:
            return False
        return True

    assert valid(CounterpartyIdentifier) == valid(AdHocIdentifier)
    assert set(CounterpartyIdentifier.model_fields) == set(AdHocIdentifier.model_fields)


def test_a_wallet_address_loses_its_whitespace_and_keeps_its_case() -> None:
    # The OFAC parser's rule for a listed address (``wallet_identifier``).
    counterparty = CounterpartyInput(wallet_address=" 0xAb C1\t23\n")
    assert counterparty.wallet_address == "0xAbC123"


def test_a_wallet_address_of_whitespace_only_is_absent() -> None:
    counterparty = CounterpartyInput(name="Ana", wallet_address=" \t ")
    assert counterparty.wallet_address is None

    with pytest.raises(ValidationError, match="COUNTERPARTY_EMPTY"):
        _ = CounterpartyInput(wallet_address="   ")
