# xolotl

Multi-tenant KYC/AML compliance platform for LATAM regulated entities: policy
packs, screening, transaction monitoring, regulatory reporting (Formato 41,
avisos) and case management. This repository is the Python monolith; the
product and package are named `xolotl`.

## Features

What is on `main` today, shown in the staff console (Spanish by default, English
available). Every screenshot below comes from a local demo workspace with invented
people, companies, identifiers and accounts; the sanctions entries are the public list
samples committed under `tests/modules/screening/fixtures/`.

### Platform

Each regulated entity is a tenant with its own workspace, isolated by Postgres
row-level security and its own encryption keys. Staff sign in with a password and
optional TOTP and hold one or more of five roles (analyst, senior analyst, MLRO,
read-only auditor, admin); the console only offers what a role may do, and the API
enforces it. Systems call the API with scoped API keys that an admin creates and
revokes. Changes, sign-ins and every view of personal data land in a per-tenant
audit chain that an MLRO, admin or auditor can verify from the console.

Regulatory reference data is versioned: policy packs (`MX/ITF-IFPE` and
`MX/LFPIORPI-AVI` so far) carry account tiers, capture thresholds, mandatory lists,
retention and governance, each value with its legal basis; an admin assigns a pack
version to the workspace (optionally per product) and can tighten it, never loosen
it. UMA, UDI and the USD/MXN FIX are kept as dated unit values, fetched from Banxico
(or from the committed seed files in demo mode), along with the regulatory holiday
calendars.

![Sign-in](docs/screenshots/sign-in.png)
![The effective policy for a date, with its legal basis and the pack assignment](docs/screenshots/policy.png)

<details>
<summary>More platform screenshots</summary>

![Staff and their roles](docs/screenshots/staff.png)
![API keys, listed by prefix only](docs/screenshots/api-keys.png)
![Audit log with a verified chain](docs/screenshots/audit-log.png)
![Unit values](docs/screenshots/units.png)

</details>

### Customers

People and companies are KYC records with identifiers (CURP, RFC, CLABE and other
LATAM IDs, each checked for structure and check digit), contacts, addresses,
documents, ownership and control relationships, and the customer relationships
(accounts, products) that transactions are booked against. Names, identifiers and
contact data are stored encrypted. Lists and detail pages show initials and the last
characters of each identifier; the full data appears only when a user presses
*Revelar*, and each reveal is recorded in the audit log first. Closing a customer
relationship starts the retention clock of the effective policy, and an MLRO can put
a legal hold on a record.

![A customer, masked](docs/screenshots/customer-detail-masked.png)
![The same customer after an audited reveal](docs/screenshots/customer-detail-revealed.png)

<details>
<summary>More customer screenshots</summary>

![Customers list](docs/screenshots/customers-list.png)
![A company's ownership and control relationships](docs/screenshots/customer-relationships.png)
![A company's documents](docs/screenshots/customer-documents.png)

</details>

### Sanctions and PEP screening

Customers are screened against the shared lists (UN Security Council consolidated
list, OFAC SDN, EU financial sanctions, UK sanctions list and SAT 69-B) and against the
tenant's own lists: its blocked-persons list (LPB) and any internal or PEP list it
uploads from the CSV or XLSX template. PEP status is also recorded on each person.
Screening runs when a customer is created or changed, on demand, monthly over the
whole base, and on every new list version, which re-screens only the entries the
version added or changed and suggests closing findings on removed ones. Findings go
to a review queue where each match shows how the names aligned, which qualifiers
(birth date, nationality, country) moved the score and which list version and matcher
produced it, and decisions follow four-eyes rules (an analyst escalates, an MLRO
confirms). API clients only ever see a status, never a list, score or finding. The
matcher's recall and precision on the evaluation corpus ship with each build.

![Review queue](docs/screenshots/screening-match-queue.png)
![A match explained, with the decision form](docs/screenshots/screening-match-detail.png)

<details>
<summary>More screening screenshots</summary>

![The match's subject revealed, at the bottom of the page](docs/screenshots/screening-match-revealed.png)
![List catalogue and freshness](docs/screenshots/lists-catalog.png)
![A list's versions with their additions, changes and removals](docs/screenshots/list-versions.png)
![The tenant's blocked-persons list](docs/screenshots/private-list-detail.png)
![Matcher evaluation report](docs/screenshots/screening-evaluation.png)

</details>

### Transaction monitoring (so far)

Transactions arrive in API batches (`POST /v1/transactions`) or as CSV, JSON Lines
or XLSX files uploaded from the console or the CLI; a rejected row is reported by row,
column and code, never with its value. A background job processes each customer's
transactions in order: it converts the amount to the units the policy needs (MXN,
UDI, USD), assigns the day, month and quarter periods and updates the running
aggregates. A transaction whose unit value or policy is not available yet is
*parked* and retried automatically; one received more than seven days after it
happened (`XOLOTL_MONITORING_LATE_DAYS`) waits for a *backfill* that an MLRO or admin
runs for a range of days. The
Transacciones page lists and filters them, shows the parked and backfill queues,
looks up an API batch and uploads files; a transaction's detail shows its unit
amounts, and its counterparty and raw payload only after an audited reveal. Each
customer's page lists their latest transactions.

![Transactions with filters](docs/screenshots/transactions-list.png)
![A processed transaction, masked](docs/screenshots/transaction-detail-masked.png)

<details>
<summary>More monitoring screenshots</summary>

![Filtered to processed transactions](docs/screenshots/transactions-filtered.png)
![The upload dialog](docs/screenshots/transactions-upload-dialog.png)
![An uploaded file's result and an API batch looked up](docs/screenshots/transactions-files-batches.png)
![Parked transactions](docs/screenshots/transactions-parked.png)
![Transactions waiting for a backfill](docs/screenshots/transactions-backfill.png)
![Requesting a backfill](docs/screenshots/transactions-backfill-dialog.png)
![The transaction revealed: counterparty and raw payload](docs/screenshots/transaction-detail-revealed.png)
![A customer's Transacciones card](docs/screenshots/customer-transactions-card.png)

</details>

### Counterparties

Every counterparty named in a transaction is resolved to one record per tenant (from
its name, account, wallet address and identifier, compared through blind indexes)
and screened the first time it is seen,
including crypto wallets, which match a listed digital-currency address exactly. Its
findings join the same review queue. The Contrapartes page lists them masked with
their screening status and number of transactions, and a counterparty's detail shows
its findings and screening runs, with the name, account and wallet revealed only on
request (audited).

![Counterparties pending review](docs/screenshots/counterparties-list.png)
![A wallet counterparty matched on the OFAC SDN list](docs/screenshots/counterparty-detail.png)

<details>
<summary>More counterparty screenshots</summary>

![The counterparty revealed](docs/screenshots/counterparty-detail-revealed.png)

</details>

### Not in the console yet / coming next

- **Monitoring rules.** The Mexico rule templates (sixteen typologies) and the rule
  version lifecycle (draft, simulated, in review, approved, active, retired, with
  four eyes) exist, with the rules API (`/v1/monitoring/templates`, `/rules`,
  `/simulations`, `/assignments`) and the CLI (`xolotl rules ...`); no console
  screen yet.
- **Rule screens:** the rule editor and simulation pages.
- **Regulatory captures:** each transaction a report must carry (Formato 41 and
  avisos) is recorded as it is processed and each period is closed by
  re-evaluation, with a staff API; the Capturas y límites page comes next.
- **The real-time decide endpoint** for payment rails (`POST
  /v1/transactions/decide`), with per-rail fail-mode settings
  (`/v1/monitoring/rails`).
- **Effectiveness metrics** for screening and monitoring.

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
| `XOLOTL_CONNECTOR_MODE_DEFAULT` | `SANDBOX` | Connector mode of a PROD tenant when a call names none. SANDBOX tenants always use mocks. The Render blueprint sets LIVE. |
| `XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS` | `10` | libpq's connect timeout, in seconds (2 to 300; libpq treats less than 2 as 2), for every connection the application and the CLI open. A database that cannot be reached fails `serve` and the `db` commands in one line, `error: cannot connect to the database: ...`, once it expires, instead of hanging. |
| `XOLOTL_LISTS_FETCH_ENABLED` | unset | Network fetching of the shared sanctions lists by the `lists.poll` job. Unset means true only when `XOLOTL_ENV=prod`; elsewhere load files with `xolotl lists load-file`. |
| `XOLOTL_LISTS_FETCH_TIMEOUT_SECONDS` | `120` | Timeout of one list download request, in seconds (5 to 600). |
| `XOLOTL_LISTS_MAX_FILE_MB` | `300` | Larger list downloads are refused (1 to 2048). |
| `XOLOTL_LISTS_MAX_REMOVAL_RATIO` | `0.20` | A new list version that removes more than this share of the previous version's entries is refused (the mass-removal guard). |
| `XOLOTL_LISTS_POLL_CRON` | `* * * * *` | When the per-tenant `lists.poll` job runs (UTC). |
| `XOLOTL_MASTER_KEY` | public dev key | Base64 of 32 random bytes (`openssl rand -base64 32`). Root key for tenant data encryption: keep it safe and stable, because data cannot be read without the key it was protected with. See [Data protection](#data-protection). |
| `XOLOTL_MONITORING_AGG_BACKFILL_BATCH` | `5000` | Transactions each page of an aggregate backfill reads (1 or more). |
| `XOLOTL_MONITORING_ALLOW_CUSTOM_RULES` | `false` | Lets an ADMIN author a monitoring rule from a bare kind, without a library template. |
| `XOLOTL_MONITORING_BATCH_MAX_ITEMS` | `1000` | Most transactions in one `POST /v1/transactions` batch (1 to 10000). |
| `XOLOTL_MONITORING_COUNTERPARTY_SCREEN_BATCH` | `100` | New counterparties each first-sight `screening.screen_counterparties` job screens in one transaction (1 or more). |
| `XOLOTL_MONITORING_DECIDE_BUDGET_MS` | `80` | Budget of one `POST /v1/transactions/decide`, in milliseconds (1 or more); past it the rail's fail mode answers. |
| `XOLOTL_MONITORING_DECIDE_FAIL_MODE` | `FAIL_OPEN` | Fail mode of a rail without a setting: `FAIL_OPEN` answers `REVIEW`, `FAIL_CLOSED` answers `BLOCK`. |
| `XOLOTL_MONITORING_FILE_CHUNK_ROWS` | `1000` | Rows of a transactions file ingested per page of the `monitoring.ingest_file` job (1 or more). |
| `XOLOTL_MONITORING_FILE_MAX_MB` | `50` | Largest transactions file upload, in MB (1 to 1024). |
| `XOLOTL_MONITORING_FILE_MAX_ROWS` | `500000` | Most rows in one transactions file. |
| `XOLOTL_MONITORING_LATE_DAYS` | `7` | A transaction received more than this many days after it occurred is held for a backfill instead of being processed (1 or more). |
| `XOLOTL_MONITORING_MAX_FUTURE_MINUTES` | `10` | How far in the future a transaction's `occurred_at` may be, in minutes, to allow for clock skew. |
| `XOLOTL_MONITORING_PARTITION_MONTHS_AHEAD` | `3` | Monthly partitions the daily partition job keeps ahead of the current month (1 to 12). |
| `XOLOTL_MONITORING_PARTITIONS_CRON` | `0 1 * * *` | When the daily partition maintenance job runs (UTC). |
| `XOLOTL_MONITORING_PERIOD_CLOSE_CRON` | `0 4 1 * *` | When the per-tenant monthly `monitoring.period_close` job runs (Mexico City): every capture period that ended is reconciled by re-evaluation and closed. |
| `XOLOTL_MONITORING_PROCESS_LEASE_SECONDS` | `300` | Lease of one `monitoring.process` job, in seconds (30 or more). |
| `XOLOTL_MONITORING_PROCESS_PAGE` | `200` | Transactions a `monitoring.process` job reads per page (1 or more). |
| `XOLOTL_MONITORING_PROFILE_CRON` | `0 3 2 * *` | When the per-tenant monthly `monitoring.profiles` job runs (Mexico City): the behaviour profile of every relationship active in the previous month. |
| `XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO` | `2.0` | Actual over declared monthly inflow or outflow at or above which a behaviour profile is `deviated` (above 0). |
| `XOLOTL_MONITORING_RETENTION_CRON` | `0 5 1 * *` | When the per-tenant monthly `monitoring.retention` job runs (UTC): transaction months past `retention.transactions`, orphaned counterparties and stale aggregate buckets. |
| `XOLOTL_MONITORING_RULE_CACHE_SECONDS` | `30` | Seconds a process caches a tenant's active rule set for a date and product (0 disables the cache). An activation or retirement refreshes it at once. |
| `XOLOTL_MONITORING_SIMULATION_MAX_AGE_DAYS` | `30` | A rule version is submitted and approved only with a completed simulation of its current definition younger than this many days (1 or more). |
| `XOLOTL_MONITORING_SIMULATION_MAX_DAYS` | `190` | The longest range, in days (both ends included), a rule simulation replays; a longer one is refused with `SIMULATION_RANGE_TOO_LONG` (1 or more). |
| `XOLOTL_MONITORING_SWEEP_CRON` | `* * * * *` | When the per-tenant `monitoring.sweep` job runs (UTC). |
| `XOLOTL_OBJECT_STORE` | `local` | Where documents and exports live. `local` (the filesystem) is the only store so far; the port is ready for later adapters. |
| `XOLOTL_OBJECT_STORE_ROOT` | `./data/local/objects` | Directory of the `local` store. |
| `XOLOTL_PACKS_DIR` | `./policy-packs` | Pack, unit seed and holiday sources; the Docker image sets `/app/policy-packs`. |
| `XOLOTL_PRIVATE_LIST_MAX_MB` | `20` | Largest private-list upload, in MB (1 to 200). |
| `XOLOTL_PRIVATE_LIST_MAX_ROWS` | `100000` | Most rows in one private-list upload. |
| `XOLOTL_RETENTION_PURGE_CRON` | `0 3 * * *` | When the daily party retention sweep runs (UTC). |
| `XOLOTL_SCREENING_DELTA_BATCH` | `200` | Added and changed list entries each list-delta re-screen job handles in one transaction (1 or more). |
| `XOLOTL_SCREENING_INDEX_REFRESH_SECONDS` | `30` | How often each process checks for newer active list versions and reloads its in-memory screening index, in seconds (1 or more). |
| `XOLOTL_SCREENING_INDEX_WARM` | `true` | Load the screening index on a background thread when the API or `xolotl worker` starts; `false` loads it synchronously on the first screen instead (the test suite does). |
| `XOLOTL_SCREENING_PERIODIC_CRON` | `0 2 1 * *` | When each tenant's monthly full re-screen of its parties against every list starts, as a five-field cron in `America/Mexico_City`. |
| `XOLOTL_SCREENING_PRIVATE_SHARDS` | `64` | How many tenants' private-list indexes each process keeps in memory; the least recently used is dropped beyond it (1 or more). |
| `XOLOTL_SCREENING_RESCREEN_BATCH` | `500` | Parties each periodic, private-list or re-index re-screen job handles in one transaction (1 or more). |
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

## Policy packs, units and calendar

Regulatory reference data lives in `policy-packs/` (`XOLOTL_PACKS_DIR`) and is loaded
into four shared tables by the owner role:

```bash
uv run xolotl packs validate policy-packs/MX/ITF-IFPE/v1.yaml   # schema and provenance check
uv run xolotl packs load --allow-unverified    # install every pack; a rerun changes nothing
uv run xolotl packs show MX/ITF-IFPE --version 1
uv run xolotl holidays seed                    # policy-packs/_holidays/MX.yaml, four scopes
uv run xolotl units seed --allow-unverified    # UMA, UDI and FIX from policy-packs/_units
uv run xolotl units fetch --unit UDI --from 2026-01-01 --to 2026-01-31
```

A pack version is immutable: loading a changed file at the same version is refused
(`PACK_VERSION_IMMUTABLE`). Without `--allow-unverified` (refused in prod) `packs load`
and `units seed` refuse values still marked `UNVERIFIED` and name them.

`policy-packs/_jurisdictions.yaml` holds the named jurisdiction lists that monitoring
rules refer to as `$NAME`: three country sets (`FATF_BLACK` from `call_for_action`,
`FATF_GREY` from `increased_monitoring`, `TAX_HAVEN` from `tax_haven`, ISO 3166-1
alpha-2) and one region set (`BORDER_ZONE_MX`, ISO 3166-2 codes under `regions`),
with the `as_of` date of the FATF statements they copy. `TAX_HAVEN` is empty until a
source is chosen, so a rule naming it is refused when its rule set loads, and
`BORDER_ZONE_MX` defaults to the six northern border states
(`regions_confidence: UNVERIFIED`). `xolotl.platform.policy.load_jurisdictions()`
reads and checks the file (a missing or unknown key, a malformed or repeated code is
`VALIDATION_FAILED`); a rule set resolves its list names to codes when it loads, and
the SHA-256 of the file is recorded in every hit's evidence (`lists_hash`) and in
each aggregate's id, so editing the lists starts new aggregates.

### Typology library

The Mexico monitoring rule templates are pack content too: one file per template in
`policy-packs/MX/_typologies/<CODE>.yaml` (`{version, country_code, packs,
definition}`, where `definition` is the typed rule document of spec §9.1), sixteen in
all (spec §9.3). `packs` names the packs a template applies to, so a tenant only gets
the templates of its packs. `uv run xolotl rules validate [directory]` checks every
file without a database and prints `<code> v<n> <kind> <hash> <packs>` per template;
an invalid file exits 1 naming it. A template version is immutable: change a loaded
template by adding it again under a new `version`. The definition hashes are pinned
in `tests/golden/monitoring/rules/hashes.yaml` and the sentence each renders in
`tests/golden/monitoring/rules/render/`. Write every decimal quoted (`"5000"`; a float
is refused) and quote the `"on"` key (YAML 1.1 reads a bare `on` as `true`).

```bash
uv run xolotl rules load [directory]           # install the library as the database owner; a rerun changes nothing
uv run xolotl rules show CASH_STRUCTURING [--version 1]   # the definition as YAML and its sentence in es-MX and en
uv run xolotl rules seed-tenant --tenant acme --author mlro@acme.mx   # a DRAFT rule per template of the tenant's packs
uv run xolotl rules simulate --tenant acme --version <id> --from 2026-06-01 --to 2026-06-29   # replay history through a version
uv run xolotl rules activate --tenant acme --version <id> --approved-by mlro@acme.mx [--simulate-from D --simulate-to D]
```

`rules simulate` runs a simulation in place as the version's author (see
"Simulation") and prints `simulation <id>: <hits> hits, <customers> customers, <weeks>
weeks`. `rules activate` takes a version the rest of the way from today: the author
simulates it (by default over the last 30 days, today included) and submits it, the
approver, an MLRO or an admin other than the author, approves it and activates it for
every product from today; it prints the simulation's summary and `active <code> v<n>
from <date>: assignment <id>`. An approver who authored or edited the version, or a
version past `APPROVED`, is refused with `RULE_TRANSITION_NOT_ALLOWED` before anything
moves, and a step already taken (a version already in review or approved) is
skipped. Both commands act as the staff users they name, so the lifecycle's audit
rows name the author and the approver; before those steps each run writes one
`rules.cli_invoked` row as the operator (`PLATFORM_OPERATOR`, `cli:<os user>`) with
the command, the version, the author, the approver and the simulation (ids only).
A run refused later, for its range say, keeps that row. The CLI cannot tell which
staff user the operator is, so it cannot refuse an operator approving their own
work: whoever holds the CLI is trusted with both users' names. Activation never starts in the
past, so a demo whose fixtures predate today runs the command under an earlier clock.

`rules load` prints one line per template version it creates and a count; `xolotl db
upgrade` and `serve` run it after seeding the list catalog. Loading a version that is
already loaded with another definition exits 1 with `RULE_TEMPLATE_IMMUTABLE` and
writes nothing. `rules seed-tenant` gives the tenant a `DRAFT` version, with the
template's default parameters and the named staff user as author, of every template
whose packs intersect the packs assigned to the tenant today or later; templates the
tenant already has a rule for are skipped. A tenant tunes `params`, `segment`,
`severity` and `dedup` within the template's bounds (`min`, `max`, `choices`, and the
`tighten` direction from the template's default); a value outside them is refused
with `RULE_PARAM_OUT_OF_BOUNDS` naming the parameter, the bound and the template
version. The structuring band and the velocity windows are derived from the tuned
parameters. Authoring a rule without a template is ADMIN-only and off unless
`XOLOTL_MONITORING_ALLOW_CUSTOM_RULES=true`.

A tenant's ADMIN assigns a pack version, optionally per product, and may tighten it
with an override; staff of any role read:

- `GET /v1/policy/packs` lists the installed pack versions with their content hash.
- `POST /v1/policy/assignments` (`Idempotency-Key`) assigns `pack_version_id` from
  `effective_from`, tenant-wide or for one `product`; `GET` lists the assignments.
- `POST /v1/policy/overrides` (`Idempotency-Key`) records a new override version; a
  value that loosens the packs is 409 `POLICY_LOOSENING`; `GET` lists the versions.
- `GET /v1/policy/effective?on=YYYY-MM-DD&product=...` resolves the effective policy,
  freezes it in a snapshot (stable `snapshot_id` and `content_hash`) and converts tier
  limits and capture thresholds to MXN; a missing unit value leaves that entry `null`
  and lists the unit in `missing_units`. No pack in force is 404 `POLICY_NOT_ASSIGNED`.
- `PUT /v1/tenants/me/regulatory-profile` sets the tenant's RFC (stored encrypted,
  answered as `rfc_masked`), legal name and supervisor keys; `GET` reads it.
- `POST /v1/tenants/me/branches` adds a branch (its address stored encrypted and never
  returned); `GET` lists them.
- `GET /v1/units`, `GET /v1/units/{code}?on=&rule=` and
  `GET /v1/units/{code}/values?from=&to=` show the units, the row a conversion would use
  (503 `UNIT_VALUE_MISSING` when none) and the dated values with their corrections (at
  most 366 days).
- `GET /v1/calendar/holidays?scope=&year=` lists a scope's holidays, and
  `GET /v1/calendar/due?rule=<json>&anchor=<datetime with offset>&scope=` evaluates a
  deadline rule in America/Mexico_City.

The `units.fetch` job runs per tenant on `XOLOTL_UNITS_FETCH_CRON` (weekdays at 18:00
America/Mexico_City): it fetches the UDI and FIX values missing since the latest stored
day from Banxico SIE (`XOLOTL_BANXICO_TOKEN`, or the seed files in demo mode), inserts
nothing for an unchanged value, supersedes a corrected one and notifies ADMIN and MLRO
when the source fails.

## Lists

The shared sanctions lists (UN, OFAC SDN, EU, UK, SAT 69-B) are catalogued in
`src/xolotl/modules/screening/sources.yaml` and versioned in four shared tables. Each
ingested file becomes a new version with a delta against the previous one; its raw
bytes go to the object store under the shared namespace.

```bash
uv run xolotl lists seed                       # upsert the catalog (db upgrade and serve run it too)
uv run xolotl lists status                     # version, last success, next poll, freshness per list
uv run xolotl lists load-file UN_CONSOLIDATED path/to/consolidated.xml   # offline ingestion
uv run xolotl lists show UN_CONSOLIDATED --version 2
uv run xolotl lists fetch UN_CONSOLIDATED --force-network                # one fetch now
```

- **Development and CI** load files with `lists load-file`: network fetching is off
  unless `XOLOTL_ENV=prod` or `XOLOTL_LISTS_FETCH_ENABLED=true`, and `lists fetch`
  needs `--force-network` there. A file removing more than
  `XOLOTL_LISTS_MAX_REMOVAL_RATIO` of the entries is refused (`LIST_MASS_REMOVAL`)
  unless `--accept-drop`, which is logged with the operator.
- **In prod** every tenant's `lists.poll` job (`XOLOTL_LISTS_POLL_CRON`, every minute)
  claims at most one due source under a ten-minute lease, so each source is fetched
  once per interval across all tenants (UN 15 min, OFAC 30 min, EU and UK 60 min, SAT
  24 h). The request is conditional (`ETag`, `Last-Modified`); a 304 or an unchanged
  file counts as a success. A failed download, a malformed file or a mass removal
  keeps the previous version active, records `last_error` (a code and a short reason)
  and backs off to `min(interval * 2^failures, 6 h)`.
- **Staleness:** a list whose last success is older than twice its interval is stale;
  the poll notifies ADMIN and MLRO once per stale episode. Each new version reaches
  every subscribed tenant as one `lists.version_published.v1` event.
- **Screening index:** each process (the API and `xolotl worker`) holds the screening
  index of the active list versions in memory, warmed in the background at start-up
  (a screen answers 503 `SCREENING_INDEX_UNAVAILABLE` until it is loaded) and
  reloaded when `XOLOTL_SCREENING_INDEX_REFRESH_SECONDS` finds a newer version.

### Screening API

Staff and API clients share the check, run and party routes but get different
responses: an API client sees the subject's status only (`CLEAR`, `PENDING_REVIEW`,
`TRUE_MATCH`), never a list code, score, entry or finding.

| Route | Who | What |
|---|---|---|
| `POST /v1/screening/checks` | staff; scope `screening:write` | screen `{party_id}` or an inline `{subject}` now; staff may narrow `lists` and get the hits with their explanations |
| `GET /v1/screening/runs/{id}` | staff; scope `screening:read` | the run's outcome; staff also get its list versions, matcher, policy snapshot and hits |
| `GET /v1/screening/parties/{party_id}` | staff; scope `screening:read` | status, last screening, lists covered; staff also get the findings |
| `GET /v1/screening/matches` · `GET /v1/screening/matches/{id}` | staff | the review queue (filters `status`, `band`, `list_code`, `list_kind`, `created_before`) and a masked finding with its explanations, dispositions and allowed actions |
| `GET /v1/screening/matches/{id}/reveal` | staff | the subject's names and identifiers, a private entry and the rationales; one `VIEW_PII` per group |
| `POST /v1/screening/matches/{id}/dispositions` | staff, not `AUDITOR_RO` | a decision under the four-eyes rules; a refusal is 409 `DISPOSITION_NOT_ALLOWED` with `blocked_reasons` |
| `DELETE /v1/screening/whitelist/{id}` | MLRO | revoke a false-positive whitelist row |
| `GET /v1/screening/batches` | staff | re-screen batches newest first (filters `kind`, `status`): progress counts, `total`, start and finish times |
| `GET /v1/screening/evaluation` | staff | the matcher's evaluation report shipped with the build: recall, precision at `ALERT` and retrieval recall per list kind, the hard-negative alert rate and each floor; 404 when the build ships none |
| `GET /v1/counterparties` | staff | counterparties masked (initials, last four of the account, wallet and identifier), newest last sighting first, with screening status and `txn_count` (filters `status`, `kind`, `seen_after`) |
| `GET /v1/counterparties/{id}` | staff | the masked counterparty with its subject's findings (newest first, linking into the queue) and its last ten runs |
| `GET /v1/counterparties/{id}/reveal` | staff | the name, account, wallet address and identifier in plaintext; one `VIEW_PII` per value, recorded before decryption |

## Screening

Creating a party, changing it or adding an identifier re-indexes its names and
re-screens it through one `screening.screen_party` job per event (mode `NEW_SUBJECT`
or `DATA_CHANGE`); the job skips when a later run already covered the event.

Counterparties are screened on first sight: a transaction naming a counterparty never
seen before indexes its name and enqueues a `screening.screen_counterparties` job,
which screens new counterparties `XOLOTL_MONITORING_COUNTERPARTY_SCREEN_BATCH` per
page (mode `COUNTERPARTY`; a wallet address matches a listed digital-currency address
exactly and whole), and their findings appear in the screening queue. The counterparty
row caches its subject's status (`UNSCREENED` until the first run) for the decide path,
and every change publishes the staff-only `screening.counterparty_status_changed.v1`.

```bash
uv run xolotl screening check --tenant acme --party <party_id>   # screen one party now
uv run xolotl screening screen-counterparties --tenant acme       # screen new counterparties now
uv run xolotl screening replay <run_id> --tenant acme             # re-score a run's hits
uv run xolotl screening rescreen --tenant acme [--kind REINDEX]  # start a full re-screen
```

Whole-base re-screens walk the tenant's `ACTIVE` parties in id order,
`XOLOTL_SCREENING_RESCREEN_BATCH` per job, under a batch whose progress
`GET /v1/screening/batches` shows. A new private-list version re-screens every party
against that version's entries (`PRIVATE_LIST`); a subscription's first version of a
shared list and the monthly `screening.periodic_rescreen` schedule
(`XOLOTL_SCREENING_PERIODIC_CRON`) re-screen every party against the full list set
(`PERIODIC`); and the worker, as it starts, opens a `REINDEX` batch when a party's name
index was built by another matcher version or is missing. `screening rescreen` starts a
`PERIODIC` (or `REINDEX`) batch by hand; the worker runs it.

`screening replay` rebuilds the subject's query, the scoring IDF of the run's exact list
versions and the scoring configuration of its policy snapshot, prints one line per hit
(stored and replayed score and band) and ends with `identical` (exit 0), or `subject
changed` or `N differences` (exit 1). A run scored by another matcher version is
refused with `MATCHER_VERSION_MISMATCH`.

### Screening evaluation

`uv run xolotl screening eval` runs the screening library's corpus
(`tests/modules/screening/corpus/`, or `--corpus PATH`) through the matcher and prints
recall, precision at `ALERT` and retrieval recall per list kind, the alert rate on the
hard negatives, and one `FLOOR <metric> <kind> <floor> <value> OK|BELOW` line per floor
of `corpus/floors.yaml`; it exits 1 when a floor is below. `uv run xolotl screening eval
--write` also writes `src/xolotl/modules/screening/data/evaluation.json` (refused below
a floor), the report `GET /v1/screening/evaluation` and the console's Evaluación page
serve. Commit that file. A matcher change that bumps `MATCHER_VERSION` must regenerate
it in the same PR: a test fails while the committed report names another version. The
corpus is not in the container image, so the command only runs from a source checkout.

## Monitoring

Transactions arrive through `POST /v1/transactions` batches or files. Each party's
`monitoring.process` job takes its received rows in `occurred_at` order, one database
transaction per row: it enriches the row with the effective policy's units and periods,
adds it to the declared aggregates and marks it `PROCESSED`. A row whose unit value or
policy is not there yet is `PARKED` and the party's later rows wait behind it. A row
received more than `XOLOTL_MONITORING_LATE_DAYS` after it occurred is held as
`BACKFILL` until staff request a backfill of its days.

The per-minute `monitoring.sweep` (`XOLOTL_MONITORING_SWEEP_CRON`) enqueues every party
with a received row over 30 seconds old, un-parks the rows whose unit value arrived and
retries the policy ones hourly (at once after a new pack assignment), notifies ADMIN and
MLRO once a Mexico City day while a parked row has waited over six hours, re-enqueues the
backfill of a declaration left incomplete, and logs files left `PROCESSING` with no job.
A new aggregate declaration is rebuilt by `monitoring.agg_backfill` from the last six
months of processed rows, `XOLOTL_MONITORING_AGG_BACKFILL_BATCH` per page; until then its
reads are incomplete. The daily `monitoring.partitions` (`XOLOTL_MONITORING_PARTITIONS_CRON`)
keeps every partitioned table `XOLOTL_MONITORING_PARTITION_MONTHS_AHEAD` months ahead.

Retention. The monthly `monitoring.retention` (`XOLOTL_MONITORING_RETENTION_CRON`, UTC)
takes the longest `retention.transactions` of every pack the tenant was assigned, for any
product, not ended before its oldest month, and of every override (ten years once ITF
was assigned), and retires every month whose end is at least that long ago: the tenant's hit
links, reportable events and emptied capture groups, hits first seen in the month,
decision links, decisions, parked and ref rows and transactions of the month, in that
order, each step in its own transaction, so a run that stops half-way is finished by the
next. Each step locks the month's parties and stops the month when one is under legal
hold, and is audited (`monitoring.retention_step`) in its own transaction. Partitions hold
every tenant's rows, so a month's `transaction` and `txn_decision` partitions are
detached and dropped only once every tenant's period has passed for it and no row of
any tenant is left in it, taking every lock it needs at once without waiting (a busy
table defers the drop to a later run); the default partitions and `audit_event` are
never dropped. A
counterparty no transaction references any more, and whose screening records are all
past the period too, is purged with its subject, runs, findings and dispositions; buckets
past the longest declared window (plus the late days) are pruned, except running
accumulators and buckets of open capture periods. Each run writes one
`monitoring.retention_ran` audit row with its counts and skipped months. A tenant with no
policy in force retires nothing (and its run is audited with no period).

Behaviour profiles. The monthly `monitoring.profiles` (`XOLOTL_MONITORING_PROFILE_CRON`,
the 2nd in Mexico City) profiles the previous Mexico City month for every relationship
with processed rows in it: MXN inflow and outflow (minor units, from each row's
`MXN/SAME_DAY` amount), counts, the cash, cross-border and crypto shares of the month's
amount, distinct counterparties and active days. It copies the relationship's declared
profile, which staff or an API key with `parties:write` set through
`PATCH /v1/relationships/{id}` (MXN only; a change is audited
`relationship.declared_profile_set` with the currency and the names of the fields that
changed, never an amount), and flags the month deviated when actual over declared inflow
or outflow reaches `XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO`, or when money moved in a
direction declared as zero (`deviation.reasons`: `DECLARED_ZERO`). A
written or changed profile publishes the staff-only
`monitoring.behaviour_profile_updated.v1`; the `PROFILE_DEVIATION` rules read the same
declaration.

```bash
uv run xolotl monitoring ingest-file --tenant acme transactions.csv   # dev and demo: ingest here
uv run xolotl monitoring process --tenant acme [--party <party_id>]  # drain now
uv run xolotl monitoring sweep --tenant acme                          # run the sweep now
uv run xolotl monitoring backfill --tenant acme --from 2026-01-20 --to 2026-01-21 [--party <id>]
uv run xolotl monitoring partitions ensure                            # create the months ahead now
uv run xolotl monitoring profiles --tenant acme --month 2027-01       # compute a month's profiles
uv run xolotl monitoring period-close --tenant acme [--capture ITF_ROR_CASH --period 2026-Q4]
```

`monitoring backfill` audits the request (`monitoring.backfill_requested`), queues its
job as the API does and runs it in place: the held rows of those Mexico City days are
processed, late, with the same code as the worker.

A payment rail asks `POST /v1/transactions/decide` before it executes a payment and
gets `ALLOW`, `REVIEW` or `BLOCK` within `XOLOTL_MONITORING_DECIDE_BUDGET_MS`. The
answer comes from the tenant's sync rules (the `TIER_N1_MONTHLY_OPS` and
`TIER_N2_MONTHLY_OPS` limits of the ITF pack) over the stored aggregates with the
payment counted in them. Over the budget, or when the database fails, the rail's fail
mode answers (`FAIL_OPEN`: `REVIEW`, `FAIL_CLOSED`: `BLOCK`, reason
`DECIDE_UNAVAILABLE`); the route never answers 503. Every answer is stored in
`txn_decision` and linked to the transaction submitted later under the same
`external_id` (within seven days). The client sees codes only: `TIER_LIMIT` with the
tier and limit, or `RISK_REVIEW`; never a rule name. The first decision after a
start (or after the rule-set cache expires) loads the rules and policy cold and may
pass a tight budget, which on a `FAIL_CLOSED` rail answers `BLOCK`.

| Route | Who | What |
|---|---|---|
| `POST /v1/transactions/decide` | API key `transactions:write`; `Idempotency-Key` | one transaction as in a batch → `{decision, decision_id, reasons: [{code, tier?, limit_code?}], latency_ms}`; 422 with each field error under `body` |
| `GET /v1/monitoring/decisions/{id}` | staff; API key `transactions:read` | staff: the decision with the rules that fired, their aggregates, the rule-set hash, timings and the linked transaction; API key: `{decision_id, external_id, decision, mode, decided_at}` |
| `GET /v1/monitoring/rails` | staff | every rail's fail mode: the one an ADMIN set, or `XOLOTL_MONITORING_DECIDE_FAIL_MODE` with `explicit` false |
| `PUT /v1/monitoring/rails/{rail}` | staff `ADMIN` | `{fail_mode}` for one rail, audited `monitoring.rail_setting_changed`; 404 `RAIL_UNKNOWN` for a rail that does not exist |
| `GET /v1/monitoring/profiles/{relationship_id}` | staff | the relationship's last 12 behaviour profiles, newest first: `month`, `metrics`, `declared_profile`, `deviation`, `deviated`, `computed_at`; 404 for an unknown relationship |
| `PATCH /v1/relationships/{id}` | staff writers; API key `parties:write` | `{declared_profile}` (`monthly_inflow_minor`, `monthly_outflow_minor`, `currency` `MXN`, `expected_countries`, `expected_instruments`), or `null` to clear it; audited `relationship.declared_profile_set`; 422 for another currency or an unknown field |

### Rule lifecycle

A tenant rule version moves through `DRAFT`, `SIMULATED`, `IN_REVIEW`, `APPROVED`,
`ACTIVE` and `RETIRED` (spec §9.4). A senior analyst, an MLRO or an admin edits a
`DRAFT` or `SIMULATED` version (any change sends it back to `DRAFT`) and simulates
it. Only its author submits a `SIMULATED` version for review, and only with a
completed simulation of its current definition younger than
`XOLOTL_MONITORING_SIMULATION_MAX_AGE_DAYS` (30 days). An MLRO or an admin who neither
authored nor edited it approves it (four eyes; the database refuses an approval by
the author too), after the same simulation check, or rejects it back to `DRAFT`
with a reason (required, but its text is not kept yet: the audit row only records
that one was given). An MLRO or an admin activates an `APPROVED` version from a date that is
today or later, for every product or for one: activation creates its assignment,
retires the rule's current `ACTIVE` version, ends every earlier assignment of the
rule the day before the new one starts, and backfills any aggregate the version
declares that the tenant does not maintain yet. Retiring an `ACTIVE` version takes
the rule out of force after today: every assignment of the rule still in force
later, including a predecessor waiting for a scheduled successor, ends today. An
activation effective today ends the predecessor's assignment yesterday, although
the predecessor evaluated today's earlier transactions; each hit keeps the
`assignment_id` it was evaluated under, so the hit, not the assignment row,
answers "which rule applied to this transaction". A refused action is 409 `RULE_TRANSITION_NOT_ALLOWED` with
`blocked_reasons` (`STATE`, `ROLE_REQUIRED`, `NOT_AUTHOR`, `SAME_AUTHOR`,
`SIMULATION_MISSING`, `SIMULATION_STALE`, `PARAM_OUT_OF_BOUNDS`), and every
transition is audited as `rules.version_*`; an activation's audit row carries the
category `CHANGE_POLICY`.

The rules that apply on a date are those whose assignment is in force on it, for
the transaction's product or for every product, so "which rules applied on this
date" is answered from the assignment rows. Each process caches a tenant's rule
set for a date and product for `XOLOTL_MONITORING_RULE_CACHE_SECONDS`; an
activation or a retirement is seen on the next read.

### Simulation

A senior analyst, an MLRO or an admin simulates a `DRAFT` or `SIMULATED` version over
a range of Mexico City dates that ends today at the latest and spans at most
`XOLOTL_MONITORING_SIMULATION_MAX_DAYS` days (190). The request writes a `QUEUED`
`rule_simulation` row, audits `rules.simulation_requested` and queues one
`monitoring.simulate` job, which is never retried. The job replays the tenant's
processed transactions of the range through that version alone, party by party, into
an in-memory aggregate store and the same evaluator processing runs, with
processing's dedup and cooldown, so it finds the hits processing would have stored for
that history. It writes nothing but its own row: no bucket, hit, notice, capture or
audit row. After each page of whole parties it saves its tallies (`transactions_read`,
`hit_count`, `customers_hit`, `hits_per_week` by the ISO week of each hit's last
instant, `overlap` with the hits already stored for the same parties and with the
parties found `TRUE_MATCH` after their simulated hits, and a `sample` of up to 50
ids-only hits) and a cursor, so a run that is started again resumes at the next
party. The job runs once, though: when its worker dies, `monitoring.sweep` finds
the row `QUEUED` or `RUNNING` with no live job (or, for a CLI run, which has no job,
a day old) and fails it with `WorkerLost`; staff request another. Windows start
cold at the range: history before it is not read, so a rule fires later in the
first days of the range than processing did, and a simulation shorter than four
months never fires `DECLARED_VS_ACTUAL_VOLUME` on its history branch. A party's
whole stream in the range is held in memory at once. Since parties are replayed
one at a time, a rule keyed on `COUNTERPARTY` or `DEVICE` (its key, its dedup key
or a declaration's) is refused with `VALIDATION_FAILED`: no library template is.
A "later true match" is a party whose status is `TRUE_MATCH` and whose last
screening came after its last simulated hit, so a party matched long ago and
screened again since counts too. On completion
the row is `COMPLETED` and the version becomes `SIMULATED` unless it was edited
meanwhile; a failure leaves it `FAILED` with the exception's class only. Either way
`monitoring.simulation_completed.v1` (staff-only) says so. A completed simulation is
valid for submission and approval for `XOLOTL_MONITORING_SIMULATION_MAX_AGE_DAYS` (30
days).

### Rules API

Staff and API keys with the `rules:read` scope read the library and the tenant's
rules; simulations, every write, every lifecycle step and the assignments are staff
only (an API key, whatever its scopes, gets 403 `ROLE_FORBIDDEN`). In a rule an API
key reads, each simulation keeps its status, range and dates, but its tallies,
weeks, overlap, sample and requester are null (even a count can tell which customer
a rule would hit), and no staff user id or `allowed_actions` is shown. For staff,
each version carries the `allowed_actions` and `blocked_reasons` of the caller, from
the same matrix the lifecycle enforces (an MLRO who edited a version is shown
`SAME_AUTHOR` on `APPROVE`). Every `POST` takes an `Idempotency-Key`; another
tenant's rule, version or simulation is 404.

| Route | Who | What |
|---|---|---|
| `GET /v1/monitoring/templates` · `GET /v1/monitoring/templates/{code}` | staff; API key `rules:read` | the latest version of each library template: family, kind, typology, packs, each parameter with its value, bounds, `tighten` direction, unit and source, the definition and the sentence in `es-MX` and `en`; 404 `RULE_TEMPLATE_UNKNOWN` |
| `GET /v1/monitoring/rules` · `GET /v1/monitoring/rules/{id}` | staff; API key `rules:read` | the tenant's rules, newest first with cursor pages, filtered by `kind`, `typology` and `status` (any version in it), each with every version (params, segment, severity, dedup, sentence, status and stamps, latest simulation, assignment), the active version and the last simulation |
| `POST /v1/monitoring/rules` | staff `SENIOR_ANALYST`, `MLRO`, `ADMIN` | `{template_code, params?, segment?, severity?, dedup?}` → 201 the `DRAFT` version (a new version when the tenant has the rule already); `{definition}` alone authors a custom rule (`ADMIN`, with `XOLOTL_MONITORING_ALLOW_CUSTOM_RULES`); 422 `RULE_PARAM_OUT_OF_BOUNDS` with the parameter, bound and source in `errors[]` |
| `POST /v1/monitoring/rules/{id}/versions` · `PATCH /v1/monitoring/rules/{id}/versions/{v}` | staff writers | a new `DRAFT` version from the latest one; an edit of a `DRAFT` or `SIMULATED` version (any change sends it back to `DRAFT`) |
| `POST /v1/monitoring/rules/{id}/versions/{v}/simulate` | staff writers | `{range_start, range_end}` → 202 `{simulation_id}`; 422 `SIMULATION_RANGE_TOO_LONG` |
| `GET /v1/monitoring/simulations/{id}` | staff | status, tallies, `hits_per_week`, `overlap`, `sample`, `valid_until` (the last day it counts for submission and approval) and `notes` (`PROFILE_HISTORY_SHORT` for a profile rule simulated over less than 120 days) |
| `POST /v1/monitoring/rules/{id}/versions/{v}/submit` · `approve` · `reject` · `activate` · `retire` | staff (the lifecycle checks the roles) | the version; `reject` takes `{reason}` (1 to 500 characters, not stored), `activate` `{effective_from, product?}`; 409 `RULE_TRANSITION_NOT_ALLOWED` with `blocked_reasons` |
| `GET /v1/monitoring/assignments?on=` | staff | the assignments in force on `on` (today by default), for every product, with the rule code |

### Rule hits

Processing evaluates the rules in force on each row's date inside the row's
database transaction, after its aggregates, so a hit commits or rolls back with the
row. A firing becomes a `rule_hit` keyed by its rule version and dedup key
`<key_kind>:<key>:<bucket>`: the bucket is the row's Mexico City month for a `MONTH`
dedup window, else `occurred_at` floored in UTC to the window (a `P7D` bucket is a
Monday-to-Sunday UTC week, `P1D` a UTC day). Another firing in the same bucket links
its transaction as `WINDOW` and counts it (`fired_count`); a firing after the key's
latest hit but before its `cooldown_until` (`occurred_at` plus `dedup.cooldown`, none
for `P0D`) links as `COOLDOWN`; anything else is a new hit. A new hit links its
trigger (`TRIGGER`) and the window's matching processed transactions (`WINDOW`, at
most 500, newest first), and its `evidence` keeps the aggregate values the rule read,
its parameters, the rendered es-MX sentence and the jurisdiction lists' hash: ids,
amounts, units and codes, never a name. It also records the policy snapshot, the
version's definition hash and the assignment. Replaying a stream, two sessions
firing at once and a late row that re-evaluates the party's later rows (out of order,
`reevaluate_later`) all leave one hit per version and dedup key. A rule whose
aggregate is not backfilled yet fires nothing.

`monitoring.rule_hit_created.v1`, like every other rules, captures, periods,
lifecycle and simulation event, is staff-only (`register_rules` marks them), so no
webhook ever carries one. The one tenant-facing event is
`monitoring.tier_limit_reached.v1` (`relationship_id`, `account_id`, `tier`,
`limit_code`, `period_key`, `outcome`, never an amount): a `TIER_LIMIT` firing makes a
`BLOCK` or `REVIEW` hit and a `tier_limit_notice` row, and only the call that inserts
that row, from processing or the decide path, publishes the event, once per
relationship, limit and month. In the developer documentation the event is the
"límite de nivel de cuenta" webhook, and nothing more: the customer is asked to raise
the account's KYC level.

Hits are staff only: an API key, whatever its scopes, is refused (403
`ROLE_FORBIDDEN`) on every route below, and a client's view of a transaction never
mentions one. Any staff role, `AUDITOR_RO` included, reads and reveals.

In the console, Hallazgos de reglas lists the hits by severity (it opens on the most
severe tab with a hit), filtered by rule, typology, outcome and age. A hit's detail
shows the rendered sentence, the aggregates it read beside its parameters, the
evidence transactions with their counterparties masked, the dedup key, the cooldown
and the decision, and reveals the party and the counterparties only on request
(audited). A transaction's detail lists the hits it is linked to and a customer's page
lists their hits. There is no disposition in SP-04: hits become alerts later.

| Route | Who | What |
|---|---|---|
| `GET /v1/monitoring/hits` | staff | the hit queue, newest first, the party masked (initials, masked identifiers); filters `severity`, `rule_code`, `typology`, `outcome`, `party_id`, `transaction_id` (the hits the transaction is linked to, in any link role), `created_before` |
| `GET /v1/monitoring/hits/{id}` | staff | the hit with its evidence (aggregates at firing time, parameters, explanation, lists hash), the rendered es-MX sentence, its provenance (assignment, policy snapshot, definition hash, dedup key), the triggering transaction's `decision` (answer, mode and time) when it had one, `transaction_count` and the newest 500 linked transactions with their role, newest first, each counterparty masked |
| `GET /v1/monitoring/hits/{id}/transactions` | staff | every transaction linked to the hit (trigger, window and cooldown links), newest first, with cursor pages |
| `GET /v1/monitoring/hits/{id}/reveal` | staff | the party's personal values and, in plaintext, the counterparties of the transactions the detail shows; `VIEW_PII` on the hit per group (`party`, `counterparties`), then the party and counterparty rows, all before decryption |
| `GET /v1/parties/{id}/hits` | staff | the party's hits, newest first |

### Captures

A capture is the legal fact "this transaction must appear in report R for period
P", recorded whether or not anything looks suspicious, so it never opens a hit and
never reaches an API client or a webhook. Captures are policy, not rules: each
capture of the transaction's policy snapshot (`ITF_ROR_CASH`, `ITF_FX_CASH`,
`ITF_INTL_TRANSFER`, `ITF_VA_OPS` under ITF; `AVI_XVI_A`, `AVI_XVI_B` under AVI)
brings its threshold, unit, conversion rule, window and period, and
`captures/triggers.yaml` says which transactions it looks at (cash, cash foreign
exchange, cross-border transfers, crypto). Processing runs them after the rules, in
the row's database transaction: a transaction at or above the threshold in the
threshold's unit (USD at the prior banking day's FIX, UDI or UMA on the day) gets a
`reportable_event` with its period key (`2026-12` or `2026-Q4`), pre-mapped report
fields (codes from the report's field map in `captures/field_maps/`, amounts, the
account reference, the counterparty's country, never a name, account or wallet) and
the unit values on both sides of the comparison. A fee capture compares the fee.

An accumulation capture (`AVI_XVI_A`, 210 UMA over six months from the first
operation) adds a relationship's operations below the threshold in a
`capture_group`; when the group crosses, every operation in it is captured, each
with its own month, and later operations of the window as they arrive. An operation
that also triggers a capture merging into it (`AVI_XVI_B`, a fee of 4 UMA or more)
yields one event naming both. A late operation that moves a group's window
earlier or its crossing point groups the relationship's operations again, in
order, as processing in order would have: replaced groups are superseded, the
events of every group that crosses are captured again in place (one row per
transaction and capture) and published as superseded and captured again, an event
whose operation no longer belongs to a crossed group is retired (and a fee capture
merged into it comes back), and a closed period touched is reopened
(`monitoring.capture_period_changed.v1`). Retention deletes groups left without
events once their window has ended.

#### Period close

Every event opens its capture's `capture_period` (`2026-12` for a monthly capture,
`2026-Q4` for a quarterly one). The monthly `monitoring.period_close` job
(`XOLOTL_MONITORING_PERIOD_CLOSE_CRON`, 04:00 on the 1st in Mexico City) first gives
every capture of every policy in force a row for each period that ended since the
tenant's first processed transaction, so a period without events is reconciled
too, then closes each open or reopened one whose last day has passed. The close
re-runs the capture over the period's processed transactions without writing
anything, each under its own policy snapshot (thresholds, fees and every member of
an accumulation window that crosses, the windows chained in order as processing
chains them), and compares the transactions it expects with the period's live
events: a missing event is written (or captured again in place) as late, an extra
one is voided (`void_reason = RECONCILIATION`) and published as superseded with no
successor, as a retired event is, and what merged into it is captured again. The period records
`reconciled_count`, `event_count`, the ids of what it found (`discrepancies`), becomes
`CLOSED` and publishes `monitoring.capture_period_closed.v1` with its
`discrepancy_count`. A discrepancy is a defect, not a business event: on clean data
the close finds nothing, and `xolotl monitoring period-close` exits 1 when it finds
one. Voiding an event a report already included, or changing a period closed
before, counts as a change (`change_count`, `monitoring.capture_period_changed.v1`)
so reporting regenerates. A late event in a closed period reopens it, and the next
close closes it again. Writers and the close lock the period row against each other
(writers `FOR KEY SHARE`, the close `FOR UPDATE`): a close waits for the events
being written and counts them, and an event written after it reopens the period.
Each period closes in its own transaction, so one that fails (logged; the job
retries) does not keep the others open. A close by hand (the API or the CLI) is
audited as `captures.period_closed` with the actor. On a first deploy every period
since the first processed transaction is created and closed; later runs start
after the last closed period, except for a capture that has never closed (a pack
assigned afterwards), whose periods are covered from the first processed
transaction.

| Route | Who | What |
|---|---|---|
| `GET /v1/monitoring/captures` | staff | reportable events, newest first; filters `capture_code`, `report_code`, `period_key`, `status`, `relationship_id`, `transaction_id` |
| `GET /v1/monitoring/captures/{id}` | staff | the event with its pre-mapped report fields, unit values and accumulation group (window, total, crossing, member count, the group that replaced it) |
| `GET /v1/monitoring/captures/periods` | staff | capture periods, newest period key first, with their reconciliation; filters `capture_code`, `status` |
| `POST /v1/monitoring/captures/periods/{id}/close` | staff MLRO, ADMIN | closes the period now; `409 PERIOD_NOT_ENDED` until its last day has passed |
| `GET /v1/monitoring/captures/effective?on=&product=` | staff | the effective policy's captures and tier monthly limits with threshold, unit, conversion rule, window, period, report, legal basis, confidence and source packs |

An API key never reaches these routes, whatever its scopes: a capture says a customer
is being reported (tipping-off).

## Connectors

A connector calls an external verification service (`sat_rfc`, `banxico_sie`) behind a
timeout, retries and a circuit breaker per connector and cell, and meters every call in
`connector_call`. A call runs in mode `SANDBOX` (the built-in mock, no network, no
credential read) or `LIVE`. A `SANDBOX` tenant always gets the mock, whatever the
caller asks for; a `PROD` tenant uses the mode its call names, or
`XOLOTL_CONNECTOR_MODE_DEFAULT` when it names none (`SANDBOX` by default, `LIVE` in the
Render blueprint).

The `banxico_sie` demo mode (no token outside prod) and its sandbox mock answer from
the committed seed files in `policy-packs/_units`, the same files the units page and
the demo unit fetcher read, and fall back to the constants `UDI = 8.500000` and
`USD_MXN_FIX = 18.500000` only on a day the seed does not cover.

The endpoints are for ADMIN staff only:

- `GET /v1/connectors` lists the connectors with their mode, routes, circuit state (of
  the process that answers) and the modes a credential is stored for.
- `PUT /v1/connectors/{name}/credentials/{mode}` with `{"fields": {"token": "..."}}`
  stores a tenant's credential encrypted under its data key, answers with the field
  names and never a value, and audits `connector.credential_set`. A `SANDBOX` credential
  is stored and listed, but no connector reads it yet.
- `GET /v1/connectors/usage?from=YYYY-MM-DD&to=YYYY-MM-DD` counts the tenant's calls,
  cost and outcomes per connector per UTC day (both days included, at most 366 days
  apart).

The raw answer of every call, credentials replaced by `[REDACTED]`, is stored as
evidence in the object store at `connectors/<connector>/<call id>`. In production,
add `XOLOTL_BANXICO_TOKEN` by hand in the Render dashboard; the blueprint does not set
it.

## Development

```bash
make ci        # ruff, ty, basedpyright, vulture, import-linter, pytest (coverage >= 90)
make test      # pytest only; needs Docker for the Postgres testcontainer
make format    # ruff --fix and ruff format
make perf      # the perf measurements; not in CI (SP-11 gates them under load)
```

Performance tests: `make perf` runs `tests/perf` (forward screen p95 at 50,000
entries, a list delta and a periodic re-screen of a 10,000-party base) and
appends the numbers to `.perf/sp03.json`. They carry the `perf` marker, which the
default run deselects, so they are not in CI; SP-11 gates them under load.

Tests run against a real Postgres 17 container with real commits; set
`XOLOTL_TEST_DATABASE_ADMIN_URL` to reuse an existing server instead. Point it at
a scratch database only: the suite bootstraps the app role (resetting its
password), migrates, and truncates every table before each test.

`uv run pytest tests/e2e/test_sp03_exit.py --no-cov` runs the SP-03 exit demo on its
own (it is part of `make ci`): two tenants, the five shared lists loaded from the
library's samples and tenant A's LPB, an explained `ALERT` for staff and status only
for an API client, a UN delta that alerts only on its added and changed entries and
suggests delisting the removed one, tenant B seeing nothing of tenant A, the
four-eyes review, no screening finding on any webhook, and both audit chains intact.

## Layout

`src/xolotl/platform/` is the shared kernel (imports nothing from modules);
`src/xolotl/modules/<name>/` are feature modules that own their tables, routers
and events; `src/xolotl/app.py` is the composition root; migrations are one
Alembic tree under `src/xolotl/migrations/`.
`console/` is the staff console (React, Vite); the Dockerfile builds it and copies
`console/dist` to `src/xolotl/static`, the directory the API serves at `/`.
