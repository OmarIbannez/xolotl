# 00 — Platform foundations
Source: https://app.notion.com/3fde5630e5e3811db68beb230d57a476

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e38175aa91cf38caaaf97e"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e3815191c9ddd6987d3598"/>
---
## Executive summary
Every feature (onboarding, screening, monitoring, reporting…) is built on the same **six shared building blocks**. Building them once, first, is what lets us add a new country by writing configuration instead of new software:
1. **Tenant context.** Every request knows which client it belongs to, and the database enforces it.
2. **Country rulebooks ("policy packs").** One versioned file per country and industry holds thresholds, required checks, report formats and deadlines.
3. **Money-unit service.** Converts amounts into local legal units (Mexico's UMA/UDI, Argentina's SMVM, Chile's UF, …) using the value valid on the right date.
4. **Regulatory calendar.** Knows every country's business days and turns rules like "first 10 business days of the quarter" into exact dates.
5. **Event backbone.** Every important action is published as an event, so other features react without being tightly wired together.
6. **Common API rules.** Same authentication, error format, idempotency and webhooks everywhere.
**Investment:** about 6–8 engineer-weeks before the first feature. It pays back with every country added.
---
## 1. Service layout
```javascript
repo: kyc-platform (monorepo, Python 3.12, uv workspace)
├── libs/
│   ├── platform_core/        # tenant context, db session, errors, ids, clock
│   ├── policy/               # policy-pack models (Pydantic) + resolver
│   ├── units/                # monetary units & FX
│   ├── calendar/             # business days & deadline rules
│   ├── events/               # event envelope, Kafka producer/consumer, outbox
│   ├── crypto/               # envelope encryption, blind indexes
│   └── connectors_sdk/       # base class for external connectors (retry, metering)
├── services/
│   ├── api_gateway/          # FastAPI public API (thin; routes to services)
│   ├── onboarding/           # 01 customer onboarding
│   ├── kyb/                  # 02 business onboarding
│   ├── screening/            # 03 screening
│   ├── risk/                 # 04 risk scoring
│   ├── monitoring/           # 05 transaction monitoring (API + Flink jobs)
│   ├── cases/                # 06 case management
│   ├── reporting/            # 07 regulatory reporting
│   ├── obligations/          # 08 compliance calendar
│   └── connectors/           # RENAPO, INE, Serpro, … adapters
└── policy-packs/             # versioned YAML rulebooks (reviewed like code)
```
- **Deployment:** each service is a container on Kubernetes (EKS), with a separate Temporal worker deployment where it needs workflows.
- **Start as a "modular monolith".** `api_gateway` + `onboarding` + `kyb` + `screening` + `risk` + `cases` + `reporting` + `obligations` share one deployable and one database at T1, with clear module boundaries. Split out only `monitoring` (streaming) and `screening-matcher` (memory-heavy) from day one. Split further only when load or team size demands it.
---
## 2. Tenant context
### 2.1 Request flow
```javascript
client ──(mTLS or OAuth2 client-credentials JWT)──► API gateway
   JWT claims: tenant_id, client_id, scopes, env (sandbox|prod)
         │
         ▼
  middleware: TenantContext(tenant_id, actor, request_id) stored in a contextvar
         │
         ▼
  db session: BEGIN; SET LOCAL app.tenant_id = '<uuid>'; SET LOCAL app.actor = '<id>'; ...
```
### 2.2 Database enforcement
- Every tenant table has `tenant_id uuid NOT NULL`, `ENABLE` + `FORCE ROW LEVEL SECURITY`, and the policy `tenant_id = current_setting('app.tenant_id')::uuid`.
- The application role is **not** the table owner and has **no BYPASSRLS**. Migrations run as a separate owner role.
- Composite foreign keys `(tenant_id, id)` everywhere, so a row can never reference another tenant's row:
	```sql
ALTER TABLE party ADD CONSTRAINT party_tenant_id_uk UNIQUE (tenant_id, id);
ALTER TABLE party_identifier
  ADD CONSTRAINT party_identifier_party_fk
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id);
	```
- **Test guardrail:** a CI test creates two tenants, writes data as tenant A, and asserts that every repository method returns nothing as tenant B. It runs for every new table, discovered automatically via `information_schema`.
### 2.3 IDs
- **UUIDv7** (time-ordered) for all primary keys. That gives good index locality and is sortable by creation.
- **External references** from clients (`external_ref`) are unique per tenant and are the idempotency anchors for clients.
---
## 3. Encryption & sensitive data
<table header-row="true">
<tr>
<td>Data class</td>
<td>Examples</td>
<td>Protection</td>
</tr>
<tr>
<td>**P1 – identifiers**</td>
<td>CURP, RFC, CPF, CUIT, RUT, account numbers, wallet addresses</td>
<td>Field-level AES-256-GCM with the tenant's data encryption key (DEK), plus **blind index** = HMAC-SHA256(tenant index key, normalized value) for equality lookups</td>
</tr>
<tr>
<td>**P2 – personal attributes**</td>
<td>names, birth date, address, phone, email</td>
<td>Field-level encryption. Normalized name tokens are stored separately for matching (see <mention-page url="https://app.notion.com/p/3fde5630e5e3814da2f5ce4e03addb79"/> §5)</td>
</tr>
<tr>
<td>**P3 – biometrics**</td>
<td>selfies, face templates, liveness videos</td>
<td>Object storage with a **separate biometric DEK**, short retention after verification (configurable per country), access limited to the onboarding service role</td>
</tr>
<tr>
<td>**P4 – documents**</td>
<td>ID images, deeds, proof of address</td>
<td>Object storage, SSE-KMS with the tenant key, SHA-256 recorded</td>
</tr>
<tr>
<td>**Non-sensitive**</td>
<td>statuses, scores, codes</td>
<td>Plain columns</td>
</tr>
</table>
**Key hierarchy:**
- Cell KMS key → per-tenant key-encryption key (KEK) in KMS → per-tenant DEK, stored wrapped in `tenant_key`, unwrapped in memory and cached 5 minutes.
- **Crypto-shredding:** destroy the tenant's KEK, or a specific DEK version.
```python
# libs/crypto/fields.py
class EncryptedStr(TypeDecorator):  # SQLAlchemy type
    impl = LargeBinary
    def process_bind_param(self, value, dialect):
        return None if value is None else tenant_aead().encrypt(value.encode(), aad=current_tenant_id().bytes)
    def process_result_value(self, value, dialect):
        return None if value is None else tenant_aead().decrypt(value, aad=current_tenant_id().bytes).decode()

def blind_index(kind: str, value: str) -> bytes:
    return hmac.new(tenant_index_key(), f"{kind}:{normalize_identifier(kind, value)}".encode(), "sha256").digest()
```
---
## 4. Policy packs (country rulebooks)
### 4.1 Concept
A **policy pack** is a versioned YAML document per *country × sector × licence*, validated by Pydantic. A tenant is assigned one or more packs (e.g. `MX/ITF-IFPE` + `MX/LFPIORPI-AVI` for an e-money institution that also offers crypto). The **resolver** merges them with the tenant's overrides into an **effective policy** for a given date.
### 4.2 Example (abridged)
```yaml
# policy-packs/MX/ITF-IFPE/v3.yaml
pack: MX/ITF-IFPE
version: 3
effective_from: 2026-11-30
country: MX
regulators: [CNBV, UIF]
units: { base_currency: MXN, threshold_units: [UDI, USD, UMA] }

kyc:
  tiers:
    - code: N1
      limits: { monthly_ops: {amount: 750, unit: UDI}, max_balance: {amount: 1000, unit: UDI} }
      required_fields: [given_names, first_surname, birth_date, gender, birth_state, occupation, email]
      required_checks: [liveness_passive]
    - code: N2
      limits: { monthly_ops: {amount: 3000, unit: UDI} }
      required_fields: [..., address, id_document]
      required_checks: [doc_auth, liveness_passive, face_match_doc, registry_curp]
    - code: FULL
      required_fields: [..., curp, rfc, clabe_own_name, digital_signature, geolocation]
      required_checks: [doc_auth, liveness_active_or_passive, face_match_registry_ine, registry_curp, registry_ine, sat_rfc]
  review_frequency: { LOW: P1Y, MEDIUM: P1Y, HIGH: P1Y }
  geolocation_required: true

ubo:
  ownership_threshold_pct: 25
  control_tests: [VOTING, BOARD_APPOINTMENT, CONTRACT]
  fallback: SENIOR_MANAGING_OFFICIAL
  legal_person_can_be_ubo: false

screening:
  lists: [UN_CONSOLIDATED, MX_LPB, OFAC_SDN, MX_SAT_69B, PEP_MX]
  pep: { cooling_off: P1Y, relatives_degree: 2 }
  on_true_match:
    MX_LPB: { action: SUSPEND_IMMEDIATELY, report: MX_ROI_24H, notify_client: true }
    UN_CONSOLIDATED: { action: SUSPEND_IMMEDIATELY, report: MX_ROI_24H }

reports:
  - MX_FORMATO41_ROR@2004.5
  - MX_FORMATO41_ROI@2004.5
  - MX_ITF_FX_CASH@1
  - MX_ITF_INTL_TRANSFERS@1
retention: { kyc_after_relationship: P10Y, transactions: P10Y }
```
### 4.3 Pydantic model & the "only tighten" rule
```python
class UboPolicy(BaseModel):
    ownership_threshold_pct: Annotated[Decimal, Field(gt=0, le=50)]
    control_tests: list[ControlTest]
    fallback: Literal["SENIOR_MANAGING_OFFICIAL"]
    legal_person_can_be_ubo: bool = False

class TenantOverride(BaseModel):
    ubo: UboOverride | None = None
    ...
    def validate_against(self, base: EffectivePolicy) -> None:
        if self.ubo and self.ubo.ownership_threshold_pct > base.ubo.ownership_threshold_pct:
            raise PolicyLooseningError("UBO threshold may only be lowered")
        # likewise: review periods may only shorten, retention only lengthen,
        # lists may only be added, limits only reduced
```
### 4.4 Resolution
```python
def effective_policy(tenant_id: UUID, on: date, product: str | None = None) -> EffectivePolicy:
    packs = tenant_pack_assignments(tenant_id, on, product)    # versions valid on `on`
    merged = merge_strictest(packs)                            # min thresholds, union lists, max retention
    return apply_overrides(merged, tenant_overrides(tenant_id, on))
```
- **Every decision record stores ****`policy_version_id`**, the hash of the effective policy, persisted in `policy_snapshot`, so decisions can be reproduced exactly.
- **Packs are code-reviewed.** Changes need approval from a product owner and a regulatory analyst, and every pack version has golden tests (fixtures → expected decisions).
```sql
CREATE TABLE policy_snapshot (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    content_hash  bytea NOT NULL,
    content       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, content_hash)
);
```
---
## 5. Monetary unit & FX service
**Responsibilities:**
- Convert `(amount, currency, on_date)` → a target unit, using **unit-specific rules**.
- Keep **effective-dated** values with their source and retrieval timestamp. Values are never overwritten; corrections are new rows.
<table header-row="true">
<tr>
<td>Unit</td>
<td>Source</td>
<td>Rule</td>
</tr>
<tr>
<td>MX **UMA**</td>
<td>INEGI (DOF)</td>
<td>Annual, valid 1 Feb → 31 Jan</td>
</tr>
<tr>
<td>MX **UDI**</td>
<td>Banxico SIE API</td>
<td>Daily</td>
</tr>
<tr>
<td>MX USD</td>
<td>Banxico **FIX**</td>
<td>FIX of the **prior banking day** (fintech rules)</td>
</tr>
<tr>
<td>BR USD</td>
<td>BCB **PTAX**</td>
<td>Daily</td>
</tr>
<tr>
<td>CO **TRM**</td>
<td>Banco de la República</td>
<td>Rate **of the transaction day**</td>
</tr>
<tr>
<td>CO **UVB / UVR / SMMLV**</td>
<td>DIAN / BanRep / decree</td>
<td>Annual / daily / annual</td>
</tr>
<tr>
<td>AR **SMVM**</td>
<td>CNEPySMVM resolutions</td>
<td>**Snapshot rule:** the value at 31-Dec applies to Jan–Jun, the value at 30-Jun to Jul–Dec (Res. UIF 84/2023)</td>
</tr>
<tr>
<td>CL **UF**, **dólar observado**</td>
<td>Banco Central de Chile API</td>
<td>Daily; USD of the **operation day**</td>
</tr>
<tr>
<td>PE **UIT**</td>
<td>MEF (annual)</td>
<td>Annual</td>
</tr>
<tr>
<td>PE USD</td>
<td>**SBS**</td>
<td>**Average of the previous month's daily selling rates**</td>
</tr>
</table>
```sql
CREATE TABLE unit_value (
    unit_code     text NOT NULL,            -- UMA, UDI, USD_MXN_FIX, USD_BRL_PTAX, TRM, UVB, SMVM, UF, USD_CLP_OBS, UIT, USD_PEN_SBS_AVG
    valid_from    date NOT NULL,
    valid_to      date,                     -- null = open
    value         numeric(20,8) NOT NULL,
    quote_ccy     char(3) NOT NULL,         -- currency the value is expressed in
    source        text NOT NULL,
    fetched_at    timestamptz NOT NULL,
    superseded_by bigint,                   -- correction chain
    id            bigserial PRIMARY KEY
);
CREATE INDEX ON unit_value (unit_code, valid_from DESC);
```
```python
class UnitService:
    def to_unit(self, amount: Money, unit: str, on: date, rule: ConversionRule) -> Decimal: ...
    def threshold_in_ccy(self, threshold: Quantity, ccy: str, on: date, rule: ConversionRule) -> Money: ...
# ConversionRule examples: SAME_DAY, PRIOR_BANKING_DAY, PREV_MONTH_AVG, SEMIANNUAL_SNAPSHOT
```
**Fetchers:**
- A scheduled Temporal workflow per source runs daily at 18:00 local time.
- If a source fails, it raises an alert and the **previous value is never silently reused** for a new date. Lookups for a missing date raise `UnitValueMissing`, which blocks threshold decisions and pages on-call.
---
## 6. Regulatory calendar
**Purpose:** turn legal deadline phrases into exact timestamps, per country and per regulator.
**Data:**
- `holiday(country, regulator_scope, date, name, source)`, seeded yearly from official decrees.
- Regulator-specific closures, e.g. the UIAF and UAF annual calendars.
**Deadline rule DSL** (stored in report and obligation definitions):
<table header-row="true">
<tr>
<td>Rule</td>
<td>Example</td>
</tr>
<tr>
<td>`BUSINESS_DAYS_AFTER(event, n)`</td>
<td>MX unusual-op report: 3 business days after the committee decision</td>
</tr>
<tr>
<td>`NTH_BUSINESS_DAY_OF(period_end + 1 month, n)`</td>
<td>MX quarterly report: first 10 business days of Jan/Apr/Jul/Oct</td>
</tr>
<tr>
<td>`DAY_OF_NEXT_MONTH(17, roll=NEXT_BUSINESS_DAY)`</td>
<td>MX non-financial avisos</td>
</tr>
<tr>
<td>`CALENDAR_DAYS_AFTER(period_end, 20)`</td>
<td>CO monthly reports</td>
</tr>
<tr>
<td>`HOURS_AFTER(event, 24)`</td>
<td>24-hour reports (calendar hours, not business)</td>
</tr>
<tr>
<td>`DATE_RANGE(Jan 2, Mar 15)`</td>
<td>AR annual systematic report</td>
</tr>
<tr>
<td>`FIXED(date)`</td>
<td>One-off transitional deadlines (e.g. BR Res. 591 backlog: first business day of Jan 2027)</td>
</tr>
</table>
```python
class DeadlineRule(BaseModel):
    kind: Literal["BUSINESS_DAYS_AFTER","NTH_BUSINESS_DAY","DAY_OF_NEXT_MONTH","CALENDAR_DAYS_AFTER","HOURS_AFTER","DATE_RANGE","FIXED"]
    params: dict
    tz: str  # e.g. "America/Mexico_City"
def due_at(rule: DeadlineRule, anchor: datetime, country: str, scope: str) -> datetime: ...
```
Golden tests per country cover several years, including holiday edge cases.
---
## 7. Events & integration
### 7.1 Transactional outbox
Services never publish to Kafka directly inside business logic:
1. In the same database transaction as the state change, they insert into `outbox`.
2. A relay (Debezium CDC, or a poller at T1) publishes the row to Kafka.
This guarantees **no lost or phantom events**.
```sql
CREATE TABLE outbox (
    id           uuid PRIMARY KEY,         -- = event id
    tenant_id    uuid NOT NULL,
    topic        text NOT NULL,
    key          text NOT NULL,            -- partition key, e.g. tenant_id:party_id
    type         text NOT NULL,            -- e.g. onboarding.application.decided.v1
    payload      jsonb NOT NULL,           -- no plaintext P1/P2 data; references only
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
```
### 7.2 Event envelope (CloudEvents-style)
```json
{
  "id": "0192f1c2-…", "type": "screening.match.created.v1", "source": "screening",
  "tenant_id": "…", "time": "2026-10-17T18:02:11Z", "subject": "party/0192…",
  "policy_version_id": "…", "correlation_id": "…", "data": { "...": "..." }
}
```
### 7.3 Core topics
<table header-row="true">
<tr>
<td>Topic</td>
<td>Producers → consumers</td>
</tr>
<tr>
<td>`party.events`</td>
<td>onboarding/kyb → screening (screen new party), risk (score)</td>
</tr>
<tr>
<td>`screening.events`</td>
<td>screening → risk, cases, onboarding (gate)</td>
</tr>
<tr>
<td>`risk.events`</td>
<td>risk → onboarding (decision), monitoring (profile refresh), obligations (review dates)</td>
</tr>
<tr>
<td>`txn.raw` / `txn.enriched`</td>
<td>ingest → monitoring (Flink) → ClickHouse/lake</td>
</tr>
<tr>
<td>`alerts.events`</td>
<td>monitoring/screening → cases</td>
</tr>
<tr>
<td>`case.events`</td>
<td>cases → reporting (STR drafts), audit</td>
</tr>
<tr>
<td>`report.events`</td>
<td>reporting → obligations (mark done), webhooks</td>
</tr>
<tr>
<td>`lists.events`</td>
<td>list ingestion → screening (delta rescreen)</td>
</tr>
<tr>
<td>`audit.events`</td>
<td>all → audit sink (WORM)</td>
</tr>
</table>
**Webhooks to clients** are produced by a webhook service consuming the topics:
- Signed with HMAC-SHA256 (`X-Signature`, `X-Timestamp`).
- Retried with exponential backoff for 24 h; failed deliveries go to a dead-letter queue that is visible in the console.
---
## 8. API conventions
<table header-row="true">
<tr>
<td>Topic</td>
<td>Convention</td>
</tr>
<tr>
<td>Style</td>
<td>REST/JSON, versioned path `/v1/...`, OpenAPI 3.1 generated from FastAPI + Pydantic</td>
</tr>
<tr>
<td>Auth</td>
<td>OAuth2 client credentials (JWT, 15 min) or mTLS for banks. Scopes per feature (`onboarding:write`, `reports:read`…)</td>
</tr>
<tr>
<td>Idempotency</td>
<td>`Idempotency-Key` header required on POST. Stored 24 h in Redis + Postgres (`idempotency_key` table) with the response hash</td>
</tr>
<tr>
<td>Errors</td>
<td>RFC 9457 `application/problem+json` with stable `code` (e.g. `POLICY_REQUIRED_FIELD_MISSING`)</td>
</tr>
<tr>
<td>Pagination</td>
<td>Cursor-based (`?cursor=`), max 200</td>
</tr>
<tr>
<td>Async</td>
<td>Long operations return `202`  • resource with `status`  • webhook. Polling allowed</td>
</tr>
<tr>
<td>Rate limits</td>
<td>Per tenant per scope (token bucket in Redis). `429` with `Retry-After`</td>
</tr>
<tr>
<td>Sandbox</td>
<td>Same API at `sandbox.` host. Connectors return deterministic results for magic test identities</td>
</tr>
<tr>
<td>PII in logs</td>
<td>Forbidden. Structured logging with a redaction filter. Test asserts no P1/P2 fields reach logs</td>
</tr>
</table>
---
## 9. Connector SDK (external calls)
All registry and vendor calls go through one base class, so retries, timeouts, circuit breaking, cost metering, evidence storage and sandbox mocks are uniform:
```python
class Connector(ABC):
    name: str; check_type: CheckType; timeout_s: float = 8.0
    @abstractmethod
    async def _call(self, req: ConnectorRequest, creds: Credentials) -> RawResponse: ...
    @abstractmethod
    def _interpret(self, raw: RawResponse) -> CheckResult: ...   # PASS/FAIL/INCONCLUSIVE + details

    async def run(self, req, ctx) -> VerificationCheck:
        creds = await vault.get(ctx.tenant_id, self.name, mode=ctx.credential_mode)
        async with circuit_breaker(self.name, ctx.cell), timeout(self.timeout_s):
            raw = await retry(self._call, req, creds, attempts=3, backoff=expo(0.5))
        ref = await evidence_store.put(ctx.tenant_id, raw)           # encrypted raw response
        result = self._interpret(raw)
        await meter.record(ctx.tenant_id, self.name, cost=self.unit_cost(raw))
        return VerificationCheck.from_result(result, response_ref=ref, provider=self.name, ...)
```
**Routing:** a `ConnectorRouter` chooses primary → fallback per check type, country and tenant configuration. It also records which route was taken.
---
## 10. Observability & quality gates
- **Tracing and metrics:** OpenTelemetry traces across API → Temporal → connectors. RED metrics per endpoint and per connector.
- **SLO dashboards** per cell; business metrics per tenant (onboarding conversion, alert volumes, report timeliness).
- **CI:** `inertclaude ci` (ruff, ty, basedpyright, vulture, pytest), tenant-isolation tests, policy-pack golden tests, schema-validation tests for every report serializer.
---
## 11. Build plan
<table header-row="true">
<tr>
<td>Milestone</td>
<td>Content</td>
<td>Effort (eng-weeks)</td>
</tr>
<tr>
<td>F1</td>
<td>Repo skeleton, tenant context, RLS migration framework, isolation test harness</td>
<td>1.5</td>
</tr>
<tr>
<td>F2</td>
<td>Crypto lib (envelope encryption, blind index), KMS integration</td>
<td>1</td>
</tr>
<tr>
<td>F3</td>
<td>Policy-pack models, resolver, snapshots, MX pack v1 + golden tests</td>
<td>1.5</td>
</tr>
<tr>
<td>F4</td>
<td>Units service + fetchers (MX first), calendar engine + MX/BR holidays</td>
<td>1.5</td>
</tr>
<tr>
<td>F5</td>
<td>Outbox + Kafka + webhook service; API conventions middleware</td>
<td>1.5</td>
</tr>
<tr>
<td>F6</td>
<td>Connector SDK + sandbox mock framework</td>
<td>1</td>
</tr>
<tr>
<td>**Total**</td>
<td></td>
<td>**≈ 8**</td>
</tr>
</table>
