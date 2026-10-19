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
