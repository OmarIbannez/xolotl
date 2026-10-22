"""What staff and API clients read of a decision (SP-04 spec §12.1, §15).

Staff read the whole row: the rules that fired with their versions, severities and
aggregates, the rule-set hash, the timings and the transaction it was linked to.
An API client reads the answer, its mode and when (D20): its ``reasons`` came with
the decide response as codes, and the stored ones name rules. A decision is found
by id through ``ix_txn_decision_id`` (plan 4c Deviation 7).
"""

from collections.abc import Sequence
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.decide.service import TIER_LIMIT
from xolotl.modules.monitoring.schemas import (
    DecisionClientResponse,
    DecisionReason,
    DecisionStaffResponse,
    DecisionSummary,
    DecisionTransaction,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform.errors import NotFoundError


def client_reasons(reasons: Sequence[dict[str, JsonValue]]) -> list[DecisionReason]:
    """Reduce stored reasons to what an API client may read, one per code.

    Returns:
        Each code once, in order; a ``TIER_LIMIT`` keeps its tier and limit code.

    """
    seen: set[str] = set()
    found: list[DecisionReason] = []
    for reason in reasons:
        code = str(reason["code"])
        if code in seen:
            continue
        seen.add(code)
        tier = reason.get("tier") if code == TIER_LIMIT else None
        limit_code = reason.get("limit_code") if code == TIER_LIMIT else None
        found.append(
            DecisionReason(
                code=code,
                tier=tier if isinstance(tier, str) else None,
                limit_code=limit_code if isinstance(limit_code, str) else None,
            )
        )
    return found


def _decision(s: Session, decision_id: UUID) -> TxnDecision:
    row = s.scalar(select(TxnDecision).where(TxnDecision.id == decision_id))
    if row is None:
        msg = "decision not found"
        raise NotFoundError(msg)
    return row


def decision_client_view(s: Session, decision_id: UUID) -> DecisionClientResponse:
    """Read a decision as its API client may; another tenant's is not found.

    Returns:
        The id, external id, decision, mode and time.

    """
    row = _decision(s, decision_id)
    return DecisionClientResponse(
        decision_id=row.id,
        external_id=row.external_id,
        decision=row.decision,
        mode=row.mode,
        decided_at=row.decided_at,
    )


def decision_staff_view(s: Session, decision_id: UUID) -> DecisionStaffResponse:
    """Read a decision with everything it read, for staff; 404 when unknown.

    Returns:
        The row, its reasons in full and the linked transaction, if any.

    """
    row = _decision(s, decision_id)
    linked: DecisionTransaction | None = None
    if row.transaction_id is not None and row.occurred_at is not None:
        tenant_id = row.tenant_id
        txn = s.get_one(Transaction, (tenant_id, row.occurred_at, row.transaction_id))
        linked = DecisionTransaction(
            id=txn.id, occurred_at=txn.occurred_at, status=txn.status
        )
    return DecisionStaffResponse(
        decision_id=row.id,
        external_id=row.external_id,
        decision=row.decision,
        mode=row.mode,
        decided_at=row.decided_at,
        rail=row.rail,
        relationship_id=row.relationship_id,
        account_id=row.account_id,
        reasons=row.reasons,
        ruleset_hash=row.ruleset_hash.hex(),
        policy_snapshot_id=row.policy_snapshot_id,
        latency_ms=row.latency_ms,
        db_ms=row.db_ms,
        actor=row.actor,
        transaction=linked,
    )


def decision_summary(s: Session, decision_id: UUID | None) -> DecisionSummary | None:
    """Summarise a transaction's decision for the hit detail (spec §12.1).

    Returns:
        The decision, mode and time, or None without a decision.

    """
    if decision_id is None:
        return None
    row = s.scalar(select(TxnDecision).where(TxnDecision.id == decision_id))
    if row is None:
        return None
    return DecisionSummary(
        decision_id=row.id,
        decision=row.decision,
        mode=row.mode,
        decided_at=row.decided_at,
    )
