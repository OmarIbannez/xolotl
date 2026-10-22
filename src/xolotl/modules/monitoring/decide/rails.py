"""Rail settings: the fail mode each rail answers with (SP-04 spec §12.1).

A rail without a ``rail_setting`` row answers ``XOLOTL_MONITORING_DECIDE_FAIL_MODE``
(``FAIL_OPEN`` by default, §21 Q4). Setting a rail's mode upserts its row as the
signed-in staff member and audits ``monitoring.rail_setting_changed``.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.decide.enums import FailMode
from xolotl.modules.monitoring.decide.models import RailSetting
from xolotl.modules.monitoring.schemas import RailSettingResponse
from xolotl.modules.monitoring.txn.schema import Rail
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context

RAIL_SETTING_CHANGED = "monitoring.rail_setting_changed"


def _default() -> FailMode:
    return FailMode(get_settings().monitoring_decide_fail_mode)


def fail_mode_for(s: Session, rail: Rail) -> FailMode:
    """Look up the fail mode ``rail`` answers with in the session's tenant.

    Returns:
        The stored mode, or the configured default when the rail has no row.

    """
    stored = s.scalar(select(RailSetting.fail_mode).where(RailSetting.rail == rail))
    return _default() if stored is None else stored


def set_fail_mode(s: Session, rail: Rail, mode: FailMode) -> RailSetting:
    """Store ``mode`` for ``rail`` as the session's staff member, and audit it.

    Returns:
        The rail's one row, inserted or updated.

    """
    ctx = bound_context(s)
    values = {
        "fail_mode": mode,
        "changed_by": UUID(ctx.actor_id),
        "updated_at": clock.now(),
    }
    statement = (
        insert(RailSetting)
        .values(tenant_id=ctx.require_tenant_id(), rail=rail, **values)
        .on_conflict_do_update(
            index_elements=[RailSetting.tenant_id, RailSetting.rail],
            set_=values,
        )
        .returning(RailSetting)
    )
    row = s.scalars(statement, execution_options={"populate_existing": True}).one()
    _ = record(
        s,
        AuditEvent(
            action=RAIL_SETTING_CHANGED,
            entity_type="rail_setting",
            entity_id=str(row.id),
            detail={"rail": rail.value, "fail_mode": mode.value},
        ),
    )
    return row


def rail_setting_response(row: RailSetting) -> RailSettingResponse:
    return RailSettingResponse(
        rail=row.rail,
        fail_mode=row.fail_mode,
        explicit=True,
        changed_by=row.changed_by,
        updated_at=row.updated_at,
    )


def rail_settings(s: Session) -> list[RailSettingResponse]:
    """List every rail's fail mode in the session's tenant, in ``Rail`` order.

    Returns:
        One entry per ``Rail`` member: the stored row, or the default with
        ``explicit`` false.

    """
    stored = {row.rail: row for row in s.scalars(select(RailSetting))}
    default = _default()
    return [
        rail_setting_response(stored[rail])
        if rail in stored
        else RailSettingResponse(
            rail=rail,
            fail_mode=default,
            explicit=False,
            changed_by=None,
            updated_at=None,
        )
        for rail in Rail
    ]
