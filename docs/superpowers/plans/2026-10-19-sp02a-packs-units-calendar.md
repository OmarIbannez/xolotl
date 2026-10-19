# SP-02a — Policy packs, units, calendar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform its regulatory reference data and the code that reads it: versioned policy packs loaded from YAML with per-value provenance, tenant pack assignments, tighten-only overrides, a resolver that produces an effective policy for a date and product and freezes it in a canonical-JSON snapshot; the `unit_value` store with corrections, the `UnitService` and its conversion rules, seeded UMA, UDI and FIX values, a Banxico SIE fetcher and a daily fetch job; the Mexico holiday calendar with business-day helpers and the deadline DSL; the tenant regulatory profile and branch catalogue; the CLI (`packs`, `units`, `holidays`) and the console pack viewer, units and calendar pages.

**Architecture:** Four shared reference tables (`policy_pack`, `policy_pack_version`, `unit_value`, `holiday`) sit outside row-level security and are written by the owner engine (packs, holidays) or by the app role under an explicit grant (`unit_value`, the fetch job); five tenant tables (`tenant_pack_assignment`, `tenant_override_version`, `policy_snapshot`, `tenant_regulatory_profile`, `tenant_branch`) follow the kernel's forced-RLS conventions. Three platform packages depend in one direction, `calendar` ← `units` ← `policy`: the calendar is pure functions over a `Holidays` value loaded from the shared table, the unit service resolves a dated row per `ConversionRule` through the calendar and records the `unit_value.id` it used (D5), and the policy resolver merges the strictest values of the assigned packs, applies the latest override and hashes the result over canonical JSON (D4, addendum b). The tenants module exposes everything through `routes_policy.py` and `routes_units.py`; the console reads the same routes.

**Tech Stack:** SP-01 stack (Python 3.13, FastAPI, SQLAlchemy 2.1, Alembic, psycopg 3, Postgres 17, React 19 / Vite / TypeScript) plus `pyyaml` moved from the dev group to the runtime dependencies (`types-pyyaml` stays dev). `httpx` (runtime) and `respx` (dev) are already installed.

**Spec:** `docs/superpowers/specs/2026-10-19-sp02-packs-units-calendar-party-design.md` §1 (the 2a bullet), §2, §3 (`PACKS_DIR`, `ALLOW_UNVERIFIED_PACK_VALUES`, `BANXICO_TOKEN`, `UNITS_FETCH_CRON`), §4.1 (all four shared tables), §4.2 (`tenant_pack_assignment`, `tenant_override_version`, `policy_snapshot`, `tenant_regulatory_profile`, `tenant_branch`), §5, §6, §7, §9 policy API, §10 (`packs load|validate|show`, `units seed|fetch`, `holidays seed`), §11 (pack viewer, units, calendar pages), §12 rows for packs, units and calendar, §13. Program spec: D4 (packs and snapshots), D5 (units and calendar), D21 (Mexico content), addendum (b) canonical JSON, §4 ports `UnitValueSource` and `Clock`. Plans 2b and 2c consume the names listed in `00-common.md`; they appear here with exactly those signatures.

## Global Constraints

- Migration: `src/xolotl/migrations/versions/0006_policy_units_calendar.py`, `revision = "0006_policy_units_calendar"`, `down_revision = "0005_encrypt_idempotency"`; the only migration of this plan; it creates every table of §4.1 and the five §4.2 tables above.
- Packages: `src/xolotl/platform/policy/`, `src/xolotl/platform/units/`, `src/xolotl/platform/calendar/`; routes in `src/xolotl/modules/tenants/routes_policy.py` and `routes_units.py`; `xolotl.platform` imports nothing from `xolotl.modules` (import-linter); `calendar` imports nothing from `units` or `policy`; `units` imports `calendar`; `policy` imports both.
- Data files: `policy-packs/MX/ITF-IFPE/v1.yaml`, `policy-packs/MX/LFPIORPI-AVI/v1.yaml`, `policy-packs/_units/UMA.yaml`, `policy-packs/_units/UDI.seed.yaml`, `policy-packs/_units/USD_MXN_FIX.seed.yaml`, `policy-packs/_holidays/MX.yaml`, `policy-packs/_jurisdictions.yaml`; goldens under `tests/golden/packs/`, `tests/golden/units/`, `tests/golden/calendar/`. The image carries `policy-packs/` (`COPY policy-packs ./policy-packs` in the Dockerfile, `policy-packs/**` in `render.yaml` `buildFilter.paths`, pinned by `tests/test_render_blueprint.py`).
- Settings (prefix `XOLOTL_`, `src/xolotl/platform/config.py`): `packs_dir: Path = Path("./policy-packs")`, `allow_unverified_pack_values: bool = False` (prod refuses `true`), `banxico_token: SecretStr | None = None` (unset = demo mode over the seed files; the Banxico source refuses demo mode when `env == "prod"`, spec §3), `units_fetch_cron: str = "0 18 * * 1-5"` (validated with `croniter.is_valid`); schedule `units.fetch` runs in `tz="America/Mexico_City"`.
- Shared tables are listed in `SHARED_TABLES` (`policy_pack`, `policy_pack_version`, `unit_value`, `holiday`); `SHARED_WRITABLE` is unchanged (`policy_pack` stays `GRANT SELECT` only: `tests/platform/test_ddl.py` pins it); `unit_value` gets `GRANT SELECT` through `grant_shared` plus `GRANT INSERT, UPDATE` emitted by the migration.
- Every tenant table: `class X(TenantTableMixin, Base)`, `tenant_table_args`, composite FKs through `tenant_fk`, `enable_rls("<table>")` in the migration with the kernel predicate, an isolation factory in `tests/isolation/factories.py`, schema-sync and autogenerate tests green.
- API: every router from `v1_router`; every route declares `require_access(...)`; writes are `require_access(roles=(Role.ADMIN,), write=True)`; reads are `require_access()` (staff of any role, no API-key scope in this plan); `POST` needs `Idempotency-Key`; `PUT` does not; lists are `Page[T]` through `paginate` on `(created_at, id)`; unique handler names (they are the `operationId`s).
- Encrypted columns: `tenant_regulatory_profile.rfc` and `tenant_branch.address` are `EncryptedStr` (no `_enc` suffix: the kernel's convention, see Deviations); responses carry a masked form (`mask_tail` from `xolotl.platform.text`: `"****"` + the last four), never an `Encrypted`; nothing in this plan calls `reveal_for_response`.
- Audit actions added: `policy.pack_assigned`, `policy.override_created`, `tenant.regulatory_profile_set`, `tenant.branch_created`; `detail` holds ids, codes and ISO dates only (no floats, no RFC, no address).
- Events: none are published by this plan (ids-only events stay the rule for plans that publish).
- Problem codes added: `UNIT_VALUE_MISSING` (503), `UNIT_SOURCE_UNAVAILABLE` (503), `UNVERIFIED_VALUE` (422), `PACK_INVALID` (422), `PACK_VERSION_IMMUTABLE` (409), `POLICY_LOOSENING` (409), `POLICY_NOT_ASSIGNED` (404).
- Job kind `units.fetch` (`UNITS_FETCH_KIND`), per-tenant schedule named `units.fetch` on `settings.units_fetch_cron`; notification template `units.fetch_failed` to `Role.ADMIN` and `Role.MLRO`.
- Canonical JSON: `content_hash = sha256(canonical_json(model.model_dump(mode="json")))` with `canonical_json` from `xolotl.platform.audit` (sorted keys, no whitespace); Decimals and dates are strings; `contains_float` must be false for every JSONB written.
- No S3, Redis, Kafka or KMS; one container plus Postgres; `make ci` (and the console gates for Tasks A9 and A10) green at the end of every task; `make format` before `make lint`.

## Deviations from the sub-spec (decided here)

- `UnitValueMissing` is spelled `UnitValueMissingError` (ruff N818 requires the `Error` suffix); plans 2b and 2c import that name.
- Column types: the spec's `char(2)`/`char(3)` are `String(2)`/`String(3)` (the kernel's `tenant.country_code` precedent); `holiday.scope` and `tenant_branch.status` are `StrEnum`s stored as VARCHAR(32) (`HolidayScope`, `BranchStatus`); `ix_unit_value_lookup` is declared without `DESC` (a B-tree scans backwards; Alembic cannot compare the direction).
- Encrypted columns drop the spec's `_enc` suffix: `tenant_regulatory_profile.rfc` and `tenant_branch.address` in the models, the migration DDL and the isolation factories, as the kernel does (`staff_credential.totp_secret`, renamed from `totp_secret_enc` by migration 0004; `response_body` for `EncryptedJson`) and as plans 2b (`connector_credential.secret`) and 2c do, so the SP-02 schema reads one way; the masked response field stays `rfc_masked`.
- `tenant_branch` gains `created_at timestamptz NOT NULL DEFAULT now()` (cursor pagination needs `(created_at, id)`); the branch list and the create response omit the address (it is encrypted; only SP-05's serializer reads it, through `.reveal()`).
- The calendar helpers take a `Holidays` value (`load_holidays(session, *, country, scope)`) instead of `country`/`scope` on every call, so the helpers and the goldens are pure; `UnitService.lookup` keeps the spec's `country="MX", scope=HolidayScope.BANXICO` keywords and loads the value itself.
- `DeadlineRule` is a structured model `{kind, params, tz}` (no text grammar); `GET /v1/calendar/due` takes it as JSON in the `rule` query parameter; `ReportRef` carries `anchor` (`period_end`, `committee_decision`, `detection`) beside the rule, and the caller supplies the anchor datetime.
- Per spec §2 (every threshold, deadline, period, list name or legal switch is `Provenanced`), `Capture.period`, `Capture.window` and `ReportRef.deadline` are `Provenanced` (the §5.1 sketch shows them bare); `PackConfig` adds `title` and `effective_to`; `Capture` adds `merge_into: Provenanced[str] | None` for the one-aviso-under-A rule; `ScreeningBands`, `recommended_lists` and `conversion_rule` stay plain as sketched. The spec's "tier-limit capture" is not a `Capture` entry: D13 makes `tier_limit` an ALERT rule kind that reads `kyc.tiers[].limits` (SP-04), and a capture row would duplicate the tier limits.
- `UnitsPolicy`, `PepPolicy` and `TenantOverride` (left open by the spec) are shaped in Tasks A6 and A7; `Confidence` lives in `platform/enums.py` because units seeds and packs both carry it.
- `ConversionRule.PREV_MONTH_AVG` is refused by `lookup` with 422 `VALIDATION_FAILED` in SP-02 (no pack uses it; a multi-row conversion has no single `unit_value.id` under D5); `SEMIANNUAL_SNAPSHOT` resolves to the last day of the previous half-year (30 June or 31 December) strictly before `on`.
- `create_override` has no `effective_from` parameter (00-common.md): the version takes effect on the UTC date of `clock.now()` and is validated once against the union of every pack version in force on that date whatever its product (`merge_packs` over `assignments_in_force`): a value that tightens the strictest merge tightens every product's policy, and a tier, capture or review level unknown to the union is 422 `VALIDATION_FAILED`. `apply_override` skips an entry whose tier, capture or review level a product's body does not carry, and never loosens: where a pack that comes into force later is stricter than the override, the pack value stays.
- `assign_pack` closes an earlier open assignment of the same product (`product IS NULL` counts as a product) on the day before the new `effective_from` when the new date is later; any other overlap of the same product is 409 `CONFLICT`. Spec §5.3 and D21 (two packs for one tenant need distinct products, so exactly one crypto capture applies) are enforced as: a tenant-wide assignment never overlaps a product assignment, whatever the packs (409 `CONFLICT`), while product assignments of different products coexist. An ITF tenant whose crypto product reports under LFPIORPI therefore assigns the ITF pack once per non-crypto product and the AVI pack to `CRYPTO`; no product ever merges two packs, and `merge_packs` serves the cross-product override validation (open question 7).
- `GET /v1/policy/effective` converts tier limits and capture thresholds best-effort: a missing unit value leaves that entry `null` and lists the unit in `missing_units` (the viewer must still render the policy); decision paths call `UnitService` directly and get the 503. The GET creates the snapshot it reports (`snapshot_policy` is an idempotent upsert): a write on a read route, also under an `AUDITOR_RO` session (`require_access` infers a GET as a read); accepted, because the upsert is idempotent, records no actor and the app role holds the INSERT grant.
- Extra read endpoints the console needs: `GET /v1/units`, `GET /v1/units/{code}/values`, `GET /v1/calendar/holidays`, `GET /v1/tenants/me/regulatory-profile`; two extra audit actions (`tenant.regulatory_profile_set`, `tenant.branch_created`).
- `units.fetch` is a per-tenant schedule (the job system is tenant-scoped); the handler is idempotent (unchanged values insert nothing), so N tenants cost N cheap calls a day; a source failure notifies ADMIN and MLRO and completes the job (the next slot retries) instead of failing eight times.
- `MX/ITF-IFPE` v1 `effective_from` is pinned to `2026-01-01` ("the platform start date" in the spec); the product owner may lower it before the first production load.
- Holidays: the SAT scope holds CFF art. 12 days plus the RMF regla 2.1.6 Semana Santa days and excludes the SAT general vacation periods (open question 1); CNBV, SAT-RMF and BANXICO rows list weekday dates only, as their publications do; every 2027 row of a published scope is a projection flagged `verify: true`, as are the Semana Santa, 2 November and 12 December rows.
- Unit goldens use fixture values from `tests/golden/units/values.yaml`; the committed UDI and FIX seeds hold the values Banxico published (copied from SIE series SP68257 and SF43718 for the pinned date ranges) and are tested for shape, not value; the UMA 2026 row is `UNVERIFIED` (§13) and no golden depends on it.
- If the SIE series cannot be read on the day the UDI and FIX seed files are written, their rows are committed with `confidence: UNVERIFIED` and a `legal_basis` saying so (never invented), `units seed` refuses them without `--allow-unverified`, demo mode (dev and CI only) reads them anyway, and the product owner replaces them before the first production load (Task A4).
- Console: no assignment or override form in 2a (ADMINs use the API; the Playwright smoke assigns the ITF pack through the API before opening the viewer); the three pages are open to every signed-in role.
- The regulatory profile's RFC is validated by shape (`RFC_SHAPE` from `xolotl.platform.text`, `^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$` after upper-casing) and masked with `mask_tail` from the same module; Task A1 creates `src/xolotl/platform/text.py` so every later task of the three plans has it (plan 2b's B4a imports `RFC_SHAPE` instead of defining its own; plan 2c's `masking.mask_identifier` delegates to `mask_tail`); the homoclave validator is plan 2c's and `modules/tenants` must not import `modules/party`.
- `iso_duration_days` and `add_iso_duration` (`xolotl.platform.policy.durations`) are exported so plan 2c computes `retention_until` without a second duration parser.
- `Conversion.unit_value_id` is `int | None` where spec §6 says `int`: the MXN identity records no row because no conversion happened; `Conversion` also carries `unit`, `currency`, `rate` and `resolved_date` so a stored amount is reproducible without a second lookup.
- Spec §2 asks a failing golden to print a unified diff; the goldens here are parametrised assertions per case and key, so pytest's assertion diff of the failing value stands in for it.
- The cron validator in `config.py` is `_check_cron`, one `@field_validator("units_fetch_cron")` whose message names the failing field from `info.field_name` (`f"XOLOTL_{info.field_name.upper()} must be a five-field cron expression"`); plan 2c's C2 adds `"retention_purge_cron"` to that decorator and nothing else, so the validator's name and wording stay field-agnostic.
- `UnitSourceAuthError(UnitSourceError)` (Task A5, `units/errors.py`) is raised when Banxico rejects the token (HTTP 401 or 403) and keeps the 503 `UNIT_SOURCE_UNAVAILABLE` code; plan 2b's B4b maps it to `PermanentCallError` (no retry) where every other `UnitSourceError` is transient. A 400 is a plain `UnitSourceError` like any other status (Xolo's port read it as a token rejection).

## Review Focus

1. Hash stability: the same pack must hash identically after a YAML key reorder and whether an amount is written `750`, `"750"` or `750.0`, and no float may reach `policy_pack_version.config` or `policy_snapshot.content`. Tests in Task A6 (`test_content_hash_ignores_key_order_and_number_spelling`, `test_config_holds_no_floats`).
2. Scope and zone confusion in date resolution: `PRIOR_BANKING_DAY` across the Banxico-only 12 December 2025, `NTH_BUSINESS_DAY_OF` across 1 January 2026 and Semana Santa 2026 under CNBV versus NATIONAL, and an anchor given in UTC just after midnight resolving to the Mexico City date. Goldens in Tasks A3 and A4.
3. Tighten-only comparisons that look tighter but are not: an override in another unit (`500 USD` against `750 UDI`), a duration spelled differently (`P12M` against `P1Y`), a band that is numerically lower but semantically looser (`id_match_floor 0.90`). Tests in Task A7 (`test_overrides_that_loosen_are_refused`, parametrised).
4. Corrections and idempotency of the fetch: a re-fetched value that differs supersedes the old row exactly once, a re-run with equal values inserts nothing, and `lookup` ignores superseded rows whatever their `fetched_at`. Tests in Tasks A4 (`test_a_correction_supersedes_once`) and A5 (`test_the_job_is_idempotent_across_runs`).
5. Privileges on the shared tables: the app role writes `unit_value` (the job) but cannot insert into `policy_pack`, `policy_pack_version` or `holiday`, and the isolation factory for `tenant_pack_assignment` creates its shared parent through the owner. Tests in Task A2 (`test_0006_grants_follow_the_plan`) and the isolation harness.

## File structure

```
pyproject.toml, uv.lock                              pyyaml to runtime
.env.example, README.md                              four settings, the packs/units/calendar guide
Dockerfile, render.yaml                              COPY policy-packs, buildFilter entry, XOLOTL_PACKS_DIR
.github/workflows/ci.yml                             packs load / holidays seed / units seed before the smoke
policy-packs/_jurisdictions.yaml                     FATF lists seed (SP-07 reads it; parse test only)
policy-packs/_holidays/MX.yaml                       2025–2027 holidays, four scopes
policy-packs/_units/UMA.yaml                         annual UMA values with DOF provenance
policy-packs/_units/UDI.seed.yaml                    daily UDI 2025-12-01..2026-01-31 (SIE SP68257)
policy-packs/_units/USD_MXN_FIX.seed.yaml            FIX per banking day 2025-12-01..2026-01-31 (SIE SF43718)
policy-packs/MX/ITF-IFPE/v1.yaml                     first pack
policy-packs/MX/LFPIORPI-AVI/v1.yaml                 second pack
src/xolotl/platform/config.py                        packs_dir, allow_unverified_pack_values, banxico_token, units_fetch_cron
src/xolotl/platform/text.py                          RFC_SHAPE, mask_tail (A1; A8 and plans 2b and 2c import them)
src/xolotl/platform/enums.py                         + Confidence, HolidayScope, BranchStatus
src/xolotl/platform/tenancy/registry.py              + four shared tables
src/xolotl/migrations/versions/0006_policy_units_calendar.py
src/xolotl/models.py                                 + calendar_models, policy_models, units_models
src/xolotl/platform/calendar/__init__.py             re-exports
src/xolotl/platform/calendar/models.py               Holiday
src/xolotl/platform/calendar/holidays.py             Holidays, HolidayRow, read_holiday_file, holidays_from_rows, load_holidays, seed_holidays
src/xolotl/platform/calendar/business.py             is_business_day, next_business_day, prev_business_day, add_business_days, nth_business_day_of_month
src/xolotl/platform/calendar/deadlines.py            DeadlineKind, DeadlineRule, due_at, MEXICO_CITY
src/xolotl/platform/units/__init__.py                re-exports
src/xolotl/platform/units/models.py                  UnitValueRow
src/xolotl/platform/units/rules.py                   ConversionRule
src/xolotl/platform/units/quantity.py                Quantity
src/xolotl/platform/units/errors.py                  UnitValueMissingError, UnitSourceError, UnverifiedValueError (A2), UnitSourceAuthError (A5)
src/xolotl/platform/units/catalogue.py               UnitSpec, UNITS, UNIT_OF
src/xolotl/platform/units/sources.py                 UnitValue, UnitValueSource, read_seed_file, SeedFileSource
src/xolotl/platform/units/store.py                   upsert_value, current_row, history, latest_valid_from, unit_summaries
src/xolotl/platform/units/service.py                 Conversion, resolve_date, UnitService
src/xolotl/platform/units/banxico.py                 parse_series, BanxicoSieSource
src/xolotl/platform/units/jobs.py                    UNITS_FETCH_KIND, FetchReport, fetch_missing, UnitsFetchJob, register_units
src/xolotl/platform/policy/__init__.py               re-exports
src/xolotl/platform/policy/models.py                 PolicyPack, PolicyPackVersion, TenantPackAssignment, TenantOverrideVersion, PolicySnapshot
src/xolotl/platform/policy/errors.py                 PackValidationError, PackImmutableError, PolicyLooseningError
src/xolotl/platform/policy/schema.py                 Provenanced … PackConfig, PolicyBody, unverified_paths
src/xolotl/platform/policy/loader.py                 load_pack, content_hash, install_pack, pack_files
src/xolotl/platform/policy/durations.py              iso_duration_days, add_iso_duration
src/xolotl/platform/policy/overrides.py              TenantOverride (+ parts), validate_against
src/xolotl/platform/policy/resolver.py               EffectivePolicy, merge_packs, apply_override, effective_policy
src/xolotl/platform/policy/snapshots.py              PolicySnapshotContent, snapshot_policy, get_snapshot
src/xolotl/platform/policy/assignments.py            assignments_on, assignments_in_force, assign_pack, create_override
src/xolotl/modules/tenants/models.py                 + TenantRegulatoryProfile, TenantBranch
src/xolotl/modules/tenants/schemas_policy.py         request/response models of the new routes
src/xolotl/modules/tenants/policy_service.py         effective_with_conversions, regulatory profile, branches
src/xolotl/modules/tenants/routes_policy.py          /policy/*, /tenants/me/regulatory-profile, /tenants/me/branches
src/xolotl/modules/tenants/routes_units.py           /units, /units/{code}, /units/{code}/values, /calendar/holidays, /calendar/due
src/xolotl/modules/tenants/service.py                schedule_platform_jobs + units.fetch
src/xolotl/app.py                                    register_units(...), the two routers
src/xolotl/cli.py                                    packs_app, units_app, holidays_app
tests/platform/test_config.py (+), tests/platform/test_text.py, tests/test_render_blueprint.py (+), tests/test_policy_packs_tree.py
tests/platform/test_migration_0006.py, test_migration_chain.py, test_migration_0005.py (edited)
tests/isolation/factories.py (+5)
tests/platform/test_calendar.py, tests/golden/calendar/{business_days,deadlines}.yaml
tests/platform/test_units.py, tests/platform/units_support.py, tests/golden/units/{values,conversions}.yaml
tests/platform/test_units_banxico.py, tests/platform/test_units_job.py, tests/platform/test_worker.py (+), tests/modules/tenants/test_kernel_events.py (+)
tests/platform/test_policy_schema.py, tests/golden/__init__.py, tests/golden/packs/{__init__.py,hashes.yaml,test_packs_load.py}
tests/platform/test_policy_service.py, tests/platform/policy_support.py, tests/golden/packs/{effective.yaml,test_effective_goldens.py}
tests/platform/test_errors.py (+)
tests/modules/tenants/policy_support.py, test_policy_routes.py, test_units_routes.py, test_regulatory_profile_routes.py
tests/test_cli.py (+ packs, units, holidays)
console/src/api/schema.d.ts (regenerated), console/src/api/types.ts (+)
console/src/features/policy/{PolicyPage.tsx,queries.ts,PolicyPage.test.tsx,provenance.ts,provenance.test.ts}   A9
console/src/features/units/{UnitsPage.tsx,queries.ts,UnitsPage.test.tsx}                                       A10
console/src/features/calendar/{CalendarPage.tsx,queries.ts,deadline-form.ts,deadline-form.test.ts,CalendarPage.test.tsx}   A10
console/src/components/layout/nav.ts, console/src/App.tsx, console/src/i18n/{es-MX.json,en.json}   A9, then A10
console/src/i18n/catalogue.test.ts, console/e2e/smoke.spec.ts (+ one step)   A9
```

---

### Task A1: Dependencies, settings, the data directory in the image

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add "pyyaml>=6.0.3"` then `uv remove --group dev pyyaml`; `types-pyyaml` stays in `dev`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (`## Configuration` table), `Dockerfile`, `render.yaml`
- Create: `src/xolotl/platform/text.py` (`RFC_SHAPE`, `mask_tail`), `policy-packs/_jurisdictions.yaml`
- Test: `tests/platform/test_config.py` (append), `tests/test_render_blueprint.py` (append), `tests/test_policy_packs_tree.py`, `tests/platform/test_text.py`

**Interfaces:**
- Consumes: `Settings`, `make_settings`, `_prod(monkeypatch)` and `ENV_EXAMPLE` from `tests/platform/test_config.py`; `web_service()` and the COPY-source parser of `tests/test_render_blueprint.py`.
- Produces: `Settings.packs_dir: Path = Path("./policy-packs")`, `Settings.allow_unverified_pack_values: bool = False`, `Settings.banxico_token: SecretStr | None = None`, `Settings.units_fetch_cron: str = "0 18 * * 1-5"`; validators `_check_cron` (`@field_validator("units_fetch_cron")` over `@classmethod`, `def _check_cron(cls, value: str, info: ValidationInfo) -> str`: `croniter.croniter.is_valid(value)` else `ValueError(f"XOLOTL_{info.field_name.upper()} must be a five-field cron expression")`; plan 2c's C2 adds `"retention_purge_cron"` to that decorator, so the name and the message stay field-agnostic) and `_refuse_unverified_pack_values_in_prod` (`model_validator(mode="after")`: `env == "prod" and allow_unverified_pack_values` → `ValueError("XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES must be false when XOLOTL_ENV=prod")`); the image's `ENV XOLOTL_PACKS_DIR=/app/policy-packs`; the shared text helpers (exact):

```python
# platform/text.py — A8 imports both; plan 2b's B4a imports RFC_SHAPE, plan 2c's mask_identifier delegates to mask_tail
RFC_SHAPE: re.Pattern[str] = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$")   # shape only; the check digit is plan 2c's validator
def mask_tail(value: str, *, keep: int = 4) -> str   # "****" + value[-keep:]; "****" when len(value) <= keep
```

`policy-packs/_jurisdictions.yaml` (seed for SP-07's risk factors; nothing in SP-02 reads it beyond the parse test):

```yaml
# FATF lists, ISO 3166-1 alpha-2. Copy both lists from fatf-gafi.org on the day you
# write this file and put that day in as_of; the grey list changes every plenary.
as_of: 2026-10-18
source: "FATF, High-risk jurisdictions subject to a call for action; Jurisdictions under increased monitoring (fatf-gafi.org)"
confidence: UNVERIFIED
call_for_action: [KP, IR, MM]
increased_monitoring: []   # the implementer fills this from the FATF page as of `as_of`
```

- [ ] **Step 1: Write the failing tests.** Append to `tests/platform/test_config.py`:

```python
def test_sp02_settings_have_their_defaults():
    s = make_settings()
    assert s.packs_dir == Path("./policy-packs")
    assert s.allow_unverified_pack_values is False
    assert s.banxico_token is None
    assert s.units_fetch_cron == "0 18 * * 1-5"


def test_sp02_settings_read_their_variables(monkeypatch):
    # Settings ignores names it does not know, so setting the variable and seeing the
    # value change proves the field reads XOLOTL_...
    monkeypatch.setenv("XOLOTL_PACKS_DIR", "/srv/packs")
    monkeypatch.setenv("XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES", "true")
    monkeypatch.setenv("XOLOTL_BANXICO_TOKEN", "tok-123")
    monkeypatch.setenv("XOLOTL_UNITS_FETCH_CRON", "30 17 * * 1-5")
    s = make_settings()
    assert s.packs_dir == Path("/srv/packs")
    assert s.allow_unverified_pack_values is True
    assert s.banxico_token is not None
    assert s.banxico_token.get_secret_value() == "tok-123"
    assert "tok-123" not in repr(s)
    assert s.units_fetch_cron == "30 17 * * 1-5"


def test_a_bad_units_fetch_cron_is_refused(monkeypatch):
    monkeypatch.setenv("XOLOTL_UNITS_FETCH_CRON", "every day")
    with pytest.raises(ValidationError, match="UNITS_FETCH_CRON"):
        _ = make_settings()


def test_prod_refuses_unverified_pack_values(monkeypatch):
    _prod(monkeypatch)
    monkeypatch.setenv("XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES", "true")
    with pytest.raises(ValidationError, match="ALLOW_UNVERIFIED_PACK_VALUES"):
        _ = make_settings()
```

and extend `test_env_example_is_a_valid_configuration`'s set to `{"master_key", "object_store", "object_store_root", "packs_dir", "allow_unverified_pack_values", "units_fetch_cron"} <= s.model_fields_set`. Append to `tests/test_render_blueprint.py`:

```python
def test_the_image_carries_the_policy_packs():
    assert "policy-packs" in copy_sources()          # the module's COPY-source parser; name it so if it is inline today
    assert "policy-packs/**" in web_service()["buildFilter"]["paths"]
```

(`test_the_build_filter_covers_everything_the_image_copies` then also covers the new COPY line.) Create `tests/test_policy_packs_tree.py`:

```python
import yaml
from pathlib import Path

PACKS = Path(__file__).resolve().parents[1] / "policy-packs"


def test_the_jurisdictions_seed_parses():
    data = yaml.safe_load((PACKS / "_jurisdictions.yaml").read_text(encoding="utf-8"))
    assert data["confidence"] in {"VERIFIED", "UNVERIFIED"}
    assert data["call_for_action"] == ["KP", "IR", "MM"]
    assert all(len(code) == 2 and code.isupper() for code in data["increased_monitoring"])
```

Create `tests/platform/test_text.py` (no database; the three cases plan 2c's `test_masking.py` pins through `mask_identifier` are among them):

```python
from xolotl.platform.text import RFC_SHAPE, mask_tail


def test_rfc_shape_accepts_both_lengths_and_refuses_the_rest():
    assert RFC_SHAPE.fullmatch("ACM010101AB1") is not None  # persona moral, three letters
    assert RFC_SHAPE.fullmatch("PEGA760510XY0") is not None  # persona física, four letters
    assert RFC_SHAPE.fullmatch("Ñ&A010101AB1") is not None
    assert RFC_SHAPE.fullmatch("acm010101ab1") is None  # callers upper-case first
    assert RFC_SHAPE.fullmatch("ACM0101011") is None


def test_mask_tail_keeps_the_last_four_or_hides_everything():
    assert mask_tail("PEGA760510XY0") == "****0XY0"
    assert mask_tail("ana@acme.mx") == "****e.mx"
    assert mask_tail("1234") == "****"
    assert mask_tail("") == "****"
    assert mask_tail("ACM010101AB1", keep=3) == "****AB1"
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_config.py tests/platform/test_text.py tests/test_render_blueprint.py tests/test_policy_packs_tree.py -q --no-cov` → FAIL (`packs_dir` missing; `xolotl.platform.text` missing; `policy-packs` not copied; file missing).
- [ ] **Step 3: Implement.** Dependencies as above. In `config.py` add the four fields after `webhook_timeout_seconds` with a one-line comment each (`# Where the YAML packs, unit seeds and holidays live (the image copies them to /app/policy-packs).`, `# Loader switch for UNVERIFIED pack values; never true in prod.`, `# Banxico SIE token; unset = demo mode over the seed files, which the source refuses in prod.`, `# Daily units fetch, five-field cron, evaluated in America/Mexico_City.`), the two validators (`_check_cron` as the Interfaces block spells it, `from pydantic import ValidationInfo`; `ValidationInfo.field_name` is typed `str | None`, so build the message from `str(info.field_name).upper()` or narrow it first: the types gate refuses `.upper()` on an optional and ruff S101 refuses `assert` in `src`), `from croniter import croniter`. `.env.example` gains `XOLOTL_PACKS_DIR=./policy-packs`, `XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES=false`, `# XOLOTL_BANXICO_TOKEN=` (commented, with the comment "Banxico SIE token; leave unset for demo mode") and `XOLOTL_UNITS_FETCH_CRON=0 18 * * 1-5`; README rows `| XOLOTL_PACKS_DIR | ./policy-packs | pack, unit seed and holiday sources |`, `| XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES | false | lets `packs load` accept UNVERIFIED values; refused in prod |`, `| XOLOTL_BANXICO_TOKEN | unset | Banxico SIE token; unset runs the fetcher in demo mode from the seed files, refused when XOLOTL_ENV=prod |`, `| XOLOTL_UNITS_FETCH_CRON | 0 18 * * 1-5 | daily UDI and FIX fetch, America/Mexico_City |`. Dockerfile: `COPY policy-packs ./policy-packs` right after `COPY src ./src` and `ENV XOLOTL_PACKS_DIR=/app/policy-packs` next to `ENV XOLOTL_STATIC_DIR`; `render.yaml`: `- policy-packs/**` under `buildFilter.paths`. Create the jurisdictions file and `src/xolotl/platform/text.py` as the Interfaces block spells it (`import re`; a one-line module docstring; `mask_tail` returns `"****" + value[-keep:]` when `len(value) > keep`, else `"****"`).
- [ ] **Step 4: Run to verify pass.** The four test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `build(platform): pyyaml at runtime, SP-02 settings, the text helpers and the policy-packs directory in the image`.

---

### Task A2: Migration 0006, models, enums, factories and the chain tests

**Files:**
- Create: `src/xolotl/migrations/versions/0006_policy_units_calendar.py`, `src/xolotl/platform/policy/__init__.py`, `src/xolotl/platform/policy/models.py`, `src/xolotl/platform/units/__init__.py`, `src/xolotl/platform/units/models.py`, `src/xolotl/platform/units/rules.py`, `src/xolotl/platform/units/quantity.py`, `src/xolotl/platform/units/errors.py`, `src/xolotl/platform/calendar/__init__.py`, `src/xolotl/platform/calendar/models.py`
- Modify: `src/xolotl/platform/enums.py` (`Confidence`, `HolidayScope`, `BranchStatus`), `src/xolotl/platform/tenancy/registry.py`, `src/xolotl/models.py`, `src/xolotl/modules/tenants/models.py` (`TenantRegulatoryProfile`, `TenantBranch`), `tests/isolation/factories.py`, `tests/platform/test_migration_0005.py` (drop the `get_heads()` line of `test_0005_follows_0004_and_is_the_only_head`, rename it `test_0005_follows_0004`), `tests/platform/test_errors.py` (three rows)
- Test: `tests/platform/test_migration_0006.py`, `tests/platform/test_migration_chain.py`

**Interfaces:**
- Consumes: `Base`, `TenantTableMixin`, `tenant_table_args`, `tenant_fk`, `enable_rls`, `grant_shared`, `enum_column`, `uuid7`, `EncryptedStr`, `APP_ROLE`, `get_admin_engine`, `register_isolation_factory`, `_staff_user` (factories), `get_key_provider().create_tenant_keys`, `encrypt`; `XolotlError`, `ValidationFailedError` (`xolotl.platform.errors`).
- Produces (exact):

```python
# platform/enums.py
class Confidence(StrEnum): VERIFIED = "VERIFIED"; UNVERIFIED = "UNVERIFIED"
class HolidayScope(StrEnum): NATIONAL = "NATIONAL"; CNBV = "CNBV"; SAT = "SAT"; BANXICO = "BANXICO"
class BranchStatus(StrEnum): ACTIVE = "ACTIVE"; CLOSED = "CLOSED"

# platform/units/rules.py
class ConversionRule(StrEnum):
    SAME_DAY = "SAME_DAY"; PRIOR_BANKING_DAY = "PRIOR_BANKING_DAY"; LAST_DAY_PREV_MONTH = "LAST_DAY_PREV_MONTH"
    PREV_MONTH_AVG = "PREV_MONTH_AVG"; SEMIANNUAL_SNAPSHOT = "SEMIANNUAL_SNAPSHOT"

# platform/units/quantity.py
class Quantity(BaseModel):            # frozen, extra="forbid"
    amount: Decimal                   # > 0 (Field(gt=0))
    unit: Literal["UDI", "UMA", "USD", "MXN"]

# platform/units/errors.py (created here so Tasks A4 and A6 both only import them)
class UnitValueMissingError(XolotlError):     status = 503; default_code = "UNIT_VALUE_MISSING"; default_detail = "no unit value is available for that date"
class UnitSourceError(XolotlError):           status = 503; default_code = "UNIT_SOURCE_UNAVAILABLE"; default_detail = "the unit value source is unavailable"
class UnverifiedValueError(ValidationFailedError):  default_code = "UNVERIFIED_VALUE"; default_detail = "the file holds UNVERIFIED values"

# platform/policy/models.py (shared: subclass Base only)
class PolicyPack(Base):            __tablename__ = "policy_pack"
    id: Mapped[UUID] (Uuid, primary_key=True, default=uuid7); code: Mapped[str] (Text, unique=True)
    country_code: Mapped[str] (String(2)); sector: Mapped[str] (Text); licence: Mapped[str] (Text); title: Mapped[str] (Text)
class PolicyPackVersion(Base):     __tablename__ = "policy_pack_version"; __table_args__ = (UniqueConstraint("pack_id", "version"),)
    id (Uuid pk default uuid7); pack_id: Mapped[UUID] (Uuid, ForeignKey("policy_pack.id")); version: Mapped[int] (Integer)
    effective_from: Mapped[date] (Date); effective_to: Mapped[date | None] (Date); config: Mapped[dict[str, JsonValue]] (JSONB)
    content_hash: Mapped[bytes] (LargeBinary); loaded_at: Mapped[datetime] (DateTime(timezone=True), server_default=text("now()"))
    loaded_by: Mapped[str] (Text); source_path: Mapped[str] (Text)
class TenantPackAssignment(TenantTableMixin, Base):   __tablename__ = "tenant_pack_assignment"
    __table_args__ = tenant_table_args("tenant_pack_assignment", tenant_fk("approved_by", "staff_user"))
    pack_version_id: Mapped[UUID] (Uuid, ForeignKey("policy_pack_version.id")); product: Mapped[str | None] (Text)
    effective_from: Mapped[date]; effective_to: Mapped[date | None]; approved_by: Mapped[UUID] (Uuid); created_at (now())
class TenantOverrideVersion(TenantTableMixin, Base):  __tablename__ = "tenant_override_version"
    __table_args__ = tenant_table_args("tenant_override_version", tenant_fk("approved_by", "staff_user"), UniqueConstraint("tenant_id", "version"))
    version: Mapped[int]; overrides: Mapped[dict[str, JsonValue]] (JSONB); effective_from: Mapped[date]; approved_by: Mapped[UUID]; created_at
class PolicySnapshot(TenantTableMixin, Base):         __tablename__ = "policy_snapshot"
    __table_args__ = tenant_table_args("policy_snapshot", UniqueConstraint("tenant_id", "content_hash"))
    content_hash: Mapped[bytes] (LargeBinary); content: Mapped[dict[str, JsonValue]] (JSONB); created_at

# platform/units/models.py (shared)
class UnitValueRow(Base):          __tablename__ = "unit_value"
    __table_args__ = (UniqueConstraint("unit_code", "valid_from", "fetched_at"),
                      Index("ix_unit_value_lookup", "unit_code", "valid_from", postgresql_where=text("superseded_by IS NULL")))
    id: Mapped[int] (BigInteger, Identity(), primary_key=True); unit_code: Mapped[str] (Text); valid_from: Mapped[date]; valid_to: Mapped[date | None]
    value: Mapped[Decimal] (Numeric(20, 8)); quote_ccy: Mapped[str] (String(3)); source: Mapped[str] (Text)
    fetched_at: Mapped[datetime] (DateTime(timezone=True)); superseded_by: Mapped[int | None] (BigInteger, ForeignKey("unit_value.id"))

# platform/calendar/models.py (shared)
class Holiday(Base):               __tablename__ = "holiday"
    country_code: Mapped[str] (String(2), primary_key=True); scope: Mapped[HolidayScope] (enum_column(HolidayScope), primary_key=True)
    day: Mapped[date] (Date, primary_key=True); name: Mapped[str] (Text); source: Mapped[str] (Text)

# modules/tenants/models.py
class TenantRegulatoryProfile(TenantTableMixin, Base):   __tablename__ = "tenant_regulatory_profile"
    __table_args__ = tenant_table_args("tenant_regulatory_profile", UniqueConstraint("tenant_id"))
    rfc: Mapped[Encrypted[str]] (EncryptedStr); legal_name: Mapped[str] (Text); casfim_supervisor: Mapped[str | None]; casfim_entity_key: Mapped[str | None]
    spplld_activity_key: Mapped[str | None]; platform_domain: Mapped[str | None]
    entidad_colegiada: Mapped[bool] (Boolean, default=False, server_default=text("false")); compliance_officer_user_id: Mapped[UUID | None] (Uuid)
    created_at (now()); updated_at: Mapped[datetime] (DateTime(timezone=True), server_default=text("now()"), onupdate=clock.now)
class TenantBranch(TenantTableMixin, Base):              __tablename__ = "tenant_branch"
    __table_args__ = tenant_table_args("tenant_branch", UniqueConstraint("tenant_id", "code"))
    code: Mapped[str]; name: Mapped[str]; address: Mapped[Encrypted[str]] (EncryptedStr); state_code: Mapped[str]; municipality_code: Mapped[str]
    status: Mapped[BranchStatus] (enum_column(BranchStatus), default=BranchStatus.ACTIVE, server_default="ACTIVE"); created_at (now())
```

Migration `upgrade()` order: `policy_pack`, `policy_pack_version`, `unit_value`, `holiday` (each `_ = op.create_table(...)` then `grant_shared("<table>")`; after `grant_shared("unit_value")` add `op.execute(f"GRANT INSERT, UPDATE ON unit_value TO {APP_ROLE}")` with `from xolotl.platform.db import APP_ROLE`; `op.create_index("ix_unit_value_lookup", "unit_value", ["unit_code", "valid_from"], postgresql_where=sa.text("superseded_by IS NULL"))`), then `tenant_pack_assignment`, `tenant_override_version`, `policy_snapshot`, `tenant_regulatory_profile`, `tenant_branch` (each followed by `enable_rls("<table>")`). Constraint names, exactly: `pk_policy_pack`, `uq_policy_pack_code`; `pk_policy_pack_version`, `uq_policy_pack_version_pack_id_version`, `fk_policy_pack_version_pack_id_policy_pack`; `pk_unit_value`, `uq_unit_value_unit_code_valid_from_fetched_at`, `fk_unit_value_superseded_by_unit_value`; `pk_holiday`; `pk_tenant_pack_assignment`, `fk_tenant_pack_assignment_tenant_id_approved_by_staff_user`, `fk_tenant_pack_assignment_pack_version_id_policy_pack_version`; `pk_tenant_override_version`, `uq_tenant_override_version_tenant_id_version`, `fk_tenant_override_version_tenant_id_approved_by_staff_user`; `pk_policy_snapshot`, `uq_policy_snapshot_tenant_id_content_hash`; `pk_tenant_regulatory_profile`, `uq_tenant_regulatory_profile_tenant_id`; `pk_tenant_branch`, `uq_tenant_branch_tenant_id_code`. Column types exactly as the models block above declares them (`tests/platform/test_schema_sync.py` compares types, nullability and server defaults: enum columns `sa.String(length=32)`, `status` with `server_default="ACTIVE"`, JSON `postgresql.JSONB()`, encrypted `sa.LargeBinary()`, `sa.Numeric(precision=20, scale=8)`, `sa.Date()`, `sa.DateTime(timezone=True)` with `server_default=sa.text("now()")`). `downgrade()` drops the nine tables in reverse order. Registry: `SHARED_TABLES = frozenset({"alembic_version", "api_key_lookup", "holiday", "policy_pack", "policy_pack_version", "unit_value"})`. `src/xolotl/models.py`: import `calendar_models`, `policy_models`, `units_models`, sorted `__all__`. Package `__init__`s export the models; `units/__init__.py` also exports `ConversionRule`, `Quantity`, `UnitValueMissingError`, `UnitSourceError` and `UnverifiedValueError` (later tasks add their functions).

Factories (`tests/isolation/factories.py`): `_pack_version_id() -> UUID` inserts a `PolicyPack` (`code=f"MX/T-{pack_id.hex[:6]}"`, `country_code="MX"`, `sector="T"`, `licence=pack_id.hex[:6]`, `title="t"`) and a `PolicyPackVersion` (`version=1`, `effective_from=date(2026, 1, 1)`, `config={}`, `content_hash=b"\x00" * 32`, `loaded_by="harness"`, `source_path="harness"`) through `get_admin_engine().begin()` and returns the version id; `tenant_pack_assignment` (`_staff_user` for `approved_by`, `effective_from=date(2026, 1, 1)`); `tenant_override_version` (`version=1`, `overrides={}`); `policy_snapshot` (`content_hash=b"\x01" * 32`, `content={"on": "2026-01-01"}`); `tenant_regulatory_profile` (`create_tenant_keys` first, `rfc=encrypt("ABC010101AB1", tenant_id=tenant_id, session=session)`, `legal_name="Acme SA de CV"`); `tenant_branch` (`create_tenant_keys`, `code="MAT"`, `name="Matriz"`, `address=encrypt("Av. Reforma 1", ...)`, `state_code="09"`, `municipality_code="015"`). Register all five in `register_all()`.

- [ ] **Step 1: Write the failing tests.** `tests/platform/test_migration_chain.py` (plans 2b and 2c append their revision to `CHAIN`):

```python
from alembic.script import ScriptDirectory

from xolotl.platform import migrate

CHAIN = [
    "0001_kernel",
    "0002_events_jobs_workflows",
    "0003_tenant_key",
    "0004_encrypt_totp",
    "0005_encrypt_idempotency",
    "0006_policy_units_calendar",
]


def test_the_revisions_form_one_chain_with_one_head():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_heads() == [CHAIN[-1]]
    assert [rev.revision for rev in script.walk_revisions()] == list(reversed(CHAIN))
```

`tests/platform/test_migration_0006.py` (copy the `database` fixture and `go_down` of `test_migration_0005.py`):

```python
TABLES = {"policy_pack", "policy_pack_version", "unit_value", "holiday", "tenant_pack_assignment",
          "tenant_override_version", "policy_snapshot", "tenant_regulatory_profile", "tenant_branch"}


def test_0006_follows_0005():
    revision = ScriptDirectory.from_config(migrate.alembic_config()).get_revision("0006_policy_units_calendar")
    assert revision is not None
    assert revision.down_revision == "0005_encrypt_idempotency"


def test_0006_round_trips(database: Engine):
    go_down("0005_encrypt_idempotency")
    with database.connect() as conn:
        assert TABLES.isdisjoint(set(inspect(conn).get_table_names()))
    migrate.upgrade()
    with database.connect() as conn:
        assert TABLES <= set(inspect(conn).get_table_names())


def test_0006_grants_follow_the_plan(database: Engine):
    # has_table_privilege, as test_migration_0005.py checks idempotency_key: it answers for
    # any role, where information_schema.role_table_grants lists only the caller's own grants.
    def privileges(table: str) -> tuple[bool, bool, bool, bool]:  # SELECT, INSERT, UPDATE, DELETE
        with database.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT has_table_privilege(:app, :table, 'SELECT'), "
                    "has_table_privilege(:app, :table, 'INSERT'), "
                    "has_table_privilege(:app, :table, 'UPDATE'), "
                    "has_table_privilege(:app, :table, 'DELETE')"
                ),
                {"app": APP_ROLE, "table": table},
            ).one()
        return tuple(row)

    assert privileges("unit_value") == (True, True, True, False)
    assert privileges("policy_pack") == (True, False, False, False)
    assert privileges("policy_pack_version") == (True, False, False, False)
    assert privileges("holiday") == (True, False, False, False)
```

`tests/platform/test_errors.py`: add `(UnitValueMissingError, 503, "UNIT_VALUE_MISSING")`, `(UnitSourceError, 503, "UNIT_SOURCE_UNAVAILABLE")` and `(UnverifiedValueError, 422, "UNVERIFIED_VALUE")` to the parametrize list of `test_error_status_and_default_code`. The existing gates are the rest: `tests/platform/test_schema_sync.py`, `test_alembic_env.py`, `tests/isolation` (`test_every_tenant_table_has_a_factory` names the five until the factories exist).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_migration_chain.py tests/platform/test_migration_0006.py tests/platform/test_errors.py tests/platform/test_schema_sync.py tests/isolation -q --no-cov` → FAIL (no revision `0006…`; `test_migration_0005` still pins the head; `xolotl.platform.units.errors` missing).
- [ ] **Step 3: Implement** the enums, the three model modules, `units/errors.py`, `units/rules.py`, `units/quantity.py`, the tenants models, the migration, the registry, `models.py`, the factories, the 0005 test edit.
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/platform/test_migration_chain.py tests/platform/test_migration_0006.py tests/platform/test_migration_0005.py tests/platform/test_migration_0004.py tests/platform/test_errors.py tests/platform/test_schema_sync.py tests/platform/test_alembic_env.py tests/platform/test_ddl.py tests/isolation -q --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(platform): migration 0006 with the policy, units, calendar, regulatory-profile and branch tables`.

---

### Task A3: Calendar — holidays file, `holidays seed`, business-day helpers, `DeadlineRule`, `due_at`, goldens

**Files:**
- Create: `policy-packs/_holidays/MX.yaml`, `src/xolotl/platform/calendar/holidays.py`, `src/xolotl/platform/calendar/business.py`, `src/xolotl/platform/calendar/deadlines.py`
- Modify: `src/xolotl/platform/calendar/__init__.py` (exports), `src/xolotl/cli.py` (`holidays_app`)
- Test: `tests/platform/test_calendar.py`, `tests/golden/calendar/business_days.yaml`, `tests/golden/calendar/deadlines.yaml`, `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `Holiday`, `HolidayScope` (A2), `get_settings().packs_dir` (A1), `ValidationFailedError`, `get_admin_engine`, `tenant_session`, `TenantContext.platform`, `_cli_actor`, `_cli_errors`.
- Produces:

```python
# holidays.py
@dataclass(frozen=True, slots=True)
class HolidayRow: country_code: str; scope: HolidayScope; day: date; name: str; source: str; verify: bool
@dataclass(frozen=True, slots=True)
class Holidays: country: str; scope: HolidayScope; days: frozenset[date]
def read_holiday_file(path: Path) -> list[HolidayRow]            # ValidationFailedError on a missing or malformed file: OSError and yaml.YAMLError are caught and re-raised as ValidationFailedError(f"{path}: {exc}"), so the CLI prints an error line, never a traceback
def holidays_from_rows(rows: Iterable[HolidayRow], *, country: str, scope: HolidayScope) -> Holidays
def load_holidays(session: Session, *, country: str = "MX", scope: HolidayScope = HolidayScope.NATIONAL) -> Holidays
def seed_holidays(session: Session, rows: Iterable[HolidayRow]) -> int   # upsert by (country_code, scope, day) with insert(...).on_conflict_do_update; returns len(rows); owner session

# business.py (pure)
def is_business_day(day: date, *, holidays: Holidays) -> bool              # weekday < 5 and day not in holidays.days
def next_business_day(day: date, *, holidays: Holidays) -> date            # first business day on or after day
def prev_business_day(day: date, *, holidays: Holidays) -> date            # last business day strictly before day
def add_business_days(day: date, n: int, *, holidays: Holidays) -> date    # n may be negative; 0 returns day
def nth_business_day_of_month(year: int, month: int, n: int, *, holidays: Holidays) -> date   # ValueError when n < 1 or past the month's count

# deadlines.py
MEXICO_CITY = "America/Mexico_City"
END_OF_DAY = time(23, 59, 59)
class DeadlineKind(StrEnum): BUSINESS_DAYS_AFTER, NTH_BUSINESS_DAY_OF, DAY_OF_NEXT_MONTH, CALENDAR_DAYS_AFTER, HOURS_AFTER, DATE_RANGE, FIXED
class DeadlineRule(BaseModel):     # frozen, extra="forbid"
    kind: DeadlineKind
    params: dict[str, JsonValue]
    tz: str = MEXICO_CITY           # must be an IANA name: a field validator calls ZoneInfo(tz) and turns zoneinfo.ZoneInfoNotFoundError (a KeyError subclass, which pydantic would let escape) into ValueError(f"unknown time zone {tz}")
def due_at(rule: DeadlineRule, anchor: datetime, *, holidays: Holidays) -> datetime
```

Params per kind, enforced by a `model_validator(mode="after")` (any other key, a missing key or a wrong type is a `ValueError`): `BUSINESS_DAYS_AFTER {n: int ≥ 1}`, `NTH_BUSINESS_DAY_OF {month_offset: int, n: int ≥ 1}`, `DAY_OF_NEXT_MONTH {day: int 1..28, roll: "NEXT_BUSINESS_DAY" | "PREV_BUSINESS_DAY" | "NONE"}`, `CALENDAR_DAYS_AFTER {n: int ≥ 1}`, `HOURS_AFTER {hours: int ≥ 1}`, `DATE_RANGE {start: "MM-DD", end: "MM-DD", year_offset: int}` (`start` is validated and otherwise informational), `FIXED {at: ISO-8601 datetime with offset}`; every kind except `HOURS_AFTER` and `FIXED` accepts an optional `time: "HH:MM"`. `due_at`: a naive `anchor` raises `ValueError`; `local = anchor.astimezone(ZoneInfo(rule.tz))`, `day = local.date()`; `BUSINESS_DAYS_AFTER` → `add_business_days(day, n)`; `NTH_BUSINESS_DAY_OF` → the month `month_offset` months after `day`'s month (`year, month` arithmetic, no third-party library), then `nth_business_day_of_month` (its `ValueError` becomes `ValidationFailedError`); `DAY_OF_NEXT_MONTH` → `date(next month, day)` then `next_business_day`/`prev_business_day`/none per `roll`; `CALENDAR_DAYS_AFTER` → `day + n`; `DATE_RANGE` → `date(day.year + year_offset, end.month, end.day)`; day-granular results are `datetime.combine(due_day, params.time or END_OF_DAY, tzinfo=zone)`; `HOURS_AFTER` → `local + timedelta(hours)`; `FIXED` → `datetime.fromisoformat(at).astimezone(zone)`.

`policy-packs/_holidays/MX.yaml` schema and contents. The `verify: true` flag marks a row for the product owner's compliance review and stays on the row whatever the implementer finds (the file test and the CLI test pin 108 rows, 35 of them flagged). The implementer checks each flagged row against the named publication when it is reachable and changes the date only when the publication contradicts it, noting the change in the PR and re-deriving any golden that used the date; a publication that cannot be reached (an agent without web access) leaves the row exactly as written here. The loader keeps `verify` out of the table; `holidays seed` reports how many flagged rows the file holds. Dates of the NATIONAL scope are computed from the Ley Federal del Trabajo art. 74 (I: 1 January; II: the first Monday of February; III: the third Monday of March; IV: 1 May; V: 16 September; VI: the third Monday of November; VII: 1 October every six years for the presidential inauguration, reformed in 2024 from 1 December, next on 2030-10-01, so no row in 2025–2027; VIII: federal election day, 2027-06-06, set by the electoral law; IX: 25 December); CNBV rows come from the CNBV's annual "Disposiciones de carácter general que señalan los días del año … en que las entidades financieras sujetas a su supervisión deberán cerrar sus puertas y suspender operaciones" (DOF, published in the preceding December; the 2027 list is not out yet); SAT rows from the Código Fiscal de la Federación art. 12 (its statutory list adds 5 May) plus the Resolución Miscelánea Fiscal regla 2.1.6 of each year (Semana Santa); BANXICO rows from Banxico's published calendar of "días inhábiles bancarios" (Circular 3/2012 art. 1 ties banking days to the CNBV list, so the rows coincide with CNBV's).

```yaml
country: MX
years: [2025, 2026, 2027]
scopes:
  NATIONAL:
    source: "LFT art. 74"
    days:
      - {day: 2025-01-01, name: "Año Nuevo", basis: "LFT 74 fr. I"}
      - {day: 2025-02-03, name: "Día de la Constitución (primer lunes de febrero)", basis: "LFT 74 fr. II"}
      - {day: 2025-03-17, name: "Natalicio de Benito Juárez (tercer lunes de marzo)", basis: "LFT 74 fr. III"}
      - {day: 2025-05-01, name: "Día del Trabajo", basis: "LFT 74 fr. IV"}
      - {day: 2025-09-16, name: "Independencia", basis: "LFT 74 fr. V"}
      - {day: 2025-11-17, name: "Revolución (tercer lunes de noviembre)", basis: "LFT 74 fr. VI"}
      - {day: 2025-12-25, name: "Navidad", basis: "LFT 74 fr. IX"}
      - {day: 2026-01-01, name: "Año Nuevo", basis: "LFT 74 fr. I"}
      - {day: 2026-02-02, name: "Día de la Constitución", basis: "LFT 74 fr. II"}
      - {day: 2026-03-16, name: "Natalicio de Benito Juárez", basis: "LFT 74 fr. III"}
      - {day: 2026-05-01, name: "Día del Trabajo", basis: "LFT 74 fr. IV"}
      - {day: 2026-09-16, name: "Independencia", basis: "LFT 74 fr. V"}
      - {day: 2026-11-16, name: "Revolución", basis: "LFT 74 fr. VI"}
      - {day: 2026-12-25, name: "Navidad", basis: "LFT 74 fr. IX"}
      - {day: 2027-01-01, name: "Año Nuevo", basis: "LFT 74 fr. I"}
      - {day: 2027-02-01, name: "Día de la Constitución", basis: "LFT 74 fr. II"}
      - {day: 2027-03-15, name: "Natalicio de Benito Juárez", basis: "LFT 74 fr. III"}
      - {day: 2027-05-01, name: "Día del Trabajo", basis: "LFT 74 fr. IV"}
      - {day: 2027-06-06, name: "Jornada electoral federal", basis: "LFT 74 fr. VIII; LGIPE art. 22 (primer domingo de junio)", verify: true}
      - {day: 2027-09-16, name: "Independencia", basis: "LFT 74 fr. V"}
      - {day: 2027-11-15, name: "Revolución", basis: "LFT 74 fr. VI"}
      - {day: 2027-12-25, name: "Navidad", basis: "LFT 74 fr. IX"}
  CNBV:
    source: "CNBV, disposiciones anuales de días de cierre (DOF)"
    days:
      - {day: 2025-01-01, name: "Año Nuevo", basis: "CNBV 2025"}
      - {day: 2025-02-03, name: "Día de la Constitución", basis: "CNBV 2025"}
      - {day: 2025-03-17, name: "Natalicio de Benito Juárez", basis: "CNBV 2025"}
      - {day: 2025-04-17, name: "Jueves Santo", basis: "CNBV 2025", verify: true}
      - {day: 2025-04-18, name: "Viernes Santo", basis: "CNBV 2025", verify: true}
      - {day: 2025-05-01, name: "Día del Trabajo", basis: "CNBV 2025"}
      - {day: 2025-09-16, name: "Independencia", basis: "CNBV 2025"}
      - {day: 2025-11-17, name: "Revolución", basis: "CNBV 2025"}
      - {day: 2025-12-12, name: "Día del empleado bancario", basis: "CNBV 2025", verify: true}
      - {day: 2025-12-25, name: "Navidad", basis: "CNBV 2025"}
      - {day: 2026-01-01, name: "Año Nuevo", basis: "CNBV 2026"}
      - {day: 2026-02-02, name: "Día de la Constitución", basis: "CNBV 2026"}
      - {day: 2026-03-16, name: "Natalicio de Benito Juárez", basis: "CNBV 2026"}
      - {day: 2026-04-02, name: "Jueves Santo", basis: "CNBV 2026", verify: true}
      - {day: 2026-04-03, name: "Viernes Santo", basis: "CNBV 2026", verify: true}
      - {day: 2026-05-01, name: "Día del Trabajo", basis: "CNBV 2026"}
      - {day: 2026-09-16, name: "Independencia", basis: "CNBV 2026"}
      - {day: 2026-11-02, name: "Día de Muertos", basis: "CNBV 2026", verify: true}
      - {day: 2026-11-16, name: "Revolución", basis: "CNBV 2026"}
      - {day: 2026-12-25, name: "Navidad", basis: "CNBV 2026"}
      # 2027: projected from the pattern; the CNBV publishes the list in December 2026.
      - {day: 2027-01-01, name: "Año Nuevo", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-02-01, name: "Día de la Constitución", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-03-15, name: "Natalicio de Benito Juárez", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-03-25, name: "Jueves Santo", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-03-26, name: "Viernes Santo", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-09-16, name: "Independencia", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-11-02, name: "Día de Muertos", basis: "CNBV 2027 (proyección)", verify: true}
      - {day: 2027-11-15, name: "Revolución", basis: "CNBV 2027 (proyección)", verify: true}
  SAT:
    source: "CFF art. 12; RMF regla 2.1.6"
    days:
      # CFF art. 12 (every year): 1 Jan, first Monday of Feb, third Monday of Mar, 1 and 5 May, 16 Sep,
      # third Monday of Nov, 25 Dec; the general vacation periods of regla 2.1.6 are not loaded (open question 1).
      - {day: 2025-01-01, name: "Año Nuevo", basis: "CFF 12"}
      - {day: 2025-02-03, name: "Día de la Constitución", basis: "CFF 12"}
      - {day: 2025-03-17, name: "Natalicio de Benito Juárez", basis: "CFF 12"}
      - {day: 2025-04-17, name: "Jueves Santo", basis: "RMF 2025 regla 2.1.6", verify: true}
      - {day: 2025-04-18, name: "Viernes Santo", basis: "RMF 2025 regla 2.1.6", verify: true}
      - {day: 2025-05-01, name: "Día del Trabajo", basis: "CFF 12"}
      - {day: 2025-05-05, name: "Batalla de Puebla", basis: "CFF 12"}
      - {day: 2025-09-16, name: "Independencia", basis: "CFF 12"}
      - {day: 2025-11-17, name: "Revolución", basis: "CFF 12"}
      - {day: 2025-12-25, name: "Navidad", basis: "CFF 12"}
      - {day: 2026-01-01, name: "Año Nuevo", basis: "CFF 12"}
      - {day: 2026-02-02, name: "Día de la Constitución", basis: "CFF 12"}
      - {day: 2026-03-16, name: "Natalicio de Benito Juárez", basis: "CFF 12"}
      - {day: 2026-04-02, name: "Jueves Santo", basis: "RMF 2026 regla 2.1.6", verify: true}
      - {day: 2026-04-03, name: "Viernes Santo", basis: "RMF 2026 regla 2.1.6", verify: true}
      - {day: 2026-05-01, name: "Día del Trabajo", basis: "CFF 12"}
      - {day: 2026-05-05, name: "Batalla de Puebla", basis: "CFF 12"}
      - {day: 2026-09-16, name: "Independencia", basis: "CFF 12"}
      - {day: 2026-11-16, name: "Revolución", basis: "CFF 12"}
      - {day: 2026-12-25, name: "Navidad", basis: "CFF 12"}
      - {day: 2027-01-01, name: "Año Nuevo", basis: "CFF 12"}
      - {day: 2027-02-01, name: "Día de la Constitución", basis: "CFF 12"}
      - {day: 2027-03-15, name: "Natalicio de Benito Juárez", basis: "CFF 12"}
      - {day: 2027-03-25, name: "Jueves Santo", basis: "RMF 2027 regla 2.1.6 (proyección)", verify: true}
      - {day: 2027-03-26, name: "Viernes Santo", basis: "RMF 2027 regla 2.1.6 (proyección)", verify: true}
      - {day: 2027-05-01, name: "Día del Trabajo", basis: "CFF 12"}
      - {day: 2027-05-05, name: "Batalla de Puebla", basis: "CFF 12"}
      - {day: 2027-09-16, name: "Independencia", basis: "CFF 12"}
      - {day: 2027-11-15, name: "Revolución", basis: "CFF 12"}
      - {day: 2027-12-25, name: "Navidad", basis: "CFF 12"}
  BANXICO:
    source: "Banxico, días inhábiles bancarios (Circular 3/2012 art. 1; calendario en banxico.org.mx)"
    days:   # the CNBV dates, Banxico's own publication as the basis
      - {day: 2025-01-01, name: "Año Nuevo", basis: "Banxico 2025"}
      - {day: 2025-02-03, name: "Día de la Constitución", basis: "Banxico 2025"}
      - {day: 2025-03-17, name: "Natalicio de Benito Juárez", basis: "Banxico 2025"}
      - {day: 2025-04-17, name: "Jueves Santo", basis: "Banxico 2025", verify: true}
      - {day: 2025-04-18, name: "Viernes Santo", basis: "Banxico 2025", verify: true}
      - {day: 2025-05-01, name: "Día del Trabajo", basis: "Banxico 2025"}
      - {day: 2025-09-16, name: "Independencia", basis: "Banxico 2025"}
      - {day: 2025-11-17, name: "Revolución", basis: "Banxico 2025"}
      - {day: 2025-12-12, name: "Día del empleado bancario", basis: "Banxico 2025", verify: true}
      - {day: 2025-12-25, name: "Navidad", basis: "Banxico 2025"}
      - {day: 2026-01-01, name: "Año Nuevo", basis: "Banxico 2026"}
      - {day: 2026-02-02, name: "Día de la Constitución", basis: "Banxico 2026"}
      - {day: 2026-03-16, name: "Natalicio de Benito Juárez", basis: "Banxico 2026"}
      - {day: 2026-04-02, name: "Jueves Santo", basis: "Banxico 2026", verify: true}
      - {day: 2026-04-03, name: "Viernes Santo", basis: "Banxico 2026", verify: true}
      - {day: 2026-05-01, name: "Día del Trabajo", basis: "Banxico 2026"}
      - {day: 2026-09-16, name: "Independencia", basis: "Banxico 2026"}
      - {day: 2026-11-02, name: "Día de Muertos", basis: "Banxico 2026", verify: true}
      - {day: 2026-11-16, name: "Revolución", basis: "Banxico 2026"}
      - {day: 2026-12-25, name: "Navidad", basis: "Banxico 2026"}
      # 2027: projected; Banxico publishes its calendar in December 2026.
      - {day: 2027-01-01, name: "Año Nuevo", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-02-01, name: "Día de la Constitución", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-03-15, name: "Natalicio de Benito Juárez", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-03-25, name: "Jueves Santo", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-03-26, name: "Viernes Santo", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-09-16, name: "Independencia", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-11-02, name: "Día de Muertos", basis: "Banxico 2027 (proyección)", verify: true}
      - {day: 2027-11-15, name: "Revolución", basis: "Banxico 2027 (proyección)", verify: true}
```

`HolidayRow.source` is the row's `basis`. Weekday facts the goldens rely on (checked): 2025-12-12 Friday, 2026-01-01 Thursday, 2026-04-02 Thursday, 2026-04-03 Friday, 2026-11-02 Monday, 2027-01-17 Sunday, 2025-11-17 Monday, 2026-12-12 Saturday and 2027-12-12 Sunday (hence absent from the published scopes).

CLI: `holidays_app = typer.Typer(no_args_is_help=True, help="Regulatory calendar")`, `app.add_typer(holidays_app, name="holidays")`; `holidays seed [path]` (`path: Annotated[Path | None, typer.Argument(help="Holidays file; default <packs_dir>/_holidays/MX.yaml")] = None`) reads the file, opens `tenant_session(TenantContext.platform(_cli_actor()), factory=sessionmaker(bind=get_admin_engine(), expire_on_commit=False))`, calls `seed_holidays`, prints `seeded 108 holidays for MX (NATIONAL 22, CNBV 28, SAT 30, BANXICO 28); 35 dates still marked verify` (stdout; the counts are what the file holds: 22 + 28 + 30 + 28 rows, 35 of them flagged).

- [ ] **Step 1: Write the failing tests.** `tests/golden/calendar/business_days.yaml`:

```yaml
is_business_day:
  - {day: 2026-04-02, scope: CNBV, expected: false}
  - {day: 2026-04-02, scope: NATIONAL, expected: true}
  - {day: 2025-12-12, scope: BANXICO, expected: false}
  - {day: 2025-12-12, scope: NATIONAL, expected: true}
  - {day: 2026-10-17, scope: NATIONAL, expected: false}   # Saturday
  - {day: 2026-05-05, scope: SAT, expected: false}
  - {day: 2026-05-05, scope: NATIONAL, expected: true}
add_business_days:
  - {day: 2026-04-01, n: 3, scope: CNBV, expected: 2026-04-08}
  - {day: 2026-04-01, n: 3, scope: NATIONAL, expected: 2026-04-06}
  - {day: 2025-12-31, n: 1, scope: CNBV, expected: 2026-01-02}
  - {day: 2026-01-02, n: -1, scope: BANXICO, expected: 2025-12-31}
  - {day: 2026-04-02, n: 0, scope: CNBV, expected: 2026-04-02}
nth_business_day_of_month:
  - {year: 2026, month: 1, n: 10, scope: CNBV, expected: 2026-01-15}
  - {year: 2026, month: 1, n: 1, scope: CNBV, expected: 2026-01-02}
  - {year: 2026, month: 4, n: 2, scope: CNBV, expected: 2026-04-06}
  - {year: 2026, month: 4, n: 10, scope: CNBV, expected: 2026-04-16}
  - {year: 2026, month: 4, n: 10, scope: NATIONAL, expected: 2026-04-14}
  - {year: 2026, month: 2, n: 25, scope: NATIONAL, error: ValueError}
next_business_day:
  - {day: 2027-01-17, scope: SAT, expected: 2027-01-18}
  - {day: 2025-11-17, scope: SAT, expected: 2025-11-18}
  - {day: 2026-04-02, scope: CNBV, expected: 2026-04-06}
  - {day: 2026-04-02, scope: NATIONAL, expected: 2026-04-02}
prev_business_day:
  - {day: 2025-12-15, scope: BANXICO, expected: 2025-12-11}
  - {day: 2025-12-15, scope: NATIONAL, expected: 2025-12-12}
  - {day: 2026-01-02, scope: BANXICO, expected: 2025-12-31}
```

`tests/golden/calendar/deadlines.yaml` (every `expected` is an ISO datetime with offset; `anchor` too):

```yaml
- {id: ror-q4-2025-jan-roll, rule: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}}, anchor: "2025-12-31T23:59:59-06:00", scope: CNBV, expected: "2026-01-15T23:59:59-06:00"}
- {id: ror-q1-2026-semana-santa-cnbv, rule: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}}, anchor: "2026-03-31T23:59:59-06:00", scope: CNBV, expected: "2026-04-16T23:59:59-06:00"}
- {id: ror-q1-2026-national, rule: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}}, anchor: "2026-03-31T23:59:59-06:00", scope: NATIONAL, expected: "2026-04-14T23:59:59-06:00"}
- {id: roi-3-business-days, rule: {kind: BUSINESS_DAYS_AFTER, params: {n: 3}}, anchor: "2026-04-01T10:00:00-06:00", scope: CNBV, expected: "2026-04-08T23:59:59-06:00"}
- {id: roi24, rule: {kind: HOURS_AFTER, params: {hours: 24}}, anchor: "2026-04-02T15:30:00-06:00", scope: CNBV, expected: "2026-04-03T15:30:00-06:00"}
- {id: aviso-17th-sunday-rolls, rule: {kind: DAY_OF_NEXT_MONTH, params: {day: 17, roll: NEXT_BUSINESS_DAY}}, anchor: "2026-12-31T00:00:00-06:00", scope: SAT, expected: "2027-01-18T23:59:59-06:00"}
- {id: aviso-17th-holiday-rolls, rule: {kind: DAY_OF_NEXT_MONTH, params: {day: 17, roll: NEXT_BUSINESS_DAY}}, anchor: "2025-10-31T00:00:00-06:00", scope: SAT, expected: "2025-11-18T23:59:59-06:00"}
- {id: aviso-17th-no-roll, rule: {kind: DAY_OF_NEXT_MONTH, params: {day: 17, roll: NONE}}, anchor: "2026-12-31T00:00:00-06:00", scope: SAT, expected: "2027-01-17T23:59:59-06:00"}
- {id: calendar-days, rule: {kind: CALENDAR_DAYS_AFTER, params: {n: 15}}, anchor: "2026-04-01T09:00:00-06:00", scope: NATIONAL, expected: "2026-04-16T23:59:59-06:00"}
- {id: date-range, rule: {kind: DATE_RANGE, params: {start: "01-01", end: "03-31", year_offset: 1}}, anchor: "2026-07-15T12:00:00-06:00", scope: NATIONAL, expected: "2027-03-31T23:59:59-06:00"}
- {id: fixed, rule: {kind: FIXED, params: {at: "2026-11-30T00:00:00-06:00"}}, anchor: "2026-01-01T00:00:00-06:00", scope: NATIONAL, expected: "2026-11-30T00:00:00-06:00"}
- {id: explicit-time, rule: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10, time: "17:00"}}, anchor: "2025-12-31T23:59:59-06:00", scope: CNBV, expected: "2026-01-15T17:00:00-06:00"}
- {id: utc-anchor-just-after-midnight, rule: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}}, anchor: "2026-01-01T03:00:00Z", scope: CNBV, expected: "2026-01-15T23:59:59-06:00"}
```

`tests/platform/test_calendar.py` (no database except where noted): a module fixture `ROWS = read_holiday_file(REPO / "policy-packs/_holidays/MX.yaml")` and `holidays_of(scope) = holidays_from_rows(ROWS, country="MX", scope=scope)`; `test_the_file_covers_the_four_scopes_and_three_years` (scopes == set(HolidayScope); years of every row ⊆ {2025, 2026, 2027}; `(country_code, scope, day)` unique; rows per scope `{NATIONAL: 22, CNBV: 28, SAT: 30, BANXICO: 28}`; the BANXICO days equal the CNBV days; every 2027 row of CNBV, SAT-RMF and BANXICO is flagged `verify`); `test_business_day_goldens` and `test_deadline_goldens` parametrised over the YAML (`ids=[case["id"] ...]`), the latter asserting `due_at(DeadlineRule.model_validate(case["rule"]), datetime.fromisoformat(case["anchor"]), holidays=holidays_of(scope)) == datetime.fromisoformat(case["expected"])` and `result.utcoffset() == timedelta(hours=-6)`; `test_a_naive_anchor_is_refused` (`ValueError`); `test_rule_params_are_validated` parametrised over `({kind: BUSINESS_DAYS_AFTER, params: {}}, {kind: DAY_OF_NEXT_MONTH, params: {day: 31, roll: NONE}}, {kind: FIXED, params: {at: "2026-11-30"}}, {kind: HOURS_AFTER, params: {hours: 24, time: "10:00"}}, {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}, tz: "Mars/Olympus"})` → `pydantic.ValidationError`; `test_nth_beyond_the_month_is_a_422` (`due_at` → `ValidationFailedError`); with the database: `test_seed_holidays_is_idempotent_and_load_holidays_reads_the_scope` (`admin_engine` session: `seed_holidays` twice → count unchanged, `load_holidays(session, scope=HolidayScope.CNBV).days` contains `date(2026, 4, 2)` and not `date(2026, 5, 5)`; the app-role session reads the same). `tests/test_cli.py`: `holidays seed` prints `seeded 108 holidays for MX (NATIONAL 22, CNBV 28, SAT 30, BANXICO 28); 35 dates still marked verify` and exits 0; a missing file exits 1 with `error: VALIDATION_FAILED: …`; `--help` lists `seed`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_calendar.py tests/test_cli.py -k "holidays" -q --no-cov` → FAIL (`xolotl.platform.calendar.holidays` missing).
- [ ] **Step 3: Implement** the three modules, the YAML file (complete, including the BANXICO rows the schema shows abbreviated), the CLI group, the exports (`Holidays`, `HolidayRow`, `read_holiday_file`, `holidays_from_rows`, `load_holidays`, `seed_holidays`, `is_business_day`, `next_business_day`, `prev_business_day`, `add_business_days`, `nth_business_day_of_month`, `DeadlineKind`, `DeadlineRule`, `due_at`, `MEXICO_CITY`). Check the flagged rows against their publications as the rule above says; the flags and the counts (108, 35) stay.
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/platform/test_calendar.py tests/test_cli.py -q --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(calendar): Mexico holidays 2025-2027, business-day helpers, the deadline DSL and holidays seed`.

---

### Task A4: Units — store, catalogue, seed source and seed files, `UnitService`, goldens

**Files:**
- Create: `src/xolotl/platform/units/catalogue.py`, `sources.py`, `store.py`, `service.py`, `policy-packs/_units/UMA.yaml`, `policy-packs/_units/UDI.seed.yaml`, `policy-packs/_units/USD_MXN_FIX.seed.yaml`
- Modify: `src/xolotl/platform/units/__init__.py` (exports)
- Test: `tests/platform/test_units.py`, `tests/platform/units_support.py`, `tests/golden/units/values.yaml`, `tests/golden/units/conversions.yaml`

**Interfaces:**
- Consumes: `UnitValueRow`, `ConversionRule`, `Quantity`, `Confidence`, `UnitValueMissingError`, `UnitSourceError`, `UnverifiedValueError` (A2); `Holidays`, `HolidayScope`, `load_holidays`, `prev_business_day`, `read_holiday_file`, `seed_holidays` (A3); `ValidationFailedError`; `clock.now`.
- Produces:

```python
# catalogue.py
@dataclass(frozen=True, slots=True)
class UnitSpec: code: str; quote_ccy: str; daily: bool; series_id: str | None; seed_file: str
UNITS: Mapping[str, UnitSpec] = {
    "UMA": UnitSpec("UMA", "MXN", daily=False, series_id=None, seed_file="UMA.yaml"),
    "UDI": UnitSpec("UDI", "MXN", daily=True, series_id="SP68257", seed_file="UDI.seed.yaml"),
    "USD_MXN_FIX": UnitSpec("USD_MXN_FIX", "MXN", daily=True, series_id="SF43718", seed_file="USD_MXN_FIX.seed.yaml"),
}
UNIT_OF: Mapping[str, str] = {"UDI": "UDI", "UMA": "UMA", "USD": "USD_MXN_FIX"}   # Quantity.unit -> unit_code; "MXN" is the quote currency itself
def unit_spec(unit_code: str) -> UnitSpec        # ValidationFailedError(f"unknown unit {unit_code}")

# sources.py
@dataclass(frozen=True, slots=True)
class UnitValue: unit_code: str; valid_from: date; valid_to: date | None; value: Decimal; quote_ccy: str; source: str; confidence: Confidence
class UnitValueSource(Protocol):
    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]: ...        # rows with start <= valid_from <= end, ascending
    def latest_available(self, unit_code: str) -> UnitValue | None: ...
def read_seed_file(path: Path) -> list[UnitValue]                                            # ValidationFailedError on a missing or malformed file (OSError, yaml.YAMLError and a bad shape all end as ValidationFailedError(f"{path}: ...")); daily units get valid_to = valid_from
class SeedFileSource:                                                                         # implements UnitValueSource
    def __init__(self, seed_dir: Path, *, allow_unverified: bool = False) -> None
    # reads seed_dir / UNITS[unit_code].seed_file; an UNVERIFIED row raises UnverifiedValueError(f"{unit_code} {valid_from.isoformat()}") unless allowed

# store.py
def upsert_value(session: Session, value: UnitValue, *, fetched_at: datetime) -> int | None
    # the current (superseded_by IS NULL) row with the same (unit_code, valid_from): equal value and valid_to -> None;
    # otherwise insert the new row, flush, set old.superseded_by = new.id, return new.id
def current_row(session: Session, unit_code: str, on: date) -> UnitValueRow | None
    # unit_code ==, valid_from <= on, (valid_to IS NULL OR valid_to >= on), superseded_by IS NULL; ORDER BY valid_from DESC, fetched_at DESC LIMIT 1
def history(session: Session, unit_code: str, *, start: date, end: date) -> list[UnitValueRow]   # valid_from in [start, end], superseded rows included, ORDER BY valid_from DESC, fetched_at DESC, LIMIT 400
def latest_valid_from(session: Session, unit_code: str) -> date | None                          # max(valid_from) WHERE superseded_by IS NULL
@dataclass(frozen=True, slots=True)
class UnitSummary: unit_code: str; quote_ccy: str; daily: bool; series_id: str | None; latest_valid_from: date | None; latest_fetched_at: datetime | None; latest_source: str | None
def unit_summaries(session: Session) -> list[UnitSummary]                                        # one per UNITS entry, catalogue order

# service.py
CCY_PLACES = Decimal("0.01"); UNIT_PLACES = Decimal("0.000001")   # ROUND_HALF_UP
class Conversion(BaseModel):   # frozen
    amount: Decimal; unit_value_id: int | None; rule: ConversionRule; on: date; resolved_date: date; unit: str; currency: str; rate: Decimal
def resolve_date(rule: ConversionRule, on: date, *, holidays: Holidays) -> date
    # SAME_DAY -> on; PRIOR_BANKING_DAY -> prev_business_day(on, holidays=holidays); LAST_DAY_PREV_MONTH -> on.replace(day=1) - 1 day;
    # SEMIANNUAL_SNAPSHOT -> date(on.year, 6, 30) if on > date(on.year, 6, 30) else date(on.year - 1, 12, 31); PREV_MONTH_AVG -> ValidationFailedError("PREV_MONTH_AVG is not supported in SP-02")
class UnitService:
    def __init__(self, session: Session) -> None
    def lookup(self, unit_code: str, on: date, rule: ConversionRule, *, country: str = "MX", scope: HolidayScope = HolidayScope.BANXICO) -> UnitValueRow
        # unit_spec(unit_code); holidays = load_holidays(session, country=country, scope=scope); resolved = resolve_date(...);
        # current_row(...) or raise UnitValueMissingError(f"no {unit_code} value for {resolved.isoformat()}")
    def to_unit(self, amount: Decimal, currency: str, unit: str, on: date, rule: ConversionRule) -> Conversion        # amount in currency -> unit; amount / row.value quantized to UNIT_PLACES
    def threshold_in_ccy(self, threshold: Quantity, currency: str, on: date, rule: ConversionRule) -> Conversion   # threshold.amount * row.value quantized to CCY_PLACES
    # currency must be "MXN" (ValidationFailedError("only MXN conversions are supported") otherwise); unit "MXN" is the identity:
    # Conversion(amount quantized, unit_value_id=None, resolved_date=on, rate=Decimal(1)); unit not in UNIT_OF -> ValidationFailedError
```

Seed file schema (one for the three files; `legal_basis`, `confidence`, `verified_as_of` at file level are the defaults a row may override; daily files omit `valid_to`, which `read_seed_file` sets to `valid_from`):

```yaml
# policy-packs/_units/UMA.yaml
unit_code: UMA
quote_ccy: MXN
source: INEGI_DOF
legal_basis: "Ley para Determinar el Valor de la Unidad de Medida y Actualización, arts. 4 y 5; INEGI publica el valor diario en el DOF en enero"
confidence: VERIFIED
verified_as_of: 2026-10-18
values:
  - {valid_from: 2024-02-01, valid_to: 2025-01-31, value: "108.57", legal_basis: "INEGI, DOF de enero de 2024 (completar el día de la publicación): UMA diaria 2024 = 108.57", confidence: VERIFIED, verified_as_of: 2024-01-10}
  - {valid_from: 2025-02-01, valid_to: 2026-01-31, value: "113.14", legal_basis: "INEGI, DOF de enero de 2025 (completar el día de la publicación): UMA diaria 2025 = 113.14", confidence: VERIFIED, verified_as_of: 2025-01-10}
  # The 2026 value is the figure INEGI published in January 2026 (inegi.org.mx/temas/uma and the DOF): copy it, never compute it from inflation. UNVERIFIED until the compliance review (spec §13).
  - {valid_from: 2026-02-01, valid_to: 2027-01-31, value: "<INEGI figure>", legal_basis: "INEGI, DOF de enero de 2026: UMA diaria 2026 (cita pendiente, §13)", confidence: UNVERIFIED, verified_as_of: 2026-10-18}

# policy-packs/_units/UDI.seed.yaml
unit_code: UDI
quote_ccy: MXN
source: "BANXICO_SIE:SP68257"
legal_basis: "Banxico, serie SP68257 (valor de la UDI), publicada para cada día natural"
confidence: VERIFIED
verified_as_of: <the day the values were copied from the SIE>
values:                       # one row per calendar day from 2025-12-01 to 2026-01-31 (62 rows), six decimals, copied from the SIE
  - {valid_from: 2025-12-01, value: "<SIE value>"}
  # ...

# policy-packs/_units/USD_MXN_FIX.seed.yaml
unit_code: USD_MXN_FIX
quote_ccy: MXN
source: "BANXICO_SIE:SF43718"
legal_basis: "Banxico, serie SF43718 (tipo de cambio FIX), publicada por día hábil bancario"
confidence: VERIFIED
verified_as_of: <the day the values were copied from the SIE>
values:                       # one row per banking day from 2025-12-01 to 2026-01-31, four decimals; no row on 2025-12-12, 2025-12-25, 2026-01-01 or a weekend
  - {valid_from: 2025-12-01, value: "<SIE value>"}
  # ...
```

- [ ] **Step 1: Write the failing tests.** `tests/golden/units/values.yaml` (fixture values, not published ones):

```yaml
fetched_at: "2026-01-20T12:00:00+00:00"
rows:
  - {unit_code: USD_MXN_FIX, valid_from: 2025-12-10, value: "18.1000"}
  - {unit_code: USD_MXN_FIX, valid_from: 2025-12-11, value: "18.2500"}
  - {unit_code: USD_MXN_FIX, valid_from: 2025-12-15, value: "18.4000"}
  - {unit_code: USD_MXN_FIX, valid_from: 2026-01-19, value: "18.5000"}   # used by the A8 route test (ROR on 2026-01-20)
  - {unit_code: UDI, valid_from: 2025-12-30, value: "8.500000"}
  - {unit_code: UDI, valid_from: 2025-12-31, value: "8.520000"}
  - {unit_code: UDI, valid_from: 2026-01-02, value: "8.530000"}
  - {unit_code: UDI, valid_from: 2026-01-15, value: "8.600000"}
  - {unit_code: UDI, valid_from: 2026-01-20, value: "8.610000"}   # used by the A8 route test (ITF_VA_OPS, SAME_DAY, on 2026-01-20)
  - {unit_code: UDI, valid_from: 2026-01-21, value: "8.620000"}   # and on 2026-01-21, where only the FIX is missing
  - {unit_code: UMA, valid_from: 2024-02-01, valid_to: 2025-01-31, value: "108.57"}
  - {unit_code: UMA, valid_from: 2025-02-01, valid_to: 2026-01-31, value: "113.14"}
corrections:   # inserted one hour before `fetched_at`; loading `rows` then supersedes them
  - {unit_code: USD_MXN_FIX, valid_from: 2025-12-11, value: "18.2000"}
```

`tests/golden/units/conversions.yaml`:

```yaml
- {id: fix-prior-banking-day-across-12-dec, op: threshold_in_ccy, threshold: {amount: "5000", unit: USD}, currency: MXN, on: 2025-12-15, rule: PRIOR_BANKING_DAY, expected: {amount: "91250.00", resolved_date: 2025-12-11, rate: "18.25", row: [USD_MXN_FIX, 2025-12-11]}}
- {id: fix-prior-banking-day-plain, op: threshold_in_ccy, threshold: {amount: "5000", unit: USD}, currency: MXN, on: 2025-12-16, rule: PRIOR_BANKING_DAY, expected: {amount: "92000.00", resolved_date: 2025-12-15, rate: "18.40", row: [USD_MXN_FIX, 2025-12-15]}}
- {id: fix-on-the-bank-holiday-itself, op: threshold_in_ccy, threshold: {amount: "5000", unit: USD}, currency: MXN, on: 2025-12-12, rule: PRIOR_BANKING_DAY, expected: {amount: "91250.00", resolved_date: 2025-12-11, rate: "18.25", row: [USD_MXN_FIX, 2025-12-11]}}
- {id: fix-same-day-on-the-holiday-is-missing, op: threshold_in_ccy, threshold: {amount: "5000", unit: USD}, currency: MXN, on: 2025-12-12, rule: SAME_DAY, error: {code: UNIT_VALUE_MISSING, detail: "no USD_MXN_FIX value for 2025-12-12"}}
- {id: udi-n1-monthly-ops-january, op: threshold_in_ccy, threshold: {amount: "750", unit: UDI}, currency: MXN, on: 2026-01-20, rule: LAST_DAY_PREV_MONTH, expected: {amount: "6390.00", resolved_date: 2025-12-31, rate: "8.52", row: [UDI, 2025-12-31]}}
- {id: udi-n1-balance-january, op: threshold_in_ccy, threshold: {amount: "1000", unit: UDI}, currency: MXN, on: 2026-01-20, rule: LAST_DAY_PREV_MONTH, expected: {amount: "8520.00", resolved_date: 2025-12-31, rate: "8.52", row: [UDI, 2025-12-31]}}
- {id: udi-missing-month-end-blocks, op: threshold_in_ccy, threshold: {amount: "3000", unit: UDI}, currency: MXN, on: 2026-02-05, rule: LAST_DAY_PREV_MONTH, error: {code: UNIT_VALUE_MISSING, detail: "no UDI value for 2026-01-31"}}
- {id: uma-before-the-february-change, op: threshold_in_ccy, threshold: {amount: "210", unit: UMA}, currency: MXN, on: 2025-01-31, rule: SAME_DAY, expected: {amount: "22799.70", resolved_date: 2025-01-31, rate: "108.57", row: [UMA, 2024-02-01]}}
- {id: uma-on-the-february-change, op: threshold_in_ccy, threshold: {amount: "210", unit: UMA}, currency: MXN, on: 2025-02-01, rule: SAME_DAY, expected: {amount: "23759.40", resolved_date: 2025-02-01, rate: "113.14", row: [UMA, 2025-02-01]}}
- {id: uma-fee-threshold, op: threshold_in_ccy, threshold: {amount: "4", unit: UMA}, currency: MXN, on: 2025-06-01, rule: SAME_DAY, expected: {amount: "452.56", resolved_date: 2025-06-01, rate: "113.14", row: [UMA, 2025-02-01]}}
- {id: to-unit-uma, op: to_unit, amount: "25000", currency: MXN, unit: UMA, on: 2025-03-10, rule: SAME_DAY, expected: {amount: "220.965176", resolved_date: 2025-03-10, rate: "113.14", row: [UMA, 2025-02-01]}}
- {id: to-unit-udi-month-end, op: to_unit, amount: "10000", currency: MXN, unit: UDI, on: 2026-01-20, rule: LAST_DAY_PREV_MONTH, expected: {amount: "1173.708920", resolved_date: 2025-12-31, rate: "8.52", row: [UDI, 2025-12-31]}}
- {id: to-unit-usd-via-fix, op: to_unit, amount: "91250", currency: MXN, unit: USD, on: 2025-12-15, rule: PRIOR_BANKING_DAY, expected: {amount: "5000.000000", resolved_date: 2025-12-11, rate: "18.25", row: [USD_MXN_FIX, 2025-12-11]}}
- {id: semiannual-snapshot, op: threshold_in_ccy, threshold: {amount: "750", unit: UDI}, currency: MXN, on: 2026-01-20, rule: SEMIANNUAL_SNAPSHOT, expected: {amount: "6390.00", resolved_date: 2025-12-31, rate: "8.52", row: [UDI, 2025-12-31]}}
- {id: semiannual-missing, op: threshold_in_ccy, threshold: {amount: "750", unit: UDI}, currency: MXN, on: 2025-12-31, rule: SEMIANNUAL_SNAPSHOT, error: {code: UNIT_VALUE_MISSING, detail: "no UDI value for 2025-06-30"}}
- {id: prev-month-avg-refused, op: threshold_in_ccy, threshold: {amount: "750", unit: UDI}, currency: MXN, on: 2026-01-20, rule: PREV_MONTH_AVG, error: {code: VALIDATION_FAILED}}
- {id: mxn-identity, op: threshold_in_ccy, threshold: {amount: "1000", unit: MXN}, currency: MXN, on: 2026-01-20, rule: SAME_DAY, expected: {amount: "1000.00", resolved_date: 2026-01-20, rate: "1", row: null}}
- {id: unsupported-currency, op: threshold_in_ccy, threshold: {amount: "1000", unit: UDI}, currency: USD, on: 2026-01-20, rule: SAME_DAY, error: {code: VALIDATION_FAILED}}
```

`tests/platform/units_support.py` (plain functions, reused by A8's `tests/modules/tenants/policy_support.py`): `seed_mx_holidays(admin_engine) -> None` (`read_holiday_file(REPO / "policy-packs/_holidays/MX.yaml")` then `seed_holidays` on an `admin_engine` session) and `load_unit_values(session: Session, path: Path) -> dict[tuple[str, date], int]`, which builds one `UnitValue` per row of `values.yaml` itself (`read_seed_file` reads one unit per file, so it is not used here): `valid_to = row.get("valid_to") or valid_from` when `UNITS[unit_code].daily` (UDI and FIX rows close the same day; with `valid_to` left NULL `current_row` would return the latest earlier row and `fix-same-day-on-the-holiday-is-missing`, `udi-missing-month-end-blocks`, `semiannual-missing` and the A8 `missing_units` cases would stop missing), UMA rows keep the `valid_to` they carry, `quote_ccy="MXN"`, `source="fixture"`, `confidence=Confidence.VERIFIED`; it upserts `corrections` first with `fetched_at - 1h`, then `rows` with the file's `fetched_at`, and returns `{(unit_code, valid_from): id}` of the current rows. `tests/platform/test_units.py` (database; module `pytestmark` none; a `loaded(engine, admin_engine)` fixture calls `seed_mx_holidays(admin_engine)` and then `load_unit_values` under `tenant_session(platform_ctx())` on the app engine, the grant allows it, and returns the ids). Tests: `test_conversion_goldens` parametrised over `conversions.yaml` by `id`: on `expected`, assert `conv.amount == Decimal(expected["amount"])`, `str(conv.amount) == expected["amount"]` (the quantization), `conv.resolved_date`, `conv.rate == Decimal(expected["rate"])`, `conv.unit_value_id == loaded[tuple(row)]` (or `None`), `conv.rule`, `conv.on`; on `error`, `pytest.raises(XolotlError)` with `.code` and, when given, `.detail`. `test_a_correction_supersedes_once`: the 2025-12-11 FIX has two rows in `history`, the older carries `superseded_by == loaded[("USD_MXN_FIX", date(2025, 12, 11))]`; `upsert_value` of the same value again returns `None` and adds nothing; a correction inserted with a `fetched_at` one day earlier than the current row still supersedes it and `lookup` returns the new row. `test_lookup_rejects_unknown_units` (422). `test_seed_files_have_the_pinned_shape`: `read_seed_file` on the three repository files: UMA rows `[(2024-02-01, 2025-01-31, "108.57", VERIFIED), (2025-02-01, 2026-01-31, "113.14", VERIFIED), (2026-02-01, 2027-01-31, <any>, UNVERIFIED)]`; UDI `[r.valid_from for r in rows] == [date(2025, 12, 1) + timedelta(days=i) for i in range(62)]`, every `valid_to == valid_from`, every value `> 0` with `exponent >= -6`; FIX: dates ⊆ 2025-12-01..2026-01-31, none in `{2025-12-12, 2025-12-25, 2026-01-01}`, no weekend, every value `> 0` with `exponent >= -4`; the UDI rows share one confidence and so do the FIX rows (`VERIFIED` when copied from the SIE, `UNVERIFIED` under the fallback of Step 3: the test accepts either and refuses a mix). `test_seed_source_refuses_unverified_rows`: `SeedFileSource(dir).fetch("UMA", date(2024, 1, 1), date(2027, 12, 31))` raises `UnverifiedValueError` matching `"UMA 2026-02-01"`; with `allow_unverified=True` returns 3 rows and `latest_available("UDI").valid_from == date(2026, 1, 31)`. `test_unit_summaries_report_the_latest_rows` (`UDI.latest_valid_from == date(2026, 1, 21)`, `USD_MXN_FIX.latest_valid_from == date(2026, 1, 19)`, `UMA.latest_valid_from == date(2025, 2, 1)`, every `latest_source == "fixture"`, catalogue order).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_units.py -q --no-cov` → FAIL (modules missing).
- [ ] **Step 3: Implement** the four modules, `tests/platform/units_support.py`, the three seed files (copy the UDI and FIX values from the SIE for the pinned ranges: `GET https://www.banxico.org.mx/SieAPIRest/service/v1/series/SP68257/datos/2025-12-01/2026-01-31` and `.../series/SF43718/datos/2025-12-01/2026-01-31` with a `Bmx-Token`, or the series' pages on banxico.org.mx; `verified_as_of` is the day you copied them. If the series cannot be read that day, do not invent numbers: write the rows with `confidence: UNVERIFIED`, a file-level `legal_basis` of `"valores pendientes de copiar de la serie <id> de la SIE; sin acceso a la SIE al escribir el archivo"` and the same `verified_as_of`, say so in the PR, and leave `units seed` to refuse them without `--allow-unverified`; the product owner replaces them before the first production load), the exports (`UnitValue`, `UnitValueSource`, `SeedFileSource`, `read_seed_file`, `UnitService`, `Conversion`, `resolve_date`, `UNITS`, `UNIT_OF`, `unit_spec`, `upsert_value`, `current_row`, `history`, `latest_valid_from`, `unit_summaries`, `UnitSummary`; the errors, `ConversionRule` and `Quantity` are exported since A2).
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/platform/test_units.py -q --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(units): unit_value store with corrections, seed source and files, UnitService and conversion goldens`.

---

### Task A5: Banxico SIE source, `units seed` and `units fetch`, the `units.fetch` job, schedule and failure notification

**Files:**
- Create: `src/xolotl/platform/units/banxico.py`, `src/xolotl/platform/units/jobs.py`
- Modify: `src/xolotl/platform/units/errors.py` (`UnitSourceAuthError`), `src/xolotl/platform/units/__init__.py`, `src/xolotl/app.py` (`wire_platform`), `src/xolotl/modules/tenants/service.py` (`schedule_platform_jobs`), `src/xolotl/cli.py` (`units_app`), `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`
- Test: `tests/platform/test_units_banxico.py`, `tests/platform/test_units_job.py`, `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `UnitValue`, `UnitValueSource`, `SeedFileSource`, `UNITS`, `unit_spec`, `upsert_value`, `current_row`, `latest_valid_from` (A4); `UnitSourceError`, `UnverifiedValueError` (A2); `get_settings().env`; `register_job_handler`, `Job`, `Notifier`, `InAppNotifier`, `Role`, `safe_exception_summary`, `PostgresJobQueue.schedule`, `get_settings`, `clock.now`; `httpx`, `respx`.
- Produces:

```python
# units/errors.py (added here; units/__init__.py exports it)
class UnitSourceAuthError(UnitSourceError):   # Banxico rejected the token (HTTP 401 or 403): same 503 UNIT_SOURCE_UNAVAILABLE;
    default_detail = "the unit value source rejected the API token"   # plan 2b's B4b maps it to PermanentCallError (no retry)

# banxico.py — ported from ~/code/xolo/src/xolo/providers/banxico.py
BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1"
MAX_RANGE_DAYS = 366
TIMEOUT_SECONDS = 10.0
def parse_series(payload: bytes) -> dict[str, list[tuple[date, Decimal]]]
class BanxicoSieSource:                      # implements UnitValueSource
    def __init__(self, token: SecretStr | None, *, seed_dir: Path, env: str, client: httpx.Client | None = None) -> None
    @property
    def mode(self) -> Literal["live", "demo"]       # "live" when a token is set
    # demo mode reads SeedFileSource(seed_dir, allow_unverified=True): dev and CI only. With env == "prod" and no token, fetch and
    # latest_available raise UnitSourceError("no XOLOTL_BANXICO_TOKEN; demo mode is refused when XOLOTL_ENV=prod") (spec §3), so the
    # job notifies ADMIN and MLRO every day instead of filling the table from the committed seeds.
    def fetch(self, unit_code: str, start: date, end: date) -> list[UnitValue]
    def latest_available(self, unit_code: str) -> UnitValue | None

# jobs.py
UNITS_FETCH_KIND = "units.fetch"
FETCH_UNITS: tuple[str, ...] = ("UDI", "USD_MXN_FIX")
BACKFILL_DAYS = 31
@dataclass(frozen=True, slots=True)
class FetchReport: unit_code: str; start: date; end: date; fetched: int; inserted: int; corrected: int
def fetch_missing(session: Session, source: UnitValueSource, unit_code: str, *, start: date, end: date, now: datetime) -> FetchReport
class UnitsFetchJob:
    def __init__(self, source: UnitValueSource, *, notifier: Notifier, clock_fn: Callable[[], datetime] = clock.now) -> None
    def window(self, session: Session, unit_code: str) -> tuple[date, date] | None
    def run(self, session: Session, _job: Job) -> None
def register_units(*, source: UnitValueSource, notifier: Notifier, clock_fn: Callable[[], datetime] = clock.now) -> UnitsFetchJob
```

What carries over from Xolo and what stays behind: carry `BASE_URL`, `MAX_RANGE_DAYS`, the wire models (`_Dato(fecha, dato)`, `_Serie(idSerie → id_serie, datos=[])`, `_Bmx`, `_Response`), `parse_series` (the `","` strip, the `dd/mm/yyyy` parse, the skip of `N/E` and unparsable rows, `ValidationError` → `UnitSourceError("Banxico returned a response in an unexpected format")`), the URL shapes `series/{id}/datos/{start}/{end}` and `series/{id}/datos/oportuno`, the `Bmx-Token` header, and the error mapping (`httpx.HTTPError` → `UnitSourceError("Banxico SIE is unreachable, try again shortly")`; status 401 or 403 (a rejected token) → `UnitSourceAuthError("Banxico rejected the API token (check XOLOTL_BANXICO_TOKEN)")`, the `UnitSourceError` subclass above, which plan 2b's connector maps to a permanent error instead of retrying; any other error status, 400 included → `UnitSourceError(f"Banxico SIE returned HTTP {status}")`). Drop the `TtlCache` and `xolo.cache`, the async client (use `httpx.Client(timeout=TIMEOUT_SECONDS)`), the `Indicator` enum and `CATALOG` (TIIE and the target rate are not units; `UNITS` holds SP68257 and SF43718), the `Banxico` facade (`latest`, `history`, `convert`), `SeriesResult`, `Conversion`, `DemoBanxico` and its JSON fixture (demo mode is `SeedFileSource(seed_dir, allow_unverified=True)` over the committed seeds, allowed outside prod only), and `xolo.http`/`xolo.tiers` entirely (the MCP server's rate limiting and tiering). Live `fetch`: `unit_spec(unit_code).series_id` is `None` (UMA) → `ValidationFailedError(f"{unit_code} has no Banxico series")`; `start > end` → `ValidationFailedError`; split the range into windows of at most `MAX_RANGE_DAYS` days (`[start, start + 365]`, then the next day onward) and concatenate; each observation becomes `UnitValue(unit_code, valid_from=d, valid_to=d, value, quote_ccy=spec.quote_ccy, source=f"BANXICO_SIE:{series_id}", confidence=Confidence.VERIFIED)`. `latest_available` reads `/datos/oportuno`.

Job semantics: `window` = `(latest_valid_from + 1 day, today)` when the table has rows for the unit, else `(today - BACKFILL_DAYS, today)`, where `today` is `clock_fn().astimezone(ZoneInfo(MEXICO_CITY)).date()`; `None` when `start > end`. `run`: for each unit in `FETCH_UNITS`, `window` then `fetch_missing` (`inserted` counts upserts with no current row, `corrected` those with one); on `UnitSourceError` it records `notifier.notify(session, template="units.fetch_failed", data={"unit": unit_code, "from": start.isoformat(), "to": end.isoformat()}, recipient_role=Role.ADMIN)` and the same for `Role.MLRO`, logs `logger.warning("units fetch failed", extra={"unit": unit_code, "error": safe_exception_summary(error)})` and continues with the next unit; the handler returns normally (the job is `DONE`; the next slot fetches again). `register_units` builds the job, calls `register_job_handler(UNITS_FETCH_KIND, job.run)` and returns it. `wire_platform` adds, after `register_webhooks(...)`: `_ = register_units(source=BanxicoSieSource(settings.banxico_token, seed_dir=settings.packs_dir / "_units", env=settings.env), notifier=InAppNotifier())`. Schedule (`modules/tenants/service.py`): `UNITS_FETCH_TZ = "America/Mexico_City"`; `schedule_platform_jobs` keeps its loop and adds `_ = queue.schedule(s, name=UNITS_FETCH_KIND, cron=get_settings().units_fetch_cron, tz=UNITS_FETCH_TZ, kind=UNITS_FETCH_KIND, payload={})` (`from xolotl.platform.units import UNITS_FETCH_KIND`).

CLI: `units_app = typer.Typer(no_args_is_help=True, help="Monetary units: seeds and Banxico fetches")`, `app.add_typer(units_app, name="units")`. `units seed [--allow-unverified] [dir]`: `SeedFileSource(dir or settings.packs_dir / "_units", allow_unverified=flag or settings.allow_unverified_pack_values)`; refuses the flag when `settings.env == "prod"` (`typer.echo("error: --allow-unverified is refused when XOLOTL_ENV=prod", err=True)`, exit 2, before any database work); for each unit of `UNITS` in order, `rows = source.fetch(code, date.min, date.max)` and `upsert_value(..., fetched_at=clock.now())` inside `tenant_session(TenantContext.platform(actor))` (app engine: the grant allows it); prints one line per unit `UMA: 3 rows (3 new, 0 unchanged)`; `UnverifiedValueError` ends as `error: UNVERIFIED_VALUE: UMA 2026-02-01` exit 1 through `_cli_errors`. `units fetch --unit <code> [--from YYYY-MM-DD] [--to YYYY-MM-DD]` (`--from`/`--to` as `str | None`, parsed with `date.fromisoformat`, both or neither): builds `BanxicoSieSource(settings.banxico_token, seed_dir=settings.packs_dir / "_units", env=settings.env)`, uses `UnitsFetchJob.window` when no range is given, runs `fetch_missing` in a platform session and prints `UDI: fetched 20, inserted 20, corrected 0 (2026-01-01..2026-01-20) [demo]` (`[live]` with a token); nothing to do → `UDI: up to date (latest 2026-01-31)`; `UnitSourceError` → `error: UNIT_SOURCE_UNAVAILABLE: …` exit 1.

- [ ] **Step 1: Write the failing tests.** `tests/platform/test_units_banxico.py` (`respx.mock` fixture, no database): `test_live_fetch_parses_the_sie_shape` — route `GET {BASE_URL}/series/SF43718/datos/2025-12-10/2025-12-15` returning `{"bmx": {"series": [{"idSerie": "SF43718", "titulo": "FIX", "datos": [{"fecha": "10/12/2025", "dato": "18.1000"}, {"fecha": "11/12/2025", "dato": "18.2500"}, {"fecha": "12/12/2025", "dato": "N/E"}, {"fecha": "15/12/2025", "dato": "1,018.4000"}]}]}}`; assert three `UnitValue`s with `valid_to == valid_from`, values `Decimal("18.1000"), Decimal("18.2500"), Decimal("1018.4000")`, `source == "BANXICO_SIE:SF43718"`, and the request carried `Bmx-Token: tok-1`; `test_a_rejected_token_is_a_source_error` (parametrised over 401 and 403 → `UnitSourceAuthError` matching `"token"`; the instance is a `UnitSourceError` with `status == 503` and `code == "UNIT_SOURCE_UNAVAILABLE"`, which pins the subclass without a `test_errors.py` row); `test_an_upstream_error_is_a_source_error` (parametrised over 400 and 500 → `"HTTP 400"`, `"HTTP 500"`, neither a `UnitSourceAuthError`); `test_an_unreachable_api_is_a_source_error` (`side_effect=httpx.ConnectError`); `test_a_malformed_body_is_a_source_error` (`{"bmx": {}}`); `test_long_ranges_are_split_into_366_day_windows` (2025-01-01..2026-12-31 → two calls: `/datos/2025-01-01/2026-01-01` and `/datos/2026-01-02/2026-12-31`); `test_latest_available_reads_oportuno`; `test_demo_mode_reads_the_seeds` (`BanxicoSieSource(None, seed_dir=REPO / "policy-packs/_units", env="dev").fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))` → 5 rows, `mode == "demo"`); `test_demo_mode_is_refused_in_prod` (`BanxicoSieSource(None, seed_dir=..., env="prod")`: `mode == "demo"`, and both `fetch("UDI", date(2026, 1, 1), date(2026, 1, 5))` and `latest_available("UDI")` raise `UnitSourceError` matching `"XOLOTL_ENV=prod"` without touching the seed files; with a token and `env="prod"` the live route is called); `test_uma_has_no_series` (422). Every live test passes `env="prod"` or `env="dev"` explicitly. `tests/platform/test_units_job.py` (database; copy the `clock` (`FakeClock` from `jobs_support.py`) and `queue` fixtures and the `make_runner` function from `tests/platform/test_jobs.py`, since `jobs_support.py` does not provide them; a `FakeSource` class in the test implementing the protocol over a `dict[tuple[str, date], Decimal]`, recording the windows it was asked for, or raising `UnitSourceError` when told to; a `units_job` fixture calls `register_units(source=fake, notifier=InAppNotifier(), clock_fn=clock)` before any `run_once()`, because `job_handler(UNITS_FETCH_KIND)` raises `KeyError` in a test run that never built the app): `test_fetch_missing_inserts_and_corrects` (`FetchReport(fetched=3, inserted=3, corrected=0)` then, after changing one value, `(3, 0, 1)`); `test_the_job_backfills_31_days_when_the_table_is_empty` (`clock_fn` returning `datetime(2026, 1, 21, 3, 0, tzinfo=UTC)` → Mexico City date 2026-01-20 → window `(date(2025, 12, 20), date(2026, 1, 20))`); `test_the_job_continues_from_the_latest_row` (latest 2026-01-15 → `(date(2026, 1, 16), date(2026, 1, 20))`; latest == today → `None`); `test_the_job_is_idempotent_across_runs` (two `run_once()` of a `make_runner(queue, kinds=[UNITS_FETCH_KIND])` with two enqueued jobs: the `unit_value` count after the second equals the count after the first); `test_a_failing_source_notifies_admin_and_mlro_and_completes` (job `DONE`; two `notification` rows, `template == "units.fetch_failed"`, `data == {"unit": "UDI", "from": "2025-12-20", "to": "2026-01-20"}` and the same for `USD_MXN_FIX`, so four rows in all: ADMIN and MLRO per unit; `caplog` holds `"units fetch failed"` at WARNING). Edit the pinned schedule tests: in `test_kernel_events.py` the expected list becomes `[("idempotency.purge", "0 * * * *", "UTC", "idempotency.purge", {}, True), ("units.fetch", "0 18 * * 1-5", "America/Mexico_City", "units.fetch", {}, True), ("workflow.sweep", "0 * * * *", "UTC", "workflow.sweep", {}, True)]`, the `slots` assertion excludes the `units.fetch` row and gains `assert units.next_run_at in {next_slot("0 18 * * 1-5", "America/Mexico_City", after=m) for m in (before, after)}`, and `names == ["idempotency.purge", "units.fetch", "workflow.sweep"]`; in `test_worker.py` `PLATFORM_SCHEDULES` gains `("units.fetch", "0 18 * * 1-5", "America/Mexico_City", "units.fetch", True)` in the middle, the jobs assertion becomes `{PURGE_KIND: JobStatus.DONE, UNITS_FETCH_KIND: JobStatus.DONE, SWEEP_KIND: JobStatus.DONE}`, and `test_the_platform_is_wired_with_its_job_handlers` adds `assert isinstance(getattr(job_handler(UNITS_FETCH_KIND), "__self__", None), UnitsFetchJob)`. `tests/test_cli.py`: `units seed` on the repository seeds without the flag exits 1 with `error: UNVERIFIED_VALUE: UMA 2026-02-01`; with `--allow-unverified` prints three `… rows (… new, 0 unchanged)` lines and a second run prints `0 new`; `units fetch --unit UDI --from 2026-01-01 --to 2026-01-05` (no token → demo) prints `UDI: fetched 5, inserted 5, corrected 0 (2026-01-01..2026-01-05) [demo]`; `--unit TIIE` exits 1 with `VALIDATION_FAILED`; `--from` without `--to` exits 2; in a prod settings object without a token (`settings = get_settings().model_copy(update={"env": "prod"})`, `monkeypatch.setattr("xolotl.cli.get_settings", lambda: settings)`, the shape `test_cli.py` already uses) `units fetch --unit UDI --from 2026-01-01 --to 2026-01-05` exits 1 with `error: UNIT_SOURCE_UNAVAILABLE: no XOLOTL_BANXICO_TOKEN; demo mode is refused when XOLOTL_ENV=prod` and inserts nothing.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_units_banxico.py tests/platform/test_units_job.py tests/platform/test_worker.py tests/modules/tenants/test_kernel_events.py tests/test_cli.py -q --no-cov` → FAIL.
- [ ] **Step 3: Implement** `banxico.py`, `jobs.py`, the exports (`UnitSourceAuthError`, `BanxicoSieSource`, `parse_series`, `UNITS_FETCH_KIND`, `FETCH_UNITS`, `FetchReport`, `fetch_missing`, `UnitsFetchJob`, `register_units`), the composition, the schedule, the CLI group.
- [ ] **Step 4: Run to verify pass.** The five test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(units): Banxico SIE source, units seed and fetch, the daily units.fetch job with its schedule and failure notification`.

---

### Task A6: Pack schema, loader, canonical hash, `install_pack`, the `packs` CLI and the two MX packs

**Files:**
- Create: `src/xolotl/platform/policy/schema.py`, `errors.py`, `durations.py`, `loader.py`, `policy-packs/MX/ITF-IFPE/v1.yaml`, `policy-packs/MX/LFPIORPI-AVI/v1.yaml`
- Modify: `src/xolotl/platform/policy/__init__.py`, `src/xolotl/platform/units/quantity.py` (`normalize_decimal`, `PlainDecimal`), `src/xolotl/cli.py` (`packs_app`), `tests/platform/test_errors.py` (three rows)
- Test: `tests/platform/test_policy_schema.py`, `tests/golden/__init__.py` and `tests/golden/packs/__init__.py` (empty: ruff runs `INP001` on `tests/**` and every test directory is a package; `tests/golden/units/` and `tests/golden/calendar/` hold only YAML and need none), `tests/golden/packs/test_packs_load.py`, `tests/golden/packs/hashes.yaml`, `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `PolicyPack`, `PolicyPackVersion`, `Confidence`, `ConversionRule`, `Quantity` (A2); `DeadlineRule` (A3); `UnverifiedValueError` (A2); `canonical_json` and `contains_float` (`xolotl.platform.audit`, `xolotl.platform.sqltypes`); `yaml.safe_load`; `get_admin_engine`.
- Produces:

```python
# units/quantity.py (added)
def normalize_decimal(value: Decimal) -> Decimal      # value.normalize(); an exponent above 0 is re-quantized to Decimal(1), so 1000 stays "1000" and 750.0 becomes "750"
type PlainDecimal = Annotated[Decimal, AfterValidator(normalize_decimal)]
class Quantity(BaseModel): amount: PlainDecimal (gt=0); unit: Literal["UDI", "UMA", "USD", "MXN"]   # unchanged otherwise

# policy/durations.py
def iso_duration_days(text: str) -> int          # ^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$ with at least one part; Y*360 + M*30 + D; ValueError otherwise
def add_iso_duration(day: date, text: str) -> date   # calendar arithmetic: years and months (day clamped to the month's length), then days
def check_iso_duration(text: str) -> str      # calls iso_duration_days(text) and returns text unchanged
type IsoDuration = Annotated[str, AfterValidator(check_iso_duration)]

# policy/errors.py
class PackValidationError(ValidationFailedError): default_code = "PACK_INVALID"; default_detail = "the pack file is invalid"
class PackImmutableError(DomainError):            default_code = "PACK_VERSION_IMMUTABLE"; default_detail = "a loaded pack version cannot change; bump the version"
class PolicyLooseningError(DomainError):          default_code = "POLICY_LOOSENING"; default_detail = "an override may only tighten the effective policy"   # raised by A7

# policy/schema.py — every model: model_config = ConfigDict(frozen=True, extra="forbid")
class Provenanced[T](BaseModel): value: T; unit: str | None = None; legal_basis: str = Field(min_length=1); confidence: Confidence; verified_as_of: date
class TierLimits(BaseModel): monthly_ops: Provenanced[Quantity] | None = None; max_balance: Provenanced[Quantity] | None = None
class KycTier(BaseModel): code: str; limits: TierLimits; required_fields: list[str]; required_checks: list[str]; assurance: str
class KycPolicy(BaseModel): tiers: list[KycTier]; review_frequency: dict[str, Provenanced[IsoDuration]]; geolocation_required: Provenanced[bool]
class UboPolicy(BaseModel): ownership_threshold_pct: Provenanced[PlainDecimal]; control_tests: list[str]; fallback: Literal["SENIOR_MANAGING_OFFICIAL"]; legal_person_can_be_ubo: bool = False
class ScreeningBands(BaseModel):   # model_config adds validate_default=True so the defaults normalise like written values
    sanctions_alert: PlainDecimal = Decimal("0.70"); sanctions_review: PlainDecimal = Decimal("0.55"); pep_alert: PlainDecimal = Decimal("0.80"); id_match_floor: PlainDecimal = Decimal("0.95"); id_conflict_cap: PlainDecimal = Decimal("0.30")
class PepPolicy(BaseModel): cooling_off: dict[str, Provenanced[IsoDuration | None]]      # keys DOMESTIC, FOREIGN, INTL_ORG; None = no cooling-off (PEP for ever)
class TrueMatchAction(BaseModel): action: Literal["SUSPEND_IMMEDIATELY", "REVIEW"]; report: str | None = None; notify_client: bool = False
class ScreeningPolicy(BaseModel): mandatory_lists: list[Provenanced[str]]; recommended_lists: list[str]; bands: ScreeningBands = ScreeningBands(); pep: PepPolicy; on_true_match: dict[str, TrueMatchAction]
class Capture(BaseModel): code: str; kind: Literal["THRESHOLD", "ACCUMULATION", "FEE_THRESHOLD"]; threshold: Provenanced[Quantity]; conversion_rule: ConversionRule; window: Provenanced[IsoDuration] | None = None; period: Provenanced[Literal["MONTH", "QUARTER"]]; report: str; filing: Literal["SERIALIZED", "MANUAL_UPLOAD"]; merge_into: Provenanced[str] | None = None
class ReportRef(BaseModel): code: str; anchor: Literal["period_end", "committee_decision", "detection"]; deadline: Provenanced[DeadlineRule]; zero_report: bool
class RetentionPolicy(BaseModel): kyc_after_relationship: Provenanced[IsoDuration]; transactions: Provenanced[IsoDuration]
class GovernancePolicy(BaseModel): committee_required: Provenanced[bool]; four_eyes_on_reports: bool = True
class UnitsPolicy(BaseModel): quote_ccy: Literal["MXN"] = "MXN"; tier_limit_rule: Provenanced[ConversionRule] | None = None
class PolicyBody(BaseModel): units: UnitsPolicy; kyc: KycPolicy; ubo: UboPolicy; screening: ScreeningPolicy; captures: list[Capture]; reports: list[ReportRef]; retention: RetentionPolicy; governance: GovernancePolicy
    # validators: tier, capture and report codes unique; every Capture.merge_into names another capture of the same pack
class PackConfig(PolicyBody): pack: str (pattern ^[A-Z]{2}/[A-Z0-9]+-[A-Z0-9]+$); version: int (ge=1); title: str; effective_from: date; effective_to: date | None = None; country: str (pattern ^[A-Z]{2}$); regulators: list[str] (min 1); products: list[str] (min 1)
    # validator: pack starts with f"{country}/"; effective_to, when given, is after effective_from
def unverified_paths(body: PolicyBody) -> list[str]   # sorted dotted paths of every Provenanced whose confidence is UNVERIFIED:
    # "kyc.tiers[N1].limits.monthly_ops", "kyc.review_frequency[HIGH]", "kyc.geolocation_required", "ubo.ownership_threshold_pct",
    # "screening.mandatory_lists[MX_LPB]", "screening.pep.cooling_off[DOMESTIC]", "captures[ITF_ROR_CASH].threshold|.window|.period|.merge_into",
    # "reports[MX_F41_ROR].deadline", "retention.kyc_after_relationship", "retention.transactions", "governance.committee_required", "units.tier_limit_rule"

# policy/loader.py
def pack_files(packs_dir: Path) -> list[Path]                       # sorted(packs_dir.glob("*/*/v*.yaml")), directories starting with "_" excluded
def load_pack(path: Path, *, allow_unverified: bool = False) -> PackConfig
    # yaml.safe_load (a missing file, a yaml.YAMLError or a non-mapping -> PackValidationError(f"{path.name}: not a pack"); OSError is caught too, so a wrong path is a 422 error line, never a traceback); PackConfig.model_validate (pydantic ValidationError ->
    # PackValidationError(f"{path.name}: {error_count()} validation errors")); unverified_paths non-empty and not allow_unverified ->
    # UnverifiedValueError(f"{config.pack} v{config.version}: " + ", ".join(paths))
def content_hash(config: PackConfig) -> bytes                        # sha256(canonical_json(config.model_dump(mode="json"))).digest(); asserts not contains_float(dump)
def install_pack(session: Session, config: PackConfig, *, source_path: str, loaded_by: str) -> UUID
    # policy_pack upsert by code (country_code, sector, licence from the code: "MX/ITF-IFPE" -> MX, ITF, IFPE; title); policy_pack_version by (pack_id, version):
    # absent -> insert (config = model_dump(mode="json"), content_hash, loaded_by, source_path) and return its id; present with the same hash -> return its id, nothing written;
    # present with another hash -> PackImmutableError. The session must be an owner session (the app role has SELECT only).
```

The two packs, complete. Write every amount, percentage and band as a quoted string; `verified_as_of: 2026-10-18` is the date of the program spec research (D21). Short form of the ITF instrument used in `legal_basis`: "DCG art. 58 LRITF" = Disposiciones de carácter general a que se refiere el artículo 58 de la Ley para Regular las Instituciones de Tecnología Financiera (DOF 2018-09-10).

```yaml
# policy-packs/MX/ITF-IFPE/v1.yaml
pack: MX/ITF-IFPE
version: 1
title: "Instituciones de Tecnología Financiera — IFPE (fondos de pago electrónico)"
effective_from: 2026-01-01
country: MX
regulators: [CNBV, UIF]
products: [EMONEY, CROWDFUNDING, CRYPTO]
units:
  quote_ccy: MXN
  tier_limit_rule: {value: LAST_DAY_PREV_MONTH, legal_basis: "DCG art. 58 LRITF: límites por nivel de cuenta en UDI al valor del último día del mes inmediato anterior", confidence: VERIFIED, verified_as_of: 2026-10-18}
kyc:
  tiers:
    - code: N1
      limits:
        monthly_ops: {value: {amount: "750", unit: UDI}, legal_basis: "DCG art. 58 LRITF: cuentas nivel 1, abonos mensuales hasta 750 UDI", confidence: VERIFIED, verified_as_of: 2026-10-18}
        max_balance: {value: {amount: "1000", unit: UDI}, legal_basis: "DCG art. 58 LRITF: cuentas nivel 1, saldo máximo 1,000 UDI", confidence: VERIFIED, verified_as_of: 2026-10-18}
      required_fields: [given_names, first_surname, second_surname, birth_date, birth_country, nationalities, gender, occupation_code, email, phone]
      required_checks: [GEOLOCATION, DEVICE_RISK, SCREENING]
      assurance: A0
    - code: N2
      limits:
        monthly_ops: {value: {amount: "3000", unit: UDI}, legal_basis: "DCG art. 58 LRITF: cuentas nivel 2, abonos mensuales hasta 3,000 UDI", confidence: VERIFIED, verified_as_of: 2026-10-18}
        max_balance: null
      required_fields: [given_names, first_surname, second_surname, birth_date, birth_country, birth_state, nationalities, gender, occupation_code, email, phone, curp, rfc, address, clave_elector]
      required_checks: [GEOLOCATION, DEVICE_RISK, SCREENING, CURP_VALIDATION, ID_DOCUMENT]
      assurance: A2
    - code: N3
      limits: {monthly_ops: null, max_balance: null}
      required_fields: [given_names, first_surname, second_surname, birth_date, birth_country, birth_state, nationalities, gender, occupation_code, economic_activity_code, email, phone, curp, rfc, address, clave_elector]
      required_checks: [GEOLOCATION, DEVICE_RISK, SCREENING, CURP_VALIDATION, RFC_VALIDATION, ID_DOCUMENT, LIVENESS, PROOF_OF_ADDRESS]
      assurance: A3
  review_frequency:
    HIGH: {value: P6M, legal_basis: "DCG art. 58 LRITF: actualización del expediente de clientes de alto riesgo", confidence: VERIFIED, verified_as_of: 2026-10-18}
    MEDIUM: {value: P12M, legal_basis: "DCG art. 58 LRITF: actualización periódica del expediente", confidence: VERIFIED, verified_as_of: 2026-10-18}
    LOW: {value: P24M, legal_basis: "DCG art. 58 LRITF: actualización periódica del expediente", confidence: VERIFIED, verified_as_of: 2026-10-18}
  geolocation_required: {value: true, legal_basis: "DCG art. 58 LRITF: geolocalización del dispositivo en la apertura y en operaciones", confidence: VERIFIED, verified_as_of: 2026-10-18}
ubo:
  ownership_threshold_pct: {value: "25", legal_basis: "DCG art. 58 LRITF: propietario real, participación del 25% o más", confidence: VERIFIED, verified_as_of: 2026-10-18}
  control_tests: [VOTING_RIGHTS, APPOINT_REMOVE_MANAGEMENT, DECISIVE_INFLUENCE]
  fallback: SENIOR_MANAGING_OFFICIAL
  legal_person_can_be_ubo: false
screening:
  mandatory_lists:
    - {value: MX_LPB, legal_basis: "DCG art. 58 LRITF: Lista de Personas Bloqueadas de la UIF", confidence: VERIFIED, verified_as_of: 2026-10-18}
    - {value: UN_CONSOLIDATED, legal_basis: "Resoluciones del Consejo de Seguridad de la ONU incorporadas a la LPB", confidence: VERIFIED, verified_as_of: 2026-10-18}
  recommended_lists: [OFAC_SDN, MX_SAT_69B, EU_FSF, UK_FCDO]
  bands: {sanctions_alert: "0.70", sanctions_review: "0.55", pep_alert: "0.80", id_match_floor: "0.95", id_conflict_cap: "0.30"}
  pep:
    cooling_off:
      DOMESTIC: {value: P1Y, legal_basis: "DCG art. 58 LRITF: PEP nacional, un año posterior al cargo", confidence: VERIFIED, verified_as_of: 2026-10-18}
      FOREIGN: {value: null, legal_basis: "DCG art. 58 LRITF: PEP extranjera, sin periodo posterior", confidence: VERIFIED, verified_as_of: 2026-10-18}
      INTL_ORG: {value: null, legal_basis: "DCG art. 58 LRITF: PEP de organismo internacional, sin periodo posterior", confidence: VERIFIED, verified_as_of: 2026-10-18}
  on_true_match:
    MX_LPB: {action: SUSPEND_IMMEDIATELY, report: MX_F41_ROI24, notify_client: false}
    UN_CONSOLIDATED: {action: SUSPEND_IMMEDIATELY, report: MX_F41_ROI24, notify_client: false}
    OFAC_SDN: {action: REVIEW}
    MX_SAT_69B: {action: REVIEW}
    EU_FSF: {action: REVIEW}
    UK_FCDO: {action: REVIEW}
captures:
  - code: ITF_ROR_CASH
    kind: THRESHOLD
    threshold: {value: {amount: "5000", unit: USD}, legal_basis: "DCG art. 58 LRITF: operación relevante, efectivo por USD 5,000 o más", confidence: VERIFIED, verified_as_of: 2026-10-18}
    conversion_rule: PRIOR_BANKING_DAY
    window: null
    period: {value: QUARTER, legal_basis: "DCG art. 58 LRITF: reporte trimestral de operaciones relevantes; granularidad mes/trimestre pendiente (spec §13)", confidence: UNVERIFIED, verified_as_of: 2026-10-18}
    report: MX_F41_ROR
    filing: SERIALIZED
  - code: ITF_FX_CASH
    kind: THRESHOLD
    threshold: {value: {amount: "500", unit: USD}, legal_basis: "DCG art. 58 LRITF: compraventa de divisas en efectivo por USD 500 o más", confidence: VERIFIED, verified_as_of: 2026-10-18}
    conversion_rule: PRIOR_BANKING_DAY
    window: null
    period: {value: QUARTER, legal_basis: "DCG art. 58 LRITF: reporte trimestral", confidence: VERIFIED, verified_as_of: 2026-10-18}
    report: MX_ITF_FX_CASH
    filing: MANUAL_UPLOAD
  - code: ITF_INTL_TRANSFER
    kind: THRESHOLD
    threshold: {value: {amount: "1000", unit: USD}, legal_basis: "DCG art. 58 LRITF: transferencias internacionales por USD 1,000 o más", confidence: VERIFIED, verified_as_of: 2026-10-18}
    conversion_rule: PRIOR_BANKING_DAY
    window: null
    period: {value: MONTH, legal_basis: "DCG art. 58 LRITF: reporte mensual de transferencias internacionales", confidence: VERIFIED, verified_as_of: 2026-10-18}
    report: MX_ITF_INTL_TRANSFER
    filing: MANUAL_UPLOAD
  - code: ITF_VA_OPS
    kind: THRESHOLD
    threshold: {value: {amount: "7500", unit: UDI}, legal_basis: "DCG art. 58 LRITF: operaciones con activos virtuales por 7,500 UDI o más", confidence: VERIFIED, verified_as_of: 2026-10-18}
    conversion_rule: SAME_DAY
    window: null
    period: {value: QUARTER, legal_basis: "DCG art. 58 LRITF: reporte trimestral", confidence: VERIFIED, verified_as_of: 2026-10-18}
    report: MX_ITF_VA_OPS
    filing: MANUAL_UPLOAD
reports:
  - code: MX_F41_ROR
    anchor: period_end
    deadline: {value: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}, tz: America/Mexico_City}, legal_basis: "DCG art. 58 LRITF: ROR dentro de los diez días hábiles siguientes al cierre del periodo", confidence: VERIFIED, verified_as_of: 2026-10-18}
    zero_report: true
  - code: MX_F41_ROI
    anchor: committee_decision
    deadline: {value: {kind: BUSINESS_DAYS_AFTER, params: {n: 3}, tz: America/Mexico_City}, legal_basis: "DCG art. 58 LRITF: ROI dentro de los tres días hábiles siguientes a la sesión del comité, que resuelve en sesenta días naturales desde la detección", confidence: VERIFIED, verified_as_of: 2026-10-18}
    zero_report: false
  - code: MX_F41_ROI24
    anchor: detection
    deadline: {value: {kind: HOURS_AFTER, params: {hours: 24}, tz: America/Mexico_City}, legal_basis: "DCG art. 58 LRITF: reporte de 24 horas desde la detección", confidence: VERIFIED, verified_as_of: 2026-10-18}
    zero_report: false
  - code: MX_ITF_INTL_TRANSFER
    anchor: period_end
    deadline: {value: {kind: BUSINESS_DAYS_AFTER, params: {n: 15}, tz: America/Mexico_City}, legal_basis: "DCG art. 58 LRITF: transferencias internacionales, quince días hábiles siguientes al cierre del mes", confidence: VERIFIED, verified_as_of: 2026-10-18}
    zero_report: false
retention:
  kyc_after_relationship: {value: P10Y, legal_basis: "DCG art. 58 LRITF: conservación del expediente diez años desde el fin de la relación", confidence: VERIFIED, verified_as_of: 2026-10-18}
  transactions: {value: P10Y, legal_basis: "DCG art. 58 LRITF: conservación de reportes y registros de operaciones diez años", confidence: VERIFIED, verified_as_of: 2026-10-18}
governance:
  committee_required: {value: true, legal_basis: "DCG art. 58 LRITF: Comité de Comunicación y Control", confidence: VERIFIED, verified_as_of: 2026-10-18}
  four_eyes_on_reports: true
```

```yaml
# policy-packs/MX/LFPIORPI-AVI/v1.yaml
pack: MX/LFPIORPI-AVI
version: 1
title: "LFPIORPI — actividad vulnerable fracción XVI (activos virtuales)"
effective_from: 2026-11-30
country: MX
regulators: [SAT, UIF]
products: [CRYPTO]
units: {quote_ccy: MXN, tier_limit_rule: null}
kyc:
  tiers: []
  review_frequency: {}
  geolocation_required: {value: false, legal_basis: "LFPIORPI y sus Reglas de carácter general: sin obligación de geolocalización", confidence: VERIFIED, verified_as_of: 2026-10-18}
ubo:
  ownership_threshold_pct: {value: "25", legal_basis: "LFPIORPI art. 3 fr. III (reforma DOF 2025-07-16): beneficiario controlador, 25% de participación, aplicable a AV desde marzo de 2027 (A-0.16)", confidence: VERIFIED, verified_as_of: 2026-10-18}
  control_tests: [VOTING_RIGHTS, APPOINT_REMOVE_MANAGEMENT, DECISIVE_INFLUENCE]
  fallback: SENIOR_MANAGING_OFFICIAL
  legal_person_can_be_ubo: false
screening:
  mandatory_lists:
    - {value: MX_LPB, legal_basis: "Reglas de carácter general LFPIORPI: Lista de Personas Bloqueadas", confidence: VERIFIED, verified_as_of: 2026-10-18}
    - {value: UN_CONSOLIDATED, legal_basis: "Resoluciones del Consejo de Seguridad de la ONU incorporadas a la LPB", confidence: VERIFIED, verified_as_of: 2026-10-18}
  recommended_lists: [OFAC_SDN, MX_SAT_69B]
  bands: {sanctions_alert: "0.70", sanctions_review: "0.55", pep_alert: "0.80", id_match_floor: "0.95", id_conflict_cap: "0.30"}
  pep:
    cooling_off:
      DOMESTIC: {value: P1Y, legal_basis: "Reglas de carácter general LFPIORPI: PEP nacional, un año posterior al cargo", confidence: VERIFIED, verified_as_of: 2026-10-18}
      FOREIGN: {value: null, legal_basis: "Reglas de carácter general LFPIORPI: PEP extranjera, sin periodo posterior", confidence: VERIFIED, verified_as_of: 2026-10-18}
      INTL_ORG: {value: null, legal_basis: "Reglas de carácter general LFPIORPI: PEP de organismo internacional, sin periodo posterior", confidence: VERIFIED, verified_as_of: 2026-10-18}
  on_true_match:
    MX_LPB: {action: SUSPEND_IMMEDIATELY, report: null, notify_client: false}
    UN_CONSOLIDATED: {action: SUSPEND_IMMEDIATELY, report: null, notify_client: false}
    OFAC_SDN: {action: REVIEW}
    MX_SAT_69B: {action: REVIEW}
captures:
  - code: AVI_XVI_A
    kind: ACCUMULATION
    threshold: {value: {amount: "210", unit: UMA}, legal_basis: "LFPIORPI art. 17 fr. XVI: aviso por operación igual o superior a 210 UMA o por acumulación en seis meses", confidence: VERIFIED, verified_as_of: 2026-10-18}
    conversion_rule: SAME_DAY
    window: {value: P6M, legal_basis: "LFPIORPI art. 17 último párrafo: acumulación de operaciones en seis meses", confidence: VERIFIED, verified_as_of: 2026-10-18}
    period: {value: MONTH, legal_basis: "LFPIORPI art. 23: aviso mensual", confidence: VERIFIED, verified_as_of: 2026-10-18}
    report: MX_AVISO_AVI
    filing: SERIALIZED
  - code: AVI_XVI_B
    kind: FEE_THRESHOLD
    threshold: {value: {amount: "4", unit: UMA}, legal_basis: "LFPIORPI art. 17 fr. XVI: comisión o cargo igual o superior a 4 UMA; redacción pendiente (spec §13)", confidence: UNVERIFIED, verified_as_of: 2026-10-18}
    conversion_rule: SAME_DAY
    window: null
    period: {value: MONTH, legal_basis: "LFPIORPI art. 23: aviso mensual", confidence: VERIFIED, verified_as_of: 2026-10-18}
    report: MX_AVISO_AVI
    filing: SERIALIZED
    merge_into: {value: AVI_XVI_A, legal_basis: "Un solo aviso bajo el inciso A cuando una operación activa A y B; base legal pendiente (spec §13)", confidence: UNVERIFIED, verified_as_of: 2026-10-18}
reports:
  - code: MX_AVISO_AVI
    anchor: period_end
    deadline: {value: {kind: DAY_OF_NEXT_MONTH, params: {day: 17, roll: NEXT_BUSINESS_DAY}, tz: America/Mexico_City}, legal_basis: "LFPIORPI art. 23: a más tardar el día 17 del mes inmediato siguiente; si es inhábil, el día hábil siguiente", confidence: VERIFIED, verified_as_of: 2026-10-18}
    zero_report: true
retention:
  kyc_after_relationship: {value: P5Y, legal_basis: "LFPIORPI art. 18 fr. IV: conservar la información cinco años; interacción con los diez años ITF pendiente (spec §13)", confidence: UNVERIFIED, verified_as_of: 2026-10-18}
  transactions: {value: P5Y, legal_basis: "LFPIORPI art. 18 fr. IV: conservar la información y documentación cinco años", confidence: VERIFIED, verified_as_of: 2026-10-18}
governance:
  committee_required: {value: false, legal_basis: "LFPIORPI art. 20: cumplimiento a través del representante encargado; sin comité obligatorio", confidence: VERIFIED, verified_as_of: 2026-10-18}
  four_eyes_on_reports: true
```

The `UNVERIFIED` values, exactly (spec §5.2 and §13): `captures[ITF_ROR_CASH].period` (ROR period granularity); `captures[AVI_XVI_B].threshold` (the 4 UMA fee wording); `captures[AVI_XVI_B].merge_into` (the one-aviso-under-A rule's legal basis); `retention.kyc_after_relationship` of the AVI pack (five years for avisos against ten for KYC when both packs apply). The other §13 items are not pack values: the Formato 41 encoding belongs to `report-defs/` (SP-05), the Anexo 3 branch fields are `tenant_branch` columns, the UMA 2026 value is the `UNVERIFIED` row of `_units/UMA.yaml` (A4), and the INE CIC/OCR authority is plan 2c's validator.

CLI: `packs_app = typer.Typer(no_args_is_help=True, help="Policy packs")`, `app.add_typer(packs_app, name="packs")`. `packs load [--allow-unverified] [path]`: `path` defaults to `settings.packs_dir`; a file path loads that one pack, a directory loads `pack_files(path)`; `allow = flag or settings.allow_unverified_pack_values`, refused with exit 2 when `settings.env == "prod"` and `allow`; owner session `tenant_session(TenantContext.platform(actor), factory=sessionmaker(bind=get_admin_engine(), expire_on_commit=False))`; per pack prints `installed MX/ITF-IFPE v1 (sha256 <first 12 hex>)` or `unchanged MX/ITF-IFPE v1 (sha256 …)`; `UnverifiedValueError`, `PackValidationError` and `PackImmutableError` end through `_cli_errors` (`error: UNVERIFIED_VALUE: MX/ITF-IFPE v1: captures[ITF_ROR_CASH].period`). `packs validate <path>`: no database; prints `valid MX/ITF-IFPE v1 (sha256 …); unverified: captures[ITF_ROR_CASH].period` (or `; unverified: none`), exit 0; an invalid file exits 1 with `error: PACK_INVALID: …`. `packs show <code> --version N`: platform session on the app engine; prints `json.dumps({"code", "version", "effective_from", "content_hash": hex, "config"}, indent=2, ensure_ascii=False)`; unknown → `error: NOT_FOUND: no pack MX/X-Y version 9`.

- [ ] **Step 1: Write the failing tests.** `tests/platform/test_policy_schema.py` (no database): `test_quantity_amounts_are_normalised` (`Quantity(amount=Decimal("750.0"), unit="UDI").amount == Decimal("750")` and `str(...) == "750"`; `str(Quantity(amount=1000, unit="UDI").amount) == "1000"`; `amount=0` and `unit="EUR"` raise); `test_iso_durations` (`iso_duration_days("P10Y") == 3600`, `("P1Y") == ("P12M") == 360`, `("P6M") == 180`, `("P90D") == 90`; `"P1W"`, `"10Y"`, `"P"`, `"PT24H"` raise `ValueError`); `test_add_iso_duration` (`add_iso_duration(date(2026, 1, 31), "P1M") == date(2026, 2, 28)`, `(date(2024, 2, 29), "P1Y") == date(2025, 2, 28)`, `(date(2026, 3, 15), "P10Y") == date(2036, 3, 15)`, `(date(2026, 12, 31), "P45D") == date(2027, 2, 14)`); `test_a_pack_with_an_unknown_key_is_refused` (`extra="forbid"`); `test_duplicate_capture_codes_are_refused`; `test_merge_into_must_name_a_capture`; `test_unverified_paths_walk_every_provenanced_field` (a minimal in-test pack dict with `UNVERIFIED` set on `kyc.geolocation_required`, `kyc.review_frequency[HIGH]`, `screening.pep.cooling_off[DOMESTIC]`, `reports[R1].deadline` → exactly those four, sorted). `tests/golden/packs/hashes.yaml`: `{"MX/ITF-IFPE": {1: "<64 hex, filled by the implementer after reviewing the YAML>"}, "MX/LFPIORPI-AVI": {1: "<64 hex>"}}`. `tests/golden/packs/test_packs_load.py`: `test_both_packs_load_with_unverified_allowed` (`load_pack(path, allow_unverified=True)` for both; `itf.products == ["EMONEY", "CROWDFUNDING", "CRYPTO"]`, `[t.code for t in itf.kyc.tiers] == ["N1", "N2", "N3"]`, `itf.kyc.tiers[0].limits.monthly_ops.value == Quantity(amount=Decimal("750"), unit="UDI")`, `[c.code for c in itf.captures] == ["ITF_ROR_CASH", "ITF_FX_CASH", "ITF_INTL_TRANSFER", "ITF_VA_OPS"]`, `avi.effective_from == date(2026, 11, 30)`, `avi.captures[1].merge_into.value == "AVI_XVI_A"`); `test_unverified_paths_are_exactly_the_open_items` (`unverified_paths(itf) == ["captures[ITF_ROR_CASH].period"]`; `unverified_paths(avi) == ["captures[AVI_XVI_B].merge_into", "captures[AVI_XVI_B].threshold", "retention.kyc_after_relationship"]`); `test_the_loader_refuses_unverified_values_by_default` (`UnverifiedValueError` matching `"captures\\[ITF_ROR_CASH\\].period"`); `test_content_hash_matches_the_golden` (both hashes equal `hashes.yaml`); `test_content_hash_ignores_key_order_and_number_spelling` (write the ITF YAML to `tmp_path` with the top-level keys reversed and `amount: "750"` rewritten as `amount: 750` and `amount: 750.0` → the same hash); `test_config_holds_no_floats` (`contains_float(config.model_dump(mode="json")) is False` for both); with the database (`admin_engine` session): `test_install_pack_is_idempotent_and_versions_are_immutable` (`install_pack` twice → same id, one `policy_pack_version` row, `policy_pack.sector == "ITF"`, `licence == "IFPE"`, `country_code == "MX"`; the same version with `title` changed → `PackImmutableError`; version 2 → a second row); `test_the_app_role_cannot_install_packs` (`install_pack` under `tenant_session(platform_ctx())` raises `sqlalchemy.exc.ProgrammingError` matching `permission denied`). `tests/platform/test_errors.py`: `(PackValidationError, 422, "PACK_INVALID")`, `(PackImmutableError, 409, "PACK_VERSION_IMMUTABLE")`, `(PolicyLooseningError, 409, "POLICY_LOOSENING")`. `tests/test_cli.py`: `packs validate policy-packs/MX/ITF-IFPE/v1.yaml` prints `valid MX/ITF-IFPE v1` and `unverified: captures[ITF_ROR_CASH].period`; `packs load` without the flag exits 1 with `error: UNVERIFIED_VALUE: MX/ITF-IFPE v1: captures[ITF_ROR_CASH].period`; `packs load --allow-unverified` prints two `installed` lines and a second run two `unchanged` lines; `packs show MX/ITF-IFPE --version 1` prints JSON whose `config.pack == "MX/ITF-IFPE"`; `--version 9` exits 1 with `NOT_FOUND`; in a prod settings object (`monkeypatch.setattr("xolotl.cli.get_settings", lambda: settings.model_copy(update={"env": "prod"}))`) `packs load --allow-unverified` exits 2 before touching the database.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_policy_schema.py tests/golden/packs tests/platform/test_errors.py tests/test_cli.py -k "packs or schema or errors" -q --no-cov` → FAIL.
- [ ] **Step 3: Implement** the modules, the two YAML files exactly as above, the CLI group, the exports (`Provenanced`, `Quantity` re-export, every schema class, `PolicyBody`, `PackConfig`, `unverified_paths`, `load_pack`, `content_hash`, `install_pack`, `pack_files`, `iso_duration_days`, `add_iso_duration`, `PackValidationError`, `PackImmutableError`, `PolicyLooseningError`); compute the two digests with `packs validate` and write them into `hashes.yaml`.
- [ ] **Step 4: Run to verify pass.** The four test targets, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(policy): pack schema with provenance, loader with canonical hash and immutable versions, packs CLI, MX/ITF-IFPE v1 and MX/LFPIORPI-AVI v1`.

---

### Task A7: Policy service — assignments, tighten-only overrides, resolver, snapshots, audit, effective-policy goldens

**Files:**
- Create: `src/xolotl/platform/policy/overrides.py`, `resolver.py`, `snapshots.py`, `assignments.py`
- Modify: `src/xolotl/platform/policy/__init__.py`
- Test: `tests/platform/test_policy_service.py`, `tests/platform/policy_support.py`, `tests/golden/packs/effective.yaml`, `tests/golden/packs/test_effective_goldens.py`

**Interfaces:**
- Consumes: `PackConfig`, `PolicyBody`, `Provenanced`, `Quantity`, `PlainDecimal`, `IsoDuration`, `iso_duration_days`, `load_pack`, `install_pack`, `PolicyLooseningError` (A6); the five models (A2); `UNIT_OF` (A4); `record`, `AuditEvent`, `bound_context`, `clock.now`, `uuid7`, `canonical_json`, `contains_float`, `NotFoundError`, `DomainError`, `ValidationFailedError`.
- Produces (the names plans 2b/2c consume, exactly as `00-common.md` lists them):

```python
# overrides.py — every model frozen, extra="forbid"
class TierLimitsOverride(BaseModel): monthly_ops: Quantity | None = None; max_balance: Quantity | None = None
class BandsOverride(BaseModel): sanctions_alert: PlainDecimal | None = None; sanctions_review: PlainDecimal | None = None; pep_alert: PlainDecimal | None = None; id_match_floor: PlainDecimal | None = None; id_conflict_cap: PlainDecimal | None = None
class RetentionOverride(BaseModel): kyc_after_relationship: IsoDuration | None = None; transactions: IsoDuration | None = None
class TenantOverride(BaseModel):
    tier_limits: dict[str, TierLimitsOverride] = {}          # by tier code
    capture_thresholds: dict[str, Quantity] = {}             # by capture code
    review_frequency: dict[str, IsoDuration] = {}            # by risk level
    retention: RetentionOverride | None = None
    mandatory_lists_add: list[str] = []
    recommended_lists_add: list[str] = []
    bands: BandsOverride | None = None
    ubo_ownership_threshold_pct: PlainDecimal | None = None
    committee_required: bool | None = None
    geolocation_required: bool | None = None
def validate_against(override: TenantOverride, body: PolicyBody) -> None       # PolicyLooseningError(f"{path}: {new} loosens {current}") / ValidationFailedError
def apply_override(body: PolicyBody, override: TenantOverride, *, version: int, approved_on: date) -> PolicyBody   # skips an entry whose tier, capture or review level the body lacks; never loosens

# resolver.py
class EffectivePolicy(PolicyBody): on: date; product: str | None; country: str; pack_codes: list[str]; pack_version_ids: list[UUID]; override_version: int | None
def merge_packs(configs: Sequence[PackConfig]) -> PolicyBody                  # strictest of each value, see the rules below
def effective_policy(session: Session, *, on: date, product: str | None) -> EffectivePolicy

# snapshots.py
class PolicySnapshotContent(BaseModel): snapshot_id: UUID; content_hash: str; created_at: datetime; policy: EffectivePolicy
def snapshot_policy(session: Session, effective: EffectivePolicy) -> UUID
def get_snapshot(session: Session, snapshot_id: UUID) -> PolicySnapshotContent

# assignments.py
def assignments_on(session: Session, *, on: date, product: str | None) -> list[TenantPackAssignment]       # tenant-wide rows plus the product's rows
def assignments_in_force(session: Session, *, on: date) -> list[TenantPackAssignment]                       # every row in force on `on`, whatever its product
def assign_pack(session: Session, *, pack_version_id: UUID, product: str | None, effective_from: date, approved_by: UUID) -> UUID
def create_override(session: Session, overrides: TenantOverride, *, approved_by: UUID) -> int
```

Tighten-only rules (`validate_against`; `path` strings as in `unverified_paths`; an unknown tier code, capture code or review level, a quantity in another unit than the pack's, or a resulting `sanctions_review` above `sanctions_alert` is a `ValidationFailedError`; equality is allowed everywhere): tier limits and capture thresholds only lower (`new.amount <= current.amount`, same `unit`; a limit the pack leaves unlimited may be set); `review_frequency` only shorter and `retention` only longer by `iso_duration_days` (so `P1Y` equals `P12M`); lists only added (`*_add`, duplicates ignored); bands: `sanctions_alert`, `sanctions_review`, `pep_alert` only lower, `id_match_floor` and `id_conflict_cap` only higher; `ubo_ownership_threshold_pct` only lower; `committee_required` and `geolocation_required` may not go from `true` to `false`. `apply_override` replaces an overridden value only where the body carries that tier, capture or review level and the override is at least as strict as the body's value under the same comparisons (the override was validated against the union of every pack in force when it was created; a pack that comes into force later with a stricter value keeps its value and provenance), wrapping the new value as `Provenanced(value=new, legal_basis=f"tenant override v{version}", confidence=Confidence.VERIFIED, verified_as_of=approved_on)` where the pack field is `Provenanced`; added lists go to the end of their list (`mandatory_lists_add` entries wrapped the same way; a value already present is skipped).

Merge rules (`merge_packs`; input ordered by pack code; a single pack returns its body unchanged): `units.tier_limit_rule` = the first non-null; `kyc.tiers` = union by code (a code in two packs keeps the first tier with each limit the lower amount, units must agree or `DomainError`); `kyc.review_frequency` = union by key, shorter duration wins; `kyc.geolocation_required` = OR; `ubo.ownership_threshold_pct` = min, `control_tests` = sorted union, `legal_person_can_be_ubo` = AND; `screening.mandatory_lists` = union by value (first provenance kept), `recommended_lists` = sorted union, `bands` = per field the stricter (min for `sanctions_alert`, `sanctions_review`, `pep_alert`; max for `id_match_floor`, `id_conflict_cap`), `pep.cooling_off` = union by key where `None` (indefinite) beats any duration and otherwise the shorter wins, `on_true_match` = union by list where `SUSPEND_IMMEDIATELY` beats `REVIEW`, `report` is the first non-null and `notify_client` is AND; `captures` = union by code (same code: lower threshold, units must agree); `reports` = union by code (first wins); `retention` = longer per field; `governance` = OR on both flags.

`assignments_on`: rows with `effective_from <= on` and (`effective_to IS NULL` or `effective_to >= on`) and (`product IS NULL` or, when `product` is given, `product == :product`), ordered by the pack code of their version. `effective_policy`: those rows, each `PackConfig.model_validate(version.config)` filtered to versions in force on `on` (`config.effective_from <= on` and `effective_to` null or `>= on`); none → `NotFoundError("no policy pack assigned for that date and product", code="POLICY_NOT_ASSIGNED")`; `merge_packs`; the latest `TenantOverrideVersion` with `effective_from <= on` (highest `version`) applied with `apply_override(..., version=row.version, approved_on=row.effective_from)`; `EffectivePolicy(**body, on=on, product=product, country=first config's country, pack_codes=[...], pack_version_ids=[...], override_version=...)`. `snapshot_policy`: `content = effective.model_dump(mode="json")` (assert `not contains_float`), `digest = hashlib.sha256(canonical_json(content)).digest()`, `insert(PolicySnapshot).values(tenant_id=ctx.require_tenant_id(), id=uuid7(), content_hash=digest, content=content).on_conflict_do_nothing(index_elements=["tenant_id", "content_hash"]).returning(PolicySnapshot.id)`; `None` → select the existing id by `(tenant_id, content_hash)`. `get_snapshot`: `s.get(PolicySnapshot, (tenant_id, snapshot_id))` or `NotFoundError`; `content_hash` as hex; `policy = EffectivePolicy.model_validate(row.content)`. `assign_pack`: `s.get(PolicyPackVersion, pack_version_id)` or `NotFoundError("pack version not found")`; among the tenant's assignments with the same product (`product IS NOT DISTINCT FROM :product`), one that is open (`effective_to IS NULL`) and starts before `effective_from` gets `effective_to = effective_from - timedelta(days=1)`; any other same-product row whose range overlaps the new one (`effective_to IS NULL or effective_to >= effective_from`) raises `DomainError("an assignment for that product already covers that date")`; then the D21 rule: with `product is None`, any row of a product (`product IS NOT NULL`) still in force on or after `effective_from` raises `DomainError("a tenant-wide assignment may not overlap a product assignment (D21: two packs need distinct products)")`, and with a `product`, any tenant-wide row (`product IS NULL`) in force on or after `effective_from` raises the same error (a row of another product is fine: EMONEY and CRYPTO coexist); insert, flush, `record(s, AuditEvent(action="policy.pack_assigned", entity_type="tenant_pack_assignment", entity_id=str(row.id), detail={"pack": pack.code, "version": version.version, "pack_version_id": str(version.id), "product": product, "effective_from": effective_from.isoformat(), "closed_assignment_id": str(old.id) or None}))`; return `row.id`. `assignments_in_force`: rows with `effective_from <= on` and (`effective_to IS NULL` or `effective_to >= on`), no product filter, ordered by the pack code of their version. `create_override`: `effective_from = clock.now().date()`; `configs` = the distinct pack versions of `assignments_in_force(s, on=effective_from)` whose config is in force on `effective_from` (the same filter `effective_policy` applies), ordered by pack code; none → the `POLICY_NOT_ASSIGNED` error; `validate_against(overrides, merge_packs(configs))` once, against that union (the strictest merge of every pack any product sees, so a value that tightens it tightens every product's policy, and a tier, capture or review level unknown to the union is `VALIDATION_FAILED`); `version = (max existing) + 1 or 1`; insert `TenantOverrideVersion(version, overrides=overrides.model_dump(mode="json"), effective_from, approved_by)`; `record(..., action="policy.override_created", entity_type="tenant_override_version", entity_id=str(row.id), detail={"version": version, "effective_from": effective_from.isoformat()})`; return `version`.

- [ ] **Step 1: Write the failing tests.** `tests/golden/packs/effective.yaml`:

```yaml
assignments:   # D21: a tenant-wide assignment never overlaps a product one, so the ITF pack is assigned per non-crypto product
  - {pack: MX/ITF-IFPE, version: 1, product: EMONEY, effective_from: 2026-01-01}
  - {pack: MX/ITF-IFPE, version: 1, product: CROWDFUNDING, effective_from: 2026-01-01}
  - {pack: MX/LFPIORPI-AVI, version: 1, product: CRYPTO, effective_from: 2026-11-30}
override:          # created with clock.now() frozen at 2026-02-01T12:00:00Z, so effective_from is 2026-02-01 and the union it is validated against is the ITF pack alone
  tier_limits: {N1: {monthly_ops: {amount: "500", unit: UDI}}}
  capture_thresholds: {ITF_ROR_CASH: {amount: "4000", unit: USD}}
  retention: {kyc_after_relationship: P12Y}
  mandatory_lists_add: [OFAC_SDN]
  bands: {sanctions_alert: "0.65"}
  committee_required: true
cases:
  - {id: itf-only-emoney, on: 2026-01-20, product: EMONEY, expect: {pack_codes: [MX/ITF-IFPE], override_version: null, tier_codes: [N1, N2, N3], tier_N1_monthly_ops: {amount: "750", unit: UDI}, capture_ITF_ROR_CASH: {amount: "5000", unit: USD}, retention_kyc: P10Y, retention_transactions: P10Y, committee_required: true, geolocation_required: true, mandatory_lists: [MX_LPB, UN_CONSOLIDATED], recommended_lists: [EU_FSF, MX_SAT_69B, OFAC_SDN, UK_FCDO], capture_codes: [ITF_FX_CASH, ITF_INTL_TRANSFER, ITF_ROR_CASH, ITF_VA_OPS], report_codes: [MX_F41_ROI, MX_F41_ROI24, MX_F41_ROR, MX_ITF_INTL_TRANSFER], sanctions_alert: "0.70", tier_limit_rule: LAST_DAY_PREV_MONTH, report_MX_F41_ROR_deadline: {kind: NTH_BUSINESS_DAY_OF, params: {month_offset: 1, n: 10}, tz: America/Mexico_City}}}
  - {id: before-the-override, on: 2026-01-20, product: CROWDFUNDING, expect: {pack_codes: [MX/ITF-IFPE], override_version: null, tier_N1_monthly_ops: {amount: "750", unit: UDI}, sanctions_alert: "0.70", mandatory_lists: [MX_LPB, UN_CONSOLIDATED]}}
  - {id: with-the-override, on: 2026-02-15, product: EMONEY, expect: {pack_codes: [MX/ITF-IFPE], override_version: 1, tier_N1_monthly_ops: {amount: "500", unit: UDI}, tier_N1_monthly_ops_basis: "tenant override v1", capture_ITF_ROR_CASH: {amount: "4000", unit: USD}, retention_kyc: P12Y, mandatory_lists: [MX_LPB, UN_CONSOLIDATED, OFAC_SDN], sanctions_alert: "0.65", committee_required: true}}
  - {id: crypto-before-avi-is-in-force, on: 2026-11-29, product: CRYPTO, error: POLICY_NOT_ASSIGNED}
  - {id: avi-only-for-crypto, on: 2026-12-01, product: CRYPTO, expect: {pack_codes: [MX/LFPIORPI-AVI], override_version: 1, tier_codes: [], capture_codes: [AVI_XVI_A, AVI_XVI_B], report_codes: [MX_AVISO_AVI], capture_AVI_XVI_A: {amount: "210", unit: UMA}, retention_kyc: P12Y, retention_transactions: P5Y, committee_required: true, geolocation_required: false, mandatory_lists: [MX_LPB, UN_CONSOLIDATED, OFAC_SDN], recommended_lists: [MX_SAT_69B, OFAC_SDN], sanctions_alert: "0.65", tier_limit_rule: null, report_MX_AVISO_AVI_deadline: {kind: DAY_OF_NEXT_MONTH, params: {day: 17, roll: NEXT_BUSINESS_DAY}, tz: America/Mexico_City}}}
  - {id: no-product-sees-only-tenant-wide-assignments, on: 2026-12-01, product: null, error: POLICY_NOT_ASSIGNED}
  - {id: nothing-assigned-yet, on: 2025-12-31, product: EMONEY, error: POLICY_NOT_ASSIGNED}
```

(`avi-only-for-crypto` shows the override of 2026-02-01 applied to a pack that came into force later: its tier and ITF capture entries are skipped because the AVI body has no tiers and no `ITF_ROR_CASH`, and its retention, list, band and committee entries apply because they are stricter than the AVI values.)

`tests/golden/packs/test_effective_goldens.py` (database): `tests/platform/policy_support.py` (created here) holds `install_both_packs(admin_engine) -> dict[str, UUID]` (both packs through an owner session, `load_pack(..., allow_unverified=True)` + `install_pack`, `MX/ITF-IFPE` first and `MX/LFPIORPI-AVI` second in one transaction; version ids by pack code) and `load_golden() -> dict` over `effective.yaml`; the module's fixtures are `installed` (that call), `assigned` (`tenant = new_tenant()`, then `assign_pack` per `assignments` under `staff_session(tenant.tenant_id, tenant.admin_user_id)` with `approved_by=tenant.admin_user_id`) and `overridden` (`monkeypatch.setattr(clock, "now", lambda: datetime(2026, 2, 1, 12, tzinfo=UTC))`, then `create_override(s, TenantOverride.model_validate(golden["override"]), approved_by=tenant.admin_user_id)`); one parametrised test over `cases` (ids from `id`) calls `effective_policy(s, on=..., product=...)` under the staff session and compares each `expect` key: `pack_codes` → `policy.pack_codes` (in order), `override_version`, `tier_codes` → `[t.code for t in policy.kyc.tiers]`, `tier_N1_monthly_ops` → `policy.kyc.tiers[N1].limits.monthly_ops.value == Quantity.model_validate(expected)`, `…_basis` → its `legal_basis`, `capture_X` → `captures[X].threshold.value == Quantity.model_validate(expected)`, `retention_kyc` / `retention_transactions` → `retention.<field>.value` as strings, `sanctions_alert` → `screening.bands.sanctions_alert == Decimal(expected)` (never a string comparison: `PlainDecimal` turns `"0.70"` into `Decimal("0.7")`), `committee_required` / `geolocation_required` → `.value`, `mandatory_lists` → `[p.value for p in …]` in order (overrides append), `recommended_lists`, `capture_codes` and `report_codes` compared sorted, `tier_limit_rule` → `units.tier_limit_rule.value if units.tier_limit_rule else None`, `report_X_deadline` → `reports[X].deadline.value.model_dump(mode="json")`; `error` cases assert `NotFoundError` with `.code == "POLICY_NOT_ASSIGNED"`. A failing case shows pytest's diff of that one value (Deviations). `tests/platform/test_policy_service.py` (the same `installed` and `assigned` fixtures, copied, without `overridden`: each test creates its own overrides, so the first one it creates is version 1; `clock.now` is frozen at `2026-02-01T12:00:00Z` unless a test says otherwise): `test_merge_packs_takes_the_strictest_of_each_value` (no database: `merge_packs([itf, avi])` of the two loaded packs → `retention.kyc_after_relationship.value == "P10Y"`, `retention.transactions.value == "P10Y"`, `kyc.geolocation_required.value is True`, `governance.committee_required.value is True`, `[t.code for t in kyc.tiers] == ["N1", "N2", "N3"]`, `sorted(c.code for c in captures)` is the six codes of both packs, `sorted(r.code for r in reports)` the five, `screening.recommended_lists == ["EU_FSF", "MX_SAT_69B", "OFAC_SDN", "UK_FCDO"]`, `[p.value for p in screening.mandatory_lists] == ["MX_LPB", "UN_CONSOLIDATED"]`, `screening.pep.cooling_off["FOREIGN"].value is None`, `screening.on_true_match["MX_LPB"].action == "SUSPEND_IMMEDIATELY"` with `report == "MX_F41_ROI24"`, `units.tier_limit_rule.value == ConversionRule.LAST_DAY_PREV_MONTH`, `ubo.ownership_threshold_pct.value == Decimal("25")`; and `merge_packs([itf])` equals the ITF body field by field); `test_overrides_that_loosen_are_refused` parametrised (`pytest.param(override_dict, code, fragment, id=...)`): `{"tier_limits": {"N1": {"monthly_ops": {"amount": "800", "unit": "UDI"}}}}` → `POLICY_LOOSENING`, `"kyc.tiers[N1].limits.monthly_ops"`; `{"capture_thresholds": {"ITF_ROR_CASH": {"amount": "6000", "unit": "USD"}}}` → `POLICY_LOOSENING`; `{"retention": {"kyc_after_relationship": "P5Y"}}` → `POLICY_LOOSENING`; `{"review_frequency": {"HIGH": "P12M"}}` → `POLICY_LOOSENING`; `{"review_frequency": {"MEDIUM": "P13M"}}` → `POLICY_LOOSENING`; `{"bands": {"sanctions_alert": "0.75"}}` → `POLICY_LOOSENING`; `{"bands": {"id_match_floor": "0.90"}}` → `POLICY_LOOSENING`; `{"committee_required": False}` → `POLICY_LOOSENING`; `{"geolocation_required": False}` → `POLICY_LOOSENING`; `{"ubo_ownership_threshold_pct": "30"}` → `POLICY_LOOSENING`; `{"tier_limits": {"N1": {"monthly_ops": {"amount": "500", "unit": "USD"}}}}` → `VALIDATION_FAILED`; `{"tier_limits": {"N9": {"monthly_ops": {"amount": "1", "unit": "UDI"}}}}` → `VALIDATION_FAILED`; `{"capture_thresholds": {"NOPE": {"amount": "1", "unit": "USD"}}}` → `VALIDATION_FAILED`; `{"bands": {"sanctions_alert": "0.50"}}` → `VALIDATION_FAILED` (below the 0.55 review band); `test_equal_values_and_different_spellings_are_accepted` (`{"review_frequency": {"MEDIUM": "P1Y", "HIGH": "P180D"}}` creates version 1); `test_an_override_is_validated_against_every_product_in_force` (with `clock.now` frozen at `2026-12-01T12:00:00Z` the union of the packs in force holds the AVI pack (CRYPTO) beside the ITF pack, so `{"capture_thresholds": {"AVI_XVI_A": {"amount": "300", "unit": "UMA"}}}` → `POLICY_LOOSENING` naming `captures[AVI_XVI_A].threshold`, and `{"capture_thresholds": {"AVI_XVI_A": {"amount": "200", "unit": "UMA"}}, "tier_limits": {"N1": {"monthly_ops": {"amount": "700", "unit": "UDI"}}}}` (one key from each pack) is accepted; the first override with the clock at `2026-02-01` → `VALIDATION_FAILED`, unknown capture, because the AVI version is not in force yet); `test_override_versions_count_up_and_are_audited` (two overrides → versions 1 and 2; `audit_events` has two `policy.override_created` rows with `detail == {"version": n, "effective_from": "2026-02-01"}`); `test_assignments_supersede_and_refuse_overlaps` (a fresh tenant: assign the ITF pack tenant-wide from 2026-01-01, then from 2026-06-01 → the first row's `effective_to == date(2026, 5, 31)` and `closed_assignment_id` in the audit detail; assigning from 2026-01-01 again → `DomainError` `CONFLICT`; from 2025-06-01 → `CONFLICT`; the AVI pack for CRYPTO beside the tenant-wide one → `CONFLICT` whose detail names D21, and the ITF pack itself for CRYPTO → the same `CONFLICT` (the rule is about overlap, not packs); on a second fresh tenant the ITF pack for EMONEY and the AVI pack for CRYPTO from the same date → both accepted, then a tenant-wide assignment → `CONFLICT`; an unknown version id → `NotFoundError`); `test_pack_assigned_is_audited` (`detail == {"pack": "MX/ITF-IFPE", "version": 1, "pack_version_id": str(vid), "product": None, "effective_from": "2026-01-01", "closed_assignment_id": None}`, `verify_chain(s).valid`); `test_snapshots_are_deduplicated_and_frozen` (`snapshot_policy` twice → same id and one row; `get_snapshot(...).policy == effective`; `content_hash` is 64 hex; `contains_float(row.content) is False`; a second tenant cannot read it → `NotFoundError`; a different `on` gives a different id); `test_create_override_without_a_pack_is_404`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/golden/packs/test_effective_goldens.py tests/platform/test_policy_service.py -q --no-cov` → FAIL (`xolotl.platform.policy.resolver` missing).
- [ ] **Step 3: Implement** the four modules and the exports (`TenantOverride`, `TierLimitsOverride`, `BandsOverride`, `RetentionOverride`, `validate_against`, `apply_override`, `EffectivePolicy`, `merge_packs`, `effective_policy`, `PolicySnapshotContent`, `snapshot_policy`, `get_snapshot`, `assignments_on`, `assignments_in_force`, `assign_pack`, `create_override`), and `tests/platform/policy_support.py`.
- [ ] **Step 4: Run to verify pass.** The two test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(policy): pack assignments, tighten-only overrides, the effective-policy resolver and canonical snapshots`.

---

### Task A8: Routes — policy, units, calendar, regulatory profile and branches

**Files:**
- Create: `src/xolotl/modules/tenants/schemas_policy.py`, `src/xolotl/modules/tenants/policy_service.py`, `src/xolotl/modules/tenants/routes_policy.py`, `src/xolotl/modules/tenants/routes_units.py`
- Modify: `src/xolotl/app.py` (`routes_policy.router`, `routes_units.router` after `routes_webhooks.router`), `README.md` (section "Policy packs, units and calendar": loading packs, seeding units and holidays, the routes, the daily fetch job)
- Test: `tests/modules/tenants/test_policy_routes.py`, `tests/modules/tenants/test_units_routes.py`, `tests/modules/tenants/test_regulatory_profile_routes.py`

**Interfaces:**
- Consumes: everything A7 exports; `UnitService`, `unit_summaries`, `history` (A4); `UnitValueMissingError`, `ConversionRule` (A2); `RFC_SHAPE`, `mask_tail` (A1, `xolotl.platform.text`); `load_holidays`, `Holiday`, `HolidayScope`, `DeadlineRule`, `due_at` (A3); `TenantRegulatoryProfile`, `TenantBranch`, `BranchStatus`, `StaffUser`; `v1_router`, `require_access`, `SessionDep`, `Actor`, `Role`, `PageDep`, `Page`, `paginate`, `record`, `AuditEvent`, `encrypt`, `bound_context`, `clock.now`.
- Produces (schemas in `schemas_policy.py`, `from_attributes=True` where a row is validated):

```python
class PackVersionResponse: id: UUID; code: str; version: int; title: str; effective_from: date; effective_to: date | None; content_hash: str; loaded_at: datetime
class ConversionResponse: amount: Decimal; currency: str; unit: str; rate: Decimal; unit_value_id: int | None; rule: ConversionRule; resolved_date: date
class EffectivePolicyResponse: policy: EffectivePolicy; snapshot_id: UUID; content_hash: str; converted: dict[str, ConversionResponse | None]; missing_units: list[str]
class AssignmentCreateRequest: pack_version_id: UUID; product: str | None = Field(default=None, min_length=1, max_length=64); effective_from: date
class AssignmentResponse: id: UUID; pack_version_id: UUID; pack: str; version: int; product: str | None; effective_from: date; effective_to: date | None; approved_by: UUID; created_at: datetime
class OverrideResponse: id: UUID; version: int; overrides: TenantOverride; effective_from: date; approved_by: UUID; created_at: datetime
class RegulatoryProfileRequest: rfc: str (1..13); legal_name: str (1..200); casfim_supervisor: str | None (..64); casfim_entity_key: str | None (..64); spplld_activity_key: str | None (..64); platform_domain: str | None (..253); entidad_colegiada: bool = False; compliance_officer_user_id: UUID | None = None
class RegulatoryProfileResponse: id: UUID; rfc_masked: str; legal_name: str; casfim_supervisor; casfim_entity_key; spplld_activity_key; platform_domain; entidad_colegiada: bool; compliance_officer_user_id: UUID | None; created_at: datetime; updated_at: datetime
class BranchCreateRequest: code: str (1..32); name: str (1..200); address: str (1..500, Field(json_schema_extra={"pii": True})); state_code: str (2); municipality_code: str (3)
class BranchResponse: id: UUID; code: str; name: str; state_code: str; municipality_code: str; status: BranchStatus; created_at: datetime
class UnitSummaryResponse: unit_code; quote_ccy; daily: bool; series_id: str | None; latest_valid_from: date | None; latest_fetched_at: datetime | None; latest_source: str | None
class UnitsResponse: items: list[UnitSummaryResponse]
class UnitLookupResponse: unit_code: str; on: date; rule: ConversionRule; resolved_date: date; value: Decimal; quote_ccy: str; unit_value_id: int; source: str; fetched_at: datetime
class UnitValueResponse: id: int; unit_code; valid_from; valid_to; value: Decimal; quote_ccy; source; fetched_at; superseded_by: int | None
class UnitValuesResponse: unit_code: str; items: list[UnitValueResponse]
class HolidayResponse: day: date; name: str; source: str
class HolidaysResponse: country: str; scope: HolidayScope; year: int; items: list[HolidayResponse]
class DueResponse: due_at: datetime; rule: DeadlineRule; anchor: datetime; scope: HolidayScope
```

`routes_policy.py` (`router = v1_router(tags=["policy"])`; `StaffReader = Annotated[Actor, Depends(require_access())]`, `AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]`): `GET /policy/packs` → `list_policy_packs(_reader: StaffReader, session, page) -> Page[PackVersionResponse]` (`paginate` over `select(PolicyPackVersion)` with `ts_col=PolicyPackVersion.loaded_at`, `id_col=PolicyPackVersion.id`; the codes and titles come from one `select(PolicyPack)` keyed by id); `GET /policy/effective?on=&product=` → `get_effective_policy(_reader, session, on: date | None = None, product: str | None = None) -> EffectivePolicyResponse` (`on` defaults to `clock.now().date()`); `POST /policy/assignments` 201 → `create_pack_assignment(body: AssignmentCreateRequest, actor: AdminWriter, session) -> AssignmentResponse` (`approved_by=UUID(actor.context.actor_id)`); `GET /policy/assignments` → `list_pack_assignments(...) -> Page[AssignmentResponse]`; `POST /policy/overrides` 201 with body `TenantOverride` → `create_policy_override(...) -> OverrideResponse`; `GET /policy/overrides` → `list_policy_overrides(...) -> Page[OverrideResponse]`; `GET /tenants/me/regulatory-profile` → `get_regulatory_profile(_reader, session) -> RegulatoryProfileResponse` (404 `NOT_FOUND` when unset); `PUT /tenants/me/regulatory-profile` → `set_regulatory_profile(body, _admin: AdminWriter, session) -> RegulatoryProfileResponse` (200); `POST /tenants/me/branches` 201 → `create_branch(body, _admin, session) -> BranchResponse`; `GET /tenants/me/branches` → `list_branches(_reader, session, page) -> Page[BranchResponse]`. `routes_units.py` (`router = v1_router(tags=["units"])`): `GET /units` → `list_units(_reader, session) -> UnitsResponse`; `GET /units/{code}?on=&rule=` → `lookup_unit(code: str, _reader, session, on: date | None = None, rule: ConversionRule = ConversionRule.SAME_DAY) -> UnitLookupResponse` (503 `UNIT_VALUE_MISSING` when missing; 422 for an unknown code); `GET /units/{code}/values?from=&to=` → `list_unit_values(code, _reader, session, from_: Annotated[date | None, Query(alias="from")] = None, to: date | None = None) -> UnitValuesResponse` (`to` defaults to today, `from` to `to - 90 days`; a span over 366 days is 422); `GET /calendar/holidays?scope=&year=` → `list_holidays(_reader, session, scope: HolidayScope = HolidayScope.NATIONAL, year: int | None = None) -> HolidaysResponse`; `GET /calendar/due?rule=&anchor=&scope=` → `evaluate_deadline(_reader, session, rule: Annotated[str, Query(max_length=1024)], anchor: datetime, scope: HolidayScope = HolidayScope.NATIONAL) -> DueResponse` (`DeadlineRule.model_validate_json(rule)`, a pydantic error → `ValidationFailedError("invalid rule")`; a naive `anchor` → `ValidationFailedError("anchor must carry an offset")`).

`policy_service.py`: `effective_with_conversions(s, *, on, product) -> EffectivePolicyResponse` (`effective_policy`, `snapshot_policy`, `get_snapshot` for the hex hash; `UnitService(s)`; for every tier limit present, key `f"kyc.tiers[{code}].limits.{name}"` converted with `threshold_in_ccy(limit.value, "MXN", on, units.tier_limit_rule.value or ConversionRule.SAME_DAY)`; for every capture, key `f"captures[{code}].threshold"` with `capture.conversion_rule`; `UnitValueMissingError` → `None` and the unit's `unit_code` appended to `missing_units` (sorted, unique); a `ValidationFailedError` from `PREV_MONTH_AVG` is treated the same way with the unit listed); `set_regulatory_profile(s, body) -> RegulatoryProfileResponse` (`rfc = body.rfc.strip().upper()`, `RFC_SHAPE.fullmatch(rfc)` (A1's `xolotl.platform.text`) else `ValidationFailedError("invalid RFC")`; `compliance_officer_user_id` must be a `StaffUser` of the tenant else `ValidationFailedError("unknown compliance officer")`; the tenant's single row is selected `with_for_update` and updated, or created; the `rfc` column holds `encrypt(rfc, tenant_id=tenant_id, session=s)`; `record(s, AuditEvent(action="tenant.regulatory_profile_set", entity_type="tenant_regulatory_profile", entity_id=str(row.id), detail={"fields": sorted(name for name, value in body.model_dump().items() if value not in (None, False) and name != "rfc")}))`; response with `rfc_masked = mask_tail(rfc)`); `get_regulatory_profile(s)` (`rfc_masked = mask_tail(row.rfc.reveal(session=s))`, processing only, no audit); `create_branch(s, body) -> BranchResponse` (duplicate `code` → `DomainError("branch code already exists")`; the `address` column holds `encrypt(body.address, ...)`; `record(..., action="tenant.branch_created", entity_type="tenant_branch", entity_id=str(row.id), detail={"code": row.code})`); `branch_response(row)` builds the model field by field (the row holds an `Encrypted`). The two routers are added to `build_app`'s list after `routes_webhooks.router`.

- [ ] **Step 1: Write the failing tests.** Shared helpers in `tests/modules/tenants/policy_support.py` (plain functions the three test modules call from their own fixtures): `packs_installed(admin_engine) -> dict[str, UUID]` (delegates to A7's `tests.platform.policy_support.install_both_packs`, which installs `MX/ITF-IFPE` and then `MX/LFPIORPI-AVI` in one owner transaction, so their `loaded_at` tie and the uuid7 `id` orders the catalogue), `holidays_seeded(admin_engine)` (A4's `tests.platform.units_support.seed_mx_holidays`), `unit_values_loaded(engine) -> dict[tuple[str, date], int]` (A4's `load_unit_values` over `tests/golden/units/values.yaml`, under `tenant_session(platform_ctx())`). `test_policy_routes.py`: `test_the_catalogue_lists_both_packs` (`GET /v1/policy/packs` → `items` with codes `["MX/LFPIORPI-AVI", "MX/ITF-IFPE"]`, the `(loaded_at, id)` descending order of `paginate` with the AVI pack installed second, `content_hash` 64 hex); `test_assign_then_effective_converts_n1_with_the_previous_month_end_udi` (`POST /v1/policy/assignments` `{"pack_version_id": ids["MX/ITF-IFPE"], "product": None, "effective_from": "2026-01-01"}` with `Idempotency-Key: a1` → 201 and `approved_by == str(tenant.admin_user_id)`; `GET /v1/policy/effective?on=2026-01-20&product=EMONEY` → 200, `body["policy"]["pack_codes"] == ["MX/ITF-IFPE"]`, `body["converted"]["kyc.tiers[N1].limits.monthly_ops"] == {"amount": "6390.00", "currency": "MXN", "unit": "UDI", "rate": "8.52000000", "unit_value_id": loaded[("UDI", date(2025, 12, 31))], "rule": "LAST_DAY_PREV_MONTH", "resolved_date": "2025-12-31"}`, `body["converted"]["captures[ITF_ROR_CASH].threshold"]["amount"] == "92500.00"` with `resolved_date == "2026-01-19"` (the FIX of the prior banking day), `body["converted"]["captures[ITF_VA_OPS].threshold"]["amount"] == "64575.00"` with `resolved_date == "2026-01-20"` (7,500 UDI at the fixture's same-day 8.61), `body["missing_units"] == []`, `body["snapshot_id"]` is a UUID and a second GET returns the same `snapshot_id`; `GET …?on=2026-01-21&product=EMONEY` → `converted["captures[ITF_ROR_CASH].threshold"] is None`, `converted["captures[ITF_VA_OPS].threshold"]["amount"] == "64650.00"` (the 8.62 of 2026-01-21) and `missing_units == ["USD_MXN_FIX"]` (the fixture has no FIX row for 2026-01-20 but UDI rows for both days); the audit chain holds `policy.pack_assigned`); `test_effective_without_an_assignment_is_404_policy_not_assigned`; `test_a_replayed_assignment_does_not_assign_twice` (same key → `Idempotent-Replay: true`, one row); `test_assignments_are_admin_only_and_need_a_key` (an ANALYST → 403 `ROLE_FORBIDDEN`; no key → 400 `IDEMPOTENCY_KEY_REQUIRED`; an API key with `tenants:read` → 403 `ROLE_FORBIDDEN` on the GETs too); `test_overrides_tighten_only_through_the_api` (`POST /v1/policy/overrides` `{"tier_limits": {"N1": {"monthly_ops": {"amount": "500", "unit": "UDI"}}}}` → 201 `version == 1`; `{"committee_required": false}` → 409 `POLICY_LOOSENING`; `GET /v1/policy/overrides` → one item whose `overrides.tier_limits.N1.monthly_ops.amount == "500"`; no band is asserted as a string, `PlainDecimal` serialises `"0.70"` as `"0.7"`, so compare `Decimal(...)` wherever a band is checked). `test_units_routes.py`: `GET /v1/units` → three items, `UDI.latest_valid_from == "2026-01-21"`; `GET /v1/units/UDI?on=2026-01-20&rule=LAST_DAY_PREV_MONTH` → `resolved_date == "2025-12-31"`, `value == "8.52000000"`; `GET /v1/units/UDI?on=2026-02-05&rule=LAST_DAY_PREV_MONTH` → 503 `UNIT_VALUE_MISSING` with `content-type: application/problem+json`; `GET /v1/units/TIIE` → 422; `GET /v1/units/USD_MXN_FIX/values?from=2025-12-01&to=2025-12-31` → two rows for `2025-12-11`, the older with `superseded_by` set; a 400-day span → 422; `GET /v1/calendar/holidays?scope=CNBV&year=2026` → `items` contains `{"day": "2026-04-02", "name": "Jueves Santo", "source": "CNBV 2026"}` and not `2026-05-05`; `GET /v1/calendar/due?rule=<json>&anchor=2025-12-31T23:59:59-06:00&scope=CNBV` → `due_at == "2026-01-15T23:59:59-06:00"`; `rule=nope` → 422; `anchor=2025-12-31T23:59:59` → 422. `test_regulatory_profile_routes.py`: `PUT /v1/tenants/me/regulatory-profile` `{"rfc": "acm010101ab1", "legal_name": "Acme SA de CV", "casfim_supervisor": "CNBV", "entidad_colegiada": false}` → 200 with `rfc_masked == "****0AB1"` and no `rfc` key; `GET` → the same; a second `PUT` with another `legal_name` keeps one row (`everything_stored` counts) and bumps `updated_at`; `GET` before any `PUT` → 404; `rfc: "nope"` → 422; `compliance_officer_user_id` of a random UUID → 422, of the admin → 200; the ANALYST → 403; `audit_events` holds `("tenant.regulatory_profile_set", {"fields": ["casfim_supervisor", "legal_name"]})`; `leaked(everything_stored(admin_engine), b"ACM010101AB1") == []` and `caplog` at DEBUG with `sqlalchemy.engine` shows no `ACM010101AB1`. Branches: `POST /v1/tenants/me/branches` `{"code": "MAT", "name": "Matriz", "address": "Av. Reforma 1, CDMX", "state_code": "09", "municipality_code": "015"}` → 201 with `status == "ACTIVE"` and no `address` key; the same code again → 409 `CONFLICT`; `GET` → `items[0]["code"] == "MAT"`; `leaked(everything_stored(admin_engine), b"Av. Reforma 1") == []`; `audit_events` holds `("tenant.branch_created", {"code": "MAT"})`. The conventions walkers (`test_every_post_route_under_v1_is_idempotent_except_auth`, `test_every_v1_route_declares_an_access_rule`, `test_openapi_lives_under_v1_with_function_operation_ids`) need no edit.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/tenants/test_policy_routes.py tests/modules/tenants/test_units_routes.py tests/modules/tenants/test_regulatory_profile_routes.py -q --no-cov` → FAIL (404 `NOT_FOUND` on every new path).
- [ ] **Step 3: Implement** the schemas, the service, the two routers, the registration, the README section.
- [ ] **Step 4: Run to verify pass.** The three test files plus `tests/platform/test_api_conventions.py tests/platform/test_principal.py`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(tenants): policy, units, calendar, regulatory-profile and branch routes`.

---

### Task A9: Console — pack viewer, navigation, strings, the smoke step and CI seeding

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `console/src/i18n/en.json`, `console/src/i18n/catalogue.test.ts`, `console/e2e/smoke.spec.ts`, `.github/workflows/ci.yml`
- Create: `console/src/features/policy/PolicyPage.tsx`, `queries.ts`, `provenance.ts`, `provenance.test.ts`, `PolicyPage.test.tsx`

**Interfaces:**
- Consumes: the A8 routes through the generated `ApiPaths`; `api`, `unwrap`, `useAuth`, `PAGE_SIZE`, `PageHeader`, `DataTable`, `Column`, `CursorPager`, `DateTimeCell`, `ErrorBlock`, `EmptyBlock`, `KeyValues`, `Badge` (variants `good`, `warning` and `muted` exist in `components/ui/badge.tsx`), `Input`, `Field`, `Button`, `Card*`, `useFormat` (`@/i18n/use-locale`), `hasProblemCode`, `renderWithProviders`/`fakeApi`, `renderApp`/`fake-network`.
- Produces: route `/policy` (lazy, inside the `AppShell` group, no `RequireRole`: every signed-in role may read it, as the API allows); `NavItem.labelKey` widened with `'nav.policy'` and one `NAV_ITEMS` entry (`icon: BookOpen` from `lucide-react`, placed after `nav.audit`); type aliases in `types.ts` for every A8 schema, so A10 adds none: `EffectivePolicyResponse`, `EffectivePolicy`, `ConversionResponse`, `PackVersionPage = Schemas['Page_PackVersionResponse_']`, `AssignmentPage = Schemas['Page_AssignmentResponse_']`, `OverridePage = Schemas['Page_OverrideResponse_']`, `UnitsResponse`, `UnitLookupResponse`, `UnitValuesResponse`, `HolidaysResponse`, `DueResponse`, `DeadlineRule`, `DeadlineKind`, `ConversionRule`, `HolidayScope`, `Quantity`, `Confidence`; hooks `useEffectivePolicy(on, product)`, `usePackVersions(cursor)`, `useAssignments(cursor)`, `useOverrides(cursor)` in `features/policy/queries.ts`, all `useQuery` with `enabled: user !== undefined && !mustChangePassword` (none of these answers holds PII); pure helpers in `provenance.ts`: `formatQuantity(q: Quantity): string` (`"750 UDI"`), `describeConversion(c: ConversionResponse | null | undefined, format: ReturnType<typeof useFormat>): string` (`"6,390.00 MXN · UDI 2025-12-31"` or `"—"`), `provenanceOf(p: { confidence: Confidence; legal_basis: string; verified_as_of: string }): { confidence: Confidence; legalBasis: string; verifiedAsOf: string }`; `ProvenanceBadge` is a component local to `PolicyPage.tsx` (not exported): a `Badge` `good` for VERIFIED, `warning` for UNVERIFIED, with the legal basis and `verified_as_of` in its `title`.

Page: **PolicyPage** (`/policy`): a filter row (`on` date input defaulting to today, `product` text input, submit) feeding `useEffectivePolicy`; a header card with the pack codes, `override_version` and the snapshot hash (`font-mono`, first 12 characters, full value in `title`); a Tiers table (code, monthly ops via `formatQuantity`, converted via `describeConversion(converted["kyc.tiers[<code>].limits.monthly_ops"], format)`, max balance and its conversion, assurance); a Captures table (code, kind, threshold, conversion rule, period, report, filing, converted); Lists (mandatory and recommended as `Badge`s); Retention and Governance `KeyValues`; every `Provenanced` value shows a `ProvenanceBadge`; a `missing_units` notice (`policy.missingUnits`, `{{units}}`) when non-empty; below, Assignments (`useAssignments`: pack, version, product, effective from/to, approved by, created at, `CursorPager`) and Overrides (`useOverrides`: version, effective from, approved by, a JSON summary of the override keys). A 404 `POLICY_NOT_ASSIGNED` renders `policy.notAssigned` as an `EmptyBlock`-style message (not an error block).

Strings (both catalogues, same keys; `SAME_IN_BOTH` gains nothing because pack codes and unit codes render raw): `nav.policy` (`Políticas` / `Policy`); `policy.*`: `title`, `description`, `filters.on`, `filters.product`, `filters.apply`, `packs`, `overrideVersion`, `snapshot`, `tiers.title`, `tiers.code`, `tiers.monthlyOps`, `tiers.maxBalance`, `tiers.converted`, `tiers.assurance`, `captures.title`, `captures.code`, `captures.kind`, `captures.threshold`, `captures.rule`, `captures.period`, `captures.report`, `captures.filing`, `lists.mandatory`, `lists.recommended`, `retention.title`, `retention.kyc`, `retention.transactions`, `governance.title`, `governance.committee`, `governance.fourEyes`, `provenance.VERIFIED`, `provenance.UNVERIFIED`, `provenance.basis`, `missingUnits`, `notAssigned`, `assignments.title`, `assignments.pack`, `assignments.version`, `assignments.product`, `assignments.allProducts`, `assignments.from`, `assignments.to`, `assignments.approvedBy`, `assignments.createdAt`, `assignments.empty`, `overrides.title`, `overrides.version`, `overrides.from`, `overrides.approvedBy`, `overrides.keys`, `overrides.empty`, `noConversion`; `errors.UNIT_VALUE_MISSING`, `errors.POLICY_NOT_ASSIGNED`, `errors.POLICY_LOOSENING` (all three added to `catalogue.test.ts`'s code list here, in one change; A10's units page reads `errors.UNIT_VALUE_MISSING`).

Smoke (`console/e2e/smoke.spec.ts`, one `test.step('the pack viewer shows the ITF tier limits')` after the step `a wide audit table scrolls inside its card, not the page` and before `a session the server has ended …`, which rotates the password): reuse the page's bearer the way that later step does (`const token = await page.evaluate(() => window.sessionStorage.getItem('xolotl.token'))`, `expect(token).toBeTruthy()`), so there is no second `/v1/auth/login` (its body would be `{ tenant_slug, email, password }`) and no second TOTP code; `const packs = await request.get('/v1/policy/packs', { headers: { Authorization: `Bearer ${token}` } })`, take the item with `code === 'MX/ITF-IFPE'` and `version === 1`; `const assigned = await request.post('/v1/policy/assignments', { headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': 'e2e-assign-itf' }, data: { pack_version_id, product: null, effective_from: '2026-01-01' } })`, `expect(assigned.status()).toBe(201)`; then `await page.getByRole('link', { name: es.nav.policy, exact: true }).click()`, and assert `page.getByRole('cell', { name: '750 UDI', exact: true })` and `page.getByRole('cell', { name: '1000 UDI', exact: true })` are visible and `page.getByText('MX/ITF-IFPE')` is visible (the viewer's default `on` is today, for which the seeds hold no UDI row: the `missing_units` notice shows and the tier cells still render). `.github/workflows/ci.yml`: after the `tenant create` step add `docker compose run --rm -T api xolotl packs load --allow-unverified`, `docker compose run --rm -T api xolotl holidays seed` and `docker compose run --rm -T api xolotl units seed --allow-unverified` (the compose `api` service runs with `XOLOTL_ENV: dev`, so the flag is accepted).

- [ ] **Step 1: Regenerate the types and write the failing tests.** From the repository root `uv run python -c "import json; from xolotl.app import build_app; print(json.dumps(build_app().openapi()))" > console/openapi.json && cd console && npm run gen:api:file`; commit the new `schema.d.ts`. `provenance.test.ts`: `formatQuantity({amount: '750', unit: 'UDI'}) === '750 UDI'`; `describeConversion(null, format) === '—'`; `describeConversion({amount: '6390.00', currency: 'MXN', unit: 'UDI', rate: '8.52000000', unit_value_id: 7, rule: 'LAST_DAY_PREV_MONTH', resolved_date: '2025-12-31'}, format)` contains `6,390.00` and `2025-12-31` (`format` from `renderHook(() => useFormat())` under the providers, or a stub of the same shape); `provenanceOf` maps the three fields. `PolicyPage.test.tsx` (`renderWithProviders` + `fakeApi`): states (skeleton `aria-busy`, the `policy.notAssigned` message on `refuse(404, 'POLICY_NOT_ASSIGNED')`, `esMX.errors.INTERNAL` with `req-test` on a 500 and a retry that refetches); the effective view renders the N1 row with `750 UDI` and `6,390.00` from a fixture `EffectivePolicyResponse` (built in the test with both tiers, one capture, lists, retention, governance, `converted` for N1 and `missing_units: ['USD_MXN_FIX']` → the notice shows `USD_MXN_FIX`); an UNVERIFIED value shows the `warning` badge with its legal basis in the `title`; a filter submit calls `/policy/effective` with `on=2026-01-20&product=EMONEY`; assignments paging (`cursor` sequence `[undefined, 'cursor-2']`); a pack title `'<img src=x onerror="window.__pwned=1">'` renders as text (`document.body.querySelector('img')` null); the language switcher.
- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck && npx vitest run src/features/policy` → FAIL (modules missing).
- [ ] **Step 3: Implement** the page, hooks, helpers, nav entry, route, catalogue keys, the smoke step and the CI seeding steps.
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; then the smoke locally (`docker compose up -d --build --wait`, `tenant create`, the three seed commands, `E2E_ADMIN_PASSWORD=… npm run test:e2e`); `make ci` stays green (no Python changed, but `tests/test_render_blueprint.py` and the CI workflow are re-read).
- [ ] **Step 5: Commit** `feat(console): pack viewer with provenance, the policy smoke step and CI seeding`.

---

### Task A10: Console — units and calendar pages

**Files:**
- Modify: `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `console/src/i18n/en.json` (`console/src/api/types.ts` only if A9 left an alias out)
- Create: `console/src/features/units/UnitsPage.tsx`, `queries.ts`, `UnitsPage.test.tsx`; `console/src/features/calendar/CalendarPage.tsx`, `queries.ts`, `deadline-form.ts`, `deadline-form.test.ts`, `CalendarPage.test.tsx`

**Interfaces:**
- Consumes: A9's type aliases and `errors.UNIT_VALUE_MISSING`; the shell components A9 lists plus `Tabs` and `Select` (`components/ui/`), `zod` and `react-hook-form` (already dependencies), `useFormat`, `hasProblemCode`, `renderWithProviders`/`fakeApi`.
- Produces: routes `/units` and `/calendar` (lazy, inside the `AppShell` group, no `RequireRole`); `NavItem.labelKey` widened with `'nav.units' | 'nav.calendar'` and two `NAV_ITEMS` entries (`icon: Coins`, `CalendarDays` from `lucide-react`, after `nav.policy`); hooks `useUnits()`, `useUnitValues(code, from, to)`, `useUnitLookup(code, on, rule, enabled)` (`features/units/queries.ts`), `useHolidays(scope, year)`, `useDeadline(rule, anchor, scope, enabled)` (`features/calendar/queries.ts`), all `useQuery` with `enabled: user !== undefined && !mustChangePassword`; `deadlineSchema` (zod: `kind`, the per-kind fields `n`, `monthOffset`, `day`, `hours`, `roll`, `start`, `end`, `yearOffset`, `at`, `time`, plus `anchor` (datetime-local) and `scope`) and `toDeadlineRule(values): DeadlineRule` in `deadline-form.ts`.

Pages: **UnitsPage** (`/units`): a summary table from `useUnits` (unit, quote currency, latest value date, last fetch, source; "demo"/"live" is not shown, the source string is); `Tabs` per unit opening the history table (`useUnitValues`, last 90 days by default with from/to inputs; a row with `superseded_by` shows `units.corrected` as a `muted` badge); a lookup form (date, `Select` of the five `ConversionRule`s) rendering `UnitLookupResponse` or, on 503, `errors.UNIT_VALUE_MISSING` in an `ErrorBlock`. **CalendarPage** (`/calendar`): `Tabs` for the four scopes and a year `Select` (2025–2027) over `useHolidays` (day, name, source); a deadline evaluator form from `deadlineSchema` whose result shows `due_at` through `useFormat().dateTime` and the rule JSON.

Strings (both catalogues, same keys; `SAME_IN_BOTH` gains nothing because unit codes, scopes and rule names render raw): `nav.units` (`Unidades` / `Units`), `nav.calendar` (`Calendario` / `Calendar`); `units.*`: `title`, `description`, `table.label`, `table.unit`, `table.quoteCcy`, `table.latest`, `table.fetchedAt`, `table.source`, `history.title`, `history.from`, `history.to`, `history.validFrom`, `history.validTo`, `history.value`, `history.fetchedAt`, `history.empty`, `corrected`, `lookup.title`, `lookup.on`, `lookup.rule`, `lookup.submit`, `lookup.result` (`{{value}} {{ccy}} ({{date}})`); `calendar.*`: `title`, `description`, `scope`, `year`, `holidays.label`, `holidays.day`, `holidays.name`, `holidays.source`, `holidays.empty`, `evaluator.title`, `evaluator.kind`, `evaluator.anchor`, `evaluator.scope`, `evaluator.n`, `evaluator.monthOffset`, `evaluator.day`, `evaluator.roll`, `evaluator.hours`, `evaluator.start`, `evaluator.end`, `evaluator.yearOffset`, `evaluator.at`, `evaluator.time`, `evaluator.submit`, `evaluator.result`; `validation.*` keys the form needs (`dateFormat`, `positiveInteger`).

- [ ] **Step 1: Write the failing tests.** `deadline-form.test.ts`: `toDeadlineRule({kind: 'NTH_BUSINESS_DAY_OF', monthOffset: 1, n: 10, ...})` equals `{kind: 'NTH_BUSINESS_DAY_OF', params: {month_offset: 1, n: 10}, tz: 'America/Mexico_City'}`; `DAY_OF_NEXT_MONTH` with `day: 17, roll: 'NEXT_BUSINESS_DAY'`; `HOURS_AFTER` with `hours: 24`; a `day` of 31 fails the schema. `UnitsPage.test.tsx`: the summary table, the history tab with a `units.corrected` badge on a superseded row, the lookup form calling `/units/UDI?on=2026-01-20&rule=LAST_DAY_PREV_MONTH` and rendering the value, a 503 `UNIT_VALUE_MISSING` showing `esMX.errors.UNIT_VALUE_MISSING`. `CalendarPage.test.tsx`: the CNBV tab requests `scope=CNBV&year=2026` and lists `Jueves Santo`; the evaluator posts no request until submitted, then calls `/calendar/due` with the JSON rule and shows the formatted `due_at`; a validation error on `day: 31`.
- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck && npx vitest run src/features/units src/features/calendar` → FAIL (modules missing).
- [ ] **Step 3: Implement** the two pages, their hooks, the form helpers, the nav entries, the routes and the catalogue keys.
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; the Playwright smoke is unchanged by this task, so run it once locally as in A9 to see the two new nav entries leave the shell intact; `make ci` stays green.
- [ ] **Step 5: Commit** `feat(console): units and calendar pages`.

---

## Parallelism

- **A1 ∥ A2**: disjoint files (A1: dependencies, settings, `platform/text.py`, image, `_jurisdictions.yaml`, their tests; A2: migration, models, enums, the units errors, registry, factories, chain tests, `test_errors.py`). A2 does not read the new settings.
- **A4 ∥ A6** (both after A3; the units errors they share come from A2): A4 touches `platform/units/{catalogue,sources,store,service}.py` and `units/__init__.py`, the seed files, `tests/platform/{test_units.py,units_support.py}`, `tests/golden/units/`; A6 touches `platform/policy/*`, `platform/units/quantity.py` (A2's file, which A4 does not edit), the two packs, `cli.py`, `tests/golden/{__init__.py,packs/}`, `tests/platform/test_policy_schema.py` and `tests/platform/test_errors.py` (A6 alone appends rows there). Disjoint.
- **A5 ∥ A7** (A5 after A4, A7 after A6): A5 touches `platform/units/{banxico,jobs}.py`, `app.py`, `modules/tenants/service.py`, `cli.py`, the worker and kernel-events tests; A7 touches `platform/policy/{overrides,resolver,snapshots,assignments}.py`, `tests/platform/{test_policy_service.py,policy_support.py}` and `tests/golden/packs/`. Disjoint.
- **A5 beside A6**: when A4 merges before A6, an A5 branch may be open beside A6; both append a typer group to `src/xolotl/cli.py` (adjacent `app.add_typer(...)` lines) and test functions to `tests/test_cli.py`, so the second to merge rebases those two files (additive, no semantic conflict).
- Everything else is sequential: A3 after A1 and A2; A8 after A5 and A7 (it registers both routers and converts through units); A9 after A8 (the console types are generated from the merged API); A10 after A9 (it extends A9's nav, routes, aliases and catalogues).
- **Across plans**: plans 2a, 2b and 2c append to the same files, and every one of those edits is additive: `src/xolotl/app.py` (`wire_platform`: A5's `register_units`, 2b B3b's store hoist and 2c C5's `register_party`; the routers of A8, 2b B5, 2c C6 and C7 all go after `routes_webhooks.router`), `src/xolotl/models.py`, `src/xolotl/platform/enums.py` (A2, 2b B1), `src/xolotl/platform/config.py` (A1, 2b B1, 2c C2), `src/xolotl/modules/tenants/service.py` (`schedule_platform_jobs`: A5, 2c C5), `src/xolotl/cli.py`, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py` (2b and 2c append their revision to `CHAIN`), `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`, `tests/platform/test_worker.py` and `tests/modules/tenants/test_kernel_events.py` (A5, 2c C5), `.env.example`, `README.md`, `render.yaml` and `tests/test_render_blueprint.py` (A1, 2b B1), and in the console `nav.ts` (A9 and 2b B6 both add after the audit entry), `App.tsx`, `access.ts` and its tests, both i18n catalogues, `catalogue.test.ts`, `schema.d.ts` and `e2e/smoke.spec.ts` (A9, 2c C8). The second task to merge rebases them; router registration order and nav order are immaterial; `console/src/api/schema.d.ts` is regenerated from the merged API with A9's recipe (`npm run gen:api:file`) rather than merged by hand.

## Exit criteria

Owned by this plan (spec §12 rows for packs, units and calendar; §13 exit criteria), refined:

- `make ci` green after every task; the console gates and the Playwright smoke green after A9 and A10.
- `xolotl packs load --allow-unverified` installs `MX/ITF-IFPE` v1 and `MX/LFPIORPI-AVI` v1 (without the flag it refuses, naming `captures[ITF_ROR_CASH].period`, `captures[AVI_XVI_B].threshold`, `captures[AVI_XVI_B].merge_into` and `retention.kyc_after_relationship`); loading again changes nothing; a changed file at the same version is refused as `PACK_VERSION_IMMUTABLE`; the content hash of each pack equals the recorded golden and survives key reordering.
- After `POST /v1/policy/assignments` of the ITF pack, `GET /v1/policy/effective?on=2026-01-20&product=EMONEY` returns the N1 monthly limit as `750 UDI` converted with the UDI of 2025-12-31 (`6390.00 MXN` on the golden fixture) and a `snapshot_id` that is stable across calls; the effective policy per date and product matches `tests/golden/packs/effective.yaml`; a loosening override is 409 `POLICY_LOOSENING`.
- A USD 5,000 threshold on 2025-12-15 converts with the FIX of 2025-12-11 (the prior banking day across the weekend and the 12 December bank holiday), and a missing value is a 503 `UNIT_VALUE_MISSING` that nothing silently fills; a correction supersedes exactly once and is visible in `GET /v1/units/{code}/values`.
- `due_at` for the quarterly ROR anchored at 2025-12-31 gives the tenth CNBV business day of January 2026, 2026-01-15 23:59:59 America/Mexico_City, and the Semana Santa, 17-January-Sunday and 12-December goldens pass.
- `xolotl units seed`, `xolotl units fetch --unit UDI` (demo and, with a token, live through the mocked SIE) and `xolotl holidays seed` work; the `units.fetch` schedule exists for every tenant and its job completes, notifying ADMIN and MLRO when the source fails.
- The console pack viewer shows the ITF tier limits for the smoke tenant (A9); the units and calendar pages render their tables and evaluators (A10).

## Open questions for the product owner

1. Should the SAT scope treat the SAT's general vacation periods (RMF regla 2.1.6) as non-working days for the aviso deadline, or only the CFF art. 12 days and Semana Santa, as planned here? (It changes `DAY_OF_NEXT_MONTH` rolls in late December and July.)
2. D4 says recommended lists are tenant-removable, while spec §5.3 says overrides may only add lists. This plan implements "add only"; should removal of a recommended list be a screening setting in SP-03 rather than a policy override?
3. `PREV_MONTH_AVG` (and any averaged rule) has no single `unit_value.id` to record under D5. Should `Conversion` carry a list of ids, or should averaged rules be dropped from the enum?
4. The AVI pack applies the 25 % UBO threshold from its `effective_from` (2026-11-30), while A-0.16 dates that threshold for AV at March 2027. Is a v2 of the AVI pack (or an `effective_to` on v1) needed for the interval?
5. The N2 and N3 tier fields, checks and assurance levels are written from the ITF catalogue summary in D21; they carry no provenance in the schema, so the loader cannot block them. Should `KycTier` gain a provenance block so the compliance review can gate them too?
6. `units.fetch` runs per tenant because the job system is tenant-scoped; once a platform-level job exists, should the fetch move there?
7. Spec §5.3 and D21 ("two packs for one tenant need distinct products") are enforced as: a tenant-wide assignment never overlaps a product assignment, so an ITF whose crypto product reports under LFPIORPI assigns the ITF pack once per non-crypto product and the AVI pack to `CRYPTO`, and no product ever sees two packs merged (the merge rules serve the cross-product override validation). Is that the intended reading, or should an ITF's crypto product stay under the ITF pack's own `ITF_VA_OPS` capture with no AVI assignment at all (the AVI pack then being for non-ITF tenants only)? Either way, should an assignment carry a list of products so one call covers several?
