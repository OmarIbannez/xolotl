# 💼 Product readiness & pricing
Source: https://app.notion.com/p/3fde5630e5e38135bb9cd16ae5a260a7

**Sentinel**, Omar's KYC/AML compliance case engine. Prepared 17 October 2026.
> **How to read this page.** Repository facts were checked in the Sentinel repository (branch main) on 2026-10-17. Market and regulatory claims carry a source ID from section 11 and a confidence level, for example (S7, H). **H**: read directly on the vendor's own page or in the legal text. **M**: a search excerpt, aggregated buyer data such as Vendr, or a reputable secondary source. **L**: a single third-party estimate, a page that no longer loads, or the researchers' own inference.<br><br>Every Sentinel price in section 7 is a hypothesis to test with design partners. Nothing here is legal advice. Confirm each regulatory point with counsel and with each buyer's vendor-risk team.
## 1. Verdict
Sentinel is not sellable as-is: the engine is credible, but the product is a single-company demo that needs a product shell, licensed watchlist data and legal review. The P0 engineering is about 17–23 engineer-weeks: a rough 2–3 months for two engineers with coding agents, with counsel review, a pen test and a DPA running alongside. With two engineers, that puts a first paid pilot with a non-bank fintech about 90 days out. Working alone (the git history shows one human author), plan on about 5–6 months. Banks and Colombian SFC-supervised buyers come later, because the SOC 2 Type II and ISO 27001 evidence their vendor reviews lean on takes 6–15 and 3–12 months to get (SEC-1, SEC-2, SEC-4, SEC-5, M). Mexican e-money institutions wait until counsel rules on their vendor-authorization and continuity-certification rules (section 8).
## 2. What is product-grade today
The engine is the asset. These parts are built, tested and explainable. None of them has run for a customer, been tuned on real data or been reviewed by counsel.
<table header-row="true">
<tr>
<td>What exists</td>
<td>Evidence</td>
<td>Why a buyer cares</td>
</tr>
<tr>
<td>**Explainable screening** against the real OFAC SDN list (17,482 entries). Name scoring is tuned for Hispanic and Portuguese names and adjusted by date of birth, nationality and ID numbers. Every hit shows its score breakdown. Wire counterparties are screened too.</td>
<td>`src/sentinel/screening/matcher.py`, README</td>
<td>Mexican banks must have list filtering evaluated independently (MX-2, H). OFAC lists past failures such as screening software not updated for list changes and missed alternative spellings (US-14, H).</td>
</tr>
<tr>
<td>**Rule packs for MX, BR, CO and US** in YAML: thresholds, aggregates, structuring, velocity, FATF jurisdictions and profile deviation. Each rule cites its regulation. All four packs are versioned (2026.10), and report names are already in the local language.</td>
<td>`src/sentinel/data/packs/*.yaml`</td>
<td>Regulators ask for documented parameters, rules and scenarios (BR-6, US-10, H). Plain YAML is easy to show an auditor.</td>
</tr>
<tr>
<td>**Onboarding checks**: real check-digit algorithms for CURP, CPF and NIT, SSN format rules, a cross-check of the birth date embedded in a CURP or RFC, and a risk score where every point names its reason.</td>
<td>README, risk weights in `mx.yaml`</td>
<td>Works with the identifiers that buyers in all four countries use.</td>
</tr>
<tr>
<td>**Four-eyes case workflow**, enforced server-side. High and critical cases need a second, senior analyst. Reporting always takes two people. The API returns `allowed_actions`, so the UI never guesses.</td>
<td>`src/sentinel/services/cases.py`, `design.md` §4</td>
<td>In Brazil, in Colombia and at Mexican fintechs, the decision to report stays with the institution (BR-6, CO-2, MX-3, H). Sentinel is built for the client's analysts to decide.</td>
</tr>
<tr>
<td>**Tamper-evident audit log**: append-only, hash-chained, protected by a database trigger and checked by `GET /api/audit/verify`.</td>
<td>`src/sentinel/audit.py`, `migrations/versions/0001_initial_schema.py`</td>
<td>Buyers need end-to-end audit trails, and 10-year records in Brazil and Mexico (BR-2, BR-6, MX-2, MX-3, H).</td>
</tr>
<tr>
<td>**Engineering discipline**: 96.76% test coverage. CI runs ruff, ty, basedpyright, vulture and pytest against a real Postgres via testcontainers.</td>
<td>`.github/workflows/ci.yml`</td>
<td>NYDFS Part 504 expects testing and change management in monitoring programs (US-10, H).</td>
</tr>
<tr>
<td>**Analyst web app** (React): dashboard (open cases by priority, status and country, plus top alerting rules), case queue, case detail with the score explanation, onboarding, screening and audit verification. OpenAPI docs at `/api/docs`.</td>
<td>`frontend/`</td>
<td>The first of the four product surfaces already exists (section 5).</td>
</tr>
<tr>
<td>**Simple deployment**: one Docker image (API and UI) plus Postgres. A Render Blueprint redeploys only after CI passes.</td>
<td>`Dockerfile`, `docker-compose.yml`, `render.yaml`</td>
<td>One deployment per pilot partner is cheap to stand up (section 8).</td>
</tr>
</table>
A point for US banks later: the 2026 model-risk guidance excludes deterministic rule-based processes from "model", and it matters most for banks above \$30B in assets (US-9, H). A rules-only engine may fall outside it; ML scores would not (the researchers' reading, L).
## 3. The gaps before anyone could pay
**P0** must exist before a partner pays for a pilot on real data. **P1** is needed to sell beyond the design partners, to banks or in other countries; start the long ones now. Effort is a rough guess in engineer-weeks for a small team with coding agents, or elapsed time where an outside party does the work.
<table header-row="true">
<tr>
<td>Gap</td>
<td>Today</td>
<td>What a customer needs</td>
<td>Priority</td>
<td>Rough effort</td>
</tr>
<tr>
<td>**Sign-in and user management**</td>
<td>One-click demo logins, guarded by `demo_mode` (`src/sentinel/api/routes/auth.py:12-27`). Roles are enforced server-side, but only two exist: analyst and senior analyst (`src/sentinel/enums.py:11-13`).</td>
<td>SSO through the customer's identity provider (OIDC), MFA, and the two roles assigned from the customer's identity-provider groups, so the customer's own admin controls who gets in and with which role. A full admin console and admin, compliance-officer and auditor roles are P1 (section 5). Mexican bank filings and Colombian cloud rules cover MFA on admin consoles (MX-4, CO-1, H). Colombian entities must keep control of user and privilege administration (CO-1, H).</td>
<td>P0</td>
<td>1–2 eng-weeks</td>
</tr>
<tr>
<td>**Production hosting, one deployment per partner**</td>
<td>Render free plan: the service sleeps and the free Postgres expires after 30 days. No tenant concept in the models or migrations.</td>
<td>Paid hosting in a region the partner can declare to its regulator, backups with a tested restore, monitoring and on-call, and a BCP/DR plan with test results. Each customer's data kept apart (BR-1, M; CO-1, H). At least 99.5% availability for Colombian SaaS buyers (CO-1, H). Mexican bank filings ask for the provider's BCP and support across time zones (MX-4, H).</td>
<td>P0</td>
<td>2 eng-weeks</td>
</tr>
<tr>
<td>**API access for integrators**</td>
<td>The API and its OpenAPI docs exist. No API keys, webhooks or rate limiting. Webhooks appear only as an empty generated type (`frontend/src/api/schema.d.ts:292`).</td>
<td>Scoped, rotatable API keys; rate limits; signed webhooks with retries for case and customer events; a sandbox. Colombia requires buyers to manage API risks (CO-1, H).</td>
<td>P0</td>
<td>2–3 eng-weeks</td>
</tr>
<tr>
<td>**Case search**</td>
<td>The case queue filters by status and priority only (`src/sentinel/api/routes/cases.py`).</td>
<td>Search by customer name, ID number and case number, so analysts can find a customer's cases when the partner asks (section 5).</td>
<td>P0</td>
<td>1 eng-week</td>
</tr>
<tr>
<td>**Encryption of personal data**</td>
<td>SSNs are masked in API responses. No encryption at rest. `design.md` §6 plans envelope encryption with KMS and an access-logged reveal path.</td>
<td>Encryption at rest and in transit (TLS 1.2+), documented key management and logged access to identifiers. Required or asked for in Colombia (CO-1, H), by Mexican e-money institutions (MX-5, H) and in Mexican bank filings (MX-4, H).</td>
<td>P0</td>
<td>2 eng-weeks</td>
</tr>
<tr>
<td>**Watchlist coverage and updates**</td>
<td>Real OFAC SDN snapshot, refreshed by a manual command (`sentinel lists sync-ofac`). No job re-screens existing customers when a list changes. No UN list. No way for a client to load its own list. FATF snapshot from June 2025, updated by hand.</td>
<td>Scheduled, versioned list updates, and re-screening of the book on each change. UN Security Council lists, which Brazilian and Colombian institutions must monitor (BR-10, M; CO-2, H). A protected list the client loads for Mexico's non-public Lista de Personas Bloqueadas (MX-3, H; "load and protect" is the researchers' inference, L).</td>
<td>P0</td>
<td>2 eng-weeks</td>
</tr>
<tr>
<td>**Commercial PEP data**</td>
<td>Bundled PEPs are fictional. Real PEPs from OpenSanctions are opt-in under CC BY-NC 4.0, for non-commercial use only.</td>
<td>A licensed source. The cheapest start is OpenSanctions' hosted API inside the product, paid per query from prepaid credits, with no reseller licence. You may not pass the data on or give customers their own API access (S25, S27, S28, H). Loading the bulk data into Sentinel's own matcher needs a Reseller/OEM licence, by quote (S28, H).</td>
<td>P0</td>
<td>1 eng-week plus contract time</td>
</tr>
<tr>
<td>**Rule packs reviewed by counsel**</td>
<td>Thresholds cite regulations but are, in the README's words, "simplified and not reviewed by counsel". MX operaciones internas preocupantes and US 314(a) requests are not modelled (`design.md` §6). Every MX rule cites the bank rules (DCG art. 115 LIC), including a cash-dollar limit written for banks (MX-USD-002); fintech institutions fall under the Fintech Law art. 58 rules (MX-3, H).</td>
<td>Counsel review of the pilot country's pack against the partner's regime, missing obligations added, and sign-off on thresholds by the partner's compliance officer. For a Mexican non-bank partner, that means re-mapping the MX pack from the bank rules to the art. 58 rules.</td>
<td>P0</td>
<td>2–4 weeks elapsed, mostly counsel</td>
</tr>
<tr>
<td>**Methodology and validation pack**</td>
<td>Explainable scores and cited, versioned rules. No methodology document, tuning evidence or back-test. `design.md` §7 lists a screening benchmark and rule back-testing as next work.</td>
<td>Scenario logic, parameters, thresholds, data mapping, change log and test evidence. Mexican banks must have alerts, monitoring parameters and list filtering evaluated independently every 2 years (MX-2, H). Brazil wants detailed parameter documentation and an annual effectiveness report (BR-6, H). NYDFS Part 504 needs end-to-end testing (US-10, H).</td>
<td>P0</td>
<td>1–2 eng-weeks for the first pack</td>
</tr>
<tr>
<td>**Regulator report output**</td>
<td>A reported case produces an on-screen draft in the country's format (the Mexican ROI was seen in the UI check). No download. Filing to UIF SITI, Siscoaf, UIAF SIREL or FinCEN BSA E-Filing is out of scope (`design.md` §4).</td>
<td>An export in the layout the partner files; `design.md` §7 already lists FinCEN SAR XML and the UIF ROI layout. Mexican bank systems must generate and securely transmit reports (MX-1, H). Colombian systems must generate reports automatically, except suspicious-transaction reports, and send UIAF reports electronically (CO-2, H). Mexican local tools sell such outputs as core features (S66, S67, H).</td>
<td>P0</td>
<td>1–2 eng-weeks per format</td>
</tr>
<tr>
<td>**Retention, export and deletion**</td>
<td>No retention setting, bulk export or deletion job in the code.</td>
<td>Core AML/KYC records kept 10 years in Brazil and Mexico (BR-6, MX-2, MX-3, H). Data returned and securely deleted at exit (MX-4, MX-5, MX-9, CO-1, H; BR-1, M). The customer owns the data (CO-1, H).</td>
<td>P0</td>
<td>1–2 eng-weeks</td>
</tr>
<tr>
<td>**Incident and breach notice**</td>
<td>No incident process or status page.</td>
<td>A breach-notice commitment that fits the buyers' clocks. Mexican e-money institutions must tell Banxico and CNBV immediately, including about incidents at a third party, and tell clients within 24 hours (MX-5, H). Firms under SEC Reg S-P must make sure service providers notify them within 72 hours (US-15, H; US-16, M). OCC banks notify within 36 hours, and bank service providers must warn banks as soon as possible after 4 or more hours of disruption (US-4, H). The research suggests a 24-hour vendor commitment (L).</td>
<td>P0</td>
<td>3–5 days</td>
</tr>
<tr>
<td>**Data processing agreement (DPA)**</td>
<td>None.</td>
<td>A DPA for the processor role: *persona encargada* in Mexico, *operador* in Brazil, *encargado* in Colombia, "service provider" under GLBA (MX-8, BR-7, CO-5, US-5, H). It needs a sub-processor list, a breach-notice SLA and a transfer mechanism: ANPD standard clauses for Brazilian data (BR-9, M), an art. 25 transmission contract in Colombia (CO-6, H) and remisión clauses in Mexico (MX-9, H).</td>
<td>P0</td>
<td>2–4 weeks of counsel, then reuse</td>
</tr>
<tr>
<td>**Outsourcing addendum** for the pilot country</td>
<td>None.</td>
<td>The clauses each regulator lists: audit and on-site access for the buyer and its regulator, data location, notice of subcontractors, advance notice of changes, exit with data return and deletion, and continuity (BR-1, M; MX-4, MX-5, CO-1, US-1, H). Mexican bank contracts also carry banking secrecy (MX-4, H). Other countries are P1.</td>
<td>P0</td>
<td>2–3 weeks of counsel per country</td>
</tr>
<tr>
<td>**Security and data-location pack**</td>
<td>None.</td>
<td>What buyers file with their regulators: hosting countries and regions, data-centre addresses (Mexico), an architecture diagram, encryption algorithms and key lengths, key management, TLS 1.2+, MFA, and access logs the buyer can see (MX-4, MX-5, CO-1, H; BR-1, M).</td>
<td>P0</td>
<td>1 eng-week once hosting is fixed</td>
</tr>
<tr>
<td>**Independent pen test**</td>
<td>None.</td>
<td>An annual independent pen test with remediation evidence. CNBV's bank guide accepts recent pen-test results with mitigation evidence as one of two options; the other is a SOC 2 Type 2 report (MX-4, H). Mexican e-money buyers must hold annual security audit results and remediation plans (MX-5, H).</td>
<td>P0</td>
<td>1–2 weeks of tester time plus fixes; price not in the research</td>
</tr>
<tr>
<td>**Commercial terms**</td>
<td>None: an MIT licence and a "portfolio project, not compliance advice" disclaimer.</td>
<td>An MSA, an order form and an SLA (availability, support hours, notice of changes), signed by a legal entity.</td>
<td>P0</td>
<td>Counsel, alongside the DPA</td>
</tr>
<tr>
<td>**Multi-tenancy**</td>
<td>One database per install.</td>
<td>Tenant-scoped data, keys and settings on one shared platform. Needed for the Starter tier and for more than a handful of customers.</td>
<td>P1</td>
<td>4–6 eng-weeks</td>
</tr>
<tr>
<td>**SOC 2 Type II**</td>
<td>None.</td>
<td>A current report, renewed every year. CNBV's bank guide names "SOC 2, tipo 2" (MX-4, H). Colombia's SFC requires SOC 1, 2 or 3 reports (CO-1, H). US bank due diligence points to SOC reports (US-1, H). Buyers treat a report as current for 12 months (SEC-2, M). The cost is not public (SEC-7, SEC-8, H).</td>
<td>P1, start now</td>
<td>Type I in 3–6 months; first Type II in 6–15 months (SEC-1, SEC-2, M)</td>
</tr>
<tr>
<td>**ISO/IEC 27001:2022**</td>
<td>None.</td>
<td>Required by Colombia's SFC for cloud providers that run core processes, along with following ISO 27017 and 27018 (CO-1, H). Certificates under the 2013 edition expired after 31 Oct 2025 (SEC-6, M).</td>
<td>P1, before any SFC buyer</td>
<td>3–12 months (SEC-4, SEC-5, M)</td>
</tr>
<tr>
<td>**Continuity certification**</td>
<td>None.</td>
<td>Mexican e-money buyers need third parties that support operations to hold a current continuity certification under an international standard; the rule names none (MX-5 art. 40, H). Ask counsel whether it covers an AML tool. If it does, it blocks e-money partners, so they are not first targets until counsel rules (section 8).</td>
<td>P1; P0 for an e-money partner if counsel says it applies</td>
<td>Months; depends on the standard</td>
</tr>
<tr>
<td>**Scale**</td>
<td>Transactions are evaluated synchronously on ingest. Screening runs in memory.</td>
<td>A queue for ingest, a trigram prefilter, then a dedicated search index once lists reach tens of millions of PEP and adverse-media records (`design.md` §2, §6).</td>
<td>P1</td>
<td>3–4 eng-weeks</td>
</tr>
<tr>
<td>**Rule changes in the product**</td>
<td>Thresholds change through YAML and a deploy.</td>
<td>Per-customer thresholds with versioning, four-eyes approval and a back-test before activation (admin console, section 5).</td>
<td>P1</td>
<td>3–4 eng-weeks</td>
</tr>
<tr>
<td>**Adverse media**</td>
<td>None.</td>
<td>Licensed adverse-media data, passed through at cost plus a margin.</td>
<td>P1</td>
<td>1–2 eng-weeks plus a data contract</td>
</tr>
<tr>
<td>**Direct regulator filing**</td>
<td>Out of scope.</td>
<td>Filing to UIF, Siscoaf, SIREL or BSA E-Filing as a premium step. Unit21 markets direct CTR filing to FinCEN (S40, M).</td>
<td>P1</td>
<td>Unknown until each channel is checked</td>
</tr>
</table>
P0 engineering adds up to about 17–23 engineer-weeks, including the Spanish UI (section 4), case search and the documents. Every P0 item in section 5 falls inside these rows. With two engineers working in parallel, that is the 2–3 months in the verdict; one engineer alone needs about 4–5 months for the same work. Counsel, the pen tester and the partner's own regulatory filings run in parallel, and they set the real date.
## 4. Gaps that decide who you can sell to
These two gaps decide which buyers you can talk to at all. Whether they block the first pilot depends on the partner: a Mexican or Colombian partner needs Spanish, and a partner that serves businesses needs KYB.
### Business onboarding (KYB)
- **Today.** Individuals only: CURP, RFC, CPF, cédula/NIT and SSN. The models and enums have no business, beneficial-owner or legal-entity concept.
- **Who it decides.** Any fintech whose customers are companies: B2B payments, business accounts, SME lending, payroll. Consumer fintechs can wait.
- **What it needs.**
	- A legal-entity record with local registration numbers.
	- Beneficial owners and controllers, linked to the company and screened as individuals.
	- Screening of the company name itself.
	- Registry and ownership data from a provider, passed through at cost plus a margin.
	- Business risk factors and rules in each pack.
- **What the data costs.** Didit lists KYB registry checks from \$2.00 and UBO checks from \$5.00 (S7, H). In Brazil, Infosimples sells public-data queries, registries included, at R\$0.20 down to R\$0.09 each (S71, H). Sumsub sells KYB only in its Custom plan (S1, H). Global KYC sells a beneficial-owner tree to Mexican clients (S67, H).
- **Effort and priority.** 4–6 engineer-weeks plus a data contract. P1. It becomes P0 only if a design partner serves businesses.
### Spanish and Portuguese
- **Today.** UI labels are in English, and the frontend has no i18n library. Some content is already local: rule names in the MX pack are in Spanish, and each pack's report name is in its regulator's language.
- **Who it decides.** Mexican and Colombian compliance teams work in Spanish, Brazilian teams in Portuguese. In Brazil, the analysis of suspicious activity may not be outsourced or done abroad, and the institution needs the resources in Brazil (BR-6, H). Your users there are Brazilian analysts.
- **What it needs.** An i18n layer; Spanish and Portuguese UI; local date and number formats; report drafts and narratives in the regulator's language; Spanish and Portuguese versions of the methodology pack and the API guide; review by a native-speaking compliance professional.
- **Effort and priority.** Spanish takes about 2 engineer-weeks; Portuguese 1–2 more. Spanish is P0 for a Mexican or Colombian partner. Portuguese is P1 until a Brazilian partner signs.
<table header-row="true">
<tr>
<td>Buyer</td>
<td>Needs KYB?</td>
<td>Language</td>
</tr>
<tr>
<td>Consumer fintech, Mexico</td>
<td>No</td>
<td>Spanish</td>
</tr>
<tr>
<td>B2B fintech, Mexico</td>
<td>Yes</td>
<td>Spanish</td>
</tr>
<tr>
<td>Non-bank fintech, US</td>
<td>Only if B2B</td>
<td>English (done)</td>
</tr>
<tr>
<td>Bank, Mexico</td>
<td>Depends on its customers</td>
<td>Spanish</td>
</tr>
<tr>
<td>SFC-supervised entity, Colombia</td>
<td>Depends on its customers</td>
<td>Spanish</td>
</tr>
<tr>
<td>BCB-authorized institution, Brazil</td>
<td>Depends on its customers</td>
<td>Portuguese</td>
</tr>
</table>
Section 8 covers the other blockers for each type of buyer.
## 5. The product surfaces
There are four surfaces. Only the first exists. Most customers integrate by API, and the compliance team works in the dashboard.
<table header-row="true">
<tr>
<td>Surface</td>
<td>Who uses it</td>
<td>Today</td>
<td>What it needs</td>
</tr>
<tr>
<td>**Analyst workspace**</td>
<td>Analysts and senior analysts</td>
<td>Exists: dashboard, case queue, case detail with the score explanation, onboarding, screening, audit verification.</td>
<td>P0: search and Spanish (section 3). P1: attachments for case evidence, which need file storage, upload, retention and malware scanning; during pilots, analysts record in the case where the evidence sits in the partner's own systems. Also P1: SLA timers (Brazil allows 45 days to select an operation and 45 more to analyse it, BR-6, H), bulk actions, Portuguese.</td>
</tr>
<tr>
<td>**Compliance-officer view**</td>
<td>Compliance officer, auditors</td>
<td>Not built. The dashboard shows open cases by priority, status and country, plus top alerting rules.</td>
<td>P0: regulator exports; during pilots the compliance officer signs in as a senior analyst. P1: alert volumes, false-positive rate, backlog aging, rule performance. These feed the buyer's own reviews: Brazil's annual effectiveness report (BR-6, H), Mexican banks' two-yearly independent evaluation (MX-2, H), the NYDFS certification due 15 April (US-11, H) and SARLAFT internal audit (CO-2, H). During pilots, pull the numbers from the database by hand.</td>
</tr>
<tr>
<td>**Admin console**</td>
<td>The customer's admin; your support</td>
<td>Not built. Only two roles exist, analyst and senior analyst (`src/sentinel/enums.py:11-13`).</td>
<td>P0, all in section 3: SSO, MFA, roles assigned from the customer's identity-provider groups, API keys, webhooks, retention settings. P1: the console itself, where the customer's admin manages users and roles; new admin, compliance-officer and auditor roles; per-country thresholds with versioning and four-eyes approval on changes. Colombian buyers must keep control of user and privilege administration (CO-1, H). Mexican e-money buyers need complete logs of access and activity (MX-5, H).</td>
</tr>
<tr>
<td>**Developer portal**</td>
<td>The customer's engineers</td>
<td>OpenAPI docs at `/api/docs`.</td>
<td>P0: a sandbox with test data, and key management. P1: self-serve keys, webhook delivery logs and replay, a changelog, a status page.</td>
</tr>
</table>
### How customers use the API and the dashboard together
1. **Onboard.** The partner's app sends each new customer to `POST /api/customers`. Sentinel validates the IDs, screens and scores. Clean customers are approved automatically; the rest get a case. The app holds the account until the case is decided.
2. **Monitor.** The partner's ledger posts each transaction to `POST /api/customers/{id}/transactions`. The country's rules run on ingest. Alerts group into one open case per customer and case type.
3. **Review.** Analysts work the queue in the dashboard. High and critical cases need a second, senior analyst.
4. **Act.** Today the partner has to poll `GET /api/cases/{id}`. With webhooks (P0), Sentinel pushes "case decided" and "customer status changed". The partner's system then unblocks, restricts or offboards the customer.
5. **Report.** The compliance officer exports the draft and files it on the regulator's own channel. The decision stays with the partner (BR-6, CO-2, MX-3, H).
6. **Run.** The customer's admin manages users and roles, through its identity provider until the admin console (P1) exists, and later thresholds. Engineers watch API usage and webhook deliveries in the developer portal.
The API is the integration. The dashboard is where the compliance team works. The compliance-officer view is what the buyer shows its board, its auditors and its regulator.
## 6. Market pricing
What comparable vendors charge, and how. Only sourced numbers appear; "contact sales" means no public price was found. All pages were read on 2026-10-17. Vendr-style figures are small buyer-reported samples that move between snapshots, so read them as ranges, not list prices. USD equivalents use ECB rates of 16 Oct 2026 (S90, H) and Colombia's TRM of 15 Oct 2026 (S91, M).
### Closest to Sentinel: transaction monitoring and case management
<table header-row="true">
<tr>
<td>Vendor</td>
<td>How they price</td>
<td>Public numbers</td>
<td>Sources</td>
</tr>
<tr>
<td>Unit21</td>
<td>Sales-led ("request pricing")</td>
<td>Contact sales. Vendr median \$151,746 a year (range \$28,842–\$246,593). Vendr buyer guide: average \$157,154, from about \$33,000 to about \$740,000.</td>
<td>S40, S41, S42 (M)</td>
</tr>
<tr>
<td>Sardine</td>
<td>Minimum monthly commit drawn down by usage; overage monthly (from one Vendr comment)</td>
<td>Contact sales. Vendr median \$95,333 a year (range \$15,000–\$254,480). An earlier copy of the page showed \$140,000 from 4 purchases.</td>
<td>S43 (M)</td>
</tr>
<tr>
<td>Flagright</td>
<td>Sales-led; the pricing page shows only a demo</td>
<td>Contact sales. Startup program: year 1 free, year 2 50% off, then standard. A third party saw annual tiers of \$30k, \$50k and \$75k, gated by API calls, risk profiles, case seats, SARs and simulations.</td>
<td>S44, S45 (H); S46 (L)</td>
</tr>
<tr>
<td>Hummingbird</td>
<td>Custom quote; modular apps; teams of 5 to 1,000+</td>
<td>Contact sales</td>
<td>S48 (M)</td>
</tr>
<tr>
<td>Alloy</td>
<td>Custom</td>
<td>Contact sales. Costbench median \$310,000 a year from 5 buyer reports ("directional").</td>
<td>S50 (L)</td>
</tr>
<tr>
<td>Hawk</td>
<td>Custom; "no extra fees per rail or seat"</td>
<td>Contact sales</td>
<td>S53 (H)</td>
</tr>
<tr>
<td>ComplyAdvantage TM on AWS Marketplace</td>
<td>12-month package, no refunds</td>
<td>\$30,000 per 12 months for 30k transactions</td>
<td>S21, S22 (H)</td>
</tr>
<tr>
<td>Didit</td>
<td>Prepaid per module; case management included free</td>
<td>Transaction monitoring \$0.02 per transaction; screening of flagged transactions \$0.20; AI investigator \$0.10 per review, \$0.40 per full investigation</td>
<td>S7, S8 (H)</td>
</tr>
<tr>
<td>Google Cloud AML AI</td>
<td>Per customer ("party") per year</td>
<td>Contact sales (prices shown as "\$X.XX")</td>
<td>S54 (M)</td>
</tr>
</table>
### Sanctions, PEP and watchlist data
<table header-row="true">
<tr>
<td>Vendor</td>
<td>How they price</td>
<td>Public numbers</td>
<td>Sources</td>
</tr>
<tr>
<td>ComplyAdvantage</td>
<td>Starter: self-serve, monthly, by tier of monitored entities (100 to 2,000); extra entities billed the next cycle. Enterprise: price on application, "unlimited usage".</td>
<td>Starter from \$99 a month. AWS: \$65,000 per 12 months for a "50K Yearly Commit" (unit undefined). Vendr median \$52,900 a year (range \$20,493–\$329,730). ComplyLaunch: free for early-stage start-ups.</td>
<td>S20, S21 (H); S24 (M)</td>
</tr>
<tr>
<td>OpenSanctions</td>
<td>API: prepaid credits valid one year, one credit per match or search query. Bulk data under CC BY-NC; commercial licences by quote.</td>
<td>€50 for 500 credits (€0.10 each) down to €15,000 for 500,000 (€0.03 each); 1M+ by quote; 2,000 free credits on sign-up. Reseller/OEM licence by quote.</td>
<td>S25–S29 (H)</td>
</tr>
<tr>
<td>dilisense</td>
<td>Pay per call, or a monthly plan with included calls</td>
<td>€0.10 per call (100 free); €300 a month for 10,000 calls, then €0.03 each; "as low as 0.01 EUR" at volume</td>
<td>S34 (H)</td>
</tr>
<tr>
<td>Dow Jones Risk & Compliance</td>
<td>Annual platform fee plus per-search or per-record fees</td>
<td>Contact sales. Vendr estimates platform fees from \$10k to over \$100k a year, but its ranges are inconsistent.</td>
<td>S31 (structure M; numbers L)</td>
</tr>
<tr>
<td>LSEG World-Check</td>
<td>Quote-based data licence</td>
<td>Contact sales</td>
<td>S32 (H); S33 (L)</td>
</tr>
<tr>
<td>complead (formerly Sanction Scanner)</td>
<td>Per-query packages, no fixed costs</td>
<td>Contact sales</td>
<td>S35 (H)</td>
</tr>
</table>
### Identity verification
Sentinel does not verify documents or faces, so partners will keep an IDV vendor. These prices shape what buyers expect to pay per check.
<table header-row="true">
<tr>
<td>Vendor</td>
<td>How they price</td>
<td>Public numbers</td>
<td>Sources</td>
</tr>
<tr>
<td>Sumsub</td>
<td>Per verification with a monthly minimum; transaction monitoring and KYB only in Custom</td>
<td>Basic \$1.35 (\$149 monthly minimum). Compliance \$1.85 (\$299 minimum), which adds AML screening and ongoing monitoring.</td>
<td>S1 (H)</td>
</tr>
<tr>
<td>Veriff</td>
<td>Per verification with a monthly minimum, plus add-ons</td>
<td>\$0.80 to \$1.89 (\$49 to \$209 minimums); PEP and sanctions +\$0.64; ongoing monitoring +\$0.09</td>
<td>S2 (H)</td>
</tr>
<tr>
<td>Persona</td>
<td>Annual subscription with included "services", then overage</td>
<td>Essential from \$250 a month on an annual contract, 500 services included, \$1.50 for each extra. Vendr median \$30,000 a year (range \$6,000–\$262,000).</td>
<td>S3 (H); S6 (M)</td>
</tr>
<tr>
<td>Didit</td>
<td>Prepaid per module; no minimums, seat fees or contract</td>
<td>Full KYC \$0.33; AML screening \$0.20; ongoing AML monitoring \$0.07 per user per year; KYB registry from \$2.00, UBO from \$5.00. The free tier becomes \$10 of credit a month from 1 Nov 2026.</td>
<td>S7, S8 (H)</td>
</tr>
<tr>
<td>Incode (now including MetaMap), Truora</td>
<td>Licence plus usage (Incode); volume plans with overage up to 2x (Truora)</td>
<td>Contact sales</td>
<td>S9, S12, S12a (H)</td>
</tr>
</table>
### LatAm-native tools
<table header-row="true">
<tr>
<td>Vendor</td>
<td>How they price</td>
<td>Public numbers</td>
<td>Sources</td>
</tr>
<tr>
<td>Quién es Quién (Mexico)</td>
<td>Per search, or plans capped by searches a day</td>
<td>MXN 29 per search, tax included (\~US\$1.58). MXN 200–300 (\~US\$11–16) for 5 to 20 searches a day; the billing period is not stated, but annual variants at ten times the price suggest monthly (L). API by quote.</td>
<td>S60–S65 (H)</td>
</tr>
<tr>
<td>KYC Systems (Mexico)</td>
<td>Monthly licence by volume and modules. Built for construction and real-estate companies, which Mexico's anti-money-laundering law (LFPIORPI) treats as "actividades vulnerables", not for regulated fintechs</td>
<td>From MXN 5,000 a month plus IVA (\~US\$272), implementation and training included</td>
<td>S66 (H)</td>
</tr>
<tr>
<td>Global KYC (Mexico)</td>
<td>Tiers by credits, client files and seats; API only on upper tiers. Also built for "actividades vulnerables", such as notaries and real-estate firms</td>
<td>Contact sales. The top tier has a full REST API, webhooks and a 99.9% SLA.</td>
<td>S67 (H)</td>
</tr>
<tr>
<td>LAFT360 (Colombia)</td>
<td>Annual plans priced per month in COP plus IVA (the annual price is shown as one payment), with list-check credits</td>
<td>COP 300,000 to 7,900,000 a month (\~US\$94–2,471); \~US\$0.43–0.94 per credit on paid tiers</td>
<td>S68 (H)</td>
</tr>
<tr>
<td>Infosimples (Brazil)</td>
<td>Prepaid per query by volume band</td>
<td>R\$0.20 down to R\$0.09 per query (\~US\$0.039–0.018); R\$100 monthly minimum</td>
<td>S71 (H)</td>
</tr>
<tr>
<td>Serpro Datavalid (Brazil)</td>
<td>Pay per use in volume bands; no monthly fee, minimum or setup</td>
<td>The price table could not be read</td>
<td>S69 (H); S70 (M)</td>
</tr>
<tr>
<td>Regcheq, Isolucion, idwall, Unico, CAF, AML Reputacional, Kronoos, Complif</td>
<td>Sales-led</td>
<td>Contact sales</td>
<td>S72 (M); S73, S77 (L); S74, S75, S78 (H)</td>
</tr>
</table>
### Common value metrics
<table header-row="true">
<tr>
<td>Metric</td>
<td>Who uses it</td>
<td>Observed prices</td>
</tr>
<tr>
<td>Per verification</td>
<td>Sumsub, Veriff, Persona, Didit</td>
<td>\$0.33 (Didit) to \$1.89 (Veriff Premium) (S7, S2, H)</td>
</tr>
<tr>
<td>Per screening or query</td>
<td>OpenSanctions, dilisense, Didit, Quién es Quién, LAFT360</td>
<td>€0.01–€0.10 through global APIs (S25, S34, H); \$0.20 at Didit (S7, H). Local tools cost more per check: \~US\$1.58 at Quién es Quién (S61, H), \~US\$0.43–0.94 per LAFT360 credit (S68, H).</td>
</tr>
<tr>
<td>Per monitored customer</td>
<td>ComplyAdvantage, Didit, Veriff, Google</td>
<td>\$0.07 per user per year (Didit, S7, H); +\$0.09 per verification (Veriff, S2, H); from \$99 a month in tiers of 100–2,000 entities (ComplyAdvantage, S20, H)</td>
</tr>
<tr>
<td>Per transaction</td>
<td>Didit, ComplyAdvantage, Flagright, Sardine</td>
<td>\$0.02 (Didit, S7, H); \$30,000 a year for 30k (ComplyAdvantage, S22, H)</td>
</tr>
<tr>
<td>Platform fee or minimum commit</td>
<td>Sardine, Unit21, Persona, Sumsub, Veriff, Dow Jones</td>
<td>\$49–\$299 monthly minimums (S1, S2, H); Persona from \$250 a month (S3, H); monitoring medians of \$95k–\$152k a year (S41, S43, M)</td>
</tr>
<tr>
<td>Seats</td>
<td>A tier limit more than a meter</td>
<td>Didit "zero seat fees" (S7, H); Hawk "no extra fees per rail or seat" (S53, H); Flagright case seats as a tier limit (S46, L)</td>
</tr>
<tr>
<td>Countries or modules as tier gates</td>
<td>ComplyAdvantage, Sumsub, Global KYC, LAFT360</td>
<td>Transaction monitoring only in Enterprise (S20, H) or Custom (S1, H); API access by tier (S67, H); batch checks from LAFT360's Esencial tier (S68, H)</td>
</tr>
</table>
### How contracts are shaped
- **Self-serve entry.** A per-unit price on a \$49–\$299 monthly minimum, paid by card, with trials of about 50 checks (S1, S2, S3, H).
- **Mid-market platforms.** A 12-month term with no refunds (S21, S47, H). Either an annual fee with included volume and overage billed the next cycle (S20, H), or a monthly minimum commit drawn down by usage (S43, M). Overage 20–50% above bundled rates is reported for Dow Jones (S31, L).
- **Enterprise.** Committed volume, custom legal terms, an SLA, data residency and a security review (Didit's description, S7, H). Didit gives 60 days' notice of price changes (S7, H).
- **LatAm local.** Plans in MXN or COP plus IVA, priced per month or per year, with credits or daily caps: LAFT360 sells annual plans (S68, H), KYC Systems a monthly licence (S66, H). Implementation is often included, and regulator outputs are part of the core (S66, S67, S68, H).
- **Startup programs are common.** Flagright, ComplyLaunch, Persona, and Didit's free monthly credit (S45, S20, S3, S8, H).
**What this means for Sentinel.** Screening alone is not worth a premium: it costs €0.01–€0.10 a query through data APIs (S25, S34, H) and \$0.20 at Didit (S7, H). Didit already bundles transaction monitoring at \$0.02 a transaction with free case management (S7, H), so Sentinel cannot win on price at the low end. It has to win on per-country rules, regulator drafts, explainability and local fit. None of the specialist monitoring and case-management vendors checked publishes prices. Local tools price in local currency, per month or per year.
## 7. Pricing recommendation
> **Every price proposed for Sentinel in this section is a hypothesis to test with design partners.** Market numbers keep their sources. The structure is the one already proposed: an annual platform subscription by tier, usage on top, premium data passed through with a margin, and enterprise add-ons. The anchors are cost to serve, the customer's alternative (analyst hours saved) and competitor quotes.
### Value metrics
1. **Monitored customers per month, the main meter.** Count each customer under ongoing monitoring in the month once. Closed customers kept only for the 10-year retention do not count. This matches how ComplyAdvantage, Didit and Google price ongoing monitoring (S20, S7, H; S54, M). It also matches Sentinel's unit of work: alerts group into one open case per customer and case type.
2. **Transactions monitored, per 1,000.** An allowance in each tier, with a published overage rate.
3. **Countries and modules as tier gates.** The number of country packs; webhooks and SSO; compliance-officer analytics; later, KYB and direct filing.
4. **Seats only at the edges.** At least 2 in every tier, because four-eyes needs two people. The market leans away from seat fees (S7, S53, H).
5. **Premium data passed through.** PEP, adverse media, local lists and business registries, at cost plus a margin.
Screening against public and client lists (OFAC, UN and the client's own lists) is not a meter. It is included under fair use.
### Tiers
<table header-row="true">
<tr>
<td></td>
<td>Starter</td>
<td>Growth</td>
<td>Enterprise</td>
</tr>
<tr>
<td>For</td>
<td>A small LatAm compliance team in one country, working mainly in the dashboard</td>
<td>A regulated fintech integrating by API</td>
<td>A bank or a larger fintech in several countries</td>
</tr>
<tr>
<td>Available</td>
<td>After multi-tenancy (P1)</td>
<td>After the P0 gaps close (target day 90, with two engineers); design-partner pilots run on this tier</td>
<td>After SOC 2 Type II (P1)</td>
</tr>
<tr>
<td>Country packs</td>
<td>1</td>
<td>The pilot country's counsel-reviewed pack; further packs added as each passes counsel review and its country addendum (P1)</td>
<td>All four (MX, BR, CO, US) once counsel-reviewed, plus custom packs</td>
</tr>
<tr>
<td>Monitored customers included</td>
<td>1,000</td>
<td>10,000</td>
<td>Committed volume</td>
</tr>
<tr>
<td>Transactions included a month</td>
<td>10,000</td>
<td>100,000</td>
<td>Committed volume</td>
</tr>
<tr>
<td>Seats included</td>
<td>2</td>
<td>5</td>
<td>15</td>
</tr>
<tr>
<td>Integration</td>
<td>Dashboard; API with low rate limits</td>
<td>Adds webhooks, sandbox and SSO (all P0)</td>
<td>Adds higher limits</td>
</tr>
<tr>
<td>Reporting</td>
<td>Report drafts and export</td>
<td>Adds compliance-officer metrics (P1; manual reports during pilots)</td>
<td>Adds custom reports, and direct filing once built</td>
</tr>
<tr>
<td>Hosting</td>
<td>Shared</td>
<td>Dedicated until multi-tenancy (P1)</td>
<td>Dedicated, in the region the customer needs</td>
</tr>
<tr>
<td>Support</td>
<td>Email, business hours</td>
<td>Priority. A 99.5% availability target, the Colombian SaaS minimum (CO-1, H), starting once paid hosting, monitoring and on-call (P0) are live</td>
<td>99.9% SLA, as on Global KYC's top tier (S67, H); a named contact</td>
</tr>
<tr>
<td>**Price hypothesis**</td>
<td>**US\$250–500 a month**, billed annually in local currency plus IVA (≈ MXN 4,600–9,200 a month)</td>
<td>**US\$18,000–30,000 a year** (≈ MXN 331,000–551,000)</td>
<td>**From US\$50,000 a year**, as a committed minimum drawn down by usage</td>
</tr>
</table>
MXN figures use USD/MXN 18.38 (S90, H).
### Price hypotheses and where they come from
<table header-row="true">
<tr>
<td>Item</td>
<td>Hypothesis</td>
<td>Derived from</td>
</tr>
<tr>
<td>Starter</td>
<td>US\$250–500 a month</td>
<td>The mid-range of local tools: LAFT360's Compliance to Advance plans at \~US\$232–516 (S68, H); ComplyAdvantage Starter from \$99 (S20, H). The Mexican anchors serve non-financial businesses under the "actividades vulnerables" rules, not regulated fintechs: KYC Systems from \~US\$272 (S66, H) and Global KYC (S67, H). Sentinel adds monitoring and a case queue.</td>
</tr>
<tr>
<td>Growth</td>
<td>US\$18,000–30,000 a year</td>
<td>Level with Didit at equal volume, not below the US platforms. At Didit's public rates, Growth's allowance costs about US\$24,700 a year: 1.2 million transactions at US\$0.02 plus 10,000 monitored customers at US\$0.07 (S7, H; our arithmetic). ComplyAdvantage's US\$30,000 package covers 30,000 transactions a year, a fortieth of Growth's (S22, H), and the research has no volume for Flagright's reported US\$30k tier (S46, L). So Growth earns its price with local rules, regulator drafts and explainability (section 6), not by undercutting. It sits far below the Unit21 and Sardine medians (S41, S43, M) and must cover a dedicated deployment until multi-tenancy (see the unit economics).</td>
</tr>
<tr>
<td>Enterprise</td>
<td>From US\$50,000 a year</td>
<td>Near the ComplyAdvantage median of \$52,900 (S24, M) and Flagright's reported \$50k and \$75k tiers (S46, L).</td>
</tr>
<tr>
<td>Monitored-customer overage</td>
<td>US\$0.40 a month (Starter), US\$0.20 (Growth)</td>
<td>Starter includes customers at US\$0.25–0.50 each a month and Growth at US\$0.15–0.25, depending on where each price lands, so these overage rates run from 20% below to 60% above the included rate. Overage 20–50% above bundled rates is reported elsewhere (S31, L), so overage is a selling point only at or below the included rate. Set it once the tier prices are tested.</td>
</tr>
<tr>
<td>Transaction overage</td>
<td>US\$15 per 1,000 (test US\$10–20)</td>
<td>Didit lists US\$20 per 1,000 (S7, H). ComplyAdvantage's AWS package works out to US\$1.00 a transaction (S22, H; our arithmetic), but Didit's is the price buyers can see.</td>
</tr>
<tr>
<td>Extra seats</td>
<td>US\$50 a seat a month</td>
<td>No benchmark. Drop it if partners push back; several vendors advertise no seat fees (S7, S53, H).</td>
</tr>
<tr>
<td>PEP screening at onboarding</td>
<td>US\$0.15 a query</td>
<td>OpenSanctions costs €0.03–0.10 a query, or US\$0.034–0.113 (S25, S90, H). US\$0.15 is about cost plus 30% at today's small-bundle price. Didit charges \$0.20 per AML screening (S7, H); Veriff adds \$0.64 (S2, H).</td>
</tr>
<tr>
<td>PEP ongoing monitoring</td>
<td>US\$0.10 per monitored customer a month (re-screened monthly)</td>
<td>One query per customer a month. It loses money on the smallest bundle (€0.10), barely covers cost on the 5,000-credit bundle (€0.08, about US\$0.090) and needs the 100,000-credit bundle (€0.05) for a healthy margin (S25, H). Competitive with ComplyAdvantage Starter (S20, H), not with Didit's \$0.07 per user per year (S7, H). Getting closer to Didit needs the Reseller/OEM bulk licence (S28, H).</td>
</tr>
<tr>
<td>Mexican local lists</td>
<td>Cost plus 30%</td>
<td>Quién es Quién charges MXN 29 a search (S61, H); its API is by quote (S65, H).</td>
</tr>
<tr>
<td>Dedicated deployment add-on</td>
<td>US\$12,000 a year once multi-tenancy exists (Growth runs dedicated until then); included in Enterprise</td>
<td>Covers an assumed US\$500 a month of hosting (not from the research; replace it with the first real bill) plus upgrade work.</td>
</tr>
<tr>
<td>Custom country pack</td>
<td>Quoted per project</td>
<td>Counsel review at cost, plus build time.</td>
</tr>
<tr>
<td>Design-partner pilot</td>
<td>US\$3,000 for 90 days (≈ MXN 55,000), credited to year 1, which starts with the pilot. Year 1 at 50% off Growth (US\$9,000–15,000). Year 2 at list, written into the pilot agreement. PEP and other pass-through data billed from the first day of the pilot.</td>
<td>Flagright gives startups year 1 free and 50% off in year 2 (S45, H). Charging something tests willingness to pay. Without the pass-through billing, the pilot runs below cost (see the unit economics).</td>
</tr>
</table>
**Contract terms**
- An annual term with a monthly minimum commit drawn down by usage (the Sardine pattern, S43, M).
- Overage billed the next month at published rates (the ComplyAdvantage pattern, S20, H).
- 60 days' notice of price changes (the Didit pattern, S7, H).
- LatAm customers billed in local currency plus IVA, as local tools do (S66, S68, H).
- Publish the Starter and Growth prices. None of the specialist monitoring and case-management vendors checked does, while the IDV and screening leaders do (S1, S2, S7, S20, H).
### Unit economics sketch
One Growth customer on a dedicated deployment, in each phase of the design-partner terms: 10,000 monitored customers, 100,000 transactions a month and PEP re-screening once a month. PEP and other pass-through data are billed in every phase, pilots included. At the midpoint price, the pilot and the rest of year 1 bring in the same platform fee a month.
<table header-row="true">
<tr>
<td>Line, per month</td>
<td>Pilot (first 90 days)</td>
<td>Rest of year 1 (50% off)</td>
<td>Growth at list (year 2 on)</td>
<td>Basis</td>
</tr>
<tr>
<td>Platform fee</td>
<td>US\$1,000</td>
<td>US\$1,000</td>
<td>US\$2,000</td>
<td>Pilot: US\$3,000 over 90 days. Rest of year 1: the discounted US\$12,000 less the pilot's US\$3,000, over 9 months. List: midpoint of US\$18,000–30,000 a year (hypotheses)</td>
</tr>
<tr>
<td>PEP monitoring billed</td>
<td>US\$1,000</td>
<td>US\$1,000</td>
<td>US\$1,000</td>
<td>10,000 × US\$0.10 (hypothesis)</td>
</tr>
<tr>
<td>**Revenue**</td>
<td>**US\$2,000**</td>
<td>**US\$2,000**</td>
<td>**US\$3,000**</td>
<td></td>
</tr>
<tr>
<td>Hosting</td>
<td>−US\$500</td>
<td>−US\$500</td>
<td>−US\$500</td>
<td>Assumption, not from the research</td>
</tr>
<tr>
<td>PEP data</td>
<td>−US\$563</td>
<td>−US\$563</td>
<td>−US\$563</td>
<td>10,000 queries × €0.05 (100,000-credit bundle) × US\$1.1253 per euro (S25, S90, H)</td>
</tr>
<tr>
<td>OFAC data</td>
<td>US\$0</td>
<td>US\$0</td>
<td>US\$0</td>
<td>Public domain (README)</td>
</tr>
<tr>
<td>**Gross margin**</td>
<td>**US\$937 (about 47%)**</td>
<td>**US\$937 (about 47%)**</td>
<td>**US\$1,937 (about 65%)**</td>
<td>Before your time</td>
</tr>
</table>
- If PEP data were not billed during the pilot, the pilot would run at about −US\$63 a month (US\$1,000 − US\$500 − US\$563) before anyone's time. Bill pass-through data from the first day.
- On a shared multi-tenant platform, with an assumed US\$100 a month of hosting, the margin is about 78% at list.
- If PEP data is bundled into the platform fee instead of billed, the margin falls to about 47% at list. Pass it through.
- Daily PEP re-screening through the API would cost about €9,000 a month for this customer, even at the best published rate (300,000 queries × €0.03; S25, H; our arithmetic). Daily re-screening needs a bulk licence.
- Starter on a dedicated deployment loses money: US\$250–500 of revenue against an assumed US\$500 of hosting. Sell Starter only after multi-tenancy.
- Not in the table: your time per partner for integration, tuning and support, which is the real limit at 2–3 partners. Also not in it: fixed costs with no public price, such as the pen test, the SOC 2 auditor and compliance platform (SEC-7, SEC-8, H), counsel, and the OpenSanctions Reseller/OEM licence (S28, H). Get quotes in month one. Every early customer is a design partner, so for the first year divide the yearly fixed costs by about US\$11,000, one design partner's margin in year 1 (US\$937 × 12), to see how many customers cover them. From year 2, divide by about US\$23,000, the yearly margin of one Growth customer at list.
- The customer's alternative: during the pilot, measure analyst minutes per case before and after. Value is minutes saved × cases a month × the partner's loaded analyst cost. Use the partner's numbers, not guesses.
## 8. Go-to-market
### Who to sell to first
**Non-bank fintechs with customers in Mexico, including US fintechs that serve Mexican customers.** Think payment companies. Pick ones that must run transaction monitoring and a case queue, and that have outgrown free startup programs. A US non-bank fintech is the fastest partner 1. Mexican e-money institutions wait until counsel rules on their vendor rules (see "Not first").
Why them:
1. **Fit today.** Name matching tuned for Hispanic names, CURP and RFC checks, and a US pack with SSN support. The MX pack (Spanish rule names, ROI draft) fits the Mexican bank rules today: every MX rule cites them (DCG art. 115 LIC), including a cash-dollar limit written for banks (`mx.yaml`). Fintech institutions fall under the Fintech Law art. 58 rules instead (MX-3, H), so the pack must be re-mapped to them before a pilot with a Mexican non-bank fintech (section 3).
2. **Room in the market.** The Mexican tools with public prices sell list screening (S60–S65, H), or client files and notices for non-financial businesses (S66, H). The specialist US monitoring platforms don't publish prices. Entry packages sit around \$30,000 a year (S22, H; S46, L), but for far less volume: ComplyAdvantage's covers 30,000 transactions a year (S22, H). Buyer-reported medians reach \$95,000–152,000 (S41, S43, M). At Growth's volume, Didit's public rates come to about US\$24,700 a year (S7, H), so Sentinel competes on local fit, not on price (section 7).
3. **Lighter vendor reviews than banks, for US non-bank fintechs.** The sources show safeguards by contract and periodic review (US-5, H), but no regulator filing. That makes a US non-bank fintech the fastest partner 1 (section 10).
Not first:
- **Banks.** In Mexico: a CNBV notice or authorization 20 business days ahead, a full security pack, and pen-test or SOC 2 Type 2 evidence (MX-4, H). In the US: review of SOC reports (US-1, H) and, where the Bank Service Company Act applies, a notice to the regulator within 30 days (US-3, H).
- **Mexican e-money institutions, until counsel rules.** A hosted Sentinel stores and processes clients' personal data (names, birth dates, CURP or RFC, transactions) with privileged access, which needs prior authorization from both Banxico and CNBV (MX-5 art. 44, H). A Mexican bank using a domestic provider only files a notice (MX-4, H), so this review is not lighter than a bank's. Third parties that support e-money operations must also hold a current continuity certification under an international standard (MX-5 art. 40, H), which Sentinel lacks (section 3). Deploying into the partner's own cloud account, with no privileged access for your team, may avoid the art. 44 trigger (our inference, L; ask counsel).
- **Colombian SFC-supervised entities.** They need your ISO 27001 certificate and SOC reports first (CO-1, H).
- **Brazilian BCB institutions.** They need Portuguese, ANPD standard clauses if data leaves Brazil (BR-9, M), and a BCB notice within 10 days of contracting (BR-1, M).
- **Early-stage startups.** They can get Flagright free for a year, or ComplyAdvantage through ComplyLaunch (S45, S20, H).
- **Buyers of list screening only.** In Mexico they can pay MXN 200–300 (about US\$11–16, apparently a month) for 5 to 20 searches a day, or MXN 29 (\~US\$1.58) a search, at Quién es Quién (S61–S64, H). In Colombia, LAFT360 charges \~US\$94–2,471 a month (S68, H). For all but LAFT360's top plans, that is less than our assumed US\$500 a month of dedicated hosting (an assumption, section 7).
### The design-partner pilot
- **Who.** 2–3 partners on discounted, paid pilots (prices in section 7). Each has a written success plan, and the year-1 and year-2 prices are agreed up front.
- **Scope.** One country pack, the partner's real customers and transactions, and their own analysts working cases in Sentinel.
- **Phases.**
	1. Contracts and filings: DPA, outsourcing addendum and security pack. The partner files any notice or authorization its regulator needs.
	2. Back-test: once the DPA and filings allow, replay recent months of the partner's transactions through the pack and compare the alerts with what they saw. Until then, test on synthetic or anonymized data.
	3. Shadow run: 2–4 weeks alongside their current process.
	4. Live: their analysts decide cases in Sentinel. The partner keeps every reporting decision.
- **Success measures, agreed at the start.** Alerts per 1,000 customers, false-positive rate, minutes per case, backlog age, time from alert to report, days to integrate, and a yes or no from their compliance officer.
- **What you get.** Real data to tune the pack, a reference, a tested price and a ranked list of missing features.
- **What they get.** A lower price, a say in the roadmap and a direct line to you.
### How a pilot is deployed
One deployment per partner until multi-tenancy exists. Sentinel already ships as one Docker image (API and UI) plus Postgres, so each partner gets:
- its own cloud project or account, database with backups, encryption keys and domain;
- a region agreed with the partner. Hosting outside Mexico turns a Mexican bank's notice into an authorization request (MX-4, H). Brazilian institutions need BCB authorization 60 days ahead for services abroad when there is no information-exchange agreement with the host supervisor (BR-1, BR-5, M);
- sign-in through the partner's identity provider, with demo mode and seeding turned off;
- the same release as every other partner, with its own pack version and a written change log;
- at exit, a full export, then deletion of the whole environment.
Why this works: segregation is physical, which is easy to explain (BR-1, M; CO-1, H). Each partner's data sits in one known region. A bug cannot expose one partner's data to another.
Option: deploy into the partner's own cloud account. The partner then controls the region, keys and access, which may change which outsourcing rules apply, including the e-money authorization (our inference; ask counsel). It also hands them the code, so settle the licence first (section 9).
Move to multi-tenancy after the third paying customer or when Starter launches, whichever comes first (hypothesis).
## 9. The licence decision
- The README says MIT, and the repository has an MIT `LICENSE` ("Copyright (c) 2026 Omar Ibañez"). The GitHub repository is private.
- Git history: 11 commits by Omar and 2 by Dependabot.
- If the repository goes public under MIT, anyone, competitors and pilot partners included, may use, change, host and sell Sentinel with attribution only. That cannot be taken back for a published version.
<table header-row="true">
<tr>
<td>Option</td>
<td>What it means</td>
<td>For</td>
<td>Against</td>
</tr>
<tr>
<td>Stay MIT and go public</td>
<td>Everything is open</td>
<td>Buyers and auditors can read the rules; reach with developers</td>
<td>Nothing stops a partner from self-hosting for free, or a competitor from hosting it</td>
</tr>
<tr>
<td>Open core</td>
<td>The core is open: matcher, rule engine, case workflow, audit log. Paid modules stay closed: SSO and admin console, multi-tenancy, regulator exports, maintained country packs, data connectors, KYB.</td>
<td>Open where buyers check; paid where they get value</td>
<td>You must maintain the boundary and decide where each feature goes</td>
</tr>
<tr>
<td>Relicense before going public</td>
<td>Source-available (for example, a licence that bars competing hosted services) or proprietary</td>
<td>Strongest protection</td>
<td>Less community reach; source-available is not open source</td>
</tr>
</table>
**Recommendation**
- Before any code reaches a partner, including a pilot in the partner's own cloud, replace MIT with a proprietary licence while the repository is still private. You are the only human author in the history, which keeps this simple. Still confirm it with counsel.
- Choose between open core and source-available after the pilots show what customers pay for. Open core fits if readable rules become a selling point.
- The code licence does not cover the data. OpenSanctions bulk data stays CC BY-NC whatever Sentinel's licence is (S27, H). Keep `sentinel lists sync-peps` out of customer deployments until you hold the right licence. The OFAC list is public domain.
## 10. 90-day plan to the first paid pilot
This assumes two engineers with coding agents, splitting section 3's 17–23 engineer-weeks of P0 work. The git history shows one human author, so contracting a second engineer is a week-1 action. Working alone, the plan cannot shrink, because it is already one partner and one country: expect about 5–6 months to the first paid pilot. The plan assumes a Mexican partner 1. A US non-bank fintech needs no regulator filing in the sources and is faster: swap in the US pack, a FinCEN SAR export and US contract terms.
<table header-row="true">
<tr>
<td>Weeks</td>
<td>Product</td>
<td>Legal, security and data</td>
<td>Sales</td>
</tr>
<tr>
<td>1–2</td>
<td>Contract a second engineer. Deployment template, one per partner. Start OIDC sign-in and MFA.</td>
<td>Engage counsel: DPA, MSA, Mexican outsourcing addendum, review of the MX pack and its re-mapping to the art. 58 fintech rules. Decide the licence. Open an OpenSanctions account (2,000 free credits, S25, H). Ask for quotes: OpenSanctions Reseller/OEM, the QeQ API, a SOC 2 auditor and platform, a pen test.</td>
<td>List 30 target fintechs. Hold 10 discovery calls. Write a one-page pilot offer with the section 7 prices.</td>
</tr>
<tr>
<td>3–6</td>
<td>API keys, rate limits, webhooks. Case search. Encryption at rest. List updates with re-screening, the UN list and client-loaded lists. OpenSanctions API. ROI export. Retention, export and deletion. Spanish UI.</td>
<td>Security and data-location pack. Methodology document for the MX pack. Incident runbook with a 24-hour breach notice. BCP/DR plan. Book the pen test.</td>
<td>Sign 2–3 letters of intent. Start partner 1's vendor questionnaire. Confirm which filings apply. A partner that needs authorization files by week 3–4, so its security pack and draft addendum come first.</td>
</tr>
<tr>
<td>7–10</td>
<td>Partner 1's environment. Sandbox integration with their engineers. Back-test on their data once the DPA and any filing allow, and on synthetic or anonymized data until then.</td>
<td>Pen test and fixes. Restore test. DPA and addendum signed. SOC 2 readiness: get controls running so the Type II observation window can start, and use a Type I for early deals (the research's suggested order, L).</td>
<td>Partner 2 in contracting.</td>
</tr>
<tr>
<td>11–13</td>
<td>Shadow run, then live.</td>
<td>Weekly metrics review. Incident drill.</td>
<td>First invoice. Review the price hypotheses with partner 1.</td>
</tr>
</table>
**Day-90 exit criteria**
- [ ] Partner 1 is live on real data under a signed, paid pilot, and the first invoice is sent.
- [ ] Every P0 item in sections 3 and 5 is closed for the pilot country.
- [ ] A pen-test report with remediation evidence exists.
- [ ] The SOC 2 Type II observation window has started or is scheduled.
- [ ] Partner 2 is in contracting, and a third partner is identified.
- [ ] The price hypotheses are checked against at least two real competitor quotes.
**Clocks that set the date**
- Mexican e-money institutions: authorities decide on authorization within 25 business days, and silence means approval, but any request for more information stops the clock (MX-5 art. 49, H). Treat 25 business days as a minimum, not a date.
- Mexican banks: notice at least 20 business days before contracting (MX-4, H).
- Colombian SFC entities: filing within the 15 days before processing starts (CO-1, H).
- Brazilian institutions: BCB notice within 10 days after contracting (BR-1, M).
- US banks, where the Bank Service Company Act applies: notice within 30 days (US-3, H).
- US non-bank fintechs: no filing found in the sources.
- If partner 1 needs authorization, file by week 3–4 and back-test on synthetic or anonymized data until it arrives. Day 90 slips by the length of any information request. A US non-bank fintech needs no filing, which makes it the fastest partner 1.
**Open questions to close in the 90 days**
- Do the e-money rules on vendor authorization (MX-5 art. 44) and continuity certification (art. 40) cover Sentinel, and would a deployment in the partner's own cloud, with no privileged access for your team, avoid art. 44? (counsel)
- Which Mexican AML rules apply to a Mexican partner 1, and what does re-mapping the MX pack from the bank rules to the art. 58 fintech rules change? (counsel)
- What do OpenSanctions, QeQ, the auditor and the pen tester charge? (quotes)
- What do two or three competitors quote the partners? (the partners' procurement)
- Will partners pay US\$18,000–30,000 a year after the pilot? (the pilots)
## 11. Sources
All web sources were read on 2026-10-17. The confidence of each claim is given where it is cited.
**Repository**, checked on 2026-10-17 (branch main): `README.md`, `LICENSE`, `docs/design.md` (§2, §4, §6, §7), `src/sentinel/api/app.py`, `src/sentinel/api/routes/auth.py`, `src/sentinel/api/routes/customers.py`, `src/sentinel/api/routes/cases.py`, `src/sentinel/models.py`, `src/sentinel/enums.py`, `src/sentinel/migrations/`, `src/sentinel/screening/matcher.py`, `src/sentinel/data/packs/*.yaml`, `src/sentinel/services/cases.py`, `src/sentinel/audit.py`, `src/sentinel/cli.py`, `frontend/src/api/schema.d.ts`, `Dockerfile`, `docker-compose.yml`, `render.yaml`, `.github/workflows/ci.yml`, and the git history.
### Market and pricing
<table header-row="true">
<tr>
<td>ID</td>
<td>Source</td>
<td>URL</td>
<td>Conf.</td>
<td>Read</td>
</tr>
<tr>
<td>S1</td>
<td>Sumsub pricing</td>
<td>[https://sumsub.com/pricing/](https://sumsub.com/pricing/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S2</td>
<td>Veriff pricing</td>
<td>[https://www.veriff.com/pricing](https://www.veriff.com/pricing)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S3</td>
<td>Persona help center: plans, allowance, overage, Startup Program</td>
<td>[https://help.withpersona.com/articles/6oZbzp7jb7AWGClF5vpY3K/](https://help.withpersona.com/articles/6oZbzp7jb7AWGClF5vpY3K/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S6</td>
<td>Vendr: Persona</td>
<td>[https://www.vendr.com/marketplace/persona](https://www.vendr.com/marketplace/persona)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S7</td>
<td>Didit pricing</td>
<td>[https://didit.me/pricing](https://didit.me/pricing)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S8</td>
<td>Didit: new pricing from November 2026 (published 2026-10-02)</td>
<td>[https://didit.me/blog/new-pricing-november-2026/](https://didit.me/blog/new-pricing-november-2026/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S9</td>
<td>Incode FAQ</td>
<td>[https://www.incode.com/faq/](https://www.incode.com/faq/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S12</td>
<td>Truora pricing pages</td>
<td>[https://www.truora.com/en/pricing/](https://www.truora.com/en/pricing/) ; [https://www.truora.com/en/pricing/background-checks](https://www.truora.com/en/pricing/background-checks) ; [https://www.truora.com/en/pricing/digital-identity](https://www.truora.com/en/pricing/digital-identity)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S12a</td>
<td>MetaMap pricing (returns 404)</td>
<td>[https://www.metamap.com/pricing](https://www.metamap.com/pricing)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S20</td>
<td>ComplyAdvantage pricing</td>
<td>[https://complyadvantage.com/pricing/](https://complyadvantage.com/pricing/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S21</td>
<td>AWS Marketplace: ComplyAdvantage screening and monitoring</td>
<td>[https://aws.amazon.com/marketplace/pp/prodview-q2ctuj7gq3ri2](https://aws.amazon.com/marketplace/pp/prodview-q2ctuj7gq3ri2)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S22</td>
<td>AWS Marketplace: ComplyAdvantage transaction monitoring</td>
<td>[https://aws.amazon.com/marketplace/pp/prodview-sx3vmfjorzrzw](https://aws.amazon.com/marketplace/pp/prodview-sx3vmfjorzrzw)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S24</td>
<td>Vendr: ComplyAdvantage</td>
<td>[https://www.vendr.com/marketplace/complyadvantage](https://www.vendr.com/marketplace/complyadvantage)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S25</td>
<td>OpenSanctions API and credit bundles</td>
<td>[https://www.opensanctions.org/api/](https://www.opensanctions.org/api/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S26</td>
<td>OpenSanctions changelog 51 (2026-09-24)</td>
<td>[https://www.opensanctions.org/changelog/51/](https://www.opensanctions.org/changelog/51/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S27</td>
<td>OpenSanctions licensing</td>
<td>[https://www.opensanctions.org/licensing/](https://www.opensanctions.org/licensing/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S28</td>
<td>OpenSanctions commercial FAQ</td>
<td>[https://www.opensanctions.org/faq/commercial/pricing-tier/](https://www.opensanctions.org/faq/commercial/pricing-tier/) ; [https://www.opensanctions.org/docs/commercial/faq/](https://www.opensanctions.org/docs/commercial/faq/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S29</td>
<td>OpenSanctions API metering FAQ</td>
<td>[https://www.opensanctions.org/faq/api/metering/](https://www.opensanctions.org/faq/api/metering/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S31</td>
<td>Vendr: Dow Jones</td>
<td>[https://www.vendr.com/marketplace/dow-jones](https://www.vendr.com/marketplace/dow-jones)</td>
<td>M/L</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S32</td>
<td>LSEG World-Check On Demand</td>
<td>[https://www.lseg.com/en/risk-intelligence/screening-solutions/world-check-kyc-screening/world-check-on-demand](https://www.lseg.com/en/risk-intelligence/screening-solutions/world-check-kyc-screening/world-check-on-demand)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S33</td>
<td>ERP Research: LSEG World-Check (updated Aug 2026)</td>
<td>[https://www.erpresearch.com/erp-add-ons/fraud-risk/lseg-world-check](https://www.erpresearch.com/erp-add-ons/fraud-risk/lseg-world-check)</td>
<td>L</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S34</td>
<td>dilisense AML screening API</td>
<td>[https://dilisense.com/en/products/aml-screening-api](https://dilisense.com/en/products/aml-screening-api)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S35</td>
<td>complead (formerly Sanction Scanner) FAQ; pricing page returns 404</td>
<td>[https://www.sanctionscanner.com/faq](https://www.sanctionscanner.com/faq) ; [https://www.sanctionscanner.com/pricing](https://www.sanctionscanner.com/pricing)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S40</td>
<td>Unit21 product page and 2025 product recap (search excerpts)</td>
<td>[https://www.unit21.ai/products/aml-transaction-monitoring](https://www.unit21.ai/products/aml-transaction-monitoring) ; [https://www.unit21.ai/blog/2025-end-of-year-product-recap-the-year-of-the-risk-fighting-platform](https://www.unit21.ai/blog/2025-end-of-year-product-recap-the-year-of-the-risk-fighting-platform)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S41</td>
<td>Vendr: Unit21</td>
<td>[https://www.vendr.com/marketplace/unit21](https://www.vendr.com/marketplace/unit21)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S42</td>
<td>Vendr buyer guide: Unit21</td>
<td>[https://www.vendr.com/buyer-guides/unit21](https://www.vendr.com/buyer-guides/unit21)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S43</td>
<td>Vendr: Sardine</td>
<td>[https://www.vendr.com/marketplace/sardine](https://www.vendr.com/marketplace/sardine)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S44</td>
<td>Flagright pricing</td>
<td>[https://www.flagright.com/pricing](https://www.flagright.com/pricing)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S45</td>
<td>Flagright seed-funding post (startup program)</td>
<td>[https://www.flagright.com/post/flagright-raises-4-3-million-in-seed-funding-to-advance-ai-native-aml-compliance-and-risk-management-solutions](https://www.flagright.com/post/flagright-raises-4-3-million-in-seed-funding-to-advance-ai-native-aml-compliance-and-risk-management-solutions)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S46</td>
<td>Toarn: Flagright (updated 2026-04-11)</td>
<td>[https://toarn.com/insights/flagright](https://toarn.com/insights/flagright)</td>
<td>L</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S47</td>
<td>AWS Marketplace: Flagright</td>
<td>[https://aws.amazon.com/marketplace/pp/prodview-b2spgoxko3ya4](https://aws.amazon.com/marketplace/pp/prodview-b2spgoxko3ya4)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S48</td>
<td>Hummingbird pricing (search excerpt) and Apps</td>
<td>[https://www.hummingbird.co/pricing](https://www.hummingbird.co/pricing) ; [https://www.hummingbird.co/resources/introducing-hummingbird-apps](https://www.hummingbird.co/resources/introducing-hummingbird-apps)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S50</td>
<td>Costbench: Alloy</td>
<td>[https://costbench.com/software/crypto-compliance/alloy/](https://costbench.com/software/crypto-compliance/alloy/)</td>
<td>L</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S53</td>
<td>Hawk fraud solutions</td>
<td>[https://hawk.ai/solutions/fraud](https://hawk.ai/solutions/fraud)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S54</td>
<td>Google Cloud AML AI pricing (search excerpt)</td>
<td>[https://cloud.google.com/financial-services/anti-money-laundering/pricing](https://cloud.google.com/financial-services/anti-money-laundering/pricing)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S60</td>
<td>Quién es Quién store catalogue</td>
<td>[https://tienda.quienesquien.mx/collections/all](https://tienda.quienesquien.mx/collections/all)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S61</td>
<td>Quién es Quién: One Search</td>
<td>[https://tienda.quienesquien.mx/products/one-search](https://tienda.quienesquien.mx/products/one-search)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S62</td>
<td>Quién es Quién: Starter</td>
<td>[https://tienda.quienesquien.mx/products/starter](https://tienda.quienesquien.mx/products/starter)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S63</td>
<td>Quién es Quién: Boost</td>
<td>[https://tienda.quienesquien.mx/products/boost](https://tienda.quienesquien.mx/products/boost)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S64</td>
<td>Quién es Quién: Plus</td>
<td>[https://tienda.quienesquien.mx/products/plus](https://tienda.quienesquien.mx/products/plus)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S65</td>
<td>Quién es Quién: Q-Detect API</td>
<td>[https://tienda.quienesquien.mx/pages/q-detect](https://tienda.quienesquien.mx/pages/q-detect)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S66</td>
<td>KYC Systems (updated 2026-09-27)</td>
<td>[https://kyc-systems.com/actividades-vulnerables/software-antilavado-construccion-inmuebles.html](https://kyc-systems.com/actividades-vulnerables/software-antilavado-construccion-inmuebles.html)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S67</td>
<td>Global KYC</td>
<td>[https://www.myglobalkyc.com/](https://www.myglobalkyc.com/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S68</td>
<td>LAFT360 pricing</td>
<td>[https://laft360.com/front-pages/pricing](https://laft360.com/front-pages/pricing)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S69</td>
<td>Serpro Datavalid on [gov.br](http://gov.br)</td>
<td>[https://servicos.gov.br/api/v1/servicos/3456](https://servicos.gov.br/api/v1/servicos/3456)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S70</td>
<td>Loja Serpro: Datavalid (search excerpt)</td>
<td>[https://loja.serpro.gov.br/datavalid](https://loja.serpro.gov.br/datavalid)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S71</td>
<td>Infosimples prices</td>
<td>[https://infosimples.com/consultas/precos/](https://infosimples.com/consultas/precos/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S72</td>
<td>Isolucion SAGRILAFT (search excerpt)</td>
<td>[https://isolucion.com/soluciones/sagrilaft-colombia/](https://isolucion.com/soluciones/sagrilaft-colombia/)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S73</td>
<td>Complif on Gartner and Y Combinator (search excerpts)</td>
<td>[https://www.gartner.com/reviews/product/complif-561992415](https://www.gartner.com/reviews/product/complif-561992415) ; [https://www.ycombinator.com/companies/complif](https://www.ycombinator.com/companies/complif)</td>
<td>L</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S74</td>
<td>AML Reputacional</td>
<td>[https://www.amlreputacional.com.br/fintechs/](https://www.amlreputacional.com.br/fintechs/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S75</td>
<td>Kronoos</td>
<td>[https://tour.kronoos.com.br/](https://tour.kronoos.com.br/)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S77</td>
<td>Forbes Brasil on BigDataCorp (search excerpt); searches for idwall, Unico and CAF prices found none</td>
<td>[https://forbes.com.br/forbes-tech/2020/07/bigdatacorp-oferece-modelo-fremium-de-consulta-de-dados/](https://forbes.com.br/forbes-tech/2020/07/bigdatacorp-oferece-modelo-fremium-de-consulta-de-dados/)</td>
<td>L</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S78</td>
<td>Regcheq Mexico blog</td>
<td>[https://regcheq.com.mx/blog/como-elegir-la-mejor-opcion-de-software-de-pld-en-mexico](https://regcheq.com.mx/blog/como-elegir-la-mejor-opcion-de-software-de-pld-en-mexico)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S90</td>
<td>ECB euro reference rates (16 Oct 2026)</td>
<td>[https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)</td>
<td>H</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>S91</td>
<td>Noticias Caracol: TRM of 15 Oct 2026</td>
<td>[https://www.noticiascaracol.com/economia/dolar-hoy-15-de-octubre-de-2026-asi-esta-la-trm-este-jueves-so35](https://www.noticiascaracol.com/economia/dolar-hoy-15-de-octubre-de-2026-asi-esta-la-trm-este-jueves-so35)</td>
<td>M</td>
<td>2026-10-17</td>
</tr>
</table>
### Buyer and regulatory requirements
<table header-row="true">
<tr>
<td>ID</td>
<td>Source</td>
<td>URL</td>
<td>Read</td>
</tr>
<tr>
<td>SEC-1</td>
<td>Drata, "SOC 2 Type 1 vs Type 2"</td>
<td>[https://drata.com/learn/soc-2/type-1-vs-type-2](https://drata.com/learn/soc-2/type-1-vs-type-2)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>SEC-2</td>
<td>Vanta, "SOC 2 Type 2"</td>
<td>[https://www.vanta.com/collection/soc-2/soc-2-type-2](https://www.vanta.com/collection/soc-2/soc-2-type-2)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>SEC-4</td>
<td>Vanta, "How long does it take to get ISO certified"</td>
<td>[https://vanta.com/collection/iso-27001/how-long-does-it-take-to-get-iso-certified](https://vanta.com/collection/iso-27001/how-long-does-it-take-to-get-iso-certified)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>SEC-5</td>
<td>Secureframe, "ISO 27001 certification timeline"</td>
<td>[https://secureframe.com/hub/iso-27001/certification-timeline](https://secureframe.com/hub/iso-27001/certification-timeline)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>SEC-6</td>
<td>DQS, "Transition requirements for ISO 27001:2022"</td>
<td>[https://www.dqsglobal.com/en/explore/blog/hk/transition-requirements-for-iso-27001-2022](https://www.dqsglobal.com/en/explore/blog/hk/transition-requirements-for-iso-27001-2022)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>SEC-7</td>
<td>Vanta pricing</td>
<td>[https://www.vanta.com/pricing](https://www.vanta.com/pricing)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>SEC-8</td>
<td>Drata pricing</td>
<td>[https://drata.com/pricing](https://drata.com/pricing)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-1</td>
<td>Res. CMN 4.893/2021 (legismap reproduction)</td>
<td>[https://legismap.com.br/leis-e-normas/resolucao-cmn-n-4-893-de-26-02-2021](https://legismap.com.br/leis-e-normas/resolucao-cmn-n-4-893-de-26-02-2021)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-2</td>
<td>BCB, Voto CMN 88/2025</td>
<td>[https://normativos.bcb.gov.br/Votos/CMN/202588/Voto_do_CMN_88_2025.pdf](https://normativos.bcb.gov.br/Votos/CMN/202588/Voto_do_CMN_88_2025.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-5</td>
<td>Comunicado BCB 41.782/2024 (NormasBrasil)</td>
<td>[https://www.normasbrasil.com.br/norma/comunicado-41782-2024_460905.html](https://www.normasbrasil.com.br/norma/comunicado-41782-2024_460905.html)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-6</td>
<td>Circular BCB 3.978/2020, consolidated</td>
<td>[https://normativos.bcb.gov.br/Lists/Normativos/Attachments/50905/Circ_3978_v6_P.pdf](https://normativos.bcb.gov.br/Lists/Normativos/Attachments/50905/Circ_3978_v6_P.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-7</td>
<td>LGPD, Lei 13.709/2018 (Câmara)</td>
<td>[https://www2.camara.leg.br/legin/fed/lei/2018/lei-13709-14-agosto-2018-787077-publicacaooriginal-156212-pl.html](https://www2.camara.leg.br/legin/fed/lei/2018/lei-13709-14-agosto-2018-787077-publicacaooriginal-156212-pl.html)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-9</td>
<td>Lefosse, ANPD standard-clause deadline (24 Jul 2025)</td>
<td>[https://lefosse.com/noticias/alerta/transferencia-internacional-de-dados-prazo-para-adocao-das-clausulas-padrao-da-anpd-se-encerra-em-23-de-agosto/](https://lefosse.com/noticias/alerta/transferencia-internacional-de-dados-prazo-para-adocao-das-clausulas-padrao-da-anpd-se-encerra-em-23-de-agosto/)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>BR-10</td>
<td>Res. BCB 44/2020, UN sanctions (NormasBrasil)</td>
<td>[https://www.normasbrasil.com.br/norma/resolucao-44-2020_404868.html](https://www.normasbrasil.com.br/norma/resolucao-44-2020_404868.html)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-1</td>
<td>DCG under LIC art. 115 (banks), compiled to the 2017 reform</td>
<td>[https://www.gob.mx/cms/uploads/attachment/file/207270/Disposiciones_de_Car_cter_General_a_que_se_refiere_el_art_culo_115_de_la_Ley_de_Instituciones_de_Cr_dito.pdf](https://www.gob.mx/cms/uploads/attachment/file/207270/Disposiciones_de_Car_cter_General_a_que_se_refiere_el_art_culo_115_de_la_Ley_de_Instituciones_de_Cr_dito.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-2</td>
<td>DOF 28 Aug 2024, reform of the bank DCG</td>
<td>[https://dof.gob.mx/nota_to_doc.php?codnota=5737473](https://dof.gob.mx/nota_to_doc.php?codnota=5737473) ; [https://dof.gob.mx/nota_detalle.php?codigo=5737473&fecha=28/08/2024](https://dof.gob.mx/nota_detalle.php?codigo=5737473&fecha=28/08/2024)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-3</td>
<td>DCG under Fintech Law art. 58 (ITF), DOF 10 Sep 2018</td>
<td>[https://www.gob.mx/cms/uploads/attachment/file/909810/DCG_Instituciones_de_Tecnolog_a_Financiera__2018.pdf](https://www.gob.mx/cms/uploads/attachment/file/909810/DCG_Instituciones_de_Tecnolog_a_Financiera__2018.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-4</td>
<td>CNBV guide for contracting third-party services (banks, Sept 2022)</td>
<td>[`https://www.gob.mx/cms/uploads/attachment/file/762170/2022_09_12_Nueva_gu_a_contrataci_n_terceros_bancos__DGSROT___DGSSI___DGASF__VF.pdf`](https://www.gob.mx/cms/uploads/attachment/file/762170/2022_09_12_Nueva_gu_a_contrataci_n_terceros_bancos__DGSROT___DGSSI___DGASF__VF.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-5</td>
<td>CNBV–Banxico rules for e-money institutions (IFPE), DOF 28 Jan 2021</td>
<td>[https://dof.gob.mx/nota_to_doc.php?codnota=5610487](https://dof.gob.mx/nota_to_doc.php?codnota=5610487) ; [https://dof.gob.mx/nota_detalle.php?codigo=5610487](https://dof.gob.mx/nota_detalle.php?codigo=5610487)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-8</td>
<td>LFPDPPP (DOF 20 Mar 2025, last reform 14 Nov 2025)</td>
<td>[https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf](https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>MX-9</td>
<td>Reglamento de la LFPDPPP (DOF 21 Dec 2011)</td>
<td>[https://www.diputados.gob.mx/LeyesBiblio/regley/Reg_LFPDPPP.pdf](https://www.diputados.gob.mx/LeyesBiblio/regley/Reg_LFPDPPP.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>CO-1</td>
<td>SFC CBJ Parte I, Título I, Cap. VI, cloud computing</td>
<td>[https://www.superfinanciera.gov.co/loader.php?lServicio=Tools2&lTipo=descargas&lFuncion=descargar&idFile=1036453](https://www.superfinanciera.gov.co/loader.php?lServicio=Tools2&lTipo=descargas&lFuncion=descargar&idFile=1036453)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>CO-2</td>
<td>SFC CBJ Parte I, Título IV, Cap. IV, SARLAFT</td>
<td>[https://www.superfinanciera.gov.co/loader.php?lServicio=Tools2&lTipo=descargas&lFuncion=descargar&idFile=1009815](https://www.superfinanciera.gov.co/loader.php?lServicio=Tools2&lTipo=descargas&lFuncion=descargar&idFile=1009815)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>CO-5</td>
<td>Ley 1581 de 2012 (DIAN normograma)</td>
<td>[https://normograma.dian.gov.co/dian/compilacion/docs/ley_1581_2012.htm](https://normograma.dian.gov.co/dian/compilacion/docs/ley_1581_2012.htm)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>CO-6</td>
<td>Decreto 1377 de 2013 (DIAN normograma)</td>
<td>[https://normograma.dian.gov.co/dian/compilacion/docs/decreto_1377_2013.htm](https://normograma.dian.gov.co/dian/compilacion/docs/decreto_1377_2013.htm)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-1</td>
<td>Interagency Guidance on Third-Party Relationships (SR 23-4 attachment)</td>
<td>[https://www.federalreserve.gov/supervisionreg/srletters/SR2304a1.pdf](https://www.federalreserve.gov/supervisionreg/srletters/SR2304a1.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-3</td>
<td>12 U.S.C. 1867, Bank Service Company Act (Cornell LII)</td>
<td>[https://www.law.cornell.edu/uscode/text/12/1867](https://www.law.cornell.edu/uscode/text/12/1867)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-4</td>
<td>12 CFR 53.3 and 53.4, incident notification (Cornell LII)</td>
<td>[https://www.law.cornell.edu/cfr/text/12/53.3](https://www.law.cornell.edu/cfr/text/12/53.3) ; [https://www.law.cornell.edu/cfr/text/12/53.4](https://www.law.cornell.edu/cfr/text/12/53.4)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-5</td>
<td>16 CFR 314.2 and 314.4, FTC Safeguards Rule (Cornell LII)</td>
<td>[https://www.law.cornell.edu/cfr/text/16/314.2](https://www.law.cornell.edu/cfr/text/16/314.2) ; [https://www.law.cornell.edu/cfr/text/16/314.4](https://www.law.cornell.edu/cfr/text/16/314.4)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-9</td>
<td>Federal Reserve SR 26-2 and attachment (17 Apr 2026)</td>
<td>[https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm) ; [https://www.federalreserve.gov/supervisionreg/srletters/SR2602a1.pdf](https://www.federalreserve.gov/supervisionreg/srletters/SR2602a1.pdf)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-10</td>
<td>NYDFS press release on final Part 504 (30 Jun 2016)</td>
<td>[https://www.dfs.ny.gov/reports_and_publications/press_releases/pr1606301](https://www.dfs.ny.gov/reports_and_publications/press_releases/pr1606301)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-11</td>
<td>NYDFS Transaction Monitoring Certification page</td>
<td>[https://www.dfs.ny.gov/industry_guidance/transaction_monitoring](https://www.dfs.ny.gov/industry_guidance/transaction_monitoring)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-14</td>
<td>OFAC, "A Framework for OFAC Compliance Commitments"</td>
<td>[https://ofac.treasury.gov/media/16331/download?inline](https://ofac.treasury.gov/media/16331/download?inline)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-15</td>
<td>SEC press release 2024-58, Regulation S-P amendments</td>
<td>[https://www.sec.gov/newsroom/press-releases/2024-58](https://www.sec.gov/newsroom/press-releases/2024-58)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-16</td>
<td>Willkie, December 3 compliance date for Regulation S-P (16 Oct 2025)</td>
<td>[https://www.willkie.com/publications/2025/10/december-3-compliance-date-for-sec-amendments-to-regulation-s-p-is-fast-approaching](https://www.willkie.com/publications/2025/10/december-3-compliance-date-for-sec-amendments-to-regulation-s-p-is-fast-approaching)</td>
<td>2026-10-17</td>
</tr>
<tr>
<td>US-17</td>
<td>Sullivan & Cromwell memo on FinCEN's April 2026 AML/CFT program proposal (16 Apr 2026)</td>
<td>[https://www.sullcrom.com/insights/memo/2026/April/Regulators-Issue-Proposed-Rules-Reforming-AML-CFT-Program-Requirements](https://www.sullcrom.com/insights/memo/2026/April/Regulators-Issue-Proposed-Rules-Reforming-AML-CFT-Program-Requirements)</td>
<td>2026-10-17</td>
</tr>
</table>
**Caveats carried over from the research.** Several pricing pages could not be fetched (Persona, Loja Serpro, Hummingbird, complead, Unit21, Sardine, Alloy), so their figures come from search excerpts or are missing. No public World-Check, Dow Jones, Incode, MetaMap, Unit21, Sardine, Hummingbird, Alloy, Hawk, idwall, Unico or CAF prices were found; real quotes are needed. On the regulatory side, the research did not re-check: Res. 4.893 on BCB's own site, later amendments to the Mexican fintech AML rules, later CUB reforms, Colombia's SIC incident rule and adequacy list, the US definition of "bank service provider", and state privacy laws. A FinCEN proposal to overhaul AML program rules (April 2026) could become final after this date (US-17, M).
