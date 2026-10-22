"""The staff review routes and the run view on a counterparty's finding (SP-04 4c, C1).

C1 adds the minimal counterparty branch of the match queue, the match detail and the
reveal, so a counterparty's finding never breaks a page; C3 extends it. An API client
never learns a counterparty was screened (tipping-off, SP-04 spec §15).
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import audit_events
from tests.modules.screening.counterparty.conftest import (
    VALERIAN_XBT,
    Screened,
    job_session,
    resolve_one,
    runs_of,
)
from tests.modules.screening.route_support import MATCHES, SCREENING, api_client
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
)
from xolotl.modules.screening.engine.models import ScreeningMatch
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII

ALARCON = CounterpartyInput(
    kind=CounterpartyKind.PERSON, name="Rodrigo Alarcón Palomarez", country="MX"
)
WALLET = CounterpartyInput(wallet_address=VALERIAN_XBT, wallet_network="XBT")


def _matches(tenant: CreatedTenant) -> list[ScreeningMatch]:
    with job_session(tenant) as s:
        return list(s.scalars(select(ScreeningMatch).order_by(ScreeningMatch.id)))


def test_the_review_pages_show_a_counterparty_finding_masked(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    screened: Screened,
):
    _ = resolve_one(tenant, ALARCON)
    _ = resolve_one(tenant, WALLET)
    assert screened.runner.run_once() == 1
    matches = _matches(tenant)
    assert matches

    page = client.get(MATCHES, headers=admin_headers)

    assert page.status_code == 200, page.text
    subjects = [m["subject"] for m in page.json()["items"]]
    assert {s["kind"] for s in subjects} == {"COUNTERPARTY"}
    assert {s["initials"] for s in subjects} == {"R.A.P.", "WALLET"}
    assert "Alarcón" not in page.text
    assert VALERIAN_XBT not in page.text
    for match in matches:
        detail = client.get(f"{MATCHES}/{match.id}", headers=admin_headers)
        assert detail.status_code == 200, detail.text
        # The listed entry shows the address OFAC publishes; the subject never.
        subject = detail.json()["subject"]
        assert subject["party_id"] is None
        assert VALERIAN_XBT not in str(subject)
    wallet = next(m for m in subjects if m["initials"] == "WALLET")
    assert wallet["identifiers"] == ["DIGITAL_CURRENCY_ADDRESS ****mcfd"]
    assert not [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]

    for match in matches:
        revealed = client.get(f"{MATCHES}/{match.id}/reveal", headers=admin_headers)
        assert revealed.status_code == 200, revealed.text
    views = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert {(e.entity_type, e.entity_id) for e in views} == {
        ("screening_subject", str(m.subject_id)) for m in matches
    }
    assert all(e.detail == {"group": "subject"} for e in views)


def test_an_api_client_never_sees_a_counterparty_run(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    screened: Screened,
):
    _ = resolve_one(tenant, ALARCON)
    assert screened.runner.run_once() == 1
    (run,) = runs_of(tenant)
    machine = api_client(tenant, "screening:read")

    hidden = client.get(f"{SCREENING}/runs/{run.id}", headers=machine)
    shown = client.get(f"{SCREENING}/runs/{run.id}", headers=admin_headers)

    assert (hidden.status_code, hidden.json()["code"]) == (404, "NOT_FOUND")
    assert shown.status_code == 200, shown.text
    assert shown.json()["mode"] == "COUNTERPARTY"


def test_the_queue_shows_a_counterparty_finding_with_its_counterparty_id(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers: dict[str, str],
    screened: Screened,
):
    wallet = resolve_one(tenant, WALLET)
    assert screened.runner.run_once() == 1

    page = client.get(MATCHES, headers=admin_headers)

    assert page.status_code == 200, page.text
    items = page.json()["items"]
    assert items
    for item in items:
        assert item["party_id"] is None
        assert item["counterparty_id"] == str(wallet.id)
        assert item["subject"]["party_id"] is None
        assert item["subject"]["counterparty_id"] == str(wallet.id)
        assert item["subject"]["initials"] == "WALLET"
        assert item["subject"]["identifiers"] == ["DIGITAL_CURRENCY_ADDRESS ****mcfd"]
    detail = client.get(f"{MATCHES}/{items[0]['id']}", headers=admin_headers)
    assert detail.json()["subject"]["counterparty_id"] == str(wallet.id)
    assert VALERIAN_XBT not in page.text
