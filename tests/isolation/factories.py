"""One row factory per tenant table for the behavioural isolation harness.

Each factory inserts exactly one row of its table for ``tenant_id`` (creating
any parent rows it needs) and flushes. Later modules append their factories
here when they add tables, and list them in ``register_all``.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.models import (
    AggBucket,
    AggDeclaration,
    AggDistinct,
)
from xolotl.modules.monitoring.captures.enums import CaptureKind
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.decide.enums import (
    DecisionMode,
    DecisionOutcome,
    FailMode,
)
from xolotl.modules.monitoring.decide.models import RailSetting, TxnDecision
from xolotl.modules.monitoring.enums import (
    AccountKind,
    BatchSource,
    BucketKind,
    FileStatus,
    ParkReason,
)
from xolotl.modules.monitoring.profiles.models import BehaviourProfile
from xolotl.modules.monitoring.rules.enums import (
    HitTransactionRole,
    NoticeSource,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleAssignment,
    RuleHit,
    RuleHitTransaction,
    RuleSimulation,
    TierLimitNotice,
)
from xolotl.modules.monitoring.txn.models import (
    Account,
    Transaction,
    TransactionBatch,
    TransactionFile,
    TransactionParked,
    TransactionRef,
)
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    Instrument,
    KeyKind,
    Rail,
)
from xolotl.modules.party.enums import (
    AddressType,
    ContactKind,
    CustomerRelationshipStatus,
    DocumentType,
    IdentifierType,
    PartyType,
    RelationshipType,
    VerificationResult,
)
from xolotl.modules.party.models import (
    CustomerRelationship,
    Document,
    Organization,
    Party,
    PartyAddress,
    PartyContact,
    PartyIdentifier,
    PartyRelationship,
    Person,
    VerificationCheck,
)
from xolotl.modules.screening.catalog.models import (
    ListSource,
    PrivateList,
    PrivateListEntryDelta,
    PrivateListEntryRow,
    PrivateListVersion,
    TenantListSubscription,
)
from xolotl.modules.screening.counterparty.models import (
    Counterparty,
    CounterpartyNameIndex,
)
from xolotl.modules.screening.counterparty.schema import CounterpartyKind
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    DecidedByType,
    Decision,
    HitBand,
    NameKind,
    RunMode,
    SubjectKind,
    SubjectStatus,
    TriggerType,
)
from xolotl.modules.screening.engine.models import (
    PartyNameIndex,
    ScreeningBatch,
    ScreeningHit,
    ScreeningListSet,
    ScreeningListSetMember,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.enums import (
    DeltaChange,
    ListKind,
    ListRequirement,
    SourceFormat,
    VersionStatus,
)
from xolotl.modules.screening.review.models import (
    MatchDisposition,
    ScreeningWhitelist,
)
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
from xolotl.platform.connectors.models import ConnectorCall, ConnectorCredential
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.crypto.encrypted import encrypt, encrypt_json
from xolotl.platform.crypto.models import TenantKey
from xolotl.platform.crypto.provider import get_key_provider
from xolotl.platform.db import get_admin_engine
from xolotl.platform.enums import (
    ApiKeyStatus,
    CallOutcome,
    CheckType,
    ConnectorMode,
    ConnectorRoute,
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
        email=f"{uuid7().hex[-12:]}@test.local",
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


def _policy_snapshot(session: Session, tenant_id: UUID) -> PolicySnapshot:
    # One per tenant: the content hash is unique, and a disposition's snapshot may be
    # the one its match's run already created.
    existing = session.scalar(
        select(PolicySnapshot).where(PolicySnapshot.tenant_id == tenant_id)
    )
    if existing is not None:
        return existing
    row = PolicySnapshot(
        tenant_id=tenant_id,
        content_hash=b"\x01" * 32,
        content={"on": "2026-01-01"},
    )
    session.add(row)
    session.flush()
    return row


def policy_snapshot(session: Session, tenant_id: UUID) -> None:
    _ = _policy_snapshot(session, tenant_id)


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


def connector_credential(session: Session, tenant_id: UUID) -> None:
    # A real ciphertext, as staff_credential does; the harness makes bare tenants.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    session.add(
        ConnectorCredential(
            tenant_id=tenant_id,
            connector="sat_rfc",
            mode=ConnectorMode.SANDBOX,
            secret=encrypt_json({"token": "t"}, tenant_id=tenant_id, session=session),
            created_by=uuid7(),
        )
    )
    session.flush()


def connector_call(session: Session, tenant_id: UUID) -> None:
    session.add(
        ConnectorCall(
            tenant_id=tenant_id,
            connector="sat_rfc",
            check_type=CheckType.RFC_VALIDATION,
            mode=ConnectorMode.SANDBOX,
            route=ConnectorRoute.MOCK,
            outcome=CallOutcome.PASS,
            duration_ms=12,
        )
    )
    session.flush()


def _party(session: Session, tenant_id: UUID) -> Party:
    # The party tables hold real ciphertext, so the bare tenant gets its keys here.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    row = Party(tenant_id=tenant_id, party_type=PartyType.PERSON, country_code="MX")
    session.add(row)
    session.flush()
    return row


def party(session: Session, tenant_id: UUID) -> None:
    _ = _party(session, tenant_id)


def person(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    session.add(
        Person(
            tenant_id=tenant_id,
            id=row.id,
            given_names=encrypt("Ana", tenant_id=tenant_id, session=session),
            first_surname=encrypt("Pérez", tenant_id=tenant_id, session=session),
        )
    )
    session.flush()


def organization(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    session.add(
        Organization(
            tenant_id=tenant_id,
            id=row.id,
            legal_name=encrypt("Acme SA de CV", tenant_id=tenant_id, session=session),
        )
    )
    session.flush()


def verification_check(session: Session, tenant_id: UUID) -> None:
    session.add(
        VerificationCheck(
            tenant_id=tenant_id,
            check_type=CheckType.RFC_VALIDATION,
            provider="sat_rfc",
            mode=ConnectorMode.SANDBOX,
            result=VerificationResult.PASS,
        )
    )
    session.flush()


def party_identifier(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    rfc = "CACX7605101P8"
    session.add(
        PartyIdentifier(
            tenant_id=tenant_id,
            party_id=row.id,
            id_type=IdentifierType.RFC,
            country_code="MX",
            value=encrypt(rfc, tenant_id=tenant_id, session=session),
            value_hash=blind_index("rfc", rfc, tenant_id=tenant_id, session=session),
        )
    )
    session.flush()


def party_contact(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    email = "ana@acme.mx"
    session.add(
        PartyContact(
            tenant_id=tenant_id,
            party_id=row.id,
            kind=ContactKind.EMAIL,
            value=encrypt(email, tenant_id=tenant_id, session=session),
            value_hash=blind_index(
                "email", email, tenant_id=tenant_id, session=session
            ),
        )
    )
    session.flush()


def party_address(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    session.add(
        PartyAddress(
            tenant_id=tenant_id,
            party_id=row.id,
            address_type=AddressType.RESIDENTIAL,
            address=encrypt_json(
                {"street": "Reforma 1"}, tenant_id=tenant_id, session=session
            ),
            country_code="MX",
            valid_from=date(2026, 1, 1),
        )
    )
    session.flush()


def party_relationship(session: Session, tenant_id: UUID) -> None:
    owner, company = _party(session, tenant_id), _party(session, tenant_id)
    session.add(
        PartyRelationship(
            tenant_id=tenant_id,
            from_party_id=owner.id,
            to_party_id=company.id,
            rel_type=RelationshipType.SHAREHOLDER,
            ownership_pct=Decimal("51.0000"),
            valid_from=date(2026, 1, 1),
        )
    )
    session.flush()


def document(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    session.add(
        Document(
            tenant_id=tenant_id,
            party_id=row.id,
            doc_type=DocumentType.IDENTITY,
            storage_key=f"documents/{row.id}/{uuid7()}",
            sha256=bytes(32),
            size=1,
            content_type="application/pdf",
            uploaded_by="staff:test",
        )
    )
    session.flush()


def _customer_relationship(session: Session, tenant_id: UUID) -> CustomerRelationship:
    row = _party(session, tenant_id)
    relationship = CustomerRelationship(
        tenant_id=tenant_id,
        party_id=row.id,
        external_ref=f"ref-{uuid7().hex[-12:]}",
        status=CustomerRelationshipStatus.ACTIVE,
    )
    session.add(relationship)
    session.flush()
    return relationship


def customer_relationship(session: Session, tenant_id: UUID) -> None:
    _ = _customer_relationship(session, tenant_id)


def _list_source_code() -> str:
    """Insert the UN list source through the owner engine, unless it exists.

    The application role may not insert into the shared ``list_source``, so the
    parent of a subscription is created by the owner engine, in its own committed
    transaction, as ``_pack_version_id`` does.

    Returns:
        The source's code, ``UN_CONSOLIDATED``.

    """
    code = "UN_CONSOLIDATED"
    engine = get_admin_engine()
    try:
        with engine.begin() as conn:
            _ = conn.execute(
                insert(ListSource)
                .values(
                    code=code,
                    list_kind=ListKind.SANCTION,
                    title="UN Security Council Consolidated List",
                    jurisdiction="UN",
                    format=SourceFormat.UN_XML,
                    url="https://lists.test/un.xml",
                    poll_interval=timedelta(minutes=15),
                )
                .on_conflict_do_nothing()
            )
    finally:
        engine.dispose()
    return code


def tenant_list_subscription(session: Session, tenant_id: UUID) -> None:
    session.add(
        TenantListSubscription(
            tenant_id=tenant_id,
            list_code=_list_source_code(),
            requirement=ListRequirement.MANDATORY,
        )
    )
    session.flush()


def _private_list(session: Session, tenant_id: UUID) -> PrivateList:
    # The tenant's one MX_LPB list, created on first use, so that a version and an
    # entry built for one factory (the delta's) belong to the same list. The private
    # tables hold real ciphertext, so the bare tenant gets its keys with the list.
    existing = session.scalar(
        select(PrivateList).where(
            PrivateList.tenant_id == tenant_id, PrivateList.list_code == "MX_LPB"
        )
    )
    if existing is not None:
        return existing
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    row = PrivateList(
        tenant_id=tenant_id,
        list_code="MX_LPB",
        list_kind=ListKind.BLOCKED,
        title="Lista de personas bloqueadas",
        created_by="staff:test",
    )
    session.add(row)
    session.flush()
    return row


def private_list(session: Session, tenant_id: UUID) -> None:
    _ = _private_list(session, tenant_id)


def _private_list_version(session: Session, tenant_id: UUID) -> PrivateListVersion:
    parent = _private_list(session, tenant_id)
    version_id = uuid7()
    row = PrivateListVersion(
        tenant_id=tenant_id,
        id=version_id,
        list_id=parent.id,
        seq=1,
        status=VersionStatus.ACTIVE,
        uploaded_by="staff:test",
        file_sha256=b"\x00" * 32,
        file_size=1,
        content_type="text/csv",
        storage_key=f"private-lists/{parent.id}/{version_id}",
        entry_count=1,
        added=1,
        changed=0,
        removed=0,
    )
    session.add(row)
    session.flush()
    return row


def private_list_version(session: Session, tenant_id: UUID) -> None:
    _ = _private_list_version(session, tenant_id)


def _private_list_entry(session: Session, tenant_id: UUID) -> PrivateListEntryRow:
    parent = _private_list(session, tenant_id)
    row = PrivateListEntryRow(
        tenant_id=tenant_id,
        list_id=parent.id,
        source_entry_key=f"clave {uuid4().hex}",
        content_tag=b"\x00" * 32,
        entity_type="PERSON",
        entry=encrypt_json({"k": "v"}, tenant_id=tenant_id, session=session),
        from_seq=1,
    )
    session.add(row)
    session.flush()
    return row


def private_list_entry(session: Session, tenant_id: UUID) -> None:
    _ = _private_list_entry(session, tenant_id)


def private_list_entry_delta(session: Session, tenant_id: UUID) -> None:
    version = _private_list_version(session, tenant_id)
    entry = _private_list_entry(session, tenant_id)
    session.add(
        PrivateListEntryDelta(
            tenant_id=tenant_id,
            version_id=version.id,
            source_entry_key=entry.source_entry_key,
            change=DeltaChange.ADDED,
            new_entry_id=entry.id,
        )
    )
    session.flush()


def party_name_index(session: Session, tenant_id: UUID) -> None:
    row = _party(session, tenant_id)
    session.add(
        PartyNameIndex(
            tenant_id=tenant_id,
            party_id=row.id,
            name_kind=NameKind.LEGAL,
            norm_name="ana perez",
            tokens=["ana", "perez"],
            phonetic_keys=["AN", "PRS"],
            matcher_version="test/1",
        )
    )
    session.flush()


def _screening_subject(session: Session, tenant_id: UUID) -> ScreeningSubject:
    row = ScreeningSubject(
        tenant_id=tenant_id,
        kind=SubjectKind.PARTY,
        party_id=_party(session, tenant_id).id,
    )
    session.add(row)
    session.flush()
    return row


def screening_subject(session: Session, tenant_id: UUID) -> None:
    _ = _screening_subject(session, tenant_id)


def _screening_list_set(session: Session, tenant_id: UUID) -> ScreeningListSet:
    row = ScreeningListSet(tenant_id=tenant_id, content_hash=uuid7().bytes * 2)
    session.add(row)
    session.flush()
    return row


def screening_list_set(session: Session, tenant_id: UUID) -> None:
    _ = _screening_list_set(session, tenant_id)


def screening_list_set_member(session: Session, tenant_id: UUID) -> None:
    list_set = _screening_list_set(session, tenant_id)
    version = _private_list_version(session, tenant_id)
    session.add(
        ScreeningListSetMember(
            tenant_id=tenant_id,
            list_set_id=list_set.id,
            list_code="MX_LPB",
            private_list_version_id=version.id,
        )
    )
    session.flush()


def _screening_run(session: Session, tenant_id: UUID) -> ScreeningRun:
    row = ScreeningRun(
        tenant_id=tenant_id,
        subject_id=_screening_subject(session, tenant_id).id,
        mode=RunMode.AD_HOC,
        trigger_type=TriggerType.CONSOLE,
        list_set_id=_screening_list_set(session, tenant_id).id,
        policy_snapshot_id=_policy_snapshot(session, tenant_id).id,
        matcher_version="test/1",
        scoring_config_hash=b"\x00" * 32,
        query_fingerprint=b"\x00" * 32,
        candidate_count=1,
        hit_count=1,
        new_match_count=1,
        outcome=SubjectStatus.PENDING_REVIEW,
        latency_ms=1,
        created_by="staff:test",
    )
    session.add(row)
    session.flush()
    return row


def screening_run(session: Session, tenant_id: UUID) -> None:
    _ = _screening_run(session, tenant_id)


def _screening_match(session: Session, tenant_id: UUID) -> ScreeningMatch:
    # A private entry: the app role cannot insert the list_source a shared one needs.
    run = _screening_run(session, tenant_id)
    entry = _private_list_entry(session, tenant_id)
    row = ScreeningMatch(
        tenant_id=tenant_id,
        subject_id=run.subject_id,
        list_code="MX_LPB",
        list_kind=ListKind.BLOCKED,
        private_list_entry_id=entry.id,
        source_entry_key=entry.source_entry_key,
        entry_content_hash=entry.content_tag,
        band=HitBand.ALERT,
        score=Decimal("0.9500"),
        origin_run_id=run.id,
    )
    session.add(row)
    session.flush()
    return row


def screening_match(session: Session, tenant_id: UUID) -> None:
    _ = _screening_match(session, tenant_id)


def screening_hit(session: Session, tenant_id: UUID) -> None:
    run = _screening_run(session, tenant_id)
    entry = _private_list_entry(session, tenant_id)
    session.add(
        ScreeningHit(
            tenant_id=tenant_id,
            run_id=run.id,
            list_code="MX_LPB",
            private_list_entry_id=entry.id,
            source_entry_key=entry.source_entry_key,
            entry_content_hash=entry.content_tag,
            score=Decimal("0.9500"),
            band=HitBand.ALERT,
            features={"score": 0.95},
        )
    )
    session.flush()


def _match_disposition(session: Session, tenant_id: UUID) -> MatchDisposition:
    match = _screening_match(session, tenant_id)
    row = MatchDisposition(
        tenant_id=tenant_id,
        match_id=match.id,
        decision=Decision.POSSIBLE_TRUE_MATCH,
        rationale=encrypt(
            "same birth date as the entry", tenant_id=tenant_id, session=session
        ),
        decided_by_type=DecidedByType.STAFF,
        decided_by=_staff_user(session, tenant_id).id,
        policy_snapshot_id=_policy_snapshot(session, tenant_id).id,
    )
    session.add(row)
    session.flush()
    return row


def match_disposition(session: Session, tenant_id: UUID) -> None:
    _ = _match_disposition(session, tenant_id)


def screening_whitelist(session: Session, tenant_id: UUID) -> None:
    disposition = _match_disposition(session, tenant_id)
    match = session.get_one(ScreeningMatch, (tenant_id, disposition.match_id))
    session.add(
        ScreeningWhitelist(
            tenant_id=tenant_id,
            subject_id=match.subject_id,
            list_code=match.list_code,
            source_entry_key=match.source_entry_key,
            entry_content_hash=match.entry_content_hash,
            disposition_id=disposition.id,
        )
    )
    session.flush()


def screening_batch(session: Session, tenant_id: UUID) -> None:
    session.add(
        ScreeningBatch(
            tenant_id=tenant_id,
            kind=BatchKind.PERIODIC,
            ref="2026-10",
            status=BatchStatus.RUNNING,
        )
    )
    session.flush()


def _account(session: Session, tenant_id: UUID) -> Account:
    relationship = _customer_relationship(session, tenant_id)
    row = Account(
        tenant_id=tenant_id,
        relationship_id=relationship.id,
        party_id=relationship.party_id,
        external_ref=f"default:{relationship.id}",
        kind=AccountKind.DEFAULT,
    )
    session.add(row)
    session.flush()
    return row


def account(session: Session, tenant_id: UUID) -> None:
    _ = _account(session, tenant_id)


def _counterparty(session: Session, tenant_id: UUID) -> Counterparty:
    # A real ciphertext, as for staff_credential: the tenant gets its keys here.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    row = Counterparty(
        tenant_id=tenant_id,
        kind=CounterpartyKind.UNKNOWN,
        name=encrypt("Probe", tenant_id=tenant_id, session=session),
        name_hash=b"\x01" * 32,
        dedup_hash=uuid7().bytes * 2,
    )
    session.add(row)
    session.flush()
    return row


def counterparty(session: Session, tenant_id: UUID) -> None:
    _ = _counterparty(session, tenant_id)


def _transaction_file(session: Session, tenant_id: UUID) -> TransactionFile:
    file_id = uuid7()
    row = TransactionFile(
        id=file_id,
        tenant_id=tenant_id,
        storage_key=f"transactions/files/{file_id}",
        sha256=file_id.bytes * 2,
        size=10,
        content_type="text/csv",
        status=FileStatus.UPLOADED,
        uploaded_by="harness",
    )
    session.add(row)
    session.flush()
    return row


def transaction_file(session: Session, tenant_id: UUID) -> None:
    _ = _transaction_file(session, tenant_id)


def _transaction_batch(session: Session, tenant_id: UUID) -> TransactionBatch:
    row = TransactionBatch(
        tenant_id=tenant_id,
        source=BatchSource.FILE,
        file_id=_transaction_file(session, tenant_id).id,
        submitted_by="cli:harness",
        item_count=1,
        accepted=1,
        duplicates=0,
    )
    session.add(row)
    session.flush()
    return row


def transaction_batch(session: Session, tenant_id: UUID) -> None:
    _ = _transaction_batch(session, tenant_id)


def _transaction(session: Session, tenant_id: UUID) -> Transaction:
    owner = _account(session, tenant_id)
    row = Transaction(
        tenant_id=tenant_id,
        occurred_at=clock.now(),
        id=uuid7(),
        external_id=f"ext-{uuid7().hex[:12]}",
        batch_id=_transaction_batch(session, tenant_id).id,
        party_id=owner.party_id,
        relationship_id=owner.relationship_id,
        account_id=owner.id,
        counterparty_id=_counterparty(session, tenant_id).id,
        policy_snapshot_id=_policy_snapshot(session, tenant_id).id,
        direction=Direction.OUT,
        rail=Rail.SPEI,
        instrument=Instrument.TRANSFER,
        channel=Channel.APP,
        amount_minor=10000,
        currency="MXN",
        content_hash=b"\x00" * 32,
        raw=encrypt_json({"k": "v"}, tenant_id=tenant_id, session=session),
    )
    session.add(row)
    session.flush()
    return row


def transaction(session: Session, tenant_id: UUID) -> None:
    _ = _transaction(session, tenant_id)


def transaction_ref(session: Session, tenant_id: UUID) -> None:
    row = _transaction(session, tenant_id)
    session.add(
        TransactionRef(
            tenant_id=tenant_id,
            external_id=row.external_id,
            transaction_id=row.id,
            occurred_at=row.occurred_at,
            content_hash=row.content_hash,
        )
    )
    session.flush()


def transaction_parked(session: Session, tenant_id: UUID) -> None:
    row = _transaction(session, tenant_id)
    session.add(
        TransactionParked(
            tenant_id=tenant_id,
            transaction_id=row.id,
            occurred_at=row.occurred_at,
            reason=ParkReason.UNIT_VALUE_MISSING,
            unit_code="UDI",
            needed_on=row.occurred_at.date(),
        )
    )
    session.flush()


AGG_ID = "0123456789abcdef"


def agg_bucket(session: Session, tenant_id: UUID) -> None:
    session.add(
        AggBucket(
            tenant_id=tenant_id,
            key_kind=KeyKind.PARTY,
            key=str(uuid7()),
            agg_id=AGG_ID,
            bucket_start=clock.now(),
            bucket_kind=BucketKind.DAY,
        )
    )
    session.flush()


def agg_distinct(session: Session, tenant_id: UUID) -> None:
    session.add(
        AggDistinct(
            tenant_id=tenant_id,
            key_kind=KeyKind.PARTY,
            key=str(uuid7()),
            agg_id=AGG_ID,
            bucket_start=clock.now(),
            member_hash=b"\x01" * 32,
        )
    )
    session.flush()


def agg_declaration(session: Session, tenant_id: UUID) -> None:
    session.add(
        AggDeclaration(
            tenant_id=tenant_id,
            agg_id=AGG_ID,
            declaration={"function": "SUM"},
            bucket_kind=BucketKind.DAY,
            window_kind="DURATION",
            window="P1D",
        )
    )
    session.flush()


def _monitoring_rule(session: Session, tenant_id: UUID) -> MonitoringRule:
    row = MonitoringRule(
        tenant_id=tenant_id,
        code=f"RULE_{uuid7().hex[-12:].upper()}",
        family="ALERT",
        kind="THRESHOLD",
        title="Test rule",
        created_by=_staff_user(session, tenant_id).id,
    )
    session.add(row)
    session.flush()
    return row


def monitoring_rule(session: Session, tenant_id: UUID) -> None:
    _ = _monitoring_rule(session, tenant_id)


def _rule_version(session: Session, tenant_id: UUID) -> MonitoringRuleVersion:
    # A DRAFT with no template version: the app role cannot insert a template row.
    rule = _monitoring_rule(session, tenant_id)
    row = MonitoringRuleVersion(
        tenant_id=tenant_id,
        rule_id=rule.id,
        version=1,
        definition={"code": rule.code},
        definition_hash=b"\x02" * 32,
        author_id=_staff_user(session, tenant_id).id,
    )
    session.add(row)
    session.flush()
    return row


def monitoring_rule_version(session: Session, tenant_id: UUID) -> None:
    _ = _rule_version(session, tenant_id)


def rule_simulation(session: Session, tenant_id: UUID) -> None:
    version = _rule_version(session, tenant_id)
    session.add(
        RuleSimulation(
            tenant_id=tenant_id,
            rule_version_id=version.id,
            requested_by=_staff_user(session, tenant_id).id,
            range_start=date(2026, 7, 1),
            range_end=date(2026, 9, 30),
            status=SimulationStatus.QUEUED,
            definition_hash=version.definition_hash,
        )
    )
    session.flush()


def _rule_assignment(session: Session, tenant_id: UUID) -> RuleAssignment:
    row = RuleAssignment(
        tenant_id=tenant_id,
        rule_version_id=_rule_version(session, tenant_id).id,
        effective_from=date(2026, 10, 1),
        approved_by=_staff_user(session, tenant_id).id,
    )
    session.add(row)
    session.flush()
    return row


def rule_assignment(session: Session, tenant_id: UUID) -> None:
    _ = _rule_assignment(session, tenant_id)


def _rule_hit(session: Session, tenant_id: UUID) -> RuleHit:
    assignment = _rule_assignment(session, tenant_id)
    trigger = _transaction(session, tenant_id)
    row = RuleHit(
        tenant_id=tenant_id,
        rule_version_id=assignment.rule_version_id,
        rule_code="CASH_STRUCTURING",
        key_kind=KeyKind.PARTY,
        key=str(trigger.party_id),
        dedup_key=f"PARTY:{trigger.party_id}:2026-10",
        party_id=trigger.party_id,
        relationship_id=trigger.relationship_id,
        severity="HIGH",
        window_start=trigger.occurred_at - timedelta(days=7),
        window_end=trigger.occurred_at,
        evidence={"v": 1},
        policy_snapshot_id=_policy_snapshot(session, tenant_id).id,
        definition_hash=b"\x02" * 32,
        assignment_id=assignment.id,
        first_transaction_id=trigger.id,
        first_occurred_at=trigger.occurred_at,
    )
    session.add(row)
    session.flush()
    return row


def rule_hit(session: Session, tenant_id: UUID) -> None:
    _ = _rule_hit(session, tenant_id)


def rule_hit_transaction(session: Session, tenant_id: UUID) -> None:
    hit = _rule_hit(session, tenant_id)
    member = _transaction(session, tenant_id)
    session.add(
        RuleHitTransaction(
            tenant_id=tenant_id,
            hit_id=hit.id,
            transaction_id=member.id,
            occurred_at=member.occurred_at,
            role=HitTransactionRole.WINDOW,
        )
    )
    session.flush()


def tier_limit_notice(session: Session, tenant_id: UUID) -> None:
    session.add(
        TierLimitNotice(
            tenant_id=tenant_id,
            relationship_id=_customer_relationship(session, tenant_id).id,
            limit_code="monthly_ops",
            period_key="2026-10",
            source=NoticeSource.PROCESS,
        )
    )
    session.flush()


def capture_group(session: Session, tenant_id: UUID) -> None:
    start = clock.now()
    session.add(
        CaptureGroup(
            tenant_id=tenant_id,
            capture_code="AVI_XVI_A",
            key_kind=KeyKind.RELATIONSHIP,
            key=str(uuid7()),
            period_key="2026-10",
            window_start=start,
            window_end=start + timedelta(days=180),
        )
    )
    session.flush()


def reportable_event(session: Session, tenant_id: UUID) -> None:
    row = _transaction(session, tenant_id)
    session.add(
        ReportableEvent(
            tenant_id=tenant_id,
            capture_code="ITF_ROR_CASH",
            report_code="MX_F41_ROR",
            capture_kind=CaptureKind.THRESHOLD,
            transaction_id=row.id,
            occurred_at=row.occurred_at,
            party_id=row.party_id,
            relationship_id=row.relationship_id,
            period_key="2026-Q4",
            fields={"tipo_operacion": "01"},
            unit_values={"threshold": {"amount": "7500", "unit": "USD"}},
            policy_snapshot_id=_policy_snapshot(session, tenant_id).id,
        )
    )
    session.flush()


def capture_period(session: Session, tenant_id: UUID) -> None:
    session.add(
        CapturePeriod(
            tenant_id=tenant_id,
            capture_code="ITF_ROR_CASH",
            report_code="MX_F41_ROR",
            period_key=f"Q-{uuid7().hex[-12:]}",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 12, 31),
        )
    )
    session.flush()


def counterparty_name_index(session: Session, tenant_id: UUID) -> None:
    session.add(
        CounterpartyNameIndex(
            tenant_id=tenant_id,
            counterparty_id=_counterparty(session, tenant_id).id,
            norm_name="probe sa",
            tokens=["probe", "sa"],
            phonetic_keys=["PRP", "S"],
            matcher_version="test/1",
        )
    )
    session.flush()


def rail_setting(session: Session, tenant_id: UUID) -> None:
    session.add(
        RailSetting(
            tenant_id=tenant_id,
            rail=Rail.SPEI,
            fail_mode=FailMode.FAIL_CLOSED,
            changed_by=_staff_user(session, tenant_id).id,
        )
    )
    session.flush()


def txn_decision(session: Session, tenant_id: UUID) -> None:
    owner = _account(session, tenant_id)
    session.add(
        TxnDecision(
            tenant_id=tenant_id,
            decided_at=clock.now(),
            external_id=f"ext-{uuid7().hex[:12]}",
            rail=Rail.SPEI,
            relationship_id=owner.relationship_id,
            account_id=owner.id,
            decision=DecisionOutcome.ALLOW,
            mode=DecisionMode.EVALUATED,
            reasons=[],
            ruleset_hash=b"\x00" * 32,
            policy_snapshot_id=_policy_snapshot(session, tenant_id).id,
            latency_ms=5,
            db_ms=1,
            actor="api_client:harness",
        )
    )
    session.flush()


def behaviour_profile(session: Session, tenant_id: UUID) -> None:
    relationship = _customer_relationship(session, tenant_id)
    session.add(
        BehaviourProfile(
            tenant_id=tenant_id,
            relationship_id=relationship.id,
            party_id=relationship.party_id,
            month="2026-09",
            metrics={"inflow_minor": "0", "currency": "MXN"},
        )
    )
    session.flush()


def _register_decide_and_profiles() -> None:
    # Plan 4c's tables (migration 0014).
    register_isolation_factory("rail_setting", rail_setting)
    register_isolation_factory("txn_decision", txn_decision)
    register_isolation_factory("behaviour_profile", behaviour_profile)


def _register_rules_and_captures() -> None:
    # The SP-04 rule and capture tables (plan 4b) and the counterparty name index.
    register_isolation_factory("monitoring_rule", monitoring_rule)
    register_isolation_factory("monitoring_rule_version", monitoring_rule_version)
    register_isolation_factory("rule_simulation", rule_simulation)
    register_isolation_factory("rule_assignment", rule_assignment)
    register_isolation_factory("rule_hit", rule_hit)
    register_isolation_factory("rule_hit_transaction", rule_hit_transaction)
    register_isolation_factory("tier_limit_notice", tier_limit_notice)
    register_isolation_factory("capture_group", capture_group)
    register_isolation_factory("reportable_event", reportable_event)
    register_isolation_factory("capture_period", capture_period)
    register_isolation_factory("counterparty_name_index", counterparty_name_index)
    _register_decide_and_profiles()


def _register_monitoring() -> None:
    # The SP-04 monitoring tables (plan 4a), apart to keep register_all short.
    register_isolation_factory("account", account)
    register_isolation_factory("counterparty", counterparty)
    register_isolation_factory("transaction_batch", transaction_batch)
    register_isolation_factory("transaction_file", transaction_file)
    register_isolation_factory("transaction", transaction)
    register_isolation_factory("transaction_ref", transaction_ref)
    register_isolation_factory("transaction_parked", transaction_parked)
    register_isolation_factory("agg_bucket", agg_bucket)
    register_isolation_factory("agg_distinct", agg_distinct)
    register_isolation_factory("agg_declaration", agg_declaration)
    # Plan 4b's tables; register_all stays under the statement limit.
    _register_rules_and_captures()


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
    register_isolation_factory("connector_credential", connector_credential)
    register_isolation_factory("connector_call", connector_call)
    register_isolation_factory("party", party)
    register_isolation_factory("person", person)
    register_isolation_factory("organization", organization)
    register_isolation_factory("verification_check", verification_check)
    register_isolation_factory("party_identifier", party_identifier)
    register_isolation_factory("party_contact", party_contact)
    register_isolation_factory("party_address", party_address)
    register_isolation_factory("party_relationship", party_relationship)
    register_isolation_factory("document", document)
    register_isolation_factory("customer_relationship", customer_relationship)
    register_isolation_factory("tenant_list_subscription", tenant_list_subscription)
    register_isolation_factory("private_list", private_list)
    register_isolation_factory("private_list_version", private_list_version)
    register_isolation_factory("private_list_entry", private_list_entry)
    register_isolation_factory("private_list_entry_delta", private_list_entry_delta)
    register_isolation_factory("party_name_index", party_name_index)
    register_isolation_factory("screening_subject", screening_subject)
    register_isolation_factory("screening_list_set", screening_list_set)
    register_isolation_factory("screening_list_set_member", screening_list_set_member)
    register_isolation_factory("screening_run", screening_run)
    register_isolation_factory("screening_match", screening_match)
    register_isolation_factory("screening_hit", screening_hit)
    register_isolation_factory("match_disposition", match_disposition)
    register_isolation_factory("screening_whitelist", screening_whitelist)
    register_isolation_factory("screening_batch", screening_batch)
    _register_monitoring()
