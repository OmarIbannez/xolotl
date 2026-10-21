"""The ``NameQuery`` a counterparty is screened with (SP-04 spec §11.2 and §11.4).

The row is decrypted for processing only (D6). A named ``PERSON`` or
``ORGANIZATION`` is screened once under its kind. A counterparty of ``UNKNOWN`` kind,
or any counterparty without a name, is screened twice, as a ``PERSON`` and as an
``ORGANIZATION`` (plan 4c Deviation 2): an address or an identifier says nothing of
who sends from it, and both runs raise findings on the one subject, so the stricter
status wins.

A wallet becomes a ``DIGITAL_CURRENCY_ADDRESS`` identifier normalized as the OFAC
parser normalizes one (whitespace removed, case kept), so it matches a listed address
exactly and whole, never by prefix or fuzzily. A wallet-only counterparty is a query
with no names: the library on ``main`` already accepts ``names=()``, returns exact
identifier candidates first and floors an identifier match at ``ID_MATCH_FLOOR``, so
no library change is needed (plan 4c Deviation 1). The ad hoc identifier reaches the
matcher only when it is an RFC, a CURP or a passport and passes the party module's
validator; the lists publish no other identifier the engine compares.
"""

from sqlalchemy.orm import Session

from xolotl.modules.party.identifiers import IdentifierError, validate
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schema import CounterpartyKind
from xolotl.modules.screening.engine.queries import SCREENED_ID_TYPES
from xolotl.modules.screening.lists import EntityType, IdType
from xolotl.modules.screening.matching import NameQuery, QueryIdentifier

_KINDS: dict[CounterpartyKind, EntityType] = {
    CounterpartyKind.PERSON: EntityType.PERSON,
    CounterpartyKind.ORGANIZATION: EntityType.ORGANIZATION,
}
_BOTH = (EntityType.PERSON, EntityType.ORGANIZATION)


def entity_types_for(row: Counterparty) -> tuple[EntityType, ...]:
    """Say under which entity types a counterparty is screened.

    Returns:
        The kind's one type for a named person or organization, else both.

    """
    kind = _KINDS.get(row.kind)
    if kind is None or row.name is None:
        return _BOTH
    return (kind,)


def _identifiers(s: Session, row: Counterparty) -> tuple[QueryIdentifier, ...]:
    found: list[QueryIdentifier] = []
    if (
        row.identifier is not None
        and row.identifier_type is not None
        and row.identifier_type in SCREENED_ID_TYPES
    ):
        result = validate(row.identifier_type, row.identifier.reveal(session=s))
        if not isinstance(result, IdentifierError):
            found.append(
                QueryIdentifier(
                    SCREENED_ID_TYPES[row.identifier_type], normalized=result.value
                )
            )
    if row.wallet is not None:
        address = "".join(row.wallet.reveal(session=s).split())
        if address:
            found.append(
                QueryIdentifier(IdType.DIGITAL_CURRENCY_ADDRESS, normalized=address)
            )
    return tuple(found)


def query_for_counterparty(
    s: Session, row: Counterparty, *, entity_type: EntityType
) -> NameQuery:
    """Build the query of one counterparty under one entity type.

    Returns:
        Its name (none for a name-less counterparty), its country and the
        identifiers the matcher compares.

    """
    return NameQuery(
        entity_type=entity_type,
        names=() if row.name is None else (row.name.reveal(session=s),),
        countries=() if row.country is None else (row.country,),
        identifiers=_identifiers(s, row),
    )
