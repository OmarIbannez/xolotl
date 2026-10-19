"""Token weights from the list corpus and Mexican surname frequencies (SP-03 §4.2).

A token's weight says how much a match on it is worth: 1.0 for a token no more
frequent than ``RARE_SHARE``, falling on a log scale to ``MIN_WEIGHT`` for the most
common. Frequency is the larger of the token's share of list entries and its share
of the Mexican population as a surname (``data/surnames_mx.csv``), so Hernández is
cheap even though few sanctioned people carry it. Unseen tokens weigh 1.0. List
shares are taken over at least ``MIN_CORPUS`` entries: a small list set (one private
list, a test fixture) says little about how common a token is, and the shared lists
alone hold tens of thousands of entries.
"""

import csv
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from functools import cache
from importlib import resources
from types import MappingProxyType

from xolotl.modules.screening.lists import ListEntry
from xolotl.modules.screening.matching.names import entry_names
from xolotl.modules.screening.matching.normalize import TokenRole

RARE_SHARE = 1e-3
MIN_WEIGHT = 0.1
MIN_CORPUS = 10_000
WEIGHTED_ROLES = frozenset({TokenRole.GIVEN, TokenRole.SURNAME, TokenRole.UNKNOWN})
_POPULATION_PREFIX = "# population:"


@cache
def surname_shares() -> Mapping[str, float]:
    """Load the share of the Mexican population carrying each common surname.

    Returns:
        Folded surname to share of the population.

    Raises:
        TypeError: the file has no population line.

    """
    text = (
        resources
        .files("xolotl.modules.screening")
        .joinpath("data/surnames_mx.csv")
        .read_text(encoding="utf-8")
    )
    lines = text.splitlines()
    population = next(
        (
            int(line.removeprefix(_POPULATION_PREFIX))
            for line in lines
            if line.startswith(_POPULATION_PREFIX)
        ),
        None,
    )
    if population is None:
        msg = "surnames_mx.csv has no population line"
        raise TypeError(msg)
    rows = csv.DictReader(line for line in lines if not line.startswith("#"))
    return MappingProxyType({
        row["surname"]: int(row["count"]) / population for row in rows
    })


def _weight(share: float) -> float:
    if share <= RARE_SHARE:
        return 1.0
    return max(MIN_WEIGHT, math.log(share) / math.log(RARE_SHARE))


class IdfTable:
    """Immutable token weights; build once per list-set and share it."""

    def __init__(self, weights: Mapping[str, float]) -> None:
        self._weights: Mapping[str, float] = MappingProxyType(dict(weights))

    @classmethod
    def build(cls, entries: Iterable[ListEntry]) -> "IdfTable":
        """Count in how many entries each token appears and weigh it.

        Returns:
            The table; tokens weighing 1.0 are not stored.

        """
        counts: Counter[str] = Counter()
        total = 0
        for entry in entries:
            total += 1
            counts.update({
                token.folded
                for name, _quality in entry_names(entry)
                for token in name.tokens
                if token.role in WEIGHTED_ROLES
            })
        shares = dict(surname_shares())
        for token, count in counts.items():
            shares[token] = max(shares.get(token, 0.0), count / max(total, MIN_CORPUS))
        return cls({t: w for t, s in shares.items() if (w := _weight(s)) < 1.0})

    def weight(self, folded: str) -> float:
        """Weigh one folded token.

        Returns:
            A weight in ``[MIN_WEIGHT, 1.0]``.

        """
        return self._weights.get(folded, 1.0)
