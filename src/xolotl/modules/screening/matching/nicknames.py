"""Given-name equivalence from ``data/nicknames_es.yaml`` (José/Pepe, Lupe, Paco…)."""

from functools import cache

from xolotl.modules.screening.matching.tables import nickname_groups


@cache
def _groups_by_name() -> dict[str, frozenset[int]]:
    index: dict[str, set[int]] = {}
    for number, group in enumerate(nickname_groups()):
        for name in group:
            index.setdefault(name, set()).add(number)
    return {name: frozenset(groups) for name, groups in index.items()}


def are_nicknames(first: str, second: str) -> bool:
    """Tell whether two different folded given names share a nickname group.

    Returns:
        True when both names sit in one group; False for equal or unknown names.

    """
    if first == second:
        return False
    table = _groups_by_name()
    return not table.get(first, frozenset()).isdisjoint(table.get(second, frozenset()))


def nickname_variants(name: str) -> frozenset[str]:
    """List the other names that share a nickname group with ``name``.

    Returns:
        The folded variants, without ``name`` itself; empty for an unknown name.

    """
    table = _groups_by_name()
    groups = nickname_groups()
    variants = {
        other for number in table.get(name, frozenset()) for other in groups[number]
    }
    variants.discard(name)
    return frozenset(variants)
