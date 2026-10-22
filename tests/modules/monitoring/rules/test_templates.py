"""Rule templates in the database: ``load_templates`` and its reads (spec §5.1)."""

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
import yaml
from sqlalchemy import Engine, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx
from xolotl.modules.monitoring.errors import (
    RuleTemplateImmutableError,
    RuleTemplateUnknownError,
)
from xolotl.modules.monitoring.rules.library import (
    TYPOLOGIES_DIR,
    TypologyFile,
    read_library,
    read_typology,
)
from xolotl.modules.monitoring.rules.models import RuleTemplate, RuleTemplateVersion
from xolotl.modules.monitoring.rules.templates import (
    LoadReport,
    definition_of,
    latest_template_versions,
    load_templates,
    template_version,
)
from xolotl.platform.db import tenant_session

GOLDEN = Path(__file__).resolve().parents[3] / "golden/monitoring/rules"
AVI = "MX/LFPIORPI-AVI"
AVI_CODES = {
    "BLOCKED_PERSON_PARTY",
    "COUNTERPARTY_TRUE_MATCH",
    "CRYPTO_OFF_RAMP",
    "NEW_ACCOUNT_VELOCITY",
    "PEP_LARGE_OPERATION",
    "RAPID_PASS_THROUGH",
    "SELF_CUSTODY_OUTFLOW",
}


def load(admin_engine: Engine, files: Sequence[TypologyFile]) -> LoadReport:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        return load_templates(s, files, loaded_by="cli:test")


def _copy(tmp_path: Path, code: str, *, old: str, new: str) -> Path:
    source = TYPOLOGIES_DIR / f"{code}.yaml"
    target = tmp_path / source.name
    text = source.read_text(encoding="utf-8")
    assert old in text
    _ = target.write_text(text.replace(old, new), encoding="utf-8")
    return target


def test_loading_the_library_twice_creates_once_and_changes_nothing(
    admin_engine: Engine,
):
    library = read_library()
    first = load(admin_engine, library)
    assert len(first.created) == 16
    assert first.unchanged == ()
    assert "CASH_STRUCTURING v1" in first.created
    again = load(admin_engine, library)
    assert again.created == ()
    assert sorted(again.unchanged) == sorted(first.created)
    pinned = cast(
        "dict[str, dict[int, str]]",
        yaml.safe_load((GOLDEN / "hashes.yaml").read_text(encoding="utf-8")),
    )
    with tenant_session(platform_ctx()) as s:
        rows = s.scalars(select(RuleTemplateVersion)).all()
        assert len(rows) == 16
        for row in rows:
            assert row.loaded_by == "cli:test"
            assert row.source_path == str(TYPOLOGIES_DIR / f"{row.template_code}.yaml")
            assert row.definition_hash.hex() == pinned[row.template_code][row.version]
            assert definition_of(row).definition_hash() == row.definition_hash
        cash = s.get_one(RuleTemplate, "CASH_STRUCTURING")
        assert (cash.family, cash.kind, cash.country_code) == (
            "ALERT",
            "STRUCTURING",
            "MX",
        )
        assert (cash.typology, cash.packs) == (["STRUCTURING"], ["MX/ITF-IFPE"])


def test_a_changed_version_is_refused_and_nothing_is_written(
    admin_engine: Engine, tmp_path: Path
):
    _ = load(admin_engine, read_library())
    changed = _copy(
        tmp_path,
        "CASH_STRUCTURING",
        old='threshold: {value: "5000"',
        new='threshold: {value: "4000"',
    )
    fresh = _copy(
        tmp_path,
        "TIER_N2_MONTHLY_OPS",
        old="code: TIER_N2_MONTHLY_OPS",
        new="code: TIER_N3_MONTHLY_OPS",
    )
    fresh = fresh.rename(tmp_path / "TIER_N3_MONTHLY_OPS.yaml")
    batch = [read_typology(fresh), read_typology(changed)]
    with pytest.raises(RuleTemplateImmutableError) as caught:
        _ = load(admin_engine, batch)
    assert (
        caught.value.detail == "CASH_STRUCTURING v1 is loaded with another definition"
    )
    assert caught.value.status == 409
    with tenant_session(platform_ctx()) as s:
        assert s.get(RuleTemplate, "TIER_N3_MONTHLY_OPS") is None
        assert len(s.scalars(select(RuleTemplateVersion)).all()) == 16


def test_a_new_version_of_a_template_updates_the_template_row(
    admin_engine: Engine, tmp_path: Path
):
    _ = load(admin_engine, read_library())
    v2 = _copy(
        tmp_path,
        "CASH_STRUCTURING",
        old="version: 1\n",
        new="version: 2\n",
    )
    _ = v2.write_text(
        v2.read_text(encoding="utf-8").replace(
            "title: Estructuración de depósitos en efectivo",
            "title: Estructuración en efectivo",
        ),
        encoding="utf-8",
    )
    report = load(admin_engine, [read_typology(v2)])
    assert report.created == ("CASH_STRUCTURING v2",)
    # Loading v1 again afterwards leaves the row on v2.
    _ = load(admin_engine, [read_typology(TYPOLOGIES_DIR / "CASH_STRUCTURING.yaml")])
    with tenant_session(platform_ctx()) as s:
        assert s.get_one(RuleTemplate, "CASH_STRUCTURING").title == (
            "Estructuración en efectivo"
        )
        assert template_version(s, "CASH_STRUCTURING").version == 2
        assert template_version(s, "CASH_STRUCTURING", 1).version == 1
        latest = latest_template_versions(s)
        assert len(latest) == 16
        cash = next(row for row in latest if row.template_code == "CASH_STRUCTURING")
        assert cash.version == 2


def test_templates_are_filtered_by_pack(admin_engine: Engine):
    _ = load(admin_engine, read_library())
    with tenant_session(platform_ctx()) as s:
        avi = latest_template_versions(s, packs={AVI})
        assert {row.template_code for row in avi} == AVI_CODES
        assert not any(row.template_code.startswith("TIER_") for row in avi)
        assert len(latest_template_versions(s, packs={AVI, "MX/ITF-IFPE"})) == 16
        assert latest_template_versions(s, packs=set()) == []


def test_an_unknown_template_is_named(admin_engine: Engine):
    _ = load(admin_engine, read_library())
    with tenant_session(platform_ctx()) as s:
        with pytest.raises(RuleTemplateUnknownError, match="no rule template NOPE"):
            _ = template_version(s, "NOPE")
        with pytest.raises(
            RuleTemplateUnknownError, match="no rule template CASH_STRUCTURING v9"
        ):
            _ = template_version(s, "CASH_STRUCTURING", 9)


def test_the_app_role_cannot_write_templates(admin_engine: Engine, tmp_path: Path):
    _ = shutil.copy(TYPOLOGIES_DIR / "CASH_STRUCTURING.yaml", tmp_path)
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(platform_ctx()) as s,
    ):
        _ = load_templates(s, read_library(tmp_path), loaded_by="cli:test")
