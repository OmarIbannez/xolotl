"""The resolved caller of a request."""

from collections.abc import Callable
from dataclasses import dataclass, field

from xolotl.platform.db import SessionFactory
from xolotl.platform.enums import ActorType
from xolotl.platform.tenancy.context import TenantContext


@dataclass(frozen=True, slots=True)
class Actor:
    context: TenantContext
    scopes: frozenset[str] = field(default_factory=frozenset)
    must_change_password: bool = False

    @property
    def is_staff(self) -> bool:
        return self.context.actor_type is ActorType.STAFF

    @property
    def is_api_client(self) -> bool:
        return self.context.actor_type is ActorType.API_CLIENT


# (session_factory, bearer token) -> Actor; raises UnauthorizedError or ForbiddenError.
ActorResolver = Callable[[SessionFactory, str], Actor]
