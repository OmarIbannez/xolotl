"""Blind indexes: equality search over encrypted columns (spec §8.2, program spec D7).

An encrypted column cannot be searched: every encryption of a value differs. A blind
index is a second column next to it holding a keyed hash of the normalised value,
``HMAC-SHA256(index_key, "<kind>:<normalised>")`` under the tenant's INDEX key. A
lookup computes the same tag for the value it is given and compares bytes. A tag says
only whether two values are equal within one tenant and kind, never what they are,
and tenants share no tags because each has its own key. INDEX keys are never rotated
(a rotation would invalidate every stored tag), so a tag computed today matches one
stored last year, and any process with the same master key and database computes the
same tags.

Normalisation decides what "the same value" means. Every kind starts with NFKC
(fullwidth forms, ligatures and compatibility spaces fold to the plain ones) and a
trim; the kind is trimmed and lower-cased, so ``"Email"`` and ``"email"`` are one
kind. Then:

============================  =====================================================
kind                          rule
============================  =====================================================
``email``                     lower-case; all whitespace removed; everything else
                              (``@``, ``.``, ``+``, ``-``, ``_``) kept
``phone``                     decimal digits only, written 0-9 (digits of other
                              scripts are mapped); the country code is as given, so
                              ``+52 55 1234 5678`` and ``55 1234 5678`` differ
``name``                      upper-case; letters and digits of any script kept,
                              accents included (``GÓMEZ`` stays ``GÓMEZ``); runs of
                              whitespace become one space; every other character is
                              removed (``O'Brien-Smith`` is ``OBRIENSMITH``)
``rfc``                       upper-case; ASCII ``A-Z`` and ``0-9``, plus ``Ñ`` and
                              ``&``, which the SAT's alphabet allows in the name part
                              (``PEÑA800101AB1`` and ``PEA800101AB1`` are different
                              RFCs, and dropping the ``Ñ`` would make a persona
                              física's look like a persona moral's)
``curp``, ``clabe`` and       upper-case; ASCII ``A-Z`` and ``0-9`` only, so
every other kind              non-ASCII letters and ``&`` are dropped
============================  =====================================================

A value with nothing left after normalisation has no tag: every such value would
share one, and a search for it would match them all. ``blind_index`` refuses it, and
callers skip blank values.

Nothing here logs, and no error message carries a value, a tag or a key.
"""

import hmac
import string
import unicodedata
from collections.abc import Callable, Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.platform.crypto.provider import get_key_provider

_CODE_CHARS = frozenset(string.ascii_uppercase + string.digits)
_RFC_CHARS = _CODE_CHARS | {"Ñ", "&"}


def _email(text: str) -> str:
    return "".join(text.lower().split())


def _phone(text: str) -> str:
    return "".join(str(unicodedata.decimal(char)) for char in text if char.isdecimal())


def _name(text: str) -> str:
    kept = (
        char if char.isalnum() else " " if char.isspace() else ""
        for char in text.upper()
    )
    return " ".join("".join(kept).split())


def _code(text: str) -> str:
    return "".join(char for char in text.upper() if char in _CODE_CHARS)


def _rfc(text: str) -> str:
    return "".join(char for char in text.upper() if char in _RFC_CHARS)


_RULES: Mapping[str, Callable[[str], str]] = {
    "email": _email,
    "phone": _phone,
    "name": _name,
    "rfc": _rfc,
}


def _label(kind: str) -> str:
    return kind.strip().lower()


def _normalise(label: str, value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).strip()
    return _RULES.get(label, _code)(folded)


def normalize_identifier(kind: str, value: str) -> str:
    """Reduce ``value`` to the form two spellings of one identifier share.

    The rules per kind are in the module docstring; an unknown kind gets the rule of
    ``curp``, ``rfc`` and ``clabe``. The result can be empty.

    Returns:
        The normalised value.

    """
    return _normalise(_label(kind), value)


def blind_index(
    kind: str, value: str, *, tenant_id: UUID, session: Session | None = None
) -> bytes:
    """Compute the searchable tag of ``value`` for one tenant.

    Pass the caller's ``session`` to read the INDEX key inside its transaction, which
    is the only way to index in the transaction that created the tenant's keys.

    Returns:
        The 32-byte HMAC-SHA256 tag.

    Raises:
        ValueError: When ``kind`` contains a colon (it would blur the boundary with
            the value), or when nothing is left of ``value`` after normalisation.

    """
    label = _label(kind)
    if ":" in label:
        msg = f"a blind-index kind cannot contain a colon: {label!r}"
        raise ValueError(msg)
    normalised = _normalise(label, value)
    if not normalised:
        msg = f"{label}: nothing left to index after normalisation"
        raise ValueError(msg)
    key = get_key_provider().index_key(tenant_id, session=session)
    return hmac.digest(key, f"{label}:{normalised}".encode(), "sha256")
