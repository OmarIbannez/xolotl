"""The platform's default route table and the router the composition root builds.

No per-tenant connector configuration exists yet, so every tenant routes through
``DEFAULT_ROUTES``. The Banxico connector joins the table and the router when it lands.
"""

from collections.abc import Mapping

from xolotl.platform.config import Settings
from xolotl.platform.connectors.base import ConnectorDeps
from xolotl.platform.connectors.router import ConnectorRouter
from xolotl.platform.connectors.sat import SAT_RFC, SatRfcConnector
from xolotl.platform.connectors.vault import CredentialVault
from xolotl.platform.enums import CheckType, ConnectorMode
from xolotl.platform.storage import ObjectStore

DEFAULT_ROUTES: Mapping[tuple[CheckType, str], tuple[str, ...]] = {
    (CheckType.RFC_VALIDATION, "MX"): (SAT_RFC,),
}


def default_router(settings: Settings, *, store: ObjectStore) -> ConnectorRouter:
    """Build the process's router over the platform's connectors.

    Returns:
        A router on ``DEFAULT_ROUTES`` whose default mode is
        ``XOLOTL_CONNECTOR_MODE_DEFAULT``.

    """
    deps = ConnectorDeps(vault=CredentialVault(), store=store)
    return ConnectorRouter(
        [SatRfcConnector(deps)],
        DEFAULT_ROUTES,
        default_mode=ConnectorMode(settings.connector_mode_default),
    )
