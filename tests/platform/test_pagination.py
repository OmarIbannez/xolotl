from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.helpers import new_tenant, staff_session
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import StaffUser
from xolotl.platform.api.pagination import (
    PageRequest,
    decode_cursor,
    encode_cursor,
    paginate,
)
from xolotl.platform.enums import Role
from xolotl.platform.errors import ValidationFailedError


def test_cursor_round_trip_is_url_safe():
    at = datetime(2026, 10, 18, 12, 30, 45, 123456, tzinfo=UTC)
    ident = uuid4()
    cursor = encode_cursor(at, ident)
    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor
    assert decode_cursor(cursor) == (at, ident)


def test_cursor_without_microseconds_drops_and_restores_padding():
    # No fractional part -> 62 raw bytes -> base64 pads with "=" (the 69-byte
    # payload above never does), so this is the case that needs rstrip and re-padding.
    at = datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)
    ident = uuid4()
    cursor = encode_cursor(at, ident)
    assert "=" not in cursor
    assert decode_cursor(cursor) == (at, ident)


@pytest.mark.parametrize(
    "bad", ["", "garbage", "bm90LWEtY3Vyc29y", "MjAyNi0xMC0xOHxub3QtYS11dWlk"]
)
def test_invalid_cursors_are_validation_failures(bad):
    with pytest.raises(ValidationFailedError):
        _ = decode_cursor(bad)


def _staff_page(s: Session, limit: int, cursor: str | None = None):
    return paginate(
        s,
        select(StaffUser),
        ts_col=StaffUser.created_at,
        id_col=StaffUser.id,
        key=lambda row: (row.created_at, row.id),
        page=PageRequest(limit=limit, cursor=cursor),
    )


def test_paginate_walks_rows_newest_first_with_id_tie_break(engine: Engine):
    created = new_tenant()
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        for n in range(4):
            _ = service.create_staff(
                s, email=f"u{n}@acme.mx", display_name=f"U{n}", roles=[Role.ANALYST]
            )
    # Stamp the five rows by id rank (earliest id first) so that created_at order
    # disagrees with id order and two rows tie: the earliest id is the oldest row,
    # the next one the newest, and the middle two share a timestamp.
    base = datetime(2026, 10, 18, 12, 0, 0, 123456, tzinfo=UTC)
    stamps = [base + timedelta(seconds=n) for n in (0, 3, 2, 2, 1)]
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        by_id = s.scalars(select(StaffUser).order_by(StaffUser.id)).all()
        for user, at in zip(by_id, stamps, strict=True):
            user.created_at = at
    seen: list = []
    cursor = None
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        expected = sorted(
            s.scalars(select(StaffUser)).all(),
            key=lambda u: (u.created_at, u.id),
            reverse=True,
        )
        ids = [u.id for u in expected]
        assert len({u.created_at for u in expected}) < len(expected)  # a tied pair
        assert ids != sorted(ids, reverse=True)  # created_at order is not id order
        everything, end = _staff_page(s, limit=len(ids))
        assert end is None  # exactly `limit` rows: no next page, so no cursor
        assert [u.id for u in everything] == ids
        most, more = _staff_page(s, limit=len(ids) - 1)
        assert more is not None  # one row left over: a next page exists
        assert [u.id for u in most] == ids[:-1]
        for _ in range(10):  # a cursor that never advances must fail, not hang
            rows, cursor = _staff_page(s, limit=2, cursor=cursor)
            seen.extend(rows)
            if cursor is None:
                break
        else:
            pytest.fail("pagination did not reach the last page within 10 pages")
    assert [u.id for u in seen] == ids
    assert len(seen) == 5
