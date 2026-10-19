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
docker compose up -d --wait db
uv sync
cp .env.example .env
uv run xolotl db bootstrap
uv run xolotl db upgrade
uv run xolotl tenant create --slug acme --legal-name "Acme SA de CV" --admin-email admin@acme.mx
uv run xolotl serve --reload
```

Open `http://localhost:8000/v1/docs`. Sign in with `POST /v1/auth/login`
(`tenant_slug`, `email`, the one-time password printed by `tenant create`),
then change the password with `POST /v1/auth/password`. The change answers `200`
with a fresh access token (the same body as a login) and ends every earlier
session, the one that made the change included: carry on with the returned token.

Every `POST` under `/v1` except `/v1/auth/*` needs an `Idempotency-Key` header.
Machine access uses API keys created by an ADMIN at `POST /v1/api-keys`
(`Authorization: Bearer xk_...`).

`uv run xolotl audit verify --tenant acme` walks the tenant's audit chain and exits
1 when it is broken; `GET /v1/audit/verify` does the same for MLRO, ADMIN and
AUDITOR_RO users.

To run the whole stack in containers instead, `docker compose up --build` starts
Postgres and the API image (bootstrap, migrations, then `xolotl serve`) on
`127.0.0.1:8000`. That is the port `uv run xolotl serve` uses too, so stop one
before starting the other, or map the `api` service to another host port.

`xolotl db bootstrap` runs as the owner role in `XOLOTL_DATABASE_ADMIN_URL`. That
role needs `CREATEROLE`, not superuser, so managed Postgres (RDS, Cloud SQL) works:
an app role that is already neither superuser nor `BYPASSRLS` only gets its login
and password set. On PostgreSQL 16 and later the owner also needs `ADMIN OPTION` on
`xolotl_app`. It has that when bootstrap created the role; if someone else created
it, they (or a superuser) first run `GRANT xolotl_app TO <owner> WITH ADMIN OPTION`.

## Configuration

Environment variables are prefixed `XOLOTL_`; see `.env.example`. `XOLOTL_ENV=prod`
refuses the default `XOLOTL_JWT_SECRET`.

## Development

```bash
make ci        # ruff, ty, basedpyright, vulture, import-linter, pytest (coverage >= 90)
make test      # pytest only; needs Docker for the Postgres testcontainer
make format    # ruff --fix and ruff format
```

Tests run against a real Postgres 17 container with real commits; set
`XOLOTL_TEST_DATABASE_ADMIN_URL` to reuse an existing server instead. Point it at
a scratch database only: the suite bootstraps the app role (resetting its
password), migrates, and truncates every table before each test.

## Layout

`src/xolotl/platform/` is the shared kernel (imports nothing from modules);
`src/xolotl/modules/<name>/` are feature modules that own their tables, routers
and events; `src/xolotl/app.py` is the composition root; migrations are one
Alembic tree under `src/xolotl/migrations/`.
