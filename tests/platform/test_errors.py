import pytest

from xolotl.modules.party.errors import IdentifierExistsError, IdentifierInvalidError
from xolotl.platform.connectors import NoRouteError
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
from xolotl.platform.events.registry import EventTypeNotDeliverableError
from xolotl.platform.policy.errors import (
    PackImmutableError,
    PackValidationError,
    PolicyLooseningError,
    PolicyNotAssignedError,
)
from xolotl.platform.units.errors import (
    UnitSourceError,
    UnitValueMissingError,
    UnverifiedValueError,
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
        (EventTypeNotDeliverableError, 422, "EVENT_TYPE_NOT_DELIVERABLE"),
        (UnitValueMissingError, 503, "UNIT_VALUE_MISSING"),
        (UnitSourceError, 503, "UNIT_SOURCE_UNAVAILABLE"),
        (UnverifiedValueError, 422, "UNVERIFIED_VALUE"),
        (NoRouteError, 409, "CONNECTOR_NO_ROUTE"),
        (PackValidationError, 422, "PACK_INVALID"),
        (PackImmutableError, 409, "PACK_VERSION_IMMUTABLE"),
        (PolicyLooseningError, 409, "POLICY_LOOSENING"),
        (PolicyNotAssignedError, 404, "POLICY_NOT_ASSIGNED"),
        (IdentifierExistsError, 409, "IDENTIFIER_EXISTS"),
        (IdentifierInvalidError, 422, "IDENTIFIER_INVALID"),
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
