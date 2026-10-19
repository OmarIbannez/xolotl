# Delivery plan — sprints & tasks
Source: https://app.notion.com/3fde5630e5e381bebc9ff87732b6439c

**Date:** 2026-10-17 · **Status:** Draft for review
**Based on:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> (effort estimates) · <mention-page url="https://app.notion.com/p/3fde5630e5e38175b09bf9e362c690e5"/>
---
## 1. Assumptions
<table header-row="true">
<tr>
<td>Item</td>
<td>Assumption</td>
</tr>
<tr>
<td>Sprint length</td>
<td>**2 weeks**</td>
</tr>
<tr>
<td>Team (engineering)</td>
<td>**8 engineers** in 3 squads + 1 platform engineer + 1 frontend engineer (below)</td>
</tr>
<tr>
<td>Team (non-eng)</td>
<td>1 product manager, 1 **regulatory/compliance analyst** (owns policy packs and report definitions), 1 designer (part-time), legal counsel (part-time / external)</td>
</tr>
<tr>
<td>Task size</td>
<td>Each task is **1–2 weeks for 1–2 people**. Not broken down further here; squads split them in sprint planning</td>
</tr>
<tr>
<td>Scope order</td>
<td>**Mexico launch first** (≈ 85 eng-weeks), then Brazil, then the rest</td>
</tr>
<tr>
<td>Calendar</td>
<td>Sprint 0 starts the first week of Nov 2026. **Mexico pilot ≈ late March 2027, GA ≈ April 2027**</td>
</tr>
</table>
### Squads
<table header-row="true">
<tr>
<td>Squad</td>
<td>People</td>
<td>Owns</td>
</tr>
<tr>
<td>**Platform**</td>
<td>1 platform eng (+ help from all squads in Sprint 0–1)</td>
<td>Cloud/IaC, CI/CD, foundations libraries, observability, security</td>
</tr>
<tr>
<td>**Onboarding**</td>
<td>2 backend</td>
<td>01 Customer onboarding, 02 Business onboarding</td>
</tr>
<tr>
<td>**Screening & Risk**</td>
<td>2 backend</td>
<td>03 Screening, 04 Risk scoring</td>
</tr>
<tr>
<td>**Monitoring & Reporting**</td>
<td>3 backend</td>
<td>05 Transaction monitoring, 07 Regulatory reporting, 08 Compliance calendar</td>
</tr>
<tr>
<td>**Console (frontend)**</td>
<td>1 frontend</td>
<td>Console shell, review UI, case workspace, reporting UI, hosted business app</td>
</tr>
</table>
Case management (06) is shared: backend from Screening & Risk, UI from Console.
---
## 2. Timeline at a glance
```javascript
            Nov        Dec        Jan        Feb        Mar        Apr        May   Jun   Jul   Aug   Sep
Sprint      0     1     2     3     4     5     6     7     8     9     10   | 11 … 15            | 16 …
Platform    ████ foundations ████ ── cell infra, observability ───── hardening ── | BR cell          |
Onboarding        ██ core ██ vendor+SDK ██ MX checks ██ review/step-up ██ KYB ███ | BR Serpro/QSA    |
Screening         ██ ingest ██ normalize ██ matcher ██ delta/LPB ██ eval ██ freeze | BR lists         |
Risk                    ██ scorecard ██ triggers ███████████████ overrides/sim █ | EWRA module      |
Monitoring              ██ ingest ██ rules/state ██ captures ██ sync API ██ sim  | BR captures, Pix |
Reporting                           ██ engine ██ Formato 41 ██ avisos XML ██ filing| Siscoaf SOAP     |
Cases/UI          ██ shell ██ review UI ██ inbox/cases ██ committee ██ workspace █ | BR playbooks     |
Milestones              M1           M2               M3           M4      M5 pilot  GA MX | M6 BR pilot
```
<table header-row="true">
<tr>
<td>Milestone</td>
<td>When</td>
<td>Meaning</td>
</tr>
<tr>
<td>**M1 — Skeleton live**</td>
<td>End Sprint 1</td>
<td>MX cell running; tenant isolation proven; MX policy pack v1 loads; a sandbox tenant can call the API</td>
</tr>
<tr>
<td>**M2 — First onboarding end-to-end**</td>
<td>End Sprint 4</td>
<td>A Mexican customer is onboarded in sandbox: document + liveness + INE/RENAPO + screening + risk + decision</td>
</tr>
<tr>
<td>**M3 — Detect & investigate**</td>
<td>End Sprint 6</td>
<td>Transactions in → alerts → cases → decisions; full LATAM name matching with delta re-screening</td>
</tr>
<tr>
<td>**M4 — Report & file**</td>
<td>End Sprint 8</td>
<td>Formato 41 and LFPIORPI avisos generated, validated, approved and filed in test environments; compliance calendar live</td>
</tr>
<tr>
<td>**M5 — Pilot**</td>
<td>Sprint 9–10</td>
<td>Hardening done; design partner(s) live in production with real customers</td>
</tr>
<tr>
<td>**GA Mexico**</td>
<td>\~April 2027</td>
<td>Open to new Mexican clients</td>
</tr>
<tr>
<td>**M6 — Brazil pilot**</td>
<td>\~Sprint 15 (July 2027)</td>
<td>Brazil cell live with Siscoaf direct filing</td>
</tr>
</table>
---
## 3. Non-engineering track (starts immediately; several items are on the critical path)
<table header-row="true">
<tr>
<td>#</td>
<td>Task</td>
<td>Owner</td>
<td>Needed by</td>
<td>Why critical</td>
</tr>
<tr>
<td>N1</td>
<td>**Recruit design partners**: 1–2 MX fintechs (one regulated fintech/ITF, one non-financial "actividad vulnerable" business), 1 BR</td>
<td>PM / CEO</td>
<td>Sprint 2</td>
<td>Report templates behind regulator logins; real-portal validation; pilot</td>
</tr>
<tr>
<td>N2</td>
<td>**Legal opinion**: INE biometric checks via vendor for non-bank fintechs; data-processor contracts; e.firma/credential policy</td>
<td>Legal</td>
<td>Sprint 3</td>
<td>Decides the MX onboarding assurance path</td>
</tr>
<tr>
<td>N3</td>
<td>**Identity vendor RFP + contract** (Incode primary; Veridas/FacePhi fallback; RENAPO/SAT data vendor)</td>
<td>PM + Onboarding lead</td>
<td>Sprint 2 (sandbox), Sprint 6 (prod contract)</td>
<td>Onboarding build depends on it</td>
</tr>
<tr>
<td>N4</td>
<td>**Screening data**: OpenSanctions Reseller/OEM quote; PEP/adverse-media vendor RFP with LATAM recall test</td>
<td>PM + Screening lead</td>
<td>Sprint 3 (OpenSanctions), Sprint 8 (vendor)</td>
<td>Licence needed before production</td>
</tr>
<tr>
<td>N5</td>
<td>**Cloud & compliance**: AWS account structure, MX region, DPA templates, LFPDPPP notices, start **SOC 2 Type I**</td>
<td>Platform + Legal</td>
<td>Sprint 1 → Sprint 10</td>
<td>Fintech procurement asks for these</td>
</tr>
<tr>
<td>N6</td>
<td>**Regulatory content**: MX policy pack, report definitions, code tables, obligations catalog; collect templates/layouts via design partners (UIF FX-cash, international transfers, crypto layouts; 2027 aviso XSDs)</td>
<td>Compliance analyst</td>
<td>Rolling, Sprint 1 → 8</td>
<td>Gaps listed in the research files</td>
</tr>
<tr>
<td>N7</td>
<td>**Brazil prep**: Serpro contract (CPF, CNPJ, Datavalid), ICP-Brasil cloud-certificate provider, BR legal entity / DPA under LGPD</td>
<td>PM + Legal</td>
<td>Sprint 10</td>
<td>Brazil phase starts Sprint 11</td>
</tr>
</table>
---
## 4. Mexico launch — sprint plan
Task IDs reference design docs: **F** = <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/>, **ON** = <mention-page url="https://app.notion.com/p/3fde5630e5e3813385c3c590786261ed"/>, **KB** = <mention-page url="https://app.notion.com/p/3fde5630e5e381afbeb3c7f4bf0fdbf5"/>, **SC** = <mention-page url="https://app.notion.com/p/3fde5630e5e3814da2f5ce4e03addb79"/>, **R** = <mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/>, **TM** = <mention-page url="https://app.notion.com/p/3fde5630e5e3817b9987f2a49b2bd540"/>, **CM** = <mention-page url="https://app.notion.com/p/3fde5630e5e3814783fbe56ba6027f61"/>, **RR** = <mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/>, **OB** = <mention-page url="https://app.notion.com/p/3fde5630e5e381f5b59bdd8bfb681a96"/>.
### Sprint 0 — Foundations I *(all hands on platform)*
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T0.1 Repo, CI (`inertclaude ci`), IaC for the MX cell (VPC, EKS, Aurora, MSK, Redis, S3, KMS)</td>
<td>Platform</td>
<td>`dev`  • `staging` environments deploy from main</td>
</tr>
<tr>
<td>T0.2 Tenant context, row-level security, migration framework, **tenant-isolation test harness** (F1)</td>
<td>Onboarding</td>
<td>Isolation test runs in CI for every table</td>
</tr>
<tr>
<td>T0.3 Crypto lib: envelope encryption, blind indexes, KMS per tenant (F2)</td>
<td>Screening & Risk</td>
<td>Encrypted field type + blind index used by a sample table</td>
</tr>
<tr>
<td>T0.4 Outbox + Kafka + event envelope; API conventions (auth, idempotency, errors) (F5)</td>
<td>Monitoring & Reporting</td>
<td>Sample event published end-to-end; OpenAPI generated</td>
</tr>
</table>
### Sprint 1 — Foundations II → **M1**
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T1.1 Policy-pack models + resolver + snapshots; **MX pack v1** (fintech + non-financial) with golden tests (F3)</td>
<td>Platform + compliance analyst</td>
<td>Effective policy resolves for a sample tenant</td>
</tr>
<tr>
<td>T1.2 Units service (UMA, UDI, Banxico FIX) + regulatory calendar (MX holidays, deadline rules) (F4)</td>
<td>Monitoring & Reporting</td>
<td>Threshold conversions and due dates pass golden tests</td>
</tr>
<tr>
<td>T1.3 Connector SDK + sandbox mock framework + cost metering (F6)</td>
<td>Onboarding</td>
<td>A mock connector runs with retries, evidence, metering</td>
</tr>
<tr>
<td>T1.4 Console shell: SSO, roles, tenant admin, API keys</td>
<td>Console</td>
<td>Staff user logs in, creates API key</td>
</tr>
<tr>
<td>T1.5 Core data model migrations (party, identifiers, relationships, documents, audit log)</td>
<td>Screening & Risk</td>
<td>Schema matches data-model doc; audit events written</td>
</tr>
</table>
### Sprint 2 — Core engines
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T2.1 Onboarding core: application API, journey engine, Temporal workflow skeleton, decision engine (ON-1)</td>
<td>Onboarding</td>
<td>Journey generated from MX pack; workflow runs with mock checks</td>
</tr>
<tr>
<td>T2.2 List ingestor + sources UN, OFAC, EU, UK, SAT 69-B (SC-1, part 1)</td>
<td>Screening & Risk</td>
<td>Versions + diffs stored; freshness dashboard</td>
</tr>
<tr>
<td>T2.3 Risk scorecard evaluator + MX factor tables + sync API (R-M1)</td>
<td>Screening & Risk</td>
<td>Golden profiles produce expected ratings</td>
</tr>
<tr>
<td>T2.4 Transaction canonical schema, ingest API (batch + file), enrichment (TM-1, part 1)</td>
<td>Monitoring & Reporting</td>
<td>Transactions land in Kafka enriched with UMA/UDI/USD</td>
</tr>
<tr>
<td>T2.5 Review queue UI (generic task list + evidence viewer)</td>
<td>Console</td>
<td>Reviewer can open an application and see evidence</td>
</tr>
</table>
### Sprint 3 — Vendors & matching
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T3.1 Identity vendor adapter (Incode) + SDK wrapper (iOS/Android/Web) + capture API + biometric vault + injection-attack checks (ON-2)</td>
<td>Onboarding</td>
<td>Sandbox selfie + INE capture produce document/liveness/face results</td>
</tr>
<tr>
<td>T3.2 Name normalization lib: particles, nicknames, LATAM phonetic key, surname frequency weights; screening profiles (SC-2)</td>
<td>Screening & Risk</td>
<td>Unit pair-corpus passes</td>
</tr>
<tr>
<td>T3.3 Rule language + compiler + bucketed state + async alerts with evidence (TM-2)</td>
<td>Monitoring & Reporting</td>
<td>Structuring scenario fires the expected alert</td>
</tr>
<tr>
<td>T3.4 Risk async triggers + review-date scheduling (R-M2, part 1)</td>
<td>Screening & Risk</td>
<td>Rating change publishes events and next review date</td>
</tr>
</table>
### Sprint 4 — Mexico checks & matcher → **M2**
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T4.1 MX registry checks: INE (via vendor), RENAPO, SAT RFC/CSF, CLABE ownership, geolocation; MX journeys (N1/N2/full) (ON-3)</td>
<td>Onboarding</td>
<td>MX sandbox journey reaches A3/A4 with reason codes</td>
</tr>
<tr>
<td>T4.2 Matcher: OpenSearch retrieval + Python scoring + bands + whitelist + sync screening API (SC-3)</td>
<td>Screening & Risk</td>
<td>Onboarding calls screening \< 300 ms p95 in staging</td>
</tr>
<tr>
<td>T4.3 Reportable-event capture + MX captures (fintech/bank cash, FX cash, international transfers, crypto, non-financial accumulation) (TM-3)</td>
<td>Monitoring & Reporting</td>
<td>Period test data produces expected reportable events</td>
</tr>
<tr>
<td>T4.4 Case management core: alert inbox, cases, grouping, queues, restricted visibility (CM-1)</td>
<td>Screening & Risk (backend) + Console</td>
<td>Alerts from monitoring and screening land in queues</td>
</tr>
</table>
### Sprint 5 — Completing flows
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T5.1 Step-up, re-KYC refresh, consent records, duplicates, device risk, evidence pack (ON-5)</td>
<td>Onboarding</td>
<td>Level 1 → Level 2 upgrade reuses prior evidence</td>
</tr>
<tr>
<td>T5.2 Delta/reverse screening, periodic re-screen workflow, **private lists (LPB upload)** (SC-4)</td>
<td>Screening & Risk</td>
<td>New UN entry alerts the matching customer within 5 min</td>
</tr>
<tr>
<td>T5.3 Case decisions with four-eyes, **country deadline clocks**, **MX compliance committee** (CM-2, CM-4 part)</td>
<td>Screening & Risk + Console</td>
<td>Committee vote → decision → report draft trigger</td>
</tr>
<tr>
<td>T5.4 Reporting engine core: definition registry, code tables, run workflow, approval, artifacts, receipts (RR-1)</td>
<td>Monitoring & Reporting</td>
<td>A dummy definition runs end-to-end to an archived artifact</td>
</tr>
</table>
### Sprint 6 — Business onboarding & quality → **M3**
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T6.1 KYB core: ownership graph, effective-ownership + beneficial-owner engine, reconciliation; MX CSF QR + 69-B (KB-1, KB-3 part)</td>
<td>Onboarding</td>
<td>Sample MX company → beneficial owners at 25% with gaps flagged</td>
</tr>
<tr>
<td>T6.2 Matching evaluation corpus v1 + yente shadow mode + calibration report (SC-5)</td>
<td>Screening & Risk</td>
<td>Recall/precision per list type published; thresholds set</td>
</tr>
<tr>
<td>T6.3 Serializers (delimited, XML/XSD, JSON package) + **Formato 41 ROR/ROI/24-hour/ROIP** incl. empty reports (RR-2, RR-4 part)</td>
<td>Monitoring & Reporting</td>
<td>Golden files byte-identical; business rules enforced</td>
</tr>
<tr>
<td>T6.4 Investigation workspace: transaction timeline, one-hop graph, documents, notes (CM-3)</td>
<td>Console + Screening & Risk</td>
<td>Analyst investigates a case without leaving the console</td>
</tr>
</table>
### Sprint 7 — Mexico reports & real-time
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T7.1 KYB: acta/poderes extraction (LLM + review), hosted business onboarding app with KYC invites (KB-3, KB-4)</td>
<td>Onboarding + Console</td>
<td>A business user completes a company onboarding with 2 beneficial owners</td>
</tr>
<tr>
<td>T7.2 Sync decision API + Redis aggregates + account-tier limits (UDI levels) (TM-4)</td>
<td>Monitoring & Reporting</td>
<td>p99 ≤ 100 ms under load in staging</td>
</tr>
<tr>
<td>T7.3 **LFPIORPI aviso XML** (current XSDs + 2027 formats), monthly zero reports, correction notices (RR-4 rest, RR-3)</td>
<td>Monitoring & Reporting</td>
<td>XSD-valid files for crypto (AVI) and 2 other activities</td>
</tr>
<tr>
<td>T7.4 Compliance calendar: obligation definitions (MX), generator, notifications, auto-evidence from reports (OB-1, OB-2)</td>
<td>Screening & Risk</td>
<td>MX tenant sees full-year calendar; filed report ticks obligation</td>
</tr>
</table>
### Sprint 8 — Filing & controls → **M4**
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T8.1 Filing tasks + receipt capture/parsing (SAT portal acuse, SITI), deadline dashboard; **test filing with design partner** (SITI QA / SAT)</td>
<td>Monitoring & Reporting</td>
<td>Design partner uploads our files successfully in a test environment</td>
</tr>
<tr>
<td>T8.2 ClickHouse history + rule simulation + effectiveness metrics (TM-5)</td>
<td>Monitoring & Reporting</td>
<td>Analyst simulates a rule over 6 months of data</td>
</tr>
<tr>
<td>T8.3 Risk overrides (four-eyes), model simulation & activation (R-M2 rest, R-M3)</td>
<td>Screening & Risk</td>
<td>Model v2 simulated → migration matrix → activated → re-score</td>
</tr>
<tr>
<td>T8.4 MX freeze playbook (LPB/UN → suspend → 24-hour report → client notice); webhooks hardening</td>
<td>Screening & Risk + Onboarding</td>
<td>End-to-end LPB hit drill passes</td>
</tr>
<tr>
<td>T8.5 Governance registries (compliance officer, training, policies) + board pack (OB-3)</td>
<td>Console + compliance analyst</td>
<td>Quarterly board pack generated</td>
</tr>
</table>
### Sprint 9 — Hardening
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T9.1 Load & resilience tests (2× launch peak), backup/restore drill, cross-provider backup copy, runbooks</td>
<td>Platform + all</td>
<td>SLOs met; restore \< 4 h proven</td>
</tr>
<tr>
<td>T9.2 Security: external penetration test, secrets review, PII-in-logs scan, access reviews; SOC 2 Type I evidence</td>
<td>Platform</td>
<td>No high findings open</td>
</tr>
<tr>
<td>T9.3 Design-partner UAT on staging with their real flows; fix list</td>
<td>All squads</td>
<td>UAT sign-off</td>
</tr>
<tr>
<td>T9.4 Developer docs, sandbox test identities, onboarding guide for client integration</td>
<td>Onboarding + PM</td>
<td>Partner integrates using docs only</td>
</tr>
</table>
### Sprint 10 — Pilot → **M5 / GA Mexico**
<table header-row="true">
<tr>
<td>Task</td>
<td>Squad</td>
<td>Done when</td>
</tr>
<tr>
<td>T10.1 Production go-live with design partner(s); hypercare rotation</td>
<td>All</td>
<td>Real customers onboarded and monitored</td>
</tr>
<tr>
<td>T10.2 First real reporting period handled (monthly zero report / aviso; quarterly ROR if aligned)</td>
<td>Monitoring & Reporting + compliance analyst</td>
<td>Filing accepted by the regulator</td>
</tr>
<tr>
<td>T10.3 Fix & polish backlog from pilot; GA checklist (pricing/metering, support, status page)</td>
<td>All</td>
<td>GA approved</td>
</tr>
</table>
---
## 5. Brazil phase (Sprints 11–15, ≈ 10 weeks) → **M6**
<table header-row="true">
<tr>
<td>Sprint</td>
<td>Tasks (one per squad)</td>
</tr>
<tr>
<td>**11**</td>
<td>BR cell infrastructure (São Paulo) · Serpro CPF + Datavalid direct connectors, BR capture vendor (unico/idwall) · BR list sources (CEIS/CNEP, CGU PEP with masked CPF) · Siscoaf SOAP adapter skeleton against homologation (`treina.siscoaf`)</td>
</tr>
<tr>
<td>**12**</td>
<td>BR journeys (A3/A4) + Pix DICT hook for participant tenants · Serpro CNPJ/QSA connectors + QSA recursion for beneficial owners · Siscoaf signing via ICP-Brasil cloud certificate + delegation records · BR captures: cash ≥ R\$ 50k, **FX cash / self-custody crypto ≥ US\$10k (Res. 591)**</td>
</tr>
<tr>
<td>**13**</td>
<td>Siscoaf definitions: suspicious (COS), automatic (COE), no-occurrence (CNO), corrections/cancellations · BR UN-freeze playbook (BC Correio / Ministry of Justice / COAF) · Pix MED 2.0 hooks · BR obligations (CNO, effectiveness report, action plan)</td>
</tr>
<tr>
<td>**14**</td>
<td>**Enterprise-wide risk assessment module** (MX + BR templates; AR ready) (R-M4) · Brazil typology library · LGPD consent/DPIA, Portuguese UI</td>
</tr>
<tr>
<td>**15**</td>
<td>Hardening + BR design-partner pilot; first Siscoaf filings in production</td>
</tr>
</table>
---
## 6. Later phases (epic level)
<table header-row="true">
<tr>
<td>Phase</td>
<td>Approx. sprints</td>
<td>Epics</td>
</tr>
<tr>
<td>**Colombia + Argentina**</td>
<td>16–21 (≈ 12 weeks)</td>
<td>CO: SIREL fixed-width/pipe annexes + absence reports, Supersociedades 2026 regime (UVB, quarterly AROS), RNEC certified biometric operator, RUES, national-lists aggregator. AR: RENAPER (bring-your-own convenio / VU Security), SMVM snapshot engine, RePET feed, self-freeze + 24 h RFT, SRO Masivo XML, risk self-assessment / external-reviewer packs, annual systematic report prefill</td>
</tr>
<tr>
<td>**Chile + Peru**</td>
<td>22–26 (≈ 10 weeks)</td>
<td>CL: cash-report files + **automated UAF channel**, MiUAF migration, NFC chip reading. PE: ROSEL/RO/IAOC templates, RENIEC (bring-your-own convenio), OFAC+EU mandatory screening, crypto travel rule. Crypto module (travel-rule vendor integration)</td>
</tr>
<tr>
<td>**Rest of LATAM**</td>
<td>27+</td>
<td>goAML serializer (Dominican Republic first), Ecuador (RESU, NO-ROS), Uruguay, Panama; then Costa Rica and Guatemala once regulations publish</td>
</tr>
<tr>
<td>**Phase-2 capabilities** (continuous, from \~Sprint 12)</td>
<td>—</td>
<td>Commercial PEP/adverse-media feed, LLM alert triage and narrative drafting, ML anomaly detection, graph analytics for mule rings, WhatsApp onboarding, reusable KYC</td>
</tr>
</table>
---
## 7. Dependencies & critical path
```javascript
N3 vendor sandbox (S2) ─► T3.1 SDK ─► T4.1 MX checks ─► M2
N2 legal opinion INE (S3) ─────────────► T4.1 assurance path decision
T2.2 lists ─► T3.2 normalize ─► T4.2 matcher ─► T5.2 delta/LPB ─► T6.2 calibration
T2.4 ingest ─► T3.3 rules ─► T4.3 captures ─► T6.3 Formato 41 / T7.3 avisos ─► T8.1 test filing ─► M4
N1 design partner (S2) ─► N6 templates (S4–S8) ─► T8.1 test filing ─► T9.3 UAT ─► pilot
```
**Top schedule risks and mitigations:**
<table header-row="true">
<tr>
<td>Risk</td>
<td>Mitigation</td>
</tr>
<tr>
<td>Design partner not signed by Sprint 2</td>
<td>Build against public specs (Formato 41, `avi.xsd`); defer layouts we can't obtain (FX-cash / international-transfer / crypto fintech reports) to a post-GA sprint</td>
</tr>
<tr>
<td>INE access path blocked for non-bank fintechs</td>
<td>Launch at A3 (RENAPO + document + liveness) + video-call fallback for full tier; INE biometric for bank tenants (bring-your-own convenio)</td>
</tr>
<tr>
<td>Vendor contract delays (identity, OpenSanctions)</td>
<td>Sandbox integration first; production licence is a Sprint 8 gate</td>
</tr>
<tr>
<td>2027 aviso XSDs still unavailable</td>
<td>Ship current formats (valid until 31-05-2027); add 2027 formats in a post-GA sprint before the 01-06-2027 deadline</td>
</tr>
<tr>
<td>Team ramp-up</td>
<td>Sprint 0–1 is shared foundations work by all squads, so everyone learns the patterns</td>
</tr>
</table>
---
## 8. Ceremonies & tracking
- **Two-week sprints:** planning (squads break these tasks into stories), demo against the milestone "Done when", retrospective.
- **Monthly regulatory review:** the compliance analyst presents law changes and their effect on policy packs and report definitions.
- **Tracking:** one epic per task ID (e.g. `T4.2 Matcher`) in the issue tracker; milestones M1–M6 as releases.
