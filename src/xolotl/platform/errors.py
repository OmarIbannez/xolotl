"""Errors the API layer maps to problem+json responses (spec §7)."""

from typing import ClassVar


class XolotlError(Exception):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "INTERNAL"
    default_detail: ClassVar[str] = "internal error"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        self.detail: str = detail if detail is not None else self.default_detail
        self.code: str = code or self.default_code
        super().__init__(self.detail)


class DomainError(XolotlError):
    status: ClassVar[int] = 409
    default_code: ClassVar[str] = "CONFLICT"
    default_detail: ClassVar[str] = "conflict"


class NotFoundError(XolotlError):
    status: ClassVar[int] = 404
    default_code: ClassVar[str] = "NOT_FOUND"
    default_detail: ClassVar[str] = "not found"


class ForbiddenError(XolotlError):
    status: ClassVar[int] = 403
    default_code: ClassVar[str] = "FORBIDDEN"
    default_detail: ClassVar[str] = "forbidden"


class UnauthorizedError(XolotlError):
    status: ClassVar[int] = 401
    default_code: ClassVar[str] = "UNAUTHENTICATED"
    default_detail: ClassVar[str] = "authentication required"


class ContextMissingError(XolotlError):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "CONTEXT_MISSING"
    default_detail: ClassVar[str] = "no tenant context is bound"


class IdempotencyKeyRequiredError(XolotlError):
    status: ClassVar[int] = 400
    default_code: ClassVar[str] = "IDEMPOTENCY_KEY_REQUIRED"
    default_detail: ClassVar[str] = (
        "Idempotency-Key header is required (1-128 characters)"
    )


class IdempotencyConflictError(XolotlError):
    status: ClassVar[int] = 422
    default_code: ClassVar[str] = "IDEMPOTENCY_KEY_REUSED"
    default_detail: ClassVar[str] = (
        "Idempotency-Key was already used with a different request"
    )


class IdempotencyInProgressError(XolotlError):
    status: ClassVar[int] = 409
    default_code: ClassVar[str] = "IDEMPOTENCY_IN_PROGRESS"
    default_detail: ClassVar[str] = "a request with this Idempotency-Key is in progress"


class ValidationFailedError(XolotlError):
    status: ClassVar[int] = 422
    default_code: ClassVar[str] = "VALIDATION_FAILED"
    default_detail: ClassVar[str] = "validation failed"
