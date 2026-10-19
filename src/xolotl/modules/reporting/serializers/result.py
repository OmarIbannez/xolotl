"""What every serializer returns."""

from dataclasses import dataclass

from xolotl.platform.enums import Confidence


@dataclass(frozen=True, slots=True)
class SerializerWarning:
    """A change the serializer made that the approver should see.

    ``line`` and ``column`` locate it in a delimited file; ``column`` is 0 for
    formats without columns.
    """

    code: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class SerializedReport:
    """The bytes of a report, split into parts when the format caps file size.

    ``confidence`` is ``UNVERIFIED`` while any part of the format (encoding,
    record shape) has not been confirmed by a regulator test upload.
    """

    code: str
    version: int
    parts: tuple[bytes, ...]
    record_count: int
    warnings: tuple[SerializerWarning, ...]
    confidence: Confidence
