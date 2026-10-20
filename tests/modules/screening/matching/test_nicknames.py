"""Nickname groups (``data/nicknames_es.yaml``)."""

import pytest

from xolotl.modules.screening.matching import are_nicknames
from xolotl.modules.screening.matching.tables import nickname_groups


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("jose", "pepe"),
        ("francisco", "paco"),
        ("pancho", "paco"),
        ("guadalupe", "lupe"),
        ("roberto", "beto"),
        ("alberto", "beto"),
        ("guillermo", "william"),
    ],
)
def test_names_in_one_group_are_nicknames_both_ways(first, second):
    assert are_nicknames(first, second)
    assert are_nicknames(second, first)


@pytest.mark.parametrize(
    ("first", "second"),
    [("roberto", "alberto"), ("jose", "jose"), ("jose", "juan"), ("xyz", "pepe")],
)
def test_other_pairs_are_not(first, second):
    assert not are_nicknames(first, second)


def test_the_table_holds_folded_single_words():
    for group in nickname_groups():
        assert len(group) >= 2
        for name in group:
            assert name.isascii()
            assert name.isalpha()
            assert name == name.lower()
