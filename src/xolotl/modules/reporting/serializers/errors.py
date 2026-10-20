"""Errors raised while normalizing or serializing a report."""

from typing import ClassVar

from xolotl.platform.errors import ValidationFailedError


class ReportFieldError(ValidationFailedError):
    """A value cannot be put in its field: wrong shape, out of range, missing."""

    default_code: ClassVar[str] = "REPORT_FIELD_INVALID"
    default_detail: ClassVar[str] = "a report field is invalid"

    def __init__(self, field: str, reason: str) -> None:
        self.field: str = field
        super().__init__(f"{field}: {reason}")


class ZeroReportNotAllowedError(ValidationFailedError):
    """The definition has no zero report and the input holds no item."""

    default_code: ClassVar[str] = "ZERO_REPORT_NOT_ALLOWED"
    default_detail: ClassVar[str] = "this report kind has no empty report"

    def __init__(self, code: str) -> None:
        super().__init__(f"{code} has no empty report")


class CorrectionUnsupportedError(ValidationFailedError):
    """The definition has no correction mechanism (Formato 41, D14)."""

    default_code: ClassVar[str] = "CORRECTION_UNSUPPORTED"
    default_detail: ClassVar[str] = "this report kind has no correction mechanism"

    def __init__(self, code: str) -> None:
        super().__init__(f"{code} has no correction mechanism")
