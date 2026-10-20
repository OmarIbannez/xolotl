"""Delisting suggestions: a finding whose entry left the list (spec §10.2, §11).

When an entry is removed from its list, or stays but becomes non-screenable (a 69-B
``DESVIRTUADO`` or ``SENTENCIA_FAVORABLE``), the findings that still await or hold a
decision on it (``OPEN``, ``ESCALATED``, ``TRUE_MATCH``) get ``suggested_disposition =
DELISTED``. Staff confirm it with a ``DELISTED`` disposition; nothing closes on its
own. Each finding publishes one staff-only ``screening.match_delisting_suggested.v1``
the first time it gets the suggestion, and never again.
"""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.enums import MatchStatus, SuggestedDisposition
from xolotl.modules.screening.engine.models import ScreeningMatch
from xolotl.modules.screening.events import (
    MATCH_DELISTING_SUGGESTED,
    publish_screening_event,
)
from xolotl.platform import clock

# A finding in one of these states still means something while its entry is listed.
_SUGGESTIBLE: tuple[MatchStatus, ...] = (
    MatchStatus.OPEN,
    MatchStatus.ESCALATED,
    MatchStatus.TRUE_MATCH,
)


def suggest_delisting(
    s: Session, *, list_code: str, entry_keys: Collection[str]
) -> list[UUID]:
    """Suggest ``DELISTED`` on the tenant's findings of these entries of a list.

    Returns:
        The ids of the findings that newly carry the suggestion, in id order.

    """
    if not entry_keys:
        return []
    rows = s.execute(
        update(ScreeningMatch)
        .where(
            ScreeningMatch.list_code == list_code,
            ScreeningMatch.source_entry_key.in_(list(entry_keys)),
            ScreeningMatch.status.in_(_SUGGESTIBLE),
            ScreeningMatch.suggested_disposition.is_distinct_from(
                SuggestedDisposition.DELISTED
            ),
        )
        .values(
            suggested_disposition=SuggestedDisposition.DELISTED,
            updated_at=clock.now(),
        )
        .returning(ScreeningMatch.id, ScreeningMatch.subject_id)
        .execution_options(synchronize_session=False)
    ).all()
    suggested = sorted(rows)
    for match_id, subject_id in suggested:
        _ = publish_screening_event(
            s,
            MATCH_DELISTING_SUGGESTED,
            entity="screening_match",
            entity_id=match_id,
            key=str(subject_id),
            data={"match_id": str(match_id), "list_code": list_code},
        )
    return [match_id for match_id, _ in suggested]
