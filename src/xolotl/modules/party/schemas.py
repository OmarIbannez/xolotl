"""Request and response bodies of the party module.

Every field that holds a personal value is tagged ``json_schema_extra={"pii": True}``
so the log filter masks it. No ``Encrypted`` value appears here: responses carry
masks, and plaintext only in the reveal's ``PartyPiiResponse``.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from xolotl.modules.party.enums import (
    AddressType,
    ContactKind,
    CustomerRelationshipStatus,
    DocumentType,
    IdentifierType,
    PartyStatus,
    PartyType,
    PepStatus,
    RelationshipType,
)

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
_PII: dict[str, JsonValue] = {"pii": True}


class IdentifierInput(BaseModel):
    id_type: IdentifierType
    value: str = Field(min_length=1, max_length=64, json_schema_extra=_PII)
    country_code: CountryCode = "MX"
    valid_from: date | None = None
    valid_to: date | None = None


class PersonInput(BaseModel):
    given_names: str = Field(min_length=1, max_length=200, json_schema_extra=_PII)
    first_surname: str = Field(min_length=1, max_length=100, json_schema_extra=_PII)
    second_surname: str | None = Field(
        default=None, max_length=100, json_schema_extra=_PII
    )
    birth_date: date | None = Field(default=None, json_schema_extra=_PII)
    birth_country: CountryCode | None = None
    birth_state: str | None = Field(default=None, max_length=64)
    nationalities: list[CountryCode] = Field(default_factory=list[str])
    gender: str | None = Field(default=None, max_length=16)
    occupation_code: str | None = Field(default=None, max_length=16)
    economic_activity_code: str | None = Field(default=None, max_length=16)
    is_resident: bool | None = None
    pep_status: PepStatus = PepStatus.NONE
    pep_since: date | None = None
    pep_until: date | None = None
    clave_elector: str | None = Field(
        default=None, max_length=20, json_schema_extra=_PII
    )


class OrganizationInput(BaseModel):
    legal_name: str = Field(min_length=1, max_length=300, json_schema_extra=_PII)
    trade_name: str | None = Field(default=None, max_length=300)
    legal_form: str | None = Field(default=None, max_length=64)
    incorporation_date: date | None = None
    industry_code: str | None = Field(default=None, max_length=16)
    registry_ref: str | None = Field(default=None, max_length=64)
    country_of_incorporation: CountryCode | None = None


class PartyCreateRequest(BaseModel):
    party_type: PartyType
    country_code: CountryCode = "MX"
    person: PersonInput | None = None
    organization: OrganizationInput | None = None
    identifiers: list[IdentifierInput] = Field(
        default_factory=list[IdentifierInput], max_length=10
    )

    @model_validator(mode="after")
    def _one_subtype(self) -> Self:
        if self.party_type is PartyType.PERSON:
            if self.person is None or self.organization is not None:
                msg = "person is required for a PERSON party"
                raise ValueError(msg)
        elif self.organization is None or self.person is not None:
            msg = "organization is required for an ORGANIZATION party"
            raise ValueError(msg)
        return self


class PersonUpdate(BaseModel):
    """A person's patchable attributes; names and birth date are not among them."""

    birth_country: CountryCode | None = None
    birth_state: str | None = Field(default=None, max_length=64)
    nationalities: list[CountryCode] | None = None
    gender: str | None = Field(default=None, max_length=16)
    occupation_code: str | None = Field(default=None, max_length=16)
    economic_activity_code: str | None = Field(default=None, max_length=16)
    is_resident: bool | None = None
    pep_status: PepStatus | None = None
    pep_since: date | None = None
    pep_until: date | None = None

    @model_validator(mode="after")
    def _no_null_for_required_columns(self) -> Self:
        for name in ("nationalities", "pep_status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                msg = f"{name} cannot be null"
                raise ValueError(msg)
        return self


class OrganizationUpdate(BaseModel):
    """An organisation's patchable attributes; the legal name is not among them."""

    trade_name: str | None = Field(default=None, max_length=300)
    legal_form: str | None = Field(default=None, max_length=64)
    incorporation_date: date | None = None
    industry_code: str | None = Field(default=None, max_length=16)
    registry_ref: str | None = Field(default=None, max_length=64)
    country_of_incorporation: CountryCode | None = None


class PartyUpdateRequest(BaseModel):
    person: PersonUpdate | None = None
    organization: OrganizationUpdate | None = None


class PartyRelationshipInput(BaseModel):
    to_party_id: UUID
    rel_type: RelationshipType
    ownership_pct: Decimal | None = Field(default=None, gt=0, le=100, decimal_places=4)
    control_basis: str | None = Field(default=None, max_length=200)
    valid_from: date | None = None
    valid_to: date | None = None
    source_doc_id: UUID | None = None


class RelationshipOpenRequest(BaseModel):
    party_id: UUID
    external_ref: str = Field(min_length=1, max_length=100)
    product: str | None = Field(default=None, max_length=32)
    account_tier: str | None = Field(default=None, max_length=32)
    started_at: datetime | None = None


class RelationshipCloseRequest(BaseModel):
    ended_at: datetime | None = None


class LegalHoldRequest(BaseModel):
    legal_hold: bool


class ContactInput(BaseModel):
    kind: ContactKind
    value: str = Field(min_length=1, max_length=254, json_schema_extra=_PII)
    is_primary: bool = False


class AddressLines(BaseModel):
    street: str = Field(min_length=1, max_length=200, json_schema_extra=_PII)
    ext_number: str | None = Field(default=None, max_length=20, json_schema_extra=_PII)
    int_number: str | None = Field(default=None, max_length=20, json_schema_extra=_PII)
    neighbourhood: str | None = Field(
        default=None, max_length=120, json_schema_extra=_PII
    )
    city: str | None = Field(default=None, max_length=120, json_schema_extra=_PII)


class AddressInput(BaseModel):
    address_type: AddressType
    lines: AddressLines
    country_code: CountryCode = "MX"
    admin_area_code: str | None = Field(default=None, max_length=8)
    municipality_code: str | None = Field(default=None, max_length=8)
    postal_code: str | None = Field(default=None, max_length=10)
    valid_from: date | None = None
    valid_to: date | None = None


class MaskedIdentifier(BaseModel):
    id: UUID
    id_type: IdentifierType
    country_code: str
    masked_value: str
    valid_from: date | None
    valid_to: date | None


class PartyResponse(BaseModel):
    id: UUID
    party_type: PartyType
    country_code: str
    status: PartyStatus
    display_name: str | None
    identifiers: list[MaskedIdentifier]
    legal_hold: bool
    retention_until: date | None
    created_at: datetime
    updated_at: datetime


class PersonAttributes(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    birth_country: str | None
    birth_state: str | None
    nationalities: list[str]
    gender: str | None
    occupation_code: str | None
    economic_activity_code: str | None
    is_resident: bool | None
    pep_status: PepStatus
    pep_since: date | None
    pep_until: date | None


class OrganizationAttributes(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    trade_name: str | None
    legal_form: str | None
    incorporation_date: date | None
    industry_code: str | None
    registry_ref: str | None
    country_of_incorporation: str | None


class MaskedContact(BaseModel):
    id: UUID
    kind: ContactKind
    masked_value: str
    is_primary: bool
    created_at: datetime


class AddressResponse(BaseModel):
    """An address's codes; its lines are personal data, shown only by the reveal."""

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    address_type: AddressType
    country_code: str
    admin_area_code: str | None
    municipality_code: str | None
    postal_code: str | None
    valid_from: date
    valid_to: date | None


class PartyRelationshipResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    from_party_id: UUID
    to_party_id: UUID
    rel_type: RelationshipType
    ownership_pct: Decimal | None
    control_basis: str | None
    valid_from: date
    valid_to: date | None
    source_doc_id: UUID | None


class DocumentResponse(BaseModel):
    id: UUID
    party_id: UUID
    doc_type: DocumentType
    sha256: str
    size: int
    content_type: str
    uploaded_by: str
    uploaded_at: datetime


class CustomerRelationshipResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    party_id: UUID
    external_ref: str
    product: str | None
    status: CustomerRelationshipStatus
    account_tier: str | None
    next_review_at: date | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class PartyStaffResponse(PartyResponse):
    person: PersonAttributes | None
    organization: OrganizationAttributes | None
    contacts: list[MaskedContact]
    addresses: list[AddressResponse]
    relationships: list[PartyRelationshipResponse]
    documents: list[DocumentResponse]
    customer_relationships: list[CustomerRelationshipResponse]


class RevealedIdentifier(BaseModel):
    id: UUID
    id_type: IdentifierType
    value: str = Field(json_schema_extra=_PII)


class RevealedContact(BaseModel):
    id: UUID
    kind: ContactKind
    value: str = Field(json_schema_extra=_PII)


class RevealedAddress(BaseModel):
    id: UUID
    address_type: AddressType
    lines: AddressLines


class PartyPiiResponse(BaseModel):
    party_id: UUID
    given_names: str | None = Field(json_schema_extra=_PII)
    first_surname: str | None = Field(json_schema_extra=_PII)
    second_surname: str | None = Field(json_schema_extra=_PII)
    birth_date: date | None = Field(json_schema_extra=_PII)
    clave_elector: str | None = Field(json_schema_extra=_PII)
    legal_name: str | None = Field(json_schema_extra=_PII)
    identifiers: list[RevealedIdentifier]
    contacts: list[RevealedContact]
    addresses: list[RevealedAddress]
