"""Both MX packs load, hash to the recorded goldens and install immutably."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
import yaml
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx
from xolotl.platform.db import tenant_session
from xolotl.platform.policy import (
    PackConfig,
    PackImmutableError,
    PackValidationError,
    PolicyPack,
    PolicyPackVersion,
    Quantity,
    content_hash,
    install_pack,
    load_pack,
    pack_files,
    unverified_paths,
)
from xolotl.platform.sqltypes import contains_float
from xolotl.platform.units import UnverifiedValueError

PACKS = Path(__file__).resolve().parents[3] / "policy-packs"
ITF = PACKS / "MX" / "ITF-IFPE" / "v1.yaml"
AVI = PACKS / "MX" / "LFPIORPI-AVI" / "v1.yaml"
HASHES = cast(
    "dict[str, dict[int, str]]",
    yaml.safe_load((Path(__file__).parent / "hashes.yaml").read_text("utf-8")),
)


def _itf() -> PackConfig:
    return load_pack(ITF, allow_unverified=True)


def _avi() -> PackConfig:
    return load_pack(AVI, allow_unverified=True)


def test_pack_files_lists_both_packs_and_skips_the_data_directories():
    assert pack_files(PACKS) == [ITF, AVI]


def test_both_packs_load_with_unverified_allowed():
    itf, avi = _itf(), _avi()
    assert itf.products == ["EMONEY", "CROWDFUNDING", "CRYPTO"]
    assert [t.code for t in itf.kyc.tiers] == ["N1", "N2", "N3"]
    monthly = itf.kyc.tiers[0].limits.monthly_ops
    assert monthly is not None
    assert monthly.value == Quantity(amount=Decimal(750), unit="UDI")
    assert [c.code for c in itf.captures] == [
        "ITF_ROR_CASH",
        "ITF_FX_CASH",
        "ITF_INTL_TRANSFER",
        "ITF_VA_OPS",
    ]
    assert avi.effective_from == date(2026, 11, 30)
    merge = avi.captures[1].merge_into
    assert merge is not None
    assert merge.value == "AVI_XVI_A"


def test_unverified_paths_are_exactly_the_open_items():
    assert unverified_paths(_itf()) == ["captures[ITF_ROR_CASH].period"]
    assert unverified_paths(_avi()) == [
        "captures[AVI_XVI_B].merge_into",
        "captures[AVI_XVI_B].threshold",
        "retention.kyc_after_relationship",
    ]


def test_the_loader_refuses_unverified_values_by_default():
    with pytest.raises(UnverifiedValueError, match=r"captures\[ITF_ROR_CASH\].period"):
        _ = load_pack(ITF)
    with pytest.raises(UnverifiedValueError) as caught:
        _ = load_pack(AVI)
    assert caught.value.detail == (
        "MX/LFPIORPI-AVI v1: captures[AVI_XVI_B].merge_into, "
        "captures[AVI_XVI_B].threshold, retention.kyc_after_relationship"
    )


def test_the_loader_refuses_a_file_that_is_not_a_mapping(tmp_path: Path):
    for name, text in (("list.yaml", "- 1\n"), ("bad.yaml", "a: [\n")):
        path = tmp_path / name
        _ = path.write_text(text, encoding="utf-8")
        with pytest.raises(PackValidationError, match=f"{name}: not a pack"):
            _ = load_pack(path)


def test_content_hash_matches_the_golden():
    assert content_hash(_itf()).hex() == HASHES["MX/ITF-IFPE"][1]
    assert content_hash(_avi()).hex() == HASHES["MX/LFPIORPI-AVI"][1]


def test_content_hash_ignores_key_order_and_number_spelling(tmp_path: Path):
    raw = cast("dict[str, object]", yaml.safe_load(ITF.read_text("utf-8")))
    reversed_keys = dict(reversed(list(raw.items())))
    text = yaml.safe_dump(reversed_keys, allow_unicode=True, sort_keys=False)
    assert "amount: '750'" in text
    expected = content_hash(_itf())
    for index, spelling in enumerate(("750", "750.0", "'750.00'")):
        path = tmp_path / f"v1-{index}.yaml"
        _ = path.write_text(
            text.replace("amount: '750'", f"amount: {spelling}"), encoding="utf-8"
        )
        assert content_hash(load_pack(path, allow_unverified=True)) == expected


def test_config_holds_no_floats():
    for config in (_itf(), _avi()):
        assert contains_float(config.model_dump(mode="json")) is False


def test_install_pack_is_idempotent_and_versions_are_immutable(admin_engine: Engine):
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    itf = _itf()
    with tenant_session(platform_ctx(), factory=owner) as s:
        first = install_pack(s, itf, source_path=str(ITF), loaded_by="test")
    with tenant_session(platform_ctx(), factory=owner) as s:
        assert install_pack(s, itf, source_path=str(ITF), loaded_by="test") == first
        assert s.scalar(select(func.count()).select_from(PolicyPackVersion)) == 1
        pack = s.scalars(select(PolicyPack)).one()
        assert (pack.code, pack.country_code, pack.sector, pack.licence) == (
            "MX/ITF-IFPE",
            "MX",
            "ITF",
            "IFPE",
        )
        version = s.get_one(PolicyPackVersion, first)
        assert version.content_hash == content_hash(itf)
        assert version.config == itf.model_dump(mode="json")
        assert (version.effective_from, version.effective_to) == (
            date(2026, 1, 1),
            None,
        )
        assert (version.loaded_by, version.source_path) == ("test", str(ITF))
    changed = itf.model_copy(update={"title": "Another title"})
    with (
        tenant_session(platform_ctx(), factory=owner) as s,
        pytest.raises(PackImmutableError),
    ):
        _ = install_pack(s, changed, source_path=str(ITF), loaded_by="test")
    v2 = itf.model_copy(update={"version": 2, "title": "v2"})
    with tenant_session(platform_ctx(), factory=owner) as s:
        second = install_pack(s, v2, source_path="v2.yaml", loaded_by="test")
        assert second != first
        assert s.scalar(select(func.count()).select_from(PolicyPackVersion)) == 2
        # The pack row follows the newest title it was given.
        assert s.scalars(select(PolicyPack.title)).one() == "v2"


def test_the_app_role_cannot_install_packs(engine: Engine):
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(platform_ctx()) as s,
    ):
        _ = install_pack(s, _itf(), source_path=str(ITF), loaded_by="test")
