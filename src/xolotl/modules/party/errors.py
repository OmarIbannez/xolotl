"""The party module's problem codes (spec §9)."""

from typing import ClassVar

from xolotl.platform.errors import DomainError, ValidationFailedError


class IdentifierExistsError(DomainError):
    """Another party of the tenant holds this identifier.

    The detail is the existing party's id for a staff actor and the default sentence
    for anyone else.
    """

    default_code: ClassVar[str] = "IDENTIFIER_EXISTS"
    default_detail: ClassVar[str] = "a party with this identifier already exists"


class IdentifierInvalidError(ValidationFailedError):
    """The identifier fails its validator; the detail is ``"<ID_TYPE>: <REASON>"``."""

    default_code: ClassVar[str] = "IDENTIFIER_INVALID"
    default_detail: ClassVar[str] = "identifier is not valid"
