"""The shared source catalog: ``sources.yaml``, its reader and the owner-role seed."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx
from tests.modules.screening.catalog.support import seed_sources
from xolotl.modules.screening.catalog import ListSource
from xolotl.modules.screening.catalog.sources import (
    SOURCES_FILE,
    SeedReport,
    read_sources,
    seed_list_sources,
)
from xolotl.modules.screening.enums import ListKind, SourceFormat
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import ValidationFailedError

SUCCESS_AT = datetime(2026, 10, 19, 12, tzinfo=UTC)


def _owner(admin_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=admin_engine, expire_on_commit=False)


def _seed(admin_engine: Engine, path: Path = SOURCES_FILE) -> SeedReport:
    with tenant_session(platform_ctx(), factory=_owner(admin_engine)) as s:
        return seed_list_sources(s, read_sources(path))


def _rows(admin_engine: Engine) -> dict[str, ListSource]:
    with tenant_session(platform_ctx(), factory=_owner(admin_engine)) as s:
        return {row.code: row for row in s.scalars(select(ListSource))}


def _shipped() -> list[dict[str, object]]:
    raw = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    return raw["sources"]


def _write(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "sources.yaml"
    _ = path.write_text(yaml.safe_dump({"sources": records}), encoding="utf-8")
    return path


def test_the_shipped_catalog_has_the_five_sources_with_the_spec_intervals():
    specs = {spec.code: spec for spec in read_sources()}
    assert {
        code: (spec.list_kind, spec.jurisdiction, spec.format, spec.poll_interval)
        for code, spec in specs.items()
    } == {
        "UN_CONSOLIDATED": (
            ListKind.SANCTION,
            "UN",
            SourceFormat.UN_XML,
            timedelta(minutes=15),
        ),
        "OFAC_SDN": (
            ListKind.SANCTION,
            "US",
            SourceFormat.OFAC_ENHANCED_XML,
            timedelta(minutes=30),
        ),
        "EU_FSF": (
            ListKind.SANCTION,
            "EU",
            SourceFormat.EU_FSF_XML,
            timedelta(minutes=60),
        ),
        "UK_FCDO": (
            ListKind.SANCTION,
            "GB",
            SourceFormat.UK_SL_XML,
            timedelta(minutes=60),
        ),
        "MX_SAT_69B": (
            ListKind.FISCAL,
            "MX",
            SourceFormat.SAT_69B_CSV,
            timedelta(hours=24),
        ),
    }
    assert all(spec.url_checked_on == date(2026, 10, 19) for spec in specs.values())
    assert all(spec.title for spec in specs.values())


@pytest.mark.parametrize("change", ["duplicate", "unknown field", "bad interval"])
def test_a_duplicate_code_or_unknown_field_is_refused(tmp_path: Path, change: str):
    records = _shipped()
    if change == "duplicate":
        records.append(dict(records[0]))
    elif change == "unknown field":
        records[0]["mirror"] = "https://mirror.test/un.xml"
    else:
        records[0]["poll_interval"] = "every quarter hour"
    with pytest.raises(ValidationFailedError):
        _ = read_sources(_write(tmp_path, records))


def test_a_missing_file_is_refused(tmp_path: Path):
    with pytest.raises(ValidationFailedError):
        _ = read_sources(tmp_path / "nope.yaml")


def test_seeding_twice_is_idempotent(admin_engine: Engine):
    assert _seed(admin_engine) == SeedReport(5, 0, 0, 0)
    assert _seed(admin_engine) == SeedReport(0, 0, 5, 0)
    rows = _rows(admin_engine)
    assert set(rows) == {spec.code for spec in read_sources()}
    assert all(row.enabled for row in rows.values())


def test_seeding_updates_the_url_and_keeps_the_operational_columns(
    admin_engine: Engine, tmp_path: Path
):
    seed_sources(admin_engine)
    # The operational columns are the app role's to write (the poll job's).
    with tenant_session(platform_ctx()) as s:
        _ = s.execute(
            update(ListSource)
            .where(ListSource.code == "UN_CONSOLIDATED")
            .values(etag='"v7"', last_success_at=SUCCESS_AT, consecutive_failures=2)
        )
    records = _shipped()
    records[0]["url"] = "https://scsanctions.un.org/resources/xml/en/moved.xml"
    assert _seed(admin_engine, _write(tmp_path, records)) == SeedReport(0, 1, 4, 0)
    row = _rows(admin_engine)["UN_CONSOLIDATED"]
    assert row.url == "https://scsanctions.un.org/resources/xml/en/moved.xml"
    assert (row.etag, row.last_success_at, row.consecutive_failures) == (
        '"v7"',
        SUCCESS_AT,
        2,
    )


def test_a_removed_code_is_disabled_not_deleted(admin_engine: Engine, tmp_path: Path):
    seed_sources(admin_engine)
    records = [r for r in _shipped() if r["code"] != "MX_SAT_69B"]
    path = _write(tmp_path, records)
    assert _seed(admin_engine, path) == SeedReport(0, 0, 4, 1)
    assert _seed(admin_engine, path) == SeedReport(0, 0, 4, 0)
    rows = _rows(admin_engine)
    assert len(rows) == 5
    assert not rows["MX_SAT_69B"].enabled
    # Back in the file, the code is enabled again.
    assert _seed(admin_engine) == SeedReport(0, 1, 4, 0)
    assert _rows(admin_engine)["MX_SAT_69B"].enabled


def test_the_app_role_cannot_seed(admin_engine: Engine, tmp_path: Path):
    seed_sources(admin_engine)
    records = _shipped()
    records[0]["url"] = "https://scsanctions.un.org/resources/xml/en/moved.xml"
    specs = read_sources(_write(tmp_path, records))
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(platform_ctx()) as s,
    ):
        _ = seed_list_sources(s, specs)
    row = _rows(admin_engine)["UN_CONSOLIDATED"]
    assert row.url == "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
