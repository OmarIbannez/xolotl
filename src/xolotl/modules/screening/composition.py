"""The screening module's composition root (SP-03 spec §8.1 and §11).

``wire_platform`` calls ``register_screening`` once per process. It marks the
staff-only screening events, so no webhook carries them (FR-SC-013), and installs
the process's index holder. It starts no thread: ``build_app`` and ``build_worker``
start the warm-up themselves.
"""

from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    set_index_holder,
)
from xolotl.modules.screening.events import STAFF_ONLY_EVENT_TYPES
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory
from xolotl.platform.events import register_staff_only


def register_screening(
    *, session_factory: SessionFactory, settings: Settings
) -> ScreeningIndexHolder:
    """Mark the staff-only event types and install a new index holder.

    Calling it again replaces the holder, so composing twice is harmless.

    Returns:
        The installed holder, not yet warmed.

    """
    for event_type in STAFF_ONLY_EVENT_TYPES:
        register_staff_only(event_type)
    holder = ScreeningIndexHolder(
        session_factory,
        refresh_seconds=settings.screening_index_refresh_seconds,
        private_shards=settings.screening_private_shards,
    )
    set_index_holder(holder)
    return holder
