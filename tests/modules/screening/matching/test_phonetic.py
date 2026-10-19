"""The Spanish- and English-aware phonetic key (plan Decision 8)."""

import pytest

from xolotl.modules.screening.matching import phonetic_key

SAME = [
    ("gonzalez", "gonsales"),
    ("chavez", "chaves"),
    ("ximenez", "jimenez"),
    ("xavier", "javier"),
    ("jorge", "george"),
    ("mohamed", "muhammad"),
    ("mohammed", "mohamad"),
    ("vazquez", "basquez"),
    ("castillo", "castiyo"),
    ("rodriguez", "rodrigues"),
    ("hernandez", "ernandez"),
    ("philip", "filip"),
    ("cecilia", "sesilia"),
    ("quintero", "kintero"),
    ("ibrahim", "ebrahim"),
    ("khamatovich", "jamatovich"),
    ("guerrero", "guerero"),
    ("zhukov", "jukov"),
    ("shakira", "shakyra"),
    ("thomas", "tomas"),
    ("youssef", "yusef"),
]
DIFFERENT = [
    ("gonzalez", "gomez"),
    ("perez", "lopez"),
    ("perez", "paredes"),
    ("chavez", "jaimes"),
    ("ramirez", "ramos"),
]


@pytest.mark.parametrize(("first", "second"), SAME)
def test_equivalent_spellings_share_a_key(first, second):
    assert phonetic_key(first) == phonetic_key(second)


# Garcia and Garza share a key on purpose (same sounds in Latin American Spanish);
# the similarity score tells them apart.
@pytest.mark.parametrize(("first", "second"), DIFFERENT)
def test_different_names_keep_different_keys(first, second):
    assert phonetic_key(first) != phonetic_key(second)


@pytest.mark.parametrize(
    ("token", "key"),
    [
        ("gonzalez", "gnsls"),
        ("hernandez", "arnds"),
        ("chavez", "cbs"),
        ("ximenez", "jmns"),
        ("mexico", "mksk"),
        ("guillermo", "gyrm"),
        ("ybarra", "abr"),
        ("h", "h"),
        ("1234", "1234"),
        ("ab12", "ab12"),
        ("صدام", "صدام"),
        ("", ""),
    ],
)
def test_documented_keys(token, key):
    assert phonetic_key(token) == key
