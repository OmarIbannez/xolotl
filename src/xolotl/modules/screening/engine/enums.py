"""Vocabularies of the screening engine and review (SP-03 spec §5.2, migration 0010).

Every value equals its name and is stored UPPERCASE as text, so a member reserved for
a later sub-project (``RunMode.ONBOARDING``) is added without a migration, as SP-04's
``SubjectKind.COUNTERPARTY`` and ``RunMode.COUNTERPARTY`` were (plan 4c, C1).
"""

from enum import StrEnum


class SubjectKind(StrEnum):
    PARTY = "PARTY"
    AD_HOC = "AD_HOC"
    COUNTERPARTY = "COUNTERPARTY"


class SubjectStatus(StrEnum):
    CLEAR = "CLEAR"
    PENDING_REVIEW = "PENDING_REVIEW"
    TRUE_MATCH = "TRUE_MATCH"


class RunMode(StrEnum):
    AD_HOC = "AD_HOC"
    NEW_SUBJECT = "NEW_SUBJECT"
    DATA_CHANGE = "DATA_CHANGE"
    LIST_DELTA = "LIST_DELTA"
    PRIVATE_LIST = "PRIVATE_LIST"
    PERIODIC = "PERIODIC"
    REINDEX = "REINDEX"
    # A counterparty's first-sight screen (SP-04 spec §11.2).
    COUNTERPARTY = "COUNTERPARTY"


class TriggerType(StrEnum):
    API = "API"
    CONSOLE = "CONSOLE"
    EVENT = "EVENT"
    JOB = "JOB"
    CLI = "CLI"


class NameKind(StrEnum):
    LEGAL = "LEGAL"
    TRADE = "TRADE"


class MatchStatus(StrEnum):
    OPEN = "OPEN"
    ESCALATED = "ESCALATED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    TRUE_MATCH = "TRUE_MATCH"
    DELISTED = "DELISTED"
    SUPERSEDED = "SUPERSEDED"


class HitBand(StrEnum):
    ALERT = "ALERT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AUTO_DISCARDED = "AUTO_DISCARDED"
    WHITELISTED = "WHITELISTED"


class AutoReason(StrEnum):
    ID_CONFLICT_CAP = "ID_CONFLICT_CAP"
    WHITELIST = "WHITELIST"


class SuggestedDisposition(StrEnum):
    DELISTED = "DELISTED"


class Decision(StrEnum):
    FALSE_POSITIVE = "FALSE_POSITIVE"
    POSSIBLE_TRUE_MATCH = "POSSIBLE_TRUE_MATCH"
    TRUE_MATCH = "TRUE_MATCH"
    DELISTED = "DELISTED"
    SUPERSEDED = "SUPERSEDED"


class DecidedByType(StrEnum):
    STAFF = "STAFF"
    SYSTEM = "SYSTEM"


class BlockedReason(StrEnum):
    STATE = "STATE"
    ROLE_REQUIRED = "ROLE_REQUIRED"
    SAME_REVIEWER = "SAME_REVIEWER"
    NOT_DELISTED = "NOT_DELISTED"


class BatchKind(StrEnum):
    LIST_DELTA = "LIST_DELTA"
    PRIVATE_LIST = "PRIVATE_LIST"
    PERIODIC = "PERIODIC"
    REINDEX = "REINDEX"


class BatchStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# A finding in one of these states still needs a decision.
OPEN_MATCH_STATUSES: frozenset[MatchStatus] = frozenset({
    MatchStatus.OPEN,
    MatchStatus.ESCALATED,
})
