# 08 — Compliance calendar & governance
Source: https://app.notion.com/3fde5630e5e381f5b59bdd8bfb681a96

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Depends on:** <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/> (policy packs, calendar engine) · **Integrates:** <mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/> (EWRA, reviews), <mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/> (reports)
---
## Executive summary
**What it does:** builds a **personal compliance calendar** for each client. It lists every recurring legal obligation in every country they operate in, with exact due dates, an owner and proof of completion. Examples:
- monthly and quarterly reports, including "nothing to report" filings;
- annual risk assessments and audits;
- training;
- notifying the regulator when the compliance officer changes;
- manual updates;
- periodic customer re-reviews.
**How it works:**
- Obligations are defined once per country in our rulebooks.
- When a client is set up (country + licence type), the calendar appears automatically and updates when laws change.
- Anything the platform itself produces (reports, risk assessments, review counts) is **ticked off automatically** with evidence. The rest is assigned to people, with reminders and escalation.
**Why it matters:**
- Missed filings are the most common and most easily penalised compliance failures.
- Regulators and auditors ask for **evidence**, which this module keeps in one place.
- For a client's board, it is the simplest way to see "are we compliant?".
**Build:** \~5 engineer-weeks.
---
## 1. Obligation catalog (seed, from research)
<table header-row="true">
<tr>
<td>Country</td>
<td>Obligation</td>
<td>Frequency / due rule</td>
<td>Auto-evidence from platform?</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>ROR + empty report, FX-cash, crypto (quarterly)</td>
<td>First 10 business days of Jan/Apr/Jul/Oct</td>
<td>✓ report receipts</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>International transfers report</td>
<td>Monthly, 15 business days</td>
<td>✓</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>Annual AML audit report to CNBV</td>
<td>≤ 60 days after year-end</td>
<td>Evidence pack</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>Compliance officer / committee change notices</td>
<td>10 / 15 business days after event</td>
<td>✓ event-triggered</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>Manual changes to CNBV</td>
<td>20 business days after approval</td>
<td>—</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>Annual entity risk assessment (≥ 12 months data)</td>
<td>Annual</td>
<td>✓ EWRA module</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>Annual training</td>
<td>Annual</td>
<td>Training module</td>
</tr>
<tr>
<td>MX (financial)</td>
<td>Annual KYC file review</td>
<td>Annual per customer</td>
<td>✓ review completion rate</td>
</tr>
<tr>
<td>MX (non-financial, LFPIORPI)</td>
<td>Avisos / monthly zero report</td>
<td>Day 17 of next month</td>
<td>✓</td>
</tr>
<tr>
<td>MX (non-financial)</td>
<td>Risk assessment available; manual with methodology</td>
<td>From 01-03-2027, then annual</td>
<td>✓ EWRA</td>
</tr>
<tr>
<td>MX (non-financial)</td>
<td>Automated monitoring in place</td>
<td>By 01-06-2027</td>
<td>✓ attestation</td>
</tr>
<tr>
<td>MX (non-financial)</td>
<td>Training (first period 2027)</td>
<td>Annual</td>
<td>—</td>
</tr>
<tr>
<td>MX (non-financial)</td>
<td>Audit dictamen (first period 2028)</td>
<td>By last business day of March</td>
<td>Evidence pack</td>
</tr>
<tr>
<td>MX (non-financial)</td>
<td>Crypto-provider registration update</td>
<td>By \~30-05-2027</td>
<td>—</td>
</tr>
<tr>
<td>BR</td>
<td>No-occurrence communication (CNO)</td>
<td>10 business days after year-end</td>
<td>✓</td>
</tr>
<tr>
<td>BR</td>
<td>Internal risk assessment</td>
<td>Every 2 years</td>
<td>✓ EWRA</td>
</tr>
<tr>
<td>BR</td>
<td>Effectiveness report to board</td>
<td>By 31 March</td>
<td>Evidence pack</td>
</tr>
<tr>
<td>BR</td>
<td>Action plan + follow-up</td>
<td>By 30 June</td>
<td>—</td>
</tr>
<tr>
<td>BR</td>
<td>Res. 591 backlog (FX cash / self-custody crypto, Oct–Dec 2026)</td>
<td>First business day of Jan 2027</td>
<td>✓</td>
</tr>
<tr>
<td>BR</td>
<td>Siscoaf user/certificate renewals</td>
<td>e-CPF expiry</td>
<td>✓ cert expiry</td>
</tr>
<tr>
<td>CO</td>
<td>Monthly absence reports (STR + each annex)</td>
<td>First 20 calendar days</td>
<td>✓</td>
</tr>
<tr>
<td>CO</td>
<td>Quarterly AROS (Supersociedades)</td>
<td>10 calendar days after quarter</td>
<td>✓</td>
</tr>
<tr>
<td>CO</td>
<td>SARLAFT review + internal audit</td>
<td>Semiannual</td>
<td>—</td>
</tr>
<tr>
<td>CO</td>
<td>Board report by compliance officer</td>
<td>Semiannual (SFC) / annual (Supersociedades)</td>
<td>Evidence pack</td>
</tr>
<tr>
<td>CO</td>
<td>Policy update</td>
<td>≤ 2 years</td>
<td>—</td>
</tr>
<tr>
<td>CO</td>
<td>Adapt to CE 100-000020</td>
<td>By 31-05-2027</td>
<td>—</td>
</tr>
<tr>
<td>AR</td>
<td>Monthly reports or "period without movements"</td>
<td>Day 1–15</td>
<td>✓</td>
</tr>
<tr>
<td>AR</td>
<td>Risk self-assessment + methodology + tolerance</td>
<td>Before 30 April</td>
<td>✓ EWRA</td>
</tr>
<tr>
<td>AR</td>
<td>External reviewer (REI) report</td>
<td>≤ 120 days after self-assessment deadline</td>
<td>Evidence pack</td>
</tr>
<tr>
<td>AR</td>
<td>Annual systematic report (RSA)</td>
<td>2 Jan – 15 Mar</td>
<td>✓ (prefill)</td>
</tr>
<tr>
<td>AR</td>
<td>SMVM threshold refresh</td>
<td>1 Jan / 1 Jul</td>
<td>✓ units service</td>
</tr>
<tr>
<td>AR</td>
<td>Training; induction ≤ 60 business days</td>
<td>Annual / per hire</td>
<td>Training module</td>
</tr>
<tr>
<td>CL</td>
<td>Cash report (ROE) or ROE negativo</td>
<td>Monthly / quarterly / semiannual, first 10 business days</td>
<td>✓</td>
</tr>
<tr>
<td>CL</td>
<td>Manual update</td>
<td>≤ 2 years</td>
<td>—</td>
</tr>
<tr>
<td>CL</td>
<td>Training</td>
<td>Annual</td>
<td>—</td>
</tr>
<tr>
<td>CL</td>
<td>Registration changes</td>
<td>10 business days after change</td>
<td>—</td>
</tr>
<tr>
<td>CL</td>
<td>MiUAF migration</td>
<td>2027</td>
<td>—</td>
</tr>
<tr>
<td>PE</td>
<td>Compliance officer annual report (IAOC)</td>
<td>By 15 Feb (crypto providers) / per norm</td>
<td>✓ (prefill stats)</td>
</tr>
<tr>
<td>PE</td>
<td>Compliance officer quarterly / semiannual reports</td>
<td>Q +30 days; S: board then SBS +15 days</td>
<td>✓ (prefill)</td>
</tr>
<tr>
<td>PE</td>
<td>Risk assessment</td>
<td>≤ 3 years (UIF-supervised)</td>
<td>✓ EWRA</td>
</tr>
<tr>
<td>PE</td>
<td>Training; induction ≤ 30 days</td>
<td>Annual / per hire</td>
<td>—</td>
</tr>
<tr>
<td>PE</td>
<td>Compliance-officer designation / changes (SISDEL)</td>
<td>15 days / 5 business days</td>
<td>—</td>
</tr>
<tr>
<td>All</td>
<td>List of FATF high-risk jurisdictions updated after each plenary</td>
<td>Feb / Jun / Oct</td>
<td>✓ platform</td>
</tr>
<tr>
<td>All</td>
<td>Periodic customer reviews by risk tier</td>
<td>Per policy</td>
<td>✓ review module</td>
</tr>
</table>
---
## 2. Data model
```sql
-- Shared definitions (part of policy packs)
CREATE TABLE obligation_def (
    id              uuid PRIMARY KEY,
    code            text UNIQUE NOT NULL,      -- 'AR_SELF_ASSESSMENT', 'MX_ITF_ROR_Q'
    jurisdiction    char(2) NOT NULL,
    title           text NOT NULL,
    legal_basis     text NOT NULL,             -- 'Res. UIF 14/2023 arts. 5-6'
    recurrence      jsonb NOT NULL,            -- {"kind":"ANNUAL","anchor":"YEAR_END"} | {"kind":"EVENT","event":"OC_CHANGED"} | {"kind":"MONTHLY"}
    deadline_rule   jsonb NOT NULL,            -- DeadlineRule (foundations §6)
    evidence_mode   text NOT NULL,             -- AUTO_REPORT | AUTO_METRIC | MANUAL_UPLOAD | ATTESTATION
    linked_report   text,                      -- report code if AUTO_REPORT
    valid_from      date NOT NULL,
    valid_to        date
);

-- Tenant instances
CREATE TABLE obligation (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    def_id          uuid NOT NULL REFERENCES obligation_def(id),
    period_key      text NOT NULL,             -- '2026Q4', '2026', '2026-11', 'EVENT:<uuid>'
    due_at          timestamptz NOT NULL,
    owner_id        uuid REFERENCES staff_user(id),
    status          text NOT NULL,             -- UPCOMING, DUE_SOON, IN_PROGRESS, DONE, LATE, WAIVED
    completed_at    timestamptz,
    UNIQUE (tenant_id, def_id, period_key)
);

CREATE TABLE obligation_evidence (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    obligation_id   uuid NOT NULL REFERENCES obligation(id),
    kind            text NOT NULL,             -- REPORT_RECEIPT, DOCUMENT, METRIC_SNAPSHOT, ATTESTATION, BOARD_MINUTES
    ref             text NOT NULL,             -- report_filing.id / document storage key / metrics json
    added_by        text NOT NULL,             -- 'system' or user id
    added_at        timestamptz NOT NULL DEFAULT now()
);

-- Supporting governance registries
CREATE TABLE compliance_officer_appointment (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    jurisdiction  char(2) NOT NULL,
    person_name   text NOT NULL,
    role          text NOT NULL,               -- PRINCIPAL, ALTERNATE, INTERIM
    appointed_at  date NOT NULL,
    ended_at      date,
    notified_regulator_at date,
    notice_ref    text
);

CREATE TABLE training_record (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    staff_user_id uuid,                        -- or external employee id
    course_code  text NOT NULL,
    completed_at date NOT NULL,
    score        numeric,
    certificate_ref text
);

CREATE TABLE policy_document (                 -- AML manual, code of conduct, KYC policy
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    kind          text NOT NULL,
    version       text NOT NULL,
    approved_at   date,
    approved_by   text,                        -- board / committee
    storage_key   text NOT NULL,
    next_review_due date
);
```
---
## 3. Engine
- **Generator** (daily Temporal cron per tenant):
	- For each `obligation_def` applicable to the tenant's packs on that date, materialise instances for the next 13 months (idempotent by `(def, period_key)`).
	- Event-based definitions are instantiated from domain events, e.g. `compliance_officer.changed` → MX 10-business-day notice obligation.
- **Status updater:**
	- `DUE_SOON` at T-7 business days (configurable).
	- `LATE` past `due_at`.
	- Notifications by email or Slack/Teams webhook to the owner, escalating to the MLRO at T-2 and when late.
- **Auto-completion:**
	- `report.filed` with a matching code and period → attach the receipt and mark `DONE`.
	- The EWRA reaching `BOARD_APPROVED` → done.
	- Metric obligations (e.g. annual KYC review) compute completion % from `customer_relationship.next_review_at` and attach a snapshot.
- **Law changes:** when a pack version changes definitions, future instances are regenerated and past ones are kept. Changes are announced to the tenant ("new obligation from 2027-03-01").
## 4. Board pack
- One-click quarterly PDF/XLSX per jurisdiction:
	- obligations met/late, reports filed with receipts;
	- alert/case/STR statistics; risk distribution and migrations;
	- training completion; open regulator requests.
- It serves as the base for CO semiannual reports, PE quarterly/semiannual compliance-officer reports, the BR effectiveness report and the PE IAOC statistics.
## 5. API
<table header-row="true">
<tr>
<td>Method</td>
<td>Path</td>
<td>Purpose</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/obligations?from=&to=&status=`</td>
<td>Calendar (also `.ics` feed per user)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/obligations/{id}/evidence`</td>
<td>Attach evidence</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/obligations/{id}/complete` · `/waive`</td>
<td>Close (waive requires a reason + MLRO)</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/board-packs?period=`</td>
<td>Generate a pack</td>
</tr>
</table>
## 6. Testing & build
- **Golden calendars per country:** a full year of due dates for a synthetic tenant, checked against regulator-published calendars (UIAF, UAF) where available.
- **Build:**
	<table header-row="true">
<tr>
<td>Milestone</td>
<td>Scope</td>
<td>Eng-weeks</td>
</tr>
<tr>
<td>OB-1</td>
<td>Definitions + generator + statuses + notifications</td>
<td>2</td>
</tr>
<tr>
<td>OB-2</td>
<td>Auto-evidence hooks (reports, EWRA, reviews)</td>
<td>1.5</td>
</tr>
<tr>
<td>OB-3</td>
<td>Governance registries (officer, training, policies) + board pack</td>
<td>1.5</td>
</tr>
<tr>
<td>**Total**</td>
<td></td>
<td>**≈ 5**</td>
</tr>
	</table>
