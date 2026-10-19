"""Structural validation of national identifiers (program spec D22).

The algorithms are copied from sentinel's ``rules/identity.py`` (CURP, CPF, cédula,
NIT) and Xolo's ``validators.py`` (RFC with its homoclave check digit, CLABE with the
bank catalogue); CNPJ, CUIT, RUT, INE, DNI and the passport MRZ check are written here.
Each check catches typos and crude fabrications; none proves the document exists.

``validate`` normalises the raw input, then returns the canonical value (what later
code encrypts and blind-indexes) with its derived attributes, or the first reason the
input fails. A failure never carries the value.
"""

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from xolotl.modules.party.enums import IdentifierType
from xolotl.platform import clock


class IdentifierReason(StrEnum):
    FORMAT = "FORMAT"
    CHECK_DIGIT = "CHECK_DIGIT"
    DATE = "DATE"
    STATE_CODE = "STATE_CODE"
    PREFIX = "PREFIX"
    REPEATED_DIGITS = "REPEATED_DIGITS"
    BIRTH_DATE_MISMATCH = "BIRTH_DATE_MISMATCH"


@dataclass(frozen=True, slots=True)
class NormalizedIdentifier:
    id_type: IdentifierType
    value: str
    attributes: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class IdentifierError:
    id_type: IdentifierType
    reason: IdentifierReason


type _Result = NormalizedIdentifier | IdentifierError

# ---- CURP (sentinel) ---------------------------------------------------------------

_CURP_RE = re.compile(
    r"""
    ^[A-Z][AEIOUX][A-Z]{2}                    # initials from the name
    (?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})   # date of birth
    (?P<sex>[HMX])                            # sex
    (?P<state>[A-Z]{2})                       # state of birth
    [B-DF-HJ-NP-TV-Z]{3}                      # inner consonants of the name
    (?P<century>[A-Z\d])                      # digit before 2000, letter after
    \d$                                       # check digit
    """,
    re.VERBOSE | re.ASCII,
)
CURP_STATES = frozenset({
    "AS", "BC", "BS", "CC", "CL", "CM", "CS", "CH", "DF", "DG", "GT", "GR", "HG",
    "JC", "MC", "MN", "MS", "NT", "NL", "OC", "PL", "QT", "QR", "SP", "SL", "SR",
    "TC", "TS", "TL", "VZ", "YN", "ZS", "NE",
})  # fmt: skip
_CURP_ALPHABET = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
_CURP_LENGTH = 18
_CURP_BODY_LEN = 17
_Y2K = 2000
_CENTURY = 100
_TEN = 10


def _two_digit_year(yy: str, *, born_2000s: bool) -> int:
    return (_Y2K if born_2000s else _Y2K - _CENTURY) + int(yy)


def _embedded_dob(yy: str, mm: str, dd: str, *, born_2000s: bool) -> date | None:
    try:
        return date(_two_digit_year(yy, born_2000s=born_2000s), int(mm), int(dd))
    except ValueError:
        return None


def curp_check_digit(first_17: str) -> int:
    """Compute the CURP check digit (RENAPO's modulus 10 over a 37-symbol alphabet).

    Returns:
        The digit the eighteenth character must equal.

    """
    total = sum(
        _CURP_ALPHABET.index(ch) * (_CURP_LENGTH - i) for i, ch in enumerate(first_17)
    )
    return (_TEN - total % _TEN) % _TEN


def _check_curp(value: str, birth_date: date | None) -> _Result:
    match = _CURP_RE.fullmatch(value)
    if match is None:
        return _fail(IdentifierType.CURP, IdentifierReason.FORMAT)
    if match["state"] not in CURP_STATES:
        return _fail(IdentifierType.CURP, IdentifierReason.STATE_CODE)
    # Position 17 is a digit for people born before 2000 and a letter after.
    embedded = _embedded_dob(
        match["yy"], match["mm"], match["dd"], born_2000s=match["century"].isalpha()
    )
    if embedded is None:
        return _fail(IdentifierType.CURP, IdentifierReason.DATE)
    if int(value[-1]) != curp_check_digit(value[:_CURP_BODY_LEN]):
        return _fail(IdentifierType.CURP, IdentifierReason.CHECK_DIGIT)
    if birth_date is not None and embedded != birth_date:
        return _fail(IdentifierType.CURP, IdentifierReason.BIRTH_DATE_MISMATCH)
    return NormalizedIdentifier(
        IdentifierType.CURP,
        value,
        {
            "birth_date": embedded.isoformat(),
            "sex": match["sex"],
            "state": match["state"],
        },
    )


# ---- RFC (Xolo) --------------------------------------------------------------------

# 3 letters (company) or 4 (individual), YYMMDD, then a 3-char homoclave whose last
# char is the check digit.
_RFC_RE = re.compile(
    r"^([A-ZÑ&]{3,4})(\d{2})(\d{2})(\d{2})([A-Z\d]{2})([A\d])$", re.ASCII
)
# Value of each character in the RFC check-digit table (SAT).
_RFC_ALPHABET = "0123456789ABCDEFGHIJKLMN&OPQRSTUVWXYZ Ñ"
_RFC_WEIGHTED_LEN = 12
_COMPANY_PREFIX_LEN = 3
_MOD = 11
_GENERIC_RFCS: Mapping[str, str] = MappingProxyType({
    "XAXX010101000": "PUBLICO_EN_GENERAL",
    "XEXX010101000": "EXTRANJERO",
})


def rfc_check_digit(body: str) -> str:
    """Compute the RFC check character over the RFC without its last character.

    Returns:
        A digit, or ``"A"`` when the check value is ten.

    """
    padded = body.rjust(_RFC_WEIGHTED_LEN)  # companies are padded with a space
    total = sum(
        _RFC_ALPHABET.index(char) * (_RFC_WEIGHTED_LEN + 1 - i)
        for i, char in enumerate(padded)
    )
    digit = (_MOD - total % _MOD) % _MOD
    return "A" if digit == _TEN else str(digit)


def _registration_date(yy: str, mm: str, dd: str) -> date | None:
    year = int(yy)
    # Two-digit years: anything after this year's YY is last century.
    century = _Y2K if year <= clock.now().year % _CENTURY else _Y2K - _CENTURY
    try:
        return date(century + year, int(mm), int(dd))
    except ValueError:
        return None


def _check_rfc(value: str, birth_date: date | None) -> _Result:
    match = _RFC_RE.fullmatch(value)
    if match is None:
        return _fail(IdentifierType.RFC, IdentifierReason.FORMAT)
    letters, yy, mm, dd, _, check = match.groups()
    registered_on = _registration_date(yy, mm, dd)
    if registered_on is None:
        return _fail(IdentifierType.RFC, IdentifierReason.DATE)
    generic = _GENERIC_RFCS.get(value)
    if generic is None and rfc_check_digit(value[:-1]) != check:
        return _fail(IdentifierType.RFC, IdentifierReason.CHECK_DIGIT)
    is_company = len(letters) == _COMPANY_PREFIX_LEN
    if not is_company and birth_date is not None and registered_on != birth_date:
        return _fail(IdentifierType.RFC, IdentifierReason.BIRTH_DATE_MISMATCH)
    attributes = {
        "person_type": "MORAL" if is_company else "FISICA",
        "registered_on": registered_on.isoformat(),
    }
    if generic is not None:
        attributes["generic"] = generic
    return NormalizedIdentifier(IdentifierType.RFC, value, attributes)


# ---- CLABE (Xolo) ------------------------------------------------------------------

# CLABE: 3-digit bank code, 3-digit plaza, 11-digit account, 1 check digit.
_CLABE_RE = re.compile(r"^\d{18}$", re.ASCII)
_CLABE_WEIGHTS = (3, 7, 1)
_CLABE_BODY_LEN = 17
_BANK_CODE_LEN = 3
# Banxico institution codes for the banks and SPEI participants people meet most.
BANKS: Mapping[str, str] = MappingProxyType({
    "002": "BANAMEX",
    "006": "BANCOMEXT",
    "009": "BANOBRAS",
    "012": "BBVA MEXICO",
    "014": "SANTANDER",
    "019": "BANJERCITO",
    "021": "HSBC",
    "030": "BAJIO",
    "036": "INBURSA",
    "042": "MIFEL",
    "044": "SCOTIABANK",
    "058": "BANREGIO",
    "059": "INVEX",
    "060": "BANSI",
    "062": "AFIRME",
    "072": "BANORTE",
    "106": "BANK OF AMERICA",
    "127": "AZTECA",
    "130": "COMPARTAMOS",
    "137": "BANCOPPEL",
    "166": "BANCO DEL BIENESTAR",
    "638": "NU MEXICO",
    "646": "STP",
    "722": "MERCADO PAGO W",
})


def clabe_check_digit(first_17: str) -> str:
    """Compute the CLABE check digit (weights 3, 7, 1 modulus 10).

    Returns:
        The digit the eighteenth character must equal.

    """
    total = sum(
        int(digit) * _CLABE_WEIGHTS[i % len(_CLABE_WEIGHTS)] % _TEN
        for i, digit in enumerate(first_17)
    )
    return str((_TEN - total % _TEN) % _TEN)


def _check_clabe(value: str, _birth_date: date | None) -> _Result:
    if _CLABE_RE.fullmatch(value) is None:
        return _fail(IdentifierType.CLABE, IdentifierReason.FORMAT)
    if clabe_check_digit(value[:_CLABE_BODY_LEN]) != value[_CLABE_BODY_LEN]:
        return _fail(IdentifierType.CLABE, IdentifierReason.CHECK_DIGIT)
    bank_code = value[:_BANK_CODE_LEN]
    attributes = {"bank_code": bank_code}
    # An unknown bank code is still a valid CLABE.
    if (bank := BANKS.get(bank_code)) is not None:
        attributes["bank"] = bank
    return NormalizedIdentifier(IdentifierType.CLABE, value, attributes)


# ---- INE ---------------------------------------------------------------------------

_INE_CIC_RE = re.compile(r"^\d{9}$", re.ASCII)
_INE_OCR_RE = re.compile(r"^\d{13}$", re.ASCII)


def _check_ine(value: str, _birth_date: date | None) -> _Result:
    if _INE_CIC_RE.fullmatch(value) is not None:
        return NormalizedIdentifier(IdentifierType.INE, value, {"format": "CIC"})
    if _INE_OCR_RE.fullmatch(value) is not None:
        return NormalizedIdentifier(IdentifierType.INE, value, {"format": "OCR"})
    return _fail(IdentifierType.INE, IdentifierReason.FORMAT)


# ---- CPF (sentinel) ----------------------------------------------------------------

_CPF_RE = re.compile(r"^\d{11}$", re.ASCII)
_CPF_BODY_LEN = 9
_MODULUS_11 = 11


def cpf_check_digits(first_9: str) -> str:
    """Compute the two CPF check digits (Receita Federal's modulus 11).

    Returns:
        The two digits the CPF must end with.

    """
    digits = [int(d) for d in first_9]
    for _ in range(2):
        weight = len(digits) + 1
        total = sum(d * (weight - i) for i, d in enumerate(digits))
        digits.append(total * _TEN % _MODULUS_11 % _TEN)
    return f"{digits[-2]}{digits[-1]}"


def _check_cpf(value: str, _birth_date: date | None) -> _Result:
    if _CPF_RE.fullmatch(value) is None:
        return _fail(IdentifierType.CPF, IdentifierReason.FORMAT)
    if len(set(value)) == 1:
        return _fail(IdentifierType.CPF, IdentifierReason.REPEATED_DIGITS)
    if cpf_check_digits(value[:_CPF_BODY_LEN]) != value[_CPF_BODY_LEN:]:
        return _fail(IdentifierType.CPF, IdentifierReason.CHECK_DIGIT)
    return NormalizedIdentifier(IdentifierType.CPF, value)


# ---- CNPJ --------------------------------------------------------------------------

_CNPJ_RE = re.compile(r"^\d{14}$", re.ASCII)
_CNPJ_BODY_LEN = 12
_CNPJ_FIRST_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_CNPJ_SECOND_WEIGHTS = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
# Remainders 0 and 1 give the check digit 0.
_LOW_REMAINDER_LIMIT = 2


def _cnpj_digit(digits: list[int], weights: tuple[int, ...]) -> int:
    remainder = sum(d * w for d, w in zip(digits, weights, strict=True)) % _MODULUS_11
    return 0 if remainder < _LOW_REMAINDER_LIMIT else _MODULUS_11 - remainder


def cnpj_check_digits(first_12: str) -> str:
    """Compute the two CNPJ check digits (modulus 11, weights 2 to 9).

    Returns:
        The two digits the CNPJ must end with.

    """
    digits = [int(d) for d in first_12]
    first = _cnpj_digit(digits, _CNPJ_FIRST_WEIGHTS)
    second = _cnpj_digit([*digits, first], _CNPJ_SECOND_WEIGHTS)
    return f"{first}{second}"


def _check_cnpj(value: str, _birth_date: date | None) -> _Result:
    if _CNPJ_RE.fullmatch(value) is None:
        return _fail(IdentifierType.CNPJ, IdentifierReason.FORMAT)
    if len(set(value)) == 1:
        return _fail(IdentifierType.CNPJ, IdentifierReason.REPEATED_DIGITS)
    if cnpj_check_digits(value[:_CNPJ_BODY_LEN]) != value[_CNPJ_BODY_LEN:]:
        return _fail(IdentifierType.CNPJ, IdentifierReason.CHECK_DIGIT)
    return NormalizedIdentifier(IdentifierType.CNPJ, value)


# ---- CUIT --------------------------------------------------------------------------

_CUIT_RE = re.compile(r"^\d{11}$", re.ASCII)
_CUIT_BODY_LEN = 10
_CUIT_PREFIX_LEN = 2
_CUIT_PREFIXES = frozenset({"20", "23", "24", "27", "30", "33", "34"})
_CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def cuit_check_digit(first_10: str) -> int | None:
    """Compute the CUIT check digit (AFIP's modulus 11).

    Returns:
        The digit the CUIT must end with, or ``None`` when the body has none.

    """
    total = sum(int(d) * w for d, w in zip(first_10, _CUIT_WEIGHTS, strict=True))
    digit = _MODULUS_11 - total % _MODULUS_11
    if digit == _MODULUS_11:
        return 0
    if digit == _TEN:
        return None
    return digit


def _check_cuit(value: str, _birth_date: date | None) -> _Result:
    if _CUIT_RE.fullmatch(value) is None:
        return _fail(IdentifierType.CUIT, IdentifierReason.FORMAT)
    if value[:_CUIT_PREFIX_LEN] not in _CUIT_PREFIXES:
        return _fail(IdentifierType.CUIT, IdentifierReason.PREFIX)
    if cuit_check_digit(value[:_CUIT_BODY_LEN]) != int(value[_CUIT_BODY_LEN]):
        return _fail(IdentifierType.CUIT, IdentifierReason.CHECK_DIGIT)
    return NormalizedIdentifier(IdentifierType.CUIT, value)


# ---- RUT ---------------------------------------------------------------------------

_RUT_RE = re.compile(r"^\d{1,8}[0-9K]$", re.ASCII)
_RUT_FIRST_WEIGHT = 2
_RUT_WEIGHT_CYCLE = 6


def rut_check_digit(body: str) -> str:
    """Compute the RUT check character (weights 2 to 7 from the right, modulus 11).

    Returns:
        A digit, or ``"K"`` when the check value is ten.

    """
    total = sum(
        int(d) * (_RUT_FIRST_WEIGHT + i % _RUT_WEIGHT_CYCLE)
        for i, d in enumerate(reversed(body))
    )
    digit = _MODULUS_11 - total % _MODULUS_11
    if digit == _MODULUS_11:
        return "0"
    if digit == _TEN:
        return "K"
    return str(digit)


def _check_rut(value: str, _birth_date: date | None) -> _Result:
    if _RUT_RE.fullmatch(value) is None:
        return _fail(IdentifierType.RUT, IdentifierReason.FORMAT)
    if rut_check_digit(value[:-1]) != value[-1]:
        return _fail(IdentifierType.RUT, IdentifierReason.CHECK_DIGIT)
    return NormalizedIdentifier(IdentifierType.RUT, value)


# ---- NIT (sentinel) ----------------------------------------------------------------

_NIT_RE = re.compile(r"^\d{10}$", re.ASCII)
_NIT_BODY_LEN = 9
_NIT_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)


def nit_check_digit(body: str) -> int:
    """Compute the NIT check digit (DIAN's prime weights, modulus 11).

    Returns:
        The remainder itself when it is 0 or 1, otherwise eleven minus it.

    """
    total = sum(int(d) * w for d, w in zip(reversed(body), _NIT_WEIGHTS, strict=False))
    remainder = total % _MODULUS_11
    if remainder < _LOW_REMAINDER_LIMIT:
        return remainder
    return _MODULUS_11 - remainder


def _check_nit(value: str, _birth_date: date | None) -> _Result:
    if _NIT_RE.fullmatch(value) is None:
        return _fail(IdentifierType.NIT, IdentifierReason.FORMAT)
    if nit_check_digit(value[:_NIT_BODY_LEN]) != int(value[_NIT_BODY_LEN]):
        return _fail(IdentifierType.NIT, IdentifierReason.CHECK_DIGIT)
    return NormalizedIdentifier(IdentifierType.NIT, value)


# ---- Cédula (sentinel) and DNI -----------------------------------------------------

_CEDULA_RE = re.compile(r"^[1-9]\d{5,9}$", re.ASCII)
_DNI_PE_RE = re.compile(r"^\d{8}$", re.ASCII)


def _check_cedula_co(value: str, _birth_date: date | None) -> _Result:
    if _CEDULA_RE.fullmatch(value) is None:
        return _fail(IdentifierType.CEDULA_CO, IdentifierReason.FORMAT)
    return NormalizedIdentifier(IdentifierType.CEDULA_CO, value)


def _check_dni_pe(value: str, _birth_date: date | None) -> _Result:
    if _DNI_PE_RE.fullmatch(value) is None:
        return _fail(IdentifierType.DNI_PE, IdentifierReason.FORMAT)
    return NormalizedIdentifier(IdentifierType.DNI_PE, value)


# ---- Passport (ICAO Doc 9303) ------------------------------------------------------

_PASSPORT_RE = re.compile(r"^[A-Z0-9]{6,9}$", re.ASCII)
_MRZ_RE = re.compile(r"^[A-Z0-9<]{9}\d$", re.ASCII)
_MRZ_FIELD_LEN = 9
_ICAO_WEIGHTS = (7, 3, 1)
_LETTER_OFFSET = ord("A") - _TEN


def icao_check_digit(field: str) -> int:
    """Compute an ICAO 9303 check digit (weights 7, 3, 1; ``<`` counts as zero).

    Returns:
        The digit that follows the field in the machine-readable zone.

    """
    total = 0
    for i, char in enumerate(field):
        if char == "<":
            value = 0
        elif char.isdigit():
            value = int(char)
        else:
            value = ord(char) - _LETTER_OFFSET
        total += value * _ICAO_WEIGHTS[i % len(_ICAO_WEIGHTS)]
    return total % _TEN


def _check_passport(value: str, _birth_date: date | None) -> _Result:
    if _PASSPORT_RE.fullmatch(value) is not None:
        return NormalizedIdentifier(IdentifierType.PASSPORT, value)
    if _MRZ_RE.fullmatch(value) is None:
        return _fail(IdentifierType.PASSPORT, IdentifierReason.FORMAT)
    mrz_field = value[:_MRZ_FIELD_LEN]
    if icao_check_digit(mrz_field) != int(value[_MRZ_FIELD_LEN]):
        return _fail(IdentifierType.PASSPORT, IdentifierReason.CHECK_DIGIT)
    # The stored number drops the MRZ padding, so it must stand on its own.
    number = mrz_field.rstrip("<")
    if _PASSPORT_RE.fullmatch(number) is None:
        return _fail(IdentifierType.PASSPORT, IdentifierReason.FORMAT)
    return NormalizedIdentifier(IdentifierType.PASSPORT, number)


# ---- Dispatch ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Normaliser:
    drop_chars: str
    keep_case: bool = True


_NORMALISERS: Mapping[IdentifierType, _Normaliser] = MappingProxyType({
    IdentifierType.CURP: _Normaliser(" -", keep_case=False),
    IdentifierType.RFC: _Normaliser(" -", keep_case=False),
    IdentifierType.CLABE: _Normaliser(" -"),
    IdentifierType.INE: _Normaliser(" -"),
    IdentifierType.CPF: _Normaliser(".-"),
    IdentifierType.CNPJ: _Normaliser("./-"),
    IdentifierType.CUIT: _Normaliser("-"),
    IdentifierType.RUT: _Normaliser(".-", keep_case=False),
    IdentifierType.NIT: _Normaliser(".-"),
    IdentifierType.CEDULA_CO: _Normaliser("."),
    IdentifierType.DNI_PE: _Normaliser(" "),
    IdentifierType.PASSPORT: _Normaliser(" -", keep_case=False),
})

_CHECKS: Mapping[IdentifierType, Callable[[str, date | None], _Result]] = (
    MappingProxyType({
        IdentifierType.CURP: _check_curp,
        IdentifierType.RFC: _check_rfc,
        IdentifierType.CLABE: _check_clabe,
        IdentifierType.INE: _check_ine,
        IdentifierType.CPF: _check_cpf,
        IdentifierType.CNPJ: _check_cnpj,
        IdentifierType.CUIT: _check_cuit,
        IdentifierType.RUT: _check_rut,
        IdentifierType.NIT: _check_nit,
        IdentifierType.CEDULA_CO: _check_cedula_co,
        IdentifierType.DNI_PE: _check_dni_pe,
        IdentifierType.PASSPORT: _check_passport,
    })
)


def _fail(id_type: IdentifierType, reason: IdentifierReason) -> IdentifierError:
    return IdentifierError(id_type, reason)


def normalize(id_type: IdentifierType, raw: str) -> str:
    """Bring a raw identifier to its canonical spelling; never raises.

    Types written with letters are NFKC-folded and upper-cased; every type is
    stripped and loses its usual separators.

    Returns:
        The normalised text, which may still be malformed.

    """
    rule = _NORMALISERS[id_type]
    text = raw if rule.keep_case else unicodedata.normalize("NFKC", raw).upper()
    text = text.strip()
    for char in rule.drop_chars:
        text = text.replace(char, "")
    return text


def validate(
    id_type: IdentifierType, raw: str, *, birth_date: date | None = None
) -> NormalizedIdentifier | IdentifierError:
    """Normalise and check an identifier.

    Returns:
        The canonical value with its attributes, or the first failing reason.

    """
    canonical = normalize(id_type, raw)
    if not canonical:
        return _fail(id_type, IdentifierReason.FORMAT)
    return _CHECKS[id_type](canonical, birth_date)
