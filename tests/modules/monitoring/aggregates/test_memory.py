"""The memory store over hand-computed scenarios (spec §8.1)."""

import pytest

from tests.modules.monitoring.aggregates import scenarios
from xolotl.modules.monitoring.aggregates.declarations import AggregateFunction
from xolotl.modules.monitoring.aggregates.memory import MemoryAggregateStore
from xolotl.modules.monitoring.aggregates.store import EMPTY


@pytest.mark.parametrize("function", list(AggregateFunction))
def test_each_function_over_a_small_stream(function: AggregateFunction):
    got = scenarios.each_function(MemoryAggregateStore(), function)
    assert got == scenarios.EXPECTED[function]


def test_a_transaction_outside_the_filter_does_not_contribute():
    assert scenarios.outside_the_filter(MemoryAggregateStore()) == (3, 3)


def test_accumulators_open_and_close_per_key():
    assert scenarios.accumulators(MemoryAggregateStore()) == scenarios.ACCUM_EXPECTED


def test_incomplete_ids_mark_reads_incomplete():
    declared = scenarios.declaration(AggregateFunction.SUM)
    store = MemoryAggregateStore(incomplete_ids={declared.agg_id()})
    for row in scenarios.SMALL:
        store.add(row, [declared])
    assert store.read(scenarios.KEY, declared, at=scenarios.READ_AT).complete is False
    assert (
        MemoryAggregateStore().read(scenarios.KEY, declared, at=scenarios.READ_AT)
        == EMPTY
    )
