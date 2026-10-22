"""Plan 4b's composition: the rules events, the processing hook and its readers.

``wire_platform`` calls ``register_rules`` right after ``register_monitoring``. B8
added the simulation handler here, B10 adds the captures hook and B11 the period
close.
"""

from xolotl.modules.monitoring.events import RULES_STAFF_ONLY_EVENT_TYPES
from xolotl.modules.monitoring.process.hooks import register_processing_hook
from xolotl.modules.monitoring.rules.evaluate import (
    ScreeningStatusReader,
    use_screening_status,
)
from xolotl.modules.monitoring.rules.hook import RulesHook
from xolotl.modules.monitoring.simulation.job import (
    SIMULATE_KIND,
    TrueMatchReader,
    handle_simulate,
    use_true_match_reader,
)
from xolotl.platform.events import register_staff_only
from xolotl.platform.jobs.queue import PostgresJobQueue, register_job_handler


def register_rules(
    *,
    queue: PostgresJobQueue,
    screening_status: ScreeningStatusReader | None,
    true_match: TrueMatchReader | None = None,
) -> None:
    """Mark the staff-only rules events, register the rules hook and simulation.

    ``screening_status`` reads a party's screening status for the evaluator's
    profile, and ``true_match`` when a party found ``TRUE_MATCH`` was last
    screened, for a simulation's overlap; the composition root passes
    screening's, which monitoring may not import itself.
    """
    del queue
    for event_type in RULES_STAFF_ONLY_EVENT_TYPES:
        register_staff_only(event_type)
    use_screening_status(screening_status)
    use_true_match_reader(true_match)
    register_processing_hook(RulesHook())
    register_job_handler(SIMULATE_KIND, handle_simulate)
