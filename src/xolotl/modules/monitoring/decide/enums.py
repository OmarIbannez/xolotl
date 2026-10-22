"""Vocabularies of the decide path (SP-04 spec §12.1; migration 0014).

Every value equals its name and is stored UPPERCASE in a VARCHAR(32) column.
"""

from enum import StrEnum


class FailMode(StrEnum):
    """What a rail answers when a decision cannot be evaluated in time.

    ``FAIL_OPEN`` answers ``REVIEW``, ``FAIL_CLOSED`` answers ``BLOCK``.
    """

    FAIL_OPEN = "FAIL_OPEN"
    FAIL_CLOSED = "FAIL_CLOSED"


class DecisionOutcome(StrEnum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class DecisionMode(StrEnum):
    """How a decision was reached: evaluated, or the rail's fail mode."""

    EVALUATED = "EVALUATED"
    FAIL_OPEN = "FAIL_OPEN"
    FAIL_CLOSED = "FAIL_CLOSED"
