"""Helpers the catalog tests share (SP-03a): seeding, versions, ingestion."""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx
from xolotl.modules.screening.catalog.ingest import (
    IngestReport,
    ingest_file,
    ingest_parsed,
)
from xolotl.modules.screening.catalog.sources import read_sources, seed_list_sources
from xolotl.modules.screening.lists import ListEntry, ParsedList
from xolotl.platform.db import tenant_session
from xolotl.platform.storage import ObjectStore

FIXTURES = Path(__file__).parent / "fixtures"
# The library's trimmed UN list, and the same with one entry added, one changed and
# one removed (fixtures/README.md).
UN_V1 = Path(__file__).parents[1] / "fixtures" / "un" / "consolidated.xml"
UN_V2 = FIXTURES / "un_v2.xml"
LOADED_AT = datetime(2026, 10, 19, 23, 30, tzinfo=UTC)


def seed_sources(admin_engine: Engine) -> None:
    """Seed the shipped ``sources.yaml`` into ``list_source`` as the owner role."""
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = seed_list_sources(s, read_sources())


def ingest_fixture(code: str, path: Path, *, store: ObjectStore) -> IngestReport:
    """Ingest a list file through ``ingest_file`` in a platform session of its own.

    Returns:
        The ingestion report.

    """
    with tenant_session(platform_ctx()) as s:
        return ingest_file(
            s,
            code,
            path.read_bytes(),
            store=store,
            loaded_by="cli:test",
            fetched_at=LOADED_AT,
        )


def load_shared(
    code: str, entries: Iterable[ListEntry], *, store: ObjectStore
) -> IngestReport:
    """Write hand-built entries as the next version of a shared list.

    Returns:
        The ingestion report.

    """
    parsed = ParsedList(
        list_code=code,
        parser_version="test/1",
        published_at=None,
        source_version=None,
        entries=tuple(entries),
    )
    data = b"\n".join(e.canonical_json() for e in parsed.entries)
    with tenant_session(platform_ctx()) as s:
        return ingest_parsed(
            s,
            parsed,
            data=data,
            store=store,
            loaded_by="test",
            fetched_at=LOADED_AT,
        )
