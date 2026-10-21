"""Errors of the monitoring module with stable codes (SP-04 spec §6, §15)."""

from collections.abc import Sequence
from typing import ClassVar

from xolotl.modules.monitoring.txn.schema import FieldError, FieldErrorCode
from xolotl.platform.errors import (
    DomainError,
    ErrorItem,
    ValidationFailedError,
    XolotlError,
)

# One fixed sentence per code: an error item never carries a submitted value.
FIELD_ERROR_MESSAGES: dict[FieldErrorCode, str] = {
    FieldErrorCode.REQUIRED: "the field is required",
    FieldErrorCode.INVALID: "the value is not valid",
    FieldErrorCode.OUT_OF_RANGE: "the value is out of range",
    FieldErrorCode.CUSTOMER_REF_UNKNOWN: "no active customer has this reference",
    FieldErrorCode.ACCOUNT_REF_CONFLICT: (
        "the account belongs to another customer relationship"
    ),
    FieldErrorCode.DUPLICATE_IN_BATCH: "the external_id repeats an earlier item",
    FieldErrorCode.FUTURE: "the time is too far in the future",
    FieldErrorCode.NAIVE_DATETIME: "the time must carry a time zone",
    FieldErrorCode.CONTEXT_REQUIRED: "the instrument or rail needs this context",
    FieldErrorCode.COUNTERPARTY_EMPTY: (
        "the counterparty needs a name, account, wallet or identifier"
    ),
    FieldErrorCode.TOO_LONG: "the value is too long",
}


def field_error_items(errors: Sequence[FieldError]) -> list[ErrorItem]:
    """Locate each field error as ``("transactions", index, field)``.

    Returns:
        One problem item per error, in the given order.

    """
    return [
        ErrorItem(
            loc=("transactions", error.index, error.field),
            msg=FIELD_ERROR_MESSAGES[error.code],
            type=error.code.value,
        )
        for error in errors
    ]


class BatchInvalidError(ValidationFailedError):
    """A batch has items that fail; ``errors`` locates each, never its value."""

    default_code: ClassVar[str] = "TRANSACTION_BATCH_INVALID"
    default_detail: ClassVar[str] = "the batch has items that do not validate"


class BatchTooLargeError(XolotlError):
    """A batch carries more items than ``MONITORING_BATCH_MAX_ITEMS``."""

    status: ClassVar[int] = 413
    default_code: ClassVar[str] = "TRANSACTION_BATCH_TOO_LARGE"
    default_detail: ClassVar[str] = "the batch carries too many items"


class ExternalIdConflictError(DomainError):
    """A known ``external_id`` was re-sent with other content; the detail names it.

    The detail is ``transactions[<index>]``, the item's position only.
    """

    default_code: ClassVar[str] = "EXTERNAL_ID_CONFLICT"
    default_detail: ClassVar[str] = "an external_id was re-sent with other content"


class FileTooLargeError(XolotlError):
    """A file is over ``MONITORING_FILE_MAX_MB`` or ``MONITORING_FILE_MAX_ROWS``."""

    status: ClassVar[int] = 413
    default_code: ClassVar[str] = "TRANSACTION_FILE_TOO_LARGE"
    default_detail: ClassVar[str] = "the file is too large"


class FileInvalidError(ValidationFailedError):
    """A file cannot be read, has no header or lacks a required column."""

    default_code: ClassVar[str] = "TRANSACTION_FILE_INVALID"
    default_detail: ClassVar[str] = "the file cannot be read as transactions"


class BackfillRangeInvalidError(ValidationFailedError):
    """A backfill range starts after it ends, or ends after today."""

    default_code: ClassVar[str] = "BACKFILL_RANGE_INVALID"
    default_detail: ClassVar[str] = "the backfill range is not valid"
