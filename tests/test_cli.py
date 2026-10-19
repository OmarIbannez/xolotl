import getpass
import re
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
import uvicorn
from sqlalchemy import Engine, text
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
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import (
    KeyDestroyedError,
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.enums import KeyPurpose

runner = CliRunner()


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

    def checked(target: Engine) -> None:
        calls.append("role check")
        db.assert_app_role_safe(target)  # the real check, against the app engine

    def run(target: str, **options: object) -> None:
        calls.append("uvicorn")
        served.update(options, target=target)

    monkeypatch.setattr(migrate, "upgrade", lambda: calls.append("upgrade"))
    monkeypatch.setattr("xolotl.cli.assert_app_role_safe", checked)
    monkeypatch.setattr(uvicorn, "run", run)
    result = runner.invoke(app, ["serve", "--port", "8123"])
    assert result.exit_code == 0, result.output
    assert calls == ["upgrade", "role check", "uvicorn"]
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


def test_db_bootstrap_reports_a_refused_alter_without_the_password(
    owner_without_admin_option: str,
):
    result = runner.invoke(app, ["db", "bootstrap"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception  # no traceback
    assert result.stderr == (
        f"error: could not set LOGIN and the password of role {db.APP_ROLE} "
        "(sqlstate 42501)\n"
    )
    assert owner_without_admin_option not in result.output


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


def test_keys_help_works_without_a_database():
    assert runner.invoke(app, ["keys", "--help"]).exit_code == 0
    rotate = runner.invoke(app, ["keys", "rotate", "--help"])
    assert rotate.exit_code == 0
    assert "--tenant" in rotate.output
    assert "--purpose" in rotate.output
    assert "DATA|BIOMETRIC" in rotate.output
    assert "INDEX" not in rotate.output  # accepted, then refused; never offered
    destroy = runner.invoke(app, ["keys", "destroy", "--help"])
    assert destroy.exit_code == 0
    assert "--tenant" in destroy.output
    assert "--yes" in destroy.output


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
