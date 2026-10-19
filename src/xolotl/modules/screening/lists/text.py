"""Small text helpers shared by the parsers."""

import re
import unicodedata

_NOT_ALNUM = re.compile(r"[^0-9A-Z]")

# Unicode character-name prefixes to ISO 15924 script codes.
_SCRIPTS = (
    ("LATIN", "Latn"),
    ("ARABIC", "Arab"),
    ("CYRILLIC", "Cyrl"),
    ("GREEK", "Grek"),
    ("HEBREW", "Hebr"),
    ("HANGUL", "Hang"),
    ("CJK", "Hani"),
    ("HIRAGANA", "Jpan"),
    ("KATAKANA", "Jpan"),
    ("THAI", "Thai"),
    ("DEVANAGARI", "Deva"),
    ("GEORGIAN", "Geor"),
    ("ARMENIAN", "Armn"),
)


def script_of(text: str) -> str | None:
    """Guess the ISO 15924 script from the first letter.

    Returns:
        The script code, or ``None`` when no letter has a known script.

    """
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        for prefix, code in _SCRIPTS:
            if name.startswith(prefix):
                return code
        return None
    return None


def normalize_identifier(value: str) -> str:
    """Reduce an identifier to the form the scorer compares.

    Returns:
        Upper-case ASCII letters and digits only.

    """
    folded = unicodedata.normalize("NFKD", value.upper())
    return _NOT_ALNUM.sub("", folded)
