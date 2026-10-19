# 🗂️ 06 — Case management
Source: https://app.notion.com/3fde5630e5e3814783fbe56ba6027f61

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Depends on:** <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e3814da2f5ce4e03addb79"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e3817b9987f2a49b2bd540"/> · **Feeds:** <mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/>
---
## Executive summary
**What it does:** gives each client's compliance team **one workspace**. Every alert lands there (suspicious payments, possible sanctions matches, onboarding reviews). Analysts investigate it, decide, and, when required, turn it into a **suspicious-activity report** for the regulator.
**How it works:**
- **Alerts are grouped into cases** by customer.
- **Work queues and timers** follow each country's legal deadlines. Argentina allows 24 hours after concluding suspicion; Mexico requires a compliance committee decision within 60 days, then filing within 3 business days.
- **Every decision needs a second person** where the law or good practice requires it.
- **Mexico's compliance committee** gets built-in meeting, voting and minutes support.
- **Sensitive cases are hidden.** Cases that may lead to a report are visible only to authorised compliance staff, because warning the customer ("tipping off") is a crime everywhere.
- The system **pre-drafts the report narrative** in each regulator's required structure and length.
**Why it matters:** analyst time is the biggest compliance cost for clients. Good tooling halves handling time, and regulators judge firms on documented, timely decisions.
**Build:** \~10 engineer-weeks for v1.
---
## 1. Requirements
<table header-row="true">
<tr>
<td>#</td>
<td>Requirement</td>
<td>Source</td>
</tr>
<tr>
<td>C1</td>
<td>Unified alert inbox (monitoring, screening, onboarding review, KYB review, manual, employee referral)</td>
<td>—</td>
</tr>
<tr>
<td>C2</td>
<td>Case grouping per subject; link alerts, parties, transactions, documents</td>
<td>—</td>
</tr>
<tr>
<td>C3</td>
<td>**Country STR clocks**: MX committee ≤ 60 days after detection → report ≤ 3 business days after; MX 24-hour reports; BR analysis ≤ 45 days from selection → file next business day after decision; AR 24 h after concluding, ≤ 90 days from operation; terrorism/proliferation 24 h from operation; CO immediate; CL ASAP; PE ≤ 24 h after compliance-officer qualification</td>
<td>Raw research files</td>
</tr>
<tr>
<td>C4</td>
<td>Four-eyes decisions; configurable approval chains</td>
<td>Audit</td>
</tr>
<tr>
<td>C5</td>
<td>**MX compliance committee (CCC)**: sessions (at least monthly), quorum, votes, minutes, decisions on unusual and internal-concern reports</td>
<td>MX DCG</td>
</tr>
<tr>
<td>C6</td>
<td>Tipping-off protection: restricted cases, need-to-know ACLs, no client-facing leakage</td>
<td>All</td>
</tr>
<tr>
<td>C7</td>
<td>Narrative assistant with regulator structure and length limits (CL 250–1,000 / 250–500 chars; MX UIF best-practice blocks; CO who/what/why/when/where; BR "Informações Adicionais")</td>
<td>Regulator guides</td>
</tr>
<tr>
<td>C8</td>
<td>Related-party roles per country code list (MX related-persons rows; BR "Tipo de Envolvimento"; AR "relación con el hecho")</td>
<td>Report formats</td>
</tr>
<tr>
<td>C9</td>
<td>Outcome actions: no action / EDD / restrict / exit relationship / freeze / STR</td>
<td>—</td>
</tr>
<tr>
<td>C10</td>
<td>Regulator information-request tracker (CO 5 business days; PE 48 business hours; AR, MX)</td>
<td>CO Res. UIAF 010/2025, PE 2660</td>
</tr>
<tr>
<td>C11</td>
<td>QA sampling of closed alerts; productivity metrics</td>
<td>Effectiveness</td>
</tr>
<tr>
<td>C12</td>
<td>Internal-concern / employee cases (MX ROIP) with stricter access</td>
<td>MX DCG</td>
</tr>
</table>
---
## 2. Domain model & state machines
### 2.1 Alert lifecycle
```javascript
NEW ──assign──► IN_TRIAGE ──close(no issue, reason)──► CLOSED_NO_ACTION
                    │
                    ├──link to existing case──► LINKED
                    └──escalate──► ESCALATED (creates or links a case)
```
- Auto-closure is **only** for configured noise patterns (e.g. a screening match auto-cleared by the whitelist). Every auto-closure is logged with the rule that closed it.
### 2.2 Case lifecycle
```javascript
OPEN ─► INVESTIGATING ─► PROPOSED_DECISION ─► (APPROVAL | COMMITTEE) ─► DECIDED ─┬─► CLOSED (no report)
                                                                                 ├─► REPORT_DRAFTING ─► REPORT_FILED ─► CLOSED
                                                                                 └─► ACTION_PENDING (exit/freeze) ─► CLOSED
```
- `PROPOSED_DECISION` needs a reviewer different from the proposer (four-eyes).
- For MX unusual-op/internal-concern decisions, the next step is `COMMITTEE`.
- Any state can **reopen** with justification, which is audited.
### 2.3 SLA clocks
Each case can have several **clocks**, created from policy-pack rules when facts happen:
<table header-row="true">
<tr>
<td>Clock</td>
<td>Start event</td>
<td>Due</td>
</tr>
<tr>
<td>`MX_CCC_DECISION`</td>
<td>`detected_at` (first alert)</td>
<td>+60 calendar days</td>
</tr>
<tr>
<td>`MX_ROI_FILING`</td>
<td>committee decision = REPORT</td>
<td>+3 business days (MX calendar)</td>
</tr>
<tr>
<td>`MX_24H`</td>
<td>flag "24-hour case" (illicit-funds indication / LPB)</td>
<td>+24 h</td>
</tr>
<tr>
<td>`BR_ANALYSIS`</td>
<td>alert selection</td>
<td>+45 days</td>
</tr>
<tr>
<td>`BR_FILING`</td>
<td>decision = REPORT</td>
<td>next business day</td>
</tr>
<tr>
<td>`AR_ROS`</td>
<td>decision = suspicious</td>
<td>+24 h; hard cap operation date + 90 days</td>
</tr>
<tr>
<td>`AR_RFT`</td>
<td>sanctions/terrorism-financing match</td>
<td>+24 h from operation</td>
</tr>
<tr>
<td>`PE_ROS`</td>
<td>compliance-officer qualification = suspicious</td>
<td>+24 h</td>
</tr>
<tr>
<td>`CO_ROS`</td>
<td>decision = REPORT</td>
<td>"immediate" (configured as +24 h internal target)</td>
</tr>
<tr>
<td>`REGULATOR_REQUEST`</td>
<td>request received</td>
<td>CO +5 business days, PE +48 business hours, etc.</td>
</tr>
</table>
Clocks show in queues (red/amber/green), trigger escalation notifications, and are reported on the obligations dashboard.
---
## 3. Data model
Extends `alert`, `aml_case`, `case_party`, `case_event` in <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> §8.
```sql
ALTER TABLE aml_case
    ADD COLUMN subject_party_id  uuid,
    ADD COLUMN priority          smallint NOT NULL DEFAULT 3,   -- 1 highest
    ADD COLUMN detected_at       timestamptz NOT NULL,
    ADD COLUMN jurisdiction      char(2) NOT NULL,
    ADD COLUMN policy_version_id uuid NOT NULL,
    ADD COLUMN visibility        text NOT NULL DEFAULT 'TEAM',  -- TEAM | RESTRICTED | INSIDER
    ADD COLUMN queue_id          uuid;

CREATE TABLE case_queue (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    name        text NOT NULL,                 -- 'Screening L1', 'TM High', 'Restricted'
    filter      jsonb NOT NULL,                -- routing rule (case_type, priority, jurisdiction…)
    assignment  text NOT NULL DEFAULT 'PULL',  -- PULL | ROUND_ROBIN | SKILL
    UNIQUE (tenant_id, name)
);

CREATE TABLE case_member (                     -- explicit ACL for RESTRICTED / INSIDER cases
    tenant_id  uuid NOT NULL,
    case_id    uuid NOT NULL REFERENCES aml_case(id),
    user_id    uuid NOT NULL REFERENCES staff_user(id),
    role       text NOT NULL,                  -- INVESTIGATOR, REVIEWER, MLRO, OBSERVER
    PRIMARY KEY (case_id, user_id)
);

CREATE TABLE case_clock (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    case_id     uuid NOT NULL REFERENCES aml_case(id),
    clock_code  text NOT NULL,                 -- MX_CCC_DECISION, AR_ROS, …
    started_at  timestamptz NOT NULL,
    due_at      timestamptz NOT NULL,
    hard_due_at timestamptz,                   -- e.g. AR 90-day cap
    stopped_at  timestamptz,
    status      text NOT NULL                  -- RUNNING, MET, BREACHED, CANCELLED
);

CREATE TABLE case_decision (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    case_id        uuid NOT NULL REFERENCES aml_case(id),
    decision       text NOT NULL,              -- NO_ACTION, EDD, RESTRICT, EXIT, FREEZE, REPORT_STR, REPORT_24H, REPORT_RFT
    rationale      text NOT NULL,
    proposed_by    uuid NOT NULL,
    approved_by    uuid,                       -- ≠ proposed_by
    committee_session_id uuid,
    decided_at     timestamptz,
    CHECK (approved_by IS NULL OR approved_by <> proposed_by)
);

CREATE TABLE committee_session (               -- MX Comité de Comunicación y Control
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    held_at      timestamptz NOT NULL,
    kind         text NOT NULL,                -- ORDINARY, EXTRAORDINARY
    quorum_met   boolean NOT NULL,
    minutes_ref  text,                         -- generated minutes (PDF), signed
    status       text NOT NULL                 -- SCHEDULED, HELD, MINUTES_SIGNED
);
CREATE TABLE committee_member (tenant_id uuid, session_id uuid, user_id uuid, role text, present boolean,
                               PRIMARY KEY (session_id, user_id));
CREATE TABLE committee_vote (
    tenant_id   uuid NOT NULL,
    session_id  uuid NOT NULL REFERENCES committee_session(id),
    case_id     uuid NOT NULL REFERENCES aml_case(id),
    user_id     uuid NOT NULL,
    vote        text NOT NULL,                 -- REPORT, NO_REPORT, ABSTAIN
    PRIMARY KEY (session_id, case_id, user_id)
);

CREATE TABLE case_narrative (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    case_id       uuid NOT NULL REFERENCES aml_case(id),
    template_code text NOT NULL,               -- CL_ROS_V2025, MX_F41_UIF_GUIDE, CO_SIREL_ROS, BR_SISCOAF_INFO, AR_SRO_ROS, PE_ROSEL
    sections      jsonb NOT NULL,              -- {"hechos": "...", "sospecha": "...", ...}
    version       int NOT NULL,
    author_id     uuid NOT NULL,
    created_at    timestamptz NOT NULL
);

CREATE TABLE case_attachment (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    case_id     uuid NOT NULL REFERENCES aml_case(id),
    document_id uuid REFERENCES document(id),  -- reuse KYC docs
    storage_key text,                          -- or uploaded evidence
    sha256      bytea NOT NULL,
    include_in_report boolean NOT NULL DEFAULT false
);

CREATE TABLE regulator_request (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    jurisdiction  char(2) NOT NULL,
    authority     text NOT NULL,               -- UIF-MX, CNBV, COAF, UIAF, UIF-AR, UAF, UIF-PE, Fiscalía…
    reference     text NOT NULL,
    received_at   timestamptz NOT NULL,
    due_at        timestamptz NOT NULL,
    subject_parties uuid[],
    status        text NOT NULL,               -- OPEN, RESPONDED, EXTENDED
    response_ref  text
);
```
**Row-level security plus visibility:** on top of the tenant policy, an extra policy on `aml_case` (and its child tables, via a view) enforces:
```sql
CREATE POLICY case_visibility ON aml_case USING (
  visibility = 'TEAM'
  OR EXISTS (SELECT 1 FROM case_member m WHERE m.case_id = aml_case.id
             AND m.user_id = current_setting('app.user_id')::uuid)
  OR current_setting('app.user_roles') LIKE '%MLRO%'
);
```
API-key (machine) actors **cannot read RESTRICTED cases**, so they can't leak into client systems or customer-facing apps.
---
## 4. Key workflows
### 4.1 Alert → case (auto-grouping)
- On `alert.created`, the router finds an **open case with the same subject party and compatible type within 30 days**. If found, it links the alert; otherwise it creates a case.
- Priority = max(alert severity, customer risk rating, list-type weight).
- Sanctions true-match candidates go to the `Screening-Urgent` queue with a 24 h clock.
### 4.2 Investigation workspace
The workspace shows one consolidated view per case:
- subject profile: KYC, beneficial owners, risk breakdown, screening history
- transaction timeline from ClickHouse with filters
- counterparty graph (one-hop, then two-hop)
- related cases
- documents and notes
**Quick actions:**
- request information from the customer (RFI) via a tenant webhook; the tenant's app collects the answer, with no mention of suspicion;
- request EDD;
- restrict the account (via webhook `account.restriction.requested`);
- propose a decision.
### 4.3 Decision → report
1. `REPORT_*` decision approved (or committee vote REPORT) → the reporting engine creates a **draft STR** pre-filled from the case: parties with country role codes, transactions, amounts in the required units, narrative sections, attachments flagged for inclusion.
2. The compliance officer finalises it in the reporting UI (<mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/>) → files it → the receipt links back to the case → the case closes.
### 4.4 Freeze workflows (from screening)
Country-specific automated playbooks, executed as a Temporal workflow with human checkpoints:
- **MX:** suspend services immediately (webhook) → 24-hour unusual-op report with the "Lista de Personas Bloqueadas" legend → written notice to the customer (template) → close.
- **BR:** freeze (webhook) → immediate notices to BCB (BC Correio template), Ministry of Justice and COAF (Siscoaf communication).
- **AR:** self-freeze → RFT within 24 h (filing package) → follow-up freeze-order reporting.
- **PE:** communicate to UIF-Perú without delay → execute the UIF freeze order → "no funds" response if applicable.
- **CL:** immediate STR → await court order (UAF request).
- **CO:** STR to UIAF + notice to the Fiscalía.
### 4.5 Narrative assistant
- **Template per regulator:** sections, guidance text, min/max lengths, mandatory questions.
- **Auto-drafted facts** (deterministic, from data): chronology of transactions, totals per instrument, profile vs actual, alerts fired, prior STRs.
- **Optional LLM drafting** (phase 2, tenant opt-in):
	- The model rewrites the facts into fluent Spanish or Portuguese within the length limits.
	- Inputs are **only the case's structured facts**; nothing leaves the cell.
	- The analyst must edit and confirm, and the final text is attributed to them.
	- Deployed on a model hosted in-region, or disabled where residency rules forbid it.
---
## 5. API & UI
<table header-row="true">
<tr>
<td>Method</td>
<td>Path</td>
<td>Purpose</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/alerts?status=&queue=`</td>
<td>Inbox (staff)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/alerts/{id}/close` · `/escalate` · `/link`</td>
<td>Triage actions</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/cases/{id}`</td>
<td>Case with parties, alerts, clocks</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/cases/{id}/decisions` · `/v1/cases/decisions/{id}/approve`</td>
<td>Four-eyes decisions</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/committee/sessions` · `/votes` · `/minutes`</td>
<td>MX committee</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/cases/{id}/narratives`</td>
<td>Save narrative version</td>
</tr>
<tr>
<td>`GET/POST`</td>
<td>`/v1/regulator-requests`</td>
<td>Request tracker</td>
</tr>
</table>
**Webhooks to tenant** (no suspicion details):
- `account.restriction.requested`
- `account.freeze.requested`
- `customer.rfi.requested`
- `customer.exit.requested`
**UI:** React console with keyboard-first triage, split view (alert list \| evidence), bulk close with reason codes (permission-gated), SLA badges.
---
## 6. Metrics (per tenant, feed EWRA & effectiveness)
- Alert volume per source/rule; median time to triage; alert→case %, case→STR %.
- SLA met % per clock type; overdue counts.
- QA: monthly random sample of 5% of closed alerts re-reviewed; disagreement rate.
- Analyst productivity (alerts/hour) is shown only to managers.
## 7. Non-functional
<table header-row="true">
<tr>
<td>Item</td>
<td>Target</td>
</tr>
<tr>
<td>Inbox load</td>
<td>p95 \< 400 ms for 50 items</td>
</tr>
<tr>
<td>Case view</td>
<td>p95 \< 1 s including transaction timeline (ClickHouse)</td>
</tr>
<tr>
<td>Audit</td>
<td>Every view of a RESTRICTED case is logged (`VIEW_RESTRICTED_CASE`)</td>
</tr>
</table>
## 8. Testing
- **State-machine property tests:** no path to `REPORT_FILED` without an approved decision.
- **Clock tests per country:** holidays; the AR 90-day hard cap.
- **ACL tests:** an analyst who isn't a member cannot see RESTRICTED cases through any endpoint, search or export.
- **Freeze playbook tests** per country, using mocked tenant webhooks.
## 9. Build plan
<table header-row="true">
<tr>
<td>Milestone</td>
<td>Scope</td>
<td>Eng-weeks</td>
</tr>
<tr>
<td>CM-1</td>
<td>Alert inbox, case model, grouping, queues, ACL/visibility</td>
<td>3</td>
</tr>
<tr>
<td>CM-2</td>
<td>Decisions with four-eyes, clocks per country, escalations</td>
<td>2</td>
</tr>
<tr>
<td>CM-3</td>
<td>Investigation workspace (timeline, graph one-hop, documents, notes)</td>
<td>3</td>
</tr>
<tr>
<td>CM-4</td>
<td>MX committee module; narrative templates (MX/BR first); regulator request tracker</td>
<td>2</td>
</tr>
<tr>
<td>**Total v1**</td>
<td></td>
<td>**≈ 10**</td>
</tr>
<tr>
<td>Phase 2</td>
<td>Freeze playbooks for all countries, LLM narrative drafting, QA module</td>
<td>4–5</td>
</tr>
</table>
## 10. Open questions
- Is the client's own case tool (if any) the system of record? Proposal: we are, with export/webhooks for clients who insist on their own.
- LLM narrative drafting: which model hosting meets residency in MX/BR? Default off.
