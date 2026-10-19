# SP-03c — Re-screening, evaluation and exit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every tenant's party base screened as the world changes, and prove SP-03 is done. A shared list delta re-screens only the parties that Postgres blocking puts near the added and changed entries, and suggests `DELISTED` on findings whose entry was removed or became non-screenable. A new private-list version re-screens the whole base against that version. A monthly periodic batch re-screens every party against the full list set. A matcher-version change re-indexes and re-screens. A purged party's name index is deleted and the query side of its hits is redacted. Batch progress is visible through `GET /v1/screening/batches`. `xolotl screening eval` and `GET /v1/screening/evaluation` publish the evaluation report, and the console shows it. Perf tests record throughput and latency. `tests/e2e/test_sp03_exit.py` scripts the program's SP-03 exit demo over committed fixtures.

**Architecture:** Everything lives under `src/xolotl/modules/screening/rescreen/` (spec §2 layout), with three small additions elsewhere: one route in 3b's `routes_screening.py`, two CLI commands on 3b's `screening` Typer app, and one schedule in `schedule_platform_jobs`. Each re-screen kind is a job that handles **one page in one transaction**. In that transaction it commits the page's runs, hits and findings, the batch's progress and cursor, and the job for the next page, so a crash replays at most one page. The replay is harmless because findings are deduplicated by 3b's `ON CONFLICT DO NOTHING` key, and a page whose cursor the batch has already passed is skipped. Forward scoring always goes through 3b's `screen()`. 3c never scores by itself. Reverse retrieval (which parties are near a changed entry) is a Postgres query over `party_name_index`. It uses the library's `blocking_plan` key sets on the phonetic-key GIN index, `pg_trgm` similarity on the trigram GIN index, and the party module's blind-index identifier lookup (spec Decision 2). Its retrieval recall is measured on the library corpus. Handlers are registered by `register_rescreen(...)`, called from `wire_platform` next to 3b's screening registration. The re-index check runs in the worker's `on_start` hook beside `ensure_platform_schedules`.

**Tech Stack:** the stack on `main` after plans 3a and 3b and the screening library are merged. No new runtime dependencies. `pg_trgm` exists (3a's 0009). The perf tests use only the standard library (`time.perf_counter`, `statistics.quantiles`).

**Spec:** `docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md` §3 (`SCREENING_DELTA_BATCH`, `SCREENING_RESCREEN_BATCH`, `SCREENING_PERIODIC_CRON`), §4.2 (`blocking_plan`, `IdfTable`, `MemoryIndex`, `MATCHER_VERSION`), §4.3 (corpus, `evaluate`, floors), §5.2 (`screening_batch`, `screening_match.suggested_disposition`, `screening_hit.features`, `party_name_index`), §8.3 (`screen()` with `only_entries`; perf budget), §8.4 (features shape), §10 (all of it), §11 (`screening.match_delisting_suggested.v1`), §12 (`GET /v1/screening/batches`, `GET /v1/screening/evaluation`), §13 (`screening rescreen`, `screening eval`), §14 (Evaluación), §15 rows FR-SC-001, 003, 010, 011, 014, NFR-SC-001, 003, 004, 006, §16 (`tests/modules/screening/rescreen/*` row and the exit criteria), §19 (the 3c row), §20 (cut list). Program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md` §7.3, SP-03 row: two isolated tenants; tenant A uploads its LPB; UN, OFAC, EU, UK and SAT 69-B are ingested; a party screened via the API returns an explained hit; a list delta re-screens the base and alerts only changed entries; tenant B sees nothing; per-tenant audit verify is intact.

**Accepted by the product owner on 2026-10-19:** every departure the SP-03 spec records (§17 Decisions 1 to 14, including forward screening in memory and reverse screening in Postgres, entries stored per content, and `lists` folded into `screening`), and the defaults of all seven open questions of spec §18:
1. No licensed global sanctions or PEP database in SP-03.
2. API clients get status only.
3. Our own CSV/XLSX private-list template.
4. **A monthly periodic full re-screen for every tenant, plus a re-screen on every list delta and every data change.**
5. A 69-B name-only resemblance reaches `LOW_CONFIDENCE` at most.
6. `OFAC_CONS` is not ingested.
7. Domestic PEPs as a high-risk floor are out of SP-03.

This plan builds those defaults and does not reopen them.

## Global Constraints

- Repository `/Users/omaribannez/code/xolotl`. Every task is one PR from `main` on branch `feat/sp03c-f<n>-<short>` in its own worktree (`docs/superpowers/execution/sp02/agreement.md` binds: gates, conventions, method, commits, report). `make ci` must be green at the end of every task. When `console/` changes (F6 only), the console gates must pass too: `npm run typecheck && npm run lint && npm test && npm run build`.
- **No migration by default.** Every table and column 3c writes is created by 3a's `0009_lists` or 3b's `0010_screening`. There is one conditional exception, in F5: if 3b's 0010 does not grant the app role `UPDATE (features)` on `screening_hit` and `DELETE` on `party_name_index`, F5 adds `src/xolotl/migrations/versions/0011_screening_rescreen.py` with `revision = "0011_screening_rescreen"`, `down_revision = "0010_screening"` and those two grants only. It also appends `"0011_screening_rescreen"` to `CHAIN` in `tests/platform/test_migration_chain.py`. No other 3c task may add a migration.
- Package `src/xolotl/modules/screening/rescreen/`: `__init__.py` (exports `register_rescreen`, `ensure_screening_index`), `batches.py` (F1), `delisting.py` (F1), `blocking.py` (F2), `list_delta.py` (F3), `walkers.py` (F4), `purge.py` (F5). Evaluation lives in `src/xolotl/modules/screening/evaluation_report.py` (F6). Tests go under `tests/modules/screening/rescreen/` (with `__init__.py`), `tests/perf/` (F7) and `tests/e2e/` (F8).
- **Import contracts** (spec §2): `screening.rescreen` may import `xolotl.platform.*`, the other screening subpackages and `xolotl.modules.party` **service functions and enums only**: `parties_query`, `get_party`, `PartyStatus`, `IdentifierType`, `IdentifierInvalidError` and F4's new `party_ids_after`. It never imports `xolotl.modules.party.models`. F4 adds one service function to `modules/party/service.py`, `party_ids_after(s, *, after: UUID | None, limit: int) -> list[UUID]`. It returns the ids of `ACTIVE` parties greater than `after`, in id order, and is what the walkers page with. `modules/tenants` never imports screening: the periodic schedule name is a string literal there.
- **Settings** (`src/xolotl/platform/config.py`, prefix `XOLOTL_`): `screening_delta_batch: int = 200`, `screening_rescreen_batch: int = 500` and `screening_periodic_cron: str = "0 2 1 * *"`. The cron is validated by adding its name to the existing `@field_validator("units_fetch_cron", "retention_purge_cron", ...)` decorator of `_check_cron`. All three go in `.env.example`, the README configuration table and the `model_fields_set` assertion of `tests/platform/test_config.py::test_env_example_is_a_valid_configuration`. F1 adds the two batch sizes and F4 adds the cron. If 3b already added a field, the task reuses it and adds nothing.
- **Job kinds** (constants in the module that handles them): `screening.list_delta` (`LIST_DELTA_KIND`, F3), `screening.private_list_rescreen` (`PRIVATE_LIST_RESCREEN_KIND`, F4), `screening.periodic_rescreen` (`PERIODIC_RESCREEN_KIND`, F4) and `screening.reindex` (`REINDEX_KIND`, F4). Next-page job keys are `<kind>:<batch_id>:<cursor>`, so the queue's dedup index refuses a second copy of the same page.
- **Payloads** (JSON, ids as strings):

  | Kind | Payload |
  |---|---|
  | `list_delta` | `{"batch_id"?, "version_id", "offset"}` |
  | `private_list_rescreen` | `{"batch_id"?, "private_list_version_id", "after_party_id"}` |
  | `periodic_rescreen` | `{}` from the schedule (opens the month's batch), else `{"batch_id", "after_party_id"}` |
  | `reindex` | `{"batch_id", "after_party_id"}` |

  `batch_id` is optional on the two kinds 3a enqueues. Without it, the handler opens or reuses the batch by `(kind, ref)`, so 3a may enqueue before or after creating the batch row.
- **Schedule:** per tenant, named `screening.periodic_rescreen`, with `cron = get_settings().screening_periodic_cron`, `tz = "America/Mexico_City"`, `kind = "screening.periodic_rescreen"` and `payload = {}`. It is declared by one more explicit `queue.schedule(...)` call in `schedule_platform_jobs` through the module constant `SCREENING_PERIODIC_SCHEDULE = "screening.periodic_rescreen"`. The pinned schedule tests (`tests/platform/test_worker.py::PLATFORM_SCHEDULES`, `tests/modules/tenants/test_kernel_events.py`) gain the row in name order in the same task (F4), as plan 2c's C5 did.
- **Batches:** `screening_batch` rows (3b's model `ScreeningBatch`). `kind` is `LIST_DELTA`, `PRIVATE_LIST`, `PERIODIC` or `REINDEX`. `ref` takes one of these forms: `str(list_version_id)`, `str(private_list_version_id)`, `YYYY-MM` (Mexico City month of the run), `initial:<code>` (3a's first-version batch, walked as `PERIODIC`) or `MATCHER_VERSION`. `status` is `RUNNING`, `COMPLETED` or `FAILED`. `cursor` is the stringified offset for deltas and the last party id for walkers. `total` is computed when the batch opens. A batch is `FAILED` only when its job exhausts its attempts. The runner records the job failure, and the next open of the same `(kind, ref)` resumes a `FAILED` batch from its cursor.
- **Runs:** every re-screen calls 3b's `screen(session, subject, *, mode, trigger, ...)` with `mode` in `LIST_DELTA`, `PRIVATE_LIST`, `PERIODIC` or `REINDEX`, `trigger.type = JOB`, `trigger.ref = str(job.id)` and the run's `batch_id` set. Delta and private-list runs pass the restricted entries, so `restricted_to_delta` is true. Only `ACTIVE` parties are screened. `PURGED` and `MERGED` parties are skipped and counted as done.
- **Events:** `screening.match_delisting_suggested.v1` (staff-only, data `{"match_id", "list_code"}`) is published once per finding that newly gets `suggested_disposition = DELISTED`. A finding that already carries it publishes nothing again. 3c publishes no other event type. Runs publish 3b's events through `screen()`.
- **Audit:** 3c records no new audit action. Jobs are system activity, and runs are their own record (spec §5.2). `screening rescreen` from the CLI runs as `cli:<user>`, so its runs carry `trigger_type = CLI`.
- **API:** `GET /v1/screening/batches` (staff, any role; machine scope `screening:read`) and `GET /v1/screening/evaluation` (staff only, no scope). Both are on 3b's `routes_screening.router`, built with `v1_router`, and the batches list is cursor-paginated with `paginate(..., ts_col=ScreeningBatch.started_at, id_col=ScreeningBatch.id)`.
- **Plaintext:** nothing in job payloads, `last_error`, logs or batch rows names a person. Payloads and errors carry ids, list codes and public `source_entry_key`s only. Redacted hits keep scores, bands, rules, qualifier outcomes and entry references.
- MVP footprint: one container plus Postgres. No new service, no Redis, no OpenSearch, no worker pool beyond the existing runner.
- Commit trailers: every commit ends with the `Co-Authored-By` line the task prompt gives.

## Deviations from the sub-spec (decided here)

- **Batch bookkeeping is shared.** `rescreen/batches.py` opens batches by `(kind, ref)` with `INSERT ... ON CONFLICT (tenant_id, kind, ref) DO NOTHING` and then a locked read. It advances them in the page's transaction. A `COMPLETED` batch is never reopened: a second periodic job in the same month is a no-op, and so is a reindex for a matcher version already done. The spec says only "creates a `PERIODIC` batch (ref `YYYY-MM`)". This rule makes the schedule safe under at-least-once delivery.
- **The re-index also backfills.** `ensure_screening_index` opens a `REINDEX` batch when any `party_name_index.matcher_version` differs from `MATCHER_VERSION` (as in the spec), **or** when an `ACTIVE` party has no `LEGAL` row. Parties created before 3b's subscriber existed (every SP-02 party) would otherwise never be reverse-screened.
- **The private-list walker builds the version's index once per page.** It loads the version's screenable entries, builds one `MemoryIndex` per page and passes it to `screen()` through `only_index=`, a keyword F1 adds to 3b's `screen()` beside `only_entries`. The two keywords are mutually exclusive. Rebuilding a 5,000-entry index for each of 500 parties would dominate the page. If 3b's `screen()` already accepts a prebuilt index, or caches the throwaway one, F1 drops the keyword and F4 uses 3b's mechanism. *Reconciliation item.*
- **`screen()` takes `batch_id`.** F1 adds `batch_id: UUID | None = None` to 3b's `screen()` (written to `screening_run.batch_id`) unless 3b's `Trigger` already carries it. *Reconciliation item.*
- **Trigram filter.** The blocking query uses `norm_name % :folded` after `SET LOCAL pg_trgm.similarity_threshold = 0.4`, not `similarity(norm_name, :folded) >= 0.4` as spec §10.2 writes it. The two predicates select the same rows, but only `%` is served by the `ix_pni_trgm` GIN index. The phonetic predicate is one `phonetic_keys @> CAST(:k AS text[])` per key set, OR-ed together, which `ix_pni_phonetic` serves.
- **Identifier blocking goes through `parties_query`.** It uses `parties_query(s, id_type=..., value=...)`, the party module's blind-index lookup, for each `RFC`, `CURP` and `PASSPORT` identifier of the entry. A value that fails the party validator raises `IdentifierInvalidError`. The entry is still blocked by name, and the failure is logged at DEBUG with the list code and entry key only. Lists publish malformed RFCs, so this is routine and not a warning.
- **Overflow:** an entry whose candidate union exceeds 5,000 parties (`BLOCKING_CAP`) is not screened in the delta. Instead it is logged at WARNING (`screening.delta_entry_overflow`, list code and entry key) and counted in `screening_batch.last_error` as `OVERFLOW:<n>`, with the batch still `COMPLETED`. The next periodic run covers it (spec §10.2).
- **The evaluation report is a committed file.** `xolotl screening eval --write` writes `src/xolotl/modules/screening/data/evaluation.json`. A test fails when its `matcher_version` differs from `MATCHER_VERSION`, so a matcher change cannot ship with a stale report. The CLI reads the corpus from `tests/modules/screening/corpus/` by default (`--corpus` overrides it). It is a build-time tool and is refused cleanly when the corpus is absent, for example in the container image.
- **Perf tests are marked and excluded.** They carry `@pytest.mark.perf` and are excluded from the default run by `-m "not perf"` in `addopts`. They run by hand with `make perf`, which overrides the marker. They assert functional outcomes and the spec's budgets, and write their measurements to `.perf/sp03.json` (gitignored). CI never runs them (spec §15: "recorded, not gating"). If the library's perf test registered the `perf` marker first, F7 reuses it.
- **Purge redaction marks the hit.** Redaction replaces the query-side strings of `features` with `""`: `query_name`, each `alignment[].query` and `unmatched_query` (emptied). It adds `"redacted": true`. Entry-side tokens, scores, bands, kinds, weights, qualifier outcomes, rules and `entry_ref` stay. Qualifier `detail` strings are masked by the library already, so they stay as stored. Redaction is idempotent.

## Dependencies outside this plan

Names are the spec's. Where the spec fixes no name, the assumed name is marked *(assumed)*, and the task that consumes it adapts to whatever the producing plan shipped. It records any rename in its PR.

**LIB-lists** (screening library, `xolotl.modules.screening.lists`):
- `ListEntry`, with `entry_key`, `screenable`, `identifiers`, `primary_name`, `aliases` and `from_json`.
- `EntityType`, `IdType` and `EntryIdentifier`.
- The trimmed UN fixture under `tests/modules/screening/fixtures/`, which F8 derives its second version from.

**LIB-matching** (`xolotl.modules.screening.matching`):
- `MATCHER_VERSION`, `normalize_name`, `NormalizedName`, `blocking_plan`, `BlockingPlan`, `IdfTable`, `MemoryIndex.build` and `IndexedEntry`.

**LIB-corpus** (`xolotl.modules.screening.matching.evaluation` and `tests/modules/screening/corpus/`):
- `evaluate(corpus) -> EvaluationReport`.
- The corpus loader `load_corpus(path) -> Corpus` *(assumed)*.
- The corpus's labelled true pairs, each a query plus its `ListEntry` (`Corpus.true_pairs` *(assumed)*).
- `corpus/floors.yaml` and its loader `load_floors(path)` *(assumed)*.
- `EvaluationReport.to_json()` *(assumed)* and the floor check `report.below(floors) -> list[str]` *(assumed)*.

**3a** (list catalog and private lists, `docs/sp03a-plan`, ids D…):
- `ListEntryRow`, `ListEntryDeltaRow` and `ListVersion` *(model names assumed)* over `list_entry`, `list_entry_delta` and `list_version`.
- `PrivateListEntryRow`, `PrivateListEntryDeltaRow`, `PrivateListVersion` and `PrivateList` *(assumed)* over the private tables.
- The catch-up of spec §6.4 enqueues `screening.list_delta` with `{"version_id", "offset": 0}` (key `list_delta:<version_id>`), plus `batch_id` if it inserted the batch.
- The first version of a list enqueues `screening.periodic_rescreen` with a `PERIODIC` batch ref `initial:<code>`.
- A private upload enqueues `screening.private_list_rescreen` with `{"private_list_version_id", "after_party_id": null}`, plus `batch_id` if it inserted the batch.
- `ingest_file(...)`, `xolotl lists load-file`, the `lists.poll` job kind constant `LISTS_POLL_KIND` *(assumed)*, `POST /v1/private-lists` and `POST /v1/private-lists/{id}/versions` (F8).
- A function decrypting a private entry row to a `ListEntry`, `private_entry(s, row) -> ListEntry` *(assumed)*.

**3b** (screening engine and review, `docs/sp03b-plan`, ids E…):
- `screen(session, subject, *, mode, trigger, lists=None, only_entries=None) -> RunResult`, with `RunResult.new_match_count`.
- `RunMode` and `TriggerType` enums, and `Trigger` *(assumed)*.
- `subject_for_party`, `query_for_party` and `index_party_names`.
- `ScreeningIndexHolder`, with the shared snapshot's `IdfTable` (`holder.snapshot().idf` *(assumed)*) and a per-process accessor (`get_index_holder()` *(assumed)*).
- The models `PartyNameIndex`, `ScreeningSubject`, `ScreeningRun`, `ScreeningHit`, `ScreeningMatch`, `ScreeningBatch` and `MatchStatus` over 0010.
- The event constant `MATCH_DELISTING_SUGGESTED = "screening.match_delisting_suggested.v1"`, registered staff-only through `register_staff_only`. If 3b omitted it, F1 adds the constant and the registration.
- The `screening` consumer name of its party subscribers, the screening `routes_screening.router`, the `screening_app` Typer group in `cli.py` and `register_screening(...)` in `wire_platform`.
- The console `features/screening/` folder and `ACCESS.screening` *(assumed)* (F6).
- `POST /v1/screening/checks`, `GET /v1/screening/matches`, `GET /v1/screening/runs/{id}`, the dispositions route and the staff-only webhook guard (F8).

**Earlier plans (on `main`):**
- `parties_query`, `get_party`, `create_party`, `PartyStatus`, `IdentifierType` and `party.purged.v1` (2c).
- `effective_policy` and `tests/platform/policy_support.py::install_both_packs` (2a).
- `PostgresJobQueue.enqueue/schedule`, `JobRunner`, `register_job_handler`, `register_subscriber`, `tenant_ids`, `verify_chain` and `GET /v1/audit/verify` (SP-01).

## Review Focus

1. **A delta alerts only on what changed.** The base holds parties near unchanged, added, changed and removed entries. After a delta, findings appear only for the added and the changed entries. The changed one supersedes the party's older finding on the same key. The removed entry's open finding gets `suggested_disposition = DELISTED` and exactly one `screening.match_delisting_suggested.v1`. No run screens an unchanged entry (`restricted_to_delta`, and each run's hits all point at delta rows). Tests: F3 `test_a_delta_alerts_only_on_added_and_changed_entries`, `test_a_removed_or_desvirtuado_entry_suggests_delisting_once`.
2. **Postgres blocking finds what the memory index finds.** On the corpus's true pairs, loaded as parties, the reverse query retrieves at least 0.995 of them (`NFR-SC-006`), with both indexes used (`EXPLAIN` shows `ix_pni_phonetic` or `ix_pni_trgm`, never a sequential scan, on a 2,000-row analysed table). Tests: F2 `test_postgres_blocking_retrieval_recall_meets_the_floor`, `test_the_blocking_query_uses_the_gin_indexes`.
3. **Crash safety.** Killing a job after its page committed and before the next page ran, or re-delivering a page job, never duplicates a finding, double-counts a batch or skips a party. A batch resumes from its cursor. Tests: F3 `test_a_redelivered_page_changes_nothing`, F4 `test_a_walker_resumes_from_its_cursor_after_a_crash`.
4. **Tenant isolation in every walker.** A private-list re-screen of tenant A never reads tenant B's parties or entries. Blocking runs under the tenant's session (RLS). Tenant B's base never gets a run from tenant A's batch. Tests: F4 `test_a_private_list_batch_screens_only_its_tenants_parties`, F8 `test_sp03_exit`.
5. **Purge leaves no name behind.** After `party.purged.v1`, the party has no `party_name_index` row. None of its hits' `features` holds a query-side token. `everything_stored(admin_engine)` holds none of its folded name tokens outside list-entry rows. Scores, bands and entry references stay. Test: F5 `test_a_purged_partys_tokens_are_gone_and_its_record_stays`.

## File structure

```
src/xolotl/modules/screening/rescreen/
  __init__.py            register_rescreen, ensure_screening_index (F1 creates; F3, F4, F5 add their registrations)
  batches.py             open_batch, advance_batch, complete_batch, batch_progress, BATCH_KINDS (F1)
  delisting.py           suggest_delisting (F1)
  blocking.py            BLOCKING_CAP, reverse_candidates, BlockedEntry (F2)
  list_delta.py          LIST_DELTA_KIND, handle_list_delta (F3)
  walkers.py             PRIVATE_LIST_RESCREEN_KIND, PERIODIC_RESCREEN_KIND, REINDEX_KIND, the three handlers,
                         ensure_screening_index, start_rescreen (F4)
  purge.py               on_party_purged, redact_features (F5)
src/xolotl/modules/screening/evaluation_report.py   load_shipped_report, EvaluationReportResponse (F6)
src/xolotl/modules/screening/data/evaluation.json   the committed report (F6)
src/xolotl/modules/screening/engine/…              screen(): + batch_id, + only_index (F1, 3b's file)
src/xolotl/modules/screening/routes_screening.py    + GET /batches (F1), + GET /evaluation (F6)
src/xolotl/modules/screening/schemas.py             + BatchResponse (F1), + EvaluationReportResponse re-export (F6)
src/xolotl/platform/config.py, .env.example, README.md   + three settings (F1, F4)
src/xolotl/modules/tenants/service.py               + SCREENING_PERIODIC_SCHEDULE (F4)
src/xolotl/modules/party/service.py                 + party_ids_after (F4)
src/xolotl/app.py                                   + register_rescreen in wire_platform (F1); + ensure_screening_index in worker_loop's on_start (F4)
src/xolotl/cli.py                                   + screening rescreen (F4), + screening eval (F6)
pyproject.toml, Makefile, .gitignore                + perf marker, -m "not perf", make perf, .perf/ (F7)
tests/modules/screening/rescreen/{__init__,conftest}.py, test_batches.py, test_delisting.py (F1), test_blocking.py (F2),
  test_list_delta.py (F3), test_walkers.py (F4), test_purge.py (F5)
tests/modules/screening/test_evaluation_report.py, tests/test_cli.py (+) (F6)
tests/platform/test_worker.py, tests/modules/tenants/test_kernel_events.py, tests/platform/test_config.py (+) (F1, F4)
tests/perf/{__init__,conftest}.py, tests/perf/test_sp03_perf.py (F7)
tests/e2e/{__init__,conftest}.py, tests/e2e/test_sp03_exit.py, tests/e2e/fixtures/{README.md, un_consolidated_v2.xml, lpb_tenant_a.csv} (F8)
console/src/features/screening/{EvaluationPage.tsx, EvaluationPage.test.tsx, queries.ts (+)}, App.tsx, components/layout/nav.ts,
  i18n/{es-MX.json, en.json}, api/schema.d.ts (regenerated) (F6)
```

---

### Task F1: Batch bookkeeping, delisting suggestions, the batches API and the `screen()` hooks

Depends on 3b: `ScreeningBatch`, `ScreeningMatch`, `screen()`, `routes_screening.router`, `register_screening` and the event constants. It creates the package every other task of this plan extends. Touches `src/xolotl/app.py` (`wire_platform`). Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/rescreen/{__init__,batches,delisting}.py`
- Modify: 3b's `screen()` module (`batch_id`, `only_index` keywords), `routes_screening.py` (`GET /batches`), `schemas.py` (`BatchResponse`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (two batch-size settings), `src/xolotl/app.py` (`register_rescreen(queue=queue)` right after 3b's `register_screening(...)`)
- Test: `tests/modules/screening/rescreen/{__init__,conftest}.py`, `test_batches.py`, `test_delisting.py`; `tests/platform/test_config.py` (+)

**Consumes:** 3b's models and `screen()`, `MatchStatus`, `MATCH_DELISTING_SUGGESTED`, `publish` (platform events), `paginate`, `require_access`, `PostgresJobQueue`.

**Produces:**

```python
# batches.py
class BatchKind(StrEnum): LIST_DELTA; PRIVATE_LIST; PERIODIC; REINDEX       # 3b's enum if it defined one
class BatchStatus(StrEnum): RUNNING; COMPLETED; FAILED

def open_batch(s: Session, *, kind: BatchKind, ref: str, total: int | None) -> ScreeningBatch | None
    # INSERT … ON CONFLICT (tenant_id, kind, ref) DO NOTHING; then SELECT … FOR UPDATE.
    # COMPLETED -> None (nothing to do); FAILED -> status RUNNING, last_error None, cursor kept; RUNNING -> the row
def lock_batch(s: Session, batch_id: UUID) -> ScreeningBatch               # FOR UPDATE; NotFoundError if absent
def advance_batch(batch: ScreeningBatch, *, done: int, runs: int, new_matches: int, cursor: str) -> None
    # adds the counts and sets cursor (never moves it backwards: compares as offset or as UUID per kind)
def complete_batch(batch: ScreeningBatch, *, note: str | None = None) -> None   # COMPLETED, finished_at = clock.now(), last_error = note
def page_already_done(batch: ScreeningBatch, cursor: str) -> bool          # True when the batch is COMPLETED or its cursor passed this page's

# delisting.py
def suggest_delisting(s: Session, *, list_code: str, entry_keys: Collection[str]) -> list[UUID]
    # UPDATE screening_match SET suggested_disposition = 'DELISTED', updated_at = now()
    #   WHERE list_code = :code AND source_entry_key = ANY(:keys) AND status IN ('OPEN','ESCALATED','TRUE_MATCH')
    #   AND suggested_disposition IS DISTINCT FROM 'DELISTED' RETURNING id;
    # publishes MATCH_DELISTING_SUGGESTED {"match_id", "list_code"} per returned id; returns the ids

# __init__.py
def register_rescreen(*, queue: PostgresJobQueue) -> None   # F1: nothing to register yet but the module; F3/F4/F5 add theirs

# routes_screening.py (3b's router)
@router.get("/batches")   # require_access(scope="screening:read"); filters kind, status; Page[BatchResponse], newest first
def list_screening_batches(...) -> Page[BatchResponse]
# BatchResponse: id, kind, ref, status, total, done, runs, new_matches, started_at, finished_at, last_error
```

- [ ] **Step 1: Write the failing tests.**
  - `test_batches.py`:
    - `test_opening_a_batch_twice_returns_the_same_row`
    - `test_a_completed_batch_is_never_reopened`
    - `test_a_failed_batch_resumes_from_its_cursor` (cursor kept, status `RUNNING`)
    - `test_the_cursor_never_moves_backwards`
    - `test_page_already_done_compares_offsets_and_party_ids`
    - `test_batches_are_listed_newest_first_for_staff_and_scoped_keys` (an `ANALYST` and a `screening:read` key see them; a key without the scope is 403 `SCOPE_MISSING`)
    - `test_another_tenant_sees_no_batches`
  - `test_delisting.py`, over findings seeded through 3b's factories:
    - `test_open_escalated_and_true_match_findings_get_the_suggestion` (`FALSE_POSITIVE`, `DELISTED` and `SUPERSEDED` ones are untouched)
    - `test_a_second_suggestion_publishes_nothing` (one `screening.match_delisting_suggested.v1` per finding, ever)
    - `test_the_suggestion_event_is_staff_only` (a wildcard webhook endpoint gets no delivery row)
  - Engine hooks (in 3b's engine test module, appended):
    - `test_a_run_records_its_batch`
    - `test_only_index_scores_against_the_given_index_and_marks_the_run_restricted`
    - `test_only_entries_and_only_index_are_exclusive` (`ValueError`)
  - `tests/platform/test_config.py`: the two new fields in `.env.example`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/rescreen tests/platform/test_config.py --no-cov -q` → `ImportError`.
- [ ] **Step 3: Implement** the code, the settings and the `wire_platform` line.
- [ ] **Step 4: Run to verify pass.** Run the targeted tests, then `make lint types vulture importlint`, then `make ci`.
- [ ] **Step 5: Docs.** Add the README configuration rows. Add a paragraph to the `rescreen/__init__.py` module docstring: one page, one transaction; batches by `(kind, ref)`.
- [ ] **Step 6: Commit** `feat(screening): re-screen batches, delisting suggestions and the batches API`.

---

### Task F2: Reverse blocking in Postgres, with retrieval recall on the corpus

Depends on LIB-matching (`normalize_name`, `blocking_plan`, `IdfTable`), LIB-corpus (true pairs) and 3b (`PartyNameIndex`, `index_party_names`, the holder's shared `IdfTable`). It does not need F1, so it runs in parallel with it. Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/rescreen/blocking.py`
- Test: `tests/modules/screening/rescreen/test_blocking.py`

**Consumes:** `parties_query` (blind-index identifier lookup), `IdentifierInvalidError`, `IdentifierType`, `PartyStatus`, `normalize_name`, `blocking_plan`, `ListEntry.identifiers`, `IdType`.

**Produces:**

```python
BLOCKING_CAP = 5000
TRIGRAM_THRESHOLD = 0.4
_ID_TYPES = {IdType.RFC: IdentifierType.RFC, IdType.CURP: IdentifierType.CURP, IdType.PASSPORT: IdentifierType.PASSPORT}

@dataclass(frozen=True, slots=True)
class BlockedEntry:
    entry_key: str
    party_ids: frozenset[UUID]
    overflow: bool                       # union exceeded BLOCKING_CAP; party_ids is empty then

def reverse_candidates(s: Session, entries: Sequence[ListEntry], *, idf: IdfTable) -> list[BlockedEntry]
    # under the caller's tenant session: SET LOCAL pg_trgm.similarity_threshold = 0.4 once;
    # per entry, per name (primary + aliases, WEAK aliases included):
    #   n = normalize_name(name.full, entity_type=entry.entity_type, given=name.given, surname=name.surname)
    #   plan = blocking_plan(n, idf)
    #   SELECT party_id FROM party_name_index
    #     WHERE (phonetic_keys @> :k1 OR phonetic_keys @> :k2 …) OR norm_name % :folded
    #     LIMIT BLOCKING_CAP + 1
    # plus, per RFC/CURP/PASSPORT identifier, parties_query(s, id_type=…, value=identifier.normalized, status=ACTIVE)
    # union per entry; overflow when > BLOCKING_CAP
```

- [ ] **Step 1: Write the failing tests** (`test_blocking.py`, parties created with 2c's `create_party` and indexed with 3b's `index_party_names`):
  - `test_a_dropped_second_surname_and_an_accent_loss_are_blocked`
  - `test_a_nickname_variant_is_blocked_through_the_phonetic_keys` (when the library's key sets cover it; otherwise through trigrams)
  - `test_an_rfc_on_the_entry_finds_the_party_without_a_name_match`
  - `test_an_invalid_list_rfc_falls_back_to_names`
  - `test_a_common_unrelated_name_is_not_blocked` (a hard negative from the corpus)
  - `test_more_than_the_cap_marks_the_entry_overflow` (monkeypatch `BLOCKING_CAP = 3`)
  - `test_blocking_never_crosses_tenants` (the same name in tenant B is not returned under A's session)
  - `test_purged_parties_are_not_returned` (their rows are gone, F5's behaviour simulated by deleting the index rows; RFC lookup filters `ACTIVE`)
  - `test_the_blocking_query_uses_the_gin_indexes`: 2,000 synthetic index rows, `ANALYZE`, then `EXPLAIN (FORMAT JSON)` of one phonetic query and one trigram query names `ix_pni_phonetic` and `ix_pni_trgm`.
  - `test_postgres_blocking_retrieval_recall_meets_the_floor`: load the corpus's true pairs' query sides as parties, at most 2,000 drawn by the corpus's seeded order so CI stays under a minute. Run `reverse_candidates` over their entries. Recall must be at least `floors.yaml`'s retrieval floor (0.995). On failure the test prints the missed pairs' folded names.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/rescreen/test_blocking.py --no-cov -q`.
- [ ] **Step 3: Implement** `blocking.py`.
- [ ] **Step 4: Run to verify pass**, then the gates and `make ci`.
- [ ] **Step 5: Docs.** Write the `blocking.py` module docstring: why `%` and not `similarity()`, and the cap. Record the measured recall in the PR body.
- [ ] **Step 6: Commit** `feat(screening): reverse blocking over party_name_index with corpus recall`.

---

### Task F3: The shared list-delta re-screen job

Depends on F1 and F2, on 3a (`list_entry_delta`, `list_entry`, the catch-up enqueue) and on 3b (`screen()`, `subject_for_party`, `get_index_holder`). Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/rescreen/list_delta.py`
- Modify: `rescreen/__init__.py` (`register_job_handler(LIST_DELTA_KIND, …)` in `register_rescreen`)
- Test: `tests/modules/screening/rescreen/test_list_delta.py`

**Consumes:** F1's `open_batch`, `lock_batch`, `advance_batch`, `complete_batch`, `page_already_done` and `suggest_delisting`; F2's `reverse_candidates`; 3a's delta and entry models; 3b's `screen`, `subject_for_party`, `IndexedEntry`, `holder.snapshot().idf`; `get_settings().screening_delta_batch`.

**Produces:**

```python
LIST_DELTA_KIND = "screening.list_delta"

def handle_list_delta(queue: PostgresJobQueue) -> JobHandler     # closure registered by register_rescreen
    # 1. batch = lock_batch(payload["batch_id"]) or open_batch(LIST_DELTA, ref=version_id, total=<count of ADDED+CHANGED+REMOVED rows>)
    #    None or page_already_done(batch, str(offset)) -> return
    # 2. at offset 0 only: removed keys and CHANGED keys whose new row is non-screenable -> suggest_delisting(list_code, keys)
    # 3. page = ADDED and CHANGED delta rows whose new row is screenable, ordered by source_entry_key, OFFSET offset LIMIT screening_delta_batch
    #    entries = [ListEntry.from_json(row.entry)]; indexed = IndexedEntry(entry, ref=new_entry_id, shard=list_code)
    # 4. blocked = reverse_candidates(s, entries, idf=holder idf); invert to party_id -> [IndexedEntry]
    # 5. for each party (id order): skip unless ACTIVE; result = screen(s, subject_for_party(s, pid), mode=LIST_DELTA,
    #       trigger=Trigger(JOB, str(job.id)), only_entries=its entries, batch_id=batch.id)
    # 6. advance_batch(done=len(page), runs=…, new_matches=sum(result.new_match_count), cursor=str(offset + len(page)))
    # 7. short page -> complete_batch(note="OVERFLOW:<n>" if any overflow else None); else enqueue the next offset (key f"{kind}:{batch.id}:{next}")
```

The handler asks the holder for the version before step 4 (`holder.ensure(version_id)`, 3b's forced refresh), so the run's list set records the delta's version.

- [ ] **Step 1: Write the failing tests** (`test_list_delta.py`). The conftest helper `load_versions(v1, v2)` ingests two `ParsedList`s built in code through 3a's `ingest_file` path, or through 3a's lower-level writer if it has one. The tenant has a subscription and parties near five entries: unchanged U, added A, changed C, removed R, and D, which becomes 69-B `DESVIRTUADO`. Before the delta, U, C, R and D each have an open finding from an earlier run.
  - `test_a_delta_alerts_only_on_added_and_changed_entries`: after the job runs, the new findings are exactly those for A and C. C's old finding is `SUPERSEDED` and points at the new one. U's finding is untouched. The batch is `COMPLETED` with `done == 2` and `new_matches == 2`. Every run is `LIST_DELTA` with `restricted_to_delta` and the batch id, and every hit's `list_entry_id` is a delta row's `new_entry_id`.
  - `test_a_removed_or_desvirtuado_entry_suggests_delisting_once`: R and D are `DELISTED`-suggested, and two events are published. Re-running the job publishes none.
  - `test_parties_far_from_every_entry_are_not_screened`: no run for them.
  - `test_pages_chain_until_the_delta_is_done`: `screening_delta_batch = 2` with five changed entries gives three jobs, cursor `"5"`, `COMPLETED`.
  - `test_a_redelivered_page_changes_nothing`: running the same page job again after its commit adds no run, hit, finding or count.
  - `test_the_job_opens_its_batch_when_the_payload_has_none`: matches 3a enqueueing before the batch exists.
  - `test_an_overflowing_entry_is_left_to_the_periodic_run`: `BLOCKING_CAP` patched, `last_error == "OVERFLOW:1"`, the batch `COMPLETED`.
  - `test_purged_parties_are_skipped`.
  - `test_the_catch_up_reaches_the_job_through_the_worker`: 3a's `lists.poll` is enqueued and the runner and bus are drained. The delta findings exist, which proves 3a's enqueue contract end to end.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates and `make ci`.
- [ ] **Step 5: Docs.** Write the module docstring (the spec §10.2 steps and the overflow rule).
- [ ] **Step 6: Commit** `feat(screening): list-delta re-screen with Postgres blocking and delisting suggestions`.

---

### Task F4: Party walkers: private-list, periodic and re-index batches, the schedule and `screening rescreen`

Depends on F1, on 3a (private-list versions, entries and deltas, `private_entry`, the upload's enqueue, the initial-version batch) and on 3b (`screen`, `index_party_names`, the holder). It runs in parallel with F3: the two tasks share only F1's helpers and touch different files, except `rescreen/__init__.py`, where each adds one line. Touches `src/xolotl/modules/tenants/service.py`, the pinned schedule tests, `src/xolotl/app.py` (`worker_loop`) and `src/xolotl/cli.py`. Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/rescreen/walkers.py`
- Modify:
  - `rescreen/__init__.py` (three handlers; `ensure_screening_index` export)
  - `src/xolotl/modules/party/service.py` (`party_ids_after`, with a test appended to `tests/modules/party/test_service.py`: id order, `ACTIVE` only, `after` exclusive)
  - `src/xolotl/modules/tenants/service.py` (`SCREENING_PERIODIC_SCHEDULE`, one `queue.schedule` call)
  - `src/xolotl/platform/config.py`, `.env.example` and `README.md` (`screening_periodic_cron`)
  - `src/xolotl/app.py` (`worker_loop`'s `on_start` runs `ensure_platform_schedules` and then `ensure_screening_index`)
  - `src/xolotl/cli.py` (`screening rescreen`)
  - `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`, `tests/platform/test_config.py` and `tests/test_cli.py` (append)
- Test: `tests/modules/screening/rescreen/test_walkers.py`

**Produces:**

```python
PRIVATE_LIST_RESCREEN_KIND = "screening.private_list_rescreen"
PERIODIC_RESCREEN_KIND = "screening.periodic_rescreen"
REINDEX_KIND = "screening.reindex"

def _walk_page(s, job, queue, batch, *, mode: RunMode, after: UUID | None, screen_one: Callable[[UUID], RunResult | None]) -> None
    # party_ids_after(s, after=after, limit=screening_rescreen_batch); screen_one each; advance_batch(cursor=str(last id));
    # short page -> complete_batch; else enqueue the same kind with {"batch_id", "after_party_id": last}

def handle_private_list_rescreen(queue) -> JobHandler
    # batch by payload or open_batch(PRIVATE_LIST, ref=version_id, total=<ACTIVE party count>)
    # first page only: removed private keys and non-screenable new rows -> suggest_delisting(private list_code, keys)
    # build once per page: entries = [private_entry(s, row) for the version's screenable rows];
    #   index = MemoryIndex.build(IndexedEntry(e, ref=row.id, shard=list_code) …, idf=holder idf)
    # screen_one = screen(…, mode=PRIVATE_LIST, only_index=index, batch_id=…)
    # a version that is no longer ACTIVE when the page runs: complete_batch(note="SUPERSEDED_VERSION") (the newer version has its own batch)
def handle_periodic_rescreen(queue) -> JobHandler
    # payload {} -> open_batch(PERIODIC, ref=today_mx.strftime("%Y-%m")) and enqueue the first page (None -> already done this month)
    # payload with batch_id (including 3a's "initial:<code>" batches) -> _walk_page(mode=PERIODIC), full list set
def handle_reindex(queue) -> JobHandler
    # _walk_page(mode=REINDEX) where screen_one = index_party_names(s, pid) then screen(…, mode=REINDEX)

def ensure_screening_index(session_factory: SessionFactory, queue: PostgresJobQueue) -> int
    # for each tenant_ids(...): stale = exists(PartyNameIndex.matcher_version != MATCHER_VERSION)
    #   or exists(ACTIVE party without a LEGAL row); if stale: open_batch(REINDEX, ref=MATCHER_VERSION) and enqueue page one
    # returns the number of tenants given a batch

def start_rescreen(s: Session, queue: PostgresJobQueue, *, kind: BatchKind) -> UUID | None
    # the CLI's entry: PERIODIC opens ref "manual:<UTC timestamp>" (never collides with the monthly batch); REINDEX opens ref MATCHER_VERSION
```

CLI (on 3b's `screening_app`):

```python
@screening_app.command("rescreen")
def screening_rescreen(*, tenant: Annotated[str, typer.Option("--tenant")],
                       kind: Annotated[str, typer.Option("--kind", help="PERIODIC or REINDEX")] = "PERIODIC") -> None:
    """Start a full re-screen of the tenant's parties; the worker runs it."""
    # prints f"batch {id} started for tenant {slug}", or "nothing to do" when REINDEX for this matcher is complete
```

The pinned rows, inserted in name order: `("screening.periodic_rescreen", "0 2 1 * *", "America/Mexico_City", "screening.periodic_rescreen", True)` in `PLATFORM_SCHEDULES`, and the six-tuple with `{}` in the kernel-events tests. The kernel-events `slots` assertion excludes the monthly row, as plan 2c excludes the daily one. A new `next_slot("0 2 1 * *", "America/Mexico_City", after=…)` check replaces it. `test_a_new_tenants_hourly_jobs_run_through_the_worker` does not gain the row, because the monthly slot is never due in that test.

- [ ] **Step 1: Write the failing tests** (`test_walkers.py`):
  - `test_a_private_list_version_rescreens_the_whole_base`: parties near and far from the LPB entries. Every `ACTIVE` party gets one `PRIVATE_LIST` run. Findings exist only for the near ones. A second version adding one entry creates exactly one new finding, because the earlier ones are deduplicated.
  - `test_a_removed_private_entry_suggests_delisting`
  - `test_a_private_list_batch_screens_only_its_tenants_parties`: tenant B, with a party of the same name, gets no run.
  - `test_a_superseded_version_stops_its_batch`
  - `test_the_periodic_job_opens_one_batch_per_month`: a second `{}` job in the same Mexico City month is a no-op. A job across the month boundary (clock patched to 2027-01-01T07:30Z, which is 01:30 in Mexico City on 1 January) opens `2027-01`.
  - `test_the_periodic_walk_screens_every_active_party_against_the_full_list_set`
  - `test_an_initial_list_batch_is_walked_as_periodic`: 3a's `initial:<code>` batch.
  - `test_a_walker_resumes_from_its_cursor_after_a_crash`: the page handler raises after half the parties (monkeypatched `screen` raising on the 3rd party). The job retries and every party ends with exactly one run of the batch. The batch `done` equals the party count.
  - `test_ensure_screening_index_opens_a_reindex_for_a_new_matcher_version`: rows with `matcher_version = "1.0.0"`. The first call opens a batch and the second opens nothing. The walk re-indexes (rows now at `MATCHER_VERSION`) and gives one `REINDEX` run each.
  - `test_ensure_screening_index_backfills_parties_without_an_index_row`
  - `test_the_worker_start_runs_the_index_check` (`build_worker().run(max_ticks=0)` opens the batch)
  - `test_the_schedule_names_the_handlers_kind` (`tenants_service.SCREENING_PERIODIC_SCHEDULE == PERIODIC_RESCREEN_KIND`)
  - `tests/test_cli.py`: `screening rescreen --tenant acme` prints the batch line and exit 0. `--kind REINDEX` twice prints "nothing to do" the second time once that batch is complete. `--tenant nope` exits 1 with `error: NOT_FOUND: no tenant 'nope'` on stderr. `--help` works without a database.
  - Pinned schedule tests updated, and `test_config` gains the cron field. An invalid cron is refused by `_check_cron`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement**, including the pinned-test edits in the same commit.
- [ ] **Step 4: Run to verify pass**, then the gates and `make ci`.
- [ ] **Step 5: Docs.** Add the README configuration row and CLI line. Update the `schedule_platform_jobs` docstring (the monthly schedule in Mexico City).
- [ ] **Step 6: Commit** `feat(screening): private-list, periodic and re-index walkers with the monthly schedule`.

---

### Task F5: Purge redaction

Depends on 3b only: the `screening` consumer, `PartyNameIndex`, `ScreeningSubject`, `ScreeningRun`, `ScreeningHit` and the features shape of spec §8.4. It runs in parallel with F1 to F4. Console: no.

**Files:**
- Create: `src/xolotl/modules/screening/rescreen/purge.py`
- Modify: `rescreen/__init__.py` (`register_subscriber(PARTY_PURGED, on_party_purged, consumer=<3b's screening consumer name>)`)
- Conditional: `src/xolotl/migrations/versions/0011_screening_rescreen.py` and `tests/platform/test_migration_chain.py`, only if 3b's grants lack `UPDATE (features)` on `screening_hit` or `DELETE` on `party_name_index` (Global Constraints)
- Test: `tests/modules/screening/rescreen/test_purge.py`

**Produces:**

```python
def redact_features(features: dict[str, JsonValue]) -> dict[str, JsonValue]
    # pure: query_name -> "", alignment[i].query -> "", unmatched_query -> [], "redacted": True; everything else as is; idempotent
def on_party_purged(session: Session, event: Event) -> None
    # party_id = UUID(event.data["party_id"]); DELETE party_name_index WHERE party_id;
    # subject = PARTY subject of that party (none -> return); UPDATE each hit of the subject's runs whose features lack "redacted"
```

- [ ] **Step 1: Write the failing tests:**
  - `test_redact_features_blanks_the_query_side_only` (pure; golden before and after on 3b's explanation golden)
  - `test_redaction_is_idempotent`
  - `test_a_purged_partys_tokens_are_gone_and_its_record_stays`: create, index and screen a party with a hit. Run 2c's `RetentionSweeper.sweep` with the clock past retention, then dispatch the bus. Afterwards there are no index rows. Every hit has `redacted` set, its score, band and `entry_ref` unchanged. The finding and its dispositions are intact. The party's name is fictional and the test's list entry is built in code with an RFC equal to the party's (so the hit comes from the identifier rule, not the name). `leaked(everything_stored(admin_engine), <the party's folded surname>)` is then empty.
  - `test_redelivering_the_event_changes_nothing` (at-least-once dispatch: the second run deletes and updates nothing)
  - `test_a_party_never_screened_is_purged_without_error`
  - When the conditional migration exists, `test_migration_0011_grants_only_what_redaction_needs`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates and `make ci`.
- [ ] **Step 5: Docs.** Write the module docstring: what is kept as the compliance record, and why.
- [ ] **Step 6: Commit** `feat(screening): redact a purged party's name index and hit tokens`.

---

### Task F6: `screening eval`, the evaluation endpoint and the Evaluación page

Depends on LIB-corpus (`evaluate`, the loader, floors) and on 3b (`routes_screening.router`, `screening_app`, the console screening feature and its access entry). It runs in parallel with F1 to F5. Touches `console/`. Console: **yes**. Per the spec §20 cut list, the console page is the first thing to cut if SP-03 runs long. Its steps are last in this task so it can be dropped without touching the endpoint or the CLI.

**Files:**
- Create: `src/xolotl/modules/screening/evaluation_report.py`, `src/xolotl/modules/screening/data/evaluation.json` (generated by the CLI in this task)
- Modify: `routes_screening.py` (`GET /evaluation`), `src/xolotl/cli.py` (`screening eval`), `Dockerfile` if `data/` is not already in the image (it is package data: assert only)
- Console:
  - Create: `console/src/features/screening/EvaluationPage.tsx`, `EvaluationPage.test.tsx`
  - Modify: `queries.ts` (+`useEvaluation`), `App.tsx` (route `/screening/evaluation`), `components/layout/nav.ts` (under Reglas, after Listas privadas), `i18n/es-MX.json`, `i18n/en.json`, `api/schema.d.ts` (regenerated with `npm run gen:api:file`)
- Test: `tests/modules/screening/test_evaluation_report.py`, `tests/test_cli.py` (append)

**Produces:**

```python
SHIPPED_REPORT = files("xolotl.modules.screening") / "data" / "evaluation.json"
class ListKindMetrics(BaseModel): list_kind: str; pairs: int; recall_review: Decimal; precision_alert: Decimal | None;
                                  retrieval_recall: Decimal; alerts_per_1000_negatives: Decimal
class Floor(BaseModel): metric: str; list_kind: str; floor: Decimal; value: Decimal; met: bool
class EvaluationReportResponse(BaseModel): matcher_version: str; generated_at: datetime; corpus_pairs: int;
                                           hard_negatives: int; kinds: list[ListKindMetrics]; floors: list[Floor]
def load_shipped_report() -> EvaluationReportResponse            # lru_cache; NotFoundError when the file is absent
@router.get("/evaluation")   # require_access() staff only (no scope); returns load_shipped_report()
def get_screening_evaluation(...) -> EvaluationReportResponse
```

CLI: `xolotl screening eval [--corpus PATH] [--write]` loads the corpus and runs `evaluate`. It prints one line per list kind and metric, then one `FLOOR <metric> <kind> <floor> <value> OK|BELOW` line each, and exits 1 when any floor is below. With `--write` (refused when a floor is below) it writes the canonical JSON (sorted keys, Decimals as strings, `generated_at` from `clock.now()`) to `src/xolotl/modules/screening/data/evaluation.json`. A missing corpus is `error: NOT_FOUND: no evaluation corpus at <path>` with exit 1.

- [ ] **Step 1: Write the failing tests.**
  - Backend:
    - `test_the_shipped_report_is_for_the_running_matcher` (`matcher_version == MATCHER_VERSION`)
    - `test_the_shipped_report_meets_every_floor`
    - `test_staff_read_the_evaluation_and_keys_cannot` (an `AUDITOR_RO` gets 200; any API key gets 403)
    - `test_the_report_is_valid_canonical_json` (rewriting it with the same content is byte-identical)
  - CLI, on a 50-pair corpus slice written to `tmp_path` by the library's generator with a fixed seed:
    - `eval` prints the lines and exits 0
    - a floors file set above the measured value makes it exit 1 and refuses `--write`
    - `--write` writes into a `tmp_path` copy (the path is patched)
    - a missing corpus gives the error line
  - Console (`EvaluationPage.test.tsx`):
    - renders the per-kind table and a met and a below floor chip from a mocked response
    - shows the matcher version and generation date
    - shows the empty state on 404
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the backend, then run `uv run xolotl screening eval --write` and commit the file.
- [ ] **Step 4: Run to verify pass.** Run the targeted tests and the gates, then `make ci`. Then the console gates: `cd console && npm ci && npm run gen:api:file && npm run typecheck && npm run lint && npm test && npm run build`.
- [ ] **Step 5: Docs.** Add the README "Screening evaluation" paragraph: how to regenerate the report, and that a matcher bump needs it. Spanish strings: "Evaluación del motor", "Umbral", "Cumple", "Por debajo".
- [ ] **Step 6: Commit** `feat(screening): screening eval, the evaluation endpoint and page`. The console part may be a second commit, `feat(console): screening evaluation page`.

---

### Task F7: Perf tests (marked, excluded from CI)

Depends on F3 and F4 (and so on 3b's `screen` and the holder). Console: no. This is the last item on the spec §20 cut list.

**Files:**
- Create: `tests/perf/{__init__,conftest}.py`, `tests/perf/test_sp03_perf.py`
- Modify: `pyproject.toml` (`markers = ["perf: performance measurement; excluded by default, run with make perf"]`, and `"-m", "not perf"` added to `addopts`, unless the library registered them), `Makefile` (`perf:` target: `uv run pytest tests/perf -m perf --no-cov -q`), `.gitignore` (`.perf/`)

**Produces:** `tests/perf/conftest.py::record(name, **measures)` appends one JSON object (name, measures, `MATCHER_VERSION`, `git rev-parse HEAD`, timestamp) to `.perf/sp03.json`. The synthetic data is seeded (`random.Random(20261019)`). Names come from the library's frequency tables and entries from the library's perturbation generator, so a run reproduces its own data exactly.

- [ ] **Step 1: Write the tests:**
  - `test_forward_screen_p95_at_50k_entries` (`NFR-SC-001`): 50,000 synthetic shared entries written as one `list_version` through 3a's writer, then the holder refreshed. 20 warm-up screens and 500 measured `screen(..., mode=AD_HOC)` calls on generated names. Asserts `p95 < 300 ms` and records p50, p95 and p99.
  - `test_delta_rescreen_of_a_10k_party_base` (`NFR-SC-003`): 10,000 parties created through `create_party` and `index_party_names` (seeded names, 5 % near list entries), then a delta of 50 added and 50 changed entries driven to completion through the runner. Asserts the batch is `COMPLETED`, the findings equal the expected near set, and the elapsed time is under 120 s. Records the elapsed time, runs and blocked parties per entry.
  - `test_periodic_rescreen_throughput` (`NFR-SC-004`): the same base and one periodic batch. Asserts every party has a run, and records parties per second. The only gate is `>= 20` parties/s, a floor generous enough for any laptop.
  - `test_perf_tests_are_excluded_by_default`: a normal-suite test (not marked) in `tests/test_toolchain_pins.py` that asserts `"not perf"` in `addopts` and the marker registered.
- [ ] **Step 2: Run** `make perf` locally. Record the numbers in the PR body (machine, Postgres container, results). `make ci` must not collect them: check with `uv run pytest --collect-only -q | grep perf` → nothing.
- [ ] **Step 3: Docs.** Add a README "Performance tests" line: `make perf` runs them, they are not in CI, SP-11 gates them under load.
- [ ] **Step 4: Commit** `test(screening): perf measurements for screening and re-screening`.

---

### Task F8: The SP-03 exit test

Depends on F1, F3 and F4, and on the whole of 3a and 3b being merged. It merges **last**, after F5, F6 and F7, so that the run it records is SP-03 as shipped. Console: no.

**Files:**
- Create:
  - `tests/e2e/__init__.py`
  - `tests/e2e/conftest.py`: the `drain(services)` helper. It loops `services.bus.dispatch_pending()` and `services.job_runner.run_once()` until both return 0, with a guard of 200 rounds that fails the test.
  - `tests/e2e/test_sp03_exit.py`
  - `tests/e2e/fixtures/README.md`
  - `tests/e2e/fixtures/un_consolidated_v2.xml`: the library's trimmed UN fixture with one entry added, one changed (a new alias) and one removed. The README names the three `DATAID`s and the derivation.
  - `tests/e2e/fixtures/lpb_tenant_a.csv`: our template (spec §7), with fictional names and valid RFC and CURP shapes.

**Produces:** one test, `test_sp03_exit`, written as numbered sections that each print a short progress line on failure, plus one helper module-level `FIXTURES` mapping list codes to the library's fixture paths. The sections follow spec §16 exit criteria and the program spec SP-03 row:

1. **Two tenants.** Create `acme` and `beta` with `new_tenant()`. Assign the ITF pack to both (`install_both_packs`, then the assignment as plan 2a's tests do). Give each a staff `ADMIN`, an `MLRO`, a `SENIOR_ANALYST` and an `ANALYST`, plus an API key with `screening:write screening:read parties:write`. Give each a wildcard webhook endpoint (`event_types = ["*"]`, pointing at `silent_server`).
2. **Shared lists.** `xolotl lists load-file` through `CliRunner` for `UN_CONSOLIDATED`, `OFAC_SDN`, `EU_FSF`, `UK_FCDO` and `MX_SAT_69B`. Enqueue `lists.poll` for both tenants and `drain`. For each tenant, `GET /v1/lists` shows the five codes `stale: false` with `seq == 1`, and `MX_LPB` absent from shared lists.
3. **LPB.** Tenant A's MLRO creates `MX_LPB` and uploads `lpb_tenant_a.csv`, then `drain`. `GET /v1/private-lists` lists it with `entry_count` equal to the CSV rows.
4. **Parties.** Tenant A creates P_LPB (a perturbed LPB name: dropped second surname, accent loss), P_OFAC (a perturbed OFAC fixture person), P_CHG (near the UN entry that v2 changes), P_REM (near the UN entry v2 removes), P_ADD (near the UN entry v2 adds) and P_CLEAR (a hard-negative name). Tenant B creates B_TWIN, whose name equals an LPB row of tenant A exactly, then `drain`. 3b's subscribers index and screen them.
5. **An explained hit.** `POST /v1/screening/checks {party_id: P_OFAC}` as tenant A's analyst returns `hits[]` with an `ALERT` whose features show aligned tokens, qualifiers, the list version seq and `MATCHER_VERSION`. The same call with tenant A's API key returns exactly `{run_id, subject_id, status: "PENDING_REVIEW", screened_at}`, with no `hits` key. P_LPB shows an `ALERT` on `MX_LPB`.
6. **Delta.** Record tenant A's findings. Load `un_consolidated_v2.xml`, enqueue `lists.poll`, `drain`. The `LIST_DELTA` batch is `COMPLETED`. The new findings are exactly one for P_ADD and one for P_CHG, P_CHG's older finding is `SUPERSEDED`, and P_REM's finding carries `suggested_disposition == "DELISTED"`. P_LPB, P_OFAC and P_CLEAR got no `LIST_DELTA` run.
7. **Tenant B sees nothing.** `GET /v1/private-lists` is empty. `GET /v1/screening/matches` holds no finding on `MX_LPB`. `GET /v1/screening/runs/{A's run}` is 404. B_TWIN's screening status is `CLEAR` with no hit on `MX_LPB`. B's batches list holds only its own.
8. **Four-eyes.** Tenant A's analyst escalates P_LPB's finding and the same analyst confirming it is 409 `SAME_REVIEWER`. The MLRO confirms `TRUE_MATCH`. This is the demo's last step, and it proves the review path on the exit data.
9. **No tipping-off.** No `webhook_delivery` row of either tenant has an event type starting `screening.match_`, `private_list.` or `screening.true_match`. Tenant A's endpoint did receive `screening.status_changed.v1`, which proves the wildcard endpoint was live.
10. **Audit.** `GET /v1/audit/verify` for each tenant reports intact.

- [ ] **Step 1: Write the test and the fixtures.** It fails only where a dependency is missing.
- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_sp03_exit.py --no-cov -q`. It must stay under 90 s on CI. If it does not, trim fixtures, not checks.
- [ ] **Step 3: Run `make ci`.** The test is part of the default suite (not marked).
- [ ] **Step 4: Docs.** Add a README "SP-03 exit" line: what the test proves and how to run it.
- [ ] **Step 5: Commit** `test(e2e): SP-03 exit, two tenants, lists, LPB, explained hit, delta, isolation, audit`.

---

## Parallelism

| Id | Title | Depends on | Console | Branch slug |
|---|---|---|---|---|
| F1 | Batch bookkeeping, delisting suggestions, batches API, `screen()` hooks | 3b: `screen()`, models, router, events, `register_screening` | no | `sp03c-f1-batches` |
| F2 | Reverse blocking in Postgres with corpus recall | LIB-matching, LIB-corpus; 3b: `party_name_index`, `index_party_names`, holder IDF | no | `sp03c-f2-reverse-blocking` |
| F3 | List-delta re-screen job | F1, F2; 3a: deltas, entries, catch-up enqueue; 3b: `screen()`, holder | no | `sp03c-f3-list-delta` |
| F4 | Private-list, periodic and re-index walkers, schedule, `screening rescreen` | F1; 3a: private versions and deltas, upload enqueue, initial batch; 3b: `screen()`, `index_party_names` | no | `sp03c-f4-walkers` |
| F5 | Purge redaction | 3b: party subscribers' consumer, hits, name index (grants) | no | `sp03c-f5-purge-redaction` |
| F6 | `screening eval`, evaluation endpoint and page | LIB-corpus; 3b: router, `screening_app`, console screening feature | **yes** | `sp03c-f6-evaluation` |
| F7 | Perf tests | F3, F4 | no | `sp03c-f7-perf` |
| F8 | SP-03 exit test | F1, F3, F4; all of 3a and 3b; merged last | no | `sp03c-f8-exit` |

- **Wave 1** (once 3b's engine tasks are merged): F1, F2, F5 and F6 in parallel. F5 and F6 touch no file F1 or F2 touch, except F6's `cli.py` and `routes_screening.py` lines against F1's `routes_screening.py` route. Both edits are additive, so the second to merge rebases.
- **Wave 2:** F3 and F4 in parallel. Both add one line to `rescreen/__init__.py`, and F4 alone touches the schedule, the pinned tests, `app.py`'s `worker_loop` and `cli.py`.
- **Wave 3:** F7, then F8.
- **Shared files across SP-03 plans:** `src/xolotl/app.py`, `src/xolotl/cli.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `src/xolotl/modules/tenants/service.py`, the pinned schedule tests (3a adds `lists.poll` and F4 adds `screening.periodic_rescreen`), `pyproject.toml` (the library and F7 may both add the `perf` marker), and the console `nav.ts`, `App.tsx` and catalogues. Every edit is additive. The later task rebases and re-applies its own lines. `schema.d.ts` is regenerated, never merged by hand.

## Exit criteria

SP-03 spec §16 (the `rescreen/*` row and the exit paragraph) and the program spec SP-03 row, refined for this plan:

- `make ci` is green after every task, with the library's corpus floors met. The console gates pass after F6.
- A shared list delta re-screens only the parties blocked near its added and changed entries. It creates findings only for those entries, supersedes the changed entry's older finding and suggests `DELISTED` on removed and 69-B `DESVIRTUADO` or `SENTENCIA_FAVORABLE` entries with one staff-only event each (F3). Postgres blocking retrieves at least 0.995 of the corpus's true pairs (F2).
- A new private-list version re-screens the whole base of its tenant only (F4). A monthly periodic batch per tenant walks every active party against the full list set, and a matcher change or a missing index row opens a re-index at worker start (F4). Every batch resumes from its cursor after a crash, and its progress shows at `GET /v1/screening/batches` (F1, F4).
- A purged party has no name-index row and no query-side token in any hit, and its findings, scores and entry references stay (F5).
- `xolotl screening eval` reports per list kind and fails below a floor. `GET /v1/screening/evaluation` serves the committed report for the running matcher, and the Evaluación page shows it (F6).
- `make perf` records forward-screen p95 at 50,000 entries under 300 ms, and the 10,000-party delta and periodic throughput (F7, not in CI).
- `tests/e2e/test_sp03_exit.py` passes in `make ci`, covering two tenants, five shared lists ingested and fresh, tenant A's LPB, an explained `ALERT` for staff with status only for the API client, a delta that alerts only on the added and changed entries and suggests delisting the removed one, tenant B seeing nothing and never hitting A's LPB, no `screening.match_*` webhook delivery, and audit verify intact for both tenants (F8).

## Open questions for the product owner

1. **Ad hoc subject expiry.** `screening_subject.retention_until` is set for `AD_HOC` subjects (spec §8.2), but no plan of SP-03 purges them when it passes. *Default:* leave it to SP-03's follow-up or SP-06, with the input staying encrypted until then. A small daily sweep beside 2c's retention sweep is about a day of work if wanted now.
2. **Overflowing entries.** An entry blocked to more than 5,000 parties waits for the next monthly periodic run, as spec §10.2 says. With the accepted monthly cadence, that wait can be up to a month. *Default:* accept it, because the corpus never produces such an entry on regional names, and record it in the batch. The alternative is to open an immediate `PERIODIC` batch restricted to that entry.
3. **Re-screen trigger for a recommended list re-enabled by ADMIN.** Turning on `EU_FSF` after months off does not re-screen the base against it until the next monthly run. *Default:* accept, since the next delta or monthly run covers it. The alternative is that 3a's subscription toggle opens an `initial:<code>`-style batch for that tenant.
