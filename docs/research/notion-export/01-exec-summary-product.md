# Executive summary — Product
Source: https://app.notion.com/3fde5630e5e38175b09bf9e362c690e5

**Date:** 2026-10-17 · **Detail:** <mention-page url="https://app.notion.com/p/3fde5630e5e381fda6a5de38d932d48d"/>
---
## What we're building
A **shared compliance platform** that many fintechs plug into, so they can:
1. **Verify who their customers are** (KYC),
2. **Detect money laundering and fraud**, and
3. **File the reports regulators require** in each Latin American country.
Each fintech's data stays completely separate and stays in its own country.
## Why now
Regulators across the region are tightening rules **right now**. Every fintech has to re-tool, and most are doing it by hand.
<table header-row="true">
<tr>
<td>Country</td>
<td>What's forcing change</td>
<td>When</td>
</tr>
<tr>
<td>Mexico</td>
<td>New anti-money-laundering rules + new report formats for thousands of businesses</td>
<td>Nov 2026 → Jun 2027</td>
</tr>
<tr>
<td>Mexico</td>
<td>Banks must add facial biometrics</td>
<td>Nov 2026</td>
</tr>
<tr>
<td>Brazil</td>
<td>New automatic reports for crypto and foreign-cash transactions</td>
<td>Already live (Oct 2026)</td>
</tr>
<tr>
<td>Brazil</td>
<td>Crypto companies must be licensed; banks can't serve unlicensed ones</td>
<td>Nov 2026</td>
</tr>
<tr>
<td>Colombia</td>
<td>Rulebook rewritten for all companies</td>
<td>Must comply by May 2027</td>
</tr>
<tr>
<td>Chile</td>
<td>Automated reporting channel opens; new data-privacy law</td>
<td>Oct–Dec 2026</td>
</tr>
<tr>
<td>Peru, Brazil, Argentina</td>
<td>Crypto "travel rule" (sender/receiver identity must travel with transfers)</td>
<td>2026–2028</td>
</tr>
</table>
## Why LATAM needs its own product
US and European tools miss what matters here:
- **Identity is checked against government databases**, not just a photo of an ID.
- **Every country has its own blacklists**, including confidential ones that can't be bought from data vendors.
- **Spanish and Portuguese names** (two surnames, accents) cause floods of false alerts in generic tools.
- **Instant payments** (Pix, SPEI) mean fraud has to be caught in milliseconds.
- **Every regulator wants reports in its own format.** Formats range from modern APIs to 20-year-old text files, deadlines differ, and many require "nothing to report" filings. **This is the hardest part, and our main moat.**
---
## What the product does (8 capabilities)
<table header-row="true">
<tr>
<td>#</td>
<td>Capability</td>
<td>In plain words</td>
</tr>
<tr>
<td>1</td>
<td>**Customer onboarding**</td>
<td>Verify ID, selfie and liveness against government records in minutes, on web, mobile or WhatsApp</td>
</tr>
<tr>
<td>2</td>
<td>**Business onboarding**</td>
<td>Identify who really owns and controls a company, using each country's ownership rules</td>
</tr>
<tr>
<td>3</td>
<td>**Screening**</td>
<td>Check every customer against sanctions, politically exposed persons and local blacklists, continuously. Freeze accounts when the law requires it</td>
</tr>
<tr>
<td>4</td>
<td>**Risk scoring**</td>
<td>Rate every customer's risk and automatically schedule their periodic reviews</td>
</tr>
<tr>
<td>5</td>
<td>**Transaction monitoring**</td>
<td>Watch payments in real time for laundering and fraud patterns specific to each country</td>
</tr>
<tr>
<td>6</td>
<td>**Investigations**</td>
<td>One workspace for compliance teams to review alerts, document decisions and get approvals</td>
</tr>
<tr>
<td>7</td>
<td>**Regulatory reporting** ⭐</td>
<td>Generate every required report in the exact official format, track deadlines, and file it, automatically where the regulator allows</td>
</tr>
<tr>
<td>8</td>
<td>**Compliance calendar**</td>
<td>Track every recurring obligation (annual audits, risk assessments, training, officer changes) so nothing is missed</td>
</tr>
</table>
Underneath all of this: **full audit trail, country-specific record retention (5–10 years), privacy-law compliance, and data kept in-country.**
## What makes us different
- **Report automation as the core product.** Brazil and Chile allow direct electronic filing; we'd do it end-to-end. Elsewhere we produce ready-to-upload files that pass the regulator's validation the first time.
- **One configuration per country, maintained by us.** When a law changes, we update it once and every client is compliant. Clients never have to write rules from scratch.
- **Built for the region.** Local name matching, local government databases, local payment rails, Spanish/Portuguese throughout.
- **Proof that controls work.** Regulators are now grading effectiveness, not paperwork. We provide the metrics and evidence.
## How much reporting automation is possible
<table header-row="true">
<tr>
<td>Level</td>
<td>Countries</td>
</tr>
<tr>
<td>**Fully automatic filing**</td>
<td>Brazil; Chile (from Oct 2026)</td>
</tr>
<tr>
<td>**We generate the file; client uploads it**</td>
<td>Mexico, Colombia, Argentina, Chile (some reports), Peru</td>
</tr>
<tr>
<td>**We prepare it; client types it into a government form**</td>
<td>Suspicious-activity reports in Colombia, Argentina, Peru</td>
</tr>
</table>
---
## Rollout
<table header-row="true">
<tr>
<td>Phase</td>
<td>Market</td>
<td>Why this order</td>
</tr>
<tr>
<td>**1**</td>
<td>**Mexico**</td>
<td>Biggest rule changes, a large underserved market (fintechs + thousands of regulated non-financial businesses), and clear 2027 deadlines that force buying decisions</td>
</tr>
<tr>
<td>**2**</td>
<td>**Brazil**</td>
<td>Largest fintech market. Its reporting can be fully automated</td>
</tr>
<tr>
<td>**3**</td>
<td>Colombia + Argentina</td>
<td>Fresh rule rewrites; strong fintech growth</td>
</tr>
<tr>
<td>**4**</td>
<td>Chile + Peru</td>
<td>New automated channel (Chile); crypto rules (Peru)</td>
</tr>
<tr>
<td>**5**</td>
<td>Ecuador, Uruguay, Panama → Dominican Republic, Costa Rica, Guatemala → others</td>
<td>Dollarized hubs and markets with brand-new 2026 laws. Venezuela excluded (US sanctions); screening support only</td>
</tr>
</table>
## Who buys
- **Fintechs and digital wallets:** fast onboarding, real-time monitoring, reporting.
- **Banks and lenders:** reporting, screening, audit trail; may require a dedicated deployment.
- **Crypto exchanges:** travel rule, wallet screening, new licensing rules.
- **Regulated non-financial businesses** (real estate, car dealers, notaries, etc. in Mexico): simple onboarding + monthly reports. Underserved, high volume.
## Infrastructure and cost (rough)
- **One deployment per country** to meet data-residency rules; fintechs share it but are fully isolated.
- **About US\$11–16k per month per country at launch**, before data-vendor fees.
- **Government and biometric checks are paid per use** and passed through to clients.
---
## Risks & open questions
1. **Many official report templates sit behind regulators' login portals.** We need **one design-partner fintech per country** to obtain them and validate our output.
2. **Access to government ID databases** (e.g. Mexico's voter registry) is restricted. We'll likely go through authorized vendors, and need to confirm terms and per-check pricing.
3. **Regulators may object to data stored or backed up outside the country.** Disaster recovery may need a second cloud provider in-country, which adds cost.
4. **Rules change constantly.** Keeping country configurations current needs a dedicated regulatory-intelligence function, not a one-time build.
## Decisions needed
- [ ] Confirm **Mexico first**, and whether the first segment is **fintechs** or **regulated non-financial businesses**
- [ ] Do we serve **crypto companies** from day one?
- [ ] Identify **design partners** in Mexico and Brazil
- [ ] Expected **client count and transaction volume** in year 1 (drives infrastructure sizing)
