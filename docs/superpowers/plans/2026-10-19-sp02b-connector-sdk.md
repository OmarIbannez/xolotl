# SP-02b — Connector SDK — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform one way to call an external verification service: a synchronous `Connector` base that looks up tenant-encrypted credentials, honours a timeout, retries with backoff behind a per-`(connector, cell)` circuit breaker, stores the raw answer (credentials redacted) in the object store as evidence, meters every call in `connector_call`, and never raises to its caller; a router that runs the primary connector and then its fallbacks per check type; sandbox mocks forced for `SANDBOX` tenants; the first two connectors (`banxico_sie`, `sat_rfc`); the ADMIN endpoints that list connectors, aggregate usage and set credentials; and the console's connectors page.

**Architecture:** Everything lives in `src/xolotl/platform/connectors/` behind the program spec's `Connector`, `ConnectorRouter` and `CredentialVault` ports. `Connector.run(session, request, *, mode)` is the only entry point a connector exposes: it reads the tenant's `env` and `cell`, forces mocks for a `SANDBOX` tenant, asks the vault for the credentials of `(connector, mode)`, consults the cell's in-process `CircuitBreaker`, runs `_call` up to `max_attempts` times with a fixed backoff, stores the raw body as `connectors/<connector>/<call id>` in the object store, writes one `connector_call` row in the caller's session and returns a `VerificationOutcome` whose `outcome` is `PASS`, `FAIL`, `INCONCLUSIVE`, `ERROR`, `TIMEOUT` or `CIRCUIT_OPEN`. `run_check(session, request, *, router)` resolves the tenant's effective mode, asks the router for the ordered connectors of the request's check type and country, and walks them until one gives a definitive outcome. The router's table is the platform default (no per-tenant connector configuration exists yet); the composition root builds one router per process and keeps it on `PlatformServices.connectors`, where the routes and plan 2c's party service reach it.

**Tech Stack:** The SP-01 stack. `httpx` (sync `httpx.Client`) and `respx` are already installed; Task B4a adds `defusedxml` (runtime) and `types-defusedxml` (dev) for the SAT SOAP parser. No new console dependency.

**Spec:** `docs/superpowers/specs/2026-10-19-sp02-packs-units-calendar-party-design.md` §1 (the "2b" bullet), §3 (`BANXICO_TOKEN`, `CONNECTOR_MODE_DEFAULT`), §4.2 (`connector_credential`, `connector_call`), §8 (the SDK), §9's connector endpoints (`GET /v1/connectors`, `GET /v1/connectors/usage?from&to`, the credential-setting endpoint `CredentialVault.set` implies), §11 (connectors page), §12 (`tests/platform/test_connectors.py` row), §13 (exit criteria). Program spec: addendum (a) (sandbox is a tenant property), §4 ports `Connector`/`ConnectorRouter`/`CredentialVault` and `UnitValueSource`, D22 (the Xolo providers: `providers/sat.py` is the SAT connector's source, `providers/banxico.py` is plan 2a's). Decisions from `00-common.md` applied: migration `0007_connectors` (down `0006_policy_units_calendar`), package `src/xolotl/platform/connectors/`, routes in `src/xolotl/modules/tenants/routes_connectors.py`, the exported names, the Banxico connector reusing plan 2a's `BanxicoSieSource`.

## Global Constraints

- Every SP-01 convention binds: tenant tables with forced RLS and the kernel predicate (`enable_rls`, never a custom predicate), one isolation factory per tenant table registered in `tests/isolation/factories.py::register_all`, `v1_router()` for every router, `require_access(...)` on every route, `Idempotency-Key` on every `POST` (this plan adds no POST), `Encrypted*` columns for secrets, audit through `record(session, AuditEvent(...))` last in the transaction, ids-only payloads (this plan publishes no event), problem-detail errors with stable codes, no S3/Redis/Kafka (the local-filesystem `ObjectStore` is the only store).
- Settings (`src/xolotl/platform/config.py`, prefix `XOLOTL_`): `banxico_token: SecretStr | None = None` (declared by plan 2a's settings task; see Deviations 1), `connector_mode_default: Literal["SANDBOX", "LIVE"] = "SANDBOX"`; `render.yaml` sets `XOLOTL_CONNECTOR_MODE_DEFAULT=LIVE`.
- Migration `src/xolotl/migrations/versions/0007_connectors.py`: `revision = "0007_connectors"`, `down_revision = "0006_policy_units_calendar"`; it creates exactly `connector_credential` and `connector_call` as spec §4.2 writes them, with the column `secret` for the spec's `secret_enc` (Deviations 17) (`PRIMARY KEY (tenant_id, id)`, `UNIQUE (tenant_id, connector, mode)` on the credential, `ix_connector_call_tenant_day (tenant_id, created_at)` on the call); both tables get `enable_rls`; `downgrade()` drops both.
- Package `src/xolotl/platform/connectors/` exports exactly `CheckType`, `ConnectorMode`, `ConnectorRequest`, `RawResponse`, `CheckResult`, `VerificationOutcome`, `Connector`, `CredentialVault`, `ConnectorRouter`, `CircuitBreaker`, `run_check` (plus the names this plan adds: `ConnectorRoute`, `CallOutcome`, `CircuitState`, `ConnectorDeps`, `NoRouteError`, `ConnectorCallError`, `PermanentCallError`, `TransientCallError`, `CredentialMissingError`, `InterpretationError`, `redact`, `DEFAULT_ROUTES`, `default_router`, `SatRfcConnector`, `BanxicoSieConnector`, `UsageRow`, `usage`).
- The vocabularies are `StrEnum`s in `src/xolotl/platform/enums.py` stored through `enum_column` (VARCHAR(32)): `ConnectorMode {SANDBOX, LIVE}`, `CheckType {UNIT_VALUE, RFC_VALIDATION, CURP_VALIDATION}`, `ConnectorRoute {PRIMARY, FALLBACK, MOCK}`, `CallOutcome {PASS, FAIL, INCONCLUSIVE, ERROR, TIMEOUT, CIRCUIT_OPEN}`, `CircuitState {CLOSED, OPEN, HALF_OPEN}`.
- Connector numbers (spec §8): `timeout_s = 8.0` per attempt, `max_attempts = 3`, backoff between attempts `0.2 s` then `0.5 s`; circuit breaker per `(connector, cell)`: `CLOSED → OPEN` after `5` failed attempts within `60 s`, `OPEN → HALF_OPEN` after `30 s`, one trial attempt in `HALF_OPEN`, success closes, failure reopens for another `30 s`.
- Evidence: the raw body of the last attempt, every credential value replaced by `[REDACTED]`, stored at object key `connectors/<connector name>/<call id>` with the connector's `evidence_content_type`; `connector_call.evidence_key` holds that key. The top-level string values of `CheckResult.details` pass through the same redaction before they reach the outcome.
- Metering: one `connector_call` row per `Connector.run`, written in the caller's session (`session.add` + `session.flush`), `cost_micros` from the connector's `CheckResult` (0 for a mock), `error` from `safe_exception_summary` or `"HTTP <status>"` or `"circuit open"`, never a credential.
- A `SANDBOX` call is always `route = MOCK` and never reads the vault, whatever made it `SANDBOX` (the tenant's `env`, the default mode or an explicit `mode`); a `SANDBOX` tenant is always a `SANDBOX` call, whatever the caller asked; `PROD` tenants use the explicit per-call `mode` or `connector_mode_default`.
- `Connector.run` never raises: a connector or store failure is an outcome `ERROR`, every attempt timing out is `TIMEOUT`, an open breaker is `CIRCUIT_OPEN` with no attempt made.
- Audit action added: `connector.credential_set` (`entity_type="connector_credential"`, `entity_id=str(row.id)`, `detail={"connector", "mode", "field_names", "rotated"}`; never a credential value).
- Problem codes added: `CONNECTOR_NO_ROUTE` (409, `NoRouteError(DomainError)`: no connector routes the check type for the country). Existing codes reused: `NOT_FOUND` (unknown connector name), `VALIDATION_FAILED` (bad credential fields, bad usage range), `ROLE_FORBIDDEN`.
- Routes (all ADMIN, staff only, `src/xolotl/modules/tenants/routes_connectors.py`, router prefix `/connectors`, tag `connectors`): `GET /v1/connectors` (`list_connectors`), `GET /v1/connectors/usage?from=YYYY-MM-DD&to=YYYY-MM-DD` (`connector_usage`), `PUT /v1/connectors/{name}/credentials/{mode}` (`set_connector_credentials`); endpoint function names are the `operationId`s and are unique app-wide.
- Console: route `/connectors` behind `ACCESS.connectors = ['ADMIN']`, nav entry `nav.connectors`, Spanish-first keyed strings in both catalogues, `schema.d.ts` regenerated and committed, no chart library.
- Gates at the end of every task: `make ci` (ruff ALL + format, basedpyright "all", ty, vulture, import-linter, pytest 90 % with the isolation harness); console tasks also `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`.

## Deviations from the sub-spec (decided here)

1. **`banxico_token` is plan 2a's field.** `00-common.md` lists it among the SP-02 settings and plan 2a's `BanxicoSieSource` reads it, so plan 2a's A1 declares `banxico_token: SecretStr | None = None` with its `.env.example` line and README row, and B1's precondition (2a's A1 and A2 merged) has them on `main` before B1 starts. B1 adds only `connector_mode_default` and never adds, moves or rewords `banxico_token`, its example line or its README row; its defaults test keeps `assert s.banxico_token is None` as a cross-check that A1 is merged, and A1's `test_sp02_settings_read_their_variables` is the test that reads `XOLOTL_BANXICO_TOKEN`. The "refuses in prod" rule of spec §3 is enforced by the `banxico_sie` connector (a `LIVE` call with no token in `ENV=prod` is an `ERROR` outcome), not by `Settings`, so a deployment that never calls Banxico still starts.
2. **`CONNECTOR_MODE_DEFAULT` is the mode of a `PROD` tenant when a call names none.** The spec's "PROD tenants default to LIVE" is met in production by `render.yaml` setting `XOLOTL_CONNECTOR_MODE_DEFAULT=LIVE`; everywhere else the `SANDBOX` default keeps every tenant on mocks. A `SANDBOX` tenant is always `SANDBOX`, and a `SANDBOX` call, whatever made it so (the tenant's `env`, the default or an explicit `mode`), runs `_mock` with no credentials and carries `route = MOCK`. The "credential for SANDBOX explicitly selected per call" of §8 is the `mode` keyword of `run_check`; because mode `SANDBOX` never reads the vault, a `SANDBOX` credential row is accepted by `PUT /v1/connectors/{name}/credentials/SANDBOX`, listed in `credential_modes` and read by no connector in this plan (a connector that distinguishes a vendor sandbox from the built-in mock, SP-08's, will read it).
3. **The SAT connector's live path is the CFDI status service that `~/code/xolo/src/xolo/providers/sat.py` wraps.** The SAT publishes no public RFC-lookup API; D22 names the CFDI service (`ConsultaCFDIService.svc`, the QR-code lookup, no credentials) as the connector's source. A live `RFC_VALIDATION` request therefore carries, in `params`, the subject `rfc` and a CFDI reference (`cfdi_uuid`, `issuer_rfc`, `receiver_rfc`, `total`, optional `seal_last8`); the subject RFC must be the CFDI's issuer or receiver. Outcome: `FAIL` when `ValidacionEFOS == "100"` (the issuer is on the SAT's 69-B list, reason `EFOS_LISTED`) or `Estado == "Cancelado"` (`CFDI_CANCELLED`); `PASS` when `Estado == "Vigente"` and EFOS is not `100`; `INCONCLUSIVE` when `No Encontrado` (`CFDI_NOT_FOUND`) or any other state (`UNKNOWN_STATE`). What carries over from Xolo: the URL and SOAP action, the RFC shape regex (used as `RFC_SHAPE` from `xolotl.platform.text`, the platform helper plan 2a's A1 creates, so `sat.py` defines no copy), `format_total`, `expression` (with the `&` → `&amp;` rule), `envelope`, `parse_response`, the `_FIELDS` and EFOS code table, the query normalisation. What stays behind: the async client, the `TtlCache`, the `CfdiStatus` model and its English summary text, `DemoSat` and `sat_demo.json` (replaced by the last-character mock), `to_status`.
4. **The Banxico connector's evidence is the port's observations as canonical JSON, not the SIE wire body**, because the SIE HTTP call belongs to plan 2a's `BanxicoSieSource` (00-common: the connector does not reimplement it). Its demo mode (no token outside prod) and its sandbox mock are one thing: `_mock`, which answers from the committed seed files of spec §6 through a `seed_lookup: Callable[[str, date], Decimal | None]` that `default_router` builds from plan 2a's `SeedFileSource(settings.packs_dir / "_units", allow_unverified=True)` (the same files `BanxicoSieSource`'s demo mode and the units page read, so a sandbox tenant's `UNIT_VALUE` check and `GET /v1/units/{code}?on=` agree on a seeded day), and falls back to the constants `MOCK_VALUES` (`UDI = 8.500000`, `USD_MXN_FIX = 18.500000` MXN) only when the seed has no row for that unit and date; the observation is dated on the requested day either way. The seed files stay plan 2a's.
5. **"cell" is `tenant.cell`** (the kernel's `tenant.cell text NOT NULL DEFAULT 'mx-1'`). Each connector keeps one `CircuitBreaker` per cell in process memory; the API process and a `xolotl worker` process each have their own, and `GET /v1/connectors` shows the state of the process that answers.
6. **The router's table is the platform default.** Spec §8 reads routes "from `tenant_connector_config` in the regulatory profile or the platform default"; plan 2a's `tenant_regulatory_profile` DDL (spec §4.2) has no such column, so this plan ships `DEFAULT_ROUTES = {(UNIT_VALUE, "MX"): ("banxico_sie",), (RFC_VALIDATION, "MX"): ("sat_rfc",)}` and no per-tenant override (SP-08 adds one with its second vendor per check type). A `SANDBOX` route returns the primary only: mocks never fail over.
7. **Retry classification.** A failed attempt is: an `httpx.TimeoutException` (counts as a timeout), any other `httpx.HTTPError` or `OSError`, a `TransientCallError` raised by `_call`, or a `RawResponse` with `status >= 500`. Each failed attempt counts one breaker failure. A `RawResponse` with a `4xx` status, a `PermanentCallError` (`CredentialMissingError` included) and any other exception end the call at once as `ERROR` and count no breaker failure (they are the caller's or the connector's fault, not the remote's health). The outcome after the last attempt is `TIMEOUT` when that attempt timed out and `ERROR` otherwise. The breaker is consulted once, before the first attempt; a breaker that opens during a run does not cut the run short. The `banxico_sie` connector classifies plan 2a's source errors the same way: `UnitSourceAuthError` (Banxico rejected the token, 401/403) is re-raised as a `PermanentCallError` (no retry, no breaker failure: a bad token does not heal with backoff), any other `UnitSourceError` (unreachable, an HTTP status, a malformed body) as a `TransientCallError`.
8. **Signatures extended, not changed:** `Connector.run(session, request, *, mode, route=ConnectorRoute.PRIMARY)` (the router passes the position it took); `ConnectorRequest` gains `country: str = "MX"` (the router keys routes by country); `run_check(session, request, *, router, mode=None)`; `VerificationOutcome` carries `attempts` beside the spec's fields. `name` and `check_types` are instance attributes set by `Connector.__init__` (fakes in tests need several names of one class); `timeout_s`, `max_attempts` and `evidence_content_type` are `ClassVar`s.
9. **`CredentialVault.get` returns `{}` when nothing is stored** (the connector decides what it needs: `sat_rfc` needs nothing, `banxico_sie` falls back to `banxico_token`); `set` upserts by `(tenant, connector, mode)`, stamps `rotated_at` when it replaces, requires a staff actor (`created_by = UUID(actor_id)`), validates field names (`^[a-z][a-z0-9_]{0,63}$`) and non-empty string values of at most 4096 characters, and audits `connector.credential_set` with the field names only.
10. **The credential endpoint is `PUT /v1/connectors/{name}/credentials/{mode}`** (no `Idempotency-Key`: only `POST` is keyed), body `{"fields": {...}}`, answer 200 with `connector`, `mode`, `field_names`, `created_at`, `rotated_at` and never a value. All three connector routes are ADMIN and staff-only (the spec names ADMIN for usage and the credential set; the list follows them, so the console has one access entry).
11. **Usage is not cursor-paginated**: `from` and `to` are inclusive UTC calendar days, both required, `from <= to`, at most 366 days apart (422 `VALIDATION_FAILED` otherwise); the answer is one row per `(day, connector)` with `calls`, `cost_micros` and `outcomes` (a count per `CallOutcome` value). A day is `created_at` in UTC.
12. **The `sat_rfc` mock is keyed by the RFC's last character**: `7` → `FAIL` (`ValidacionEFOS = 100`, `Vigente`), `8` → `FAIL` (`Cancelado`), `9` → `INCONCLUSIVE` (`No Encontrado`), anything else → `PASS` (`Vigente`, EFOS `200`); an RFC that fails the shape regex is an `ERROR` in both modes. The mock builds the same SOAP XML the live service returns, so `_interpret` has one parser.
13. **The metering row joins the caller's transaction.** A caller that rolls back loses the row (and, once SP-08 writes `verification_check` from a `VerificationOutcome`, that row with it; in SP-02 no plan 2c service writes `verification_check`: its isolation factory is the only writer). An autonomous write would need a second session and connection; not for the MVP (open question 3).
14. **`CheckType` has three members** (`UNIT_VALUE`, `RFC_VALIDATION`, `CURP_VALIDATION`, the spec's "…" is SP-08's); no connector serves `CURP_VALIDATION` here, so a `run_check` for it raises `NoRouteError`.
15. **Mocks also leave evidence** (the mock body under the same key, cost 0), so a `SANDBOX` tenant's calls look like live ones to plan 2c and the console.
16. **The console's "usage chart" is a table** of calls per connector per day for the last 7 or 30 days with an inline `<meter>` per row; no chart library joins the build.
17. **Column conventions** as the kernel models write them (`src/xolotl/platform/api/models.py`, `src/xolotl/modules/tenants/models.py`): spec `text` vocabularies are `enum_column` VARCHAR(32); the spec's `secret_enc bytea` is the column `secret` of type `EncryptedJson` (model attribute `secret`, same name as the column): the kernel drops the `_enc` suffix (`staff_credential.totp_secret`, which migration 0004 renamed from `totp_secret_enc`; `response_body` for `EncryptedJson`), and plans 2a (`tenant_regulatory_profile.rfc`, `tenant_branch.address`) and 2c follow it, so the SP-02 schema reads one way; `cost_micros bigint DEFAULT 0` is `BigInteger, default=0, server_default=text("0")`; `subject_party_id` is a plain nullable `Uuid` with no FK (the `party` table is plan 2c's and a platform table cannot reference a module's).
18. **The SAT SOAP XML is parsed with `defusedxml`** (as Xolo does; ruff `S314` refuses `xml.etree` and the SAT answer is untrusted input), with `types-defusedxml` for basedpyright.
19. **`GET /v1/connectors` reports `credential_modes`** (the modes a credential row exists for) beyond the spec's mode, route and circuit state, so an admin can tell that a `LIVE` connector has a credential without revealing it; the console shows it as the "Credenciales" column.

## Review Focus

1. **Credentials must never leave the vault's ciphertext.** A token that `_call` puts in a request header and that the remote echoes in its body must not appear in the evidence file, in `CheckResult.details` (which SP-08's verification writer will copy into `verification_check`; in SP-02 nothing in plan 2c calls `run_check`), in `connector_call.error`, in any table (`everything_stored`), or in the logs at DEBUG (`caplog` with `sqlalchemy.engine`). Test `test_credentials_reach_the_call_and_never_the_evidence_or_logs` in Task B3a, whose interpreter copies the echoed field into `details` and expects `{"echo": "[REDACTED]"}`.
2. **Sandbox forcing is absolute, and a mock is always route `MOCK`.** A `SANDBOX` tenant whose caller passes `mode=ConnectorMode.LIVE` must get the mock and never a network call, with the row saying `mode = SANDBOX`, `route = MOCK`; a `PROD` tenant whose call is `SANDBOX` (the default mode or an explicit one) gets the same row. Tests `test_a_sandbox_tenant_always_gets_the_mock` and `test_a_sandbox_mode_call_of_a_prod_tenant_is_a_mock_on_route_mock` (B3a, fake connector), `test_run_check_uses_the_default_mode_for_a_prod_tenant_and_the_explicit_one_when_given` (B3b), `test_a_sandbox_tenant_never_reaches_the_sat_service` and `test_run_check_through_the_default_router_gives_a_sandbox_tenant_the_mock_and_a_metering_row` (B4a, `respx` route uncalled, the metering row and the evidence object asserted).
3. **Breaker time boundaries.** Four failures, 61 seconds, one more failure must leave the breaker `CLOSED`; five within the window must open it; at 29 seconds it is still `OPEN` and at 30 `HALF_OPEN`; a `HALF_OPEN` trial that fails must reopen it for a full further 30 seconds; a second caller during a trial must be refused. Tests in Task B2 (`test_circuit_breaker.py`) with an injected monotonic clock, and end to end in B3a on one `FakeConnector` instance re-scripted through `fake.calls`: `test_five_failures_in_a_minute_open_the_circuit_and_the_next_call_is_refused`, `test_the_circuit_half_opens_after_thirty_seconds_and_a_success_closes_it`, `test_a_failed_trial_reopens_the_circuit_for_another_thirty_seconds` (the second-caller case stays a breaker unit test: one `run` completes its trial synchronously).
4. **SAT parsing on hostile input.** A non-XML body, an XML without `Estado`, an HTTP 500 that retries three times, and an RFC containing `&` (escaped in `expresionImpresa` but not in the subject check) must each give the documented outcome without an exception leaving `run`. Tests in Task B4a (`test_connector_sat.py`), at the parser (`test_unreadable_answers_are_interpretation_errors`) and through `run` (`test_a_200_with_an_unreadable_body_is_an_error_outcome`, `test_a_500_is_retried_three_times_then_an_error`).
5. **Usage aggregation across the UTC day boundary and across tenants.** A call at `2026-10-18T23:59:59Z` counts on 18 October and one at `2026-10-19T00:00:01Z` on 19 October; another tenant's calls never appear even when the owner can see them. Test `test_usage_aggregates_per_connector_per_utc_day_for_this_tenant_only` in Task B5.

## File structure

```
pyproject.toml, uv.lock                              (+ defusedxml runtime, types-defusedxml dev)                   [B4a]
.env.example, README.md, render.yaml                 (+ XOLOTL_CONNECTOR_MODE_DEFAULT; README rows and a Connectors section) [B1, B5]
src/xolotl/platform/config.py                        (+ connector_mode_default)                                      [B1]
src/xolotl/platform/enums.py                         (+ ConnectorMode, CheckType, ConnectorRoute, CallOutcome, CircuitState) [B1]
src/xolotl/platform/tenancy/tenants.py               (+ TenantRuntime, tenant_runtime: env and cell of the session's tenant) [B2]
src/xolotl/platform/services.py                      (+ connectors: ConnectorRouter)                                 [B3b]
src/xolotl/app.py                                    (wire default_router; mount routes_connectors.router)           [B3b, B5]
src/xolotl/models.py                                 (+ connectors_models)                                           [B1]
src/xolotl/migrations/versions/0007_connectors.py    connector_credential, connector_call                           [B1]
src/xolotl/platform/connectors/__init__.py           re-exports                                                     [B1..B5]
src/xolotl/platform/connectors/models.py             ConnectorCredential, ConnectorCall                              [B1]
src/xolotl/platform/connectors/types.py              ConnectorRequest, RawResponse, CheckResult, VerificationOutcome, the call-error hierarchy [B2]
src/xolotl/platform/connectors/errors.py             NoRouteError (409 CONNECTOR_NO_ROUTE)                           [B2]
src/xolotl/platform/connectors/breaker.py            CircuitBreaker                                                  [B2]
src/xolotl/platform/connectors/vault.py              CredentialVault (get, set + audit)                              [B2]
src/xolotl/platform/connectors/base.py               ConnectorDeps, Connector (run: force sandbox → vault → breaker → attempts → evidence → metering), redact [B3a]
src/xolotl/platform/connectors/router.py             ConnectorRouter, run_check                                      [B3b]
src/xolotl/platform/connectors/defaults.py           DEFAULT_ROUTES, default_router(settings, *, store), the plan-2a adapters (rows_to_pairs, sie_fetch_factory, seed_lookup_from) [B3b, B4a, B4b]
src/xolotl/platform/connectors/metering.py           UsageRow, usage(session, *, start, end)                         [B5]
src/xolotl/platform/connectors/sat.py                the ported SAT functions, CfdiQuery, SatRfcConnector            [B4a]
src/xolotl/platform/connectors/banxico.py            BanxicoSieConnector over a (date, Decimal) fetch callable and a seed lookup [B4b]
src/xolotl/modules/tenants/schemas.py                (+ Connector* response and request models)                      [B5]
src/xolotl/modules/tenants/routes_connectors.py      list, usage, credentials                                        [B5]
tests/platform/test_config.py                        (+ connector_mode_default)                                      [B1]
tests/platform/test_migration_0007.py                round trip through 0006                                         [B1]
tests/platform/test_migration_chain.py               (+ "0007_connectors" in CHAIN)                                  [B1]
tests/isolation/factories.py                         (+ connector_credential, connector_call)                        [B1]
tests/test_render_blueprint.py                       (+ connector_mode_default == "LIVE")                            [B1]
tests/platform/test_circuit_breaker.py               pure breaker tests                                              [B2]
tests/platform/test_connector_vault.py               vault, audit, isolation, no plaintext                           [B2]
tests/platform/test_tenant_runtime.py                env and cell read                                               [B2]
tests/platform/connectors_support.py                 FakeConnector (re-scriptable), make_deps, RecordingSleep, FakeMonotonic, evidence_of, calls_of [B3a]
tests/platform/test_connectors.py                    run, retries, breaker end to end (open, half-open, failed trial), evidence, metering, sandbox, redaction [B3a]
tests/platform/test_connector_router.py              run_check modes, fallback, definitive outcomes, route table errors [B3b]
tests/platform/test_connector_wiring.py              PlatformServices.connectors from build_app and wire_platform    [B3b, B4a, B4b]
tests/platform/test_connector_sat.py                 ported functions, fixtures, live via respx, mock table, run_check through default_router [B4a]
tests/platform/test_connector_banxico.py             pair-shaped fake fetch and seed lookup, token precedence, demo and mock from the seed, rows_to_pairs, seed_lookup_from [B4b]
tests/fixtures/connectors/sat_vigente.xml, sat_efos.xml, sat_cancelado.xml, sat_no_encontrado.xml                [B4a]
tests/modules/tenants/test_connector_routes.py       the three routes, roles, usage boundaries, audit (fake router only) [B5]
console/src/api/schema.d.ts (regenerated), console/src/api/types.ts (+ aliases)                                   [B6]
console/src/auth/access.ts, access.test.ts, guards.test.tsx (+ connectors)                                        [B6]
console/src/components/layout/nav.ts (+ nav.connectors), console/src/App.tsx (+ /connectors)                      [B6]
console/src/i18n/es-MX.json, en.json, catalogue.test.ts (+ connectors.*, nav.connectors, errors.CONNECTOR_NO_ROUTE) [B6]
console/src/features/connectors/queries.ts, ConnectorsPage.tsx, ConnectorsTable.tsx, UsageCard.tsx, badges.tsx, format.ts, format.test.ts, ConnectorsPage.test.tsx [B6]
```

---

### Task B1: Migration 0007, the connector models, their factories and the `connector_mode_default` setting

**Files:**
- Create: `src/xolotl/platform/connectors/__init__.py` (module docstring and, for now, the enum re-exports), `src/xolotl/platform/connectors/models.py`, `src/xolotl/migrations/versions/0007_connectors.py`
- Modify: `src/xolotl/platform/enums.py`, `src/xolotl/platform/config.py`, `src/xolotl/models.py`, `.env.example`, `README.md` (configuration table), `render.yaml`
- Test: `tests/platform/test_config.py` (append), `tests/platform/test_migration_0007.py` (new), `tests/platform/test_migration_chain.py` (append `"0007_connectors"`), `tests/isolation/factories.py` (two factories), `tests/test_render_blueprint.py` (one assertion)

Precondition: plan 2a's A1 and A2 merged. A1 declares `banxico_token` (B1's defaults test reads it), owns its `.env.example` line and README row, and creates `src/xolotl/platform/text.py` (which B4a imports); A2's migration `0006_policy_units_calendar` is what the chain and `down_revision` depend on.

**Interfaces:**

Consumes: `TenantTableMixin`, `tenant_table_args`, `enable_rls` (`xolotl.platform.tenancy.ddl`); `enum_column` (`xolotl.platform.sqltypes`); `Encrypted`, `EncryptedJson`, `encrypt_json` (`xolotl.platform.crypto.encrypted`); `get_key_provider` (`xolotl.platform.crypto.provider`); `uuid7`; the `CHAIN` list in `tests/platform/test_migration_chain.py` (plan 2a's A2 creates it; if 2a left the single-head pin in `tests/platform/test_migration_0005.py`, update that `get_heads()` assertion instead: `grep -rn get_heads tests/` finds it).

Produces:

```python
# src/xolotl/platform/enums.py (append; UPPERCASE values, String(32) columns)
class ConnectorMode(StrEnum): SANDBOX = "SANDBOX"; LIVE = "LIVE"
class CheckType(StrEnum): UNIT_VALUE = "UNIT_VALUE"; RFC_VALIDATION = "RFC_VALIDATION"; CURP_VALIDATION = "CURP_VALIDATION"
class ConnectorRoute(StrEnum): PRIMARY = "PRIMARY"; FALLBACK = "FALLBACK"; MOCK = "MOCK"
class CallOutcome(StrEnum): PASS = "PASS"; FAIL = "FAIL"; INCONCLUSIVE = "INCONCLUSIVE"; ERROR = "ERROR"; TIMEOUT = "TIMEOUT"; CIRCUIT_OPEN = "CIRCUIT_OPEN"
class CircuitState(StrEnum): CLOSED = "CLOSED"; OPEN = "OPEN"; HALF_OPEN = "HALF_OPEN"

# src/xolotl/platform/connectors/models.py
class ConnectorCredential(TenantTableMixin, Base):
    __tablename__: str = "connector_credential"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "connector_credential", UniqueConstraint("tenant_id", "connector", "mode")
    )
    connector: Mapped[str] = mapped_column(Text)
    mode: Mapped[ConnectorMode] = mapped_column(enum_column(ConnectorMode))
    secret: Mapped[Encrypted[dict[str, object]]] = mapped_column(EncryptedJson)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class ConnectorCall(TenantTableMixin, Base):
    __tablename__: str = "connector_call"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "connector_call", Index("ix_connector_call_tenant_day", "tenant_id", "created_at")
    )
    connector: Mapped[str] = mapped_column(Text)
    check_type: Mapped[CheckType] = mapped_column(enum_column(CheckType))
    mode: Mapped[ConnectorMode] = mapped_column(enum_column(ConnectorMode))
    route: Mapped[ConnectorRoute] = mapped_column(enum_column(ConnectorRoute))
    outcome: Mapped[CallOutcome] = mapped_column(enum_column(CallOutcome))
    duration_ms: Mapped[int] = mapped_column(Integer)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    evidence_key: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    subject_party_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

# src/xolotl/platform/config.py (Settings, after object_store_root)
# The connector mode a PROD tenant gets when a call names none; a SANDBOX tenant
# always uses mocks. render.yaml sets LIVE for production.
connector_mode_default: Literal["SANDBOX", "LIVE"] = "SANDBOX"
# (plan 2a declares, beside it:)  banxico_token: SecretStr | None = None
```

Migration `0007_connectors.py` (header as `src/xolotl/migrations/versions/0005_encrypt_idempotency.py` writes it: a module docstring with `Revision ID: 0007_connectors`, `Revises: 0006_policy_units_calendar`, `Create Date: 2026-10-19`; `from collections.abc import Sequence`, `import sqlalchemy as sa`, `from alembic import op`, `from xolotl.platform.tenancy.ddl import enable_rls`; `revision: str = "0007_connectors"`, `down_revision: str | None = "0006_policy_units_calendar"`, `branch_labels: str | Sequence[str] | None = None`, `depends_on: str | Sequence[str] | None = None`): `upgrade()` creates `connector_credential` (`sa.Column("tenant_id", sa.Uuid(), nullable=False)`, `id`, `connector sa.Text()`, `mode sa.String(length=32)`, `secret sa.LargeBinary()`, `created_by sa.Uuid()`, `created_at sa.DateTime(timezone=True), server_default=sa.text("now()")`, `rotated_at sa.DateTime(timezone=True), nullable=True`, `sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_connector_credential")`, `sa.UniqueConstraint("tenant_id", "connector", "mode", name="uq_connector_credential_tenant_id_connector_mode")`) then `enable_rls("connector_credential")`; then `connector_call` (`connector sa.Text()`, `check_type`, `mode`, `route`, `outcome` all `sa.String(length=32)`, `duration_ms sa.Integer()`, `cost_micros sa.BigInteger(), server_default=sa.text("0")`, `evidence_key sa.Text(), nullable=True`, `error sa.Text(), nullable=True`, `subject_party_id sa.Uuid(), nullable=True`, `created_at ... now()`, `sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_connector_call")`), `op.create_index("ix_connector_call_tenant_day", "connector_call", ["tenant_id", "created_at"])`, `enable_rls("connector_call")`. `downgrade()`: `op.drop_table("connector_call")`, `op.drop_table("connector_credential")`.

- [ ] **Step 1: Write the failing tests.**

`tests/platform/test_config.py` (append; the module's `_clean_environment` and `make_settings` apply):

```python
def test_connector_settings_default_to_sandbox_and_no_token():
    s = make_settings()
    assert s.connector_mode_default == "SANDBOX"
    assert s.banxico_token is None  # plan 2a's A1 field: a cross-check that A1 is merged


def test_connector_settings_are_read_from_the_environment(monkeypatch):
    # Settings ignores names it does not know, so setting the variable and seeing
    # the value change proves the field reads XOLOTL_CONNECTOR_MODE_DEFAULT.
    # (XOLOTL_BANXICO_TOKEN is plan 2a's A1 field: its
    # test_sp02_settings_read_their_variables already pins the read and the repr.)
    monkeypatch.setenv("XOLOTL_CONNECTOR_MODE_DEFAULT", "LIVE")
    s = make_settings()
    assert s.connector_mode_default == "LIVE"


def test_the_connector_mode_rejects_nonsense(monkeypatch):
    monkeypatch.setenv("XOLOTL_CONNECTOR_MODE_DEFAULT", "demo")
    with pytest.raises(ValidationError, match="connector_mode_default"):
        _ = make_settings()
```

and in `test_env_example_is_a_valid_configuration` add `connector_mode_default` to the set the `<= s.model_fields_set` assertion checks (plan 2a's A1 has already added `packs_dir`, `allow_unverified_pack_values` and `units_fetch_cron` to it and plan 2c's C2 adds `retention_purge_cron`: add the one name, drop none).

`tests/test_render_blueprint.py`: in `test_the_environment_render_gives_is_a_valid_production_configuration` add `assert settings.connector_mode_default == "LIVE"`.

`tests/platform/test_migration_0007.py` (copy the `go_down`/`database` fixture pattern of `test_migration_0005.py`):

```python
REVISION = "0007_connectors"
BEFORE = "0006_policy_units_calendar"
TABLES = ("connector_credential", "connector_call")


def test_0007_follows_0006():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_downgrade_drops_both_tables_and_upgrade_recreates_them_with_forced_rls(database):
    go_down(BEFORE)
    assert tables_present(database) == set()
    migrate.upgrade()
    assert tables_present(database) == set(TABLES)
    with database.connect() as conn:
        rows = conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:names)"), {"names": list(TABLES)}).all()
    assert sorted(rows) == [("connector_call", True, True), ("connector_credential", True, True)]
    with database.connect() as conn:
        index = conn.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_connector_call_tenant_day'"))
    assert index is not None
    assert "(tenant_id, created_at)" in index
```

(`tables_present(engine) -> set[str]` is a module helper: `inspect(engine).get_table_names()` intersected with `TABLES`.)

`tests/platform/test_migration_chain.py`: append `"0007_connectors"` to `CHAIN`.

`tests/isolation/factories.py`:

```python
def connector_credential(session: Session, tenant_id: UUID) -> None:
    # A real ciphertext, as staff_credential does; the harness makes bare tenants.
    get_key_provider().create_tenant_keys(tenant_id, session=session)
    session.add(
        ConnectorCredential(
            tenant_id=tenant_id, connector="sat_rfc", mode=ConnectorMode.SANDBOX,
            secret=encrypt_json({"token": "t"}, tenant_id=tenant_id, session=session),
            created_by=uuid7(),
        )
    )
    session.flush()


def connector_call(session: Session, tenant_id: UUID) -> None:
    session.add(
        ConnectorCall(
            tenant_id=tenant_id, connector="sat_rfc", check_type=CheckType.RFC_VALIDATION,
            mode=ConnectorMode.SANDBOX, route=ConnectorRoute.MOCK, outcome=CallOutcome.PASS,
            duration_ms=12,
        )
    )
    session.flush()
```

registered in `register_all()` as `register_isolation_factory("connector_credential", connector_credential)` and `register_isolation_factory("connector_call", connector_call)`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_config.py tests/platform/test_migration_0007.py tests/platform/test_migration_chain.py tests/isolation -v --no-cov` → FAIL: `test_config` with `AttributeError: connector_mode_default` (and the nonsense test with `DID NOT RAISE`), `test_migration_0007` with revision `0007_connectors` not found, `tests/isolation` with `ImportError` on `xolotl.platform.connectors.models` at collection (`test_behavioural.py` imports `factories` at module level, so the `register an isolation factory for: [...]` message cannot appear until the models exist and the factories are the missing piece).
- [ ] **Step 3: Implement.** The five enums; the two models as above with the imports the kernel models use (`from xolotl.platform.crypto.encrypted import Encrypted, EncryptedJson` as `src/xolotl/platform/api/models.py` and `src/xolotl/modules/tenants/models.py` import them, `from xolotl.platform.db import Base`, `from xolotl.platform.enums import ...`, `from xolotl.platform.sqltypes import enum_column`, `from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_table_args`, `from sqlalchemy.sql.schema import SchemaItem`); the migration; `from xolotl.platform.connectors import models as connectors_models` and `"connectors_models"` in `src/xolotl/models.py` (keep `__all__` sorted); the setting with its comment; the `.env.example` line `XOLOTL_CONNECTOR_MODE_DEFAULT=SANDBOX` and the README configuration row `| XOLOTL_CONNECTOR_MODE_DEFAULT | SANDBOX | Connector mode of a PROD tenant when a call names none. SANDBOX tenants always use mocks. The Render blueprint sets LIVE. |`; B1 owns only `XOLOTL_CONNECTOR_MODE_DEFAULT`: the commented `# XOLOTL_BANXICO_TOKEN=` example line and the `| XOLOTL_BANXICO_TOKEN | unset | ... |` README row are plan 2a's A1's and are on `main` already (B1's precondition); leave them as A1 wrote them (B5's README "Connectors" section is where this plan describes the connector's use of the token); `render.yaml` `envVars` gains `- key: XOLOTL_CONNECTOR_MODE_DEFAULT` / `value: LIVE` (a string; no `XOLOTL_BANXICO_TOKEN` entry: `test_the_operator_pastes_one_secret_the_master_key` pins the prompted list, the operator adds the token in the dashboard, which the README row says); the two factories.
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/platform/test_config.py tests/platform/test_migration_0007.py tests/platform/test_migration_chain.py tests/platform/test_schema_sync.py tests/platform/test_alembic_env.py tests/platform/test_ddl.py tests/isolation tests/test_render_blueprint.py -v --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): connector_credential and connector_call tables, migration 0007 and the connector mode setting`.

---

### Task B2: Types, the circuit breaker, the credential vault and the tenant runtime read

**Files:**
- Create: `src/xolotl/platform/connectors/types.py`, `src/xolotl/platform/connectors/errors.py`, `src/xolotl/platform/connectors/breaker.py`, `src/xolotl/platform/connectors/vault.py`
- Modify: `src/xolotl/platform/connectors/__init__.py` (re-exports), `src/xolotl/platform/tenancy/tenants.py` (+ `TenantRuntime`, `tenant_runtime`)
- Test: `tests/platform/test_circuit_breaker.py`, `tests/platform/test_connector_vault.py`, `tests/platform/test_tenant_runtime.py`, `tests/platform/test_errors.py` (one row appended)

**Interfaces:**

Consumes (B1): `ConnectorCredential`, `ConnectorMode`, `CheckType`, `ConnectorRoute`, `CallOutcome`, `CircuitState`. Kernel: `bound_context` (`xolotl.platform.db`), `record`, `AuditEvent` (`xolotl.platform.audit`), `encrypt_json` (`xolotl.platform.crypto.encrypted`), `clock.now`, `ActorType`, `DomainError`, `ForbiddenError`, `NotFoundError`, `ValidationFailedError` (`xolotl.platform.errors`).

Produces:

```python
# types.py
@dataclass(frozen=True, slots=True)
class ConnectorRequest:
    check_type: CheckType
    subject_party_id: UUID | None
    params: Mapping[str, str]
    country: str = "MX"

@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    elapsed_ms: int

class CheckResult(BaseModel):                       # model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    outcome: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    details: dict[str, JsonValue]
    cost_micros: int = 0

@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    call_id: UUID
    connector: str
    check_type: CheckType
    mode: ConnectorMode
    route: ConnectorRoute
    outcome: CallOutcome
    details: dict[str, JsonValue]
    evidence_key: str | None
    duration_ms: int
    cost_micros: int
    attempts: int
    error: str | None

class ConnectorCallError(Exception): ...           # raised inside _call/_interpret/_mock; never leaves run
class TransientCallError(ConnectorCallError): ...  # run retries it and counts a breaker failure (classified by class; no flag to drift)
class PermanentCallError(ConnectorCallError): ...  # run stops at once and counts no breaker failure
class CredentialMissingError(PermanentCallError): ...
class InterpretationError(PermanentCallError): ...  # _interpret could not read the answer

# errors.py
class NoRouteError(DomainError):
    default_code: ClassVar[str] = "CONNECTOR_NO_ROUTE"
    default_detail: ClassVar[str] = "no connector routes this check type for this country"

# breaker.py
FAILURE_THRESHOLD = 5
FAILURE_WINDOW_S = 60.0
OPEN_FOR_S = 30.0

class CircuitBreaker:
    def __init__(self, *, clock_fn: Callable[[], float] = time.monotonic) -> None
        # the three module constants are the spec's numbers; nothing configures them, so no knob is taken
    @property
    def state(self) -> CircuitState      # OPEN turns HALF_OPEN once OPEN_FOR_S elapsed since it opened
    def allow(self) -> bool              # CLOSED: True; OPEN: False; HALF_OPEN: True for one trial, then False until the trial reports
    def record_success(self) -> None     # CLOSED, failures cleared, trial over
    def record_failure(self) -> None     # drops failures older than FAILURE_WINDOW_S, appends now; at FAILURE_THRESHOLD (or during a trial) → OPEN from now

# vault.py
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_FIELD_VALUE_LEN = 4096

class CredentialVault:
    def get(self, session: Session, connector: str, mode: ConnectorMode) -> Mapping[str, str]
        # this tenant's row, revealed with session=session, string values only; {} when none
    def set(self, session: Session, connector: str, mode: ConnectorMode, fields: Mapping[str, str]) -> ConnectorCredential
        # staff only (ForbiddenError otherwise); validates; upserts by (tenant, connector, mode): a new row,
        # or secret replaced and rotated_at = clock.now(); flush; then
        # record(session, AuditEvent(action="connector.credential_set", entity_type="connector_credential",
        #        entity_id=str(row.id), detail={"connector": connector, "mode": mode.value,
        #        "field_names": sorted(fields), "rotated": existed}))

# tenancy/tenants.py (the lightweight table gains env and cell)
_TENANT = table("tenant", column("id", Uuid), column("env", String(32)), column("cell", Text))

@dataclass(frozen=True, slots=True)
class TenantRuntime:
    env: TenantEnv
    cell: str

def tenant_runtime(session: Session, tenant_id: UUID) -> TenantRuntime
    # a tenant-bound session reads its own row (the tenant policy admits id = app.tenant_id); NotFoundError when none
```

`__init__.py` re-exports: the five enums, `ConnectorRequest`, `RawResponse`, `CheckResult`, `VerificationOutcome`, `ConnectorCallError`, `TransientCallError`, `PermanentCallError`, `CredentialMissingError`, `InterpretationError`, `NoRouteError`, `CircuitBreaker`, `CredentialVault` (sorted `__all__`).

- [ ] **Step 1: Write the failing tests.**

`tests/platform/test_circuit_breaker.py` (no database; `FakeMonotonic` is a tiny class with `now: float` and `advance(seconds)`; `breaker = CircuitBreaker(clock_fn=fake)`):

```python
def test_a_new_breaker_is_closed_and_allows():
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True

def test_five_failures_within_sixty_seconds_open_it():
    for _ in range(4): breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False

def test_failures_older_than_the_window_are_forgotten():
    for _ in range(4): breaker.record_failure()
    fake.advance(61)
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED

def test_it_half_opens_after_thirty_seconds_and_admits_one_trial():
    for _ in range(5): breaker.record_failure()
    fake.advance(29.9); assert breaker.state is CircuitState.OPEN; assert breaker.allow() is False
    fake.advance(0.1)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is True      # the trial
    assert breaker.allow() is False     # a second caller waits for the trial's verdict

def test_a_successful_trial_closes_it():
    ...; breaker.record_success(); assert breaker.state is CircuitState.CLOSED; assert breaker.allow() is True

def test_a_failed_trial_reopens_it_for_another_thirty_seconds():
    ...; breaker.record_failure(); assert breaker.state is CircuitState.OPEN
    fake.advance(29); assert breaker.state is CircuitState.OPEN
    fake.advance(1); assert breaker.state is CircuitState.HALF_OPEN

def test_a_success_while_closed_clears_the_failure_count():
    for _ in range(4): breaker.record_failure()
    breaker.record_success()
    for _ in range(4): breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
```

`tests/platform/test_connector_vault.py` (database; `tenant` fixture; sessions through `staff_session(tenant.tenant_id, tenant.admin_user_id)`; `vault = CredentialVault()`):

```python
def test_set_then_get_round_trips_and_an_unset_pair_is_empty(tenant):
    with staff_session(t, u) as s:
        row = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "tok-1", "user": "ana"})
        assert row.rotated_at is None
    with tenant_session(system_ctx(t)) as s:
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.LIVE)) == {"token": "tok-1", "user": "ana"}
        assert dict(vault.get(s, "sat_rfc", ConnectorMode.SANDBOX)) == {}
        assert dict(vault.get(s, "banxico_sie", ConnectorMode.LIVE)) == {}

def test_the_secret_is_stored_as_ciphertext_and_the_audit_names_fields_only(tenant, admin_engine):
    ... set {"token": "tok-secret-1"} ...
    with admin_engine.connect() as conn:
        raw = conn.scalar(text("SELECT secret FROM connector_credential WHERE tenant_id = :t"), {"t": t})
    assert b"tok-secret-1" not in raw
    assert leaked(everything_stored(admin_engine), b"tok-secret-1") == []
    assert [(e.action, e.detail) for e in audit_events(t) if e.action == "connector.credential_set"] == [
        ("connector.credential_set", {"connector": "sat_rfc", "mode": "LIVE", "field_names": ["token"], "rotated": False}),
    ]

def test_setting_again_replaces_the_fields_and_stamps_rotated_at(tenant, monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: T0)
    ... set {"token": "old"} then set {"token": "new"} ...
    assert row.rotated_at == T0
    assert dict(vault.get(s, "sat_rfc", ConnectorMode.LIVE)) == {"token": "new"}
    assert [e.detail["rotated"] for e in credential_events(t)] == [False, True]
    assert count of connector_credential rows for t == 1

@pytest.mark.parametrize(("fields", "match"), [
    ({}, "at least one"), ({"Bad Key": "x"}, "field name"), ({"token": ""}, "empty"), ({"token": "x" * 4097}, "4096")])
def test_invalid_fields_are_a_422(tenant, fields, match):
    with pytest.raises(ValidationFailedError, match=match): vault.set(s, "sat_rfc", ConnectorMode.LIVE, fields)

def test_only_staff_may_set(tenant):
    with tenant_session(system_ctx(t)) as s, pytest.raises(ForbiddenError):
        _ = vault.set(s, "sat_rfc", ConnectorMode.LIVE, {"token": "x"})

def test_another_tenant_sees_nothing(tenant):
    other = new_tenant("beta", "admin@beta.mx")
    ... set for tenant; get under system_ctx(other.tenant_id) == {}
```

`tests/platform/test_tenant_runtime.py`:

```python
def test_a_tenant_session_reads_its_own_env_and_cell(engine):
    t = make_tenant("sbx", env=TenantEnv.SANDBOX)
    with tenant_session(system_ctx(t)) as s:
        assert tenant_runtime(s, t) == TenantRuntime(env=TenantEnv.SANDBOX, cell="mx-1")

def test_another_tenants_row_is_not_found(engine):
    a, b = make_tenant("a"), make_tenant("b")
    with tenant_session(system_ctx(a)) as s, pytest.raises(NotFoundError):
        _ = tenant_runtime(s, b)
```

`tests/platform/test_errors.py`: add `(NoRouteError, 409, "CONNECTOR_NO_ROUTE")` to the parametrised list of `test_error_status_and_default_code`, with `from xolotl.platform.connectors import NoRouteError` (plan 2a's A2 and A6 and plan 2c's C3 append their own rows to the same list: additive, the second to merge rebases).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_circuit_breaker.py tests/platform/test_connector_vault.py tests/platform/test_tenant_runtime.py tests/platform/test_errors.py -v --no-cov` → FAIL with `ImportError` on the new modules and names.
- [ ] **Step 3: Implement.** The four modules as specified. `CircuitBreaker` keeps `_failures: deque[float]`, `_opened_at: float | None`, `_trial: bool`, under one `threading.Lock`; `state` is computed from the clock on every read. `CredentialVault.get`: `select(ConnectorCredential).where(connector == ..., mode == ...)` (RLS scopes the tenant), `row.secret.reveal(session=session)`, keep `str` values. `set`: `ctx = bound_context(session)`; `if ctx.actor_type is not ActorType.STAFF: raise ForbiddenError(msg)`; validation messages `"at least one field is required"`, `"invalid field name"`, `"a field value is empty"`, `"a field value exceeds 4096 characters"`; load the existing row by the unique pair under a row lock, `session.scalars(select(ConnectorCredential).where(ConnectorCredential.connector == connector, ConnectorCredential.mode == mode).with_for_update()).one_or_none()` (RLS adds the tenant; `with_for_update=True` is `Session.get`'s keyword and needs the primary key, which this lookup does not have); `payload: dict[str, object] = dict(fields)` then `encrypt_json(payload, tenant_id=tenant_id, session=session)` (`dict` is invariant in its value type, so basedpyright refuses a `dict[str, str]` where `dict[str, object]` is expected; the factories' literal is fine because a literal is inferred against the parameter, and the same care applies to every `CheckResult.details` and `VerificationOutcome.details` literal: build it in the constructor call, not in a `dict[str, str]` variable first); `record` last. `tenant_runtime`: `session.execute(select(_TENANT.c.env, _TENANT.c.cell).where(_TENANT.c.id == tenant_id)).one_or_none()`, `TenantEnv(row.env)`.
- [ ] **Step 4: Run to verify pass.** The four test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): request and outcome types, circuit breaker, encrypted credential vault and the tenant runtime read`.

---

### Task B3a: The `Connector` base: run, retries, the breaker, evidence, metering and redaction

**Files:**
- Create: `src/xolotl/platform/connectors/base.py`
- Modify: `src/xolotl/platform/connectors/__init__.py` (+ `Connector`, `ConnectorDeps`, `redact`)
- Test: `tests/platform/connectors_support.py` (the fake connector and the helpers every later connector test imports), `tests/platform/test_connectors.py`

**Interfaces:**

Consumes: B1 and B2 names (`ConnectorCall`, the five enums, the types, the call-error hierarchy, `CircuitBreaker`, `CredentialVault`, `tenant_runtime`); `ObjectStore` (`xolotl.platform.storage`); `LocalFsObjectStore` (tests); `safe_exception_summary` (`xolotl.platform.logging`); `uuid7`; `bound_context` (`xolotl.platform.db`); `TenantEnv`; `make_tenant_with_keys`, `tenant_session`, `staff_session`, `system_ctx`, `leaked`, `everything_stored` (`tests/helpers`).

Produces:

```python
# base.py
DEFAULT_TIMEOUT_S = 8.0
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_S: tuple[float, ...] = (0.2, 0.5)        # after attempt 1, after attempt 2
EVIDENCE_PREFIX = "connectors"
REDACTED = "[REDACTED]"

def redact(text: str, secrets: Iterable[str]) -> str              # longest secret first; empty secrets skipped
def redact_bytes(body: bytes, secrets: Iterable[str]) -> bytes    # the same on utf-8 bytes
def redact_details(details: dict[str, JsonValue], secrets: Iterable[str]) -> dict[str, JsonValue]
    # redact() on every top-level str value, other values untouched (one dict comprehension)

@dataclass(frozen=True, slots=True)
class ConnectorDeps:
    vault: CredentialVault
    store: ObjectStore
    clock_fn: Callable[[], float] = time.monotonic   # breakers and durations
    sleep_fn: Callable[[float], None] = time.sleep   # the backoff; tests record instead of sleeping

class Connector(ABC):
    timeout_s: ClassVar[float] = DEFAULT_TIMEOUT_S
    max_attempts: ClassVar[int] = DEFAULT_MAX_ATTEMPTS
    evidence_content_type: ClassVar[str] = "application/octet-stream"

    def __init__(self, deps: ConnectorDeps, *, name: str, check_types: frozenset[CheckType]) -> None
        # self.name, self.check_types, self._deps, self._breakers: dict[str, CircuitBreaker]
    def breaker(self, cell: str) -> CircuitBreaker            # creates on first use, clock_fn from deps
    def circuit_state(self, cell: str) -> CircuitState        # CLOSED when no breaker exists yet (creates none)
    @abstractmethod
    def _call(self, request: ConnectorRequest, credentials: Mapping[str, str]) -> RawResponse: ...
    @abstractmethod
    def _interpret(self, raw: RawResponse) -> CheckResult: ...
    @abstractmethod
    def _mock(self, request: ConnectorRequest) -> RawResponse: ...
    def run(self, session: Session, request: ConnectorRequest, *, mode: ConnectorMode,
            route: ConnectorRoute = ConnectorRoute.PRIMARY) -> VerificationOutcome
```

`run` (the algorithm is open, so its body is decided here; split into `_attempts`, `_store_evidence`, `_meter` to stay under mccabe 10 and three nested blocks):

1. `ctx = bound_context(session)`, `tenant_id = ctx.require_tenant_id()`, `runtime = tenant_runtime(session, tenant_id)`, `call_id = uuid7()`, `started = clock_fn()`; `if runtime.env is TenantEnv.SANDBOX: mode = ConnectorMode.SANDBOX`; then `if mode is ConnectorMode.SANDBOX: route = ConnectorRoute.MOCK`. A mock answer always carries `MOCK`, whatever made the call `SANDBOX` (the tenant's `env`, the router's default mode or an explicit `mode`); a `LIVE` call keeps the `route` the router passed.
2. `mode is SANDBOX`: `credentials = {}` (the vault is never read for a mock, Deviations 2); one try of `self._mock(request)` → `raw`, or an exception → `ERROR` (`attempts = 0`).
3. `LIVE`: `credentials = vault.get(session, self.name, mode)`; `breaker = self.breaker(runtime.cell)`; `if not breaker.allow()`: outcome `CIRCUIT_OPEN`, `error = "circuit open"`, `attempts = 0`, no evidence, go to 6. Otherwise `for attempt in range(1, self.max_attempts + 1)`: `raw = self._call(request, credentials)`; `raw.status >= 500` → `breaker.record_failure()`, last error `f"HTTP {raw.status}"`, kind `ERROR`, keep `raw` as the evidence candidate, retry; `400 <= raw.status < 500` → kind `ERROR`, error `f"HTTP {raw.status}"`, stop; `< 400` → `breaker.record_success()`, stop with success. Exceptions, classified by class alone (Deviations 7; the hierarchy is the only source of truth): `httpx.TimeoutException` → `record_failure`, kind `TIMEOUT`, retry; `httpx.HTTPError | OSError | TransientCallError` → `record_failure`, kind `ERROR`, retry; `PermanentCallError` → kind `ERROR`, stop; any other `Exception` (one `# ruff: ignore[blind-except]` as `src/xolotl/platform/api/health.py` writes it, with the comment "a connector must never take the caller's transaction down") → kind `ERROR`, stop. Between attempts `sleep_fn(BACKOFF_S[attempt - 1])`. The error text is `safe_exception_summary(exc)` passed through `redact(..., credentials.values())`.
4. On a successful `raw`: `result = self._interpret(raw)` → outcome `CallOutcome(result.outcome)`, `details = redact_details(result.details, credentials.values())` (a remote that echoes a credential must not reach `verification_check` through `details` either, once SP-08 copies them there), `cost = result.cost_micros` (0 when `route is MOCK`); an exception → `ERROR` with the summary, `details = {}`.
5. Evidence: when a `raw` exists (success, a 4xx/5xx answer, or a mock): `key = f"{EVIDENCE_PREFIX}/{self.name}/{call_id}"`, `store.put(tenant_id, key, redact_bytes(raw.body, credentials.values()), content_type=self.evidence_content_type)`; a store exception turns the outcome into `ERROR` with its summary and `evidence_key = None`.
6. `duration_ms = int((clock_fn() - started) * 1000)`; `session.add(ConnectorCall(tenant_id=tenant_id, id=call_id, connector=self.name, check_type=request.check_type, mode=mode, route=route, outcome=outcome, duration_ms=duration_ms, cost_micros=cost, evidence_key=key_or_none, error=error_or_none, subject_party_id=request.subject_party_id))`; `session.flush()`; `logger.info("connector call", extra={"connector": self.name, "call_id": str(call_id), "outcome": outcome.value, "route": route.value, "attempts": attempts, "duration_ms": duration_ms})`; return the `VerificationOutcome`.

- [ ] **Step 1: Write the failing tests.**

`tests/platform/connectors_support.py`:

```python
class FakeMonotonic:              # now: float = 1000.0; __call__() -> float; advance(seconds: float) -> None
class RecordingSleep:             # calls: list[float]; __call__(seconds: float) -> None appends

def make_deps(tmp_path: Path, *, clock: FakeMonotonic | None = None) -> tuple[ConnectorDeps, LocalFsObjectStore, RecordingSleep]
    # ConnectorDeps(vault=CredentialVault(), store=LocalFsObjectStore(tmp_path / "objects"), clock_fn=clock or FakeMonotonic(), sleep_fn=RecordingSleep())

Scripted = RawResponse | BaseException

class FakeConnector(Connector):
    evidence_content_type = "application/json"
    def __init__(self, deps, *, name: str = "fake", check_types=frozenset({CheckType.RFC_VALIDATION}),
                 calls: Iterable[Scripted] = (), interpret: Callable[[RawResponse], CheckResult] | None = None,
                 cost_micros: int = 7) -> None
    # calls: list[Scripted]  the script, public and mutable (self.calls = list(calls)): a test extends it
    #        (fake.calls.extend([ok()])) to script further attempts on the same instance, because the
    #        breakers live on the instance and a half-open trial must run on the one that opened
    # seen_credentials: list[dict[str, str]]; call_count: int
    # _call: appends dict(credentials) to seen_credentials, increments call_count, pops calls[0]
    #        (raises it when it is an exception; raises AssertionError("unscripted call") when the script is empty)
    # _interpret: interpret(raw) when given, else PASS with details {"status": raw.status} and cost_micros
    # _mock: RawResponse(200, b'{"mock": true, "rfc": "<params rfc>"}', {"content-type": "application/json"}, 0)

def ok(body: bytes = b'{"ok": 1}', status: int = 200) -> RawResponse
def evidence_of(store_root: Path, tenant_id: UUID, outcome: VerificationOutcome) -> bytes   # (root / str(tenant_id) / outcome.evidence_key).read_bytes()
def calls_of(tenant_id: UUID) -> list[ConnectorCall]                                        # system session, ordered by created_at, id
def request(rfc: str = "XAXX010101000", **params: str) -> ConnectorRequest                   # RFC_VALIDATION, subject_party_id=None, params={"rfc": rfc, **params}
```

`tests/platform/test_connectors.py` (database; fixtures `prod` and `sandbox` return `make_tenant_with_keys("prod", env=TenantEnv.PROD)` and `make_tenant_with_keys("sbx", env=TenantEnv.SANDBOX)` after the `engine` fixture, in the idiom of `tests/platform/test_encrypted_json.py`; per test `deps, store, sleep = make_deps(tmp_path)`; calls run inside `tenant_session(system_ctx(tenant_id))`):

```python
def test_a_live_call_passes_stores_evidence_and_meters(tmp_path, engine, prod):
    deps, store, sleep = make_deps(tmp_path); fake = FakeConnector(deps, calls=[ok()])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(), mode=ConnectorMode.LIVE)
    assert (out.outcome, out.attempts, out.route, out.mode, out.cost_micros, out.error) == (
        CallOutcome.PASS, 1, ConnectorRoute.PRIMARY, ConnectorMode.LIVE, 7, None)
    assert out.evidence_key == f"connectors/fake/{out.call_id}"
    assert evidence_of(tmp_path / "objects", prod, out) == b'{"ok": 1}'
    (row,) = calls_of(prod)
    assert (row.id, row.connector, row.check_type, row.mode, row.route, row.outcome, row.cost_micros, row.evidence_key) == (
        out.call_id, "fake", CheckType.RFC_VALIDATION, ConnectorMode.LIVE, ConnectorRoute.PRIMARY, CallOutcome.PASS, 7, out.evidence_key)
    assert row.duration_ms >= 0 and row.error is None and sleep.calls == []

def test_transient_failures_are_retried_with_backoff():
    fake = FakeConnector(deps, calls=[httpx.ConnectError("down"), ok(status=503), ok()])
    assert (out.outcome, out.attempts) == (CallOutcome.PASS, 3); assert sleep.calls == [0.2, 0.5]

def test_three_timeouts_end_as_timeout():
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 3)
    assert (out.outcome, out.attempts, out.evidence_key) == (CallOutcome.TIMEOUT, 3, None)
    assert "ReadTimeout" in (out.error or ""); assert sleep.calls == [0.2, 0.5]

def test_a_4xx_is_an_error_without_retry_and_keeps_the_body_as_evidence():
    fake = FakeConnector(deps, calls=[RawResponse(401, b"bad token", {}, 1)])
    assert (out.outcome, out.attempts, out.error) == (CallOutcome.ERROR, 1, "HTTP 401")
    assert evidence_of(...) == b"bad token"; assert fake.circuit_state("mx-1") is CircuitState.CLOSED

def test_a_permanent_error_stops_at_once_without_counting_a_failure():
    fake = FakeConnector(deps, calls=[CredentialMissingError("no token")])
    assert (out.outcome, out.attempts, out.evidence_key) == (CallOutcome.ERROR, 1, None)
    assert "CredentialMissingError" in (out.error or ""); assert fake.circuit_state("mx-1") is CircuitState.CLOSED

def test_an_interpreter_crash_is_an_error_outcome_not_an_exception():
    def boom(raw: RawResponse) -> CheckResult: raise KeyError("Estado")
    fake = FakeConnector(deps, calls=[ok()], interpret=boom)
    out = fake.run(...)                      # must not raise
    assert out.outcome is CallOutcome.ERROR and out.evidence_key is not None and out.details == {}

def test_five_failures_in_a_minute_open_the_circuit_and_the_next_call_is_refused():
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 6)
    run twice (3 + 3 failed attempts)      # 6 >= 5 within 60 s
    assert fake.circuit_state("mx-1") is CircuitState.OPEN
    out = fake.run(...)                      # third run
    assert (out.outcome, out.attempts, out.error, out.evidence_key) == (CallOutcome.CIRCUIT_OPEN, 0, "circuit open", None)
    assert fake.call_count == 6
    assert [r.outcome for r in calls_of(prod)] == [CallOutcome.TIMEOUT, CallOutcome.TIMEOUT, CallOutcome.CIRCUIT_OPEN]

def test_the_circuit_half_opens_after_thirty_seconds_and_a_success_closes_it(tmp_path, engine, prod):
    clock = FakeMonotonic(); deps, store, sleep = make_deps(tmp_path, clock=clock)
    fake = FakeConnector(deps, calls=[httpx.ReadTimeout("slow")] * 6)
    with tenant_session(system_ctx(prod)) as s:
        for _ in range(2): _ = fake.run(s, request(), mode=ConnectorMode.LIVE)        # six failed attempts: OPEN
        clock.advance(29)
        assert fake.run(s, request(), mode=ConnectorMode.LIVE).outcome is CallOutcome.CIRCUIT_OPEN   # 29 s is not 30
        clock.advance(1)
        assert fake.circuit_state("mx-1") is CircuitState.HALF_OPEN
        fake.calls.extend([ok()])                                                      # the trial, on the same instance
        out = fake.run(s, request(), mode=ConnectorMode.LIVE)
    assert (out.outcome, out.attempts, fake.call_count) == (CallOutcome.PASS, 1, 7)
    assert fake.circuit_state("mx-1") is CircuitState.CLOSED
    assert [r.outcome for r in calls_of(prod)] == [CallOutcome.TIMEOUT, CallOutcome.TIMEOUT, CallOutcome.CIRCUIT_OPEN, CallOutcome.PASS]

def test_a_failed_trial_reopens_the_circuit_for_another_thirty_seconds(tmp_path, engine, prod):
    ... six timeouts and clock.advance(30) as above (HALF_OPEN), then on the same instance:
        fake.calls.extend([httpx.ReadTimeout("slow")] * 3)
        out = fake.run(s, request(), mode=ConnectorMode.LIVE)                           # the trial fails on its first attempt
        assert (out.outcome, out.attempts, fake.circuit_state("mx-1")) == (CallOutcome.TIMEOUT, 3, CircuitState.OPEN)   # the run is not cut short (Deviations 7)
        clock.advance(29); assert fake.run(s, request(), mode=ConnectorMode.LIVE).outcome is CallOutcome.CIRCUIT_OPEN
        clock.advance(1); assert fake.circuit_state("mx-1") is CircuitState.HALF_OPEN
    assert fake.call_count == 9

def test_a_breaker_is_kept_per_cell():
    # two tenants, cells "mx-1" and "mx-2" (UPDATE tenant SET cell = 'mx-2' as the owner for the second); opening mx-1 leaves mx-2 CLOSED

def test_a_sandbox_tenant_always_gets_the_mock(tmp_path, engine, sandbox):
    fake = FakeConnector(deps, calls=[ok()])
    with tenant_session(system_ctx(sandbox)) as s:
        out = fake.run(s, request(rfc="ABC010101AAA"), mode=ConnectorMode.LIVE)
    assert (out.outcome, out.mode, out.route, out.attempts, out.cost_micros) == (CallOutcome.PASS, ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0, 0)
    assert fake.call_count == 0
    assert evidence_of(...) == b'{"mock": true, "rfc": "ABC010101AAA"}'
    (row,) = calls_of(sandbox); assert (row.mode, row.route) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK)

def test_a_sandbox_mode_call_of_a_prod_tenant_is_a_mock_on_route_mock(tmp_path, engine, prod):
    # SANDBOX because the caller said so (run_check's default mode or an explicit mode), not the tenant's env: route is MOCK all the same
    fake = FakeConnector(deps, calls=[ok()])
    with tenant_session(system_ctx(prod)) as s:
        out = fake.run(s, request(rfc="ABC010101AAA"), mode=ConnectorMode.SANDBOX)
    assert (out.outcome, out.mode, out.route, out.attempts, out.cost_micros) == (CallOutcome.PASS, ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0, 0)
    assert fake.call_count == 0 and fake.seen_credentials == []
    (row,) = calls_of(prod); assert (row.mode, row.route, row.cost_micros) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0)

def test_credentials_reach_the_call_and_never_the_evidence_or_logs(tmp_path, engine, prod, admin_engine, caplog):
    caplog.set_level(logging.DEBUG); caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    token = f"tok-{uuid4().hex}"
    with staff_session(prod, uuid7()) as s:          # a staff context is enough: created_by has no FK
        _ = CredentialVault().set(s, "fake", ConnectorMode.LIVE, {"token": token})
    def echo(raw: RawResponse) -> CheckResult:        # an interpreter that copies a raw field verbatim, as a careless one would
        return CheckResult(outcome="PASS", details={"echo": json.loads(raw.body)["echo"]})
    fake = FakeConnector(deps, calls=[RawResponse(500, f'{{"echo": "{token}"}}'.encode(), {}, 1), ok(f'{{"echo": "{token}"}}'.encode())], interpret=echo)
    out = fake.run(...)
    assert fake.seen_credentials == [{"token": token}] * 2
    assert evidence_of(...) == b'{"echo": "[REDACTED]"}'
    assert out.details == {"echo": "[REDACTED]"}
    assert leaked(caplog.text, token.encode()) == []
    assert leaked(everything_stored(admin_engine), token.encode()) == []

def test_redact_replaces_every_secret_longest_first():
    assert redact("a=tok-12 b=tok-1 c=", ["tok-1", "tok-12", ""]) == "a=[REDACTED] b=[REDACTED] c="
```

(`safe_exception_summary` renders an ordinary exception as `"<Class>: <message>"`, which is what the `"ReadTimeout" in out.error` and `"CredentialMissingError" in out.error` assertions rely on.)

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_connectors.py -v --no-cov` → FAIL with `ImportError` (`Connector`, `ConnectorDeps` from `xolotl.platform.connectors`).
- [ ] **Step 3: Implement** `base.py` and the re-exports. Logging uses `extra=` only (ids as `str`), never a parameter value.
- [ ] **Step 4: Run to verify pass.** The test file, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): Connector base with timeout, retries, circuit breaker, evidence, metering and redaction`.

---

### Task B3b: The router, `run_check`, the default router and the services wiring

**Files:**
- Create: `src/xolotl/platform/connectors/router.py`, `src/xolotl/platform/connectors/defaults.py`
- Modify: `src/xolotl/platform/connectors/__init__.py` (+ `ConnectorRouter`, `run_check`, `DEFAULT_ROUTES`, `default_router`), `src/xolotl/platform/services.py` (+ `connectors: ConnectorRouter`), `src/xolotl/app.py` (`wire_platform` builds the store once and the router)
- Test: `tests/platform/test_connector_router.py`, `tests/platform/test_connector_wiring.py`

Precondition: B3a merged.

**Interfaces:**

Consumes: B3a's `Connector`, `ConnectorDeps`; `tests/platform/connectors_support.py` (`FakeConnector`, `make_deps`, `ok`, `request`, `calls_of`); `NoRouteError`, `ContextMissingError`; `PlatformServices`; `build_object_store`, `wire_platform`, `build_app` (`xolotl.app`); `Settings`, `get_settings`; `get_sessionmaker`; `state_of(app)` as `tests/platform/test_webhook_wiring.py` defines it (copy the helper).

Produces:

```python
# router.py
class ConnectorRouter:
    def __init__(self, connectors: Sequence[Connector], routes: Mapping[tuple[CheckType, str], Sequence[str]],
                 *, default_mode: ConnectorMode) -> None
        # ValueError: a route names an unknown connector, or one that does not serve the check type, or two connectors share a name
    @property
    def connectors(self) -> Sequence[Connector]          # registration order
    @property
    def default_mode(self) -> ConnectorMode
    def connector(self, name: str) -> Connector          # KeyError when unknown
    def route(self, check_type: CheckType, *, country: str, tenant_mode: ConnectorMode) -> list[Connector]
        # primary then fallbacks; SANDBOX: the primary only; NoRouteError when the table has no entry
    def positions(self, name: str) -> list[tuple[CheckType, str, ConnectorRoute]]
        # every (check_type, country) the connector appears in, PRIMARY or FALLBACK, table order

def run_check(session: Session, request: ConnectorRequest, *, router: ConnectorRouter,
              mode: ConnectorMode | None = None) -> VerificationOutcome

# defaults.py
DEFAULT_ROUTES: Mapping[tuple[CheckType, str], tuple[str, ...]] = {}      # B4a and B4b fill it
def default_router(settings: Settings, *, store: ObjectStore) -> ConnectorRouter
    # deps = ConnectorDeps(vault=CredentialVault(), store=store)
    # ConnectorRouter(connectors=(), routes=DEFAULT_ROUTES, default_mode=ConnectorMode(settings.connector_mode_default)); B4a and B4b add the connectors

# services.py
class PlatformServices: ... ; connectors: ConnectorRouter     # last field
```

`run_check`: `runtime = tenant_runtime(session, ctx.require_tenant_id())`; `effective = ConnectorMode.SANDBOX if runtime.env is TenantEnv.SANDBOX else (mode or router.default_mode)`; `connectors = router.route(request.check_type, country=request.country, tenant_mode=effective)`; for `index, connector`: `outcome = connector.run(session, request, mode=effective, route=ConnectorRoute.PRIMARY if index == 0 else ConnectorRoute.FALLBACK)` (a `SANDBOX` call comes back with `route = MOCK`: B3a's `run` decides the route of a mock); return it when `outcome.outcome in {PASS, FAIL, INCONCLUSIVE}`; after the loop return the last outcome.

`wire_platform` change: `store = build_object_store(settings)` once, `object_store=store`, `connectors=default_router(settings, store=store)`.

- [ ] **Step 1: Write the failing tests.**

`tests/platform/test_connector_router.py` (database; the `prod` and `sandbox` fixtures and the `deps, store, sleep = make_deps(tmp_path)` line as in `test_connectors.py`; calls inside `tenant_session(system_ctx(tenant_id))`):

```python
def test_run_check_uses_the_default_mode_for_a_prod_tenant_and_the_explicit_one_when_given(tmp_path, engine, prod):
    fake = FakeConnector(deps, calls=[ok()])
    router = ConnectorRouter([fake], {(CheckType.RFC_VALIDATION, "MX"): ("fake",)}, default_mode=ConnectorMode.SANDBOX)
    with tenant_session(system_ctx(prod)) as s:
        by_default = run_check(s, request(), router=router)
        explicit = run_check(s, request(), router=router, mode=ConnectorMode.LIVE)
    assert (by_default.mode, by_default.route, by_default.attempts) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0)   # a SANDBOX call is a mock on route MOCK
    assert (explicit.mode, explicit.route, explicit.attempts) == (ConnectorMode.LIVE, ConnectorRoute.PRIMARY, 1)
    assert fake.call_count == 1

def test_run_check_forces_sandbox_for_a_sandbox_tenant_even_when_live_is_asked():
    ... default_mode=LIVE, mode=LIVE, sandbox tenant → (out.mode, out.route) == (SANDBOX, MOCK), fake.call_count == 0

def test_the_router_falls_back_after_a_primary_error_and_records_both_routes():
    primary = FakeConnector(deps, name="primary", calls=[ok(status=500)] * 3); fallback = FakeConnector(deps, name="fallback", calls=[ok()])
    router = ConnectorRouter([primary, fallback], {(CheckType.RFC_VALIDATION, "MX"): ("primary", "fallback")}, default_mode=ConnectorMode.LIVE)
    out = run_check(s, request(), router=router)
    assert (out.connector, out.route, out.outcome) == ("fallback", ConnectorRoute.FALLBACK, CallOutcome.PASS)
    assert [(r.connector, r.route, r.outcome) for r in calls_of(prod)] == [
        ("primary", ConnectorRoute.PRIMARY, CallOutcome.ERROR), ("fallback", ConnectorRoute.FALLBACK, CallOutcome.PASS)]

def test_a_definitive_outcome_stops_the_route():
    primary FAIL (interpret returns FAIL) → fallback.call_count == 0; out.outcome is FAIL

def test_every_connector_failing_returns_the_last_outcome():
    both ERROR → out.connector == "fallback", out.outcome is ERROR

def test_sandbox_routes_only_the_primary():
    assert [c.name for c in router.route(CheckType.RFC_VALIDATION, country="MX", tenant_mode=ConnectorMode.SANDBOX)] == ["primary"]
    assert [...LIVE...] == ["primary", "fallback"]

def test_no_route_is_a_409_conflict_with_a_stable_code():
    with pytest.raises(NoRouteError) as refused: router.route(CheckType.CURP_VALIDATION, country="MX", tenant_mode=ConnectorMode.LIVE)
    assert (refused.value.status, refused.value.code) == (409, "CONNECTOR_NO_ROUTE")

@pytest.mark.parametrize("routes", [{(CheckType.RFC_VALIDATION, "MX"): ("nobody",)}, {(CheckType.UNIT_VALUE, "MX"): ("fake",)}])
def test_the_router_refuses_unknown_names_and_unsupported_check_types(routes):
    with pytest.raises(ValueError, match="fake|nobody"): ConnectorRouter([fake], routes, default_mode=ConnectorMode.LIVE)

def test_positions_list_every_route_of_a_connector():
    assert router.positions("fallback") == [(CheckType.RFC_VALIDATION, "MX", ConnectorRoute.FALLBACK)]

def test_run_check_needs_a_tenant_bound_session():
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError): run_check(s, request(), router=router)
```

`tests/platform/test_connector_wiring.py`:

```python
def test_the_app_is_wired_with_a_connector_router_that_follows_the_settings(engine, monkeypatch):
    monkeypatch.setenv("XOLOTL_CONNECTOR_MODE_DEFAULT", "LIVE"); get_settings.cache_clear()
    try:
        app = build_app()
    finally:
        get_settings.cache_clear()
    router = state_of(app).services.connectors
    assert isinstance(router, ConnectorRouter)
    assert router.default_mode is ConnectorMode.LIVE
    assert [c.name for c in router.connectors] == []        # B4a changes this line to ["sat_rfc"], B4b to ["banxico_sie", "sat_rfc"]

def test_the_worker_process_is_wired_with_the_same_router(engine):
    services = wire_platform(get_sessionmaker(), get_settings())
    assert isinstance(services.connectors, ConnectorRouter)
    assert services.connectors.default_mode is ConnectorMode.SANDBOX
    assert [c.name for c in services.connectors.connectors] == []   # B4a and B4b change it with the app test
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_connector_router.py tests/platform/test_connector_wiring.py -v --no-cov` → FAIL with `ImportError` (`ConnectorRouter`, `run_check`, `default_router`) and `AttributeError: connectors` on `PlatformServices`.
- [ ] **Step 3: Implement** `router.py`, `defaults.py`, the `services.py` field, the `wire_platform` change, the re-exports.
- [ ] **Step 4: Run to verify pass.** The two test files, `tests/platform/test_worker.py` (the wiring still builds), then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): ConnectorRouter, run_check, the default router and the services wiring`.

---

### Task B4a: The `sat_rfc` connector: the ported SAT functions, recorded answers, the last-character mock and the RFC route

**Files:**
- Create: `src/xolotl/platform/connectors/sat.py`, `tests/fixtures/connectors/sat_vigente.xml`, `sat_efos.xml`, `sat_cancelado.xml`, `sat_no_encontrado.xml`
- Modify: `pyproject.toml`, `uv.lock` (`uv add defusedxml` and `uv add --dev types-defusedxml`), `src/xolotl/platform/connectors/defaults.py` (`DEFAULT_ROUTES[(CheckType.RFC_VALIDATION, "MX")]`, `SatRfcConnector(deps)` in `default_router`), `src/xolotl/platform/connectors/__init__.py` (+ `SatRfcConnector`)
- Test: `tests/platform/test_connector_sat.py`, `tests/platform/test_connector_wiring.py` (the names and positions assertions)

Precondition: B3b merged. B4a needs nothing from plan 2a beyond A1's `xolotl.platform.text` (`RFC_SHAPE`), which B1's precondition already has on `main`, so it may merge before plan 2a's units tasks.

**Interfaces:**

Consumes: B3a's `Connector`, `ConnectorDeps`; B3b's `run_check`, `default_router`; B2's `PermanentCallError`, `InterpretationError`; `RFC_SHAPE` (`xolotl.platform.text`, plan 2a's A1: the platform's one RFC shape regex; `sat.py` defines none); `httpx`; `respx`; `defusedxml.ElementTree`; `get_settings`; `LocalFsObjectStore`; `tests/platform/connectors_support.py` (`make_deps`, `evidence_of`, `calls_of`); `make_tenant_with_keys`, `tenant_session`, `system_ctx` (`tests/helpers`).

Produces:

```python
# sat.py — ported from ~/code/xolo/src/xolo/providers/sat.py (Deviations 3)
SAT_RFC = "sat_rfc"
SAT_URL = "https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc"
SOAP_ACTION = "http://tempuri.org/IConsultaCFDIService/Consulta"
RESULT_NS = "{http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio}"
FIELDS = ("CodigoEstatus", "Estado", "EsCancelable", "EstatusCancelacion", "ValidacionEFOS")
from xolotl.platform.text import RFC_SHAPE                   # plan 2a's A1: ^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$; shape only: the check digit is plan 2c's validator
SEAL_TAIL = re.compile(r"^[A-Za-z0-9+/=]{8}$")
EFOS_LISTED = "100"
EFOS_CLEAR = "200"

@dataclass(frozen=True, slots=True)
class CfdiQuery:
    rfc: str                 # the subject, upper-cased; must equal issuer_rfc or receiver_rfc
    uuid: UUID
    issuer_rfc: str
    receiver_rfc: str
    total: Decimal
    seal_last8: str | None = None

def cfdi_query_from(params: Mapping[str, str]) -> CfdiQuery
    # keys rfc, cfdi_uuid, issuer_rfc, receiver_rfc, total, optional seal_last8; strips and upper-cases the RFCs;
    # PermanentCallError on a missing key, a bad UUID, an RFC outside RFC_SHAPE, a negative or non-decimal total,
    # a seal_last8 outside SEAL_TAIL, or an rfc that is neither issuer nor receiver; every message names the
    # offending key ("cfdi_uuid is missing", "rfc is not shaped like an RFC", "rfc is neither the issuer or receiver")
def format_total(total: Decimal) -> str              # up to 6 decimals, no trailing zeros, at least one: 1160.00 → "1160.0"
def expression(query: CfdiQuery) -> str              # "?re=<issuer>&rr=<receiver>&tt=<total>&id=<UUID upper>[&fe=<seal>]"; "&" in an RFC → "&amp;"
def envelope(query: CfdiQuery) -> str                # the SOAP 1.1 envelope with <tem:expresionImpresa><![CDATA[...]]>
def parse_response(payload: bytes) -> dict[str, str] # the FIELDS found under RESULT_NS; InterpretationError on non-XML or when Estado is absent
def interpret_fields(fields: Mapping[str, str]) -> CheckResult
    # details = {"estado", "codigo_estatus", "es_cancelable", "estatus_cancelacion", "validacion_efos", "reason"} (strings, "" when absent),
    # built as one literal inside the CheckResult(...) call (a dict[str, str] variable would not type as dict[str, JsonValue])
    # EFOS == "100" → FAIL EFOS_LISTED; Estado == "Cancelado" → FAIL CFDI_CANCELLED; "Vigente" → PASS VIGENTE;
    # "No Encontrado" → INCONCLUSIVE CFDI_NOT_FOUND; anything else → INCONCLUSIVE UNKNOWN_STATE
def mock_fields(rfc: str) -> dict[str, str]          # last character: "7" EFOS 100 + Vigente; "8" Cancelado; "9" No Encontrado; else Vigente + 200
def mock_envelope(fields: Mapping[str, str]) -> bytes  # the SOAP answer the live service sends, with these fields under RESULT_NS

class SatRfcConnector(Connector):
    evidence_content_type = "text/xml"
    def __init__(self, deps: ConnectorDeps, *, client: httpx.Client | None = None) -> None
        # super().__init__(deps, name=SAT_RFC, check_types=frozenset({CheckType.RFC_VALIDATION}))
        # client or httpx.Client(timeout=httpx.Timeout(self.timeout_s))
    def _call(self, request, credentials) -> RawResponse   # no credentials; POST SAT_URL, content=envelope(query).encode(),
        # headers {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{SOAP_ACTION}"'}; RawResponse(status, content, dict(headers), elapsed ms)
    def _interpret(self, raw) -> CheckResult               # interpret_fields(parse_response(raw.body))
    def _mock(self, request) -> RawResponse                # rfc from params (shape-checked, PermanentCallError otherwise); RawResponse(200, mock_envelope(mock_fields(rfc)), {"content-type": "text/xml; charset=utf-8"}, 0)

# defaults.py after B4a
DEFAULT_ROUTES: Mapping[tuple[CheckType, str], tuple[str, ...]] = {(CheckType.RFC_VALIDATION, "MX"): (SAT_RFC,)}
def default_router(settings: Settings, *, store: ObjectStore) -> ConnectorRouter
    # deps = ConnectorDeps(vault=CredentialVault(), store=store)
    # ConnectorRouter([SatRfcConnector(deps)], DEFAULT_ROUTES, default_mode=ConnectorMode(settings.connector_mode_default))   # B4b inserts banxico_sie first
```

The recorded fixtures (`tests/fixtures/connectors/`), the real service's shape; `sat_vigente.xml`:

```xml
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><ConsultaResponse xmlns="http://tempuri.org/"><ConsultaResult xmlns:a="http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio" xmlns:i="http://www.w3.org/2001/XMLSchema-instance"><a:CodigoEstatus>S - Comprobante obtenido satisfactoriamente.</a:CodigoEstatus><a:EsCancelable>Cancelable con aceptación</a:EsCancelable><a:Estado>Vigente</a:Estado><a:EstatusCancelacion/><a:ValidacionEFOS>200</a:ValidacionEFOS></ConsultaResult></ConsultaResponse></s:Body></s:Envelope>
```

The other three differ only in the result fields: `sat_efos.xml`: `EsCancelable = "Cancelable sin aceptación"`, `Estado = "Vigente"`, `EstatusCancelacion = "En proceso"`, `ValidacionEFOS = "100"`; `sat_cancelado.xml`: `EsCancelable = "No cancelable"`, `Estado = "Cancelado"`, `EstatusCancelacion = "Cancelado con aceptación"`, `ValidacionEFOS = "200"`; `sat_no_encontrado.xml`: `CodigoEstatus = "N - 602: Comprobante no encontrado."`, `Estado = "No Encontrado"`, no `EsCancelable`, `EstatusCancelacion` or `ValidacionEFOS` elements.

- [ ] **Step 1: Write the failing tests.**

`tests/platform/test_connector_sat.py`. Fixtures: `prod` and `sandbox` as `test_connectors.py` defines them (`make_tenant_with_keys("prod", env=TenantEnv.PROD)`, `make_tenant_with_keys("sbx", env=TenantEnv.SANDBOX)`); `deps` (`deps, store, sleep = make_deps(tmp_path)`; `tmp_path / "objects"` is the root `evidence_of` reads); `connector` (`SatRfcConnector(deps)`); `sat` (the `respx` router below). Module constants `FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors"` and `QUERY = {"rfc": "AAA010101AAA", "cfdi_uuid": "6128396f-c09b-4ec6-8699-43c5f7e3b230", "issuer_rfc": "AAA010101AAA", "receiver_rfc": "XAXX010101000", "total": "1160.00"}`; helper `run_rfc(connector, tenant_id, params, *, mode=ConnectorMode.LIVE)` runs `connector.run(s, ConnectorRequest(CheckType.RFC_VALIDATION, None, params), mode=mode)` inside `tenant_session(system_ctx(tenant_id))`:

```python
@pytest.mark.parametrize(("total", "text"), [("1160.00", "1160.0"), ("100", "100.0"), ("0.123456", "0.123456"), ("12.5", "12.5")])
def test_format_total_follows_the_cfdi_qr_rule(total, text): assert format_total(Decimal(total)) == text

def test_expression_and_envelope_match_the_qr_payload():
    query = cfdi_query_from(QUERY)
    assert expression(query) == "?re=AAA010101AAA&rr=XAXX010101000&tt=1160.0&id=6128396F-C09B-4EC6-8699-43C5F7E3B230"
    assert expression(cfdi_query_from({**QUERY, "seal_last8": "AbCd1234"})).endswith("&fe=AbCd1234")
    assert "<![CDATA[?re=AAA010101AAA" in envelope(query) and 'xmlns:tem="http://tempuri.org/"' in envelope(query)

def test_an_ampersand_in_an_rfc_is_escaped_in_the_expression_but_matched_as_the_subject():
    query = cfdi_query_from({**QUERY, "rfc": "A&A010101AAA", "issuer_rfc": "A&A010101AAA"})
    assert expression(query).startswith("?re=A&amp;A010101AAA&rr=")

@pytest.mark.parametrize(("params", "match"), [
    ({k: v for k, v in QUERY.items() if k != "cfdi_uuid"}, "cfdi_uuid"), ({**QUERY, "cfdi_uuid": "nope"}, "cfdi_uuid"),
    ({**QUERY, "rfc": "ABC"}, "rfc"), ({**QUERY, "total": "-1"}, "total"), ({**QUERY, "total": "ten"}, "total"),
    ({**QUERY, "seal_last8": "x"}, "seal_last8"), ({**QUERY, "rfc": "ZZZ010101ZZZ"}, "issuer or receiver")])
def test_a_bad_query_is_a_permanent_error(params, match):
    with pytest.raises(PermanentCallError, match=match): _ = cfdi_query_from(params)

@pytest.mark.parametrize(("fixture", "outcome", "reason"), [
    ("sat_vigente.xml", "PASS", "VIGENTE"), ("sat_efos.xml", "FAIL", "EFOS_LISTED"),
    ("sat_cancelado.xml", "FAIL", "CFDI_CANCELLED"), ("sat_no_encontrado.xml", "INCONCLUSIVE", "CFDI_NOT_FOUND")])
def test_recorded_answers_interpret_as_documented(fixture, outcome, reason):
    result = interpret_fields(parse_response((FIXTURES / fixture).read_bytes()))
    assert (result.outcome, result.details["reason"]) == (outcome, reason)
    assert result.details["estado"] in {"Vigente", "Cancelado", "No Encontrado"}

@pytest.mark.parametrize("payload", [b"<html>503</html>", b"\x00\x01", b"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'><s:Body/></s:Envelope>"])
def test_unreadable_answers_are_interpretation_errors(payload):
    with pytest.raises(InterpretationError): _ = parse_response(payload)

@pytest.mark.parametrize(("rfc", "outcome", "reason"), [
    ("XAXX010101000", "PASS", "VIGENTE"), ("ABC010101AAA", "PASS", "VIGENTE"), ("ABC010101AA7", "FAIL", "EFOS_LISTED"),
    ("ABC010101AA8", "FAIL", "CFDI_CANCELLED"), ("ABC010101AA9", "INCONCLUSIVE", "CFDI_NOT_FOUND")])
def test_the_mock_is_keyed_by_the_last_character(rfc, outcome, reason):
    result = interpret_fields(parse_response(mock_envelope(mock_fields(rfc))))
    assert (result.outcome, result.details["reason"]) == (outcome, reason)

@pytest.fixture
def sat() -> Generator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router: yield router

def test_a_live_call_posts_the_envelope_and_passes(tmp_path, engine, prod, deps, connector, sat):
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=(FIXTURES / "sat_vigente.xml").read_bytes(), headers={"content-type": "text/xml; charset=utf-8"}))
    out = run_rfc(connector, prod, QUERY)
    assert (out.outcome, out.attempts, out.details["reason"]) == (CallOutcome.PASS, 1, "VIGENTE")
    sent = route.calls.last.request
    assert sent.content == envelope(cfdi_query_from(QUERY)).encode()
    assert (sent.headers["content-type"], sent.headers["soapaction"]) == ("text/xml; charset=utf-8", f'"{SOAP_ACTION}"')
    assert evidence_of(tmp_path / "objects", prod, out) == (FIXTURES / "sat_vigente.xml").read_bytes()

def test_a_500_is_retried_three_times_then_an_error(sat):
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(500, content=b"<html>down</html>"))
    assert (out.outcome, out.attempts, out.error) == (CallOutcome.ERROR, 3, "HTTP 500"); assert route.call_count == 3

def test_a_timeout_is_a_timeout_outcome(sat):
    sat.post(SAT_URL).mock(side_effect=httpx.ReadTimeout("slow")); assert out.outcome is CallOutcome.TIMEOUT

@pytest.mark.parametrize("payload", [b"<html>maintenance</html>", b"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'><s:Body/></s:Envelope>"])
def test_a_200_with_an_unreadable_body_is_an_error_outcome(tmp_path, engine, prod, deps, connector, sat, payload):
    sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=payload))
    out = run_rfc(connector, prod, QUERY)                     # the InterpretationError stays inside run
    assert (out.outcome, out.attempts) == (CallOutcome.ERROR, 1)
    assert "InterpretationError" in (out.error or "")
    assert evidence_of(tmp_path / "objects", prod, out) == payload   # the unreadable body is still the evidence

def test_a_sandbox_tenant_never_reaches_the_sat_service(tmp_path, engine, sandbox, deps, connector, sat):
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=b""))
    out = run_rfc(connector, sandbox, {"rfc": "ABC010101AA8"}, mode=ConnectorMode.LIVE)
    assert (out.outcome, out.mode, out.route, out.details["reason"]) == (CallOutcome.FAIL, ConnectorMode.SANDBOX, ConnectorRoute.MOCK, "CFDI_CANCELLED")
    assert route.call_count == 0
    (row,) = calls_of(sandbox)
    assert (row.mode, row.route, row.cost_micros, row.evidence_key) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0, f"connectors/sat_rfc/{out.call_id}")

def test_run_check_through_the_default_router_gives_a_sandbox_tenant_the_mock_and_a_metering_row(tmp_path, engine, sandbox, sat):
    # the exit criterion of spec §13, through the router the composition root builds (its store pointed at tmp_path)
    route = sat.post(SAT_URL).mock(return_value=httpx.Response(200, content=b""))
    router = default_router(get_settings(), store=LocalFsObjectStore(tmp_path / "objects"))
    with tenant_session(system_ctx(sandbox)) as s:
        out = run_check(s, ConnectorRequest(CheckType.RFC_VALIDATION, None, {"rfc": "ABC010101AA8"}), router=router, mode=ConnectorMode.LIVE)
    assert (out.connector, out.outcome, out.mode, out.route, out.details["reason"]) == ("sat_rfc", CallOutcome.FAIL, ConnectorMode.SANDBOX, ConnectorRoute.MOCK, "CFDI_CANCELLED")
    (row,) = calls_of(sandbox)
    assert (row.id, row.mode, row.route, row.outcome, row.cost_micros, row.evidence_key) == (
        out.call_id, ConnectorMode.SANDBOX, ConnectorRoute.MOCK, CallOutcome.FAIL, 0, f"connectors/sat_rfc/{out.call_id}")
    assert evidence_of(tmp_path / "objects", sandbox, out) == mock_envelope(mock_fields("ABC010101AA8"))
    assert route.call_count == 0

def test_a_malformed_rfc_is_an_error_in_sandbox_too():
    ... {"rfc": "nope"} under sandbox → out.outcome is CallOutcome.ERROR, "PermanentCallError" in out.error
```

`tests/platform/test_connector_wiring.py`: change both names assertions to `== ["sat_rfc"]` and add, in the app test, `assert router.positions("sat_rfc") == [(CheckType.RFC_VALIDATION, "MX", ConnectorRoute.PRIMARY)]`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_connector_sat.py tests/platform/test_connector_wiring.py -v --no-cov` → FAIL with `ImportError` (`xolotl.platform.connectors.sat`) and the names assertion (`[] != ["sat_rfc"]`).
- [ ] **Step 3: Implement.** `uv add defusedxml && uv add --dev types-defusedxml`; `sat.py`; `DEFAULT_ROUTES` and `default_router` as above. In `parse_response` use `defusedxml.ElementTree.fromstring` and catch `defusedxml.ElementTree.ParseError` (raise `InterpretationError` from it). The `httpx.Client` of `SatRfcConnector` is created once per connector (per process) and never closed explicitly (it lives as long as the process; a test passes its own client when it needs to). `elapsed_ms` from `response.elapsed` (`int(elapsed.total_seconds() * 1000)`).
- [ ] **Step 4: Run to verify pass.** The two test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): sat_rfc connector (SAT CFDI status, ported from Xolo) with recorded answers, the last-character mock and the RFC route`.

---

### Task B4b: The `banxico_sie` connector over plan 2a's `BanxicoSieSource` and the unit-value route

**Files:**
- Create: `src/xolotl/platform/connectors/banxico.py`
- Modify: `src/xolotl/platform/connectors/defaults.py` (`rows_to_pairs`, `sie_fetch_factory`, `seed_lookup_from`, `DEFAULT_ROUTES[(CheckType.UNIT_VALUE, "MX")]`, `BanxicoSieConnector` first in `default_router`), `src/xolotl/platform/connectors/__init__.py` (+ `BanxicoSieConnector`)
- Test: `tests/platform/test_connector_banxico.py`, `tests/platform/test_connector_wiring.py` (the names line, one positions assertion)

Precondition: B4a merged, and plan 2a's A5 on `main` (`xolotl.platform.units` exports `BanxicoSieSource`, `SeedFileSource`, `UnitValue`, `UnitSourceError` and `UnitSourceAuthError`).

**Interfaces:**

Consumes: B3a's `Connector`, `ConnectorDeps`; B3b's `run_check`, `default_router`; B2's `PermanentCallError`, `TransientCallError`, `CredentialMissingError`; `canonical_json` (`xolotl.platform.audit`); `XolotlError` (`xolotl.platform.errors`, the kernel's base error); `Settings`, `get_settings` (`Settings.banxico_token`, `Settings.env`, `Settings.packs_dir`); `SecretStr` (`pydantic`); `Confidence` (`xolotl.platform.enums`, plan 2a's A2); `LocalFsObjectStore`; `httpx` (tests); `tests/platform/connectors_support.py` (`make_deps`, `evidence_of`, `calls_of`); `make_tenant_with_keys`, `tenant_session`, `staff_session`, `system_ctx` (`tests/helpers`). From plan 2a's `xolotl.platform.units`: `BanxicoSieSource` (A5: `__init__(self, token: SecretStr | None, *, seed_dir: Path, env: str, client: httpx.Client | None = None)`, `fetch(unit_code, start, end) -> list[UnitValue]`, the live SIE client; `00-common.md`: this connector does not reimplement the SIE calls), `SeedFileSource` (A4: `__init__(self, seed_dir: Path, *, allow_unverified: bool = False)`, the same `fetch`, over the committed seed files), `UnitValue` (A4's frozen dataclass: `unit_code: str; valid_from: date; valid_to: date | None; value: Decimal; quote_ccy: str; source: str; confidence: Confidence`), `UnitSourceError` (503 `UNIT_SOURCE_UNAVAILABLE`: unreachable, an HTTP status, a malformed body) and `UnitSourceAuthError(UnitSourceError)` (A5: Banxico rejected the token, 401/403); both subclass `XolotlError`. `UnitValueMissingError` is raised by `UnitService.lookup`, never by `fetch`, so this connector never sees it.

The boundary with plan 2a is a pair shape: `banxico.py` and its tests know only `(date, Decimal)` pairs, a `(unit_code, start, end)` fetch callable and a `(unit_code, on)` seed lookup; `defaults.py` holds the adapters that read plan 2a's names, pinned here from plan 2a's A4 and A5 (`UnitValue(unit_code, valid_from, valid_to, value, quote_ccy, source, confidence)`, `SeedFileSource(seed_dir, *, allow_unverified=False)` and `BanxicoSieSource(token: SecretStr | None, *, seed_dir: Path, env: str, client: httpx.Client | None = None)`; `make types` confirms them against the merged `src/xolotl/platform/units/`: an attribute or keyword that does not exist fails basedpyright).

Produces:

```python
# banxico.py
BANXICO_SIE = "banxico_sie"
UNIT_CODES = frozenset({"UDI", "USD_MXN_FIX"})
LOOKBACK_DAYS = 10
MOCK_VALUES: Mapping[str, str] = {"UDI": "8.500000", "USD_MXN_FIX": "18.500000"}
type ObservationPairs = Sequence[tuple[date, Decimal]]
type ObservationFetch = Callable[[str, date, date], ObservationPairs]     # (unit_code, start, end) → (date, value) pairs, any order
type SeedLookup = Callable[[str, date], Decimal | None]                 # (unit_code, on) → the committed seed's value for that day, None when the seed has no row

@dataclass(frozen=True, slots=True)
class UnitQuery:
    unit_code: str
    on: date

def unit_query_from(params: Mapping[str, str]) -> UnitQuery
    # PermanentCallError naming the key: "unit_code" absent or outside UNIT_CODES, "on" absent or not an ISO date
def observations_body(query: UnitQuery, rows: ObservationPairs) -> bytes
    # canonical_json({"unit_code", "on": iso, "observations": [{"date": iso, "value": str(value)}, ...]}) sorted by date
def interpret_body(body: bytes) -> CheckResult
    # no observations → INCONCLUSIVE {"reason": "NO_OBSERVATION", "unit_code", "on"}; else PASS with
    # details {"unit_code", "on", "value", "value_date", "source": "banxico_sie"} from the latest observation on or before `on`
    # (details built as one literal in the CheckResult(...) call)

class BanxicoSieConnector(Connector):
    evidence_content_type = "application/json"
    def __init__(self, deps: ConnectorDeps, *, fetch_factory: Callable[[str], ObservationFetch],
                 seed_lookup: SeedLookup, platform_token: str | None, prod: bool) -> None
        # super().__init__(deps, name=BANXICO_SIE, check_types=frozenset({CheckType.UNIT_VALUE}))
    def _call(self, request, credentials) -> RawResponse
        # token = credentials.get("token") or platform_token; none: prod → CredentialMissingError("banxico_sie has no SIE token"),
        # otherwise return self._mock(request) (demo mode: the mock body travels the live path, so the route stays what the router passed);
        # query = unit_query_from(request.params) (before the factory is called); fetch = fetch_factory(token);
        # rows = fetch(query.unit_code, query.on - timedelta(days=LOOKBACK_DAYS), query.on); a UnitSourceAuthError → PermanentCallError(str(exc)) from it (no retry, Deviations 7);
        # any other XolotlError → TransientCallError(str(exc)) from it
        # (httpx errors propagate to the base, which retries them); RawResponse(200, observations_body(query, rows), {"content-type": "application/json"}, elapsed ms)
    def _interpret(self, raw) -> CheckResult               # interpret_body(raw.body)
    def _mock(self, request) -> RawResponse                # unit_query_from(params); one observation dated on: seed_lookup(unit_code, on), or Decimal(MOCK_VALUES[unit_code]) when that is None (Deviations 4)

# defaults.py after B4b
def rows_to_pairs(rows: Iterable[UnitValue]) -> list[tuple[date, Decimal]]
    # [(row.valid_from, row.value) for row in rows]: plan 2a's A4 field names, read here and in seed_lookup_from only
def sie_fetch_factory(settings: Settings) -> Callable[[str], ObservationFetch]
    # returns lambda token: (lambda unit_code, start, end: rows_to_pairs(BanxicoSieSource(SecretStr(token), seed_dir=settings.packs_dir / "_units", env=settings.env).fetch(unit_code, start, end)))
    # (plan 2a's A5 constructor: the token positional as a SecretStr, seed_dir and env keyword-only; inner defs instead of lambdas where ruff prefers them)
def seed_lookup_from(source: SeedFileSource) -> SeedLookup
    # def lookup(unit_code: str, on: date) -> Decimal | None:
    #     rows = source.fetch(unit_code, on, on)             # the seed row whose valid_from is `on`, if any
    #     return rows[0].value if rows else None
    # a missing or malformed seed file is plan 2a's ValidationFailedError, which run reports as an ERROR outcome (the files are committed: it means a wrong XOLOTL_PACKS_DIR)
DEFAULT_ROUTES: Mapping[tuple[CheckType, str], tuple[str, ...]] = {
    (CheckType.UNIT_VALUE, "MX"): (BANXICO_SIE,),
    (CheckType.RFC_VALIDATION, "MX"): (SAT_RFC,),
}
def default_router(settings: Settings, *, store: ObjectStore) -> ConnectorRouter
    # token = settings.banxico_token.get_secret_value() if settings.banxico_token is not None else None
    # seeds = seed_lookup_from(SeedFileSource(settings.packs_dir / "_units", allow_unverified=True))   # the files BanxicoSieSource's demo mode reads
    # ConnectorRouter([BanxicoSieConnector(deps, fetch_factory=sie_fetch_factory(settings), seed_lookup=seeds, platform_token=token, prod=settings.env == "prod"),
    #                  SatRfcConnector(deps)], DEFAULT_ROUTES, default_mode=ConnectorMode(settings.connector_mode_default))
```

- [ ] **Step 1: Write the failing tests.**

`tests/platform/test_connector_banxico.py`. Fixtures `prod`, `sandbox` and `deps` as in `test_connector_sat.py`; `ON = "2026-10-19"`; helper `run_unit(connector, tenant_id, *, params=None, mode=ConnectorMode.LIVE)` runs `connector.run(s, ConnectorRequest(CheckType.UNIT_VALUE, None, params or {"unit_code": "UDI", "on": ON}), mode=mode)` inside `tenant_session(system_ctx(tenant_id))`; three test doubles, shaped like the production boundary:

```python
class FakeFetch:                 # the fetch callable the connector consumes
    def __init__(self, rows: ObservationPairs = (), raises: Exception | None = None) -> None   # calls: list[tuple[str, date, date]] = []
    def __call__(self, unit_code: str, start: date, end: date) -> ObservationPairs             # records (unit_code, start, end); raises `raises` when set; else rows

class RecordingFactory:          # the fetch_factory; tokens: list[str] = []
    def __init__(self, fetch: FakeFetch) -> None
    def __call__(self, token: str) -> FakeFetch                                                 # appends token, returns fetch

SEED: Mapping[tuple[str, date], Decimal] = {("UDI", date(2026, 10, 19)): Decimal("8.700000")}   # the test's seed: a UDI row on ON, no FIX row
def seed_lookup(unit_code: str, on: date) -> Decimal | None:                                   # the seed_lookup the connector consumes
    return SEED.get((unit_code, on))
```

```python
def test_a_vault_token_wins_over_the_platform_token_and_the_latest_prior_value_is_taken(tmp_path, engine, prod, deps):
    fetch = FakeFetch(rows=[(date(2026, 10, 16), Decimal("8.650000")), (date(2026, 10, 15), Decimal("8.600000"))]); factory = RecordingFactory(fetch)
    connector = BanxicoSieConnector(deps, fetch_factory=factory, seed_lookup=seed_lookup, platform_token="platform-token", prod=False)
    with staff_session(prod, uuid7()) as s: _ = CredentialVault().set(s, "banxico_sie", ConnectorMode.LIVE, {"token": "tenant-token"})
    out = run_unit(connector, prod)
    assert factory.tokens == ["tenant-token"]
    assert fetch.calls == [("UDI", date(2026, 10, 9), date(2026, 10, 19))]
    assert (out.outcome, out.route, out.attempts) == (CallOutcome.PASS, ConnectorRoute.PRIMARY, 1)
    assert out.details == {"unit_code": "UDI", "on": ON, "value": "8.650000", "value_date": "2026-10-16", "source": "banxico_sie"}
    assert json.loads(evidence_of(tmp_path / "objects", prod, out)) == {"observations": [{"date": "2026-10-15", "value": "8.600000"}, {"date": "2026-10-16", "value": "8.650000"}], "on": ON, "unit_code": "UDI"}

def test_the_platform_token_is_used_when_the_vault_is_empty():
    ... assert factory.tokens == ["platform-token"]

@pytest.mark.parametrize(("unit_code", "value"), [("UDI", "8.700000"), ("USD_MXN_FIX", "18.500000")])   # UDI from the seed, FIX from MOCK_VALUES (no seed row)
def test_no_token_outside_prod_answers_from_the_demo_values(tmp_path, engine, prod, deps, unit_code, value):
    connector = BanxicoSieConnector(deps, fetch_factory=factory, seed_lookup=seed_lookup, platform_token=None, prod=False)
    out = run_unit(connector, prod, params={"unit_code": unit_code, "on": ON})   # a LIVE call: the demo body travels the live path, so the route stays PRIMARY
    assert (out.outcome, out.route, out.attempts, out.details["value"], out.details["value_date"]) == (CallOutcome.PASS, ConnectorRoute.PRIMARY, 1, value, ON)
    assert factory.tokens == []

def test_no_token_in_prod_is_refused():
    connector = BanxicoSieConnector(deps, fetch_factory=factory, seed_lookup=seed_lookup, platform_token=None, prod=True)
    assert out.outcome is CallOutcome.ERROR and "CredentialMissingError" in (out.error or "") and factory.tokens == []

def test_no_observation_is_inconclusive():
    FakeFetch(rows=[]) → (out.outcome, out.details["reason"]) == (CallOutcome.INCONCLUSIVE, "NO_OBSERVATION")

@pytest.mark.parametrize(("params", "key"), [({"unit_code": "UMA", "on": ON}, "unit_code"), ({"unit_code": "UDI", "on": "yesterday"}, "on"), ({"unit_code": "UDI"}, "on")])
def test_bad_params_are_errors(tmp_path, engine, prod, deps, params, key):
    # prod tenant, LIVE, platform_token="platform-token": unit_query_from runs inside _call, so the PermanentCallError counts one
    # attempt and the factory is never called (a SANDBOX call would report attempts == 0, since _mock is not an attempt)
    connector = BanxicoSieConnector(deps, fetch_factory=factory, seed_lookup=seed_lookup, platform_token="platform-token", prod=False)
    out = run_unit(connector, prod, params=params)
    assert (out.outcome, out.attempts) == (CallOutcome.ERROR, 1)
    assert "PermanentCallError" in (out.error or "") and key in (out.error or "")
    assert factory.tokens == []

def test_a_fetch_transport_error_is_retried_and_a_platform_error_is_wrapped_as_transient(tmp_path, engine, prod, deps):
    for raised in (httpx.ConnectError("down"), UnitSourceError("Banxico SIE is unreachable, try again shortly")):   # plan 2a's A5 message for an unreachable SIE
        fetch = FakeFetch(raises=raised); factory = RecordingFactory(fetch); deps, store, sleep = make_deps(tmp_path / raised.__class__.__name__)
        connector = BanxicoSieConnector(deps, fetch_factory=factory, seed_lookup=seed_lookup, platform_token="platform-token", prod=False)
        out = run_unit(connector, prod)
        assert (out.outcome, out.attempts, sleep.calls) == (CallOutcome.ERROR, 3, [0.2, 0.5])
    assert "TransientCallError" in (out.error or "")                                # the UnitSourceError leg

def test_a_rejected_token_is_a_permanent_error_without_retry(tmp_path, engine, prod):
    fetch = FakeFetch(raises=UnitSourceAuthError("Banxico rejected the API token (check XOLOTL_BANXICO_TOKEN)")); factory = RecordingFactory(fetch)
    deps, store, sleep = make_deps(tmp_path)
    connector = BanxicoSieConnector(deps, fetch_factory=factory, seed_lookup=seed_lookup, platform_token="platform-token", prod=False)
    out = run_unit(connector, prod)
    assert (out.outcome, out.attempts, sleep.calls) == (CallOutcome.ERROR, 1, [])   # Deviations 7: no retry, no breaker failure
    assert "PermanentCallError" in (out.error or "") and "token" in (out.error or "")
    assert connector.circuit_state("mx-1") is CircuitState.CLOSED

def test_rows_to_pairs_reads_plan_2a_rows():
    row = UnitValue(unit_code="UDI", valid_from=date(2026, 10, 16), valid_to=date(2026, 10, 16), value=Decimal("8.650000"), quote_ccy="MXN", source="BANXICO_SIE:SP68257", confidence=Confidence.VERIFIED)
    assert rows_to_pairs([row]) == [(date(2026, 10, 16), Decimal("8.650000"))]

def test_seed_lookup_from_reads_plan_2a_seed_files():
    lookup = seed_lookup_from(SeedFileSource(REPO / "policy-packs" / "_units", allow_unverified=True))   # REPO = Path(__file__).resolve().parents[2]; no database
    seeded = lookup("UDI", date(2026, 1, 20))                        # a day inside the committed seed (plan 2a's A4: 2025-12-01..2026-01-31)
    assert seeded is not None and seeded > 0
    assert lookup("USD_MXN_FIX", date(2026, 1, 20)) is not None
    assert lookup("UDI", date(2026, 10, 19)) is None                  # beyond the seed: the connector falls back to MOCK_VALUES

@pytest.mark.parametrize(("unit_code", "value"), [("UDI", "8.700000"), ("USD_MXN_FIX", "18.500000")])   # UDI from the seed, FIX from MOCK_VALUES (no seed row)
def test_a_sandbox_tenant_gets_the_mock_values(tmp_path, engine, sandbox, deps, unit_code, value):
    out = run_unit(connector, sandbox, params={"unit_code": unit_code, "on": ON})   # connector built with seed_lookup=seed_lookup as above
    assert (out.mode, out.route, out.attempts, out.cost_micros, out.details["value"], out.details["value_date"]) == (ConnectorMode.SANDBOX, ConnectorRoute.MOCK, 0, 0, value, ON)
    assert factory.tokens == []

def test_run_check_through_the_default_router_gives_a_sandbox_tenant_the_seed_value(tmp_path, engine, sandbox):
    # the sandbox UNIT_VALUE check and GET /v1/units/UDI?on=2026-01-20 read the same seed file (Deviations 4)
    settings = get_settings()
    expected = SeedFileSource(settings.packs_dir / "_units", allow_unverified=True).fetch("UDI", date(2026, 1, 20), date(2026, 1, 20))[0].value
    router = default_router(settings, store=LocalFsObjectStore(tmp_path / "objects"))
    with tenant_session(system_ctx(sandbox)) as s:
        out = run_check(s, ConnectorRequest(CheckType.UNIT_VALUE, None, {"unit_code": "UDI", "on": "2026-01-20"}), router=router)
    assert (out.connector, out.outcome, out.mode, out.route) == ("banxico_sie", CallOutcome.PASS, ConnectorMode.SANDBOX, ConnectorRoute.MOCK)
    assert (out.details["value"], out.details["value_date"]) == (str(expected), "2026-01-20")
```

`tests/platform/test_connector_wiring.py`: change both names assertions to `== ["banxico_sie", "sat_rfc"]` and add `assert router.positions("banxico_sie") == [(CheckType.UNIT_VALUE, "MX", ConnectorRoute.PRIMARY)]`.

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_connector_banxico.py tests/platform/test_connector_wiring.py -v --no-cov` → FAIL with `ImportError` (`xolotl.platform.connectors.banxico`, `rows_to_pairs`, `seed_lookup_from`) and the names assertion.
- [ ] **Step 3: Implement** `banxico.py`; `rows_to_pairs`, `sie_fetch_factory`, `seed_lookup_from`, `DEFAULT_ROUTES` and `default_router` as above, with plan 2a's names as pinned in the Interfaces block (`make types` confirms them against the merged `src/xolotl/platform/units/`); the re-export.
- [ ] **Step 4: Run to verify pass.** The two test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): banxico_sie connector over the SIE unit-value source with token precedence, the seed-backed demo mode and mock, and the unit-value route`.

---

### Task B5: The connector routes: list, usage and credentials

**Files:**
- Create: `src/xolotl/modules/tenants/routes_connectors.py`, `src/xolotl/platform/connectors/metering.py`
- Modify: `src/xolotl/modules/tenants/schemas.py` (+ request/response models), `src/xolotl/app.py` (`routes_connectors` import and `routes_connectors.router` after `routes_webhooks.router`), `src/xolotl/platform/connectors/__init__.py` (+ `UsageRow`, `usage`), `README.md` (a short "Connectors" section after "Data protection")
- Test: `tests/modules/tenants/test_connector_routes.py`

**Interfaces:**

Consumes: B2's `CredentialVault`, `tenant_runtime`; B3a's `Connector.circuit_state`; B3b's `ConnectorRouter`, `positions`; `app_services` (`xolotl.platform.api.app`); `require_access`, `SessionDep` (`xolotl.platform.auth.principal`); `v1_router`; `ConnectorCall`, `ConnectorCredential`; `ValidationFailedError`, `NotFoundError`; the `tenant` and `admin_headers` fixtures; `tests/platform/connectors_support.py` (B3a: `FakeConnector`, `make_deps`) for the `fake_router` fixture that every list and credential test uses; `state_of(app)` and `dataclasses.replace` to install a fake router. Nothing from B4a or B4b: no test here needs a real connector.

Produces:

```python
# metering.py
MAX_USAGE_DAYS = 366

@dataclass(frozen=True, slots=True)
class UsageRow:
    day: date
    connector: str
    calls: int
    cost_micros: int
    outcomes: dict[str, int]          # CallOutcome value → count

def usage(session: Session, *, start: date, end: date) -> list[UsageRow]
    # ValidationFailedError("from must be on or before to") / ("the range may span at most 366 days");
    # WHERE created_at >= start 00:00Z AND created_at < (end + 1 day) 00:00Z (index-friendly),
    # GROUP BY CAST(timezone('UTC', created_at) AS date), connector, outcome; folded per (day, connector); ordered by day, connector

# schemas.py (append)
class ConnectorRouteResponse(BaseModel): check_type: CheckType; country: str; position: ConnectorRoute
class ConnectorResponse(BaseModel): name: str; check_types: list[CheckType]; mode: ConnectorMode; routes: list[ConnectorRouteResponse]; circuit_state: CircuitState; credential_modes: list[ConnectorMode]
class ConnectorListResponse(BaseModel): items: list[ConnectorResponse]
class ConnectorUsageRow(BaseModel): day: date; connector: str; calls: int; cost_micros: int; outcomes: dict[str, int]
class ConnectorUsageResponse(BaseModel): start: date; end: date; items: list[ConnectorUsageRow]
class ConnectorCredentialRequest(BaseModel): fields: dict[str, str] = Field(min_length=1, repr=False)
class ConnectorCredentialResponse(BaseModel): connector: str; mode: ConnectorMode; field_names: list[str]; created_at: datetime; rotated_at: datetime | None

# routes_connectors.py
router = v1_router(prefix="/connectors", tags=["connectors"])
AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]
AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
def connector_router(request: Request) -> ConnectorRouter      # app_services(request).connectors
RouterDep = Annotated[ConnectorRouter, Depends(connector_router)]

@router.get("")
def list_connectors(actor: AdminReader, session: SessionDep, connectors: RouterDep) -> ConnectorListResponse
    # runtime = tenant_runtime(session, tenant_id); mode = SANDBOX if runtime.env is SANDBOX else connectors.default_mode;
    # credential_modes from select(ConnectorCredential.connector, ConnectorCredential.mode) (RLS: this tenant);
    # one ConnectorResponse per connectors.connectors, check_types sorted, routes = positions(name), circuit_state = circuit_state(runtime.cell)
@router.get("/usage")
def connector_usage(_admin: AdminReader, session: SessionDep, from_: Annotated[date, Query(alias="from")], to: Annotated[date, Query()]) -> ConnectorUsageResponse
@router.put("/{name}/credentials/{mode}")
def set_connector_credentials(name: str, mode: ConnectorMode, body: ConnectorCredentialRequest, _admin: AdminWriter, session: SessionDep, connectors: RouterDep) -> ConnectorCredentialResponse
    # unknown name → NotFoundError("unknown connector"); both modes are accepted (a SANDBOX row is stored and listed, read by no
    # connector here: Deviations 2); row = CredentialVault().set(session, name, mode, body.fields);
    # ConnectorCredentialResponse(connector=row.connector, mode=row.mode, field_names=sorted(body.fields), created_at=row.created_at, rotated_at=row.rotated_at)
```

The `/usage` path is declared before the `/{name}/...` route in the file (there is no `GET /{name}`, so no ambiguity either way).

- [ ] **Step 1: Write the failing tests.** `tests/modules/tenants/test_connector_routes.py` (`client`, `tenant`, `admin_headers`; the fixture tenant is `SANDBOX`; `fake_router` fixture (`tmp_path`; `deps, _, _ = make_deps(tmp_path)`) installs `ConnectorRouter([FakeConnector(deps, name="primary"), FakeConnector(deps, name="fallback")], {(CheckType.RFC_VALIDATION, "MX"): ("primary", "fallback")}, default_mode=ConnectorMode.LIVE)` with `state = cast("AppState", app.state.xolotl); state.services = replace(state.services, connectors=router)`. Every test that names a connector uses this fixture and the name `primary`: the real router holds no connector until B4a, so the file is green on a branch from `main` after B3b):

```python
def test_admin_lists_connectors_with_mode_routes_circuit_and_credential_modes(client, admin_headers, fake_router):
    _ = client.put("/v1/connectors/primary/credentials/LIVE", json={"fields": {"token": "t"}}, headers=admin_headers)
    resp = client.get("/v1/connectors", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [
        {"name": "primary", "check_types": ["RFC_VALIDATION"], "mode": "SANDBOX",
         "routes": [{"check_type": "RFC_VALIDATION", "country": "MX", "position": "PRIMARY"}],
         "circuit_state": "CLOSED", "credential_modes": ["LIVE"]},
        {"name": "fallback", "check_types": ["RFC_VALIDATION"], "mode": "SANDBOX",
         "routes": [{"check_type": "RFC_VALIDATION", "country": "MX", "position": "FALLBACK"}],
         "circuit_state": "CLOSED", "credential_modes": []}]}

def test_the_list_shows_this_cells_open_circuit(client, admin_headers, fake_router):
    for _ in range(5): fake_router.connector("primary").breaker("mx-1").record_failure()
    assert client.get("/v1/connectors", headers=admin_headers).json()["items"][0]["circuit_state"] == "OPEN"

def test_setting_credentials_answers_without_the_values_and_audits_the_field_names(client, admin_headers, tenant, admin_engine, caplog, fake_router):
    caplog.set_level(logging.DEBUG); caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    token = f"tok-{uuid4().hex}"
    resp = client.put("/v1/connectors/primary/credentials/LIVE", json={"fields": {"token": token, "user": "ana"}}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["connector"], body["mode"], body["field_names"], body["rotated_at"]) == ("primary", "LIVE", ["token", "user"], None)
    assert token not in resp.text
    assert leaked(caplog.text, token.encode()) == [] and leaked(everything_stored(admin_engine), token.encode()) == []
    assert [(e.action, e.detail) for e in audit_events(tenant.tenant_id) if e.action == "connector.credential_set"] == [
        ("connector.credential_set", {"connector": "primary", "mode": "LIVE", "field_names": ["token", "user"], "rotated": False})]
    again = client.put(... {"fields": {"token": "new"}} ...)
    assert again.json()["rotated_at"] is not None and again.json()["field_names"] == ["token"]

def test_an_unknown_connector_is_a_404(client, admin_headers):
    resp = client.put("/v1/connectors/nobody/credentials/LIVE", json={"fields": {"token": "t"}}, headers=admin_headers)
    assert (resp.status_code, resp.json()["code"]) == (404, "NOT_FOUND")

@pytest.mark.parametrize("body", [{"fields": {}}, {"fields": {"Bad Key": "x"}}, {"fields": {"token": ""}}, {}])
def test_invalid_credential_bodies_are_422(client, admin_headers, fake_router, body):
    assert client.put("/v1/connectors/primary/credentials/LIVE", json=body, headers=admin_headers).status_code == 422

def test_usage_aggregates_per_connector_per_utc_day_for_this_tenant_only(client, admin_headers, tenant):
    other = new_tenant("beta", "admin@beta.mx")
    seed(tenant.tenant_id, "sat_rfc", CallOutcome.PASS, cost=1000, at=datetime(2026, 10, 18, 23, 59, 59, tzinfo=UTC))
    seed(tenant.tenant_id, "sat_rfc", CallOutcome.ERROR, cost=0, at=datetime(2026, 10, 19, 0, 0, 1, tzinfo=UTC))
    seed(tenant.tenant_id, "sat_rfc", CallOutcome.PASS, cost=1000, at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    seed(tenant.tenant_id, "banxico_sie", CallOutcome.PASS, cost=0, at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    seed(other.tenant_id, "sat_rfc", CallOutcome.PASS, cost=5000, at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    resp = client.get("/v1/connectors/usage", params={"from": "2026-10-18", "to": "2026-10-19"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"start": "2026-10-18", "end": "2026-10-19", "items": [
        {"day": "2026-10-18", "connector": "sat_rfc", "calls": 1, "cost_micros": 1000, "outcomes": {"PASS": 1}},
        {"day": "2026-10-19", "connector": "banxico_sie", "calls": 1, "cost_micros": 0, "outcomes": {"PASS": 1}},
        {"day": "2026-10-19", "connector": "sat_rfc", "calls": 2, "cost_micros": 1000, "outcomes": {"ERROR": 1, "PASS": 1}}]}
    # (seed inserts ConnectorCall rows with created_at=at under tenant_session(system_ctx(tenant_id)))

@pytest.mark.parametrize("params", [{"from": "2026-10-19", "to": "2026-10-18"}, {"from": "2025-01-01", "to": "2026-12-31"}, {"to": "2026-10-19"}, {"from": "soon", "to": "2026-10-19"}])
def test_bad_usage_ranges_are_422(client, admin_headers, params):
    resp = client.get("/v1/connectors/usage", params=params, headers=admin_headers)
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")

def test_every_connector_route_is_admin_and_staff_only(client, admin_headers, tenant):
    analyst = _staff_headers(client, tenant, roles=[Role.ANALYST]); auditor = _staff_headers(client, tenant, roles=[Role.AUDITOR_RO])
    machine = {"Authorization": f"Bearer {_api_key(tenant, scopes=['tenants:read'])}"}
    for headers in (analyst, auditor, machine):
        assert client.get("/v1/connectors", headers=headers).json()["code"] == "ROLE_FORBIDDEN"
        assert client.get("/v1/connectors/usage", params={"from": "2026-10-01", "to": "2026-10-02"}, headers=headers).json()["code"] == "ROLE_FORBIDDEN"
        assert client.put("/v1/connectors/sat_rfc/credentials/LIVE", json={"fields": {"token": "t"}}, headers=headers).json()["code"] == "ROLE_FORBIDDEN"
```

(`_staff_headers(client, tenant, roles=...)` and `_api_key(tenant, scopes=...)` are this file's helpers in the idiom of `tests/platform/test_audit_routes.py::_staff_headers` and `tests/modules/tenants/test_api_keys.py`: create the user or key through the service inside `staff_session(tenant.tenant_id, tenant.admin_user_id)`, then `first_login(...)` or the issued key. `seed(tenant_id, connector, outcome, *, cost, at)` adds a `ConnectorCall(..., created_at=at)` under `tenant_session(system_ctx(tenant_id))`. The fixture tenant is `SANDBOX` in cell `mx-1`, as `new_tenant` creates it. `test_every_connector_route_is_admin_and_staff_only` and `test_an_unknown_connector_is_a_404` need no router fixture: `require_access` runs as a dependency and refuses before the handler looks the name up, and `nobody` is unknown to any router.)

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/tenants/test_connector_routes.py -v --no-cov` → FAIL (404 `NOT_FOUND` on every path: the router is not mounted).
- [ ] **Step 3: Implement** `metering.py`, the schemas, the routes, the `app.py` mount, the README section ("Connectors": modes and the sandbox rule, that a `SANDBOX` credential is stored but read by no connector yet, that the `banxico_sie` demo mode (no token outside prod) and its sandbox mock answer from the committed seed files in `policy-packs/_units` (the same files the units page and plan 2a's demo fetcher read), falling back to the constants `UDI = 8.500000` and `USD_MXN_FIX = 18.500000` only on a day the seed does not cover (Deviations 4), `PUT /v1/connectors/{name}/credentials/{mode}`, `GET /v1/connectors/usage?from&to`, where evidence lives, that `XOLOTL_BANXICO_TOKEN` is added by hand in the Render dashboard).
- [ ] **Step 4: Run to verify pass.** The test file, `tests/platform/test_principal.py` (every route declares an access rule), `tests/platform/test_api_conventions.py` (unique operation ids), then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(connectors): list, usage and credential routes for administrators`.

---

### Task B6: The console connectors page

**Files:**
- Create: `console/src/features/connectors/queries.ts`, `ConnectorsPage.tsx`, `ConnectorsTable.tsx`, `UsageCard.tsx`, `badges.tsx`, `format.ts`, `format.test.ts`, `ConnectorsPage.test.tsx`
- Modify: `console/src/api/schema.d.ts` (regenerate), `console/src/api/types.ts`, `console/src/auth/access.ts`, `console/src/auth/access.test.ts`, `console/src/auth/guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `console/src/i18n/en.json`, `console/src/i18n/catalogue.test.ts`
- Test: the two test files above plus the three modified test files

Precondition: B5 merged (the OpenAPI document carries the three routes).

**Interfaces:**

Consumes: the generated `components['schemas']` names `ConnectorResponse`, `ConnectorListResponse`, `ConnectorUsageResponse`, `ConnectorUsageRow`, `ConnectorMode`, `CheckType`, `ConnectorRoute`, `CircuitState`; the harness `renderWithProviders`, `fakeApi`, `respond`, `refuse`, `pending`, `makeAppQueryClient`; `PageHeader`, `DataTable`, `Column`, `ErrorBlock`, `Badge`, `KeyValues`, `Card*`, `Button`; `useAuth`; `useTranslation`; `useFormat` (`@/i18n/use-locale`: `const format = useFormat()` gives `format.locale`, `format.shortDate`, `format.number`, the last with no fraction-digit option); `Locale` (`@/i18n`).

Produces:

```ts
// api/types.ts
export type ConnectorResponse = Schemas['ConnectorResponse']
export type ConnectorList = Schemas['ConnectorListResponse']
export type ConnectorUsage = Schemas['ConnectorUsageResponse']
export type ConnectorUsageRow = Schemas['ConnectorUsageRow']
export type ConnectorMode = Schemas['ConnectorMode']
export type CheckType = Schemas['CheckType']
export type ConnectorRoute = Schemas['ConnectorRoute']
export type CircuitState = Schemas['CircuitState']

// auth/access.ts: connectors: ['ADMIN']
// layout/nav.ts: labelKey union + 'nav.connectors'; { to: '/connectors', labelKey: 'nav.connectors', icon: Plug, roles: ACCESS.connectors } after the audit entry
// App.tsx: a RequireRole group roles={ACCESS.connectors} with { path: '/connectors', lazy: () => import('@/features/connectors/ConnectorsPage').then((m) => ({ Component: m.ConnectorsPage })) }

// features/connectors/format.ts
export type UsageRange = 7 | 30
export function isoDay(d: Date): string                                  // 'YYYY-MM-DD' in UTC
export function usageWindow(days: UsageRange, now: Date): { from: string; to: string }   // to = isoDay(now), from = isoDay(now - (days - 1) days)
export function formatMicros(costMicros: number, locale: Locale): string
//   costMicros / 1_000_000 through new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }), one formatter cached per locale
//   (useFormat().number keeps Intl's default of at most 3 fraction digits, so 1 micro would print as '0')

// features/connectors/queries.ts
const CONNECTORS = 'connectors'
export function useConnectors()                                           // ['connectors', tenant_id] → unwrap(api.GET('/connectors'))
export function useConnectorUsage(window: { from: string; to: string })  // ['connectors', 'usage', tenant_id, window] → api.GET('/connectors/usage', { params: { query: window } }); keepPreviousData
// both: enabled: user !== undefined && !mustChangePassword

// features/connectors/badges.tsx
export function ModeBadge({ mode }: { mode: ConnectorMode })             // SANDBOX → variant 'info' + FlaskConical; LIVE → 'good' + Radio; label t(`connectors.mode.${mode}`)
export function CircuitBadge({ state }: { state: CircuitState })         // CLOSED 'good' + CircleCheck; HALF_OPEN 'warning' + CircleDashed; OPEN 'critical' + CircleAlert; label t(`connectors.circuit.${state}`)

// features/connectors/ConnectorsTable.tsx: DataTable<ConnectorResponse> columns name (mono), checkTypes (Badge variant 'mono' per t(`connectors.checkType.${ct}`)),
//   mode (ModeBadge), routes ("RFC_VALIDATION · MX · PRIMARY" lines from t(`connectors.route.${position}`)), circuit (CircuitBadge),
//   credentials (t('connectors.credentials.none') or the modes joined)
// features/connectors/UsageCard.tsx: const format = useFormat(); two toggle Buttons (7 / 30 days, aria-pressed), DataTable<ConnectorUsageRow> columns day (format.shortDate), connector (mono),
//   calls (<meter min={0} max={maxCalls} value={row.calls} aria-label=...> + the number), failed ((outcomes.ERROR ?? 0) + (outcomes.TIMEOUT ?? 0) + (outcomes.CIRCUIT_OPEN ?? 0)),
//   cost (formatMicros(row.cost_micros, format.locale)); empty → t('connectors.usage.empty')
// features/connectors/ConnectorsPage.tsx: PageHeader + Card(ConnectorsTable) + UsageCard
```

Catalogue keys (es-MX / en; `nav.connectors` beside the other nav keys; `errors.CONNECTOR_NO_ROUTE` in the `errors` block and in `catalogue.test.ts`'s code list; `connectors.mode.SANDBOX` and `connectors.checkType.*` added to `SAME_IN_BOTH`):

| key | es-MX | en |
|---|---|---|
| `nav.connectors` | Conectores | Connectors |
| `connectors.title` | Conectores | Connectors |
| `connectors.description` | Servicios externos de verificación: modo, ruta, estado del circuito y uso. | External verification services: mode, route, circuit state and usage. |
| `connectors.table.label` | Conectores configurados | Configured connectors |
| `connectors.table.name` | Nombre | Name |
| `connectors.table.checkTypes` | Verificaciones | Check types |
| `connectors.table.mode` | Modo | Mode |
| `connectors.table.routes` | Rutas | Routes |
| `connectors.table.circuit` | Circuito | Circuit |
| `connectors.table.credentials` | Credenciales | Credentials |
| `connectors.empty` | No hay conectores configurados. | No connectors are configured. |
| `connectors.mode.SANDBOX` | Sandbox | Sandbox |
| `connectors.mode.LIVE` | En vivo | Live |
| `connectors.circuit.CLOSED` | Cerrado | Closed |
| `connectors.circuit.HALF_OPEN` | Semiabierto | Half open |
| `connectors.circuit.OPEN` | Abierto | Open |
| `connectors.route.PRIMARY` | Primaria | Primary |
| `connectors.route.FALLBACK` | Respaldo | Fallback |
| `connectors.route.MOCK` | Simulada | Mock |
| `connectors.checkType.UNIT_VALUE` | UNIT_VALUE | UNIT_VALUE |
| `connectors.checkType.RFC_VALIDATION` | RFC_VALIDATION | RFC_VALIDATION |
| `connectors.checkType.CURP_VALIDATION` | CURP_VALIDATION | CURP_VALIDATION |
| `connectors.credentials.none` | Sin credenciales | No credentials |
| `connectors.credentials.modes` | Configuradas: {{modes}} | Set for: {{modes}} |
| `connectors.usage.title` | Uso | Usage |
| `connectors.usage.description` | Llamadas y costo por conector y día (UTC). | Calls and cost per connector and day (UTC). |
| `connectors.usage.label` | Uso por día | Usage per day |
| `connectors.usage.range7` | Últimos 7 días | Last 7 days |
| `connectors.usage.range30` | Últimos 30 días | Last 30 days |
| `connectors.usage.day` | Día | Day |
| `connectors.usage.connector` | Conector | Connector |
| `connectors.usage.calls` | Llamadas | Calls |
| `connectors.usage.failed` | Fallidas | Failed |
| `connectors.usage.cost` | Costo | Cost |
| `connectors.usage.empty` | Sin llamadas en el periodo. | No calls in the period. |
| `errors.CONNECTOR_NO_ROUTE` | Ningún conector atiende esta verificación en este país. | No connector serves this check in this country. |

- [ ] **Step 1: Regenerate the API types and write the failing tests.** From the repository root `uv run python -c "import json; from xolotl.app import build_app; print(json.dumps(build_app().openapi()))" > console/openapi.json && cd console && npm run gen:api:file`; commit `schema.d.ts`. Then:

`console/src/auth/access.test.ts`: extend `allowed` with `connectors: hasAnyRole([role], ACCESS.connectors)` and every expectation (`ADMIN` true, the other four false). `guards.test.tsx`: a third `RequireRole roles={ACCESS.connectors}` route `/connectors` → `'page of connectors'`, added to both `it.each` tables. `format.test.ts`: `isoDay(new Date('2026-10-19T03:00:00Z')) === '2026-10-19'`; `usageWindow(7, new Date('2026-10-19T12:00:00Z'))` → `{ from: '2026-10-13', to: '2026-10-19' }`; `usageWindow(30, ...)` → `{ from: '2026-09-20', to: '2026-10-19' }`; `formatMicros(2000, 'es-MX') === '0.002'`, `formatMicros(1_234_567, 'en') === '1.234567'`, `formatMicros(0, 'es-MX') === '0'`.

`ConnectorsPage.test.tsx` (`// @vitest-environment jsdom`, `vi.mock('@/api/client', ...)` to `fakeApi`, `vi.useFakeTimers({ now: new Date('2026-10-19T12:00:00Z'), toFake: ['Date'] })`; fixtures `connectorList()` with `sat_rfc` (`SANDBOX`, `CLOSED`, routes `[RFC_VALIDATION · MX · PRIMARY]`, `credential_modes: []`) and `banxico_sie` (`credential_modes: ['LIVE']`), `usage()` with two rows):

```ts
describe('states', () => {
  it('shows the skeleton while loading, then the connectors with their badges', ...)   // aria-busy then esMX.connectors.circuit.CLOSED, esMX.connectors.mode.SANDBOX, 'sat_rfc'
  it('shows an empty block when no connector is configured', ...)                        // esMX.connectors.empty
  it('shows the failure with its reference and retries', ...)                             // refuse(500,'INTERNAL') → esMX.errors.INTERNAL, 'req-test'; click retry → second request
  it('renders an OPEN circuit as critical', ...)                                           // data-variant="critical" and esMX.connectors.circuit.OPEN
})
describe('usage', () => {
  it('asks for the last 7 days by default and 30 on request', ...)
    // fakeApi.GET calls to '/connectors/usage': query {from:'2026-10-13', to:'2026-10-19'} then {from:'2026-09-20', to:'2026-10-19'}; aria-pressed moves
  it('lists calls, failures and cost per day and connector', ...)                         // a row with calls 3, failed 1, cost '0.002'
  it('shows an empty message for a quiet period', ...)
})
describe('access', () => {
  it('makes exactly one request and shows the forbidden page for a 403', ...)             // makeAppQueryClient(); refuse(403,'ROLE_FORBIDDEN')
})
it('follows the language switcher', ...)                                                   // i18n.changeLanguage('en') → en.connectors.title
it('shows names as text, never markup', ...)                                               // name '<img src=x onerror="window.__pwned=1">' → no <img>
```

- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck` → errors on the missing `ACCESS.connectors`, `nav.connectors`, the `connectors.*` keys; `npx vitest run src/features/connectors src/auth` → FAIL (modules missing).
- [ ] **Step 3: Implement** the access entry, the nav item (icon `Plug` from `lucide-react`), the route group, both catalogues (the table above, same tree), the `catalogue.test.ts` additions, `types.ts`, the feature modules (`format.ts`, `queries.ts`, `badges.tsx`, `ConnectorsTable.tsx`, `UsageCard.tsx`) and the page, following `StaffPage.tsx` for the table card and `features/audit/queries.ts` for the hooks.
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; then from the root `make ci` (nothing in `src/` changed, but the gate closes the task).
- [ ] **Step 5: Commit** `feat(console): connectors page with mode, routes, circuit state and daily usage`.

---

## Parallelism

- B1 first (every later task imports its models and enums); it needs plan 2a's A1 (`banxico_token`, `xolotl.platform.text`) and A2 (`0006_policy_units_calendar`) on `main`.
- B2 after B1 (the vault uses `ConnectorCredential`; the breaker and types could be written earlier but share the package `__init__`).
- B3a after B2; B3b after B3a (the router runs `Connector.run`; `connectors/__init__.py` is the only file both touch, two export lines).
- **B4a ∥ B5** after B3b: B4a touches `sat.py`, `defaults.py`, `pyproject.toml`, the wiring test's names lines; B5 touches `metering.py`, `routes_connectors.py`, `schemas.py`, `app.py`'s router list, the README; no file in common (both add a line to `connectors/__init__.py`'s exports: the second to merge rebases a one-line conflict). B5's tests use only B3a's `FakeConnector` through its `fake_router` fixture, never a real connector, so B5 is green on a branch from `main` after B3b.
- **B4b after B4a** (it edits `defaults.py` and the names lines B4a wrote) and after plan 2a's A5 (`BanxicoSieSource`, `SeedFileSource`, `UnitValue`, `UnitSourceError`, `UnitSourceAuthError` on `main`); B4b ∥ B5 and B4b ∥ B6 (no file in common).
- **B6 after B5** (the OpenAPI document B6 regenerates carries the three routes); B6 ∥ B4a and B6 ∥ B4b (the document does not change with the connectors).
- Across plans: in SP-02 plan 2c needs only B1 (the `connector_call` table its `verification_check.connector_call_id` references by composite FK, and the `CheckType` and `ConnectorMode` enums its C2 imports); no plan 2c task calls `run_check` in SP-02 (copying a `VerificationOutcome` into `verification_check` is SP-08's), so B3a onward gates no 2c task. B1 needs plan 2a's A1 and A2; B4a needs nothing more from plan 2a than A1's `xolotl.platform.text` (`RFC_SHAPE`), already on `main` by B1's precondition; B4b needs plan 2a's A5 (`BanxicoSieSource`, `SeedFileSource`, `UnitValue`, `UnitSourceError`, `UnitSourceAuthError`) merged.
- **Across plans (shared files).** The three plans append to the same files: `src/xolotl/app.py` (A5, B3b and C5 in `wire_platform`; A8, B5, C6 and C7 each mount a router after `routes_webhooks.router`), `src/xolotl/models.py`, `src/xolotl/platform/enums.py` (A2, B1), `src/xolotl/platform/config.py`, `src/xolotl/modules/tenants/service.py` (A5, C5), `src/xolotl/cli.py`, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`, `tests/platform/test_worker.py` and `tests/modules/tenants/test_kernel_events.py` (A5, C5), `.env.example`, `README.md`, `render.yaml` and `tests/test_render_blueprint.py` (A1, B1), and in the console `nav.ts` (A9 and B6 both insert after the audit entry), `App.tsx`, `access.ts` and its tests, both catalogues, `catalogue.test.ts`, `schema.d.ts` and `e2e/smoke.spec.ts` (A9, C8). Every such edit is additive (a line, a row, an entry, a factory or a test beside the existing ones), so the second task to merge rebases and keeps both sides; router and nav order is immaterial (each router has its own prefix and `NAV_ITEMS` is filtered per role, so the entries may land in any order); `console/src/api/schema.d.ts` is regenerated from the merged API (`npm run gen:api:file`), never merged by hand.

## Exit criteria

From spec §13, owned here: **a `SANDBOX` tenant's SAT connector call returns a mock and a metering row** (`run_check` with `{"rfc": "ABC010101AA8"}` under a `SANDBOX` tenant gives `FAIL`/`CFDI_CANCELLED` with `route = MOCK`, a `connector_call` row with `mode = SANDBOX`, `cost_micros = 0`, and an evidence object at `connectors/sat_rfc/<call id>`; no HTTP request is made): pinned by `test_run_check_through_the_default_router_gives_a_sandbox_tenant_the_mock_and_a_metering_row` in Task B4a, through the router `default_router` builds for the composition root. Refined for this plan: `tests/platform/test_connectors.py` (B3a) proves retries with backoff `[0.2, 0.5]`, `TIMEOUT` after three timeouts, the breaker opening at five failures within 60 s, still refusing at 29 s, half-opening at 30 s, closing on a successful trial and reopening for a further 30 s on a failed one, evidence and `details` stored with every credential value replaced by `[REDACTED]`, one metering row per call including `CIRCUIT_OPEN`, and sandbox forcing with route `MOCK` for every `SANDBOX` call; `tests/platform/test_connector_router.py` (B3b) proves primary-then-fallback routing with both routes recorded and the default-mode rule; a `PROD` tenant calls the SAT service through `respx` with the exact SOAP envelope and interprets the four recorded answers as documented, and a 200 with an unreadable body is an `ERROR` outcome (B4a); the Banxico connector takes a vault token over the platform token and the latest prior observation, and its demo mode and sandbox mock answer from the committed seed files (B4b); `GET /v1/connectors` lists `banxico_sie` and `sat_rfc` with mode, routes, circuit state and credential modes; `GET /v1/connectors/usage` aggregates per connector per UTC day for the caller's tenant only; `PUT /v1/connectors/{name}/credentials/{mode}` stores ciphertext, never echoes a value and audits `connector.credential_set`; the console's `/connectors` page is ADMIN-only and shows the two cards; `make ci` and the console gates are green after every task.

## Open questions for the product owner

1. **What should a live `RFC_VALIDATION` call verify?** D22 names Xolo's SAT CFDI status service, which validates an RFC only as the issuer or receiver of a known CFDI (Deviations 3). The SAT's RFC-validation portal has no public API and the 69-B (EFOS) list is a periodic publication. If SP-08's onboarding needs "this RFC exists and is active" without a CFDI, that is a different source (the 69-B CSV ingested as a list, or a vendor) and a product decision; this plan ports what exists.
2. **Where should per-tenant connector routing live?** Spec §8 reads routes from `tenant_connector_config` "in the regulatory profile", but plan 2a's `tenant_regulatory_profile` has no such column and no second vendor exists yet. This plan ships the platform default only; the column (or a `tenant_connector_config` table) is for the sub-project that adds the second vendor per check type (SP-08).
3. **Metering durability.** A `connector_call` row joins the caller's transaction, so a caller that rolls back after a paid live call loses the metering (and the evidence object stays as an orphan). Billing-grade metering needs an autonomous write (a second session) or an outbox-style row; is best-effort metering acceptable for the pilot?
4. **`CONNECTOR_MODE_DEFAULT` in production.** This plan sets `LIVE` in `render.yaml` so `PROD` tenants call live services by default (Deviations 2). If the pilot should start every tenant on mocks and switch per call instead, the blueprint keeps `SANDBOX` and the switch is one line.
