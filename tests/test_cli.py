import re

import uvicorn
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from tests.helpers import auth_session
from xolotl.cli import app
from xolotl.modules.tenants import service
from xolotl.platform import db, migrate

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


def test_tenant_list_and_audit_verify(engine: Engine):
    assert _create().exit_code == 0
    listed = runner.invoke(app, ["tenant", "list"])
    assert listed.exit_code == 0
    assert "cli-demo\tSANDBOX\tACTIVE" in listed.output
    verified = runner.invoke(app, ["audit", "verify", "--tenant", "cli-demo"])
    assert verified.exit_code == 0, verified.output
    assert '"valid": true' in verified.output
    assert '"entries_checked": 2' in verified.output
    unknown = runner.invoke(app, ["audit", "verify", "--tenant", "nope"])
    assert unknown.exit_code == 1


def test_audit_verify_fails_on_a_tampered_chain(engine: Engine, admin_engine: Engine):
    assert _create().exit_code == 0
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(text("UPDATE audit_event SET action = 'forged' WHERE seq = 1"))
    broken = runner.invoke(app, ["audit", "verify", "--tenant", "cli-demo"])
    assert broken.exit_code == 1
    assert '"valid": false' in broken.output


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
