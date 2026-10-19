"""Parser text helpers."""

import pytest

from xolotl.modules.screening.lists.text import normalize_identifier, script_of


@pytest.mark.parametrize(
    ("value", "script"),
    [
        ("Juan", "Latn"),
        ("  12 Иван", "Cyrl"),
        ("صدام", "Arab"),
        ("金正恩", "Hani"),
        ("김정은", "Hang"),
        ("ᚠᚢᚦ", None),
        ("123 -", None),
    ],
)
def test_script_of(value, script):
    assert script_of(value) == script


def test_normalize_identifier_keeps_upper_ascii_alphanumerics():
    assert normalize_identifier(" ab-12.3/ñ ") == "AB123N"
