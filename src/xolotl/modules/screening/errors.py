"""Errors of the screening module with stable codes (SP-03 spec §12)."""

from collections.abc import Sequence
from typing import ClassVar

from xolotl.modules.screening.engine.enums import BlockedReason
from xolotl.platform.errors import DomainError, ValidationFailedError, XolotlError


class ListMassRemovalError(DomainError):
    """A new list version would remove more entries than the guard allows.

    The detail is ``removed <r> of <n>``; ``xolotl lists load-file --accept-drop``
    is the only way past it.
    """

    default_code: ClassVar[str] = "LIST_MASS_REMOVAL"
    default_detail: ClassVar[str] = "the new version removes too many entries"


class ListMandatoryError(DomainError):
    """A mandatory list subscription cannot be disabled."""

    default_code: ClassVar[str] = "LIST_MANDATORY"
    default_detail: ClassVar[str] = "a mandatory list cannot be disabled"


class PrivateListInvalidError(ValidationFailedError):
    """A private-list upload has bad rows; ``errors`` locates each, never its value."""

    default_code: ClassVar[str] = "PRIVATE_LIST_INVALID"
    default_detail: ClassVar[str] = "the file has rows that do not follow the template"


class PrivateListExistsError(DomainError):
    """The tenant already has a private list with that code."""

    default_code: ClassVar[str] = "PRIVATE_LIST_EXISTS"
    default_detail: ClassVar[str] = "a private list with this code already exists"


class DispositionNotAllowedError(DomainError):
    """The current user may not take this decision on this finding now.

    ``blocked_reasons`` tells why, in the order the action matrix checks them, and
    travels on the problem body (Deviation 10).
    """

    default_code: ClassVar[str] = "DISPOSITION_NOT_ALLOWED"
    default_detail: ClassVar[str] = "this decision is not allowed"

    def __init__(self, reasons: Sequence[BlockedReason] = ()) -> None:
        super().__init__()
        self.blocked_reasons: tuple[str, ...] = tuple(r.value for r in reasons)


class ScreeningIndexUnavailableError(XolotlError):
    """The process has not loaded the screening index, or lacks a required version.

    Answered while the index warms up at start-up, and when a screen needs a list
    version that a forced refresh could not find. Retrying later succeeds.
    """

    status: ClassVar[int] = 503
    default_code: ClassVar[str] = "SCREENING_INDEX_UNAVAILABLE"
    default_detail: ClassVar[str] = "the screening index is loading"


class MatcherVersionMismatchError(DomainError):
    """A run cannot be replayed exactly by this process (NFR-SC-007).

    Its matcher version differs from the running ``MATCHER_VERSION``, or the scoring
    configuration rebuilt from its policy snapshot no longer has the stored hash.
    """

    default_code: ClassVar[str] = "MATCHER_VERSION_MISMATCH"
    default_detail: ClassVar[str] = "the run was scored by another matcher version"
