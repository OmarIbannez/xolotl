# xolotl

Multi-tenant KYC/AML compliance platform for LATAM regulated entities: policy
packs, screening, transaction monitoring, regulatory reporting (Formato 41,
avisos) and case management. This repository is the Python monolith; the
product and package are named `xolotl`.

## Status

Plans 1a and 1b of the platform kernel: tenancy with Postgres row-level security,
staff login with TOTP, scoped API keys, problem+json API conventions with
`Idempotency-Key`, the per-tenant audit chain and the `xolotl` CLI, and data
protection (per-tenant keys, encrypted values, blind indexes and a local object
store; see [Data protection](#data-protection)). Design documents live in
`docs/superpowers/specs/`; implementation plans in `docs/superpowers/plans/`.

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
Bootstrap sends the password as a SCRAM-SHA-256 verifier computed by libpq on the
application's side, so an `ALTER ROLE` the server refuses and logs carries the
verifier, never the password.

That image is the whole deployment: it also carries the staff console (`console/`,
built in a Node stage and served by the API at `/`, after the API's own routes).
With the stack up, create a tenant inside the container and sign in at
`http://127.0.0.1:8000/`:

```bash
docker compose exec api xolotl tenant create --slug acme --legal-name "Acme SA de CV" --admin-email admin@acme.mx
```

`console/README.md` covers developing the console and its Playwright smoke test.

`xolotl serve` also runs the bootstrap itself, before the migrations, whenever
`XOLOTL_DATABASE_ADMIN_URL` is set and the app role cannot sign in (it does not exist
yet, or its password changed), so a host can run the image with no step of its own. The
role is cluster-wide, so give each deployment its own Postgres cluster, as Render does.

## Deploy to Render

`render.yaml` is a Render Blueprint: the image as one web service (API, console and
worker, with a persistent disk for the object store) plus Render Postgres 17, on the
smallest paid plans (about $15 a month). With CI green on `main`, make a master key
with `openssl rand -base64 32` and keep a copy, create the Blueprint in Render
(*New > Blueprint*) from `main` and paste the key when asked: it is the only value to
enter.
The first start creates the app role and migrates; then create a tenant from the
service's Shell (`xolotl tenant create ...`) and sign in at the service URL.
[docs/deploy-render.md](docs/deploy-render.md) has the steps, what each deploy does,
the single-instance and disk caveats, and the costs.

## Configuration

Environment variables are prefixed `XOLOTL_`; see `.env.example`. `XOLOTL_ENV=prod`
refuses the default `XOLOTL_JWT_SECRET`, the default `XOLOTL_MASTER_KEY` and the
default `XOLOTL_DATABASE_URL` (set it, or let it be derived from
`XOLOTL_DATABASE_ADMIN_URL` and `XOLOTL_APP_DB_PASSWORD`, as below). Database URLs
may be spelled `postgres://` or `postgresql://`, as hosts hand them out; the psycopg
driver is added.

| Variable | Default | Purpose |
| --- | --- | --- |
| `XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES` | `false` | Lets `packs load` accept UNVERIFIED values; refused in prod. |
| `XOLOTL_APP_DB_PASSWORD` | unset | For a host that provides only the owner's URL (Render): with `XOLOTL_DATABASE_URL` unset and `XOLOTL_DATABASE_ADMIN_URL` set, the app URL is the admin URL signing in as `xolotl_app` with this password. An explicit `XOLOTL_DATABASE_URL` wins. |
| `XOLOTL_BANXICO_TOKEN` | unset | Banxico SIE token; unset runs the fetcher in demo mode from the seed files, refused when `XOLOTL_ENV=prod`. |
| `XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS` | `10` | libpq's connect timeout, in seconds (2 to 300; libpq treats less than 2 as 2), for every connection the application and the CLI open. A database that cannot be reached fails `serve` and the `db` commands in one line, `error: cannot connect to the database: ...`, once it expires, instead of hanging. |
| `XOLOTL_MASTER_KEY` | public dev key | Base64 of 32 random bytes (`openssl rand -base64 32`). Root key for tenant data encryption: keep it safe and stable, because data cannot be read without the key it was protected with. See [Data protection](#data-protection). |
| `XOLOTL_OBJECT_STORE` | `local` | Where documents and exports live. `local` (the filesystem) is the only store so far; the port is ready for later adapters. |
| `XOLOTL_OBJECT_STORE_ROOT` | `./data/local/objects` | Directory of the `local` store. |
| `XOLOTL_PACKS_DIR` | `./policy-packs` | Pack, unit seed and holiday sources; the Docker image sets `/app/policy-packs`. |
| `XOLOTL_STATIC_DIR` | `src/xolotl/static` | Directory of the built console. When it holds an `index.html` the API serves it at `/`, with a single-page fallback, after all of its own routes; the Docker image puts the build there. |
| `XOLOTL_UNITS_FETCH_CRON` | `0 18 * * 1-5` | Daily UDI and FIX fetch, America/Mexico_City. |

## Data protection

Every tenant has keys of its own. A value encrypted for one tenant opens with nothing of
another's, and destroying a tenant's keys makes what was encrypted for it unreadable for
good (crypto-shredding). Today that covers the TOTP secrets
(`staff_credential.totp_secret`) and the responses the idempotency store keeps for 24
hours so a lost response can be replayed (`idempotency_key.response_body`, which can
hold a new API key or an initial password): encrypting those was the deployment gate
before any production tenant, closed by migration 0005. Webhook signing secrets follow
with plan 1c's webhook task, and nothing stores one before that.

**Keys.** `xolotl tenant create` gives the tenant three keys in the creation transaction
(audited as `tenant.keys_created`): `DATA` encrypts values, `INDEX` computes blind
indexes, `BIOMETRIC` is reserved. Each is a random 256-bit AES key stored in `tenant_key`,
wrapped by the master key (the only key adapter today; the `KeyProvider` port is ready
for a KMS one); the plain key lives only in process memory, for 5 minutes. A stored value
is `version (2 bytes) || nonce (12) || ciphertext and tag (16)`,
authenticated with the tenant id and the purpose. The version names the key that sealed
the value, which is why old values stay readable after a rotation.

**Master key.** `XOLOTL_MASTER_KEY` wraps every tenant key, and every process that
creates or opens encrypted data needs the same one: the application (`xolotl serve`,
whose embedded worker shares its process), the standalone `xolotl worker`,
`xolotl tenant create`, `xolotl keys` and the migrations that touch encrypted data (in
both directions). Make it with `openssl rand -base64 32`, keep it out of the database and
its backups, and keep it stable: there is no master-key rotation yet, and changing the
key makes every tenant key unreadable. `XOLOTL_ENV=prod` refuses the public development
key (the one `docker-compose.yml` runs with); under any other `XOLOTL_ENV` an unset key
means that public key, and it is accepted.

Each wrapped key records the master key that wrapped it in `tenant_key.kek_ref`:
`local:v1:` and the first 8 bytes, in hex, of `HMAC-SHA256(master key,
"xolotl:kek-ref:v1")`. It is not secret and does not reveal the key (the development
key's is `local:v1:ccda9faa1d7fd54d`). A process started with another master key answers
`503 KEK_MISMATCH` when it reads a key (and logs the code at ERROR with the request id).
Because the idempotency store is sealed, every `POST` under `/v1` except `/v1/auth/*`
reads a key, and so does every TOTP step: they all answer 503 while `/readyz` stays 200,
so a health check does not catch it.
`xolotl keys rotate` under the wrong one refuses before it writes, so a stray
environment variable cannot add a key version the servers cannot read to a tenant that
has keys. Keys written before references had a fingerprint hold the bare `local:v1`; the
unwrap is their check. `xolotl keys destroy` works under any valid master key, a wrong
one included: with the master key lost, run it with any other valid one (under
`XOLOTL_ENV=prod` the process refuses to start with the public development key or with
none).

A tenant's first keys are the exception, since there is nothing to compare them with:
they take the reference of whoever creates them. Run `xolotl tenant create` only in the
production environment, never from a shell with a stray `XOLOTL_MASTER_KEY` or none
(outside `XOLOTL_ENV=prod` that means the public development key, and it is accepted).
Keys made under the wrong master key can be neither mended nor re-wrapped with these
commands, and destroying them is final, so that slug is lost. Right after creating a
tenant, check that the reference stored for it is the one this environment writes.
`SELECT DISTINCT kek_ref FROM tenant_key` lists what the database holds (add
`WHERE tenant_id = '<tenant uuid>'` for one tenant; `xolotl tenant list` prints the ids),
and this prints what the current environment writes (in the container, run the same
program with `docker compose exec api python -c ...`, without `uv run`):

```bash
uv run python -c "from xolotl.platform.config import get_settings; from xolotl.platform.crypto.provider import kek_ref_for; print(kek_ref_for(get_settings().master_key_bytes))"
```

`tenant_key` is under forced row-level security, so run the query as a Postgres
superuser (the `xolotl` role of `docker-compose.yml` is one). Any other role, the
application's and a managed server's owner included, sees no row until its session names
a tenant with `SELECT set_config('app.tenant_id', '<tenant uuid>', false)`, and then sees
that tenant's rows only.

**Encrypted values and `VIEW_PII`.** An encrypted column holds an opaque `Encrypted`
value: it prints as `Encrypted(***)`, never becomes JSON by accident and is masked if it
reaches a log. Service code opens it with `reveal()` to process it (checking a TOTP code)
and records nothing. The only way to show plaintext to a person is
`reveal_for_response(session, value, entity_type=..., entity_id=..., field=...)`, which
records a `VIEW_PII` audit event (the entity in the event's columns, the field in
`detail`) before it decrypts, and decrypts nothing if the event cannot be recorded.
Detail views use it; list views never reveal.

**Blind indexes.** To look a record up by a value stored encrypted, keep
`blind_index(kind, value, tenant_id=...)` beside it: an HMAC-SHA256 of the normalised
value under the tenant's `INDEX` key, so equal values have equal tags within a tenant and
different tags across tenants. Each kind has its own normalisation (`email`, `phone`,
`name`, `rfc`, and `curp`, `clabe` and anything else; see the table in
`platform/crypto/blind_index.py`), and a value with nothing left has no tag. `INDEX`
keys are never rotated, because every stored tag would have to be recomputed.

**Rotation.** `xolotl keys rotate --tenant acme --purpose DATA` (or `BIOMETRIC`; `INDEX`
is refused) adds the next key version and retires the current one. Values written before
keep their version and stay readable; nothing is re-encrypted. The `tenant.key_rotated`
event holds the purpose and the version and nothing else. A server that cached the
previous key may keep sealing under it for up to 5 minutes, which is harmless. Both
`keys` commands wait at most 5 seconds for a lock that another transaction holds on the
tenant's keys or audit trail, then stop with `CONFLICT ... try again shortly`, having
changed nothing. In the container, run them with `docker compose exec api xolotl keys ...`.

**Destruction.** `xolotl keys destroy --tenant acme --yes` is irreversible. It audits
`tenant.keys_destroyed`, then removes every wrapped key of the tenant in the same
transaction, and no key can be created for that tenant again. Everything that needs a key
then answers `410 KEY_DESTROYED`: the second sign-in step of every user with TOTP (it
reads the database each time, so it refuses at once), TOTP enrolment, and every `POST`
under `/v1` except `/v1/auth/*`, because the idempotency store is sealed with the
tenant's key and is read before the endpoint runs (revoking an API key and disabling a
user are `POST`s too). What still works is reading, with the tokens and API keys that
exist, and a password sign-in for users without TOTP; the tenant's status does not
change. So destruction is the last step of an offboarding: export what the tenant needs,
revoke its API keys, disable its staff and set its status first. The process that ran the
command forgets its cached keys at once, other server processes within 5 minutes.

Destruction does not reach copies. Backups, archived WAL and replicas taken before the
command still hold the wrapped keys, and so do the old row versions that its update
leaves in the live table until the table is vacuumed (autovacuum does it in time;
`VACUUM FULL tenant_key` rewrites the table and drops them at once). While
`XOLOTL_MASTER_KEY` is unchanged, a restore from any of them brings the keys back, so
shredding is complete once those copies have expired and the table has been vacuumed.
Nor does it reach the object store: its files are plaintext, so `keys destroy` leaves a
tenant's documents readable until you delete `<XOLOTL_OBJECT_STORE_ROOT>/<tenant_id>/`.
Destroying a tenant that has no keys is refused (and not audited); destroying twice is
harmless and audited each time.

**Object store.** `XOLOTL_OBJECT_STORE=local` is the only adapter today; the
`ObjectStore` port is ready for later ones, such as S3. An object is the file
`<XOLOTL_OBJECT_STORE_ROOT>/<tenant_id>/<key>` with its record beside it in
`<key>.meta.json`. Keys are validated (no `..`, empty segments, backslashes or NUL; at
most 512 characters), symlinks under the root are refused, and `get` recomputes the
sha256, so a changed object answers `500 OBJECT_CORRUPT`. The store keeps tenants apart
and checks what it serves but does not encrypt contents: put the directory on an
encrypted volume and back it up with the database. A whole object is read into memory,
concurrent puts to one key are last writer wins, and on a case-insensitive filesystem
(macOS by default) keys that differ only in case are one object. `docker-compose.yml`
mounts no volume for it yet; nothing writes objects until a feature does.

### Operating notes

- Migration 0004 (encrypted TOTP secrets) is not safe for a rolling deploy: code from
  before it writes plaintext into the column the new code reads as ciphertext. Stop the
  old version, run the migrations (`xolotl serve` and `xolotl db upgrade` both do, and
  log their progress), then start the new one. It needs `XOLOTL_MASTER_KEY`, and a
  downgrade needs the same key. It creates the keys a tenant lacks and logs that instead
  of auditing it; a tenant whose keys were destroyed is skipped and its old plaintext
  TOTP secrets are dropped. Migration 0005 (the sealed idempotency store) is no safer:
  neither direction works with the application running, and it empties the 24-hour
  replay cache, so a client that retries across the upgrade runs its request again
  instead of receiving the stored response.
- Migration 0004 is the template for every later conversion of a plaintext column to
  an encrypted one (the CURP and RFC columns, for one). It holds an ACCESS EXCLUSIVE
  lock on the table for its whole transaction (`ADD COLUMN` takes it, and the final
  table rewrite adds negligible time: 25 ms on 20,000 rows in an 8.8 s run), so size the
  window by the backfill. It drops the plaintext column first and rewrites the table
  after, so no `VACUUM FULL` is needed: a rewrite before the drop would leave the
  plaintext in the live heap pages.
  Either way the old values stay in backups, archived WAL and replicas taken before the
  conversion until their retention expires, and that holds for every such conversion,
  not only TOTP.
- Never lower the `sqlalchemy.engine` logger to DEBUG in production: result rows carry
  ciphertexts. The application holds it at INFO or quieter whatever `XOLOTL_LOG_LEVEL`
  says. Do not run an error tracker that captures local variables (Sentry's
  `include_local_variables`, rich tracebacks): keys are masked there, plaintexts and
  ciphertexts are not.
- A key that is not cached is read through a connection of its own unless the caller
  passes its session, so a request can hold two pooled connections for a moment. Do not
  size the pool at one connection per concurrent request.

### For developers

- A loaded `Encrypted` needs the tenant as the current context: `tenant_session` and
  `bind_context` set it, `apply_context` alone does not. Code that moves a session
  between tenants, or runs outside a request, binds it per tenant (the login steps do),
  and every process that creates or opens encrypted data installs the key provider first
  (`build_app`, `build_worker` for the standalone `xolotl worker`, `tenant create` and
  `keys` do).
- Assign `encrypt(value, tenant_id=..., session=s)` results. Plain text assigned to an
  attribute is sealed when the row is flushed, under the row's own tenant, so set
  `tenant_id` first; text bound in a Core statement is sealed under the current
  context's tenant, and text in an ORM bulk UPDATE is not sealed per tenant.
- `Model.secret == "x"` on an encrypted column compiles and never matches: search through
  a blind index. Never return an `Encrypted` from a route (FastAPI would serialise its
  ciphertext and metadata); response models carry `str`.
- The authenticated data binds the tenant and the purpose, and the key version selects the
  key. It binds neither row nor column, so someone with write access to the database can
  move a ciphertext between rows or columns of one tenant and it still opens. A
  ciphertext is the plaintext's UTF-8 length plus 30 bytes: pad at the application layer
  where length matters, for example identifiers of fixed length.

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
`console/` is the staff console (React, Vite); the Dockerfile builds it and copies
`console/dist` to `src/xolotl/static`, the directory the API serves at `/`.
