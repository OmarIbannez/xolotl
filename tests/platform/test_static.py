"""The API process serves the built console, with a single-page fallback (plan 1d)."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from xolotl.app import build_app
from xolotl.platform.api.app import create_app
from xolotl.platform.api.static import mount_console
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker

INDEX_HTML = "<!doctype html><title>xolotl console</title><div id=root></div>"
APP_JS = "console.log('xolotl console');"
FAVICON = "<svg xmlns='http://www.w3.org/2000/svg'/>"
SECRET = "kept outside the static directory"
# What the browser is told about the console. Spelled out here on purpose: loosening the
# policy should be a visible change to a test, not only to the constant.
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
}


@contextmanager
def _static_dir_setting(monkeypatch: pytest.MonkeyPatch, path: Path) -> Generator[None]:
    """Point ``XOLOTL_STATIC_DIR`` at ``path`` for the settings the app reads."""
    try:
        with monkeypatch.context() as patch:
            patch.setenv("XOLOTL_STATIC_DIR", str(path))
            get_settings.cache_clear()
            yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """Build a console as Vite leaves it, with a file beside it that must stay hidden.

    Returns:
        The directory to serve; ``secret.txt`` sits next to it, not inside.

    """
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    _ = (root / "index.html").write_text(INDEX_HTML)
    _ = (root / "assets" / "app.js").write_text(APP_JS)
    _ = (root / "favicon.svg").write_text(FAVICON)
    _ = (tmp_path / "secret.txt").write_text(SECRET)
    return root


@pytest.fixture
def api(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """Build the real application without a console, whatever the checkout holds.

    Yields:
        The application ``xolotl serve`` builds.

    """
    with _static_dir_setting(monkeypatch, tmp_path / "no-console"):
        yield build_app()


@pytest.fixture
def console(api: FastAPI, static_dir: Path) -> Iterator[TestClient]:
    assert mount_console(api, static_dir) is True
    # A server error comes back as a response, so a failure reads "500 != 200".
    with TestClient(api, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def configured_console(
    engine: Engine, static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Build the application the way ``xolotl serve`` does, from the environment.

    Yields:
        A client of the application with the console mounted from the setting.

    """
    with _static_dir_setting(monkeypatch, static_dir), TestClient(build_app()) as c:
        yield c


def test_the_shared_client_never_serves_a_console_build(client):
    # A build copied to src/xolotl/static (the documented place) must not leak into
    # the API tests: tests/conftest.py points XOLOTL_STATIC_DIR at a directory with
    # no build.
    resp = client.get("/")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"


def test_the_root_and_a_deep_link_return_the_index(console):
    for path in ("/", "/audit", "/api-keys", "/some/deeper/link"):
        resp = console.get(path)
        assert resp.status_code == 200, path
        assert resp.text == INDEX_HTML, path
        assert resp.headers["content-type"].startswith("text/html"), path


def test_the_index_is_revalidated_so_a_release_is_picked_up(console):
    # Hashed assets change name with every build; a cached index.html would keep
    # pointing at files the new image no longer has.
    assert console.get("/audit").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/", "/audit", "/some/deeper/link", "/favicon.svg", "/assets/app.js"]
)
def test_the_console_is_served_with_security_headers(console, path):
    resp = console.get(path)
    assert resp.status_code == 200
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers[name] == value, name


def test_head_carries_the_security_headers_too(console):
    resp = console.head("/")
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers[name] == value, name


@pytest.mark.parametrize(
    "path",
    ["/v1/docs", "/v1/openapi.json", "/healthz", "/readyz", "/v1/no/such/route"],
)
def test_the_api_does_not_get_the_consoles_policy(console, path):
    # The Swagger UI at /v1/docs loads its scripts from a CDN, which this policy would
    # block: the policy belongs to the console's own pages, not to the API's.
    assert "content-security-policy" not in console.get(path).headers


def test_a_build_asset_is_served_as_the_file(console):
    resp = console.get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.text == APP_JS
    assert "javascript" in resp.headers["content-type"]


def test_a_missing_asset_is_a_404_not_the_index(console):
    # A stale script URL must fail loudly instead of parsing HTML as JavaScript.
    resp = console.get("/assets/gone-0a1b2c.js")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "NOT_FOUND"


def test_a_file_at_the_root_of_the_build_is_served_as_itself(console):
    resp = console.get("/favicon.svg")
    assert resp.status_code == 200
    assert resp.text == FAVICON
    assert "svg" in resp.headers["content-type"]


def test_head_answers_like_get_without_a_body(console):
    resp = console.head("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.content == b""


def test_the_api_routes_are_never_shadowed(console):
    openapi = console.get("/v1/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == "xolotl"

    docs = console.get("/v1/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()

    health = console.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    ready = console.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "ok"}


def test_the_catch_all_stays_out_of_the_openapi_document(console):
    paths = console.get("/v1/openapi.json").json()["paths"]
    assert "/{full_path}" not in paths
    assert all(path.startswith(("/v1", "/healthz", "/readyz")) for path in paths)


@pytest.mark.parametrize(
    "path",
    ["/v1", "/v1/", "/v1/no/such/route", "/healthz/extra", "/readyz/extra"],
)
def test_an_unknown_api_path_is_a_problem_not_the_index(console, path):
    resp = console.get(path)
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["code"] == "NOT_FOUND"
    assert body["instance"] == path
    assert INDEX_HTML not in resp.text


@pytest.mark.parametrize("path", ["/v1x", "/v10/docs", "/healthzz", "/readyz.json"])
def test_only_whole_path_segments_are_reserved(console, path):
    # A console route that merely starts like an API path still gets the index.
    assert console.get(path).text == INDEX_HTML


def test_a_wrong_method_on_an_api_route_is_still_a_405(console):
    resp = console.get("/v1/auth/login")  # POST only
    assert resp.status_code == 405
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "METHOD_NOT_ALLOWED"
    assert resp.headers["allow"] == "POST"


def test_an_unknown_api_path_is_a_404_for_every_method(console):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        resp = console.request(method, "/v1/no/such/route")
        assert resp.status_code == 404, method
        assert resp.json()["code"] == "NOT_FOUND", method


def test_the_api_still_answers_posts(console):
    resp = console.post(
        "/v1/auth/login",
        json={"tenant_slug": "nobody", "email": "a@b.mx", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


# These reach the console's catch-all, which must answer the index. The client resolves
# literal dot segments before sending, so the first two never arrive as "..": they name
# the plan's example. Percent-encoded, the rest arrive decoded as "/../secret.txt",
# which is what uvicorn hands over for `curl --path-as-is /../secret.txt`.
CLIMBING_PATHS = [
    "/../secret.txt",
    "/assets/../../secret.txt",
    "/%2e%2e/secret.txt",
    "/%2e%2e%2fsecret.txt",
    "/..%5csecret.txt",
]
# Names no file system can hold. A name is limited to 255 bytes and a path to PATH_MAX,
# and CPython 3.13's pathlib raises OSError (ENAMETOOLONG) from is_file() for either; a
# NUL is a ValueError. macOS counts the characters of a name and Linux its bytes, hence
# the two-byte letters (130 of them are 260 bytes).
LONG_PATH = "/".join(["b" * 100] * 50)  # 5,050 characters in 50 segments
IMPOSSIBLE_NAMES = {
    "a name of 255 characters": "a" * 255,
    "a name of 256 characters": "a" * 256,
    "a name of 260 bytes": "%C3%A9" * 130,
    "a path of 5,050 characters": LONG_PATH,
    "a NUL": "%00",
    "a NUL inside a name": "a%00b",
    "a NUL inside a directory name": "dir%00/file.txt",
}


@pytest.mark.parametrize("path", CLIMBING_PATHS)
def test_a_path_that_climbs_out_of_the_build_is_the_index(console, path):
    resp = console.get(path)
    assert resp.status_code == 200
    assert resp.text == INDEX_HTML  # and so not the secret


@pytest.mark.parametrize("name", IMPOSSIBLE_NAMES.values(), ids=IMPOSSIBLE_NAMES.keys())
def test_a_name_that_cannot_exist_is_the_index_not_a_server_error(console, name):
    resp = console.get(f"/{name}")
    assert resp.status_code == 200
    assert resp.text == INDEX_HTML


@pytest.mark.parametrize("name", IMPOSSIBLE_NAMES.values(), ids=IMPOSSIBLE_NAMES.keys())
def test_a_name_that_cannot_exist_under_assets_is_a_404_problem(console, name):
    resp = console.get(f"/assets/{name}")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "path", ["/assets/%2e%2e/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"]
)
def test_a_path_under_assets_that_climbs_out_is_a_404_problem(console, path):
    resp = console.get(path)
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert SECRET not in resp.text


def test_an_absolute_path_cannot_reach_a_file_outside(console, static_dir):
    # "//abs/path" reaches the handler as full_path="/abs/path", and joining an
    # absolute path onto a directory replaces the directory.
    secret = static_dir.parent / "secret.txt"
    resp = console.get("/%2F" + str(secret).lstrip("/"))
    assert SECRET not in resp.text
    assert resp.text == INDEX_HTML


def test_a_symlink_out_of_the_static_directory_is_not_followed(console, static_dir):
    (static_dir / "leak.txt").symlink_to(static_dir.parent / "secret.txt")
    resp = console.get("/leak.txt")
    assert SECRET not in resp.text
    assert resp.text == INDEX_HTML


def test_without_an_index_nothing_is_mounted(engine, tmp_path):
    app = create_app(session_factory=get_sessionmaker())
    routes_before = len(app.routes)

    assert mount_console(app, tmp_path / "does-not-exist") is False

    assert len(app.routes) == routes_before
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "NOT_FOUND"


def test_a_build_without_an_index_is_not_mounted(engine, static_dir):
    (static_dir / "index.html").unlink()
    app = create_app(session_factory=get_sessionmaker())

    assert mount_console(app, static_dir) is False

    with TestClient(app) as client:
        assert client.get("/assets/app.js").status_code == 404
        assert client.get("/").status_code == 404


def test_an_index_that_is_a_directory_is_not_mounted(engine, tmp_path):
    (tmp_path / "index.html").mkdir()
    app = create_app(session_factory=get_sessionmaker())

    assert mount_console(app, tmp_path) is False


def test_a_build_without_an_assets_directory_still_serves_the_index(engine, tmp_path):
    _ = (tmp_path / "index.html").write_text(INDEX_HTML)
    app = create_app(session_factory=get_sessionmaker())

    assert mount_console(app, tmp_path) is True

    with TestClient(app) as client:
        assert client.get("/audit").text == INDEX_HTML


def test_the_application_mounts_the_console_from_xolotl_static_dir(
    configured_console,
):
    assert configured_console.get("/").text == INDEX_HTML
    assert configured_console.get("/assets/app.js").text == APP_JS
    assert configured_console.get("/v1/openapi.json").status_code == 200
    assert configured_console.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "built", [True, False], ids=["with-console", "without-console"]
)
def test_building_the_app_writes_nothing_to_stdout(
    engine, static_dir, tmp_path, monkeypatch, capsys, built
):
    # The OpenAPI document is exported with `print(json.dumps(build_app().openapi()))`
    # redirected to a file (console/README.md), so building the app must stay silent.
    where = static_dir if built else tmp_path / "no-console"
    with _static_dir_setting(monkeypatch, where):
        _ = build_app()
    assert not capsys.readouterr().out
