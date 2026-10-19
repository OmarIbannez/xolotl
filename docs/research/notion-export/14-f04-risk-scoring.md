# 04 — Risk scoring
Source: https://app.notion.com/3fde5630e5e38138a2dfc3a914546e81

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Depends on:** <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/> · **Feeds:** onboarding decision, review scheduling, monitoring thresholds, case priority
---
## Executive summary
**What it does:** gives every customer a risk rating (Low / Medium / High / Prohibited) and explains exactly why. The rating decides:
- how much checking happens at onboarding,
- how often the customer is re-reviewed,
- how closely their payments are watched.
A second module helps each client produce the **company-wide risk assessment** that regulators require every year (Argentina, Mexico) or every two years (Brazil).
**How it works:**
- A transparent **scorecard**: points for who the customer is, where they are, what products they use, through which channel, and how they behave.
- **Hard rules** override the score. For example, a sanctions hit means *Prohibited* and a foreign politician means *High*.
- Every score is stored with its full breakdown and the exact model version, so it can be defended to a regulator years later.
**Why it matters:** "risk-based approach" is the core legal requirement in every LATAM AML law. Regulators also now ask for **evidence** that the risk model drives real decisions, which is what GAFILAT's new round of country evaluations tests.
**Build:** \~6 engineer-weeks for customer scoring; \~4 more for the enterprise-wide assessment module.
**Not in v1:** machine-learning risk models. They come later, as an *additional* signal, because regulators expect explainable scorecards.
---
## 1. Requirements
<table header-row="true">
<tr>
<td>#</td>
<td>Requirement</td>
<td>Source</td>
</tr>
<tr>
<td>R1</td>
<td>Rate every customer at onboarding and on relevant change; ratings at least LOW/MEDIUM/HIGH (+ PROHIBITED)</td>
<td>All countries (MX ITF DCG Art. 33ff; BR Circ. 3.978 Art. 20; AR Res. 14/2023; CO SARLAFT; CL Circ. 62; PE 2660-2015)</td>
</tr>
<tr>
<td>R2</td>
<td>Mandatory high-risk categories: foreign PEPs (all), domestic PEPs (most), FATF high-risk jurisdictions, non-residents (MX), entities ≥ 25% PEP-owned (PE)</td>
<td>Country policy packs</td>
</tr>
<tr>
<td>R3</td>
<td>Factors: customer type & attributes, geography, product/service, channel (non-face-to-face), transactional behaviour</td>
<td>All</td>
</tr>
<tr>
<td>R4</td>
<td>Explainability: per-factor contribution, versioned model, reproducible</td>
<td>Audit expectations</td>
</tr>
<tr>
<td>R5</td>
<td>Drives **review frequency** (AR 1/3/5 years; CO-Supersociedades 1/2 years; MX & CL annual) and **due diligence level** (simplified / standard / enhanced)</td>
<td>Policy packs</td>
</tr>
<tr>
<td>R6</td>
<td>Manual override with justification + four-eyes approval; overrides expire</td>
<td>Common practice / audit</td>
</tr>
<tr>
<td>R7</td>
<td>Enterprise-wide risk assessment (EWRA): methodology, inherent risk, controls, residual risk, approvals, versions, export. AR: filed by 30 April. BR: every 2 years. MX: ≥ 12 months of data, reviewed within 12 months. PE: every 3 years</td>
<td>AR Res. 14/2023 Arts. 5–6; BR Circ. 3.978; MX ITF Arts. 3–7; PE 789-2018</td>
</tr>
<tr>
<td>R8</td>
<td>Municipality-level geographic risk (e.g. MX border states, BR frontier)</td>
<td>Differentiator</td>
</tr>
</table>
---
## 2. Architecture
```javascript
party.events / screening.events / txn behaviour features
                │
                ▼
       ┌──────────────────┐   reads   ┌──────────────────────┐
       │  Risk service     │◄─────────│ risk_model_version    │  (scorecard JSON)
       │  (sync + async)   │          │ factor lookup tables  │
       └─────────┬────────┘          └──────────────────────┘
                 │ writes risk_assessment (+ factor rows)
                 ▼
          risk.events ──► onboarding (gate) · obligations (next review) · monitoring (profile) · cases (priority)
```
**Sync API** (for onboarding):
- `POST /internal/risk/score` with a party + context.
- Returns rating + breakdown, p95 \< 50 ms. Pure in-memory evaluation of the cached model.
**Async triggers** (recalculate and persist a new assessment):
<table header-row="true">
<tr>
<td>Trigger</td>
<td>Event</td>
</tr>
<tr>
<td>Party data change</td>
<td>`party.updated`</td>
</tr>
<tr>
<td>Screening result</td>
<td>`screening.match.dispositioned` with TRUE_MATCH, or PEP status change</td>
</tr>
<tr>
<td>Behaviour drift</td>
<td>Monitoring publishes `behaviour.profile.updated` monthly, or when a deviation exceeds a configured threshold</td>
</tr>
<tr>
<td>Policy/model change</td>
<td>New model version → **batch re-score** (§6)</td>
</tr>
<tr>
<td>Periodic review</td>
<td>Obligations module schedules it</td>
</tr>
</table>
---
## 3. Scoring model
### 3.1 Structure
A model version is a JSON document (validated by Pydantic) with:
1. **Factors**: each maps an input attribute to points via a lookup table, range bands or a function.
2. **Weights** per risk dimension.
3. **Hard rules (knock-outs/floors)** evaluated first.
4. **Bands** mapping the score to a rating.
5. **Outputs**: due-diligence level, review period, monitoring profile.
```yaml
model: MX-ITF-default
version: 4
dimensions:
  customer:   { weight: 0.30 }
  geography:  { weight: 0.25 }
  product:    { weight: 0.20 }
  channel:    { weight: 0.10 }
  behaviour:  { weight: 0.15 }
factors:
  - id: occupation
    dimension: customer
    input: person.occupation_code            # SINCO (MX) / CBO (BR) / CIUO (CO/PE)
    type: lookup
    table: occupation_risk_mx_v2              # 0..100 per code; default 40
  - id: economic_activity
    dimension: customer
    input: organization.industry_code         # SCIAN / CNAE / CIIU
    type: lookup
    table: activity_risk_mx_v3
  - id: residence_municipality
    dimension: geography
    input: address.residential.municipality_code   # INEGI clave
    type: lookup
    table: geo_risk_mx_municipal_v1           # derived from NRA + crime/frontier data
  - id: nationality
    dimension: geography
    input: person.nationalities
    type: max_lookup
    table: country_risk_v7                    # FATF lists, Basel AML index, tax havens
  - id: product
    dimension: product
    input: relationship.products
    type: max_lookup
    table: product_risk_tenant                # tenant-defined (e.g. international transfers = 80)
  - id: onboarding_channel
    dimension: channel
    input: application.channel
    type: lookup
    values: { BRANCH: 10, SDK_WEB: 50, SDK_IOS: 45, SDK_ANDROID: 45, WHATSAPP: 60, API: 55 }
  - id: declared_vs_actual_volume
    dimension: behaviour
    input: behaviour.monthly_inflow_ratio     # actual / declared expected volume
    type: bands
    bands: [ {lt: 1.2, points: 10}, {lt: 2.0, points: 40}, {lt: 4.0, points: 70}, {gte: 4.0, points: 95} ]
hard_rules:
  - when: screening.true_match_list in [UN_CONSOLIDATED, MX_LPB, OFAC_SDN]
    then: { rating: PROHIBITED }
  - when: person.pep_status == FOREIGN
    then: { floor: HIGH }
  - when: person.pep_status in [DOMESTIC, RCA] and policy.pep_domestic_floor == HIGH
    then: { floor: HIGH }
  - when: any(nationality in country_list.FATF_CALL_FOR_ACTION)
    then: { floor: HIGH }
  - when: party.is_non_resident and country == MX
    then: { floor: HIGH }
bands: { LOW: [0, 35), MEDIUM: [35, 65), HIGH: [65, 100] }
outputs:
  LOW:    { dd_level: STANDARD, review: P1Y, monitoring_profile: STD }   # MX: annual regardless
  MEDIUM: { dd_level: STANDARD, review: P1Y, monitoring_profile: STD }
  HIGH:   { dd_level: ENHANCED, review: P1Y, monitoring_profile: ENHANCED, approval: SENIOR }
```
### 3.2 Algorithm
```javascript
1. Resolve the effective model for (tenant, product, date). Load the factor tables (cached, versioned).
2. Evaluate hard rules → collect {forced_rating?, floors[]}.
3. For each dimension d:
     points_d = aggregate(factor points in d)        # default: max (conservative); configurable: mean
4. score = Σ_d weight_d × points_d                    # 0..100
5. rating = band(score); rating = max(rating, floors…); if forced → forced
6. Look up outputs (dd_level, review period → next_review_at, monitoring profile).
7. Persist risk_assessment + risk_assessment_factor rows (each factor's input value, points, weight, contribution).
8. Emit risk.assessment.created.v1 (+ risk.rating.changed.v1 if the rating changed).
```
- **Missing data policy:** a missing factor input gets the factor's `missing_points` (default = 70, conservative) and is flagged `data_gap=true`. Data gaps feed the onboarding "missing information" task list.
- **Determinism:** same inputs + same model version + same tables ⇒ same output. This is property-tested.
### 3.3 Factor tables
<table header-row="true">
<tr>
<td>Table</td>
<td>Seed source</td>
<td>Owner</td>
</tr>
<tr>
<td>`country_risk`</td>
<td>FATF lists (call for action / increased monitoring, updated after each plenary), EU high-risk list, tax-haven lists (MX regímenes fiscales preferentes, CL SII list), Basel AML Index</td>
<td>Platform (shared), versioned</td>
</tr>
<tr>
<td>`geo_risk_<cc>_municipal`</td>
<td>National Risk Assessments, public crime statistics (e.g. INEGI/SESNSP in MX), frontier/port zones, Triple Frontier (AR/BR/PY flag in AR STR form)</td>
<td>Platform (shared); tenant may tighten</td>
</tr>
<tr>
<td>`occupation_risk_<cc>`</td>
<td>Country occupational classifications (SINCO, CBO, CIUO) mapped to risk; PEP-adjacent roles</td>
<td>Platform</td>
</tr>
<tr>
<td>`activity_risk_<cc>`</td>
<td>SCIAN/CNAE/CIIU codes; "vulnerable activities" (MX LFPIORPI Art. 17) flagged high</td>
<td>Platform</td>
</tr>
<tr>
<td>`product_risk_tenant`</td>
<td>Each tenant's own products (from onboarding questionnaire)</td>
<td>Tenant</td>
</tr>
</table>
---
## 4. Data model
Extends <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> §6 (`risk_assessment`).
```sql
-- Shared, versioned risk models (platform templates) + tenant copies
CREATE TABLE risk_model (
    id          uuid PRIMARY KEY,
    tenant_id   uuid,                         -- NULL = platform template
    code        text NOT NULL,                -- 'MX-ITF-default'
    UNIQUE (tenant_id, code)
);

CREATE TABLE risk_model_version (
    id              uuid PRIMARY KEY,
    model_id        uuid NOT NULL REFERENCES risk_model(id),
    version         int  NOT NULL,
    definition      jsonb NOT NULL,           -- §3.1, validated by Pydantic
    table_versions  jsonb NOT NULL,           -- {"country_risk": 7, "geo_risk_mx_municipal": 1, …}
    status          text NOT NULL,            -- DRAFT, IN_REVIEW, APPROVED, ACTIVE, RETIRED
    approved_by     uuid, approved_at timestamptz,
    effective_from  timestamptz,
    UNIQUE (model_id, version)
);

CREATE TABLE risk_factor_table (
    id          uuid PRIMARY KEY,
    tenant_id   uuid,                          -- NULL = shared
    code        text NOT NULL,                 -- 'country_risk'
    version     int NOT NULL,
    rows        jsonb NOT NULL,                -- {"VE": 95, "MX": 40, …} or [{key, points, rationale}]
    source_note text NOT NULL,
    UNIQUE (tenant_id, code, version)
);

-- risk_assessment as in data-model.md, plus:
ALTER TABLE risk_assessment
    ADD COLUMN dimension_scores  jsonb NOT NULL DEFAULT '{}',   -- {"customer": 55, "geography": 80, …}
    ADD COLUMN hard_rules_fired  text[] NOT NULL DEFAULT '{}',
    ADD COLUMN dd_level          text NOT NULL DEFAULT 'STANDARD',   -- SIMPLIFIED, STANDARD, ENHANCED
    ADD COLUMN review_period     interval,
    ADD COLUMN data_gaps         text[] NOT NULL DEFAULT '{}',
    ADD COLUMN previous_id       uuid REFERENCES risk_assessment(id);

CREATE TABLE risk_assessment_factor (
    assessment_id  uuid NOT NULL REFERENCES risk_assessment(id),
    tenant_id      uuid NOT NULL,
    factor_id      text NOT NULL,
    dimension      text NOT NULL,
    input_value    text,                       -- non-PII representation (codes, bands), never raw names/IDs
    points         numeric(5,2) NOT NULL,
    contribution   numeric(5,2) NOT NULL,      -- weighted contribution to the final score
    data_gap       boolean NOT NULL DEFAULT false,
    PRIMARY KEY (assessment_id, factor_id)
);

-- Manual overrides (four-eyes, expiring)
CREATE TABLE risk_override (
    id               uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    relationship_id  uuid NOT NULL,
    from_rating      text NOT NULL,
    to_rating        text NOT NULL,
    reason           text NOT NULL,
    requested_by     uuid NOT NULL,
    approved_by      uuid,                     -- must differ from requested_by (CHECK in app + trigger)
    status           text NOT NULL,            -- PENDING, APPROVED, REJECTED, EXPIRED
    expires_at       timestamptz NOT NULL,     -- max 12 months; downgrades from HIGH max 6 months
    created_at       timestamptz NOT NULL DEFAULT now()
);
```
**Rules:**
- An override **cannot downgrade a PROHIBITED rating**, or a hard-rule floor imposed by law (e.g. foreign PEP → HIGH). Both are validated against the policy.
- An **approved override** creates a new `risk_assessment` with `trigger='MANUAL'` and references the override.
### 4.1 Enterprise-wide risk assessment (EWRA)
```sql
CREATE TABLE ewra_assessment (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    jurisdiction      char(2) NOT NULL,
    methodology_ver   text NOT NULL,          -- platform methodology template version
    period_start      date NOT NULL,
    period_end        date NOT NULL,          -- MX requires ≥ 12 months of data
    status            text NOT NULL,          -- DRAFT, IN_REVIEW, BOARD_APPROVED, FILED, SUPERSEDED
    risk_tolerance    text,                   -- AR requires a tolerance declaration
    approved_by_board date,
    filed_at          timestamptz,            -- AR: to UIF + sector supervisor by 30-Apr
    document_ref      text                    -- generated PDF/DOCX
);

CREATE TABLE ewra_risk_item (                 -- one row per risk (factor × segment)
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    assessment_id     uuid NOT NULL REFERENCES ewra_assessment(id),
    category          text NOT NULL,          -- CUSTOMER, PRODUCT, CHANNEL, GEOGRAPHY, TF, PF
    segment           text NOT NULL,          -- e.g. 'International transfers – SPEI→US'
    likelihood        smallint NOT NULL CHECK (likelihood BETWEEN 1 AND 5),
    impact            smallint NOT NULL CHECK (impact BETWEEN 1 AND 5),
    inherent_score    smallint GENERATED ALWAYS AS (likelihood * impact) STORED,
    metrics           jsonb NOT NULL,         -- auto-filled exposure: #customers, volume, %high-risk, alerts, STRs
    rationale         text
);

CREATE TABLE ewra_control (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    assessment_id   uuid NOT NULL REFERENCES ewra_assessment(id),
    risk_item_id    uuid NOT NULL REFERENCES ewra_risk_item(id),
    control_ref     text NOT NULL,            -- e.g. 'TM-RULE-STRUCT-01', 'KYC-LIVENESS'
    design_rating   smallint CHECK (design_rating BETWEEN 1 AND 5),
    operating_rating smallint CHECK (operating_rating BETWEEN 1 AND 5),
    evidence        jsonb                     -- auto-linked platform metrics (e.g. rule hit rate, test results)
);
-- residual = inherent × (1 − control_effectiveness), computed in a view with a methodology-specific formula
```
**EWRA automation (the differentiator):**
- The platform **pre-fills** `metrics` from live data: customer counts per segment, % high risk, volumes per product/geography, alerts, STRs, true-match rates.
- It links platform controls as evidence (rule coverage, screening latency, review backlog).
- It **generates the document** per country template: AR "informe técnico de autoevaluación", BR "avaliação interna de risco", MX "evaluación de riesgos".
- The compliance team then judges likelihood and impact.
---
## 5. API
<table header-row="true">
<tr>
<td>Method</td>
<td>Path</td>
<td>Purpose</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/customers/{id}/risk`</td>
<td>Current rating, score, breakdown, next review</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/customers/{id}/risk/history`</td>
<td>All assessments</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/customers/{id}/risk/overrides`</td>
<td>Request an override (staff)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/risk/overrides/{id}/approve`</td>
<td>Second-person approval</td>
</tr>
<tr>
<td>`GET/POST`</td>
<td>`/v1/risk/models`, `/v1/risk/models/{id}/versions`</td>
<td>Manage tenant model copies (console)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/risk/models/{id}/versions/{v}/simulate`</td>
<td>Re-score a sample / all customers **without persisting**; returns the rating migration matrix</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/risk/models/{id}/versions/{v}/activate`</td>
<td>Activate (requires approval) → batch re-score</td>
</tr>
<tr>
<td>`GET/POST`</td>
<td>`/v1/ewra/...`</td>
<td>EWRA CRUD, prefill, export</td>
</tr>
</table>
**Webhooks:** `risk.rating.changed`, `risk.review.due`.
---
## 6. Model change management
1. Analyst creates a draft version (copy of active).
2. **Simulation:** run over the full customer base in a read replica / ClickHouse snapshot and produce a **migration matrix** (LOW→MEDIUM counts etc.), plus the list of customers newly HIGH.
3. Approval by the compliance officer (four-eyes).
4. Activation:
	- A Temporal workflow `RescoreAll(tenant, model_version)` streams customers in batches of 1,000.
	- New assessments get `trigger='MODEL_CHANGE'`.
	- Review dates are recomputed; rating changes are published as events.
	- Throughput target: 1M customers \< 30 min per cell (CPU-bound, parallel workers).
5. The previous version is kept for reproducibility.
---
## 7. Machine learning (phase 2, optional)
- **Supervised model** (gradient boosting) predicting P(true STR / exit) from features + behaviour. It is used **as one additional factor** ("ml_signal") with capped weight, never as the sole decider.
- **Governance:**
	- model card, training data lineage, fairness checks (no protected attributes such as gender or ethnicity used directly);
	- drift monitoring;
	- challenger/champion process;
	- per-prediction SHAP explanations stored with the assessment.
---
## 8. Non-functional
<table header-row="true">
<tr>
<td>Item</td>
<td>Target</td>
</tr>
<tr>
<td>Sync scoring latency</td>
<td>p95 \< 50 ms</td>
</tr>
<tr>
<td>Batch re-score</td>
<td>1M customers \< 30 min</td>
</tr>
<tr>
<td>Reproducibility</td>
<td>Re-running an assessment with its stored versions reproduces the score exactly (nightly sampled check)</td>
</tr>
<tr>
<td>Privacy</td>
<td>Factor rows store codes/bands, not raw PII</td>
</tr>
</table>
---
## 9. Testing
- **Golden tests** per country template: fixtures of customer profiles → expected rating, dimensions, hard rules fired.
- **Property tests:** monotonicity (raising a factor never lowers the score) and determinism.
- **Override guardrails:** a test confirms legal floors can't be overridden.
- **EWRA snapshots:** golden document for a synthetic tenant per country.
## 10. Build plan
<table header-row="true">
<tr>
<td>Milestone</td>
<td>Scope</td>
<td>Eng-weeks</td>
</tr>
<tr>
<td>R-M1</td>
<td>Model schema, evaluator, factor tables (MX), assessment persistence, sync API</td>
<td>2.5</td>
</tr>
<tr>
<td>R-M2</td>
<td>Async triggers, review scheduling hookup, overrides with four-eyes</td>
<td>1.5</td>
</tr>
<tr>
<td>R-M3</td>
<td>Simulation + activation workflow, migration matrix UI</td>
<td>2</td>
</tr>
<tr>
<td>R-M4</td>
<td>EWRA module: data model, metric prefill, MX/AR/BR document templates</td>
<td>4</td>
</tr>
<tr>
<td>**Total**</td>
<td></td>
<td>**≈ 10**</td>
</tr>
</table>
## 11. Open questions
- Should domestic PEPs be a HIGH floor by default (common, but not mandatory everywhere)? Proposal: default floor HIGH, tenant may not lower it in MX/PE, may lower in BR (risk-based).
- Source and licence for municipality-level crime data per country.
- Do we ship a default product-risk catalog per fintech archetype (wallet, lender, remittance, crypto)?
