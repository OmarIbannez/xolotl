# Executive summary — Architecture
Source: https://app.notion.com/3fde5630e5e381b3bf91f5271e50b24a

**Date:** 2026-10-17 · **Detail:** <mention-page url="https://app.notion.com/p/3fde5630e5e38175aa91cf38caaaf97e"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e3815191c9ddd6987d3598"/>
---
## The big picture
```javascript
        ┌──────────────────────────────────────────────┐
        │            CENTRAL CONTROL (global)           │
        │  client accounts · billing · country rules    │
        │  ── holds NO customer personal data ──        │
        └───────────────────────┬──────────────────────┘
                                │ settings only
       ┌────────────────────────┼────────────────────────┐
       ▼                        ▼                        ▼
┌──────────────┐        ┌──────────────┐        ┌──────────────────┐
│   MEXICO     │        │   BRAZIL     │        │  DEDICATED        │
│  deployment  │        │  deployment  │        │  (large bank)     │
│              │        │              │        │                   │
│ Fintech A ░░ │        │ Fintech D ░░ │        │ Bank X only       │
│ Fintech B ░░ │        │ Fintech E ░░ │        │                   │
│ Fintech C ░░ │        │     …        │        │                   │
└──────┬───────┘        └──────┬───────┘        └──────────────────┘
       │                       │
       ▼                       ▼
 Mexican government      Brazilian government
 ID databases &          ID databases &
 regulators              regulators
```
**In one sentence:** one platform, deployed once per country, shared by many fintechs that can never see each other's data.
---
## Five design principles
<table header-row="true">
<tr>
<td>#</td>
<td>Principle</td>
<td>What it means</td>
<td>Why it matters</td>
</tr>
<tr>
<td>1</td>
<td>**Data stays in-country**</td>
<td>Each country gets its own deployment; customer data never leaves it</td>
<td>Regulators and banks require it</td>
</tr>
<tr>
<td>2</td>
<td>**Every client is walled off**</td>
<td>Each fintech's data is separated by the database itself and encrypted with that client's own key</td>
<td>One client can never see another's data. If a client leaves, their data can be made permanently unreadable</td>
</tr>
<tr>
<td>3</td>
<td>**Country rules are configuration, not code**</td>
<td>Each country's rules (thresholds, report formats, deadlines, retention) live in a maintained "rulebook" per country and industry</td>
<td>When a law changes, we update it once and every client is compliant</td>
</tr>
<tr>
<td>4</td>
<td>**Every decision can be explained later**</td>
<td>Each approval, alert or report records exactly which rules and lists were used at the time</td>
<td>Regulators can ask "why did you approve this customer in 2026?" years later, and we can answer</td>
</tr>
<tr>
<td>5</td>
<td>**Nothing is ever silently changed or deleted**</td>
<td>Decisions are added, never overwritten. A tamper-proof audit log is kept for 10 years</td>
<td>Required by law, and it's what auditors check first</td>
</tr>
</table>
---
## What's inside each country deployment
<table header-row="true">
<tr>
<td>Component</td>
<td>What it does</td>
<td>Analogy</td>
</tr>
<tr>
<td>**Front door (API)**</td>
<td>Where fintechs connect their apps; includes mobile/web onboarding kits</td>
<td>Reception desk</td>
</tr>
<tr>
<td>**Onboarding engine**</td>
<td>Runs each customer's verification step by step, retrying when a government system is down</td>
<td>Assembly line</td>
</tr>
<tr>
<td>**Government connectors**</td>
<td>Talk to national ID, tax and company registries</td>
<td>Translators</td>
</tr>
<tr>
<td>**Screening engine**</td>
<td>Matches names against sanctions and blacklists, tuned for Spanish/Portuguese names</td>
<td>Watchlist checker</td>
</tr>
<tr>
<td>**Real-time monitor**</td>
<td>Scores every payment in milliseconds and raises alerts</td>
<td>Security camera</td>
</tr>
<tr>
<td>**Case workspace**</td>
<td>Where compliance analysts investigate and decide</td>
<td>Detective's desk</td>
</tr>
<tr>
<td>**Report factory**</td>
<td>Builds regulator reports in each official format, tracks deadlines and files them</td>
<td>Tax-filing software</td>
</tr>
<tr>
<td>**Vault & archive**</td>
<td>Encrypted documents, 10-year records, tamper-proof audit log</td>
<td>Bank vault</td>
</tr>
</table>
---
## Data: what we store
<table header-row="true">
<tr>
<td>Kind</td>
<td>Examples</td>
<td>Shared or per client?</td>
</tr>
<tr>
<td>**Reference data**</td>
<td>Sanctions lists, politically-exposed-person lists, report formats, local money units</td>
<td>**Shared** by all clients (public, no personal data)</td>
</tr>
<tr>
<td>**Customer data**</td>
<td>People, companies, IDs, documents, selfies, transactions</td>
<td>**Per client**, encrypted, never shared</td>
</tr>
<tr>
<td>**Decisions**</td>
<td>Risk scores, screening results, alerts, cases, filed reports</td>
<td>**Per client**, permanent record</td>
</tr>
<tr>
<td>**Confidential government lists**</td>
<td>e.g. Mexico's blocked-persons list</td>
<td>**Per client**: each receives it privately</td>
</tr>
</table>
---
## Infrastructure in plain terms
- **Cloud-hosted** in local cloud regions (Mexico: Querétaro; Brazil: São Paulo), across 3 separate data centres per region so one outage doesn't stop service.
- **Main database** with a primary copy plus live backup copies: one takes over automatically if the primary fails, and others handle reporting so it doesn't slow down customers.
- **Growth path:**
	<table header-row="true">
<tr>
<td>Stage</td>
<td>Setup</td>
</tr>
<tr>
<td>Start</td>
<td>One shared database per country</td>
</tr>
<tr>
<td>Growth</td>
<td>Transaction history moves to a cheaper analytics store</td>
</tr>
<tr>
<td>Scale</td>
<td>Large clients get their own database</td>
</tr>
	</table>
- **Disaster recovery:** Mexico has only one region per cloud provider, so keeping backups in-country may require **a second cloud provider**. This also matches Mexican fintech rules on cloud providers.
- **Target performance:**
	<table header-row="true">
<tr>
<td>What</td>
<td>Target</td>
</tr>
<tr>
<td>Payment risk decisions</td>
<td>\< 0.1 seconds</td>
</tr>
<tr>
<td>Name screening</td>
<td>\< 0.3 seconds</td>
</tr>
<tr>
<td>Uptime</td>
<td>99.95%</td>
</tr>
	</table>
---
## Cost & scale (rough)
<table header-row="true">
<tr>
<td>Stage</td>
<td>Clients</td>
<td>Customers</td>
<td>Transactions/sec</td>
<td>Infra cost per country</td>
</tr>
<tr>
<td>**Launch**</td>
<td>5–20 fintechs</td>
<td>up to 2M</td>
<td>\~500 at peak</td>
<td>**\~US\$11–16k / month**</td>
</tr>
<tr>
<td>**Growth**</td>
<td>20–80</td>
<td>up to 20M</td>
<td>\~5,000 at peak</td>
<td>scales with volume</td>
</tr>
<tr>
<td>**Scale**</td>
<td>100+, incl. banks</td>
<td>100M+</td>
<td>20,000+ at peak</td>
<td>per-client dedicated options</td>
</tr>
</table>
**Government and biometric verification fees are usually larger than infrastructure cost.** They are metered per client and passed through.
---
## Key risks
1. **Data-residency rules for backups.** Need legal confirmation per country before choosing the disaster-recovery setup.
2. **Government database access.** Some registries (e.g. Mexico's voter ID) only allow access through authorized vendors.
3. **Regulators' systems vary widely.** Only Brazil and Chile offer machine-to-machine filing. Elsewhere, a person still uploads or types in what we generate.
## Decisions needed
- [ ] Primary cloud provider (AWS, Google or Azure; all have Mexico and Brazil regions)
- [ ] Year-1 volume estimate (sets launch sizing)
- [ ] Whether backups may leave the country, or we budget for a second in-country cloud provider
