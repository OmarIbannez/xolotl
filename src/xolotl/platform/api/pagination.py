"""Keyset pagination behind an opaque cursor (spec §7).

Lists are ordered ``(timestamp DESC, id DESC)``; the cursor is the URL-safe
base64 of ``<timestamp isoformat>|<id>`` of the last row returned.
"""

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import InstrumentedAttribute, Session

from xolotl.platform.errors import ValidationFailedError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(created_at: datetime, id_: UUID) -> str:
    raw = f"{created_at.isoformat()}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    msg = "invalid cursor"
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailedError(msg) from exc
    timestamp, separator, ident = raw.partition("|")
    if separator != "|":
        raise ValidationFailedError(msg)
    try:
        return datetime.fromisoformat(timestamp), UUID(ident)
    except ValueError as exc:
        raise ValidationFailedError(msg) from exc


@dataclass(frozen=True, slots=True)
class PageRequest:
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None


def page_request(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PageRequest:
    return PageRequest(limit=limit, cursor=cursor)


PageDep = Annotated[PageRequest, Depends(page_request)]


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def paginate[M](
    session: Session,
    stmt: Select[M],
    *,
    ts_col: InstrumentedAttribute[datetime],
    id_col: InstrumentedAttribute[UUID],
    key: Callable[[M], tuple[datetime, UUID]],
    page: PageRequest,
) -> tuple[list[M], str | None]:
    if page.cursor is not None:
        after_ts, after_id = decode_cursor(page.cursor)
        stmt = stmt.where(
            or_(ts_col < after_ts, and_(ts_col == after_ts, id_col < after_id))
        )
    rows = list(
        session.scalars(
            stmt.order_by(ts_col.desc(), id_col.desc()).limit(page.limit + 1)
        )
    )
    if len(rows) <= page.limit:
        return rows, None
    rows = rows[: page.limit]
    last_ts, last_id = key(rows[-1])
    return rows, encode_cursor(last_ts, last_id)
