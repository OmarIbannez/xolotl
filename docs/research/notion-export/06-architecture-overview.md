# Architecture overview
Source: https://app.notion.com/3fde5630e5e38175aa91cf38caaaf97e

**Date:** 2026-10-17
**Status:** Draft proposal
**Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e381a7a13fd6fda3ef15ad"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e3818fb517ec69c9a9717a"/>
---
## 1. Guiding principle
**One shared platform where every fintech is a strictly isolated tenant, deployed in one copy ("cell") per country or region.**
The legal setup drives most choices:
- Each fintech is the **regulated entity** (*sujeto obligado*) and the **data controller**.
- We are its **processor** (*encargado* in Mexico, *operador* under LGPD in Brazil) and its **outsourced technology provider**.
- So the design must prove that:
	1. one tenant can never see another tenant's data;
	2. every decision can be reproduced and explained to a regulator;
	3. data can stay in-country where required.
---
## 2. Overall shape
```javascript
                   ┌──────────────────────────────────────────┐
                   │      CONTROL PLANE (global, no PII)       │
                   │  tenant registry · billing/metering ·     │
                   │  policy packs · rule/model registry ·     │
                   │  feature flags · admin console            │
                   └───────────────┬──────────────────────────┘
                                   │ config only
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  CELL: MX       │      │  CELL: BR       │      │ CELL: dedicated │
│                 │      │  (São Paulo)    │      │ (large bank)    │
│ API gateway     │      │                 │      │                 │
│ Onboarding wf   │      │  same stack     │      │  same stack,    │
│ Screening       │      │                 │      │  single tenant  │
│ Txn monitoring  │      │                 │      │                 │
│ Case mgmt       │      │                 │      │                 │
│ Reg. reporting  │      │                 │      │                 │
│ Tenant data     │      │                 │      │                 │
└────────┬────────┘      └────────┬────────┘      └─────────────────┘
         │                        │
         ▼                        ▼
  Connectors: RENAPO, INE,  Connectors: Serpro, Receita,
  SAT, SPEI feeds, UIF      Pix/DICT, SISCOAF
         ▲                        ▲
         └──── SHARED REFERENCE DATA (no customer data) ────┘
              OFAC/UN/EU/UK lists, PEP DBs, adverse media,
              UMA/SMVM/SMMLV/UF/UIT tables, report schemas
```
### Why one copy per country (a "cell")
- **Data residency.** Banks, and increasingly fintechs, require customer data to stay in-country.
- **Contained failures.** A problem in one cell doesn't affect the others.
- **Different regulators per country.** Connectors, report formats, and outsourcing notifications differ.
- **A path for large clients.** A bank that demands its own deployment gets a dedicated cell running the same code.
### Control plane vs. cell
<table header-row="true">
<tr>
<td>Control plane (global)</td>
<td>Cell (regional)</td>
</tr>
<tr>
<td>Tenant registry and which cell each tenant lives in</td>
<td>All customer data (PII, documents, biometrics, transactions)</td>
</tr>
<tr>
<td>Billing and usage metering (aggregated counts only)</td>
<td>Onboarding workflows, screening, monitoring, cases, reports</td>
</tr>
<tr>
<td>Policy packs, rule and model registry (definitions only)</td>
<td>Per-tenant encryption keys (in the regional key management service)</td>
</tr>
<tr>
<td>Admin console and feature flags</td>
<td>Audit log and write-once (WORM) archive</td>
</tr>
</table>
**No customer data ever leaves its cell.**
---
## 3. Tenant isolation, in layers
<table header-row="true">
<tr>
<td>Layer</td>
<td>Mechanism</td>
</tr>
<tr>
<td>**Database**</td>
<td>`tenant_id` on every tenant-scoped row; **Postgres row-level security (RLS)** enforces it in the database, not just in app code; `tenant_id` leads every composite key and index so references can't cross tenants</td>
</tr>
<tr>
<td>**Encryption**</td>
<td>Per-tenant data encryption key (KMS envelope encryption); bring-your-own-key option for large clients; field-level encryption of PII</td>
</tr>
<tr>
<td>**Deletion**</td>
<td>Deleting a tenant's key makes its data unreadable ("crypto-shredding"), subject to legal holds and retention periods</td>
</tr>
<tr>
<td>**Data model**</td>
<td>The same human at fintech A and fintech B = **two separate parties**; never linked without explicit consent</td>
</tr>
<tr>
<td>**Object storage**</td>
<td>Per-tenant prefixes and per-tenant access roles for documents and biometrics</td>
</tr>
<tr>
<td>**Compute**</td>
<td>Per-tenant quotas; large tenants get dedicated workers</td>
</tr>
<tr>
<td>**Secrets**</td>
<td>Per-tenant vault paths for the tenant's own regulator and registry credentials</td>
</tr>
</table>
---
## 4. Configuration: policy packs (country × sector)
**Don't fork code per client.** Keep versioned configuration bundles instead:
- Examples: `MX/ITF`, `MX/BANK`, `MX/LFPIORPI-REAL-ESTATE`, `BR/IP-PIX`, `BR/VASP`, `CO/SARLAFT`, `CO/SAGRILAFT`, `AR/PSAV`.
- Each pack defines:
	- **Beneficial-owner rules:** ownership threshold (e.g. 25% in MX) and control criteria.
	- **Identity checks:** which are required per account tier (e.g. MX Levels 1–4).
	- **Screening:** which lists apply and how strict matching is.
	- **Risk:** the scoring matrix and triggers for enhanced due diligence.
	- **Reporting:** thresholds expressed in local units (UMA, SMVM, SMMLV, UF, UIT) and report templates and deadlines.
	- **Retention:** how long records are kept.
- Each tenant **inherits one or more packs** and may add **overrides that only tighten** the rules. Overrides are validated in code, and changes are versioned, approved, and audited.
- **When the law changes, update the pack once** and every tenant inherits the change on the new pack version's effective date.
---
## 5. Data boundary: shared vs. tenant-private
<table header-row="true">
<tr>
<td>Shared across tenants</td>
<td>Strictly per tenant</td>
</tr>
<tr>
<td>OFAC/UN/EU/UK sanctions lists</td>
<td>Customers (parties), identifiers, documents, biometrics</td>
</tr>
<tr>
<td>PEP databases, adverse media</td>
<td>Transactions, accounts, counterparties</td>
</tr>
<tr>
<td>Monetary unit tables (UMA, SMVM, UF…)</td>
<td>Alerts, cases, suspicious-activity reports (SARs/ROS)</td>
</tr>
<tr>
<td>Regulatory report schemas</td>
<td>**Private lists, e.g. Mexico's LPB** (confidential per recipient)</td>
</tr>
<tr>
<td>Policy pack definitions</td>
<td>Tenant policy overrides, rules, model calibrations</td>
</tr>
</table>
**Cross-tenant fraud intelligence** (shared mule and device signals) is valuable but **phase 2**:
- Signals are hashed or tokenized.
- Tenants opt in explicitly by contract.
- It needs a legal review under LGPD and Mexico's LFPDPPP.
- It must **never** expose SAR/ROS content or that a report exists (tipping-off risk).
---
## 6. Core services (per cell)
### 6.1 API gateway and developer surface
- **Authentication:** OAuth2 client credentials or **mTLS** per tenant, plus a **sandbox environment per tenant** with synthetic test identities.
- **Request handling:** idempotency keys on every write, per-tenant rate limits, and **signed webhooks** for asynchronous results (onboarding decided, screening hit, alert raised, report filed).
- **SDKs:** web, iOS, and Android for capture (document, selfie, liveness, geolocation), with white-label theming per tenant.
- **Channels:** WhatsApp onboarding (phase 2).
### 6.2 Onboarding orchestration
- **Durable workflow engine** (Temporal or similar). Onboarding is long-running and asynchronous: document upload, registry checks, retries when a registry is down, manual review, video-call fallback.
- The workflow definition is chosen by the tenant's policy pack version, which is **frozen at the start** of the application.
### 6.3 Connector layer (external data sources)
- One adapter per source: RENAPO, INE, SAT, Serpro/Datavalid, Receita, RENAPER, Registraduría, RENIEC, company registries, identity and liveness vendors.
- Each adapter handles retries, circuit breakers, fallbacks (vendor A → vendor B), and caching **only where legally allowed**.
- **Per-tenant cost metering** on every call; registry checks are pay-per-use and are passed through in billing.
- **Credential mode per tenant:** use the platform's contract, or the tenant's own credentials (some regulated fintechs have direct Serpro or RENAPO access).
- Every call produces an **immutable ****`verification_check`**** record with the raw response stored as evidence**.
### 6.4 Screening
- **Shared list ingestion:** versioned snapshots of each list, with diffs between versions.
- **Private list ingestion:** tenant-scoped and encrypted (e.g. Mexico's LPB).
- **LATAM-tuned matcher:** two surnames, particles ("de la", "dos"), accents (ñ, ç), word-order swaps, aliases, and date-of-birth or ID tie-breakers.
- **When to screen:** at onboarding, on transaction counterparties, and **delta re-screening of the whole customer base on every list update**. Mexico UIF designations require same-day action.
- **Suppressing repeat alerts:** a "good guy" table records cleared matches so they don't re-alert unless the list entry changes.
- **Freeze workflow:** a confirmed LPB hit triggers immediate suspension and the required notification.
### 6.5 Transaction monitoring (real-time)
```javascript
tenant ingest API / bank feed
         │
         ▼
  event stream (Kafka/Redpanda) ── partitioned by tenant_id
         │
         ▼
  stream processor (Flink or similar)
    ├── stateful aggregates (rolling sums per customer in UMA/USD, velocity, counterparties)
    ├── rules engine (versioned rules per tenant)
    ├── ML scoring (versioned models)
    └── threshold-report accumulators (automatic reports)
         │
         ├──► alerts topic ──► alert/case service
         ├──► sync decision API (Pix/SPEI pre-authorization, < ~200 ms)
         └──► lakehouse (Parquet/Iceberg) for history, backtesting, 10-year retention
```
- **Noisy neighbors:** per-tenant quotas; large tenants get their own consumer groups or workers; small tenants share a pool.
- **Rule simulation and backtesting** run against the tenant's own lakehouse history. This is the evidence of effectiveness that GAFILAT's 5th round looks for.
### 6.6 Case management
- Flow: alert → triage → case, with **four-eyes approval** and SLA timers driven by regulatory deadlines.
- **Restricted cases (tipping-off):** cases with a SAR/ROS are visible only to compliance roles.
- Staff use their **own SSO** (SAML/OIDC); roles are tenant-scoped (analyst, senior analyst, compliance officer/MLRO, auditor read-only).
### 6.7 Regulatory reporting
- Template engine driven by **versioned report schemas** per jurisdiction:
	- Mexico: UIF reports via SPPLD and LFPIORPI *avisos*
	- Brazil: SISCOAF
	- Colombia: SIREL
	- Argentina, Chile, Peru: the national FIU formats
- **Deadline calendar**, including required zero ("no operations") reports.
- **Filing happens under the tenant's own regulator credentials.** The tenant is the obligated entity, so it either stores its credentials and certificates in its secrets vault, or downloads the generated file and uploads it to the regulator itself.
- Filing attempts, regulator receipts, and rejections are tracked.
### 6.8 Audit and retention
- Append-only, hash-chained **audit log** per tenant, streamed to **write-once (WORM) object storage** (S3 Object Lock or equivalent).
- Logs every PII view, decision, export, policy change, and platform-operator access.
- `retention_until` is **computed** from the policy pack and the relationship end date. A purge job crypto-shreds expired data unless a **legal hold** applies.
---
## 7. Outsourcing rules apply to us
Regulators of our fintech clients treat us as an outsourced or cloud provider. **These rules were not verified in this research — confirm before contracting:**
- **Brazil:** Res. CMN 4.893 sets the cloud and outsourcing rules: notifying the BCB, regulator access to data, contingency plans.
- **Mexico:** CNBV and Ley Fintech rules for fintechs (ITFs) include cloud-provider requirements, e.g. a primary and a secondary provider in different jurisdictions.
**Practical consequences:**
- **Contracts:** audit rights and regulator access clauses.
- **Resilience:** a documented business-continuity and disaster-recovery plan with tested failover.
- **Certifications:** SOC 2 Type II and ISO 27001 early; procurement will ask for them.
- **Data processing agreements** aligned with LGPD, LFPDPPP, Ley 1581 (CO), Ley 25.326 (AR), Ley 21.719 (CL, from Dec 2026), and Ley 29733 (PE).
---
## 8. Suggested stack
<table header-row="true">
<tr>
<td>Concern</td>
<td>Choice</td>
</tr>
<tr>
<td>Services</td>
<td>Python + FastAPI, Pydantic models (incl. policy-pack validation), httpx for connectors</td>
</tr>
<tr>
<td>OLTP</td>
<td>PostgreSQL with RLS, monthly-partitioned transaction table</td>
</tr>
<tr>
<td>Workflows</td>
<td>Temporal</td>
</tr>
<tr>
<td>Streaming</td>
<td>Kafka/Redpanda + Flink (or a Python stream framework at low volume)</td>
</tr>
<tr>
<td>History / analytics</td>
<td>Parquet/Iceberg lakehouse</td>
</tr>
<tr>
<td>Objects</td>
<td>S3-compatible storage, Object Lock for audit and evidence</td>
</tr>
<tr>
<td>Keys / secrets</td>
<td>Cloud KMS (per-tenant data keys), Vault or cloud secrets manager</td>
</tr>
<tr>
<td>Hosting</td>
<td>Cloud regions in-country where available (Mexico, São Paulo), dedicated-cell option</td>
</tr>
</table>
---
## 9. Build order
1. **One cell (Mexico), pooled tenants** — with RLS, per-tenant keys, and policy packs from day 1. Retrofitting isolation later is very painful.
2. Shared reference-data service + screening (incl. private LPB lists).
3. Onboarding workflows + connectors with cost metering.
4. Case management + regulatory report generation.
5. Real-time transaction monitoring on the event stream.
6. Second cell (Brazil), then the dedicated-cell option for banks.
7. Phase 2: cross-tenant fraud intelligence, reusable KYC (with consent), WhatsApp channel, travel-rule integration for VASP clients.
---
## 10. Open decisions
- [ ] **Scale:** number of tenants expected, and peak transactions per second for the largest one. This decides Postgres+lakehouse vs. a dedicated OLAP store from day 1, and whether a Python stream framework is enough.
- [ ] **Residency:** do the first prospects require in-country data?
- [ ] **Name matching vs. encryption:** where readable normalized names live (see <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/>).
- [ ] **Registry access:** platform-held contracts vs. tenant-held credentials for INE, RENAPO, and Serpro.
- [ ] **Outsourcing obligations:** verify Res. CMN 4.893 and the Mexican fintech cloud rules.
