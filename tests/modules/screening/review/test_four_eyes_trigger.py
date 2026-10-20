"""The database refuses a same-user answer to an escalation (DR-305), from any writer.

The rows are inserted in plain SQL as the application role, bypassing the service,
so only ``match_disposition_four_eyes`` stands in the way.
"""

from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import staff_session
from tests.modules.screening.review.support import add_staff, party_finding
from xolotl.modules.screening.engine.models import ScreeningRun
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.ids import uuid7

INSERT = text(
    """
    INSERT INTO match_disposition (tenant_id, id, match_id, decision, rationale,
      decided_by_type, decided_by, prior_disposition_id, policy_snapshot_id)
    VALUES (:tenant_id, :id, :match_id, :decision, :rationale, :by_type, :by,
      :prior, :snapshot)
    """
)


def _insert(
    s: Session,
    tenant_id: UUID,
    match_id: UUID,
    *,
    decision: str,
    by: UUID | None,
    prior: UUID | None,
) -> UUID:
    snapshot = s.scalars(select(ScreeningRun.policy_snapshot_id)).first()
    row_id = uuid7()
    _ = s.execute(
        INSERT,
        {
            "tenant_id": tenant_id,
            "id": row_id,
            "match_id": match_id,
            "decision": decision,
            "rationale": b"\x00" * 40,
            "by_type": "SYSTEM" if by is None else "STAFF",
            "by": by,
            "prior": prior,
            "snapshot": snapshot,
        },
    )
    return row_id


def _escalation(tenant: CreatedTenant, user_id: UUID) -> tuple[UUID, UUID]:
    _, match_id = party_finding(tenant)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        escalation = _insert(
            s,
            tenant.tenant_id,
            match_id,
            decision="POSSIBLE_TRUE_MATCH",
            by=user_id,
            prior=None,
        )
    return match_id, escalation


def test_the_four_eyes_trigger_refuses_a_same_user_row_inserted_in_sql(
    engine: Engine, tenant: CreatedTenant
):
    senior = add_staff(tenant)
    match_id, escalation = _escalation(tenant, senior)
    with (
        pytest.raises(IntegrityError, match="match_disposition_four_eyes"),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = _insert(
            s,
            tenant.tenant_id,
            match_id,
            decision="TRUE_MATCH",
            by=senior,
            prior=escalation,
        )
        s.flush()


def test_the_trigger_accepts_a_different_user_and_system_rows(
    engine: Engine, tenant: CreatedTenant
):
    analyst = add_staff(tenant)
    senior = add_staff(tenant)
    match_id, escalation = _escalation(tenant, analyst)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = _insert(
            s,
            tenant.tenant_id,
            match_id,
            decision="TRUE_MATCH",
            by=senior,
            prior=escalation,
        )
        _ = _insert(
            s,
            tenant.tenant_id,
            match_id,
            decision="SUPERSEDED",
            by=None,
            prior=escalation,
        )
        count = s.scalar(text("SELECT count(*) FROM match_disposition"))
    assert count == 3
