"""A purged party leaves no name behind in screening (SP-03 spec §10.1)."""

import copy
import json
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from tests.helpers import everything_stored, leaked, staff_session, system_ctx
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.review.support import assign_itf
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyStatus, PartyType
from xolotl.modules.party.events import PARTY_PURGED
from xolotl.modules.party.identifiers import rfc_check_digit
from xolotl.modules.party.retention import RetentionSweeper
from xolotl.modules.party.schemas import (
    IdentifierInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import Decision, MatchStatus
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import (
    PartyNameIndex,
    ScreeningHit,
    ScreeningMatch,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.rescreen.party_events import SCREENING_CONSUMER
from xolotl.modules.screening.rescreen.purge import redact_features
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import OutboxStatus, Role
from xolotl.platform.events import OutboxEventBus
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.storage.local import LocalFsObjectStore

GOLDEN = Path(__file__).parents[1] / "matching" / "goldens" / "explanations.json"
OFAC = "OFAC_SDN"
# A fictional person whose RFC is listed under another name: the hit comes from the
# identifier rule, and the surnames appear in the hit's query side only.
SURNAME = "zacatlan"
RFC_BODY = "ZATX800101AB"
RFC = RFC_BODY + rfc_check_digit(RFC_BODY)
XOCHITL = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Xóchitl",
        first_surname="Zacatlán",
        second_surname="Tepozteco",
    ),
    identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value=RFC)],
)
LISTED = ListEntry(
    list_code=OFAC,
    entry_key="9001",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("XOCHITL RAMIREZ ORTEGA", NameQuality.PRIMARY),
    identifiers=(EntryIdentifier(IdType.RFC, RFC, RFC),),
    source_ref="sdn 9001",
)
RETAINED_UNTIL = date(2027, 1, 1)
SWEEP_DAY = date(2030, 1, 1)


# ---- the pure redaction -----------------------------------------------------------


@cache
def _golden() -> dict[str, dict[str, JsonValue]]:
    return {
        name: case["explanation"]
        for name, case in json.loads(GOLDEN.read_text()).items()
    }


def _stored(explanation: dict[str, JsonValue]) -> dict[str, JsonValue]:
    features = copy.deepcopy(explanation)
    features.update({
        "v": 1,
        "list_kind": "SANCTION",
        "thresholds": {"alert": "0.70", "review": "0.55"},
        "matcher_version": "m1",
        "config_hash": "ab12",
        "entry_ref": {
            "list_code": OFAC,
            "entry_key": "9001",
            "list_entry_id": "0190f1e2-0000-7000-8000-000000000001",
            "list_version_seq": 3,
        },
    })
    return features


def test_redact_features_blanks_the_query_side_only():
    before = _stored(_golden()["identifier_floor"])
    kept = copy.deepcopy(before)

    after = redact_features(before)

    assert before == kept
    assert after == {
        "query_name": "",
        "entry_name": "joaquin archivaldo guzman loera",
        "entry_name_quality": "PRIMARY",
        "name_score": 0.6795,
        "alignment": [
            {
                "query": "",
                "entry": "joaquin",
                "kind": "INITIAL",
                "similarity": 0.8,
                "weight": 0.7,
                "role": "GIVEN",
            },
            {
                "query": "",
                "entry": "loera",
                "kind": "EXACT",
                "similarity": 1.0,
                "weight": 1.0,
                "role": "SURNAME",
            },
        ],
        "unmatched_query": [],
        "unmatched_entry": ["archivaldo", "guzman"],
        "qualifiers": [
            {
                "attribute": "IDENTIFIER",
                "outcome": "MATCH",
                "adjustment": 0.2705,
                "detail": "CURP ****RQ02 is listed",
            }
        ],
        "rules": [{"code": "ID_MATCH_FLOOR", "detail": "CURP ****RQ02 is listed"}],
        "v": 1,
        "list_kind": "SANCTION",
        "thresholds": {"alert": "0.70", "review": "0.55"},
        "matcher_version": "m1",
        "config_hash": "ab12",
        "entry_ref": before["entry_ref"],
        "redacted": True,
    }


@pytest.mark.parametrize("case", sorted(_golden()))
def test_redaction_is_idempotent(case: str):
    once = redact_features(_stored(_golden()[case]))

    assert redact_features(once) == once
    assert once["redacted"] is True
    assert not once["query_name"]
    assert once["unmatched_query"] == []
    alignment = once["alignment"]
    assert isinstance(alignment, list)
    assert all(isinstance(a, dict) and not a["query"] for a in alignment)


# ---- the purge subscriber -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Wired:
    bus: OutboxEventBus
    runner: JobRunner
    store: LocalFsObjectStore


@pytest.fixture
def wired(
    tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path
) -> Iterator[Wired]:
    store = LocalFsObjectStore(tmp_path / "objects")
    seed_sources(admin_engine)
    _ = load_shared(OFAC, [LISTED], store=store)
    assign_itf(tenant, admin_engine)
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=queue
    )
    yield Wired(
        OutboxEventBus(factory), JobRunner(queue, factory, worker_id="w-test"), store
    )
    reset_subscribers()
    set_index_holder(None)


@contextmanager
def _staff(tenant: CreatedTenant) -> Generator[Session]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,)) as s:
        yield s


def _dispatch(tenant: CreatedTenant, wired: Wired) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while wired.bus.dispatch_pending():
        pass


def _create(tenant: CreatedTenant, wired: Wired) -> UUID:
    with _staff(tenant) as s:
        party = service.create_party(s, XOCHITL)
        party.retention_until = RETAINED_UNTIL
        party_id = party.id
    _dispatch(tenant, wired)
    return party_id


def _purge(tenant: CreatedTenant, wired: Wired) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert RetentionSweeper(wired.store).sweep(s, today=SWEEP_DAY) == 1
    _dispatch(tenant, wired)


def _index_rows(tenant: CreatedTenant, party_id: UUID) -> int:
    with _staff(tenant) as s:
        count = s.scalar(
            select(func.count())
            .select_from(PartyNameIndex)
            .where(PartyNameIndex.party_id == party_id)
        )
        return count or 0


def _hits(tenant: CreatedTenant) -> list[ScreeningHit]:
    with _staff(tenant) as s:
        return list(s.scalars(select(ScreeningHit).order_by(ScreeningHit.id)))


def _hit_versions(tenant: CreatedTenant) -> list[str]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return [
            str(v)
            for v in s.scalars(text("SELECT xmin::text FROM screening_hit ORDER BY id"))
        ]


def _purged_event_status(tenant: CreatedTenant) -> list[OutboxStatus]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(select(OutboxRow.status).where(OutboxRow.type == PARTY_PURGED))
        )


def _record(hit: ScreeningHit) -> tuple[object, ...]:
    return (
        hit.id,
        hit.score,
        hit.band,
        hit.list_code,
        hit.source_entry_key,
        hit.list_entry_id,
        hit.match_id,
        hit.features["entry_ref"],
        hit.features["entry_name"],
        hit.features["rules"],
        hit.features["qualifiers"],
    )


def test_a_purged_partys_tokens_are_gone_and_its_record_stays(
    tenant: CreatedTenant, wired: Wired, admin_engine: Engine
):
    party_id = _create(tenant, wired)
    assert wired.runner.run_once() == 1
    (hit,) = _hits(tenant)
    assert hit.match_id is not None
    assert leaked(json.dumps(hit.features), SURNAME.encode())
    with _staff(tenant) as s:
        disposition_id = disposition_match(
            s,
            hit.match_id,
            decision=Decision.POSSIBLE_TRUE_MATCH,
            rationale="Mismo RFC que el listado; se escala.",
        ).id
    assert _index_rows(tenant, party_id) > 0

    _purge(tenant, wired)

    assert _index_rows(tenant, party_id) == 0
    (after,) = _hits(tenant)
    assert after.features["redacted"] is True
    assert not after.features["query_name"]
    assert _record(after) == _record(hit)
    assert _purged_event_status(tenant) == [OutboxStatus.PUBLISHED]
    with _staff(tenant) as s:
        assert service.get_party(s, party_id).status is PartyStatus.PURGED
        match = s.get_one(ScreeningMatch, (tenant.tenant_id, hit.match_id))
        assert match.status is MatchStatus.ESCALATED
        assert (match.score, match.band) == (hit.score, hit.band)
        dispositions = list(
            s.scalars(
                select(MatchDisposition.id).where(MatchDisposition.match_id == match.id)
            )
        )
        assert dispositions == [disposition_id]
    assert leaked(everything_stored(admin_engine), SURNAME.encode()) == []


def test_redelivering_the_event_changes_nothing(tenant: CreatedTenant, wired: Wired):
    party_id = _create(tenant, wired)
    assert wired.runner.run_once() == 1
    _purge(tenant, wired)
    versions = _hit_versions(tenant)
    features = [h.features for h in _hits(tenant)]

    replayed = wired.bus.replay(
        since=clock.now() - timedelta(hours=1),
        types=[PARTY_PURGED],
        consumer=SCREENING_CONSUMER,
        tenant_id=tenant.tenant_id,
    )

    assert replayed == 1
    assert _hit_versions(tenant) == versions
    assert [h.features for h in _hits(tenant)] == features
    assert _index_rows(tenant, party_id) == 0


def test_a_party_never_screened_is_purged_without_error(
    tenant: CreatedTenant, wired: Wired
):
    party_id = _create(tenant, wired)
    assert _index_rows(tenant, party_id) > 0

    _purge(tenant, wired)

    assert _index_rows(tenant, party_id) == 0
    assert _hits(tenant) == []
    assert _purged_event_status(tenant) == [OutboxStatus.PUBLISHED]
