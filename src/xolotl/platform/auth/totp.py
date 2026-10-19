"""TOTP enrolment and verification (RFC 6238) via pyotp."""

from collections.abc import Callable
from typing import cast

import pyotp

ISSUER_PREFIX = "xolotl"
# pyotp leaves the **kwargs of provisioning_uri untyped.
_UriBuilder = Callable[..., str]


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, *, email: str, tenant_slug: str) -> str:
    build = cast("_UriBuilder", pyotp.TOTP(secret).provisioning_uri)
    return build(name=email, issuer_name=f"{ISSUER_PREFIX}:{tenant_slug}")


def verify_code(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def current_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()
