# SP-04c — Counterparty screening, decide, replay, profiles and exit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The working agreement `docs/superpowers/execution/sp04/agreement.md` (gates, worktrees, commits, PRs, report) applies to every task; the cross-plan decisions the three SP-04 plans carry are in `docs/superpowers/plans/2026-10-20-sp04-reconciliation.md`.

**Goal:** Finish SP-04 and prove it. A counterparty a transaction names becomes a screening subject of its own kind: it is screened asynchronously on first sight (its wallet address exactly, its name fuzzily), re-screened when a shared list changes and by the monthly walk, and its row is the synchronous status cache that the enricher and the decide path read. `POST /v1/transactions/decide` answers `ALLOW`, `REVIEW` or `BLOCK` from indexed point reads inside an 80 ms budget, with a fail mode per rail, stores every decision in the monthly-partitioned `txn_decision` and links it to the transaction that arrives later. The N1 and N2 monthly-operations limits block or review on that path and send the one webhook a client gets, once per relationship and month. `xolotl monitoring replay --dry-run` reproduces stored hits without side effects, `GET /v1/monitoring/metrics` computes effectiveness on read, `monitoring.retention` retires months in dependency order and purges orphaned counterparties, `monitoring.profiles` writes monthly behaviour profiles against a declared profile that staff set through `PATCH /v1/relationships/{id}`. The console gets Contrapartes, Admin › Rieles and the Cliente › Perfil de comportamiento tab. Perf tests record the decide and alert latencies. `tests/e2e/test_sp04_exit.py` scripts the program's SP-04 exit demo over committed fixtures.

**Architecture:** Counterparties stay in `screening` (spec Decision 18): plan 4a's `screening/counterparty/` package gains subjects, queries, the name index writer, the first-sight job, the status write-back and the reverse-blocking query of this plan; the list-delta job and the periodic walker of SP-03c learn to visit counterparties after parties, one page per transaction as before. Forward scoring always goes through SP-03b's `screen()`, which gains one option (`entity_type`) and one `subject_query` branch; nothing else in the engine changes. The decide path lives in `monitoring/decide/`: validation and resolution reuse 4a's ingest pieces, the rules and the pure evaluator are 4b's, the aggregates come from one `ix_agg_bucket_window` range read per declaration into a `MemoryAggregateStore` seeded with the candidate, and the decision row is written in the request's transaction with the fail mode taking over when the budget or the database gives out. Replay and metrics are read-only functions over 4b's tables. Retention runs on the admin engine for the DDL steps and in the tenant session for everything else, one step per transaction. Behaviour profiles are a monthly job over `PROCESSED` rows. Every event carries ids only and every one of them is staff-only (`register_staff_only`); the spec's opt-in webhook has no platform mechanism and SP-04 builds none (reconciliation finding 7).

**Tech Stack:** the stack on `main` after plans 4a and 4b are merged. No new runtime dependency. The perf tests use the standard library and the helpers of `tests/perf/conftest.py`.

**Spec:** `docs/superpowers/specs/2026-10-20-sp04-monitoring-captures-design.md`: §1 (the counterparty screening, decide, simulation and behaviour-profile bullets), §1.1 (the third row), §2 (layout; `screening/counterparty/` is completed here; the migration bullet, renumbered below), §3 (`MONITORING_DECIDE_BUDGET_MS`, `MONITORING_DECIDE_FAIL_MODE`, `MONITORING_PROFILE_CRON`, `MONITORING_COUNTERPARTY_SCREEN_BATCH`, `MONITORING_PROFILE_DEVIATION_RATIO`, `MONITORING_RETENTION_CRON`), §5.2 (the "Migration 0013 (plan 4c)" block, built here as 0014; the counterparty columns of the 4a and 4b blocks), §6.1 step 3 (decision linkage), §8.6 (retention), §9.3 (`COUNTERPARTY_TRUE_MATCH`, `BLOCKED_PERSON_PARTY`, the `TIER_*` templates), §11 (all of it), §12 (all of it), §13.1 (the replay sentence), §13.2, §13.3, §14 (the `monitoring.decision_issued.v1`, `monitoring.tier_limit_reached.v1`, `monitoring.behaviour_profile_updated.v1` and `screening.counterparty_status_changed.v1` rows; the audit actions `monitoring.rail_setting_changed`, `relationship.declared_profile_set`), §15 (the decide, decisions, rails, metrics, profiles, counterparties and `PATCH /v1/relationships/{id}` rows; the scopes `transactions:decide` and `monitoring:read`; the errors `RAIL_UNKNOWN`, `PERIOD_INVALID`), §16 (`monitoring replay`, `monitoring profiles`, `screening screen-counterparties`), §17 (Contrapartes, the Perfil tab, Admin › Rieles), §18 rows FR-TM-003, FR-TM-008, FR-TM-012, FR-TM-013, FR-TM-018, NFR-TM-001, NFR-TM-002, NFR-TM-005, DR-506, DR-507, INT-505, OPS-502, FR-SC-012, DR-308, FR-SC-015, §19 (the `decide/*`, `simulation/*`, `profiles/*` and `screening/counterparty/*` rows and the exit criteria), §20 Decisions 9, 10, 11, 18, 20, 22, §21 Q3, Q4, Q5, Q7, Q8, §22 (the 4c row), §23 (cut list). Program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md` §7.3, the SP-05 demo whose first clause "transactions in; captures" is SP-04's exit. SP-03 spec `docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md` §8 and §10 for what it deferred here (`FR-SC-012`, `DR-308`, `FR-SC-015`).

**Accepted by the product owner (blanket approval of 2026-10-19, applied to the SP-04 draft of 2026-10-20):** the 22 decisions of spec §20 and the defaults of the ten open questions of §21. The ones that bind this plan: Decision 9 (counterparties are rows and subjects of their own kind; the row is the synchronous cache; no synchronous screening on the decide path), Decision 10 (`PROFILE_DEVIATION` reads `customer_relationship.declared_profile`, set by staff until SP-08), Decision 11 (the client sees `TIER_LIMIT` or `RISK_REVIEW`, never a rule name), Decision 18 (screening owns counterparties), Decision 20 (wallet addresses are PII-class), Decision 22 (retention retires a month in dependency order); Q3 (`XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO = 2.0`), Q4 (`FAIL_OPEN` answering `REVIEW` for every rail without a setting), Q5 (reason codes as Decision 11), Q7 (thresholds read the fiat `amount`), Q8 (counterparties without a transaction in 12 months are skipped by the periodic walk). This plan builds those defaults and does not reopen them.

## Global Constraints

- **Migration numbering.** The chain on `main` ends at `0011_screening_hit_run_index` (merged 2026-10-20), so the spec's SP-04 migrations are renumbered: plan 4a's is `0012_transactions`, plan 4b's is `0013_rules_captures` and this plan's is **`0014_decide_profiles`** (`revision = "0014_decide_profiles"`, `down_revision = "0013_rules_captures"`), built by C4 with exactly the "Migration 0013 (plan 4c)" block of spec §5.2 plus the two additions in Deviations. Wherever the spec writes 0011, 0012 and 0013, read 0012, 0013 and 0014. `"0014_decide_profiles"` is appended to `CHAIN` in `tests/platform/test_migration_chain.py` after `"0013_rules_captures"`. No other task of this plan adds a migration.
- Repository `/Users/omaribannez/code/xolotl`. Every task is one PR from `main` on branch `feat/sp04c-c<n>-<short>` in its own worktree; `make ci` green at the end of every task; the console gates (`cd console && npm ci && npm run gen:api:file && npm run typecheck && npm run lint && npm test && npm run build`) whenever `console/` changes (C3, C9).
- **MVP profile is binding:** one application container plus Postgres. No new service, no cache, no worker pool beyond the existing runner. The decide path is a request handler over indexed reads; nothing is precomputed in memory across requests except 4b's per-process rule-set cache.
- **Tenant tables** of 0014 (`rail_setting`, `txn_decision`, `behaviour_profile`) follow the kernel: `(tenant_id, id)` primary key (`txn_decision`: `(tenant_id, decided_at, id)`), composite foreign keys through `tenant_fk` or a named `ForeignKeyConstraint`, `enable_rls` in the migration, one isolation factory each in `tests/isolation/factories.py::register_all`, models imported in `src/xolotl/models.py`, the schema-sync test green. `txn_decision` is partitioned like `transaction`: a `DEFAULT` partition plus three months ahead created by the migration through `xolotl.platform.partitions.ensure_partitions(conn, table="txn_decision", ...)` (A1's generalisation), each partition hardened with forced RLS, and the isolation harness discovers partitions as tables (A1's change). `SHARED_TABLES` is untouched.
- **Encrypted columns:** none new. `counterparty.name`, `.account`, `.wallet` and `.identifier` are 4a's `EncryptedStr` columns (spec `*_enc`; no `_enc` suffix, as every plan since SP-01); this plan reveals them only through `reveal_for_response` (C3), never in a list, a log, a payload or a job.
- **Import rules** (`.importlinter`): `screening` never imports `monitoring` (A2's contract `screening-never-imports-monitoring`; this plan keeps it: the counterparty tasks touch `screening` and `platform` only). `monitoring` imports `xolotl.modules.party` and `xolotl.modules.screening` service functions only, never their `models` (A2's contract `monitoring-boundaries`). `screening.counterparty.status` imports nothing from `screening.engine` (it is called from `engine/subjects.py`; a cycle would be an import error at start-up).
- **Settings** (`src/xolotl/platform/config.py`, prefix `XOLOTL_`): `monitoring_counterparty_screen_batch: int = Field(default=100, ge=1)` (C1); `monitoring_decide_budget_ms: int = Field(default=80, ge=1)` and `monitoring_decide_fail_mode: FailMode = FailMode.FAIL_OPEN` (C4); `monitoring_retention_cron: str = "0 5 1 * *"` (C7); `monitoring_profile_cron: str = "0 3 2 * *"` and `monitoring_profile_deviation_ratio: Decimal = Field(default=Decimal("2.0"), gt=0)` (C8). The two crons join the `@field_validator(...)` list of `_check_cron`. Every setting goes in `.env.example`, the README configuration table and the `model_fields_set` assertion of `tests/platform/test_config.py::test_env_example_is_a_valid_configuration`. None of the six is 4a's or 4b's: the 23 `MONITORING_*` keys of spec §3 split twelve (4a), five (4b) and six (this plan), reconciliation finding 18.
- **Job kinds** (constants in the module that handles them): `screening.screen_counterparties` (`FIRST_SIGHT_KIND`, C1; key `screen_counterparties:<YYYY-MM-DDTHH:MM UTC>` for first-sight enqueues, `screen_counterparties:continue:<job id>` for a follow-up page; payload `{}`), `monitoring.retention` (`RETENTION_KIND`, C7; payload `{}`), `monitoring.profiles` (`PROFILES_KIND`, C8; payload `{}` from the schedule or `{"month": "YYYY-MM"}` from the CLI). Each job handles one page in one transaction and enqueues its follow-up in that transaction, as SP-03c's jobs do.
- **Schedules** (per tenant, in `schedule_platform_jobs`, `src/xolotl/modules/tenants/service.py`): `monitoring.profiles` (`MONITORING_PROFILES_SCHEDULE`, cron `get_settings().monitoring_profile_cron`, `tz = "America/Mexico_City"`, payload `{}`, C8) and `monitoring.retention` (`MONITORING_RETENTION_SCHEDULE`, cron `get_settings().monitoring_retention_cron`, `tz = "UTC"`, payload `{}`, C7). The pinned rows in `tests/platform/test_worker.py::PLATFORM_SCHEDULES` and `tests/modules/tenants/test_kernel_events.py` are inserted in name order by the task that adds the schedule, and the non-hourly name sets of those tests gain the name. A9's `monitoring.sweep` and `monitoring.partitions` and B11's `monitoring.period_close` rows are already there.
- **Events:** `screening.counterparty_status_changed.v1` (`COUNTERPARTY_STATUS_CHANGED` in `screening/events.py`, appended to `STAFF_ONLY_EVENT_TYPES`, data `{"counterparty_id", "subject_id", "status"}`, key `str(subject_id)`, C1); `monitoring.decision_issued.v1` (`DECISION_ISSUED`, appended by C5 to A4's `monitoring/events.py` and to its `STAFF_ONLY_EVENT_TYPES`, data `{"decision_id", "external_id", "decision", "mode"}`, key `str(relationship_id)`; staff-only, not opt-in: the platform has no per-endpoint opt-in and SP-04 adds none, reconciliation finding 7); `monitoring.behaviour_profile_updated.v1` (`BEHAVIOUR_PROFILE_UPDATED`, appended by C8 the same way, data `{"relationship_id", "party_id", "month", "deviated"}`, key `str(relationship_id)`); `monitoring.tier_limit_reached.v1` is B4's constant `TIER_LIMIT_REACHED`, published by C5 through B7's `notice_tier_limit`. Ids and codes only, never an amount, a name or an address. The staff-only types are registered at composition (`register_screening` for screening's tuple, A5's `register_monitoring` for 4a's), so a wildcard webhook endpoint never gets them.
- **Audit actions:** `monitoring.rail_setting_changed` (entity `rail_setting`, detail `{"rail", "fail_mode"}`, C4), `relationship.declared_profile_set` (entity `customer_relationship`, detail `{"currency"}` only, C8), `monitoring.retention_ran` (entity `tenant`, detail: counts per step and the skipped months, C7), `VIEW_PII` through `reveal_for_response` on the counterparty reveal (C3; one row per revealed field). Decisions are not audited per request (volume; the `txn_decision` row with `actor` is the record, spec §14).
- **API:** routers come from `v1_router`; access from `require_access`; POST and PUT and PATCH honour `Idempotency-Key`; lists paginate with `paginate(...)`. Machine scopes: `transactions:decide` (`write=True`) for the decide route, `transactions:read` for the client decision view, `monitoring:read` for metrics, `parties:write` for the relationship PATCH; staff-only routes declare no scope. Scopes are free strings on an API key (no catalogue on `main`), so no file lists them. An API client never receives a rule name, an aggregate, a screening status, a hit or a capture (D20, `PRV-502`).
- **Problem codes** introduced: `RAIL_UNKNOWN` (404, `RailUnknownError(NotFoundError)`, C4) and `PERIOD_INVALID` (422, `PeriodInvalidError(ValidationFailedError)`, C6), both appended to A4's `monitoring/errors.py`, each with a row in `tests/platform/test_errors.py`.
- **Plaintext:** nothing in job payloads, `last_error`, logs, event data, `txn_decision.reasons`, `behaviour_profile.metrics` or `rule_hit` names a person, an account or an address. A wallet address appears to a human only masked (`****a1b2`, the library's masking) or through the audited reveal.
- **Dates:** "today" is `policy_date(clock.now())` (A6's `monitoring.enrich.policy`, America/Mexico_City) for policy resolution and the profile month; periods and bucket keys are 4a's. Tests patch `xolotl.platform.clock.now` as `tests/modules/screening/rescreen/test_walkers.py::test_the_periodic_job_opens_one_batch_per_month` does.
- **Console:** Spanish-first keyed strings in both catalogues; `schema.d.ts` regenerated against the merged API in every console task; revealed values only through `useMutation` with `gcTime: 0`; no string a customer could see mentions a rule, a hit, a capture or a suspicion (DD-00); the limit webhook's documented copy is "límite de nivel de cuenta".
- **Commit trailers:** every commit ends with exactly the `Co-Authored-By` line the task prompt gives.

## Dependencies outside this plan

`docs/superpowers/plans/2026-10-20-sp04-reconciliation.md` aligned the names below with what 4a and 4b build; each item names the task that provides it. Everything under "SP-03 on `main`" and "The screening library" is on `main` at `0c68cb7`.

**Plan 4a** (`docs/superpowers/plans/2026-10-20-sp04a-transactions-aggregates.md`, tasks A1 to A12):
- Migration `0012_transactions` (A2) with the first §5.2 block, including the `counterparty` table; `xolotl.platform.partitions` (A1) with `PartitionedTable`, `register_partitioned_table`, `ensure_partitions(conn, *, table, months_ahead, start)`, `harden_partition(conn, name, *, truncate_guard=None)` and `partition_name(table, month)`; the daily `monitoring.partitions` job (A9) covers every registered table, so C4's `decide/models.py` registers `PartitionedTable("txn_decision", "decided_at", None)` at import and touches no job.
- `src/xolotl/modules/screening/counterparty/`: `schema.py` (A3) with `CounterpartyInput`, `CounterpartyKind`, `CounterpartyScreeningStatus` (`UNSCREENED`, `CLEAR`, `PENDING_REVIEW`, `TRUE_MATCH`) and `CounterpartyIdentifier`; `models.py` (A2) with `Counterparty` (`id`, `kind`, `name`, `name_hash`, `account`, `account_hash`, `institution`, `country`, `wallet`, `wallet_hash`, `wallet_network`, `identifier_type`, `identifier`, `identifier_hash`, `dedup_hash`, `screening_status`, `screened_at`, `screening_subject_id`, `first_seen_at`, `last_seen_at`, `txn_count`); `resolve.py` (A4) with `resolve(s, input) -> CounterpartyRef` (`id`, `dedup_hash`, `country`, `screening_status`, `created: bool`), `dedup_hash_of(s, input) -> bytes`, `status_by_dedup_hash(s, dedup_hash) -> CounterpartyScreeningStatus`, `status_of(s, counterparty_id)` (A6) and the component helpers `name_hash_of(s, name, *, entity_type)`, `account_hash_of(s, account)`, `wallet_hash_of(s, address)`, `identifier_hash_of(s, id_type, value)`; `schemas.py` and `views.py` (A10) with `CounterpartySummary`, `RevealedCounterparty`, `counterparty_summary(s, counterparty_id)` and `reveal_counterparty(s, counterparty_id)` (one `VIEW_PII` row per non-null field), which C3 reuses.
- `monitoring/txn/schema.py` (A3): `TransactionInput`, `Rail`, `Direction`, `Instrument`, `FieldError`, `parse_item(index, data, *, now, max_future_minutes)` (the §4 validation C5's route runs on its one item); `monitoring/txn/ingest.py` (A4) with `ingest_batch(session, items, *, source, submitted_by, queue, file_id=None, request_id=None) -> BatchReport`, where C5 inserts `link_decisions` (spec §6.1 step 3; 4a Deviation 4); `monitoring/txn/models.py` (A2) with `Transaction`, `Account`, `TransactionBatch`; `monitoring/txn/accounts.py` (A4) with `find_account(session, relationship_id, account_ref) -> Account | None` (a read, no upsert: the decide path's account); `xolotl.modules.party.service.relationship_by_external_ref(s, refs) -> dict[str, RelationshipRef]` and `RelationshipRef(id, party_id, product, account_tier, status)` (A4).
- `monitoring/enrich/` (A6): `EnrichedTransaction` (A3) and `enrich_input(session, item, *, relationship, account_id, units, holidays, snapshot, now) -> EnrichedTransaction`, the enrichment of a validated input without a `transaction` row (the decide path; no counterparty upsert, the status read by `dedup_hash`); `enrich/policy.py` with `SnapshotRef(snapshot_id, policy)`, `snapshot_for(session, *, on, product, cache)` and `policy_date(at) -> date`. The evaluator's profile is B7's `profile_for(s, txn) -> EvalProfile`, which C8 extends with `declared_profile`.
- `monitoring/aggregates/` (A7): `AggregateStore`, `PostgresAggregateStore(s)` (`buckets(key, declaration, *, start, end) -> list[BucketRow]`), `MemoryAggregateStore()` (`seed(key, declaration, buckets)`), `AggregateDeclaration`, `AggregateKey`, `AggregateValue`, `window_bounds`, `period_bucket_start`, `accum_window`, `load_declarations`; `KeyKind` (A3).
- `monitoring/composition.py::register_monitoring(*, session_factory, settings, queue, store, notifier)` (A5), `monitoring/events.py` (A4) with `publish_monitoring_event(s, event_type, *, entity, entity_id, key, data, policy_snapshot_id=None)` and `STAFF_ONLY_EVENT_TYPES`, `monitoring/errors.py` (A4), `monitoring/schemas.py` (A10), `monitoring/enums.py` (A2), `monitoring/kinds.py` (A4), `routes_transactions.py` and `routes_monitoring.py` (A10), and the `monitoring_app` Typer group in `src/xolotl/cli.py` (A5).
- No opt-in webhook mechanism: `register_opt_in` does not exist on `main` and SP-04 adds none; `monitoring.decision_issued.v1` is staff-only like 4a's `monitoring.transaction_processed.v1` (4a Deviation 7, reconciliation finding 7). Whether a per-endpoint opt-in is wanted is a product-owner question.
- Test support (A2, A4, A7, A8): `tests/modules/monitoring/support.py` with `itf_tenant`, `both_packs_tenant(tenant, admin_engine) -> UUID` (ITF for `EMONEY`, AVI for `CRYPTO`, holidays and the golden unit values), `make_item(**overrides) -> dict`, `ingest_items(session, items, *, queue) -> BatchReport` and `run_processing(services, tenant_id) -> int`; the isolation helpers `_customer_relationship`, `_account`, `_counterparty`, `_transaction` (A2).

**Plan 4b** (`docs/superpowers/plans/2026-10-20-sp04b-rules-captures.md`, tasks B1 to B15):
- Migration `0013_rules_captures` (**B4**), which adds `screening_subject.counterparty_id`, the new `ck_screening_subject_kind_input`, `uq_screening_subject_counterparty`, `fk_counterparty_subject` and `counterparty_name_index` (spec §5.2 end of the 0012 block), together with `ScreeningSubject.counterparty_id: Mapped[UUID | None]` on SP-03b's model and the model `CounterpartyNameIndex` in `screening/counterparty/models.py`. C1 needs B4 and nothing else from 4b, which is what lets counterparty screening run beside the rest of 4b (spec §22's "after 4a, in parallel with 4b", read as "after A4 and B4").
- `monitoring/rules/` (B1, B2, B6, B7): `RuleDefinition`, `RuleKind`, `Severity`, `HitOutcome` (B1); `Firing(rule_code, key_kind, key, window_start, window_end, severity, outcome, aggregates, params, explanation)`, `EvalContext(txn, aggregates, profile, policy, lists, now)`, `EvalProfile`, `DeclaredProfile(monthly_inflow_minor, monthly_outflow_minor, currency)`, `INCOMPLETE`, `evaluate(definition, ctx) -> Firing | None` (B2); `active_rules(s, *, on, product) -> RuleSet` with `RuleSet.rules`, `RuleSet.sync_rules`, `RuleSet.declarations`, `RuleSet.lists` and `RuleSet.ruleset_hash()` (the sha256 over the sorted definition hashes of the sync rules: `txn_decision.ruleset_hash`), `assignments_on(s, *, on, product)`, `LoadedRule(rule_id, rule_version_id, rule_code, assignment_id, definition, …)` (B6); `reader_for(store, txn, declarations_by_name)`, `profile_for(s, txn) -> EvalProfile` (B7); `MonitoringRuleVersion`, `RuleHit`, `RuleHitTransaction`, `RuleAssignment`, `RuleTemplate` in `monitoring/rules/models.py` (B4); `monitoring/rules/tier_limits.py::notice_tier_limit(s, *, relationship_id, account_id, tier, limit_code, period_key, outcome, source: NoticeSource) -> bool` and `LIMIT_CODE_MONTHLY_OPS` (B7), the `ON CONFLICT DO NOTHING` insert that publishes `monitoring.tier_limit_reached.v1` only when it inserted (spec §12.1 step 6, §12.2); `NoticeSource` (B4); `xolotl.platform.policy.load_jurisdictions()` (B1).
- `monitoring/captures/models.py` (B4) with `ReportableEvent`, `CaptureGroup`, `CapturePeriod`; the period rows with `reconciled_count` that B11's close writes.
- `monitoring/simulation/engine.py::replay_rule_set(s, ruleset, *, range_start, range_end, page) -> Iterator[SimulatedHit]` (B8), the party-aligned in-memory replay the `monitoring.simulate` job wraps, with `SimulatedHit.rule_version_id`, `.rule_code` and `.dedup_key`; C6 runs it over `active_rules(...)`.
- The hit detail (B12): `HitSummary.decision_id` is the trigger transaction's `transaction.decision_id`; C5 appends `decision: DecisionSummary | None` to `HitDetail` in `monitoring/schemas.py` and fills it in `routes_hits.py`. The template files `COUNTERPARTY_TRUE_MATCH.yaml` and `BLOCKED_PERSON_PARTY.yaml` under `policy-packs/MX/_typologies/` (B3) fire only once C1 lands.
- The staff-only registration of this plan's `monitoring.*` events in B7's `register_rules` (`RULES_STAFF_ONLY_EVENT_TYPES`).

**SP-03 on `main`** (plans 3a, 3b, 3c; the engine this plan extends): `screen(s, subject, *, mode, trigger, **ScreenOptions) -> RunResult` with `ScreenOptions` (`lists`, `only_entries`, `only_index`, `require_versions`, `batch_id`) and `subject_query(s, subject)` in `engine/screen.py`; `RunMode`, `SubjectKind`, `TriggerType`, `BatchKind`, `NameKind` in `engine/enums.py`; `subject_for_party`, `refresh_subject_status` in `engine/subjects.py`; `index_party_names` in `engine/names.py` (the pattern C1 mirrors); `get_index_holder().shared(s, require=...)` and `SharedShard.blocking_idf`; `rescreen/blocking.py` (`reverse_candidates`, `name_plans`, `stop_keys`, `blocking_query`, `BLOCKING_CAP`, `TRIGRAM_THRESHOLD`, the stop-key rule merged in PR #95), `rescreen/list_delta.py` (`handle_list_delta`, `_blocked_by_party`), `rescreen/walkers.py` (`handle_periodic_rescreen`, `_walk_page`, `enqueue_walk`, `WALKER_START`), `rescreen/batches.py`, `rescreen/purge.py`; `api.py` (`_masked`, `_subject_query`, `match_detail`, `reveal_match`) and `schemas.py` (`MaskedSubject`, `MatchSummary`); `routes_screening.py`; `screening_app` in `cli.py`; `tests/e2e/conftest.py::drain`, `tests/e2e/test_sp03_exit.py` (`Receiver`, `section`, `_delivered_types`, the fixtures of the demo), `tests/perf/conftest.py::record`.

**The screening library** (`src/xolotl/modules/screening/{lists,matching}/`, another engineer's; read-only): `NameQuery` (its `names` is a tuple that may be empty: `query_for_profile` already builds `NameQuery(names=())` for a purged party), `QueryIdentifier`, `IdType.DIGITAL_CURRENCY_ADDRESS` (parsed by the OFAC parser with the network as its label), `MemoryIndex.candidates` (identifier hits first, all of them), `score()` whose `ID_MATCH_FLOOR` rule floors an identifier match at `id_match_floor` (0.95 in both packs, above `sanctions_alert` 0.70) with no name at all, `normalize_name`, `name_keys`, `blocking_plan`, `IdfTable`, `MATCHER_VERSION`. The optional-names change of spec §11.4 is **not needed** against the library on `main` (see Deviations); nothing is planned into the library, and a task that finds the library refusing an empty `names` reports `BLOCKED` to its owner instead of working around it.

## Deviations from the sub-spec (decided here)

1. **No library change for wallet-only counterparties.** Spec §11.4 asks the library to make `NameQuery.names` optional. On `main`, `names=()` is already a valid `NameQuery`, `MemoryIndex.candidates` returns exact identifier hits before any name candidate, and `score()` yields `ID_MATCH_FLOOR` (0.95) through `_identifier_rules` when no name scores, which bands `ALERT`. C1 builds the wallet-only query as `NameQuery(entity_type, names=(), identifiers=(QueryIdentifier(IdType.DIGITAL_CURRENCY_ADDRESS, normalized=address),))` and pins the behaviour with `test_a_wallet_only_counterparty_alerts_on_an_exact_ofac_address`. The request to the library owner is withdrawn unless that test ever fails on a library bump.
2. **A counterparty without a name is screened under both entity types.** Spec §11.2 screens `UNKNOWN` as `PERSON` and `ORGANIZATION` in two runs. An address is a hash and a list entry's entity type says nothing about who sends from it, so a name-less counterparty (wallet or identifier only) is screened both ways whatever its `kind`; a named `PERSON` or `ORGANIZATION` gets one run. The stricter status wins by construction: both runs raise findings on the same subject and `refresh_subject_status` reads all of them.
3. **`screen()` gains the option `entity_type`.** SP-03's reconciliation fixed one `screen()` signature; this plan adds one `ScreenOptions` key, `entity_type: EntityType | None`, read only by the `COUNTERPARTY` branch of `subject_query` (a `PARTY` or `AD_HOC` subject ignores it). It is how the two runs of Deviation 2 differ; the run's `query_fingerprint` records it.
4. **The status cache follows dispositions, not only runs.** Spec §11.2 says the engine writes `counterparty.screening_status` after a run. A `TRUE_MATCH` is confirmed by a disposition days later, and `COUNTERPARTY_TRUE_MATCH` must fire on the next transaction, so the write-back lives in `refresh_subject_status` (every status change of a `COUNTERPARTY` subject updates the row and publishes `screening.counterparty_status_changed.v1`); `screened_at` is stamped after each run. The row never holds a status its subject does not.
5. **The first-sight queue is a process-level hook.** `resolve(s, input)` has no queue parameter and the spec keeps its signature, so `screening/counterparty/first_sight.py` holds a module-level queue set at composition (`set_first_sight_queue`, like `set_index_holder`), and `resolve()` calls `on_counterparty_created(s, ref.id)` after an insert. Without a queue set (a unit test of 4a's resolver) the hook indexes the name and enqueues nothing.
6. **The periodic walk's counterparty pages are the same batch.** After the party pages complete, the `PERIODIC` batch continues with counterparty pages (cursor `cp:<counterparty id>`, payload `after_counterparty_id`) and completes after a short counterparty page; `total` counts both. Private-list and re-index walks stay party-only (spec §11.3 names only deltas and the periodic walk; Open questions 1 and 2).
7. **`txn_decision` gets an index on `(tenant_id, id)`.** `GET /v1/monitoring/decisions/{id}` looks a decision up by id, and the partitioned primary key `(tenant_id, decided_at, id)` would scan every partition. 0014 adds `ix_txn_decision_id`.
8. **The decide budget is wall time with one check per step.** `decide()` reads `time.monotonic()` after each of the six steps of spec §12.1 and switches to the rail's fail mode when the elapsed time passes `MONITORING_DECIDE_BUDGET_MS`, or when any step raises a database error, A6's `ParkTransactionError` (`monitoring.enrich.service`; it wraps `UnitValueMissingError` as `UNIT_VALUE_MISSING` and a missing policy as `POLICY_NOT_ASSIGNED`), `UnitValueMissingError` or `PolicyNotAssignedError` raised directly, or `ScreeningIndexUnavailableError`. `db_ms` is the time spent inside the read steps (2 to 4), measured the same way; no cursor-level instrumentation.
9. **A failed decision row is retried once in a savepoint, then logged.** The decision row is written inside a `begin_nested()`; a failure there is logged `monitoring.decision_unrecorded` (decision, mode, external id) and the response still goes out (`INT-505`). The row is never written outside the request's session.
10. **Replay is dry-run only.** `monitoring replay` requires `--dry-run`; without it the command exits 2 with `error: only --dry-run is supported in SP-04`. The flag is kept so a future side-effecting replay changes nothing in the CLI contract.
11. **The retention purge of counterparties runs on the admin engine.** A counterparty's findings may carry `match_disposition` rows, which are append-only for the application role (`APPEND_ONLY_PRIVILEGES`), so the step that deletes a purged counterparty's dispositions, findings, hits, runs, subject, name-index row and row runs on the admin engine, like the partition steps, with `tenant_id = :tenant` in every statement. Everything else in the job runs in the tenant session.
12. **The declared profile is in the pack's quote currency.** `PATCH /v1/relationships/{id}` refuses a `declared_profile.currency` other than `MXN` (422 `VALIDATION_FAILED`, "declared_profile.currency must be MXN"), and the profile job reads `unit_amounts["MXN/SAME_DAY"]` for every amount, so a declared and an actual amount are always comparable without a conversion. SP-08 can relax this with a conversion when onboarding fills the profile.
13. **Behaviour-profile shares are amount shares.** `cash_share`, `international_share` and `crypto_share` are the share of the month's MXN total (in plus out) carried by `instrument = CASH`, by cross-border rows (`counterparty_country` differs from the pack's `country` or `rail = SWIFT`) and by `instrument = CRYPTO`, as decimal strings with four places. The spec names the fields and not their definition.
14. **The 12 December 2026 is a Saturday.** The exit demo's "prior banking day across the 12 December Banxico holiday" is exercised with a cash operation on Saturday 12 December 2026 whose FIX resolves to Friday 11 December; the weekend, not a holiday row, is what the rule crosses (the BANXICO scope of `policy-packs/_holidays/MX.yaml` lists weekday holidays only). The fixture also puts an operation on Monday 14 December 2026 (whose prior banking day is the 11th too) outside the demo week, so a wrong rule is caught twice.

## Review Focus

1. **A never-seen counterparty never delays a decision, and a confirmed one blocks the next transaction.** The decide path reads the row by `dedup_hash` in one indexed query; a counterparty whose first-sight job has not run answers `UNSCREENED`, which no sync rule treats as clear. After the MLRO confirms `TRUE_MATCH` on a counterparty's finding, the row says `TRUE_MATCH` before the next transaction is processed, and `COUNTERPARTY_TRUE_MATCH` fires on it. Tests: C1 `test_a_disposition_updates_the_counterparty_cache_and_publishes_once`, C5 `test_an_unscreened_counterparty_is_not_treated_as_clear`, C11 `test_sp04_exit` section 11.
2. **Fail modes answer, never 503.** With the budget set to 0 ms, or with the aggregate read raising, `FAIL_OPEN` answers 200 `REVIEW` with `mode = FAIL_OPEN` and `reasons = [{"code": "DECIDE_UNAVAILABLE"}]`, `FAIL_CLOSED` answers 200 `BLOCK`; both write a `txn_decision` row when they can and the response goes out when the row cannot be written. Tests: C5 `test_fail_open_answers_review_on_a_budget_overrun`, `test_fail_closed_answers_block_on_a_database_error`, `test_a_decision_row_that_cannot_be_written_does_not_lose_the_response`.
3. **One limit webhook per relationship, limit and month, whichever path first.** A decision that blocks on the N1 limit and the asynchronous hit on the executed transaction of the same relationship and month publish `monitoring.tier_limit_reached.v1` once between them; the row names which path (`source`). The client's wildcard endpoint receives it and nothing else of `monitoring.rule_hit_*`, `monitoring.reportable_event_*` or `monitoring.decision_issued.v1` (staff-only, reconciliation finding 7). Tests: C5 `test_the_limit_webhook_is_sent_once_across_both_paths`, `test_decision_issued_never_reaches_a_webhook`, C11 section 10.
4. **Retention drops nothing early and honours legal hold.** With `retention.transactions = P10Y`, a month ten years old minus one day is kept; a month past the period whose rows include a party under legal hold is skipped and named in the audit row; the dependency order holds under a month that has hits with `WINDOW` links into a later month. Tests: C7 `test_a_month_inside_the_retention_period_is_kept`, `test_a_month_with_a_held_party_is_skipped_and_named`, `test_a_month_past_the_period_is_retired_in_dependency_order_and_its_partitions_dropped`.
5. **Replay and simulation have no side effects, and the client view hides every rule.** `monitoring replay --dry-run` over a week leaves every monitoring table's row count unchanged and reproduces the stored hit set; the client view of a decision and of a transaction carries no rule code, aggregate, status or hit. Tests: C6 `test_replay_twice_gives_the_same_hits_and_writes_nothing`, C5 `test_the_client_decision_view_hides_rules_and_aggregates`, C11 section 12.

## File structure

```
src/xolotl/modules/screening/counterparty/          (A3, A2, A4 and A10 create __init__.py, schema.py, models.py, resolve.py, schemas.py, views.py; B4 adds CounterpartyNameIndex)
  queries.py        query_for_counterparty, entity_types_for (C1)
  names.py          index_counterparty_name (C1)
  subjects.py       subject_for_counterparty (C1)
  status.py         sync_counterparty_status, mark_screened (C1; imports no engine module)
  first_sight.py    FIRST_SIGHT_KIND, set_first_sight_queue, on_counterparty_created, enqueue_first_sight,
                    unscreened_page (C1; imports no engine module)
  runs.py           screen_counterparty, screen_unscreened, handle_first_sight (C1)
  blocking.py       BlockedCounterparty, reverse_counterparty_candidates (C2)
  walk.py           DORMANT_MONTHS, counterparty_ids_after, active_counterparty_count (C2)
  api.py            counterparties_query, counterparty_row, counterparty_detail (C3; the reveal is A10's reveal_counterparty)
src/xolotl/modules/screening/routes_counterparties.py   GET /counterparties, /{id}, /{id}/reveal (C3)
src/xolotl/modules/screening/engine/enums.py            + SubjectKind.COUNTERPARTY, RunMode.COUNTERPARTY (C1)
src/xolotl/modules/screening/engine/screen.py           subject_query COUNTERPARTY branch, ScreenOptions.entity_type (C1)
src/xolotl/modules/screening/engine/subjects.py         refresh_subject_status calls sync_counterparty_status (C1)
src/xolotl/modules/screening/events.py                  + COUNTERPARTY_STATUS_CHANGED (C1)
src/xolotl/modules/screening/composition.py             + first-sight job handler, set_first_sight_queue (C1)
src/xolotl/modules/screening/rescreen/list_delta.py     + counterparty candidates and runs (C2)
src/xolotl/modules/screening/rescreen/walkers.py        + counterparty pages of the periodic walk (C2)
src/xolotl/modules/screening/api.py, schemas.py         _masked and MaskedSubject.counterparty_id, MatchSummary.counterparty_id (C3)
src/xolotl/modules/screening/counterparty/resolve.py    (A4) + on_counterparty_created call (C1)
src/xolotl/modules/screening/counterparty/schemas.py    (A10) + CounterpartyResponse, CounterpartyDetail (C3)
src/xolotl/migrations/versions/0014_decide_profiles.py  (C4)
src/xolotl/modules/monitoring/decide/
  __init__.py       empty (C4)
  enums.py          FailMode, DecisionOutcome, DecisionMode (C4)
  models.py         RailSetting, TxnDecision (C4)
  rails.py          fail_mode_for, set_fail_mode, rail_settings (C4)
  service.py        decide, DecisionResult, DECIDE_UNAVAILABLE (C5)
  linkage.py        link_decisions (C5)
  views.py          decision_staff_view, decision_client_view (C5)
src/xolotl/modules/monitoring/routes_decide.py          rails routes (C4); decide and decisions routes (C5)
src/xolotl/modules/monitoring/profiles/
  __init__.py       empty (C4)
  models.py         BehaviourProfile (C4)
  metrics.py        metrics_for_period, MetricsResponse (C6)
  job.py            PROFILES_KIND, compute_profiles, handle_profiles, profile_month (C8)
src/xolotl/modules/monitoring/simulation/replay.py      replay_range, ReplayReport (C6)
src/xolotl/modules/monitoring/process/retention.py      RETENTION_KIND, handle_retention, retire_month, purge_counterparties, prune_buckets (C7)
src/xolotl/platform/partitions.py                       (A1) + retire_partition (C7)
src/xolotl/modules/monitoring/{composition,events,errors,schemas}.py   (A5, A4, A4, A10) appended by C4, C5, C6, C7, C8 (enums.py is not touched: this plan's enums live in decide/enums.py)
src/xolotl/modules/monitoring/txn/ingest.py             (A4) + link_decisions call (C5)
src/xolotl/modules/monitoring/rules/evaluate.py         (B7) profile_for reads declared_profile (C8)
src/xolotl/modules/monitoring/routes_hits.py, schemas.py   (B12, A10) + HitDetail.decision (C5)
src/xolotl/modules/party/{schemas,customer,routes_relationships}.py   DeclaredProfile, set_declared_profile, PATCH (C8)
src/xolotl/modules/tenants/service.py                   + MONITORING_RETENTION_SCHEDULE (C7), MONITORING_PROFILES_SCHEDULE (C8)
src/xolotl/platform/config.py, .env.example, README.md  + six settings (C1, C4, C7, C8)
src/xolotl/app.py                                       + routes_counterparties.router (C3), routes_decide.router (C4)
src/xolotl/cli.py                                       + screening screen-counterparties (C1), monitoring replay (C6), monitoring profiles (C8)
src/xolotl/models.py                                    + monitoring decide and profiles models (C4)
tests/isolation/factories.py, tests/platform/test_migration_chain.py, test_errors.py, test_config.py, test_worker.py,
  tests/modules/tenants/test_kernel_events.py           (+) (C1, C4, C6, C7, C8)
tests/modules/screening/counterparty/{__init__,conftest,test_subjects,test_first_sight,test_wallets}.py (C1),
  test_blocking.py, test_rescreen.py (C2), test_routes_counterparties.py (C3)
tests/modules/monitoring/decide/{__init__,conftest,test_migration_0014,test_rails}.py (C4), test_decide.py, test_linkage.py,
  test_decision_views.py, test_tier_limit_webhook.py (C5)
tests/modules/monitoring/simulation/test_replay.py, tests/modules/monitoring/profiles/test_metrics.py (C6)
tests/modules/monitoring/process/test_retention.py (C7)
tests/modules/monitoring/profiles/test_profiles.py, tests/modules/party/test_declared_profile.py (C8)
tests/perf/test_sp04_perf.py (C10)
tests/e2e/test_sp04_exit.py, tests/e2e/fixtures/sp04/{README.md, batch_week.json, batch_accumulation.json, late_crypto.json,
  decide_n1.json} (C11; the unit rows are B7's tests/modules/monitoring/fixtures/unit_values_2026.yaml)
console/src/features/counterparties/{CounterpartiesPage.tsx, CounterpartyPage.tsx, queries.ts, *.test.tsx} (C3)
console/src/features/rails/{RailsPage.tsx, RailsPage.test.tsx, queries.ts} (C9)
console/src/features/parties/{BehaviourProfileCard.tsx, BehaviourProfileCard.test.tsx, DeclaredProfileDialog.tsx, queries.ts (+)} (C9)
console/src/{App.tsx, components/layout/nav.ts, auth/access.ts, i18n/es-MX.json, i18n/en.json, api/types.ts, api/schema.d.ts} (C3, C9)
```

---

### Task C1: Counterparty subjects, queries, first-sight screening, the status cache and wallet matching

Depends on **A4** (the `counterparty` table and `screening/counterparty/` package: `Counterparty`, `CounterpartyInput`, `CounterpartyKind`, `CounterpartyScreeningStatus`, `resolve`, `name_hash_of`) and on **B4** only (`ScreeningSubject.counterparty_id`, `CounterpartyNameIndex`, the kind constraint). Nothing else of 4b. Touches SP-03b's `engine/enums.py`, `engine/screen.py`, `engine/subjects.py`, `events.py`, `composition.py` and 4a's `resolve.py`. Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/counterparty/{queries,names,subjects,status,first_sight}.py`
- Modify: `engine/enums.py` (`SubjectKind.COUNTERPARTY = "COUNTERPARTY"`, `RunMode.COUNTERPARTY = "COUNTERPARTY"`), `engine/screen.py` (`ScreenOptions.entity_type`; `subject_query` branch), `engine/subjects.py` (`refresh_subject_status`), `events.py` (`COUNTERPARTY_STATUS_CHANGED`), `composition.py` (`register_job_handler(FIRST_SIGHT_KIND, handle_first_sight)`, `set_first_sight_queue(queue)`), `counterparty/resolve.py` (A4; `on_counterparty_created(s, ref.id)` on the `created` branch), `src/xolotl/cli.py` (`screening screen-counterparties`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (`monitoring_counterparty_screen_batch`)
- Test: `tests/modules/screening/counterparty/{__init__,conftest}.py`, `test_subjects.py`, `test_first_sight.py`, `test_wallets.py`; `tests/platform/test_config.py` (+); `tests/test_cli.py` (+)

**Consumes:** A2's `Counterparty`, A3's `CounterpartyInput`, `CounterpartyKind`, `CounterpartyScreeningStatus`, A4's `resolve`, `name_hash_of`; B4's `CounterpartyNameIndex`, `ScreeningSubject.counterparty_id`; SP-03's `screen`, `Trigger`, `RunResult`, `refresh_subject_status`, `index_party_names` (pattern), `normalize_name`, `name_keys`, `MATCHER_VERSION`, `NameQuery`, `QueryIdentifier`, `IdType`, `EntityType`, `publish_screening_event`, `register_staff_only`; `PostgresJobQueue`, `register_job_handler`, `get_settings`.

**Produces:**

```python
# queries.py
_KINDS = {CounterpartyKind.PERSON: EntityType.PERSON, CounterpartyKind.ORGANIZATION: EntityType.ORGANIZATION}
def entity_types_for(row: Counterparty) -> tuple[EntityType, ...]
    # a named PERSON or ORGANIZATION: one type; UNKNOWN, or any row without a name: (PERSON, ORGANIZATION) (Deviation 2)
def query_for_counterparty(s: Session, row: Counterparty, *, entity_type: EntityType) -> NameQuery
    # names = (row.name.reveal(session=s),) when a name exists, else (); countries = (row.country,) when set;
    # identifiers: the ad hoc identifier through engine.queries._screened's mapping when its type is RFC, CURP or PASSPORT,
    # plus QueryIdentifier(IdType.DIGITAL_CURRENCY_ADDRESS, normalized=row.wallet.reveal(session=s)) when a wallet exists
    # (case kept, whitespace removed, as the OFAC parser normalizes); decrypts for processing only (D6)

# names.py
def index_counterparty_name(s: Session, row: Counterparty) -> CounterpartyNameIndex | None
    # UPSERT counterparty_name_index by (tenant_id, counterparty_id): norm_name, tokens, phonetic_keys = sorted(name_keys(...)),
    # matcher_version = MATCHER_VERSION; entity type PERSON for a PERSON row, ORGANIZATION otherwise; None (and DELETE) without a name

# subjects.py
def subject_for_counterparty(s: Session, counterparty_id: UUID, *, for_update: bool = False) -> ScreeningSubject
    # INSERT … ON CONFLICT (tenant_id, counterparty_id) DO NOTHING with kind COUNTERPARTY, status CLEAR; then SELECT;
    # sets counterparty.screening_subject_id on first creation

# status.py  (no screening.engine import)
def sync_counterparty_status(s: Session, *, counterparty_id: UUID, subject_id: UUID, status: str) -> bool
    # UPDATE counterparty SET screening_status = :status WHERE id AND screening_status <> :status;
    # publishes COUNTERPARTY_STATUS_CHANGED {"counterparty_id", "subject_id", "status"} (key str(subject_id)) when it updated; returns that
def mark_screened(s: Session, counterparty_id: UUID) -> None      # screened_at = clock.now()

# first_sight.py
FIRST_SIGHT_KIND = "screening.screen_counterparties"
def set_first_sight_queue(queue: PostgresJobQueue | None) -> None
def enqueue_first_sight(s: Session) -> UUID | None
    # queue.enqueue(kind=FIRST_SIGHT_KIND, key=f"screen_counterparties:{clock.now():%Y-%m-%dT%H:%M}", payload={}); None without a queue
def on_counterparty_created(s: Session, counterparty_id: UUID) -> None   # loads the row; index_counterparty_name, then enqueue_first_sight
def unscreened_page(s: Session, *, limit: int) -> list[Counterparty]
    # rows with screening_subject_id IS NULL ORDER BY first_seen_at, id LIMIT limit  (FOR UPDATE SKIP LOCKED)

# runs.py  (the engine half: monitoring reaches first_sight.py through resolve, and monitoring-boundaries
# forbids any monitoring path to screening.engine.models)
def screen_counterparty(s: Session, row: Counterparty, *, mode: RunMode, trigger: Trigger, **options: Unpack[ScreenOptions]) -> list[RunResult]
    # one screen() per entity_types_for(row) with entity_type=…, on subject_for_counterparty(s, row.id); then mark_screened
def screen_unscreened(s: Session, *, trigger: Trigger, limit: int) -> int   # one unscreened_page, screened; returns its length
def handle_first_sight(s: Session, job: Job) -> None
    # page = unscreened_page(limit=get_settings().monitoring_counterparty_screen_batch); screen_counterparty(mode=COUNTERPARTY,
    # trigger=Trigger(JOB, str(job.id))) each; a full page enqueues FIRST_SIGHT_KIND with key f"screen_counterparties:continue:{job.id}"
```

`subject_query(s, subject)` in `engine/screen.py` gains: `if subject.counterparty_id is not None: row = s.get_one(Counterparty, (subject.tenant_id, subject.counterparty_id)); return query_for_counterparty(s, row, entity_type=options.get("entity_type") or entity_types_for(row)[0])`; `screen()` passes its options through. `refresh_subject_status` gains, after the status changes: `if subject.counterparty_id is not None: sync_counterparty_status(s, counterparty_id=subject.counterparty_id, subject_id=subject.id, status=status.value)`. The `COUNTERPARTY` subject stores no `input` (the row is the input; the kind constraint of 4b's migration says so).

CLI (on `screening_app`): `screening screen-counterparties --tenant <slug>` runs `handle_first_sight` synchronously over every page until `unscreened_page` is empty, as `cli:<user>` (`TriggerType.CLI`, ref the actor), and prints `screened <n> counterparties for tenant <slug>`.

- [ ] **Step 1: Write the failing tests.** The conftest seeds a tenant with the ITF pack (`assign_itf`), loads OFAC's fixture and a small UN set through `load_shared`, warms the holder, and resolves counterparties through A4's `resolve()` with `CounterpartyInput` values.
  - `test_subjects.py`:
    - `test_a_counterparty_subject_is_created_once_and_linked_to_its_row` (second call returns the same row; `counterparty.screening_subject_id` set)
    - `test_the_subject_kind_constraint_refuses_a_party_id_or_an_input_on_a_counterparty_subject` (raw SQL insert → `IntegrityError`)
    - `test_the_name_index_row_holds_tokens_and_keys_and_no_name_as_written`
    - `test_a_name_less_counterparty_keeps_no_index_row`
    - `test_a_disposition_updates_the_counterparty_cache_and_publishes_once`: a finding on the counterparty's subject is confirmed `TRUE_MATCH` through `disposition_match`; `counterparty.screening_status == "TRUE_MATCH"`; exactly one `screening.counterparty_status_changed.v1` with `status == "TRUE_MATCH"`; a second `refresh_subject_status` publishes nothing
    - `test_the_status_event_is_staff_only` (a wildcard endpoint gets no delivery row)
  - `test_first_sight.py`:
    - `test_a_new_counterparty_is_screened_by_the_first_sight_job` (`resolve` of a perturbed OFAC name enqueues one job; the runner screens it; the row is `PENDING_REVIEW` with `screened_at` set; the run is `mode == COUNTERPARTY`, `trigger_type == JOB`)
    - `test_two_resolutions_in_the_same_minute_enqueue_one_job`
    - `test_a_full_page_enqueues_a_follow_up` (`monitoring_counterparty_screen_batch = 2`, three new rows)
    - `test_an_unknown_kind_is_screened_as_person_and_organization` (two runs, one subject; an ORGANIZATION OFAC entry near the name produces the finding; status `PENDING_REVIEW`)
    - `test_a_clear_counterparty_is_marked_clear_and_screened_at`
    - `test_the_enricher_reads_unscreened_until_the_job_runs` (`status_by_dedup_hash` answers `UNSCREENED` before the runner and `PENDING_REVIEW` after)
    - `test_a_resolver_without_a_queue_indexes_and_enqueues_nothing`
    - `tests/test_cli.py`: `screening screen-counterparties --tenant acme` prints the line; `--tenant nope` exits 1 with `error: NOT_FOUND: no tenant 'nope'`
  - `test_wallets.py`:
    - `test_a_wallet_only_counterparty_alerts_on_an_exact_ofac_address` (the OFAC fixture's digital-currency address; the hit's band is `ALERT`, its features' rules name `ID_MATCH_FLOOR`, the query name is `""`, and the qualifier detail shows the masked address, never the whole one)
    - `test_a_one_character_difference_does_not_match`
    - `test_a_lower_cased_address_does_not_match` (case-sensitive, whole)
    - `test_a_wallet_counterparty_with_a_name_is_matched_by_both` (one run, the identifier candidate first)
    - `test_no_plaintext_address_is_stored_outside_the_ciphertext` (`leaked(everything_stored(admin_engine), address)` is empty)
  - `tests/platform/test_config.py`: the new field in `.env.example`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/counterparty --no-cov -q` → `ImportError`.
- [ ] **Step 3: Implement** the five modules, the engine edits, the event, the composition lines, the `resolve.py` hook, the CLI command and the setting.
- [ ] **Step 4: Run to verify pass.** The targeted tests, `tests/modules/screening/engine`, `tests/modules/screening/review` (the subject-kind change must not disturb them), then `make lint types vulture importlint` and `make ci`.
- [ ] **Step 5: Docs.** Module docstrings: `first_sight.py` (the one-minute key, one page per job, Deviation 5), `queries.py` (Deviations 1 and 2). README configuration row. A line in the README "Screening" section: counterparties are screened on first sight and shown in the queue.
- [ ] **Step 6: Commit** `feat(screening): counterparty subjects, first-sight screening, the status cache and wallet matching`.

---

### Task C2: Reverse screening of counterparties and the periodic walk

Depends on C1 and on SP-03c's `rescreen/` (on `main`). Touches `rescreen/list_delta.py` and `rescreen/walkers.py`. Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/counterparty/{blocking,walk}.py`
- Modify: `rescreen/blocking.py` (the `table` parameter of `blocking_query` and `stop_keys`, default `PartyNameIndex`), `rescreen/list_delta.py` (`_blocked_by_counterparty`; runs after the party runs in `handle_list_delta`), `rescreen/walkers.py` (counterparty pages of `handle_periodic_rescreen`; `active_party_count` plus `active_counterparty_count` in the batch `total`)
- Test: `tests/modules/screening/counterparty/test_blocking.py`, `test_rescreen.py`

**Consumes:** `reverse_candidates`'s building blocks `name_plans`, `stop_keys`, `blocking_query`, `BLOCKING_CAP`, `TRIGRAM_THRESHOLD` (as `rescreen/blocking.py` defines them; `blocking_query` and `stop_keys` take the index model as a parameter after this task: `table: type[PartyNameIndex] | type[CounterpartyNameIndex]`, default `PartyNameIndex`, so SP-03c's callers are unchanged); A4's `wallet_hash_of`, `identifier_hash_of`; `CounterpartyNameIndex` (B4), `Counterparty` (A2); C1's `counterparty.runs.screen_counterparty`, `subject_for_counterparty`; `advance_batch`, `complete_batch`, `page_already_done`, `WALKER_START`.

**Produces:**

```python
# blocking.py
@dataclass(frozen=True, slots=True)
class BlockedCounterparty: entry_key: str; counterparty_ids: frozenset[UUID]; overflow: bool
def reverse_counterparty_candidates(s: Session, entries: Sequence[ListEntry], *, idf: IdfTable) -> list[BlockedCounterparty]
    # names: blocking_query(plan, folded, limit=BLOCKING_CAP + 1, stop=stop_keys(s, plans, table=CounterpartyNameIndex), table=CounterpartyNameIndex)
    # identifiers: Counterparty.identifier_hash == identifier_hash_of(s, id_type, normalized) for RFC, CURP, PASSPORT;
    #              Counterparty.wallet_hash == wallet_hash_of(s, normalized) for DIGITAL_CURRENCY_ADDRESS
    # overflow as reverse_candidates: name candidates dropped, identifier and wallet matches kept

# walk.py
DORMANT_MONTHS = 12
def counterparty_ids_after(s: Session, *, after: UUID | None, limit: int, today: date) -> list[UUID]
    # ids > after in id order with last_seen_at >= today - 12 months (spec §11.3, Q8)
def active_counterparty_count(s: Session, *, today: date) -> int
```

`handle_list_delta` (SP-03c): after the party loop, `by_counterparty, overflow_cp = _blocked_by_counterparty(s, rows, version)` and, per counterparty in id order, `screen_counterparty(s, row, mode=RunMode.LIST_DELTA, trigger=Trigger(JOB, str(job.id)), only_entries=by_counterparty[id], require_versions={list_code: seq}, batch_id=batch.id)`; `runs` and `new_matches` add the counterparty runs; `done` still counts entries; overflow is the sum. `handle_periodic_rescreen`: a party page that comes up short no longer completes the batch; it enqueues the first counterparty page (key `f"{PERIODIC_RESCREEN_KIND}:{batch.id}:cp:{WALKER_START}"`, payload `{"batch_id", "after_counterparty_id": None, ...carried}`); a counterparty page walks `counterparty_ids_after(limit=screening_rescreen_batch)`, screens each with `mode=RunMode.PERIODIC`, `require_versions` carried, `batch_id`, advances the cursor to `f"cp:{last id}"` and completes the batch after a short page. `page_already_done` compares `cp:` cursors as UUIDs after the party cursors (a `cp:` cursor is past every party cursor). The delta job pages and the private-list and re-index walks are otherwise unchanged.

- [ ] **Step 1: Write the failing tests.**
  - `test_blocking.py`:
    - `test_a_perturbed_entry_name_blocks_the_counterparty` (dropped second surname, accent loss)
    - `test_a_wallet_on_the_entry_finds_the_counterparty_without_a_name`
    - `test_an_rfc_on_the_entry_finds_the_counterparty`
    - `test_a_common_name_is_not_blocked` (a hard negative from the corpus)
    - `test_stop_keys_apply_to_the_counterparty_index` (`STOP_KEY_MIN_HOLDERS` patched to 3; a key held by four of five rows is dropped)
    - `test_more_than_the_cap_marks_the_entry_overflow_and_keeps_the_wallet_match` (`BLOCKING_CAP` patched)
    - `test_blocking_never_crosses_tenants`
    - `test_party_blocking_is_unchanged` (SP-03c's `test_blocking.py` still passes; this test asserts `blocking_query(plan, folded, limit=1)` without `table` targets `party_name_index`)
  - `test_rescreen.py`:
    - `test_a_delta_rescreens_only_the_counterparties_near_its_added_and_changed_entries` (U, A, C, R counterparties as SP-03c's F3 fixture; findings for A and C only; `restricted_to_delta`; parties of the same tenant still get theirs)
    - `test_a_delta_with_no_counterparties_behaves_as_before`
    - `test_the_periodic_walk_visits_parties_then_counterparties` (the batch's `total` is parties plus active counterparties; the cursor sequence passes from party ids to `cp:` ids; every active counterparty has one `PERIODIC` run; `COMPLETED`)
    - `test_a_dormant_counterparty_is_skipped_by_the_periodic_walk` (`last_seen_at` 13 months back; no run; a delta still screens it)
    - `test_a_redelivered_counterparty_page_changes_nothing`
    - `test_a_counterparty_walk_resumes_from_its_cursor_after_a_crash` (`screen_counterparty` patched to raise on the third row; retry; one run each)
    - `test_the_manual_rescreen_covers_counterparties_with_the_cli_trigger`
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then `tests/modules/screening/rescreen` whole, the gates and `make ci`.
- [ ] **Step 5: Docs.** Extend the `walkers.py` and `list_delta.py` module docstrings with the counterparty pages and Q8's dormancy rule; `blocking.py`'s with the wallet lookup.
- [ ] **Step 6: Commit** `feat(screening): re-screen counterparties on list deltas and in the periodic walk`.

---

### Task C3: The counterparties API, the queue's counterparty findings and the Contrapartes page

Depends on **C1** (subjects, status), **A10** (`counterparty/schemas.py` and `counterparty/views.py`: `CounterpartySummary`, `RevealedCounterparty`, `counterparty_summary`, `reveal_counterparty`) and on SP-03b's `api.py`, `schemas.py`, `routes_screening.py` and console `features/screening/`. C2 is not needed. Touches `console/`. Console: **yes**. The page is third on the spec §23 cut list; its steps come last so it can be dropped without touching the API.

**Files:**
- Create: `src/xolotl/modules/screening/counterparty/api.py`, `src/xolotl/modules/screening/routes_counterparties.py`
- Modify: `screening/api.py` (`_subject_query`, `_masked` for `COUNTERPARTY`), `screening/schemas.py` (`MaskedSubject.counterparty_id: UUID | None`, `MatchSummary.counterparty_id: UUID | None`), `screening/counterparty/schemas.py` (A10; + `CounterpartyResponse`, `CounterpartyDetail`), `src/xolotl/app.py` (`routes_counterparties.router`)
- Console:
  - Create: `console/src/features/counterparties/{queries.ts, CounterpartiesPage.tsx, CounterpartiesPage.test.tsx, CounterpartyPage.tsx, CounterpartyPage.test.tsx, StatusBadge.tsx}`
  - Modify: `features/screening/MatchPage.tsx` (a "Abrir contraparte" link beside "Abrir persona" when `subject.counterparty_id` is set), `features/screening/MatchesPage.tsx` (a `Contraparte` chip on counterparty rows), `App.tsx` (routes `/counterparties`, `/counterparties/:id`), `components/layout/nav.ts` (flat entry `nav.counterparties`, icon `Handshake`, `roles: ACCESS.screening`, after `nav.screeningCheck`), `auth/access.ts` (no new key: `ACCESS.screening` reads, `ACCESS.screeningCheck` reveals), `i18n/{es-MX,en}.json`, `api/types.ts`, `api/schema.d.ts` (regenerated)
- Test: `tests/modules/screening/counterparty/test_routes_counterparties.py`, `tests/modules/screening/test_routes_screening.py` (+)

Note from C1: `screening/api.py` already has a minimal `COUNTERPARTY` branch (approved for C1 under the 2026-10-19 blanket approval): `_subject_query` returns the engine's `subject_query` for a counterparty subject, `_masked` shows the initials of its name, or `WALLET` for a counterparty known only by its wallet, with masked identifiers, and `run_view` answers 404 to an API client for a counterparty's run. The queue, the match detail and the reveal already answer 200 on a counterparty finding (`tests/modules/screening/counterparty/test_routes.py`). C3 extends that branch (`counterparty_id` on `MaskedSubject` and `MatchSummary`) rather than adding it.

**Produces:**

```python
class CounterpartyResponse(CounterpartySummary):   # A10's masked summary plus the list-row fields below; appended to screening/counterparty/schemas.py
    initials: str; account_masked: str | None   # "****1234"; last four of the normalized account
    wallet_masked: str | None; identifier_masked: str | None; identifier_type: str | None
    screened_at: datetime | None; subject_id: UUID | None; first_seen_at: datetime; last_seen_at: datetime   # kind, institution, country, wallet_network, screening_status and txn_count come from the base
class CounterpartyDetail(CounterpartyResponse):
    findings: list[MatchSummary]                 # the subject's findings, newest first (links into the queue)
    runs: list[RunResponse]                      # last 10
# the reveal is A10's RevealedCounterparty (name, account, wallet_address, wallet_network, identifier_type, identifier) through A10's reveal_counterparty:
# one VIEW_PII row per non-null field (entity counterparty, field name); nothing is redefined here

def counterparties_query(s, *, status: CounterpartyScreeningStatus | None, kind: CounterpartyKind | None, seen_after: datetime | None) -> Select[Counterparty]
def counterparty_row(s, row) -> CounterpartyResponse         # A10's counterparty_summary plus initials (a processing decryption, D6), the masked tails, screened_at, subject_id and the seen timestamps
def counterparty_detail(s, counterparty_id) -> CounterpartyDetail

# routes_counterparties.py  (prefix "/counterparties", staff only: require_access() without a scope)
GET ""            list_counterparties  -> Page[CounterpartyResponse]; filters status, kind, seen_after; newest last_seen_at first
GET "/{id}"       get_counterparty     -> CounterpartyDetail; 404
GET "/{id}/reveal" reveal_counterparty_endpoint -> RevealedCounterparty (A10's `reveal_counterparty`); ACCESS as the match reveal (any staff role but AUDITOR_RO is read-only and may reveal: same rule as parties)
```

`initials` for a counterparty is the initials of the decrypted name: the row is decrypted for processing to compute them, as `_masked` does for a party's subject today (D6: masking is processing, not a reveal). `_masked` for a `COUNTERPARTY` subject sets `counterparty_id`, `party_id = None`, `entity_type` of the first run's query and the wallet among `identifiers` as `DIGITAL_CURRENCY_ADDRESS ****a1b2`.

Console: `CounterpartiesPage` (`/counterparties`, title "Contrapartes") is a table (masked name initials, kind, country, institution, masked account or wallet, status badge, first and last seen, transactions) with a status filter (tabs `Pendientes de revisión`, `Coincidencia confirmada`, `Sin alerta`, `Sin revisar`) and cursor paging; `CounterpartyPage` shows the masked card, "Revelar" (a `useMutation` with `gcTime: 0` that shows the `VIEW_PII` notice), the findings list linking to `/screening/matches/{id}` and the runs. Strings: `counterparties.title`, `counterparties.description`, `counterparties.table.*`, `counterparties.status.{UNSCREENED,CLEAR,PENDING_REVIEW,TRUE_MATCH}` ("Sin revisar", "Sin alerta", "Pendiente de revisión", "Coincidencia confirmada"), `counterparties.reveal.*`, `screening.match.openCounterparty` ("Abrir contraparte"), `nav.counterparties` ("Contrapartes").

- [ ] **Step 1: Write the failing tests.**
  - `test_routes_counterparties.py`:
    - `test_staff_list_counterparties_masked_with_status_and_counts` (no name, account or address in the JSON; `leaked(response.text, name, address)` empty)
    - `test_the_list_filters_by_status_and_paginates`
    - `test_the_detail_lists_the_findings_and_runs`
    - `test_the_reveal_records_one_view_pii_per_field` (name, account, wallet, identifier each one audit row with `field`)
    - `test_api_keys_cannot_read_counterparties` (403 with any scope)
    - `test_another_tenant_sees_nothing` (404 on A's id under B)
  - `test_routes_screening.py` (+): `test_the_queue_shows_a_counterparty_finding_with_its_counterparty_id` (`party_id` null, `counterparty_id` set, subject initials and the masked wallet)
  - Console: `CounterpartiesPage.test.tsx` (renders rows and the status tabs from a fake page; the empty state), `CounterpartyPage.test.tsx` (masked card; the reveal shows the fields and the notice; the findings link).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the backend, regenerate `schema.d.ts`, then the console.
- [ ] **Step 4: Run to verify pass.** Targeted tests, the gates, `make ci`, then the console gates.
- [ ] **Step 5: Docs.** README API table rows for the three routes; the module docstring of `counterparty/api.py` (what is masked, what is revealed, and that `initials` is processing, not a reveal).
- [ ] **Step 6: Commit** `feat(screening): counterparties API and the Contrapartes page`. The console part may be a second commit, `feat(console): Contrapartes`.

---

### Task C4: Migration 0014, the decide and profile models, rail settings and the rails API

Depends on **A10** (the `monitoring/` package complete: `routes_transactions.router`, before which `routes_decide.router` is included; `monitoring/schemas.py`, `monitoring/errors.py`, `monitoring_app`, `register_monitoring`; A1's `xolotl.platform.partitions`) and on **B4** (the chain: `down_revision = "0013_rules_captures"`; the migrations merge A2 → B4 → C4); on SP-01's `staff_user`. Touches `src/xolotl/models.py`, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_errors.py`. Console: no.

**Files:**
- Create: `src/xolotl/migrations/versions/0014_decide_profiles.py`, `src/xolotl/modules/monitoring/decide/{__init__,enums,models,rails}.py`, `src/xolotl/modules/monitoring/profiles/{__init__,models}.py`, `src/xolotl/modules/monitoring/routes_decide.py`
- Modify: `src/xolotl/models.py`, `monitoring/errors.py` (`RailUnknownError`), `monitoring/schemas.py` (`RailSettingResponse`, `RailSettingRequest`), `src/xolotl/app.py` (`routes_decide.router` included before `routes_transactions.router`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (`monitoring_decide_budget_ms`, `monitoring_decide_fail_mode`), `tests/isolation/factories.py` (+3), `tests/platform/test_migration_chain.py`, `tests/platform/test_errors.py`
- Test: `tests/modules/monitoring/decide/{__init__,conftest}.py`, `test_migration_0014.py`, `test_rails.py`; `tests/platform/test_config.py` (+)

**Produces:**

```python
# enums.py
class FailMode(StrEnum): FAIL_OPEN = "FAIL_OPEN"; FAIL_CLOSED = "FAIL_CLOSED"
class DecisionOutcome(StrEnum): ALLOW = "ALLOW"; REVIEW = "REVIEW"; BLOCK = "BLOCK"
class DecisionMode(StrEnum): EVALUATED = "EVALUATED"; FAIL_OPEN = "FAIL_OPEN"; FAIL_CLOSED = "FAIL_CLOSED"

# models.py: RailSetting (rail_setting), TxnDecision (txn_decision: columns exactly as spec §5.2's block; reasons JSONB; ruleset_hash LargeBinary);
#   register_partitioned_table(PartitionedTable("txn_decision", "decided_at", None)) at import (A1's registry), so A9's daily job and `xolotl db upgrade` keep its partitions ahead
# profiles/models.py: BehaviourProfile (behaviour_profile); CustomerRelationship gains declared_profile: Mapped[dict[str, JsonValue] | None] (JSONB) in party/models.py

# rails.py
def fail_mode_for(s: Session, rail: Rail) -> FailMode            # rail_setting row, else get_settings().monitoring_decide_fail_mode
def set_fail_mode(s: Session, rail: Rail, mode: FailMode) -> RailSetting   # upsert by (tenant_id, rail); audit monitoring.rail_setting_changed
def rail_settings(s: Session) -> list[RailSettingResponse]       # one row per Rail member: the stored mode or the default, with `explicit: bool`

# routes_decide.py (router = v1_router(prefix="", tags=["decide"]))
GET  /monitoring/rails          list_rail_settings  (staff, any role)  -> list[RailSettingResponse]  {rail, fail_mode, explicit, changed_by, updated_at}
PUT  /monitoring/rails/{rail}   put_rail_setting    (staff ADMIN; Idempotency-Key)  {fail_mode} -> RailSettingResponse; 404 RAIL_UNKNOWN for a string that is no Rail member
```

Migration 0014: `rail_setting`, `txn_decision` (`PARTITION BY RANGE (decided_at)`, `txn_decision_default`, `ensure_partitions(conn, table="txn_decision", months_ahead=3)`, every partition hardened), `ux_txn_decision_external`, `ix_txn_decision_relationship`, `ix_txn_decision_id` (Deviation 7), `fk_transaction_decision` on `transaction`, `behaviour_profile`, `customer_relationship.declared_profile jsonb`; `enable_rls` on the three tables; downgrade in reverse. Isolation factories `rail_setting`, `txn_decision`, `behaviour_profile` registered in `register_all`.

- [ ] **Step 1: Write the failing tests.**
  - `test_migration_0014.py`: `test_the_chain_ends_at_0014` (`CHAIN[-1] == "0014_decide_profiles"`), `test_txn_decision_has_a_default_and_three_months_of_partitions_with_forced_rls`, `test_the_app_role_may_write_the_three_tables`, `test_a_transaction_may_point_at_its_decision_and_not_at_another_tenants` (composite FK; cross-tenant insert refused), `test_downgrade_removes_everything_0014_added`.
  - `test_rails.py`: `test_a_rail_without_a_row_answers_the_default_fail_mode`, `test_an_admin_sets_fail_closed_and_it_is_audited` (one `monitoring.rail_setting_changed` with `{"rail": "SPEI", "fail_mode": "FAIL_CLOSED"}`), `test_a_second_put_updates_the_same_row`, `test_an_unknown_rail_is_404_rail_unknown`, `test_only_admin_may_put_and_every_staff_role_may_read` (MLRO PUT is 403 `ROLE_FORBIDDEN`; AUDITOR_RO GET 200), `test_api_keys_cannot_read_rails`, `test_another_tenant_sees_the_defaults_only`.
  - `tests/platform/test_config.py`: the two fields; `XOLOTL_MONITORING_DECIDE_FAIL_MODE=sideways` is refused.
  - `tests/platform/test_errors.py`: `(RailUnknownError, 404, "RAIL_UNKNOWN")`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** The migration first, then the models, the schema-sync test, the factories, then the service and routes.
- [ ] **Step 4: Run to verify pass**, the isolation harness (`uv run pytest tests/isolation --no-cov -q`), the gates and `make ci`.
- [ ] **Step 5: Docs.** The migration docstring (what and why; the index of Deviation 7). README configuration rows and the API table rows for the rails routes.
- [ ] **Step 6: Commit** `feat(monitoring): migration 0014, decision and profile tables, rail settings`.

---

### Task C5: `POST /v1/transactions/decide`, fail modes, `txn_decision`, linkage, the decide-path tier limit and its webhook

Depends on **C4**, on 4a (A3 `TransactionInput`, `parse_item`; A4 `relationship_by_external_ref`, `find_account`, `ingest_batch`, `dedup_hash_of`, `status_by_dedup_hash`, `publish_monitoring_event`; A6 `enrich_input`, `snapshot_for`, `policy_date`; A7 `PostgresAggregateStore.buckets`, `MemoryAggregateStore.seed`, `window_bounds`, `load_declarations`) and on 4b (**B7**: `active_rules`, `RuleSet.sync_rules`, `RuleSet.ruleset_hash()`, `reader_for`, `profile_for`, `notice_tier_limit`, `NoticeSource.DECIDE`, `LIMIT_CODE_MONTHLY_OPS`; B2 `evaluate`, `EvalContext`, `Firing`, `HitOutcome`, `INCOMPLETE`; B1 `RuleKind.TIER_LIMIT`, `Severity.CRITICAL`, `load_jurisdictions`; **B12**: `HitDetail` and `routes_hits.py`, which this task extends with `decision`). Touches A4's `txn/ingest.py` and B12's hit detail. Console: no.

Note from A7: build the memory store's `incomplete_ids` as `{d.agg_id() for d in declarations} - backfilled_ids(s)` (`aggregates/postgres.py`, `backfilled_ids(session) -> frozenset[str]`: the `agg_id`s whose `backfilled_through` is set), so a declaration with no `agg_declaration` row yet reads incomplete too.

Note from A4: the counterparty key is `dedup_hash_of(session, counterparty_of(item))`, with `counterparty_of` from `xolotl.modules.monitoring.txn.schema` (the crypto address stands in for a wallet the counterparty does not name, which changes the hash). `dedup_hash_of` raises `CounterpartyUnindexableError` for an input with nothing to index, which callers must catch (the decide path treats it as no counterparty).

**Files:**
- Create: `src/xolotl/modules/monitoring/decide/{service,linkage,views}.py`
- Modify: `routes_decide.py` (two routes), `monitoring/schemas.py` (`DecideResponse`, `DecisionReason`, `DecisionClientResponse`, `DecisionStaffResponse`, `DecisionSummary`; `HitDetail.decision`), `monitoring/events.py` (`DECISION_ISSUED`, appended to A4's `STAFF_ONLY_EVENT_TYPES`), `monitoring/txn/ingest.py` (A4; `link_decisions` between steps 2 and 4), `monitoring/routes_hits.py` (B12; fills `HitDetail.decision`)
- Test: `tests/modules/monitoring/decide/test_decide.py`, `test_linkage.py`, `test_decision_views.py`, `test_tier_limit_webhook.py`

**Produces:**

```python
# service.py
DECIDE_UNAVAILABLE = "DECIDE_UNAVAILABLE"; RISK_REVIEW = "RISK_REVIEW"; TIER_LIMIT = "TIER_LIMIT"

@dataclass(frozen=True, slots=True)
class DecisionResult: decision: DecisionOutcome; decision_id: UUID; mode: DecisionMode; reasons: tuple[dict[str, JsonValue], ...]; latency_ms: int

def decide(s: Session, item: TransactionInput, *, actor: str) -> DecisionResult
    # the six steps of spec §12.1 under a monotonic budget (Deviation 8):
    # 1 relationship_by_external_ref(s, [item.customer_ref]) (missing or not ACTIVE -> 422 CUSTOMER_REF_UNKNOWN on customer_ref); find_account(s, relationship.id, item.account_ref)
    #   (None when unseen; no upsert on a decision); fail_mode_for(item.rail)
    # 2 now = clock.now(); snapshot = snapshot_for(s, on=policy_date(now), product=relationship.product, cache={}); ruleset = active_rules(s, on=policy_date(now), product=relationship.product);
    #   sync = ruleset.sync_rules; status_by_dedup_hash(s, dedup_hash_of(s, item.counterparty)) when a counterparty is given (no upsert), else None
    # 3 enriched = enrich_input(s, item, relationship=relationship, account_id=account.id if account else None (A3's account_id is UUID | None; ACCOUNT-keyed aggregates then read empty), units=UnitService(s), holidays=..., snapshot=snapshot, now=now)
    #   (A6's ParkTransactionError, which wraps UnitValueMissingError and PolicyNotAssignedError, -> fail mode; never a 500)
    # 4 store = MemoryAggregateStore(); for each declaration d of the sync rules: key = AggregateKey(d.key_kind, enriched.key_for(d.key_kind)); start, end = the window ending
    #   now (window_bounds for DURATION, period_bucket_start for MONTH/QUARTER, the open accumulator for ACCUM); store.seed(key, d, PostgresAggregateStore(s).buckets(key, d, start=start, end=end));
    #   with store = MemoryAggregateStore(incomplete_ids={d.agg_id for d in sync declarations with no agg_declaration row or backfilled_through NULL (load_declarations)}) (A7); then store.add(enriched, declarations);
    #   reader = reader_for(store, enriched, by name) yields INCOMPLETE for those ids exactly as processing does (spec §8.4)
    # 5 profile = profile_for(s, enriched); firings = [(r, evaluate(r.definition, EvalContext(enriched, reader, profile, snapshot.policy, ruleset.lists, now))) for r in sync];
    #   decision per §12.1 step 5
    # 6 INSERT txn_decision (mode EVALUATED, reasons from the firings: {"code": "TIER_LIMIT"|"RISK_REVIEW", "rule_version_id", "severity", "detail": {...aggregates}},
    #   ruleset_hash = ruleset.ruleset_hash(), policy_snapshot_id, latency_ms, db_ms, actor); publish DECISION_ISSUED;
    #   per TIER_LIMIT firing: notice_tier_limit(s, relationship_id=..., account_id=..., tier=..., limit_code=LIMIT_CODE_MONTHLY_OPS, period_key=..., outcome=..., source=NoticeSource.DECIDE) (publishes once)
    # over budget or on error at any step: write the row with mode FAIL_OPEN|FAIL_CLOSED, reasons [{"code": "DECIDE_UNAVAILABLE"}], decision REVIEW|BLOCK
    #   inside begin_nested(); a failing write is logged "monitoring.decision_unrecorded" and the result is still returned (Deviation 9)

# linkage.py
def link_decisions(s: Session, rows: Sequence[tuple[UUID, datetime, str]]) -> int
    # (transaction_id, occurred_at, external_id): the latest txn_decision of the external_id with decided_at >= now - 7 days and
    # transaction_id IS NULL gets (transaction_id, occurred_at); the transaction gets (decision_id, decided_at); returns how many linked

# views.py
def decision_staff_view(s, decision_id) -> DecisionStaffResponse    # the row, reasons in full, the rule codes and versions, the linked transaction summary
def decision_client_view(s, decision_id) -> DecisionClientResponse  # {decision_id, external_id, decision, mode, decided_at}

# routes_decide.py
POST /transactions/decide   decide_transaction   require_access(scope="transactions:decide", write=True); Idempotency-Key
                            TransactionInput -> 200 DecideResponse {decision, decision_id, reasons: [{code, tier?, limit_code?}], latency_ms}
                            422 field errors (the §4 validation through A3's parse_item), never 503
GET  /monitoring/decisions/{id}  get_decision   staff -> DecisionStaffResponse; API key transactions:read -> DecisionClientResponse; 404
```

The client's `reasons` carry `code` only, plus `tier` and `limit_code` on `TIER_LIMIT`; `RISK_REVIEW` for any other firing, deduplicated to one entry per code. `ingest_batch` (A4) calls `link_decisions(session, rows)` after the row inserts and before the processing enqueue, with the `(id, occurred_at, external_id)` of the rows it created (duplicates excluded; 4a Deviation 4). C5 adds `decision: DecisionSummary | None` (`{decision_id, decision, mode, decided_at}`) to B12's `HitDetail` and fills it in `routes_hits.py` when the trigger transaction carries `decision_id` (the "decided BLOCK at 15:20:11" of spec §12.1).

- [ ] **Step 1: Write the failing tests.** The conftest builds a tenant with both packs assigned by product (A2's `both_packs_tenant`), the ITF tiers, unit values for the test month, a relationship with `account_tier = "N1"` and another `"N2"`, the `TIER_N1_MONTHLY_OPS` and `TIER_N2_MONTHLY_OPS` versions activated with `rules activate` (CLI) and a `COUNTERPARTY_TRUE_MATCH` version, and 4a's processing of a few transactions so `agg_bucket` holds month-to-date sums.
  - `test_decide.py`:
    - `test_allow_when_no_sync_rule_fires`
    - `test_block_on_the_n1_monthly_limit_with_the_tier_and_limit_code` (month-to-date 700 UDI plus a 100 UDI candidate; `reasons == [{"code": "TIER_LIMIT", "tier": "N1", "limit_code": "monthly_ops"}]`; `mode == "EVALUATED"`; the row's `ruleset_hash` is the sha256 of the sorted definition hashes)
    - `test_review_on_the_n2_limit`
    - `test_the_candidate_counts_in_its_own_aggregates` (exactly at the limit allows; one unit of the limit's currency over blocks: B2 fires on `month_in.value > limit.amount`, spec §12.2 "must not exceed")
    - `test_block_on_a_critical_sync_rule_and_the_client_sees_risk_review_only` (`COUNTERPARTY_TRUE_MATCH` with a `TRUE_MATCH` counterparty, marked `sync`; the client gets `RISK_REVIEW`, no rule code)
    - `test_an_unscreened_counterparty_is_not_treated_as_clear` (the sync rule reads `UNSCREENED`; no firing, no exception; the staff view records the status read)
    - `test_fail_open_answers_review_on_a_budget_overrun` (`monitoring_decide_budget_ms = 0`; 200, `REVIEW`, `mode FAIL_OPEN`, `reasons [{"code": "DECIDE_UNAVAILABLE"}]`; a row with `mode = FAIL_OPEN` exists)
    - `test_fail_closed_answers_block_on_a_database_error` (`PostgresAggregateStore.read` patched to raise `OperationalError`; rail `SPEI` set `FAIL_CLOSED`)
    - `test_a_missing_unit_value_is_a_budget_failure_not_a_stale_value` (A6's `enrich_input` raises `ParkTransactionError(UNIT_VALUE_MISSING, …)`; the rail's fail mode answers 200, never a 500)
    - `test_a_decision_row_that_cannot_be_written_does_not_lose_the_response` (the insert patched to raise; `caplog` has `monitoring.decision_unrecorded`; 200)
    - `test_the_decide_route_needs_the_decide_scope_and_an_idempotency_key` (`transactions:write` alone is 403 `SCOPE_MISSING`; a replayed key returns the same `decision_id`)
    - `test_a_naive_occurred_at_is_a_422_field_error` (4a's validator through this route)
    - `test_decision_issued_never_reaches_a_webhook` (a wildcard endpoint has no delivery row; an endpoint naming `monitoring.decision_issued.v1` is refused with 422 `EVENT_TYPE_NOT_DELIVERABLE`; the event is on the outbox for in-process consumers)
  - `test_linkage.py`: `test_a_later_transaction_links_to_its_decision_by_external_id` (both sides set; the processed transaction's staff view shows the decision), `test_a_decision_older_than_seven_days_is_not_linked`, `test_a_duplicate_resend_links_nothing_twice`, `test_the_hit_detail_shows_the_decision`.
  - `test_decision_views.py`: `test_the_client_decision_view_hides_rules_and_aggregates` (a JSON-schema walk of `DecisionClientResponse` finds no `rule`, `aggregate`, `severity`, `screening` key; the response of an API key equals it), `test_the_staff_view_shows_rules_aggregates_and_the_linked_transaction`, `test_another_tenant_gets_404`.
  - `test_tier_limit_webhook.py`: `test_the_limit_webhook_is_sent_once_across_both_paths` (decide blocks; then the same transaction is ingested anyway and processed by 4b's `TIER_N1_MONTHLY_OPS`; one `tier_limit_notice` row with `source == "DECIDE"`, one `monitoring.tier_limit_reached.v1` outbox row, one delivery to the wildcard endpoint), `test_the_webhook_payload_has_ids_and_codes_only` (`relationship_id`, `account_id`, `tier`, `limit_code`, `period_key`, `outcome`; no amount), `test_a_new_month_sends_again`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** `service.py` first with its unit tests, then the routes, the linkage, the views and the hit-detail line.
- [ ] **Step 4: Run to verify pass**, then `tests/modules/monitoring` whole, the gates and `make ci`.
- [ ] **Step 5: Docs.** `service.py` module docstring (the six steps, the budget, the fail modes, what the client sees). README API table rows; the developer-docs sentence for the limit webhook ("límite de nivel de cuenta"). Record the local decide latency of the tests in the PR body.
- [ ] **Step 6: Commit** `feat(monitoring): synchronous decisions with fail modes, decision linkage and the tier-limit webhook`.

---

### Task C6: `monitoring replay --dry-run` and the effectiveness metrics endpoint

Depends on 4b (**B8** `replay_rule_set`, `SimulatedHit`; B6 `active_rules`, `assignments_on`; B4 `RuleHit`, `RuleHitTransaction`, `ReportableEvent`, `RuleTemplate`; **B11** the `CapturePeriod` rows with `reconciled_count`), on 4a (A5 `monitoring_app`, A10 `routes_monitoring.router`) and on **C4** (`TxnDecision` for `OPS-502`). Runs in parallel with C5. Console: no. The metrics endpoint is fifth on the spec §23 cut list; its steps come after the replay's.

**Files:**
- Create: `src/xolotl/modules/monitoring/simulation/replay.py`, `src/xolotl/modules/monitoring/profiles/metrics.py`
- Modify: `src/xolotl/cli.py` (`monitoring replay`), `routes_monitoring.py` (`GET /metrics`), `monitoring/schemas.py` (`MetricsResponse` and its parts), `monitoring/errors.py` (`PeriodInvalidError`), `tests/platform/test_errors.py`
- Test: `tests/modules/monitoring/simulation/test_replay.py`, `tests/modules/monitoring/profiles/test_metrics.py`, `tests/test_cli.py` (+)

**Produces:**

```python
# replay.py
@dataclass(frozen=True, slots=True)
class RuleReplay: rule_code: str; rule_version_id: UUID; simulated: int; stored: int; missing: int; extra: int
@dataclass(frozen=True, slots=True)
class ReplayReport: range_start: date; range_end: date; rules: tuple[RuleReplay, ...]; identical: bool
def replay_range(s: Session, *, range_start: date, range_end: date) -> ReplayReport
    # for product in {None} | the products of assignments_on(s, on=range_end): ruleset = active_rules(s, on=range_end, product=product);
    # simulated |= {(h.rule_version_id, h.dedup_key) for h in replay_rule_set(s, ruleset, range_start=..., range_end=..., page=settings.monitoring_agg_backfill_batch)} (B8; rule versions deduplicated across products);
    # stored = rule_hit rows of those versions with first_occurred_at in the range, keyed (rule_version_id, dedup_key);
    # missing = stored - simulated, extra = simulated - stored; identical when both are empty for every rule. Writes nothing.

# metrics.py
class RuleMetrics(BaseModel): rule_version_id: UUID; rule_code: str; version: int; typology: list[str]; hits: int; parties_hit: int
                              hits_by_severity: dict[Severity, int]; median_seconds_to_hit: Decimal | None; p95_seconds_to_hit: Decimal | None
class CaptureMetrics(BaseModel): report_code: str; period_key: str; captured: int; reconciled_count: int | None; discrepancies: int
class DecisionMetrics(BaseModel): count: int; p50_ms: int | None; p95_ms: int | None; p99_ms: int | None; fail_open: int; fail_closed: int   # OPS-502
class MetricsResponse(BaseModel): period: str; hits: int; parties_hit: int; hits_by_severity: dict[Severity, int]
                                  typology_coverage: dict[str, int]      # active templates per typology tag
                                  rules: list[RuleMetrics]; captures: list[CaptureMetrics]; decisions: DecisionMetrics
                                  alert_to_case_rate: None = None; case_to_report_rate: None = None   # SP-06 fills them
def metrics_for_period(s: Session, *, period: str, rule_version_id: UUID | None) -> MetricsResponse
    # period "YYYY-MM" in America/Mexico_City else PeriodInvalidError; month bounds from the calendar; one query per table
    # (ix_rule_hit_rule_period, ix_reportable_event_period, ix_txn_decision_relationship is not used: decisions scan the month's partition)

# routes_monitoring.py
GET /monitoring/metrics?period=YYYY-MM[&rule_version_id]   get_monitoring_metrics   staff; API key monitoring:read -> MetricsResponse; 422 PERIOD_INVALID
```

CLI: `monitoring replay --tenant <slug> --from <date> --to <date> --dry-run` prints one line per rule, `<code> v<n>: simulated <s>, stored <t>, missing <m>, extra <e>`, then `identical` or `differs`, and exits 0 when identical, 1 otherwise; without `--dry-run` it exits 2 with `error: only --dry-run is supported in SP-04` (Deviation 10); a range longer than `MONITORING_SIMULATION_MAX_DAYS` exits 1 with `error: SIMULATION_RANGE_TOO_LONG`.

- [ ] **Step 1: Write the failing tests.** The replay conftest uses 4b's structuring golden (three near-threshold cash deposits in a week) processed through 4a's job so one `CASH_STRUCTURING` hit is stored.
  - `test_replay.py`: `test_replay_reproduces_the_stored_hits_of_a_week` (`identical`), `test_replay_twice_gives_the_same_hits_and_writes_nothing` (row counts of every table whose name starts with `transaction`, `agg_`, `rule_`, `reportable_event`, `capture_`, `txn_decision`, `tier_limit_notice`, `behaviour_profile`, `outbox` and `audit_event` are equal before and after two replays; the two reports are equal), `test_a_hit_deleted_from_the_store_shows_as_missing`, `test_a_rule_activated_after_the_week_shows_its_hits_as_extra`, `tests/test_cli.py`: the output lines and the three exit codes.
  - `test_metrics.py`: `test_metrics_count_hits_parties_and_severities_for_the_month`, `test_time_to_hit_percentiles_come_from_created_at_minus_first_occurred_at`, `test_typology_coverage_counts_active_templates_per_tag`, `test_captures_per_report_code_against_the_reconciled_count` (a `CLOSED` period with one discrepancy), `test_decision_percentiles_and_fail_counts` (`OPS-502`), `test_a_rule_version_filter_narrows_everything_but_coverage`, `test_a_bad_period_is_422_period_invalid`, `test_the_monitoring_read_scope_is_required_for_keys`, `test_case_rates_are_null_until_sp06`.
  - `tests/platform/test_errors.py`: `(PeriodInvalidError, 422, "PERIOD_INVALID")`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, the gates and `make ci`.
- [ ] **Step 5: Docs.** README CLI line for `monitoring replay`; the API table row; the `metrics.py` docstring (computed on read; no table; SP-11 decides on a materialised row).
- [ ] **Step 6: Commit** `feat(monitoring): replay --dry-run and the effectiveness metrics endpoint`.

---

### Task C7: The retention job

Depends on **C4** (`txn_decision` partitions), **C1** (counterparty subjects), 4a (A1 `xolotl.platform.partitions`; A2 `transaction` partitions, `transaction_ref`, `transaction_parked`, `agg_bucket`, `agg_distinct`, `agg_declaration`) and 4b (B4 `rule_hit`, `rule_hit_transaction`, `reportable_event`, `capture_group`, `capture_period`). Touches `src/xolotl/platform/partitions.py` and `src/xolotl/modules/tenants/service.py` and the pinned schedule tests. Console: no.

**Files:**
- Create: `src/xolotl/modules/monitoring/process/retention.py`
- Modify: `src/xolotl/platform/partitions.py` (A1; `retire_partition`), `monitoring/composition.py` (`register_job_handler(RETENTION_KIND, handle_retention)`), `src/xolotl/modules/tenants/service.py` (`MONITORING_RETENTION_SCHEDULE`, one `queue.schedule`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (`monitoring_retention_cron`), `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`, `tests/platform/test_config.py`
- Test: `tests/modules/monitoring/process/test_retention.py`, `tests/platform/test_partitions.py` (+)

**Produces:**

```python
# platform/partitions.py (4a's module; added)
def retire_partition(conn: Connection, *, table: str, month: date) -> bool
    # under the table's advisory lock: ALTER TABLE <table> DETACH PARTITION <name>; DROP TABLE <name>; False when the partition does not exist;
    # refuses (ValueError) a month whose partition still holds rows referenced by another table (the caller deletes those first)

# process/retention.py
RETENTION_KIND = "monitoring.retention"
@dataclass(frozen=True, slots=True)
class RetiredMonth: month: str; hit_links: int; events: int; groups: int; hits: int; decision_links: int; decisions_dropped: bool; parked: int; refs: int; dropped: bool
@dataclass(frozen=True, slots=True)
class RetentionReport: retired: tuple[RetiredMonth, ...]; skipped: tuple[str, ...]; counterparties_purged: int; buckets_pruned: int

def retention_period(s: Session, *, today: date) -> IsoDuration | None
    # the longest retention.transactions over effective_policy(on=today, product=p) for p in {None} | the products of the tenant's
    # assignments effective today (assignments_on); None when no policy is in force (the job then does nothing and logs)
def months_due(s: Session, *, today: date, period: IsoDuration) -> list[date]
    # the first days of the transaction partitions (pg_inherits over `transaction`) whose month end is before today - period, oldest first
def held_parties_in(s: Session, month: date) -> int           # join transaction.party_id -> party.legal_hold for the month's rows
def retire_month(session_factory: SessionFactory, admin_engine: Engine, *, tenant_id: UUID, month: date) -> RetiredMonth
    # spec §8.6's order, each step its own transaction: rule_hit_transaction of the month; reportable_event of the month and the
    # capture_group rows left empty; rule_hit whose first_occurred_at is in the month with every rule_hit_transaction link of those hits;
    # txn_decision links (transaction_id, occurred_at NULL) and transaction.decision_id, decided_at of the month's rows; the txn_decision
    # partition of the month (retire_partition); transaction_parked and transaction_ref rows of the month; the transaction partition
def purge_counterparties(admin_engine: Engine, *, tenant_id: UUID) -> int
    # recompute txn_count from transaction; rows with txn_count = 0: delete match_disposition, screening_match, screening_hit, screening_run
    # of the subject, the subject, counterparty_name_index, the row (Deviation 11); returns the count
def prune_buckets(s: Session, *, today: date) -> int
    # longest window among agg_declaration (ISO duration or the calendar period); delete agg_bucket and agg_distinct rows whose bucket end
    # is before today - longest, except ACCUM buckets with bucket_end >= now and MONTH/QUARTER buckets of a capture_period OPEN or REOPENED
def handle_retention(session_factory, admin_engine) -> JobHandler
    # per tenant job: period -> months_due -> for each month: held_parties_in > 0 ? skipped : retire_month; purge_counterparties; prune_buckets;
    # audit monitoring.retention_ran {"retired": [...counts per month], "skipped": [...], "counterparties_purged", "buckets_pruned"}
```

The schedule row `("monitoring.retention", "0 5 1 * *", "UTC", "monitoring.retention", True)` is inserted in name order in `PLATFORM_SCHEDULES` and the six-tuple with `{}` in the kernel-events test; the test's `daily` name set gains it.

- [ ] **Step 1: Write the failing tests** (`test_retention.py`; the tenant has the ITF pack with `retention.transactions = P10Y` and a second tenant with an override `P2Y` where the policy allows, else the fixture patches the pack through `install_both_packs`' golden; months are seeded by inserting processed transactions, hits with `TRIGGER` and `WINDOW` links, captures, groups, decisions and parked rows under a patched clock):
  - `test_a_month_inside_the_retention_period_is_kept` (ten years minus a day)
  - `test_a_month_past_the_period_is_retired_in_dependency_order_and_its_partitions_dropped` (a hit of the month with a `WINDOW` link into the next month; both partitions gone; the next month's rows intact; the audit row's counts)
  - `test_a_month_with_a_held_party_is_skipped_and_named`
  - `test_each_step_commits_on_its_own` (the `transaction_ref` delete patched to raise on the first attempt; the earlier steps' deletes are committed; the retry finishes the month)
  - `test_orphaned_counterparties_go_with_their_subjects_findings_and_dispositions` (a counterparty whose only transaction was in the retired month and whose finding carries an MLRO disposition; afterwards no row, subject, finding, hit, run, disposition or index row; a counterparty with a live transaction stays)
  - `test_buckets_older_than_the_longest_window_are_pruned_except_open_accumulators_and_open_periods`
  - `test_a_tenant_without_a_policy_retires_nothing`
  - `test_the_job_runs_through_the_schedule` (`drain`; the audit row exists)
  - `tests/platform/test_partitions.py` (+): `test_retire_partition_detaches_and_drops_under_the_lock`, `test_retire_partition_refuses_a_referenced_month`.
  - Pinned schedule tests and `test_config` updated.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, the isolation harness, the gates and `make ci`.
- [ ] **Step 5: Docs.** The `retention.py` module docstring (Decision 22's order, legal hold, the admin-engine step and why). README configuration row and a "Retention" paragraph.
- [ ] **Step 6: Commit** `feat(monitoring): monthly retention of transaction and decision months, orphaned counterparties and stale buckets`.

---

### Task C8: Behaviour profiles and the declared profile

Depends on **C4** (`behaviour_profile`, `customer_relationship.declared_profile`), 4a (A8 `PROCESSED` transactions with `unit_amounts`, `period_keys`, `counterparty_country`, `instrument`, `rail`; A5 `monitoring_app`; A10 `routes_monitoring.router`) and 4b (**B7** `profile_for` in `rules/evaluate.py`, which this task makes read the column into B2's `DeclaredProfile`). Touches the party module (`schemas.py`, `customer.py`, `routes_relationships.py`), `tenants/service.py` and the pinned schedule tests. Runs in parallel with C5, C6 and C7. Console: no. First on the spec §23 cut list, so nothing else depends on it but C9 and the exit test's optional section.

**Files:**
- Create: `src/xolotl/modules/monitoring/profiles/job.py`
- Modify: `src/xolotl/modules/party/schemas.py` (`DeclaredProfile`, `RelationshipPatchRequest`; `CustomerRelationshipResponse.declared_profile`), `party/customer.py` (`set_declared_profile`), `party/routes_relationships.py` (`PATCH /{relationship_id}`), `monitoring/rules/evaluate.py` (B7; `profile_for` fills `EvalProfile.declared_profile` from the column), `routes_monitoring.py` (`GET /profiles/{relationship_id}`), `monitoring/schemas.py` (`BehaviourProfileResponse`), `monitoring/events.py` (`BEHAVIOUR_PROFILE_UPDATED`, staff-only), `monitoring/composition.py` (handler), `src/xolotl/cli.py` (`monitoring profiles`), `src/xolotl/modules/tenants/service.py` (`MONITORING_PROFILES_SCHEDULE`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (two settings), `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`, `tests/platform/test_config.py`
- Test: `tests/modules/monitoring/profiles/test_profiles.py`, `tests/modules/party/test_declared_profile.py`, `tests/test_cli.py` (+)

**Produces:**

```python
# party/schemas.py
class DeclaredProfile(BaseModel):            # extra = "forbid"
    monthly_inflow_minor: int = Field(ge=0); monthly_outflow_minor: int = Field(ge=0); currency: Literal["MXN"]   # Deviation 12
    expected_countries: list[CountryCode] = []; expected_instruments: list[str] = []   # instrument codes as ^[A-Z_]{2,16}$ strings: party never imports monitoring, so not 4a's Instrument enum
class RelationshipPatchRequest(BaseModel): declared_profile: DeclaredProfile | None   # None clears it

# party/customer.py
def set_declared_profile(s: Session, relationship_id: UUID, profile: DeclaredProfile | None) -> CustomerRelationship
    # stores model_dump(mode="json") or NULL; audit relationship.declared_profile_set {"currency": "MXN"} (no amounts in the audit row)
# routes_relationships.py: PATCH /relationships/{relationship_id}  patch_customer_relationship  PartyWriter (staff writers; parties:write); Idempotency-Key -> CustomerRelationshipResponse (with declared_profile)

# profiles/job.py
PROFILES_KIND = "monitoring.profiles"
def profile_month(today: date) -> str                          # the previous Mexico City month, "YYYY-MM"
def compute_profiles(s: Session, *, month: str) -> int
    # per relationship with PROCESSED rows whose period_keys->>'MONTH' = month: metrics (spec §5.2; amounts from unit_amounts["MXN/SAME_DAY"]
    # as decimal strings of minor units, shares per Deviation 13, distinct_counterparties over counterparty_id, active_days over period_keys->>'DAY');
    # declared_profile copied; deviation = {"monthly_inflow": actual/declared, "monthly_outflow": ...} as 1-place decimal strings when declared
    # and the declared amount > 0; deviated = any ratio >= monitoring_profile_deviation_ratio; UPSERT by (tenant_id, relationship_id, month);
    # publish BEHAVIOUR_PROFILE_UPDATED per row written or changed; returns the count
def handle_profiles(s: Session, job: Job) -> None              # month = payload.get("month") or profile_month(policy_date(clock.now()))
```

`GET /v1/monitoring/profiles/{relationship_id}` (staff only) returns the last 12 `BehaviourProfileResponse` rows (`month`, `metrics`, `declared_profile`, `deviation`, `deviated`, `computed_at`), newest first; 404 for an unknown relationship. CLI: `monitoring profiles --tenant <slug> --month <YYYY-MM>` runs `compute_profiles` synchronously and prints `profiles: <n> relationships for <month>`. B7's `profile_for` reads `CustomerRelationship.declared_profile` into `EvalProfile.declared_profile` (B2's `DeclaredProfile(monthly_inflow_minor, monthly_outflow_minor, currency)`; the `getattr` fallback goes), so `PROFILE_DEVIATION` fires once a declaration exists. The schedule row `("monitoring.profiles", "0 3 2 * *", "America/Mexico_City", "monitoring.profiles", True)` joins the pinned tests in name order.

- [ ] **Step 1: Write the failing tests.**
  - `test_declared_profile.py`: `test_a_staff_writer_sets_and_clears_the_declared_profile_and_it_is_audited` (the audit detail is `{"currency": "MXN"}` only), `test_a_key_with_parties_write_may_patch` , `test_a_currency_other_than_mxn_is_422`, `test_an_unknown_field_is_422`, `test_auditor_ro_cannot_patch`, `test_another_tenant_gets_404`.
  - `test_profiles.py`: `test_a_month_of_processed_rows_yields_the_metrics` (golden values from six fixture transactions: inflow, outflow, counts, the three shares, distinct counterparties, active days), `test_without_a_declaration_deviation_is_null_and_not_deviated`, `test_a_ratio_at_or_above_the_threshold_marks_deviated_and_publishes` (`2.0` default; one staff-only event; a wildcard endpoint gets nothing), `test_a_ratio_below_the_threshold_does_not_deviate`, `test_the_threshold_setting_is_honoured` (`3.0`), `test_recomputing_a_month_updates_the_row_and_publishes_only_on_change`, `test_the_job_computes_the_previous_mexico_city_month` (clock at 2027-02-02T09:00Z → `2027-01`), `test_the_profile_endpoint_returns_the_last_twelve_months_newest_first`, `test_the_evaluator_profile_carries_the_declaration` (B2's `PROFILE_DEVIATION` golden fires only after the PATCH), `tests/test_cli.py`: the `monitoring profiles` line.
  - Pinned schedule tests, `test_config` with the two fields (an invalid cron is refused; a ratio of `0` is refused).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, `tests/modules/party` whole, the gates and `make ci`.
- [ ] **Step 5: Docs.** README configuration rows, the API table rows (PATCH and GET), the CLI line; the `job.py` docstring (Deviations 12 and 13; what SP-07 consumes).
- [ ] **Step 6: Commit** `feat(monitoring): monthly behaviour profiles and the declared profile of a relationship`.

---

### Task C9: Console: Admin › Rieles and the Cliente › Perfil de comportamiento tab

Depends on **C4** (the rails API), **C8** (the profiles API and the relationship PATCH), **A11** (`ACCESS.transactions`, the console's transactions kit) and on SP-02c's `PartyPage.tsx` and `CustomerRelationshipsCard.tsx`. Touches `console/` only, plus `api/schema.d.ts` regeneration. Console: **yes**. The Perfil tab is first on the spec §23 cut list; its steps come last so it can be dropped without touching Rieles.

**Files:**
- Create: `console/src/features/rails/{RailsPage.tsx, RailsPage.test.tsx, queries.ts}` (its own folder; 4b has none named `monitoring`), `console/src/features/parties/{BehaviourProfileCard.tsx, BehaviourProfileCard.test.tsx, DeclaredProfileDialog.tsx, DeclaredProfileDialog.test.tsx, profile-form.ts, profile-form.test.ts}`
- Modify: `features/parties/queries.ts` (`useBehaviourProfiles(relationshipId)`, `useSetDeclaredProfile(relationshipId)`), `features/parties/PartyPage.tsx` (the card per `ACTIVE` customer relationship under the collections, behind `hasRole(ACCESS.transactions)`, A11's key with every staff role, so this task does not wait for B14's `ACCESS.monitoring`), `App.tsx` (route `/admin/rails`), `components/layout/nav.ts` (flat entry `nav.rails`, icon `Waypoints`, `roles: ACCESS.rails`, after `nav.connectors`), `auth/access.ts` (`rails: ['ADMIN']`), `i18n/{es-MX,en}.json`, `api/types.ts`, `api/schema.d.ts`

**Produces:**
- `RailsPage` (`/admin/rails`, title "Rieles", ADMIN): one row per rail with its fail mode (`Permisivo: revisar` for `FAIL_OPEN`, `Restrictivo: bloquear` for `FAIL_CLOSED`), the `explicit` badge "Predeterminado" when no row exists, and a `Switch` per rail that calls `PUT /v1/monitoring/rails/{rail}` through `useIdempotentMutation`; a toast on success, the problem's code on failure. Copy under `rails.*`: `rails.title`, `rails.description` ("Qué responde una decisión cuando el motor no alcanza a evaluarla dentro del presupuesto."), `rails.mode.FAIL_OPEN`, `rails.mode.FAIL_CLOSED`, `rails.default`.
- `BehaviourProfileCard` (per relationship): the last 12 months as a bar chart (inflow and outflow in MXN per month, SVG drawn in the component, no chart library) with the declared inflow and outflow as dashed lines when a declaration exists, the `deviated` badge ("Desviación") on a month at or above the ratio, a table of the month's metrics, and "Editar perfil declarado" (writers: `ACCESS.partiesWrite`) opening `DeclaredProfileDialog` (monthly inflow and outflow in MXN with two decimals, expected countries, expected instruments; `profile-form.ts` validates and converts to minor units). Copy under `parties.profile.*` and the tab label `parties.detail.tabs.profile` ("Perfil de comportamiento").
- No customer-facing string: both areas are staff-only; the copy names no rule, hit or suspicion.

- [ ] **Step 1: Write the failing tests.** `RailsPage.test.tsx` (renders the rails from a fake response; flipping a switch sends `{fail_mode: "FAIL_CLOSED"}` with an idempotency key; a 403 shows the code; AUDITOR_RO sees nothing: the route is guarded), `BehaviourProfileCard.test.tsx` (twelve months rendered; the deviated badge on the flagged month; the empty state "Sin perfil todavía"; the dialog hidden for a reader), `DeclaredProfileDialog.test.tsx` and `profile-form.test.ts` (MXN with two decimals → minor units; a negative amount refused; the PATCH body shape), `auth/access.test.ts` (+ `rails`), `i18n/catalogue.test.ts` (both catalogues hold the new keys).
- [ ] **Step 2: Run to verify failure.** `cd console && npm test -- RailsPage BehaviourProfile`.
- [ ] **Step 3: Implement.** Regenerate `schema.d.ts` against the merged API first (`npm run gen:api:file`).
- [ ] **Step 4: Run to verify pass.** The console gates: `npm run typecheck && npm run lint && npm test && npm run build`; `make ci` for the regenerated types.
- [ ] **Step 5: Docs.** `console/README.md`: the two new areas in the pages list.
- [ ] **Step 6: Commit** `feat(console): Admin › Rieles and the behaviour profile tab`. The Perfil tab may be a second commit, `feat(console): behaviour profile tab`.

---

### Task C10: Perf records (marked, excluded from CI)

Depends on **C1** and **C5** (A8's ingest and processing and B7's rules and hits come with them). The `perf` marker, `-m "not perf"` and `make perf` exist (SP-03c F7). Console: no. Seventh on the spec §23 cut list.

**Files:**
- Create: `tests/perf/test_sp04_perf.py`
- Modify: `tests/perf/conftest.py` (`record` gains a `file` parameter defaulting to `.perf/sp03.json`; SP-04's tests pass `.perf/sp04.json`), `.gitignore` (unchanged: `.perf/` is ignored), `README.md` (the performance-tests line names the SP-04 file)

**Produces:** three measured tests, each asserting its functional outcome and recording its numbers, seeded with `Draw(SEED)` from `tests/perf/conftest.py` and A4's `make_item`:

- `test_decide_p99_under_the_budget` (`NFR-TM-001`, `FR-TM-003`): one tenant on the ITF pack, 200 relationships with six months of processed history (12,000 transactions through `ingest_batch` and the processing job, enough `agg_bucket` rows per relationship for every window the sync rules read), `TIER_N1_MONTHLY_OPS`, `TIER_N2_MONTHLY_OPS` and `COUNTERPARTY_TRUE_MATCH` active and `sync`. 50 warm-up and 1,000 measured `decide()` calls over the relationships in a seeded order, half with a known counterparty. Asserts every decision has `mode == EVALUATED` and records p50, p95, p99 and `db_ms` p95; the recorded budget is p99 under 100 ms (`NFR-TM-001`) and the service budget 80 ms; the floor that fails the test is p99 under 500 ms (generous for a laptop; SP-11 gates the real one).
- `test_alert_latency_p95_over_1000_transactions` (`NFR-TM-002`): 1,000 transactions over 100 relationships in one `POST /v1/transactions` batch, with ten structuring scenarios planted, then the runner drained; asserts every row is `PROCESSED` and exactly ten `CASH_STRUCTURING` hits exist; records the elapsed time from the batch's `received_at` to the last `processed_at`, the p95 of `rule_hit.created_at - transaction.received_at` over the hits, and transactions per second. The only gate is that the drain finishes under 300 s.
- `test_counterparty_first_sight_throughput`: 1,000 new counterparties resolved in one minute of clock (seeded names drawn from the list pool, 5 % near OFAC entries), the first-sight job drained; asserts every row has `screened_at` and the near ones are `PENDING_REVIEW`; records counterparties per second, with a floor of 20/s as SP-03c's periodic floor.

- [ ] **Step 1: Write the tests** (marked `perf` through `pytestmark`).
- [ ] **Step 2: Run** `make perf` locally; record machine, Postgres container and the numbers in the PR body. `uv run pytest --collect-only -q | grep sp04_perf` → nothing in the default run.
- [ ] **Step 3: Docs.** README "Performance tests": `.perf/sp04.json` and what each number is.
- [ ] **Step 4: Commit** `test(monitoring): perf records for decide latency, alert latency and counterparty screening`.

---

### Task C11: The SP-04 exit test

Depends on C1 to C10 and on the whole of 4a and 4b being merged. It merges **last**, so the run it records is SP-04 as shipped. Console: no.

**Files:**
- Create: `tests/e2e/test_sp04_exit.py`; `tests/e2e/fixtures/sp04/README.md` (what each fixture holds and which spec sentence it serves), `batch_accumulation.json` (one crypto operation per month, July to November 2026, for the AVI accumulation, customer `CRYPTO-001`), `batch_week.json` (the week of 7 to 13 December 2026: three near-threshold cash deposits of `EMONEY-001` on the 8th, 9th and 10th at 70 to 100 % of USD 5,000; one cash deposit of USD 5,000 or more on Saturday 12 December; an international transfer on the 9th with a counterparty in `US` by `SWIFT`; the December crypto operation of `CRYPTO-001` with a fee that crosses 210 UMA for the six-month group; one transfer whose counterparty name is a perturbed OFAC entry (dropped second surname, accent loss); one crypto operation whose `counterparty.wallet_address` is an OFAC digital-currency address; a Monday 14 December cash operation per Deviation 14), `late_crypto.json` (a crypto operation of `CRYPTO-001` dated 10 December, sent on 12 December), `decide_n1.json` (the N1 relationship's candidate that passes 750 UDI month to date). No `unit_values.yaml`: the unit rows are B7's `tests/modules/monitoring/fixtures/unit_values_2026.yaml` (UDI, UMA and `USD_MXN_FIX` for July to December 2026, with the 11 December FIX and no 12 or 13 December row), loaded with `tests/platform/units_support.py::load_unit_values`
- Modify: `tests/e2e/conftest.py` (none expected; `drain` is reused), `README.md` ("SP-04 exit" line)

**Produces:** one test, `test_sp04_exit`, in the sections and style of `tests/e2e/test_sp03_exit.py` (`section`, `Demo`, `Receiver`, `_delivered_types`, the helpers), with the clock patched to 2026-06-30T15:00Z for sections 1 and 2 (the rules must be active before the first fixture date, July 2026: `rules activate` activates from today and B6 refuses a past `effective_from`), to 2026-12-14T15:00Z for sections 3 to 8 and 10 to 14, and to 2027-01-01T11:00Z for section 9; each section prints one line on failure. The sections follow spec §19's exit paragraph:

1. **Two tenants.** `acme` with `MX/ITF-IFPE` assigned to product `EMONEY` and `MX/LFPIORPI-AVI` to product `CRYPTO` (A2's `both_packs_tenant`), `beta` with `MX/ITF-IFPE` tenant-wide (A2's `itf_tenant`). Holidays seeded (`seed_mx_holidays`), unit values loaded from B7's fixture, `lists seed`, `OFAC_SDN` and `UN_CONSOLIDATED` loaded from the library fixtures, `lists.poll` drained for both. Staff per tenant (`ADMIN`, `MLRO`, `SENIOR_ANALYST`, `ANALYST`), an API key per tenant with `transactions:write transactions:read transactions:decide parties:write screening:read`, a wildcard webhook endpoint per tenant on the `Receiver`.
2. **Rules.** `xolotl rules load` (CLI), `rules seed-tenant --tenant acme`, then, still under the 2026-06-30 clock, `rules activate --tenant acme --version <id> --approved-by mlro@acme.mx --simulate-from 2026-06-01 --simulate-to 2026-06-29` (29 days, under `MONITORING_SIMULATION_MAX_DAYS`; an empty range simulates to zero hits) for `CASH_STRUCTURING`, `COUNTERPARTY_TRUE_MATCH` and `TIER_N1_MONTHLY_OPS`, so the three assignments are effective from 2026-06-30 and cover every fixture; `GET /v1/monitoring/rules` shows the three `ACTIVE`. The clock then moves to 2026-12-14.
3. **Customers.** Tenant A: `EMONEY-001` (tier `N2`, product `EMONEY`), `CRYPTO-001` (product `CRYPTO`), `EMONEY-N1` (tier `N1`); tenant B: `B-001`. Parties created through `POST /v1/parties`, relationships through `POST /v1/relationships`, drained.
4. **Batches.** `POST /v1/transactions` with `batch_accumulation.json` then `batch_week.json` as tenant A's key: 202 with `accepted == len(items)`; the same two bodies re-sent with new idempotency keys: `accepted == 0`, `duplicates == len(items)`; `drain`; every transaction of A is `PROCESSED`; tenant B ingests one transaction of `B-001`.
5. **Enrichment.** `GET /v1/transactions/{id}` as A's analyst for the 12 December cash deposit shows `unit_amounts` with `USD/PRIOR_BANKING_DAY` (`resolved_date == "2026-12-11"`, a `unit_value_id`), `UDI/LAST_DAY_PREV_MONTH` (`resolved_date == "2026-11-30"`), `UMA/SAME_DAY` and `MXN/SAME_DAY`; the API key's view of the same id is exactly `{id, external_id, occurred_at, status, late}` with `status == "PROCESSED"`.
6. **Structuring.** `GET /v1/monitoring/hits` (analyst) holds exactly one `CASH_STRUCTURING` hit for `EMONEY-001` whose evidence transactions are the three near-threshold deposits with `role` `TRIGGER` or `WINDOW`; `GET /v1/transactions/{id}` for them as the API key shows `PROCESSED` and no `hits` key.
7. **Captures.** `GET /v1/monitoring/captures?capture_code=ITF_ROR_CASH` holds exactly one event, the 12 December deposit, with `period_key` `2026-Q4` and `unit_values.converted.resolved_date == "2026-12-11"`; `?capture_code=AVI_XVI_A` holds six events (July to December) sharing one `capture_group_id`, each with its own month's `period_key` and `unit_values.threshold == {"amount": "210", "unit": "UMA"}`.
8. **Late event.** `POST /v1/transactions` with `late_crypto.json`; `drain`; the row is `late == true`; the AVI group's six events are `SUPERSEDED` with `superseded_by_id`, seven `CAPTURED` events exist in a new group; the `CASH_STRUCTURING` hit count is still one and its `fired_count` unchanged.
9. **Period close.** With the clock at 2027-01-01T11:00Z, `xolotl monitoring period-close --tenant acme`; `GET /v1/monitoring/captures/periods` shows `2026-12` for `AVI_XVI_A` and `ITF_INTL_TRANSFER` and `2026-Q4` for `ITF_ROR_CASH` as `CLOSED` with `discrepancies` empty and `reconciled_count == event_count`.
10. **Decide.** `POST /v1/transactions/decide` with `decide_n1.json` as A's key answers 200 `{"decision": "BLOCK", "reasons": [{"code": "TIER_LIMIT", "tier": "N1", "limit_code": "monthly_ops"}], ...}` with `latency_ms < 80` recorded (asserted under 1,000 in CI, recorded in the log); the staff decision view names `TIER_N1_MONTHLY_OPS`; the same payload ingested as a transaction and drained links to the decision; tenant A's endpoint received exactly one `monitoring.tier_limit_reached.v1`, and `_delivered_types(acme)` holds no type starting `monitoring.rule_hit_`, `monitoring.reportable_event_`, `monitoring.capture_period_`, `screening.counterparty_` or equal to `monitoring.decision_issued.v1`.
11. **Counterparties.** `GET /v1/counterparties?status=PENDING_REVIEW` as A's analyst lists exactly the OFAC-named counterparty and the OFAC wallet one; `GET /v1/screening/matches` holds a finding for each with `counterparty_id` set, the wallet one's hit features naming `ID_MATCH_FLOOR`; the MLRO confirms the wallet finding `TRUE_MATCH` (analyst escalates first), `drain`, and the counterparty row reads `TRUE_MATCH`; one more crypto operation to that wallet, ingested and drained, yields a `COUNTERPARTY_TRUE_MATCH` hit.
12. **Governance and replay.** A's `SENIOR_ANALYST` creates a new `CASH_STRUCTURING` version with `near_ratio` `0.75` (`POST /v1/monitoring/rules/{id}/versions`, `PATCH`), simulates 2026-06-14 to 2026-12-13 (`drain`), submits; the same user's `approve` is 409 `RULE_TRANSITION_NOT_ALLOWED` with `SAME_AUTHOR`; the MLRO approves and activates from 2026-12-14; `xolotl monitoring replay --tenant acme --from 2026-12-07 --to 2026-12-13 --dry-run` exits 0 and prints `identical`.
13. **Tenant B sees nothing.** `GET /v1/transactions`, `/v1/monitoring/hits`, `/v1/monitoring/captures`, `/v1/counterparties` as B's analyst hold only B's own transaction and nothing else; `GET /v1/transactions/{A's id}` and `/v1/monitoring/decisions/{A's id}` are 404; B's wildcard endpoint received no `monitoring.tier_limit_reached.v1`.
14. **Audit.** `GET /v1/audit/verify` for each tenant reports `valid` with `entries_checked > 0`.

- [ ] **Step 1: Write the test and the fixtures.** It fails only where a dependency is missing.
- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_sp04_exit.py --no-cov -q`. It must stay under 120 s on CI; trim fixtures, not checks.
- [ ] **Step 3: Run `make ci`.** The test is part of the default suite (not marked).
- [ ] **Step 4: Docs.** README "SP-04 exit": what the test proves and how to run it; the fixtures README.
- [ ] **Step 5: Commit** `test(e2e): SP-04 exit, two tenants, batches, enrichment, structuring, captures, late event, period close, decide, counterparties, governance, isolation, audit`.

---

## Parallelism

| Id | Title | Depends on | Console | Branch slug |
|---|---|---|---|---|
| C1 | Counterparty subjects, first-sight screening, status cache, wallet matching | A4, B4 | no | `sp04c-c1-counterparty-subjects` |
| C2 | Reverse screening of counterparties and the periodic walk | C1 | no | `sp04c-c2-counterparty-rescreen` |
| C3 | Counterparties API, queue findings, Contrapartes page | C1, A10 | **yes** | `sp04c-c3-contrapartes` |
| C4 | Migration 0014, decide and profile models, rail settings and rails API | A10, B4 | no | `sp04c-c4-migration-rails` |
| C5 | Decide API, fail modes, linkage, tier-limit webhook | C4, B7, B12 | no | `sp04c-c5-decide` |
| C6 | Replay --dry-run and metrics endpoint | C4, B8, B11 | no | `sp04c-c6-replay-metrics` |
| C7 | Retention job | C4, C1 | no | `sp04c-c7-retention` |
| C8 | Behaviour profiles and the declared profile | C4, B7 | no | `sp04c-c8-profiles` |
| C9 | Console: Admin › Rieles and the Perfil tab | C4, C8, A11 | **yes** | `sp04c-c9-console-rails-profile` |
| C10 | Perf records | C1, C5 | no | `sp04c-c10-perf` |
| C11 | SP-04 exit test | C1 to C10, A12, B13, B14, B15 (every SP-04 task); merged last | no | `sp04c-c11-exit` |

- **Wave 1** (C1 once A4 and B4 are merged, C4 once A10 and B4 are; beside the rest of 4b): C1 and C4 in parallel. C1 touches `screening/` only; C4 touches `monitoring/` and the migration chain.
- **Wave 2:** C2 after C1 and C3 after C1 and A10 (they share `api.py` and `schemas.py` edits with nothing of C2; C2 touches `rescreen/` only); C5 (after B7 and B12), C6 (after B8 and B11), C7 (after C1) and C8 (after B7) after C4, in parallel. C5, C6 and C8 each append to `routes_monitoring.py` or `routes_decide.py`, `monitoring/schemas.py`, `monitoring/composition.py` and `cli.py`; every edit is additive and the later task rebases.
- **Wave 3:** C9 after C8 and A11; C10 after C5 and C1.
- **Wave 4:** C11, last.
- **Shared files across SP-04 plans:** `src/xolotl/app.py`, `src/xolotl/cli.py`, `src/xolotl/models.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `src/xolotl/modules/tenants/service.py`, the pinned schedule tests, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_errors.py`, `tests/platform/test_config.py`, `src/xolotl/modules/monitoring/{composition,events,errors,schemas}.py`, `src/xolotl/modules/screening/{api,schemas,events,composition}.py`, `src/xolotl/modules/screening/counterparty/{models,resolve,schemas}.py`, and the console `nav.ts`, `access.ts`, `App.tsx` and both catalogues. Every edit is additive; the later task rebases and re-applies its own lines; `schema.d.ts` is regenerated, never merged by hand (the ownership table is reconciliation finding 17). The migration chain is strict: 0012 (A2) before 0013 (B4) before 0014 (C4).

## Exit criteria

Spec §19's exit paragraph and the 4c rows of §21 (Q3, Q4, Q5, Q7, Q8 built as their defaults), refined for this plan:

- `make ci` is green after every task; the console gates pass after C3 and C9.
- A counterparty named by a transaction is screened on first sight by `screening.screen_counterparties`, its name index row holds tokens only, its row carries the status its subject has (after runs and after dispositions), and `screening.counterparty_status_changed.v1` never reaches a webhook (C1). An OFAC digital-currency address matches a wallet counterparty exactly and a one-character difference does not (C1, `FR-SC-015`). A list delta re-screens the counterparties blocked near its added and changed entries, and the monthly walk visits counterparties after parties, skipping those without a transaction in 12 months (C2, `FR-SC-012`, `DR-308`). `GET /v1/counterparties` lists them masked with their status and counts, the detail reveals with `VIEW_PII`, and the Contrapartes page shows them (C3).
- Migration 0014 creates `rail_setting`, the partitioned `txn_decision`, `behaviour_profile` and `customer_relationship.declared_profile` with forced RLS, discovered by the isolation harness (C4). `PUT /v1/monitoring/rails/{rail}` sets a fail mode, audited (C4).
- `POST /v1/transactions/decide` answers `ALLOW`, `REVIEW` or `BLOCK` from point reads with the candidate counted in its own aggregates; the N1 limit blocks and the N2 limit reviews with `TIER_LIMIT`, any other firing is `RISK_REVIEW`; over budget or on a database error the rail's fail mode answers and never a 503; every decision is stored with its ruleset hash and linked to the later transaction by `external_id`; `monitoring.tier_limit_reached.v1` is sent once per relationship, limit and month across both paths, and `monitoring.decision_issued.v1` to no webhook at all (C5, `FR-TM-003`, `FR-TM-008`, `INT-505`, `DR-506`).
- `xolotl monitoring replay --dry-run` over a week reproduces the stored hits and writes nothing; `GET /v1/monitoring/metrics?period=` computes hits, parties, severities, time to hit, typology coverage, captures against reconciliation and decision percentiles on read (C6, `FR-TM-018`, `FR-TM-012`, `OPS-502`, `OPS-503`, `OPS-504`).
- `monitoring.retention` retires every month past the longest `retention.transactions` in dependency order, skips and names months with a held party, purges counterparties no transaction references and prunes buckets past the longest window (C7, `NFR-TM-005`).
- `monitoring.profiles` writes a monthly behaviour profile per active relationship, compares it with the declared profile staff set through `PATCH /v1/relationships/{id}`, flags a deviation at the configured ratio and publishes the staff-only event; the Perfil tab shows it and Admin › Rieles the rails (C8, C9, `FR-TM-013`, `DR-507`).
- `make perf` records decide p50, p95 and p99, the alert latency over 1,000 transactions and the counterparty first-sight throughput (C10, `NFR-TM-001`, `NFR-TM-002`; not in CI).
- `tests/e2e/test_sp04_exit.py` passes in `make ci`: two tenants with their packs by product; the Mexico library loaded, seeded and three rules activated for tenant A; the December batch accepted, re-sent without duplicates and processed; UDI, UMA and USD amounts with their unit rows on the staff view; `CASH_STRUCTURING` once with three deposits as evidence while the API client sees `PROCESSED`; `ITF_ROR_CASH` capturing the one cash operation at the prior banking day's FIX and `AVI_XVI_A` the whole six-month group; a late crypto operation changing totals, re-firing nothing twice and superseding the group; December 2026 and Q4 2026 closed with zero discrepancies; the N1 decision `BLOCK` with `TIER_LIMIT` and one limit webhook while no hit, capture, period or counterparty event reaches any endpoint; the OFAC-named and OFAC-wallet counterparties `PENDING_REVIEW` in the counterparties list and the screening queue, then a confirmed one firing `COUNTERPARTY_TRUE_MATCH`; a tuned `CASH_STRUCTURING` simulated, submitted, approved by a second user and activated, and the replay reproducing the stored hits; tenant B seeing none of it; audit verify intact for both (C11).

## Open questions for the product owner

1. **Private-list re-screens of counterparties.** Spec §11.3 re-screens counterparties on shared-list deltas and in the periodic walk, and says nothing of a new private-list version (the LPB). A counterparty on the LPB is therefore found at the next monthly walk, up to a month after the upload. *Default:* as the spec says; the private-list walker stays party-only. Adding counterparty pages to it is the same change as C2's periodic pages, about half a day, if wanted now.
2. **Re-indexing `counterparty_name_index` on a matcher change.** The table carries `matcher_version` (spec §5.2) but spec §11 does not say the `REINDEX` walk covers it, so a matcher bump leaves counterparty rows at the old version until each is re-indexed on its next resolution, which never happens for a row that is only re-screened. *Default:* not in 4c; `ensure_screening_index` keeps looking at `party_name_index` only. The fix is a counterparty page in the re-index walk (as question 1).
3. **Counterparty purge versus the compliance record.** Spec §8.6 deletes an orphaned counterparty with its subject and findings, which removes dispositioned findings an MLRO made; parties keep theirs and are redacted instead (SP-03c F5). *Default:* delete, as the spec says, through the admin engine (Deviation 11), since the transaction the finding was about is gone with its month. The alternative is to keep a counterparty whose findings carry a disposition and redact its name index and hit tokens like a purged party.
4. **The demo date.** Spec §19's "prior banking day across the 12 December Banxico holiday" falls on a Saturday in 2026, so the fixture exercises the weekend rule (Deviation 14). If the demo should show a weekday holiday, the fixture can move to a week containing one (1 January 2027 is a Friday).
