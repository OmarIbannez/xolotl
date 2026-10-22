"""The staff rule hits API (SP-04 spec §9.6, §15; plan 4b B12).

``GET /v1/monitoring/hits`` is the queue, newest first, with the party masked;
the detail adds the evidence (aggregates, parameters, the rendered explanation),
the count of linked transactions and the newest 500 with their roles, each
counterparty masked; ``/hits/{id}/transactions`` pages every link. The reveal
is its own route (plan 4b Deviation 7): it records ``VIEW_PII`` on the hit for
each non-empty group (``party``, the ``counterparties`` of the transactions the
detail shows), and the party and
counterparty reveals record their own rows before anything is decrypted. Every
route is staff only (``require_access()`` without a scope refuses every API
client): a hit never reaches a client (tipping-off, D20, PRV-502). Any staff role
reads and reveals, as for parties and the match reveal. Another tenant's hit or
party is ``NOT_FOUND`` (row-level security). Routes hold no business rule.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import JsonValue
from sqlalchemy import Select, and_, func, select, tuple_
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.rules.definitions import (
    HitOutcome,
    RuleDefinition,
    Severity,
)
from xolotl.modules.monitoring.rules.hits import EVIDENCE_LIMIT
from xolotl.modules.monitoring.rules.models import (
    MonitoringRuleVersion,
    RuleHit,
    RuleHitTransaction,
)
from xolotl.modules.monitoring.schemas import (
    EvidenceView,
    HitDetail,
    HitQuery,
    HitReveal,
    HitSummary,
    HitTransactionView,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.party.schemas import PartyResponse
from xolotl.modules.party.service import get_party, reveal_pii, summaries_of
from xolotl.modules.screening.counterparty.views import (
    counterparty_summaries,
    reveal_counterparty,
)
from xolotl.platform.api.pagination import Page, PageDep, PageRequest, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.audit import VIEW_PII, AuditEvent, record
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError

if TYPE_CHECKING:
    from xolotl.modules.monitoring.rules.enums import HitTransactionRole

router = v1_router(prefix="/monitoring", tags=["monitoring-hits"])
party_router = v1_router(prefix="/parties", tags=["monitoring-hits"])

# Staff only: a route without a scope refuses every API client.
HitStaff = Annotated[Actor, Depends(require_access())]

ENTITY = "rule_hit"
SENTENCE_LOCALE = "es-MX"
# The detail and the reveal cover a hit's newest links, as many as the evidence a
# hit is created with; later WINDOW and COOLDOWN links are paged by
# GET /hits/{id}/transactions.
LINK_LIMIT = EVIDENCE_LIMIT


def _hits_query(query: HitQuery) -> Select[RuleHit]:
    stmt = select(RuleHit)
    if query.severity is not None:
        stmt = stmt.where(RuleHit.severity == query.severity.value)
    if query.rule_code is not None:
        stmt = stmt.where(RuleHit.rule_code == query.rule_code)
    if query.outcome is not None:
        stmt = stmt.where(RuleHit.outcome == query.outcome.value)
    if query.party_id is not None:
        stmt = stmt.where(RuleHit.party_id == query.party_id)
    if query.created_before is not None:
        stmt = stmt.where(RuleHit.created_at < query.created_before)
    if query.typology is not None:
        versions = select(MonitoringRuleVersion.id).where(
            MonitoringRuleVersion.definition["typology"].contains([query.typology])
        )
        stmt = stmt.where(RuleHit.rule_version_id.in_(versions))
    if query.transaction_id is not None:
        linked = select(RuleHitTransaction.hit_id).where(
            RuleHitTransaction.transaction_id == query.transaction_id
        )
        stmt = stmt.where(RuleHit.id.in_(linked))
    return stmt


def _page(
    session: Session, query: HitQuery, page: PageRequest
) -> tuple[list[RuleHit], str | None]:
    return paginate(
        session,
        _hits_query(query),
        ts_col=RuleHit.created_at,
        id_col=RuleHit.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )


def _definitions(
    session: Session, version_ids: set[UUID]
) -> dict[UUID, RuleDefinition]:
    if not version_ids:
        return {}
    rows = session.execute(
        select(MonitoringRuleVersion.id, MonitoringRuleVersion.definition).where(
            MonitoringRuleVersion.id.in_(version_ids)
        )
    ).all()
    return {
        version_id: RuleDefinition.model_validate(definition)
        for version_id, definition in rows
    }


def _decisions(session: Session, rows: Sequence[RuleHit]) -> dict[UUID, UUID | None]:
    # The triggering transaction's decision, read through its partition key.
    if not rows:
        return {}
    keys = [(row.first_occurred_at, row.first_transaction_id) for row in rows]
    found = session.execute(
        select(Transaction.id, Transaction.decision_id).where(
            tuple_(Transaction.occurred_at, Transaction.id).in_(keys)
        )
    ).all()
    return dict(found)


def _summary(
    row: RuleHit,
    *,
    definition: RuleDefinition,
    party: PartyResponse,
    decision_id: UUID | None,
) -> HitSummary:
    return HitSummary(
        id=row.id,
        rule_code=row.rule_code,
        rule_version_id=row.rule_version_id,
        typology=list(definition.typology),
        severity=Severity(row.severity),
        outcome=HitOutcome(row.outcome),
        key_kind=row.key_kind,
        key=row.key,
        party=party,
        relationship_id=row.relationship_id,
        window_start=row.window_start,
        window_end=row.window_end,
        fired_count=row.fired_count,
        cooldown_until=row.cooldown_until,
        first_transaction_id=row.first_transaction_id,
        first_occurred_at=row.first_occurred_at,
        decision_id=decision_id,
        created_at=row.created_at,
    )


def _summaries(session: Session, rows: Sequence[RuleHit]) -> list[HitSummary]:
    definitions = _definitions(session, {row.rule_version_id for row in rows})
    parties = summaries_of(session, {row.party_id for row in rows})
    decisions = _decisions(session, rows)
    return [
        _summary(
            row,
            definition=definitions[row.rule_version_id],
            party=parties[row.party_id],
            decision_id=decisions.get(row.first_transaction_id),
        )
        for row in rows
    ]


def _hit(session: Session, hit_id: UUID) -> RuleHit:
    tenant_id = bound_context(session).require_tenant_id()
    row = session.get(RuleHit, (tenant_id, hit_id))
    if row is None:
        msg = "rule hit not found"
        raise NotFoundError(msg)
    return row


def _of_hit[T](stmt: Select[T], hit: RuleHit) -> Select[T]:
    # The hit's linked transactions; the join carries each one's partition key.
    return stmt.join(
        RuleHitTransaction,
        and_(
            RuleHitTransaction.tenant_id == Transaction.tenant_id,
            RuleHitTransaction.occurred_at == Transaction.occurred_at,
            RuleHitTransaction.transaction_id == Transaction.id,
        ),
    ).where(RuleHitTransaction.hit_id == hit.id)


def _newest[T](stmt: Select[T], hit: RuleHit) -> Select[T]:
    return (
        _of_hit(stmt, hit)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(LINK_LIMIT)
    )


def _transaction_views(
    session: Session, hit: RuleHit, rows: Sequence[Transaction]
) -> list[HitTransactionView]:
    if not rows:
        return []
    roles: dict[UUID, HitTransactionRole] = dict(
        session.execute(
            select(RuleHitTransaction.transaction_id, RuleHitTransaction.role).where(
                RuleHitTransaction.hit_id == hit.id,
                RuleHitTransaction.transaction_id.in_([txn.id for txn in rows]),
            )
        ).all()
    )
    counterparties = counterparty_summaries(
        session, {txn.counterparty_id for txn in rows if txn.counterparty_id}
    )
    return [
        HitTransactionView(
            transaction_id=txn.id,
            occurred_at=txn.occurred_at,
            role=roles[txn.id],
            direction=txn.direction,
            instrument=txn.instrument,
            rail=txn.rail,
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            counterparty=(
                None
                if txn.counterparty_id is None
                else counterparties[txn.counterparty_id]
            ),
        )
        for txn in rows
    ]


def _detail(session: Session, hit: RuleHit) -> HitDetail:
    definition = _definitions(session, {hit.rule_version_id})[hit.rule_version_id]
    summary = _summary(
        hit,
        definition=definition,
        party=summaries_of(session, {hit.party_id})[hit.party_id],
        decision_id=_decisions(session, [hit]).get(hit.first_transaction_id),
    )
    newest = session.scalars(_newest(select(Transaction), hit)).all()
    count = session.scalar(
        select(func.count())
        .select_from(RuleHitTransaction)
        .where(RuleHitTransaction.hit_id == hit.id)
    )
    return HitDetail.model_validate({
        **summary.model_dump(),
        "evidence": EvidenceView.model_validate(hit.evidence),
        "transactions": _transaction_views(session, hit, newest),
        "transaction_count": count or 0,
        "assignment_id": hit.assignment_id,
        "policy_snapshot_id": hit.policy_snapshot_id,
        "definition_hash": hit.definition_hash.hex(),
        "dedup_key": hit.dedup_key,
        "sentence": definition.render(SENTENCE_LOCALE),
    })


def _view_pii(session: Session, hit: RuleHit, detail: dict[str, JsonValue]) -> None:
    _ = record(
        session,
        AuditEvent(
            action=VIEW_PII,
            entity_type=ENTITY,
            entity_id=str(hit.id),
            detail=detail,
        ),
    )


def _reveal(session: Session, hit: RuleHit) -> HitReveal:
    # The counterparties of the transactions the detail shows, nothing older: one
    # column of the newest links, never whole rows.
    shown = session.scalars(_newest(select(Transaction.counterparty_id), hit)).all()
    counterparty_ids = sorted({cid for cid in shown if cid is not None})
    _view_pii(session, hit, {"group": "party"})
    party = reveal_pii(session, hit.party_id)
    if counterparty_ids:
        _view_pii(
            session, hit, {"group": "counterparties", "count": len(counterparty_ids)}
        )
    return HitReveal(
        party=party,
        counterparties={
            counterparty_id: reveal_counterparty(session, counterparty_id)
            for counterparty_id in counterparty_ids
        },
    )


@router.get("/hits")
def list_hits(
    _staff: HitStaff,
    session: SessionDep,
    page: PageDep,
    query: Annotated[HitQuery, Query()],
) -> Page[HitSummary]:
    rows, next_cursor = _page(session, query, page)
    return Page(items=_summaries(session, rows), next_cursor=next_cursor)


@router.get("/hits/{hit_id}")
def get_hit(hit_id: UUID, _staff: HitStaff, session: SessionDep) -> HitDetail:
    return _detail(session, _hit(session, hit_id))


@router.get("/hits/{hit_id}/reveal")
def reveal_hit(hit_id: UUID, _staff: HitStaff, session: SessionDep) -> HitReveal:
    return _reveal(session, _hit(session, hit_id))


@router.get("/hits/{hit_id}/transactions")
def list_hit_transactions(
    hit_id: UUID, _staff: HitStaff, session: SessionDep, page: PageDep
) -> Page[HitTransactionView]:
    hit = _hit(session, hit_id)
    rows, next_cursor = paginate(
        session,
        _of_hit(select(Transaction), hit),
        ts_col=Transaction.occurred_at,
        id_col=Transaction.id,
        key=lambda row: (row.occurred_at, row.id),
        page=page,
    )
    return Page(items=_transaction_views(session, hit, rows), next_cursor=next_cursor)


@party_router.get("/{party_id}/hits")
def list_party_hits(
    party_id: UUID, _staff: HitStaff, session: SessionDep, page: PageDep
) -> Page[HitSummary]:
    _ = get_party(session, party_id)
    rows, next_cursor = _page(session, HitQuery(party_id=party_id), page)
    return Page(items=_summaries(session, rows), next_cursor=next_cursor)
