"""The credential vault: tenant-encrypted connector credentials (spec §8)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text

from tests.helpers import (
    audit_events,
    everything_stored,
    leaked,
    new_tenant,
    staff_session,
    system_ctx,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.connectors import ConnectorMode, CredentialVault
from xolotl.platform.connectors.models import ConnectorCredential
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import ForbiddenError, ValidationFailedError

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

vault = CredentialVault()


def _credential_events(tenant_id: UUID) -> list[AuditEventRow]:
    return [
        e for e in audit_events(tenant_id) if e.action == "connector.credential_set"
    ]


def test_set_then_get_round_trips_and_an_unset_pair_is_empty(tenant: CreatedTenant):
    t, u = tenant.tenant_id, tenant.admin_user_id
    with staff_session(t, u) as s:
        row = vault.set(
            s, "sat_rfc", ConnectorMode.LIVE, {"token": "tok-1", "user": "ana"}
        )
        assert row.rotated_at is None
        assert row.created_by == u
    with tenant_session(system_ctx(t)) as s:
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.LIVE)) == {
            "token": "tok-1",
            "user": "ana",
        }
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.SANDBOX)) == {}
        assert dict(vault.get(s, "banxico_sie", ConnectorMode.LIVE)) == {}


def test_the_secret_is_stored_as_ciphertext_and_the_audit_names_fields_only(
    tenant: CreatedTenant, admin_engine: Engine
):
    t, u = tenant.tenant_id, tenant.admin_user_id
    with staff_session(t, u) as s:
        _ = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "tok-secret-1"})
    with admin_engine.connect() as conn:
        raw = conn.scalar(
            text("SELECT secret FROM connector_credential WHERE tenant_id = :t"),
            {"t": t},
        )
    assert raw is not None
    assert b"tok-secret-1" not in bytes(raw)
    assert leaked(everything_stored(admin_engine), b"tok-secret-1") == []
    assert [(e.action, e.detail) for e in _credential_events(t)] == [
        (
            "connector.credential_set",
            {
                "connector": "sat_rfc",
                "mode": "LIVE",
                "field_names": ["token"],
                "rotated": False,
            },
        ),
    ]


def test_setting_again_replaces_the_fields_and_stamps_rotated_at(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    t, u = tenant.tenant_id, tenant.admin_user_id
    with staff_session(t, u) as s:
        first = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "old"})
        first_id = first.id
    monkeypatch.setattr(clock, "now", lambda: T0)
    with staff_session(t, u) as s:
        row = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "new"})
        assert row.id == first_id
        assert row.rotated_at == T0
    with tenant_session(system_ctx(t)) as s:
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.LIVE)) == {"token": "new"}
        count = s.scalar(select(func.count()).select_from(ConnectorCredential))
    assert count == 1
    events = _credential_events(t)
    assert [e.detail["rotated"] for e in events if e.detail is not None] == [
        False,
        True,
    ]
    assert {e.entity_id for e in events} == {str(first_id)}


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({}, "at least one"),
        ({"Bad Key": "x"}, "field name"),
        ({"9token": "x"}, "field name"),
        ({"a" * 65: "x"}, "field name"),
        ({"token": ""}, "empty"),
        ({"token": "x" * 4097}, "4096"),
    ],
)
def test_invalid_fields_are_a_422(
    tenant: CreatedTenant, fields: dict[str, str], match: str
):
    with (
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
        pytest.raises(ValidationFailedError, match=match),
    ):
        _ = vault.set(s, "sat_rfc", ConnectorMode.LIVE, fields)


def test_the_longest_field_name_and_value_are_accepted(tenant: CreatedTenant):
    t, u = tenant.tenant_id, tenant.admin_user_id
    fields = {"a" * 64: "x" * 4096}
    with staff_session(t, u) as s:
        _ = vault.set(s, "sat_rfc", ConnectorMode.SANDBOX, fields)
    with tenant_session(system_ctx(t)) as s:
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.SANDBOX)) == fields


def test_only_staff_may_set(tenant: CreatedTenant):
    with (
        tenant_session(system_ctx(tenant.tenant_id)) as s,
        pytest.raises(ForbiddenError),
    ):
        _ = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "x"})
    assert _credential_events(tenant.tenant_id) == []


def test_another_tenant_sees_nothing(tenant: CreatedTenant):
    other = new_tenant("beta", "admin@beta.mx")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "mine"})
    with tenant_session(system_ctx(other.tenant_id)) as s:
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.LIVE)) == {}
