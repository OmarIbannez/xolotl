"""What is screened: a person or company with optional qualifiers (SP-03 §4.2).

``NameQuery`` is the library's own input type, so the library never imports the
party module; the engine builds one from a party or an ad hoc payload.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from xolotl.modules.screening.lists import EntityType, IdType, JsonValue
from xolotl.modules.screening.matching.normalize import NormalizedName, normalize_name


@dataclass(frozen=True, slots=True)
class QueryIdentifier:
    id_type: IdType
    normalized: str
    country: str | None = None


@dataclass(frozen=True, slots=True)
class NameQuery:
    """The first name is the legal name; then trade or other known names."""

    entity_type: EntityType
    names: tuple[str, ...]
    given_names: str | None = None
    first_surname: str | None = None
    second_surname: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    nationalities: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    identifiers: tuple[QueryIdentifier, ...] = ()

    def surname(self) -> str | None:
        """Join the two surnames.

        Returns:
            The surnames separated by a space, or ``None`` when neither is set.

        """
        parts = [p for p in (self.first_surname, self.second_surname) if p]
        return " ".join(parts) or None

    def fingerprint(self) -> bytes:
        """Hash the query's canonical JSON; stored on a run for replay (NFR-SC-007).

        Returns:
            The 32-byte SHA-256 digest.

        """
        data: dict[str, JsonValue] = {
            "entity_type": self.entity_type.value,
            "names": list(self.names),
            "given_names": self.given_names,
            "first_surname": self.first_surname,
            "second_surname": self.second_surname,
            "birth_date": None
            if self.birth_date is None
            else self.birth_date.isoformat(),
            "gender": self.gender,
            "nationalities": list(self.nationalities),
            "countries": list(self.countries),
            "identifiers": [
                {
                    "id_type": i.id_type.value,
                    "normalized": i.normalized,
                    "country": i.country,
                }
                for i in self.identifiers
            ],
        }
        canonical = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode()).digest()


def query_names(query: NameQuery) -> list[NormalizedName]:
    """Normalize every name of a query, using the structured fields for the first.

    Returns:
        The non-empty normalized names, legal name first.

    """
    names: list[NormalizedName] = []
    for position, text in enumerate(query.names):
        structured = position == 0 and query.entity_type is EntityType.PERSON
        name = normalize_name(
            text,
            entity_type=query.entity_type,
            given=query.given_names if structured else None,
            surname=query.surname() if structured else None,
        )
        if name.tokens:
            names.append(name)
    return names


def compatible(query_type: EntityType, entry_type: EntityType) -> bool:
    """Tell whether a query may match an entry of this type.

    A person matches persons; an organization matches organizations, vessels and
    aircraft; ``OTHER`` entries match both.

    Returns:
        False for a person against a non-person or the reverse.

    """
    if entry_type is EntityType.OTHER:
        return True
    return (query_type is EntityType.PERSON) == (entry_type is EntityType.PERSON)
