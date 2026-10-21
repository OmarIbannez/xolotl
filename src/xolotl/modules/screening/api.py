"""What the screening routes do, for staff and for API clients (SP-03 spec §12).

A check screens one party or one inline subject synchronously. The views come in two
audiences: an API client sees status fields only (FR-SC-013), staff also see hits,
findings and their explanations. The match detail is masked and audits nothing; the
reveal shows plaintext to a person and records one ``VIEW_PII`` per non-empty group
(Deviation 3). Personal values are decrypted for processing only everywhere else.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from xolotl.modules.party.masking import initials, mask_identifier
from xolotl.modules.party.service import get_party
from xolotl.modules.screening.catalog.models import ListEntryRow, PrivateListEntryRow
from xolotl.modules.screening.catalog.private import private_entry_of
from xolotl.modules.screening.engine.enums import (
    OPEN_MATCH_STATUSES,
    BatchKind,
    BatchStatus,
    HitBand,
    MatchStatus,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.models import (
    ScreeningBatch,
    ScreeningHit,
    ScreeningListSetMember,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.queries import (
    AdHocSubjectInput,
    query_for_ad_hoc,
    query_for_party,
)
from xolotl.modules.screening.engine.screen import (
    RunResult,
    Trigger,
    screen,
    subject_query,
)
from xolotl.modules.screening.engine.subjects import (
    create_ad_hoc_subject,
    subject_for_party,
)
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.lists import IdType
from xolotl.modules.screening.matching import NameQuery
from xolotl.modules.screening.review.actions import escalated_by, evaluate_actions
from xolotl.modules.screening.review.models import (
    MatchDisposition,
    ScreeningWhitelist,
)
from xolotl.modules.screening.schemas import (
    BatchResponse,
    CheckResponse,
    CheckStaffResponse,
    DispositionView,
    HitHistoryItem,
    HitView,
    ListSetMemberView,
    MaskedSubject,
    MatchDetail,
    MatchReveal,
    MatchSummary,
    PartyScreeningResponse,
    PartyScreeningStaffResponse,
    RevealedSubjectIdentifier,
    RunResponse,
    RunStaffResponse,
)
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.db import bound_context
from xolotl.platform.enums import ActorType
from xolotl.platform.errors import NotFoundError, ValidationFailedError

if TYPE_CHECKING:
    from xolotl.platform.crypto.encrypted import Encrypted

_STAFF_ONLY_LISTS = "only staff narrow a check to some lists"
# The masked label of a counterparty known only by its wallet.
WALLET_LABEL = "WALLET"


class CheckRequest(BaseModel):
    """A synchronous check of one party or one inline subject, never both."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    party_id: UUID | None = None
    subject: AdHocSubjectInput | None = None
    # Staff only: narrows the run to these list codes.
    lists: list[str] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _one_subject(self) -> Self:
        if (self.party_id is None) == (self.subject is None):
            msg = "give exactly one of party_id and subject"
            raise ValueError(msg)
        return self


# ---- checks ---------------------------------------------------------------------


def run_check(s: Session, body: CheckRequest) -> RunResult:
    """Screen a party or an inline subject now, in mode ``AD_HOC`` (Deviation 4).

    Staff runs are triggered ``CONSOLE``, API clients' ``API``, with the request id
    as the trigger reference. An inline subject's check is audited
    ``screening.check_requested`` with the run id only, never its input.

    Returns:
        The completed run.

    Raises:
        ValidationFailedError: When an API client sends ``lists``.

    """
    ctx = bound_context(s)
    staff = ctx.actor_type is ActorType.STAFF
    if body.lists is not None and not staff:
        raise ValidationFailedError(_STAFF_ONLY_LISTS)
    trigger = Trigger(
        TriggerType.CONSOLE if staff else TriggerType.API, ctx.request_id or None
    )
    if body.party_id is not None:
        subject = subject_for_party(s, body.party_id, for_update=True)
        return screen(
            s, subject, mode=RunMode.AD_HOC, trigger=trigger, lists=body.lists
        )
    subject = create_ad_hoc_subject(s, cast("AdHocSubjectInput", body.subject))
    result = screen(s, subject, mode=RunMode.AD_HOC, trigger=trigger, lists=body.lists)
    _ = record(
        s,
        AuditEvent(
            action="screening.check_requested",
            entity_type="screening_subject",
            entity_id=str(subject.id),
            detail={"run_id": str(result.run.id)},
        ),
    )
    return result


def hit_view(hit: ScreeningHit) -> HitView:
    """Build the staff view of a stored hit.

    Returns:
        The hit with its stored explanation.

    """
    return HitView(
        id=hit.id,
        list_code=hit.list_code,
        list_kind=str(hit.features.get("list_kind", "")),
        band=hit.band,
        score=hit.score,
        auto_reason=hit.auto_reason,
        match_id=hit.match_id,
        features=hit.features,
    )


def check_response(
    result: RunResult, *, staff: bool
) -> CheckStaffResponse | CheckResponse:
    """Build the response of a check for its audience.

    Returns:
        Status fields for an API client; the hits too for staff.

    """
    subject = result.subject
    base = CheckResponse(
        run_id=result.run.id,
        subject_id=subject.id,
        status=subject.status,
        screened_at=subject.last_screened_at or result.run.created_at,
    )
    if not staff:
        return base
    return CheckStaffResponse.model_validate({
        **base.model_dump(),
        "hits": [hit_view(h) for h in result.hits],
    })


# ---- runs and parties -----------------------------------------------------------


def _hits(s: Session, run_id: UUID) -> list[ScreeningHit]:
    return list(
        s.scalars(
            select(ScreeningHit)
            .where(ScreeningHit.run_id == run_id)
            .order_by(ScreeningHit.score.desc(), ScreeningHit.id)
        )
    )


def _members(s: Session, list_set_id: UUID) -> list[ScreeningListSetMember]:
    return list(
        s.scalars(
            select(ScreeningListSetMember)
            .where(ScreeningListSetMember.list_set_id == list_set_id)
            .order_by(ScreeningListSetMember.list_code)
        )
    )


def run_view(
    s: Session, run_id: UUID, *, staff: bool
) -> RunStaffResponse | RunResponse:
    """Build the view of one run of the session's tenant.

    Returns:
        Status fields for an API client; the list set, versions and hits for staff.

    Raises:
        NotFoundError: When the tenant has no such run, or an API client asks for a
            counterparty's run (tipping-off, SP-04 spec §15).

    """
    run = s.get(ScreeningRun, (bound_context(s).require_tenant_id(), run_id))
    if run is None:
        msg = "run not found"
        raise NotFoundError(msg)
    subject = s.get_one(ScreeningSubject, (run.tenant_id, run.subject_id))
    if not staff and subject.counterparty_id is not None:
        msg = "run not found"
        raise NotFoundError(msg)
    base = RunResponse(
        id=run.id,
        subject_id=subject.id,
        party_id=subject.party_id,
        mode=run.mode,
        outcome=run.outcome,
        created_at=run.created_at,
    )
    if not staff:
        return base
    return RunStaffResponse.model_validate({
        **base.model_dump(),
        "trigger_type": run.trigger_type,
        "trigger_ref": run.trigger_ref,
        "list_set": [
            ListSetMemberView(
                list_code=m.list_code,
                list_version_id=m.list_version_id,
                private_list_version_id=m.private_list_version_id,
            )
            for m in _members(s, run.list_set_id)
        ],
        "matcher_version": run.matcher_version,
        "scoring_config_hash": run.scoring_config_hash.hex(),
        "policy_snapshot_id": run.policy_snapshot_id,
        "candidate_count": run.candidate_count,
        "hit_count": run.hit_count,
        "new_match_count": run.new_match_count,
        "latency_ms": run.latency_ms,
        "hits": [hit_view(h) for h in _hits(s, run.id)],
    })


def match_summaries(
    s: Session, matches: Sequence[ScreeningMatch]
) -> list[MatchSummary]:
    """Build the queue rows of some findings, with their subjects masked.

    The page's subjects are loaded in one query; each party subject then loads
    its profile (four queries: party, person, organization and identifiers, with
    one decrypt per encrypted field), however many findings it has. Pages are at
    most 200 rows. Subjects are shown by initials and masked identifiers only,
    so nothing is audited. No query is issued for an empty page.

    Returns:
        One summary per finding, in the given order.

    """
    if not matches:
        return []
    subject_ids = {m.subject_id for m in matches}
    subjects = {
        subject.id: _masked(s, subject)
        for subject in s.scalars(
            select(ScreeningSubject).where(ScreeningSubject.id.in_(subject_ids))
        )
    }
    return [_summary(m, subjects[m.subject_id]) for m in matches]


def _summary(match: ScreeningMatch, subject: MaskedSubject) -> MatchSummary:
    return MatchSummary(
        id=match.id,
        subject_id=match.subject_id,
        party_id=subject.party_id,
        subject=subject,
        list_code=match.list_code,
        list_kind=match.list_kind,
        band=match.band,
        score=match.score,
        status=match.status,
        suggested_disposition=match.suggested_disposition,
        created_at=match.created_at,
        updated_at=match.updated_at,
    )


def party_view(
    s: Session, party_id: UUID, *, staff: bool
) -> PartyScreeningStaffResponse | PartyScreeningResponse:
    """Build a party's screening view without creating its subject.

    A party never screened has no status and covers no list.

    Returns:
        Status, last screening and the lists covered; staff also get the findings.

    """
    party = get_party(s, party_id)
    subject = s.scalars(
        select(ScreeningSubject).where(ScreeningSubject.party_id == party.id)
    ).one_or_none()
    covered: list[str] = []
    if subject is not None and subject.last_run_id is not None:
        run = s.get_one(ScreeningRun, (subject.tenant_id, subject.last_run_id))
        covered = [m.list_code for m in _members(s, run.list_set_id)]
    screened = subject is not None and subject.last_screened_at is not None
    base = PartyScreeningResponse(
        party_id=party.id,
        status=subject.status if subject is not None and screened else None,
        last_screened_at=None if subject is None else subject.last_screened_at,
        lists_covered=covered,
    )
    if not staff:
        return base
    findings: list[ScreeningMatch] = []
    if subject is not None:
        findings = list(
            s.scalars(
                select(ScreeningMatch)
                .where(ScreeningMatch.subject_id == subject.id)
                .order_by(ScreeningMatch.created_at.desc(), ScreeningMatch.id.desc())
            )
        )
    return PartyScreeningStaffResponse.model_validate({
        **base.model_dump(),
        "open_finding_count": sum(f.status in OPEN_MATCH_STATUSES for f in findings),
        "findings": match_summaries(s, findings),
    })


# ---- the match queue and detail -------------------------------------------------


def matches_query(
    *,
    status: MatchStatus | None,
    band: HitBand | None,
    list_code: str | None,
    list_kind: ListKind | None,
    created_before: datetime | None,
) -> Select[ScreeningMatch]:
    """Select the tenant's findings that pass the queue filters.

    Returns:
        The statement, unordered; the caller paginates it.

    """
    query = select(ScreeningMatch)
    if status is not None:
        query = query.where(ScreeningMatch.status == status)
    if band is not None:
        query = query.where(ScreeningMatch.band == band)
    if list_code is not None:
        query = query.where(ScreeningMatch.list_code == list_code)
    if list_kind is not None:
        query = query.where(ScreeningMatch.list_kind == list_kind)
    if created_before is not None:
        query = query.where(ScreeningMatch.created_at < created_before)
    return query


def batches_query(
    *, kind: BatchKind | None, status: BatchStatus | None
) -> Select[ScreeningBatch]:
    """Select the tenant's re-screen batches that pass the filters.

    Returns:
        The statement, unordered; the caller paginates it.

    """
    query = select(ScreeningBatch)
    if kind is not None:
        query = query.where(ScreeningBatch.kind == kind)
    if status is not None:
        query = query.where(ScreeningBatch.status == status)
    return query


def batch_response(batch: ScreeningBatch) -> BatchResponse:
    """Describe a batch's progress.

    Returns:
        The response model.

    """
    return BatchResponse(
        id=batch.id,
        kind=batch.kind,
        ref=batch.ref,
        status=batch.status,
        total=batch.total,
        done=batch.done,
        runs=batch.runs,
        new_matches=batch.new_matches,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        last_error=batch.last_error,
    )


def _match(s: Session, match_id: UUID) -> ScreeningMatch:
    match = s.get(ScreeningMatch, (bound_context(s).require_tenant_id(), match_id))
    if match is None:
        msg = "match not found"
        raise NotFoundError(msg)
    return match


def _subject_query(s: Session, subject: ScreeningSubject) -> NameQuery:
    # Decrypted for processing: the caller decides what a person is shown.
    if subject.party_id is not None:
        return query_for_party(s, subject.party_id)
    if subject.counterparty_id is not None:
        # The minimal counterparty branch (SP-04 plan 4c, C1); C3 extends it.
        return subject_query(s, subject)
    stored = cast("Encrypted[dict[str, object]]", subject.input)
    return query_for_ad_hoc(AdHocSubjectInput.model_validate(stored.reveal(session=s)))


def _label(query: NameQuery) -> str:
    # A name-less counterparty is labelled by where it came from, never by a value.
    if query.names:
        return initials(query.names[0])
    if any(i.id_type is IdType.DIGITAL_CURRENCY_ADDRESS for i in query.identifiers):
        return WALLET_LABEL
    return ""


def _masked(s: Session, subject: ScreeningSubject) -> MaskedSubject:
    query = _subject_query(s, subject)
    return MaskedSubject(
        kind=subject.kind,
        party_id=subject.party_id,
        entity_type=query.entity_type.value,
        initials=_label(query),
        identifiers=[
            f"{i.id_type.value} {mask_identifier(i.normalized)}"
            for i in query.identifiers
        ],
    )


def _history(s: Session, match_id: UUID) -> list[tuple[ScreeningHit, ScreeningRun]]:
    rows = s.execute(
        select(ScreeningHit, ScreeningRun)
        .join(
            ScreeningRun,
            (ScreeningRun.tenant_id == ScreeningHit.tenant_id)
            & (ScreeningRun.id == ScreeningHit.run_id),
        )
        .where(ScreeningHit.match_id == match_id)
        .order_by(ScreeningRun.created_at, ScreeningRun.id)
    )
    return [(hit, run) for hit, run in rows]


def _dispositions(s: Session, match_id: UUID) -> list[MatchDisposition]:
    return list(
        s.scalars(
            select(MatchDisposition)
            .where(MatchDisposition.match_id == match_id)
            .order_by(MatchDisposition.decided_at, MatchDisposition.id)
        )
    )


def _whitelist_id(s: Session, match: ScreeningMatch) -> UUID | None:
    return s.scalar(
        select(ScreeningWhitelist.id).where(
            ScreeningWhitelist.subject_id == match.subject_id,
            ScreeningWhitelist.list_code == match.list_code,
            ScreeningWhitelist.source_entry_key == match.source_entry_key,
            ScreeningWhitelist.entry_content_hash == match.entry_content_hash,
            ScreeningWhitelist.revoked_at.is_(None),
        )
    )


def _shared_entry(s: Session, match: ScreeningMatch) -> dict[str, JsonValue] | None:
    if match.list_entry_id is None:
        return None
    row = s.get_one(ListEntryRow, match.list_entry_id)
    return row.entry


def match_detail(s: Session, match_id: UUID) -> MatchDetail:
    """Build the staff detail of a finding, masked, for the current user.

    The subject is shown by initials and masked identifiers, so nothing is audited.
    ``allowed_actions`` and ``blocked_reasons`` come from the action matrix for the
    session's user (D15, D20).

    Returns:
        The finding with its subject, entry, hits, dispositions and actions.

    Raises:
        NotFoundError: When the tenant has no such finding, or it has no hit.

    """
    match = _match(s, match_id)
    subject = s.get_one(ScreeningSubject, (match.tenant_id, match.subject_id))
    history = _history(s, match.id)
    if not history:
        msg = "match has no hit"
        raise NotFoundError(msg)
    ctx = bound_context(s)
    escalator, _ = escalated_by(s, match.id)
    actions = evaluate_actions(
        match, escalated_by=escalator, roles=ctx.roles, user_id=UUID(ctx.actor_id)
    )
    summary = _summary(match, _masked(s, subject))
    return MatchDetail.model_validate({
        **summary.model_dump(),
        "entry": _shared_entry(s, match),
        "origin_hit": hit_view(history[0][0]),
        "latest_hit": hit_view(history[-1][0]),
        "history": [
            HitHistoryItem(
                hit_id=hit.id,
                run_id=run.id,
                created_at=run.created_at,
                score=hit.score,
                band=hit.band,
            )
            for hit, run in history
        ],
        "dispositions": [
            DispositionView(
                id=d.id,
                decision=d.decision,
                decided_by_type=d.decided_by_type,
                decided_by=d.decided_by,
                decided_at=d.decided_at,
            )
            for d in _dispositions(s, match.id)
        ],
        "whitelist_id": _whitelist_id(s, match),
        "allowed_actions": list(actions.allowed),
        "blocked_reasons": {k: list(v) for k, v in actions.blocked.items()},
    })


def _view_pii(
    s: Session, entity_type: str, entity_id: UUID, detail: dict[str, JsonValue]
) -> None:
    _ = record(
        s,
        AuditEvent(
            action=VIEW_PII,
            entity_type=entity_type,
            entity_id=str(entity_id),
            detail=detail,
        ),
    )


def reveal_match(s: Session, match_id: UUID) -> MatchReveal:
    """Show a person a finding's personal values, auditing each group.

    The groups are ``subject`` (names, birth date, identifier values; entity
    ``screening_subject``), ``private_entry`` (a private list's entry; entity
    ``screening_match``) and ``rationales`` (entity ``screening_match``, with the
    count), each recorded as one ``VIEW_PII`` when it holds anything.

    Returns:
        The plaintext values.

    """
    match = _match(s, match_id)
    subject = s.get_one(ScreeningSubject, (match.tenant_id, match.subject_id))
    query = _subject_query(s, subject)
    dispositions = _dispositions(s, match.id)
    if query.names or query.birth_date is not None or query.identifiers:
        _view_pii(s, "screening_subject", subject.id, {"group": "subject"})
    private_entry: dict[str, JsonValue] | None = None
    if match.private_list_entry_id is not None:
        _view_pii(s, "screening_match", match.id, {"group": "private_entry"})
        row = s.get_one(
            PrivateListEntryRow, (match.tenant_id, match.private_list_entry_id)
        )
        private_entry = cast(
            "dict[str, JsonValue]",
            json.loads(private_entry_of(s, row).canonical_json()),
        )
    if dispositions:
        _view_pii(
            s,
            "screening_match",
            match.id,
            {"group": "rationales", "count": len(dispositions)},
        )
    return MatchReveal(
        subject_names=list(query.names),
        birth_date=query.birth_date,
        identifiers=[
            RevealedSubjectIdentifier(id_type=i.id_type.value, value=i.normalized)
            for i in query.identifiers
        ],
        private_entry=private_entry,
        rationales={d.id: d.rationale.reveal(session=s) for d in dispositions},
    )
