"""Connector endpoints, for tenant administrators (SP-02 spec §9).

Staff with the ``ADMIN`` role only: the list of the process's connectors with their
mode, routes, circuit state and the modes a credential is stored for; the usage of the
tenant's connector calls per UTC day; and the credential set, which answers with the
field names and never a value.
"""

from datetime import date
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy import select

from xolotl.modules.tenants.schemas import (
    ConnectorCredentialRequest,
    ConnectorCredentialResponse,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorRouteResponse,
    ConnectorUsageResponse,
    ConnectorUsageRow,
)
from xolotl.platform.api.app import app_services
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.connectors import ConnectorRouter, CredentialVault, usage
from xolotl.platform.connectors.models import ConnectorCredential
from xolotl.platform.enums import ConnectorMode, Role, TenantEnv
from xolotl.platform.errors import NotFoundError
from xolotl.platform.tenancy.tenants import tenant_runtime

router = v1_router(prefix="/connectors", tags=["connectors"])

AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]
AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]


def connector_router(request: Request) -> ConnectorRouter:
    """Provide the connector router the process was composed with.

    Returns:
        The router on the application's platform services.

    """
    return app_services(request).connectors


RouterDep = Annotated[ConnectorRouter, Depends(connector_router)]


@router.get("")
def list_connectors(
    actor: AdminReader, session: SessionDep, connectors: RouterDep
) -> ConnectorListResponse:
    runtime = tenant_runtime(session, actor.context.require_tenant_id())
    mode = (
        ConnectorMode.SANDBOX
        if runtime.env is TenantEnv.SANDBOX
        else connectors.default_mode
    )
    stored: dict[str, set[ConnectorMode]] = {}
    for name, stored_mode in session.execute(
        select(ConnectorCredential.connector, ConnectorCredential.mode)
    ):
        stored.setdefault(name, set()).add(stored_mode)
    return ConnectorListResponse(
        items=[
            ConnectorResponse(
                name=connector.name,
                check_types=sorted(connector.check_types),
                mode=mode,
                routes=[
                    ConnectorRouteResponse(
                        check_type=check_type, country=country, position=position
                    )
                    for check_type, country, position in connectors.positions(
                        connector.name
                    )
                ],
                circuit_state=connector.circuit_state(runtime.cell),
                credential_modes=sorted(stored.get(connector.name, set())),
            )
            for connector in connectors.connectors
        ]
    )


@router.get("/usage")
def connector_usage(
    _admin: AdminReader,
    session: SessionDep,
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
) -> ConnectorUsageResponse:
    rows = usage(session, start=from_, end=to)
    return ConnectorUsageResponse(
        start=from_,
        end=to,
        items=[
            ConnectorUsageRow(
                day=row.day,
                connector=row.connector,
                calls=row.calls,
                cost_micros=row.cost_micros,
                outcomes=row.outcomes,
            )
            for row in rows
        ],
    )


@router.put("/{name}/credentials/{mode}")
def set_connector_credentials(
    name: str,
    mode: ConnectorMode,
    body: ConnectorCredentialRequest,
    _admin: AdminWriter,
    session: SessionDep,
    connectors: RouterDep,
) -> ConnectorCredentialResponse:
    if name not in {connector.name for connector in connectors.connectors}:
        msg = "unknown connector"
        raise NotFoundError(msg)
    row = CredentialVault().set(session, name, mode, body.fields)
    return ConnectorCredentialResponse(
        connector=row.connector,
        mode=row.mode,
        field_names=sorted(body.fields),
        created_at=row.created_at,
        rotated_at=row.rotated_at,
    )
