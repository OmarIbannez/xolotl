"""The masked summary and the audited reveal of a counterparty (SP-04 plan 4a, A10).

The summary says only which components a counterparty has: masking a value would
need its plaintext. The reveal records one ``VIEW_PII`` row per value it shows
before it decrypts any of them.
"""

from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.helpers import audit_events, new_tenant, staff_session
from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.resolve import resolve
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.counterparty.schemas import (
    CounterpartySummary,
    RevealedCounterparty,
)
from xolotl.modules.screening.counterparty.views import (
    counterparty_summary,
    reveal_counterparty,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.crypto.encrypted import Encrypted
from xolotl.platform.crypto.errors import KeyDestroyedError
from xolotl.platform.errors import NotFoundError

WALLET = "bc1qexampleexampleexampleexample0000000"
FULL = CounterpartyInput(
    kind=CounterpartyKind.PERSON,
    name="Juan Pérez",
    account="012180001234567897",
    institution="BBVA",
    country="MX",
    wallet_address=WALLET,
    wallet_network="XBT",
    identifier=CounterpartyIdentifier(
        id_type=IdentifierType.RFC, value="PEJJ800101AAA"
    ),
)
NAME_ONLY = CounterpartyInput(kind=CounterpartyKind.PERSON, name="Ana Ruiz")


def _resolve(tenant: CreatedTenant, counterparty: CounterpartyInput) -> UUID:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return resolve(s, counterparty).id


def _view_pii(tenant: CreatedTenant) -> list[AuditEventRow]:
    return [row for row in audit_events(tenant.tenant_id) if row.action == VIEW_PII]


def _refuse_decryption(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: Encrypted[object], *, session: Session | None = None) -> object:
        _ = (self, session)
        msg = "a summary must not decrypt"
        raise AssertionError(msg)

    monkeypatch.setattr(Encrypted, "reveal", refuse)


def test_the_summary_never_decrypts(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    full = _resolve(tenant, FULL)
    named = _resolve(tenant, NAME_ONLY)
    _refuse_decryption(monkeypatch)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        summary = counterparty_summary(s, full)
        name_only = counterparty_summary(s, named)
    assert summary == CounterpartySummary(
        id=full,
        kind=CounterpartyKind.PERSON,
        has_name=True,
        has_account=True,
        has_wallet=True,
        has_identifier=True,
        institution="BBVA",
        country="MX",
        wallet_network="XBT",
        screening_status=CounterpartyScreeningStatus.UNSCREENED,
        txn_count=1,
    )
    assert (
        name_only.has_name,
        name_only.has_account,
        name_only.has_wallet,
        name_only.has_identifier,
    ) == (True, False, False, False)
    # Neither a value nor a fragment of one is in the summary.
    dumped = summary.model_dump_json()
    for value in ("Juan", "Pérez", "1234567897", WALLET, "PEJJ800101AAA"):
        assert value not in dumped
    assert not _view_pii(tenant)


def test_reveal_audits_each_field_then_decrypts(tenant: CreatedTenant):
    full = _resolve(tenant, FULL)
    named = _resolve(tenant, NAME_ONLY)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        revealed = reveal_counterparty(s, full)
        name_only = reveal_counterparty(s, named)
    assert revealed == RevealedCounterparty(
        name="Juan Pérez",
        account="012180001234567897",
        wallet_address=WALLET,
        wallet_network="XBT",
        identifier_type="RFC",
        identifier="PEJJ800101AAA",
    )
    assert name_only == RevealedCounterparty(
        name="Ana Ruiz",
        account=None,
        wallet_address=None,
        wallet_network=None,
        identifier_type=None,
        identifier=None,
    )
    rows = _view_pii(tenant)
    assert [(row.entity_type, row.entity_id, row.detail) for row in rows] == [
        ("counterparty", str(full), {"field": "name"}),
        ("counterparty", str(full), {"field": "account"}),
        ("counterparty", str(full), {"field": "wallet"}),
        ("counterparty", str(full), {"field": "identifier"}),
        ("counterparty", str(named), {"field": "name"}),
    ]


def test_the_audit_rows_precede_the_key_read_and_roll_back_with_it(
    tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    full = _resolve(tenant, FULL)
    recorded_before: list[int] = []

    def failing(self: Encrypted[object], *, session: Session | None = None) -> object:
        assert session is not None
        recorded_before.append(
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.action == VIEW_PII)
            )
            or 0
        )
        raise KeyDestroyedError

    monkeypatch.setattr(Encrypted, "reveal", failing)
    with (
        pytest.raises(KeyDestroyedError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = reveal_counterparty(s, full)
    # Every field was audited before the first key read, and nothing survived it.
    assert recorded_before == [4]
    assert not _view_pii(tenant)


def test_another_tenants_counterparty_is_not_found(tenant: CreatedTenant):
    other = new_tenant("globex", "admin@globex.mx")
    theirs = _resolve(other, FULL)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        with pytest.raises(NotFoundError):
            _ = counterparty_summary(s, theirs)
        with pytest.raises(NotFoundError):
            _ = reveal_counterparty(s, theirs)
    assert not _view_pii(tenant)
    assert not _view_pii(other)
