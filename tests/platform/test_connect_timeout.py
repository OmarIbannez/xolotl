"""Every engine connects with libpq's ``connect_timeout`` (``db.connect_args``).

A database that completes the TCP handshake and never answers (a hung server, a
firewall that drops packets) would otherwise hold ``serve`` and every command for
130 s per address, psycopg's own default; ``silent_server`` is one. Only the first
connection to it is sure to wait, because its backlog is one deep and what the
kernel does with the next connection differs by platform, so each test times the
connection it makes first.
"""

import time
from typing import Any, cast

import psycopg
import pytest
from sqlalchemy import Engine, make_url
from sqlalchemy.exc import OperationalError

from xolotl.platform import db, migrate
from xolotl.platform.config import Settings, get_settings

TIMEOUT = 2


def test_the_admin_engine_gives_up_after_the_connect_timeout(
    engine: Engine, silent_server: tuple[str, int], monkeypatch: pytest.MonkeyPatch
):
    host, port = silent_server
    admin_url = make_url(get_settings().database_admin_url).set(host=host, port=port)
    settings = get_settings().model_copy(
        update={
            "database_admin_url": admin_url.render_as_string(hide_password=False),
            "database_connect_timeout_seconds": TIMEOUT,
        }
    )
    # The suite's admin engine leaves the cache for this test and the next caller
    # rebuilds it; disposed first, so no pool is garbage collected with its
    # connections open (a ResourceWarning, and so an error here).
    db.get_admin_engine().dispose()
    db.get_admin_engine.cache_clear()
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    try:
        started = time.monotonic()
        with pytest.raises(OperationalError) as caught:
            _ = db.get_admin_engine().connect()
        elapsed = time.monotonic() - started
    finally:
        db.get_admin_engine.cache_clear()  # the next caller builds the suite's again
    assert elapsed < 8
    assert "timeout" in str(caught.value)


def test_the_app_role_probe_gives_up_after_the_connect_timeout(
    engine: Engine, silent_server: tuple[str, int], monkeypatch: pytest.MonkeyPatch
):
    host, port = silent_server
    # Settings the way Render's Blueprint gives them (render_shaped_settings in
    # test_ensure_app_role.py): the owner URL as a host hands it out and the app
    # password, no app URL, so the URL the probe signs in with is derived from the two.
    admin = make_url(get_settings().database_admin_url).set(
        drivername="postgres", host=host, port=port
    )
    monkeypatch.delenv("XOLOTL_DATABASE_URL")
    monkeypatch.setenv(
        "XOLOTL_DATABASE_ADMIN_URL", admin.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", "never-used-4711")
    monkeypatch.setenv("XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS", str(TIMEOUT))
    settings = cast("Any", Settings)(_env_file=None)
    assert settings.database_connect_timeout_seconds == TIMEOUT
    bootstrapped: list[None] = []
    monkeypatch.setattr(migrate, "get_settings", lambda: settings)
    monkeypatch.setattr(db, "get_settings", lambda: settings)  # connect_args reads it
    monkeypatch.setattr(migrate, "bootstrap", lambda: bootstrapped.append(None))
    started = time.monotonic()
    with pytest.raises(OperationalError) as caught:
        _ = migrate.ensure_app_role()
    elapsed = time.monotonic() - started
    assert elapsed < 8
    # A database that never answers is not a role that cannot sign in: the probe's
    # timeout is the end of it, and bootstrap (a second connect, a second timeout)
    # never runs.
    assert isinstance(caught.value.orig, psycopg.errors.ConnectionTimeout)
    assert bootstrapped == []
