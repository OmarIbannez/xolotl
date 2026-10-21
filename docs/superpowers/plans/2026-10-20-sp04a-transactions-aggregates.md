# SP-04a — Transactions, enrichment and aggregates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every task also follows `docs/superpowers/execution/sp04/agreement.md` (gates, worktrees, commits, PRs, report); the cross-plan decisions the three SP-04 plans carry are in `docs/superpowers/plans/2026-10-20-sp04-reconciliation.md`.

**Goal:** Take a tenant's transactions in (a 1,000-item API batch or a CSV, JSON Lines or XLSX file), keep each exactly once by `external_id`, resolve it to a party, a relationship, an account and a counterparty, enrich it with every threshold unit of the effective policy, its period keys and its policy snapshot, maintain the declared aggregates per key as it is processed in `occurred_at` order, park what cannot be valued yet and sweep it back, hold late rows for a backfill, and show all of it to staff in the Transacciones pages; without a single rule, hit or capture (those are plans 4b and 4c).

**Architecture:** A new module `monitoring` (spec §2 layout) with four packages this plan builds: `txn/` (the canonical schema, models, batch and file ingest, parking rows), `enrich/` (policy snapshot, unit amounts, period keys, device hash), `aggregates/` (the `AggregateStore` port with a Postgres and a memory adapter, declarations, windows, backfill) and `process/` (the per-party `monitoring.process` job that drains a key in order, one database transaction per row, the sweep, the backfill job, partition maintenance). Counterparties are screening's (spec Decision 18): `screening/counterparty/` holds the `counterparty` table, its schema types, the blind-index resolution and the status read; `monitoring` calls those functions and keeps only `transaction.counterparty_id`. The `transaction` table is monthly-partitioned by `occurred_at` through a generalised `xolotl.platform.partitions` that `audit_event` keeps using. The processing-hook registry of the job is empty in this plan: B7 registers `RulesHook` and B10 `CapturesHook` into it.

**Tech Stack:** The SP-01 to SP-03 stack. No new runtime dependency: `openpyxl` (read-only XLSX, SP-03a) and `pyyaml` are present. Migration 0012 (one, first task after the platform change). No new console dependency.

**Spec:** `docs/superpowers/specs/2026-10-20-sp04-monitoring-captures-design.md` §1 (scope) and §1.1 (first row), §2 (layout, contracts, partitions, migrations), §3 (the twelve `MONITORING_*` keys this plan owns), §4 (the canonical transaction), §5.2 first block (the 4a tables), §6 (ingest), §7 (enrichment), §8.1 to §8.5 (aggregates, declarations, distinct counts, backfill, the processing job and the sweep), §11.1 (counterparty resolution and the status cache), §14 (the four 4a events), §15 (the transactions rows, `POST /v1/monitoring/backfill`, `GET /v1/monitoring/status`), §16 (`monitoring ingest-file`, `process`, `sweep`, `backfill`, `partitions ensure`, `show`), §17 (Transacciones and the Cliente › Transacciones tab), §19 (the `txn`, `enrich`, `aggregates`, `process`, platform, isolation and console rows), §20 Decisions 3, 4, 5, 6, 9, 15, 18, 19, 20, 21, §21 Q6, Q7, Q9 defaults, §22 (the 4a row). Program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md` D5, D6, D11, D12, D20, §4 ports `AggregateStore`, `ObjectStore`, `EventBus`, `JobQueue`, `UnitValueSource`, `Clock`, `Notifier`.

## Accepted by the product owner

The SP-04 spec is "draft 2026-10-20 for the product owner's review": nothing in it is accepted yet. This plan binds itself to the spec's Decisions and §21 defaults as written and names, in "Deviations" below, every point where it departs; the reviewer who accepts the spec accepts or amends those in the same pass. The defaults this plan builds: Q6 a transaction is immutable (no amend or cancel; `raw.corrects` and `raw.reverses` are client extras the schema keeps in `raw`), Q7 the fiat `amount` of a crypto operation is what thresholds read (`crypto.asset_qty` is kept as sent), Q9 tenants send every transaction (no floor).

## Dependencies outside this plan

| Id | What | Provided by | Needed by |
|---|---|---|---|
| SP-03 library T4 | `xolotl.modules.screening.matching.normalize.normalize_name(text, *, entity_type, given=None, surname=None) -> NormalizedName` (`.folded`) and `xolotl.modules.screening.lists.types.EntityType` (on `main`) | merged | A4 (the name component of a counterparty's blind index) |
| 4b, 4c | nothing: no 4a task depends on 4b or 4c (B4, C1, C3, C4, C5, C7, C8 append to files this plan creates; the ownership table is reconciliation finding 17) | — | — |

Everything else this plan consumes is on `main` at `0c68cb7` (migrations 0001 to `0011_screening_hit_run_index`).

## Interfaces this plan exposes to plans 4b and 4c

Named as the spec names them; the reconciliation matches on these.

- **Migration** `0012_transactions` (`down_revision = "0011_screening_hit_run_index"`); 4b's `0013_rules_captures` (B4) and 4c's `0014_decide_profiles` (C4) follow it, in that order (the spec's 0011, 0012, 0013; see Global Constraints). Tables exactly as spec §5.2's first block: `account`, `counterparty` (owned by `screening/counterparty/`), `transaction_batch`, `transaction_file`, `transaction` (partitioned, `transaction_default` plus monthly partitions), `transaction_ref`, `transaction_parked`, `agg_bucket`, `agg_distinct`, `agg_declaration`. Deviations 2 and 3 are the only schema differences. `counterparty.screening_subject_id` is a plain nullable `uuid`; B4's 0013 adds `fk_counterparty_subject`, `counterparty_name_index` and extends `ck_screening_subject_kind_input`.
- **Platform** `xolotl.platform.partitions`: `PartitionedTable(table, column, truncate_guard)`, `register_partitioned_table`, `partitioned_tables`, `partition_name(table, month)`, `is_partition_name(name)`, `next_month`, `harden_partition(conn, name, *, truncate_guard=None)`, `ensure_partitions(conn, *, table, months_ahead=3, start=None) -> PartitionReport`, `ensure_all_partitions(conn, *, months_ahead=3) -> dict[str, PartitionReport]`. C4's 0014 creates `txn_decision` with it and registers `PartitionedTable("txn_decision", "decided_at", None)` in `monitoring/decide/models.py`; `monitoring.partitions` then covers it with no 4c change (A9). C7 adds `retire_partition(conn, *, table, month)` to this module.
- **Models** `xolotl.modules.monitoring.txn.models`: `Account`, `TransactionBatch`, `TransactionFile`, `Transaction`, `TransactionRef`, `TransactionParked`; `xolotl.modules.monitoring.aggregates.models`: `AggBucket`, `AggDistinct`, `AggDeclaration`; `xolotl.modules.screening.counterparty.models`: `Counterparty`.
- **Enums** `xolotl.modules.monitoring.enums`: `TransactionStatus {RECEIVED, PROCESSED, PARKED, BACKFILL}`, `BatchSource {API, FILE, CLI}`, `FileStatus {UPLOADED, PROCESSING, COMPLETED, FAILED}`, `AccountKind {DEFAULT, EMONEY, WALLET, CARD, OTHER}`, `AccountStatus {ACTIVE, CLOSED}`, `ParkReason {UNIT_VALUE_MISSING, POLICY_NOT_ASSIGNED}`, `BucketKind {HOUR, DAY, MONTH, QUARTER, ACCUM}`. Pure vocabulary in `xolotl.modules.monitoring.txn.schema`: `Direction`, `Rail`, `Instrument`, `Channel`, `KeyKind {PARTY, RELATIONSHIP, ACCOUNT, COUNTERPARTY, DEVICE}`; in `xolotl.modules.screening.counterparty.schema`: `CounterpartyKind`, `CounterpartyScreeningStatus {UNSCREENED, CLEAR, PENDING_REVIEW, TRUE_MATCH}`.
- **Canonical types** (`monitoring.txn.schema`, pure): `TransactionInput`, `BatchRequest`, `FieldError`, `FieldErrorCode`, `CryptoContext`, `FxContext`, `DeviceContext`, `UnitAmount`, `PeriodKeys`, `EnrichedTransaction` (the evaluator's `txn`; `key_for(kind) -> str | None`), `content_hash(item) -> bytes`; (`screening.counterparty.schema`): `CounterpartyInput`, `CounterpartyIdentifier`.
- **Aggregates** (`monitoring.aggregates.declarations`, pure): `AggregateFunction`, `WindowKind`, `Measure`, `TxnFilter`, `AggregateDeclaration` (`agg_id()`, `canonical_json()`, `from_json()`, the extra field `lists_hash: str | None` of Deviation 8), `matches(where, txn) -> bool` (the one filter function; B2's `applies` and B10's triggers call it; a `$NAME` list name never reaches it: B6 resolves names before a filter is used, reconciliation finding 3); (`aggregates.store`, pure): `AggregateKey(kind, key)`, `BucketRow`, `AggregateValue` (`complete: bool`; `INCOMPLETE` is `complete is False`), `AggregateStore` Protocol; (`aggregates.memory`, pure): `MemoryAggregateStore` (`seed(key, declaration, buckets)`, which C5 fills from `PostgresAggregateStore.buckets` before `add()`-ing the candidate); (`aggregates.postgres`): `PostgresAggregateStore(session)` (`buckets(key, declaration, *, start, end)`), `load_declarations(session)`, `ensure_declarations(session, declarations, *, queue) -> list[str]` (B6 calls it at activation; A8's job calls it for the declarations a hook reports that `agg_declaration` lacks). There is no `aggregate_key` helper: a key is `AggregateKey(kind, txn.key_for(kind))`.
- **Processing hook** (`monitoring.process.hooks`): `ProcessingContext(txn, row, store, declarations, snapshot: SnapshotRef, units: UnitService, holidays: Holidays, late: bool, queue: PostgresJobQueue)`; `ProcessingHook` (a Protocol: `name: str`, `declarations(self, s, snapshot: SnapshotRef, *, on: date, product: str | None) -> Sequence[AggregateDeclaration]`, `evaluate(self, s, txn: EnrichedTransaction, ctx: ProcessingContext) -> None`); `register_processing_hook(hook)`, `processing_hooks()`, `reset_processing_hooks()`. 4b registers `RulesHook` (B7) and `CapturesHook` (B10) in its `register_rules`; the job asks every hook for its declarations once per snapshot it meets in a drain (`ensure_declarations` for the ones `agg_declaration` lacks) and calls every hook's `evaluate` in the row's transaction after `store.add(...)`, in registration order, so whatever they insert commits with the row.
- **Enrichment** (`monitoring.enrich.service`): `enrich(session, row, *, units, holidays, relationship, snapshot) -> EnrichedTransaction`, `enrich_input(session, item, *, relationship, account_id, units, holidays, snapshot, now) -> EnrichedTransaction` (the same enrichment over a validated input with no row; C5's decide path), `enriched_from_row(row) -> EnrichedTransaction` (B8's simulation, B7's late re-evaluation and B11's period close rebuild `PROCESSED` rows with it), `ParkTransactionError`; (`enrich.policy`): `SnapshotRef(snapshot_id, policy)`, `snapshot_for(session, *, on, product, cache) -> SnapshotRef`; (`enrich.units`): `unit_pairs(policy) -> frozenset[tuple[str, ConversionRule]]`, `unit_key(unit, rule) -> str`, `unit_amounts_for(units, *, amount, currency, on, pairs, holidays)` (the one conversion path, Deviation 6; B10 converts a capture's fee through it).
- **Ingest** (`monitoring.txn.ingest`): `ingest_batch(session, items, *, source, submitted_by, queue, file_id=None, request_id=None) -> BatchReport`; C5 inserts its decision-linking step there (Deviation 4: `link_decisions(session, rows)` from `monitoring.decide.linkage`); (`monitoring.txn.files`): `upload_file`, `IngestFileJob`.
- **Jobs and keys** (`monitoring.kinds`): `PROCESS_KIND = "monitoring.process"` (key `process:<party_id>`, payload `{"party_id"}`), `INGEST_FILE_KIND = "monitoring.ingest_file"` (key `ingest_file:<file_id>`), `SWEEP_KIND = "monitoring.sweep"`, `AGG_BACKFILL_KIND = "monitoring.agg_backfill"` (key `agg_backfill:<agg_id>`), `BACKFILL_KIND = "monitoring.backfill"` (key `backfill:<request id>`), `PARTITIONS_KIND = "monitoring.partitions"`; `process_key(party_id)`; (`monitoring.process.enqueue`): `enqueue_processing(session, queue, party_ids) -> int`.
- **Events** (`monitoring.events`, source `xolotl.monitoring`, ids only): `TRANSACTION_BATCH_ACCEPTED = "monitoring.transaction_batch_accepted.v1"`, `TRANSACTION_FILE_COMPLETED`, `TRANSACTION_PROCESSED`, `TRANSACTION_PARKED`; `STAFF_ONLY_EVENT_TYPES: tuple[str, ...]` (C5 appends `DECISION_ISSUED` and C8 `BEHAVIOUR_PROFILE_UPDATED` to it; 4b's eight staff-only types are B4's own `RULES_STAFF_ONLY_EVENT_TYPES`, registered by B7's `register_rules`); `publish_monitoring_event(s, event_type, *, entity, entity_id, key: str, data, policy_snapshot_id=None) -> Event` (the signature of `publish_screening_event`).
- **Composition** (`monitoring.composition`): `register_monitoring(*, session_factory, settings, queue, store, notifier) -> None`, called by `wire_platform` after `register_rescreen` and before 4b's `register_rules(queue=queue)` (B7); C5, C7 and C8 append their job handlers to it.
- **Errors** (`monitoring.errors`): `BatchInvalidError` (422 `TRANSACTION_BATCH_INVALID`, `errors`), `BatchTooLargeError` (413 `TRANSACTION_BATCH_TOO_LARGE`), `ExternalIdConflictError` (409 `EXTERNAL_ID_CONFLICT`), `FileTooLargeError` (413 `TRANSACTION_FILE_TOO_LARGE`), `FileInvalidError` (422 `TRANSACTION_FILE_INVALID`), `BackfillRangeInvalidError` (422 `BACKFILL_RANGE_INVALID`).
- **Routes** `monitoring/routes_transactions.py` (A10) and `monitoring/routes_monitoring.py` (A10 creates it with `/v1/monitoring/backfill` and `/v1/monitoring/status`; C6 appends `GET /metrics` and C8 `GET /profiles/{relationship_id}`; 4b mounts its own `routes_rules.py`, `routes_hits.py` and `routes_captures.py`; C4 includes `routes_decide.router` before `routes_transactions.router` so `/transactions/decide` wins over `/transactions/{transaction_id}`). API-key scopes `transactions:read`, `transactions:write`.
- **Party additions** (`xolotl.modules.party.service`): `RelationshipRef(id, party_id, product, account_tier, status)`, `relationship_by_external_ref(s, refs) -> dict[str, RelationshipRef]`, `relationship_ref(s, relationship_id) -> RelationshipRef`.
- **Counterparty** (`xolotl.modules.screening.counterparty.resolve`): `CounterpartyRef(id, dedup_hash, country, screening_status, created)`, `resolve(session, input) -> CounterpartyRef`, `dedup_hash_of(session, input) -> bytes`, the component helpers `name_hash_of(session, name, *, entity_type)`, `account_hash_of(session, account)`, `wallet_hash_of(session, address)`, `identifier_hash_of(session, id_type, value)` (each `-> bytes`; C2's reverse lookup uses the last two), `status_by_dedup_hash(session, dedup_hash) -> CounterpartyScreeningStatus`, `status_of(session, counterparty_id) -> tuple[bytes, str | None, CounterpartyScreeningStatus]` (the dedup hash, country and screening status, read through the identity map) and `country_and_status_by_dedup_hash(session, dedup_hash) -> tuple[str | None, CounterpartyScreeningStatus] | None` (A6); (`counterparty.schemas`, A10): `CounterpartySummary`, `RevealedCounterparty`; (`counterparty.views`, A10): `counterparty_summary(session, counterparty_id) -> CounterpartySummary` (masked, never decrypts), `reveal_counterparty(session, counterparty_id) -> RevealedCounterparty` (one `VIEW_PII` row per non-null field, recorded before any decryption). C1 makes `resolve` call `on_counterparty_created(session, ref.id)` on its `created=True` branch (the name index and the first-sight enqueue) and C1's status sync writes `screening_status` and `screened_at`; C3 appends `CounterpartyResponse` and `CounterpartyDetail` to `counterparty/schemas.py` and reuses `reveal_counterparty`.
- **Test support** `tests/modules/monitoring/support.py`: `itf_tenant`, `both_packs_tenant` (packs, holidays and unit values seeded), `make_item(**overrides) -> dict`, `ingest_items(session, items, *, queue) -> BatchReport`, `run_processing(services, tenant_id) -> int`, `declare(session, declaration, *, queue)`, `stream(n, *, seed)`; 4b and 4c use these names (B7 loads its own December 2026 unit rows with `tests/platform/units_support.py::load_unit_values`); isolation factories `_customer_relationship`, `_account`, `_counterparty`, `_transaction_batch`, `_transaction` (module-level helpers B4's and C4's factories call).
- **Console** `console/src/features/transactions/` (`queries.ts`, pages, `PartyTransactionsCard`), `ACCESS.transactions`, `ACCESS.backfill`, nav key `nav.transactions` (after `nav.parties`; the whole SP-04 nav order is reconciliation finding 16), catalogue namespace `transactions.*`. B14 adds a hits card and B15 a captures card to `TransactionPage.tsx`; C9 gates its Perfil card with `ACCESS.transactions`.

## Global Constraints

- **Migration renumbering.** `main` now ends at `0011_screening_hit_run_index` (merged 2026-10-20 after the spec was written), so SP-04's migrations are `0012_transactions` (this plan, A2), `0013_rules_captures` (4b) and `0014_decide_profiles` (4c): the spec's 0011, 0012 and 0013 respectively. Every "0011" of spec §2, §5.2 and Decision 18 reads 0012 here, every "0012" reads 0013, every "0013" reads 0014. 4b and 4c carry the same mapping (B4 is `0013_rules_captures`, C4 is `0014_decide_profiles`); the three migrations merge in the order A2 → B4 → C4, each appending its revision to `CHAIN`.
- Every SP-01 to SP-03 convention binds: tenant tables with `tenant_id` first in the primary key, composite FKs through `tenant_fk`, `enable_rls`, one isolation factory per table in `tests/isolation/factories.py::register_all`; `v1_router()` and `require_access(...)` on every route; `Idempotency-Key` on every POST; `Encrypted*` columns without an `_enc` suffix with blind-index columns beside them; `record(session, AuditEvent(...))` last in the transaction; ids-only, float-free event payloads through `publish` (amounts as minor-unit integers or decimal strings); problem details with stable codes; UPPERCASE values in `enum_column` (VARCHAR(32)) columns; Google-style docstrings; golden tests that print a diff; the `perf` marker only for non-gating records (none in this plan).
- MVP footprint (ADR-022): one app container plus Postgres; the local-filesystem object store; no Kafka, Redis, S3, Temporal or KMS. Nothing in this plan builds a topic, a cache or a lake.
- Settings (`src/xolotl/platform/config.py`, prefix `XOLOTL_`), exactly these twelve (the other `MONITORING_*` keys of spec §3 are 4b's and 4c's): `monitoring_batch_max_items: int = Field(default=1000, ge=1, le=10000)`, `monitoring_file_max_mb: int = Field(default=50, ge=1, le=1024)`, `monitoring_file_max_rows: int = Field(default=500000, ge=1)`, `monitoring_file_chunk_rows: int = Field(default=1000, ge=1)`, `monitoring_late_days: int = Field(default=7, ge=1)`, `monitoring_max_future_minutes: int = Field(default=10, ge=0)`, `monitoring_process_lease_seconds: int = Field(default=300, ge=30)`, `monitoring_process_page: int = Field(default=200, ge=1)`, `monitoring_sweep_cron: str = "* * * * *"`, `monitoring_agg_backfill_batch: int = Field(default=5000, ge=1)`, `monitoring_partitions_cron: str = "0 1 * * *"`, `monitoring_partition_months_ahead: int = Field(default=3, ge=1, le=12)`. The two crons join the existing cron validator list.
- Module boundaries (`.importlinter`): `monitoring` never imports `xolotl.modules.reporting`; `screening` never imports `monitoring`; `monitoring` reads party and counterparty data through service functions, never `party.models` or `screening.counterparty.models` (the engine contract's shape); the pure core (`monitoring.txn.schema`, `monitoring.aggregates.declarations`, `monitoring.aggregates.windows`, `monitoring.aggregates.store`, `monitoring.aggregates.memory`, `screening.counterparty.schema`; 4b's pure modules have their own contract `monitoring-evaluator-is-pure`, B1) imports no `sqlalchemy`, `fastapi`, `xolotl.platform` (except `platform.errors`, `platform.enums`, `platform.units.rules`, `platform.policy.durations`) and no other module except `party.schemas` and `screening.counterparty.schema`.
- Keys and hashes (all HMAC-SHA256 under the tenant INDEX key through `blind_index` or `get_key_provider().index_key`): counterparty `name_hash = blind_index("name", normalize_name(name, entity_type=…).folded)`, `account_hash = blind_index("account", account)`, `identifier_hash = blind_index(id_type.lower(), value)`, `wallet_hash = hmac(index_key, b"wallet:" + address.encode())` (case kept: `blind_index` would fold it), `dedup_hash = hmac(index_key, b"counterparty:" + name_hash + b"|" + account_hash + b"|" + wallet_hash + b"|" + identifier_hash)` with `b""` for an absent component; `transaction.device_hash = blind_index("device", device_id)`; `agg_distinct.member_hash = blind_index("agg_member", member)`; `agg_bucket.key` is `str(uuid)` for PARTY, RELATIONSHIP and ACCOUNT, `dedup_hash.hex()` for COUNTERPARTY and `device_hash.hex()` for DEVICE.
- Time: `occurred_at` and `received_at` are aware UTC; `period_keys` (`DAY`, `MONTH`, `QUARTER`, `BUSINESS_DAY` under `HolidayScope.BANXICO`) and the policy date are computed in `MEXICO_CITY` (`xolotl.platform.calendar`); `HOUR` and `DAY` buckets start at UTC boundaries, `MONTH` and `QUARTER` buckets at Mexico City boundaries (spec §5.2 `agg_bucket.bucket_start`); the partition bounds are UTC midnights, as `audit_event`'s.
- Statuses: `BACKFILL` is set at ingest when `received_at − occurred_at > monitoring_late_days` days (spec §8.5 step 3 "at receipt"), and such a row is neither enqueued nor counted as a duplicate; `late = true` when a processed row's `occurred_at` is before the party's latest processed `occurred_at` or `received_at − occurred_at > P1D`; a `PARKED` row stops its party's drain at that row; a `PARKED` row is retried only after the sweep set it `RECEIVED` again.
- Object keys: uploads `transactions/files/<file_id>` (sealed with the tenant DATA key, content type `application/octet-stream`, metadata `content_type` the upload's); error reports `transactions/files/<file_id>.errors.csv` (plain `text/csv`, columns `row,column,code`; never a cell value), beside the upload rather than under it because the local object store refuses a key that is also a folder; readers find the report through `transaction_file.errors_key`, never by building the key.
- The machine view (an API key) of a transaction is `{id, external_id, occurred_at, status, late}` and the file and batch counters; it never carries a counterparty's screening status, a unit amount, a snapshot or anything 4b and 4c add (D20, tipping-off). `raw`, the counterparty's name, account, wallet and identifier are shown to staff only through a `VIEW_PII` reveal.
- No customer-facing string mentions a rule, a hit, a capture or a suspicion; console strings are Spanish-first keyed strings in both catalogues.
- Gates at the end of every task: `make ci`; console tasks also `cd console && npm ci && npm run typecheck && npm run lint && npm test && npm run build`.

## Deviations from the sub-spec (decided here)

1. **Migration numbers are 0012, 0013, 0014** (Global Constraints, first bullet): the spec predates `0011_screening_hit_run_index`.
2. **Encrypted columns drop the `_enc` suffix** (the kernel convention, as SP-03a Deviation 3): `counterparty.name`, `account`, `wallet`, `identifier` are `EncryptedStr`; `transaction.raw` is `EncryptedJson`. The hash columns keep the spec's names (`name_hash`, `account_hash`, `wallet_hash`, `identifier_hash`).
3. **`transaction`'s ORM model is declared like `AuditEventRow`**, with its three primary-key columns `(tenant_id, occurred_at, id)` explicit and no `TenantTableMixin`; `transaction`, `transaction_ref`, `transaction_parked`, `agg_bucket`, `agg_distinct` and `agg_declaration` join `NATURALLY_KEYED` in `tests/platform/test_schema_sync.py` (a partitioned table cannot carry `UNIQUE (tenant_id, id)`; the others have no `id`).
4. **Decision linking is 4c's step.** Spec §6.1 step 3 reads `txn_decision`, a 0014 table; `ingest_batch` here has steps 1, 2, 4 and 5, and C5 inserts `link_decisions(session, rows)` (`monitoring.decide.linkage`; `rows` are the `(id, occurred_at, external_id)` triples of the rows this batch created, duplicates excluded) between 2 and 4, in the same transaction.
5. **The file upload is a raw request body typed by `Content-Type`** (as SP-03a Deviation 4 and `POST /v1/parties/{id}/documents`): `text/csv`, `application/x-ndjson` (JSON Lines) or `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. The spec says multipart; `python-multipart` is not a dependency and spec §2 adds none.
6. **Only `MXN` and `USD` are accepted as `currency`.** `UnitService.to_unit` on `main` converts from `MXN` only (`_check_currency`), and the catalogue knows `USD` through `USD_MXN_FIX`. A `USD` transaction is first brought to pesos with `threshold_in_ccy(Quantity(amount, "USD"), "MXN", on, PRIOR_BANKING_DAY)` (the only rule with a value on every calendar day for a banking-day series), recorded as `unit_amounts["MXN/PRIOR_BANKING_DAY"]` with the FIX row's id, and every other unit is converted from that peso amount; a `MXN` transaction records `unit_amounts["MXN/SAME_DAY"]` with no `unit_value_id` as spec §7 says. Any other currency is the field error `INVALID` on `currency`. The spec's "every transaction whose currency differs from MXN under `SAME_DAY`" would park every weekend USD transaction forever (no FIX row exists for a Saturday). Reported to the product owner (Open question 1).
7. **`monitoring.transaction_processed.v1` is registered staff-only in this plan.** The spec makes it tenant-visible but "opt-in, off by default per endpoint", a per-endpoint switch the platform's webhooks do not have and whose delivery is last (eighth) on the cut list (§23). Registering it staff-only is "off by default" with no new mechanism; the event is still published for in-process consumers. Open question 2. C5 registers `monitoring.decision_issued.v1` the same way: no `register_opt_in` exists in SP-04 (reconciliation finding 7).
8. **`AggregateDeclaration` carries `lists_hash: str | None = None`** in its canonical JSON, so 4b can make a changed `_jurisdictions.yaml` yield a new `agg_id` (spec §8.2) without redefining the hash; this plan never sets it; B6 sets it (`lists.content_hash.hex()`) on the declarations of a loaded rule set after resolving list names (reconciliation finding 3).
9. **The processing job reads its declarations from `agg_declaration` plus each registered hook's `declarations(...)`**, which it ensures the first time a snapshot is met in a drain (reconciliation finding 4): spec §8.2 says the job loads "the union … (`agg_declaration`)". `ensure_declarations` stays the only writer of `agg_declaration`; with no hook registered and no 4b code the table is empty and `add()` updates nothing. Tests declare through `support.declare`.
10. **The sweep's "at once when a pack assignment is created" is a query, not an event subscription:** a `POLICY_NOT_ASSIGNED` row is retried when `parked_at` is older than one hour or a `tenant_pack_assignment.created_at` is later than `parked_at`. Re-parking a row sets `parked_at = now` and `attempts + 1`.
11. **"Once a day" for the `monitoring.parked` notification** is "no notification with that template for this tenant since 00:00 Mexico City today" (SP-03a Deviation 12).
12. **File row errors accumulate in the report object itself.** Each page rewrites `transactions/files/<id>.errors.csv` (previous bytes plus the page's rows; the key sits beside the upload's because the local store refuses key-as-folder, and readers use `transaction_file.errors_key`); no table is added. Unknown CSV and XLSX columns go into `raw` under the column name (spec §4: `raw` keeps whatever else the client sent); nested fields are dotted headers (`counterparty.name`, `crypto.network`, `fx.rate`, `device.ip`, `raw.<key>`).
13. **`CounterpartyIdentifier` is a copy of `AdHocIdentifier`'s shape** (`id_type: IdentifierType`, `value`, `country`), defined in the pure `screening/counterparty/schema.py`, because `screening.engine.queries` (where `AdHocIdentifier` lives) is not importable from the pure core.
14. **`ensure_partitions` is called for every registered table by `migrate.upgrade()`** through `ensure_all_partitions`; the registry (`register_partitioned_table`) is filled at import by `platform.audit.partitions` and `monitoring.txn.models`, both imported before any CLI command migrates (`xolotl.cli` imports `xolotl.app`).
15. **Flat navigation:** `nav.transactions` is one entry after `nav.parties` (SP-03a Deviation 11).
16. **The console's "Cliente › Transacciones" tab is a card** (`PartyTransactionsCard`) on the existing party page, which has cards rather than tabs; 4b and 4c add their cards beside it.
17. **A counterparty account is blind-indexed as sent, stripped of whitespace.** Spec §11.1 normalizes it through `normalize_identifier("CLABE" | "ACCOUNT", …)`, but `main`'s `normalize_identifier(value)` takes no type argument and knows no account types; `account_hash = blind_index("account", account.strip())` until the party module gains account normalization (reconciliation finding 22).

## Review Focus

1. **Two batches re-sending one `external_id` at the same moment must leave one row and one ref.** `transaction_ref` is the key (D11); the insert order (ref first, `ON CONFLICT DO NOTHING`, then the row) and the hash comparison decide duplicate versus `EXTERNAL_ID_CONFLICT`. Test `test_two_concurrent_batches_with_one_external_id_keep_one_row` (A4, two threads).
2. **A crash between `add()` and `PROCESSED` must leave the aggregates untouched.** The row's transaction holds both; a retry must not add the transaction twice. Test `test_a_failure_after_add_rolls_the_buckets_back_with_the_row` (A8, a hook that raises).
3. **An un-parked row keeps its place.** A `PARKED` row with later `RECEIVED` rows behind it must process first when the unit value arrives, and the key's aggregates must then equal a stream that never parked. Test `test_an_unparked_row_processes_before_the_rows_behind_it` (A9).
4. **A transaction dated before the oldest partition still lands, isolated.** A 2019 `occurred_at` goes to `transaction_default`, which carries forced RLS and no grant to the app role. Test `test_a_row_outside_every_monthly_partition_lands_in_the_default_partition_under_rls` (A2).
5. **A rejected file row never echoes a cell, and a file with a BOM, semicolons or quoted commas reads the same.** Tests `test_row_errors_never_echo_cell_values` and `test_bom_semicolons_and_quoted_commas_are_read` (A5).

## File structure

```
src/xolotl/platform/partitions.py                            the generic partition helper and registry                        [A1]
src/xolotl/platform/audit/partitions.py                      thin wrappers over it; registers audit_event                       [A1]
src/xolotl/platform/migrate.py                               (upgrade: ensure_all_partitions)                                  [A1]
src/xolotl/migrations/env.py, tests/platform/test_schema_sync.py   (is_partition_name)                                         [A1]
src/xolotl/platform/crypto/blobs.py                          seal_blob, open_blob                                              [A5]
src/xolotl/platform/config.py, .env.example, README.md       (+ twelve MONITORING_* settings, a "Monitoring" section)          [A2, A9]
src/xolotl/migrations/versions/0012_transactions.py          ten tables, the default partition, RLS                             [A2]
src/xolotl/models.py                                         (+ monitoring txn, aggregates and counterparty models)             [A2]
.importlinter                                                (+ monitoring-core-is-pure, monitoring-boundaries, screening-never-imports-monitoring)   [A3, A2, A7]
src/xolotl/modules/monitoring/__init__.py, enums.py, kinds.py, errors.py, events.py, schemas.py, composition.py,
    routes_transactions.py, routes_monitoring.py             [A2, A4, A5, A8, A9, A10]
src/xolotl/modules/monitoring/txn/__init__.py, schema.py     canonical schema, validation, content hash, EnrichedTransaction   [A3]
src/xolotl/modules/monitoring/txn/models.py                  the six txn models                                                [A2]
src/xolotl/modules/monitoring/txn/accounts.py, ingest.py     accounts, batch ingest                                            [A4]
src/xolotl/modules/monitoring/txn/files.py                   readers, upload, the ingest_file job, the error report            [A5]
src/xolotl/modules/monitoring/txn/queries.py, views.py       list filters, client/staff/revealed views                         [A10]
src/xolotl/modules/monitoring/enrich/__init__.py, policy.py, units.py, periods.py, service.py                                  [A6]
src/xolotl/modules/monitoring/aggregates/__init__.py, models.py                                                                 [A2]
src/xolotl/modules/monitoring/aggregates/declarations.py, windows.py, store.py, memory.py, postgres.py                         [A7]
src/xolotl/modules/monitoring/aggregates/backfill.py         the agg_backfill job                                              [A9]
src/xolotl/modules/monitoring/process/__init__.py, enqueue.py                                                                  [A4]
src/xolotl/modules/monitoring/process/hooks.py, job.py        the processing-hook registry, the process job                     [A8]
src/xolotl/modules/monitoring/process/sweep.py, backfill.py, partitions.py, status.py                                          [A9, A10]
src/xolotl/modules/screening/counterparty/__init__.py, schema.py   CounterpartyKind, CounterpartyScreeningStatus, CounterpartyIdentifier, CounterpartyInput   [A3]
src/xolotl/modules/screening/counterparty/models.py          Counterparty                                                      [A2]
src/xolotl/modules/screening/counterparty/resolve.py, schemas.py, views.py   resolution, dedup hash, status read; summary and revealed types, masked and revealed views [A4, A10]
src/xolotl/modules/party/service.py                          (+ RelationshipRef, relationship_by_external_ref, relationship_ref) [A4]
src/xolotl/modules/tenants/service.py                        (+ monitoring.sweep and monitoring.partitions schedules)           [A9]
src/xolotl/app.py                                            (register_monitoring in wire_platform; two routers)               [A5, A10]
src/xolotl/cli.py                                            (+ monitoring_app: ingest-file, process, sweep, backfill, partitions ensure, show)   [A5, A8, A9, A10]
tests/platform/test_partitions.py                                                                                              [A1]
tests/isolation/factories.py, tests/platform/test_migration_chain.py, test_config.py, test_errors.py, test_worker.py, tests/modules/tenants/test_kernel_events.py   [A2, A4, A5, A9]
tests/modules/monitoring/__init__.py, support.py, conftest.py                                                                  [A2, A4]
tests/modules/monitoring/txn/test_migration_0012.py, test_schema.py, test_accounts.py, test_ingest.py, test_files.py, test_routes_transactions.py   [A2..A5, A10]
tests/modules/monitoring/txn/fixtures/*.csv, *.jsonl, *.xlsx  (synthetic)                                                      [A5]
tests/modules/monitoring/enrich/test_enrich.py, test_golden.py; tests/golden/monitoring/enrichment/cases.yaml                [A6]
tests/modules/monitoring/aggregates/test_declarations.py, test_windows.py, test_memory.py, test_postgres.py, test_contract.py, test_backfill.py   [A7, A9]
tests/modules/monitoring/process/test_job.py, test_sweep.py, test_backfill.py, test_partitions.py, test_status.py             [A8, A9, A10]
tests/modules/monitoring/test_routes_monitoring.py                                                                            [A10]
tests/modules/screening/counterparty/__init__.py, test_schema.py, test_resolve.py, test_views.py                              [A3, A4, A10]
tests/modules/party/test_service.py (appended)                                                                                [A4]
tests/test_cli.py (appended)                                                                                                  [A5, A8, A9, A10]
console/src/api/schema.d.ts (regenerated), api/types.ts, auth/access.ts (+ tests), components/layout/nav.ts, App.tsx, i18n/es-MX.json, en.json, catalogue.test.ts   [A11, A12]
console/src/features/transactions/…                                                                                           [A11, A12]
console/src/features/parties/PartyPage.tsx                   (+ PartyTransactionsCard)                                         [A12]
console/public/plantilla-transacciones.csv                                                                                    [A12]
```

---

### Task A1: `xolotl.platform.partitions`: the generic monthly-partition helper, the registry, `audit_event` as its first user

Depends on: nothing. Console: no. Branch `sp04a-a1-platform-partitions`.

**Files:**
- Create: `src/xolotl/platform/partitions.py`
- Modify: `src/xolotl/platform/audit/partitions.py`, `src/xolotl/platform/migrate.py`, `src/xolotl/migrations/env.py`, `tests/platform/test_schema_sync.py` (`_include_name`)
- Test: `tests/platform/test_partitions.py`; `tests/platform/test_audit.py`, `tests/platform/test_ddl.py`, `tests/isolation/test_structural.py` pass unchanged

**Interfaces:**

Consumes: `rls_statements(name, grant=None)`, `clock.now`, the advisory-lock, savepoint and `_SKIPPABLE` logic of today's `audit/partitions.py`.

Produces:

```python
# platform/partitions.py
LOCK_TIMEOUT = "5s"
@dataclass(frozen=True, slots=True)
class PartitionedTable:
    table: str                        # "audit_event", "transaction", later "txn_decision"
    column: str                       # the RANGE column, for documentation and is_partition_name
    truncate_guard: str | None = None # a trigger function to run BEFORE TRUNCATE on each partition (audit's IMMUTABLE_FUNCTION)
def register_partitioned_table(spec: PartitionedTable) -> None      # idempotent by table name
def partitioned_tables() -> tuple[PartitionedTable, ...]            # registration order
def partition_name(table: str, month: date) -> str                  # f"{table}_y{month:%Y}m{month:%m}"
def is_partition_name(name: str) -> bool                            # <table>_default or <table>_yYYYYmMM for a registered table
def next_month(d: date) -> date
def harden_partition(conn: Connection, name: str, *, truncate_guard: str | None = None) -> None
@dataclass(frozen=True, slots=True)
class PartitionReport: created: list[str]; skipped: dict[str, str]
def ensure_partitions(conn: Connection, *, table: str, months_ahead: int = 3, start: date | None = None) -> PartitionReport
    # advisory lock hashtext('<table>_partitions'); the truncate guard of the registered spec, if any
def ensure_all_partitions(conn: Connection, *, months_ahead: int = 3) -> dict[str, PartitionReport]   # every registered table, in order
```

`audit/partitions.py` keeps `TABLE`, `IMMUTABLE_FUNCTION`, `partition_name(month)`, `next_month`, `harden_partition(conn, name)`, `PartitionReport`, `ensure_partitions(conn, *, months_ahead=3, start=None)` as one-line wrappers (so `0001_kernel.py`, `migrate.py`, the tests and `test_structural.py` keep their imports) and calls `register_partitioned_table(PartitionedTable("audit_event", "at", IMMUTABLE_FUNCTION))` at import. `migrate.upgrade()` calls `ensure_all_partitions(conn, months_ahead=3)`. `env.py`'s `include_name` and `test_schema_sync._include_name` use `is_partition_name(name)` instead of the `audit_event_` prefix (`transaction_ref` and `transaction_batch` must not be excluded). The log line becomes `"%s partition %s not created (sqlstate %s): %s"` with the table first.

- [ ] **Step 1: Write the failing tests** in `tests/platform/test_partitions.py` (`admin_engine`; a throwaway partitioned table `pt_probe (tenant_id uuid, at timestamptz, id uuid) PARTITION BY RANGE (at)` created and dropped by a fixture, registered with `truncate_guard=None`):

```python
def test_audit_event_is_registered_with_its_truncate_guard(): ...          # partitioned_tables()[0] == PartitionedTable("audit_event", "at", "audit_event_immutable")
def test_partition_names_follow_the_table(): ...                           # partition_name("transaction", date(2027, 2, 1)) == "transaction_y2027m02"
def test_is_partition_name_matches_only_registered_tables(): ...           # audit_event_default, audit_event_y2026m10 True; transaction_ref, audit_event_x, unregistered_y2026m10 False
def test_ensure_partitions_creates_hardens_and_is_idempotent_for_a_registered_table(admin_engine): ...   # pt_probe: 2 created then 0; relforcerowsecurity; no grant to xolotl_app (has_table_privilege false for SELECT)
def test_a_table_without_a_guard_gets_no_truncate_trigger(admin_engine): ...
def test_two_tables_take_turns_on_their_own_locks(admin_engine): ...       # a transaction holding pt_probe's advisory lock does not block audit_event's ensure (completes within LOCK_TIMEOUT)
def test_ensure_all_partitions_covers_every_registered_table(admin_engine): ...   # keys == [spec.table for spec in partitioned_tables()]
def test_upgrade_creates_the_partitions_of_every_registered_table(admin_engine): ...   # drop pt_probe's month partitions, migrate.upgrade(), present again
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_partitions.py --no-cov -q` → FAIL (import).
- [ ] **Step 3: Implement** the module, the wrappers, the `migrate` and `env.py` changes.
- [ ] **Step 4: Run to verify pass**, plus `uv run pytest tests/platform/test_audit.py tests/platform/test_ddl.py tests/platform/test_schema_sync.py tests/platform/test_alembic_env.py tests/isolation --no-cov -q`, then the gates.
- [ ] **Step 5: Commit** `refactor(platform): generalise monthly partition maintenance into xolotl.platform.partitions`.

---

### Task A3: The canonical transaction schema, validation with field errors, the content hash, `EnrichedTransaction`, the counterparty input types

Depends on: nothing (pure code; runs beside A1). Console: no. Branch `sp04a-a3-canonical-schema`.

**Files:**
- Create: `src/xolotl/modules/monitoring/__init__.py` (docstring), `src/xolotl/modules/monitoring/txn/__init__.py`, `src/xolotl/modules/monitoring/txn/schema.py`, `src/xolotl/modules/screening/counterparty/__init__.py`, `src/xolotl/modules/screening/counterparty/schema.py`
- Modify: `.importlinter` (the `monitoring-core-is-pure` contract)
- Test: `tests/modules/monitoring/__init__.py`, `tests/modules/monitoring/txn/__init__.py`, `tests/modules/monitoring/txn/test_schema.py`, `tests/modules/screening/counterparty/__init__.py`, `tests/modules/screening/counterparty/test_schema.py`

**Interfaces:**

Consumes: `CountryCode` (`xolotl.modules.party.schemas`), `IdentifierType` (`party.enums`), `ConversionRule`, `pydantic` (`JsonValue`, `ValidationError`), `canonical_json` is not platform-pure (`platform.audit.chain`): the hash uses `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` locally.

Produces:

```python
# screening/counterparty/schema.py (pure)
class CounterpartyKind(StrEnum): PERSON; ORGANIZATION; UNKNOWN
class CounterpartyScreeningStatus(StrEnum): UNSCREENED; CLEAR; PENDING_REVIEW; TRUE_MATCH
class CounterpartyIdentifier(BaseModel): id_type: IdentifierType; value: str = Field(min_length=1, max_length=64); country: CountryCode | None = None   # Deviation 13
class CounterpartyInput(BaseModel):              # extra="forbid"; a model validator: at least one of name, account, wallet_address, identifier, else ValueError("COUNTERPARTY_EMPTY")
    kind: CounterpartyKind = CounterpartyKind.UNKNOWN; name: str | None = Field(default=None, max_length=300); account: str | None = Field(default=None, max_length=64)
    institution: str | None = Field(default=None, max_length=120); country: CountryCode | None = None
    wallet_address: str | None = Field(default=None, max_length=128); wallet_network: str | None = Field(default=None, max_length=32)
    identifier: CounterpartyIdentifier | None = None

# monitoring/txn/schema.py (pure)
class Direction(StrEnum): IN; OUT; INTERNAL
class Rail(StrEnum): SPEI; CODI; CASH; CARD; WALLET; SWIFT; CRYPTO_ONCHAIN; OTHER
class Instrument(StrEnum): CASH; TRANSFER; CARD; CHEQUE; CRYPTO; FX; FEE
class Channel(StrEnum): APP; WEB; API; BRANCH; ATM; AGENT; OTHER
class KeyKind(StrEnum): PARTY; RELATIONSHIP; ACCOUNT; COUNTERPARTY; DEVICE
SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"MXN", "USD"})     # Deviation 6
class CryptoContext(BaseModel): network: str; tx_hash: str | None = None; address: str | None = None; self_custody: bool | None = None; asset_code: str; asset_qty: Decimal = Field(gt=0)
class FxContext(BaseModel): from_ccy: str; to_ccy: str; rate: Decimal = Field(gt=0); from_amount_minor: int; to_amount_minor: int
class DeviceContext(BaseModel): device_id: str | None = None; ip: str | None = None; geo_country: CountryCode | None = None; geo_admin_area: str | None = None
class TransactionInput(BaseModel):               # extra="forbid"; fields and constraints exactly spec §4 (external_id 1..128, amount gt=0 and decimal_places=2, currency ^[A-Z]{3}$, raw: dict[str, JsonValue] = {})
class BatchRequest(BaseModel): transactions: list[TransactionInput] = Field(min_length=1)    # the max is the setting, checked by the route (413)
class FieldErrorCode(StrEnum): REQUIRED; INVALID; OUT_OF_RANGE; CUSTOMER_REF_UNKNOWN; ACCOUNT_REF_CONFLICT; DUPLICATE_IN_BATCH; FUTURE; NAIVE_DATETIME; CONTEXT_REQUIRED; COUNTERPARTY_EMPTY; TOO_LONG
class FieldError(BaseModel): index: int; field: str; code: FieldErrorCode
def parse_item(index: int, data: Mapping[str, object], *, now: datetime, max_future_minutes: int) -> TransactionInput | list[FieldError]
    # pydantic errors mapped by type: missing → REQUIRED; string_too_long → TOO_LONG; greater_than, less_than, decimal_* → OUT_OF_RANGE; the COUNTERPARTY_EMPTY ValueError → COUNTERPARTY_EMPTY; anything else → INVALID; field = the dotted loc ("counterparty.name")
    # then: naive occurred_at → NAIVE_DATETIME; occurred_at > now + max_future_minutes → FUTURE; currency not in SUPPORTED_CURRENCIES → INVALID on "currency";
    # instrument CRYPTO without crypto, FX without fx, rail CRYPTO_ONCHAIN without crypto.network → CONTEXT_REQUIRED on "crypto" / "fx" / "crypto.network"
def validate_batch(items: Sequence[Mapping[str, object]], *, now: datetime, max_future_minutes: int) -> tuple[list[TransactionInput], list[FieldError]]
    # every item through parse_item; a repeated external_id → DUPLICATE_IN_BATCH on the later index; errors sorted by (index, field)
def content_hash(item: TransactionInput) -> bytes                     # sha256 of the canonical JSON of model_dump(mode="json")
def amount_minor(amount: Decimal) -> int                              # amount × 100 (two decimal places are enforced by the schema)

class UnitAmount(BaseModel): amount: Decimal; unit_value_id: int | None; resolved_date: date     # frozen
class PeriodKeys(BaseModel): DAY: str; MONTH: str; QUARTER: str; BUSINESS_DAY: bool             # "2027-02-03", "2027-02", "2027-Q1"
class EnrichedTransaction(BaseModel):            # frozen; every stored column of spec §5.2 `transaction` that the evaluator reads, plus:
    id: UUID; external_id: str; occurred_at: datetime; received_at: datetime; party_id: UUID; relationship_id: UUID; account_id: UUID | None; product: str | None
    direction: Direction; rail: Rail; instrument: Instrument; channel: Channel; amount_minor: int; currency: str; fee_minor: int | None
    asset_qty: Decimal | None; asset_code: str | None; unit_amounts: dict[str, UnitAmount]
    counterparty_id: UUID | None; counterparty_key: str | None; counterparty_country: str | None; counterparty_screening_status: CounterpartyScreeningStatus | None
    device_key: str | None; geo_country: str | None; geo_admin_area: str | None; crypto: dict[str, JsonValue] | None; fx: dict[str, JsonValue] | None; purpose_code: str | None
    period_keys: PeriodKeys; tier: str | None; risk_rating: str | None; monitoring_profile: str; policy_snapshot_id: UUID; policy_country: str; late: bool
    def key_for(self, kind: KeyKind) -> str | None     # PARTY/RELATIONSHIP/ACCOUNT → str(id); COUNTERPARTY → counterparty_key; DEVICE → device_key; ACCOUNT → None when account_id is None (C5's decide path with an account_ref never seen: ACCOUNT-keyed aggregates read empty)
    def unit_amount(self, unit_key: str) -> Decimal | None
```

`.importlinter`:

```ini
[importlinter:contract:monitoring-core-is-pure]
name = the monitoring core imports no database, web framework or other module (SP-04 spec section 2)
type = forbidden
source_modules =
    xolotl.modules.monitoring.txn.schema
    xolotl.modules.screening.counterparty.schema
forbidden_modules =
    sqlalchemy
    fastapi
    xolotl.platform
    xolotl.modules.party
    xolotl.modules.screening.engine
    xolotl.modules.screening.catalog
    xolotl.modules.tenants
    xolotl.modules.reporting
ignore_imports =
    xolotl.modules.monitoring.txn.schema -> xolotl.platform.errors
    xolotl.modules.monitoring.txn.schema -> xolotl.platform.enums
    xolotl.modules.monitoring.txn.schema -> xolotl.platform.units.rules
    xolotl.modules.monitoring.txn.schema -> xolotl.modules.party.schemas
    xolotl.modules.screening.counterparty.schema -> xolotl.modules.party.schemas
    xolotl.modules.screening.counterparty.schema -> xolotl.modules.party.enums
```

(A7 adds the four `aggregates` modules and `platform.policy.durations`; 4b's pure modules get their own contract `monitoring-evaluator-is-pure` in B1. import-linter fails on an unmatched ignore line, so A3 ships without the three txn.schema -> platform.* lines; the first task whose pure module imports platform.errors, platform.enums, platform.units.rules or platform.policy.durations adds that line.)

- [ ] **Step 1: Write the failing tests.** `test_schema.py` (no database; `now = datetime(2026, 12, 13, 12, tzinfo=UTC)`; a helper `item(**overrides)` giving a valid cash deposit):

```python
def test_a_valid_item_parses_and_hashes_deterministically(): ...                 # same dict twice → same content_hash; a changed raw value → different
@pytest.mark.parametrize(("overrides", "field", "code"), [...])                  # one row per FieldErrorCode except CUSTOMER_REF_UNKNOWN and ACCOUNT_REF_CONFLICT (A4's), plus: unknown top-level key → INVALID "x"; amount "10.001" → OUT_OF_RANGE; currency "EUR" → INVALID; occurred_at 11 minutes ahead → FUTURE; 9 minutes ahead ok
def test_each_rule_reports_its_field_and_code(overrides, field, code): ...
def test_context_is_required_per_instrument_and_rail(): ...                      # CRYPTO/crypto, FX/fx, CRYPTO_ONCHAIN/crypto.network
def test_a_counterparty_needs_one_of_name_account_wallet_or_identifier(): ...   # institution alone → COUNTERPARTY_EMPTY on "counterparty"
def test_duplicate_external_ids_in_a_batch_are_reported_on_the_later_index(): ...
def test_errors_are_sorted_by_index_then_field(): ...
def test_amount_minor_is_exact(): ...                                            # Decimal("1234.56") → 123456
def test_enriched_transaction_keys_per_kind(): ...                               # key_for for the five kinds; None for a missing counterparty or device
```

`tests/modules/screening/counterparty/test_schema.py`: `test_kind_defaults_to_unknown_and_extra_fields_are_refused`, `test_identifier_shape_matches_the_ad_hoc_identifier` (same field names and constraints as `AdHocIdentifier`: a dict valid for one validates for the other).
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/txn/test_schema.py tests/modules/screening/counterparty/test_schema.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the two schema modules and the contract.
- [ ] **Step 4: Run to verify pass**, then `make importlint` and the gates.
- [ ] **Step 5: Commit** `feat(monitoring): canonical transaction schema with field-level validation and the counterparty input types`.

---

### Task A2: Migration 0012, the models, enums, settings, module contracts and isolation factories

Depends on: A1 (the migration creates partitions through the generic helper), A3 (column enums). Console: no. Branch `sp04a-a2-migration-0012`.

**Files:**
- Create: `src/xolotl/migrations/versions/0012_transactions.py`, `src/xolotl/modules/monitoring/enums.py`, `src/xolotl/modules/monitoring/txn/models.py`, `src/xolotl/modules/monitoring/aggregates/__init__.py`, `src/xolotl/modules/monitoring/aggregates/models.py`, `src/xolotl/modules/screening/counterparty/models.py`, `tests/modules/monitoring/support.py` (`itf_tenant`, `both_packs_tenant`)
- Modify: `src/xolotl/platform/config.py`, `.env.example`, `README.md` (configuration table), `src/xolotl/models.py`, `.importlinter` (`monitoring-boundaries`, `screening-never-imports-monitoring`), `tests/platform/test_schema_sync.py` (`NATURALLY_KEYED`), `tests/isolation/test_structural.py` (partitions ahead for `transaction`)
- Test: `tests/modules/monitoring/txn/test_migration_0012.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_config.py`, `tests/isolation/factories.py`

**Interfaces:**

Consumes: `TenantTableMixin`, `tenant_table_args`, `tenant_fk`, `enable_rls`, `enum_column`, `EncryptedStr`, `EncryptedJson`, `APP_ROLE`; A1's `ensure_partitions`, `harden_partition`, `register_partitioned_table`, `PartitionedTable`; A3's enums; the `_pack_version_id`/`_policy_snapshot` factory helpers; `install_both_packs`, `assign_pack`, `seed_mx_holidays`, `load_unit_values`.

Produces:

```python
# monitoring/enums.py
class TransactionStatus(StrEnum): RECEIVED; PROCESSED; PARKED; BACKFILL
class BatchSource(StrEnum): API; FILE; CLI
class FileStatus(StrEnum): UPLOADED; PROCESSING; COMPLETED; FAILED
class AccountKind(StrEnum): DEFAULT; EMONEY; WALLET; CARD; OTHER
class AccountStatus(StrEnum): ACTIVE; CLOSED
class ParkReason(StrEnum): UNIT_VALUE_MISSING; POLICY_NOT_ASSIGNED
class BucketKind(StrEnum): HOUR; DAY; MONTH; QUARTER; ACCUM

# txn/models.py (TenantTableMixin unless stated; vocabulary columns enum_column(...))
class Account: relationship_id, party_id, external_ref, kind, currency, status, first_seen_at, last_seen_at    # UNIQUE (tenant_id, external_ref)
class TransactionBatch: source, file_id, submitted_by, item_count, accepted, duplicates, received_at, request_id
class TransactionFile: storage_key, sha256 (LargeBinary), size (BigInteger), content_type, status, rows_total, rows_accepted, rows_duplicate, rows_rejected, cursor, errors_key, uploaded_by, uploaded_at, finished_at, last_error   # UNIQUE (tenant_id, sha256)
class Transaction(Base):   # Deviation 3: tenant_id, occurred_at, id explicit, PrimaryKeyConstraint("tenant_id", "occurred_at", "id")
    external_id, received_at, batch_id, party_id, relationship_id, account_id, product, direction, rail, instrument, channel, amount_minor (BigInteger), currency (String(3)),
    fee_minor, asset_qty (Numeric(28, 10)), asset_code, unit_amounts (JSONB), counterparty_id, counterparty_country, counterparty_screening_status (Text), device_hash (LargeBinary),
    geo_country, geo_admin_area, crypto (JSONB), fx (JSONB), purpose_code, period_keys (JSONB), tier, risk_rating, monitoring_profile (default "STD"), policy_snapshot_id,
    status (TransactionStatus, default RECEIVED), late (default False), processed_at, decision_id (Uuid, nullable), decided_at, content_hash (LargeBinary), raw (EncryptedJson)
class TransactionRef(Base): tenant_id, external_id (composite PK), transaction_id, occurred_at, content_hash
class TransactionParked(Base): tenant_id, transaction_id (composite PK), occurred_at, reason, unit_code, needed_on, attempts, parked_at, last_error
# aggregates/models.py
class AggBucket(Base): tenant_id, key_kind, key, agg_id, bucket_start (composite PK), bucket_kind, bucket_end, value (Numeric(28,10)), count (BigInteger), max_value, min_value, first_at, last_at, updated_at
class AggDistinct(Base): tenant_id, key_kind, key, agg_id, bucket_start, member_hash (composite PK)
class AggDeclaration(Base): tenant_id, agg_id (composite PK), declaration (JSONB), bucket_kind, window_kind, window, backfilled_through, created_at
# screening/counterparty/models.py
class Counterparty(TenantTableMixin, Base): kind, name (EncryptedStr, nullable), name_hash, account, account_hash, institution, country, wallet, wallet_hash, wallet_network,
    identifier_type, identifier, identifier_hash, dedup_hash (LargeBinary), screening_status (CounterpartyScreeningStatus, default UNSCREENED), screened_at, screening_subject_id (Uuid, nullable, no FK),
    first_seen_at, last_seen_at, txn_count (BigInteger)    # UNIQUE (tenant_id, dedup_hash); CHECK ck_counterparty_one_component; indexes ix_counterparty_name_hash, ix_counterparty_wallet_hash
```

`txn/models.py` calls `register_partitioned_table(PartitionedTable("transaction", "occurred_at", None))` at import.

Migration `0012_transactions.py` (`revision = "0012_transactions"`, `down_revision = "0011_screening_hit_run_index"`; header docstring like 0010's): the ten tables exactly as spec §5.2's first block with Deviations 2 and 3, constraint names in the kernel's `pk_`/`fk_`/`uq_`/`ck_` style, in the order `account`, `counterparty`, `transaction_batch`, `transaction_file`, `transaction` (`postgresql_partition_by="RANGE (occurred_at)"`, the five indexes including the expression index `ix_transaction_period` on `(tenant_id, (period_keys->>'MONTH'), instrument)` and the partial `ix_transaction_pending`), `transaction_ref`, `transaction_parked`, `agg_bucket` (+ `ix_agg_bucket_window`), `agg_distinct`, `agg_declaration`; `enable_rls(table)` after each; then `op.execute("CREATE TABLE transaction_default PARTITION OF transaction DEFAULT")`, `harden_partition(conn, "transaction_default")` and `ensure_partitions(conn, table="transaction", months_ahead=3)`. `transaction_file.file_id` has no FK to `transaction_batch` (`transaction_batch.file_id` references `transaction_file`, composite). `downgrade()` drops the ten tables in reverse order (`transaction` takes its partitions with it).

Settings: the twelve of Global Constraints, `.env.example` lines with the defaults, README rows. `.importlinter`:

```ini
[importlinter:contract:monitoring-boundaries]
name = monitoring reads parties and counterparties through services and never imports reporting
type = forbidden
source_modules =
    xolotl.modules.monitoring
forbidden_modules =
    xolotl.modules.reporting
    xolotl.modules.party.models
    xolotl.modules.screening.counterparty.models
    xolotl.modules.screening.engine.models
ignore_imports =
    xolotl.modules.party.service -> xolotl.modules.party.models
    xolotl.modules.screening.counterparty.resolve -> xolotl.modules.screening.counterparty.models
    xolotl.modules.screening.counterparty.views -> xolotl.modules.screening.counterparty.models

[importlinter:contract:screening-never-imports-monitoring]
name = screening never imports monitoring (SP-04 spec Decision 18)
type = forbidden
source_modules =
    xolotl.modules.screening
forbidden_modules =
    xolotl.modules.monitoring
```

Factories (`tests/isolation/factories.py`): module-level `_customer_relationship(session, tenant_id) -> CustomerRelationship` (over `_party`; B4's hit and notice factories call it), `_account(session, tenant_id) -> Account` (over `_customer_relationship`), `_counterparty(session, tenant_id) -> Counterparty` (`kind=UNKNOWN`, `name=encrypt("Probe", ...)`, `name_hash=b"\x01" * 32`, `dedup_hash=uuid7().bytes * 2`), `_transaction_batch(session, tenant_id)`, `_transaction(session, tenant_id) -> Transaction` (`occurred_at=clock.now()`, `status=RECEIVED`, `raw=encrypt_json({"k": "v"}, ...)`, `content_hash=b"\x00" * 32`), and the registered factories `account`, `counterparty`, `transaction_batch`, `transaction_file`, `transaction`, `transaction_ref`, `transaction_parked`, `agg_bucket`, `agg_distinct`, `agg_declaration` in `register_all`. `test_structural.py` gains `test_transaction_partitions_exist_for_this_month_and_three_ahead`. `support.py`: `itf_tenant(tenant, admin_engine) -> UUID` (ITF pack assigned to every product from 2026-01-01, Mexico holidays and `tests/golden/units/values.yaml` loaded) and `both_packs_tenant(...)` (ITF for product `EMONEY`, AVI for `CRYPTO`), both as plain functions the module's `conftest.py` (A4) wraps in fixtures.

- [ ] **Step 1: Write the failing tests.** `test_migration_0012.py` (the `go_down`/`database` pattern of `tests/platform/test_migration_0007.py`):

```python
REVISION, BEFORE = "0012_transactions", "0011_screening_hit_run_index"
TABLES = ("account", "counterparty", "transaction_batch", "transaction_file", "transaction", "transaction_ref", "transaction_parked", "agg_bucket", "agg_distinct", "agg_declaration")
def test_0012_follows_0011(): ...
def test_downgrade_drops_the_ten_tables_and_upgrade_recreates_them_with_forced_rls(database): ...
def test_transaction_is_partitioned_with_a_hardened_default_and_three_months_ahead(database): ...   # relkind 'p'; transaction_default and this month + 3 are partitions with forced RLS and no app-role privilege
def test_a_row_outside_every_monthly_partition_lands_in_the_default_partition_under_rls(engine): ...   # Review Focus 4: occurred_at 2019-06-01 → tableoid = transaction_default; tenant B reads nothing
def test_the_external_id_is_unique_per_tenant_and_the_ref_points_at_the_row(admin_engine): ...   # second transaction_ref with one external_id → IntegrityError; a ref to a missing (occurred_at, id) → IntegrityError
def test_a_counterparty_needs_one_hash_and_one_row_per_dedup_hash(admin_engine): ...
def test_agg_bucket_is_keyed_by_key_kind_key_agg_id_and_bucket_start(admin_engine): ...
def test_the_pending_and_period_indexes_exist(admin_engine): ...                # pg_indexes: ix_transaction_pending has the WHERE; ix_transaction_period's definition contains "period_keys ->> 'MONTH'"
```

`test_config.py`: `test_monitoring_settings_default_to_the_spec_values` (the twelve values), `test_the_monitoring_crons_are_validated` (`"daily"` → `ValidationError` for both); add the twelve names to `test_env_example_is_a_valid_configuration`. `CHAIN` gains `"0012_transactions"`. The factories and the structural test as above.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/txn/test_migration_0012.py tests/platform/test_migration_chain.py tests/platform/test_config.py tests/isolation --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the enums, models, migration, settings, `.env.example`, README rows, `src/xolotl/models.py` imports (`monitoring_txn_models`, `monitoring_aggregates_models`, `screening_counterparty_models`, sorted `__all__`), the two contracts, `NATURALLY_KEYED`, the factories, `support.py`.
- [ ] **Step 4: Run to verify pass**, plus `tests/platform/test_schema_sync.py tests/platform/test_alembic_env.py tests/platform/test_partitions.py`, then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): migration 0012 with accounts, counterparties, partitioned transactions, refs, files, parked rows and aggregates`.

---

### Task A4: Accounts, counterparty resolution with the blind index, batch ingest with idempotent refs, the processing enqueue and the batch event

Depends on: A2, A3. Console: no. Branch `sp04a-a4-batch-ingest`.

Note from A2: add the ignore line `xolotl.modules.screening.counterparty.resolve -> xolotl.modules.screening.counterparty.models` to the `monitoring-boundaries` contract in `.importlinter` in this task.

**Files:**
- Create: `src/xolotl/modules/monitoring/kinds.py`, `src/xolotl/modules/monitoring/errors.py`, `src/xolotl/modules/monitoring/events.py`, `src/xolotl/modules/monitoring/txn/accounts.py`, `src/xolotl/modules/monitoring/txn/ingest.py`, `src/xolotl/modules/monitoring/process/__init__.py`, `src/xolotl/modules/monitoring/process/enqueue.py`, `src/xolotl/modules/screening/counterparty/resolve.py`, `tests/modules/monitoring/conftest.py`
- Modify: `src/xolotl/modules/party/service.py`, `src/xolotl/modules/screening/counterparty/__init__.py`, `tests/modules/monitoring/support.py` (+ `make_item`, `ingest_items`)
- Test: `tests/modules/monitoring/txn/test_accounts.py`, `tests/modules/monitoring/txn/test_ingest.py`, `tests/modules/screening/counterparty/test_resolve.py`, `tests/modules/party/test_service.py`, `tests/platform/test_errors.py`

**Interfaces:**

Consumes: A2 models and enums; A3 `TransactionInput`, `content_hash`, `amount_minor`, `CounterpartyInput`; `normalize_name`, `EntityType` (library); `blind_index`, `get_key_provider().index_key`, `encrypt`, `encrypt_json`; `PostgresJobQueue.enqueue`; `publish`, `Event`; `ErrorItem`, `XolotlError.errors`; `clock.now`; `bound_context`.

Produces:

```python
# party/service.py
@dataclass(frozen=True, slots=True)
class RelationshipRef: id: UUID; party_id: UUID; product: str | None; account_tier: str | None; status: CustomerRelationshipStatus
def relationship_by_external_ref(s: Session, refs: Collection[str]) -> dict[str, RelationshipRef]   # one query; missing refs absent
def relationship_ref(s: Session, relationship_id: UUID) -> RelationshipRef                           # NotFoundError

# monitoring/kinds.py
PROCESS_KIND = "monitoring.process"; INGEST_FILE_KIND = "monitoring.ingest_file"; SWEEP_KIND = "monitoring.sweep"
AGG_BACKFILL_KIND = "monitoring.agg_backfill"; BACKFILL_KIND = "monitoring.backfill"; PARTITIONS_KIND = "monitoring.partitions"
def process_key(party_id: UUID) -> str      # f"process:{party_id}"

# monitoring/errors.py
class BatchInvalidError(ValidationFailedError): default_code = "TRANSACTION_BATCH_INVALID"   # errors: ErrorItem(loc=("transactions", index, field), msg=<fixed sentence per code>, type=code)
class BatchTooLargeError(XolotlError): status = 413; default_code = "TRANSACTION_BATCH_TOO_LARGE"
class ExternalIdConflictError(DomainError): default_code = "EXTERNAL_ID_CONFLICT"            # detail f"transactions[{index}]"

# monitoring/events.py
EVENT_SOURCE = "xolotl.monitoring"
TRANSACTION_BATCH_ACCEPTED = "monitoring.transaction_batch_accepted.v1"; TRANSACTION_FILE_COMPLETED = "monitoring.transaction_file_completed.v1"
TRANSACTION_PROCESSED = "monitoring.transaction_processed.v1"; TRANSACTION_PARKED = "monitoring.transaction_parked.v1"
STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (TRANSACTION_PARKED, TRANSACTION_PROCESSED)     # Deviation 7
def publish_monitoring_event(s, event_type, *, entity: str, entity_id: UUID, key: str, data: dict[str, JsonValue], policy_snapshot_id: UUID | None = None) -> Event

# screening/counterparty/resolve.py
@dataclass(frozen=True, slots=True)
class ComponentHashes: name_hash: bytes | None; account_hash: bytes | None; wallet_hash: bytes | None; identifier_hash: bytes | None
@dataclass(frozen=True, slots=True)
class CounterpartyRef: id: UUID; dedup_hash: bytes; country: str | None; screening_status: CounterpartyScreeningStatus; created: bool
def name_hash_of(session: Session, name: str, *, entity_type: EntityType) -> bytes; def account_hash_of(session: Session, account: str) -> bytes
def wallet_hash_of(session: Session, address: str) -> bytes; def identifier_hash_of(session: Session, id_type: IdentifierType, value: str) -> bytes   # the four components; C2 looks counterparties up by the last two
def component_hashes(session: Session, input: CounterpartyInput) -> ComponentHashes          # the four helpers over an input
def dedup_hash_of(session: Session, input: CounterpartyInput) -> bytes                 # Global Constraints "Keys and hashes"
def resolve(session: Session, input: CounterpartyInput) -> CounterpartyRef
    # INSERT … ON CONFLICT (tenant_id, dedup_hash) DO UPDATE SET last_seen_at = now(), txn_count = txn_count + 1 RETURNING id, country, screening_status, (xmax = 0) AS created;
    # a new row stores the encrypted name, account, wallet and identifier plus institution, country, wallet_network, identifier_type; txn_count starts at 1
def status_by_dedup_hash(session: Session, dedup_hash: bytes) -> CounterpartyScreeningStatus   # UNSCREENED when no row

# monitoring/txn/accounts.py
class AccountRefConflict(Exception): index: int
def default_account(session: Session, relationship: RelationshipRef) -> Account       # kind DEFAULT, external_ref f"default:{relationship.id}", upsert
def account_for(session: Session, relationship: RelationshipRef, account_ref: str | None) -> Account
    # None → default_account; else upsert by (tenant_id, external_ref) with kind OTHER, last_seen_at = now; an existing row of another relationship → AccountRefConflict
def find_account(session: Session, relationship_id: UUID, account_ref: str | None) -> Account | None   # a read, no upsert: the row by (tenant_id, external_ref), or the DEFAULT account when None; C5's decide path

# monitoring/txn/ingest.py
@dataclass(frozen=True, slots=True)
class BatchReport: batch_id: UUID; accepted: int; duplicates: int; backfill: int; transaction_ids: tuple[UUID, ...]; party_ids: frozenset[UUID]
def ingest_batch(session: Session, items: Sequence[TransactionInput], *, source: BatchSource, submitted_by: str, queue: PostgresJobQueue,
                 file_id: UUID | None = None, request_id: str | None = None) -> BatchReport

# monitoring/process/enqueue.py
def enqueue_processing(session: Session, queue: PostgresJobQueue, party_ids: Iterable[UUID]) -> int   # enqueue(kind=PROCESS_KIND, key=process_key(id), payload={"party_id": str(id)}); counts the ids the dedup index accepted
```

`ingest_batch` (spec §6.1 steps 1, 2, 4, 5; Deviation 4): `relationship_by_external_ref` for the batch's distinct `customer_ref`s (missing → `CUSTOMER_REF_UNKNOWN` on `customer_ref`; a relationship whose `status` is not `ACTIVE` is also `CUSTOMER_REF_UNKNOWN`), `account_for` per item (`AccountRefConflict` → `ACCOUNT_REF_CONFLICT` on `account_ref`); any error → `BatchInvalidError` with every error, nothing written. Then `resolve` for each counterparty input (one call per distinct `dedup_hash_of`), the `transaction_batch` row, and per item: `content_hash`, `uuid7()` id, `received_at = clock.now()`, `status = BACKFILL` when `received_at − occurred_at > monitoring_late_days` days else `RECEIVED`; insert the `transaction_ref` first with `ON CONFLICT (tenant_id, external_id) DO NOTHING RETURNING external_id`; when nothing returned, read the stored ref: equal `content_hash` → duplicate (no row written, counted, the stored `transaction_id` returned in `transaction_ids`), different → `ExternalIdConflictError` (the whole batch rolls back with the caller's transaction); otherwise insert the row (`raw = encrypt_json(item.model_dump(mode="json"), …)` with the counterparty input inside, `crypto` without `address`, `device_hash` from `device.device_id`, `geo_*` plain, the IP nowhere but `raw`). `accepted` counts new rows (`BACKFILL` ones included), `batch.item_count = len(items)`. Then `enqueue_processing` for the parties of `RECEIVED` rows and `publish_monitoring_event(TRANSACTION_BATCH_ACCEPTED, entity="transaction_batch", entity_id=batch.id, key=str(batch.id), data={"batch_id", "accepted", "duplicates"})`. No audit row for an API batch (spec §14: the batch row is the provenance record). `support.make_item(**overrides) -> dict` (a valid MXN cash deposit for `customer_ref="cust-1"`, `occurred_at` ten minutes ago) and `support.ingest_items(session, items, *, queue) -> BatchReport` (parses with `validate_batch` and raises on errors). `conftest.py`: fixtures `itf_tenant`, `both_packs_tenant`, `queue` (a `PostgresJobQueue` over the test engine), `customer` (a party with `customer_relationship(external_ref="cust-1", product="EMONEY", account_tier="N1")`).

- [ ] **Step 1: Write the failing tests.** `test_errors.py`: rows `(BatchInvalidError, 422, "TRANSACTION_BATCH_INVALID")`, `(BatchTooLargeError, 413, "TRANSACTION_BATCH_TOO_LARGE")`, `(ExternalIdConflictError, 409, "EXTERNAL_ID_CONFLICT")`. `tests/modules/party/test_service.py`: `test_relationship_by_external_ref_returns_the_found_refs_in_one_query`, `test_relationship_ref_of_another_tenant_is_not_found`. `test_resolve.py` (database, keys):

```python
def test_the_same_counterparty_resolves_to_one_row_with_a_growing_count(): ...        # "Juan Pérez" then "juan perez " → one row, txn_count 2, created True then False
def test_components_change_the_dedup_hash(): ...                                       # name alone, name + account, account alone → three rows
def test_a_wallet_hash_is_case_sensitive(): ...                                        # "0xAbC" and "0xabc" → two rows
def test_identifiers_are_normalized_with_the_party_rules(): ...                        # " goma-800101 h7a " and "GOMA800101H7A" → one row
def test_no_plaintext_is_stored(): ...                                                 # name, account, address absent from everything_stored
def test_status_by_dedup_hash_is_unscreened_for_an_unknown_hash(): ...
def test_another_tenant_never_sees_the_row(): ...
```

`test_accounts.py`: `test_the_default_account_is_one_per_relationship`, `test_an_account_ref_is_created_on_first_sight_and_reused`, `test_an_account_ref_bound_to_another_relationship_conflicts`. `test_ingest.py`:

```python
def test_a_batch_writes_rows_refs_a_batch_row_and_one_job_per_party(): ...             # status RECEIVED, received_at set, raw decrypts to the item, counterparty_id set, job key process:<party>
def test_a_resend_is_a_duplicate_and_writes_nothing_new(): ...                          # second report: accepted 0, duplicates 1, same transaction_ids; row count unchanged
def test_a_resend_with_different_content_is_a_conflict_and_the_batch_rolls_back(): ...  # EXTERNAL_ID_CONFLICT; no batch row, no new rows
def test_an_unknown_or_closed_customer_ref_refuses_the_whole_batch(): ...              # errors [("transactions", 1, "customer_ref", "CUSTOMER_REF_UNKNOWN")]; nothing written
def test_an_old_transaction_is_backfill_and_not_enqueued(): ...                         # occurred_at 8 days ago → BACKFILL; report.backfill 1; no job
def test_the_batch_event_carries_ids_and_counts_only(): ...
def test_two_concurrent_batches_with_one_external_id_keep_one_row(): ...               # Review Focus 1: two threads, two sessions; one row, one ref, the other reports a duplicate or a conflict, never two rows
def test_the_ip_lives_only_in_raw(): ...                                                # "203.0.113.9" absent from every column but raw's ciphertext (everything_stored minus raw)
```
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/txn/test_accounts.py tests/modules/monitoring/txn/test_ingest.py tests/modules/screening/counterparty/test_resolve.py tests/modules/party/test_service.py -k "relationship_by or relationship_ref" tests/platform/test_errors.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the party additions, the counterparty resolution, accounts, ingest, enqueue, events, errors, kinds, support and conftest.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): batch ingest with idempotent external ids, accounts, blind-indexed counterparty resolution and per-party processing jobs`.

---

### Task A5: File ingest: CSV, JSON Lines and XLSX readers, the sealed upload, the paged `monitoring.ingest_file` job with resume and the row error report, the composition root, `monitoring ingest-file`

Depends on: A4. Console: no. Branch `sp04a-a5-file-ingest`.

Note from A4: a file page that meets `BatchInvalidError` reports the failing rows, drops those indexes and re-ingests the rest of the page within it (under a savepoint), per spec §6.2.

Note from A3: the monitoring composition root must `register_pii_model` `CounterpartyInput`, `CounterpartyIdentifier`, `DeviceContext` and `CryptoContext`, because a nested model's PII tags take effect only once registered and the pure core cannot register them.

**Files:**
- Create: `src/xolotl/platform/crypto/blobs.py`, `src/xolotl/modules/monitoring/txn/files.py`, `src/xolotl/modules/monitoring/composition.py`, `tests/modules/monitoring/txn/fixtures/` (`deposits.csv`, `deposits_semicolon_bom.csv`, `deposits.jsonl`, `deposits.xlsx`, `bad_rows.csv`, `README.md`; synthetic names and refs only)
- Modify: `src/xolotl/modules/monitoring/errors.py`, `src/xolotl/app.py` (`register_monitoring` in `wire_platform` after `register_rescreen`), `src/xolotl/cli.py` (`monitoring_app`, `ingest-file`)
- Test: `tests/platform/test_crypto_primitives.py` (blobs), `tests/modules/monitoring/txn/test_files.py`, `tests/platform/test_errors.py`, `tests/platform/test_worker.py` (wiring assertion), `tests/test_cli.py`

**Interfaces:**

Consumes: A3 `parse_item`, `validate_batch`; A4 `ingest_batch`, `publish_monitoring_event`, `INGEST_FILE_KIND`; `ObjectStore`, `get_key_provider().data_key`, `encrypt_value`, `decrypt_value`, `aad_for`, `parse_header`; `openpyxl.load_workbook(read_only=True, data_only=True)`; `csv`; `register_job_handler`, `register_staff_only`; `get_settings()`.

Produces:

```python
# platform/crypto/blobs.py
def seal_blob(session: Session, tenant_id: UUID, data: bytes) -> bytes      # the tenant DATA key's current version; encrypt_value with aad_for(tenant, DATA)
def open_blob(session: Session, tenant_id: UUID, sealed: bytes) -> bytes    # the version from parse_header

# monitoring/errors.py (+)
class FileTooLargeError(XolotlError): status = 413; default_code = "TRANSACTION_FILE_TOO_LARGE"
class FileInvalidError(ValidationFailedError): default_code = "TRANSACTION_FILE_INVALID"

# monitoring/txn/files.py
CSV_TYPE = "text/csv"; JSONL_TYPE = "application/x-ndjson"; XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ACCEPTED_TYPES = frozenset({CSV_TYPE, JSONL_TYPE, XLSX_TYPE})
REQUIRED_COLUMNS = ("external_id", "occurred_at", "customer_ref", "direction", "rail", "instrument", "amount", "currency")
@dataclass(frozen=True, slots=True)
class FileRow: number: int; data: dict[str, object] | None; errors: tuple[FieldError, ...]   # number: the 1-based data row (header excluded) or the JSONL line
@dataclass(frozen=True, slots=True)
class RowError: row: int; column: str; code: str
def read_header(data: bytes, content_type: str) -> list[str]            # FileInvalidError: unreadable, empty, or a REQUIRED_COLUMNS name missing (CSV/XLSX); JSONL: the first line is not a JSON object
def count_rows(data: bytes, content_type: str) -> int
def iter_rows(data: bytes, content_type: str, *, start: int, limit: int) -> Iterator[FileRow]   # data rows [start, start + limit); CSV: UTF-8 with BOM tolerated, delimiter from the header line ("," or ";"); XLSX: first sheet; JSONL: json.loads per line
def row_to_item(cells: Mapping[str, str]) -> dict[str, object]           # dotted headers nest; blank cells dropped; unknown columns → raw[column] (Deviation 12); a malformed JSONL line → FieldError(index=row, field="", code=INVALID)
def upload_file(session: Session, store: ObjectStore, *, data: bytes, content_type: str, uploaded_by: str, queue: PostgresJobQueue) -> tuple[TransactionFile, bool]
    # size > monitoring_file_max_mb MiB → FileTooLargeError; content type outside ACCEPTED_TYPES → FileInvalidError; read_header; rows_total = count_rows (> monitoring_file_max_rows → FileTooLargeError);
    # an existing row with this sha256 → (row, False); else store.put(tenant, f"transactions/files/{id}", seal_blob(...), content_type="application/octet-stream", metadata={"content_type": content_type}),
    # the UPLOADED row, audit monitoring.file_uploaded {"file_id", "size", "rows_total", "content_type"}, enqueue INGEST_FILE_KIND key f"ingest_file:{id}" payload {"file_id"}; (row, True)
def error_report_csv(errors: Sequence[RowError]) -> bytes               # header "row,column,code"
class IngestFileJob:
    def __init__(self, *, session_factory: SessionFactory, store: ObjectStore, queue: PostgresJobQueue, settings: Settings, clock_fn: Callable[[], datetime] = clock.now) -> None
    def run(self, session: Session, job: Job) -> None                     # the runner's session is only used for the final status; every page is its own tenant_session from session_factory
def ingest_page(session: Session, file: TransactionFile, rows: Sequence[FileRow], *, store: ObjectStore, queue: PostgresJobQueue) -> tuple[int, int, list[RowError]]   # (accepted, duplicates, errors)

# monitoring/composition.py
def register_monitoring(*, session_factory: SessionFactory, settings: Settings, queue: PostgresJobQueue, store: ObjectStore, notifier: Notifier) -> None
    # register_staff_only for STAFF_ONLY_EVENT_TYPES; register_job_handler(INGEST_FILE_KIND, IngestFileJob(...).run); A8 and A9 append their handlers, C5, C7 and C8 theirs; wire_platform calls B7's register_rules(queue=queue) right after it
```

`IngestFileJob.run`: load the file (`PROCESSING`, `started` logged); loop: open the sealed object (`open_blob`), `iter_rows(start=file.cursor, limit=monitoring_file_chunk_rows)`; in one `tenant_session`: rows with `data` through `validate_batch` (errors become `RowError(row, field, code)`; a `DUPLICATE_IN_BATCH` inside a page is a row error too), the valid ones through `ingest_batch(source=FILE, file_id=…, submitted_by=file.uploaded_by)` (an `ExternalIdConflictError` becomes `RowError(row, "external_id", "EXTERNAL_ID_CONFLICT")` for that row and the page is ingested again without it), `rows_accepted`, `rows_duplicate`, `rows_rejected` and `cursor` advanced, the error report object rewritten (Deviation 12) and `errors_key` set, commit; `queue.heartbeat(job, lease=timedelta(seconds=settings.monitoring_process_lease_seconds))` after each page; when `cursor >= rows_total`: `COMPLETED`, `finished_at`, publish `TRANSACTION_FILE_COMPLETED` (entity `transaction_file`, key `str(file.id)`, data `{"file_id", "status", "rows_accepted", "rows_rejected"}`). An exception sets `FAILED` with `last_error` (≤ 200 characters, the error's code and class, never a cell) in a fresh session and re-raises so the queue retries from `cursor`. The CLI `monitoring ingest-file --tenant <slug> <path> [--content-type]` (type sniffed from the suffix `.csv`, `.jsonl`, `.xlsx` unless given) uploads with `uploaded_by = f"cli:{actor}"` and runs the pages synchronously (`IngestFileJob.run` with a `Job` built in place, no worker needed), printing `file <id>: 1000 rows, 998 accepted, 1 duplicate, 1 rejected (errors: transactions/files/<id>/errors.csv)` or `file <id>: already uploaded (COMPLETED)`.

- [ ] **Step 1: Write the failing tests.** `test_crypto_primitives.py`: `test_seal_blob_round_trips_under_the_tenant_data_key`, `test_a_blob_of_another_tenant_does_not_open`. `test_errors.py`: the two rows. `test_files.py` (`itf_tenant`, `customer`, a local store in `tmp_path`):

```python
def test_a_csv_a_jsonl_and_an_xlsx_of_the_same_rows_give_the_same_items(): ...
def test_bom_semicolons_and_quoted_commas_are_read(): ...                              # Review Focus 5
def test_dotted_headers_nest_and_unknown_columns_go_to_raw(): ...
def test_an_unreadable_or_headerless_file_is_invalid_and_oversize_is_too_large(): ...
def test_an_upload_seals_the_file_stores_a_row_audits_and_enqueues_the_job(): ...      # object bytes != upload; open_blob == upload; audit row; job key ingest_file:<id>
def test_the_same_file_twice_returns_the_existing_row(): ...
def test_the_job_accepts_valid_rows_and_reports_the_rest(): ...                          # bad_rows.csv: 10 rows, 7 accepted, 1 duplicate, 2 rejected; errors.csv has two rows with (row, column, code)
def test_row_errors_never_echo_cell_values(): ...                                        # Review Focus 5: no cell of bad_rows.csv in errors.csv, last_error or any log line
def test_the_job_resumes_from_the_cursor_after_a_crash(): ...                            # chunk_rows 3, 10 rows; raise after page 2; rerun: cursor 6 → COMPLETED, no duplicate rows, counts add up
def test_completion_publishes_the_file_event(): ...
def test_a_chunk_is_one_transaction(): ...                                               # a failure inside page 2 leaves pages 0..1 committed and page 2's rows absent
```

`test_cli.py`: `test_monitoring_ingest_file_uploads_and_processes_synchronously`, `test_monitoring_ingest_file_reports_an_already_uploaded_file`. `test_worker.py`: `job_handler(INGEST_FILE_KIND).__self__` is an `IngestFileJob` after `wire_platform`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/txn/test_files.py tests/platform/test_crypto_primitives.py tests/test_cli.py -k "ingest_file or blob" --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** as above; `wire_platform` calls `register_monitoring(session_factory=…, settings=settings, queue=queue, store=store, notifier=InAppNotifier())`.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): file ingest of CSV, JSON Lines and XLSX through a sealed upload, a resumable paged job and a row error report`.

---

### Task A6: Enrichment: the policy snapshot per date and product, unit amounts keyed by unit and rule, period keys, the counterparty status and device hash, parking, and the enrichment goldens

Depends on: A2, A3 (runs beside A4). Console: no. Branch `sp04a-a6-enrichment`.

Note from A4: the counterparty key is `dedup_hash_of(session, counterparty_of(item))`, with `counterparty_of` from `xolotl.modules.monitoring.txn.schema` (the crypto address stands in for a wallet the counterparty does not name, which changes the hash). `dedup_hash_of` raises `CounterpartyUnindexableError` for an input with nothing to index, which callers must catch (the decide path treats it as no counterparty).

**Files:**
- Create: `src/xolotl/modules/monitoring/enrich/__init__.py`, `policy.py`, `units.py`, `periods.py`, `service.py`, `tests/golden/monitoring/enrichment/cases.yaml`, `tests/golden/monitoring/enrichment/README.md`
- Test: `tests/modules/monitoring/enrich/__init__.py`, `test_enrich.py`, `test_golden.py`

**Interfaces:**

Consumes: `effective_policy`, `snapshot_policy`, `EffectivePolicy`, `PolicyNotAssignedError`, `Capture`, `KycTier`, `Quantity`; `UnitService`, `Conversion`, `ConversionRule`, `UnitValueMissingError`, `resolve_date`; `load_holidays`, `Holidays`, `is_business_day`, `MEXICO_CITY`, `HolidayScope.BANXICO`; `blind_index`; A2 `Transaction`; A3 `EnrichedTransaction`, `UnitAmount`, `PeriodKeys`; A4's `RelationshipRef` is only a type here (`party.service`).

Produces:

```python
# enrich/policy.py
@dataclass(frozen=True, slots=True)
class SnapshotRef: snapshot_id: UUID; policy: EffectivePolicy
type SnapshotCache = dict[tuple[date, str | None], SnapshotRef]
def policy_date(occurred_at: datetime) -> date                                   # the calendar date in MEXICO_CITY
def snapshot_for(session: Session, *, on: date, product: str | None, cache: SnapshotCache) -> SnapshotRef   # effective_policy + snapshot_policy, memoised per (on, product); PolicyNotAssignedError propagates

# enrich/units.py
LOCAL_CCY = "MXN"; LIBRARY_PAIR = ("USD", ConversionRule.PRIOR_BANKING_DAY)
def unit_key(unit: str, rule: ConversionRule) -> str                             # f"{unit}/{rule.value}"
def unit_pairs(policy: EffectivePolicy) -> frozenset[tuple[str, ConversionRule]]
    # every captures[].threshold.value.unit with its conversion_rule; every kyc.tiers[].limits.{monthly_ops,max_balance}.value.unit with units.tier_limit_rule.value (when set); LIBRARY_PAIR
@dataclass(frozen=True, slots=True)
class UnitMissing: unit_code: str; needed_on: date
def unit_amounts_for(units: UnitService, *, amount: Decimal, currency: str, on: date, pairs: Iterable[tuple[str, ConversionRule]], holidays: Holidays) -> dict[str, UnitAmount]
    # Deviation 6: the local leg first ("MXN/SAME_DAY" identity for MXN; "MXN/PRIOR_BANKING_DAY" through threshold_in_ccy(Quantity(amount, "USD"), "MXN", on, PRIOR_BANKING_DAY) for USD);
    # then to_unit(local_amount, "MXN", unit, on, rule) per pair, except a pair whose unit equals the currency (identity, no unit_value_id);
    # a UnitValueMissingError → raise ParkTransactionError(UNIT_VALUE_MISSING, unit_code, needed_on=resolve_date(rule, on, holidays=holidays)) for the first missing pair in sorted order

# enrich/periods.py
def period_keys_for(occurred_at: datetime, *, holidays: Holidays) -> PeriodKeys  # Mexico City date; QUARTER f"{y}-Q{(m-1)//3+1}"; BUSINESS_DAY = is_business_day(day, holidays=holidays)

# enrich/service.py
class ParkTransactionError(Exception): reason: ParkReason; unit_code: str | None; needed_on: date | None
def enrich(session: Session, row: Transaction, *, units: UnitService, holidays: Holidays, relationship: RelationshipRef, snapshot: SnapshotRef) -> EnrichedTransaction
    # spec §7 steps 2 to 6: tier = relationship.account_tier; risk_rating None; monitoring_profile "STD"; unit_amounts_for over unit_pairs(snapshot.policy);
    # counterparty_country and counterparty_screening_status read from the counterparty row by id (screening.counterparty.resolve.status_of(session, id) -> tuple[bytes, str | None, CounterpartyScreeningStatus], the dedup_hash, country and screening_status, added here); enrich_input takes the stored country and status by dedup hash when the counterparty is stored (country_and_status_by_dedup_hash, added here), the input's country and UNSCREENED otherwise;
    # counterparty_key = dedup_hash.hex(); device_key = row.device_hash.hex(); policy_country = snapshot.policy.country; late as given on the row
def enrich_input(session: Session, item: TransactionInput, *, relationship: RelationshipRef, account_id: UUID | None, units: UnitService, holidays: Holidays, snapshot: SnapshotRef, now: datetime) -> EnrichedTransaction
    # the same steps over a validated input with no row (C5's decide path): id = uuid7(), received_at = now, late = False, the counterparty's key and status through
    # dedup_hash_of + status_by_dedup_hash (no upsert, no row); enrich(row, ...) is this function over the row's stored input plus the row's identity columns
def enriched_from_row(row: Transaction) -> EnrichedTransaction               # a PROCESSED row: everything from its columns (unit_amounts, period_keys, counterparty_* as stored); no lookups
def apply_enrichment(row: Transaction, txn: EnrichedTransaction) -> None     # writes unit_amounts, period_keys, counterparty_country, counterparty_screening_status, tier, risk_rating, monitoring_profile, policy_snapshot_id onto the row
```

`unit_amounts` JSON shape on the row: `{"UDI/SAME_DAY": {"amount": "1234.567890", "unit_value_id": 812, "resolved_date": "2026-12-10"}, …}` (strings for decimals). Goldens (`cases.yaml`, over `tests/golden/units/values.yaml` and the Mexico holidays, ITF for `EMONEY` and AVI for `CRYPTO`): `uma-change-31-jan-vs-1-feb` (two 25,000 MXN crypto operations, 2025-01-31 and 2025-02-01: `UMA/SAME_DAY` rows differ, 108.57 then 113.14), `fix-prior-banking-day-across-12-dec` (a USD 5,000 transfer on 2025-12-15 and one on 2025-12-12: both resolve `MXN/PRIOR_BANKING_DAY` to 2025-12-11, 91,250.00), `udi-last-day-prev-month-january` (10,000 MXN on 2026-01-20: `UDI/LAST_DAY_PREV_MONTH` 1173.708920 from 2025-12-31), `udi-missing-parks` (2026-02-05: `ParkTransactionError(UNIT_VALUE_MISSING, "UDI", 2026-01-31)`), `quarter-at-the-year-boundary` (2026-01-01T05:30:00Z → DAY 2025-12-31, MONTH 2025-12, QUARTER 2025-Q4; 2026-01-01T06:00:00Z → 2026-01-01, 2026-Q1), `business-day-flag` (2025-12-12 false, 2025-12-15 true). The golden test prints a unified diff of the expected and actual JSON on failure, as `tests/platform/test_units.py` does.

- [ ] **Step 1: Write the failing tests.** `test_enrich.py`:

```python
def test_unit_pairs_come_from_captures_tiers_and_the_library_pair(): ...       # ITF: {(USD, PRIOR_BANKING_DAY), (UDI, SAME_DAY), (UDI, LAST_DAY_PREV_MONTH)}; both packs add (UMA, SAME_DAY)
def test_a_mxn_transaction_records_the_local_leg_without_a_row(): ...
def test_a_usd_transaction_converts_through_the_fix_first(): ...               # Deviation 6; USD/PRIOR_BANKING_DAY is the identity
def test_the_snapshot_is_memoised_per_date_and_product(): ...                  # two rows same day and product → one snapshot_policy call (spy), one id
def test_no_policy_on_the_date_parks_with_policy_not_assigned(): ...           # a date before the assignment → PolicyNotAssignedError propagates (the job maps it, A8)
def test_the_counterparty_status_and_country_are_read_at_enrichment(): ...     # UNSCREENED; flipping the row to TRUE_MATCH is seen by the next enrich
def test_the_device_hash_is_keyed_per_tenant_and_the_ip_is_not_stored(): ...
def test_enriched_from_row_rebuilds_what_apply_enrichment_wrote(): ...         # enrich → apply_enrichment → enriched_from_row equals the EnrichedTransaction (late and received_at included)
def test_enrich_input_gives_the_same_unit_amounts_and_periods_as_enrich(): ...   # the decide path's enrichment of an item equals the row's (id, received_at and late aside); no counterparty row is written
```

`test_golden.py`: `test_enrichment_goldens(case)` parametrised over `cases.yaml` ids.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/enrich --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the four modules and `status_of` in `screening/counterparty/resolve.py`.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): enrichment with the policy snapshot, unit amounts per unit and rule, period keys and parking`.

---

### Task A7: Aggregates: declarations and `agg_id`, filters, bucket choice and windows, the `AggregateStore` port with the memory and Postgres adapters, exact distinct counts, declarations table and the contract test

Depends on: A2, A3 (runs beside A4 and A6). Console: no. Branch `sp04a-a7-aggregates`.

**Files:**
- Create: `src/xolotl/modules/monitoring/aggregates/declarations.py`, `windows.py`, `store.py`, `memory.py`, `postgres.py`
- Modify: `.importlinter` (the pure contract gains the four modules `declarations`, `windows`, `store`, `memory` and `xolotl.platform.policy.durations`), `tests/modules/monitoring/support.py` (+ `declare`, `stream`)
- Test: `tests/modules/monitoring/aggregates/__init__.py`, `test_declarations.py`, `test_windows.py`, `test_memory.py`, `test_postgres.py`, `test_contract.py`

**Interfaces:**

Consumes: A3 `EnrichedTransaction`, `KeyKind`, `Direction`, `Instrument`, `Rail`, `Channel`; A2 `AggBucket`, `AggDistinct`, `AggDeclaration`, `BucketKind`; `add_iso_duration` (`platform.policy.durations`); `blind_index`; `MEXICO_CITY`; `PostgresJobQueue.enqueue` (A4's `AGG_BACKFILL_KIND`); `insert(...).on_conflict_do_update`.

Produces:

```python
# aggregates/declarations.py (pure)
class AggregateFunction(StrEnum): SUM; COUNT; AVG; MAX; MIN; DISTINCT_COUNT; FIRST_SEEN; LAST_SEEN
class WindowKind(StrEnum): DURATION; MONTH; QUARTER; ACCUM
class Measure(StrEnum): AMOUNT_MINOR; UNIT_AMOUNT; FEE_MINOR; ASSET_QTY; ONE
@dataclass(frozen=True, slots=True)
class TxnFilter: direction: frozenset[Direction] = frozenset(); instrument: frozenset[Instrument] = frozenset(); rail: frozenset[Rail] = frozenset(); channel: frozenset[Channel] = frozenset()
    currency: frozenset[str] = frozenset(); counterparty_country_in: frozenset[str] = frozenset(); counterparty_country_not_in: frozenset[str] = frozenset()
    counterparty_screening_status: frozenset[str] = frozenset(); self_custody: bool | None = None; cross_border: bool | None = None
    geo_admin_area_in: frozenset[str] = frozenset(); amount_round_multiple: int | None = None
    amount_unit_key: str | None = None; amount_gte: Decimal | None = None; amount_lt: Decimal | None = None
@dataclass(frozen=True, slots=True)
class AggregateDeclaration: name: str; function: AggregateFunction; measure: Measure; unit_key: str | None; key_kind: KeyKind; window_kind: WindowKind; window: str
    where: TxnFilter = TxnFilter(); distinct_of: Literal["COUNTERPARTY", "DEVICE", "COUNTRY", "ACCOUNT"] | None = None; lists_hash: str | None = None   # Deviation 8
    def canonical_json(self) -> str            # sorted keys, sets as sorted lists, Decimals as strings, `name` excluded
    def agg_id(self) -> str                    # sha256(canonical_json)[:16]
    @classmethod
    def from_json(cls, name: str, data: Mapping[str, JsonValue]) -> AggregateDeclaration
    def validate(self) -> None                 # ValueError: UNIT_AMOUNT needs unit_key; DISTINCT_COUNT needs distinct_of; DURATION windows are ISO durations PT1H..P6M; ACCUM window is P6M; MONTH/QUARTER windows equal the kind
def matches(where: TxnFilter, txn: EnrichedTransaction) -> bool          # conjunctive; cross_border: counterparty_country not in (None, policy_country) or rail SWIFT; amount_round_multiple on amount_minor / 100; the band on txn.unit_amount(amount_unit_key)
def measure_of(declaration: AggregateDeclaration, txn: EnrichedTransaction) -> Decimal | None   # None when the row lacks the unit key, fee or asset_qty (the transaction then does not contribute)
def member_of(declaration: AggregateDeclaration, txn: EnrichedTransaction) -> str | None         # COUNTERPARTY → counterparty_key, DEVICE → device_key, COUNTRY → counterparty_country, ACCOUNT → str(account_id)

# aggregates/windows.py (pure)
HOUR_MAX = "P7D"; DAY_MAX = "P6M"
def bucket_kind_for(declaration: AggregateDeclaration) -> BucketKind       # DURATION ≤ P7D → HOUR; ≤ P6M → DAY; MONTH, QUARTER, ACCUM → themselves
def bucket_start_for(kind: BucketKind, at: datetime) -> datetime           # HOUR/DAY: UTC floor; MONTH/QUARTER: Mexico City month or quarter start, returned in UTC
def window_bounds(declaration: AggregateDeclaration, *, at: datetime) -> tuple[datetime, datetime]   # DURATION: (bucket_start_for(kind, at − window), bucket_start_for(kind, at)) inclusive of both bucket starts
def period_bucket_start(kind: BucketKind, period_key: str) -> datetime     # "2027-02" / "2027-Q1" → the Mexico City start in UTC
def accum_window(first_at: datetime, window: str) -> tuple[datetime, datetime]   # (Mexico City day start of first_at in UTC, that + window through add_iso_duration)

# aggregates/store.py
@dataclass(frozen=True, slots=True)
class AggregateKey: kind: KeyKind; key: str
@dataclass(frozen=True, slots=True)
class BucketRow: bucket_kind: BucketKind; bucket_start: datetime; bucket_end: datetime | None; value: Decimal; count: int; max_value: Decimal | None; min_value: Decimal | None; first_at: datetime | None; last_at: datetime | None; members: frozenset[bytes]
@dataclass(frozen=True, slots=True)
class AggregateValue: value: Decimal; count: int; max_value: Decimal | None; min_value: Decimal | None; first_at: datetime | None; last_at: datetime | None; distinct: int | None; complete: bool
    def of(self, function: AggregateFunction) -> Decimal | int | datetime | None   # SUM value, COUNT count, AVG value/count, MAX, MIN, DISTINCT_COUNT distinct, FIRST_SEEN first_at, LAST_SEEN last_at
EMPTY = AggregateValue(Decimal(0), 0, None, None, None, None, None, True)
class AggregateStore(Protocol):
    def add(self, txn: EnrichedTransaction, declarations: Sequence[AggregateDeclaration]) -> None: ...
    def read(self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime) -> AggregateValue: ...
    def calendar_read(self, key: AggregateKey, declaration: AggregateDeclaration, *, period_key: str) -> AggregateValue: ...
    def accumulator_read(self, key: AggregateKey, declaration: AggregateDeclaration, *, at: datetime) -> AggregateValue: ...

# aggregates/memory.py (pure)
class MemoryAggregateStore:            # implements AggregateStore; buckets in dicts keyed (kind, key, agg_id, bucket_start); distinct members as sets; complete is False for a declaration whose agg_id is in incomplete_ids, else True
    def __init__(self, *, member_hash: Callable[[str], bytes] = lambda m: hashlib.sha256(m.encode()).digest(), incomplete_ids: Collection[str] = ()) -> None   # incomplete_ids: C5 passes the agg_ids load_declarations reports with backfilled_through NULL, so the decide path reads INCOMPLETE as processing does
    def seed(self, key: AggregateKey, declaration: AggregateDeclaration, buckets: Iterable[BucketRow]) -> None    # C5's decide path seeds it from PostgresAggregateStore.buckets, then add()-s the candidate

# aggregates/postgres.py
class PostgresAggregateStore:          # implements AggregateStore over the session's tenant; member_hash = blind_index("agg_member", …)
    def __init__(self, session: Session, *, complete_ids: Collection[str] | None = None, queue: PostgresJobQueue | None = None) -> None   # None: completeness read from agg_declaration.backfilled_through per read; queue: enqueues the rebuild of an accumulator a late row cannot split (else AccumulatorSplitError)
    def buckets(self, key: AggregateKey, declaration: AggregateDeclaration, *, start: datetime, end: datetime) -> list[BucketRow]   # the decide path's range read (ix_agg_bucket_window)
def backfilled_ids(session: Session) -> frozenset[str]   # agg_ids whose backfilled_through IS NOT NULL
def load_declarations(session: Session) -> list[AggregateDeclaration]
def ensure_declarations(session: Session, declarations: Iterable[AggregateDeclaration], *, queue: PostgresJobQueue) -> list[str]
    # inserts missing agg_declaration rows (backfilled_through NULL) and enqueues AGG_BACKFILL_KIND key f"agg_backfill:{agg_id}" payload {"agg_id"}; returns the new ids
```

`add`: for each declaration whose `where` matches and `measure_of` is not None and `txn.key_for(key_kind)` is not None, upsert the bucket (`bucket_start_for(kind, occurred_at)`; `ACCUM`: the key's open accumulator whose `bucket_end > occurred_at`, else a new one from `accum_window(occurred_at, window)`, into which a later accumulator starting inside the new window is folded when its `last_at` is before the new end, spec §10.5) with `value += measure`, `count += 1`, `max_value`, `min_value`, `first_at = least`, `last_at = greatest`, `updated_at`; `DISTINCT_COUNT` also inserts the `agg_distinct` member `ON CONFLICT DO NOTHING`. `read`: the buckets with `bucket_start` in `window_bounds`, summed; `distinct = COUNT(DISTINCT member_hash)` over the same range; `complete` false when the declaration's `backfilled_through` is NULL. `calendar_read`: the one bucket at `period_bucket_start`. `accumulator_read`: the `ACCUM` bucket with `bucket_start <= at < bucket_end`, else `EMPTY`. The transaction under evaluation is included because `add()` runs before any read in the processing transaction (spec §8.5). `support.declare(session, declaration, *, queue) -> str` and `support.stream(n, *, seed) -> list[EnrichedTransaction]` (a deterministic synthetic stream over three parties, two accounts each, five counterparties, two devices, MXN and USD amounts, cash, transfers and crypto, hours apart, with `unit_amounts` for `USD/PRIOR_BANKING_DAY`, `UDI/SAME_DAY`, `UDI/LAST_DAY_PREV_MONTH`, `UMA/SAME_DAY`, `MXN/SAME_DAY` computed at a fixed rate).

- [ ] **Step 1: Write the failing tests.** `test_declarations.py`: `test_agg_id_ignores_the_name_and_is_order_independent` (same sets in another order → same id; a changed `where` → another), `test_lists_hash_changes_the_id`, `test_from_json_round_trips`, `test_validate_refuses_the_spec_violations` (parametrised), `test_matches_each_filter_field` (parametrised over the fourteen fields, `cross_border` with SWIFT and with a foreign counterparty), `test_measure_and_member_per_kind`. `test_windows.py`: `test_bucket_kind_by_window_length` (`PT1H`, `P7D` HOUR; `P8D`, `P6M` DAY; `MONTH`, `QUARTER`, `ACCUM`), `test_hour_and_day_buckets_are_utc_and_calendar_buckets_are_mexico_city` (2026-01-01T05:30Z → MONTH bucket 2025-12-01T06:00Z), `test_window_bounds_cover_exactly_the_window` (`P7D` at 2026-12-13T12:30Z → 2026-12-06T12:00Z..2026-12-13T12:00Z), `test_period_bucket_start_and_accum_window` (`2027-Q1`; P6M from 2026-07-14 → 2027-01-14 Mexico City). `test_memory.py`: `test_each_function_over_a_small_stream` (parametrised over the eight functions with hand-computed values), `test_a_transaction_outside_the_filter_does_not_contribute`, `test_accumulators_open_and_close_per_key`. `test_postgres.py` (`itf_tenant`): the same three tests against Postgres, plus `test_distinct_members_are_hmacs_not_values` (no counterparty key hex in `agg_distinct`), `test_reads_are_incomplete_until_backfilled_through_is_set`, `test_ensure_declarations_inserts_once_and_enqueues_a_backfill_per_new_id`, `test_buckets_returns_the_window_range_newest_first`, `test_another_tenant_reads_nothing`. `test_contract.py`: `test_postgres_and_memory_stores_agree_on_a_replayed_stream` (`stream(300, seed=4)` through both with eight declarations covering every function, window kind and key kind; `read`, `calendar_read` and `accumulator_read` at 20 instants equal, `complete` aside).
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/aggregates --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the five modules, the contract lines and the support helpers.
- [ ] **Step 4: Run to verify pass**, then `make importlint` and the gates.
- [ ] **Step 5: Commit** `feat(monitoring): aggregate declarations, windows and the AggregateStore port with Postgres and memory adapters`.

---

### Task A8: The `monitoring.process` job: the per-party drain in `occurred_at` order, one transaction per row, late rows, parking, the processing hook, the processed and parked events, `monitoring process`

Depends on: A4, A6, A7. Console: no. Branch `sp04a-a8-processing-job`.

Note from A7: build the store once per drain as `PostgresAggregateStore(s, complete_ids=backfilled_ids(s), queue=queue)` (`backfilled_ids(session) -> frozenset[str]` in `aggregates/postgres.py`: the `agg_id`s whose `backfilled_through` is set), instead of a `backfilled_through` lookup per read. Pass `queue`: a late row that opens an accumulator before a key's open one moves that window earlier (spec §10.5), and when the later accumulator already holds an operation past the new window's end the store sets `backfilled_through` back to NULL and enqueues `agg_backfill:<agg_id>`; a store without a queue raises `AccumulatorSplitError` (`aggregates/store.py`) there instead. Import `AGG_BACKFILL_KIND` from `monitoring.kinds` (A4) in `aggregates/postgres.py` and delete the copy A7 defines there.

Note from A8: `process_row` ships as `process_row(session, row, *, units, store, drain: DrainState) -> EnrichedTransaction` instead of the signature below. `DrainState` (`process/job.py`) carries what a drain keeps from row to row: `queue`, `holidays`, `cache` (the snapshots), `hooked` (each hook's declarations by snapshot id), `declarations` and `complete` (re-read each page), `latest` (the party's latest processed `occurred_at`, re-read each page, and in a row's transaction only when it had to wait for the party's advisory lock), and the `processed` and `parked` counts. The plan's eight keyword arguments exceed ruff's argument limit, and the gates allow no suppressions.

**Files:**
- Create: `src/xolotl/modules/monitoring/process/hooks.py`, `src/xolotl/modules/monitoring/process/job.py`
- Modify: `src/xolotl/modules/monitoring/composition.py`, `src/xolotl/cli.py` (`monitoring process`), `tests/modules/monitoring/support.py` (+ `run_processing`)
- Test: `tests/modules/monitoring/process/__init__.py`, `test_job.py`, `tests/platform/test_worker.py`, `tests/test_cli.py`

**Interfaces:**

Consumes: A2 models; A4 `relationship_ref`, `enqueue_processing`, `publish_monitoring_event`, `PROCESS_KIND`; A6 `snapshot_for`, `policy_date`, `enrich`, `apply_enrichment`, `ParkTransactionError`; A7 `PostgresAggregateStore`, `load_declarations`; `UnitService`, `load_holidays`; `PostgresJobQueue.heartbeat`; `Job`; `clock.now`; `get_settings()`.

Produces:

```python
# process/hooks.py
@dataclass(frozen=True, slots=True)
class ProcessingContext: txn: EnrichedTransaction; row: Transaction; store: AggregateStore; declarations: Sequence[AggregateDeclaration]; snapshot: SnapshotRef; units: UnitService; holidays: Holidays; late: bool; queue: PostgresJobQueue
class ProcessingHook(Protocol):                   # B7's RulesHook and B10's CapturesHook
    name: str
    def declarations(self, s: Session, snapshot: SnapshotRef, *, on: date, product: str | None) -> Sequence[AggregateDeclaration]: ...
    def evaluate(self, s: Session, txn: EnrichedTransaction, ctx: ProcessingContext) -> None: ...
def register_processing_hook(hook: ProcessingHook) -> None     # appended in order; a hook with the same name registered again replaces the first
def processing_hooks() -> tuple[ProcessingHook, ...]
def reset_processing_hooks() -> None              # tests only

# process/job.py
@dataclass(frozen=True, slots=True)
class ProcessReport: processed: int; parked: int; stopped_at: UUID | None     # stopped_at: the PARKED row that ended the drain
def latest_processed_at(session: Session, party_id: UUID) -> datetime | None
def pending_rows(session: Session, party_id: UUID, *, limit: int) -> list[Transaction]   # status IN (RECEIVED, PARKED) ordered by occurred_at, id (ix_transaction_pending)
def process_row(session: Session, row: Transaction, *, units: UnitService, holidays: Holidays, cache: SnapshotCache, declarations: Sequence[AggregateDeclaration], store: PostgresAggregateStore, latest: datetime | None) -> EnrichedTransaction
def park_row(session: Session, row: Transaction, *, reason: ParkReason, unit_code: str | None, needed_on: date | None, error: str) -> None
def process_party(session_factory: SessionFactory, tenant_id: UUID, party_id: UUID, *, queue: PostgresJobQueue, job: Job | None, settings: Settings) -> ProcessReport
class ProcessJob:
    def __init__(self, *, session_factory: SessionFactory, queue: PostgresJobQueue, settings: Settings) -> None
    def run(self, session: Session, job: Job) -> None     # process_party(payload["party_id"])
```

`process_party` (spec §8.5): pages of `pending_rows(limit=monitoring_process_page)`; for each row, one `tenant_session` (system context `job:<id>` or `cli:<user>`): a `PARKED` row stops the drain (`stopped_at`); `snapshot_for(on=policy_date(row.occurred_at), product=relationship.product, cache)` (a `PolicyNotAssignedError` → `park_row(POLICY_NOT_ASSIGNED)`, publish `TRANSACTION_PARKED` with `{"transaction_id", "reason", "unit_code": None, "needed_on": None}`, stop); `late = latest is not None and row.occurred_at < latest or row.received_at − row.occurred_at > P1D`, written to `row.late` before enrichment (so `EnrichedTransaction.late` carries it); `enrich` (a `ParkTransactionError` → `park_row(UNIT_VALUE_MISSING, unit_code, needed_on)` and the event, stop); `apply_enrichment`; `store.add(txn, declarations)`; every registered hook's `evaluate(s, txn, ProcessingContext(..., units=units, holidays=holidays, late=late, queue=queue))` in registration order; `status = PROCESSED`, `processed_at`, `late`; publish `TRANSACTION_PROCESSED` (entity `transaction`, key `str(party_id)`, data `{"transaction_id", "external_id", "status": "PROCESSED"}`); commit. `park_row` deletes the previous `transaction_parked` row of the transaction if any, inserts the new one (`attempts` carried over + 1, `parked_at = now`, `last_error` ≤ 200 characters), sets `status = PARKED`, commits in the same row transaction; the parked event's data is `{"transaction_id", "reason", "unit_code", "needed_on"}` (an ISO date string or null). The `declarations` of a row are `load_declarations(s)` plus, the first time a snapshot id is met in the drain, every hook's `declarations(s, snapshot, on=policy_date(row.occurred_at), product=relationship.product)`, with `ensure_declarations(s, new, queue=queue)` for the ones `agg_declaration` lacks (so a capture's `ACCUM` accumulator gets its row and its backfill the first time a snapshot is seen; a rule's are already there from B6's activation). After each page: `queue.heartbeat(job, lease=timedelta(seconds=monitoring_process_lease_seconds))` when `job` is given, `declarations` reloaded (a declaration activated mid-drain applies from the next page). The CLI `monitoring process --tenant <slug> [--party <id>]` drains the given party, or every party with a pending row, printing `party <id>: 12 processed, 1 parked (stopped at <transaction id>)` per party. `support.run_processing(services, tenant_id) -> int` runs `services.job_runner.run_once()` until no `monitoring.process` job is pending (bounded to 50 rounds). Composition registers `ProcessJob(...).run` for `PROCESS_KIND`.

- [ ] **Step 1: Write the failing tests** in `test_job.py` (`itf_tenant`, `customer`, `queue`, a second party `customer_b`; the stream helpers of A4 and A7; a test declaration `cash_sum_p7d` declared through `support.declare`):

```python
def test_a_drain_processes_a_party_in_occurred_at_order_and_updates_the_declared_buckets(): ...   # three rows ingested out of order → processed_at order == occurred_at order; cash_sum_p7d value == sum; snapshot id set
def test_each_row_is_its_own_transaction_and_a_failure_after_add_rolls_the_buckets_back_with_the_row(): ...   # Review Focus 2: a hook raising on row 2 → rows 1 PROCESSED, 2 RECEIVED with empty buckets for it, 3 untouched; a rerun processes 2 and 3 once (bucket count == 3)
def test_a_late_row_is_flagged_and_an_out_of_order_row_too(): ...                # received 2 days after occurred → late; a row older than the latest processed → late
def test_a_missing_unit_value_parks_the_row_and_stops_the_key(): ...              # UDI needed_on 2026-01-31 absent: row PARKED with reason, unit_code, needed_on; the rows behind stay RECEIVED; the parked event is staff-only and ids-only
def test_no_policy_on_the_date_parks_with_policy_not_assigned(): ...
def test_two_runners_never_process_one_party_at_once(): ...                       # two JobRunners in threads over 200 rows of one party: every row PROCESSED once, processed_at strictly increasing per party, buckets exact
def test_hooks_run_in_registration_order_inside_the_row_transaction(): ...       # a recording hook sees store reads that include the row (spec §8.5); its declarations are ensured once per snapshot and read back as INCOMPLETE until backfilled
def test_the_processed_event_carries_ids_and_status_only(): ...
def test_a_page_heartbeats_the_job(): ...                                         # spy on queue.heartbeat with monitoring_process_page 2 and 5 rows → ≥ 2 beats with the configured lease
```

`test_worker.py`: `job_handler(PROCESS_KIND).__self__` is a `ProcessJob`. `test_cli.py`: `test_monitoring_process_drains_a_party_and_reports_counts`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/process/test_job.py tests/test_cli.py -k "monitoring_process" --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the processing-hook registry, the job, the composition line and the CLI command.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): the per-party processing job with ordered drains, one transaction per row, parking, late flags and the processing hook`.

---

### Task A9: The sweep (enqueue race, un-parking, policy retries, the parked notification), the transaction backfill request and job, the aggregate backfill job, partition maintenance, schedules, `monitoring sweep`, `backfill` and `partitions ensure`

Depends on: A8. Console: no. Branch `sp04a-a9-sweep-backfill-partitions`.

Note from A5: a file can stay `PROCESSING` when `IngestFileJob._complete` fails on every attempt (the pages are committed, the job is out of retries), or when a run lost its lease and nothing claimed the job again. The sweep, or `GET /v1/monitoring/status` (A10), reports `transaction_file` rows that are `PROCESSING` with no live `ingest_file:<id>` job (`PENDING` or `RUNNING`).

Note from A5: `register_monitoring` (`src/xolotl/modules/monitoring/composition.py`) has no `notifier` parameter yet, because vulture flags an unused argument. A9 adds the parameter and the `notifier=` keyword at its call in `wire_platform` when the parked notification uses it.

Note from A7: the transaction backfill reprocesses rows more than `monitoring_late_days` late, the only rows that can make a late accumulator split; give its `PostgresAggregateStore` the `queue` (A8's note) so the split marks the declaration incomplete and enqueues its `agg_backfill` instead of raising `AccumulatorSplitError`.

Note from A7 (review): the `agg_backfill` job deletes the declaration's `agg_bucket` **and `agg_distinct`** rows before it walks (test both). If an `agg_backfill` job for the same `agg_id` is already PENDING or RUNNING, the queue's unique key drops the store's rebuild enqueue, and the running job could set `backfilled_through` over buckets a late row has split meanwhile: before setting `backfilled_through`, the job re-checks that it is still NULL-or-its-own run (or re-enqueues itself when a split happened during the walk).

Note from A8: `process_party` stops when `queue.heartbeat` is refused, assuming another run owns the job. When the reaper sent the job to `DEAD` instead (out of attempts), nothing owns it and the party's remaining `RECEIVED` rows are resumed only by this task's sweep enqueuing `process:<party_id>` for every party with a `RECEIVED` row (the enqueue race). The reviewer checks that the sweep covers that case, with a test. Second, the `agg_backfill` job must upsert buckets in a fixed key order within a page (or keep its pages short): a drain row upserts the party bucket and then the counterparty bucket of the same `agg_id`, and a backfill page that upserts across keys in `occurred_at` order can take them in the opposite order and deadlock. A9 tests a drain running concurrently with an `agg_backfill`.

**Files:**
- Create: `src/xolotl/modules/monitoring/process/sweep.py`, `src/xolotl/modules/monitoring/process/backfill.py`, `src/xolotl/modules/monitoring/process/partitions.py`, `src/xolotl/modules/monitoring/aggregates/backfill.py`
- Modify: `src/xolotl/modules/monitoring/errors.py`, `src/xolotl/modules/monitoring/composition.py`, `src/xolotl/modules/tenants/service.py` (`schedule_platform_jobs`), `src/xolotl/cli.py`, `README.md` (a "Monitoring" section: ingest-file for dev and demo, process, sweep, backfill, partitions)
- Test: `tests/modules/monitoring/process/test_sweep.py`, `test_backfill.py`, `test_partitions.py`, `tests/modules/monitoring/aggregates/test_backfill.py`, `tests/platform/test_worker.py` (`PLATFORM_SCHEDULES`), `tests/modules/tenants/test_kernel_events.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`

**Interfaces:**

Consumes: A8 `process_party`, `pending_rows`; A7 `PostgresAggregateStore`, `load_declarations`; A6 `enriched_from_row`; A4 `enqueue_processing`, the kind constants; A1 `ensure_all_partitions`; `current_row` (`platform.units.store`); `TenantPackAssignment`; `Notifier`, `Role`; `get_admin_engine`; `record`, `AuditEvent`.

Produces:

```python
# monitoring/errors.py (+)
class BackfillRangeInvalidError(ValidationFailedError): default_code = "BACKFILL_RANGE_INVALID"

# process/sweep.py
PARKED_TEMPLATE = "monitoring.parked"; RECEIVED_AGE = timedelta(seconds=30); PARKED_ALERT_AGE = timedelta(hours=6); POLICY_RETRY = timedelta(hours=1)
@dataclass(frozen=True, slots=True)
class SweepReport: enqueued: int; unparked: int; policy_retried: int; notified: bool
def sweep(session: Session, *, queue: PostgresJobQueue, notifier: Notifier, now: datetime) -> SweepReport
    # 1. enqueue_processing for every party with a RECEIVED row older than RECEIVED_AGE (the enqueue race of spec §8.5);
    # 2. PARKED rows with reason UNIT_VALUE_MISSING whose current_row(unit_code, needed_on) now exists → status RECEIVED, parked row deleted, party enqueued;
    # 3. PARKED rows with reason POLICY_NOT_ASSIGNED whose parked_at < now − POLICY_RETRY or a tenant_pack_assignment.created_at > parked_at (Deviation 10) → the same;
    # 4. when any row has been PARKED for over PARKED_ALERT_AGE and no PARKED_TEMPLATE notification exists since 00:00 Mexico City today: notify ADMIN and MLRO, data {"parked_count", "oldest_parked_at"} (Deviation 11)
class SweepJob: __init__(self, *, queue, notifier, clock_fn=clock.now); run(self, session, job)

# process/backfill.py
def request_backfill(session: Session, *, from_: date, to: date, party_id: UUID | None, queue: PostgresJobQueue) -> UUID
    # from_ > to or to > today → BackfillRangeInvalidError; audit monitoring.backfill_requested {"from", "to", "party_id"}; enqueue BACKFILL_KIND key f"backfill:{uuid7()}" payload {"from", "to", "party_id"}; returns the job id
class BackfillJob:  # run: BACKFILL rows with occurred_at in [from_, to] (and party_id when given) → status RECEIVED in pages of monitoring_process_page, then process_party for each party in turn (late is true for every one: they are out of order by construction); capture periods are B10's `CapturesHook`'s concern (spec §10.5)
    def __init__(self, *, session_factory, queue, settings) -> None

# aggregates/backfill.py
class AggBackfillJob:   # payload {"agg_id"}: walks PROCESSED rows with occurred_at >= now − P6M in (occurred_at, id) order, monitoring_agg_backfill_batch per page, each page one tenant_session,
                        # enriched_from_row → PostgresAggregateStore.add(txn, [declaration]); then backfilled_through = now; a declaration already backfilled is a no-op
    def __init__(self, *, session_factory, settings, clock_fn=clock.now) -> None

# process/partitions.py
class PartitionsJob:    # run: ensure_all_partitions on get_admin_engine() with monitoring_partition_months_ahead; logs created and skipped per table
    def __init__(self, *, months_ahead: int) -> None
```

Schedules (`modules/tenants/service.py`, literal kinds): `monitoring.sweep` on `get_settings().monitoring_sweep_cron` in UTC and `monitoring.partitions` on `monitoring_partitions_cron` in UTC; `PLATFORM_SCHEDULES` gains `("monitoring.partitions", "0 1 * * *", "UTC", "monitoring.partitions", True)` and `("monitoring.sweep", "* * * * *", "UTC", "monitoring.sweep", True)` in name order, `test_kernel_events.py` likewise. CLI: `monitoring sweep --tenant <slug>` prints `sweep: 2 enqueued, 1 unparked, 0 policy retries, notified`; `monitoring backfill --tenant <slug> --from --to [--party]` requests and runs the job synchronously, printing `backfill <job id>: 7 rows processed for 2 parties`; `monitoring partitions ensure` prints one line per table `transaction: created transaction_y2027m02; skipped none`.

- [ ] **Step 1: Write the failing tests.** `test_errors.py`: `(BackfillRangeInvalidError, 422, "BACKFILL_RANGE_INVALID")`. `test_sweep.py`:

```python
def test_a_received_row_older_than_thirty_seconds_gets_its_party_enqueued(): ...     # the dedup drops it when a job is PENDING already
def test_an_unparked_row_processes_before_the_rows_behind_it(): ...                   # Review Focus 3: park on UDI 2026-01-31; ingest two later rows; load the value; sweep; drain → order and buckets equal an unparked stream
def test_policy_not_assigned_rows_retry_hourly_or_when_a_pack_is_assigned(): ...
def test_parked_over_six_hours_notifies_admin_and_mlro_once_a_day(): ...             # two sweeps same day → 2 rows; next Mexico City day → 2 more
def test_the_sweep_job_runs_through_the_runner_on_its_schedule(): ...
```

`test_backfill.py` (process): `test_a_backfill_request_is_audited_validated_and_enqueued`, `test_the_backfill_job_reprocesses_backfill_rows_as_late`, `test_a_party_filter_limits_the_backfill`. `tests/modules/monitoring/aggregates/test_backfill.py`: `test_a_new_declaration_is_backfilled_from_six_months_of_processed_rows_and_becomes_complete`, `test_a_backfill_page_is_one_transaction_and_the_job_resumes` (the walk resumes after a crash without double-adding: `backfilled_through` null until the end, buckets rebuilt from scratch on retry because the job first deletes the declaration's buckets), `test_rows_processed_during_the_walk_are_not_added_twice` (a row processed by `monitoring.process` after the walk's page passed its `occurred_at` is counted once: the walk only reads rows `processed_at` before the job started). `test_partitions.py`: `test_the_partitions_job_keeps_every_registered_table_ahead`. `test_worker.py`/`test_kernel_events.py`: the pinned schedules; handlers wired. `test_cli.py`: `test_monitoring_sweep_reports_counts`, `test_monitoring_backfill_processes_the_range`, `test_monitoring_partitions_ensure_lists_tables`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/process tests/modules/monitoring/aggregates/test_backfill.py tests/platform/test_worker.py tests/modules/tenants/test_kernel_events.py tests/test_cli.py -k "monitoring" --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the four modules, the composition lines, the schedules, the CLI commands and the README section.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): the sweep, transaction and aggregate backfills, partition maintenance and their schedules`.

---

### Task A10: The transactions API for both audiences, the backfill and status endpoints, the counterparty views, `monitoring show`

Depends on: A5, A9. Console: no. Branch `sp04a-a10-transactions-api`.

Note from A2: add the ignore line `xolotl.modules.screening.counterparty.views -> xolotl.modules.screening.counterparty.models` to the `monitoring-boundaries` contract in `.importlinter` in this task.

Note from A9: the sweep keeps a row's `transaction_parked` entry when it sets the row `RECEIVED` again, so parking it again counts the attempt (Deviation 10). A `transaction_parked` row therefore does not mean the transaction is parked: `list_parked_transactions` filters `transaction.status = 'PARKED'` (joining the parked row for its reason, unit, date and attempts), `staff_view` joins the parked row only when the transaction's `status` is `PARKED` (else `parked` is None), and `MonitoringStatus.parked` counts `transaction.status = 'PARKED'`, not `transaction_parked` rows. A12's parked tab reads that list, so it needs nothing of its own.

Note from A5: `upload_file` adds and flushes the `transaction_file` row before it stores the sealed object, so two concurrent uploads of the same new file meet on the unique `(tenant_id, sha256)` index and the loser raises `IntegrityError` before writing anything. The upload endpoint maps that `IntegrityError` to the existing row (read it again in a fresh transaction) and answers `200` with it, as for a file uploaded earlier.

**Files:**
- Create: `src/xolotl/modules/monitoring/schemas.py`, `src/xolotl/modules/monitoring/routes_transactions.py`, `src/xolotl/modules/monitoring/routes_monitoring.py`, `src/xolotl/modules/monitoring/txn/queries.py`, `src/xolotl/modules/monitoring/txn/views.py`, `src/xolotl/modules/monitoring/process/status.py`, `src/xolotl/modules/screening/counterparty/schemas.py`, `src/xolotl/modules/screening/counterparty/views.py`
- Modify: `src/xolotl/app.py` (two routers after the screening routers), `src/xolotl/cli.py` (`monitoring show`)
- Test: `tests/modules/monitoring/txn/test_routes_transactions.py`, `tests/modules/monitoring/test_routes_monitoring.py`, `tests/modules/screening/counterparty/test_views.py`, `tests/platform/test_api_conventions.py` (passes unchanged), `tests/test_cli.py`

**Interfaces:**

Consumes: A3 `validate_batch`, `BatchRequest`; A4 `ingest_batch`, errors; A5 `upload_file`, `ACCEPTED_TYPES`; A9 `request_backfill`; `v1_router`, `require_access`, `SessionDep`, `PageDep`, `Page`, `paginate`; the `object_store` dependency pattern of `routes_documents.py`; `reveal_for_response`, `record`, `VIEW_PII`; `ActorType`; `get_settings()`; `mask_identifier`, `initials` (`party.masking`).

Produces (routes; endpoint function names are the operation ids):

| Function | Method and path | Access |
|---|---|---|
| `submit_transactions` | `POST /v1/transactions` → 202 `BatchAccepted` | `require_access(scope="transactions:write", write=True)`; staff writers |
| `upload_transaction_file` | `POST /v1/transactions/files` (raw body, Deviation 5) → 202 `FileStatusResponse`, 200 when the sha256 exists | same |
| `list_parked_transactions` | `GET /v1/transactions/parked` | staff (`require_access()`) |
| `list_backfill_transactions` | `GET /v1/transactions/backfill` | staff |
| `get_transaction_file` | `GET /v1/transactions/files/{file_id}` | `scope="transactions:read"` |
| `get_transaction_file_errors` | `GET /v1/transactions/files/{file_id}/errors` → `text/csv` | same |
| `get_transaction_batch` | `GET /v1/transactions/batches/{batch_id}` | same |
| `list_transactions` | `GET /v1/transactions` (filters `party_id`, `relationship_id`, `external_id`, `from`, `to`, `status`, `instrument`, `rail`, `late`; `paginate` on `(occurred_at, id)`) | same; client items are `TransactionClientView` |
| `get_transaction` | `GET /v1/transactions/{transaction_id}?reveal=` | same; a client gets `TransactionClientView`; staff `TransactionStaffView`; `reveal=true` by an API key is 403 |
| `request_backfill_endpoint` | `POST /v1/monitoring/backfill` → 202 `{job_id}` | `roles=(Role.MLRO, Role.ADMIN)` |
| `monitoring_status` | `GET /v1/monitoring/status` | `roles=(Role.ADMIN,)` |

```python
# monitoring/schemas.py
class BatchAccepted(BaseModel): batch_id: UUID; accepted: int; duplicates: int; transaction_ids: list[UUID]
class FileStatusResponse(BaseModel): file_id: UUID; status: FileStatus; rows_total: int | None; rows_accepted: int; rows_duplicate: int; rows_rejected: int; uploaded_at: datetime; finished_at: datetime | None; last_error: str | None
class BatchResponse(BaseModel): id: UUID; source: BatchSource; item_count: int; accepted: int; duplicates: int; received_at: datetime; request_id: str | None; file_id: UUID | None
class TransactionClientView(BaseModel): id: UUID; external_id: str; occurred_at: datetime; status: TransactionStatus; late: bool
class UnitAmountView(BaseModel): key: str; unit: str; rule: ConversionRule; amount: Decimal; unit_value_id: int | None; resolved_date: date
# screening/counterparty/schemas.py (A10 creates it; monitoring/schemas.py imports these two, never the reverse: screening does not import monitoring; C3 appends CounterpartyResponse and CounterpartyDetail)
class CounterpartySummary(BaseModel): id: UUID; kind: CounterpartyKind; has_name: bool; has_account: bool; has_wallet: bool; has_identifier: bool; institution: str | None; country: str | None; wallet_network: str | None; screening_status: CounterpartyScreeningStatus; txn_count: int
    # masking a value would need its plaintext, so the summary says only which components exist; the revealed view is the one path to characters of a value
class RevealedCounterparty(BaseModel): name: str | None; account: str | None; wallet_address: str | None; wallet_network: str | None; identifier_type: str | None; identifier: str | None
# monitoring/schemas.py (continued)
class TransactionStaffView(TransactionClientView): received_at; batch_id; party_id; relationship_id; account_id; account_ref: str; product; direction; rail; instrument; channel; amount_minor; currency; fee_minor; asset_qty; asset_code;
    unit_amounts: list[UnitAmountView]; period_keys: PeriodKeys | None; counterparty: CounterpartySummary | None; counterparty_screening_status_at_processing: CounterpartyScreeningStatus | None; device_present: bool; geo_country; geo_admin_area; crypto; fx; purpose_code; tier; risk_rating; monitoring_profile; policy_snapshot_id; processed_at; decision_id: UUID | None
    parked: ParkedInfo | None                        # ParkedInfo(reason, unit_code, needed_on, attempts, parked_at, last_error)
class TransactionRevealedView(TransactionStaffView): counterparty_revealed: RevealedCounterparty | None; raw: dict[str, JsonValue]
class BackfillRequest(BaseModel): from_: date = Field(alias="from"); to: date; party_id: UUID | None = None
class BackfillAccepted(BaseModel): job_id: UUID
class MonitoringStatus(BaseModel): received_by_age: dict[str, int]  # {"lt_1m", "lt_10m", "lt_1h", "gte_1h"}; parked: int; backfill: int; oldest_received_at: datetime | None; running_process_jobs: int; partitions_ahead: dict[str, int]

# txn/queries.py
@dataclass(frozen=True, slots=True)
class TransactionFilters: party_id; relationship_id; external_id; from_; to; status; instrument; rail; late
def transactions_query(filters: TransactionFilters) -> Select[Transaction]
# txn/views.py
def client_view(row: Transaction) -> TransactionClientView
def staff_view(session: Session, row: Transaction) -> TransactionStaffView            # joins account.external_ref, the parked row, counterparty_summary
def revealed_view(session: Session, row: Transaction) -> TransactionRevealedView      # one VIEW_PII per field revealed (entity "transaction", fields "raw" and "counterparty"); the counterparty's own VIEW_PII through reveal_counterparty
# screening/counterparty/views.py
def counterparty_summary(session: Session, counterparty_id: UUID) -> CounterpartySummary   # never decrypts: the has_* flags come from the hash columns
def reveal_counterparty(session: Session, counterparty_id: UUID) -> RevealedCounterparty  # one VIEW_PII row per non-null field (entity "counterparty", the field name) before any decryption; C3 reuses it
# process/status.py
def monitoring_status(session: Session, *, now: datetime) -> MonitoringStatus
```

`submit_transactions`: the body is `BatchRequest`'s raw shape read as `dict` (`transactions: list[dict]`); more than `monitoring_batch_max_items` → `BatchTooLargeError`; `validate_batch` → `BatchInvalidError(errors=...)` on any error; `ingest_batch(source=API, submitted_by=f"{actor_type.lower()}:{actor_id}", request_id=ctx.request_id)`. `upload_transaction_file`: `Content-Type` outside `ACCEPTED_TYPES` → `FileInvalidError`; `Content-Length` or body over the cap → `FileTooLargeError` before reading it all. `get_transaction_file_errors` streams the report object (404 until it exists). `monitoring_status`: counts over `transaction` and `job` (`kind = monitoring.process AND status = RUNNING`); `partitions_ahead` from `pg_class` for every registered table (months after this one). The CLI `monitoring show <transaction_id> --tenant <slug> [--reveal]` prints the staff view as key: value lines and, with `--reveal`, the decrypted counterparty and raw (audited as the API is).

- [ ] **Step 1: Write the failing tests.** `test_views.py`: `test_the_summary_never_decrypts`, `test_reveal_audits_each_field_then_decrypts` (one `VIEW_PII` row per non-null field; the audit rows precede the key read: a failing provider leaves them rolled back with the request), `test_another_tenants_counterparty_is_not_found`. `test_routes_transactions.py` (`client`, `admin_headers`, an analyst's and an auditor's headers through `tests/modules/party/route_support.py`, an API key with `transactions:write` and one with `transactions:read`; `itf_tenant`, `customer`):

```python
def test_an_api_key_submits_a_batch_and_gets_ids_back(): ...                           # 202; Idempotency-Key replays; the job exists
def test_a_batch_over_the_cap_is_413_and_an_invalid_one_422_with_field_errors(): ...   # errors[].loc == ["transactions", 1, "customer_ref"], type CUSTOMER_REF_UNKNOWN
def test_a_conflicting_resend_is_409_naming_the_index_only(): ...
def test_a_file_upload_is_202_and_the_same_bytes_again_200(): ...
def test_file_status_and_the_error_report_are_readable_by_both_audiences(): ...
def test_the_client_view_hides_everything_but_identity_and_status(): ...                # exact key set {id, external_id, occurred_at, status, late} after processing; no screening status, no amounts
def test_the_staff_view_shows_unit_amounts_periods_the_masked_counterparty_and_the_snapshot(): ...
def test_reveal_is_staff_only_and_audited(): ...                                         # ?reveal=true: analyst 200 with raw and the counterparty name, one VIEW_PII row for raw and one per revealed counterparty field; API key 403
def test_list_filters_and_cursors(): ...                                                 # party_id, from/to, status, instrument, late; next_cursor
def test_parked_and_backfill_lists_show_reasons(): ...
def test_tenant_b_sees_none_of_tenant_a(): ...
def test_literal_routes_win_over_the_id_route(): ...                                     # /transactions/parked is not a 404 "transaction parked"
```

`test_routes_monitoring.py`: `test_mlro_requests_a_backfill_and_it_is_audited`, `test_an_analyst_cannot_request_a_backfill`, `test_an_invalid_range_is_422`, `test_admin_reads_the_status_counters` (received by age, parked, backfill, oldest, running jobs, partitions ahead ≥ 3 for `transaction`), `test_only_admin_reads_the_status`. `test_cli.py`: `test_monitoring_show_prints_the_staff_view`, `test_monitoring_show_reveal_audits`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/txn/test_routes_transactions.py tests/modules/monitoring/test_routes_monitoring.py tests/modules/screening/counterparty/test_views.py tests/test_cli.py -k "monitoring_show" --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the schemas, queries, views, routers (literal routes registered before `/{transaction_id}`), the mounting and the CLI command.
- [ ] **Step 4: Run to verify pass**, plus `tests/platform/test_api_conventions.py` (operation ids unique, every route declares access, every POST idempotent), then the gates.
- [ ] **Step 5: Commit** `feat(monitoring): transactions API for API clients and staff, backfill and status endpoints, audited reveals`.

---

### Task A11: Console: Transacciones list and the transaction detail

Depends on: A10. Console: yes. Branch `sp04a-a11-console-transactions`.

**Files:**
- Create: `console/src/features/transactions/queries.ts`, `TransactionsPage.tsx`, `TransactionPage.tsx`, `TransactionStatusBadge.tsx`, `UnitAmountsCard.tsx`, `CounterpartyCard.tsx`, `RawPayloadCard.tsx`, `filters.ts`, `filters.test.ts`, `TransactionsPage.test.tsx`, `TransactionPage.test.tsx`, `fixtures.ts`
- Modify: `console/src/api/schema.d.ts` (regenerate), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `en.json`, `catalogue.test.ts`

**Interfaces:**

Consumes: the generated `TransactionClientView`, `TransactionStaffView`, `TransactionRevealedView`, `UnitAmountView`, `CounterpartySummary`, `TransactionStatus`, `Page_TransactionStaffView_`; `PageHeader`, `DataTable`, `Badge`, `KeyValues`, `Card`, `CursorPager`, `ErrorBlock`, `LoadingBlock`, `EmptyBlock`, `useFormat`, `useAuth`, `renderWithProviders`, `fakeApi`; the `RevealCard` pattern of `features/parties`.

Produces:

```ts
// auth/access.ts
transactions: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN'], backfill: ['MLRO', 'ADMIN']
// nav.ts: 'nav.transactions' { to: '/transactions', icon: ArrowLeftRight, roles: ACCESS.transactions } after nav.parties
// App.tsx: '/transactions' and '/transactions/:transactionId' behind RequireRole ACCESS.transactions
// filters.ts
export interface TransactionFilters { party_id?: string; external_id?: string; from?: string; to?: string; status?: TransactionStatus; instrument?: string; rail?: string; late?: boolean }
export function filtersToQuery(filters: TransactionFilters): Record<string, string | boolean>   // drops empty values; dates as ISO
export function filtersFromSearch(params: URLSearchParams): TransactionFilters
// queries.ts: useTransactions(filters, cursor), useTransaction(id), useRevealTransaction(id) (a mutation GET with reveal=true, invalidates nothing)
```

`TransactionsPage`: the filter bar (customer ref or party id, date range, status, instrument, rail, late switch), the table (external id mono, occurred at, customer, instrument and rail, amount with currency, status badge, late badge), a row links to `/transactions/:id`. `TransactionPage`: the canonical fields in `KeyValues`; `UnitAmountsCard` (one row per `unit_amounts` entry: unit, rule, amount, the `unit_value` id and resolved date); period keys; `CounterpartyCard` (kind, institution, country, the three "registrado" markers, the screening status badge, "Revelar" → `useRevealTransaction` and the `VIEW_PII` notice `transactions.detail.revealNotice`); the parked card when `parked` is set; `RawPayloadCard` (behind the same reveal, pretty JSON as text). Catalogue keys under `transactions.*` (`transactions.title`, `transactions.table.*`, `transactions.status.{RECEIVED,PROCESSED,PARKED,BACKFILL}`, `transactions.detail.*`, `transactions.parkReason.{UNIT_VALUE_MISSING,POLICY_NOT_ASSIGNED}`, `transactions.screeningStatus.{UNSCREENED,CLEAR,PENDING_REVIEW,TRUE_MATCH}`), `nav.transactions` "Transacciones"/"Transactions", `errors.TRANSACTION_BATCH_INVALID`, `errors.TRANSACTION_BATCH_TOO_LARGE`, `errors.EXTERNAL_ID_CONFLICT`, `errors.TRANSACTION_FILE_TOO_LARGE`, `errors.TRANSACTION_FILE_INVALID`, `errors.BACKFILL_RANGE_INVALID` in both catalogues and the code list. No string on these pages mentions a rule, a hit or a capture.

- [ ] **Step 1: Regenerate the API types and write the failing tests** (`gen:api:file` from `build_app().openapi()`, as plan 2b's B6). `filters.test.ts` (round trip, empties dropped). `access.test.ts`/`guards.test.tsx` for `transactions` and `backfill`. `TransactionsPage.test.tsx`: loading then rows with badges; filters change the query string sent; cursor navigation; empty state; an auditor sees the page. `TransactionPage.test.tsx`: the staff view renders every card; unit amounts list the unit value ids; the counterparty shows no name until "Revelar" and the reveal request carries `reveal=true`; after reveal the notice shows and the raw JSON renders as text (no markup injection); a parked row shows its reason; 404 renders `NotFound`.
- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck` and `npx vitest run src/features/transactions src/auth` → FAIL.
- [ ] **Step 3: Implement** following `features/lists` and `features/parties` for tables, cursors, queries and reveals.
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; `make ci`.
- [ ] **Step 5: Commit** `feat(console): Transacciones list and transaction detail with unit amounts, periods and audited reveals`.

---

### Task A12: Console: batches and files panels with the upload and template, the parked and backfill tabs with "Ejecutar backfill", and the Cliente › Transacciones card

Depends on: A10 (runs beside A11; both touch nav, access, App and the catalogues additively; `queries.ts` is created by whichever merges first and appended by the other). Console: yes. Branch `sp04a-a12-console-ingest-backfill`.

**Files:**
- Create: `console/public/plantilla-transacciones.csv`, `console/src/features/transactions/FilesCard.tsx`, `UploadDialog.tsx`, `BatchesCard.tsx`, `ParkedTab.tsx`, `BackfillTab.tsx`, `BackfillDialog.tsx`, `PartyTransactionsCard.tsx`, `upload.ts`, `upload.test.ts`, `FilesCard.test.tsx`, `ParkedTab.test.tsx`, `BackfillTab.test.tsx`, `PartyTransactionsCard.test.tsx`, `tests/modules/monitoring/txn/test_template_file.py`
- Modify: as A11 (`schema.d.ts` regenerated if A11 has not merged), `queries.ts` (+ `useTransactionFile`, `useUploadTransactionFile`, `useParkedTransactions`, `useBackfillTransactions`, `useRequestBackfill`), `TransactionsPage.tsx` (the tabs "Todas", "Estacionadas", "Pendientes de backfill" and the two panels), `console/src/features/parties/PartyPage.tsx` (the card for `ACCESS.transactions`), both catalogues

**Interfaces:**

Consumes: generated `FileStatusResponse`, `BatchResponse`, `BackfillRequest`, `BackfillAccepted`, `Problem`; `useIdempotentMutation`; the raw-body upload pattern of `features/parties/DocumentsCard.tsx` and `queries.ts`; `Tabs`, `Dialog`; `useAuth().hasRole`.

Produces:

```ts
// upload.ts
export const ACCEPTED = { 'text/csv': ['.csv'], 'application/x-ndjson': ['.jsonl'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] } as const
export const MAX_FILE_MB = 50
export function contentTypeFor(file: File): string | null       // by suffix when the browser gives none; null when unsupported
export function checkUpload(file: File): 'ok' | 'tooLarge' | 'unsupported'
// PartyTransactionsCard: props { partyId }; the last 20 transactions of the party (useTransactions({ party_id }, undefined)) with a link "Ver todas" to /transactions?party_id=
```

`FilesCard`: the upload dialog (file input accepting the three suffixes, client checks of type and size, a "Descargar plantilla" link to `/plantilla-transacciones.csv`, the POST with the file's content type and an `Idempotency-Key`), the uploaded file's status card polled every 5 s while `UPLOADED` or `PROCESSING` (counts, "Descargar errores" when `rows_rejected > 0`), and a 413 or 422 shown through the translated error. `BatchesCard`: the last batch returned by a submission in this session (id, counts) and the `GET /transactions/batches/{id}` lookup by id. `ParkedTab`: table of parked rows with reason, unit and date needed, attempts. `BackfillTab`: `BACKFILL` rows; the "Ejecutar backfill" button for `ACCESS.backfill` opens `BackfillDialog` (from, to, optional party id) → `POST /monitoring/backfill` with an `Idempotency-Key`, toast `transactions.backfill.requested`. `plantilla-transacciones.csv`: the header row `external_id,occurred_at,customer_ref,account_ref,direction,rail,instrument,amount,currency,channel,purpose_code,fee_amount,counterparty.kind,counterparty.name,counterparty.account,counterparty.institution,counterparty.country,counterparty.wallet_address,counterparty.wallet_network,crypto.network,crypto.tx_hash,crypto.self_custody,crypto.asset_code,crypto.asset_qty,fx.from_ccy,fx.to_ccy,fx.rate,fx.from_amount_minor,fx.to_amount_minor,device.device_id,device.geo_country,device.geo_admin_area`; `test_template_file.py::test_the_console_template_header_is_read_by_the_file_reader` pins it through A5's `read_header` (every column known, the required ones present). Catalogue keys `transactions.files.*`, `transactions.batches.*`, `transactions.parked.*`, `transactions.backfill.*`, `transactions.tabs.*`, `parties.transactions.*` ("Transacciones" card title, "Ver todas").

- [ ] **Step 1: Write the failing tests.** `test_template_file.py` as above. `upload.test.ts` (suffix fallback, size, unsupported). `FilesCard.test.tsx`: an upload sends the file bytes with its content type and an `Idempotency-Key`; a too-large file is refused before any request; a 422 shows the translated error; the status polls while processing and stops at `COMPLETED`; the errors link appears only with rejections. `ParkedTab.test.tsx`, `BackfillTab.test.tsx`: rows and reasons; the backfill button is absent for an analyst and present for the MLRO; the dialog posts `{from, to, party_id}` and shows the toast. `PartyTransactionsCard.test.tsx`: requests `/transactions?party_id=…`, renders rows, links to the list.
- [ ] **Step 2: Run to verify failure.** `cd console && npx vitest run src/features/transactions src/features/parties`; `uv run pytest tests/modules/monitoring/txn/test_template_file.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass.** The console gates and `make ci`.
- [ ] **Step 5: Commit** `feat(console): transaction file upload with template and error report, batches, parked and backfill tabs, the party transactions card`.

---

## Task table

| Id | Title | Depends on | Other plans | Console | Branch |
|---|---|---|---|---|---|
| A1 | `xolotl.platform.partitions`: the generic helper and registry; `audit_event` as its first user | — | — | no | `sp04a-a1-platform-partitions` |
| A3 | Canonical schema, validation, content hash, `EnrichedTransaction`, counterparty input types | — | — | no | `sp04a-a3-canonical-schema` |
| A2 | Migration 0012, models, enums, settings, contracts, factories | A1, A3 | — | no | `sp04a-a2-migration-0012` |
| A4 | Accounts, counterparty resolution, batch ingest, processing enqueue, batch event | A2, A3 | — | no | `sp04a-a4-batch-ingest` |
| A5 | File ingest: readers, sealed upload, paged job, error report, composition, `ingest-file` | A4 | — | no | `sp04a-a5-file-ingest` |
| A6 | Enrichment: snapshot, unit amounts, periods, parking, goldens | A2, A3 | — | no | `sp04a-a6-enrichment` |
| A7 | Aggregates: declarations, windows, port, both adapters, distinct counts, contract test | A2, A3 | — | no | `sp04a-a7-aggregates` |
| A8 | The processing job: ordered drain, one transaction per row, late, parking, processing hook, `process` | A4, A6, A7 | — | no | `sp04a-a8-processing-job` |
| A9 | Sweep, transaction and aggregate backfills, partition maintenance, schedules, CLI | A8 | — | no | `sp04a-a9-sweep-backfill-partitions` |
| A10 | Transactions API, backfill and status endpoints, counterparty views, `show` | A5, A9 | — | no | `sp04a-a10-transactions-api` |
| A11 | Console: Transacciones list and detail | A10 | — | yes | `sp04a-a11-console-transactions` |
| A12 | Console: files, batches, parked and backfill tabs, party card | A10 | — | yes | `sp04a-a12-console-ingest-backfill` |

## Parallelism

- **Wave 1: A1 ∥ A3** (no file in common). **A2 after both**; 4b's `0013_rules_captures` can be written once A2 merges (its FKs reference `transaction`, `counterparty`, `policy_snapshot`).
- **Wave 2 after A2: A4 ∥ A6 ∥ A7** (A6 and A7 share only `support.py` additions and the pure contract's `ignore_imports`; A4 creates `conftest.py`, `events.py`, `errors.py`, `kinds.py`, which neither A6 nor A7 edits; A6 adds `status_of` to A4's `resolve.py` — the second to merge rebases and re-applies its lines).
- **A5 after A4** (∥ A6, A7). **A8 after A4, A6, A7.** **A9 after A8.** **A10 after A5 and A9** (A10 is the only task that mounts routers). **A11 ∥ A12 after A10** (additive edits to `nav.ts`, `access.ts`, `App.tsx`, both catalogues, `catalogue.test.ts`, `queries.ts`; `schema.d.ts` is regenerated, never merged by hand).
- Longest chain: A1 → A2 → A4 → A8 → A9 → A10 → A11 (seven tasks); with three agents the plan fits its 1.5-week estimate.
- Shared files across plans (the SP-02 rule: edits are additive, the second to merge rebases and re-applies its lines): `src/xolotl/app.py`, `src/xolotl/cli.py` (`monitoring_app` is A5's; B3's `rules_app`, B11's `period-close`, C1's `screening screen-counterparties`, C6's `replay` and C8's `profiles` append), `src/xolotl/models.py`, `.importlinter`, `src/xolotl/platform/config.py`, `.env.example`, README, `src/xolotl/modules/monitoring/{errors,events,kinds,schemas,composition,routes_monitoring}.py` (created here; 4b and 4c append; the ownership table is reconciliation finding 17), `src/xolotl/modules/screening/counterparty/{__init__,schema,models,resolve,schemas,views}.py` (created here; B4 appends `CounterpartyNameIndex` to `models.py`, C1 the `on_counterparty_created` call to `resolve.py`, C3 its response models to `schemas.py`), `src/xolotl/modules/tenants/service.py` and the pinned schedule tests, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_errors.py`, `tests/platform/test_worker.py`, `tests/modules/monitoring/support.py` and `conftest.py`, the console nav, access map and catalogues.

## Exit criteria

Plan 4a's share of spec §19 and the 4a row of §22: with `make ci` green after every task, `tests/modules/monitoring/txn/*`, `enrich/*`, `aggregates/*` and `process/*`, `tests/golden/monitoring/enrichment/`, `tests/modules/screening/counterparty/*` (resolution, dedup, status cache, views), `tests/platform/test_partitions.py` and the isolation harness (every new tenant table and every `transaction` partition auto-discovered, forced RLS, no app-role privilege on a partition) pass; a tenant with `MX/ITF-IFPE` for `EMONEY` and `MX/LFPIORPI-AVI` for `CRYPTO` accepts a `POST /v1/transactions` batch of the week of 7 to 13 December 2026 fixtures shape (cash deposits, an international transfer, crypto operations with fees, one counterparty by name and one by wallet) with `202` and ids, accepts the same batch again with `accepted 0` and the same ids, refuses a changed item with `409 EXTERNAL_ID_CONFLICT` and refuses an unknown customer with `422 TRANSACTION_BATCH_INVALID` naming the index; `xolotl monitoring ingest-file` loads the same rows from a CSV, a JSON Lines file and an XLSX with the one bad row reported in `errors.csv` by row, column and code and no cell value anywhere; the worker's `monitoring.process` drains each party in `occurred_at` order, one database transaction per row, and `GET /v1/transactions/{id}` as staff shows UDI (`SAME_DAY` and `LAST_DAY_PREV_MONTH`), UMA, USD and MXN amounts with the `unit_value` rows and rules used, the period keys, the masked counterparty with status `UNSCREENED`, and the snapshot id, while the API-client view of the same transaction shows `{id, external_id, occurred_at, status: PROCESSED, late}` only; a transaction whose UDI value is missing parks with `UNIT_VALUE_MISSING` and the date needed, the rows behind it wait, and `xolotl units seed` followed by the sweep processes them in order; a crypto operation dated 10 December arriving on 12 December is `late = true` and re-fires nothing (there is nothing to fire); a transaction eight days old is `BACKFILL`, visible under "Pendientes de backfill", and `POST /v1/monitoring/backfill` as MLRO processes it; a declared `cash_sum_p7d` aggregate reads the same through `PostgresAggregateStore` and `MemoryAggregateStore` over the replayed stream, and a declaration added after the fact is `INCOMPLETE` until `monitoring.agg_backfill` walks the history; `transaction` has its partitions for this month and three ahead after `xolotl db upgrade` and after the daily job; `GET /v1/monitoring/status` reports no `RECEIVED` row older than a minute once the worker is idle; tenant B sees none of tenant A's transactions, accounts or counterparties; `GET /v1/audit/verify` is intact for both tenants; the console shows the Transacciones list, the detail with the audited reveal, the upload with the template and the error report, and the parked and backfill tabs to the roles that may see them. Spec §19's exit demo (`tests/e2e/test_sp04_exit.py`) is 4c's; this plan's `support.py` is what it builds on. The December 2026 unit values and the 12 December 2026 Banxico holiday the demo needs are not on `main` (Open question 3).

## Open questions for the product owner

1. **Currencies other than MXN and USD, and the rule for the peso leg.** Spec §7 converts "every transaction whose currency differs from MXN" under `SAME_DAY`, but `UnitService` converts from pesos only and the FIX series has no weekend value. This plan accepts `MXN` and `USD`, brings a USD amount to pesos at the prior banking day's FIX (Deviation 6), and refuses other currencies as a field error. Is that the launch rule, or should other currencies park until a rate source exists?
2. **`monitoring.transaction_processed.v1` to webhooks.** The spec wants it tenant-visible but opt-in per endpoint, a switch the webhook endpoints do not have, and lists the opt-in webhook last on the cut list. This plan registers it staff-only (Deviation 7). Confirm, or ask for the per-endpoint opt-in as a platform task.
3. **Fixture data for December 2026.** The exit demo (spec §19) processes the week of 7 to 13 December 2026 across the 12 December Banxico holiday with UDI, UMA and USD values; `policy-packs/_units/*.seed.yaml` cover December 2025 to January 2026 and `policy-packs/_holidays/MX.yaml` has 2025-12-12 marked `verify`. Either the demo fixtures move to the seeded window (December 2025) or someone verifies and adds the 2026 values and the 2026 holiday before 4c's exit test. This plan's goldens use the seeded window; B7, B10 and C11 carry their own December 2026 unit rows as test fixtures (reconciliation finding 19), and 12 December 2026 is a Saturday, so the weekend rule is what the demo crosses (4c Deviation 14).
4. **Status of the SP-04 spec.** It is a draft for review; this plan's Deviations 1 to 16 are what the review should accept or amend, and A2 (the migration) should not start before the schema of §5.2's first block is accepted.
