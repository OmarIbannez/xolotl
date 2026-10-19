"""The adapters behind the kernel's ports, composed once per process (spec §9).

``xolotl.app.wire_platform`` builds them, for the API and for ``xolotl worker`` alike.
The API keeps them on its ``AppState`` for the routes, and the worker loop runs three
of them: the bus's dispatcher, the job runner and the scheduler. Every adapter opens
its sessions through ``session_factory``, so one process has one connection pool.
"""

from dataclasses import dataclass

from xolotl.platform.db import SessionFactory
from xolotl.platform.events.bus import OutboxEventBus
from xolotl.platform.events.notifier import Notifier
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.jobs.scheduler import Scheduler
from xolotl.platform.storage import ObjectStore
from xolotl.platform.workflows.runner import PostgresWorkflowRunner


@dataclass(frozen=True, slots=True)
class PlatformServices:
    session_factory: SessionFactory
    bus: OutboxEventBus
    queue: PostgresJobQueue
    job_runner: JobRunner
    scheduler: Scheduler
    workflows: PostgresWorkflowRunner
    notifier: Notifier
    object_store: ObjectStore
