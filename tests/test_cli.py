import base64
import getpass
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NoReturn
from uuid import UUID

import httpx
import psycopg
import pytest
import respx
import uvicorn
import yaml
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, event, func, make_url, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

from tests.helpers import (
    SessionBoundProvider,
    audit_events,
    auth_session,
    data_key_of,
    holding_key_rows,
    key_states,
    leaked,
    make_tenant,
    make_tenant_with_keys,
    new_tenant,
    staff_session,
    system_ctx,
    wrapped_keys,
)
from tests.modules.monitoring import support as monitoring_support
from tests.modules.monitoring.support import add_customer
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.review.support import assign_itf
from xolotl.cli import app
from xolotl.modules.monitoring.enums import FileStatus
from xolotl.modules.monitoring.rules.library import TYPOLOGIES_DIR, read_typology
from xolotl.modules.monitoring.txn import files
from xolotl.modules.monitoring.txn.models import Transaction, TransactionFile
from xolotl.modules.party import documents
from xolotl.modules.party import service as party_service
from xolotl.modules.party.enums import (
    DocumentType,
    IdentifierType,
    PartyStatus,
    PartyType,
)
from xolotl.modules.party.models import Party
from xolotl.modules.party.schemas import (
    IdentifierInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening import evaluation_report
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.resolve import resolve
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    set_index_holder,
)
from xolotl.modules.screening.engine.models import (
    ScreeningBatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.screening.rescreen import register_rescreen
from xolotl.modules.screening.rescreen.walkers import (
    PERIODIC_RESCREEN_KIND,
    REINDEX_KIND,
)
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock, db, migrate
from xolotl.platform.config import DEV_MASTER_KEY, get_settings
from xolotl.platform.crypto import (
    KeyDestroyedError,
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.storage.local import LocalFsObjectStore
from xolotl.platform.units.banxico import BASE_URL as BANXICO_BASE_URL

runner = CliRunner()
pytestmark = pytest.mark.usefixtures("_restore_logging")


@pytest.fixture
def _restore_logging() -> Generator[None]:
    """Keep each test's logging setup from leaking into the next.

    ``configure_logging`` leaves a stream handler bound to a CliRunner stream that
    is closed once its test ends.

    Yields:
        Nothing; the root logger's handlers and level are restored afterwards.

    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


# One key has more than one base64 spelling: the last character of the public key
# carries two unused bits, so this decodes to the same 32 bytes (the settings
# compare bytes).
RESPELLED_DEV_MASTER_KEY = DEV_MASTER_KEY[:-2] + "J="
REAL_MASTER_KEY = base64.b64encode(bytes(range(100, 132))).decode()
DEV_KEY_WARNING = (
    "the public development master key is in use; every tenant's keys are readable "
    "to anyone with the repository; set XOLOTL_MASTER_KEY before creating real tenants"
)


def _create(slug: str = "cli-demo"):
    return runner.invoke(
        app,
        [
            "tenant",
            "create",
            "--slug",
            slug,
            "--legal-name",
            "CLI Demo SA",
            "--admin-email",
            "admin@cli.mx",
        ],
    )


def test_help_works_without_a_database():
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["serve", "--help"]).exit_code == 0


def test_serve_migrates_and_checks_the_role_before_it_serves(
    engine: Engine, monkeypatch
):
    calls: list[str] = []
    served: dict[str, object] = {}
    ensure_app_role = migrate.ensure_app_role

    def ensured() -> bool:
        calls.append("app role")
        return ensure_app_role()  # the real one: this role signs in, so it stays

    def checked(target: Engine) -> None:
        calls.append("role check")
        db.assert_app_role_safe(target)  # the real check, against the app engine

    def run(target: str, **options: object) -> None:
        calls.append("uvicorn")
        served.update(options, target=target)

    monkeypatch.setattr(migrate, "ensure_app_role", ensured)
    monkeypatch.setattr(migrate, "upgrade", lambda: calls.append("upgrade"))
    monkeypatch.setattr("xolotl.cli.assert_app_role_safe", checked)
    monkeypatch.setattr(uvicorn, "run", run)
    result = runner.invoke(app, ["serve", "--port", "8123"])
    assert result.exit_code == 0, result.output
    # The role first: the migrations grant to it, so it must exist before they run.
    assert calls == ["app role", "upgrade", "role check", "uvicorn"]
    assert (served["target"], served["factory"], served["port"]) == (
        "xolotl.app:build_app",
        True,
        8123,
    )
    assert served["log_config"] is None  # uvicorn logs through the JSON root handler


def test_serve_refuses_a_role_that_can_bypass_rls(engine: Engine, monkeypatch):
    def bypassing(_target: Engine) -> None:
        msg = "database role can bypass row-level security (superuser or BYPASSRLS)"
        raise db.RoleBypassError(msg)

    ran: list[object] = []

    def run(*args: object, **options: object) -> None:
        ran.append(args)

    monkeypatch.setattr(migrate, "upgrade", lambda: None)
    monkeypatch.setattr("xolotl.cli.assert_app_role_safe", bypassing)
    monkeypatch.setattr(uvicorn, "run", run)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "can bypass row-level security" in result.output
    assert ran == []


def test_serve_bootstraps_an_app_role_that_cannot_sign_in(
    admin_engine: Engine, monkeypatch
):
    # What a redeploy after XOLOTL_APP_DB_PASSWORD changed on Render looks like.
    configured = make_url(get_settings().database_url)
    assert configured.password
    rotate = sql.SQL("ALTER ROLE {} PASSWORD {}")
    with admin_engine.begin() as conn:
        migrate.exec_literal_ddl(
            conn, rotate.format(sql.Identifier(db.APP_ROLE), sql.Literal("elsewhere"))
        )
    served: list[str] = []

    def run(target: str, **_options: object) -> None:
        served.append(target)

    monkeypatch.setattr(uvicorn, "run", run)
    try:
        result = runner.invoke(app, ["serve"])
        probe = create_engine(configured, poolclass=NullPool)
        with probe.connect() as conn:  # a new sign-in, with the configured password
            assert conn.scalar(text("SELECT current_user")) == db.APP_ROLE
        probe.dispose()
    finally:
        with admin_engine.begin() as conn:
            migrate.exec_literal_ddl(
                conn,
                rotate.format(
                    sql.Identifier(db.APP_ROLE), sql.Literal(configured.password)
                ),
            )
    assert result.exit_code == 0, result.output
    assert served == ["xolotl.app:build_app"]
    assert "app role cannot sign in; bootstrapping it" in result.output


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write anywhere")
def test_serve_says_whether_it_can_write_the_object_store(
    engine: Engine, monkeypatch, tmp_path: Path
):
    # A persistent disk can mount for another user: serve tells, and creates nothing.
    disk = tmp_path / "disk"
    disk.mkdir()
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o555)

    def run(*_args: object, **_options: object) -> None:
        pass

    monkeypatch.setattr(migrate, "upgrade", lambda: None)
    monkeypatch.setattr(uvicorn, "run", run)
    said: dict[str, str] = {}
    for name, root in (("disk", disk / "objects"), ("locked", locked / "objects")):
        settings = get_settings().model_copy(update={"object_store_root": root})
        monkeypatch.setattr("xolotl.cli.get_settings", lambda s=settings: s)
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0, result.output
        assert not root.exists()
        said[name] = result.output
    assert "object store root is writable" in said["disk"]
    assert "not writable" not in said["disk"]
    assert "object store root is not writable by this process" in said["locked"]
    assert str(locked / "objects") in said["locked"]
    records = {
        name: [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        for name, output in said.items()
    }
    # The warning names the directory it checked and says what to do, so the operator
    # needs no manual; the INFO line carries no hint.
    (warning,) = [
        r
        for r in records["locked"]
        if r["level"] == "WARNING"
        and r["msg"] == "object store root is not writable by this process"
    ]
    assert warning["decided_by"] == str(locked)
    assert "ls -ldn" in warning["hint"]
    assert "docs/deploy-render.md" in warning["hint"]
    assert warning["object_store_root"] == str(locked / "objects")
    (info,) = [
        r
        for r in records["disk"]
        if r["level"] == "INFO" and r["msg"] == "object store root is writable"
    ]
    assert "hint" not in info


def _serve_warnings(
    monkeypatch: pytest.MonkeyPatch, **changes: object
) -> tuple[list[str], str]:
    # `serve` with the settings changed, past the migrations and short of listening.
    # Returns the messages of the WARNING records it logged and everything it wrote.
    settings = get_settings().model_copy(update=changes)

    def run(*_args: object, **_options: object) -> None:
        pass

    monkeypatch.setattr("xolotl.cli.get_settings", lambda: settings)
    monkeypatch.setattr(migrate, "upgrade", lambda: None)
    monkeypatch.setattr(uvicorn, "run", run)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    records = [
        json.loads(line) for line in result.output.splitlines() if line.startswith("{")
    ]
    return [r["msg"] for r in records if r["level"] == "WARNING"], result.output


@pytest.mark.parametrize(
    ("env", "key"),
    [
        ("dev", DEV_MASTER_KEY),  # what a deployment that sets neither variable runs
        ("test", DEV_MASTER_KEY),
        ("dev", RESPELLED_DEV_MASTER_KEY),
    ],
    ids=["dev", "test", "respelled"],
)
def test_serve_warns_once_when_the_public_development_master_key_is_in_use(
    engine: Engine, monkeypatch, env: str, key: str
):
    decode = base64.b64decode
    assert decode(key, validate=True) == decode(DEV_MASTER_KEY, validate=True)
    warned, output = _serve_warnings(monkeypatch, env=env, master_key=SecretStr(key))
    assert warned.count(DEV_KEY_WARNING) == 1
    # It names the setting to change and never shows a key.
    assert DEV_MASTER_KEY not in output
    assert key not in output


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
def test_serve_does_not_warn_when_the_master_key_is_a_real_one(
    engine: Engine, monkeypatch, env: str
):
    warned, output = _serve_warnings(
        monkeypatch, env=env, master_key=SecretStr(REAL_MASTER_KEY)
    )
    assert not [message for message in warned if "master key" in message]
    assert REAL_MASTER_KEY not in output


def test_serve_does_not_warn_in_prod_where_the_settings_refuse_the_public_key(
    engine: Engine, monkeypatch
):
    # `Settings` refuses this pair when it loads (tests/platform/test_config.py), so
    # no environment can produce it; copying one skips the check. Prod is not this
    # warning's business: it either runs a key of its own or does not start.
    warned, _ = _serve_warnings(
        monkeypatch, env="prod", master_key=SecretStr(DEV_MASTER_KEY)
    )
    assert not [message for message in warned if "master key" in message]


def test_db_bootstrap_is_idempotent(admin_engine: Engine):
    assert runner.invoke(app, ["db", "bootstrap"]).exit_code == 0
    assert runner.invoke(app, ["db", "bootstrap"]).exit_code == 0
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0


def test_db_bootstrap_refuses_a_role_that_can_bypass_rls_in_one_line(
    admin_engine: Engine,
):
    with admin_engine.begin() as conn:
        owner = conn.scalar(text("SELECT current_user"))
        _ = conn.execute(text(f'GRANT "{owner}" TO {db.APP_ROLE}'))
    try:
        result = runner.invoke(app, ["db", "bootstrap"])
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f'REVOKE "{owner}" FROM {db.APP_ROLE}'))
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception  # no traceback
    assert result.output == (
        f"error: role {db.APP_ROLE} can bypass row-level security: "
        "member of the owner role\n"
    )


@contextmanager
def _owner_statements(owner: Engine) -> Generator[list[str]]:
    """Record what the engine bootstrap runs on sends while the block runs.

    Yields:
        The statements, in the order they are sent.

    """
    statements: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(owner, "before_cursor_execute", keep)
    try:
        yield statements
    finally:
        event.remove(owner, "before_cursor_execute", keep)


def test_db_bootstrap_reports_a_refused_alter_without_the_password(
    owner_without_admin_option: str, managed_owner: tuple[Engine, str]
):
    owner, _ = managed_owner  # what migrate.get_admin_engine() returns for now
    with _owner_statements(owner) as statements:
        result = runner.invoke(app, ["db", "bootstrap"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception  # no traceback
    assert result.stderr == (
        f"error: could not set LOGIN and the password of role {db.APP_ROLE} "
        "(sqlstate 42501)\n"
    )
    assert owner_without_admin_option not in result.output
    # The server logs the statement it refused: it carries the SCRAM verifier.
    refused = f'ALTER ROLE "{db.APP_ROLE}" WITH LOGIN PASSWORD \'SCRAM-SHA-256$'
    assert [s for s in statements if s.startswith(refused)]
    assert not [s for s in statements if owner_without_admin_option in s]


def test_tenant_create_prints_the_password_once_and_rejects_duplicates(engine: Engine):
    result = _create()
    assert result.exit_code == 0, result.output
    assert result.output.count("one-time password:") == 1
    match = re.search(r"one-time password: (\S+)", result.output)
    assert match
    password = match.group(1)
    with auth_session() as s:
        outcome = service.login(
            s, tenant_slug="cli-demo", email="admin@cli.mx", password=password
        )
    assert outcome.must_change_password is True
    duplicate = _create()
    assert duplicate.exit_code == 1
    assert "CONFLICT" in duplicate.output
    invalid = _create(slug="x")
    assert invalid.exit_code == 1
    assert "VALIDATION_FAILED" in invalid.output


def test_tenant_create_gives_the_tenant_its_keys_without_the_app_running(
    engine: Engine, admin_engine: Engine, no_key_provider: None
):
    # The command line is a process of its own: nothing has built the app, so the
    # command has to install the key provider itself.
    result = _create()
    assert result.exit_code == 0, result.output
    match = re.search(r"tenant cli-demo created \(([0-9a-f-]+)\)", result.output)
    assert match
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tenant_id, purpose FROM tenant_key ORDER BY purpose")
        ).all()
    assert [(str(tenant_id), purpose) for tenant_id, purpose in rows] == [
        (match.group(1), "BIOMETRIC"),
        (match.group(1), "DATA"),
        (match.group(1), "INDEX"),
    ]
    # Wrapped by the configured master key, the one the application will unwrap with:
    # keys the command made under another one could never be read by the server.
    tenant_id = UUID(match.group(1))
    _, dek = get_key_provider().data_key(tenant_id, KeyPurpose.DATA)
    assert data_key_of(admin_engine, tenant_id) == dek


def test_the_key_provider_is_installed_once_per_process(
    engine: Engine, admin_engine: Engine, no_key_provider: None
):
    assert _create().exit_code == 0
    installed = get_key_provider()
    assert _create(slug="cli-second").exit_code == 0
    assert get_key_provider() is installed  # the second command shares the first's
    with admin_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM tenant_key")) == 6


def test_tenant_create_keeps_the_key_provider_the_process_already_has(
    engine: Engine,
):
    installed = get_key_provider()  # the test session's, as the application's is
    assert _create().exit_code == 0
    assert get_key_provider() is installed


def test_tenant_list_and_audit_verify(engine: Engine):
    assert _create().exit_code == 0
    listed = runner.invoke(app, ["tenant", "list"])
    assert listed.exit_code == 0
    assert "cli-demo\tSANDBOX\tACTIVE" in listed.output
    verified = runner.invoke(app, ["audit", "verify", "--tenant", "cli-demo"])
    assert verified.exit_code == 0, verified.output
    assert '"valid": true' in verified.output
    assert '"entries_checked": 3' in verified.output  # created, keys created, admin
    unknown = runner.invoke(app, ["audit", "verify", "--tenant", "nope"])
    assert unknown.exit_code == 1
    assert unknown.output == "error: NOT_FOUND: no tenant 'nope'\n"
    assert isinstance(unknown.exception, SystemExit), unknown.exception  # no traceback


def test_audit_verify_fails_on_a_tampered_chain(engine: Engine, admin_engine: Engine):
    assert _create().exit_code == 0
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(text("UPDATE audit_event SET action = 'forged' WHERE seq = 1"))
    broken = runner.invoke(app, ["audit", "verify", "--tenant", "cli-demo"])
    assert broken.exit_code == 1
    assert '"valid": false' in broken.output


# --- keys ---------------------------------------------------------------------
# A CLI command is a process of its own, so these tests start the way one does, with no
# key provider installed. They make their keys with a provider they hold themselves.

ACTOR = f"cli:{getpass.getuser()}"
INDEX_REASON = "INDEX keys are never rotated: every blind index would need recomputing"
BUSY = "this tenant's keys or audit trail are in use by another transaction"
IRREVERSIBLE = (
    "irreversible: everything encrypted for the tenant becomes unreadable for good"
)


# What a destroy says on stderr before it acts.
def _announcement(slug: str) -> str:
    return f"destroying every encryption key of tenant {slug} ({IRREVERSIBLE})"


@pytest.fixture
def new_process(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    try:
        before: KeyProvider | None = get_key_provider()
    except RuntimeError:
        before = None
    set_key_provider(None)
    # The provider the command builds for itself will not rotate or destroy outside the
    # service's session: a regression there fails at once instead of hanging the run.
    monkeypatch.setattr("xolotl.app.LocalKeyProvider", SessionBoundProvider)
    yield
    set_key_provider(before)


def _provider() -> LocalKeyProvider:
    return LocalKeyProvider(get_settings().master_key_bytes, db.get_sessionmaker())


def _keyed_tenant(slug: str = "acme") -> tuple[UUID, LocalKeyProvider]:
    provider = _provider()
    return make_tenant_with_keys(slug, provider=provider), provider


def _keys(*args: str):
    return runner.invoke(app, ["keys", *args])


# Every unwrapped and wrapped key of the tenant, to scan the output for.
def _key_material(
    provider: LocalKeyProvider, admin_engine: Engine, tenant_id: UUID
) -> list[bytes]:
    unwrapped = [
        bytes(provider.data_key(tenant_id, KeyPurpose(purpose), version)[1])
        for purpose, version, _ in key_states(admin_engine, tenant_id)
    ]
    return unwrapped + wrapped_keys(admin_engine, tenant_id)


def _help(*args: str) -> str:
    # Help is rendered by rich: on a narrow, colourless CI terminal it wraps the
    # option table and interleaves ANSI codes, so the text is read plain and wide.
    result = runner.invoke(
        app, [*args, "--help"], env={"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}
    )
    assert result.exit_code == 0, result.output
    return re.sub(r"\x1b\[[0-9;]*m", "", result.output)


def test_keys_help_works_without_a_database():
    _ = _help("keys")
    rotate = _help("keys", "rotate")
    assert "--tenant" in rotate
    assert "--purpose" in rotate
    assert "DATA|BIOMETRIC" in rotate
    assert "INDEX" not in rotate  # accepted, then refused; never offered
    destroy = _help("keys", "destroy")
    assert "--tenant" in destroy
    assert "--yes" in destroy


def test_keys_rotate_prints_the_new_version_and_audits_it(
    new_process: None, admin_engine: Engine
):
    tenant_id, provider = _keyed_tenant()
    first = _keys("rotate", "--tenant", " ACME ", "--purpose", "DATA")
    assert first.exit_code == 0, first.output
    assert first.output == "rotated the DATA key of tenant acme: now version 2\n"
    second = _keys("rotate", "--tenant", "acme", "--purpose", "DATA")
    assert second.output == "rotated the DATA key of tenant acme: now version 3\n"
    other = _keys("rotate", "--tenant", "acme", "--purpose", "BIOMETRIC")
    assert other.output == "rotated the BIOMETRIC key of tenant acme: now version 2\n"
    assert (first.stderr, second.stderr, other.stderr) == ("", "", "")
    assert [(e.action, e.detail) for e in audit_events(tenant_id)] == [
        ("tenant.key_rotated", {"purpose": "DATA", "version": 2}),
        ("tenant.key_rotated", {"purpose": "DATA", "version": 3}),
        ("tenant.key_rotated", {"purpose": "BIOMETRIC", "version": 2}),
    ]
    assert {(e.actor_type, e.actor_id) for e in audit_events(tenant_id)} == {
        ("PLATFORM_OPERATOR", ACTOR)
    }
    assert key_states(admin_engine, tenant_id) == [
        ("BIOMETRIC", 1, "retired"),
        ("BIOMETRIC", 2, "current"),
        ("DATA", 1, "retired"),
        ("DATA", 2, "retired"),
        ("DATA", 3, "current"),
        ("INDEX", 1, "current"),
    ]
    verified = runner.invoke(app, ["audit", "verify", "--tenant", "acme"])
    assert verified.exit_code == 0, verified.output
    assert '"entries_checked": 3' in verified.output
    printed = first.output + second.output + other.output + verified.output
    assert leaked(printed, *_key_material(provider, admin_engine, tenant_id)) == []


def test_keys_rotate_refuses_index_keys_in_one_line(
    new_process: None, admin_engine: Engine
):
    tenant_id, _ = _keyed_tenant()
    result = _keys("rotate", "--tenant", "acme", "--purpose", "INDEX")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception  # no traceback
    assert not result.stdout
    assert result.stderr == f"error: CONFLICT: {INDEX_REASON}\n"
    assert audit_events(tenant_id) == []
    assert {state for _, _, state in key_states(admin_engine, tenant_id)} == {"current"}
    assert len(key_states(admin_engine, tenant_id)) == len(KeyPurpose)


def test_keys_rotate_rejects_what_it_cannot_act_on(new_process: None):
    _ = _keyed_tenant()
    _ = make_tenant("bare")
    unknown = _keys("rotate", "--tenant", "nope", "--purpose", "DATA")
    assert (unknown.exit_code, unknown.stdout) == (1, "")
    assert unknown.stderr == "error: NOT_FOUND: no tenant 'nope'\n"
    keyless = _keys("rotate", "--tenant", "bare", "--purpose", "DATA")
    assert (keyless.exit_code, keyless.stdout) == (1, "")
    assert keyless.stderr == (
        "error: KEY_NOT_FOUND: no such encryption key for this tenant\n"
    )
    assert _keys("rotate", "--tenant", "acme", "--purpose", "OTHER").exit_code == 2
    assert _keys("rotate", "--tenant", "acme").exit_code == 2
    assert _keys("rotate", "--purpose", "DATA").exit_code == 2


def test_keys_destroy_refuses_without_yes_and_touches_nothing(
    new_process: None, admin_engine: Engine
):
    tenant_id, provider = _keyed_tenant()
    result = _keys("destroy", "--tenant", "acme")
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit), result.exception
    assert not result.stdout
    assert result.stderr.splitlines() == [
        "refusing to destroy the keys of tenant acme without --yes",
        f"this is {IRREVERSIBLE}",
    ]
    assert audit_events(tenant_id) == []
    assert {state for _, _, state in key_states(admin_engine, tenant_id)} == {"current"}
    assert provider.data_key(tenant_id, KeyPurpose.DATA)[0] == 1
    # The refusal comes before any database work: it does not look the tenant up.
    assert _keys("destroy", "--tenant", "nope").exit_code == 2


def test_keys_destroy_with_yes_says_what_it_does_and_destroys_every_key(
    new_process: None, admin_engine: Engine
):
    tenant_id, provider = _keyed_tenant()
    other_id, _ = _keyed_tenant("other")
    assert _keys("rotate", "--tenant", "acme", "--purpose", "DATA").exit_code == 0
    material = _key_material(provider, admin_engine, tenant_id)
    result = _keys("destroy", "--tenant", " ACME ", "--yes")
    assert result.exit_code == 0, result.output
    # What it is about to do goes to stderr before it acts; stdout carries the result.
    assert result.stderr == _announcement("acme") + "\n"
    assert result.output.splitlines()[0] == _announcement("acme")
    report = result.stdout.splitlines()
    assert report[0] == "destroyed the encryption keys of tenant acme"
    assert "5 minutes" in report[1]  # servers that cached the keys let go of them
    assert report[2].startswith("backups and archived WAL")
    assert "XOLOTL_MASTER_KEY" in report[3]
    assert len(report) == 4
    assert leaked(result.output, *material) == []
    states = key_states(admin_engine, tenant_id)
    assert len(states) == len(KeyPurpose) + 1
    assert {state for _, _, state in states} == {"destroyed"}
    assert wrapped_keys(admin_engine, tenant_id) == []
    assert [(e.action, e.detail) for e in audit_events(tenant_id)] == [
        ("tenant.key_rotated", {"purpose": "DATA", "version": 2}),
        ("tenant.keys_destroyed", None),
    ]
    assert audit_events(tenant_id)[-1].actor_id == ACTOR
    verified = runner.invoke(app, ["audit", "verify", "--tenant", "acme"])
    assert '"entries_checked": 2' in verified.output
    for purpose in KeyPurpose:  # a provider that never cached anything finds nothing
        with pytest.raises(KeyDestroyedError):
            _ = _provider().data_key(tenant_id, purpose)
    assert {s for _, _, s in key_states(admin_engine, other_id)} == {"current"}
    assert audit_events(other_id) == []


def test_a_keys_destroy_that_fails_has_nothing_on_stdout_and_one_error_line(
    new_process: None,
):
    bare = make_tenant("bare")
    unknown = _keys("destroy", "--tenant", "nope", "--yes")
    assert (unknown.exit_code, unknown.stdout) == (1, "")
    assert unknown.stderr == "error: NOT_FOUND: no tenant 'nope'\n"  # nothing announced
    keyless = _keys("destroy", "--tenant", "bare", "--yes")
    assert (keyless.exit_code, keyless.stdout) == (1, "")
    assert isinstance(keyless.exception, SystemExit), keyless.exception
    assert keyless.stderr.splitlines() == [
        _announcement("bare"),
        "error: KEY_NOT_FOUND: this tenant has no encryption keys to destroy",
    ]
    assert "destroyed" not in keyless.output  # it never claims what did not happen
    assert audit_events(bare) == []
    assert _keys("destroy", "--yes").exit_code == 2


def test_keys_destroy_twice_reports_the_same_and_is_audited_each_time(
    new_process: None,
):
    tenant_id, _ = _keyed_tenant()
    first = _keys("destroy", "--tenant", "acme", "--yes")
    second = _keys("destroy", "--tenant", "acme", "--yes")
    assert (first.exit_code, second.exit_code) == (0, 0)
    assert (second.stdout, second.stderr) == (first.stdout, first.stderr)
    assert first.stdout.startswith("destroyed the encryption keys of tenant acme\n")
    assert [e.action for e in audit_events(tenant_id)] == ["tenant.keys_destroyed"] * 2


def test_keys_commands_need_the_exact_slug_not_a_prefix(
    new_process: None, admin_engine: Engine
):
    acme, _ = _keyed_tenant("acme")
    acmeco, _ = _keyed_tenant("acmeco")
    for args in (
        ("rotate", "--tenant", "acm", "--purpose", "DATA"),
        ("destroy", "--tenant", "acm", "--yes"),
    ):
        result = _keys(*args)
        assert (result.exit_code, result.stdout) == (1, "")
        assert result.stderr == "error: NOT_FOUND: no tenant 'acm'\n"
    for tenant_id in (acme, acmeco):
        states = key_states(admin_engine, tenant_id)
        assert {state for _, _, state in states} == {"current"}
        assert len(states) == len(KeyPurpose)
        assert audit_events(tenant_id) == []


def test_keys_rotate_takes_the_purpose_in_any_case(new_process: None):
    _ = _keyed_tenant()
    lower = _keys("rotate", "--tenant", "acme", "--purpose", "data")
    assert lower.output == "rotated the DATA key of tenant acme: now version 2\n"
    mixed = _keys("rotate", "--tenant", "acme", "--purpose", "BioMetric")
    assert mixed.output == "rotated the BIOMETRIC key of tenant acme: now version 2\n"
    refused = _keys("rotate", "--tenant", "acme", "--purpose", "index")
    assert (refused.exit_code, refused.stdout) == (1, "")
    assert refused.stderr == f"error: CONFLICT: {INDEX_REASON}\n"


def test_keys_destroy_gives_up_with_a_conflict_when_the_keys_are_locked(
    new_process: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("xolotl.modules.tenants.service.KEY_LOCK_TIMEOUT", "300ms")
    tenant_id, _ = _keyed_tenant()
    with ThreadPoolExecutor(max_workers=1) as pool, holding_key_rows(tenant_id):
        waiting = pool.submit(_keys, "destroy", "--tenant", "acme", "--yes")
        result = waiting.result(timeout=15)  # a hang would fail here, not block
    assert (result.exit_code, result.stdout) == (1, "")
    assert isinstance(result.exception, SystemExit), result.exception  # no traceback
    announced, error = result.stderr.splitlines()
    assert announced == _announcement("acme")
    assert error == f"error: CONFLICT: {BUSY}; try again shortly"
    assert audit_events(tenant_id) == []


def test_keys_rotate_refuses_a_tenant_whose_keys_were_destroyed(new_process: None):
    tenant_id, _ = _keyed_tenant()
    assert _keys("destroy", "--tenant", "acme", "--yes").exit_code == 0
    result = _keys("rotate", "--tenant", "acme", "--purpose", "DATA")
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr == (
        "error: KEY_DESTROYED: the encryption keys of this tenant were destroyed\n"
    )
    assert [e.action for e in audit_events(tenant_id)] == ["tenant.keys_destroyed"]


def test_keys_commands_install_the_key_provider_a_new_process_lacks(
    new_process: None,
):
    _ = _keyed_tenant()
    with pytest.raises(RuntimeError, match="key provider not configured"):
        _ = get_key_provider()
    assert _keys("rotate", "--tenant", "acme", "--purpose", "DATA").exit_code == 0
    assert isinstance(get_key_provider(), LocalKeyProvider)


def test_keys_commands_use_the_key_provider_the_process_already_has(
    new_process: None,
):
    tenant_id, provider = _keyed_tenant()
    set_key_provider(provider)
    assert provider.data_key(tenant_id, KeyPurpose.DATA)[0] == 1  # now cached
    assert _keys("rotate", "--tenant", "acme", "--purpose", "DATA").exit_code == 0
    assert get_key_provider() is provider
    assert provider.data_key(tenant_id, KeyPurpose.DATA)[0] == 2  # evicted on commit
    assert _keys("destroy", "--tenant", "acme", "--yes").exit_code == 0
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant_id, KeyPurpose.DATA)


def _party_status(tenant_id: UUID, party_id: UUID) -> PartyStatus:
    with db.tenant_session(system_ctx(tenant_id)) as s:
        return s.get_one(Party, (tenant_id, party_id)).status


def test_party_purge_due_counts_then_purges_the_due_parties(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    store = LocalFsObjectStore(tmp_path / "objects")
    created = new_tenant()
    request = PartyCreateRequest(
        party_type=PartyType.PERSON,
        person=PersonInput(given_names="Ana", first_surname="Pérez"),
    )
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        party = party_service.create_party(s, request)
        _ = documents.upload_document(
            s,
            store,
            party_id=party.id,
            doc_type=DocumentType.IDENTITY,
            content_type="application/pdf",
            data=b"%PDF-1.4 cli",
        )
        # Due under the real clock, with no customer relationship to keep it.
        party.retention_until = date(2020, 1, 1)
    objects = tmp_path / "objects"
    assert any(path.is_file() for path in objects.rglob("*"))
    monkeypatch.setattr(
        "xolotl.cli.get_settings",
        lambda: get_settings().model_copy(update={"object_store_root": objects}),
    )

    counted = runner.invoke(
        app, ["party", "purge-due", "--tenant", "acme", "--dry-run"]
    )
    assert counted.exit_code == 0, counted.output
    assert counted.stdout == "1 parties of tenant acme due for purge\n"
    assert _party_status(created.tenant_id, party.id) is PartyStatus.ACTIVE

    purged = runner.invoke(app, ["party", "purge-due", "--tenant", "acme"])
    assert purged.exit_code == 0, purged.output
    assert purged.stdout == "purged 1 parties of tenant acme\n"
    assert _party_status(created.tenant_id, party.id) is PartyStatus.PURGED
    assert not any(path.is_file() for path in objects.rglob("*"))

    unknown = runner.invoke(app, ["party", "purge-due", "--tenant", "nope"])
    assert (unknown.exit_code, unknown.stdout) == (1, "")
    assert unknown.stderr == "error: NOT_FOUND: no tenant 'nope'\n"


def test_party_purge_due_help_needs_no_database():
    result = runner.invoke(
        app,
        ["party", "purge-due", "--help"],
        env={"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"},
    )
    assert result.exit_code == 0
    assert "--dry-run" in result.stdout


def test_db_reset_requires_confirmation_and_remigrates(admin_engine: Engine):
    refused = runner.invoke(app, ["db", "reset"])
    assert refused.exit_code == 2
    result = runner.invoke(app, ["db", "reset", "--yes"])
    assert result.exit_code == 0, result.output
    db.get_engine().dispose()
    db.get_admin_engine().dispose()
    with admin_engine.connect() as conn:
        tables = set(
            conn.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).all()
        )
    assert {"tenant", "staff_user", "audit_event_default", "alembic_version"} <= tables


# --- the database cannot be reached ---------------------------------------------
# The commands build their engines from the environment: pointing them elsewhere
# needs the settings and engine caches cleared, and cleared again afterwards so the
# next test rebuilds the suite's (test_db_reset_requires_confirmation_and_remigrates
# is the precedent for rebuilding engines mid-suite).

CANNOT_CONNECT = "error: cannot connect to the database: "


@pytest.fixture
def database_at(monkeypatch: pytest.MonkeyPatch) -> Generator[Callable[..., None]]:
    """Yield ``point(host, port, *, timeout=None)``: the next commands go there.

    ``point`` sets ``XOLOTL_DATABASE_URL`` and ``XOLOTL_DATABASE_ADMIN_URL`` to the
    suite's URLs with that host and port (user, password and database kept), sets
    ``XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS`` when a timeout is given, and clears
    the settings and engine caches so the commands rebuild from the environment.
    Teardown clears them again: the next caller rebuilds from the environment
    monkeypatch has restored by then.

    Yields:
        The ``point`` function.

    """

    def clear_caches() -> None:
        # Dispose what the caches hold before dropping it: a pool that is garbage
        # collected finalizes its connections unclosed, which is a ResourceWarning
        # and so an error here.
        db.get_engine().dispose()
        db.get_admin_engine().dispose()
        get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_admin_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

    def point(host: str, port: int, *, timeout: int | None = None) -> None:
        for name in ("XOLOTL_DATABASE_URL", "XOLOTL_DATABASE_ADMIN_URL"):
            url = make_url(os.environ[name]).set(host=host, port=port)
            monkeypatch.setenv(name, url.render_as_string(hide_password=False))
        if timeout is not None:
            monkeypatch.setenv("XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS", str(timeout))
        clear_caches()

    yield point
    clear_caches()


def _one_error_line(result) -> str:
    # Exit status 1 and one line on stderr: the error, never a traceback.
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "Traceback" not in result.output
    [line] = result.stderr.splitlines()
    assert line.startswith(CANNOT_CONNECT)
    return line


def test_serve_fails_in_one_line_when_the_database_is_down(
    engine: Engine, database_at, monkeypatch
):
    database_at("127.0.0.1", 1)  # a closed port: the connection is refused at once
    ran: list[object] = []

    def run(*args: object, **_options: object) -> None:
        ran.append(args)

    monkeypatch.setattr(uvicorn, "run", run)
    result = runner.invoke(app, ["serve"])
    assert "127.0.0.1" in _one_error_line(result)
    assert ran == []


def test_db_upgrade_fails_in_one_line_when_the_database_is_down(
    engine: Engine, database_at
):
    database_at("127.0.0.1", 1)
    result = runner.invoke(app, ["db", "upgrade"])
    assert "127.0.0.1" in _one_error_line(result)
    assert "upgraded" not in result.output  # it never claims what did not happen


def test_db_upgrade_gives_up_within_the_connect_timeout_when_the_database_never_answers(
    engine: Engine, database_at, silent_server: tuple[str, int]
):
    host, port = silent_server
    database_at(host, port, timeout=2)
    started = time.monotonic()
    result = runner.invoke(app, ["db", "upgrade"])
    assert time.monotonic() - started < 10
    assert "timeout" in _one_error_line(result)


def test_a_server_sent_operational_error_keeps_its_traceback(
    engine: Engine, monkeypatch
):
    # A SQLSTATE means a server answered: the database was reached and something
    # else is wrong, which the traceback says and one line would hide.
    def shut_down() -> NoReturn:
        cause = psycopg.errors.AdminShutdown(  # SQLSTATE 57P01
            "terminating connection due to administrator command"
        )
        statement = "SELECT 1"
        raise OperationalError(statement, {}, cause)

    monkeypatch.setattr(migrate, "upgrade", shut_down)
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert isinstance(result.exception, OperationalError), result.exception


HOLIDAYS_SEEDED = (
    "seeded 108 holidays for MX (NATIONAL 22, CNBV 28, SAT 30, BANXICO 28); "
    "35 dates still marked verify\n"
)


def test_holidays_help_lists_seed():
    result = runner.invoke(app, ["holidays", "--help"])
    assert result.exit_code == 0
    assert "seed" in result.stdout


def test_holidays_seed_loads_the_file_and_says_how_many_rows_await_review(
    admin_engine: Engine,
):
    first = runner.invoke(app, ["holidays", "seed"])
    assert (first.exit_code, first.stdout, first.stderr) == (0, HOLIDAYS_SEEDED, "")
    again = runner.invoke(
        app, ["holidays", "seed", str(Path("policy-packs/_holidays/MX.yaml"))]
    )
    assert (again.exit_code, again.stdout) == (0, HOLIDAYS_SEEDED)
    with admin_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM holiday")) == 108


def test_holidays_seed_refuses_a_missing_file_in_one_line(
    admin_engine: Engine, tmp_path: Path
):
    result = runner.invoke(app, ["holidays", "seed", str(tmp_path / "nope.yaml")])
    assert (result.exit_code, result.stdout) == (1, "")
    assert isinstance(result.exception, SystemExit), result.exception
    [line] = result.stderr.splitlines()
    assert line.startswith("error: VALIDATION_FAILED: ")
    assert "nope.yaml" in line
    with admin_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM holiday")) == 0


# ---- units ------------------------------------------------------------------------

UNITS_SEEDED = (
    "UMA: 3 rows (3 new, 0 unchanged)\n"
    "UDI: 62 rows (62 new, 0 unchanged)\n"
    "USD_MXN_FIX: 42 rows (42 new, 0 unchanged)\n"
)


def _unit_values(admin_engine: Engine) -> int:
    with admin_engine.connect() as conn:
        return conn.scalar(text("SELECT count(*) FROM unit_value")) or 0


def _with_settings(monkeypatch: pytest.MonkeyPatch, **changes: object) -> None:
    settings = get_settings().model_copy(update=changes)
    monkeypatch.setattr("xolotl.cli.get_settings", lambda: settings)


def test_units_seed_refuses_unverified_values_without_the_flag(
    engine: Engine, admin_engine: Engine, tmp_path: Path
):
    for seed in Path("policy-packs/_units").glob("*.yaml"):
        text_ = seed.read_text(encoding="utf-8")
        if seed.name == "UMA.yaml":
            row = next(line for line in text_.splitlines() if "2026-02-01" in line)
            text_ = text_.replace(
                row, row.replace("confidence: VERIFIED", "confidence: UNVERIFIED")
            )
        _ = (tmp_path / seed.name).write_text(text_, encoding="utf-8")
    result = runner.invoke(app, ["units", "seed", str(tmp_path)])
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr == "error: UNVERIFIED_VALUE: UMA 2026-02-01\n"
    assert _unit_values(admin_engine) == 0


def test_units_seed_loads_the_committed_seeds_without_the_flag(
    engine: Engine, admin_engine: Engine
):
    result = runner.invoke(app, ["units", "seed"])
    assert (result.exit_code, result.stdout) == (0, UNITS_SEEDED), result.output
    assert _unit_values(admin_engine) == 107


def test_units_seed_with_the_flag_loads_every_unit_once(
    engine: Engine, admin_engine: Engine
):
    first = runner.invoke(app, ["units", "seed", "--allow-unverified"])
    assert (first.exit_code, first.stdout) == (0, UNITS_SEEDED), first.output
    again = runner.invoke(
        app, ["units", "seed", "--allow-unverified", "policy-packs/_units"]
    )
    assert again.exit_code == 0, again.output
    assert again.stdout == (
        "UMA: 3 rows (0 new, 3 unchanged)\n"
        "UDI: 62 rows (0 new, 62 unchanged)\n"
        "USD_MXN_FIX: 42 rows (0 new, 42 unchanged)\n"
    )
    assert _unit_values(admin_engine) == 107


def test_units_seed_refuses_the_flag_in_prod(
    engine: Engine, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _with_settings(monkeypatch, env="prod")
    result = runner.invoke(app, ["units", "seed", "--allow-unverified"])
    assert result.exit_code == 2
    assert (
        result.stderr == "error: --allow-unverified is refused when XOLOTL_ENV=prod\n"
    )
    assert _unit_values(admin_engine) == 0


def test_units_fetch_in_demo_mode_reads_the_seeds(
    engine: Engine, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _with_settings(monkeypatch, banxico_token=None)
    args = ["units", "fetch", "--unit", "UDI", "--from", "2026-01-01"]
    result = runner.invoke(app, [*args, "--to", "2026-01-05"])
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "UDI: fetched 5, inserted 5, corrected 0 (2026-01-01..2026-01-05) [demo]\n"
    )
    assert _unit_values(admin_engine) == 5


def test_units_fetch_without_a_range_continues_from_the_latest_row(
    engine: Engine, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _with_settings(monkeypatch, banxico_token=None)
    seeded = runner.invoke(app, ["units", "seed", "--allow-unverified"])
    assert seeded.exit_code == 0, seeded.output
    monkeypatch.setattr(
        "xolotl.platform.clock.now", lambda: datetime(2026, 1, 31, 20, 0, tzinfo=UTC)
    )
    result = runner.invoke(app, ["units", "fetch", "--unit", "UDI"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "UDI: up to date (latest 2026-01-31)\n"


def test_units_fetch_live_calls_the_sie_with_the_token(
    engine: Engine, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _with_settings(monkeypatch, banxico_token=SecretStr("tok-1"))
    body = {
        "bmx": {
            "series": [
                {
                    "idSerie": "SP68257",
                    "datos": [{"fecha": "02/01/2026", "dato": "8.700000"}],
                }
            ]
        }
    }
    with respx.mock() as sie:
        route = sie.get(
            f"{BANXICO_BASE_URL}/series/SP68257/datos/2026-01-01/2026-01-05"
        ).mock(return_value=httpx.Response(200, json=body))
        result = runner.invoke(
            app,
            [
                "units",
                "fetch",
                "--unit",
                "UDI",
                "--from",
                "2026-01-01",
                "--to",
                "2026-01-05",
            ],
        )
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "UDI: fetched 1, inserted 1, corrected 0 (2026-01-01..2026-01-05) [live]\n"
    )
    assert route.calls.last.request.headers["Bmx-Token"] == "tok-1"


def test_units_fetch_refuses_what_it_cannot_act_on(engine: Engine):
    unknown = runner.invoke(app, ["units", "fetch", "--unit", "TIIE"])
    assert unknown.exit_code == 1
    assert unknown.stderr.startswith("error: VALIDATION_FAILED: ")
    half = runner.invoke(
        app, ["units", "fetch", "--unit", "UDI", "--from", "2026-01-01"]
    )
    assert half.exit_code == 2


def test_units_fetch_refuses_demo_mode_in_prod(
    engine: Engine, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _with_settings(monkeypatch, env="prod", banxico_token=None)
    result = runner.invoke(
        app,
        [
            "units",
            "fetch",
            "--unit",
            "UDI",
            "--from",
            "2026-01-01",
            "--to",
            "2026-01-05",
        ],
    )
    assert result.exit_code == 1
    assert result.stderr == (
        "error: UNIT_SOURCE_UNAVAILABLE: no XOLOTL_BANXICO_TOKEN; "
        "demo mode is refused when XOLOTL_ENV=prod\n"
    )
    assert _unit_values(admin_engine) == 0


ITF_PACK = "policy-packs/MX/ITF-IFPE/v1.yaml"
_HASHED = r"\(sha256 [0-9a-f]{12}\)"


def test_packs_help_lists_load_validate_and_show():
    result = runner.invoke(app, ["packs", "--help"])
    assert result.exit_code == 0
    for command in ("load", "validate", "show"):
        assert command in result.stdout


def test_packs_validate_names_the_unverified_values_without_a_database():
    result = runner.invoke(app, ["packs", "validate", ITF_PACK])
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    unverified = r"unverified: captures\[ITF_ROR_CASH\]\.period"
    expected = rf"valid MX/ITF-IFPE v1 {_HASHED}; {unverified}\n"
    assert re.fullmatch(expected, result.stdout)


def test_packs_validate_says_none_when_every_value_is_verified(tmp_path: Path):
    text_ = Path(ITF_PACK).read_text(encoding="utf-8")
    verified = tmp_path / "v1.yaml"
    _ = verified.write_text(
        text_.replace("confidence: UNVERIFIED", "confidence: VERIFIED"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["packs", "validate", str(verified)])
    assert result.exit_code == 0
    assert result.stdout.endswith("; unverified: none\n")


def test_packs_validate_refuses_an_invalid_file_in_one_line(tmp_path: Path):
    broken = tmp_path / "v1.yaml"
    _ = broken.write_text("pack: MX/X-Y\nversion: 1\n", encoding="utf-8")
    result = runner.invoke(app, ["packs", "validate", str(broken)])
    assert (result.exit_code, result.stdout) == (1, "")
    [line] = result.stderr.splitlines()
    assert line.startswith("error: PACK_INVALID: v1.yaml: ")
    missing = runner.invoke(app, ["packs", "validate", str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 1
    assert missing.stderr == "error: PACK_INVALID: nope.yaml: not a pack\n"


def test_rules_validate_lists_the_library_without_a_database(tmp_path: Path):
    cash = TYPOLOGIES_DIR / "CASH_STRUCTURING.yaml"
    result = runner.invoke(app, ["rules", "validate"])
    assert (result.exit_code, result.stderr) == (0, ""), result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 16
    assert lines[2] == (
        "CASH_STRUCTURING v1 STRUCTURING "
        f"{read_typology(cash).definition_hash.hex()[:12]} MX/ITF-IFPE"
    )
    assert all(
        re.fullmatch(r"[A-Z0-9_]+ v1 [A-Z_]+ [0-9a-f]{12} MX/[A-Z/,-]+", line)
        for line in lines
    )
    _ = shutil.copy(
        TYPOLOGIES_DIR / "TIER_N1_MONTHLY_OPS.yaml", tmp_path / "TIER_N3.yaml"
    )
    bad = runner.invoke(app, ["rules", "validate", str(tmp_path)])
    assert (bad.exit_code, bad.stdout) == (1, "")
    assert bad.stderr == (
        "error: VALIDATION_FAILED: TIER_N3.yaml: the code TIER_N1_MONTHLY_OPS "
        "differs from the file name\n"
    )


def test_rules_load_show_and_seed_tenant(admin_engine: Engine, tenant: CreatedTenant):
    first = runner.invoke(app, ["rules", "load"])
    assert (first.exit_code, first.stderr) == (0, ""), first.stderr
    lines = first.stdout.splitlines()
    assert lines[-1] == "16 created, 0 unchanged"
    assert "created CASH_STRUCTURING v1" in lines
    assert len(lines) == 17
    again = runner.invoke(app, ["rules", "load", str(TYPOLOGIES_DIR)])
    assert (again.exit_code, again.stdout) == (0, "0 created, 16 unchanged\n")

    show = runner.invoke(app, ["rules", "show", "CASH_STRUCTURING"])
    assert show.exit_code == 0, show.stderr
    cash = read_typology(TYPOLOGIES_DIR / "CASH_STRUCTURING.yaml").definition
    assert f"es-MX: {cash.render('es-MX')}\n" in show.stdout
    assert show.stdout.endswith(f"en: {cash.render('en')}\n")
    shown = yaml.safe_load(show.stdout.split("es-MX: ")[0])
    assert (shown["code"], shown["version"]) == ("CASH_STRUCTURING", 1)
    assert shown["definition"]["params"]["threshold"]["value"] == "5000"
    missing = runner.invoke(
        app, ["rules", "show", "CASH_STRUCTURING", "--version", "2"]
    )
    assert missing.exit_code == 1
    assert missing.stderr == (
        "error: RULE_TEMPLATE_UNKNOWN: no rule template CASH_STRUCTURING v2\n"
    )

    assign_itf(tenant, admin_engine)
    seed = [
        "rules",
        "seed-tenant",
        "--tenant",
        "acme",
        "--author",
        "Admin@Acme.mx",
    ]
    seeded = runner.invoke(app, seed)
    assert seeded.exit_code == 0, seeded.stderr
    drafts = seeded.stdout.splitlines()
    assert drafts[-1] == "16 drafts for acme"
    assert re.fullmatch(r"draft CASH_STRUCTURING v1 \([0-9a-f-]{36}\)", drafts[2])
    assert runner.invoke(app, seed).stdout == "0 drafts for acme\n"
    nobody = runner.invoke(app, [*seed[:-1], "nobody@acme.mx"])
    assert nobody.exit_code == 1
    assert nobody.stderr.startswith("error: NOT_FOUND: no staff user")


def test_packs_load_refuses_unverified_values_without_the_flag(admin_engine: Engine):
    result = runner.invoke(app, ["packs", "load"])
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr == (
        "error: UNVERIFIED_VALUE: MX/ITF-IFPE v1: captures[ITF_ROR_CASH].period\n"
    )
    with admin_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM policy_pack_version")) == 0


def test_packs_load_installs_both_packs_once(admin_engine: Engine):
    first = runner.invoke(app, ["packs", "load", "--allow-unverified"])
    assert first.exit_code == 0, first.stderr
    itf, avi = first.stdout.splitlines()
    assert re.fullmatch(rf"installed MX/ITF-IFPE v1 {_HASHED}", itf)
    assert re.fullmatch(rf"installed MX/LFPIORPI-AVI v1 {_HASHED}", avi)
    again = runner.invoke(app, ["packs", "load", "--allow-unverified"])
    assert again.exit_code == 0, again.stderr
    assert again.stdout == first.stdout.replace("installed", "unchanged")
    one = runner.invoke(app, ["packs", "load", "--allow-unverified", ITF_PACK])
    assert one.stdout.startswith("unchanged MX/ITF-IFPE v1 ")
    with admin_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM policy_pack_version")) == 2


def test_packs_load_refuses_a_changed_version(admin_engine: Engine, tmp_path: Path):
    loaded = runner.invoke(app, ["packs", "load", "--allow-unverified", ITF_PACK])
    assert loaded.exit_code == 0
    changed = tmp_path / "v1.yaml"
    _ = changed.write_text(
        Path(ITF_PACK).read_text(encoding="utf-8").replace("— IFPE", "— IFPE (2)"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["packs", "load", "--allow-unverified", str(changed)])
    assert result.exit_code == 1
    assert result.stderr.startswith("error: PACK_VERSION_IMMUTABLE: ")


def test_packs_load_takes_the_flag_from_the_settings(
    admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings().model_copy(update={"allow_unverified_pack_values": True})
    monkeypatch.setattr("xolotl.cli.get_settings", lambda: settings)
    result = runner.invoke(app, ["packs", "load", ITF_PACK])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("installed MX/ITF-IFPE v1 ")


def test_packs_load_refuses_unverified_values_in_prod_before_the_database(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_settings().model_copy(update={"env": "prod"})
    monkeypatch.setattr("xolotl.cli.get_settings", lambda: settings)

    def no_database() -> NoReturn:
        pytest.fail("packs load reached the database")

    monkeypatch.setattr("xolotl.cli.get_admin_engine", no_database)
    result = runner.invoke(app, ["packs", "load", "--allow-unverified"])
    assert (result.exit_code, result.stdout) == (2, "")
    assert "prod" in result.stderr


def test_packs_show_prints_the_installed_version(admin_engine: Engine):
    assert runner.invoke(app, ["packs", "load", "--allow-unverified"]).exit_code == 0
    result = runner.invoke(app, ["packs", "show", "MX/ITF-IFPE", "--version", "1"])
    assert result.exit_code == 0, result.stderr
    shown = json.loads(result.stdout)
    assert set(shown) == {"code", "version", "effective_from", "content_hash", "config"}
    assert (shown["code"], shown["version"], shown["effective_from"]) == (
        "MX/ITF-IFPE",
        1,
        "2026-01-01",
    )
    assert re.fullmatch(r"[0-9a-f]{64}", shown["content_hash"])
    assert shown["config"]["pack"] == "MX/ITF-IFPE"
    assert "Tecnología" in result.stdout
    missing = runner.invoke(app, ["packs", "show", "MX/ITF-IFPE", "--version", "9"])
    assert missing.exit_code == 1
    assert missing.stderr == "error: NOT_FOUND: no pack MX/ITF-IFPE version 9\n"


# --- lists: the shared source catalog ---------------------------------------------

LISTS_SEEDED = "list sources: 5 inserted, 0 updated, 0 unchanged, 0 disabled\n"
LISTS_RESEEDED = "list sources: 0 inserted, 0 updated, 5 unchanged, 0 disabled\n"


def _list_source_count(admin_engine: Engine) -> int:
    with admin_engine.connect() as conn:
        return conn.scalar(text("SELECT count(*) FROM list_source WHERE enabled"))


def test_lists_help_lists_seed_and_status():
    result = runner.invoke(app, ["lists", "--help"])
    assert result.exit_code == 0
    assert "seed" in result.output
    assert "status" in result.output


def test_lists_seed_reports_counts_and_is_idempotent(admin_engine: Engine):
    first = runner.invoke(app, ["lists", "seed"])
    assert first.exit_code == 0, first.output
    assert first.output == LISTS_SEEDED
    again = runner.invoke(
        app,
        ["lists", "seed", str(Path("src/xolotl/modules/screening/sources.yaml"))],
    )
    assert again.exit_code == 0, again.output
    assert again.output == LISTS_RESEEDED
    assert _list_source_count(admin_engine) == 5


def test_lists_seed_refuses_a_missing_file_in_one_line(
    admin_engine: Engine, tmp_path: Path
):
    result = runner.invoke(app, ["lists", "seed", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    [line] = result.stderr.splitlines()
    assert line.startswith("error: VALIDATION_FAILED: ")


def test_db_upgrade_seeds_the_catalog(admin_engine: Engine):
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0, result.output
    # The migrations log JSON lines first; the two results come last.
    assert result.output.endswith(
        LISTS_SEEDED
        + "rule templates: 16 created, 0 unchanged\n"
        + "database upgraded to head\n"
    )
    assert _list_source_count(admin_engine) == 5


def test_serve_seeds_the_catalog_after_migrating(admin_engine: Engine, monkeypatch):
    def run(*_args: object, **_options: object) -> None:
        pass

    monkeypatch.setattr(uvicorn, "run", run)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert _list_source_count(admin_engine) == 5
    with admin_engine.connect() as conn:
        templates = conn.scalar(text("SELECT count(*) FROM rule_template_version"))
    assert templates == 16


def test_lists_status_lists_every_source(admin_engine: Engine, monkeypatch):
    now = datetime(2026, 10, 19, 12, 5, tzinfo=UTC)
    monkeypatch.setattr(clock, "now", lambda: now)
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE list_source SET last_success_at = :success, next_poll_at = :next
                WHERE code = 'UN_CONSOLIDATED'
                """
            ),
            {
                "success": datetime(2026, 10, 19, 12, tzinfo=UTC),
                "next": datetime(2026, 10, 19, 12, 15, tzinfo=UTC),
            },
        )
        _ = conn.execute(
            text(
                """
                UPDATE list_source SET last_success_at = :success, next_poll_at = :next,
                    consecutive_failures = 2, last_error = 'HTTP_503: server answered'
                WHERE code = 'OFAC_SDN'
                """
            ),
            {
                "success": datetime(2026, 10, 19, 11, 50, tzinfo=UTC),
                "next": datetime(2026, 10, 20, 1, 0, tzinfo=UTC),
            },
        )
        _ = conn.execute(
            text(
                "UPDATE list_source SET next_poll_at = :next WHERE code <> ALL(:done)"
            ),
            {
                "next": datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
                "done": ["UN_CONSOLIDATED", "OFAC_SDN"],
            },
        )
        _ = conn.execute(
            text("UPDATE list_source SET enabled = false WHERE code = 'UK_FCDO'")
        )
        _ = conn.execute(
            text(
                """
                INSERT INTO list_version (id, list_code, seq, status, parser_version,
                    fetched_at, loaded_by, file_sha256, file_size, storage_key,
                    entry_count, added, changed, removed)
                VALUES (:id, 'UN_CONSOLIDATED', 3, 'ACTIVE', 'un-xml/1', :at,
                    'cli:test', :sha, 10, 'lists/x', 1003, 0, 0, 0)
                """
            ),
            {"id": uuid7(), "at": now, "sha": b"\x01" * 32},
        )
    result = runner.invoke(app, ["lists", "status"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "EU_FSF           enabled   no version  last success never  next 12:00Z  stale",
        "MX_SAT_69B       enabled   no version  last success never  next 12:00Z  stale",
        (
            "OFAC_SDN         enabled   no version  last success 2026-10-19T11:50Z  "
            "next 2026-10-20T01:00Z  failing: HTTP_503 (2)"
        ),
        "UK_FCDO          disabled  no version  last success never  next 12:00Z  stale",
        (
            "UN_CONSOLIDATED  enabled   v3  1003 entries  "
            "last success 2026-10-19T12:00Z  next 12:15Z  ok"
        ),
    ]


def test_lists_status_with_no_sources_says_so(admin_engine: Engine):
    result = runner.invoke(app, ["lists", "status"])
    assert result.exit_code == 0, result.output
    assert result.output == "no list sources; run xolotl lists seed\n"


# --- lists: offline ingestion and versions --------------------------------------------

UN_V1_FILE = Path("tests/modules/screening/fixtures/un/consolidated.xml")
UN_V2_FILE = Path("tests/modules/screening/catalog/fixtures/un_v2.xml")


@pytest.fixture
def list_objects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    objects = tmp_path / "objects"
    monkeypatch.setattr(
        "xolotl.cli.get_settings",
        lambda: get_settings().model_copy(update={"object_store_root": objects}),
    )
    return objects


def test_lists_load_file_ingests_then_reports_unchanged(
    admin_engine: Engine, list_objects: Path
):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    first = runner.invoke(
        app, ["lists", "load-file", "UN_CONSOLIDATED", str(UN_V1_FILE)]
    )
    assert first.exit_code == 0, first.output
    assert first.stdout == (
        "UN_CONSOLIDATED v1: 30 entries (30 added, 0 changed, 0 removed, "
        "0 unchanged) [un-xml/1]\n"
    )
    second = runner.invoke(
        app, ["lists", "load-file", "UN_CONSOLIDATED", str(UN_V2_FILE)]
    )
    assert second.exit_code == 0, second.output
    assert second.stdout == (
        "UN_CONSOLIDATED v2: 30 entries (1 added, 1 changed, 1 removed, "
        "28 unchanged) [un-xml/1]\n"
    )
    again = runner.invoke(
        app, ["lists", "load-file", "UN_CONSOLIDATED", str(UN_V2_FILE)]
    )
    assert again.exit_code == 0, again.output
    assert again.stdout == "UN_CONSOLIDATED: unchanged (same file as v2)\n"
    stored = sorted(p.name for p in list_objects.rglob("*.xml"))
    assert len(stored) == 2


def test_lists_load_file_refuses_a_mass_removal_without_the_flag(
    admin_engine: Engine,
    list_objects: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["lists", "load-file", "UN_CONSOLIDATED", str(UN_V1_FILE)]
        ).exit_code
        == 0
    )
    # The UN fixture without its entities: 8 of 30 entries removed.
    text_v1 = UN_V1_FILE.read_text(encoding="utf-8")
    start, end = text_v1.index("<ENTITIES>"), text_v1.index("</ENTITIES>")
    smaller = tmp_path / "smaller.xml"
    _ = smaller.write_text(
        text_v1[:start] + "<ENTITIES>" + text_v1[end:], encoding="utf-8"
    )
    refused = runner.invoke(
        app, ["lists", "load-file", "UN_CONSOLIDATED", str(smaller)]
    )
    assert refused.exit_code == 1
    assert refused.stderr == "error: LIST_MASS_REMOVAL: removed 8 of 30\n"
    accepted = runner.invoke(
        app,
        ["lists", "load-file", "UN_CONSOLIDATED", str(smaller), "--accept-drop"],
    )
    assert accepted.exit_code == 0, accepted.output
    assert accepted.stdout.startswith("UN_CONSOLIDATED v2: 22 entries (0 added, ")
    [warning] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warning.getMessage() == (
        f"cli:{getpass.getuser()} accepted a mass removal on UN_CONSOLIDATED v2: "
        "removed 8 of 30"
    )


def test_lists_load_file_logs_no_warning_for_an_accepted_drop_under_the_ratio(
    admin_engine: Engine, list_objects: Path, caplog: pytest.LogCaptureFixture
):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    for path in (UN_V1_FILE, UN_V2_FILE):
        loaded = runner.invoke(
            app, ["lists", "load-file", "UN_CONSOLIDATED", str(path), "--accept-drop"]
        )
        assert loaded.exit_code == 0, loaded.output
    # v2 removes 1 of 30 entries, under the guard's ratio: nothing was overridden.
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_lists_load_file_logs_the_orphan_when_it_fails_after_the_put(
    admin_engine: Engine,
    list_objects: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0

    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "entries failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "xolotl.modules.screening.catalog.ingest._write_entries", broken
    )
    result = runner.invoke(
        app, ["lists", "load-file", "UN_CONSOLIDATED", str(UN_V1_FILE)]
    )
    assert isinstance(result.exception, RuntimeError)
    [error] = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error.getMessage() == (
        "loading UN_CONSOLIDATED failed; a raw file under lists/UN_CONSOLIDATED/ "
        "in the shared namespace may be orphaned"
    )
    assert len(list(list_objects.rglob("*.xml"))) == 1
    shown = runner.invoke(app, ["lists", "show", "UN_CONSOLIDATED"])
    assert shown.stderr == "error: NOT_FOUND: no active UN_CONSOLIDATED version\n"


def test_lists_load_file_reports_a_format_error_in_one_line(
    admin_engine: Engine, list_objects: Path, tmp_path: Path
):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    broken = tmp_path / "broken.xml"
    _ = broken.write_bytes(UN_V1_FILE.read_bytes()[:1000])
    result = runner.invoke(app, ["lists", "load-file", "UN_CONSOLIDATED", str(broken)])
    assert result.exit_code == 1
    [line] = result.stderr.splitlines()
    assert line.startswith("error: LIST_FORMAT_INVALID: ")


def test_lists_show_prints_the_active_version(admin_engine: Engine, list_objects: Path):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    for path in (UN_V1_FILE, UN_V2_FILE):
        loaded = runner.invoke(
            app, ["lists", "load-file", "UN_CONSOLIDATED", str(path)]
        )
        assert loaded.exit_code == 0, loaded.output
    sha_v2 = hashlib.sha256(UN_V2_FILE.read_bytes()).hexdigest()[:12]
    actor = f"cli:{getpass.getuser()}"
    shown = runner.invoke(app, ["lists", "show", "UN_CONSOLIDATED"])
    assert shown.exit_code == 0, shown.output
    lines = shown.stdout.splitlines()
    assert lines[0] == "UN_CONSOLIDATED v2  ACTIVE"
    assert lines[1] == "published  2026-10-20T23:00Z"
    assert lines[2].startswith("fetched    ")
    assert lines[3:] == [
        f"loaded by  {actor}",
        "parser     un-xml/1",
        f"sha256     {sha_v2}",
        "entries    30 (1 added, 1 changed, 1 removed)",
    ]
    first = runner.invoke(app, ["lists", "show", "UN_CONSOLIDATED", "--version", "1"])
    assert first.exit_code == 0, first.output
    assert first.stdout.splitlines()[0] == "UN_CONSOLIDATED v1  SUPERSEDED"
    assert (
        first.stdout.splitlines()[-1]
        == "entries    30 (30 added, 0 changed, 0 removed)"
    )
    missing = runner.invoke(app, ["lists", "show", "UN_CONSOLIDATED", "--version", "9"])
    assert missing.exit_code == 1
    assert missing.stderr == "error: NOT_FOUND: no UN_CONSOLIDATED version 9\n"
    none = runner.invoke(app, ["lists", "show", "OFAC_SDN"])
    assert none.exit_code == 1
    assert none.stderr == "error: NOT_FOUND: no active OFAC_SDN version\n"


# --- lists: one immediate network fetch -----------------------------------------------

UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"


def test_lists_fetch_is_refused_without_force_network_outside_prod(
    admin_engine: Engine, list_objects: Path
):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(UN_URL).respond(200, content=UN_V1_FILE.read_bytes())
        result = runner.invoke(app, ["lists", "fetch", "UN_CONSOLIDATED"])
    assert result.exit_code == 2
    assert result.stderr == (
        "error: network fetching is off (XOLOTL_LISTS_FETCH_ENABLED); "
        "pass --force-network\n"
    )
    assert route.call_count == 0


def test_lists_fetch_with_force_network_ingests(
    admin_engine: Engine, list_objects: Path
):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    with respx.mock(assert_all_called=False) as mock:
        _ = mock.get(UN_URL).respond(
            200, content=UN_V1_FILE.read_bytes(), headers={"ETag": '"v1"'}
        )
        result = runner.invoke(
            app, ["lists", "fetch", "UN_CONSOLIDATED", "--force-network"]
        )
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            "UN_CONSOLIDATED: ingested v1 (30 added, 0 changed, 0 removed)\n"
        )
        _ = mock.get(UN_URL).respond(304)
        again = runner.invoke(
            app, ["lists", "fetch", "UN_CONSOLIDATED", "--force-network"]
        )
    assert again.exit_code == 0, again.output
    assert again.stdout == "UN_CONSOLIDATED: not modified\n"
    shown = runner.invoke(app, ["lists", "show", "UN_CONSOLIDATED"])
    assert f"loaded by  cli:{getpass.getuser()}" in shown.stdout
    with admin_engine.connect() as conn:
        holder = conn.scalar(
            text("SELECT lease_holder FROM list_source WHERE code = 'UN_CONSOLIDATED'")
        )
    assert holder is None


def test_lists_fetch_reports_a_failure(admin_engine: Engine, list_objects: Path):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    with respx.mock(assert_all_called=False) as mock:
        _ = mock.get(UN_URL).respond(503)
        result = runner.invoke(
            app, ["lists", "fetch", "UN_CONSOLIDATED", "--force-network"]
        )
    assert result.exit_code == 1
    assert result.stderr == (
        "error: UN_CONSOLIDATED: failed HTTP_503: the server answered 503\n"
    )


def test_lists_fetch_reports_a_busy_lease(admin_engine: Engine, list_objects: Path):
    assert runner.invoke(app, ["lists", "seed"]).exit_code == 0
    lease = (
        "UPDATE list_source SET lease_until = now() + interval '1 hour', "
        "lease_holder = 'job:other' WHERE code = 'UN_CONSOLIDATED'"
    )
    with admin_engine.begin() as conn:
        _ = conn.execute(text(lease))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(UN_URL).respond(200, content=UN_V1_FILE.read_bytes())
        result = runner.invoke(
            app, ["lists", "fetch", "UN_CONSOLIDATED", "--force-network"]
        )
    assert result.exit_code == 1
    assert result.stderr == "error: UN_CONSOLIDATED is being fetched by job:other\n"
    assert route.call_count == 0
    unknown = runner.invoke(app, ["lists", "fetch", "NOPE", "--force-network"])
    assert unknown.exit_code == 1
    assert unknown.stderr == "error: NOT_FOUND: no enabled list source NOPE\n"


# ---- screening -------------------------------------------------------------------


def test_screening_help_lists_check_and_replay():
    result = runner.invoke(
        app,
        ["screening", "--help"],
        env={"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"},
    )
    assert result.exit_code == 0
    assert "check" in result.stdout
    assert "replay" in result.stdout


def test_screening_check_and_replay(admin_engine: Engine, tmp_path: Path):
    created = new_tenant()
    seed_sources(admin_engine)
    _ = load_shared(
        "OFAC_SDN",
        [
            ListEntry(
                list_code="OFAC_SDN",
                entry_key="7001",
                entity_type=EntityType.PERSON,
                primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
                source_ref="sdn 7001",
            )
        ],
        store=LocalFsObjectStore(tmp_path / "objects"),
    )
    assign_itf(created, admin_engine)
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        party = party_service.create_party(
            s,
            PartyCreateRequest(
                party_type=PartyType.PERSON,
                person=PersonInput(
                    given_names="Juan Carlos",
                    first_surname="Pérez",
                    second_surname="Gómez",
                ),
            ),
        )
    set_index_holder(None)
    try:
        checked = runner.invoke(
            app, ["screening", "check", "--tenant", "acme", "--party", str(party.id)]
        )
        assert checked.exit_code == 0, checked.output
        match = re.fullmatch(
            r"run ([0-9a-f-]{36}): PENDING_REVIEW \(1 hits, 1 new findings\)\n",
            checked.stdout,
        )
        assert match is not None, checked.stdout

        replayed = runner.invoke(
            app, ["screening", "replay", match.group(1), "--tenant", "acme"]
        )
        assert replayed.exit_code == 0, replayed.output
        lines = replayed.stdout.splitlines()
        assert lines == [
            "OFAC_SDN 7001 1.0000 ALERT -> 1.0000 ALERT same",
            "identical",
        ]

        with staff_session(created.tenant_id, created.admin_user_id) as s:
            _ = party_service.add_identifier(
                s,
                party.id,
                IdentifierInput(id_type=IdentifierType.PASSPORT, value="G12345678"),
            )
        changed = runner.invoke(
            app, ["screening", "replay", match.group(1), "--tenant", "acme"]
        )
        assert changed.exit_code == 1
        assert changed.stdout == "subject changed\n"

        unknown = runner.invoke(
            app, ["screening", "check", "--tenant", "nope", "--party", str(party.id)]
        )
        assert unknown.exit_code == 1
        assert unknown.stderr == "error: NOT_FOUND: no tenant 'nope'\n"
    finally:
        set_index_holder(None)


def test_screening_screen_counterparties_screens_every_new_counterparty(
    admin_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    created = new_tenant()
    seed_sources(admin_engine)
    _ = load_shared(
        "OFAC_SDN",
        [
            ListEntry(
                list_code="OFAC_SDN",
                entry_key="7001",
                entity_type=EntityType.PERSON,
                primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
                source_ref="sdn 7001",
            )
        ],
        store=LocalFsObjectStore(tmp_path / "objects"),
    )
    assign_itf(created, admin_engine)
    # Two pages of two, and a third that comes up short.
    monkeypatch.setattr(get_settings(), "monitoring_counterparty_screen_batch", 2)
    with db.tenant_session(system_ctx(created.tenant_id)) as s:
        juan = resolve(
            s,
            CounterpartyInput(
                kind=CounterpartyKind.PERSON, name="Juan Carlos Pérez Gómez"
            ),
        ).id
        for name in ("Comercial Uno", "Comercial Dos", "Comercial Tres"):
            _ = resolve(s, CounterpartyInput(name=name))
    set_index_holder(None)
    try:
        result = runner.invoke(
            app, ["screening", "screen-counterparties", "--tenant", "acme"]
        )
        assert result.exit_code == 0, result.output
        assert result.stdout == "screened 4 counterparties for tenant acme\n"
        with db.tenant_session(system_ctx(created.tenant_id)) as s:
            rows = s.scalars(select(Counterparty)).all()
            runs = s.scalars(select(ScreeningRun)).all()
        assert all(r.screened_at is not None for r in rows)
        assert {
            r.id
            for r in rows
            if r.screening_status is not CounterpartyScreeningStatus.CLEAR
        } == {juan}
        assert {(r.trigger_type, r.trigger_ref) for r in runs} == {
            (TriggerType.CLI, f"cli:{getpass.getuser()}")
        }

        again = runner.invoke(
            app, ["screening", "screen-counterparties", "--tenant", "acme"]
        )
        assert again.stdout == "screened 0 counterparties for tenant acme\n"

        unknown = runner.invoke(
            app, ["screening", "screen-counterparties", "--tenant", "nope"]
        )
        assert unknown.exit_code == 1
        assert unknown.stderr == "error: NOT_FOUND: no tenant 'nope'\n"
    finally:
        set_index_holder(None)


def test_screening_rescreen_help_needs_no_database():
    result = runner.invoke(
        app,
        ["screening", "rescreen", "--help"],
        env={"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"},
    )
    assert result.exit_code == 0
    assert "--tenant" in result.stdout
    assert "PERIODIC or REINDEX" in result.stdout


def _rescreen_batches(tenant_id: UUID) -> list[ScreeningBatch]:
    with db.tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(ScreeningBatch).order_by(
                    ScreeningBatch.started_at, ScreeningBatch.id
                )
            )
        )


def test_screening_rescreen_starts_a_batch_for_the_worker(engine: Engine):
    created = new_tenant()
    factory = db.get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    try:
        register_rescreen(queue=queue)
        started = runner.invoke(app, ["screening", "rescreen", "--tenant", "acme"])
        assert started.exit_code == 0, started.output
        (periodic,) = _rescreen_batches(created.tenant_id)
        assert started.stdout == f"batch {periodic.id} started for tenant acme\n"
        assert (periodic.kind, periodic.ref[:7]) == (BatchKind.PERIODIC, "manual:")

        reindex = runner.invoke(
            app, ["screening", "rescreen", "--tenant", "acme", "--kind", "REINDEX"]
        )
        assert reindex.exit_code == 0, reindex.output
        batch = _rescreen_batches(created.tenant_id)[1]
        assert reindex.stdout == f"batch {batch.id} started for tenant acme\n"
        # The worker walks both; with no party each completes on its first page.
        walker = JobRunner(
            queue,
            factory,
            worker_id="w-cli",
            kinds=[PERIODIC_RESCREEN_KIND, REINDEX_KIND],
        )
        assert walker.run_once() == 2
        assert {b.status for b in _rescreen_batches(created.tenant_id)} == {
            BatchStatus.COMPLETED
        }

        again = runner.invoke(
            app, ["screening", "rescreen", "--tenant", "acme", "--kind", "REINDEX"]
        )
        assert again.exit_code == 0, again.output
        assert again.stdout == "nothing to do\n"

        wrong = runner.invoke(
            app, ["screening", "rescreen", "--tenant", "acme", "--kind", "DELTA"]
        )
        assert wrong.exit_code == 1
        assert wrong.stderr == (
            "error: VALIDATION_FAILED: kind must be PERIODIC or REINDEX\n"
        )
        # A batch kind a person cannot start is refused as well.
        private = runner.invoke(
            app, ["screening", "rescreen", "--tenant", "acme", "--kind", "PRIVATE_LIST"]
        )
        assert private.exit_code == 1
        assert private.stderr == (
            "error: VALIDATION_FAILED: kind must be PERIODIC or REINDEX\n"
        )

        unknown = runner.invoke(app, ["screening", "rescreen", "--tenant", "nope"])
        assert unknown.exit_code == 1
        assert unknown.stderr == "error: NOT_FOUND: no tenant 'nope'\n"
    finally:
        reset_subscribers()


def test_screening_rescreen_runs_carry_the_cli_user(admin_engine: Engine):
    created = new_tenant()
    seed_sources(admin_engine)
    assign_itf(created, admin_engine)
    factory = db.get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    set_index_holder(None)
    try:
        register_rescreen(queue=queue)
        with staff_session(created.tenant_id, created.admin_user_id) as s:
            party = party_service.create_party(
                s,
                PartyCreateRequest(
                    party_type=PartyType.PERSON,
                    person=PersonInput(
                        given_names="Juan Carlos",
                        first_surname="Pérez",
                        second_surname="Gómez",
                    ),
                ),
            )
        for kind in ("PERIODIC", "REINDEX"):
            started = runner.invoke(
                app, ["screening", "rescreen", "--tenant", "acme", "--kind", kind]
            )
            assert started.exit_code == 0, started.output
        # The worker screens with the holder it wires as it starts.
        settings = get_settings()
        set_index_holder(
            ScreeningIndexHolder(
                factory,
                refresh_seconds=settings.screening_index_refresh_seconds,
                private_shards=settings.screening_private_shards,
            )
        )
        walker = JobRunner(
            queue,
            factory,
            worker_id="w-cli",
            kinds=[PERIODIC_RESCREEN_KIND, REINDEX_KIND],
        )
        assert walker.run_once() == 2

        with db.tenant_session(system_ctx(created.tenant_id)) as s:
            runs = s.execute(
                select(
                    ScreeningRun.mode,
                    ScreeningRun.trigger_type,
                    ScreeningRun.trigger_ref,
                )
                .join(ScreeningSubject, ScreeningRun.subject_id == ScreeningSubject.id)
                .where(ScreeningSubject.party_id == party.id)
                .order_by(ScreeningRun.mode)
            ).all()
        assert [tuple(r) for r in runs] == [
            (RunMode.PERIODIC, TriggerType.CLI, ACTOR),
            (RunMode.REINDEX, TriggerType.CLI, ACTOR),
        ]
    finally:
        reset_subscribers()
        set_index_holder(None)


SCREENING_TESTS = Path(__file__).parent / "modules" / "screening"


def _corpus_slice(root: Path, *, recall_floor: str = "0.98") -> Path:
    """Write a small evaluation corpus under ``root``.

    It holds the library's fixtures, one generated pair per entry with a fixed seed
    and the first 20 hard negatives.

    Returns:
        The corpus directory to pass to ``--corpus``.

    """
    _ = shutil.copytree(SCREENING_TESTS / "fixtures", root / "fixtures")
    corpus = root / "corpus"
    corpus.mkdir()
    negatives = (SCREENING_TESTS / "corpus" / "hard_negatives.csv").read_text(
        encoding="utf-8"
    )
    rows = [line for line in negatives.splitlines() if not line.startswith("#")]
    _ = (corpus / "hard_negatives.csv").write_text(
        "\n".join(rows[:21]) + "\n", encoding="utf-8"
    )
    floors = [
        "seed: 7",
        "per_entry: 1",
        "min_pairs: 1",
        "min_hard_negatives: 1",
        "top_k: 50",
        'thresholds: {alert: "0.70", review: "0.55"}',
        "floors:",
        f"  SANCTION: {{recall: {recall_floor}, retrieval_recall: 0.995}}",
        "  FISCAL: {recall: 0.98}",
        "max_alerts_per_1000_negatives: 2.0",
    ]
    _ = (corpus / "floors.yaml").write_text("\n".join(floors) + "\n", encoding="utf-8")
    return corpus


def test_screening_eval_reports_every_kind_and_floor(tmp_path: Path, monkeypatch):
    target = tmp_path / "evaluation.json"
    monkeypatch.setattr(evaluation_report, "SHIPPED_REPORT", target)
    corpus = _corpus_slice(tmp_path / "screening")

    result = runner.invoke(app, ["screening", "eval", "--corpus", str(corpus)])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("corpus ")
    assert "SANCTION recall_review 1.0000" in lines
    assert "FISCAL precision_alert -" in lines
    assert "ALL alerts_per_1000_negatives 0.0000" in lines
    assert [line for line in lines if line.startswith("FLOOR ")] == [
        "FLOOR recall FISCAL 0.9800 1.0000 OK",
        "FLOOR recall SANCTION 0.9800 1.0000 OK",
        "FLOOR retrieval_recall SANCTION 0.9950 1.0000 OK",
        "FLOOR alerts_per_1000_negatives ALL 2.0000 0.0000 OK",
    ]
    assert not target.exists()


def test_screening_eval_write_writes_the_canonical_report(tmp_path: Path, monkeypatch):
    target = tmp_path / "evaluation.json"
    monkeypatch.setattr(evaluation_report, "SHIPPED_REPORT", target)
    now = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(clock, "now", lambda: now)
    corpus = _corpus_slice(tmp_path / "screening")

    result = runner.invoke(
        app, ["screening", "eval", "--corpus", str(corpus), "--write"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == f"wrote {target}"
    written = target.read_text(encoding="utf-8")
    report = evaluation_report.EvaluationReportResponse.model_validate_json(written)
    assert evaluation_report.canonical_json(report) == written
    assert report.generated_at == now
    assert report.matcher_version == MATCHER_VERSION
    assert all(floor.met for floor in report.floors)


def test_screening_eval_below_a_floor_exits_1_and_refuses_to_write(
    tmp_path: Path, monkeypatch
):
    target = tmp_path / "evaluation.json"
    monkeypatch.setattr(evaluation_report, "SHIPPED_REPORT", target)
    corpus = _corpus_slice(tmp_path / "screening", recall_floor="1.5")

    checked = runner.invoke(app, ["screening", "eval", "--corpus", str(corpus)])
    assert checked.exit_code == 1
    assert "FLOOR recall SANCTION 1.5000 1.0000 BELOW" in checked.stdout.splitlines()

    written = runner.invoke(
        app, ["screening", "eval", "--corpus", str(corpus), "--write"]
    )
    assert written.exit_code == 1
    assert written.stderr == (
        "error: FLOOR_BELOW: refusing to write a report below a floor\n"
    )
    assert not target.exists()


def test_screening_eval_without_a_corpus_says_so(tmp_path: Path):
    missing = tmp_path / "nowhere"

    result = runner.invoke(app, ["screening", "eval", "--corpus", str(missing)])

    assert result.exit_code == 1
    assert result.stderr == f"error: NOT_FOUND: no evaluation corpus at {missing}\n"


def test_screening_eval_refuses_a_corpus_dir_not_named_corpus(tmp_path: Path):
    corpus = _corpus_slice(tmp_path / "screening")
    renamed = corpus.rename(corpus.with_name("slice"))

    result = runner.invoke(app, ["screening", "eval", "--corpus", str(renamed)])

    assert result.exit_code == 1
    assert result.stderr == f"error: NOT_FOUND: no evaluation corpus at {renamed}\n"


def test_monitoring_process_drains_a_party_and_reports_counts(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    _ = monitoring_support.itf_tenant(tenant, admin_engine)
    customer = monitoring_support.add_customer(tenant, "cust-1")
    other = monitoring_support.add_customer(tenant, "cust-2")
    # 2026-01-20 has every unit value of the fixture; 2026-01-22 has no UDI.
    items = [
        monitoring_support.make_item(external_id=external_id, occurred_at=at, **extra)
        for external_id, at, extra in (
            ("tx-1", "2026-01-20T12:00:00Z", {}),
            ("tx-2", "2026-01-22T12:00:00Z", {}),
            ("tx-3", "2026-01-22T13:00:00Z", {}),
            ("tx-4", "2026-01-20T12:00:00Z", {"customer_ref": "cust-2"}),
        )
    ]
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: datetime(2026, 1, 22, 20, tzinfo=UTC))
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            report = monitoring_support.ingest_items(
                s, items, queue=PostgresJobQueue(db.get_sessionmaker())
            )
    parked = report.transaction_ids[1]

    one = runner.invoke(
        app,
        [
            "monitoring",
            "process",
            "--tenant",
            tenant.slug,
            "--party",
            str(other.party_id),
        ],
    )
    assert one.exit_code == 0, one.output
    assert one.stdout == f"party {other.party_id}: 1 processed, 0 parked\n"

    every = runner.invoke(app, ["monitoring", "process", "--tenant", tenant.slug])
    assert every.exit_code == 0, every.output
    assert every.stdout == (
        f"party {customer.party_id}: 1 processed, 1 parked (stopped at {parked})\n"
    )


# ---- monitoring ingest-file ----------------------------------------------------------

MONITORING_FIXTURES = Path("tests/modules/monitoring/txn/fixtures")


def _transaction_files(tenant_id: UUID) -> list[TransactionFile]:
    with db.tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(TransactionFile)))


def test_monitoring_ingest_file_uploads_and_processes_synchronously(
    engine: Engine, list_objects: Path
):
    created = new_tenant()
    _ = add_customer(created, "cust-1")
    result = runner.invoke(
        app,
        [
            "monitoring",
            "ingest-file",
            "--tenant",
            "acme",
            str(MONITORING_FIXTURES / "bad_rows.csv"),
        ],
    )
    assert result.exit_code == 0, result.output
    (row,) = _transaction_files(created.tenant_id)
    assert result.stdout == (
        f"file {row.id}: 10 rows, 8 accepted, 0 duplicates, 2 rejected, COMPLETED "
        f"(errors: transactions/files/{row.id}.errors.csv)\n"
    )
    assert row.status is FileStatus.COMPLETED
    assert row.uploaded_by.startswith("cli:")
    report = (
        list_objects
        / str(created.tenant_id)
        / f"transactions/files/{row.id}.errors.csv"
    )
    assert report.read_bytes() == (
        b"row,column,code\n4,amount,INVALID\n8,customer_ref,CUSTOMER_REF_UNKNOWN\n"
    )
    with db.tenant_session(system_ctx(created.tenant_id)) as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 8


def test_monitoring_ingest_file_reports_an_already_uploaded_file(
    engine: Engine, list_objects: Path, tmp_path: Path
):
    created = new_tenant()
    _ = add_customer(created, "cust-1")
    jsonl = MONITORING_FIXTURES / "deposits.jsonl"
    first = runner.invoke(
        app, ["monitoring", "ingest-file", "--tenant", "acme", str(jsonl)]
    )
    assert first.exit_code == 0, first.output
    (row,) = _transaction_files(created.tenant_id)
    assert first.stdout == (
        f"file {row.id}: 4 rows, 4 accepted, 0 duplicates, 0 rejected, COMPLETED\n"
    )
    # The same bytes under another name, typed by hand.
    renamed = tmp_path / "deposits.txt"
    _ = renamed.write_bytes(jsonl.read_bytes())
    again = runner.invoke(
        app,
        [
            "monitoring",
            "ingest-file",
            "--tenant",
            "acme",
            "--content-type",
            "application/x-ndjson",
            str(renamed),
        ],
    )
    assert again.exit_code == 0, again.output
    assert again.stdout == f"file {row.id}: already uploaded (COMPLETED)\n"
    untyped = runner.invoke(
        app, ["monitoring", "ingest-file", "--tenant", "acme", str(renamed)]
    )
    assert untyped.exit_code == 1
    assert "pass --content-type" in untyped.stderr


def test_monitoring_ingest_file_that_loses_the_file_to_another_run_says_so(
    engine: Engine, list_objects: Path, monkeypatch: pytest.MonkeyPatch
):
    created = new_tenant()
    _ = add_customer(created, "cust-1")
    real_rows = files.iter_rows

    def another_run_moves_the_cursor(
        data: bytes, content_type: str, *, start: int, limit: int
    ) -> Generator[files.FileRow]:
        with db.tenant_session(system_ctx(created.tenant_id)) as s:
            (file,) = s.scalars(select(TransactionFile))
            file.cursor = 2
        return real_rows(data, content_type, start=start, limit=limit)

    monkeypatch.setattr(files, "iter_rows", another_run_moves_the_cursor)
    result = runner.invoke(
        app,
        [
            "monitoring",
            "ingest-file",
            "--tenant",
            "acme",
            str(MONITORING_FIXTURES / "deposits.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output
    (row,) = _transaction_files(created.tenant_id)
    assert result.stdout == (
        f"file {row.id}: 4 rows, 0 accepted, 0 duplicates, 0 rejected, PROCESSING\n"
    )
