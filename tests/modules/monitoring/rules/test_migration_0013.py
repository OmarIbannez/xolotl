"""Migration 0013: rule templates, versions, hits, captures and the counterparty index.

Rows are built with the isolation factories inside the tenant's own session, so
every check below is the one the application role meets. The round trip moves the
session database back to 0012 and forward again with real commits; the ``database``
fixture always brings it back to head afterwards.
"""

from collections.abc import Callable, Generator
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from tests.helpers import system_ctx
from tests.isolation import factories
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus, CaptureKind
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.rules.enums import RuleVersionStatus
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
    RuleHit,
    RuleHitTransaction,
    RuleSimulation,
    TierLimitNotice,
)
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.modules.party.models import Party
from xolotl.modules.screening.counterparty.models import (
    Counterparty,
    CounterpartyNameIndex,
)
from xolotl.modules.tenants.models import StaffUser
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import migrate
from xolotl.platform.db import APP_ROLE, tenant_session
from xolotl.platform.ids import uuid7

REVISION, BEFORE = "0013_rules_captures", "0012_transactions"
SHARED = ("rule_template", "rule_template_version")
TENANT_TABLES = (
    "monitoring_rule",
    "monitoring_rule_version",
    "rule_simulation",
    "rule_assignment",
    "rule_hit",
    "rule_hit_transaction",
    "capture_group",
    "reportable_event",
    "tier_limit_notice",
    "capture_period",
    "counterparty_name_index",
)
TABLES = SHARED + TENANT_TABLES
KEY = ["tenant_id", "id"]
TXN_KEY = ["tenant_id", "occurred_at", "id"]
TXN_LINK = ["tenant_id", "occurred_at", "transaction_id"]
# The foreign keys named by hand (their conventional name would pass 63 bytes, or
# the plan pins a shorter one): table -> name -> (columns, referred table, columns).
NAMED_FKS = {
    "monitoring_rule_version": {
        "fk_rule_version_retired_by": (
            ["tenant_id", "retired_by_version_id"],
            "monitoring_rule_version",
            KEY,
        ),
        "fk_rule_version_simulation": (
            ["tenant_id", "simulation_id"],
            "rule_simulation",
            KEY,
        ),
        "fk_rule_version_template_version": (
            ["template_version_id"],
            "rule_template_version",
            ["id"],
        ),
    },
    "rule_simulation": {
        "fk_rule_simulation_rule_version": (
            ["tenant_id", "rule_version_id"],
            "monitoring_rule_version",
            KEY,
        ),
    },
    "rule_assignment": {
        "fk_rule_assignment_rule_version": (
            ["tenant_id", "rule_version_id"],
            "monitoring_rule_version",
            KEY,
        ),
    },
    "rule_hit": {
        "fk_rule_hit_first_transaction": (
            ["tenant_id", "first_occurred_at", "first_transaction_id"],
            "transaction",
            TXN_KEY,
        ),
    },
    "rule_hit_transaction": {
        "fk_rule_hit_transaction_txn": (TXN_LINK, "transaction", TXN_KEY),
    },
    "capture_group": {
        "fk_capture_group_superseded_by": (
            ["tenant_id", "superseded_by_id"],
            "capture_group",
            KEY,
        ),
    },
    "reportable_event": {
        "fk_reportable_event_transaction": (TXN_LINK, "transaction", TXN_KEY),
        "fk_reportable_event_supersedes": (
            ["tenant_id", "supersedes_id"],
            "reportable_event",
            KEY,
        ),
        "fk_reportable_event_superseded_by": (
            ["tenant_id", "superseded_by_id"],
            "reportable_event",
            KEY,
        ),
        "fk_reportable_event_policy_snapshot": (
            ["tenant_id", "policy_snapshot_id"],
            "policy_snapshot",
            KEY,
        ),
        "fk_reportable_event_relationship": (
            ["tenant_id", "relationship_id"],
            "customer_relationship",
            KEY,
        ),
    },
    "tier_limit_notice": {
        "fk_tier_limit_notice_relationship": (
            ["tenant_id", "relationship_id"],
            "customer_relationship",
            KEY,
        ),
    },
    "counterparty_name_index": {
        "fk_counterparty_name_index_counterparty": (
            ["tenant_id", "counterparty_id"],
            "counterparty",
            KEY,
        ),
    },
    "screening_subject": {
        "fk_screening_subject_counterparty": (
            ["tenant_id", "counterparty_id"],
            "counterparty",
            KEY,
        ),
    },
    "counterparty": {
        "fk_counterparty_subject": (
            ["tenant_id", "screening_subject_id"],
            "screening_subject",
            KEY,
        ),
    },
}
FORCED = text(
    """
    SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relname = ANY(:names) AND relkind = 'r'
    """
)
SUBJECT_CHECKS = text(
    """
    SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
    WHERE conrelid = 'screening_subject'::regclass AND contype = 'c'
    """
)
# 0010 named its check through the naming convention, which prefixed it twice.
CHECK_0010 = "ck_screening_subject_ck_screening_subject_kind_input"
SUBJECT = text(
    """
    INSERT INTO screening_subject (tenant_id, id, kind, party_id, counterparty_id)
    VALUES (:tenant, :id, 'COUNTERPARTY', :party, :counterparty)
    """
)

type Factory = Callable[[Session, UUID], None]


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def tables_present(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names()) & set(TABLES)


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


@pytest.fixture
def tenant_id(engine: Engine, tenant: CreatedTenant) -> UUID:
    return tenant.tenant_id


def _new[T](s: Session, tenant_id: UUID, model: type[T], make: Factory) -> T:
    """Run a factory and return the one row of ``model`` it added.

    Returns:
        The new row.

    """
    before = set(s.scalars(select(model)).all())
    make(s, tenant_id)
    (row,) = set(s.scalars(select(model)).all()) - before
    return row


def _refused(tenant_id: UUID, constraint: str, build: Callable[[Session], object]):
    with (
        pytest.raises(IntegrityError, match=constraint),
        tenant_session(system_ctx(tenant_id)) as s,
    ):
        _ = build(s)
        s.flush()


def _approved(
    s: Session, tenant_id: UUID, status: RuleVersionStatus
) -> MonitoringRuleVersion:
    """Move a new version to ``status`` with a second user and a simulation.

    Returns:
        The version, flushed.

    """
    simulation = _new(s, tenant_id, RuleSimulation, factories.rule_simulation)
    approver = _new(s, tenant_id, StaffUser, factories.staff_user)
    version = s.get_one(MonitoringRuleVersion, (tenant_id, simulation.rule_version_id))
    version.status = status
    version.approved_by = approver.id
    version.simulation_id = simulation.id
    s.flush()
    return version


def test_0013_follows_0012():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_the_thirteen_tables_exist_and_the_tenant_ones_force_rls(
    admin_engine: Engine,
):
    with admin_engine.connect() as conn:
        rows = conn.execute(FORCED, {"names": list(TABLES)}).all()
        granted = conn.execute(
            text(
                """
                SELECT c.relname, p FROM pg_class c
                CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
                WHERE c.relname = ANY(:names)
                  AND has_table_privilege(:role, c.oid, p)
                """
            ),
            {"names": list(SHARED), "role": APP_ROLE},
        ).all()
    assert {row[0]: (row[1], row[2]) for row in rows} == {
        **dict.fromkeys(SHARED, (False, False)),
        **dict.fromkeys(TENANT_TABLES, (True, True)),
    }
    assert sorted((row[0], row[1]) for row in granted) == [
        (name, "SELECT") for name in SHARED
    ]


def test_the_composite_foreign_keys_keep_references_inside_the_tenant(
    admin_engine: Engine,
):
    with admin_engine.connect() as conn:
        inspector = inspect(conn)
        fks = {
            table: inspector.get_foreign_keys(table)
            for table in {*TENANT_TABLES, *NAMED_FKS}
        }
    for table, named in NAMED_FKS.items():
        found = {
            fk["name"]: (
                fk["constrained_columns"],
                fk["referred_table"],
                fk["referred_columns"],
            )
            for fk in fks[table]
        }
        for name, shape in named.items():
            assert found.get(name) == shape, (table, name)
    for table in TENANT_TABLES:
        for fk in fks[table]:
            if fk["referred_table"] in SHARED:
                assert fk["constrained_columns"][0] != "tenant_id", fk["name"]
                continue
            assert fk["constrained_columns"][0] == "tenant_id", fk["name"]
            assert fk["referred_columns"] in (KEY, TXN_KEY), fk["name"]


def test_the_approver_must_differ_from_the_author(tenant_id: UUID):
    def same_author(s: Session) -> object:
        version = _new(
            s, tenant_id, MonitoringRuleVersion, factories.monitoring_rule_version
        )
        version.approved_by = version.author_id
        return version

    _refused(tenant_id, "ck_monitoring_rule_version_approver_not_author", same_author)


def test_an_approved_or_active_version_needs_an_approver_and_a_simulation(
    tenant_id: UUID,
):
    def unsimulated(status: RuleVersionStatus) -> Callable[[Session], object]:
        def build(s: Session) -> object:
            version = _new(
                s, tenant_id, MonitoringRuleVersion, factories.monitoring_rule_version
            )
            approver = _new(s, tenant_id, StaffUser, factories.staff_user)
            version.status = status
            version.approved_by = approver.id
            return version

        return build

    def unapproved(status: RuleVersionStatus) -> Callable[[Session], object]:
        def build(s: Session) -> object:
            version = _approved(s, tenant_id, status)
            version.approved_by = None
            return version

        return build

    for status in (RuleVersionStatus.APPROVED, RuleVersionStatus.ACTIVE):
        for build in (unsimulated(status), unapproved(status)):
            _refused(
                tenant_id, "ck_monitoring_rule_version_approved_needs_simulation", build
            )
    with tenant_session(system_ctx(tenant_id)) as s:
        version = _approved(s, tenant_id, RuleVersionStatus.APPROVED)
    assert version.simulation_id is not None


def test_one_active_version_per_rule(tenant_id: UUID):
    with tenant_session(system_ctx(tenant_id)) as s:
        rule_id = _approved(s, tenant_id, RuleVersionStatus.ACTIVE).rule_id

    def onto_the_rule(status: RuleVersionStatus) -> Callable[[Session], object]:
        def build(s: Session) -> object:
            version = _approved(s, tenant_id, RuleVersionStatus.APPROVED)
            return s.execute(
                update(MonitoringRuleVersion)
                .where(MonitoringRuleVersion.id == version.id)
                .values(rule_id=rule_id, version=2, status=status)
            )

        return build

    _refused(
        tenant_id, "ux_rule_version_active", onto_the_rule(RuleVersionStatus.ACTIVE)
    )
    # A retired version beside the active one is allowed.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = onto_the_rule(RuleVersionStatus.RETIRED)(s)
        versions = s.scalars(
            select(MonitoringRuleVersion.status).where(
                MonitoringRuleVersion.rule_id == rule_id
            )
        ).all()
    assert sorted(versions) == [RuleVersionStatus.ACTIVE, RuleVersionStatus.RETIRED]


def test_a_hit_is_unique_per_version_and_dedup_key(tenant_id: UUID):
    with tenant_session(system_ctx(tenant_id)) as s:
        hit = _new(s, tenant_id, RuleHit, factories.rule_hit)
        values = {
            column.key: getattr(hit, column.key)
            for column in RuleHit.__table__.columns
            if column.key not in {"id", "created_at"}
        }

    _refused(
        tenant_id,
        "uq_rule_hit_tenant_id_rule_version_id_dedup_key",
        lambda s: s.add(RuleHit(**values)),
    )


def test_one_event_per_transaction_per_capture(tenant_id: UUID):
    with tenant_session(system_ctx(tenant_id)) as s:
        event = _new(s, tenant_id, ReportableEvent, factories.reportable_event)
        values = {
            column.key: getattr(event, column.key)
            for column in ReportableEvent.__table__.columns
            if column.key not in {"id", "captured_at"}
        }
    values["capture_kind"] = CaptureKind.FEE_THRESHOLD

    _refused(
        tenant_id,
        "uq_reportable_event_tenant_id_capture_code_transaction_id",
        lambda s: s.add(ReportableEvent(**values)),
    )


def test_one_open_group_per_capture_key_and_window(tenant_id: UUID):
    with tenant_session(system_ctx(tenant_id)) as s:
        group = _new(s, tenant_id, CaptureGroup, factories.capture_group)
        group_id = group.id
        values = {
            "tenant_id": tenant_id,
            "capture_code": group.capture_code,
            "key_kind": KeyKind.RELATIONSHIP,
            "key": group.key,
            "period_key": group.period_key,
            "window_start": group.window_start,
            "window_end": group.window_end,
        }

    _refused(
        tenant_id, "ux_capture_group_open", lambda s: s.add(CaptureGroup(**values))
    )
    # A superseded group leaves room for its replacement.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(CaptureGroup)
            .where(CaptureGroup.id == group_id)
            .values(status=CaptureGroupStatus.SUPERSEDED)
        )
        replacement = CaptureGroup(**values)
        s.add(replacement)
        s.flush()
        assert replacement.status == CaptureGroupStatus.OPEN


def test_the_subject_check_admits_counterparty_subjects(tenant_id: UUID):
    subject_id = uuid7()
    with tenant_session(system_ctx(tenant_id)) as s:
        counterparty_id = _new(s, tenant_id, Counterparty, factories.counterparty).id
        spare_id = _new(s, tenant_id, Counterparty, factories.counterparty).id
        party_id = _new(s, tenant_id, Party, factories.party).id
        _ = s.execute(
            SUBJECT,
            {
                "tenant": tenant_id,
                "id": subject_id,
                "party": None,
                "counterparty": counterparty_id,
            },
        )
        _ = s.execute(
            update(Counterparty)
            .where(Counterparty.id == counterparty_id)
            .values(screening_subject_id=subject_id)
        )
    for constraint, party, counterparty in (
        ("ck_screening_subject_kind_input", party_id, spare_id),
        ("ck_screening_subject_kind_input", None, None),
        ("uq_screening_subject_counterparty", None, counterparty_id),
        ("fk_screening_subject_counterparty", None, uuid7()),
    ):
        params = {
            "tenant": tenant_id,
            "id": uuid7(),
            "party": party,
            "counterparty": counterparty,
        }
        _refused(tenant_id, constraint, lambda s, p=params: s.execute(SUBJECT, p))

    def dangling_subject(s: Session) -> object:
        return s.execute(
            update(Counterparty)
            .where(Counterparty.id == spare_id)
            .values(screening_subject_id=uuid7())
        )

    _refused(tenant_id, "fk_counterparty_subject", dangling_subject)


def test_the_name_index_has_its_gin_indexes(admin_engine: Engine):
    with admin_engine.connect() as conn:
        definitions = dict(
            conn.execute(
                text(
                    """
                    SELECT indexname, indexdef FROM pg_indexes
                    WHERE tablename = 'counterparty_name_index'
                    """
                )
            ).all()
        )
    assert definitions["ix_cni_phonetic"].endswith("USING gin (phonetic_keys)")
    assert definitions["ix_cni_trgm"].endswith("USING gin (norm_name gin_trgm_ops)")


def test_the_hit_capture_and_version_indexes_are_the_specs(admin_engine: Engine):
    with admin_engine.connect() as conn:
        definitions = dict(
            conn.execute(
                text(
                    """
                    SELECT indexname, indexdef FROM pg_indexes
                    WHERE tablename = ANY(:names)
                    """
                ),
                {"names": list(TENANT_TABLES)},
            ).all()
        )
    expected = {
        "ux_rule_version_active": (
            "UNIQUE INDEX",
            "(tenant_id, rule_id) WHERE ((status)::text = 'ACTIVE'::text)",
        ),
        "ix_rule_hit_queue": ("INDEX", "(tenant_id, severity, created_at DESC)"),
        "ix_rule_hit_party": ("INDEX", "(tenant_id, party_id, created_at DESC)"),
        "ix_rule_hit_rule_period": (
            "INDEX",
            "(tenant_id, rule_version_id, created_at)",
        ),
        "ix_rule_hit_transaction_txn": ("INDEX", "(tenant_id, transaction_id)"),
        "ux_capture_group_open": (
            "UNIQUE INDEX",
            (
                "(tenant_id, capture_code, key_kind, key, window_start) "
                "WHERE ((status)::text <> 'SUPERSEDED'::text)"
            ),
        ),
        "ix_reportable_event_period": (
            "INDEX",
            "(tenant_id, report_code, period_key, status)",
        ),
        "ix_reportable_event_transaction": ("INDEX", "(tenant_id, transaction_id)"),
    }
    for name, (kind, tail) in expected.items():
        definition = definitions[name]
        assert definition.startswith(f"CREATE {kind} {name} "), definition
        assert definition.endswith(tail), definition


def test_the_app_role_reads_but_never_writes_templates(engine: Engine):
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM rule_template")) == 0
        assert conn.scalar(text("SELECT count(*) FROM rule_template_version")) == 0
    for statement in (
        """
        INSERT INTO rule_template (code, family, kind, title, typology,
                                   country_code, packs)
        VALUES ('X', 'ALERT', 'THRESHOLD', 't', '{}', 'MX', '{}')
        """,
        "UPDATE rule_template SET title = 'x'",
        "DELETE FROM rule_template_version",
    ):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            engine.begin() as conn,
        ):
            _ = conn.execute(text(statement))


ISOLATED: tuple[tuple[type[object], Factory], ...] = (
    (MonitoringRule, factories.monitoring_rule),
    (RuleAssignment, factories.rule_assignment),
    (RuleHitTransaction, factories.rule_hit_transaction),
    (TierLimitNotice, factories.tier_limit_notice),
    (ReportableEvent, factories.reportable_event),
    (CapturePeriod, factories.capture_period),
    (CounterpartyNameIndex, factories.counterparty_name_index),
)


def test_a_tenant_never_sees_another_tenants_rules_hits_or_captures(
    tenant_id: UUID,
):
    with tenant_session(system_ctx(tenant_id)) as s:
        for model, make in ISOLATED:
            _ = _new(s, tenant_id, model, make)
    with tenant_session(system_ctx(uuid7())) as s:
        for model, _make in ISOLATED:
            assert s.scalars(select(model)).all() == [], model


def test_downgrade_removes_the_tables_and_restores_the_subject_check(
    database: Engine,
):
    go_down(BEFORE)
    assert tables_present(database) == set()
    with database.connect() as conn:
        inspector = inspect(conn)
        ((name, check),) = conn.execute(SUBJECT_CHECKS).all()
        columns = {c["name"] for c in inspector.get_columns("screening_subject")}
        fk_names = {fk["name"] for fk in inspector.get_foreign_keys("counterparty")}
    assert name == CHECK_0010
    assert "AD_HOC" in check
    assert "COUNTERPARTY" not in check
    assert "counterparty_id" not in columns
    assert "fk_counterparty_subject" not in fk_names
    migrate.upgrade()
    assert tables_present(database) == set(TABLES)
    with database.connect() as conn:
        ((name, check),) = conn.execute(SUBJECT_CHECKS).all()
    assert name == "ck_screening_subject_kind_input"
    assert "COUNTERPARTY" in check
