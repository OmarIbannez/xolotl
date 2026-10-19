# SP-02 plans: cross-plan reconciliation record

The three SP-02 plans (`2026-10-19-sp02a-packs-units-calendar.md`, `2026-10-19-sp02b-connector-sdk.md`, `2026-10-19-sp02c-party-core.md`) were written by one writer each, critiqued for spec coverage and executability, and revised. A cross-plan check then compared the seams between them and found the fifteen points below. This file records each finding, the decision taken where a finding offered alternatives, and the state of the plans.

## State

- **Applied.** Every finding below is applied to the plans committed next to this file. The edits were exact string replacements with match counts checked (2a: 32, 2b: 54, 2c: 34); each plan keeps all its task sections and its Parallelism, Exit criteria and Open questions sections.
- **Checked mechanically.** The cross-plan names agree across the three files: `xolotl.platform.text` with `RFC_SHAPE` and `mask_tail` (created in A1), `_check_cron` (A1, extended by C2), `UnitSourceAuthError` (A5, mapped by B4b), `seed_lookup` and `sie_fetch_factory(settings)` (B4b), `install_both_packs` (A7, reused by C4), no `_enc` column suffix left except where a deviation maps the spec's name, `UnitValueMissingError` everywhere, an "Across plans" paragraph in each Parallelism section.
- **Not yet reviewed independently.** No reviewer has read the edited task sections against these findings. Do that once before executing the tasks it touches (the Notion task "SP-02 plans: verification read of the reconciliation"); a mismatch is fixed in a small docs PR.

## Decisions on the findings that offered alternatives

- **F4.** `open_relationship` resolves `effective_policy(s, on=started.date(), product=body.product)` at open time, so a tenant whose assignments do not cover that product fails at open with 404 `POLICY_NOT_ASSIGNED` (a C4 service test and a C7 route test pin it); `product` stays optional; plan 2c's open question 2 gains the per-product case. Plan 2a's golden is unchanged.
- **F7.** The kernel's convention wins: no `_enc` suffix. `tenant_regulatory_profile.rfc`, `tenant_branch.address`, `connector_credential.secret` in models, migration DDL, isolation factories and tests; masked response fields such as `rfc_masked` are unchanged.
- **F8.** Plan 2a's A1 names the validator `_check_cron`, decorated `@field_validator("units_fetch_cron")`, with the message built from `info.field_name` (`f"XOLOTL_{info.field_name.upper()} must be a five-field cron expression"`); plan 2c's C2 adds `"retention_purge_cron"` to that decorator and lists A1 among its preconditions.
- **F9.** One platform helper, created in plan 2a's **A1** so every later task of the three plans has it: `src/xolotl/platform/text.py` with `RFC_SHAPE` (`^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$`) and `mask_tail(value, *, keep=4)` (`"****" + value[-keep:]`, `"****"` when the value has `keep` characters or fewer). A8 and B4a import `RFC_SHAPE`; plan 2c's `mask_identifier` delegates to `mask_tail`; 2c's full RFC validator with the check digit stays 2c's.
- **F10.** The `banxico_sie` connector's demo mode and sandbox mock read the committed seed files through a `seed_lookup` callable that `default_router` builds from plan 2a's `SeedFileSource`, falling back to constants only when the seed has no row, so a sandbox check and the units page agree on a date.
- **F14.** Plan 2a's A5 adds `UnitSourceAuthError(UnitSourceError)` for a rejected token (401/403, same 503 `UNIT_SOURCE_UNAVAILABLE` code); plan 2b's B4b maps it to `PermanentCallError` (no retry) and uses `UnitSourceError` in its transient-error test.
- **F15.** Each Parallelism section names the files the three plans append to; those edits are additive, the second task to merge rebases them, router and nav order is immaterial, and `console/src/api/schema.d.ts` is regenerated, never merged by hand.
- All other findings are applied as written.

## Dependency edges the findings added

- B1 needs plan 2a's **A1** as well as A2 (`banxico_token`, `xolotl.platform.text`, the `.env.example` and README rows).
- C5 needs plan 2a's **A5** and plan 2b's **B3b** as well as C4 (the `units.fetch` schedule rows in the pinned tests; the hoisted object store in `wire_platform`).
- C2 needs plan 2a's A1 (the cron validator); it already follows from C2 needing B1.

The task table in `docs/superpowers/execution/sp02/tasks.json` carries these edges.

## The findings

### F1. B1 depends on 2a's A1 (banxico_token) but names only A2, and its fallback collides with A1

- **Severity:** Important
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Task B1 — Precondition, Deviations 1, Step 3 (.env.example and README rows)
- **Finding and change:** B1's precondition says only "plan 2a's migration 0006 is merged" (A2), yet its tests assert `s.banxico_token is None` and read `XOLOTL_BANXICO_TOKEN`, which 2a's A1 declares; in 2a, A1 runs in parallel with A2, so A1 may not be on main when B1 starts. Deviations 1 and Step 3 then tell the implementer to add the field, the commented `.env.example` line and a README row themselves "if grep finds none", with wording that differs from A1's (A1: `# Banxico SIE token; leave unset for demo mode` and `| XOLOTL_BANXICO_TOKEN | unset | Banxico SIE token; unset runs the fetcher in demo mode from the seed files, refused when XOLOTL_ENV=prod |`). Whichever merges second conflicts on config.py, .env.example and README. Change 2b: make B1's precondition "2a's A1 and A2 merged", delete the add-it-yourself fallback in Deviations 1 and Step 3, and drop B1's duplicated BANXICO_TOKEN env-read assertions (A1's `test_sp02_settings_read_their_variables` already pins them), keeping only `assert s.banxico_token is None` as a cross-check if wanted.

### F2. sie_fetch_factory(token: str) cannot construct 2a's BanxicoSieSource

- **Severity:** Important
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Task B4b — defaults.py (`sie_fetch_factory`, `rows_to_pairs`) and `test_rows_to_pairs_reads_plan_2a_rows`
- **Finding and change:** 2a A5 defines `BanxicoSieSource.__init__(self, token: SecretStr | None, *, seed_dir: Path, env: str, client: httpx.Client | None = None)`: the token is positional and a SecretStr, and `seed_dir` and `env` are required keyword-only. 2b's `sie_fetch_factory(token: str) -> ObservationFetch` with `BanxicoSieSource(<token keyword>=token)` has neither value and no settings in scope, and `BanxicoSieConnector(deps, *, fetch_factory: Callable[[str], ObservationFetch], ...)` fixes that shape. Change 2b: make `default_router` build the closure with settings, e.g. `def sie_fetch_factory(settings: Settings) -> Callable[[str], ObservationFetch]` returning `lambda token: (lambda unit_code, start, end: rows_to_pairs(BanxicoSieSource(SecretStr(token), seed_dir=settings.packs_dir / "_units", env=settings.env).fetch(unit_code, start, end)))`, and pass `fetch_factory=sie_fetch_factory(settings)`. Also pin what 2b leaves open now that 2a has fixed it: `rows_to_pairs` reads `(row.valid_from, row.value)`, and the test constructs 2a's dataclass as `UnitValue(unit_code="UDI", valid_from=date(2026, 10, 16), valid_to=date(2026, 10, 16), value=Decimal("8.650000"), quote_ccy="MXN", source="BANXICO_SIE:SP68257", confidence=Confidence.VERIFIED)` (`Confidence` from `xolotl.platform.enums`).

### F3. C5 assumes 2b's B3b store hoist and 2a's A5 schedule edits but lists neither as a precondition

- **Severity:** Important
- **Plan:** 2026-10-19-sp02c-party-core.md
- **Where:** Task C5 — Interfaces (`wire_platform`, `schedule_platform_jobs`), the pinned schedule tests, and the Parallelism section
- **Finding and change:** C5 says `register_party(object_store=store)` reuses "the store plan 2b's B3 already hoisted above PlatformServices(...)" and that its schedule call goes "after plan 2a's units.fetch call", and its edits to tests/platform/test_worker.py and tests/modules/tenants/test_kernel_events.py are written with the `units.fetch` row already present (four-row lists). On main today `wire_platform` builds `object_store=build_object_store(settings)` inline and `schedule_platform_jobs` is the bare `for kind in TENANT_JOBS` loop; the hoist is 2b B3b's and the units.fetch call is 2a A5's, and A5 runs in parallel with A7 (which C4 needs), so A5 can still be open when C5 starts. The Parallelism section only requires 0006 and 0007 on main. Change 2c: state C5's preconditions as "2a A5 and 2b B3b merged" (and C4), and place `register_party(object_store=store)` after the hoisted `store = build_object_store(settings)` line rather than "next to register_job_handler(PURGE_KIND, ...)", which precedes it. (Also "B3" should read "B3b": 2b split the task.)

### F4. A product=None customer relationship can never be closed on a tenant with per-product pack assignments

- **Severity:** Important
- **Plan:** 2026-10-19-sp02c-party-core.md
- **Where:** Task C4 — `open_relationship` / `close_relationship` semantics; Open question 2 (against 2a A7's assign_pack D21 rule and `assignments_on`)
- **Finding and change:** 2a A7 enforces D21 so that a tenant-wide assignment never overlaps a product one, and tells an ITF tenant with a crypto product to assign the ITF pack once per non-crypto product and the AVI pack to CRYPTO; `assignments_on(product=None)` returns tenant-wide rows only and the golden `no-product-sees-only-tenant-wide-assignments` pins `POLICY_NOT_ASSIGNED` for `product=None`. 2c's `RelationshipOpenRequest.product` defaults to `None` and `close_relationship` calls `effective_policy(s, on=ended.date(), product=row.product)`, so on such a tenant every relationship opened without a product is accepted at open time and then answers 404 `POLICY_NOT_ASSIGNED` at exit for ever. Change 2c: have `open_relationship` resolve `effective_policy(s, on=started.date(), product=body.product)` (letting `POLICY_NOT_ASSIGNED` propagate so the mismatch surfaces at open, with a C4 test and a C7 route test for it), or make `product` required; and extend open question 2 with this per-product case. If the product owner prefers, the alternative is in 2a: define `effective_policy(product=None)` as the strictest merge over `assignments_in_force`, which changes 2a's golden.

### F5. 2b still describes 2c as writing verification_check through run_check

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Review Focus 1, Deviations 13, Parallelism ("Across plans")
- **Finding and change:** 2b says "plan 2c's task that writes verification_check.connector_call_id needs B1 and its service needs B3b (run_check)", "a caller that rolls back loses the row (and plan 2c's verification_check with it)" and "CheckResult.details (which plan 2c copies into verification_check)". 2c's deviations decide that no service function writes `verification_check` in SP-02 (the isolation factory is its only writer) and nothing calls `run_check`; C2 imports only `CheckType` and `ConnectorMode` from B1's re-exports. Change 2b: say that in SP-02 plan 2c needs only B1 (the `connector_call` table and the enums) and that the verification_check copy is SP-08's; keep the redaction and rollback points as forward-looking notes.

### F6. B1 restates the model_fields_set assertion without 2a's three names

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Task B1 — Step 1, `test_env_example_is_a_valid_configuration`
- **Finding and change:** The repository asserts `{"master_key", "object_store", "object_store_root"} <= s.model_fields_set`. 2a A1 extends it with `packs_dir`, `allow_unverified_pack_values`, `units_fetch_cron`; 2c C2 says "add retention_purge_cron"; 2b B1 says "extend the set to {master_key, object_store, object_store_root, connector_mode_default}", which, applied after A1, silently drops 2a's three names from the check (a `<=` test still passes) and produces a textual conflict either way. Change 2b: phrase it as 2c does, "add `connector_mode_default` to the set".

### F7. Encrypted column naming diverges across the three plans

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02a-packs-units-calendar.md; 2026-10-19-sp02b-connector-sdk.md
- **Where:** 2a Task A2 (`rfc_enc`, `address_enc`, Deviations) and 2b Task B1 (`secret_enc`, Deviations 17) versus 2c's Deviations
- **Finding and change:** 2a keeps the spec's `_enc` suffix (`tenant_regulatory_profile.rfc_enc`, `tenant_branch.address_enc`), 2b keeps it (`connector_credential.secret_enc`), and 2c drops it (`person.given_names`, `party_identifier.value`, ...) citing the kernel precedent; the repository confirms the precedent (`staff_credential.totp_secret`, migration 0004 renamed `totp_secret_enc`; `response_body` for EncryptedJson). Change 2a and 2b to the kernel's convention: `rfc`, `address`, `secret` in the models, the migration DDL and the isolation factories (`rfc_masked` and the masked responses are unchanged), so the SP-02 schema reads one way. If the product owner prefers the spec's spelling, change 2c instead; either way all three should match.

### F8. The cron validator 2c extends is named and worded for one field in 2a

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02a-packs-units-calendar.md; 2026-10-19-sp02c-party-core.md
- **Where:** 2a Task A1 (`_check_units_fetch_cron`) and 2c Task C2 (the `retention_purge_cron` validator and its precondition)
- **Finding and change:** 2a A1 produces `_check_units_fetch_cron` with the fixed message `"XOLOTL_UNITS_FETCH_CRON must be a five-field cron expression"`. 2c C2 expects to add `retention_purge_cron` to "the @field_validator plan 2a wrote (one decorator naming both fields, naming the variable of the failing field)" and tests `match="RETENTION_PURGE_CRON"`, with an "if 2a added no validator" branch. Change 2a: name it `_check_cron`, decorate `@field_validator("units_fetch_cron")` and build the message from `info.field_name` (`f"XOLOTL_{info.field_name.upper()} must be a five-field cron expression"`) so 2c only adds a name to the decorator. Change 2c: list 2a A1 among C2's preconditions (the validator, and the .env.example/README table it extends) and drop the no-validator branch.

### F9. The RFC shape regex and the four-character tail mask are built three and two times

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02a-packs-units-calendar.md; 2026-10-19-sp02b-connector-sdk.md; 2026-10-19-sp02c-party-core.md
- **Where:** 2a Task A8 (`policy_service.set_regulatory_profile`, `rfc_masked`), 2b Task B4a (`RFC_SHAPE`), 2c Task C1/C3 (`_RFC_RE`, `mask_identifier`)
- **Finding and change:** 2a A8 validates the RFC with `^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$` and masks with `"****" + rfc[-4:]`; 2b B4a defines `RFC_SHAPE = ^[A-ZÑ&]{3,4}\d{6}[A-Z\d]{3}$` (the same language); 2c C1 ports Xolo's `_RFC_RE` and C3 adds `mask_identifier` (`"****" + value[-4:]`, `"****"` when four or fewer). The import rules (tenants never imports party, platform never imports modules) explain the copies but a platform helper serves all three. Change 2a A8 to put `RFC_SHAPE` and `mask_tail(value: str, *, keep: int = 4) -> str` in a platform module (e.g. `xolotl/platform/text.py`), 2b B4a to import `RFC_SHAPE` from it, and 2c to have `masking.mask_identifier` delegate to `mask_tail` (its full RFC validator with the check digit stays 2c's).

### F10. Two different Banxico demo semantics: seed files in 2a, constants in 2b

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Task B4b — Deviations 4, `MOCK_VALUES` and `_mock`, versus 2a Task A5's demo mode
- **Finding and change:** Spec §3 and 2a A5 define an unset token as demo mode over the committed seed files (`SeedFileSource(seed_dir, allow_unverified=True)`), so `GET /v1/units/UDI?on=2026-01-20` answers from `UDI.seed.yaml`. 2b's `banxico_sie` demo mode and sandbox mock return `UDI = 8.500000` and `USD_MXN_FIX = 18.500000` for any date, so a sandbox tenant's `UNIT_VALUE` check disagrees with the units page on the same day. Change 2b: either have `_mock` read the seed files through a `seed_lookup: Callable[[str, date], Decimal | None]` that `default_router` builds from 2a's `SeedFileSource(settings.packs_dir / "_units", allow_unverified=True)` (falling back to the constant when the seed has no row), or state the divergence in Deviations 4 and the README's Connectors section so nobody expects the two to agree.

### F11. 2b omits its problem code from the shared test_errors.py list that 2a and 2c extend

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Task B2 — `NoRouteError` and tests/platform/test_errors.py
- **Finding and change:** 2a (A2: three rows; A6: three rows) and 2c (C3: two rows) append their error classes to the parametrised list of `test_error_status_and_default_code` in tests/platform/test_errors.py; 2b pins `NoRouteError`'s 409/`CONNECTOR_NO_ROUTE` only inside test_connector_router.py. Change 2b B2: add `tests/platform/test_errors.py` to its Modify list with the row `(NoRouteError, 409, "CONNECTOR_NO_ROUTE")`, imported from `xolotl.platform.connectors`.

### F12. "plan 2a's console task" for validation.dateFormat is A10, not A9

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02c-party-core.md
- **Where:** Task C8 — i18n (`validation.dateFormat`) and form.ts
- **Finding and change:** 2c C8 adds `validation.dateFormat` "only when plan 2a's console task has not added it". 2a has two console tasks; A9 adds no `validation.*` key, A10 adds `validation.dateFormat` and `validation.positiveInteger`. Change 2c: name A10 explicitly, and note that C8 regenerating `schema.d.ts` and A9/A10/B6 touching `nav.ts`, `App.tsx`, both catalogues and `catalogue.test.ts` means the later-merging console task rebases those files.

### F13. itf_pack re-implements 2a's install_both_packs helper

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02c-party-core.md
- **Where:** Task C4 — `tests/modules/party/conftest.py::itf_pack`
- **Finding and change:** 2a A7 creates `tests/platform/policy_support.py::install_both_packs(admin_engine) -> dict[str, UUID]` (both packs through an owner session with `allow_unverified=True`, version ids by code), which 2a's A8 route tests reuse. 2c's `itf_pack` fixture repeats the owner-session `load_pack` + `install_pack` dance for the ITF pack alone. Change 2c C4: `version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]` then the `assign_pack` call, and list A7 (not just "xolotl.platform.policy") as the precondition that provides it.

### F14. The XolotlError leg uses UnitValueMissingError, which 2a's source never raises

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02b-connector-sdk.md
- **Where:** Task B4b — `test_a_fetch_transport_error_is_retried_and_a_platform_error_is_wrapped_as_transient` and `_call`'s XolotlError wrapping
- **Finding and change:** 2a A5's `BanxicoSieSource.fetch` raises `UnitSourceError` (503 `UNIT_SOURCE_UNAVAILABLE`: unreachable, HTTP status, malformed body, rejected token) or `ValidationFailedError`; `UnitValueMissingError` is raised by `UnitService.lookup`. 2b's test constructs `UnitValueMissingError(...)` as the platform-error example, and `_call` wraps every `XolotlError` as `TransientCallError`, so 2a's "Banxico rejected the API token" 401 is retried three times with backoff. Change 2b: use `UnitSourceError("Banxico SIE is unreachable, try again shortly")` in the test, and either accept the retry of a rejected token in Deviations 7 or map `UnitSourceError` messages containing "token" to `PermanentCallError` (2a could add `UnitSourceAuthError(UnitSourceError)` if a class is preferred over a message check).

### F15. No plan names the files every plan appends to, so cross-plan rebases are unplanned

- **Severity:** Minor
- **Plan:** 2026-10-19-sp02a-packs-units-calendar.md; 2026-10-19-sp02b-connector-sdk.md; 2026-10-19-sp02c-party-core.md
- **Where:** Parallelism sections of all three plans
- **Finding and change:** Each plan's parallelism section covers only its own tasks. Across plans, the same files are appended to by 2a, 2b and 2c: `src/xolotl/app.py` (A5 and B3b and C5 in `wire_platform`; A8, B5, C6, C7 all "after routes_webhooks.router"), `src/xolotl/models.py`, `src/xolotl/platform/enums.py` (A2, B1), `src/xolotl/platform/config.py`, `src/xolotl/modules/tenants/service.py` (A5, C5), `src/xolotl/cli.py`, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`, `tests/platform/test_worker.py` and `tests/modules/tenants/test_kernel_events.py` (A5, C5), `.env.example`, `README.md`, `render.yaml`/`tests/test_render_blueprint.py` (A1, B1), and in the console `nav.ts` (A9 and B6 both "after the audit entry"), `App.tsx`, `access.ts` and its tests, both catalogues, `catalogue.test.ts`, `schema.d.ts` and `e2e/smoke.spec.ts` (A9, C8). Add one "across plans" paragraph to each parallelism section (or a shared note) saying these edits are additive, that the second task to merge rebases, that router and nav order is immaterial, and that `schema.d.ts` is regenerated rather than merged.

## The cross-check's summary

The three plans agree on the things 00-common.md fixes: migration ids and down_revisions (0006 -> 0007 -> 0008), the six settings and their defaults, package and route paths, the shared tables 2c references by composite FK (policy_snapshot (tenant_id, id) from 2a, connector_call (tenant_id, id) from 2b), the audit actions and problem codes (no collisions), and the exported signatures (load_pack, install_pack, assign_pack, create_override, effective_policy, snapshot_policy, get_snapshot, UnitService.lookup/to_unit/threshold_in_ccy, the calendar helpers, CheckType/ConnectorMode/.../run_check). The only name that differs from 00-common is UnitValueMissing -> UnitValueMissingError, which 2a records as a deviation and 2b consumes correctly; 2b's extensions (run_check mode=None, Connector.run route=, ConnectorRequest.country) and 2a's (lookup country/scope keywords) are backward-compatible. The inconsistencies are in the seams: 2b B1 depends on 2a A1 (banxico_token) but names only A2 and carries a fallback that would collide with A1; 2b B4b's sie_fetch_factory cannot construct 2a A5's BanxicoSieSource (positional SecretStr token plus required seed_dir/env); 2c C5 assumes 2b B3b's hoisted store and 2a A5's units.fetch schedule/pinned-test edits without listing them as preconditions; 2a's D21 assignment rule plus assignments_on(product=None) makes a 2c customer_relationship with product=None unclosable on a tenant with per-product assignments; 2b still describes 2c as writing verification_check through run_check, which 2c no longer does in SP-02. Smaller items: 2b B1 restates the model_fields_set assertion without 2a's three names; encrypted-column naming diverges (2a/2b keep _enc, 2c and the kernel drop it); the cron validator 2c extends is named and worded for one field in 2a; duplicated RFC shape regexes and tail masks across all three; two different Banxico demo semantics (2a seed files, 2b constants); 2b omits its NoRouteError row from test_errors.py; 2c cites "plan 2a's console task" for validation.dateFormat (it is A10); 2c's itf_pack re-implements 2a's install_both_packs; 2b's banxico test wraps UnitValueMissingError where 2a's source raises UnitSourceError; and none of the parallelism sections name the dozen files every plan appends to (app.py, nav.ts, App.tsx, catalogues, factories.py, models.py, cli.py, test_cli.py, test_errors.py, test_config.py, .env.example, README). 00-common.md itself still spells UnitValueMissing; it is outside the three plans but should read UnitValueMissingError.
