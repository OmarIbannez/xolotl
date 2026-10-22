"""Synchronous decisions: ``POST /v1/transactions/decide`` (SP-04 spec §12.1).

``decide`` answers ``ALLOW``, ``REVIEW`` or ``BLOCK`` for one validated
transaction about to be executed, in six steps under a wall-clock budget
(``XOLOTL_MONITORING_DECIDE_BUDGET_MS``, plan 4c Deviation 8):

1. Resolve ``customer_ref`` (missing or not ``ACTIVE``: 422 ``CUSTOMER_REF_UNKNOWN``)
   and ``account_ref`` (read, never created; bound to another customer: 422
   ``ACCOUNT_REF_CONFLICT``), and read the rail's fail mode.
2. Resolve today's policy snapshot and the sync rules of the customer's product.
3. Enrich the candidate (``enrich_input``): units, periods and the counterparty's
   cached screening status, read by its key and never written, so a counterparty
   never seen is ``UNSCREENED``, which no sync rule treats as clear.
4. Seed a ``MemoryAggregateStore`` with one ``ix_agg_bucket_window`` range read per
   declaration of the sync rules, then add the candidate, so it counts in its own
   aggregates as it does in processing. A declaration whose backfill has not run
   reads ``INCOMPLETE``.
5. Read the customer's profile and evaluate the sync rules with the pure
   evaluator: ``BLOCK`` when a ``TIER_LIMIT`` with outcome ``BLOCK`` or a
   ``CRITICAL`` rule fires, ``REVIEW`` when anything else fires, else ``ALLOW``.
6. Store the ``txn_decision`` row (``mode = EVALUATED``) and publish the staff-only
   ``monitoring.decision_issued.v1``; for each ``TIER_LIMIT`` firing record the
   month's ``tier_limit_notice`` through ``notice_tier_limit`` (``DECIDE``), which
   publishes ``monitoring.tier_limit_reached.v1`` only for the first path to see
   the overrun.

The elapsed time is checked after steps 1 to 5. Over budget, or when a step meets
a database error, a missing unit value or policy (``ParkTransactionError``,
``UnitValueMissingError``, ``PolicyNotAssignedError``) or an unavailable screening
index, the rail's fail mode answers: ``FAIL_OPEN`` with ``REVIEW``, ``FAIL_CLOSED``
with ``BLOCK``, both with the one reason ``DECIDE_UNAVAILABLE``; never a 503. The
row is still written when it can be; a row that cannot be written is logged
``monitoring.decision_unrecorded`` and the answer goes out anyway (Deviation 9).

When step 1 itself fails on the database, before the rail's setting was read, the
configured default fail mode answers and no row can be written (it needs the
relationship); the answer is logged ``monitoring.decision_unrecorded``. A second
decision of one ``external_id`` stored at the very same instant is retried once a
microsecond later, so ``ux_txn_decision_external`` never drops an answered one.

The decide path never waits on processing: it takes no advisory lock and reads
``agg_bucket`` without row locks. A ``statement_timeout`` of the remaining budget
cancels a step's statement that stalls (a slow read), and a ``lock_timeout`` of the
budget bounds the few writes that could meet another transaction's uncommitted row
(the policy snapshot, the notice). A notice that times out is left to the
transaction holding it; one that fails on the database is recorded by the next
firing of the month (the next decision or the processed transaction), and the
client already had ``TIER_LIMIT`` in this answer. Nothing here creates a
partition: ``txn_decision`` falls into its ``DEFAULT`` partition when the month's
is missing (partition maintenance runs months ahead).

``latency_ms``, on the row and in the answer, is the whole call: the steps, the
row's insert and the notice. ``db_ms`` is the time of the read steps (2 to 4).
The first decision of a process, or the first after the rule-set cache
(``XOLOTL_MONITORING_RULE_CACHE_SECONDS``) expired, loads the rule set and the
policy cold and may pass a tight budget: on a ``FAIL_CLOSED`` rail that answer is
``BLOCK``. Nothing is warmed at start-up (the caches are per tenant, date and
product); C10 measures it.

The client sees the decision, its id, the latency and ``reasons`` of codes only:
``TIER_LIMIT`` with the tier and limit code, ``RISK_REVIEW`` for any other firing,
never a rule name, a hit, an aggregate or a screening status (D20, §21 Q5). The
row's ``reasons`` keep the rule versions and aggregates for staff.
"""

import logging
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from uuid import UUID

from psycopg.errors import UniqueViolation
from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.aggregates.postgres import (
    PostgresAggregateStore,
    backfilled_ids,
)
from xolotl.modules.monitoring.aggregates.store import AggregateKey
from xolotl.modules.monitoring.aggregates.windows import (
    bucket_start_for,
    window_bounds,
)
from xolotl.modules.monitoring.decide.enums import (
    DecisionMode,
    DecisionOutcome,
    FailMode,
)
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.decide.rails import default_fail_mode, fail_mode_for
from xolotl.modules.monitoring.enrich.policy import policy_date, snapshot_for
from xolotl.modules.monitoring.enrich.service import (
    ParkTransactionError,
    enrich_input,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.errors import body_error_items
from xolotl.modules.monitoring.events import DECISION_ISSUED, publish_monitoring_event
from xolotl.modules.monitoring.rules.assignments import LoadedRule, active_rules
from xolotl.modules.monitoring.rules.definitions import (
    HitOutcome,
    RuleKind,
    Severity,
)
from xolotl.modules.monitoring.rules.enums import NoticeSource
from xolotl.modules.monitoring.rules.evaluate import evaluate_rules, profile_for
from xolotl.modules.monitoring.rules.kinds import Firing
from xolotl.modules.monitoring.rules.tier_limits import (
    LIMIT_CODE_MONTHLY_OPS,
    notice_tier_limit,
)
from xolotl.modules.monitoring.txn.accounts import DEFAULT_REF_PREFIX, find_account
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    FieldError,
    FieldErrorCode,
    TransactionInput,
)
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.service import RelationshipRef, relationship_by_external_ref
from xolotl.modules.screening.errors import ScreeningIndexUnavailableError
from xolotl.platform import clock
from xolotl.platform.calendar import load_holidays
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.db import bound_context
from xolotl.platform.enums import HolidayScope
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.ids import uuid7
from xolotl.platform.policy.durations import iso_duration_days
from xolotl.platform.policy.errors import PolicyNotAssignedError
from xolotl.platform.units import UnitService
from xolotl.platform.units.errors import UnitValueMissingError

logger = logging.getLogger(__name__)

DECIDE_UNAVAILABLE = "DECIDE_UNAVAILABLE"
RISK_REVIEW = "RISK_REVIEW"
TIER_LIMIT = "TIER_LIMIT"
DECISION_UNRECORDED = "monitoring.decision_unrecorded"
NOTICE_DEFERRED = "monitoring.tier_limit_notice_deferred"

# What switches a decision to the rail's fail mode besides the budget.
_FAILURES = (
    SQLAlchemyError,
    ParkTransactionError,
    UnitValueMissingError,
    PolicyNotAssignedError,
    ScreeningIndexUnavailableError,
)
_ANSWER: dict[FailMode, tuple[DecisionOutcome, DecisionMode]] = {
    FailMode.FAIL_OPEN: (DecisionOutcome.REVIEW, DecisionMode.FAIL_OPEN),
    FailMode.FAIL_CLOSED: (DecisionOutcome.BLOCK, DecisionMode.FAIL_CLOSED),
}
_UNAVAILABLE: tuple[dict[str, JsonValue], ...] = ({"code": DECIDE_UNAVAILABLE},)
# Sets a timeout setting for the rest of the transaction; returns the old value.
_SET_TIMEOUT = text("SELECT current_setting(:name), set_config(:name, :value, true)")
_RESTORE_TIMEOUT = text("SELECT set_config(:name, :value, true)")
EXTERNAL_INDEX = "ux_txn_decision_external"
_EXTERNAL_PARTITION_SUFFIX = "_tenant_id_external_id_decided_at_idx"
# The least statement_timeout steps 1 to 5 get, however little budget is left, so
# a loaded machine never cancels step 1 and loses the fail mode's row with it.
STATEMENT_TIMEOUT_FLOOR_MS = 5
# A second decision of one external_id at the very same instant moves this far, so
# ux_txn_decision_external keeps both.
_TIE_BREAK = timedelta(microseconds=1)
# An ACCUM window measured on the 360-day scale may fall up to a week short of the
# calendar (P1Y is 360 days); the seed reads that much further back.
_ACCUM_SLACK = timedelta(days=7)
_MS = 1000
_CUSTOMER_REF = "customer_ref"
_ACCOUNT_REF = "account_ref"


class _OverBudgetError(Exception):
    """The decision took longer than the budget allows."""


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """A decision as ``decide`` returns it; ``reasons`` are the row's, for staff."""

    decision: DecisionOutcome
    decision_id: UUID
    mode: DecisionMode
    reasons: tuple[dict[str, JsonValue], ...]
    latency_ms: int


@dataclass(slots=True)
class _Timer:
    """The budget's wall clock, and the time spent in the read steps (2 to 4)."""

    budget_ms: int
    started: float = field(default_factory=time.monotonic)
    db_seconds: float = 0.0

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * _MS)

    def check(self) -> None:
        if (time.monotonic() - self.started) * _MS > self.budget_ms:
            raise _OverBudgetError

    @contextmanager
    def reading(self) -> Generator[None]:
        began = time.monotonic()
        try:
            yield
        finally:
            self.db_seconds += time.monotonic() - began


@dataclass(frozen=True, slots=True)
class _Resolved:
    relationship: RelationshipRef
    account_id: UUID | None
    fail_mode: FailMode


@dataclass(frozen=True, slots=True)
class _Evaluated:
    decision: DecisionOutcome
    reasons: tuple[dict[str, JsonValue], ...]
    ruleset_hash: bytes
    snapshot_id: UUID
    txn: EnrichedTransaction
    limits: tuple[HitOutcome, ...]


def _invalid(field_name: str, code: FieldErrorCode) -> ValidationFailedError:
    return ValidationFailedError(
        errors=body_error_items([FieldError(index=0, field=field_name, code=code)])
    )


def _resolve(s: Session, item: TransactionInput) -> _Resolved:
    relationship = relationship_by_external_ref(s, [item.customer_ref]).get(
        item.customer_ref
    )
    if (
        relationship is None
        or relationship.status is not CustomerRelationshipStatus.ACTIVE
    ):
        raise _invalid(_CUSTOMER_REF, FieldErrorCode.CUSTOMER_REF_UNKNOWN)
    if item.account_ref is not None and item.account_ref.startswith(DEFAULT_REF_PREFIX):
        # Reserved for DEFAULT accounts, as ingest refuses it.
        raise _invalid(_ACCOUNT_REF, FieldErrorCode.INVALID)
    account = find_account(s, relationship.id, item.account_ref)
    if account is not None and account.relationship_id != relationship.id:
        raise _invalid(_ACCOUNT_REF, FieldErrorCode.ACCOUNT_REF_CONFLICT)
    return _Resolved(
        relationship=relationship,
        account_id=None if account is None else account.id,
        fail_mode=fail_mode_for(s, item.rail),
    )


def _seed_range(
    declaration: AggregateDeclaration, at: datetime
) -> tuple[datetime, datetime]:
    """Return the bucket starts a read of ``declaration`` at ``at`` may need.

    Returns:
        The window's buckets for a duration, month or quarter; for an
        accumulator, every start whose window could still hold ``at``.

    """
    if declaration.window_kind is not WindowKind.ACCUM:
        return window_bounds(declaration, at=at)
    last = bucket_start_for(BucketKind.ACCUM, at)
    span = timedelta(days=iso_duration_days(declaration.window))
    return last - span - _ACCUM_SLACK, last


def _seeded_store(
    s: Session,
    txn: EnrichedTransaction,
    declarations: tuple[AggregateDeclaration, ...],
) -> MemoryAggregateStore:
    tenant_id = bound_context(s).require_tenant_id()
    incomplete = {item.agg_id() for item in declarations} - backfilled_ids(s)
    store = MemoryAggregateStore(
        # The tenant's blind index, so the candidate's distinct member compares
        # with the stored members' hashes.
        member_hash=lambda member: blind_index(
            "agg_member", member, tenant_id=tenant_id, session=s
        ),
        incomplete_ids=incomplete,
    )
    stored = PostgresAggregateStore(s)
    for declaration in declarations:
        key = txn.key_for(declaration.key_kind)
        if key is None:
            continue
        aggregate_key = AggregateKey(declaration.key_kind, key)
        start, end = _seed_range(declaration, txn.occurred_at)
        store.seed(
            aggregate_key,
            declaration,
            stored.buckets(aggregate_key, declaration, start=start, end=end),
        )
    store.add(txn, declarations)
    return store


def _is_limit(loaded: LoadedRule) -> bool:
    return loaded.definition.kind is RuleKind.TIER_LIMIT


def _reason(
    loaded: LoadedRule, firing: Firing, tier: str | None
) -> dict[str, JsonValue]:
    detail: dict[str, JsonValue] = {
        name: {
            "value": str(snapshot.value),
            "unit": snapshot.unit,
            "window": snapshot.window,
            "count": snapshot.count,
        }
        for name, snapshot in firing.aggregates.items()
    }
    reason: dict[str, JsonValue] = {
        "code": TIER_LIMIT if _is_limit(loaded) else RISK_REVIEW,
        "rule_code": loaded.rule_code,
        "rule_version_id": str(loaded.rule_version_id),
        "severity": firing.severity.value,
        "outcome": firing.outcome.value,
        "detail": detail,
    }
    if _is_limit(loaded):
        reason["tier"] = tier
        reason["limit_code"] = LIMIT_CODE_MONTHLY_OPS
    return reason


def _decision(fired: list[tuple[LoadedRule, Firing]]) -> DecisionOutcome:
    blocks = any(
        (_is_limit(loaded) and firing.outcome is HitOutcome.BLOCK)
        or firing.severity is Severity.CRITICAL
        for loaded, firing in fired
    )
    if blocks:
        return DecisionOutcome.BLOCK
    return DecisionOutcome.REVIEW if fired else DecisionOutcome.ALLOW


def _evaluate(
    s: Session,
    item: TransactionInput,
    resolved: _Resolved,
    *,
    now: datetime,
    timer: _Timer,
) -> _Evaluated:
    relationship = resolved.relationship
    on = policy_date(now)
    with timer.reading():
        snapshot = snapshot_for(s, on=on, product=relationship.product, cache={})
        ruleset = active_rules(s, on=on, product=relationship.product)
    sync = replace(ruleset, rules=ruleset.sync_rules)
    timer.check()
    with timer.reading():
        txn = enrich_input(
            s,
            item,
            relationship=relationship,
            account_id=resolved.account_id,
            units=UnitService(s),
            holidays=load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
            snapshot=snapshot,
            now=now,
        )
    timer.check()
    with timer.reading():
        store = _seeded_store(s, txn, sync.declarations)
    timer.check()
    with timer.reading():
        profile = profile_for(s, txn)
    fired = evaluate_rules(
        sync, txn=txn, store=store, profile=profile, policy=snapshot.policy, now=now
    )
    timer.check()
    return _Evaluated(
        decision=_decision(fired),
        reasons=tuple(_reason(loaded, firing, txn.tier) for loaded, firing in fired),
        ruleset_hash=ruleset.ruleset_hash(),
        snapshot_id=snapshot.snapshot_id,
        txn=txn,
        limits=tuple(firing.outcome for loaded, firing in fired if _is_limit(loaded)),
    )


@contextmanager
def _timeout(s: Session, name: str, ms: int) -> Generator[None]:
    # Postgres reads a timeout of 0 as "wait forever".
    previous = s.execute(
        _SET_TIMEOUT, {"name": name, "value": f"{max(ms, 1)}ms"}
    ).scalar()
    try:
        yield
    finally:
        _ = s.execute(_RESTORE_TIMEOUT, {"name": name, "value": previous})


def record_decision(s: Session, row: TxnDecision) -> None:
    """Insert the decision row and publish ``monitoring.decision_issued.v1``."""
    s.add(row)
    s.flush()
    _ = publish_monitoring_event(
        s,
        DECISION_ISSUED,
        entity="txn_decision",
        entity_id=row.id,
        key=str(row.relationship_id),
        data={
            "decision_id": str(row.id),
            "external_id": row.external_id,
            "decision": row.decision.value,
            "mode": row.mode.value,
        },
        policy_snapshot_id=row.policy_snapshot_id,
    )


def _notify_limits(s: Session, evaluated: _Evaluated) -> None:
    txn = evaluated.txn
    if txn.tier is None:
        return
    for outcome in evaluated.limits:
        try:
            with s.begin_nested():
                _ = notice_tier_limit(
                    s,
                    relationship_id=txn.relationship_id,
                    account_id=txn.account_id,
                    tier=txn.tier,
                    limit_code=LIMIT_CODE_MONTHLY_OPS,
                    period_key=txn.period_keys.MONTH,
                    outcome=outcome,
                    source=NoticeSource.DECIDE,
                )
        except SQLAlchemyError:
            # Another transaction holds the month's notice (lock_timeout) or the
            # database failed: whoever holds it publishes the one webhook.
            logger.warning(
                NOTICE_DEFERRED, extra={"relationship_id": str(txn.relationship_id)}
            )


def _attempt(
    s: Session, item: TransactionInput, *, now: datetime, timer: _Timer
) -> tuple[_Resolved | None, _Evaluated | None]:
    # Steps 1 to 5 in a savepoint: a failure rolls back to it and leaves the
    # transaction usable for the fail mode's row. A 422 propagates.
    resolved: _Resolved | None = None
    try:
        with s.begin_nested():
            resolved = _resolve(s, item)
            timer.check()
            return resolved, _evaluate(s, item, resolved, now=now, timer=timer)
    except (_OverBudgetError, *_FAILURES):
        return resolved, None


def _is_external_id_tie(exc: IntegrityError) -> bool:
    # Postgres names the partition's copy of ux_txn_decision_external
    # (txn_decision_<month>_tenant_id_external_id_decided_at_idx), or the index.
    if not isinstance(exc.orig, UniqueViolation):
        return False
    name = exc.orig.diag.constraint_name or ""
    return name == EXTERNAL_INDEX or name.endswith(_EXTERNAL_PARTITION_SUFFIX)


def _stored(
    s: Session, build: Callable[[datetime], TxnDecision], now: datetime
) -> TxnDecision | None:
    # A decision of the same external_id stored at the same instant (no
    # idempotency key, a frozen clock) is another decision: it is retried once, a
    # microsecond on.
    for at in (now, now + _TIE_BREAK):
        row = build(at)
        try:
            with s.begin_nested():
                record_decision(s, row)
        except IntegrityError as exc:
            if _is_external_id_tie(exc):
                continue
            return None
        except SQLAlchemyError:
            return None
        return row
    return None


def decide(s: Session, item: TransactionInput, *, actor: str) -> DecisionResult:
    """Decide on one transaction about to be executed (spec §12.1).

    An unknown or inactive customer, or an account bound to another customer,
    raises ``ValidationFailedError`` (422) before anything is written.

    Args:
        s: The request's tenant session; the row and events commit with it.
        item: The validated transaction.
        actor: Who asked, ``api_client:<id>``; stored on the row.

    Returns:
        The decision; ``reasons`` as stored, which only staff may read beyond
        each ``code``.

    """
    timer = _Timer(budget_ms=get_settings().monitoring_decide_budget_ms)
    decision_id = uuid7()
    now = clock.now()
    with _timeout(s, "lock_timeout", timer.budget_ms):
        # Cancels a step's statement that stalls past the budget without waiting
        # on a lock (an OperationalError, so the fail mode answers). Set outside
        # the steps' savepoint, so its rollback keeps the setting.
        remaining = max(
            timer.budget_ms - timer.elapsed_ms(), STATEMENT_TIMEOUT_FLOOR_MS
        )
        with _timeout(s, "statement_timeout", remaining):
            resolved, evaluated = _attempt(s, item, now=now, timer=timer)
        if evaluated is not None:
            decision, mode = evaluated.decision, DecisionMode.EVALUATED
            reasons = evaluated.reasons
        else:
            fail_mode = default_fail_mode() if resolved is None else resolved.fail_mode
            decision, mode = _ANSWER[fail_mode]
            reasons = _UNAVAILABLE

        row: TxnDecision | None = None
        if resolved is not None:
            customer = resolved

            def build(at: datetime) -> TxnDecision:
                return TxnDecision(
                    tenant_id=bound_context(s).require_tenant_id(),
                    decided_at=at,
                    id=decision_id,
                    external_id=item.external_id,
                    rail=item.rail,
                    relationship_id=customer.relationship.id,
                    account_id=customer.account_id,
                    decision=decision,
                    mode=mode,
                    reasons=list(reasons),
                    # Nothing was evaluated under the fail mode, so no rule set.
                    ruleset_hash=b"" if evaluated is None else evaluated.ruleset_hash,
                    policy_snapshot_id=(
                        None if evaluated is None else evaluated.snapshot_id
                    ),
                    latency_ms=timer.elapsed_ms(),
                    db_ms=int(timer.db_seconds * _MS),
                    actor=actor,
                )

            row = _stored(s, build, now)
        if row is None:
            logger.warning(
                DECISION_UNRECORDED,
                extra={
                    "decision": decision.value,
                    "mode": mode.value,
                    "external_id": item.external_id,
                },
            )
        elif evaluated is not None:
            _notify_limits(s, evaluated)
        latency_ms = timer.elapsed_ms()
        if row is not None:
            # The whole request's time, the insert and the notice included; the
            # update goes out with the request's commit.
            row.latency_ms = latency_ms
    return DecisionResult(
        decision=decision,
        decision_id=decision_id,
        mode=mode,
        reasons=reasons,
        latency_ms=latency_ms,
    )
