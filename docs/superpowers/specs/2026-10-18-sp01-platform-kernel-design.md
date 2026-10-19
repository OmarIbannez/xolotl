# SP-01 — Platform kernel (xolotl) — Sub-project design

**Parent spec:** `docs/superpowers/specs/2026-10-18-program-architecture-design.md` (decisions D1–D10, D20, D22, D23; ports §4; core model §6; SP-01 row and exit criteria §7.1).
**Status:** written 2026-10-18 against the approved program spec. This document pins the exact tables, interfaces, endpoints and events the four SP-01 implementation plans share.

## 1. Scope

In scope: repository and toolchain; tenancy with RLS and the isolation harness; staff authentication (local password + TOTP) and scoped API keys; API conventions (problem+json, Idempotency-Key, cursor pagination, OpenAPI, health); per-tenant encryption with blind indexes and the local key provider; per-tenant hash-chained audit; object store (local filesystem; the port is ready for an S3 adapter); transactional outbox with in-process dispatcher and signed webhooks; Postgres job queue, cron schedules and the step-table workflow runner; the `xolotl` CLI; the console shell forked from sentinel with tenant login, TOTP, API-key and webhook pages, audit page and i18n scaffold; Dockerfile and docker-compose.

Out of scope: any feature tables beyond this document; OIDC provider integration (claims shape only); Kafka, Temporal, KMS and S3 adapters (the MVP is one application container and Postgres; the ports stay ready); presigned object URLs (added by onboarding); rate limiting (added with the first public write endpoint); cloud IaC; load testing.

Delivered as four plans, in this order:
- **1a — Repo, tenancy, API conventions, auth, audit chain, CLI** (`tenants` module, session contract, isolation harness, problem+json, idempotency, pagination, staff login with TOTP, API keys, the per-tenant audit chain of §8.3 because login and key events are audited from day one, `xolotl serve|db|tenant|audit`).
- **1b — Data protection: key provider, encrypted fields, blind index, object store** (adds `VIEW_PII` on top of the 1a audit chain).
- **1c — Events and jobs: outbox, dispatcher, webhooks, job queue, schedules, workflow runner, `xolotl worker`.**
- **1d — Console shell: fork, auth flow, tenant pages, audit page, i18n, Docker image with console.**

## 2. Toolchain and repository conventions

- Python 3.13, `uv` project (not a workspace), build backend hatchling, package `xolotl` under `src/`, CLI entry point `xolotl = xolotl.cli:app`.
- Runtime dependencies (1a): `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy>=2`, `alembic`, `psycopg[binary]`, `pyjwt[crypto]`, `argon2-cffi`, `pyotp`, `typer`, `httpx`. Added by 1b: `cryptography`. Added by 1c: `croniter`.
- Dev dependencies: `ruff`, `ty`, `basedpyright`, `vulture`, `pytest`, `pytest-cov`, `testcontainers[postgres]`, `import-linter`, `respx` (1c), `types-croniter` (1c).
- Quality gates copied from sentinel's `pyproject.toml` (ruff `select = ["ALL"]` with the same ignores, line length 88, `ty` with `error-on-warning`, `basedpyright` in `all` mode with `reportAny`/`reportExplicitAny` errors, vulture at 80, pytest with `--cov-fail-under=90`, `filterwarnings = error`). `make ci` runs them all and reports every failure.
- Import-linter contracts: `xolotl.platform` must not import `xolotl.modules`; `xolotl.modules.<a>` may import `xolotl.modules.<b>` only when `<b>` is listed as a dependency of `<a>` in the program spec §3.3.
- Enum convention: `StrEnum` with UPPERCASE values stored as non-native `String(32)` columns (sentinel's `_enum` helper). One module `xolotl/platform/enums.py` for kernel enums.
- Commit cadence: every task in a plan ends with a commit; messages follow `type(scope): summary`.

## 3. Configuration (`xolotl/platform/config.py`, prefix `XOLOTL_`)

| Key | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl` | runtime connection as the application role |
| `DATABASE_ADMIN_URL` | `postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl` | owner role for migrations and bootstrap |
| `ENV` | `dev` | `dev`, `test`, `prod`; `prod` refuses default secrets |
| `JWT_SECRET` | dev value, min 32 chars | HS256 signing key for staff tokens |
| `JWT_ISSUER` | `xolotl` | `iss` claim |
| `ACCESS_TOKEN_TTL_MINUTES` | `480` | staff access token lifetime |
| `MFA_TOKEN_TTL_MINUTES` | `5` | lifetime of the intermediate token between password and TOTP |
| `MASTER_KEY` | dev value (base64, 32 bytes) | wraps tenant DEKs in the local key provider (1b); `ENV=prod` refuses the dev value; its reference is stored with every key (§8.1) |
| `OBJECT_STORE` | `local` | `local` is the only adapter; a later adapter widens the choice (1b) |
| `OBJECT_STORE_ROOT` | `./data/local/objects` | local adapter root (1b) |
| `WORKER_TICK_SECONDS` | `1.0` | dispatcher and job runner poll interval (1c) |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | outbound webhook timeout (1c) |
| `PORT` | `8000` (also reads `PORT`) | |
| `STATIC_DIR` | `src/xolotl/static` | built console, served at `/` when present (1d) |
| `LOG_LEVEL` | `INFO` | JSON logs with the redaction filter |

## 4. Tenancy: session contract, roles, RLS

### 4.1 Actor and tenant context

```python
class ActorType(StrEnum): STAFF = "STAFF"; API_CLIENT = "API_CLIENT"; SYSTEM = "SYSTEM"; PLATFORM_OPERATOR = "PLATFORM_OPERATOR"
class Role(StrEnum): ANALYST = "ANALYST"; SENIOR_ANALYST = "SENIOR_ANALYST"; MLRO = "MLRO"; AUDITOR_RO = "AUDITOR_RO"; ADMIN = "ADMIN"

@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID | None          # None only for PLATFORM_OPERATOR
    actor_type: ActorType
    actor_id: str                   # staff_user.id, api_key.id, job id, or "cli:<user>"
    roles: frozenset[Role] = frozenset()
    request_id: str = ""

current_context: ContextVar[TenantContext | None]
@contextmanager
def bind_context(ctx: TenantContext) -> Iterator[None]: ...
def require_context() -> TenantContext: ...   # raises ContextMissingError
```

### 4.2 Database session

`xolotl/platform/db.py` exposes `get_engine()` (app role, `pool_pre_ping=True`), `get_admin_engine()`, `Base`, `tenant_session()` context manager and `get_session()` FastAPI dependency. On every transaction start (`after_begin` event) the session runs, with bind parameters:

```sql
SELECT set_config('app.tenant_id',  :tenant_id,  true),
       set_config('app.actor_type', :actor_type, true),
       set_config('app.actor_id',   :actor_id,   true),
       set_config('app.roles',      :roles,      true);   -- roles comma-joined, e.g. ",ADMIN,MLRO,"
```

If no context is bound the session raises `ContextMissingError` before any statement runs. `PLATFORM_OPERATOR` contexts set `app.tenant_id` to `''` (empty), which no tenant policy matches.

### 4.3 Table conventions and DDL helpers (`xolotl/platform/tenancy/ddl.py`)

- `TenantTableMixin`: `tenant_id: Mapped[UUID]` (first PK column), `id: Mapped[UUID]` (uuid7 default); models build `__table_args__` with `tenant_table_args(table, *extra)`. The primary key `(tenant_id, id)` is the target of composite foreign keys, so no `UNIQUE (tenant_id, id)` is declared beside it (Postgres discards a unique constraint that duplicates the primary key). The rule: a table whose primary key is `(tenant_id, id)` declares no extra unique for it; a table whose primary key is anything else (a partitioned table, for instance) declares its own unique constraint when it must be a composite-FK target.
- `tenant_fk(column: str, target: str, *, target_column: str = "id", ondelete: str | None = None) -> ForeignKeyConstraint`: builds `FOREIGN KEY (tenant_id, <column>) REFERENCES <target> (tenant_id, <target_column>)`.
- Alembic helpers: `enable_rls(table, *, predicate=TENANT_PREDICATE, grant=DML_PRIVILEGES)` runs `ALTER TABLE t ENABLE ROW LEVEL SECURITY; ALTER TABLE t FORCE ROW LEVEL SECURITY; CREATE POLICY tenant_isolation ON t AS PERMISSIVE FOR ALL USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid); GRANT SELECT, INSERT, UPDATE, DELETE ON t TO xolotl_app;` (the `NULLIF` is required: a platform-operator context sets `app.tenant_id` to `''`, and casting `''` to `uuid` raises). Append-only tables pass `grant=APPEND_ONLY_PRIVILEGES` (`SELECT, INSERT`) and are listed in `APPEND_ONLY_TABLES` (1a: `audit_event`); `audit_event` partitions get no grant at all. `add_restrictive_policy(table, name, using_sql, check_sql)` creates `AS RESTRICTIVE` policies only. `grant_shared(table)` grants SELECT (and INSERT/UPDATE/DELETE where the registry says writable) on shared tables.
- `SHARED_TABLES` registry (`xolotl/platform/tenancy/registry.py`): the frozenset of tables that are not tenant-isolated. They carry no row security and may still reference tenants (`api_key_lookup.tenant_id` routes a key prefix to its tenant before any tenant context exists). The registry, not the presence of a `tenant_id` column, is the isolation harness's authority. 1a registers `alembic_version` and `api_key_lookup`. Later: `policy_pack`, `policy_pack_version`, `unit_value`, `holiday`, `list_source`, …
- The `tenant` table is special: its tenant column is `id`. Its single permissive policy is `USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid OR current_setting('app.actor_type', true) = 'PLATFORM_OPERATOR')`.

### 4.4 Isolation harness (`tests/isolation/`)

1. Structural: `SHARED_TABLES` is the authority. Every table it lists must exist and carry no row security and no policies. Every other base table in `public` must have `tenant_id` (`id` for `tenant`), `relrowsecurity` and `relforcerowsecurity`, exactly one permissive policy named `tenant_isolation` with the expressions above (`USING` equal to `WITH CHECK`), and only restrictive policies besides it; the app role must hold exactly `SELECT, INSERT, UPDATE, DELETE` on it (`SELECT, INSERT` on the `APPEND_ONLY_TABLES`) and nothing on any partition. Fail with the table name.
2. Behavioural: every tenant table must have a registered factory (`register_isolation_factory(table, fn)` in `xolotl/platform/tenancy/testing.py`); the harness creates two tenants, inserts one row per table as tenant A, and asserts tenant B reads zero rows, cannot update or delete A's row (on an append-only table the app role is refused UPDATE and DELETE outright), and cannot insert a row carrying A's `tenant_id`. A tenant table without a factory fails the suite.
3. Role: the test engine connects as `xolotl_app`, which must be neither superuser nor `BYPASSRLS`, must own no table and must not be a member of any table owner's role (`pg_has_role(..., 'MEMBER')`).

### 4.5 Database roles and bootstrap

`xolotl db bootstrap` (admin URL) creates role `xolotl_app` if missing (`LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB`), sets `LOGIN` and the password from `DATABASE_URL`, grants `CONNECT` and `USAGE`, then verifies the role (`migrate.verify_app_role`): neither superuser nor `BYPASSRLS`, and not a member of the owner role. On PG16 and later only a superuser may change `SUPERUSER` and only a `BYPASSRLS` role may change `BYPASSRLS`, so bootstrap reads the role's attributes first and names one in `ALTER ROLE` only to remove it: a role that already satisfies the invariants is altered no further than `LOGIN` and its password, and the owner needs `CREATEROLE`, not `SUPERUSER`, which is what managed Postgres (RDS, Cloud SQL) grants. On PG16 and later that owner also needs `ADMIN OPTION` on `xolotl_app` to set the password. It has it when it created the role (bootstrap's first run creates it); for a role created by someone else, they or a superuser first run `GRANT xolotl_app TO <owner> WITH ADMIN OPTION`. When the database refuses one of these role ALTERs, bootstrap raises `BootstrapError`, naming the role and the SQLSTATE but never the statement, which can carry the password (`from None` keeps it out of the traceback too); when verification fails it raises `RoleBypassError` (a `RuntimeError`) naming the attribute. Either way nothing changes, and `xolotl db bootstrap` prints the error as one `error:` line and exits 1. Migrations run as the owner, and every `db upgrade` (so every `serve`) then runs `ensure_partitions(months_ahead=3)` (§8.3), which logs and skips a month it cannot create yet instead of failing. At runtime `db.assert_app_role_safe(engine)` refuses an app connection whose role is a superuser or has `BYPASSRLS`: `serve` checks it after migrating and exits 1, `/readyz` answers 503 `NOT_READY` (§7). The test harness runs the same bootstrap against the testcontainer, and again through a `CREATEROLE` owner that is not a superuser.

## 5. Core tables (1a)

All tenant tables carry `tenant_id uuid NOT NULL`, `id uuid NOT NULL` (uuid7) and `PRIMARY KEY (tenant_id, id)`, which is the composite-FK target (no separate `UNIQUE (tenant_id, id)`; see the rule in §4.3), with RLS as in §4.3. Timestamps are `timestamptz`. Enum columns are `String(32)`.

```sql
CREATE TABLE tenant (
  id uuid PRIMARY KEY, slug text UNIQUE NOT NULL,              -- ^[a-z0-9][a-z0-9-]{2,39}$
  legal_name text NOT NULL, country_code char(2) NOT NULL, cell text NOT NULL DEFAULT 'mx-1',
  env text NOT NULL,                                           -- SANDBOX, PROD (program spec addendum a)
  status text NOT NULL,                                        -- ONBOARDING, ACTIVE, SUSPENDED, OFFBOARDED
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE api_key_lookup (                                  -- shared (in SHARED_TABLES, no RLS): resolves a key prefix to its tenant before any RLS context exists
  key_prefix text PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id));

CREATE TABLE staff_user (
  tenant_id uuid NOT NULL REFERENCES tenant(id), id uuid NOT NULL,
  email text NOT NULL, display_name text NOT NULL, roles text[] NOT NULL,
  status text NOT NULL,                                        -- ACTIVE, DISABLED
  must_change_password boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, email));

CREATE TABLE staff_credential (
  tenant_id uuid NOT NULL, id uuid NOT NULL, staff_user_id uuid NOT NULL,
  password_hash text NOT NULL,                                 -- argon2id
  totp_secret bytea,                                           -- EncryptedStr ciphertext (migration 0004 in 1b); plaintext base32 in 1a
  totp_enabled boolean NOT NULL DEFAULT false,
  failed_attempts int NOT NULL DEFAULT 0, locked_until timestamptz,
  password_changed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, staff_user_id),
  FOREIGN KEY (tenant_id, staff_user_id) REFERENCES staff_user (tenant_id, id));

CREATE TABLE api_key (
  tenant_id uuid NOT NULL, id uuid NOT NULL,
  name text NOT NULL, key_prefix text NOT NULL,                -- first 15 characters of the key, e.g. xk_sbx_ab12cd34
  key_hash bytea NOT NULL,                                     -- sha256 of the full key
  scopes text[] NOT NULL, env text NOT NULL,                   -- SANDBOX, PROD
  status text NOT NULL,                                        -- ACTIVE, REVOKED
  created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz, revoked_at timestamptz,
  PRIMARY KEY (tenant_id, id), UNIQUE (key_prefix),
  FOREIGN KEY (tenant_id, created_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE idempotency_key (
  tenant_id uuid NOT NULL, actor_id text NOT NULL, key text NOT NULL,
  request_hash bytea NOT NULL,
  status_code int NOT NULL,                                    -- 0 while the key's first request runs (a reservation)
  response_body jsonb NOT NULL,                                -- {} while reserved, then {status, media_type, body, raw}
  created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, actor_id, key));                     -- the key's scope (§7)
CREATE INDEX ix_idempotency_key_tenant_id_expires_at ON idempotency_key (tenant_id, expires_at);   -- the purge (§7)
```

The stored record keeps the endpoint's 2xx outcome: `status`, `media_type` (the `Content-Type`), `body` (the parsed JSON body when the media type is JSON) and `raw` (the base64 of any other non-empty body); `body` and `raw` are both null for an empty body. A replay rebuilds exactly that response with `Idempotent-Replay: true`; other headers are not replayed.

Key format: `xk_<sbx|prd>_<8 hex>_<32 hex>` (48 characters); `key_prefix` = the first 15 characters `xk_<env>_<8 hex>`; `api_key.env` must equal the tenant's `env`; lookup by prefix through `api_key_lookup`, then the tenant-scoped row, verified by constant-time hash comparison.

## 6. Authentication and authorization (1a)

### 6.1 Staff login

- `POST /v1/auth/login` `{tenant_slug, email, password}` → if TOTP enabled `{"mfa_required": true, "mfa_token": "<jwt, 5 min, purpose=mfa>"}` else `{"access_token", "token_type": "Bearer", "expires_in", "must_change_password"}`.
- `POST /v1/auth/login/totp` `{mfa_token, code}` → access token.
- `POST /v1/auth/password` (Bearer staff) `{current_password, new_password}`; new password ≥ 12 chars; clears `must_change_password` and answers `200 {"access_token", "token_type": "Bearer", "expires_in", "must_change_password": false}`: a fresh access token, because the change ends every session issued before it. A staff token whose `iat` is earlier, in whole seconds, than `staff_credential.password_changed_at` is refused with 401, the token that made the change included. The same rule refuses an MFA token at `POST /v1/auth/login/totp`: a password step taken with the old password cannot be finished after the change.
- `POST /v1/auth/totp/enroll` (Bearer staff) → `{secret, otpauth_uri}`; `POST /v1/auth/totp/confirm` `{code}` → enables TOTP.
- `GET /v1/auth/me` → `{id, tenant_id, tenant_slug, email, display_name, roles, must_change_password, totp_enabled}`.
- Lockout: after 10 consecutive failures `locked_until = now() + 15 min`; failures and successes are audited (`auth.login_failed`, `auth.login_succeeded`, `auth.locked`). Responses never reveal whether the email exists: every failure is the same 401, and every early exit (unknown tenant or email, a disabled or locked account, and the TOTP step's failures) spends one argon2id verification against a dummy hash, so it takes about as long as a wrong password.
- Access token claims: `iss`, `sub` (staff_user.id), `tenant_id`, `tenant_slug`, `roles`, `actor_type: "STAFF"`, `iat`, `exp`, `jti`. HS256 with `JWT_SECRET`.

### 6.2 API keys

- `POST /v1/api-keys` (ADMIN) `{name, scopes, env}` → `{id, key}` with the full key shown once. `GET /v1/api-keys`, `POST /v1/api-keys/{id}/revoke`.
- Requests authenticate with `Authorization: Bearer xk_...`; the dependency resolves the key, checks `status = ACTIVE`, binds `TenantContext(actor_type=API_CLIENT, actor_id=key.id)`, updates `last_used_at` at most once per minute.
- Scope grammar `<area>:<read|write>`; `require_access(scope="tenants:read")` returns 403 problem `SCOPE_MISSING` to a key without that scope (§6.3). A request counts as a write when the route's scope ends in `:write` or its method is not `GET`, `HEAD` or `OPTIONS`, unless the route passes `write=` explicitly (a read-only `POST` passes `write=False`). 1a exposes `GET /v1/tenants/me` (scope `tenants:read`) as the hello endpoint and `POST /v1/ping` (scope `tenants:write`) to exercise idempotency.

### 6.3 Roles

`require_access(*, roles=(), scope=None, write: bool | None = None)` is the one access dependency: staff need one of `roles` when roles are given, API keys need `scope` (a route without a scope is staff-only), and `AUDITOR_RO` may never write, with the write inferred as in §6.2. `ADMIN` manages staff, keys and webhooks; `AUDITOR_RO` is read-only everywhere (any write returns 403 `ROLE_FORBIDDEN`) except for its own account: the self-service routes under `/v1/auth/*` (password change, TOTP enrolment and confirmation, `me`) use `current_staff`, which admits any staff member, a pending password change included.

### 6.4 Staff management

`POST /v1/staff` (ADMIN) `{email, display_name, roles}` → creates the user with a generated initial password returned once and `must_change_password = true`. `GET /v1/staff`, `POST /v1/staff/{id}/disable`.

## 7. API conventions (1a)

- Prefix `/v1`; OpenAPI at `/v1/openapi.json`, docs at `/v1/docs`; operation ids are function names.
- Errors: `application/problem+json` with `type` (`https://xolotl.dev/problems/<code>`), `title`, `status`, `detail`, `instance`, `code` (stable UPPER_SNAKE), and `errors[]` (`{loc, msg, type}`) for validation. Mapping: `DomainError` 409 `CONFLICT`, `NotFoundError` 404 `NOT_FOUND`, `ForbiddenError` 403, `UnauthorizedError` 401 `UNAUTHENTICATED` with `WWW-Authenticate: Bearer`, `RequestValidationError` 422 `VALIDATION_FAILED`, `IdempotencyKeyRequiredError` 400 `IDEMPOTENCY_KEY_REQUIRED`, `IdempotencyConflictError` 422 `IDEMPOTENCY_KEY_REUSED`, `IdempotencyInProgressError` 409 `IDEMPOTENCY_IN_PROGRESS` (the key's first request is still running), unexpected 500 `INTERNAL`.
- Idempotency: every `POST` under `/v1` except `/v1/auth/*` requires `Idempotency-Key` (1–128 chars) or returns 400 `IDEMPOTENCY_KEY_REQUIRED`. The key is scoped to `(tenant_id, actor_id, key)`; `request_hash = sha256(method, path, body)`. A replay with the same hash returns the stored status and body with `Idempotent-Replay: true`; a different hash returns 422; while the first request still runs a retry gets 409 `IDEMPOTENCY_IN_PROGRESS`. Entries expire after 24 hours; every reservation first deletes its tenant's expired entries (index `(tenant_id, expires_at)`), and the 1c job purges the rest.
- Pagination: list endpoints accept `limit` (1–200, default 50) and `cursor`; respond `{items, next_cursor}`; the cursor is a URL-safe base64 of `created_at|id` for keyset ordering `(created_at DESC, id DESC)`.
- Health: `GET /healthz` → `{"status":"ok"}`; `GET /readyz` → queries as the app role and answers 503 `NOT_READY` when the database is unreachable or the role can bypass row-level security (superuser or `BYPASSRLS`, §4.5).
- Request id: `X-Request-Id` honoured or generated; logged and returned.
- Logging: JSON lines via stdlib logging; the `RedactionFilter` masks values of keys named like `password`, `token`, `secret`, `authorization`, `curp`, `rfc`, `clabe`, `email`, and any field tagged PII by Pydantic metadata `json_schema_extra={"pii": True}`.

## 8. Data protection (1b)

### 8.1 Tables

```sql
CREATE TABLE tenant_key (
  tenant_id uuid NOT NULL REFERENCES tenant(id), id uuid NOT NULL,
  purpose text NOT NULL,                                       -- DATA, INDEX, BIOMETRIC
  version int NOT NULL, wrapped_dek bytea,                     -- NULL after destroy
  kek_ref text NOT NULL,                                       -- 'local:v1:<16 hex>' (§8.2) or 'kms:<arn>'; a bare 'local:v1' is a row from before the fingerprint
  created_at timestamptz NOT NULL DEFAULT now(), retired_at timestamptz, destroyed_at timestamptz,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, purpose, version));
```

### 8.2 Interfaces

```python
class KeyPurpose(StrEnum): DATA = "DATA"; INDEX = "INDEX"; BIOMETRIC = "BIOMETRIC"

class KeyProvider(Protocol):             # every method works in the caller's session when given one, else in a short session of its own
    def create_tenant_keys(self, tenant_id: UUID, *, session: Session | None = None) -> None: ...
    def data_key(self, tenant_id: UUID, purpose: KeyPurpose, version: int | None = None, *, session: Session | None = None) -> tuple[int, bytes]: ...
    def index_key(self, tenant_id: UUID, *, session: Session | None = None) -> bytes: ...
    def rotate(self, tenant_id: UUID, purpose: KeyPurpose, *, session: Session | None = None) -> int: ...
    def destroy(self, tenant_id: UUID, *, session: Session | None = None) -> None: ...

class LocalKeyProvider(KeyProvider):     # the only adapter; a KMS adapter replaces it behind the port
    def __init__(self, kek: bytes, session_factory: SessionFactory, *, cache_ttl: timedelta = timedelta(minutes=5), clock_fn: Callable[[], float] = time.monotonic) -> None
    kek_ref: str                         # property: kek_ref_for(kek), see below
def get_key_provider() -> KeyProvider; def set_key_provider(provider: KeyProvider | None) -> None   # installed once per process by the composition root (build_app, tenant create, keys)

@dataclass(frozen=True, slots=True)
class Encrypted[T]:                      # the value EncryptedStr columns hold and return
    ciphertext: bytes; tenant_id: UUID; purpose: KeyPurpose; version: int   # tenant and purpose from the current context at load time, version from the header
    def reveal(self, *, session: Session | None = None) -> T: ...          # decrypts with the key of (tenant_id, purpose, version); records nothing
    def __repr__(self) -> str: return "Encrypted(***)"

def encrypt(value: str, *, tenant_id: UUID, purpose: KeyPurpose = KeyPurpose.DATA, session: Session | None = None) -> Encrypted[str]
def reveal_for_response(session: Session, value: Encrypted[str], *, entity_type: str, entity_id: str, field: str) -> str
def blind_index(kind: str, value: str, *, tenant_id: UUID, session: Session | None = None) -> bytes   # HMAC-SHA256(index_key, f"{kind}:{normalize_identifier(kind, value)}")
def normalize_identifier(kind: str, value: str) -> str                                              # per-kind rules, tabled in platform/crypto/blind_index.py
```

- Ciphertext layout: `version (2 bytes big-endian) || nonce (12) || AES-256-GCM ciphertext+tag (16)`; AAD = `tenant_id.bytes + purpose.encode()`; a value cannot be decrypted under another tenant's key or purpose. The version only selects the key and is not authenticated by itself (another version is another key, and the tag check fails). Keys are exactly 32 bytes, and versions run from 0 to 65535, the two bytes of the header; `rotate` refuses a version beyond.
- `LocalKeyProvider`: KEK = `MASTER_KEY`; `wrapped_dek = AES-GCM(KEK, DEK, aad=tenant_id|purpose|version)` with its 12-byte nonce in front; DEKs cached in memory for 5 minutes (on a monotonic clock) keyed by `(tenant_id, purpose, version)`; `destroy` sets `wrapped_dek = NULL`, `destroyed_at = now()` on every version of every purpose, and the process that committed it evicts its cache.
- `kek_ref` is `local:v1:` and the first 8 bytes, in hex, of `HMAC-SHA256(MASTER_KEY, "xolotl:kek-ref:v1")`: not secret, not reversible and not a bare digest of the key. A read compares the row's reference with the provider's before it unwraps (a mismatch is `KekMismatchError`, 500 `KEK_MISMATCH`, a kind of `DecryptionError`). A write (`rotate`, or `create_tenant_keys` when it has a purpose to add) first requires that the tenant has no live key yet, which makes the writer's reference the tenant's, or that one of its live keys carries the provider's reference; so a process started with another `MASTER_KEY` cannot leave a version nobody else can read, and a rotation under the right key mends a chain broken that way. `destroy` needs no key. Rows from before the fingerprint hold the bare `local:v1`: a read relies on the authenticated unwrap, and a write proves the key by unwrapping one of them.
- `EncryptedStr` is a SQLAlchemy `TypeDecorator(LargeBinary)` that holds `Encrypted[str]` values of purpose DATA. Plain text assigned to an attribute is sealed in a `before_flush` hook under the tenant of its own row (a row without `tenant_id` yet, or of another tenant than a tenant-bound session's, is refused with `TenantScopeError`); text bound outside a flush (a Core `insert().values(...)`) is sealed in `process_bind_param` under the context's tenant; reading binds each value to the current context's tenant, so a session needs `bind_context`, not only `apply_context`. Migrations 0003 and 0004 add `tenant_key` and convert `staff_credential.totp_secret` to the encrypted form (§8.5).
- `reveal_for_response(session, value, ...)` records `VIEW_PII` (the entity in the event's `entity_type` and `entity_id`, `detail={"field": field}`) and then decrypts through the session: nothing is decrypted if the event cannot be recorded, a value of another tenant than the session's is refused, and a failed decryption rolls the event back with the caller's transaction. API detail views use it; list views never reveal; internal processing (TOTP verification) uses `reveal()` and records nothing.
- Rotation is per purpose (`DATA`, `BIOMETRIC`). `INDEX` keys are not rotated in 1b (`DomainError`, 409 `CONFLICT`): every blind index would need recomputing. Nothing is re-encrypted; an old version is retired, not destroyed, and stays readable. `destroy` is final: `create_tenant_keys` then refuses the tenant (`KeyDestroyedError`, 410 `KEY_DESTROYED`). The provider's `destroy` of a tenant with no keys does nothing, and the tenants service refuses it (`KeyNotFoundError`, nothing audited) rather than record a destruction that did not happen. `rotate_tenant_key` and `destroy_tenant_keys` (tenants service) audit `tenant.key_rotated` (`detail={"purpose", "version"}`) and `tenant.keys_destroyed` (no detail, recorded before the destruction) in the caller's transaction; the CLI is `xolotl keys rotate|destroy` (§10).
- The redaction test from §7 asserts no revealed value appears in logs during the suite: `Encrypted` is masked by class, `master_key` is a sensitive key, and the `sqlalchemy.engine` logger is held at INFO or quieter because its DEBUG rows print every column of every row it reads, ciphertexts among them.

### 8.3 Audit (built in plan 1a)

```sql
CREATE TABLE audit_event (
  tenant_id uuid NOT NULL, at timestamptz NOT NULL, id uuid NOT NULL, seq bigint NOT NULL,
  actor_type text NOT NULL, actor_id text NOT NULL, action text NOT NULL,
  entity_type text, entity_id text, detail jsonb,
  prev_hash bytea NOT NULL, hash bytea NOT NULL,
  PRIMARY KEY (tenant_id, at, id)
) PARTITION BY RANGE (at);
-- audit_event_default partition plus one partition per month; append-only trigger rejects UPDATE/DELETE/TRUNCATE
```

```python
class AuditEvent(BaseModel):
    action: str; entity_type: str | None = None; entity_id: str | None = None
    detail: dict[str, JsonValue] | None = None   # rejects floats at any depth: JSONB numerics do not round-trip them

def digest(row: AuditEventRow) -> bytes
    # sha256(canonical_json([str(tenant_id), seq, at in UTC ISO-8601, actor_type, actor_id, action, entity_type,
    #                        entity_id, detail, prev_hash.hex()])); canonical_json = sorted keys, compact, UTF-8
def record(session: Session, event: AuditEvent) -> AuditEventRow
    # takes pg_advisory_xact_lock(hashtext(tenant_id::text)::bigint), held to the end of the caller's transaction
    # (READ COMMITTED assumed), reads the tenant's last seq and hash, writes seq+1, prev_hash and hash = digest(row)
def verify_chain(session: Session, tenant_id: UUID | None = None) -> ChainVerification
    # fail-closed: verifies the tenant the session is bound to; raises ContextMissingError when the session has no
    # tenant or tenant_id names another one; walks ORDER BY seq and reports the first break
def ensure_partitions(conn: Connection, *, months_ahead: int = 3, start: date | None = None) -> PartitionReport
    # UTC month bounds; run by migration 0001 and by every `db upgrade`, so by every `serve`. Callers take turns on
    # pg_advisory_xact_lock(hashtext('audit_event_partitions')::bigint). Each month is created in its own savepoint with
    # lock_timeout = '5s'; a month refused because its rows already sit in audit_event_default (23514), because a table
    # of that name exists (42P07) or because audit_event stayed locked (55P03) is logged and skipped, and the next month
    # is tried; any other error propagates. PartitionReport(created: list[str], skipped: dict[str, str]) names the
    # partitions created and maps each skipped one to its SQLSTATE.
```

The chain lives in `xolotl/platform/audit/chain.py`; `xolotl/platform/audit/__init__.py` re-exports its names. The app role holds only `SELECT, INSERT` on `audit_event`. Verification detects an edited row, a row removed from or inserted into the middle of the chain, and a broken `prev_hash` link. It cannot detect rows removed from the end (tail truncation) or a suffix rewritten and re-hashed, because the head is not anchored outside the database; anchoring the latest `(seq, hash)` in a signed or write-once checkpoint is a later requirement.

Actions used by the kernel: `auth.login_succeeded`, `auth.login_failed`, `auth.locked`, `auth.password_changed`, `auth.totp_enrolled`, `staff.created`, `staff.disabled`, `api_key.created`, `api_key.revoked`, `webhook_endpoint.created`, `webhook_endpoint.deleted`, `tenant.created`, `tenant.keys_created`, `tenant.key_rotated`, `tenant.keys_destroyed`, `VIEW_PII`. Endpoint `GET /v1/audit` (cursor list, MLRO/ADMIN/AUDITOR_RO) and `GET /v1/audit/verify`.

### 8.4 Object store

```python
@dataclass(frozen=True, slots=True) class ObjectRef: tenant_id: UUID; key: str; sha256: bytes; size: int; content_type: str   # sha256 is the raw 32-byte digest
class ObjectStore(Protocol):
    def put(self, tenant_id: UUID, key: str, data: bytes, *, content_type: str, metadata: Mapping[str, str] = NO_METADATA) -> ObjectRef: ...
    def get(self, ref: ObjectRef) -> bytes: ...
    def delete(self, ref: ObjectRef) -> None: ...
    def list(self, tenant_id: UUID, prefix: str) -> Iterator[ObjectRef]: ...
```

Keys are always stored under `<tenant_id>/<key>`; `get` recomputes and verifies the sha256 (`ObjectIntegrityError`, 500 `OBJECT_CORRUPT`), and a missing object is `NotFoundError`. The port (`platform/storage/port.py`, re-exported by `platform/storage/__init__.py`) validates once, for every adapter and before anything is written: keys (1 to 512 characters in `/`-separated segments, none empty, `.`, `..` or ending in `.meta.json`, and no backslash, NUL or lone surrogate; `InvalidObjectKeyError`, 422), list prefixes, content types and metadata (`InvalidObjectMetadataError`, 422: at most 32 entries, lowercase names, printable values, 8 KiB in all). `LocalFsObjectStore(root)` is the only adapter: it writes `<root>/<tenant_id>/<key>` plus `<key>.meta.json` atomically (staged in `<root>/.tmp`, the bytes first and the record last), refuses symlinks under the root and a tenant id that is not a `UUID`, and does not encrypt contents. `app.py` selects it from `OBJECT_STORE`. An S3 adapter is a later plan: the MVP is one application container, so there is no `S3ObjectStore`, no `boto3` or `moto` dependency and no `S3_*` setting, and the port is what a later adapter implements.

### 8.5 Errata from plan 1b

The sections above describe what was built. These are the places where it departs from the earlier text of this section, and why.

- **The provider takes a `SessionFactory`.** `LocalKeyProvider` is built with one, and every method takes an optional `session=`, instead of a `Session` as first argument: the SQLAlchemy type decorator and `Encrypted.reveal()` run outside any caller session, so on a cache miss the provider opens its own short session (a system context for the tenant). Callers that hold a session (tenant creation, rotation, the login steps, the migrations) pass it and work in its transaction, which also lets them see keys created in it.
- **`Encrypted` carries its tenant and purpose.** `process_result_value` has no session, so a loaded value captures `tenant_id` and `purpose` from the current context and the key version from its header; `reveal()` then needs no arguments. That is why a session must have its context *bound* (`bind_context`, which `tenant_session` does) and not only applied (`apply_context`) to load one.
- **The TOTP conversion is two migrations.** `0003_tenant_key` creates the table; `0004_encrypt_totp` adds `totp_secret_enc`, creates the keys a tenant lacks, seals each secret under its tenant's DATA key (one `tenant_session` per tenant on the owner's connection, inside the migration's transaction), then drops the plaintext column and renames the new one in its place. It logs instead of auditing the keys it creates, skips a tenant whose keys were destroyed (its plaintext secrets are dropped: destruction is final) and stops, rolling everything back, if any secret could not be sealed. A downgrade opens each secret with the version in its header. It is not safe for a rolling deploy (code from before it writes plaintext into the column the new code reads as ciphertext), and it needs `MASTER_KEY` in both directions. Because it reaches the key table through `LocalKeyProvider`, 0004 depends on the live `TenantKey` model, which an Alembic migration should not: a migration freezes the table definitions it needs. The next change to `tenant_key` must first freeze them inside 0004 (the upgrade tests from 0003 in `test_migration_0004.py` fail otherwise). 0004 is also the template for the later conversions of plaintext columns to encrypted ones (the CURP and RFC columns): such a conversion holds an ACCESS EXCLUSIVE lock for its whole transaction (`ADD COLUMN` already takes it, and the final table rewrite adds negligible time: 25 ms on 20,000 rows in an 8.8 s run), and the rewrite must come after the drop of the plaintext column, since a rewrite before it leaves the plaintext in the live heap pages.
- **INDEX keys are not rotatable in 1b.** Blind indexes are deterministic tags, so a new INDEX key would invalidate every stored tag until all of them were recomputed; `rotate(INDEX)` is refused. DATA and BIOMETRIC rotate, per purpose, without re-encrypting anything (there is no re-encryption job in 1b).
- **`kek_ref` is checked.** It was written as `local:v1` and never read, so a process with another `MASTER_KEY` could create a version nobody else could read. It is now a fingerprint of the master key, verified on reads and before writes (§8.2).
- **What the authenticated data does not bind.** The AAD is the tenant and the purpose (the version selects the key; a wrapped key's AAD adds the version). It binds neither the row nor the column, so someone with write access to the database can move a ciphertext between rows or columns of one tenant and it still opens. A ciphertext is the plaintext's UTF-8 length plus 30 bytes, so its length gives the plaintext's away (an RFC of 12 or 13 characters tells a persona moral from a persona física): pad at the application layer where that matters. Comparing an encrypted column compiles and never matches (use a blind index), and FastAPI's encoder would serialise an `Encrypted` returned from a route without a response model (its ciphertext and metadata, never plaintext), which the API conventions already rule out.
- **Shredding reaches the live database.** `destroy` is complete in the database at once and in the process that committed it; other processes keep serving a cached key for up to the cache TTL (5 minutes; TOTP checks read the database every time). It does not reach backups, archived WAL, replicas or pages VACUUM has not cleaned: while `MASTER_KEY` is unchanged, restoring any of them recovers the wrapped keys. Dropping a plaintext column likewise leaves the old values in copies taken before the conversion, for 0004 and for every later one (the migration rewrites the table, so no live page keeps them); they last until the retention of those copies expires. The TTL runs on a monotonic clock, which a suspended machine can still stretch by the length of the suspension.
- **A KMS adapter needs the destroy lock behind the port.** `destroy_tenant_keys` (tenants service) selects and locks the tenant's `tenant_key` rows itself, to refuse a tenant with no keys and to take the key locks before the audit lock, the order a concurrent rotation uses. An adapter with no such rows would make every destroy fail with `KeyNotFoundError`, so that lock and check has to move behind the `KeyProvider` port first.
- **No algorithm byte.** The header holds only the key version, so a change of algorithm has to travel in the key rows' metadata (`kek_ref` and the like), and the two header bytes cap versions at 65535.
- **Audit shapes.** `tenant.keys_created` (on creation, `detail={"purposes": [...]}`) joins the actions of §8.3. `VIEW_PII` keeps the entity in `entity_type` and `entity_id` and only the field in `detail`. The key events name ids and numbers (tenant, purpose, version) and never key material.
- **Blind-index normalisation is per kind.** "Upper, strip spaces/punctuation" became a table (`platform/crypto/blind_index.py`): `email` lower-cases and removes whitespace only (`+`, `-`, `_` are part of an address), `phone` keeps digits, `name` keeps letters and accents, `rfc` keeps `Ñ` and `&`, and `curp`, `clabe` and other kinds keep `A-Z` and `0-9`. A value with nothing left has no tag (`ValueError`).
- **Object store.** Local only (§8.4); the S3 adapter and its settings, dependencies and moto tests were removed by the single-container decision.
- **Smaller things.** A 410 problem is titled "Gone". Keys are exactly 32 bytes. A failing Core statement that bound plain text keeps it in `StatementError.params` (the display is hidden by `hide_parameters`). An error tracker must not capture local variables, which would show plaintexts and ciphertexts (keys are masked as `SecretBytes`).

## 9. Events, jobs, workflows (1c)

### 9.1 Tables

```sql
CREATE TABLE outbox (
  seq bigserial, tenant_id uuid NOT NULL, id uuid NOT NULL,
  type text NOT NULL, key text NOT NULL, payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'PENDING',                      -- PENDING, PUBLISHED, DEAD
  attempts int NOT NULL DEFAULT 0, locked_until timestamptz, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
  PRIMARY KEY (tenant_id, id), UNIQUE (seq));

CREATE TABLE event_consumption (
  tenant_id uuid NOT NULL, consumer text NOT NULL, event_id uuid NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id, consumer, event_id));

CREATE TABLE webhook_endpoint (
  tenant_id uuid NOT NULL, id uuid NOT NULL, url text NOT NULL, secret bytea NOT NULL,   -- EncryptedStr
  event_types text[] NOT NULL, status text NOT NULL,           -- ACTIVE, DISABLED
  created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id));

CREATE TABLE webhook_delivery (
  tenant_id uuid NOT NULL, id uuid NOT NULL, endpoint_id uuid NOT NULL, event_id uuid NOT NULL,
  attempt int NOT NULL DEFAULT 0, status text NOT NULL,        -- PENDING, DELIVERED, DEAD
  response_code int, last_error text, next_attempt_at timestamptz, delivered_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, endpoint_id, event_id),
  FOREIGN KEY (tenant_id, endpoint_id) REFERENCES webhook_endpoint (tenant_id, id));

CREATE TABLE job (
  tenant_id uuid NOT NULL, id uuid NOT NULL, kind text NOT NULL, key text,
  payload jsonb NOT NULL, status text NOT NULL DEFAULT 'PENDING', -- PENDING, RUNNING, DONE, FAILED, DEAD
  attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL DEFAULT 8,
  run_after timestamptz NOT NULL DEFAULT now(), locked_until timestamptz, locked_by text, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  PRIMARY KEY (tenant_id, id));
CREATE UNIQUE INDEX job_dedup ON job (tenant_id, kind, key) WHERE key IS NOT NULL AND status IN ('PENDING','RUNNING');

CREATE TABLE job_schedule (
  tenant_id uuid NOT NULL, id uuid NOT NULL, name text NOT NULL, cron text NOT NULL, tz text NOT NULL,
  kind text NOT NULL, payload jsonb NOT NULL, enabled boolean NOT NULL DEFAULT true,
  next_run_at timestamptz NOT NULL, last_run_at timestamptz,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, name));

CREATE TABLE workflow_run (
  tenant_id uuid NOT NULL, id uuid NOT NULL, definition text NOT NULL, business_key text NOT NULL,
  status text NOT NULL,                                        -- RUNNING, WAITING, DONE, FAILED
  current_step text, state jsonb NOT NULL, wake_at timestamptz,
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, last_error text,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, definition, business_key));

CREATE TABLE workflow_step (
  tenant_id uuid NOT NULL, run_id uuid NOT NULL, step_id text NOT NULL, attempt int NOT NULL,
  status text NOT NULL,                                        -- RUNNING, DONE, FAILED
  output jsonb, error text, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  PRIMARY KEY (tenant_id, run_id, step_id, attempt),
  FOREIGN KEY (tenant_id, run_id) REFERENCES workflow_run (tenant_id, id));

CREATE TABLE workflow_signal (
  tenant_id uuid NOT NULL, id uuid NOT NULL, run_id uuid NOT NULL, name text NOT NULL, payload jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(), consumed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES workflow_run (tenant_id, id));

CREATE TABLE notification (
  tenant_id uuid NOT NULL, id uuid NOT NULL, recipient_user_id uuid, recipient_role text,
  template text NOT NULL, data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), read_at timestamptz,
  PRIMARY KEY (tenant_id, id));
```

### 9.2 Interfaces

```python
class Event(BaseModel):
    id: UUID; type: str; source: str; tenant_id: UUID; subject: str; time: datetime
    key: str; correlation_id: str | None = None; policy_snapshot_id: UUID | None = None; data: dict[str, JsonValue]

Handler = Callable[[Session, Event], None]
class EventBus(Protocol):
    def publish(self, session: Session, event: Event) -> None: ...                 # inserts outbox row in the caller's transaction
    def subscribe(self, event_type: str, handler: Handler, *, consumer: str) -> None: ...
    def dispatch_pending(self, *, batch: int = 200) -> int: ...                     # worker loop; returns rows handled
    def replay(self, *, since: datetime, types: Sequence[str], consumer: str, tenant_id: UUID | None = None) -> int: ...

class JobQueue(Protocol):
    def enqueue(self, session: Session, *, kind: str, payload: dict[str, JsonValue], run_after: datetime | None = None, key: str | None = None, max_attempts: int = 8) -> UUID: ...
    def claim(self, *, worker_id: str, kinds: Sequence[str] | None, limit: int, lease: timedelta) -> list[Job]: ...
    def complete(self, job_id: UUID, tenant_id: UUID) -> None: ...
    def fail(self, job_id: UUID, tenant_id: UUID, error: str, *, retry_at: datetime | None) -> None: ...
    def schedule(self, session: Session, *, name: str, cron: str, tz: str, kind: str, payload: dict[str, JsonValue]) -> UUID: ...

JobHandler = Callable[[Session, Job], None]
register_job_handler(kind: str, handler: JobHandler) -> None

class StepResult(BaseModel):
    state: dict[str, JsonValue]; done: bool = False
    wait_for_signal: str | None = None; wake_at: datetime | None = None; timed_out: bool = False
Step = Callable[[Session, "WorkflowRun", dict[str, JsonValue], "StepInput"], StepResult]   # StepInput carries signal payload or timeout flag
class WorkflowDefinition: name: str; steps: list[tuple[str, Step]]; timeout: timedelta | None

class WorkflowRunner(Protocol):
    def start(self, session: Session, definition: str, *, business_key: str, input: dict[str, JsonValue]) -> UUID: ...
    def signal(self, session: Session, run_id: UUID, name: str, payload: dict[str, JsonValue]) -> None: ...
    def advance(self, run_id: UUID, tenant_id: UUID) -> None: ...     # executes the next step as a job
    def get_run(self, session: Session, run_id: UUID) -> WorkflowRun: ...
```

- Dispatcher semantics (D8): claim `PENDING` rows with `created_at < now() - 1s` and `locked_until IS NULL OR locked_until < now()`, ordered by `seq`, `FOR UPDATE SKIP LOCKED`; a row whose key has an earlier unpublished row is skipped this cycle; each handler runs in its own transaction with the event's tenant context (`SYSTEM` actor); success inserts `event_consumption`; a handler that already has a consumption row is skipped; after `attempts >= 10` the row is `DEAD`.
- Webhooks: the subscriber `webhooks.fanout` (consumer `webhooks`) creates one `webhook_delivery` per active endpoint whose `event_types` contains the type or `*`, and enqueues job `webhook.deliver`. Delivery POSTs the event JSON with headers `X-Xolotl-Event-Id`, `X-Xolotl-Event-Type`, `X-Xolotl-Timestamp: <unix>`, `X-Xolotl-Signature: v1=<hex hmac_sha256(secret, f"{timestamp}.{body}")>`; 2xx → `DELIVERED`; otherwise retry with backoff `min(2^attempt minutes, 60 min)` until 24 hours, then `DEAD`. Endpoints and deliveries have endpoints: `POST/GET/DELETE /v1/webhooks/endpoints`, `GET /v1/webhooks/deliveries?status=`, `POST /v1/webhooks/deliveries/{id}/retry`.
- Job runner: `claim` uses `FOR UPDATE SKIP LOCKED` on `status = 'PENDING' AND run_after <= now()`, sets `RUNNING`, `locked_by`, `locked_until = now() + lease` (default 60 s), heartbeats every lease/2; the handler runs with the job's tenant context; on exception `fail()` schedules `retry_at = now() + min(2^attempts minutes, 60 min)` or marks `DEAD` at `max_attempts`; a reaper returns expired `RUNNING` leases to `PENDING`.
- Scheduler: every tick, enabled schedules with `next_run_at <= now()` enqueue their job with `key = f"{name}:{next_run_at.isoformat()}"` (so a crashed tick cannot double-enqueue) and advance `next_run_at` with `croniter` in `tz`.
- Workflow runner: `start` inserts the run with `status RUNNING` and enqueues `workflow.advance`; `advance` loads the run, runs the current step inside one transaction keyed `(run_id, step_id, attempt)`, persists the `workflow_step` row and the new state, then either enqueues the next step, sets `WAITING` with `wake_at` (and enqueues a timer job for `wake_at`), or finishes. `signal` stores the row and, if the run is waiting for that signal, enqueues `advance`; a second signal with the same name while not waiting is stored unconsumed and ignored by `advance`. A timer that fires while still waiting calls the step with `timed_out=True`.
- `xolotl worker` runs three loops (dispatcher, job runner, scheduler) with `WORKER_TICK_SECONDS`, graceful shutdown on SIGTERM.
- Kernel events: `tenant.created.v1`, `staff.created.v1`, `api_key.created.v1`, `api_key.revoked.v1`, `test.ping.v1` (used by tests and `POST /v1/ping`).

## 10. CLI (`xolotl`)

`serve [--reload]` (migrate, then uvicorn factory), `worker`, `db bootstrap`, `db upgrade`, `db reset --yes`, `tenant create --slug --legal-name --country --cell --admin-email` (prints the admin's one-time password), `tenant list`, `audit verify --tenant <slug>`, `keys rotate --tenant <slug> --purpose DATA|BIOMETRIC` and `keys destroy --tenant <slug> --yes` (1b; both audited, destroy irreversible and refused without `--yes`).

## 11. Console shell (1d)

Fork `~/code/sentinel/frontend` into `console/` keeping Vite, React 19, TypeScript, Tailwind, shadcn/ui, TanStack Query, react-router, openapi-fetch and openapi-typescript (`gen:api` against `http://localhost:8000/v1/openapi.json`). Replace: auth (`/login` with workspace slug, email, password, then TOTP step; `/setup-password` when `must_change_password`), API client error adapter for problem+json, `AppShell` with tenant name and roles. Add pages: API keys (list, create with one-time display, revoke), Webhook endpoints and deliveries (with retry and the dead-letter filter), Audit (list, verify), Security (TOTP enrolment), Staff (ADMIN). i18n with `i18next` and `react-i18next`, `es-MX` default and `en` secondary; every string keyed. The Dockerfile builds the console and serves it from `STATIC_DIR`.

## 12. Test matrix (exit criteria restated as suites)

| Suite | What it proves |
|---|---|
| `tests/isolation/` | §4.4 structural and behavioural isolation for every tenant table; app role has no BYPASSRLS and owns nothing |
| `tests/platform/test_context_session.py` | session refuses to start without context; GUCs are set per transaction; platform operator sees no tenant rows |
| `tests/platform/test_api_conventions.py` | problem+json shape for every mapped error; Idempotency-Key required, replayed, conflicting; cursor pagination order and bounds; request id echo; health and ready |
| `tests/modules/tenants/test_auth.py` | login, TOTP enrol/confirm/login, lockout after 10 failures, password change, token claims, expired and tampered tokens |
| `tests/modules/tenants/test_api_keys.py` | key create/list/revoke, prefix lookup, scope enforcement, revoked key rejected, cross-tenant key rejected |
| `tests/platform/test_crypto_primitives.py`, `test_key_provider.py`, `test_kek_ref.py`, `test_encrypted.py`, `test_blind_index.py`, `test_migration_0004.py`, `tests/modules/tenants/test_totp_keys.py` | round trip per tenant; cross-tenant decrypt fails; blind index equality; rotation keeps old versions readable (TOTP sign-in survives it); destroy makes rows unreadable (the TOTP step answers 410); `kek_ref` checked on reads and before writes; the TOTP migration both ways; no plaintext in logs |
| `tests/platform/test_audit.py` | per-tenant chain, verify detects a tampered row when the trigger is disabled, trigger blocks UPDATE/DELETE/TRUNCATE, partitions created |
| `tests/platform/test_object_store.py` | local put/get/list/delete, sha256 verification, tenant prefixing, key and metadata validation, path containment |
| `tests/platform/test_outbox.py` | publish inside a transaction, dispatch exactly-once under forced redelivery, per-key ordering, dead letter, replay |
| `tests/platform/test_webhooks.py` | fan-out, signature header verification, retry with backoff, DEAD after 24h, manual retry |
| `tests/platform/test_jobs.py` | claim with SKIP LOCKED, lease expiry reaper, retry then success, DEAD at max attempts, dedup key, cron schedule enqueues once per slot |
| `tests/platform/test_workflows.py` | two-step run survives worker restart mid-run; duplicate signal; timeout path; business_key uniqueness |
| `tests/test_cli.py` | tenant create prints a password once; audit verify; db bootstrap idempotent |
| `console` | `npm run typecheck`, `npm run lint`, `npm run build`; Playwright smoke: login with TOTP, create an API key, see it masked |

## 13. Exit criteria (from the program spec, SP-01 row)

`make ci` green; Docker image builds and serves the console at `/`; isolation harness passes for every table; encrypted field round-trips per tenant and fails across tenants; destroying a tenant key makes its rows unreadable; a sample event is dispatched exactly once across a forced redelivery and reaches a webhook with a valid signature; a failing webhook retries and lands in the dead-letter view; a job fails twice and succeeds on the third attempt; a cron schedule fires; a two-step workflow survives a worker restart; `GET /v1/audit/verify` detects a manually altered row; a repeated `Idempotency-Key` returns the stored response; errors are problem+json with stable codes; a staff user signs in with TOTP in the console, creates a scoped API key, and the key cannot call outside its scope nor see another tenant's data.
