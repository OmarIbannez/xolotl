# SP-02 — Policy packs, units, calendar, connector SDK, party core — Sub-project design

**Parent spec:** `docs/superpowers/specs/2026-10-18-program-architecture-design.md` (D2–D7, D17, D20–D23; ports §4: `UnitValueSource`, `Connector`/`Router`/`CredentialVault`, `Clock`; SP-02 row of §7.1). Research sources: Notion `10-f00-platform-foundations.md` §4–§6, §9; `07-data-model.md` §3, §5, §6; feature list F00/F01; the ITF/IFPE catalogue and the LFPIORPI fr. XVI notes consolidated in program spec D21.
**Status:** draft 2026-10-19 for the product owner's review. Builds on SP-01 (1a kernel, 1b data protection, 1c events/jobs, 1d console) and uses their ports as they stand on branch `feat/sp01a-kernel`.

## 1. Scope

In scope:
- **Policy packs**: versioned YAML rulebooks per `<CC>/<SECTOR>-<LICENCE>`, a Pydantic schema with per-value provenance, a loader (`xolotl packs load`), tenant pack assignments, tighten-only tenant overrides, a resolver producing an *effective policy* for a date and product, canonical-JSON snapshots (`policy_snapshot`) referenced by every later decision, golden tests, and the first two packs: `MX/ITF-IFPE` v1 and `MX/LFPIORPI-AVI` v1 (fr. XVI only).
- **Units**: the `unit_value` store with corrections, `ConversionRule`s, the `UnitService`, seeded UMA values, Banxico SIE fetchers for UDI and USD/MXN FIX (ported from the Xolo MCP server), a daily fetch job, and the rule that a missing value blocks.
- **Regulatory calendar**: `holiday` table seeded from `policy-packs/_holidays/MX.yaml` with national, CNBV and SAT scopes, business-day helpers, and the deadline DSL (`due_at`) with golden tests across 2025–2027.
- **Connector SDK**: a synchronous `Connector` base with timeouts, retries, a circuit breaker, credential lookup from a tenant-encrypted vault, evidence capture to the object store, metering rows, a router with primary→fallback per check type, and sandbox mocks selected by `tenant.env`; the first connectors are Banxico SIE (live or demo) and SAT RFC (live or demo); identity-vendor connectors come in SP-08.
- **Party core**: `party`, `person`, `organization`, `party_identifier` (encrypted value + blind index), `party_contact`, `party_address`, `party_relationship`, `document`, `customer_relationship`, a minimal `verification_check`, identifier validators (CURP, RFC with homoclave and persona moral, CLABE, INE CIC format, CPF, CNPJ, CUIT, RUT, NIT, cédula CO, DNI PE, passport format), retention (`retention_until`, `legal_hold`, a purge job), the staff and machine API views, and `party.*` events.
- **Console increments**: pack viewer (effective policy by date, assignments, overrides), units and calendar pages, party pages (search, detail with explicit reveal, create/edit, identifiers, addresses, relationships, documents).

Out of scope (later sub-projects): list ingestion and screening (SP-03; `party_name_index` is created there per D7), transactions (SP-04), report definitions and serializers (SP-05, which consume the packs' `captures` and `reports` sections), identity-vendor connectors and onboarding journeys (SP-08), UBO computation (SP-09; the graph tables exist here), obligations (SP-10).

Delivered as three plans, in this order:
- **2a — Packs, units, calendar** (shared reference tables, pack schema and loader, resolver and snapshots, both MX packs with goldens, units and fetch job, holidays and deadline DSL, CLI, pack/units/calendar console pages).
- **2b — Connector SDK** (vault, router, circuit breaker, metering, evidence, sandbox mocks, Banxico and SAT connectors, console connector status page).
- **2c — Party core** (tables, validators, blind indexes, retention, API, events, party console pages).

## 2. Toolchain and conventions

- Adds runtime `pyyaml` and dev `types-pyyaml`. Everything else is the SP-01 stack (`httpx` already present).
- Repository layout per the program spec §3.2: `policy-packs/MX/ITF-IFPE/v1.yaml`, `policy-packs/MX/LFPIORPI-AVI/v1.yaml`, `policy-packs/_units/{UMA.yaml, UDI.seed.yaml, USD_MXN_FIX.seed.yaml}`, `policy-packs/_holidays/MX.yaml`, `policy-packs/_jurisdictions.yaml` (seed only; risk factors use it in SP-07). Code: `src/xolotl/platform/{policy,units,calendar,connectors}/`, `src/xolotl/modules/party/`, pack-related tenant routes in `modules/tenants/routes_policy.py`.
- Every pack value that is a threshold, deadline, period, list name or legal switch is a `Provenanced[T]`: `{value, unit?, legal_basis, confidence: VERIFIED|UNVERIFIED, verified_as_of}` (D4). The loader refuses `UNVERIFIED` values unless `--allow-unverified` (never in `ENV=prod`).
- Golden tests are data files under `tests/golden/packs/`, `tests/golden/units/`, `tests/golden/calendar/`; a failing golden prints a unified diff.
- All SP-01 conventions bind (RLS, isolation factories, `v1_router`, `require_access`, idempotency, `Encrypted` fields, audit actions, events with ids-only payloads).

## 3. Configuration (prefix `XOLOTL_`)

| Key | Default | Purpose |
|---|---|---|
| `PACKS_DIR` | `./policy-packs` | pack, unit seed and holiday sources |
| `ALLOW_UNVERIFIED_PACK_VALUES` | `false` | loader switch; must be false in prod (validated) |
| `BANXICO_TOKEN` | unset | SIE API token; when unset the Banxico connector runs in demo mode outside prod and refuses in prod |
| `CONNECTOR_MODE_DEFAULT` | `SANDBOX` | mode for tenants without an explicit setting; PROD tenants default to `LIVE` |
| `UNITS_FETCH_CRON` | `0 18 * * 1-5` | daily fetch in `America/Mexico_City` |
| `RETENTION_PURGE_CRON` | `0 3 * * *` | daily party retention sweep |

## 4. Tables

### 4.1 Shared reference tables (no `tenant_id`; registered in `SHARED_TABLES`, read-only for the app role except where noted)

```sql
CREATE TABLE policy_pack (id uuid PRIMARY KEY, code text UNIQUE NOT NULL,          -- '<CC>/<SECTOR>-<LICENCE>'
  country_code char(2) NOT NULL, sector text NOT NULL, licence text NOT NULL, title text NOT NULL);

CREATE TABLE policy_pack_version (id uuid PRIMARY KEY, pack_id uuid NOT NULL REFERENCES policy_pack(id),
  version int NOT NULL, effective_from date NOT NULL, effective_to date,
  config jsonb NOT NULL,                       -- validated PackConfig, provenance included
  content_hash bytea NOT NULL,                 -- sha256 of canonical JSON
  loaded_at timestamptz NOT NULL DEFAULT now(), loaded_by text NOT NULL, source_path text NOT NULL,
  UNIQUE (pack_id, version));

CREATE TABLE unit_value (id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  unit_code text NOT NULL,                     -- UMA, UDI, USD_MXN_FIX
  valid_from date NOT NULL, valid_to date,     -- null = open
  value numeric(20,8) NOT NULL, quote_ccy char(3) NOT NULL,
  source text NOT NULL, fetched_at timestamptz NOT NULL, superseded_by bigint REFERENCES unit_value(id),
  UNIQUE (unit_code, valid_from, fetched_at));
CREATE INDEX ix_unit_value_lookup ON unit_value (unit_code, valid_from DESC) WHERE superseded_by IS NULL;
-- app role: SELECT + INSERT + UPDATE (the fetch job writes; corrections are new rows plus superseded_by on the old one)

CREATE TABLE holiday (country_code char(2) NOT NULL, scope text NOT NULL,          -- NATIONAL, CNBV, SAT, BANXICO
  day date NOT NULL, name text NOT NULL, source text NOT NULL, PRIMARY KEY (country_code, scope, day));
```

### 4.2 Tenant tables (forced RLS, PK `(tenant_id, id)`, isolation factories)

```sql
CREATE TABLE tenant_pack_assignment (tenant_id uuid NOT NULL, id uuid NOT NULL,
  pack_version_id uuid NOT NULL REFERENCES policy_pack_version(id),
  product text,                                -- null = all products; otherwise the tenant's product code
  effective_from date NOT NULL, effective_to date,
  approved_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), FOREIGN KEY (tenant_id, approved_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE tenant_override_version (tenant_id uuid NOT NULL, id uuid NOT NULL,
  version int NOT NULL, overrides jsonb NOT NULL,     -- validated TenantOverride (tighten-only, proven against the merged packs)
  effective_from date NOT NULL, approved_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, version),
  FOREIGN KEY (tenant_id, approved_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE policy_snapshot (tenant_id uuid NOT NULL, id uuid NOT NULL,
  content_hash bytea NOT NULL, content jsonb NOT NULL,  -- effective policy + {pack_version_ids, override_version, on, product}
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, content_hash));

CREATE TABLE tenant_regulatory_profile (tenant_id uuid NOT NULL, id uuid NOT NULL,
  rfc_enc bytea NOT NULL, legal_name text NOT NULL, casfim_supervisor text, casfim_entity_key text,
  spplld_activity_key text, platform_domain text, entidad_colegiada boolean NOT NULL DEFAULT false,
  compliance_officer_user_id uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id));   -- one profile per tenant

CREATE TABLE tenant_branch (tenant_id uuid NOT NULL, id uuid NOT NULL, code text NOT NULL, name text NOT NULL,
  address_enc bytea NOT NULL, state_code text NOT NULL, municipality_code text NOT NULL, status text NOT NULL,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, code));

CREATE TABLE connector_credential (tenant_id uuid NOT NULL, id uuid NOT NULL, connector text NOT NULL,
  mode text NOT NULL,                          -- SANDBOX, LIVE
  secret_enc bytea NOT NULL,                   -- EncryptedStr JSON of the credential fields
  created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), rotated_at timestamptz,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, connector, mode));

CREATE TABLE connector_call (tenant_id uuid NOT NULL, id uuid NOT NULL, connector text NOT NULL, check_type text NOT NULL,
  mode text NOT NULL, route text NOT NULL,     -- PRIMARY, FALLBACK, MOCK
  outcome text NOT NULL,                       -- PASS, FAIL, INCONCLUSIVE, ERROR, TIMEOUT, CIRCUIT_OPEN
  duration_ms int NOT NULL, cost_micros bigint NOT NULL DEFAULT 0, evidence_key text, error text,
  subject_party_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id));
CREATE INDEX ix_connector_call_tenant_day ON connector_call (tenant_id, created_at);

CREATE TABLE party (tenant_id uuid NOT NULL, id uuid NOT NULL, party_type text NOT NULL,   -- PERSON, ORGANIZATION
  country_code char(2) NOT NULL, status text NOT NULL DEFAULT 'ACTIVE',                   -- ACTIVE, MERGED, PURGED
  retention_until date, legal_hold boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id));

CREATE TABLE person (tenant_id uuid NOT NULL, id uuid NOT NULL,                             -- id = party.id
  given_names_enc bytea NOT NULL, first_surname_enc bytea NOT NULL, second_surname_enc bytea,
  birth_date_enc bytea, birth_country char(2), birth_state text, nationalities char(2)[] NOT NULL DEFAULT '{}',
  gender text, occupation_code text, economic_activity_code text, is_resident boolean,
  pep_status text NOT NULL DEFAULT 'NONE', pep_since date, pep_until date,                 -- NONE, DOMESTIC, FOREIGN, RCA, INTL_ORG
  clave_elector_enc bytea,
  PRIMARY KEY (tenant_id, id), FOREIGN KEY (tenant_id, id) REFERENCES party (tenant_id, id));

CREATE TABLE organization (tenant_id uuid NOT NULL, id uuid NOT NULL,
  legal_name_enc bytea NOT NULL, trade_name text, legal_form text, incorporation_date date,
  industry_code text, registry_ref text, country_of_incorporation char(2),
  PRIMARY KEY (tenant_id, id), FOREIGN KEY (tenant_id, id) REFERENCES party (tenant_id, id));

CREATE TABLE party_identifier (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid NOT NULL,
  id_type text NOT NULL, country_code char(2) NOT NULL, value_enc bytea NOT NULL, value_hash bytea NOT NULL,
  verified_check_id uuid, valid_from date, valid_to date, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, id_type, value_hash),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

CREATE TABLE party_contact (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid NOT NULL,
  kind text NOT NULL, value_enc bytea NOT NULL, value_hash bytea NOT NULL,                  -- EMAIL, PHONE
  verified_check_id uuid, is_primary boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, party_id, kind, value_hash),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

CREATE TABLE party_address (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid NOT NULL,
  address_type text NOT NULL, address_enc bytea NOT NULL,                                   -- RESIDENTIAL, FISCAL, OPERATIONAL
  country_code char(2) NOT NULL, admin_area_code text, municipality_code text, postal_code text,
  valid_from date NOT NULL, valid_to date,
  PRIMARY KEY (tenant_id, id), FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

CREATE TABLE party_relationship (tenant_id uuid NOT NULL, id uuid NOT NULL,
  from_party_id uuid NOT NULL, to_party_id uuid NOT NULL, rel_type text NOT NULL,
  ownership_pct numeric(7,4), control_basis text, valid_from date NOT NULL, valid_to date, source_doc_id uuid,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, from_party_id) REFERENCES party (tenant_id, id),
  FOREIGN KEY (tenant_id, to_party_id) REFERENCES party (tenant_id, id),
  CHECK (from_party_id <> to_party_id));

CREATE TABLE document (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid NOT NULL,
  doc_type text NOT NULL, storage_key text NOT NULL, sha256 bytea NOT NULL, size int NOT NULL, content_type text NOT NULL,
  extracted_enc bytea, uploaded_by text NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

CREATE TABLE customer_relationship (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid NOT NULL,
  external_ref text NOT NULL, product text, status text NOT NULL,                           -- PENDING, ACTIVE, RESTRICTED, BLOCKED, EXITED
  account_tier text, next_review_at date, started_at timestamptz, ended_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, external_ref),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));

CREATE TABLE verification_check (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid,
  check_type text NOT NULL, provider text NOT NULL, mode text NOT NULL, result text NOT NULL,   -- PASS, FAIL, INCONCLUSIVE
  details jsonb, evidence_key text, connector_call_id uuid, policy_snapshot_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));
```

`party_identifier.verified_check_id` and `party_contact.verified_check_id` get composite FKs to `verification_check` in the same migration (both tables exist). `person.id = party.id` and `organization.id = party.id` (one-to-one subtype rows). `party_name_index` (D7) is created by SP-03.

## 5. Policy packs

### 5.1 Pack schema v1 (`xolotl/platform/policy/schema.py`)

```python
class Confidence(StrEnum): VERIFIED = "VERIFIED"; UNVERIFIED = "UNVERIFIED"

class Provenanced[T](BaseModel):             # every regulatory value
    value: T; unit: str | None = None; legal_basis: str; confidence: Confidence; verified_as_of: date

class Quantity(BaseModel): amount: Decimal; unit: str            # UDI, UMA, USD, MXN
class TierLimits(BaseModel): monthly_ops: Provenanced[Quantity] | None; max_balance: Provenanced[Quantity] | None
class KycTier(BaseModel): code: str; limits: TierLimits; required_fields: list[str]; required_checks: list[str]; assurance: str
class KycPolicy(BaseModel): tiers: list[KycTier]; review_frequency: dict[str, Provenanced[str]]; geolocation_required: Provenanced[bool]
class UboPolicy(BaseModel): ownership_threshold_pct: Provenanced[Decimal]; control_tests: list[str]; fallback: Literal["SENIOR_MANAGING_OFFICIAL"]; legal_person_can_be_ubo: bool = False
class ScreeningBands(BaseModel): sanctions_alert: Decimal = Decimal("0.70"); sanctions_review: Decimal = Decimal("0.55"); pep_alert: Decimal = Decimal("0.80"); id_match_floor: Decimal = Decimal("0.95"); id_conflict_cap: Decimal = Decimal("0.30")
class TrueMatchAction(BaseModel): action: Literal["SUSPEND_IMMEDIATELY", "REVIEW"]; report: str | None; notify_client: bool = False
class ScreeningPolicy(BaseModel): mandatory_lists: list[Provenanced[str]]; recommended_lists: list[str]; bands: ScreeningBands; pep: PepPolicy; on_true_match: dict[str, TrueMatchAction]
class Capture(BaseModel): code: str; kind: Literal["THRESHOLD", "ACCUMULATION", "FEE_THRESHOLD"]; threshold: Provenanced[Quantity]; conversion_rule: ConversionRule; window: str | None; period: Literal["MONTH", "QUARTER"]; report: str; filing: Literal["SERIALIZED", "MANUAL_UPLOAD"]
class ReportRef(BaseModel): code: str; deadline: DeadlineRule; zero_report: bool
class RetentionPolicy(BaseModel): kyc_after_relationship: Provenanced[str]; transactions: Provenanced[str]     # ISO-8601 durations
class GovernancePolicy(BaseModel): committee_required: Provenanced[bool]; four_eyes_on_reports: bool = True
class PackConfig(BaseModel):
    pack: str; version: int; effective_from: date; country: str; regulators: list[str]; products: list[str]
    units: UnitsPolicy; kyc: KycPolicy; ubo: UboPolicy; screening: ScreeningPolicy
    captures: list[Capture]; reports: list[ReportRef]; retention: RetentionPolicy; governance: GovernancePolicy
```

The loader (`load_pack(path) -> PackConfig`) validates the YAML, refuses `UNVERIFIED` values unless allowed, computes `content_hash` over canonical JSON (sorted keys, no whitespace, Decimals as strings — program spec addendum b), and upserts `policy_pack`/`policy_pack_version` (a changed hash for an existing version is an error: versions are immutable).

### 5.2 Pack contents (values per program spec D21; every value carries provenance in the YAML)

`MX/ITF-IFPE` v1 (`effective_from` = platform start date): products `EMONEY`, `CROWDFUNDING`, `CRYPTO`; tiers N1 (750 UDI monthly ops, 1 000 UDI balance, assurance A0 with geolocation, device risk and screening), N2 (3 000 UDI monthly ops), N3/FULL per the ITF catalogue, tier limits converted with `LAST_DAY_PREV_MONTH`; UBO 25 %; mandatory lists `[MX_LPB, UN_CONSOLIDATED]`, recommended `[OFAC_SDN, MX_SAT_69B, EU_FSF, UK_FCDO]`; captures `ITF_ROR_CASH` (USD 5 000, `PRIOR_BANKING_DAY`, quarterly, `MX_F41_ROR`, serialized, zero report), `ITF_FX_CASH` (USD 500, quarterly, manual upload), `ITF_INTL_TRANSFER` (USD 1 000, monthly, manual upload), `ITF_VA_OPS` (7 500 UDI, quarterly, manual upload), tier-limit capture; reports with deadline rules (ROR `NTH_BUSINESS_DAY_OF(period_end + 1 month, 10)`, ROI `BUSINESS_DAYS_AFTER(committee_decision, 3)`, ROI24 `HOURS_AFTER(detection, 24)`); retention 10 years; `committee_required: true`.
`MX/LFPIORPI-AVI` v1 (`effective_from` 2026-11-30): product `CRYPTO`; captures `AVI_XVI_A` (210 UMA per operation or six-month accumulation, `SAME_DAY` UMA of each operation's date, monthly, `MX_AVISO_AVI`, serialized, informe en ceros) and `AVI_XVI_B` (4 UMA fee, no aggregation); reports with `DAY_OF_NEXT_MONTH(17, roll=NEXT_BUSINESS_DAY)`; retention 5 years for avisos (10 for KYC per the ITF pack when both apply); mandatory lists `[MX_LPB, UN_CONSOLIDATED]`.
Values whose legal basis is not yet verified in the research (Formato 41 encoding, ROR period granularity, Anexo 3 branch fields) are marked `UNVERIFIED` and listed in §12 for the compliance review; the loader refuses them in prod until they are verified.

### 5.3 Assignment, overrides, resolution, snapshots

- `assign_pack(session, *, pack_version_id, product, effective_from, approved_by)` (ADMIN; audits `policy.pack_assigned`; two packs for one tenant need distinct products — program spec D21).
- `TenantOverride` mirrors `PackConfig` with every field optional; `validate_against(effective)` enforces tighten-only: thresholds and limits only lower, review periods only shorter, retention only longer, lists only added, bands only stricter, `committee_required` only true; violations raise `PolicyLooseningError` (409 `POLICY_LOOSENING`). `create_override(session, overrides, approved_by)` stores a new version and audits `policy.override_created`.
- `effective_policy(session, *, on: date, product: str | None) -> EffectivePolicy` loads the assignments valid on `on` (filtered by product), merges strictest (min thresholds, union of lists, max retention, strictest bands, `committee_required` OR), applies the latest override version effective on `on`, and returns an `EffectivePolicy` with `pack_version_ids`, `override_version`, `on`, `product`.
- `snapshot_policy(session, effective) -> UUID` canonicalises, hashes, upserts `policy_snapshot` by `(tenant_id, content_hash)` and returns its id; every later decision stores `policy_snapshot_id` (D4). `get_snapshot(session, id)` returns the frozen content.
- Golden tests: for each pack version, a fixtures file of `(on, product, overrides) -> expected effective values` (tier limits, capture thresholds, lists, deadlines, retention, committee), run on every CI; an override that loosens is a golden expecting `POLICY_LOOSENING`.

## 6. Units

- `ConversionRule(StrEnum)`: `SAME_DAY`, `PRIOR_BANKING_DAY` (Banxico FIX for ITF USD thresholds), `LAST_DAY_PREV_MONTH` (UDI for tier limits), `PREV_MONTH_AVG`, `SEMIANNUAL_SNAPSHOT`.
- `UnitService.lookup(unit_code, on, rule, *, country="MX", scope="BANXICO") -> UnitValueRow` resolves the effective row per rule (uses the calendar for banking days); `to_unit(amount: Decimal, currency: str, unit: str, on, rule) -> Conversion` and `threshold_in_ccy(threshold: Quantity, currency, on, rule) -> Conversion`, where `Conversion = {amount: Decimal, unit_value_id: int, rule, on, resolved_date}` so every stored amount records the `unit_value.id` it used (D5). A missing value raises `UnitValueMissing` (503 `UNIT_VALUE_MISSING`); nothing is silently reused.
- Sources: `UnitValueSource` port with `BanxicoSieSource` (series SF43718 FIX, SP68257 UDI; ported from `~/code/xolo/src/xolo/providers/banxico.py`, token from `BANXICO_TOKEN`, demo mode returns the committed seed), and `SeedFileSource` for UMA (`policy-packs/_units/UMA.yaml`, annual values with DOF citation and provenance). `xolotl units seed` loads seeds; `xolotl units fetch --unit UDI --from --to` and the job `units.fetch` (schedule `UNITS_FETCH_CRON`) fetch missing days; corrections insert a new row and set `superseded_by` on the old one; the job raises an in-app notification to ADMIN/MLRO when a source fails.
- Golden tests: FIX prior-banking-day resolution across a weekend and a Banxico holiday; UDI last-day-of-previous-month for January; UMA annual boundary on 1 February; missing-value blocking.

## 7. Regulatory calendar

- `holiday` seeded from `policy-packs/_holidays/MX.yaml` (2025–2027: national statutory days, CNBV non-working days, SAT non-working days, Banxico non-banking days) by `xolotl holidays seed`.
- `calendar.is_business_day(day, *, country, scope)`, `add_business_days(day, n, ...)`, `nth_business_day_of_month(year, month, n, ...)`, `next_business_day(day, ...)`.
- `DeadlineRule(kind, params, tz)` with kinds `BUSINESS_DAYS_AFTER`, `NTH_BUSINESS_DAY_OF`, `DAY_OF_NEXT_MONTH`, `CALENDAR_DAYS_AFTER`, `HOURS_AFTER`, `DATE_RANGE`, `FIXED`; `due_at(rule, anchor: datetime, *, country, scope) -> datetime` (aware, in `tz`, end of business day 23:59:59 local for day-granular rules unless `params.time` given). Golden tests per kind for 2025–2027 including Semana Santa and the 1-January roll.

## 8. Connector SDK

```python
class CheckType(StrEnum): UNIT_VALUE = "UNIT_VALUE"; RFC_VALIDATION = "RFC_VALIDATION"; CURP_VALIDATION = "CURP_VALIDATION"; ...   # extended by SP-08
class ConnectorMode(StrEnum): SANDBOX = "SANDBOX"; LIVE = "LIVE"
@dataclass(frozen=True) class ConnectorRequest: check_type: CheckType; subject_party_id: UUID | None; params: Mapping[str, str]
@dataclass(frozen=True) class RawResponse: status: int; body: bytes; headers: Mapping[str, str]; elapsed_ms: int
class CheckResult(BaseModel): outcome: Literal["PASS", "FAIL", "INCONCLUSIVE"]; details: dict[str, JsonValue]; cost_micros: int = 0

class Connector(ABC):
    name: str; check_types: frozenset[CheckType]; timeout_s: float = 8.0; max_attempts: int = 3
    def _call(self, request: ConnectorRequest, credentials: Mapping[str, str]) -> RawResponse: ...
    def _interpret(self, raw: RawResponse) -> CheckResult: ...
    def _mock(self, request: ConnectorRequest) -> RawResponse: ...        # sandbox behaviour, deterministic per subject
    def run(self, session: Session, request: ConnectorRequest, *, mode: ConnectorMode) -> VerificationOutcome
        # vault.get → circuit breaker → retry(_call) with backoff → evidence store put (raw body, redacted of credentials)
        # → connector_call row (metering) → CheckResult; errors become outcome ERROR/TIMEOUT/CIRCUIT_OPEN, never exceptions to callers

class CredentialVault: get(session, connector, mode) -> Mapping[str, str]; set(session, connector, mode, fields) (ADMIN, audited `connector.credential_set`, EncryptedStr JSON)
class ConnectorRouter: route(check_type, *, country, tenant_mode) -> list[Connector] (primary then fallbacks from `tenant_connector_config` in the regulatory profile or the platform default); records the route taken
class CircuitBreaker: per (connector, cell) in-process state: CLOSED → OPEN after 5 failures in 60 s → HALF_OPEN after 30 s
```

- `tenant.env = SANDBOX` forces `mode = SANDBOX` (mocks) regardless of configuration; `PROD` tenants use `LIVE` unless a credential for `SANDBOX` is explicitly selected per call for testing.
- First connectors: `banxico_sie` (check type `UNIT_VALUE`; live with token, demo otherwise) and `sat_rfc` (RFC validity and EFOS status from the SAT services, ported from `~/code/xolo/src/xolo/providers/sat.py`; mock returns deterministic results keyed by the RFC's last character).
- Metering: `connector_call` rows; `GET /v1/connectors/usage?from&to` (ADMIN) aggregates calls and cost per connector per day; `GET /v1/connectors` lists connectors with mode, route and circuit state.

## 9. Party core

- Identifier validators (`xolotl/modules/party/identifiers.py`), each `validate(value) -> NormalizedIdentifier | IdentifierError` with a structured reason: CURP (18 chars, date and sex checks, state code, check digit), RFC (persona física 13 / persona moral 12 with homoclave check digit, ported from Xolo), CLABE (18 digits, weights 3-7-1 check digit, bank catalogue from Xolo), INE CIC/OCR format, CPF, CNPJ, CUIT/CUIL, RUT, NIT, cédula CO, DNI PE, passport (ICAO MRZ check digit when provided). Sentinel's `rules/identity.py` is the base; the corpus of valid and invalid samples is copied into `tests/golden/identifiers/`.
- Writes go through the service: `create_party(session, PartyCreate) -> Party` (person or organization with encrypted names, identifiers validated, blind-indexed and deduplicated per `(id_type, value_hash)`; a duplicate raises 409 `IDENTIFIER_EXISTS` with the existing party id in `detail` only for staff), `add_identifier`, `add_contact`, `add_address`, `add_relationship` (validates the pair exists and `ownership_pct` only for `SHAREHOLDER`), `upload_document` (object store `documents/<party_id>/<doc_id>`, sha256 and size recorded; max 25 MB; allowed content types list), `open_relationship`/`close_relationship` (customer_relationship lifecycle; closing computes `retention_until = ended_at + retention.kyc_after_relationship` from the effective policy's snapshot).
- Reads: staff detail view reveals names and identifiers through `reveal_for_response` (one `VIEW_PII` per field group per request); list and machine views return masked values (`****1234` for identifiers, initials for names) and never the plaintext; search by exact identifier uses `blind_index` (`GET /v1/parties?id_type=RFC&value=`); search by name waits for SP-03's `party_name_index`.
- Retention: job `party.retention_sweep` (schedule `RETENTION_PURGE_CRON`) sets `status = PURGED`, nulls encrypted fields and deletes documents from the object store for parties past `retention_until` without `legal_hold` and without open relationships; audits `party.purged`; a `legal_hold` toggle (MLRO) audits `party.legal_hold_set`.
- Events: `party.created.v1`, `party.updated.v1`, `party.identifier_added.v1`, `party.relationship_opened.v1`, `party.relationship_closed.v1`, `party.purged.v1` — ids only.
- API (`/v1/parties`, staff and `parties:read|write` scopes): `POST /v1/parties` 201, `GET /v1/parties` (filters: `id_type`+`value`, `external_ref`, `status`; cursor), `GET /v1/parties/{id}` (staff view with reveal; machine view masked), `PATCH /v1/parties/{id}` (non-identifying attributes), `POST /v1/parties/{id}/identifiers|contacts|addresses|relationships|documents`, `GET /v1/parties/{id}/documents/{doc_id}/content` (staff, `VIEW_PII`), `POST /v1/relationships` (customer relationship open) and `POST /v1/relationships/{id}/close`, `POST /v1/parties/{id}/legal-hold`.
- Policy API: `GET /v1/policy/packs` (catalogue), `GET /v1/policy/effective?on=&product=` (staff; includes `snapshot_id`), `POST /v1/policy/assignments` (ADMIN), `GET /v1/policy/assignments`, `POST /v1/policy/overrides` (ADMIN; tighten-only), `GET /v1/policy/overrides`, `GET /v1/units/{code}?on=` (staff), `GET /v1/calendar/due?rule=&anchor=` (staff; evaluation helper), `PUT /v1/tenants/me/regulatory-profile` (ADMIN), `POST/GET /v1/tenants/me/branches`.

## 10. CLI

`packs load [--allow-unverified] [path]`, `packs validate <path>`, `packs show <code> --version`, `units seed`, `units fetch --unit <code> [--from --to]`, `holidays seed`, `party purge-due --tenant <slug> [--dry-run]`.

## 11. Console increments

Pack viewer (effective policy for a date and product with provenance shown per value; assignments and overrides with approver and dates; snapshot hash), Units page (table per unit with corrections and sources; fetch status), Calendar page (holiday list by scope; deadline evaluator), Connectors page (mode, route, circuit state, usage chart), Party pages (search by identifier, list, detail with explicit "Revelar" buttons that call the reveal endpoints and show the `VIEW_PII` notice, create/edit person and organization, identifiers, contacts, addresses, relationships graph (table in 2c; graph in SP-09), documents upload/list/download, customer relationships, legal hold). Spanish-first, keyed strings.

## 12. Test matrix and exit criteria

| Suite | Proves |
|---|---|
| `tests/golden/packs` | both packs load; effective policy per date/product matches fixtures; overrides tighten-only; snapshot hash stable |
| `tests/platform/test_units.py` + goldens | rules resolve the right row across weekends and holidays; missing value blocks; corrections supersede; Banxico demo and live (respx) fetchers |
| `tests/platform/test_calendar.py` + goldens | business-day helpers and every deadline kind, 2025–2027 |
| `tests/platform/test_connectors.py` | retries, timeout, circuit breaker, vault, evidence stored and credential-redacted, metering rows, router fallback, sandbox forced for SANDBOX tenants |
| `tests/modules/party/*` | validators corpus; create/search/dedup via blind index; RLS harness for every new table; reveal audits `VIEW_PII`; masked machine view; documents in the object store with sha256; retention sweep honours legal hold; events published |
| console | typecheck/lint/unit tests; Playwright: pack viewer shows the ITF tier limits; a party is created and its RFC revealed with an audit row |

Exit criteria: `make ci` green; `xolotl packs load` installs both packs and `GET /v1/policy/effective` returns the ITF N1 limit as 750 UDI converted with the UDI of the last day of the previous month; a USD 5 000 threshold converts with the FIX of the prior banking day across a holiday; `due_at` for the quarterly ROR gives the tenth business day; a party with a valid CURP and RFC is created, found by RFC without decryption, revealed by an analyst with a `VIEW_PII` audit row, and invisible to another tenant; a SANDBOX tenant's SAT connector call returns a mock and a metering row; retention purges a closed relationship's party after its period unless on legal hold.

## 13. Open items for the compliance review (loader-blocked as `UNVERIFIED`)

ROR period granularity (month vs quarter) and Formato 41 ASCII encoding (owned by SP-05 but referenced by captures); the exact UMA 2026 value and DOF citation; LFPIORPI fee threshold wording (4 UMA) and the one-aviso-under-A rule's legal basis; INE CIC/OCR format authority; retention for avisos (5 years) vs KYC (10 years) when both packs apply to one tenant.
