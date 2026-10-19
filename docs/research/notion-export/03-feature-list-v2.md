# 📋 Feature list (v2) — all LATAM
Source: https://app.notion.com/3fde5630e5e381fda6a5de38d932d48d

**Date:** 2026-10-17
**Status:** Living document. Supersedes the feature map in <mention-page url="https://app.notion.com/p/3fde5630e5e381a7a13fd6fda3ef15ad"/>.
**Evidence base:**
- <mention-page url="https://app.notion.com/p/3fde5630e5e381ad879bdf8c29dfbf98"/>
- <mention-page url="https://app.notion.com/p/3fde5630e5e38109bb2fd118304e5427"/>
- <mention-page url="https://app.notion.com/p/3fde5630e5e38194810ffb516e55f6fe"/>
Each fact there carries a confidence tag (VERIFIED / SECONDARY / UNVERIFIED) and a source URL.
**Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e38175aa91cf38caaaf97e"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e3815191c9ddd6987d3598"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e3818fb517ec69c9a9717a"/>
**Priority legend:**
- **P0** = required to sell to a regulated fintech in that country
- **P1** = expected by mid-market and enterprise buyers
- **P2** = differentiator
**Country codes:** MX, BR, CO, AR, CL, PE (core six). Rest of LATAM is in §13.
---
## 0. What the research changed
These corrections to the v1 assumptions affect the product design:
<table header-row="true">
<tr>
<td>v1 assumption</td>
<td>Reality (2026-10-17)</td>
</tr>
<tr>
<td>Brazil: Res. BCB 588/2026 adds automatic COAF reports</td>
<td>588 was **revoked before taking effect**. **Res. BCB 591/2026** keeps the same rule: automatic reports for **FX cash ≥ US\$10k** and **self-custody crypto transfers ≥ US\$10k**. Oct–Dec 2026 operations are reported on the **first business day of Jan 2027**</td>
</tr>
<tr>
<td>Mexico: "Acuerdo 115/2026" unverified</td>
<td>**Real.** Published in the DOF 07-08-2026, in force 30-11-2026, phased to 2028</td>
</tr>
<tr>
<td>Mexico: non-financial businesses report via the SAT portal in XML</td>
<td>**New XML formats are mandatory 01-06-2027.** Old formats disappear 01-07-2027. The 24-hour suspicion notices can only be filed from then</td>
</tr>
<tr>
<td>Mexico: banks and fintechs report "to the UIF"</td>
<td>They report via the CNBV's **SITI PLD/FT** system, using the 2004 **41-column semicolon-delimited TXT** format. No public API</td>
</tr>
<tr>
<td>Colombia: SAGRILAFT = Circular 100-000016/2020</td>
<td>**Repealed by CE 100-000020 (02-07-2026).** SAGRILAFT and the anti-bribery programme (PTEE) are merged, thresholds are in **UVB**, and the no-STR report (AROS) is **quarterly**. Companies must adapt by **31-05-2027**</td>
</tr>
<tr>
<td>Colombia: retain records 10 years</td>
<td>**Financial sector (SFC): 5 years.** Supersociedades companies: 10 years</td>
</tr>
<tr>
<td>Argentina: cash-report threshold in SMVM unclear</td>
<td>**40 SMVM** (Res. UIF 78/2025). SMVM uses a **31-Dec / 30-Jun snapshot rule**</td>
</tr>
<tr>
<td>Chile: Circulars 49 / 57–60, cash report at 450 UF</td>
<td>**All repealed by Circular 62 (in force 01-06-2025).** Cash report (ROE) **\> USD 10,000**, **mandatory "ROE negativo"** when there's nothing to report, beneficial owner ≥ 10%, PEP ≥ 1 year, travel rule ≥ USD 1,000</td>
</tr>
<tr>
<td>Chile: portal only</td>
<td>**From 19-10-2026, a separate automated (IP-whitelisted) channel** for cash and suspicious reports. A new platform, **MiUAF**, replaces the portal in 2027</td>
</tr>
<tr>
<td>Peru: travel rule from "Res. SBS 000132-2024"</td>
<td>It comes from **Res. SBS 02648-2024** (crypto-provider norm), with the travel rule in force **01-08-2026**. Peru **mandates OFAC + EU screening** for entities supervised directly by UIF-Perú</td>
</tr>
</table>
---
## 1. Platform foundations (all countries)
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
<td>Notes</td>
</tr>
<tr>
<td>F-1</td>
<td>**Multi-tenant isolation**: Postgres row-level security, per-tenant encryption keys, per-tenant object storage and secrets</td>
<td>P0</td>
<td>See architecture overview §3</td>
</tr>
<tr>
<td>F-2</td>
<td>**Cells per country/region** with data residency; dedicated-cell option</td>
<td>P0 (MX) / P1</td>
<td>See infrastructure doc</td>
</tr>
<tr>
<td>F-3</td>
<td>**Policy packs** per *country × sector × licence type*, versioned with effective dates. Tenants inherit packs and may only tighten them</td>
<td>P0</td>
<td>A fintech can hold several licences (e.g. an MX e-money institution that also offers crypto) → several packs</td>
</tr>
<tr>
<td>F-4</td>
<td>**Monetary unit service** with effective-dated values and **unit-specific conversion rules**</td>
<td>P0</td>
<td>MX: **UMA** (yearly, 1 Feb), **UDI** (daily), Banxico **FIX** (prior banking day). BR: BRL, USD (PTAX). CO: COP, **SMMLV**, **UVB**, **UVR**, **TRM** (of the transaction day). AR: **SMVM with 31-Dec / 30-Jun snapshot** (Res. UIF 84/2023). CL: **UF** (daily), **dólar observado** (of the operation day). PE: **UIT**, USD at the **SBS average selling rate of the previous month**</td>
</tr>
<tr>
<td>F-5</td>
<td>**Regulatory calendar engine**: business days per country (national holidays, regulator-specific non-working days), deadline rules ("first 10 business days", "day 17 or next business day", "15 business days after month-end", "within 24 h")</td>
<td>P0</td>
<td>Feeds the report scheduler, case SLAs and recurring obligations (§10). Seed it from regulator calendars (UIAF and UAF publish annual PDFs)</td>
</tr>
<tr>
<td>F-6</td>
<td>**Effective-dated rule changes**: two formats live in parallel during transitions (e.g. MX old XML until 30-06-2027 for corrections, new XML from 01-06-2027)</td>
<td>P0</td>
<td>Report definitions carry `valid_from`/`valid_to`</td>
</tr>
<tr>
<td>F-7</td>
<td>Spanish and Portuguese UI, end-user flows and narrative templates</td>
<td>P0</td>
<td></td>
</tr>
<tr>
<td>F-8</td>
<td>Audit log (hash-chained, write-once storage), per-country retention with legal hold</td>
<td>P0</td>
<td>Retention matrix in §11</td>
</tr>
<tr>
<td>F-9</td>
<td>API-first + SDKs (web/iOS/Android), signed webhooks, per-tenant sandbox with mocked registries</td>
<td>P0</td>
<td></td>
</tr>
<tr>
<td>F-10</td>
<td>Staff SSO, tenant-scoped roles (analyst / senior / compliance officer / auditor read-only), **tipping-off restricted cases**</td>
<td>P0</td>
<td>Every country prohibits tipping off</td>
</tr>
</table>
---
## 2. Onboarding & identity verification (KYC — individuals)
### 2.1 Common features
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>K-1</td>
<td>Document capture + OCR for all national IDs and passports</td>
<td>P0</td>
</tr>
<tr>
<td>K-2</td>
<td>**Government registry validation connectors** (table below) with vendor fallback and per-tenant cost metering</td>
<td>P0</td>
</tr>
<tr>
<td>K-3</td>
<td>Passive and active liveness, face match against the document and the registry photo</td>
<td>P0</td>
</tr>
<tr>
<td>K-4</td>
<td>Device fingerprint, IP and **geolocation capture** (mandatory in MX fintech rules)</td>
<td>P0</td>
</tr>
<tr>
<td>K-5</td>
<td>Video-call KYC with an agent (recorded, scripted) as a fallback</td>
<td>P1 (MX)</td>
</tr>
<tr>
<td>K-6</td>
<td>**Tiered / simplified accounts with limit enforcement** (table below)</td>
<td>P0</td>
</tr>
<tr>
<td>K-7</td>
<td>Data-collection schemas per policy pack (required fields per country, sector and tier) with validators (CURP, RFC, CPF, CUIT/CUIL check digit, RUT check digit, NIT check digit, DNI, RUC)</td>
<td>P0</td>
</tr>
<tr>
<td>K-8</td>
<td>PEP self-declaration + verification (AR requires a sworn statement; CL has a UAF "declaración de vínculo" form)</td>
<td>P0</td>
</tr>
<tr>
<td>K-9</td>
<td>Source-of-funds / source-of-wealth questionnaires for enhanced due diligence</td>
<td>P0</td>
</tr>
<tr>
<td>K-10</td>
<td>Electronic signature capture (MX: e.firma / digitized signature; BR: ICP-Brasil optional)</td>
<td>P1</td>
</tr>
<tr>
<td>K-11</td>
<td>**Periodic re-KYC scheduler** driven by risk rating and country rules (§5)</td>
<td>P0</td>
</tr>
<tr>
<td>K-12</td>
<td>Reusable KYC across tenants (consent-based identity wallet)</td>
<td>P2</td>
</tr>
</table>
### 2.2 Country specifics
<table header-row="true">
<tr>
<td></td>
<td>Registry / ID checks</td>
<td>Minimum data highlights</td>
<td>Tiered / simplified regimes</td>
<td>Remote onboarding rule</td>
</tr>
<tr>
<td>**MX**</td>
<td>**RENAPO (CURP)**, **INE** (credential + biometric match), SRE (passport, consular ID), SAT (RFC), **biometric CURP** (once SEGOB/ATDT publish the process)</td>
<td>Fintech (ITF): full name, gender, DOB, state and country of birth, nationality, voter key, address, occupation, CURP, digitized signature, phone, email, **CLABE in the client's name**, own-behalf declaration, **device geolocation**</td>
<td>Fintech **Level 1 ≤ 750 UDIs/month (balance ≤ 1,000 UDIs)**, **Level 2 ≤ 3,000 UDIs/month**. Banks Levels 1–4 + **new Level 2 Bis ≤ 15,000 UDIs/month (≤ 3,000 cash), liveness required for remote opening** (DOF 10-07-2026). Remote business accounts ≤ 30,000 UDIs/month</td>
<td>Validate against the issuing authority. Video conference allowed. Banks: **facial biometrics ≥ 90% match** (new Anexo 71, DOF 01-07-2026, \~90 business days to comply)</td>
</tr>
<tr>
<td>**BR**</td>
<td>Receita (CPF/CNPJ status), **Serpro Datavalid** (biometric + data), Pix DICT (keys)</td>
<td>Identification = **name + CPF/CNPJ**. *Qualification* = address, income/revenue, financial capacity</td>
<td>Risk-based (no fixed tiers in Circ. 3.978)</td>
<td>Technology-neutral. Must validate against public/private databases. **No relationship before identification and qualification are complete**</td>
</tr>
<tr>
<td>**CO**</td>
<td>Registraduría (cédula), DIAN (NIT, **beneficial-owner registry RUB**), RUES</td>
<td>ID type/number/issue date, name, plus full SARLAFT data</td>
<td>**Simplified only for listed products** (e.g. low-amount funds/brokerage ≤ 66,000 UVR): minimal data, rest within 3 business days</td>
<td>Expressly allowed via digital channels and *servicios ciudadanos digitales*</td>
</tr>
<tr>
<td>**AR**</td>
<td>**RENAPER** (biometric), ARCA/AFIP (CUIT/CUIL)</td>
<td>Includes **marital status**, **electronic domicile**, CUIL/CUIT/CDI, PEP sworn statement</td>
<td>**Simplified while ≤ 12 SMVM/month or ≤ 120 SMVM/year** (financial-inclusion products)</td>
<td>**"Rigorous, storable, auditable, non-manipulable biometric techniques"** with anti-spoofing. Entity documents a risk analysis; the external reviewer gives an opinion on it</td>
</tr>
<tr>
<td>**CL**</td>
<td>Registro Civil (RUN), SII (RUT)</td>
<td>Includes **tax domicile**, purpose and nature of the relationship. Occasional-transaction due diligence ≥ USD 3,000 (FX/remittance USD 5,000; notaries 1,000 UF)</td>
<td>Simplified due diligence allowed if documented in the manual</td>
<td>Electronic means allowed for all obligations</td>
</tr>
<tr>
<td>**PE**</td>
<td>**RENIEC** (DNI), SUNAT (RUC), Migraciones</td>
<td>SBS standard "Declaración Jurada de Conocimiento del Cliente" (can be digital)</td>
<td>Simplified **only for SBS pre-authorized products**. Crypto providers: simplified \< US\$1,000 non-cash</td>
<td>Allowed; non-face-to-face channel = risk factor</td>
</tr>
</table>
---
## 3. KYB & beneficial ownership
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>B-1</td>
<td>Company registry connectors: MX SIGER/RPC + SAT, BR Receita CNPJ + Juntas, CO RUES + DIAN, AR IGJ/ARCA, CL CBR/SII, PE SUNARP/SUNAT</td>
<td>P0</td>
</tr>
<tr>
<td>B-2</td>
<td>**Ownership/control graph** with recursive effective-% calculation and **country-specific thresholds** (below)</td>
<td>P0</td>
</tr>
<tr>
<td>B-3</td>
<td>Control-based beneficial owners + senior-manager fallback; "a legal person can't be the beneficial owner" rule</td>
<td>P0</td>
</tr>
<tr>
<td>B-4</td>
<td>Trust/fideicomiso roles (settlor, trustee, beneficiaries, controller), incl. MX `total_fig_juridicas` (number of legal arrangements in the ownership chain)</td>
<td>P0</td>
</tr>
<tr>
<td>B-5</td>
<td>Beneficial-owner change tracking (AR: client must notify within 30 days. CL: unjustified delay \> 40 business days = red flag)</td>
<td>P1</td>
</tr>
<tr>
<td>B-6</td>
<td>Corporate document OCR (acta constitutiva, contrato social, estatuto, poderes)</td>
<td>P2</td>
</tr>
<tr>
<td>B-7</td>
<td>Cross-check against national beneficial-owner registries where accessible (CO RUB at DIAN, PE SUNAT, MX Secretaría de Economía registry when live)</td>
<td>P2</td>
</tr>
</table>
<table header-row="true">
<tr>
<td>Country</td>
<td>Beneficial-owner threshold</td>
<td>Source</td>
</tr>
<tr>
<td>MX</td>
<td>**≥ 25%** (AML; tax regime uses 15%)</td>
<td>LFPIORPI 2025, ITF DCG</td>
</tr>
<tr>
<td>BR</td>
<td>Risk-based, **≤ 25%** reference set by the institution</td>
<td>Circ. 3.978</td>
</tr>
<tr>
<td>CO</td>
<td>**≥ 5%** (Estatuto Tributario art. 631-5)</td>
<td>SFC CE 011/2022 ⚠️ SECONDARY</td>
</tr>
<tr>
<td>AR</td>
<td>**≥ 10%**</td>
<td>Res. UIF 112/2021</td>
</tr>
<tr>
<td>CL</td>
<td>**≥ 10%**</td>
<td>Circular 62</td>
</tr>
<tr>
<td>PE</td>
<td>**≥ 10%** beneficial owner (DL 1372) + shareholders \> 25% collected</td>
<td>⚠️ SECONDARY</td>
</tr>
</table>
---
## 4. Screening, sanctions & freezing
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>S-1</td>
<td>Shared global lists: **UN consolidated** (binding everywhere), OFAC SDN/non-SDN, EU, UK</td>
<td>P0</td>
</tr>
<tr>
<td>S-2</td>
<td>**Country lists** (below), incl. **private/confidential list upload** (MX LPB)</td>
<td>P0</td>
</tr>
<tr>
<td>S-3</td>
<td>LATAM name matcher: two surnames, particles, accents/ñ/ç, word-order swaps, aliases, ID and date-of-birth tie-breakers; per-tenant tuning</td>
<td>P0</td>
</tr>
<tr>
<td>S-4</td>
<td>Continuous screening: delta rescreening of the whole customer base on every list update (same day)</td>
<td>P0</td>
</tr>
<tr>
<td>S-5</td>
<td>**Country-specific freeze/suspend workflows** with statutory notices (below)</td>
<td>P0</td>
</tr>
<tr>
<td>S-6</td>
<td>PEP screening with **country PEP definitions and cooling-off periods** (below); relatives and close associates</td>
<td>P0</td>
</tr>
<tr>
<td>S-7</td>
<td>Adverse media in Spanish/Portuguese</td>
<td>P1</td>
</tr>
<tr>
<td>S-8</td>
<td>FATF high-risk / increased-monitoring jurisdictions + tax-haven lists (MX regímenes fiscales preferentes, CL SII list) → enhanced due diligence triggers</td>
<td>P0</td>
</tr>
<tr>
<td>S-9</td>
<td>Crypto wallet screening (sanctioned addresses)</td>
<td>P1 / P0 for crypto providers</td>
</tr>
<tr>
<td>S-10</td>
<td>Unauthorised-counterparty lists: **BR unauthorised VASPs (from 06-11-2026)** and **unauthorised betting operators** (Res. BCB 590/2026); AR "client that is an obligated subject but not UIF-registered" check</td>
<td>P0 (BR, AR)</td>
</tr>
</table>
<table header-row="true">
<tr>
<td>Country</td>
<td>Mandatory / key lists</td>
<td>Freeze model & timeline</td>
<td>PEP cooling-off</td>
</tr>
<tr>
<td>**MX**</td>
<td>**LPB** (confidential, via CNBV/SAT), UN, FATF, PEP list "Consulta PEP 2.0" (\~Aug 2027)</td>
<td>**Suspend immediately**  • **24-hour unusual-operation report** marked "Lista de Personas Bloqueadas" + written notice to the client. Non-financial businesses: 24-hour aviso</td>
<td>**1 year** (national PEPs)</td>
</tr>
<tr>
<td>**BR**</td>
<td>UN (Lei 13.810), FATF, CGU PEP data</td>
<td>**Freeze immediately, no prior notice**  • notify BCB (BC Correio), Ministry of Justice and COAF</td>
<td>**5 years**</td>
</tr>
<tr>
<td>**CO**</td>
<td>UN binding lists (Ley 1121/2006); OFAC reputational; Procuraduría/Contraloría/Policía records</td>
<td>Report **immediately to UIAF (STR) and the Fiscalía**</td>
<td>**2 years** ⚠️</td>
</tr>
<tr>
<td>**AR**</td>
<td>**RePET** (public JSON feed), UN</td>
<td>**Self-executed administrative freeze without delay**, plus the terrorism-financing report (**RFT**) **within 24 h**. When the UIF orders a freeze: screen and report within 24 h</td>
<td>**2 years**</td>
</tr>
<tr>
<td>**CL**</td>
<td>UN lists (by Supreme Decree)</td>
<td>**Immediate STR (ROS)**, no analysis needed; **court-ordered freeze** at UAF request (no self-freeze)</td>
<td>**≥ 1 year**</td>
</tr>
<tr>
<td>**PE**</td>
<td>UN 1267/1718/2231 + **OFAC + EU** (mandatory for entities supervised directly by UIF-Perú)</td>
<td>Communicate **without delay**. **UIF orders administrative freeze**, which is executed immediately, including "no funds" responses</td>
<td>**5 years**</td>
</tr>
</table>
---
## 5. Risk assessment & periodic review
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>R-1</td>
<td>Configurable customer risk scoring (customer / product / channel / geography incl. **municipality-level**) with explainable factor breakdown</td>
<td>P0</td>
</tr>
<tr>
<td>R-2</td>
<td>Enhanced due diligence workflow: senior approval, source of funds/wealth, intensified monitoring</td>
<td>P0</td>
</tr>
<tr>
<td>R-3</td>
<td>**Periodic review scheduler** per country rules (below)</td>
<td>P0</td>
</tr>
<tr>
<td>R-4</td>
<td>**Enterprise-wide risk assessment module**: methodology, inherent/residual risk, controls, board approval, versioning, export</td>
<td>P0. It is a filed or inspected deliverable in AR, BR, MX, PE</td>
</tr>
<tr>
<td>R-5</td>
<td>National Risk Assessment linkage: map NRA typologies to rules (CL Circ. 62 requires it)</td>
<td>P1</td>
</tr>
<tr>
<td>R-6</td>
<td>New-product / new-geography risk assessment workflow (PE requires one before launch)</td>
<td>P1</td>
</tr>
</table>
<table header-row="true">
<tr>
<td>Country</td>
<td>KYC file update frequency</td>
</tr>
<tr>
<td>MX</td>
<td>At least **annually**, all risk levels (fintech rules; non-financial businesses too)</td>
</tr>
<tr>
<td>BR</td>
<td>Risk-based, permanent re-evaluation; CVM max 5 years</td>
</tr>
<tr>
<td>CO</td>
<td>Supersociedades: **high risk ≥ annually, medium/low ≥ every 2 years**. SFC: risk-based</td>
</tr>
<tr>
<td>AR</td>
<td>**High ≤ 1 year, medium ≤ 3 years, low ≤ 5 years**</td>
</tr>
<tr>
<td>CL</td>
<td>At least **annually** or on relevant change</td>
</tr>
<tr>
<td>PE</td>
<td>Risk-based; enhanced: more frequent + annual shareholder update; crypto-provider suppliers ≤ 2 years</td>
</tr>
</table>
---
## 6. Transaction monitoring & threshold engines
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>T-1</td>
<td>Real-time rules engine for instant payments (SPEI, Pix, CVU/CBU transfers, PSE/Bre-B), with a synchronous decision API</td>
<td>P0</td>
</tr>
<tr>
<td>T-2</td>
<td>Typology library per country (structuring under thresholds, mule rings, funnel accounts, Pix scams, FX/remittance layering, trade-based ML, crypto off-ramp). Country red-flag catalogs seeded (BR Carta Circular 4.001 + IN BCB 732/2026; CO, CL UAF guide; MX UIF guides)</td>
<td>P0</td>
</tr>
<tr>
<td>T-3</td>
<td>**Threshold accumulators** that turn every *reportable* event into a report line (table below), incl. aggregation windows (MX non-financial: 6-month accumulation; CO cash: monthly multiple; PE: monthly multiple ≥ US\$50k)</td>
<td>P0</td>
</tr>
<tr>
<td>T-4</td>
<td>**Account-tier limit enforcement** (MX UDI tiers, AR inclusion caps, PE simplified e-money caps)</td>
<td>P0</td>
</tr>
<tr>
<td>T-5</td>
<td>Behavioural/ML anomaly detection, graph analytics for mule networks</td>
<td>P1</td>
</tr>
<tr>
<td>T-6</td>
<td>Rule simulation and backtesting on the tenant's own history (effectiveness evidence for GAFILAT 5th round)</td>
<td>P1</td>
</tr>
<tr>
<td>T-7</td>
<td>**Pix fraud integration (BR)**: MED 2.0 return requests (mandatory from 02-02-2026), precautionary blocks, DICT fraud marks</td>
<td>P1 (BR)</td>
</tr>
<tr>
<td>T-8</td>
<td>Crypto monitoring: chain analytics integration, self-custody wallet detection, travel-rule data checks</td>
<td>P1 / P0 for crypto providers</td>
</tr>
</table>
**Automatic (threshold) reporting triggers to implement:**
<table header-row="true">
<tr>
<td>Country</td>
<td>Trigger (value = threshold)</td>
</tr>
<tr>
<td>MX banks</td>
<td>Cash **≥ USD 7,500** (unusual-op regime, ⚠️ SECONDARY), USD cash, cashier's cheques, international transfers</td>
</tr>
<tr>
<td>MX fintechs (ITF)</td>
<td>Cash **≥ USD 5,000**. Foreign-currency cash **≥ USD 500**. International transfers **≥ USD 1,000** (monthly). Crypto buy/sell **≥ 7,500 UDIs**</td>
</tr>
<tr>
<td>MX non-financial businesses (LFPIORPI)</td>
<td>Per-activity UMA thresholds, e.g. crypto **≥ 210 UMA per operation or fee ≥ 4 UMA**; real estate 8,025 UMA; vehicles 6,420 UMA; 6-month aggregation</td>
</tr>
<tr>
<td>BR (BCB)</td>
<td>Cash **≥ R\$ 50,000** (deposits, withdrawals, payments, provisioning requests). **FX cash ≥ US\$10,000**. **Self-custody crypto transfers ≥ US\$10,000** (Res. 591/2026)</td>
</tr>
<tr>
<td>BR (others)</td>
<td>Factoring cash ≥ R\$ 50k. Luxury goods cash ≥ R\$ 30k per client per 6 months. SUSEP cash premiums ≥ R\$ 10k ⚠️</td>
</tr>
<tr>
<td>CO (SFC)</td>
<td>Cash **≥ COP 10M individual / ≥ COP 50M monthly multiple**. **All** FX operations. Foreign-card transactions ≥ COP 10M</td>
</tr>
<tr>
<td>CO cooperatives (Supersolidaria)</td>
<td>Cash ≥ COP 5M / ≥ COP 30M per month (scheduled reduction suspended)</td>
</tr>
<tr>
<td>CO crypto providers</td>
<td>Individual **≥ USD 150**, monthly multiple **≥ USD 450**</td>
</tr>
<tr>
<td>AR</td>
<td>Cash **≥ 40 SMVM** (bank deposits/withdrawals, FX). International transfers **≥ 1 SMVM**. Crypto providers **≥ 6 SMVM**. Payment providers: cards ≥ 13/50 SMVM, cash ≥ 2 SMVM/month</td>
</tr>
<tr>
<td>CL</td>
<td>Cash **\> USD 10,000** (each operation)</td>
</tr>
<tr>
<td>PE (financial)</td>
<td>Single ops **≥ US\$10,000**. Cash transfers ≥ US\$2,500. Cash FX ≥ US\$5,000. Multiple ops ≥ US\$50,000/month (kept on file). Crypto providers: any cash op, others ≥ US\$1,000</td>
</tr>
</table>
---
## 7. Case management & STR workflow
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>C-1</td>
<td>Alert triage → case → investigation, four-eyes approval, evidence attachments, chronology builder</td>
<td>P0</td>
</tr>
<tr>
<td>C-2</td>
<td>**Country-specific STR clocks** (below), with SLA timers and escalation</td>
<td>P0</td>
</tr>
<tr>
<td>C-3</td>
<td>**Compliance committee workflow (MX)**: unusual-op and internal-concern reports need a committee decision ≤ 60 days after detection, then filing ≤ 3 business days. Meeting minutes and votes</td>
<td>P0 (MX)</td>
</tr>
<tr>
<td>C-4</td>
<td>**Narrative assistant** per country template and length limits (CL: facts 250–1,000 chars + suspicion 250–500 chars; CO: who/what/why/when/where; MX UIF best-practice structure for narrative columns 40/41; BR "Informações Adicionais")</td>
<td>P1</td>
</tr>
<tr>
<td>C-5</td>
<td>Related-party capture with country role codes (MX related-persons rows; BR "Tipo de Envolvimento"; AR "relación con el hecho": direct / indirect / due-diligence failure)</td>
<td>P0</td>
</tr>
<tr>
<td>C-6</td>
<td>Tipping-off controls: restricted cases, no client-facing leakage, PE "no OC/entity identification inside the STR"</td>
<td>P0</td>
</tr>
<tr>
<td>C-7</td>
<td>Regulator information requests tracker (CO **5 business days**; PE records available to SBS within **48 business hours**; AR UIF requests)</td>
<td>P1</td>
</tr>
<tr>
<td>C-8</td>
<td>Employee/insider cases (MX internal-concern reports, "ROIP")</td>
<td>P1</td>
</tr>
</table>
<table header-row="true">
<tr>
<td>Country</td>
<td>STR deadline</td>
</tr>
<tr>
<td>MX financial</td>
<td>Committee decides ≤ 60 days after detection → file ≤ **3 business days** after. **24-hour report** for illicit-funds indications or a blocked-persons (LPB) hit</td>
</tr>
<tr>
<td>MX non-financial (LFPIORPI)</td>
<td>**24 h** from suspicion (fileable from 01-06-2027)</td>
</tr>
<tr>
<td>BR</td>
<td>Analysis ≤ 45 days from selection → file by the **business day after the decision**. CVM: 24 h after concluding the analysis</td>
</tr>
<tr>
<td>CO</td>
<td>**Immediately** after determining suspicion</td>
</tr>
<tr>
<td>AR</td>
<td>**24 h after concluding suspicion, max 90 days from the operation**. Terrorism/proliferation financing: **24 h from the operation**</td>
</tr>
<tr>
<td>CL</td>
<td>"As soon as possible"; UN-list hits immediately</td>
</tr>
<tr>
<td>PE</td>
<td>**≤ 24 h** from the compliance officer's qualification as suspicious</td>
</tr>
</table>
---
## 8. Regulatory Reporting Engine (core differentiator)
### 8.1 Engine capabilities
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>RR-1</td>
<td>**Report definition registry**: each report = (country, regulator, sector, report type, version, `valid_from`/`valid_to`, trigger query, field mapping, serializer, channel, deadline rule, zero-report rule, correction rule). Definitions are versioned; the tenant's policy pack selects them</td>
<td>P0</td>
</tr>
<tr>
<td>RR-2</td>
<td>**Serializers** (below). Each validates against the official schema or layout **before** filing</td>
<td>P0</td>
</tr>
<tr>
<td>RR-3</td>
<td>**Character/encoding normalizers**: uppercase, strip accents except Ñ (MX), CP1252 (CO), UTF-8 (MX XML), integer-only amounts without cents (BR Siscoaf), special-char removal (AR: D'Angelo → DANGELO), fixed-width padding and alignment (CO), check-digit validation (CUIT, RUT, NIT)</td>
<td>P0</td>
</tr>
<tr>
<td>RR-4</td>
<td>**Code-table service**: versioned regulator catalogs (MX operation types / monetary instruments / alert types / CASFIM codes; CO product, transaction and ID-type tables + DANE municipality codes; BR occurrence codes ("enquadramentos") and "Tipo de Envolvimento"; AR provinces / person types; CL regions/comunas; ISO currencies and countries)</td>
<td>P0</td>
</tr>
<tr>
<td>RR-5</td>
<td>**Zero / no-operations reports** generated automatically when a period closes with no reportable events</td>
<td>P0</td>
</tr>
<tr>
<td>RR-6</td>
<td>**Corrections lifecycle**: MX non-financial correction notice (once, within 30 days); BR correction/cancellation (justification after the 5th business day); CO annulment request → re-upload; AR bulk annul/rectify; CL ROE correction within 10 business days</td>
<td>P0</td>
</tr>
<tr>
<td>RR-7</td>
<td>**Approval workflow**: draft → compliance-officer approval (four-eyes) → file → acknowledgment → archive (immutable copy of the exact bytes filed + receipt)</td>
<td>P0</td>
</tr>
<tr>
<td>RR-8</td>
<td>**Filing channel adapters** (§8.2)</td>
<td>P0</td>
</tr>
<tr>
<td>RR-9</td>
<td>**Deadline dashboard**: every obligation per tenant per period: due date, status, receipt, late flag; certificates (e.g. CL "Certificado ROE" valid 60 days)</td>
<td>P0</td>
</tr>
<tr>
<td>RR-10</td>
<td>Report preview + regulator-side validation simulation (e.g. CO SIREL rejects the whole file on a one-position shift; MX 2 MB XML cap; BR 500 occurrences per batch ⚠️)</td>
<td>P0</td>
</tr>
<tr>
<td>RR-11</td>
<td>**Credential vault for filings**: e.firma (MX), ICP-Brasil e-CPF (BR), SIREL users (CO), CUIT/clave (AR), Clave Única + UAF key (CL), Portal PLAFT credentials (PE) — tenant-owned, never visible to platform staff</td>
<td>P0</td>
</tr>
<tr>
<td>RR-12</td>
<td>Regulator template change monitoring (watch DOF/DOU/Boletín Oficial/regulator pages; alert product team to update definitions)</td>
<td>P1</td>
</tr>
</table>
**Serializers needed:**
<table header-row="true">
<tr>
<td>Serializer</td>
<td>Used for</td>
</tr>
<tr>
<td>**XML + XSD validation**</td>
<td>MX non-financial avisos (per-activity XSD, current + 2027 versions); BR Siscoaf batch XML; AR bulk report XML (one `<Operacion>` per file); AR registration & compliance report XSD v1.2</td>
</tr>
<tr>
<td>**Delimited TXT**</td>
<td>MX "Formato 41" (`;`, 41 columns, uppercase); CO crypto-provider reports (`|`, CP1252, header/body/trailer)</td>
</tr>
<tr>
<td>**Fixed-width TXT**</td>
<td>CO SFC annexes (e.g. cash = 645 positions, FX = 1,760, CP1252, header/body/trailer records); CL bank cash report "generic data file" ⚠️ layout behind login</td>
</tr>
<tr>
<td>**Excel templates**</td>
<td>CL non-bank cash report (2-sheet / 5-sheet); PE ROSEL / RO / annual compliance-officer report templates ⚠️ behind login; MX Excel→XML helpers</td>
</tr>
<tr>
<td>**Web-form packages**</td>
<td>Structured JSON + narrative + attachments, ready for a human to paste where no file channel exists: CO STR (SIREL), AR STR/RFT/RFP, CL STR (until automated), BR single forms</td>
</tr>
</table>
### 8.2 Filing automation by country
<table header-row="true">
<tr>
<td>Level</td>
<td>Meaning</td>
<td>Where</td>
</tr>
<tr>
<td>**L3 — Direct API**</td>
<td>Platform submits machine-to-machine with the tenant's credential</td>
<td>**BR Siscoaf SOAP web service** (`EnviarLoteComunicacao`, correction, cancellation, receipt; e-CPF-signed). **CL automated ROE/STR channel** (from 19-10-2026; spec on request from UAF; IP-whitelisted)</td>
</tr>
<tr>
<td>**L2 — Bulk file upload**</td>
<td>Platform generates a validated file; a tenant user (or an agent we provide) uploads it</td>
<td>MX SAT portal (XML); MX CNBV SITI (Formato 41 TXT); CO SIREL (fixed-width/pipe TXT); AR "SRO Masivo" desktop app (XML folder); CL portal (cash-report files); PE Portal PLAFT templates</td>
</tr>
<tr>
<td>**L1 — Guided web form**</td>
<td>Platform produces a filing package + field-by-field guide; a human keys it in; receipt captured back</td>
<td>CO STR, AR STR/RFT/RFP, CL STR (pre-automation), PE ROSEL, BR single forms</td>
</tr>
</table>
Browser automation of regulator portals is **not** planned by default (terms-of-service, credential and audit risk). Revisit per regulator.
---
## 9. Report catalog — core six countries
Full detail (fields, layouts, URLs, confidence) is in the raw research files. ⚠️ = layout or detail not yet obtained.
### 9.1 Mexico
<table header-row="true">
<tr>
<td>Report</td>
<td>Who</td>
<td>Trigger</td>
<td>Deadline</td>
<td>Channel / format</td>
<td>Zero?</td>
</tr>
<tr>
<td>Relevant-operations report (ROR)</td>
<td>Banks, SOFOMs, fintechs (ITFs), etc.</td>
<td>Cash ≥ USD 7,500 (banks ⚠️) / ≥ USD 5,000 (fintechs)</td>
<td>Quarterly, first 10 business days of Jan/Apr/Jul/Oct</td>
<td>SITI PLD/FT · Formato 41 TXT</td>
<td>**Yes** (empty report)</td>
</tr>
<tr>
<td>Unusual-operations report (ROI)</td>
<td>idem</td>
<td>Profile mismatch etc.; committee ≤ 60 days</td>
<td>3 business days after committee</td>
<td>SITI · Formato 41</td>
<td>No</td>
</tr>
<tr>
<td>24-hour unusual-operations report</td>
<td>idem</td>
<td>Illicit-funds indications / blocked-persons (LPB) hit</td>
<td>≤ 24 h</td>
<td>SITI · Formato 41 + legend</td>
<td>No</td>
</tr>
<tr>
<td>Internal-concern report (ROIP)</td>
<td>idem</td>
<td>Employee conduct</td>
<td>3 business days after committee</td>
<td>SITI · Formato 41</td>
<td>No</td>
</tr>
<tr>
<td>Foreign-currency cash report</td>
<td>Fintechs (+ banks, FX houses)</td>
<td>FX cash ≥ USD 500</td>
<td>Quarterly</td>
<td>SITI · UIF format ⚠️</td>
<td>Yes</td>
</tr>
<tr>
<td>International transfers report</td>
<td>Banks, SOFOMs, fintechs</td>
<td>≥ USD 1,000</td>
<td>Monthly, 15 business days</td>
<td>SITI · UIF format ⚠️</td>
<td>Yes</td>
</tr>
<tr>
<td>Money transmission report</td>
<td>E-money institutions (IFPE) transmitting money</td>
<td>≥ USD 1,000</td>
<td>Monthly</td>
<td>SITI ⚠️</td>
<td>Yes</td>
</tr>
<tr>
<td>Crypto operations report</td>
<td>Fintechs with Banxico-authorized crypto</td>
<td>≥ 7,500 UDIs</td>
<td>Quarterly</td>
<td>SITI ⚠️</td>
<td>Yes</td>
</tr>
<tr>
<td>**Aviso** (non-financial businesses, incl. crypto under fr. XVI)</td>
<td>Non-financial regulated businesses</td>
<td>Per-activity UMA thresholds, 6-month aggregation</td>
<td>Day 17 of next month</td>
<td>SAT portal (SPPLD) · XML/XSD (new XSDs mandatory 01-06-2027)</td>
<td>**Monthly zero report**</td>
</tr>
<tr>
<td>24-hour aviso (suspicion / indications / LPB)</td>
<td>Non-financial businesses</td>
<td>Suspicion, any amount</td>
<td>24 h (from 01-06-2027)</td>
<td>SAT portal · XML</td>
<td>—</td>
</tr>
<tr>
<td>Correction aviso</td>
<td>Non-financial businesses</td>
<td>Correction of an accepted aviso</td>
<td>Once, ≤ 30 days</td>
<td>SAT portal · XML</td>
<td>—</td>
</tr>
<tr>
<td>Compliance officer / committee / manual notices; annual AML audit to CNBV</td>
<td>Financial entities</td>
<td>Events / annual</td>
<td>10–20 business days; audit ≤ 60 days after year-end</td>
<td>CNBV</td>
<td>—</td>
</tr>
</table>
### 9.2 Brazil
<table header-row="true">
<tr>
<td>Report</td>
<td>Who</td>
<td>Trigger</td>
<td>Deadline</td>
<td>Channel / format</td>
<td>Zero?</td>
</tr>
<tr>
<td>Suspicious-activity communication ("COS")</td>
<td>BCB-regulated</td>
<td>Suspicion; analysis ≤ 45 days</td>
<td>Business day after the decision</td>
<td>Siscoaf · form / XML batch / **SOAP**</td>
<td>—</td>
</tr>
<tr>
<td>Automatic communication ("COE"), Art. 49</td>
<td>BCB-regulated</td>
<td>Cash ≥ R\$ 50k; **FX cash ≥ US\$10k; self-custody crypto ≥ US\$10k**</td>
<td>Next business day (items IV–V for Oct–Dec 2026: first business day of Jan 2027)</td>
<td>Siscoaf</td>
<td>—</td>
</tr>
<tr>
<td>No-occurrence communication (CNO)</td>
<td>BCB-regulated</td>
<td>No communications in the year</td>
<td>10 business days after year-end</td>
<td>Siscoaf form</td>
<td>**Is** the zero report</td>
</tr>
<tr>
<td>Asset-freeze communication</td>
<td>All</td>
<td>UN sanctions match</td>
<td>Immediately</td>
<td>BC Correio + Ministry of Justice + Siscoaf</td>
<td>—</td>
</tr>
<tr>
<td>CVM suspicious communication / CNO</td>
<td>Securities</td>
<td>Suspicion / none</td>
<td>24 h after analysis / last business day of April</td>
<td>Siscoaf</td>
<td>Yes</td>
</tr>
<tr>
<td>SUSEP communications / CNO</td>
<td>Insurers</td>
<td>Suspicion; cash premiums ≥ R\$ 10k ⚠️</td>
<td>⚠️ / last business day of March ⚠️</td>
<td>Siscoaf</td>
<td>Yes</td>
</tr>
<tr>
<td>COAF-supervised sectors (factoring, luxury goods, jewelry…)</td>
<td>DNFBPs</td>
<td>Sector thresholds</td>
<td>24 h / next business day</td>
<td>Siscoaf</td>
<td>CNO by 31 Jan</td>
</tr>
<tr>
<td>Effectiveness report; action plan</td>
<td>BCB-regulated</td>
<td>Annual</td>
<td>Board by 31 Mar; action plan by 30 Jun</td>
<td>Internal (inspected)</td>
<td>—</td>
</tr>
</table>
### 9.3 Colombia
<table header-row="true">
<tr>
<td>Report</td>
<td>Who</td>
<td>Trigger</td>
<td>Deadline</td>
<td>Channel / format</td>
<td>Zero?</td>
</tr>
<tr>
<td>STR (ROS)</td>
<td>All</td>
<td>Suspicion</td>
<td>Immediate</td>
<td>SIREL web form + attachments ≤ 20 MB</td>
<td>**Monthly absence report** (SFC, crypto providers); **quarterly AROS** (Supersociedades)</td>
</tr>
<tr>
<td>Cash transactions (Anexo 2)</td>
<td>SFC entities</td>
<td>≥ COP 10M / ≥ COP 50M per month</td>
<td>Monthly, 20 calendar days</td>
<td>SIREL · fixed-width 645, CP1252</td>
<td>Yes</td>
</tr>
<tr>
<td>Exempted clients (Anexo 3)</td>
<td>SFC</td>
<td>Exempted clients</td>
<td>Quarterly</td>
<td>Fixed-width 299</td>
<td>Yes</td>
</tr>
<tr>
<td>FX operations (Anexo 4)</td>
<td>FX intermediaries</td>
<td>**All**</td>
<td>Monthly</td>
<td>Fixed-width 1,760</td>
<td>Yes</td>
</tr>
<tr>
<td>Foreign cards (Anexo 5)</td>
<td>Card franchise representatives</td>
<td>≥ COP 10M</td>
<td>Monthly</td>
<td>Fixed-width 176</td>
<td>Yes</td>
</tr>
<tr>
<td>Products (Anexo 6)</td>
<td>SFC</td>
<td>All products</td>
<td>Monthly</td>
<td>Fixed-width (2 files per sub-report)</td>
<td>Yes</td>
</tr>
<tr>
<td>APP trusts / political campaigns (Anexos 7–8)</td>
<td>Fiduciarias etc.</td>
<td>Event / all</td>
<td>3 business days / monthly</td>
<td>Fixed-width</td>
<td>Yes</td>
</tr>
<tr>
<td>Crypto provider clients / transactions</td>
<td>Crypto providers (Supersociedades)</td>
<td>All clients / ≥ USD 150 or ≥ USD 450 per month</td>
<td>Monthly</td>
<td>Pipe-delimited TXT</td>
<td>Yes</td>
</tr>
<tr>
<td>Cooperative transactions, products, cards</td>
<td>Supersolidaria cooperatives</td>
<td>≥ COP 5M / 30M</td>
<td>Monthly</td>
<td>SIREL TXT ⚠️</td>
<td>Yes</td>
</tr>
<tr>
<td>Listed-person assets</td>
<td>All</td>
<td>UN-list match</td>
<td>Immediate</td>
<td>SIREL + Fiscalía</td>
<td>—</td>
</tr>
<tr>
<td>Compliance officer designation ("informe no financiero")</td>
<td>Supersociedades</td>
<td>Appointment</td>
<td>15 business days</td>
<td>Supersociedades portal</td>
<td>—</td>
</tr>
</table>
### 9.4 Argentina
<table header-row="true">
<tr>
<td>Report</td>
<td>Who</td>
<td>Trigger</td>
<td>Deadline</td>
<td>Channel / format</td>
<td>Zero?</td>
</tr>
<tr>
<td>STR (ROS-LA)</td>
<td>All</td>
<td>Suspicion</td>
<td>24 h after concluding, ≤ 90 days from the operation</td>
<td>SRO+ web form</td>
<td>No</td>
</tr>
<tr>
<td>Terrorism / proliferation financing reports (RFT / RFP) + freeze report</td>
<td>All</td>
<td>RePET/UN match or FT indications</td>
<td>24 h from the operation</td>
<td>SRO+</td>
<td>No</td>
</tr>
<tr>
<td>Response to a UIF freeze order</td>
<td>All</td>
<td>UIF freeze resolution</td>
<td>24 h</td>
<td>SRO+</td>
<td>—</td>
</tr>
<tr>
<td>Cash transactions (RTE)</td>
<td>Banks & FX</td>
<td>≥ 40 SMVM</td>
<td>Monthly, day 1–15</td>
<td>SRO+ web template or XML (SRO Masivo) ⚠️ XSD</td>
<td>**"Período sin movimientos"**</td>
</tr>
<tr>
<td>Cash in FX operations (RTEOC)</td>
<td>FX</td>
<td>≥ 40 SMVM</td>
<td>Monthly, day 1–15</td>
<td>idem</td>
<td>Yes</td>
</tr>
<tr>
<td>International transfers (RTI)</td>
<td>Banks & FX</td>
<td>≥ 1 SMVM</td>
<td>Monthly, day 1–15</td>
<td>idem</td>
<td>Yes</td>
</tr>
<tr>
<td>Crypto-provider operations; client additions/removals</td>
<td>Crypto providers (PSAV)</td>
<td>≥ 6 SMVM / all</td>
<td>Monthly, day 1–15</td>
<td>idem</td>
<td>Yes</td>
</tr>
<tr>
<td>Payment-provider card report (RMTC) and cash report</td>
<td>Payment providers (PSP)</td>
<td>13/50 SMVM; cash 2 SMVM</td>
<td>Monthly</td>
<td>idem</td>
<td>Yes</td>
</tr>
<tr>
<td>Annual systematic report (RSA)</td>
<td>Financial, payment providers, crypto providers, insurers…</td>
<td>Annual company data</td>
<td>2 Jan – 15 Mar</td>
<td>SRO+ form</td>
<td>—</td>
</tr>
<tr>
<td>Risk self-assessment + methodology</td>
<td>All 2023–24 regimes</td>
<td>Annual</td>
<td>Before 30 Apr</td>
<td>UIF + sector supervisor</td>
<td>—</td>
</tr>
<tr>
<td>Independent external reviewer (REI) report</td>
<td>idem</td>
<td>Annual</td>
<td>≤ 120 days after the self-assessment deadline</td>
<td>UIF</td>
<td>—</td>
</tr>
<tr>
<td>Registration & compliance report</td>
<td>All</td>
<td>Client that is an obligated subject is not UIF-registered</td>
<td>Monthly</td>
<td>XML (XSD v1.2)</td>
<td>—</td>
</tr>
</table>
### 9.5 Chile
<table header-row="true">
<tr>
<td>Report</td>
<td>Who</td>
<td>Trigger</td>
<td>Deadline</td>
<td>Channel / format</td>
<td>Zero?</td>
</tr>
<tr>
<td>STR (ROS)</td>
<td>All</td>
<td>Suspicion; UN-list hit (immediate)</td>
<td>ASAP</td>
<td>Portal → automated channel from 19-10-2026 → MiUAF 2027 · web form (+ bank CSV ⚠️)</td>
<td>No</td>
</tr>
<tr>
<td>Cash-operations report (ROE)</td>
<td>All</td>
<td>Cash \> USD 10,000</td>
<td>Monthly (banks) / quarterly (non-bank financial & fintech) / semiannual (non-financial); first 10 business days</td>
<td>Portal / automated · bank TXT, non-bank Excel ⚠️ layouts behind login</td>
<td>**ROE negativo**</td>
</tr>
<tr>
<td>ROE correction</td>
<td>All</td>
<td>Error</td>
<td>≤ 10 business days after deadline</td>
<td>Contact form</td>
<td>—</td>
</tr>
<tr>
<td>Registration / changes</td>
<td>All</td>
<td>Change</td>
<td>10 business days</td>
<td>[uaf.cl](http://uaf.cl) → MiUAF</td>
<td>—</td>
</tr>
</table>
### 9.6 Peru
<table header-row="true">
<tr>
<td>Report</td>
<td>Who</td>
<td>Trigger</td>
<td>Deadline</td>
<td>Channel / format</td>
<td>Zero?</td>
</tr>
<tr>
<td>STR (ROS)</td>
<td>All</td>
<td>Suspicion</td>
<td>≤ 24 h after qualification</td>
<td>ROSEL · Plantilla ROSEL ⚠️</td>
<td>⚠️ unknown</td>
</tr>
<tr>
<td>Operations register (RO), financial</td>
<td>SBS-supervised</td>
<td>≥ US\$10k (and sub-thresholds)</td>
<td>Monthly ⚠️ (SUCAVE)</td>
<td>SBS electronic ⚠️</td>
<td>⚠️</td>
</tr>
<tr>
<td>Operations register (RO), non-financial / crypto providers</td>
<td>UIF-supervised, crypto providers</td>
<td>Sector thresholds; crypto any cash / ≥ US\$1k</td>
<td>Per instructions; notaries by month-end +1</td>
<td>Portal PLAFT templates ⚠️</td>
<td>⚠️</td>
</tr>
<tr>
<td>Compliance officer annual report (IAOC)</td>
<td>UIF-supervised, crypto providers</td>
<td>Annual</td>
<td>By 15 Feb (crypto providers)</td>
<td>Portal PLAFT</td>
<td>—</td>
</tr>
<tr>
<td>Compliance officer semiannual report</td>
<td>Financial</td>
<td>Semiannual</td>
<td>Board + SBS within 15 days</td>
<td>SBS</td>
<td>—</td>
</tr>
<tr>
<td>UN asset communication / freeze execution</td>
<td>All</td>
<td>Match / UIF order</td>
<td>Without delay</td>
<td>UIF-Perú</td>
<td>—</td>
</tr>
</table>
---
## 10. Recurring compliance obligations (governance module)
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>G-1</td>
<td>**Obligations calendar per tenant** generated from policy packs (rows below), with owners, evidence upload and board-approval tracking</td>
<td>P0</td>
</tr>
<tr>
<td>G-2</td>
<td>Compliance officer registry and **change-notice generation** (MX 10 business days; CO Supersociedades 15 business days; AR 24 h for an acting substitute / 15 days for a new appointment; CL 10 business days; PE 15 days / 5 business days via SISDEL)</td>
<td>P0</td>
</tr>
<tr>
<td>G-3</td>
<td>AML manual versioning + distribution with staff acknowledgment (MX: send to CNBV within 20 business days of approval; CL: update ≤ 2 years; PE: staff sworn statements)</td>
<td>P1</td>
</tr>
<tr>
<td>G-4</td>
<td>Training module: annual plans, attendance, evaluations, certificates (MX trainers ≥ 5 years' experience; AR induction ≤ 60 business days; PE induction ≤ 30 days)</td>
<td>P1</td>
</tr>
<tr>
<td>G-5</td>
<td>Independent review / audit support: evidence packs for MX annual AML audit (CNBV-certified signer), AR external reviewer (REI), PE internal and external audit, BR effectiveness report</td>
<td>P1</td>
</tr>
<tr>
<td>G-6</td>
<td>Board / committee reporting packs (CO semiannual SARLAFT; PE quarterly + semiannual compliance-officer reports; BR effectiveness report)</td>
<td>P1</td>
</tr>
</table>
<table header-row="true">
<tr>
<td>Country</td>
<td>Key recurring obligations</td>
</tr>
<tr>
<td>MX</td>
<td>Annual risk assessment; annual AML audit to CNBV ≤ 60 days after year-end; annual training; annual KYC file review. Non-financial businesses: risk assessment available from 01-03-2027, monitoring from 01-06-2027, first audit period 2028</td>
</tr>
<tr>
<td>BR</td>
<td>Internal risk assessment every 2 years; effectiveness report by 31 Mar; action plan by 30 Jun; CNO; Siscoaf/COAF registrations; crypto-provider authorisation and supervisory feeds</td>
</tr>
<tr>
<td>CO</td>
<td>SARLAFT review every 6 months; internal audit every 6 months; annual training; Supersociedades policy update ≤ 2 years; compliance-officer report to board</td>
</tr>
<tr>
<td>AR</td>
<td>Risk self-assessment by 30 Apr; external reviewer report ≤ 120 days later; annual systematic report 2 Jan–15 Mar; monthly systematic reports; SMVM snapshots 1 Jan / 1 Jul</td>
</tr>
<tr>
<td>CL</td>
<td>Manual update ≤ 2 years; annual training; cash-report calendar; registration updates</td>
</tr>
<tr>
<td>PE</td>
<td>Compliance-officer annual report (by 15 Feb for crypto providers); quarterly/semiannual compliance-officer reports (financial); risk assessment ≤ 3 years (UIF-supervised); annual training; internal and external audit</td>
</tr>
</table>
---
## 11. Data protection, retention & residency
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Pri</td>
</tr>
<tr>
<td>D-1</td>
<td>**Retention engine** per country × sector (below), legal hold, crypto-shredding after expiry</td>
<td>P0</td>
</tr>
<tr>
<td>D-2</td>
<td>Consent and purpose capture, data-subject rights workflows (access, correction; deletion blocked by AML retention with an explanation)</td>
<td>P0</td>
</tr>
<tr>
<td>D-3</td>
<td>Biometric data handled as **sensitive data** (separate keys, minimal retention, explicit consent)</td>
<td>P0</td>
</tr>
<tr>
<td>D-4</td>
<td>Cross-border transfer controls + in-country residency per cell</td>
<td>P0 / P1</td>
</tr>
<tr>
<td>D-5</td>
<td>Data-processing agreement templates per law: MX LFPDPPP, BR LGPD, CO Ley 1581, AR Ley 25.326, **CL Ley 21.719 (from 01-12-2026)**, PE Ley 29733</td>
<td>P0</td>
</tr>
</table>
<table header-row="true">
<tr>
<td>Country</td>
<td>Record retention</td>
</tr>
<tr>
<td>MX</td>
<td>**10 years** (financial + non-financial)</td>
</tr>
<tr>
<td>BR</td>
<td>BCB **10 years**; CVM / COAF-supervised **5 years**</td>
</tr>
<tr>
<td>CO</td>
<td>SFC **5 years**; Supersociedades & others **10 years**</td>
</tr>
<tr>
<td>AR</td>
<td>**10 years**</td>
</tr>
<tr>
<td>CL</td>
<td>**≥ 5 years** (UN-screening evidence ≥ 3 years)</td>
</tr>
<tr>
<td>PE</td>
<td>Financial RO **10 years**; UIF-supervised **5 years** (⚠️ conflict with the Reglamento — keep 10)</td>
</tr>
</table>
---
## 12. Crypto / VASP module (P0 for crypto-provider tenants, else P1)
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Countries</td>
</tr>
<tr>
<td>V-1</td>
<td>**Travel rule** (IVMS101; protocol aggregator integration)</td>
<td>**BR** phase I 02-02-2027 / phase II 02-02-2028; **AR** Res. 49/2024 art. 37; **CL** ≥ USD 1,000 since 2025; **PE** 01-08-2026; **CO** (reports carry counterparty VASP/wallet data)</td>
</tr>
<tr>
<td>V-2</td>
<td>Self-custody wallet handling: ownership proof, risk screening, **BR automatic COAF report ≥ US\$10k**</td>
<td>BR, PE (refuse if no counterparty info)</td>
</tr>
<tr>
<td>V-3</td>
<td>Crypto threshold reports</td>
<td>MX non-financial (fr. XVI) and fintechs; CO monthly client/transaction files; AR monthly; PE RO</td>
</tr>
<tr>
<td>V-4</td>
<td>MX fr. XVI new XML fields: wallet number, transfer network, geolocation; retention of travel-rule-like data (10 years)</td>
<td>MX</td>
</tr>
<tr>
<td>V-5</td>
<td>Licensing-status checks: **BR crypto providers must be BCB-authorised; banks can't deal with unauthorised ones from 06-11-2026**; AR CNV PSAV registry; MX non-financial crypto registration update by \~30-05-2027</td>
<td>BR, AR, MX</td>
</tr>
</table>
---
## 13. Rest of LATAM
Source: <mention-page url="https://app.notion.com/p/3fde5630e5e38194810ffb516e55f6fe"/>.
⚠️ **Confidence is lower than for the core six.** Most STR and cash-report rules are SECONDARY (law firms, press), and many beneficial-owner, PEP and retention figures are UNVERIFIED. **No native file layout was retrieved** for these countries; most are behind regulated-entity logins.
<table header-row="true">
<tr>
<td>Country</td>
<td>FIU / channel</td>
<td>STR deadline</td>
<td>Threshold report</td>
<td>Format</td>
<td>Beneficial-owner %</td>
<td>Crypto regime</td>
<td>Priority</td>
</tr>
<tr>
<td>**Ecuador**</td>
<td>UAFE · SISLAFT</td>
<td>**5 days** (Res. UAFE-DG-2026-0007, in force 29-04-2026). **Monthly no-STR report (NO-ROS) by day 10**</td>
<td>**RESU ≥ USD 10,000** (any instrument, 30-day aggregation), monthly within 15 days</td>
<td>Structured upload ⚠️ layout + web STR</td>
<td>10% (SRI)</td>
<td>Partial: obligated, no licence</td>
<td>**P1**</td>
</tr>
<tr>
<td>**Uruguay**</td>
<td>UIAF (BCU)</td>
<td>"Without delay" ⚠️</td>
<td>Cash \> USD 10k, transfers \> USD 1k ⚠️. **New e-money issuer report (Jun 2026)**. Ley 20.469 (Mar 2026): cash cap \~USD 33k; FIU can block operations for 5 business days</td>
<td>Web form + token digital signature</td>
<td>15% ⚠️</td>
<td>**Yes**: crypto-provider regime (Jul 2026), licence applications 01-09-2026 → 31-03-2027</td>
<td>**P1**</td>
</tr>
<tr>
<td>**Panama**</td>
<td>UAF · UAF en Línea; bank supervisor (SBP)</td>
<td>**Immediately**</td>
<td>**Cash/quasi-cash ≥ USD 10,000** (weekly aggregation). Non-bank firms also file a **monthly OFAC-screening certification**  • half-yearly Excel reports to SBP</td>
<td>Web form or xlsx/csv/txt upload</td>
<td>\> 10% (SBP) / 25% ⚠️</td>
<td>No (bills pending)</td>
<td>**P1**</td>
</tr>
<tr>
<td>**Costa Rica**</td>
<td>UIF-ICD · UIF-Directo; SUGEF</td>
<td>"Without delay" ⚠️</td>
<td>Cash ≥ USD 10,000</td>
<td>SUGEF XML data classes; Firma Digital</td>
<td>**15%** (beneficial-owner register)</td>
<td>**New**: Ley 10961 (Jun 2026) SUGEF registration; regulation pending</td>
<td>P2</td>
</tr>
<tr>
<td>**Dominican Rep.**</td>
<td>UAF · **goAML**</td>
<td>5 business days ⚠️</td>
<td>**Cash \> USD 15,000**</td>
<td>**goAML XML** (the UN's standard FIU reporting system, also used in many other countries)</td>
<td>20% ⚠️</td>
<td>No</td>
<td>P2</td>
</tr>
<tr>
<td>**Guatemala**</td>
<td>IVE (SIB)</td>
<td>Pending regulation (due by 17-03-2027)</td>
<td>Cash ≥ USD 10,000</td>
<td>Legacy IVE electronic forms</td>
<td>15% or 20% (sources conflict)</td>
<td>**New**: Decreto 15-2026 (in force 17-09-2026) covers crypto providers</td>
<td>P2 (wait for regulation)</td>
</tr>
<tr>
<td>**Paraguay**</td>
<td>SEPRELAD · SIRO</td>
<td>Sector-specific ⚠️</td>
<td>Cash ≥ USD 10k ⚠️</td>
<td>SIRO web, incl. **negative STR**</td>
<td>10% ⚠️</td>
<td>Partial: crypto providers (incl. miners) obligated</td>
<td>P3</td>
</tr>
<tr>
<td>**El Salvador**</td>
<td>UIF (FGR)</td>
<td>**24 h** (DL 426, in force Oct 2025)</td>
<td>Cash \> USD 10k; other/multiple \> USD 25k/month; within 5 business days</td>
<td>UIF portal ⚠️</td>
<td>25%</td>
<td>**Yes**: digital-asset regulator (CNAD)</td>
<td>P3</td>
</tr>
<tr>
<td>**Honduras**</td>
<td>UIF (CNBS)</td>
<td>⚠️</td>
<td>Cash ≥ USD 10k; monthly reports</td>
<td>"Capturador UIF" desktop tool file</td>
<td>⚠️</td>
<td>No</td>
<td>P4</td>
</tr>
<tr>
<td>**Bolivia**</td>
<td>UIF</td>
<td>⚠️</td>
<td>≥ USD 10k ⚠️</td>
<td>⚠️</td>
<td>⚠️</td>
<td>Partial</td>
<td>P4: FATF grey list + EU high-risk list (Jan 2026)</td>
</tr>
<tr>
<td>**Nicaragua**</td>
<td>UAF</td>
<td>Immediate, max 30 days from alert</td>
<td>Cash report + **monthly zero report (RAII)**, first 10 days</td>
<td>UAF formats</td>
<td>⚠️</td>
<td>Partial ⚠️</td>
<td>P5: sanctions/political risk</td>
</tr>
<tr>
<td>**Venezuela**</td>
<td>UNIF</td>
<td>—</td>
<td>—</td>
<td>—</td>
<td>—</td>
<td>SUNACRIP</td>
<td>**Do not serve** (FATF grey list, EU high-risk list, US OFAC government sanctions). Offer **screening only** for clients elsewhere onboarding Venezuelan nationals (OFAC SDN + 50%-ownership rule + EU)</td>
</tr>
</table>
**Product implications:**
- **goAML XML serializer (P1 engineering investment).** goAML is used by the Dominican Republic and many FIUs worldwide; it's the most reusable report format in scope.
- **USD-denominated markets** (Ecuador, Panama, El Salvador) make thresholds simpler. Most of the region uses a **USD 10,000 cash threshold**.
- **Zero/negative reports** also exist in Ecuador (NO-ROS), Paraguay (negative STR) and Nicaragua (RAII). The zero-report engine (RR-5) must cover them.
- **Several regimes are in transition** (Guatemala, Costa Rica, Uruguay, El Salvador). Ship country packs only once implementing regulations and formats are published.
- **Re-check after the October 2026 FATF plenary**, whose results are not yet included.
---
## 14. Roadmap
<table header-row="true">
<tr>
<td>Phase</td>
<td>Scope</td>
</tr>
<tr>
<td>**1 — MX launch**</td>
<td>Foundations (§1), MX onboarding (RENAPO/INE/SAT, tiers, geolocation), KYB with beneficial owners at 25%, screening incl. LPB upload + freeze workflow, risk + annual review, threshold engine (UDI/UMA/FIX), case management with committee workflow, **reports: Formato 41 (ROR/ROI/24 h/ROIP) incl. empty reports, international-transfer and FX-cash reports, non-financial business XML avisos (current + 2027 XSD) incl. monthly zero reports**, obligations calendar</td>
</tr>
<tr>
<td>**2 — BR**</td>
<td>Receita/Datavalid, Siscoaf **SOAP direct filing** (COS, COE incl. Res. 591 items, CNO), UN freeze workflow (BC Correio/Ministry of Justice), effectiveness-report pack, Pix MED 2.0 hooks, crypto-provider module</td>
</tr>
<tr>
<td>**3 — CO + AR**</td>
<td>CO SIREL fixed-width/pipe generators + absence reports, Supersociedades 2026 regime (UVB, quarterly AROS); AR SRO Masivo XML, SMVM snapshot engine, RePET feed, self-freeze + 24 h RFT, risk self-assessment/external-reviewer support</td>
</tr>
<tr>
<td>**4 — CL + PE**</td>
<td>CL ROE generators + **automated channel**, MiUAF migration; PE templates (ROSEL/RO/IAOC), OFAC + EU mandatory screening, travel rule</td>
</tr>
<tr>
<td>**5 — Rest of LATAM**</td>
<td>**5a:** Ecuador, Uruguay, Panama (+ goAML engine → Dominican Republic). **5b:** Costa Rica, Guatemala (after regulations publish). **5c:** Paraguay, El Salvador. Later: Honduras, Bolivia, Nicaragua. Venezuela: screening only</td>
</tr>
</table>
---
## 15. Gaps to close before building each generator
Many official templates are **only available inside authenticated regulator portals**. A **design-partner fintech per country** whose compliance officer can download them, or share masked examples of filings and receipts, is the fastest way to close these gaps.
<table header-row="true">
<tr>
<td>Country</td>
<td>Missing artefact</td>
</tr>
<tr>
<td>MX</td>
<td>New 2027 per-activity XSDs (`avi2.xsd` etc.: SAT link returned an error on 2026-10-17); UIF catalogs (alert types, transfer networks); layouts for USD-cash / international-transfer / fintech crypto / money-transmission reports; SITI authentication and any API; consolidated bank rules (USD 7,500 threshold)</td>
</tr>
<tr>
<td>BR</td>
<td>Siscoaf batch XML layout + occurrence-code tables (behind login), incl. new codes for Res. 591; SUSEP 612 details; DNFBP sector specifics</td>
</tr>
<tr>
<td>CO</td>
<td>Content of crypto-provider resolutions UIAF 314/2021 and 84/2022 (scanned images, not readable); SFC KYC update frequency; confirm PEP 2-year rule</td>
</tr>
<tr>
<td>AR</td>
<td>Per-sector bulk-report XSDs (cash, FX cash, international transfers, crypto providers, payment providers); RFP (Res. 03/2026) body; crypto-provider travel-rule modality; BCRA parallel reporting regime</td>
</tr>
<tr>
<td>CL</td>
<td>Cash-report Excel/TXT layouts, bank CSV manual, automated-channel technical spec (request from UAF); CMF rules for Ley 21.521 providers</td>
</tr>
<tr>
<td>PE</td>
<td>ROSEL / RO / IAOC templates (Portal PLAFT); financial RO layout and channel (SUCAVE); zero-report rule; post-2020 amendments to Res. 2660-2015</td>
</tr>
</table>
