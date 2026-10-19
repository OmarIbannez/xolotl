"""The version delta (SP-03 spec §4.1, FR-SC-002)."""

import pytest

from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    ListFormatError,
    NameQuality,
    diff,
)


def _entry(key: str, name: str = "A", *, screenable: bool = True) -> ListEntry:
    return ListEntry(
        list_code="MX_SAT_69B",
        entry_key=key,
        entity_type=EntityType.ORGANIZATION,
        primary_name=EntryName(name, NameQuality.PRIMARY),
        screenable=screenable,
    )


def test_added_changed_removed_and_unchanged_keys_are_sorted():
    previous = {k: _entry(k).content_hash() for k in ("b", "a", "c", "d")}
    current = [
        _entry("z"),
        _entry("y"),
        _entry("a"),
        _entry("d", "B"),
        _entry("c", "B"),
    ]
    delta = diff(previous, current)
    assert delta.added == ("y", "z")
    assert delta.changed == ("c", "d")
    assert delta.removed == ("b",)
    assert delta.unchanged == 1


def test_turning_non_screenable_is_a_change():
    previous = {"a": _entry("a").content_hash()}
    delta = diff(previous, [_entry("a", screenable=False)])
    assert delta.changed == ("a",)


def test_first_version_is_all_added():
    delta = diff({}, [_entry("a")])
    assert (delta.added, delta.changed, delta.removed, delta.unchanged) == (
        ("a",),
        (),
        (),
        0,
    )


def test_duplicate_keys_in_the_new_version_are_refused():
    with pytest.raises(ListFormatError, match="duplicate entry key 'a'"):
        _ = diff({}, [_entry("a"), _entry("a", "B")])
