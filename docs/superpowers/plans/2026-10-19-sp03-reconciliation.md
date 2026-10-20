# SP-03 plans: cross-plan reconciliation record

The three SP-03 plans (`2026-10-19-sp03a-list-catalog.md`, `2026-10-19-sp03b-screening-engine.md`, `2026-10-19-sp03c-rescreening-exit.md`) were written in parallel by three writers who could not see each other, against the SP-03 spec (`docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md`, whose departures and open-question defaults the product owner accepted on 2026-10-19). Each plan marked what it guessed about the others as *(assumed)* or "3a: …". A cross-plan check compared the seams between them, and the seams with the screening library (`docs/superpowers/plans/2026-10-19-screening-library.md`, tasks T1 to T6, built by another engineer). This file records the sixteen findings below, the decision taken on each, and the sections edited.

## State

- **Applied.** Every finding is applied to the plans committed next to this file. The edits were exact string replacements with match counts checked (3a: 29, 3b: 34, 3c: 61). Each plan keeps all its task sections, its Parallelism, Exit criteria and Open questions sections. No *(assumed)* marker and no "3a: <what>" or "3b: <what>" placeholder is left; every cross-plan reference names a task id and an exact symbol.
- **The library plan and code are untouched.** Where our plans assumed a library name, they now use the library plan's (and, for T1, which merged in PR #52, the code's). What our plans need and the library plan does not provide is listed in finding 15 for the library owner; nothing is planned into the library.
- **The execution kit** is `docs/superpowers/execution/sp03/`. `tasks.json` carries the dependency edges below, including the library prerequisites as `external` rows; `check-graph.mjs` validates the graph.
- **Not yet reviewed independently.** A reviewer should read the edited task sections against these findings before the tasks they touch start; a mismatch is fixed in a small docs PR.

## Findings

| # | Finding | Decision |
|---|---|---|
| 1 | Who creates re-screen batches and enqueues their jobs | 3c's consumers only: F3 for `lists.version_published.v1` deltas, F4 for initial versions and private versions; 3a only publishes |
| 2 | Three different `screen()` signatures | One signature owned by 3b's E5 with `only_index`, `require_versions` and `batch_id`; 3c changes nothing in it |
| 3 | Model, function and helper names guessed across plans | 3a's names everywhere; new 3a helpers `ingest_parsed`, `write_private_version`, `load_shared`, `load_private` and named factory helpers |
| 4 | 3a's staff-only event and 3b's guard | D6 needs only E1, which has no dependency; D6 registers its own type |
| 5 | Migration chain and 3c's conditional 0011 | `0008_party` → `0009_lists` (D1) → `0010_screening` (E2), no 0011; E2 pins the privileges F5 needs |
| 6 | Shared files several tasks append to | Ownership table below; first to merge creates, the other appends |
| 7 | 3c's open questions | Kept open; checked that nothing contradicts them; Q3 gains what 3a's catch-up actually does |
| 8 | 3c used holder methods that 3b does not have | `get_index_holder().shared(s, require=...)` and `SharedShard.blocking_idf` |
| 9 | `BatchKind`/`BatchStatus` defined twice | 3b's E2 owns them in `engine/enums.py`; F1 imports |
| 10 | The `rescreen` package and the registration points | E8 creates the package; `register_rescreen` (F1) for 3c's jobs and catalog consumers; the purge subscriber in `register_screening` (F5) |
| 11 | `publish_screening_event` defined twice with different `key` types | One signature, `key: str`, `policy_snapshot_id` optional; D3 or E2 creates the file |
| 12 | Duplicate UN v2 fixture and library fixture path | F8 reuses 3a's `un_v2.xml`; the library fixture is `tests/modules/screening/fixtures/un/consolidated.xml` |
| 13 | Library task ids and the already-merged T1 | Every library dependency names its task (T1 … T6); T1's "if not merged" branches removed |
| 14 | Missing or wrong cross-task edges | E3→D4, D6; E10→D7; F1→E5, E7, E8; F4→F3; F5→E8 only; F6→E7, E8, E9; F7, F8 library edges; F3 no longer needs D5 |
| 15 | What our plans need that the library plan does not name | Six gaps for the library owner (below); not planned into the library |
| 16 | Smaller exact-name fixes | `RunResult.run.new_match_count`, `LIST_CODE_UNSUPPORTED`, flat nav for F6, the agreement path |

### 1. Batch creation and job enqueue have one owner: 3c's consumers

- **Finding.** Spec §6.4 has 3a's `lists.poll` insert `screening_batch` rows and enqueue `screening.list_delta`; 3a's Deviation 1 instead publishes `lists.version_published.v1` (with `initial`) and `private_list.version_activated.v1`, because `screening_batch` is 3b's 0010. 3b's "What 3a needs" still said 3a's catch-up and activation insert batches and need E2. 3c's dependencies said 3a enqueues `screening.list_delta`, `screening.periodic_rescreen` and `screening.private_list_rescreen` (with an optional `batch_id`), and F3's test `test_the_job_opens_its_batch_when_the_payload_has_none` covered "3a enqueueing before the batch exists".
- **Decision.** 3a publishes the two events and touches no 0010 table (its Deviation 1 stands). 3c's consumer `screening` is the only code that opens batches and enqueues their first page, in the dispatcher's transaction (exactly once per consumer through `event_consumption`; a redelivery finds the batch by `(kind, ref)` and the page key already queued):
  - **F3** registers `on_list_version_published`: `initial: false` → `open_batch(LIST_DELTA, ref=str(version_id))` and `screening.list_delta` page one with key `screening.list_delta:<batch_id>:0`, payload `{batch_id, version_id, offset: 0}`.
  - **F4** adds its `initial: true` branch (`PERIODIC` batch, ref `initial:<list_code>`, `screening.periodic_rescreen` page one) and registers `on_private_list_version_activated` (`PRIVATE_LIST` batch, ref `str(version_id)`, `screening.private_list_rescreen` page one).
  - `batch_id` is now always in page payloads (except the schedule's `{}`), so the "optional `batch_id`" rule and the handlers' open-by-payload branch are gone. F4 therefore depends on F3 (it edits F3's consumer); it no longer runs beside F3.
- **Edited.** 3a: "Interfaces this plan exposes" (Events bullet). 3b: "From plan 3a" (what 3a takes from this plan), Parallelism "Across plans". 3c: Architecture, Global Constraints (page keys, payloads, new "Catalog-event consumers" bullet, batches ref), "Dependencies outside this plan" (3a), F3 (header, Files, Consumes, Produces, the batch test, the end-to-end test wording), F4 (header, Files, Produces, two new consumer tests replacing `test_an_initial_list_batch_is_walked_as_periodic`), the Parallelism table and Wave 2.

### 2. One `screen()` signature, owned by E5

- **Finding.** Spec §8.3: `screen(session, subject, *, mode, trigger, lists=None, only_entries=None)`. 3b's E5 added `require_versions`; 3c's F1 planned to add `batch_id` and `only_index` to 3b's file, with "engine hook" tests appended to 3b's test module.
- **Decision.** E5 owns the only signature: `screen(s, subject, *, mode, trigger, lists=None, only_entries=None, only_index=None, require_versions=None, batch_id=None) -> RunResult`. `only_entries` and `only_index` together raise `ValueError`; `only_index` takes candidates per kind group with `top_k_for(kind)`; `restricted_to_delta` is true with either; `batch_id` is written to `screening_run.batch_id`. The three tests move to E5 (`test_only_index_scores_against_the_given_index_and_marks_the_run_restricted`, `test_only_entries_and_only_index_are_exclusive`, `test_a_run_records_its_batch`). F1 no longer edits `screen()`. F3 passes `only_entries`, `require_versions={list_code: seq}` and `batch_id`; F4's private walker passes `lists={list_code}`, `only_index` and `batch_id`.
- **Edited.** 3b: E5 Produces, step 3, step 4, the test list; "Interfaces for plan 3c". 3c: Global Constraints (Runs), the two Deviations marked "Reconciliation item", file structure, F1 (header, Files, the hook tests removed), F3 and F4 Produces.

### 3. Names: 3a's models, readers, writers and test helpers

- **Finding.** 3b wrote `PrivateListEntry` for 3a's `PrivateListEntryRow`, `ListVersionRow`/`PrivateListVersionRow`, a `tests/modules/screening/catalog_support.py::load_shared/load_private` that 3a does not have, and "if 3a defines no `ListKind`". 3c wrote `ListEntryDeltaRow`, `PrivateListEntryDeltaRow`, `private_entry(s, row)`, `LISTS_POLL_KIND` *(assumed)* and "3a's lower-level writer if it has one". 3a's `support.py` had only `seed_sources`, `make_version` and `ingest_fixture`, none of which loads hand-built `ListEntry` values, which 3b's E3, E5, E8 and 3c's F3, F4, F7 need.
- **Decision.** 3a's names everywhere: `ListSource`, `ListVersion`, `ListEntryRow`, `ListEntryDelta`, `TenantListSubscription`, `PrivateList`, `PrivateListVersion`, `PrivateListEntryRow`, `PrivateListEntryDelta` (`screening.catalog.models`); `ListKind`, `VersionStatus` (`screening.enums`); `private_entry_of` (`catalog.private`); `LISTS_POLL_KIND` (`catalog.poll`). 3a gains four small public pieces so the other plans write lists the same way 3a does:
  - **D4** `ingest_parsed(session, parsed, *, data, store, loaded_by, fetched_at, accept_drop=False) -> IngestReport` (everything `ingest_file` does after `parse`), and `support.load_shared(code, entries, *, store) -> IngestReport` over it (test `test_ingest_parsed_writes_hand_built_entries`);
  - **D6** `write_private_version(s, store, *, private_list, entries, data, content_type) -> PrivateListVersion` (everything after validation), and `support.load_private(session, store, list_code, entries) -> PrivateListVersion` over it;
  - **D1** names its factory helpers `_private_list`, `_private_list_version`, `_private_list_entry` (beside `_list_source_code`), which 3b's E2 factories call.
- **Edited.** 3a: Interfaces (Write functions, Test support), File structure, D1 factories paragraph, D4 Produces and support paragraph, D6 Files, Produces and rules paragraph. 3b: Dependencies table, "Assumed from plan 3a" rewritten as "From plan 3a", E3 header, `indexed_private_entry` signature, the holder test comment. 3c: "Dependencies outside this plan" (3a), F3 conftest helper, F4 Produces, F7 test.

### 4. Staff-only events: D6 needs E1 only, E1 needs nothing

- **Finding.** 3a's D6 publishes the staff-only `private_list.version_activated.v1` and depended on a placeholder "PLAN-3b-staff-only". 3b said webhooks withhold it only once E1 **and** E3 (`register_screening`) are merged, so "3a's activation task should merge after E3".
- **Decision.** D6 depends on **E1** and calls `register_staff_only(PRIVATE_LIST_VERSION_ACTIVATED)` itself in `register_lists`, so E1 alone guarantees no webhook carries it; E3's `register_screening` registers it again (idempotent). E1 has no dependency and is in the first wave. No 3a task depends on E2 or E3.
- **Edited.** 3a: "Dependencies outside this plan" (the E1 row and the paragraph under it), D6 header and Consumes, the task table, Parallelism. 3b: "From plan 3a", Parallelism "Across plans".

### 5. One migration chain, no 0011

- **Finding.** 3c's F5 planned a conditional `0011_screening_rescreen` granting `UPDATE (features)` on `screening_hit` and `DELETE` on `party_name_index` "if 3b's 0010 does not". 3b's E2 calls `enable_rls(table)` with the kernel default `grant=DML_PRIVILEGES` (`SELECT, INSERT, UPDATE, DELETE`, `xolotl.platform.tenancy.ddl`) on every 0010 table except `match_disposition` (`APPEND_ONLY_PRIVILEGES`), so the grants already exist.
- **Decision.** The chain is `0008_party` → `0009_lists` (D1) → `0010_screening` (E2), and 0010 is SP-03's last migration. E2 pins the two privileges in `test_the_app_role_may_update_hits_and_delete_name_index_rows`. F5 has no conditional migration and no migration test; 3c's Global Constraints say no 3c task adds a migration.
- **Edited.** 3b: Global Constraints (Migration), E2 test list. 3c: Global Constraints (first bullet), F5 Files and tests.

### 6. Shared files: who creates each

The rule is the SP-02 one: edits are additive, the second task to merge rebases and re-applies its own lines, `console/src/api/schema.d.ts` is regenerated and never merged by hand. Where two plans may each create a file, the first to merge creates it with the content both plans name, and the other appends.

| File | Created by | Appended by |
|---|---|---|
| `src/xolotl/modules/screening/__init__.py`, `tests/modules/screening/__init__.py` | library T1 (on `main`) | none |
| `src/xolotl/modules/screening/enums.py` | D1 | none (3b's enums are `engine/enums.py`, E2) |
| `src/xolotl/modules/screening/events.py` | D3 or E2, whichever merges first, with the finding-11 signature | the other; D6 (none new: D3 defines its constant) |
| `src/xolotl/modules/screening/errors.py` | D4 or E3, whichever first | D6, E6, E8 |
| `src/xolotl/modules/screening/schemas.py` | D7 or E7, whichever first | F1 (`BatchResponse`), F6 |
| `src/xolotl/modules/screening/composition.py` | E3 | E8 (subscribers, job, `queue`), F5 (purge subscriber) |
| `src/xolotl/modules/screening/rescreen/__init__.py` | E8 (docstring only) | F1 (`register_rescreen`), F3, F4 |
| `src/xolotl/modules/screening/routes_screening.py` | E7 | F1 (`GET /batches`), F6 (`GET /evaluation`) |
| `tests/modules/screening/catalog/support.py` | D2 | D3, D4, D6 |
| `src/xolotl/app.py` (`wire_platform`, routers, `worker_loop`) | — | D3, D5, D6, D7 (3a); E3, E7, E8 (3b); F1 (`register_rescreen` after `register_screening`), F4 (`ensure_screening_index` in `on_start`) |
| `src/xolotl/cli.py` | — | D2 (`lists_app`), D4, D5; E8 (`screening_app`); F4 (`rescreen`), F6 (`eval`) |
| `src/xolotl/platform/config.py`, `.env.example`, README configuration table | — | D1 (seven `LISTS_*`/`PRIVATE_LIST_*`), E3 (two `SCREENING_INDEX_*`/`PRIVATE_SHARDS`), F1 (two batch sizes), F4 (periodic cron) |
| `src/xolotl/platform/errors.py`, `platform/api/problems.py` | — | D6 (`ErrorItem`, `errors`), E6 (`blocked_reasons`) |
| `src/xolotl/modules/party/service.py` | — | E4 (`screening_profile`), F4 (`party_ids_after`) |
| `src/xolotl/modules/tenants/service.py` and the pinned schedule tests | — | D3 (`lists.poll`), F4 (`screening.periodic_rescreen`) |
| `.importlinter` | library T1 (its two contracts) | D1, D6 (catalog contract), E4 (engine contract) |
| `tests/isolation/factories.py`, `test_migration_chain.py` | — | D1, E2 |
| Console `auth/access.ts`, `components/layout/nav.ts`, `App.tsx`, both catalogues, `catalogue.test.ts` | — | D8 (`lists`, `listsAdmin`, `nav.lists`), D9 (`privateLists`, `nav.privateLists`), E9 (`screening`, `dispositions`, `whitelistRevoke`, `nav.matches`), E10 (`screeningCheck`, `nav.screeningCheck`), F6 (`nav.screeningEvaluation`) |
| `console/src/features/screening/queries.ts` | E9 (E10 creates it if E9 has not merged) | E10, F6 |
| `pyproject.toml` `perf` marker | library T5's perf test or F7, whichever first | the other reuses it |

- **Edited.** 3a: Interfaces (Events), Parallelism "Shared files". 3b: "From plan 3a". 3c: Global Constraints (package bullet), file structure, F1, F5.

### 7. 3c's open questions stay open and consistent

- **Ad hoc subject expiry (Q1).** E4 sets `screening_subject.retention_until`; no SP-03 task purges `AD_HOC` subjects. Nothing in 3a or 3b contradicts it.
- **Overflowing entries wait for the monthly run (Q2).** F2's `BLOCKING_CAP` and F3's `OVERFLOW:<n>` note; the monthly cadence is the accepted spec §18 Q4 default. Consistent.
- **A re-enabled recommended list (Q3).** Checked against 3a: a disabled subscription keeps its `last_seq` and is not caught up (D3 `test_a_disabled_subscription_is_not_caught_up`), so on re-enabling, the versions published meanwhile arrive as ordinary `initial: false` events and their deltas are re-screened; unchanged entries are not. Q3 now says so, and notes that its alternative (an `initial: true` event from the toggle) would need a ref with the version, since `initial:<code>` is already `COMPLETED`.
- **Edited.** 3c: Open question 3.

### 8. The holder's API in 3c

- **Finding.** 3c used `holder.snapshot().idf` *(assumed)* and `holder.ensure(version_id)`; 3b's E3 has `shared(s, require=...) -> SharedShard` with `blocking_idf` and `private(s)`.
- **Decision.** F2's `reverse_candidates` takes the `IdfTable` from its caller; F3 calls `get_index_holder().shared(s, require={list_code: seq})` and blocks with that shard's `blocking_idf`, and passes the same `require_versions` to `screen()`; F4 builds a private version's `MemoryIndex` with `get_index_holder().shared(s).blocking_idf`.
- **Edited.** 3b: "Interfaces for plan 3c" (holder bullet). 3c: "Dependencies outside this plan" (3b), F2 header, F3 Consumes and the paragraph after Produces, F4 Produces.

### 9. Batch enums

- **Decision.** `BatchKind` and `BatchStatus` are 3b's E2 enums in `screening.engine.enums`; F1's `batches.py` imports them and defines none.
- **Edited.** 3c: F1 Consumes and Produces.

### 10. The `rescreen` package and where things are registered

- **Finding.** 3b's E8 creates `rescreen/__init__.py` and `party_events.py` and said 3c registers its jobs in `composition.register_screening`; 3c's F1 "created" `rescreen/__init__.py` and registered everything in its own `register_rescreen`, and F5 extended F1's function while claiming to run beside F1.
- **Decision.** E8 creates the package (`__init__.py` with a docstring only). F1 adds `register_rescreen(*, queue)` there, called by `wire_platform` right after `register_screening`; F3 and F4 add their job handlers and catalog-event consumers to it. F5's `party.purged.v1` subscriber joins 3b's party subscribers in `register_screening` under `SCREENING_CONSUMER`, so F5 needs E8 only and really runs beside F1 to F4. `ensure_screening_index` runs in `app.py`'s `worker_loop` `on_start` (F4). F1 depends on E8.
- **Edited.** 3b: E8 Files and the composition comment, "Interfaces for plan 3c" (composition bullet). 3c: Architecture, Global Constraints (package and tests bullets), file structure, F1 header and Files, F5 header and Files, Parallelism Wave 1.

### 11. `publish_screening_event`

- **Finding.** 3a's D3 defined `publish_screening_event(..., key: str, data)`, 3b's E2 `(..., key: UUID, data, policy_snapshot_id=None)`, each "create or append".
- **Decision.** One signature, `publish_screening_event(s, event_type, *, entity, entity_id, key: str, data, policy_snapshot_id: UUID | None = None) -> Event`, pinned in both D3 and E2; 3b passes `key=str(subject_id)`. `EVENT_SOURCE` and `PRIVATE_LIST_VERSION_ACTIVATED` are D3's; E2 defines them only if D3 has not merged.
- **Edited.** 3a: Interfaces (Events), D3 Produces. 3b: "From plan 3a", E2 Produces.

### 12. Fixtures

- **Decision.** The library's UN fixture is `tests/modules/screening/fixtures/un/consolidated.xml` (on `main`). 3a's D4 derives `tests/modules/screening/catalog/fixtures/un_v2.xml` from it; 3c's F8 reuses that file as the UN second version instead of committing its own `un_consolidated_v2.xml`.
- **Edited.** 3a: "Dependencies outside this plan", D4 fixture paragraph. 3c: F8 Files, section 6, file structure.

### 13. Library task ids, and T1 already merged

- **Finding.** 3b named the library as "LIB-matching"/"LIB-lists", 3c as LIB-lists/LIB-matching/LIB-corpus, without task ids for most uses. Library T1 merged in PR #52 (`src/xolotl/modules/screening/{__init__,lists/*}.py`, `tests/modules/screening/__init__.py`, the fixture, two contracts), so 3a's "if D1 merges before library T1" fallback is dead.
- **Decision.** Each dependency names its library task, as the library plan splits them: T1 (`lists` types, `parse`, `diff`, UN parser), T2 (OFAC, EU), T3 (UK, SAT 69-B), T4 (`MATCHER_VERSION`, `normalize_name`, `NormalizedName`, `Token`, `TokenRole`, `phonetic_key`), T5 (`NameQuery`, `QueryIdentifier`, `IdfTable`, `blocking_plan`, `MemoryIndex`, `IndexedEntry`, `Candidate`, `score`, `ScoringConfig`, `MatchScore`, `MatchExplanation`, `Band`, `BandThresholds`, `band_for`), T6 (corpus, `evaluate`, floors). The kit's ids are `LIB-lists-T1`, `LIB-lists-T2`, `LIB-lists-T3`, `LIB-matching-T4`, `LIB-matching-T5`, `LIB-corpus-T6`. D1 creates neither `__init__.py`. Checked against the merged T1 code: `ListEntry`, `EntryName`, `EntryIdentifier`, `PartialDate`, `ParsedList`, `IdType` (with `OTHER`), `EntityType` (with `AIRCRAFT`), `ListFormatError(ValidationFailedError)`, `parse(list_code, data: BinaryIO)` and `diff(previous: Mapping[str, bytes], current: Iterable[ListEntry])` match what 3a consumes.
- **Edited.** 3a: "Dependencies outside this plan" and the paragraph under it, D1 Files and Test. 3b: Dependencies table, the task table. 3c: "Dependencies outside this plan", F2, F6, F7, F8 headers, the Parallelism table.

### 14. Cross-task edges made explicit

| Task | Edge added or changed | Why |
|---|---|---|
| E3 | + D4, D6 | the test loaders `load_shared` and `load_private` |
| E10 | + D7 | its list filter reads `GET /v1/lists`; the "hidden when unavailable" hedge is removed |
| F1 | 3b: E5, E7, E8 | `screen()` with `batch_id`, the router, the `rescreen` package and `SCREENING_CONSUMER` |
| F2 | E4 and LIB T4, T5, T6 | `index_party_names`, the matcher, the corpus |
| F3 | F1, F2; D3, D4; E5 | the version event, `load_shared`; D5 is not needed (the poll's tenant half is D3) |
| F4 | + F3; D6 | it edits F3's consumer; `private_entry_of`, `load_private`, the activation event |
| F5 | E8 only | finding 10 |
| F6 | E7, E8, E9; LIB T6 | router, `screening_app`, the console screening feature |
| F7 | + LIB T6 | the generator and frequency tables |
| F8 | F1, F3–F7, the rest of 3a and 3b, LIB T2, T3 | "merged last", and it loads the five shared lists |

E2 depends on D1 only (not on any 3a task that touches batches). 3c's F4 → F3 lengthens the longest chain to twelve tasks.

### 15. Library gaps (for the coordinator; not planned into the library)

The library plan fixes the names 3a and 3b consume; 3c needs more from T6 than T6 names. None is built in our plans; the consuming tasks use the names T6 ships and record them, or block if a capability is missing.

1. **Corpus loading** (F2, F6). T6 names `evaluate(corpus) -> EvaluationReport` but not how a corpus is loaded from `tests/modules/screening/corpus/`, nor its type.
2. **The labelled true pairs** (F2's Postgres recall test loads their query sides as parties). Needed: an accessor giving each pair's query, as `NameQuery` or its structured fields (names, given names, surnames, birth date, identifiers), and its `ListEntry`.
3. **Floors** (F6). A loader for `corpus/floors.yaml` and a check of a report against the floors (which metric and list kind fell below), so `xolotl screening eval` fails the same way CI does.
4. **Report serialization** (F6 commits `data/evaluation.json` and serves it). A stable JSON form of `EvaluationReport` with per-list-kind recall at review, precision at alert, retrieval recall, alerts per 1,000 negatives and the matcher version.
5. **The perturbation generator's public name** (F6's CLI test writes a 50-pair slice with a fixed seed; F7 generates synthetic entries). T6 puts it in `matching/evaluation.py` (library Decision 9) without naming it; also the frequency tables `data/surnames_mx.csv` and `data/given_names_mx.csv` without a loader name.
6. **The `perf` pytest marker** (F7). T5 adds "a non-gating perf test at 50,000 entries"; whichever of T5 and F7 merges first registers `perf` and `-m "not perf"` in `pyproject.toml`, and the other reuses them. The library owner should know F7 depends on that name.

Two points to confirm rather than gaps: the spec has `NameQuery.fingerprint()` as a method while the library plan lists `fingerprint` beside `NameQuery` in `matching/query.py` (3b's E5 and E8 call the method); and F2 relies on `party_name_index.phonetic_keys` storing the library's `name_keys` (spec §4.2), the same keys the `blocking_plan(...).key_sets` use.

### 16. Smaller exact-name fixes

- 3c summed `result.new_match_count`; `RunResult` has `run.new_match_count` (F3).
- The merged T1 `parse` raises `ListFormatError` with code `LIST_CODE_UNSUPPORTED` for a code with no parser yet (OFAC, EU, UK and SAT until T2 and T3 merge). 3a's `last_error` codes, its errors bullet and D8's error catalogue now include it.
- F6's nav entry is the flat `nav.screeningEvaluation` (`ACCESS.screening`) after `nav.privateLists`, since 3a's Deviation 11 records that the nav has no groups (3c had "under Reglas").
- The three plan headers and 3c's Global Constraints point at `docs/superpowers/execution/sp03/agreement.md`.
- 3a's enums bullet said "3b appends its own"; 3b's enums are in `screening.engine.enums`.
