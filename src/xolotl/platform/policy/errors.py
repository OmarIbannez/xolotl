"""Errors of the policy packs and the tighten-only overrides (spec §5)."""

from typing import ClassVar

from xolotl.platform.errors import DomainError, NotFoundError, ValidationFailedError


class PackValidationError(ValidationFailedError):
    default_code: ClassVar[str] = "PACK_INVALID"
    default_detail: ClassVar[str] = "the pack file is invalid"


class PackImmutableError(DomainError):
    default_code: ClassVar[str] = "PACK_VERSION_IMMUTABLE"
    default_detail: ClassVar[str] = (
        "a loaded pack version cannot change; bump the version"
    )


class PolicyLooseningError(DomainError):
    default_code: ClassVar[str] = "POLICY_LOOSENING"
    default_detail: ClassVar[str] = "an override may only tighten the effective policy"


class PolicyNotAssignedError(NotFoundError):
    default_code: ClassVar[str] = "POLICY_NOT_ASSIGNED"
    default_detail: ClassVar[str] = "no policy pack assigned for that date and product"
