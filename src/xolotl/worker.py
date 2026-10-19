"""The worker loop: events, jobs and schedules, one tick at a time (spec §9, §10).

A tick runs the outbox dispatcher (``OutboxEventBus.dispatch_pending``), the job runner
(``JobRunner.run_once``, which first takes back leases that ran out) and the scheduler
(``Scheduler.tick``), in that order and once each. Events go first, so a job their
handlers queue runs in the same tick; schedules go last, so the jobs they queue run in
the next one. A step that raises is logged and the tick carries on with the next step:
a database blip in one of the three must stop neither the others nor the next tick.
Between ticks the loop pauses ``WORKER_TICK_SECONDS``. Each step opens and closes its
own short sessions, so nothing holds a database connection during the pause.

Before its first tick the loop runs its start-up step once, when it has one (the
composition root gives it one that declares every tenant's platform schedules). A
start-up step that fails is logged, and the loop runs all the same.

The loop ends after the tick in progress when ``stop()`` is called, from any thread
(the API does that on shutdown, see ``EmbeddedWorker``), or inside
``handle_signals()`` on SIGTERM or SIGINT (``xolotl worker``). A job that is running
then finishes, its heartbeat keeping its lease. Jobs are claimed one at a time, so no
job is ever claimed and left waiting, and there is nothing to release on the way out.
A second signal does not wait for the tick: it raises ``SystemExit`` at once, which
nothing in the loop catches, so ``xolotl worker`` exits. It unwinds whatever was
running and rolls its transaction back; the leases of what that held then run out,
and another worker picks the work up again.

A signal handler runs in the main thread between two bytecodes of whatever it was
doing, maybe while that holds a lock (logging's, or the pause's own event), so the
first signal only sets a flag; the pause looks at it every ``POLL_SECONDS``.
``stop()``, which runs in an ordinary thread, wakes the pause at once.

``xolotl.app`` composes the loop: ``build_worker`` for ``xolotl worker``, and the API
lifespan for the embedded thread, both from one ``wire_platform`` call.
"""

import logging
import signal
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from types import FrameType

from xolotl.platform.events.bus import OutboxEventBus
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.jobs.scheduler import Scheduler

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.1  # how long a signal can go unnoticed during the pause
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
EMBEDDED_THREAD_NAME = "xolotl-embedded-worker"
# How long the API's shutdown waits for the embedded loop to finish its tick.
STOP_TIMEOUT_SECONDS = 10.0


class Worker:
    """Ticks the dispatcher, the job runner and the scheduler until it is stopped."""

    def __init__(
        self,
        *,
        bus: OutboxEventBus,
        runner: JobRunner,
        scheduler: Scheduler,
        tick_seconds: float,
        on_start: Callable[[], object] | None = None,
    ) -> None:
        if tick_seconds < 0:
            msg = "tick_seconds must not be negative"
            raise ValueError(msg)
        self._on_start: Callable[[], object] | None = on_start
        self._steps: tuple[tuple[str, Callable[[], int]], ...] = (
            ("dispatch", bus.dispatch_pending),
            ("jobs", runner.run_once),
            ("schedules", scheduler.tick),
        )
        self._tick_seconds: float = tick_seconds
        self._wake: threading.Event = threading.Event()
        self._signal: int | None = None  # written only by the signal handler

    @property
    def stopping(self) -> bool:
        return self._signal is not None or self._wake.is_set()

    def stop(self) -> None:
        """Ask the loop to end after the tick in progress; safe from any thread."""
        self._wake.set()

    def tick(self) -> None:
        """Run the three steps once, in order, each whatever the others did."""
        for name, step in self._steps:
            try:
                _ = step()
            except Exception:
                logger.exception("worker step failed", extra={"worker_step": name})

    def run(self, max_ticks: int | None = None) -> int:
        """Run the start-up step, then tick until stopped or ``max_ticks`` ticks ran.

        ``max_ticks=0`` runs the start-up step and no tick.

        Returns:
            How many ticks ran.

        Raises:
            ValueError: When ``max_ticks`` is negative.

        """
        if max_ticks is not None and max_ticks < 0:
            msg = "max_ticks must not be negative"
            raise ValueError(msg)
        logger.info("worker started", extra={"tick_seconds": self._tick_seconds})
        if self._on_start is not None and not self.stopping:
            try:
                _ = self._on_start()
            except Exception:
                logger.exception("worker start-up step failed")
        ticks = 0
        while not self.stopping and (max_ticks is None or ticks < max_ticks):
            self.tick()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            self._pause()
        stopped_by = None if self._signal is None else signal.Signals(self._signal).name
        logger.info("worker stopped", extra={"ticks": ticks, "signal": stopped_by})
        return ticks

    @contextmanager
    def handle_signals(self) -> Generator[None]:
        """Stop after the tick in progress on SIGTERM or SIGINT, while the block runs.

        A second signal stops at once: it raises ``SystemExit(128 + signal)`` in the
        main thread, wherever that is. Only the main thread can install signal
        handlers; the ones it had before are put back when the block ends.

        Yields:
            Nothing; the handlers are in place until the block ends.

        """
        previous = {
            number: signal.signal(number, self._on_signal) for number in STOP_SIGNALS
        }
        try:
            yield
        finally:
            for number, handler in previous.items():
                # None: a handler installed from outside Python, which has no value
                # that signal() could put back.
                _ = signal.signal(
                    number, signal.SIG_DFL if handler is None else handler
                )

    def _on_signal(self, number: int, _frame: FrameType | None) -> None:
        # See the module docstring: set the flag, take no lock, log nothing. A second
        # signal means the operator will not wait for the tick, maybe stuck on a lock.
        if self._signal is not None:
            raise SystemExit(128 + number)
        self._signal = number

    def _pause(self) -> None:
        deadline = time.monotonic() + self._tick_seconds
        while not self.stopping:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            _ = self._wake.wait(min(remaining, POLL_SECONDS))


class EmbeddedWorker:
    """The worker loop on a daemon thread of the API process (one container).

    ``serve`` starts it from the application's lifespan, after the migrations and
    the role check, and stops it when the application shuts down. Its loop runs on
    the API's own platform services, so its sessions come from the API's pool.
    """

    def __init__(
        self, worker: Worker, *, stop_timeout: float = STOP_TIMEOUT_SECONDS
    ) -> None:
        self._worker: Worker = worker
        self._stop_timeout: float = stop_timeout
        self.thread: threading.Thread = threading.Thread(
            target=self._run, name=EMBEDDED_THREAD_NAME, daemon=True
        )

    def start(self) -> None:
        self.thread.start()
        logger.info(
            "embedded worker started",
            extra={"stop_timeout_seconds": self._stop_timeout},
        )

    def stop(self) -> bool:
        """Ask the loop to end, and wait for it at most ``stop_timeout`` seconds.

        A job still running when the wait ends keeps going until the process exits,
        which ends the daemon thread with it; its lease then runs out and another
        worker runs the job again. Handlers are written to be run twice.

        Returns:
            Whether the thread ended within the wait.

        """
        self._worker.stop()
        self.thread.join(self._stop_timeout)
        if self.thread.is_alive():
            logger.warning(
                "embedded worker still busy at shutdown; its job is left to its lease",
                extra={"stop_timeout_seconds": self._stop_timeout},
            )
            return False
        logger.info("embedded worker stopped")
        return True

    def _run(self) -> None:
        try:
            _ = self._worker.run()
        except BaseException:
            # Only a BaseException gets out of a tick. Logged here, through the
            # redacting handler, rather than by threading's hook, which prints the
            # raw traceback to stderr.
            logger.exception(
                "embedded worker stopped by an error; no events or jobs run here now"
            )
