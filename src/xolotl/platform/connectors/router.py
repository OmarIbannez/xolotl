"""The connector router and ``run_check``: primary first, then the fallbacks (§8).

The router holds the connectors of a process and the route table that orders them
per ``(check type, country)``. ``run_check`` resolves the tenant's effective mode and
walks the route until a connector gives a definitive outcome.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from xolotl.platform.connectors.base import Connector
from xolotl.platform.connectors.errors import NoRouteError
from xolotl.platform.connectors.types import ConnectorRequest, VerificationOutcome
from xolotl.platform.db import bound_context
from xolotl.platform.enums import (
    CallOutcome,
    CheckType,
    ConnectorMode,
    ConnectorRoute,
    TenantEnv,
)
from xolotl.platform.tenancy.tenants import tenant_runtime

DEFINITIVE: frozenset[CallOutcome] = frozenset({
    CallOutcome.PASS,
    CallOutcome.FAIL,
    CallOutcome.INCONCLUSIVE,
})


class ConnectorRouter:
    """The connectors of a process and the order they serve each check type in."""

    def __init__(
        self,
        connectors: Sequence[Connector],
        routes: Mapping[tuple[CheckType, str], Sequence[str]],
        *,
        default_mode: ConnectorMode,
    ) -> None:
        """Check the route table against the connectors.

        Raises:
            ValueError: When two connectors share a name, or a route names an unknown
                connector or one that does not serve the route's check type.

        """
        by_name: dict[str, Connector] = {}
        for connector in connectors:
            if connector.name in by_name:
                msg = f"two connectors are named {connector.name!r}"
                raise ValueError(msg)
            by_name[connector.name] = connector
        for (check_type, country), names in routes.items():
            for name in names:
                found = by_name.get(name)
                if found is None:
                    msg = f"the route {check_type}/{country} names {name!r}, unknown"
                    raise ValueError(msg)
                if check_type not in found.check_types:
                    msg = f"{name!r} does not serve {check_type}"
                    raise ValueError(msg)
        self._connectors: tuple[Connector, ...] = tuple(connectors)
        self._by_name: dict[str, Connector] = by_name
        self._routes: dict[tuple[CheckType, str], tuple[str, ...]] = {
            key: tuple(names) for key, names in routes.items()
        }
        self._default_mode: ConnectorMode = default_mode

    @property
    def connectors(self) -> Sequence[Connector]:
        """The connectors, in registration order."""
        return self._connectors

    @property
    def default_mode(self) -> ConnectorMode:
        """The mode of a ``PROD`` tenant's call that names none."""
        return self._default_mode

    def connector(self, name: str) -> Connector:
        """Return the connector named ``name``.

        Returns:
            The connector.

        """
        return self._by_name[name]

    def route(
        self, check_type: CheckType, *, country: str, tenant_mode: ConnectorMode
    ) -> list[Connector]:
        """Order the connectors of a check type and country, the primary first.

        A ``SANDBOX`` call routes the primary only: mocks never fail over.

        Returns:
            The primary, then the fallbacks in table order.

        Raises:
            NoRouteError: When the table has no entry for the check type and country.

        """
        names = self._routes.get((check_type, country))
        if not names:
            raise NoRouteError
        if tenant_mode is ConnectorMode.SANDBOX:
            names = names[:1]
        return [self._by_name[name] for name in names]

    def positions(self, name: str) -> list[tuple[CheckType, str, ConnectorRoute]]:
        """List every route the connector named ``name`` appears in.

        Returns:
            ``(check type, country, PRIMARY or FALLBACK)``, in table order.

        """
        return [
            (
                check_type,
                country,
                ConnectorRoute.PRIMARY if index == 0 else ConnectorRoute.FALLBACK,
            )
            for (check_type, country), names in self._routes.items()
            for index, routed in enumerate(names)
            if routed == name
        ]


def run_check(
    session: Session,
    request: ConnectorRequest,
    *,
    router: ConnectorRouter,
    mode: ConnectorMode | None = None,
) -> VerificationOutcome:
    """Run a check through its route until a connector gives a definitive outcome.

    A ``SANDBOX`` tenant is always a ``SANDBOX`` call; a ``PROD`` tenant's call takes
    ``mode`` or, when none is given, the router's default mode.

    Returns:
        The first ``PASS``, ``FAIL`` or ``INCONCLUSIVE`` outcome, or the last outcome
        when every connector of the route failed.

    """
    runtime = tenant_runtime(session, bound_context(session).require_tenant_id())
    effective = (
        ConnectorMode.SANDBOX
        if runtime.env is TenantEnv.SANDBOX
        else (mode or router.default_mode)
    )
    primary, *fallbacks = router.route(
        request.check_type, country=request.country, tenant_mode=effective
    )
    outcome = primary.run(session, request, mode=effective)
    for connector in fallbacks:
        if outcome.outcome in DEFINITIVE:
            break
        outcome = connector.run(
            session, request, mode=effective, route=ConnectorRoute.FALLBACK
        )
    return outcome
