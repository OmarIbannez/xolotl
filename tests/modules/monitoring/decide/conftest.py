"""Fixtures of the decide tests (plan 4c C4 and C5)."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Engine

from tests.modules.monitoring.decide.support import (
    TEST_BUDGET_MS,
    DecideStage,
    build_decide_stage,
)
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings


@pytest.fixture
def tenant_id(engine: Engine, tenant: CreatedTenant) -> UUID:
    return tenant.tenant_id


@pytest.fixture
def decide_stage(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[DecideStage]:
    """C5's stage, with the decide budget raised to ``TEST_BUDGET_MS``.

    Yields:
        The stage; the processing hooks are forgotten afterwards.

    """
    stage = build_decide_stage(tenant, admin_engine, monkeypatch)
    monkeypatch.setattr(get_settings(), "monitoring_decide_budget_ms", TEST_BUDGET_MS)
    yield stage
    reset_processing_hooks()
