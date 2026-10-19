"""The connector SDK: one way to call an external verification service.

Connectors look up tenant-encrypted credentials, retry behind a circuit breaker,
store the raw answer as evidence and meter every call in ``connector_call``.
"""

from xolotl.platform.enums import (
    CallOutcome,
    CheckType,
    CircuitState,
    ConnectorMode,
    ConnectorRoute,
)

__all__ = [
    "CallOutcome",
    "CheckType",
    "CircuitState",
    "ConnectorMode",
    "ConnectorRoute",
]
