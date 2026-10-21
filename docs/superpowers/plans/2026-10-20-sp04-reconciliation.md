# SP-04 plans: cross-plan reconciliation record

The three SP-04 plans (`2026-10-20-sp04a-transactions-aggregates.md`, tasks A1 to A12; `2026-10-20-sp04b-rules-captures.md`, B1 to B15; `2026-10-20-sp04c-counterparties-decide-exit.md`, C1 to C11) were written in parallel by three writers who could not see each other, against the SP-04 spec (`docs/superpowers/specs/2026-10-20-sp04-monitoring-captures-design.md`, a draft dated 2026-10-20 that the product owner has not yet formally accepted; the plans bind themselves to its Decisions and §21 defaults under the blanket approval of 2026-10-19 and list their departures as Deviations). Each plan marked what it guessed about the others as *(assumed)*: 4a had 5 markers, 4b 24 and 4c 32. A cross-plan check compared every seam between them and the code on `main` at `0c68cb7` they build on (`xolotl.platform.{events,policy,units,calendar,audit.partitions}`, `modules.party`, `modules.screening`, the migration chain ending at `0011_screening_hit_run_index`). This file records the twenty-six findings below (22 from the first pass, 23 to 26 from fix round 1 of PR #98), the decision taken on each, and the sections edited.

## State

- **Applied.** Every finding is applied to the plans committed next to this file. The edits were exact string replacements with match counts checked (first pass 4a: 52, 4b: 77, 4c: 75; fix round 1 added 4a: 8, 4b: 18, 4c: 14). A "Round 1" line under a finding records what the review changed in it. Each plan keeps all its task sections, its Deviations, Review Focus, File structure, Parallelism, Exit criteria and Open questions. No *(assumed)* marker and no "4a's …"/"4b's …" placeholder is left in a dependency; every cross-plan reference names a task id and an exact symbol.
- **The code on `main` is untouched.** Where a plan assumed a platform mechanism `main` does not have (an opt-in webhook registry, a `today_mx` outside screening, a `PARTITIONED_TABLES` tuple), the plan now uses what the owning plan builds or what `main` has.
- **The screening library is untouched.** The spec's §11.4 library change (optional `NameQuery.names`) is withdrawn: 4c's Deviation 1 shows `main` already accepts `names=()` and scores identifiers alone. `tasks.json` therefore carries no `external` row.
- **The execution kit** is `docs/superpowers/execution/sp04/`. `tasks.json` carries the dependency edges of finding 20; `check-graph.mjs` validates the graph.
- **Not yet reviewed independently.** A reviewer should read the edited task sections against these findings before the tasks they touch start; a mismatch is fixed in a small docs PR.

## Findings

| # | Finding | Decision |
|---|---|---|
| 1 | Migration numbers and their order | `0012_transactions` (A2) → `0013_rules_captures` (B4) → `0014_decide_profiles` (C4), the spec's 0011, 0012, 0013; they merge in that order |
| 2 | 4c's C1 waited for "4b's B1" | C1 depends on A4 and B4 (the migration task is B4); the spec's "counterparty screening after 4a, in parallel with 4b" reads "after A4 and B4" |
| 3 | `JurisdictionLists` and `resolve_list_names`: 4a and 4b each needed the other first | List-name resolution is 4b's: B1 defines `resolve_list_names` and `resolve_definition`, B6 applies them when it loads a rule set; 4a never sees a name and A7 has no 4b dependency |
| 4 | Two processing-hook shapes (`register_evaluator` versus `ProcessingHook`) | One shape, 4b's, owned by A8: `ProcessingHook` with `declarations` and `evaluate`, `register_processing_hook`; the context carries `units`, `holidays` and `queue` |
| 5 | Names 4b guessed in 4a's pure modules | 4a's names: `KeyKind` in `txn.schema`, `matches(where, txn)`, `AggregateKey` and `AggregateValue` in `aggregates.store` (now in the pure contract), no `aggregate_key`, `enriched_from_row(row)`, `policy_date`, `period_keys` attributes, `PROCESS_KIND` in `kinds.py`, `ensure_declarations -> list[str]` |
| 6 | Where decision linkage lives | C5's `link_decisions(session, rows)` in `monitoring/decide/linkage.py`, called by A4's `ingest_batch` between steps 2 and 4 |
| 7 | The opt-in webhook mechanism | None exists and none is built: `monitoring.transaction_processed.v1` (4a) and `monitoring.decision_issued.v1` (C5) are staff-only; a per-endpoint opt-in is an open question (the delivery is last on the spec's cut list) |
| 8 | USD: 4a's Deviation 6 versus 4b's Deviation 8 | Consistent and recorded in both: MXN and USD only, the USD leg at the prior banking day's FIX, captures compare in unit space, fees convert through `unit_amounts_for` |
| 9 | `MemoryAggregateStore.seed` assumed by 4c | A7's `seed(key, declaration, buckets)` filled from `PostgresAggregateStore.buckets`; A7's store takes `incomplete_ids` so the decide path reads an unbackfilled declaration as `INCOMPLETE` |
| 10 | 4a pieces the decide path assumed | A6 gains `enrich_input`, A4 gains `find_account`; `parse_item` and `relationship_by_external_ref` replace `resolve_customer`; the profile is B7's `profile_for`, extended by C8; `account_id` is `UUID \| None` |
| 11 | 4b names 4c guessed | `notice_tier_limit` with `NoticeSource.DECIDE`; `RuleSet.sync_rules` and `ruleset_hash()` over the sync rules; `replay_rule_set` and `SimulatedHit`; C5 appends `HitDetail.decision` |
| 12 | Counterparty ownership and the helpers 4c needed | `counterparty` in 0012 (A2), the name index and subject columns in 0013 (B4), the screening code in C1; A4 exposes the four component hash helpers; `on_counterparty_created(s, id)`; `_customer_relationship` is A2's |
| 13 | 4a's counterparty views returned models from `monitoring/schemas.py` | `CounterpartySummary` and `RevealedCounterparty` move to `screening/counterparty/schemas.py` (A10); one `VIEW_PII` row per revealed field; C3 extends them |
| 14 | Staff-only event registration across three composition roots | 4a's `STAFF_ONLY_EVENT_TYPES` (C5 and C8 append), 4b's own `RULES_STAFF_ONLY_EVENT_TYPES` through `register_rules`, screening's tuple for C1 |
| 15 | Partition maintenance | A1's registry, not a tuple: C4 registers `txn_decision` at import; C7 adds `retire_partition` to A1's module |
| 16 | Console folders, `ACCESS` keys, nav order and the detail cards | Recorded below; B14 and B15 add the hit and capture cards to A11's transaction detail; C9 owns `features/rails/` and gates its Perfil card with `ACCESS.transactions` |
| 17 | Shared files several tasks append to | Ownership table below; first to merge creates, the others append |
| 18 | Settings and schedules | The 23 `MONITORING_*` keys split 12 (4a), 5 (4b), 6 (4c) with no overlap; schedules A9, B11, C7, C8 |
| 19 | Test support names and the December 2026 fixtures | 4a's helpers everywhere; B7 commits the 2026 unit rows that C11 reuses; 12 December 2026 is a Saturday |
| 20 | Missing or wrong cross-task edges | Table below (B12 → A10 added in round 1); the longest chain is twelve tasks |
| 21 | The library's optional-names change | Withdrawn (4c Deviation 1); no external row; C1 edits SP-03's `screen()` (ours) for `entity_type` |
| 22 | Smaller exact-name fixes | `today_mx` → `policy_date(clock.now())`, `period_key_bounds` is B11's, the agreement path, `CounterpartyReveal` gone, `profiles/declared.py` gone, the two reveal conventions, account normalization |
| 23 | The exit test activated rules after its fixtures' dates and simulated 196 days | C11 sections 1 and 2 run under a 2026-06-30 clock and simulate 1 to 29 June; B7's fixtures activate the same way |
| 24 | `TIER_LIMIT`'s `month_in` was policy-derived in B2 but declared nowhere | B3's tier files declare it statically in `UDI/LAST_DAY_PREV_MONTH`; B2 reads it by name and stays silent when the snapshot disagrees |
| 25 | A `ParkTransaction` on the decide path would be a 500 | C5 lists A6's `ParkTransaction` among the fail-mode triggers and tests it |
| 26 | C5's tier-limit boundary contradicted B2 and the spec | At the limit allows; one unit over blocks |

### 1. One migration chain, in order

- **Finding.** The spec numbers the SP-04 migrations 0011, 0012 and 0013; `main` gained `0011_screening_hit_run_index` after the spec was written. All three plans renumbered independently and agreed (4a Deviation 1, 4b and 4c Global Constraints); 4b still marked `down_revision = "0012_transactions"` as assumed.
- **Decision.** `0011_screening_hit_run_index` → `0012_transactions` (A2) → `0013_rules_captures` (B4) → `0014_decide_profiles` (C4). The three migration tasks merge in that order and each appends its revision to `CHAIN` in `tests/platform/test_migration_chain.py`. B4 is the only 0013; C4 the only 0014; no other SP-04 task adds a migration.
- **Edited.** 4a: Interfaces (Migration), Global Constraints (Migration renumbering). 4b: Global Constraints (Migration). 4c: C4 header, Parallelism.

### 2. C1 depends on B4 (and A4), not on "4b's B1"

- **Finding.** 4c wrote "4b's migration task (B1, assumed)" for the task that adds `screening_subject.counterparty_id`, the `COUNTERPARTY` branch of `ck_screening_subject_kind_input` and `counterparty_name_index`. In 4b that task is B4; B1 is the pure definitions task. 4c also said "depends on 4a" without a task.
- **Decision.** C1 depends on A4 (the `counterparty` table from A2, `resolve` and the component hashes from A4) and B4, and on nothing else of 4b. C4 depends on A10 and B4. The spec's §1.1 and §22 "counterparty screening can start after 4a, in parallel with 4b" is read as "after A4 and B4, beside B5 to B12".
- **Edited.** 4b: "From plan 4c", "Interfaces for plan 4c" (counterparty bullet), Parallelism "Across plans". 4c: "Dependencies outside this plan" (plan 4b), C1 header and Consumes, C4 header, the Parallelism table and Wave 1.

### 3. List names are resolved in 4b; 4a never sees one

- **Finding.** 4b assumed 4a's `aggregates/declarations.py` ships `resolve_list_names(where, lists) -> TxnFilter` and `ensure_declarations` taking B1's `JurisdictionLists`, and so wrote "B1 merges before 4a's declarations task"; 4a's A7 has neither, but gave `AggregateDeclaration` a `lists_hash` field "assumed by 4b". Each plan needed the other merged first.
- **Decision.** The simpler dependency: A7 is pure and knows nothing of jurisdictions; B1 depends on A7. B1 defines, in `rules/definitions.py`, `resolve_list_names(where, lists)` (a `$NAME` in `counterparty_country_in`, `counterparty_country_not_in` or `geo_admin_area_in` becomes the set's codes) and `resolve_definition(definition, lists)` (over `on`, `evidence.transactions_where` and every declaration's `where`, setting `lists_hash = lists.content_hash.hex()`, so a changed `_jurisdictions.yaml` yields new `agg_id`s, spec §8.2). B6 calls `resolve_definition` when it loads a rule set and hands resolved declarations to `ensure_declarations`; A7's `matches` only ever sees literal codes. 4a's pure contract no longer lists `platform.policy.jurisdictions`; 4b's own contract `monitoring-evaluator-is-pure` (B1) does.
- **Edited.** 4a: Interfaces (Aggregates), Global Constraints (module boundaries), Deviation 8, A3's contract note, A7 Files. 4b: "From plan 4a" (declarations row), Global Constraints (import contracts), Deviation 1, B1 header, Consumes, Produces and tests, B6 Consumes and `LoadedRule`, Parallelism Wave 0.

### 4. One processing-hook shape, owned by A8

- **Finding.** 4a's A8 offered `ProcessingContext(txn, row, store, declarations, snapshot, late)`, `Evaluator = Callable[[Session, ProcessingContext], None]` and `register_evaluator`, with 4b registering "`evaluate_rules` and `evaluate_captures` *(assumed names)*"; the job read its declarations from `agg_declaration` only (4a Deviation 9). 4b's B7 and B10 implement a `ProcessingHook` Protocol (`name`, `declarations(s, snapshot, *, on, product)`, `evaluate(s, txn, ctx)`) registered with `register_processing_hook`, with a context carrying `units`, `holidays` and `queue` (B10's fee conversion and accumulation need the first two).
- **Decision.** A8 builds 4b's shape: `ProcessingContext(txn, row, store, declarations, snapshot: SnapshotRef, units, holidays, late, queue)`, `ProcessingHook`, `register_processing_hook`, `processing_hooks`, `reset_processing_hooks`. The job's declarations for a row are `load_declarations(s)` plus, the first time a snapshot id is met in a drain, every hook's `declarations(...)`, with `ensure_declarations` for the ones `agg_declaration` lacks (so a capture's `ACCUM` accumulator gets its row and backfill the first time a snapshot is seen; a rule's are already there from B6's activation). Hooks run in registration order inside the row's transaction after `store.add`. `RulesHook` (B7) and `CapturesHook` (B10) are the two hooks; `register_rules` (B7) registers them.
- **Edited.** 4a: Architecture, Interfaces (Processing hook, Aggregates), Review Focus 2, File structure, A8 title, Produces, drain description, tests, step 3 and commit, A9's backfill comment, the task table. 4b: "From plan 4a" (hooks row), B10 `capture_transaction` signature and `CapturesHook.evaluate`.

### 5. The names 4b took from 4a's pure modules

- **Finding.** 4b wrote `KeyKind` in `monitoring/enums.py` (4a: `monitoring/txn/schema.py`), `matches_filter(txn, where)` in `aggregates/windows.py` (4a: `matches(where, txn)` in `declarations.py`), `AggregateValue` in `windows.py` (4a: `store.py`), an `aggregate_key(txn, kind)` helper (4a has `EnrichedTransaction.key_for(kind)` and `AggregateKey(kind, key)`), `enriched_from_row(s, row)` in `enrich/rows.py` (4a: `enriched_from_row(row)` in `enrich/service.py`), `mx_date` and `period_key_bounds` in `enrich/periods.py` (4a: `policy_date` in `enrich/policy.py`; no bounds helper), `period_keys["MONTH"]` (4a: a `PeriodKeys` model with attributes), `device_hash` (4a: `device_key`, hex), `monitoring.process.job.PROCESS_KIND` (4a: `monitoring/kinds.py`), `ensure_declarations -> int` (4a: `-> list[str]`).
- **Decision.** 4a's names everywhere. A7 adds `aggregates/store.py` to the pure contract (dataclasses and a Protocol) so B2 may import `AggregateKey` and `AggregateValue`. `period_key_bounds(period_key) -> tuple[date, date]` is defined by B11 in `captures/periods.py`.
- **Edited.** 4a: Global Constraints (module boundaries), A3's contract note, A7 Files. 4b: the whole "From plan 4a" table, Global Constraints (Dates), Deviation 1, B1, B2, B7, B8, B10, B11 Consumes and the pseudo-code that used the old names (`applies`, `Firing.key`, `reader_for`, `dedup_bucket`, `RulesHook.evaluate`, `event_fields`, `open_group_for`, `open_period`).

### 6. Decision linkage: C5's function in A4's `ingest_batch`

- **Finding.** 4a's Deviation 4 left spec §6.1 step 3 to 4c as "`link_decisions(session, report)` *(assumed)*"; 4c planned `link_decisions(s, rows: Sequence[tuple[UUID, datetime, str]]) -> int` in `monitoring/decide/linkage.py` and assumed `ingest_batch(s, items, *, source, submitted_by)`.
- **Decision.** 4c's function and signature. C5 edits A4's `txn/ingest.py` so that, after the row inserts and before the processing enqueue, it calls `link_decisions(session, rows)` with the `(id, occurred_at, external_id)` triples of the rows the batch created (duplicates excluded), in the same transaction. `ingest_batch`'s signature is A4's: `(session, items, *, source, submitted_by, queue, file_id=None, request_id=None)`.
- **Edited.** 4a: Interfaces (Ingest), Deviation 4. 4c: "Dependencies outside this plan" (plan 4a), C5 header, Files and the linkage paragraph.

### 7. No opt-in webhooks in SP-04

- **Finding.** Spec §14 makes `monitoring.transaction_processed.v1` and `monitoring.decision_issued.v1` tenant-visible but "opt-in, off by default per endpoint". `xolotl.platform.events.registry` on `main` has `register_staff_only`, `is_staff_only` and `require_deliverable` and nothing per endpoint. 4a's Deviation 7 registered `transaction_processed` staff-only and raised Open question 2; 4c assumed `register_opt_in`/`is_opt_in` from 4a and would have added them to the platform in C5 otherwise.
- **Decision.** The MVP answer: no opt-in mechanism. Both events are published (in-process consumers and the outbox see them) and registered staff-only, so a wildcard endpoint never receives them and an endpoint naming them is refused with `EVENT_TYPE_NOT_DELIVERABLE`. C5 appends `DECISION_ISSUED` to 4a's `STAFF_ONLY_EVENT_TYPES`; its test becomes `test_decision_issued_never_reaches_a_webhook`. The opt-in delivery is last (eighth) on the spec's cut list (§23) and is listed for the product owner below.
- **Round 1.** The cut-list position was wrongly "first" in 4a Deviation 7, 4a Open question 2 and here; corrected to last.
- **Edited.** 4a: Interfaces (Events), Deviation 7. 4c: Architecture, Global Constraints (Events), "Dependencies outside this plan" (plan 4a), Review Focus 3, C5 Files and tests, Exit criteria.

### 8. USD handling is one rule across 4a and 4b

- **Finding.** `UnitService.to_unit` and `threshold_in_ccy` on `main` convert from pesos only (`_check_currency`) and the FIX series has no weekend value. 4a's Deviation 6 accepts `MXN` and `USD`, brings a USD amount to pesos at the prior banking day's FIX (`unit_amounts["MXN/PRIOR_BANKING_DAY"]`) and converts every other unit from that; 4b's Deviation 8 compares captures in unit space (`unit_amounts[unit_key].amount >= threshold.amount`) and converted fees with `to_unit(fee_minor / 100, currency, …)`, which would fail for a USD fee.
- **Decision.** The two deviations are one rule, written in both: amounts compare in the threshold's unit through 4a's `unit_amounts`; the peso leg of a USD transaction is the prior banking day's FIX; a `FEE_THRESHOLD` capture converts its fee through 4a's `unit_amounts_for(units, amount=…, currency=txn.currency, on=…, pairs={(unit, rule)}, holidays=…)` (A6 exposes it), so a USD fee follows the same rule; `unit_values.converted` records the peso threshold for the record. The currency rule is 4a's Open question 1 for the product owner.
- **Edited.** 4a: Interfaces (Enrichment). 4b: "From plan 4a" (enrichment row), Deviation 8, B10 Consumes and `capture_transaction`.

### 9. Seeding the memory store on the decide path

- **Finding.** 4c's C5 wrote `store.seed(key, declaration, PostgresAggregateStore(s).read(...))` and "assumed 4a method, else C5 adds it". 4a's A7 has `MemoryAggregateStore.seed(key, declaration, buckets: Iterable[BucketRow])` and `PostgresAggregateStore.buckets(key, declaration, *, start, end) -> list[BucketRow]` (the `ix_agg_bucket_window` range read), marked "*(assumed use)*".
- **Decision.** C5 seeds with bucket rows: for each declaration the sync rules read, `AggregateKey(d.key_kind, enriched.key_for(d.key_kind))`, the window bounds ending now (`window_bounds`, `period_bucket_start` or the open accumulator), `seed(key, d, PostgresAggregateStore(s).buckets(...))`, then `add()` the candidate. A declaration whose `agg_declaration.backfilled_through` is NULL, or that has no row yet (`load_declarations`), is read as `INCOMPLETE`, as processing does (spec §8.4).
- **Round 1.** The mechanism was unnamed (the memory store's `complete` was always true). A7's `MemoryAggregateStore` gains `incomplete_ids: Collection[str] = ()`, the mirror of `PostgresAggregateStore(complete_ids=...)`: a read of a declaration whose `agg_id` is in it has `complete = False`, and B7's `reader_for` turns that into `INCOMPLETE`. C5 builds the set from `load_declarations(s)`.
- **Round 2.** A7 adds `backfilled_ids(session) -> frozenset[str]` (`aggregates/postgres.py`): C5 passes `incomplete_ids = {d.agg_id() for d in declarations} - backfilled_ids(s)` and A8 `complete_ids=backfilled_ids(s)` once per drain.
- **Edited.** 4a: Interfaces (Aggregates), A7 `seed` comment. 4b: "Interfaces for plan 4c" (evaluator bullet). 4c: C5 step 4.

### 10. The 4a pieces the decide path needs

- **Finding.** 4c assumed `txn/resolve.py` with `resolve_customer`/`resolve_account`, `enrich/profile.py::profile_context`, `enrich_input(s, item, resolved, *, units, holidays, policy)` and a `tests/platform/policy_support.py::assign_product`. 4a has `relationship_by_external_ref` (party service), `account_for` (an upsert), `enrich(session, row, …)` over a `Transaction` row only, and no profile context (the profile the evaluator reads is 4b's `profile_for`).
- **Decision.** A6 exposes `enrich_input(session, item, *, relationship, account_id, units, holidays, snapshot, now) -> EnrichedTransaction`, the same steps over a validated input with no row (the counterparty's key and status through `dedup_hash_of` and `status_by_dedup_hash`, no upsert), and `enrich(row, …)` is that function over the row's stored input. A4 adds `find_account(session, relationship_id, account_ref) -> Account | None`, a read, so a decision never creates an account. C5 validates with A3's `parse_item`, resolves with `relationship_by_external_ref`, and reads the profile with B7's `profile_for(s, enriched)`; C8 makes `profile_for` read `declared_profile` instead of extending a 4a context. `both_packs_tenant` (A2) replaces `assign_product`.
- **Round 1.** `find_account` may return None (an `account_ref` never seen), so A3's `EnrichedTransaction.account_id` and A6's `enrich_input(..., account_id)` are `UUID | None`; `key_for(ACCOUNT)` is None then and ACCOUNT-keyed aggregates read empty on the decide path. The `transaction` row's `account_id` stays NOT NULL: ingest always resolves or creates the account.
- **Edited.** 4a: Interfaces (Enrichment, Counterparty), A4 Produces (`find_account`), A6 Produces and tests. 4c: "Dependencies outside this plan" (plan 4a), C5 header and steps 1 to 3, C8 header, Files and the profile paragraph, C11 section 1.

### 11. The 4b names 4c took

- **Finding.** 4c assumed `record_tier_limit_notice(s, *, relationship_id, limit_code, period_key, source)`, `RuleSet.versions`/`sync_versions`, `Firing(rule_version, …)`, `simulate(s, *, definitions, …) -> SimulationOutcome` with `hit_keys` and `hits_per_rule`, and a `HitDetail.decision` field 4b "leaves None".
- **Decision.** 4b's names: `notice_tier_limit(s, *, relationship_id, account_id, tier, limit_code, period_key, outcome, source: NoticeSource)` with `NoticeSource.DECIDE` and `LIMIT_CODE_MONTHLY_OPS` (B7); `RuleSet.rules`, `RuleSet.sync_rules`, `RuleSet.lists`, `RuleSet.declarations` (B6); `Firing(rule_code, key_kind, key, window_start, window_end, severity, outcome, aggregates, params, explanation)` and `evaluate(definition, ctx)` (B2), which C5 runs per sync rule; `replay_rule_set(s, ruleset, *, range_start, range_end, page) -> Iterator[SimulatedHit]` (B8), over which C6's `replay_range` compares `(rule_version_id, dedup_key)` with `rule_hit` per product of `assignments_on`. `RuleSet.ruleset_hash()` hashes the **sync** rules' definition hashes (what `txn_decision.ruleset_hash` records). B12's `HitSummary.decision_id` is the trigger transaction's `transaction.decision_id`; C5 appends `decision: DecisionSummary | None` to `HitDetail` in `monitoring/schemas.py` and fills it in `routes_hits.py`; B14 renders it.
- **Edited.** 4b: B6 `ruleset_hash` and its test, "Interfaces for plan 4c" (evaluator, replay, hits-on-the-decision bullets). 4c: "Dependencies outside this plan" (plan 4b), C5 header and steps 5 and 6, C6 header and `replay_range`.

### 12. Counterparty ownership and the helpers 4c needed

- **Finding.** The `counterparty` table is in 0012 (A2, `screening/counterparty/models.py`), the name index and the `screening_subject` columns in 0013 (B4 appends `CounterpartyNameIndex` to A2's file and edits `engine/models.py`), the subjects, queries, first-sight job and status cache in C1. 4b said B4 adds the contract `screening-never-imports-monitoring` and "`_customer_relationship` when 4a has not"; 4a's A2 adds the contract and builds `_account` on an unnamed relationship. 4c assumed the blind-index helpers `name_hash_of`, `wallet_hash_of`, `identifier_hash_of` (4a had only `component_hashes` and `dedup_hash_of`), `on_counterparty_created(s, row)` with a row 4a's `INSERT … RETURNING` never materialises, and `status_by_dedup_hash -> str`.
- **Decision.** A2 defines `_customer_relationship(session, tenant_id)` and builds `_account` on it; B4 imports it. A2 owns `screening-never-imports-monitoring`; B4 adds no contract. A4 exposes `name_hash_of(session, name, *, entity_type)`, `account_hash_of`, `wallet_hash_of(session, address)` and `identifier_hash_of(session, id_type, value)`, which `component_hashes` composes; C2 looks counterparties up by the last two. C1 makes `resolve` call `on_counterparty_created(session, ref.id)` on its `created=True` branch (C1 loads the row, indexes the name and enqueues the first-sight job; 4c Deviation 5's process-level queue stands). `status_by_dedup_hash` returns `CounterpartyScreeningStatus`.
- **Edited.** 4a: Interfaces (Counterparty, Test support), A2 factories, A4 Produces, Parallelism shared files. 4b: "From plan 4a" (migration and factories rows), B4 header, Files, contract line and factories. 4c: Global Constraints (Import rules), "Dependencies outside this plan" (plan 4a), Deviation 5, C1 header, Files, Consumes and `on_counterparty_created`, C2 Consumes.

### 13. The counterparty views and their schemas

- **Finding.** 4a's A10 defined `CounterpartySummary` and `RevealedCounterparty` in `monitoring/schemas.py` and returned them from `screening/counterparty/views.py`, which would make screening import monitoring, against 4a's own contract. 4a's reveal recorded one `VIEW_PII` with field `identity`; 4c's C3 planned its own `counterparty/api.py` with a second `reveal_counterparty` returning `CounterpartyReveal` and one `VIEW_PII` per field, and a richer masked list row.
- **Decision.** A10 creates `screening/counterparty/schemas.py` with `CounterpartySummary` and `RevealedCounterparty` (`name`, `account`, `wallet_address`, `wallet_network`, `identifier_type`, `identifier`); `monitoring/schemas.py` imports them. `reveal_counterparty` records one `VIEW_PII` row per non-null field, before any decryption (the party precedent); A10's test is `test_reveal_audits_each_field_then_decrypts`. C3 appends `CounterpartyResponse(CounterpartySummary)` (initials, masked tails, `screened_at`, `subject_id`, the seen timestamps) and `CounterpartyDetail` to that file, builds them in `counterparty/api.py` (`counterparty_row`, `counterparty_detail`) and reuses A10's reveal through `reveal_counterparty_endpoint`. C3 depends on A10.
- **Round 1.** 4c's File structure still listed `counterparty_summary` and `reveal_counterparty` under C3's `api.py`, and `CounterpartyResponse` redeclared the base's fields; both corrected. B12 consumes the same A10 views and gains the edge (finding 20).
- **Edited.** 4a: Interfaces (Counterparty), File structure, A10 Files, schemas block, `reveal_counterparty`, tests. 4b: "From plan 4a" (reveal row), B12 Consumes. 4c: File structure, C3 header, Files, Produces and routes, the Parallelism table.

### 14. Staff-only registration across three composition roots

- **Finding.** 4a registers its `STAFF_ONLY_EVENT_TYPES` in `register_monitoring` (A5) and said "4b and 4c append theirs"; 4b's B4 defined `RULES_STAFF_ONLY_EVENT_TYPES` "appended to 4a's tuple" while B7's `register_rules` registers them itself; 4c put `COUNTERPARTY_STATUS_CHANGED` in screening's tuple and assumed 4a's `register_monitoring` for its two monitoring events.
- **Decision.** Three tuples, three roots: 4a's `STAFF_ONLY_EVENT_TYPES` (A4; C5 appends `DECISION_ISSUED`, C8 `BEHAVIOUR_PROFILE_UPDATED`; A5's `register_monitoring` registers it), 4b's `RULES_STAFF_ONLY_EVENT_TYPES` (B4, a separate tuple in the same file; B7's `register_rules` registers it, which is what B7's `test_register_rules_marks_every_staff_only_type` pins), screening's `STAFF_ONLY_EVENT_TYPES` (C1 appends `COUNTERPARTY_STATUS_CHANGED`; `register_screening` registers it, as on `main`). `TIER_LIMIT_REACHED` is B4's constant and is webhook-safe. `wire_platform` order: `register_screening`, `register_rescreen`, `register_monitoring`, `register_rules`.
- **Edited.** 4a: Interfaces (Events, Composition), A5 composition comment. 4b: Global Constraints (Events), B4 events block, "Interfaces for plan 4c" (composition bullet). 4c: Global Constraints (Events), C5 Files.

### 15. Partition maintenance is a registry

- **Finding.** 4c assumed "a module tuple `PARTITIONED_TABLES` that C4 extends with `"txn_decision"`" and `harden_partition(conn, name)`. 4a's A1 generalises `audit/partitions.py` into `xolotl.platform.partitions` with `PartitionedTable`, `register_partitioned_table` (called at import by `audit/partitions.py` and `monitoring/txn/models.py`), `ensure_all_partitions`, and `harden_partition(conn, name, *, truncate_guard=None)`; A9's daily job and `migrate.upgrade()` cover every registered table.
- **Decision.** C4's `decide/models.py` calls `register_partitioned_table(PartitionedTable("txn_decision", "decided_at", None))` at import and touches no job; 0014 creates the default partition and three months ahead through `ensure_partitions(conn, table="txn_decision", …)`. C7 adds `retire_partition(conn, *, table, month)` to A1's module.
- **Edited.** 4a: Interfaces (Platform). 4c: Global Constraints (Tenant tables), "Dependencies outside this plan" (plan 4a), File structure, C4 Files and models, C7 Files.

### 16. Console folders, `ACCESS` keys, the nav order and the detail cards

- **Finding.** 4a adds `features/transactions/`, `ACCESS.transactions` and `ACCESS.backfill`; 4b adds `features/rules/`, `features/hits/`, `features/captures/`, `ACCESS.rules`, `rulesWrite`, `rulesApprove`, `monitoring` ("reused when 4a defined it": 4a does not), `capturesClose`; 4c adds `features/counterparties/` (C3) and put `RailsPage` in "4b's `features/monitoring/`", which 4b does not create, gating its Perfil card with 4b's `ACCESS.monitoring`. 4b's dependency table expected the transaction detail's "hits and captures on this transaction" cards (spec §17) to read its endpoints, but no task built them.
- **Decision.** Folders: `transactions` (A11, A12), `rules` (B13), `hits` (B14), `captures` (B15), `counterparties` (C3), `rails` (C9, its own). Keys: `transactions`, `backfill` (A11); `rules`, `rulesWrite`, `rulesApprove` (B13); `monitoring` (B14); `capturesClose` (B15); `rails` (C9); C3 reuses `screening` and `screeningCheck`; C9 gates the Perfil card with `ACCESS.transactions` (the same five roles) so it does not wait for B14. Nav, flat (SP-03a Deviation 11): `nav.parties`, `nav.transactions` (A11), `nav.matches`, `nav.screeningCheck`, `nav.counterparties` (C3), `nav.apiKeys`, `nav.staff`, `nav.audit`, `nav.policy`, `nav.lists`, `nav.privateLists`, `nav.ruleHits` (B14), `nav.monitoringRules` (B13), `nav.captures` (B15), `nav.screeningEvaluation`, `nav.units`, `nav.calendar`, `nav.connectors`, `nav.rails` (C9), `nav.security`. B14 adds `TransactionHitsCard` to A11's `TransactionPage.tsx` and `PartyHitsCard` to `PartyPage.tsx` (`useTransactionHits`, `usePartyHits`; B12's `list_hits` gains a `transaction_id` filter); B15 adds `TransactionCapturesCard` to `TransactionPage.tsx` (`useTransactionCaptures`; B11's `list_captures` gains `transaction_id`). B14 and B15 therefore depend on A11.
- **Edited.** 4a: Interfaces (Console). 4b: "From plan 4a" (console row), File structure, B11 and B12 route filters, B14 and B15 headers, Files and Produces, "Interfaces for plan 4c" (console bullet), the two task tables and Wave 4. 4c: File structure, C9 Files and the Parallelism table.

### 17. Shared files: who creates each

The rule is the SP-02 one: edits are additive, the second task to merge rebases and re-applies its own lines, `console/src/api/schema.d.ts` is regenerated and never merged by hand. Where two plans may each create a file, the first to merge creates it with the content both name, and the other appends.

| File | Created by | Appended by |
|---|---|---|
| `src/xolotl/platform/partitions.py` | A1 | C7 (`retire_partition`) |
| `src/xolotl/migrations/versions/0012_transactions.py`, `0013_rules_captures.py`, `0014_decide_profiles.py` | A2, B4, C4 (in that merge order) | none |
| `src/xolotl/modules/monitoring/__init__.py`, `txn/__init__.py`, `txn/schema.py`; `src/xolotl/modules/screening/counterparty/__init__.py`, `schema.py` | A3 | none |
| `src/xolotl/modules/monitoring/enums.py` | A2 | C4 (`FailMode`, `DecisionOutcome`, `DecisionMode` live in `decide/enums.py`; nothing appended) |
| `src/xolotl/modules/screening/counterparty/models.py` | A2 | B4 (`CounterpartyNameIndex`) |
| `src/xolotl/modules/screening/counterparty/resolve.py` | A4 | A6 (`status_of`), C1 (the `on_counterparty_created` call) |
| `src/xolotl/modules/screening/counterparty/schemas.py`, `views.py` | A10 | C3 (`CounterpartyResponse`, `CounterpartyDetail`) |
| `src/xolotl/modules/screening/engine/{enums,screen,subjects,models}.py`, `screening/{events,composition,api,schemas}.py`, `rescreen/{list_delta,walkers,blocking}.py` | SP-03 (on `main`) | B4 (`engine/models.py`), C1 (`engine/enums.py`, `engine/screen.py`, `engine/subjects.py`, `events.py`, `composition.py`), C2 (`rescreen/*`), C3 (`api.py`, `schemas.py`) |
| `src/xolotl/modules/monitoring/kinds.py` | A4 | none (4b's and 4c's kinds live in the modules that handle them) |
| `src/xolotl/modules/monitoring/errors.py` | A4 | A5, A9; B5, B6, B8, B11; C4 (`RailUnknownError`), C6 (`PeriodInvalidError`) |
| `src/xolotl/modules/monitoring/events.py` | A4 | B4 (its constants and `RULES_STAFF_ONLY_EVENT_TYPES`), C5 (`DECISION_ISSUED`), C8 (`BEHAVIOUR_PROFILE_UPDATED`), both appending to `STAFF_ONLY_EVENT_TYPES` |
| `src/xolotl/modules/monitoring/composition.py` | A5 | A8, A9; C5, C7, C8 (job handlers); 4b registers in its own `rules/wiring.py` |
| `src/xolotl/modules/monitoring/process/hooks.py` | A8 | none (B7 and B10 register hooks from `register_rules`) |
| `src/xolotl/modules/monitoring/schemas.py` | A10 | B9, B11, B12; C4, C5 (`HitDetail.decision` too), C6, C8 |
| `src/xolotl/modules/monitoring/routes_monitoring.py` | A10 | C6 (`GET /metrics`), C8 (`GET /profiles/{relationship_id}`) |
| `src/xolotl/modules/monitoring/routes_hits.py` | B12 | C5 (fills `HitDetail.decision`) |
| `src/xolotl/modules/monitoring/rules/wiring.py` | B7 | B8, B10, B11 |
| `src/xolotl/modules/monitoring/rules/evaluate.py` | B7 | C8 (`profile_for` reads `declared_profile`) |
| `src/xolotl/modules/monitoring/txn/ingest.py` | A4 | C5 (`link_decisions`) |
| `src/xolotl/modules/monitoring/captures/{events,supersede}.py` | B10 | B11 (replaces the minimal `open_period`/`reopen_period` by imports) |
| `src/xolotl/app.py` (`wire_platform`, routers) | — | A5 (`register_monitoring`), A10 (two routers); B7 (`register_rules`), B9, B11, B12 (routers); C3 (`routes_counterparties`), C4 (`routes_decide`, before `routes_transactions`) |
| `src/xolotl/cli.py` | — | A5 (`monitoring_app`: `ingest-file`), A8 (`process`), A9 (`sweep`, `backfill`, `partitions`), A10 (`show`); B3 (`rules_app`: `validate`), B5 (`load`, `show`, `seed-tenant`, `db upgrade` and `serve`), B8 (`simulate`, `activate`), B11 (`monitoring period-close`); C1 (`screening screen-counterparties`), C6 (`monitoring replay`), C8 (`monitoring profiles`) |
| `src/xolotl/platform/config.py`, `.env.example`, README configuration table | — | A2 (twelve keys); B5, B6, B8, B11 (five); C1, C4, C7, C8 (six) |
| `src/xolotl/models.py` | — | A2, B4, C4 |
| `.importlinter` | — | A3 (`monitoring-core-is-pure`), A2 (`monitoring-boundaries`, `screening-never-imports-monitoring`), A7 (the pure set), B1 (`monitoring-evaluator-is-pure`) |
| `src/xolotl/platform/tenancy/registry.py` (`SHARED_TABLES`) | — | B4 (`rule_template`, `rule_template_version`); nothing from 4a or 4c |
| `src/xolotl/modules/tenants/service.py` and the pinned schedule tests (`tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`) | — | A9 (`monitoring.sweep`, `monitoring.partitions`), B11 (`monitoring.period_close`), C7 (`monitoring.retention`), C8 (`monitoring.profiles`), rows in name order |
| `src/xolotl/modules/party/service.py` | — | A4 (`RelationshipRef`, `relationship_by_external_ref`, `relationship_ref`) |
| `src/xolotl/modules/party/{schemas,customer,routes_relationships,models}.py` | — | C4 (`CustomerRelationship.declared_profile`), C8 (`DeclaredProfile`, `set_declared_profile`, `PATCH /relationships/{id}`) |
| `tests/isolation/factories.py` | — | A2 (`_customer_relationship`, `_account`, `_counterparty`, `_transaction_batch`, `_transaction` and ten factories), B4 (eleven), C4 (three) |
| `tests/platform/test_migration_chain.py`, `test_schema_sync.py` (`NATURALLY_KEYED`), `tests/isolation/test_structural.py` | — | A1, A2; B4; C4 |
| `tests/platform/test_errors.py`, `test_config.py`, `test_worker.py`, `test_partitions.py`, `tests/test_cli.py` | — | the task that adds the error, setting, handler, schedule, partition helper or command |
| `tests/modules/monitoring/support.py`, `conftest.py` | A2 (`itf_tenant`, `both_packs_tenant`), A4 (`conftest.py`, `make_item`, `ingest_items`) | A7 (`declare`, `stream`), A8 (`run_processing`) |
| `tests/modules/monitoring/fixtures/unit_values_2026.yaml` | B7 | none (B10 goldens carry their own `units.yaml`; C11 reuses B7's file) |
| `tests/modules/monitoring/route_support.py` | B9 (B12 creates it if B9 has not merged) | B11, B12 |
| `tests/modules/screening/counterparty/__init__.py` | A3 | A4, A10 (tests), C1 (`conftest.py`), C2, C3 |
| `tests/perf/conftest.py` (`record` gains `file`) | SP-03c (on `main`) | C10 |
| `tests/e2e/conftest.py` | SP-03c | none expected (C11 reuses `drain`) |
| Console `auth/access.ts`, `components/layout/nav.ts`, `App.tsx`, both catalogues, `catalogue.test.ts` | — | A11 (`transactions`, `backfill`, `nav.transactions`), A12, B13 (`rules*`, `nav.monitoringRules`), B14 (`monitoring`, `nav.ruleHits`), B15 (`capturesClose`, `nav.captures`), C3 (`nav.counterparties`), C9 (`rails`, `nav.rails`) |
| `console/src/features/transactions/queries.ts`, `TransactionsPage.tsx`, `TransactionPage.tsx` | A11 (A12 creates `queries.ts` if A11 has not merged) | A12 (tabs, panels, queries); B14 (`TransactionHitsCard`), B15 (`TransactionCapturesCard`) on `TransactionPage.tsx` |
| `console/src/features/parties/PartyPage.tsx`, `queries.ts` | SP-02c | A12 (`PartyTransactionsCard`), B14 (`PartyHitsCard`), C9 (`BehaviourProfileCard`, the two queries) |
| `console/src/features/screening/{MatchPage,MatchesPage}.tsx` | SP-03b | C3 |
| `console/e2e/smoke.spec.ts` | SP-01d | B13 |
| `policy-packs/_jurisdictions.yaml` | SP-02 | B1 (`tax_haven`, `regions`) |

- **Edited.** 4a: Parallelism "Shared files". 4b: File structure, Parallelism "Across plans". 4c: File structure, Parallelism "Shared files across SP-04 plans".
- **Round 1.** 4c's File structure and shared-files list said `monitoring/enums.py` is appended by 4c; it is not (C4's enums live in `decide/enums.py`), and both now say so. B5, B9 and B11 append to A4's `errors.py` and A10's `schemas.py` and `app.py` without an edge to A4 or A10; their headers now say the wave order (A4 and A10 merge waves earlier) and this table's first-to-merge rule cover it. C2's Files gained `rescreen/blocking.py` (the `table` parameter).

### 18. Settings and schedules split cleanly

- **Finding.** Spec §3 lists 23 `MONITORING_*` keys. 4a claims twelve (`BATCH_MAX_ITEMS`, `FILE_MAX_MB`, `FILE_MAX_ROWS`, `FILE_CHUNK_ROWS`, `LATE_DAYS`, `MAX_FUTURE_MINUTES`, `PROCESS_LEASE_SECONDS`, `PROCESS_PAGE`, `SWEEP_CRON`, `AGG_BACKFILL_BATCH`, `PARTITIONS_CRON`, `PARTITION_MONTHS_AHEAD`), 4b five (`ALLOW_CUSTOM_RULES`, `RULE_CACHE_SECONDS`, `SIMULATION_MAX_AGE_DAYS`, `SIMULATION_MAX_DAYS`, `PERIOD_CLOSE_CRON`), 4c six (`COUNTERPARTY_SCREEN_BATCH`, `DECIDE_BUDGET_MS`, `DECIDE_FAIL_MODE`, `RETENTION_CRON`, `PROFILE_CRON`, `PROFILE_DEVIATION_RATIO`). 4c hedged "if 4a or 4b already added one". Schedules: A9 `monitoring.sweep` and `monitoring.partitions`, B11 `monitoring.period_close`, C7 `monitoring.retention`, C8 `monitoring.profiles`, the pinned rows inserted in name order.
- **Decision.** The split is complete and disjoint; the hedge is removed. The kinds of 4b's and 4c's jobs live in the modules that handle them (`SIMULATE_KIND`, `PERIOD_CLOSE_KIND`, `FIRST_SIGHT_KIND`, `RETENTION_KIND`, `PROFILES_KIND`); 4a's six stay in `monitoring/kinds.py`.
- **Edited.** 4c: Global Constraints (Settings, Schedules).

### 19. Test support names and the December 2026 fixtures

- **Finding.** 4b assumed `ingest_fixtures`, `process_party(engine, tenant_id, party_id)` and `seed_unit_values(admin_engine, rows)`; 4c assumed `ingest`, `process_all`, `make_txn` and `assign_product`. 4a's `support.py` has `itf_tenant`, `both_packs_tenant`, `make_item`, `ingest_items`, `run_processing`, `declare`, `stream`; `main` has `tests/platform/units_support.py::load_unit_values(session, path)`. The exit demo and 4b's goldens sit in the week of 7 to 13 December 2026; the seed files on `main` cover December 2025 to January 2026 and `policy-packs/_holidays/MX.yaml` lists no Banxico holiday on 12 December 2026, which is a Saturday (4a Open question 3; 4c Deviation 14).
- **Decision.** 4a's names everywhere. B7 commits `tests/modules/monitoring/fixtures/unit_values_2026.yaml` (UDI, UMA and `USD_MXN_FIX` for July to December 2026, with the 11 December FIX and no row for 12 and 13 December) and loads it with `load_unit_values`; B10's capture goldens keep their own per-code `units.yaml`; C11 reuses B7's file and commits no `unit_values.yaml`. The seed files on `main` are not changed by SP-04: the demo's "prior banking day across the 12 December holiday" crosses a weekend, and 4a's goldens use the seeded window. Whether the demo should instead show a weekday holiday is for the product owner.
- **Edited.** 4a: Interfaces (Test support), Open question 3. 4b: "From plan 4a" (test-support row). 4c: "Dependencies outside this plan" (plan 4a), C5 conftest, C10, C11 Files and section 1, File structure.
- **Round 1.** B7's Step 1 and B10's goldens still used `seed_unit_values`, `ingest_fixtures` and `process_party`, and `reevaluate_later` still read `enriched_from_row(s, row)`; all now use `load_unit_values`, `ingest_items`, `run_processing` and `enriched_from_row(row)`. B7's Files now create `tests/modules/monitoring/fixtures/unit_values_2026.yaml`. 4b's three mentions of a "12 December holiday" now say the weekend of 12 and 13 December.

### 20. Cross-task edges made explicit

| Task | Edge added or changed | Why |
|---|---|---|
| B1 | A7 (was "4a's declarations.py, assumed") | `TxnFilter`, `AggregateDeclaration`, `AggregateKey`; finding 3 removes the reverse edge |
| B2 | B1 only (A7 comes with it) | `AggregateValue`, `matches` |
| B4 | A2 | the migration, the models, the factory helpers |
| B7 | B2, B6, A8 | the hook shape, `run_processing` |
| B12 | + A10 (round 1) | `reveal_counterparty`, `counterparty_summary`, `monitoring/schemas.py` |
| B14, B15 | + A11 | the transaction detail they extend (finding 16) |
| C1 | A4, B4 | finding 2 |
| C3 | C1, A10 | the counterparty views and schemas (finding 13) |
| C4 | A10, B4 | the complete `monitoring/` package to append to; the chain |
| C5 | C4, B7, B12 | the rules, the notice, the hit detail it extends |
| C6 | C4, B8, B11 | `replay_rule_set`; the period rows with `reconciled_count` |
| C7 | C4, C1 | B4 and A1 come through C4 |
| C8 | C4, B7 | `profile_for` |
| C9 | C4, C8, A11 | `ACCESS.transactions` and the console transactions kit |
| C10 | C1, C5 | A8 and B7 come through C5 |
| C11 | C1 to C10, A12, B13, B14, B15 | merged last: every SP-04 task |

The longest chain is twelve tasks: A1 → A2 → A7 → B1 → B3 → B5 → B6 → B7 → B10 → B11 → C6 → C11 (B1 now waits for A7, and C6 for B11's period rows). The first wave is A1 and A3; `docs/superpowers/execution/sp04/check-graph.mjs` prints the twelve waves.

- **Edited.** The task and Parallelism tables of all three plans.

### 21. The library change is withdrawn; `screen()` gains one option

- **Finding.** Spec §11.4 and §22 asked 4b to agree a library change with its owner (optional `NameQuery.names`); 4b did not plan it, and 4c's Deviation 1 shows `main` already builds `NameQuery(names=())` for a purged party, returns identifier hits first and scores them alone at `ID_MATCH_FLOOR`.
- **Decision.** No library change and no `external` row in `tasks.json`; C1 pins the behaviour with `test_a_wallet_only_counterparty_alerts_on_an_exact_ofac_address` and reports `BLOCKED` to the library owner only if a library bump ever breaks it. C1's `ScreenOptions.entity_type` and the `COUNTERPARTY` branch of `subject_query` edit SP-03b's `engine/screen.py`, which is ours (the kit's agreement says so).
- **Edited.** None beyond what 4c already said; recorded here and in the kit.

### 22. Smaller exact-name fixes

- 4c's "today" was `today_mx()` from `screening.engine.policy`; monitoring uses A6's `policy_date(clock.now())` (Global Constraints, C8's `handle_profiles`).
- `period_key_bounds` is B11's (`captures/periods.py`), not a 4a helper.
- 4c's `profiles/declared.py` is gone: the Pydantic `DeclaredProfile` lives in `party/schemas.py` (C8); B2's `DeclaredProfile` dataclass is the evaluator's, and `profile_for` maps one to the other.
- 4c's `CounterpartyReveal` is A10's `RevealedCounterparty`; its route handler is `reveal_counterparty_endpoint`.
- 4b's `register_rules` registers no contract; `.importlinter` lines moved to A2 and B1.
- The three plan headers point at `docs/superpowers/execution/sp04/agreement.md` and this file.
- **Round 1.** Two reveal conventions coexist on purpose: A10 keeps `?reveal=true` on `GET /v1/transactions/{id}` as spec §15 writes it (one detail, one reveal, audited each call), while 4b Deviation 7 (hits) and C3 (counterparties) use a separate `GET /{id}/reveal`, the party and screening precedent, so a cached detail never records `VIEW_PII`. Both are intended. 4a gains Deviation 17: a counterparty account is blind-indexed as sent and stripped, since `main`'s `normalize_identifier(value)` has no account type argument. C11's section 9 named `MX_ITF_INTL_TRANSFER` where the capture code is `ITF_INTL_TRANSFER`.

### 23. The exit test must activate its rules before its fixtures' dates

- **Finding.** C11 patched the clock to 2026-12-14 and activated the three rules with `rules activate`, which (B8) calls `activate_version(effective_from=today)`; B6 refuses a past `effective_from`, and `active_rules(on=policy_date(txn.occurred_at))` sees only assignments effective on the transaction's date, so the 7 to 13 December deposits and the July to November AVI batch would have been evaluated with no active rule and `CASH_STRUCTURING` would never fire in section 6. The same command's `--simulate-from 2026-06-01 --simulate-to 2026-12-13` is 196 days, over `MONITORING_SIMULATION_MAX_DAYS` (190). B7's test fixtures had the same gap ("activated through B6 with a simulation row inserted directly" at an unstated date).
- **Decision.** The clock, not a B6 exception: C11 runs sections 1 and 2 under 2026-06-30T15:00Z, simulating 2026-06-01 to 2026-06-29 (29 days; an empty range completes with zero hits), so the three assignments are effective from 30 June and cover every fixture; the clock then moves to 2026-12-14 for sections 3 to 8 and 10 to 14 and to 2027-01-01 for section 9. B7's fixtures activate under a 2026-06-30 clock too. B8's `rules activate` description says why. No `--effective-from` is added to the CLI and B6 keeps refusing past dates.
- **Edited.** 4b: B7 Step 1, B8 CLI paragraph. 4c: C11 Produces (the clock sentence) and section 2.

### 24. `TIER_LIMIT` declares `month_in` statically

- **Finding.** B2 derived `month_in`'s unit key from the snapshot (`f"{limit.unit}/{rule}"`), but `RulesHook.declarations`, B6's `ensure_declarations` and C5's seeding take declarations from `RuleSet.declarations`, the template definition, and B3 said the tier templates' params are `{"outcome"}` without saying what they declare; the accumulator would never have been maintained.
- **Decision.** `TIER_N1_MONTHLY_OPS.yaml` and `TIER_N2_MONTHLY_OPS.yaml` declare `month_in` (SUM, UNIT_AMOUNT, `unit_key: UDI/LAST_DAY_PREV_MONTH`, RELATIONSHIP, MONTH, direction IN), the ITF pack's `kyc.tiers[].limits.monthly_ops.unit` under `units.tier_limit_rule`. B2 reads it by name like every other aggregate and does not fire when the snapshot's `limit.unit` or `tier_limit_rule` differ from the declared key (a pack change needs a template version). B3's test pins the declaration; B2's test adds the mismatch case.
- **Edited.** 4b: B2 `TIER_LIMIT` semantics and test, B3 file description and test.

### 25. `ParkTransaction` is a fail-mode trigger on the decide path

- **Finding.** 4c Deviation 8 and C5 step 3 switched to the fail mode on `UnitValueMissingError`, but A6's `unit_amounts_for` and `enrich_input` convert it into `ParkTransaction(UNIT_VALUE_MISSING, …)` (and a missing policy into `POLICY_NOT_ASSIGNED`); unhandled, it would have been a 500 where the spec promises never a 503.
- **Decision.** C5 lists A6's `ParkTransaction` (`monitoring.enrich.service`) among the fail-mode triggers beside the directly raised errors, and `test_a_missing_unit_value_is_a_budget_failure_not_a_stale_value` asserts the rail's fail mode answers 200.
- **Edited.** 4c: Deviation 8, C5 step 3 and test.

### 26. The tier-limit boundary

- **Finding.** C5's `test_the_candidate_counts_in_its_own_aggregates` said "at the limit minus one centavo allows; at the limit blocks", but B2 fires on `month_in.value > limit.amount` (its test: exactly 750 → None) and spec §12.2 says the sum "must not exceed the limit".
- **Decision.** At the limit allows; one unit over blocks. The test says so and cites B2.
- **Edited.** 4c: C5 tests.

## Open for the product owner

Execution proceeds on the plans' defaults; a different answer becomes a follow-up task. Collected from the three plans' "Open questions" and from the findings above:

1. **The currency rule (4a Q1; finding 8).** Only `MXN` and `USD` are accepted; a USD amount is brought to pesos at the prior banking day's FIX, since `UnitService` converts from pesos only and the FIX has no weekend value; other currencies are a field error. Is that the launch rule, or should other currencies park until a rate source exists?
2. **Opt-in webhooks (4a Q2; finding 7).** `monitoring.transaction_processed.v1` and `monitoring.decision_issued.v1` are staff-only (never delivered) because the platform has no per-endpoint opt-in and the delivery is last on the spec's cut list. Confirm, or ask for the opt-in as a platform task.
3. **Jurisdiction lists (4b Q1, Q2).** `TAX_HAVEN` is empty (no source named; a template naming it is a load error) and `BORDER_ZONE_MX` defaults to the six border states as ISO 3166-2 codes, marked `UNVERIFIED`; the customs definition (the 20 km strip) would need admin-area codes the transaction's `geo_admin_area` carries.
4. **December 2026 fixtures (4a Q3, 4c Q4; finding 19).** The exit demo and 4b's goldens use the week of 7 to 13 December 2026 with test-local unit rows; the seed files on `main` cover December 2025 to January 2026, and 12 December 2026 is a Saturday, so the FIX rule crosses a weekend, not a holiday. Either accept that, or move the demo to a week with a weekday holiday (1 January 2027 is a Friday), or verify and seed the 2026 values and holidays on `main`.
5. **Counterparty purge versus the compliance record (4c Q3).** Retention deletes an orphaned counterparty with its subject and findings, including dispositions an MLRO made (on the admin engine, since dispositions are append-only for the app role); parties are redacted instead. Confirm deletion, or ask for redaction of dispositioned counterparties.
6. **The spec is not yet formally accepted (4a Q4).** The plans carry 16 (4a), 16 (4b) and 14 (4c) Deviations the review should accept or amend; A2 (the first migration) should not start before spec §5.2's first block is accepted.
7. **Who may submit a version (4b Q4).** Only the author (`NOT_AUTHOR` blocks another writer); should a colleague be allowed to submit a simulated version when the author is away?
8. **`rules.loaded` as an audit action (4b Q5).** Shared template seeding has no tenant chain; `rule_template_version.loaded_by`, `loaded_at` and `source_path` are its provenance. Confirm, or name a tenant whose chain should carry it.
9. **Simulations shorter than four months (4b Q6).** `DECLARED_VS_ACTUAL_VOLUME`'s history branch never fires in one and the simulation says so in `notes`; the alternative reads the relationship's real history from before the range.
10. **The Playwright flow of spec §19's console row (4b Q3).** B13's smoke proves the library renders; the tune, simulate, submit, approve-with-a-second-user, activate flow is proven at the API level (B9) and in C11's exit test over fixtures, not in the browser.
11. **Counterparties and private lists (4c Q1, Q2).** A counterparty on the LPB is found by the next monthly walk, not on the private-list upload; a matcher bump leaves `counterparty_name_index` rows at the old version until each counterparty is next resolved. Both are half-day additions to C2 if wanted now.
