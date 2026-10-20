"""Migration 0010: the screening engine and review tables.

The rows are built with the isolation factories' helpers, inside the tenant's own
session, so every check below is the one the application role meets. The round trip
moves the session database back to 0009 and forward again with real commits; the
``database`` fixture always brings it back to head afterwards.
"""

from collections.abc import Callable, Generator
from decimal import Decimal
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from psycopg import errors as pg_errors
from sqlalchemy import Engine, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from tests.helpers import system_ctx
from tests.isolation import factories
from xolotl.modules.party.models import Party
from xolotl.modules.screening.catalog.models import PrivateListEntryRow
from xolotl.modules.screening.engine.enums import (
    DecidedByType,
    Decision,
    HitBand,
    SubjectKind,
)
from xolotl.modules.screening.engine.models import (
    ScreeningHit,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import migrate
from xolotl.platform.crypto.encrypted import encrypt, encrypt_json
from xolotl.platform.db import APP_ROLE, tenant_session
from xolotl.platform.ids import uuid7
from xolotl.platform.policy.models import PolicySnapshot

REVISION, BEFORE = "0010_screening", "0009_lists"
TABLES = (
    "party_name_index",
    "screening_subject",
    "screening_list_set",
    "screening_list_set_member",
    "screening_run",
    "screening_match",
    "screening_hit",
    "match_disposition",
    "screening_whitelist",
    "screening_batch",
)
# The foreign keys named by hand (their conventional name would pass 63 bytes, or
# the plan pins a shorter one): table -> name -> (columns, referred table, columns).
NAMED_FKS = {
    "screening_list_set_member": {
        "fk_screening_list_set_member_list_set": (
            ["tenant_id", "list_set_id"],
            "screening_list_set",
            ["tenant_id", "id"],
        ),
        "fk_screening_list_set_member_private_version": (
            ["tenant_id", "private_list_version_id"],
            "private_list_version",
            ["tenant_id", "id"],
        ),
        "fk_screening_list_set_member_list_version": (
            ["list_version_id"],
            "list_version",
            ["id"],
        ),
    },
    "screening_match": {
        "fk_screening_match_private_entry": (
            ["tenant_id", "private_list_entry_id"],
            "private_list_entry",
            ["tenant_id", "id"],
        ),
        "fk_screening_match_superseded_by": (
            ["tenant_id", "superseded_by_match_id"],
            "screening_match",
            ["tenant_id", "id"],
        ),
    },
    "screening_hit": {
        "fk_screening_hit_private_entry": (
            ["tenant_id", "private_list_entry_id"],
            "private_list_entry",
            ["tenant_id", "id"],
        ),
    },
    "match_disposition": {
        "fk_match_disposition_prior": (
            ["tenant_id", "prior_disposition_id"],
            "match_disposition",
            ["tenant_id", "id"],
        ),
        "fk_match_disposition_policy_snapshot": (
            ["tenant_id", "policy_snapshot_id"],
            "policy_snapshot",
            ["tenant_id", "id"],
        ),
    },
    "screening_whitelist": {
        "fk_screening_whitelist_disposition": (
            ["tenant_id", "disposition_id"],
            "match_disposition",
            ["tenant_id", "id"],
        ),
    },
}
SHARED_TARGETS = frozenset({"list_entry", "list_version"})


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def tables_present(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names()) & set(TABLES)


def four_eyes_function_exists(engine: Engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.scalar(
                text("SELECT 1 FROM pg_proc WHERE proname = :name"),
                {"name": "match_disposition_four_eyes"},
            )
        )


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def _one[T](s: Session, model: type[T]) -> T:
    return s.scalars(select(model)).one()


def _run_and_entry(tenant_id: UUID) -> tuple[ScreeningRun, UUID]:
    """Commit a run (with its subject, list set and snapshot) and a private entry.

    Returns:
        The run, detached from its session, and the private entry's id.

    """
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.screening_run(s, tenant_id)
        factories.private_list_entry(s, tenant_id)
        run = _one(s, ScreeningRun)
        entry_id = _one(s, PrivateListEntryRow).id
        s.expunge(run)
    return run, entry_id


def _refused(tenant_id: UUID, constraint: str, build: Callable[[Session], object]):
    with (
        pytest.raises(IntegrityError, match=constraint),
        tenant_session(system_ctx(tenant_id)) as s,
    ):
        _ = build(s)
        s.flush()


def _match(
    s: Session, run: ScreeningRun, entry_id: UUID | None, **kw: object
) -> ScreeningMatch:
    values: dict[str, object] = {
        "tenant_id": run.tenant_id,
        "subject_id": run.subject_id,
        "list_code": "MX_LPB",
        "list_kind": ListKind.BLOCKED,
        "private_list_entry_id": entry_id,
        "source_entry_key": "clave 1",
        "entry_content_hash": b"\x07" * 32,
        "band": HitBand.ALERT,
        "score": Decimal("0.9000"),
        "origin_run_id": run.id,
    }
    values.update(kw)
    row = ScreeningMatch(**values)
    s.add(row)
    return row


def test_0010_follows_0009():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_the_ten_tables_exist_with_row_level_security_forced(admin_engine: Engine):
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
                WHERE relname = ANY(:names) AND relkind = 'r'
                """
            ),
            {"names": list(TABLES)},
        ).all()
    assert {row[0]: (row[1], row[2]) for row in rows} == dict.fromkeys(
        TABLES, (True, True)
    )


def test_the_composite_foreign_keys_keep_references_inside_the_tenant(
    admin_engine: Engine,
):
    with admin_engine.connect() as conn:
        inspector = inspect(conn)
        fks = {table: inspector.get_foreign_keys(table) for table in TABLES}
    for table, named in NAMED_FKS.items():
        found = {
            fk["name"]: (
                fk["constrained_columns"],
                fk["referred_table"],
                fk["referred_columns"],
            )
            for fk in fks[table]
        }
        for name, shape in named.items():
            assert found.get(name) == shape, (table, name)
    for table_fks in fks.values():
        for fk in table_fks:
            if fk["referred_table"] in SHARED_TARGETS:
                assert fk["constrained_columns"][0] != "tenant_id", fk["name"]
                continue
            assert fk["constrained_columns"][0] == "tenant_id", fk["name"]
            assert fk["referred_columns"] == ["tenant_id", "id"], fk["name"]


def test_the_entry_reference_checks_refuse_both_or_neither(
    engine: Engine, tenant: CreatedTenant
):
    tenant_id = tenant.tenant_id
    run, entry_id = _run_and_entry(tenant_id)

    def match(
        list_entry_id: UUID | None, private_id: UUID | None
    ) -> Callable[[Session], object]:
        def build(s: Session) -> object:
            return _match(s, run, private_id, list_entry_id=list_entry_id)

        return build

    def hit(
        list_entry_id: UUID | None, private_id: UUID | None
    ) -> Callable[[Session], object]:
        def build(s: Session) -> None:
            s.add(
                ScreeningHit(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    list_code="MX_LPB",
                    list_entry_id=list_entry_id,
                    private_list_entry_id=private_id,
                    source_entry_key="clave 1",
                    entry_content_hash=b"\x07" * 32,
                    score=Decimal("0.5000"),
                    band=HitBand.LOW_CONFIDENCE,
                    features={},
                )
            )

        return build

    # CHECK runs before the foreign keys, so a made-up shared entry id is enough.
    for list_entry_id, private_id in ((uuid7(), entry_id), (None, None)):
        _refused(
            tenant_id,
            "ck_screening_match_one_entry",
            match(list_entry_id, private_id),
        )
        _refused(
            tenant_id, "ck_screening_hit_one_entry", hit(list_entry_id, private_id)
        )
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = match(None, entry_id)(s)
        _ = hit(None, entry_id)(s)


def test_the_subject_check_ties_kind_to_party_or_input(
    engine: Engine, tenant: CreatedTenant
):
    tenant_id = tenant.tenant_id
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.party(s, tenant_id)
        party_id = _one(s, Party).id

    def subject(kind: SubjectKind, *, party: bool, query: bool):
        def build(s: Session) -> None:
            s.add(
                ScreeningSubject(
                    tenant_id=tenant_id,
                    kind=kind,
                    party_id=party_id if party else None,
                    input=encrypt_json({"name": "x"}, tenant_id=tenant_id, session=s)
                    if query
                    else None,
                )
            )

        return build

    for kind, party, query in (
        (SubjectKind.PARTY, False, False),
        (SubjectKind.PARTY, True, True),
        (SubjectKind.AD_HOC, False, False),
        (SubjectKind.AD_HOC, True, True),
    ):
        _refused(
            tenant_id,
            "ck_screening_subject_kind_input",
            subject(kind, party=party, query=query),
        )
    with tenant_session(system_ctx(tenant_id)) as s:
        subject(SubjectKind.PARTY, party=True, query=False)(s)
        subject(SubjectKind.AD_HOC, party=False, query=True)(s)


def _disposition(
    tenant_id: UUID,
    match_id: UUID,
    snapshot_id: UUID,
    *,
    decision: Decision = Decision.FALSE_POSITIVE,
    decided_by_type: DecidedByType = DecidedByType.STAFF,
    decided_by: UUID | None = None,
    prior: UUID | None = None,
) -> Callable[[Session], None]:
    def build(s: Session) -> None:
        s.add(
            MatchDisposition(
                tenant_id=tenant_id,
                match_id=match_id,
                decision=decision,
                rationale=encrypt(
                    "the birth dates differ", tenant_id=tenant_id, session=s
                ),
                decided_by_type=decided_by_type,
                decided_by=decided_by,
                prior_disposition_id=prior,
                policy_snapshot_id=snapshot_id,
            )
        )

    return build


def _review_parents(tenant_id: UUID) -> tuple[UUID, UUID]:
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.screening_match(s, tenant_id)
        return _one(s, ScreeningMatch).id, _one(s, PolicySnapshot).id


def test_a_true_match_disposition_needs_a_prior_disposition(
    engine: Engine, tenant: CreatedTenant
):
    tenant_id = tenant.tenant_id
    match_id, snapshot_id = _review_parents(tenant_id)
    user_id = tenant.admin_user_id
    _refused(
        tenant_id,
        "ck_match_disposition_true_match_prior",
        _disposition(
            tenant_id,
            match_id,
            snapshot_id,
            decision=Decision.TRUE_MATCH,
            decided_by=user_id,
        ),
    )


def test_staff_decisions_name_a_user_and_system_ones_do_not(
    engine: Engine, tenant: CreatedTenant
):
    tenant_id = tenant.tenant_id
    match_id, snapshot_id = _review_parents(tenant_id)
    user_id = tenant.admin_user_id
    for decided_by_type, decided_by in (
        (DecidedByType.STAFF, None),
        (DecidedByType.SYSTEM, user_id),
    ):
        _refused(
            tenant_id,
            "ck_match_disposition_decided_by",
            _disposition(
                tenant_id,
                match_id,
                snapshot_id,
                decided_by_type=decided_by_type,
                decided_by=decided_by,
            ),
        )
    with tenant_session(system_ctx(tenant_id)) as s:
        _disposition(
            tenant_id,
            match_id,
            snapshot_id,
            decision=Decision.SUPERSEDED,
            decided_by_type=DecidedByType.SYSTEM,
        )(s)


def test_the_four_eyes_trigger_refuses_an_answer_by_the_same_user(
    engine: Engine, tenant: CreatedTenant
):
    tenant_id = tenant.tenant_id
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.match_disposition(s, tenant_id)
        escalation = _one(s, MatchDisposition)
        match_id, snapshot_id = escalation.match_id, escalation.policy_snapshot_id
        escalation_id, first_user = escalation.id, escalation.decided_by
    _refused(
        tenant_id,
        "match_disposition_four_eyes",
        _disposition(
            tenant_id,
            match_id,
            snapshot_id,
            decision=Decision.TRUE_MATCH,
            decided_by=first_user,
            prior=escalation_id,
        ),
    )
    with tenant_session(system_ctx(tenant_id)) as s:
        _disposition(
            tenant_id,
            match_id,
            snapshot_id,
            decision=Decision.TRUE_MATCH,
            decided_by=tenant.admin_user_id,
            prior=escalation_id,
        )(s)


def test_match_disposition_is_append_only_for_the_app_role(
    engine: Engine, tenant: CreatedTenant
):
    tenant_id = tenant.tenant_id
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.match_disposition(s, tenant_id)
    for statement in (
        update(MatchDisposition).values(decision=Decision.FALSE_POSITIVE),
        delete(MatchDisposition),
    ):
        with (
            pytest.raises(ProgrammingError) as refused,
            tenant_session(system_ctx(tenant_id)) as s,
        ):
            _ = s.execute(statement)
        assert isinstance(refused.value.orig, pg_errors.InsufficientPrivilege)


def _index_definitions(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
            {"t": table},
        ).all()
    return {row[0]: row[1] for row in rows}


def test_the_name_index_has_its_gin_indexes(admin_engine: Engine):
    indexes = _index_definitions(admin_engine, "party_name_index")
    assert "USING gin (phonetic_keys)" in indexes["ix_pni_phonetic"]
    assert "USING gin (norm_name gin_trgm_ops)" in indexes["ix_pni_trgm"]


def test_the_run_queue_and_lookup_indexes_exist(admin_engine: Engine):
    run = _index_definitions(admin_engine, "screening_run")
    match = _index_definitions(admin_engine, "screening_match")
    hit = _index_definitions(admin_engine, "screening_hit")
    assert "(tenant_id, subject_id, created_at DESC)" in run["ix_screening_run_subject"]
    assert "(tenant_id, status, band, created_at)" in match["ix_screening_match_queue"]
    assert (
        "(tenant_id, list_code, source_entry_key)"
        in (match["ix_screening_match_entry"])
    )
    assert "(tenant_id, match_id)" in hit["ix_screening_hit_match"]


def test_the_finding_key_is_unique(engine: Engine, tenant: CreatedTenant):
    tenant_id = tenant.tenant_id
    run, entry_id = _run_and_entry(tenant_id)

    def finding(s: Session) -> object:
        return _match(s, run, entry_id)

    with tenant_session(system_ctx(tenant_id)) as s:
        _ = finding(s)
    _refused(tenant_id, "uq_screening_match_finding", finding)
    # A new content of the same entry is a new finding.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = _match(s, run, entry_id, entry_content_hash=b"\x08" * 32)


def test_downgrade_removes_the_tables_and_the_trigger_function(database: Engine):
    go_down(BEFORE)
    assert tables_present(database) == set()
    assert not four_eyes_function_exists(database)
    migrate.upgrade()
    assert tables_present(database) == set(TABLES)
    assert four_eyes_function_exists(database)


def test_the_app_role_may_update_hits_and_delete_name_index_rows(admin_engine: Engine):
    def may(table: str, privilege: str) -> bool:
        with admin_engine.connect() as conn:
            return bool(
                conn.scalar(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {"role": APP_ROLE, "table": table, "privilege": privilege},
                )
            )

    assert may("screening_hit", "UPDATE")
    assert may("party_name_index", "DELETE")
    assert may("match_disposition", "INSERT")
    assert not may("match_disposition", "UPDATE")
    assert not may("match_disposition", "DELETE")


def test_a_subject_is_unique_per_party(engine: Engine, tenant: CreatedTenant):
    tenant_id = tenant.tenant_id
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.screening_subject(s, tenant_id)
        party_id = _one(s, ScreeningSubject).party_id

    def again(s: Session) -> None:
        s.add(
            ScreeningSubject(
                tenant_id=tenant_id, kind=SubjectKind.PARTY, party_id=party_id
            )
        )

    _refused(tenant_id, "uq_screening_subject_tenant_id_party_id", again)
