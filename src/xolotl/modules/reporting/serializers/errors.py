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
