# 🗄️ Data model
Source: https://app.notion.com/3fde5630e5e38162885fd93c9ea7118b

**Date:** 2026-10-17
**Status:** Draft proposal (illustrative PostgreSQL DDL, not yet a migration)
**Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e38175aa91cf38caaaf97e"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e381a7a13fd6fda3ef15ad"/>
---
## 1. Core idea
- A **party** (a person or a company) belongs to **exactly one tenant**.
- Every **decision** about that party is stored as an **unchangeable record** that points to the exact policy, rule, list, and matcher versions used at the time.
- Being a *customer* is a **relationship** between a party and the tenant. A party can also be a beneficial owner, legal representative, or a PEP's relative without being a customer.
## 2. Domains at a glance
```javascript
SHARED (no tenant_id)              TENANT-SCOPED (tenant_id on every row, enforced by RLS)
─────────────────────              ─────────────────────────────────────────────────────
policy_pack ─┐                     tenant ── tenant_policy (pack version + overrides)
policy_pack_version                   │      staff_user
monetary_unit (UMA/SMVM/UF…)          ▼
list_source ── list_version        party ──┬── person / organization
  └── list_entry (OFAC, UN, PEP…)    │     ├── party_identifier (CURP, RFC, CPF, CUIT…)
report_schema                        │     ├── party_address
                                     │     ├── party_relationship (UBO, legal rep, PEP relative)
                                     │     └── ubo_determination
                                     │
                                     ├── customer_relationship (status, tier, risk)
                                     │     ├── onboarding_application ── verification_check
                                     │     ├── document
                                     │     └── risk_assessment
                                     │
                                     ├── screening_run ── screening_match ── match_disposition
                                     │                    screening_whitelist
                                     ├── private_list ── private_list_entry  (e.g. MX LPB)
                                     │
                                     ├── account ── transaction ── counterparty
                                     │
                                     ├── alert ──► aml_case ── case_party / case_event
                                     │                 └── regulatory_report ── report_item / report_filing
                                     │
                                     └── audit_event (append-only, everything)
```
The DDL below is ordered so that referenced tables come first.
---
## 3. Shared reference data (no `tenant_id`)
```sql
-- Policy packs: versioned configuration per country × sector
CREATE TABLE policy_pack (
    id    uuid PRIMARY KEY,
    code  text UNIQUE NOT NULL           -- 'MX/ITF', 'MX/LFPIORPI-REAL-ESTATE', 'BR/IP-PIX', 'CO/SARLAFT'…
);

CREATE TABLE policy_pack_version (
    id              uuid PRIMARY KEY,
    pack_id         uuid NOT NULL REFERENCES policy_pack(id),
    version         int  NOT NULL,
    effective_from  date NOT NULL,
    config          jsonb NOT NULL,      -- validated by a Pydantic model: UBO threshold, required checks per tier,
                                         -- lists, matcher strictness, EDD triggers, report thresholds, retention…
    UNIQUE (pack_id, version)
);

-- Local monetary units used for thresholds (updated yearly; UF daily)
CREATE TABLE monetary_unit (
    code        text NOT NULL,           -- UMA (MX), SMVM (AR), SMMLV (CO), UF (CL), UIT (PE)
    valid_from  date NOT NULL,
    valid_to    date,
    value       numeric NOT NULL,
    currency    char(3) NOT NULL,
    PRIMARY KEY (code, valid_from)
);

-- Sanctions / PEP / adverse-media / fiscal lists
CREATE TABLE list_source (
    id         uuid PRIMARY KEY,
    code       text UNIQUE NOT NULL,     -- OFAC_SDN, UN_CSNU, EU, UK_HMT, MX_SAT_69B, BR_CEIS, AR_REPET, PEP_MX…
    list_kind  text NOT NULL             -- SANCTION, PEP, ADVERSE_MEDIA, FISCAL, ENFORCEMENT
);

CREATE TABLE list_version (
    id            uuid PRIMARY KEY,
    source_id     uuid NOT NULL REFERENCES list_source(id),
    published_at  timestamptz,
    ingested_at   timestamptz NOT NULL,
    checksum      bytea NOT NULL
);

CREATE TABLE list_entry (
    id            uuid PRIMARY KEY,
    version_id    uuid NOT NULL REFERENCES list_version(id),
    entity_type   text NOT NULL,         -- PERSON, ORGANIZATION, VESSEL, WALLET…
    names         jsonb NOT NULL,        -- primary + aliases, already normalized
    identifiers   jsonb,
    birth_dates   jsonb,
    countries     char(2)[],
    pep_position  jsonb,
    content_hash  bytea NOT NULL,        -- used by the whitelist to detect changed entries
    raw           jsonb
);

-- Regulatory report formats (change over time)
CREATE TABLE report_schema (
    id            uuid PRIMARY KEY,
    jurisdiction  char(2) NOT NULL,
    report_type   text NOT NULL,
    version       text NOT NULL,
    valid_from    date NOT NULL,
    definition    jsonb NOT NULL,        -- field mapping, XSD/JSON-schema reference, file naming rules
    UNIQUE (jurisdiction, report_type, version)
);
```
---
## 4. Tenancy, configuration, staff
```sql
CREATE TABLE tenant (
    id            uuid PRIMARY KEY,
    legal_name    text NOT NULL,
    country_code  char(2) NOT NULL,
    cell          text NOT NULL,          -- 'mx-1', 'br-1', 'dedicated-acme'
    status        text NOT NULL,          -- ONBOARDING, ACTIVE, SUSPENDED, OFFBOARDED
    kms_key_ref   text NOT NULL,          -- per-tenant key-encryption key
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tenant_policy (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL REFERENCES tenant(id),
    pack_version_id  uuid NOT NULL REFERENCES policy_pack_version(id),
    overrides        jsonb NOT NULL DEFAULT '{}',   -- validated: may only TIGHTEN legal minimums
    effective_from   timestamptz NOT NULL,
    approved_by      uuid NOT NULL
);

CREATE TABLE staff_user (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenant(id),
    sso_subject  text NOT NULL,
    email        text NOT NULL,
    roles        text[] NOT NULL,         -- ANALYST, SENIOR_ANALYST, MLRO, AUDITOR_RO, ADMIN
    active       boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, sso_subject)
);
```
---
## 5. Party (the core)
```sql
CREATE TABLE party (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL REFERENCES tenant(id),
    party_type       text NOT NULL CHECK (party_type IN ('person','organization')),
    country_code     char(2) NOT NULL,          -- primary jurisdiction
    created_at       timestamptz NOT NULL DEFAULT now(),
    retention_until  date,                      -- computed from policy pack (MX/BR/CO: +10y after relationship ends)
    legal_hold       boolean NOT NULL DEFAULT false
);

CREATE TABLE person (
    party_id         uuid PRIMARY KEY REFERENCES party(id),
    tenant_id        uuid NOT NULL,
    given_names      bytea NOT NULL,            -- encrypted with tenant key
    first_surname    bytea NOT NULL,            -- apellido paterno / sobrenome
    second_surname   bytea,                     -- apellido materno (MX/CO/PE…); often null in BR
    full_name_norm   text NOT NULL,             -- normalized for matching (see §9 rule 4)
    birth_date       bytea,                     -- encrypted
    birth_country    char(2),
    nationalities    char(2)[],
    gender           text,
    occupation       text,
    is_pep           boolean NOT NULL DEFAULT false   -- denormalized; derived from screening
);

CREATE TABLE organization (
    party_id            uuid PRIMARY KEY REFERENCES party(id),
    tenant_id           uuid NOT NULL,
    legal_name          text NOT NULL,
    trade_name          text,
    legal_form          text,                   -- SA de CV, Ltda, SAS, SRL, SpA…
    incorporation_date  date,
    industry_code       text,                   -- SCIAN (MX), CNAE (BR), CIIU (CO/PE)
    registry_ref        text                    -- folio mercantil, NIRE…
);

CREATE TABLE party_address (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    party_id      uuid NOT NULL REFERENCES party(id),
    address_type  text NOT NULL,                -- RESIDENTIAL, FISCAL, OPERATIONAL
    address_enc   bytea NOT NULL,               -- full structured address, encrypted
    country_code  char(2) NOT NULL,
    admin_area    text,                         -- estado / UF / provincia / departamento (for geo risk)
    municipality  text,                         -- municipio (for municipality-level risk)
    valid_from    date NOT NULL,
    valid_to      date
);
```
### Document and verification check
A `verification_check` is stored once per external call and never edited. Its raw response is kept as evidence.
```sql
CREATE TABLE document (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    party_id     uuid NOT NULL REFERENCES party(id),
    doc_type     text NOT NULL,     -- INE_FRONT, INE_BACK, CNH, PASSPORT, SELFIE, PROOF_OF_ADDRESS,
                                    -- ACTA_CONSTITUTIVA, CONTRATO_SOCIAL, PODER…
    storage_key  text NOT NULL,     -- tenant-prefixed object-store path
    sha256       bytea NOT NULL,    -- tamper evidence
    extracted    jsonb,             -- OCR output (encrypted fields where PII)
    uploaded_at  timestamptz NOT NULL
);
```
`verification_check` references `onboarding_application`, so it is defined in §6. `party_identifier.verified_by` is added as a foreign key after both exist.
### Identifiers
One person usually has several identifiers (MX: CURP + RFC + INE; BR: CPF + RG).
```sql
CREATE TABLE party_identifier (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    party_id      uuid NOT NULL REFERENCES party(id),
    id_type       text NOT NULL,    -- CURP, RFC, INE_CIC, CPF, CNPJ, RG, CUIT, CUIL, DNI_AR, RUT, NIT,
                                    -- CC_CO, DNI_PE, RUC, PASSPORT
    country_code  char(2) NOT NULL,
    value_enc     bytea NOT NULL,   -- encrypted
    value_hash    bytea NOT NULL,   -- HMAC(tenant_key, normalized value): exact lookup & dedup without decrypting
    verified_by   uuid,             -- FK → verification_check(id), added below
    UNIQUE (tenant_id, id_type, value_hash)
);
```
### Ownership and control graph (beneficial owners)
```sql
CREATE TABLE party_relationship (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    from_party_id  uuid NOT NULL REFERENCES party(id),   -- owner / controller / relative
    to_party_id    uuid NOT NULL REFERENCES party(id),   -- company / trust / the PEP
    rel_type       text NOT NULL,   -- SHAREHOLDER, CONTROLLER, LEGAL_REP, ATTORNEY, DIRECTOR,
                                    -- TRUST_SETTLOR, TRUSTEE, TRUST_BENEFICIARY, PEP_RELATIVE, PEP_ASSOCIATE
    ownership_pct  numeric(7,4),    -- direct %, SHAREHOLDER only
    control_basis  text,            -- VOTING, BOARD_APPOINTMENT, CONTRACT, OTHER (MX "control" test)
    valid_from     date NOT NULL,
    valid_to       date,
    source_doc_id  uuid REFERENCES document(id)
);

-- Snapshot of computed effective ownership, with the threshold that applied
CREATE TABLE ubo_determination (
    id                 uuid PRIMARY KEY,
    tenant_id          uuid NOT NULL,
    organization_id    uuid NOT NULL REFERENCES party(id),
    policy_version_id  uuid NOT NULL REFERENCES policy_pack_version(id),   -- 25% MX AML vs 15% tax, etc.
    computed_at        timestamptz NOT NULL,
    result             jsonb NOT NULL   -- [{person_id, effective_pct, basis: OWNERSHIP|CONTROL|SENIOR_MANAGER_FALLBACK}]
);
```
**Effective (indirect) ownership** is calculated by walking the graph with a recursive query that multiplies percentages along each chain:
```sql
WITH RECURSIVE chain AS (
    SELECT from_party_id AS owner, to_party_id AS target, ownership_pct / 100 AS eff, 1 AS depth
    FROM party_relationship
    WHERE to_party_id = :org_id AND rel_type = 'SHAREHOLDER' AND valid_to IS NULL
  UNION ALL
    SELECT r.from_party_id, c.target, c.eff * r.ownership_pct / 100, c.depth + 1
    FROM party_relationship r
    JOIN chain c ON r.to_party_id = c.owner
    WHERE r.rel_type = 'SHAREHOLDER' AND r.valid_to IS NULL AND c.depth < 10
)
SELECT owner, SUM(eff) AS effective_ownership
FROM chain
JOIN party p ON p.id = chain.owner AND p.party_type = 'person'   -- a legal person can't be the final UBO
GROUP BY owner;
```
Then apply the policy pack:
1. Persons at or above the ownership threshold are beneficial owners.
2. Add anyone with a `CONTROLLER` relationship.
3. If nobody qualifies, fall back to senior management.
---
## 6. Customer relationship, onboarding, risk
```sql
CREATE TABLE customer_relationship (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    party_id         uuid NOT NULL REFERENCES party(id),
    external_ref     text NOT NULL,           -- fintech's own customer ID
    status           text NOT NULL,           -- PENDING, ACTIVE, RESTRICTED, BLOCKED, EXITED
    account_tier     text,                    -- MX Nivel 1–4, simplified due diligence, etc.
    current_risk_id  uuid,                    -- → risk_assessment (FK added below)
    next_review_at   date,                    -- periodic re-KYC, driven by risk
    started_at       timestamptz,
    ended_at         timestamptz,
    UNIQUE (tenant_id, external_ref)
);

CREATE TABLE onboarding_application (
    id                  uuid PRIMARY KEY,
    tenant_id           uuid NOT NULL,
    relationship_id     uuid NOT NULL REFERENCES customer_relationship(id),
    policy_version_id   uuid NOT NULL REFERENCES policy_pack_version(id),  -- frozen at start
    workflow_id         text NOT NULL,        -- Temporal workflow ID
    status              text NOT NULL,        -- IN_PROGRESS, MANUAL_REVIEW, APPROVED, REJECTED, ABANDONED
    channel             text,                 -- SDK_WEB, SDK_IOS, SDK_ANDROID, WHATSAPP, API, BRANCH
    geo_lat             numeric(9,6),         -- MX remote contracting requirement
    geo_lon             numeric(9,6),
    device_fingerprint  text,
    ip_address          inet,
    decided_by          text,                 -- 'auto' or staff_user id
    decided_at          timestamptz
);

CREATE TABLE verification_check (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    party_id          uuid NOT NULL REFERENCES party(id),
    application_id    uuid REFERENCES onboarding_application(id),
    check_type        text NOT NULL,   -- REGISTRY_ID (RENAPO/Serpro/RENAPER…), REGISTRY_BIOMETRIC (INE face, Datavalid),
                                       -- TAX_ID, LIVENESS, FACE_MATCH, DOC_AUTH, VIDEO_CALL, COMPANY_REGISTRY,
                                       -- ADDRESS, WALLET_OWNERSHIP
    provider          text NOT NULL,   -- renapo, ine, serpro, veridas, …
    credential_mode   text NOT NULL,   -- PLATFORM or TENANT (whose contract was used)
    result            text NOT NULL,   -- PASS, FAIL, INCONCLUSIVE, ERROR
    score             numeric,
    response_ref      text,            -- object-store key of the raw, encrypted response (evidence)
    evidence_doc_ids  uuid[],
    cost_minor        bigint,          -- per-tenant billing
    performed_at      timestamptz NOT NULL
);

ALTER TABLE party_identifier
    ADD CONSTRAINT party_identifier_verified_by_fk
    FOREIGN KEY (verified_by) REFERENCES verification_check(id);

CREATE TABLE risk_assessment (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    relationship_id   uuid NOT NULL REFERENCES customer_relationship(id),
    model_version_id  uuid NOT NULL,      -- scoring model / matrix version
    score             numeric NOT NULL,
    rating            text NOT NULL,      -- LOW, MEDIUM, HIGH, PROHIBITED
    factors           jsonb NOT NULL,     -- [{factor:'geography', value:'Tamaulipas', points:15}, …] → explainability
    trigger           text NOT NULL,      -- ONBOARDING, PERIODIC, SCREENING_HIT, TXN_BEHAVIOUR, MANUAL
    edd_required      boolean NOT NULL,
    assessed_at       timestamptz NOT NULL
);

ALTER TABLE customer_relationship
    ADD CONSTRAINT customer_relationship_current_risk_fk
    FOREIGN KEY (current_risk_id) REFERENCES risk_assessment(id);
```
**Never update a risk assessment in place.** Insert a new row and repoint `customer_relationship.current_risk_id`, so the full history is kept.
---
## 7. Screening
```sql
-- Private, tenant-scoped lists (e.g. Mexico's confidential LPB)
CREATE TABLE private_list (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    code         text NOT NULL,          -- MX_LPB, INTERNAL_BLACKLIST, …
    list_kind    text NOT NULL,
    uploaded_at  timestamptz NOT NULL,
    uploaded_by  uuid NOT NULL,
    UNIQUE (tenant_id, code, uploaded_at)
);

CREATE TABLE private_list_entry (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    list_id        uuid NOT NULL REFERENCES private_list(id),
    entity_type    text NOT NULL,
    names_enc      bytea NOT NULL,       -- encrypted with tenant key
    identifiers    jsonb,                -- hashed identifiers for exact matching
    content_hash   bytea NOT NULL
);

CREATE TABLE screening_run (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    party_id         uuid NOT NULL REFERENCES party(id),
    trigger          text NOT NULL,      -- ONBOARDING, LIST_UPDATE, PERIODIC, TXN_COUNTERPARTY, MANUAL
    list_versions    uuid[] NOT NULL,    -- exactly which shared list versions were used
    private_lists    uuid[],             -- which private list uploads were used
    matcher_version  text NOT NULL,      -- name-matching algorithm + config version
    run_at           timestamptz NOT NULL
);

CREATE TABLE screening_match (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    run_id            uuid NOT NULL REFERENCES screening_run(id),
    list_entry_id     uuid REFERENCES list_entry(id),          -- shared entry, or…
    private_entry_id  uuid REFERENCES private_list_entry(id),  -- …private (LPB) entry
    score             numeric NOT NULL,
    match_detail      jsonb NOT NULL,    -- which fields matched and how (surname swap, accent, alias…)
    status            text NOT NULL,     -- OPEN, FALSE_POSITIVE, TRUE_MATCH, ESCALATED
    CHECK (num_nonnulls(list_entry_id, private_entry_id) = 1)
);

-- Append-only decision history
CREATE TABLE match_disposition (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    match_id    uuid NOT NULL REFERENCES screening_match(id),
    decision    text NOT NULL,
    rationale   text NOT NULL,
    decided_by  uuid NOT NULL REFERENCES staff_user(id),
    decided_at  timestamptz NOT NULL
);

-- "Good guy" table: suppress repeat false positives unless the list entry changes
CREATE TABLE screening_whitelist (
    tenant_id           uuid NOT NULL,
    party_id            uuid NOT NULL REFERENCES party(id),
    entry_content_hash  bytea NOT NULL,  -- list_entry.content_hash at the time of clearing
    disposition_id      uuid NOT NULL REFERENCES match_disposition(id),
    PRIMARY KEY (tenant_id, party_id, entry_content_hash)
);
```
---
## 8. Accounts, transactions, alerts, cases, reports
### Accounts and transactions (high volume)
```sql
CREATE TABLE account (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    relationship_id  uuid NOT NULL REFERENCES customer_relationship(id),
    external_ref     text NOT NULL,
    product_type     text NOT NULL,      -- DEBIT, WALLET, CREDIT, INVESTMENT, CRYPTO_CUSTODY
    currency         char(3) NOT NULL,
    identifier_enc   bytea,              -- CLABE / Pix key / CBU / CVU / wallet address, encrypted
    identifier_hash  bytea,              -- HMAC for lookup
    status           text NOT NULL,
    opened_at        timestamptz,
    UNIQUE (tenant_id, external_ref)
);

CREATE TABLE counterparty (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    name_norm        text,
    institution      text,               -- bank / ISPB / VASP
    identifier_enc   bytea,
    identifier_hash  bytea,
    country_code     char(2),
    party_id         uuid REFERENCES party(id),   -- set if the counterparty is also a known party
    UNIQUE (tenant_id, identifier_hash)
);

CREATE TABLE transaction (
    tenant_id           uuid NOT NULL,
    id                  uuid NOT NULL,
    occurred_at         timestamptz NOT NULL,
    account_id          uuid NOT NULL,
    direction           text NOT NULL,   -- IN, OUT
    rail                text NOT NULL,   -- SPEI, PIX, CVU, PSE, CARD, CASH, CRYPTO, FX
    amount_minor        bigint NOT NULL,
    currency            char(3) NOT NULL,
    amount_usd_minor    bigint,          -- normalized for thresholds
    counterparty_id     uuid,
    channel             text,
    geo                 jsonb,
    device_fingerprint  text,
    travel_rule         jsonb,           -- IVMS101 payload ref + status (crypto only)
    raw                 jsonb,
    PRIMARY KEY (tenant_id, occurred_at, id)
) PARTITION BY RANGE (occurred_at);      -- monthly; sub-partition by tenant for large tenants
```
**Where transaction data lives:**
- **Postgres:** the recent window, roughly 13–24 months, for investigations and lookback rules.
- **Stream processor:** the running aggregates (e.g. 7-day cash total per customer in UMA).
- **Lakehouse (Parquet/Iceberg):** full history, for backtesting and the 10-year retention.
### Alerts and cases
```sql
CREATE TABLE aml_case (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    case_type    text NOT NULL,          -- AML_INVESTIGATION, SANCTIONS_HIT, FRAUD, EDD_REVIEW, TRAVEL_RULE
    status       text NOT NULL,          -- OPEN, IN_REVIEW, PENDING_APPROVAL, CLOSED
    restricted   boolean NOT NULL DEFAULT false,   -- tipping-off: visible only to compliance roles
    assignee_id  uuid REFERENCES staff_user(id),
    opened_at    timestamptz NOT NULL,
    sla_due_at   timestamptz,            -- e.g. MX unusual-operation report deadline
    outcome      text                    -- NO_REPORT, REPORTED, EXITED_CUSTOMER, BLOCKED
);

CREATE TABLE alert (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    source           text NOT NULL,      -- TM_RULE, TM_MODEL, SCREENING, MANUAL, EMPLOYEE_REPORT
    rule_version_id  uuid,               -- exact rule/model definition that fired
    party_id         uuid REFERENCES party(id),
    transaction_ids  uuid[],
    screening_match_id uuid REFERENCES screening_match(id),
    severity         text NOT NULL,
    status           text NOT NULL,      -- NEW, IN_REVIEW, CLOSED_NO_ACTION, ESCALATED
    case_id          uuid REFERENCES aml_case(id),
    created_at       timestamptz NOT NULL
);

CREATE TABLE case_party (
    tenant_id  uuid NOT NULL,
    case_id    uuid NOT NULL REFERENCES aml_case(id),
    party_id   uuid NOT NULL REFERENCES party(id),
    role       text NOT NULL,            -- SUBJECT, COUNTERPARTY, RELATED
    PRIMARY KEY (case_id, party_id)
);

CREATE TABLE case_event (                -- notes, evidence, approvals (four-eyes), status changes
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    case_id     uuid NOT NULL REFERENCES aml_case(id),
    event_type  text NOT NULL,
    actor_id    uuid NOT NULL REFERENCES staff_user(id),
    payload     jsonb,
    at          timestamptz NOT NULL
);
```
### Regulatory reports
```sql
CREATE TABLE regulatory_report (
    id                 uuid PRIMARY KEY,
    tenant_id          uuid NOT NULL,
    jurisdiction       char(2) NOT NULL,
    report_type        text NOT NULL,   -- MX_UIF_RELEVANTE, MX_UIF_INUSUAL, MX_UIF_PREOCUPANTE, MX_LFPIORPI_AVISO,
                                        -- BR_COAF_COMUNICACAO, CO_UIAF_ROS, CO_UIAF_EFECTIVO, AR_UIF_ROS,
                                        -- CL_UAF_ROS, PE_UIF_ROS; incl. ZERO ("no operations") reports
    schema_id          uuid NOT NULL REFERENCES report_schema(id),
    period_start       date,
    period_end         date,
    case_id            uuid REFERENCES aml_case(id),
    due_at             timestamptz NOT NULL,
    status             text NOT NULL,   -- DRAFT, PENDING_APPROVAL, APPROVED, FILED, ACKNOWLEDGED, REJECTED
    payload_ref        text,            -- generated XML/JSON in object store
    approved_by        uuid REFERENCES staff_user(id)
);

CREATE TABLE report_item (
    tenant_id       uuid NOT NULL,
    report_id       uuid NOT NULL REFERENCES regulatory_report(id),
    party_id        uuid REFERENCES party(id),
    transaction_id  uuid
);

CREATE TABLE report_filing (
    id                 uuid PRIMARY KEY,
    tenant_id          uuid NOT NULL,
    report_id          uuid NOT NULL REFERENCES regulatory_report(id),
    attempted_at       timestamptz NOT NULL,
    method             text NOT NULL,   -- AUTO (tenant credentials) or MANUAL_UPLOAD
    regulator_receipt  text,
    result             text NOT NULL,   -- ACCEPTED, REJECTED, ERROR
    response_ref       text
);
```
### Audit
```sql
CREATE TABLE audit_event (             -- append-only; INSERT-only grant; streamed to WORM storage
    id           bigserial,
    tenant_id    uuid NOT NULL,
    at           timestamptz NOT NULL,
    actor_type   text NOT NULL,        -- STAFF, API_CLIENT, SYSTEM, PLATFORM_OPERATOR
    actor_id     text NOT NULL,
    action       text NOT NULL,        -- VIEW_PII, DECIDE_MATCH, APPROVE_ONBOARDING, EXPORT, CHANGE_POLICY…
    entity_type  text,
    entity_id    uuid,
    detail       jsonb,
    prev_hash    bytea,                -- hash chain → tamper evidence
    hash         bytea NOT NULL,
    PRIMARY KEY (tenant_id, at, id)
) PARTITION BY RANGE (at);
```
---
## 9. Design rules
1. **Tenant isolation is enforced by the database.** Every tenant table carries `tenant_id` with Postgres RLS:
	```sql
ALTER TABLE party ENABLE ROW LEVEL SECURITY;
ALTER TABLE party FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON party
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
	```
	- The app sets `app.tenant_id` per transaction.
	- `tenant_id` comes first in composite keys and indexes.
	- Long term, make foreign keys composite (`(tenant_id, id)`) so a reference can never cross tenants.
2. **Every decision is reproducible.** Approvals, matches, alerts, and reports store the **policy version, rule version, list versions, and matcher version** that were used. That lets you answer "why was this customer approved in 2026?" exactly.
3. **Decisions are insert-only; status changes are events.** Use dispositions, case events, new risk assessments, and filings. Only current-pointer and status columns are ever updated, and every update also writes an `audit_event`.
4. **Personal data is encrypted per field with the tenant's key, plus searchable hashes.**
	- `value_hash` columns are HMACs with a tenant-scoped key, so exact lookups never need decryption.
	- **Open decision: ****`full_name_norm`****.** Fuzzy name matching needs readable names. Options:
		- **(a)** keep it in plaintext, readable only by the matching service's database role;
		- **(b)** decrypt into an in-memory matching index per tenant.
	- Decide early; it determines which data-protection guarantees we can promise.
5. **Retention is calculated, never left to manual cleanup.** `retention_until` = relationship end date + the retention years from the policy pack. A purge job crypto-shreds or deletes expired data **unless ****`legal_hold`** is set.
6. **Configuration is validated in code.** `policy_pack_version.config` and `tenant_policy.overrides` are Pydantic models. Overrides that would **loosen** a legal minimum are rejected at write time, e.g. a beneficial-owner threshold above 25% for an MX pack, or retention below 10 years.
7. **Thresholds use local units, looked up by date.** Convert using the `monetary_unit` value that was valid **on the transaction date**, not today's value.
---
## 10. Decisions to make now
<table header-row="true">
<tr>
<td>Decision</td>
<td>Recommendation</td>
</tr>
<tr>
<td>Reuse a person across tenants?</td>
<td>**No.** Separate party per tenant. Reusable KYC comes later as a consent-based "identity wallet" service.</td>
</tr>
<tr>
<td>Transaction storage</td>
<td>Postgres (recent window) + lakehouse (history) to start; add an OLAP store only if volume requires. Depends on the expected peak transactions per second.</td>
</tr>
<tr>
<td>Graph queries (beneficial owners, mule rings)</td>
<td>Recursive SQL in Postgres first. Add a graph engine only when network analytics becomes a product feature.</td>
</tr>
<tr>
<td>Readable normalized names</td>
<td>See §9 rule 4.</td>
</tr>
<tr>
<td>Foreign keys</td>
<td>Start simple; move to composite `(tenant_id, id)` foreign keys before the first production tenant.</td>
</tr>
</table>
