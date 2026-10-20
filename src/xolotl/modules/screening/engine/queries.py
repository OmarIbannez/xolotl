"""The ``NameQuery`` a party or an ad hoc subject is screened with (spec §8.2).

A party is read through ``party.service.screening_profile``, which decrypts for
processing only (D6). An ad hoc subject comes from a request body; its identifiers
are validated exactly like a party's. Only RFC, CURP and passport numbers reach the
matcher: the lists publish no other identifier the engine can compare.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import IdentifierType, PartyStatus, PartyType
from xolotl.modules.party.errors import IdentifierInvalidError
from xolotl.modules.party.identifiers import IdentifierError, validate
from xolotl.modules.party.service import PartyScreeningProfile, screening_profile
from xolotl.modules.screening.lists import EntityType, IdType
from xolotl.modules.screening.matching import NameQuery, QueryIdentifier

SCREENED_ID_TYPES: Mapping[IdentifierType, IdType] = {
    IdentifierType.RFC: IdType.RFC,
    IdentifierType.CURP: IdType.CURP,
    IdentifierType.PASSPORT: IdType.PASSPORT,
}
_GENDERS = frozenset({"MALE", "FEMALE"})
_PII: dict[str, JsonValue] = {"pii": True}

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
SubjectName = Annotated[str, StringConstraints(min_length=1, max_length=300)]


def _dedup(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in values if v))


def _screened(
    identifiers: Iterable[tuple[IdentifierType, str, str | None]],
) -> tuple[QueryIdentifier, ...]:
    return tuple(
        QueryIdentifier(SCREENED_ID_TYPES[id_type], normalized=value, country=country)
        for id_type, value, country in identifiers
        if id_type in SCREENED_ID_TYPES
    )


def full_name(profile: PartyScreeningProfile) -> str:
    """Join a person's given names and surnames, the one name a person goes by.

    The name index and the query both use it, so they never disagree on a name.

    Returns:
        The full name, empty when the person has no name parts.

    """
    parts = (profile.given_names, profile.first_surname, profile.second_surname)
    return " ".join(p for p in parts if p)


def query_for_profile(profile: PartyScreeningProfile) -> NameQuery:
    """Build the query of a party's screening profile.

    A person is screened by the full name, birth date, gender, nationalities and
    countries; an organization by its legal name and trade name. Both carry their
    RFC, CURP and passport numbers. A purged party is screened by nothing: purging
    keeps an organization's trade name, but the query drops it with every other
    name and identifier.

    Returns:
        The query; a purged party's carries no name and no identifier.

    """
    entity_type = (
        EntityType.PERSON
        if profile.party_type is PartyType.PERSON
        else EntityType.ORGANIZATION
    )
    if profile.status is PartyStatus.PURGED:
        return NameQuery(entity_type=entity_type, names=())
    identifiers = _screened(profile.identifiers)
    if profile.party_type is PartyType.PERSON:
        full = full_name(profile)
        return NameQuery(
            entity_type=EntityType.PERSON,
            names=(full,) if full else (),
            given_names=profile.given_names,
            first_surname=profile.first_surname,
            second_surname=profile.second_surname,
            birth_date=profile.birth_date,
            gender=profile.gender if profile.gender in _GENDERS else None,
            nationalities=profile.nationalities,
            countries=_dedup((profile.country_code, profile.birth_country)),
            identifiers=identifiers,
        )
    names = tuple(n for n in (profile.legal_name, profile.trade_name) if n)
    return NameQuery(
        entity_type=EntityType.ORGANIZATION,
        names=names,
        countries=_dedup((profile.country_of_incorporation, profile.country_code)),
        identifiers=identifiers,
    )


def query_for_party(s: Session, party_id: UUID) -> NameQuery:
    """Build the query of one party of the session's tenant.

    Returns:
        The party's query.

    """
    return query_for_profile(screening_profile(s, party_id))


class AdHocIdentifier(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    id_type: IdentifierType
    value: str = Field(min_length=1, max_length=64, json_schema_extra=_PII)
    country: CountryCode | None = None


class AdHocSubjectInput(BaseModel):
    """A subject screened without being a party: the check's inline ``subject``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    type: Literal["PERSON", "ORGANIZATION"]
    names: list[SubjectName] = Field(min_length=1, max_length=3, json_schema_extra=_PII)
    given_names: str | None = Field(
        default=None, max_length=300, json_schema_extra=_PII
    )
    first_surname: str | None = Field(
        default=None, max_length=300, json_schema_extra=_PII
    )
    second_surname: str | None = Field(
        default=None, max_length=300, json_schema_extra=_PII
    )
    birth_date: date | None = Field(default=None, json_schema_extra=_PII)
    gender: Literal["MALE", "FEMALE"] | None = None
    nationalities: list[CountryCode] = Field(default_factory=list[str])
    countries: list[CountryCode] = Field(default_factory=list[str])
    identifiers: list[AdHocIdentifier] = Field(
        default_factory=list[AdHocIdentifier], max_length=10
    )


def query_for_ad_hoc(body: AdHocSubjectInput) -> NameQuery:
    """Build the query of an ad hoc subject, validating its identifiers.

    Returns:
        The query, with the identifiers in their canonical form.

    Raises:
        IdentifierInvalidError: When an identifier fails its validator; the detail
            is ``"<ID_TYPE>: <REASON>"`` and never the value.

    """
    canonical: list[tuple[IdentifierType, str, str | None]] = []
    for item in body.identifiers:
        result = validate(item.id_type, item.value, birth_date=body.birth_date)
        if isinstance(result, IdentifierError):
            msg = f"{result.id_type.value}: {result.reason.value}"
            raise IdentifierInvalidError(msg)
        canonical.append((item.id_type, result.value, item.country))
    return NameQuery(
        entity_type=EntityType(body.type),
        names=tuple(body.names),
        given_names=body.given_names,
        first_surname=body.first_surname,
        second_surname=body.second_surname,
        birth_date=body.birth_date,
        gender=body.gender,
        nationalities=tuple(body.nationalities),
        countries=tuple(body.countries),
        identifiers=_screened(canonical),
    )
