"""Password hashing (argon2id), generation and the minimum-length rule."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from xolotl.platform.errors import ValidationFailedError

MIN_PASSWORD_LENGTH = 12
_hasher = PasswordHasher()  # argon2id with the library's current defaults
# A real hash of a random password, made with the same parameters as every stored
# hash, so checking a password against it costs what a real check costs.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(18))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        verified = _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return verified


def verify_dummy(password: str) -> None:
    """Spend one argon2id verification on a hash that never matches.

    Login failures that need no real check (unknown tenant or email, a disabled or
    locked account) call this so they cost what a wrong password costs.
    """
    _ = verify_password(_DUMMY_HASH, password)


def generate_password() -> str:
    """Generate a one-time initial password.

    Returns:
        24 URL-safe characters (144 bits).

    """
    return secrets.token_urlsafe(18)


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        msg = f"password must have at least {MIN_PASSWORD_LENGTH} characters"
        raise ValidationFailedError(msg)
