"""The connector SDK: one way to call an external verification service.

Connectors look up tenant-encrypted credentials, retry behind a circuit breaker,
store the raw answer as evidence and meter every call in ``connector_call``.
"""

from xolotl.platform.connectors.base import Connector, ConnectorDeps, redact
from xolotl.platform.connectors.breaker import CircuitBreaker
from xolotl.platform.connectors.defaults import DEFAULT_ROUTES, default_router
from xolotl.platform.connectors.errors import NoRouteError
from xolotl.platform.connectors.metering import UsageRow, usage
from xolotl.platform.connectors.router import ConnectorRouter, run_check
from xolotl.platform.connectors.types import (
    CheckResult,
    ConnectorCallError,
    ConnectorRequest,
    CredentialMissingError,
    InterpretationError,
    PermanentCallError,
    RawResponse,
    TransientCallError,
    VerificationOutcome,
)
from xolotl.platform.connectors.vault import CredentialVault
from xolotl.platform.enums import (
    CallOutcome,
    CheckType,
    CircuitState,
    ConnectorMode,
    ConnectorRoute,
)

__all__ = [
    "DEFAULT_ROUTES",
    "CallOutcome",
    "CheckResult",
    "CheckType",
    "CircuitBreaker",
    "CircuitState",
    "Connector",
    "ConnectorCallError",
    "ConnectorDeps",
    "ConnectorMode",
    "ConnectorRequest",
    "ConnectorRoute",
    "ConnectorRouter",
    "CredentialMissingError",
    "CredentialVault",
    "InterpretationError",
    "NoRouteError",
    "PermanentCallError",
    "RawResponse",
    "TransientCallError",
    "UsageRow",
    "VerificationOutcome",
    "default_router",
    "redact",
    "run_check",
    "usage",
]
