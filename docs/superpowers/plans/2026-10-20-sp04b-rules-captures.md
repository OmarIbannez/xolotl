# SP-04b — Rules, simulation, hits and captures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The working agreement `docs/superpowers/execution/sp04/agreement.md` (gates, worktrees, commits, PRs, report) applies to every task; the cross-plan decisions the three SP-04 plans carry are in `docs/superpowers/plans/2026-10-20-sp04-reconciliation.md`.

**Goal:** Turn the transactions plan 4a enriches and aggregates into rule hits and reportable events, under governance. That means: the jurisdiction lists loader; typed rule definitions with a rendered sentence; the pure evaluator for the seven ALERT kinds; the Mexico typology library as pack content with its loader and CLI; migration 0013 with rule templates, tenant rules, versions, simulations, assignments, hits, tier-limit notices, capture groups, reportable events, capture periods, the counterparty subject columns and `counterparty_name_index`; rule versions with parameter bounds, the D19 lifecycle with a different approver and date-and-product assignments; the simulation job that replays Postgres history through the same evaluator in memory and writes one `rule_simulation` row; hits with dedup, cooldown and evidence, and the asynchronous tier-limit webhook; capture definitions from the policy snapshot, trigger and field maps, accumulation groups, `merge_into`, supersession on late events and the period-close reconciliation; the rules, hits and captures API; staff-only event registration; and the console's Reglas › Monitoreo, Hallazgos de reglas and Reglas › Capturas y límites pages.

**Architecture:** Everything lives in `src/xolotl/modules/monitoring/` beside plan 4a's `txn/`, `enrich/`, `aggregates/` and `process/` packages (spec §2 layout). `rules/` holds the definitions, the pure evaluator (`kinds.py`), the typology loader, templates, versions, lifecycle, assignments, hits and the processing hook; `captures/` holds the capture definitions, trigger and field maps, reportable events, groups, supersession and periods; `simulation/` holds the replay engine and the job. The evaluator is a pure function over `(transaction, aggregates reader, profile, policy, lists, now)`: the processing job (4a) calls 4b's hooks inside its per-transaction database transaction with the Postgres store, and the simulation job calls the same evaluator with a `MemoryAggregateStore` per party, so simulation equals production by construction (spec Decision 7). Captures are evaluated from the transaction's policy snapshot and never from a tenant rule version (Decision 2); `TIER_LIMIT` is an ALERT template whose threshold comes from the snapshot (Decision 17). Writes run in the caller's transaction: rows, then audit, then ids-only events. `monitoring` imports `screening` service functions only, and `screening` never imports `monitoring` (Decision 18): the counterparty name index table is created here because 0013 is this plan's migration, and plan 4c fills it.

**Tech Stack:** the stack on `main` after plan 4a (SP-01 to SP-03 merged; migrations 0001 to 0011 on `main`, 0012 from 4a). No new runtime dependency: `pyyaml` reads the typology files, the trigger map and the field maps; `croniter` validates the period-close cron. No Postgres extension beyond `pg_trgm` (0009), which `counterparty_name_index` reuses.

**Spec:** `docs/superpowers/specs/2026-10-20-sp04-monitoring-captures-design.md`: §1 (the rules, hits, captures and simulation bullets), §1.1 (the second row), §2 (layout, the pure import contract, migrations), §3 (`MONITORING_RULE_CACHE_SECONDS`, `MONITORING_SIMULATION_MAX_DAYS`, `MONITORING_SIMULATION_MAX_AGE_DAYS`, `MONITORING_PERIOD_CLOSE_CRON`, `MONITORING_ALLOW_CUSTOM_RULES`), §5.1 (the shared template tables), §5.2 (the "Migration 0012 (plan 4b)" block, built here as 0013), §8.1, §8.2 and §8.4 (the store, declarations and `ensure_declarations` this plan consumes), §8.5 (where the evaluator runs), §9 (all of it), §10 (all of it), §12.2 (the asynchronous tier-limit path), §13.1 (simulation), §14 (the 4b events, the `rules.*` audit actions), §15 (the hits, captures, templates, rules, simulations and assignments rows), §16 (`rules …` and `monitoring period-close`), §17 (Hallazgos de reglas, Reglas › Monitoreo, Reglas › Capturas y límites), §18 rows FR-TM-004 to 007, 009 to 011, 014, 016, NFR-TM-004, DR-502, DR-504, DR-505, SEC-501, PRV-501, PRV-503, OPS-504, §19 (the `rules/*` and `captures/*` rows and the 4b share of the exit criteria), §20 Decisions 1, 2, 7, 8, 12, 13, 14, 16, 17, §21 Q1, Q2, Q10, §22 (the 4b row) and §23. Program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md`: D12 (pure evaluator), D13 (hits with evidence; captures per transaction), D19 (rule lifecycle), D20 (two audiences), D21 (accumulation from the first operation), D22 (the rule kinds).

## Accepted by the product owner

The spec is dated 2026-10-20 "for the product owner's review"; under the blanket approval of 2026-10-19 this plan builds the spec's defaults and does not reopen them:
- **Decisions of spec §20 that bind this plan:** 1 (parameterised kinds with `language: "KIND/1"`, no expression grammar), 2 (captures are policy, not tenant versions), 7 (simulation in memory, no temporary schema), 8 (`reportable_event` unique per transaction per capture; groups for accumulation), 12 (the library is pack content in shared template tables), 13 (rule assignment mirrors pack assignment), 14 (hits are not alerts: no disposition), 16 (period close reconciles by re-evaluation), 17 (`TIER_LIMIT` is an ALERT template).
- **Open-question defaults of spec §21 that bind this plan:** Q1 kinds only; Q2 custom rules off (`XOLOTL_MONITORING_ALLOW_CUSTOM_RULES = false`); Q10 events carry both `MONTH` and `QUARTER` keys and `capture_period` rows are quarterly for `ITF_ROR_CASH`.

## Dependencies outside this plan

Plans 4a and 4c were written in parallel with this one; `docs/superpowers/plans/2026-10-20-sp04-reconciliation.md` aligned the names below with what 4a and 4c build, and each row names the 4a task that provides it.

### From plan 4a

| Symbol | What this plan takes | Needed by |
|---|---|---|
| Migration `0012_transactions` (A2; `revision = "0012_transactions"`, `down_revision = "0011_screening_hit_run_index"`) | the tables `account`, `counterparty`, `transaction_batch`, `transaction_file`, `transaction` (partitioned), `transaction_ref`, `transaction_parked`, `agg_bucket`, `agg_distinct`, `agg_declaration` of spec §5.2; 0013 sets `down_revision = "0012_transactions"`. A2 also adds the `.importlinter` contracts `monitoring-boundaries` and `screening-never-imports-monitoring` and the factory helpers `_customer_relationship`, `_account`, `_counterparty`, `_transaction_batch`, `_transaction` | B4 |
| `xolotl.modules.monitoring.enums` (A2): `TransactionStatus` (`RECEIVED`, `PROCESSED`, `PARKED`, `BACKFILL`); `xolotl.modules.monitoring.txn.schema` (A3): `KeyKind` (`PARTY`, `RELATIONSHIP`, `ACCOUNT`, `COUNTERPARTY`, `DEVICE`) | the key kinds of declarations, dedup and hits; the status filter of evidence, simulation and period-close reads | B1, B7, B8, B10 |
| `xolotl.modules.monitoring.txn.schema` (A3): `Direction`, `Rail`, `Instrument`, `Channel`, `TransactionInput`, `EnrichedTransaction`, `UnitAmount`, `PeriodKeys` | `EnrichedTransaction` is the frozen, database-free value the evaluator reads: `id`, `external_id`, `occurred_at`, `received_at`, `party_id`, `relationship_id`, `account_id`, `product`, `direction`, `rail`, `instrument`, `channel`, `amount_minor`, `currency`, `fee_minor`, `asset_qty`, `asset_code`, `unit_amounts: dict[str, UnitAmount]` (`UnitAmount(amount: Decimal, unit_value_id: int \| None, resolved_date: date)` keyed `"<unit>/<rule>"`; `unit_amount(unit_key) -> Decimal \| None`), `counterparty_id`, `counterparty_key` (the dedup hash as hex), `counterparty_country`, `counterparty_screening_status`, `device_key` (the device hash as hex), `geo_country`, `geo_admin_area`, `crypto`, `fx`, `purpose_code`, `period_keys: PeriodKeys` (attributes `DAY`, `MONTH`, `QUARTER`, `BUSINESS_DAY`), `tier`, `risk_rating`, `monitoring_profile`, `policy_snapshot_id`, `policy_country`, `late`; `key_for(kind) -> str \| None` | B1, B2, B7, B8, B10 |
| `xolotl.modules.monitoring.txn.models` (A2): `Transaction`, `TransactionBatch`, `Account`; `xolotl.modules.screening.counterparty.models.Counterparty` (A2); `xolotl.modules.screening.counterparty.schema.CounterpartyInput`, `CounterpartyKind` (A3) | the rows hits, events and the name index reference; the evidence and simulation reads (`Transaction.status`, `.occurred_at`, `.party_id`, `.relationship_id`, `.policy_snapshot_id`) | B4, B7, B8, B10, B11 |
| `xolotl.modules.monitoring.aggregates.declarations` (A7, pure): `AggregateFunction`, `WindowKind`, `Measure`, `TxnFilter`, `AggregateDeclaration` (`agg_id()`, `canonical_json()`, `from_json()`, `lists_hash`), `matches(where, txn) -> bool`; `xolotl.modules.monitoring.aggregates.store` (A7, pure): `AggregateKey(kind, key)`, `BucketRow`, `AggregateValue` (`value`, `count`, `max_value`, `min_value`, `first_at`, `last_at`, `distinct`, `complete: bool`; `of(function)`); `xolotl.modules.monitoring.aggregates.postgres.ensure_declarations(session, declarations, *, queue) -> list[str]` | spec §8.2 and §8.4. A key is `AggregateKey(kind, txn.key_for(kind))` (no `aggregate_key` helper); `complete = False` is the `INCOMPLETE` of spec §8.4; the `on` filter of a rule and a capture is a `TxnFilter` evaluated by the same `matches` that `add()` uses. List names (`$FATF_GREY`) are this plan's: `resolve_list_names` and `resolve_definition` (B1) rewrite a `TxnFilter` to literal codes, and B6 applies them to every `on`, `evidence.transactions_where` and declaration `where` of a loaded rule, setting `lists_hash`, before anything reaches 4a | B1, B2, B6, B7, B10 |
| `xolotl.modules.monitoring.aggregates.windows` (A7, pure): `window_bounds`, `period_bucket_start`, `accum_window`; `aggregates.store.AggregateStore` (the §8.1 Protocol: `add`, `read`, `calendar_read`, `accumulator_read`); `aggregates.postgres.PostgresAggregateStore(session)`; `aggregates.memory.MemoryAggregateStore()` | the reader wraps a store; simulation and period close build a memory store per key | B7, B8, B10, B11 |
| `xolotl.modules.monitoring.process.hooks` (A8): `ProcessingContext(txn, row, store, declarations, snapshot: SnapshotRef, units: UnitService, holidays: Holidays, late: bool, queue: PostgresJobQueue)`, `ProcessingHook` (a Protocol with `name: str`, `declarations(self, s, snapshot: SnapshotRef, *, on: date, product: str \| None) -> Sequence[AggregateDeclaration]` and `evaluate(self, s, txn: EnrichedTransaction, ctx: ProcessingContext) -> None`), `register_processing_hook(hook)`; `xolotl.modules.monitoring.enrich.policy.SnapshotRef(snapshot_id, policy: EffectivePolicy)` (A6) | spec §8.5 step 2: the job asks every hook for its declarations once per snapshot it meets in a drain (`ensure_declarations` for the ones `agg_declaration` lacks) and calls every hook's `evaluate` after `add()`, in the row's transaction, before publishing `monitoring.transaction_processed.v1` | B7, B10 |
| `xolotl.modules.monitoring.enrich.service.enriched_from_row(row: Transaction) -> EnrichedTransaction` (A6; no session), `xolotl.modules.monitoring.enrich.policy.policy_date(at: datetime) -> date` (A6; the Mexico City calendar date), `xolotl.modules.monitoring.enrich.units.unit_amounts_for(units, *, amount, currency, on, pairs, holidays) -> dict[str, UnitAmount]` (A6; the one conversion path, 4a Deviation 6) | rebuilding the evaluator's input from stored rows (simulation, late re-evaluation, period close), the calendar date, and the fee conversion of a `FEE_THRESHOLD` capture (Deviation 8) | B7, B8, B10, B11 |
| `xolotl.modules.monitoring.events` (A4): `EVENT_SOURCE = "xolotl.monitoring"`, `STAFF_ONLY_EVENT_TYPES: tuple[str, ...]` (4a's; not edited here), `publish_monitoring_event(s, event_type, *, entity, entity_id, key: str, data, policy_snapshot_id=None) -> Event` (the `publish_screening_event` shape) | this plan appends its constants and its own `RULES_STAFF_ONLY_EVENT_TYPES` tuple, which `register_rules` registers | B4 |
| `xolotl.modules.monitoring.errors` (A4), `schemas.py` (A10), `composition.py` (A5; `register_monitoring(*, session_factory, settings, queue, store, notifier)`), `routes_transactions.py` (A10), `kinds.py` (A4; `PROCESS_KIND = "monitoring.process"`) | shared files this plan appends to; `register_rules(*, queue)` is called by `wire_platform` right after `register_monitoring` | B4, B7, B9 |
| `tests/modules/monitoring/support.py` (A2, A4, A7, A8): `itf_tenant`, `both_packs_tenant`, `make_item(**overrides) -> dict`, `ingest_items(session, items, *, queue) -> BatchReport`, `run_processing(services, tenant_id) -> int`, `declare`, `stream`; `tests/platform/units_support.py::load_unit_values(session, path)` (on `main`) | the capture goldens, the hit tests and the hook tests run real rows through 4a's job. B7 commits `tests/modules/monitoring/fixtures/unit_values_2026.yaml` (UDI, UMA and `USD_MXN_FIX` for July to December 2026, with the 11 December FIX and no row for 12 and 13 December) and loads it with `load_unit_values`; C11 reuses the file | B7, B10, B11 |
| `Settings.monitoring_agg_backfill_batch` (A2; `XOLOTL_MONITORING_AGG_BACKFILL_BATCH`, default 5000), `Settings.monitoring_process_page` (A2) | the simulation page size (spec §13.1) | B8 |
| `tests/isolation/factories.py` helpers (A2) `_transaction(session, tenant_id) -> Transaction`, `_counterparty(session, tenant_id) -> Counterparty`, `_customer_relationship(session, tenant_id) -> CustomerRelationship` | the hit, event and name-index factories | B4 |
| `xolotl.modules.screening.counterparty.views.reveal_counterparty(s, counterparty_id) -> RevealedCounterparty` (A10; `name`, `account`, `wallet_address`, `wallet_network`, `identifier_type`, `identifier`; one `VIEW_PII` row per non-null field) and `counterparty_summary(s, counterparty_id) -> CounterpartySummary` (A10; masked: the `has_*` flags, kind, institution, country, network, status, count) | the hit reveal and the masked counterparty of an evidence transaction | B12 |
| Console (A11, A12): `console/src/features/transactions/` (`TransactionPage.tsx`, `queries.ts`), `ACCESS.transactions`; `features/parties/PartyPage.tsx` (SP-02c; A12 adds its `PartyTransactionsCard`) | B14 adds `TransactionHitsCard` to the transaction detail and `PartyHitsCard` to the party page; B15 adds `TransactionCapturesCard` to the transaction detail (additive edits of 4a's files; the "hits and captures on this transaction" of spec §17) | B14, B15 |

### On `main` already

`effective_policy`, `snapshot_policy`, `get_snapshot`, `PolicySnapshotContent`, `EffectivePolicy` (`kyc.tiers[].limits.monthly_ops`, `units.tier_limit_rule`, `captures[]`, `country`), `Quantity`, `Capture`, `add_iso_duration`, `iso_duration_days` (`xolotl.platform.policy`); `UnitService.to_unit`, `threshold_in_ccy`, `Conversion`, `ConversionRule` (`xolotl.platform.units`; conversions are MXN-only, `_check_currency`); `load_holidays`, `MEXICO_CITY` (`xolotl.platform.calendar`); `canonical_json`, `record`, `AuditEvent`, `VIEW_PII` (`xolotl.platform.audit`); `register_staff_only`, `register_subscriber`, `publish` (`xolotl.platform.events`); `PostgresJobQueue.enqueue`/`schedule`, `register_job_handler`, `Job` (`xolotl.platform.jobs.queue`); `v1_router`, `require_access`, `paginate`, `Page`, `PageDep`, `SessionDep`; `XolotlError.blocked_reasons`, `ErrorItem`, `XolotlError.errors`; `enable_rls`, `grant_shared`, `tenant_fk`, `tenant_table_args`, `TenantTableMixin`, `enum_column`; `get_party`, `reveal_pii`, `summaries`, `CustomerRelationship` (`party_id`, `product`, `account_tier`, `started_at`), `PepStatus`; `xolotl.modules.screening.api.party_view(s, party_id, staff=True).status` (the party's screening status without side effects); `ScreeningSubject`, `SubjectStatus`; `install_both_packs`, `both_packs` (`tests/platform/policy_support.py`); `everything_stored`, `leaked`, `new_tenant`, `staff_session`, `system_ctx` (`tests/helpers.py`); `report-defs/codetables/MX/f41_tipo_operacion.yaml`, `f41_instrumento_monetario.yaml`.

### From plan 4c

Nothing is consumed. 4c consumes this plan (see "Interfaces for plan 4c"). `customer_relationship.declared_profile` is C4's column (migration 0014) and C8 makes B7's `profile_for` read it into B2's `DeclaredProfile`; until then `profile_for` returns `declared_profile=None` and `PROFILE_DEVIATION` stays silent (spec Decision 10). C5 reads `active_rules`, `RuleSet.sync_rules`, `RuleSet.ruleset_hash()`, `reader_for`, `profile_for`, `evaluate` and `notice_tier_limit(..., source=NoticeSource.DECIDE)`, and appends `decision: DecisionSummary | None` to B12's `HitDetail`; C6 reads `replay_rule_set`; C7 deletes this plan's rows in the order of "Interfaces for plan 4c".

## Global Constraints

- **Migration numbering.** The chain on `main` ends at `0011_screening_hit_run_index` (merged 2026-10-20), so SP-04's migrations are renumbered: 4a's is `0012_transactions`, this plan's is **`0013_rules_captures`**, 4c's is `0014_decide_profiles`. Wherever the spec writes "migration 0011", "0012" and "0013" (spec §2, §5.2, §11.1, §12.1, §22), read 0012, 0013 and 0014. This plan uses 0013 throughout.
- Repository `/Users/omaribannez/code/xolotl`; every task is one PR from `main` on branch `feat/sp04b-b<n>-<short>` in its own worktree; `make ci` green at the end of every task; the console gates (`npm ci && npm run typecheck && npm run lint && npm test && npm run build`, plus the Playwright smoke) whenever `console/` changes.
- **Migration** `src/xolotl/migrations/versions/0013_rules_captures.py`: `revision = "0013_rules_captures"`, `down_revision = "0012_transactions"` (A2's revision; the three SP-04 migrations merge in the order A2 → B4 → C4). It is this plan's only migration. `"0013_rules_captures"` is appended to `CHAIN` in `tests/platform/test_migration_chain.py` after 4a's `"0012_transactions"`.
- **Two shared tables** (`rule_template`, `rule_template_version`, spec §5.1) join `SHARED_TABLES` and not `SHARED_WRITABLE`: the owner role seeds them (`xolotl rules load`), the app role gets `SELECT` through `grant_shared`. **Eleven tenant tables** exactly as the 4b block of spec §5.2: `monitoring_rule`, `monitoring_rule_version`, `rule_simulation`, `rule_assignment`, `rule_hit`, `rule_hit_transaction`, `capture_group`, `reportable_event`, `tier_limit_notice`, `capture_period`, `counterparty_name_index`. Each is `class X(TenantTableMixin, Base)` with `tenant_table_args`, composite FKs through `tenant_fk` or a named `ForeignKeyConstraint` where the convention would pass 63 bytes, `enable_rls("<table>")` in the migration (default grant), one isolation factory in `register_all()`, and its model imported in `src/xolotl/models.py`. A link to a transaction carries `(tenant_id, occurred_at, transaction_id)` with the composite FK to the partitioned primary key (spec §5.2). Nothing in 0013 is append-only.
- **No encrypted column in 0013.** `rule_hit.evidence`, `reportable_event.fields`, `rule_simulation.sample` and `counterparty_name_index` carry amounts, units, codes, hashes, ids and normalized tokens only (PRV-501, D7); no route serializes `counterparty_name_index`.
- **Import contracts** (`.importlinter`, B1 adds the contract `monitoring-evaluator-is-pure`): the pure set `xolotl.modules.monitoring.rules.definitions`, `xolotl.modules.monitoring.rules.kinds`, `xolotl.modules.monitoring.rules.render`, `xolotl.modules.monitoring.rules.library` and `xolotl.modules.monitoring.captures.definitions` never imports `sqlalchemy`, `fastapi`, `xolotl.modules.screening`, `xolotl.modules.party.models`, `xolotl.modules.party.service` or any `xolotl.platform` package other than `xolotl.platform.errors`, `xolotl.platform.enums`, `xolotl.platform.units.rules`, `xolotl.platform.units.quantity`, `xolotl.platform.policy.schema`, `xolotl.platform.policy.resolver` (the `EffectivePolicy` type), `xolotl.platform.policy.jurisdictions` and `xolotl.platform.policy.durations` (`check_iso_duration`, `iso_duration_days`; B1); among monitoring modules it imports only `monitoring.enums`, `monitoring.txn.schema`, `monitoring.aggregates.declarations`, `monitoring.aggregates.windows` and `monitoring.aggregates.store` (4a's pure modules, A3 and A7). Because the contract forbids `xolotl.modules.screening` and import-linter follows indirect imports, it also ignores the edge `xolotl.modules.monitoring.txn.schema -> xolotl.modules.screening.counterparty.schema` (4a's transaction schema takes the counterparty input types from screening's pure schema module; B1). `xolotl.modules.monitoring` never imports `xolotl.modules.reporting` and `xolotl.modules.screening` never imports `xolotl.modules.monitoring` (A2's contracts `monitoring-boundaries` and `screening-never-imports-monitoring`; this plan adds neither).
- **Settings** (`src/xolotl/platform/config.py`, prefix `XOLOTL_`): `monitoring_allow_custom_rules: bool = False` (B5), `monitoring_rule_cache_seconds: int = Field(default=30, ge=0)` and `monitoring_simulation_max_age_days: int = Field(default=30, ge=1)` (B6), `monitoring_simulation_max_days: int = Field(default=190, ge=1)` (B8), `monitoring_period_close_cron: str = "0 4 1 * *"` added to the `_check_cron` validator (B11). Each goes in `.env.example`, the README configuration table and the `model_fields_set` assertion of `tests/platform/test_config.py::test_env_example_is_a_valid_configuration`.
- **Problem codes** introduced, each with a row in `tests/platform/test_errors.py`: `RULE_TEMPLATE_UNKNOWN` (404, B5), `RULE_TEMPLATE_IMMUTABLE` (409, B5; Deviation 2), `RULE_PARAM_OUT_OF_BOUNDS` (422 with `errors[]`, B5), `RULE_TRANSITION_NOT_ALLOWED` (409 with `blocked_reasons`, B6), `SIMULATION_RANGE_TOO_LONG` (422, B8), `PERIOD_NOT_ENDED` (409, B11). Reused: `NOT_FOUND`, `VALIDATION_FAILED`, `POLICY_NOT_ASSIGNED`, `ROLE_FORBIDDEN`, `SCOPE_MISSING`, `IDEMPOTENCY_*`.
- **Events** (`source = "xolotl.monitoring"`, ids and codes only, exactly the fields of spec §14): staff-only `monitoring.rule_hit_created.v1` (key `str(party_id)`), `monitoring.reportable_event_captured.v1`, `monitoring.reportable_event_superseded.v1`, `monitoring.capture_period_closed.v1`, `monitoring.capture_period_changed.v1` (key `str(party_id)` for events, the period id for periods), `monitoring.rule_version_activated.v1`, `monitoring.rule_version_retired.v1`, `monitoring.simulation_completed.v1` (key `str(rule_version_id)`); webhook-safe `monitoring.tier_limit_reached.v1` (key `str(relationship_id)`, data `relationship_id`, `account_id`, `tier`, `limit_code`, `period_key`, `outcome`, never an amount). The eight staff-only types are B4's `RULES_STAFF_ONLY_EVENT_TYPES` (a separate tuple; 4a's `STAFF_ONLY_EVENT_TYPES` is not edited) and `register_rules` (B7) marks each with `register_staff_only`.
- **Audit actions** (entity type is the table name, ids as `str(uuid)`, codes only in `detail`): `rules.seeded`, `rules.version_created`, `rules.version_updated`, `rules.version_submitted`, `rules.version_approved`, `rules.version_rejected`, `rules.version_activated` (detail `{"assignment_id", "effective_from", "product", "category": "CHANGE_POLICY"}`, Deviation 4), `rules.version_retired`, `rules.simulation_requested`, and `VIEW_PII` from the hit reveal only. `record(...)` comes last in each service function, before `publish`.
- **API**: routers from `v1_router` only; every `POST`/`PATCH` needs `Idempotency-Key`; API clients use the new scope `rules:read` on templates, rules and simulations; hits, captures, periods and every rule write are staff-only (no scope); lists paginate with `paginate(...)`; literal routes (`/monitoring/captures/periods`, `/monitoring/templates`) are registered before their `/{id}` siblings; handler names are unique operation ids. An API client never receives a hit, a capture, a rule name of a hit or a counterparty's screening status (D20, PRV-502).
- **Dates**: the calendar date of a transaction is 4a's `policy_date(occurred_at)` (`monitoring.enrich.policy`, America/Mexico_City); "today" for lifecycle and periods is `policy_date(clock.now())`; dedup windows floor in UTC for ISO durations and use `period_keys.MONTH` for `MONTH`.
- **Job kinds and keys**: `monitoring.simulate` (`SIMULATE_KIND`, key `simulate:<simulation_id>`, payload `{"simulation_id"}`, `max_attempts=1`), `monitoring.period_close` (`PERIOD_CLOSE_KIND`, payload `{}` from the schedule or `{"capture_code", "period_key"}` from the CLI and the manual close). Schedule `monitoring.period_close` per tenant on `XOLOTL_MONITORING_PERIOD_CLOSE_CRON` in `America/Mexico_City`, declared in `schedule_platform_jobs` through the literal `MONITORING_PERIOD_CLOSE_SCHEDULE`.
- **Console**: Spanish-first keyed strings in both catalogues; `schema.d.ts` regenerated against the merged API in every console task, never merged by hand; revealed values only through `useMutation` with `gcTime: 0`; no string visible to a customer mentions a rule, a hit, a capture or a suspicion (DD-00). Flat navigation (SP-03a Deviation 11): `nav.ruleHits`, `nav.monitoringRules`, `nav.captures` are three entries after `nav.privateLists`.
- MVP footprint: one container plus Postgres; no new service, cache or worker pool. The rule-set cache is a per-process dictionary.
- **Commit trailers**: every commit ends with exactly the `Co-Authored-By` line the task prompt gives.

## Deviations from the sub-spec (decided here)

1. **The pure set imports 4a's pure modules.** Spec §2 lists what `monitoring.rules.kinds` may import and names neither `monitoring.aggregates.declarations` (`TxnFilter`, `AggregateDeclaration`, `matches`) nor `monitoring.aggregates.store` (`AggregateKey`, `AggregateValue`) nor `monitoring.aggregates.windows`, yet a definition declares aggregates with those types and the evaluator applies the `on` filter. The contract therefore treats 4a's three pure aggregate modules and this plan's pure modules as one pure set (Global Constraints). Nothing else changes: no `sqlalchemy`, no `fastapi`, no other module.
2. **`RULE_TEMPLATE_IMMUTABLE`.** Spec §5.1 says a changed hash for an existing template version "is an error" without a code; `xolotl rules load` raises `RuleTemplateImmutableError` (409, `RULE_TEMPLATE_IMMUTABLE`) naming the code and version, and loads nothing of that file. `rules load` is not audited per tenant: like list ingestion (SP-03a), shared seeding is not tenant activity and `rule_template_version.loaded_by`, `loaded_at` and `source_path` are its provenance; `rules.seeded` (tenant) is audited.
3. **`NOT_AUTHOR` is a blocked reason.** Spec §9.4 lets only the author submit, but its reason list has no reason for someone else trying. `RuleBlockedReason` gains `NOT_AUTHOR`, reported in the order `STATE`, `ROLE_REQUIRED`, `NOT_AUTHOR`, `SAME_AUTHOR`, `SIMULATION_MISSING`, `SIMULATION_STALE`, `PARAM_OUT_OF_BOUNDS`.
4. **`CHANGE_POLICY` is a detail, not an action.** Spec §14 writes `rules.version_activated (also CHANGE_POLICY)`; no `CHANGE_POLICY` audit action exists on `main`. The activation audit row is `rules.version_activated` with `detail.category = "CHANGE_POLICY"`, so a filter on the category finds it.
5. **Assignments resolve by date, versions by status.** At activation the new `rule_assignment` starts at `effective_from` (must be ≥ today, else 422 `VALIDATION_FAILED`); the rule's current `ACTIVE` version becomes `RETIRED` at once (the partial unique index allows one `ACTIVE` per rule) and its assignment gets `effective_to = effective_from − 1 day`. `active_rules(on, product)` reads versions in status `ACTIVE` or `RETIRED` whose assignment is in force on `on`, so a rule keeps applying until its successor starts and "which rules applied on this date" is answered from the assignment rows (spec Decision 13). Retire sets `effective_to = today`.
6. **`POST /v1/monitoring/rules` for an existing code creates a version.** Spec §15 has one route to create a rule from a template and another to add a version; when the tenant already has a rule for `template_code`, the first behaves as the second (a new version from the latest one) instead of refusing, so seeding and the console need one call.
7. **The hit reveal is its own endpoint.** Spec §15 puts `?reveal=true` on `GET /v1/monitoring/hits/{id}`; the party and screening precedent (SP-03b Deviation 3) moves every reveal to `GET /v1/monitoring/hits/{id}/reveal`, because a detail that reveals would record `VIEW_PII` on every cached refresh. The detail is masked; the reveal records one `VIEW_PII` per non-empty group (`party`, `counterparties`).
8. **Captures compare in unit space and record the converted threshold.** Spec §10.2 compares `amount_minor` with `threshold_in_ccy(...)`, but `UnitService` converts into MXN only and a transaction may be in USD. 4a accepts `MXN` and `USD` only and brings a USD amount to pesos at the prior banking day's FIX before converting it to every threshold unit (4a Deviation 6), so `unit_amounts["<unit>/<rule>"]` already holds the transaction in the threshold's unit for every capture of the snapshot (spec §7 step 3). A capture fires when `unit_amounts[unit_key].amount >= threshold.amount`; `unit_values.converted` records `threshold_in_ccy(threshold, "MXN", on, rule)` for the record. A fee threshold converts the fee through the same path at evaluation, 4a's `unit_amounts_for(units, amount=Decimal(fee_minor) / 100, currency=txn.currency, on=policy_date(txn.occurred_at), pairs={(unit, rule)}, holidays=…)` (the row carries no fee unit amount), so a USD fee follows the same FIX rule. The currency rule itself is 4a's Open question 1 for the product owner.
9. **Accumulation reads the store; the group row records it.** The `ACCUM` aggregate of an `ACCUMULATION` capture is declared through `capture_definitions(...).declarations` (so 4a maintains it and 4c's decide path can read it); the crossing test reads `accumulator_read` (which includes the transaction under evaluation) and `capture_group.accumulated_unit`/`accumulated_minor` mirror that value. Members of a group are the transactions of the key in the group's window that match the capture's `on` (a query over `transaction`, no member table), which is also what the late-event supersession recomputes.
10. **`merge_into` voids the merged event.** When an `AVI_XVI_A` event is written for a transaction that already has an `AVI_XVI_B` event (a group crossing after the fee event), the B event becomes `VOIDED` with `void_reason = "MERGED"` and the A event carries `fields.merged_captures = ["AVI_XVI_B"]`; when both fire at once only A is written (spec §10.2).
11. **Period rows exist for every capture of the period.** Spec §10.4 closes "every `(capture_code, period_key)` that ended"; a period with no event would have no row and would never be reconciled. `open_period(...)` upserts the row on the first event, and the close job first ensures a row for every capture code of every policy in force on the last day of each ended period (`MONTH` for every tenant, `QUARTER` on the first of January, April, July and October), then reconciles each `OPEN` row whose `period_end < today`.
12. **Simulation pages are party-aligned.** Spec §13.1 pages by `MONITORING_AGG_BACKFILL_BATCH` from `cursor`; a page boundary inside a party's stream would lose the memory store. A page reads at least that many transactions and then finishes the current party; after each page the job commits the tallies (`transactions_read`, `hit_count`, `customers_hit`, `hits_per_week`, `overlap`, `sample`) and `cursor = <last party id>`, so a crash resumes at the next party with exact counts.
13. **History months in simulation come from the stream.** `PROFILE_DEVIATION` needs three months of history (spec §9.1); processing counts them from the relationship's `PROCESSED` rows, simulation from the first transaction of the key inside the range. A simulation over fewer than four months therefore never fires `DECLARED_VS_ACTUAL_VOLUME`'s history branch; the staff view says so (`simulation.notes`).
14. **The typology file is a wrapper.** Spec §9.3 says one file per template; the file is `{version, country_code, packs, definition}` where `definition` is a `RuleDefinition` document (`code`, `family`, `kind`, `title`, `typology`, `on`, `params`, `aggregates`, `conditions`, `severity`, `sync`, `dedup`, `evidence`, `unit_key`), so `rule_template` columns come from the definition and the wrapper carries only what the table needs beyond it.
15. **`JurisdictionLists` keeps the file's keys.** `policy-packs/_jurisdictions.yaml` on `main` has `call_for_action` and `increased_monitoring`; the loader maps them to `FATF_BLACK` and `FATF_GREY`, and B1 adds `tax_haven: []` and `regions: {BORDER_ZONE_MX: [...]}` with their own `confidence` (Open question 2). A name a definition uses that the file lacks is a load error, never an empty set.
16. **A failed simulation is final.** `monitoring.simulate` is enqueued with `max_attempts=1`; an exception sets the row `FAILED` with `last_error` (the exception class and code, never data), publishes `monitoring.simulation_completed.v1` with `status = FAILED` and re-raises, so the runner records it and the staff view shows it. Staff request another.
17. **B5 follows the code on `main` where the task text differs.** `PolicyPackVersion` has no `pack_code`: `seed_tenant_rules` joins `policy_pack` for the codes of the assignments whose `effective_to` is null or not before today. A `RuleDefinition` does not know its template version, so `check_params` and `build_definition` take a keyword `source` (`"template <code> v<n>"`, or `"rule <code> v<n>"` for a custom rule, which is bounded by its latest version). B1's validator already refuses a `STRUCTURING` window that differs from its declarations, so `build_definition` derives the band and windows in the document before validating it; `coupling_problem` (in `versions.py`) is the validator of reconciliation finding 27 and refuses a custom rule whose band or windows disagree with its params. A `VELOCITY` evidence window follows `window` too. `RuleTransitionNotAllowedError` is created by B5 (B6 had not merged) and `WRITERS` lives in `versions.py` for B6 to import. `rules load` defaults to `<packs_dir>/MX/_typologies`, as `holidays seed` reads `packs_dir`. A custom rule must be an `ALERT` rule and a code the tenant already has is 409 `CONFLICT`. Fix round 1 (review M2, M7, M8, M4): a window outside its kind's limits (PT1H..P6M, months not mixed with days, VELOCITY at most P7D) is a `RULE_PARAM_OUT_OF_BOUNDS` item at `("params", "window")` rather than a bare `VALIDATION_FAILED`. A JSON float for a parameter is refused, as `RuleDefinition` already refuses floats. A first create that loses the race on `(tenant_id, code)` becomes a new version of the winner's rule (savepoint), and a seed skips the code. `rules seed-tenant --author` must be an active writer.
18. **B6 follows the code on `main` where the task text differs.** `RuleTransitionNotAllowedError` (in `errors.py`) and `WRITERS` (in `versions.py`) are B5's; `lifecycle.py` imports both and re-exports `WRITERS`, and the error row in `test_errors.py` is B5's too. `evaluate_rule_actions` takes a keyword `out_of_bounds: bool = False`, because a pure evaluation cannot read the template version: the service computes it with `check_params` against the version's template version (a custom rule, bounded by its own versions, is never out of bounds). A simulation is stale when its age is at least `MONITORING_SIMULATION_MAX_AGE_DAYS` ("younger than", spec §9.4), when its hash differs, and missing when it is absent, not `COMPLETED` or has no `finished_at`; reasons accumulate as in SP-03's matrix, so `SUBMIT` from `DRAFT` without a simulation reports `STATE, SIMULATION_MISSING`. Activation ends **every** assignment of the rule still in force on or after `effective_from` (not only the current `ACTIVE` version's) on `effective_from − 1 day`, so a version activated before an already scheduled successor starts never overlaps the version that successor was to replace; a range may end before it starts, which simply never applies. The retirement an activation causes is audited as `rules.version_retired` (`{"assignment_id", "retired_by_version_id"}`), and submit and approve record `{"simulation_id"}`. Reject keeps `simulation_id` (the reviewed simulation stays linked; a new simulation replaces it). `active_rules` keys its cache on the count and maximum of the tenant's `activated_at` and `retired_at` (two activations in one instant still change the key) and reads the lists from `<packs_dir>/_jurisdictions.yaml` (`load_rule_lists`); `product=None` reads tenant-wide assignments only, as `policy.assignments_on` does. The list-name test uses `BORDER_ZONE_CASH` (`$BORDER_ZONE_MX`), the only shipped template naming a list in its filters, and the deduplication and `ruleset_hash` tests build `RuleSet` in memory. Fix round 1 (review I1 to I3, M3, M4): retire ends every assignment of the rule still in force after today on today, so retiring a scheduled successor does not leave its predecessor running to the successor's start. Four eyes extends to editing: `APPROVE` reports `SAME_AUTHOR` when the caller has a `rules.version_updated` audit row on the version (`version_editors`; spec §9.4 lets any writer edit, which would otherwise let an approver approve their own parameters), without a migration. The rejection reason is required (1 to 500 characters) but its text is kept nowhere: the audit detail is `{"simulation_id", "reason_given": true}` (codes only; free text could name a person, and 0013 has no encrypted column for it; Open question 12). The lifecycle locks the rule, then its versions, with `FOR NO KEY UPDATE` (`key_share=True`), in activation and retirement alike: it conflicts with every other `FOR UPDATE`/`FOR NO KEY UPDATE`, so lifecycle writers and `create_version` still serialise, but no longer blocks the `KEY SHARE` locks of foreign-key inserts (hits, simulations, a new version). An activation effective today ends the predecessor's assignment yesterday although it evaluated part of today; hits keep their `assignment_id`, which answers per transaction.
19. **B7 follows the code on `main` where the task text differs.** `evaluate_rules(ruleset, *, txn, store, profile, policy, now)` builds one `EvalContext` per rule with `reader_for(store, txn, <that rule's declarations by name>)`, because two rules may give one name to different declarations (a single context's reader cannot serve both). The contract `monitoring-boundaries` forbids `xolotl.modules.screening.engine.models`, which `screening.api` imports, so `profile_for` cannot call `party_view` itself: `register_rules(*, queue, screening_status)` installs a reader (`use_screening_status`), and `wire_platform` passes `party_screening_status` (in `app.py`, the composition root), which builds `party_view(s, party_id, staff=False).status` (the status is the same in both views, and the client view loads no findings); without a reader the status is None. The PEP status lives on `person` and monitoring may not import `party.models`, so `party.service` gains `pep_status_of(s, party_id)` (`NONE` for an organization) and `RelationshipRef` gains `started_at`; the profile's tier is the relationship's `account_tier`, its risk rating and monitoring profile the transaction's, and `declared_profile` stays None for C8. A reader asked for a declaration whose key the transaction lacks returns the empty value (nothing of it was counted). ISO dedup windows floor from 0001-01-01 00:00 UTC, a Monday, so `P7D` buckets are Monday-to-Sunday UTC weeks (the exit week of 7 to 13 December is one bucket) and whole months or years floor to calendar months. A cooldown applies only to a hit whose `first_occurred_at` is at or before the firing (a late firing before the key's hit is not suppressed by it). `notice_tier_limit` is called for every `TIER_LIMIT` firing, not only one that made a new hit: the notice is idempotent, so a crash between the two can never lose the webhook. `reevaluate_later` re-evaluates each later row under the rules in force on its own date and the policy of its own snapshot; the late row's rule set only bounds the range (the reach of its longest declaration: the window for `DURATION` and `ACCUM`, the end of the calendar bucket for `MONTH` and `QUARTER`). `evidence_transactions` excludes the trigger and filters `transactions_where` in Python over `enriched_from_row`. The evidence's `aggregates` are the values the rule read (`Firing.aggregates`). `RulesHook`'s two methods are static (ruff's no-self-use). Fix round 1 (review I1 to I5, M1, M3, M5, M7):
- `fired_count` counts a transaction once: the `WINDOW` link insert returns what it inserted, and only an inserted link increments it, so a replay, a concurrent loser or a re-evaluated row already linked adds nothing (C11 step 8's "fired_count unchanged" holds when the late row itself does not fire).
- `record_firing(..., in_flight=())` and `evidence_transactions(..., in_flight=())` take the row being processed. `reevaluate_later` passes the late row, which is not `PROCESSED` while the hooks run, so a hit found by re-evaluation links it as `WINDOW`.
- `Dedup.window` refuses a zero duration (`P0D`, `P0M`, `P0Y`) with a `ValueError`; the API refuses it as 422 `VALIDATION_FAILED`, as B5 refuses any invalid definition document. `dedup_bucket` buckets a stored zero window per instant instead of dividing by it.
- `app.register_processing(queue=...)` registers the processing hooks (through `register_rules`). `wire_platform`, `xolotl monitoring process` and `xolotl monitoring backfill` all call it, so a CLI drain evaluates rules as the worker does.
- `reevaluate_later` re-runs only the rules that read an aggregate. Each rule runs only over the later rows inside its own reach from the late row. Rows at the late row's instant are included (`occurred_at >=`, the late row excluded by id). The remaining cost is still one rule-set evaluation per later row in reach, each with `profile_for` (about six queries), and `evidence_transactions` still filters a key's window in Python over `enriched_from_row`. Both are listed for the performance follow-ups.
- **M1:** `P1D` and `P7D` buckets are UTC days and weeks, so a "day" runs 18:00 to 18:00 Mexico City (17:00 to 17:00 in summer time where it applied) and a week ends Sunday evening there. This follows Global Constraints ("dedup windows floor in UTC"); the cooldown hides most of the difference. Whether to floor in Mexico City instead is for the product owner.
- **M5:** a re-evaluated historical row is judged against the customer's profile as it stands now: current tier, PEP and screening status. A late row can therefore produce a hit on an older row that the profile of its day would not have fired, for example `BLOCKED_PERSON_PARTY` on rows inside the reach of a party matched since. The row's own `tier` is recorded on the row, but the PEP and screening status at the time are not kept anywhere. Accepted for SP-04.
Fix round 2 (re-review N1, N2):
- **N2:** `reevaluate_later(s, late, ctx)` no longer takes the late row's rule set. Every later row of the party within six months of the late row (the longest reach any window can have: P6M, or a quarter's end) is a candidate; only its key columns are read first. A candidate is re-evaluated under the rules in force on its own date that read an aggregate and whose own reach from the late row holds it, with the reach computed per version. So a rule that started after the late row's date is re-run, and so is a newer version with a longer window.
- **N1:** the validator refuses a zero dedup window, but a stored one (written before the validator, or by hand in SQL) would make `active_rules` fail for the whole tenant: `RuleDefinition.model_validate` raises when the rule set loads. No write path bypasses the validator today. B9's routes must keep validating every definition they store through `RuleDefinition`/`Dedup`, and nothing may write `monitoring_rule_version.definition` around them.
20. **B8 follows the code on `main` where the task text differs.** The lifecycle moves a version only in a staff context (`_staff` refuses a platform operator), so `rules activate` acts as the author (simulate, submit) and as the approver (approve, activate) in staff contexts carrying each user's stored roles, not in "a platform context narrowed to the tenant"; `rules simulate` names no requester, so it runs as the version's author. `rules activate` refuses an approver who is the author with `RULE_TRANSITION_NOT_ALLOWED` (`SAME_AUTHOR`) before anything moves, skips the steps already taken (a version `IN_REVIEW` or `APPROVED`), and simulates the last 30 days with today included by default. `ScreeningSubject` and `SubjectStatus` live in `screening.engine`, which `monitoring-boundaries` forbids, so `later_true_match` reads a `TrueMatchReader` (`simulation.job.use_true_match_reader`) that `register_rules(..., true_match=...)` installs and `wire_platform` fills with `app.party_true_match_at` (the party's `last_screened_at` when its status is `TRUE_MATCH`), as Deviation 19 did for the screening status. `hits_per_week` keys on the ISO week (Mexico City) of each hit's `window_end`, not its `window_start`: the task's own test expects `2026-W50` for the 7 to 13 December week, whose structuring window starts on 2 December (W49). The job pages through a public `replay_pages` (one `ReplayPage` of whole parties with its row count and hits), on which `replay_rule_set` is built, because the tallies and the cursor are committed per page. The row's `definition_hash` is rewritten at the `RUNNING` transition to the hash of the definition actually replayed, so an edit between the request and the start leaves the version unmarked; a resumed run whose version changed since fails. `rules.simulation_requested` is audited on the version (`monitoring_rule_version`, detail `{"simulation_id", "range_start", "range_end"}`), like every other `rules.*` row. `run_simulation_now` writes no job row. The typology goldens' `expected.yaml` also records the trigger's `external_id`, the severity and each aggregate's `count`, and a scenario transaction may carry its own `profile` (the customer matched between two deposits). Open question 6 keeps its default: history months count from the range (Deviation 13), and the README says so. With `max_attempts=1` (Deviation 16), a worker killed mid-run leaves its job `DEAD` after the reaper and the row `RUNNING`; the resume logic (the cursor) runs only when `handle_simulate` is called again for that row, so staff request another simulation.
Fix round 1 (review I1, I2, M1 to M9):
- **I1:** `rules simulate` and `rules activate` first write one `rules.cli_invoked` audit row on the version as the operator (`PLATFORM_OPERATOR`, `cli:<os user>`), detail `{"command", "version_id", "author_id", "approver_id", "simulation_id"}` (ids only; the simulation id is chosen before the run through `run_simulation_now(..., simulation_id=)`). The staff steps that follow stay audited as the author and the approver. A simulate refused afterwards (its range) keeps the row; activations refused up front write none. The CLI cannot map the OS operator to a staff user, so it does not refuse an operator who names themselves as approver; the README says so.
- **I2:** a page holds every row it loads (`loaded_as_persistent`), so the counterparty and each policy snapshot are read once per page, not once per transaction; the simulation's profile comes from the new `rules.evaluate.profile_with_history` (processing's `profile_for` computes the history months and calls it, unchanged), so the discarded `min(occurred_at)` query is gone; `TrueMatchReader` takes a page's parties and `app.party_true_match_at` answers them in one query. `test_a_page_reads_a_bounded_number_of_queries` pins it. Left for the performance follow-ups: about four queries per relationship for the profile (relationship, party, screening subject, PEP status), and one `rule_hit` query per page.
- **M1:** a rule keyed on `COUNTERPARTY` or `DEVICE` (`key_kind`, `dedup.key` or a declaration) is refused by `request_simulation` with `VALIDATION_FAILED`, since parties are replayed one at a time; `replay_rule_set` (4c's dry run) does not check, so C6 must not compare such rules.
- **M2, M3 (documented):** windows start cold at the range (a product-owner call beside Open question 6: warming the store from `range_start` minus the longest window), and a party's whole in-range stream is held at once (Deviation 12).
- **M4:** `rules activate` refuses an approver who authored or edited the version (`version_editors`), and a version past `APPROVED`, before anything moves.
- **M5:** `monitoring.sweep` calls `simulation.job.fail_lost_simulations`: a `QUEUED` or `RUNNING` row whose `simulate:<id>` job is no longer live, or that has no job (a CLI run) and is over a day old, becomes `FAILED` with `last_error = "WorkerLost"` and publishes the completion event. The README no longer promises a resume.
- **M6:** a cross-tenant test (tenant B's rows, hits and version stay out of tenant A's simulation).
- **M7:** `party_true_match_at` is tested; "later" compares the party's last screening, which overcounts a party matched long ago and screened again since (documented).
- **M8, for B9:** `rule_simulation.sample` (party, relationship and trigger ids per simulated hit) and `overlap` say which customers a rule would hit: API clients with `rules:read` must not receive them (PRV-502); serialize them to staff only.
- **M9:** `RuleSet(on=)` in `replay_party` uses `policy_date`; `_simulate_as` says why it registers the processing wiring; the plan's `test_composition_root.py` does not exist, and `tests/platform/test_worker.py::test_the_platform_is_wired_with_its_job_handlers` carries the `monitoring.simulate` row.

## Review Focus

1. **Tipping-off (PRV-502, spec §14).** A wildcard webhook endpoint receives `monitoring.tier_limit_reached.v1` but never a `monitoring.rule_hit_*`, `monitoring.reportable_event_*`, `monitoring.capture_period_*`, `monitoring.rule_version_*` or `monitoring.simulation_*` delivery row; an endpoint naming one is refused with 422 `EVENT_TYPE_NOT_DELIVERABLE`; the limit payload carries no amount. Tests: B7 `test_a_wildcard_endpoint_gets_the_limit_event_but_never_a_hit_event`, B10 `test_capture_events_never_reach_a_webhook`, B7 `test_the_limit_payload_carries_ids_and_codes_only`.
2. **Exactly once (NFR-TM-004, FR-TM-006).** The same batch processed twice, a late row, and two concurrent firings for one key produce one hit per `(rule_version, dedup_key)` with the later firings linked as `WINDOW` or `COOLDOWN`. Tests: B7 `test_replaying_the_same_stream_creates_each_hit_once`, `test_two_sessions_firing_at_once_create_one_hit`, `test_a_late_row_refires_nothing_already_recorded`.
3. **Four eyes and a fresh simulation (SEC-501, FR-TM-011, FR-TM-016).** The exhaustive state × role × same-user × action matrix matches spec §9.4; the author cannot approve; an approval 31 days after the simulation finished, or with another `definition_hash`, is refused; the database CHECK refuses a same-user `approved_by` inserted in SQL. Tests: B6 `test_the_lifecycle_matrix_is_exactly_the_spec`, `test_a_stale_simulation_blocks_approval`, `test_the_check_refuses_a_same_author_approval_in_sql`.
4. **Captures are complete and reproducible (FR-TM-007, FR-TM-009, OPS-504).** Each capture code over its golden week produces exactly the expected events with period keys and unit values; the whole six-month group is captured on crossing; a late operation supersedes and re-captures the group, reopens a closed period and publishes `capture_period_changed`; the close reconciles with zero discrepancies on clean data and names an injected gap. Tests: B10 `test_capture_goldens`, `test_a_late_operation_supersedes_the_group`, B11 `test_the_close_finds_an_injected_gap_and_writes_the_missing_event`.
5. **Simulation has no side effects and equals production (PRV-503, FR-TM-011, D12).** Row counts of every monitoring table other than `rule_simulation` and the outbox are equal before and after a simulation; the simulated hits of a week equal the hits processing stored for it. Tests: B8 `test_a_simulation_writes_only_its_own_row`, `test_simulation_reproduces_the_stored_hits_of_the_week`.
6. **No plaintext beyond ids (PRV-501).** After a hit, a capture, a simulation and a reveal, `everything_stored(admin_engine)` holds no counterparty name, account or wallet and no party name outside the encrypted columns and the name indexes; outbox payloads, job payloads, `last_error` and audit details hold ids and codes. Test: B12 `test_no_plaintext_is_stored_beyond_the_encrypted_columns`.

## File structure

```
policy-packs/_jurisdictions.yaml                         + tax_haven, regions.BORDER_ZONE_MX (B1)
policy-packs/MX/_typologies/<CODE>.yaml                  the 16 templates of spec §9.3 (B3)
src/xolotl/platform/policy/jurisdictions.py              JurisdictionLists, load_jurisdictions, JURISDICTIONS_FILE (B1)
src/xolotl/platform/policy/__init__.py                   + the two exports (B1)
src/xolotl/platform/config.py, .env.example, README.md   + the five settings (B5, B6, B8, B11)
src/xolotl/migrations/versions/0013_rules_captures.py    (B4)
src/xolotl/models.py                                     + monitoring rules and captures models, CounterpartyNameIndex (B4)
src/xolotl/platform/tenancy/registry.py                  SHARED_TABLES + rule_template, rule_template_version (B4)
src/xolotl/modules/screening/counterparty/models.py      + CounterpartyNameIndex (B4; A2's file)
src/xolotl/modules/screening/engine/models.py            ScreeningSubject.counterparty_id, the kind check (B4)
src/xolotl/modules/monitoring/
  events.py            + the 4b constants and RULES_STAFF_ONLY_EVENT_TYPES (B4; A4's file, appended)
  errors.py            + RuleTemplateUnknownError, RuleTemplateImmutableError, RuleParamOutOfBoundsError, RuleTransitionNotAllowedError, SimulationRangeTooLongError, PeriodNotEndedError (B5, B6, B8, B11)
  schemas.py           + rules, hits and captures response models (B9, B11, B12; A10's file, appended)
  routes_rules.py      /v1/monitoring/templates, /rules, /simulations, /assignments (B9)
  routes_hits.py       /v1/monitoring/hits, /v1/parties/{id}/hits (B12)
  routes_captures.py   /v1/monitoring/captures, /periods (B11)
  rules/__init__.py    empty (B1)
  rules/definitions.py RuleFamily, RuleKind, Severity, HitOutcome, Param, Dedup, Evidence, Condition, SegmentFilter, RuleDefinition (B1)
  rules/render.py      render(definition, locale) (B1)
  rules/kinds.py       Incomplete, AggregateReader, EvalProfile, DeclaredProfile, EvalContext, AggregateSnapshot, Firing, applies, evaluate (B2)
  rules/library.py     TypologyFile, read_typology, read_library, TYPOLOGIES_DIR (B3)
  rules/enums.py       RuleVersionStatus, SimulationStatus, HitTransactionRole, RuleAction, RuleBlockedReason, NoticeSource (B4)
  rules/models.py      RuleTemplate, RuleTemplateVersion, MonitoringRule, MonitoringRuleVersion, RuleSimulation, RuleAssignment, RuleHit, RuleHitTransaction, TierLimitNotice (B4)
  rules/templates.py   LoadReport, load_templates, template_version, latest_template_versions (B5)
  rules/versions.py    VersionInput, check_params, create_rule, create_version, update_version, seed_tenant_rules (B5)
  rules/lifecycle.py   WRITERS, APPROVERS, RuleActionSet, evaluate_rule_actions, submit_version, approve_version, reject_version, activate_version, retire_version (B6)
  rules/assignments.py LoadedRule, RuleSet, active_rules, clear_rule_cache, assignments_on (B6)
  rules/evaluate.py    reader_for, profile_for, evaluate_rules (B7)
  rules/hits.py        record_firing, evidence_transactions, dedup_bucket, link_transaction (B7)
  rules/tier_limits.py notice_tier_limit (B7)
  rules/hook.py        RulesHook, reevaluate_later (B7)
  rules/wiring.py      register_rules (B7; B8, B10, B11 append)
  simulation/__init__.py, engine.py   SimulatedHit, replay_party, replay_rule_set (B8)
  simulation/job.py    SIMULATE_KIND, request_simulation, handle_simulate, run_simulation_now (B8)
  captures/__init__.py empty (B10)
  captures/enums.py    CaptureKind, CaptureGroupStatus, ReportableEventStatus, CapturePeriodStatus (B4)
  captures/models.py   CaptureGroup, ReportableEvent, CapturePeriod (B4)
  captures/definitions.py  CaptureDefinition, capture_definitions, TRIGGERS, read_triggers (B10)
  captures/triggers.yaml, captures/field_maps/<REPORT_CODE>.yaml (B10)
  captures/fields.py   FieldMap, read_field_map, event_fields, unit_values (B10)
  captures/events.py   capture_transaction, CaptureOutcome (B10)
  captures/groups.py   open_group_for, add_to_group, cross_group, group_members (B10)
  captures/supersede.py  supersede_group, reopen_period (B10)
  captures/hook.py     CapturesHook (B10)
  captures/periods.py  open_period, ensure_periods, check_captures, close_period, PERIOD_CLOSE_KIND, handle_period_close (B11)
src/xolotl/modules/tenants/service.py                    + MONITORING_PERIOD_CLOSE_SCHEDULE (B11)
src/xolotl/app.py                                        register_rules after register_monitoring (B7); + three routers (B9, B11, B12)
src/xolotl/cli.py                                        rules_app: validate (B3), load, show, seed-tenant (B5), simulate, activate (B8); monitoring_app (4a) + period-close (B11); db upgrade and serve run the library load (B5)
.importlinter                                            + monitoring-evaluator-is-pure (B1)
tests/platform/test_jurisdictions.py (B1); test_migration_chain.py, test_errors.py, test_config.py, test_worker.py (appended)
tests/isolation/factories.py (+11) (B4)
tests/modules/monitoring/rules/{__init__,conftest,test_definitions,test_render,test_kinds,test_library,test_migration_0013,test_templates,test_versions,test_lifecycle,test_assignments,test_evaluate,test_hits,test_hook,test_tier_limits}.py
tests/modules/monitoring/simulation/{__init__,test_engine,test_job}.py; tests/modules/monitoring/captures/{__init__,test_definitions,test_fields,test_events,test_groups,test_supersede,test_periods}.py
tests/modules/monitoring/test_routes_rules.py, test_routes_hits.py, test_routes_captures.py, route_support.py (B9, B11, B12)
tests/golden/monitoring/rules/render/<CODE>.es-MX.txt, <CODE>.en.txt (B1, B3); tests/golden/monitoring/typologies/<CODE>/{scenario,expected}.yaml (B8); tests/golden/monitoring/captures/<CODE>/{units,transactions,expected}.yaml (B10)
tests/test_cli.py (+) (B3, B5, B8, B11)
console/src/features/rules/{queries.ts, queries.test.tsx, fixtures.ts, RulesPage.tsx, RulesPage.test.tsx, RulePage.tsx, RulePage.test.tsx, VersionEditor.tsx, VersionEditor.test.tsx, SimulationPanel.tsx, SimulationPanel.test.tsx, LifecycleButtons.tsx, LifecycleButtons.test.tsx, RuleStatusBadge.tsx} (B13)
console/src/features/hits/{queries.ts, HitsPage.tsx, HitsPage.test.tsx, HitPage.tsx, HitPage.test.tsx, EvidenceTable.tsx, EvidenceTable.test.tsx, HitRevealCard.tsx, SeverityBadge.tsx, fixtures.ts} (B14)
console/src/features/captures/{queries.ts, CapturesPage.tsx, CapturesPage.test.tsx, PeriodTable.tsx, PeriodTable.test.tsx, TransactionCapturesCard.tsx} (B15)
console/src/features/hits/{TransactionHitsCard.tsx, PartyHitsCard.tsx} (B14); console/src/features/transactions/TransactionPage.tsx (A11; + the two cards, B14 and B15); console/src/features/parties/PartyPage.tsx (+ PartyHitsCard, B14)
console/src/{api/schema.d.ts, api/types.ts, auth/access.ts, auth/access.test.ts, auth/guards.test.tsx, components/layout/nav.ts, App.tsx, i18n/es-MX.json, i18n/en.json, i18n/catalogue.test.ts} (B13, B14, B15); console/e2e/smoke.spec.ts (+ step) (B13)
```

## Tasks at a glance

| Id | Title | Depends on | Console | Branch slug |
|---|---|---|---|---|
| B1 | Jurisdiction lists loader, rule definitions and the rendered sentence | A7 (`aggregates/declarations.py`, `store.py`; pure) | no | `sp04b-b1-definitions` |
| B2 | The pure evaluator: the seven kinds | B1 (A7's `store.py` and `windows.py` come with it) | no | `sp04b-b2-evaluator` |
| B3 | The Mexico typology library files, the loader and `rules validate` | B1 | no | `sp04b-b3-typology-library` |
| B4 | Migration 0013, models, enums, events, isolation factories | A2 (0012, models, factories) | no | `sp04b-b4-migration-0013` |
| B5 | Templates in the database, tenant rules and versions with bounds, `rules load/show/seed-tenant` | B3, B4 | no | `sp04b-b5-templates-versions` |
| B6 | Lifecycle, assignments and the active rule set | B5 | no | `sp04b-b6-lifecycle` |
| B7 | Hits, dedup, cooldown, evidence, the tier-limit notice, the rules processing hook and staff-only registration | B2, B6, A8 (hooks, `run_processing`) | no | `sp04b-b7-hits-hook` |
| B8 | Simulation engine and job, `rules simulate`, `rules activate`, typology goldens | B7 | no | `sp04b-b8-simulation` |
| B9 | Rules API: templates, rules, versions, transitions, simulations, assignments | B8 | no | `sp04b-b9-rules-api` |
| B10 | Capture definitions, trigger and field maps, reportable events, groups, `merge_into`, supersession, the captures hook, capture goldens | B4, B7 | no | `sp04b-b10-captures` |
| B11 | Capture periods, period close, its schedule and CLI, captures API | B10 | no | `sp04b-b11-period-close-api` |
| B12 | Hits API | B7, A10 | no | `sp04b-b12-hits-api` |
| B13 | Console: Reglas › Monitoreo with the editor, simulation panel and lifecycle | B9 | yes | `sp04b-b13-console-rules` |
| B14 | Console: Hallazgos de reglas | B12, A11 | yes | `sp04b-b14-console-hits` |
| B15 | Console: Reglas › Capturas y límites | B11, A11 | yes | `sp04b-b15-console-captures` |

---

### Task B1: Jurisdiction lists loader, rule definitions and the rendered sentence

Depends on **A7** (`aggregates/declarations.py`: `TxnFilter`, `AggregateDeclaration`; `aggregates/store.py`: `AggregateKey`; and A3's `KeyKind` in `monitoring/txn/schema.py`), which is pure, so B1 starts as soon as A7 is on `main`, in parallel with the rest of 4a. List-name resolution is this plan's: B1 defines `resolve_list_names` and `resolve_definition` beside `JurisdictionLists`, and 4a never sees a name. No tables, no console. Adds one platform module (spec §9.1: "plan 4b adds the loader").

**Files:**
- Create: `src/xolotl/platform/policy/jurisdictions.py`, `src/xolotl/modules/monitoring/rules/__init__.py`, `src/xolotl/modules/monitoring/rules/definitions.py`, `src/xolotl/modules/monitoring/rules/render.py`
- Modify: `policy-packs/_jurisdictions.yaml`, `src/xolotl/platform/policy/__init__.py`, `.importlinter`
- Test: `tests/platform/test_jurisdictions.py`, `tests/modules/monitoring/rules/__init__.py`, `tests/modules/monitoring/rules/test_definitions.py`, `tests/modules/monitoring/rules/test_render.py`, `tests/golden/monitoring/rules/render/`

**Interfaces:**

Consumes: `canonical_json` (`xolotl.platform.audit.chain`, pure), `ConversionRule`, `Quantity`, `TxnFilter`, `AggregateDeclaration`, `AggregateKey` (A7), `KeyKind` (A3), `check_iso_duration` (`xolotl.platform.policy.durations`).

Produces:

```python
# platform/policy/jurisdictions.py
JURISDICTIONS_FILE = Path("policy-packs/_jurisdictions.yaml")
COUNTRY_SETS = ("FATF_BLACK", "FATF_GREY", "TAX_HAVEN"); REGION_SETS = ("BORDER_ZONE_MX",)
@dataclass(frozen=True, slots=True)
class JurisdictionLists:
    as_of: date; countries: Mapping[str, frozenset[str]]; regions: Mapping[str, frozenset[str]]; content_hash: bytes   # sha256 of the file bytes
    def resolve_countries(self, names: Iterable[str]) -> frozenset[str]     # "$FATF_GREY" or "FATF_GREY"; unknown -> ValueError
    def resolve_regions(self, names: Iterable[str]) -> frozenset[str]
def load_jurisdictions(path: Path = JURISDICTIONS_FILE) -> JurisdictionLists
    # file keys: as_of, source, confidence, call_for_action -> FATF_BLACK, increased_monitoring -> FATF_GREY, tax_haven -> TAX_HAVEN,
    # regions: {BORDER_ZONE_MX: [...]}; a missing key, a non-alpha-2 code or a region not matching ^[A-Z]{2}-[A-Z0-9]{1,3}$ -> ValidationFailedError

# rules/definitions.py (pure; list-name resolution is this plan's, reconciliation finding 3)
def resolve_list_names(where: TxnFilter, lists: JurisdictionLists) -> TxnFilter
    # a `$NAME` in counterparty_country_in, counterparty_country_not_in or geo_admin_area_in is replaced by the set's codes (countries through
    # resolve_countries, regions through resolve_regions); literal codes pass through; an unknown name -> ValueError
def resolve_definition(definition: RuleDefinition, lists: JurisdictionLists) -> RuleDefinition
    # resolve_list_names over `on`, `evidence.transactions_where` and every declaration's `where`, and lists_hash = lists.content_hash.hex() on every
    # declaration (4a Deviation 8), so a changed _jurisdictions.yaml yields new agg_ids (spec §8.2); B6 calls it when it loads a rule set

# rules/definitions.py
class RuleFamily(StrEnum): ALERT; CAPTURE
class RuleKind(StrEnum): THRESHOLD; AGGREGATE; STRUCTURING; VELOCITY; HIGH_RISK_JURISDICTION; PROFILE_DEVIATION; TIER_LIMIT
class Severity(StrEnum): LOW; MEDIUM; HIGH; CRITICAL
class HitOutcome(StrEnum): ALERT; BLOCK; REVIEW
ParamValue = Decimal | int | str | bool
class Param[T: ParamValue](BaseModel):    # strict: "5000" -> Decimal, 3 -> int, never a float
    value: T; min: T | None = None; max: T | None = None; choices: list[T] | None = None; tighten: Literal["LOWER", "RAISE"] | None = None; unit: str | None = None
class Dedup(BaseModel): key: KeyKind = KeyKind.RELATIONSHIP; window: str = "P1D"; cooldown: str = "P7D"   # window: ISO duration or "MONTH"; cooldown: ISO duration ("P0D" = none)
class Evidence(BaseModel): transactions_window: str | None = None; transactions_where: TxnFilter = TxnFilter(); aggregates: list[str] = []
class Condition(BaseModel): aggregate: str; op: Literal["GTE", "GT", "LTE", "LT"]; threshold: str | None = None; ratio_of: str | None = None; ratio: Decimal | None = None
    # exactly one of threshold (a params name) or ratio_of + ratio; validated
class SegmentFilter(BaseModel): risk_rating: list[str] = []; tier: list[str] = []; monitoring_profile: list[str] = []   # empty = any
class RuleDefinition(BaseModel):             # extra = "forbid"
    language: Literal["KIND/1"] = "KIND/1"; code: str; family: RuleFamily; kind: RuleKind; title: str; typology: list[str]
    on: TxnFilter = TxnFilter(); key_kind: KeyKind = KeyKind.RELATIONSHIP; segment: SegmentFilter | None = None
    unit_key: str | None = None             # "<unit>/<rule>" the amount params are read in, e.g. "USD/PRIOR_BANKING_DAY"
    params: dict[str, Param[Decimal] | Param[int] | Param[str] | Param[bool]]
    aggregates: list[AggregateDeclaration] = []; conditions: list[Condition] = []
    severity: Severity; sync: bool = False; dedup: Dedup = Dedup(); evidence: Evidence = Evidence()
    def definition_hash(self) -> bytes       # sha256(canonical_json(model_dump(mode="json"))): Decimals as strings, sorted keys
    def render(self, locale: Literal["es-MX", "en"] = "es-MX") -> str   # delegates to rules.render.render
    def with_params(self, values: Mapping[str, ParamValue]) -> RuleDefinition   # copies, replacing each Param.value, bounds kept
REQUIRED_PARAMS: Mapping[RuleKind, frozenset[str]] = {
    THRESHOLD: {"threshold"} (optional for the screening and PEP rules: see B3), AGGREGATE: set(), STRUCTURING: {"threshold", "near_ratio", "window", "min_count"},
    VELOCITY: {"window"} plus one of {"count", "sum"}, HIGH_RISK_JURISDICTION: {"threshold", "lists"}, PROFILE_DEVIATION: {"ratio", "history_ratio"}, TIER_LIMIT: {"outcome"}}
# model validators: the kind's required params exist and have the expected type; every Condition.threshold names a param and every
# Condition.aggregate / ratio_of and Evidence.aggregates names a declared aggregate; a TIER_LIMIT has params.outcome with choices ["BLOCK", "REVIEW"],
# dedup.window == "MONTH" and sync True; `on` and `evidence.transactions_where` may hold "$NAME" list names (resolved at load, spec §8.2);
# ISO durations are checked with check_iso_duration; a Param value outside its own min/max or choices -> ValueError

# rules/render.py
def render(definition: RuleDefinition, locale: Literal["es-MX", "en"]) -> str
    # one sentence per kind, parameters substituted; es-MX first. Fixed copy for CASH_STRUCTURING (spec §9.1):
    # "Dispara cuando el cliente recibe 3 o más depósitos en efectivo entre 70 % y 100 % del umbral en 7 días y su suma alcanza el umbral"
    # the threshold is written as "<amount> <unit>" with a thin-space thousands separator in es-MX ("5 000 USD") and a comma in en ("5,000 USD")
```

`.importlinter`: the contract `monitoring-evaluator-is-pure` of Global Constraints (as built, it lists `rules.definitions` and `rules.render` only: import-linter refuses a source module that does not exist, so B2, B3 and B10 each add theirs; see their notes from B1).

`_jurisdictions.yaml` gains `tax_haven: []` (with a comment: no source chosen, Open question 1) and `regions: {BORDER_ZONE_MX: [MX-BCN, MX-SON, MX-CHH, MX-COA, MX-NLE, MX-TAM]}` with `regions_confidence: UNVERIFIED` and a comment naming the six border states as the default of Open question 2.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/platform/test_jurisdictions.py
def test_the_shipped_file_loads_with_the_four_sets()                     # FATF_BLACK == {"KP","IR","MM"}, FATF_GREY has 22 codes, TAX_HAVEN == set(), BORDER_ZONE_MX has 6 regions; content_hash == sha256 of the bytes
def test_names_resolve_with_or_without_the_dollar()                      # resolve_countries(["$FATF_GREY", "FATF_BLACK"]) is the union; "NOWHERE" -> ValueError
def test_a_malformed_code_is_refused(tmp_path)                           # "MEX" -> ValidationFailedError; a missing key -> ValidationFailedError

# tests/modules/monitoring/rules/test_definitions.py
def test_a_definition_hash_is_stable_and_changes_with_a_param()          # same document twice -> same bytes; threshold 5000 -> 4999 -> different; key order irrelevant
def test_params_are_strict_numbers()                                     # "5000" -> Decimal; 3 -> int; 3.5 (float) -> ValidationError
def test_a_param_default_outside_its_bounds_is_refused()                 # value 900, min 1000 -> ValidationError naming "threshold"
def test_each_kind_requires_its_parameters()                             # STRUCTURING without min_count -> ValidationError; TIER_LIMIT without outcome choices -> ValidationError
def test_conditions_name_declared_aggregates_and_params()               # unknown aggregate -> ValidationError; both threshold and ratio_of -> ValidationError
def test_with_params_replaces_values_and_keeps_bounds()
def test_a_tier_limit_definition_is_sync_and_monthly()
def test_list_names_resolve_to_codes_and_change_the_agg_id()              # `$FATF_GREY` -> the 22 codes; lists_hash set on every declaration; another file hash -> another agg_id; an unknown name -> ValueError

# tests/modules/monitoring/rules/test_render.py (golden: prints a unified diff on mismatch, as tests/golden/packs does)
def test_the_cash_structuring_sentence_matches_the_spec()                # the exact es-MX sentence above
def test_every_kind_renders_in_both_locales()                            # one hand-built definition per kind against tests/golden/monitoring/rules/render/<kind>.<locale>.txt
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_jurisdictions.py tests/modules/monitoring/rules --no-cov -q` → `ImportError`.
- [ ] **Step 3: Implement** the loader, the models, the renderer and the contract; export `JurisdictionLists`, `load_jurisdictions` from `xolotl.platform.policy`.
- [ ] **Step 4: Run to verify pass**, then `make lint types vulture importlint` (the new contract passes with the modules that exist), then `make test`.
- [ ] **Docs:** README "Policy packs, units and calendar" gains a paragraph on `_jurisdictions.yaml` (the four sets, `as_of`, the hash recorded in every hit).
- [ ] **Step 5: Commit** `feat(monitoring): jurisdiction lists loader and typed rule definitions with a rendered sentence`.

---

### Task B2: The pure evaluator: the seven kinds

Depends on **B1** (A7's `aggregates/store.py` gives `AggregateValue` and `AggregateKey`, `aggregates/declarations.py` gives `matches`). Pure code: no session, no tables. Can run beside B3 and B4.

**Files:**
- Create: `src/xolotl/modules/monitoring/rules/kinds.py`
- Modify: `.importlinter` (see the note from B1)
- Test: `tests/modules/monitoring/rules/conftest.py` (the fake reader and `enriched(**overrides)` builder), `tests/modules/monitoring/rules/test_kinds.py`

**Note from B1 (import contract).** import-linter refuses a source module that does not exist, so B1's `monitoring-evaluator-is-pure` lists only `rules.definitions` and `rules.render`. Add `xolotl.modules.monitoring.rules.kinds` to its `source_modules` in `.importlinter`, with one `ignore_imports` line per allowed `xolotl.platform` module it actually imports (`xolotl.modules.monitoring.rules.kinds -> xolotl.platform.<module>`); an ignore line that matches nothing fails the gate, so add exactly the lines your imports need.

**Note from B1 (choices this task must know).**
- `params.lists` is one comma-separated `str` (`"FATF_BLACK,FATF_GREY"`), not a list; `resolve_definition` resolves its names when the rule set loads, so an unknown name or an empty set (`TAX_HAVEN`, Open question 1) is a load error.
- A `THRESHOLD` rule may omit `threshold` when `on.counterparty_screening_status` is set or it has a `party_screening_status` or `pep_status_not` param.
- A param's type is inferred from its value (`bool`, `int`, a decimal string, else `str`) and the known names have fixed types (`_PARAM_TYPES`: `threshold`, `near_ratio`, `sum`, `ratio`, `history_ratio` are decimals; `min_count`, `count`, `new_account_days`, `dormant_days` integers; `window`, `lists`, `outcome` and the two gates strings). A float anywhere in the document is refused, so YAML writes every decimal quoted (`"5000"`, `"0.70"`).
- A `window` param is an A7 `DURATION` window (`P[nM][nD][TnH]`, `PT1H`..`P6M`, months not mixed with days or hours); a `VELOCITY` window lies within `PT1H`..`P7D`, and a `STRUCTURING` window must equal the window of its `near_count`/`near_sum` declarations.
- `evidence.transactions_window` (and `dedup.window`) accepts `MONTH` besides an ISO duration.
- An `AGGREGATE` rule needs at least one condition, and aggregate names are unique within a definition.
- `with_params` coerces an `int` to `Decimal` for a decimal param.
- `resolve_definition` sets `lists_hash` on every declaration, whether or not its filter names a list.
- `unit_key` reads `<unit>/<ConversionRule>` (`USD/PRIOR_BANKING_DAY`); a literal country or region code that is not ISO 3166-1 alpha-2 / ISO 3166-2 (a list name written without `$`) fails at load.

**Note from B3 (the profile declarations are peso unit amounts).** `DECLARED_VS_ACTUAL_VOLUME.yaml` declares `mtd_in`, `mtd_out`, `trailing_in` and `trailing_out` as SUM of `UNIT_AMOUNT` in `MXN/SAME_DAY`, not `AMOUNT_MINOR` (which added MXN and USD minor units together). `PROFILE_DEVIATION` therefore reads `mtd` in pesos (a `Decimal` in major units): against the declaration it compares with `declared_profile.monthly_*_minor` converted to pesos (divided by 100, the MXN exponent), gated on `declared_profile.currency == "MXN"` (4c finding 12 refuses any other) rather than on the transaction's currency; the history branch compares like with like and needs no change. Caveat for the orchestrator: on `main`, A6 records a USD transaction's peso leg under `MXN/PRIOR_BANKING_DAY` and no `MXN/SAME_DAY` (`test_a_usd_transaction_converts_through_the_fix_first` asserts its absence), so a USD transaction contributes nothing to these sums until enrichment records `MXN/SAME_DAY` for every transaction as spec Decision 19 says (reconciliation finding 27).

**Interfaces:**

Consumes: B1's models, `EnrichedTransaction`, `KeyKind` (A3), `AggregateValue`, `AggregateKey` (A7 `store`), `matches`, `AggregateDeclaration` (A7 `declarations`), `EffectivePolicy` (`kyc.tiers`, `units.tier_limit_rule`, `country`), `JurisdictionLists`, `iso_duration_days`.

Produces (`rules/kinds.py`):

```python
class Incomplete: ...                                     # singleton INCOMPLETE: the window's history is not backfilled (spec §8.4)
INCOMPLETE = Incomplete()
class AggregateReader(Protocol):
    def read(self, name: str) -> AggregateValue | Incomplete       # by the declaration's name, for the key and window ending at ctx.txn.occurred_at
@dataclass(frozen=True, slots=True)
class DeclaredProfile: monthly_inflow_minor: int | None; monthly_outflow_minor: int | None; currency: str
@dataclass(frozen=True, slots=True)
class EvalProfile:
    tier: str | None; risk_rating: str | None; monitoring_profile: str; pep_status: str      # PepStatus value
    party_screening_status: str | None                       # SubjectStatus value or None (never screened)
    relationship_started_at: datetime | None; declared_profile: DeclaredProfile | None; history_months: int
@dataclass(frozen=True, slots=True)
class EvalContext:
    txn: EnrichedTransaction; aggregates: AggregateReader; profile: EvalProfile; policy: EffectivePolicy; lists: JurisdictionLists; now: datetime
@dataclass(frozen=True, slots=True)
class AggregateSnapshot: value: Decimal; unit: str | None; window: str; count: int
@dataclass(frozen=True, slots=True)
class Firing:
    rule_code: str; key_kind: KeyKind; key: str; window_start: datetime; window_end: datetime
    severity: Severity; outcome: HitOutcome; aggregates: Mapping[str, AggregateSnapshot]; params: Mapping[str, str]; explanation: str
def applies(definition: RuleDefinition, ctx: EvalContext) -> bool
    # family ALERT; matches(definition.on, ctx.txn); ctx.txn.key_for(definition.key_kind) is not None; segment: each non-empty list contains the profile's value (tier, risk_rating, monitoring_profile)
def evaluate(definition: RuleDefinition, ctx: EvalContext) -> Firing | None
    # applies() first; then the kind; any declared aggregate the kind reads that is INCOMPLETE -> None (never a hit from a partial window);
    # window_end = txn.occurred_at; window_start = window_end - the kind's window (THRESHOLD, TIER_LIMIT: the month start for TIER_LIMIT, occurred_at for THRESHOLD);
    # params rendered as strings; explanation = render(definition, "es-MX"); key = txn.key_for(definition.key_kind)
```

Kind semantics, exactly spec §9.1:
- `THRESHOLD`: `txn.unit_amounts[unit_key].amount >= params.threshold` when `threshold` is present; without a threshold any matching transaction fires. Three profile gates in `on` are evaluated here because `TxnFilter` has no profile fields: a definition whose `params` has `party_screening_status` (a `str` param) fires only when `profile.party_screening_status` equals it; `pep_status_not: "NONE"` fires only when `profile.pep_status != "NONE"`. Both are plain `Param[str]` entries so no new filter type is needed.
- `AGGREGATE`: every `Condition` holds: `threshold` form compares `read(aggregate).value` with `params[threshold].value` by `op`; ratio form compares with `ratio × read(ratio_of).value` (a ratio against a zero denominator never fires).
- `STRUCTURING`: declarations `near_count` (COUNT over `[near_ratio × threshold, threshold)` in `unit_key`) and `near_sum` (SUM, same band); fires when `near_count.count >= min_count` and `near_sum.value >= threshold`.
- `VELOCITY`: `count` over `window >= params.count` or `sum >= params.sum` (whichever is declared); `new_account_days` gate: `relationship_started_at` is None or older than N days → no firing; `dormant_days` gate: declaration `last_seen` (LAST_SEEN over `P6M` ending before the window) older than N days or absent with `history_months >= 6` → reactivation, else no firing.
- `HIGH_RISK_JURISDICTION`: codes = `lists.resolve_countries(params.lists.split(","))`; fires when `txn.counterparty_country` or `txn.geo_country` is in codes and the amount in `unit_key >= threshold`, or when the declared `to_listed_sum`/`to_listed_count` over the window reaches `threshold`/`count`.
- `PROFILE_DEVIATION`: declarations `mtd_in`, `mtd_out` (SUM, AMOUNT_MINOR, MONTH) and `trailing_in`, `trailing_out` (SUM, AMOUNT_MINOR, P6M); against the declaration: `mtd / declared_profile.monthly_*_minor >= ratio` (same currency, else no firing); against history: `history_months >= 3` and `mtd / ((trailing − mtd) / history_months_in_window) >= history_ratio`; no declaration and under three months → None.
- `TIER_LIMIT`: `tier = profile.tier`; `limit = policy.kyc.tiers[tier].limits.monthly_ops` (None → no firing); `rule = policy.units.tier_limit_rule.value`; the definition's declared `month_in` (SUM, UNIT_AMOUNT, RELATIONSHIP, MONTH, direction IN, with a static `unit_key`: B3's tier files declare `UDI/LAST_DAY_PREV_MONTH`), read by name like every other aggregate; no firing when `f"{limit.unit}/{rule}"` differs from the declared `unit_key` (the snapshot and the template disagree: a pack change needs a template version); fires when `month_in.value > limit.amount` (the sum already includes the transaction, spec §8.5); `outcome = params.outcome`, severity from the definition (HIGH), `window_start` = the month's first instant in Mexico City.

- [ ] **Step 1: Write the failing tests** (`conftest.py`: `FakeReader(values: Mapping[str, AggregateValue | Incomplete])`, `enriched(**overrides) -> EnrichedTransaction` with a MXN deposit of 1,000 on 2026-12-10 10:00 Mexico City, `unit_amounts` for `USD/PRIOR_BANKING_DAY`, `UDI/SAME_DAY`, `UDI/LAST_DAY_PREV_MONTH`, `UMA/SAME_DAY`, `MXN/SAME_DAY` at fixed rates (18.0, 8.6, 8.6, 117.31, 1), `profile(**overrides)`, `itf_policy()` from `both_packs()["MX/ITF-IFPE"]`, `lists()` from the shipped file, and one hand-built definition per kind):

```python
def test_a_threshold_fires_at_the_threshold_and_not_below()              # 5,000 USD on, 4,999.99 off; window_start == window_end == occurred_at
def test_a_threshold_profile_gate_reads_the_profile()                    # party_screening_status "TRUE_MATCH" fires only with that status; pep gate
def test_a_rule_does_not_apply_outside_its_segment_or_filter()           # segment tier ["N1"] with profile tier "N2" -> None; on.instrument CASH with a TRANSFER -> None
def test_an_incomplete_aggregate_never_fires()                           # FakeReader returns INCOMPLETE for near_sum -> None
def test_structuring_needs_the_count_and_the_sum()                       # count 3 sum 4,800 USD with threshold 5,000 -> None; sum 5,100 -> Firing with aggregates near_count, near_sum
def test_an_aggregate_rule_evaluates_every_condition()                   # funnel: distinct 10 and sum 5,000 -> fires; distinct 9 -> None
def test_a_ratio_condition_compares_against_another_aggregate()          # out 1,800 / in 2,000 at 0.9 -> fires; in 0 -> None
def test_velocity_gates_on_account_age_and_dormancy()                    # started 10 days ago -> fires with new_account_days 30; 40 days -> None; dormant_days 180 with last_seen 200 days ago -> fires
def test_high_risk_jurisdiction_resolves_list_names()                    # counterparty_country "IR" with lists "FATF_BLACK,FATF_GREY" and 1,000 USD -> fires; "MX" -> None
def test_profile_deviation_is_silent_without_a_declaration_and_history() # declared None, history_months 2 -> None; declared 100,000 minor, mtd 250,000 -> fires with ratio 2.0
def test_tier_limit_reads_the_snapshots_tier_and_includes_the_transaction()  # N1, month_in 750.000001 UDI -> BLOCK; exactly 750 -> None (the limit must not be exceeded, spec §12.2); N3 -> None; tier None -> None; a snapshot whose limit unit or tier_limit_rule differs from the declared unit_key -> None
def test_a_firing_carries_rendered_params_and_the_explanation()          # params {"threshold": "5000", ...}; explanation == definition.render("es-MX")
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/rules/test_kinds.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates (import-linter proves purity), then `make test`.
- [ ] **Step 5: Commit** `feat(monitoring): pure evaluator for the seven alert kinds`.

---

### Task B3: The Mexico typology library files, the loader and `rules validate`

Depends on **B1**. Pure code plus pack content: no tables. Runs beside B2 and B4. Touches `src/xolotl/cli.py` (a new `rules_app`).

**Files:**
- Create: `policy-packs/MX/_typologies/{CASH_STRUCTURING,RAPID_PASS_THROUGH,NEW_ACCOUNT_VELOCITY,MANY_TO_ONE_FUNNEL,DECLARED_VS_ACTUAL_VOLUME,HIGH_RISK_JURISDICTION_TRANSFER,CRYPTO_OFF_RAMP,SELF_CUSTODY_OUTFLOW,BORDER_ZONE_CASH,ROUND_AMOUNT_FX_LAYERING,DORMANT_REACTIVATION,COUNTERPARTY_TRUE_MATCH,BLOCKED_PERSON_PARTY,PEP_LARGE_OPERATION,TIER_N1_MONTHLY_OPS,TIER_N2_MONTHLY_OPS}.yaml`, `src/xolotl/modules/monitoring/rules/library.py`
- Modify: `src/xolotl/cli.py`, `tests/test_cli.py`, `tests/test_policy_packs_tree.py` (the tree test learns `_typologies/`), `.importlinter` (see the note from B1)
- Test: `tests/modules/monitoring/rules/test_library.py`, `tests/golden/monitoring/rules/render/<CODE>.es-MX.txt` and `.en.txt` (16 each)

**Note from B1 (import contract).** import-linter refuses a source module that does not exist, so B1's `monitoring-evaluator-is-pure` lists only `rules.definitions` and `rules.render`. Add `xolotl.modules.monitoring.rules.library` to its `source_modules` in `.importlinter`, with one `ignore_imports` line per allowed `xolotl.platform` module it actually imports (`xolotl.modules.monitoring.rules.library -> xolotl.platform.<module>`); an ignore line that matches nothing fails the gate, so add exactly the lines your imports need.

**Note from B1 (choices this task must know).**
- `params.lists` is one comma-separated `str` (`"FATF_BLACK,FATF_GREY"`), not a list; `resolve_definition` resolves its names when the rule set loads, so an unknown name or an empty set (`TAX_HAVEN`, Open question 1) is a load error.
- A `THRESHOLD` rule may omit `threshold` when `on.counterparty_screening_status` is set or it has a `party_screening_status` or `pep_status_not` param.
- A param's type is inferred from its value (`bool`, `int`, a decimal string, else `str`) and the known names have fixed types (`_PARAM_TYPES`: `threshold`, `near_ratio`, `sum`, `ratio`, `history_ratio` are decimals; `min_count`, `count`, `new_account_days`, `dormant_days` integers; `window`, `lists`, `outcome` and the two gates strings). A float anywhere in the document is refused, so YAML writes every decimal quoted (`"5000"`, `"0.70"`).
- A `window` param is an A7 `DURATION` window (`P[nM][nD][TnH]`, `PT1H`..`P6M`, months not mixed with days or hours); a `VELOCITY` window lies within `PT1H`..`P7D`, and a `STRUCTURING` window must equal the window of its `near_count`/`near_sum` declarations.
- `evidence.transactions_window` (and `dedup.window`) accepts `MONTH` besides an ISO duration.
- An `AGGREGATE` rule needs at least one condition, and aggregate names are unique within a definition.
- `with_params` coerces an `int` to `Decimal` for a decimal param.
- `resolve_definition` sets `lists_hash` on every declaration, whether or not its filter names a list.
- `unit_key` reads `<unit>/<ConversionRule>` (`USD/PRIOR_BANKING_DAY`); a literal country or region code that is not ISO 3166-1 alpha-2 / ISO 3166-2 (a list name written without `$`) fails at load.
- `definition_hash` normalises decimals as A7's `_canonical_value` does (`format(value.normalize(), "f")`), so `"5000"` and `"5000.00"` hash alike; `tests/golden/monitoring/rules/hashes.yaml` pins hashes of that spelling.

**Interfaces:**

Produces (`rules/library.py`):

```python
TYPOLOGIES_DIR = Path("policy-packs/MX/_typologies")
@dataclass(frozen=True, slots=True)
class TypologyFile:
    code: str; version: int; country_code: str; packs: tuple[str, ...]; definition: RuleDefinition; definition_hash: bytes; source_path: str
    # code, family, kind, title, typology are the definition's
def read_typology(path: Path) -> TypologyFile      # file = {version, country_code, packs, definition}; definition.code must equal the file stem; family ALERT only
def read_library(directory: Path = TYPOLOGIES_DIR) -> list[TypologyFile]   # *.yaml sorted by code; a duplicate code or a validation error names the file
```

The sixteen files carry exactly spec §9.3's parameters, defaults and bounds, `unit_key = "USD/PRIOR_BANKING_DAY"` for USD thresholds, `typology` tags, `packs` (`MX/ITF-IFPE` for all; `MX/LFPIORPI-AVI` too for the four crypto, two screening and the PEP rules; the two tier rules `MX/ITF-IFPE` only), severity (`CRITICAL` for `COUNTERPARTY_TRUE_MATCH` and `BLOCKED_PERSON_PARTY`, `HIGH` for the tier rules and `PEP_LARGE_OPERATION`, `MEDIUM` otherwise), `dedup` (`RELATIONSHIP`, window = the rule's window or `P1D` for thresholds, cooldown `P7D`; tier rules `MONTH` and `P0D`) and `evidence` (the window's matching transactions and every declared aggregate). The three profile-gated rules use the `Param[str]` gates of B2 (`party_screening_status: {value: "TRUE_MATCH", choices: ["TRUE_MATCH"]}`, `pep_status_not: {value: "NONE", choices: ["NONE"]}`); `COUNTERPARTY_TRUE_MATCH` uses `on.counterparty_screening_status: ["TRUE_MATCH"]`. `CASH_STRUCTURING`: `threshold {value: "5000", min: "1000", tighten: LOWER, unit: USD}`, `near_ratio {value: "0.70", min: "0.50", max: "0.95"}`, `window {value: "P7D"}`, `min_count {value: 3, min: 2}`. `TIER_N1_MONTHLY_OPS`: `outcome {value: BLOCK, choices: [BLOCK, REVIEW]}`; N2 `REVIEW`. Both tier files declare `month_in` statically (SUM, UNIT_AMOUNT, `unit_key: UDI/LAST_DAY_PREV_MONTH`, RELATIONSHIP, MONTH, direction IN), the ITF pack's `kyc.tiers[].limits.monthly_ops.unit` under `units.tier_limit_rule`, so `RuleSet.declarations`, B6's `ensure_declarations` and C5's seeding see it like any declaration (reconciliation finding 24).

CLI (`src/xolotl/cli.py`): `rules_app = typer.Typer(no_args_is_help=True, help="Monitoring rule templates and tenant rules")`, `app.add_typer(rules_app, name="rules")`; `rules validate [path]` reads the library (default `TYPOLOGIES_DIR`) without a database and prints one line per template `"<code> v<n> <kind> <hash[:12]> <packs>"`, exit 1 with the file and the error on a bad file. B5 adds `load`, `show`, `seed-tenant`; B8 adds `simulate`, `activate`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_library.py
def test_the_shipped_library_has_the_sixteen_templates_of_the_spec()     # codes == the table of spec §9.3; kinds and typologies per row; every params default within its bounds
def test_cash_structuring_carries_the_spec_bounds()                      # threshold 5000 LOWER min 1000; near_ratio 0.70 in [0.50, 0.95]; window P7D; min_count 3
def test_the_tier_templates_read_nothing_but_the_outcome()               # params == {"outcome"}; kind TIER_LIMIT; sync True; dedup MONTH; aggregates == [month_in: SUM UNIT_AMOUNT UDI/LAST_DAY_PREV_MONTH, RELATIONSHIP, MONTH, direction IN]
def test_a_file_whose_code_differs_from_its_name_is_refused(tmp_path)
def test_every_template_renders_its_golden_sentence()                    # both locales, unified diff on mismatch
def test_hashes_are_pinned()                                             # tests/golden/monitoring/rules/hashes.yaml (code -> version -> hex), as tests/golden/packs/hashes.yaml

# tests/test_cli.py (append)
def test_rules_validate_lists_the_library_without_a_database()           # exit 0, 16 lines; a bad file in tmp_path -> exit 1 naming it
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/rules/test_library.py tests/test_cli.py -k "library or rules_validate" --no-cov -q`.
- [ ] **Step 3: Implement** the sixteen files, the loader and the command.
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README "Policy packs, units and calendar" gains "Typology library" (where the files live, `xolotl rules validate`, versions are immutable); `policy-packs/MX/_typologies/README.md` (one paragraph: how to add a template, bounds and `tighten`).
- [ ] **Step 5: Commit** `feat(monitoring): the Mexico typology library as pack content with its loader`.

---

### Task B4: Migration 0013, models, enums, events and isolation factories

Depends on **A2** (migration 0012; the models `Transaction`, `Account`, `Counterparty`; the factory helpers `_transaction`, `_counterparty`, `_customer_relationship`; the contract `screening-never-imports-monitoring`). No console. B1 is not needed: the models hold text, bytes and JSON.

**Files:**
- Create: `src/xolotl/migrations/versions/0013_rules_captures.py`, `src/xolotl/modules/monitoring/rules/{enums,models}.py`, `src/xolotl/modules/monitoring/captures/{__init__,enums,models}.py`
- Modify: `src/xolotl/modules/monitoring/events.py` (A4's file; append), `src/xolotl/models.py`, `src/xolotl/platform/tenancy/registry.py`, `src/xolotl/modules/screening/counterparty/models.py` (A2's file; + `CounterpartyNameIndex`), `src/xolotl/modules/screening/engine/models.py` (`ScreeningSubject.counterparty_id`, `SUBJECT_KIND_CHECK`), `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`
- Test: `tests/modules/monitoring/rules/test_migration_0013.py`

**Interfaces:**

Consumes: `TenantTableMixin`, `tenant_table_args`, `tenant_fk`, `enable_rls`, `grant_shared`, `enum_column`, `Base`, tables `party`, `customer_relationship`, `staff_user`, `policy_snapshot`, `transaction`, `counterparty`, `screening_subject`.

Produces:

```python
# rules/enums.py (every value equals its name)
class RuleVersionStatus(StrEnum): DRAFT; SIMULATED; IN_REVIEW; APPROVED; ACTIVE; RETIRED
class SimulationStatus(StrEnum): QUEUED; RUNNING; COMPLETED; FAILED
class HitTransactionRole(StrEnum): TRIGGER; WINDOW; COOLDOWN
class RuleAction(StrEnum): EDIT; SIMULATE; SUBMIT; APPROVE; REJECT; ACTIVATE; RETIRE
class RuleBlockedReason(StrEnum): STATE; ROLE_REQUIRED; NOT_AUTHOR; SAME_AUTHOR; SIMULATION_MISSING; SIMULATION_STALE; PARAM_OUT_OF_BOUNDS
class NoticeSource(StrEnum): DECIDE; PROCESS
# captures/enums.py
class CaptureKind(StrEnum): THRESHOLD; ACCUMULATION; FEE_THRESHOLD
class CaptureGroupStatus(StrEnum): OPEN; CROSSED; SUPERSEDED
class ReportableEventStatus(StrEnum): CAPTURED; INCLUDED; VOIDED; SUPERSEDED
class CapturePeriodStatus(StrEnum): OPEN; CLOSED; REOPENED

# rules/models.py: column for column spec §5.1 and the 4b block of §5.2; arrays ARRAY(Text); hashes LargeBinary; jsonb JSONB
class RuleTemplate(Base); class RuleTemplateVersion(Base)              # shared: no tenant_id
class MonitoringRule; class MonitoringRuleVersion; class RuleSimulation; class RuleAssignment; class RuleHit; class RuleHitTransaction; class TierLimitNotice
# captures/models.py
class CaptureGroup; class ReportableEvent; class CapturePeriod
# screening/counterparty/models.py (append)
class CounterpartyNameIndex                                            # as PartyNameIndex: norm_name, tokens, phonetic_keys, matcher_version, updated_at

# events.py (append; 4a's EVENT_SOURCE and publish_monitoring_event)
RULE_HIT_CREATED = "monitoring.rule_hit_created.v1"; TIER_LIMIT_REACHED = "monitoring.tier_limit_reached.v1"
REPORTABLE_EVENT_CAPTURED = "monitoring.reportable_event_captured.v1"; REPORTABLE_EVENT_SUPERSEDED = "monitoring.reportable_event_superseded.v1"
CAPTURE_PERIOD_CLOSED = "monitoring.capture_period_closed.v1"; CAPTURE_PERIOD_CHANGED = "monitoring.capture_period_changed.v1"
RULE_VERSION_ACTIVATED = "monitoring.rule_version_activated.v1"; RULE_VERSION_RETIRED = "monitoring.rule_version_retired.v1"
SIMULATION_COMPLETED = "monitoring.simulation_completed.v1"
RULES_STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (RULE_HIT_CREATED, REPORTABLE_EVENT_CAPTURED, REPORTABLE_EVENT_SUPERSEDED, CAPTURE_PERIOD_CLOSED,
    CAPTURE_PERIOD_CHANGED, RULE_VERSION_ACTIVATED, RULE_VERSION_RETIRED, SIMULATION_COMPLETED)   # a separate tuple beside 4a's STAFF_ONLY_EVENT_TYPES; B7's register_rules registers it
```

Migration 0013 (order: `rule_template`, `rule_template_version`, `monitoring_rule`, `monitoring_rule_version`, `rule_simulation`, the `fk_rule_version_simulation` constraint, `rule_assignment`, `rule_hit`, `rule_hit_transaction`, `capture_group`, `reportable_event`, `tier_limit_notice`, `capture_period`, the `screening_subject` changes, `fk_counterparty_subject`, `counterparty_name_index`; downgrade in reverse):
- Every column, check, unique key and index of spec §5.1 and the 4b block of §5.2: `ux_rule_version_active`, `ix_rule_hit_queue`, `ix_rule_hit_party`, `ix_rule_hit_rule_period`, `ux_capture_group_open`, `ix_reportable_event_period`, `ix_cni_phonetic`, `ix_cni_trgm` (GIN, `gin_trgm_ops`), the two CHECKs on `monitoring_rule_version`, the `screening_subject` constraint `ck_screening_subject_kind_input` dropped and re-created with the `COUNTERPARTY` branch, `uq_screening_subject_counterparty`, `fk_screening_subject_counterparty`. Add `ix_reportable_event_transaction (tenant_id, transaction_id)` and `ix_rule_hit_transaction_txn (tenant_id, transaction_id)` for the transaction detail's hit and capture cards (spec §15 names neither; both are cheap).
- Explicit FK names where the convention would pass 63 bytes, pinned by the migration test: `fk_rule_version_retired_by`, `fk_rule_version_simulation`, `fk_rule_hit_first_transaction`, `fk_rule_hit_transaction_txn`, `fk_reportable_event_supersedes`, `fk_reportable_event_superseded_by`, `fk_reportable_event_policy_snapshot`, `fk_capture_group_superseded_by`.
- `grant_shared("rule_template")`, `grant_shared("rule_template_version")` (SELECT for the app role); `enable_rls` with the default grant on the eleven tenant tables.
- `SHARED_TABLES` gains the two template tables. `models.py` imports `monitoring_rules_models`, `monitoring_captures_models` (and the counterparty models file already imported by 4a).
- `.importlinter`: nothing to add; A2's `screening-never-imports-monitoring` already covers `CounterpartyNameIndex` and the `screening_subject` change.

Isolation factories (one each, registered in `register_all()`): `monitoring_rule` over `_staff_user`; `monitoring_rule_version` (status `DRAFT`, `template_version_id = NULL`: the app role cannot insert a template row) over a rule and `_staff_user`; `rule_simulation` over a version and `_staff_user`; `rule_assignment` over a version; `rule_hit` over a version, an assignment, `_party`, `_customer_relationship`, `_policy_snapshot` and **4a's** `_transaction`; `rule_hit_transaction` over a hit and `_transaction`; `tier_limit_notice` over `_customer_relationship`; `capture_group`; `reportable_event` over `_transaction`, `_party`, `_customer_relationship`, `_policy_snapshot`; `capture_period`; `counterparty_name_index` over **A2's** `_counterparty`. `_customer_relationship(session, tenant_id) -> CustomerRelationship` is A2's helper.

- [ ] **Step 1: Write the failing tests** (`test_migration_0013.py`, with the `admin_engine` inspector as `tests/modules/screening/engine/test_migration_0010.py` does):

```python
def test_the_thirteen_tables_exist_and_the_tenant_ones_force_rls(admin_engine)   # two shared without RLS, eleven with
def test_the_composite_foreign_keys_keep_references_inside_the_tenant(admin_engine)   # pins every FK name and column pair above
def test_the_approver_must_differ_from_the_author(engine, tenant)               # approved_by == author_id -> IntegrityError
def test_an_approved_or_active_version_needs_an_approver_and_a_simulation(engine, tenant)
def test_one_active_version_per_rule(engine, tenant)                            # second ACTIVE -> IntegrityError on ux_rule_version_active
def test_a_hit_is_unique_per_version_and_dedup_key(engine, tenant)
def test_one_event_per_transaction_per_capture(engine, tenant)
def test_one_open_group_per_capture_key_and_window(engine, tenant)
def test_the_subject_check_admits_counterparty_subjects(engine, tenant)         # COUNTERPARTY with counterparty_id and no party or input -> ok; with a party -> IntegrityError
def test_the_name_index_has_its_gin_indexes(admin_engine)                        # ix_cni_phonetic, ix_cni_trgm with gin_trgm_ops
def test_the_app_role_reads_but_never_writes_templates(engine)                  # INSERT into rule_template -> InsufficientPrivilege
def test_downgrade_removes_the_tables_and_restores_the_subject_check(admin_engine)   # alembic downgrade 0012_transactions then upgrade head
```

The isolation harness discovers the eleven tenant tables and fails until the factories exist; `test_schema_sync.py` fails until the models match; `test_migration_chain.py` gets `"0013_rules_captures"`; `tests/isolation/test_structural.py` checks the two shared tables.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/rules/test_migration_0013.py tests/isolation tests/platform/test_schema_sync.py tests/platform/test_migration_chain.py --no-cov -q`.
- [ ] **Step 3: Implement** the enums, models, events constants, migration, factories and registry.
- [ ] **Step 4: Run to verify pass**, then `make lint types vulture importlint`, then `make test`. Vulture: an enum module with members unused until B6 or B10 is left as is only when a test references the members; otherwise leave the member out until its first user lands.
- [ ] **Step 5: Commit** `feat(monitoring): migration 0013 with rule templates, versions, hits, captures and the counterparty name index`.

---

### Task B5: Templates in the database, tenant rules and versions with bounds, `rules load`, `rules show`, `rules seed-tenant`

Note from A9: `agg_declaration` is append-only. Never delete from it, not even to replace a declaration, or when a rule, rule set or assignment is retired: a drain row compares the declarations' cheap version (`declarations_version`: count and max `created_at`, `monitoring/aggregates/backfill.py`) with the one its page read, and a delete followed by an insert can leave both unchanged, so the drain would never add rows to the new declaration. An unused declaration simply stays.

Depends on **B3** (the library) and **B4** (the tables). It appends to A4's `monitoring/errors.py` without an edge to A4: the wave order (A4 merges three waves earlier) and finding 17's first-to-merge rule cover it. No console. Touches `src/xolotl/cli.py` (`rules_app`, `db upgrade`, `serve`) and `src/xolotl/platform/config.py`.

**Note from B3 (declarations derived from the params).** Tunable parameters do not move a template's declared aggregates by themselves: `CASH_STRUCTURING`'s band `[3500, 5000)` USD is written out while `near_ratio` and `threshold` are tunable, and a `VELOCITY` `window` param does not change the window of its `COUNT`/`SUM` declarations. `build_definition` rewrites the derived parts of the declarations from the tuned params: for `STRUCTURING`, `near_count`/`near_sum` get `where.amount_gte = near_ratio × threshold`, `where.amount_lt = threshold` and `window = params.window` (and the evidence's `transactions_where`/`transactions_window` follow); for `VELOCITY`, every `COUNT` and `SUM` declaration gets `window = params.window`. New `agg_id`s follow from the changed declarations and B6's `ensure_declarations` backfills them. B1 or B5 adds a validator refusing a version whose band or windows disagree with its params. `DORMANT_REACTIVATION`'s `dormant_days` carries `max: 180`, since `last_seen` looks back `P6M`. B3's `test_the_shipped_declarations_follow_their_params` pins the coupling in the shipped defaults (reconciliation finding 27).

**Files:**
- Create: `src/xolotl/modules/monitoring/rules/templates.py`, `src/xolotl/modules/monitoring/rules/versions.py`
- Modify: `src/xolotl/modules/monitoring/errors.py` (create or append), `src/xolotl/cli.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`
- Test: `tests/modules/monitoring/rules/test_templates.py`, `tests/modules/monitoring/rules/test_versions.py`

**Interfaces:**

Consumes: `read_library`, `TypologyFile` (B3), B4 models, `assignments_in_force` (`xolotl.platform.policy`), `PolicyPackVersion.pack_code` (the assigned pack codes), `bound_context` (`actor_id`, `actor_type`, `roles`), `record`, `ErrorItem`, `get_settings`, the CLI helpers `_cli_actor`, `_cli_errors`, `_resolve_tenant`, `_ensure_key_provider`, the owner engine pattern of `holidays_seed`.

Produces:

```python
# rules/templates.py
@dataclass(frozen=True, slots=True)
class LoadReport: created: tuple[str, ...]; unchanged: tuple[str, ...]        # "CODE v1"
def load_templates(s: Session, files: Sequence[TypologyFile], *, loaded_by: str) -> LoadReport
    # owner-role session (platform context): upsert rule_template (family, kind, title, typology, country_code, packs from the latest version);
    # insert rule_template_version when (code, version) is new; same hash -> unchanged; different hash -> RuleTemplateImmutableError, nothing of the batch written
def template_version(s: Session, code: str, version: int | None = None) -> RuleTemplateVersion   # latest when None; unknown -> RuleTemplateUnknownError
def latest_template_versions(s: Session, *, packs: Collection[str] | None = None) -> list[RuleTemplateVersion]   # one per code, filtered by rule_template.packs && packs
def definition_of(row: RuleTemplateVersion) -> RuleDefinition

# rules/versions.py
class VersionInput(BaseModel): params: dict[str, ParamValue] | None = None; segment: SegmentFilter | None = None; severity: Severity | None = None; dedup: Dedup | None = None
@dataclass(frozen=True, slots=True)
class BoundViolation: param: str; bound: str; limit: str; source: str      # bound in {"min", "max", "choices", "tighten", "type"}; source "template <code> v<n>"
def check_params(template: RuleDefinition, values: Mapping[str, ParamValue]) -> list[BoundViolation]
    # unknown name -> "type"; type mismatch; min/max; choices; tighten LOWER -> value <= template default, RAISE -> value >= default
def build_definition(template: RuleDefinition, body: VersionInput) -> RuleDefinition      # with_params + segment/severity/dedup overrides; violations -> RuleParamOutOfBoundsError(errors=[ErrorItem(loc=("params", name), msg, type="RULE_PARAM_OUT_OF_BOUNDS")])
def create_rule(s: Session, *, template_code: str, body: VersionInput) -> MonitoringRuleVersion
    # writers only (SENIOR_ANALYST, MLRO, ADMIN; else ForbiddenError); unknown template -> RuleTemplateUnknownError (404);
    # an existing rule for the code -> create_version on it (Deviation 6); else insert monitoring_rule (code = template code, created_by = actor)
    # and version 1 (DRAFT, author_id = actor, template_version_id = latest, definition, definition_hash, sync = definition.sync); audit rules.version_created
def create_custom_rule(s: Session, *, definition: RuleDefinition) -> MonitoringRuleVersion
    # settings.monitoring_allow_custom_rules False -> ValidationFailedError("custom rules are disabled"); not ADMIN -> ForbiddenError; template_code NULL
def create_version(s: Session, rule_id: UUID, *, body: VersionInput) -> MonitoringRuleVersion
    # next version number from the latest version's definition (its params are the base; bounds from its template version); DRAFT
def update_version(s: Session, version_id: UUID, *, body: VersionInput) -> MonitoringRuleVersion
    # DRAFT or SIMULATED only (else RuleTransitionNotAllowedError([STATE]), B6's error, created here if B6 has not merged); re-validate; any change -> status DRAFT,
    # simulation_id NULL, definition_hash recomputed; audit rules.version_updated {"changed": [field names]}
def seed_tenant_rules(s: Session, *, author_id: UUID) -> list[MonitoringRuleVersion]
    # packs = codes of the tenant's assignments in force today or later; for each latest template version whose packs intersect and whose code has no rule yet:
    # create_rule with default params, author = author_id; audit rules.seeded {"codes": [...]}

# errors.py
class RuleTemplateUnknownError(NotFoundError): default_code = "RULE_TEMPLATE_UNKNOWN"
class RuleTemplateImmutableError(DomainError): default_code = "RULE_TEMPLATE_IMMUTABLE"; default_detail = "a loaded template version cannot change"
class RuleParamOutOfBoundsError(ValidationFailedError): default_code = "RULE_PARAM_OUT_OF_BOUNDS"; default_detail = "a parameter is outside the template's bounds"
```

CLI (`rules_app`): `rules load [path]` (owner engine, like `holidays seed`; prints one line per created version and a count of unchanged; `db upgrade` and `serve` call `_load_rule_library()` right after `_seed_list_sources()`); `rules show <code> [--version]` (the template's definition as YAML and its sentence in both locales); `rules seed-tenant --tenant <slug> --author <email>` (the author is a staff user of the tenant; prints one line per draft created). Settings: `monitoring_allow_custom_rules`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_templates.py
def test_loading_the_library_twice_creates_once_and_changes_nothing(admin_engine)      # 16 created, then 16 unchanged; rows carry loaded_by, source_path, the pinned hashes
def test_a_changed_version_is_refused_and_nothing_is_written(admin_engine, tmp_path)   # CASH_STRUCTURING v1 with threshold 4000 -> RuleTemplateImmutableError; a new file in the same batch not inserted
def test_a_new_version_of_a_template_updates_the_template_row(admin_engine, tmp_path)  # v2 with another title -> rule_template.title is v2's
def test_templates_are_filtered_by_pack()                                              # packs {"MX/LFPIORPI-AVI"} -> the crypto, screening and PEP templates, no tier template

# test_versions.py
def test_a_rule_is_created_from_a_template_with_default_params(...)                    # version 1 DRAFT, definition_hash == build_definition(...).definition_hash(), audit rules.version_created
def test_params_outside_the_bounds_are_refused_with_the_source(...)                    # threshold 900 -> 422 errors[0].loc == ("params", "threshold"), msg names "min 1000" and "template CASH_STRUCTURING v1"
def test_tighten_lower_refuses_a_looser_threshold(...)                                 # threshold 6000 -> RULE_PARAM_OUT_OF_BOUNDS bound "tighten"
def test_a_choice_param_takes_only_its_choices(...)                                    # TIER_N1 outcome "ALLOW" -> refused; "REVIEW" -> ok
def test_a_second_create_for_the_same_template_is_a_new_version(...)                   # Deviation 6: version 2 on the same rule
def test_updating_a_simulated_version_resets_it_to_draft(...)                          # status SIMULATED with simulation_id -> after PATCH DRAFT, simulation_id None, hash changed
def test_a_version_in_review_cannot_be_edited(...)                                     # 409 RULE_TRANSITION_NOT_ALLOWED blocked_reasons ["STATE"]
def test_only_writers_create_rules(...)                                                # ANALYST -> ForbiddenError; AUDITOR_RO -> ForbiddenError
def test_custom_rules_are_off_by_default_and_admin_only(settings_override)            # flag off -> VALIDATION_FAILED; on + SENIOR_ANALYST -> ROLE_FORBIDDEN; on + ADMIN -> DRAFT with template_code None
def test_seeding_follows_the_tenants_packs(...)                                        # tenant A (ITF EMONEY + AVI CRYPTO) -> 16 drafts; tenant B (ITF) -> 16 minus none; a tenant with AVI only -> 7; seeding twice adds nothing

# tests/test_cli.py (append)
def test_rules_load_show_and_seed_tenant(...)   # load -> "16 created"; load again -> "0 created, 16 unchanged"; show CASH_STRUCTURING prints the sentence; seed-tenant prints the drafts; db upgrade output mentions the library
```

`tests/platform/test_errors.py`: the three rows. `tests/platform/test_config.py`: `monitoring_allow_custom_rules`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/rules/test_templates.py tests/modules/monitoring/rules/test_versions.py tests/test_cli.py -k rules --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README CLI section gains `rules load`, `rules show`, `rules seed-tenant`; the configuration table gains `XOLOTL_MONITORING_ALLOW_CUSTOM_RULES`.
- [ ] **Step 5: Commit** `feat(monitoring): rule templates in the database and tenant rule versions within bounds`.

---

### Task B6: Lifecycle, assignments and the active rule set

Note from A9: `agg_declaration` is append-only. Never delete from it, not even to replace a declaration, or when a rule, rule set or assignment is retired: a drain row compares the declarations' cheap version (`declarations_version`: count and max `created_at`, `monitoring/aggregates/backfill.py`) with the one its page read, and a delete followed by an insert can leave both unchanged, so the drain would never add rows to the new declaration. An unused declaration simply stays.

Depends on **B5**. No console. Touches `src/xolotl/platform/config.py`. Mirrors SP-03's disposition matrix (`screening/review/actions.py`).

**Files:**
- Create: `src/xolotl/modules/monitoring/rules/lifecycle.py`, `src/xolotl/modules/monitoring/rules/assignments.py`
- Modify: `src/xolotl/modules/monitoring/errors.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`
- Test: `tests/modules/monitoring/rules/test_lifecycle.py`, `tests/modules/monitoring/rules/test_assignments.py`

**Interfaces:**

Consumes: B4 models and enums, B5 `check_params`, `definition_of`, `ensure_declarations` (A7), `resolve_definition` and `load_jurisdictions` (B1), `publish_monitoring_event`, `record`, `bound_context`, `Role`, `policy_date` (A6), `clock`, `get_settings`.

Produces:

```python
# errors.py
class RuleTransitionNotAllowedError(DomainError):
    default_code = "RULE_TRANSITION_NOT_ALLOWED"; default_detail = "this transition is not allowed"
    def __init__(self, reasons: Sequence[RuleBlockedReason] = ()) -> None    # self.blocked_reasons = tuple(r.value for r in reasons)

# rules/lifecycle.py
WRITERS = frozenset({Role.SENIOR_ANALYST, Role.MLRO, Role.ADMIN}); APPROVERS = frozenset({Role.MLRO, Role.ADMIN})
@dataclass(frozen=True, slots=True)
class RuleActionSet: allowed: tuple[RuleAction, ...]; blocked: Mapping[RuleAction, tuple[RuleBlockedReason, ...]]
def evaluate_rule_actions(version: MonitoringRuleVersion, *, simulation: RuleSimulation | None, roles: frozenset[Role], user_id: UUID, now: datetime) -> RuleActionSet
    # per action, reasons in the order STATE, ROLE_REQUIRED, NOT_AUTHOR, SAME_AUTHOR, SIMULATION_MISSING, SIMULATION_STALE, PARAM_OUT_OF_BOUNDS:
    # EDIT, SIMULATE: from DRAFT, SIMULATED; WRITERS
    # SUBMIT: from SIMULATED; WRITERS; NOT_AUTHOR when user_id != author_id; SIMULATION_MISSING when simulation is None or not COMPLETED;
    #         SIMULATION_STALE when finished_at older than monitoring_simulation_max_age_days or simulation.definition_hash != version.definition_hash
    # APPROVE: from IN_REVIEW; APPROVERS; SAME_AUTHOR when user_id == author_id; the two simulation reasons re-checked
    # REJECT: from IN_REVIEW; APPROVERS
    # ACTIVATE: from APPROVED; APPROVERS; PARAM_OUT_OF_BOUNDS when check_params(template, version params) is non-empty
    # RETIRE: from ACTIVE; APPROVERS
def submit_version(s, version_id) -> MonitoringRuleVersion          # IN_REVIEW, submitted_at; audit rules.version_submitted
def approve_version(s, version_id) -> MonitoringRuleVersion         # APPROVED, approved_by, approved_at; audit rules.version_approved
def reject_version(s, version_id, *, reason: str) -> MonitoringRuleVersion   # DRAFT; reason 1..500 chars in the audit detail {"reason"}; rules.version_rejected
def activate_version(s, version_id, *, effective_from: date, product: str | None, queue: PostgresJobQueue) -> MonitoringRuleVersion
    # effective_from < today -> ValidationFailedError; lock the rule's versions FOR UPDATE; the current ACTIVE version (if any): RETIRED, retired_at, retired_by_version_id,
    # its assignment effective_to = effective_from - 1 day, publish RULE_VERSION_RETIRED; insert rule_assignment (product, segment_filter = definition.segment, effective_from,
    # approved_by = actor); ACTIVE, activated_at; ensure_declarations(s, resolved declarations, queue=queue); clear_rule_cache(); audit rules.version_activated
    # {"assignment_id", "effective_from", "product", "category": "CHANGE_POLICY"}; publish RULE_VERSION_ACTIVATED {"rule_id", "rule_version_id", "assignment_id"}
def retire_version(s, version_id) -> MonitoringRuleVersion           # RETIRED, retired_at; the open assignment effective_to = today; clear_rule_cache(); audit; publish RULE_VERSION_RETIRED
# every transition: staff only (else ForbiddenError); evaluate_rule_actions -> RuleTransitionNotAllowedError(blocked[action]) when not allowed

# rules/assignments.py
@dataclass(frozen=True, slots=True)
class LoadedRule:
    rule_id: UUID; rule_version_id: UUID; rule_code: str; assignment_id: UUID; definition: RuleDefinition   # resolve_definition(definition, lists): filters resolved, lists_hash set
    segment: SegmentFilter | None; sync: bool; definition_hash: bytes; typology: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class RuleSet:
    on: date; product: str | None; rules: tuple[LoadedRule, ...]; lists: JurisdictionLists
    @property
    def declarations(self) -> tuple[AggregateDeclaration, ...]    # union over rules, deduplicated by agg_id, with list names resolved
    @property
    def sync_rules(self) -> tuple[LoadedRule, ...]
    def ruleset_hash(self) -> bytes                                # sha256 over the sorted definition hashes of the sync rules (C5's txn_decision.ruleset_hash)
def assignments_on(s, *, on: date, product: str | None) -> list[RuleAssignment]
    # effective_from <= on AND (effective_to IS NULL OR effective_to >= on) AND (product IS NULL OR product = :product), joined to versions ACTIVE or RETIRED (Deviation 5)
def active_rules(s, *, on: date, product: str | None) -> RuleSet
    # per-process cache keyed (tenant_id, on, product, max(activated_at, retired_at) of the tenant); entries live monitoring_rule_cache_seconds (0 disables)
def clear_rule_cache() -> None
```

Settings: `monitoring_rule_cache_seconds`, `monitoring_simulation_max_age_days`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_lifecycle.py: the acceptance test of spec §9.4, mirroring tests/modules/screening/review/test_actions.py
STATES = list(RuleVersionStatus); ROLES = [Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO, Role.AUDITOR_RO, Role.ADMIN]
SIMULATIONS = ["none", "fresh", "stale", "other_hash"]
EXPECTED: dict[tuple[RuleVersionStatus, RuleAction], ...]     # written by hand from the spec table, not from the implementation
@pytest.mark.parametrize(("state", "role", "same_user", "simulation", "action"), itertools.product(STATES, ROLES, [False, True], SIMULATIONS, list(RuleAction)))
def test_the_lifecycle_matrix_is_exactly_the_spec(state, role, same_user, simulation, action)
    # allowed iff the spec row allows it; otherwise the blocked reasons equal the oracle's, in order
def test_an_author_submits_and_another_approver_activates(...)              # SENIOR_ANALYST author: simulate (row inserted COMPLETED), submit -> IN_REVIEW; MLRO approve -> APPROVED; activate -> ACTIVE with an assignment; events activated; audit rows with category CHANGE_POLICY
def test_the_author_cannot_approve(...)                                     # 409 blocked_reasons ["SAME_AUTHOR"]
def test_a_stale_simulation_blocks_approval(...)                            # finished_at 31 days ago -> ["SIMULATION_STALE"]; another hash -> the same
def test_a_version_without_a_simulation_cannot_be_submitted(...)            # ["SIMULATION_MISSING"]
def test_activation_retires_the_previous_version_and_closes_its_assignment(...)   # v1 ACTIVE from 2026-11-01; v2 activated effective 2026-12-15 -> v1 RETIRED, retired_by_version_id v2, v1 assignment effective_to 2026-12-14; active_rules on 2026-12-14 has v1, on 2026-12-15 v2
def test_activation_in_the_past_is_refused(...)                             # effective_from yesterday -> VALIDATION_FAILED
def test_retire_closes_the_assignment_today(...)
def test_the_check_refuses_a_same_author_approval_in_sql(engine, tenant)    # raw UPDATE approved_by = author_id -> IntegrityError
def test_activation_ensures_the_declarations(...)                           # agg_declaration rows for the rule's agg_ids (4a's table) and the backfill job enqueued

# test_assignments.py
def test_rules_resolve_by_date_and_product_like_packs(...)                  # product NULL applies to every product; product "EMONEY" not to "CRYPTO"; before effective_from nothing
def test_the_rule_set_deduplicates_declarations_by_agg_id(...)              # two rules with the same in_sum declaration -> one
def test_list_names_are_resolved_at_load(...)                               # on.counterparty_country_in {"$FATF_GREY"} -> the 22 codes in the loaded rule
def test_the_cache_refreshes_on_activation_and_after_the_ttl(fake_monotonic, ...)
def test_the_ruleset_hash_is_order_independent_and_covers_the_sync_rules_only(...)
```

`tests/platform/test_errors.py`: `(RuleTransitionNotAllowedError, 409, "RULE_TRANSITION_NOT_ALLOWED")`. `tests/platform/test_config.py`: the two settings.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/rules/test_lifecycle.py tests/modules/monitoring/rules/test_assignments.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README configuration table (two rows); README "Monitoring" section (created by 4a, or created here) gains "Rule lifecycle" (the table of spec §9.4 in prose, the different-approver rule, the 30-day simulation).
- [ ] **Step 5: Commit** `feat(monitoring): rule lifecycle with four eyes, a fresh simulation and dated assignments`.

---

### Task B7: Hits, dedup, cooldown, evidence, the tier-limit notice, the rules processing hook and staff-only registration

Depends on **B2**, **B6** and **A8** (`ProcessingHook`, `register_processing_hook`, `run_processing`; A6's `enriched_from_row` and A7's store come with it). No console. Touches `src/xolotl/app.py` (`register_rules` after `register_monitoring`). This is the plan's centre (spec §8.5 step 2, §9.5, §9.6, §12.2 asynchronous path).

**Files:**
- Create: `src/xolotl/modules/monitoring/rules/{evaluate,hits,tier_limits,hook,wiring}.py`, `tests/modules/monitoring/fixtures/unit_values_2026.yaml` (UDI, UMA and `USD_MXN_FIX` for July to December 2026 in `load_unit_values`'s format; no FIX row for 12 and 13 December, a weekend; C11 reuses it)
- Modify: `src/xolotl/app.py`
- Test: `tests/modules/monitoring/rules/{test_evaluate,test_hits,test_hook,test_tier_limits}.py`

**Interfaces:**

Consumes: `evaluate`, `applies`, `EvalContext`, `EvalProfile`, `DeclaredProfile`, `Firing`, `INCOMPLETE` (B2), `active_rules`, `RuleSet`, `LoadedRule` (B6), `AggregateStore`, `AggregateKey`, `matches`, `ProcessingContext`, `ProcessingHook`, `register_processing_hook`, `enriched_from_row`, `policy_date`, `Transaction` (4a), `get_party`, `PepStatus`, `CustomerRelationship`, `screening.api.party_view`, `publish_monitoring_event`, `register_staff_only`, `add_iso_duration`, `iso_duration_days`, `load_jurisdictions`.

Produces:

```python
# rules/evaluate.py
def reader_for(store: AggregateStore, txn: EnrichedTransaction, declarations: Mapping[str, AggregateDeclaration]) -> AggregateReader
    # name -> store.read / calendar_read(period_key = txn.period_keys.MONTH or .QUARTER) / accumulator_read by window_kind, key = AggregateKey(decl.key_kind, txn.key_for(decl.key_kind)), at = txn.occurred_at;
    # value.complete False -> INCOMPLETE; reads are memoised per name
def profile_for(s: Session, txn: EnrichedTransaction) -> EvalProfile
    # relationship: account_tier, started_at, declared_profile (getattr with None until 4c); party pep_status (get_party); screening status party_view(s, party_id, staff=True).status;
    # history_months = months between the relationship's earliest PROCESSED occurred_at and txn.occurred_at (0 when none)
def evaluate_rules(ruleset: RuleSet, ctx: EvalContext) -> list[tuple[LoadedRule, Firing]]    # evaluate(rule.definition, ctx) per rule, in rule_code order

# rules/hits.py
def dedup_bucket(txn: EnrichedTransaction, dedup: Dedup) -> str    # "MONTH" -> txn.period_keys.MONTH; else floor(occurred_at UTC, window).isoformat()
def dedup_key_for(firing: Firing, txn: EnrichedTransaction, dedup: Dedup) -> str   # f"{key_kind}:{key}:{bucket}"
def evidence_transactions(s, rule: LoadedRule, firing: Firing, txn: EnrichedTransaction) -> list[Transaction]
    # PROCESSED rows of the firing's key with occurred_at in [occurred_at - evidence.transactions_window, occurred_at), matching evidence.transactions_where, occurred_at DESC, limit 500
def record_firing(s, rule: LoadedRule, firing: Firing, *, txn: EnrichedTransaction, lists_hash: bytes) -> RuleHit | None
    # 1. existing = hit with (rule_version_id, dedup_key): link txn WINDOW (ON CONFLICT DO NOTHING on the link key), fired_count + 1 -> None
    # 2. latest hit of (rule_version_id, key_kind, key) with cooldown_until > occurred_at: link COOLDOWN -> None
    # 3. insert RuleHit ON CONFLICT (tenant_id, rule_version_id, dedup_key) DO NOTHING (a concurrent insert turns into case 1); cooldown_until = occurred_at + dedup.cooldown
    #    (NULL for P0D); evidence = {"v": 1, "aggregates": {name: {"value": str, "unit", "window"}}, "params": firing.params, "explanation", "lists_hash": hex};
    #    policy_snapshot_id = txn.policy_snapshot_id, definition_hash, assignment_id, first_transaction_id/first_occurred_at = txn; links: TRIGGER for txn, WINDOW for evidence_transactions
    #    publish RULE_HIT_CREATED {"hit_id", "rule_version_id", "rule_code", "party_id", "relationship_id", "severity", "outcome", "window_start", "window_end"} key str(party_id)

# rules/tier_limits.py
def notice_tier_limit(s, *, relationship_id: UUID, account_id: UUID | None, tier: str, limit_code: str, period_key: str, outcome: HitOutcome, source: NoticeSource) -> bool
    # INSERT tier_limit_notice ON CONFLICT DO NOTHING; True (and publish TIER_LIMIT_REACHED, key str(relationship_id)) only when inserted; 4c's decide path calls it with DECIDE
LIMIT_CODE_MONTHLY_OPS = "monthly_ops"

# rules/hook.py
class RulesHook:                                   # implements 4a's ProcessingHook
    name = "rules"
    def declarations(self, s, snapshot, *, on, product) -> Sequence[AggregateDeclaration]    # active_rules(s, on=on, product=product).declarations
    def evaluate(self, s, txn, ctx) -> None
        # ruleset = active_rules(s, on=policy_date(txn.occurred_at), product=txn.product); ectx = EvalContext(txn, reader_for(ctx.store, txn, by name), profile_for(s, txn), ctx.snapshot.policy, ruleset.lists, clock.now())
        # for rule, firing in evaluate_rules(...): hit = record_firing(...); TIER_LIMIT firings with a new hit -> notice_tier_limit(source=PROCESS)
        # ctx.late -> reevaluate_later(s, txn, ruleset, ctx)
def reevaluate_later(s, late: EnrichedTransaction, ruleset: RuleSet, ctx: ProcessingContext) -> int
    # longest = max window of the ruleset's declarations; for each PROCESSED row of the same party with occurred_at in (late.occurred_at, late.occurred_at + longest], occurred_at ASC:
    # txn = enriched_from_row(row); evaluate and record as above (dedup makes repeats harmless); returns the number of rows re-evaluated

# rules/wiring.py
def register_rules(*, queue: PostgresJobQueue) -> None
    # for t in RULES_STAFF_ONLY_EVENT_TYPES: register_staff_only(t); register_processing_hook(RulesHook()); B8 adds the simulate handler, B10 the CapturesHook, B11 the period-close handler
```

`src/xolotl/app.py`: `wire_platform` calls `register_rules(queue=queue)` right after 4a's `register_monitoring(...)`.

**Note from B2 (the reader).** B2's `AggregateReader` protocol is `read(name, *, at=None)`, with `None` meaning `txn.occurred_at`: the dormancy gate reads `last_seen` at the last instant before the rule's window. So `reader_for` implements `read(name, *, at=None)`; its memo is keyed by `(name, at)`, not by name alone; `DURATION` declarations pass `at` to `store.read`; `MONTH`, `QUARTER` and `ACCUM` declarations derive their period from `at` rather than from `txn.period_keys`. C5's decide path uses the same reader.

- [ ] **Step 1: Write the failing tests** (fixtures: tenant A with both packs assigned as the exit demo, the library loaded, `CASH_STRUCTURING`, `COUNTERPARTY_TRUE_MATCH` and `TIER_N1_MONTHLY_OPS` activated through B6 with a simulation row inserted directly and an `effective_from` on or before the first fixture (the clock is patched to 2026-06-30 for the activation and to the December week afterwards: `activate_version` refuses a past `effective_from` and `active_rules(on=policy_date(txn.occurred_at))` sees only assignments effective on the transaction's date, reconciliation finding 23); unit values loaded with `load_unit_values(session, Path("tests/modules/monitoring/fixtures/unit_values_2026.yaml"))`, the fixture this task commits; transactions through `ingest_items` and `run_processing`):

```python
# test_evaluate.py
def test_the_reader_picks_the_read_by_window_kind(...)                     # DURATION -> read, MONTH -> calendar_read, ACCUM -> accumulator_read; an unbackfilled declaration -> INCOMPLETE
def test_the_profile_reads_tier_pep_and_screening_status(...)              # N1, pep DOMESTIC, party never screened -> None; declared_profile None before 4c

# test_hits.py
def test_three_near_threshold_deposits_make_one_hit_with_evidence(...)      # 3 cash deposits of 3,600, 3,700, 3,800 USD-equivalent in 7 days -> one rule_hit; links: TRIGGER (the third) + 2 WINDOW; evidence.aggregates near_count 3, near_sum; explanation in es-MX; lists_hash hex; event rule_hit_created once
def test_a_fourth_deposit_in_the_window_links_as_window(...)               # fired_count 2, no second hit, no second event
def test_a_firing_inside_the_cooldown_links_as_cooldown(...)               # day 9 (new dedup bucket, cooldown P7D) -> COOLDOWN link, no hit
def test_replaying_the_same_stream_creates_each_hit_once(...)              # ingest the batch again (duplicates), process -> row counts of rule_hit and rule_hit_transaction unchanged
def test_two_sessions_firing_at_once_create_one_hit(...)                   # two threads record_firing the same firing -> one hit, two links at most
def test_evidence_carries_ids_amounts_and_codes_only(...)                  # no name, account or wallet in evidence; everything_stored holds no counterparty name
def test_an_incomplete_window_fires_nothing_until_the_backfill(...)        # agg_declaration.backfilled_through NULL -> no hit; after the backfill job -> the hit
def test_a_late_row_refires_nothing_already_recorded(...)                  # a 10 December deposit arriving 12 December after the hit -> reevaluate_later returns > 0, hit count unchanged, the late row linked WINDOW

# test_hook.py
def test_the_hook_runs_inside_the_processing_transaction(...)              # a hook that raises after the hit insert leaves the row RECEIVED and no hit (4a's one-transaction-per-row)
def test_the_hook_declares_the_active_rules_aggregates(...)                # agg_declaration rows for the three active rules after the first processed row
def test_a_wildcard_endpoint_gets_the_limit_event_but_never_a_hit_event(...)   # register_webhooks + register_rules; wildcard endpoint; a structuring hit and an N1 overrun -> deliveries for tier_limit_reached only
def test_register_rules_marks_every_staff_only_type()                      # is_staff_only(t) for t in RULES_STAFF_ONLY_EVENT_TYPES

# test_tier_limits.py
def test_an_n1_relationship_over_750_udi_gets_a_block_hit_and_one_notice(...)   # deposits summing 760 UDI/LAST_DAY_PREV_MONTH in December -> rule_hit outcome BLOCK severity HIGH dedup "RELATIONSHIP:<id>:2026-12"; one tier_limit_notice (source PROCESS); one tier_limit_reached event
def test_the_limit_payload_carries_ids_and_codes_only(...)                 # data keys == {relationship_id, account_id, tier, limit_code, period_key, outcome}
def test_a_second_overrun_in_the_month_sends_no_second_webhook(...)        # notice ON CONFLICT -> False; the hit links WINDOW
def test_a_notice_from_the_decide_source_blocks_the_process_one(...)       # notice_tier_limit(source=DECIDE) then PROCESS -> False (4c's seam)
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/rules/test_evaluate.py tests/modules/monitoring/rules/test_hits.py tests/modules/monitoring/rules/test_hook.py tests/modules/monitoring/rules/test_tier_limits.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README "Monitoring" gains "Rule hits" (dedup key, cooldown, evidence, the staff-only events, the one tenant-facing limit webhook).
- [ ] **Step 5: Commit** `feat(monitoring): rule hits with dedup, cooldown and evidence from the processing hook`.

---

### Task B8: Simulation engine and job, `rules simulate`, `rules activate`, typology goldens

Depends on **B7** (the reader, the profile, the hits for overlap). No console. Touches `src/xolotl/cli.py`, `rules/wiring.py`, `src/xolotl/platform/config.py`.

**Files:**
- Create: `src/xolotl/modules/monitoring/simulation/{__init__,engine,job}.py`, `tests/golden/monitoring/typologies/<CODE>/{scenario,expected}.yaml` (16)
- Modify: `src/xolotl/modules/monitoring/rules/wiring.py`, `src/xolotl/modules/monitoring/errors.py`, `src/xolotl/cli.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`
- Test: `tests/modules/monitoring/simulation/{__init__,test_engine,test_job}.py`, `tests/modules/monitoring/rules/test_typology_goldens.py`

**Interfaces:**

Consumes: `MemoryAggregateStore`, `enriched_from_row`, `Transaction`, `Settings.monitoring_agg_backfill_batch` (A2), `reader_for`, `profile_for`, `evaluate_rules`, `dedup_key_for` (B7), `LoadedRule`, `RuleSet`, `active_rules` (B6), `get_snapshot`, `ScreeningSubject`, `SubjectStatus`, `register_job_handler`, `PostgresJobQueue.enqueue`, `Job`.

Produces:

```python
# simulation/engine.py (no session: the callers hand it everything)
@dataclass(frozen=True, slots=True)
class SimulatedHit:
    rule_code: str; rule_version_id: UUID; party_id: UUID; relationship_id: UUID; key_kind: KeyKind; key: str; dedup_key: str
    window_start: datetime; window_end: datetime; severity: Severity; outcome: HitOutcome; aggregates: Mapping[str, AggregateSnapshot]; trigger_transaction_id: UUID
def replay_party(rules: Sequence[LoadedRule], stream: Iterable[EnrichedTransaction], *, policy_for: Callable[[UUID], EffectivePolicy],
                 profile_for: Callable[[EnrichedTransaction], EvalProfile], lists: JurisdictionLists, now: datetime) -> list[SimulatedHit]
    # one MemoryAggregateStore; declarations = union over rules; per transaction in occurred_at order: store.add(txn, declarations); ctx; evaluate_rules; dedup and cooldown in memory
    # exactly as record_firing (same dedup_key, same cooldown rule), so production and simulation agree
def replay_rule_set(s: Session, ruleset: RuleSet, *, range_start: date, range_end: date, page: int) -> Iterator[SimulatedHit]
    # the generic walk B8's job and 4c's `monitoring replay --dry-run` share: PROCESSED rows in the range ordered (party_id, occurred_at, id), party-aligned pages (Deviation 12),
    # enriched_from_row, get_snapshot cached by id, profile_for from the stream (Deviation 13)

# simulation/job.py
SIMULATE_KIND = "monitoring.simulate"
def request_simulation(s, version_id: UUID, *, range_start: date, range_end: date, queue: PostgresJobQueue) -> RuleSimulation
    # writers; version DRAFT or SIMULATED (else RuleTransitionNotAllowedError([STATE])); range_end <= today, range_start <= range_end, days <= monitoring_simulation_max_days
    # (else SimulationRangeTooLongError 422); insert QUEUED with definition_hash; enqueue key f"simulate:{id}", max_attempts=1; audit rules.simulation_requested {"simulation_id", "range_start", "range_end"}
def handle_simulate(s: Session, job: Job) -> None
    # RUNNING, started_at; pages through replay_rule_set over the single LoadedRule built from the version (assignment_id = nil UUID, every product); after each page: commit tallies
    # (transactions_read, hit_count, customers_hit, hits_per_week [{"week": ISO week of window_start, "hits", "customers"}], overlap {"existing_hits": rule_hit rows of the same party
    # with window overlap, "same_customers": distinct such parties, "later_true_match": parties whose subject is TRUE_MATCH with last_screened_at > their last window_end},
    # sample: up to 50 ids-only dicts) and cursor; at the end COMPLETED, finished_at; the version gets status SIMULATED and simulation_id when it is still DRAFT/SIMULATED with the same hash;
    # publish SIMULATION_COMPLETED {"simulation_id", "rule_version_id", "status"}; failure -> Deviation 16
def run_simulation_now(s, version_id, *, range_start, range_end) -> RuleSimulation     # request + handle inline (the CLI and `rules activate`)

# errors.py
class SimulationRangeTooLongError(ValidationFailedError): default_code = "SIMULATION_RANGE_TOO_LONG"; default_detail = "the simulation range is too long"
```

Settings: `monitoring_simulation_max_days`. `register_rules` registers `register_job_handler(SIMULATE_KIND, handle_simulate)`.

CLI: `rules simulate --tenant <slug> --version <id> --from <date> --to <date>` prints `"simulation <id>: <hits> hits, <customers> customers, <weeks> weeks"`; `rules activate --tenant <slug> --version <id> --approved-by <email> [--simulate-from --simulate-to]` runs `run_simulation_now` over the range (default the last 30 days), then `submit_version` as the author (a platform context narrowed to the tenant with `actor_id = str(author_id)`), `approve_version` as the approver (must differ from the author; the same email is refused with `RULE_TRANSITION_NOT_ALLOWED`) and `activate_version(effective_from=today, product=None)`; prints the assignment id. `effective_from` is today and B6 refuses a past date, so a demo that activates after its fixtures' dates runs the command under an earlier clock (C11 section 2 at 2026-06-30, simulating 2026-06-01 to 2026-06-29; reconciliation finding 23). Both commands are audited through the service functions.

Typology goldens (`tests/golden/monitoring/typologies/<CODE>/scenario.yaml`: `rates`, `profile`, `transactions` as enriched fields; `expected.yaml`: exactly one hit with `dedup_key`, `window_start`, `window_end`, `outcome`, `aggregates`): `test_typology_goldens` is parametrised over the 16 templates, builds the `LoadedRule` from the library file with default params, runs `replay_party` with the ITF policy (`both_packs()`) and the shipped lists, and prints a unified diff on mismatch. The scenarios are the exit demo's shapes: three near-threshold deposits for `CASH_STRUCTURING`, a `TRUE_MATCH` counterparty for `COUNTERPARTY_TRUE_MATCH`, 760 UDI in a month for `TIER_N1_MONTHLY_OPS`, and so on per spec §9.3; each scenario also contains one transaction that must not fire.

- [ ] **Step 1: Write the failing tests.**

```python
# test_engine.py
def test_replay_dedups_and_cools_down_like_production(...)                 # four near deposits in a week -> one SimulatedHit; a fifth 9 days later -> none
def test_replay_keeps_one_partys_memory_at_a_time(...)                     # two parties interleaved -> each evaluated against its own aggregates only
def test_typology_goldens(code)                                            # tests/modules/monitoring/rules/test_typology_goldens.py, parametrised

# test_job.py
def test_a_simulation_writes_only_its_own_row(...)                         # row counts of every monitoring table, agg_*, audit (beyond the request's row) and outbox (beyond the completion event) unchanged
def test_simulation_reproduces_the_stored_hits_of_the_week(...)            # week of 7 to 13 December processed with CASH_STRUCTURING active -> simulated (dedup_key, window) set == stored hits
def test_the_summary_counts_weeks_customers_and_overlap(...)               # hits_per_week == [{"week": "2026-W50", "hits": 1, "customers": 1}]; overlap.existing_hits == 1, same_customers == 1; sample has 1 ids-only entry
def test_a_crash_between_pages_resumes_at_the_next_party(...)              # page size 2 with 3 parties; raise after page 1 (monkeypatch); re-run -> cursor advanced, transactions_read exact, no double count
def test_a_too_long_range_is_refused(...)                                  # 191 days -> SIMULATION_RANGE_TOO_LONG; range_end tomorrow -> VALIDATION_FAILED
def test_completion_marks_the_version_simulated_only_when_unchanged(...)   # PATCH during the run (new hash) -> row COMPLETED, version stays DRAFT without simulation_id
def test_a_failure_is_final_and_visible(...)                               # injected error -> FAILED, last_error "RuntimeError", event with status FAILED, no retry (attempts == max_attempts)

# tests/test_cli.py (append)
def test_rules_simulate_and_activate(...)   # simulate prints the summary; activate with --approved-by the author -> exit 1 "RULE_TRANSITION_NOT_ALLOWED"; with the MLRO -> ACTIVE and an assignment
```

`tests/platform/test_errors.py`: `(SimulationRangeTooLongError, 422, "SIMULATION_RANGE_TOO_LONG")`. `tests/platform/test_worker.py` and `test_composition_root.py`: the job kind `monitoring.simulate` is registered.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/simulation tests/modules/monitoring/rules/test_typology_goldens.py tests/test_cli.py -k "simulat or activate" --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README CLI section gains `rules simulate`, `rules activate`; the configuration table gains `XOLOTL_MONITORING_SIMULATION_MAX_DAYS`; "Monitoring" gains "Simulation" (memory replay, side-effect-free, 30-day validity).
- [ ] **Step 5: Commit** `feat(monitoring): rule simulation replays history in memory without side effects`.

---

### Task B9: Rules API: templates, rules, versions, transitions, simulations, assignments

Depends on **B8** (every service it mounts). It appends to A10's `monitoring/schemas.py` and `app.py` without an edge to A10: the wave order and finding 17's first-to-merge rule cover it. No console (B13 regenerates the types). Mounts the router in `src/xolotl/app.py`.

**Files:**
- Create: `src/xolotl/modules/monitoring/routes_rules.py`; `src/xolotl/modules/monitoring/schemas.py` (create or append)
- Modify: `src/xolotl/app.py`
- Test: `tests/modules/monitoring/route_support.py` (`staff_client(role)`, `api_client(scopes)`, `activate_rule(...)`, shared with B11 and B12), `tests/modules/monitoring/test_routes_rules.py`

**Interfaces:**

Consumes: B5, B6 and B8 service functions, `evaluate_rule_actions`, `require_access`, `SessionDep`, `PageDep`, `paginate`, `Page`, `app_services` (the queue), `bound_context`.

Produces (`schemas.py`; every enum typed so the console's generated types carry it):

```python
class ParamView(BaseModel): name: str; value: str; type: Literal["DECIMAL", "INT", "STR", "BOOL"]; min: str | None; max: str | None; choices: list[str] | None; tighten: Literal["LOWER", "RAISE"] | None; unit: str | None; source: str
class TemplateResponse(BaseModel): code: str; family: RuleFamily; kind: RuleKind; title: str; typology: list[str]; country_code: str; packs: list[str]; version: int
    params: list[ParamView]; definition: dict[str, JsonValue]; sentences: dict[str, str]     # {"es-MX": ..., "en": ...}
class SimulationResponse(BaseModel): id: UUID; rule_version_id: UUID; status: SimulationStatus; range_start: date; range_end: date; definition_hash: str
    transactions_read: int; hit_count: int; customers_hit: int; hits_per_week: list[dict[str, JsonValue]]; overlap: dict[str, int] | None; sample: list[dict[str, JsonValue]]
    requested_by: UUID; started_at: datetime | None; finished_at: datetime | None; last_error: str | None; valid_until: date | None; notes: list[str]   # valid_until = finished_at + max_age_days
class AssignmentResponse(BaseModel): id: UUID; rule_version_id: UUID; rule_code: str; product: str | None; segment_filter: dict[str, list[str]] | None; effective_from: date; effective_to: date | None; approved_by: UUID
class VersionResponse(BaseModel): id: UUID; rule_id: UUID; version: int; status: RuleVersionStatus; definition_hash: str; sync: bool; params: list[ParamView]; segment: SegmentFilter | None
    severity: Severity; dedup: Dedup; sentence: str; author_id: UUID; submitted_at: datetime | None; approved_by: UUID | None; approved_at: datetime | None
    simulation: SimulationResponse | None; assignment: AssignmentResponse | None; activated_at: datetime | None; retired_at: datetime | None; retired_by_version_id: UUID | None
    allowed_actions: list[RuleAction]; blocked_reasons: dict[RuleAction, list[RuleBlockedReason]]; created_at: datetime
class RuleResponse(BaseModel): id: UUID; code: str; family: RuleFamily; kind: RuleKind; template_code: str | None; title: str; typology: list[str]; created_by: UUID; created_at: datetime
    versions: list[VersionResponse]; active_version_id: UUID | None; last_simulation: SimulationResponse | None
class RuleCreateRequest(BaseModel): template_code: str | None = None; definition: dict[str, JsonValue] | None = None; params: dict[str, ParamValue] | None = None   # exactly one of template_code, definition
    segment: SegmentFilter | None = None; severity: Severity | None = None; dedup: Dedup | None = None
class VersionRequest(VersionInput): ...
class SimulateRequest(BaseModel): range_start: date; range_end: date
class SimulateAccepted(BaseModel): simulation_id: UUID
class ActivateRequest(BaseModel): effective_from: date; product: str | None = None
class RejectRequest(BaseModel): reason: str = Field(min_length=1, max_length=500)
```

Routes (`router = v1_router(prefix="/monitoring", tags=["monitoring-rules"])`):

| Handler | Route | Access | Notes |
|---|---|---|---|
| `list_templates`, `get_template` | `GET /templates`, `GET /templates/{code}` | `require_access(scope="rules:read")` | the latest version of each template; 404 `RULE_TEMPLATE_UNKNOWN` |
| `list_rules`, `get_rule` | `GET /rules`, `GET /rules/{rule_id}` | scope `rules:read` | `paginate(ts_col=MonitoringRule.created_at, ...)`; filters `kind`, `typology`, `status` (any version in it) |
| `create_rule` | `POST /rules` (201) | `require_access(roles=WRITERS, write=True)` | `VersionResponse`; Idempotency-Key |
| `create_rule_version` | `POST /rules/{rule_id}/versions` (201) | writers | |
| `update_rule_version` | `PATCH /rules/{rule_id}/versions/{v}` | writers | `v` is the version number |
| `simulate_rule_version` | `POST /rules/{rule_id}/versions/{v}/simulate` (202) | writers | `SimulateAccepted` |
| `get_simulation` | `GET /simulations/{simulation_id}` | scope `rules:read` | |
| `submit_rule_version`, `approve_rule_version`, `reject_rule_version`, `activate_rule_version`, `retire_rule_version` | `POST /rules/{rule_id}/versions/{v}/{submit,approve,reject,activate,retire}` | staff, write (`require_access(write=True)`; the service checks the roles) | `VersionResponse`; 409 with `blocked_reasons` |
| `list_assignments` | `GET /assignments?on=` | staff | `on` defaults to today |

- [ ] **Step 1: Write the failing tests** (`test_routes_rules.py`):

```python
def test_the_library_lists_templates_with_bounds_and_both_sentences(...)      # 16 templates; CASH_STRUCTURING params threshold min "1000" tighten LOWER; sentences["es-MX"] == the spec sentence
def test_an_api_client_reads_templates_and_rules_with_the_scope(...)         # rules:read -> 200; without -> 403 SCOPE_MISSING; POST with every scope -> 403
def test_a_senior_analyst_creates_tunes_and_simulates_a_rule(...)             # POST /rules 201 DRAFT; PATCH near_ratio 0.75 -> DRAFT; simulate -> 202; run the job; GET /simulations/{id} COMPLETED with valid_until
def test_out_of_bounds_params_are_a_422_with_the_source(...)                  # errors[0].loc ["params", "threshold"]
def test_the_lifecycle_through_the_api_with_two_users(...)                    # submit (author) -> IN_REVIEW; approve (author) -> 409 ["SAME_AUTHOR"]; approve (MLRO) -> APPROVED; activate -> ACTIVE with assignment; GET /assignments?on= lists it
def test_the_version_view_lists_allowed_actions_for_the_caller(...)           # DRAFT as author: allowed [EDIT, SIMULATE]; blocked SUBMIT [STATE, SIMULATION_MISSING]; as ANALYST everything blocked by ROLE_REQUIRED
def test_a_rejection_needs_a_reason_and_returns_to_draft(...)
def test_custom_rules_need_the_flag_and_admin(...)
def test_writes_are_idempotent(...)                                           # same Idempotency-Key -> the same version id, one row
def test_auditor_reads_but_never_writes(...)
def test_another_tenant_sees_nothing(...)
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/test_routes_rules.py --no-cov -q`.
- [ ] **Step 3: Implement** the schemas, the routes and the router mount.
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README API overview gains the rules routes with their audiences and the `rules:read` scope.
- [ ] **Step 5: Commit** `feat(monitoring): templates, rules, versions, lifecycle and simulations API`.

---

### Task B10: Capture definitions, trigger and field maps, reportable events, groups, `merge_into`, supersession, the captures hook and capture goldens

Depends on **B4** (tables) and **B7** (`register_rules`, the hook pattern, `reader_for`). It does not need B8 or B9, so it runs beside them. No console.

**Files:**
- Create: `src/xolotl/modules/monitoring/captures/{definitions,fields,events,groups,supersede,hook}.py`, `src/xolotl/modules/monitoring/captures/triggers.yaml`, `src/xolotl/modules/monitoring/captures/field_maps/{MX_F41_ROR,MX_ITF_FX_CASH,MX_ITF_INTL_TRANSFER,MX_ITF_VA_OPS,MX_AVISO_AVI}.yaml`, `tests/golden/monitoring/captures/<CODE>/{units,transactions,expected}.yaml` (six codes)
- Modify: `src/xolotl/modules/monitoring/rules/wiring.py` (the `CapturesHook`), `pyproject.toml` (package data for the YAML files if the build excludes them), `.importlinter` (see the note from B1)
- Test: `tests/modules/monitoring/captures/{__init__,test_definitions,test_fields,test_events,test_groups,test_supersede}.py`, `tests/modules/monitoring/captures/test_capture_goldens.py`

**Note from B1 (import contract).** import-linter refuses a source module that does not exist, so B1's `monitoring-evaluator-is-pure` lists only `rules.definitions` and `rules.render`. Add `xolotl.modules.monitoring.captures.definitions` to its `source_modules` in `.importlinter`, with one `ignore_imports` line per allowed `xolotl.platform` module it actually imports (`xolotl.modules.monitoring.captures.definitions -> xolotl.platform.<module>`); an ignore line that matches nothing fails the gate, so add exactly the lines your imports need.

**Interfaces:**

Consumes: `EffectivePolicy`, `Capture`, `Quantity`, `ConversionRule`, `TxnFilter`, `AggregateDeclaration`, `matches`, `AggregateKey`, `AggregateStore.accumulator_read`, `unit_amounts_for`, `UnitService`, `Holidays`, `add_iso_duration`, `policy_date`, `Transaction`, `Account`, `enriched_from_row` (4a), `ProcessingHook`, `publish_monitoring_event`, B4 models and enums, the code tables under `report-defs/codetables/MX/`.

Produces:

```python
# captures/definitions.py (pure)
TRIGGERS_FILE = Path(__file__).with_name("triggers.yaml")
@dataclass(frozen=True, slots=True)
class CaptureDefinition:
    code: str; kind: CaptureKind; report_code: str; threshold: Quantity; conversion_rule: ConversionRule; window: str | None
    period: Literal["MONTH", "QUARTER"]; merge_into: str | None; on: TxnFilter; filing: str
    @property
    def unit_key(self) -> str                                              # f"{threshold.unit}/{conversion_rule}"
    def declarations(self) -> tuple[AggregateDeclaration, ...]              # ACCUMULATION: (AggregateDeclaration(name=f"{code}_accum", SUM, UNIT_AMOUNT, unit_key, RELATIONSHIP, ACCUM, window, where=on),); else ()
def read_triggers(path: Path = TRIGGERS_FILE) -> Mapping[str, TxnFilter]  # code -> TxnFilter fields; validated
TRIGGERS = read_triggers()
def capture_definitions(policy: EffectivePolicy, *, triggers: Mapping[str, TxnFilter] = TRIGGERS) -> list[CaptureDefinition]
    # one per policy.captures[]; a capture code without a trigger row -> ValueError naming it (a pack content defect; the processing job fails loudly)

# captures/triggers.yaml (spec §9.2 table)
ITF_ROR_CASH: {instrument: [CASH]}
ITF_FX_CASH: {instrument: [FX], rail: [CASH]}
ITF_INTL_TRANSFER: {instrument: [TRANSFER], cross_border: true}
ITF_VA_OPS: {instrument: [CRYPTO]}
AVI_XVI_A: {instrument: [CRYPTO]}
AVI_XVI_B: {instrument: [CRYPTO]}            # the fee threshold cannot fire without fee_minor

# captures/fields.py
@dataclass(frozen=True, slots=True)
class FieldMap: report_code: str; operation_type: Mapping[str, str]; instrument: Mapping[str, str]; codetables: Mapping[str, str]   # "<DIRECTION>/<INSTRUMENT>" -> code; "<INSTRUMENT>" -> code; field -> code table name or absent (values written as-is)
def read_field_map(report_code: str) -> FieldMap                            # captures/field_maps/<report_code>.yaml; cached
def event_fields(txn: EnrichedTransaction, account_ref: str, fmap: FieldMap, *, merged: Sequence[str] = ()) -> dict[str, JsonValue]
    # {"operation_date": policy_date(txn.occurred_at).isoformat(), "operation_type", "instrument", "amount": {"minor": int, "currency"}, "fee": {"minor", "currency"} | None, "account_ref", "branch": "0000",
    #  "locality": None, "counterparty": {"country", "institution": None, "account_hash": None} | None (institution and the account hash are counterparty-row facts 4a stores; written
    #  when the enriched transaction carries them, else None), "crypto": {"network", "asset_code", "asset_qty": str} | None, "merged_captures": list(merged), "period_keys": {"MONTH", "QUARTER"}}
def unit_values(definition: CaptureDefinition, txn: EnrichedTransaction, converted: Conversion, *, fee: Conversion | None = None) -> dict[str, JsonValue]
    # {"threshold": {"amount": str, "unit"}, "converted": {"amount": str, "currency": "MXN", "unit_value_id", "rule", "resolved_date"}, "transaction": {"amount": str (the unit_amounts entry), "unit", "unit_value_id"}, "fee": {...} | None}

# captures/groups.py
def open_group_for(s, definition: CaptureDefinition, txn: EnrichedTransaction) -> CaptureGroup
    # key_kind RELATIONSHIP, key str(relationship_id); the OPEN or CROSSED group whose [window_start, window_end) contains occurred_at, else a new OPEN group with
    # window_start = the Mexico City day start of occurred_at, window_end = add_iso_duration(day, window) at day start, period_key = getattr(txn.period_keys, period); FOR UPDATE
def add_to_group(group: CaptureGroup, txn: EnrichedTransaction, unit_amount: Decimal) -> None     # accumulated_minor += amount_minor; accumulated_unit = the store's accumulator value (Deviation 9)
def group_members(s, definition: CaptureDefinition, group: CaptureGroup) -> list[Transaction]     # PROCESSED rows of the key in the window matching definition.on, occurred_at ASC
def cross_group(s, definition, group, *, at: datetime) -> list[ReportableEvent]                   # CROSSED, threshold_crossed_at = at; one event per member without a CAPTURED event (capture_group_id = group.id, period_key of each member's own month)

# captures/events.py
@dataclass(frozen=True, slots=True)
class CaptureOutcome: events: tuple[ReportableEvent, ...]; voided: tuple[ReportableEvent, ...]
def capture_transaction(s, definitions: Sequence[CaptureDefinition], txn: EnrichedTransaction, *, store: AggregateStore, units: UnitService, holidays: Holidays, late: bool) -> CaptureOutcome
    # for each definition with matches(on, txn), in a fixed order (merge targets before the captures that merge into them):
    # THRESHOLD: txn.unit_amounts[unit_key].amount >= threshold.amount -> write_event; FEE_THRESHOLD: fee_minor present and
    #   unit_amounts_for(units, amount=Decimal(fee_minor) / 100, currency=txn.currency, on=policy_date(txn.occurred_at), pairs={(unit, rule)}, holidays=holidays)[unit_key].amount >= threshold -> write_event (Deviation 8)
    # ACCUMULATION: amount >= threshold -> write_event alone; else group = open_group_for; add_to_group; accumulated_unit = store.accumulator_read(...).value;
    #   group OPEN and accumulated_unit >= threshold -> cross_group; group CROSSED -> write_event for txn (capture_group_id)
    # merge_into: when the target's event was written for txn in this call, skip this capture and set target.fields.merged_captures += [code]; when the target's event is written now
    #   and a previous event of this capture exists for txn -> that event VOIDED void_reason "MERGED" (Deviation 10)
    # write_event: INSERT ON CONFLICT (tenant_id, capture_code, transaction_id) DO NOTHING (an existing CAPTURED/INCLUDED event is kept; a VOIDED/SUPERSEDED one is left and a new row is not possible:
    #   supersession voids-before-recapturing by updating the existing row's status back to CAPTURED with supersedes_id, see supersede.py); fields, unit_values, policy_snapshot_id, late;
    #   open_period (B11's upsert; created here as a minimal `open_period(s, definition, period_key) -> CapturePeriod` that B11 completes); publish REPORTABLE_EVENT_CAPTURED
    #   {"reportable_event_id", "capture_code", "report_code", "period_key", "transaction_id"} key str(party_id)

# captures/supersede.py
def supersede_group(s, definition, group: CaptureGroup, *, late: EnrichedTransaction, store, units) -> CaptureGroup
    # called by the hook when a late ACCUMULATION transaction falls before group.window_start or changes the crossing (a group OPEN that now crosses earlier, or CROSSED whose
    # first-crossing member changes): every event of the group -> SUPERSEDED with superseded_by_id (publish REPORTABLE_EVENT_SUPERSEDED {"reportable_event_id", "superseded_by_id", "period_key"});
    # group SUPERSEDED with superseded_by_id -> a new group rebuilt from group_members including the late row, re-captured with supersedes_id on each new event and late = True;
    # an INCLUDED event is superseded the same way (never edited); each touched period -> reopen_period (B11; created here minimally)
def reopen_period(s, capture_code: str, period_key: str) -> CapturePeriod | None     # CLOSED -> REOPENED, reopened_at, change_count + 1, publish CAPTURE_PERIOD_CHANGED; OPEN -> unchanged

# captures/hook.py
class CapturesHook:                                 # 4a's ProcessingHook
    name = "captures"
    def declarations(self, s, snapshot, *, on, product) -> Sequence[AggregateDeclaration]     # union of capture_definitions(snapshot.policy).declarations
    def evaluate(self, s, txn, ctx) -> None          # capture_transaction(s, capture_definitions(ctx.snapshot.policy), txn, store=ctx.store, units=ctx.units, holidays=ctx.holidays, late=ctx.late); ctx.late and an ACCUMULATION group affected -> supersede_group
```

`register_rules` adds `register_processing_hook(CapturesHook())` after the rules hook.

Capture goldens (`tests/golden/monitoring/captures/<CODE>/`): `units.yaml` (the unit rows of the week: `USD_MXN_FIX` for 7 to 13 December 2026 with no row for 12 and 13 December, a weekend, `UDI`, `UMA` 117.31), `transactions.yaml` (`TransactionInput` items for one relationship), `expected.yaml` (the exact events: `transaction external_id`, `period_key`, `capture_group`, `fields`, `unit_values`, `late`). The test ingests through `ingest_items`, runs `run_processing`, and compares the `reportable_event` rows, printing a unified diff. Six directories: `ITF_ROR_CASH` (a USD 5,000 cash deposit on 13 December converted at the prior banking day's FIX of 11 December across the weekend of 12 and 13 December, and a 4,999 one that is not captured), `ITF_FX_CASH`, `ITF_INTL_TRANSFER` (a SWIFT transfer and a domestic one), `ITF_VA_OPS`, `AVI_XVI_A` (one crypto operation per month July to December 2026 whose December operation crosses 210 UMA: six events, one group, each with its own month), `AVI_XVI_B` (a fee of 4 UMA on an operation that also crosses: one `AVI_XVI_A` event with `merged_captures ["AVI_XVI_B"]`).

- [ ] **Step 1: Write the failing tests.**

```python
# test_definitions.py
def test_every_capture_of_both_packs_has_a_trigger(...)                   # capture_definitions over each pack's EffectivePolicy: six codes, the §9.2 filters, unit keys USD/PRIOR_BANKING_DAY, UDI/SAME_DAY, UMA/SAME_DAY
def test_an_accumulation_capture_declares_its_accumulator(...)            # AVI_XVI_A -> one ACCUM P6M declaration in UMA/SAME_DAY keyed RELATIONSHIP; the others none
def test_a_capture_without_a_trigger_row_is_a_load_error(tmp_path)
# test_fields.py
def test_every_code_in_a_field_map_exists_in_its_code_table()            # for each map with codetables: every value in report-defs/codetables/MX/<table>.yaml entries
def test_event_fields_carry_codes_amounts_and_ids_only(...)              # no name, account or wallet; branch "0000"; period_keys has MONTH and QUARTER
# test_events.py
def test_capture_goldens(code)                                            # test_capture_goldens.py, parametrised over the six directories
def test_a_threshold_capture_records_both_sides_of_the_conversion(...)   # unit_values.converted.unit_value_id == the FIX row of 11 December; resolved_date 2026-12-11
def test_a_fee_below_four_uma_is_not_captured(...)
def test_a_capture_never_opens_a_hit_or_reaches_a_client(...)            # no rule_hit; GET /v1/transactions/{id} as an API client (4a's route) shows status only
def test_capture_events_never_reach_a_webhook(...)                       # wildcard endpoint -> no reportable_event_* delivery
# test_groups.py
def test_the_whole_group_is_captured_on_crossing(...)                    # six monthly operations, December crosses -> six events with capture_group_id, period keys 2026-07..2026-12, group CROSSED with threshold_crossed_at
def test_later_operations_of_a_crossed_group_are_captured_as_they_arrive(...)
def test_a_window_that_elapses_stays_open_and_a_new_group_starts(...)    # an operation seven months after the first -> a second group
def test_an_operation_at_the_threshold_is_captured_alone(...)            # no group membership
# test_supersede.py
def test_a_late_operation_supersedes_the_group(...)                      # a 10 December operation arriving 12 December that moves the crossing -> old events SUPERSEDED with superseded_by_id, new events with supersedes_id and late True, group SUPERSEDED, events superseded published once each
def test_a_late_operation_after_the_close_reopens_the_period(...)        # capture_period CLOSED (inserted directly) -> REOPENED, change_count 1, capture_period_changed published
def test_an_included_event_is_superseded_never_edited(...)
def test_a_late_threshold_capture_is_just_late(...)                       # late True, nothing superseded
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/captures --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README "Monitoring" gains "Captures" (policy-derived, the trigger map, groups, supersession); `src/xolotl/modules/monitoring/captures/README.md` (how to add a capture code: a pack row, a trigger row, a field map).
- [ ] **Step 5: Commit** `feat(monitoring): reportable events from the policy snapshot with accumulation groups and supersession`.

---

### Task B11: Capture periods, period close, its schedule and CLI, captures API

Depends on **B10**. It appends to A10's `monitoring/schemas.py`, A5's `monitoring_app` and A9's schedule rows without an edge to them: the wave order and finding 17's first-to-merge rule cover it. No console (B15 regenerates the types). Touches `src/xolotl/modules/tenants/service.py` (the schedule), the pinned schedule tests, `src/xolotl/cli.py` (`monitoring period-close` on 4a's `monitoring_app`), `src/xolotl/platform/config.py` and `src/xolotl/app.py` (the router).

**Files:**
- Create: `src/xolotl/modules/monitoring/captures/periods.py`, `src/xolotl/modules/monitoring/routes_captures.py`
- Modify: `src/xolotl/modules/monitoring/captures/{events,supersede}.py` (replace the minimal `open_period`/`reopen_period` by imports), `src/xolotl/modules/monitoring/rules/wiring.py`, `src/xolotl/modules/monitoring/errors.py`, `src/xolotl/modules/monitoring/schemas.py`, `src/xolotl/modules/tenants/service.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `src/xolotl/cli.py`, `src/xolotl/app.py`, `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`
- Test: `tests/modules/monitoring/captures/test_periods.py`, `tests/modules/monitoring/test_routes_captures.py`

**Interfaces:**

Consumes: B10, `policy_date` (A6), `MemoryAggregateStore`, `enriched_from_row`, `get_snapshot`, `assignments_in_force`, `effective_policy`, `register_job_handler`, `PostgresJobQueue.schedule`, `require_access`, `paginate`.

Produces:

```python
# captures/periods.py
PERIOD_CLOSE_KIND = "monitoring.period_close"
def open_period(s, definition: CaptureDefinition, period_key: str) -> CapturePeriod      # INSERT ON CONFLICT (tenant_id, capture_code, period_key) DO NOTHING then select; period_start/end from period_key_bounds(period_key) -> tuple[date, date], defined here (a YYYY-MM or YYYY-Qn key to its first and last day); event_count recomputed = CAPTURED + INCLUDED events
def ensure_periods(s, *, ended_before: date) -> int
    # for each ended MONTH (and QUARTER when ended_before is the first day of a quarter) since the tenant's earliest PROCESSED transaction: for every capture of every policy in force
    # on the period's last day (assignments_in_force on that day, effective_policy per product): open_period (Deviation 11); returns rows created
def check_captures(s, definition: CaptureDefinition, *, period_start: date, period_end: date) -> set[UUID]
    # check mode (no writes): PROCESSED rows of the period (and, for ACCUMULATION, from period_start - window) per relationship in occurred_at order through a MemoryAggregateStore and
    # the same capture logic with a recording sink instead of write_event; returns the transaction ids that should be CAPTURED in the period
def close_period(s, period: CapturePeriod, *, units: UnitService) -> CapturePeriod
    # period_end >= today -> PeriodNotEndedError (409); expected = check_captures; stored = ids of CAPTURED/INCLUDED events of (capture_code, period_key); missing = expected - stored -> write each
    # with late = True through capture_transaction's write path; extra = stored - expected -> VOIDED void_reason "RECONCILIATION" (INCLUDED extras are voided too and the period REOPENED);
    # reconciled_count = len(expected); discrepancies = {"missing": [...], "extra": [...]} (ids) or None when empty; CLOSED, closed_at, reconciled_at; publish CAPTURE_PERIOD_CLOSED
    # {"capture_period_id", "capture_code", "report_code", "period_key", "status", "discrepancy_count"}
def handle_period_close(s: Session, job: Job) -> None     # payload {} -> ensure_periods(today) then close every OPEN/REOPENED period with period_end < today; {"capture_code", "period_key"} -> that one
# the two helpers B10 created minimally are completed here and B10's modules import them

# errors.py
class PeriodNotEndedError(DomainError): default_code = "PERIOD_NOT_ENDED"; default_detail = "the period has not ended"

# schemas.py
class ReportableEventSummary(BaseModel): id: UUID; capture_code: str; report_code: str; capture_kind: CaptureKind; transaction_id: UUID; occurred_at: datetime; party_id: UUID; relationship_id: UUID
    product: str | None; period_key: str; capture_group_id: UUID | None; status: ReportableEventStatus; late: bool; captured_at: datetime
class CaptureGroupView(BaseModel): id: UUID; status: CaptureGroupStatus; window_start: datetime; window_end: datetime; threshold_crossed_at: datetime | None; accumulated_minor: int; accumulated_unit: str; member_count: int; superseded_by_id: UUID | None
class ReportableEventDetail(ReportableEventSummary): fields: dict[str, JsonValue]; unit_values: dict[str, JsonValue]; group: CaptureGroupView | None; supersedes_id: UUID | None; superseded_by_id: UUID | None; void_reason: str | None; policy_snapshot_id: UUID
class CapturePeriodResponse(BaseModel): id: UUID; capture_code: str; report_code: str; period_key: str; period_start: date; period_end: date; status: CapturePeriodStatus; event_count: int
    reconciled_count: int | None; reconciled_at: datetime | None; discrepancies: dict[str, list[UUID]] | None; closed_at: datetime | None; reopened_at: datetime | None; change_count: int
class CaptureLimitView(BaseModel): code: str; kind: CaptureKind | Literal["TIER_LIMIT"]; threshold: str; unit: str; conversion_rule: ConversionRule; window: str | None; period: str | None; report_code: str | None; legal_basis: str; confidence: str; source: str   # the read-only Capturas y límites rows
```

Routes (`router = v1_router(prefix="/monitoring/captures", tags=["monitoring-captures"])`, staff only): `list_captures` `GET ""` (filters `capture_code`, `report_code`, `period_key`, `status`, `relationship_id`, `transaction_id` (B15's card on the transaction detail); `paginate(ts_col=ReportableEvent.captured_at, ...)`), `list_capture_periods` `GET /periods` (filters `capture_code`, `status`; ordered `period_key DESC`), `close_capture_period` `POST /periods/{period_id}/close` (`require_access(roles=(Role.MLRO, Role.ADMIN), write=True)`; runs `close_period` now), `list_effective_captures` `GET /effective?on=&product=` (the snapshot's captures and tier limits as `CaptureLimitView` with provenance, for B15), `get_capture` `GET /{event_id}`.

Schedule: `MONITORING_PERIOD_CLOSE_SCHEDULE = "monitoring.period_close"` in `schedule_platform_jobs` with `cron = get_settings().monitoring_period_close_cron`, `tz = "America/Mexico_City"`, `payload = {}`; `PLATFORM_SCHEDULES` gains the row in name order (4a adds its own rows; additive). CLI: `monitoring period-close --tenant <slug> [--capture <code> --period <key>]` runs `handle_period_close` inline and prints one line per period `"<capture> <period> CLOSED events=<n> reconciled=<n> discrepancies=<n>"`, exit 1 when any discrepancy count is non-zero.

- [ ] **Step 1: Write the failing tests.**

```python
# test_periods.py
def test_periods_are_opened_for_every_capture_of_the_ended_month(...)     # tenant A on 1 January 2027: December rows for ITF_INTL_TRANSFER, AVI_XVI_A, AVI_XVI_B (MONTH) and Q4 rows for ITF_ROR_CASH, ITF_FX_CASH, ITF_VA_OPS
def test_a_clean_period_closes_with_zero_discrepancies(...)               # the golden week -> CLOSED, reconciled_count == event_count, discrepancies None, event published with discrepancy_count 0
def test_the_close_finds_an_injected_gap_and_writes_the_missing_event(...)   # delete one CAPTURED event in SQL -> close writes it back late = True, discrepancies.missing == [id]; an extra event inserted -> VOIDED RECONCILIATION
def test_a_period_that_has_not_ended_cannot_be_closed(...)                # PERIOD_NOT_ENDED
def test_check_mode_writes_nothing(...)                                   # row counts unchanged after check_captures
def test_a_reopened_period_closes_again_with_its_change_count(...)        # REOPENED -> CLOSED, change_count kept
def test_the_schedule_is_declared_per_tenant(...)                         # PLATFORM_SCHEDULES row ("monitoring.period_close", "0 4 1 * *", "America/Mexico_City", ...)

# test_routes_captures.py
def test_captures_list_and_detail_with_group_and_chain(...)               # filters; detail with fields, unit_values, group member_count 6, supersedes chain
def test_periods_list_and_a_manual_close_by_the_mlro(...)                 # ANALYST POST -> 403; MLRO -> CLOSED; not ended -> 409
def test_effective_captures_carry_provenance(...)                         # GET /effective: six captures and the N1/N2 limits with legal_basis and confidence
def test_captures_are_staff_only(...)                                     # API key with every scope -> 403
def test_another_tenant_sees_nothing(...)

# tests/test_cli.py (append)
def test_monitoring_period_close_reports_discrepancies(...)               # clean -> exit 0; injected gap -> exit 1 naming the period
```

`tests/platform/test_errors.py`: `(PeriodNotEndedError, 409, "PERIOD_NOT_ENDED")`. `tests/platform/test_config.py`: `monitoring_period_close_cron` and its cron validation. `tests/platform/test_worker.py`, `test_composition_root.py`: the job kind and the schedule.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/captures/test_periods.py tests/modules/monitoring/test_routes_captures.py tests/platform/test_worker.py tests/test_cli.py -k "period" --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README configuration table (`XOLOTL_MONITORING_PERIOD_CLOSE_CRON`), CLI section (`monitoring period-close`), API overview (the captures routes), "Monitoring" gains "Period close" (reconciliation by re-evaluation; a discrepancy is a defect).
- [ ] **Step 5: Commit** `feat(monitoring): capture periods closed by re-evaluation, with the captures API`.

---

### Task B12: Hits API

Depends on **B7** and **A10** (`reveal_counterparty` and `counterparty_summary` in `screening.counterparty.views`; `monitoring/schemas.py`, which this task appends to). It does not need B8 to B11, so it runs beside them. No console (B14 regenerates the types). Mounts the router in `src/xolotl/app.py`.

**Files:**
- Create: `src/xolotl/modules/monitoring/routes_hits.py`
- Modify: `src/xolotl/modules/monitoring/schemas.py`, `src/xolotl/app.py`
- Test: `tests/modules/monitoring/test_routes_hits.py` (and `route_support.py`, created here if B9 has not merged)

**Interfaces:**

Consumes: B4 models, `summaries` (`xolotl.modules.party.service`, the masked `PartyResponse`), `reveal_pii`, `reveal_counterparty`, `counterparty_summary` (A10, `screening.counterparty.views`), `mask_tail`, `require_access`, `paginate`, `Transaction`, `Counterparty`.

Produces (`schemas.py`):

```python
class HitSummary(BaseModel): id: UUID; rule_code: str; rule_version_id: UUID; typology: list[str]; severity: Severity; outcome: HitOutcome; key_kind: KeyKind; key: str
    party: PartyResponse; relationship_id: UUID; window_start: datetime; window_end: datetime; fired_count: int; cooldown_until: datetime | None
    first_transaction_id: UUID; first_occurred_at: datetime; decision_id: UUID | None; created_at: datetime
class EvidenceView(BaseModel): aggregates: dict[str, dict[str, str]]; params: dict[str, str]; explanation: str; lists_hash: str
class HitTransactionView(BaseModel): transaction_id: UUID; occurred_at: datetime; role: HitTransactionRole; direction: Direction; instrument: Instrument; rail: Rail; amount_minor: int; currency: str
    counterparty: str | None      # masked: kind and the counterparty id's last four, never a name
class HitDetail(HitSummary): evidence: EvidenceView; transactions: list[HitTransactionView]; assignment_id: UUID; policy_snapshot_id: UUID; definition_hash: str; dedup_key: str; sentence: str
class HitReveal(BaseModel): party: PartyPiiResponse; counterparties: dict[UUID, RevealedCounterpartyView]    # per counterparty id of the evidence transactions: name, account, wallet masked tail revealed
```

Routes: `router = v1_router(prefix="/monitoring", tags=["monitoring-hits"])` with `list_hits` `GET /hits` (staff; filters `severity`, `rule_code`, `typology`, `outcome`, `party_id`, `transaction_id` (B14's card on the transaction detail), `created_before`; `paginate(ts_col=RuleHit.created_at, ...)`), `get_hit` `GET /hits/{hit_id}`, `reveal_hit` `GET /hits/{hit_id}/reveal` (one `VIEW_PII` per non-empty group: `party` through `reveal_pii`, `counterparties` through `reveal_counterparty`; Deviation 7); and `party_router = v1_router(prefix="/parties", tags=["monitoring-hits"])` with `list_party_hits` `GET /parties/{party_id}/hits` (staff; the party's hits newest first). Both routers are mounted.

- [ ] **Step 1: Write the failing tests.**

```python
def test_the_queue_filters_and_paginates(...)                              # severity, rule_code, typology, outcome, party_id, created_before; cursor pages; party masked (initials, masked identifiers)
def test_the_detail_shows_evidence_and_masked_transactions(...)            # aggregates against thresholds, params, explanation, 3 transactions with roles, counterparty masked
def test_the_reveal_records_view_pii_per_group(...)                        # party + counterparties -> two VIEW_PII rows; the detail GET records none
def test_a_partys_hits_are_listed_on_the_party(...)
def test_hits_are_staff_only_and_hidden_from_api_clients(...)              # API key with every scope -> 403 on each route; GET /v1/transactions/{id} as a client has no hit field
def test_auditor_reads_hits(...)
def test_another_tenant_sees_nothing(...)
def test_no_plaintext_is_stored_beyond_the_encrypted_columns(admin_engine, caplog, ...)
    # after a hit, a reveal and a capture: leaked(everything_stored minus the encrypted columns and the two name indexes, party names, counterparty name, account, wallet) == []
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/monitoring/test_routes_hits.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README API overview gains the hits routes (staff only).
- [ ] **Step 5: Commit** `feat(monitoring): rule hits API with evidence and an audited reveal`.

---

### Task B13: Console: Reglas › Monitoreo with the editor, simulation panel and lifecycle

Depends on **B9** (regenerate `schema.d.ts` against it). Console only. 4a's Transacciones pages, B14 and B15 also append to `nav.ts`, `App.tsx`, `access.ts`, both catalogues and `catalogue.test.ts`; whichever merges later rebases and re-applies its own lines (additive) and regenerates `schema.d.ts`.

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `en.json`, `catalogue.test.ts`, `console/e2e/smoke.spec.ts`
- Create: `console/src/features/rules/{queries.ts, queries.test.tsx, fixtures.ts, RulesPage.tsx, RulesPage.test.tsx, RulePage.tsx, RulePage.test.tsx, VersionEditor.tsx, VersionEditor.test.tsx, SimulationPanel.tsx, SimulationPanel.test.tsx, LifecycleButtons.tsx, LifecycleButtons.test.tsx, RuleStatusBadge.tsx}`

**Interfaces:**

Consumes: the console kit the screening pages use (`api`, `unwrap`, `useIdempotentMutation`, `hasProblemCode`, `useAuth`, `ACCESS`, `hasAnyRole`, `RequireRole`, `PAGE_SIZE`, `PageHeader`, `DataTable`, `CursorPager`, `DateTimeCell`, `KeyValues`, `Badge`, `Card*`, `Tabs`, `Select`, `Input`, `Textarea`, `Button`, `FormAlert`, `Tooltip`, `useFormat`, `renderWithProviders`/`fakeApi`); B9's routes.

Produces:
- `types.ts`: `TemplateResponse`, `RuleResponse`, `VersionResponse`, `SimulationResponse`, `AssignmentResponse`, `ParamView`, `RuleCreateRequest`, `VersionRequest`, `SimulateRequest`, `ActivateRequest`, `RejectRequest`, `RuleVersionStatus`, `RuleAction`, `RuleBlockedReason`, `SimulationStatus`, `Severity`, `Page_RuleResponse_`.
- `access.ts`: `rules: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']`, `rulesWrite: ['SENIOR_ANALYST', 'MLRO', 'ADMIN']`, `rulesApprove: ['MLRO', 'ADMIN']`, with `access.test.ts` and `guards.test.tsx` rows.
- `nav.ts`: `'nav.monitoringRules'`, `{ to: '/rules/monitoring', labelKey: 'nav.monitoringRules', icon: SlidersHorizontal, roles: ACCESS.rules }` after `nav.privateLists`.
- `App.tsx`: a `RequireRole roles={ACCESS.rules}` group with `/rules/monitoring` (`RulesPage`) and `/rules/monitoring/:ruleId` (`RulePage`).
- `queries.ts`: `useTemplates()`, `useRules(filters, cursor)`, `useRule(ruleId)`, `useCreateRule()`, `useCreateVersion(ruleId)`, `useUpdateVersion(ruleId, v)`, `useSimulate(ruleId, v)`, `useSimulation(simulationId, { refetchInterval: 5000 while QUEUED or RUNNING })`, `useTransition(ruleId, v, action)` (all writes through `useIdempotentMutation`, invalidating the rule and the list).
- `RulesPage`: tabs `library` (default: every template with kind, typology tags and the es-MX sentence; "Crear regla" for `ACCESS.rulesWrite` posting `{template_code}`) and `tenant` (the tenant's rules with their latest version status badge, active version, last simulation age); `CursorPager`.
- `RulePage`: the rule's versions as a timeline; the selected version's `VersionEditor` (one native control per `ParamView`: a number input with `min`/`max` and a hint naming the bound and its `source`, a select for `choices`, a switch for booleans; segment picker (tier, risk rating, profile as multi-selects); severity select; dedup key, window and cooldown; the sentence read-only, re-rendered from the server after each save; disabled unless `allowed_actions` has `EDIT`); `SimulationPanel` ("Simular últimos 6 meses" posting the range ending today, progress while running, the last simulation with hits per week as a small table, customers, overlap, the sample list of ids, and the age badge "válida 30 días" / "vencida" from `valid_until`); `LifecycleButtons` (one button per `allowed_actions` entry among `SUBMIT`, `APPROVE`, `REJECT` (with a reason dialog), `ACTIVATE` (with a date and optional product), `RETIRE`; the other actions disabled with a `Tooltip` naming `blocked_reasons` as `rules.blocked.<REASON>`, the `SAME_AUTHOR` copy being "El aprobador debe ser distinto del autor").
- i18n (both catalogues): `nav.monitoringRules`; `rules.{title,description,tabs.library,tabs.tenant,create,empty,table.*,kind.*,typology.*,status.*,severity.*,editor.*,bound.{min,max,choices,tighten,source},simulation.{title,run,running,hitsPerWeek,customers,overlap.*,sample,validUntil,expired,failed,notes.*},action.*,blocked.*,reject.reason,activate.{effectiveFrom,product}}`; `errors.RULE_PARAM_OUT_OF_BOUNDS`, `errors.RULE_TRANSITION_NOT_ALLOWED`, `errors.SIMULATION_RANGE_TOO_LONG`, `errors.RULE_TEMPLATE_UNKNOWN`.
- Smoke (`console/e2e/smoke.spec.ts`): one step, `the rules library lists the Mexico typologies`, after the private-lists step: open Reglas › Monitoreo and see `CASH_STRUCTURING` with its sentence (the smoke stack runs `db upgrade`, which loads the library). The tune, simulate, submit, approve-as-another-user and activate flow of spec §19 needs transactions and a second user and is left to plan 4c's exit test (Open question 3).

- [ ] **Step 1: Write the failing tests:** `RulesPage.test.tsx` (`opens on the library with sentences`, `the tenant tab lists versions with status`, `create posts the template code`); `RulePage.test.tsx` (`the editor shows bounds and their source`, `an out-of-bounds value maps to its field`, `offers only the allowed actions and explains the blocked ones`, `approve as the author shows SAME_AUTHOR`); `VersionEditor.test.tsx`; `SimulationPanel.test.tsx` (`runs a six-month simulation and polls until completed`, `shows the age badge`, `shows a failed simulation`); `LifecycleButtons.test.tsx`; `queries.test.tsx`; access rows.
- [ ] **Step 2: Run to verify failure.** `cd console && npm test -- rules`.
- [ ] **Step 3: Implement**, regenerating `schema.d.ts` (`npm run gen:api:file` against the merged API).
- [ ] **Step 4: Run to verify pass.** `cd console && npm ci && npm run typecheck && npm run lint && npm test && npm run build`, then the Playwright smoke.
- [ ] **Step 5: Commit** `feat(console): monitoring rules library, editor, simulation panel and lifecycle`.

---

### Task B14: Console: Hallazgos de reglas

Depends on **B12** and **A11** (the transaction detail it extends). Console only; runs beside B13 and B15 (additive shared files).

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, both catalogues, `catalogue.test.ts`, `console/src/features/transactions/TransactionPage.tsx` (A11; + `TransactionHitsCard`), `console/src/features/parties/PartyPage.tsx` (+ `PartyHitsCard`)
- Create: `console/src/features/hits/{queries.ts, HitsPage.tsx, HitsPage.test.tsx, HitPage.tsx, HitPage.test.tsx, EvidenceTable.tsx, EvidenceTable.test.tsx, HitRevealCard.tsx, SeverityBadge.tsx, TransactionHitsCard.tsx, PartyHitsCard.tsx, fixtures.ts}`

**Interfaces:**

Produces:
- `types.ts`: `HitSummary`, `HitDetail`, `HitReveal`, `EvidenceView`, `HitTransactionView`, `HitOutcome`, `HitTransactionRole`, `Page_HitSummary_`.
- `access.ts`: `monitoring: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']` (defined here; A11's `transactions` has the same five roles, and C9 reuses that one so it does not depend on this task).
- `nav.ts`: `'nav.ruleHits'`, `{ to: '/monitoring/hits', labelKey: 'nav.ruleHits', icon: Siren, roles: ACCESS.monitoring }` before `nav.monitoringRules`.
- `App.tsx`: `/monitoring/hits` (`HitsPage`) and `/monitoring/hits/:hitId` (`HitPage`) under `RequireRole roles={ACCESS.monitoring}`.
- `queries.ts`: `useHits(filters, cursor)`, `useHit(hitId)`, `useRevealHit(hitId)` (`useMutation`, `gcTime: 0`), `usePartyHits(partyId)` and `useTransactionHits(transactionId)` (`GET /monitoring/hits?party_id=` and `?transaction_id=`); `PartyHitsCard` on `PartyPage.tsx` beside A12's `PartyTransactionsCard`, and `TransactionHitsCard` on A11's `TransactionPage.tsx` (the "hits on this transaction" of spec §17), both behind `ACCESS.monitoring`.
- `HitsPage`: tabs by severity (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`; default the highest with rows), filters rule, typology, outcome, "older than" (days → `created_before`); columns party (masked), rule, typology tags, severity, outcome, window, fired count, created; `CursorPager`.
- `HitPage`: the rendered sentence, parameters against the aggregates at firing time (`EvidenceTable`: name, value, unit, window, the parameter it was compared with), the evidence transactions table (role, date, direction, instrument, rail, amount, counterparty masked), the decision link when `decision_id` is set (4c's route; rendered as plain text until then), the dedup key and cooldown state, `HitRevealCard` ("Revelar", the audited notice, `aria-live`). No disposition, no note field (spec §17).
- i18n: `nav.ruleHits`; `hits.{title,description,tabs.*,empty,filters.*,table.*,severity.*,outcome.*,role.*,detail.{sentence,params,aggregates,transactions,dedup,cooldown,decision,decided}}`, `hits.reveal.*`.

- [ ] **Step 1: Write the failing tests:** `HitsPage.test.tsx` (`opens on the highest severity with rows`, `filters by rule and outcome`); `HitPage.test.tsx` (`renders the sentence, aggregates and evidence transactions masked`, `the reveal is a mutation and is forgotten on hide`); `EvidenceTable.test.tsx`; access rows.
- [ ] **Step 2: Run to verify failure.** `cd console && npm test -- hits`.
- [ ] **Step 3: Implement**, regenerating `schema.d.ts`.
- [ ] **Step 4: Run to verify pass.** The console gates.
- [ ] **Step 5: Commit** `feat(console): rule hit queue and detail with evidence`.

---

### Task B15: Console: Reglas › Capturas y límites

Depends on **B11** and **A11** (the transaction detail it extends). Console only; runs beside B13 and B14.

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, both catalogues, `catalogue.test.ts`, `console/src/features/transactions/TransactionPage.tsx` (A11; + `TransactionCapturesCard`)
- Create: `console/src/features/captures/{queries.ts, CapturesPage.tsx, CapturesPage.test.tsx, PeriodTable.tsx, PeriodTable.test.tsx, TransactionCapturesCard.tsx, fixtures.ts}`

**Interfaces:**

Produces:
- `types.ts`: `CaptureLimitView`, `CapturePeriodResponse`, `ReportableEventSummary`, `ReportableEventDetail`, `CapturePeriodStatus`, `ReportableEventStatus`, `Page_ReportableEventSummary_`.
- `access.ts`: `capturesClose: ['MLRO', 'ADMIN']`.
- `nav.ts`: `'nav.captures'`, `{ to: '/rules/captures', labelKey: 'nav.captures', icon: FileCheck, roles: ACCESS.rules }` after `nav.monitoringRules`.
- `App.tsx`: `/rules/captures` (`CapturesPage`) under `RequireRole roles={ACCESS.rules}`.
- `queries.ts`: `useEffectiveCaptures(on, product)`, `useCapturePeriods(filters)`, `useCaptures(filters, cursor)`, `useTransactionCaptures(transactionId)` (`GET /monitoring/captures?transaction_id=`, for `TransactionCapturesCard` on A11's `TransactionPage.tsx`), `useClosePeriod()` (`useIdempotentMutation`, invalidating periods).
- `CapturesPage`: the effective policy's captures and tier limits read-only (code, kind, threshold with unit, conversion rule, window, period, report, legal basis and confidence as the Políticas page shows provenance), each with "Proponer ajuste" linking to the SP-02 override flow (`/policy` with the capture code as a query parameter); the `PeriodTable` (capture, period, status, events, reconciled, discrepancies count with the ids behind a disclosure) with "Cerrar periodo" for `ACCESS.capturesClose` on ended `OPEN`/`REOPENED` rows (a `ConfirmDialog`; `PERIOD_NOT_ENDED` shown as a form alert); a captures list below filtered by period.
- i18n: `nav.captures`; `captures.{title,description,limits.*,kind.*,period.*,status.*,table.*,close.{action,confirm,done},propose,discrepancies.*,events.*}`; `errors.PERIOD_NOT_ENDED`.

- [ ] **Step 1: Write the failing tests:** `CapturesPage.test.tsx` (`shows the captures and limits with provenance`, `the close button is offered to the MLRO on ended periods only`, `a not-ended close shows the problem`); `PeriodTable.test.tsx` (`discrepancy ids are disclosed`); access rows.
- [ ] **Step 2: Run to verify failure.** `cd console && npm test -- captures`.
- [ ] **Step 3: Implement**, regenerating `schema.d.ts`.
- [ ] **Step 4: Run to verify pass.** The console gates.
- [ ] **Step 5: Commit** `feat(console): captures and limits with period reconciliation`.

---

## Interfaces for plan 4c

Named exactly as the spec does where the spec names them; the others are this plan's and are listed for 4c to consume:
- **The evaluator for the decide path (spec §12.1 step 5):** `active_rules(s, *, on, product) -> RuleSet` (B6) with `RuleSet.sync_rules`, `RuleSet.declarations` and `RuleSet.ruleset_hash()` (the `txn_decision.ruleset_hash`); `reader_for(store, txn, declarations_by_name)`, `profile_for(s, txn)` (B7) over a `MemoryAggregateStore` seeded with A7's `seed(key, declaration, PostgresAggregateStore(s).buckets(key, declaration, start=…, end=…))` and `add()`-ed the candidate; C5 evaluates `ruleset.sync_rules` with B2's `evaluate(rule.definition, ctx)` and reads a declaration whose `agg_declaration.backfilled_through` is NULL as `INCOMPLETE`; `EvalContext`, `EvalProfile`, `Firing`, `HitOutcome` (B2, B1). `BLOCK` when a `TIER_LIMIT` firing has `outcome BLOCK` or any firing has `severity CRITICAL`; `REVIEW` for any other firing.
- **The tier-limit notice (spec §12.1 step 6, §12.2):** `notice_tier_limit(s, *, relationship_id, account_id, tier, limit_code, period_key, outcome, source=NoticeSource.DECIDE) -> bool` (B7); it publishes `monitoring.tier_limit_reached.v1` only when it inserted the row, so the two paths send one webhook per relationship, limit and month. `LIMIT_CODE_MONTHLY_OPS = "monthly_ops"`.
- **Replay (spec §13.1, §16 `monitoring replay --dry-run`):** `replay_rule_set(s, ruleset, *, range_start, range_end, page) -> Iterator[SimulatedHit]` (B8) over `active_rules(...)` (C6's `replay_range`); `SimulatedHit.dedup_key` and `rule_version_id` are what the exit test compares with `rule_hit` rows.
- **Metrics (spec §13.2):** `RuleHit` (`rule_version_id`, `severity`, `created_at`, `first_occurred_at`, `party_id`), `MonitoringRule.typology`, `ReportableEvent` (`report_code`, `period_key`, `status`), `CapturePeriod.reconciled_count` (B4, B11); the indexes `ix_rule_hit_rule_period` and `ix_reportable_event_period`.
- **Counterparty screening (spec §11.2, §11.3):** `CounterpartyNameIndex` (`xolotl.modules.screening.counterparty.models`, B4) with `ix_cni_phonetic` and `ix_cni_trgm`; `ScreeningSubject.counterparty_id` and the `COUNTERPARTY` branch of `ck_screening_subject_kind_input`; `fk_counterparty_subject`. C1 needs B4 and nothing else of this plan; it adds `SubjectKind.COUNTERPARTY` and `RunMode.COUNTERPARTY` (text columns, no migration) and registers `screening.counterparty_status_changed.v1` staff-only itself.
- **Hits on the decision (spec §12.1, §15):** B12's `HitSummary.decision_id` is the trigger transaction's `transaction.decision_id`, which C5's linkage sets; C5 appends `decision: DecisionSummary | None` to `HitDetail` (`monitoring/schemas.py`) and fills it in `routes_hits.py`, and B14 renders it as "decided BLOCK at …" when present (the bare id until then).
- **Retention (spec §8.6):** the month's rows 4c deletes in order: `rule_hit_transaction`, `reportable_event` (and emptied `capture_group`), `rule_hit` by `first_occurred_at`; `tier_limit_notice` is kept (no transaction link); `capture_period` rows are kept.
- **Composition (B7):** `register_rules(*, queue, screening_status)` in `monitoring/rules/wiring.py` (Deviation 19), called by `wire_platform` after A5's `register_monitoring`; 4c registers nothing there (C5, C7 and C8 append to `register_monitoring`).
- **API and console:** `routes_rules.router` (prefix `/monitoring`), `routes_hits.router`, `routes_captures.router` (B9, B11, B12); `monitoring/schemas.py` (A10's file) for C6's metrics models; console `ACCESS.rules` and `ACCESS.monitoring` (B14; C9 gates its Perfil card with A11's `ACCESS.transactions` instead, so it does not wait for B14).

## Parallelism

| Id | Title | Depends on | Console | Branch slug |
|---|---|---|---|---|
| B1 | Jurisdiction lists loader, rule definitions and the rendered sentence | A7 | no | `sp04b-b1-definitions` |
| B2 | The pure evaluator: the seven kinds | B1 | no | `sp04b-b2-evaluator` |
| B3 | The Mexico typology library files, the loader and `rules validate` | B1 | no | `sp04b-b3-typology-library` |
| B4 | Migration 0013, models, enums, events, isolation factories | A2 | no | `sp04b-b4-migration-0013` |
| B5 | Templates in the database, tenant rules and versions with bounds, `rules load/show/seed-tenant` | B3, B4 | no | `sp04b-b5-templates-versions` |
| B6 | Lifecycle, assignments and the active rule set | B5 | no | `sp04b-b6-lifecycle` |
| B7 | Hits, dedup, cooldown, evidence, the tier-limit notice, the rules processing hook and staff-only registration | B2, B6, A8 | no | `sp04b-b7-hits-hook` |
| B8 | Simulation engine and job, `rules simulate`, `rules activate`, typology goldens | B7 | no | `sp04b-b8-simulation` |
| B9 | Rules API | B8 | no | `sp04b-b9-rules-api` |
| B10 | Capture definitions, maps, reportable events, groups, supersession, the captures hook, capture goldens | B4, B7 | no | `sp04b-b10-captures` |
| B11 | Capture periods, period close, schedule, CLI, captures API | B10 | no | `sp04b-b11-period-close-api` |
| B12 | Hits API | B7, A10 | no | `sp04b-b12-hits-api` |
| B13 | Console: Reglas › Monitoreo | B9 | yes | `sp04b-b13-console-rules` |
| B14 | Console: Hallazgos de reglas | B12, A11 | yes | `sp04b-b14-console-hits` |
| B15 | Console: Reglas › Capturas y límites | B11, A11 | yes | `sp04b-b15-console-captures` |

- **Wave 0, beside 4a (pure code):** B1 as soon as A7 is on `main`; then B2 ∥ B3. Neither needs a table. Nothing in 4a depends on B1: list names are resolved in this plan (reconciliation finding 3).
- **Wave 1, after A2:** B4 (it can start beside B2 and B3; the migrations merge A2 → B4 → C4). Then B5 (needs B3 and B4), then B6.
- **Wave 2:** B7 after B2, B6 and A8. Then **B8 ∥ B10 ∥ B12** (disjoint files; B8 and B10 both append to `rules/wiring.py`, additive).
- **Wave 3:** B9 after B8; B11 after B10. B9, B11 and B12 each add a router line to `app.py` and models to `schemas.py` (additive; the later one rebases).
- **Wave 4 (console):** B13 ∥ B14 ∥ B15 after B9, B12 and B11 respectively (B14 and B15 also after A11, whose transaction detail they extend). They share the additive console files and regenerate `schema.d.ts` against the merged API.
- **Across plans.** 4a, 4b and 4c all append to `src/xolotl/app.py`, `src/xolotl/cli.py`, `src/xolotl/models.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `.importlinter`, `src/xolotl/modules/tenants/service.py` and the pinned schedule tests, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `test_config.py`, `test_errors.py`, `tests/test_cli.py`, `src/xolotl/modules/monitoring/{events,errors,schemas,composition}.py`, `src/xolotl/modules/screening/counterparty/models.py` and the console's nav, routes, access and catalogues. Every edit is additive; whichever task merges second rebases. C1 needs only B4 from this plan and runs beside B5 to B12; C4 needs B4; C5 needs B7 and B12; C6 needs B8 and B11; C8 needs B7; C11 merges after B13, B14 and B15.
- **Estimate:** two weeks with three agents: wave 0 overlaps 4a's second week; waves 1 to 3 take about seven working days on the critical path B4 → B5 → B6 → B7 → B8 → B9 → B13; waves 2 and 3 run three tasks abreast.

## Exit criteria

The 4b share of spec §19 (the `rules/*`, `captures/*`, `simulation/*` rows, the platform and isolation rows, the console row) and of its exit paragraph, refined for this plan:
- `make ci` green after every task; the console gates and the Playwright smoke green after B13, B14 and B15.
- Every new table passes the RLS harness and the schema-sync gate; `rule_template` and `rule_template_version` are registered shared and read-only for the app role; the `approved_by <> author_id` CHECK refuses a same-author approval inserted in SQL (B4, B6).
- `xolotl rules load` installs the sixteen templates of the Mexico library, loading twice changes nothing, and a changed version is refused; `xolotl rules seed-tenant` gives tenant A (ITF `EMONEY` and AVI `CRYPTO`) draft versions for the templates of its packs; `xolotl rules activate` simulates, submits, approves with a second user and activates `CASH_STRUCTURING`, `COUNTERPARTY_TRUE_MATCH` and `TIER_N1_MONTHLY_OPS` (B3, B5, B8).
- Every kind's evaluator is proven pure and exact, and each of the sixteen templates fires its golden scenario once and its negative never (B2, B8).
- Parameters outside the template's bounds or looser than `tighten` allows are refused naming the bound and its source; the lifecycle matrix (state × role × same-user × action) matches spec §9.4 exhaustively; the author cannot approve; a simulation older than 30 days or of another definition blocks submission and approval; assignments resolve by date and product like packs (B5, B6).
- Over the exit fixtures (the week of 7 to 13 December 2026), `CASH_STRUCTURING` fires once with three near-threshold deposits as evidence; re-sending the batch creates no second hit; a late deposit re-fires nothing already recorded; the N1 overrun produces a `BLOCK` hit and exactly one `monitoring.tier_limit_reached.v1` with ids and codes only; a wildcard webhook endpoint never receives a `monitoring.rule_hit_*`, `reportable_event_*`, `capture_period_*`, `rule_version_*` or `simulation_*` delivery (B7).
- A simulation of the week writes only its own row and reproduces the stored hits; it reports hits per week, customers, overlap and an ids-only sample, and resumes after a crash with exact counts (B8).
- `ITF_ROR_CASH` captures exactly the cash operation at or above USD 5,000 at the prior banking day's FIX across the weekend of 12 and 13 December, and `AVI_XVI_A` captures the whole six-month group when the December operation crosses 210 UMA, each event carrying its period key, pre-mapped fields and unit values; a crypto operation dated 10 December arriving on 12 December supersedes and re-captures the affected group and reopens a closed period; `AVI_XVI_B` merges into `AVI_XVI_A` (B10).
- `xolotl monitoring period-close` closes December 2026 and Q4 2026 with zero discrepancies on clean data and names an injected gap; the schedule runs it monthly per tenant (B11).
- Staff see hits with evidence and captures with their chains through the API; API clients cannot reach hits, captures, rule names or counterparty statuses; the reveal is audited per group; no plaintext is stored beyond the encrypted columns and the name indexes (B9, B11, B12).
- Console: the library shows every template's sentence; an analyst tunes a parameter within its bounds, simulates, submits, and a second user approves and activates from `allowed_actions` with `blocked_reasons` as tooltips; the hit queue opens on the highest severity with rows and the detail shows the rendered rule, aggregates against thresholds and the evidence transactions; the captures page shows the effective captures and limits with provenance and the period table with "Cerrar periodo" for the MLRO (B13, B14, B15).
- Tenant B sees none of tenant A's rules, hits, simulations or captures (every route test's "another tenant sees nothing").

## Open questions for the product owner

1. **The `tax_haven` set is empty.** Spec §9.1 names `TAX_HAVEN` among the lists; no template of §9.3 reads it and no source is named. *Default:* the loader accepts an empty set and the file says so; a template that names it is a load error until a source is chosen.
2. **What `BORDER_ZONE_MX` contains.** Spec §9.3 says "the region list in `_jurisdictions.yaml`" without the codes. *Default:* the six border states as ISO 3166-2 codes (`MX-BCN`, `MX-SON`, `MX-CHH`, `MX-COA`, `MX-NLE`, `MX-TAM`), marked `UNVERIFIED`; the customs definition (the 20 km strip and named municipalities) needs admin-area codes the transaction's `geo_admin_area` would have to carry.
3. **The Playwright flow of spec §19's console row** (tune a rule parameter, simulate, submit, approve with a second user, activate) needs processed transactions and a second staff user on the smoke stack. *Default:* B13's smoke proves the library renders; B9's route test proves the flow at the API level with two users; plan 4c's exit test (`tests/e2e/test_sp04_exit.py`) scripts it over the committed fixtures.
4. **Who may submit.** Spec §9.4 lets only the author submit (built, with `NOT_AUTHOR`, Deviation 3). Should another writer of the same tenant be allowed to submit a colleague's simulated version, for instance when the author is away?
5. **`rules.loaded` as an audit action.** Spec §14 lists it for the CLI under the owner role, but shared seeding has no tenant chain to write to (Deviation 2 follows SP-03a's list ingestion). Confirm that `rule_template_version.loaded_by`, `loaded_at` and `source_path` are provenance enough, or name the tenant whose chain should carry it.
6. **A simulation shorter than four months.** With Deviation 13, `DECLARED_VS_ACTUAL_VOLUME`'s history branch never fires in such a simulation and the simulation says so in `notes`. Confirm, or ask the simulation to read the relationship's real history from before the range (one query per party, which the spec's "replays Postgres history per key" allows).
7. **The AVI pack's crypto templates.** Spec §9.3 does not say which templates `MX/LFPIORPI-AVI` carries. *Default (B3):* the AVI pack carries `CRYPTO_OFF_RAMP`, `SELF_CUSTODY_OUTFLOW`, `RAPID_PASS_THROUGH` and `NEW_ACCOUNT_VELOCITY` (besides the three screening and PEP templates both packs carry). Confirm, or name a different set; a change is a new version of each file touched.
8. **`PROFILE_DEVIATION`'s trailing average is understated as a month advances.** The trailing `P6M` sum minus the month-to-date sum spans the six months before the transaction less the part of the current month already elapsed, so between about 5 and 6 months of history, but B2 divides it by 6 (or by the history months when fewer). The average is right on the month's first day and understated by up to about 17% on its last (5/6 of the history over 6 months), with the ratio overstated accordingly. *Default:* divide by 6; a precise divisor would need the trailing window's start, which the declaration does not carry.
9. **Segment values are only checked for shape (B5 review M3).** Spec §9.4 says "segment valid", but `SegmentFilter` accepts any strings, so a typo (`tier: ["n2"]`) makes a rule that never fires. *Default:* structural only in SP-04. Tiers could be checked against the effective policy's `kyc.tiers` (B9 or later); risk ratings and monitoring profiles have no domain until SP-07.
10. **Existing rules keep their original template version (B5 review M5).** `create_version` keeps the latest version's `template_version_id`, and Deviation 6 sends `create_rule` there, so once a template v2 is loaded an existing rule has no path to adopt it, including a tightened bound. This is consistent with spec §9.4 ("a new version from the active or draft one"). *Default:* unchanged. A later task may add "rebase on the latest template version".
11. **Template provenance outside the hash (B5 review M6).** `packs` and `country_code` are wrapper fields outside `definition_hash`, and `rule_template` is upserted on every load, so editing `packs:` in a loaded v1 file without a version bump silently changes which tenants future seeds reach. *Default:* follow the plan ("packs from the latest version"). Bump the version when packs change; including the wrapper in the immutability check would need the stored packs per version (a column 0013 lacks).
12. **The rejection reason has no column (B6 review I2).** Spec §9.4 rejects "with a reason", but 0013 gives `monitoring_rule_version` no column for it and audit details hold codes only, so B6 requires a reason and records `reason_given: true` without its text. *Default:* a later migration adds an encrypted `rejection_note` (as SP-03's `match_disposition.rationale`), and the reason is shown to the author from there.
