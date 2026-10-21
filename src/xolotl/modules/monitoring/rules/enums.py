"""Vocabularies of the rule tables (SP-04 spec §5.2 4b block, §9.4; migration 0013).

Every value equals its name and is stored UPPERCASE in a VARCHAR(32) column, so a
member added later needs no migration. A definition's own vocabulary (family, kind,
severity, outcome) lives with the pure definitions.
"""

from enum import StrEnum


class RuleVersionStatus(StrEnum):
    """The D19 lifecycle of a tenant rule version."""

    DRAFT = "DRAFT"
    SIMULATED = "SIMULATED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class SimulationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class HitTransactionRole(StrEnum):
    """Why a transaction is linked to a hit (spec §9.6)."""

    TRIGGER = "TRIGGER"
    WINDOW = "WINDOW"
    COOLDOWN = "COOLDOWN"


class RuleAction(StrEnum):
    EDIT = "EDIT"
    SIMULATE = "SIMULATE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ACTIVATE = "ACTIVATE"
    RETIRE = "RETIRE"


class RuleBlockedReason(StrEnum):
    """Why a lifecycle action is refused, in the order they are reported."""

    STATE = "STATE"
    ROLE_REQUIRED = "ROLE_REQUIRED"
    NOT_AUTHOR = "NOT_AUTHOR"
    SAME_AUTHOR = "SAME_AUTHOR"
    SIMULATION_MISSING = "SIMULATION_MISSING"
    SIMULATION_STALE = "SIMULATION_STALE"
    PARAM_OUT_OF_BOUNDS = "PARAM_OUT_OF_BOUNDS"


class NoticeSource(StrEnum):
    """The path that published a tier-limit notice first (spec §12)."""

    DECIDE = "DECIDE"
    PROCESS = "PROCESS"
