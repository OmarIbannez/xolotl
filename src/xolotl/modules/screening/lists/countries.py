"""Country names as the list sources spell them, mapped to ISO 3166-1 alpha-2.

The table is ``data/countries.csv``. An unknown spelling maps to ``None`` and is
logged: refusing a whole list over one new spelling would stop ingestion, which is
worse than a missing qualifier. Tests assert that every spelling in every fixture
maps, so the table grows with the sources.
"""

import csv
import logging
import re
import unicodedata
from functools import cache
from importlib import resources

logger = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Values sources put in a country field that mean "no country", and historic states
# with no current alpha-2 code.
_BLANK = frozenset({"na", "n a", "unknown", "none", "former soviet union", "ussr"})
# Withdrawn ISO codes sources still use (Serbia and Montenegro, Netherlands
# Antilles, USSR, Yugoslavia, Zaire, East Timor) and "00" for unknown.
_HISTORIC_CODES = frozenset({"00", "CS", "AN", "SU", "YU", "ZR", "TP"})


def _key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped.casefold()).strip()


@cache
def _codes() -> frozenset[str]:
    return frozenset(_table().values())


@cache
def _table() -> dict[str, str]:
    text = (
        resources
        .files("xolotl.modules.screening")
        .joinpath("data/countries.csv")
        .read_text(encoding="utf-8")
    )
    rows = csv.DictReader(
        line for line in text.splitlines() if not line.startswith("#")
    )
    table: dict[str, str] = {}
    for row in rows:
        code = row["code"]
        for name in row["names"].split("|"):
            table[_key(name)] = code
    return table


def country_code(name: str | None, *, quiet: bool = False) -> str | None:
    """Map a country name to its alpha-2 code.

    Codes are not accepted: ``NA`` (Namibia) is also how sources write "not
    available". ``quiet`` skips the warning for fields that hold a country or a
    city (OFAC's legacy remarks put either in parentheses after a document).

    Returns:
        The upper-case alpha-2 code, or ``None`` for an empty, blank-meaning or
        unknown value (the last one is logged with the spelling).

    """
    if name is None:
        return None
    key = _key(name)
    if not key or key in _BLANK:
        return None
    code = _table().get(key)
    if code is None and not quiet:
        logger.warning("unmapped country name %r", name)
    return code


def alpha2(code: str | None) -> str | None:
    """Check an ISO 3166-1 alpha-2 code a source gives directly.

    Returns:
        The upper-case code, or ``None`` for an empty value, ``00`` (unknown), a
        withdrawn code, or an unknown code (logged).

    """
    if code is None:
        return None
    value = code.strip().upper()
    if not value or value in _HISTORIC_CODES:
        return None
    if value not in _codes():
        logger.warning("unknown country code %r", code)
        return None
    return value
