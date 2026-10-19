# SP-01a — Repo, Tenancy, API Conventions, Auth, Audit Chain, CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `xolotl` repository with a Postgres-RLS multi-tenant kernel: tenant session contract, isolation harness, problem+json API conventions with idempotency and cursor pagination, staff login with TOTP, scoped API keys, the per-tenant hash-chained audit log, and the `xolotl` CLI.

**Architecture:** One Python package `xolotl` under `src/` with a `platform/` kernel that imports nothing from `modules/`, and a first feature module `modules/tenants`. Every tenant table carries `tenant_id` as the first primary-key column with `FORCE ROW LEVEL SECURITY` and exactly one permissive `tenant_isolation` policy; the application connects as `xolotl_app` (no `BYPASSRLS`, owns nothing) and sets `app.tenant_id`, `app.actor_type`, `app.actor_id`, `app.roles` with `set_config(..., true)` at the start of every transaction. FastAPI routes resolve a bearer token (staff JWT or `xk_` API key) into an `Actor`, open a session bound to that actor's `TenantContext`, and return RFC 9457 problems on every error. Tests run against a real Postgres 17 in testcontainers with real commits and a truncate-before-each-test fixture, so RLS is exercised across connections exactly as in production.

**Tech Stack:** Python 3.13, uv, hatchling, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2, Alembic, psycopg 3, PyJWT, argon2-cffi, pyotp, Typer, httpx; dev: ruff, ty, basedpyright, vulture, import-linter, pytest, pytest-cov, testcontainers[postgres].

**Spec:** `docs/superpowers/specs/2026-10-18-sp01-platform-kernel-design.md` (sections 1–7, 8.3, 10, 12, 13) under the program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md` (decisions D1–D3, D10, D20, D22, D23, addendum (a)).

## Global Constraints

- Python `>=3.13`; `uv` project (not a workspace); build backend `hatchling`; package `xolotl` under `src/`; CLI entry point `xolotl = "xolotl.cli:app"`.
- Runtime dependencies for 1a, exactly: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy>=2`, `alembic`, `psycopg[binary]`, `pyjwt[crypto]`, `argon2-cffi`, `pyotp`, `typer`, `httpx`. Dev: `ruff`, `ty`, `basedpyright`, `vulture`, `pytest`, `pytest-cov`, `testcontainers[postgres]`, `import-linter`.
- Quality gates copied from sentinel: ruff `select = ["ALL"]` with sentinel's ignores, line length 88, `ty` with `error-on-warning = true`, basedpyright `typeCheckingMode = "all"` with `reportAny`/`reportExplicitAny` as errors, vulture `min_confidence = 80`, pytest `--cov-fail-under=90`, `filterwarnings = ["error"]`. `make ci` runs every gate and reports every failure.
- Import-linter: `xolotl.platform` must not import `xolotl.modules`; neither `xolotl.platform` nor `xolotl.modules` may import `xolotl.app` or `xolotl.cli`.
- Enum convention: `StrEnum` with UPPERCASE values stored as non-native `String(32)` columns. Kernel enums live in `xolotl/platform/enums.py`.
- Environment prefix `XOLOTL_`; keys and defaults as in spec §3 (`DATABASE_URL` default `postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl`, `DATABASE_ADMIN_URL` default `postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl`, `ENV` default `dev`, `JWT_ISSUER` default `xolotl`, `ACCESS_TOKEN_TTL_MINUTES` 480, `MFA_TOKEN_TTL_MINUTES` 5, `PORT` 8000 also read from bare `PORT`, `LOG_LEVEL` INFO). `ENV=prod` refuses the default `JWT_SECRET`.
- Session contract (D1, spec §4.2): `set_config('app.tenant_id'|'app.actor_type'|'app.actor_id'|'app.roles', value, true)` on every transaction start; roles comma-joined with leading and trailing commas, e.g. `",ADMIN,MLRO,"`; `PLATFORM_OPERATOR` sets `app.tenant_id` to `''`.
- RLS predicate on every tenant table: `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid` (the `NULLIF` is required: casting `''` to `uuid` raises). `tenant` table policy: `id = NULLIF(current_setting('app.tenant_id', true), '')::uuid OR current_setting('app.actor_type', true) = 'PLATFORM_OPERATOR'`. Exactly one permissive policy named `tenant_isolation` per table; all other policies `AS RESTRICTIVE`.
- Every tenant table: `tenant_id uuid NOT NULL` first in the PK, `id uuid NOT NULL` (UUIDv7); the primary key `(tenant_id, id)` is the composite-FK target (Postgres discards a `UNIQUE (tenant_id, id)` that duplicates the PK, so none is emitted; a table whose PK is not `(tenant_id, id)` declares its own unique); tenant-to-tenant references are composite `FOREIGN KEY (tenant_id, x_id) REFERENCES x (tenant_id, id)`. Tables that are not tenant-isolated are listed in `SHARED_TABLES`, the authority for the harness (they may reference tenants but carry no row security); 1a registers `alembic_version` and `api_key_lookup`.
- API: prefix `/v1`; OpenAPI at `/v1/openapi.json`, docs at `/v1/docs`; operation ids are function names; errors are `application/problem+json` with `type` (`https://xolotl.dev/problems/<code>`), `title`, `status`, `detail`, `instance`, `code`, and `errors[]` for validation. Codes used in 1a: `CONFLICT` 409, `NOT_FOUND` 404, `FORBIDDEN`/`ROLE_FORBIDDEN`/`SCOPE_MISSING`/`TENANT_SUSPENDED`/`PASSWORD_CHANGE_REQUIRED` 403, `UNAUTHENTICATED` 401 with `WWW-Authenticate: Bearer`, `VALIDATION_FAILED` 422, `IDEMPOTENCY_KEY_REQUIRED` 400, `IDEMPOTENCY_KEY_REUSED` 422, `IDEMPOTENCY_IN_PROGRESS` 409 (`IdempotencyInProgressError`: the key's first request is still running), `METHOD_NOT_ALLOWED` 405, `INTERNAL` 500.
- Idempotency: every `POST` under `/v1` except `/v1/auth/*` requires `Idempotency-Key` (1–128 chars); scoped to `(tenant_id, actor_id, key)`; `request_hash = sha256(method, path, body)`; same hash replays the stored status and body with `Idempotent-Replay: true`; different hash → 422 `IDEMPOTENCY_KEY_REUSED`; entries expire after 24 hours.
- Pagination: `limit` 1–200 default 50, `cursor` = URL-safe base64 of `created_at|id`, ordering `(created_at DESC, id DESC)`, response `{items, next_cursor}`.
- Staff auth: argon2id passwords, new password ≥ 12 characters, lockout after 10 consecutive failures for 15 minutes, TOTP with `pyotp` (`valid_window=1`), HS256 JWT claims `iss, sub, tenant_id, tenant_slug, roles, actor_type, iat, exp, jti` plus `purpose` (`access` or `mfa`). Login responses never reveal whether the email exists.
- API keys: format `xk_<sbx|prd>_<8 hex>_<32 hex>` (48 characters), `key_prefix` = first 15 characters, `key_hash` = sha256 of the full key, constant-time comparison, `api_key.env` must equal `tenant.env`, `last_used_at` updated at most once per minute, scope grammar `<area>:<read|write>`.
- Audit (D10, spec §8.3): `audit_event` partitioned monthly by `at`; per-tenant chain with `seq`, `prev_hash`, `hash = sha256(canonical_json([tenant_id, seq, at, actor_type, actor_id, action, entity_type, entity_id, detail, prev_hash]))`; writers take `pg_advisory_xact_lock(hashtext(tenant_id::text)::bigint)`; triggers reject UPDATE, DELETE and TRUNCATE; genesis hash is 32 zero bytes.
- Logging: JSON lines; the `RedactionFilter` masks values of keys named like `password`, `token`, `secret`, `authorization`, `curp`, `rfc`, `clabe`, `email`, and any Pydantic field tagged `json_schema_extra={"pii": True}`.
- Commit after every task; messages `type(scope): summary`.

## Deviations from the sub-spec (decided here, carried back to the spec when 1a ships)

- `get_session` (the FastAPI dependency) lives in `xolotl/platform/auth/principal.py`, next to the actor resolution it depends on, not in `db.py`; `db.py` keeps `tenant_session()` for non-HTTP callers.
- `ensure_partitions` takes a SQLAlchemy `Connection` (migrations and CLI have one) rather than a `Session`.
- Tests use real transactions and truncate every table before each test instead of sentinel's savepoint rollback; the savepoint pattern cannot exercise RLS across connections or the advisory lock, and transaction-local GUCs behave differently inside savepoints.
- The CLI's `tenant create` accepts `--env SANDBOX|PROD` (default `SANDBOX`) because `tenant.env` is a column (program spec addendum (a)).
- basedpyright stays in `all` mode for `src/`; a `tests` execution environment turns off the `reportAny`/`reportExplicitAny`/`reportUnknown*`/`reportMissingParameterType`/`reportMissingTypeArgument`/`reportUnusedParameter`/`reportUnannotatedClassAttribute` rules there, because fixtures and raw SQL rows are untyped by nature. In `src/`, results of `text()` queries are wrapped in `typing.cast(...)`, instance and redefined class attributes carry annotations, and every overriding method is decorated with `typing.override`.
- The spec's `require_roles(*roles)` and `require_scope(scope)` are one dependency factory, `require_access(*, roles=(), scope=None, write: bool | None = None)`, because every route needs both halves of the rule (staff roles, machine scopes) and the AUDITOR_RO write ban. With `write=None` a write is inferred from a `:write` scope or a method other than GET, HEAD and OPTIONS; a read-only POST passes `write=False` (Task 9 ruling).
- `idempotency_key` has primary key `(tenant_id, actor_id, key)` (spec §5 wrote `(tenant_id, key)`, which contradicts the `(tenant_id, actor_id, key)` scope in §7 and Review Focus 1).

## Review Focus

Inputs the spec implies but did not spell out; each gets a test in the owning task.

1. Two different actors in the same tenant send the same `Idempotency-Key` with different bodies: both must succeed independently (scope is `(tenant_id, actor_id, key)`), never a 422 for the second actor. Test in Task 11.
2. A staff user whose account is disabled, or whose tenant is suspended, still holds a valid, unexpired JWT: the next request must fail with 401 `UNAUTHENTICATED` (disabled) or 403 `TENANT_SUSPENDED` (suspended), not succeed until expiry. Tests in Task 10 and Task 11.
3. A `PLATFORM_OPERATOR` session (`app.tenant_id = ''`) runs a query against a tenant table: it must return zero rows, not raise `invalid input syntax for type uuid`. Test in Task 4 (predicate) and Task 6 (behavioural).
4. Email and slug normalisation: `Admin@Acme.MX ` must log in as `admin@acme.mx`, and `Acme-Fintech` must resolve as `acme-fintech`; a second staff user with the same email in a different case is a 409 `CONFLICT`. Tests in Task 10.
5. A valid key for tenant B, presented while tenant A's rows are in the database, authenticates as tenant B and sees only tenant B (`GET /v1/tenants/me` returns B, never A); a revoked key presented a second time, and a key with an unknown prefix, are 401, and the revoked key's `last_used_at` does not move. Test in Task 12.

## File structure

```
pyproject.toml · .python-version · Makefile · .importlinter · alembic.ini · docker-compose.yml · Dockerfile
.env.example · README.md · .github/workflows/ci.yml
src/xolotl/
  __init__.py                       package version
  app.py                            composition root: build_app(session_factory=None)
  cli.py                            typer app: serve, db, tenant, audit
  models.py                         imports every module's ORM models so Base.metadata is complete
  platform/
    config.py                       Settings + get_settings()
    ids.py                          uuid7()
    enums.py                        ActorType, Role, TenantStatus, TenantEnv, StaffStatus, ApiKeyStatus
    errors.py                       XolotlError hierarchy with code/status
    clock.py                        now()
    logging.py                      JSON formatter, RedactionFilter, request_id contextvar, configure_logging()
    db.py                           Base, engines, sessionmaker, apply_context, after_begin hook, tenant_session()
    migrate.py                      alembic config, upgrade(), bootstrap(), reset()
    tenancy/context.py              TenantContext, bind_context, require_context, roles_guc
    tenancy/registry.py             SHARED_TABLES, SHARED_WRITABLE
    tenancy/ddl.py                  TenantTableMixin, tenant_table_args, tenant_fk, rls_statements, shared_grant_statements
    tenancy/testing.py              register_isolation_factory / isolation_factories
    audit/partitions.py             ensure_partitions(conn)
    audit/models.py                 AuditEventRow
    audit/__init__.py               AuditEvent, ChainVerification, record(), verify_chain(), canonical_json()
    audit/routes.py                 GET /v1/audit, GET /v1/audit/verify
    auth/passwords.py               hash_password, verify_password, generate_password, MIN_PASSWORD_LENGTH
    auth/tokens.py                  TokenClaims, issue_token, decode_token
    auth/totp.py                    new_secret, otpauth_uri, verify_code
    auth/principal.py               Actor, ActorResolver, resolve_request_actor, current_actor, get_session, require_access, current_staff
    api/problems.py                 Problem model, exception handlers, problem()
    api/request_id.py               RequestIdMiddleware
    api/health.py                   /healthz, /readyz
    api/idempotency.py              IdempotentRoute (APIRoute subclass), lookup/store helpers
    api/pagination.py               Page, paginate(), encode_cursor/decode_cursor
    api/app.py                      create_app(*, session_factory, actor_resolver, routers)
    api/models.py                   IdempotencyKey (kernel table behind the Idempotency-Key convention)
    api/router.py                   v1_router(): APIRouter preconfigured with IdempotentRoute
  modules/tenants/
    models.py                       Tenant, ApiKeyLookup, StaffUser, StaffCredential, ApiKey
    schemas.py                      request/response Pydantic models
    service.py                      create_tenant, login, login_totp, enroll_totp, confirm_totp, change_password, create_staff, disable_staff, create_api_key, revoke_api_key, resolve_bearer
    routes_auth.py                  /v1/auth/*
    routes_tenants.py               /v1/tenants/me, /v1/ping
    routes_staff.py                 /v1/staff*
    routes_api_keys.py              /v1/api-keys*
  migrations/env.py · script.py.mako · versions/0001_kernel.py
tests/
  __init__.py · conftest.py · helpers.py
  platform/ test_config.py test_ids.py test_errors.py test_context.py test_context_session.py test_logging.py
            test_api_conventions.py test_audit.py test_auth_primitives.py test_principal.py test_pagination.py test_schema_sync.py
  isolation/ __init__.py factories.py test_structural.py test_behavioural.py
  modules/tenants/ test_service.py test_auth.py test_api_keys.py test_staff.py
  test_cli.py
```

Commands in this plan assume the repository root as the working directory and `uv` on the PATH. Every `uv run pytest` invocation needs Docker running (testcontainers). Add `-p no:cacheprovider` if the sandbox forbids writing `.pytest_cache`.

---

### Task 1: Project skeleton, quality gates and settings

**Files:**
- Create: `pyproject.toml`, `.python-version`, `Makefile`, `.importlinter`, `docker-compose.yml`, `.env.example`, `README.md` (two lines; Task 15 replaces it)
- Create: `src/xolotl/__init__.py`, `src/xolotl/platform/__init__.py`, `src/xolotl/modules/__init__.py`, `src/xolotl/platform/config.py`
- Create: `tests/__init__.py`, `tests/platform/__init__.py`, `tests/platform/test_config.py`
- Modify: `.gitignore` (add `.venv/`, `data/`, `.coverage`, `htmlcov/`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info/`, `.env`)

**Interfaces:**
- Produces: `xolotl.platform.config.Settings` (fields `database_url: str`, `database_admin_url: str`, `env: Literal["dev", "test", "prod"]`, `jwt_secret: str`, `jwt_issuer: str`, `access_token_ttl_minutes: int`, `mfa_token_ttl_minutes: int`, `port: int`, `static_dir: Path`, `log_level: str`) and `get_settings() -> Settings` (lru_cached; tests call `get_settings.cache_clear()`).

- [ ] **Step 1: Write the project files**

`.python-version`:

```
3.13
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "xolotl"
version = "0.1.0"
description = "Multi-tenant KYC/AML compliance platform for LATAM regulated entities: policy packs, screening, monitoring, regulatory reporting and case management."
authors = [{ name = "Omar Ibañez" }]
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "alembic>=1.16",
    "argon2-cffi>=25.1",
    "fastapi>=0.118",
    "httpx>=0.28",
    "psycopg[binary]>=3.2",
    "pydantic>=2.12",
    "pydantic-settings>=2.11",
    "pyjwt[crypto]>=2.10",
    "pyotp>=2.9",
    "sqlalchemy>=2.0.43",
    "typer>=0.19",
    "uvicorn[standard]>=0.37",
]

[project.scripts]
xolotl = "xolotl.cli:app"

[dependency-groups]
dev = [
    "basedpyright>=1.37",
    "import-linter>=2.3",
    "pytest>=9.0",
    "pytest-cov>=7.0",
    "ruff>=0.15",
    "testcontainers[postgres]>=4.13",
    "ty>=0.0.15",
    "vulture>=2.14",
]

[tool.hatch.build.targets.wheel]
packages = ["src/xolotl"]

# ============================================================
# RUFF
# ============================================================
[tool.ruff]
target-version = "py313"
line-length = 88
fix = true
preview = true
extend-exclude = ["console"]

[tool.ruff.lint]
select = ["ALL"]
ignore = [
    "missing-trailing-comma",                   # Conflicts with formatter
    "single-line-implicit-string-concatenation", # Conflicts with formatter
    "D1",                                        # Missing docstrings
    "incorrect-blank-line-before-class",         # Conflicts with D211
    "multi-line-summary-second-line",            # Conflicts with D212
    "missing-copyright-notice",                  # No copyright headers
]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = [
    "assert",                   # Allow assert
    "magic-value-comparison",   # Allow magic values
    "ANN",                      # Don't require annotations in tests
    "S105", "S106",             # Test passwords are literals
    "PLR0913",                  # Fixture-heavy test signatures
    "ARG001",                   # Fixtures are requested for their side effects
    "PT012",                    # Multi-statement pytest.raises blocks read better
]
"src/xolotl/migrations/versions/*.py" = ["INP001", "N999", "D"]
"src/xolotl/migrations/env.py" = ["INP001"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.pylint]
max-nested-blocks = 3
max-args = 8

[tool.ruff.lint.isort]
known-first-party = ["xolotl", "tests"]

[tool.ruff.lint.flake8-type-checking]
runtime-evaluated-base-classes = ["xolotl.platform.db.Base", "pydantic.BaseModel", "pydantic_settings.BaseSettings"]

[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["fastapi.Depends", "fastapi.Query", "fastapi.Header", "fastapi.Body", "typer.Option", "typer.Argument"]

# ============================================================
# TY
# ============================================================
[tool.ty.environment]
python-version = "3.13"

[tool.ty.rules]
division-by-zero = "error"

[tool.ty.terminal]
error-on-warning = true

# ============================================================
# BASEDPYRIGHT
# ============================================================
[tool.basedpyright]
pythonVersion = "3.13"
typeCheckingMode = "all"
reportAny = "error"
reportExplicitAny = "error"
reportUnknownParameterType = "error"
reportUnknownArgumentType = "error"
reportUnknownVariableType = "error"
reportUnknownMemberType = "error"
reportMissingParameterType = "error"
reportMissingTypeArgument = "error"
reportUnusedClass = "error"
reportUnusedFunction = "error"
reportUnusedVariable = "error"
reportUnusedImport = "error"
reportDuplicateImport = "error"
reportUnnecessaryIsInstance = "error"
reportUnnecessaryCast = "error"
reportUnnecessaryComparison = "error"
reportUnnecessaryContains = "error"
reportUnnecessaryTypeIgnoreComment = "error"
exclude = ["**/__pycache__", ".venv", "build", "dist", "console", "src/xolotl/migrations/versions"]

# Tests keep every rule except the ones that fight fixtures and untyped SQL rows.
[[tool.basedpyright.executionEnvironments]]
root = "tests"
reportAny = "none"
reportExplicitAny = "none"
reportUnknownParameterType = "none"
reportUnknownArgumentType = "none"
reportUnknownVariableType = "none"
reportUnknownMemberType = "none"
reportMissingParameterType = "none"
reportMissingTypeArgument = "none"
reportUnusedParameter = "none"
reportUnannotatedClassAttribute = "none"

# ============================================================
# VULTURE
# ============================================================
[tool.vulture]
paths = ["src"]
exclude = ["src/xolotl/migrations/"]
min_confidence = 80
sort_by_size = true

# ============================================================
# PYTEST & COVERAGE
# ============================================================
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = [
    "--strict-markers",
    "--strict-config",
    "--tb=short",
    "--cov=src",
    "--cov-report=term-missing:skip-covered",
    "--cov-fail-under=90",
]
filterwarnings = ["error"]

[tool.coverage.run]
source = ["src"]
branch = true
omit = ["src/xolotl/migrations/*"]

[tool.coverage.report]
precision = 2
show_missing = true
fail_under = 90
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

`Makefile`:

```make
.PHONY: install lint format types vulture importlint test ci

install:
	uv sync

lint:
	uv run ruff check --no-fix src tests
	uv run ruff format --check src tests

format:
	uv run ruff check --fix src tests
	uv run ruff format src tests

types:
	uv run ty check src tests
	uv run basedpyright src tests

vulture:
	uv run vulture

importlint:
	uv run lint-imports

test:
	uv run pytest

# Runs every gate and reports every failure instead of stopping at the first.
ci:
	@fail=0; for target in lint types vulture importlint test; do \
		echo "== make $$target"; $(MAKE) --no-print-directory $$target || fail=1; \
	done; exit $$fail
```

`.importlinter`:

```ini
[importlinter]
root_package = xolotl

[importlinter:contract:platform-does-not-import-modules]
name = platform imports nothing from modules
type = forbidden
source_modules =
    xolotl.platform
forbidden_modules =
    xolotl.modules

[importlinter:contract:no-upward-imports-of-composition-roots]
name = platform and modules never import the composition roots
type = forbidden
source_modules =
    xolotl.platform
    xolotl.modules
forbidden_modules =
    xolotl.app
    xolotl.cli
```

`docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: xolotl
      POSTGRES_PASSWORD: xolotl
      POSTGRES_DB: xolotl
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U xolotl -d xolotl"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
```

`.env.example`:

```
XOLOTL_DATABASE_URL=postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl
XOLOTL_DATABASE_ADMIN_URL=postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl
XOLOTL_ENV=dev
XOLOTL_JWT_SECRET=dev-only-insecure-jwt-secret-change-me-000000
XOLOTL_LOG_LEVEL=INFO
```

`README.md` (hatchling needs it to exist; Task 15 writes the real one):

```markdown
# xolotl

Multi-tenant KYC/AML compliance platform for LATAM regulated entities. Work in progress; see `docs/superpowers/`.
```

`src/xolotl/__init__.py`:

```python
"""xolotl: multi-tenant KYC/AML compliance platform."""

__version__ = "0.1.0"
```

`src/xolotl/platform/__init__.py` and `src/xolotl/modules/__init__.py`: empty files. `tests/__init__.py` and `tests/platform/__init__.py`: empty files.

- [ ] **Step 2: Write the failing settings test**

`tests/platform/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from xolotl.platform.config import DEV_JWT_SECRET, Settings, get_settings


def test_defaults_match_the_spec(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("XOLOTL_") or key == "PORT":
            monkeypatch.delenv(key)
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl"
    assert s.database_admin_url == "postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl"
    assert s.env == "dev"
    assert s.jwt_issuer == "xolotl"
    assert s.access_token_ttl_minutes == 480
    assert s.mfa_token_ttl_minutes == 5
    assert s.port == 8000
    assert s.log_level == "INFO"
    assert s.static_dir.name == "static"


def test_env_prefix_and_bare_port(monkeypatch):
    monkeypatch.setenv("XOLOTL_JWT_ISSUER", "issuer-x")
    monkeypatch.setenv("PORT", "9100")
    s = Settings(_env_file=None)
    assert s.jwt_issuer == "issuer-x"
    assert s.port == 9100


def test_prod_refuses_the_dev_jwt_secret(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.delenv("XOLOTL_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None)


def test_prod_accepts_a_real_secret(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    assert Settings(_env_file=None).jwt_secret != DEV_JWT_SECRET


def test_short_jwt_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "short")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("XOLOTL_JWT_ISSUER", "first")
    first = get_settings()
    monkeypatch.setenv("XOLOTL_JWT_ISSUER", "second")
    assert get_settings() is first
    get_settings.cache_clear()
```

- [ ] **Step 3: Install and run the test to verify it fails**

Run: `uv sync && uv run pytest tests/platform/test_config.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.platform.config'`

- [ ] **Step 4: Write the settings module**

`src/xolotl/platform/config.py`:

```python
"""Runtime configuration, read from environment variables prefixed ``XOLOTL_``."""

from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-000000"  # noqa: S105
PACKAGE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="XOLOTL_", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl"
    database_admin_url: str = "postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl"
    env: Literal["dev", "test", "prod"] = "dev"
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32)
    jwt_issuer: str = "xolotl"
    access_token_ttl_minutes: int = 480
    mfa_token_ttl_minutes: int = 5
    # Render and most PaaS hosts inject a bare PORT.
    port: int = Field(
        default=8000, validation_alias=AliasChoices("PORT", "XOLOTL_PORT")
    )
    # Built console, served at "/" when the directory exists (plan 1d).
    static_dir: Path = PACKAGE_DIR / "static"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _refuse_dev_secrets_in_prod(self) -> Self:
        if self.env == "prod" and self.jwt_secret == DEV_JWT_SECRET:
            msg = "XOLOTL_JWT_SECRET must be set to a non-default value when XOLOTL_ENV=prod"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_config.py -v --no-cov`
Expected: 6 passed

- [ ] **Step 6: Run the static gates on what exists so far**

Run: `make lint types importlint`
Expected: ruff clean, ty and basedpyright clean, import-linter "Contracts: 2 kept, 0 broken". If a gate reports an issue in the files of this task, fix it in place; do not add ignores other than the per-file ones already in `pyproject.toml`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version Makefile .importlinter docker-compose.yml .env.example README.md .gitignore src tests
git commit -m "build(repo): project skeleton, quality gates and settings"
```

---

### Task 2: Identifiers, enums, errors and clock

**Files:**
- Create: `src/xolotl/platform/ids.py`, `src/xolotl/platform/enums.py`, `src/xolotl/platform/errors.py`, `src/xolotl/platform/clock.py`
- Test: `tests/platform/test_ids.py`, `tests/platform/test_errors.py`

**Interfaces:**
- Produces: `uuid7() -> UUID` and `uuid7_time(value: UUID) -> datetime`; enums `ActorType`, `Role`, `TenantStatus`, `TenantEnv`, `StaffStatus`, `ApiKeyStatus`; errors `XolotlError(detail=None, *, code=None)` with class attributes `status: int` and `default_code: str`, and subclasses `DomainError` (409 `CONFLICT`), `NotFoundError` (404 `NOT_FOUND`), `ForbiddenError` (403 `FORBIDDEN`), `UnauthorizedError` (401 `UNAUTHENTICATED`), `ContextMissingError` (500 `CONTEXT_MISSING`), `IdempotencyKeyRequiredError` (400 `IDEMPOTENCY_KEY_REQUIRED`), `IdempotencyConflictError` (422 `IDEMPOTENCY_KEY_REUSED`), `ValidationFailedError` (422 `VALIDATION_FAILED`, for cursor and password checks done in service code); `clock.now() -> datetime` (UTC, tests monkeypatch `xolotl.platform.clock.now`, so callers must write `clock.now()` through the module, never `from xolotl.platform.clock import now`).

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_ids.py`:

```python
import itertools
import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

from xolotl.platform.ids import uuid7, uuid7_time


def test_uuid7_has_version_7_and_rfc_variant():
    value = uuid7()
    assert isinstance(value, UUID)
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_is_strictly_monotonic_within_a_process():
    values = [uuid7() for _ in range(10_000)]
    assert all(a.int < b.int for a, b in itertools.pairwise(values))


def test_uuid7_embeds_the_current_time():
    before = datetime.now(UTC) - timedelta(milliseconds=2)
    value = uuid7()
    after = datetime.now(UTC) + timedelta(milliseconds=2)
    assert before <= uuid7_time(value) <= after


def test_uuid7_is_unique_across_threads():
    seen: list[UUID] = []
    lock = threading.Lock()

    def work():
        local = [uuid7() for _ in range(2_000)]
        with lock:
            seen.extend(local)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen)) == len(seen) == 16_000
```

`tests/platform/test_errors.py`:

```python
import pytest

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import (
    ContextMissingError,
    DomainError,
    ForbiddenError,
    IdempotencyConflictError,
    IdempotencyKeyRequiredError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
    XolotlError,
)


@pytest.mark.parametrize(
    ("cls", "status", "code"),
    [
        (XolotlError, 500, "INTERNAL"),
        (DomainError, 409, "CONFLICT"),
        (NotFoundError, 404, "NOT_FOUND"),
        (ForbiddenError, 403, "FORBIDDEN"),
        (UnauthorizedError, 401, "UNAUTHENTICATED"),
        (ContextMissingError, 500, "CONTEXT_MISSING"),
        (IdempotencyKeyRequiredError, 400, "IDEMPOTENCY_KEY_REQUIRED"),
        (IdempotencyConflictError, 422, "IDEMPOTENCY_KEY_REUSED"),
        (ValidationFailedError, 422, "VALIDATION_FAILED"),
    ],
)
def test_error_status_and_default_code(cls, status, code):
    exc = cls()
    assert exc.status == status
    assert exc.code == code
    assert str(exc) == exc.detail
    assert exc.detail


def test_error_code_and_detail_can_be_overridden():
    exc = ForbiddenError("scope tenants:read required", code="SCOPE_MISSING")
    assert exc.code == "SCOPE_MISSING"
    assert exc.detail == "scope tenants:read required"
    assert exc.status == 403


def test_enum_values_are_uppercase_strings():
    assert ActorType.PLATFORM_OPERATOR == "PLATFORM_OPERATOR"
    assert Role.AUDITOR_RO.value == "AUDITOR_RO"
    assert {r.value for r in Role} == {
        "ANALYST",
        "SENIOR_ANALYST",
        "MLRO",
        "AUDITOR_RO",
        "ADMIN",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_ids.py tests/platform/test_errors.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError` for `xolotl.platform.ids`

- [ ] **Step 3: Write the modules**

`src/xolotl/platform/ids.py`:

```python
"""UUIDv7 identifiers (RFC 9562).

Layout: 48-bit Unix milliseconds | 4-bit version | 12-bit counter | 2-bit variant |
62 random bits. The counter is seeded randomly each millisecond and incremented
for ids generated within the same millisecond, so ids are strictly increasing
within a process, which keeps B-tree inserts append-only.
"""

import secrets
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1


class _Uuid7Generator:
    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._last_ms: int = 0
        self._counter: int = 0

    def __call__(self) -> UUID:
        with self._lock:
            ms = time.time_ns() // 1_000_000
            if ms > self._last_ms:
                self._last_ms = ms
                # Leave headroom above the seed so the counter rarely overflows.
                self._counter = secrets.randbits(_COUNTER_BITS - 1)
            else:
                self._counter += 1
                if self._counter > _COUNTER_MAX:
                    self._last_ms += 1
                    self._counter = secrets.randbits(_COUNTER_BITS - 1)
            ms, counter = self._last_ms, self._counter
        rand_b = secrets.randbits(62)
        value = (ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | rand_b
        return UUID(int=value)


uuid7 = _Uuid7Generator()


def uuid7_time(value: UUID) -> datetime:
    """Return the timestamp embedded in a UUIDv7."""
    return datetime.fromtimestamp((value.int >> 80) / 1000, tz=UTC)
```

`src/xolotl/platform/enums.py`:

```python
"""Kernel enums. Values are UPPERCASE and stored as String(32) columns."""

from enum import StrEnum


class ActorType(StrEnum):
    STAFF = "STAFF"
    API_CLIENT = "API_CLIENT"
    SYSTEM = "SYSTEM"
    PLATFORM_OPERATOR = "PLATFORM_OPERATOR"


class Role(StrEnum):
    ANALYST = "ANALYST"
    SENIOR_ANALYST = "SENIOR_ANALYST"
    MLRO = "MLRO"
    AUDITOR_RO = "AUDITOR_RO"
    ADMIN = "ADMIN"


class TenantStatus(StrEnum):
    ONBOARDING = "ONBOARDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    OFFBOARDED = "OFFBOARDED"


class TenantEnv(StrEnum):
    SANDBOX = "SANDBOX"
    PROD = "PROD"


class StaffStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ApiKeyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
```

`src/xolotl/platform/errors.py`:

```python
"""Errors the API layer maps to problem+json responses (spec §7)."""

from typing import ClassVar


class XolotlError(Exception):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "INTERNAL"
    default_detail: ClassVar[str] = "internal error"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        self.detail: str = detail if detail is not None else self.default_detail
        self.code: str = code or self.default_code
        super().__init__(self.detail)


class DomainError(XolotlError):
    status: ClassVar[int] = 409
    default_code: ClassVar[str] = "CONFLICT"
    default_detail: ClassVar[str] = "conflict"


class NotFoundError(XolotlError):
    status: ClassVar[int] = 404
    default_code: ClassVar[str] = "NOT_FOUND"
    default_detail: ClassVar[str] = "not found"


class ForbiddenError(XolotlError):
    status: ClassVar[int] = 403
    default_code: ClassVar[str] = "FORBIDDEN"
    default_detail: ClassVar[str] = "forbidden"


class UnauthorizedError(XolotlError):
    status: ClassVar[int] = 401
    default_code: ClassVar[str] = "UNAUTHENTICATED"
    default_detail: ClassVar[str] = "authentication required"


class ContextMissingError(XolotlError):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "CONTEXT_MISSING"
    default_detail: ClassVar[str] = "no tenant context is bound"


class IdempotencyKeyRequiredError(XolotlError):
    status: ClassVar[int] = 400
    default_code: ClassVar[str] = "IDEMPOTENCY_KEY_REQUIRED"
    default_detail: ClassVar[str] = "Idempotency-Key header is required (1-128 characters)"


class IdempotencyConflictError(XolotlError):
    status: ClassVar[int] = 422
    default_code: ClassVar[str] = "IDEMPOTENCY_KEY_REUSED"
    default_detail: ClassVar[str] = (
        "Idempotency-Key was already used with a different request"
    )


class ValidationFailedError(XolotlError):
    status: ClassVar[int] = 422
    default_code: ClassVar[str] = "VALIDATION_FAILED"
    default_detail: ClassVar[str] = "validation failed"
```

`src/xolotl/platform/clock.py`:

```python
"""Single source of wall-clock time so tests can freeze it with monkeypatch."""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_ids.py tests/platform/test_errors.py -v --no-cov`
Expected: 15 passed

- [ ] **Step 5: Run the static gates**

Run: `make lint types`
Expected: clean. If a gate reports an issue in the files of this task, fix it in place.

- [ ] **Step 6: Commit**

```bash
git add src/xolotl/platform tests/platform
git commit -m "feat(platform): uuid7 ids, kernel enums, error hierarchy and clock"
```

---

### Task 3: Tenant context

**Implemented as** (plan erratum; the code below is the plan as written): the test snippets assign raise-only calls to `_ =`, pin the `''` wire encoding with `not x` or a parametrized `""`, and expect `dataclasses.FrozenInstanceError` through an indirect `setattr`, because the gates reject the plain forms.

**Files:**
- Create: `src/xolotl/platform/tenancy/__init__.py` (empty), `src/xolotl/platform/tenancy/context.py`
- Test: `tests/platform/test_context.py`

**Interfaces:**
- Consumes: `ActorType`, `Role`, `ContextMissingError` from Task 2.
- Produces: `TenantContext(tenant_id: UUID | None, actor_type: ActorType, actor_id: str, roles: frozenset[Role] = frozenset(), request_id: str = "")` (frozen dataclass; `tenant_id is None` allowed only for `PLATFORM_OPERATOR`), classmethods `TenantContext.platform(actor_id, *, tenant_id=None)` and `TenantContext.system(tenant_id, actor_id)`, method `require_tenant_id() -> UUID`; `current_context: ContextVar[TenantContext | None]`; `bind_context(ctx)` context manager; `require_context() -> TenantContext`; `roles_guc(roles: Iterable[Role]) -> str` (`",ADMIN,MLRO,"`, sorted, `""` when empty); `tenant_guc(ctx) -> str` (`str(tenant_id)` or `""`).

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_context.py`:

```python
from uuid import uuid4

import pytest

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import (
    TenantContext,
    bind_context,
    current_context,
    require_context,
    roles_guc,
    tenant_guc,
)


def test_platform_operator_may_have_no_tenant():
    ctx = TenantContext.platform("cli:omar")
    assert ctx.tenant_id is None
    assert ctx.actor_type is ActorType.PLATFORM_OPERATOR
    assert tenant_guc(ctx) == ""
    with pytest.raises(ContextMissingError):
        ctx.require_tenant_id()


def test_other_actor_types_require_a_tenant():
    with pytest.raises(ValueError, match="PLATFORM_OPERATOR"):
        TenantContext(tenant_id=None, actor_type=ActorType.STAFF, actor_id="x")


def test_system_context_and_guc_values():
    tenant_id = uuid4()
    ctx = TenantContext.system(tenant_id, "job:123")
    assert ctx.actor_type is ActorType.SYSTEM
    assert ctx.require_tenant_id() == tenant_id
    assert tenant_guc(ctx) == str(tenant_id)


def test_roles_guc_is_sorted_and_comma_delimited():
    assert roles_guc({Role.MLRO, Role.ADMIN}) == ",ADMIN,MLRO,"
    assert roles_guc(()) == ""


def test_bind_and_require_context():
    assert current_context.get() is None
    with pytest.raises(ContextMissingError):
        require_context()
    outer = TenantContext.platform("cli:a")
    inner = TenantContext.system(uuid4(), "job:1")
    with bind_context(outer):
        assert require_context() is outer
        with bind_context(inner):
            assert require_context() is inner
        assert require_context() is outer
    assert current_context.get() is None


def test_context_is_frozen_and_hashable():
    ctx = TenantContext.system(uuid4(), "job:1")
    with pytest.raises(AttributeError):
        ctx.actor_id = "other"  # type: ignore[misc]
    assert {ctx: 1}[ctx] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_context.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.platform.tenancy'`

- [ ] **Step 3: Write the module**

`src/xolotl/platform/tenancy/context.py`:

```python
"""Who is acting and for which tenant (spec §4.1)."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Self
from uuid import UUID

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ContextMissingError


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID | None  # None only for PLATFORM_OPERATOR
    actor_type: ActorType
    actor_id: str  # staff_user.id, api_key.id, job id, or "cli:<user>"
    roles: frozenset[Role] = field(default_factory=frozenset)
    request_id: str = ""

    def __post_init__(self) -> None:
        if self.tenant_id is None and self.actor_type is not ActorType.PLATFORM_OPERATOR:
            msg = "tenant_id may be None only for PLATFORM_OPERATOR"
            raise ValueError(msg)

    @classmethod
    def platform(cls, actor_id: str, *, tenant_id: UUID | None = None) -> Self:
        return cls(
            tenant_id=tenant_id, actor_type=ActorType.PLATFORM_OPERATOR, actor_id=actor_id
        )

    @classmethod
    def system(cls, tenant_id: UUID, actor_id: str) -> Self:
        return cls(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=actor_id)

    def require_tenant_id(self) -> UUID:
        if self.tenant_id is None:
            msg = "this operation needs a tenant-scoped context"
            raise ContextMissingError(msg)
        return self.tenant_id


current_context: ContextVar[TenantContext | None] = ContextVar(
    "xolotl_tenant_context", default=None
)


@contextmanager
def bind_context(ctx: TenantContext) -> Iterator[None]:
    token = current_context.set(ctx)
    try:
        yield
    finally:
        current_context.reset(token)


def require_context() -> TenantContext:
    ctx = current_context.get()
    if ctx is None:
        raise ContextMissingError
    return ctx


def roles_guc(roles: Iterable[Role]) -> str:
    """Comma-delimited roles with sentinels, e.g. ``,ADMIN,MLRO,`` (empty: ``""``)."""
    names = sorted(role.value for role in roles)
    return f",{','.join(names)}," if names else ""


def tenant_guc(ctx: TenantContext) -> str:
    return "" if ctx.tenant_id is None else str(ctx.tenant_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_context.py -v --no-cov`
Expected: 6 passed

- [ ] **Step 5: Run the static gates, then commit**

Run: `make lint types`
Expected: clean.

```bash
git add src/xolotl/platform/tenancy tests/platform/test_context.py
git commit -m "feat(tenancy): TenantContext, contextvar binding and GUC encoders"
```

---

### Task 4: Database layer, migrations scaffold and test fixtures

**Implemented as** (plan errata; the code below is the plan as written): the `after_begin` hook is registered with `event.listen(Session, "after_begin", ...)` instead of the untyped decorator, and it calls `connection.invalidate()` before raising `ContextMissingError`, so a context-less transaction refuses every later statement too; `Base.metadata` is declared `ClassVar[MetaData]`; `xolotl.models` exposes `metadata()` instead of being imported for its side effect (env.py and the tests call it); the basedpyright `tests` environment gains `extraPaths = [".", "src"]`; `bootstrap()` runs its literal-bearing DDL through `exec_literal_ddl`, which doubles `%` so a password containing one survives psycopg.

**Files:**
- Create: `src/xolotl/platform/db.py`, `src/xolotl/platform/migrate.py`, `src/xolotl/models.py`
- Create: `src/xolotl/migrations/__init__.py` (empty), `src/xolotl/migrations/env.py`, `src/xolotl/migrations/script.py.mako`, `src/xolotl/migrations/versions/__init__.py` (empty), `alembic.ini`
- Create: `tests/conftest.py`, `tests/helpers.py`
- Test: `tests/platform/test_context_session.py`

**Interfaces:**
- Consumes: `get_settings()` (Task 1); `TenantContext`, `current_context`, `bind_context`, `roles_guc`, `tenant_guc` (Task 3); `ContextMissingError` (Task 2).
- Produces: `db.Base` (DeclarativeBase with naming convention), `db.APP_ROLE = "xolotl_app"`, `db.CONTEXT_KEY = "tenant_context"`, `db.get_engine()`, `db.get_admin_engine()`, `db.get_sessionmaker()` (all lru_cached), `db.SessionFactory = Callable[[], Session]`, `db.apply_context(session, ctx) -> None`, `db.bound_context(session) -> TenantContext`, `db.tenant_session(ctx, *, factory=None)` context manager (binds the contextvar, applies the context, commits on success, rolls back on error, always closes); `migrate.upgrade(revision="head")`, `migrate.bootstrap() -> None`, `migrate.reset() -> None`, `migrate.MIGRATIONS_DIR`; test fixtures `admin_url` (session), `engines` (session: sets env vars, clears caches, bootstraps and migrates), `clean_db` (truncates every table except `alembic_version`), `engine`, `admin_engine` (both depend on `clean_db`); helpers `tests.helpers.platform_ctx()`, `tests.helpers.staff_ctx(tenant_id, *, actor_id="staff:test", roles=())`, `tests.helpers.system_ctx(tenant_id)`.

- [ ] **Step 1: Write the failing tests**

`tests/helpers.py`:

```python
"""Small helpers shared by test modules."""

from collections.abc import Iterable
from uuid import UUID

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.tenancy.context import TenantContext


def platform_ctx(actor_id: str = "cli:test") -> TenantContext:
    return TenantContext.platform(actor_id)


def staff_ctx(
    tenant_id: UUID, *, actor_id: str = "staff:test", roles: Iterable[Role] = ()
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_type=ActorType.STAFF,
        actor_id=actor_id,
        roles=frozenset(roles),
    )


def system_ctx(tenant_id: UUID, actor_id: str = "job:test") -> TenantContext:
    return TenantContext.system(tenant_id, actor_id)
```

`tests/conftest.py`:

```python
"""Shared fixtures.

Tests run against a real Postgres 17 (testcontainers, or
``XOLOTL_TEST_DATABASE_ADMIN_URL`` if set) with real commits on real
connections, so row-level security and advisory locks behave as in production.
Every table except ``alembic_version`` is truncated before each test that
touches the database.
"""

import os
from collections.abc import Iterator
from typing import cast

import pytest
from sqlalchemy import Engine, make_url, text
from testcontainers.community.postgres import PostgresContainer

from xolotl.platform import db, migrate
from xolotl.platform.config import get_settings

APP_PASSWORD = "xolotl"


@pytest.fixture(scope="session")
def admin_url() -> Iterator[str]:
    if url := os.environ.get("XOLOTL_TEST_DATABASE_ADMIN_URL"):
        yield url
        return
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


def _reset_caches() -> None:
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_admin_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


@pytest.fixture(scope="session")
def engines(admin_url: str) -> Iterator[tuple[Engine, Engine]]:
    app_url = make_url(admin_url).set(username=db.APP_ROLE, password=APP_PASSWORD)
    os.environ["XOLOTL_DATABASE_ADMIN_URL"] = admin_url
    os.environ["XOLOTL_DATABASE_URL"] = app_url.render_as_string(hide_password=False)
    os.environ["XOLOTL_ENV"] = "test"
    _reset_caches()
    migrate.bootstrap()
    migrate.upgrade()
    yield db.get_engine(), db.get_admin_engine()
    db.get_engine().dispose()
    db.get_admin_engine().dispose()


@pytest.fixture
def clean_db(engines: tuple[Engine, Engine]) -> None:
    """Truncate every table except alembic_version before the test runs."""
    _, admin = engines
    with admin.begin() as conn:
        names = cast(
            "list[str]",
            conn.scalars(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                    "AND NOT c.relispartition AND c.relname <> 'alembic_version'"
                )
            ).all(),
        )
        if names:
            # Replica mode disables the audit append-only triggers for this
            # transaction only; the owner may truncate everything.
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            quoted = ", ".join(f'"{name}"' for name in names)
            conn.execute(text(f"TRUNCATE TABLE {quoted} CASCADE"))  # noqa: S608


@pytest.fixture
def engine(engines: tuple[Engine, Engine], clean_db: None) -> Engine:
    return engines[0]


@pytest.fixture
def admin_engine(engines: tuple[Engine, Engine], clean_db: None) -> Engine:
    return engines[1]
```

`tests/platform/test_context_session.py`:

```python
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from xolotl.platform import migrate
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    APP_ROLE,
    apply_context,
    bound_context,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import Role
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import (
    TenantContext,
    current_context,
    require_context,
)
from tests.helpers import platform_ctx, staff_ctx, system_ctx

GUCS = text(
    "SELECT current_setting('app.tenant_id', true), "
    "current_setting('app.actor_type', true), "
    "current_setting('app.actor_id', true), "
    "current_setting('app.roles', true)"
)


def test_engine_connects_as_the_application_role(engine: Engine):
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_user")) == APP_ROLE


def test_session_refuses_to_start_without_context(engine: Engine):
    with get_sessionmaker()() as session, pytest.raises(ContextMissingError):
        session.execute(text("SELECT 1"))


def test_gucs_are_set_on_every_transaction(engine: Engine):
    tenant_id = uuid4()
    ctx = staff_ctx(tenant_id, actor_id="staff:1", roles=[Role.MLRO, Role.ADMIN])
    with tenant_session(ctx) as s:
        assert tuple(s.execute(GUCS).one()) == (
            str(tenant_id),
            "STAFF",
            "staff:1",
            ",ADMIN,MLRO,",
        )
        s.commit()  # ends the transaction; the next statement autobegins
        assert s.scalar(text("SELECT current_setting('app.tenant_id', true)")) == str(
            tenant_id
        )


def test_gucs_do_not_leak_into_a_fresh_connection(engine: Engine):
    with tenant_session(system_ctx(uuid4())) as s:
        s.execute(text("SELECT 1"))
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_setting('app.tenant_id', true)")) in (
            None,
            "",
        )


def test_platform_operator_predicate_is_null_not_an_error(engine: Engine):
    with tenant_session(platform_ctx()) as s:
        assert tuple(s.execute(GUCS).one()) == ("", "PLATFORM_OPERATOR", "cli:test", "")
        assert (
            s.scalar(
                text(
                    "SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid IS NULL"
                )
            )
            is True
        )


def test_apply_context_switches_tenant_mid_transaction(engine: Engine):
    a, b = uuid4(), uuid4()
    with tenant_session(system_ctx(a)) as s:
        s.execute(text("SELECT 1"))
        apply_context(s, system_ctx(b))
        assert s.scalar(text("SELECT current_setting('app.tenant_id', true)")) == str(b)
        assert bound_context(s).tenant_id == b


def test_tenant_session_binds_the_contextvar_and_rolls_back_on_error(engine: Engine):
    ctx = system_ctx(uuid4())
    with tenant_session(ctx) as s:
        assert require_context() is ctx
        assert bound_context(s) is ctx
    assert current_context.get() is None
    with pytest.raises(RuntimeError, match="boom"), tenant_session(ctx) as s:
        s.execute(text("SELECT 1"))
        msg = "boom"
        raise RuntimeError(msg)
    assert current_context.get() is None


def test_bound_context_requires_a_context(engine: Engine):
    with get_sessionmaker()() as session, pytest.raises(ContextMissingError):
        bound_context(session)


def test_bootstrap_is_idempotent_and_the_role_cannot_bypass_rls(admin_engine: Engine):
    migrate.bootstrap()
    migrate.bootstrap()
    with admin_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolbypassrls, rolsuper, rolcanlogin FROM pg_roles "
                "WHERE rolname = :role"
            ),
            {"role": APP_ROLE},
        ).one()
    assert tuple(row) == (False, False, True)


def test_reset_refuses_in_prod(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "p" * 40)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="prod"):
            migrate.reset()
    finally:
        get_settings.cache_clear()


def test_platform_context_has_no_tenant_for_tenant_scoped_checks(engine: Engine):
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        bound_context(s).require_tenant_id()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_context_session.py -v --no-cov`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'xolotl.platform.db'` (from conftest).

- [ ] **Step 3: Write the database layer**

`src/xolotl/platform/db.py`:

```python
"""Engines, declarative base and the tenant session contract (spec §4.2).

Every transaction starts by setting ``app.tenant_id``, ``app.actor_type``,
``app.actor_id`` and ``app.roles`` with ``set_config(..., true)`` so the values
are transaction-local. Row-level security policies read them with
``current_setting(name, true)``. A session with no bound context refuses to run
any statement.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, MetaData, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction, sessionmaker

from xolotl.platform.config import get_settings
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import (
    TenantContext,
    bind_context,
    current_context,
    roles_guc,
    tenant_guc,
)

APP_ROLE = "xolotl_app"
CONTEXT_KEY = "tenant_context"
SessionFactory = Callable[[], Session]

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SET_CONTEXT_SQL = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.actor_type', :actor_type, true), "
    "set_config('app.actor_id', :actor_id, true), "
    "set_config('app.roles', :roles, true)"
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def context_params(ctx: TenantContext) -> dict[str, str]:
    return {
        "tenant_id": tenant_guc(ctx),
        "actor_type": ctx.actor_type.value,
        "actor_id": ctx.actor_id,
        "roles": roles_guc(ctx.roles),
    }


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_admin_engine() -> Engine:
    return create_engine(get_settings().database_admin_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def bound_context(session: Session) -> TenantContext:
    """Return the context a session runs under; raise when none is bound."""
    raw: object = session.info.get(CONTEXT_KEY)
    if not isinstance(raw, TenantContext):
        raise ContextMissingError
    return raw


def apply_context(session: Session, ctx: TenantContext) -> None:
    """Bind ``ctx`` to the session and set the GUCs in the current transaction.

    Works mid-transaction too: ``set_config(..., true)`` overrides the values for
    the rest of the transaction, which is how login switches from the platform
    scope to the tenant it just resolved.
    """
    session.info[CONTEXT_KEY] = ctx
    _ = session.execute(SET_CONTEXT_SQL, context_params(ctx))


@event.listens_for(Session, "after_begin")
def _set_context_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    if transaction.nested:
        return  # savepoints inherit the enclosing transaction's settings
    raw: object = session.info.get(CONTEXT_KEY)
    ctx = raw if isinstance(raw, TenantContext) else current_context.get()
    if ctx is None:
        raise ContextMissingError
    session.info[CONTEXT_KEY] = ctx
    _ = connection.execute(SET_CONTEXT_SQL, context_params(ctx))


@contextmanager
def tenant_session(
    ctx: TenantContext, *, factory: SessionFactory | None = None
) -> Iterator[Session]:
    """A session bound to ``ctx``: commits on success, rolls back on error."""
    session = (factory or get_sessionmaker())()
    try:
        with bind_context(ctx):
            apply_context(session, ctx)
            yield session
            session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
```

`src/xolotl/platform/migrate.py`:

```python
"""Alembic entry points and database bootstrap (spec §4.5, §10)."""

from pathlib import Path
from typing import cast

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import make_url, text

from xolotl.platform.config import get_settings
from xolotl.platform.db import APP_ROLE, get_admin_engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def upgrade(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


def _app_password() -> str:
    url = make_url(get_settings().database_url)
    if url.username != APP_ROLE or not url.password:
        msg = f"XOLOTL_DATABASE_URL must authenticate as {APP_ROLE} with a password"
        raise ValueError(msg)
    return url.password


def bootstrap() -> None:
    """Create the application role if missing and sync its password and grants.

    Idempotent. Runs as the owner role (``DATABASE_ADMIN_URL``).
    """
    password = _app_password()
    database = make_url(get_settings().database_admin_url).database or "xolotl"
    with get_admin_engine().begin() as conn:
        exists = cast(
            "int | None",
            conn.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
            ),
        )
        if exists is None:
            _ = conn.exec_driver_sql(
                sql.SQL("CREATE ROLE {} LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB")
                .format(sql.Identifier(APP_ROLE))
                .as_string()
            )
        _ = conn.exec_driver_sql(
            sql.SQL("ALTER ROLE {} WITH LOGIN NOBYPASSRLS PASSWORD {}")
            .format(sql.Identifier(APP_ROLE), sql.Literal(password))
            .as_string()
        )
        _ = conn.exec_driver_sql(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
            .format(sql.Identifier(database), sql.Identifier(APP_ROLE))
            .as_string()
        )
        _ = conn.exec_driver_sql(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}")
            .format(sql.Identifier(APP_ROLE))
            .as_string()
        )


def reset() -> None:
    """Drop and recreate the public schema, then bootstrap and migrate.

    Refuses to run when ``XOLOTL_ENV=prod``.
    """
    if get_settings().env == "prod":
        msg = "db reset is not allowed when XOLOTL_ENV=prod"
        raise RuntimeError(msg)
    with get_admin_engine().begin() as conn:
        _ = conn.execute(text("DROP SCHEMA public CASCADE"))
        _ = conn.execute(text("CREATE SCHEMA public"))
    bootstrap()
    upgrade()
```

`src/xolotl/models.py` (completed in Task 6; for now only the docstring so Alembic imports succeed):

```python
"""Imports every ORM model module so ``Base.metadata`` is complete.

Alembic's env.py and the schema-sync test import this module. Each feature
module adds its ``models`` import here when it creates its first table.
"""
```

`src/xolotl/migrations/env.py`:

```python
from alembic import context

from xolotl import models  # noqa: F401  # registers every table on Base.metadata
from xolotl.platform.db import Base, get_admin_engine

with get_admin_engine().connect() as connection:
    context.configure(
        connection=connection, target_metadata=Base.metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()
```

`src/xolotl/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`alembic.ini` (for the bare `alembic` command; the application uses `migrate.py`):

```ini
[alembic]
script_location = src/xolotl/migrations
prepend_sys_path = src
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_context_session.py -v --no-cov`
Expected: 11 passed (the first DB test pulls the `postgres:17-alpine` image; allow a minute the first time).

- [ ] **Step 5: Run the static gates**

Run: `make lint types importlint`
Expected: clean. Known trip-ups and their fixes: basedpyright flags the result of `conn.scalar(text(...))` as `Any`, so it is wrapped in `cast("int | None", ...)`; `session.info.get(...)` is assigned to a variable annotated `object` and narrowed with `isinstance`; ruff `S608` on the test TRUNCATE is suppressed inline because the names come from `pg_class`, not from input.

- [ ] **Step 6: Commit**

```bash
git add src/xolotl/platform/db.py src/xolotl/platform/migrate.py src/xolotl/models.py src/xolotl/migrations alembic.ini tests/conftest.py tests/helpers.py tests/platform/test_context_session.py
git commit -m "feat(platform): tenant session contract, engines, alembic scaffold and db test fixtures"
```

---

### Task 5: Tenancy DDL helpers, migration 0001 and the structural isolation test

**Implemented as** (plan errata; the code below is the plan as written): `SHARED_TABLES` is the harness's authority, not the presence of a `tenant_id` column (`api_key_lookup` references tenants but is shared), so the structural test asserts every registry table exists without row security or policies and every other table is isolated (spec §4.4, program spec D1); `tenant_table_args` emits no `UNIQUE (tenant_id, id)` and migration 0001 has none, because the primary key is the composite-FK target and Postgres discards the duplicate (spec §4.3/§5, program spec D2); partition bounds carry an explicit UTC offset (`'YYYY-MM-DD 00:00:00+00'`); the bypass test asserts `rolsuper OR rolbypassrls` is false and `pg_has_role(app_role, relowner, 'MEMBER')` is false for every relation.

**Files:**
- Create: `src/xolotl/platform/tenancy/registry.py`, `src/xolotl/platform/tenancy/ddl.py`
- Create: `src/xolotl/platform/audit/__init__.py` (empty for now; filled in Task 7), `src/xolotl/platform/audit/partitions.py`
- Create: `src/xolotl/migrations/versions/0001_kernel.py`
- Test: `tests/isolation/__init__.py` (empty), `tests/isolation/test_structural.py`, `tests/platform/test_ddl.py`

**Interfaces:**
- Consumes: `APP_ROLE` (Task 4), `uuid7` (Task 2), `clock` (Task 2).
- Produces: `registry.SHARED_TABLES` (`{"alembic_version", "api_key_lookup"}`), `registry.SHARED_WRITABLE` (`{"api_key_lookup"}`); `ddl.TENANT_PREDICATE`, `ddl.TENANT_TABLE_PREDICATE`, `ddl.TenantTableMixin` (`tenant_id`, `id` mapped as composite PK, `id` defaults to `uuid7`), `ddl.tenant_table_args(table, *extra)`, `ddl.tenant_fk(column, target, *, target_column="id", ondelete=None)`, `ddl.rls_statements(table, *, predicate=TENANT_PREDICATE, grant=True) -> list[str]`, `ddl.restrictive_policy_statement(table, name, using_sql, check_sql=None) -> str`, `ddl.shared_grant_statements(table) -> list[str]`, and the Alembic wrappers `ddl.enable_rls(table, *, predicate=...)`, `ddl.add_restrictive_policy(table, name, using_sql, check_sql=None)`, `ddl.grant_shared(table)`; `partitions.TABLE`, `partitions.IMMUTABLE_FUNCTION`, `partitions.partition_name(month: date) -> str`, `partitions.next_month(d: date) -> date`, `partitions.harden_partition(conn, name)`, `partitions.ensure_partitions(conn, *, months_ahead=3, start=None) -> list[str]`.
- Schema produced by migration `0001_kernel` (ORM models in Task 6 must match it exactly): `tenant`, `api_key_lookup`, `staff_user`, `staff_credential`, `api_key`, `idempotency_key` (PK `(tenant_id, actor_id, key)`), `audit_event` (partitioned by `RANGE (at)`, default partition plus the current month and three ahead).

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_ddl.py`:

```python
from datetime import date

from xolotl.platform.audit.partitions import next_month, partition_name
from xolotl.platform.db import APP_ROLE
from xolotl.platform.tenancy.ddl import (
    TENANT_PREDICATE,
    restrictive_policy_statement,
    rls_statements,
    shared_grant_statements,
    tenant_fk,
)


def test_rls_statements_enable_force_policy_and_grant():
    stmts = rls_statements("widget")
    assert stmts[0] == "ALTER TABLE widget ENABLE ROW LEVEL SECURITY"
    assert stmts[1] == "ALTER TABLE widget FORCE ROW LEVEL SECURITY"
    assert stmts[2] == (
        "CREATE POLICY tenant_isolation ON widget AS PERMISSIVE FOR ALL "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    assert stmts[3] == f"GRANT SELECT, INSERT, UPDATE, DELETE ON widget TO {APP_ROLE}"
    assert len(rls_statements("widget", grant=False)) == 3


def test_tenant_predicate_uses_nullif():
    assert TENANT_PREDICATE == (
        "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )


def test_restrictive_policy_statement():
    assert restrictive_policy_statement("c", "c_vis", "visibility = 'TEAM'") == (
        "CREATE POLICY c_vis ON c AS RESTRICTIVE FOR ALL "
        "USING (visibility = 'TEAM') WITH CHECK (visibility = 'TEAM')"
    )


def test_shared_grants_follow_the_registry():
    assert shared_grant_statements("api_key_lookup") == [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_key_lookup TO {APP_ROLE}"
    ]
    assert shared_grant_statements("policy_pack") == [
        f"GRANT SELECT ON policy_pack TO {APP_ROLE}"
    ]


def test_tenant_fk_is_composite():
    fk = tenant_fk("staff_user_id", "staff_user")
    assert [c for c in fk.column_keys] == ["tenant_id", "staff_user_id"]
    assert [e.target_fullname for e in fk.elements] == [
        "staff_user.tenant_id",
        "staff_user.id",
    ]


def test_partition_naming_and_month_arithmetic():
    assert partition_name(date(2026, 10, 1)) == "audit_event_y2026m10"
    assert next_month(date(2026, 12, 15)) == date(2027, 1, 1)
    assert next_month(date(2026, 1, 31)) == date(2026, 2, 1)
```

`tests/isolation/test_structural.py`:

```python
"""Structural half of the isolation harness (spec §4.4, items 1 and 3).

Every base table in ``public`` either carries ``tenant_id`` with forced RLS and
exactly one permissive ``tenant_isolation`` policy, or is listed in
``SHARED_TABLES``. The application role owns nothing and cannot bypass RLS.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, text

from xolotl.platform.audit.partitions import next_month, partition_name
from xolotl.platform.db import APP_ROLE
from xolotl.platform.tenancy.registry import SHARED_TABLES

RELATIONS = text(
    """
    SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity, c.relispartition,
           pg_get_userbyid(c.relowner) AS owner,
           EXISTS (SELECT 1 FROM pg_attribute a
                   WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                     AND NOT a.attisdropped) AS has_tenant_id
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)
POLICIES = text(
    """
    SELECT polname, polpermissive, polcmd,
           pg_get_expr(polqual, polrelid) AS using_expr,
           pg_get_expr(polwithcheck, polrelid) AS check_expr
    FROM pg_policy WHERE polrelid = :oid ORDER BY polname
    """
)


def _relations(engine: Engine):
    with engine.connect() as conn:
        return conn.execute(RELATIONS).mappings().all()


def _tenant_scoped(row) -> bool:
    return bool(row["has_tenant_id"]) or row["relname"] == "tenant"


def test_every_table_has_tenant_id_or_is_registered_shared(admin_engine: Engine):
    rows = _relations(admin_engine)
    without = {r["relname"] for r in rows if not _tenant_scoped(r)}
    unregistered = without - SHARED_TABLES
    stale = SHARED_TABLES - without
    assert not unregistered, f"tables without tenant_id not in SHARED_TABLES: {unregistered}"
    assert not stale, f"SHARED_TABLES entries that are missing or now tenant-scoped: {stale}"


def test_tenant_tables_force_rls_with_exactly_one_permissive_policy(admin_engine: Engine):
    reference: str | None = None
    with admin_engine.connect() as conn:
        for row in conn.execute(RELATIONS).mappings().all():
            if not _tenant_scoped(row):
                continue
            name = row["relname"]
            assert row["relrowsecurity"] and row["relforcerowsecurity"], (
                f"{name} must ENABLE and FORCE ROW LEVEL SECURITY"
            )
            policies = conn.execute(POLICIES, {"oid": row["oid"]}).mappings().all()
            permissive = [p for p in policies if p["polpermissive"]]
            assert [p["polname"] for p in permissive] == ["tenant_isolation"], (
                f"{name}: permissive policies {[p['polname'] for p in permissive]}"
            )
            policy = permissive[0]
            assert policy["polcmd"] == "*", f"{name}: tenant_isolation must be FOR ALL"
            assert policy["using_expr"] == policy["check_expr"], f"{name}: USING != WITH CHECK"
            if name == "tenant":
                assert "PLATFORM_OPERATOR" in policy["using_expr"]
                continue
            lowered = policy["using_expr"].lower()
            assert "nullif" in lowered and "app.tenant_id" in lowered, (
                f"{name}: {policy['using_expr']}"
            )
            reference = reference or policy["using_expr"]
            assert policy["using_expr"] == reference, (
                f"{name}: predicate differs from the kernel predicate {reference}"
            )


def test_app_role_owns_nothing_and_cannot_bypass_rls(admin_engine: Engine):
    owned = [r["relname"] for r in _relations(admin_engine) if r["owner"] == APP_ROLE]
    assert owned == []
    with admin_engine.connect() as conn:
        bypass = conn.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        )
    assert bypass is False


def test_app_role_cannot_read_partitions_directly(admin_engine: Engine):
    with admin_engine.connect() as conn:
        readable = conn.scalars(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relispartition "
                "AND has_table_privilege(:role, c.oid, 'SELECT')"
            ),
            {"role": APP_ROLE},
        ).all()
    assert readable == []


def test_audit_partitions_exist_for_this_month_and_three_ahead(admin_engine: Engine):
    month = datetime.now(UTC).date().replace(day=1)
    expected = {"audit_event_default"}
    for _ in range(4):
        expected.add(partition_name(month))
        month = next_month(month)
    names = {r["relname"] for r in _relations(admin_engine)}
    assert expected <= names, f"missing partitions: {expected - names}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_ddl.py tests/isolation/test_structural.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.platform.tenancy.ddl'`

- [ ] **Step 3: Write the registry, DDL helpers and partition helper**

`src/xolotl/platform/tenancy/registry.py`:

```python
"""Tables that have no ``tenant_id`` column (program spec D1).

The isolation harness asserts that this set equals the set of base tables
without ``tenant_id``. Add a table here only when it is genuinely shared across
tenants (reference data, lookups that must work before a tenant context exists).
"""

SHARED_TABLES: frozenset[str] = frozenset({"alembic_version", "api_key_lookup"})

# Shared tables the application role may also write; the rest are read-only.
SHARED_WRITABLE: frozenset[str] = frozenset({"api_key_lookup"})
```

`src/xolotl/platform/tenancy/ddl.py`:

```python
"""Conventions for tenant tables and the RLS DDL migrations emit (spec §4.3)."""

from uuid import UUID

from alembic import op
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import APP_ROLE
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.registry import SHARED_WRITABLE

TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
TENANT_TABLE_PREDICATE = (
    "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
    "OR current_setting('app.actor_type', true) = 'PLATFORM_OPERATOR'"
)


class TenantTableMixin:
    """``tenant_id`` first in the primary key, ``id`` a UUIDv7."""

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


def tenant_table_args(table: str, *extra: SchemaItem) -> tuple[SchemaItem, ...]:
    """``__table_args__`` for a tenant table: the ``UNIQUE (tenant_id, id)`` plus extras."""
    return (UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id"), *extra)


def tenant_fk(
    column: str, target: str, *, target_column: str = "id", ondelete: str | None = None
) -> ForeignKeyConstraint:
    """``FOREIGN KEY (tenant_id, <column>) REFERENCES <target> (tenant_id, <target_column>)``."""
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target}.tenant_id", f"{target}.{target_column}"],
        ondelete=ondelete,
    )


def rls_statements(
    table: str, *, predicate: str = TENANT_PREDICATE, grant: bool = True
) -> list[str]:
    statements = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
        f"USING ({predicate}) WITH CHECK ({predicate})",
    ]
    if grant:
        statements.append(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}"
        )
    return statements


def restrictive_policy_statement(
    table: str, name: str, using_sql: str, check_sql: str | None = None
) -> str:
    check = using_sql if check_sql is None else check_sql
    return (
        f"CREATE POLICY {name} ON {table} AS RESTRICTIVE FOR ALL "
        f"USING ({using_sql}) WITH CHECK ({check})"
    )


def shared_grant_statements(table: str) -> list[str]:
    privileges = (
        "SELECT, INSERT, UPDATE, DELETE" if table in SHARED_WRITABLE else "SELECT"
    )
    return [f"GRANT {privileges} ON {table} TO {APP_ROLE}"]


# Alembic wrappers: call these from migrations.


def enable_rls(table: str, *, predicate: str = TENANT_PREDICATE) -> None:
    for statement in rls_statements(table, predicate=predicate):
        op.execute(statement)


def add_restrictive_policy(
    table: str, name: str, using_sql: str, check_sql: str | None = None
) -> None:
    op.execute(restrictive_policy_statement(table, name, using_sql, check_sql))


def grant_shared(table: str) -> None:
    for statement in shared_grant_statements(table):
        op.execute(statement)
```

`src/xolotl/platform/audit/partitions.py`:

```python
"""Monthly partitions of ``audit_event`` with RLS and append-only triggers."""

from datetime import date
from typing import cast

from sqlalchemy import Connection, text

from xolotl.platform import clock
from xolotl.platform.tenancy.ddl import rls_statements

TABLE = "audit_event"
IMMUTABLE_FUNCTION = "audit_event_immutable"


def partition_name(month: date) -> str:
    return f"{TABLE}_y{month:%Y}m{month:%m}"


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def harden_partition(conn: Connection, name: str) -> None:
    """Force RLS on a partition and reject TRUNCATE on it.

    Partitions get no GRANT: the application role reaches rows only through the
    parent table, and the parent's row trigger is cloned onto every partition.
    """
    for statement in rls_statements(name, grant=False):
        _ = conn.execute(text(statement))
    _ = conn.execute(
        text(
            f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {name} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
        )
    )


def ensure_partitions(
    conn: Connection, *, months_ahead: int = 3, start: date | None = None
) -> list[str]:
    """Create the partition for ``start``'s month and ``months_ahead`` more.

    Returns the names of the partitions created in this call.
    """
    created: list[str] = []
    month = (start or clock.now().date()).replace(day=1)
    for _ in range(months_ahead + 1):
        name = partition_name(month)
        exists = cast(
            "int | None",
            conn.scalar(
                text("SELECT 1 FROM pg_class WHERE relname = :name"), {"name": name}
            ),
        )
        if exists is None:
            upper = next_month(month)
            _ = conn.execute(
                text(
                    f"CREATE TABLE {name} PARTITION OF {TABLE} "
                    f"FOR VALUES FROM ('{month:%Y-%m-%d}') TO ('{upper:%Y-%m-%d}')"
                )
            )
            harden_partition(conn, name)
            created.append(name)
        month = next_month(month)
    return created
```

- [ ] **Step 4: Write migration 0001**

`src/xolotl/migrations/versions/0001_kernel.py`:

```python
"""kernel: tenant, staff, api keys, idempotency, audit

Revision ID: 0001_kernel
Revises:
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.audit.partitions import (
    IMMUTABLE_FUNCTION,
    ensure_partitions,
    harden_partition,
)
from xolotl.platform.tenancy.ddl import (
    TENANT_TABLE_PREDICATE,
    enable_rls,
    grant_shared,
)

revision: str = "0001_kernel"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_IMMUTABLE_FN = f"""
CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    _ = op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("cell", sa.Text(), nullable=False, server_default="mx-1"),
        sa.Column("env", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant"),
        sa.UniqueConstraint("slug", name="uq_tenant_slug"),
    )
    enable_rls("tenant", predicate=TENANT_TABLE_PREDICATE)

    _ = op.create_table(
        "api_key_lookup",
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name="fk_api_key_lookup_tenant_id_tenant"
        ),
        sa.PrimaryKeyConstraint("key_prefix", name="pk_api_key_lookup"),
    )
    grant_shared("api_key_lookup")

    _ = op.create_table(
        "staff_user",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name="fk_staff_user_tenant_id_tenant"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_staff_user"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_staff_user_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_staff_user_tenant_id_email"),
    )
    enable_rls("staff_user")

    _ = op.create_table(
        "staff_credential",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("staff_user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("totp_secret", sa.LargeBinary(), nullable=True),
        sa.Column(
            "totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "failed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "staff_user_id"],
            ["staff_user.tenant_id", "staff_user.id"],
            name="fk_staff_credential_tenant_id_staff_user_id_staff_user",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_staff_credential"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_staff_credential_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "staff_user_id", name="uq_staff_credential_tenant_id_staff_user_id"
        ),
    )
    enable_rls("staff_credential")

    _ = op.create_table(
        "api_key",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("env", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["staff_user.tenant_id", "staff_user.id"],
            name="fk_api_key_tenant_id_created_by_staff_user",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_api_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_api_key_tenant_id_id"),
        sa.UniqueConstraint("key_prefix", name="uq_api_key_key_prefix"),
    )
    enable_rls("api_key")

    _ = op.create_table(
        "idempotency_key",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "actor_id", "key", name="pk_idempotency_key"),
    )
    enable_rls("idempotency_key")

    _ = op.create_table(
        "audit_event",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "at", "id", name="pk_audit_event"),
        postgresql_partition_by="RANGE (at)",
    )
    op.create_index("ix_audit_event_tenant_seq", "audit_event", ["tenant_id", "seq"])
    enable_rls("audit_event")
    op.execute(AUDIT_IMMUTABLE_FN)
    op.execute(
        "CREATE TRIGGER audit_event_no_update_delete BEFORE UPDATE OR DELETE "
        f"ON audit_event FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE "
        f"ON audit_event FOR EACH STATEMENT EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
    )
    op.execute("CREATE TABLE audit_event_default PARTITION OF audit_event DEFAULT")
    conn = op.get_bind()
    harden_partition(conn, "audit_event_default")
    _ = ensure_partitions(conn, months_ahead=3)


def downgrade() -> None:
    op.drop_table("audit_event")  # drops partitions, policies and triggers with it
    op.execute(f"DROP FUNCTION IF EXISTS {IMMUTABLE_FUNCTION}()")
    op.drop_table("idempotency_key")
    op.drop_table("api_key")
    op.drop_table("staff_credential")
    op.drop_table("staff_user")
    op.drop_table("api_key_lookup")
    op.drop_table("tenant")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_ddl.py tests/isolation/test_structural.py tests/platform/test_context_session.py -v --no-cov`
Expected: all pass. The structural test prints the offending table name on failure; if Postgres rejects the statement-level TRUNCATE trigger on the partitioned parent (older servers), keep the per-partition triggers created by `harden_partition` and drop the parent one; Postgres 17 accepts both.

- [ ] **Step 6: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean.

```bash
git add src/xolotl/platform/tenancy src/xolotl/platform/audit src/xolotl/migrations tests/isolation tests/platform/test_ddl.py
git commit -m "feat(tenancy): RLS DDL helpers, kernel migration 0001 and structural isolation test"
```

---

### Task 6: ORM models, schema-sync test and the behavioural isolation harness

**Implemented as** (plan errata; the code below is the plan as written): the forge step re-inserts tenant A's committed row in tenant B's session with a Core `INSERT` without `RETURNING`, so each table's own `WITH CHECK` rejects it, and the move test (a tenant's `UPDATE` of `tenant_id` to another tenant) is parametrized per table; the harness and env.py call `xolotl.models.metadata()` and `tests/isolation/factories.register_all()` instead of importing for side effects; tables in `SHARED_TABLES` are skipped even when they have a `tenant_id` column.

**Files:**
- Create: `src/xolotl/platform/sqltypes.py`, `src/xolotl/platform/audit/models.py`, `src/xolotl/platform/api/__init__.py` (empty), `src/xolotl/platform/api/models.py`, `src/xolotl/platform/tenancy/testing.py`
- Create: `src/xolotl/modules/tenants/__init__.py` (empty), `src/xolotl/modules/tenants/models.py`
- Modify: `src/xolotl/models.py` (add the two imports), `tests/helpers.py` (append `make_tenant`)
- Test: `tests/platform/test_schema_sync.py`, `tests/isolation/factories.py`, `tests/isolation/test_behavioural.py`

**Interfaces:**
- Consumes: `Base`, `tenant_session` (Task 4); `TenantTableMixin`, `tenant_table_args`, `tenant_fk` (Task 5); enums (Task 2).
- Produces: `sqltypes.enum_column(enum_cls)` (sentinel's helper: `sqlalchemy.Enum(enum_cls, native_enum=False, length=32, values_callable=...)`, so the column is a plain VARCHAR(32) and the attribute is typed as the enum); ORM classes `Tenant`, `ApiKeyLookup`, `StaffUser`, `StaffCredential`, `ApiKey` in `xolotl.modules.tenants.models`, `IdempotencyKey` in `xolotl.platform.api.models` (the idempotency layer is platform code) and `AuditEventRow` in `xolotl.platform.audit.models` (attribute names equal the column names of migration 0001); `testing.IsolationFactory = Callable[[Session, UUID], None]`, `testing.register_isolation_factory(table, factory)`, `testing.isolation_factories() -> dict[str, IsolationFactory]`; test helper `make_tenant(slug, *, env=TenantEnv.SANDBOX, status=TenantStatus.ACTIVE) -> UUID`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/helpers.py`:

```python
from xolotl.modules.tenants.models import Tenant  # noqa: E402
from xolotl.platform.db import tenant_session  # noqa: E402
from xolotl.platform.enums import TenantEnv, TenantStatus  # noqa: E402


def make_tenant(
    slug: str,
    *,
    env: TenantEnv = TenantEnv.SANDBOX,
    status: TenantStatus = TenantStatus.ACTIVE,
) -> UUID:
    with tenant_session(platform_ctx()) as s:
        tenant = Tenant(
            slug=slug,
            legal_name=slug.replace("-", " ").title(),
            country_code="MX",
            env=env,
            status=status,
        )
        s.add(tenant)
        s.flush()
        return tenant.id
```

(Move the three imports to the top of the file with the others when you edit it; the `noqa` markers are only needed if you append literally.)

`tests/platform/test_schema_sync.py`:

```python
"""The ORM metadata and the migrated schema must agree (no drift)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from xolotl import models  # noqa: F401  # registers every table
from xolotl.platform.db import Base


def _include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    if type_ == "table" and name is not None:
        # Partitions are created by ensure_partitions, not declared as models.
        return not (name.startswith("audit_event_") or name == "alembic_version")
    return True


def test_orm_metadata_matches_the_migrated_schema(admin_engine: Engine):
    with admin_engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_name": _include_name}
        )
        diffs = compare_metadata(ctx, Base.metadata)
    assert diffs == [], diffs


def test_every_table_with_tenant_id_uses_the_composite_unique():
    for table in Base.metadata.sorted_tables:
        if "tenant_id" not in table.c or table.name == "audit_event":
            continue
        unique_sets = [
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ in {"UniqueConstraint", "PrimaryKeyConstraint"}
        ]
        assert ("tenant_id", "id") in unique_sets or table.name == "idempotency_key", (
            table.name
        )
```

`tests/isolation/factories.py`:

```python
"""One row factory per tenant table for the behavioural isolation harness.

Each factory inserts exactly one row of its table for ``tenant_id`` (creating
any parent rows it needs) and flushes. Later modules append their factories
here when they add tables.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.modules.tenants.models import ApiKey, StaffCredential, StaffUser
from xolotl.platform import clock
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.enums import ApiKeyStatus, StaffStatus, TenantEnv
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.testing import register_isolation_factory


def _staff_user(session: Session, tenant_id: UUID) -> StaffUser:
    user = StaffUser(
        tenant_id=tenant_id,
        email=f"{uuid7().hex[:8]}@test.local",
        display_name="Test User",
        roles=["ANALYST"],
        status=StaffStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    return user


def staff_user(session: Session, tenant_id: UUID) -> None:
    _ = _staff_user(session, tenant_id)


def staff_credential(session: Session, tenant_id: UUID) -> None:
    user = _staff_user(session, tenant_id)
    session.add(
        StaffCredential(tenant_id=tenant_id, staff_user_id=user.id, password_hash="x")
    )
    session.flush()


def api_key(session: Session, tenant_id: UUID) -> None:
    user = _staff_user(session, tenant_id)
    session.add(
        ApiKey(
            tenant_id=tenant_id,
            name="harness",
            key_prefix=f"xk_sbx_{uuid7().hex[:8]}",
            key_hash=b"\x00" * 32,
            scopes=["tenants:read"],
            env=TenantEnv.SANDBOX,
            status=ApiKeyStatus.ACTIVE,
            created_by=user.id,
        )
    )
    session.flush()


def idempotency_key(session: Session, tenant_id: UUID) -> None:
    session.add(
        IdempotencyKey(
            tenant_id=tenant_id,
            actor_id="harness",
            key="k1",
            request_hash=b"\x00" * 32,
            status_code=200,
            response_body={"ok": True},
            expires_at=clock.now(),
        )
    )
    session.flush()


def audit_event(session: Session, tenant_id: UUID) -> None:
    session.add(
        AuditEventRow(
            tenant_id=tenant_id,
            at=clock.now(),
            id=uuid7(),
            seq=1,
            actor_type="SYSTEM",
            actor_id="harness",
            action="harness.row",
            prev_hash=b"\x00" * 32,
            hash=b"\x01" * 32,
        )
    )
    session.flush()


register_isolation_factory("staff_user", staff_user)
register_isolation_factory("staff_credential", staff_credential)
register_isolation_factory("api_key", api_key)
register_isolation_factory("idempotency_key", idempotency_key)
register_isolation_factory("audit_event", audit_event)
```

`tests/isolation/test_behavioural.py`:

```python
"""Behavioural half of the isolation harness (spec §4.4, item 2).

For every tenant table: tenant A inserts one row; tenant B reads zero rows,
updates and deletes nothing, and cannot insert a row carrying A's tenant_id.
A platform-operator session with no tenant sees nothing and raises nothing.
"""

import pytest
from sqlalchemy import Engine, Table, delete, func, select, update
from sqlalchemy.exc import ProgrammingError

from tests.helpers import make_tenant, platform_ctx, system_ctx
from tests.isolation import factories  # noqa: F401  # registers the factories
from xolotl import models  # noqa: F401
from xolotl.modules.tenants.models import Tenant
from xolotl.platform.db import Base, tenant_session
from xolotl.platform.tenancy.testing import isolation_factories


def _tenant_tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if "tenant_id" in t.c]


def _count(table: Table, tenant_id, *, ctx=None) -> int:
    with tenant_session(ctx or system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(table)) or 0


def test_every_tenant_table_has_a_factory():
    missing = {t.name for t in _tenant_tables()} - set(isolation_factories())
    assert not missing, f"register an isolation factory for: {sorted(missing)}"


@pytest.mark.parametrize("table_name", [t.name for t in _tenant_tables()])
def test_tenant_b_cannot_see_touch_or_forge_tenant_a_rows(engine: Engine, table_name):
    a, b = make_tenant("alpha"), make_tenant("beta")
    table = Base.metadata.tables[table_name]
    factory = isolation_factories()[table_name]

    with tenant_session(system_ctx(a)) as s:
        factory(s, a)
    assert _count(table, a) == 1

    with tenant_session(system_ctx(b)) as s:
        assert s.scalar(select(func.count()).select_from(table)) == 0
        assert s.execute(update(table).values(tenant_id=b)).rowcount == 0
        assert s.execute(delete(table)).rowcount == 0

    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_session(system_ctx(b)) as s,
    ):
        factory(s, a)

    assert _count(table, a) == 1
    assert _count(table, b) == 0


@pytest.mark.parametrize("table_name", [t.name for t in _tenant_tables()])
def test_platform_operator_without_tenant_sees_nothing(engine: Engine, table_name):
    a = make_tenant("alpha")
    table = Base.metadata.tables[table_name]
    with tenant_session(system_ctx(a)) as s:
        isolation_factories()[table_name](s, a)
    assert _count(table, a, ctx=platform_ctx()) == 0


def test_tenant_rows_are_visible_only_to_themselves_and_the_platform(engine: Engine):
    a, b = make_tenant("alpha"), make_tenant("beta")
    with tenant_session(system_ctx(a)) as s:
        assert [t.id for t in s.scalars(select(Tenant))] == [a]
    with tenant_session(system_ctx(b)) as s:
        assert [t.id for t in s.scalars(select(Tenant))] == [b]
    with tenant_session(platform_ctx()) as s:
        assert {t.id for t in s.scalars(select(Tenant))} == {a, b}


def test_tenant_cannot_move_its_row_to_another_tenant(engine: Engine):
    a, b = make_tenant("alpha"), make_tenant("beta")
    staff = Base.metadata.tables["staff_user"]
    with tenant_session(system_ctx(a)) as s:
        isolation_factories()["staff_user"](s, a)
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_session(system_ctx(a)) as s,
    ):
        _ = s.execute(update(staff).values(tenant_id=b))
    assert _count(staff, a) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_schema_sync.py tests/isolation -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.modules.tenants.models'`

- [ ] **Step 3: Write the column type, the models and the factory registry**

`src/xolotl/platform/sqltypes.py`:

```python
"""Column types shared by every module."""

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def enum_column[E: StrEnum](enum_cls: type[E]) -> SAEnum:
    """UPPERCASE enum values in a VARCHAR(32), never a native Postgres enum (D20).

    Stores the member value, not the member name, and skips native enums so
    adding a member never needs an ``ALTER TYPE`` migration. Alembic compares
    it as ``VARCHAR(32)``, which is what migration 0001 declares.
    """

    def values(members: type[E]) -> list[str]:
        return [member.value for member in members]

    return SAEnum(enum_cls, native_enum=False, length=32, values_callable=values)
```

`src/xolotl/platform/tenancy/testing.py`:

```python
"""Registry of row factories used by the behavioural isolation harness.

A factory inserts exactly one row of its table for the given tenant (plus any
parent rows it needs) and flushes. The harness in ``tests/isolation`` fails
when a tenant table has no factory, so every new table must register one.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

IsolationFactory = Callable[[Session, UUID], None]

_FACTORIES: dict[str, IsolationFactory] = {}


def register_isolation_factory(table: str, factory: IsolationFactory) -> None:
    _FACTORIES[table] = factory


def isolation_factories() -> dict[str, IsolationFactory]:
    return dict(_FACTORIES)
```

`src/xolotl/platform/audit/models.py`:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, LargeBinary, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xolotl.platform.db import Base


class AuditEventRow(Base):
    """One link of a tenant's audit chain. Partitioned monthly by ``at``."""

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_tenant_seq", "tenant_id", "seq"),
        {"postgresql_partition_by": "RANGE (at)"},
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary)
```

`src/xolotl/platform/api/models.py`:

```python
"""Kernel table behind the Idempotency-Key convention (spec §5, §7)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, LargeBinary, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xolotl.platform.db import Base


class IdempotencyKey(Base):
    """One reservation or stored response per ``(tenant, actor, key)``.

    ``status_code == 0`` marks a reservation whose request is still running.
    """

    __tablename__ = "idempotency_key"

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`src/xolotl/modules/tenants/models.py`:

```python
"""Tables owned by the tenants module (spec §5)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from xolotl.platform.db import Base
from xolotl.platform.enums import ApiKeyStatus, StaffStatus, TenantEnv, TenantStatus
from xolotl.platform.ids import uuid7
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    legal_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(String(2))
    cell: Mapped[str] = mapped_column(Text, default="mx-1", server_default="mx-1")
    env: Mapped[TenantEnv] = mapped_column(enum_column(TenantEnv))
    status: Mapped[TenantStatus] = mapped_column(enum_column(TenantStatus))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ApiKeyLookup(Base):
    """Shared table: resolves a key prefix to its tenant before any RLS context exists."""

    __tablename__ = "api_key_lookup"

    key_prefix: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenant.id"))


class StaffUser(TenantTableMixin, Base):
    __tablename__ = "staff_user"
    __table_args__ = tenant_table_args(
        "staff_user",
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        UniqueConstraint("tenant_id", "email"),
    )

    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    status: Mapped[StaffStatus] = mapped_column(enum_column(StaffStatus))
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class StaffCredential(TenantTableMixin, Base):
    __tablename__ = "staff_credential"
    __table_args__ = tenant_table_args(
        "staff_credential",
        tenant_fk("staff_user_id", "staff_user"),
        UniqueConstraint("tenant_id", "staff_user_id"),
    )

    staff_user_id: Mapped[UUID] = mapped_column(Uuid)
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ApiKey(TenantTableMixin, Base):
    __tablename__ = "api_key"
    __table_args__ = tenant_table_args(
        "api_key",
        tenant_fk("created_by", "staff_user"),
        UniqueConstraint("key_prefix"),
    )

    name: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    env: Mapped[TenantEnv] = mapped_column(enum_column(TenantEnv))
    status: Mapped[ApiKeyStatus] = mapped_column(enum_column(ApiKeyStatus))
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`src/xolotl/models.py` (replace the file):

```python
"""Imports every ORM model module so ``Base.metadata`` is complete.

Alembic's env.py and the schema-sync test import this module. Each feature
module adds its ``models`` import here when it creates its first table.
"""

from xolotl.modules.tenants import models as tenants_models
from xolotl.platform.api import models as api_models
from xolotl.platform.audit import models as audit_models

__all__ = ["api_models", "audit_models", "tenants_models"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_schema_sync.py tests/isolation -v --no-cov`
Expected: all pass. If `compare_metadata` reports a diff, the migration and the model disagree: fix whichever side departs from spec §5 (the migration is the source of truth for names; the model for types), never silence the test. Known benign diff to watch for: a `modify_type` on `ARRAY` columns means the migration used `sa.Text()` where the model used `String(32)` or vice versa.

- [ ] **Step 5: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean.

```bash
git add src/xolotl/platform/sqltypes.py src/xolotl/platform/audit/models.py src/xolotl/platform/api src/xolotl/platform/tenancy/testing.py src/xolotl/modules/tenants src/xolotl/models.py tests/helpers.py tests/platform/test_schema_sync.py tests/isolation
git commit -m "feat(tenants): kernel ORM models, schema-sync test and behavioural isolation harness"
```

---

### Task 7: Audit chain service

**Implemented as** (plan errata; the code below is the plan as written): the chain lives in `src/xolotl/platform/audit/chain.py` and `audit/__init__.py` only re-exports its names (ruff's RUF067 rejects definitions in a package `__init__`); `AuditEvent` rejects floats anywhere in `detail`, because JSONB does not round-trip them and an untouched row would fail verification; `verify_chain(session, tenant_id=None)` fails closed, raising `ContextMissingError` when the session has no tenant or `tenant_id` names another one; the package docstring states that tail truncation and a re-hashed suffix are not detectable without an external anchor of the chain head (program spec D10 follow-up).

**Files:**
- Modify: `src/xolotl/platform/audit/__init__.py` (was empty)
- Test: `tests/platform/test_audit.py`

**Interfaces:**
- Consumes: `AuditEventRow` (Task 6), `bound_context`, `tenant_session` (Task 4), `clock`, `uuid7` (Task 2), `ensure_partitions`, `partition_name` (Task 5), `make_tenant` (Task 6 helper).
- Produces: `GENESIS_HASH = bytes(32)`; `AuditEvent(action: str, entity_type: str | None = None, entity_id: str | None = None, detail: dict[str, JsonValue] | None = None)`; `ChainVerification(tenant_id: UUID, valid: bool, entries_checked: int, first_broken_seq: int | None = None, reason: str | None = None)`; `canonical_json(value: object) -> bytes`; `digest(row: AuditEventRow) -> bytes` (hash of the row's content and `prev_hash`, ignoring `row.hash`); `record(session, event) -> AuditEventRow` (actor and tenant come from the session's bound context; takes the per-tenant advisory lock; flushes); `verify_chain(session, tenant_id: UUID | None = None) -> ChainVerification`.

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_audit.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import ProgrammingError

from tests.helpers import make_tenant, platform_ctx, staff_ctx, system_ctx
from xolotl.platform import clock
from xolotl.platform.audit import (
    GENESIS_HASH,
    AuditEvent,
    canonical_json,
    record,
    verify_chain,
)
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.audit.partitions import ensure_partitions, partition_name
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.errors import ContextMissingError


def _record(tenant_id, action: str, **kwargs) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return record(s, AuditEvent(action=action, **kwargs)).seq


def test_canonical_json_is_sorted_compact_and_utf8():
    assert canonical_json({"b": 1, "a": "ñ", "c": [1.5, None, True]}) == (
        b'{"a":"\xc3\xb1","b":1,"c":[1.5,null,true]}'
    )


def test_chain_links_per_tenant(engine: Engine):
    a, b = make_tenant("alpha"), make_tenant("beta")
    assert _record(a, "test.one") == 1
    assert _record(a, "test.two", entity_type="x", entity_id="1", detail={"k": "v"}) == 2
    assert _record(b, "test.one") == 1  # tenant B has its own chain
    with tenant_session(system_ctx(a)) as s:
        rows = s.scalars(select(AuditEventRow).order_by(AuditEventRow.seq)).all()
        assert [r.seq for r in rows] == [1, 2]
        assert rows[0].prev_hash == GENESIS_HASH
        assert rows[1].prev_hash == rows[0].hash
        assert rows[0].actor_type == "SYSTEM"
        assert rows[0].actor_id == "job:test"
        assert rows[1].detail == {"k": "v"}
        assert verify_chain(s).model_dump(exclude={"tenant_id"}) == {
            "valid": True,
            "entries_checked": 2,
            "first_broken_seq": None,
            "reason": None,
        }


def test_record_uses_the_bound_actor(engine: Engine):
    a = make_tenant("alpha")
    with tenant_session(staff_ctx(a, actor_id="staff:42", roles=[Role.ADMIN])) as s:
        row = record(s, AuditEvent(action="auth.login_succeeded"))
        assert (row.actor_type, row.actor_id) == ("STAFF", "staff:42")


def test_record_requires_a_tenant_scoped_context(engine: Engine):
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        _ = record(s, AuditEvent(action="x"))


def test_concurrent_writers_get_distinct_sequence_numbers(engine: Engine):
    a = make_tenant("alpha")

    def work(n: int) -> list[int]:
        return [_record(a, f"concurrent.{n}.{i}") for i in range(5)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        seqs = sorted(s for result in pool.map(work, range(4)) for s in result)
    assert seqs == list(range(1, 21))
    with tenant_session(system_ctx(a)) as s:
        assert verify_chain(s).valid is True


def test_trigger_blocks_update_delete_and_truncate(engine: Engine, admin_engine: Engine):
    a = make_tenant("alpha")
    _ = _record(a, "test.one")
    for statement in (
        "UPDATE audit_event SET action = 'x'",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
        "TRUNCATE audit_event_default",
    ):
        with (
            pytest.raises(ProgrammingError, match="append-only"),
            admin_engine.begin() as conn,
        ):
            _ = conn.execute(text(statement))


def _tamper(admin_engine: Engine, statement: str, params: dict[str, object]) -> None:
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(text(statement), params)


def test_verify_detects_a_rewritten_row(engine: Engine, admin_engine: Engine):
    a = make_tenant("alpha")
    for n in range(3):
        _ = _record(a, f"test.{n}")
    _tamper(
        admin_engine,
        "UPDATE audit_event SET action = 'forged' WHERE tenant_id = :t AND seq = 2",
        {"t": a},
    )
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s)
    assert result.valid is False
    assert result.first_broken_seq == 2
    assert result.reason == "content does not match its hash"


def test_verify_detects_a_deleted_row(engine: Engine, admin_engine: Engine):
    a = make_tenant("alpha")
    for n in range(3):
        _ = _record(a, f"test.{n}")
    _tamper(admin_engine, "DELETE FROM audit_event WHERE tenant_id = :t AND seq = 2", {"t": a})
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s)
    assert (result.valid, result.first_broken_seq) == (False, 3)
    assert result.reason == "sequence gap or duplicate"


def test_verify_detects_a_relinked_row(engine: Engine, admin_engine: Engine):
    a = make_tenant("alpha")
    for n in range(2):
        _ = _record(a, f"test.{n}")
    _tamper(
        admin_engine,
        "UPDATE audit_event SET prev_hash = :h WHERE tenant_id = :t AND seq = 2",
        {"t": a, "h": b"\x09" * 32},
    )
    with tenant_session(system_ctx(a)) as s:
        result = verify_chain(s)
    assert (result.valid, result.first_broken_seq) == (False, 2)
    assert result.reason == "prev_hash does not match the previous entry"


def test_ensure_partitions_is_idempotent_and_routes_rows(
    engine: Engine, admin_engine: Engine, monkeypatch
):
    with admin_engine.begin() as conn:
        created = ensure_partitions(conn, start=date(2031, 1, 1), months_ahead=1)
        assert created == ["audit_event_y2031m01", "audit_event_y2031m02"]
        assert ensure_partitions(conn, start=date(2031, 1, 1), months_ahead=1) == []
        rls = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'audit_event_y2031m02'"
            )
        ).one()
        assert tuple(rls) == (True, True)
    a = make_tenant("alpha")
    monkeypatch.setattr(clock, "now", lambda: datetime(2031, 2, 14, 12, 0, tzinfo=UTC))
    with tenant_session(system_ctx(a)) as s:
        row = record(s, AuditEvent(action="future"))
        placed = s.scalar(
            text("SELECT tableoid::regclass::text FROM audit_event WHERE id = :id"),
            {"id": row.id},
        )
    assert placed == partition_name(date(2031, 2, 1))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_audit.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'GENESIS_HASH' from 'xolotl.platform.audit'`

- [ ] **Step 3: Write the audit service**

`src/xolotl/platform/audit/__init__.py`:

```python
"""Per-tenant hash-chained audit log (spec §8.3, program spec D10).

Each row stores the SHA-256 of its own content plus the previous row's hash
for the same tenant, so editing or removing any row breaks every hash after
it. A Postgres trigger rejects UPDATE, DELETE and TRUNCATE outright; the chain
catches anything that gets past the trigger, such as a superuser disabling it.
Writers serialise per tenant with a transaction-scoped advisory lock so ``seq``
is gap-free under concurrency.
"""

import hashlib
import json
from datetime import UTC
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7

GENESIS_HASH = bytes(32)

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext(CAST(:tenant_id AS text))::bigint)")


class AuditEvent(BaseModel):
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    detail: dict[str, JsonValue] | None = None


class ChainVerification(BaseModel):
    tenant_id: UUID
    valid: bool
    entries_checked: int
    first_broken_seq: int | None = None
    reason: str | None = None


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(row: AuditEventRow) -> bytes:
    """SHA-256 over the canonical JSON of the row's content plus ``prev_hash``."""
    payload: list[object] = [
        str(row.tenant_id),
        row.seq,
        row.at.astimezone(UTC).isoformat(),
        row.actor_type,
        row.actor_id,
        row.action,
        row.entity_type,
        row.entity_id,
        row.detail,
        row.prev_hash.hex(),
    ]
    return hashlib.sha256(canonical_json(payload)).digest()


def record(session: Session, event: AuditEvent) -> AuditEventRow:
    """Append an event for the session's tenant inside the caller's transaction."""
    ctx = bound_context(session)
    tenant_id = ctx.require_tenant_id()
    _ = session.execute(_LOCK_SQL, {"tenant_id": str(tenant_id)})
    last = session.execute(
        select(AuditEventRow.seq, AuditEventRow.hash)
        .where(AuditEventRow.tenant_id == tenant_id)
        .order_by(AuditEventRow.seq.desc())
        .limit(1)
    ).one_or_none()
    seq, prev_hash = (last[0] + 1, last[1]) if last is not None else (1, GENESIS_HASH)
    row = AuditEventRow(
        tenant_id=tenant_id,
        at=clock.now(),
        id=uuid7(),
        seq=seq,
        actor_type=ctx.actor_type.value,
        actor_id=ctx.actor_id,
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        detail=cast("dict[str, object] | None", event.detail),
        prev_hash=prev_hash,
        hash=b"",
    )
    row.hash = digest(row)
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session, tenant_id: UUID | None = None) -> ChainVerification:
    """Walk the tenant's chain in ``seq`` order and report the first break."""
    tenant_id = tenant_id or bound_context(session).require_tenant_id()
    expected_prev = GENESIS_HASH
    expected_seq = 1
    checked = 0
    rows = session.scalars(
        select(AuditEventRow)
        .where(AuditEventRow.tenant_id == tenant_id)
        .order_by(AuditEventRow.seq)
    )
    for row in rows:
        checked += 1
        reason: str | None = None
        if row.seq != expected_seq:
            reason = "sequence gap or duplicate"
        elif row.prev_hash != expected_prev:
            reason = "prev_hash does not match the previous entry"
        elif digest(row) != row.hash:
            reason = "content does not match its hash"
        if reason is not None:
            return ChainVerification(
                tenant_id=tenant_id,
                valid=False,
                entries_checked=checked,
                first_broken_seq=row.seq,
                reason=reason,
            )
        expected_prev = row.hash
        expected_seq += 1
    return ChainVerification(tenant_id=tenant_id, valid=True, entries_checked=checked)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_audit.py -v --no-cov`
Expected: 10 passed. If `test_concurrent_writers_get_distinct_sequence_numbers` reports duplicate seqs, the advisory lock is not being taken in the same transaction as the insert: check that `record` runs the lock statement on the same session before the `SELECT max`.

- [ ] **Step 5: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean.

```bash
git add src/xolotl/platform/audit tests/platform/test_audit.py
git commit -m "feat(audit): per-tenant hash chain with advisory-locked sequence and verification"
```

---

### Task 8: Logging with redaction, problem+json, request ids, health and the app factory

**Implemented as** (plan errata; the code below is the plan as written): `httpx2` joins the dev dependencies (Starlette's `TestClient` warns without it and `filterwarnings = error` turns that into a failure); unhandled 500s carry `X-Request-Id` and are logged with status 500 and the request id; the redaction filter also covers positional args, Pydantic models (their `pii` fields), sets and mapping messages, and scrubs database errors down to their class, sqlstate and frames, whole exception chains included; both engines use `hide_parameters=True`; `SENSITIVE_KEYS` gains `api_key` and `cookie`, with `-` normalised to `_`.

**Files:**
- Create: `src/xolotl/platform/logging.py`, `src/xolotl/platform/api/problems.py`, `src/xolotl/platform/api/request_id.py`, `src/xolotl/platform/api/health.py`, `src/xolotl/platform/api/app.py`, `src/xolotl/app.py`
- Modify: `tests/conftest.py` (append the `app` and `client` fixtures)
- Test: `tests/platform/test_logging.py`, `tests/platform/test_api_conventions.py`

**Interfaces:**
- Consumes: `XolotlError` hierarchy (Task 2), `current_context` (Task 3), `SessionFactory`, `get_sessionmaker`, `get_engine` (Task 4), `get_settings` (Task 1), `uuid7` (Task 2).
- Produces: `logging.request_id_var: ContextVar[str]`, `logging.SENSITIVE_KEYS`, `logging.REDACTED = "***"`, `logging.register_pii_model(model)`, `logging.is_sensitive_key(key) -> bool`, `logging.redact(value) -> object`, `logging.RedactionFilter`, `logging.JsonFormatter`, `logging.configure_logging(level)`; `problems.Problem`, `problems.ProblemError`, `problems.problem(request, *, status, code, detail, errors=None, headers=None) -> JSONResponse`, `problems.install_problem_handlers(app)`; `request_id.RequestIdMiddleware`; `health.router`; `api.app.AppState(session_factory)`, `api.app.app_state(request) -> AppState`, `api.app.create_app(*, session_factory, routers=()) -> FastAPI` (title `xolotl`, OpenAPI at `/v1/openapi.json`, docs at `/v1/docs`, operation ids = function names, routers mounted under `/v1`); `xolotl.app.build_app(session_factory=None) -> FastAPI` (composition root; later tasks register routers and the actor resolver here); fixtures `app` and `client`.

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_logging.py`:

```python
import io
import json
import logging
from uuid import uuid4

from pydantic import BaseModel, Field

from tests.helpers import system_ctx
from xolotl.platform.logging import (
    JsonFormatter,
    RedactionFilter,
    is_sensitive_key,
    redact,
    register_pii_model,
    request_id_var,
)
from xolotl.platform.tenancy.context import bind_context


def _capture() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger(f"test.{uuid4().hex}")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger, stream


def _last_line(stream: io.StringIO) -> dict:
    return json.loads(stream.getvalue().strip().splitlines()[-1])


def test_sensitive_key_matching():
    assert is_sensitive_key("password")
    assert is_sensitive_key("Access_Token")
    assert is_sensitive_key("AUTHORIZATION")
    assert is_sensitive_key("customer_rfc")
    assert is_sensitive_key("email")
    assert not is_sensitive_key("status")


def test_redact_walks_nested_structures():
    assert redact({"email": "a@b.mx", "n": {"token": "t", "ok": 1}, "l": [{"curp": "x"}]}) == {
        "email": "***",
        "n": {"token": "***", "ok": 1},
        "l": [{"curp": "***"}],
    }


def test_extra_fields_and_dict_args_are_redacted():
    logger, stream = _capture()
    logger.info(
        "login for %(email)s",
        {"email": "a@b.mx"},
        extra={"password": "hunter2", "detail": {"secret": "s", "count": 2}},
    )
    line = _last_line(stream)
    assert line["msg"] == "login for ***"
    assert line["password"] == "***"
    assert line["detail"] == {"secret": "***", "count": 2}
    assert line["level"] == "INFO"


def test_pii_tagged_pydantic_fields_are_redacted_after_registration():
    class Person(BaseModel):
        given_name: str = Field(json_schema_extra={"pii": True})
        city: str

    logger, stream = _capture()
    logger.info("before", extra={"given_name": "Ana", "city": "CDMX"})
    assert _last_line(stream)["given_name"] == "Ana"
    register_pii_model(Person)
    logger.info("after", extra={"given_name": "Ana", "city": "CDMX"})
    line = _last_line(stream)
    assert (line["given_name"], line["city"]) == ("***", "CDMX")


def test_request_id_and_tenant_id_are_included_when_bound():
    logger, stream = _capture()
    tenant_id = uuid4()
    token = request_id_var.set("req-123")
    try:
        with bind_context(system_ctx(tenant_id)):
            logger.info("hello")
    finally:
        request_id_var.reset(token)
    line = _last_line(stream)
    assert line["request_id"] == "req-123"
    assert line["tenant_id"] == str(tenant_id)
    logger.info("outside")
    assert "request_id" not in _last_line(stream)


def test_exceptions_are_serialised():
    logger, stream = _capture()
    try:
        _ = 1 / 0
    except ZeroDivisionError:
        logger.exception("failed")
    assert "ZeroDivisionError" in _last_line(stream)["exc_info"]
```

Append to `tests/conftest.py`:

```python
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from xolotl.app import build_app  # noqa: E402


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    return build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
```

(Move the imports to the top of the file.)

`tests/platform/test_api_conventions.py`:

```python
import re

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from xolotl.app import build_app
from xolotl.platform.errors import (
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance", "code"}


def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/domain")
    def domain() -> dict[str, str]:
        raise DomainError("duplicate slug")

    @router.get("/probe/notfound")
    def notfound() -> dict[str, str]:
        raise NotFoundError

    @router.get("/probe/unauth")
    def unauth() -> dict[str, str]:
        raise UnauthorizedError

    @router.get("/probe/forbidden")
    def forbidden() -> dict[str, str]:
        raise ForbiddenError("scope tenants:read required", code="SCOPE_MISSING")

    @router.get("/probe/boom")
    def boom() -> dict[str, str]:
        msg = "kaboom"
        raise RuntimeError(msg)

    @router.get("/probe/echo")
    def echo(q: int) -> dict[str, int]:
        return {"q": q}

    return router


@pytest.fixture
def probe_app(engine: Engine) -> FastAPI:
    app = build_app()
    app.include_router(_probe_router(), prefix="/v1")
    return app


@pytest.fixture
def probe(probe_app: FastAPI):
    with TestClient(probe_app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "status", "code", "detail"),
    [
        ("/v1/probe/domain", 409, "CONFLICT", "duplicate slug"),
        ("/v1/probe/notfound", 404, "NOT_FOUND", "not found"),
        ("/v1/probe/unauth", 401, "UNAUTHENTICATED", "authentication required"),
        ("/v1/probe/forbidden", 403, "SCOPE_MISSING", "scope tenants:read required"),
        ("/v1/probe/boom", 500, "INTERNAL", "internal error"),
        ("/v1/no/such/route", 404, "NOT_FOUND", "Not Found"),
    ],
)
def test_errors_are_problem_json_with_stable_codes(probe, path, status, code, detail):
    resp = probe.get(path)
    assert resp.status_code == status
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert PROBLEM_KEYS <= set(body)
    assert body["code"] == code
    assert body["type"] == f"https://xolotl.dev/problems/{code}"
    assert body["status"] == status
    assert body["detail"] == detail
    assert body["instance"] == path
    if status == 401:
        assert resp.headers["www-authenticate"] == "Bearer"


def test_validation_errors_carry_locations(probe):
    resp = probe.get("/v1/probe/echo", params={"q": "abc"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["errors"][0]["loc"] == ["query", "q"]
    assert body["errors"][0]["msg"]
    assert body["errors"][0]["type"]


def test_method_not_allowed_is_a_problem(probe):
    resp = probe.post("/v1/probe/echo")
    assert resp.status_code == 405
    assert resp.json()["code"] == "METHOD_NOT_ALLOWED"


def test_request_id_is_echoed_or_generated(client: TestClient):
    resp = client.get("/healthz", headers={"X-Request-Id": "abc-123_X.y"})
    assert resp.headers["x-request-id"] == "abc-123_X.y"
    generated = client.get("/healthz").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    rejected = client.get("/healthz", headers={"X-Request-Id": "has spaces"})
    assert rejected.headers["x-request-id"] != "has spaces"
    too_long = client.get("/healthz", headers={"X-Request-Id": "x" * 129})
    assert len(too_long.headers["x-request-id"]) == 32


def test_health_and_ready(client: TestClient):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok", "database": "ok"}


def test_openapi_lives_under_v1_with_function_operation_ids(client: TestClient):
    spec = client.get("/v1/openapi.json").json()
    assert spec["info"]["title"] == "xolotl"
    assert spec["paths"]["/healthz"]["get"]["operationId"] == "healthz"
    assert client.get("/v1/docs").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_logging.py tests/platform/test_api_conventions.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.platform.logging'` (and conftest fails importing `xolotl.app`).

- [ ] **Step 3: Write the logging module**

`src/xolotl/platform/logging.py`:

```python
"""JSON-lines logging with PII redaction (spec §7).

Values of keys named like a credential or an identifier (``password``,
``token``, ``secret``, ``authorization``, ``curp``, ``rfc``, ``clabe``,
``email``) and of any Pydantic field tagged ``json_schema_extra={"pii": True}``
are replaced by ``***`` in ``extra`` fields and in dict-style message
arguments before a record is formatted.
"""

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import cast, override

from pydantic import BaseModel

from xolotl.platform.tenancy.context import current_context

request_id_var: ContextVar[str] = ContextVar("xolotl_request_id", default="")

SENSITIVE_KEYS = frozenset(
    {"password", "token", "secret", "authorization", "curp", "rfc", "clabe", "email"}
)
REDACTED = "***"
_PII_FIELDS: set[str] = set()
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None))
) | {"message", "asctime", "taskName"}


def register_pii_model(model: type[BaseModel]) -> None:
    """Mark every field of ``model`` tagged ``{"pii": True}`` as sensitive."""
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("pii") is True:
            _PII_FIELDS.add(name)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _PII_FIELDS or any(part in lowered for part in SENSITIVE_KEYS)


def redact(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {
            str(k): REDACTED if is_sensitive_key(str(k)) else redact(v)
            for k, v in mapping.items()
        }
    if isinstance(value, list | tuple):
        items = cast("list[object] | tuple[object, ...]", value)
        return [redact(v) for v in items]
    return value


class RedactionFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, Mapping):
            record.args = cast("Mapping[str, object]", redact(record.args))
        for key in list(vars(record)):
            if key in _STANDARD_ATTRS:
                continue
            value: object = getattr(record, key)
            setattr(record, key, REDACTED if is_sensitive_key(key) else redact(value))
        return True


class JsonFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if request_id := request_id_var.get():
            payload["request_id"] = request_id
        ctx = current_context.get()
        if ctx is not None and ctx.tenant_id is not None:
            payload["tenant_id"] = str(ctx.tenant_id)
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS:
                payload[key] = cast("object", value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's access log duplicates the request line emitted by RequestIdMiddleware.
    logging.getLogger("uvicorn.access").disabled = True
```

- [ ] **Step 4: Write the problem handlers, middleware, health router and app factory**

`src/xolotl/platform/api/problems.py`:

```python
"""RFC 9457 problem responses with stable codes (spec §7)."""

import logging
from collections.abc import Mapping
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from xolotl.platform.errors import XolotlError

logger = logging.getLogger(__name__)

PROBLEM_TYPE_BASE = "https://xolotl.dev/problems/"
MEDIA_TYPE = "application/problem+json"
_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Content",
    500: "Internal Server Error",
    503: "Service Unavailable",
}
_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


class ProblemError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    errors: list[ProblemError] | None = None


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    errors: list[ProblemError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        type=PROBLEM_TYPE_BASE + code,
        title=_TITLES.get(status, "Error"),
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        errors=errors,
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=MEDIA_TYPE,
        headers=dict(headers) if headers else None,
    )


def _expect[E: Exception](exc: Exception, cls: type[E]) -> E:
    if not isinstance(exc, cls):  # Starlette types handlers over plain Exception
        msg = f"handler for {cls.__name__} received {type(exc).__name__}"
        raise TypeError(msg)
    return exc


def _xolotl_error(request: Request, exc: Exception) -> Response:
    error = _expect(exc, XolotlError)
    headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
    return problem(
        request, status=error.status, code=error.code, detail=error.detail, headers=headers
    )


def _validation_error(request: Request, exc: Exception) -> Response:
    error = _expect(exc, RequestValidationError)
    raw = cast("list[dict[str, object]]", list(error.errors()))
    errors = [
        ProblemError(
            loc=[
                part if isinstance(part, int) else str(part)
                for part in cast("tuple[object, ...]", item.get("loc", ()))
            ],
            msg=str(item.get("msg", "")),
            type=str(item.get("type", "")),
        )
        for item in raw
    ]
    return problem(
        request,
        status=422,
        code="VALIDATION_FAILED",
        detail="request validation failed",
        errors=errors,
    )


def _http_exception(request: Request, exc: Exception) -> Response:
    error = _expect(exc, HTTPException)
    code = _HTTP_CODES.get(error.status_code, f"HTTP_{error.status_code}")
    return problem(
        request,
        status=error.status_code,
        code=code,
        detail=str(error.detail),
        headers=error.headers,
    )


def _unhandled(request: Request, exc: Exception) -> Response:
    logger.exception("unhandled error", exc_info=exc)
    return problem(request, status=500, code="INTERNAL", detail="internal error")


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(XolotlError, _xolotl_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_exception)
    app.add_exception_handler(Exception, _unhandled)
```

`src/xolotl/platform/api/request_id.py`:

```python
"""Request id propagation and one JSON log line per request."""

import logging
import re
import time
from typing import cast

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from xolotl.platform.ids import uuid7
from xolotl.platform.logging import request_id_var

logger = logging.getLogger("xolotl.request")
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


class RequestIdMiddleware:
    """Honour a well-formed ``X-Request-Id`` or generate one; echo it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if cast("str", scope["type"]) != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid7().hex
        status = {"code": 0}
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            if cast("str", message["type"]) == "http.response.start":
                status["code"] = cast("int", message["status"])
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
            logger.info(
                "request",
                extra={
                    "method": cast("str", scope["method"]),
                    "path": cast("str", scope["path"]),
                    "status": status["code"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": request_id,
                },
            )
```

`src/xolotl/platform/api/health.py`:

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from xolotl.platform.api.problems import problem
from xolotl.platform.db import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    try:
        with get_engine().connect() as conn:
            _ = conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001  # readiness must never raise
        return problem(request, status=503, code="NOT_READY", detail="database unreachable")
    return JSONResponse({"status": "ok", "database": "ok"})
```

`src/xolotl/platform/api/app.py`:

```python
"""FastAPI application factory carrying the kernel conventions (spec §7)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute

from xolotl import __version__
from xolotl.platform.api.health import router as health_router
from xolotl.platform.api.problems import install_problem_handlers
from xolotl.platform.api.request_id import RequestIdMiddleware
from xolotl.platform.db import SessionFactory


@dataclass(slots=True)
class AppState:
    session_factory: SessionFactory


def app_state(request: Request) -> AppState:
    return cast("AppState", request.app.state.xolotl)


def _operation_id(route: APIRoute) -> str:
    return route.name


def create_app(
    *, session_factory: SessionFactory, routers: Sequence[APIRouter] = ()
) -> FastAPI:
    app = FastAPI(
        title="xolotl",
        version=__version__,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )
    app.state.xolotl = AppState(session_factory=session_factory)
    app.add_middleware(RequestIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health_router)
    for router in routers:
        app.include_router(router, prefix="/v1")
    return app
```

`src/xolotl/app.py`:

```python
"""Composition root: binds adapters to ports and mounts the module routers.

``build_app`` is what ``xolotl serve`` runs (as a uvicorn factory) and what the
tests build. Each later task registers its router here.
"""

from fastapi import FastAPI

from xolotl.platform.api.app import create_app
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.logging import configure_logging


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    configure_logging(get_settings().log_level)
    return create_app(session_factory=session_factory or get_sessionmaker(), routers=[])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_logging.py tests/platform/test_api_conventions.py -v --no-cov`
Expected: all pass. The 500 case only works with `raise_server_exceptions=False` (set in the `probe` fixture); the standard `client` fixture re-raises server errors, which is what the other suites want.

- [ ] **Step 6: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean. `configure_logging` replacing root handlers is intentional; pytest's own capturing still works because caplog attaches its handler to the root logger per test.

```bash
git add src/xolotl/platform/logging.py src/xolotl/platform/api src/xolotl/app.py tests/conftest.py tests/platform/test_logging.py tests/platform/test_api_conventions.py
git commit -m "feat(api): json logging with redaction, problem+json handlers, request ids, health and app factory"
```

---

### Task 9: Auth primitives and the principal layer

**Files:**
- Create: `src/xolotl/platform/auth/__init__.py` (empty), `src/xolotl/platform/auth/passwords.py`, `src/xolotl/platform/auth/tokens.py`, `src/xolotl/platform/auth/totp.py`, `src/xolotl/platform/auth/actor.py`, `src/xolotl/platform/auth/principal.py`
- Modify: `src/xolotl/platform/api/app.py` (add `actor_resolver` to `AppState` and `create_app`)
- Test: `tests/platform/test_auth_primitives.py`, `tests/platform/test_principal.py`

**Interfaces:**
- Consumes: `get_settings` (Task 1), `clock`, `uuid7`, enums, errors (Task 2), `TenantContext`, `bind_context` (Task 3), `SessionFactory`, `apply_context` (Task 4), `request_id_var` (Task 8), `app_state`, `AppState`, `create_app` (Task 8).
- Produces:
  - `passwords.MIN_PASSWORD_LENGTH = 12`, `hash_password(password) -> str`, `verify_password(password_hash, password) -> bool`, `generate_password() -> str` (24 URL-safe characters), `check_password_strength(password) -> None` (raises `ValidationFailedError` under 12 characters).
  - `tokens.TokenPurpose = Literal["access", "mfa"]`, `tokens.TokenClaims` (pydantic: `iss, sub, tenant_id: UUID, tenant_slug, roles: list[Role], actor_type: ActorType, purpose, iat: int, exp: int, jti`), `issue_token(*, subject, tenant_id, tenant_slug, roles, purpose, ttl: timedelta) -> str`, `decode_token(token, *, purpose) -> TokenClaims` (raises `UnauthorizedError`).
  - `totp.new_secret() -> str`, `otpauth_uri(secret, *, email, tenant_slug) -> str`, `verify_code(secret, code) -> bool`, `current_code(secret) -> str`.
  - `actor.Actor(context: TenantContext, scopes: frozenset[str] = frozenset(), must_change_password: bool = False)` with `is_staff`/`is_api_client` properties; `actor.ActorResolver = Callable[[SessionFactory, str], Actor]` (takes the app's session factory and the bearer token; raises `UnauthorizedError`/`ForbiddenError`).
  - `principal.resolve_request_actor(request) -> Actor` (sync, cached on `request.state.actor`, stamps `request_id` into the context), `principal.current_actor` (async generator dependency binding the contextvar), `principal.ActorDep`, `principal.get_session(request, actor) -> Iterator[Session]` (stores `request.state.session`, applies the context, commits/rolls back, closes), `principal.SessionDep`, `principal.require_access(*, roles=(), scope=None, write=False)` dependency factory, `principal.current_staff`, `principal.StaffDep`.
  - `api.app.AppState(session_factory, actor_resolver: ActorResolver | None = None)`, `create_app(*, session_factory, actor_resolver=None, routers=())`; with no resolver every bearer is rejected with 401.

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_auth_primitives.py`:

```python
from datetime import timedelta
from uuid import uuid4

import jwt
import pytest

from xolotl.platform.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    check_password_strength,
    generate_password,
    hash_password,
    verify_password,
)
from xolotl.platform.auth.tokens import decode_token, issue_token
from xolotl.platform.auth.totp import current_code, new_secret, otpauth_uri, verify_code
from xolotl.platform.config import get_settings
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import UnauthorizedError, ValidationFailedError


def test_password_hash_round_trip():
    digest = hash_password("correct horse battery")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "correct horse battery")
    assert not verify_password(digest, "wrong")
    assert not verify_password("not-a-hash", "whatever")


def test_generated_passwords_are_long_and_unique():
    first, second = generate_password(), generate_password()
    assert len(first) >= 20
    assert first != second
    check_password_strength(first)


def test_password_strength_minimum():
    with pytest.raises(ValidationFailedError):
        check_password_strength("x" * (MIN_PASSWORD_LENGTH - 1))
    check_password_strength("x" * MIN_PASSWORD_LENGTH)


def _issue(**overrides):
    kwargs = {
        "subject": "staff-1",
        "tenant_id": uuid4(),
        "tenant_slug": "acme",
        "roles": [Role.MLRO, Role.ADMIN],
        "purpose": "access",
        "ttl": timedelta(minutes=5),
    }
    kwargs.update(overrides)
    return kwargs, issue_token(**kwargs)


def test_token_round_trip_carries_oidc_shaped_claims():
    kwargs, token = _issue()
    claims = decode_token(token, purpose="access")
    assert claims.iss == get_settings().jwt_issuer
    assert claims.sub == "staff-1"
    assert claims.tenant_id == kwargs["tenant_id"]
    assert claims.tenant_slug == "acme"
    assert claims.roles == [Role.ADMIN, Role.MLRO]
    assert claims.actor_type is ActorType.STAFF
    assert claims.purpose == "access"
    assert claims.exp - claims.iat == 300
    assert len(claims.jti) == 32


def test_expired_tampered_wrong_purpose_and_wrong_issuer_are_rejected():
    _, expired = _issue(ttl=timedelta(seconds=-1))
    with pytest.raises(UnauthorizedError):
        decode_token(expired, purpose="access")
    _, good = _issue()
    tampered = good[:-2] + ("AA" if not good.endswith("AA") else "BB")
    with pytest.raises(UnauthorizedError):
        decode_token(tampered, purpose="access")
    _, mfa = _issue(purpose="mfa")
    with pytest.raises(UnauthorizedError):
        decode_token(mfa, purpose="access")
    assert decode_token(mfa, purpose="mfa").purpose == "mfa"
    foreign = jwt.encode(
        {"iss": "someone-else", "sub": "x", "exp": 4_102_444_800, "iat": 0},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        decode_token(foreign, purpose="access")
    with pytest.raises(UnauthorizedError):
        decode_token("garbage", purpose="access")


def test_totp_enrolment_and_verification():
    secret = new_secret()
    assert len(secret) == 32
    uri = otpauth_uri(secret, email="ana@acme.mx", tenant_slug="acme")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=xolotl%3Aacme" in uri
    assert verify_code(secret, current_code(secret))
    assert not verify_code(secret, "000000")
```

`tests/platform/test_principal.py`:

```python
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import staff_ctx
from xolotl.platform.api.app import create_app
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import (
    ActorDep,
    SessionDep,
    StaffDep,
    require_access,
    resolve_request_actor,
)
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import UnauthorizedError
from xolotl.platform.tenancy.context import TenantContext, require_context

TENANT = uuid4()
RESOLVES: list[str] = []


def fake_resolver(_factory: SessionFactory, token: str) -> Actor:
    RESOLVES.append(token)
    match token:
        case "staff-admin":
            return Actor(staff_ctx(TENANT, actor_id="u-admin", roles=[Role.ADMIN]))
        case "staff-analyst":
            return Actor(staff_ctx(TENANT, actor_id="u-analyst", roles=[Role.ANALYST]))
        case "staff-auditor":
            return Actor(staff_ctx(TENANT, actor_id="u-aud", roles=[Role.AUDITOR_RO]))
        case "staff-pw":
            return Actor(
                staff_ctx(TENANT, actor_id="u-pw", roles=[Role.ADMIN]),
                must_change_password=True,
            )
        case "machine-read":
            ctx = TenantContext(
                tenant_id=TENANT, actor_type=ActorType.API_CLIENT, actor_id="key-1"
            )
            return Actor(ctx, scopes=frozenset({"tenants:read"}))
    raise UnauthorizedError


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/whoami")
    def whoami(request: Request, actor: ActorDep) -> dict[str, str]:
        again = resolve_request_actor(request)
        assert again is actor
        return {
            "actor_id": actor.context.actor_id,
            "bound": require_context().actor_id,
            "request_id": require_context().request_id,
        }

    @router.post("/admin-write")
    def admin_write(
        actor: Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/staff-read")
    def staff_read(actor: Annotated[Actor, Depends(require_access())]) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/machine-read")
    def machine_read(
        actor: Annotated[Actor, Depends(require_access(scope="tenants:read"))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.post("/machine-write")
    def machine_write(
        actor: Annotated[Actor, Depends(require_access(scope="tenants:write"))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/staff-only")
    def staff_only(actor: StaffDep) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/session-ctx")
    def session_ctx(session: SessionDep) -> dict[str, str | None]:
        return {
            "tenant_id": session.scalar(text("SELECT current_setting('app.tenant_id', true)")),
            "actor_id": session.scalar(text("SELECT current_setting('app.actor_id', true)")),
        }

    return router


@pytest.fixture
def api(engine: Engine):
    RESOLVES.clear()
    app = create_app(
        session_factory=get_sessionmaker(), actor_resolver=fake_resolver, routers=[_router()]
    )
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_or_malformed_bearer_is_401(api: TestClient):
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}):
        resp = api.get("/v1/whoami", headers=headers)
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHENTICATED"
        assert resp.headers["www-authenticate"] == "Bearer"
    assert api.get("/v1/whoami", headers=_auth("nope")).status_code == 401


def test_actor_is_resolved_once_and_bound_for_the_request(api: TestClient):
    resp = api.get("/v1/whoami", headers={**_auth("staff-admin"), "X-Request-Id": "r-1"})
    assert resp.json() == {"actor_id": "u-admin", "bound": "u-admin", "request_id": "r-1"}
    assert RESOLVES == ["staff-admin"]


def test_role_and_write_rules(api: TestClient):
    assert api.post("/v1/admin-write", headers=_auth("staff-admin")).status_code == 200
    denied = api.post("/v1/admin-write", headers=_auth("staff-analyst"))
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
    auditor = api.post("/v1/admin-write", headers=_auth("staff-auditor"))
    assert (auditor.status_code, auditor.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert api.get("/v1/staff-read", headers=_auth("staff-auditor")).status_code == 200


def test_password_change_required_blocks_everything_but_plain_actor_routes(api: TestClient):
    blocked = api.get("/v1/staff-read", headers=_auth("staff-pw"))
    assert (blocked.status_code, blocked.json()["code"]) == (403, "PASSWORD_CHANGE_REQUIRED")
    assert api.get("/v1/whoami", headers=_auth("staff-pw")).status_code == 200
    assert api.get("/v1/staff-only", headers=_auth("staff-pw")).status_code == 200


def test_scope_rules_for_api_clients(api: TestClient):
    assert api.get("/v1/machine-read", headers=_auth("machine-read")).status_code == 200
    missing = api.post("/v1/machine-write", headers=_auth("machine-read"))
    assert (missing.status_code, missing.json()["code"]) == (403, "SCOPE_MISSING")
    staff_only = api.get("/v1/staff-read", headers=_auth("machine-read"))
    assert (staff_only.status_code, staff_only.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert api.get("/v1/staff-only", headers=_auth("machine-read")).status_code == 403
    assert api.get("/v1/machine-read", headers=_auth("staff-admin")).status_code == 200


def test_session_dependency_runs_under_the_actor_context(api: TestClient):
    resp = api.get("/v1/session-ctx", headers=_auth("staff-analyst"))
    assert resp.json() == {"tenant_id": str(TENANT), "actor_id": "u-analyst"}


def test_app_without_resolver_rejects_every_bearer(engine: Engine):
    app = create_app(session_factory=get_sessionmaker(), routers=[_router()])
    with TestClient(app) as c:
        assert c.get("/v1/whoami", headers=_auth("staff-admin")).status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_auth_primitives.py tests/platform/test_principal.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.platform.auth'`

- [ ] **Step 3: Write the primitives**

`src/xolotl/platform/auth/passwords.py`:

```python
"""Password hashing (argon2id), generation and the minimum-length rule."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from xolotl.platform.errors import ValidationFailedError

MIN_PASSWORD_LENGTH = 12
_hasher = PasswordHasher()  # argon2id with the library's current defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        verified = _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return verified


def generate_password() -> str:
    """A one-time initial password: 24 URL-safe characters (144 bits)."""
    return secrets.token_urlsafe(18)


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        msg = f"password must have at least {MIN_PASSWORD_LENGTH} characters"
        raise ValidationFailedError(msg)
```

`src/xolotl/platform/auth/tokens.py`:

```python
"""HS256 JWTs for staff sessions with OIDC-shaped claims (spec §6.1, D23)."""

from collections.abc import Iterable
from datetime import timedelta
from typing import Literal
from uuid import UUID

import jwt
from pydantic import BaseModel, ValidationError

from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import UnauthorizedError
from xolotl.platform.ids import uuid7

ALGORITHM = "HS256"
type TokenPurpose = Literal["access", "mfa"]


class TokenClaims(BaseModel):
    iss: str
    sub: str
    tenant_id: UUID
    tenant_slug: str
    roles: list[Role]
    actor_type: ActorType
    purpose: TokenPurpose
    iat: int
    exp: int
    jti: str


def issue_token(
    *,
    subject: str,
    tenant_id: UUID,
    tenant_slug: str,
    roles: Iterable[Role],
    purpose: TokenPurpose,
    ttl: timedelta,
) -> str:
    settings = get_settings()
    now = clock.now()
    claims = TokenClaims(
        iss=settings.jwt_issuer,
        sub=subject,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        roles=sorted(roles),
        actor_type=ActorType.STAFF,
        purpose=purpose,
        iat=int(now.timestamp()),
        exp=int((now + ttl).timestamp()),
        jti=uuid7().hex,
    )
    return jwt.encode(claims.model_dump(mode="json"), settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, *, purpose: TokenPurpose) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        claims = TokenClaims.model_validate(payload)
    except (jwt.PyJWTError, ValidationError) as exc:
        msg = "invalid token"
        raise UnauthorizedError(msg) from exc
    if claims.purpose != purpose:
        msg = "invalid token"
        raise UnauthorizedError(msg)
    return claims
```

`src/xolotl/platform/auth/totp.py`:

```python
"""TOTP enrolment and verification (RFC 6238) via pyotp."""

import pyotp

ISSUER_PREFIX = "xolotl"


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, *, email: str, tenant_slug: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=email, issuer_name=f"{ISSUER_PREFIX}:{tenant_slug}"
    )


def verify_code(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def current_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()
```

`src/xolotl/platform/auth/actor.py`:

```python
"""The resolved caller of a request."""

from collections.abc import Callable
from dataclasses import dataclass, field

from xolotl.platform.db import SessionFactory
from xolotl.platform.enums import ActorType
from xolotl.platform.tenancy.context import TenantContext


@dataclass(frozen=True, slots=True)
class Actor:
    context: TenantContext
    scopes: frozenset[str] = field(default_factory=frozenset)
    must_change_password: bool = False

    @property
    def is_staff(self) -> bool:
        return self.context.actor_type is ActorType.STAFF

    @property
    def is_api_client(self) -> bool:
        return self.context.actor_type is ActorType.API_CLIENT


# (session_factory, bearer token) -> Actor; raises UnauthorizedError or ForbiddenError.
ActorResolver = Callable[[SessionFactory, str], Actor]
```

- [ ] **Step 4: Extend the app factory and write the principal layer**

Modify `src/xolotl/platform/api/app.py`:

```python
from xolotl.platform.auth.actor import ActorResolver  # add to the imports


@dataclass(slots=True)
class AppState:
    session_factory: SessionFactory
    actor_resolver: ActorResolver | None = None


def create_app(
    *,
    session_factory: SessionFactory,
    actor_resolver: ActorResolver | None = None,
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    app = FastAPI(
        title="xolotl",
        version=__version__,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )
    app.state.xolotl = AppState(
        session_factory=session_factory, actor_resolver=actor_resolver
    )
    app.add_middleware(RequestIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health_router)
    for router in routers:
        app.include_router(router, prefix="/v1")
    return app
```

`src/xolotl/platform/auth/principal.py`:

```python
"""Who is calling: bearer resolution, session binding and access checks (spec §6).

``current_actor`` is an *async* dependency on purpose: FastAPI runs async
dependencies in the event-loop task, so the contextvar it binds is visible to
sync endpoints, which Starlette runs in a threadpool with a copy of the
current context. The blocking resolver itself runs in the threadpool.
"""

from collections.abc import AsyncIterator, Callable, Collection, Iterator
from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from xolotl.platform.api.app import app_state
from xolotl.platform.auth.actor import Actor
from xolotl.platform.db import apply_context
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ForbiddenError, UnauthorizedError
from xolotl.platform.logging import request_id_var
from xolotl.platform.tenancy.context import bind_context


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError
    return token.strip()


def resolve_request_actor(request: Request) -> Actor:
    """Resolve the bearer once per request; later calls return the cached actor."""
    cached: object = getattr(request.state, "actor", None)
    if isinstance(cached, Actor):
        return cached
    state = app_state(request)
    if state.actor_resolver is None:
        raise UnauthorizedError
    resolved = state.actor_resolver(state.session_factory, _bearer_token(request))
    actor = replace(
        resolved, context=replace(resolved.context, request_id=request_id_var.get())
    )
    request.state.actor = actor
    return actor


async def current_actor(request: Request) -> AsyncIterator[Actor]:
    actor = await run_in_threadpool(resolve_request_actor, request)
    with bind_context(actor.context):
        yield actor


ActorDep = Annotated[Actor, Depends(current_actor)]


def get_session(request: Request, actor: ActorDep) -> Iterator[Session]:
    """A session bound to the actor's context; commits on success, rolls back on error."""
    session = app_state(request).session_factory()
    request.state.session = session
    try:
        apply_context(session, actor.context)
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def require_access(
    *, roles: Collection[Role] = (), scope: str | None = None, write: bool = False
) -> Callable[[Actor], Actor]:
    """Dependency factory.

    Staff: must not have a pending password change; ``AUDITOR_RO`` may never
    write; when ``roles`` is given one of them is required. API clients: need
    ``scope`` (a route without a scope is staff-only).
    """

    def dependency(actor: ActorDep) -> Actor:
        ctx = actor.context
        if ctx.actor_type is ActorType.STAFF:
            if actor.must_change_password:
                msg = "password change required"
                raise ForbiddenError(msg, code="PASSWORD_CHANGE_REQUIRED")
            if write and Role.AUDITOR_RO in ctx.roles:
                msg = "AUDITOR_RO is read-only"
                raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
            if roles and not ctx.roles.intersection(roles):
                msg = f"requires one of {sorted(role.value for role in roles)}"
                raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
            return actor
        if ctx.actor_type is ActorType.API_CLIENT:
            if scope is None:
                msg = "staff only"
                raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
            if scope not in actor.scopes:
                msg = f"scope {scope} required"
                raise ForbiddenError(msg, code="SCOPE_MISSING")
            return actor
        raise ForbiddenError

    return dependency


def current_staff(actor: ActorDep) -> Actor:
    """Any staff member, including one who still has to change their password."""
    if not actor.is_staff:
        msg = "staff only"
        raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
    return actor


StaffDep = Annotated[Actor, Depends(current_staff)]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_auth_primitives.py tests/platform/test_principal.py tests/platform/test_api_conventions.py -v --no-cov`
Expected: all pass (the conventions suite still passes because `create_app`'s new parameter is optional).

- [ ] **Step 6: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean. If basedpyright complains that `request.state.actor` is `Any`, keep the `cached: object` annotation plus the `isinstance` narrowing shown above.

```bash
git add src/xolotl/platform/auth src/xolotl/platform/api/app.py tests/platform/test_auth_primitives.py tests/platform/test_principal.py
git commit -m "feat(auth): password, token and TOTP primitives; actor resolution, tenant-bound sessions and access rules"
```

---

### Task 10: Tenants service: tenant creation, login, TOTP, passwords, staff and API keys

**Files:**
- Create: `src/xolotl/modules/tenants/schemas.py`, `src/xolotl/modules/tenants/service.py`
- Modify: `src/xolotl/app.py` (pass `actor_resolver=resolve_bearer`), `tests/helpers.py` (append `auth_session`, `staff_session`, `new_tenant`)
- Test: `tests/modules/__init__.py` (empty), `tests/modules/tenants/__init__.py` (empty), `tests/modules/tenants/test_service.py`

**Interfaces:**
- Consumes: models (Task 6), `record`/`AuditEvent` (Task 7), auth primitives and `Actor` (Task 9), `tenant_session`, `apply_context`, `bound_context`, `SessionFactory` (Task 4), `clock`, enums, errors.
- Produces (all in `xolotl.modules.tenants.service`):
  - constants `AUTH_ACTOR = "auth"`, `MAX_FAILED_ATTEMPTS = 10`, `LOCKOUT = timedelta(minutes=15)`, `KEY_PREFIX_LEN = 15`, `API_KEY_RE`, `SLUG_RE`, `SCOPE_RE`; `auth_context() -> TenantContext` (platform context with actor `auth`, used by the login routes).
  - `normalize_slug(slug) -> str`, `normalize_email(email) -> str`, `validate_scopes(scopes) -> list[str]` (all raise `ValidationFailedError`).
  - `CreatedTenant(tenant_id, slug, admin_user_id, admin_email, admin_password)`; `create_tenant(session_factory, *, slug, legal_name, country_code, admin_email, env=TenantEnv.SANDBOX, cell="mx-1", actor_id="cli:xolotl") -> CreatedTenant`; `list_tenants(session) -> list[Tenant]` (platform context).
  - `LoginOutcome(mfa_required: bool, token: str, expires_in: int, must_change_password: bool)`; `login(session, *, tenant_slug, email, password) -> LoginOutcome` and `login_totp(session, *, mfa_token, code) -> LoginOutcome` (session bound to `auth_context()`; failure bookkeeping is committed before `UnauthorizedError` is raised).
  - Staff-session functions (session bound to the staff actor): `me(session) -> MeResponse`, `enroll_totp(session) -> TotpEnrollResponse`, `confirm_totp(session, *, code) -> None`, `change_password(session, *, current_password, new_password) -> None`, `create_staff(session, *, email, display_name, roles) -> StaffCreatedResponse`, `disable_staff(session, staff_user_id) -> StaffResponse`, `create_api_key(session, *, name, scopes, env=None) -> ApiKeyCreatedResponse`, `revoke_api_key(session, api_key_id) -> ApiKeyResponse`.
  - `resolve_bearer(session_factory, token) -> Actor` (the `ActorResolver` wired into `build_app`).
  - Schemas in `xolotl.modules.tenants.schemas`: `LoginRequest`, `MfaRequired`, `TokenResponse`, `TotpLoginRequest`, `PasswordChangeRequest`, `TotpEnrollResponse`, `TotpConfirmRequest`, `MeResponse`, `StaffCreateRequest`, `StaffCreatedResponse`, `StaffResponse`, `ApiKeyCreateRequest`, `ApiKeyCreatedResponse`, `ApiKeyResponse`, `TenantResponse` (response models have `from_attributes=True`).
  - Test helpers: `auth_session()` (context manager), `staff_session(tenant_id, user_id, roles)`, `new_tenant(slug="acme", email="admin@acme.mx") -> CreatedTenant`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/helpers.py` (imports to the top):

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from xolotl.modules.tenants import service
from xolotl.platform.db import get_sessionmaker


@contextmanager
def auth_session() -> Iterator[Session]:
    with tenant_session(service.auth_context()) as s:
        yield s


@contextmanager
def staff_session(
    tenant_id: UUID, user_id: UUID, roles: Iterable[Role] = (Role.ADMIN,)
) -> Iterator[Session]:
    with tenant_session(staff_ctx(tenant_id, actor_id=str(user_id), roles=roles)) as s:
        yield s


def new_tenant(slug: str = "acme", email: str = "admin@acme.mx") -> service.CreatedTenant:
    return service.create_tenant(
        get_sessionmaker(),
        slug=slug,
        legal_name=f"{slug.title()} SA de CV",
        country_code="MX",
        admin_email=email,
    )
```

`tests/modules/tenants/test_service.py`:

```python
import re
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select

from tests.helpers import (
    auth_session,
    new_tenant,
    platform_ctx,
    staff_session,
    system_ctx,
)
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import ApiKey, ApiKeyLookup, StaffCredential, StaffUser, Tenant
from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.auth.tokens import decode_token
from xolotl.platform.auth.totp import current_code
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import ActorType, ApiKeyStatus, Role, StaffStatus, TenantEnv, TenantStatus
from xolotl.platform.errors import (
    DomainError,
    ForbiddenError,
    UnauthorizedError,
    ValidationFailedError,
)


def _login(slug: str, email: str, password: str) -> service.LoginOutcome:
    with auth_session() as s:
        return service.login(s, tenant_slug=slug, email=email, password=password)


def _actions(tenant_id) -> list[str]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(AuditEventRow.action).order_by(AuditEventRow.seq)))


def _credential(tenant_id, user_id) -> StaffCredential:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(
            select(StaffCredential).where(StaffCredential.staff_user_id == user_id)
        ).one()


# --- tenants -----------------------------------------------------------------


def test_create_tenant_creates_an_admin_with_a_one_time_password(engine: Engine):
    created = new_tenant()
    assert len(created.admin_password) >= 20
    with tenant_session(platform_ctx()) as s:
        tenant = s.get(Tenant, created.tenant_id)
        assert tenant is not None
        assert (tenant.slug, tenant.env, tenant.status) == ("acme", TenantEnv.SANDBOX, TenantStatus.ACTIVE)
    with tenant_session(system_ctx(created.tenant_id)) as s:
        admin = s.get(StaffUser, (created.tenant_id, created.admin_user_id))
        assert admin is not None
        assert (admin.email, admin.roles, admin.must_change_password) == ("admin@acme.mx", ["ADMIN"], True)
    assert _actions(created.tenant_id) == ["tenant.created", "staff.created"]


def test_create_tenant_normalises_slug_and_email_and_rejects_duplicates(engine: Engine):
    created = service.create_tenant(
        get_sessionmaker(),
        slug=" Acme-Fintech ",
        legal_name="Acme",
        country_code="mx",
        admin_email=" Admin@Acme.MX ",
    )
    assert (created.slug, created.admin_email) == ("acme-fintech", "admin@acme.mx")
    with pytest.raises(DomainError, match="already exists"):
        new_tenant(slug="acme-fintech")
    with pytest.raises(ValidationFailedError):
        new_tenant(slug="a")
    with pytest.raises(ValidationFailedError):
        new_tenant(slug="ok-slug", email="not-an-email")


# --- login -------------------------------------------------------------------


def test_login_success_issues_an_access_token_with_db_roles(engine: Engine):
    created = new_tenant()
    outcome = _login("acme", "admin@acme.mx", created.admin_password)
    assert outcome.mfa_required is False
    assert outcome.must_change_password is True
    assert outcome.expires_in == 480 * 60
    claims = decode_token(outcome.token, purpose="access")
    assert (claims.sub, claims.tenant_slug, claims.roles) == (str(created.admin_user_id), "acme", [Role.ADMIN])
    assert _actions(created.tenant_id)[-1] == "auth.login_succeeded"


def test_login_normalises_slug_and_email(engine: Engine):
    created = new_tenant()
    assert _login(" ACME ", "Admin@ACME.mx ", created.admin_password).token


def test_login_failures_are_generic_and_persist_bookkeeping(engine: Engine):
    created = new_tenant()
    for slug, email, password in (
        ("nope", "admin@acme.mx", created.admin_password),
        ("acme", "ghost@acme.mx", created.admin_password),
        ("acme", "admin@acme.mx", "wrong-password"),
    ):
        with pytest.raises(UnauthorizedError, match="invalid credentials"):
            _login(slug, email, password)
    assert _credential(created.tenant_id, created.admin_user_id).failed_attempts == 1
    # Only the attempt that matched a user is attributable; unknown slugs and
    # unknown emails leave no audit row because there is nobody to attribute to.
    assert _actions(created.tenant_id).count("auth.login_failed") == 1


def test_ten_failures_lock_the_account_for_fifteen_minutes(engine: Engine, monkeypatch):
    created = new_tenant()
    for _ in range(service.MAX_FAILED_ATTEMPTS):
        with pytest.raises(UnauthorizedError):
            _login("acme", "admin@acme.mx", "wrong")
    cred = _credential(created.tenant_id, created.admin_user_id)
    assert cred.locked_until is not None
    assert cred.failed_attempts == 0
    assert "auth.locked" in _actions(created.tenant_id)
    with pytest.raises(UnauthorizedError):  # right password, still locked
        _login("acme", "admin@acme.mx", created.admin_password)
    real_now = clock.now()
    monkeypatch.setattr(clock, "now", lambda: real_now + service.LOCKOUT + timedelta(seconds=1))
    assert _login("acme", "admin@acme.mx", created.admin_password).token


def test_login_on_suspended_tenant_is_forbidden(engine: Engine):
    created = new_tenant()
    with tenant_session(platform_ctx()) as s:
        tenant = s.get(Tenant, created.tenant_id)
        assert tenant is not None
        tenant.status = TenantStatus.SUSPENDED
    with pytest.raises(ForbiddenError) as exc:
        _login("acme", "admin@acme.mx", created.admin_password)
    assert exc.value.code == "TENANT_SUSPENDED"


# --- totp --------------------------------------------------------------------


def test_totp_enrolment_then_two_step_login(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        enrolled = service.enroll_totp(s)
        assert "otpauth://totp/" in enrolled.otpauth_uri
        with pytest.raises(ValidationFailedError):
            service.confirm_totp(s, code="000000")
        service.confirm_totp(s, code=current_code(enrolled.secret))
        assert service.me(s).totp_enabled is True
    first = _login("acme", "admin@acme.mx", created.admin_password)
    assert first.mfa_required is True
    assert decode_token(first.token, purpose="mfa").purpose == "mfa"
    with auth_session() as s, pytest.raises(UnauthorizedError):
        service.login_totp(s, mfa_token=first.token, code="000000")
    assert _credential(t, u).failed_attempts == 1
    with auth_session() as s:
        final = service.login_totp(s, mfa_token=first.token, code=current_code(enrolled.secret))
    assert final.mfa_required is False
    assert decode_token(final.token, purpose="access").sub == str(u)
    assert _credential(t, u).failed_attempts == 0
    with auth_session() as s, pytest.raises(UnauthorizedError):  # access token is not an mfa token
        service.login_totp(s, mfa_token=final.token, code=current_code(enrolled.secret))
    assert "auth.totp_enrolled" in _actions(t)


# --- password change ---------------------------------------------------------


def test_change_password_rules(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        with pytest.raises(ValidationFailedError, match="current password"):
            service.change_password(s, current_password="nope", new_password="a-long-new-password")
        with pytest.raises(ValidationFailedError, match="12 characters"):
            service.change_password(s, current_password=created.admin_password, new_password="short")
        service.change_password(s, current_password=created.admin_password, new_password="a-long-new-password")
        assert service.me(s).must_change_password is False
    assert _login("acme", "admin@acme.mx", "a-long-new-password").must_change_password is False
    with pytest.raises(UnauthorizedError):
        _login("acme", "admin@acme.mx", created.admin_password)
    assert "auth.password_changed" in _actions(t)


# --- staff -------------------------------------------------------------------


def test_create_and_disable_staff(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        analyst = service.create_staff(s, email="Ana@Acme.MX", display_name="Ana", roles=[Role.ANALYST, Role.MLRO])
        assert analyst.email == "ana@acme.mx"
        assert analyst.roles == [Role.ANALYST, Role.MLRO]
        assert len(analyst.initial_password) >= 20
        with pytest.raises(DomainError, match="already exists"):
            service.create_staff(s, email="ANA@acme.mx", display_name="Dup", roles=[Role.ANALYST])
        with pytest.raises(DomainError, match="own account"):
            service.disable_staff(s, u)
        disabled = service.disable_staff(s, analyst.id)
        assert disabled.status is StaffStatus.DISABLED
        assert service.disable_staff(s, analyst.id).status is StaffStatus.DISABLED  # idempotent
    assert _actions(t)[-2:] == ["staff.created", "staff.disabled"]
    with pytest.raises(UnauthorizedError):
        _login("acme", "ana@acme.mx", analyst.initial_password)


def test_resolve_bearer_rechecks_user_and_tenant_status(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        analyst = service.create_staff(s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST])
    token = _login("acme", "ana@acme.mx", analyst.initial_password).token
    actor = service.resolve_bearer(get_sessionmaker(), token)
    assert actor.context.actor_type is ActorType.STAFF
    assert actor.context.roles == frozenset({Role.ANALYST})
    assert actor.must_change_password is True
    with staff_session(t, u) as s:
        _ = service.disable_staff(s, analyst.id)
    with pytest.raises(UnauthorizedError):  # still-valid JWT, disabled user
        service.resolve_bearer(get_sessionmaker(), token)
    admin_token = _login("acme", "admin@acme.mx", created.admin_password).token
    with tenant_session(platform_ctx()) as s:
        tenant = s.get(Tenant, t)
        assert tenant is not None
        tenant.status = TenantStatus.SUSPENDED
    with pytest.raises(ForbiddenError) as exc:
        service.resolve_bearer(get_sessionmaker(), admin_token)
    assert exc.value.code == "TENANT_SUSPENDED"
    with pytest.raises(UnauthorizedError):
        service.resolve_bearer(get_sessionmaker(), "not-a-token")


# --- api keys ----------------------------------------------------------------


def test_api_key_lifecycle(engine: Engine, monkeypatch):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        issued = service.create_api_key(s, name="ingest", scopes=["tenants:write", "tenants:read"])
        with pytest.raises(DomainError, match="env"):
            service.create_api_key(s, name="x", scopes=["tenants:read"], env=TenantEnv.PROD)
        with pytest.raises(ValidationFailedError):
            service.create_api_key(s, name="x", scopes=["Tenants:Read"])
    assert re.fullmatch(r"xk_sbx_[0-9a-f]{8}_[0-9a-f]{32}", issued.key)
    assert len(issued.key) == 48
    assert issued.key_prefix == issued.key[:15]
    assert issued.scopes == ["tenants:read", "tenants:write"]
    with tenant_session(platform_ctx()) as s:
        lookup = s.get(ApiKeyLookup, issued.key_prefix)
        assert lookup is not None and lookup.tenant_id == t

    actor = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert actor.context.actor_type is ActorType.API_CLIENT
    assert actor.context.tenant_id == t
    assert actor.context.actor_id == str(issued.id)
    assert actor.scopes == frozenset({"tenants:read", "tenants:write"})

    def last_used():
        with tenant_session(system_ctx(t)) as s:
            row = s.get(ApiKey, (t, issued.id))
            assert row is not None
            return row.last_used_at

    first_seen = last_used()
    assert first_seen is not None
    _ = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert last_used() == first_seen  # not bumped within a minute
    monkeypatch.setattr(clock, "now", lambda: first_seen + timedelta(minutes=2))
    _ = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert last_used() == first_seen + timedelta(minutes=2)

    forged = issued.key[:-4] + ("0000" if not issued.key.endswith("0000") else "1111")
    with pytest.raises(UnauthorizedError):
        service.resolve_bearer(get_sessionmaker(), forged)
    with pytest.raises(UnauthorizedError):
        service.resolve_bearer(get_sessionmaker(), "xk_sbx_short")

    with staff_session(t, u) as s:
        revoked = service.revoke_api_key(s, issued.id)
        assert revoked.status is ApiKeyStatus.REVOKED
        assert revoked.revoked_at is not None
    before = last_used()
    with pytest.raises(UnauthorizedError):
        service.resolve_bearer(get_sessionmaker(), issued.key)
    assert last_used() == before
    assert _actions(t)[-2:] == ["api_key.created", "api_key.revoked"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/modules/tenants/test_service.py -v --no-cov`
Expected: FAIL at collection with `ImportError` (`xolotl.modules.tenants.service` does not exist).

- [ ] **Step 3: Write the schemas**

`src/xolotl/modules/tenants/schemas.py`:

```python
"""Request and response bodies for the tenants module."""

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from xolotl.platform.enums import ApiKeyStatus, Role, StaffStatus, TenantEnv, TenantStatus


class LoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


class MfaRequired(BaseModel):
    mfa_required: Literal[True] = True
    mfa_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    must_change_password: bool


class TotpLoginRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)


class TotpEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_slug: str
    email: str
    display_name: str
    roles: list[Role]
    must_change_password: bool
    totp_enabled: bool


class StaffCreateRequest(BaseModel):
    email: str
    display_name: str = Field(min_length=1, max_length=200)
    roles: list[Role] = Field(min_length=1)


class StaffCreatedResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    roles: list[Role]
    initial_password: str


class StaffResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    roles: list[Role]
    status: StaffStatus
    must_change_password: bool
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1)
    env: TenantEnv | None = None


class ApiKeyCreatedResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    env: TenantEnv
    key: str  # shown once


class ApiKeyResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    env: TenantEnv
    status: ApiKeyStatus
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TenantResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    legal_name: str
    country_code: str
    cell: str
    env: TenantEnv
    status: TenantStatus
    created_at: datetime
```

- [ ] **Step 4: Write the service**

`src/xolotl/modules/tenants/service.py`:

```python
"""Tenant lifecycle, staff authentication, staff management and API keys (spec §5–§6).

Two session shapes are used:

* ``auth`` sessions (``auth_context()``): a platform-operator session with no
  tenant, used by login before anyone is authenticated. ``login`` resolves the
  tenant and switches the session into that tenant's scope with
  ``apply_context``. Failure bookkeeping (counters, audit) is committed before
  the error is raised so a rollback cannot erase it.
* staff sessions: bound by ``get_session`` to the authenticated staff actor;
  ``actor_id`` is the ``staff_user.id``.
"""

import hashlib
import hmac
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.tenants.models import (
    ApiKey,
    ApiKeyLookup,
    StaffCredential,
    StaffUser,
    Tenant,
)
from xolotl.modules.tenants.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    MeResponse,
    StaffCreatedResponse,
    StaffResponse,
    TotpEnrollResponse,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.passwords import (
    check_password_strength,
    generate_password,
    hash_password,
    verify_password,
)
from xolotl.platform.auth.tokens import decode_token, issue_token
from xolotl.platform.auth.totp import new_secret, otpauth_uri, verify_code
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, apply_context, bound_context, tenant_session
from xolotl.platform.enums import (
    ActorType,
    ApiKeyStatus,
    Role,
    StaffStatus,
    TenantEnv,
    TenantStatus,
)
from xolotl.platform.errors import (
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from xolotl.platform.tenancy.context import TenantContext

AUTH_ACTOR = "auth"
MAX_FAILED_ATTEMPTS = 10
LOCKOUT = timedelta(minutes=15)
LAST_USED_GRANULARITY = timedelta(minutes=1)
KEY_PREFIX_LEN = 15
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCOPE_RE = re.compile(r"^[a-z][a-z_]*:(read|write)$")
API_KEY_RE = re.compile(r"^xk_(sbx|prd)_[0-9a-f]{8}_[0-9a-f]{32}$")
_ENV_TAG = {TenantEnv.SANDBOX: "sbx", TenantEnv.PROD: "prd"}
_INVALID_CREDENTIALS = "invalid credentials"


def auth_context() -> TenantContext:
    return TenantContext.platform(AUTH_ACTOR)


# --- validation ----------------------------------------------------------------


def normalize_slug(slug: str) -> str:
    value = slug.strip().lower()
    if not SLUG_RE.match(value):
        msg = "slug must match ^[a-z0-9][a-z0-9-]{2,39}$"
        raise ValidationFailedError(msg)
    return value


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not EMAIL_RE.match(value):
        msg = "invalid email address"
        raise ValidationFailedError(msg)
    return value


def validate_scopes(scopes: Sequence[str]) -> list[str]:
    cleaned = sorted({scope.strip() for scope in scopes})
    for scope in cleaned:
        if not SCOPE_RE.match(scope):
            msg = f"scope {scope!r} must look like <area>:<read|write>"
            raise ValidationFailedError(msg)
    return cleaned


def _roles(user: StaffUser) -> list[Role]:
    return sorted(Role(value) for value in user.roles)


def _ensure_tenant_active(tenant: Tenant) -> None:
    if tenant.status is not TenantStatus.ACTIVE:
        msg = "tenant is not active"
        raise ForbiddenError(msg, code="TENANT_SUSPENDED")


# --- tenants -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreatedTenant:
    tenant_id: UUID
    slug: str
    admin_user_id: UUID
    admin_email: str
    admin_password: str


def _add_staff(
    s: Session,
    tenant_id: UUID,
    *,
    email: str,
    display_name: str,
    roles: Sequence[Role],
    password: str,
) -> StaffUser:
    if s.scalar(select(StaffUser.id).where(StaffUser.email == email)) is not None:
        msg = f"staff user {email} already exists"
        raise DomainError(msg)
    user = StaffUser(
        tenant_id=tenant_id,
        email=email,
        display_name=display_name.strip(),
        roles=sorted({role.value for role in roles}),
        status=StaffStatus.ACTIVE,
        must_change_password=True,
    )
    s.add(user)
    s.flush()
    s.add(
        StaffCredential(
            tenant_id=tenant_id, staff_user_id=user.id, password_hash=hash_password(password)
        )
    )
    s.flush()
    return user


def create_tenant(
    session_factory: SessionFactory,
    *,
    slug: str,
    legal_name: str,
    country_code: str,
    admin_email: str,
    env: TenantEnv = TenantEnv.SANDBOX,
    cell: str = "mx-1",
    actor_id: str = "cli:xolotl",
) -> CreatedTenant:
    slug = normalize_slug(slug)
    email = normalize_email(admin_email)
    country = country_code.strip().upper()
    if len(country) != 2 or not country.isalpha():  # noqa: PLR2004
        msg = "country_code must be an ISO 3166-1 alpha-2 code"
        raise ValidationFailedError(msg)
    if not legal_name.strip():
        msg = "legal_name is required"
        raise ValidationFailedError(msg)
    with tenant_session(TenantContext.platform(actor_id), factory=session_factory) as s:
        if s.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
            msg = f"tenant slug {slug!r} already exists"
            raise DomainError(msg)
        tenant = Tenant(
            slug=slug,
            legal_name=legal_name.strip(),
            country_code=country,
            cell=cell,
            env=env,
            status=TenantStatus.ACTIVE,
        )
        s.add(tenant)
        s.flush()
        # Same transaction, now scoped to the new tenant (still as the operator).
        apply_context(s, TenantContext.platform(actor_id, tenant_id=tenant.id))
        password = generate_password()
        admin = _add_staff(
            s,
            tenant.id,
            email=email,
            display_name="Administrator",
            roles=[Role.ADMIN],
            password=password,
        )
        _ = record(
            s,
            AuditEvent(
                action="tenant.created",
                entity_type="tenant",
                entity_id=str(tenant.id),
                detail={"slug": slug, "env": env.value},
            ),
        )
        _ = record(
            s,
            AuditEvent(
                action="staff.created",
                entity_type="staff_user",
                entity_id=str(admin.id),
                detail={"roles": ["ADMIN"]},
            ),
        )
        return CreatedTenant(tenant.id, slug, admin.id, email, password)


def list_tenants(s: Session) -> list[Tenant]:
    return list(s.scalars(select(Tenant).order_by(Tenant.created_at, Tenant.id)))


# --- login ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    mfa_required: bool
    token: str
    expires_in: int
    must_change_password: bool


def _load_staff(s: Session, email: str) -> tuple[StaffUser | None, StaffCredential | None]:
    user = s.scalar(select(StaffUser).where(StaffUser.email == email))
    if user is None:
        return None, None
    cred = s.scalar(select(StaffCredential).where(StaffCredential.staff_user_id == user.id))
    return user, cred


def _fail_and_commit(s: Session, user: StaffUser | None, reason: str) -> UnauthorizedError:
    if user is not None:
        _ = record(
            s,
            AuditEvent(
                action="auth.login_failed",
                entity_type="staff_user",
                entity_id=str(user.id),
                detail={"reason": reason},
            ),
        )
    s.commit()
    return UnauthorizedError(_INVALID_CREDENTIALS)


def _register_failure(
    s: Session, user: StaffUser, cred: StaffCredential, now: datetime, *, stage: str
) -> UnauthorizedError:
    cred.failed_attempts += 1
    _ = record(
        s,
        AuditEvent(
            action="auth.login_failed",
            entity_type="staff_user",
            entity_id=str(user.id),
            detail={"reason": "bad_" + stage, "failed_attempts": cred.failed_attempts},
        ),
    )
    if cred.failed_attempts >= MAX_FAILED_ATTEMPTS:
        cred.locked_until = now + LOCKOUT
        cred.failed_attempts = 0
        _ = record(
            s,
            AuditEvent(
                action="auth.locked",
                entity_type="staff_user",
                entity_id=str(user.id),
                detail={"locked_until": cred.locked_until.isoformat()},
            ),
        )
    s.commit()
    return UnauthorizedError(_INVALID_CREDENTIALS)


def _is_locked(cred: StaffCredential, now: datetime) -> bool:
    return cred.locked_until is not None and cred.locked_until > now


def _succeed(s: Session, tenant: Tenant, user: StaffUser, cred: StaffCredential) -> LoginOutcome:
    cred.failed_attempts = 0
    cred.locked_until = None
    _ = record(
        s,
        AuditEvent(
            action="auth.login_succeeded", entity_type="staff_user", entity_id=str(user.id)
        ),
    )
    ttl = timedelta(minutes=get_settings().access_token_ttl_minutes)
    token = issue_token(
        subject=str(user.id),
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        roles=_roles(user),
        purpose="access",
        ttl=ttl,
    )
    return LoginOutcome(
        mfa_required=False,
        token=token,
        expires_in=int(ttl.total_seconds()),
        must_change_password=user.must_change_password,
    )


def login(s: Session, *, tenant_slug: str, email: str, password: str) -> LoginOutcome:
    """Password step. ``s`` is bound to ``auth_context()``."""
    tenant = s.scalar(select(Tenant).where(Tenant.slug == tenant_slug.strip().lower()))
    if tenant is None:
        raise UnauthorizedError(_INVALID_CREDENTIALS)
    apply_context(s, TenantContext.system(tenant.id, AUTH_ACTOR))
    user, cred = _load_staff(s, email.strip().lower())
    if user is None or cred is None or user.status is not StaffStatus.ACTIVE:
        raise _fail_and_commit(s, user, "unknown_or_disabled")
    now = clock.now()
    if _is_locked(cred, now):
        raise _fail_and_commit(s, user, "locked")
    if not verify_password(cred.password_hash, password):
        raise _register_failure(s, user, cred, now, stage="password")
    _ensure_tenant_active(tenant)
    if cred.totp_enabled:
        ttl = timedelta(minutes=get_settings().mfa_token_ttl_minutes)
        token = issue_token(
            subject=str(user.id),
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            roles=_roles(user),
            purpose="mfa",
            ttl=ttl,
        )
        return LoginOutcome(
            mfa_required=True,
            token=token,
            expires_in=int(ttl.total_seconds()),
            must_change_password=user.must_change_password,
        )
    return _succeed(s, tenant, user, cred)


def login_totp(s: Session, *, mfa_token: str, code: str) -> LoginOutcome:
    """TOTP step. ``s`` is bound to ``auth_context()``."""
    claims = decode_token(mfa_token, purpose="mfa")
    apply_context(s, TenantContext.system(claims.tenant_id, AUTH_ACTOR))
    tenant = s.get(Tenant, claims.tenant_id)
    user = s.get(StaffUser, (claims.tenant_id, UUID(claims.sub)))
    cred = (
        None
        if user is None
        else s.scalar(select(StaffCredential).where(StaffCredential.staff_user_id == user.id))
    )
    if (
        tenant is None
        or user is None
        or cred is None
        or user.status is not StaffStatus.ACTIVE
        or not cred.totp_enabled
        or cred.totp_secret is None
    ):
        raise _fail_and_commit(s, user, "mfa_not_available")
    now = clock.now()
    if _is_locked(cred, now):
        raise _fail_and_commit(s, user, "locked")
    if not verify_code(cred.totp_secret.decode(), code):
        raise _register_failure(s, user, cred, now, stage="totp")
    _ensure_tenant_active(tenant)
    return _succeed(s, tenant, user, cred)


# --- staff-session operations --------------------------------------------------


def _current_rows(s: Session) -> tuple[Tenant, StaffUser, StaffCredential]:
    ctx = bound_context(s)
    tenant_id = ctx.require_tenant_id()
    tenant = s.get(Tenant, tenant_id)
    user = s.get(StaffUser, (tenant_id, UUID(ctx.actor_id)))
    cred = (
        None
        if user is None
        else s.scalar(select(StaffCredential).where(StaffCredential.staff_user_id == user.id))
    )
    if tenant is None or user is None or cred is None:
        raise UnauthorizedError
    return tenant, user, cred


def me(s: Session) -> MeResponse:
    tenant, user, cred = _current_rows(s)
    return MeResponse(
        id=user.id,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        email=user.email,
        display_name=user.display_name,
        roles=_roles(user),
        must_change_password=user.must_change_password,
        totp_enabled=cred.totp_enabled,
    )


def enroll_totp(s: Session) -> TotpEnrollResponse:
    tenant, user, cred = _current_rows(s)
    if cred.totp_enabled:
        msg = "TOTP is already enabled"
        raise DomainError(msg)
    secret = new_secret()
    cred.totp_secret = secret.encode()
    s.flush()
    return TotpEnrollResponse(
        secret=secret, otpauth_uri=otpauth_uri(secret, email=user.email, tenant_slug=tenant.slug)
    )


def confirm_totp(s: Session, *, code: str) -> None:
    _, user, cred = _current_rows(s)
    if cred.totp_secret is None:
        msg = "enrol TOTP before confirming it"
        raise DomainError(msg)
    if cred.totp_enabled:
        msg = "TOTP is already enabled"
        raise DomainError(msg)
    if not verify_code(cred.totp_secret.decode(), code):
        msg = "invalid TOTP code"
        raise ValidationFailedError(msg)
    cred.totp_enabled = True
    _ = record(
        s,
        AuditEvent(action="auth.totp_enrolled", entity_type="staff_user", entity_id=str(user.id)),
    )
    s.flush()


def change_password(s: Session, *, current_password: str, new_password: str) -> None:
    _, user, cred = _current_rows(s)
    if not verify_password(cred.password_hash, current_password):
        msg = "current password is incorrect"
        raise ValidationFailedError(msg)
    check_password_strength(new_password)
    if new_password == current_password:
        msg = "new password must differ from the current password"
        raise ValidationFailedError(msg)
    cred.password_hash = hash_password(new_password)
    cred.password_changed_at = clock.now()
    user.must_change_password = False
    _ = record(
        s,
        AuditEvent(
            action="auth.password_changed", entity_type="staff_user", entity_id=str(user.id)
        ),
    )
    s.flush()


def create_staff(
    s: Session, *, email: str, display_name: str, roles: Sequence[Role]
) -> StaffCreatedResponse:
    tenant_id = bound_context(s).require_tenant_id()
    password = generate_password()
    user = _add_staff(
        s,
        tenant_id,
        email=normalize_email(email),
        display_name=display_name,
        roles=roles,
        password=password,
    )
    _ = record(
        s,
        AuditEvent(
            action="staff.created",
            entity_type="staff_user",
            entity_id=str(user.id),
            detail={"roles": list(user.roles)},
        ),
    )
    return StaffCreatedResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=_roles(user),
        initial_password=password,
    )


def disable_staff(s: Session, staff_user_id: UUID) -> StaffResponse:
    ctx = bound_context(s)
    user = s.get(StaffUser, (ctx.require_tenant_id(), staff_user_id))
    if user is None:
        raise NotFoundError
    if str(user.id) == ctx.actor_id:
        msg = "you cannot disable your own account"
        raise DomainError(msg)
    if user.status is not StaffStatus.DISABLED:
        user.status = StaffStatus.DISABLED
        _ = record(
            s,
            AuditEvent(action="staff.disabled", entity_type="staff_user", entity_id=str(user.id)),
        )
        s.flush()
    return StaffResponse.model_validate(user)


# --- api keys ------------------------------------------------------------------


def generate_api_key(env: TenantEnv) -> tuple[str, str, bytes]:
    """Return ``(key, key_prefix, sha256(key))`` for a new key in ``env``."""
    key = f"xk_{_ENV_TAG[env]}_{secrets.token_hex(4)}_{secrets.token_hex(16)}"
    return key, key[:KEY_PREFIX_LEN], hashlib.sha256(key.encode()).digest()


def create_api_key(
    s: Session, *, name: str, scopes: Sequence[str], env: TenantEnv | None = None
) -> ApiKeyCreatedResponse:
    ctx = bound_context(s)
    tenant = s.get(Tenant, ctx.require_tenant_id())
    if tenant is None:
        raise NotFoundError
    key_env = env or tenant.env
    if key_env is not tenant.env:
        msg = f"api key env must match the tenant env {tenant.env.value}"
        raise DomainError(msg)
    cleaned = validate_scopes(scopes)
    key, prefix, digest = generate_api_key(key_env)
    row = ApiKey(
        tenant_id=tenant.id,
        name=name.strip(),
        key_prefix=prefix,
        key_hash=digest,
        scopes=cleaned,
        env=key_env,
        status=ApiKeyStatus.ACTIVE,
        created_by=UUID(ctx.actor_id),
    )
    s.add(row)
    s.add(ApiKeyLookup(key_prefix=prefix, tenant_id=tenant.id))
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="api_key.created",
            entity_type="api_key",
            entity_id=str(row.id),
            detail={"name": row.name, "scopes": cleaned, "env": key_env.value},
        ),
    )
    return ApiKeyCreatedResponse(
        id=row.id, name=row.name, key_prefix=prefix, scopes=cleaned, env=key_env, key=key
    )


def revoke_api_key(s: Session, api_key_id: UUID) -> ApiKeyResponse:
    row = s.get(ApiKey, (bound_context(s).require_tenant_id(), api_key_id))
    if row is None:
        raise NotFoundError
    if row.status is ApiKeyStatus.ACTIVE:
        row.status = ApiKeyStatus.REVOKED
        row.revoked_at = clock.now()
        _ = record(
            s,
            AuditEvent(action="api_key.revoked", entity_type="api_key", entity_id=str(row.id)),
        )
        s.flush()
    return ApiKeyResponse.model_validate(row)


# --- bearer resolution ---------------------------------------------------------


def _resolve_staff_token(session_factory: SessionFactory, token: str) -> Actor:
    claims = decode_token(token, purpose="access")
    probe = TenantContext(
        tenant_id=claims.tenant_id,
        actor_type=ActorType.STAFF,
        actor_id=claims.sub,
        roles=frozenset(claims.roles),
    )
    with tenant_session(probe, factory=session_factory) as s:
        tenant = s.get(Tenant, claims.tenant_id)
        user = s.get(StaffUser, (claims.tenant_id, UUID(claims.sub)))
        if tenant is None or user is None or user.status is not StaffStatus.ACTIVE:
            raise UnauthorizedError
        _ensure_tenant_active(tenant)
        roles = frozenset(_roles(user))  # the row is authoritative, not the token
        must_change = user.must_change_password
    return Actor(
        context=TenantContext(
            tenant_id=claims.tenant_id,
            actor_type=ActorType.STAFF,
            actor_id=claims.sub,
            roles=roles,
        ),
        must_change_password=must_change,
    )


def _resolve_api_key(session_factory: SessionFactory, token: str) -> Actor:
    if not API_KEY_RE.match(token):
        raise UnauthorizedError
    prefix = token[:KEY_PREFIX_LEN]
    digest = hashlib.sha256(token.encode()).digest()
    with tenant_session(auth_context(), factory=session_factory) as s:
        lookup = s.get(ApiKeyLookup, prefix)  # shared table, readable before any tenant scope
        if lookup is None:
            raise UnauthorizedError
        tenant_id = lookup.tenant_id
        apply_context(
            s,
            TenantContext(
                tenant_id=tenant_id, actor_type=ActorType.API_CLIENT, actor_id=f"key:{prefix}"
            ),
        )
        row = s.scalar(select(ApiKey).where(ApiKey.key_prefix == prefix))
        if (
            row is None
            or not hmac.compare_digest(row.key_hash, digest)
            or row.status is not ApiKeyStatus.ACTIVE
        ):
            raise UnauthorizedError
        tenant = s.get(Tenant, tenant_id)
        if tenant is None:
            raise UnauthorizedError
        _ensure_tenant_active(tenant)
        now = clock.now()
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_GRANULARITY:
            row.last_used_at = now
        return Actor(
            context=TenantContext(
                tenant_id=tenant_id, actor_type=ActorType.API_CLIENT, actor_id=str(row.id)
            ),
            scopes=frozenset(row.scopes),
        )


def resolve_bearer(session_factory: SessionFactory, token: str) -> Actor:
    """The ``ActorResolver`` for the kernel: staff JWTs and ``xk_`` API keys."""
    if token.startswith("xk_"):
        return _resolve_api_key(session_factory, token)
    return _resolve_staff_token(session_factory, token)
```

Modify `src/xolotl/app.py` so the resolver is wired:

```python
from fastapi import FastAPI

from xolotl.modules.tenants.service import resolve_bearer
from xolotl.platform.api.app import create_app
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.logging import configure_logging


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    configure_logging(get_settings().log_level)
    return create_app(
        session_factory=session_factory or get_sessionmaker(),
        actor_resolver=resolve_bearer,
        routers=[],
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/modules/tenants/test_service.py -v --no-cov`
Expected: 12 passed. If `test_login_failures_are_generic_and_persist_bookkeeping` shows `failed_attempts == 0`, the failure path raised before committing: every failure must go through `_fail_and_commit` or `_register_failure`, which commit first.

- [ ] **Step 6: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean. ruff may flag the `len(country) != 2` literal (`PLR2004`); the inline `noqa` above covers it.

```bash
git add src/xolotl/modules/tenants src/xolotl/app.py tests/helpers.py tests/modules
git commit -m "feat(tenants): tenant creation, staff login with lockout and TOTP, password change, staff and API key management, bearer resolution"
```

---

### Task 11: Idempotency-Key layer, auth routes and the hello endpoints

**Files:**
- Create: `src/xolotl/platform/api/idempotency.py`, `src/xolotl/platform/api/router.py`, `src/xolotl/modules/tenants/routes_auth.py`, `src/xolotl/modules/tenants/routes_tenants.py`
- Modify: `src/xolotl/modules/tenants/schemas.py` (append `PingRequest`, `PingResponse`), `src/xolotl/app.py` (mount the two routers), `tests/conftest.py` (append `tenant`, `admin_headers`), `tests/helpers.py` (append `login_token`), `tests/platform/test_api_conventions.py` (append the idempotency tests)
- Test: `tests/modules/tenants/test_auth.py`

**Interfaces:**
- Consumes: `resolve_request_actor`, `SessionDep`, `StaffDep`, `require_access`, `Actor` (Task 9), `app_state` (Task 8), `IdempotencyKey` (Task 6), `tenant_session`, `apply_context` (Task 4), service functions and schemas (Task 10), errors (Task 2).
- Produces: `idempotency.IdempotentRoute` (APIRoute subclass), `idempotency.RESERVED_STATUS = 0`, `idempotency.TTL = timedelta(hours=24)`, `idempotency.request_hash(method, path, body) -> bytes`, `idempotency.is_idempotent_request(request) -> bool`; `router.v1_router(**kwargs) -> APIRouter` (every module router is created with it); `routes_auth.router` (`POST /v1/auth/login`, `POST /v1/auth/login/totp`, `POST /v1/auth/password` 204, `POST /v1/auth/totp/enroll`, `POST /v1/auth/totp/confirm` 204, `GET /v1/auth/me`), `routes_auth.AuthSessionDep`; `routes_tenants.router` (`GET /v1/tenants/me` scope `tenants:read`, `POST /v1/ping` scope `tenants:write`, write); schemas `PingRequest(message: str = "ping")`, `PingResponse(pong: str, actor_id: str, at: datetime)`; fixtures `tenant` (a `CreatedTenant`) and `admin_headers` (bearer headers for the tenant admin after the forced password change; the new password is `tests.conftest.ADMIN_PASSWORD`); helper `login_token(client, slug, email, password) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/helpers.py`:

```python
from fastapi.testclient import TestClient


def login_token(client: TestClient, slug: str, email: str, password: str) -> str:
    resp = client.post(
        "/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]
```

Append to `tests/conftest.py`:

```python
from tests.helpers import login_token, new_tenant
from xolotl.modules.tenants.service import CreatedTenant

ADMIN_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def tenant(engine: Engine) -> CreatedTenant:
    return new_tenant()


@pytest.fixture
def admin_headers(client: TestClient, tenant: CreatedTenant) -> dict[str, str]:
    """Bearer headers for the tenant admin once the forced password change is done."""
    token = login_token(client, tenant.slug, tenant.admin_email, tenant.admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    changed = client.post(
        "/v1/auth/password",
        json={"current_password": tenant.admin_password, "new_password": ADMIN_PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 204, changed.text
    return headers
```

`tests/modules/tenants/test_auth.py`:

```python
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD
from tests.helpers import login_token, platform_ctx, staff_session
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import Tenant
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.auth.tokens import issue_token
from xolotl.platform.auth.totp import current_code
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role, TenantStatus


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_a_bearer_token(client: TestClient, tenant: CreatedTenant):
    resp = client.post(
        "/v1/auth/login",
        json={"tenant_slug": "acme", "email": tenant.admin_email, "password": tenant.admin_password},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 28_800
    assert body["must_change_password"] is True
    me = client.get("/v1/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["tenant_slug"] == "acme"
    assert me.json()["roles"] == ["ADMIN"]
    assert me.json()["totp_enabled"] is False


def test_login_failures_are_401_problems_without_detail_leaks(client: TestClient, tenant: CreatedTenant):
    for payload in (
        {"tenant_slug": "acme", "email": tenant.admin_email, "password": "wrong"},
        {"tenant_slug": "acme", "email": "ghost@acme.mx", "password": "wrong"},
        {"tenant_slug": "nope", "email": tenant.admin_email, "password": tenant.admin_password},
    ):
        resp = client.post("/v1/auth/login", json=payload)
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHENTICATED"
        assert resp.json()["detail"] == "invalid credentials"
    assert client.post("/v1/auth/login", json={"email": "x"}).status_code == 422


def test_me_requires_a_valid_unexpired_untampered_token(client: TestClient, tenant: CreatedTenant):
    assert client.get("/v1/auth/me").status_code == 401
    token = login_token(client, "acme", tenant.admin_email, tenant.admin_password)
    assert client.get("/v1/auth/me", headers=_bearer(token[:-3] + "xyz")).status_code == 401
    expired = issue_token(
        subject=str(tenant.admin_user_id),
        tenant_id=tenant.tenant_id,
        tenant_slug="acme",
        roles=[Role.ADMIN],
        purpose="access",
        ttl=timedelta(seconds=-1),
    )
    assert client.get("/v1/auth/me", headers=_bearer(expired)).status_code == 401


def test_forced_password_change_gates_everything_but_the_auth_routes(
    client: TestClient, tenant: CreatedTenant
):
    token = login_token(client, "acme", tenant.admin_email, tenant.admin_password)
    blocked = client.get("/v1/tenants/me", headers=_bearer(token))
    assert (blocked.status_code, blocked.json()["code"]) == (403, "PASSWORD_CHANGE_REQUIRED")
    weak = client.post(
        "/v1/auth/password",
        json={"current_password": tenant.admin_password, "new_password": "short"},
        headers=_bearer(token),
    )
    assert weak.status_code == 422
    changed = client.post(
        "/v1/auth/password",
        json={"current_password": tenant.admin_password, "new_password": ADMIN_PASSWORD},
        headers=_bearer(token),
    )
    assert changed.status_code == 204
    assert client.get("/v1/tenants/me", headers=_bearer(token)).status_code == 200
    assert client.get("/v1/auth/me", headers=_bearer(token)).json()["must_change_password"] is False
    assert login_token(client, "acme", tenant.admin_email, ADMIN_PASSWORD)


def test_totp_enrolment_and_two_step_login_over_http(client: TestClient, admin_headers):
    enrolled = client.post("/v1/auth/totp/enroll", headers=admin_headers)
    assert enrolled.status_code == 200
    secret = enrolled.json()["secret"]
    assert enrolled.json()["otpauth_uri"].startswith("otpauth://totp/")
    bad = client.post("/v1/auth/totp/confirm", json={"code": "000000"}, headers=admin_headers)
    assert bad.status_code == 422
    ok = client.post(
        "/v1/auth/totp/confirm", json={"code": current_code(secret)}, headers=admin_headers
    )
    assert ok.status_code == 204
    step1 = client.post(
        "/v1/auth/login",
        json={"tenant_slug": "acme", "email": "admin@acme.mx", "password": ADMIN_PASSWORD},
    )
    assert step1.json()["mfa_required"] is True
    mfa_token = step1.json()["mfa_token"]
    wrong = client.post("/v1/auth/login/totp", json={"mfa_token": mfa_token, "code": "000000"})
    assert wrong.status_code == 401
    step2 = client.post(
        "/v1/auth/login/totp", json={"mfa_token": mfa_token, "code": current_code(secret)}
    )
    assert step2.status_code == 200
    assert client.get("/v1/auth/me", headers=_bearer(step2.json()["access_token"])).status_code == 200
    # An mfa token is not an access token.
    assert client.get("/v1/auth/me", headers=_bearer(mfa_token)).status_code == 401


def test_disabled_user_and_suspended_tenant_are_rejected_with_live_tokens(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        analyst = service.create_staff(s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST])
    token = login_token(client, "acme", "ana@acme.mx", analyst.initial_password)
    assert client.get("/v1/auth/me", headers=_bearer(token)).status_code == 200
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = service.disable_staff(s, analyst.id)
    assert client.get("/v1/auth/me", headers=_bearer(token)).status_code == 401
    with tenant_session(platform_ctx()) as s:
        row = s.get(Tenant, tenant.tenant_id)
        assert row is not None
        row.status = TenantStatus.SUSPENDED
    suspended = client.get("/v1/tenants/me", headers=admin_headers)
    assert (suspended.status_code, suspended.json()["code"]) == (403, "TENANT_SUSPENDED")


def test_tenants_me_and_ping(client: TestClient, tenant: CreatedTenant, admin_headers):
    me = client.get("/v1/tenants/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["slug"] == "acme"
    assert me.json()["env"] == "SANDBOX"
    assert me.json()["id"] == str(tenant.tenant_id)
    pong = client.post(
        "/v1/ping",
        json={"message": "hola"},
        headers={**admin_headers, "Idempotency-Key": "k-1"},
    )
    assert pong.status_code == 200
    assert pong.json()["pong"] == "hola"
    assert pong.json()["actor_id"] == str(tenant.admin_user_id)
```

Append to `tests/platform/test_api_conventions.py`:

```python
from datetime import timedelta  # noqa: E402

from tests.helpers import login_token, staff_session  # noqa: E402
from xolotl.modules.tenants import service  # noqa: E402
from xolotl.modules.tenants.service import CreatedTenant  # noqa: E402
from xolotl.platform import clock  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from xolotl.platform.api.idempotency import IdempotentRoute  # noqa: E402
from xolotl.platform.enums import Role  # noqa: E402


def _ping(client: TestClient, headers, key: str | None, message: str = "ping"):
    extra = {} if key is None else {"Idempotency-Key": key}
    return client.post("/v1/ping", json={"message": message}, headers={**headers, **extra})


def test_idempotency_key_is_required_before_authentication_is_checked(
    client: TestClient, admin_headers
):
    missing = _ping(client, admin_headers, None)
    assert (missing.status_code, missing.json()["code"]) == (400, "IDEMPOTENCY_KEY_REQUIRED")
    too_long = _ping(client, admin_headers, "k" * 129)
    assert too_long.status_code == 400
    unauthenticated = _ping(client, {}, "k-1")
    assert unauthenticated.status_code == 401
    assert client.post("/v1/auth/login", json={"tenant_slug": "x", "email": "y", "password": "z"}).status_code == 401  # no key needed under /v1/auth


def test_same_key_replays_the_stored_response(client: TestClient, admin_headers):
    first = _ping(client, admin_headers, "k-replay", "uno")
    second = _ping(client, admin_headers, "k-replay", "uno")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # same `at`: the handler did not run again
    assert "idempotent-replay" not in first.headers
    assert second.headers["idempotent-replay"] == "true"


def test_same_key_with_a_different_body_is_rejected(client: TestClient, admin_headers):
    assert _ping(client, admin_headers, "k-conflict", "uno").status_code == 200
    conflict = _ping(client, admin_headers, "k-conflict", "dos")
    assert (conflict.status_code, conflict.json()["code"]) == (422, "IDEMPOTENCY_KEY_REUSED")


def test_keys_are_scoped_per_actor(client: TestClient, tenant: CreatedTenant, admin_headers):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        issued = service.create_api_key(s, name="ingest", scopes=["tenants:write"])
    machine = {"Authorization": f"Bearer {issued.key}"}
    assert _ping(client, admin_headers, "shared-key", "from-admin").status_code == 200
    other = _ping(client, machine, "shared-key", "from-machine")
    assert other.status_code == 200
    assert other.json()["pong"] == "from-machine"
    assert "idempotent-replay" not in other.headers


def test_a_failed_request_releases_its_key(client: TestClient, admin_headers):
    invalid = client.post(
        "/v1/ping", json={"message": 123456}, headers={**admin_headers, "Idempotency-Key": "k-fail"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_FAILED"
    retry = _ping(client, admin_headers, "k-fail", "ok-now")
    assert retry.status_code == 200
    assert "idempotent-replay" not in retry.headers


def test_entries_expire_after_24_hours(client: TestClient, admin_headers, monkeypatch):
    first = _ping(client, admin_headers, "k-expire", "uno")
    later = clock.now() + timedelta(hours=25)
    monkeypatch.setattr(clock, "now", lambda: later)
    again = _ping(client, admin_headers, "k-expire", "uno")
    assert again.status_code == 200
    assert "idempotent-replay" not in again.headers
    assert again.json()["at"] != first.json()["at"]


def test_every_post_route_under_v1_is_idempotent_except_auth(app):
    for route in app.routes:
        if not isinstance(route, APIRoute) or "POST" not in route.methods:
            continue
        if route.path.startswith("/v1/") and not route.path.startswith("/v1/auth/"):
            assert isinstance(route, IdempotentRoute), route.path
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/modules/tenants/test_auth.py tests/platform/test_api_conventions.py -v --no-cov`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'xolotl.platform.api.idempotency'`.

- [ ] **Step 3: Write the idempotency layer and the router helper**

`src/xolotl/platform/api/idempotency.py`:

```python
"""``Idempotency-Key`` for every POST under ``/v1`` except ``/v1/auth/*`` (spec §7).

Flow per request: validate the header, resolve the actor, hash
``method\\npath\\nbody``, then *reserve* ``(tenant, actor, key)`` with an
``INSERT ... ON CONFLICT DO NOTHING``. A hit with the same hash replays the
stored response; a different hash is a 422; a reservation whose request is
still running is a 409. After a 2xx the response is written into the
endpoint's own session (``request.state.session``) so it commits atomically
with the endpoint's work; on any failure the reservation is released so the
client can retry with the same key.
"""

import hashlib
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import cast, override

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from xolotl.platform import clock
from xolotl.platform.api.app import app_state
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import resolve_request_actor
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.errors import (
    DomainError,
    IdempotencyConflictError,
    IdempotencyKeyRequiredError,
)

RESERVED_STATUS = 0
TTL = timedelta(hours=24)
MAX_KEY_LENGTH = 128
REPLAY_HEADER = "Idempotent-Replay"


@dataclass(frozen=True, slots=True)
class Stored:
    status_code: int
    body: object


def request_hash(method: str, path: str, body: bytes) -> bytes:
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\n")
    digest.update(path.encode())
    digest.update(b"\n")
    digest.update(body)
    return digest.digest()


def is_idempotent_request(request: Request) -> bool:
    path = request.url.path
    return (
        request.method == "POST"
        and path.startswith("/v1/")
        and not path.startswith("/v1/auth/")
    )


def _pk(actor: Actor, key: str) -> tuple[object, str, str]:
    return (actor.context.require_tenant_id(), actor.context.actor_id, key)


def _reserve(factory: SessionFactory, actor: Actor, key: str, digest: bytes) -> Stored | None:
    """Return the stored response for a replay, or ``None`` after reserving the key."""
    now = clock.now()
    with tenant_session(actor.context, factory=factory) as s:
        existing = s.get(IdempotencyKey, _pk(actor, key))
        if existing is not None and existing.expires_at <= now:
            s.delete(existing)
            s.flush()
            existing = None
        if existing is None:
            tenant_id, actor_id, _ = _pk(actor, key)
            inserted = s.execute(
                insert(IdempotencyKey)
                .values(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=digest,
                    status_code=RESERVED_STATUS,
                    response_body={},
                    expires_at=now + TTL,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "actor_id", "key"])
            ).rowcount
            if inserted == 1:
                return None
            existing = s.get(IdempotencyKey, _pk(actor, key))
            if existing is None:  # lost a race and the winner already released it
                msg = "a request with this Idempotency-Key is in progress"
                raise DomainError(msg, code="IDEMPOTENCY_IN_PROGRESS")
        if existing.request_hash != digest:
            raise IdempotencyConflictError
        if existing.status_code == RESERVED_STATUS:
            msg = "a request with this Idempotency-Key is in progress"
            raise DomainError(msg, code="IDEMPOTENCY_IN_PROGRESS")
        return Stored(existing.status_code, existing.response_body.get("body"))


def _store(s: Session, actor: Actor, key: str, status_code: int, body: object) -> None:
    row = s.get(IdempotencyKey, _pk(actor, key))
    if row is not None:
        row.status_code = status_code
        row.response_body = {"body": body}
        s.flush()


def _finalize(
    request: Request,
    factory: SessionFactory,
    actor: Actor,
    key: str,
    status_code: int,
    body: object,
) -> None:
    endpoint_session: object = getattr(request.state, "session", None)
    if isinstance(endpoint_session, Session) and endpoint_session.is_active:
        _store(endpoint_session, actor, key, status_code, body)  # commits with the endpoint
        return
    with tenant_session(actor.context, factory=factory) as s:
        _store(s, actor, key, status_code, body)


def _release(factory: SessionFactory, actor: Actor, key: str) -> None:
    with tenant_session(actor.context, factory=factory) as s:
        row = s.get(IdempotencyKey, _pk(actor, key))
        if row is not None and row.status_code == RESERVED_STATUS:
            s.delete(row)


def _json_body(response: Response) -> object | None:
    raw: object = getattr(response, "body", None)
    if not isinstance(raw, bytes) or not raw:
        return None
    try:
        parsed = cast("object", json.loads(raw))
    except ValueError:
        return None
    return parsed


class IdempotentRoute(APIRoute):
    @override
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if not is_idempotent_request(request):
                return await original(request)
            key = request.headers.get("idempotency-key")
            if key is None or not 1 <= len(key) <= MAX_KEY_LENGTH:
                raise IdempotencyKeyRequiredError
            actor = await run_in_threadpool(resolve_request_actor, request)
            body = await request.body()
            digest = request_hash(request.method, request.url.path, body)
            factory = app_state(request).session_factory
            stored = await run_in_threadpool(_reserve, factory, actor, key, digest)
            if stored is not None:
                return JSONResponse(
                    stored.body, status_code=stored.status_code, headers={REPLAY_HEADER: "true"}
                )
            try:
                response = await original(request)
            except BaseException:
                await run_in_threadpool(_release, factory, actor, key)
                raise
            parsed = _json_body(response)
            if 200 <= response.status_code < 300 and parsed is not None:  # noqa: PLR2004
                await run_in_threadpool(
                    _finalize, request, factory, actor, key, response.status_code, parsed
                )
            else:
                await run_in_threadpool(_release, factory, actor, key)
            return response

        return handler
```

`src/xolotl/platform/api/router.py`:

```python
"""Every module router is created here so the Idempotency-Key rule cannot be skipped."""

from fastapi import APIRouter

from xolotl.platform.api.idempotency import IdempotentRoute


def v1_router(*, prefix: str = "", tags: list[str] | None = None) -> APIRouter:
    return APIRouter(prefix=prefix, tags=tags, route_class=IdempotentRoute)
```

- [ ] **Step 4: Write the routes and wire them**

Append to `src/xolotl/modules/tenants/schemas.py`:

```python
class PingRequest(BaseModel):
    message: str = Field(default="ping", max_length=200)


class PingResponse(BaseModel):
    pong: str
    actor_id: str
    at: datetime
```

`src/xolotl/modules/tenants/routes_auth.py`:

```python
"""Staff login, TOTP and password routes (spec §6.1). No Idempotency-Key here."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from xolotl.modules.tenants import service
from xolotl.modules.tenants.schemas import (
    LoginRequest,
    MeResponse,
    MfaRequired,
    PasswordChangeRequest,
    TokenResponse,
    TotpConfirmRequest,
    TotpEnrollResponse,
    TotpLoginRequest,
)
from xolotl.platform.api.app import app_state
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.principal import SessionDep, StaffDep
from xolotl.platform.db import apply_context

router = v1_router(prefix="/auth", tags=["auth"])


def auth_session(request: Request) -> Iterator[Session]:
    """A platform-scoped session for the steps that happen before authentication."""
    session = app_state(request).session_factory()
    try:
        apply_context(session, service.auth_context())
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


AuthSessionDep = Annotated[Session, Depends(auth_session)]


def _to_response(outcome: service.LoginOutcome) -> TokenResponse | MfaRequired:
    if outcome.mfa_required:
        return MfaRequired(mfa_token=outcome.token)
    return TokenResponse(
        access_token=outcome.token,
        expires_in=outcome.expires_in,
        must_change_password=outcome.must_change_password,
    )


@router.post("/login")
def login(body: LoginRequest, session: AuthSessionDep) -> TokenResponse | MfaRequired:
    return _to_response(
        service.login(
            session, tenant_slug=body.tenant_slug, email=body.email, password=body.password
        )
    )


@router.post("/login/totp")
def login_totp(body: TotpLoginRequest, session: AuthSessionDep) -> TokenResponse | MfaRequired:
    return _to_response(service.login_totp(session, mfa_token=body.mfa_token, code=body.code))


@router.post("/password", status_code=204)
def change_password(body: PasswordChangeRequest, _staff: StaffDep, session: SessionDep) -> None:
    service.change_password(
        session, current_password=body.current_password, new_password=body.new_password
    )


@router.post("/totp/enroll")
def totp_enroll(_staff: StaffDep, session: SessionDep) -> TotpEnrollResponse:
    return service.enroll_totp(session)


@router.post("/totp/confirm", status_code=204)
def totp_confirm(body: TotpConfirmRequest, _staff: StaffDep, session: SessionDep) -> None:
    service.confirm_totp(session, code=body.code)


@router.get("/me")
def me(_staff: StaffDep, session: SessionDep) -> MeResponse:
    return service.me(session)
```

`src/xolotl/modules/tenants/routes_tenants.py`:

```python
"""The tenant's own record and the idempotency exercise endpoint (spec §6.2)."""

from typing import Annotated

from fastapi import Depends

from xolotl.modules.tenants.models import Tenant
from xolotl.modules.tenants.schemas import PingRequest, PingResponse, TenantResponse
from xolotl.platform import clock
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.errors import NotFoundError

router = v1_router(tags=["tenants"])

TenantReader = Annotated[Actor, Depends(require_access(scope="tenants:read"))]
TenantWriter = Annotated[Actor, Depends(require_access(scope="tenants:write", write=True))]


@router.get("/tenants/me")
def tenants_me(actor: TenantReader, session: SessionDep) -> TenantResponse:
    tenant = session.get(Tenant, actor.context.require_tenant_id())
    if tenant is None:
        raise NotFoundError
    return TenantResponse.model_validate(tenant)


@router.post("/ping")
def ping(body: PingRequest, actor: TenantWriter) -> PingResponse:
    return PingResponse(pong=body.message, actor_id=actor.context.actor_id, at=clock.now())
```

Modify `src/xolotl/app.py`:

```python
from fastapi import FastAPI

from xolotl.modules.tenants import routes_auth, routes_tenants
from xolotl.modules.tenants.service import resolve_bearer
from xolotl.platform.api.app import create_app
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.logging import configure_logging


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    configure_logging(get_settings().log_level)
    return create_app(
        session_factory=session_factory or get_sessionmaker(),
        actor_resolver=resolve_bearer,
        routers=[routes_auth.router, routes_tenants.router],
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/modules/tenants/test_auth.py tests/platform/test_api_conventions.py tests/platform/test_principal.py -v --no-cov`
Expected: all pass. If `test_same_key_replays_the_stored_response` fails because the second call ran the handler, check that `_finalize` found `request.state.session`: `/v1/ping` has no session dependency, so it takes the fresh-session branch; both branches must store `{"body": ...}`.

- [ ] **Step 6: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean. Import-linter must still report both contracts kept: the idempotency layer lives in `platform` and reads only `platform.api.models`.

```bash
git add src/xolotl/platform/api src/xolotl/modules/tenants src/xolotl/app.py tests/conftest.py tests/helpers.py tests/platform/test_api_conventions.py tests/modules/tenants/test_auth.py
git commit -m "feat(api): Idempotency-Key route class, staff auth routes and tenant hello endpoints"
```

---

### Task 12: Cursor pagination and the API-key routes

**Files:**
- Create: `src/xolotl/platform/api/pagination.py`, `src/xolotl/modules/tenants/routes_api_keys.py`
- Modify: `src/xolotl/app.py` (mount `routes_api_keys.router`)
- Test: `tests/platform/test_pagination.py`, `tests/modules/tenants/test_api_keys.py`

**Interfaces:**
- Consumes: `v1_router` (Task 11), `require_access`, `SessionDep`, `Actor` (Task 9), service and schemas (Task 10), `ApiKey` model (Task 6), `ValidationFailedError` (Task 2).
- Produces: `pagination.DEFAULT_LIMIT = 50`, `pagination.MAX_LIMIT = 200`, `pagination.encode_cursor(created_at, id_) -> str`, `pagination.decode_cursor(cursor) -> tuple[datetime, UUID]` (raises `ValidationFailedError`), `pagination.PageRequest(limit, cursor)`, `pagination.PageDep` (FastAPI dependency parsing `limit` 1–200 default 50 and `cursor`), `pagination.Page[T](items: list[T], next_cursor: str | None)`, `pagination.paginate(session, stmt, *, ts_col, id_col, key, page) -> tuple[list[M], str | None]` (keyset `(ts DESC, id DESC)`, fetches `limit + 1` to decide the next cursor); `routes_api_keys.router` (`POST /v1/api-keys` 201 ADMIN write, `GET /v1/api-keys` ADMIN, `POST /v1/api-keys/{id}/revoke` ADMIN write).

- [ ] **Step 1: Write the failing tests**

`tests/platform/test_pagination.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select

from tests.helpers import new_tenant, staff_session
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import StaffUser
from xolotl.platform.api.pagination import (
    PageRequest,
    decode_cursor,
    encode_cursor,
    paginate,
)
from xolotl.platform.enums import Role
from xolotl.platform.errors import ValidationFailedError


def test_cursor_round_trip_is_url_safe():
    at = datetime(2026, 10, 18, 12, 30, 45, 123456, tzinfo=UTC)
    ident = uuid4()
    cursor = encode_cursor(at, ident)
    assert "=" not in cursor and "+" not in cursor and "/" not in cursor
    assert decode_cursor(cursor) == (at, ident)


@pytest.mark.parametrize("bad", ["", "garbage", "bm90LWEtY3Vyc29y", "MjAyNi0xMC0xOHxub3QtYS11dWlk"])
def test_invalid_cursors_are_validation_failures(bad):
    with pytest.raises(ValidationFailedError):
        decode_cursor(bad)


def test_paginate_walks_rows_newest_first_with_id_tie_break(engine: Engine):
    created = new_tenant()
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        for n in range(4):  # same transaction -> identical created_at -> id breaks ties
            _ = service.create_staff(s, email=f"u{n}@acme.mx", display_name=f"U{n}", roles=[Role.ANALYST])
    seen: list = []
    cursor = None
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        expected = sorted(
            s.scalars(select(StaffUser)).all(), key=lambda u: (u.created_at, u.id), reverse=True
        )
        while True:
            rows, cursor = paginate(
                s,
                select(StaffUser),
                ts_col=StaffUser.created_at,
                id_col=StaffUser.id,
                key=lambda row: (row.created_at, row.id),
                page=PageRequest(limit=2, cursor=cursor),
            )
            seen.extend(rows)
            if cursor is None:
                break
    assert [u.id for u in seen] == [u.id for u in expected]
    assert len(seen) == 5
```

`tests/modules/tenants/test_api_keys.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import login_token, new_tenant, staff_session, system_ctx
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import ApiKey
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role


def _create(client: TestClient, headers, key: str, **body):
    payload = {"name": "ingest", "scopes": ["tenants:read"], **body}
    return client.post("/v1/api-keys", json=payload, headers={**headers, "Idempotency-Key": key})


def test_admin_creates_lists_and_revokes_keys(client: TestClient, admin_headers):
    created = _create(client, admin_headers, "k1")
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("xk_sbx_")
    assert body["key_prefix"] == body["key"][:15]
    listed = client.get("/v1/api-keys", headers=admin_headers).json()
    assert [item["key_prefix"] for item in listed["items"]] == [body["key_prefix"]]
    assert "key" not in listed["items"][0]
    assert listed["items"][0]["status"] == "ACTIVE"
    revoked = client.post(
        f"/v1/api-keys/{body['id']}/revoke", headers={**admin_headers, "Idempotency-Key": "r1"}
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    again = client.post(
        f"/v1/api-keys/{body['id']}/revoke", headers={**admin_headers, "Idempotency-Key": "r2"}
    )
    assert again.status_code == 200
    missing = client.post(
        "/v1/api-keys/00000000-0000-7000-8000-000000000000/revoke",
        headers={**admin_headers, "Idempotency-Key": "r3"},
    )
    assert missing.status_code == 404


def test_validation_and_env_rules(client: TestClient, admin_headers):
    bad_scope = _create(client, admin_headers, "k2", scopes=["Tenants:Read"])
    assert (bad_scope.status_code, bad_scope.json()["code"]) == (422, "VALIDATION_FAILED")
    wrong_env = _create(client, admin_headers, "k3", env="PROD")
    assert (wrong_env.status_code, wrong_env.json()["code"]) == (409, "CONFLICT")
    empty = _create(client, admin_headers, "k4", scopes=[])
    assert empty.status_code == 422


def test_only_admins_manage_keys(client: TestClient, tenant: CreatedTenant, admin_headers):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        analyst = service.create_staff(s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST])
    token = login_token(client, "acme", "ana@acme.mx", analyst.initial_password)
    headers = {"Authorization": f"Bearer {token}"}
    _ = client.post(
        "/v1/auth/password",
        json={"current_password": analyst.initial_password, "new_password": "another-long-password"},
        headers=headers,
    )
    denied = _create(client, headers, "k5")
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert client.get("/v1/api-keys", headers=headers).status_code == 403


def test_keys_authenticate_machines_within_their_scope(client: TestClient, admin_headers):
    read_key = _create(client, admin_headers, "k6").json()["key"]
    machine = {"Authorization": f"Bearer {read_key}"}
    assert client.get("/v1/tenants/me", headers=machine).status_code == 200
    denied = client.post("/v1/ping", json={}, headers={**machine, "Idempotency-Key": "p1"})
    assert (denied.status_code, denied.json()["code"]) == (403, "SCOPE_MISSING")
    staff_only = client.get("/v1/api-keys", headers=machine)
    assert (staff_only.status_code, staff_only.json()["code"]) == (403, "ROLE_FORBIDDEN")
    write_key = _create(client, admin_headers, "k7", scopes=["tenants:write"]).json()["key"]
    pong = client.post(
        "/v1/ping", json={}, headers={"Authorization": f"Bearer {write_key}", "Idempotency-Key": "p2"}
    )
    assert pong.status_code == 200


def test_revoked_and_foreign_keys_are_rejected_and_do_not_touch_last_used(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    issued = _create(client, admin_headers, "k8").json()
    machine = {"Authorization": f"Bearer {issued['key']}"}
    assert client.get("/v1/tenants/me", headers=machine).status_code == 200
    _ = client.post(f"/v1/api-keys/{issued['id']}/revoke", headers={**admin_headers, "Idempotency-Key": "r9"})

    def last_used():
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            return s.scalars(select(ApiKey.last_used_at).where(ApiKey.key_prefix == issued["key_prefix"])).one()

    before = last_used()
    assert before is not None
    assert client.get("/v1/tenants/me", headers=machine).status_code == 401
    assert last_used() == before

    other = new_tenant(slug="beta", email="admin@beta.mx")
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        foreign = service.create_api_key(s, name="x", scopes=["tenants:read"])
    seen = client.get("/v1/tenants/me", headers={"Authorization": f"Bearer {foreign.key}"})
    assert seen.status_code == 200
    assert seen.json()["slug"] == "beta"  # a key only ever sees its own tenant
    unknown_prefix = "xk_sbx_00000000_" + "0" * 32
    assert client.get("/v1/tenants/me", headers={"Authorization": f"Bearer {unknown_prefix}"}).status_code == 401


def test_list_pagination_bounds_and_cursors(client: TestClient, admin_headers):
    for n in range(3):
        assert _create(client, admin_headers, f"pk{n}", name=f"key-{n}").status_code == 201
    assert client.get("/v1/api-keys", params={"limit": 0}, headers=admin_headers).status_code == 422
    assert client.get("/v1/api-keys", params={"limit": 201}, headers=admin_headers).status_code == 422
    first = client.get("/v1/api-keys", params={"limit": 2}, headers=admin_headers).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get(
        "/v1/api-keys", params={"limit": 2, "cursor": first["next_cursor"]}, headers=admin_headers
    ).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    names = [i["name"] for i in first["items"] + second["items"]]
    assert sorted(names) == ["key-0", "key-1", "key-2"]
    bad = client.get("/v1/api-keys", params={"cursor": "garbage"}, headers=admin_headers)
    assert (bad.status_code, bad.json()["code"]) == (422, "VALIDATION_FAILED")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/platform/test_pagination.py tests/modules/tenants/test_api_keys.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.platform.api.pagination'`

- [ ] **Step 3: Write the pagination helper and the routes**

`src/xolotl/platform/api/pagination.py`:

```python
"""Keyset pagination behind an opaque cursor (spec §7).

Lists are ordered ``(timestamp DESC, id DESC)``; the cursor is the URL-safe
base64 of ``<timestamp isoformat>|<id>`` of the last row returned.
"""

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import InstrumentedAttribute, Session

from xolotl.platform.errors import ValidationFailedError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(created_at: datetime, id_: UUID) -> str:
    raw = f"{created_at.isoformat()}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    msg = "invalid cursor"
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailedError(msg) from exc
    timestamp, separator, ident = raw.partition("|")
    if separator != "|":
        raise ValidationFailedError(msg)
    try:
        return datetime.fromisoformat(timestamp), UUID(ident)
    except ValueError as exc:
        raise ValidationFailedError(msg) from exc


@dataclass(frozen=True, slots=True)
class PageRequest:
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None


def page_request(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PageRequest:
    return PageRequest(limit=limit, cursor=cursor)


PageDep = Annotated[PageRequest, Depends(page_request)]


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def paginate[M](
    session: Session,
    stmt: Select[tuple[M]],
    *,
    ts_col: InstrumentedAttribute[datetime],
    id_col: InstrumentedAttribute[UUID],
    key: Callable[[M], tuple[datetime, UUID]],
    page: PageRequest,
) -> tuple[list[M], str | None]:
    if page.cursor is not None:
        after_ts, after_id = decode_cursor(page.cursor)
        stmt = stmt.where(
            or_(ts_col < after_ts, and_(ts_col == after_ts, id_col < after_id))
        )
    rows = list(
        session.scalars(stmt.order_by(ts_col.desc(), id_col.desc()).limit(page.limit + 1))
    )
    if len(rows) <= page.limit:
        return rows, None
    rows = rows[: page.limit]
    last_ts, last_id = key(rows[-1])
    return rows, encode_cursor(last_ts, last_id)
```

`src/xolotl/modules/tenants/routes_api_keys.py`:

```python
"""Scoped API keys managed by tenant admins (spec §6.2)."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import ApiKey
from xolotl.modules.tenants.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
)
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/api-keys", tags=["api-keys"])

AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]


@router.post("", status_code=201)
def create_api_key(
    body: ApiKeyCreateRequest, _admin: AdminWriter, session: SessionDep
) -> ApiKeyCreatedResponse:
    return service.create_api_key(session, name=body.name, scopes=body.scopes, env=body.env)


@router.get("")
def list_api_keys(_admin: AdminReader, session: SessionDep, page: PageDep) -> Page[ApiKeyResponse]:
    rows, next_cursor = paginate(
        session,
        select(ApiKey),
        ts_col=ApiKey.created_at,
        id_col=ApiKey.id,
        key=lambda row: (row.created_at, row.id),  # noqa: FURB118
        page=page,
    )
    return Page(items=[ApiKeyResponse.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.post("/{api_key_id}/revoke")
def revoke_api_key(api_key_id: UUID, _admin: AdminWriter, session: SessionDep) -> ApiKeyResponse:
    return service.revoke_api_key(session, api_key_id)
```

Modify `src/xolotl/app.py`: import `routes_api_keys` next to the other route modules and add `routes_api_keys.router` to the `routers` list.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/platform/test_pagination.py tests/modules/tenants/test_api_keys.py tests/platform/test_api_conventions.py -v --no-cov`
Expected: all pass (the structural idempotency test in the conventions suite now also covers the two new POST routes).

- [ ] **Step 5: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean. If basedpyright rejects `Page[ApiKeyResponse]` as a return annotation, the Pydantic generic needs `Page[ApiKeyResponse]` referenced directly (not through a variable); the code above already does that.

```bash
git add src/xolotl/platform/api/pagination.py src/xolotl/modules/tenants/routes_api_keys.py src/xolotl/app.py tests/platform/test_pagination.py tests/modules/tenants/test_api_keys.py
git commit -m "feat(api): keyset cursor pagination and the API-key management routes"
```

---

### Task 13: Staff routes and audit routes

**Files:**
- Create: `src/xolotl/modules/tenants/routes_staff.py`, `src/xolotl/platform/audit/routes.py`
- Modify: `src/xolotl/app.py` (mount `routes_staff.router` and `audit routes.router`)
- Test: `tests/modules/tenants/test_staff.py`, `tests/platform/test_audit_routes.py`

**Interfaces:**
- Consumes: `v1_router`, `Page`, `PageDep`, `paginate` (Tasks 11–12), `require_access`, `SessionDep`, `Actor` (Task 9), service and schemas (Task 10), `AuditEventRow`, `verify_chain`, `ChainVerification` (Tasks 6–7).
- Produces: `routes_staff.router` (`POST /v1/staff` 201 ADMIN write, `GET /v1/staff` ADMIN, `POST /v1/staff/{id}/disable` ADMIN write); `xolotl.platform.audit.routes.router` (`GET /v1/audit` with optional `action` filter, `GET /v1/audit/verify`; roles MLRO, ADMIN, AUDITOR_RO; staff only), `AuditEventResponse(id, seq, at, actor_type, actor_id, action, entity_type, entity_id, detail, prev_hash: str, hash: str)` with hashes as hex.

- [ ] **Step 1: Write the failing tests**

`tests/modules/tenants/test_staff.py`:

```python
from fastapi.testclient import TestClient

from tests.helpers import login_token
from xolotl.modules.tenants.service import CreatedTenant


def _create(client: TestClient, headers, key: str, **body):
    payload = {"email": "ana@acme.mx", "display_name": "Ana", "roles": ["ANALYST"], **body}
    return client.post("/v1/staff", json=payload, headers={**headers, "Idempotency-Key": key})


def test_admin_creates_lists_and_disables_staff(client: TestClient, tenant: CreatedTenant, admin_headers):
    created = _create(client, admin_headers, "s1", roles=["ANALYST", "MLRO"])
    assert created.status_code == 201
    body = created.json()
    assert body["roles"] == ["ANALYST", "MLRO"]
    assert len(body["initial_password"]) >= 20
    assert login_token(client, "acme", "ana@acme.mx", body["initial_password"])

    listed = client.get("/v1/staff", headers=admin_headers).json()
    assert {item["email"] for item in listed["items"]} == {"admin@acme.mx", "ana@acme.mx"}
    assert all("initial_password" not in item for item in listed["items"])
    page = client.get("/v1/staff", params={"limit": 1}, headers=admin_headers).json()
    assert len(page["items"]) == 1 and page["next_cursor"]

    duplicate = _create(client, admin_headers, "s2", email="ANA@acme.mx")
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "CONFLICT")
    no_roles = _create(client, admin_headers, "s3", email="b@acme.mx", roles=[])
    assert no_roles.status_code == 422

    disabled = client.post(f"/v1/staff/{body['id']}/disable", headers={**admin_headers, "Idempotency-Key": "d1"})
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    denied = client.post(
        "/v1/auth/login",
        json={"tenant_slug": "acme", "email": "ana@acme.mx", "password": body["initial_password"]},
    )
    assert denied.status_code == 401
    me = client.post(
        f"/v1/staff/{tenant.admin_user_id}/disable", headers={**admin_headers, "Idempotency-Key": "d2"}
    )
    assert (me.status_code, me.json()["code"]) == (409, "CONFLICT")
    missing = client.post(
        "/v1/staff/00000000-0000-7000-8000-000000000000/disable",
        headers={**admin_headers, "Idempotency-Key": "d3"},
    )
    assert missing.status_code == 404


def test_non_admins_cannot_manage_staff(client: TestClient, admin_headers):
    created = _create(client, admin_headers, "s4", roles=["AUDITOR_RO"]).json()
    token = login_token(client, "acme", "ana@acme.mx", created["initial_password"])
    headers = {"Authorization": f"Bearer {token}"}
    changed = client.post(
        "/v1/auth/password",
        json={"current_password": created["initial_password"], "new_password": "auditor-long-password"},
        headers=headers,
    )
    assert changed.status_code == 204
    assert client.get("/v1/staff", headers=headers).status_code == 403
    denied = _create(client, headers, "s5", email="c@acme.mx")
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
```

`tests/platform/test_audit_routes.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import login_token, staff_session
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role


def _staff_headers(client: TestClient, tenant: CreatedTenant, email: str, roles: list[Role]) -> dict[str, str]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        created = service.create_staff(s, email=email, display_name=email, roles=roles)
    token = login_token(client, "acme", email, created.initial_password)
    headers = {"Authorization": f"Bearer {token}"}
    changed = client.post(
        "/v1/auth/password",
        json={"current_password": created.initial_password, "new_password": "a-long-enough-password"},
        headers=headers,
    )
    assert changed.status_code == 204
    return headers


def test_audit_list_is_newest_first_and_filterable(client: TestClient, tenant: CreatedTenant, admin_headers):
    resp = client.get("/v1/audit", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    seqs = [item["seq"] for item in items]
    assert seqs == sorted(seqs, reverse=True)
    assert items[0]["action"] == "auth.password_changed"
    assert len(items[0]["hash"]) == 64 and len(items[0]["prev_hash"]) == 64
    assert items[-1]["action"] == "tenant.created"
    filtered = client.get("/v1/audit", params={"action": "auth.login_succeeded"}, headers=admin_headers).json()
    assert {item["action"] for item in filtered["items"]} == {"auth.login_succeeded"}
    paged = client.get("/v1/audit", params={"limit": 2}, headers=admin_headers).json()
    assert len(paged["items"]) == 2 and paged["next_cursor"]


def test_audit_roles(client: TestClient, tenant: CreatedTenant, admin_headers):
    auditor = _staff_headers(client, tenant, "aud@acme.mx", [Role.AUDITOR_RO])
    mlro = _staff_headers(client, tenant, "mlro@acme.mx", [Role.MLRO])
    analyst = _staff_headers(client, tenant, "ana@acme.mx", [Role.ANALYST])
    assert client.get("/v1/audit", headers=auditor).status_code == 200
    assert client.get("/v1/audit/verify", headers=mlro).status_code == 200
    denied = client.get("/v1/audit", headers=analyst)
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        key = service.create_api_key(s, name="x", scopes=["audit:read"])
    machine = client.get("/v1/audit", headers={"Authorization": f"Bearer {key.key}"})
    assert machine.status_code == 403


def test_verify_reports_a_tampered_row(client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine):
    ok = client.get("/v1/audit/verify", headers=admin_headers).json()
    assert ok["valid"] is True
    assert ok["entries_checked"] >= 3
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(
            text("UPDATE audit_event SET action = 'forged' WHERE tenant_id = :t AND seq = 1"),
            {"t": tenant.tenant_id},
        )
    broken = client.get("/v1/audit/verify", headers=admin_headers).json()
    assert broken["valid"] is False
    assert broken["first_broken_seq"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/modules/tenants/test_staff.py tests/platform/test_audit_routes.py -v --no-cov`
Expected: FAIL with 404 responses (`NOT_FOUND`) because the routes are not mounted yet.

- [ ] **Step 3: Write the routes and wire them**

`src/xolotl/modules/tenants/routes_staff.py`:

```python
"""Staff management by tenant admins (spec §6.4)."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import StaffUser
from xolotl.modules.tenants.schemas import (
    StaffCreatedResponse,
    StaffCreateRequest,
    StaffResponse,
)
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/staff", tags=["staff"])

AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]


@router.post("", status_code=201)
def create_staff(
    body: StaffCreateRequest, _admin: AdminWriter, session: SessionDep
) -> StaffCreatedResponse:
    return service.create_staff(
        session, email=body.email, display_name=body.display_name, roles=body.roles
    )


@router.get("")
def list_staff(_admin: AdminReader, session: SessionDep, page: PageDep) -> Page[StaffResponse]:
    rows, next_cursor = paginate(
        session,
        select(StaffUser),
        ts_col=StaffUser.created_at,
        id_col=StaffUser.id,
        key=lambda row: (row.created_at, row.id),  # noqa: FURB118
        page=page,
    )
    return Page(items=[StaffResponse.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.post("/{staff_user_id}/disable")
def disable_staff(staff_user_id: UUID, _admin: AdminWriter, session: SessionDep) -> StaffResponse:
    return service.disable_staff(session, staff_user_id)
```

`src/xolotl/platform/audit/routes.py`:

```python
"""Read and verify the tenant's audit chain (spec §8.3)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel, JsonValue
from sqlalchemy import select

from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.audit import ChainVerification, verify_chain
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/audit", tags=["audit"])

AuditReader = Annotated[
    Actor, Depends(require_access(roles=(Role.MLRO, Role.ADMIN, Role.AUDITOR_RO)))
]


class AuditEventResponse(BaseModel):
    id: UUID
    seq: int
    at: datetime
    actor_type: str
    actor_id: str
    action: str
    entity_type: str | None
    entity_id: str | None
    detail: dict[str, JsonValue] | None
    prev_hash: str
    hash: str


def _to_response(row: AuditEventRow) -> AuditEventResponse:
    return AuditEventResponse.model_validate(
        {
            "id": row.id,
            "seq": row.seq,
            "at": row.at,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "detail": row.detail,
            "prev_hash": row.prev_hash.hex(),
            "hash": row.hash.hex(),
        }
    )


@router.get("")
def list_audit(
    _reader: AuditReader,
    session: SessionDep,
    page: PageDep,
    action: Annotated[str | None, Query(max_length=120)] = None,
) -> Page[AuditEventResponse]:
    stmt = select(AuditEventRow)
    if action is not None:
        stmt = stmt.where(AuditEventRow.action == action)
    rows, next_cursor = paginate(
        session,
        stmt,
        ts_col=AuditEventRow.at,
        id_col=AuditEventRow.id,
        key=lambda row: (row.at, row.id),  # noqa: FURB118
        page=page,
    )
    return Page(items=[_to_response(row) for row in rows], next_cursor=next_cursor)


@router.get("/verify")
def verify_audit(_reader: AuditReader, session: SessionDep) -> ChainVerification:
    return verify_chain(session)
```

Modify `src/xolotl/app.py`:

```python
from fastapi import FastAPI

from xolotl.modules.tenants import routes_api_keys, routes_auth, routes_staff, routes_tenants
from xolotl.modules.tenants.service import resolve_bearer
from xolotl.platform.api.app import create_app
from xolotl.platform.audit import routes as audit_routes
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.logging import configure_logging


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    configure_logging(get_settings().log_level)
    return create_app(
        session_factory=session_factory or get_sessionmaker(),
        actor_resolver=resolve_bearer,
        routers=[
            routes_auth.router,
            routes_tenants.router,
            routes_api_keys.router,
            routes_staff.router,
            audit_routes.router,
        ],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/modules/tenants/test_staff.py tests/platform/test_audit_routes.py -v --no-cov`
Expected: all pass. `/v1/audit` lists events in `(at DESC, id DESC)` order, which equals `seq DESC` because `record` stamps `at` under the per-tenant lock.

- [ ] **Step 5: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean.

```bash
git add src/xolotl/modules/tenants/routes_staff.py src/xolotl/platform/audit/routes.py src/xolotl/app.py tests/modules/tenants/test_staff.py tests/platform/test_audit_routes.py
git commit -m "feat(api): staff management and audit list/verify routes"
```

---

### Task 14: The `xolotl` command line

**Files:**
- Create: `src/xolotl/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `migrate.bootstrap/upgrade/reset` (Task 4), `service.create_tenant/list_tenants` (Task 10), `verify_chain` (Task 7), `tenant_session`, `TenantContext.platform`, `configure_logging`, `get_settings`.
- Produces: Typer app `xolotl.cli:app` with commands `serve [--reload] [--port N]`, `db bootstrap`, `db upgrade`, `db reset --yes`, `tenant create --slug --legal-name --admin-email [--country MX] [--cell mx-1] [--env SANDBOX|PROD]` (prints the admin's one-time password once), `tenant list`, `audit verify --tenant <slug>` (exit code 1 when the chain is broken or the tenant is unknown). Service errors print `error: <CODE>: <detail>` to stderr and exit 1.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import re

from sqlalchemy import Engine, text
from typer.testing import CliRunner

from tests.helpers import auth_session
from xolotl.cli import app
from xolotl.modules.tenants import service
from xolotl.platform import db

runner = CliRunner()


def _create(slug: str = "cli-demo"):
    return runner.invoke(
        app,
        ["tenant", "create", "--slug", slug, "--legal-name", "CLI Demo SA", "--admin-email", "admin@cli.mx"],
    )


def test_help_works_without_a_database():
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["serve", "--help"]).exit_code == 0


def test_db_bootstrap_is_idempotent(admin_engine: Engine):
    assert runner.invoke(app, ["db", "bootstrap"]).exit_code == 0
    assert runner.invoke(app, ["db", "bootstrap"]).exit_code == 0
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0


def test_tenant_create_prints_the_password_once_and_rejects_duplicates(engine: Engine):
    result = _create()
    assert result.exit_code == 0, result.output
    assert result.output.count("one-time password:") == 1
    match = re.search(r"one-time password: (\S+)", result.output)
    assert match
    password = match.group(1)
    with auth_session() as s:
        outcome = service.login(s, tenant_slug="cli-demo", email="admin@cli.mx", password=password)
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
            conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).all()
        )
    assert {"tenant", "staff_user", "audit_event_default", "alembic_version"} <= tables
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'xolotl.cli'`

- [ ] **Step 3: Write the CLI**

`src/xolotl/cli.py`:

```python
"""The ``xolotl`` command line (spec §10)."""

import getpass
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
import uvicorn
from sqlalchemy import select

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import Tenant
from xolotl.platform import migrate
from xolotl.platform.audit import verify_chain
from xolotl.platform.config import get_settings
from xolotl.platform.db import APP_ROLE, get_sessionmaker, tenant_session
from xolotl.platform.enums import TenantEnv
from xolotl.platform.errors import XolotlError
from xolotl.platform.logging import configure_logging
from xolotl.platform.tenancy.context import TenantContext

app = typer.Typer(no_args_is_help=True, help="xolotl: multi-tenant KYC/AML platform")
db_app = typer.Typer(no_args_is_help=True, help="Database bootstrap and migrations")
tenant_app = typer.Typer(no_args_is_help=True, help="Tenant administration")
audit_app = typer.Typer(no_args_is_help=True, help="Audit chain tools")
app.add_typer(db_app, name="db")
app.add_typer(tenant_app, name="tenant")
app.add_typer(audit_app, name="audit")


def _cli_actor() -> str:
    return f"cli:{getpass.getuser()}"


@contextmanager
def _cli_errors() -> Iterator[None]:
    try:
        yield
    except XolotlError as exc:
        typer.echo(f"error: {exc.code}: {exc.detail}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def serve(
    *,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (development)")] = False,
    port: Annotated[int | None, typer.Option(help="Port (defaults to XOLOTL_PORT)")] = None,
) -> None:
    """Run pending migrations, then serve the API."""
    settings = get_settings()
    configure_logging(settings.log_level)
    migrate.upgrade()
    uvicorn.run(
        "xolotl.app:build_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104  # container entrypoint
        port=port or settings.port,
        reload=reload,
        log_config=None,
    )


@db_app.command("bootstrap")
def db_bootstrap() -> None:
    """Create the application role and its grants (idempotent)."""
    migrate.bootstrap()
    typer.echo(f"role {APP_ROLE} is ready")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply migrations up to head."""
    migrate.upgrade()
    typer.echo("database upgraded to head")


@db_app.command("reset")
def db_reset(
    *,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm dropping every table")] = False,
) -> None:
    """Drop the public schema and migrate from scratch (never in prod)."""
    if not yes:
        typer.echo("refusing to reset without --yes", err=True)
        raise typer.Exit(code=2)
    migrate.reset()
    typer.echo("database reset and migrated")


@tenant_app.command("create")
def tenant_create(
    *,
    slug: Annotated[str, typer.Option(help="Workspace slug, e.g. acme-fintech")],
    legal_name: Annotated[str, typer.Option(help="Legal name of the obligated entity")],
    admin_email: Annotated[str, typer.Option(help="Email of the first ADMIN user")],
    country: Annotated[str, typer.Option(help="ISO 3166-1 alpha-2")] = "MX",
    cell: Annotated[str, typer.Option(help="Deployment cell")] = "mx-1",
    env: Annotated[TenantEnv, typer.Option(help="SANDBOX or PROD")] = TenantEnv.SANDBOX,
) -> None:
    """Create a tenant and its first administrator."""
    with _cli_errors():
        created = service.create_tenant(
            get_sessionmaker(),
            slug=slug,
            legal_name=legal_name,
            country_code=country,
            admin_email=admin_email,
            env=env,
            cell=cell,
            actor_id=_cli_actor(),
        )
    typer.echo(f"tenant {created.slug} created ({created.tenant_id})")
    typer.echo(f"admin {created.admin_email}")
    typer.echo(f"one-time password: {created.admin_password}")
    typer.echo("Shown once; it must be changed at first login.")


@tenant_app.command("list")
def tenant_list() -> None:
    """List tenants as slug, env, status and id."""
    with tenant_session(TenantContext.platform(_cli_actor())) as s:
        for tenant in service.list_tenants(s):
            typer.echo(f"{tenant.slug}\t{tenant.env.value}\t{tenant.status.value}\t{tenant.id}")


@audit_app.command("verify")
def audit_verify(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
) -> None:
    """Verify a tenant's audit chain; exit 1 when it is broken."""
    actor = _cli_actor()
    with tenant_session(TenantContext.platform(actor)) as s:
        row = s.scalar(select(Tenant).where(Tenant.slug == tenant.strip().lower()))
        if row is None:
            typer.echo(f"error: NOT_FOUND: no tenant {tenant!r}", err=True)
            raise typer.Exit(code=1)
        tenant_id = row.id
    with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
        result = verify_chain(s)
    typer.echo(result.model_dump_json(indent=2))
    if not result.valid:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v --no-cov`
Expected: 6 passed. Keep `test_db_reset_requires_confirmation_and_remigrates` last in the file: it drops and recreates the schema, and the engines are disposed afterwards so pooled connections do not hold stale plans.

- [ ] **Step 5: Run the static gates, then commit**

Run: `make lint types importlint`
Expected: clean.

```bash
git add src/xolotl/cli.py tests/test_cli.py
git commit -m "feat(cli): serve, db bootstrap/upgrade/reset, tenant create/list and audit verify"
```

---

### Task 15: Dockerfile, CI workflow, README and the full gate

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`
- Modify: `README.md` (replace the Task 1 stub), `docker-compose.yml` (add the `api` service)

**Interfaces:**
- Consumes: everything above. Produces the shippable image (`xolotl serve` on port 8000) and a green `make ci`.

- [ ] **Step 1: Write the Docker and CI files**

`.dockerignore`:

```
.git
.venv
.pytest_cache
.ruff_cache
htmlcov
.coverage
data
console/node_modules
docs
tests
```

`Dockerfile` (API only; plan 1d adds the console build stage):

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 xolotl && chown -R xolotl:xolotl /app
USER xolotl
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"
CMD ["xolotl", "serve"]
```

Append the `api` service to `docker-compose.yml` (under `services:`):

```yaml
  api:
    build: .
    environment:
      XOLOTL_DATABASE_URL: postgresql+psycopg://xolotl_app:xolotl@db:5432/xolotl
      XOLOTL_DATABASE_ADMIN_URL: postgresql+psycopg://xolotl:xolotl@db:5432/xolotl
      XOLOTL_ENV: dev
      XOLOTL_LOG_LEVEL: INFO
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
    command: ["sh", "-c", "xolotl db bootstrap && xolotl serve"]
```

`.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  gates:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.13"
          enable-cache: true
      - run: uv sync --frozen
      - run: make ci

  image:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          load: true
          tags: xolotl:ci
      - run: docker run --rm xolotl:ci xolotl --help
```

`README.md` (replace the stub):

```markdown
# xolotl

Multi-tenant KYC/AML compliance platform for LATAM regulated entities: policy
packs, screening, transaction monitoring, regulatory reporting (Formato 41,
avisos) and case management. This repository is the Python monolith; the
product and package are named `xolotl`.

## Status

Plan 1a of the platform kernel: tenancy with Postgres row-level security,
staff login with TOTP, scoped API keys, problem+json API conventions with
`Idempotency-Key`, the per-tenant audit chain and the `xolotl` CLI. Design
documents live in `docs/superpowers/specs/`; implementation plans in
`docs/superpowers/plans/`.

## Quick start

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), Docker.

```bash
docker compose up -d db
uv sync
cp .env.example .env
uv run xolotl db bootstrap
uv run xolotl db upgrade
uv run xolotl tenant create --slug acme --legal-name "Acme SA de CV" --admin-email admin@acme.mx
uv run xolotl serve --reload
```

Open `http://localhost:8000/v1/docs`. Sign in with `POST /v1/auth/login`
(`tenant_slug`, `email`, the one-time password printed by `tenant create`),
then change the password with `POST /v1/auth/password`.

Every `POST` under `/v1` except `/v1/auth/*` needs an `Idempotency-Key` header.
Machine access uses API keys created by an ADMIN at `POST /v1/api-keys`
(`Authorization: Bearer xk_...`).

## Configuration

Environment variables are prefixed `XOLOTL_`; see `.env.example`. `XOLOTL_ENV=prod`
refuses the default `JWT_SECRET`.

## Development

```bash
make ci        # ruff, ty, basedpyright, vulture, import-linter, pytest (coverage >= 90)
make test      # pytest only; needs Docker for the Postgres testcontainer
make format    # ruff --fix and ruff format
```

Tests run against a real Postgres 17 container with real commits; set
`XOLOTL_TEST_DATABASE_ADMIN_URL` to reuse an existing server instead.

## Layout

`src/xolotl/platform/` is the shared kernel (imports nothing from modules);
`src/xolotl/modules/<name>/` are feature modules that own their tables, routers
and events; `src/xolotl/app.py` is the composition root; migrations are one
Alembic tree under `src/xolotl/migrations/`.
```

- [ ] **Step 2: Build the image and run the whole gate**

Run: `docker build -t xolotl:dev . && docker run --rm xolotl:dev xolotl --help`
Expected: the build succeeds and the help text prints.

Run: `make ci`
Expected: every gate green: ruff (check and format), ty, basedpyright, vulture, import-linter (2 contracts kept), pytest with total coverage at or above 90%. If coverage falls short, the uncovered lines are almost always `cli.serve` (uvicorn) and the `readyz` failure branch; add `# pragma: no cover` only to the `uvicorn.run(...)` call, and cover `readyz`'s failure branch with a test that monkeypatches `xolotl.platform.api.health.get_engine` to raise.

Run: `docker compose up -d && sleep 5 && curl -s localhost:8000/readyz && docker compose down`
Expected: `{"status":"ok","database":"ok"}`.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml .github/workflows/ci.yml README.md uv.lock
git commit -m "build: Dockerfile, compose api service, CI workflow and README"
```

---

## Exit criteria for plan 1a (subset of spec §13 owned by this plan)

- `make ci` green, including the isolation harness for every table in migration 0001.
- A repeated `Idempotency-Key` returns the stored response with `Idempotent-Replay: true`; a different body is 422 `IDEMPOTENCY_KEY_REUSED`.
- Errors are `application/problem+json` with stable codes.
- `GET /v1/audit/verify` detects a manually altered row (test and CLI).
- A staff user signs in with TOTP, an ADMIN creates a scoped API key, and the key cannot call outside its scope nor see another tenant's data.
- The Docker image builds and `xolotl serve` answers `/readyz` against the compose database. (Serving the console at `/` is plan 1d.)
