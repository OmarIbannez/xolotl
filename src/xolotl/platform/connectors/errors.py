"""Problem-detail errors of the connector SDK."""

from typing import ClassVar

from xolotl.platform.errors import DomainError


class NoRouteError(DomainError):
    default_code: ClassVar[str] = "CONNECTOR_NO_ROUTE"
    default_detail: ClassVar[str] = (
        "no connector routes this check type for this country"
    )
