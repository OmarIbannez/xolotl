"""Helpers the catalog tests share (SP-03a): seeding, versions, ingestion."""

import hashlib
from datetime import date
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session
from xolotl.modules.screening.catalog.models import ListVersion
from xolotl.modules.screening.catalog.sources import read_sources, seed_list_sources
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.policy import assign_pack


def seed_sources(admin_engine: Engine) -> None:
    """Seed the shipped ``sources.yaml`` into ``list_source`` as the owner role."""
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = seed_list_sources(s, read_sources())


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
