"""The capture stage as a fixture (plan 4b B10)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from tests.modules.monitoring.captures.support import CaptureStage, build_stage
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.tenants.service import CreatedTenant


@pytest.fixture
def stage(tenant: CreatedTenant, admin_engine: Engine) -> Iterator[CaptureStage]:
    """Both packs, the 2026 unit values and the wired platform.

    Yields:
        The stage; the processing hooks are forgotten afterwards.

    """
    yield build_stage(tenant, admin_engine)
    reset_processing_hooks()
