# SP-04 — Transaction monitoring and captures — Sub-project design

**Parent spec:** `docs/superpowers/specs/2026-10-18-program-architecture-design.md` (D1, D2, D3, D4, D5, D6, D8, D9, D10, D11, D12, D13, D19, D20, D21, D22; ports §4: `AggregateStore`, `ObjectStore`, `EventBus`, `JobQueue`, `UnitValueSource` (through `UnitService`), `Clock`, `Notifier`; SP-04 row of §7.1, §7.2 "monitoring before cases", and the SP-05 demo of §7.3, whose first clause, "transactions in; captures", is SP-04's exit).
**Product sources (Notion, Xolotl Docs):** PRD-05 Transaction monitoring (`FR-TM-001`..`018`, `NFR-TM-001`..`006`), TS-05 Transaction monitoring (`DR-501`..`508`, `INT-501`..`505`, `SEC-501`, `PRV-501`..`503`, `OPS-501`..`504`), ADR-005 Rule expression language, ADR-012 Stream processing for monitoring (MVP note), ADR-017 Transaction history and analytics store (MVP note), ADR-021 Monetary units and threshold conversion rules, ADR-003 Rulebook format, ADR-004 Adding a country, ADR-022 MVP runtime profile, DD-05 Rulebook, rules and calendar admin, DD-00 Console shell. Tasks XOL-7 (rule expression language), XOL-23 (ingest, enrichment, monitoring consumer with aggregates and alerts), XOL-24 (Mexico reportable-event captures, tier limits and late events), XOL-36 (synchronous decision API, rule simulation, effectiveness metrics and behaviour profiles) and XOL-110 (this spec). SP-03's spec §1, §8 and §10 for what it deferred here: `FR-SC-012`, `DR-308` (counterparty subjects and the synchronous counterparty cache) and `FR-SC-015` (crypto-wallet exact matching).
**Status:** draft 2026-10-20 for the product owner's review. Builds on SP-01, SP-02 and SP-03 as merged on `main` at `e084143` (migrations 0001 to 0010; the party core, the policy resolver and snapshots, `UnitService`, the calendar, the screening engine and its `screen()` signature, staff-only event types, the SP-03 exit demo `tests/e2e/test_sp03_exit.py`). The reporting serializers library (`src/xolotl/modules/reporting/serializers/`, built in parallel for SP-05) is on `main` too; SP-04 produces the rows SP-05 will map onto its `F41Operation` and aviso inputs, and does not import it.

## 1. Scope

In scope:
- **Canonical transaction schema** (D11): the `transaction` table partitioned monthly by `occurred_at`, the unpartitioned `transaction_ref` idempotency table, link tables for every reference, the `account` and `counterparty` rows a transaction resolves to, the encrypted raw payload, and the `RECEIVED → PROCESSED` life of a row with `PARKED` and `BACKFILL` side paths.
- **Batch and file ingest**: `POST /v1/transactions` (up to 1,000 canonical items, all-or-nothing, idempotent on `external_id`) and `POST /v1/transactions/files` (CSV, JSON Lines or XLSX through the object store, processed by a job with a row-level error report), both producing the same rows and the same per-key processing jobs.
- **Enrichment**: party, relationship, account and tier from `customer_ref` and `account_ref`; counterparty resolution with a blind index; amounts in every threshold unit of the effective policy (`UDI`, `UMA`, `USD` at the prior banking day) through SP-02's `UnitService`, each with the `unit_value.id` it used; FX context; business-day flag and period keys from the calendar; the counterparty's screening status; the effective policy snapshot for the transaction's date and product.
- **Aggregates** (D12): `agg_bucket` rows (hourly up to 7 days, daily up to 6 months, calendar month and quarter, and the LFPIORPI six-months-from-first accumulator) updated in the processing transaction of each transaction, keyed per party, relationship, account, counterparty or device; exact distinct counts through a member table; per-key ordering by a per-key job lease and a drain loop; the 7-day late-event rule with a backfill path beyond it.
- **Rule kinds, versions and assignments**: the six ALERT kinds from sentinel (`THRESHOLD`, `AGGREGATE`, `STRUCTURING`, `VELOCITY`, `HIGH_RISK_JURISDICTION`, `PROFILE_DEVIATION`) plus `TIER_LIMIT`, fed from aggregates (D22, D13); CAPTURE kinds (`THRESHOLD`, `ACCUMULATION`, `FEE_THRESHOLD`) read from the effective policy's `captures`; a shared typology library loaded from `policy-packs/MX/_typologies/` as pack content; tenant rule versions with the D19 lifecycle (`DRAFT, SIMULATED, IN_REVIEW, APPROVED, ACTIVE, RETIRED`), parameters within declared bounds, an approver different from the author, and assignments resolved by date and product like pack assignments.
- **Rule hits with evidence** (D13): `rule_hit` rows deduplicated by `(rule_version_id, dedup_key)` with cooldown, `rule_hit_transaction` links, aggregate values at firing time, exposed through a staff API and page. SP-04 stores and exposes hits; cases turn them into alerts in SP-06.
- **Captures**: `reportable_event` rows per transaction per capture with period keys, capture groups for accumulation, supersession on late events, pre-mapped report fields and the unit values used; `capture_period` rows with the period-close reconciliation against the transaction table; events reporting consumes in SP-05.
- **Synchronous decide API and tier limits**: `POST /v1/transactions/decide` returning `ALLOW`, `REVIEW` or `BLOCK` from indexed point reads of `agg_bucket` and the relationship profile, decisions stored in a monthly-partitioned `txn_decision` table and linked to the later transaction by `external_id`; ITF N1 and N2 monthly-operations limits in UDI with the limit-reached webhook; fail-open or fail-closed per rail.
- **Simulation**: a job that replays the tenant's Postgres history per key in `occurred_at` order through the pure evaluator into in-memory aggregates, writing one `rule_simulation` row (alerts per week, customers hit, overlap with existing hits, a sample of hit references) and nothing else; approval requires a simulation under 30 days old.
- **Behaviour profiles**: monthly `behaviour_profile` rows per relationship compared with the declared profile, with a deviation event risk scoring consumes in SP-07.
- **Counterparty screening** (from SP-03): `SubjectKind.COUNTERPARTY`, `counterparty_name_index`, asynchronous screening on first sight, reverse screening on list deltas, and the `counterparty` row as the synchronous cache the decide path and the enricher read; **crypto-wallet exact matching** through `DIGITAL_CURRENCY_ADDRESS` identifiers.
- **API, events, CLI and console**: both audiences (D20) with tipping-off protection (no hit, capture or rule detail ever reaches the API client of the customer concerned), `xolotl monitoring …` and `xolotl rules …` commands, and the pages: Transacciones (list, detail, batches, files, parked), Hallazgos de reglas (hit queue and detail with evidence), Reglas › Monitoreo (library, tenant versions, parameter editor, simulation, approval), Reglas › Capturas y límites (read-only with period reconciliation), Contrapartes, and the Cliente › Transacciones and Perfil tabs.

Out of scope (later sub-projects, or not in the MVP profile): the rule expression grammar, compiler and type checker of ADR-005 and XOL-7 (Decision 1; parameterised kinds only, the grammar is §21 Q1); Kafka topics `txn.raw` and `txn.enriched`, the embedded key-value state, checkpoints to object storage and the hot-aggregate cache of ADR-012 (its MVP note puts aggregates in Postgres, which this spec builds); the Parquet and Iceberg lake of ADR-017 (its MVP note keeps everything in Postgres with no cut-off); streaming ingest (`FR-TM-001` names it; batches and files are the MVP); balance feeds and `max_balance` limits (`FR-TM-017`, SHOULD, not in MVP per TS-05; the N1 1,000 UDI balance limit is deferred with it); Brazil Pix hooks (`FR-TM-015`, v1.1); anomaly scores, graph analytics and alert prioritisation (phase 2); alerts, cases and the alert-to-case and case-to-report rates (`FR-TM-012` metrics that need cases come in SP-06; SP-04 publishes the rates it can compute); the attestation document of `FR-TM-014` (SP-10 produces it from the rules and captures SP-04 records); risk rating and monitoring profile as rule inputs (SP-07; until then the enricher records `risk_rating = NULL` and `monitoring_profile = 'STD'`, program spec §7.2); per-client ingest rate limits and 429 (TS-05 §7; SP-11 adds the limiter with the load test); the rule simulation UI beyond the panel of §17 (program spec §7.4 cut list keeps the API); the licensed sanctions and PEP feeds (SP-03 §18 Q1).

### 1.1 What depends on what

| Part | Needs | Can start |
|---|---|---|
| Canonical schema, `transaction_ref`, accounts, counterparties, batch and file ingest, enrichment, aggregates, processing jobs, transactions API and pages (§5.2 first block, §6, §7, §8) | party core (`customer_relationship`, `party`), `UnitService`, calendar, `ObjectStore`, jobs | now; the screening status of a counterparty is `UNSCREENED` until plan 4c lands |
| Rule library, rule versions, simulation, assignments, evaluator, hits, captures, period close, tier limits, hits and captures API and the Reglas pages (§5.2 second block, §9, §10, §13.1) | plan 4a's rows and aggregates | the evaluator and the library loader are pure and can start now; the rest after 4a |
| Counterparty screening (subjects, runs, re-screening, wallets, Contrapartes), decide API, rail settings, replay, metrics, retention, behaviour profiles, exit demo (§5.2 third block, §11.2 to §11.4, §12, §13.2, §13.3) | 4a and 4b; the screening engine's `screen()`, index holder and blocking query | counterparty screening can start after 4a, beside 4b; the rest after 4b |

## 2. Toolchain and conventions

- **Module layout.** One module, `monitoring`, as in the program spec §3.3 (`monitoring: platform, party, screening`); counterparties live in `screening` (a counterparty is a screening subject: its row, its name index, its resolution and its status cache are screening's, Decision 18), under a new `screening/counterparty/` package that plan 4a creates (schema, rows, resolution, status cache), plan 4b's migration extends (the name index) and plan 4c completes (subjects, runs, re-screening, wallets). `monitoring` calls `screening.counterparty.resolve()` and `status_by_dedup_hash()` and keeps only `transaction.counterparty_id`; `screening` never imports `monitoring`.

  ```
  src/xolotl/modules/monitoring/
  ├── txn/          canonical schema (Pydantic), models, transaction_ref, accounts, batch ingest, file ingest, parking
  ├── enrich/       customer and account resolution, unit amounts, periods, snapshot (counterparties through screening.counterparty)
  ├── aggregates/   AggregateStore port, PostgresAggregateStore, MemoryAggregateStore (simulation), declarations, windows, buckets, backfill
  ├── rules/        kinds (pure evaluator), definitions, library loader (typologies), versions, lifecycle, assignments, hits
  ├── captures/     capture definitions from the policy, reportable events, groups, supersession, period close
  ├── process/      the per-key processing job (drain loop), late events, backfill, sweep
  ├── decide/       synchronous decisions, rail settings, txn_decision
  ├── simulation/   replay job, rule_simulation
  ├── profiles/     behaviour profiles, metrics
  ├── routes_transactions.py · routes_rules.py · routes_hits.py · routes_captures.py · routes_decide.py · routes_monitoring.py
  ├── schemas.py · events.py · errors.py · enums.py · composition.py
  src/xolotl/modules/screening/counterparty/   schema, rows, resolution, dedup hash, status read (4a); name-index table (4b, migration 0012); subjects, runs, first-sight and delta screening, wallet identifiers, Contrapartes (4c)
  policy-packs/MX/_typologies/*.yaml           the Mexico typology library (rule templates with parameter bounds), §9.3
  ```
- **Import-linter contracts (added).** `monitoring.rules.kinds`, `monitoring.aggregates.windows` and `monitoring.aggregates.memory` are pure: they import only the standard library, `pydantic`, `xolotl.platform.errors`, `xolotl.platform.enums`, `xolotl.platform.units.rules`, `xolotl.platform.policy.jurisdictions` (types only: `JurisdictionLists`), `monitoring.txn.schema` and, for `CountryCode`, `xolotl.modules.party.schemas` (a types module without database access); never `sqlalchemy`, `fastapi` or another module. This is what makes the evaluator "a pure function over (transaction, aggregates, profile, policy)" (D12) and lets simulation run it unchanged. `monitoring` may import `xolotl.modules.party` and `xolotl.modules.screening` service functions only; it never imports `xolotl.modules.reporting`.
- **Partitions.** `xolotl.platform.audit.partitions` is generalised into `xolotl.platform.partitions` (`ensure_partitions(conn, *, table, months_ahead, start)` keeps its name and gains `table`; `harden_partition`; the advisory lock per table); `audit_event` keeps using it unchanged. Migration 0011 creates `transaction` and `transaction_ref`, 0013 creates `txn_decision`, both with a `DEFAULT` partition and three months ahead; `xolotl db upgrade` and a daily job keep partitions ahead (§3).
- **Dependencies.** No new runtime dependency: `openpyxl` (read-only XLSX, SP-03) and `pyyaml` (typologies, SP-02) are present. No Postgres extension is added (distinct counts are exact, §8.3).
- **Migrations.** `0011_transactions.py` (plan 4a: accounts, counterparties, transactions, refs, batches, files, parked rows, aggregates), `0012_rules_captures.py` (plan 4b: rule templates, rules, versions, simulations, assignments, hits, tier-limit notices, captures, periods, counterparty subjects and name index), `0013_decide_profiles.py` (plan 4c: rail settings, decisions, behaviour profiles, `customer_relationship.declared_profile`). One migration per plan, as before.
- All SP-01 to SP-03 conventions bind: RLS and isolation factories, `tenant_fk`, `v1_router`, `require_access`, `Idempotency-Key` on POSTs, `Encrypted` fields with `reveal()` only in service code, `VIEW_PII` when a human sees revealed values, ids-only event payloads without floats (amounts travel as minor-unit integers or decimal strings), UPPERCASE non-native enums emitted to TypeScript, Google-style docstrings, golden tests that print a diff, the `perf` marker for non-gating latency records.

## 3. Configuration (prefix `XOLOTL_`)

| Key | Default | Purpose |
|---|---|---|
| `MONITORING_BATCH_MAX_ITEMS` | `1000` | items per `POST /v1/transactions`; more is 413 `TRANSACTION_BATCH_TOO_LARGE` |
| `MONITORING_FILE_MAX_MB` / `MONITORING_FILE_MAX_ROWS` | `50` / `500000` | file upload limits |
| `MONITORING_FILE_CHUNK_ROWS` | `1000` | rows per file-ingest job page (one database transaction per page) |
| `MONITORING_LATE_DAYS` | `7` | `received_at − occurred_at` up to this is late (re-evaluated in place); beyond it the row waits for a backfill (§8.5) |
| `MONITORING_MAX_FUTURE_MINUTES` | `10` | `occurred_at` later than now plus this is a field error |
| `MONITORING_PROCESS_LEASE_SECONDS` | `300` | lease of a `monitoring.process` job; heartbeat every 30 s |
| `MONITORING_PROCESS_PAGE` | `200` | transactions a processing job handles before re-checking its lease and the queue |
| `MONITORING_SWEEP_CRON` | `* * * * *` | per-tenant `monitoring.sweep`: enqueues processing for keys with `RECEIVED` rows older than 30 s, retries `PARKED` rows whose unit value has arrived |
| `MONITORING_RULE_CACHE_SECONDS` | `30` | per-process cache of a tenant's active rule set (keyed by the latest `rule_version.activated_at`) |
| `MONITORING_AGG_BACKFILL_BATCH` | `5000` | transactions per `monitoring.agg_backfill` job page when a new declaration needs history |
| `MONITORING_DECIDE_BUDGET_MS` | `80` | database and evaluation budget of a decision; over it the rail's fail mode applies |
| `MONITORING_DECIDE_FAIL_MODE` | `FAIL_OPEN` | default for rails without a `rail_setting` row (§12.1; §21 Q4) |
| `MONITORING_SIMULATION_MAX_DAYS` | `190` | longest simulation range (six months and a week) |
| `MONITORING_SIMULATION_MAX_AGE_DAYS` | `30` | a version is approvable only with a simulation younger than this (`FR-TM-011`) |
| `MONITORING_PROFILE_CRON` | `0 3 2 * *` | monthly `monitoring.profiles` job (`America/Mexico_City`), the 2nd so the 1st's late events are in |
| `MONITORING_PERIOD_CLOSE_CRON` | `0 4 1 * *` | monthly `monitoring.period_close` (closes last month, and last quarter on 1 January, April, July and October) |
| `MONITORING_PARTITIONS_CRON` / `MONITORING_PARTITION_MONTHS_AHEAD` | `0 1 * * *` / `3` | partition maintenance for `transaction` and `txn_decision` |
| `MONITORING_COUNTERPARTY_SCREEN_BATCH` | `100` | counterparties per `screening.screen_counterparties` job |
| `MONITORING_ALLOW_CUSTOM_RULES` | `false` | lets ADMIN author a rule from a bare kind without a template (§9.4, §21 Q2) |
| `MONITORING_PROFILE_DEVIATION_RATIO` | `2.0` | actual over declared ratio at or above which a behaviour profile is `deviated` (§13.3, §21 Q3) |
| `MONITORING_RETENTION_CRON` | `0 5 1 * *` | monthly `monitoring.retention`: drops `transaction` and `txn_decision` partitions past `retention.transactions`, purges orphaned counterparties (§8.6) |

## 4. The canonical transaction (`monitoring/txn/schema.py`)

One Pydantic model for the API batch, each file row and the decide request, validated with field-level errors (`FR-TM-001`, `INT-501`). Field names are normative; `raw` keeps whatever else the client sent.

```python
class Direction(StrEnum): IN = "IN"; OUT = "OUT"; INTERNAL = "INTERNAL"
class Rail(StrEnum): SPEI = "SPEI"; CODI = "CODI"; CASH = "CASH"; CARD = "CARD"; WALLET = "WALLET"; SWIFT = "SWIFT"; CRYPTO_ONCHAIN = "CRYPTO_ONCHAIN"; OTHER = "OTHER"
class Instrument(StrEnum): CASH = "CASH"; TRANSFER = "TRANSFER"; CARD = "CARD"; CHEQUE = "CHEQUE"; CRYPTO = "CRYPTO"; FX = "FX"; FEE = "FEE"
class Channel(StrEnum): APP = "APP"; WEB = "WEB"; API = "API"; BRANCH = "BRANCH"; ATM = "ATM"; AGENT = "AGENT"; OTHER = "OTHER"
# CounterpartyKind and CounterpartyInput are defined in screening/counterparty/schema.py and imported here (Decision 18): the
# dependency runs monitoring -> screening only
class CounterpartyKind(StrEnum): PERSON = "PERSON"; ORGANIZATION = "ORGANIZATION"; UNKNOWN = "UNKNOWN"

class CounterpartyInput(BaseModel):           # every field optional; at least one of name, account, wallet_address
    kind: CounterpartyKind = CounterpartyKind.UNKNOWN
    name: str | None                          # PII: encrypted at rest, blind-indexed
    account: str | None                       # CLABE, card token, IBAN, …; PII: encrypted, blind-indexed
    institution: str | None                   # bank code or name as sent (not PII)
    country: CountryCode | None               # ISO 3166-1 alpha-2
    wallet_address: str | None; wallet_network: str | None     # case preserved; blind-indexed; FR-SC-015
    identifier: AdHocIdentifier | None        # RFC, CURP, passport … (the SP-03 ad hoc identifier shape)

class CryptoContext(BaseModel): network: str; tx_hash: str | None; address: str | None; self_custody: bool | None; asset_code: str; asset_qty: Decimal   # address = the counterparty wallet when `counterparty.wallet_address` is absent; stored only in counterparty and raw_enc
class FxContext(BaseModel): from_ccy: str; to_ccy: str; rate: Decimal; from_amount_minor: int; to_amount_minor: int
class DeviceContext(BaseModel): device_id: str | None; ip: str | None; geo_country: CountryCode | None; geo_admin_area: str | None   # device_id is hashed on storage; the IP stays in raw_enc; geo codes are regions, not PII, and are stored plain

class TransactionInput(BaseModel):            # extra = "forbid" at the top level; `raw` carries extras explicitly
    external_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime                     # aware; naive is a field error; up to MONITORING_MAX_FUTURE_MINUTES ahead
    customer_ref: str                         # customer_relationship.external_ref; unknown is a field error CUSTOMER_REF_UNKNOWN
    account_ref: str | None = None            # account.external_ref; created on first sight under the relationship
    direction: Direction; rail: Rail; instrument: Instrument
    amount: Decimal = Field(gt=0, decimal_places=2); currency: str = Field(pattern=r"^[A-Z]{3}$")   # fiat amount; crypto in `crypto`
    counterparty: CounterpartyInput | None = None
    channel: Channel = Channel.OTHER; device: DeviceContext | None = None
    crypto: CryptoContext | None = None; fx: FxContext | None = None
    purpose_code: str | None = None; fee_amount: Decimal | None = None      # fee for AVI_XVI_B
    raw: dict[str, JsonValue] = {}            # client extras; encrypted at rest with the counterparty input (PRV-501)

class BatchRequest(BaseModel): transactions: list[TransactionInput] = Field(min_length=1, max_length=MONITORING_BATCH_MAX_ITEMS)
class FieldError(BaseModel): index: int; field: str; code: str      # codes: REQUIRED, INVALID, OUT_OF_RANGE, CUSTOMER_REF_UNKNOWN, ACCOUNT_REF_CONFLICT, DUPLICATE_IN_BATCH, FUTURE, NAIVE_DATETIME, CONTEXT_REQUIRED (crypto or fx missing), COUNTERPARTY_EMPTY, TOO_LONG
```

Validation rules: `external_id` unique within the batch (`DUPLICATE_IN_BATCH`); `instrument = CRYPTO` requires `crypto`; `instrument = FX` requires `fx`; `rail = CRYPTO_ONCHAIN` requires `crypto.network`; `account_ref` already bound to another relationship is `ACCOUNT_REF_CONFLICT`; the whole batch is refused with 422 `TRANSACTION_BATCH_INVALID` and `errors: FieldError[]` when any item fails (all-or-nothing per batch; files differ, §6.2). A re-sent `external_id` with the same content hash is accepted and reported as `duplicate` (idempotent); the same `external_id` with different content is `EXTERNAL_ID_CONFLICT` (a transaction is never edited; a correction is a new `external_id` with `raw.corrects`, §21 Q6).

## 5. Tables

### 5.1 Shared reference tables (no `tenant_id`; added to `SHARED_TABLES`; migration 0012)

```sql
CREATE TABLE rule_template (code text PRIMARY KEY,              -- CASH_STRUCTURING, RAPID_PASS_THROUGH, … (§9.3)
  family text NOT NULL,                                        -- ALERT (CAPTURE kinds come from the policy, not templates)
  kind text NOT NULL,                                          -- THRESHOLD, AGGREGATE, STRUCTURING, VELOCITY, HIGH_RISK_JURISDICTION, PROFILE_DEVIATION, TIER_LIMIT
  title text NOT NULL, typology text[] NOT NULL,               -- STRUCTURING, MULE, VELOCITY, JURISDICTION, CRYPTO, DORMANCY, PROFILE, …
  country_code char(2) NOT NULL, packs text[] NOT NULL);       -- pack codes it applies to, e.g. {MX/ITF-IFPE, MX/LFPIORPI-AVI}

CREATE TABLE rule_template_version (id uuid PRIMARY KEY, template_code text NOT NULL REFERENCES rule_template(code),
  version int NOT NULL, definition jsonb NOT NULL,             -- RuleDefinition (§9.1): kind, on, params with defaults and bounds, dedup, evidence, severity, sync
  definition_hash bytea NOT NULL,                              -- sha256 of canonical JSON; immutable per version
  loaded_at timestamptz NOT NULL DEFAULT now(), loaded_by text NOT NULL, source_path text NOT NULL,
  UNIQUE (template_code, version));
-- seeded from policy-packs/MX/_typologies/*.yaml by `xolotl rules load` (owner role); `xolotl db upgrade` runs it; a changed hash for an
-- existing version is an error (versions are immutable, as pack versions are). App role: SELECT.
```

### 5.2 Tenant tables (forced RLS, isolation factories; PK `(tenant_id, id)` unless stated)

Migration 0011 (plan 4a):

```sql
CREATE TABLE account (tenant_id uuid NOT NULL, id uuid NOT NULL, relationship_id uuid NOT NULL, party_id uuid NOT NULL,
  external_ref text NOT NULL, kind text NOT NULL DEFAULT 'DEFAULT',     -- DEFAULT (one per relationship when the client sends no account_ref; external_ref 'default:<relationship_id>'), EMONEY, WALLET, CARD, OTHER
  currency char(3), status text NOT NULL DEFAULT 'ACTIVE',               -- ACTIVE, CLOSED
  first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, external_ref),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

-- counterparty is a screening table (Decision 18): created here because 0011 is plan 4a's migration, owned by screening/counterparty/
CREATE TABLE counterparty (tenant_id uuid NOT NULL, id uuid NOT NULL,
  kind text NOT NULL,                                          -- PERSON, ORGANIZATION, UNKNOWN
  name_enc bytea, name_hash bytea,                             -- EncryptedStr; HMAC(index key, normalized name) (D6, PRV-501)
  account_enc bytea, account_hash bytea, institution text, country char(2),
  wallet_enc bytea, wallet_hash bytea, wallet_network text,    -- address kept case-sensitive inside the ciphertext
  identifier_type text, identifier_enc bytea, identifier_hash bytea,
  dedup_hash bytea NOT NULL,                                   -- HMAC over (name_hash, account_hash, wallet_hash, identifier_hash): one row per distinct counterparty
  screening_status text NOT NULL DEFAULT 'UNSCREENED',         -- UNSCREENED, CLEAR, PENDING_REVIEW, TRUE_MATCH (the synchronous cache, DR-308)
  screened_at timestamptz, screening_subject_id uuid,         -- composite FK added in 0012 when screening_subject gains counterparty_id
  first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), txn_count bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, dedup_hash),
  CHECK (name_hash IS NOT NULL OR account_hash IS NOT NULL OR wallet_hash IS NOT NULL OR identifier_hash IS NOT NULL));
CREATE INDEX ix_counterparty_name_hash ON counterparty (tenant_id, name_hash);
CREATE INDEX ix_counterparty_wallet_hash ON counterparty (tenant_id, wallet_hash);

CREATE TABLE transaction_batch (tenant_id uuid NOT NULL, id uuid NOT NULL,
  source text NOT NULL,                                        -- API, FILE, CLI
  file_id uuid, submitted_by text NOT NULL,                    -- 'api_key:<id>', 'staff:<id>', 'cli:<user>'
  item_count int NOT NULL, accepted int NOT NULL, duplicates int NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(), request_id text,
  PRIMARY KEY (tenant_id, id));

CREATE TABLE transaction_file (tenant_id uuid NOT NULL, id uuid NOT NULL,
  storage_key text NOT NULL, sha256 bytea NOT NULL, size bigint NOT NULL, content_type text NOT NULL,   -- sealed with the tenant DATA key, stored at transactions/files/<id>
  status text NOT NULL,                                        -- UPLOADED, PROCESSING, COMPLETED, FAILED
  rows_total int, rows_accepted int NOT NULL DEFAULT 0, rows_duplicate int NOT NULL DEFAULT 0, rows_rejected int NOT NULL DEFAULT 0,
  cursor int NOT NULL DEFAULT 0, errors_key text,              -- transactions/files/<id>/errors.csv (row, column, code; never a cell value)
  uploaded_by text NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, last_error text,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, sha256));

CREATE TABLE transaction (tenant_id uuid NOT NULL, occurred_at timestamptz NOT NULL, id uuid NOT NULL,
  external_id text NOT NULL, received_at timestamptz NOT NULL DEFAULT now(), batch_id uuid NOT NULL,
  party_id uuid NOT NULL, relationship_id uuid NOT NULL, account_id uuid NOT NULL, product text,
  direction text NOT NULL, rail text NOT NULL, instrument text NOT NULL, channel text NOT NULL,
  amount_minor bigint NOT NULL, currency char(3) NOT NULL,     -- fiat; minor units of `currency`
  fee_minor bigint,
  asset_qty numeric(28,10), asset_code text,                   -- crypto
  unit_amounts jsonb,                                          -- keyed by "<unit>/<rule>": {"UDI/SAME_DAY": {"amount": "1234.567890", "unit_value_id": 812, "resolved_date": "2026-12-10"}, "UDI/LAST_DAY_PREV_MONTH": {...}, "USD/PRIOR_BANKING_DAY": {...}, "UMA/SAME_DAY": {...}, "MXN/SAME_DAY": {...}} (Decision 19)
  counterparty_id uuid, counterparty_country char(2), counterparty_screening_status text,   -- status at processing time
  device_hash bytea, geo_country char(2), geo_admin_area text,
  crypto jsonb, fx jsonb, purpose_code text,                   -- crypto: {network, tx_hash, self_custody, asset_code, asset_qty}; no address (it is the counterparty's wallet, encrypted there and in raw_enc; Decision 20)
  period_keys jsonb,                                           -- {"DAY":"2027-02-03","MONTH":"2027-02","QUARTER":"2027-Q1","BUSINESS_DAY":true} in America/Mexico_City
  tier text, risk_rating text, monitoring_profile text NOT NULL DEFAULT 'STD',
  policy_snapshot_id uuid,                                     -- the effective policy for (occurred_at date, product); set at processing
  status text NOT NULL DEFAULT 'RECEIVED',                     -- RECEIVED, PROCESSED, PARKED, BACKFILL
  late boolean NOT NULL DEFAULT false, processed_at timestamptz,
  decision_id uuid, decided_at timestamptz,                    -- composite FK to txn_decision added by 0013 (§12.1)
  content_hash bytea NOT NULL,                                 -- sha256 of the canonical input; idempotent re-sends compare it
  raw_enc bytea NOT NULL,                                      -- EncryptedJson of the submitted item (counterparty input included)
  PRIMARY KEY (tenant_id, occurred_at, id),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id),
  FOREIGN KEY (tenant_id, account_id) REFERENCES account (tenant_id, id),
  FOREIGN KEY (tenant_id, counterparty_id) REFERENCES counterparty (tenant_id, id),
  FOREIGN KEY (tenant_id, batch_id) REFERENCES transaction_batch (tenant_id, id),
  FOREIGN KEY (tenant_id, policy_snapshot_id) REFERENCES policy_snapshot (tenant_id, id)
) PARTITION BY RANGE (occurred_at);                            -- transaction_default plus monthly partitions, each hardened (forced RLS)
CREATE INDEX ix_transaction_party_time ON transaction (tenant_id, party_id, occurred_at DESC);
CREATE INDEX ix_transaction_relationship_time ON transaction (tenant_id, relationship_id, occurred_at DESC);
CREATE INDEX ix_transaction_counterparty ON transaction (tenant_id, counterparty_id, occurred_at DESC);
CREATE INDEX ix_transaction_pending ON transaction (tenant_id, party_id, occurred_at) WHERE status IN ('RECEIVED', 'PARKED');
CREATE INDEX ix_transaction_period ON transaction (tenant_id, (period_keys->>'MONTH'), instrument);

CREATE TABLE transaction_ref (tenant_id uuid NOT NULL, external_id text NOT NULL, transaction_id uuid NOT NULL, occurred_at timestamptz NOT NULL,
  content_hash bytea NOT NULL,
  PRIMARY KEY (tenant_id, external_id),                        -- the idempotency key (D11); written in the same transaction as the row
  FOREIGN KEY (tenant_id, occurred_at, transaction_id) REFERENCES transaction (tenant_id, occurred_at, id));

CREATE TABLE transaction_parked (tenant_id uuid NOT NULL, transaction_id uuid NOT NULL, occurred_at timestamptz NOT NULL,
  reason text NOT NULL,                                        -- UNIT_VALUE_MISSING, POLICY_NOT_ASSIGNED
  unit_code text, needed_on date, attempts int NOT NULL DEFAULT 0, parked_at timestamptz NOT NULL DEFAULT now(), last_error text,
  PRIMARY KEY (tenant_id, transaction_id),
  FOREIGN KEY (tenant_id, occurred_at, transaction_id) REFERENCES transaction (tenant_id, occurred_at, id));

CREATE TABLE agg_bucket (tenant_id uuid NOT NULL, key_kind text NOT NULL,   -- PARTY, RELATIONSHIP, ACCOUNT, COUNTERPARTY, DEVICE
  key text NOT NULL,                                           -- the uuid as text, or the hex of counterparty.dedup_hash / transaction.device_hash
  agg_id text NOT NULL,                                        -- 16-hex-char hash of the declaration (function, measure, where, window), §8.2
  bucket_kind text NOT NULL,                                   -- HOUR, DAY, MONTH, QUARTER, ACCUM
  bucket_start timestamptz NOT NULL,                           -- hour or day start (UTC); month and quarter start (America/Mexico_City); ACCUM: first operation's day
  bucket_end timestamptz,                                      -- ACCUM only: bucket_start + window (P6M); a later transaction starts a new accumulator
  value numeric(28,10) NOT NULL DEFAULT 0, count bigint NOT NULL DEFAULT 0,
  max_value numeric(28,10), min_value numeric(28,10), first_at timestamptz, last_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, key_kind, key, agg_id, bucket_start));
CREATE INDEX ix_agg_bucket_window ON agg_bucket (tenant_id, key_kind, key, agg_id, bucket_kind, bucket_start DESC);   -- the decide path's point read

CREATE TABLE agg_distinct (tenant_id uuid NOT NULL, key_kind text NOT NULL, key text NOT NULL, agg_id text NOT NULL,
  bucket_start timestamptz NOT NULL, member_hash bytea NOT NULL,   -- HMAC(index key, member): a counterparty, device, country, …
  PRIMARY KEY (tenant_id, key_kind, key, agg_id, bucket_start, member_hash));   -- DISTINCT_COUNT = COUNT(*) over the window's buckets (Decision 6)

CREATE TABLE agg_declaration (tenant_id uuid NOT NULL, agg_id text NOT NULL,
  declaration jsonb NOT NULL,                                  -- AggregateDeclaration canonical JSON
  bucket_kind text NOT NULL, window_kind text NOT NULL, window text NOT NULL,   -- as AggregateDeclaration: window_kind DURATION|MONTH|QUARTER|ACCUM, window an ISO-8601 duration ('P6M' for ACCUM) or the period name
  backfilled_through timestamptz,                              -- NULL until monitoring.agg_backfill has walked the window's history (§8.4)
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, agg_id));
```

A link to a transaction always carries `(tenant_id, transaction_id, occurred_at)` with the composite FK to the partitioned primary key (D11); `transaction.raw_enc` is the only copy of the counterparty's name and account outside `counterparty`; staff reveal it with `VIEW_PII`, nothing serialises it otherwise. Partition rows carry forced RLS through `harden_partition` like `audit_event`'s, and the isolation harness discovers partitions as tables.

Migration 0012 (plan 4b):

```sql
CREATE TABLE monitoring_rule (tenant_id uuid NOT NULL, id uuid NOT NULL,
  code text NOT NULL,                                          -- the template code, or a tenant code for a rule authored from a kind (§9.4)
  family text NOT NULL, kind text NOT NULL, template_code text REFERENCES rule_template(code),
  title text NOT NULL, created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, code),
  FOREIGN KEY (tenant_id, created_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE monitoring_rule_version (tenant_id uuid NOT NULL, id uuid NOT NULL, rule_id uuid NOT NULL,
  version int NOT NULL, template_version_id uuid REFERENCES rule_template_version(id),
  definition jsonb NOT NULL,                                   -- RuleDefinition with the tenant's params and segment (§9.1); params within the template's bounds
  definition_hash bytea NOT NULL,                              -- sha256 of canonical JSON (the "compiled hash" every hit stores)
  status text NOT NULL DEFAULT 'DRAFT',                        -- DRAFT, SIMULATED, IN_REVIEW, APPROVED, ACTIVE, RETIRED (D19)
  sync boolean NOT NULL DEFAULT false,                         -- evaluated by the decide path too (§12)
  author_id uuid NOT NULL, submitted_at timestamptz, approved_by uuid, approved_at timestamptz,
  simulation_id uuid, activated_at timestamptz, retired_at timestamptz, retired_by_version_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, rule_id, version),
  FOREIGN KEY (tenant_id, rule_id) REFERENCES monitoring_rule (tenant_id, id),
  FOREIGN KEY (tenant_id, author_id) REFERENCES staff_user (tenant_id, id),
  FOREIGN KEY (tenant_id, approved_by) REFERENCES staff_user (tenant_id, id),
  FOREIGN KEY (tenant_id, retired_by_version_id) REFERENCES monitoring_rule_version (tenant_id, id),
  CHECK (approved_by IS NULL OR approved_by <> author_id),     -- SEC-501 in the database too
  CHECK (status NOT IN ('APPROVED', 'ACTIVE') OR (approved_by IS NOT NULL AND simulation_id IS NOT NULL)));
CREATE UNIQUE INDEX ux_rule_version_active ON monitoring_rule_version (tenant_id, rule_id) WHERE status = 'ACTIVE';

CREATE TABLE rule_simulation (tenant_id uuid NOT NULL, id uuid NOT NULL, rule_version_id uuid NOT NULL,
  requested_by uuid NOT NULL, range_start date NOT NULL, range_end date NOT NULL,
  status text NOT NULL,                                        -- QUEUED, RUNNING, COMPLETED, FAILED
  definition_hash bytea NOT NULL,                              -- the version's hash at request time; a changed definition invalidates the simulation
  transactions_read bigint NOT NULL DEFAULT 0, hit_count int NOT NULL DEFAULT 0, customers_hit int NOT NULL DEFAULT 0,
  hits_per_week jsonb,                                         -- [{"week":"2026-W40","hits":6,"customers":5}, …]
  overlap jsonb,                                               -- {"existing_hits": 12, "same_customers": 9, "later_true_match": 3} (cases join in SP-06)
  sample jsonb,                                                -- up to 50 {"party_id","relationship_id","window_start","window_end","aggregates":{…}} (ids only)
  cursor text, started_at timestamptz, finished_at timestamptz, last_error text, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, rule_version_id) REFERENCES monitoring_rule_version (tenant_id, id),
  FOREIGN KEY (tenant_id, requested_by) REFERENCES staff_user (tenant_id, id));
ALTER TABLE monitoring_rule_version ADD CONSTRAINT fk_rule_version_simulation FOREIGN KEY (tenant_id, simulation_id) REFERENCES rule_simulation (tenant_id, id);

CREATE TABLE rule_assignment (tenant_id uuid NOT NULL, id uuid NOT NULL, rule_version_id uuid NOT NULL,
  product text,                                                -- NULL = every product; else the relationship's product (as tenant_pack_assignment)
  segment_filter jsonb,                                        -- {"risk_rating": ["HIGH","MEDIUM"], "tier": ["N1"], "monitoring_profile": ["STD"]}; NULL = everyone
  effective_from date NOT NULL, effective_to date, approved_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, rule_version_id) REFERENCES monitoring_rule_version (tenant_id, id),
  FOREIGN KEY (tenant_id, approved_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE rule_hit (tenant_id uuid NOT NULL, id uuid NOT NULL, rule_version_id uuid NOT NULL, rule_code text NOT NULL,
  key_kind text NOT NULL, key text NOT NULL, dedup_key text NOT NULL,   -- dedup_key = '<key_kind>:<key>:<window_bucket>' (§9.6)
  party_id uuid NOT NULL, relationship_id uuid NOT NULL,
  severity text NOT NULL,                                      -- LOW, MEDIUM, HIGH, CRITICAL
  outcome text NOT NULL DEFAULT 'ALERT',                       -- ALERT; TIER_LIMIT kinds: BLOCK or REVIEW
  window_start timestamptz NOT NULL, window_end timestamptz NOT NULL,
  evidence jsonb NOT NULL,                                     -- {"v":1,"aggregates":{"near_sum":{"value":"4800.00","unit":"USD","window":"P7D"}},"params":{...},"explanation":"..."} (§9.6); no PII
  cooldown_until timestamptz, fired_count int NOT NULL DEFAULT 1,
  policy_snapshot_id uuid NOT NULL, definition_hash bytea NOT NULL, assignment_id uuid NOT NULL,
  first_transaction_id uuid NOT NULL, first_occurred_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), run_id uuid,   -- run_id: workflow run (D9), unused in SP-04
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, rule_version_id, dedup_key),   -- D13, NFR-TM-004
  FOREIGN KEY (tenant_id, rule_version_id) REFERENCES monitoring_rule_version (tenant_id, id),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id),
  FOREIGN KEY (tenant_id, policy_snapshot_id) REFERENCES policy_snapshot (tenant_id, id),
  FOREIGN KEY (tenant_id, assignment_id) REFERENCES rule_assignment (tenant_id, id),
  FOREIGN KEY (tenant_id, first_occurred_at, first_transaction_id) REFERENCES transaction (tenant_id, occurred_at, id));
CREATE INDEX ix_rule_hit_queue ON rule_hit (tenant_id, severity, created_at DESC);
CREATE INDEX ix_rule_hit_party ON rule_hit (tenant_id, party_id, created_at DESC);
CREATE INDEX ix_rule_hit_rule_period ON rule_hit (tenant_id, rule_version_id, created_at);

CREATE TABLE rule_hit_transaction (tenant_id uuid NOT NULL, hit_id uuid NOT NULL, transaction_id uuid NOT NULL, occurred_at timestamptz NOT NULL,
  role text NOT NULL,                                          -- TRIGGER (the one that fired), WINDOW (evidence in the window), COOLDOWN (fired again inside the cooldown)
  PRIMARY KEY (tenant_id, hit_id, transaction_id),
  FOREIGN KEY (tenant_id, hit_id) REFERENCES rule_hit (tenant_id, id),
  FOREIGN KEY (tenant_id, occurred_at, transaction_id) REFERENCES transaction (tenant_id, occurred_at, id));

CREATE TABLE capture_group (tenant_id uuid NOT NULL, id uuid NOT NULL, capture_code text NOT NULL,
  key_kind text NOT NULL, key text NOT NULL,                   -- RELATIONSHIP as a rule; the accumulator's key
  period_key text NOT NULL, window_start timestamptz NOT NULL, window_end timestamptz NOT NULL,
  threshold_crossed_at timestamptz, accumulated_minor bigint NOT NULL DEFAULT 0, accumulated_unit numeric(28,10) NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'OPEN',                         -- OPEN, CROSSED, SUPERSEDED
  superseded_by_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, superseded_by_id) REFERENCES capture_group (tenant_id, id));
CREATE UNIQUE INDEX ux_capture_group_open ON capture_group (tenant_id, capture_code, key_kind, key, window_start) WHERE status <> 'SUPERSEDED';

CREATE TABLE reportable_event (tenant_id uuid NOT NULL, id uuid NOT NULL,
  capture_code text NOT NULL, report_code text NOT NULL,       -- ITF_ROR_CASH → MX_F41_ROR, AVI_XVI_A → MX_AVISO_AVI, … from the policy
  capture_kind text NOT NULL,                                  -- THRESHOLD, ACCUMULATION, FEE_THRESHOLD
  transaction_id uuid NOT NULL, occurred_at timestamptz NOT NULL,
  party_id uuid NOT NULL, relationship_id uuid NOT NULL, product text,
  period_key text NOT NULL,                                    -- '2027-02' or '2027-Q1' per the capture's period
  capture_group_id uuid,                                       -- ACCUMULATION: the group whose crossing captured this transaction
  fields jsonb NOT NULL,                                       -- pre-mapped report fields without PII (§10.3); SP-05 joins the party for names
  unit_values jsonb NOT NULL,                                  -- {"threshold": {"amount":"5000","unit":"USD"}, "converted": {"amount":"91,250.00","currency":"MXN","unit_value_id":812,"rule":"PRIOR_BANKING_DAY","resolved_date":"2027-02-02"}}
  status text NOT NULL DEFAULT 'CAPTURED',                     -- CAPTURED, INCLUDED (SP-05), VOIDED, SUPERSEDED
  supersedes_id uuid, superseded_by_id uuid, void_reason text,
  policy_snapshot_id uuid NOT NULL, captured_at timestamptz NOT NULL DEFAULT now(), late boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, capture_code, transaction_id),            -- one event per transaction per capture (D13); supersession voids before re-capturing
  FOREIGN KEY (tenant_id, occurred_at, transaction_id) REFERENCES transaction (tenant_id, occurred_at, id),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id),
  FOREIGN KEY (tenant_id, capture_group_id) REFERENCES capture_group (tenant_id, id),
  FOREIGN KEY (tenant_id, supersedes_id) REFERENCES reportable_event (tenant_id, id),
  FOREIGN KEY (tenant_id, superseded_by_id) REFERENCES reportable_event (tenant_id, id),
  FOREIGN KEY (tenant_id, policy_snapshot_id) REFERENCES policy_snapshot (tenant_id, id));
CREATE INDEX ix_reportable_event_period ON reportable_event (tenant_id, report_code, period_key, status);

CREATE TABLE tier_limit_notice (tenant_id uuid NOT NULL, relationship_id uuid NOT NULL, limit_code text NOT NULL, period_key text NOT NULL,
  first_sent_at timestamptz NOT NULL DEFAULT now(), source text NOT NULL,   -- DECIDE, PROCESS: whichever path published monitoring.tier_limit_reached.v1 first (§12)
  PRIMARY KEY (tenant_id, relationship_id, limit_code, period_key),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id));

CREATE TABLE capture_period (tenant_id uuid NOT NULL, id uuid NOT NULL, capture_code text NOT NULL, report_code text NOT NULL,
  period_key text NOT NULL, period_start date NOT NULL, period_end date NOT NULL,
  status text NOT NULL DEFAULT 'OPEN',                         -- OPEN, CLOSED, REOPENED (a late capture after close; SP-05 regenerates)
  event_count int NOT NULL DEFAULT 0, reconciled_count int, reconciled_at timestamptz, discrepancies jsonb,   -- ids of transactions the close found missing or extra (§10.4)
  closed_at timestamptz, reopened_at timestamptz, change_count int NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, capture_code, period_key));

-- Counterparty screening (screening module; 0012 touches screening tables)
ALTER TABLE screening_subject ADD COLUMN counterparty_id uuid;
ALTER TABLE screening_subject ADD CONSTRAINT fk_screening_subject_counterparty FOREIGN KEY (tenant_id, counterparty_id) REFERENCES counterparty (tenant_id, id);
ALTER TABLE screening_subject ADD CONSTRAINT uq_screening_subject_counterparty UNIQUE (tenant_id, counterparty_id);
ALTER TABLE screening_subject DROP CONSTRAINT ck_screening_subject_kind_input;       -- 0010's constraint, on column `input`
ALTER TABLE screening_subject ADD CONSTRAINT ck_screening_subject_kind_input CHECK (
     (kind = 'PARTY'        AND party_id IS NOT NULL AND counterparty_id IS NULL AND input IS NULL)
  OR (kind = 'AD_HOC'       AND party_id IS NULL AND counterparty_id IS NULL AND input IS NOT NULL)
  OR (kind = 'COUNTERPARTY' AND party_id IS NULL AND counterparty_id IS NOT NULL AND input IS NULL));
ALTER TABLE counterparty ADD CONSTRAINT fk_counterparty_subject FOREIGN KEY (tenant_id, screening_subject_id) REFERENCES screening_subject (tenant_id, id);

CREATE TABLE counterparty_name_index (tenant_id uuid NOT NULL, id uuid NOT NULL, counterparty_id uuid NOT NULL,
  norm_name text NOT NULL, tokens text[] NOT NULL, phonetic_keys text[] NOT NULL,
  matcher_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, counterparty_id),
  FOREIGN KEY (tenant_id, counterparty_id) REFERENCES counterparty (tenant_id, id));
CREATE INDEX ix_cni_phonetic ON counterparty_name_index USING gin (phonetic_keys);
CREATE INDEX ix_cni_trgm ON counterparty_name_index USING gin (norm_name gin_trgm_ops);   -- same contract as party_name_index (D7, PRV-301)
```

Migration 0013 (plan 4c):

```sql
CREATE TABLE rail_setting (tenant_id uuid NOT NULL, id uuid NOT NULL, rail text NOT NULL,
  fail_mode text NOT NULL,                                     -- FAIL_OPEN (answer REVIEW), FAIL_CLOSED (answer BLOCK)
  changed_by uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, rail),
  FOREIGN KEY (tenant_id, changed_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE txn_decision (tenant_id uuid NOT NULL, decided_at timestamptz NOT NULL, id uuid NOT NULL,
  external_id text NOT NULL, rail text NOT NULL, relationship_id uuid NOT NULL, account_id uuid,
  decision text NOT NULL,                                      -- ALLOW, REVIEW, BLOCK
  mode text NOT NULL,                                          -- EVALUATED, FAIL_OPEN, FAIL_CLOSED
  reasons jsonb NOT NULL,                                      -- [{"code":"TIER_LIMIT","rule_version_id":"…","severity":"HIGH","detail":{…}}]; staff view only beyond `code`
  ruleset_hash bytea NOT NULL,                                 -- sha256 over the sorted definition hashes of the sync versions evaluated
  policy_snapshot_id uuid, latency_ms int NOT NULL, db_ms int NOT NULL,
  transaction_id uuid, occurred_at timestamptz,                -- linked when the transaction arrives through ingest (by external_id)
  actor text NOT NULL,                                         -- 'api_key:<id>'
  PRIMARY KEY (tenant_id, decided_at, id),
  FOREIGN KEY (tenant_id, occurred_at, transaction_id) REFERENCES transaction (tenant_id, occurred_at, id),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id),
  FOREIGN KEY (tenant_id, account_id) REFERENCES account (tenant_id, id),
  FOREIGN KEY (tenant_id, policy_snapshot_id) REFERENCES policy_snapshot (tenant_id, id)
) PARTITION BY RANGE (decided_at);
CREATE UNIQUE INDEX ux_txn_decision_external ON txn_decision (tenant_id, external_id, decided_at);
ALTER TABLE transaction ADD CONSTRAINT fk_transaction_decision FOREIGN KEY (tenant_id, decided_at, decision_id) REFERENCES txn_decision (tenant_id, decided_at, id);
CREATE INDEX ix_txn_decision_relationship ON txn_decision (tenant_id, relationship_id, decided_at DESC);

CREATE TABLE behaviour_profile (tenant_id uuid NOT NULL, id uuid NOT NULL, relationship_id uuid NOT NULL, party_id uuid NOT NULL,
  month text NOT NULL,                                         -- '2027-02'
  metrics jsonb NOT NULL,                                      -- {"inflow_minor","outflow_minor","inflow_count","outflow_count","cash_share","international_share","crypto_share","distinct_counterparties","active_days","currency":"MXN"} (strings for amounts)
  declared_profile jsonb,                                      -- copy of customer_relationship.declared_profile at computation
  deviation jsonb,                                             -- {"monthly_inflow": "2.4", "monthly_outflow": "0.9"} ratios actual/declared; NULL without a declaration
  deviated boolean NOT NULL DEFAULT false, computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, relationship_id, month),
  FOREIGN KEY (tenant_id, relationship_id) REFERENCES customer_relationship (tenant_id, id),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

ALTER TABLE customer_relationship ADD COLUMN declared_profile jsonb;   -- {"monthly_inflow_minor","monthly_outflow_minor","currency","expected_countries":[],"expected_instruments":[]}; set by PATCH /v1/relationships/{id} (new in the party module, plan 4c), filled by onboarding in SP-08
```

`rule_hit.evidence` and `reportable_event.fields` carry amounts, units, codes, hashes and ids only, never a name, account or address (`PRV-501`); a staff page joins the party and counterparty rows and reveals them with `VIEW_PII`. Hits, captures and decisions all store `policy_snapshot_id` plus the definition hash that fired, so a decision can be reproduced from its snapshot (ADR-003 §5).

## 6. Ingest

### 6.1 Batch (`POST /v1/transactions`)

`ingest_batch(session, items, *, source, submitted_by) -> BatchReport`, in one database transaction:
1. Validate every item (§4); resolve each `customer_ref` to `(party_id, relationship_id, product)` through `modules.party` (`relationship_by_external_ref`, which plan 4a adds beside `parties_query(external_ref=…)`, one query for the batch) and each `account_ref` to an `account` row (upsert by `(tenant_id, external_ref)`; absent `account_ref` uses the relationship's `DEFAULT` account). Any failure refuses the whole batch with 422 `TRANSACTION_BATCH_INVALID` and `errors[]`; nothing is written.
2. Resolve counterparties (§7.2) and insert the `transaction_batch`, then the `transaction` rows (`status = RECEIVED`, `content_hash`, `raw_enc`) and their `transaction_ref` rows with `INSERT … ON CONFLICT (tenant_id, external_id) DO NOTHING`. A conflict whose stored `content_hash` equals the item's is a duplicate (counted, not an error, `FR-TM-001`); a different hash refuses the batch with 409 `EXTERNAL_ID_CONFLICT` naming the index only.
3. Link decisions: for each item whose `external_id` has a `txn_decision` row in the last 7 days, set `txn_decision.transaction_id, occurred_at` and `transaction.decision_id, decided_at` (both composite FKs, §12).
4. Enqueue one `monitoring.process` job per distinct `party_id` (key `process:<party_id>`, payload `{party_id}`); the queue's dedup index drops the enqueue when a job for the key is already `PENDING` or `RUNNING`, and the sweep covers the window in which a running job has passed its last check (§8.5).
5. Publish `monitoring.transaction_batch_accepted.v1` and return `{batch_id, accepted, duplicates, transaction_ids[]}` (the ids are the client's handle; the machine view of a transaction is §15).

### 6.2 Files (`POST /v1/transactions/files`)

Multipart upload of a CSV (UTF-8, comma or semicolon, header row with the §4 field names, nested fields as `counterparty.name`, `crypto.network`, `fx.rate`, `device.ip`), JSON Lines (one `TransactionInput` per line, TS-05 §4) or XLSX (first sheet, same header as the CSV; added for compliance officers who export spreadsheets, Decision 21). The file is sealed with the tenant DATA key and stored at `transactions/files/<file_id>` (`ObjectStore.put`), a `transaction_file` row is written (`UPLOADED`; a repeated `sha256` returns the existing row, 200) and `monitoring.ingest_file` is enqueued (key `ingest_file:<file_id>`). The job walks the file `MONITORING_FILE_CHUNK_ROWS` at a time from `cursor`, each page in its own transaction: rows that validate are ingested as a batch with `source = FILE` and `file_id`; rows that do not are appended to the error report (`row`, `column`, `code`; never a cell value) and counted. A file accepts valid rows and reports the rest (Decision 5): a 100,000-row file with one typo must not be bounced. The job resumes from `cursor` after a crash; when the last page is done it writes `errors.csv`, sets `COMPLETED` and publishes `monitoring.transaction_file_completed.v1`. `xolotl monitoring ingest-file` runs the same code from the CLI (the dev and demo path).

## 7. Enrichment (inside the processing transaction, §8.5)

`enrich(session, txn, *, units: UnitService, holidays, policy) -> EnrichedTransaction`:

1. **Policy.** `effective_policy(on=occurred_at.date() in America/Mexico_City, product=relationship.product)` and `snapshot_policy()`; the transaction stores `policy_snapshot_id`. A tenant without a policy in force on that date parks the row (`POLICY_NOT_ASSIGNED`, `monitoring.transaction_parked.v1`, §8.5).
2. **Profile.** `tier = customer_relationship.account_tier`; `risk_rating = NULL` and `monitoring_profile = 'STD'` until SP-07 (program spec §7.2); `pep_status` and the party's `screening_subject.status` read through `modules.party.screening_profile` and `modules.screening` for the evaluator's context (not stored on the row).
3. **Units** (`FR-TM-002`, ADR-021). The set of `(unit, rule)` pairs to compute is derived from the snapshot: every `captures[].threshold.unit` with its `conversion_rule`, every `kyc.tiers[].limits.*.unit` with `units.tier_limit_rule`, `USD` with `PRIOR_BANKING_DAY` for the typology library, and the pack's `units.quote_ccy` (`MXN`, the local currency of `FR-TM-002`) with `SAME_DAY` for every transaction whose `currency` differs from it (a MXN transaction records itself under `MXN/SAME_DAY` with no `unit_value_id`). For each `(unit, rule)` pair `UnitService.to_unit(amount, currency, unit, on=occurred_at date, rule)` gives a `Conversion`; `unit_amounts["<unit>/<rule>"] = {amount, unit_value_id, resolved_date}` (D11 keys the map by unit alone; `UDI` is needed under `SAME_DAY` for `ITF_VA_OPS` and under `LAST_DAY_PREV_MONTH` for tiers, so the key carries the rule, Decision 19). A `UnitValueMissingError` parks the row (`UNIT_VALUE_MISSING`, `unit_code`, `needed_on`; `monitoring.transaction_parked.v1` is published) and processing of that key stops at this transaction (order is preserved: later rows of the key wait), per TS-05 §7 and D5. Crypto amounts: `asset_qty` is kept as sent; the fiat `amount` the client reports for the operation is what thresholds read (§21 Q7).
4. **Calendar.** `period_keys` (`DAY`, `MONTH`, `QUARTER`, `BUSINESS_DAY` under the `BANXICO` scope) in `America/Mexico_City`.
5. **Counterparty.** `counterparty_country` and the row's `screening_status` at this moment (`UNSCREENED` when the first-sight screening has not run yet; the evaluator treats `UNSCREENED` as unknown, never as clear).
6. **Device.** `device_hash = HMAC(index key, device_id)`; `geo_country` from the device context. The IP is not stored (it is in `raw_enc`).

Golden tests (`tests/golden/monitoring/enrichment/`): the UMA change on 1 February (an AVI operation on 31 January and one on 1 February use different UMA rows), the FIX of the prior banking day across a weekend and a Banxico holiday, the UDI of the last day of the previous month for a January tier limit, a missing value that parks, a `QUARTER` key at the year boundary.

## 8. Aggregates and processing

### 8.1 The `AggregateStore` port

```python
class AggregateStore(Protocol):
    def add(self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]) -> None     # updates every bucket the transaction falls in
    def read(self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime) -> AggregateValue   # sum of the buckets in the window ending at `at`
    def calendar_read(self, key: AggregateKey, declaration: AggregateDeclaration, *, period_key: str) -> AggregateValue
    def accumulator_read(self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime) -> AggregateValue   # ACCUM: the open window containing `at`
```

The program spec §4 names `add`, `read` and `calendar_read`; `accumulator_read` is added for the six-months-from-first window. `PostgresAggregateStore` (production and the decide path) writes `agg_bucket` and `agg_distinct` with `INSERT … ON CONFLICT DO UPDATE` and reads by the primary key or `ix_agg_bucket_window`; `MemoryAggregateStore` (simulation and tests) keeps the same buckets in dictionaries. A contract test runs both against the same stream and asserts identical reads (D12: "simulation replays Postgres history through identical code").

### 8.2 Declarations and `agg_id`

```python
class AggregateFunction(StrEnum): SUM = "SUM"; COUNT = "COUNT"; AVG = "AVG"; MAX = "MAX"; MIN = "MIN"; DISTINCT_COUNT = "DISTINCT_COUNT"; FIRST_SEEN = "FIRST_SEEN"; LAST_SEEN = "LAST_SEEN"
class WindowKind(StrEnum): DURATION = "DURATION"; MONTH = "MONTH"; QUARTER = "QUARTER"; ACCUM = "ACCUM"
class Measure(StrEnum): AMOUNT_MINOR = "AMOUNT_MINOR"; UNIT_AMOUNT = "UNIT_AMOUNT"; FEE_MINOR = "FEE_MINOR"; ASSET_QTY = "ASSET_QTY"; ONE = "ONE"
@dataclass(frozen=True, slots=True)
class TxnFilter:        # the `where` of a declaration; every field optional, conjunctive, values are sets
    direction: frozenset[Direction] = (); instrument: frozenset[Instrument] = (); rail: frozenset[Rail] = (); channel: frozenset[Channel] = ()
    currency: frozenset[str] = (); counterparty_country_in: frozenset[str] = (); counterparty_country_not_in: frozenset[str] = ()
    counterparty_screening_status: frozenset[str] = (); self_custody: bool | None = None; cross_border: bool | None = None   # cross_border: counterparty country differs from the pack's `country`, or rail SWIFT
    geo_admin_area_in: frozenset[str] = (); amount_round_multiple: int | None = None     # literal region codes; amount is a multiple of N major units
    # A definition may write a list name (`$FATF_GREY`, `$BORDER_ZONE_MX`) in country or region sets; `ensure_declarations` resolves names to codes
    # when it loads declarations, so `agg_id` hashes the literal codes plus the jurisdictions file hash and `add()` never sees a name; a changed
    # `_jurisdictions.yaml` therefore yields a new `agg_id` and a backfill (§8.4). The same resolution applies to a kind's `on` filter at rule load.
    amount_unit_key: str | None = None; amount_gte: Decimal | None = None; amount_lt: Decimal | None = None     # band in a unit key (USD/PRIOR_BANKING_DAY, UDI/SAME_DAY, UMA/SAME_DAY, MXN/SAME_DAY)
@dataclass(frozen=True, slots=True)
class AggregateDeclaration:
    name: str; function: AggregateFunction; measure: Measure; unit_key: str | None   # "<unit>/<rule>" for UNIT_AMOUNT, e.g. "USD/PRIOR_BANKING_DAY"
    key_kind: KeyKind; window_kind: WindowKind; window: str                      # 'PT1H'..'P7D' (HOUR buckets), 'P8D'..'P6M' (DAY), 'MONTH', 'QUARTER', 'P6M' with ACCUM
    where: TxnFilter = TxnFilter(); distinct_of: Literal["COUNTERPARTY", "DEVICE", "COUNTRY", "ACCOUNT"] | None = None
    def agg_id(self) -> str: ...     # first 16 hex chars of sha256(canonical JSON without `name`); two rules declaring the same aggregate share state
```

Bucket choice is mechanical: durations up to `P7D` use `HOUR` buckets, up to `P6M` use `DAY`, `MONTH` and `QUARTER` use one calendar bucket, `ACCUM` uses one bucket per accumulation window starting at the key's first qualifying operation and closing `window` later (the LFPIORPI six months from the first operation, D21). Only declared aggregates are maintained (`FR-TM-005`): the processing job loads the union of declarations of the tenant's active rule versions, the snapshot's captures (`agg_declaration`), and `add()` updates exactly those.

### 8.3 Distinct counts

`DISTINCT_COUNT` inserts `agg_distinct` member rows (HMAC of the member) per bucket and reads `COUNT(DISTINCT member_hash)` over the window's buckets. Exact, no HyperLogLog, no extension (Decision 6); the member table is bounded by distinct members per key per window, which at MVP volumes is small, and SP-11 measures it.

### 8.4 Backfill of a new declaration

Activating a rule version or assigning a pack with new captures may introduce an `agg_id` with no history. `ensure_declarations(session, declarations)` inserts the missing `agg_declaration` rows (`backfilled_through = NULL`) and enqueues `monitoring.agg_backfill` (key `agg_backfill:<agg_id>`), which walks `transaction` rows with `status = PROCESSED` over the declaration's longest window (6 months) in `occurred_at` order, `MONITORING_AGG_BACKFILL_BATCH` per page, calling `add()` for that declaration only, then sets `backfilled_through`. Until then the evaluator treats the aggregate as `INCOMPLETE` and rules reading it do not fire (a hit from a partial window would be wrong twice: a false alert and a missed dedup). Buckets outlive their readers until retention (§8.6); nothing garbage-collects them earlier.

### 8.5 The processing job and ordering (D12)

`monitoring.process` (payload `{party_id}`; key `process:<party_id>`) is the per-key lease: the queue's `UNIQUE (tenant_id, kind, key)` over `PENDING` and `RUNNING` jobs means at most one job per party runs at a time, and the job's lease (`MONITORING_PROCESS_LEASE_SECONDS`, heartbeat) survives a crash with retry. The handler drains the key:

1. Read up to `MONITORING_PROCESS_PAGE` rows with `status IN ('RECEIVED', 'PARKED')` for the party ordered by `occurred_at, id` (a `PARKED` row is retried only when the sweep marked it `RECEIVED` again; a `RECEIVED` row behind a `PARKED` one waits).
2. For each row, **one database transaction** (TS-05 §5): enrich (§7); mark `late` when `occurred_at` is before the key's latest processed `occurred_at` (out of order) or `received_at − occurred_at > P1D`; `add()` to the aggregates, then evaluate ALERT rules (§9.5, tier limits included, §12.2) and CAPTURE rules (§10.2) with the pure evaluator over `(transaction, aggregates, profile, policy)`: the evaluator always reads aggregates that already include the transaction under evaluation (on the decide path the memory store is seeded with the candidate for the same reason, §12.1); insert hits, links and captures; set `status = PROCESSED`, `processed_at`; publish `monitoring.rule_hit_created.v1` per new hit, `monitoring.reportable_event_captured.v1` per event and `monitoring.transaction_processed.v1` for the row; commit. A crash between rows loses nothing: the row stays `RECEIVED` and the job retries from the queue (`NFR-TM-006`). A late row additionally re-evaluates the key's ALERT rules for the windows that contain `occurred_at` (hits are deduplicated, so only genuinely new firings appear) and re-runs the captures of its period (§10.5).
3. Rows older than `MONITORING_LATE_DAYS` at receipt are set to `BACKFILL` and skipped: they wait for `POST /v1/monitoring/backfill` (staff) or `xolotl monitoring backfill`, which processes them with the same code and marks every affected `capture_period` `REOPENED` (`FR-TM-009`: a late event never silently disappears; it is visible in the Transacciones page under "Pendientes de backfill").
4. After a page, heartbeat and loop; when no row remains, complete.

Per-key ordering holds because one job per key runs at a time and processes rows in `occurred_at` order; cross-key ordering is not needed (every aggregate is per key). The race in which a batch arrives after a running job's last read and its enqueue is deduplicated away is closed by `monitoring.sweep` (`MONITORING_SWEEP_CRON`): every minute, per tenant, it enqueues `monitoring.process` for every party with a `RECEIVED` row older than 30 s, flips `PARKED` rows whose `unit_code` now has a value for `needed_on` back to `RECEIVED`, retries `POLICY_NOT_ASSIGNED` rows once an hour (and at once when a pack assignment is created, through `policy.pack_assigned` handling in the sweep's next run), enqueues their keys, and raises an in-app notification `monitoring.parked` to ADMIN and MLRO once a day while any row has been parked for over 6 hours (TS-05 §7 "pages on-call"). Latency: the normal path is API → job claim → processing, a few seconds on the MVP profile (`NFR-TM-002`); the sweep is the fallback, bounded by one minute.

### 8.6 Retention

`monitoring.retention` (schedule `MONITORING_RETENTION_CRON`) computes the tenant's longest `retention.transactions` across its packs (ten years for ITF) and, through the admin engine like partition creation, retires every month whose end is before today minus that period. A partition cannot be detached while rows reference it, so a month is retired in order, each step in its own transaction (Decision 22): `rule_hit_transaction` rows of the month, `reportable_event` rows of the month and the `capture_group` rows left empty, `rule_hit` rows whose `first_occurred_at` is in the month together with every `rule_hit_transaction` link of those hits (a `WINDOW` or `COOLDOWN` link may point at a later month), the `txn_decision` links (`transaction_id`, `occurred_at` set NULL) and `transaction.decision_id`, `decided_at` of the month's rows, and then the decision partition of the same month, `transaction_parked` and `transaction_ref` rows of the month, and finally the `transaction` partition, detached and dropped. Hits and captures share `retention.transactions`, so losing them with their month is the policy, and the audit chain keeps what was decided. A month is skipped, and named in the audit row, while a join from `transaction.party_id` to `party.legal_hold` finds a held party with rows in it. The job then deletes `counterparty` rows no transaction references (`txn_count` recomputed) together with their subjects, findings and name-index rows, and `agg_bucket` and `agg_distinct` rows whose bucket ended before the longest declared window, except `ACCUM` buckets whose `bucket_end` has not passed and `MONTH`/`QUARTER` buckets of a `capture_period` still `OPEN` or `REOPENED`. Audited `monitoring.retention_ran` with counts per step and the skipped months; `NFR-TM-005` is satisfied by never dropping earlier than the policy allows.

## 9. Rules

### 9.1 Rule definitions (`monitoring/rules/definitions.py`)

A rule is a typed document, not an expression (Decision 1). Every kind has a Pydantic definition whose fields are the parameters sentinel's kinds took, each parameter carrying bounds in the template and a value in the tenant's version:

```python
class RuleFamily(StrEnum): ALERT = "ALERT"; CAPTURE = "CAPTURE"
class RuleKind(StrEnum): THRESHOLD = "THRESHOLD"; AGGREGATE = "AGGREGATE"; STRUCTURING = "STRUCTURING"; VELOCITY = "VELOCITY"; HIGH_RISK_JURISDICTION = "HIGH_RISK_JURISDICTION"; PROFILE_DEVIATION = "PROFILE_DEVIATION"; TIER_LIMIT = "TIER_LIMIT"
class Severity(StrEnum): LOW = "LOW"; MEDIUM = "MEDIUM"; HIGH = "HIGH"; CRITICAL = "CRITICAL"
class Param[T](BaseModel): value: T; min: T | None = None; max: T | None = None; choices: list[T] | None = None; tighten: Literal["LOWER", "RAISE", None] = None   # bounds from the template; `tighten` says which direction a tenant may move it (captures: LOWER only)
class Dedup(BaseModel): key: KeyKind = KeyKind.RELATIONSHIP; window: str = "P1D"; cooldown: str = "P7D"      # window: an ISO duration (window_bucket = floor(occurred_at, window)) or "MONTH" (the calendar month in America/Mexico_City; TIER_LIMIT uses it); cooldown suppresses new hits for the key
class Evidence(BaseModel): transactions_window: str | None = None; transactions_where: TxnFilter = TxnFilter(); aggregates: list[str] = []   # what rule_hit_transaction and evidence.aggregates carry
class Condition(BaseModel): aggregate: str; op: Literal["GTE", "GT", "LTE", "LT"]; threshold: str | None = None; ratio_of: str | None = None; ratio: Decimal | None = None   # AGGREGATE kind; exactly one of threshold (a params name) or ratio_of + ratio

class RuleDefinition(BaseModel):                 # discriminated on `kind`; `language: "KIND/1"` so a later expression language can coexist (§21 Q1)
    language: Literal["KIND/1"] = "KIND/1"; code: str; family: RuleFamily; kind: RuleKind; title: str; typology: list[str]
    on: TxnFilter                                 # the trigger filter: which transactions can fire the rule
    key_kind: KeyKind = KeyKind.RELATIONSHIP; segment: SegmentFilter | None = None
    params: dict[str, Param[Decimal | int | str | bool]]; aggregates: list[AggregateDeclaration]; conditions: list[Condition] = []   # AGGREGATE kind
    severity: Severity; sync: bool = False; dedup: Dedup; evidence: Evidence
    def definition_hash(self) -> bytes: ...       # sha256 of canonical JSON (sorted keys, Decimals as strings)
    def render(self, locale: str) -> str: ...     # "Dispara cuando el cliente recibe 3 o más depósitos en efectivo entre 70 % y 100 % del umbral en 7 días y su suma alcanza el umbral" — the analyst-readable sentence (FR-TM-004 acceptance; DD-05 §3.3 shows it read-only)
```

Kind semantics (each a pure function `evaluate(definition, ctx: EvalContext) -> Firing | None` in `rules/kinds.py`, with `EvalContext = (txn, aggregates reader, profile, policy, lists, now)` where `lists: JurisdictionLists` is loaded by the processing job, the decide path and the simulation from `policy-packs/_jurisdictions.yaml` through `xolotl.platform.policy.load_jurisdictions()` (SP-02 ships the file as a seed; plan 4b adds the loader): named sets of country codes (`FATF_BLACK`, `FATF_GREY`, `TAX_HAVEN`) and region codes (`BORDER_ZONE_MX`), with the file's content hash recorded in every hit's `evidence.lists_hash`):
- `THRESHOLD`: fires when the transaction matches `on` and `amount in params.unit >= params.threshold` (optionally with `counterparty_screening_status`, `self_custody`, `cross_border` filters in `on`; this is how "counterparty true match" and "self-custody outflow" rules are expressed).
- `AGGREGATE`: the general windowed rule; fires when every entry of `conditions` holds. A condition is `{aggregate, op, threshold}` (`op` in `GTE`, `GT`, `LTE`, `LT`; `threshold` names a parameter, so bounds apply) or `{aggregate, op, ratio_of, ratio}` (the aggregate against `ratio × another declared aggregate`), so a funnel is two conditions (distinct counterparties and a sum) and a pass-through is a ratio (OUT sum over IN sum in the window). Order inside the window is not expressed (a sequence is approximated by a short window; §23 lists what that costs).
- `STRUCTURING`: `count` of transactions in `[near_ratio × threshold, threshold)` over the window `>= min_count` and their `sum >= threshold` (sentinel's rule, fed from two declarations).
- `VELOCITY`: `count` or `sum` over a short window (`PT1H`..`P7D`) `>= threshold`, with an optional `new_account_days` gate (relationship younger than N days) or `dormant_days` gate (the key's `LAST_SEEN` before the window is older than N days: reactivation).
- `HIGH_RISK_JURISDICTION`: the transaction's `counterparty_country` or `geo_country` is in the code sets that `params.lists` named (resolved from `ctx.lists` when the rule set is loaded, §8.2) and `amount >= params.threshold`, or the `count`/`sum` to such countries over the window reaches it.
- `PROFILE_DEVIATION`: the month-to-date `sum` for the key against `declared_profile.monthly_inflow` or `monthly_outflow` exceeds `params.ratio`, or against the key's own trailing six-month average (`params.history_ratio`); without a declaration and with under three months of history it does not fire.
- `TIER_LIMIT`: `sum(IN, calendar MONTH, in the tier's limit unit with the pack's tier_limit_rule) + this transaction > kyc.tiers[tier].limits.monthly_ops`; outcome `params.outcome` (`BLOCK` or `REVIEW`); `sync = true` by default (§12.2).

### 9.2 Captures and limits are policy, not templates

CAPTURE definitions are built from the effective policy snapshot at processing time (`captures/definitions.py: capture_definitions(policy) -> list[CaptureDefinition]`): `THRESHOLD` (one transaction at or above `threshold` under `conversion_rule`, `on` from the capture code's instrument mapping below), `ACCUMULATION` (a transaction at or above the threshold, or the `ACCUM` aggregate over `window` crossing it), `FEE_THRESHOLD` (the `fee_amount` at or above the threshold). CAPTURE definitions have no tenant rule versions: a tenant tightens a capture threshold through an SP-02 override (`captures[CODE].threshold`, tighten-only, `FR-TM-016`, `SEC-501`), and the console shows them read-only with the override entry point (DD-05 "Captures and limits"). `TIER_LIMIT` is different (Decision 17): it is an ALERT template (`TIER_N1_MONTHLY_OPS`, `TIER_N2_MONTHLY_OPS`, §9.3) whose threshold is read from the transaction's snapshot (`kyc.tiers[tier].limits.monthly_ops` with `units.tier_limit_rule`) at evaluation, so a tenant cannot loosen it, whose only parameter is `outcome`, and whose versions are seeded, simulated, approved and activated like any other, so its hits carry a `rule_version_id`, an `assignment_id` and a `definition_hash` like every hit. The capture codes of the two packs map to triggers as follows (pack content, so a new capture is a YAML row):

| Capture | `on` | Report | Period |
|---|---|---|---|
| `ITF_ROR_CASH` | `instrument = CASH`, any direction | `MX_F41_ROR` | QUARTER (ROR granularity still `UNVERIFIED` in the pack; the period key is computed for both and SP-05 picks) |
| `ITF_FX_CASH` | `instrument = FX` and `rail = CASH` | `MX_ITF_FX_CASH` | QUARTER |
| `ITF_INTL_TRANSFER` | `instrument = TRANSFER` and `cross_border` (counterparty country differs from the pack's `country`, or `rail = SWIFT`) | `MX_ITF_INTL_TRANSFER` | MONTH |
| `ITF_VA_OPS` | `instrument = CRYPTO` | `MX_ITF_VA_OPS` | QUARTER |
| `AVI_XVI_A` | `instrument = CRYPTO`, product `CRYPTO` | `MX_AVISO_AVI` | MONTH; `ACCUM P6M` from the first operation |
| `AVI_XVI_B` | `instrument = CRYPTO` with `fee_minor`; `merge_into: AVI_XVI_A` | `MX_AVISO_AVI` | MONTH |

The `on` mapping lives in `captures/triggers.yaml` beside the code, keyed by capture code, so a pack author adds a row rather than code when a new capture code appears (ADR-004). A tenant holding both packs has distinct products per assignment (D21), so exactly one crypto capture applies to a transaction.

### 9.3 The Mexico typology library (pack content)

`policy-packs/MX/_typologies/<code>.yaml`, one file per template, loaded into `rule_template` and `rule_template_version` by `xolotl rules load`. The initial library (`FR-TM-010`), each with a golden scenario under `tests/golden/monitoring/typologies/<code>/` that fires the expected hit once:

| Code | Kind | Typology | Parameters (defaults, bounds) |
|---|---|---|---|
| `CASH_STRUCTURING` | STRUCTURING | STRUCTURING | threshold USD 5,000 (`LOWER` only, min 1,000), near_ratio 0.70 (0.50..0.95), window P7D, min_count 3 |
| `RAPID_PASS_THROUGH` | AGGREGATE | MULE | conditions: `out_sum GTE ratio_of in_sum × 0.9` over P2D and `in_sum GTE USD 2,000` |
| `NEW_ACCOUNT_VELOCITY` | VELOCITY | VELOCITY | relationship ≤ 30 days, count ≥ 10 or sum ≥ USD 3,000 over P1D |
| `MANY_TO_ONE_FUNNEL` | AGGREGATE | MULE | conditions: `distinct_counterparties_in GTE 10` and `in_sum GTE USD 5,000` over P7D |
| `DECLARED_VS_ACTUAL_VOLUME` | PROFILE_DEVIATION | PROFILE | ratio ≥ 2.0 (1.2..5.0); history_ratio ≥ 3.0 |
| `HIGH_RISK_JURISDICTION_TRANSFER` | HIGH_RISK_JURISDICTION | JURISDICTION | threshold USD 1,000; list FATF_BLACK + FATF_GREY |
| `CRYPTO_OFF_RAMP` | AGGREGATE | CRYPTO | conditions: `fiat_out_sum GTE ratio_of crypto_in_sum × 0.8` over P1D (order inside the day not checked) |
| `SELF_CUSTODY_OUTFLOW` | THRESHOLD | CRYPTO | `self_custody = true`, amount ≥ USD 1,000 |
| `BORDER_ZONE_CASH` | AGGREGATE | STRUCTURING | `geo_admin_area_in: BORDER_ZONE_MX`, condition `cash_sum GTE USD 3,000` over P30D (the region list in `_jurisdictions.yaml`) |
| `ROUND_AMOUNT_FX_LAYERING` | AGGREGATE | LAYERING | `amount_round_multiple: 1000`, condition `fx_round_count GTE 5` over P7D |
| `DORMANT_REACTIVATION` | VELOCITY | DORMANCY | `dormant_days: 180`, sum ≥ USD 2,000 within P2D |
| `COUNTERPARTY_TRUE_MATCH` | THRESHOLD | SCREENING | any amount with `counterparty_screening_status = TRUE_MATCH` (severity CRITICAL) |
| `BLOCKED_PERSON_PARTY` | THRESHOLD | SCREENING | any transaction while the party's screening status is `TRUE_MATCH` (`FR-TM-014`: blocked persons) |
| `PEP_LARGE_OPERATION` | THRESHOLD | PEP | party `pep_status <> NONE`, amount ≥ USD 10,000 (`FR-TM-014`: PEPs) |
| `TIER_N1_MONTHLY_OPS`, `TIER_N2_MONTHLY_OPS` | TIER_LIMIT | LIMIT | from `kyc.tiers`; outcome BLOCK (N1), REVIEW (N2) (`FR-TM-008`) |

Templates declare `packs` so `xolotl rules seed-tenant` copies only the ones applicable to the tenant's assigned packs, as `DRAFT` versions. The library covers `FR-TM-014`'s list (consolidated operations are the captures; accumulation is `AVI_XVI_A`; high-risk customers wait for the risk rating segment in SP-07).

### 9.4 Lifecycle (D19) and governance (`FR-TM-016`, `SEC-501`)

| From | Action | Who | Checks | To |
|---|---|---|---|---|
| — | `POST /v1/monitoring/rules` (from a template) or `…/{id}/versions` (a new version from the active or draft one) | SENIOR_ANALYST, MLRO, ADMIN | params within bounds and `tighten` direction, segment valid; else 422 `RULE_PARAM_OUT_OF_BOUNDS` with the bound's source | `DRAFT` |
| `DRAFT`, `SIMULATED` | `PATCH …/versions/{v}` (params, segment, severity, dedup) | author or any writer | same; any change resets to `DRAFT` and clears `simulation_id` | `DRAFT` |
| `DRAFT`, `SIMULATED` | `…/simulate` | SENIOR_ANALYST, MLRO, ADMIN | a range ≤ `MONITORING_SIMULATION_MAX_DAYS` | `SIMULATED` when the job completes |
| `SIMULATED` | `…/submit` | author | simulation completed, younger than 30 days, same `definition_hash` | `IN_REVIEW` |
| `IN_REVIEW` | `…/approve` | MLRO, ADMIN, **not** the author | `SIMULATION_STALE`, `SIMULATION_MISSING`, `SAME_AUTHOR`, `ROLE_REQUIRED` as `blocked_reasons` | `APPROVED` |
| `IN_REVIEW` | `…/reject` with a reason | MLRO, ADMIN | — | `DRAFT` |
| `APPROVED` | `…/activate` with `effective_from` and optional `product` | MLRO, ADMIN | creates the `rule_assignment`; retires the rule's current `ACTIVE` version (`retired_by_version_id`, `monitoring.rule_version_retired.v1`); `ensure_declarations`; publishes `monitoring.rule_version_activated.v1` | `ACTIVE` |
| `ACTIVE` | `…/retire` | MLRO, ADMIN | closes the assignment (`effective_to = today`); publishes `monitoring.rule_version_retired.v1` | `RETIRED` |

Violations are 409 `RULE_TRANSITION_NOT_ALLOWED` with `blocked_reasons` (`STATE`, `ROLE_REQUIRED`, `SAME_AUTHOR`, `SIMULATION_MISSING`, `SIMULATION_STALE`, `PARAM_OUT_OF_BOUNDS`); the staff view returns `allowed_actions` and `blocked_reasons` for the current user (D15, D20). Every transition is audited (`rules.version_*`, §14) and the exhaustive state × role × same-user × action test is the acceptance test, mirroring SP-03's disposition matrix. Rule text editing is not offered: the definition's structure comes from the template; tenants tune `params`, `segment`, `severity` and `dedup` (DD-05 §3.3). Authoring a rule from a bare kind without a template is ADMIN-only and off by default (`XOLOTL_MONITORING_ALLOW_CUSTOM_RULES`, §21 Q2).

### 9.5 Assignment resolution and evaluation

`active_rules(session, *, on: date, product) -> RuleSet` loads the `ACTIVE` versions whose `rule_assignment` is effective on `on` with `product IS NULL OR product = :product`, as `tenant_pack_assignment` is resolved, cached per process for `MONITORING_RULE_CACHE_SECONDS` (keyed by the tenant's latest `activated_at`/`retired_at`). The evaluator filters by `segment_filter` (tier, risk rating, profile) and by `on`, reads the declared aggregates through the store, and returns `Firing(rule_version, key, window, severity, outcome, aggregates, explanation)` or nothing. Rule hits store the `assignment_id`, so "why did this rule apply to this customer on this date" is answered from the row.

### 9.6 Hits, dedup, cooldown, evidence (`FR-TM-006`, `NFR-TM-004`)

A firing becomes a `rule_hit` with `dedup_key = f"{key_kind}:{key}:{floor(occurred_at, dedup.window)}"` inserted `ON CONFLICT (tenant_id, rule_version_id, dedup_key) DO NOTHING`; a conflict links the transaction to the existing hit (`role = WINDOW`) and increments `fired_count`. A firing for the same `(rule_version, key)` whose latest hit has `cooldown_until > occurred_at` links the transaction (`role = COOLDOWN`) and creates no hit. Evidence: `rule_hit_transaction` rows for the triggering transaction and the window's matching transactions (bounded to 500 by `occurred_at DESC`), and `evidence.aggregates` with every declared value at firing time plus the parameters and the rendered explanation. Replaying the same stream through processing produces the same hits once (the replay test, §19). The staff API exposes hits with the party's masked summary and the list of transaction ids; the hit detail reveals names on request with `VIEW_PII`. `monitoring.rule_hit_created.v1` (staff-only) carries ids; SP-06 subscribes and backfills by `EventBus.replay()` (program spec §7.2).

## 10. Captures (`FR-TM-007`, D13)

### 10.1 What a capture is

A `reportable_event` is the legal fact "this transaction must appear in report R for period P", recorded independently of suspicion. A capture never opens a case, never raises a hit and never reaches the API client of the customer (tipping-off); reporting (SP-05) consumes the rows and marks them `INCLUDED`.

### 10.2 Evaluation (inside the processing transaction)

For each `CaptureDefinition` of the snapshot whose `on` the transaction matches:
- `THRESHOLD` and `FEE_THRESHOLD`: `converted = UnitService.threshold_in_ccy(threshold, currency, on=occurred_at date, rule)`; the event is written when `amount_minor >= converted` (fee: `fee_minor`). The `unit_amounts` entry for the threshold's unit is already on the row; `unit_values` on the event records both sides.
- `ACCUMULATION` (`AVI_XVI_A`): a transaction at or above the threshold is captured on its own; otherwise it joins the key's open `capture_group` (window `P6M` from the first operation's day, `ACCUM` aggregate in UMA at each operation's date, D21). When the group's accumulated value crosses the threshold, **every transaction of the group** is captured (one event each, `capture_group_id` set, `period_key` of each transaction's own month; the group is `CROSSED`), and later transactions of the same open window are captured as they arrive (`FR-TM-007` acceptance). A group whose window elapses without crossing stays `OPEN` as history and a new one starts at the next operation.
- `merge_into` (`AVI_XVI_B` → `AVI_XVI_A`): when the same transaction yields both, only the `AVI_XVI_A` event is written and its `fields.merged_captures = ["AVI_XVI_B"]` (D21's one-aviso rule; its legal basis is `UNVERIFIED` in the pack, so the behaviour is a golden the compliance review can flip).

### 10.3 Pre-mapped fields

`fields` is what the serializer line needs that does not come from the party: `{"operation_date","operation_type","instrument","amount":{"minor","currency"},"account_ref","branch":"0000","locality":null,"counterparty":{"country","institution","account_hash"},"crypto":{"network","asset_code","asset_qty"},"merged_captures":[…]}`, mapped through `report-defs/codetables/MX/*.yaml` code tables where SP-05's definitions need codes (`tipo_operacion`, `instrumento_monetario`); the mapping table is data (`captures/field_maps/<report_code>.yaml`), and the aviso's counterparty block is filled by SP-05 from the `counterparty` row with `VIEW_PII` at render time, never from the event.

### 10.4 Period close and reconciliation

`monitoring.period_close` (schedule `MONITORING_PERIOD_CLOSE_CRON`) runs on the first of the month for every `(capture_code, period_key)` that ended: it re-runs the capture definitions of the period's snapshot over the period's `PROCESSED` transactions in check mode (no writes), compares the set of transaction ids with the `CAPTURED` events, records `reconciled_count` and any `discrepancies` (ids only), writes the missing events with `late = true`, voids extra ones (`VOIDED`, `void_reason = RECONCILIATION`), sets the period `CLOSED` and publishes `monitoring.capture_period_closed.v1`. A discrepancy is a defect, not a business event; it fails the exit demo if non-empty. `xolotl monitoring period-close` runs it on demand.

### 10.5 Late events and supersession (`FR-TM-009`)

A late transaction (§8.5) re-runs captures for its period: a new event is written as usual; for an `ACCUMULATION` group whose window now starts earlier or whose crossing point moves, the group's events are superseded (`SUPERSEDED`, `superseded_by_id`; `monitoring.reportable_event_superseded.v1` per event) and re-captured in a new group (`supersedes_id`). When the period is `CLOSED`, it becomes `REOPENED` (`change_count + 1`) and `monitoring.capture_period_changed.v1` is published so SP-05 marks a drafted report `NEEDS_REGENERATION` (D14); an `INCLUDED` event is never silently changed: it is superseded and the new event is `CAPTURED`. The event on the API carries ids and the period only.

## 11. Counterparty screening and wallets (from SP-03: `FR-SC-012`, `DR-308`, `FR-SC-015`)

### 11.1 Counterparty resolution (`screening/counterparty/resolve.py`)

`screening.counterparty.resolve(session, input: CounterpartyInput) -> CounterpartyRef` (id, country, `screening_status`), called by the enricher; `dedup_hash_of(session, input) -> bytes`, the pure key computation; and `status_by_dedup_hash(session, dedup_hash) -> str`, called by the decide path with the former's result so a decision never upserts a row (`CounterpartyInput` and `CounterpartyKind` are `screening/counterparty/schema.py` types that `monitoring/txn/schema.py` imports): normalize the name with the SP-03 library's `normalize_name` (the folded form feeds the blind index through `blind_index("name", …)`), the account through `normalize_identifier("CLABE" | "ACCOUNT", …)`, the wallet address with whitespace removed, case kept (the OFAC parser's rule, applied by the `CounterpartyInput` validator and to `CryptoContext.address`); compute `dedup_hash`; upsert by `(tenant_id, dedup_hash)` (`last_seen_at`, `txn_count`); from plan 4c (§11.2, §22) a new row also inserts `counterparty_name_index` (when a name exists) and enqueues `screening.screen_counterparties` (key `screen_counterparties:<tenant minute>`; batches `MONITORING_COUNTERPARTY_SCREEN_BATCH` new rows); until then the row stays `UNSCREENED`. The decide path reads the row's `screening_status` by `dedup_hash` in one indexed query (the synchronous cache, `DR-308`): the status is whatever the last run concluded, `UNSCREENED` for a counterparty never seen before, and the decide response never waits for a screening.

### 11.2 Subjects and runs (`screening/counterparty/`, plan 4c)

`subject_for_counterparty(session, counterparty_id)` creates a `COUNTERPARTY` subject (plan 4c adds `SubjectKind.COUNTERPARTY` and `RunMode.COUNTERPARTY` to `screening/engine/enums.py`, with the code that uses them; values are stored as text, so no migration beyond 0012's constraint change). `query_for_counterparty` builds a `NameQuery` from the decrypted name (entity type from `kind`; `UNKNOWN` screens as `PERSON` and `ORGANIZATION` in two runs, the stricter status wins), `country`, the identifier and, for wallets, a `QueryIdentifier(IdType.DIGITAL_CURRENCY_ADDRESS, normalized=address)`. `screen(…, mode=RunMode.COUNTERPARTY, trigger=Trigger(type=TriggerType.JOB, ref=<job id>))` runs with the tenant's full list set; hits and findings land in `screening_match` as for parties, flagged by the subject kind in the queue; dispositions and whitelist apply unchanged. After a run the engine writes `counterparty.screening_status` and `screened_at` (`CLEAR`, `PENDING_REVIEW`, `TRUE_MATCH`) and publishes `screening.counterparty_status_changed.v1` (staff-only). A `TRUE_MATCH` confirmed on a counterparty fires `COUNTERPARTY_TRUE_MATCH` on the next transaction and shows on every past transaction's staff view.

### 11.3 Reverse screening and periodic re-screen

SP-03's list-delta blocking query (§10.2 there) gains a `UNION` over `counterparty_name_index`, and identifier blocking looks up `counterparty.identifier_hash` and `wallet_hash` beside the party blind index; the periodic re-screen walks counterparties after parties (`SCREENING_RESCREEN_BATCH` applies). A counterparty with `txn_count = 0` in the last 12 months is skipped by the periodic walk (cost control; §21 Q8). Retention: a counterparty is purged by `monitoring.retention` (§8.6) once no transaction references it, never earlier.

### 11.4 Crypto-wallet exact matching (`FR-SC-015`)

OFAC's `DIGITAL_CURRENCY_ADDRESS` identifiers are already parsed and indexed (SP-03 §4.1); `MemoryIndex.candidates` returns exact identifier hits first. A wallet-only counterparty (no name) needs a `NameQuery` with no names and one identifier: the library's `NameQuery.names` becomes optional when `identifiers` is non-empty and the scorer then scores identifiers only (`ID_MATCH_FLOOR` → 1.0; nothing else can match), a small library change plan 4b agrees with the library owner before relying on it. Addresses are compared case-sensitively and whole; no fuzzy or prefix matching (an address is a hash, not a name). The hit's explanation shows the masked address (`****a1b2`), network and the list entry's programs.

## 12. Synchronous decisions and tier limits (`FR-TM-003`, `FR-TM-008`, `NFR-TM-001`)

### 12.1 `POST /v1/transactions/decide`

Request: one `TransactionInput` (the payment about to be executed; its `rail` selects the rail setting). Flow, inside `MONITORING_DECIDE_BUDGET_MS`:
1. Validate; resolve `customer_ref` and `account_ref` (indexed reads); read `rail_setting` (default `MONITORING_DECIDE_FAIL_MODE`).
2. Profile: `customer_relationship` (tier, status), the party's screening status, the counterparty cache (§11.1), all point reads; the `sync` rule set from the cache (§9.5) and the snapshot for today and the product.
3. Units: `to_unit` for the sync rules' units with `UnitService` (the `unit_value` lookup is a cached indexed read; a missing value is a budget failure, never a stale value, ADR-021).
4. Aggregates: for each declaration the sync rules read, one `ix_agg_bucket_window` range read (the hot path, TS-05 §5: single-digit milliseconds).
5. Evaluate the sync rules through the same pure evaluator with a `MemoryAggregateStore` seeded from step 4 and then `add()`-ed the candidate, so the aggregates include it as they do in processing (§8.5). `BLOCK` if any `TIER_LIMIT` with outcome `BLOCK` or any sync rule with severity `CRITICAL` fires; `REVIEW` if any other sync rule fires; else `ALLOW`.
6. Write `txn_decision` (`mode = EVALUATED`, `ruleset_hash`, latency and database time) and publish `monitoring.decision_issued.v1` in the same transaction; when a `TIER_LIMIT` fired, insert `tier_limit_notice (relationship_id, limit_code, period_key)` `ON CONFLICT DO NOTHING` and publish `monitoring.tier_limit_reached.v1` only when the insert happened, so the asynchronous hit for the same relationship and month (§12.2) does not send it twice; respond.

Over budget, or on a database error: `FAIL_OPEN` answers `REVIEW` with `mode = FAIL_OPEN` and `reasons = [{"code": "DECIDE_UNAVAILABLE"}]`; `FAIL_CLOSED` answers `BLOCK` with `mode = FAIL_CLOSED`; both write the decision row when they can and count `OPS-502`'s fail metric; a decision row that cannot be written is logged and the response still goes out (`INT-505`). The response to the API client is `{decision, decision_id, reasons: [{code}], latency_ms}` where `code` is `TIER_LIMIT` (with `tier` and `limit_code`) or `RISK_REVIEW` (D20: stable codes, never a rule name; §21 Q5); the staff view of the decision (`GET /v1/monitoring/decisions/{id}`) shows the rules, aggregates and the linked transaction. The decision is linked to the later transaction by `external_id` in ingest (§6.1); the linked transaction carries `decision_id`, and the hit queue shows "decided BLOCK at 15:20:11".

`PUT /v1/monitoring/rails/{rail}` (ADMIN) sets `fail_mode` per rail, audited `monitoring.rail_setting_changed`. The budget is measured by a non-gating `perf` test; SP-11 gates `NFR-TM-001` under load (ADR-012 MVP gate: over 50 transactions per second sustained or p99 over 100 ms moves state out of Postgres).

### 12.2 Tier limits (`FR-TM-008`)

The `TIER_N1_MONTHLY_OPS` and `TIER_N2_MONTHLY_OPS` templates (§9.3) read `kyc.tiers[tier].limits.monthly_ops` (N1 750 UDI, N2 3,000 UDI, N3 none) from the transaction's snapshot with `units.tier_limit_rule = LAST_DAY_PREV_MONTH` (D5): the month-to-date `SUM(IN, MONTH)` of the relationship in `UDI/LAST_DAY_PREV_MONTH` (each transaction converted on its own date under that rule), which already includes the transaction under evaluation (§8.5), must not exceed the limit. On the decide path the outcome is `BLOCK` (N1) or `REVIEW` (N2) (`params.outcome`, tunable within `choices`). On the asynchronous path (a transaction that was executed anyway, or a batch without prior decisions) the rule produces a `rule_hit` with `outcome = BLOCK | REVIEW`, severity `HIGH`, dedup per relationship and month, and publishes `monitoring.tier_limit_reached.v1` through the same `tier_limit_notice` insert as the decide path (once per relationship, limit and month), the one webhook that reaches the client (`relationship_id`, `account_id`, `tier`, `limit_code`, `period_key`, `outcome`; no amounts: `PRV-502`), so the client prompts a KYC upgrade. `max_balance` needs the balance feed (`FR-TM-017`) and is not evaluated; the N1 balance limit is listed as deferred in §18.

## 13. Simulation, metrics and behaviour profiles

### 13.1 Simulation (`FR-TM-011`, `FR-TM-018`, `PRV-503`)

`POST /v1/monitoring/rules/{id}/versions/{v}/simulate` with `{range_start, range_end}` writes a `rule_simulation` row (`QUEUED`) and enqueues `monitoring.simulate` (key `simulate:<simulation_id>`). The job reads the tenant's `PROCESSED` transactions in the range ordered by `(party_id, occurred_at, id)`, `MONITORING_AGG_BACKFILL_BATCH` per page from `cursor`, feeding each party's stream through the version's declarations into a `MemoryAggregateStore` (one party at a time, so memory is bounded by one key's windows) and the pure evaluator with the snapshot of each transaction's date; hits are kept in memory and reduced to `hits_per_week`, `customers_hit`, `overlap` (existing `rule_hit` rows of the same party and overlapping window for any rule; `later_true_match` counts parties whose screening status became `TRUE_MATCH` after the simulated window; case overlap joins in SP-06) and a `sample` of up to 50 ids-only references. The job writes only `rule_simulation` and publishes `monitoring.simulation_completed.v1` (staff-only) on completion; no bucket, hit, capture, capture event or audit row results (the side-effect-free test counts rows in every monitoring table before and after). TS-05's "temporary schema" is replaced by the memory store (Decision 7). `xolotl monitoring replay --dry-run` is the same job over the active rule set, which is `FR-TM-018`'s replay without side effects; the exit test runs a week twice and compares hit sets.

### 13.2 Effectiveness metrics (`FR-TM-012`, `OPS-503`)

`GET /v1/monitoring/metrics?period=2027-02[&rule_version_id]` computes on read, per rule version and overall: hit volume, parties hit, hits per severity, median and p95 `created_at − first_occurred_at` (time from transaction to hit), typology coverage (templates active per typology tag), captures per report code and period versus `capture_period.reconciled_count` (`OPS-504`). `alert_to_case_rate` and `case_to_report_rate` are `null` until SP-06 fills them from the same endpoint. Risk scoring (SP-07) and the board pack (SP-10) call the same function. No metrics table in SP-04: the indexes on `rule_hit` and `reportable_event` serve a month in one query each; a materialised monthly row is the first step if SP-11's measurement says so.

### 13.3 Behaviour profiles (`FR-TM-013`)

`monitoring.profiles` (schedule `MONITORING_PROFILE_CRON`) computes, per relationship with activity in the previous month, the `metrics` of §5.2 from `PROCESSED` transactions, copies `customer_relationship.declared_profile`, derives `deviation` ratios when a declaration exists, sets `deviated` when any ratio is at or above `XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO` (default `2.0`; the pack has no field for it yet, §21 Q3) and publishes `monitoring.behaviour_profile_updated.v1` (staff-only; `relationship_id`, `month`, `deviated`); SP-07 subscribes and re-scores. `GET /v1/monitoring/profiles/{relationship_id}` returns the last 12 months (staff). `PATCH /v1/relationships/{id}` (added to the party module by plan 4c; staff and `parties:write`) accepts `declared_profile`.

## 14. Events, webhooks and audit

| Event | Audience | Data (ids only) |
|---|---|---|
| `monitoring.transaction_batch_accepted.v1` | tenant (webhook-safe) | `batch_id`, `accepted`, `duplicates` |
| `monitoring.transaction_file_completed.v1` | tenant (webhook-safe) | `file_id`, `status`, `rows_accepted`, `rows_rejected` |
| `monitoring.transaction_processed.v1` | tenant (webhook-safe; opt-in, off by default per endpoint) | `transaction_id`, `external_id`, `status` (`PROCESSED`, `PARKED`, `BACKFILL`); never a hit or capture count |
| `monitoring.transaction_parked.v1` | **staff-only** | `transaction_id`, `reason`, `unit_code`, `needed_on` |
| `monitoring.rule_hit_created.v1` | **staff-only** | `hit_id`, `rule_version_id`, `rule_code`, `party_id`, `relationship_id`, `severity`, `outcome`, `window_start`, `window_end` (SP-06 consumes; program spec §7.2) |
| `monitoring.tier_limit_reached.v1` | tenant (webhook-safe) | `relationship_id`, `account_id`, `tier`, `limit_code`, `period_key`, `outcome` (`PRV-502`); once per relationship, limit and month from either path (§12) |
| `monitoring.decision_issued.v1` | tenant (webhook-safe; opt-in) | `decision_id`, `external_id`, `decision`, `mode` |
| `monitoring.reportable_event_captured.v1` | **staff-only** | `reportable_event_id`, `capture_code`, `report_code`, `period_key`, `transaction_id` (SP-05 consumes) |
| `monitoring.reportable_event_superseded.v1` | **staff-only** | `reportable_event_id`, `superseded_by_id`, `period_key` |
| `monitoring.capture_period_closed.v1` · `monitoring.capture_period_changed.v1` | **staff-only** | `capture_period_id`, `capture_code`, `report_code`, `period_key`, `status`, `discrepancy_count` (SP-05 regenerates on `changed`) |
| `monitoring.rule_version_activated.v1` · `monitoring.rule_version_retired.v1` | **staff-only** | `rule_id`, `rule_version_id`, `assignment_id` |
| `monitoring.simulation_completed.v1` | **staff-only** | `simulation_id`, `rule_version_id`, `status` (also on `FAILED`) |
| `monitoring.behaviour_profile_updated.v1` | **staff-only** | `relationship_id`, `party_id`, `month`, `deviated` (SP-07 consumes) |
| `screening.counterparty_status_changed.v1` | **staff-only** | `counterparty_id`, `subject_id`, `status` |

The staff-only types are registered with `register_staff_only` at composition, so webhooks never carry them, wildcard subscriptions included (SP-03 §11); the test subscribes a wildcard endpoint, ingests a structuring scenario and a capture, and asserts no `monitoring.rule_hit_*`, `monitoring.reportable_event_*` or `monitoring.capture_period_*` delivery row exists. Names follow the platform's `module.noun_verb.v1` form (`screening.match_created.v1`). `monitoring.transaction_processed.v1` and `monitoring.decision_issued.v1` are opt-in because they are high-volume, not because they are sensitive. Event keys are the party id (processing), the relationship id (limits, profiles) or the rule version id (lifecycle), so a consumer sees one customer's events in order.

**Audit actions:** `monitoring.file_uploaded`, `monitoring.backfill_requested`, `monitoring.rail_setting_changed`, `rules.loaded` (CLI, owner), `rules.seeded`, `rules.version_created`, `rules.version_updated`, `rules.version_submitted`, `rules.version_approved`, `rules.version_rejected`, `rules.version_activated` (also `CHANGE_POLICY`), `rules.version_retired`, `rules.simulation_requested`, `relationship.declared_profile_set`, plus `VIEW_PII` when a staff transaction, hit or counterparty view reveals a name, account, wallet or the raw payload. Batch ingest by an API key is not audited per transaction (volume); the `transaction_batch` row with `submitted_by` and `request_id` is its provenance record, as `list_version` is for list ingestion.

## 15. API

Machine view (API client) = acceptance, status and stable codes; never a hit, a capture, a rule name or a counterparty's screening status (D20, `PRV-502`, tipping-off). Staff view = full typed detail with `allowed_actions` and `blocked_reasons`. Cursor pagination is the platform's keyset cursor; every POST takes `Idempotency-Key`.

| Method and path | Audience; access | Request → response | Errors |
|---|---|---|---|
| `POST /v1/transactions` | API key `transactions:write`; staff writers | `BatchRequest` → 202 `{batch_id, accepted, duplicates, transaction_ids[]}` | 422 `TRANSACTION_BATCH_INVALID` (`errors[]`), 413 `TRANSACTION_BATCH_TOO_LARGE`, 409 `EXTERNAL_ID_CONFLICT` |
| `POST /v1/transactions/files` | API key `transactions:write`; staff writers | multipart `file` → 202 `{file_id, status}` (200 when the sha256 exists) | 413 `TRANSACTION_FILE_TOO_LARGE`, 422 `TRANSACTION_FILE_INVALID` (unreadable header) |
| `GET /v1/transactions/files/{id}` · `GET …/files/{id}/errors` | both (`transactions:read`) | status and counts; the error report (CSV download) | — |
| `GET /v1/transactions/batches/{id}` | both | counts, `request_id` | — |
| `GET /v1/transactions` | staff; API key `transactions:read` | filters `party_id`, `relationship_id`, `external_id`, `from`, `to`, `status`, `instrument`, `rail`; cursor | — |
| `GET /v1/transactions/{id}` | staff; API key `transactions:read` | client: `{id, external_id, occurred_at, status, late}`; staff: the row with `unit_amounts`, periods, masked counterparty, snapshot, decision, hits, captures; `?reveal=true` decrypts the counterparty and `raw` with `VIEW_PII` | 404 |
| `GET /v1/transactions/parked` · `GET /v1/transactions/backfill` | staff | parked and `BACKFILL` rows with reasons | — |
| `POST /v1/monitoring/backfill` | staff MLRO, ADMIN | `{from, to, party_id?}` → 202 job; processes `BACKFILL` rows, reopens periods | 422 `BACKFILL_RANGE_INVALID` |
| `POST /v1/transactions/decide` | API key `transactions:decide` (`write=True`) | `TransactionInput` → 200 `{decision, decision_id, reasons: [{code, tier?, limit_code?}], latency_ms}` | 422 field errors, 404 `RAIL_UNKNOWN`; never 503 (fail mode answers) |
| `GET /v1/monitoring/decisions/{id}` | staff; API key `transactions:read` (client: decision and mode only) | the decision with rules, aggregates and the linked transaction (staff) | 404 |
| `GET /v1/monitoring/rails` · `PUT /v1/monitoring/rails/{rail}` | staff; ADMIN for PUT | `{fail_mode}` | 404 `RAIL_UNKNOWN` |
| `GET /v1/monitoring/hits` · `GET /v1/monitoring/hits/{id}` | staff only | queue filters `severity`, `rule_code`, `typology`, `outcome`, `party_id`, age; detail with evidence (aggregates, params, rendered explanation, transactions with `role`), masked party, `decision`; `?reveal=true` with `VIEW_PII` | 404 |
| `GET /v1/parties/{id}/hits` | staff only | the party's hits (TS-05's `GET /v1/customers/{id}/alerts`) | — |
| `GET /v1/monitoring/captures` · `GET /v1/monitoring/captures/{id}` | staff only | filters `capture_code`, `report_code`, `period_key`, `status`, `relationship_id`; detail with `fields`, `unit_values`, group and supersession chain | — |
| `GET /v1/monitoring/captures/periods` · `POST /v1/monitoring/captures/periods/{id}/close` | staff; MLRO, ADMIN for close | period rows with reconciliation; manual close runs §10.4 now | 409 `PERIOD_NOT_ENDED` |
| `GET /v1/monitoring/templates` · `GET /v1/monitoring/templates/{code}` | staff; API key `rules:read` | the library with parameter bounds and the rendered sentence | — |
| `GET /v1/monitoring/rules` · `GET /v1/monitoring/rules/{id}` | staff; API key `rules:read` | tenant rules with their versions, status, assignment, last simulation | — |
| `POST /v1/monitoring/rules` · `POST /v1/monitoring/rules/{id}/versions` · `PATCH …/versions/{v}` | staff SENIOR_ANALYST, MLRO, ADMIN | `{template_code, params?, segment?, severity?, dedup?}` → 201 the version | 404 `RULE_TEMPLATE_UNKNOWN`, 422 `RULE_PARAM_OUT_OF_BOUNDS` (names the parameter, the bound and its source), 409 `RULE_TRANSITION_NOT_ALLOWED` |
| `POST …/versions/{v}/simulate` · `GET /v1/monitoring/simulations/{id}` | staff (§9.4) | `{range_start, range_end}` → 202 `{simulation_id}`; the result | 422 `SIMULATION_RANGE_TOO_LONG` |
| `POST …/versions/{v}/submit` · `approve` · `reject` · `activate` · `retire` | staff (§9.4) | `activate`: `{effective_from, product?}`; `reject`: `{reason}` | 409 `RULE_TRANSITION_NOT_ALLOWED` with `blocked_reasons` |
| `GET /v1/monitoring/assignments` | staff | assignments effective on `?on=` | — |
| `GET /v1/monitoring/metrics?period=` | staff; API key `monitoring:read` | §13.2 | 422 `PERIOD_INVALID` |
| `GET /v1/monitoring/status` | staff ADMIN | `OPS-501` counters: `RECEIVED` rows by age, parked and backfill counts, oldest `RECEIVED`, running processing jobs, partitions ahead | — |
| `GET /v1/monitoring/profiles/{relationship_id}` | staff | last 12 behaviour profiles | — |
| `GET /v1/counterparties` · `GET /v1/counterparties/{id}` | staff only | masked list with screening status and `txn_count`; detail reveals with `VIEW_PII`; findings link to the screening queue | — |
| `PATCH /v1/relationships/{id}` | staff writers; API key `parties:write` | `{declared_profile}` (party module; new in plan 4c) | 422 |

Literal routes (`/v1/transactions/parked`, `/v1/transactions/backfill`, `/v1/monitoring/captures/periods`) are registered before their `/{id}` siblings. New API-key scopes: `transactions:read`, `transactions:write`, `transactions:decide`, `rules:read`, `monitoring:read`. Errors added to the catalogue: `TRANSACTION_BATCH_INVALID`, `TRANSACTION_BATCH_TOO_LARGE`, `EXTERNAL_ID_CONFLICT`, `TRANSACTION_FILE_TOO_LARGE`, `TRANSACTION_FILE_INVALID`, `BACKFILL_RANGE_INVALID`, `RAIL_UNKNOWN`, `RULE_TEMPLATE_UNKNOWN`, `RULE_PARAM_OUT_OF_BOUNDS`, `RULE_TRANSITION_NOT_ALLOWED`, `SIMULATION_RANGE_TOO_LONG`, `PERIOD_NOT_ENDED`, `PERIOD_INVALID`; `POLICY_LOOSENING` (SP-02) covers a capture override that loosens; `UNIT_VALUE_MISSING` never surfaces on ingest (the row parks) and surfaces on decide only as the fail mode.

## 16. CLI

`monitoring ingest-file --tenant <slug> <path>` (the dev, CI and demo path), `monitoring process --tenant <slug> [--party <id>]` (drain now), `monitoring sweep --tenant <slug>`, `monitoring backfill --tenant <slug> --from --to [--party]`, `monitoring replay --tenant <slug> --from --to --dry-run` (simulation over the active set; prints hit counts per rule and compares with stored hits), `monitoring period-close --tenant <slug> [--capture <code> --period <key>]`, `monitoring profiles --tenant <slug> --month <YYYY-MM>`, `monitoring partitions ensure`, `monitoring show <transaction_id> --tenant <slug> [--reveal]`, `rules load [path]` (owner role; also run by `xolotl db upgrade`), `rules validate <path>`, `rules seed-tenant --tenant <slug>`, `rules show <code> [--version]`, `rules simulate --tenant <slug> --version <id> --from --to`, `rules activate --tenant <slug> --version <id> --approved-by <email> [--simulate-from --simulate-to]` (for seeding demos: runs a simulation over the given range, or over the last 30 days, synchronously, then submits, approves and activates, so the `simulation_id` CHECK holds; audited like the API), `screening screen-counterparties --tenant <slug>`.

## 17. Console increments (Spanish-first, keyed strings; DD-00 navigation; DD-05 §3.3)

- **Transacciones** (`/transactions`, nav `nav.transactions`, every staff role): list with filters (customer, date range, status, instrument, rail, late), the batch and file panels (upload with the template download, progress, the row-error report), and the parked and backfill tabs with reasons and the "Ejecutar backfill" action (MLRO, ADMIN).
- **Detalle de transacción**: the canonical fields, unit amounts with the `unit_value` used and its rule, period keys, the counterparty card (masked; "Revelar" calls `?reveal=true` and shows the `VIEW_PII` notice; screening status badge), the decision (if any), hits and captures on this transaction, the raw payload behind "Revelar".
- **Hallazgos de reglas** (`/monitoring/hits`, nav `nav.ruleHits`; ACCESS `monitoring`: every staff role): the hit queue by severity and age, filters by rule, typology and outcome; detail with the rendered rule sentence, parameters, aggregates at firing time against thresholds, the evidence transactions table (role, date, amount, instrument, counterparty masked), the decision link, and the dedup and cooldown state. No disposition in SP-04 (hits become alerts in SP-06); a "Marcar como revisado" note field is not offered (it would be a second queue).
- **Reglas › Monitoreo** (`/rules/monitoring`, nav `nav.monitoringRules`; ACCESS `rules`: SENIOR_ANALYST, MLRO, ADMIN write; ANALYST, AUDITOR_RO read): the library with the rendered sentence per template; tenant rules with versions and status; the version editor (parameters as native controls with bounds and the bound's source, segment picker, severity, dedup) with the rule text read-only; the simulation panel ("Simular últimos 6 meses", last simulation with hits per week, customers, overlap, sample, age badge "válida 30 días"); the lifecycle buttons from `allowed_actions` with `blocked_reasons` as tooltips ("El aprobador debe ser distinto del autor").
- **Reglas › Capturas y límites** (`/rules/captures`, nav `nav.captures`): the effective policy's captures and tier limits read-only with provenance, each with "Proponer ajuste" linking to the SP-02 override flow; the period table (capture, period, status, events, reconciled, discrepancies) with "Cerrar periodo" (MLRO, ADMIN).
- **Contrapartes** (`/counterparties`, nav `nav.counterparties`): masked list with screening status, first and last seen, transaction count; detail with reveal and the link to its findings in the screening queue.
- **Cliente › Transacciones** and **Cliente › Perfil de comportamiento** tabs on the party detail: the relationship's transactions, hits, captures, decisions; the monthly profile chart against the declared profile with the deviation badge; "Editar perfil declarado".
- **Admin › Rieles**: fail mode per rail.
- No customer-facing string mentions a rule, a hit, a capture or a suspicion (DD-00 tipping-off rule); the limit webhook's copy in the developer docs says "límite de nivel de cuenta", nothing else.

## 18. Requirement traceability

| Requirement | Status | Design | Test |
|---|---|---|---|
| FR-TM-001 ingest by API batch, file, streaming; canonical schema; idempotent on the external id | in scope (batch, file); streaming deferred past SP-11, where the ADR-012 gate decides the Kafka adapter | §4, §5.2 `transaction_ref`, §6 | duplicate batch creates nothing; field errors; file row report |
| FR-TM-002 enrichment in USD, local and every threshold unit; customer, relationship, rating, profile, tier; counterparty status; business day and periods | in scope (rating and profile default until SP-07) | §7 | enrichment goldens: UMA 1 February, FIX across a holiday, UDI January |
| FR-TM-003 synchronous decision with fail mode per rail; every decision stored and linked | in scope | §12.1, `txn_decision`, `rail_setting` | linkage test; fail-open and fail-closed tests; `perf` latency record; load in SP-11 |
| FR-TM-004 readable rule language, declared aggregates, versioned and compiled | in scope as parameterised kinds with a rendered sentence (Decision 1); the grammar deferred (§21 Q1) | §9.1, §9.3 | definition goldens; `render()` goldens in es-MX; simulation equals production (§13.1) |
| FR-TM-005 aggregate functions | in scope: sum, count, avg, max, min, distinct count, first-seen, time-since (`LAST_SEEN`), ratio to profile (PROFILE_DEVIATION), deviation from own history, list membership (jurisdiction lists); declared state only | §8.2, §9.1 | function unit tests on both stores; contract test Postgres = memory |
| FR-TM-006 alerts with evidence, exactly once, deduplicated, prioritised | in scope as rule hits (D13; alerts in SP-06); priority by severity (risk in SP-07) | §9.6 | replay produces the same hits once; evidence golden |
| FR-TM-007 captures per pack, legal units, windows, periods, pre-mapped fields, unit values | in scope | §9.2, §10 | capture goldens per code; monthly-multiple (accumulation) behaviour; unit edge dates |
| FR-TM-008 tier limits with block or review and the webhook | in scope for `monthly_ops`; `max_balance` deferred with FR-TM-017 | §12.2 | N1 750 UDI golden on both paths; webhook payload test |
| FR-TM-009 late and out-of-order events; backfill | in scope | §8.5, §10.5 | late event corrects totals, supersedes captures, reopens a closed period |
| FR-TM-010 typology library with bounds | in scope (Mexico; Brazil v1.1) | §9.3 | one golden scenario per template |
| FR-TM-011 simulation with isolated state; approval requires a recent simulation | in scope | §13.1, §9.4 | side-effect-free test; stale simulation blocks approval |
| FR-TM-012 effectiveness metrics | in scope for what SP-04 knows; alert-to-case, case-to-report and handling time in SP-06 | §13.2 | metric computation test |
| FR-TM-013 behaviour profile monthly, compared with declared, published to risk | in scope | §13.3 | deviation event test |
| FR-TM-014 MX non-financial automated monitoring | in scope for captures, accumulation, PEP, blocked persons, jurisdictions and cash rules; high-risk customers after SP-07; the attestation document in SP-10 | §9.3, §10 | AVI goldens |
| FR-TM-015 Brazil Pix hooks | out of scope (v1.1) | — | — |
| FR-TM-016 governance: lifecycle, tighten-only captures, approver differs | in scope | §9.4, §9.2, `CHECK (approved_by <> author_id)` | state × role × user matrix; loosening override refused (SP-02 golden) |
| FR-TM-017 daily balance feed | out of scope (SHOULD; not in MVP per TS-05) | — | — |
| FR-TM-018 replay of any period without side effects | in scope | §13.1 (`monitoring replay --dry-run`) | replay-twice test |
| NFR-TM-001 decision p99 ≤ 100 ms, 99.95 % | measured, not gated (ADR-022; SP-11 load test) | §12.1 budget | `perf` record |
| NFR-TM-002 alert p95 ≤ 5 s | designed for (§8.5); measured in SP-11 | §8.5 | `perf` record over 1,000 transactions |
| NFR-TM-003 500 tps launch, 5,000 growth | out of the MVP profile; ADR-012 gate at 50 tps sustained | §8.5 | SP-11 |
| NFR-TM-004 exactly-once alerting | in scope | `rule_hit` unique key, §9.6 | replay test |
| NFR-TM-005 10-year queryable history | in scope: monthly partitions, no cut-off before `retention.transactions` (ADR-017 MVP); the lake is the production profile | §5.2, §8.6 | retention job drops nothing before the period and honours legal hold |
| NFR-TM-006 crash mid-batch restores with no duplicates | in scope | §8.5 one transaction per row; queue lease | kill the worker mid-page; resume; no duplicate hit |
| DR-501 partitioned `transaction`, unique external id, encrypted counterparty with blind index, units jsonb | in scope (`unit_amounts`; counterparty in its own table) | §5.2 | isolation harness; partition RLS test |
| DR-502 compiled definition and hash, lifecycle, assignment with params within bounds, captures tighten only | in scope (`definition_hash`; params in the version; captures through overrides) | §5.2, §9.1, §9.4 | bounds test |
| DR-503 `agg_bucket` hourly and daily, `agg_period` calendar and accumulation | in scope as one table with `bucket_kind` (Decision 6) | §5.2, §8.2 | window tests |
| DR-504 alert unique on (rule version, key, window); evidence | in scope as `rule_hit` and `rule_hit_transaction` (D13) | §5.2, §9.6 | dedup test |
| DR-505 `reportable_event` unique, fields, unit values, statuses | in scope with D13's key (per transaction, groups for accumulation; Decision 8) | §5.2, §10 | capture goldens |
| DR-506 `txn_decision` partitioned, ruleset hash, linked by external id | in scope | §5.2, §12.1 | linkage test |
| DR-507 `behaviour_profile` | in scope | §5.2, §13.3 | profile test |
| DR-508 consumer offset checkpoint | in scope as the job queue's per-key lease plus row status (no outbox consumer; Decision 3) | §8.5 | crash test |
| INT-501 batch validation, dedup, `txn.raw` | in scope without the topic (rows and jobs instead; Decision 3) | §6.1 | — |
| INT-502 enrichment consumer, parking on a missing unit | in scope inside processing | §7 | parking test |
| INT-503 events produced | in scope under versioned names (§14) | §14 | event tests |
| INT-504 webhooks: alerts to staff only; limit reached; decision opt-in | in scope | §14 | wildcard webhook test |
| INT-505 fail-open or fail-closed per rail | in scope | §12.1 | fail-mode tests |
| SEC-501 approver differs; captures and floors tighten only | in scope (database CHECK too) | §9.4, §9.2 | matrix; SQL insert refused |
| PRV-501 counterparty identifiers encrypted with blind index; aggregates on hashes; evidence by ids | in scope | §5.2, §8.3, §9.6 | no plaintext name in `counterparty`, `agg_*`, `rule_hit` (PII scan) |
| PRV-502 `alert.created` never to clients; limit webhook references only | in scope | §14 | webhook tests |
| PRV-503 simulation inside the tenant on production data; no raw export | in scope | §13.1 | side-effect and isolation tests |
| OPS-501 ingest throughput and backlog, enrichment and consumer lag | in scope as counters: `RECEIVED` rows by age per tenant, parked count, oldest `RECEIVED` (`GET /v1/monitoring/status`, ADMIN; `/healthz` stays unauthenticated and minimal) | §8.5 | — |
| OPS-502 decision p50, p95, p99 and fail counts | in scope from `txn_decision` (`latency_ms`, `mode`) | §12.1 | — |
| OPS-503 hits per rule per period; case rates (SP-06); time to hit | in scope | §13.2 | — |
| OPS-504 captures per report code per period versus reconciled totals | in scope | §10.4, §13.2 | reconciliation test |
| FR-SC-012, DR-308 counterparty screening and cache | in scope | §11 | first-sight run; delta re-screen hits a counterparty; decide reads the cache |
| FR-SC-015 wallet matching | in scope (exact) | §11.4 | OFAC address fixture matches a wallet counterparty; a one-character difference does not |

## 19. Test matrix and exit criteria

| Suite | Proves |
|---|---|
| `tests/modules/monitoring/txn/*` | schema validation and field errors; batch all-or-nothing; idempotent re-send; `EXTERNAL_ID_CONFLICT`; account and relationship resolution; file ingest CSV, JSON Lines and XLSX with partial acceptance and the error report; resume from `cursor`; `raw_enc` holds the counterparty, nothing else does |
| `tests/modules/monitoring/enrich/*` and `tests/golden/monitoring/enrichment/` | unit amounts with `unit_value_id` per rule; parking and un-parking; period keys; counterparty resolution and dedup; device hash |
| `tests/modules/monitoring/aggregates/*` | bucket choice per window; Postgres and memory stores agree on a replayed stream; distinct counts; calendar and accumulator reads; backfill of a new declaration; `INCOMPLETE` blocks firing |
| `tests/modules/monitoring/process/*` | per-key ordering under two concurrent jobs; one transaction per row (kill mid-page, resume, no duplicate); late row re-evaluation; `BACKFILL` status and the backfill job; sweep closes the enqueue race and un-parks |
| `tests/modules/monitoring/rules/*` and `tests/golden/monitoring/typologies/<code>/` | every kind's evaluator; the library loads and is immutable; params and bounds; `render()` goldens; the lifecycle matrix (state × role × same-user × action); assignment by date and product; dedup and cooldown; evidence golden |
| `tests/modules/monitoring/captures/*` and `tests/golden/monitoring/captures/<code>/` | each capture code over a fixture period produces exactly the expected events; accumulation captures the whole group on crossing; `merge_into`; supersession on a late event; period close reconciliation with an injected gap |
| `tests/modules/monitoring/decide/*` | ALLOW, REVIEW, BLOCK; N1 limit golden; fail-open and fail-closed on an injected budget overrun; linkage by `external_id`; client view hides rule names; `perf` record |
| `tests/modules/monitoring/simulation/*`, `profiles/*` | no side effects (row counts of every monitoring table before and after); replay twice gives the same hits; metrics computation; profile deviation event |
| `tests/modules/screening/counterparty/*` | first-sight screening; status cache; delta re-screen over `counterparty_name_index`; wallet exact match; `UNKNOWN` kind screened both ways |
| platform | partitions generalised; `transaction` partitions carry forced RLS; staff-only monitoring events never reach webhooks |
| isolation harness | every new tenant table and partition auto-discovered; `rule_template`, `rule_template_version` registered as shared |
| console | typecheck, lint, unit tests; Playwright: upload a CSV, open the transaction, reveal the counterparty, see the hit with evidence, tune a rule parameter, simulate, submit, approve with a second user, activate |

**Exit criteria** (program spec §7.3 "transactions in; captures", refined): `make ci` green; two tenants exist, A with `MX/ITF-IFPE` (product `EMONEY`) and `MX/LFPIORPI-AVI` (product `CRYPTO`), B with `MX/ITF-IFPE`; `xolotl rules load` installs the Mexico typology library, `xolotl rules seed-tenant` gives tenant A draft versions and `xolotl rules activate` activates `CASH_STRUCTURING`, `COUNTERPARTY_TRUE_MATCH` and `TIER_N1_MONTHLY_OPS` for the demo; a `POST /v1/transactions` batch of fixtures for tenant A (the week of 7 to 13 December 2026: cash deposits, an international transfer, crypto operations with fees, one counterparty whose name is a perturbed OFAC entry and one whose wallet is an OFAC address; plus one crypto operation per month from July to December 2026 for the AVI accumulation) is accepted, re-sent without duplicates, and processed by the worker; `GET /v1/transactions/{id}` as staff shows UDI, UMA and USD amounts with the `unit_value` rows and rules used; `CASH_STRUCTURING` fires once with three near-threshold deposits as evidence and `GET /v1/monitoring/hits` shows it while the API-client view of the same transactions shows only `PROCESSED`; `ITF_ROR_CASH` captures exactly the cash operation at or above USD 5,000 at the prior banking day's FIX across the 12 December Banxico holiday and `AVI_XVI_A` captures the whole six-month group when the December operation crosses 210 UMA, each event carrying its period key and unit values; a crypto operation dated 10 December arriving on 12 December changes the totals, re-fires nothing twice, and supersedes and re-captures the affected group; `xolotl monitoring period-close` closes December 2026 and Q4 2026 with zero discrepancies; `POST /v1/transactions/decide` for an N1 account about to exceed 750 UDI answers `BLOCK` with `TIER_LIMIT` in under the budget and `monitoring.tier_limit_reached.v1` reaches tenant A's webhook once while no `monitoring.rule_hit_*` or `monitoring.reportable_event_*` event does; the OFAC-named counterparty and the OFAC wallet show `PENDING_REVIEW` in `GET /v1/counterparties` and in the screening queue; a tenant A analyst edits `CASH_STRUCTURING`'s `near_ratio`, simulates six months, submits, a second user approves and activates it, and `xolotl monitoring replay --dry-run` over the week reproduces the stored hits; tenant B sees none of tenant A's transactions, hits, captures or counterparties; `GET /v1/audit/verify` is intact for both tenants. Scripted as `tests/e2e/test_sp04_exit.py` over committed fixtures (`tests/e2e/fixtures/sp04/`).

## 20. Decisions

1. **Parameterised rule kinds, not an expression grammar, in SP-04.** ADR-005 and XOL-7 specify a typed expression language with a compiler, type checker and function catalogue, and PRD-05 asks for rules an analyst can read and predict. This spec builds the seven ALERT kinds (sentinel's six plus `TIER_LIMIT`, D22, D13) and the three CAPTURE kinds as typed documents with parameters, bounds, declared aggregates and a rendered sentence per locale. Every template of the Mexico library (§9.3) and every capture is expressible this way once `AGGREGATE` takes a conditions list with ratios, `TxnFilter` has region and round-amount predicates and `VELOCITY` has a dormancy gate (fix round 1, C2); sequences inside a window are not expressible and are approximated by short windows. That is what "no feature is added without a rule in the Mexico kit that needs it" (ADR-005's own MVP note) amounts to for the launch; a grammar would cost a parser, a type checker, a language reference and an editor with validation for one developer, before the first tenant tunes a rule. The definition carries `language: "KIND/1"`, aggregates are declared in the same `AggregateDeclaration` shape ADR-005 describes, and the evaluator's `EvalContext` is the typed context a compiled expression would read, so XOL-7 can land as `language: "EXPR/1"` on the same tables, stores, hits and simulation without a migration. The grammar is §21 Q1.
2. **Captures are policy, not tenant rule versions.** Their thresholds, units, rules, windows and periods already live in the SP-02 packs with provenance and tighten-only overrides; giving them a second lifecycle would let a tenant draft a looser capture. They are evaluated from the transaction's policy snapshot by the same evaluator, and `reportable_event.policy_snapshot_id` is their version. Tier limits are rule versions (Decision 17).
3. **No `txn.raw` and `txn.enriched` topics; rows and per-key jobs instead.** The transaction row's `status` is the queue, the `monitoring.process` job per party is the per-key lease D12 asks for, and one database transaction per transaction is the checkpoint (`DR-508`). The outbox stays what it is, a publisher of facts; ingest of 1,000-item batches through it would triple the rows for no ordering gain.
4. **Processing is asynchronous, enrichment included.** D12 says aggregates update "inside the ingest transaction"; this spec reads that as the transaction in which the row is processed, not the HTTP request, so a 1,000-item batch is acknowledged in one write and evaluated in order by the worker. The decide path is the synchronous exception and reads the same tables.
5. **Files accept valid rows and report the rest; batches are all-or-nothing.** TS-05 §4 asks for a row-level error report on files and field-level rejection on batches; a batch is a client's unit of retry, a file is a day's export.
6. **One `agg_bucket` table with `bucket_kind`; exact distinct counts.** TS-05's `agg_bucket` plus `agg_period` become one table (same key, same reads); HyperLogLog needs an extension or a library and the MVP's distinct counts fit a member table.
7. **Simulation runs in memory, not in a temporary schema.** The pure evaluator plus `MemoryAggregateStore` per party is the isolation; it also doubles as the contract test of the Postgres store.
8. **`reportable_event` is unique per transaction per capture (D13), not per transaction set (`DR-505`).** Multiple-operation captures are a `capture_group` whose members each have an event, so the serializer and the reconciliation see one row per operation.
9. **Counterparties are rows, screening subjects of their own kind, and the synchronous cache is the row.** No separate cache table and no synchronous screening on the decide path; a never-seen counterparty is `UNSCREENED`, which rules treat as unknown.
10. **`PROFILE_DEVIATION` reads a `declared_profile` on the relationship.** Onboarding (SP-08) will fill it; until then staff set it through the party API, and the rule stays silent without it and without three months of history.
11. **Tipping-off on the decide path.** The client sees `TIER_LIMIT` (a product limit the customer already knows) or `RISK_REVIEW`; rule names, aggregates and screening statuses are staff-only.
12. **The library is pack content under `policy-packs/MX/_typologies/`,** loaded into shared `rule_template*` tables (D1: platform templates are shared; tenant copies carry `template_version_id`), and `rule_template.packs` ties templates to pack codes so seeding follows assignments.
13. **Rule assignment mirrors pack assignment** (date, product, approver) rather than TS-05's `rule_assignment(active)` flag, so "which rules applied on this date for this product" is the same question as "which packs".
14. **Hits are not alerts.** SP-04 has no disposition, note or queue state on a hit; SP-06 creates alerts from `monitoring.rule_hit_created.v1` and replays the stored events.
15. **Partition maintenance is shared with `audit_event`** (`xolotl.platform.partitions`), one helper for three partitioned tables.
16. **Period close reconciles by re-evaluation**, not by a second bookkeeping path: the check mode of the capture evaluator over the period is the oracle, and a discrepancy is a bug to fix, not a status to work.
17. **`TIER_LIMIT` is an ALERT template (D13), not policy-derived.** Its threshold comes from the snapshot's `kyc.tiers` at evaluation (so it cannot be loosened), its only parameter is the outcome, and it has versions, a simulation and an assignment like every alert rule, so every hit has a rule version, an assignment and a definition hash (fix round 1, C1).
18. **Screening owns counterparties.** `counterparty` is a screening table created in plan 4a's migration (0011) and `counterparty_name_index` in plan 4b's (0012), both under `screening/counterparty/`, whose `schema.py` defines `CounterpartyInput` and `CounterpartyKind` for monitoring to import; `screening.counterparty.resolve()` and `status_by_dedup_hash()` are the services monitoring calls, and `transaction.counterparty_id` is the only thing monitoring keeps. No cycle: screening never imports monitoring (fix round 1, C3).
19. **Unit amounts are keyed by `<unit>/<rule>`**, departing from D11's one-rule-per-unit map, because `UDI` is read under `SAME_DAY` for `ITF_VA_OPS` and under `LAST_DAY_PREV_MONTH` for tier limits; the local currency `MXN/SAME_DAY` is always present (`FR-TM-002`) (fix round 1, C4, I1).
20. **Wallet addresses are PII-class.** They live in `counterparty.wallet_enc` with a blind index and in `raw_enc`, never in `transaction.crypto`; `tx_hash` stays plain (a public chain fact) (fix round 1, I5).
21. **File drops accept CSV, JSON Lines and XLSX.** TS-05 names CSV and JSON Lines; XLSX is added because compliance officers export spreadsheets and the reader (`openpyxl`) is already a dependency; it is first on the cut list (fix round 1, I11).
22. **Retention retires a month in dependency order, then drops the partition.** Postgres will not detach a partition that rows reference, so `monitoring.retention` deletes the month's hit links, reportable events, hits, decision links, parked and ref rows before the `transaction` partition goes; hits and captures share `retention.transactions`, so that is the policy, not a loss. Nullable `ON DELETE SET NULL` references were the alternative; they would make every hit's evidence optional for the sake of a job that runs once a month (fix round 2, N3).

## 21. Open questions for the product owner (each with the default this spec builds)

1. **Rule expression language (XOL-7, ADR-005).** Build the typed grammar, compiler, type checker and catalogue in SP-04, or keep parameterised kinds and schedule the grammar as its own package? *Default:* kinds only (Decision 1); XOL-7 becomes a parallel pure-library package after SP-04, like the screening library, landing as `language: "EXPR/1"`.
2. **Tenant-authored rules from bare kinds.** ADR-004 leaves open whether tenants may author beyond the library. *Default:* off (`XOLOTL_MONITORING_ALLOW_CUSTOM_RULES = false`); library templates with tunable parameters only.
3. **Deviation threshold for behaviour profiles.** No pack field exists. *Default:* `XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO = 2.0`, and a pack field (`monitoring.profile_deviation_ratio`) in the next pack schema version.
4. **Fail-open or fail-closed per rail** (PRD-05 §13). *Default:* `FAIL_OPEN` answering `REVIEW` for every rail; the design partner sets `FAIL_CLOSED` per rail in Admin › Rieles.
5. **Reasons on the decide response.** *Default:* Decision 11 (`TIER_LIMIT` with the tier and limit code, `RISK_REVIEW` otherwise; no rule names to clients).
6. **Corrections and reversals.** The canonical schema has no amend or cancel. *Default:* a transaction is immutable; a reversal is a new transaction in the opposite direction with `raw.reverses = <external_id>`; corrections are a later schema version.
7. **Crypto amounts.** Thresholds read the fiat `amount` the client reports for a crypto operation. *Default:* required for `instrument = CRYPTO` (the client's own valuation); platform-side asset pricing is not built.
8. **Periodic re-screen of dormant counterparties.** *Default:* counterparties without a transaction in 12 months are skipped by the periodic walk and re-screened on their next transaction; list deltas still cover them.
9. **Do tenants send all transactions or only above a floor** (PRD-05 §13)? *Default:* all; the integration guide says so, and structuring rules assume it.
10. **ROR period granularity** (month versus quarter, `UNVERIFIED` in the pack). *Default:* events carry both `MONTH` and `QUARTER` keys and `capture_period` rows are quarterly for `ITF_ROR_CASH`; SP-05 chooses the report period when the regulator's expectation is confirmed.

## 22. Plan split

| Plan | Contents | Depends on | Can start |
|---|---|---|---|
| **4a — Transactions, enrichment, aggregates** | migration 0011; `xolotl.platform.partitions`; canonical schema and validation; accounts and counterparties (resolution and blind index; screening status stays `UNSCREENED`); batch and file ingest with the error report; enrichment with units, periods and snapshots; parking and the sweep; `AggregateStore` with both adapters, declarations, windows, distinct counts, backfill; the processing job with ordering, late and backfill statuses (no rules yet: the evaluator hook is a no-op list); transactions API, CLI (`ingest-file`, `process`, `sweep`, `backfill`, `partitions`), the Transacciones pages | SP-02 party core and units, SP-01 jobs and object store; nothing from SP-03 | now |
| **4b — Rules, simulation, hits, captures** | migration 0012 (including `counterparty_name_index`); definitions, kinds and the pure evaluator; the typology library files and loader; rule versions, lifecycle, assignments, `rules` API and CLI; the simulation job, `rule_simulation`, `…/simulate` and `GET /v1/monitoring/simulations/{id}` (approval needs them) and the simulation panel; hits, dedup, cooldown, evidence, hits API and page; capture definitions from the policy, trigger and field maps, reportable events, groups, supersession, period close, captures API and page; staff-only event registration | 4a | the evaluator, definitions, library and loader can start now in parallel with 4a (pure code); the rest after 4a |
| **4c — Counterparty screening, decide, replay, profiles, exit** | counterparty subjects and runs, first-sight and delta screening, periodic walk, wallet matching, `screening.screen_counterparties`, Contrapartes page (§11.2 to §11.4; `COUNTERPARTY_TRUE_MATCH` and `BLOCKED_PERSON_PARTY` only fire once this lands; `screening.counterparty_status_changed.v1` is registered staff-only here); migration 0013; rail settings and the decide API with fail modes; `txn_decision` and linkage; the decide-path tier limit and its webhook; `monitoring replay --dry-run`; metrics endpoint; retention job; behaviour profiles and `declared_profile` (with `PATCH /v1/relationships/{id}` in the party module); Admin › Rieles, the Perfil tab; `perf` records; `tests/e2e/test_sp04_exit.py` | 4a, 4b; SP-03's engine (`screen()`, index holder, blocking query) and the library's optional-names change (§11.4) | counterparty screening after 4a, in parallel with 4b; the rest after 4b |

Estimate: 4a 1.5 weeks, 4b 2 weeks, 4c 1 week, which fits the program's 4.5 weeks with the library files and the evaluator written in parallel with 4a and counterparty screening in parallel with 4b.

## 23. Cut list (in order, if SP-04 runs long)

Behaviour profiles and the Perfil tab (`FR-TM-013`; SP-07 can compute them from the same tables when it needs them); XLSX file ingest (CSV stays); the Contrapartes page (the API and the screening queue stay); the simulation panel in the console (the API and CLI stay; program spec §7.4); the metrics endpoint (SP-10 would compute it); `BORDER_ZONE_CASH`, `ROUND_AMOUNT_FX_LAYERING`, `DORMANT_REACTIVATION` and `CRYPTO_OFF_RAMP` from the initial library (the files are cheap to add later; `CRYPTO_OFF_RAMP` also waits for an in-window ordering predicate if the design partner needs one); the `perf` records (SP-11 measures); the opt-in `monitoring.transaction_processed.v1` webhook.
