"""Builders for the review tests: staff users, policies and hand-made findings.

The review tests do not need ``screen()``: a finding is built with the models, with a
private entry of its own (``MX_LPB`` unless a test names an ``INTERNAL_*`` list), keyed
uniquely.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.helpers import staff_session
from tests.platform.policy_support import install_both_packs
from xolotl.modules.party import service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.catalog.models import PrivateList, PrivateListEntryRow
from xolotl.modules.screening.engine.enums import (
    HitBand,
    MatchStatus,
    RunMode,
    SubjectStatus,
    SuggestedDisposition,
    TriggerType,
)
from xolotl.modules.screening.engine.models import (
    ScreeningListSet,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.tenants.models import StaffUser
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.crypto.encrypted import encrypt_json
from xolotl.platform.enums import Role, StaffStatus
from xolotl.platform.policy import PolicySnapshot, assign_pack

ANA = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(given_names="Ana María", first_surname="Pérez"),
)


def assign_itf(tenant: CreatedTenant, admin_engine: Engine) -> None:
    """Assign the ITF pack to every product of the tenant from 2026-01-01."""
    version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = assign_pack(
            s,
            pack_version_id=version_id,
            product=None,
            effective_from=date(2026, 1, 1),
            approved_by=tenant.admin_user_id,
        )


def add_staff(tenant: CreatedTenant, *roles: Role) -> UUID:
    """Add an active staff user with ``roles`` to the tenant.

    Returns:
        The user's id.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        user = StaffUser(
            tenant_id=tenant.tenant_id,
            email=f"{uuid4().hex[:12]}@acme.mx",
            display_name="Revisor",
            roles=sorted(role.value for role in roles),
            status=StaffStatus.ACTIVE,
        )
        s.add(user)
        s.flush()
        return user.id


def _kind(list_code: str) -> ListKind:
    return ListKind.BLOCKED if list_code == "MX_LPB" else ListKind.INTERNAL


def _entry(s: Session, tenant_id: UUID, list_code: str) -> PrivateListEntryRow:
    parent = s.scalar(select(PrivateList).where(PrivateList.list_code == list_code))
    if parent is None:
        parent = PrivateList(
            tenant_id=tenant_id,
            list_code=list_code,
            list_kind=_kind(list_code),
            title="Lista de personas bloqueadas",
            created_by="staff:test",
        )
        s.add(parent)
        s.flush()
    entry = PrivateListEntryRow(
        tenant_id=tenant_id,
        list_id=parent.id,
        source_entry_key=f"clave {uuid4().hex}",
        content_tag=uuid4().bytes * 2,
        entity_type="PERSON",
        entry=encrypt_json({"k": "v"}, tenant_id=tenant_id, session=s),
        from_seq=1,
    )
    s.add(entry)
    s.flush()
    return entry


def add_finding(
    s: Session,
    subject: ScreeningSubject,
    *,
    status: MatchStatus = MatchStatus.OPEN,
    suggested: SuggestedDisposition | None = None,
    list_code: str = "MX_LPB",
) -> ScreeningMatch:
    """Add a finding of a new private entry of ``list_code``, from a run of its own.

    Returns:
        The finding, flushed.

    """
    tenant_id = subject.tenant_id
    list_set = ScreeningListSet(tenant_id=tenant_id, content_hash=uuid4().bytes * 2)
    snapshot = PolicySnapshot(
        tenant_id=tenant_id, content_hash=uuid4().bytes * 2, content={"on": "x"}
    )
    s.add_all([list_set, snapshot])
    s.flush()
    run = ScreeningRun(
        tenant_id=tenant_id,
        subject_id=subject.id,
        mode=RunMode.AD_HOC,
        trigger_type=TriggerType.CONSOLE,
        list_set_id=list_set.id,
        policy_snapshot_id=snapshot.id,
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
    s.add(run)
    s.flush()
    entry = _entry(s, tenant_id, list_code)
    match = ScreeningMatch(
        tenant_id=tenant_id,
        subject_id=subject.id,
        list_code=list_code,
        list_kind=_kind(list_code),
        private_list_entry_id=entry.id,
        source_entry_key=entry.source_entry_key,
        entry_content_hash=entry.content_tag,
        band=HitBand.ALERT,
        score=Decimal("0.9500"),
        origin_run_id=run.id,
        status=status,
        suggested_disposition=suggested,
    )
    s.add(match)
    s.flush()
    return match


def party_finding(
    tenant: CreatedTenant,
    *,
    status: MatchStatus = MatchStatus.OPEN,
    suggested: SuggestedDisposition | None = None,
) -> tuple[UUID, UUID]:
    """Create a party with one finding and the subject's status refreshed.

    Returns:
        ``(subject id, match id)``.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        subject = subject_for_party(s, party.id)
        match = add_finding(s, subject, status=status, suggested=suggested)
        subject.status = SubjectStatus.PENDING_REVIEW
        return subject.id, match.id
