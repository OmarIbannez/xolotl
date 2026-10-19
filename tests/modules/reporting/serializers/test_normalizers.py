"""Normalizers and the character rules of both regimes (no database)."""

import re
import string
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xolotl.modules.reporting.serializers import normalizers as n
from xolotl.modules.reporting.serializers.errors import ReportFieldError

F41_ALLOWED = frozenset(string.printable[:95]) - {";"}


# ---- Formato 41 text --------------------------------------------------------------


def test_f41_text_folds_to_upper_case_ascii():
    assert n.f41_text("Peña Nieto, José Ángel") == "PENA NIETO, JOSE ANGEL"
    assert n.f41_text("Müller  Straße\tgüero") == "MULLER STRASSE GUERO"


def test_f41_text_never_keeps_the_separator_or_control_characters():
    assert n.f41_text("Av. Juárez 10; int. 3\r\nCol. Centro") == (
        "AV. JUAREZ 10 INT. 3 COL. CENTRO"
    )


def test_f41_text_turns_characters_without_an_ascii_form_into_spaces():
    assert n.f41_text("東京 Tower €5") == "TOWER 5"


def test_f41_text_composes_decomposed_input():
    assert n.f41_text("Peña") == "PENA"


def test_f41_text_of_nothing_is_empty():
    assert not n.f41_text(None)
    assert not n.f41_text("  \t ")


@given(st.text())
def test_f41_text_is_printable_ascii_without_the_separator(value):
    cleaned = n.f41_text(value)
    assert set(cleaned) <= F41_ALLOWED
    assert cleaned == cleaned.strip()
    assert "  " not in cleaned


@given(st.text())
def test_f41_text_is_idempotent(value):
    once = n.f41_text(value)
    assert n.f41_text(once) == once


@given(st.text(), st.integers(min_value=1, max_value=80))
def test_fitted_f41_text_never_exceeds_its_width(value, width):
    fitted, cut = n.fit(n.f41_text(value), width)
    assert len(fitted) <= width
    assert cut == (len(n.f41_text(value)) > width)


def test_fit_keeps_short_values_and_cuts_long_ones():
    assert n.fit("BAPK850101", 13) == ("BAPK850101", False)
    assert n.fit("ABCDEFGHIJ", 4) == ("ABCD", True)


# ---- Aviso text -------------------------------------------------------------------


def test_aviso_text_keeps_the_enye_and_strips_other_diacritics():
    assert n.aviso_text("Compañía Ñandú  Álvarez") == "COMPAÑIA ÑANDU ALVAREZ"
    assert n.aviso_text("Peña") == "PEÑA"
    assert not n.aviso_text(None)


@given(st.text())
def test_aviso_text_has_no_accented_vowel_and_is_idempotent(value):
    cleaned = n.aviso_text(value)
    assert not set(cleaned) & set("ÁÉÍÓÚÜáéíóúüàèìòù")
    assert n.aviso_text(cleaned) == cleaned


# ---- Identifiers ------------------------------------------------------------------


def test_rfc_and_curp_are_compacted_and_shape_checked():
    assert n.rfc("pepj-910101 f45") == "PEPJ910101F45"
    assert n.rfc("EPS 180516 UF3") == "EPS180516UF3"
    assert n.curp("pepj910101hcsrrn09") == "PEPJ910101HCSRRN09"


@pytest.mark.parametrize(
    ("func", "value"),
    [(n.rfc, "PEPJ91010"), (n.rfc, "1234567890123"), (n.curp, "PEPJ910101")],
)
def test_bad_identifiers_name_their_field(func, value):
    with pytest.raises(ReportFieldError) as err:
        func(value, field="col21")
    assert err.value.code == "REPORT_FIELD_INVALID"
    assert err.value.field == "col21"
    assert value not in err.value.detail


# ---- Amounts and quantities -------------------------------------------------------


def test_amounts_are_rounded_half_up_to_two_places():
    assert n.amount(Decimal(75000)) == "75000.00"
    assert n.amount(Decimal("0.005")) == "0.01"
    assert n.amount(Decimal("12.344")) == "12.34"
    assert n.amount(Decimal("99999999999999.994")) == "99999999999999.99"


@pytest.mark.parametrize(
    "value",
    [Decimal(-1), Decimal("NaN"), Decimal("Infinity"), Decimal("1e14")],
)
def test_amounts_out_of_range_are_refused(value):
    with pytest.raises(ReportFieldError):
        _ = n.amount(value, field="col11")


def test_quantities_keep_between_two_and_ten_decimals():
    assert n.quantity(Decimal("1.5")) == "1.50"
    assert n.quantity(Decimal("0.3973460252")) == "0.3973460252"
    assert n.quantity(Decimal("0.39734602525")) == "0.3973460253"
    assert n.quantity(Decimal(10000)) == "10000.00"


@given(st.decimals(min_value=0, max_value=Decimal("1e13"), places=6))
def test_amount_text_matches_the_layout(value):
    text = n.amount(value)
    whole, decimals = text.split(".")
    assert whole.isdigit()
    assert len(whole) <= 14
    assert len(decimals) == 2
    assert abs(Decimal(text) - value) <= Decimal("0.005")


# ---- Dates ------------------------------------------------------------------------


def test_dates_render_as_the_layout_wants():
    assert n.yyyymm(date(2027, 3, 31)) == "202703"
    assert n.yyyymmdd(date(2004, 9, 24)) == "20040924"


def test_datetimes_are_rendered_in_mexico_city():
    assert n.mx_datetime(datetime(2026, 12, 1, 5, 30, tzinfo=UTC)) == "20261130233000"
    plus_two = timezone(timedelta(hours=2))
    assert n.mx_datetime(datetime(2026, 7, 1, 2, 0, tzinfo=plus_two)) == (
        "20260630180000"
    )


def test_naive_datetimes_are_refused():
    with pytest.raises(ReportFieldError, match="time zone"):
        naive = datetime.fromisoformat("2026-01-01T00:00")
        _ = n.mx_datetime(naive, field="fecha_hora_operacion")


# ---- CASFIM -----------------------------------------------------------------------


def test_casfim_keys_lose_the_hyphen_and_gain_a_leading_zero():
    assert n.casfim_key("40-002") == "040002"
    assert n.casfim_key("6-001") == "006001"
    assert n.casfim_key("013005") == "013005"


@pytest.mark.parametrize("value", ["40002", "4000-2", "ABC-123", "0400021"])
def test_bad_casfim_keys_are_refused(value):
    with pytest.raises(ReportFieldError):
        _ = n.casfim_key(value, field="col4")


# ---- Review findings --------------------------------------------------------------


def test_negative_zero_renders_without_a_sign():
    assert n.amount(Decimal("-0")) == "0.00"
    assert n.quantity(Decimal("-0.000")) == "0.00"


@pytest.mark.parametrize("value", [Decimal("1E+30"), Decimal(123456789012345678)])
def test_huge_amounts_are_field_errors_not_decimal_errors(value):
    with pytest.raises(ReportFieldError, match="14 integer digits"):
        _ = n.amount(value)
    with pytest.raises(ReportFieldError, match="14 integer digits"):
        _ = n.quantity(value)


def test_casfim_keys_take_ascii_digits_only_and_allow_the_eight_digit_form():
    assert n.casfim_key("08920001") == "08920001"
    with pytest.raises(ReportFieldError):
        _ = n.casfim_key("\uff14\uff10-\uff10\uff10\uff12")
    with pytest.raises(ReportFieldError):
        _ = n.casfim_key("\uff10\uff14\uff10\uff10\uff10\uff12")


def test_yyyymmdd_refuses_a_datetime():
    with pytest.raises(TypeError, match="not a datetime"):
        _ = n.yyyymmdd(datetime(2026, 1, 1, 3, tzinfo=UTC))


def test_letters_without_a_decomposition_are_transliterated():
    assert n.f41_text("Łukasz Øster Æble Đorđe Œuvre") == (
        "LUKASZ OSTER AEBLE DORDE OEUVRE"
    )
    assert n.aviso_text("Łódź") == "LODZ"


def test_identifiers_compose_a_decomposed_enye():
    assert n.compact_identifier("PEÑA800101AB1") == "PEÑA800101AB1"


def test_aviso_text_turns_control_characters_into_spaces():
    assert n.aviso_text("A\x07B\u200bC\x00") == "A B C"


def test_fit_drops_the_space_left_at_the_cut():
    assert n.fit("ABC DEF", 4) == ("ABC", True)


@given(st.decimals(allow_nan=False, allow_infinity=False))
def test_amount_raises_only_field_errors(value):
    try:
        text = n.amount(value)
    except ReportFieldError:
        return
    assert not text.startswith("-")


def test_aviso_descriptions_keep_only_the_schema_punctuation():
    assert n.aviso_description("; Cliente: Peña — monto $1,500.00 (MXN)!") == (
        "CLIENTE: PEÑA MONTO $1,500.00 MXN"
    )
    assert not n.aviso_description(" ;;; ")


@given(st.text())
def test_aviso_descriptions_match_the_xsd_pattern(value):
    cleaned = n.aviso_description(value)
    assert not cleaned or re.fullmatch(r"[A-ZÑ\d'$\-][A-ZÑ\d ,\.:/'$\-]*", cleaned)
