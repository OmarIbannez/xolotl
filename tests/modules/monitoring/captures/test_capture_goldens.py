"""The capture goldens (tests/golden/monitoring/captures/<CODE>/, plan 4b B10).

Each directory holds the unit rows it needs (``units.yaml``), the transactions of
one customer (``transactions.yaml``) and the exact reportable events and groups
processing writes for them (``expected.yaml``). The items are ingested one at a
time, an hour after they occurred, and processed by the worker; unit value ids
are written as ``"<unit_code> <valid_from>"``, transactions by external id and
groups by their creation order (``G1``, ``G2``).
"""

import difflib
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from pydantic import JsonValue
from sqlalchemy import Engine

from tests.modules.monitoring.captures.support import (
    CaptureStage,
    add_relationship,
    build_stage,
    events,
    external_ids,
    feed,
    groups,
)
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.tenants.service import CreatedTenant

if TYPE_CHECKING:
    from uuid import UUID

GOLDEN = Path(__file__).resolve().parents[3] / "golden/monitoring/captures"
CODES = sorted(path.name for path in GOLDEN.iterdir() if path.is_dir())


def _read(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _named_units(value: JsonValue, names: dict[int, str]) -> JsonValue:
    """Replace every ``unit_value_id`` by the row's code and date.

    Returns:
        The value with ids named.

    """
    if isinstance(value, dict):
        return {
            key: (
                names.get(item, item)
                if key == "unit_value_id" and isinstance(item, int)
                else _named_units(item, names)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_named_units(item, names) for item in value]
    return value


def render(stage: CaptureStage) -> dict[str, Any]:
    """Describe the stage's events and groups as ``expected.yaml`` spells them.

    Returns:
        ``{"events": [...], "groups": [...]}``.

    """
    names = {
        row_id: f"{code} {valid_from.isoformat()}"
        for (code, valid_from), row_id in stage.unit_ids.items()
    }
    by_id = external_ids(stage)
    labels: dict[UUID, str] = {}
    rendered_groups: list[dict[str, Any]] = []
    for index, group in enumerate(groups(stage), start=1):
        labels[group.id] = f"G{index}"
        rendered_groups.append({
            "group": f"G{index}",
            "capture_code": group.capture_code,
            "status": group.status.value,
            "period_key": group.period_key,
            "window_start": group.window_start.isoformat(),
            "window_end": group.window_end.isoformat(),
            "threshold_crossed_at": None
            if group.threshold_crossed_at is None
            else group.threshold_crossed_at.isoformat(),
            "accumulated_minor": group.accumulated_minor,
            "accumulated_unit": str(group.accumulated_unit.normalize()),
            "superseded_by": None
            if group.superseded_by_id is None
            else labels.get(group.superseded_by_id, "?"),
        })
    rendered_events = [
        {
            "transaction": by_id[event.transaction_id],
            "capture_code": event.capture_code,
            "capture_kind": event.capture_kind.value,
            "report_code": event.report_code,
            "period_key": event.period_key,
            "capture_group": None
            if event.capture_group_id is None
            else labels[event.capture_group_id],
            "status": event.status.value,
            "void_reason": event.void_reason,
            "late": event.late,
            "fields": event.fields,
            "unit_values": _named_units(event.unit_values, names),
        }
        for event in sorted(
            events(stage),
            key=lambda e: (e.occurred_at, by_id[e.transaction_id], e.capture_code),
        )
    ]
    return {"events": rendered_events, "groups": rendered_groups}


def run_golden(stage: CaptureStage, directory: Path) -> dict[str, Any]:
    """Ingest and process the directory's transactions on ``stage``.

    Returns:
        The rendered events and groups.

    """
    data = _read(directory / "transactions.yaml")
    for relationship in data["relationships"]:
        _ = add_relationship(
            stage, relationship["ref"], product=relationship["product"]
        )
    _ = feed(stage, data["items"])
    return render(stage)


def _dump(value: Any) -> list[str]:
    text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100)
    return text.splitlines(keepends=True)


@pytest.fixture
def forget_hooks() -> Iterator[None]:
    yield
    reset_processing_hooks()


@pytest.mark.usefixtures("forget_hooks")
@pytest.mark.parametrize("code", CODES)
def test_capture_goldens(code: str, tenant: CreatedTenant, admin_engine: Engine):
    directory = GOLDEN / code
    stage = build_stage(tenant, admin_engine, directory / "units.yaml")

    actual = run_golden(stage, directory)

    expected = _read(directory / "expected.yaml")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                _dump(expected), _dump(actual), "expected.yaml", "actual"
            )
        )
        pytest.fail(f"{code} differs from its golden:\n{diff}")


def test_the_goldens_cover_every_capture_code():
    assert CODES == [
        "AVI_XVI_A",
        "AVI_XVI_B",
        "ITF_FX_CASH",
        "ITF_INTL_TRANSFER",
        "ITF_ROR_CASH",
        "ITF_VA_OPS",
    ]
    assert date(2026, 12, 13).weekday() == 6  # the ROR deposit is on a Sunday
