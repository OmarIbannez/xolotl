"""Kernel enums. Values are UPPERCASE and stored as String(32) columns."""

from enum import StrEnum


class ActorType(StrEnum):
    STAFF = "STAFF"
    API_CLIENT = "API_CLIENT"
    SYSTEM = "SYSTEM"
    PLATFORM_OPERATOR = "PLATFORM_OPERATOR"


class Role(StrEnum):
    ANALYST = "ANALYST"
    SENIOR_ANALYST = "SENIOR_ANALYST"
    MLRO = "MLRO"
    AUDITOR_RO = "AUDITOR_RO"
    ADMIN = "ADMIN"


class TenantStatus(StrEnum):
    ONBOARDING = "ONBOARDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    OFFBOARDED = "OFFBOARDED"


class TenantEnv(StrEnum):
    SANDBOX = "SANDBOX"
    PROD = "PROD"


class StaffStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ApiKeyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    DEAD = "DEAD"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    DEAD = "DEAD"


class EndpointStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    DEAD = "DEAD"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    DONE = "DONE"
    FAILED = "FAILED"


class StepStatus(StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class KeyPurpose(StrEnum):
    DATA = "DATA"
    INDEX = "INDEX"
    BIOMETRIC = "BIOMETRIC"


class Confidence(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class HolidayScope(StrEnum):
    NATIONAL = "NATIONAL"
    CNBV = "CNBV"
    SAT = "SAT"
    BANXICO = "BANXICO"


class BranchStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class ConnectorMode(StrEnum):
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


class CheckType(StrEnum):
    UNIT_VALUE = "UNIT_VALUE"
    RFC_VALIDATION = "RFC_VALIDATION"
    CURP_VALIDATION = "CURP_VALIDATION"


class ConnectorRoute(StrEnum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"
    MOCK = "MOCK"


class CallOutcome(StrEnum):
    PASS = "PASS"  # ruff: ignore[hardcoded-password-string] -- a check outcome, not a password
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
