"""Request and response bodies for the tenants module."""

from datetime import datetime
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from xolotl.platform.enums import (
    ApiKeyStatus,
    DeliveryStatus,
    EndpointStatus,
    Role,
    StaffStatus,
    TenantEnv,
    TenantStatus,
)
from xolotl.platform.events.url_policy import MAX_URL_LENGTH


class LoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


class MfaRequired(BaseModel):
    mfa_required: Literal[True] = True
    mfa_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # ruff: ignore[hardcoded-password-string]
    expires_in: int
    must_change_password: bool


class TotpLoginRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)


class TotpEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_slug: str
    email: str
    display_name: str
    roles: list[Role]
    must_change_password: bool
    totp_enabled: bool


class StaffCreateRequest(BaseModel):
    email: str
    display_name: str = Field(min_length=1, max_length=200)
    roles: list[Role] = Field(min_length=1)


class StaffCreatedResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    roles: list[Role]
    initial_password: str


class StaffResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    roles: list[Role]
    status: StaffStatus
    must_change_password: bool
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1)
    env: TenantEnv | None = None


class ApiKeyCreatedResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    env: TenantEnv
    key: str  # shown once


class ApiKeyResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    env: TenantEnv
    status: ApiKeyStatus
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TenantResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    legal_name: str
    country_code: str
    cell: str
    env: TenantEnv
    status: TenantStatus
    created_at: datetime


class PingRequest(BaseModel):
    message: str = Field(default="ping", max_length=200)


class PingResponse(BaseModel):
    pong: str
    actor_id: str
    at: datetime


class NotificationResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    template: str
    data: dict[str, JsonValue]
    recipient_user_id: UUID | None
    recipient_role: Role | None
    created_at: datetime
    read_at: datetime | None


# One event type, such as ``party.created.v1``, or ``*`` for all of them.
EventTypeName = Annotated[
    str, StringConstraints(pattern=r"^(\*|[a-z][a-z0-9_.-]*)$", max_length=100)
]


class WebhookEndpointCreateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    event_types: list[EventTypeName] = Field(min_length=1, max_length=50)


class WebhookEndpointResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    event_types: list[str]
    status: EndpointStatus
    created_at: datetime


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    # Shown once, here, and never again; repr=False keeps it out of any printed model.
    secret: str = Field(repr=False)


class WebhookSecretResponse(BaseModel):
    id: UUID
    secret: str = Field(repr=False)  # shown once, like the one a new endpoint gets


class WebhookDeliveryResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    event_id: UUID
    attempt: int
    status: DeliveryStatus
    response_code: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
