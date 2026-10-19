"""HS256 JWTs for staff sessions with OIDC-shaped claims (spec §6.1, D23)."""

from collections.abc import Iterable
from datetime import timedelta
from typing import Literal
from uuid import UUID

import jwt
from pydantic import BaseModel, ValidationError

from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import UnauthorizedError
from xolotl.platform.ids import uuid7

ALGORITHM = "HS256"
type TokenPurpose = Literal["access", "mfa"]


class TokenClaims(BaseModel):
    iss: str
    sub: str
    tenant_id: UUID
    tenant_slug: str
    roles: list[Role]
    actor_type: ActorType
    purpose: TokenPurpose
    iat: int
    exp: int
    jti: str


def issue_token(
    *,
    subject: str,
    tenant_id: UUID,
    tenant_slug: str,
    roles: Iterable[Role],
    purpose: TokenPurpose,
    ttl: timedelta,
) -> str:
    settings = get_settings()
    now = clock.now()
    claims = TokenClaims(
        iss=settings.jwt_issuer,
        sub=subject,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        roles=sorted(roles),
        actor_type=ActorType.STAFF,
        purpose=purpose,
        iat=int(now.timestamp()),
        exp=int((now + ttl).timestamp()),
        jti=uuid7().hex,
    )
    return jwt.encode(
        claims.model_dump(mode="json"), settings.jwt_secret, algorithm=ALGORITHM
    )


def decode_token(token: str, *, purpose: TokenPurpose) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        claims = TokenClaims.model_validate(payload)
    except (jwt.PyJWTError, ValidationError) as exc:
        msg = "invalid token"
        raise UnauthorizedError(msg) from exc
    if claims.purpose != purpose:
        msg = "invalid token"
        raise UnauthorizedError(msg)
    return claims
