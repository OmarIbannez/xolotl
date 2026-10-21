"""Vocabularies of the monitoring tables (SP-04 spec §5.2, migration 0012).

Every value equals its name and is stored UPPERCASE in a VARCHAR(32) column, so a
member added later needs no migration. The transaction's own vocabulary
(``Direction``, ``Rail``, ``Instrument``, ``Channel``, ``KeyKind``) lives in the pure
``monitoring.txn.schema``.
"""

from enum import StrEnum


class TransactionStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    PARKED = "PARKED"
    BACKFILL = "BACKFILL"


class BatchSource(StrEnum):
    API = "API"
    FILE = "FILE"
    CLI = "CLI"


class FileStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AccountKind(StrEnum):
    DEFAULT = "DEFAULT"
    EMONEY = "EMONEY"
    WALLET = "WALLET"
    CARD = "CARD"
    OTHER = "OTHER"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class ParkReason(StrEnum):
    UNIT_VALUE_MISSING = "UNIT_VALUE_MISSING"
    POLICY_NOT_ASSIGNED = "POLICY_NOT_ASSIGNED"


class BucketKind(StrEnum):
    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    ACCUM = "ACCUM"
