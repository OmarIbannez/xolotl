"""Rule hits: dedup, cooldown, evidence and exactly-once (spec §9.6, B7).

The structuring customer is tier N3 (no monthly limit), so only
``CASH_STRUCTURING`` fires. The near band is [3,500, 5,000) USD over seven days and
three near deposits summing at least 5,000 USD fire it; the dedup bucket is the
UTC week starting on Monday (7 to 13 December 2026) and the cooldown seven days.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from tests.helpers import everything_stored, staff_session, system_ctx
from tests.modules.monitoring.rules.conftest import enriched
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    TIER_N1,
    TRUE_MATCH,
    Stage,
    add_relationship,
    count_rows,
    deposit,
    events_of,
    feed,
    hits,
    ingest,
    links,
    local,
    run_backfills,
    statuses,
)
from tests.modules.monitoring.rules.lifecycle_support import (
    APPROVER_ROLES,
    AUTHOR_ROLES,
    draft,
    freeze,
    simulate,
)
from tests.modules.monitoring.support import run_processing
from xolotl.modules.monitoring.aggregates.models import AggDeclaration
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.events import RULE_HIT_CREATED
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND
from xolotl.modules.monitoring.process.hooks import ProcessingContext
from xolotl.modules.monitoring.process.job import process_party
from xolotl.modules.monitoring.rules import hook as hook_module
from xolotl.modules.monitoring.rules.assignments import (
    LoadedRule,
    RuleSet,
    active_rules,
)
from xolotl.modules.monitoring.rules.definitions import Dedup, HitOutcome, Severity
from xolotl.modules.monitoring.rules.evaluate import evaluate_rules
from xolotl.modules.monitoring.rules.hits import dedup_bucket, record_firing
from xolotl.modules.monitoring.rules.kinds import Firing
from xolotl.modules.monitoring.rules.lifecycle import (
    activate_version,
    approve_version,
    submit_version,
)
from xolotl.modules.monitoring.rules.models import RuleHit, RuleHitTransaction
from xolotl.modules.monitoring.rules.versions import VersionInput
from xolotl.modules.monitoring.txn import ingest as ingest_module
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session

REF = "cust-n3"


@pytest.fixture
def n3(stage: Stage) -> UUID:
    return add_relationship(stage.tenant, REF, "N3")


def _near(number: int, day: int, amount: str, hour: int = 10) -> dict[str, object]:
    return deposit(f"dep-{number}", local(day, hour), amount, customer_ref=REF)


THREE = [_near(1, 7, "3600.00"), _near(2, 8, "3700.00"), _near(3, 9, "3800.00")]


@pytest.mark.usefixtures("n3")
def test_three_near_threshold_deposits_make_one_hit_with_evidence(stage: Stage):
    first, second, third = feed(stage, THREE)

    [hit] = hits(stage, CASH)
    assert links(stage, hit.id) == {
        third: "TRIGGER",
        second: "WINDOW",
        first: "WINDOW",
    }
    assert hit.first_transaction_id == third
    assert hit.fired_count == 1
    assert hit.rule_version_id == stage.versions[CASH]
    assert (
        hit.dedup_key == f"RELATIONSHIP:{hit.relationship_id}:2026-12-07T00:00:00+00:00"
    )
    assert hit.cooldown_until == local(9) + timedelta(days=7)
    assert (hit.severity, hit.outcome) == ("MEDIUM", "ALERT")

    aggregates = hit.evidence["aggregates"]
    assert isinstance(aggregates, dict)
    assert aggregates["near_count"] == {"value": "3", "unit": None, "window": "P7D"}
    near_sum = aggregates["near_sum"]
    assert isinstance(near_sum, dict)
    assert near_sum["unit"] == "USD/PRIOR_BANKING_DAY"
    assert abs(float(str(near_sum["value"])) - 11100) < 1
    assert hit.evidence["v"] == 1
    assert hit.evidence["params"] == {
        "threshold": "5000",
        "near_ratio": "0.70",
        "window": "P7D",
        "min_count": "3",
    }
    explanation = str(hit.evidence["explanation"])
    assert "efectivo" in explanation
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        ruleset = active_rules(s, on=local(9).date(), product="EMONEY")
        loaded = next(rule for rule in ruleset.rules if rule.rule_code == CASH)
    assert explanation == loaded.definition.render("es-MX")
    assert hit.evidence["lists_hash"] == ruleset.lists.content_hash.hex()
    assert hit.definition_hash == loaded.definition_hash
    assert hit.assignment_id == loaded.assignment_id

    [event] = events_of(stage.tenant_id, RULE_HIT_CREATED)
    assert event.key == str(hit.party_id)
    assert event.data == {
        "hit_id": str(hit.id),
        "rule_version_id": str(hit.rule_version_id),
        "rule_code": CASH,
        "party_id": str(hit.party_id),
        "relationship_id": str(hit.relationship_id),
        "severity": "MEDIUM",
        "outcome": "ALERT",
        "window_start": hit.window_start.isoformat(),
        "window_end": hit.window_end.isoformat(),
    }


@pytest.mark.usefixtures("n3")
def test_a_fourth_deposit_in_the_window_links_as_window(stage: Stage):
    _ = feed(stage, THREE)
    [fourth] = feed(stage, [_near(4, 11, "3650.00")])

    [hit] = hits(stage, CASH)
    assert hit.fired_count == 2
    assert links(stage, hit.id)[fourth] == "WINDOW"
    assert len(events_of(stage.tenant_id, RULE_HIT_CREATED)) == 1


@pytest.mark.usefixtures("n3")
def test_a_firing_inside_the_cooldown_links_as_cooldown(stage: Stage):
    _ = feed(stage, [*THREE, _near(4, 11, "3650.00")])
    # Tuesday 15 December: the next week's bucket, still inside the cooldown.
    [later] = feed(stage, [_near(5, 15, "3600.00")])

    [hit] = hits(stage, CASH)
    assert links(stage, hit.id)[later] == "COOLDOWN"
    assert hit.fired_count == 2
    assert len(events_of(stage.tenant_id, RULE_HIT_CREATED)) == 1


@pytest.mark.usefixtures("n3")
def test_replaying_the_same_stream_creates_each_hit_once(stage: Stage):
    _ = feed(stage, THREE)
    before = (count_rows(stage, RuleHit), count_rows(stage, RuleHitTransaction))

    rows = set(statuses(stage))
    _ = ingest(stage, THREE)
    assert set(statuses(stage)) == rows
    _ = run_processing(stage.services, stage.tenant_id)

    assert (count_rows(stage, RuleHit), count_rows(stage, RuleHitTransaction)) == (
        before
    )
    assert len(events_of(stage.tenant_id, RULE_HIT_CREATED)) == 1


def _firing(row: Transaction) -> Firing:
    return Firing(
        rule_code=CASH,
        key_kind=KeyKind.RELATIONSHIP,
        key=str(row.relationship_id),
        window_start=row.occurred_at,
        window_end=row.occurred_at,
        severity=Severity.MEDIUM,
        outcome=HitOutcome.ALERT,
        aggregates={},
        params={},
        explanation="prueba",
    )


@pytest.mark.usefixtures("n3")
def test_two_sessions_firing_at_once_create_one_hit(stage: Stage):
    [row_id] = feed(stage, THREE[:1])
    barrier = threading.Barrier(2)

    def fire() -> RuleHit | None:
        with tenant_session(system_ctx(stage.tenant_id)) as s:
            row = s.scalars(select(Transaction).where(Transaction.id == row_id)).one()
            txn = enriched_from_row(row)
            ruleset = active_rules(s, on=local(7).date(), product="EMONEY")
            loaded = next(r for r in ruleset.rules if r.rule_code == CASH)
            _ = barrier.wait(timeout=10)
            return record_firing(
                s, loaded, _firing(row), txn=txn, lists_hash=ruleset.lists.content_hash
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fire) for _ in range(2)]
        # result() raises a thread's failure here.
        results = [future.result(timeout=30) for future in futures]

    assert sum(result is not None for result in results) == 1
    [hit] = hits(stage, CASH)
    # One transaction, linked once and counted once (B7 review I1).
    assert hit.fired_count == 1
    assert links(stage, hit.id) == {row_id: "TRIGGER"}


@pytest.mark.usefixtures("n3")
def test_evidence_carries_ids_amounts_and_codes_only(
    stage: Stage, admin_engine: Engine
):
    _ = feed(stage, THREE)
    [hit] = hits(stage, CASH)

    stored = repr(hit.evidence)
    for forbidden in ("Lucía", "Ramos", REF, "dep-1"):
        assert forbidden not in stored
    dump = everything_stored(admin_engine)
    assert "Lucía" not in dump
    assert "Ramos" not in dump


def _unbackfill(stage: Stage) -> list[str]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        ids = list(
            s.scalars(
                update(AggDeclaration)
                .where(AggDeclaration.window == "P7D")
                .values(backfilled_through=None)
                .returning(AggDeclaration.agg_id)
            )
        )
        for agg_id in ids:
            _ = stage.services.queue.enqueue(
                s,
                kind=AGG_BACKFILL_KIND,
                payload={"agg_id": agg_id},
                key=f"agg_backfill:{agg_id}",
            )
        return ids


@pytest.mark.usefixtures("n3")
def test_an_incomplete_window_fires_nothing_until_the_backfill(stage: Stage):
    assert _unbackfill(stage)
    # The worker's jobs would run the backfill first: process without it.
    _ = ingest(stage, THREE)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        party_id = s.scalars(select(Transaction.party_id)).first()
    assert party_id is not None
    _drain(stage, party_id)
    assert hits(stage, CASH) == []

    assert run_backfills(stage.services) > 0
    [fourth] = feed(stage, [_near(4, 11, "3650.00")])
    [hit] = hits(stage, CASH)
    assert links(stage, hit.id)[fourth] == "TRIGGER"
    aggregates = hit.evidence["aggregates"]
    assert isinstance(aggregates, dict)
    assert aggregates["near_count"] == {"value": "4", "unit": None, "window": "P7D"}


def _drain(stage: Stage, party_id: UUID) -> None:
    _ = process_party(
        get_sessionmaker(),
        stage.tenant_id,
        party_id,
        queue=stage.services.queue,
        job=None,
        settings=get_settings(),
        actor="cli:test",
    )


@pytest.mark.usefixtures("n3")
def test_a_late_row_refires_nothing_already_recorded(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
):
    _ = feed(stage, [*THREE, _near(4, 11, "3650.00")])
    [hit] = hits(stage, CASH)
    assert hit.fired_count == 2
    returned: list[int] = []
    original = hook_module.reevaluate_later

    def spy(s: Session, late: EnrichedTransaction, ctx: ProcessingContext) -> int:
        count = original(s, late, ctx)
        returned.append(count)
        return count

    monkeypatch.setattr(hook_module, "reevaluate_later", spy)
    # A 10 December deposit that arrives on 12 December.
    [late] = feed(stage, [_near(9, 10, "3550.00")], received=local(12))

    assert returned
    assert returned[0] > 0
    assert [h.id for h in hits(stage, CASH)] == [hit.id]
    assert links(stage, hit.id)[late] == "WINDOW"
    assert len(events_of(stage.tenant_id, RULE_HIT_CREATED)) == 1
    # The late row is one more transaction; re-evaluating the 11 December row,
    # already linked, counts nothing (B7 review I1).
    [after] = hits(stage, CASH)
    assert after.fired_count == 3


@pytest.mark.usefixtures("n3")
def test_a_hit_found_by_re_evaluation_links_the_late_row(stage: Stage):
    # 7 and 9 December are two near deposits: no hit. The 8 December one arrives
    # on 12 December; on its own date it sees two, but the 9 December row now
    # sees three and fires, and the late row is part of its evidence (review I2).
    seventh, ninth = feed(stage, [THREE[0], THREE[2]])
    assert hits(stage, CASH) == []

    [eighth] = feed(stage, [THREE[1]], received=local(12))

    [hit] = hits(stage, CASH)
    assert hit.first_transaction_id == ninth
    assert links(stage, hit.id) == {
        ninth: "TRIGGER",
        eighth: "WINDOW",
        seventh: "WINDOW",
    }
    assert hit.fired_count == 1


def test_a_zero_dedup_window_buckets_by_instant():
    # The validator refuses a zero window; a stored one still never divides by it.
    txn = enriched()
    zero = Dedup.model_construct(window="P0D", cooldown="P0D", key=KeyKind.RELATIONSHIP)
    assert dedup_bucket(txn, zero) == txn.occurred_at.astimezone(UTC).isoformat()


@pytest.mark.usefixtures("n3")
def test_re_evaluation_runs_only_the_rules_whose_reach_holds_the_late_row(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
):
    small = "100.00"
    same, fifth, twentieth = feed(
        stage,
        [
            deposit("same", local(2), small, customer_ref=REF, currency="MXN"),
            deposit("fifth", local(5), small, customer_ref=REF, currency="MXN"),
            deposit("twentieth", local(20), small, customer_ref=REF, currency="MXN"),
        ],
    )
    seen: list[tuple[UUID, frozenset[str]]] = []

    def spy(ruleset: RuleSet, **kwargs: Any) -> list[tuple[LoadedRule, Firing]]:
        txn = kwargs["txn"]
        assert isinstance(txn, EnrichedTransaction)
        seen.append((txn.id, frozenset(r.rule_code for r in ruleset.rules)))
        return evaluate_rules(ruleset, **kwargs)

    monkeypatch.setattr(hook_module, "evaluate_rules", spy)
    # Nineteen days late, yet processed rather than held for a backfill.
    patient = get_settings().model_copy(update={"monitoring_late_days": 30})
    monkeypatch.setattr(ingest_module, "get_settings", lambda: patient)
    # Late: occurred at the same instant as "same", received on 21 December.
    [late] = feed(
        stage,
        [deposit("late", local(2), small, customer_ref=REF, currency="MXN")],
        received=local(21),
    )

    every = {CASH, TIER_N1, TRUE_MATCH}
    assert seen[0] == (late, frozenset(every))
    # The row at the late row's instant and the 5 December one are inside both
    # reaches (CASH's seven days, TIER's month); the 20 December one only inside
    # TIER's month. TRUE_MATCH reads no aggregate, so a late row never changes it.
    assert seen[1:] == [
        (same, frozenset({CASH, TIER_N1})),
        (fifth, frozenset({CASH, TIER_N1})),
        (twentieth, frozenset({TIER_N1})),
    ]


def _spy_rules(monkeypatch: pytest.MonkeyPatch) -> list[tuple[UUID, frozenset[str]]]:
    """Record which rules the hook evaluates for which transaction.

    Returns:
        The ``(transaction id, rule codes)`` pairs, in call order.

    """
    seen: list[tuple[UUID, frozenset[str]]] = []

    def spy(ruleset: RuleSet, **kwargs: Any) -> list[tuple[LoadedRule, Firing]]:
        txn = kwargs["txn"]
        assert isinstance(txn, EnrichedTransaction)
        seen.append((txn.id, frozenset(r.rule_code for r in ruleset.rules)))
        return evaluate_rules(ruleset, **kwargs)

    monkeypatch.setattr(hook_module, "evaluate_rules", spy)
    return seen


def _activate_from(
    stage: Stage,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    effective_from: date,
    body: VersionInput | None = None,
) -> None:
    # Activated on 15 November, so the version's simulation (finished on 30
    # October) is still fresh, effective on a later December day.
    with monkeypatch.context() as patched:
        freeze(patched, datetime(2026, 11, 15, 18, tzinfo=UTC))
        version = draft(stage.tenant, code, body=body)
        _ = simulate(stage.tenant, version)
        with staff_session(
            stage.tenant_id, stage.tenant.admin_user_id, AUTHOR_ROLES
        ) as s:
            _ = submit_version(s, version.id)
        with staff_session(stage.tenant_id, stage.approver, APPROVER_ROLES) as s:
            _ = approve_version(s, version.id)
            _ = activate_version(
                s,
                version.id,
                effective_from=effective_from,
                product=None,
                queue=stage.services.queue,
            )
    _ = run_backfills(stage.services)


@pytest.mark.usefixtures("n3")
def test_re_evaluation_runs_a_rule_that_started_after_the_late_row(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
):
    # NEW_ACCOUNT_VELOCITY (P1D) applies from 11 December only; a 10 December
    # deposit arriving late still re-runs it on the 11 December row (review N2).
    velocity = "NEW_ACCOUNT_VELOCITY"
    _activate_from(stage, monkeypatch, velocity, date(2026, 12, 11))
    [eleventh] = feed(
        stage, [deposit("eleventh", local(11, 9), "100.00", customer_ref=REF)]
    )
    seen = _spy_rules(monkeypatch)

    [late] = feed(
        stage,
        [deposit("late", local(10), "100.00", customer_ref=REF)],
        received=local(12),
    )

    assert seen[0] == (late, frozenset({CASH, TIER_N1, TRUE_MATCH}))
    assert seen[1:] == [(eleventh, frozenset({CASH, TIER_N1, velocity}))]


@pytest.mark.usefixtures("n3")
def test_re_evaluation_uses_the_reach_of_the_version_in_force_on_the_row(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
):
    # From 11 December CASH_STRUCTURING looks back fourteen days. A 1 December
    # deposit arriving late is outside the old version's seven days from the 12
    # December row, but inside the new version's (review N2).
    _activate_from(
        stage,
        monkeypatch,
        CASH,
        date(2026, 12, 11),
        body=VersionInput(params={"window": "P14D"}),
    )
    [twelfth] = feed(stage, [deposit("twelfth", local(12), "100.00", customer_ref=REF)])
    seen = _spy_rules(monkeypatch)
    patient = get_settings().model_copy(update={"monitoring_late_days": 30})
    monkeypatch.setattr(ingest_module, "get_settings", lambda: patient)

    [late] = feed(
        stage,
        [deposit("late", local(1), "100.00", customer_ref=REF)],
        received=local(13),
    )

    assert seen[0] == (late, frozenset({CASH, TIER_N1, TRUE_MATCH}))
    assert seen[1:] == [(twelfth, frozenset({CASH, TIER_N1}))]
