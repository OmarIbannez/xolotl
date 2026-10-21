"""The memory of the shared screening index at the size of the real lists.

The Render service (one container, 512 MB, API and embedded worker) holds the
shared shard of every screenable entry: about 40,000 across UN, OFAC, EU, UK and
SAT 69-B. This ingests 40,000 entries shaped like the real ones, in the lists'
real proportions: each is a real entry of the library's fixtures for that list
(its aliases, identifiers, addresses, dates and programs), with every name drawn
afresh from the frequency tables and every identifier made unique, so no two
entries share what the real lists would not.

It then measures the shard in fresh interpreters (``spawn``), so the ingest's own
memory is no part of the numbers: one traces each step of the shard read, one
traces the holder's warm-up and refresh, and one runs them untraced for the
process's peak RSS and the RSS it keeps after each step.

Marked ``perf`` like the SP-03 measurements; the numbers go to ``.perf/sp03.json``.
"""

import dataclasses
import multiprocessing
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.perf import index_memory_probe
from tests.perf.conftest import SEED, Measure, draw_name, record, split_pools
from xolotl.modules.screening.lists import EntryName, ListEntry, parse
from xolotl.modules.screening.matching.evaluation import PERTURBATIONS, Draw, perturb
from xolotl.platform.storage.local import LocalFsObjectStore

pytestmark = pytest.mark.perf

FIXTURES = Path(__file__).parents[1] / "modules" / "screening" / "fixtures"
# Screenable entries per list in production, roughly, and a fixture of its format.
LISTS = {
    "OFAC_SDN": (17_000, FIXTURES / "ofac" / "sdn_enhanced.xml"),
    "MX_SAT_69B": (12_000, FIXTURES / "sat69b" / "Listado_Completo_69-B.csv"),
    "EU_FSF": (5_500, FIXTURES / "eu" / "full_sanctions_list_1_1.xml"),
    "UK_FCDO": (4_500, FIXTURES / "uk" / "UK-Sanctions-List.xml"),
    "UN_CONSOLIDATED": (1_000, FIXTURES / "un" / "consolidated.xml"),
}
ENTRIES = sum(count for count, _ in LISTS.values())
# What the shard may add to the application: the service's 512 MB, less a ~100 MB
# application and headroom for requests and the worker's jobs. Met only once the
# library's MemoryIndex and name cache are leaner (the fix's PR names the request).
SHARD_BUDGET_MB = 250
# A refresh frees the old shard before it builds the new one, so it costs about
# what the warm-up does, never two shards.
REFRESH_MARGIN = 1.2
PROBE_TIMEOUT_SECONDS = 900


def _templates(code: str, path: Path) -> list[ListEntry]:
    with path.open("rb") as f:
        return [e for e in parse(code, f).entries if e.screenable]


def _renamed(name: EntryName, drawn: str) -> EntryName:
    given, _, surname = drawn.rpartition(" ")
    return dataclasses.replace(
        name,
        full=drawn.upper(),
        given=given if name.given is not None else None,
        surname=surname if name.surname is not None else None,
    )


def _shaped(template: ListEntry, key: str, rng: Draw) -> ListEntry:
    pools = split_pools()
    given = tuple(sorted(pools[0][0] + pools[1][0]))
    family = tuple(sorted(pools[0][1] + pools[1][1]))
    kinds = list(PERTURBATIONS)

    def drawn() -> str:
        base = " ".join(draw_name(rng, given, family))
        return perturb(base, rng.sample(kinds, 1), rng)

    return dataclasses.replace(
        template,
        entry_key=key,
        primary_name=_renamed(template.primary_name, drawn()),
        aliases=tuple(_renamed(alias, drawn()) for alias in template.aliases),
        identifiers=tuple(
            dataclasses.replace(
                i, value=f"{i.value}-{key}", normalized=f"{i.normalized}{key}"
            )
            for i in template.identifiers
        ),
        source_ref=f"perf {key}",
    )


def _ingest(store: LocalFsObjectStore) -> None:
    rng = Draw(SEED)
    for code, (count, path) in LISTS.items():
        templates = _templates(code, path)
        entries = (
            _shaped(templates[i % len(templates)], f"{code}-{i:05d}", rng)
            for i in range(count)
        )
        report = load_shared(code, entries, store=store)
        assert report.entry_count == count


def _run(mode: str) -> dict[str, Measure]:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=index_memory_probe.probe, args=(mode, send))
    child.start()
    send.close()
    assert receive.poll(PROBE_TIMEOUT_SECONDS), f"{mode} probe sent nothing"
    measures: dict[str, Measure] = receive.recv()
    child.join(PROBE_TIMEOUT_SECONDS)
    assert child.exitcode == 0, f"{mode} probe exited {child.exitcode}"
    return measures


def test_shared_shard_memory_at_40k_entries(admin_engine: Engine, tmp_path: Path):
    seed_sources(admin_engine)
    _ingest(LocalFsObjectStore(tmp_path / "objs"))

    stages = _run("stages")
    traced = _run("holder-traced")
    untraced = _run("holder")
    for name, measures in (("stages", stages), ("traced", traced), ("rss", untraced)):
        record(f"screening_index_memory_{name}", entries=ENTRIES, **measures)

    assert stages["rows_read"] == ENTRIES
    assert untraced["indexed"] == ENTRIES
    app = float(untraced["app_kept_rss_mb"])
    warm = float(untraced["warm_rss_mb"]) - app
    refresh = float(untraced["refresh_rss_mb"]) - app
    assert refresh < warm * REFRESH_MARGIN, f"refresh {refresh} MB, warm {warm} MB"
    assert max(warm, refresh) < SHARD_BUDGET_MB, f"the shard adds {warm} MB"
