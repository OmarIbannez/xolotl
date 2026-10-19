"""The event envelope and its mapping onto ``outbox`` rows (spec §9.2, program D8)."""

from datetime import datetime
from typing import ClassVar, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from xolotl.platform.events.models import OutboxRow
from xolotl.platform.sqltypes import contains_float

# Carried by columns of the outbox row; every other field travels in its payload.
_COLUMN_FIELDS = {"id", "type", "tenant_id", "key"}
_FLOATS_MESSAGE = "event data must not contain floats; use ints or strings"


class Event(BaseModel):
    """A fact published through the outbox.

    ``key`` (1 to 200 characters) orders delivery: events with one key reach each
    consumer in publication order. ``data`` carries ids and no personal data
    (program D8), and no floats at any depth, the same rule as ``AuditEvent.detail``
    (see ``contains_float``): the event is rebuilt from its JSONB row, which does not
    round-trip every float. Use ints or strings instead, such as an amount in minor
    units or a decimal string.

    Frozen: attributes cannot be reassigned. ``data`` is a plain dict, shared and not
    copied, so do not change it in place.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: UUID
    type: str
    source: str
    tenant_id: UUID
    subject: str
    time: datetime
    key: str = Field(min_length=1, max_length=200)
    correlation_id: str | None = None
    policy_snapshot_id: UUID | None = None
    data: dict[str, JsonValue]

    @field_validator("data")
    @classmethod
    def _reject_floats(cls, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if contains_float(data):
            raise ValueError(_FLOATS_MESSAGE)
        return data


def row_from_event(event: Event) -> OutboxRow:
    """Map an event onto a new, unsaved outbox row.

    ``tenant_id``, ``id``, ``type`` and ``key`` are columns; the other fields go into
    ``payload`` as JSON. ``status``, ``attempts`` and ``created_at`` take their
    defaults and ``seq`` is assigned by the database when the row is flushed.

    The floats rule is checked again here: ``model_copy(update=...)`` skips
    validation, and a float written to the outbox would be rejected only at
    dispatch, where it would fail every attempt until the row is dead.

    Returns:
        The row to add to the publisher's session.

    Raises:
        ValueError: When ``event.data`` holds a float at any depth.

    """
    if contains_float(event.data):
        raise ValueError(_FLOATS_MESSAGE)
    payload = cast(
        "dict[str, JsonValue]", event.model_dump(mode="json", exclude=_COLUMN_FIELDS)
    )
    return OutboxRow(
        tenant_id=event.tenant_id,
        id=event.id,
        type=event.type,
        key=event.key,
        payload=payload,
    )


def event_from_row(row: OutboxRow) -> Event:
    """Rebuild the event a row was made from, validating it again.

    Returns:
        The event; its columns win over any same-named payload entry.

    """
    return Event.model_validate({
        **row.payload,
        "id": row.id,
        "type": row.type,
        "tenant_id": row.tenant_id,
        "key": row.key,
    })
