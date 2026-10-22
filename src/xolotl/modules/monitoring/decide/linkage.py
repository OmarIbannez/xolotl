"""Link decisions to the transactions that arrive later (SP-04 spec §6.1 step 3).

A client asks ``POST /v1/transactions/decide`` before it executes a payment and
submits the executed transaction later under the same ``external_id``. Ingest
calls ``link_decisions`` with the rows a batch created (duplicates excluded), after
their inserts and before the processing enqueue, in the batch's transaction: the
latest unlinked decision of each ``external_id`` from the last seven days, made for
the same customer relationship (a client that reuses an id across customers links
nothing), gets the transaction's id and ``occurred_at``, and the transaction gets
the decision's id and ``decided_at`` (both composite foreign keys). Both updates
go by primary key, so each touches one row of one partition.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform import clock
from xolotl.platform.db import bound_context

LINK_WINDOW = timedelta(days=7)


def link_decisions(s: Session, rows: Sequence[tuple[UUID, datetime, str, UUID]]) -> int:
    """Link each new transaction to its external id's latest recent decision.

    Args:
        s: The ingest transaction's session.
        rows: ``(transaction_id, occurred_at, external_id, relationship_id)`` of
            the rows created; a decision of another relationship never links.

    Returns:
        How many transactions were linked.

    """
    if not rows:
        return 0
    by_external_id = {external_id: (txn_id, at) for txn_id, at, external_id, _ in rows}
    found = s.execute(
        select(TxnDecision.external_id, TxnDecision.decided_at, TxnDecision.id)
        .where(
            tuple_(TxnDecision.external_id, TxnDecision.relationship_id).in_([
                (external_id, relationship_id)
                for _, _, external_id, relationship_id in rows
            ]),
            TxnDecision.decided_at >= clock.now() - LINK_WINDOW,
            TxnDecision.transaction_id.is_(None),
        )
        .order_by(TxnDecision.external_id, TxnDecision.decided_at.desc())
        .ext(distinct_on(TxnDecision.external_id))
    ).all()
    if not found:
        return 0
    tenant_id = bound_context(s).require_tenant_id()
    decisions: list[dict[str, object]] = []
    transactions: list[dict[str, object]] = []
    for external_id, decided_at, decision_id in found:
        txn_id, occurred_at = by_external_id[external_id]
        decisions.append({
            "tenant_id": tenant_id,
            "decided_at": decided_at,
            "id": decision_id,
            "transaction_id": txn_id,
            "occurred_at": occurred_at,
        })
        transactions.append({
            "tenant_id": tenant_id,
            "occurred_at": occurred_at,
            "id": txn_id,
            "decision_id": decision_id,
            "decided_at": decided_at,
        })
    _ = s.execute(update(TxnDecision), decisions)
    _ = s.execute(update(Transaction), transactions)
    return len(found)
