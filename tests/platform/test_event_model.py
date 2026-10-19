"""``Event`` and its mapping to and from ``outbox`` rows (spec §9.2).

The second half pins the migrated 1c tables to the spec, independently of the
models, and covers what the schema-sync test cannot see: Alembic does not compare
primary keys or the predicate of a partial index.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.engine.interfaces import ReflectedIndex
from sqlalchemy.exc import IntegrityError

from tests.helpers import make_tenant, system_ctx
from xolotl.models import metadata
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import JobStatus, OutboxStatus, RunStatus, StepStatus
from xolotl.platform.events.event import Event, event_from_row, row_from_event
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.workflows.models import WorkflowRunRow, WorkflowStepRow

TIME = datetime(2026, 10, 19, 12, 30, 45, 123456, tzinfo=UTC)


def event_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "id": uuid7(),
        "type": "party.created.v1",
        "source": "xolotl.party",
        "tenant_id": uuid7(),
        "subject": "party/123",
        "time": TIME,
        "key": "party-123",
        "correlation_id": "req-42",
        "policy_snapshot_id": uuid7(),
        "data": {"party_id": str(uuid7()), "count": 3, "tags": ["a", "b"]},
    }
    return fields | overrides


def an_event(**overrides: object) -> Event:
    return Event.model_validate(event_fields(**overrides))


# ---- the row mapping ------------------------------------------------------------


def test_event_survives_the_row_mapping_unchanged():
    event = an_event()
    row = row_from_event(event)
    assert (row.tenant_id, row.id, row.type, row.key) == (
        event.tenant_id,
        event.id,
        event.type,
        event.key,
    )
    assert event_from_row(row) == event


def test_optional_fields_default_to_none_and_round_trip():
    required = {
        name: value
        for name, value in event_fields().items()
        if name not in {"correlation_id", "policy_snapshot_id"}
    }
    event = Event.model_validate(required)
    assert event.correlation_id is None
    assert event.policy_snapshot_id is None
    row = row_from_event(event)
    assert row.payload["correlation_id"] is None
    assert row.payload["policy_snapshot_id"] is None
    assert event_from_row(row) == event


def test_payload_holds_exactly_what_the_columns_do_not():
    event = an_event()
    payload = row_from_event(event).payload
    assert set(payload) == {
        "source",
        "subject",
        "time",
        "correlation_id",
        "policy_snapshot_id",
        "data",
    }
    assert payload["source"] == "xolotl.party"
    assert payload["subject"] == "party/123"
    assert payload["correlation_id"] == "req-42"
    assert payload["policy_snapshot_id"] == str(event.policy_snapshot_id)
    assert payload["data"] == event.data
    assert datetime.fromisoformat(cast("str", payload["time"])) == TIME


def test_payload_is_plain_json():
    payload = row_from_event(an_event()).payload
    assert json.loads(json.dumps(payload)) == payload


def test_the_instant_survives_a_non_utc_offset():
    mexico_city = timezone(timedelta(hours=-6))
    event = an_event(time=datetime(2026, 10, 19, 6, 30, tzinfo=mexico_city))
    restored = event_from_row(row_from_event(event))
    assert restored == event
    assert restored.time == datetime(2026, 10, 19, 12, 30, tzinfo=UTC)


def test_event_survives_the_database(engine: Engine):
    tenant_id = make_tenant("alpha")
    event = an_event(tenant_id=tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(row_from_event(event))
    with tenant_session(system_ctx(tenant_id)) as s:
        row = s.scalars(select(OutboxRow)).one()
        assert event_from_row(row) == event
        assert row.status is OutboxStatus.PENDING
        assert row.attempts == 0
        assert row.seq >= 1
        assert row.locked_until is None
        assert row.published_at is None


def test_seq_follows_insertion_order(engine: Engine):
    tenant_id = make_tenant("alpha")
    events = [an_event(tenant_id=tenant_id, key=f"k{n}") for n in range(3)]
    with tenant_session(system_ctx(tenant_id)) as s:
        for event in events:
            s.add(row_from_event(event))
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).order_by(OutboxRow.seq)).all()
    assert [event_from_row(row) for row in rows] == events
    seqs = [row.seq for row in rows]
    assert seqs == sorted(set(seqs))


# ---- validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"amount": 1.5},
        {"amount": 2.0},
        {"amount": -0.0},
        {"amount": 1e16},
        {"nested": {"amount": 0.1}},
        {"items": [1, 2.5]},
        {"deep": [{"x": [{"y": {"z": 6.25}}]}]},
    ],
)
def test_data_with_floats_is_rejected_at_any_depth(data: dict[str, object]):
    with pytest.raises(ValidationError, match="floats"):
        _ = an_event(data=data)


def test_data_accepts_ids_ints_strings_booleans_and_nulls():
    data = {
        "party_id": str(uuid7()),
        "count": 3,
        "big": 10**16,
        "ok": True,
        "note": None,
        "tags": ["a", "b"],
        "nested": {"n": [1, 2, {"deeper": "x"}]},
    }
    event = an_event(data=data)
    assert event.data == data
    assert event_from_row(row_from_event(event)) == event


def test_a_key_of_200_characters_is_accepted():
    assert an_event(key="k" * 200).key == "k" * 200


def test_a_key_longer_than_200_characters_is_rejected():
    with pytest.raises(ValidationError, match="at most 200 characters"):
        _ = an_event(key="k" * 201)


def test_a_key_must_not_be_empty():
    assert an_event(key="k").key == "k"
    with pytest.raises(ValidationError, match="at least 1 character"):
        _ = an_event(key="")


def test_row_from_event_rejects_floats_that_skipped_validation():
    # model_copy(update=...) does not validate, so a float can reach the mapping;
    # it must fail here, in the publisher's transaction, not at dispatch.
    event = an_event().model_copy(update={"data": {"amounts": [{"x": 1.5}]}})
    with pytest.raises(ValueError, match="floats"):
        _ = row_from_event(event)


@pytest.mark.parametrize(
    "name", ["id", "type", "source", "tenant_id", "subject", "time", "key", "data"]
)
def test_event_requires_the_spec_fields(name: str):
    fields = event_fields()
    del fields[name]
    with pytest.raises(ValidationError) as caught:
        _ = Event.model_validate(fields)
    assert [(e["loc"], e["type"]) for e in caught.value.errors()] == [
        ((name,), "missing")
    ]


@pytest.mark.parametrize("field", ["id", "key", "data", "correlation_id"])
def test_event_is_frozen(field: str):
    event = an_event()
    with pytest.raises(ValidationError, match="frozen"):
        setattr(event, field, None)


# ---- facts about the migrated tables Alembic cannot see -------------------------

NEW_TABLES = (
    "outbox",
    "event_consumption",
    "webhook_endpoint",
    "webhook_delivery",
    "job",
    "job_schedule",
    "workflow_run",
    "workflow_step",
    "workflow_signal",
    "notification",
)

# Every column default the spec gives, as Postgres prints it. No other column of the
# ten tables may have one: a missing default breaks raw INSERTs, an extra one hides
# a forgotten value.
SERVER_DEFAULTS = {
    ("outbox", "status"): "'PENDING'::character varying",
    ("outbox", "attempts"): "0",
    ("outbox", "created_at"): "now()",
    ("event_consumption", "consumed_at"): "now()",
    ("webhook_endpoint", "created_at"): "now()",
    ("webhook_delivery", "attempt"): "0",
    ("webhook_delivery", "created_at"): "now()",
    ("job", "status"): "'PENDING'::character varying",
    ("job", "attempts"): "0",
    ("job", "max_attempts"): "8",
    ("job", "run_after"): "now()",
    ("job", "created_at"): "now()",
    ("job_schedule", "enabled"): "true",
    ("workflow_run", "started_at"): "now()",
    ("workflow_step", "started_at"): "now()",
    ("workflow_signal", "received_at"): "now()",
    ("notification", "created_at"): "now()",
}

# ``job.lock_token`` is the one column beyond the spec (lease fencing, plan 1c E3).
NULLABLE_COLUMNS = {
    "outbox": {"locked_until", "last_error", "published_at"},
    "event_consumption": set(),
    "webhook_endpoint": set(),
    "webhook_delivery": {
        "response_code",
        "last_error",
        "next_attempt_at",
        "delivered_at",
    },
    "job": {
        "key",
        "locked_until",
        "locked_by",
        "lock_token",
        "last_error",
        "finished_at",
    },
    "job_schedule": {"last_run_at"},
    "workflow_run": {"current_step", "wake_at", "finished_at", "last_error"},
    "workflow_step": {"output", "error", "finished_at"},
    "workflow_signal": {"consumed_at"},
    "notification": {"recipient_user_id", "recipient_role", "read_at"},
}


def test_server_defaults_are_exactly_the_specs(admin_engine: Engine):
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name, column_name, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ANY(:tables)
                  AND column_default IS NOT NULL
                """
            ),
            {"tables": list(NEW_TABLES)},
        ).all()
    assert {(r[0], r[1]): r[2] for r in rows} == SERVER_DEFAULTS


def test_nullability_is_exactly_the_specs(admin_engine: Engine):
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ANY(:tables)
                  AND is_nullable = 'YES'
                """
            ),
            {"tables": list(NEW_TABLES)},
        ).all()
    nullable: dict[str, set[str]] = {table: set() for table in NEW_TABLES}
    for table, column in rows:
        nullable[table].add(column)
    assert nullable == NULLABLE_COLUMNS


def test_outbox_seq_is_a_by_default_identity(admin_engine: Engine):
    with admin_engine.connect() as conn:
        column = conn.execute(
            text(
                """
                SELECT data_type, is_identity, identity_generation
                FROM information_schema.columns
                WHERE table_name = 'outbox' AND column_name = 'seq'
                """
            )
        ).one()
    assert tuple(column) == ("bigint", "YES", "BY DEFAULT")


def test_primary_keys_are_the_specs(admin_engine: Engine):
    expected = {
        "outbox": ["tenant_id", "id"],
        "event_consumption": ["tenant_id", "consumer", "event_id"],
        "webhook_endpoint": ["tenant_id", "id"],
        "webhook_delivery": ["tenant_id", "id"],
        "job": ["tenant_id", "id"],
        "job_schedule": ["tenant_id", "id"],
        "workflow_run": ["tenant_id", "id"],
        "workflow_step": ["tenant_id", "run_id", "step_id", "attempt"],
        "workflow_signal": ["tenant_id", "id"],
        "notification": ["tenant_id", "id"],
    }
    insp = inspect(admin_engine)
    assert {
        table: insp.get_pk_constraint(table)["constrained_columns"]
        for table in NEW_TABLES
    } == expected


def test_unique_constraints_are_the_specs(admin_engine: Engine):
    expected = {
        "outbox": [["seq"]],
        "event_consumption": [],
        "webhook_endpoint": [],
        "webhook_delivery": [["tenant_id", "endpoint_id", "event_id"]],
        "job": [],
        "job_schedule": [["tenant_id", "name"]],
        "workflow_run": [["tenant_id", "definition", "business_key"]],
        "workflow_step": [],
        "workflow_signal": [],
        "notification": [],
    }
    insp = inspect(admin_engine)
    assert {
        table: [u["column_names"] for u in insp.get_unique_constraints(table)]
        for table in NEW_TABLES
    } == expected


def test_foreign_keys_are_composite_and_stay_inside_the_tenant(admin_engine: Engine):
    children = {
        "webhook_delivery": ("webhook_endpoint", ["tenant_id", "endpoint_id"]),
        "workflow_step": ("workflow_run", ["tenant_id", "run_id"]),
        "workflow_signal": ("workflow_run", ["tenant_id", "run_id"]),
    }
    insp = inspect(admin_engine)
    for table in NEW_TABLES:
        found = [
            (fk["referred_table"], fk["constrained_columns"], fk["referred_columns"])
            for fk in insp.get_foreign_keys(table)
        ]
        if table in children:
            parent, columns = children[table]
            assert found == [(parent, columns, ["tenant_id", "id"])], table
        else:
            assert found == [], table


def test_claim_and_wake_indexes_are_the_specs(admin_engine: Engine):
    insp = inspect(admin_engine)

    def index(table: str, name: str) -> ReflectedIndex:
        (found,) = (i for i in insp.get_indexes(table) if i["name"] == name)
        return found

    def where(found: ReflectedIndex) -> str:
        return found.get("dialect_options", {}).get("postgresql_where", "")

    pending = index("outbox", "ix_outbox_pending")
    assert pending["column_names"] == ["status", "created_at"]
    assert not pending["unique"]
    assert "'PENDING'" in where(pending)

    for table, name, columns in (
        ("job", "ix_job_claim", ["status", "run_after"]),
        ("workflow_run", "ix_workflow_run_wake", ["status", "wake_at"]),
        ("webhook_delivery", "ix_webhook_delivery_next", ["status", "next_attempt_at"]),
    ):
        found = index(table, name)
        assert found["column_names"] == columns, name
        assert not found["unique"], name
        assert not where(found), name

    dedup = index("job", "job_dedup")
    assert dedup["column_names"] == ["tenant_id", "kind", "key"]
    assert dedup["unique"]
    predicate = where(dedup)
    assert "key IS NOT NULL" in predicate
    assert "'PENDING'" in predicate
    assert "'RUNNING'" in predicate
    assert "'DONE'" not in predicate

    signals = index("workflow_signal", "ix_workflow_signal_pending")
    assert signals["column_names"] == [
        "tenant_id",
        "run_id",
        "name",
        "received_at",
        "id",
    ]
    assert not signals["unique"]
    assert where(signals) == "(consumed_at IS NULL)"


def test_the_oldest_unconsumed_signal_of_a_name_is_found_through_its_index(
    admin_engine: Engine,
):
    # The lookup the workflow runner makes on every wake and every signal: a run's
    # unconsumed signals of one name, oldest first. Sequential scans are switched off
    # so that a table this small still shows which index the planner can use.
    with admin_engine.connect() as conn:
        _ = conn.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            conn.execute(
                text(
                    """
                    EXPLAIN SELECT id FROM workflow_signal
                    WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
                      AND run_id = '22222222-2222-2222-2222-222222222222'
                      AND name = 'approve' AND consumed_at IS NULL
                    ORDER BY received_at, id LIMIT 1
                    """
                )
            ).scalars()
        )
    assert "ix_workflow_signal_pending" in plan
    assert "Sort" not in plan  # the index already holds the rows in that order


def test_model_partial_index_predicates_are_the_specs():
    # Alembic does not compare predicates, so nothing else ties the models to the
    # migration; the test above pins the migrated database, this one the models.
    wheres = {
        (table.name, index.name): str(index.dialect_options["postgresql"]["where"])
        for table in metadata().tables.values()
        for index in table.indexes
        if index.dialect_options["postgresql"]["where"] is not None
    }
    assert wheres == {
        ("outbox", "ix_outbox_pending"): "status = 'PENDING'",
        ("job", "job_dedup"): "key IS NOT NULL AND status IN ('PENDING','RUNNING')",
        ("workflow_signal", "ix_workflow_signal_pending"): "consumed_at IS NULL",
        ("unit_value", "ix_unit_value_lookup"): "superseded_by IS NULL",
    }


def _job(
    tenant_id: UUID,
    *,
    key: str | None,
    kind: str = "k.v1",
    status: JobStatus = JobStatus.PENDING,
) -> JobRow:
    return JobRow(tenant_id=tenant_id, kind=kind, key=key, payload={}, status=status)


def _add_jobs(tenant_id: UUID, *jobs: JobRow) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add_all(jobs)


def test_job_dedup_holds_while_a_keyed_job_is_pending_or_running(engine: Engine):
    tenant_id = make_tenant("alpha")
    _add_jobs(tenant_id, _job(tenant_id, key="slot-1"))
    with pytest.raises(IntegrityError, match="job_dedup"):
        _add_jobs(tenant_id, _job(tenant_id, key="slot-1"))
    with pytest.raises(IntegrityError, match="job_dedup"):
        _add_jobs(tenant_id, _job(tenant_id, key="slot-1", status=JobStatus.RUNNING))


def test_job_dedup_ignores_finished_jobs_null_keys_and_other_tenants(engine: Engine):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    _add_jobs(
        alpha,
        _job(alpha, key="slot-1", status=JobStatus.DONE),
        _job(alpha, key="slot-1", status=JobStatus.FAILED),
        _job(alpha, key="slot-1", status=JobStatus.DEAD),
    )
    _add_jobs(alpha, _job(alpha, key="slot-1"))  # a fresh run once the old ones ended
    _add_jobs(alpha, _job(alpha, key=None), _job(alpha, key=None))
    _add_jobs(beta, _job(beta, key="slot-1"))
    with tenant_session(system_ctx(alpha)) as s:
        assert len(s.scalars(select(JobRow)).all()) == 6
    with tenant_session(system_ctx(beta)) as s:
        assert len(s.scalars(select(JobRow)).all()) == 1


def test_job_dedup_is_per_kind(engine: Engine):
    tenant_id = make_tenant("alpha")
    _add_jobs(
        tenant_id,
        _job(tenant_id, key="slot-1"),
        _job(tenant_id, key="slot-1", kind="other.v1"),
    )


def test_a_step_without_output_stores_sql_null_not_json_null(engine: Engine):
    tenant_id = make_tenant("alpha")
    with tenant_session(system_ctx(tenant_id)) as s:
        run = WorkflowRunRow(
            tenant_id=tenant_id,
            definition="d.v1",
            business_key="b-1",
            status=RunStatus.RUNNING,
            state={},
        )
        s.add(run)
        s.flush()
        s.add(
            WorkflowStepRow(
                tenant_id=tenant_id,
                run_id=run.id,
                step_id="collect",
                attempt=1,
                status=StepStatus.RUNNING,
                output=None,
            )
        )
    with tenant_session(system_ctx(tenant_id)) as s:
        assert s.scalar(text("SELECT output IS NULL FROM workflow_step")) is True
        assert s.scalars(select(WorkflowStepRow)).one().output is None
