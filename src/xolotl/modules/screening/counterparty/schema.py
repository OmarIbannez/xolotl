"""The counterparty a transaction names, as the client sends it (SP-04 spec §4).

Pure: no database and no other module except the party module's value types, so
the monitoring core can import it (spec §2, contract ``monitoring-core-is-pure``).
"""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.party.schemas import CountryCode

_PII: dict[str, JsonValue] = {"pii": True}


def normalize_wallet(address: str | None) -> str | None:
    """Normalise a wallet address as the OFAC parser normalises a listed one.

    The screening library's ``wallet_identifier`` removes every whitespace character
    and keeps the case (base58 and bech32 checksums are case-sensitive), so a wallet
    is compared, hashed and screened in that form. An address with nothing left is
    absent.

    Returns:
        The address without whitespace, or None when nothing is left.

    """
    if address is None:
        return None
    return "".join(address.split()) or None


class CounterpartyKind(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    UNKNOWN = "UNKNOWN"


class CounterpartyScreeningStatus(StrEnum):
    UNSCREENED = "UNSCREENED"
    CLEAR = "CLEAR"
    PENDING_REVIEW = "PENDING_REVIEW"
    TRUE_MATCH = "TRUE_MATCH"


class CounterpartyIdentifier(BaseModel):
    """The SP-03 ad hoc identifier's shape, copied (plan 4a Deviation 13)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    id_type: IdentifierType
    value: str = Field(min_length=1, max_length=64, json_schema_extra=_PII)
    country: CountryCode | None = None


class CounterpartyInput(BaseModel):
    """Every field optional; at least a name, an account, a wallet or an identifier."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    kind: CounterpartyKind = CounterpartyKind.UNKNOWN
    name: str | None = Field(default=None, max_length=300, json_schema_extra=_PII)
    account: str | None = Field(default=None, max_length=64, json_schema_extra=_PII)
    institution: str | None = Field(default=None, max_length=120)
    country: CountryCode | None = None
    wallet_address: str | None = Field(
        default=None, max_length=128, json_schema_extra=_PII
    )
    wallet_network: str | None = Field(default=None, max_length=32)
    identifier: CounterpartyIdentifier | None = None

    @field_validator("wallet_address")
    @classmethod
    def _normalized_wallet(cls, value: str | None) -> str | None:
        return normalize_wallet(value)

    @model_validator(mode="after")
    def _names_someone(self) -> Self:
        if self.identifier is None and not (
            self.name or self.account or self.wallet_address
        ):
            msg = "COUNTERPARTY_EMPTY"
            raise ValueError(msg)
        return self
