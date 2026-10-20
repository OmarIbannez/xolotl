"""Vocabularies of the list catalog (SP-03 spec §5), stored UPPERCASE as text."""

from enum import StrEnum


class ListKind(StrEnum):
    SANCTION = "SANCTION"
    FISCAL = "FISCAL"
    BLOCKED = "BLOCKED"
    PEP = "PEP"
    INTERNAL = "INTERNAL"


class SourceFormat(StrEnum):
    UN_XML = "UN_XML"
    OFAC_ENHANCED_XML = "OFAC_ENHANCED_XML"
    EU_FSF_XML = "EU_FSF_XML"
    UK_SL_XML = "UK_SL_XML"
    SAT_69B_CSV = "SAT_69B_CSV"


class VersionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class DeltaChange(StrEnum):
    ADDED = "ADDED"
    CHANGED = "CHANGED"
    REMOVED = "REMOVED"


class ListRequirement(StrEnum):
    MANDATORY = "MANDATORY"
    RECOMMENDED = "RECOMMENDED"
