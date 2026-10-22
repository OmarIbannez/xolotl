"""Builders of the dry-run replay tests: a processed week and hand-made rules."""

from datetime import date
from uuid import UUID

from sqlalchemy import Engine, func, inspect, select, table

from tests.helpers import system_ctx
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    Stage,
    add_relationship,
    deposit,
    feed,
    hits,
    local,
)
from xolotl.modules.monitoring.rules.enums import RuleVersionStatus
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
)
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.ids import uuid7

WEEK_START = date(2026, 12, 7)
WEEK_END = date(2026, 12, 13)
N3 = "cust-n3"


def process_week(stage: Stage) -> None:
    """Process 4b's structuring golden: three near-threshold cash deposits.

    One ``CASH_STRUCTURING`` hit is stored, on the third deposit.
    """
    _ = add_relationship(stage.tenant, N3, "N3")
    _ = feed(
        stage,
        [
            deposit(f"dep-{n}", local(day), amount, customer_ref=N3)
            for n, day, amount in (
                (1, 7, "3600.00"),
                (2, 8, "3700.00"),
                (3, 9, "3800.00"),
            )
        ],
    )
    assert len(hits(stage, CASH)) == 1


def clone_rule(
    stage: Stage,
    code: str,
    *,
    effective_from: date,
    product: str | None = None,
    dedup_key: str | None = None,
) -> UUID:
    """Copy the active ``CASH_STRUCTURING`` version into an active rule ``code``.

    The copy is assigned from ``effective_from`` to ``product`` (null: every
    product), with its dedup key replaced by ``dedup_key`` when one is given.

    Returns:
        The new version's id.

    """
    tenant_id = stage.tenant_id
    with tenant_session(system_ctx(tenant_id)) as s:
        source = s.get_one(MonitoringRuleVersion, (tenant_id, stage.versions[CASH]))
        origin = s.get_one(MonitoringRule, (tenant_id, source.rule_id))
        rule = MonitoringRule(
            tenant_id=tenant_id,
            id=uuid7(),
            code=code,
            family=origin.family,
            kind=origin.kind,
            title=origin.title,
            created_by=origin.created_by,
        )
        definition = {**source.definition, "code": code}
        if dedup_key is not None:
            dedup = source.definition["dedup"]
            assert isinstance(dedup, dict)
            definition["dedup"] = {**dedup, "key": dedup_key}
        version = MonitoringRuleVersion(
            tenant_id=tenant_id,
            id=uuid7(),
            rule_id=rule.id,
            version=1,
            definition=definition,
            definition_hash=source.definition_hash,
            status=RuleVersionStatus.ACTIVE,
            sync=source.sync,
            author_id=source.author_id,
            approved_by=source.approved_by,
            approved_at=source.approved_at,
            simulation_id=source.simulation_id,
            activated_at=clock.now(),
        )
        assert source.approved_by is not None
        assignment = RuleAssignment(
            tenant_id=tenant_id,
            id=uuid7(),
            rule_version_id=version.id,
            product=product,
            effective_from=effective_from,
            approved_by=source.approved_by,
        )
        s.add(rule)
        s.flush()
        s.add(version)
        s.flush()
        s.add(assignment)
        return version.id


def row_counts(admin_engine: Engine) -> dict[str, int]:
    """Count the rows of every table of the schema, partitions included.

    Returns:
        The count per table name.

    """
    with admin_engine.connect() as conn:
        return {
            name: conn.execute(
                select(func.count()).select_from(table(name))
            ).scalar_one()
            for name in sorted(inspect(conn).get_table_names())
        }
