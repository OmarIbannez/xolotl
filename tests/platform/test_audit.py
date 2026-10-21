import hashlib
import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
from queue import Queue
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue, ValidationError
from sqlalchemy import Connection, Engine, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.helpers import make_tenant, platform_ctx, staff_ctx, system_ctx
from xolotl.platform import clock, migrate
from xolotl.platform.audit import (
    GENESIS_HASH,
    AuditEvent,
    canonical_json,
    digest,
    record,
    verify_chain,
)
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.audit.partitions import (
    ensure_partitions,
    next_month,
    partition_name,
)
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.errors import ContextMissingError

GOLDEN_TENANT = UUID("0192f0c1-7e1a-7b2c-8d3e-4f5a6b7c8d9e")
UTC_MINUS_6 = timezone(timedelta(hours=-6))  # a golden row outside UTC
CONTENT_BROKEN = "content does not match its hash"


def _record(tenant_id, action: str, **kwargs) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return record(s, AuditEvent(action=action, **kwargs)).seq


def test_canonical_json_is_sorted_compact_and_utf8():
    assert canonical_json({"b": 1, "a": "ñ", "c": [1.5, None, True]}) == (
        b'{"a":"\xc3\xb1","b":1,"c":[1.5,null,true]}'
    )


@pytest.mark.parametrize(
    ("row", "payload", "expected"),
    [
        pytest.param(
            AuditEventRow(
                tenant_id=GOLDEN_TENANT,
                seq=7,
                at=datetime(2026, 10, 18, 6, 34, 56, 789012, tzinfo=UTC_MINUS_6),
                actor_type="STAFF",
                actor_id="0192f0c1-0000-7000-8000-000000000001",
                action="staff.created",
                entity_type="staff_user",
                entity_id="0192f0c1-0000-7000-8000-000000000002",
                detail={
                    "roles": ["ADMIN", "MLRO"],
                    "n": 3,
                    "ñandú": True,
                    "none": None,
                },
                prev_hash=bytes.fromhex("ab" * 32),
            ),
            (
                '["0192f0c1-7e1a-7b2c-8d3e-4f5a6b7c8d9e",7,'
                '"2026-10-18T12:34:56.789012+00:00","STAFF",'
                '"0192f0c1-0000-7000-8000-000000000001","staff.created","staff_user",'
                '"0192f0c1-0000-7000-8000-000000000002",'
                '{"n":3,"none":null,"roles":["ADMIN","MLRO"],"ñandú":true},'
                f'"{"ab" * 32}"]'
            ),
            "5b4f94b374fa3f4a024c4197baaa67378d5988e6da1bde25e95780f8a6ab86a7",
            id="full-row-outside-utc",
        ),
        pytest.param(
            AuditEventRow(
                tenant_id=GOLDEN_TENANT,
                seq=1,
                at=datetime(2026, 1, 1, tzinfo=UTC),
                actor_type="SYSTEM",
                actor_id="job:test",
                action="tenant.created",
                entity_type=None,
                entity_id=None,
                detail=None,
                prev_hash=GENESIS_HASH,
            ),
            (
                '["0192f0c1-7e1a-7b2c-8d3e-4f5a6b7c8d9e",1,"2026-01-01T00:00:00+00:00",'
                '"SYSTEM","job:test","tenant.created",null,null,null,'
                f'"{"00" * 32}"]'
            ),
            "57fb3efefd77fb2dbb1d06b92b347cf79bf2b2f99d8e019783e11f3c4915fb78",
            id="genesis-row-with-nulls",
        ),
    ],
)
def test_digest_matches_the_golden_vector(row: AuditEventRow, payload: str, expected):
    # The payload spells the canonical form out: field order, UTC ISO-8601 time,
    # sorted compact UTF-8 JSON and the previous hash in hex. No database involved.
    assert hashlib.sha256(payload.encode()).hexdigest() == expected
    assert digest(row).hex() == expected


def test_chain_links_per_tenant(engine: Engine):
    a, b = make_tenant("alpha"), make_tenant("beta")
    assert _record(a, "test.one") == 1
    assert (
        _record(a, "test.two", entity_type="x", entity_id="1", detail={"k": "v"}) == 2
    )
    assert _record(b, "test.one") == 1  # tenant B has its own chain
    with tenant_session(system_ctx(a)) as s:
        rows = s.scalars(select(AuditEventRow).order_by(AuditEventRow.seq)).all()
        assert [r.seq for r in rows] == [1, 2]
        assert rows[0].prev_hash == GENESIS_HASH
        assert rows[1].prev_hash == rows[0].hash
        assert rows[0].actor_type == "SYSTEM"
        assert rows[0].actor_id == "job:test"
        assert rows[1].detail == {"k": "v"}
        assert verify_chain(s).model_dump(exclude={"tenant_id"}) == {
            "valid": True,
            "entries_checked": 2,
            "first_broken_seq": None,
            "reason": None,
        }


def test_record_uses_the_bound_actor(engine: Engine):
    a = make_tenant("alpha")
    with tenant_session(staff_ctx(a, actor_id="staff:42", roles=[Role.ADMIN])) as s:
        row = record(s, AuditEvent(action="auth.login_succeeded"))
        assert (row.actor_type, row.actor_id) == ("STAFF", "staff:42")


def test_record_requires_a_tenant_scoped_context(engine: Engine):
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        _ = record(s, AuditEvent(action="x"))


@pytest.mark.parametrize(
    "detail",
    [
        {"amount": 1e16},
        {"a": [{"b": -0.0}]},
        {"ratio": 0.5},
        {"xs": [1, 2.5]},
    ],
    ids=["integral-float", "nested-negative-zero", "fraction", "float-in-list"],
)
def test_audit_event_rejects_floats_anywhere_in_detail(detail):
    with pytest.raises(ValidationError, match="must not contain floats"):
        _ = AuditEvent(action="x", detail=detail)


def test_detail_without_floats_round_trips_through_jsonb(engine: Engine):
    a = make_tenant("alpha")
    detail: dict[str, JsonValue] = {
        "count": 3,
        "big": 10**18,
        "negative": -7,
        "name": "ñandú",
        "yes": True,
        "no": False,
        "nothing": None,
        "nested": {"items": [1, "two", None, {"deep": [True, {"k": "v"}]}], "e": {}},
        "empty_list": [],
    }
    _ = _record(a, "test.detail", detail=detail)
    with tenant_session(system_ctx(a)) as s:
        stored = s.scalars(select(AuditEventRow)).one()
        assert stored.detail == detail
        assert verify_chain(s).valid is True


def test_concurrent_writers_get_distinct_sequence_numbers(engine: Engine):
    a = make_tenant("alpha")

    def work(n: int) -> list[int]:
        return [_record(a, f"concurrent.{n}.{i}") for i in range(5)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        seqs = sorted(s for result in pool.map(work, range(4)) for s in result)
    assert seqs == list(range(1, 21))
    with tenant_session(system_ctx(a)) as s:
        assert verify_chain(s).valid is True


def test_trigger_blocks_update_delete_and_truncate(
    engine: Engine, admin_engine: Engine
):
    a = make_tenant("alpha")
    _ = _record(a, "test.one")
    for statement in (
        "UPDATE audit_event SET action = 'x'",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
        "TRUNCATE audit_event_default",
    ):
        with (
            pytest.raises(ProgrammingError, match="append-only"),
            admin_engine.begin() as conn,
        ):
            _ = conn.execute(text(statement))


def _tamper(admin_engine: Engine, statement: str, params: dict[str, object]) -> None:
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(text(statement), params)


TAMPERS: dict[str, Callable[[AuditEventRow], object]] = {
    "tenant_id": lambda _row: uuid4(),
    "seq": lambda row: row.seq + 10,
    "at": lambda row: row.at + timedelta(microseconds=1),
    "actor_type": lambda _row: "API_CLIENT",
    "actor_id": lambda row: row.actor_id + "x",
    "action": lambda _row: "forged",
    "entity_type": lambda _row: "forged",
    "entity_id": lambda _row: "forged",
    "detail": lambda _row: {"k": "forged"},
    "prev_hash": lambda _row: b"\x09" * 32,
    "hash": lambda _row: b"\x0a" * 32,
}


@pytest.mark.parametrize(
    ("column", "broken_at", "reason"),
    [
        # Moving the row to another tenant takes it out of this chain: a gap.
        ("tenant_id", 3, "sequence gap or duplicate"),
        ("seq", 3, "sequence gap or duplicate"),
        ("at", 2, CONTENT_BROKEN),
        ("actor_type", 2, CONTENT_BROKEN),
        ("actor_id", 2, CONTENT_BROKEN),
        ("action", 2, CONTENT_BROKEN),
        ("entity_type", 2, CONTENT_BROKEN),
        ("entity_id", 2, CONTENT_BROKEN),
        ("detail", 2, CONTENT_BROKEN),
        ("prev_hash", 2, "prev_hash does not match the previous entry"),
        ("hash", 2, CONTENT_BROKEN),
    ],
)
def test_verify_detects_tampering_with_any_digest_column(
    engine: Engine, admin_engine: Engine, column: str, broken_at: int, reason: str
):
    assert set(TAMPERS) == {c.name for c in AuditEventRow.__table__.columns} - {"id"}
    a = make_tenant("alpha")
    for n in range(3):
        _ = _record(a, f"test.{n}", entity_type="x", entity_id=str(n), detail={"k": n})
    with tenant_session(system_ctx(a)) as s:
        middle = s.scalars(select(AuditEventRow).where(AuditEventRow.seq == 2)).one()
        value = TAMPERS[column](middle)
    with admin_engine.begin() as conn:
        # Replica mode switches the append-only trigger off: a superuser's rewrite.
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(
            update(AuditEventRow)
            .where(AuditEventRow.tenant_id == a, AuditEventRow.seq == 2)
            .values({column: value})
        )
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s)
    assert (result.valid, result.first_broken_seq, result.reason) == (
        False,
        broken_at,
        reason,
    )


def test_verify_detects_a_deleted_row(engine: Engine, admin_engine: Engine):
    a = make_tenant("alpha")
    for n in range(3):
        _ = _record(a, f"test.{n}")
    _tamper(
        admin_engine,
        "DELETE FROM audit_event WHERE tenant_id = :t AND seq = 2",
        {"t": a},
    )
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s)
    assert (result.valid, result.first_broken_seq) == (False, 3)
    assert result.reason == "sequence gap or duplicate"


def test_verify_chain_refuses_a_tenant_the_session_is_not_bound_to(engine: Engine):
    a, b = make_tenant("alpha"), make_tenant("beta")
    _ = _record(a, "test.one")
    _ = _record(b, "test.one")
    # RLS hides b's rows from a's session: that must not read as an empty, valid chain.
    with tenant_session(system_ctx(a)) as s, pytest.raises(ContextMissingError) as err:
        _ = verify_chain(s, b)
    assert str(err.value) == f"session is bound to tenant {a}, cannot verify tenant {b}"


def test_verify_chain_requires_a_tenant_scoped_context(engine: Engine):
    a = make_tenant("alpha")
    _ = _record(a, "test.one")
    with tenant_session(platform_ctx()) as s:
        with pytest.raises(ContextMissingError):
            _ = verify_chain(s)
        # RLS shows a tenant-less session no rows at all: naming the tenant cannot help.
        with pytest.raises(ContextMissingError):
            _ = verify_chain(s, a)


def test_verify_chain_reports_a_genuinely_empty_chain_as_valid(engine: Engine):
    a = make_tenant("alpha")
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s)
    assert result.tenant_id == a
    assert result.model_dump(exclude={"tenant_id"}) == {
        "valid": True,
        "entries_checked": 0,
        "first_broken_seq": None,
        "reason": None,
    }


def test_verify_chain_accepts_the_sessions_own_tenant(engine: Engine):
    a = make_tenant("alpha")
    _ = _record(a, "test.one")
    _ = _record(a, "test.two")
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s, a)
    assert (result.tenant_id, result.valid, result.entries_checked) == (a, True, 2)


def test_ensure_partitions_is_idempotent_and_routes_rows(
    engine: Engine, admin_engine: Engine, monkeypatch
):
    with admin_engine.begin() as conn:
        report = ensure_partitions(conn, start=date(2031, 1, 1), months_ahead=1)
        assert report.created == ["audit_event_y2031m01", "audit_event_y2031m02"]
        again = ensure_partitions(conn, start=date(2031, 1, 1), months_ahead=1)
        assert (again.created, again.skipped) == ([], {})
        rls = conn.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity FROM pg_class
                WHERE relname = 'audit_event_y2031m02'
                """
            )
        ).one()
        assert tuple(rls) == (True, True)
    a = make_tenant("alpha")
    monkeypatch.setattr(clock, "now", lambda: datetime(2031, 2, 14, 12, 0, tzinfo=UTC))
    with tenant_session(system_ctx(a)) as s:
        row = record(s, AuditEvent(action="future"))
        placed = s.scalar(
            text("SELECT tableoid::regclass::text FROM audit_event WHERE id = :id"),
            {"id": row.id},
        )
    assert placed == partition_name(date(2031, 2, 1))


def test_upgrade_recreates_missing_partitions(admin_engine: Engine):
    month = clock.now().date().replace(day=1)
    expected: list[str] = []
    for _ in range(4):  # this month and the three after it
        expected.append(partition_name(month))
        month = next_month(month)
    with admin_engine.begin() as conn:
        for name in expected:
            _ = conn.execute(text(f"DROP TABLE {name}"))
    try:
        migrate.upgrade()  # already at head: only the partition check has work to do
        with admin_engine.connect() as conn:
            present = conn.scalars(
                text(
                    """
                    SELECT relname FROM pg_class
                    WHERE relname = ANY(:names) AND relispartition
                      AND relrowsecurity AND relforcerowsecurity
                    """
                ),
                {"names": expected},
            ).all()
        assert sorted(present) == sorted(expected)
    finally:
        with admin_engine.begin() as conn:
            _ = ensure_partitions(conn, months_ahead=3)


def _drop_partitions(admin_engine: Engine, names: Iterable[str]) -> None:
    with admin_engine.begin() as conn:
        for name in names:
            _ = conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _backend_pid(conn: Connection) -> int:
    return conn.scalar(text("SELECT pg_backend_pid()"))


LOCK_WAIT = text(
    """
    SELECT count(*) FROM pg_stat_activity
    WHERE pid = :pid AND wait_event_type = 'Lock' AND strpos(query, :fragment) > 0
    """
)


def _await_lock_wait(admin_engine: Engine, pid: int, fragment: str = "") -> None:
    # Returns once backend ``pid`` waits for a lock in a statement containing
    # ``fragment``. pg_stat_activity is read once per transaction: poll in new ones.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with admin_engine.connect() as conn:
            if conn.scalar(LOCK_WAIT, {"pid": pid, "fragment": fragment}):
                return
        time.sleep(0.02)
    pytest.fail(f"backend {pid} never waited for a lock in {fragment!r}")


def test_rows_in_the_default_partition_skip_their_month_only(
    engine: Engine, admin_engine: Engine, monkeypatch, caplog
):
    # An API that outlives its partitions writes the month's rows into
    # audit_event_default, and Postgres then refuses that month's partition.
    stuck = next_month(clock.now().date().replace(day=1))
    months = [stuck, next_month(stuck), next_month(next_month(stuck))]
    names = [partition_name(month) for month in months]
    _drop_partitions(admin_engine, names)
    tenant_id = make_tenant("alpha")
    with monkeypatch.context() as patched:
        patched.setattr(
            clock, "now", lambda: datetime(stuck.year, stuck.month, 15, tzinfo=UTC)
        )
        _ = _record(tenant_id, "written.past.the.partitions")
    try:
        with admin_engine.connect() as conn:
            placed = conn.scalar(
                text("SELECT tableoid::regclass::text FROM audit_event")
            )
        assert placed == "audit_event_default"
        with admin_engine.begin() as conn:
            report = ensure_partitions(conn, months_ahead=3)
        assert report.skipped == {names[0]: "23514"}
        assert report.created == names[1:]
        logged = [
            (r.levelno, names[0] in r.getMessage())
            for r in caplog.records
            if r.name == "xolotl.platform.partitions"
        ]
        assert logged == [(logging.ERROR, True)]
        migrate.upgrade()  # `db upgrade`, and so `serve`, carries on
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
            _ = conn.execute(text("DELETE FROM audit_event_default"))
        with admin_engine.begin() as conn:
            _ = ensure_partitions(conn, months_ahead=3)


def test_concurrent_partition_checks_take_turns(admin_engine: Engine):
    # Two processes starting together, or a start and the monthly job: the second
    # waits for the first to commit, then finds the partitions instead of racing it.
    start = date(2036, 1, 1)
    names = [partition_name(start), partition_name(next_month(start))]
    pids: Queue[int] = Queue()

    def second_caller():
        with admin_engine.begin() as conn:
            pids.put(_backend_pid(conn))
            return ensure_partitions(conn, start=start, months_ahead=1)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with admin_engine.begin() as conn:
                first = ensure_partitions(conn, start=start, months_ahead=1)
                second = pool.submit(second_caller)
                _await_lock_wait(admin_engine, pids.get(timeout=10))
            result = second.result(timeout=30)
        assert first.created == names
        assert (result.created, result.skipped) == ([], {})
    finally:
        _drop_partitions(admin_engine, names)


def test_a_month_held_up_by_a_lock_on_audit_event_is_skipped_after_the_timeout(
    admin_engine: Engine,
):
    # Creating a partition needs ACCESS EXCLUSIVE on audit_event, and while it waits
    # every audit read and write queues behind it: give up on that month instead.
    start = date(2041, 1, 1)
    months = [start, next_month(start), next_month(next_month(start))]
    names = [partition_name(month) for month in months]
    pids: Queue[int] = Queue()

    def create():
        with admin_engine.begin() as conn:
            pids.put(_backend_pid(conn))
            began = time.perf_counter()
            report = ensure_partitions(conn, start=start, months_ahead=2)
            return report, time.perf_counter() - began

    try:
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            admin_engine.connect() as reader,
        ):
            _ = reader.execute(text("LOCK TABLE audit_event IN ACCESS SHARE MODE"))
            pending = pool.submit(create)
            # The first month gives up after the lock timeout. Once the second one
            # waits, the reader finishes and the remaining months go through.
            _await_lock_wait(
                admin_engine, pids.get(timeout=10), f"CREATE TABLE {names[1]} "
            )
            reader.commit()
            report, elapsed = pending.result(timeout=30)
        assert report.skipped == {names[0]: "55P03"}
        assert report.created == names[1:]
        assert elapsed < 15, f"ensure_partitions took {elapsed:.1f}s"
    finally:
        _drop_partitions(admin_engine, names)


def test_ensure_partitions_raises_any_other_error(admin_engine: Engine):
    # Another partition already covers the month under a different name: a schema
    # problem to look at, not a month to skip.
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                CREATE TABLE audit_event_overlap PARTITION OF audit_event
                FOR VALUES FROM ('2042-01-01 00:00:00+00') TO ('2042-02-01 00:00:00+00')
                """
            )
        )
    try:
        with (
            pytest.raises(DBAPIError, match="would overlap"),
            admin_engine.begin() as conn,
        ):
            _ = ensure_partitions(conn, start=date(2042, 1, 1), months_ahead=0)
    finally:
        _drop_partitions(admin_engine, ["audit_event_overlap"])


def test_ensure_partitions_leaves_the_callers_lock_timeout_alone(admin_engine: Engine):
    start = date(2043, 1, 1)
    try:
        with admin_engine.begin() as conn:
            _ = conn.execute(text("SET LOCAL lock_timeout = '42s'"))
            report = ensure_partitions(conn, start=start, months_ahead=0)
            assert report.created == [partition_name(start)]
            assert conn.scalar(text("SHOW lock_timeout")) == "42s"
    finally:
        _drop_partitions(admin_engine, [partition_name(start)])
