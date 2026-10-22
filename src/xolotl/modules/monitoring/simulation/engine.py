"""Replaying stored history through the pure evaluator in memory (spec §13.1).

``replay_party`` feeds one party's stream, in ``occurred_at`` order, into one
``MemoryAggregateStore`` and evaluates the rules after each transaction exactly as
processing does (``evaluate_rules``), then applies processing's dedup and
cooldown in memory: the same ``dedup_key_for`` and the same ``cooldown_until``, so
a simulated hit is the hit processing would have stored (Decision 7). Nothing is
written.

``replay_rule_set`` walks the tenant's ``PROCESSED`` rows of a date range by
``(party_id, occurred_at, id)``, a page at a time. A page reads at least ``page``
rows and then finishes its last party, so no party's stream is split across pages
and memory holds one party's windows at a time (plan 4b Deviation 12). The
customer's profile is read once per relationship; its history months count from
the relationship's first transaction in the range (Deviation 13). The simulation
job and plan 4c's dry-run replay share the walk.
"""

from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby
from uuid import UUID

from sqlalchemy import event, select, tuple_
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.rules.assignments import LoadedRule, RuleSet
from xolotl.modules.monitoring.rules.definitions import HitOutcome, Severity
from xolotl.modules.monitoring.rules.evaluate import (
    evaluate_rules,
    months_between,
    profile_with_history,
)
from xolotl.modules.monitoring.rules.hits import cooldown_until, dedup_key_for
from xolotl.modules.monitoring.rules.kinds import AggregateSnapshot, EvalProfile
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.platform import clock
from xolotl.platform.policy import EffectivePolicy
from xolotl.platform.policy.jurisdictions import JurisdictionLists
from xolotl.platform.policy.snapshots import get_snapshot


@dataclass(frozen=True, slots=True)
class SimulatedHit:
    """A hit processing would have stored, held in memory only."""

    rule_code: str
    rule_version_id: UUID
    party_id: UUID
    relationship_id: UUID
    key_kind: KeyKind
    key: str
    dedup_key: str
    window_start: datetime
    window_end: datetime
    severity: Severity
    outcome: HitOutcome
    aggregates: Mapping[str, AggregateSnapshot]
    trigger_transaction_id: UUID


@dataclass(frozen=True, slots=True)
class ReplayPage:
    """One page of a replay: whole parties, their rows counted, their hits."""

    last_party_id: UUID
    transactions_read: int
    hits: tuple[SimulatedHit, ...]


def _declarations(rules: Sequence[LoadedRule]) -> tuple[AggregateDeclaration, ...]:
    seen: dict[str, AggregateDeclaration] = {}
    for loaded in rules:
        for declaration in loaded.definition.aggregates:
            _ = seen.setdefault(declaration.agg_id(), declaration)
    return tuple(seen.values())


def replay_party(
    rules: Sequence[LoadedRule],
    stream: Iterable[EnrichedTransaction],
    *,
    policy_for: Callable[[UUID], EffectivePolicy],
    profile_for: Callable[[EnrichedTransaction], EvalProfile],
    lists: JurisdictionLists,
    now: datetime,
) -> list[SimulatedHit]:
    """Replay one party's transactions through ``rules`` in memory.

    ``policy_for`` maps a transaction's policy snapshot id to its policy and
    ``profile_for`` gives the customer's profile at a transaction.

    Returns:
        The hits, in the order they would have been stored.

    """
    ordered = sorted(stream, key=lambda txn: (txn.occurred_at, txn.id))
    if not ordered:
        return []
    ruleset = RuleSet(
        on=policy_date(ordered[-1].occurred_at),
        product=None,
        rules=tuple(rules),
        lists=lists,
    )
    declarations = _declarations(rules)
    store = MemoryAggregateStore()
    found: list[SimulatedHit] = []
    keys: set[tuple[UUID, str]] = set()
    # The latest hit's cooldown per (version, key kind, key).
    cooling: dict[tuple[UUID, KeyKind, str], datetime | None] = {}
    for txn in ordered:
        store.add(txn, declarations)
        fired = evaluate_rules(
            ruleset,
            txn=txn,
            store=store,
            profile=profile_for(txn),
            policy=policy_for(txn.policy_snapshot_id),
            now=now,
        )
        for loaded, firing in fired:
            dedup = loaded.definition.dedup
            dedup_key = dedup_key_for(firing, txn, dedup)
            if (loaded.rule_version_id, dedup_key) in keys:
                continue
            key = (loaded.rule_version_id, firing.key_kind, firing.key)
            until = cooling.get(key)
            if until is not None and until > txn.occurred_at:
                continue
            keys.add((loaded.rule_version_id, dedup_key))
            cooling[key] = cooldown_until(txn, dedup)
            found.append(
                SimulatedHit(
                    rule_code=loaded.rule_code,
                    rule_version_id=loaded.rule_version_id,
                    party_id=txn.party_id,
                    relationship_id=txn.relationship_id,
                    key_kind=firing.key_kind,
                    key=firing.key,
                    dedup_key=dedup_key,
                    window_start=firing.window_start,
                    window_end=firing.window_end,
                    severity=firing.severity,
                    outcome=firing.outcome,
                    aggregates=firing.aggregates,
                    trigger_transaction_id=txn.id,
                )
            )
    return found


def range_bounds(range_start: date, range_end: date) -> tuple[datetime, datetime]:
    """Turn a range of Mexico City dates into UTC instants.

    Returns:
        The start of ``range_start`` and the start of the day after
        ``range_end``, both in UTC.

    """
    start = datetime.combine(range_start, time(), MEXICO_CITY_ZONE)
    end = datetime.combine(range_end + timedelta(days=1), time(), MEXICO_CITY_ZONE)
    return start.astimezone(UTC), end.astimezone(UTC)


def _page_rows(
    s: Session,
    *,
    start: datetime,
    end: datetime,
    after: UUID | None,
    page: int,
    product: str | None,
) -> list[Transaction]:
    in_range = select(Transaction).where(
        Transaction.status == TransactionStatus.PROCESSED,
        Transaction.occurred_at >= start,
        Transaction.occurred_at < end,
    )
    if product is not None:
        in_range = in_range.where(Transaction.product == product)
    query = in_range if after is None else in_range.where(Transaction.party_id > after)
    rows = list(
        s.scalars(
            query.order_by(
                Transaction.party_id, Transaction.occurred_at, Transaction.id
            ).limit(page)
        )
    )
    if len(rows) < page:
        return rows
    # Finish the last party, so its stream stays in one memory store.
    last = rows[-1]
    rest = s.scalars(
        in_range.where(
            Transaction.party_id == last.party_id,
            tuple_(Transaction.occurred_at, Transaction.id)
            > tuple_(last.occurred_at, last.id),
        ).order_by(Transaction.occurred_at, Transaction.id)
    )
    return [*rows, *rest]


@dataclass(slots=True)
class _Profiles:
    """The customer's profile, read once per relationship, aged by the stream."""

    s: Session
    first_seen: dict[UUID, datetime]
    base: dict[UUID, EvalProfile]

    def at(self, txn: EnrichedTransaction) -> EvalProfile:
        months = months_between(self.first_seen[txn.relationship_id], txn.occurred_at)
        base = self.base.get(txn.relationship_id)
        if base is None:
            base = profile_with_history(self.s, txn, history_months=months)
            self.base[txn.relationship_id] = base
        return replace(
            base,
            risk_rating=txn.risk_rating,
            monitoring_profile=txn.monitoring_profile,
            history_months=months,
        )


@contextmanager
def _holding_loads(s: Session) -> Generator[None]:
    """Hold every row the session loads, so its identity map serves repeats.

    The identity map holds rows weakly: without this, the counterparty and the
    policy snapshot ``enriched_from_row`` reads by id would be read again for
    each transaction of a page.
    """
    held: list[object] = []

    def keep(_session: Session, instance: object) -> None:
        held.append(instance)

    event.listen(s, "loaded_as_persistent", keep)
    try:
        yield
    finally:
        event.remove(s, "loaded_as_persistent", keep)
        held.clear()


def replay_pages(
    s: Session,
    ruleset: RuleSet,
    *,
    range_start: date,
    range_end: date,
    page: int,
    after: UUID | None = None,
    product: str | None = None,
) -> Iterator[ReplayPage]:
    """Replay the range's processed rows party by party, a page at a time.

    ``after`` resumes after that party id; ``product`` keeps only that product's
    rows (plan 4c's dry run replays a product's own rules over them). Each page is
    read whole before it is replayed, so the caller may commit between pages.

    Yields:
        One page per batch of whole parties, in party id order.

    """
    start, end = range_bounds(range_start, range_end)
    policies: dict[UUID, EffectivePolicy] = {}

    def policy_for(snapshot_id: UUID) -> EffectivePolicy:
        policy = policies.get(snapshot_id)
        if policy is None:
            policy = get_snapshot(s, snapshot_id).policy
            policies[snapshot_id] = policy
        return policy

    now = clock.now()
    while rows := _page_rows(
        s, start=start, end=end, after=after, page=page, product=product
    ):
        found: list[SimulatedHit] = []
        with _holding_loads(s):
            for _, party_rows in groupby(rows, key=lambda row: row.party_id):
                found += _replay_rows(
                    s, ruleset, list(party_rows), policy_for=policy_for, now=now
                )
        after = rows[-1].party_id
        yield ReplayPage(after, len(rows), tuple(found))


def _replay_rows(
    s: Session,
    ruleset: RuleSet,
    party_rows: Sequence[Transaction],
    *,
    policy_for: Callable[[UUID], EffectivePolicy],
    now: datetime,
) -> list[SimulatedHit]:
    stream = [enriched_from_row(row) for row in party_rows]
    first_seen: dict[UUID, datetime] = {}
    for txn in stream:
        _ = first_seen.setdefault(txn.relationship_id, txn.occurred_at)
    profiles = _Profiles(s, first_seen, {})
    return replay_party(
        ruleset.rules,
        stream,
        policy_for=policy_for,
        profile_for=profiles.at,
        lists=ruleset.lists,
        now=now,
    )


def replay_rule_set(
    s: Session,
    ruleset: RuleSet,
    *,
    range_start: date,
    range_end: date,
    page: int,
    product: str | None = None,
) -> Iterator[SimulatedHit]:
    """Replay the range's processed rows through ``ruleset`` without side effects.

    ``product`` keeps only that product's rows; None replays every row.

    Yields:
        Every simulated hit, party by party.

    """
    for replayed in replay_pages(
        s,
        ruleset,
        range_start=range_start,
        range_end=range_end,
        page=page,
        product=product,
    ):
        yield from replayed.hits
