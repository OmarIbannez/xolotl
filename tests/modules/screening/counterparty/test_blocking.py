"""Reverse blocking of counterparties near a list entry (SP-04 spec §11.3).

The party blocking queries run over ``counterparty_name_index``, with XOL-107's
stop-key rule counted over the counterparties' own rows, and the entry's wallet
addresses and identifiers find counterparties by their hashes.
"""

from functools import cache
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.helpers import new_tenant
from tests.modules.screening.counterparty.conftest import VALERIAN_XBT, job_session
from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty import blocking
from xolotl.modules.screening.counterparty.blocking import (
    BlockedCounterparty,
    reverse_counterparty_candidates,
)
from xolotl.modules.screening.counterparty.models import CounterpartyNameIndex
from xolotl.modules.screening.counterparty.resolve import resolve
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
    CounterpartyKind,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.lists.ofac_xml import wallet_identifier
from xolotl.modules.screening.matching import (
    BlockingPlan,
    IdfTable,
    blocking_plan,
    name_keys,
    normalize_name,
)
from xolotl.modules.screening.matching.evaluation import (
    Corpus,
    load_corpus,
    load_floors,
)
from xolotl.modules.screening.rescreen import blocking as party_blocking
from xolotl.modules.screening.rescreen.blocking import blocking_query, stop_keys
from xolotl.modules.tenants.service import CreatedTenant

SCREENING_TESTS = Path(__file__).parents[1]
RFC = "SAGC800101AB8"
NAMESAKES = ("Ulises Beltrán Ochoa", "Ulises Cota Duarte", "Ulises Espino Fierro")


@cache
def _corpus() -> Corpus:
    floors = load_floors(SCREENING_TESTS / "corpus" / "floors.yaml")
    return load_corpus(SCREENING_TESTS, floors)


@cache
def _idf() -> IdfTable:
    return IdfTable.build(_corpus().entries)


def _add(
    s: Session,
    name: str | None = None,
    *,
    kind: CounterpartyKind = CounterpartyKind.PERSON,
    wallet: str | None = None,
    rfc: str | None = None,
) -> UUID:
    identifier = (
        None
        if rfc is None
        else CounterpartyIdentifier(id_type=IdentifierType.RFC, value=rfc)
    )
    return resolve(
        s,
        CounterpartyInput(
            kind=kind,
            name=name,
            wallet_address=wallet,
            wallet_network=None if wallet is None else "XBT",
            identifier=identifier,
        ),
    ).id


def _entry(
    full: str,
    *,
    entity_type: EntityType = EntityType.PERSON,
    given: str | None = None,
    surname: str | None = None,
    identifiers: tuple[EntryIdentifier, ...] = (),
) -> ListEntry:
    return ListEntry(
        list_code="OFAC_SDN",
        entry_key="E-1",
        entity_type=entity_type,
        primary_name=EntryName(
            full=full, quality=NameQuality.PRIMARY, given=given, surname=surname
        ),
        identifiers=identifiers,
    )


def _wallet(address: str) -> EntryIdentifier:
    return EntryIdentifier(
        id_type=IdType.DIGITAL_CURRENCY_ADDRESS,
        value=address,
        normalized=address,
        label="XBT",
    )


def _rfc(value: str) -> EntryIdentifier:
    return EntryIdentifier(id_type=IdType.RFC, value=value, normalized=value)


def _blocked(
    s: Session, entry: ListEntry, idf: IdfTable | None = None
) -> BlockedCounterparty:
    (found,) = reverse_counterparty_candidates(s, [entry], idf=idf or _idf())
    return found


def test_a_perturbed_entry_name_blocks_the_counterparty(
    engine: Engine, tenant: CreatedTenant
):
    with job_session(tenant) as s:
        near = _add(s, "Jose Martinez")
        other = _add(s, "Fernanda Ochoa Beltrán")
        found = _blocked(
            s,
            _entry(
                "José Martínez Hernández", given="José", surname="Martínez Hernández"
            ),
        )
    assert found == BlockedCounterparty("E-1", frozenset({near}), overflow=False)
    assert other not in found.counterparty_ids


def test_a_wallet_on_the_entry_finds_the_counterparty_without_a_name(
    engine: Engine, tenant: CreatedTenant
):
    with job_session(tenant) as s:
        wallet_only = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=VALERIAN_XBT)
        # One character off, and the same address lower-cased: neither matches.
        _ = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=VALERIAN_XBT[:-1] + "e")
        _ = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=VALERIAN_XBT.upper())
        found = _blocked(
            s,
            _entry(
                "Valerian Labs, Inc.",
                entity_type=EntityType.ORGANIZATION,
                identifiers=(_wallet(VALERIAN_XBT),),
            ),
        )
    assert found.counterparty_ids == {wallet_only}


def test_a_wallet_sent_with_whitespace_is_found_by_the_listed_address(
    engine: Engine, tenant: CreatedTenant
):
    spaced = f" {VALERIAN_XBT[:10]} {VALERIAN_XBT[10:]}\t"
    with job_session(tenant) as s:
        wallet_only = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=spaced)
        _ = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=f" {VALERIAN_XBT.upper()} ")
        _ = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=f" {VALERIAN_XBT[:-1]}e ")
        # The list side's own identifier, as the OFAC parser builds it.
        listed = wallet_identifier("XBT", VALERIAN_XBT)
        found = _blocked(s, _entry("Valerian Labs", identifiers=(listed,)))
    assert found.counterparty_ids == {wallet_only}


def test_an_rfc_on_the_entry_finds_the_counterparty(
    engine: Engine, tenant: CreatedTenant
):
    with job_session(tenant) as s:
        by_rfc = _add(s, "Carlos Sánchez Gil", rfc=RFC)
        found = _blocked(
            s,
            _entry(
                "Comercializadora Tlaloc",
                entity_type=EntityType.ORGANIZATION,
                identifiers=(_rfc(RFC),),
            ),
        )
        # A malformed list RFC is skipped; the names still block.
        malformed = _blocked(
            s, _entry("Carlos Sánchez Gil", identifiers=(_rfc("NOT-AN-RFC"),))
        )
    assert found.counterparty_ids == {by_rfc}
    assert malformed.counterparty_ids == {by_rfc}


def test_a_common_name_is_not_blocked(engine: Engine, tenant: CreatedTenant):
    negative = _corpus().hard_negatives[0]
    keys = name_keys(
        normalize_name(negative.names[0], entity_type=negative.entity_type)
    )
    kind = (
        CounterpartyKind.PERSON
        if negative.entity_type is EntityType.PERSON
        else CounterpartyKind.ORGANIZATION
    )
    with job_session(tenant) as s:
        counterparty_id = _add(s, negative.names[0], kind=kind)
        found = reverse_counterparty_candidates(s, _corpus().entries, idf=_idf())
        by_trigram = [
            entry.entry_key
            for entry in _corpus().entries
            if counterparty_id
            in s.scalars(
                blocking_query(
                    BlockingPlan(()),
                    normalize_name(
                        entry.primary_name.full, entity_type=entry.entity_type
                    ).folded,
                    limit=1,
                    table=CounterpartyNameIndex,
                )
            )
        ]
    blocked_on = {b.entry_key for b in found if counterparty_id in b.counterparty_ids}
    # Only through single keys the library files rare entry tokens under, as for a
    # party: no key set of two or more tokens and no trigram meets the name.
    covering = {
        entry.entry_key
        for entry in _corpus().entries
        for name in (entry.primary_name, *entry.aliases)
        for key_set in blocking_plan(
            normalize_name(name.full, entity_type=entry.entity_type), _idf()
        ).key_sets
        if set(key_set) <= keys
    }
    assert blocked_on == covering
    assert by_trigram == []


def test_stop_keys_apply_to_the_counterparty_index(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(party_blocking, "STOP_KEY_MIN_HOLDERS", 3)
    idf = IdfTable({"quintanilla": 0.3, "nava": 0.3})
    entry = _entry(
        "Ulises Quintanilla Nava", given="Ulises", surname="Quintanilla Nava"
    )
    (name,) = [normalize_name(entry.primary_name.full, entity_type=EntityType.PERSON)]
    plan = blocking_plan(name, idf)
    singles = BlockingPlan(tuple(k for k in plan.key_sets if len(k) == 1))
    assert singles.key_sets
    with job_session(tenant) as s:
        target = _add(s, "Ulises Quintanilla Nava")
        namesakes = {_add(s, name) for name in NAMESAKES}
        _ = _add(s, "Graciela Montemayor")
        # Four of the five counterparty rows hold the given name's keys.
        by_single = set(
            s.scalars(
                blocking_query(singles, "", limit=10, table=CounterpartyNameIndex)
            )
        )
        dropped = stop_keys(s, [singles], table=CounterpartyNameIndex)
        # The party index is empty: the rule never counts the parties' rows.
        assert stop_keys(s, [singles]) == frozenset()
        found = _blocked(s, entry, idf)
    assert by_single == namesakes | {target}
    assert dropped == {keys[0] for keys in singles.key_sets}
    assert found == BlockedCounterparty("E-1", frozenset({target}), overflow=False)


def test_more_than_the_cap_marks_the_entry_overflow_and_keeps_the_wallet_match(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(blocking, "BLOCKING_CAP", 3)
    with job_session(tenant) as s:
        for second in ("Nava", "Nieto", "Olmos", "Pardo"):
            _ = _add(s, f"Ulises Quintanilla {second}")
        wallet_only = _add(s, kind=CounterpartyKind.UNKNOWN, wallet=VALERIAN_XBT)
        found = _blocked(
            s, _entry("Ulises Quintanilla", identifiers=(_wallet(VALERIAN_XBT),))
        )
    assert found == BlockedCounterparty("E-1", frozenset({wallet_only}), overflow=True)


def test_blocking_never_crosses_tenants(engine: Engine, tenant: CreatedTenant):
    other = new_tenant("beta", "admin@beta.mx")
    with job_session(other) as s:
        _ = _add(s, "Graciela Montemayor Ruvalcaba", rfc=RFC, wallet=VALERIAN_XBT)
    with job_session(tenant) as s:
        found = _blocked(
            s,
            _entry(
                "Graciela Montemayor Ruvalcaba",
                identifiers=(_rfc(RFC), _wallet(VALERIAN_XBT)),
            ),
        )
    assert found.counterparty_ids == frozenset()


def test_party_blocking_is_unchanged():
    plan = BlockingPlan((("ulises",),))
    party = str(blocking_query(plan, "ulises", limit=1))
    counterparty = str(
        blocking_query(plan, "ulises", limit=1, table=CounterpartyNameIndex)
    )
    assert "party_name_index.party_id" in party
    assert "counterparty" not in party
    assert "counterparty_name_index.counterparty_id" in counterparty
    assert "party_name_index" not in counterparty.replace("counterparty_name_index", "")
