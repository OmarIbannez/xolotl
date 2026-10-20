"""Name normalization v2 (SP-03 spec §4.2, D7).

``normalize_name`` turns a published or customer name into tokens with a role:

- Folding: NFKC, case folding, Cyrillic transliterated (a deterministic table),
  diacritics dropped (ñ becomes n), apostrophes and abbreviation dots removed so
  "O'Brien" and "S.A." stay one token, any other punctuation a separator.
- Persons: particles (de, del, la, los, y…) and honorifics are tagged and carry no
  weight. Given and surname roles come from the structured fields when given, else
  from the OFAC "SURNAMES, Given names" form (reordered to given names first), else
  by position: the last two content tokens are surnames when there are three or
  more (Spanish naming: "Juan de la Cruz Pérez"), the last one when there are two.
- Organizations: runs of single letters are joined ("S. A. de C. V." becomes
  "sa de cv"); a legal form at the end (or, failing that, the start) is tagged and
  canonicalized; generic words
  (grupo, company, international…) are tagged.
- Letters with no deterministic transliteration (Arabic, Han…) are kept as they are
  and their tokens are ``UNKNOWN``.

``folded`` keeps every token, particles included, in final order.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from xolotl.modules.screening.lists import EntityType
from xolotl.modules.screening.matching.phonetic import phonetic_key
from xolotl.modules.screening.matching.tables import legal_forms, word_tables

MATCHER_VERSION = "2.1.0"


class TokenRole(StrEnum):
    GIVEN = "GIVEN"
    SURNAME = "SURNAME"
    PARTICLE = "PARTICLE"
    LEGAL_FORM = "LEGAL_FORM"
    GENERIC = "GENERIC"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    folded: str
    phonetic: str
    role: TokenRole


@dataclass(frozen=True, slots=True)
class NormalizedName:
    folded: str
    tokens: tuple[Token, ...]
    legal_form: str | None


# Cyrillic lower-case letters (by code point, so no look-alike glyphs sit in the
# source) to Latin, following the passport transliteration of ICAO Doc 9303.
_CYRILLIC = str.maketrans({
    0x0430: "a", 0x0431: "b", 0x0432: "v", 0x0433: "g", 0x0434: "d", 0x0435: "e",
    0x0451: "e", 0x0436: "zh", 0x0437: "z", 0x0438: "i", 0x0439: "i", 0x043A: "k",
    0x043B: "l", 0x043C: "m", 0x043D: "n", 0x043E: "o", 0x043F: "p", 0x0440: "r",
    0x0441: "s", 0x0442: "t", 0x0443: "u", 0x0444: "f", 0x0445: "kh", 0x0446: "ts",
    0x0447: "ch", 0x0448: "sh", 0x0449: "shch", 0x044A: "", 0x044B: "y", 0x044C: "",
    0x044D: "e", 0x044E: "iu", 0x044F: "ia", 0x0456: "i", 0x0457: "i", 0x0454: "ie",
    0x0491: "g",
})  # fmt: skip
# Apostrophes (straight, curly, grave, acute) and dots join the letters around them.
_JOINERS = re.compile(r"['\u2019\u2018`\u00b4.]")
_SEPARATORS = re.compile(r"[^\w]+|_")
_PERSON_SURNAMES_FROM = 3


def _fold(piece: str) -> str:
    text = unicodedata.normalize("NFKC", piece).casefold().translate(_CYRILLIC)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _pieces(text: str) -> list[str]:
    joined = _JOINERS.sub("", unicodedata.normalize("NFKC", text))
    return [p for p in _SEPARATORS.split(joined) if p]


def normalize_name(
    text: str,
    *,
    entity_type: EntityType,
    given: str | None = None,
    surname: str | None = None,
) -> NormalizedName:
    """Normalize one name for matching.

    Returns:
        The folded name, its tokens with roles and phonetic keys, and the canonical
        legal form of an organization (``None`` otherwise).

    """
    if entity_type is EntityType.PERSON:
        tokens = _person(text, given, surname)
        form = None
    else:
        tokens, form = _organization(text)
    return NormalizedName(
        folded=" ".join(t.folded for t in tokens), tokens=tuple(tokens), legal_form=form
    )


def _token(piece: str, role: TokenRole) -> Token:
    folded = _fold(piece)
    if not folded.isascii():
        role = TokenRole.UNKNOWN
    return Token(text=piece, folded=folded, phonetic=phonetic_key(folded), role=role)


type _Roles = list[TokenRole | None]


def _structured(text: str, given: str | None, surname: str | None) -> _Roles:
    given_set = {_fold(p) for p in _pieces(given or "")}
    surname_set = {_fold(p) for p in _pieces(surname or "")}
    roles: _Roles = []
    for piece in _pieces(text):
        folded = _fold(piece)
        if folded in surname_set:
            roles.append(TokenRole.SURNAME)
        elif folded in given_set:
            roles.append(TokenRole.GIVEN)
        else:
            roles.append(None)
    return roles


def _person_pieces(
    text: str, given: str | None, surname: str | None
) -> tuple[list[str], _Roles]:
    if given or surname:
        return _pieces(text), _structured(text, given, surname)
    head, comma, tail = text.partition(",")
    if comma and _pieces(tail):
        # OFAC writes individuals as "SURNAMES, Given names": reorder them.
        given_pieces, surname_pieces = _pieces(tail), _pieces(head)
        roles: _Roles = [TokenRole.GIVEN] * len(given_pieces)
        roles += [TokenRole.SURNAME] * len(surname_pieces)
        return given_pieces + surname_pieces, roles
    pieces = _pieces(text)
    return pieces, _positional_roles([_fold(p) for p in pieces])


def _person(text: str, given: str | None, surname: str | None) -> list[Token]:
    words = word_tables()
    pieces, roles = _person_pieces(text, given, surname)
    tokens: list[Token] = []
    for piece, role in zip(pieces, roles, strict=True):
        folded = _fold(piece)
        if folded in words.particles:
            tokens.append(_token(piece, TokenRole.PARTICLE))
        elif folded in words.honorifics:
            tokens.append(_token(piece, TokenRole.GENERIC))
        else:
            tokens.append(_token(piece, role or TokenRole.UNKNOWN))
    return _keep_some_weight(tokens)


def _positional_roles(folded: list[str]) -> _Roles:
    words = word_tables()
    content = [
        i
        for i, f in enumerate(folded)
        if f not in words.particles and f not in words.honorifics
    ]
    roles: _Roles = [None] * len(folded)
    surnames = 2 if len(content) >= _PERSON_SURNAMES_FROM else 1
    if len(content) < surnames + 1:
        return roles
    for position, index in enumerate(content):
        in_tail = position >= len(content) - surnames
        roles[index] = TokenRole.SURNAME if in_tail else TokenRole.GIVEN
    return roles


def _keep_some_weight(tokens: list[Token]) -> list[Token]:
    # A name made only of weightless words ("De La") must still be matchable.
    weightless = {TokenRole.PARTICLE, TokenRole.GENERIC, TokenRole.LEGAL_FORM}
    if tokens and all(t.role in weightless for t in tokens):
        return [Token(t.text, t.folded, t.phonetic, TokenRole.UNKNOWN) for t in tokens]
    return tokens


def _join_initials(pieces: list[str]) -> list[str]:
    joined: list[str] = []
    run: list[str] = []
    for piece in [*pieces, ""]:
        if len(piece) == 1:
            run.append(piece)
            continue
        if run:
            joined.append("".join(run))
            run = []
        if piece:
            joined.append(piece)
    return joined


def _legal_form_span(folded: list[str]) -> tuple[range, str | None]:
    # A legal form ends the name ("Acme SA de CV"), or starts it as some registries
    # write it ("LLC Synesis"); the end wins when both match.
    for at_end in (True, False):
        for sequence, code in legal_forms():
            size = len(sequence)
            if size >= len(folded):
                continue
            if at_end and tuple(folded[-size:]) == sequence:
                return range(len(folded) - size, len(folded)), code
            if not at_end and tuple(folded[:size]) == sequence:
                return range(size), code
    return range(0), None


def _organization(text: str) -> tuple[list[Token], str | None]:
    words = word_tables()
    pieces = _join_initials(_pieces(text))
    folded = [_fold(p) for p in pieces]
    span, form = _legal_form_span(folded)
    tokens: list[Token] = []
    for index, (piece, f) in enumerate(zip(pieces, folded, strict=True)):
        if index in span:
            role = TokenRole.LEGAL_FORM
        elif f in words.particles:
            role = TokenRole.PARTICLE
        elif f in words.generic:
            role = TokenRole.GENERIC
        else:
            role = TokenRole.UNKNOWN
        tokens.append(_token(piece, role))
    return _keep_some_weight(tokens), form
