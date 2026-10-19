import itertools
import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

from xolotl.platform.ids import uuid7, uuid7_time


def test_uuid7_has_version_7_and_rfc_variant():
    value = uuid7()
    assert isinstance(value, UUID)
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_is_strictly_monotonic_within_a_process():
    values = [uuid7() for _ in range(10_000)]
    assert all(a.int < b.int for a, b in itertools.pairwise(values))


def test_uuid7_embeds_the_current_time():
    before = datetime.now(UTC) - timedelta(milliseconds=2)
    value = uuid7()
    after = datetime.now(UTC) + timedelta(milliseconds=2)
    assert before <= uuid7_time(value) <= after


def test_uuid7_is_unique_across_threads():
    seen: list[UUID] = []
    lock = threading.Lock()

    def work():
        local = [uuid7() for _ in range(2_000)]
        with lock:
            seen.extend(local)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen)) == len(seen) == 16_000
