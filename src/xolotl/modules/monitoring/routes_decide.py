"""The decide API: rail settings now, decisions in plan 4c C5 (SP-04 spec §12, §15).

Every staff role reads the rails' fail modes; only an ADMIN sets one, and no API
key reaches either route. A path naming no ``Rail`` member is 404 ``RAIL_UNKNOWN``.
The router is included before the transactions router, so the literal
``/transactions/decide`` of C5 wins over ``/transactions/{transaction_id}``. Routes
hold no business rule.
"""

from typing import Annotated

from fastapi import Depends

from xolotl.modules.monitoring.decide.rails import (
    rail_setting_response,
    rail_settings,
    set_fail_mode,
)
from xolotl.modules.monitoring.errors import RailUnknownError
from xolotl.modules.monitoring.schemas import RailSettingRequest, RailSettingResponse
from xolotl.modules.monitoring.txn.schema import Rail
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="", tags=["decide"])

# Staff only: a route without a scope refuses every API client.
StaffReader = Annotated[Actor, Depends(require_access())]
AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]


def _rail(value: str) -> Rail:
    try:
        return Rail(value)
    except ValueError:
        msg = f"rail {value!r} is not known"
        raise RailUnknownError(msg) from None


@router.get("/monitoring/rails")
def list_rail_settings(
    _staff: StaffReader, session: SessionDep
) -> list[RailSettingResponse]:
    return rail_settings(session)


@router.put("/monitoring/rails/{rail}")
def put_rail_setting(
    rail: str, body: RailSettingRequest, _admin: AdminWriter, session: SessionDep
) -> RailSettingResponse:
    row = set_fail_mode(session, _rail(rail), body.fail_mode)
    return rail_setting_response(row)
