"""Errors of the unit service and its sources (spec §6)."""

from typing import ClassVar

from xolotl.platform.errors import ValidationFailedError, XolotlError


class UnitValueMissingError(XolotlError):
    status: ClassVar[int] = 503
    default_code: ClassVar[str] = "UNIT_VALUE_MISSING"
    default_detail: ClassVar[str] = "no unit value is available for that date"


class UnitSourceError(XolotlError):
    status: ClassVar[int] = 503
    default_code: ClassVar[str] = "UNIT_SOURCE_UNAVAILABLE"
    default_detail: ClassVar[str] = "the unit value source is unavailable"


class UnverifiedValueError(ValidationFailedError):
    default_code: ClassVar[str] = "UNVERIFIED_VALUE"
    default_detail: ClassVar[str] = "the file holds UNVERIFIED values"


class UnitSourceAuthError(UnitSourceError):
    """The source rejected the API token: a permanent failure, not a transient one."""

    default_detail: ClassVar[str] = "the unit value source rejected the API token"
