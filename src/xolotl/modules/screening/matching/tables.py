"""The data tables under ``screening/data``, each loaded once and validated.

A malformed table is a packaging bug, so it raises ``TypeError`` on first use and
the test suite catches it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import cast

import yaml


@dataclass(frozen=True, slots=True)
class WordTables:
    particles: frozenset[str]
    honorifics: frozenset[str]
    generic: frozenset[str]


def _load(name: str) -> object:
    path = resources.files("xolotl.modules.screening").joinpath(f"data/{name}")
    return cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        msg = f"{where} must be a mapping"
        raise TypeError(msg)
    return cast("Mapping[str, object]", value)


def _words(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        msg = f"{where} must be a list of words"
        raise TypeError(msg)
    words = cast("list[object]", value)
    if not all(isinstance(w, str) and w for w in words):
        msg = f"{where} holds a value that is not a word"
        raise TypeError(msg)
    return tuple(cast("list[str]", words))


@cache
def word_tables() -> WordTables:
    """Particles, honorifics and generic organization words.

    Returns:
        The three word sets from ``particles.yaml``.

    """
    data = _mapping(_load("particles.yaml"), "particles.yaml")
    return WordTables(
        particles=frozenset(_words(data["particles"], "particles")),
        honorifics=frozenset(_words(data["honorifics"], "honorifics")),
        generic=frozenset(_words(data["generic"], "generic")),
    )


@cache
def legal_forms() -> tuple[tuple[tuple[str, ...], str], ...]:
    """Legal-form token sequences with their canonical code.

    Returns:
        ``(tokens, code)`` pairs, longest sequence first.

    """
    data = _mapping(_load("legal_forms.yaml"), "legal_forms.yaml")
    pairs = [
        (tuple(variant.split()), code)
        for code, variants in data.items()
        for variant in _words(variants, f"legal form {code}")
    ]
    return tuple(sorted(pairs, key=lambda pair: -len(pair[0])))


@cache
def nickname_groups() -> tuple[frozenset[str], ...]:
    """Load the groups of given names that name the same person.

    Returns:
        One set per group in ``nicknames_es.yaml``.

    Raises:
        TypeError: the file is not a mapping with a ``groups`` list.

    """
    data = _mapping(_load("nicknames_es.yaml"), "nicknames_es.yaml")
    groups = data["groups"]
    if not isinstance(groups, list):
        msg = "nicknames_es.yaml groups must be a list"
        raise TypeError(msg)
    return tuple(
        frozenset(_words(group, "nickname group"))
        for group in cast("list[object]", groups)
    )
