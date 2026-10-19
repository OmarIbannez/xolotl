"""Durable workflows on Postgres: ordered steps, signals and timers (spec §9).

A workflow is a list of named steps declared once and registered by name. A run lives
in the database, not in a worker, so a worker that dies loses nothing. ``start`` and
``signal`` join the caller's transaction; jobs do the rest, one step each.
"""

from xolotl.platform.workflows.definition import (
    Step,
    StepInput,
    StepResult,
    WorkflowDefinition,
    register_workflow,
    workflow,
)
from xolotl.platform.workflows.runner import (
    ADVANCE_KIND,
    SWEEP_KIND,
    TIMER_KIND,
    PostgresWorkflowRunner,
)

__all__ = [
    "ADVANCE_KIND",
    "SWEEP_KIND",
    "TIMER_KIND",
    "PostgresWorkflowRunner",
    "Step",
    "StepInput",
    "StepResult",
    "WorkflowDefinition",
    "register_workflow",
    "workflow",
]
