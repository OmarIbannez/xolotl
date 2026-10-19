"""``ListEntry`` canonical JSON, hash and round trip (SP-03 spec §4.1, D18)."""

import json
from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xolotl.modules.screening.lists import (
    EntityType,
    EntryAddress,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    ListFormatError,
    NameQuality,
    ParsedList,
    PartialDate,
)

text = st.text(max_size=12)
opt_text = st.none() | text
names = st.builds(
    EntryName,
    full=text,
    quality=st.sampled_from(NameQuality),
    given=opt_text,
    surname=opt_text,
    script=opt_text,
)
partial_dates = st.dates().flatmap(
    lambda d: st.sampled_from([
        PartialDate(d.year),
        PartialDate(d.year, d.month),
        PartialDate(d.year, d.month, d.day),
    ])
)
entries = st.builds(
    ListEntry,
    list_code=text,
    entry_key=text,
    entity_type=st.sampled_from(EntityType),
    primary_name=names,
    aliases=st.lists(names, max_size=3).map(tuple),
    birth_dates=st.lists(partial_dates, max_size=3).map(tuple),
    birth_places=st.lists(text, max_size=2).map(tuple),
    nationalities=st.lists(text, max_size=2).map(tuple),
    gender=opt_text,
    identifiers=st.lists(
        st.builds(
            EntryIdentifier,
            id_type=st.sampled_from(IdType),
            value=text,
            normalized=text,
            country=opt_text,
            label=opt_text,
        ),
        max_size=2,
    ).map(tuple),
    addresses=st.lists(
        st.builds(EntryAddress, country=opt_text, text=opt_text), max_size=2
    ).map(tuple),
    programs=st.lists(text, max_size=2).map(tuple),
    listed_on=st.none() | st.dates(),
    delisted_on=st.none() | st.dates(),
    status=opt_text,
    screenable=st.booleans(),
    source_ref=text,
)


BASE = ListEntry(
    list_code="UN_CONSOLIDATED",
    entry_key="1",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN PEREZ", NameQuality.PRIMARY),
)


def _entry(**changes: object) -> ListEntry:
    return replace(BASE, **changes)


@given(entries)
def test_canonical_json_round_trips(entry: ListEntry):
    data = json.loads(entry.canonical_json())
    assert ListEntry.from_json(data) == entry
    assert ListEntry.from_json(data).content_hash() == entry.content_hash()


def test_canonical_json_is_sorted_compact_and_complete():
    raw = _entry(birth_dates=(PartialDate(1970, 5),), listed_on=date(2020, 1, 2))
    text_ = raw.canonical_json().decode()
    data = json.loads(text_)
    assert text_ == json.dumps(data, sort_keys=True, separators=(",", ":"))
    assert set(data) == set(ListEntry.__dataclass_fields__)
    assert data["birth_dates"] == [{"day": None, "month": 5, "year": 1970}]
    assert data["listed_on"] == "2020-01-02"
    assert data["entity_type"] == "PERSON"


def test_any_field_change_changes_the_hash():
    entry = _entry()
    assert entry.content_hash() == _entry().content_hash()
    assert len(entry.content_hash()) == 32
    assert _entry(screenable=False).content_hash() != entry.content_hash()
    assert _entry(source_ref="x").content_hash() != entry.content_hash()


MISSING = object()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("aliases", MISSING, "aliases"),
        ("screenable", "yes", "boolean"),
        ("entity_type", "SHIP", "SHIP"),
        ("birth_dates", [{"year": None, "month": None, "day": None}], "year"),
        ("birth_dates", [{"year": True, "month": None, "day": None}], "integer"),
        ("programs", "X", "array"),
        ("primary_name", "X", "object"),
        ("source_ref", 1, "string"),
        ("listed_on", "2020-13-01", "month"),
    ],
)
def test_from_json_refuses_invalid_data(field, value, message):
    data = json.loads(_entry().canonical_json())
    if value is MISSING:
        del data[field]
    else:
        data[field] = value
    with pytest.raises(ListFormatError, match=message) as caught:
        _ = ListEntry.from_json(data)
    assert caught.value.code == "LIST_FORMAT_INVALID"
    assert caught.value.status == 422


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((2020, None, 1), "needs a month"),
        ((2020, 13), "out of range"),
        ((2020, 0), "out of range"),
        ((2021, 2, 29), "day is out of range"),
        ((0,), "year 0 is out of range"),
    ],
)
def test_partial_date_refuses_impossible_dates(args, message):
    with pytest.raises(ValueError, match=message):
        _ = PartialDate(*args)


def test_parsed_list_refuses_duplicate_keys():
    with pytest.raises(ListFormatError, match="duplicate entry key '1' at entry 1"):
        _ = ParsedList("UN_CONSOLIDATED", "un-xml/1", None, None, (_entry(), _entry()))
