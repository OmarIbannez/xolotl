"""A counterparty's identifier in its query (SP-04 spec §11.4).

Only an RFC, a CURP or a passport the party validator accepts reaches the matcher,
in its canonical form and with the counterparty's country; any other identifier is
left out, and an invalid one never fails the first-sight job.
"""

from sqlalchemy import select

from tests.modules.screening.counterparty.conftest import (
    Screened,
    job_session,
    resolve_one,
    row_of,
)
from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.queries import query_for_counterparty
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.engine.models import ScreeningHit
from xolotl.modules.screening.lists import EntityType, IdType
from xolotl.modules.screening.matching import QueryIdentifier
from xolotl.modules.tenants.service import CreatedTenant

# OFAC uid 11006, COPA DE PLATA S.A. DE C.V., as the fixture lists its R.F.C.
COPA_RFC = "CPL9103222F5"


def _identified(id_type: IdentifierType, value: str) -> CounterpartyInput:
    return CounterpartyInput(
        country="MX", identifier=CounterpartyIdentifier(id_type=id_type, value=value)
    )


def _query_identifiers(
    tenant: CreatedTenant, row: Counterparty
) -> tuple[QueryIdentifier, ...]:
    with job_session(tenant) as s:
        query = query_for_counterparty(s, row, entity_type=EntityType.ORGANIZATION)
        return query.identifiers


def test_a_valid_rfc_reaches_the_query_and_matches_its_listed_entry(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _identified(IdentifierType.RFC, COPA_RFC.lower()))

    assert _query_identifiers(tenant, row_of(tenant, ref.id)) == (
        QueryIdentifier(IdType.RFC, normalized=COPA_RFC, country="MX"),
    )
    assert screened.runner.run_once() == 1
    with job_session(tenant) as s:
        keys = set(s.scalars(select(ScreeningHit.source_entry_key)))
    assert "11006" in keys
    assert row_of(tenant, ref.id).screening_status is (
        CounterpartyScreeningStatus.PENDING_REVIEW
    )


def test_an_invalid_identifier_is_skipped_without_failing_the_job(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _identified(IdentifierType.PASSPORT, "12"))

    assert _query_identifiers(tenant, row_of(tenant, ref.id)) == ()
    assert screened.runner.run_once() == 1
    row = row_of(tenant, ref.id)
    assert row.screening_status is CounterpartyScreeningStatus.CLEAR
    assert row.screened_at is not None


def test_an_identifier_the_lists_do_not_publish_is_ignored(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, _identified(IdentifierType.CLABE, "002010077777777771"))

    assert _query_identifiers(tenant, row_of(tenant, ref.id)) == ()
    assert screened.runner.run_once() == 1
    assert row_of(tenant, ref.id).screening_status is (
        CounterpartyScreeningStatus.CLEAR
    )
