"""Party writes and reads (SP-02 spec §9, program spec D6, D7, D17, D20).

Writes run in the caller's transaction: each seals every personal value explicitly
under the tenant's DATA key, records its audit event last and then publishes its
ids-only event. Reads decrypt for processing only (masks and initials, no audit);
``reveal_pii`` is the one read that shows plaintext to a person, and it records one
``VIEW_PII`` event per non-empty field group before it decrypts anything.
``screening_profile`` decrypts names, birth date and identifiers for the screening
engine only: no person sees them, so it records nothing.
"""

import re
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Select, or_, select, update
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import (
    ContactKind,
    CustomerRelationshipStatus,
    IdentifierType,
    PartyStatus,
    PartyType,
    PepStatus,
    RelationshipType,
)
from xolotl.modules.party.errors import IdentifierExistsError, IdentifierInvalidError
from xolotl.modules.party.events import (
    IDENTIFIER_ADDED,
    PARTY_CREATED,
    PARTY_UPDATED,
    publish_party_event,
)
from xolotl.modules.party.identifiers import IdentifierError, validate
from xolotl.modules.party.masking import initials, mask_identifier
from xolotl.modules.party.models import (
    CustomerRelationship,
    Document,
    Organization,
    Party,
    PartyAddress,
    PartyContact,
    PartyIdentifier,
    PartyRelationship,
    Person,
)
from xolotl.modules.party.schemas import (
    AddressInput,
    AddressLines,
    AddressResponse,
    ContactInput,
    CustomerRelationshipResponse,
    DocumentResponse,
    IdentifierInput,
    MaskedContact,
    MaskedIdentifier,
    OrganizationAttributes,
    OrganizationInput,
    PartyCreateRequest,
    PartyPiiResponse,
    PartyRelationshipInput,
    PartyRelationshipResponse,
    PartyResponse,
    PartyStaffResponse,
    PartyUpdateRequest,
    PersonAttributes,
    PersonInput,
    RevealedAddress,
    RevealedContact,
    RevealedIdentifier,
)
from xolotl.platform import clock
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.crypto import Encrypted, encrypt, encrypt_json
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.db import bound_context
from xolotl.platform.enums import ActorType
from xolotl.platform.errors import DomainError, NotFoundError, ValidationFailedError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Applied after stripping spaces, hyphens, parentheses, dots and one leading "+".
PHONE_RE = re.compile(r"^\d{8,15}$")
_PHONE_SEPARATORS = re.compile(r"[\s().-]")


def _seal(s: Session, tenant_id: UUID, value: str | None) -> Encrypted[str] | None:
    if value is None:
        return None
    return encrypt(value, tenant_id=tenant_id, session=s)


def _plain(s: Session, value: Encrypted[str] | None) -> str:
    return "" if value is None else value.reveal(session=s)


def _person_row(s: Session, party: Party, body: PersonInput) -> Person:
    tenant_id = party.tenant_id
    birth = body.birth_date.isoformat() if body.birth_date is not None else None
    return Person(
        tenant_id=tenant_id,
        id=party.id,
        given_names=_seal(s, tenant_id, body.given_names),
        first_surname=_seal(s, tenant_id, body.first_surname),
        second_surname=_seal(s, tenant_id, body.second_surname),
        birth_date=_seal(s, tenant_id, birth),
        birth_country=body.birth_country,
        birth_state=body.birth_state,
        nationalities=list(body.nationalities),
        gender=body.gender,
        occupation_code=body.occupation_code,
        economic_activity_code=body.economic_activity_code,
        is_resident=body.is_resident,
        pep_status=body.pep_status,
        pep_since=body.pep_since,
        pep_until=body.pep_until,
        clave_elector=_seal(s, tenant_id, body.clave_elector),
    )


def _organization_row(
    s: Session, party: Party, body: OrganizationInput
) -> Organization:
    return Organization(
        tenant_id=party.tenant_id,
        id=party.id,
        legal_name=_seal(s, party.tenant_id, body.legal_name),
        trade_name=body.trade_name,
        legal_form=body.legal_form,
        incorporation_date=body.incorporation_date,
        industry_code=body.industry_code,
        registry_ref=body.registry_ref,
        country_of_incorporation=body.country_of_incorporation,
    )


def _birth_date_of(s: Session, party: Party) -> date | None:
    if party.party_type is not PartyType.PERSON:
        return None
    person = s.get(Person, (party.tenant_id, party.id))
    if person is None or person.birth_date is None:
        return None
    return date.fromisoformat(person.birth_date.reveal(session=s))


def _add_identifier(s: Session, party: Party, body: IdentifierInput) -> PartyIdentifier:
    # Validation, deduplication and the row; the caller audits and publishes.
    result = validate(body.id_type, body.value, birth_date=_birth_date_of(s, party))
    if isinstance(result, IdentifierError):
        msg = f"{result.id_type.value}: {result.reason.value}"
        raise IdentifierInvalidError(msg)
    tag = blind_index(
        body.id_type.value.lower(), result.value, tenant_id=party.tenant_id, session=s
    )
    existing = s.scalar(
        select(PartyIdentifier).where(
            PartyIdentifier.id_type == body.id_type,
            PartyIdentifier.value_hash == tag,
        )
    )
    if existing is not None:
        if bound_context(s).actor_type is ActorType.STAFF:
            raise IdentifierExistsError(str(existing.party_id))
        raise IdentifierExistsError
    row = PartyIdentifier(
        tenant_id=party.tenant_id,
        party_id=party.id,
        id_type=body.id_type,
        country_code=body.country_code,
        value=encrypt(result.value, tenant_id=party.tenant_id, session=s),
        value_hash=tag,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
    )
    s.add(row)
    s.flush()
    return row


def create_party(s: Session, body: PartyCreateRequest) -> Party:
    """Create a person or an organisation with its identifiers.

    An identifier that fails its validator raises ``IdentifierInvalidError``, and
    one another party of the tenant holds raises ``IdentifierExistsError``.

    Returns:
        The new party, flushed.

    """
    tenant_id = bound_context(s).require_tenant_id()
    party = Party(
        tenant_id=tenant_id, party_type=body.party_type, country_code=body.country_code
    )
    s.add(party)
    s.flush()
    if body.person is not None:
        s.add(_person_row(s, party, body.person))
    if body.organization is not None:
        s.add(_organization_row(s, party, body.organization))
    s.flush()
    identifier_ids: list[JsonValue] = [
        str(_add_identifier(s, party, item).id) for item in body.identifiers
    ]
    _ = record(
        s,
        AuditEvent(
            action="party.created",
            entity_type="party",
            entity_id=str(party.id),
            detail={
                "party_type": party.party_type.value,
                "identifier_ids": identifier_ids,
            },
        ),
    )
    _ = publish_party_event(
        s,
        PARTY_CREATED,
        entity="party",
        entity_id=party.id,
        data={
            "party_id": str(party.id),
            "party_type": party.party_type.value,
            "identifier_ids": identifier_ids,
        },
    )
    return party


def get_party(s: Session, party_id: UUID, *, for_update: bool = False) -> Party:
    """Load one party of the session's tenant.

    Returns:
        The party.

    Raises:
        NotFoundError: When no such party exists for the tenant (another tenant's
            row is invisible under row-level security).

    """
    query = select(Party).where(Party.id == party_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    party = s.scalar(query)
    if party is None:
        msg = "party not found"
        raise NotFoundError(msg)
    return party


def add_identifier(
    s: Session, party_id: UUID, body: IdentifierInput
) -> PartyIdentifier:
    """Validate, deduplicate and store one more identifier of a party.

    Returns:
        The new identifier row.

    """
    party = get_party(s, party_id)
    row = _add_identifier(s, party, body)
    _ = record(
        s,
        AuditEvent(
            action="party.identifier_added",
            entity_type="party_identifier",
            entity_id=str(row.id),
            detail={"party_id": str(party.id), "id_type": row.id_type.value},
        ),
    )
    _ = publish_party_event(
        s,
        IDENTIFIER_ADDED,
        entity="party",
        entity_id=party.id,
        data={
            "party_id": str(party.id),
            "identifier_id": str(row.id),
            "id_type": row.id_type.value,
        },
    )
    return row


def _canonical_contact(body: ContactInput) -> str:
    if body.kind is ContactKind.EMAIL:
        canonical = body.value.strip().lower()
        if not EMAIL_RE.match(canonical):
            msg = "invalid email"
            raise ValidationFailedError(msg)
        return canonical
    canonical = _PHONE_SEPARATORS.sub("", body.value).removeprefix("+")
    if not PHONE_RE.match(canonical):
        msg = "invalid phone"
        raise ValidationFailedError(msg)
    return canonical


def add_contact(s: Session, party_id: UUID, body: ContactInput) -> PartyContact:
    """Normalise and store an email address or a phone number of a party.

    A primary contact clears the primary flag of the party's other contacts of
    its kind.

    Returns:
        The new contact row.

    A value that is not an email address or a phone number raises
    ``ValidationFailedError``.

    Raises:
        DomainError: When the party already has this contact.

    """
    party = get_party(s, party_id)
    canonical = _canonical_contact(body)
    tag = blind_index(
        body.kind.value.lower(), canonical, tenant_id=party.tenant_id, session=s
    )
    existing = s.scalar(
        select(PartyContact.id).where(
            PartyContact.party_id == party.id,
            PartyContact.kind == body.kind,
            PartyContact.value_hash == tag,
        )
    )
    if existing is not None:
        msg = "contact already exists"
        raise DomainError(msg)
    if body.is_primary:
        _ = s.execute(
            update(PartyContact)
            .where(PartyContact.party_id == party.id, PartyContact.kind == body.kind)
            .values(is_primary=False)
        )
    row = PartyContact(
        tenant_id=party.tenant_id,
        party_id=party.id,
        kind=body.kind,
        value=encrypt(canonical, tenant_id=party.tenant_id, session=s),
        value_hash=tag,
        is_primary=body.is_primary,
    )
    s.add(row)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="party.contact_added",
            entity_type="party_contact",
            entity_id=str(row.id),
            detail={"party_id": str(party.id), "kind": body.kind.value},
        ),
    )
    return row


def add_address(s: Session, party_id: UUID, body: AddressInput) -> PartyAddress:
    """Store an address of a party: lines encrypted, codes in clear.

    Returns:
        The new address row; ``valid_from`` defaults to today.

    """
    party = get_party(s, party_id)
    row = PartyAddress(
        tenant_id=party.tenant_id,
        party_id=party.id,
        address_type=body.address_type,
        address=encrypt_json(
            body.lines.model_dump(mode="json", exclude_none=True),
            tenant_id=party.tenant_id,
            session=s,
        ),
        country_code=body.country_code,
        admin_area_code=body.admin_area_code,
        municipality_code=body.municipality_code,
        postal_code=body.postal_code,
        valid_from=body.valid_from or clock.now().date(),
        valid_to=body.valid_to,
    )
    s.add(row)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="party.address_added",
            entity_type="party_address",
            entity_id=str(row.id),
            detail={"party_id": str(party.id), "address_type": body.address_type.value},
        ),
    )
    return row


def update_party(s: Session, party_id: UUID, body: PartyUpdateRequest) -> Party:
    """Change a party's plain attributes; names and identifiers are not patchable.

    Only the fields the request sets are applied. A request that changes nothing
    records no audit event and publishes nothing.

    Returns:
        The party.

    Raises:
        ValidationFailedError: When the attributes are those of the other party
            type.

    """
    party = get_party(s, party_id, for_update=True)
    is_person = party.party_type is PartyType.PERSON
    if (body.person is not None and not is_person) or (
        body.organization is not None and is_person
    ):
        msg = "attributes do not match the party type"
        raise ValidationFailedError(msg)
    target: Person | Organization | None = None
    values: dict[str, object] = {}
    if body.person is not None:
        target = s.get_one(Person, (party.tenant_id, party.id))
        values = body.person.model_dump(exclude_unset=True)
    elif body.organization is not None:
        target = s.get_one(Organization, (party.tenant_id, party.id))
        values = body.organization.model_dump(exclude_unset=True)
    changed: list[str] = []
    for name, value in values.items():
        if getattr(target, name) != value:
            setattr(target, name, value)
            changed.append(name)
    if not changed:
        return party
    fields: list[JsonValue] = list[JsonValue](sorted(changed))
    party.updated_at = clock.now()
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="party.updated",
            entity_type="party",
            entity_id=str(party.id),
            detail={"fields": fields},
        ),
    )
    _ = publish_party_event(
        s,
        PARTY_UPDATED,
        entity="party",
        entity_id=party.id,
        data={"party_id": str(party.id), "fields": fields},
    )
    return party


def add_relationship(
    s: Session, from_party_id: UUID, body: PartyRelationshipInput
) -> PartyRelationship:
    """Record an ownership or control edge from one party to another.

    Returns:
        The new edge; ``valid_from`` defaults to today.

    Raises:
        ValidationFailedError: When the other party is unknown or is the same
            party, or when ``ownership_pct`` is given for a type other than
            ``SHAREHOLDER``.

    """
    party = get_party(s, from_party_id)
    if body.to_party_id == party.id:
        msg = "a party cannot relate to itself"
        raise ValidationFailedError(msg)
    if s.get(Party, (party.tenant_id, body.to_party_id)) is None:
        msg = "unknown to_party_id"
        raise ValidationFailedError(msg)
    if (
        body.ownership_pct is not None
        and body.rel_type is not RelationshipType.SHAREHOLDER
    ):
        msg = "ownership_pct applies to SHAREHOLDER only"
        raise ValidationFailedError(msg)
    row = PartyRelationship(
        tenant_id=party.tenant_id,
        from_party_id=party.id,
        to_party_id=body.to_party_id,
        rel_type=body.rel_type,
        ownership_pct=body.ownership_pct,
        control_basis=body.control_basis,
        valid_from=body.valid_from or clock.now().date(),
        valid_to=body.valid_to,
        source_doc_id=body.source_doc_id,
    )
    s.add(row)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="party.relationship_added",
            entity_type="party_relationship",
            entity_id=str(row.id),
            detail={
                "from_party_id": str(party.id),
                "to_party_id": str(body.to_party_id),
                "rel_type": body.rel_type.value,
            },
        ),
    )
    return row


def parties_query(
    s: Session,
    *,
    status: PartyStatus | None = None,
    external_ref: str | None = None,
    id_type: IdentifierType | None = None,
    value: str | None = None,
) -> Select[Party]:
    """Build the query of the tenant's parties matching every filter given.

    An identifier is found through its blind index: nothing is decrypted.

    Returns:
        The unordered select; the caller paginates it.

    Raises:
        ValidationFailedError: When only one of ``id_type`` and ``value`` is given.
        IdentifierInvalidError: When the identifier fails its validator.

    """
    query = select(Party)
    if status is not None:
        query = query.where(Party.status == status)
    if external_ref is not None:
        query = query.where(
            Party.id.in_(
                select(CustomerRelationship.party_id).where(
                    CustomerRelationship.external_ref == external_ref
                )
            )
        )
    if (id_type is None) != (value is None):
        msg = "id_type and value go together"
        raise ValidationFailedError(msg)
    if id_type is not None and value is not None:
        result = validate(id_type, value)
        if isinstance(result, IdentifierError):
            msg = f"{result.id_type.value}: {result.reason.value}"
            raise IdentifierInvalidError(msg)
        tag = blind_index(
            id_type.value.lower(),
            result.value,
            tenant_id=bound_context(s).require_tenant_id(),
            session=s,
        )
        query = query.where(
            Party.id.in_(
                select(PartyIdentifier.party_id).where(
                    PartyIdentifier.id_type == id_type,
                    PartyIdentifier.value_hash == tag,
                )
            )
        )
    return query


def party_ids_after(s: Session, *, after: UUID | None, limit: int) -> list[UUID]:
    """Page through the tenant's ``ACTIVE`` party ids in id order (re-screen walks).

    Returns:
        Up to ``limit`` ids greater than ``after`` (every id when it is None).

    """
    query = select(Party.id).where(Party.status == PartyStatus.ACTIVE)
    if after is not None:
        query = query.where(Party.id > after)
    return list(s.scalars(query.order_by(Party.id).limit(limit)))


def masked_identifier(s: Session, row: PartyIdentifier) -> MaskedIdentifier:
    """Mask an identifier for a list, a detail or a machine (no audit).

    Returns:
        The identifier with its value reduced to the last four characters.

    """
    return MaskedIdentifier(
        id=row.id,
        id_type=row.id_type,
        country_code=row.country_code,
        masked_value=mask_identifier(row.value.reveal(session=s)),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def masked_contact(s: Session, row: PartyContact) -> MaskedContact:
    """Mask a contact the way identifiers are masked (no audit).

    Returns:
        The contact with its value reduced to the last four characters.

    """
    return MaskedContact(
        id=row.id,
        kind=row.kind,
        masked_value=mask_identifier(row.value.reveal(session=s)),
        is_primary=row.is_primary,
        created_at=row.created_at,
    )


def document_response(row: Document) -> DocumentResponse:
    """Describe a stored document, its digest as hex.

    Returns:
        The document's metadata.

    """
    return DocumentResponse(
        id=row.id,
        party_id=row.party_id,
        doc_type=row.doc_type,
        sha256=row.sha256.hex(),
        size=row.size,
        content_type=row.content_type,
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
    )


def _display_name(
    s: Session, party: Party, person: Person | None, organization: Organization | None
) -> str | None:
    if party.status is PartyStatus.PURGED:
        return None
    if person is not None:
        return initials(
            _plain(s, person.given_names),
            _plain(s, person.first_surname),
            _plain(s, person.second_surname),
        )
    if organization is not None:
        return initials(_plain(s, organization.legal_name))
    return None


def _masked_identifiers(
    s: Session, party_ids: Sequence[UUID]
) -> dict[UUID, list[MaskedIdentifier]]:
    masked: defaultdict[UUID, list[MaskedIdentifier]] = defaultdict(list)
    rows = s.scalars(
        select(PartyIdentifier)
        .where(PartyIdentifier.party_id.in_(party_ids))
        .order_by(PartyIdentifier.created_at, PartyIdentifier.id)
    )
    for row in rows:
        masked[row.party_id].append(masked_identifier(s, row))
    return masked


def summaries(s: Session, parties: Sequence[Party]) -> list[PartyResponse]:
    """Describe parties with masked identifiers and initials, in their order.

    One query per subtype table and one for the identifiers, whatever the count.

    Returns:
        One summary per party.

    """
    ids = [party.id for party in parties]
    if not ids:
        return []
    people = {p.id: p for p in s.scalars(select(Person).where(Person.id.in_(ids)))}
    organizations = {
        o.id: o for o in s.scalars(select(Organization).where(Organization.id.in_(ids)))
    }
    identifiers = _masked_identifiers(s, ids)
    return [
        PartyResponse(
            id=party.id,
            party_type=party.party_type,
            country_code=party.country_code,
            status=party.status,
            display_name=_display_name(
                s, party, people.get(party.id), organizations.get(party.id)
            ),
            identifiers=identifiers.get(party.id, []),
            legal_hold=party.legal_hold,
            retention_until=party.retention_until,
            created_at=party.created_at,
            updated_at=party.updated_at,
        )
        for party in parties
    ]


def summary(s: Session, party: Party) -> PartyResponse:
    """Describe one party the way ``summaries`` does.

    Returns:
        The party's summary.

    """
    (one,) = summaries(s, [party])
    return one


def staff_view(s: Session, party_id: UUID) -> PartyStaffResponse:
    """Describe a party for staff: masked values plus every sub-collection.

    Returns:
        The summary with the subtype's plain attributes, masked contacts, address
        codes, relationship edges, documents and customer relationships.

    """
    party = get_party(s, party_id)
    base = summary(s, party)
    person = s.get(Person, (party.tenant_id, party.id))
    organization = s.get(Organization, (party.tenant_id, party.id))
    return PartyStaffResponse(
        id=base.id,
        party_type=base.party_type,
        country_code=base.country_code,
        status=base.status,
        display_name=base.display_name,
        identifiers=base.identifiers,
        legal_hold=base.legal_hold,
        retention_until=base.retention_until,
        created_at=base.created_at,
        updated_at=base.updated_at,
        person=PersonAttributes.model_validate(person) if person else None,
        organization=(
            OrganizationAttributes.model_validate(organization)
            if organization
            else None
        ),
        contacts=[masked_contact(s, row) for row in _contacts(s, party.id)],
        addresses=[
            AddressResponse.model_validate(row) for row in _addresses(s, party.id)
        ],
        relationships=[
            PartyRelationshipResponse.model_validate(row)
            for row in _relationships(s, party.id)
        ],
        documents=[document_response(row) for row in _documents(s, party.id)],
        customer_relationships=[
            CustomerRelationshipResponse.model_validate(row)
            for row in _customer_relationships(s, party.id)
        ],
    )


def _identifiers(s: Session, party_id: UUID) -> Sequence[PartyIdentifier]:
    return s.scalars(
        select(PartyIdentifier)
        .where(PartyIdentifier.party_id == party_id)
        .order_by(PartyIdentifier.created_at, PartyIdentifier.id)
    ).all()


def _contacts(s: Session, party_id: UUID) -> Sequence[PartyContact]:
    return s.scalars(
        select(PartyContact)
        .where(PartyContact.party_id == party_id)
        .order_by(PartyContact.created_at, PartyContact.id)
    ).all()


def _addresses(s: Session, party_id: UUID) -> Sequence[PartyAddress]:
    return s.scalars(
        select(PartyAddress)
        .where(PartyAddress.party_id == party_id)
        .order_by(PartyAddress.valid_from, PartyAddress.id)
    ).all()


def _relationships(s: Session, party_id: UUID) -> Sequence[PartyRelationship]:
    return s.scalars(
        select(PartyRelationship)
        .where(
            or_(
                PartyRelationship.from_party_id == party_id,
                PartyRelationship.to_party_id == party_id,
            )
        )
        .order_by(PartyRelationship.valid_from, PartyRelationship.id)
    ).all()


def _documents(s: Session, party_id: UUID) -> Sequence[Document]:
    return s.scalars(
        select(Document)
        .where(Document.party_id == party_id)
        .order_by(Document.uploaded_at, Document.id)
    ).all()


def _customer_relationships(
    s: Session, party_id: UUID
) -> Sequence[CustomerRelationship]:
    return s.scalars(
        select(CustomerRelationship)
        .where(CustomerRelationship.party_id == party_id)
        .order_by(CustomerRelationship.created_at, CustomerRelationship.id)
    ).all()


def _person_lines(person: Person) -> list[tuple[str, Encrypted[str] | None]]:
    return [
        ("given_names", person.given_names),
        ("first_surname", person.first_surname),
        ("second_surname", person.second_surname),
        ("birth_date", person.birth_date),
        ("clave_elector", person.clave_elector),
    ]


def _profile(s: Session, party: Party) -> dict[str, Encrypted[str]]:
    person = s.get(Person, (party.tenant_id, party.id))
    organization = s.get(Organization, (party.tenant_id, party.id))
    lines: list[tuple[str, Encrypted[str] | None]] = []
    if person is not None:
        lines = _person_lines(person)
    elif organization is not None:
        lines = [("legal_name", organization.legal_name)]
    return {name: value for name, value in lines if value is not None}


def _group_reveal(
    s: Session,
    party_id: UUID,
    group: str,
    fields: list[str],
    count: int | None = None,
) -> None:
    # One VIEW_PII per non-empty group, recorded before anything is decrypted.
    detail: dict[str, JsonValue] = {"group": group, "fields": list[JsonValue](fields)}
    if count is not None:
        detail["count"] = count
    _ = record(
        s,
        AuditEvent(
            action=VIEW_PII,
            entity_type="party",
            entity_id=str(party_id),
            detail=detail,
        ),
    )


def reveal_pii(s: Session, party_id: UUID) -> PartyPiiResponse:
    """Show a party's personal values to a person, auditing each field group.

    The groups are ``profile`` (names, birth date, clave de elector or legal
    name), ``identifiers``, ``contacts`` and ``addresses``, each recorded as one
    ``VIEW_PII`` event when it holds anything.

    Returns:
        The plaintext values.

    """
    party = get_party(s, party_id)
    profile = _profile(s, party)
    identifiers = _identifiers(s, party.id)
    contacts = _contacts(s, party.id)
    addresses = _addresses(s, party.id)
    if profile:
        _group_reveal(s, party.id, "profile", list(profile))
    if identifiers:
        _group_reveal(s, party.id, "identifiers", ["value"], len(identifiers))
    if contacts:
        _group_reveal(s, party.id, "contacts", ["value"], len(contacts))
    if addresses:
        _group_reveal(s, party.id, "addresses", ["lines"], len(addresses))
    shown = {name: value.reveal(session=s) for name, value in profile.items()}
    birth = shown.get("birth_date")
    return PartyPiiResponse(
        party_id=party.id,
        given_names=shown.get("given_names"),
        first_surname=shown.get("first_surname"),
        second_surname=shown.get("second_surname"),
        birth_date=date.fromisoformat(birth) if birth is not None else None,
        clave_elector=shown.get("clave_elector"),
        legal_name=shown.get("legal_name"),
        identifiers=[
            RevealedIdentifier(
                id=row.id, id_type=row.id_type, value=row.value.reveal(session=s)
            )
            for row in identifiers
        ],
        contacts=[
            RevealedContact(id=row.id, kind=row.kind, value=row.value.reveal(session=s))
            for row in contacts
        ],
        addresses=[
            RevealedAddress(
                id=row.id,
                address_type=row.address_type,
                lines=AddressLines.model_validate(row.address.reveal(session=s)),
            )
            for row in addresses
        ],
    )


@dataclass(frozen=True, slots=True)
class PartyScreeningProfile:
    """What the screening engine needs of a party, decrypted for processing only.

    ``identifiers`` holds ``(id_type, canonical value, country_code)`` triples.
    """

    party_id: UUID
    party_type: PartyType
    status: PartyStatus
    country_code: str
    given_names: str | None
    first_surname: str | None
    second_surname: str | None
    birth_date: date | None
    gender: str | None
    nationalities: tuple[str, ...]
    birth_country: str | None
    legal_name: str | None
    trade_name: str | None
    country_of_incorporation: str | None
    identifiers: tuple[tuple[IdentifierType, str, str], ...]


def _opened(s: Session, value: Encrypted[str] | None) -> str | None:
    return None if value is None else value.reveal(session=s)


def screening_profile(s: Session, party_id: UUID) -> PartyScreeningProfile:
    """Read a party's names, birth date and identifiers for screening (D6).

    The values are decrypted for processing only and never shown to a person, so
    nothing is audited. A purged party comes back with its encrypted names empty
    and no identifiers; an organization keeps its plaintext trade name, so a caller
    that must not use a purged party's names checks ``status``.

    Returns:
        The party's screening profile.

    """
    party = get_party(s, party_id)
    person = s.get(Person, (party.tenant_id, party.id))
    organization = s.get(Organization, (party.tenant_id, party.id))
    birth = None if person is None else _opened(s, person.birth_date)
    return PartyScreeningProfile(
        party_id=party.id,
        party_type=party.party_type,
        status=party.status,
        country_code=party.country_code,
        given_names=None if person is None else _opened(s, person.given_names),
        first_surname=None if person is None else _opened(s, person.first_surname),
        second_surname=None if person is None else _opened(s, person.second_surname),
        birth_date=None if birth is None else date.fromisoformat(birth),
        gender=None if person is None else person.gender,
        nationalities=() if person is None else tuple(person.nationalities),
        birth_country=None if person is None else person.birth_country,
        legal_name=(
            None if organization is None else _opened(s, organization.legal_name)
        ),
        trade_name=None if organization is None else organization.trade_name,
        country_of_incorporation=(
            None if organization is None else organization.country_of_incorporation
        ),
        identifiers=tuple(
            (row.id_type, row.value.reveal(session=s), row.country_code)
            for row in _identifiers(s, party.id)
        ),
    )


def pep_status_of(s: Session, party_id: UUID) -> PepStatus:
    """Read a party's PEP status for transaction monitoring (SP-04 §9.1).

    Returns:
        The person's status; an organization is never a PEP (``NONE``). An
        unknown party raises ``get_party``'s ``NotFoundError``.

    """
    status = s.scalar(select(Person.pep_status).where(Person.id == party_id))
    if status is None:
        _ = get_party(s, party_id)
        return PepStatus.NONE
    return status


@dataclass(frozen=True, slots=True)
class RelationshipRef:
    """What transaction monitoring needs of a customer relationship (SP-04 §6.1)."""

    id: UUID
    party_id: UUID
    product: str | None
    account_tier: str | None
    status: CustomerRelationshipStatus
    # When the relationship began, for the new-account gates (SP-04 §9.1).
    started_at: datetime | None = None


def _ref_of(row: CustomerRelationship) -> RelationshipRef:
    return RelationshipRef(
        id=row.id,
        party_id=row.party_id,
        product=row.product,
        account_tier=row.account_tier,
        status=row.status,
        started_at=row.started_at,
    )


def relationship_by_external_ref(
    s: Session, refs: Collection[str]
) -> dict[str, RelationshipRef]:
    """Resolve the tenant's relationships by their ``external_ref`` in one query.

    Returns:
        The found relationships by external reference; a missing one is absent.

    """
    if not refs:
        return {}
    rows = s.scalars(
        select(CustomerRelationship).where(
            CustomerRelationship.external_ref.in_(set(refs))
        )
    ).all()
    return {row.external_ref: _ref_of(row) for row in rows}


def relationship_ref(s: Session, relationship_id: UUID) -> RelationshipRef:
    """Load one relationship of the session's tenant.

    Returns:
        The relationship.

    Raises:
        NotFoundError: When the tenant has no such relationship (another tenant's
            row is invisible under row-level security).

    """
    row = s.scalar(
        select(CustomerRelationship).where(CustomerRelationship.id == relationship_id)
    )
    if row is None:
        msg = "relationship not found"
        raise NotFoundError(msg)
    return _ref_of(row)
