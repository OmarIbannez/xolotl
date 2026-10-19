# Requirements & planning phases (SDLC)
Source: https://app.notion.com/3fde5630e5e3817baf46c1af561a502b

**Date:** 2026-10-17 · **Status:** Proposal
**Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e381bebc9ff87732b6439c"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e381fda6a5de38d932d48d"/>
---
## 0. Approach in one paragraph
This is a **regulated product**, so we run a **hybrid lifecycle**:
- **Regulatory requirements** get a formal baseline: every one is traceable from law → requirement → feature → test → evidence, and changes are controlled.
- **Product and UX requirements** stay agile: refined every sprint with design partners.
The requirements and planning phases take about **6 weeks**. They overlap with Sprint 0, because foundations work (cloud, tenancy, encryption) is low-regret and doesn't depend on open requirement decisions.
```javascript
Week:          1     2     3     4     5     6     7 …
Requirements   ███████████████████████████████          (then continuous refinement)
Planning                   ███████████████████
Sprint 0 (foundations)           ███████████
Sprint 1+                                    ███████ …
Gates                                   G1 ▲        G2 ▲
```
- **G1 — Requirements baseline** (end of week 5)
- **G2 — Plan approved / build go** (end of week 6)
---
## 1. What we already have vs. what is missing
<table header-row="true">
<tr>
<td>Already done</td>
<td>Missing for a proper requirements baseline</td>
</tr>
<tr>
<td>Regulatory research for 17 countries (`docs/research/raw/`), with confidence tags</td>
<td>**Verification** of UNVERIFIED/SECONDARY items that the Mexico launch depends on (legal + design partner)</td>
</tr>
<tr>
<td>Feature list, executive summaries, technical designs per feature</td>
<td>**Validation with real users**: compliance officers, analysts, client developers</td>
</tr>
<tr>
<td>Architecture, data model, infrastructure sizing</td>
<td>**Non-functional targets agreed** with design partners (latency, uptime, residency)</td>
</tr>
<tr>
<td>Draft sprint plan</td>
<td>**Regulatory traceability matrix**, user stories with acceptance criteria, UI prototypes, API mock</td>
</tr>
<tr>
<td>Vendor/registry research</td>
<td>**Commercial requirements**: pricing model, SLAs, support, contracts</td>
</tr>
</table>
---
## 2. Requirements phase (weeks 1–5)
### 2.1 Stakeholders & roles
<table header-row="true">
<tr>
<td>Stakeholder</td>
<td>Why we need them</td>
<td>How we engage</td>
</tr>
<tr>
<td>**Design-partner compliance officers (MLRO / Oficial de Cumplimiento)**</td>
<td>Own the legal obligations; sign reports</td>
<td>Interviews, workflow shadowing, sign-off of the Mexico scope</td>
</tr>
<tr>
<td>**Design-partner analysts**</td>
<td>Daily users of alerts, cases and reviews</td>
<td>Shadow 1–2 days of alert handling; usability tests on prototypes</td>
</tr>
<tr>
<td>**Design-partner engineers**</td>
<td>Integrate our API/SDK</td>
<td>API mock review, integration constraints (volumes, rails, data available)</td>
</tr>
<tr>
<td>**Design-partner risk/fraud & product**</td>
<td>Onboarding conversion, fraud losses</td>
<td>Journey mapping, conversion targets</td>
</tr>
<tr>
<td>**Auditors / external reviewers**</td>
<td>Decide what evidence is "enough"</td>
<td>1 interview per country (or a former regulator / consultant)</td>
</tr>
<tr>
<td>**Our legal counsel**</td>
<td>INE access, data protection, outsourcing, credential custody</td>
<td>Legal opinions with dates</td>
</tr>
<tr>
<td>**Our compliance analyst**</td>
<td>Owns the regulatory catalog and policy packs</td>
<td>Author of REG requirements</td>
</tr>
<tr>
<td>**Industry bodies** (Asociación Fintech México, ABFintechs, Colombia Fintech)</td>
<td>Market practice, regulator expectations</td>
<td>Informal sessions</td>
</tr>
</table>
### 2.2 Elicitation activities
<table header-row="true">
<tr>
<td>#</td>
<td>Activity</td>
<td>Output</td>
<td>Weeks</td>
</tr>
<tr>
<td>E1</td>
<td>**Structured interviews** (8–12) with design partners, using a guide: current process, tools, pain points, volumes, reports filed, audit findings, must-haves</td>
<td>Interview notes, pain-point list</td>
<td>1–3</td>
</tr>
<tr>
<td>E2</td>
<td>**Contextual observation**: watch analysts work alerts and file a report (SITI / SAT portal)</td>
<td>Current-state process maps, time per task</td>
<td>2–3</td>
</tr>
<tr>
<td>E3</td>
<td>**Artifact collection** (masked): past filings, portal receipts, templates behind logins, audit reports, AML manuals</td>
<td>Fills the gaps listed in the research files</td>
<td>1–5</td>
</tr>
<tr>
<td>E4</td>
<td>**Regulatory verification**: compliance analyst + legal confirm each Mexico-launch rule marked UNVERIFIED/SECONDARY against the official text</td>
<td>Updated confidence tags; legal opinions</td>
<td>1–4</td>
</tr>
<tr>
<td>E5</td>
<td>**Competitive teardown**: 3–4 incumbents used by partners (demos, docs)</td>
<td>Gaps and differentiators</td>
<td>2–3</td>
</tr>
<tr>
<td>E6</td>
<td>**Volume & data survey** per partner: customers, transactions/day, peaks, rails, data fields available</td>
<td>Inputs for sizing (T1/T2) and the transaction schema</td>
<td>2</td>
</tr>
</table>
### 2.3 Analysis & specification: requirement types
<table header-row="true">
<tr>
<td>Type</td>
<td>ID pattern</td>
<td>Example</td>
<td>Where it lives</td>
</tr>
<tr>
<td>**Regulatory**</td>
<td>`REG-<CC>-<NNN>`</td>
<td>`REG-MX-014`: fintechs file a quarterly relevant-operations report (cash ≥ USD 5,000) within the first 10 business days, empty report if none — ITF DCG Art. 66</td>
<td>Regulatory catalog (§2.4)</td>
</tr>
<tr>
<td>**Functional**</td>
<td>`FR-<feature>-<NNN>` as epics/user stories</td>
<td>`FR-RR-021`: as a compliance officer, I approve a report draft and download the file for SITI</td>
<td>Issue tracker</td>
</tr>
<tr>
<td>**Non-functional**</td>
<td>`NFR-<NNN>`</td>
<td>`NFR-003`: sync screening p95 \< 300 ms</td>
<td>NFR spec</td>
</tr>
<tr>
<td>**Data**</td>
<td>`DR-<NNN>`</td>
<td>`DR-010`: CURP stored encrypted with blind index</td>
<td>Data dictionary (from data model)</td>
</tr>
<tr>
<td>**Integration**</td>
<td>`INT-<NNN>`</td>
<td>`INT-004`: INE verification via vendor, 20 s timeout, async retry 30 min</td>
<td>Interface catalog</td>
</tr>
<tr>
<td>**Security / privacy**</td>
<td>`SEC-<NNN>` / `PRV-<NNN>`</td>
<td>`PRV-002`: biometric consent per LFPDPPP 2025 before capture</td>
<td>Security & privacy spec</td>
</tr>
<tr>
<td>**Commercial / operational**</td>
<td>`OPS-<NNN>`</td>
<td>`OPS-005`: support response 4 h for P1</td>
<td>SLA draft</td>
</tr>
</table>
### 2.4 Regulatory requirements catalog (the core artifact)
One record per legal obligation:
```yaml
id: REG-MX-014
title: Quarterly relevant-operations report (fintech)
jurisdiction: MX
regulator: CNBV / UIF
applies_to: [MX/ITF-IFPE, MX/ITF-IFC]
legal_basis: "DCG Art. 58 LRITF, Art. 66; Formato 41 Resolution (DOF 14-12-2004, rev. 01-08-2013)"
obligation: >
  Report cash operations ≥ USD 5,000 (Banxico FIX prior banking day) in Formato 41,
  quarterly, within the first 10 business days of Jan/Apr/Jul/Oct; empty report if none.
effective_from: 2018-09-10
confidence: VERIFIED            # VERIFIED | SECONDARY | UNVERIFIED
verified_by: compliance-analyst + legal
evidence_url: https://www.gob.mx/cms/uploads/attachment/file/909810/…
priority: MUST (MX launch)
implemented_by: [TM capture MX_ITF_ROR_CASH, RR MX_FORMATO41_ROR@2004.5, OB MX_ITF_ROR_Q]
tests: [golden/MX_FORMATO41_ROR/*, capture/MX_ITF_ROR_CASH/*, calendar/MX/2027]
status: BASELINED
```
**Traceability matrix:** a generated view `REG → FR → design section → test → evidence`. Auditors and enterprise buyers will ask for it, and it is a sales asset.
**Seed:** about 120–150 REG items for Mexico, extracted from the raw research files. Brazil follows in its own phase.
### 2.5 Functional requirements: personas → journeys → stories
**Personas:**
- **Valeria**, compliance officer at a Mexican e-money fintech.
- **Diego**, AML analyst.
- **Ana**, backend developer integrating the API.
- **Carlos**, SME owner onboarding his company.
- **María**, end customer opening a wallet.
- **Auditor**, external reviewer.
- **Ops**, platform on-call.
**Story format** with Gherkin acceptance criteria, linked to REG items:
```gherkin
Feature: Quarterly empty report (REG-MX-014)
  Scenario: No reportable cash operations in the quarter
    Given tenant "FintechX" with policy pack MX/ITF-IFPE
    And no reportable events for period 2027Q1
    When the period closes
    Then a draft MX_FORMATO41_ROR run with is_zero = true is created
    And it contains only fields 1,2,4,5
    And its due date is the 10th business day of April 2027 (MX calendar)
```
**Prototypes (validated with partners in weeks 3–5):**
- **Figma** prototypes for review, case, report-approval and business-onboarding screens.
- An **OpenAPI mock** (e.g. Prism) for the onboarding, transactions and screening APIs.
### 2.6 Non-functional requirements (agree numbers with partners)
<table header-row="true">
<tr>
<td>Area</td>
<td>Proposed (from designs)</td>
<td>To confirm</td>
</tr>
<tr>
<td>Sync payment decision</td>
<td>p99 ≤ 100 ms</td>
<td>Partner's own SPEI timeout budget</td>
</tr>
<tr>
<td>Screening</td>
<td>p95 \< 300 ms; UN/OFAC refresh ≤ 30 min</td>
<td>—</td>
</tr>
<tr>
<td>Availability</td>
<td>99.95% per cell</td>
<td>Contractual SLA level</td>
</tr>
<tr>
<td>RPO / RTO</td>
<td>≤ 5 min / ≤ 4 h</td>
<td>Whether in-country disaster recovery is required</td>
</tr>
<tr>
<td>Data residency</td>
<td>MX data in Mexico</td>
<td>Per partner contract</td>
</tr>
<tr>
<td>Retention</td>
<td>10 years (MX)</td>
<td>—</td>
</tr>
<tr>
<td>Volumes</td>
<td>T1: 500 tx/s peak, 2M customers</td>
<td>Survey E6</td>
</tr>
<tr>
<td>Security</td>
<td>SOC 2 Type I at launch, Type II at 12 months; annual pen test</td>
<td>Partner procurement checklist</td>
</tr>
</table>
### 2.7 Prioritization
- **MoSCoW for the Mexico launch:**
	- **MUST** = legal obligations of the launch segments, plus anything the design partner can't go live without.
	- **SHOULD** = efficiency (simulation, narrative templates).
	- **COULD** = differentiators (ML, LLM triage).
- **Regulatory dates drive the order:**
	<table header-row="true">
<tr>
<td>Date</td>
<td>Mexico change</td>
</tr>
<tr>
<td>30-11-2026</td>
<td>New AML rules (Acuerdo 115/2026) in force</td>
</tr>
<tr>
<td>01-03-2027</td>
<td>Risk, KYC and beneficial-owner chapters apply</td>
</tr>
<tr>
<td>01-06-2027</td>
<td>New XML formats + automated monitoring</td>
</tr>
<tr>
<td>\~Nov 2026</td>
<td>Bank facial biometrics</td>
</tr>
	</table>
### 2.8 Validation & baseline (Gate G1, end of week 5)
**Exit criteria:**
- [ ] REG catalog for Mexico: every MUST item **VERIFIED**, or explicitly accepted as a risk by legal
- [ ] Design partner signs off on the Mexico scope (MUST list) and the NFR numbers
- [ ] Prototypes tested with ≥ 5 users; critical usability issues resolved
- [ ] OpenAPI mock reviewed by ≥ 1 partner engineering team
- [ ] Legal opinions delivered: INE access path, credential custody, data-processing agreement, outsourcing
- [ ] Open questions log: every item has an owner and a due date
**Change control after baseline:**
- REG changes go through a **change board** (PM + compliance analyst + engineering lead), meeting weekly or ad hoc for urgent law changes.
- The regulatory watcher (RR-12) feeds the board.
- Product stories change freely through backlog refinement.
---
## 3. Planning phase (weeks 3–6)
### 3.1 Activities & deliverables
<table header-row="true">
<tr>
<td>#</td>
<td>Activity</td>
<td>Deliverable</td>
</tr>
<tr>
<td>P1</td>
<td>**Project charter**: goals, scope (Mexico launch), out of scope, success metrics, budget envelope, sponsors</td>
<td>Charter (2 pages)</td>
</tr>
<tr>
<td>P2</td>
<td>**Release plan**: MVP (pilot) → GA Mexico → Brazil → CO/AR → CL/PE → rest</td>
<td>Roadmap with milestones M1–M6 (<mention-page url="https://app.notion.com/p/3fde5630e5e381bebc9ff87732b6439c"/>)</td>
</tr>
<tr>
<td>P3</td>
<td>**Work breakdown**: epics from feature designs → tasks per sprint (done) → stories (each sprint)</td>
<td>Backlog with epics T0.x–T10.x</td>
</tr>
<tr>
<td>P4</td>
<td>**Estimation**: re-estimate epics with the squads (planning poker on t-shirt sizes), add **20–25% contingency**, re-base the calendar (December holidays)</td>
<td>Updated delivery plan</td>
</tr>
<tr>
<td>P5</td>
<td>**Team & hiring plan**: 8 engineers + PM + compliance analyst + designer; hiring order (platform + onboarding first)</td>
<td>Hiring plan, RACI</td>
</tr>
<tr>
<td>P6</td>
<td>**Dependency & critical-path plan**</td>
<td>Dependency map (delivery plan §7)</td>
</tr>
<tr>
<td>P7</td>
<td>**Risk register** (§3.3)</td>
<td>Register with owners, reviewed every 2 weeks</td>
</tr>
<tr>
<td>P8</td>
<td>**Budget**: people, cloud (T1 ≈ US\$11–16k/month/cell), vendors (identity, data, OpenSanctions, PEP vendor), legal, SOC 2, pen test</td>
<td>Budget v1</td>
</tr>
<tr>
<td>P9</td>
<td>**Procurement plan**: vendor RFPs and contract dates aligned to sprint needs</td>
<td>Vendor timeline</td>
</tr>
<tr>
<td>P10</td>
<td>**Quality plan**: test strategy (unit, golden files, isolation, matcher evaluation, load, security), Definition of Ready/Done, regulator test filings</td>
<td>Test strategy + DoR/DoD</td>
</tr>
<tr>
<td>P11</td>
<td>**Security & privacy plan**: threat model, DPIA per cell, SOC 2 roadmap</td>
<td>Security plan</td>
</tr>
<tr>
<td>P12</td>
<td>**Governance & communication**</td>
<td>Cadence below</td>
</tr>
<tr>
<td>P13</td>
<td>**Go/no-go criteria** for pilot and GA</td>
<td>Checklist (§3.5)</td>
</tr>
</table>
### 3.2 Governance cadence
<table header-row="true">
<tr>
<td>Forum</td>
<td>Who</td>
<td>Frequency</td>
<td>Purpose</td>
</tr>
<tr>
<td>Sprint planning / review / retro</td>
<td>Squads, PM</td>
<td>Every 2 weeks</td>
<td>Delivery</td>
</tr>
<tr>
<td>Change board (requirements)</td>
<td>PM, compliance analyst, engineering lead</td>
<td>Weekly</td>
<td>REG/scope changes</td>
</tr>
<tr>
<td>Design-partner sync</td>
<td>PM, partner compliance officer</td>
<td>Every 2 weeks</td>
<td>Feedback, demos, artifacts</td>
</tr>
<tr>
<td>Steering committee</td>
<td>Sponsors, PM, engineering lead, legal</td>
<td>Monthly</td>
<td>Budget, risks, milestone decisions</td>
</tr>
<tr>
<td>Regulatory review</td>
<td>Compliance analyst + legal</td>
<td>Monthly</td>
<td>Law changes → impact</td>
</tr>
</table>
### 3.3 Risk register (initial top 8)
<table header-row="true">
<tr>
<td>#</td>
<td>Risk</td>
<td>P</td>
<td>I</td>
<td>Owner</td>
<td>Mitigation</td>
</tr>
<tr>
<td>R1</td>
<td>No design partner by Sprint 2</td>
<td>M</td>
<td>H</td>
<td>PM</td>
<td>Pipeline of 5 prospects; incentive (free pilot); build on public specs</td>
</tr>
<tr>
<td>R2</td>
<td>INE biometric access unavailable for non-bank fintechs</td>
<td>M</td>
<td>H</td>
<td>Legal</td>
<td>Launch at A3 + video-call fallback; bank tenants bring their own convenio</td>
</tr>
<tr>
<td>R3</td>
<td>Report layouts behind regulator logins not obtained</td>
<td>H</td>
<td>M</td>
<td>Compliance analyst</td>
<td>Design partners; defer the affected reports past GA</td>
</tr>
<tr>
<td>R4</td>
<td>Identity vendor contract / pricing delays</td>
<td>M</td>
<td>M</td>
<td>PM</td>
<td>Fallback vendor; sandbox first</td>
</tr>
<tr>
<td>R5</td>
<td>Mexican rules change again before launch</td>
<td>M</td>
<td>M</td>
<td>Compliance analyst</td>
<td>Policy packs + regulatory watcher; monthly review</td>
</tr>
<tr>
<td>R6</td>
<td>Hiring delays</td>
<td>M</td>
<td>H</td>
<td>Engineering lead</td>
<td>Contractors for platform/frontend; staggered start</td>
</tr>
<tr>
<td>R7</td>
<td>False-positive rate too high on LATAM names</td>
<td>M</td>
<td>M</td>
<td>Screening lead</td>
<td>Evaluation corpus + calibration in Sprint 6</td>
</tr>
<tr>
<td>R8</td>
<td>Data-residency demands exceed the single-region design</td>
<td>L</td>
<td>M</td>
<td>Platform</td>
<td>Cross-provider backup from day 1; option C ready</td>
</tr>
</table>
### 3.4 Definition of Ready / Done
- **Ready:** story linked to an FR (and a REG if regulatory), acceptance criteria written, designs/prototype attached, dependencies resolved, estimated.
- **Done:**
	- code reviewed;
	- `inertclaude ci` green;
	- tenant-isolation tests pass;
	- golden files updated for reports/policies;
	- docs and API spec updated;
	- deployed to staging;
	- REG items traced to tests;
	- demoed.
### 3.5 Go/no-go criteria
<table header-row="true">
<tr>
<td>Gate</td>
<td>Criteria</td>
</tr>
<tr>
<td>**G2 — Build go** (end of week 6)</td>
<td>Charter + budget approved; REG baseline (G1) passed; team ≥ 6 engineers onboarded; identity-vendor sandbox access; plan re-estimated with contingency</td>
</tr>
<tr>
<td>**Pilot go** (\~Sprint 9)</td>
<td>All MUST REG items implemented and tested; test filing accepted (SITI QA / SAT); load & pen tests passed; design-partner UAT sign-off; runbooks + on-call</td>
</tr>
<tr>
<td>**GA go**</td>
<td>One real reporting period filed successfully; pilot SLOs met for 4 weeks; SOC 2 Type I report; pricing & contracts ready</td>
</tr>
</table>
---
## 4. Week-by-week (first 6 weeks)
<table header-row="true">
<tr>
<td>Week</td>
<td>Requirements</td>
<td>Planning</td>
<td>Engineering (Sprint 0)</td>
</tr>
<tr>
<td>1</td>
<td>Interview guide; first 3 interviews; artifact request list; start legal opinions</td>
<td>Charter draft; hiring kickoff</td>
<td>—</td>
</tr>
<tr>
<td>2</td>
<td>Interviews 4–8; observation sessions; volume survey</td>
<td>Vendor RFPs out; budget draft</td>
<td>Repo, CI, IaC start</td>
</tr>
<tr>
<td>3</td>
<td>REG catalog draft (MX) from research; personas & journeys</td>
<td>Risk register; dependency map</td>
<td>Tenancy + RLS + isolation harness</td>
</tr>
<tr>
<td>4</td>
<td>Prototypes + API mock; legal opinions in; regulatory verification</td>
<td>Re-estimation with squads</td>
<td>Crypto lib, outbox/Kafka</td>
</tr>
<tr>
<td>5</td>
<td>Prototype tests; NFR agreement; **G1 baseline**</td>
<td>Release plan v1; quality & security plans</td>
<td>Foundations demo</td>
</tr>
<tr>
<td>6</td>
<td>Backlog refinement for Sprints 1–2</td>
<td>**G2 steering approval**</td>
<td>Sprint 1 starts</td>
</tr>
</table>
