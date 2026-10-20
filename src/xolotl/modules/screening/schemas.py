"""Request and response models of the screening API (SP-03 spec §12).

The lists models carry public-source facts and counts; a private-list entry is
plaintext and only ever built after the reveal has been audited.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from xolotl.modules.screening.enums import (
    DeltaChange,
    ListKind,
    ListRequirement,
    VersionStatus,
)
from xolotl.modules.screening.lists import EntityType


class ListOverview(BaseModel):
    """A subscribed list with its active version and freshness."""

    code: str
    list_kind: ListKind
    title: str
    jurisdiction: str
    requirement: ListRequirement
    enabled: bool
    active_seq: int | None
    published_at: datetime | None
    fetched_at: datetime | None
    entry_count: int | None
    stale: bool
    failing: bool
    last_error: str | None
    last_success_at: datetime | None


class ListOverviewResponse(BaseModel):
    items: list[ListOverview]


class ListVersionResponse(BaseModel):
    id: UUID
    seq: int
    status: VersionStatus
    published_at: datetime | None
    source_version: str | None
    parser_version: str
    fetched_at: datetime
    loaded_by: str
    file_sha256: str
    file_size: int
    entry_count: int
    added: int
    changed: int
    removed: int


class DeltaRowResponse(BaseModel):
    source_entry_key: str
    change: DeltaChange
    prev_entry_id: UUID | None
    new_entry_id: UUID | None
    prev_name: str | None
    new_name: str | None


class ListEntryResponse(BaseModel):
    id: UUID
    list_code: str
    source_entry_key: str
    from_seq: int
    to_seq: int | None
    entry: dict[str, JsonValue]


class SubscriptionUpdate(BaseModel):
    enabled: bool


class PrivateListCreate(BaseModel):
    list_code: str = Field(max_length=64)
    title: str = Field(min_length=1, max_length=200)
    list_kind: ListKind | None = None


class PrivateListVersionResponse(BaseModel):
    id: UUID
    seq: int
    status: VersionStatus
    uploaded_by: str
    uploaded_at: datetime
    content_type: str
    file_size: int
    entry_count: int
    added: int
    changed: int
    removed: int


class PrivateListResponse(BaseModel):
    id: UUID
    list_code: str
    list_kind: ListKind
    title: str
    created_at: datetime
    active_version: PrivateListVersionResponse | None


class PrivateListEntryResponse(BaseModel):
    id: UUID
    source_entry_key: str
    entity_type: EntityType
    entry: dict[str, JsonValue]
