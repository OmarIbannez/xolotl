"""Text helpers shared across modules: the RFC shape and tail masking."""

import re

# Shape only (upper-case the value first); the check digit is a separate validator.
RFC_SHAPE: re.Pattern[str] = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$")
MASK = "****"


def mask_tail(value: str, *, keep: int = 4) -> str:
    """Hide all but the last ``keep`` characters of ``value``.

    Returns:
        ``"****"`` followed by the last ``keep`` characters, or ``"****"`` alone
        when ``value`` is no longer than ``keep``.

    """
    if len(value) > keep:
        return MASK + value[-keep:]
    return MASK
