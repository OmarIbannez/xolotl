"""Helpers the catalog tests share (SP-03a): seeding, versions, ingestion."""

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx
from xolotl.modules.screening.catalog.sources import read_sources, seed_list_sources
from xolotl.platform.db import tenant_session


def seed_sources(admin_engine: Engine) -> None:
    """Seed the shipped ``sources.yaml`` into ``list_source`` as the owner role."""
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = seed_list_sources(s, read_sources())
