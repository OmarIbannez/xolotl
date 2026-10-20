"""Fixtures of the re-screening tests: job sessions and a webhook stack."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.helpers import system_ctx
from tests.platform.jobs_support import FakeClock
from tests.platform.webhooks_support import Stack
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.events.registry import reset_subscribers


@contextmanager
def job_session(tenant: CreatedTenant) -> Generator[Session]:
    """Open a session as a re-screen job does: system activity of the tenant.

    Yields:
        The session, committed on a clean exit.

    """
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        yield s


@pytest.fixture
def webhook_stack(engine: Engine) -> Iterator[Stack]:
    """Build the webhook stack and compose the screening module on its queue.

    Yields:
        The stack; the subscriptions and the index holder are dropped afterwards.

    """
    reset_subscribers()
    stack = Stack(FakeClock())
    _ = register_screening(
        session_factory=get_sessionmaker(), settings=get_settings(), queue=stack.queue
    )
    yield stack
    reset_subscribers()
    set_index_holder(None)
