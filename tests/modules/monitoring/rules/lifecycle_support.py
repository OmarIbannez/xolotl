"""Builders of the lifecycle and assignment tests: staff, drafts, simulations.

The clock is frozen at 2026-10-30 12:00 Mexico City, so "today" is 30 October and
an assignment may start on any later date.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, update
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session
from xolotl.modules.monitoring.rules.enums import RuleVersionStatus, SimulationStatus
from xolotl.modules.monitoring.rules.library import read_library
from xolotl.modules.monitoring.rules.lifecycle import (
    activate_version,
    approve_version,
    submit_version,
)
from xolotl.modules.monitoring.rules.models import (
    MonitoringRuleVersion,
    RuleSimulation,
)
from xolotl.modules.monitoring.rules.templates import load_templates
from xolotl.modules.monitoring.rules.versions import VersionInput, create_rule
from xolotl.modules.tenants.models import StaffUser
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role, StaffStatus
from xolotl.platform.jobs.queue import PostgresJobQueue

CASH = "CASH_STRUCTURING"
NOW = datetime(2026, 10, 30, 18, tzinfo=UTC)
TODAY = date(2026, 10, 30)
AUTHOR_ROLES = (Role.SENIOR_ANALYST,)
APPROVER_ROLES = (Role.MLRO,)


def load_library(admin_engine: Engine) -> None:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = load_templates(s, read_library(), loaded_by="cli:test")


def freeze(monkeypatch: pytest.MonkeyPatch, at: datetime = NOW) -> None:
    monkeypatch.setattr(clock, "now", lambda: at)


def add_staff(tenant: CreatedTenant, *roles: Role) -> UUID:
    """Add an active staff user with ``roles`` to the tenant.

    Returns:
        The user's id.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        user = StaffUser(
            tenant_id=tenant.tenant_id,
            email=f"{uuid4().hex[:12]}@acme.mx",
            display_name="Aprobador",
            roles=sorted(role.value for role in roles),
            status=StaffStatus.ACTIVE,
        )
        s.add(user)
        s.flush()
        return user.id


def draft(
    tenant: CreatedTenant,
    code: str = CASH,
    *,
    body: VersionInput | None = None,
    roles: Iterable[Role] = AUTHOR_ROLES,
) -> MonitoringRuleVersion:
    """Create a draft version authored by the tenant's admin user.

    Returns:
        The new ``DRAFT`` version.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles) as s:
        return create_rule(s, template_code=code, body=body or VersionInput())


def simulate(
    tenant: CreatedTenant,
    version: MonitoringRuleVersion,
    *,
    finished_at: datetime = NOW,
    definition_hash: bytes | None = None,
    status: SimulationStatus = SimulationStatus.COMPLETED,
) -> UUID:
    """Record a simulation of the version and mark it ``SIMULATED`` (B8's job).

    Returns:
        The simulation's id.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        simulation = RuleSimulation(
            tenant_id=tenant.tenant_id,
            rule_version_id=version.id,
            requested_by=tenant.admin_user_id,
            range_start=date(2026, 9, 1),
            range_end=date(2026, 9, 30),
            status=status,
            definition_hash=definition_hash or version.definition_hash,
            finished_at=finished_at,
        )
        s.add(simulation)
        s.flush()
        _ = s.execute(
            update(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.id == version.id)
            .values(status=RuleVersionStatus.SIMULATED, simulation_id=simulation.id)
        )
        return simulation.id


def submitted(tenant: CreatedTenant, code: str = CASH) -> MonitoringRuleVersion:
    """Draft, simulate and submit a version as its author.

    Returns:
        The version, ``IN_REVIEW``.

    """
    version = draft(tenant, code)
    _ = simulate(tenant, version)
    with staff_session(tenant.tenant_id, tenant.admin_user_id, AUTHOR_ROLES) as s:
        return submit_version(s, version.id)


def approved(
    tenant: CreatedTenant, approver: UUID, code: str = CASH
) -> MonitoringRuleVersion:
    """Take a version to ``APPROVED`` with ``approver`` as the second pair of eyes.

    Returns:
        The approved version.

    """
    version = submitted(tenant, code)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        return approve_version(s, version.id)


def activated(
    tenant: CreatedTenant,
    approver: UUID,
    queue: PostgresJobQueue,
    code: str = CASH,
    *,
    effective_from: date = TODAY,
    product: str | None = None,
) -> MonitoringRuleVersion:
    """Take a new version of ``code`` all the way to ``ACTIVE``.

    Returns:
        The active version.

    """
    version = approved(tenant, approver, code)
    with staff_session(tenant.tenant_id, approver, APPROVER_ROLES) as s:
        return activate_version(
            s, version.id, effective_from=effective_from, product=product, queue=queue
        )
