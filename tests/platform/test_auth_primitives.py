from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest

from xolotl.platform.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    check_password_strength,
    generate_password,
    hash_password,
    verify_password,
)
from xolotl.platform.auth.tokens import decode_token, issue_token
from xolotl.platform.auth.totp import current_code, new_secret, otpauth_uri, verify_code
from xolotl.platform.config import get_settings
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import UnauthorizedError, ValidationFailedError


def test_password_hash_round_trip():
    digest = hash_password("correct horse battery")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "correct horse battery")
    assert not verify_password(digest, "wrong")
    assert not verify_password("not-a-hash", "whatever")


def test_generated_passwords_are_long_and_unique():
    first, second = generate_password(), generate_password()
    assert len(first) >= 20
    assert first != second
    check_password_strength(first)


def test_password_strength_minimum():
    with pytest.raises(ValidationFailedError):
        check_password_strength("x" * (MIN_PASSWORD_LENGTH - 1))
    check_password_strength("x" * MIN_PASSWORD_LENGTH)


def _issue(**overrides):
    kwargs: dict[str, Any] = {
        "subject": "staff-1",
        "tenant_id": uuid4(),
        "tenant_slug": "acme",
        "roles": [Role.MLRO, Role.ADMIN],
        "purpose": "access",
        "ttl": timedelta(minutes=5),
    }
    kwargs.update(overrides)
    return kwargs, issue_token(**kwargs)


def test_token_round_trip_carries_oidc_shaped_claims():
    kwargs, token = _issue()
    claims = decode_token(token, purpose="access")
    assert claims.iss == get_settings().jwt_issuer
    assert claims.sub == "staff-1"
    assert claims.tenant_id == kwargs["tenant_id"]
    assert claims.tenant_slug == "acme"
    assert claims.roles == [Role.ADMIN, Role.MLRO]
    assert claims.actor_type is ActorType.STAFF
    assert claims.purpose == "access"
    assert claims.exp - claims.iat == 300
    assert len(claims.jti) == 32


def test_expired_tampered_wrong_purpose_and_wrong_issuer_are_rejected():
    _, expired = _issue(ttl=timedelta(seconds=-1))
    with pytest.raises(UnauthorizedError):
        _ = decode_token(expired, purpose="access")
    _, good = _issue()
    tampered = good[:-2] + ("AA" if not good.endswith("AA") else "BB")
    with pytest.raises(UnauthorizedError):
        _ = decode_token(tampered, purpose="access")
    _, mfa = _issue(purpose="mfa")
    with pytest.raises(UnauthorizedError):
        _ = decode_token(mfa, purpose="access")
    assert decode_token(mfa, purpose="mfa").purpose == "mfa"
    foreign = jwt.encode(
        {"iss": "someone-else", "sub": "x", "exp": 4_102_444_800, "iat": 0},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        _ = decode_token(foreign, purpose="access")
    with pytest.raises(UnauthorizedError):
        _ = decode_token("garbage", purpose="access")


def test_totp_enrolment_and_verification():
    secret = new_secret()
    assert len(secret) == 32
    uri = otpauth_uri(secret, email="ana@acme.mx", tenant_slug="acme")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=xolotl%3Aacme" in uri
    assert verify_code(secret, current_code(secret))
    assert not verify_code(secret, "000000")
