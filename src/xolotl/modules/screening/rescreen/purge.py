"""Forget a purged party's name in screening (SP-03 spec §10.1, D7).

Consumer ``screening`` handles ``party.purged.v1`` in the dispatcher's transaction.
It deletes the party's ``party_name_index`` rows, the only other place its folded
name lives in the clear, so no reverse screening finds the party again. It then
redacts the query side of every hit of the party's runs: ``query_name``, each
alignment's ``query`` token and ``unmatched_query`` are blanked and the hit is
marked ``"redacted": true``.

What stays is the compliance record: the subject, its runs, the findings and their
dispositions, and in each hit the score, band, auto reason, the entry-side tokens
with each alignment's kind, similarity and weight, the qualifier outcomes, the rules
applied, the thresholds, the matcher version and configuration hash, and
``entry_ref``. A supervisor can still see that the party was screened, against which
list entry, how strongly it matched and what the staff decided, which the record
must show for the retention period of the screening itself. Qualifier and rule
details already mask identifiers, so they stay as stored. The redaction is
idempotent and skips hits already marked, so a redelivered event changes nothing.
"""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.enums import SubjectKind
from xolotl.modules.screening.engine.models import (
    PartyNameIndex,
    ScreeningHit,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.platform.events import Event

REDACTED = "redacted"


def redact_features(features: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Blank the query side of a hit's stored explanation.

    Returns:
        A new dict with ``query_name`` and every ``alignment[].query`` set to ``""``,
        ``unmatched_query`` emptied and ``"redacted": True``; everything else as is.

    """
    redacted = dict(features)
    redacted["query_name"] = ""
    alignment = features.get("alignment")
    if isinstance(alignment, list):
        redacted["alignment"] = [
            {**pair, "query": ""} if isinstance(pair, dict) else pair
            for pair in alignment
        ]
    redacted["unmatched_query"] = []
    redacted[REDACTED] = True
    return redacted


def on_party_purged(session: Session, event: Event) -> None:
    """Delete a purged party's name index rows and redact its hits."""
    party_id = UUID(str(event.data["party_id"]))
    _ = session.execute(
        delete(PartyNameIndex).where(PartyNameIndex.party_id == party_id)
    )
    subject_id = session.scalar(
        select(ScreeningSubject.id).where(
            ScreeningSubject.kind == SubjectKind.PARTY,
            ScreeningSubject.party_id == party_id,
        )
    )
    if subject_id is None:
        return
    runs = select(ScreeningRun.id).where(ScreeningRun.subject_id == subject_id)
    hits = session.scalars(select(ScreeningHit).where(ScreeningHit.run_id.in_(runs)))
    for hit in hits:
        if REDACTED not in hit.features:
            hit.features = redact_features(hit.features)
