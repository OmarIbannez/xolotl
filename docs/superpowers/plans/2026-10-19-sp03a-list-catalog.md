# SP-03a — List catalog and private lists — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every task also follows `docs/superpowers/execution/sp02/agreement.md` (gates, worktrees, commits, PRs, report).

**Goal:** Give every tenant a fresh, versioned, auditable set of sanctions lists and its own private lists: the shared `list_source` catalog seeded from `sources.yaml`; versioned snapshots of each list with per-entry history (`from_seq`/`to_seq`) and deltas; one conditional HTTP fetch per source and interval across all tenants, under a lease, guarded against mass removal; offline loading from the CLI; tenant subscriptions derived from the effective policy (mandatory always on, recommended tenant-removable) with a catch-up that hands each new version to re-screening by event, stale and missing-LPB notifications; tenant private lists (`MX_LPB`, `INTERNAL_<SLUG>`) uploaded as CSV or XLSX, validated with row-level errors that never echo a cell, encrypted per tenant and versioned with deltas; the lists and private-list API; and the console's "Listas" and "Listas privadas" pages.

**Architecture:** Everything lives in `src/xolotl/modules/screening/catalog/` (spec §2 layout), which is party-free by an import-linter contract. It consumes the pure screening library under `screening/lists/` (`parse`, `ListEntry`, `diff`; built by a parallel package, not planned here). Shared tables (`list_source`, `list_version`, `list_entry`, `list_entry_delta`) are written by the app role through column grants; raw files go to the object store under the nil-UUID namespace. A per-tenant `lists.poll` job (every minute) claims at most one due source under a lease on `list_source`, fetches it outside the tenant's transaction, ingests it in a short transaction of its own, then, in the tenant's transaction, reconciles subscriptions against `effective_policy(...).screening`, catches the tenant up by publishing one `lists.version_published.v1` per new version, and raises stale and missing-LPB notifications. Re-screening (plans 3b and 3c) subscribes to the two catalog events; nothing in 3a touches `screening_batch` or any 0010 table. Private lists are tenant tables under RLS; entries are `EncryptedJson` with an HMAC content tag under the tenant INDEX key, and the uploaded file is sealed with the tenant DATA key before it is stored.

**Tech Stack:** The SP-01 and SP-02 stack. `httpx` (sync, streaming) and `respx` are already installed. Task D6 adds runtime `openpyxl` (read-only XLSX). Migration 0009 creates the `pg_trgm` extension for plan 3b. No new console dependency.

**Spec:** `docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md` §1 (scope), §1.1 (party-free), §2 (layout, contracts, dependencies, migrations), §3 (the `LISTS_*` and `PRIVATE_LIST_*` keys), §4.1 (the library interface this plan consumes), §5.1 and the 0009 block of §5.2 (tables), §6 (ingestion), §7 (private lists), §11 (the two catalog events, the catalog audit actions), §12 (the lists and private-list rows), §13 (`lists …` commands), §14 (the two catalog pages), §16 (the `catalog/*` suite), §17 Decisions 1, 3, 4, 5, 13, §19 (plan 3a row). Program spec `docs/superpowers/specs/2026-10-18-program-architecture-design.md` D1, D4, D7, D18, D20, D21, §3.3 (amended by spec §2), §4 ports `ObjectStore`, `KeyProvider`, `EventBus`, `JobQueue`, `Notifier`.

## Accepted by the product owner (2026-10-19)

- **Spec departures accepted:** (1) the `lists` module is folded into `screening` (spec Decision 1; `screening.catalog` stays party-free by an import-linter contract); (2) staff-only event types that webhooks never deliver (spec §11; the platform mechanism is plan 3b's, see "Dependencies outside this plan"); (3) forward screening through an in-memory index, reverse screening in Postgres (spec Decision 2; 3b and 3c build it, 3a only creates `pg_trgm`).
- **Open-question defaults accepted (spec §18), as they bind this plan:** Q1 no licensed global or PEP feed (private lists of kind `PEP` are allowed; the catalog takes a licensed feed later as one more `sources.yaml` row and parser); Q2 API clients see status only (3a's tenant-visible surface is `GET /v1/lists` and the private-list scopes, no match detail); Q3 our CSV/XLSX template for the LPB (D6), no UIF-layout parser yet; Q4 monthly periodic re-screen (3c); Q5 69-B name-only resemblance at most `LOW_CONFIDENCE` (library and 3b); Q6 `OFAC_CONS` not ingested (no catalog row); Q7 domestic PEP floor out of SP-03.

## Dependencies outside this plan

| Id | What | Provided by | Needed by |
|---|---|---|---|
| `LIB-lists` T1 | `xolotl.modules.screening.lists`: `ListCode`, `EntityType`, `NameQuality`, `IdType`, `PartialDate`, `EntryName`, `EntryIdentifier`, `EntryAddress`, `ListEntry` (`canonical_json`, `content_hash`, `from_json`), `ParsedList`, `parse`, `ListFormatError` (422 `LIST_FORMAT_INVALID`), `ListDelta`, `diff`; the `UN_CONSOLIDATED` parser (`un-xml/1`) and its trimmed fixture under `tests/modules/screening/fixtures/`; `src/xolotl/modules/screening/__init__.py` and two import-linter contracts | screening library plan (`docs/superpowers/plans/2026-10-19-screening-library.md` T1, branch `feat/screening-lib-un`; prompt `docs/superpowers/handoffs/screening-library.md`) | D4, D5, D6, D7 |
| `LIB-lists` T2 | `OFAC_SDN` (enhanced XML, CSV fallback) and `EU_FSF` parsers | library T2 | no 3a task; `lists load-file`/fetch of those codes works the day it merges |
| `LIB-lists` T3 | `UK_FCDO` and `MX_SAT_69B` parsers | library T3 | no 3a task; as above |
| `PLAN-3b-staff-only` | `xolotl.platform.events.register_staff_only(event_type: str) -> None` and the webhook guard (fan-out skips staff-only types, wildcard included; endpoint create/update refuse them with 422 `EVENT_TYPE_NOT_DELIVERABLE`), spec §11 | plan 3b (a platform-only task with no 0010 dependency) | D6 (publishes the staff-only `private_list.version_activated.v1`) |

D1, D2 and D3 need none of these. The library owns `src/xolotl/modules/screening/__init__.py`; if D1 merges before library T1, D1 creates it with a one-line module docstring and the second to merge keeps one copy.

## Interfaces this plan exposes to plans 3b and 3c

Named as the spec names them; a later reconciliation matches on these.

- **Migration** `0009_lists` (`down_revision = "0008_party"`); 3b's `0010_screening` follows it. Tables exactly as spec §5.1 and the 0009 block of §5.2: `list_source`, `list_version`, `list_entry`, `list_entry_delta` (shared, in `SHARED_TABLES`), `tenant_list_subscription`, `private_list`, `private_list_version`, `private_list_entry`, `private_list_entry_delta` (tenant tables, forced RLS), plus `CREATE EXTENSION IF NOT EXISTS pg_trgm`. Deviations 2 and 3 are the only schema differences.
- **Models** in `xolotl.modules.screening.catalog.models`: `ListSource`, `ListVersion`, `ListEntryRow`, `ListEntryDelta`, `TenantListSubscription`, `PrivateList`, `PrivateListVersion`, `PrivateListEntryRow`, `PrivateListEntryDelta`.
- **Enums** in `xolotl.modules.screening.enums` (3b appends its own): `ListKind {SANCTION, FISCAL, BLOCKED, PEP, INTERNAL}`, `SourceFormat {UN_XML, OFAC_ENHANCED_XML, EU_FSF_XML, UK_SL_XML, SAT_69B_CSV}`, `VersionStatus {ACTIVE, SUPERSEDED}`, `DeltaChange {ADDED, CHANGED, REMOVED}`, `ListRequirement {MANDATORY, RECOMMENDED}`.
- **Read functions** in `xolotl.modules.screening.catalog.queries`: `active_versions(session) -> dict[str, ListVersion]`, `version_members(session, list_code, seq) -> Select[tuple[ListEntryRow]]`, `entry_of(row: ListEntryRow) -> ListEntry`, `delta_page(session, version_id, *, after_key, limit) -> list[DeltaRowView]`, `enabled_subscriptions(session) -> list[TenantListSubscription]`; in `catalog.private`: `active_private_versions(session) -> list[tuple[PrivateList, PrivateListVersion]]`, `private_entry_of(session, row: PrivateListEntryRow) -> ListEntry` (decrypts; no `VIEW_PII`: the engine is not a person).
- **Write function** `ingest_file(session, code, data, *, store, loaded_by, fetched_at, accept_drop=False) -> IngestReport` in `catalog.ingest` (3c's exit test loads fixtures through it or through `xolotl lists load-file`).
- **Events** (`xolotl.modules.screening.events`, source `xolotl.screening`, ids only): `LISTS_VERSION_PUBLISHED = "lists.version_published.v1"` (tenant, webhook-safe) with data `{list_code, version_id, seq, added, changed, removed, initial}`, one per tenant per version handed over (Deviation 1); `PRIVATE_LIST_VERSION_ACTIVATED = "private_list.version_activated.v1"` (staff-only) with data `{private_list_id, version_id}`. **3c subscribes consumer `screening` to both**: on `lists.version_published.v1` it creates `screening_batch(kind=LIST_DELTA, ref=version_id)` and enqueues `screening.list_delta` (key `list_delta:<version_id>`), or, when `initial` is true, the `PERIODIC` batch with ref `initial:<list_code>`; on `private_list.version_activated.v1` it creates the `PRIVATE_LIST` batch and enqueues `screening.private_list_rescreen`.
- **Job kind** `lists.poll` (`LISTS_POLL_KIND`), scheduled per tenant on `XOLOTL_LISTS_POLL_CRON` in UTC.
- **Platform additions:** `SHARED_NAMESPACE = UUID(int=0)` in `xolotl.platform.storage`; `ErrorItem` and `XolotlError.errors` in `xolotl.platform.errors`, rendered as the problem's `errors` (Deviation 10; 3b may use it for `blocked_reasons`).
- **Errors** in `xolotl.modules.screening.errors` (3b appends): `ListMassRemovalError` (409 `LIST_MASS_REMOVAL`), `ListMandatoryError` (409 `LIST_MANDATORY`), `PrivateListInvalidError` (422 `PRIVATE_LIST_INVALID`), `PrivateListExistsError` (409 `PRIVATE_LIST_EXISTS`); `LIST_FORMAT_INVALID` is the library's.
- **Notification templates** `lists.source_stale`, `lists.lpb_missing`; **audit actions** `lists.subscription_changed`, `private_list.created`, `private_list.version_uploaded`, `private_list.entries_viewed`.
- **Test support** `tests/modules/screening/catalog/support.py` (`seed_sources`, `make_version`, `ingest_fixture`) and the fixture `tests/modules/screening/catalog/fixtures/un_v2.xml` (the library's UN fixture with one entry added, one changed, one removed), which 3c's exit test reuses.
- **Console:** `ACCESS.lists`, `ACCESS.listsAdmin`, `ACCESS.privateLists`; nav keys `nav.lists`, `nav.privateLists`; `console/src/features/lists/`, `console/src/features/private-lists/`.

## Global Constraints

- Every SP-01/SP-02 convention binds: tenant tables with `tenant_id` first in `(tenant_id, id)`, composite FKs, `enable_rls`, one isolation factory each in `tests/isolation/factories.py::register_all`; shared tables in `SHARED_TABLES`; `v1_router()` and `require_access(...)` on every route; `Idempotency-Key` on every POST; `Encrypted*` columns without an `_enc` suffix; `record(session, AuditEvent(...))` last in the transaction; ids-only event payloads through `publish`; problem details with stable codes; UPPERCASE values in `enum_column` (VARCHAR(32)) columns; Google-style docstrings.
- MVP footprint: one container plus Postgres; the local-filesystem object store is the only store; no S3, Redis, Kafka, Temporal, KMS or OpenSearch.
- Settings (`src/xolotl/platform/config.py`, prefix `XOLOTL_`), exactly these (the `SCREENING_*` keys are 3b's and 3c's): `lists_fetch_enabled: bool | None = None` (unset means `env == "prod"`; Deviation 14), `lists_poll_cron: str = "* * * * *"` (UTC, cron-validated with the existing validator), `lists_fetch_timeout_seconds: int = Field(default=120, ge=5, le=600)`, `lists_max_file_mb: int = Field(default=300, ge=1, le=2048)`, `lists_max_removal_ratio: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)`, `private_list_max_mb: int = Field(default=20, ge=1, le=200)`, `private_list_max_rows: int = Field(default=100000, ge=1)`.
- Catalog numbers: lease `LEASE = timedelta(minutes=10)`; backoff `min(poll_interval * 2**consecutive_failures, timedelta(hours=6))`; stale when `last_success_at` is null or older than `2 * poll_interval`; poll intervals UN 15 min, OFAC 30 min, EU and UK 60 min, SAT 24 h (INT-304); the guard refuses `removed / previous entry_count > lists_max_removal_ratio`; private upload caps `private_list_max_mb` and `private_list_max_rows`; at most `MAX_REPORTED_ERRORS = 200` row errors in one response.
- Object keys: shared raw files `lists/<code>/<version_id>.<ext>` under `SHARED_NAMESPACE` (`ext` and content type sniffed from the bytes: `xml`/`application/xml`, `zip`/`application/zip`, else `csv`/`text/csv`); sealed private uploads `private-lists/<list_id>/<version_id>` under the tenant, content type `application/octet-stream`.
- Grants (app role): `list_source` SELECT plus UPDATE on exactly `next_poll_at, lease_until, lease_holder, etag, last_modified, last_attempt_at, last_success_at, last_change_at, last_error, consecutive_failures`; `list_version` SELECT, INSERT, UPDATE (`status`); `list_entry` SELECT, INSERT, UPDATE (`to_seq`); `list_entry_delta` SELECT, INSERT. None of the four is in `SHARED_WRITABLE`. Only the owner role writes `list_source` rows' catalog columns (`xolotl lists seed`).
- `last_error` holds a code and a short reason only (`HTTP_503`, `TIMEOUT`, `TOO_LARGE`, `NETWORK`, `LIST_FORMAT_INVALID: <library detail>`, `LIST_MASS_REMOVAL: removed 412 of 1003`), never a response body or a record.
- Shared ingestion is not tenant activity: no tenant audit event for it; `list_version` (sha256, `loaded_by`) is the provenance record (spec §11).
- No customer-facing string mentions screening, lists or suspicion; console strings are Spanish-first keyed strings in both catalogues.
- Gates at the end of every task: `make ci`; console tasks also `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`.

## Deviations from the sub-spec (decided here)

1. **Catch-up hands versions over by event, not by creating the batch.** Spec §6.4 has the poll create `screening_batch` and enqueue `screening.list_delta` in its transaction, but `screening_batch` is migration 0010 (3b) and the job handler is 3c's; 3a writing them would invert the plan order. The poll publishes `lists.version_published.v1` and advances `last_seq` in one transaction; 3c's consumer `screening` creates the batch and job in the dispatcher's transaction (exactly once per consumer through `event_consumption`), so the effect is the same transactional chain. A subscription's first catch-up (`last_seq = 0`) publishes one event for the active version with `initial: true` and jumps `last_seq` to it (spec §6.3 step 5: a full re-screen, not one delta per historic version); later ones publish one event per version with `initial: false`, in `seq` order. `initial` is the one field added to the spec's event data. Private versions are handed over the same way through `private_list.version_activated.v1`.
2. **`list_version` has no `UNIQUE (list_code, file_sha256)`.** A source that reverts to an earlier file (A, B, A) is a real change against the active version, and the constraint would refuse it forever. The same-file check of §6.2 step 3 compares with the active version only; the migration keeps a plain index `ix_list_version_sha (list_code, file_sha256)`.
3. **`private_list_entry.entry_enc` is the column `entry`** of type `EncryptedJson` (the kernel convention: `staff_credential.totp_secret`, `connector_credential.secret`).
4. **The private-list upload is a raw request body typed by `Content-Type`**, like `POST /v1/parties/{id}/documents`, not multipart (no `python-multipart` dependency): `text/csv` or `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
5. **Template mapping choices.** `source_ref` is `"clave <clave>"` or `""` (never a row number, which would change every entry's content hash when a row is inserted above it); `observaciones` stays in the sealed upload only and is not part of the entry (free text must not reach explanations); `alias` entries get `NameQuality.STRONG`; `otro_identificador` becomes `IdType.OTHER`; `fecha_alta` is `listed_on`; `nacionalidad` is one ISO alpha-2 code or several separated by `|`.
6. **The private delta compares content tags.** `diff()` compares library `content_hash()`es, while a private entry stores only its HMAC `content_tag` (spec §5.2). `private_delta(previous: Mapping[str, bytes], current: Mapping[str, bytes]) -> ListDelta` (library type) compares key-to-tag maps; spec §7 says "diff against the active version's keys and tags".
7. **The catalog contract has one ignored import:** `xolotl.modules.screening.catalog.template -> xolotl.modules.party.identifiers` (the pure RFC/CURP validators, which import only `party.enums` and `platform.clock`), so private-list identifiers are "normalized with the party validators' rules" (spec §7) without the catalog touching party tables or services.
8. **`ingest_file` takes `store: ObjectStore`** as a keyword (the spec signature leaves out where the raw file goes) and reads the ratio from `get_settings()`.
9. **`xolotl serve` seeds the catalog too.** Production runs only `serve` (render.yaml has no pre-deploy command), so both `db upgrade` and `serve` run `seed_list_sources` right after `migrate.upgrade()`, on the owner engine.
10. **Row errors travel in the problem's `errors`.** `xolotl.platform.errors` gains `ErrorItem(loc: tuple[str | int, ...], msg: str, type: str)` and `XolotlError.__init__(..., errors: Sequence[ErrorItem] = ())`; `_xolotl_error` renders them as the existing `Problem.errors` (`ProblemError`). A row error is `loc=("row", 12, "rfc")`, `type="INVALID_RFC"`, `msg` a fixed English sentence per code; never a cell value.
11. **Flat navigation.** The shell's nav has no groups, so "Reglas › Listas" and "Reglas › Listas privadas" are two entries `nav.lists` and `nav.privateLists` after `nav.policy`.
12. **"Once a day" for `lists.lpb_missing`** is "no notification with that template for this tenant since 00:00 America/Mexico_City today" (no column for it).
13. **No policy, no reconcile.** A tenant with no pack assigned (`PolicyNotAssignedError`) keeps its subscriptions untouched and the poll logs `lists poll: no policy`; codes in the policy that the catalog does not hold, other than `MX_LPB`, are skipped with a warning (`MX_LPB` is the LPB check).
14. **`LISTS_FETCH_ENABLED` unset follows `XOLOTL_ENV`** (true only in `prod`); `Settings.lists_fetching` (a property) is what code reads.
15. **Delta pages are keyset on `source_entry_key`** (ascending, opaque base64url cursor of the last key); `paginate` needs a timestamp and id the delta table does not have.
16. **`GET /v1/lists` returns more than the spec lists:** `title`, `jurisdiction`, `last_success_at`, `failing` (`consecutive_failures > 0`) and `last_error`, so the console's freshness badge needs one call. They are public-source operational facts, not tipping-off.
17. **The template download is a static console file** `console/public/plantilla-lista-privada.csv` (header row only); a Python test pins its header to `TEMPLATE_COLUMNS`.
18. **The poll handler's own session stays open during a download.** The runner opens the job's tenant session before calling the handler; the claim and the ingest use short sessions of their own from the session factory, so no lock is held while bytes arrive (spec §6.2 "outside any transaction" holds for every lock-taking statement).

## Review Focus

1. **Flush order against the partial unique indexes.** Superseding the active version and closing `to_seq` on changed rows must be executed (Core `update(...)`) before the new `ACTIVE` version and the new current rows are inserted, or `ux_list_version_active` / `ux_list_entry_current` fail inside the ORM flush. Tests `test_a_second_version_supersedes_the_first_atomically` and `test_a_changed_entry_closes_its_row_and_opens_a_new_one` (D4); `test_a_new_private_version_supersedes_the_previous` (D6).
2. **One fetch across tenants, and a lease that frees itself.** Two tenants' polls at once must make one HTTP request; a job that dies with the lease held must let the next poll after `lease_until` take it. Tests `test_two_tenants_polling_at_once_fetch_once` (two threads, one `respx` route asserted `call_count == 1`) and `test_an_expired_lease_is_taken_over` (D5).
3. **A truncated or broken download never changes the active version.** A body cut in half (`ListFormatError`), an HTTP 200 with an HTML error page, and a file dropping 25 % of entries must each keep the previous version `ACTIVE`, set `last_error`, back off, and publish nothing. Tests `test_a_truncated_file_keeps_the_previous_version`, `test_an_html_error_page_is_a_format_error`, `test_mass_removal_is_refused_and_backs_off` (D5) and `test_the_guard_refuses_above_the_ratio_unless_accepted` (D4).
4. **Private data never at rest in plaintext and never echoed.** A name, RFC or `observaciones` value from an uploaded file must not appear in any table, the object store or a logged line, and a rejected row's error must not carry its cell. Tests `test_no_plaintext_of_an_upload_is_stored` (`leaked(everything_stored(...))` plus the sealed object's bytes) and `test_row_errors_never_echo_cell_values` (D6); `test_an_invalid_upload_answers_row_errors_without_values` (D7).
5. **A tenant's catch-up happens once per version even when two polls overlap.** Two `lists.poll` runs of one tenant at once (a slow one and the next minute's) must publish each version's event once. Test `test_overlapping_polls_publish_each_version_once` (D3), which relies on `SELECT … FOR UPDATE` of the subscription rows.

## File structure

```
pyproject.toml, uv.lock                                   (+ openpyxl)                                                [D6]
.importlinter                                             (+ screening-catalog-is-party-free, one ignored import)      [D1, D6]
.env.example, README.md                                   (+ the seven XOLOTL_LISTS_* / XOLOTL_PRIVATE_LIST_* rows; a "Lists" section) [D1, D5]
src/xolotl/platform/config.py                             (+ the seven settings, lists_fetching)                       [D1]
src/xolotl/platform/errors.py, api/problems.py            (+ ErrorItem, XolotlError.errors)                           [D6]
src/xolotl/platform/storage/port.py, __init__.py          (+ SHARED_NAMESPACE)                                         [D4]
src/xolotl/platform/tenancy/registry.py                   (+ the four shared tables)                                   [D1]
src/xolotl/models.py                                      (+ screening catalog models)                                 [D1]
src/xolotl/migrations/versions/0009_lists.py              pg_trgm, nine tables, grants, RLS                           [D1]
src/xolotl/modules/screening/enums.py                     ListKind, SourceFormat, VersionStatus, DeltaChange, ListRequirement [D1]
src/xolotl/modules/screening/errors.py                    the four catalog errors                                      [D3, D4, D6]
src/xolotl/modules/screening/events.py                    event constants, publish_screening_event                    [D3, D6]
src/xolotl/modules/screening/sources.yaml                 the five shared sources                                      [D2]
src/xolotl/modules/screening/catalog/__init__.py          re-exports                                                   [D1..D6]
src/xolotl/modules/screening/catalog/models.py            the nine models                                              [D1]
src/xolotl/modules/screening/catalog/sources.py           SourceSpec, read_sources, seed_list_sources                  [D2]
src/xolotl/modules/screening/catalog/fetcher.py           ListFetcher, FetchResult, ListFetchError, HttpListFetcher    [D2]
src/xolotl/modules/screening/catalog/subscriptions.py     reconcile_subscriptions, catch_up, check_staleness, remind_lpb_missing [D3]
src/xolotl/modules/screening/catalog/poll.py              ListsPollJob, register_lists, claim_due_source, poll_source [D3, D5]
src/xolotl/modules/screening/catalog/ingest.py            IngestReport, ingest_file, file_kind                         [D4]
src/xolotl/modules/screening/catalog/queries.py           active_versions, version_members, entry_of, delta_page, enabled_subscriptions [D4, D7]
src/xolotl/modules/screening/catalog/template.py          TEMPLATE_COLUMNS, TemplateError, read_template, rows_to_entries [D6]
src/xolotl/modules/screening/catalog/private.py           create_private_list, upload_private_version, private_delta, active_private_versions, private_entry_of, view_entries [D6, D7]
src/xolotl/modules/screening/catalog/service.py           set_subscription_enabled, list_overview                      [D7]
src/xolotl/modules/screening/schemas.py                   lists and private-list request/response models               [D7]
src/xolotl/modules/screening/routes_lists.py              GET /lists…, PATCH /lists/subscriptions/{code}              [D7]
src/xolotl/modules/screening/routes_private_lists.py      /private-lists…                                              [D7]
src/xolotl/modules/tenants/service.py                     (+ lists.poll schedule, literal kind)                        [D3]
src/xolotl/app.py                                         (register_lists in wire_platform; two routers)               [D3, D5, D6, D7]
src/xolotl/cli.py                                         (+ lists_app: seed, status, load-file, show, fetch; seed after upgrade in db upgrade and serve) [D2, D4, D5]
tests/isolation/factories.py                              (+ five tenant factories, _list_source_code)                 [D1]
tests/platform/test_migration_chain.py                    (+ "0009_lists")                                             [D1]
tests/platform/test_config.py, test_errors.py, test_object_store.py, test_worker.py, tests/modules/tenants/test_kernel_events.py, tests/test_cli.py (appended) [D1..D7]
tests/modules/screening/catalog/support.py                seed_sources, make_version, ingest_fixture                   [D2, D3, D4]
tests/modules/screening/catalog/fixtures/un_v2.xml, README.md                                                         [D4]
tests/modules/screening/catalog/fixtures/private/*.csv, *.xlsx (synthetic only)                                        [D6]
tests/modules/screening/catalog/test_migration_0009.py, test_sources.py, test_fetcher.py, test_subscriptions.py, test_poll.py, test_ingest.py, test_template.py, test_private.py, test_lists_routes.py, test_private_list_routes.py [D1..D7]
console/public/plantilla-lista-privada.csv                                                                             [D9]
console/src/api/schema.d.ts (regenerated), api/types.ts, auth/access.ts (+ tests), components/layout/nav.ts, App.tsx, i18n/es-MX.json, en.json, catalogue.test.ts [D8, D9]
console/src/features/lists/…, console/src/features/private-lists/…                                                    [D8, D9]
```

---

### Task D1: Migration 0009, the catalog models and enums, settings, the shared-table registry and the isolation factories

Depends on: nothing in this plan (0008 is on `main`). Console: no. Branch `sp03a-d1-migration-0009`.

**Files:**
- Create: `src/xolotl/migrations/versions/0009_lists.py`, `src/xolotl/modules/screening/enums.py`, `src/xolotl/modules/screening/catalog/__init__.py`, `src/xolotl/modules/screening/catalog/models.py` (and `src/xolotl/modules/screening/__init__.py` if library T1 has not merged)
- Modify: `src/xolotl/platform/tenancy/registry.py`, `src/xolotl/platform/config.py`, `src/xolotl/models.py`, `.importlinter`, `.env.example`, `README.md` (configuration table)
- Test: `tests/modules/screening/__init__.py`, `tests/modules/screening/catalog/__init__.py`, `tests/modules/screening/catalog/test_migration_0009.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_config.py`, `tests/isolation/factories.py`

**Interfaces:**

Consumes: `TenantTableMixin`, `tenant_table_args`, `tenant_fk`, `enable_rls`, `grant_shared` (`xolotl.platform.tenancy.ddl`); `enum_column`; `EncryptedJson`, `Encrypted`; `APP_ROLE`; the `CHAIN` list; the factories' `_pack_version_id` pattern (owner engine for shared parents).

Produces:

```python
# screening/enums.py
class ListKind(StrEnum): SANCTION; FISCAL; BLOCKED; PEP; INTERNAL
class SourceFormat(StrEnum): UN_XML; OFAC_ENHANCED_XML; EU_FSF_XML; UK_SL_XML; SAT_69B_CSV
class VersionStatus(StrEnum): ACTIVE; SUPERSEDED
class DeltaChange(StrEnum): ADDED; CHANGED; REMOVED
class ListRequirement(StrEnum): MANDATORY; RECOMMENDED

# catalog/models.py (shared: plain Base, no mixin; tenant: TenantTableMixin)
class ListSource(Base): code (Text PK), list_kind, title, jurisdiction, format, url, poll_interval (Interval),
    enabled, next_poll_at, lease_until, lease_holder, etag, last_modified, last_attempt_at, last_success_at,
    last_change_at, last_error, consecutive_failures
class ListVersion(Base): id, list_code (FK), seq, status, published_at, source_version, parser_version, fetched_at,
    loaded_by, file_sha256, file_size (BigInteger), storage_key, entry_count, added, changed, removed, previous_version_id
class ListEntryRow(Base): __tablename__ = "list_entry"; id, list_code, source_entry_key, content_hash, entity_type (String(32), str),
    primary_name, screenable, entry (JSONB, dict[str, JsonValue]), from_seq, to_seq
class ListEntryDelta(Base): version_id, source_entry_key (composite PK), change, prev_entry_id, new_entry_id
class TenantListSubscription(TenantTableMixin, Base): list_code, requirement, enabled, last_seq, stale_notified_at, changed_by, updated_at
class PrivateList(TenantTableMixin, Base): list_code, list_kind, title, created_by, created_at
class PrivateListVersion(TenantTableMixin, Base): list_id, seq, status, uploaded_by, uploaded_at, file_sha256, file_size (Integer),
    content_type, storage_key, entry_count, added, changed, removed
class PrivateListEntryRow(TenantTableMixin, Base): __tablename__ = "private_list_entry"; list_id, source_entry_key, content_tag,
    entity_type (String(32)), entry (EncryptedJson), from_seq, to_seq
class PrivateListEntryDelta(Base): tenant_id, version_id, source_entry_key (composite PK), change, prev_entry_id, new_entry_id
```

`entity_type` is stored as text with the library's `EntityType` values; D1 does not import the library. Vocabulary columns are `enum_column(...)`.

Migration `0009_lists.py`: header like `0006_policy_units_calendar.py` (`revision = "0009_lists"`, `down_revision = "0008_party"`). `upgrade()`: `op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")`; the four shared tables exactly as spec §5.1 (Deviation 2: `ix_list_version_sha` instead of the sha unique; keep `UNIQUE (list_code, seq)`, `ux_list_version_active` with `postgresql_where=status = 'ACTIVE'`, `UNIQUE (list_code, source_entry_key, from_seq)`, `ux_list_entry_current` with `WHERE to_seq IS NULL`, the delta `CHECK` named `ck_list_entry_delta_change`), each followed by `grant_shared(table)` and the extra grants of Global Constraints (`GRANT UPDATE (next_poll_at, …) ON list_source TO xolotl_app` and so on); the five tenant tables exactly as spec §5.2's 0009 block (Deviation 3 for `entry`), constraint names in the kernel's `pk_`/`fk_`/`uq_`/`ck_` style, the same delta `CHECK` on `private_list_entry_delta`, then `enable_rls(table)` for each. `downgrade()` drops the nine tables in reverse dependency order and leaves the extension (other databases' objects may use it).

Registry: `SHARED_TABLES` gains `list_source`, `list_version`, `list_entry`, `list_entry_delta`; `SHARED_WRITABLE` unchanged, with a comment that their migration grants column-level rights.

`.importlinter`:

```ini
[importlinter:contract:screening-catalog-is-party-free]
name = the list catalog never imports the party module
type = forbidden
source_modules =
    xolotl.modules.screening.catalog
forbidden_modules =
    xolotl.modules.party
```

(D6 adds its one `ignore_imports` line.)

- [ ] **Step 1: Write the failing tests.**

`tests/modules/screening/catalog/test_migration_0009.py` (the `go_down`/`database` pattern of `tests/platform/test_migration_0007.py`):

```python
REVISION, BEFORE = "0009_lists", "0008_party"
SHARED = ("list_source", "list_version", "list_entry", "list_entry_delta")
TENANT = ("tenant_list_subscription", "private_list", "private_list_version", "private_list_entry", "private_list_entry_delta")

def test_0009_follows_0008(): ...                                   # down_revision == BEFORE
def test_downgrade_drops_the_nine_tables_and_upgrade_recreates_them(database): ...
def test_tenant_tables_force_rls_and_shared_ones_have_none(database): ...  # pg_class relrowsecurity/relforcerowsecurity
def test_pg_trgm_is_installed(database): ...                        # SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'
def test_the_app_role_may_update_only_the_operational_columns_of_list_source(database):
    # has_column_privilege('xolotl_app', 'list_source', col, 'UPDATE') True for the ten columns, False for url, title, list_kind, format, poll_interval, enabled
def test_the_app_role_may_update_only_status_and_to_seq(database): ...      # list_version.status, list_entry.to_seq; delta tables have no UPDATE
def test_only_one_active_version_and_one_current_entry_per_key(admin_engine): ...  # IntegrityError on the second insert of each
def test_the_delta_check_refuses_an_added_row_with_a_previous_entry(admin_engine): ...
def test_private_list_codes_and_kinds_are_checked(admin_engine):
    # ('MX_LPB','BLOCKED') ok, ('MX_LPB','SANCTION') refused, ('INTERNAL_VIP','PEP') ok, ('INTERNAL_VIP','BLOCKED') refused, ('OTHER','PEP') refused
def test_a_reverted_file_may_be_stored_again(admin_engine): ...     # two versions with one sha256 insert (Deviation 2)
```

`tests/platform/test_config.py`: `test_list_settings_default_to_the_spec_values` (`lists_poll_cron == "* * * * *"`, `lists_fetch_timeout_seconds == 120`, `lists_max_file_mb == 300`, `lists_max_removal_ratio == Decimal("0.20")`, `private_list_max_mb == 20`, `private_list_max_rows == 100000`, `lists_fetch_enabled is None`); `test_list_fetching_follows_the_environment_unless_set` (`dev` → False, `prod` → True, `XOLOTL_LISTS_FETCH_ENABLED=true` in `dev` → True, `false` in `prod` → False); `test_the_poll_cron_is_validated` (`"every minute"` → `ValidationError` matching `lists_poll_cron`); add the seven names to `test_env_example_is_a_valid_configuration`'s set. `CHAIN` gains `"0009_lists"`. Factories: `tenant_list_subscription` (code from `_list_source_code()`, which inserts `list_source('UN_CONSOLIDATED', …)` through the owner engine with `ON CONFLICT DO NOTHING`), `private_list` (`MX_LPB`/`BLOCKED`), `private_list_version`, `private_list_entry` (keys created, `entry=encrypt_json({"k": "v"}, ...)`, `content_tag=b"\x00" * 32`), `private_list_entry_delta` (an `ADDED` row pointing at the entry), registered in `register_all`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_migration_0009.py tests/platform/test_migration_chain.py tests/platform/test_config.py tests/isolation --no-cov -q` → FAIL (revision missing, settings missing, models missing at collection).
- [ ] **Step 3: Implement** the enums, models, migration, registry, settings (with `lists_fetching` property and comments in the style of the surrounding fields), `.env.example` lines (commented `# XOLOTL_LISTS_FETCH_ENABLED=` and the six defaults), README rows, `src/xolotl/models.py` import (`from xolotl.modules.screening.catalog import models as screening_catalog_models`, sorted `__all__`), the contract, the factories.
- [ ] **Step 4: Run to verify pass.** The same tests plus `tests/platform/test_schema_sync.py tests/platform/test_alembic_env.py tests/isolation`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(screening): migration 0009 with the list catalog and private-list tables, models and settings`.

---

### Task D2: The source catalog and its seed, the fetcher port and HTTP adapter, `lists seed` and `lists status`

Depends on: D1. Console: no. Branch `sp03a-d2-sources-fetcher`.

**Files:**
- Create: `src/xolotl/modules/screening/sources.yaml`, `src/xolotl/modules/screening/catalog/sources.py`, `src/xolotl/modules/screening/catalog/fetcher.py`, `tests/modules/screening/catalog/support.py`
- Modify: `src/xolotl/modules/screening/catalog/__init__.py`, `src/xolotl/cli.py` (`lists_app`; `db upgrade` and `serve` seed after migrating)
- Test: `tests/modules/screening/catalog/test_sources.py`, `tests/modules/screening/catalog/test_fetcher.py`, `tests/test_cli.py`

**Interfaces:**

Consumes: D1 models and enums; `get_admin_engine`, `tenant_session`, `TenantContext.platform`; `httpx`, `respx`; `get_settings()`.

Produces:

```python
# catalog/sources.py
SOURCES_FILE: Path = Path(__file__).parent.parent / "sources.yaml"
class SourceSpec(BaseModel):          # strict, frozen, extra="forbid"
    code: str; list_kind: ListKind; title: str; jurisdiction: str; format: SourceFormat
    url: HttpUrl; poll_interval: timedelta; url_checked_on: date
@dataclass(frozen=True, slots=True)
class SeedReport: inserted: int; updated: int; unchanged: int; disabled: int
def read_sources(path: Path = SOURCES_FILE) -> list[SourceSpec]          # ValidationFailedError on a bad file or duplicate code
def seed_list_sources(session: Session, specs: Sequence[SourceSpec]) -> SeedReport
    # owner session; upserts the catalog columns by code; never touches the operational columns;
    # a code no longer in the file becomes enabled = false (rows are referenced, never deleted)

# catalog/fetcher.py
@dataclass(frozen=True, slots=True)
class FetchResult: not_modified: bool; body: bytes | None; sha256: bytes | None; etag: str | None; last_modified: str | None
class ListFetchError(Exception):       # code: str (HTTP_<status>, TIMEOUT, TOO_LARGE, NETWORK); str() is "<code>: <short reason>"
class ListFetcher(Protocol):
    def fetch(self, url: str, *, etag: str | None, last_modified: str | None) -> FetchResult: ...
class HttpListFetcher:                 # implements ListFetcher
    def __init__(self, *, timeout_seconds: int, max_bytes: int, client: httpx.Client | None = None) -> None
```

`sources.yaml` (one record per code; `poll_interval` as ISO-8601 durations `PT15M`, `PT30M`, `PT1H`, `PT1H`, `P1D`): `UN_CONSOLIDATED` SANCTION UN `UN_XML`; `OFAC_SDN` SANCTION US `OFAC_ENHANCED_XML`; `EU_FSF` SANCTION EU `EU_FSF_XML`; `UK_FCDO` SANCTION GB `UK_SL_XML`; `MX_SAT_69B` FISCAL MX `SAT_69B_CSV`. Each `url` is the one the library's fixture README for that source records; for a source whose library task has not merged, the implementer checks the official publication page on the day, writes the URL and sets `url_checked_on` to that date (the URL list is never invented from memory).

`HttpListFetcher.fetch`: `GET` with `If-None-Match`/`If-Modified-Since` when given, `follow_redirects=True`, `timeout`, streamed; a `Content-Length` above `max_bytes` or more bytes than that read → `ListFetchError("TOO_LARGE")`; 304 → `FetchResult(not_modified=True, body=None, sha256=None, …)` keeping the given validators; 2xx → body, `hashlib.sha256(body).digest()`, the response's `ETag` and `Last-Modified`; other statuses → `HTTP_<status>`; `httpx.TimeoutException` → `TIMEOUT`; other `httpx.HTTPError` → `NETWORK`.

CLI: `lists_app = typer.Typer(no_args_is_help=True, help="Shared sanctions lists")`, `app.add_typer(lists_app, name="lists")`. `lists seed [path]` (owner engine, platform context) prints `list sources: 5 inserted, 0 updated, 0 unchanged, 0 disabled`. `db_upgrade` and `serve` call the same seed after `migrate.upgrade()` (Deviation 9) and `db upgrade` prints the line. `lists status` prints one line per source: `UN_CONSOLIDATED  enabled  v3  1003 entries  last success 2026-10-19T12:00Z  next 12:15Z  ok` (`stale`, or `failing: HTTP_503 (2)`), with `no version` when none.

- [ ] **Step 1: Write the failing tests.** `test_sources.py`: `test_the_shipped_catalog_has_the_five_sources_with_the_spec_intervals` (codes, kinds, formats, `{UN: 15 min, OFAC: 30 min, EU: 60 min, UK: 60 min, SAT: 24 h}`); `test_a_duplicate_code_or_unknown_field_is_refused`; `test_seeding_twice_is_idempotent` (`SeedReport(5,0,0,0)` then `(0,0,5,0)`); `test_seeding_updates_the_url_and_keeps_the_operational_columns` (set `etag`, `last_success_at`, `consecutive_failures` with the app role, change the URL in a temp YAML, reseed: URL changed, the three kept, `updated == 1`); `test_a_removed_code_is_disabled_not_deleted`; `test_the_app_role_cannot_seed` (app-role session → `ProgrammingError` permission denied on `url`). `test_fetcher.py` (`respx`): `test_a_200_returns_the_body_its_hash_and_validators`; `test_conditional_headers_are_sent_and_304_is_not_modified`; `test_redirects_are_followed`; `test_a_body_over_the_cap_is_too_large` (with and without `Content-Length`); `test_error_statuses_timeouts_and_network_errors_have_codes` (parametrised 403, 500, 503, `httpx.ReadTimeout`, `httpx.ConnectError` → `HTTP_403`, `HTTP_500`, `HTTP_503`, `TIMEOUT`, `NETWORK`); `test_an_error_message_never_carries_the_body`. `tests/test_cli.py`: `test_lists_seed_reports_counts_and_is_idempotent`, `test_db_upgrade_seeds_the_catalog`, `test_lists_status_lists_every_source`. `support.py` gets `seed_sources(admin_engine) -> None` (reads the shipped YAML, seeds through the owner engine).
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_sources.py tests/modules/screening/catalog/test_fetcher.py tests/test_cli.py -k "lists or upgrade" --no-cov -q` → FAIL (imports).
- [ ] **Step 3: Implement** the YAML, the two modules, the CLI group and the seed calls.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(screening): shared list sources catalog with lists seed and status, and the HTTP list fetcher`.

---

### Task D3: The `lists.poll` job's tenant half: subscriptions from the effective policy, catch-up events, stale and missing-LPB notifications, the schedule

Depends on: D1, D2 (`support.seed_sources`). Console: no. Branch `sp03a-d3-poll-subscriptions`.

**Files:**
- Create: `src/xolotl/modules/screening/catalog/subscriptions.py`, `src/xolotl/modules/screening/catalog/poll.py`, `src/xolotl/modules/screening/events.py`
- Modify: `src/xolotl/modules/screening/catalog/__init__.py`, `src/xolotl/modules/tenants/service.py` (`schedule_platform_jobs`), `src/xolotl/app.py` (`wire_platform`), `tests/modules/screening/catalog/support.py` (+ `make_version`)
- Test: `tests/modules/screening/catalog/test_subscriptions.py`, `tests/modules/screening/catalog/test_poll.py`, `tests/platform/test_worker.py` (`PLATFORM_SCHEDULES`), `tests/modules/tenants/test_kernel_events.py`

**Interfaces:**

Consumes: D1 models; `effective_policy`, `PolicyNotAssignedError` (`xolotl.platform.policy`); `publish`, `Event` (`xolotl.platform.events`); `Notifier`, `InAppNotifier`, `Notification`; `register_job_handler`, `Job`; `clock.now`; `MEXICO_CITY`; `Role`; `install_both_packs` and the assignment helpers of `tests/platform/policy_support.py` / `tests/modules/tenants/policy_support.py`.

Produces:

```python
# screening/events.py
EVENT_SOURCE = "xolotl.screening"
LISTS_VERSION_PUBLISHED = "lists.version_published.v1"
PRIVATE_LIST_VERSION_ACTIVATED = "private_list.version_activated.v1"     # published by D6
def publish_screening_event(s: Session, event_type: str, *, entity: str, entity_id: UUID,
                            key: str, data: dict[str, JsonValue]) -> Event   # as publish_party_event, with an explicit ordering key

# catalog/subscriptions.py
STALE_TEMPLATE = "lists.source_stale"; LPB_MISSING_TEMPLATE = "lists.lpb_missing"
@dataclass(frozen=True, slots=True)
class ReconcileReport: added: tuple[str, ...]; changed: tuple[str, ...]; removed: tuple[str, ...]
def reconcile_subscriptions(s: Session, *, today: date) -> ReconcileReport | None    # None when no policy (Deviation 13)
def catch_up(s: Session) -> list[Event]                                           # Deviation 1
def is_stale(source: ListSource, *, now: datetime) -> bool
def check_staleness(s: Session, notifier: Notifier, *, now: datetime) -> list[str]  # codes notified this run
def remind_lpb_missing(s: Session, notifier: Notifier, *, now: datetime) -> bool

# catalog/poll.py
LISTS_POLL_KIND = "lists.poll"
class ListsPollJob:
    def __init__(self, *, session_factory: SessionFactory, notifier: Notifier,
                 clock_fn: Callable[[], datetime] = clock.now) -> None   # D5 adds fetcher, store, settings
    def run(self, session: Session, job: Job) -> None                   # D5 adds the shared half first
def register_lists(*, session_factory: SessionFactory, notifier: Notifier) -> ListsPollJob
```

Rules: `reconcile_subscriptions` reads `effective_policy(s, on=today, product=None).screening` (today in Mexico City, computed by the caller); every catalog code in `mandatory_lists` (by `.value`) gets `requirement = MANDATORY, enabled = true`; every catalog code in `recommended_lists` and not mandatory gets `RECOMMENDED`, enabled on creation and otherwise untouched; rows for other codes are deleted; `changed_by = "system"`, `updated_at = now`; codes outside the catalog are skipped (`MX_LPB` silently, others with a `logger.warning`). `catch_up` locks the tenant's enabled subscriptions `FOR UPDATE`, reads `list_version` rows with `seq > last_seq` per code, and publishes per Deviation 1 (entity `list_version`, key = list code, data `{list_code, version_id, seq, added, changed, removed, initial}` with the version's counts), then sets `last_seq`. `is_stale`: `last_success_at is None or now - last_success_at > 2 * poll_interval`. `check_staleness`: for each enabled subscription whose source is stale and `stale_notified_at is None`, notify `ADMIN` and `MLRO` (`data={"list_code": code, "last_success_at": iso or None}`) and set `stale_notified_at = now`; a fresh source clears `stale_notified_at`. `remind_lpb_missing`: when `MX_LPB` is in `mandatory_lists`, the tenant has no `ACTIVE` `MX_LPB` private version, and no notification with `LPB_MISSING_TEMPLATE` exists since today 00:00 Mexico City (Deviation 12), notify `ADMIN` and `MLRO` with `data={}`. `ListsPollJob.run` (tenant half): `reconcile_subscriptions`, `catch_up`, `check_staleness`, `remind_lpb_missing`, in the job's session. Schedule: `LISTS_POLL_SCHEDULE = "lists.poll"` literal in `modules/tenants/service.py` (it never imports screening), `queue.schedule(s, name=..., cron=get_settings().lists_poll_cron, tz="UTC", kind=..., payload={})`. `wire_platform` calls `_ = register_lists(session_factory=session_factory, notifier=InAppNotifier())`.

- [ ] **Step 1: Write the failing tests.** `support.make_version(admin_engine, code, seq, *, added=1, changed=0, removed=0) -> UUID` inserts a `list_version` (and supersedes the previous) directly, no parser. `test_subscriptions.py` (a tenant with the ITF pack assigned; `seed_sources`):

```python
def test_mandatory_and_recommended_lists_become_subscriptions(tenant): ...
    # UN_CONSOLIDATED MANDATORY enabled; OFAC_SDN, MX_SAT_69B, EU_FSF, UK_FCDO RECOMMENDED enabled; no MX_LPB row
def test_a_disabled_recommended_list_stays_disabled_on_reconcile(tenant): ...
def test_a_list_leaving_the_policy_is_removed(tenant): ...                 # reassign to the AVI pack: EU_FSF and UK_FCDO rows gone
def test_without_a_policy_subscriptions_are_left_alone(tenant): ...        # None, rows unchanged
def test_the_first_catch_up_is_one_initial_event_for_the_active_version(tenant): ...
    # versions 1..3 of UN exist: one event, seq 3, initial True; last_seq 3
def test_later_versions_are_handed_over_one_event_each_in_order(tenant): ...    # seq 4 and 5 → two events, initial False, in order
def test_a_disabled_subscription_is_not_caught_up(tenant): ...
def test_overlapping_polls_publish_each_version_once(tenant): ...          # two threads, two sessions: one event per version (Review Focus 5)
def test_a_stale_source_notifies_admin_and_mlro_once_per_episode(tenant): ...   # two runs → 2 rows; fresh run clears; stale again → 2 more
def test_a_fresh_source_is_not_stale_at_twice_its_interval(): ...         # boundary: exactly 2 × interval is fresh, one second more is stale
def test_a_missing_lpb_is_reminded_once_a_day(tenant): ...                # two runs same day → 2 rows (ADMIN, MLRO); next Mexico City day → 2 more
def test_another_tenant_is_never_caught_up_by_this_one(tenant): ...       # events carry only the session tenant
```

`test_poll.py`: `test_the_poll_job_runs_the_tenant_half_through_the_runner` (enqueue `lists.poll`, `make_runner(kinds=[LISTS_POLL_KIND]).run_once()`, job `DONE`, subscriptions and the event exist). Pinned schedules: `PLATFORM_SCHEDULES` gains `("lists.poll", "* * * * *", "UTC", "lists.poll", True)` first (name order), `test_kernel_events.py`'s expected list and names likewise; `test_the_platform_is_wired_with_its_job_handlers` asserts `job_handler(LISTS_POLL_KIND).__self__` is a `ListsPollJob`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_subscriptions.py tests/modules/screening/catalog/test_poll.py tests/platform/test_worker.py tests/modules/tenants/test_kernel_events.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the three modules, the schedule and the composition.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(screening): lists.poll subscriptions from the effective policy, catch-up events, stale and missing-LPB notifications`.

---

### Task D4: Ingestion: `ingest_file`, the mass-removal guard, raw files under the shared namespace, `lists load-file` and `lists show`

Depends on: D1, D2; **LIB-lists T1**. Console: no. Branch `sp03a-d4-ingest`.

**Files:**
- Create: `src/xolotl/modules/screening/catalog/ingest.py`, `src/xolotl/modules/screening/catalog/queries.py`, `src/xolotl/modules/screening/errors.py`, `tests/modules/screening/catalog/fixtures/un_v2.xml`, `tests/modules/screening/catalog/fixtures/README.md`
- Modify: `src/xolotl/platform/storage/port.py` and `__init__.py` (`SHARED_NAMESPACE`), `src/xolotl/modules/screening/catalog/__init__.py`, `src/xolotl/cli.py`, `tests/modules/screening/catalog/support.py` (+ `ingest_fixture`)
- Test: `tests/modules/screening/catalog/test_ingest.py`, `tests/platform/test_object_store.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`

**Interfaces:**

Consumes: library `parse`, `ParsedList`, `ListEntry` (`entry_key`, `entity_type`, `primary_name.full`, `screenable`, `canonical_json`, `content_hash`, `from_json`), `diff`, `ListDelta`, `ListFormatError`, `ListCode`; D1 models; `ObjectStore`, `ObjectRef`, `LocalFsObjectStore`; `get_settings()`.

Produces:

```python
# platform/storage/port.py
SHARED_NAMESPACE: UUID = UUID(int=0)     # the namespace of objects no tenant owns (spec Decision 4)

# screening/errors.py
class ListMassRemovalError(DomainError): default_code = "LIST_MASS_REMOVAL"   # 409
class ListMandatoryError(DomainError): default_code = "LIST_MANDATORY"        # 409 (raised in D7)

# catalog/ingest.py
@dataclass(frozen=True, slots=True)
class IngestReport:
    list_code: str; version_id: UUID | None; seq: int | None      # None when the file equals the active version's
    entry_count: int; added: int; changed: int; removed: int; unchanged: int; parser_version: str; same_file: bool
def file_kind(data: bytes) -> tuple[str, str]                      # (ext, content type), Global Constraints
def ingest_file(session: Session, code: str, data: bytes, *, store: ObjectStore, loaded_by: str,
                fetched_at: datetime, accept_drop: bool = False) -> IngestReport
def record_success(session: Session, source: ListSource, *, now: datetime, changed: bool) -> None
    # last_attempt_at, last_success_at = now; last_error None; consecutive_failures 0; next_poll_at = now + poll_interval;
    # last_change_at = now when changed

# catalog/queries.py
def active_versions(session: Session) -> dict[str, ListVersion]
def current_hashes(session: Session, list_code: str) -> dict[str, bytes]          # key -> content_hash of rows with to_seq IS NULL
def version_members(session: Session, list_code: str, seq: int) -> Select[tuple[ListEntryRow]]
def entry_of(row: ListEntryRow) -> ListEntry                                       # ListEntry.from_json(row.entry)
```

`ingest_file` steps (spec §6.3): an unknown or disabled code → `NotFoundError`; sha256 of `data` equal to the active version's → `record_success(changed=False)`, `IngestReport(same_file=True, version_id=None, …)`; `parsed = parse(code, BytesIO(data))`; `delta = diff(current_hashes(...), parsed.entries)`; guard: previous `entry_count > 0` and `Decimal(len(delta.removed)) / entry_count > lists_max_removal_ratio` and not `accept_drop` → `ListMassRemovalError(f"removed {r} of {n}")`; `version_id = uuid7()`; `store.put(SHARED_NAMESPACE, f"lists/{code}/{version_id}.{ext}", data, content_type=…)`; then, in order (Review Focus 1): Core update of the previous version to `SUPERSEDED`; Core update `to_seq = seq` on the current rows of changed and removed keys; insert the `list_version` (`seq = previous + 1` or 1, `ACTIVE`, `published_at`, `source_version`, `parser_version` from `parsed`, counts, `previous_version_id`); bulk insert `list_entry` rows for added and changed entries (`from_seq = seq`, `entry = json.loads(e.canonical_json())`, `content_hash = e.content_hash()`); bulk insert `list_entry_delta` rows; `record_success(changed=True)`. No commit: the caller's transaction. A failure after the `put` leaves an orphan object (logged by the caller, never referenced).

CLI: `lists load-file <code> <path> [--accept-drop]` (platform-context app session, the object store from `build_object_store(settings)`, `loaded_by = f"cli:{actor}"`; with `--accept-drop` it logs a WARNING naming the operator and the counts) prints `UN_CONSOLIDATED v2: 1004 entries (1 added, 1 changed, 1 removed, 1001 unchanged) [un-xml/1]` or `UN_CONSOLIDATED: unchanged (same file as v2)`; `ListFormatError` and `ListMassRemovalError` end as `error: <CODE>: <detail>` exit 1. `lists show <code> [--version N]`: the active (or numbered) version's seq, status, published, fetched, `loaded_by`, parser, first 12 hex of the sha256, entry count and delta counts.

`un_v2.xml`: the library's trimmed UN fixture with one individual added (synthetic name, new `DATAID`), one entry's alias changed and one entry removed; `README.md` says so and names the source fixture.

- [ ] **Step 1: Write the failing tests.** `test_object_store.py`: `test_the_shared_namespace_round_trips_on_the_local_store`. `test_errors.py`: rows `(ListMassRemovalError, 409, "LIST_MASS_REMOVAL")`, `(ListMandatoryError, 409, "LIST_MANDATORY")`. `test_ingest.py` (owner-free: app-role platform session; `seed_sources`; the library's UN fixture as v1):

```python
def test_the_first_version_is_all_added_and_active(...): ...      # seq 1, added == entry_count, every delta ADDED, one object at lists/UN_CONSOLIDATED/<id>.xml
def test_the_same_file_again_makes_no_version_and_refreshes_freshness(...): ...
def test_a_second_version_supersedes_the_first_atomically(...): ...           # Review Focus 1
def test_a_changed_entry_closes_its_row_and_opens_a_new_one(...): ...         # un_v2: old row to_seq 2, new row from_seq 2, delta CHANGED with both ids
def test_a_removed_entry_is_closed_and_an_unchanged_one_keeps_its_row(...): ...
def test_version_members_are_the_entries_of_that_version(...): ...          # members(1) == v1 keys, members(2) == v2 keys
def test_entries_round_trip_through_the_stored_json(...): ...               # entry_of(row) == the parsed ListEntry
def test_a_format_error_writes_nothing(...): ...                            # truncated XML: no version, no object, no row
def test_the_guard_refuses_above_the_ratio_unless_accepted(...): ...         # a v2 keeping 70 % → LIST_MASS_REMOVAL; same with accept_drop → v2; exactly 20 % → accepted
def test_an_unknown_code_is_not_found(...): ...
def test_a_failed_transaction_leaves_the_active_version(...): ...           # force an IntegrityError after put: v1 still ACTIVE
```

`tests/test_cli.py`: `test_lists_load_file_ingests_then_reports_unchanged`, `test_lists_load_file_refuses_a_mass_removal_without_the_flag`, `test_lists_show_prints_the_active_version`. `support.ingest_fixture(code, path, *, store) -> IngestReport` wraps `ingest_file` in a platform session.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_ingest.py tests/platform/test_object_store.py tests/platform/test_errors.py tests/test_cli.py -k "lists or shared or MASS or MANDATORY" --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(screening): versioned list ingestion with deltas, the mass-removal guard, lists load-file and lists show`.

---

### Task D5: The poll job's shared half: lease, conditional fetch, ingest, backoff, and `lists fetch`

Depends on: D2, D3, D4; **LIB-lists T1**. Console: no. Branch `sp03a-d5-poll-fetch`.

**Files:**
- Modify: `src/xolotl/modules/screening/catalog/poll.py`, `src/xolotl/modules/screening/catalog/__init__.py`, `src/xolotl/app.py`, `src/xolotl/cli.py`, `README.md` (a "Lists" section: seed, load-file for dev and CI, fetching in prod, staleness)
- Test: `tests/modules/screening/catalog/test_poll.py`, `tests/test_cli.py`, `tests/platform/test_worker.py` (wiring assertion)

**Interfaces:**

Consumes: `HttpListFetcher`, `ListFetcher`, `ListFetchError`, `FetchResult` (D2); `ingest_file`, `record_success` (D4); `ListFormatError`; `ListMassRemovalError`; `Settings.lists_fetching`.

Produces:

```python
LEASE = timedelta(minutes=10)
MAX_BACKOFF = timedelta(hours=6)
def claim_due_source(session: Session, *, holder: str, now: datetime) -> ListSource | None
    # the spec §6.2 step 1 statement (UPDATE … WHERE code = (SELECT … FOR UPDATE SKIP LOCKED) RETURNING *)
def claim_source(session: Session, code: str, *, holder: str, now: datetime) -> ListSource | None   # lists fetch: ignores next_poll_at
@dataclass(frozen=True, slots=True)
class PollOutcome: code: str; result: Literal["NOT_MODIFIED", "SAME_FILE", "INGESTED", "FAILED"]; report: IngestReport | None; error: str | None
def poll_source(session_factory: SessionFactory, source_code: str, *, holder: str, fetcher: ListFetcher,
                store: ObjectStore, now: datetime) -> PollOutcome
    # fetch outside any session; then one short session: not-modified → record_success(changed=False) with the validators kept;
    # else ingest_file(loaded_by=holder) and store the new etag/last_modified; any ListFetchError, ListFormatError or
    # ListMassRemovalError → a fresh session records last_attempt_at, last_error (str of the error, ≤ 200 chars),
    # consecutive_failures + 1, next_poll_at = now + min(poll_interval * 2**failures, MAX_BACKOFF); every path releases the lease
class ListsPollJob:
    def __init__(self, *, session_factory: SessionFactory, notifier: Notifier, fetcher: ListFetcher, store: ObjectStore,
                 fetching: bool, clock_fn: Callable[[], datetime] = clock.now) -> None
def register_lists(*, session_factory: SessionFactory, notifier: Notifier, fetcher: ListFetcher, store: ObjectStore, fetching: bool) -> ListsPollJob
```

`run`: when `fetching`, claim at most one due source in a short session of its own (`holder = bound_context(session).actor_id`, i.e. `job:<id>`), commit, `poll_source`, log `lists poll` with code and result at INFO (WARNING for `FAILED`); then the D3 tenant half in the job's session (so a version ingested this run is caught up this run). With `fetching` false the claim is skipped entirely. `wire_platform`: `register_lists(session_factory=…, notifier=InAppNotifier(), fetcher=HttpListFetcher(timeout_seconds=settings.lists_fetch_timeout_seconds, max_bytes=settings.lists_max_file_mb * 1024 * 1024), store=store, fetching=settings.lists_fetching)`. CLI `lists fetch <code> [--force-network]`: refused with exit 2 `error: network fetching is off (XOLOTL_LISTS_FETCH_ENABLED); pass --force-network` when `not settings.lists_fetching and not force_network`; a lease held by someone else → exit 1 `error: <code> is being fetched by <holder>`; prints the outcome (`UN_CONSOLIDATED: not modified`, `…: ingested v4 (2 added, 0 changed, 0 removed)`, `…: failed HTTP_503: …` exit 1).

- [ ] **Step 1: Write the failing tests** in `test_poll.py` (`respx`, a `FakeClock`, the local store in `tmp_path`, `seed_sources`, two tenants with the ITF pack):

```python
def test_a_due_source_is_fetched_ingested_and_caught_up_in_one_run(...): ...   # UN fixture served: v1, the tenant's initial event published in the same run
def test_not_modified_refreshes_freshness_and_keeps_the_version(...): ...      # 304: last_success_at moves, next_poll_at = now + 15 min, no version
def test_a_body_equal_to_the_active_file_is_same_file(...): ...
def test_two_tenants_polling_at_once_fetch_once(...): ...                     # Review Focus 2
def test_an_expired_lease_is_taken_over(...): ...                             # lease_until in the past, held by job:x → claimed
def test_a_leased_or_not_due_source_is_skipped(...): ...
def test_a_truncated_file_keeps_the_previous_version(...): ...               # Review Focus 3
def test_an_html_error_page_is_a_format_error(...): ...                       # 200 text/html → LIST_FORMAT_INVALID in last_error
def test_mass_removal_is_refused_and_backs_off(...): ...                      # last_error starts LIST_MASS_REMOVAL; no event
def test_failures_back_off_exponentially_up_to_six_hours(...): ...           # failures 1,2,3 → 30, 60, 120 min for UN; 10 failures → 6 h
def test_a_success_after_failures_resets_the_count(...): ...
def test_fetching_off_never_touches_the_network(...): ...                     # respx route uncalled; tenant half still runs
def test_last_error_never_holds_a_response_body(...): ...                     # 500 with a secret-looking body: not in list_source
```

`tests/test_cli.py`: `test_lists_fetch_is_refused_without_force_network_outside_prod`, `test_lists_fetch_with_force_network_ingests` (`respx`), `test_lists_fetch_reports_a_busy_lease`. `test_worker.py`: the wired `ListsPollJob` has `fetching is False` under `XOLOTL_ENV=test`.
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_poll.py tests/test_cli.py -k "lists" --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(screening): leased conditional fetch of shared lists with backoff in lists.poll, and lists fetch`.

---

### Task D6: Private lists: template reading and validation, keyed tags, encryption, versions with deltas, audit and the activation event

Depends on: D1, D3 (`events.py`), D4 (`errors.py`); **LIB-lists T1** (types only); **PLAN-3b-staff-only**. Console: no. Branch `sp03a-d6-private-lists`.

**Files:**
- Create: `src/xolotl/modules/screening/catalog/template.py`, `src/xolotl/modules/screening/catalog/private.py`, `tests/modules/screening/catalog/fixtures/private/` (`lpb_v1.csv`, `lpb_v2.csv`, `lpb_semicolon.csv`, `lpb_v1.xlsx`, `bad_rows.csv`; synthetic names only)
- Modify: `pyproject.toml`/`uv.lock` (`uv add openpyxl`), `.importlinter` (Deviation 7), `src/xolotl/platform/errors.py`, `src/xolotl/platform/api/problems.py` (Deviation 10), `src/xolotl/modules/screening/errors.py`, `src/xolotl/modules/screening/catalog/__init__.py`, `src/xolotl/app.py` (`register_staff_only(PRIVATE_LIST_VERSION_ACTIVATED)` in `register_lists`)
- Test: `tests/modules/screening/catalog/test_template.py`, `tests/modules/screening/catalog/test_private.py`, `tests/platform/test_errors.py`, `tests/platform/test_api_conventions.py` (or the problems test that renders `errors`)

**Interfaces:**

Consumes: library `ListEntry`, `EntryName`, `EntryIdentifier`, `PartialDate`, `NameQuality`, `IdType`, `EntityType`, `ListDelta`; `xolotl.modules.party.identifiers.validate`, `NormalizedIdentifier`, `IdentifierError` and `xolotl.modules.party.enums.IdentifierType` (Deviation 7); `get_key_provider().index_key` and `.data_key`, `encrypt_value`, `aad_for` (`xolotl.platform.crypto.primitives`), `encrypt_json`; `record`, `AuditEvent`; `publish_screening_event`; `register_staff_only` (PLAN-3b-staff-only); `ObjectStore`.

Produces:

```python
# platform/errors.py
@dataclass(frozen=True, slots=True)
class ErrorItem: loc: tuple[str | int, ...]; msg: str; type: str
class XolotlError(Exception):
    def __init__(self, detail: str | None = None, *, code: str | None = None, errors: Sequence[ErrorItem] = ()) -> None   # self.errors: tuple[ErrorItem, ...]

# screening/errors.py
class PrivateListInvalidError(ValidationFailedError): default_code = "PRIVATE_LIST_INVALID"    # 422, errors = row errors
class PrivateListExistsError(DomainError): default_code = "PRIVATE_LIST_EXISTS"              # 409

# catalog/template.py
TEMPLATE_COLUMNS: tuple[str, ...] = ("clave", "tipo", "nombre_completo", "nombres", "primer_apellido", "segundo_apellido",
    "razon_social", "alias", "fecha_nacimiento", "rfc", "curp", "nacionalidad", "otro_identificador", "fecha_alta", "observaciones")
CSV_TYPE = "text/csv"; XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_REPORTED_ERRORS = 200
class TemplateError(StrEnum): EMPTY_FILE; UNREADABLE_FILE; MISSING_COLUMN; UNKNOWN_COLUMN; TOO_MANY_ROWS; REQUIRED; INVALID_TYPE;
    NAME_AMBIGUOUS; INVALID_DATE; INVALID_RFC; INVALID_CURP; RFC_TYPE_MISMATCH; INVALID_COUNTRY; DUPLICATE_KEY
@dataclass(frozen=True, slots=True)
class TemplateRow: number: int; cells: Mapping[str, str]            # number = the 1-based file row (header is row 1)
def read_template(data: bytes, content_type: str, *, max_rows: int) -> list[TemplateRow]
    # CSV: UTF-8 (BOM tolerated), delimiter "," or ";" chosen from the header line; XLSX: openpyxl read_only, data_only, first sheet,
    # date cells as ISO; header names trimmed and lower-cased; MISSING_COLUMN for "clave" or "tipo", UNKNOWN_COLUMN for others
def rows_to_entries(rows: Sequence[TemplateRow], *, list_code: str, key_tag: Callable[[str], str]) -> list[ListEntry]
    # raises PrivateListInvalidError with every row error (first MAX_REPORTED_ERRORS) when any row fails; never a partial result

# catalog/private.py
def create_private_list(s: Session, *, list_code: str, title: str, list_kind: ListKind | None) -> PrivateList
def upload_private_version(s: Session, store: ObjectStore, *, list_id: UUID, data: bytes, content_type: str) -> PrivateListVersion
def private_delta(previous: Mapping[str, bytes], current: Mapping[str, bytes]) -> ListDelta     # Deviation 6
def active_private_versions(s: Session) -> list[tuple[PrivateList, PrivateListVersion]]
def private_entry_of(s: Session, row: PrivateListEntryRow) -> ListEntry
```

Row rules: `tipo` `PF` → `PERSON`, `PM` → `ORGANIZATION`, else `INVALID_TYPE`; a PF needs `nombre_completo` or `nombres` + `primer_apellido` (both forms → `NAME_AMBIGUOUS`), a PM needs `razon_social` (`REQUIRED` otherwise); `EntryName(full=…, quality=PRIMARY, given=nombres, surname=" ".join(apellidos))`; `alias` split on `|`, trimmed, empties dropped, `STRONG`; `fecha_nacimiento` `AAAA`, `AAAA-MM` or `AAAA-MM-DD` → `PartialDate` (`INVALID_DATE`); `rfc`, `curp` through `validate(IdentifierType.RFC|CURP, raw)` (`INVALID_RFC`, `INVALID_CURP`), an RFC of 13 characters on a PM or 12 on a PF → `RFC_TYPE_MISMATCH`; `nacionalidad` codes must match `^[A-Z]{2}$` after upper-casing (`INVALID_COUNTRY`); `otro_identificador` → `EntryIdentifier(IdType.OTHER, value, normalized=upper alphanumerics)`; `fecha_alta` → `listed_on` (`INVALID_DATE`); `entry_key` = `clave` or `key_tag(f"{folded name}|{fecha_nacimiento}")`; a repeated key → `DUPLICATE_KEY` on the later row; Deviation 5 for `source_ref` and `observaciones`. Error `msg` is a fixed English sentence per `TemplateError` (`"invalid RFC"`, …).

`create_private_list`: staff or a client with the scope (the route checks access); `MX_LPB` forces `BLOCKED` (a given other kind → `ValidationFailedError`); `INTERNAL_<SLUG>` must match `^INTERNAL_[A-Z0-9_]{1,40}$` and take `SANCTION`, `PEP` or `INTERNAL` (default `INTERNAL`); a second list with the code → `PrivateListExistsError`; audit `private_list.created` (`detail={"list_code", "list_kind"}`). `upload_private_version`: size > `private_list_max_mb` MiB or empty → `ValidationFailedError`; content type outside `{CSV_TYPE, XLSX_TYPE}` → `ValidationFailedError("unsupported content type")`; `read_template` and `rows_to_entries` (key tags `hmac(index_key, b"private_list_entry_key:" + text).hexdigest()[:32]`); content tags `hmac(index_key, b"private_list_entry_content:" + entry.canonical_json())`; `delta = private_delta(current key→tag map, new key→tag map)`; seal: `version, dek = data_key(tenant, DATA, session=s)`, `encrypt_value(dek, version=version, plaintext=data, aad=aad_for(tenant, DATA))`, `store.put(tenant, f"private-lists/{list_id}/{version_id}", sealed, content_type="application/octet-stream")`; then, as D4 orders it, supersede, close `to_seq`, insert the version (`file_sha256` and `file_size` of the plaintext upload), the new entries (`entry=encrypt_json(json.loads(e.canonical_json()), …)`), the delta rows; audit `private_list.version_uploaded` (`detail={"list_id", "version_id", "seq", "entry_count", "added", "changed", "removed"}`, counts only); publish `PRIVATE_LIST_VERSION_ACTIVATED` (entity `private_list_version`, key `str(list_id)`, data `{"private_list_id", "version_id"}`). An upload identical in every tag to the active version still makes a new version (an explicit act of the compliance officer). `register_lists` calls `register_staff_only(PRIVATE_LIST_VERSION_ACTIVATED)`.

- [ ] **Step 1: Write the failing tests.** `test_errors.py`: `(PrivateListInvalidError, 422, "PRIVATE_LIST_INVALID")`, `(PrivateListExistsError, 409, "PRIVATE_LIST_EXISTS")`; a problems test `test_error_items_render_as_problem_errors` (`loc`, `msg`, `type`). `test_template.py` (no database; `key_tag=lambda s: "t:" + s`):

```python
def test_a_csv_and_the_same_xlsx_give_the_same_entries(): ...
def test_semicolon_csv_and_a_bom_are_read(): ...
@pytest.mark.parametrize(("cells", "column", "code"), [...])     # one case per TemplateError row rule
def test_each_row_rule_reports_its_code_and_cell(cells, column, code): ...   # loc == ("row", 2, column)
def test_every_bad_row_is_reported_up_to_two_hundred(): ...
def test_row_errors_never_echo_cell_values(): ...                  # the exception's detail, msgs and str() contain no cell of bad_rows.csv (Review Focus 4)
def test_a_blank_clave_gets_a_keyed_tag_and_duplicates_are_refused(): ...
def test_too_many_rows_and_missing_columns_are_file_errors(): ...  # loc == ("file",)
def test_identifiers_are_normalized_with_the_party_rules(): ...    # " goma-800101 h7a " style input → normalized RFC
def test_observaciones_and_row_numbers_never_enter_the_entry(): ...
```

`test_private.py` (database; keys; `staff_session` as an MLRO):

```python
def test_create_mx_lpb_once_and_internal_lists_by_slug(tenant): ...    # second MX_LPB → PRIVATE_LIST_EXISTS; INTERNAL_bad-slug → 422; audit rows
def test_an_upload_creates_an_active_version_with_all_added(tenant, store): ...
def test_a_new_private_version_supersedes_the_previous(tenant, store): ...     # lpb_v2: counts, closed and new rows, delta rows (Review Focus 1)
def test_an_invalid_upload_creates_nothing(tenant, store): ...                 # no version, no object, no audit
def test_no_plaintext_of_an_upload_is_stored(tenant, store, admin_engine): ...  # names, RFC, observaciones absent from everything_stored and from the sealed object; the object decrypts back to the upload
def test_content_tags_differ_across_tenants_for_one_file(tenant): ...
def test_the_activation_event_is_staff_only_and_carries_ids(tenant, store): ...  # event data keys; the type is registered staff-only
def test_private_entry_of_decrypts_what_was_uploaded(tenant, store): ...
def test_another_tenant_sees_no_private_list_rows(tenant, store): ...          # RLS: list, versions, entries, deltas all empty for tenant B
def test_private_delta_classifies_added_changed_removed(): ...
```
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_template.py tests/modules/screening/catalog/test_private.py tests/platform/test_errors.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the platform error extension, the two modules, the contract line `ignore_imports = xolotl.modules.screening.catalog.template -> xolotl.modules.party.identifiers` (and `-> xolotl.modules.party.enums`), the registration.
- [ ] **Step 4: Run to verify pass**, then the gates.
- [ ] **Step 5: Commit** `feat(screening): encrypted, versioned private lists from the CSV and XLSX template with row-level errors`.

---

### Task D7: The lists and private-list API

Depends on: D3, D4, D6; **LIB-lists T1**. Console: no. Branch `sp03a-d7-lists-api`.

**Files:**
- Create: `src/xolotl/modules/screening/schemas.py`, `src/xolotl/modules/screening/routes_lists.py`, `src/xolotl/modules/screening/routes_private_lists.py`, `src/xolotl/modules/screening/catalog/service.py`
- Modify: `src/xolotl/modules/screening/catalog/queries.py` (+ `delta_page`, `enabled_subscriptions`), `src/xolotl/modules/screening/catalog/private.py` (+ `view_entries`), `src/xolotl/app.py` (two routers after `party_relationship_routes.router`)
- Test: `tests/modules/screening/catalog/test_lists_routes.py`, `tests/modules/screening/catalog/test_private_list_routes.py`, `tests/platform/test_api_conventions.py` (passes unchanged: operation ids unique, every route declares access)

**Interfaces:**

Consumes: D3 `is_stale`, D4 `active_versions`, `entry_of`, D6 `create_private_list`, `upload_private_version`, `private_entry_of`; `v1_router`, `require_access`, `SessionDep`, `PageDep`, `Page`, `paginate`; the `object_store` dependency pattern of `routes_documents.py`; `record`; `ActorType`.

Produces (routes; endpoint function names are the operation ids):

| Function | Method and path | Access |
|---|---|---|
| `list_lists` | `GET /v1/lists` | `require_access(scope="lists:read")` (all staff roles) |
| `list_list_versions` | `GET /v1/lists/{code}/versions` | staff (`require_access()`), `paginate` on `(fetched_at, id)` |
| `list_version_delta` | `GET /v1/lists/{code}/versions/{version_id}/delta` | staff, Deviation 15, `limit` ≤ 200 |
| `get_list_entry` | `GET /v1/lists/{code}/entries/{entry_id}` | staff |
| `set_list_subscription` | `PATCH /v1/lists/subscriptions/{code}` | `roles=(Role.ADMIN,)`, staff only |
| `create_private_list_endpoint` | `POST /v1/private-lists` | `roles=(MLRO, ADMIN)`, `scope="private_lists:write"` |
| `list_private_lists` | `GET /v1/private-lists` | `roles=(MLRO, ADMIN)`, `scope="private_lists:read"` |
| `upload_private_list_version` | `POST /v1/private-lists/{list_id}/versions` (raw body, Deviation 4) | `roles=(MLRO, ADMIN)`, `scope="private_lists:write"` |
| `list_private_list_versions` | `GET /v1/private-lists/{list_id}/versions` | as upload, read |
| `list_private_list_entries` | `GET /v1/private-lists/{list_id}/entries` | `roles=(MLRO, ADMIN)`, staff only |

```python
# schemas.py (Response models; enums from screening.enums)
class ListOverview(BaseModel): code; list_kind; title; jurisdiction; requirement; enabled; active_seq: int | None;
    published_at; fetched_at; entry_count: int | None; stale: bool; failing: bool; last_error: str | None; last_success_at
class ListOverviewResponse(BaseModel): items: list[ListOverview]
class ListVersionResponse(BaseModel): id; seq; status; published_at; source_version; parser_version; fetched_at; loaded_by;
    file_sha256: str (hex); file_size; entry_count; added; changed; removed
class DeltaRowResponse(BaseModel): source_entry_key; change; prev_entry_id; new_entry_id; prev_name: str | None; new_name: str | None
class ListEntryResponse(BaseModel): id; list_code; source_entry_key; from_seq; to_seq; entry: dict[str, JsonValue]
class SubscriptionUpdate(BaseModel): enabled: bool
class PrivateListCreate(BaseModel): list_code: str; title: str (1..200); list_kind: ListKind | None = None
class PrivateListResponse(BaseModel): id; list_code; list_kind; title; created_at; active_version: PrivateListVersionResponse | None
class PrivateListVersionResponse(BaseModel): id; seq; status; uploaded_by; uploaded_at; content_type; file_size; entry_count; added; changed; removed
class PrivateListEntryResponse(BaseModel): id; source_entry_key; entity_type; entry: dict[str, JsonValue]

# catalog/service.py
def list_overview(s: Session, *, now: datetime) -> list[ListOverview]     # one row per subscription (enabled or not), ordered by code
def set_subscription_enabled(s: Session, code: str, *, enabled: bool) -> TenantListSubscription
    # NotFoundError for an unsubscribed code; MANDATORY and enabled=False → ListMandatoryError; changed_by = actor id;
    # audit lists.subscription_changed {"list_code", "enabled"} (only when the value changes)
# catalog/queries.py
def delta_page(s: Session, version_id: UUID, *, after_key: str | None, limit: int) -> list[DeltaRowView]   # joins prev/new primary names
def enabled_subscriptions(s: Session) -> list[TenantListSubscription]
# catalog/private.py
def view_entries(s: Session, list_id: UUID, *, page: PageRequest) -> tuple[list[tuple[PrivateListEntryRow, ListEntry]], str | None]
    # the active version's members, paginated by (from_seq, id); one VIEW_PII audit event (entity private_list, field "entries")
    # and one private_list.entries_viewed {"list_id", "count"} per page, both before any value is decrypted
```

Versions and deltas of a code the tenant is not subscribed to are still readable by staff (public data); entries of another list's code are `NOT_FOUND`. The private routes return `NOT_FOUND` for another tenant's list id (RLS). `PrivateListInvalidError` answers 422 with `errors` (Deviation 10).

- [ ] **Step 1: Write the failing tests.** `test_lists_routes.py` (`client`, `admin_headers`, an analyst's headers, an API key with `lists:read`; `seed_sources`, ITF pack, UN v1 then `un_v2.xml` ingested):

```python
def test_get_lists_shows_each_subscription_with_its_version_and_freshness(...): ...   # UN active_seq 2, stale False, OFAC no version, stale True
def test_an_api_client_with_lists_read_sees_the_overview_and_nothing_else(...): ...   # 200 on /lists; 403 on versions, delta, entries
def test_versions_page_newest_first_with_a_cursor(...): ...
def test_the_delta_shows_added_changed_and_removed_with_names(...): ...              # CHANGED row has prev_name and new_name; cursor by key
def test_an_entry_is_its_list_entry_json(...): ...                                   # == json.loads(ListEntry.canonical_json())
def test_admin_toggles_a_recommended_list_and_it_is_audited(...): ...
def test_a_mandatory_list_cannot_be_disabled(...): ...                               # 409 LIST_MANDATORY
def test_only_admin_may_toggle(...): ...                                             # ANALYST 403 ROLE_FORBIDDEN; AUDITOR_RO 403
```

`test_private_list_routes.py`:

```python
def test_mlro_creates_and_lists_private_lists(...): ...                              # Idempotency-Key replays the same body
def test_an_analyst_and_an_auditor_cannot_reach_private_lists(...): ...              # 403 on every route
def test_an_api_key_with_the_scope_uploads_and_without_it_is_refused(...): ...
def test_an_upload_returns_the_version_and_history_lists_it(...): ...
def test_an_invalid_upload_answers_row_errors_without_values(...): ...              # 422 PRIVATE_LIST_INVALID, errors[].loc == ["row", n, col]; no cell text in the body (Review Focus 4)
def test_an_unsupported_content_type_or_oversize_body_is_refused(...): ...
def test_entries_are_revealed_to_staff_with_view_pii_and_an_audit_row(...): ...
def test_an_api_client_never_reads_entries(...): ...                                 # 403 with private_lists:read
def test_tenant_b_gets_not_found_for_tenant_a_lists(...): ...
```
- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/screening/catalog/test_lists_routes.py tests/modules/screening/catalog/test_private_list_routes.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement** the schemas, services, routers and the mounting.
- [ ] **Step 4: Run to verify pass**, plus `tests/platform/test_api_conventions.py`, then the gates.
- [ ] **Step 5: Commit** `feat(screening): lists and private-list API with subscriptions, versions, deltas and audited entry reveal`.

---

### Task D8: Console — Listas (sources, freshness, versions with deltas, subscription toggles)

Depends on: D7. Console: yes. Branch `sp03a-d8-console-lists`.

**Files:**
- Create: `console/src/features/lists/queries.ts`, `ListsPage.tsx`, `ListVersionsPage.tsx`, `FreshnessBadge.tsx`, `DeltaTable.tsx`, `EntrySheet.tsx`, `freshness.ts`, `freshness.test.ts`, `ListsPage.test.tsx`, `ListVersionsPage.test.tsx`
- Modify: `console/src/api/schema.d.ts` (regenerate), `console/src/api/types.ts`, `console/src/auth/access.ts`, `access.test.ts`, `guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `en.json`, `catalogue.test.ts`

**Interfaces:**

Consumes: the generated `ListOverview`, `ListOverviewResponse`, `ListVersionResponse`, `DeltaRowResponse`, `ListEntryResponse`, `ListKind`, `ListRequirement`, `DeltaChange`; `PageHeader`, `DataTable`, `Badge`, `KeyValues`, `Sheet`, `Switch`/`Button`, `ErrorBlock`, `CursorNav` (from `features/audit`), `useFormat`, `useAuth`, `renderWithProviders`, `fakeApi`.

Produces:

```ts
// auth/access.ts
lists: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN'], listsAdmin: ['ADMIN']
// nav.ts: 'nav.lists' { to: '/lists', icon: ListChecks, roles: ACCESS.lists } after nav.policy
// App.tsx: '/lists' and '/lists/:code' lazy routes behind RequireRole ACCESS.lists
// freshness.ts
export type Freshness = 'FRESH' | 'STALE' | 'FAILING' | 'EMPTY'
export function freshnessOf(row: ListOverview): Freshness   // no active version → EMPTY; failing → FAILING; stale → STALE; else FRESH
// queries.ts: useListOverview(), useListVersions(code, cursor), useListDelta(code, versionId, cursor), useListEntry(code, entryId),
//   useSetSubscription() (PATCH, invalidates ['lists'])
```

Pages: `ListsPage` — table of subscriptions (code mono, title, requirement badge, freshness badge with `last_error` as its tooltip, active version, entries, last success), a switch for `RECOMMENDED` rows shown to `ACCESS.listsAdmin` only (mandatory rows show a lock and `lists.mandatory`); a row links to `/lists/:code`. `ListVersionsPage` — versions table (seq, status, published, fetched, counts, loaded by) with cursor navigation; selecting a version shows `DeltaTable` with three filter buttons (`ADDED`, `CHANGED`, `REMOVED`; client-side filter of the page) and, for `CHANGED`, previous and new names side by side; clicking a name opens `EntrySheet` with the entry's names, aliases, dates, nationalities, programmes and identifiers. Catalogue keys under `lists.*` (titles, columns, `lists.freshness.{FRESH,STALE,FAILING,EMPTY}`, `lists.requirement.{MANDATORY,RECOMMENDED}`, `lists.change.{ADDED,CHANGED,REMOVED}`, `lists.mandatory`, `lists.toggled`), `nav.lists` "Listas"/"Lists", `errors.LIST_MANDATORY`, `errors.LIST_MASS_REMOVAL`, `errors.LIST_FORMAT_INVALID` in both catalogues and the code list.

- [ ] **Step 1: Regenerate the API types and write the failing tests** (`gen:api:file` from `build_app().openapi()`, as plan 2b's B6). `freshness.test.ts` (the four cases). `access.test.ts`/`guards.test.tsx` for `lists` and `listsAdmin`. `ListsPage.test.tsx`: loading then rows with badges; `FAILING` shows the error code; an analyst sees no switch; an admin toggles OFAC off and the PATCH body is `{enabled: false}`; a mandatory row has no switch; a 409 `LIST_MANDATORY` shows the translated error; empty state; names render as text. `ListVersionsPage.test.tsx`: versions page with cursor; selecting a version requests its delta; the `CHANGED` filter shows both names; opening an entry requests `/lists/{code}/entries/{id}`.
- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck` and `npx vitest run src/features/lists src/auth` → FAIL.
- [ ] **Step 3: Implement** following `features/connectors` and `features/audit` for tables, cursors and queries.
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; `make ci`.
- [ ] **Step 5: Commit** `feat(console): Listas page with freshness, versions, deltas and subscription toggles`.

---

### Task D9: Console — Listas privadas (create, template, upload with row errors, history, revealed entries)

Depends on: D7 (can run beside D8; both touch nav, access, App and the catalogues additively). Console: yes. Branch `sp03a-d9-console-private-lists`.

**Files:**
- Create: `console/public/plantilla-lista-privada.csv`, `console/src/features/private-lists/queries.ts`, `PrivateListsPage.tsx`, `CreatePrivateListDialog.tsx`, `PrivateListPage.tsx`, `UploadCard.tsx`, `RowErrorsTable.tsx`, `EntriesCard.tsx`, `row-errors.ts`, `row-errors.test.ts`, `PrivateListsPage.test.tsx`, `PrivateListPage.test.tsx`, `tests/modules/screening/catalog/test_template_file.py`
- Modify: as D8 (`schema.d.ts` regenerated if D8 has not merged), `types.ts`, `access.ts` (+ tests), `nav.ts`, `App.tsx`, both catalogues, `catalogue.test.ts`

**Interfaces:**

Consumes: generated `PrivateListResponse`, `PrivateListCreate`, `PrivateListVersionResponse`, `PrivateListEntryResponse`, the `Problem` type with `errors`; `useIdempotentMutation` (POSTs), the upload pattern of `features/parties/DocumentsCard.tsx` and `queries.ts` (raw `File` body with its content type); `RevealCard` pattern from `features/parties`.

Produces:

```ts
// access.ts: privateLists: ['MLRO', 'ADMIN']
// nav.ts: 'nav.privateLists' { to: '/private-lists', icon: ListLock (or Lock), roles: ACCESS.privateLists } after nav.lists
// App.tsx: '/private-lists' and '/private-lists/:listId'
// row-errors.ts
export interface RowError { row: number | null; column: string | null; code: string }
export function rowErrorsOf(problem: Problem): RowError[]   // loc ["row", n, col] → {row, column}; ["file"] → nulls; type → code
```

Pages: `PrivateListsPage` — the lists with their active version (or "Sin versión"), a banner `privateLists.lpbMissing` when no `MX_LPB` list has an active version, and the create dialog (LPB, or internal with slug, kind `SANCTION`/`PEP`/`INTERNAL` and title). `PrivateListPage` — `UploadCard` (file input accepting `.csv,.xlsx`, client checks of type and `20` MB, a "Descargar plantilla" link to `/plantilla-lista-privada.csv`, and on a 422 the `RowErrorsTable` of row, column and the translated `privateLists.rowError.<CODE>`, never a value); the version history table; `EntriesCard` behind a "Revelar" button that loads `/entries` (one audited view per page) and shows key, type, name and identifiers. Catalogue keys under `privateLists.*` (including `privateLists.rowError.*` for every `TemplateError` value), `nav.privateLists` "Listas privadas"/"Private lists", `errors.PRIVATE_LIST_INVALID`, `errors.PRIVATE_LIST_EXISTS`.

- [ ] **Step 1: Write the failing tests.** `test_template_file.py`: `test_the_console_template_header_is_the_template_columns` (reads `console/public/plantilla-lista-privada.csv`, first line split on `,` == `TEMPLATE_COLUMNS`). `row-errors.test.ts`: row, file and malformed `loc` cases. `PrivateListsPage.test.tsx`: lists and the LPB banner; creating an LPB sends an `Idempotency-Key` and `{list_code: 'MX_LPB', title}`; a 409 `PRIVATE_LIST_EXISTS` is shown; access for `ACCESS.privateLists` only. `PrivateListPage.test.tsx`: an upload sends the file with `text/csv`; a 422 renders three row errors with translated codes and no cell text; a too-large file is refused before any request; history lists versions; entries load only after "Revelar"; text never renders as markup.
- [ ] **Step 2: Run to verify failure.** `cd console && npx vitest run src/features/private-lists src/auth`; `uv run pytest tests/modules/screening/catalog/test_template_file.py --no-cov -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass.** The console gates and `make ci`.
- [ ] **Step 5: Commit** `feat(console): Listas privadas page with template, upload, row errors, history and revealed entries`.

---

## Task table

| Id | Title | Depends on | Library / other plans | Console | Branch |
|---|---|---|---|---|---|
| D1 | Migration 0009, models, enums, settings, registry, factories | — | — | no | `sp03a-d1-migration-0009` |
| D2 | Source catalog and seed, fetcher port and HTTP adapter, `lists seed`/`status` | D1 | — | no | `sp03a-d2-sources-fetcher` |
| D3 | `lists.poll` tenant half: subscriptions, catch-up events, stale and LPB notifications, schedule | D1, D2 | — | no | `sp03a-d3-poll-subscriptions` |
| D4 | Ingestion, mass-removal guard, shared raw files, `lists load-file`/`show` | D1, D2 | LIB-lists T1 | no | `sp03a-d4-ingest` |
| D5 | `lists.poll` shared half: lease, conditional fetch, backoff, `lists fetch` | D2, D3, D4 | LIB-lists T1 | no | `sp03a-d5-poll-fetch` |
| D6 | Private lists: template, validation, encryption, versions, event | D1, D3, D4 | LIB-lists T1; PLAN-3b-staff-only | no | `sp03a-d6-private-lists` |
| D7 | Lists and private-list API | D3, D4, D6 | LIB-lists T1 | no | `sp03a-d7-lists-api` |
| D8 | Console: Listas | D7 | — | yes | `sp03a-d8-console-lists` |
| D9 | Console: Listas privadas | D7 | — | yes | `sp03a-d9-console-private-lists` |

## Parallelism

- **D1 first.** Plan 3b's migration 0010 can be written once D1 merges (its FKs reference `list_entry`, `list_version`, `private_list_version`, `private_list_entry`).
- **D2 ∥ (LIB-lists T1)**; **D3 after D2** (it uses `support.seed_sources`); **D4 after D2 and LIB T1**; D3 ∥ D4 (no file in common except two export lines in `catalog/__init__.py`; `support.py` gets two different helpers).
- **D5 after D3 and D4.** **D6 after D3 and D4** (`events.py`, `errors.py`) and after PLAN-3b-staff-only; D5 ∥ D6 (they share only `app.py`'s `register_lists` call, which D6 extends by one line: the second to merge rebases). PLAN-3b-staff-only is a platform-only 3b task with no 0010 dependency; schedule it at the start of 3b so D6 never waits.
- **D7 after D4 and D6** (D5 is not needed by the routes). **D8 ∥ D9** after D7 (additive edits to `nav.ts`, `access.ts`, `App.tsx`, both catalogues, `catalogue.test.ts`; `schema.d.ts` is regenerated, never merged by hand).
- Shared files across plans: `src/xolotl/app.py`, `src/xolotl/cli.py`, `src/xolotl/models.py`, `.importlinter`, `src/xolotl/platform/config.py`, `src/xolotl/modules/screening/{enums,errors,events,schemas}.py` (3b appends), `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_errors.py`, `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`, the console nav and catalogues. Every edit is additive.

## Exit criteria

Plan 3a's share of spec §16 and the SP-03 exit: with `make ci` green after every task, `xolotl lists seed` registers the five shared sources; `xolotl lists load-file UN_CONSOLIDATED <library fixture>` then `<un_v2.xml>` creates versions 1 and 2 with one added, one changed and one removed entry (immutable rows with `from_seq`/`to_seq`, delta rows, the raw files under the shared namespace), and once library T2 and T3 merge the same command ingests OFAC SDN, EU, UK and SAT 69-B with no 3a change; `GET /v1/lists` shows each subscribed list fresh with its version; in `prod` the `lists.poll` job fetches each due source once across all tenants under a lease, honours `304`, refuses a mass removal and backs off on failure, keeping the previous version active; a tenant's subscriptions follow its effective policy, a mandatory list cannot be disabled and a recommended one can; each new version reaches every subscribed tenant as one `lists.version_published.v1` within one poll (the first as `initial`); stale sources and a missing LPB notify ADMIN and MLRO once per episode or day; tenant A uploads an `MX_LPB` CSV or XLSX, a bad row answers 422 with row errors and no values, a good one creates an encrypted, versioned list with a delta and the staff-only `private_list.version_activated.v1`; tenant B sees none of tenant A's private lists; `GET /v1/audit/verify` stays intact; the console shows both pages to the roles that may see them.

## Open questions for the product owner

1. **EU FSF access token.** The EU's consolidated XML download has historically required a token in the URL. If the current publication still does, is a committed public token acceptable in `sources.yaml`, or should it be a setting (`XOLOTL_EU_FSF_TOKEN`, appended to the URL by the fetcher)? This plan assumes a public URL; D2 raises it if the check on the day finds otherwise.
2. **Staleness in development.** With fetching off, a list loaded by `lists load-file` becomes stale after twice its interval and each tenant is notified once. Acceptable as honest dev behaviour, or should staleness be silenced when `LISTS_FETCH_ENABLED` is false?
3. **Who may upload private lists through the API.** The spec gives API keys `private_lists:write`. A key holder can then replace the tenant's LPB without four-eyes; should uploads by API key be allowed in the pilot, or staff-only like entry viewing?
