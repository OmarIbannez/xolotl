"""Normalizers shared by the Formato 41 and aviso serializers (FR-RR-003).

Two character regimes exist. Formato 41 v1 is ASCII: upper case, diacritics
stripped, Ñ folded to N, and the column separator ``;`` never inside a value
(D14; ``encoding_confidence: UNVERIFIED`` until a SITI QA upload confirms it).
The aviso XSD allows Ñ but no accented vowel, so aviso text keeps Ñ and strips
the other diacritics. Amounts are ``Decimal`` only; dates and times are rendered
in ``America/Mexico_City``.
"""

import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from xolotl.modules.reporting.serializers.errors import ReportFieldError
from xolotl.platform.text import RFC_SHAPE

MX_TZ = ZoneInfo("America/Mexico_City")
F41_SEPARATOR = ";"
# Printable ASCII is 0x20 to 0x7E; the separator is excluded from values.
_PRINTABLE_LOW = 0x20
_PRINTABLE_HIGH = 0x7E
_SPACES = re.compile(r"\s+")
_IDENTIFIER_JUNK = re.compile(r"[^A-ZÑ&0-9]")
# Latin letters that have no Unicode decomposition to a base letter.
_UNDECOMPOSABLE = str.maketrans({
    "Ł": "L", "Ø": "O", "Æ": "AE", "Œ": "OE", "Đ": "D", "Ð": "D", "Þ": "TH",
    "Ħ": "H", "Ŧ": "T", "Ŀ": "L", "Ŋ": "N", "Ə": "E",
})  # fmt: skip
CURP_SHAPE = re.compile(r"^[A-Z]{4}\d{6}[HMX][A-Z]{5}[A-Z\d]\d$")
CASFIM_KEY = re.compile(r"^[0-9]{1,2}-[0-9]{3}$")
# Six digits from a CASFIM key; eight for a CNBV-assigned centro cambiario or
# transmisor key (sector plus institution, column 5 "6/8").
_CASFIM_DIGITS = re.compile(r"^(?:[0-9]{6}|[0-9]{8})$")
_CASFIM_LENGTH = 6
_MAX_INTEGER_DIGITS = 14
_ENYE = "Ñ"
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def _strip_marks(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.translate(_UNDECOMPOSABLE))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _collapse(value: str) -> str:
    return _SPACES.sub(" ", value).strip()


def f41_text(value: str | None) -> str:
    """Bring free text to the Formato 41 v1 character set.

    Upper-cases, strips diacritics (so Ñ becomes N), turns the separator, control
    characters and anything outside printable ASCII into spaces, then collapses
    runs of spaces and trims.

    Returns:
        The cleaned text, ``""`` for ``None`` or blank input.

    """
    if value is None:
        return ""
    folded = _strip_marks(unicodedata.normalize("NFC", value).upper()).upper()
    kept = "".join(
        ch
        if _PRINTABLE_LOW <= ord(ch) <= _PRINTABLE_HIGH and ch != F41_SEPARATOR
        else " "
        for ch in folded
    )
    return _collapse(kept)


def aviso_text(value: str | None) -> str:
    """Bring free text to the aviso character set: upper case, Ñ kept.

    Every other diacritic is stripped, control and unassigned characters become
    spaces and whitespace is collapsed; the XSD patterns decide what else is
    allowed.

    Returns:
        The cleaned text, ``""`` for ``None`` or blank input.

    """
    if value is None:
        return ""
    upper = unicodedata.normalize("NFC", value).upper()
    kept = "".join(
        " "
        if unicodedata.category(ch) in _CONTROL_CATEGORIES
        else ch
        if ch == _ENYE
        else _strip_marks(ch)
        for ch in upper
    )
    return _collapse(kept.upper())


def fit(value: str, width: int) -> tuple[str, bool]:
    """Truncate ``value`` to ``width`` characters, as the Formato 41 layout says.

    A space left at the cut is dropped.

    Returns:
        The value cut to at most ``width`` and whether anything was cut.

    """
    if len(value) <= width:
        return value, False
    return value[:width].rstrip(), True


def compact_identifier(value: str) -> str:
    """Upper-case an identifier and drop separators, spaces and punctuation.

    Returns:
        Only ``A-Z``, ``Ñ``, ``&`` and digits, in their original order.

    """
    return _IDENTIFIER_JUNK.sub("", unicodedata.normalize("NFC", value).upper())


def rfc(value: str, *, field: str = "rfc") -> str:
    """Normalize an RFC (12 characters for companies, 13 for people).

    Returns:
        The compact RFC.

    Raises:
        ReportFieldError: the value does not have the RFC shape.

    """
    compact = compact_identifier(value)
    if not RFC_SHAPE.fullmatch(compact):
        raise ReportFieldError(field, "not an RFC")
    return compact


def curp(value: str, *, field: str = "curp") -> str:
    """Normalize a CURP (18 characters).

    Returns:
        The compact CURP.

    Raises:
        ReportFieldError: the value does not have the CURP shape.

    """
    compact = compact_identifier(value)
    if not CURP_SHAPE.fullmatch(compact):
        raise ReportFieldError(field, "not a CURP")
    return compact


def amount(value: Decimal, *, field: str = "amount", places: int = 2) -> str:
    """Render a non-negative amount with exactly ``places`` decimals, half up.

    Returns:
        The amount as plain digits, a point and the decimals (``"1234.50"``).

    Raises:
        ReportFieldError: the amount is negative, not finite, or has more than
            14 integer digits.

    """
    if not value.is_finite() or value < 0:
        raise ReportFieldError(field, "must be a finite non-negative amount")
    # Checked before rounding: quantizing a huge value overflows the context.
    if not value.is_zero() and value.adjusted() >= _MAX_INTEGER_DIGITS:
        raise ReportFieldError(field, "more than 14 integer digits")
    quantized = abs(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{quantized:f}"
    if len(text.split(".", maxsplit=1)[0]) > _MAX_INTEGER_DIGITS:
        raise ReportFieldError(field, "more than 14 integer digits")
    return text


def quantity(
    value: Decimal,
    *,
    field: str = "quantity",
    min_places: int = 2,
    max_places: int = 10,
) -> str:
    """Render a non-negative quantity with between ``min_places`` and ``max_places``.

    The value is rounded half up to ``max_places`` and trailing zeros beyond
    ``min_places`` are dropped. Range checks are :func:`amount`'s.

    Returns:
        The quantity as plain digits, a point and the decimals.

    """
    text = amount(value, field=field, places=max_places)
    whole, decimals = text.split(".")
    decimals = decimals.rstrip("0").ljust(min_places, "0")
    return f"{whole}.{decimals}"


def yyyymm(value: date) -> str:
    """Render a period month.

    Returns:
        ``AAAAMM``.

    """
    return f"{value.year:04d}{value.month:02d}"


def yyyymmdd(value: date) -> str:
    """Render a calendar date.

    A ``datetime`` is refused: its calendar day depends on the time zone, so the
    caller converts it to a Mexico City date first.

    Returns:
        ``AAAAMMDD``.

    Raises:
        TypeError: ``value`` is a ``datetime``.

    """
    if isinstance(value, datetime):
        msg = "pass a date, not a datetime"
        raise TypeError(msg)
    return f"{value.year:04d}{value.month:02d}{value.day:02d}"


def mx_datetime(value: datetime, *, field: str = "datetime") -> str:
    """Render an aware instant as Mexico City wall-clock time.

    Returns:
        ``AAAAMMDDhhmmss`` in ``America/Mexico_City``.

    Raises:
        ReportFieldError: the datetime has no time zone or cannot be expressed
            in Mexico City time.

    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReportFieldError(field, "needs a time zone")
    try:
        local = value.astimezone(MX_TZ)
    except OverflowError as exc:
        raise ReportFieldError(field, "out of range") from exc
    return local.strftime("%Y%m%d%H%M%S")


def casfim_key(value: str, *, field: str = "casfim") -> str:
    """Turn a CASFIM key such as ``40-002`` into the digits Formato 41 wants.

    The hyphen is dropped and the key is left-padded with zeros, so ``40-002``
    becomes ``040002``. A key already in six-digit form, or the eight-digit form
    the CNBV assigns to centros cambiarios and transmisores, is returned
    unchanged. Only ASCII digits are accepted.

    Returns:
        Six or eight digits.

    Raises:
        ReportFieldError: the value is not ``N-NNN``/``NN-NNN`` nor six or eight
            ASCII digits.

    """
    stripped = value.strip()
    if CASFIM_KEY.fullmatch(stripped):
        return stripped.replace("-", "").rjust(_CASFIM_LENGTH, "0")
    if _CASFIM_DIGITS.fullmatch(stripped):
        return stripped
    raise ReportFieldError(field, "not a CASFIM key")
