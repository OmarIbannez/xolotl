"""Replay a run: re-score its stored hits exactly (SP-03 spec §8.4, NFR-SC-007).

A replay rebuilds the subject's query and compares its fingerprint with the run's: a
subject whose data changed since is reported and not re-scored. Otherwise each stored
hit is scored again against its immutable entry row, with the scoring IDF rebuilt
from the exact shared versions of the run's list set (Deviation 2) and the scoring
configuration rebuilt from the run's policy snapshot, and banded with the thresholds
stored on the hit. A run scored by another matcher version is refused.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import (
    ListEntryRow,
    ListVersion,
    PrivateListEntryRow,
)
from xolotl.modules.screening.catalog.queries import entry_of
from xolotl.modules.screening.engine.entries import (
    indexed_private_entry,
    shared_version_entries,
)
from xolotl.modules.screening.engine.enums import HitBand
from xolotl.modules.screening.engine.models import (
    ScreeningHit,
    ScreeningListSetMember,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.policy import scoring_config
from xolotl.modules.screening.engine.screen import subject_query
from xolotl.modules.screening.errors import MatcherVersionMismatchError
from xolotl.modules.screening.lists import ListEntry
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    Band,
    BandThresholds,
    IdfTable,
    NameQuery,
    ScoringConfig,
    band_for,
    score,
)
from xolotl.platform.errors import NotFoundError
from xolotl.platform.policy import get_snapshot


@dataclass(frozen=True, slots=True)
class ReplayRow:
    """One stored hit and its score and band now."""

    hit_id: UUID
    list_code: str
    entry_key: str
    stored_score: Decimal
    stored_band: HitBand
    replayed_score: Decimal
    # A whitelisted hit replays as WHITELISTED when its would-be band holds.
    replayed_band: Band | HitBand
    same: bool


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What a replay of one run found."""

    run_id: UUID
    matcher_version: str
    subject_changed: bool
    rows: tuple[ReplayRow, ...]

    @property
    def identical(self) -> bool:
        """Tell whether the replay reproduced the run.

        Returns:
            True when the subject is unchanged and every hit replayed the same.

        """
        return not self.subject_changed and all(row.same for row in self.rows)


def _run(s: Session, run_id: UUID) -> ScreeningRun:
    run = s.scalar(select(ScreeningRun).where(ScreeningRun.id == run_id))
    if run is None:
        msg = "screening run not found"
        raise NotFoundError(msg)
    return run


def _scoring_idf(s: Session, list_set_id: UUID) -> IdfTable:
    # The screenable entries of the set's shared versions, in the holder's order:
    # by list code, then by row id.
    members = s.execute(
        select(ScreeningListSetMember.list_code, ListVersion.seq)
        .join(ListVersion, ListVersion.id == ScreeningListSetMember.list_version_id)
        .where(ScreeningListSetMember.list_set_id == list_set_id)
        .order_by(ScreeningListSetMember.list_code)
    ).all()
    return IdfTable.build(
        entry_of(row)
        for code, seq in members
        for row in shared_version_entries(s, code, seq)
    )


def _config(s: Session, run: ScreeningRun) -> ScoringConfig:
    bands = get_snapshot(s, run.policy_snapshot_id).policy.screening.bands
    config = scoring_config(bands)
    if config.config_hash() != run.scoring_config_hash:
        msg = "the scoring configuration of the run's policy snapshot differs"
        raise MatcherVersionMismatchError(msg)
    return config


def _entry(s: Session, hit: ScreeningHit) -> ListEntry:
    if hit.list_entry_id is not None:
        row = s.get_one(ListEntryRow, hit.list_entry_id)
        return entry_of(row)
    private = s.scalars(
        select(PrivateListEntryRow).where(
            PrivateListEntryRow.id == hit.private_list_entry_id
        )
    ).one()
    return indexed_private_entry(s, private, hit.list_code).entry


def _thresholds(hit: ScreeningHit) -> BandThresholds:
    stored = hit.features["thresholds"]
    if not isinstance(stored, dict):
        msg = f"hit {hit.id} stores no thresholds"
        raise TypeError(msg)
    return BandThresholds(
        alert=Decimal(str(stored["alert"])), review=Decimal(str(stored["review"]))
    )


def _row(
    s: Session,
    hit: ScreeningHit,
    query: NameQuery,
    *,
    idf: IdfTable,
    config: ScoringConfig,
) -> ReplayRow:
    result = score(query, _entry(s, hit), idf=idf, config=config)
    band: Band | HitBand = band_for(result, _thresholds(hit))
    if hit.band is HitBand.WHITELISTED and band.value == hit.features.get(
        "band_before_whitelist"
    ):
        # The whitelist is a stored decision; the band it suppressed is replayed.
        band = HitBand.WHITELISTED
    return ReplayRow(
        hit_id=hit.id,
        list_code=hit.list_code,
        entry_key=hit.source_entry_key,
        stored_score=hit.score,
        stored_band=hit.band,
        replayed_score=result.score,
        replayed_band=band,
        same=band.value == hit.band.value and result.score == hit.score,
    )


def replay_run(s: Session, run_id: UUID) -> ReplayReport:
    """Re-score a run's stored hits and compare them with what was stored.

    Returns:
        The report: ``subject_changed`` with no rows when the subject's query no
        longer has the run's fingerprint, else one row per stored hit.

    Raises:
        MatcherVersionMismatchError: When the run was scored by another matcher
            version, or its policy snapshot no longer gives its scoring
            configuration.

    """
    run = _run(s, run_id)
    if run.matcher_version != MATCHER_VERSION:
        raise MatcherVersionMismatchError
    subject = s.get_one(ScreeningSubject, (run.tenant_id, run.subject_id))
    query = subject_query(s, subject)
    if query.fingerprint() != run.query_fingerprint:
        return ReplayReport(
            run_id=run.id,
            matcher_version=run.matcher_version,
            subject_changed=True,
            rows=(),
        )
    config = _config(s, run)
    idf = _scoring_idf(s, run.list_set_id)
    hits = s.scalars(
        select(ScreeningHit)
        .where(ScreeningHit.run_id == run.id)
        .order_by(ScreeningHit.score.desc(), ScreeningHit.id)
    )
    return ReplayReport(
        run_id=run.id,
        matcher_version=run.matcher_version,
        subject_changed=False,
        rows=tuple(_row(s, hit, query, idf=idf, config=config) for hit in hits),
    )
