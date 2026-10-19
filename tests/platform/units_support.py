"""Seed the holidays and the fixture unit values the unit goldens read."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from xolotl.platform.calendar import read_holiday_file, seed_holidays
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Confidence
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.units import UNITS, UnitValue, upsert_value

REPO = Path(__file__).resolve().parents[2]
UNIT_GOLDEN = REPO / "tests/golden/units"


def seed_mx_holidays(admin_engine: Engine) -> None:
    rows = read_holiday_file(REPO / "policy-packs/_holidays/MX.yaml")
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(TenantContext.platform("test:units"), factory=owner) as s:
        _ = seed_holidays(s, rows)


def _value(row: dict[str, Any]) -> UnitValue:
    unit_code = str(row["unit_code"])
    valid_from: date = row["valid_from"]
    valid_to: date | None = row.get("valid_to")
    if UNITS[unit_code].daily:
        valid_to = valid_to or valid_from
    return UnitValue(
        unit_code=unit_code,
        valid_from=valid_from,
        valid_to=valid_to,
        value=Decimal(str(row["value"])),
        quote_ccy="MXN",
        source="fixture",
        confidence=Confidence.VERIFIED,
    )


def load_unit_values(session: Session, path: Path) -> dict[tuple[str, date], int]:
    """Upsert the corrections an hour early, then the rows; map keys to row ids.

    Returns:
        ``{(unit_code, valid_from): id}`` of the current rows.

    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    fetched_at = datetime.fromisoformat(str(data["fetched_at"]))
    for row in data.get("corrections", []):
        _ = upsert_value(
            session, _value(row), fetched_at=fetched_at - timedelta(hours=1)
        )
    ids: dict[tuple[str, date], int] = {}
    for row in data["rows"]:
        value = _value(row)
        new_id = upsert_value(session, value, fetched_at=fetched_at)
        assert new_id is not None
        ids[value.unit_code, value.valid_from] = new_id
    return ids
