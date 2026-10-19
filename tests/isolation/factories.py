"""One row factory per tenant table for the behavioural isolation harness.

Each factory inserts exactly one row of its table for ``tenant_id`` (creating
any parent rows it needs) and flushes. Later modules append their factories
here when they add tables, and list them in ``register_all``.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.modules.tenants.models import (
    ApiKey,
    StaffCredential,
    StaffUser,
    TenantBranch,
    TenantRegulatoryProfile,
)
from xolotl.platform import clock
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.crypto.encrypted import encrypt, encrypt_json
from xolotl.platform.crypto.models import TenantKey
from xolotl.platform.crypto.provider import get_key_provider
from xolotl.platform.db import get_admin_engine
from xolotl.platform.enums import (
    ApiKeyStatus,
    DeliveryStatus,
    EndpointStatus,
    KeyPurpose,
    RunStatus,
    StaffStatus,
    StepStatus,
    TenantEnv,
)
from xolotl.platform.events.models import (
    EventConsumption,
    Notification,
    OutboxRow,
    WebhookDelivery,
    WebhookEndpoint,
)
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow, JobSchedule
from xolotl.platform.policy.models import (
    PolicyPack,
    PolicyPackVersion,
    PolicySnapshot,
    TenantOverrideVersion,
    TenantPackAssignment,
)
from xolotl.platform.tenancy.testing import register_isolation_factory
from xolotl.platform.workflows.models import (
    WorkflowRunRow,
    WorkflowSignalRow,
    WorkflowStepRow,
)


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
    # A real ciphertext, so the harness replays what the column really holds. The
    # tenant has no keys yet (the harness makes bare tenants), so it gets them here.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    session.add(
        StaffCredential(
            tenant_id=tenant_id,
            staff_user_id=user.id,
            password_hash="x",
            totp_secret=encrypt(
                "JBSWY3DPEHPK3PXP", tenant_id=tenant_id, session=session
            ),
        )
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
    # A real ciphertext, as for staff_credential: the column holds nothing else.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    session.add(
        IdempotencyKey(
            tenant_id=tenant_id,
            actor_id="harness",
            key="k1",
            request_hash=b"\x00" * 32,
            status_code=200,
            response_body=encrypt_json(
                {"ok": True}, tenant_id=tenant_id, session=session
            ),
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


def outbox(session: Session, tenant_id: UUID) -> None:
    session.add(
        OutboxRow(
            tenant_id=tenant_id,
            type="harness.row.v1",
            key="harness",
            payload={"data": {}},
        )
    )
    session.flush()


def event_consumption(session: Session, tenant_id: UUID) -> None:
    session.add(
        EventConsumption(tenant_id=tenant_id, consumer="harness", event_id=uuid7())
    )
    session.flush()


def _webhook_endpoint(session: Session, tenant_id: UUID) -> WebhookEndpoint:
    # A real ciphertext, as for the TOTP secret above: the tenant gets its keys here.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        url="https://hooks.test/harness",
        secret=encrypt("whsec_harness", tenant_id=tenant_id, session=session),
        event_types=["*"],
        status=EndpointStatus.ACTIVE,
        created_by=uuid7(),
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def webhook_endpoint(session: Session, tenant_id: UUID) -> None:
    _ = _webhook_endpoint(session, tenant_id)


def webhook_delivery(session: Session, tenant_id: UUID) -> None:
    endpoint = _webhook_endpoint(session, tenant_id)
    session.add(
        WebhookDelivery(
            tenant_id=tenant_id,
            endpoint_id=endpoint.id,
            event_id=uuid7(),
            status=DeliveryStatus.PENDING,
        )
    )
    session.flush()


def job(session: Session, tenant_id: UUID) -> None:
    session.add(JobRow(tenant_id=tenant_id, kind="harness.noop", payload={}))
    session.flush()


def job_schedule(session: Session, tenant_id: UUID) -> None:
    session.add(
        JobSchedule(
            tenant_id=tenant_id,
            name="harness",
            cron="0 * * * *",
            tz="UTC",
            kind="harness.noop",
            payload={},
            next_run_at=clock.now(),
        )
    )
    session.flush()


def _workflow_run(session: Session, tenant_id: UUID) -> WorkflowRunRow:
    run = WorkflowRunRow(
        tenant_id=tenant_id,
        definition="harness",
        business_key=uuid7().hex,
        status=RunStatus.RUNNING,
        state={},
    )
    session.add(run)
    session.flush()
    return run


def workflow_run(session: Session, tenant_id: UUID) -> None:
    _ = _workflow_run(session, tenant_id)


def workflow_step(session: Session, tenant_id: UUID) -> None:
    run = _workflow_run(session, tenant_id)
    session.add(
        WorkflowStepRow(
            tenant_id=tenant_id,
            run_id=run.id,
            step_id="collect",
            attempt=1,
            status=StepStatus.DONE,
        )
    )
    session.flush()


def workflow_signal(session: Session, tenant_id: UUID) -> None:
    run = _workflow_run(session, tenant_id)
    session.add(
        WorkflowSignalRow(
            tenant_id=tenant_id, run_id=run.id, name="approve", payload={}
        )
    )
    session.flush()


def notification(session: Session, tenant_id: UUID) -> None:
    session.add(
        Notification(
            tenant_id=tenant_id,
            recipient_role="ADMIN",
            template="harness.notice",
            data={},
        )
    )
    session.flush()


def tenant_key(session: Session, tenant_id: UUID) -> None:
    session.add(
        TenantKey(
            tenant_id=tenant_id,
            purpose=KeyPurpose.DATA,
            version=1,
            wrapped_dek=b"\x00" * 60,
            kek_ref="local:v1",
        )
    )
    session.flush()


def _pack_version_id() -> UUID:
    """Insert a pack and one version of it through the owner engine.

    The application role may only read the shared pack tables, so the parent of an
    assignment is created by the owner engine, in its own committed transaction.

    Returns:
        The id of the new ``policy_pack_version`` row.

    """
    pack_id, version_id = uuid7(), uuid7()
    engine = get_admin_engine()
    try:
        with Session(engine) as owner, owner.begin():
            owner.add(
                PolicyPack(
                    id=pack_id,
                    code=f"MX/T-{pack_id.hex[:6]}",
                    country_code="MX",
                    sector="T",
                    licence=pack_id.hex[:6],
                    title="t",
                )
            )
            owner.flush()
            owner.add(
                PolicyPackVersion(
                    id=version_id,
                    pack_id=pack_id,
                    version=1,
                    effective_from=date(2026, 1, 1),
                    config={},
                    content_hash=b"\x00" * 32,
                    loaded_by="harness",
                    source_path="harness",
                )
            )
    finally:
        engine.dispose()
    return version_id


def tenant_pack_assignment(session: Session, tenant_id: UUID) -> None:
    pack_version_id = _pack_version_id()
    user = _staff_user(session, tenant_id)
    session.add(
        TenantPackAssignment(
            tenant_id=tenant_id,
            pack_version_id=pack_version_id,
            effective_from=date(2026, 1, 1),
            approved_by=user.id,
        )
    )
    session.flush()


def tenant_override_version(session: Session, tenant_id: UUID) -> None:
    user = _staff_user(session, tenant_id)
    session.add(
        TenantOverrideVersion(
            tenant_id=tenant_id,
            version=1,
            overrides={},
            effective_from=date(2026, 1, 1),
            approved_by=user.id,
        )
    )
    session.flush()


def policy_snapshot(session: Session, tenant_id: UUID) -> None:
    session.add(
        PolicySnapshot(
            tenant_id=tenant_id,
            content_hash=b"\x01" * 32,
            content={"on": "2026-01-01"},
        )
    )
    session.flush()


def tenant_regulatory_profile(session: Session, tenant_id: UUID) -> None:
    # A real ciphertext, as for staff_credential: the tenant gets its keys here.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    session.add(
        TenantRegulatoryProfile(
            tenant_id=tenant_id,
            rfc=encrypt("ABC010101AB1", tenant_id=tenant_id, session=session),
            legal_name="Acme SA de CV",
        )
    )
    session.flush()


def tenant_branch(session: Session, tenant_id: UUID) -> None:
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    session.add(
        TenantBranch(
            tenant_id=tenant_id,
            code="MAT",
            name="Matriz",
            address=encrypt("Av. Reforma 1", tenant_id=tenant_id, session=session),
            state_code="09",
            municipality_code="015",
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
    register_isolation_factory("outbox", outbox)
    register_isolation_factory("event_consumption", event_consumption)
    register_isolation_factory("webhook_endpoint", webhook_endpoint)
    register_isolation_factory("webhook_delivery", webhook_delivery)
    register_isolation_factory("job", job)
    register_isolation_factory("job_schedule", job_schedule)
    register_isolation_factory("workflow_run", workflow_run)
    register_isolation_factory("workflow_step", workflow_step)
    register_isolation_factory("workflow_signal", workflow_signal)
    register_isolation_factory("notification", notification)
    register_isolation_factory("tenant_key", tenant_key)
    register_isolation_factory("tenant_pack_assignment", tenant_pack_assignment)
    register_isolation_factory("tenant_override_version", tenant_override_version)
    register_isolation_factory("policy_snapshot", policy_snapshot)
    register_isolation_factory("tenant_regulatory_profile", tenant_regulatory_profile)
    register_isolation_factory("tenant_branch", tenant_branch)
