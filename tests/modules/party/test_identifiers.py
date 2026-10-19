"""Identifier validators (plan 2c task C1) pinned by the golden corpus.

Every file under ``tests/golden/identifiers`` lists raw inputs with the canonical value
and attributes ``validate`` must return, or the reason it must refuse them. The check
digit helpers are pinned separately against published numbers.
"""

import json
import string
from datetime import date
from pathlib import Path

import pytest

from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.party.identifiers import (
    IdentifierError,
    IdentifierReason,
    NormalizedIdentifier,
    clabe_check_digit,
    cnpj_check_digits,
    cpf_check_digits,
    cuit_check_digit,
    curp_check_digit,
    icao_check_digit,
    nit_check_digit,
    normalize,
    rfc_check_digit,
    rut_check_digit,
    validate,
)

CORPUS_DIR = Path(__file__).parents[2] / "golden" / "identifiers"


def _load() -> list[tuple[IdentifierType, dict[str, object], bool]]:
    samples: list[tuple[IdentifierType, dict[str, object], bool]] = []
    for path in sorted(CORPUS_DIR.glob("*.json")):
        corpus = json.loads(path.read_text(encoding="utf-8"))
        id_type = IdentifierType(corpus["id_type"])
        samples.extend((id_type, entry, True) for entry in corpus["valid"])
        samples.extend((id_type, entry, False) for entry in corpus["invalid"])
    return samples


SAMPLES = _load()
VALID = [(t, e) for t, e, ok in SAMPLES if ok]
INVALID = [(t, e) for t, e, ok in SAMPLES if not ok]


def _ids(samples: list[tuple[IdentifierType, dict[str, object]]]) -> list[str]:
    return [
        f"{t.value}-{i}-{e['input']!r}-{e.get('birth_date', '')}"
        for i, (t, e) in enumerate(samples)
    ]


def _date(value: object) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


@pytest.mark.parametrize(("id_type", "entry"), VALID, ids=_ids(VALID))
def test_valid_samples_normalise_to_their_canonical_value(id_type, entry):
    result = validate(
        id_type, entry["input"], birth_date=_date(entry.get("birth_date"))
    )
    assert isinstance(result, NormalizedIdentifier), result
    assert result.id_type is id_type
    assert result.value == entry["canonical"]
    assert result.attributes == entry.get("attributes", {})


@pytest.mark.parametrize(("id_type", "entry"), INVALID, ids=_ids(INVALID))
def test_invalid_samples_name_their_reason(id_type, entry):
    result = validate(
        id_type, entry["input"], birth_date=_date(entry.get("birth_date"))
    )
    assert result == IdentifierError(id_type, IdentifierReason(entry["reason"]))


def test_every_identifier_type_has_a_corpus():
    assert {p.stem for p in CORPUS_DIR.glob("*.json")} == {
        t.value for t in IdentifierType
    }


@pytest.mark.parametrize(("id_type", "entry"), VALID, ids=_ids(VALID))
def test_normalising_a_canonical_value_changes_nothing(id_type, entry):
    assert normalize(id_type, entry["canonical"]) == entry["canonical"]
    result = validate(id_type, entry["canonical"])
    assert isinstance(result, NormalizedIdentifier), result
    assert result.value == entry["canonical"]


def test_check_digit_helpers_agree_with_published_numbers():
    assert curp_check_digit("CAQR521024HSLRNF0") == 6
    assert rfc_check_digit("EKU9003173C") == "9"
    assert rfc_check_digit("ACM0101017A") == "0"
    assert clabe_check_digit("00201007777777777") == "1"
    assert cpf_check_digits("529982247") == "25"
    assert cnpj_check_digits("003944600058") == "87"
    assert cuit_check_digit("3369345023") == 9
    assert cuit_check_digit("2012345678") == 6
    assert rut_check_digit("12345678") == "5"
    assert rut_check_digit("6") == "K"
    assert nit_check_digit("800197268") == 4
    assert icao_check_digit("L898902C3") == 6


def test_rfc_check_digit_ten_is_written_a():
    # Xolo's test: some of these bodies have the check value ten, written "A".
    bodies = [f"AAA0101{day:02d}AB" for day in range(1, 29)]
    with_a = [b for b in bodies if rfc_check_digit(b) == "A"]
    assert with_a
    assert all(
        isinstance(validate(IdentifierType.RFC, b + "A"), NormalizedIdentifier)
        for b in with_a
    )


def test_a_cuit_body_with_no_check_digit_is_refused():
    # A remainder of one leaves no single check digit; AFIP never issues such a body.
    body = next(
        f"20{n:08d}" for n in range(1000) if cuit_check_digit(f"20{n:08d}") is None
    )
    for digit in string.digits:
        assert validate(IdentifierType.CUIT, body + digit) == IdentifierError(
            IdentifierType.CUIT, IdentifierReason.CHECK_DIGIT
        )


def test_blank_input_is_a_format_error_for_every_type():
    for id_type in IdentifierType:
        for raw in ("", "   ", "\t\n"):
            assert validate(id_type, raw) == IdentifierError(
                id_type, IdentifierReason.FORMAT
            )


def test_fullwidth_and_foreign_digits_never_reach_the_canonical_form():
    # NFKC folds fullwidth characters; a non-ASCII digit is never a digit here.
    fullwidth = "".join(chr(ord(c) + 0xFEE0) for c in "CAQR521024HSLRNF06")
    result = validate(IdentifierType.CURP, fullwidth)
    assert isinstance(result, NormalizedIdentifier), result
    assert result.value == "CAQR521024HSLRNF06"
    arabic_indic = "".join(chr(0x0660 + int(c)) for c in "12345678")
    assert validate(IdentifierType.DNI_PE, arabic_indic) == IdentifierError(
        IdentifierType.DNI_PE, IdentifierReason.FORMAT
    )


def test_an_mrz_field_of_padding_only_is_a_format_error():
    assert validate(IdentifierType.PASSPORT, "<<<<<<<<<0") == IdentifierError(
        IdentifierType.PASSPORT, IdentifierReason.FORMAT
    )


def test_the_error_carries_no_value():
    assert "EKU9003173C1" not in repr(validate(IdentifierType.RFC, "EKU9003173C1"))
