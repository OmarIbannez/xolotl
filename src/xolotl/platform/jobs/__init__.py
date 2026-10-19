"""Background work on Postgres: a job queue, a runner and a cron scheduler (spec §9).

A job is a row. ``enqueue`` and ``schedule`` join the caller's transaction, so the work
exists only if the write that asked for it commits. Workers claim jobs with a leased,
fenced lock, so a job survives a crash or a hung handler and retries with backoff
until it succeeds or goes ``DEAD``. Handlers are registered once per process by job
kind and must be safe to run twice.
"""

from xolotl.platform.jobs.queue import (
    Job,
    JobHandler,
    PostgresJobQueue,
    job_handler,
    register_job_handler,
)
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.jobs.scheduler import Scheduler

__all__ = [
    "Job",
    "JobHandler",
    "JobRunner",
    "PostgresJobQueue",
    "Scheduler",
    "job_handler",
    "register_job_handler",
]
