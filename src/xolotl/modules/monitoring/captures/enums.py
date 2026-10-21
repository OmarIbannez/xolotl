"""Vocabularies of the capture tables (SP-04 spec §5.2 4b block, §10; migration 0013).

Every value equals its name and is stored UPPERCASE in a VARCHAR(32) column.
"""

from enum import StrEnum


class CaptureKind(StrEnum):
    THRESHOLD = "THRESHOLD"
    ACCUMULATION = "ACCUMULATION"
    FEE_THRESHOLD = "FEE_THRESHOLD"


class CaptureGroupStatus(StrEnum):
    OPEN = "OPEN"
    CROSSED = "CROSSED"
    SUPERSEDED = "SUPERSEDED"


class ReportableEventStatus(StrEnum):
    """``INCLUDED`` is set by reporting (SP-05) when a report takes the event."""

    CAPTURED = "CAPTURED"
    INCLUDED = "INCLUDED"
    VOIDED = "VOIDED"
    SUPERSEDED = "SUPERSEDED"


class CapturePeriodStatus(StrEnum):
    """``REOPENED`` marks a late capture after the close (spec §10.5)."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"
