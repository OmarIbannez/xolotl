import pytest

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import (
    ContextMissingError,
    DomainError,
    ForbiddenError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyRequiredError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
    XolotlError,
)


@pytest.mark.parametrize(
    ("cls", "status", "code"),
    [
        (XolotlError, 500, "INTERNAL"),
        (DomainError, 409, "CONFLICT"),
        (NotFoundError, 404, "NOT_FOUND"),
        (ForbiddenError, 403, "FORBIDDEN"),
        (UnauthorizedError, 401, "UNAUTHENTICATED"),
        (ContextMissingError, 500, "CONTEXT_MISSING"),
        (IdempotencyKeyRequiredError, 400, "IDEMPOTENCY_KEY_REQUIRED"),
        (IdempotencyConflictError, 422, "IDEMPOTENCY_KEY_REUSED"),
        (IdempotencyInProgressError, 409, "IDEMPOTENCY_IN_PROGRESS"),
        (ValidationFailedError, 422, "VALIDATION_FAILED"),
    ],
)
def test_error_status_and_default_code(cls, status, code):
    exc = cls()
    assert exc.status == status
    assert exc.code == code
    assert str(exc) == exc.detail
    assert exc.detail


def test_error_code_and_detail_can_be_overridden():
    exc = ForbiddenError("scope tenants:read required", code="SCOPE_MISSING")
    assert exc.code == "SCOPE_MISSING"
    assert exc.detail == "scope tenants:read required"
    assert exc.status == 403


def test_enum_values_are_uppercase_strings():
    assert ActorType.PLATFORM_OPERATOR == "PLATFORM_OPERATOR"
    assert Role.AUDITOR_RO.value == "AUDITOR_RO"
    assert {r.value for r in Role} == {
        "ANALYST",
        "SENIOR_ANALYST",
        "MLRO",
        "AUDITOR_RO",
        "ADMIN",
    }
