"""Masks that let a reader recognise a value without seeing it."""

from xolotl.platform.text import mask_tail

MASK_KEEP = 4


def mask_identifier(value: str, *, keep: int = MASK_KEEP) -> str:
    """Hide all but the last ``keep`` characters of an identifier or a contact.

    Returns:
        ``"****"`` and the tail, or ``"****"`` alone for a short value.

    """
    return mask_tail(value, keep=keep)


def initials(*names: str) -> str:
    """Reduce names to the first letter of each word, upper-cased, each with a dot.

    Returns:
        The initials, such as ``"A.M.P.G."``, or ``""`` when there is no word.

    """
    return "".join(f"{word[0].upper()}." for name in names for word in name.split())
