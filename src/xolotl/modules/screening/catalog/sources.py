"""The shared list catalog: ``sources.yaml``, its reader and the seed (spec §6.1).

``read_sources`` validates the file; ``seed_list_sources`` upserts the catalog
columns of ``list_source`` by code through the owner role (the application role may
update only the operational columns, migration 0009). A code that leaves the file
is disabled, never deleted: its versions and the tenants' subscriptions reference
it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import ClassVar, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import ListSource
from xolotl.modules.screening.enums import ListKind, SourceFormat
from xolotl.platform.errors import ValidationFailedError

SOURCES_FILE: Path = Path(__file__).parent.parent / "sources.yaml"


class SourceSpec(BaseModel):
    """One shared list as ``sources.yaml`` describes it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    list_kind: ListKind
    title: str = Field(min_length=1)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    format: SourceFormat
    url: HttpUrl
    poll_interval: timedelta = Field(gt=timedelta(0))
    url_checked_on: date


class _File(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    sources: list[SourceSpec]


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one seed did: rows inserted, updated, left alone and disabled."""

    inserted: int
    updated: int
    unchanged: int
    disabled: int


def read_sources(path: Path = SOURCES_FILE) -> list[SourceSpec]:
    """Read and validate a sources file.

    Returns:
        Every source of the file, in file order.

    Raises:
        ValidationFailedError: When the file is missing, is not YAML, does not
            follow the schema or lists a code twice.

    """
    try:
        raw = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
        specs = _File.model_validate(raw).sources
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        msg = f"{path}: {exc}"
        raise ValidationFailedError(msg) from exc
    seen: set[str] = set()
    for spec in specs:
        if spec.code in seen:
            msg = f"{path}: the code {spec.code} is listed twice"
            raise ValidationFailedError(msg)
        seen.add(spec.code)
    return specs


def _catalog_columns(spec: SourceSpec) -> dict[str, object]:
    return {
        "list_kind": spec.list_kind,
        "title": spec.title,
        "jurisdiction": spec.jurisdiction,
        "format": spec.format,
        "url": str(spec.url),
        "poll_interval": spec.poll_interval,
        "enabled": True,
    }


def seed_list_sources(session: Session, specs: Sequence[SourceSpec]) -> SeedReport:
    """Upsert the catalog columns of ``list_source`` from the specs, by code.

    Runs in an owner-role session. Only columns whose value differs are written, and
    the operational columns (lease, validators, outcome) are never touched. A row
    whose code is not among the specs is set ``enabled = false``.

    Returns:
        The counts of inserted, updated, unchanged and newly disabled rows.

    """
    existing = {row.code: row for row in session.scalars(select(ListSource))}
    inserted = updated = unchanged = disabled = 0
    for spec in specs:
        columns = _catalog_columns(spec)
        row = existing.get(spec.code)
        if row is None:
            session.add(ListSource(code=spec.code, **columns))
            inserted += 1
            continue
        changed = False
        for name, value in columns.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed = True
        if changed:
            updated += 1
        else:
            unchanged += 1
    listed = {spec.code for spec in specs}
    for code, row in existing.items():
        if code not in listed and row.enabled:
            row.enabled = False
            disabled += 1
    session.flush()
    return SeedReport(
        inserted=inserted, updated=updated, unchanged=unchanged, disabled=disabled
    )
