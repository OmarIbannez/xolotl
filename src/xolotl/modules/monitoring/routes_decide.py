"""The decide API: decisions and rail settings (SP-04 spec §12, §15; plan 4c C4, C5).

``POST /v1/transactions/decide`` takes one transaction from an API key with
``transactions:write`` (the product owner's call of 2026-10-21: no separate
``transactions:decide`` scope) and an ``Idempotency-Key``; the item is validated as
a batch item is (422 with each field located under ``body``) and answered 200 with
the decision and codes only, the fail mode included, never 503. ``GET
/v1/monitoring/decisions/{id}`` shows staff the whole decision and an API key with
``transactions:read`` the answer and its mode only (D20).

Every staff role reads the rails' fail modes; only an ADMIN sets one, and no API
key reaches either route. A path naming no ``Rail`` member is 404 ``RAIL_UNKNOWN``.
The router is included before the transactions router, so the literal
``/transactions/decide`` wins over ``/transactions/{transaction_id}``. Routes hold
no business rule.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from pydantic import JsonValue

from xolotl.modules.monitoring.decide.rails import (
    rail_setting_response,
    rail_settings,
    set_fail_mode,
)
from xolotl.modules.monitoring.decide.service import decide
from xolotl.modules.monitoring.decide.views import (
    client_reasons,
    decision_client_view,
    decision_staff_view,
)
from xolotl.modules.monitoring.errors import RailUnknownError, body_error_items
from xolotl.modules.monitoring.schemas import (
    DecideResponse,
    DecisionClientResponse,
    DecisionStaffResponse,
    RailSettingRequest,
    RailSettingResponse,
)
from xolotl.modules.monitoring.txn.schema import Rail, parse_item
from xolotl.platform import clock
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.config import get_settings
from xolotl.platform.enums import Role
from xolotl.platform.errors import ValidationFailedError

router = v1_router(prefix="", tags=["decide"])

# Staff only: a route without a scope refuses every API client.
StaffReader = Annotated[Actor, Depends(require_access())]
AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
DecideWriter = Annotated[
    Actor, Depends(require_access(scope="transactions:write", write=True))
]
DecisionReader = Annotated[Actor, Depends(require_access(scope="transactions:read"))]


def _rail(value: str) -> Rail:
    try:
        return Rail(value)
    except ValueError:
        msg = f"rail {value!r} is not known"
        raise RailUnknownError(msg) from None


def _actor(actor: Actor) -> str:
    ctx = actor.context
    return f"{ctx.actor_type.value.lower()}:{ctx.actor_id}"


@router.post("/transactions/decide", response_model_exclude_none=True)
def decide_transaction(
    body: dict[str, JsonValue], actor: DecideWriter, session: SessionDep
) -> DecideResponse:
    parsed = parse_item(
        0,
        body,
        now=clock.now(),
        max_future_minutes=get_settings().monitoring_max_future_minutes,
    )
    if isinstance(parsed, list):
        raise ValidationFailedError(errors=body_error_items(parsed))
    result = decide(session, parsed, actor=_actor(actor))
    return DecideResponse(
        decision=result.decision,
        decision_id=result.decision_id,
        reasons=client_reasons(result.reasons),
        latency_ms=result.latency_ms,
    )


@router.get("/monitoring/decisions/{decision_id}")
def get_decision(
    decision_id: UUID, actor: DecisionReader, session: SessionDep
) -> DecisionStaffResponse | DecisionClientResponse:
    if actor.is_staff:
        return decision_staff_view(session, decision_id)
    return decision_client_view(session, decision_id)


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
