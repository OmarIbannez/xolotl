"""``contains_float``: the one rule about floats in values bound for JSONB."""

import pytest

from xolotl.platform.sqltypes import contains_float


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        2.0,
        -0.0,
        1e16,
        {"a": 0.5},
        {"a": {"b": {"c": 3.25}}},
        [1, 2.5],
        [[[1.5]]],
        {"a": [{"b": [{"c": 0.1}]}]},
    ],
)
def test_a_float_is_found_at_any_depth(value: object):
    assert contains_float(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        10**30,
        "1.5",
        "",
        {},
        [],
        {"s": "1e16"},
        {"a": 1, "b": "x", "c": None, "d": True, "e": [1, "2", {"f": []}]},
    ],
)
def test_ints_strings_booleans_nulls_and_empty_containers_are_not_floats(
    value: object,
):
    assert not contains_float(value)


def test_a_very_deep_value_does_not_exhaust_the_stack():
    value: object = 1.5
    for _ in range(5_000):
        value = [value]
    assert contains_float(value)
