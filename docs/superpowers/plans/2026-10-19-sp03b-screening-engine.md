# SP-03b — Screening engine and review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The working agreement `docs/superpowers/execution/sp03/agreement.md` (gates, worktrees, commits, PRs, report) applies to every task unchanged.

**Goal:** Screen parties and ad hoc subjects against the shared and private lists, and let staff review what was found. That means: staff-only event types that webhooks never carry; migration 0010 with the name index, subjects, list sets, runs, hits, findings, dispositions with the four-eyes trigger, whitelist and batches; the in-memory index holder; the party name index and subject queries; `screen()` with exact run records and stored explanations; dispositions, whitelist and `allowed_actions`; the screening API for both audiences; party event subscribers that re-index and re-screen; replay; and the console's match queue, match detail, party screening tab and ad hoc check.

**Architecture:** Everything lives in `src/xolotl/modules/screening/` beside the library (`lists/`, `matching/`, LIB) and plan 3a's `catalog/`. `engine/` holds the index holder, subjects and queries, `screen()`, runs, hits, findings and replay. `review/` holds dispositions, the whitelist and the action matrix. `rescreen/party_events.py` holds the party subscribers and the `screening.screen_party` job, and plan 3c adds its batch jobs beside it. Forward screening runs in memory: one `ScreeningIndexHolder` per process wraps the library's `MemoryIndex`, with a shared shard built from the active `list_entry` rows and per-tenant private shards decrypted inside the tenant's session. Reverse screening (plan 3c) is a Postgres query over `party_name_index`, which this plan builds and keeps current. Parties are read only through `xolotl.modules.party.service` functions, and this plan adds one of them, `screening_profile`, which decrypts for processing only. Writes run in the caller's transaction: rows, then audit, then ids-only events. Staff and API clients share the routes but get different response models; clients see status and stable codes only.

**Tech Stack:** the stack on `main` (SP-01, SP-02 plans 2a, 2b and 2c merged; 2c's C9 may still be open), plus the screening library and plan 3a. No new runtime dependency: `pg_trgm` is created by 3a's migration 0009, and the library brings its own string libraries.

**Spec:** `docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md`: §1 (the screening engine and API bullets), §2 (layout, import contracts, migration 0010), §3 (`SCREENING_INDEX_REFRESH_SECONDS`, `SCREENING_PRIVATE_SHARDS`), §4.2 (the interface consumed), §5.2 (the second block, migration 0010), §8 (index holder, subjects and queries, `screen()`, stored explanation, replay), §9 (dispositions and whitelist), §10.1 (party data change; the purge part belongs to 3c), §11 (events, the staff-only platform change, audit), §12 (the screening rows), §13 (`screening check`, `screening replay`), §14 (Coincidencias, Detalle de coincidencia, Cliente › Screening, Consulta ad hoc), §15 (FR-SC-001, 005 to 008, 013; NFR-SC-007), §16 (the `engine/*`, `review/*` and platform rows), §17 Decisions 2, 8 to 12 and 14, and §19 (the 3b row). Program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md`: D6 (decrypt boundary), D7 (`party_name_index`), D8 (ids-only events), D15 and D20 (`allowed_actions`, `blocked_reasons`, two audiences), D18 (findings, bands, whitelist key), §4 (`ScreeningIndex` port).

## Accepted by the product owner (2026-10-19)

- **Spec departures:** lists are folded into the screening module (spec Decision 1); staff-only event types that webhooks never deliver, a small platform change (spec §11, built here as E1); an in-memory index for forward screening and Postgres reverse screening (spec Decision 2).
- **Defaults of all seven open questions of spec §18:** (1) no licensed global sanctions or PEP database in SP-03; (2) **API clients see status only** (`CLEAR`, `PENDING_REVIEW`, `TRUE_MATCH`), with no per-tenant switch; (3) our own CSV/XLSX template for private lists; (4) a monthly full re-screen plus deltas and data changes; (5) **a 69-B name-only resemblance is `LOW_CONFIDENCE` at most**, never `ALERT`; (6) no `OFAC_CONS`; (7) domestic PEPs as a high-risk floor are left out of SP-03.

These are settled. Tasks build them and do not reopen them.

## Dependencies outside this plan

| Id | What | Consumed by |
|---|---|---|
| **LIB-matching** | The screening library's matcher (spec §4.2), built by the parallel package (`docs/superpowers/handoffs/screening-library.md`, plan `docs/superpowers/plans/2026-10-19-screening-library.md` tasks T4 and T5; kit ids `LIB-matching-T4`, `LIB-matching-T5`) under `src/xolotl/modules/screening/matching/`. T4 gives `MATCHER_VERSION`, `normalize_name`, `NormalizedName`, `Token`, `TokenRole` and `phonetic_key`; T5 gives the rest (`NameQuery` and `QueryIdentifier` are T5's `matching/query.py`). Symbols: `MATCHER_VERSION`, `NameQuery`, `QueryIdentifier`, `normalize_name`, `NormalizedName`, `Token`, `TokenRole`, `phonetic_key`, `blocking_plan`, `IdfTable`, `IndexedEntry`, `Candidate`, `MemoryIndex`, `ScoringConfig`, `score`, `MatchScore`, `MatchExplanation` (and `to_json`), `Band`, `BandThresholds`, `band_for`. | E3, E4, E5, E8 |
| **LIB-lists** | The library's list types (spec §4.1, plan T1, kit id `LIB-lists-T1`, merged on `main` in PR #52), from the same package: `ListEntry` (`canonical_json`, `content_hash`, `from_json`), `EntityType`, `IdType`. Plan 3a also consumes them. | E3, E4, E5, E8 |
| **3a** | Plan 3a (`docs/superpowers/plans/2026-10-19-sp03a-list-catalog.md`, tasks D1 to D9). What this plan takes from 3a is listed under "From plan 3a" below, with the 3a task that provides each. | E2, E3, E5, E10 |
| **2c: C9** | SP-02 plan 2c's last console task (`PartyCollections` on `PartyPage.tsx`). | E10 |

### From plan 3a

Names as plan 3a defines them (`docs/superpowers/plans/2026-10-19-sp03-reconciliation.md`, finding 3).
- **D1: migration `0009_lists`** (`revision = "0009_lists"`), which creates `pg_trgm` and the tables of spec §5.1 and the first block of §5.2: `list_source`, `list_version`, `list_entry`, `list_entry_delta`, `tenant_list_subscription`, `private_list`, `private_list_version`, `private_list_entry`, `private_list_entry_delta`. 0010 sets `down_revision = "0009_lists"`.
- **D1: ORM models** in `xolotl.modules.screening.catalog.models`: `ListSource` (`code`, `list_kind`), `ListVersion` (`id`, `list_code`, `seq`, `status`), `ListEntryRow` (table `list_entry`; the name `ListEntry` belongs to the library dataclass), `ListEntryDelta`, `TenantListSubscription` (`list_code`, `enabled`), `PrivateList` (`id`, `list_code`, `list_kind`), `PrivateListVersion` (`id`, `list_id`, `seq`, `status`), `PrivateListEntryRow` (table `private_list_entry`: `id`, `list_id`, `source_entry_key`, `content_tag`, `entity_type`, `from_seq`, `to_seq`, and the encrypted entry `entry`, 3a Deviation 3) and `PrivateListEntryDelta`.
- **D1: enums** in `xolotl.modules.screening.enums`: `ListKind` (`SANCTION`, `FISCAL`, `BLOCKED`, `PEP`, `INTERNAL`) and `VersionStatus` (`ACTIVE`, `SUPERSEDED`). This plan's enums live in `xolotl.modules.screening.engine.enums` (E2).
- **D1: isolation factory helpers** `_private_list_version(session, tenant_id) -> PrivateListVersion` and `_private_list_entry(session, tenant_id) -> PrivateListEntryRow` in `tests/isolation/factories.py`. E2's factories reuse them, because the app role cannot insert a `list_source` row, so a factory row of `screening_match` or `screening_hit` points at a private entry.
- **D4 and D6: test loaders** in `tests/modules/screening/catalog/support.py`: `load_shared(code, entries, *, store) -> IngestReport` (D4, over `ingest_parsed`; needs `seed_sources(admin_engine)` from D2 first) and `load_private(session, store, list_code, entries) -> PrivateListVersion` (D6, over `write_private_version`).
- **D3: `src/xolotl/modules/screening/events.py`** with `EVENT_SOURCE`, `PRIVATE_LIST_VERSION_ACTIVATED = "private_list.version_activated.v1"` and `publish_screening_event(s, event_type, *, entity, entity_id, key: str, data, policy_snapshot_id=None)`. Shared files: `src/xolotl/modules/screening/{events,errors,schemas}.py` are created by 3a's D3, D4 and D7 or by this plan's E2, E3 and E7, whichever merges first; the other appends (finding 6). `PRIVATE_LIST_VERSION_ACTIVATED` is defined once, in `screening/events.py`.
- **D7: `GET /v1/lists`**, for E10's list filter.

What 3a takes from this plan: only **E1**. 3a's D6 calls `register_staff_only(PRIVATE_LIST_VERSION_ACTIVATED)` itself in `register_lists`, so E1 alone keeps webhooks from carrying it; `register_screening` (E3) registers it again, harmlessly. 3a creates no `screening_batch` row: its catch-up and its private-list activation publish `lists.version_published.v1` and `private_list.version_activated.v1`, and plan 3c's consumers create the batches and enqueue the jobs (finding 1).

## Global Constraints

- Repository `/Users/omaribannez/code/xolotl`; one PR per task from `main`; `make ci` green at the end of every task; the console gates (`npm ci && npm run typecheck && npm run lint && npm test && npm run build`, plus the Playwright smoke) whenever `console/` changes.
- **Migration** `src/xolotl/migrations/versions/0010_screening.py`: `revision = "0010_screening"`, `down_revision = "0009_lists"`. It is this plan's only migration and the last of SP-03: plan 3c adds none, because `enable_rls`' default grant (`SELECT, INSERT, UPDATE, DELETE`) on every 0010 table but `match_disposition` already lets 3c's F5 update `screening_hit.features` and delete `party_name_index` rows (finding 5). `"0010_screening"` is appended to `CHAIN` in `tests/platform/test_migration_chain.py`, after 3a's `"0009_lists"`.
- **Ten tenant tables** exactly as spec §5.2 (second block): `party_name_index`, `screening_subject`, `screening_list_set`, `screening_list_set_member`, `screening_run`, `screening_match`, `screening_hit`, `match_disposition`, `screening_whitelist`, `screening_batch`. Each is `class X(TenantTableMixin, Base)` with `tenant_table_args`, composite FKs through `tenant_fk` or a named `ForeignKeyConstraint` where the naming convention's expansion would pass Postgres's 63-byte limit, `enable_rls("<table>")` in the migration, and one isolation factory registered in `register_all()`. The models are imported in `src/xolotl/models.py`. `match_disposition` joins `APPEND_ONLY_TABLES` and gets `grant=APPEND_ONLY_PRIVILEGES`. `SHARED_TABLES` is untouched (3a adds its four).
- **Encrypted columns** (`sa.LargeBinary()` in the migration): `screening_subject.input` (`EncryptedJson`; spec `input_enc`) and `match_disposition.rationale` (`EncryptedStr`; spec `rationale_enc`). No blind index. `party_name_index` holds plaintext normalized tokens only (D7). No route ever serializes it.
- **Import rules** (`.importlinter`; E4 adds the contract): `screening.engine`, `screening.review` and `screening.rescreen` may import `xolotl.modules.party.service`, `.enums`, `.identifiers` and `.errors`, but never `xolotl.modules.party.models`. `screening.catalog` never imports `xolotl.modules.party` (3a's contract). `xolotl.platform` imports nothing from modules. The tenants module never imports screening.
- **Settings** (E3) in `src/xolotl/platform/config.py`: `screening_index_refresh_seconds: int = 30` (`XOLOTL_SCREENING_INDEX_REFRESH_SECONDS`, ≥ 1) and `screening_private_shards: int = 64` (`XOLOTL_SCREENING_PRIVATE_SHARDS`, ≥ 1). Both go in `.env.example`, the README configuration table and the `model_fields_set` assertion of `tests/platform/test_config.py::test_env_example_is_a_valid_configuration`. Plan 3c adds `SCREENING_DELTA_BATCH`, `SCREENING_RESCREEN_BATCH` and `SCREENING_PERIODIC_CRON`.
- **Problem codes** introduced: `EVENT_TYPE_NOT_DELIVERABLE` (422, platform, E1); `SCREENING_INDEX_UNAVAILABLE` (503, E3); `DISPOSITION_NOT_ALLOWED` (409, with `blocked_reasons`, E6); `MATCHER_VERSION_MISMATCH` (409, replay, E8). Each gets a row in `tests/platform/test_errors.py`. Reused: `NOT_FOUND`, `VALIDATION_FAILED`, `POLICY_NOT_ASSIGNED`, `ROLE_FORBIDDEN`, `SCOPE_MISSING`, `IDEMPOTENCY_*`, `IDENTIFIER_INVALID`.
- **Events**: `source = "xolotl.screening"`, `subject = "screening_subject/<id>"` (or `screening_match/<id>` for match events), `key = str(subject_id)`, data ids and codes only, exactly the fields of spec §11: `screening.run_completed.v1`, `screening.status_changed.v1` (webhook-safe); `screening.match_created.v1`, `screening.match_dispositioned.v1`, `screening.true_match_confirmed.v1`, `screening.match_delisting_suggested.v1`, `private_list.version_activated.v1` (staff-only, registered by `register_screening`). `true_match_confirmed` and the dispositions set the envelope's `policy_snapshot_id`.
- **Audit actions** (entity type is the table name, ids as `str(uuid)`, no names, inputs or rationale in `detail`): `screening.check_requested` (ad hoc subjects only, detail `{"run_id"}`), `screening.match_dispositioned` (detail `{"decision", "disposition_id"}`), `screening.whitelist_revoked`, and `VIEW_PII` from the match reveal only (one per non-empty group). `record(...)` comes last in each service function, before `publish`.
- **API**: routers come from `v1_router` only; `POST` needs `Idempotency-Key`; API clients use scopes `screening:read` and `screening:write`; staff-only routes declare no scope (matches, dispositions, whitelist, reveal); lists paginate with `paginate(...)`; handler names are unique operation ids. An API client never receives a list code of a hit, a score, an explanation, an entry, a finding or a count of findings (FR-SC-013).
- **Dates**: "today" for policy resolution is `today_mx()` = `clock.now().astimezone(ZoneInfo(MEXICO_CITY)).date()` (`MEXICO_CITY` from `xolotl.platform.calendar.deadlines`). A run resolves `effective_policy(s, on=today_mx(), product=None)` (spec Decision 14).
- **Console**: Spanish-first keyed strings in both catalogues; `schema.d.ts` regenerated against the merged API in every console task; revealed values only through `useMutation` with `gcTime: 0`; no string visible to a customer mentions screening, lists or suspicion (the console is staff-only, but the rule binds copy reused in client-facing places).
- **Commit trailers**: every commit ends with exactly the `Co-Authored-By` line the task prompt gives.

## Deviations from the sub-spec (decided here)

1. **No `_enc` suffix** on `screening_subject.input` and `match_disposition.rationale`, following the kernel and party precedent (`staff_credential.totp_secret`, `person.given_names`). 3a's Deviation 3 does the same for `private_list_entry.entry`.
2. **Scoring IDF is per list set, not per process.** Replay (NFR-SC-007) must re-score a hit exactly, but an `IdfTable` built over "every active shared list" changes whenever any list changes, including lists the run did not use. So the holder builds the blocking index over all shared lists, while scoring uses an `IdfTable.build` over the screenable entries of the run's own shared list-set members. That table is cached in the holder by the list set's content hash (LRU of 16). Replay rebuilds the same table from the immutable `list_entry` rows of those versions. Private entries never enter an IDF table. With the default subscriptions, one tenant uses one table.
3. **The match detail does not reveal by itself.** Spec §12 puts the revealed subject name inside `GET /v1/screening/matches/{id}`. The party precedent (2c Deviation on `/pii`, map C trap 4) moves every reveal to its own endpoint, because a detail that reveals would record `VIEW_PII` on every cached refresh. So the detail returns the subject masked (initials, masked identifiers), the stored explanations (normalized tokens, D7 exposure) and, for shared lists, the public entry. `GET /v1/screening/matches/{id}/reveal` (staff) returns the subject's names, birth date and identifier values, the private entry (for private-list findings) and the disposition rationales. It records one `VIEW_PII` per non-empty group (`subject`, `private_entry`, `rationales`).
4. **Synchronous checks use mode `AD_HOC`**, for an inline subject and for `{party_id}` alike: spec §5.2 lists no other on-demand mode. `NEW_SUBJECT` and `DATA_CHANGE` are reserved for event-driven runs (E8). The trigger is `CONSOLE` for staff and `API` for API clients, with the request id as `trigger_ref`.
5. **The client party view drops `open_finding_count`.** The accepted Q2 default ("status only") takes precedence over the §12 row: API clients get `party_id`, `status`, `last_screened_at` and `lists_covered`, while staff also get `open_finding_count` and the findings. A never-screened party answers with `status: null`.
6. **The run's list set is derived, not read.** The shared lists screened are `mandatory_lists ∪ recommended_lists` of the effective policy, minus the codes whose `tenant_list_subscription` row is `enabled = false`, intersected with the lists the holder has an active version for. This is exactly the state 3a's reconciliation produces, but it holds before the tenant's first `lists.poll` too, so a tenant created a minute ago is never screened against nothing. Private lists are the tenant's lists that have an active version. `MX_LPB` in `mandatory_lists` without an active version is simply absent from the set (3a notifies `lists.lpb_missing`).
7. **The run row is inserted first.** `screening_match.origin_run_id` and `screening_hit.run_id` reference it, so `screen()` inserts the run with zero counts and the subject's current status, then updates the counts, outcome and latency before returning. Callers see only the final row.
8. **A revoked whitelist reopens on the next hit.** The finding key `(subject, list, entry key, content hash)` is unique, so after `DELETE /v1/screening/whitelist/{id}` a new hit on the same content finds the existing `FALSE_POSITIVE` finding. When no active whitelist row covers it, `screen()` sets that finding back to `OPEN` (`closed_at = NULL`), counts it in `new_match_count` and publishes `screening.match_created.v1` again. This is how "the next run may alert again" (spec §9) holds under the unique key.
9. **Queue order through tabs.** Platform keyset pagination orders by `created_at, id` only, so "`ALERT` before `LOW_CONFIDENCE`" is done in the console: the queue opens on the `ALERT` tab, then `LOW_CONFIDENCE`, both served by the `band` filter. "Age" is the filter `created_before` (an instant).
10. **`blocked_reasons` on the problem body.** Spec §9 wants machine-readable reasons on the 409. The platform `Problem` gains an optional `blocked_reasons: list[str]`, filled from a new class attribute `XolotlError.blocked_reasons: tuple[str, ...] = ()` that only `DispositionNotAllowedError` sets (E6). This is a second small platform change and does not alter any other error's body.
11. **`prior_disposition_id` is set only by decisions that answer an escalation** (`TRUE_MATCH`, and `FALSE_POSITIVE` from `ESCALATED`), so the four-eyes trigger binds exactly the two-person decisions. `DELISTED` and the system `SUPERSEDED` leave it `NULL`.
12. **Runs of one subject are serialised** by `SELECT … FOR UPDATE` on its `screening_subject` row at the start of `screen()`. Finding dedup, superseding and the status recompute become deterministic under concurrency. `ON CONFLICT DO NOTHING` on the finding key stays as a second guard.
13. **Reserved enum members are not added** (`SubjectKind.COUNTERPARTY`, `RunMode.ONBOARDING` and `COUNTERPARTY`). The columns are text, so SP-04 and SP-08 add them without a migration.
14. **The webhook guard covers creation only.** There is no endpoint-update route on `main` (create, disable and rotate only). If one is added later, it must call the same `require_deliverable`.

## Review Focus

1. **Tipping-off (FR-SC-013, PRV-303).** A wildcard webhook endpoint receives `screening.run_completed.v1` and `screening.status_changed.v1` but never a `screening.match_*`, `screening.true_match_confirmed.v1` or `private_list.version_activated.v1` delivery row. An endpoint naming one of those types is refused with 422 `EVENT_TYPE_NOT_DELIVERABLE`. The client responses of checks, runs and parties carry no list code, score, entry or finding (a schema test walks every field of their JSON schemas). Tests: E1 `test_a_staff_only_type_is_never_fanned_out_even_to_a_wildcard_endpoint`; E5 `test_a_wildcard_endpoint_gets_run_and_status_events_but_never_match_events`; E7 `test_client_views_expose_status_only`.
2. **Exact run records and replay (FR-SC-001, NFR-SC-007).** Every run stores mode, trigger, list set (exact version ids), policy snapshot, matcher version, scoring config hash and query fingerprint. Replay of a fixture tenant's runs reproduces every stored score and band, reports a changed subject without re-scoring, and refuses a different `MATCHER_VERSION`. Tests: E5 `test_a_run_records_everything_needed_to_reproduce_it`; E8 `test_replay_reproduces_every_hit_of_a_fixture_tenant`, `test_replay_reports_a_changed_subject_and_does_not_rescore`, `test_replay_refuses_another_matcher_version`.
3. **Findings, whitelist and superseding (FR-SC-007, DR-305, DR-306).** One finding per subject and entry content, also under two concurrent runs. A whitelisted content gives a `WHITELISTED` hit and no finding, and a changed entry (new content hash) alerts again and supersedes the older open finding with a `SYSTEM` disposition. Tests: E5 `test_concurrent_runs_create_one_finding`, `test_a_whitelisted_entry_is_suppressed_and_logged_as_a_hit`, `test_a_changed_entry_realerts_and_supersedes_the_open_finding`.
4. **Four eyes (DR-305).** The exhaustive state × suggestion × role × same-or-different user × decision matrix matches spec §9 exactly. The database trigger refuses a same-user answer inserted directly in SQL. Tests: E6 `test_the_disposition_matrix_is_exactly_the_spec`, `test_the_four_eyes_trigger_refuses_a_same_user_row_inserted_in_sql`.
5. **Tenant isolation of private lists (PRV-302).** Tenant B's parties never produce a hit from tenant A's `MX_LPB`, through the in-memory private shard as well as through the RLS harness. Private shards are keyed and consulted by the session's own tenant only. Test: E5 `test_tenant_b_never_hits_tenant_a_private_list`.
6. **No plaintext beyond D7.** After a party check, an ad hoc check, a disposition and a reveal, `everything_stored(admin_engine)` holds no given name, surname, RFC, CURP or rationale in plaintext outside `party_name_index` and `screening_hit.features` (normalized tokens only). Logs, outbox payloads, job payloads and audit details hold ids and codes. Test: E7 `test_no_plaintext_is_stored_or_logged_beyond_the_name_index`.

## File structure

```
src/xolotl/platform/events/registry.py        + register_staff_only, is_staff_only, require_deliverable, EventTypeNotDeliverableError (E1)
src/xolotl/platform/events/webhooks.py        fanout skips staff-only types (E1)
src/xolotl/modules/tenants/webhook_service.py create_webhook_endpoint calls require_deliverable (E1)
src/xolotl/platform/errors.py, platform/api/problems.py   XolotlError.blocked_reasons, Problem.blocked_reasons (E6)
src/xolotl/platform/tenancy/registry.py       APPEND_ONLY_TABLES + match_disposition (E2)
src/xolotl/platform/config.py, .env.example, README.md   + screening_index_refresh_seconds, screening_private_shards (E3)
src/xolotl/migrations/versions/0010_screening.py          (E2)
src/xolotl/models.py                          + screening engine and review models (E2)
src/xolotl/modules/party/service.py           + PartyScreeningProfile, screening_profile (E4)
src/xolotl/modules/screening/
  events.py            event constants, STAFF_ONLY_EVENT_TYPES, publish_screening_event (E2; create or append)
  errors.py            ScreeningIndexUnavailableError (E3), DispositionNotAllowedError (E6), MatcherVersionMismatchError (E8) (create or append)
  composition.py       register_screening (E3; E8 adds subscribers and the job)
  schemas.py           check, run, party, match, disposition request and response models (E7; create or append)
  routes_screening.py  /v1/screening/* (E7)
  engine/__init__.py   empty (E2)
  engine/enums.py      SubjectKind, SubjectStatus, RunMode, TriggerType, NameKind, MatchStatus, HitBand, AutoReason, SuggestedDisposition, Decision, DecidedByType, BlockedReason, BatchKind, BatchStatus (E2)
  engine/models.py     PartyNameIndex, ScreeningSubject, ScreeningListSet, ScreeningListSetMember, ScreeningRun, ScreeningMatch, ScreeningHit, ScreeningBatch (E2)
  engine/holder.py     ScreeningIndexHolder, SharedShard, get_index_holder, set_index_holder (E3)
  engine/entries.py    indexed_shared_entry, indexed_private_entry, shared_version_entries (E3)
  engine/names.py      index_party_names (E4)
  engine/queries.py    query_for_party, AdHocSubjectInput, query_for_ad_hoc (E4)
  engine/subjects.py   subject_for_party, create_ad_hoc_subject, refresh_subject_status (E4)
  engine/policy.py     today_mx, ScreeningPolicyView, policy_for_run, thresholds_for, top_k_for (E5)
  engine/list_sets.py  ListSetMemberRef, upsert_list_set (E5)
  engine/screen.py     Trigger, RunResult, screen (E5)
  engine/features.py   hit_features (E5)
  engine/replay.py     ReplayRow, ReplayReport, replay_run (E8)
  review/__init__.py   empty (E2)
  review/models.py     MatchDisposition, ScreeningWhitelist (E2)
  review/actions.py    ActionSet, evaluate_actions (E6)
  review/dispositions.py  disposition_match (E6)
  review/whitelist.py  whitelist_entry, revoke_whitelist, active_whitelist_keys (E6)
  rescreen/__init__.py empty (E8)
  rescreen/party_events.py SCREENING_CONSUMER, SCREEN_PARTY_KIND, on_party_event, screen_party_job (E8)
src/xolotl/app.py      register_screening in wire_platform, index warm-up in build_app/build_worker (E3); + routes_screening.router (E7)
src/xolotl/cli.py      screening_app: check, replay (E8)
.importlinter          screening-engine-reads-party-through-services contract (E4)
tests/platform/test_webhook_fanout.py, test_webhooks.py (+) (E1); test_errors.py (+rows) (E1, E3, E6, E8); test_config.py (+) (E3); test_migration_chain.py (+) (E2)
tests/isolation/factories.py (+10) (E2)
tests/modules/screening/engine/{__init__,conftest,test_migration_0010,test_holder,test_names,test_subjects,test_queries,test_screen,test_screen_isolation,test_replay}.py
tests/modules/screening/review/{__init__,test_actions,test_dispositions,test_four_eyes_trigger,test_whitelist}.py
tests/modules/screening/rescreen/{__init__,test_party_events}.py
tests/modules/screening/test_routes_screening.py, route_support.py (E7)
tests/test_cli.py (+) (E8)
console/src/features/screening/{queries.ts, queries.test.tsx, BandBadge.tsx, MatchStatusBadge.tsx, MatchesPage.tsx, MatchesPage.test.tsx, MatchPage.tsx, MatchPage.test.tsx,
  ExplanationTable.tsx, ExplanationTable.test.tsx, DispositionForm.tsx, DispositionForm.test.tsx, MatchRevealCard.tsx, fixtures.ts} (E9)
console/src/features/screening/{CheckPage.tsx, CheckPage.test.tsx, check-form.ts, check-form.test.ts, PartyScreeningCard.tsx, PartyScreeningCard.test.tsx, HitList.tsx} (E10)
console/src/{api/schema.d.ts, api/types.ts, auth/access.ts, auth/access.test.ts, auth/guards.test.tsx, components/layout/nav.ts, App.tsx, i18n/es-MX.json, i18n/en.json, i18n/catalogue.test.ts} (E9, E10)
console/src/features/parties/PartyPage.tsx (+ screening card) (E10); console/e2e/smoke.spec.ts (+ step) (E10)
```

## Tasks at a glance

| Id | Title | Depends on | Console | Branch slug |
|---|---|---|---|---|
| E1 | Staff-only event types and the webhook guard (platform) | none | no | `sp03b-e1-staff-only-events` |
| E2 | Migration 0010, models, enums, events, isolation factories | 3a: D1 | no | `sp03b-e2-migration-0010` |
| E3 | Index holder and the screening composition root | E1, E2; LIB T1, T5; 3a: D4, D6 (test loaders) | no | `sp03b-e3-index-holder` |
| E4 | Party name index, subjects, queries, subject status | E2; LIB T1, T4, T5 | no | `sp03b-e4-name-index-subjects` |
| E5 | `screen()`: runs, list sets, hits, findings, status | E3, E4; LIB T5; 3a: D1 (via E2) | no | `sp03b-e5-screen` |
| E6 | Dispositions, action matrix, whitelist | E2, E4 | no | `sp03b-e6-dispositions` |
| E7 | Screening API for both audiences | E5, E6 | no | `sp03b-e7-screening-api` |
| E8 | Party event subscribers, screen-party job, replay, CLI | E5; LIB T5 | no | `sp03b-e8-party-events-replay` |
| E9 | Console: match queue and match detail | E7 | yes | `sp03b-e9-console-matches` |
| E10 | Console: party screening card and ad hoc check | E7; 3a: D7; 2c: C9 (merged) | yes | `sp03b-e10-console-party-check` |

---

### Task E1: Staff-only event types and the webhook guard (platform)

Depends on nothing; can start now. Platform only; no console. A small platform change (accepted): `xolotl.platform.events` learns that some event types are staff-only, and webhooks never deliver them, wildcard subscriptions included.

**Files:**
- Modify: `src/xolotl/platform/events/registry.py`, `src/xolotl/platform/events/__init__.py` (exports), `src/xolotl/platform/events/webhooks.py` (`WebhookService.fanout`), `src/xolotl/modules/tenants/webhook_service.py` (`create_webhook_endpoint`)
- Test: `tests/platform/test_webhook_fanout.py` (append), `tests/platform/test_webhooks.py` or the existing webhook route test module (append), `tests/platform/test_errors.py` (one row)

**Interfaces:**

Consumes: `register_subscriber`, `subscribers_for`, `WILDCARD` (`events.registry`), `WebhookService.fanout`, `tests/platform/webhooks_support.py` (endpoint and dispatch helpers already used by `test_webhook_fanout.py`), `ValidationFailedError`.

Produces (`src/xolotl/platform/events/registry.py`):

```python
_staff_only: set[str] = set()

class EventTypeNotDeliverableError(ValidationFailedError):
    default_code = "EVENT_TYPE_NOT_DELIVERABLE"
    default_detail = "event type is not deliverable to webhooks"

def register_staff_only(event_type: str) -> None
    # idempotent; refuses "*" and blanks with ValueError (a wildcard cannot be staff-only)
def is_staff_only(event_type: str) -> bool
def require_deliverable(event_types: Iterable[str]) -> None
    # raises EventTypeNotDeliverableError(f"{event_type} is staff-only") for the first staff-only type, in sorted order
```

`WebhookService.fanout`: the first statement is `if is_staff_only(event.type): return`. No delivery row and no job are created, and the dispatcher still records the `webhooks` consumption, so the event is not retried. `create_webhook_endpoint` calls `require_deliverable(event_types)` before the URL check. In-process subscribers are unaffected: `subscribers_for` does not change.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/platform/test_webhook_fanout.py (append)
STAFF_ONLY = "test.staff_only.v1"

def test_a_staff_only_type_is_never_fanned_out_even_to_a_wildcard_endpoint(...):
    # register_staff_only(STAFF_ONLY); endpoints: one listing "*", one listing STAFF_ONLY (inserted directly, bypassing the service guard)
    # publish an event of STAFF_ONLY and dispatch -> no webhook_delivery row, no webhook.deliver job; event_consumption has the "webhooks" row
def test_an_ordinary_type_still_reaches_the_wildcard_endpoint(...)        # same setup, type "test.ordinary.v1" -> one delivery row
def test_in_process_consumers_still_receive_staff_only_events(...)        # a recording subscriber on STAFF_ONLY receives the event once
def test_registering_a_staff_only_type_twice_is_harmless_and_a_wildcard_is_refused():
    # register twice -> is_staff_only True; register_staff_only("*") and ("") raise ValueError

# webhook route tests (append)
def test_creating_an_endpoint_for_a_staff_only_type_is_refused(client, ...):
    # POST /v1/webhooks/endpoints {"url": ..., "event_types": ["party.created.v1", STAFF_ONLY]} -> 422, code EVENT_TYPE_NOT_DELIVERABLE,
    # detail names STAFF_ONLY; no webhook_endpoint row; ["*"] alone is still accepted
```

`tests/platform/test_errors.py`: the row `(EventTypeNotDeliverableError, 422, "EVENT_TYPE_NOT_DELIVERABLE")`.

The registry is process-wide, so the tests register the fake type themselves. The real screening types are registered by E3's `register_screening`, and E5 proves them end to end.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_webhook_fanout.py tests/platform/test_webhooks.py tests/platform/test_errors.py --no-cov -q` → `ImportError` on `register_staff_only`.
- [ ] **Step 3: Implement** the registry functions, the fan-out guard and the creation check.
- [ ] **Step 4: Run to verify pass.** The same tests, then `make lint types vulture importlint`, then `make test`.
- [ ] **Docs:** the `events.registry` module docstring gains one paragraph on staff-only types (FR-SC-013, PRV-303). The README's webhook section, where it lists what endpoints may subscribe to, says that staff-only types are refused.
- [ ] **Step 5: Commit** `feat(events): staff-only event types that webhooks never deliver`.

---

### Task E2: Migration 0010, the screening models, enums, events and isolation factories

Depends on **3a's D1** (migration `0009_lists`, the catalog models and the factory helpers `_private_list_version` and `_private_list_entry`). No console. LIB is not needed: the models hold bytes, text and JSON.

**Files:**
- Create: `src/xolotl/migrations/versions/0010_screening.py`, `src/xolotl/modules/screening/engine/{__init__,enums,models}.py`, `src/xolotl/modules/screening/review/{__init__,models}.py`; `src/xolotl/modules/screening/events.py` (create or append)
- Modify: `src/xolotl/models.py`, `src/xolotl/platform/tenancy/registry.py` (`APPEND_ONLY_TABLES`), `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`
- Test: `tests/modules/screening/engine/__init__.py`, `tests/modules/screening/engine/test_migration_0010.py`, `tests/modules/screening/review/__init__.py`

**Interfaces:**

Consumes: `TenantTableMixin`, `tenant_table_args`, `tenant_fk`, `enable_rls`, `APPEND_ONLY_PRIVILEGES` (`xolotl.platform.tenancy.ddl`), `enum_column` (`xolotl.platform.sqltypes`), `EncryptedStr`, `EncryptedJson`, `Base`, `Party` (only in the migration and the factories; models reference `party` by table name), `StaffUser` (table `staff_user`), `PolicySnapshot` (table `policy_snapshot`), **3a:** tables `list_entry`, `list_version`, `private_list_version`, `private_list_entry` and the private factory helpers.

Produces:

```python
# engine/enums.py (every value equals its name)
class SubjectKind(StrEnum): PARTY; AD_HOC
class SubjectStatus(StrEnum): CLEAR; PENDING_REVIEW; TRUE_MATCH
class RunMode(StrEnum): AD_HOC; NEW_SUBJECT; DATA_CHANGE; LIST_DELTA; PRIVATE_LIST; PERIODIC; REINDEX
class TriggerType(StrEnum): API; CONSOLE; EVENT; JOB; CLI
class NameKind(StrEnum): LEGAL; TRADE
class MatchStatus(StrEnum): OPEN; ESCALATED; FALSE_POSITIVE; TRUE_MATCH; DELISTED; SUPERSEDED
class HitBand(StrEnum): ALERT; LOW_CONFIDENCE; AUTO_DISCARDED; WHITELISTED
class AutoReason(StrEnum): ID_CONFLICT_CAP; WHITELIST
class SuggestedDisposition(StrEnum): DELISTED
class Decision(StrEnum): FALSE_POSITIVE; POSSIBLE_TRUE_MATCH; TRUE_MATCH; DELISTED; SUPERSEDED
class DecidedByType(StrEnum): STAFF; SYSTEM
class BlockedReason(StrEnum): STATE; ROLE_REQUIRED; SAME_REVIEWER; NOT_DELISTED
class BatchKind(StrEnum): LIST_DELTA; PRIVATE_LIST; PERIODIC; REINDEX
class BatchStatus(StrEnum): RUNNING; COMPLETED; FAILED
OPEN_MATCH_STATUSES = frozenset({MatchStatus.OPEN, MatchStatus.ESCALATED})

# engine/models.py: column for column spec §5.2 (second block); arrays are ARRAY(Text); hashes LargeBinary; score Numeric(5, 4)
class PartyNameIndex; class ScreeningSubject (input: Encrypted[dict[str, object]] | None); class ScreeningListSet; class ScreeningListSetMember
class ScreeningRun; class ScreeningMatch; class ScreeningHit (features: JSONB dict[str, JsonValue]); class ScreeningBatch
# review/models.py
class MatchDisposition (rationale: Encrypted[str]); class ScreeningWhitelist

# events.py
EVENT_SOURCE = "xolotl.screening"
RUN_COMPLETED = "screening.run_completed.v1"; STATUS_CHANGED = "screening.status_changed.v1"
MATCH_CREATED = "screening.match_created.v1"; MATCH_DISPOSITIONED = "screening.match_dispositioned.v1"
TRUE_MATCH_CONFIRMED = "screening.true_match_confirmed.v1"; MATCH_DELISTING_SUGGESTED = "screening.match_delisting_suggested.v1"
PRIVATE_LIST_VERSION_ACTIVATED = "private_list.version_activated.v1"     # 3a's D3 constant: create it only if D3 has not merged
STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (PRIVATE_LIST_VERSION_ACTIVATED, MATCH_CREATED, MATCH_DISPOSITIONED, TRUE_MATCH_CONFIRMED, MATCH_DELISTING_SUGGESTED)
def publish_screening_event(s: Session, event_type: str, *, entity: str, entity_id: UUID, key: str,
                            data: dict[str, JsonValue], policy_snapshot_id: UUID | None = None) -> Event
    # 3a's D3 signature (whichever of D3 and E2 merges first creates events.py); this plan passes key=str(subject_id)
    # so a consumer gets one subject's events in order
```

Migration 0010 (order: `party_name_index`, `screening_subject`, `screening_list_set`, `screening_list_set_member`, `screening_run`, `screening_match`, `screening_hit`, `match_disposition`, `screening_whitelist`, `screening_batch`; downgrade in reverse, then `DROP FUNCTION match_disposition_four_eyes()`):
- Every column, check, unique key and index of spec §5.2, including `ix_pni_phonetic` (GIN on `phonetic_keys`), `ix_pni_trgm` (GIN `norm_name gin_trgm_ops`; `pg_trgm` exists from 0009), `ix_screening_run_subject (tenant_id, subject_id, created_at DESC)` and `ix_screening_match_queue (tenant_id, status, band, created_at)`. Add `ix_screening_match_entry (tenant_id, list_code, source_entry_key)` for 3c's delisting lookup and `ix_screening_hit_match (tenant_id, match_id)` for the hit history; spec §5.2 names neither, and both are cheap.
- Explicit FK names wherever the convention would pass 63 bytes, pinned by the migration test: `fk_screening_list_set_member_private_version`, `fk_screening_list_set_member_list_version`, `fk_screening_match_private_entry`, `fk_screening_match_superseded_by`, `fk_screening_hit_private_entry`, `fk_match_disposition_prior`, `fk_match_disposition_policy_snapshot`, `fk_screening_whitelist_disposition`.
- `enable_rls` for each table; `match_disposition` with `grant=APPEND_ONLY_PRIVILEGES`.
- The four-eyes constraint trigger (spec §5.2, DR-305):

```sql
CREATE FUNCTION match_disposition_four_eyes() RETURNS trigger AS $$
BEGIN
  IF NEW.prior_disposition_id IS NOT NULL AND EXISTS (
       SELECT 1 FROM match_disposition p
       WHERE p.tenant_id = NEW.tenant_id AND p.id = NEW.prior_disposition_id
         AND p.decided_by IS NOT DISTINCT FROM NEW.decided_by AND NEW.decided_by IS NOT NULL) THEN
    RAISE EXCEPTION 'four-eyes: the prior disposition was made by the same user'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'match_disposition_four_eyes';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER match_disposition_four_eyes AFTER INSERT ON match_disposition
  DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION match_disposition_four_eyes();
```

Isolation factories (one each in `tests/isolation/factories.py`, registered in `register_all()`): `party_name_index` and `screening_subject` over `_party`; `screening_list_set`; `screening_list_set_member` over a list set and **3a's** `_private_list_version`; `screening_run` over a subject, list set and `_policy_snapshot`; `screening_match` and `screening_hit` over a run and **3a's** `_private_list_entry` (the CHECK needs exactly one entry reference, and the app role cannot insert a `list_source`); `match_disposition` over a match, `_staff_user` and a snapshot (decision `POSSIBLE_TRUE_MATCH`); `screening_whitelist` over a subject and a disposition; `screening_batch`.

- [ ] **Step 1: Write the failing tests** (`tests/modules/screening/engine/test_migration_0010.py`, using the `admin_engine` inspector as `tests/modules/party/test_migration_0008.py` does):

```python
def test_the_ten_tables_exist_with_row_level_security_forced(admin_engine)
def test_the_composite_foreign_keys_keep_references_inside_the_tenant(admin_engine)   # pins every FK name and column pair listed above
def test_the_entry_reference_checks_refuse_both_or_neither(engine, tenant)            # match and hit with both / neither entry ids -> IntegrityError
def test_the_subject_check_ties_kind_to_party_or_input(engine, tenant)
def test_a_true_match_disposition_needs_a_prior_disposition(engine, tenant)          # CHECK decision <> TRUE_MATCH OR prior IS NOT NULL
def test_staff_decisions_name_a_user_and_system_ones_do_not(engine, tenant)
def test_match_disposition_is_append_only_for_the_app_role(engine, tenant)          # UPDATE and DELETE -> InsufficientPrivilege
def test_the_name_index_has_its_gin_indexes(admin_engine)                            # ix_pni_phonetic, ix_pni_trgm with gin_trgm_ops
def test_the_finding_key_is_unique(engine, tenant)
def test_downgrade_removes_the_tables_and_the_trigger_function(admin_engine)          # alembic downgrade 0009_lists then upgrade head
def test_the_app_role_may_update_hits_and_delete_name_index_rows(admin_engine)       # has_table_privilege: screening_hit UPDATE, party_name_index DELETE (3c F5)
```

The isolation harness (`tests/isolation`) discovers the ten tables automatically and fails until the factories exist. `test_schema_sync.py` fails until the models match. `test_migration_chain.py` gets `"0010_screening"`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/engine/test_migration_0010.py tests/isolation tests/platform/test_schema_sync.py tests/platform/test_migration_chain.py --no-cov -q`.
- [ ] **Step 3: Implement** the enums, models, events module, migration, factories and registry entry. Import the models in `src/xolotl/models.py` (`screening_engine_models`, `screening_review_models` in `__all__`).
- [ ] **Step 4: Run to verify pass**, then `make lint types vulture importlint`, then `make test`. Vulture: constants unused until E5 and E6 are referenced by `STAFF_ONLY_EVENT_TYPES` or by the tests; if vulture still flags an enum module, leave that member out until its first user lands, rather than whitelisting it.
- [ ] **Step 5: Commit** `feat(screening): migration 0010 with runs, findings, dispositions and the four-eyes trigger`.

---

### Task E3: The index holder and the screening composition root

Depends on **E1** (`register_staff_only`) and **E2** (events constants), **LIB-matching** (`MemoryIndex`, `IdfTable`, `IndexedEntry`) and **LIB-lists** (`ListEntry.from_json`), and **3a's catalog models** (D1: `ListVersion`, `ListEntryRow`, `PrivateList`, `PrivateListVersion`, `PrivateListEntryRow`) with **3a's test loaders** `load_shared` (D4) and `load_private` (D6). No console. Touches `src/xolotl/app.py` (`wire_platform`, `build_app`, `build_worker`) and `src/xolotl/platform/config.py`.

**Files:**
- Create: `src/xolotl/modules/screening/engine/holder.py`, `src/xolotl/modules/screening/engine/entries.py`, `src/xolotl/modules/screening/composition.py`; `src/xolotl/modules/screening/errors.py` (create or append)
- Modify: `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `src/xolotl/app.py`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`
- Test: `tests/modules/screening/engine/conftest.py`, `tests/modules/screening/engine/test_holder.py`

**Interfaces:**

Consumes: the LIB and 3a symbols above; `get_settings`, `tenant_session`, `TenantContext.platform`, `get_sessionmaker`; `Encrypted.reveal(session=...)`.

Produces:

```python
# engine/entries.py
def indexed_shared_entry(row: ListEntryRow) -> IndexedEntry               # IndexedEntry(entry=ListEntry.from_json(row.entry), ref=row.id, shard=row.list_code)
def indexed_private_entry(s: Session, row: PrivateListEntryRow, list_code: str) -> IndexedEntry   # decrypts row.entry in s (processing only, no audit)
def shared_version_entries(s: Session, list_code: str, seq: int) -> list[ListEntryRow]
    # members of version seq that are screenable: from_seq <= seq AND (to_seq IS NULL OR to_seq > seq) AND screenable, ordered by id

# engine/holder.py
@dataclass(frozen=True, slots=True)
class HeldVersion: list_code: str; version_id: UUID; seq: int; list_kind: str

@dataclass(frozen=True, slots=True)
class SharedShard:
    versions: Mapping[str, HeldVersion]            # by list code: what this index holds
    index: MemoryIndex                             # all shared screenable entries, built with blocking_idf
    blocking_idf: IdfTable                         # IdfTable.build over every held entry
    entries: Mapping[str, tuple[ListEntry, ...]]   # by list code, for per-list-set scoring IDF

@dataclass(frozen=True, slots=True)
class PrivateShard:
    tenant_id: UUID
    versions: Mapping[str, HeldVersion]            # by private list code (version_id = private_list_version.id)
    index: MemoryIndex

class ScreeningIndexHolder:
    def __init__(self, session_factory: SessionFactory, *, refresh_seconds: int, private_shards: int,
                 monotonic: Callable[[], float] = time.monotonic) -> None
    def warm(self) -> None                         # build the shared shard in a platform-context session; logs and keeps None on failure
    def start_warming(self) -> threading.Thread    # daemon thread running warm(); marks the holder "warming"
    def shared(self, s: Session, *, require: Mapping[str, int] | None = None) -> SharedShard
        # never loaded: warming -> ScreeningIndexUnavailableError; not warming (tests, CLI) -> load synchronously in s
        # at most every refresh_seconds (or at once when require names a seq the shard lacks): read the active versions
        #   (list_code, id, seq); if any differs, rebuild synchronously under the holder's lock and swap; screens that do not
        #   hold the lock keep using the previous shard until the swap
        # after a forced refresh, a required seq that is still missing -> ScreeningIndexUnavailableError
    def private(self, s: Session) -> PrivateShard | None
        # tenant = bound_context(s).require_tenant_id(); key = (tenant, frozenset of the tenant's ACTIVE private_list_version ids);
        # LRU of private_shards; a miss builds from the active versions' screenable current entries (to_seq IS NULL) decrypted in s;
        # None when the tenant has no active private version
    def scoring_idf(self, shard: SharedShard, list_codes: Collection[str], *, key: bytes) -> IdfTable
        # IdfTable.build over shard.entries of list_codes; LRU of 16 keyed by key (the list set's content hash)

def get_index_holder() -> ScreeningIndexHolder      # raises RuntimeError when register_screening has not run
def set_index_holder(holder: ScreeningIndexHolder | None) -> None   # composition and tests

# errors.py
class ScreeningIndexUnavailableError(XolotlError): status = 503; default_code = "SCREENING_INDEX_UNAVAILABLE"; default_detail = "the screening index is loading"

# composition.py
def register_screening(*, session_factory: SessionFactory, settings: Settings) -> ScreeningIndexHolder
    # for t in STAFF_ONLY_EVENT_TYPES: register_staff_only(t); builds and installs the holder; idempotent (replaces the holder)
```

The shared shard is loaded in one `REPEATABLE READ` transaction (`s.connection(execution_options={"isolation_level": "REPEATABLE READ"})` on a fresh session), so the active versions and their entries come from one snapshot even while 3a ingests. Entries are the current rows (`to_seq IS NULL`, `screenable`) of lists with an `ACTIVE` version, which by 3a's construction are that version's members. `list_kind` comes from `list_source.list_kind`; for private lists, from `private_list.list_kind`.

`src/xolotl/app.py`: `wire_platform` calls `register_screening(session_factory=session_factory, settings=settings)`. `build_app` and `build_worker` then call `get_index_holder().start_warming()`. `wire_platform` itself never starts a thread, so the tests that call it stay synchronous.

- [ ] **Step 1: Write the failing tests** (`tests/modules/screening/engine/test_holder.py`; `conftest.py` provides the fixtures `un_entries` (three hand-built `ListEntry` values of `UN_CONSOLIDATED`), `holder` (a fresh `ScreeningIndexHolder` over `get_sessionmaker()` with `refresh_seconds=30`, a fake monotonic clock, installed with `set_index_holder` and removed after), and `tenant` and `admin_engine` from the root conftest):

```python
def test_the_shared_shard_holds_the_active_versions_screenable_entries(holder, admin_engine, un_entries)
    # load_shared(UN, entries incl. one screenable=False) -> shard.versions["UN_CONSOLIDATED"].seq == 1; the non-screenable entry is absent
def test_a_newer_version_is_picked_up_after_the_refresh_interval(holder, admin_engine, un_entries)
    # load v1; shared(); load v2; shared() before 30 s -> still v1; advance the fake clock 31 s -> v2
def test_a_required_version_forces_a_refresh_at_once(holder, admin_engine, un_entries)    # shared(require={"UN_CONSOLIDATED": 2}) -> v2 without waiting
def test_a_required_version_that_does_not_exist_is_unavailable(holder, ...)              # require seq 9 -> ScreeningIndexUnavailableError (503)
def test_a_holder_still_warming_answers_unavailable(holder, monkeypatch)                  # warming flag set, nothing loaded -> 503
def test_a_private_shard_is_built_per_tenant_and_invalidated_by_a_new_version(holder, tenant, other_tenant)
    # load_private(tenant A, "MX_LPB", ...) -> private(sA) has it; private(sB) is None; a new A version -> a new shard object
def test_private_shards_are_evicted_beyond_the_configured_number(...)                     # private_shards=1, two tenants -> the first rebuilt on its next use
def test_private_entries_are_decrypted_only_inside_their_tenants_session(holder, tenant)  # PrivateListEntryRow.entry is Encrypted; the shard's ListEntry equals the loaded one
def test_scoring_idf_is_cached_per_list_set_key(holder, ...)                             # same key -> same object; another key -> another table
def test_register_screening_marks_every_staff_only_type(engine)                           # wire_platform(...) -> is_staff_only(t) for t in STAFF_ONLY_EVENT_TYPES
def test_wire_platform_does_not_start_a_thread(engine)                                    # threading.active_count() unchanged
```

`tests/platform/test_config.py`: the two settings in `.env.example`, `model_fields_set` and the bounds (0 → `ValidationError`). `tests/platform/test_errors.py`: `(ScreeningIndexUnavailableError, 503, "SCREENING_INDEX_UNAVAILABLE")`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/engine/test_holder.py tests/platform/test_config.py tests/platform/test_errors.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates and `make test`.
- [ ] **Docs:** README configuration table (two rows); README "Processes" (or the equivalent section) gains one sentence: each process holds the screening index in memory, warmed at start-up.
- [ ] **Step 5: Commit** `feat(screening): in-memory index holder with shared and private shards`.

---

### Task E4: Party name index, subjects, queries and the subject status

Depends on **E2**, **LIB-matching** (`normalize_name`, `NormalizedName`, `TokenRole`, `NameQuery`, `QueryIdentifier`, `MATCHER_VERSION`) and **LIB-lists** (`EntityType`, `IdType`). It does not need E3, so it can run in parallel with E3. No console. Adds one function to the party module.

**Files:**
- Create: `src/xolotl/modules/screening/engine/{names,queries,subjects}.py`
- Modify: `src/xolotl/modules/party/service.py` (`PartyScreeningProfile`, `screening_profile`), `.importlinter`
- Test: `tests/modules/screening/engine/test_names.py`, `test_queries.py`, `test_subjects.py`; `tests/modules/party/test_service.py` (append)

**Interfaces:**

Consumes: `get_party`, `PartyType`, `PartyStatus`, `IdentifierType` (party), `validate` and `IdentifierError` (`party.identifiers`, for ad hoc identifiers), `encrypt_json`, `effective_policy`, `add_iso_duration`, `clock`, `bound_context`, `publish_screening_event`, `STATUS_CHANGED` (E2), E2 models.

Produces:

```python
# party/service.py: decrypts for processing only; no audit (D6: no person sees these values)
@dataclass(frozen=True, slots=True)
class PartyScreeningProfile:
    party_id: UUID; party_type: PartyType; status: PartyStatus; country_code: str
    given_names: str | None; first_surname: str | None; second_surname: str | None
    birth_date: date | None; gender: str | None; nationalities: tuple[str, ...]; birth_country: str | None
    legal_name: str | None; trade_name: str | None; country_of_incorporation: str | None
    identifiers: tuple[tuple[IdentifierType, str, str], ...]     # (id_type, canonical value, country_code)
def screening_profile(s: Session, party_id: UUID) -> PartyScreeningProfile    # NotFoundError like get_party

# engine/queries.py
SCREENED_ID_TYPES: Mapping[IdentifierType, IdType] = {RFC: IdType.RFC, CURP: IdType.CURP, PASSPORT: IdType.PASSPORT}
def query_for_profile(profile: PartyScreeningProfile) -> NameQuery
    # PERSON: names=(" ".join(given, first, second),), given_names, first_surname, second_surname, birth_date,
    #   gender only when MALE or FEMALE, nationalities, countries=dedup(country_code, birth_country)
    # ORGANIZATION: names=(legal_name,) + ((trade_name,) if trade_name), countries=dedup(country_of_incorporation, country_code)
    # identifiers: SCREENED_ID_TYPES only, QueryIdentifier(id_type, normalized=value, country)
def query_for_party(s: Session, party_id: UUID) -> NameQuery     # query_for_profile(screening_profile(s, party_id))

class AdHocIdentifier(BaseModel): id_type: IdentifierType; value: str = Field(max_length=64); country: str | None = Field(None, pattern="^[A-Z]{2}$")
class AdHocSubjectInput(BaseModel):                 # strict; also the API body's "subject" (E7)
    type: Literal["PERSON", "ORGANIZATION"]
    names: list[str] = Field(min_length=1, max_length=3)        # each 1..300 characters
    given_names: str | None = None; first_surname: str | None = None; second_surname: str | None = None
    birth_date: date | None = None; gender: Literal["MALE", "FEMALE"] | None = None
    nationalities: list[str] = []; countries: list[str] = []     # alpha-2
    identifiers: list[AdHocIdentifier] = Field(default=[], max_length=10)
def query_for_ad_hoc(body: AdHocSubjectInput) -> NameQuery
    # identifiers validated with party.identifiers.validate; IdentifierError -> IdentifierInvalidError("<TYPE>: <REASON>")

# engine/names.py
def index_party_names(s: Session, party_id: UUID) -> list[PartyNameIndex]
    # PURGED party or no name -> delete its rows, return []
    # LEGAL from the person's full name (normalize_name(full, entity_type=PERSON, given=given_names, surname="first second"))
    #   or the legal name (ORGANIZATION); TRADE when trade_name is set, else its row is deleted
    # norm_name = n.folded; tokens = [t.folded for t in n.tokens]; phonetic_keys = sorted({t.phonetic for t in n.tokens
    #   if t.role not in {PARTICLE, LEGAL_FORM}}); matcher_version = MATCHER_VERSION; upsert ON CONFLICT (tenant_id, party_id, name_kind)

# engine/subjects.py
def subject_for_party(s: Session, party_id: UUID, *, for_update: bool = False) -> ScreeningSubject
    # INSERT ... ON CONFLICT (tenant_id, party_id) DO NOTHING, then select (FOR UPDATE when asked); get_party first (404 for an unknown party)
def create_ad_hoc_subject(s: Session, body: AdHocSubjectInput) -> ScreeningSubject
    # input = encrypt_json(body.model_dump(mode="json")); retention_until = add_iso_duration(today_mx(),
    #   effective_policy(s, on=today_mx(), product=None).retention.kyc_after_relationship.value)
def refresh_subject_status(s: Session, subject: ScreeningSubject) -> SubjectStatus
    # TRUE_MATCH if any finding TRUE_MATCH; else PENDING_REVIEW if any OPEN or ESCALATED; else CLEAR;
    # when it changed: update, publish STATUS_CHANGED {"subject_id", "party_id" (null for AD_HOC), "status"}
```

`today_mx` lives in `engine/policy.py`. E4 creates that file with `today_mx` alone, and E5 fills in the rest.

`.importlinter`: a `forbidden` contract named "screening engine reads parties through services" with source modules `xolotl.modules.screening.engine`, `.review`, `.rescreen` and forbidden module `xolotl.modules.party.models`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/modules/party/test_service.py (append)
def test_the_screening_profile_decrypts_without_auditing(...)   # person and organization; no audit row; identifiers canonical

# test_names.py
def test_a_person_is_indexed_with_the_librarys_normalization(...)          # rows equal normalize_name(...) output and MATCHER_VERSION
def test_an_organization_gets_a_legal_and_a_trade_row(...)
def test_removing_the_trade_name_removes_its_row(...)
def test_reindexing_is_an_upsert(...)                                      # twice -> one row per name kind, updated_at moves
def test_a_purged_party_has_no_name_index_rows(...)
def test_the_name_index_is_invisible_to_another_tenant(...)

# test_queries.py
def test_a_person_query_carries_names_birth_date_and_screened_identifiers(...)   # CLABE not passed; RFC, CURP, PASSPORT are
def test_an_organization_query_carries_legal_and_trade_names(...)
def test_an_ad_hoc_identifier_is_validated_like_a_party_identifier()            # bad RFC -> IdentifierInvalidError "RFC: CHECK_DIGIT"
def test_the_query_fingerprint_is_stable_across_calls(...)

# test_subjects.py
def test_a_party_subject_is_created_once(...)                             # two calls -> one row
def test_an_ad_hoc_subject_stores_its_input_encrypted_with_a_retention_date(itf_pack, ...)   # P10Y from today_mx; no plaintext in everything_stored
def test_the_status_follows_the_findings_and_publishes_only_on_change(...)
    # findings OPEN -> PENDING_REVIEW (one event); a second refresh -> no event; TRUE_MATCH -> TRUE_MATCH; all closed -> CLEAR
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/engine/test_names.py tests/modules/screening/engine/test_queries.py tests/modules/screening/engine/test_subjects.py tests/modules/party/test_service.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates (import-linter proves the new contract), then `make test`.
- [ ] **Step 5: Commit** `feat(screening): party name index, subjects and screening queries`.

---

### Task E5: `screen()`: runs, list sets, hits, findings and status

Depends on **E3** and **E4**, **LIB-matching** (`score`, `band_for`, `Band`, `BandThresholds`, `ScoringConfig`, `MatchScore`, `MatchExplanation.to_json`, `Candidate`, `MemoryIndex.build`, `NameQuery.fingerprint`, `MATCHER_VERSION`), **LIB-lists** (`ListEntry.content_hash`), and **3a's D1 models `TenantListSubscription`, `PrivateList`** (list kinds). No console. This is the plan's centre (spec §8.3, §8.4).

**Files:**
- Create: `src/xolotl/modules/screening/engine/{list_sets,screen,features}.py`
- Modify: `src/xolotl/modules/screening/engine/policy.py` (complete it)
- Test: `tests/modules/screening/engine/test_screen.py`, `tests/modules/screening/engine/test_screen_isolation.py`

**Interfaces:**

Consumes: `effective_policy`, `snapshot_policy`, `EffectivePolicy` (`screening.bands`, `mandatory_lists`, `recommended_lists`), `get_index_holder`, `subject_for_party`, `query_for_party`, `refresh_subject_status`, E2 models and events, `encrypt` (system disposition rationale), `bound_context`, `time.monotonic`.

Produces:

```python
# engine/policy.py
def today_mx() -> date
@dataclass(frozen=True, slots=True)
class ScreeningPolicyView: snapshot_id: UUID; effective: EffectivePolicy; config: ScoringConfig
def policy_for_run(s: Session) -> ScreeningPolicyView
    # effective_policy(s, on=today_mx(), product=None) (POLICY_NOT_ASSIGNED propagates); snapshot_policy;
    # ScoringConfig(id_match_floor=bands.id_match_floor, id_conflict_cap=bands.id_conflict_cap)
PEP_REVIEW_OFFSET = Decimal("0.15")                                   # spec Decision 6
def thresholds_for(list_kind: str, bands: ScreeningBands) -> BandThresholds
    # SANCTION, BLOCKED, INTERNAL, FISCAL -> (sanctions_alert, sanctions_review); PEP -> (pep_alert, pep_alert - 0.15)
def top_k_for(list_kind: str) -> int                                 # PEP 25, otherwise 50 (TS-03 §5)

# engine/list_sets.py
@dataclass(frozen=True, slots=True)
class ListSetMemberRef: list_code: str; list_kind: str; list_version_id: UUID | None; private_list_version_id: UUID | None; seq: int
def list_set_hash(members: Iterable[ListSetMemberRef]) -> bytes      # sha256 of sorted "code:version_uuid" lines joined by "\n"
def upsert_list_set(s: Session, members: Sequence[ListSetMemberRef]) -> ScreeningListSet
    # INSERT ON CONFLICT (tenant_id, content_hash) DO NOTHING; members inserted only with a new set

# engine/features.py
def hit_features(result: MatchScore, *, list_kind: str, thresholds: BandThresholds, config_hash: bytes,
                 entry_ref: Mapping[str, JsonValue]) -> dict[str, JsonValue]
    # result.explanation.to_json() | {"v": 1, "list_kind", "thresholds": {"alert", "review"} as strings, "matcher_version",
    #   "config_hash": hex, "entry_ref": {"list_code", "entry_key", "list_entry_id" | "private_list_entry_id", "list_version_seq"}}

# engine/screen.py
@dataclass(frozen=True, slots=True)
class Trigger: type: TriggerType; ref: str | None = None
@dataclass(frozen=True, slots=True)
class RunResult:
    run: ScreeningRun; subject: ScreeningSubject
    hits: tuple[ScreeningHit, ...]                 # stored hits, in score order (descending)
    new_matches: tuple[ScreeningMatch, ...]        # created or reopened by this run
def screen(s: Session, subject: ScreeningSubject, *, mode: RunMode, trigger: Trigger,
           lists: Collection[str] | None = None, only_entries: Sequence[IndexedEntry] | None = None,
           only_index: MemoryIndex | None = None, require_versions: Mapping[str, int] | None = None,
           batch_id: UUID | None = None) -> RunResult
    # the one SP-03 signature (finding 2), owned by this task: 3c's F3 passes only_entries, require_versions and batch_id;
    # F4 passes only_index (a version's index built once per page), lists and batch_id. only_entries and only_index
    # together -> ValueError.
```

`screen()` steps (spec §8.3; Deviations 2, 6, 7, 8, 12):
1. Lock the subject row (`FOR UPDATE`), then build its query: `query_for_party` for `PARTY`, or `query_for_ad_hoc(AdHocSubjectInput.model_validate(subject.input.reveal(session=s)))` for `AD_HOC`. Then `policy = policy_for_run(s)` and `t0 = monotonic()`.
2. `shard = holder.shared(s, require=require_versions)` and `private = holder.private(s)`. The shared codes are `(mandatory ∪ recommended)` minus disabled subscriptions, ∩ `shard.versions`; the private codes are `private.versions`; both are narrowed by `lists` when it is given. These give the members, then `list_set = upsert_list_set(...)` and `idf = holder.scoring_idf(shard, shared_codes, key=list_set.content_hash)`.
3. Candidates. With `only_entries`, `MemoryIndex.build(only_entries, idf=idf).candidates(query, shards=codes, top_k=len(only_entries))`, keeping only entries whose shard is in the set. With `only_index`, `only_index.candidates(query, shards=codes, top_k=top_k_for(kind))` per kind group. Otherwise, for each kind group (PEP or not), `shard.index.candidates(query, shards=shared codes of the group, top_k=top_k_for(kind))` plus the same on `private.index`. Duplicates by `ref` are dropped.
4. Insert the run (`candidate_count = len(candidates)`, zero counts, `outcome = subject.status`, `restricted_to_delta = only_entries is not None or only_index is not None`, `batch_id`, `scoring_config_hash = policy.config.config_hash()`, `query_fingerprint = query.fingerprint()`, `matcher_version = MATCHER_VERSION`, `created_by = bound_context(s).actor_id`, `trigger_type`, `trigger_ref`, `mode`, `latency_ms = 0`) and flush.
5. For each candidate: `result = score(query, entry, idf=idf, config=policy.config)`, then `band = band_for(result, thresholds_for(kind, bands))`. `BELOW_CUTOFF` is counted and not stored. The entry hash is `entry.content_hash()` for shared entries and `row.content_tag` for private ones. When the active whitelist keys contain `(list_code, entry_key, hash)`, store the hit as `WHITELISTED` with `auto_reason = WHITELIST` and create no finding. `AUTO_DISCARDED` is stored with `auto_reason = ID_CONFLICT_CAP` and no finding. `ALERT` and `LOW_CONFIDENCE` insert the finding (`ON CONFLICT DO NOTHING` on the finding key, `origin_run_id = run.id`, `list_kind`, band and score). If it already existed, the hit links to it, and a `FALSE_POSITIVE` finding without an active whitelist row is reopened (Deviation 8). A newly created finding closes every `OPEN` or `ESCALATED` finding of the same subject, list and key that has another hash: status `SUPERSEDED`, `superseded_by_match_id`, `closed_at`, and a `SYSTEM` `SUPERSEDED` disposition (rationale `encrypt("SUPERSEDED_BY_CHANGED_ENTRY")`, `policy_snapshot_id`). Each new or reopened finding publishes `MATCH_CREATED {"match_id", "subject_id", "list_kind", "band"}`.
6. `status = refresh_subject_status(...)`; update the run (`hit_count` = stored hits, `new_match_count`, `outcome = status`, `latency_ms`) and the subject (`last_screened_at = clock.now()`, `last_run_id`). Publish `RUN_COMPLETED {"run_id", "subject_id", "party_id", "mode", "outcome"}` and return.

`active_whitelist_keys(s, subject_id) -> set[tuple[str, str, bytes]]` is needed here before E6 exists. E5 writes it in `review/whitelist.py` (a single query over `ScreeningWhitelist` with `revoked_at IS NULL`), and E6 adds the rest of that file. If E6 merges first, E5 imports it.

- [ ] **Step 1: Write the failing tests.** `tests/modules/screening/engine/test_screen.py` fixtures: `itf_pack` (ITF assigned), `holder`, an OFAC-style `ListEntry` for "JUAN CARLOS PEREZ GOMEZ" with a birth date, a party "Juan Carlos Pérez Gómez" with that birth date, and a hand-built `MX_LPB` private entry. Scores are asserted by band, never by exact value, except in the replay-style equality test. The library's corpus owns score values.

```python
def test_a_party_check_creates_an_alert_finding_with_a_stored_explanation(...)
    # screen(mode=AD_HOC, trigger=Trigger(CONSOLE)) -> one ALERT hit; features["v"] == 1, alignment non-empty, thresholds "0.70"/"0.55",
    # entry_ref list_version_seq == 1; one OPEN finding; subject PENDING_REVIEW; run outcome PENDING_REVIEW
def test_a_run_records_everything_needed_to_reproduce_it(...)
    # run: list_set members == the held versions (ids), policy_snapshot_id set, matcher_version == MATCHER_VERSION,
    # scoring_config_hash == ScoringConfig(...).config_hash(), query_fingerprint == query_for_party(...).fingerprint()
def test_the_list_set_is_shared_by_runs_over_the_same_versions(...)            # two runs -> one screening_list_set
def test_lists_follow_the_policy_and_a_disabled_recommended_list_is_skipped(...)
    # no subscription rows yet -> OFAC screened (recommended); subscription OFAC enabled=False -> OFAC absent from the set
def test_a_staff_list_filter_narrows_the_set(...)                               # lists={"UN_CONSOLIDATED"}
def test_bands_come_from_the_policy_per_list_kind(...)
    # thresholds_for: SANCTION/BLOCKED/INTERNAL/FISCAL -> sanctions bands; PEP -> 0.80 / 0.65; an override tightening sanctions_alert changes the band
def test_a_rescreen_of_the_same_content_raises_no_second_finding(...)           # second run: hit links to the same finding, new_match_count 0
def test_concurrent_runs_create_one_finding(...)                                # two threads, two sessions, same party -> one finding, two runs
def test_a_whitelisted_entry_is_suppressed_and_logged_as_a_hit(...)             # whitelist row -> hit WHITELISTED, auto_reason WHITELIST, no finding
def test_a_changed_entry_realerts_and_supersedes_the_open_finding(...)
    # finding on v1 content; v2 changes the entry -> new finding; old SUPERSEDED with superseded_by and a SYSTEM disposition
def test_a_revoked_whitelist_reopens_the_false_positive_finding(...)            # Deviation 8
def test_an_id_conflict_is_stored_as_auto_discarded_without_a_finding(...)      # party RFC differs from an entry RFC
def test_below_cutoff_candidates_are_counted_not_stored(...)                    # candidate_count > hit_count
def test_only_entries_restricts_scoring_to_the_given_entries(...)               # restricted_to_delta True; hits only on those refs
def test_only_index_scores_against_the_given_index_and_marks_the_run_restricted(...)
def test_only_entries_and_only_index_are_exclusive(...)                          # ValueError
def test_a_run_records_its_batch(...)                                            # batch_id written to screening_run.batch_id
def test_a_69b_name_only_resemblance_reaches_low_confidence_at_most(...)        # organization without RFC vs an MX_SAT_69B entry -> never ALERT (accepted Q5)
def test_a_tenant_without_a_policy_cannot_be_screened(...)                      # POLICY_NOT_ASSIGNED, no run row
def test_a_wildcard_endpoint_gets_run_and_status_events_but_never_match_events(...)
    # register_webhooks + register_screening; a wildcard endpoint; screen -> dispatch -> deliveries for run_completed and status_changed only
def test_events_carry_ids_and_codes_only(...)                                   # payloads_of(...) has no name, RFC or token
```

`tests/modules/screening/engine/test_screen_isolation.py`:

```python
def test_tenant_b_never_hits_tenant_a_private_list(...)
    # A uploads MX_LPB with "JUAN CARLOS PEREZ GOMEZ"; B has the same party -> B's run has no private member and no hit;
    # A's run has the hit; B cannot read A's runs, hits or findings (RLS)
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/engine/test_screen.py tests/modules/screening/engine/test_screen_isolation.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Step 5: Commit** `feat(screening): screen subjects with exact run records and deduplicated findings`.

---

### Task E6: Dispositions, the action matrix and the whitelist

Depends on **E2** and **E4** (`refresh_subject_status`). It does not need E5: tests create findings with the models directly, so E6 runs in parallel with E5. No console. Touches the platform error body (Deviation 10).

**Files:**
- Create: `src/xolotl/modules/screening/review/{actions,dispositions}.py`; `src/xolotl/modules/screening/review/whitelist.py` (create or append after E5)
- Modify: `src/xolotl/platform/errors.py` (`XolotlError.blocked_reasons`), `src/xolotl/platform/api/problems.py` (`Problem.blocked_reasons`, filled by the `XolotlError` handler when non-empty), `src/xolotl/modules/screening/errors.py`, `tests/platform/test_errors.py`
- Test: `tests/modules/screening/review/test_actions.py`, `test_dispositions.py`, `test_four_eyes_trigger.py`, `test_whitelist.py`; `tests/platform/test_api_conventions.py` (append: one problem-body test)

**Interfaces:**

Consumes: `bound_context` (`roles`, `actor_id`, `actor_type`), `Role`, `policy_for_run` (E5's `engine/policy.py`; if E5 has not merged, E6 adds `policy_for_run` there and E5 rebases), `encrypt`, `record`, `publish_screening_event`, `MATCH_DISPOSITIONED`, `TRUE_MATCH_CONFIRMED`, `get_snapshot` (the `on_true_match` action), `refresh_subject_status`.

Produces:

```python
# review/actions.py
STAFF_DECISIONS = (Decision.FALSE_POSITIVE, Decision.POSSIBLE_TRUE_MATCH, Decision.TRUE_MATCH, Decision.DELISTED)
FIRST_REVIEW = frozenset({Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO})
SECOND_REVIEW = frozenset({Role.SENIOR_ANALYST, Role.MLRO})
@dataclass(frozen=True, slots=True)
class ActionSet: allowed: tuple[Decision, ...]; blocked: Mapping[Decision, tuple[BlockedReason, ...]]
def evaluate_actions(match: ScreeningMatch, *, escalated_by: UUID | None, roles: frozenset[Role], user_id: UUID) -> ActionSet
    # per decision, reasons in this order (all that apply):
    # STATE: FALSE_POSITIVE needs OPEN or ESCALATED; POSSIBLE_TRUE_MATCH needs OPEN; TRUE_MATCH needs ESCALATED; DELISTED needs OPEN or ESCALATED
    # ROLE_REQUIRED: from OPEN, FALSE_POSITIVE and POSSIBLE_TRUE_MATCH need FIRST_REVIEW; TRUE_MATCH, FALSE_POSITIVE from ESCALATED and DELISTED need SECOND_REVIEW
    # SAME_REVIEWER: TRUE_MATCH, and FALSE_POSITIVE from ESCALATED, when user_id == escalated_by
    # NOT_DELISTED: DELISTED when match.suggested_disposition != DELISTED
def escalated_by(s: Session, match_id: UUID) -> tuple[UUID | None, UUID | None]   # (decided_by, disposition id) of the latest POSSIBLE_TRUE_MATCH

# errors.py
class DispositionNotAllowedError(DomainError):
    default_code = "DISPOSITION_NOT_ALLOWED"; default_detail = "this decision is not allowed"
    def __init__(self, reasons: Sequence[BlockedReason]) -> None    # sets self.blocked_reasons = tuple(r.value for r in reasons)

# review/dispositions.py
def disposition_match(s: Session, match_id: UUID, *, decision: Decision, rationale: str) -> MatchDisposition
    # staff only (actor_type STAFF, else ForbiddenError); SUPERSEDED -> ValidationFailedError; rationale stripped, 10..2000 chars
    # lock the match FOR UPDATE (404 when unknown); evaluate_actions -> DispositionNotAllowedError(blocked[decision]) when not allowed
    # policy = policy_for_run(s); insert MatchDisposition(decision, rationale=encrypt(...), decided_by_type=STAFF, decided_by=user,
    #   prior_disposition_id = escalation id for TRUE_MATCH and for FALSE_POSITIVE from ESCALATED, policy_snapshot_id)
    # match.status: FALSE_POSITIVE | ESCALATED (for POSSIBLE_TRUE_MATCH) | TRUE_MATCH | DELISTED; closed_at for the terminal ones; updated_at
    # FALSE_POSITIVE -> whitelist_entry(...); record "screening.match_dispositioned"; publish MATCH_DISPOSITIONED {"match_id", "decision", "disposition_id"}
    # TRUE_MATCH -> publish TRUE_MATCH_CONFIRMED {"match_id", "subject_id", "party_id", "list_code",
    #   "on_true_match": snapshot.screening.on_true_match.get(list_code) dumped, or None} with policy_snapshot_id
    # refresh_subject_status(subject)

# review/whitelist.py
def active_whitelist_keys(s: Session, subject_id: UUID) -> set[tuple[str, str, bytes]]
def whitelist_entry(s: Session, match: ScreeningMatch, disposition: MatchDisposition) -> ScreeningWhitelist
    # INSERT ... ON CONFLICT (finding key) DO UPDATE SET disposition_id, revoked_at = NULL, revoked_by = NULL, created_at = now()
def revoke_whitelist(s: Session, whitelist_id: UUID) -> ScreeningWhitelist
    # MLRO only (ForbiddenError/ROLE_FORBIDDEN otherwise); already revoked -> unchanged, no audit; else revoked_at, revoked_by;
    # record "screening.whitelist_revoked"
```

- [ ] **Step 1: Write the failing tests.**

```python
# test_actions.py: the acceptance test of spec §9, mirroring sentinel's
STATES = list(MatchStatus); SUGGESTIONS = [None, SuggestedDisposition.DELISTED]
ROLES = [Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO, Role.AUDITOR_RO, Role.ADMIN]
EXPECTED: dict[tuple[MatchStatus, Decision], ...]   # written by hand from the spec table, not from the implementation
@pytest.mark.parametrize(("state", "suggestion", "role", "same_user", "decision"), itertools.product(STATES, SUGGESTIONS, ROLES, [False, True], STAFF_DECISIONS))
def test_the_disposition_matrix_is_exactly_the_spec(state, suggestion, role, same_user, decision)
    # allowed iff the spec row allows it; otherwise the blocked reasons equal the oracle's, in order
def test_terminal_states_allow_nothing()                    # TRUE_MATCH, FALSE_POSITIVE, DELISTED, SUPERSEDED: every decision blocked by STATE

# test_dispositions.py
def test_an_analyst_escalates_and_a_senior_confirms_a_true_match(...)
    # analyst POSSIBLE_TRUE_MATCH -> ESCALATED; senior TRUE_MATCH -> TRUE_MATCH, prior_disposition_id = escalation; subject TRUE_MATCH;
    # TRUE_MATCH_CONFIRMED data on_true_match == {"action": "SUSPEND_IMMEDIATELY", "report": "MX_F41_ROI24", "notify_client": False} for UN_CONSOLIDATED
def test_the_escalating_user_cannot_confirm(...)            # same senior escalates then confirms -> 409, blocked_reasons ["SAME_REVIEWER"]
def test_a_false_positive_whitelists_the_exact_content(...) # whitelist row with the finding's hash; subject CLEAR
def test_a_second_review_clearance_needs_another_person(...)
def test_delisted_needs_the_suggestion_and_a_senior_role(...)
def test_the_rationale_is_encrypted_and_never_audited(...)  # everything_stored has no rationale text; audit detail {"decision", "disposition_id"}
def test_api_clients_never_disposition(...)                 # API_CLIENT context -> ForbiddenError
def test_superseded_is_not_a_staff_decision(...)

# test_four_eyes_trigger.py
def test_the_four_eyes_trigger_refuses_a_same_user_row_inserted_in_sql(engine, tenant)
    # raw INSERT of a TRUE_MATCH whose prior is the same user's escalation -> IntegrityError naming match_disposition_four_eyes
def test_the_trigger_accepts_a_different_user_and_system_rows(engine, tenant)

# test_whitelist.py
def test_revoking_is_mlro_only_audited_and_idempotent(...)
def test_a_false_positive_after_a_revocation_reactivates_the_row(...)
```

`tests/platform/test_api_conventions.py`: `test_a_problem_carries_blocked_reasons_only_when_the_error_has_them`. `tests/platform/test_errors.py`: `(DispositionNotAllowedError, 409, "DISPOSITION_NOT_ALLOWED")`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/review tests/platform/test_api_conventions.py tests/platform/test_errors.py --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Step 5: Commit** `feat(screening): four-eyes dispositions, allowed actions and the content-keyed whitelist`.

---

### Task E7: The screening API for both audiences

Depends on **E5** and **E6**. No console (E9 and E10 regenerate the types). Mounts the router in `src/xolotl/app.py`.

**Files:**
- Create: `src/xolotl/modules/screening/routes_screening.py`; `src/xolotl/modules/screening/schemas.py` (create or append)
- Modify: `src/xolotl/app.py` (router list, after the party routers)
- Test: `tests/modules/screening/route_support.py`, `tests/modules/screening/test_routes_screening.py`

**Interfaces:**

Consumes: `screen`, `Trigger`, `subject_for_party`, `create_ad_hoc_subject`, `AdHocSubjectInput`, `disposition_match`, `evaluate_actions`, `escalated_by`, `revoke_whitelist`, `require_access`, `SessionDep`, `PageDep`, `paginate`, `Page`, `mask_identifier`, `initials`, `screening_profile`, `record`, `VIEW_PII`, `bound_context` (request id).

Produces (`schemas.py`; every enum typed so the console's generated types carry it):

```python
class CheckRequest(BaseModel):          # exactly one of party_id and subject
    party_id: UUID | None = None; subject: AdHocSubjectInput | None = None
    lists: list[str] | None = Field(None, max_length=20)          # staff only: an API client sending it -> 422 VALIDATION_FAILED
class CheckResponse(BaseModel): run_id: UUID; subject_id: UUID; status: SubjectStatus; screened_at: datetime          # client view
class HitView(BaseModel): id: UUID; list_code: str; list_kind: str; band: HitBand; score: Decimal; auto_reason: AutoReason | None
    match_id: UUID | None; features: dict[str, JsonValue]
class CheckStaffResponse(CheckResponse): hits: list[HitView]
class RunResponse(BaseModel): id: UUID; subject_id: UUID; party_id: UUID | None; mode: RunMode; outcome: SubjectStatus; created_at: datetime
class ListSetMemberView(BaseModel): list_code: str; list_version_id: UUID | None; private_list_version_id: UUID | None
class RunStaffResponse(RunResponse): trigger_type: TriggerType; trigger_ref: str | None; list_set: list[ListSetMemberView]
    matcher_version: str; scoring_config_hash: str; policy_snapshot_id: UUID; candidate_count: int; hit_count: int
    new_match_count: int; latency_ms: int; hits: list[HitView]
class PartyScreeningResponse(BaseModel): party_id: UUID; status: SubjectStatus | None; last_screened_at: datetime | None; lists_covered: list[str]
class MatchSummary(BaseModel): id: UUID; subject_id: UUID; party_id: UUID | None; list_code: str; list_kind: str; band: HitBand
    score: Decimal; status: MatchStatus; suggested_disposition: SuggestedDisposition | None; created_at: datetime; updated_at: datetime
class PartyScreeningStaffResponse(PartyScreeningResponse): open_finding_count: int; findings: list[MatchSummary]
class MaskedSubject(BaseModel): kind: SubjectKind; party_id: UUID | None; entity_type: str; initials: str; identifiers: list[str]   # "RFC ****XY0"
class DispositionView(BaseModel): id: UUID; decision: Decision; decided_by_type: DecidedByType; decided_by: UUID | None; decided_at: datetime
class HitHistoryItem(BaseModel): hit_id: UUID; run_id: UUID; created_at: datetime; score: Decimal; band: HitBand
class MatchDetail(MatchSummary): subject: MaskedSubject; entry: dict[str, JsonValue] | None    # shared ListEntry JSON; None for private lists
    origin_hit: HitView; latest_hit: HitView; history: list[HitHistoryItem]; dispositions: list[DispositionView]
    whitelist_id: UUID | None; allowed_actions: list[Decision]; blocked_reasons: dict[Decision, list[BlockedReason]]
class MatchReveal(BaseModel): subject_names: list[str]; birth_date: date | None; identifiers: list[RevealedSubjectIdentifier]
    private_entry: dict[str, JsonValue] | None; rationales: dict[UUID, str]     # disposition id -> rationale
class DispositionRequest(BaseModel): decision: Literal["FALSE_POSITIVE", "POSSIBLE_TRUE_MATCH", "TRUE_MATCH", "DELISTED"]; rationale: str = Field(min_length=10, max_length=2000)
class DispositionResponse(BaseModel): id: UUID; match_id: UUID; decision: Decision; match_status: MatchStatus; decided_at: datetime
```

Routes (`router = v1_router(prefix="/screening", tags=["screening"])`):

| Handler | Route | Access | Notes |
|---|---|---|---|
| `create_check` | `POST /checks` (201) | `require_access(scope="screening:write", write=True)` | party: `subject_for_party(for_update=True)`; inline: `create_ad_hoc_subject` plus audit `screening.check_requested` after the run; `screen(mode=AD_HOC, trigger=Trigger(CONSOLE if staff else API, request id))`; `CheckStaffResponse \| CheckResponse` |
| `get_run` | `GET /runs/{run_id}` | scope `screening:read` | `RunStaffResponse \| RunResponse` |
| `get_party_screening` | `GET /parties/{party_id}` | scope `screening:read` | 404 for an unknown party; `status: null` when never screened; `lists_covered` = codes of the last run's list set |
| `list_matches` | `GET /matches` | staff | filters `status`, `band`, `list_code`, `list_kind`, `created_before`; `paginate(ts_col=ScreeningMatch.created_at, ...)` |
| `get_match` | `GET /matches/{match_id}` | staff | `MatchDetail`; `allowed_actions` for the current user |
| `reveal_match` | `GET /matches/{match_id}/reveal` | staff | one `VIEW_PII` per non-empty group: `subject` (entity `screening_subject`), `private_entry` (entity `screening_match`), `rationales` (entity `screening_match`, `count`) |
| `create_disposition` | `POST /matches/{match_id}/dispositions` (201) | staff, write | Idempotency-Key |
| `revoke_whitelist_entry` | `DELETE /whitelist/{whitelist_id}` (204) | `require_access(roles=(Role.MLRO,))` | |

The masked subject comes from `screening_profile` for parties and from the decrypted input for ad hoc subjects: `initials(...)` over the names and `mask_identifier` over each screened identifier. Nothing is audited, because nothing plaintext is shown.

- [ ] **Step 1: Write the failing tests** (`test_routes_screening.py`, with `route_support.py` helpers `staff_client(role)`, `api_client(scopes)`, `seed_match(...)` over E5's fixtures):

```python
def test_a_staff_party_check_returns_hits_with_explanations(...)        # 201, hits[0].band ALERT, features.alignment present
def test_an_api_client_check_returns_status_only(...)                    # body keys == {"run_id", "subject_id", "status", "screened_at"}, status PENDING_REVIEW
def test_client_views_expose_status_only(app)
    # walk the OpenAPI schemas of CheckResponse, RunResponse, PartyScreeningResponse: no property named list_code, score, band, hits,
    # features, findings, entry, open_finding_count, explanation (recursively)
def test_an_api_client_cannot_narrow_lists(...)                          # 422
def test_an_inline_subject_check_is_audited_without_its_input(...)       # audit screening.check_requested detail {"run_id"}; no name in audit or logs
def test_a_check_needs_exactly_one_of_party_and_subject(...)             # both / neither -> 422
def test_the_check_is_idempotent(...)                                    # same Idempotency-Key -> same run_id, one run row
def test_the_party_view_for_a_never_screened_party_has_no_status(...)
def test_the_match_queue_filters_and_paginates(...)                       # band=ALERT, status=OPEN, list_kind, created_before; cursor pages
def test_the_match_detail_is_masked_and_lists_the_allowed_actions(...)   # analyst: allowed [FALSE_POSITIVE, POSSIBLE_TRUE_MATCH]; blocked TRUE_MATCH: [STATE, ROLE_REQUIRED]
def test_the_reveal_records_view_pii_per_group(...)                      # subject + rationales -> two VIEW_PII rows; the detail GET records none
def test_a_refused_disposition_is_a_409_with_blocked_reasons(...)        # problem body blocked_reasons == ["SAME_REVIEWER"]
def test_matches_dispositions_and_reveal_are_staff_only(...)             # API key with every scope -> 403 on each
def test_auditor_reads_but_never_writes(...)                              # AUDITOR_RO: GET 200, POST disposition 403
def test_the_whitelist_revoke_is_mlro_only(...)                           # ANALYST 403, MLRO 204
def test_another_tenant_sees_nothing(...)                                 # runs, matches, party views -> 404 for tenant B
def test_the_index_loading_is_a_503(...)                                  # holder warming -> 503 SCREENING_INDEX_UNAVAILABLE
def test_no_plaintext_is_stored_or_logged_beyond_the_name_index(admin_engine, caplog, ...)
    # after party check, ad hoc check, disposition and reveal: leaked(everything_stored minus party_name_index and screening_hit.features, names, RFC, rationale) == []
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/test_routes_screening.py --no-cov -q`.
- [ ] **Step 3: Implement** the schemas, the routes and the router mount.
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README API overview (or `docs/` API notes, wherever the party routes are listed) gains the screening routes with their audiences.
- [ ] **Step 5: Commit** `feat(screening): checks, runs, matches and dispositions API for staff and clients`.

---

### Task E8: Party event subscribers, the screen-party job, replay and the screening CLI

Depends on **E5** (and E3's composition root). It does not need E6 or E7, so it runs in parallel with them. **LIB-matching** (`score`, `band_for`, `IdfTable.build`, `MATCHER_VERSION`) and **LIB-lists** (`ListEntry.from_json`) are needed for replay. No console. Touches `src/xolotl/cli.py`, `src/xolotl/modules/screening/composition.py` and one line of `src/xolotl/app.py` (the `register_screening(...)` call gains `queue=queue`).

**Files:**
- Create: `src/xolotl/modules/screening/rescreen/{__init__,party_events}.py` (`__init__.py` with a module docstring only; 3c's F1 adds `register_rescreen` to it), `src/xolotl/modules/screening/engine/replay.py`
- Modify: `src/xolotl/modules/screening/composition.py`, `src/xolotl/modules/screening/errors.py`, `src/xolotl/app.py` (one argument), `src/xolotl/cli.py`, `tests/test_cli.py`, `tests/platform/test_errors.py`
- Test: `tests/modules/screening/rescreen/__init__.py`, `tests/modules/screening/rescreen/test_party_events.py`, `tests/modules/screening/engine/test_replay.py`

**Interfaces:**

Consumes: `register_subscriber`, `Event`, `PostgresJobQueue.enqueue`, `register_job_handler`, `Job`, `JobRunner` (tests), `PARTY_CREATED`, `PARTY_UPDATED`, `IDENTIFIER_ADDED` (`xolotl.modules.party.events`), `index_party_names`, `subject_for_party`, `screen`, `Trigger`, `shared_version_entries`, `indexed_private_entry`, `query_for_party`, `query_for_ad_hoc`, `thresholds_for`; CLI helpers `_cli_actor`, `_cli_errors`, `_resolve_tenant`, `_ensure_key_provider`, `tenant_session(TenantContext.platform(actor, tenant_id=...))`.

Produces:

```python
# rescreen/party_events.py
SCREENING_CONSUMER = "screening"
SCREEN_PARTY_KIND = "screening.screen_party"
PARTY_EVENT_MODES: Mapping[str, RunMode] = {PARTY_CREATED: RunMode.NEW_SUBJECT, PARTY_UPDATED: RunMode.DATA_CHANGE, IDENTIFIER_ADDED: RunMode.DATA_CHANGE}
def make_party_event_handler(queue: PostgresJobQueue) -> Handler
    # handler(s, event): party_id = UUID(event.data["party_id"]); index_party_names(s, party_id);
    # queue.enqueue(s, kind=SCREEN_PARTY_KIND, key=f"screen_party:{party_id}:{event.id}",
    #   payload={"party_id", "event_id", "event_time": event.time.isoformat(), "mode": PARTY_EVENT_MODES[event.type]})
def screen_party_job(s: Session, job: Job) -> None
    # party PURGED -> return; subject = subject_for_party(for_update=True); skip when a run of the subject has created_at > event_time;
    # screen(s, subject, mode=payload mode, trigger=Trigger(TriggerType.EVENT, payload["event_id"]))

# composition.py (append to register_screening; it gains queue: PostgresJobQueue)
#   for t in PARTY_EVENT_MODES: register_subscriber(t, handler, consumer=SCREENING_CONSUMER)
#   register_job_handler(SCREEN_PARTY_KIND, screen_party_job)
#   (plan 3c's F5 registers party.purged.v1 here, under the same SCREENING_CONSUMER; 3c's batch jobs and its consumers of
#   the two catalog events go in rescreen/__init__.py's register_rescreen(*, queue), which wire_platform calls right after
#   register_screening (F1))

# engine/replay.py
@dataclass(frozen=True, slots=True)
class ReplayRow: hit_id: UUID; list_code: str; entry_key: str; stored_score: Decimal; stored_band: HitBand
    replayed_score: Decimal; replayed_band: Band | HitBand; same: bool
@dataclass(frozen=True, slots=True)
class ReplayReport: run_id: UUID; matcher_version: str; subject_changed: bool; rows: tuple[ReplayRow, ...]
    @property
    def identical(self) -> bool                    # not subject_changed and all rows same
def replay_run(s: Session, run_id: UUID) -> ReplayReport
    # run.matcher_version != MATCHER_VERSION -> MatcherVersionMismatchError
    # rebuild the query (party or ad hoc); fingerprint differs -> ReplayReport(subject_changed=True, rows=())
    # idf = IdfTable.build(ListEntry.from_json(r.entry) for each shared member m, r in shared_version_entries(s, m.list_code, seq of m.list_version_id))
    # config = ScoringConfig rebuilt from the run's policy snapshot bands; assert config.config_hash() == run.scoring_config_hash (else subject_changed-like
    #   refusal: MatcherVersionMismatchError "scoring config differs")
    # per stored hit: entry from list_entry.entry (or the decrypted private entry); score; band_for with features["thresholds"];
    #   a WHITELISTED hit compares the score and the would-be band recorded in features["band_before_whitelist"]

# errors.py
class MatcherVersionMismatchError(DomainError): default_code = "MATCHER_VERSION_MISMATCH"; default_detail = "the run was scored by another matcher version"
```

So that replay can compare a whitelisted hit, E8 adds `"band_before_whitelist"` to E5's `hit_features` (one key, set only on `WHITELISTED` hits). It is a small edit to `engine/features.py`, which E5 owns. If E5 is still open, the key goes into E5 instead.

CLI (`src/xolotl/cli.py`): `screening_app = typer.Typer(no_args_is_help=True, help="Screening")`, `app.add_typer(screening_app, name="screening")`:

```python
@screening_app.command("check")
def screening_check(*, tenant: Annotated[str, typer.Option("--tenant")], party: Annotated[UUID, typer.Option("--party")]) -> None:
    """Screen one party now against the tenant's lists."""
    # platform context narrowed to the tenant; subject_for_party(for_update=True); screen(mode=AD_HOC, trigger=Trigger(CLI, actor))
    # echo f"run {run.id}: {run.outcome} ({run.hit_count} hits, {run.new_match_count} new findings)"

@screening_app.command("replay")
def screening_replay(run_id: UUID, *, tenant: Annotated[str, typer.Option("--tenant")]) -> None:
    """Re-score a run's hits and say whether they are identical (NFR-SC-007)."""
    # one line per row "<list_code> <entry_key> <stored_score> <stored_band> -> <replayed_score> <replayed_band> <same|DIFFERENT>";
    # last line "identical" (exit 0) or "subject changed" / "N differences" (exit 1)
```

`plan 3c` adds `screening rescreen` and `screening eval` to `screening_app`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_party_events.py
def test_creating_a_party_indexes_its_names_and_screens_it(...)
    # register_screening(...); create_party -> dispatch -> party_name_index rows; one screening.screen_party job; run it -> run mode NEW_SUBJECT, trigger EVENT
def test_an_update_and_an_identifier_rescreen_as_data_change(...)
def test_redelivery_enqueues_no_second_job(...)                            # bus.replay of the same event -> job key dedup
def test_a_job_skips_when_a_later_run_already_covered_the_event(...)       # a synchronous check after the event -> the job makes no run
def test_a_purged_party_is_not_screened(...)

# test_replay.py
def test_replay_reproduces_every_hit_of_a_fixture_tenant(...)
    # fixture tenant: two parties, an ad hoc subject, shared UN and OFAC lists, a private MX_LPB, a whitelisted entry; a run each;
    # then a new UN version is loaded (changes the holder's IDF) -> replay_run(...) identical for every run
def test_replay_reports_a_changed_subject_and_does_not_rescore(...)        # add an identifier after the run -> subject_changed, rows ()
def test_replay_refuses_another_matcher_version(monkeypatch, ...)          # patch MATCHER_VERSION -> MatcherVersionMismatchError

# tests/test_cli.py (append)
def test_screening_check_and_replay(...)
    # screening check --tenant acme --party <id> -> exit 0, "run <uuid>: PENDING_REVIEW (...)"; screening replay <run> --tenant acme -> last line "identical";
    # unknown tenant -> exit 1 with "error: NOT_FOUND: no tenant 'nope'" on stderr; --help works without a database
```

`tests/platform/test_errors.py`: `(MatcherVersionMismatchError, 409, "MATCHER_VERSION_MISMATCH")`. `tests/platform/test_worker.py` and `test_composition_root.py`: the job kind `screening.screen_party` is registered and the consumer `screening` is among `consumers()`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/rescreen tests/modules/screening/engine/test_replay.py tests/test_cli.py -k "screening or replay or party_event" --no-cov -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then the gates, then `make test`.
- [ ] **Docs:** README CLI section gains `xolotl screening check` and `xolotl screening replay`.
- [ ] **Step 5: Commit** `feat(screening): re-screen on party changes, replay runs and the screening CLI`.

---

### Task E9: Console: the match queue and the match detail with explanation and dispositions

Depends on **E7** (regenerate `schema.d.ts` against it). Console only. 3a's Listas pages and E10 also append to `nav.ts`, `App.tsx`, `access.ts`, both catalogues and `catalogue.test.ts`. Whichever merges later rebases and re-applies its own lines (additive), and regenerates `schema.d.ts` instead of merging it.

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `en.json`, `catalogue.test.ts`
- Create: `console/src/features/screening/{queries.ts, queries.test.tsx, fixtures.ts, BandBadge.tsx, MatchStatusBadge.tsx, MatchesPage.tsx, MatchesPage.test.tsx, MatchPage.tsx, MatchPage.test.tsx, ExplanationTable.tsx, ExplanationTable.test.tsx, DispositionForm.tsx, DispositionForm.test.tsx, MatchRevealCard.tsx}`

**Interfaces:**

Consumes: the console kit the party pages use (`api`, `unwrap`, `useIdempotentMutation`, `hasProblemCode`, `useAuth`, `ACCESS`, `hasAnyRole`, `RequireRole`, `PAGE_SIZE`, `PageHeader`, `DataTable`, `CursorPager`, `DateTimeCell`, `KeyValues`, `Badge`, `Card*`, `Select`, `Textarea`, `Button`, `FormAlert`, `Tooltip`, `useFormat`, `renderWithProviders`/`fakeApi`); E7's routes.

Produces:
- `types.ts`: `MatchSummary`, `MatchDetail`, `MatchReveal`, `HitView`, `DispositionRequest`, `DispositionResponse`, `MatchStatus`, `HitBand`, `Decision`, `BlockedReason`, `Page_MatchSummary_`.
- `access.ts`: `screening: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']`, `dispositions: ['ANALYST', 'SENIOR_ANALYST', 'MLRO']`, `whitelistRevoke: ['MLRO']`, with `access.test.ts` and `guards.test.tsx` rows.
- `nav.ts`: `'nav.matches'`, `{ to: '/screening/matches', labelKey: 'nav.matches', icon: ShieldAlert, roles: ACCESS.screening }` after parties.
- `App.tsx`: a `RequireRole roles={ACCESS.screening}` group with `/screening/matches` (`MatchesPage`) and `/screening/matches/:matchId` (`MatchPage`).
- `queries.ts`: `useMatches(filters, cursor)`, `useMatch(matchId)` (masked; `useQuery`), `useRevealMatch(matchId)` (`useMutation`, `gcTime: 0`), `useDisposition(matchId)` (`useIdempotentMutation`, invalidates the match and the queue), `useRevokeWhitelist()`.
- `MatchesPage`: tabs `ALERT` (default) and `LOW_CONFIDENCE` driving `band` (Deviation 9); filters status (default `OPEN`), list kind, list code, "older than" (days → `created_before`); columns subject (initials and masked ids), list, band, score, status (with a `suggested_disposition` badge `screening.suggested.DELISTED`), created; `CursorPager`.
- `MatchPage`: subject (masked) beside the entry (`KeyValues` of names, aliases, birth dates, nationalities, identifiers masked, programmes, listed on); `ExplanationTable` over `latest_hit.features` (query token, entry token, kind, similarity, weight; unmatched tokens; qualifiers with outcome and adjustment; rules applied; score against `thresholds.alert` and `thresholds.review`; list, version seq, matcher version); hit history; disposition timeline; `DispositionForm` showing one button per `allowed_actions` decision, the other decisions disabled with a `Tooltip` naming `blocked_reasons` (`screening.blocked.<REASON>`), a required rationale (10 to 2000 characters), and a confirm step for `TRUE_MATCH`; `MatchRevealCard` ("Revelar", audited notice, `aria-live`); "Revocar lista blanca" for `ACCESS.whitelistRevoke` when `whitelist_id` is set.
- i18n (both catalogues): `nav.matches`; `screening.matches.{title,description,tabs.ALERT,tabs.LOW_CONFIDENCE,empty,filters.*,table.*}`, `screening.band.*`, `screening.status.*`, `screening.suggested.DELISTED`, `screening.match.{title,subject,entry,explanation.*,qualifiers.*,rules.*,history,timeline,thresholds}`, `screening.decision.*`, `screening.blocked.*`, `screening.disposition.{rationale,submit,confirmTrueMatch,done}`, `screening.reveal.*`, `screening.whitelist.revoke*`; `errors.DISPOSITION_NOT_ALLOWED`, `errors.SCREENING_INDEX_UNAVAILABLE`. The kind codes (`EXACT`, `FUZZY`, …) are translated, not shown raw.

- [ ] **Step 1: Write the failing tests:** `MatchesPage.test.tsx` (`opens on the ALERT tab with OPEN findings`, `switching tab requests LOW_CONFIDENCE`, `shows the delisting suggestion`); `MatchPage.test.tsx` (`renders the aligned tokens, qualifiers and thresholds`, `offers only the allowed decisions and explains the blocked ones`, `a refused disposition shows its blocked reasons`, `the reveal is a mutation and is forgotten on hide`); `ExplanationTable.test.tsx` (`every match kind and qualifier outcome has a label`); `DispositionForm.test.tsx` (`the rationale is required and at least ten characters`, `true match asks for confirmation`); `access.test.ts` and `guards.test.tsx` rows.
- [ ] **Step 2: Run to verify failure.** `cd console && npm test -- screening`.
- [ ] **Step 3: Implement**, regenerating `schema.d.ts` (`npm run gen:api:file` against the merged API).
- [ ] **Step 4: Run to verify pass.** `cd console && npm ci && npm run typecheck && npm run lint && npm test && npm run build`.
- [ ] **Step 5: Commit** `feat(console): match queue and match detail with explanations and dispositions`.

---

### Task E10: Console: the party screening card and the ad hoc check

Depends on **E7**, on **3a's D7** (`GET /v1/lists` for the list filter), and on **2c: C9** merged (both edit `PartyPage.tsx`). It runs in parallel with E9; the shared files are additive (see E9). The smoke step needs no lists to be loaded.

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, both catalogues, `catalogue.test.ts`, `console/src/features/parties/PartyPage.tsx`, `console/e2e/smoke.spec.ts`
- Create: `console/src/features/screening/{CheckPage.tsx, CheckPage.test.tsx, check-form.ts, check-form.test.ts, PartyScreeningCard.tsx, PartyScreeningCard.test.tsx, HitList.tsx}`; `queries.ts` gains `usePartyScreening(partyId)`, `useCheck()` (create it if E9 has not merged)

**Interfaces:**

Produces:
- `access.ts`: `screeningCheck: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'ADMIN']` (plus `screening` if E9 has not merged).
- `nav.ts`: `'nav.screeningCheck'`, `{ to: '/screening/check', labelKey: 'nav.screeningCheck', icon: ScanSearch, roles: ACCESS.screeningCheck }`.
- `PartyScreeningCard` on `PartyPage` (for `ACCESS.screening`): status badge (or "never screened"), last screened, lists covered, open finding count, findings (links to `/screening/matches/:id`), and "Revisar ahora" (`ACCESS.screeningCheck`) posting `{party_id}` and showing the staff result through `HitList`.
- `CheckPage`: a person/organization toggle; names (up to three), given names and surnames for persons, birth date, gender, nationalities, countries, up to ten identifiers (type and value); an optional list filter (`Select` multi over the codes of `GET /v1/lists`, 3a's D7); submit posts `{subject}`; the result shows status and `HitList` (band, list, score, a link to the finding when `match_id` is set). The form is cleared and the result forgotten on leaving the page. Nothing is kept in storage.
- `check-form.ts`: a zod schema mirroring `AdHocSubjectInput` and `toCheckRequest(values)`.
- i18n: `nav.screeningCheck`; `screening.party.{title,never,lastScreened,listsCovered,openFindings,checkNow,running}`; `screening.check.{title,description,entityType,names,addName,givenNames,firstSurname,secondSurname,birthDate,gender.*,nationalities,countries,identifiers,addIdentifier,lists,submit,result,noHits}`; `screening.hits.*`.
- Smoke (`console/e2e/smoke.spec.ts`): one step, `an ad hoc check returns a status`, after the party step. It opens Consulta ad hoc, enters a person name, submits, and sees a status badge, with no assertion on hits: the smoke stack may have no lists loaded. The four-eyes Playwright flow of spec §16 (LPB upload, escalate, confirm as a second user) needs loaded lists and a second staff user and is left to plan 3c's exit test (Open question 3).

- [ ] **Step 1: Write the failing tests:** `PartyScreeningCard.test.tsx` (`shows never screened`, `check now posts the party id and shows the hits`, `hidden actions for AUDITOR_RO`); `CheckPage.test.tsx` (`a person check posts the subject and shows the status`, `validation errors map to fields`, `the result is forgotten on leaving`); `check-form.test.ts`; access rows.
- [ ] **Step 2: Run to verify failure.** `cd console && npm test -- screening parties`.
- [ ] **Step 3: Implement**, regenerating `schema.d.ts`.
- [ ] **Step 4: Run to verify pass:** the console gates and the Playwright smoke.
- [ ] **Step 5: Commit** `feat(console): party screening card and ad hoc check`.

---

## Interfaces for plan 3c

Named exactly as the spec does where the spec names them; the others are this plan's and are listed for 3c to consume:
- **`screen(session, subject, *, mode, trigger, lists=None, only_entries=None, only_index=None, require_versions=None, batch_id=None)`** returning **`RunResult`** (E5; `run.new_match_count` is the count 3c's batches add up). A delta job passes `require_versions={list_code: seq}` so the holder refreshes to the version it is screening; the private-list walker passes a prebuilt `only_index`; every batch run passes `batch_id`. 3c changes nothing in `screen()`. `Trigger(type, ref)`; `RunMode.LIST_DELTA`, `PRIVATE_LIST`, `PERIODIC`, `REINDEX`; `TriggerType.JOB`.
- **`subject_for_party(session, party_id)`**, **`query_for_party(session, party_id)`**, **`index_party_names(session, party_id)`** (E4); `refresh_subject_status(session, subject)` (E4).
- **`ScreeningIndexHolder`** (E3) via `get_index_holder()`: `shared(s, require=...)` returning `SharedShard` (whose `blocking_idf` 3c's reverse blocking and private-version indexes use), `private(s)`, `scoring_idf(...)`; `indexed_shared_entry(row)`, `indexed_private_entry(s, row, list_code)` and `shared_version_entries(s, code, seq)` to build `only_entries` from delta rows.
- **`blocking_plan`** stays the library's. 3c's Postgres blocking queries `PartyNameIndex` (`norm_name`, `tokens`, `phonetic_keys` with the GIN indexes `ix_pni_phonetic` and `ix_pni_trgm`) and `PartyNameIndex.matcher_version` (reindex check).
- Models and enums of migration 0010 (E2): `ScreeningBatch` (`kind`, `ref`, `status`, `total`, `done`, `runs`, `new_matches`, `cursor`, `last_error`), `BatchKind`, `BatchStatus`; `ScreeningMatch.suggested_disposition` with `SuggestedDisposition.DELISTED`; `ScreeningHit.features` (3c redacts the query-side tokens on purge); `OPEN_MATCH_STATUSES`; index `ix_screening_match_entry` for the delisting lookup.
- Events (E2): `MATCH_DELISTING_SUGGESTED` (`screening.match_delisting_suggested.v1`, already registered staff-only), `publish_screening_event`.
- Composition (E3, E8): **`register_screening(*, session_factory, settings, queue)`** in `screening/composition.py`, where 3c's F5 registers `party.purged.v1` under **`SCREENING_CONSUMER = "screening"`** (`rescreen/party_events.py`). 3c's job kinds `screening.list_delta`, `screening.private_list_rescreen`, `screening.periodic_rescreen`, `screening.reindex` and its consumers of `lists.version_published.v1` and `private_list.version_activated.v1` are registered by 3c's own `register_rescreen(*, queue)`, called by `wire_platform` after `register_screening`; `ensure_screening_index` joins `worker_loop`'s `on_start` in `app.py` (3c F4).
- API (E7): `router` in `routes_screening.py` (prefix `/screening`), where 3c adds `GET /batches` and `GET /evaluation`; `schemas.py` for its response models.
- CLI (E8): `screening_app` in `src/xolotl/cli.py`, where 3c adds `rescreen` and `eval`.
- Console (E9, E10): `console/src/features/screening/` and `ACCESS.screening`, where 3c adds the Evaluación page.
- Replay (E8): `replay_run(session, run_id) -> ReplayReport`, for the exit test.

## Parallelism

- **E1** has no dependency and should start first: it is also what 3a's staff-only `private_list.version_activated.v1` relies on.
- **E2** waits for 3a's D1 (migration 0009 and models). Then **E3 ∥ E4** (disjoint files; E3 edits `app.py` and `config.py`, E4 edits `party/service.py` and `.importlinter`). E4 does not need E3.
- **E5** after E3 and E4. **E6** after E4, in parallel with E5. E6 only reads `engine/policy.py`: if E5 has not merged, E6 adds `policy_for_run` there and E5 rebases. Both may touch `review/whitelist.py`: E5 writes `active_whitelist_keys`, E6 the rest.
- **E7** after E5 and E6 (edits `app.py`). **E8** after E5, in parallel with E6 and E7 (edits `cli.py`, `composition.py` and one argument in `app.py`'s `wire_platform`, which does not collide with E7's router line). E8 adds one key to `engine/features.py`, so it rebases on E5.
- **E9 ∥ E10** after E7 (console only; E10 also waits for 2c's C9). They share the additive console files and regenerate `schema.d.ts` against the merged API.
- **Across plans.** 3a and 3b both append to `src/xolotl/app.py`, `src/xolotl/cli.py`, `src/xolotl/models.py`, `src/xolotl/platform/config.py`, `.env.example`, `README.md`, `.importlinter`, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `test_config.py`, `test_errors.py`, `tests/test_cli.py`, `src/xolotl/modules/screening/{events,errors,schemas}.py` and the console's nav, routes, access and catalogues. Every edit is additive, so whichever task merges second rebases. 3a's D6 needs only **E1** (it registers its staff-only event itself); no 3a task touches `screening_batch`. E3 needs 3a's D4 and D6 for the test loaders, E10 needs D7. 3c needs E5 for every job and E8 for the consumer and package it extends.

## Exit criteria

Spec §16 rows `engine/*`, `review/*` and platform, refined for this plan:
- `make ci` green after every task; the console gates and the Playwright smoke green after E9 and E10.
- Every new table passes the RLS harness and the schema-sync gate; `match_disposition` is append-only and its four-eyes trigger refuses a same-user answer inserted directly in SQL (E2, E6).
- A party whose name is a perturbed OFAC or LPB entry, checked through `POST /v1/screening/checks`, gives staff an `ALERT` hit with aligned tokens, qualifiers, list version and matcher version, while the API client sees only `PENDING_REVIEW` (E5, E7).
- Every run records mode, trigger, exact list versions, policy snapshot, matcher version, scoring config hash and query fingerprint, and `xolotl screening replay` reproduces a fixture tenant's runs exactly (E5, E8).
- One finding per subject and entry content, also under concurrency. A whitelisted content is suppressed and logged; a changed entry re-alerts and supersedes; a revoked whitelist alerts again (E5, E6).
- The disposition matrix matches spec §9 exhaustively, and `TRUE_MATCH` publishes `screening.true_match_confirmed.v1` with the pack's `on_true_match` (E6).
- A wildcard webhook endpoint receives no `screening.match_*`, `screening.true_match_confirmed.v1` or `private_list.version_activated.v1` delivery, and an endpoint naming one is refused (E1, E5).
- Creating or changing a party re-indexes its names and re-screens it through the `screening.screen_party` job, at most once per event (E8).
- Tenant B never hits tenant A's private list and cannot read A's runs, hits or findings (E5, E7).
- Console: the queue opens on alerts; the detail shows the explanation and offers only the allowed decisions with their blocked reasons; the party card screens on demand; the ad hoc check returns a status in the smoke (E9, E10).

## Open questions for the product owner

1. **A tenant without a policy assignment cannot be screened.** `screen()` propagates `POLICY_NOT_ASSIGNED` (404 on a check), and the `screening.screen_party` job fails and retries until it is dead, visible in the job table. This plan treats it as a configuration error to be fixed rather than screening with a platform default. Confirm, or name a default list set.
2. **Re-screening on every `party.updated.v1`.** The event carries only the names of the changed fields. This plan re-screens on any update (the patchable fields include nationalities, gender and trade name, which change the query) instead of filtering by field. Confirm that the volume is acceptable.
3. **The four-eyes Playwright flow** (spec §16 console row: upload an LPB file, screen a party, open the match, escalate, confirm as a second user). It needs loaded lists and a second staff user on the smoke stack. This plan's smoke covers the ad hoc check, and the full flow is proposed for plan 3c's exit test (`tests/e2e/test_sp03_exit.py` already covers it at the API level). Confirm that split.
4. **Who may revoke a whitelist row.** Spec §9 says MLRO only, and this plan builds that. Should a SENIOR_ANALYST who made the original clearance also be allowed to revoke it?
