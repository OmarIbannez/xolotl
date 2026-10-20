"""Helpers the catalog tests share (SP-03a): seeding, versions, ingestion."""

import hashlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session
from xolotl.modules.screening.catalog.ingest import (
    IngestReport,
    ingest_file,
    ingest_parsed,
)
from xolotl.modules.screening.catalog.models import ListVersion
from xolotl.modules.screening.catalog.sources import read_sources, seed_list_sources
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.lists import ListEntry, ParsedList
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.policy import assign_pack
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


def make_version(
    admin_engine: Engine,
    code: str,
    seq: int,
    *,
    added: int = 1,
    changed: int = 0,
    removed: int = 0,
) -> UUID:
    """Insert an ``ACTIVE`` ``list_version`` directly, superseding the active one.

    No parser and no entries: the row carries only the counts a catch-up reads.

    Returns:
        The id of the new version.

    """
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        previous = s.scalars(
            select(ListVersion.id).where(
                ListVersion.list_code == code,
                ListVersion.status == VersionStatus.ACTIVE,
            )
        ).one_or_none()
        _ = s.execute(
            update(ListVersion)
            .where(ListVersion.id == previous)
            .values(status=VersionStatus.SUPERSEDED)
        )
        version = ListVersion(
            list_code=code,
            seq=seq,
            status=VersionStatus.ACTIVE,
            parser_version="test/1",
            fetched_at=clock.now(),
            loaded_by="cli:test",
            file_sha256=hashlib.sha256(f"{code}:{seq}".encode()).digest(),
            file_size=1,
            storage_key=f"lists/{code}/{seq}.xml",
            entry_count=added,
            added=added,
            changed=changed,
            removed=removed,
            previous_version_id=previous,
        )
        s.add(version)
        s.flush()
        return version.id


def assign_tenant_wide(
    tenant: CreatedTenant, pack_version_id: UUID, effective_from: date
) -> UUID:
    """Assign a pack version to every product of the tenant from a date.

    Returns:
        The id of the assignment.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return assign_pack(
            s,
            pack_version_id=pack_version_id,
            product=None,
            effective_from=effective_from,
            approved_by=tenant.admin_user_id,
        )
