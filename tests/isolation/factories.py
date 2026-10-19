"""One row factory per tenant table for the behavioural isolation harness.

Each factory inserts exactly one row of its table for ``tenant_id`` (creating
any parent rows it needs) and flushes. Later modules append their factories
here when they add tables, and list them in ``register_all``.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.modules.tenants.models import ApiKey, StaffCredential, StaffUser
from xolotl.platform import clock
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.enums import ApiKeyStatus, StaffStatus, TenantEnv
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.testing import register_isolation_factory


def _staff_user(session: Session, tenant_id: UUID) -> StaffUser:
    user = StaffUser(
        tenant_id=tenant_id,
        email=f"{uuid7().hex[:8]}@test.local",
        display_name="Test User",
        roles=["ANALYST"],
        status=StaffStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    return user


def staff_user(session: Session, tenant_id: UUID) -> None:
    _ = _staff_user(session, tenant_id)


def staff_credential(session: Session, tenant_id: UUID) -> None:
    user = _staff_user(session, tenant_id)
    session.add(
        StaffCredential(tenant_id=tenant_id, staff_user_id=user.id, password_hash="x")
    )
    session.flush()


def api_key(session: Session, tenant_id: UUID) -> None:
    user = _staff_user(session, tenant_id)
    session.add(
        ApiKey(
            tenant_id=tenant_id,
            name="harness",
            key_prefix=f"xk_sbx_{uuid7().hex[:8]}",
            key_hash=b"\x00" * 32,
            scopes=["tenants:read"],
            env=TenantEnv.SANDBOX,
            status=ApiKeyStatus.ACTIVE,
            created_by=user.id,
        )
    )
    session.flush()


def idempotency_key(session: Session, tenant_id: UUID) -> None:
    session.add(
        IdempotencyKey(
            tenant_id=tenant_id,
            actor_id="harness",
            key="k1",
            request_hash=b"\x00" * 32,
            status_code=200,
            response_body={"ok": True},
            expires_at=clock.now(),
        )
    )
    session.flush()


def audit_event(session: Session, tenant_id: UUID) -> None:
    session.add(
        AuditEventRow(
            tenant_id=tenant_id,
            at=clock.now(),
            id=uuid7(),
            seq=1,
            actor_type="SYSTEM",
            actor_id="harness",
            action="harness.row",
            prev_hash=b"\x00" * 32,
            hash=b"\x01" * 32,
        )
    )
    session.flush()


def register_all() -> None:
    """Register every factory above; calling it again is harmless."""
    register_isolation_factory("staff_user", staff_user)
    register_isolation_factory("staff_credential", staff_credential)
    register_isolation_factory("api_key", api_key)
    register_isolation_factory("idempotency_key", idempotency_key)
    register_isolation_factory("audit_event", audit_event)
