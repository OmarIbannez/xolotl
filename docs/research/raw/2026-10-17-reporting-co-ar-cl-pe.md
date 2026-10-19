# AML/CFT Reporting Research — Colombia, Argentina, Chile, Peru (2026-10-17)

_Status: COMPLETE (first pass) — researched 2026-10-17_

Confidence legend: **VERIFIED** = primary source read (page/PDF opened); **SECONDARY** = from secondary source or search snippet of primary source not opened; **UNVERIFIED** = recollection / inference, needs confirmation.

## Executive summary & corrections to the brief

**Corrections to "already known":**
1. **CO – Supersociedades:** CE 100-000016/2020, 100-000004/2021, 100-000015/2021, 100-300000/2024 etc. were **repealed by CE 100-000020 of 2-Jul-2026** (new integral Circular Básica Jurídica). SAGRILAFT + PTEE are merged into one "Sistema de Autocontrol y Gestión de Riesgos LA/FT/FP y C/ST" (Ch. IX); thresholds now in **UVB**; **AROS is quarterly** (10 calendar days after quarter); existing obligated companies must adapt by **31-May-2027**. (VERIFIED)
2. **CO – retention:** SFC entities = **5 years** (art. 96 EOSF) — not 10; 10 years applies to Supersociedades/others (art. 28 Ley 962/2005). (VERIFIED)
3. **CO – SFC annexes** (ROS, efectivo, exonerados, cambiarias, tarjetas, productos, APP, campañas) are still the Jul-2019 versions (CE 018/2019); cash thresholds **COP 10M individual / COP 50M monthly multiple**; all monthly reports due within **20 calendar days**; ROS absence report is **monthly** for SFC & PSAV. New: **UIAF Res. 010/2025** (5 business days for UIAF information requests). (VERIFIED)
4. **AR:** sector resolutions confirmed (14/2023 financial; 99/2023 mutuales; 126/2023 seguros; 42/2024 contadores; 43/2024 inmobiliarios; **54/2024 = antigüedades, 55/2024 = joyas**; **49/2024 = full PSAV regime**, travel rule is art. 37 of it). New since: **200/2024 (PSP & non-financial credit)**, **199/2024**, **78/2025 (thresholds ×2: RTE 40 SMVM)**, **207/2025 (RFT & freezing)**, **03/2026 (RFP)**, 37/2026, 93/2026, 109/2026. ROS-LA deadline = **24 h after concluding suspicion, max 90 days from operation**; RFT/RFP = **24 h from operation**. (VERIFIED)
5. **CL:** Circulars 49, 57, 59, 60 etc. were **repealed 1-Jun-2025 by Circular N°62** (consolidated rulebook). **ROE threshold is now USD 10,000 (not 450 UF)**; ROE periodicity monthly (banks) / quarterly (non-bank financial & fintech) / semiannual (non-financial), due in first 10 business days; **ROE negativo** mandatory. New **Oficio Circular 543 (25-Aug-2026)**: Clave Única from **19-Oct-2026** and a **separate authenticated channel for automated ROE/ROS submission**; **MiUAF** replaces the portal in 2027. (VERIFIED)
6. **PE:** The Aug-2026 travel-rule date belongs to **Res. SBS 02648-2024 (PSAV norm)**, chapter VIII effective **1-Aug-2026**; "Res. SBS 000132-2024" could not be found. RO thresholds are more granular than "US$10k/50k": financial RO single ops ≥ US$10,000 (cash transfers ≥ US$2,500; cash FX ≥ US$5,000; etc.), multiple ops (> US$1,000 each) ≥ US$50,000/month kept at SBS disposal. **PEP window is 5 years** in Peru (vs 2 y CO/AR, ≥1 y CL). Peru explicitly requires **OFAC and EU** list screening for UIF-supervised SO. (VERIFIED)

**Cross-country design implications for report generators**
| Country | ROS channel | Bulk/machine channel? | Periodic report file formats | Zero/negative reports |
|---|---|---|---|---|
| CO | UIAF **SIREL** web form + attachments | **No API**; periodic files uploaded manually in SIREL | **Fixed-width TXT (CP1252, header/body/trailer)** for SFC annexes; **pipe-delimited TXT** for PSAV | Monthly "ausencia" for SFC & PSAV (ROS and each periodic report); quarterly **AROS** for Supersociedades |
| AR | UIF **SRO+** web form (ROS/RFT/RFP) | **SRO Masivo** desktop app: one XML per operation validated vs per-SO **XSD** | XML (RSM masivo) or web templates; RSA web form | Monthly "período sin movimientos" for RSM |
| CL | UAF **Portal de Entidades Reportantes** (→ MiUAF 2027) | **Yes (new)**: differentiated automated access for ROE/ROS from 19-Oct-2026 (IP-whitelisted; spec on request) | ROE: banks TXT "datos genéricos"; non-banks Excel (2 or 5 sheets); ROE Simplificado web form; bank ROS CSV attachments validated | **ROE negativo** each period |
| PE | **ROSEL** with Plantilla ROSEL | No public API; templates in Portal PLAFT; financial RO via SBS electronic channel (SUCAVE) | Plantillas (RO, IAOC, ROSEL) from Portal PLAFT (login) | Not found |

Units/conversions the engine must support: COP & **UVB** (CO), **SMVM** with 31-Dec/30-Jun snapshot (AR), **USD at dólar observado** + UF (CL), **USD at SBS monthly-average selling rate of the previous month** (PE financial RO) / TRM (CO).

## Method notes

- Primary PDFs were downloaded with curl from official domains (uiaf.gov.co, etc.) and text-extracted locally (pypdf); scanned PDFs were rendered to images and read visually. The `agent-browser` cloud IP is WAF-blocked by several LatAm government sites (uiaf.gov.co, normativa.colpensiones), so direct curl from the workstation was used instead.
- "VERIFIED" below means I read the relevant passage of the primary document myself in this session.
- Monetary units: CO = COP / SMMLV / UVB (Unidad de Valor Básico, replaces SMMLV/UVT for many regulatory thresholds since Ley 2294/2023 art. 313); AR = ARS / SMVM (Salario Mínimo Vital y Móvil); CL = UF / USD; PE = USD / PEN / UIT.

---

# 1. COLOMBIA

## 1.0 Regulatory map (who supervises, which AML regime, which UIAF sector code)

| Supervisor | Obligated entities (examples) | AML system | Main instrument | Reports to UIAF via |
|---|---|---|---|---|
| Superintendencia Financiera (SFC) | Banks, compañías de financiamiento, cooperativas financieras, **SEDPE** (e-money), fiduciarias, comisionistas de bolsa, SAI, aseguradoras, AFP, casas de cambio/IMC, sociedades de intermediación cambiaria y servicios financieros especiales (SICSFE), crowdfunding societies, sociedades titularizadoras | **SARLAFT** | Circular Básica Jurídica (CBJ, CE 029/2014) Parte I, Título IV, Capítulo IV — rewritten by CE 027/2020 ("SARLAFT 4.0"), amended by CE 011/2022 | SIREL; annexes 1–8 (CE 017/2018 → CE 018/2019 versions, last updated Jul-2019) |
| Superintendencia de Sociedades (Supersociedades) | Real-sector companies above revenue/asset thresholds; DNFBP-like sectors (real estate, precious metals, legal, accounting, construction, vehicles), **VASPs (PSAV)**, chambers of commerce, foreign ESAL | **SAGRILAFT** (full) or **Régimen de Medidas Mínimas (RMM)**; combined with **PTEE** (anti-bribery) | **NEW: CE 100-000020 of 2-Jul-2026** — new integral Circular Básica Jurídica; Ch. IX unifies old Ch. X (SAGRILAFT) + Ch. XIII (PTEE) | SIREL (ROS, AROS) + sector annexes (PSAV Res. UIAF 314/2021) |
| Superintendencia de la Economía Solidaria (Supersolidaria) | Savings & credit cooperatives (cooperativas de ahorro y crédito, multiactivas con sección de ahorro), fondos de empleados, mutuales | **SARLAFT** (solidario) | CBJ Supersolidaria CE 20/2020 Título V Cap. I, mod. CE 32/2021; CE 72/2024 and **CE 95/2026** (freeze of Anexo 2 threshold schedule) | SIREL; annexes ROS, TRSO, PMSO, TASO |
| MinTIC | Operadores postales de pago (giros) | SARLAFT (Res. MinTIC 1292/2021, mod. 00003/2022, 2296/2022) | Res. UIAF / MinTIC 2679/2016 | SIREL; giros nacionales and internacionales annexes |
| Coljuegos / CNJSA | Gambling operators | SIPLAFT | Coljuegos acuerdos | SIREL |
| Supersalud, Supertransporte, Supervigilancia, SNR (notaries), DIAN (customs/FX professionals, *profesionales de compra y venta de divisas*), MinDeporte, MinCultura, CNE (campaigns) | Various DNFBP/real-sector | SARLAFT/SIPLAFT variants | Each supervisor's circular | SIREL |

Source: UIAF sector list https://www.uiaf.gov.co/reportantes/entidades (VERIFIED, page fetched 2026-10-17).

## 1.1 Report catalog — Colombia

Common to all UIAF reports: filed through **SIREL (Sistema de Reporte en Línea)** at https://www.uiaf.gov.co; entity first requests a *código de entidad* (format `SSTTTCCC`: SS sector, TTT entity type, CCC entity code) and then a named user tied to the compliance officer's ID; certificate of receipt (PDF) with radicación number, EXITOSO/FALLIDO status. No digital signature file is attached — authentication is SIREL username/password tied to the person (VERIFIED, Anexos 1–2 SFC).

### 1.1.a Financial sector supervised by SFC (banks, SEDPE/e-money, fiduciarias, brokers, insurers, IMC/FX, crowdfunding)

| # | Native report | Sector(s) | Trigger / threshold | Deadline / periodicity | Channel | Format | Official layout | Auth | Zero report? | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|
| CO-F1 | **ROS – Reporte de Operaciones Sospechosas** (incl. intentadas, rechazadas, tentativas de vinculación) — Anexo 1 | All SFC-supervised (incl. those exempt from rest of SARLAFT) + **revisor fiscal** (art. 207 num. 10 C.Co.) | Suspicion; no amount threshold; no need to identify predicate offence | **"Inmediato"** = from the moment the entity decides the operation is suspicious | SIREL – web form ("Reportes de Operación Sospechosa / ROS / +Crear / Reporte Positivo") | **Web form** with structured fields (operation value, currency, period start/end, narrative, persons involved) + attachments (each ≤ **20 MB**) | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%201.pdf | SIREL user | **Yes – "reporte de ausencia" (negativo) monthly**, within first **20 calendar days** of following month, only if no ROS filed that month | VERIFIED |
| CO-F2 | **Reporte de Transacciones en Efectivo** (individuales y múltiples) — Anexo 2 | All SFC-supervised handling cash | Individual cash txn **≥ COP 10,000,000**; multiple (same client/user, one calendar month, one or more offices) **≥ COP 50,000,000** (FX converted at TRM of txn day). Exempt: tax collection, valorización, health/pension contributions, utilities, mobile phone, ARL (latter 4 also exempt from individual). Include ATM/corresponsal/credit-card cash advances of own clients. Network-use contracts (corresponsales): both network user and service provider report | **Monthly**, first **20 calendar days** after month-end; failed upload may be corrected up to **10 calendar days** after deadline and still count as on time | SIREL file upload | **TXT fixed-width, 645 positions/record**, ANSI **CP1252**, header (type 1) / body (type 2) / trailer (type 3), UPPERCASE | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%202.pdf + "Cuadros complementarios del Anexo 2" | SIREL user | **Yes – monthly ausencia** | VERIFIED |
| CO-F3 | **Reporte de Clientes Exonerados** del reporte de efectivo — Anexo 3 | SFC-supervised that exempt clients | All exempted clients | **Quarterly**, first 20 calendar days of month after quarter-end | SIREL | TXT fixed-width **299 positions**, CP1252; file name `CLEXSSTTTCCCMMAA` | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%203.pdf | SIREL | Yes (ausencia) | VERIFIED |
| CO-F4 | **Reporte de Operaciones Cambiarias** (transferencias, remesas, compra/venta de divisas por ventanilla) — Anexo 4 | Intermediarios del mercado cambiario (banks, CF, casas de cambio, SICSFE…) | **All** international transfers (incl. monetizations), physical remittances, and window FX buy/sell (cash/cheque); excludes FX derivatives, ops with BanRep or other SFC entities. **No amount threshold** | Monthly, first 20 calendar days | SIREL | TXT fixed-width **1,760 positions**; name `OPCASSTTTCCCMMAA` | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%204.pdf | SIREL | Yes | VERIFIED |
| CO-F5 | **Reporte de Transacciones con Tarjetas Internacionales** — Anexo 5 | Entities administering/representing card franchises (Visa, MC, Amex, Diners, Credencial) | Cleared ops with **foreign-issued** credit/debit/prepaid cards at ATMs or low-value payment systems, individually or summed in month **≥ COP 10,000,000**; successful txns only | Monthly, first 20 calendar days | SIREL | TXT fixed-width **176 positions**; name `TATASSTTTCCCMMAA` | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%205.pdf | SIREL | Yes | VERIFIED |
| CO-F6 | **Reporte de Productos Ofrecidos** — Anexo 6 (4 sub-reports: Productos General `PRGE…_1/_2`, Fondos `REFO…_1/_2`, Negocios Fiduciarios `NEFI…_1/_2`, Industria Aseguradora `INAS…_1/_2`) | All SFC-supervised (except products held with other SFC entities / BanRep) | **All** active/passive products: vigentes (active & inactive) every month even if unchanged; saldados/cancelados only in the month they changed state | Monthly, first 20 calendar days | SIREL | TXT fixed-width (e.g. 108 positions for one file), CP1252; two files per sub-report (products + holders) | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%206.pdf | SIREL | Yes | VERIFIED |
| CO-F7 | **Reporte de Patrimonios Autónomos (Ley 1508/2012, APP)** — Anexo 7 | Fiduciarias | Constitution of PA under APP projects | Within **3 business days** after constitution (+monthly ausencia) | SIREL | TXT fixed-width 597 positions; `PAAUSSTTTCCCMMAA_1/_2` | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%207.pdf | SIREL | Yes | VERIFIED |
| CO-F8 | **Reporte de Campañas y Partidos Políticos** — Anexo 8 | Entities managing products of political campaigns/parties/groups | All products of campaigns/parties | Monthly, first 20 calendar days | SIREL | TXT fixed-width 1,396 positions; `PAPOSSTTTCCCMMAA_1/_2` | https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%208.pdf | SIREL | Yes | VERIFIED |
| CO-F9 | **Respuesta a requerimientos de información adicional UIAF** | All SFC-supervised | Ad-hoc UIAF request | **5 business days**; one written extension, max equal term | Per request | Per request | Res. UIAF 010 of 13-Jan-2025: https://www.uiaf.gov.co/sites/default/files/2025-02/Resoluci%C3%B3n%20No.%20010%20de%202025%20de%2013%20de%20enero%20de%202025%20de%20la%20UIAF.pdf | — | n/a | VERIFIED (scanned PDF read visually) |
| CO-F10 | Report to UIAF + Fiscalía on **listed-person assets** (binding UN/terrorist lists) | All | Match against Listas Vinculantes (art. 20 Ley 1121/2006) | Immediately | ROS via SIREL + communication to Fiscalía | — | CBJ SFC / Ley 1121 | — | — | SECONDARY (SFC text references; Supersociedades text VERIFIED) |

Annex versioning: all eight SFC annexes show "Última actualización: julio de 2019" (CE 018/2019, which repealed CE 017/2018) and are still the ones linked on the UIAF SFC page as of Oct-2026 (VERIFIED: https://www.uiaf.gov.co/sector/sfc). Excel "Genera Plano … CE018" utilities (max 65,524 rows) are published for each annex.

### 1.1.b SEDPE / fintech / payments specifics
- **SEDPE** (Sociedades Especializadas en Depósitos y Pagos Electrónicos, Ley 1735/2014) are SFC-supervised → full SARLAFT + annexes 1, 2, 6 (and 3 if exonerating clients). Payment gateways/aggregators that are not SFC-licensed are not UIAF reporting entities under SFC annexes (they may fall under Supersociedades SAGRILAFT if above thresholds). (SECONDARY — inferred from scope of CBJ; confirm per license.)
- **Crowdfunding** (sociedades de financiación colaborativa, Decreto 1357/2018) — SFC-supervised → SARLAFT. (UNVERIFIED as to specific annex scope.)
- **Open finance** (CE 004/2024 SFC; deadlines extended 2026) — not AML reporting but relevant to data. (SECONDARY: hklaw.com Feb-2026.)

### 1.1.c VASPs / PSAV (Res. UIAF 314 of Dec-2021, mod. Res. 84/2022)

Scope (from UIAF 2026 calendar): natural or legal persons that, for or on behalf of another person, perform **regardless of amount**: fiat↔VA exchange, VA↔VA exchange, VA transfer, custody/administration, participation in issuer offer/sale, and in general VA services. Supervised (SAGRILAFT) by Supersociedades. (VERIFIED: https://www.uiaf.gov.co/sites/default/files/2025-12/Calendario%202026%20-%20ActivosVirtuales_314.pdf)

| # | Report | Trigger | Periodicity | Format | Layout URL | Zero report | Conf. |
|---|---|---|---|---|---|---|---|
| CO-V1 | ROS PSAV (Anexo 1) | Suspicion, attempted/rejected included | Immediate; SIREL web form | Web form | https://www.uiaf.gov.co/sites/default/files/2024-10/Anexo_1_ROS_PSAV.pdf (Dec-2021) | **Monthly ausencia** within 20 days of following month (annex wording "mes subsiguiente") | VERIFIED |
| CO-V2 | **Reporte de Clientes PSAV** (Anexo 2) | All clients (active, inactive, de-linked) trading on platform in month; one row per active product | Monthly, first 20 calendar days | **TXT pipe-delimited (pipe char)**, CP1252, header/body/trailer; name `CPSVSSTTTCCCMMAA`; wallet fields case-sensitive | https://www.uiaf.gov.co/sites/default/files/2024-10/20220610_Anexo2_Reporte_Clientes.pdf (Jun-2022) | Yes | VERIFIED |
| CO-V3 | **Reporte de Transacciones PSAV** (Anexo 3) | Multiple txns same client/month **≥ USD 450** (report each underlying txn) + individual txns **≥ USD 150** (USD at TRM of day) | Monthly, first 20 calendar days | **TXT pipe-delimited**, CP1252; name `TPSVSSTTTCCCMMAA`; 26+ columns incl. IP address, wallet, crypto symbol (CoinMarketCap ticker), quantity, USD value, COP value, balances, counterparty wallet/ID/name/nationality, counterparty VASP and country, tx hash | https://www.uiaf.gov.co/sites/default/files/2024-10/Anexo%203%20-%20Reporte%20de%20transacciones..pdf (Jun-2022) | **Yes – ausencia de transacciones** monthly | VERIFIED |

Note: Res. 314/2021 and 84/2022 PDFs are scanned images (text not extracted); content above taken from the annexes and the official 2026 calendar.

### 1.1.d Real sector / DNFBPs under Supersociedades (SAGRILAFT / RMM) — NEW CBJ CE 100-000020 of 2-Jul-2026

| # | Report | Who | Trigger | Deadline | Channel / format | Zero report | Conf. |
|---|---|---|---|---|---|---|---|
| CO-S1 | **ROS** | SAGRILAFT and RMM obligated subjects (OC registers in SIREL; under RMM the **representante legal** is the registered SIREL user) + revisor fiscal | Suspicion | Immediate | SIREL web form per "Manual de Usuario SIREL" and Supersociedades ROS annex (https://www.uiaf.gov.co/sites/default/files/2024-10/2021_Anexo1_Reporte_Operaciones_Sospechosas.pdf) | — | VERIFIED |
| CO-S2 | **AROS – Ausencia de ROS** | Same | No ROS filed during a **calendar quarter** | Within **10 calendar days** after quarter-end | SIREL | This *is* the zero report | VERIFIED (CBJ 2026 §9.19, §9.21.4) |
| CO-S3 | Report to UIAF **and Fiscalía General** of assets of persons on Listas Vinculantes | All | Match | Immediately | SIREL + Fiscalía | — | VERIFIED (§9.15, §9.21.4) |
| CO-S4 | **Reporte de presunta conducta de soborno transnacional** to Supersociedades | Revisor fiscal and OC | Knowledge of facts | n/s (prompt) | Supersociedades portal → Servicios Digitales | — | VERIFIED (§9.18.2) |
| CO-S5 | **Informe no financiero — designación/desvinculación del Oficial de Cumplimiento** (principal y suplente) | SAGRILAFT obligated | Appointment; removal/resignation | **15 business days** after board meeting appointing; outgoing OC files before leaving; new OC within **30 calendar days** | Online "Informes Empresariales" (SIRFIN) on supersociedades.gov.co, with CV, certification of requirements, SIREL registration proof, board-minute extract, education proof, no-incompatibility certificate | — | VERIFIED (§9.9.4) |
| CO-S6 | Sector reports, e.g. **vehicle dealers**: compra/venta de vehículos (Res. UIAF 101/2013, Anexo 2 concesionarios) | Car dealers | Per annex | Per UIAF calendar | SIREL TXT | Yes | SECONDARY (UIAF page lists annex; not read) |

Scope thresholds (CBJ 2026, VERIFIED §9.4–9.6): general SAGRILAFT if total revenue or assets ≥ **4,929,017 UVB** at 31-Dec prior year; sector SAGRILAFT (real estate CIIU 6810/6820, precious metals 4662/3211/2421/0722, legal 6910, accounting 6920, construction 4111/4112/4210/4220/4290, vehicles 4511/4512/4541) ≥ **3,696,762 UVB**; VA contributions or VA services ≥ **12,323 UVB** (and revenue ≥ 369,676 UVB or assets ≥ 616,127 UVB for service providers) → must apply **enhanced DD** to VA counterparties. RMM covers smaller entities in listed sectors (also pharma, infrastructure, manufacturing, mining-energy). Min. permanence after falling below threshold: 2 years (SAGRILAFT), 1 year (RMM). **Transition: existing obligated subjects must adapt by 31-May-2027**; new obligated subjects by 31 May of the year following qualification.

### 1.1.e Cooperatives under Supersolidaria
| # | Report | Notes | Conf. |
|---|---|---|---|
| CO-C1 | ROS (Anexo 1) — SIREL | immediate + ausencia | SECONDARY (annex listed on UIAF page, not read) |
| CO-C2 | **TRSO – Reporte de transacciones de clientes** (Anexo 2) | Thresholds: individual **≥ COP 5,000,000**, multiple (month) **≥ COP 30,000,000**. CE 20/2020 had a step-down schedule 2023→2025 (2025 target: COP 2M / COP 20M); **CE 72 of 23-Dec-2024** suspended the step-down for 2025 and **CE 95/2026** extended the suspension *until a new circular is issued* (pending a sector risk study) | VERIFIED (https://www.uiaf.gov.co/sites/default/files/2024-12/Circular_Externa_72_de_2024.pdf ; https://www.uiaf.gov.co/sites/default/files/2026-06/CIRCULAR%20EXTERNA%2095%20DE%202026_0.pdf) |
| CO-C3 | **PMSO – Reporte de productos mensual** (Anexo 3) | monthly | SECONDARY |
| CO-C4 | **TASO – Reporte de tarjetas mensual** (Anexo 4) | monthly | SECONDARY |
Layouts: https://www.uiaf.gov.co/sector/ses

### 1.1.f Postal payment operators (MinTIC)
ROS + giros postales nacionales (Anexo técnico Aug-2012) and internacionales (Anexo II B) — https://www.uiaf.gov.co/sector/mintic (SECONDARY).

## 1.2 Format details — Colombia key reports

### 1.2.1 ROS (all sectors) — SIREL web form
- Source: SFC Anexo 1 (Jul-2019) https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%201.pdf ; PSAV Anexo 1 (Dec-2021); Supersociedades ROS annex (2021). VERIFIED.
- **Channel:** SIREL only ("El único medio de envío del ROS es SIREL"). Interactive form; mandatory fields enforced by the form. **No bulk/XML/API channel published** for ROS → platform must produce a pre-filled case package (structured data + narrative + attachments) for a human to key/paste into SIREL, or use browser automation (risk: ToS). 
- **Structured blocks:** (1) operation info — value (estimate or 0 if attempted/no value), currency, analysis period start/end date; (2) narrative ("descripción de la operación": first paragraph answers who/what/why/when/where; then chronology, financial profile vs. segment, other products, unexplained wealth growth, identity misuse, fake docs, front companies, links to criminals, cash structuring, alert/typology triggered, detection procedure, related prior ROS, press links); (3) persons/entities involved — ID type, ID number (use `-1` if unknown), names; (4) attachments ("Adjunte aquí los anexos"), each ≤ 20 MB (statements, KYC forms, FX declarations, contracts, tax returns, chamber of commerce certificate…).
- **Reporting date** = date of SIREL submission (certificate), not the analysis period.
- **Tipping-off**: art. 11 Ley 526/1999; safe harbour art. 42 Ley 190/1995.
- **Retention** of ROS support: SFC ≥ 5 years (art. 96 EOSF); Supersociedades: art. 28 Ley 962/2005 (10 years).
- **Zero report**: SFC & PSAV monthly "reporte de ausencia" (first 20 days); Supersociedades **quarterly AROS** (10 calendar days after quarter).

### 1.2.2 Reporte de Transacciones en Efectivo (SFC Anexo 2) — fixed-width TXT
- File name: `TREFSSTTTCCCMMAA.txt` (TREF + entity code + month + 2-digit year). Encoding **ANSI CP1252**; allowed alphanumeric chars: space, A–Z, ANSI 140/142, 192–214, 216–221 and numeric chars; **UPPERCASE**; no dots/dashes inside alphanumerics; numeric right-aligned, alpha left-aligned; record length **645**; all fields mandatory (use `-1` for unknown where allowed).
- **Header (type 1):** pos 1–10 consecutive = `0` (right-aligned); 11–18 entity code `SSTTTCCC`; 19–28 cut-off date `AAAA-MM-DD` (last day of month); 29–38 total type-2 records; 39–645 filled with `X`.
- **Body (type 2):** 1–10 consecutive (from 1); 11–20 txn date; 21–40 value in COP, no decimals (FX converted at TRM of day); 41–43 currency ISO 4217; 44–58 office code; 59–60 product type (Cuadro 1); 61 txn type (Cuadro 2); 62 medium (Cuadro 3, e.g. pure cash vs. cheque at window); 63–82 product number (must match product ID in Anexo 6); 83–84 holder ID type (Cuadro 4); 85–104 holder ID; 105–106 holder NIT check digit (`0d` or `-1`); 107–361 holder name (255); 362–366 DANE municipality code (Bogotá 11001); 367–368 ID type of person performing txn (Cuadro 5); 369–388 ID; 389–390 check digit; 391–645 full name. Either holder (cols 10–13) **or** performer (15–18) may be `-1`, not both. Real-estate trusts: report buyer, not builder.
- **Trailer (type 3):** 1–10 `0`; 11–18 entity code; 19–28 total records; 29–645 `X`.
- **Validation:** SIREL rejects whole file if any field is shifted by one position; result EXITOSO/FALLIDO with error list; one-time 10-calendar-day correction window after deadline counts as on time.
- **Corrections after successful load:** fill "formato de solicitud de anulación para corrección de reportes" → submit through UIAF PQRSD ("Peticiones > Soporte > Solicitud Anulación para corrección de Reportes") → UIAF marks report FALLIDO → re-upload full corrected file (status "ENVÍO CORREGIDO").
- Code tables: "Cuadros complementarios del Anexo 2" (PDF) https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%202%20Cuadros%20complementarios.pdf ; DANE codes and ISO tables under SIREL > Formatos y Tablas Generales. VERIFIED.


#### Code tables — SFC Anexo 2 "Cuadros complementarios" (Jul-2019) — VERIFIED
Source: https://www.uiaf.gov.co/sites/default/files/inline-images/Anexo%202%20Cuadros%20complementarios.pdf
- **Cuadro 1 Tipo de producto (2 digits):** 01 Cuenta de ahorros; 02 Cuenta corriente; 03 **Depósito electrónico** (e-money/SEDPE); 04 CDT; 05 CDAT; 06 CDM; 07 Otras captaciones; 08 Crédito de consumo; 09 Comercial; 10 Vivienda; 11 Microcrédito; 12 Tarjeta de crédito; 13 **Tarjeta prepago**; 14 Leasing; 15 Factoring; 16 Descuento; 17 Otras colocaciones; 18 Aval/garantía; 19 Aceptación; 20 Remate/subasta; 21 Carta de crédito importación; 22 exportación; 23 APT; 24 Administración de valores; 25/26 Derivados compra/venta; 27/28 Contado compra/venta; 29–36 FIC (general/monetario/inmobiliario/bursátil, abierto/cerrado); 37 Cuenta ómnibus; 38 FCP; 39 Fondos mutuos; 40 Fondo capital extranjero; 41 Pensiones voluntarias; 42 Cesantías no dependiente; 43 Otro fondo; 44–54 Fiducia (inversión, inmobiliaria, administración, garantía subtypes); 55 Pasivos pensionales; 56 Otros recursos seguridad social; 57–65 Seguros con ahorro (vida, pensiones voluntarias, rentas, educativo, AP, colectivo, grupo, BEPS, otro); 66 Título de capitalización.
- **Cuadro 2 Tipo de transacción (1 digit):** 1 Retiro (cash delivered by entity); 2 Depósito (cash received).
- **Cuadro 3 Medio:** 0 Efectivo; 1 Pago de cheque en ventanilla.
- **Cuadro 4 Tipo de identificación (2 digits):** 00 Otro (carné diplomático, PEP-permiso especial de permanencia…); 03 Código único SFC (negocio fiduciario / fondo); 07 Sociedad extranjera sin NIT; 11 Registro civil; 12 Tarjeta de identidad; 13 Cédula de ciudadanía; 21 Tarjeta de extranjería; 22 Cédula de extranjería; 31 NIT; 41 Pasaporte; 42 Documento de identificación extranjero.

### 1.2.3 PSAV transactions/clients — pipe-delimited TXT
- Same header/body/trailer concept but **pipe `|` separated** (not fixed width), CP1252; header/trailer are described positionally in the Nov-2021 annex (pos 1–10 consecutive `0`, 11–18 entity code, 19–28 cut-off date, 29–38 record count, filler `X` to pos 39) while body fields are pipe-separated — confirm exact header serialization against the Jun-2022 annex / Excel utility before coding (UNVERIFIED detail). File names `TPSV…`, `CPSV…`. Wallet/hash fields must preserve case. VERIFIED.

## 1.3 KYC minimums — Colombia

| Topic | SFC (SARLAFT) | Supersociedades (SAGRILAFT/RMM, CBJ 2026) | Conf. |
|---|---|---|---|
| Timing | No contractual relationship until info collected, identity verified and onboarding approved; legal entities need updated certificate of existence & representation | DD before the business; exceptionally after, documented | VERIFIED (CE 027/2020 text; CBJ 2026 §9.15) |
| Remote onboarding | Expressly allowed: "presencial o no presencial a través de canales digitales o electrónicos"; may verify against public DBs, *servicios ciudadanos digitales*, own or external DBs (with risk analysis of the external source, Ley 1581/2012 compliance) | Not restricted; reliable independent sources | VERIFIED |
| Simplified DD | Listed products only (e.g. low-amount FIC participations, FPV contributions, brokerage ≤ **66,000 UVR**, plus other listed simplified products such as low-amount deposits/e-money — list in §4.2.2.2.1.4): minimum = ID type, name, number, issue date; remaining info within **3 business days**; full KYC before any other product | RBA; simplified allowed in low risk if no suspicion | VERIFIED (FIC/FPV/SCBV 66,000 UVR); list of other products SECONDARY |
| Enhanced DD | Mandatory when risk is high, PEPs, FATF high-risk countries, correspondent banking (senior approval, no shell banks) | High risk, PEPs (extends to spouse, relatives to 2nd degree consanguinity / 1st affinity / 1st civil, foreign accounts, close associates), FATF lists; **all VA counterparties**; senior management approval, source of funds, enhanced monitoring | VERIFIED |
| UBO | Art. **631-5 Estatuto Tributario** (adopted by SFC CE 011/2022): natural person with **≥ 5%** capital/votes or benefits ≥ 5% of assets/returns/profits, or ultimate control; fallback senior managing official. Registry: **RUB (Registro Único de Beneficiarios Finales)** at DIAN, Res. DIAN 000164/2021 (mod. 037/2022) | "Beneficiario Final" = ET definition; must identify ownership & control chain | SECONDARY (DIAN Concepto 54/2023 via search; CBJ 2026 VERIFIED for reference to ET) |
| PEP | Decreto 1081/2015 as amended by **Decreto 830/2021** (national PEP list of positions; foreign PEP; PEP of international orgs). PEP status lasts during office **+ 2 years** after leaving | Same definition (Decreto 1081/2015), plus close associates (CE 100-000015/2021) | SECONDARY (2-year rule from memory of Decreto 830/2021 art. 2.1.4.2.4 — confirm) |
| Update frequency | SFC: risk-based; entities must update at least annually for high-risk is common practice but **SFC text sets no fixed term** (was "annual" in pre-2020 text) | **High risk ≥ annually; medium/low ≥ every 2 years** or on trigger events | VERIFIED (Supersoc.); SFC = UNVERIFIED that no fixed term remains |
| Record retention | **5 years** from entry (art. 96 EOSF) for SFC entities; **10 years** (art. 28 Ley 962/2005) for others incl. Supersociedades obligated subjects | — | VERIFIED |
| Criminal-record clients | SFC CE 010/2025 (28-Aug-2025): instructions on financial inclusion of people with criminal records; does not modify Ch. IV | — | SECONDARY (cerlatam.com) |
| Wire transfer (travel rule) | Originator/beneficiary data must travel with transfer; reduced data ≤ **USD 1,000**; "client" status for frequent remitters (≥3 ops/quarter etc. > ½ SMMLV) | — | VERIFIED (CE 027 §5) |

## 1.4 Screening lists & freezing — Colombia
- **Binding lists (Listas Vinculantes):** UNSC 1267/1989/2253 (ISIL/Al-Qaida), 1988 (Taliban), 1718 (DPRK), 1737/2231 (Iran), 1373 domestic designations; Supersociedades text also lists US terrorist lists and EU terrorist lists as "vinculantes" — legally only UNSC lists are binding under art. 20 Ley 1121/2006; OFAC used as reputational list. (VERIFIED for CBJ 2026 text.)
- **Distribution:** UN consolidated list (scsanctions.un.org); Cancillería communicates UNSC updates; UIAF publishes guidance. No national "push" API. (SECONDARY.)
- **Freeze/report:** on identifying any asset of a listed person: report **immediately** to UIAF (ROS) **and** inform Fiscalía General de la Nación (VERIFIED, CBJ 2026 §9.15/§9.21.4). Financial-sector freeze mechanics follow Ley 1121/2006 art. 20 and Decreto 1068/2015; SFC CBJ requires permanent consultation. (SECONDARY.)
- Domestic lists commonly screened: Procuraduría (antecedentes disciplinarios), Contraloría (responsables fiscales), Policía (antecedentes judiciales), RNMC — reputational, not legally mandated.

## 1.5 Other automatable recurring obligations — Colombia
| Obligation | Who | Frequency / deadline | Conf. |
|---|---|---|---|
| SARLAFT review of stages/elements | SFC entities | At least **semiannual** | VERIFIED (CE 027) |
| Monitoring reports (residual risk profile) | SFC | At least semiannual | VERIFIED |
| Oficial de cumplimiento report to board | SFC: at least semiannual (SECONDARY); Supersociedades: **at least annually** | VERIFIED (Supersoc.) |
| Internal audit evaluation of SARLAFT | SFC | **Semiannual**, reported to OC and board | VERIFIED |
| Revisor fiscal SARLAFT report | SFC: quarterly report to board (SECONDARY) | |
| Training | SFC: at least **annually** + induction; Supersoc RMM: at least annually | VERIFIED |
| Update of SAGRILAFT policy | Supersociedades: OC proposes updates at least **every 2 years** | VERIFIED |
| OC appointment notification | Supersociedades: 15 business days via Informe no financiero; SFC: OC must be *posesionado* before SFC (SECONDARY) | |
| Annual management report must include AML risk management section | SFC | Annual (fiscal year close) | VERIFIED |
| UIAF info requests | SFC entities | 5 business days | VERIFIED |
| Public disclosure of AML risk strategy (art. 97 EOSF) | SFC | Ongoing | VERIFIED |
| UIAF reporting calendars | All | UIAF publishes annual PDF calendars per sector (e.g. 2026 PSAV calendar) — useful to seed deadline engine | VERIFIED |

## 1.6 Recent / upcoming changes (2025–2027) — Colombia
| Date | Change | Impact | Conf. |
|---|---|---|---|
| 13-Jan-2025 | UIAF Res. 010/2025: 5-business-day deadline for UIAF information requests to SFC entities | SLA tracking | VERIFIED |
| 23-Dec-2024 / 2026 | Supersolidaria CE 72/2024 & CE 95/2026 freeze cooperative cash-report thresholds at COP 5M / 30M | Parameterize thresholds by supervisor & effective date | VERIFIED |
| 28-Aug-2025 | SFC CE 010/2025 (clients with criminal records) | Onboarding rules | SECONDARY |
| 14-Apr-2026 | SFC CE 004/2026 applies SARLAFT to STANH (non-mortgage securitization companies) | New obligated entities | SECONDARY |
| **2-Jul-2026** | **Supersociedades CE 100-000020/2026: new integral CBJ; SAGRILAFT + PTEE merged (Ch. IX "Sistema de Autocontrol y Gestión de Riesgos LA/FT/FP y C/ST"); thresholds in UVB; quarterly AROS; DD update 1y/2y; repeals CE 100-000016/2020, 100-000004/2021, 100-000011/2021, 100-000015/2021, 100-000008/2022, 100-300000/2024 etc.** | Re-map all real-sector clients; existing obligated must comply by **31-May-2027** | VERIFIED |
| Pending | SFC draft on UBO update obligations (§4.2.2.2.1.6.3) | | SECONDARY (infolaft) |
| Pending | FATF/GAFILAT 5th-round mutual evaluation of Colombia (on-site expected ~2025-2026) may drive SFC/UIAF annex updates; SFC annexes still Jul-2019 | Watch | UNVERIFIED |

## 1.7 Gaps — Colombia
- Could not read Res. UIAF 314/2021 and 84/2022 body text (scanned) — confirm sanctions/registration and whether Res. 84/2022 changed thresholds.
- Did not read the SFC "Cuadros complementarios" code tables (product types, transaction types, ID types) — needed for generators; URLs above.
- SFC update-frequency rule for KYC and OC reporting frequency to board under current CBJ text not confirmed.
- Insurance-specific UIAF reports (beyond Anexo 6 INAS) and *profesionales de cambio* (DIAN) not researched in depth.
- No evidence of any SIREL API/web-service; confirm with UIAF.

---

# 2. ARGENTINA

## 2.0 Regulatory map
- **Law:** Ley 25.246 (art. 20 lists 23+ categories of *Sujetos Obligados* — SO), as amended by **Ley 27.739 (B.O. 15-Mar-2024)** which added PSAV, PSP/non-financial credit providers, custodians, lawyers, trust/company service providers, etc., and raised sanctions. Ley 26.734 (FT freezing), Decreto 918/2012 (freezing), Decreto 489/2019 (RePET).
- **FIU:** UIF (Unidad de Información Financiera), now under Ministerio de Justicia. Reporting system **SRO+ (Sistema de Reporte de Operaciones)** reached from https://www.argentina.gob.ar/uif with CUIT + password; registration of SO under Res. UIF 50/2011 (as amended; Res. 47/2024; **Res. 37/2026 fully digitalized registration — supporting documents uploaded in SRO+**). VERIFIED (UIF resolutions page, fetched 2026-10-17).
- **OCE (organismos de control específico):** BCRA (banks, FX, PSP), CNV (capital markets, crowdfunding, **PSAV registry**), SSN (insurance), INAES (mutuales/cooperativas).
- Thresholds are expressed in **SMVM** (Salario Mínimo, Vital y Móvil). Per **Res. UIF 84/2023**, UIF thresholds update **semiannually** using the SMVM in force at 31-Dec (applies H1) and 30-Jun (applies H2). SMVM: ARS 367,800 from Jun-2026 (Res. CNEPySMVM 9/2025 schedule) → applies to H2-2026; reportedly ARS 383,800 from Sep-2026 (Res. 4/2026). (SECONDARY — press: cronista.com, bloomberglinea.com, elsol.com.ar.) **Engine must store SMVM table with effective dates and apply the 31-Dec/30-Jun snapshot rule.**

### UIF resolutions in force per SO (VERIFIED — https://www.argentina.gob.ar/uif/normativa-uif/resoluciones-aplicables-cada-sujeto-obligado)
| SO (art. 20 inc.) | Resolution |
|---|---|
| Financial & FX entities (1, 2) | **14/2023** (t.o. by 56/2024, 199/2024, 78/2025); REI: 67-E/2017 (mod. 132/2024) |
| Remittance companies (3) | 1/2023 |
| Cash-in-transit / custody (4) | 2/2023 |
| **Payment/collection service issuers, operators, providers (PSP/PSC) (5) and non-financial credit providers (6)** | **200/2024** (replaced 76/2019); REI 67-E/2017 |
| Capital markets (CNV) (7) and crowdfunding platforms (8) | 78/2023 (adjusted by **109/2026** to Ley 27.802; adds simplified regime for Fondos de Asistencia Laboral) |
| Insurers/reinsurers (9) & intermediaries life-savings/retirement (10) | 126/2023 |
| Mutuales & cooperativas (11) | 99/2023 |
| Capitalización y ahorro (12) | 169/2023 (thresholds mod. 71/2024) |
| **PSAV (13)** | **49/2024** |
| Gambling (14) | 194/2023 |
| Real-estate brokers (15) | 43/2024 |
| Art/antiques (54/2024), jewelry/precious metals (55/2024) (16) | |
| Lawyers 48/2024 (REI first filing suspended by 90/2026), accountants 42/2024, notaries 242/2023 (17) | |
| Trust & company service providers (18) | 140/2012 |
| Registries (19): Property registries 41/2011 → replaced by **93/2026 from 8-Nov-2026**; car registries 127/2012 | |
| OCEs (20): BCRA 12/2011, ARCA 38/2011, SSN 19/2011, CNV 22/2011, INAES 12/2012 | |
| Sports (23) | 32/2012 |
General (all SO): ROS LA deadlines 56/2024; **RFT 207/2025** (repealed 29/2013); **RFP (proliferation) 03/2026**; PEP **35/2023** mod. **192/2024**; UBO **112/2021**; registration 50/2011; ROS online 51/2011; RSM online 70/2011; thresholds update 84/2023; sanctions procedure 90/2024 & 129/2024; module value for fines 95/2025 (ARS 54,140).

## 2.1 Report catalog — Argentina

Common: all reports through **SRO+** (UIF web; CUIT + clave). ROS/RFT/RFP are web forms; RSM can be web-form ("RSM individual") or **XML bulk via the "SRO Masivo" desktop application** (installer v7.2) validating each XML against per-SO XSD schemas ("Esquema de Operaciones RSM y Manual Técnico RSM"). RSA is a web form. (VERIFIED — https://www.argentina.gob.ar/uif/rsm ; FAQ https://www.argentina.gob.ar/uif/preguntas-frecuentes-generales-uif/consultas-acerca-de-reportes-de-operaciones-rsm-rsa-ros-la-ft-fp)

| # | Native report | Sector(s) | Trigger / threshold | Deadline / periodicity | Channel | Format | Layout source | Auth | Zero report? | Conf. |
|---|---|---|---|---|---|---|---|---|---|---|
| AR-1 | **ROS-LA** (Reporte de Operación Sospechosa de Lavado de Activos) | All SO | Suspicious (realized or attempted) | **24 h** from the date the SO concludes it is suspicious, and **no later than 90 calendar days** from the date the operation was performed/attempted | SRO+ tab "ROS/RFT" → Nueva operación | Web form + supporting docs | Instructivo https://www.argentina.gob.ar/uif/instructivos/rosrft ; Res. 51/2011; deadlines Res. 56/2024 / 14/2023 art. 40 | CUIT + clave (OC registered) | No (no "ausencia" for ROS) | VERIFIED |
| AR-2 | **RFT** (Reporte de Financiación del Terrorismo) | All SO | Assets owned/controlled (directly/indirectly) by, or operation by/for, persons designated by UNSC 1267 or by the Executive (Decreto 918/2012 art. 25 d) — check **RePET**; or assets possibly linked to FT (CP arts. 41 quinquies, 306) | **24 h from the operation (realized or attempted)**; may pre-notify UIF by any means; if impossible without delay, notify competent judge immediately | SRO+ ROS/RFT tab | Web form | Res. 207/2025 (B.O. 4-Nov-2025) | idem | No | VERIFIED |
| AR-3 | **Congelamiento administrativo** (self-executed freeze) + immediate notice | All SO | Match per AR-2 1(a)(b)(c) | Freeze **without delay and inaudita parte**, inform UIF immediately, file RFT | SRO+ / "Reporte Orden de Congelamiento" | Web | Res. 207/2025 art. 3 | | | VERIFIED |
| AR-4 | **Reporte Orden de Congelamiento** (response to UIF freeze resolution) | All SO | Notification of UIF freeze resolution | Screen client base; immobilize assets; report results **within 24 h** of notification; report later operations during freeze (freeze ≤ 6 months, extendable once) | SRO+ system "REPORTE ORDEN DE CONGELAMIENTO" | Web | Res. 207/2025 art. 4 | | | VERIFIED |
| AR-5 | **RFP** (ROS Financiamiento de la Proliferación) | All SO | UNSC proliferation designations (1718 etc.) | **24 h** from operation; administrative freeze rules analogous | SRO+ | Web | Res. 03/2026 https://www.argentina.gob.ar/normativa/nacional/resoluci%C3%B3n-3-2026-422247 ; instructivo /uif/instructivos/rfp | | | SECONDARY (title read; body not read) |
| AR-6 | **RTE** – Reporte de Transacciones en Efectivo de Alto Monto | Banks & FX entities (Res. 14/2023) | Each cash deposit/withdrawal (local or foreign currency) **≥ 40 SMVM** (raised from 20 by Res. 78/2025) | Monthly, **day 1–15** for previous month | SRO+ RSM | Web template (RSM individual) or XML (SRO Masivo) | Instructivo https://www.argentina.gob.ar/uif/reporte-de-transaccion-en-efectivo-rte | | **Yes — "Informar período sin movimientos"** for RSM (monthly) | VERIFIED |
| AR-7 | **RTEOC** – Cash in FX operations | FX entities/banks | Cash FX buy/sell **≥ 40 SMVM** | Monthly, day 1–15 | SRO+ | idem | https://www.argentina.gob.ar/uif/reporte-de-transacciones-en-efectivo-en-operaciones-de-cambio-rteoc | | Yes | VERIFIED |
| AR-8 | **RTI** – Reporte de Transferencias Internacionales | Banks & FX entities | Each transfer between AR account and foreign account **≥ 1 SMVM** | Monthly, day 1–15 | SRO+ | Template/XML; fields incl. RIOC (BCRA FX regime) operation code, BIC/SWIFT of origin/destination bank, all persons linked to product | Guía RTI 09-2025 https://www.argentina.gob.ar/sites/default/files/guia_rti_entidades_financieras_09-2025.pdf | | Yes | VERIFIED |
| AR-9 | **RSA** – Reporte Sistemático Anual | Financial, PSP, PSAV, insurers etc. | Company info, ownership (shareholders ≥ 20 %, look-through; PEP shareholders any %), accounting, business, client counts by type | **2-Jan → 15-Mar** for prior calendar year | SRO+ tab RSA | Web form | https://www.argentina.gob.ar/uif/reporte-sistematico-anual-rsa | | n/a | VERIFIED |
| AR-10 | **Informe técnico de autoevaluación de riesgos** + methodology (+ risk tolerance declaration) | All SO under 2023-24 regimes | Annual (FX houses under Ley 18.924 may opt biennial) and on risk-level change | **Before 30-April** each year, to UIF and the OCE (BCRA/CNV/SSN/INAES); new SO: before starting operations | SRO+ / TAD (UIF electronic) | PDF documents | Res. 14/2023 art. 5–6; 49/2024; 200/2024 | Approved by board | n/a | VERIFIED |
| AR-11 | **Informe del Revisor Externo Independiente (REI)** | Same | Annual (biennial option for FX houses) | **Within 120 calendar days** after the self-assessment deadline (≈ 28-Aug); PSAV first report by 31-Aug-2025; PSP (new SO) by 31-Aug-2026 | Electronic to UIF | PDF | Res. 67-E/2017 mod. 132/2024 | REI registered | n/a | VERIFIED |
| AR-12 | **OC designation / substitution** | All | Appointment; suplente acting | Suplente acting: notify within **24 h** (sujetosobligados@uif.gob.ar); removal + new appointment: **15 days** | Email/SRO+ registration | — | Res. 14/2023 art. 11 | | | VERIFIED |
| AR-13 | **PSAV – Reporte de operaciones con Activos Virtuales** | PSAV (CNV-registered) | All VA operations **≥ 6 SMVM**; includes client base (active/inactive > 1 year), type, date, ARS amount, origin/destination country | Monthly, day 1–15 | SRO+ RSM PSAV | Template/XML | Res. 49/2024 art. 39; https://www.argentina.gob.ar/uif/rsm-psav | | Yes (sin movimientos) | VERIFIED |
| AR-14 | **PSAV – Reporte de altas y bajas de clientes** | PSAV | Client base: name, ID/CUIT/CUIL, nationality, PEP status | Monthly, day 1–15 | SRO+ | Template/XML | idem | | Yes | VERIFIED |
| AR-15 | **PSP – RMTC (Reporte mensual de tarjetas de crédito)** | Aggregators/sub-acquirers; card issuers | Users' monthly card spend/cash advances ≥ **13 SMVM** (individual) / ≥ **50 SMVM** (corporate); prefunding ≥ 13 / 100 SMVM; issuers: card & holder identification | Monthly, **day 15 → last business day** for previous month | SRO+ | Template | Res. 200/2024 art. 39(a) | | Yes | VERIFIED |
| AR-16 | **PSP – RTE** | Proveedores de Servicios de Cobro (PSC) / de Pago (PSP) | Identify cash depositor ≥ **20 SMVM**; report monthly cash deposits/payments summing ≥ **2 SMVM/month** (PSC: payments; PSP: account holders receiving cash) | Monthly, day 1–15 | SRO+ (template to be published) | Template | Res. 200/2024 art. 39(b); https://www.argentina.gob.ar/uif/instructivos/rsm-psp | | Yes | VERIFIED |
| AR-17 | Capital markets RSM: **Cuentas comitentes**; **RTI de valores negociables** | ALyCs etc. (Res. 78/2023) | per resolution | Monthly 1–15 | SRO+ | | https://www.argentina.gob.ar/uif/rsm-mercado-de-capitales | | | SECONDARY (thresholds not read) |
| AR-18 | Insurance RSM: pago de siniestros; rescates anticipados vida/retiro; seguros patrimoniales | Insurers (126/2023) | per resolution | Monthly | SRO+ | | https://www.argentina.gob.ar/uif/rsm-sector-seguros | | | SECONDARY |
| AR-19 | Mutuales & cooperativas RSM | 99/2023 | per resolution | Monthly | SRO+ | | https://www.argentina.gob.ar/uif/rsm-mutuales-y-cooperativas | | | SECONDARY |
| AR-20 | DNFBP RSM: real-estate brokers (purchase/sale; rentals ≥ **300 SMVM/yr**); accountants (cash real-estate deals > **700 SMVM**, border zone, trusts, company formation, share transfers); notaries (> **750 SMVM**, raised by 78/2025) | 43/2024, 42/2024, 242/2023 | | Monthly | SRO+ | | UIF RSM pages | | | VERIFIED (titles on UIF pages) |
| AR-21 | **Reporte de Registración y Cumplimiento** | All SO | A client that is itself a SO fails to present UIF registration certificate | Monthly within RSM | SRO+ RSM → "Reporte de Registración y Cumplimiento" | **XML (XSD v1.2)** | https://www.argentina.gob.ar/sites/default/files/reporte_de_registracion_y_cumplimiento_v.1.2.zip | | | VERIFIED |
| AR-22 | Response to UIF information requests (art. 14 Ley 25.246) | All | Request | Per request | SRO+ "Requerimientos" | | /uif/instructivos/requerimientos | | | SECONDARY |
| AR-23 | Regularización de activos special ROS mechanism | All | Ley 27.743 tax amnesty | Expired with amnesty window | | | Res. 110/2024 | | | SECONDARY (historic) |

## 2.2 Format details — Argentina

### ROS / RFT (SRO+ web form) — VERIFIED from UIF instructivo
1. Log in with CUIT + clave → tab "ROS/RFT" → select tipo de sujeto → "Nueva operación" → choose ROS or RFT.
2. Header fields: exteriorización voluntaria flag (Ley 26.860 instruments only); **Realizada / Tentada**; **Delito precedente** Y/N and type (multi), **fuente de información**.
3. **Involved persons** (all natural/legal, local/foreign, directly or indirectly linked): CUIT/CUIL/CDI, ID doc type/number, names, geolocation, flags "relacionada con paraíso fiscal / Triple Frontera", **PEP** checkbox (opens PEP fields), **relación con el hecho**: DIRECTA / INDIRECTA / INCUMPLIMIENTO DEBIDA DILIGENCIA. Unknown fields must be left blank (do not use "S/D", "NULL", "XX", etc.).
4. Operation detail, amounts, products, narrative (fundamentación — reasons/unusual features), attachments.
5. Confidential — not shown to supervisors except BCRA/CNV during on-site supervision; REI may see anonymized info only.
- Generator implication: produce a structured JSON payload + narrative + attachment bundle; submission remains a manual web step (no ROS XML/API published).

### RSM bulk XML (SRO Masivo) — VERIFIED (manual SROM v2, XSD sample)
- Desktop app ("SROMasivo", installer v7.2 zip) authenticates with the same SRO credentials; reads a folder of **.xml files, one `<Operacion>` per file**, checks well-formedness, then validates against the per-SO/per-report XSD; for RSM requires selecting the **period (month/year)**; sends valid operations only; returns a **control number per operation**; moves files to OK/Error folders; duplicates detected by control number.
- XSD convention (seen in "Reporte de Registración y Cumplimiento" v1.2): root `Operacion`; element names in Spanish with accented chars encoded as two-digit codes (e.g. `Nombre_o_Raz93n_Social`, `C93digo_Postal`, `Pa92s`); `xs:annotation/xs:documentation` carries validation hints (`ValidationExpression` regex e.g. CUIT `^\d{2}\-?\d{8}\-?\d{1}$`, `ValidarCUIT` function, conditional groups like `Tipo_Persona`); enumerations for provinces etc.
- **Anulación / rectificación masiva** instructivos available (https://www.argentina.gob.ar/sites/default/files/anulacionesmasivas_operacionessistematicas_version_1.2.pdf ; intructivo_rectificacionesmasivas_rsms.zip).
- **Zero report:** "Informar período sin movimientos" per month; filing a "sin movimiento" blocks reporting operations for that month.
- Web-template validations (RTE/RTI): CUIT check digit and person-type consistency; date not after report date / current month; at least one operator and one holder; count of persons linked to product must equal declared count; if currency is ARS, both amount fields equal; integers only (no decimals); no special characters in names (e.g. D'Angelo → Dangelo).
- Gap: the per-SO XSDs for RTE/RTI/PSAV/PSP were not downloaded (behind the RSM page "Esquema de Operaciones RSM y Manual Técnico RSM" per SO type). Action: download from https://www.argentina.gob.ar/uif/rsm and sector pages.

## 2.3 KYC minimums — Argentina (Res. 14/2023 t.o.; PSAV 49/2024 and PSP 200/2024 mirror it) — VERIFIED
| Topic | Rule |
|---|---|
| Natural persons | Full name; ID type/number (DNI; foreign ID card or passport); nationality; DOB; **marital status**; **CUIL/CUIT/CDI**; real address (street, number, locality, province, country, ZIP); **electronic domicile** (art. 75 CCCN); main occupation/profession; PEP DDJJ; FT screening. Same for attorneys/representatives + proof of authority. |
| Legal persons | Name; registration date & number; CUIT/CDI/CIE; legal address; electronic domicile; main activity; board members/legal reps/attorneys (identified as natural persons); **UBO identification and verification**; listed companies exempt from UBO; PEP & FT checks on UBOs. Medium/high risk: bylaws, board list, shareholding. |
| Other structures | Public sector: identify operator + designation instrument; trusts: trustee, settlors, beneficiaries, administrator + UBOs (financial trusts: only trustee); FCI: management & custodian companies. |
| Remote onboarding | Allowed with **"técnicas biométricas rigurosas, almacenables, auditables y no manipulables"** with anti-fraud (physical & digital attack) protection; automated verification allowed if evidence shows ≥ human performance; SO must perform a risk analysis of the remote procedure; no UIF pre-approval; REI must opine on it; keep evidence with date/time. (In practice: RENAPER biometric validation.) |
| Risk tiers | Low → **Simplified** (basic ID); financial-inclusion low-risk products: simplified while monthly credits ≤ **12 SMVM** or annual ≤ **120 SMVM**. Medium → **DD Media** (proof of activity & source of income/funds). High → **Reforzada** (documented source of income/funds/wealth, purpose, sanctions/AML history, intensified monitoring). Foreign PEPs and FATF "call for action" countries = high risk by default. |
| File update (legajo) | High risk ≤ **1 year**; medium ≤ **3 years**; low ≤ **5 years** (medium/low may skip with RBA & materiality). High-risk updates must be based on documents. |
| UBO | **≥ 10 %** of capital or voting rights, or final control by other means; fallback: highest administrative authority. Client must notify UBO change within **30 calendar days**. (Res. 112/2021) |
| PEP | Res. 35/2023 (mod. 192/2024): national, provincial, municipal, CABA, foreign and international-organization PEPs + relatives and close associates; status lasts during office **+ 2 years** after leaving (relatives/associates follow principal). **Sworn statement (DDJJ PEP)** at onboarding and on change (may be electronic); SO must verify. |
| Cash deposits identification | Banks: depositor identification ≥ **40 SMVM**; PSAV ≥ **6 SMVM**; PSP ≥ **20 SMVM**. FX houses: < 20 SMVM/month only basic ID. |
| Retention | **10 years** from operation (transactions) and from end of relationship/last transaction (CDD records). |
| Training | Annual plan; new staff within **60 business days**. |

## 2.4 Screening lists & freezing — Argentina
- **RePET** (Registro Público de Personas y Entidades vinculadas a actos de Terrorismo y su Financiamiento; Decreto 489/2019, Ministerio de Justicia) — consolidates UNSC lists (1267 ISIL/AQ, 1988, 1718 DPRK, 2231 Iran, Yemen, etc.) and domestic designations. **Machine-readable JSON:** https://repet.jus.gob.ar/xml/personas.json and https://repet.jus.gob.ar/xml/entidades.json (VERIFIED, page fetched 2026-10-17). Mandatory check under Res. 207/2025 art. 1.
- **Freeze:** self-executed administrative freeze "sin demora e inaudita parte" + immediate notice + RFT within 24 h; on UIF freeze resolution → screen & report within 24 h via "Reporte Orden de Congelamiento"; tipping-off prohibited (tell client only that assets are frozen under Ley 26.734 art. 6 / Decreto 918/2012). UIF freeze lasts ≤ 6 months, renewable once. (VERIFIED, Res. 207/2025.)
- Proliferation: Res. 03/2026 (UNSC 1718/2231) — analogous RFP + freezing. (SECONDARY.)
- FATF lists: high-risk "call for action" → mandatory EDD; increased monitoring → risk factor.

## 2.5 Other recurring obligations — Argentina (automatable)
| Obligation | Deadline | Conf. |
|---|---|---|
| Risk self-assessment + methodology + risk tolerance declaration to UIF & OCE | Before **30-Apr** yearly (FX houses may be biennial) | VERIFIED |
| REI report | ≤ **120 calendar days** after self-assessment deadline | VERIFIED |
| RSA | 2-Jan → 15-Mar | VERIFIED |
| Monthly RSM/RTE/RTI/RTEOC/PSAV reports | Day 1–15 monthly (RMTC: day 15 → last business day) | VERIFIED |
| Internal audit includes AML in annual program | Annual | VERIFIED |
| AML manual, code of conduct, compliance committee (financial entities) | Maintain/approve | VERIFIED (articles exist) |
| Training plan | Annual; induction ≤ 60 business days | VERIFIED |
| Threshold refresh (SMVM) | 1-Jan and 1-Jul snapshots (Res. 84/2023) | VERIFIED (title) |
| SO registration & updates with UIF (SRO+), incl. OC data | On change; Res. 37/2026 digital docs | SECONDARY |
| Check clients that are SO are UIF-registered (consulta de SO registrados) → otherwise "Reporte de Registración y Cumplimiento" | Monthly | VERIFIED |

## 2.6 Recent / upcoming changes 2025–2027 — Argentina
| Date | Change | Conf. |
|---|---|---|
| 15-Mar-2024 | Ley 27.739 reforms Ley 25.246 (new SO incl. PSAV; FP; sanctions) | SECONDARY |
| 26-Mar-2024 | Res. 56/2024 new ROS deadlines (24 h / 90 days) + RFP | VERIFIED |
| 2024 | Res. 49/2024 PSAV regime; first self-assessment 30-Apr-2025, first REI 31-Aug-2025, first RSA by 15-Mar-2025 | VERIFIED |
| 19-Dec-2024 | Res. 199/2024 amends 14/2023 (KYC, remote onboarding, file update 1/3/5 y); Res. 200/2024 PSP & non-financial credit regime (implementation by 1-May-2025; new SO first self-assessment 30-Apr-2026, first REI 31-Aug-2026) | VERIFIED |
| 10-Dec-2024 | Res. 192/2024 PEP clarifications | VERIFIED |
| 12-Mar-2025 | CNV RG 1058/2025 PSAV registration categories, capital, custody, AML; existing PSAV to adapt by 1-Aug-2025 (local) / 1-Sep-2025 (foreign) | SECONDARY (law-firm notes) |
| 5-Jun-2025 | Res. 78/2025 raises thresholds (cash deposits/RTE/RTEOC 20 → 40 SMVM; notaries 750 SMVM; car & property registries) | VERIFIED |
| 2025 | Res. 95/2025 module UIF = ARS 54,140 (fines) | VERIFIED (title) |
| 4-Nov-2025 | Res. 207/2025 new RFT & freezing regime (repeals 29/2013) | VERIFIED |
| early 2026 | Res. 03/2026 RFP & freezing | SECONDARY |
| 2026 | Res. 37/2026 digital SO registration in SRO+; Res. 35/2026 info exchange among OCEs; Res. 233/2025 OCE exchange from 22-Mar-2026 | SECONDARY |
| 8-Nov-2026 | **Res. 93/2026** property registries regime starts (replaces 41/2011) | SECONDARY |
| 18-Sep-2026 | **Res. 109/2026** adapts capital-markets Res. 78/2023 to Ley 27.802 and adds Fondos de Asistencia Laboral simplified regime | SECONDARY |
| Note | GAFILAT mutual evaluation of Argentina adopted 2024 — drove 2024-25 reforms | UNVERIFIED |

## 2.7 Gaps — Argentina
- Per-SO RSM XSDs (RTE, RTI, RTEOC, PSAV, PSP, insurance, capital markets) not downloaded; ROS form full field list not enumerated beyond instructivo.
- Res. 78/2023 (capital markets) and 126/2023 (insurance) thresholds/report specifics not extracted (texts downloaded: InfoLEG).
- BCRA "Régimen Informativo de Prevención del LA/FT" (t-RI-LAFT) PDF returned HTTP 502 — BCRA may also require parallel AML regimes for banks (e.g. RTE copy to BCRA). Unverified.
- Res. 03/2026 (RFP) and 207/2025 PSAV-specific travel-rule modality (Res. 49/2024 art. 37 says "modality UIF establishes") not found.

---

# 3. CHILE

## 3.0 Regulatory map
- **Law:** Ley 19.913 (UAF, LA/FT); art. 3 inc. 1 lists obligated private persons (banks, financial institutions, factoring, leasing, securitization, fund managers, brokers, insurers, FX houses, money transfer/remittance companies, card issuers/operators incl. prepaid, cash-in-transit, casinos, notaries & conservadores, real-estate brokers & developers, car dealers, customs agents, auction houses, free-zone users, jewelry/precious metals, arms dealers, professional sports orgs, etc.). **Ley 21.521 (Ley Fintech, Jan-2023)** added fintech service providers (crowdfunding platforms, alternative trading systems, custody, intermediation, credit advisory, **payment initiation**, and activities with crypto-assets under CMF registration). Terrorism law now **Ley 21.732** (repealed Ley 18.314; FT offence art. 10). (VERIFIED — Circular 62 footnote 1 and Anexo 1 list.)
- **FIU:** UAF (Unidad de Análisis Financiero). Prudential AML for banks/coops/card issuers additionally by **CMF** (RAN Cap. 1-14; CMF Circular 2.325 of Nov-2022 aligned with UAF circulars). (SECONDARY — Carey law firm note.)
- **NEW consolidated rulebook: UAF Circular N°62 (19-Mar-2025; extract D.O. 3-Apr-2025; in force 1-Jun-2025; transfers chapter for Ley 21.521 providers from 1-Jul-2025)** repeals Circulars 6, 7, 11, 12, 16–19, 34, 35, 40, 42, 46, 47, **49**, 52–60. ⇒ The "known" list (Circ. 49, 57, 59, 60) is **obsolete since 1-Jun-2025**. VERIFIED: https://www.uaf.gob.cl/media/documentos/Circular_N62.pdf ; Anexos https://www.uaf.gob.cl/media/documentos/Anexos.pdf
- **Circular N°63 (30-Jul-2026)** — joint UAF–SCJ instructions for casino operators (VERIFIED title).
- **Oficio Circular N°543 (25-Aug-2026)** — Portal authentication via **Clave Única from 19-Oct-2026**, and a **differentiated access for automated systems** (ROE & ROS) with IP whitelisting; **MiUAF** platform to replace the Portal gradually during **2027**. VERIFIED: https://www.uaf.gob.cl/media/documentos/Oficio_Circular_N543__Implementaci%C3%B3n_Clave_Unica_VF.pdf

## 3.1 Report catalog — Chile

| # | Native report | Sector(s) | Trigger / threshold | Deadline / periodicity | Channel | Format | Layout source | Auth | Zero report? | Conf. |
|---|---|---|---|---|---|---|---|---|---|---|
| CL-1 | **ROS – Reporte de Operaciones Sospechosas** (LA, FT or both) | All art. 3 obligated subjects | Unusual / no apparent economic or legal justification (LA) or possible FT conduct (Ley 21.732 art. 10). **No periodicity or amount** | "En el menor tiempo posible" | **Portal de Entidades Reportantes** → module "Formulario ROS" (4 steps) | Web form + attachments (JPG, PDF, TIF, DOC/DOCX, XLS/XLSX, ZIP, RAR, TXT, PPT, PNG; **banks also CSV validated against the "Manual de Estandarización de la Información Enviada a la UAF"**); ≤ **50 MB per file** | Instructivo ROS (Jul-2025) https://www.uaf.gob.cl/media/documentos/2025_Instrucciones_Env%C3%ADo_del_ROS_IPRI.pdf ; FAQ https://www.uaf.gob.cl/media/documentos/2025_Preguntas_frecuentes_ROS_S6jEvei.pdf | RUN + clave UAF (OC, legal rep or enabled web user) or **Clave Única + UAF key as second factor at submission**; automated-system access per Oficio 543 | No | VERIFIED |
| CL-2 | **ROS por coincidencia con Listas ONU** | All | Any client/prospect matches UNSC sanctions lists (1267, 1333, 1373, 1390, 1718, 1737, 1747, 1803, 1929, 1988, 1989, 2253, 2356, 2371 + successors, as published by Supreme Decree in D.O.) | **Immediately**, no analysis required (to let UAF request freezing under art. 38 Ley 19.913) | Portal ROS | Web form | Circular 62 c.9–c.11 | | | VERIFIED |
| CL-3 | **ROE – Reporte de Operaciones en Efectivo** | All art. 3 subjects | Each cash operation (banknotes/coins) **> USD 10,000** (or CLP equivalent at *dólar observado* of the day), incl. cash deposits into the SO's own bank accounts. ⚠ The older **450 UF** threshold was replaced by USD 10,000 (art. 5 Ley 19.913 as amended) | **Monthly** (banks), **quarterly** (non-bank financial incl. fintech, insurers, coops, FX houses, money transfer, card/prepaid issuers, AFP/AGF, brokers…), **semiannual** (non-financial: notaries, conservadores, real estate, car dealers, casinos, customs agents, free-zone users, jewelers, arms…); due within the **first 10 business days** of the following month (published calendar, e.g. 1–15 Oct-2026) | Portal → "Ingreso ROE" (file upload; 120-minute session) / "ROE Simplificado" | **Banks: generic data file / plain text** (product codes ROEB01 current/demand/savings accounts, ROEB02 time deposits & vale vista, ROEB03 FX operations, ROEB04 payment orders); **non-banks: Excel 2-sheet template**; **FX houses & money transfer: Excel 5-sheet template**; real-estate brokers & free-zone users: **ROE Simplificado** web form | Instructivo ROE (Jul-2025) https://www.uaf.gob.cl/media/documentos/2025_Env%C3%ADo_del_ROE_gxRNfwu.pdf ; templates in Portal module "Instrucciones de reporte ROE" (login required); Calendar 2026 https://www.uaf.gob.cl/media/documentos/Calendario_ROE_2026_fJZ3WvN.pdf | idem | **Yes — "ROE negativo"** (module ROE Negativo: select period & year) | VERIFIED (layouts not public) |
| CL-4 | **Rectificación de ROE / ROE negativo** | All | Approved report needs correction | Within **10 business days** after the ROE deadline | Contact form uaf.cl → "Solicitud" / "Consulta sobre el proceso de envío del ROE"; PDF support docs if swapping ROE↔negativo | — | Instructivo ROE | | | VERIFIED |
| CL-5 | **Registro / actualización de registro UAF** (incl. OC change, legal rep change) | All | Start of activity; any change | Register at start; changes within **10 business days** | uaf.cl (→ MiUAF 2027) | Web | Circular 62 A, B | | | VERIFIED |
| CL-6 | **DPTE – Declaración de Porte y Transporte de Efectivo** | Travellers (public-sector reporting via Aduanas) | Cross-border cash > USD 10,000 | At border | Aduanas | — | uaf.cl public sector | | | SECONDARY (not a platform report) |
| CL-7 | Public-sector ROS (art. 3 inc. 6) | State bodies | | | Portal | | | | | SECONDARY (out of scope) |

ROE statuses: Ingresado → Recibido → Aprobado / **Aprobado Fuera de Plazo** / Rechazado (error messages per row in "Consulta ROE"). Rejected = obligation not met. **Certificado ROE** (only if Aprobado on time) valid 60 days with Folio + verification code — used by counterparties as compliance evidence. (VERIFIED)

## 3.2 Format details — Chile

### ROS (Formulario ROS, 4 steps) — VERIFIED (Instructivo Jul-2025)
- **Step 1 – Operation:** Tipo de reporte (LA / FT / Ambos); linked to previous ROS? (Y → reference of last ROS); scope: single transaction (date) or set of unusual operations (date from/to + number of operations); **amount in CLP** (numeric, no separators); products used (multi-select list); **(1) facts narrative 250–1,000 chars** (who/what products/when/where, chronological); **(2) what was specifically suspicious 250–500 chars**; alert trigger (list or "Otro" + text); analysis period (must cover operation period).
- **Step 2 – Reported/linked persons:** role Reportado / Vinculado (+ link type); natural/legal; name/razón social (≤100), apellido paterno (≤40), materno (≤40); ID type Nacional (RUN/RUT with **check-digit validation** — invalid blocks progress) or Extranjera; nationality & residence (natural) or country of incorporation & operation (legal); address known/unknown (unknown only for linked persons): street type, street name (≤50, UPPERCASE, no symbols), number (≤10, "S/N"), complement, **Región, Comuna** (lists), mobile & email (or "Sin Información"), postal code; economic activity; PEP data.
- **Step 3 – Attachments:** see CL-1; banks select product type for CSV files, validated line by line against the *Manual de Estandarización* (Portal-only document).
- **Step 4 – Review & send** → printable "Certificado de envío" with UAF-assigned ROS number.
- Confidentiality: art. 6 Ley 19.913 tipping-off prohibition.
- Generator implication: field-length constraints (narratives 250–1,000 / 250–500 chars) must be enforced in the case-management UI; bank CSV schema must be obtained from the Portal.

### ROE
- Upload per period; Excel templates downloaded from Portal; bank plain-text "generic data file". Validation is server-side; the platform should pre-validate (USD conversion at *dólar observado* of operation date — Banco Central series).
- Gap: exact column layouts of the Excel/TXT templates are behind login.

## 3.3 KYC minimums — Chile (Circular 62, VERIFIED)
| Topic | Rule |
|---|---|
| Data (all SO) | Name/razón social (+ fantasy name); ID card or passport (natural) / **RUT** (or foreign equivalent) + proof of incorporation, validity and legal form (legal); nationality, profession/occupation (natural) / line of business (legal); country of residence & **tax domicile**; address in Chile or origin; email or phone; **purpose and nature** of relationship/occasional transaction |
| When | Before/during permanent relationship; occasional transactions **≥ USD 3,000** (single or linked); notaries & conservadores **1,000 UF**; FX houses, money transfer, cash-in-transit **USD 5,000**; always on suspicion |
| Client file update | **At least annually** or on relevant change (f.2) |
| Simplified DD | Allowed for low-risk products/channels: third-party data sources, deferred verification until a monetary threshold, deferred UBO, lower update frequency, reduced monitoring, no purpose question — must be documented in the Manual and based on National Risk Assessments |
| Enhanced DD | High-risk countries/products/channels, suspicion, PEPs: purpose, **source of funds & source of wealth**, **senior management approval**, intensified monitoring, more frequent updates |
| Remote onboarding | "Los sujetos obligados podrán utilizar medios electrónicos para la implementación y cumplimiento de todas las obligaciones" (SEGUNDO); UBO declaration may be electronic. No specific biometric standard in Circ. 62 (CMF RAN 1-14 may add for banks — UNVERIFIED) |
| UBO | Natural person with **≥ 10 %** capital/votes directly or indirectly, or effective control; declaration on **UAF base form** (https://www.uaf.cl → Beneficiario final); foreign entities: identity of top manager abroad + local legal reps; **unjustified delay > 40 business days** = red flag (consider ROS / decline) |
| PEP | Chilean & foreign PEPs, **until at least 1 year after leaving office** (SO may extend); includes spouse, civil partner, relatives to 2nd degree of consanguinity (grandparents, grandchildren, siblings) and persons with joint-action voting pacts; minimum list of national positions in h.3 (President, legislators, mayors, councillors, judges, ministers, prosecutors, Central Bank board, SOE executives, party leaders…). UAF provides "Declaración de vínculo" form. Registro de operaciones PEP mandatory |
| Records | Permanent registers: ROE, ROS (incl. discarded cases), DDC, PEP operations, electronic transfers. **Retention ≥ 5 years** from end of relationship / last occasional op; case files of analysed potential STRs 5 years; UNSC list screening evidence **≥ 3 years** |
| Travel rule | Transfers (cross-border or domestic, incl. Ley 21.521 crypto/payment providers) **≥ USD 1,000**: amount, currency/asset, date, originator name, account, ID (RUN/passport/RUT), address; beneficiary name and account; receiving institutions must isolate non-compliant transfers (execute/reject/suspend). In force 1-Jun-2025 (1-Jul-2025 for Ley 21.521 providers) |

## 3.4 Screening lists & freezing — Chile
- **UNSC lists only are mandatory** (list of resolutions in Circ. 62 c.10, published via Supreme Decree in D.O.); UAF website banner "Listas de Resoluciones ONU" links to UN consolidated lists (https://www.uaf.gob.cl/es-cl/sujetos-obligados/sector-privado/listas-de-resoluciones-onu). Screening must be **periodic and systematic** for all clients and prospects; positive match → **immediate ROS**; **freezing is ordered by court at UAF request** (art. 38 Ley 19.913) — SO do not self-freeze under Circ. 62 (contrast AR/PE). (VERIFIED)
- FATF lists (UAF publishes) → EDD / countermeasures; **SII list of preferential tax regimes** → special attention (VERIFIED).

## 3.5 Other recurring obligations — Chile
| Obligation | Frequency | Conf. |
|---|---|---|
| Manual de Prevención y Detección LA/FT/FP — approved by highest governing body, delivered to all staff with evidence | Update **at least every 2 years** + on regulatory change | VERIFIED |
| Training of all staff incl. OC (content: Manual + UAF red-flag guide) with attendance register | **Annually** | VERIFIED |
| OC designation (senior position, e.g. area manager; micro/small companies may use owner/partner) | On registration; change within 10 business days | VERIFIED |
| Risk policy based on National Risk Assessments (ENR 2024) & sector approaches | Ongoing | VERIFIED |
| ROE calendar compliance + Certificado ROE | Monthly/quarterly/semiannual | VERIFIED |
| Note: no formal annual self-assessment filing or external auditor report to UAF is required by Circ. 62 (unlike AR/PE); CMF-supervised entities have additional board/audit requirements (RAN 1-14) | | SECONDARY |

## 3.6 Recent / upcoming changes — Chile
| Date | Change | Conf. |
|---|---|---|
| Jan-2023 | Ley Fintech 21.521 – new obligated subjects | SECONDARY |
| 2024–2025 | Ley 21.732 (new anti-terrorism law) replaces Ley 18.314 | VERIFIED (Circ. 62 fn.) |
| **1-Jun-2025** | **Circular 62** consolidates UAF rules (ROE USD 10k; DDC USD 3k; UBO 10 %; PEP ≥1 y; travel rule USD 1k) | VERIFIED |
| 1-Jul-2025 | Travel rule effective for Ley 21.521 providers | VERIFIED |
| 30-Jul-2026 | Circular 63 (casinos, joint with SCJ) | VERIFIED (title) |
| **19-Oct-2026** | **Clave Única login to Portal; separate authenticated channel for automated ROE/ROS submission (request via SIAC with system type, report types, IP range, technical contact)** | VERIFIED |
| **1-Dec-2026** | **Ley 21.719** (new personal data protection law, Agencia de Protección de Datos) in force — affects KYC data processing, cross-border transfers | SECONDARY |
| **2027** | **MiUAF** platform gradually replaces Portal de Entidades Reportantes (registration, reporting, training, supervision) | VERIFIED |
| Pending | Boletín 16.475-05 – national Registry of Beneficial Owners (bill, Dec-2023) — status unknown | SECONDARY |

## 3.7 Gaps — Chile
- ROE Excel/TXT column layouts and the bank CSV "Manual de Estandarización" are only in the authenticated Portal — must be obtained by a registered SO.
- API/technical spec for the new automated-access channel (Oficio 543) not published; ask UAF via SIAC.
- CMF RAN Cap. 1-14 / Circular 2.325 specifics (banks, coops, card issuers) and CMF rules for Ley 21.521 providers (crypto) not reviewed.
- Status of beneficial-ownership registry bill unknown.

---

# 4. PERU

## 4.0 Regulatory map
- **Law:** Ley 27693 (UIF-Perú) and Reglamento **DS 020-2017-JUS**; Ley 29038 (UIF into SBS; art. 3 lists obligated subjects); DL 1106 (ML offence); DL 1249 (2016 reforms; OCP notaries); **DL 1372 (2018) beneficial ownership** + DS 003-2019-EF; SBS freezing: **Res. SBS 3862-2016** (administrative freezing by UIF-Perú for UNSC 1267/1718/2231 designations).
- **Supervisors:** SBS (prudential + AML for banks, financieras, CMACs/CRACs, cooperativas de ahorro y crédito, insurers, AFPs, **EEDE e-money issuers**, ETCAN etc.) under **Res. SBS 2660-2015** (Reglamento de Gestión de Riesgos LA/FT, mod. 4705-2017, 5060-2018, 3329-2019, 1743-2020 and later); SMV for securities; **UIF-Perú directly** supervises DNFBPs and others under **Res. SBS 789-2018** (amended by 02351-2023 and **01754-2024**), notaries via OCP LA/FT (**Res. SBS 01754-2024**), **PSAV via Res. SBS 02648-2024**, online gaming/sports betting (MINCETUR-supervised) via **Res. SBS 03622-2025** (14-Oct-2025).
- **Systems:** **Portal PLAFT** (https://plaft.sbs.gob.pe/) — templates & instructions for RO, IAOC, ROSEL (only accessible to registered OC); **ROSEL** (ROS en línea, with "Plantilla ROSEL"); **SISDEL** (plaft.sbs.gob.pe/sisdel) for OC designation/changes; financial entities send RO "por el medio electrónico que la Superintendencia establezca" (SUCAVE per SBS FAQ — SECONDARY).
- Note: www.sbs.gob.pe is behind Imperva/Incapsula — automated fetching blocked; SBS consolidated texts were obtained from intranet2.sbs.gob.pe and El Peruano mirrors (lpderecho.pe).

## 4.1 Report catalog — Peru

| # | Native report | Sector(s) | Trigger / threshold | Deadline / periodicity | Channel | Format | Layout source | Auth | Zero report? | Conf. |
|---|---|---|---|---|---|---|---|---|---|---|
| PE-1 | **ROS – Reporte de Operaciones Sospechosas** | All SO (financial under 2660-2015; UIF-supervised under 789-2018; PSAV 2648-2024; notaries via OCP) | Realized or attempted suspicious ops, **any amount** | **Immediately, never > 24 h from the moment the OC qualifies the operation as suspicious** (qualification time depends on complexity) | **ROSEL** | **"Plantilla ROSEL"** (downloaded from Portal PLAFT) + attachments; must not identify the OC or entity (secret codes assigned by UIF) | Res. 2660-2015 arts. 58–60; Res. 2648-2024 art. 27; Res. SBS 3949-2019 (Plantilla ROSEL) | OC registered with UIF-Perú (secret code) | No | VERIFIED |
| PE-2 | **RO – Registro de Operaciones** (financial system) — single ops | Banks, financieras, CMAC/CRAC, coops, insurers, AFP, ETCAN, EEDE… | **≥ US$10,000** general (withdrawals, cheque cashing, cashier's cheques, premiums, claims, pension contributions, loan disbursements, discounting, domestic/international transfers…); **cash transfers / cash delivery to beneficiary ≥ US$2,500**; coops not taking deposits ≥ US$5,000; life-with-savings premiums ≥ US$1,500/yr; cash FX buy/sell **≥ US$5,000**; pledged gold realization ≥ US$2,500. FX rate = average of daily SBS selling rates of the previous month | Record same day; **send monthly** (SBS FAQ: within 15 days after month-end, via SUCAVE — SECONDARY) | SBS electronic channel (SUCAVE) | Fixed structure per SBS instructivo (Reporte RO) | Res. 2660-2015 art. 49 (t.o. 4705-2017, 3329-2019, 1743-2020) | | UNVERIFIED (whether "sin operaciones" must be filed) | VERIFIED (thresholds); SECONDARY (channel/periodicity) |
| PE-3 | **RO – operaciones múltiples** (financial) | idem | Ops individually > **US$1,000** that together in a calendar month **≥ US$50,000** (cash transfers: ≥ US$10,000; non-deposit coops: ≥ US$20,000) | Kept **at SBS disposal** (single ops are the ones sent) | — | — | art. 49.3 | | | VERIFIED |
| PE-4 | **Declaración jurada de origen de fondos** (cash RO ops) + extra docs for foreign-currency cash: transfers ≥ US$7,500, FX ≥ US$10,000, others ≥ US$50,000 | Financial | Cash op recorded in RO | At transaction | Internal (kept) | Form | art. 50, 50-A | | | VERIFIED |
| PE-5 | **RO – UIF-supervised SO** (Res. 789-2018 Título II sectors: real estate, vehicles, casinos/slots, lotteries, hipódromos, pawn/loans, FX houses, money remitters not SBS-licensed, construction, mining, jewelry/precious metals, art/antiques, customs agents, professionals (lawyers/accountants, persona jurídica profesional), NPOs, etc.) | DNFBPs | Sector thresholds per Título II (commonly US$10,000; notaries **US$2,500** with all corporate acts recorded regardless of amount) | Per Instructivo RO; notaries: by the **last calendar day of the following month** (current structure extended; new structure delayed to **1-Mar-2028** by Res. SBS 01917-2026) | Portal PLAFT (Plantilla RO) | Template per Instructivo RO | Portal PLAFT (login) ; Res. 1754-2024 art. 26 (notaries); Res. 03012-2025 | OC | ? | VERIFIED (notaries); SECONDARY (other sectors' thresholds) |
| PE-6 | **RO – PSAV** | PSAV | Exchanges VA↔fiat, VA↔VA, transfers, custody, issuer services: **any amount if cash**; **≥ US$1,000** otherwise | Per Instructivo RO (Portal PLAFT) | Portal PLAFT | Plantilla RO | Res. SBS 02648-2024 art. 26 | OC | ? | VERIFIED |
| PE-7 | **IAOC – Informe Anual del Oficial de Cumplimiento** | UIF-supervised SO incl. PSAV, notaries/OCP | Annual (calendar year): contacts, shareholders %, managers, monthly statistics of RO, unusual ops & ROS, new red flags/typologies, procedures, training stats, manual/code update date, corrective actions, offices | Approved by board within 30 days of year-end; **sent by 15-Feb** (PSAV) | Portal PLAFT | Plantilla IAOC | Res. 2648-2024 art. 30; 789-2018 art. 26 | OC + board approval | n/a | VERIFIED |
| PE-8 | **Informe semestral del OC** to board **and SBS** | Financial (2660-2015) | Semiannual (Anexo 4 content) | Presented to board in month after semester; sent to SBS within **15 days** after presentation | SBS electronic channel | Per Anexo 4 | art. 53 | | | VERIFIED |
| PE-9 | **Informe trimestral del OC** to board | Financial | Quarterly | Within **30 days** after quarter (kept at SBS disposal) | Internal | Anexo 4 | art. 52 | | | VERIFIED |
| PE-10 | **Cajas de seguridad** annual report | Financial entities offering safe-deposit boxes | Holders, ID, number of accesses | **Annual** | SBS electronic | — | art. 51 | | Yes ("no changes" statement) | VERIFIED |
| PE-11 | **Communication of UNSC-designated assets / freeze execution / "no funds" response** | All SO | Match with 1267, 1718, 2231 lists; UIF administrative freeze order | **Without delay**; also report non-existence of funds when UIF orders a freeze | UIF-Perú (per Res. 3862-2016 procedure) | — | Res. SBS 3862-2016; infractions annex of 2648-2024 | | | VERIFIED (obligation); SECONDARY (channel) |
| PE-12 | **OC designation / changes** | UIF-supervised SO, PSAV | Designation within **15 days**; changes/vacancy within **5 business days** | SISDEL | Sworn statement | 2648-2024 art. 7 | Legal rep | | VERIFIED |
| PE-13 | **EEDE international transfers report (Anexo ETF N.º 1-B)** | E-money issuers | International transfers | per Res. SBS 01881-2026 | SBS | — | https://lpderecho.pe/modifican-reglamento-empresas-emisoras-dinero-electronico-resolucion-sbs-01881-2026/ | | | SECONDARY (prudential, not AML) |
| PE-14 | **Informe de auditoría interna (IAI)** on SPLAFT | UIF-supervised SO | Annual | per norm | Kept/sent per SBS | — | 789-2018 art. 33.2 (as amended 1754-2024) | | | VERIFIED |
| PE-15 | **External audit report on SPLAFT** (financial: "informe especial" by external auditors) | Financial | Annual | per Reglamento de Auditoría Externa | SBS | — | 2660-2015 art. 63 | | | SECONDARY |

## 4.2 Format details — Peru
- **ROSEL:** The OC downloads the **Plantilla ROSEL** (spreadsheet-based template — Res. SBS 3949-2019) from Portal PLAFT, fills it and uploads through ROSEL with attachments. Minimum content (2660-2015 art. 59): full identity of persons involved (names, DOB, ID type/number, nationality, profession, address, phone; legal entities: name, RUC, purpose, address, phone, legal rep), third-party executors, prior ROS references, list and description of operations (dates, amounts, currencies, accounts used & linked, place, supporting docs), irregularities that led to suspicion, other info. **No identification of the OC or entity** inside the ROS (only UIF secret codes). Tipping-off prohibited; only OC/alternate may know of the ROS. Retention of unusual-op analyses: 10 years (financial) / 5 years (UIF-supervised).
- **RO (financial):** per operation: ejecutante (physically performs), ordenante, beneficiario/destinatario identification + operation data per Ley art. 9.3 and Reglamento art. 24.5. Backup copy at end of each quarter, 5-year compendium; available to SBS/Ministerio Público within **48 business hours**.
- **Templates (RO, IAOC, ROSEL) and instructivos are inside Portal PLAFT (login)** — not public. Gap.

## 4.3 KYC minimums — Peru
| Topic | Financial (2660-2015) | UIF-supervised (789-2018 t.o.) / PSAV (2648-2024) | Conf. |
|---|---|---|---|
| Natural person data (general regime) | Full names; ID type/no.; nationality & residence; address; phone/email; purpose; occupation & employer; PEP (institution & position); reps/attorneys + powers | Same minimum via **SBS standard "Declaración Jurada de Conocimiento del Cliente"** format (may be signed digitally); PSAV also wallet address/account | VERIFIED |
| Legal person data | Name; **RUC**; purpose/activity; shareholders > **25 %** (incl. PEP flag); purpose; legal reps & powers; related companies/group; main office address & phone | Name, RUC, reps, UBO per DL 1372 | VERIFIED |
| UBO | 2660: natural person exercising final effective control; DL 1372 + DS 003-2019-EF: **≥ 10 %** ownership or control, fallback senior manager; legal entities file **Declaración de Beneficiario Final** with SUNAT | | SECONDARY (10 % from DS 003-2019-EF) |
| Tiers | General / **Simplified** (only for products **pre-authorized by SBS**) / Reinforced (PEPs, relatives to 2nd degree consanguinity & 2nd affinity, spouse/partner, entities ≥ 25 % PEP-owned, FATF non-cooperative countries, etc.; annual shareholder update for legal entities) | PSAV: general regime for **any cash op** or ops **≥ US$1,000**; simplified < US$1,000 non-cash (name + ID; for legal: name, RUC, legal rep) | VERIFIED |
| Remote onboarding | Allowed; verification method chosen by entity per risk studies; non-face-to-face channels treated as risk factor; simplified e-money accounts (cuentas básicas / dinero electrónico simplificado) with caps (2026: ~S/ 3,300 per txn, ~S/ 10,450 balance — press) | Digital DJ allowed | SECONDARY |
| PEP | Persons who hold or **held in the last 5 years** prominent public functions (national/foreign) or prominent functions in int'l orgs; also direct collaborator of the institution's top authority. List of positions: Res. SBS 4349-2016 | Same (5 years) | VERIFIED (definition) |
| Update | Risk-based periodicity; reinforced: more frequent + annual shareholder update | PSAV suppliers ≤ 2 years | VERIFIED |
| Retention | **RO 10 years** from op; SPLAFT info 10 years | **5 years** (789-2018 art. 31 as amended; PSAV 5 years for ROS support) — note the Reglamento of Ley 27693 may require 10 years for RO | VERIFIED (both texts) — conflict flagged |
| Travel rule | Wire transfers: art. 47 (financial) | **PSAV travel rule (arts. 24–25) in force 1-Aug-2026**: all VA transfers; full originator data (name, ID/RUC, wallet/account, address or DOB/place or op ID) + beneficiary (name, wallet); < US$1,000 reduced set; self-hosted wallets: obtain counterparty info from client, else refuse/terminate + consider ROS | VERIFIED |

## 4.4 Screening lists & freezing — Peru
- **Mandatory (binding):** UNSC 1267/1989/2253, **1718**, **1737/2231** lists — check at onboarding and throughout; communicate funds found **without delay** to UIF-Perú; **UIF-Perú orders administrative freezing** (Res. SBS 3862-2016) which SO must execute immediately and respond (incl. "no funds") — PSAV/789 norms list failures as *muy graves* infractions. (VERIFIED in 2648-2024 & 1754-2024 texts.)
- **Also required to check (art. 12 g) of 789-2018 as amended):** **OFAC** list, **EU terrorist list**, FATF non-cooperative countries list, other UNSC lists, others SBS indicates. (VERIFIED — note Peru explicitly mandates OFAC/EU screening for UIF-supervised SO.)
- Domestic: SBS publishes PEP position list; no consolidated domestic sanctions list identified.

## 4.5 Other recurring obligations — Peru
| Obligation | Who | Frequency | Conf. |
|---|---|---|---|
| IAOC to UIF-Perú | UIF-supervised SO, PSAV | Annual; by 15-Feb (PSAV) | VERIFIED |
| OC quarterly report to board; semiannual to board + SBS | Financial | Q: 30 days; S: month after + 15 days | VERIFIED |
| OC annual work programme | Financial | Annual (art. 12) | VERIFIED |
| Risk identification & assessment update | UIF-supervised | At least every **3 years** (789-2018 art. 28 as amended) | VERIFIED |
| New products / new geographic zones risk report | All | Before launch | VERIFIED |
| Training | All | **At least annually**; induction within **30 days** of hiring (infraction otherwise) | VERIFIED |
| Manual & Code of Conduct (approved, acknowledged by staff via DJ, 5-yr retention) | All | Update on change | VERIFIED |
| Internal audit annual report on SPLAFT (IAI); external audit special report | All / financial | Annual | VERIFIED / SECONDARY |
| Supplier DD update | PSAV | ≤ 2 years | VERIFIED |

## 4.6 Recent / upcoming changes — Peru
| Date | Change | Conf. |
|---|---|---|
| 2023 | Res. SBS 02351-2023 amends 789-2018 | SECONDARY (as given) |
| 10-May-2024 | Res. SBS 01754-2024 — notaries/OCP norm + broad amendment of 789-2018 (definitions incl. FP, PEP 5y, OFAC/EU screening, IAOC, risk assessment every 3y, retention 5y, RO via Portal PLAFT) | VERIFIED |
| 1-Aug-2024 | Res. SBS 02648-2024 — PSAV SPLAFT (120-day adaptation); **travel rule chapter effective 1-Aug-2026** | VERIFIED |
| 28-Mar-2025 | Res. SBS 1139-2025 — notaries' RO Instructivo | VERIFIED (footnotes) |
| 29-Aug-2025 | Res. SBS 03012-2025 — keeps notaries' RO structure; monthly by last day of following month | VERIFIED (footnotes) / SECONDARY |
| 14-Oct-2025 | Res. SBS 03622-2025 — SPLAFT for online gaming & sports betting operators (MINCETUR) | VERIFIED |
| 2026 | Res. SBS 01917-2026 — postpones new notaries' RO regime to **1-Mar-2028**; Res. SBS 01881-2026 — EEDE (e-money) reform incl. international transfers report | VERIFIED (footnote) / SECONDARY |
| Pending | GAFILAT 5th-round mutual evaluation of Peru (on-site ~2025-26) | UNVERIFIED |

## 4.7 Gaps — Peru
- Could not access www.sbs.gob.pe (Imperva) → RO/IAOC/ROSEL templates & instructivos, SUCAVE RO layout for financial entities, and current consolidated 789-2018 sector thresholds (Título II) not verified. Request from a registered OC via Portal PLAFT.
- Whether a "negative" RO/ROS is required (no evidence found).
- Post-2020 amendments to 2660-2015 (intranet2 consolidated v5.0 only goes to 1743-2020) — check for 2023–2026 modifications (e.g. FP inclusion, remote onboarding).
- Res. "SBS 000132-2024" cited in the brief could not be located; the Aug-2026 travel-rule date matches **Res. SBS 02648-2024** instead.
