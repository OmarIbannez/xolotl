"""What staff are shown of a counterparty (plan 4a A10, reconciliation finding 13).

Screening owns these types and monitoring imports them, never the reverse. The
summary is masked by construction: it says which components a counterparty has,
because masking a value would need its plaintext. ``RevealedCounterparty`` is the one
path to the characters of a value, built by ``views.reveal_counterparty`` after it
has audited each one. Plan 4c (C3) appends its response models here.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from xolotl.modules.screening.counterparty.schema import (
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.schemas import MatchSummary, RunResponse


class CounterpartySummary(BaseModel):
    """A counterparty for staff, masked: no value, only which components exist."""

    id: UUID
    kind: CounterpartyKind
    has_name: bool
    has_account: bool
    has_wallet: bool
    has_identifier: bool
    institution: str | None
    country: str | None
    wallet_network: str | None
    screening_status: CounterpartyScreeningStatus
    txn_count: int


class RevealedCounterparty(BaseModel):
    """A counterparty's values in plaintext, each audited as ``VIEW_PII`` first."""

    name: str | None
    account: str | None
    wallet_address: str | None
    wallet_network: str | None
    identifier_type: str | None
    identifier: str | None


class CounterpartyResponse(CounterpartySummary):
    """A row of the Contrapartes list (plan 4c C3): the summary plus masked values.

    ``initials`` and the ``*_masked`` tails are computed by decrypting for processing
    (D6), never shown whole; ``initials`` is empty for a counterparty without a name.
    """

    initials: str
    # "****1234": the last four characters of the account as stored.
    account_masked: str | None
    wallet_masked: str | None
    identifier_masked: str | None
    identifier_type: str | None
    screened_at: datetime | None
    subject_id: UUID | None
    first_seen_at: datetime
    last_seen_at: datetime


class CounterpartyDetail(CounterpartyResponse):
    """A counterparty with its findings (newest first) and its last ten runs."""

    findings: list[MatchSummary]
    runs: list[RunResponse]
