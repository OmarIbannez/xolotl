"""Install both MX packs and read the effective-policy golden."""

from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from xolotl.platform.db import tenant_session
from xolotl.platform.policy import PackConfig, install_pack, load_pack
from xolotl.platform.tenancy.context import TenantContext

REPO = Path(__file__).resolve().parents[2]
PACKS = REPO / "policy-packs"
ITF = PACKS / "MX" / "ITF-IFPE" / "v1.yaml"
AVI = PACKS / "MX" / "LFPIORPI-AVI" / "v1.yaml"
GOLDEN = REPO / "tests/golden/packs/effective.yaml"


def both_packs() -> dict[str, PackConfig]:
    """Load both packs, unverified values allowed.

    Returns:
        The configs by pack code, ITF first.

    """
    return {
        config.pack: config
        for config in (
            load_pack(ITF, allow_unverified=True),
            load_pack(AVI, allow_unverified=True),
        )
    }


def install_both_packs(admin_engine: Engine) -> dict[str, UUID]:
    """Install both packs in one owner transaction, ITF first.

    Returns:
        The version ids by pack code.

    """
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    ids: dict[str, UUID] = {}
    with tenant_session(TenantContext.platform("test:policy"), factory=owner) as s:
        for path in (ITF, AVI):
            config = load_pack(path, allow_unverified=True)
            ids[config.pack] = install_pack(
                s, config, source_path=str(path), loaded_by="test"
            )
    return ids


def load_golden() -> dict[str, Any]:
    """Read ``effective.yaml``, naming back the keys YAML 1.1 reads as ``True``.

    Returns:
        The golden: ``assignments``, ``override`` and ``cases``.

    """
    data: dict[str, Any] = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    data["cases"] = [
        {("on" if key is True else key): value for key, value in case.items()}
        for case in data["cases"]
    ]
    return data
