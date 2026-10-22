"""Fixtures of the simulation tests: the B7 stage of the hits and hook tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from tests.modules.monitoring.rules.hook_support import Stage, build_stage
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.tenants.service import CreatedTenant


@pytest.fixture
def stage(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Stage]:
    """Tenant A with three rules active from 30 June 2026 (plan 4b B7).

    Yields:
        The stage; the processing hooks are forgotten afterwards.

    """
    yield build_stage(tenant, admin_engine, monkeypatch)
    reset_processing_hooks()
