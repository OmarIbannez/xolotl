import base64
import getpass
import json
import os
import re
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn
from uuid import UUID

import psycopg
import pytest
import uvicorn
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, event, make_url, text
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
    wrapped_keys,
)
from xolotl.cli import app
from xolotl.modules.tenants import service
from xolotl.platform import db, migrate
from xolotl.platform.config import DEV_MASTER_KEY, get_settings
from xolotl.platform.crypto import (
    KeyDestroyedError,
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.enums import KeyPurpose

runner = CliRunner()

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
