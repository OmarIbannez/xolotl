"""Country names to alpha-2 codes."""

import logging

import pytest

from xolotl.modules.screening.lists.countries import alpha2, country_code


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Iran (Islamic Republic of)", "IR"),
        ("Democratic People's Republic of Korea", "KP"),
        ("Republic of Korea", "KR"),
        ("Türkiye", "TR"),
        ("  united   states of america ", "US"),
        ("México", "MX"),
        ("Central African Republic", "CF"),
        ("Bosnia & Herzegovina", "BA"),
    ],
)
def test_source_spellings_map(name, code):
    assert country_code(name) == code


@pytest.mark.parametrize("name", [None, "", "  ", "na", "N/A", "former Soviet Union"])
def test_blank_and_historic_values_map_to_none_silently(name, caplog):
    with caplog.at_level(logging.WARNING):
        assert country_code(name) is None
    assert not caplog.records


def test_unknown_spellings_are_logged_not_refused(caplog):
    with caplog.at_level(logging.WARNING):
        assert country_code("Atlantis") is None
    assert "Atlantis" in caplog.text


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("mx", "MX"),
        (" XK ", "XK"),
        (None, None),
        ("", None),
        ("00", None),
        ("CS", None),
    ],
)
def test_alpha2_codes(code, expected, caplog):
    with caplog.at_level(logging.WARNING):
        assert alpha2(code) == expected
    assert not caplog.records


def test_unknown_alpha2_codes_are_logged(caplog):
    with caplog.at_level(logging.WARNING):
        assert alpha2("QQ") is None
    assert "QQ" in caplog.text


def test_quiet_lookups_do_not_log(caplog):
    with caplog.at_level(logging.WARNING):
        assert country_code("Stuttgart", quiet=True) is None
    assert not caplog.records
