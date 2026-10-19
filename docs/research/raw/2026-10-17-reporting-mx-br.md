# AML/CFT Reporting Research — Mexico & Brazil

- **Research date:** 2026-10-17
- **Scope:** Mandatory AML/CFT reports, formats, KYC minimums, screening lists, recurring obligations, 2025–2027 changes — Mexico (MX) and Brazil (BR).
- **Purpose:** Source of truth for building report generators in a multi-tenant KYC/AML SaaS.
- **Confidence legend:** **VERIFIED** = primary source text read by the researcher (URL given); **SECONDARY** = law firm / vendor / press / unofficial compilation; **UNVERIFIED** = recollection or unconfirmed claim. Where a fact was verified only in part, the unverified part is called out.

_Status: COMPLETE (first pass) — see Gaps sections A.7 and B.6._

## Executive summary (most important findings)

1. **MX "Acuerdo 115/2026" is real**: reform of the LFPIORPI Reglas de carácter general, DOF **07-08-2026**, in force **30-11-2026**, staged to 2027-2028 (EBR, BO, KYC chapters from 01-03-2027; automated monitoring 01-06-2027; first AV audit period 2028).
2. **MX new AV XML formats** (DOF **24-09-2026**) become **mandatory 01-06-2027** for all 17+ actividades vulnerables (new BO block, wallet/geolocation/network fields for VASPs, 24-h avisos). Current XSD (`avi.xsd`) downloaded and documented; new XSDs linked by SAT but not downloadable on 2026-10-17.
3. **MX financial sector** still uses the 2004 **"Formato 41" semicolon-delimited TXT** for ROR/ROI/ROIP via CNBV **SITI PLD/FT** (no public API). ITF (fintech) DCG verified: quarterly ROR (cash ≥ USD 5,000 for ITFs), USD-cash ≥ USD 500, VA ops ≥ 7,500 UDIs, monthly international transfers ≥ USD 1,000, ROI 3 business days after committee (≤ 60 days), 24-h ROI, all with mandatory empty reports.
4. **MX banks 2026**: new **Nivel 2 Bis** account (≤ 15,000 UDIs/month, liveness for remote opening, DOF 10-07-2026, 18-month adaptation) and **facial biometrics / Anexo 71 replaced** (DOF 01-07-2026, 90 business days).
5. **BR correction: Res. BCB 588/2026 was revoked before taking effect** and replaced by **Res. BCB 591/2026** (DOU extra 01-10-2026): automatic Coaf communication of **FX cash ≥ US$10k** and **self-custody VA transfers ≥ US$10k**; Oct–Dec 2026 operations are reported on the **first business day of January 2027**.
6. **BR Siscoaf has a public SOAP webservice** (WSDL retrieved; e-CPF-signed calls) besides form and XML batch; the batch XML layout and occurrence-code tables are only available inside Siscoaf (gap).
7. **BR VASP regime** (Res. BCB 519/520/521, in force 02-02-2026): travel rule phase I 02-02-2027, phase II 02-02-2028; FIs barred from unauthorised VASPs from **06-11-2026**.


---

# PART A — MEXICO

## A.0 Legal architecture (two parallel regimes)

| Regime | Who | Supervisor / recipient | Primary law | Secondary rules |
|---|---|---|---|---|
| **Financial sector ("Entidades Financieras")** | Banks (Art. 115 LIC), SOFOMs, casas de bolsa, casas de cambio, transmisores de dinero, centros cambiarios, SOCAPs/SOFIPOs, uniones de crédito, almacenes, fondos de inversión, ITFs (IFPE/IFC), insurers/afianzadoras (LISF Art. 492), AFOREs | CNBV / CNSF / CONSAR supervise; reports go to **UIF (SHCP) via the supervisor's channel (CNBV SITI PLD/FT)** | Sector law (LIC, LRITF Art. 58, LGOAAC Art. 95/95 Bis, LMV Art. 212, LISF Art. 492, LACP, LRASCAP, LSAR) | One "Disposiciones de carácter general" (DCG) per sector issued by SHCP |
| **Actividades Vulnerables (AV) — DNFBPs + non-bank VASPs etc.** | 17 categories in Art. 17 LFPIORPI incl. **fr. XVI activos virtuales** (non-financial-entity VASPs), lenders that are not financial entities, prepaid cards/e-money not issued by financial entities, real estate, notaries, etc. | **SAT** supervises; avisos go to **UIF via SAT's SPPLD portal** | **LFPIORPI** (last reform DOF 16-07-2025) | Reglamento LFPIORPI (last reform DOF 27-03-2026); **Reglas de carácter general (RCG) — Acuerdo 115/2026, DOF 07-08-2026**; Resolución de formatos oficiales de Avisos e Informes (DOF 30-08-2013, last reformed **DOF 24-09-2026**) |

---

## A.1 LFPIORPI (Actividades Vulnerables) — VERIFIED from primary texts

### A.1.1 Status of the 2025 reform and its secondary regulation

| Item | Fact | Confidence / source |
|---|---|---|
| LFPIORPI reform | Published DOF **16-07-2025**; in force the **day after publication (17-07-2025)** (Transitorio Primero). SHCP had 12 months to amend the RCG. | VERIFIED — https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPIORPI.pdf (Transitorios of the 16-07-2025 decree) |
| Reglamento LFPIORPI reform | Last reform DOF **27-03-2026** (adds Art. 7 Bis — aviso by suspicion; amends Art. 14). | VERIFIED (header of compiled text + recital of 24-09-2026 resolution) — https://www.diputados.gob.mx/LeyesBiblio/regley/Reg_LFPIORPI.pdf |
| **"Acuerdo 115/2026"** (the user's unverified item) | **CONFIRMED.** "ACUERDO 115/2026 por el que se modifican las Reglas de carácter general a que se refiere la LFPIORPI", signed 24-07-2026 by SHCP Secretary Édgar Amador Zamora, **published DOF 07-08-2026**. | VERIFIED — https://dof.gob.mx/nota_detalle.php?codigo=5795797&fecha=07/08/2026 |
| Acuerdo 115/2026 entry into force | **30-11-2026** (Transitorio Primero) with staged exceptions (see A.6). | VERIFIED — same URL |
| New avisos/informes formats | "Resolución que modifica la diversa por la que se expiden los formatos oficiales de los avisos e informes…" **DOF 24-09-2026** (signed 11-09-2026 by UIF head Omar Reyes Colmenares). Reforms Anexos 1–16 and A; adds Anexos **12-C** (facilitadores MASC) and **14-A** (comercio exterior 24-h avisos). **New formats mandatory from 01-06-2027**; old formats unavailable from **01-07-2027**; modificatorios of old-format avisos allowed in old format until 30-06-2027. | VERIFIED — https://dof.gob.mx/nota_detalle.php?codigo=5799445&fecha=24/09/2026 |
| New alta/registro format | "Resolución que modifica la diversa por la que se expide el formato oficial para el alta y registro…" DOF 24-09-2026. Secondary sources disagree on its effective date (30-11-2026 vs 01-02-2027) — not resolved. | VERIFIED existence — https://dof.gob.mx/nota_detalle.php?codigo=5799444&fecha=24/09/2026 ; effective date UNVERIFIED |

### A.1.2 Aviso thresholds (Art. 17 LFPIORPI, text as reformed 16-07-2025)

All in **veces el valor diario de la UMA**. UMA 2026 = **MXN 117.31/day** (valid 01-02-2026 → 31-01-2027; INEGI, DOF 09-01-2026) — SECONDARY (https://facturama.mx/blog/que-significa/uma-unidad-de-medida-actualizacion/); confirm on INEGI. Thresholds VERIFIED from https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPIORPI.pdf; MXN equivalents computed at 117.31.

| Fr. | Activity | Identification threshold | **Aviso threshold** | ≈ MXN 2026 (aviso) |
|---|---|---|---|---|
| I | Juegos con apuesta, concursos, sorteos | 325 UMA | 645 UMA | 75,665 |
| II a) | Tarjetas de servicio/crédito (non-financial issuers) | 805 UMA monthly spend | 1,285 UMA monthly spend | 150,743 |
| II b) | Tarjetas prepagadas | 645 UMA per op. | 645 UMA | 75,665 |
| II c) | Instrumentos de almacenamiento de valor monetario | 645 UMA per op. | 645 UMA | 75,665 |
| III | Cheques de viajero | always identify | 645 UMA | 75,665 |
| IV | Mutuo/préstamo/crédito by non-financial entities | always identify | 1,605 UMA | 188,283 |
| V | Construction / real-estate intermediation | always | 8,025 UMA | 941,413 |
| V Bis (new 2025) | Receipt of funds for Desarrollo Inmobiliario | always | 8,025 UMA | 941,413 |
| VI | Metales/piedras preciosas, joyas, relojes | 805 UMA | 1,605 UMA | 188,283 |
| VII | Obras de arte | 2,410 UMA | 4,815 UMA | 564,848 |
| VIII | Vehículos | 3,210 UMA | 6,420 UMA | 753,130 |
| IX | Blindaje | 2,410 UMA | 4,815 UMA | 564,848 |
| X | Traslado/custodia de valores | always | 3,210 UMA, or always if amount indeterminable | 376,565 |
| XI | Servicios profesionales independientes | always | Whenever a financial op. is executed on behalf of the client | — |
| XII-A | Notarios | always | e.g. real estate ≥ 8,000 UMA; poderes irrevocables, constitution of companies/share transfers: always; fideicomisos ≥ 4,000 UMA; mutuo non-financial: always | — |
| XII-B | Corredores públicos | always | avalúos ≥ 8,025 UMA; others always | — |
| XII-D (new 2025) | Facilitadores (MASC) | as XII-A | as XII-A | — |
| XIII | Donativos (NPOs) | 1,605 UMA | 3,210 UMA | 376,565 |
| XIV | Agentes/agencias aduanales | always for listed goods | always (jewelry ≥ 485 UMA, art ≥ 4,815 UMA per item) | — |
| XV | Arrendamiento de inmuebles | 1,605 UMA monthly | 3,210 UMA monthly | 376,565 |
| **XVI** | **Activos virtuales** (non-financial-entity VASPs; now explicitly incl. custody/transfer and ops with Mexicans from abroad) | always identify | **(a) operation ≥ 210 UMA** OR **(b) fee/contraprestación ≥ 4 UMA** | (a) 24,635; (b) 469 |

- **Aggregation:** ops below threshold that sum above it within **6 months** may trigger aviso (Art. 17 penúltimo párrafo; RCG Art. 19 — 6-month window starts at first op). Fees under XVI(b) are **not** aggregated (RCG Art. 24 Bis 5). If both XVI(a) and (b) are met → **one aviso under (a)** (RCG Art. 24 Bis 5 III). VERIFIED (Acuerdo 115/2026).
- **Cash prohibitions (Art. 32):** cash (MXN/FX) and precious metals prohibited for: real estate ≥ 8,025 UMA; vehicles, jewelry/watches/art, gaming tickets/prizes, blindaje, shares/partes sociales, leases of those assets ≥ 3,210 UMA. VERIFIED.

### A.1.3 Report catalog — Actividades Vulnerables

| Native report name | Sector | Trigger & threshold | Deadline / periodicity | Channel | Format | Layout location | Auth/signing | Zero report? | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| **Aviso (de actos u operaciones)** | All Art. 17 AV incl. **fr. XVI VASPs** | Per op (or 6-month accumulation) ≥ Art. 17 threshold. **One aviso per op** (RCG Art. 24 Bis 1); many avisos per XML file | **By day 17 of the following month** (Art. 23 LFPIORPI); if 17th inhábil → next business day; UIF publishes a staggered **calendarización** allowing filing after the 17th on an assigned day | **SPPLD** (Sistema del Portal en Internet) https://sppld.sat.gob.mx/pld/interiores/sppld.html — online capture ("Captura de avisos") or **"Envío masivo de avisos" (XML upload)** | **XML validated against per-activity XSD**; Excel (.xlsm) templates that generate XML provided | Per-activity pages, e.g. https://sppld.sat.gob.mx/pld/interiores/activos.html ; XSD for VASPs: https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/xsd/avi.xsd | Login with **RFC + e.firma (FIEL) vigente**; legal entities must use the entity's own e.firma, not the representative's (RCG Art. 24) | n/a | VERIFIED |
| **Informe en ceros** (informe de no operaciones) | All AV | No reportable ops in the month | Monthly, by day 17 of next month | SPPLD | XML (current: same per-activity XSD with `informe` and **no `aviso` children**; Excel template `informeenceros.zip`). **From 01-06-2027 a single format, Anexo "14"** | https://www.pld.hacienda.gob.mx/work/models/PLD/plantillas/informeenceros.zip | e.firma | — (this *is* the zero report). **Cannot be modified or deleted once sent** (RCG Art. 25) | VERIFIED |
| **Informe Art. 25 Bis / 27 Bis** (intra-group-only ops) | AV in Grupo Empresarial | Month with only ops under RCG Art. 27 Bis | Monthly | SPPLD | XML (field 2.4 flag in each activity format) | DOF 24-09-2026 Art. 4 | e.firma | — | VERIFIED |
| **Aviso por sospecha (24 h)** | All AV | Suspicion that resources derive from/are destined to ORPI crimes — **regardless of amount, even if op not concluded** | **Within 24 hours** of the suspicion arising (LFPIORPI Art. 18 VI; RCG Art. 26 Bis) | SPPLD | XML (new formats) | DOF 24-09-2026 Anexos | e.firma | — | VERIFIED. **Operational start: can be sent 6 months after the formats resolution takes effect → from 01-06-2027** (RCG Transitorio Quinto + Resolution Transitorio Quinto) |
| **Aviso por hechos o indicios (24 h)** | All AV | Information from other public/private sources linking client to ORPI crimes | Within 24 h (RCG Art. 26 Bis 1) | SPPLD | XML | idem | e.firma | — | VERIFIED; same 01-06-2027 start |
| **Aviso — persona en Lista de Personas Bloqueadas** | All AV | Client or attempted client on the LPB (list under RCG Art. 38) | Within 24 h (RCG Art. 27, 2nd para.) | SPPLD | XML | idem | e.firma | — | VERIFIED |
| **Aviso modificatorio** | All AV | Correction of an **ACEPTADO** aviso | **Once only, within 30 calendar days** of the acuse (Resolution Art. 8) | SPPLD | XML with `<modificatorio><folio_modificacion>AAAA-n</folio_modificacion><descripcion_modificacion>…` + full content of the aviso | https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/inst_modificatorio.pdf | e.firma | Informes en cero cannot be modified | VERIFIED |
| Alta y registro / actualización / baja en el Padrón | All AV (and **VASP-specific registration, RCG Cap. II Bis**; fideicomisos Cap. II Ter) | Before starting the activity | Event-driven; VASPs already registered must update Art. 10 Bis info **within 6 months of 30-11-2026** (i.e. by ~30-05-2027) | SPPLD | Web form + document upload | RCG Arts. 4-10 Quáter | e.firma | — | VERIFIED |
| Designación/aceptación del **Representante Encargado de Cumplimiento** | AV legal entities / trusts | On designation | Designee accepts in SPPLD with own RFC + e.firma; SAT notifies within 10 business days | SPPLD | Web | RCG Art. 10 | e.firma of designee | — | VERIFIED |

### A.1.4 Format details — LFPIORPI aviso XML (example: fr. XVI Activos Virtuales, clave `AVI`)

**Current schema (valid until 31-05-2027)** — VERIFIED by downloading https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/xsd/avi.xsd and https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/ejemplosxml/ejemplo_avi.xml

- Encoding UTF-8; namespace `http://www.uif.shcp.gob.mx/recepcion/avi`; root `<archivo>`; `elementFormDefault="qualified"`.
- Structure: `archivo/informe/{mes_reportado, sujeto_obligado, aviso*}`.
  - `mes_reportado`: `AAAAMM` (pattern `([2-9]\d{3})(0[1-9]|1[0-2])`).
  - `sujeto_obligado`: `clave_entidad_colegiada?` (12-char RFC of the Entidad Colegiada if filing through one), `clave_sujeto_obligado` (RFC 12/13), `clave_actividad` (fixed `AVI`), `exento?` (`1`), `dominio_plataforma` (platform domain, 2–100 chars `[A-Z\d-]`).
  - `aviso` (0..n; zero `aviso` elements = informe en ceros): `referencia_aviso` (1–14 `[A-ZÑ0-9]`, filer-assigned unique ref), `prioridad` (`1` or `2`; 2 is generally understood as "urgente" — UNVERIFIED semantics), `modificatorio?` {`folio_modificacion` pattern `[2-9]\d{3}-[1-9]\d{0,8}`, `descripcion_modificacion` ≤3000}, `alerta` {`tipo_alerta` 3–4 digits from UIF catalog, `descripcion_alerta?` ≤3000}, `operaciones_persona`.
  - `operaciones_persona`: `persona_aviso` {`datos_cuenta_plataforma` (id_usuario, cuenta_relacionada, clabe_interbancaria, moneda_cuenta), `tipo_persona` (persona_fisica | persona_moral | fideicomiso with RFC/CURP/ID doc/representative), `tipo_domicilio?` (nacional | extranjero), `telefono?`}, `dueno_beneficiario*`, `detalle_operaciones` {`operaciones_compra`, `operaciones_venta`, `operaciones_intercambio`, `operaciones_transferencia` (enviadas/recibidas with `hash_operacion`), `operaciones_fondos` (retiros/depósitos with CLABE/bank or foreign account)}.
  - Amounts: `\d{1,14}\.\d{2}` (string). Crypto quantity: `\d{1,14}\.\d{2,10}`. Datetime: `AAAAMMDDhhmmss` (14 chars). Hash: `[A-Z0-9]{1,2000}`. Text fields uppercase-only character classes (`[A-ZÑ\d …]`) → generator must upper-case and strip accents except Ñ.
- File rules (SAT instructivo, VERIFIED — https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/inst_excel.pdf): **max 2 MB per XML**; any file name, extension `.xml`; upload in "Envío masivo de avisos"; acuse appears in "Ver Acuses" after minutes. Batch = one `informe` per file per month per activity, multiple `aviso` elements.
- Rejections: integrity errors → acuse with status **"rechazado"**, which does **not** count as compliance; correct and resend within the legal deadline (Resolution Art. 3, DOF 24-09-2026). VERIFIED.
- No public API: only portal capture or XML upload behind e.firma login. VERIFIED (SPPLD page states it is "el único medio"). No SOAP/REST web service documented — UNVERIFIED that none exists.

**New schema (Anexo "16", DOF 24-09-2026; mandatory 01-06-2027)** — field table VERIFIED from the DOF text; **the new XSD (`avi2.xsd`) and example (`ejemplo_avi2.xml`) are linked on the SAT page but returned an HTML error page on 2026-10-17** (gap). Key additions vs current:
- `persona_moral` adds `naturaleza_juridica`, `tipo_persona_moral`, `otro_pm`; persona_fisica adds `actividad_economica`.
- `dueno_beneficiario` replaced by **`beneficiario_controlador`** (with `bc_persona_fisica` …) + **`total_fig_juridicas`** ("how many legal arrangements sit before the BO").
- `detalle_operaciones` adds `intermediario` (SI/NO), `modalidad` (+`otro`), and per operation **`numero_wallet`**, optional **`geolocalizacion` {latitud, longitud}**, **`red_transferencia`** (+ `descripcion_red_transferencia` if not in catalog).
- Travel-rule-like data to be **retained 10 years and delivered on request** (not in the aviso): originator/recipient/BO name, country, wallet/account id; per op date-time, asset type, amount in VA and MXN, op type, fee (RCG Art. 24 Bis 2). Custody avisos must include custody start date, wallet address and MXN value (Art. 24 Bis 3).
- Per-activity XSDs for all 17+ activities follow the same pattern: `https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/xsd/<clave>.xsd` (activity pages under https://sppld.sat.gob.mx/pld/interiores/). Clave list per activity: see each page (UNVERIFIED list compiled; only `avi` downloaded).

### A.1.5 Other AV obligations (RCG as amended by Acuerdo 115/2026) — VERIFIED

| Obligation | Rule | Deadline / periodicity |
|---|---|---|
| Risk-based assessment (EBR) methodology & entity risk evaluation | RCG Cap. II Quáter (Arts. 10 Septies–10 Septies 6) | Available to authorities on request from **01-03-2027** (covers prior year); review/update when risks change |
| Manual de Políticas Internas | Art. 37/37 Bis; within **90 calendar days of alta**; existing filers must include EBR methodology by **01-03-2027** | Keep available to UIF/SAT on request (not filed) |
| Client risk classification (Grado de Riesgo) & KYC policy (perfil transaccional, alerts) | Caps. III Bis, III Ter | Applies to ops from **01-03-2027** |
| Beneficiario Controlador identification | Cap. III Quinquies; **≥25%** ownership → control by other means → senior managing official; trusts: any natural person with effective control | Ops from **01-03-2027** |
| Expediente review | Art. 21: verify client files **at least once a year**; proof of address ≤ 3 months old | Annual |
| Training | Art. 39 Bis: at least **annually**; trainers ≥5 yrs PLD experience; evaluations; evidence kept 10 yrs | First annual period **01-01-2027 → 31-12-2027** |
| Personnel selection | Art. 39 Bis 2 | New hires from 01-03-2027 |
| Automated monitoring mechanisms | Cap. XIII Art. 41 (client file, consolidated ops, accumulation, risk model, alerts for high risk/PEP/LPB/high-risk jurisdictions, cash monitoring) | By **01-06-2027** |
| Audit (dictamen) | Arts. 42–51: annual Jan–Dec effectiveness review; internal audit allowed if entity risk low/medium, **external independent auditor if high risk**; dictamen delivered to auditee by **last business day of March**; kept ≥5 years; **not filed** (on request) | First audited period **01-01-2028 → 31-12-2028** (dictamen by 31-03-2029) |
| PEP consultation (Consulta PEP 2.0, UIF) | Art. 23 Quáter 1: AV access with e.firma; financial entities may request access by escrito libre | Usable **9 months after 30-11-2026 (≈ 30-08-2027)** |
| Electronic notifications system (SAT) | Art. 6 | SHCP to implement within 8 months of 30-11-2026 |
| UIF compliance certification (optional) | Art. 34 Bis; valid 5 years | Per UIF convocatoria |
| Record retention | Art. 18 IV LFPIORPI: **≥10 years** from the date of the activity | — |

**PEP definition (RCG Art. 23 Quáter, VERIFIED):** natural person who performs or performed prominent public functions in Mexico or abroad (heads of state/government, senior politicians, senior government/judicial/military officials, senior executives of state-owned enterprises, important party officials, **and members of international organizations**). Assimilated: spouse/concubine, relatives by consanguinity or affinity **to 2nd degree**, and associates/partners in legal entities with patrimonial links. **Cooling-off for national PEPs: 1 year after leaving office.** SHCP maintains a nominative list of PEP positions (LFPIORPI Art. 51 Ter).

**Beneficial ownership registry (Secretaría de Economía):** LFPIORPI Arts. 33 Bis/33 Ter (added 16-07-2025) require *sociedades mercantiles* to file share-transfer notices and register beneficial-owner information in the SE electronic system (PSM/“sistema electrónico” under LOAPF Art. 34 XXXI), per guidelines to be issued. VERIFIED (law text); operational guidelines/launch date UNVERIFIED.


---

## A.2 Financial-sector regime (CNBV-supervised) — reports to UIF via CNBV

### A.2.1 Sources used

| Source | What | Confidence |
|---|---|---|
| DCG Art. 58 LRITF (ITFs: IFPE & IFC), DOF 10-09-2018, unofficial compiled text on gob.mx | Full report catalogue for fintechs (Arts. 66-75, 102-103), KYC (Arts. 11-25), LPB (Arts. 60-65), audit (Art. 76), manual (Arts. 82-83) | VERIFIED (compiled text) — https://www.gob.mx/cms/uploads/attachment/file/909810/DCG_Instituciones_de_Tecnolog_a_Financiera__2018.pdf . Reforms after the compilation date not checked (gap). |
| Resolución formato oficial ROR/ROI/ROIP ("Formato 41 campos"), DOF 14-12-2004, reformed 18-05-2005, 25-10-2010, 08-06-2012, 01-08-2013 | Layout for relevantes/inusuales/internas preocupantes | VERIFIED (UIF compiled PDF) — https://www.pld.hacienda.gob.mx/work/models/uif/librerias/documentos/formatos/Formato_41_Campos_Compilado.pdf (also mirrored at gob.mx/cms/uploads/attachment/file/618089) |
| DCG Art. 115 LIC (banks) reform DOF **10-07-2026** | Adds **nivel 2 Bis** account; ID documents (CURP biométrica), remote verification (7ª), transfers (16ª/17ª), USD cash (33ª Quáter), Anexo 2 | VERIFIED — https://dof.gob.mx/nota_detalle.php?codigo=5793357&fecha=10/07/2026 |
| CUB (Disposiciones aplicables a instituciones de crédito) biometrics reform DOF **01-07-2026** | Arts. 51 Bis–51 Bis 5, **Anexo 71 replaced** (fingerprint + facial biometrics) | VERIFIED — https://dof.gob.mx/nota_detalle.php?codigo=5792307&fecha=01/07/2026 |
| Bank DCG Art. 115 LIC consolidated text | Only 2009 original (ABM mirror) and 2022 compilation (gob.mx, blocked by bot challenge) available → bank-specific thresholds/deadlines below are mostly **SECONDARY** | gap |

### A.2.2 Report catalog — Financial entities (CNBV → UIF)

Common mechanics (VERIFIED from ITF DCG + Formato 41 resolution): all reports go "a la Secretaría (UIF), por conducto de la CNBV", transmitted electronically through **SITI PLD/FT** (CNBV "Sistema Interinstitucional de Transferencia de Información", PLD module — https://www.cnbv.gob.mx/Paginas/SITI-PLD.aspx; QA/test environment https://www.cnbv.gob.mx/Paginas/SITI-PLD-QA.aspx). Information "deberá enviarse una sola vez… no podrá ser modificada"; system issues an electronic acuse with user, folio, report type, period, timestamp and record count (Formato 41 Resolution Art. 4). Reports are submitted by the **Oficial de Cumplimiento** (Art. 5), who must first be registered (Anexo 4). Secondary sources mention a CNBV pre-validation tool "ValidaPLD" (SECONDARY — https://kyc-systems.com/blog/reportes-pld-sofom-enr).

| Native report name | Sector(s) | Trigger & threshold | Deadline / periodicity | Channel | Format | Layout | Auth | Zero report? | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| **Reporte de Operaciones Relevantes (ROR)** | Banks, SOFOM ER/ENR, casas de bolsa, casas de cambio, SOCAP/SOFIPO, uniones de crédito, transmisores, centros cambiarios, ITFs | Cash (billetes/monedas MX or foreign; banks also traveler's cheques & gold/silver/platinum coins) **≥ USD 7,500 equiv.** (banks/SOFOMs — SECONDARY); **ITFs: ≥ USD 5,000 equiv.** (ITF DCG Art. 2 def. "Operación Relevante" — VERIFIED). FX at Banxico DOF FIX of prior banking day | **Quarterly: within the first 10 business days of Jan, Apr, Jul, Oct** for the prior 3 months (ITF Art. 66 VERIFIED; banks 34ª SECONDARY) | SITI PLD/FT | TXT, `;`-delimited, **Formato 41** (type 1) — F36 variant for SOFOM ENR per secondary | Formato 41 (see A.2.3) | SITI credentials (CNBV-issued) — exact mechanism UNVERIFIED | **Yes — "reporte vacío"**: only entity id, report type, period fields (ITF Art. 66; Formato 41 Res. Art. 2) | VERIFIED (ITF) / SECONDARY (banks) |
| **Reporte de Operaciones Inusuales (ROI)** | All of the above | Op/conduct inconsistent with client profile etc. (ITF Art. 70 list); Committee (CCC) dictamen within **60 calendar days** of detection | **Within 3 business days after the Comité session** that dictamina it (ITF Art. 69) | SITI PLD/FT | Formato 41 (type 2) incl. related persons rows (col. 34 consecutive "00" = main) and narrative cols. 40-41 | idem | idem | No | VERIFIED (ITF) |
| **ROI de 24 horas** | All | Facts/indications that client attempts op with illicit funds / TF; or accepts such op; or **client in Lista de Personas Bloqueadas** | **≤ 24 hours** from knowledge; insert legend **"Reporte de 24 horas"** (or **"Lista de Personas Bloqueadas"**) in description column | SITI PLD/FT | Formato 41 (type 2) | idem | idem | No | VERIFIED (ITF Arts. 62, 73) |
| **Reporte de Operaciones Internas Preocupantes (ROIP)** | All | Employee/director conduct (lifestyle, repeated involvement in ROIs, suspicion of 139 Quáter/400 Bis CPF, mismatch of functions) | Within 3 business days after Comité session; dictamen ≤ 60 days | SITI PLD/FT | Formato 41 (type 3) | idem | idem | No | VERIFIED (ITF Art. 75) |
| **Reporte de operaciones en efectivo en moneda extranjera** | ITFs (also banks/casas de cambio) | Each op in **foreign-currency cash ≥ USD 500** | Quarterly, first 10 business days of Jan/Apr/Jul/Oct | SITI PLD/FT | UIF format (layout not obtained) | — | — | **Yes** (only ITF id + period) | VERIFIED (ITF Art. 67); layout gap |
| **Reporte de transferencias internacionales de fondos (TIF/FTI)** | Banks, SOFOMs, casas de bolsa, ITFs | Each international transfer sent/received **≥ USD 1,000** | **Monthly, within 15 business days** after last business day of the prior month (ITF Art. 68) | SITI PLD/FT | UIF "Formato Transferencias Internacionales" (layout not obtained; old UIF URL `…/formatos/Formato_Instituciones_de_Credito_TIF.pdf` returns 404) | — | — | **Yes** | VERIFIED (ITF); banks SECONDARY (IMCP bulletins) |
| **Reporte de transmisión de dinero (IFPE)** | IFPE providing money transmission (Art. 81-A Bis LGOAAC) | Each international money transfer ≥ USD 1,000; remittances must include beneficiary data (Art. 103) | Monthly, within 15 business days | SITI PLD/FT | UIF format | — | — | **Yes** | VERIFIED (ITF Arts. 102-103) |
| **Reporte de Operaciones con Activos Virtuales** | ITFs operating with Banxico-authorized VAs | Client buys VA with MXN/FX, or sells VA for MXN, **≥ 7,500 UDIs** | Quarterly, first 10 business days of Jan/Apr/Jul/Oct | SITI PLD/FT | UIF format | — | — | **Yes** | VERIFIED (ITF Art. 74) |
| Reporte de cheques de caja (CHC) | Banks | Each cashier's cheque issued/paid ≥ USD 10,000 equiv. | Per DCG 34ª Ter (periodicity not verified) | SITI PLD/FT | UIF format | — | — | UNVERIFIED | SECONDARY (taxtodaymexico summary of UIF 2022 report) |
| Reporte de operaciones en efectivo con dólares (USD cash) | Banks, casas de cambio, centros cambiarios | USD cash ops ≥ USD 500 (clients), ≥ USD 250 (users) ; centros cambiarios ≥ USD 1,000 | Monthly/quarterly — UNVERIFIED | SITI PLD/FT | UIF format | — | — | UNVERIFIED | SECONDARY |
| Aviso Comité de Comunicación y Control (integration/changes) | ITFs (all FE) | Board designation of committee areas; changes | **15 business days** after designation/change; small ITFs without Committee: notice within 60 business days | CNBV medium "formato oficial" | Formato oficial (UIF) — https://www.gob.mx/cms/uploads/attachment/file/573859/Formato_oficial_para_informar_la_designaci_n_del_OF__la_integraci_n_del_CC_y_C.pdf | — | — | — | VERIFIED (ITF Art. 48) |
| Aviso designación / revocación de **Oficial de Cumplimiento** | ITFs (all FE) | Designation, interim designation (Art. 52), revocation | **10 business days** after the event | CNBV | Formato oficial (link above) | — | — | — | VERIFIED (ITF Art. 53) |
| **Manual de Cumplimiento** (submission) | ITFs (all FE) | Initial manual + every modification (full copy) | Modifications: **within 20 business days** after approval | CNBV medium | Document | — | — | — | VERIFIED (ITF Arts. 82-83) |
| **Informe de auditoría PLD (revisión anual)** | ITFs (all FE) | Annual Jan–Dec effectiveness review by internal audit or independent third party; signer must hold **CNBV PLD/FT certification** | **Send to CNBV within 60 calendar days after year-end (≈ 1 March)**; retain ≥ 5 years | CNBV medium | Report | CNBV guidelines | — | — | VERIFIED (ITF Art. 76) |
| Catálogo de sucursales | All FE using Formato 41 | Branch catalogue; changes ≥ 5 business days before sending reports that reference changed branches | Event-driven | SITI | Fixed fields (clave 8, nombre 50, calle 50, colonia 30, localidad 8, CP 5, estatus 1) | Formato 41 Anexo 3 | — | — | VERIFIED |

Insurance (CNSF; LISF Art. 492 DCG), AFOREs (CONSAR) and casas de bolsa (LMV Art. 212) use the same Formato 41 ROR/ROI/ROIP regime; CNSF/CONSAR filers transmit flat files using the **file-name nomenclature** below (CNBV filers do not, since SITI assigns metadata). Sector-specific thresholds/deadlines for insurers, AFOREs and brokers: UNVERIFIED (not read).

### A.2.3 Format details — Formato 41 (ROR / ROI / ROIP) — VERIFIED (UIF compiled resolution)

- **Encoding/structure:** flat text file, one record per line, **semicolon `;` column separator**; `;` forbidden inside text; empty fields = consecutive `;;` with no spaces; values shorter than max length are not padded (written then terminated by `;`); longer values truncated; **ALL UPPERCASE**. (Character encoding not specified in the resolution — UNVERIFIED; ASCII/ANSI uppercase is safe.)
- **One report type per file** (relevantes, inusuales or preocupantes).
- **41 columns:** 1 tipo de reporte (1=Relevante, 2=Inusual, 3=Preocupante); 2 periodo (AAAAMM for ROR; AAAAMMDD for ROI/ROIP); 3 folio (000001… consecutive per send); 4 órgano supervisor (CASFIM code without hyphen, left-padded to 6, e.g. 40-002 → 040002); 5 clave sujeto obligado (CASFIM, 6); 6 localidad (8); 7 sucursal (8; 0 = head office); 8 tipo de operación (01 depósito, 02 retiro, 03 compra divisas, 04 venta divisas, 05 cheques de caja, 06 giros, 07 órdenes de pago, 08 otorgamiento de crédito, 09 pago de crédito, … 36); 9 instrumento monetario (01 efectivo, 02 documentos/valores, 03 transferencia, 04 cheques de viajero, 05 oro/platino amonedados, 06 plata amonedada, 07 divisas, 08 derechos, 09 bienes); 10 número de cuenta/contrato (16); 11 monto (17: 14 int + "." + 2 dec; zeros if conduct-only report); 12 moneda (3); 13 fecha de la operación (AAAAMMDD); 14 fecha de detección (ROI/ROIP only); 15 nacionalidad (0 unknown — only ROR, 1 MX, 2 foreign); 16 tipo de persona (1 PF, 2 PM); 17 razón social (60); 18 nombre (60); 19 apellido paterno (60; "XXXX" if none); 20 apellido materno (30); 21 RFC (13); 22 CURP (18); 23 fecha nacimiento/constitución (AAAAMMDD) — at least one of 21/22/23 mandatory; 24 domicilio (60); 25 colonia; 26 ciudad/población; 27 teléfono; 28 actividad económica; 29-33 insurance agent / paying money transmitter fields (insurers, sureties, money transmitters only); **34 consecutivo de cuentas/personas relacionadas** (00 = main op, 01..n related); 35 cuenta relacionada; 36 clave sujeto obligado of related account; 37-39 name/surnames of related person; **40 descripción de la operación**; **41 razones por las que se considera inusual/preocupante** (variable length; mandatory for ROI/ROIP). Columns 14 and 34-41 do not apply to ROR.
- **Related parties:** additional rows repeating only cols 1-5 and filling 34-39.
- **File name (CNSF/CONSAR only):** `T` + `CCCCCC` (entity key) + `AAMM` (ROR) or `AAMMDD` (ROI/ROIP) + `.` + `OOO` (supervisor: CNBV 002, CNSF 003, CONSAR 004) → e.g. `10130050412.003`. CNBV/SITI: nomenclature does not apply.
- **Corrections:** reports "no podrán ser modificados" once accepted (Art. 4); correction practice (resubmission of ROI with reference to prior folio) — UNVERIFIED.
- **UIF narrative guidance:** UIF "Guía de mejores prácticas" (e.g. for SOFOMES ENR, June 2021) prescribes structure for cols. 35/40 (priority classification — "Operativa Prioridad Alta / Sin Prioridad / EOPERATIVA / Tentativa / 24 Horas / 24 Horas LPB", KYC summary, product, transactional profile) and 36/41 (alert, analysis, context, actions taken, determination). SECONDARY — https://bhrmx.com/wp-content/uploads/2021/06/Guia-Mejores-Practicas-SOFOMES-.pdf ; https://kyc-systems.com/blog/reportes-pld-sofom-enr . Secondary sources also describe a 36-column "F36" variant for SOFOM ENR (cols 35/36 = narrative) vs 41-column "F41" — UNVERIFIED against a primary layout.
- **API:** none public; SITI web upload by the compliance officer. UNVERIFIED whether SITI offers a machine interface.

---

## A.3 KYC minimums — Mexico

### A.3.1 Financial entities (ITF DCG — VERIFIED; banks — VERIFIED where noted)

| Topic | Rule | Source / confidence |
|---|---|---|
| Individual (MX or resident foreigner) — ITF | Full name (no abbreviations), gender, DOB, state of birth, country of birth, nationality, clave de elector (if any), full address, occupation/activity, **CURP**, **firma autógrafa digitalizada**, phone, email, bank account + **CLABE in the client's name**, self-declaration of acting on own behalf, digital copy of ID; **geolocation of the device used to open the account** | ITF DCG Art. 11 — VERIFIED |
| Legal entity — ITF | Name, business/purpose, nationality, **RFC with homoclave** (+ foreign TIN & countries), **e.firma serial number**, address, phone, email, incorporation date, names of administrators/legal representatives (from official ID), CLABE in entity name, digitized signature of representative; digital copies of incorporation deed (registered), Cédula de Identificación Fiscal & e.firma certificate, proof of address, powers of attorney, IDs | ITF DCG Art. 11 II — VERIFIED |
| VA-specific (ITF) | Per op: VA code & units, MXN equivalent (Banxico FIX of prior banking day), date | ITF DCG Art. 21 — VERIFIED |
| Remote validation (ITF) | Must validate data and verify authenticity of digital documents; may be remote per CNBV rules; low-risk ops may be verified after opening (client cannot operate until done); may outsource to third parties | ITF DCG Art. 23 — VERIFIED |
| Simplified tiers (ITF) | **Nivel 1:** ≤ **750 UDIs/month** in ops, **max balance 1,000 UDIs**; data: name, DOB, gender, state, occupation, email. **Nivel 2:** ≤ **3,000 UDIs/month**; adds address + digital ID document. International-transfer low-risk accounts ≤ 1,700 UDIs/month. Above → full file. UDI value = last day of previous month | ITF DCG Art. 12 — VERIFIED |
| Bank account levels (Banxico Circular 3/2012 + DCG 115 14ª Bis) | **Nivel 1:** 750 UDIs/month deposits, balance ≤ 1,000 UDIs (SECONDARY). **Nivel 2:** ≤ **3,000 UDIs/month** deposits; name, DOB, address from official ID/INM doc (VERIFIED). **Nivel 2 Bis (new, Banxico Circular 11/2026 DOF 17-06-2026; DCG 115 reform DOF 10-07-2026):** Mexican nationals resident in MX; ≤ **15,000 UDIs/month** deposits of which ≤ **3,000 UDIs cash**; full 4ª I data + ID copy; no RFC/e.firma needed; must verify ID & CURP data against issuing authority (7ª); **remote opening requires liveness ("prueba de vida")** (VERIFIED). **Nivel 3:** ≤ 10,000 UDIs/month (SECONDARY). **Nivel 4:** unlimited, full file (SECONDARY). **Remote PM/PFAE accounts ≤ 30,000 UDIs/month** (14ª Bis IV — VERIFIED). UDI ≈ 8.857 MXN (Aug-2026, DOF banner — VERIFIED) | DOF 10-07-2026 — https://dof.gob.mx/nota_detalle.php?codigo=5793357&fecha=10/07/2026 |
| Accepted IDs (banks, 2026 text) | **CURP biométrica (with fingerprints & photo)**, INE voter card (incl. consular-issued), passport, cartilla militar, matrícula consular, military ID, INAPAM, IMSS/ISSSTE/ISSFAM cards, driver's licence, federal/state/municipal credentials; foreigners: passport / INM document / SRE diplomatic card. Proof of address ≤ 3 months (INE/matrícula address suffices) | DCG 115 4ª as reformed 10-07-2026 — VERIFIED |
| Remote ID verification (banks, 7ª) | Verify against issuer: **CURP** (QR, name, 18-char key vs RENAPO), **INE** (CIC or OCR, registration year, clave de elector, issue number/year vs INE), **passport** (OCR, name, number vs SRE), **matrícula consular** (name, issue/expiry, number vs SRE); consent via **e.firma or Firma Electrónica**; earlier (2019) reform allows identification by **real-time videoconference** | DCG 115 7ª — VERIFIED (2026 text); videoconference: recital of same DOF — VERIFIED |
| Biometrics (banks, CUB 51 Bis–51 Bis 5 + **Anexo 71**, DOF 01-07-2026) | In-person Nivel 4 / Nivel 3-4 active ops: online biometric verification with **≥ 90 % match** vs INE / SRE records (fingerprint; now **facial biometrics** allowed); banks may build their own biometric DB (fingerprint + facial) under Anexo 71 technical specs (e.g. face: 2D frontal, 24-bit colour, inter-eye distance ≥ 90 px, preferably 120 px); annual security validation, remediation plan to CNBV within 20 business days; banks already holding biometric DBs must file **Anexo 75 notice within 30 calendar days**; comply within **90 business days** of 02-07-2026 (≈ mid-Nov-2026) | VERIFIED — https://dof.gob.mx/nota_detalle.php?codigo=5792307&fecha=01/07/2026 |
| CURP biométrica dependency | Acceptance/verification of biometric CURP takes effect only when SEGOB/ATDT publish and run the verification processes | DCG 115 reform Transitoria Quinta — VERIFIED |
| UBO / Propietario Real | Natural person who ultimately benefits/owns, incl. persons exercising **Control**; control = directly/indirectly holding **≥ 25 %** of shares/capital (ITF def.); high-risk civil associations: whoever controls regardless of % | ITF DCG Arts. 2, 22 — VERIFIED |
| PEP | Same definition as LFPIORPI RCG (domestic & foreign; incl. international organisations; family to 2nd degree; close associates); national PEPs remain PEP **1 year after leaving office** | ITF DCG Art. 2 XXV — VERIFIED |
| Periodic update | Review/update files **at least annually** (ITF, all risk levels) | ITF DCG Art. 24 — VERIFIED |
| Retention | **≥ 10 years** from op; KYC file for life of relationship + 10 years; copies of all reports and acuses | ITF DCG Art. 25 — VERIFIED |
| Risk assessment | Entity-wide EBR methodology using ≥ 12 months data; update policies within 12 months of results; review within 12 months / on new risks / on new National Risk Assessment; keep 5 years | ITF DCG Arts. 3-7 — VERIFIED |
| Client risk | High risk at least: PEPs (foreign), non-residents etc. (Art. 33); EDD, senior approval (Art. 39) | ITF DCG — VERIFIED (summary) |
| Wire-transfer info (travel rule, banks) | Originator & beneficiary data per 16ª/17ª and Anexo 2 thresholds (USD bands, 360-day accumulation) | DCG 115 — VERIFIED (2026 text partially) |

### A.3.2 Actividades Vulnerables (RCG) — VERIFIED (Acuerdo 115/2026)
- Expediente único de identificación per client (Art. 12, Anexos 3-8 by person type); clients via electronic means must be identified beforehand with documented mechanisms (Art. 18).
- Annual file review; proof of address ≤ 3 months (Art. 21).
- BO: ≥ 25 % → control by other means → senior managing official; trusts: any natural person with effective control; exemptions for listed companies and certain public entities (Arts. 23 Quinquies–23 Quinquies 2). PF clients must declare whether a BO exists (Art. 18 III LFPIORPI).
- Risk classification, KYC policy, transactional profile, alerts, EDD (Caps. III Bis/III Ter) from 01-03-2027.
- Retention 10 years.

---

## A.4 Screening lists — Mexico

| List | Who must screen | Distribution | Action on hit | Confidence |
|---|---|---|---|---|
| **Lista de Personas Bloqueadas (LPB)** (UIF/SHCP) — includes UNSC 1267/1373 designees, foreign-authority/agreement lists, domestic designations | Financial entities (via CNBV); AV (RCG Art. 38) | **Confidential**: SHCP makes it available to financial entities through CNBV (ITF Art. 60) — not public; AV receive it via SAT/UIF mechanisms (UNVERIFIED channel) | **Immediately suspend** all acts/ops/services; **ROI within 24 h** with legend "Lista de Personas Bloqueadas"; notify client in writing (may be by email) of basis and right to appear before UIF within 10 business days (ITF Arts. 62-63). AV: aviso within 24 h (RCG Art. 27) | VERIFIED |
| UN Security Council consolidated list | All (feeds LPB) | Public (un.org); SAT portal page https://sppld.sat.gob.mx/pld/interiores/csnu.html | Via LPB procedure | VERIFIED (ITF Art. 61 I) |
| FATF high-risk / monitored jurisdictions | All (EDD, alerts) | Public (SAT portal https://sppld.sat.gob.mx/pld/interiores/listas.html) | EDD / alerts (RCG Art. 41 V) | VERIFIED |
| Regímenes fiscales preferentes (LISR) | All (alerts) | Public (LISR / SAT) | Alerts/EDD | VERIFIED (RCG Art. 41 V) |
| **PEP list — "Consulta PEP 2.0"** (UIF, LFPIORPI Art. 51 Ter) | AV (with e.firma); financial entities on request | Restricted application on UIF site | Classify/EDD | VERIFIED (RCG Art. 23 Quáter 1); go-live ~Aug-2027 |
| SAT Art. 69-B CFF lists (EFOS/EDOS), OFAC SDN | Not mandated by AML rules; market practice | Public | Risk factor | UNVERIFIED (practice) |

---

## A.5 Other recurring obligations a platform could automate — Mexico

| Obligation | Sector | Deadline / periodicity | Confidence |
|---|---|---|---|
| Monthly avisos / informe en ceros | AV | Day 17 of following month (or assigned calendar day) | VERIFIED |
| Quarterly ROR, USD-cash and VA reports (incl. empty reports) | FE / ITF | First 10 business days of Jan, Apr, Jul, Oct | VERIFIED (ITF) |
| Monthly international transfer report | FE / ITF / IFPE | 15 business days after month-end | VERIFIED (ITF) |
| ROI / ROIP | FE | 3 business days after Comité; Comité ≤ 60 days after detection; Comité meets at least monthly (SECONDARY) | VERIFIED (ITF) |
| Annual entity risk assessment (EBR) | FE (ITF Arts. 3-6); AV (from 01-03-2027) | Review ≤ 12 months / on new NRA | VERIFIED |
| Annual PLD audit report | FE: to CNBV within 60 days after year-end; AV: dictamen by last business day of March (not filed), first period 2028 | VERIFIED |
| Annual training + evaluations + certificates | FE (ITF Art. 54-55), AV (from 2027) | Annual | VERIFIED |
| Compliance-officer / Comité notices | FE: OC 10 business days; Comité 15 business days. AV: REC designation/acceptance in SPPLD | VERIFIED |
| Manual de cumplimiento updates | FE: send to CNBV within 20 business days after approval. AV: keep available (90 days after alta) | VERIFIED |
| CNBV PLD/FT certification of OC & auditors | FE (auditor signing must be certified — ITF Art. 76); AV optional UIF certification (5-year validity) | VERIFIED |
| Annual KYC file review | FE & AV | Annual | VERIFIED |
| Biometric DB annual security validation | Banks (CUB 51 Bis 2) | Annual; remediation to CNBV in 20 business days | VERIFIED |
| VASP (fr. XVI) registration update | AV VASPs | Within 6 months of 30-11-2026 | VERIFIED |

---

## A.6 Recent / upcoming changes — Mexico (2025-2027)

| Date | Change | Confidence |
|---|---|---|
| 17-07-2025 | LFPIORPI reform in force (BO ≥25 %, 24-h suspicion avisos, EBR, manual, training, audit, automated monitoring, 10-yr retention, VASP scope incl. custody & cross-border, fideicomisos, Desarrollo Inmobiliario V Bis, facilitadores XII-D, SE BO registry Arts. 33 Bis-Ter, PEP list Art. 51 Ter) | VERIFIED |
| 27-03-2026 | Reglamento LFPIORPI reform (Art. 7 Bis aviso, Art. 14) | VERIFIED (recital) |
| 17-06-2026 | Banxico Circular 11/2026 — new deposit account **Nivel 2 Bis** | VERIFIED (recital in DOF 10-07-2026) |
| 02-07-2026 | CUB biometrics reform (facial biometrics, Anexo 71 replaced); 30-day Anexo 75 notice; full compliance within 90 business days (~Nov-2026) | VERIFIED |
| 11-07-2026 | DCG 115 LIC reform (Nivel 2 Bis, ID list incl. biometric CURP, remote verification); **18 months** to adapt (~Jan-2028); Nivel 2 Bis liveness within 18 months then 6 months to remediate files | VERIFIED |
| 07-08-2026 | Acuerdo 115/2026 (RCG) published | VERIFIED |
| 30-11-2026 | RCG reform in force | VERIFIED |
| 01-12-2026 | Formats resolution in force for purposes of 24-h aviso transitional (Transitorio Quinto) | VERIFIED |
| 01-03-2027 | AV: EBR evaluation available; manual with methodology; client risk/KYC/BO chapters apply; personnel selection | VERIFIED |
| ~30-05-2027 | AV VASPs: update registration info (6 months after 30-11-2026) | VERIFIED (computed) |
| **01-06-2027** | **New AV XML formats mandatory** (all Anexos 1-16, 12-C, 14-A; informe en ceros via Anexo 14); 24-h avisos (sospecha/indicios/LPB) can be filed; automated mechanisms required | VERIFIED |
| 30-06-2027 / 01-07-2027 | Last day for old-format modificatorios / old formats removed | VERIFIED |
| ~Jul/Aug-2027 | SAT electronic notifications (8 months); Consulta PEP 2.0 for AV & FE (9 months ≈ 30-08-2027) | VERIFIED (computed) |
| 01-01-2027 → 31-12-2027 | First annual AV training period | VERIFIED |
| 01-01-2028 → 31-12-2028 | First AV audit period (dictamen by 31-03-2029) | VERIFIED |
| TBD | SE beneficial-ownership registry guidelines; alta/registro new format effective date (30-11-2026 vs 01-02-2027 per secondary) | UNVERIFIED |

---

## A.7 Gaps — Mexico
1. **New per-activity XSDs** (`avi2.xsd` etc., effective 01-06-2027) are linked on the SAT portal but returned an HTML error on 2026-10-17; only the field tables in the DOF are available. Re-download before building the 2027 generator.
2. Catalogs (tipo_alerta, activo_virtual_operado, red_transferencia, instrumento_monetario, países) — published by UIF via the portal (Resolución Art. 9); not downloaded.
3. Bank DCG Art. 115 LIC consolidated text (2022 compilation behind gob.mx bot challenge): bank ROR threshold (USD 7,500), cheques de caja, USD-cash report periodicities are SECONDARY.
4. Layouts for USD-cash, international-transfer, VA (ITF) and IFPE transmission reports not obtained (UIF format library URLs 404 / gob.mx blocked).
5. SITI PLD/FT authentication mechanism (certificate vs user/password) and whether any API exists — UNVERIFIED.
6. Insurance (CNSF), AFORE (CONSAR), casas de bolsa, casas de cambio, transmisores, centros cambiarios, SOCAP/SOFIPO DCG — not read; assumed Formato 41 regime.
7. LPB distribution channel for AV — UNVERIFIED.
8. Semantics of `prioridad` (1/2) in AV XML — UNVERIFIED.
9. Whether ITF DCG were amended after the gob.mx compilation (e.g. post-2025) — UNVERIFIED.


---

# PART B — BRAZIL

## B.0 Legal architecture

- **Lei 9.613/1998** (planalto, VERIFIED — https://www.planalto.gov.br/ccivil_03/leis/l9613.htm): Art. 9 lists obliged persons (incl. **XIX — prestadoras de serviços de ativos virtuais**, added by Lei 14.478/2022); Art. 10: KYC, records of transactions (now incl. **ativos virtuais**) above limits set by the competent authority, internal controls, **registration with regulator or, failing one, with Coaf**, retention **≥ 5 years** (authority may extend — BCB extends to 10); Art. 11 II: communicate to **Coaf within 24 hours**, without tipping off, (a) transactions above the authority's threshold and (b) suspicious proposals/operations; **Art. 11 III: communicate "não ocorrência" to the regulator (or Coaf) per their rules**; Art. 11-A: international transfers and cash withdrawals must be pre-notified to the FI per BCB rules.
- **Every sector's regulator** sets thresholds/deadlines; **all communications go to Coaf through Siscoaf**, by sector "segmento". Coaf's official map of regulators and norms in force (VERIFIED, page modified 10-08-2026 — https://www.gov.br/coaf/pt-br/centrais-de-conteudo/legislacao-e-normass/normas-de-pld-ftp-de-outros-supervisores):

| Regulator | Obliged sector | Norm in force |
|---|---|---|
| BCB | Banks, payment institutions, FX brokers, DTVM/CTVM, **SPSAV (VASPs)**, cooperatives, consórcio administrators | **Circular BCB 3.978/2020** (+ Res. BCB 119/2021, 282/2022, 344/2023, **591/2026**); Carta Circular 4.001/2020 (+ IN BCB 461/2024, IN BCB 732/2026); Res. BCB 44/2020 (UN sanctions) |
| CVM | Securities market participants | **Res. CVM 50/2021** (amended by Res. CVM 179/2023 and **245/2026**) |
| SUSEP | Insurers, capitalização, open pension, reinsurers (and brokers) | **Circular SUSEP 612/2020**, Circular SUSEP 622/2021 |
| PREVIC | Closed pension funds | Res. PREVIC 23/2023, 25/2024 |
| ANS | Health plan operators | RN 529/2022 |
| Coaf (direct supervision) | Jewelry/precious metals (Res. 23/2012), luxury/high-value goods (Res. 25/2013), factoring (Res. 41/2022), athlete/artist rights (Res. 30/2018); general PLD/FT controls Res. 36/2021; PEP Res. 40/2021; UN sanctions Res. 31/2019; Coaf registration IN 5/2020; **IN Coaf 1/2026 (EDD for FATF-listed jurisdictions, in force 03-08-2026)** | |
| COFECI | Real-estate brokers/promoters | Res. COFECI 1.336/2014 |
| CFC | Accountants | Res. CFC 1.721/2024 (in force 03-06-2024) |
| COFECON | Economists | Res. COFECON 1.902/2013 |
| CNJ | Notaries & registrars | Provimento CNJ 161/2024 |
| DREI | Juntas Comerciais | IN DREI 76/2020 |
| IPHAN | Art & antiques dealers | Portaria IPHAN 396/2016 |
| Polícia Federal | Cash-in-transit/guard companies | IN 196-DG/PF/2021 |
| SPA/MF (betting) | Fixed-odds betting (federal), lotteries | Portaria SPA/MF 1.143/2024; Portaria MF 537/2013 |
| State lotteries (Lottopar, Lotep, Loterj, Loto Potiguar, AGRESE-SE) | State-licensed betting | respective portarias (2025-2026) |
| ANM | Precious-metal/stone mining | Res. ANM 129/2023 |

---

## B.1 Report catalog — Brazil

All filed via **Siscoaf** (https://siscoaf.coaf.gov.br) unless noted. Access: **mandatory digital certificate (ICP-Brasil e-CPF) + gov.br login + 2FA (gov.br app)** for portal; webservice calls must be **signed with an e-CPF** of an enabled user (VERIFIED — Comunicado Siscoaf 101 https://www.gov.br/coaf/pt-br/sistemas/comunicado-siscoaf-101-lottopar-apostas-de-quota-fixa.pdf/@@download/file ; https://www.gov.br/coaf/pt-br/sistemas/siscoaf/integracao-e-webservice). Institution must first be **habilitada** in Siscoaf (Circ. 3.978 Art. 55).

| Native report name | Sector(s) | Trigger & threshold | Deadline | Channel | Format | Layout / spec | Auth | Zero report? | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| **Comunicação de operação/situação suspeita** (commonly "COS") | BCB-regulated (banks, IPs, FX, SPSAV, etc.) | Any op/situation/proposal with indications of ML/TF (CC 4.001 list as guide); analysis must conclude **≤ 45 days from selection**; decision documented in dossier | **By the business day following the decision to communicate** (Circ. 3.978 Art. 48 §2) | Siscoaf | Web form, **XML lote** upload, or **SOAP webservice** | Manual + "instruções de envio em lote" inside Siscoaf per segment/model | e-CPF (+gov.br 2FA for portal) | see CNO | VERIFIED |
| **Comunicação de operação em espécie / automática** (commonly "COE") — Art. 49 | BCB-regulated | (I) cash deposit/aporte or withdrawal **≥ R$ 50,000**; (II) payments/receipts/transfers against cash **≥ R$ 50,000**; (III) cash-withdrawal **provisioning request ≥ R$ 50,000**; **(IV) FX ops with delivery/receipt of foreign cash ≥ US$ 10,000 (or equiv.)**; **(V) virtual-asset transfers to/from self-hosted wallets ≥ US$ 10,000 equiv.** — reported **regardless of analysis** | **By the business day following the operation/provisioning**; **items IV & V contracted 01-10-2026 → 31-12-2026 must be reported on the first business day of January 2027** | Siscoaf | idem | idem | e-CPF | — | VERIFIED — Circ. 3.978 Art. 49 as amended by **Res. BCB 591/2026** (https://www.bcb.gov.br/estabilidadefinanceira/exibenormativo?tipo=Resolu%C3%A7%C3%A3o%20BCB&numero=591) |
| Comunicação de não ocorrência (CNO) | BCB-regulated | No communications made in the calendar year | **Within 10 business days after year-end** | Siscoaf ("Comunicação de Não Ocorrência" menu) | Web form | Siscoaf manual §5.1.4 | e-CPF | **is** the zero report | VERIFIED (Circ. 3.978 Art. 54) |
| Comunicação de indisponibilidade de ativos (UN sanctions) | All BCB-regulated | Asset freeze applied / attempted transfer re UNSC-sanctioned persons | **Immediately**: to BCB (**BC Correio**), Ministério da Justiça e Segurança Pública, and Coaf (via Siscoaf, as a communication) | BC Correio + Siscoaf + MJSP | — | Res. BCB 44/2020 | — | — | VERIFIED |
| Retificação / cancelamento | All Siscoaf filers | Correct or cancel a sent communication | Any time; **if altered/cancelled after the 5th business day, must include justification** (BCB) | Siscoaf (form "Retificador", lote, or WS `RetificarLoteComunicacao`/`CancelarLoteComunicacao`) | Same XML | Siscoaf manual | e-CPF | — | VERIFIED (Circ. 3.978 Art. 51; manual) |
| **Comunicação de operação suspeita (CVM)** | Securities market (Res. CVM 50 Art. 3 I-IV) | Situations/operations/proposals that may be serious ML/TF indications; content minimum: relationship start date, reasoned red flags, op details, diligence info incl. PEP status, conclusion | **Within 24 h of concluding the analysis** | Siscoaf | Siscoaf | — | e-CPF | — | VERIFIED (Res. CVM 50 Art. 22) |
| CNO (CVM) | Securities | No reportable events in prior calendar year | **By last business day of April** | Siscoaf (CVM–Coaf convênio) | Web form | — | e-CPF | yes | VERIFIED (Art. 23) |
| Sanctions freeze notice (CVM) | Securities | Inability to freeze immediately | Without delay to MJSP and CVM | — | — | Res. CVM 50 Art. 27 | — | — | VERIFIED |
| Comunicação (SUSEP) | Insurers, capitalização, open pension, brokers | Suspicious; automatic for premiums/contributions/capitalização purchases **paid in cash ≥ R$ 10,000** (secondary) | Next business day (UNVERIFIED) | Siscoaf | — | Circ. SUSEP 612/2020 | e-CPF | CNO **by last business day of March** (SECONDARY — Demarest https://www.demarest.com.br/?p=134620) | SECONDARY |
| Comunicação — factoring (Coaf) | Factoring | Suspicious (monitoring ≤ 45 days + analysis ≤ 45 days); automatic: **cash ≥ R$ 50,000** or bearer cheque/title ≥ R$ 50,000 | Suspicious: next business day after analysis; automatic: **next business day after the operation** | Siscoaf | — | Res. Coaf 41/2022 Arts. 20-25 | e-CPF | **CNO by 31 January** (Art. 29) | VERIFIED |
| Comunicação — luxury/high-value goods (Coaf) | Dealers of goods ≥ R$ 10,000 | **Cash ≥ R$ 30,000 per client in any 6-month period** (automatic, no analysis); plus suspicious ops | Within 24 h (Lei 9.613 Art. 11) | Siscoaf | — | Res. Coaf 25/2013 Arts. 4-6 | e-CPF | CNO by 31 Jan (SECONDARY) | VERIFIED (threshold) |
| Comunicação — jewelry/precious metals (Coaf) | Jewelry, gems, precious metals | Similar cash threshold (R$ 30,000 — UNVERIFIED) + suspicious | 24 h | Siscoaf | — | Res. Coaf 23/2012 | e-CPF | CNO by 31 Jan (SECONDARY) | SECONDARY |
| Comunicações — accountants, real estate, notaries, betting, others | per sector norm | per sector norm (e.g., CFC 1.721/2024; COFECI 1.336/2014; Prov. CNJ 161/2024; Portaria SPA/MF 1.143/2024) | per norm; CNO for accountants by 31 Jan (SECONDARY) | Siscoaf (segment per regulator) | — | — | e-CPF | yes (SECONDARY) | not read — UNVERIFIED details |
| Cadastro no Coaf / regulator | Coaf-supervised (and those without regulator) | Before operating; keep updated | IN Coaf 5/2020 | Siscoaf | Web | — | e-CPF | — | VERIFIED (existence) |

### B.1.1 Format details — Siscoaf (VERIFIED where marked)

- **Three submission modes** (Comunicado Siscoaf 101 — VERIFIED): (i) individual web form, (ii) **batch (lote) XML upload**, (iii) **automated SOAP webservice**.
- **Webservice (VERIFIED by downloading the public WSDL):** SOAP 1.1, document/literal; namespace `http://www.gov.br/coaf/`; endpoints — production `https://siscoaf.coaf.gov.br/siscoaf-internet/ComunicacaoService`, training/homologation `https://treina.siscoaf.coaf.gov.br/siscoaf-internet/ComunicacaoService` (`?wsdl` publicly retrievable). Operations: `EnviarLoteComunicacao(lote:string)`, `RetificarLoteComunicacao(lote)`, `CancelarLoteComunicacao(lote)`, `ReciboLoteComunicacao(lote)`, `SolicitaReciboEnvio(lote)`, `SolicitaProtocoloComunicacao(protocolo)` — each takes/returns an **XML document serialized as a string**. Calls must be **signed with an ICP-Brasil e-CPF** certificate of a user enabled for the entity.
- **Batch/lote XML:** structure is **segment- and model-specific** (segment = regulator norm, e.g. "BCB – Circular 3.978"; model = type, e.g. suspicious vs automatic). The integration manual, **occurrence-code table ("Código Ocorrências" = enquadramentos)** with per-code validation rules, and **involvement-type codes ("Tipo de Envolvimento")** are only shown inside Siscoaf after login (Siscoaf operational manual v3.5.1 §5.1.3 — VERIFIED: https://www.gov.br/coaf/pt-br/sistemas/siscoaf/manuais-siscoaf/manual-siscoaf2-operacional-v3-5-1.pdf/@@display-file/file). Old public copies describe root `LOTE` → `OCORRENCIAS` → `OCORRENCIA` (max **500 per file**), case-sensitive tags, **max 1 MB per file** — SECONDARY/possibly outdated.
- **Key fields of a communication (manual — VERIFIED):** Tipo de Comunicação (`Normal` | `Retificador` — retification requires the **Número Coaf** of the original and the **same Número de Origem**, then the full communication is resent; wrong refs → error code 32 "Comunicação não encontrada na base"); **Número de Origem** (filer-controlled unique id, e.g. sequential); data inicial/final do fato; agency number/name; city/UF; values — **Valor de Crédito, Valor de Débito, Valor de Provisionamento, Valor da Proposta** (**integers, no cents, no commas/points**); **Envolvidos** (CPF/CNPJ or "Pessoa sem CPF/CNPJ" with document details in Informações Adicionais; Nome; **Tipo de Envolvimento** — segment-specific, e.g. at least one "Titular" for SFN atípica; **PEP flag**; **Pessoa Obrigada flag**; **Servidor Público** = Não/Sim/SPF/SPE/SPM; agency/account; city/UF); **Enquadramentos** (one or more occurrence codes from the segment table, each with validation rules on other fields); **Informações Adicionais** (free-text narrative — the core of the STR).
- **Processing:** communication processed within **24 h**; result in "Lotes Enviados" (download submitted XML and **error XML** with codes); rejected communications must be corrected and resent; "Validar Arquivo" dry-run available; valid communications in a lote are accepted even if others fail.
- **Cancellation:** requires Número Coaf + Autenticação + detailed reason.
- **Art. 53 Circ. 3.978 content flags:** specify whether subject is PEP/relative/associate; known terrorist; holds/controls funds at the institution (for TF).
- **CVM content minimum:** Res. CVM 50 Art. 22 §1 (see table).

---

## B.2 KYC minimums — Brazil

| Topic | BCB (Circ. 3.978) | CVM (Res. 50) | Coaf-supervised (Res. 36/2021, 40/2021) | Confidence |
|---|---|---|---|---|
| Identification — natural person | **Full name + CPF** (address moved to qualification by Res. BCB 119/2021); non-residents without CPF: travel document (issuing country, number, type) | Anexo B detailed cadastro (name, DOB, nationality, CPF, ID doc, address, phone, email, occupation, income/patrimony, PEP, dates of updates, etc.) | Sector minimum datasets | VERIFIED |
| Identification — legal person | **Name + CNPJ** (foreign: name, address, home-country registration no.) | Anexo B (incl. avg monthly revenue of last 12 months, constitutive docs) | idem | VERIFIED |
| Qualification | Residence location (PF) / head office or branch (PJ); **financial capacity: income (PF) / revenue (PJ)**; verification proportional to risk; permanent re-evaluation | Continuous; cadastro update criteria with **max interval 5 years** | compatibility of capacity vs op | VERIFIED |
| Validation / remote | Must verify & validate identity, incl. confronting public/private databases (Art. 16 §1) — technology-neutral; no prescribed biometrics | "sistemas alternativos de cadastro, inclusive por meio eletrônico" allowed (Art. 12) | must include non-face-to-face (Art. 7 §3) | VERIFIED |
| Business relationship start | **Prohibited** before identification & qualification complete (Art. 23); exemption for federal debt-renegotiation programmes (Art. 23-A, Res. 344/2023) | Art. 18 | Art. 10 | VERIFIED |
| **UBO (beneficiário final)** | Look through ownership chain to natural person; institution sets risk-based reference threshold **≤ 25 %** (direct + indirect), documented; representatives with de-facto command are BOs; exemptions: listed cos, non-profits, cooperatives, certain CVM funds, certain non-resident investors (Res. 119) | **> 25 %** or de-facto influence (Art. 2; Art. 13 threshold ≤ 25 %) | ≤ 25 % reference (Res. 36 Art. 9) | VERIFIED |
| **PEP** | Domestic list by office (federal elected, ministers, DAS-6 or equiv., superior courts, PGR, TCU, party presidents/treasurers, governors/state secretaries/deputies, mayors/councillors/municipal secretaries…); foreign heads of state/government, senior politicians/officials/judiciary/military generals, SOE executives, party leaders; heads of international organisations. **Family: relatives in direct or collateral line up to 2nd degree, spouse/partner, stepchildren. Close associates** defined. **PEP status lasts 5 years** after leaving office. Senior approval for relationship. Non-residents: ≥2 of declaration / public info / PEP databases | Anexo A; **5 years** | Res. Coaf 40/2021; 5 years | VERIFIED (BCB, CVM) |
| Risk classification | Per internal risk assessment categories (Art. 20) | Art. 5 | Art. 7 §5 | VERIFIED |
| Cash records | Cash ops > R$ 2,000: record carrier name + CPF; **cash deposits ≥ R$ 50,000**: owner, carrier, **origin of funds**; **withdrawals ≥ R$ 50,000**: recipient, carrier, purpose, protocol; **provisioning request ≥ 3 business days in advance for withdrawals ≥ R$ 50,000**; boletos paid in cash: specific record | — | — | VERIFIED |
| Wire/transfer data | Art. 30 (originator/beneficiary data for transfers); **VASP travel rule** (Res. BCB 520 Art. 44): originator name, account id, address, CPF/CNPJ or foreign id, wallet; beneficiary name, account id, wallet. **Phase I (domestic VASP↔VASP) by 02-02-2027; Phase II (international) by 02-02-2028; mandatory for all authorised SPSAVs from 02-02-2028**; until then self-declarations allowed | — | — | VERIFIED |
| Retention | **10 years** (KYC from first day of year after relationship ends; records from first day of year after op; dossiers); policy docs 5 yrs | **5 years** (from cadastro/last update/detection; CVM may extend) | **5 years** | VERIFIED |
| Employee/partner KYC | Required (Art. 56-58) | — | Res. 36 Arts. 11-12 | VERIFIED |

---

## B.3 Screening lists — Brazil

| List | Who | Distribution | Obligation | Confidence |
|---|---|---|---|---|
| **UN Security Council sanctions (Lei 13.810/2019)** incl. committee designations | All obliged persons | Public (UN site). BCB also notifies via **BC Correio**, but institutions must **monitor directly** and act regardless of BCB notice | **Freeze immediately, without prior notice** to the sanctioned party; prohibition to transfer/convert/dispose (incl. interest/fruits — CVM); **immediately** notify BCB (BC Correio), MJSP and Coaf (Siscoaf); notify MJSP without delay when unable to comply; also comply with judicial freeze orders and national designations | VERIFIED (Res. BCB 44/2020; Res. CVM 50 Art. 27; Res. Coaf 31/2019) |
| Sanctioned VA wallet addresses (domestic/foreign) | SPSAVs | Market tools/oracles | Controls to identify sanctioned wallets (Res. BCB 520 art. on outsourced custody, item III) | VERIFIED (requirement); source lists UNVERIFIED |
| FATF high-risk / increased-monitoring lists | All | FATF; **Coaf publishes via Siscoaf "Informativo"** | EDD (IN Coaf 1/2026; Res. CVM 245/2026 new Art. 17-A; Res. BCB 520 references FATF lists) | VERIFIED |
| PEP lists | All | CGU Portal da Transparência PEP dataset (public download) and Siscoaf PEP consultation | Classification/EDD | SECONDARY / UNVERIFIED |
| Unauthorised VASPs | BCB-regulated FIs/IPs | BCB authorisation register | **From 06-11-2026 FIs/IPs may not transact with VASPs not authorised by BCB** (Res. BCB 520 Art. 91 as amended by Res. 589/2026) | VERIFIED |
| Unauthorised fixed-odds betting operators | BCB-regulated | per MP 1.394/2026 | Prohibition to process/settle transactions (Res. BCB 590/2026, DOU 30-09-2026) | VERIFIED (existence) |

---

## B.4 Other recurring obligations a platform could automate — Brazil

| Obligation | Sector | Deadline / periodicity | Confidence |
|---|---|---|---|
| Designate **director responsible** for PLD/FT to BCB | BCB | On designation (Circ. 3.978 Art. 9) | VERIFIED |
| Internal risk assessment (avaliação interna de risco) | BCB, Coaf-supervised, CVM | BCB: documented, director-approved, sent to risk/audit committees & board, **reviewed every 2 years** or on significant change. Coaf-supervised: every 2 years. CVM: annual risk report by **last business day of April** | VERIFIED |
| **Relatório de avaliação de efetividade** | BCB | Annual, data-base 31-Dec, to audit committee & board **by 31 March**; kept 5 years | VERIFIED |
| **Plano de ação + relatório de acompanhamento** | BCB | **By 30 June** of following year | VERIFIED |
| CNO | All | BCB: **10 business days after year-end** (~mid-Jan). CVM: **last business day of April**. Coaf-supervised (factoring etc.): **31 January**. SUSEP: last business day of March (SECONDARY) | VERIFIED / SECONDARY |
| Monitoring → analysis SLA | BCB, factoring | BCB analysis ≤ 45 days from selection; Coaf factoring: selection ≤ 45 days from occurrence + analysis ≤ 45 days | VERIFIED |
| Manuals (KYC, monitoring), employee KYC doc, policy | All | Approved by board/diretoria; kept available (not filed) | VERIFIED |
| Siscoaf habilitação, user management, Coaf cadastro updates | All | Ongoing | VERIFIED |
| VASP authorisation and supervisory feeds | SPSAV | Authorisation request within 270 days of 02-02-2026 (≈ 30-10-2026); from protocol: CCS client data, balances, custody totals, monthly proof-of-reserves, staking totals (Res. 520 Art. 88 / Res. 589 from 01-01-2027) | VERIFIED |
| Training | All | Required by policy (Res. Coaf 36 Art. 2 §1 I f; BCB policy) — no fixed frequency | VERIFIED |

---

## B.5 Recent / upcoming changes — Brazil (2025-2027)

| Date | Change | Confidence |
|---|---|---|
| 10/11-11-2025 | Res. BCB **519** (authorisation processes incl. SPSAV), **520** (SPSAV constitution/operation; travel rule Art. 44), **521** (VASP activities in FX market; amends Res. 277/278/279) — published DOU 11-11-2025; **in force 02-02-2026** (521 has staged dates 02-02-2026 and 04-05-2026) | VERIFIED |
| 05-05-2026 | IN BCB 732/2026 adds CC 4.001 indicator: attempted cash provisioning/withdrawal of **parliamentary-amendment funds** | VERIFIED |
| 01-07-2026 | Res. CVM 245/2026 amends Res. CVM 50 (continuous & reinforced monitoring; new Art. 17-A EDD for FATF-listed jurisdictions) | VERIFIED |
| 03-08-2026 | IN Coaf 1/2026 in force — EDD for FATF high-risk jurisdictions for Coaf-supervised entities | VERIFIED |
| 23/25-09-2026 | **Res. BCB 588/2026** (art. 49 IV & V) — **REVOKED** by Res. BCB 591 before taking effect | VERIFIED |
| **30-09/01-10-2026** | **Res. BCB 591/2026** (DOU extra 01-10-2026, in force on publication): automatic Coaf communication of **FX cash ≥ US$ 10k** and **VA transfers to/from self-custody wallets ≥ US$ 10k**; ops from 01-10-2026 to 31-12-2026 reported on **first business day of January 2027** (≈ 04-01-2027) | VERIFIED |
| 01-10-2026 / 01-01-2027 | Res. BCB 589/2026 amends Res. 520 (Art. 91 cut-off moved to 06-11-2026; reporting feeds from 01-01-2027) | VERIFIED |
| 30-09-2026 | Res. BCB 590/2026 — ban on processing payments for unauthorised fixed-odds betting (MP 1.394/2026) | VERIFIED |
| ≈ 30-10-2026 | Deadline for existing VASPs to request BCB authorisation (270 days from 02-02-2026); non-filers must cease within 30 days after | VERIFIED (computed) |
| **06-11-2026** | FIs/IPs barred from dealing with non-authorised VASPs | VERIFIED |
| 02-02-2027 | Travel rule Phase I (domestic) | VERIFIED |
| 02-02-2028 | Travel rule Phase II (international); full mandatory compliance | VERIFIED |

---

## B.6 Gaps — Brazil
1. **Siscoaf batch XML schema / integration manual** is only available inside Siscoaf after authentication; the per-segment **occurrence-code tables** for the BCB segment (including any new codes for Art. 49 IV/V under Res. 591) were not obtained. Public WSDL obtained (payload is an XML string).
2. Whether Siscoaf now limits file size (1 MB) and items per lote (500) — only from outdated secondary copies.
3. SUSEP Circular 612/2020: thresholds, deadlines and CNO date are SECONDARY.
4. Coaf Res. 23/2012 (jewelry) cash threshold; CNO deadlines for Coaf-supervised sectors other than factoring (31 Jan generally reported by secondary sources).
5. CFC, COFECI, CNJ Prov. 161/2024, SPA/MF 1.143/2024 and state-lottery reporting specifics not read.
6. CMN Res. 4.753/2019 & BCB Res. 96/2021 remote-account-opening specifics not read in detail (BR KYC is technology-neutral).
7. PEP list sources (CGU dataset / Siscoaf PEP consultation) — UNVERIFIED.
8. A reported 2026 Coaf norm on confidentiality ("novas regras de sigilo", press) — not verified.
9. Exact Siscoaf "segmento"/"modelo" names for BCB COS vs COE (the acronyms are market usage).

