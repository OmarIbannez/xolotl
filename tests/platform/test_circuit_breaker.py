"""The circuit breaker's state machine, on an injected monotonic clock (spec §8)."""

import pytest

from xolotl.platform.connectors import CircuitBreaker, CircuitState


class FakeMonotonic:
    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake() -> FakeMonotonic:
    return FakeMonotonic()


@pytest.fixture
def breaker(fake: FakeMonotonic) -> CircuitBreaker:
    return CircuitBreaker(clock_fn=fake)


def _open(breaker: CircuitBreaker) -> None:
    for _ in range(5):
        breaker.record_failure()


def _half_open(breaker: CircuitBreaker, fake: FakeMonotonic) -> None:
    _open(breaker)
    fake.advance(30)
    assert breaker.allow() is True


def test_a_new_breaker_is_closed_and_allows(breaker: CircuitBreaker):
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_five_failures_within_sixty_seconds_open_it(breaker: CircuitBreaker):
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False


def test_failures_older_than_the_window_are_forgotten(
    breaker: CircuitBreaker, fake: FakeMonotonic
):
    for _ in range(4):
        breaker.record_failure()
    fake.advance(61)
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_it_half_opens_after_thirty_seconds_and_admits_one_trial(
    breaker: CircuitBreaker, fake: FakeMonotonic
):
    _open(breaker)
    fake.advance(29.9)
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False
    fake.advance(0.1)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is True
    assert breaker.allow() is False


def test_a_successful_trial_closes_it(breaker: CircuitBreaker, fake: FakeMonotonic):
    _half_open(breaker, fake)
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_a_failed_trial_reopens_it_for_another_thirty_seconds(
    breaker: CircuitBreaker, fake: FakeMonotonic
):
    _half_open(breaker, fake)
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False
    fake.advance(29)
    assert breaker.state is CircuitState.OPEN
    fake.advance(1)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is True


def test_a_success_while_closed_clears_the_failure_count(breaker: CircuitBreaker):
    for _ in range(4):
        breaker.record_failure()
    breaker.record_success()
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_a_failure_while_open_does_not_extend_the_open_period(
    breaker: CircuitBreaker, fake: FakeMonotonic
):
    _open(breaker)
    fake.advance(20)
    breaker.record_failure()
    fake.advance(10)
    assert breaker.state is CircuitState.HALF_OPEN


def test_a_released_trial_lets_the_next_call_try(
    breaker: CircuitBreaker, fake: FakeMonotonic
):
    _half_open(breaker, fake)
    assert breaker.allow() is False
    breaker.release()
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is True


def test_release_leaves_a_closed_or_open_breaker_alone(
    breaker: CircuitBreaker, fake: FakeMonotonic
):
    breaker.release()
    assert breaker.state is CircuitState.CLOSED
    _half_open(breaker, fake)
    breaker.record_failure()
    breaker.release()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False
