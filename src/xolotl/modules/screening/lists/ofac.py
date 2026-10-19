"""``OFAC_SDN`` format detection: enhanced XML, or a zip of XML or legacy CSV.

The first bytes decide: ``<`` (after an optional byte-order mark and whitespace)
is the enhanced XML; the zip magic ``PK`` is either OFAC's ``SDN_ENHANCED.ZIP``
(an ``.xml`` member) or a zip holding ``sdn.csv`` and optionally ``alt.csv``.
Anything else is refused.
"""

import io
import zipfile
from typing import BinaryIO

from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.ofac_csv import decode, parse_ofac_csv
from xolotl.modules.screening.lists.ofac_xml import parse_ofac_xml
from xolotl.modules.screening.lists.types import ListCode, ParsedList

_WHERE = f"{ListCode.OFAC_SDN}"
_BOM = b"\xef\xbb\xbf"
_ZIP_MAGIC = b"PK"
_SNIFF_BYTES = 512
# Refuse a zip member that would expand beyond this (the XML is about 110 MB).
_MAX_MEMBER_BYTES = 512 * 1024 * 1024


def parse_ofac(data: BinaryIO) -> ParsedList:
    """Parse an OFAC SDN file in whichever supported format it is.

    Returns:
        The parsed list.

    Raises:
        ListFormatError: the format is not recognised, the zip lacks ``sdn.csv``
            and an XML member, a member is too large, or the file is malformed.

    """
    stream = data if data.seekable() else io.BytesIO(data.read())
    head = stream.read(_SNIFF_BYTES)
    _ = stream.seek(0)
    if head.removeprefix(_BOM).lstrip().startswith(b"<"):
        return parse_ofac_xml(stream)
    if head.startswith(_ZIP_MAGIC):
        return _parse_zip(stream)
    msg = f"{_WHERE}: not an SDN XML file or zip"
    raise ListFormatError(msg)


def _parse_zip(stream: BinaryIO) -> ParsedList:
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        msg = f"{_WHERE}: corrupt zip ({exc})"
        raise ListFormatError(msg) from exc
    with archive:
        members = {
            info.filename.rsplit("/", 1)[-1].lower(): info
            for info in archive.infolist()
        }
        for info in members.values():
            if info.file_size > _MAX_MEMBER_BYTES:
                msg = f"{_WHERE}: zip member {info.filename!r} is too large"
                raise ListFormatError(msg)
        xml = next((i for n, i in members.items() if n.endswith(".xml")), None)
        if xml is not None:
            with archive.open(xml) as member:
                return parse_ofac_xml(member)
        sdn = members.get("sdn.csv")
        if sdn is None:
            msg = f"{_WHERE}: zip has neither an XML member nor sdn.csv"
            raise ListFormatError(msg)
        alt = members.get("alt.csv")
        return parse_ofac_csv(
            decode(archive.read(sdn)),
            decode(archive.read(alt)) if alt is not None else None,
        )
