"""Streaming XML records with bounded memory and no entity expansion.

``defusedxml`` refuses DTDs, entity declarations and external references. Each
record element is cleared after the parser has handled it, so a 100 MB file never
sits in memory as a tree.
"""

from collections.abc import Iterator
from typing import BinaryIO, Protocol, cast

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, iterparse

from xolotl.modules.screening.lists.errors import ListFormatError


class Node(Protocol):
    """The part of an ElementTree element the parsers use.

    ``iterparse`` is typed as yielding ``Any``; this keeps the parsers typed without
    importing ``xml.etree`` (which only ``defusedxml`` may touch).
    """

    @property
    def tag(self) -> str: ...
    @property
    def text(self) -> str | None: ...
    @property
    def attrib(self) -> dict[str, str]: ...
    def find(self, path: str) -> "Node | None": ...
    def iterfind(self, path: str) -> Iterator["Node"]: ...
    def clear(self) -> None: ...


def local(tag: str) -> str:
    """Drop the ``{namespace}`` prefix of a tag.

    Returns:
        The local name.

    """
    return tag.rpartition("}")[2]


class RecordStream:
    """Iterate the elements named in ``record_tags`` as each one closes.

    ``root_attrib`` holds the root element's attributes once iteration has begun.
    ``where`` prefixes every error (source and parser version).
    """

    def __init__(self, data: BinaryIO, record_tags: frozenset[str], where: str) -> None:
        self._data: BinaryIO = data
        self._record_tags: frozenset[str] = record_tags
        self._where: str = where
        self.root_attrib: dict[str, str] = {}
        self.root_tag: str = ""

    def __iter__(self) -> Iterator[Node]:
        """Yield each record element, then clear it.

        Yields:
            Record elements in document order.

        Raises:
            ListFormatError: the document is not well-formed or uses forbidden
                constructs.

        """
        try:
            yield from self._records()
        except ParseError as exc:
            line, column = exc.position
            msg = f"{self._where}: malformed XML at line {line}, column {column}"
            raise ListFormatError(msg) from exc
        except DefusedXmlException as exc:
            msg = f"{self._where}: forbidden XML construct ({type(exc).__name__})"
            raise ListFormatError(msg) from exc

    def _records(self) -> Iterator[Node]:
        events = cast(
            "Iterator[tuple[str, Node]]",
            iterparse(self._data, events=("start", "end"), forbid_dtd=True),
        )
        for event, element in events:
            if event == "start" and not self.root_tag:
                self.root_tag = local(element.tag)
                self.root_attrib = dict(element.attrib)
            elif event == "end" and local(element.tag) in self._record_tags:
                yield element
                element.clear()


def text(element: Node, path: str) -> str | None:
    """Read the trimmed text at ``path`` below ``element``.

    Returns:
        The text with whitespace collapsed, or ``None`` when absent or blank.

    """
    found = element.find(path)
    if found is None or found.text is None:
        return None
    collapsed = " ".join(found.text.split())
    return collapsed or None


def texts(element: Node, path: str) -> list[str]:
    """Read every non-blank text at ``path`` below ``element``.

    Returns:
        The texts in document order, whitespace collapsed.

    """
    collapsed = (
        " ".join((found.text or "").split()) for found in element.iterfind(path)
    )
    return [value for value in collapsed if value]
