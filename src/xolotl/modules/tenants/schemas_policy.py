"""Request and response bodies of the policy, units and calendar routes (SP-02a).

Rows are validated with ``from_attributes`` where a response mirrors a table. The
regulatory profile carries its RFC masked and a branch never its address: both are
encrypted, and nothing here reveals them to a person.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from xolotl.platform.calendar import DeadlineRule
from xolotl.platform.enums import BranchStatus, HolidayScope
from xolotl.platform.policy import EffectivePolicy, TenantOverride
from xolotl.platform.units import ConversionRule

_ROWS = ConfigDict(from_attributes=True)
_PII: dict[str, JsonValue] = {"pii": True}


class PackVersionResponse(BaseModel):
    id: UUID
    code: str
    version: int
    title: str
    effective_from: date
    effective_to: date | None
    content_hash: str
    loaded_at: datetime


class ConversionResponse(BaseModel):
    amount: Decimal
    currency: str
    unit: str
    rate: Decimal
    unit_value_id: int | None
    rule: ConversionRule
    resolved_date: date


class EffectivePolicyResponse(BaseModel):
    policy: EffectivePolicy
    snapshot_id: UUID
    content_hash: str
    # Keyed by the value's path in the policy; null where a unit value is missing.
    converted: dict[str, ConversionResponse | None]
    missing_units: list[str]


class AssignmentCreateRequest(BaseModel):
    pack_version_id: UUID
    product: str | None = Field(default=None, min_length=1, max_length=64)
    effective_from: date


class AssignmentResponse(BaseModel):
    id: UUID
    pack_version_id: UUID
    pack: str
    version: int
    product: str | None
    effective_from: date
    effective_to: date | None
    approved_by: UUID
    created_at: datetime


class OverrideResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = _ROWS

    id: UUID
    version: int
    overrides: TenantOverride
    effective_from: date
    approved_by: UUID
    created_at: datetime


class RegulatoryProfileRequest(BaseModel):
    rfc: str = Field(min_length=1, max_length=13, json_schema_extra=_PII)
    legal_name: str = Field(min_length=1, max_length=200)
    casfim_supervisor: str | None = Field(default=None, max_length=64)
    casfim_entity_key: str | None = Field(default=None, max_length=64)
    spplld_activity_key: str | None = Field(default=None, max_length=64)
    platform_domain: str | None = Field(default=None, max_length=253)
    entidad_colegiada: bool = False
    compliance_officer_user_id: UUID | None = None


class RegulatoryProfileResponse(BaseModel):
    id: UUID
    rfc_masked: str
    legal_name: str
    casfim_supervisor: str | None
    casfim_entity_key: str | None
    spplld_activity_key: str | None
    platform_domain: str | None
    entidad_colegiada: bool
    compliance_officer_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class BranchCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500, json_schema_extra=_PII)
    state_code: str = Field(min_length=2, max_length=2)
    municipality_code: str = Field(min_length=3, max_length=3)


class BranchResponse(BaseModel):
    id: UUID
    code: str
    name: str
    state_code: str
    municipality_code: str
    status: BranchStatus
    created_at: datetime


class UnitSummaryResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = _ROWS

    unit_code: str
    quote_ccy: str
    daily: bool
    series_id: str | None
    latest_valid_from: date | None
    latest_fetched_at: datetime | None
    latest_source: str | None


class UnitsResponse(BaseModel):
    items: list[UnitSummaryResponse]


class UnitLookupResponse(BaseModel):
    unit_code: str
    on: date
    rule: ConversionRule
    resolved_date: date
    value: Decimal
    quote_ccy: str
    unit_value_id: int
    source: str
    fetched_at: datetime


class UnitValueResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = _ROWS

    id: int
    unit_code: str
    valid_from: date
    valid_to: date | None
    value: Decimal
    quote_ccy: str
    source: str
    fetched_at: datetime
    superseded_by: int | None


class UnitValuesResponse(BaseModel):
    unit_code: str
    items: list[UnitValueResponse]


class HolidayResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = _ROWS

    day: date
    name: str
    source: str


class HolidaysResponse(BaseModel):
    country: str
    scope: HolidayScope
    year: int
    items: list[HolidayResponse]


class DueResponse(BaseModel):
    due_at: datetime
    rule: DeadlineRule
    anchor: datetime
    scope: HolidayScope
