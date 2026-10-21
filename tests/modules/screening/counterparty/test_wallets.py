"""Crypto-wallet exact matching (SP-04 spec §11.4, FR-SC-015).

An OFAC digital-currency address matches a wallet counterparty exactly, whole and
case-sensitively, with no name at all (plan 4c Deviation 1: the library on ``main``
scores an identifier-only query at ``ID_MATCH_FLOOR``).
"""

import pytest
from sqlalchemy import Engine, select

from tests.helpers import everything_stored, leaked
from tests.modules.screening.counterparty.conftest import (
    ALARCON_ETH,
    VALERIAN_XBT,
    Screened,
    job_session,
    resolve_one,
    row_of,
    runs_of,
)
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.engine.enums import HitBand
from xolotl.modules.screening.engine.models import ScreeningHit
from xolotl.modules.tenants.service import CreatedTenant


def _hits(tenant: CreatedTenant) -> list[ScreeningHit]:
    with job_session(tenant) as s:
        return list(
            s.scalars(
                select(ScreeningHit).order_by(
                    ScreeningHit.score.desc(), ScreeningHit.id
                )
            )
        )


def _wallet(address: str) -> CounterpartyInput:
    return CounterpartyInput(wallet_address=address, wallet_network="XBT")


def test_a_wallet_only_counterparty_alerts_on_an_exact_ofac_address(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _wallet(VALERIAN_XBT))

    assert screened.runner.run_once() == 1

    # Screened as a person and as an organization; both find the one entry.
    assert len(runs_of(tenant)) == 2
    hits = _hits(tenant)
    assert {h.source_entry_key for h in hits} == {"45314"}
    assert {h.band for h in hits} == {HitBand.ALERT}
    features = hits[0].features
    assert isinstance(features, dict)
    assert features["rules"] == [
        {
            "code": "ID_MATCH_FLOOR",
            "detail": "DIGITAL_CURRENCY_ADDRESS ****mcfd is listed",
        }
    ]
    assert not features["query_name"]
    assert VALERIAN_XBT not in str(features)
    assert row_of(tenant, ref.id).screening_status is (
        CounterpartyScreeningStatus.PENDING_REVIEW
    )


def _spaced(address: str) -> str:
    return f"  {address[:8]} {address[8:20]}\t{address[20:]} "


def test_a_wallet_sent_with_whitespace_alerts_on_the_listed_address(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _wallet(_spaced(VALERIAN_XBT)))

    assert screened.runner.run_once() == 1

    hits = _hits(tenant)
    assert {h.source_entry_key for h in hits} == {"45314"}
    assert {h.band for h in hits} == {HitBand.ALERT}
    assert row_of(tenant, ref.id).screening_status is (
        CounterpartyScreeningStatus.PENDING_REVIEW
    )


@pytest.mark.parametrize(
    "address", [VALERIAN_XBT.upper(), VALERIAN_XBT[:-1] + "e"], ids=["upper", "one"]
)
def test_a_spaced_wallet_still_matches_only_with_its_case_and_every_character(
    tenant: CreatedTenant, screened: Screened, address: str
):
    ref = resolve_one(tenant, _wallet(_spaced(address)))

    assert screened.runner.run_once() == 1

    assert _hits(tenant) == []
    assert row_of(tenant, ref.id).screening_status is CounterpartyScreeningStatus.CLEAR


def test_a_one_character_difference_does_not_match(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _wallet(VALERIAN_XBT[:-1] + "e"))

    assert screened.runner.run_once() == 1

    assert _hits(tenant) == []
    assert row_of(tenant, ref.id).screening_status is CounterpartyScreeningStatus.CLEAR


def test_a_lower_cased_address_does_not_match(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _wallet(ALARCON_ETH.lower()))

    assert screened.runner.run_once() == 1

    assert _hits(tenant) == []
    assert row_of(tenant, ref.id).screening_status is CounterpartyScreeningStatus.CLEAR


def test_a_wallet_counterparty_with_a_name_is_matched_by_both(
    tenant: CreatedTenant, screened: Screened
):
    _ = resolve_one(
        tenant,
        CounterpartyInput(
            kind=CounterpartyKind.PERSON,
            name="Rodrigo Alarcón Palomares",
            wallet_address=ALARCON_ETH,
            wallet_network="ETH",
        ),
    )

    assert screened.runner.run_once() == 1

    (run,) = runs_of(tenant)
    first = _hits(tenant)[0]
    assert first.run_id == run.id
    assert first.source_entry_key == "57118"
    assert first.band is HitBand.ALERT
    features = first.features
    assert isinstance(features, dict)
    rules = features["rules"]
    assert isinstance(rules, list)
    assert {
        "code": "ID_MATCH_FLOOR",
        "detail": "DIGITAL_CURRENCY_ADDRESS ****CE22 is listed",
    } in rules
    assert features["query_name"] == "rodrigo alarcon palomares"
    assert features["name_score"] == pytest.approx(1.0)


def test_no_plaintext_address_is_stored_outside_the_ciphertext(
    tenant: CreatedTenant, screened: Screened, admin_engine: Engine
):
    # An address no list publishes, so only what the tenant sent could carry it.
    address = "bc1qxolotl0prueba0de0cifrado0sin0listar9"
    ref = resolve_one(tenant, _wallet(address))
    assert screened.runner.run_once() == 1

    assert row_of(tenant, ref.id).screened_at is not None
    assert leaked(everything_stored(admin_engine), address.encode()) == []
