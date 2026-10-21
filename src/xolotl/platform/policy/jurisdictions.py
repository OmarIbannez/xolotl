"""The named jurisdiction lists rules refer to (spec §9.1, plan 4b Deviation 15).

``policy-packs/_jurisdictions.yaml`` holds named sets of ISO 3166-1 alpha-2 country
codes (``FATF_BLACK``, ``FATF_GREY``, ``TAX_HAVEN``) and of ISO 3166-2 region codes
(``BORDER_ZONE_MX``). The file keeps the FATF statements' own key names
(``call_for_action``, ``increased_monitoring``); the loader maps them to the set
names a rule writes. The SHA-256 of the file's bytes is recorded in every hit, so a
changed file is visible and yields new aggregate ids.

Pure: reads one file, touches no database.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast

import yaml

from xolotl.platform.errors import ValidationFailedError

JURISDICTIONS_FILE = Path("policy-packs/_jurisdictions.yaml")
COUNTRY_SETS = ("FATF_BLACK", "FATF_GREY", "TAX_HAVEN")
REGION_SETS = ("BORDER_ZONE_MX",)

# The file key each country set is read from.
_COUNTRY_KEYS = {
    "FATF_BLACK": "call_for_action",
    "FATF_GREY": "increased_monitoring",
    "TAX_HAVEN": "tax_haven",
}
# The spelling of a country (ISO 3166-1 alpha-2) and of a region (ISO 3166-2) code;
# rules check their literal codes against these too.
COUNTRY_CODE = re.compile(r"[A-Z]{2}")
REGION_CODE = re.compile(r"[A-Z]{2}-[A-Z0-9]{1,3}")
_CONFIDENCE = frozenset({"VERIFIED", "UNVERIFIED"})
_KEYS = frozenset({
    "as_of",
    "source",
    "confidence",
    *_COUNTRY_KEYS.values(),
    "regions_confidence",
    "regions",
})


@dataclass(frozen=True, slots=True)
class JurisdictionLists:
    """The named sets of one jurisdictions file, with the hash of its bytes."""

    as_of: date
    countries: Mapping[str, frozenset[str]]
    regions: Mapping[str, frozenset[str]]
    content_hash: bytes

    def resolve_countries(self, names: Iterable[str]) -> frozenset[str]:
        """Union the country sets ``names`` name (``"$FATF_GREY"`` or ``"FATF_GREY"``).

        A name that is not a country set, or names an empty one, raises
        ``ValueError``.

        Returns:
            The country codes of every named set.

        """
        return _resolve(self.countries, names, "country")

    def resolve_regions(self, names: Iterable[str]) -> frozenset[str]:
        """Union the region sets ``names`` name (``"$BORDER_ZONE_MX"`` or bare).

        A name that is not a region set, or names an empty one, raises
        ``ValueError``.

        Returns:
            The region codes of every named set.

        """
        return _resolve(self.regions, names, "region")


def _resolve(
    sets: Mapping[str, frozenset[str]], names: Iterable[str], what: str
) -> frozenset[str]:
    codes: set[str] = set()
    for name in names:
        bare = name.removeprefix("$")
        if bare not in sets:
            msg = f"unknown {what} list {name!r}"
            raise ValueError(msg)
        if not sets[bare]:
            # An empty set would read as "no condition" in a filter, so a rule
            # naming it would fire on every transaction (plan 4b Open question 1).
            msg = f"{bare} has no source yet (plan 4b Open question 1)"
            raise ValueError(msg)
        codes |= sets[bare]
    return frozenset(codes)


def _refuse(path: Path, problem: str) -> ValidationFailedError:
    return ValidationFailedError(f"{path.name}: {problem}")


def _codes(
    path: Path, key: str, raw: object, pattern: re.Pattern[str]
) -> frozenset[str]:
    if not isinstance(raw, list):
        raise _refuse(path, f"{key} must be a list of codes")
    codes: set[str] = set()
    for item in cast("list[object]", raw):
        if not isinstance(item, str) or pattern.fullmatch(item) is None:
            raise _refuse(path, f"{key}: {item!r} is not a valid code")
        if item in codes:
            raise _refuse(path, f"{key}: {item} is listed twice")
        codes.add(item)
    return frozenset(codes)


def _regions(path: Path, raw: object) -> dict[str, frozenset[str]]:
    if not isinstance(raw, dict):
        raise _refuse(path, "regions must be a mapping of named sets")
    named = cast("dict[object, object]", raw)
    unknown = sorted(str(name) for name in named if name not in REGION_SETS)
    if unknown:
        raise _refuse(path, f"regions: unknown sets {', '.join(unknown)}")
    regions: dict[str, frozenset[str]] = {}
    for name in REGION_SETS:
        if name not in named:
            raise _refuse(path, f"regions: missing {name}")
        regions[name] = _codes(path, f"regions.{name}", named[name], REGION_CODE)
    return regions


def load_jurisdictions(path: Path = JURISDICTIONS_FILE) -> JurisdictionLists:
    """Read and check the jurisdictions file.

    Returns:
        The named country and region sets, ``as_of`` and the file's SHA-256.

    Raises:
        ValidationFailedError: When the file is not a YAML mapping, a key is
            missing or unknown, ``as_of`` is not a date, a confidence is neither
            ``VERIFIED`` nor ``UNVERIFIED``, a country is not ISO 3166-1 alpha-2, a
            region does not read ``CC-XXX`` (ISO 3166-2) or a set lists a code
            twice.

    """
    content = path.read_bytes()
    try:
        raw = cast("object", yaml.safe_load(content))
    except yaml.YAMLError as exc:
        msg = f"{path.name}: not YAML"
        raise ValidationFailedError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path.name}: not a mapping"
        raise ValidationFailedError(msg)
    data = cast("dict[str, object]", raw)
    missing = sorted(_KEYS.difference(data))
    if missing:
        msg = f"{path.name}: missing {', '.join(missing)}"
        raise ValidationFailedError(msg)
    unknown = sorted(str(key) for key in data if key not in _KEYS)
    if unknown:
        msg = f"{path.name}: unknown keys {', '.join(unknown)}"
        raise ValidationFailedError(msg)
    as_of = data["as_of"]
    # A datetime is a date too; the statements are dated by day.
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        msg = f"{path.name}: as_of must be a date"
        raise ValidationFailedError(msg)
    for key in ("confidence", "regions_confidence"):
        if data[key] not in _CONFIDENCE:
            msg = f"{path.name}: {key} must be VERIFIED or UNVERIFIED"
            raise ValidationFailedError(msg)
    countries = {
        name: _codes(path, key, data[key], COUNTRY_CODE)
        for name, key in _COUNTRY_KEYS.items()
    }
    return JurisdictionLists(
        as_of=as_of,
        countries=countries,
        regions=_regions(path, data["regions"]),
        content_hash=hashlib.sha256(content).digest(),
    )
