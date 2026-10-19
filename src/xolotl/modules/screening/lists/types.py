"""The one record shape every list parser produces (SP-03 spec §4.1, FR-SC-002).

``ListEntry.canonical_json`` is the stored form of an entry and ``content_hash`` its
identity for deltas and the whitelist (D18), so both are deterministic: keys sorted,
no whitespace, every field present, tuples as arrays, enums as their values, dates
as ``YYYY-MM-DD`` and partial dates as ``{"day", "month", "year"}`` with nulls kept.
``from_json`` is the exact inverse.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from xolotl.modules.screening.lists.errors import ListFormatError

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)


class ListCode(StrEnum):
    """Shared sources. Private codes (``MX_LPB``, ``INTERNAL_<SLUG>``) are strings."""

    UN_CONSOLIDATED = "UN_CONSOLIDATED"
    OFAC_SDN = "OFAC_SDN"
    EU_FSF = "EU_FSF"
    UK_FCDO = "UK_FCDO"
    MX_SAT_69B = "MX_SAT_69B"


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    VESSEL = "VESSEL"
    AIRCRAFT = "AIRCRAFT"
    OTHER = "OTHER"


class NameQuality(StrEnum):
    """``WEAK`` is a low-quality a.k.a. that sources publish but advise against."""

    PRIMARY = "PRIMARY"
    STRONG = "STRONG"
    WEAK = "WEAK"


class IdType(StrEnum):
    """Normalized identifier kinds the scorer compares."""

    RFC = "RFC"
    CURP = "CURP"
    PASSPORT = "PASSPORT"
    NATIONAL_ID = "NATIONAL_ID"
    TAX_ID = "TAX_ID"
    REGISTRATION = "REGISTRATION"
    IMO = "IMO"
    DIGITAL_CURRENCY_ADDRESS = "DIGITAL_CURRENCY_ADDRESS"
    OTHER = "OTHER"


_MAX_MONTH = 12


@dataclass(frozen=True, slots=True)
class PartialDate:
    """A date known to the precision that is set: year, year and month, or full."""

    year: int
    month: int | None = None
    day: int | None = None

    def __post_init__(self) -> None:
        if self.month is None and self.day is not None:
            msg = "a partial date with a day needs a month"
            raise ValueError(msg)
        if self.month is not None and not 1 <= self.month <= _MAX_MONTH:
            msg = f"month {self.month} is out of range"
            raise ValueError(msg)
        # date() rejects impossible days and years outside 1..9999.
        _ = date(self.year, self.month or 1, self.day or 1)


@dataclass(frozen=True, slots=True)
class EntryName:
    full: str
    quality: NameQuality
    given: str | None = None
    surname: str | None = None
    script: str | None = None


@dataclass(frozen=True, slots=True)
class EntryIdentifier:
    id_type: IdType
    value: str
    normalized: str
    country: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class EntryAddress:
    country: str | None
    text: str | None


@dataclass(frozen=True, slots=True)
class ListEntry:
    list_code: str
    entry_key: str
    entity_type: EntityType
    primary_name: EntryName
    aliases: tuple[EntryName, ...] = ()
    birth_dates: tuple[PartialDate, ...] = ()
    birth_places: tuple[str, ...] = ()
    nationalities: tuple[str, ...] = ()
    gender: str | None = None
    identifiers: tuple[EntryIdentifier, ...] = ()
    addresses: tuple[EntryAddress, ...] = ()
    programs: tuple[str, ...] = ()
    listed_on: date | None = None
    delisted_on: date | None = None
    status: str | None = None
    screenable: bool = True
    source_ref: str = ""

    def canonical_json(self) -> bytes:
        """Serialize the entry deterministically.

        Returns:
            UTF-8 JSON with sorted keys and no whitespace.

        """
        return json.dumps(
            _entry_to_json(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()

    def content_hash(self) -> bytes:
        """Hash the canonical JSON; the delta and whitelist key (D18).

        Returns:
            The 32-byte SHA-256 digest.

        """
        return hashlib.sha256(self.canonical_json()).digest()

    @classmethod
    def from_json(cls, data: Mapping[str, JsonValue]) -> "ListEntry":
        """Rebuild an entry from its canonical JSON object.

        Returns:
            The entry, equal to the one that produced ``data``.

        Raises:
            ListFormatError: a field is missing or has the wrong type.

        """
        try:
            return _entry_from_json(data)
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"stored list entry is invalid: {exc}"
            raise ListFormatError(msg) from exc


@dataclass(frozen=True, slots=True)
class ParsedList:
    """One parsed file. ``entry_key`` is unique within it."""

    list_code: str
    parser_version: str
    published_at: datetime | None
    source_version: str | None
    entries: tuple[ListEntry, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for position, entry in enumerate(self.entries):
            if entry.entry_key in seen:
                msg = (
                    f"{self.list_code} ({self.parser_version}): duplicate entry key"
                    f" {entry.entry_key!r} at entry {position}"
                )
                raise ListFormatError(msg)
            seen.add(entry.entry_key)


# ---- to JSON -------------------------------------------------------------------------


def _date_json(value: date | None) -> JsonValue:
    return None if value is None else value.isoformat()


def _name_json(name: EntryName) -> JsonValue:
    return {
        "full": name.full,
        "quality": name.quality.value,
        "given": name.given,
        "surname": name.surname,
        "script": name.script,
    }


def _entry_to_json(entry: ListEntry) -> dict[str, JsonValue]:
    return {
        "list_code": entry.list_code,
        "entry_key": entry.entry_key,
        "entity_type": entry.entity_type.value,
        "primary_name": _name_json(entry.primary_name),
        "aliases": [_name_json(a) for a in entry.aliases],
        "birth_dates": [
            {"year": d.year, "month": d.month, "day": d.day} for d in entry.birth_dates
        ],
        "birth_places": list(entry.birth_places),
        "nationalities": list(entry.nationalities),
        "gender": entry.gender,
        "identifiers": [
            {
                "id_type": i.id_type.value,
                "value": i.value,
                "normalized": i.normalized,
                "country": i.country,
                "label": i.label,
            }
            for i in entry.identifiers
        ],
        "addresses": [{"country": a.country, "text": a.text} for a in entry.addresses],
        "programs": list(entry.programs),
        "listed_on": _date_json(entry.listed_on),
        "delisted_on": _date_json(entry.delisted_on),
        "status": entry.status,
        "screenable": entry.screenable,
        "source_ref": entry.source_ref,
    }


# ---- from JSON -----------------------------------------------------------------------


def _obj(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        msg = "expected an object"
        raise TypeError(msg)
    return value


def _arr(value: JsonValue) -> Sequence[JsonValue]:
    if not isinstance(value, list):
        msg = "expected an array"
        raise TypeError(msg)
    return value


def _str(value: JsonValue) -> str:
    if not isinstance(value, str):
        msg = "expected a string"
        raise TypeError(msg)
    return value


def _opt_str(value: JsonValue) -> str | None:
    return None if value is None else _str(value)


def _opt_int(value: JsonValue) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = "expected an integer"
        raise TypeError(msg)
    return value


def _opt_date(value: JsonValue) -> date | None:
    return None if value is None else date.fromisoformat(_str(value))


def _name_from(value: JsonValue) -> EntryName:
    data = _obj(value)
    return EntryName(
        full=_str(data["full"]),
        quality=NameQuality(_str(data["quality"])),
        given=_opt_str(data["given"]),
        surname=_opt_str(data["surname"]),
        script=_opt_str(data["script"]),
    )


def _partial_date_from(value: JsonValue) -> PartialDate:
    data = _obj(value)
    year = _opt_int(data["year"])
    if year is None:
        msg = "a partial date needs a year"
        raise TypeError(msg)
    return PartialDate(year, _opt_int(data["month"]), _opt_int(data["day"]))


def _identifier_from(value: JsonValue) -> EntryIdentifier:
    data = _obj(value)
    return EntryIdentifier(
        id_type=IdType(_str(data["id_type"])),
        value=_str(data["value"]),
        normalized=_str(data["normalized"]),
        country=_opt_str(data["country"]),
        label=_opt_str(data["label"]),
    )


def _address_from(value: JsonValue) -> EntryAddress:
    data = _obj(value)
    return EntryAddress(country=_opt_str(data["country"]), text=_opt_str(data["text"]))


def _entry_from_json(data: Mapping[str, JsonValue]) -> ListEntry:
    screenable = data["screenable"]
    if not isinstance(screenable, bool):
        msg = "screenable must be a boolean"
        raise TypeError(msg)
    return ListEntry(
        list_code=_str(data["list_code"]),
        entry_key=_str(data["entry_key"]),
        entity_type=EntityType(_str(data["entity_type"])),
        primary_name=_name_from(data["primary_name"]),
        aliases=tuple(_name_from(a) for a in _arr(data["aliases"])),
        birth_dates=tuple(_partial_date_from(d) for d in _arr(data["birth_dates"])),
        birth_places=tuple(_str(p) for p in _arr(data["birth_places"])),
        nationalities=tuple(_str(n) for n in _arr(data["nationalities"])),
        gender=_opt_str(data["gender"]),
        identifiers=tuple(_identifier_from(i) for i in _arr(data["identifiers"])),
        addresses=tuple(_address_from(a) for a in _arr(data["addresses"])),
        programs=tuple(_str(p) for p in _arr(data["programs"])),
        listed_on=_opt_date(data["listed_on"]),
        delisted_on=_opt_date(data["delisted_on"]),
        status=_opt_str(data["status"]),
        screenable=screenable,
        source_ref=_str(data["source_ref"]),
    )
