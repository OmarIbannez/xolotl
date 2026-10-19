"""Party vocabularies. Values are UPPERCASE and stored as String(32) columns."""

from enum import StrEnum


class PartyType(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"


class PartyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    PURGED = "PURGED"


class PepStatus(StrEnum):
    NONE = "NONE"
    DOMESTIC = "DOMESTIC"
    FOREIGN = "FOREIGN"
    RCA = "RCA"
    INTL_ORG = "INTL_ORG"


class IdentifierType(StrEnum):
    CURP = "CURP"
    RFC = "RFC"
    CLABE = "CLABE"
    INE = "INE"
    CPF = "CPF"
    CNPJ = "CNPJ"
    CUIT = "CUIT"
    RUT = "RUT"
    NIT = "NIT"
    CEDULA_CO = "CEDULA_CO"
    DNI_PE = "DNI_PE"
    PASSPORT = "PASSPORT"


class ContactKind(StrEnum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"


class AddressType(StrEnum):
    RESIDENTIAL = "RESIDENTIAL"
    FISCAL = "FISCAL"
    OPERATIONAL = "OPERATIONAL"


class RelationshipType(StrEnum):
    SHAREHOLDER = "SHAREHOLDER"
    DIRECTOR = "DIRECTOR"
    LEGAL_REP = "LEGAL_REP"
    ATTORNEY = "ATTORNEY"
    CONTROLLER = "CONTROLLER"


class DocumentType(StrEnum):
    IDENTITY = "IDENTITY"
    PROOF_OF_ADDRESS = "PROOF_OF_ADDRESS"
    CONSTITUTIVE_ACT = "CONSTITUTIVE_ACT"
    POWER_OF_ATTORNEY = "POWER_OF_ATTORNEY"
    TAX_CERTIFICATE = "TAX_CERTIFICATE"
    OTHER = "OTHER"


class CustomerRelationshipStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    RESTRICTED = "RESTRICTED"
    BLOCKED = "BLOCKED"
    EXITED = "EXITED"


class VerificationResult(StrEnum):
    # A check outcome, not a credential: the password-literal rule misreads the name.
    PASS = "PASS"  # ruff: ignore[hardcoded-password-string]
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
