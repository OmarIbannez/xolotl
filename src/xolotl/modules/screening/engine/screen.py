"""Screen one subject against the tenant's lists (SP-03 spec §8.3; FR-SC-001, 005, 007).

A run locks its subject, so runs of one subject never interleave (Deviation 12). It
resolves today's policy and snapshots it, derives the list set from the policy, the
tenant's subscriptions and the versions the holder has (Deviation 6), retrieves
candidates per kind group, scores and bands each, and stores every non-trivial score
as a hit with its explanation. ``ALERT`` and ``LOW_CONFIDENCE`` hits raise a finding,
one per subject and entry content; a whitelisted content is logged as a hit and raises
none. The run row is inserted first and completed last (Deviation 7). Everything runs
in the caller's transaction and publishes ids and codes only.
"""

import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict, Unpack, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import (
    PrivateListEntryRow,
    TenantListSubscription,
)
from xolotl.modules.screening.engine.enums import (
    OPEN_MATCH_STATUSES,
    AutoReason,
    DecidedByType,
    Decision,
    HitBand,
    MatchStatus,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.features import hit_features
from xolotl.modules.screening.engine.holder import (
    PrivateShard,
    SharedShard,
    get_index_holder,
)
from xolotl.modules.screening.engine.list_sets import ListSetMemberRef, upsert_list_set
from xolotl.modules.screening.engine.models import (
    ScreeningHit,
    ScreeningListSet,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.policy import (
    ScreeningPolicyView,
    policy_for_run,
    thresholds_for,
    top_k_for,
)
from xolotl.modules.screening.engine.queries import (
    AdHocSubjectInput,
    query_for_ad_hoc,
    query_for_party,
)
from xolotl.modules.screening.engine.subjects import refresh_subject_status
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.events import (
    MATCH_CREATED,
    RUN_COMPLETED,
    publish_screening_event,
)
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    Band,
    Candidate,
    IdfTable,
    IndexedEntry,
    MatchScore,
    MemoryIndex,
    NameQuery,
    band_for,
    score,
)
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.modules.screening.review.whitelist import active_whitelist_keys
from xolotl.platform import clock
from xolotl.platform.crypto import encrypt
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7

if TYPE_CHECKING:
    from pydantic import JsonValue

    from xolotl.platform.crypto.encrypted import Encrypted

SUPERSEDED_RATIONALE = "SUPERSEDED_BY_CHANGED_ENTRY"
_FINDING_BANDS = frozenset({Band.ALERT, Band.LOW_CONFIDENCE})


@dataclass(frozen=True, slots=True)
class Trigger:
    """What started a run: its type and the request, event, job or batch id."""

    type: TriggerType
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """A completed run, its subject, its stored hits and the findings it raised."""

    run: ScreeningRun
    subject: ScreeningSubject
    # Stored hits, by score, highest first.
    hits: tuple[ScreeningHit, ...]
    # Findings created or reopened by this run.
    new_matches: tuple[ScreeningMatch, ...]


@dataclass(frozen=True, slots=True)
class _Scored:
    indexed: IndexedEntry
    member: ListSetMemberRef
    result: MatchScore
    band: Band


@dataclass(slots=True)
class _Outcome:
    hits: list[ScreeningHit]
    new_matches: list[ScreeningMatch]


def _locked(s: Session, subject: ScreeningSubject) -> ScreeningSubject:
    return s.scalars(
        select(ScreeningSubject)
        .where(ScreeningSubject.id == subject.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _query(s: Session, subject: ScreeningSubject) -> NameQuery:
    if subject.party_id is not None:
        return query_for_party(s, subject.party_id)
    # An AD_HOC subject always has its input (the kind_input check constraint).
    stored = cast("Encrypted[dict[str, object]]", subject.input)
    return query_for_ad_hoc(AdHocSubjectInput.model_validate(stored.reveal(session=s)))


def _shared_codes(
    s: Session, view: ScreeningPolicyView, shard: SharedShard
) -> list[str]:
    screening = view.effective.screening
    wanted = dict.fromkeys(
        [entry.value for entry in screening.mandatory_lists]
        + list(screening.recommended_lists)
    )
    disabled = set(
        s.scalars(
            select(TenantListSubscription.list_code).where(
                TenantListSubscription.enabled.is_(False)
            )
        )
    )
    return [code for code in wanted if code not in disabled and code in shard.versions]


def _members(
    s: Session,
    view: ScreeningPolicyView,
    shard: SharedShard,
    private: PrivateShard | None,
    lists: Collection[str] | None,
) -> list[ListSetMemberRef]:
    members = [
        ListSetMemberRef(
            list_code=code,
            list_kind=shard.versions[code].list_kind,
            list_version_id=shard.versions[code].version_id,
            private_list_version_id=None,
            seq=shard.versions[code].seq,
        )
        for code in _shared_codes(s, view, shard)
    ]
    if private is not None:
        members.extend(
            ListSetMemberRef(
                list_code=code,
                list_kind=held.list_kind,
                list_version_id=None,
                private_list_version_id=held.version_id,
                seq=held.seq,
            )
            for code, held in sorted(private.versions.items())
        )
    if lists is not None:
        members = [m for m in members if m.list_code in lists]
    return members


def _kind_groups(
    members: Iterable[ListSetMemberRef],
) -> dict[str, list[ListSetMemberRef]]:
    # PEP lists retrieve fewer candidates than the sanctions-like ones.
    groups: dict[str, list[ListSetMemberRef]] = {}
    for member in members:
        kind = ListKind.PEP if member.list_kind == ListKind.PEP else ListKind.SANCTION
        groups.setdefault(kind, []).append(member)
    return groups


@dataclass(frozen=True, slots=True)
class _Sources:
    """Where a run's candidates come from: the holder, or a caller's restriction."""

    shard: SharedShard
    private: PrivateShard | None
    idf: IdfTable
    only_entries: Sequence[IndexedEntry] | None
    only_index: MemoryIndex | None


def _candidates(
    query: NameQuery, members: Sequence[ListSetMemberRef], sources: _Sources
) -> list[Candidate]:
    codes = {m.list_code for m in members}
    found: list[Candidate] = []
    if sources.only_entries is not None:
        kept = [e for e in sources.only_entries if e.shard in codes]
        index = MemoryIndex.build(kept, idf=sources.idf)
        found = index.candidates(query, shards=codes, top_k=len(kept))
    else:
        for kind, group in _kind_groups(members).items():
            top_k = top_k_for(kind)
            shared = {m.list_code for m in group if m.list_version_id is not None}
            own = {m.list_code for m in group if m.private_list_version_id is not None}
            if sources.only_index is not None:
                found.extend(
                    sources.only_index.candidates(
                        query, shards=shared | own, top_k=top_k
                    )
                )
                continue
            if shared:
                found.extend(
                    sources.shard.index.candidates(query, shards=shared, top_k=top_k)
                )
            if own and sources.private is not None:
                found.extend(
                    sources.private.index.candidates(query, shards=own, top_k=top_k)
                )
    unique: dict[UUID, Candidate] = {}
    for candidate in found:
        _ = unique.setdefault(candidate.indexed.ref, candidate)
    return list(unique.values())


def _score_all(
    query: NameQuery,
    candidates: Iterable[Candidate],
    members: Sequence[ListSetMemberRef],
    view: ScreeningPolicyView,
    idf: IdfTable,
) -> list[_Scored]:
    bands = view.effective.screening.bands
    by_code = {m.list_code: m for m in members}
    scored: list[_Scored] = []
    for candidate in candidates:
        member = by_code[candidate.indexed.shard]
        result = score(query, candidate.indexed.entry, idf=idf, config=view.config)
        band = band_for(result, thresholds_for(member.list_kind, bands))
        scored.append(_Scored(candidate.indexed, member, result, band))
    # Stable, so equal scores keep the retrieval order.
    return sorted(scored, key=lambda item: -item.result.score)


def _private_tags(s: Session, scored: Iterable[_Scored]) -> dict[UUID, bytes]:
    refs = [i.indexed.ref for i in scored if i.member.private_list_version_id]
    if not refs:
        return {}
    rows = s.execute(
        select(PrivateListEntryRow.id, PrivateListEntryRow.content_tag).where(
            PrivateListEntryRow.id.in_(refs)
        )
    )
    return dict(rows.all())


class _Run:
    """The writes of one run, after its row exists."""

    def __init__(
        self,
        s: Session,
        subject: ScreeningSubject,
        run: ScreeningRun,
        view: ScreeningPolicyView,
    ) -> None:
        self.s: Session = s
        self.subject: ScreeningSubject = subject
        self.run: ScreeningRun = run
        self.view: ScreeningPolicyView = view
        self.whitelisted: set[tuple[str, str, bytes]] = active_whitelist_keys(
            s, subject.id
        )
        self.hits: list[ScreeningHit] = []
        self.new_matches: list[ScreeningMatch] = []

    def store(self, item: _Scored, content_hash: bytes) -> None:
        entry = item.indexed.entry
        member = item.member
        shared = member.list_version_id is not None
        thresholds = thresholds_for(
            member.list_kind, self.view.effective.screening.bands
        )
        entry_ref: dict[str, JsonValue] = {
            "list_code": member.list_code,
            "entry_key": entry.entry_key,
            "list_entry_id" if shared else "private_list_entry_id": str(
                item.indexed.ref
            ),
            "list_version_seq": member.seq,
        }
        hit = ScreeningHit(
            tenant_id=self.run.tenant_id,
            run_id=self.run.id,
            list_code=member.list_code,
            list_entry_id=item.indexed.ref if shared else None,
            private_list_entry_id=None if shared else item.indexed.ref,
            source_entry_key=entry.entry_key,
            entry_content_hash=content_hash,
            score=item.result.score,
            band=HitBand(item.band),
            features=hit_features(
                item.result,
                list_kind=member.list_kind,
                thresholds=thresholds,
                config_hash=self.run.scoring_config_hash,
                entry_ref=entry_ref,
            ),
        )
        if (member.list_code, entry.entry_key, content_hash) in self.whitelisted:
            hit.band = HitBand.WHITELISTED
            hit.auto_reason = AutoReason.WHITELIST
        elif item.band is Band.AUTO_DISCARDED:
            hit.auto_reason = AutoReason.ID_CONFLICT_CAP
        elif item.band in _FINDING_BANDS:
            hit.match_id = self._finding(hit, member, item.result.score).id
        self.s.add(hit)
        self.s.flush()
        self.hits.append(hit)

    def store_all(self, scored: Sequence[_Scored]) -> None:
        stored = [item for item in scored if item.band is not Band.BELOW_CUTOFF]
        tags = _private_tags(self.s, stored)
        for item in stored:
            shared = item.member.list_version_id is not None
            entry = item.indexed.entry
            self.store(item, entry.content_hash() if shared else tags[item.indexed.ref])

    def complete(self, started: float) -> RunResult:
        run, subject = self.run, self.subject
        status = refresh_subject_status(self.s, subject)
        run.hit_count = len(self.hits)
        run.new_match_count = len(self.new_matches)
        run.outcome = status
        run.latency_ms = round((time.monotonic() - started) * 1000)
        subject.last_screened_at = clock.now()
        subject.last_run_id = run.id
        self.s.flush()
        _ = publish_screening_event(
            self.s,
            RUN_COMPLETED,
            entity="screening_subject",
            entity_id=subject.id,
            key=str(subject.id),
            data={
                "run_id": str(run.id),
                "subject_id": str(subject.id),
                "party_id": None if subject.party_id is None else str(subject.party_id),
                "mode": run.mode.value,
                "outcome": status.value,
            },
        )
        return RunResult(
            run=run,
            subject=subject,
            hits=tuple(self.hits),
            new_matches=tuple(self.new_matches),
        )

    def _finding(
        self, hit: ScreeningHit, member: ListSetMemberRef, value: Decimal
    ) -> ScreeningMatch:
        created = self.s.scalars(
            insert(ScreeningMatch)
            .values(
                tenant_id=self.run.tenant_id,
                id=uuid7(),
                subject_id=self.subject.id,
                list_code=hit.list_code,
                list_kind=ListKind(member.list_kind),
                list_entry_id=hit.list_entry_id,
                private_list_entry_id=hit.private_list_entry_id,
                source_entry_key=hit.source_entry_key,
                entry_content_hash=hit.entry_content_hash,
                band=hit.band,
                score=value,
                origin_run_id=self.run.id,
                status=MatchStatus.OPEN,
            )
            .on_conflict_do_nothing(constraint="uq_screening_match_finding")
            .returning(ScreeningMatch),
            execution_options={"populate_existing": True},
        ).one_or_none()
        if created is not None:
            self._supersede(created)
            self._raised(created)
            return created
        existing = self.s.scalars(
            select(ScreeningMatch).where(
                ScreeningMatch.subject_id == self.subject.id,
                ScreeningMatch.list_code == hit.list_code,
                ScreeningMatch.source_entry_key == hit.source_entry_key,
                ScreeningMatch.entry_content_hash == hit.entry_content_hash,
            )
        ).one()
        if existing.status is MatchStatus.FALSE_POSITIVE:
            # Its whitelist row was revoked: the content alerts again (Deviation 8).
            existing.status = MatchStatus.OPEN
            existing.closed_at = None
            self.s.flush()
            self._raised(existing)
        return existing

    def _supersede(self, new: ScreeningMatch) -> None:
        older = self.s.scalars(
            select(ScreeningMatch)
            .where(
                ScreeningMatch.subject_id == new.subject_id,
                ScreeningMatch.list_code == new.list_code,
                ScreeningMatch.source_entry_key == new.source_entry_key,
                ScreeningMatch.entry_content_hash != new.entry_content_hash,
                ScreeningMatch.status.in_(OPEN_MATCH_STATUSES),
            )
            .order_by(ScreeningMatch.created_at, ScreeningMatch.id)
            .with_for_update()
        ).all()
        for match in older:
            match.status = MatchStatus.SUPERSEDED
            match.superseded_by_match_id = new.id
            match.closed_at = clock.now()
            self.s.add(
                MatchDisposition(
                    tenant_id=match.tenant_id,
                    match_id=match.id,
                    decision=Decision.SUPERSEDED,
                    rationale=encrypt(
                        SUPERSEDED_RATIONALE, tenant_id=match.tenant_id, session=self.s
                    ),
                    decided_by_type=DecidedByType.SYSTEM,
                    decided_by=None,
                    prior_disposition_id=None,
                    policy_snapshot_id=self.view.snapshot_id,
                )
            )
        self.s.flush()

    def _raised(self, match: ScreeningMatch) -> None:
        self.new_matches.append(match)
        _ = publish_screening_event(
            self.s,
            MATCH_CREATED,
            entity="screening_match",
            entity_id=match.id,
            key=str(self.subject.id),
            data={
                "match_id": str(match.id),
                "subject_id": str(self.subject.id),
                "list_kind": match.list_kind.value,
                "band": match.band.value,
            },
        )


class ScreenOptions(TypedDict, total=False):
    """The optional keywords of ``screen`` (the one SP-03 signature, finding 2)."""

    lists: Collection[str] | None
    only_entries: Sequence[IndexedEntry] | None
    only_index: MemoryIndex | None
    require_versions: Mapping[str, int] | None
    batch_id: UUID | None


def _prepare(
    s: Session, view: ScreeningPolicyView, options: ScreenOptions
) -> tuple[list[ListSetMemberRef], ScreeningListSet, _Sources]:
    # The list set from the holder's versions, and the scoring IDF of its shared
    # members (Deviation 2).
    holder = get_index_holder()
    shard = holder.shared(s, require=options.get("require_versions"))
    private = holder.private(s)
    members = _members(s, view, shard, private, options.get("lists"))
    list_set = upsert_list_set(s, members)
    idf = holder.scoring_idf(
        shard,
        [m.list_code for m in members if m.list_version_id is not None],
        key=list_set.content_hash,
    )
    sources = _Sources(
        shard, private, idf, options.get("only_entries"), options.get("only_index")
    )
    return members, list_set, sources


def screen(
    s: Session,
    subject: ScreeningSubject,
    *,
    mode: RunMode,
    trigger: Trigger,
    **options: Unpack[ScreenOptions],
) -> RunResult:
    """Screen a subject and record the run, its hits and its findings.

    ``lists`` narrows the list set (a staff check). ``only_entries`` scores just
    those entries through a throwaway index, and ``only_index`` takes candidates
    from a caller's index instead of the holder's (3c's batches); either marks the
    run ``restricted_to_delta``. ``require_versions`` makes the holder hold at least
    those shared versions. A tenant without a policy in force gets
    ``POLICY_NOT_ASSIGNED`` and no run.

    Returns:
        The completed run, the subject, the stored hits and the new findings.

    Raises:
        ValueError: When both ``only_entries`` and ``only_index`` are given.

    """
    if (
        options.get("only_entries") is not None
        and options.get("only_index") is not None
    ):
        msg = "only_entries and only_index are exclusive"
        raise ValueError(msg)
    subject = _locked(s, subject)
    query = _query(s, subject)
    view = policy_for_run(s)
    started = time.monotonic()
    members, list_set, sources = _prepare(s, view, options)
    candidates = _candidates(query, members, sources)
    run = ScreeningRun(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        mode=mode,
        trigger_type=trigger.type,
        trigger_ref=trigger.ref,
        list_set_id=list_set.id,
        policy_snapshot_id=view.snapshot_id,
        matcher_version=MATCHER_VERSION,
        scoring_config_hash=view.config.config_hash(),
        query_fingerprint=query.fingerprint(),
        restricted_to_delta=(
            sources.only_entries is not None or sources.only_index is not None
        ),
        candidate_count=len(candidates),
        hit_count=0,
        new_match_count=0,
        outcome=subject.status,
        latency_ms=0,
        created_by=bound_context(s).actor_id,
        batch_id=options.get("batch_id"),
    )
    s.add(run)
    s.flush()
    writes = _Run(s, subject, run, view)
    writes.store_all(_score_all(query, candidates, members, view, sources.idf))
    return writes.complete(started)
