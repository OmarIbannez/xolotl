"""Declaring a workflow: ordered steps, what a step receives and what it returns.

A workflow is a tuple of named steps that run in order. A step is a plain function
``(session, run, state, step_input) -> StepResult``. It runs inside the transaction
that records it, so whatever it writes through ``session`` commits together with its
step row, the run's new state and the job for the next step, or not at all. A step
must not commit or roll back the session.

``StepResult`` says what happens next:

* by default the run moves on to the following step, or finishes after the last one;
* ``done=True`` finishes the run at once, skipping the steps that remain;
* ``wait_for_signal`` and ``wake_at`` park the run (``WAITING``) at this step. The
  step is called again, with the signal in ``StepInput`` or with ``timed_out=True``
  once ``wake_at`` has passed, and then decides again. A signal wins over a timeout
  that is due at the same moment.

A step that raises fails its attempt: the attempt is recorded and the job retries it
with the queue's backoff (2, 4, 8, 16, 32, 60 and 60 minutes), so a step that keeps
raising takes about three hours and eight ``FAILED`` rows before the run is
``FAILED``. A raise is for faults, which a retry can mend. A business outcome (a
rejection, an expiry, a sanctions hit) is a result, not an error: finish with
``done=True`` and the outcome in ``state``. There is no exception yet that fails a
run at once; the first flow that needs one (SP-08) adds ``WorkflowFailure``.

Give each wait of a run its own signal name where a leftover must not satisfy a later
wait. Signals are durable until consumed and deduplicated while pending, but one that
arrives after the first was consumed cannot be told from an early signal for the next
wait of the same name.

A step holds a database transaction and the run's row lock while it runs, so it should
be quick. Slow or external work belongs in a job that the step enqueues through the
session it is given; the step then waits for the signal that job sends when it is
done. The run's state is stored as plain JSON, in the run and again in every step row:
keep identifiers in it, not personal data.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)
from sqlalchemy.orm import Session

from xolotl.platform.workflows.models import WorkflowRunRow


class StepInput(BaseModel):
    """What woke the step: a signal and its payload, or the wake time passing.

    A step that has just been reached receives the empty input.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    signal: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    timed_out: bool = False


class StepResult(BaseModel):
    """What a step hands back: the run's new state and where the run goes next."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    state: dict[str, JsonValue]
    done: bool = False
    wait_for_signal: str | None = None
    wake_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _a_finished_run_does_not_wait(self) -> Self:
        if self.done and (self.wait_for_signal is not None or self.wake_at is not None):
            msg = "a step that finishes the run cannot also wait"
            raise ValueError(msg)
        return self


Step = Callable[[Session, WorkflowRunRow, dict[str, JsonValue], StepInput], StepResult]


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """A named, ordered list of steps.

    ``timeout`` is how long a run may wait for a signal when its step did not give a
    ``wake_at`` of its own; ``None`` waits for as long as it takes.
    """

    name: str
    steps: tuple[tuple[str, Step], ...]
    timeout: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.steps:
            msg = f"workflow {self.name!r} needs at least one step"
            raise ValueError(msg)
        seen: set[str] = set()
        for step_id, _ in self.steps:
            if step_id in seen:
                msg = f"workflow {self.name!r} has a duplicate step id {step_id!r}"
                raise ValueError(msg)
            seen.add(step_id)

    def index_of(self, step_id: str) -> int:
        """Find where ``step_id`` sits in the order of steps.

        Returns:
            The step's position, counting from zero.

        Raises:
            LookupError: When the definition has no such step, as when a run outlives
                a deploy that renamed or dropped it.

        """
        for index, (candidate, _) in enumerate(self.steps):
            if candidate == step_id:
                return index
        msg = f"workflow {self.name!r} has no step {step_id!r}"
        raise LookupError(msg)


_WORKFLOWS: dict[str, WorkflowDefinition] = {}


def register_workflow(definition: WorkflowDefinition) -> None:
    """Make ``definition`` available to ``start`` and to the job handlers by name.

    Registering an equal definition again (same name, steps and timeout) changes
    nothing: composition runs once per app instance, and a test builds many. A
    different definition under a name that is taken is refused, because the runs
    already started under the first would meet the wrong steps.

    Raises:
        ValueError: When ``definition.name`` is registered with a different
            definition.

    """
    name = definition.name
    if _WORKFLOWS.setdefault(name, definition) != definition:
        msg = f"workflow {name!r} is already registered with a different definition"
        raise ValueError(msg)


def workflow(name: str) -> WorkflowDefinition:
    """Look up the definition registered under ``name``.

    Returns:
        The definition.

    """
    return _WORKFLOWS[name]  # KeyError carries the name
