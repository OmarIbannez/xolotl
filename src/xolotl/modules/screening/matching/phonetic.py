"""A phonetic key for Spanish and English names (SP-03 spec §4.2; plan Decision 8).

Published phonetic codes (Soundex, Metaphone) are English-only and break the
equivalences Mexican and Latin American names rely on, so the key is a small rule
table over a folded token (lower-case ASCII letters and digits). Read left to right:

- Silent and doubled spellings: ``h`` is silent except in ``ch``, ``sh``, ``kh``
  and ``zh``; ``ph`` is ``f``; ``th``, ``dh`` and ``gh`` drop the ``h``.
- Spanish sounds: ``b``/``v`` are one sound; ``ll`` and a ``y`` before a vowel are
  ``y``; ``c`` before ``e``/``i``, ``z`` and ``s`` are ``s``; ``qu``, ``k``, ``ck``
  and other ``c`` are ``k``; ``gu`` before ``e``/``i`` is a hard ``g``; ``g`` before
  ``e``/``i``, ``j``, ``kh`` and ``zh`` are ``j`` (so Jorge and George agree); ``ch``
  keeps its own sound; ``sh`` is ``x``; ``x`` starting a name before a vowel is
  ``j`` (Ximénez, Xavier), elsewhere ``ks``; ``w`` is ``u``.
- Vowels: a leading vowel becomes ``a``; later vowels drop (so Mohamed, Muhammad and
  Mohammed agree); repeated symbols collapse.

Digits and non-ASCII letters (scripts with no deterministic transliteration) pass
through unchanged. The key is stable across patch releases of ``MATCHER_VERSION``.

Examples: ``gonzalez`` and ``gonsales`` give ``gnsls``; ``hernandez`` gives
``arnds``; ``chavez`` and ``chaves`` give ``cbs``; ``ximenez`` and ``jimenez`` give
``jmns``.
"""

_VOWELS = frozenset("aeiou")
_FRONT = frozenset("ei")
# Two-letter spellings that map to one symbol (or to nothing) before any other rule.
_DIGRAPHS = {
    "ch": "c",
    "sh": "x",
    "kh": "j",
    "zh": "j",
    "ph": "f",
    "th": "t",
    "dh": "d",
    "gh": "g",
    "ll": "y",
    "qu": "k",
    "ck": "k",
}
_LETTERS = {"v": "b", "z": "s", "k": "k", "q": "k", "w": "u", "j": "j"}


def _symbol(token: str, i: int) -> tuple[str, int]:
    """Map the spelling at ``token[i]``.

    Returns:
        The symbol(s) it sounds as and how many letters it consumed.

    """
    pair = token[i : i + 2]
    nxt = token[i + 1 : i + 2]
    after = token[i + 2 : i + 3]
    char = token[i]
    symbol: str
    used = 1
    if pair in _DIGRAPHS:
        symbol, used = _DIGRAPHS[pair], 2
    elif pair == "gu" and after in _FRONT:
        symbol, used = "g", 2
    elif char == "c":
        symbol = "s" if nxt in _FRONT else "k"
    elif char == "g":
        symbol = "j" if nxt in _FRONT else "g"
    elif char == "y":
        symbol = "y" if nxt in _VOWELS else "i"
    elif char == "x":
        symbol = "j" if i == 0 and nxt in _VOWELS else "ks"
    elif char == "h":
        symbol = ""
    else:
        symbol = _LETTERS.get(char, char)
    return symbol, used


def phonetic_key(token: str) -> str:
    """Compute the phonetic key of one folded name token.

    Returns:
        The key; the token itself when it holds anything but ASCII letters, or is a
        number.

    """
    if not token.isascii() or not token.isalpha():
        return token
    symbols: list[str] = []
    i = 0
    while i < len(token):
        symbol, used = _symbol(token, i)
        symbols.append(symbol)
        i += used
    sounds = "".join(symbols)
    if not sounds:
        return token
    head = "a" if sounds[0] in _VOWELS else sounds[0]
    rest = [s for s in sounds[1:] if s not in _VOWELS]
    key: list[str] = [head]
    for s in rest:
        if s != key[-1]:
            key.append(s)
    return "".join(key)
