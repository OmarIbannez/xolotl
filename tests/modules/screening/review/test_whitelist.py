"""The whitelist: MLRO-only revocation, audited once, reactivated by a new clearance.

Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error rolls the session back.
"""

from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from tests.helpers import audit_events, staff_session, system_ctx
from tests.modules.screening.review.support import (
    add_finding,
    add_staff,
    assign_itf,
    party_finding,
)
from xolotl.modules.screening.engine.enums import Decision, MatchStatus
from xolotl.modules.screening.engine.models import ScreeningMatch, ScreeningSubject
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.screening.review.models import ScreeningWhitelist
from xolotl.modules.screening.review.whitelist import (
    active_whitelist_keys,
    revoke_whitelist,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.errors import ForbiddenError, NotFoundError
from xolotl.platform.ids import uuid7

RATIONALE = "Homonimia: CURP y fecha de nacimiento distintas"


@pytest.fixture
def itf(tenant: CreatedTenant, admin_engine: Engine) -> None:
    assign_itf(tenant, admin_engine)


def _clear(tenant: CreatedTenant, user_id: UUID, match_id: UUID) -> UUID:
    with staff_session(tenant.tenant_id, user_id, roles=(Role.ANALYST,)) as s:
        return disposition_match(
            s, match_id, decision=Decision.FALSE_POSITIVE, rationale=RATIONALE
        ).id


def _whitelist(tenant: CreatedTenant) -> ScreeningWhitelist:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalars(select(ScreeningWhitelist)).one()


def _revocations(tenant: CreatedTenant) -> list[str | None]:
    return [
        e.entity_id
        for e in audit_events(tenant.tenant_id)
        if e.action == "screening.whitelist_revoked"
    ]


def test_revoking_is_mlro_only_audited_and_idempotent(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    analyst = add_staff(tenant, Role.ANALYST)
    mlro = add_staff(tenant, Role.MLRO)
    subject_id, match_id = party_finding(tenant)
    _ = _clear(tenant, analyst, match_id)
    row = _whitelist(tenant)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        match = s.get_one(ScreeningMatch, (tenant.tenant_id, match_id))
        key = (match.list_code, match.source_entry_key, match.entry_content_hash)
        assert active_whitelist_keys(s, subject_id) == {key}

    for role in (Role.ANALYST, Role.SENIOR_ANALYST, Role.ADMIN, Role.AUDITOR_RO):
        with (
            pytest.raises(ForbiddenError) as caught,
            staff_session(tenant.tenant_id, analyst, roles=(role,)) as s,
        ):
            _ = revoke_whitelist(s, row.id)
        assert caught.value.code == "ROLE_FORBIDDEN"
    with (
        pytest.raises(NotFoundError),
        staff_session(tenant.tenant_id, mlro, roles=(Role.MLRO,)) as s,
    ):
        _ = revoke_whitelist(s, uuid7())

    with staff_session(tenant.tenant_id, mlro, roles=(Role.MLRO,)) as s:
        revoked = revoke_whitelist(s, row.id)
        assert active_whitelist_keys(s, subject_id) == set()
    assert revoked.revoked_by == mlro
    assert revoked.revoked_at is not None
    with staff_session(tenant.tenant_id, mlro, roles=(Role.MLRO,)) as s:
        again = revoke_whitelist(s, row.id)
    assert again.revoked_at == revoked.revoked_at
    assert _revocations(tenant) == [str(row.id)]
    (audited,) = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "screening.whitelist_revoked"
    ]
    assert (audited.entity_type, audited.detail) == (
        "screening_whitelist",
        {"subject_id": str(subject_id)},
    )


def test_a_false_positive_after_a_revocation_reactivates_the_row(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    analyst = add_staff(tenant, Role.ANALYST)
    mlro = add_staff(tenant, Role.MLRO)
    subject_id, match_id = party_finding(tenant)
    _ = _clear(tenant, analyst, match_id)
    row = _whitelist(tenant)
    with staff_session(tenant.tenant_id, mlro, roles=(Role.MLRO,)) as s:
        _ = revoke_whitelist(s, row.id)
        # A later run reopens the finding on the same content (Deviation 8).
        s.get_one(
            ScreeningMatch, (tenant.tenant_id, match_id)
        ).status = MatchStatus.OPEN

    second = _clear(tenant, analyst, match_id)
    reactivated = _whitelist(tenant)
    assert reactivated.id == row.id
    assert (
        reactivated.disposition_id,
        reactivated.revoked_at,
        reactivated.revoked_by,
    ) == (second, None, None)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        count = s.scalar(select(func.count()).select_from(ScreeningWhitelist))
        assert len(active_whitelist_keys(s, subject_id)) == 1
    assert count == 1


def test_active_keys_are_per_subject(engine: Engine, tenant: CreatedTenant, itf: None):
    analyst = add_staff(tenant, Role.ANALYST)
    subject_id, match_id = party_finding(tenant)
    other_subject, _ = party_finding(tenant)
    _ = _clear(tenant, analyst, match_id)
    with staff_session(tenant.tenant_id, analyst) as s:
        subject = s.get_one(ScreeningSubject, (tenant.tenant_id, subject_id))
        _ = add_finding(s, subject)
        assert len(active_whitelist_keys(s, subject_id)) == 1
        assert active_whitelist_keys(s, other_subject) == set()
