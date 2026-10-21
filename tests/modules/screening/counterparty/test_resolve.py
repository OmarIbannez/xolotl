"""Counterparty resolution: one row per blind-indexed counterparty (spec §11.1)."""

import pytest
from sqlalchemy import Engine, func, select

from tests.helpers import everything_stored, new_tenant, staff_session
from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.resolve import (
    CounterpartyUnindexableError,
    dedup_hash_of,
    resolve,
    status_by_dedup_hash,
)
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.tenants.service import CreatedTenant


def _count(tenant: CreatedTenant) -> int | None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return s.scalar(select(func.count()).select_from(Counterparty))


def test_the_same_counterparty_resolves_to_one_row_with_a_growing_count(
    tenant: CreatedTenant,
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        first = resolve(
            s, CounterpartyInput(kind=CounterpartyKind.PERSON, name="Juan Pérez")
        )
        second = resolve(
            s, CounterpartyInput(kind=CounterpartyKind.PERSON, name="juan perez ")
        )
        row = s.get_one(Counterparty, (tenant.tenant_id, first.id))
        assert row.txn_count == 2
        assert row.name is not None
        assert row.name.reveal(session=s) == "Juan Pérez"
    assert first.created
    assert not second.created
    assert second.id == first.id
    assert second.dedup_hash == first.dedup_hash
    assert first.screening_status is CounterpartyScreeningStatus.UNSCREENED


def test_components_change_the_dedup_hash(tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ids = {
            resolve(s, CounterpartyInput(name="Comercial Uno")).id,
            resolve(
                s, CounterpartyInput(name="Comercial Uno", account="012180001234567")
            ).id,
            resolve(s, CounterpartyInput(account="012180001234567")).id,
        }
    assert len(ids) == 3
    assert _count(tenant) == 3


def test_a_wallet_hash_is_case_sensitive(tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        upper = resolve(s, CounterpartyInput(wallet_address="0xAbC"))
        lower = resolve(s, CounterpartyInput(wallet_address="0xabc"))
    assert upper.id != lower.id


def test_identifiers_are_normalized_with_the_party_rules(tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        spaced = resolve(
            s,
            CounterpartyInput(
                identifier=CounterpartyIdentifier(
                    id_type=IdentifierType.RFC, value=" goma-800101 h7a "
                )
            ),
        )
        plain = resolve(
            s,
            CounterpartyInput(
                identifier=CounterpartyIdentifier(
                    id_type=IdentifierType.RFC, value="GOMA800101H7A"
                )
            ),
        )
    assert spaced.id == plain.id
    assert _count(tenant) == 1


def test_no_plaintext_is_stored(tenant: CreatedTenant, admin_engine: Engine):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = resolve(
            s,
            CounterpartyInput(
                name="Zacarías Quintanilla",
                account="646180157000000004",
                wallet_address="bc1qplaintextprobe",
            ),
        )
    stored = everything_stored(admin_engine)
    for secret in ("Zacarías", "Quintanilla", "646180157000000004", "plaintextprobe"):
        assert secret not in stored


def test_status_by_dedup_hash_is_unscreened_for_an_unknown_hash(
    tenant: CreatedTenant,
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        unknown = dedup_hash_of(s, CounterpartyInput(name="Nadie Conocido"))
        assert (
            status_by_dedup_hash(s, unknown) is CounterpartyScreeningStatus.UNSCREENED
        )
        known = resolve(s, CounterpartyInput(name="Alguien Conocido"))
        row = s.get_one(Counterparty, (tenant.tenant_id, known.id))
        row.screening_status = CounterpartyScreeningStatus.CLEAR
        s.flush()
        assert (
            status_by_dedup_hash(s, known.dedup_hash)
            is CounterpartyScreeningStatus.CLEAR
        )
    assert _count(tenant) == 1


def test_a_counterparty_with_nothing_to_index_is_refused(tenant: CreatedTenant):
    with (
        pytest.raises(CounterpartyUnindexableError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = resolve(s, CounterpartyInput(name="!!!"))


def test_another_tenant_never_sees_the_row(engine: Engine, tenant: CreatedTenant):
    other = new_tenant("otra", "admin@otra.mx")
    probe = CounterpartyInput(name="Juan Pérez")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        mine = resolve(s, probe)
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        assert s.scalar(select(func.count()).select_from(Counterparty)) == 0
        assert (
            status_by_dedup_hash(s, mine.dedup_hash)
            is CounterpartyScreeningStatus.UNSCREENED
        )
        theirs = resolve(s, probe)
    assert theirs.created
    assert theirs.dedup_hash != mine.dedup_hash
