# Tech research: IDV, KYB, registry access and vendor landscape (LATAM)

**Date:** 2026-10-17
**Scope:** Customer onboarding (KYC) and business onboarding (KYB) for a multi-tenant KYC/AML SaaS. Launch in MX, then BR, CO, AR, CL, PE.
**Evidence tags:**
- **VERIFIED**: I read the primary source (government site, DOF, regulator, or vendor's own docs for vendor claims).
- **SECONDARY**: law-firm, press, or vendor-blog summary of a primary source.
- **UNVERIFIED**: single weak source, inferred, or from memory and not confirmed this session.

> **Prices.** Government tariffs are only quoted when published. Vendor prices are only quoted from a public pricing page. Where neither exists, the doc says "not public".

---

## A. Government registry access paths

### A.1 Mexico (MX)

| Registry / service | What it offers | Who can access | API / channel | Latency and availability | Cost | Vendors that resell or broker access |
|---|---|---|---|---|---|---|
| **INE: Servicio de Verificación de Datos de la Credencial para Votar** | Matches CIC, OCR, issue number, clave de elector, names, registration year and CURP. Also does a **fingerprint** match (returns true/false plus a fingerprint similarity %). A **facial** verification variant (photo vs. padrón) has existed since about 2023. | Public or private institutions **with a signed Convenio de Apoyo y Colaboración** with INE (DERFE). INE's published list is mostly banks and SOFIPOs (ABC, Actinver, Banco del Bienestar, Compartamos, Monex, Scotiabank, Sabadell, Volkswagen Bank, etc.). The end user must consent. Legal basis: INE/CG92/2016 and INE/CG91/2020. | Signed and encrypted online request (institution encrypts with INE's public key). Convenio holders usually integrate through a technology provider. | INE states **5–7 s per online query** | Not public. Set per convenio. | **NA-AT Technologies (naat.tech)** says it holds a convenio for INE Verificación Facial and runs its own infrastructure housed at INE. **Veridas** says its biometric engine runs on INE servers. Many IDV vendors (Truora, MetaMap/Incode, Jaak, Mati, Nubarium) only offer a **data-only "Lista Nominal" check** through the public listanominal.ine.mx query. That check is not biometric. |
| **RENAPO: CURP** | CURP exists and is current (vigente), and personal data matches BDNCURP. **CURP Biométrica** (Cédula Única with photo and fingerprints) comes from the July 2025 reform to the Ley General de Población. Enrollment is in progress through 2026. | RENAPO provides CURP **web services under coordination agreements** (for example DOF 2026-07-20, SEP Guanajuato: online query, vigencia check, data validation). The Reglamento LGP allows agreements with private institutions. Private access in practice is through convenios or through vendors. There is a free public web lookup at gob.mx/curp (captcha-protected). | SOAP or REST web service for convenio holders. Vendors mostly scrape or proxy the gob.mx/curp lookup. | The public lookup is often slow or captcha-gated. Vendor proxies typically take 1–5 s and have periodic outages. | Free to citizens. B2B cost not public. | Nubarium, Truora, MetaMap/Incode, Jaak, Moffin, Kiban, Veridas (CURP validation) |
| **SAT: RFC / e.firma** | Checks that an RFC is registered and can receive CFDI ("Validador de RFC" in the SAT portal, plus bulk validation of up to 5k RFCs in a TXT file through the SAT portal with e.firma). Certificate (CSD / e.firma) status is published through SAT's certificate-recovery service and OCSP. | The portal is public. Bulk validation requires the requester's e.firma. There is **no documented open REST API**. | Portal or e.firma. Vendors wrap it. For the **Constancia de Situación Fiscal** (CSF), the customer uploads a PDF and the vendor parses its QR, which links to a SAT validation URL. | Varies. SAT portal outages are common at month-end. | Free | Nubarium, Kiban, Moffin, Syntage (formerly Satws), Palenca, Belvo (SAT data with taxpayer CIEC/e.firma consent) |
| **SRE: Passport and matrícula consular** | Since 2023, banks can check passport and matrícula validity in real time **through a SRE–Banxico agreement**. SRE matches data and biometrics without returning personal data. CNBV's new Anexo 71 lists SRE as an official source for the facial 1:1 match. | Banks (credit institutions) through Banxico. Not open to fintechs directly. | Banxico-intermediated system | Not public | Not public | None confirmed. Facephi and Veridas advertise "INE/SRE" matching for banks. |
| **SIGER 2.0 / Registro Público de Comercio (RPC)** | Folio Mercantil Electrónico (FME) per company: incorporation, partners, powers of attorney, amendments | Public consultation by folio, name or partner through rpc.economia.gob.mx. Login required (account or e.firma). Bulk download is restricted. The "Acervo histórico" holds older paper records. | Web UI only. **No public API.** Copies and certifications are paid per item. | Interactive web, so slow. Not all acts are digitized (pre-SIGER records sit in state registries). | Consultation is free. Certifications are paid (fee table not verified this session). | Commercial: Kiban, Moffin, Nubarium (acta parsing), zavia/zephira-style aggregators, D&B Mexico, Infobel. **Most KYB vendors OCR the acta constitutiva the customer uploads** instead of querying RPC. |
| **PSM: Publicaciones de Sociedades Mercantiles** (psm.economia.gob.mx) | Legally required publications (mergers, capital reductions, liquidations, share transfers in some cases) | Public | Web search | — | Free | Rarely integrated. Useful for adverse corporate-event checks. |
| **Secretaría de Economía UBO registry** (Art. 33 Bis LFPIORPI, July 2025 reform) | Planned registry of beneficiarios controladores | — | — | — | — | **Not operational.** SHCP's AML General Rules (DOF 2026-08-07, in force 2026-11-30) set UBO criteria (≥25% ownership, then control by other means, then senior management; effective 2027-03-01). The SE registry guidelines **are still pending**. Tax-side UBO information goes to SAT under CFF Arts. 32-B Ter–Quinquies and is **not accessible** to obligated entities. |

**Mexico regulatory note (CNBV, July 2026).** CNBV amended the CUB (DOF 2026-07-01, in force 2026-07-02, 90 business days to comply, so roughly early November 2026). The changes cover Arts. 51 Bis to 51 Bis 5 and fully replace **Anexo 71**:
- Facial recognition is now an approved verification factor alongside fingerprints. It applies to higher-risk **in-person** operations: passive operations on Level 4 accounts, and active, payment-service or payment-instrument operations on Level 3 and 4 accounts.
- A biometric can only enter a bank's own database after a **1:1 match against official records (INE, SRE, SAT or another federal agency) with ≥90% match**.
- Liveness/PAD must meet **ISO/IEC 30107-3**. Deduplication should reference **NIST FRVT** methods.
- Encryption at rest and in transit, logical segregation of biometric databases, and irreversible deletion are required.
- Biometric databases **cannot be sold, transferred, shared or made interoperable**.
- **Anexo 75** notifies CNBV of existing biometric databases (due about 2026-08-01).
- Fingerprint requires capturing at least 6 fingers plus live-finger detection.

Sources: SECONDARY ([Facephi Observatory, 2026-07-09](https://facephi.com/observatory/en/cnbv-facial-biometrics-regulation/), [Sovos](https://sovos.com/es/blog/iva/reforma-cnbv-ampliacion-validacion-biometrica-mexico/), [La Silla Rota 2026-07-01](https://lasillarota.com/nacion/2026/7/1/de-que-va-la-nueva-regla-que-permitira-bancos-verificar-tu-identidad-con-biometria-facial-518516.html)). **I did not read the DOF text itself, so the 90% threshold is SECONDARY.** Remote onboarding for banks and ITFs still follows the earlier non-presencial rules (Arts. 51 Bis 6–8 CUB and the CNBV ITF/SOFIPO rules), which require checking INE data and biometrics with INE or an equivalent source. Confirm which of those remote-onboarding annexes were also changed.

**MX evidence**
- INE service, convenio list and 5–7 s latency: VERIFIED, [ine.mx/servicio-verificacion-datos-credencial-votar](https://ine.mx/servicio-verificacion-datos-credencial-votar/) (page dated 2021-08-06; the list may be stale).
- INE/CG91/2020 (sensitive data, photo and fingerprints, needs a specific convenio plus consent): SECONDARY, [DOF PDF](https://www.dof.gob.mx/2020/INE/CGex202005_15_ap_2.pdf).
- INE facial verification and tech providers: SECONDARY (vendor claims), [naat.tech](https://www.naat.tech/blog/validar-datos-faciales-con-el-ine), [arzatenoticias 2023-09-28](https://arzatenoticias.com/2023/09/28/presentan-al-ine-tecnologia-de-verificacion-biometrica-facial-para-evitar-fraudes/), [Veridas INE facial PDF](https://veridas.com/docs/Servicio-de-Verificacion-Facial-INE-2023.pdf).
- INE new credential model in production since 2026-06-01, with both models valid during the transition: SECONDARY, [El Cronista](https://www.cronista.com/mexico/actualidad-mx/adios-a-la-credencial-del-ine-tradicional-habra-una-nueva-identificacion-para-votar-y-tendra-mayor-seguridad/). **Impact: the document-classification model must support the new INE model.**
- RENAPO web services under coordination agreements: SECONDARY, DOF summary through [sidof.segob.gob.mx](https://sidof.segob.gob.mx/notas/docFuente/5793937).
- CURP Biométrica (DOF 2025-07-16, RENAPO is the sole custodian): SECONDARY, [kyc-systems.com](https://kyc-systems.com/blog/que-es-la-curp), [veridas.com](https://veridas.com/es/curp-biometrico/). **Whether RENAPO exposes biometric 1:1 matching to private entities: UNVERIFIED and not found.**
- SRE–Banxico passport verification: SECONDARY, [El Financiero](https://www.elfinanciero.com.mx/economia/asi-como-lo-hace-el-ine-la-sre-compartira-su-base-de-datos-con-los-bancos), [Publimetro 2023-01-30](https://www.publimetro.com.mx/nacional/2023/01/30/sre-y-banxico-aceptan-matriculas-y-pasaportes-en-bancos-como-identificacion/).
- SIGER consulta pública: VERIFIED that the manuals exist, [rpc.economia.gob.mx manuals](https://rpc.economia.gob.mx/siger2/resources/docs/Manual_Usuario/Manual_Usuario-Analista_SIGER.pdf). Login and bulk restrictions: SECONDARY, [businessdataguide](https://www.businessdataguide.com/blog/jurisdictions/mexico-company-search-guide).
- UBO registry pending; AML rules DOF 2026-08-07, in force 2026-11-30, UBO obligations from 2027-03-01, Consulta PEP 2.0 usable 9 months after entry into force: VERIFIED, [Baker McKenzie Aug 2026](https://www.bakermckenzie.com/en/insight/publications/2026/08/mexico-new-general-rules-of-the-anti-money-laundering-law).
- SAT bulk RFC validation (TXT up to 5,000) and the CSF QR: UNVERIFIED (from memory, consistent with vendor pages such as [edifact](https://www.edifact.com.mx/masinfo/validacion-rfc-masiva)).

**Design implication (MX).** As a SaaS provider, we will almost certainly **not** hold our own INE convenio at launch. INE prioritizes regulated entities and its approval history is opaque (2024 transparency requests ask about rejected private applicants). There are two paths:
- **(a)** Integrate a vendor that already holds INE biometric access, such as NA-AT or Veridas, or an IDV vendor reselling it.
- **(b)** Support "bring-your-own-convenio" for bank tenants. The tenant's credentials are used and the call is routed through the tenant's certified tech provider.

Model the INE check as a pluggable `RegistryCheck` with `assurance_level ∈ {format, data_match_public, data_match_official, biometric_1to1_official}`.

### A.2 Brazil (BR)

| Registry / service | What it offers | Who can access | API | Latency / availability | Cost (published) | Vendors reselling |
|---|---|---|---|---|---|---|
| **Serpro Consulta CPF** (Receita Federal base) | CPF status (regular / suspensa / cancelada / titular falecido / nula), name, birth date, and death year where applicable | **Any company** that contracts through Loja Serpro (B2B contract, CNPJ required). Not restricted to regulated entities. | REST/JSON, OAuth2 client credentials (API Gateway) | Real-time, sub-second typical (UNVERIFIED) | **VERIFIED tiered per query/month:** R$0.6591 (≤999), R$0.5649 (1k–9,999), R$0.3557 (10k–49,999), R$0.2616 (50k–99,999), R$0.1779 (100k–249,999) … R$0.017 (>30M) | BigDataCorp, idwall, CAF, unico, Serasa, Neoway, Truora, Sumsub, and nearly every BR KYC vendor |
| **Serpro Consulta CNPJ** | Three products: *Básica* (status, CNAE, address), *QSA* (quadro de sócios e administradores), *Empresa* (QSA with full partner CPF/CNPJ) | Any contracting company | REST/JSON | Real-time | **VERIFIED per query, tier 1 (≤999/month):** Básica R$0.6591, QSA R$0.8683, Empresa R$1.1717. Tier 5 (100k–249k): R$0.1779 / 0.3976 / 0.5649. Floor (>30M): R$0.025 / 0.067 / 0.108 | Same as above. Also free **Receita "Dados Abertos CNPJ"** monthly bulk dump (QSA with masked partner CPF) that many vendors (BrasilAPI, Minha Receita, CNPJá) re-host. |
| **Serpro Datavalid** | Biographic validation (name, birth date, parents, document data, CPF status) **plus 1:1 facial against the government image base** (historically CNH/SENATRAN photos; Serpro's AIBio now aggregates ~376M faces from electoral, CNH and gov.br sources). Also fingerprint via the Datavalid app, CNH QR decode, and a liveness API (passive) / Bioconnect SDK. Returns similarity 0–1 plus a probability band (baixa / alta / altíssima). | Any contracting company. Serpro markets it to banks, retail, car rental, insurers, ride-hailing and "ID techs & startups". Free trial: 30 days or 3,000 queries. | REST/JSON (OAS3 docs) | Serpro claims a few seconds. 2020 PR: "4 seconds". | **Tiered table is not machine-readable on the site and was not captured.** A customer quoted about **R$1.50/validation** (SECONDARY, Serpro news 2024). No minimum, no monthly fee, no setup fee (VERIFIED). | unico, idwall, CAF, BigDataCorp, Truora, Sumsub, Didit, Jumio/Onfido BR partners |
| **Pix DICT** | Key-to-account lookup returns holder name (masked), CPF/CNPJ (masked), and ISPB/account | **Only Pix direct/indirect participants** (authorized IPs/IFs), and only to start a Pix payment. Anti-scan rate limits (token bucket per participant and per user) and a BCB fraud-marking API (MED, "marcação de fraude") apply. Public entities have a separate regime under Res. BCB 338/2023. | RSFN/ICOM SOAP/REST (Pix participants only) | ms | n/a | **Cannot be used as a generic KYC data source by a SaaS vendor.** A fintech tenant that is a Pix participant can use DICT fraud markers ("informações de segurança") in its own flows. Name-matching by "Pix micro-deposit" (send R$0.01 and read the returned recipient name) is a common grey-zone pattern. |
| **CIN (Carteira de Identidade Nacional)** | CPF as the single number. ICAO-style MRZ. QR code verifiable in the official app ("partial" offline read; "full" read needs gov.br login, validates authenticity and shows registered data). Old RGs remain valid until 2032-03-01. | QR validation by companies through the app. **No documented B2B API.** Biometric match goes through Datavalid (gov.br / TSE / CNH bases), not a CIN-specific endpoint. | — | — | — | Document OCR/QR: unico, idwall, CAF, Veridas, Regula |
| **Juntas Comerciais / REDESIM** | Articles of incorporation, amendments, administrators | 27 state Juntas, with no unified API. Some offer paid "certidão simplificada" online. | Web, per state | Slow (minutes to days for certidões) | Per-certidão fees vary by state | BigDataCorp, Neoway, Serasa, CNPJá, Escavador; KYB vendors OCR the contrato social |

**BR notes**
- **Datavalid scope, from Serpro's own docs (VERIFIED):** *"Datavalid does not verify whether the face image sent by the client has been manipulated by virtual camera capture, AI, face blending, deepfake, image injection …"* and *"biometric comparison is probabilistic … up to the client to decide how to use results."* You must therefore put your own PAD and injection-attack detection in front of Datavalid. The source is [apicenter.estaleiro.serpro.gov.br/documentacao/datavalid/en](https://apicenter.estaleiro.serpro.gov.br/documentacao/datavalid/en/). The docs also say the Datavalid passive liveness is "100% compliant with INSS payroll-loan (consignado) technical requirements".
- Datavalid image requirements: at least 250×250 px, 750×750 recommended. CPFs without a CNH get biographic-only checks, and the `cnh_disponivel` field is returned from v3. Source: SECONDARY, search summary of apidocs.datavalidp (the docs site has an invalid TLS certificate).
- Serpro AIBio holds about 376M faces (Aug 2026): SECONDARY, [Biometric Update 2026-08-21](https://www.biometricupdate.com/202608/serpros-376m-biometric-database-underpins-brazils-sovereign-identity-strategy) and [Mobile Time 2026-08-18](https://www.mobiletime.com.br/noticias/18/08/2026/biometria-serpro-376-mi/).
- Prices: VERIFIED by rendering [loja.serpro.gov.br/product/consultacpf](https://www.loja.serpro.gov.br/product/consultacpf) and [/product/consultacnpj](https://www.loja.serpro.gov.br/product/consultacnpj) on 2026-10-17. Datavalid: [/product/datavalid](https://www.loja.serpro.gov.br/product/datavalid). The R$1.50 figure comes from [Serpro news 2024](https://serpro.gov.br/menu/noticias/noticias-2024/ia-e-biometria-contra-fraudes) (SECONDARY).
- CIN: SECONDARY, [Assespro-PR](https://assespropr.org.br/qr-code-da-autenticidade-a-carteira-de-identidade-nacional/), [Polícia Científica SC 2026-05-22](https://www.policiacientifica.sc.gov.br/2026/05/22/rg-antigo-continua-valido-ate-2032-entenda-quando-sera-obrigatoria-a-troca-pela-nova-cin/).
- DICT rules: SECONDARY/UNVERIFIED. The query-purpose restriction and anti-scan limits are from memory of the Manual do DICT. Sources: [Voto BCB 19/2025](https://normativos.bcb.gov.br/Votos/BCB/202519/Voto_do_BC_19_2025.pdf), [Res. BCB 338](https://okai.com.br/documento/2023-08-23/resolucao-bcb-n-338).
- **Design implication (BR).** Contract Serpro **directly** for CPF, CNPJ-QSA and Datavalid. It is open to non-regulated buyers, transparently priced and cheap. Treat the IDV vendor as the capture, PAD and document layer. This avoids paying vendor markup on the most common checks. Watch the risk that LGPD controller/processor roles must be explicit in tenant contracts.

### A.3 Colombia (CO)

| Registry / service | Offers | Who can access | API | Cost | Vendors |
|---|---|---|---|---|---|
| **Registraduría (RNEC): ANI (Archivo Nacional de Identificación)** | Cédula status (vigente / cancelada por muerte, etc.), biographic data. **Fingerprint** biometric 1:1 against ANI. Facial biometrics are now offered to the financial sector (RNEC communiqué on facial technology). | Public entities and private entities with a **convenio**. Biometric checks go through **RNEC-certified "operadores biométricos"** (habilitation via technical test, Res. 11916/2017 and successors). Financial institutions access the ANI through those operators. Asobancaria runs the sector program. | SOAP/REST through the operator | **Res. 1500/2026 (VERIFIED in search extract, SECONDARY):** ANI/SIRC query of non-reserved information **COP 108.81 per record**, with special processing/filters **COP 164.23 per record**. Biometric per-match fees are set by each operator and not public. | Certified operators (list not retrieved): e.g. **Olimpia IT, Reconoser ID, Ado Tech, GSE, Certicámara** (UNVERIFIED list). IDV vendors with ANI integrations include Truora, Verifik, MetaMap/Incode, Jumio (via partners) and AdoTech. |
| **SFC rule.** Concepto 2025218928 (2025-12-18) under Circular Externa 006/2025 (CBJ Parte I, Título II, Cap. I) | Financial entities using biometrics must verify against **RNEC databases or authorized operators** and implement **liveness ("pruebas de vida")**. They must also offer non-biometric alternatives for people unable to use biometrics. | — | — | — | SECONDARY: [ambitojuridico](https://www.ambitojuridico.com/noticias/comercial/entidades-financieras-pueden-establecer-uso-de-datos-biometricos-como), [accolombianlawyers](https://www.accolombianlawyers.com/noticia/superfinanciera-fortalece-la-seguridad-en-la-autenticacion-biometrica) |
| **RUES (Confecámaras)** | Registration (matrícula), status, NIT, legal representatives, activities, establishments. Certificado de Existencia y Representación Legal (CERL) from the relevant cámara. | **Public.** Basic data is free (SIC rule requires free minimum data access). CERL is paid per certificate. | Public web. **No documented open API**; Confecámaras offers bulk/institutional data agreements (UNVERIFIED). | CERL paid per certificate (about COP 7–8k, UNVERIFIED) | Verifik (`/v3/co/rues-complete`), Truora, Apify scrapers, Informa Colombia, Experian/Datacrédito, Bureau Van Dijk |
| **DIAN RUT** | Taxpayer status, responsibilities, address. Public "Consulta estado RUT". | Public lookup (captcha). Full RUT document is supplied by the customer (PDF with QR/verification code). | Web | Free | Verifik, Truora, Nubarium-like local vendors |
| **DIAN RUB (Registro Único de Beneficiarios Finales)** | UBOs of Colombian legal entities (Ley 2155/2021; Res. 164/2021, compiled in **Res. 227/2025**, modified by **Res. 0004/2026**) | **Confidential and reserved.** Only authorities expressly authorized by law can access it (DIAN FAQ). **Obligated entities and fintechs cannot query it**, so KYB must still collect UBO declarations. | — | — | — |

Sources: [Res. 1500/2026 on SUIN](https://www.suin-juriscol.gov.co/viewDocument.asp?ruta=Resolucion%2F30056097) (the page did not render via curl; content is SECONDARY from the search extract). [RNEC facial for financial sector](https://www.registraduria.gov.co/Registraduria-Nacional-pone-a-disposicion-del-sector-financiero-tecnologia-de.html) (SECONDARY). [Asobancaria program PDF](https://www.asobancaria.com/wp-content/uploads/2023/01/Programa_Autenticacion_Biometrica_2023_v1801.pdf). [RUB FAQ](https://accounter.co/descarga/42743/circulares/93554/preguntas_frecuentes_rub) (SECONDARY). [Verifik RUES docs](https://docs.verifik.co/business-validation/rues-complete-v3/) (VERIFIED that the vendor docs exist).

### A.4 Argentina (AR)

| Registry / service | Offers | Who can access | API | Cost (published) | Vendors |
|---|---|---|---|---|---|
| **RENAPER SID (Sistema de Identidad Digital)** | Three flows (VERIFIED, argentina.gob.ar/sid). **(1)** Front and back DNI photos plus selfie with **active liveness (2 random gestures)**: the SDK scores document "veracity" and calls RENAPER facial biometrics, which returns positive or negative. **(2)** DNI number + sex + selfie, with no card: RENAPER returns a match %. **(3)** DNI number + sex + **número de trámite**: returns document vigencia (validity). | Public bodies and **private companies under a convenio** with RENAPER (for example ENACOM/telcos 2024, Signatura e-signature 2026). Companies use the **RENAPER app/SDK or their own API**. Fees go to Ley 27.431 Art. 94. | REST API ("SERVICIOS DE VALIDACIÓN MEDIANTE TECNOLOGÍA API REST", per convenio) plus a mobile SDK | **Res. MI 19/2026, in force about 2026-03-06 (SECONDARY, table reproduced by derechoenzapatillas.com):** DNI vigencia / data validation / death-notice check at **ARS 60 (≤40k tx/month), ARS 35 (40k–2M), ARS 20 (>2M)**. Passive liveness and passport verification appear as **ARS 70** (table garbled). APN public bodies pay about 10% of that. The row for **facial biometric validation was not legible**. | Truora, MetaMap/Incode, Didit, VU Security (Argentine, long-time RENAPER integrator), Nosis, Veraz/Equifax, Signatura, Sumsub (via partners) |
| **ARCA (ex-AFIP) padrón: `ws_sr_padron_a5 / a13` / constancia de inscripción** | CUIT/CUIL status, name, tax regimes, activity, address, and (for legal entities) some partner data in A5 | Any CUIT holder with an ARCA digital certificate, after authorizing the WS in "Administrador de Relaciones" (UNVERIFIED, from memory) | SOAP via WSAA token (X.509 certificate) | Free | Nosis, Veraz, Truora, Didit, TusFacturas, AFIP SDK |
| **IGJ (Inspección General de Justicia, CABA only)** plus provincial registries (DPPJ Buenos Aires, etc.) | Company incorporation, authorities, statutes | IGJ entity data partly in **datos.jus.gob.ar open data** (UNVERIFIED). Certificates and filings are paid via TAD. **No API.** | Web / open-data CSV | — | Nosis, Veraz, D&B Argentina |
| **UBO** | Argentina has no public UBO registry. IGJ RG 7/2015 and its amendments require UBO disclosure to IGJ. AFIP RG 4697/2020 "beneficiarios finales" (tax) is **not accessible** to private parties. | — | — | — | — |

**AR "rigorous biometric techniques".** The UIF framework (Res. 30-E/2017 for banks and successor resolutions, including the 2024–2025 rewrite for sujetos obligados) allows **non-face-to-face onboarding** when the identity is validated with **"técnicas biométricas"** against RENAPER. In practice, the market reads "rigorous" as: RENAPER SID facial 1:1 plus liveness plus DNI image capture (front and back, PDF417). The exact wording of the current UIF resolution was **not retrieved** (UNVERIFIED). SECONDARY: [pensamientocivil.com.ar](https://pensamientocivil.com.ar/doctrina/3950-aceptacion-identificacion-clientes-no-presenciales-y-tecnicas).

Sources: [SID process](https://www.argentina.gob.ar/sid/proceso-de-verificacion) (VERIFIED). [ENACOM–RENAPER telco convenio](https://www.enacom.gob.ar/institucional/enacom-y-renaper-firmaron-un-acuerdo-con-empresas-de-telefonia-celular-para-brindar-el-servicio-de-validacion-de-identidad-digital_n4691) (SECONDARY: canon paid per query). [Res. 19/2026 fee table](https://www.derechoenzapatillas.com/2026/nuevas-tasas-renaper-registro-nacional-de-las-personas/) (SECONDARY; confirm on [Boletín Oficial](https://www.boletinoficial.gob.ar/)). [Signatura RENAPER integration 2026](https://signatura.co/blog/validacion-renaper-biometrica/).

### A.5 Chile (CL)

| Registry / service | Offers | Who can access | API | Cost | Vendors |
|---|---|---|---|---|---|
| **Registro Civil (SRCeI): "Consulta de vigencia de documento"** | Checks whether a cédula/passport (RUN + document serial number) is valid, blocked or lost | **Public** web query (captcha) | Web; no public API. Vendors proxy it. | Free | Most CL IDV vendors (Autentia, TOC Biometrics, Truora, MetaMap/Incode, Floid) |
| **Registro Civil biometrics** | Fingerprint 1:1 against SRCeI is the historic Chilean standard (notaries, banks, telcos) through providers. The **new IDEMIA cédula (issued since 2024-12-16)** has **contactless and contact chips** with biometric data and passive/active authentication. The digital cédula app was regulated by Res. Exenta 234 of 2026-05-13 and activates with facial liveness. | Through **authorized biometric providers** with SRCeI agreements. **Subtel Res. 566/2025** (telcos) requires fingerprint vs SRCeI **or** facial-with-liveness vs the cédula photo. | Provider APIs | Not public | **Autentia** (fingerprint vs SRCeI, dominant in banking/notaries; UNVERIFIED), **TOC Biometrics** (Chilean, facial vs cédula; UNVERIFIED), Sovos/Keynua, Facephi, Veridas |
| **SII** | RUT existence, "situación tributaria de terceros" (activities, start date, tax-document authorization) | Public web (captcha) | No official open API. Vendors scrape. | Free | Floid, Truora, Simple API, Boletas/Facturación vendors |
| **Registro de Empresas y Sociedades (RES, Ley 20.659 "Empresa en un día")** | Companies formed under the simplified regime: statutes, amendments, certificates of validity | Public, with **open data** downloads (UNVERIFIED) | Web plus datos.gob.cl | Free | — |
| **Conservador de Bienes Raíces (CBR) Registro de Comercio** | Traditional companies (inscripción, vigencia, notes) | Public, with paid certificates (CBR Santiago online) | Web | Per certificate | Manual |

**CL privacy.** Ley 21.719 (new data-protection law) takes full effect on **2026-12-01**. It classifies biometrics as **sensitive data** and creates an agency. Plan explicit consent and a DPIA for the CL cell.

Sources: [Sovos on Subtel 566](https://sovos.com/es/blog/iva/biometria-resolucion-566-y-industria-de-las-telecomunicaciones/), [dplnews](https://dplnews.com/chile-activa-biometria-obligatoria-tramites-operadores/), [Res. 234 Exenta 2026 (BCN)](https://www.bcn.cl/leychile/Navegar?idNorma=1224056), [revistaseguridad 2024 new cédula](https://revistaseguridad.cl/2024/06/05/nueva-cedula-de-identidad/). All SECONDARY except BCN, which I located but did not read in full.

### A.6 Peru (PE)

| Registry / service | Offers | Who can access | API | Cost (published) | Vendors |
|---|---|---|---|---|---|
| **RENIEC: Servicio de Verificación de Identidad** | Modalities: online query (consulta en línea), **fingerprint biometric verification**, "consulta integrada", **web services including a facial biometric web service** | **Notaries, public entities and legal entities incorporated in Peru** operating locally. **Requires a convenio de suministro de información.** Use is restricted to identity verification for the institution's own activities. | Web services (SOAP) or a desktop client with a fingerprint reader | **VERIFIED (gob.pe):** fingerprint verification per query: **S/1.50 (0–30k)**, S/0.70 (30,001–120k), S/0.59 (120,001–390k), S/0.32 (390,001–1.2M), **S/0.14 (>1.2M)**. Facial tariff **not published** on that page (page last updated 2021-09-27, so it may be stale). | Facephi, Veridas, Truora, MetaMap/Incode, Keynua, Equifax PE; local integrators (e.g. Soluciones Biométricas) |
| **SUNAT RUC padrón** | RUC status ("activo / habido"), name, address | Public. **"Padrón reducido" daily bulk download (free)** plus web lookup (UNVERIFIED) | Bulk TXT plus web | Free | apis.net.pe, Decolecta, Equifax |
| **SUNARP** | Companies (partida registral), powers of attorney, assets | Public "Publicidad registral en línea", paid per copy. Interoperability (PIDE) is for public entities only. | Web, paid | Per certificate | Equifax, Sentinel, Infocorp |

**PE regulatory note.** SBS Res. 2660-2015 (Reglamento de Gestión de Riesgos LA/FT) and later amendments allow non-face-to-face identification using RENIEC biometric verification. The specific article was not retrieved (UNVERIFIED). Peru's new Data Protection Regulation (DS 016-2024-JUS, in force March 2025) treats biometrics as sensitive (UNVERIFIED).

Sources: [gob.pe RENIEC verification service](https://www.gob.pe/13547-solicitar-acceso-al-servicio-de-verificacion-de-identidad-de-personas-en-reniec-acceder-a-servicio-de-verificacion-biometrica-de-identidad-de-personas) (VERIFIED), [sample convenio](https://cdn.www.gob.pe/uploads/document/file/7908182/6657973-convenio-con-reniec.pdf).

---

## B. Identity verification vendors active in LATAM

### B.1 iBeta ISO/IEC 30107-3 PAD confirmation letters (VERIFIED)

Source: the iBeta PAD letter index pages ([biometric-spoofing-and-pad-testing](https://www.ibeta.com/biometric-spoofing-and-pad-testing/) and the confirmation-letter list), fetched 2026-10-17. Dates are the letter dates. iBeta now issues **Level 3** letters (2026+). Letters cover the specific product, version and platform tested. They are **not a product certification**.

| Vendor | iBeta letters found (date, level) | Not found / notes |
|---|---|---|
| **Incode** | L1 2019-08; L2 2023-01-30; **L3 2026-02-16** | — |
| **Sumsub** | L1 2025-02-07; L2 2025-03-25; **L3 2026-09-17** | — |
| **Jumio** | L2 2025-09-11 | — |
| **Onfido** | L1 2021-06; L1/L2 video, iOS/Android 2021-10 to 2022-06 | — |
| **Entrust** (Onfido parent) | L1 2026-07-27 | — |
| **Veridas** | L1 2020-12; L1 2024-03-15; L2 2024-04-11; "Veridas Shield" L1 2024-12 | — |
| **FacePhi** | L2 2022-03-03 | — |
| **idwall** (BR) | L1 2023-11-27; L2 2024-08-14 | — |
| **CAF** (Combate à Fraude, BR) | L1 2022-01-13 | No L2 found |
| **VU Security** (AR) | L1 2021; L2 2021-12; **L1+L2 retest 2026-04-22** | — |
| **FaceTec** | L2 2019-07 | Old letter. FaceTec mostly relies on its own spoof bounty. |
| **iProov** | L1 and L2 2020 (two products) | — |
| **Regula** | L1 2024-10; L2 2025-03-21; Android L2 2025-12-10 | — |
| **Jaak** (MX) | L1 2024-12-24 | — |
| **Didit** | L1 iOS 2026-01-04 | — |
| Paravision, ID R&D (Mitek), Yoti (L3 2026-01), Shufti Pro (L3 2026-04), ZOLOZ (L2 2026-05) | various | Engine/OEM vendors |
| **unico, Truora, AuthenticID, MetaMap** | **No letter under these names** | They may test through other labs (BixeLab, Ingenium) or embed an OEM engine. MetaMap now uses Incode's passive liveness (SECONDARY, Sacra). **Ask each for its lab report.** |

### B.2 Vendor comparison

Legend: **V** = VERIFIED in vendor docs or price page this session; **S** = SECONDARY; **U** = UNVERIFIED (prior knowledge, confirm in RFP).

| Vendor | HQ / ownership | LATAM registry integrations | Doc coverage | Liveness | Deployment | Data residency MX/BR | API style | Public pricing |
|---|---|---|---|---|---|---|---|---|
| **Incode** (owns **MetaMap** since June 2024, including the MX and BR subsidiaries, S) | US/MX founders | **MX INE: direct face+data and fingerprint** (V). **BR Serpro: face-only and data+face** (V). **AR RENAPER: face; requires client's own RENAPER contract** (V). **CO RNEC + Migración: data only** (V). **CL SRCeI: RUN + doc number data only** (V). PE not documented. | Global plus deep MX | Passive, iBeta L3 | SaaS, SDK (web/iOS/Android). On-prem for large banks (U). | MX hosting for banks claimed (U) | REST "Omni" API, session-based (`/omni/process/government-validation`) (V) | Not public |
| **Truora** (Colombia, YC) | CO | Background checks plus ID data across CO, MX, PE, CL, BR, CR (S). Strong WhatsApp-native flows (U). | LATAM IDs | Passive/active (U) | SaaS | U | REST + webhooks | Not public |
| **unico** (BR; SoftBank/GA, ~US$1B valuation) | BR | "Rede" of about 6M faces/month in its own network. IDUnico returns "Yes / Inconclusive + score" (S). Serpro checks (U). | BR docs (CNH, RG, CIN) | SmartLive (proprietary) | SaaS + SDK | BR (U) | REST | Not public |
| **idwall** (BR) | BR | Receita/Serpro, background checks, CNH/RG OCR (U) | BR + LATAM | iBeta L2 2024 (V) | SaaS | BR (U) | REST | Not public |
| **CAF** (Combate à Fraude, BR) | BR | Serpro, Receita, PEP/sanctions (U). Expanding to MX (U). | BR | iBeta L1 2022 (V) | SaaS | BR (U) | REST | Not public |
| **VU Security** (AR) | AR | **RENAPER** long-time integrator (U), AFIP. Present in AR, PY, UY, CL, EC (U). | LATAM | iBeta L2 retest 2026 (V) | SaaS + **on-prem** (common for AR banks, U) | AR (U) | REST/SDK | Not public |
| **Jumio** | US | LATAM: "selfie.DONE" reusable ID expanding from BR to LATAM in 2026 (S). Government DB via partners (U). | 5,000+ doc types (U) | iBeta L2 2025 (V) | SaaS | Regional data centers in US/EU/SG; LATAM residency U | REST | Not public |
| **Onfido (Entrust)** | UK/US | Limited native LATAM DB checks (U) | Broad | L1/L2 (V); Entrust L1 2026 | SaaS | U | REST | Not public |
| **Veridas** (ES; partly owned by BBVA-linked investors, U) | ES | **MX INE facial (engine claimed to run on INE servers, vendor claim S)**, RENAPO CURP | LATAM | iBeta L1/L2 2024 (V) | SaaS + **on-prem** | On-prem enables MX/BR residency | REST/SDK | Not public |
| **FacePhi** (ES, listed) | ES | MX INE/SRE claims for CNBV Anexo 71 (S). PE RENIEC, CO, CR banks (U). | LATAM | iBeta L2 2022 (V) | SDK + on-prem/SaaS | On-prem | SDK-centric | Not public |
| **Sumsub** | UK/CY | Government DB checks in BR/MX etc. (U) | 14k+ doc types (U) | iBeta **L3 2026** (V) | SaaS | U | REST | **V: $1.35/verification "Basic" ($149 monthly minimum); $1.85 "Compliance" ($299 minimum)** |
| **Didit** | ES | **Database validation per answered attempt (V):** MX RENAPO $0.20, BR Receita Federal $0.40, AR RENAPER $0.20, CL SRCeI $0.20, CO RNEC/ANI $0.20, PE RENIEC $0.20 | Global | iBeta L1 iOS 2026 (V) | SaaS | Enterprise "data residency" option (V, no regions listed) | REST | **V: $0.33/full KYC; modules: ID $0.15, liveness $0.10, face match $0.05, AML from $0.07; 500 free/month.** Pricing changes 2026-11-01. |
| **NA-AT Technologies** (MX) | MX | **INE Verificación Facial convenio, infrastructure at INE** (S, vendor claim) | MX | U | SaaS/on-prem | MX | REST | Not public |
| **Nubarium / Kiban / Moffin / Jaak** (MX) | MX | CURP/RENAPO, INE Lista Nominal (data), SAT CSF/RFC, IMSS, SIGER (U) | MX | Jaak iBeta L1 (V) | SaaS | MX | REST | Some publish per-call prices (not checked) |
| **AuthenticID** | US | Doc auth focus. LATAM DBs U. | Broad | Not on iBeta list | SaaS | U | REST | Not public |
| **FaceTec** (component) | US | — | — | 3D FaceMap liveness, iBeta L2 2019; Spoof Bounty | SDK + **self-hosted server** | Self-host anywhere | SDK | Per-device/per-session license, not public |
| **iProov** (component) | UK | Used by gov (U) | — | Dynamic/Express liveness, L1/L2 (V). Strong on injection (Flashmark). | SaaS (cloud), US/EU regions (U) | U | SDK + REST | Not public |
| **Regula** (component) | LV/US | — | Largest doc template DB. **NFC chip reading (eMRTD), MRZ, barcodes** | L2 2025 (V) | **On-prem / self-hosted Document Reader SDK** | Self-host | SDK + REST server | License-based |

**Market notes**
- Jumio is expanding selfie.DONE across LATAM ([Biometric Update 2026-04](https://www.biometricupdate.com/202604/jumio-expanding-biometric-reusable-digital-identity-across-latam)).
- Incode bought MetaMap ([PrivSource](https://www.privsource.com/acquisitions/deal/VnSb3D), [Latin Lawyer](https://latinlawyer.com/organisations/incode-technologies)).
- Incode docs: [system-of-record-mexico](https://devhub.omnihub.incode.com/general-reference/system-of-record-mexico.md), [INE fingerprint](https://developer.incode.com/features-and-modules/ine-fingerprint-validation.md), and the `/system-of-record-{brazil,colombia,argentina,chile}.md` pages (all VERIFIED). One detail in the Incode docs: an optional "fallback to scraping" if the INE connection fails (S). **Turn it off** for regulated tenants so there is no silent assurance downgrade.
- Pricing pages: [Sumsub](https://sumsub.com/pricing/), [Didit](https://didit.me/pricing) (VERIFIED 2026-10-17).

### B.3 Recommended primary and fallback per country

| Country | Primary | Fallback | Rationale |
|---|---|---|---|
| **MX** | **Incode** (doc + passive liveness L3 + **direct INE face+data** + INE fingerprint for branch flows) | **Veridas or FacePhi** for INE facial and CNBV Anexo 71 bank tenants (on-prem option). **NA-AT** as an INE-only connector. Data layer (RENAPO, SAT, Lista Nominal) from a cheap MX data vendor (Nubarium/Kiban) or Didit ($0.20 RENAPO). | INE biometric 1:1 is the regulator-recognized official source. Few vendors have it directly, and Incode documents it publicly. Keep a second INE path because INE is a single point of failure (5–7 s, outages). |
| **BR** | **Serpro direct** (Consulta CPF, CNPJ-QSA, Datavalid face) + **unico or idwall** for capture, PAD and doc OCR. idwall has a verified iBeta L2. | **CAF** or **Incode** (Serpro face V) | Serpro is open to non-regulated buyers, cheap and published. Use a local vendor for doc OCR and the BR-specific fraud network (unico's face network). Datavalid itself has **no injection/deepfake detection** (V), so PAD/IAD must come from the vendor. |
| **CO** | **Truora** (local, RNEC data, WhatsApp flows) + a **certified RNEC biometric operator** (e.g. Olimpia, Reconoser, AdoTech) for fingerprint/facial vs ANI where SFC requires it | Incode/MetaMap (RNEC data only), Didit ($0.20 ANI) | SFC requires biometric verification against RNEC or authorized operators plus liveness. Global vendors only offer data checks. |
| **AR** | **VU Security** (RENAPER-native, on-prem friendly, iBeta L2 retest 2026) | **Incode with the tenant's own RENAPER credentials** (V) or Didit (RENAPER $0.20) | RENAPER access is per convenio. Many vendors pass the tenant's credentials through. Design for **BYO RENAPER convenio per tenant**. |
| **CL** | **Chip-based (NFC) reading of the cédula** (Regula SDK or vendor with NFC) + SRCeI vigencia + facial vs chip photo; or **Autentia** fingerprint vs SRCeI (U) for higher assurance | Incode/Truora (data only), Didit ($0.20 SRCeI) | The Chilean cédula has an ICAO chip, so cryptographic doc authenticity is possible without a government biometric API. |
| **PE** | **FacePhi or Veridas** with **RENIEC facial web service** (via the tenant's or our own RENIEC convenio; PE-incorporated entity required) | Truora / Didit (RENIEC data $0.20) + DNIe 3.0 NFC chip read | RENIEC requires a PE legal entity plus convenio. Fingerprint pricing is published (S/1.50 → S/0.14). |

---

## C. Liveness and biometrics: technical standards

### C.1 Standards landscape

- **ISO/IEC 30107-3 (PAD testing and reporting).** Metrics are **APCER** (attack presentation classification error rate, per PAI species), **BPCER** (bona fide rejection), **IAPAR** (impostor attack presentation accept rate, for the full system).
  - iBeta's letters define levels by attacker effort. **L1:** simple artifacts (photo, screen replay, paper masks), 8 hours per species. **L2:** 24 hours per species, 3D masks, latex/resin. **L3:** introduced around 2025–26, highest effort (custom silicone, hyper-realistic masks; U on exact scope).
  - Each species has 150 attack presentations and 50 bona fide presentations (S, iBeta letters).
  - iBeta conformance requires **0% successful attacks** at the tested threshold, with BPCER reported (U: BPCER ≤15% cap from memory).
- **NIST FATE (Face Analysis Technology Evaluation) PAD track** and **FRTE 1:1 / 1:N** (formerly FRVT): use these for face-match accuracy. CNBV Anexo 71 references **NIST FRVT methodology** for deduplication (S). Ask vendors for their FRTE 1:1 submission name and FNMR at FMR=1e-6 on the Visa/Border sets.
- **Injection attacks** (virtual cameras, emulators, hooked SDKs, deepfake streams) are **out of scope for ISO 30107-3**.
  - **CEN/TS 18099** (approved October 2024) is the operational spec for **biometric data injection attack detection (IAD)**.
  - **ISO/IEC 25456** is still a **working draft** (ISO page: "under development"). Maturity is estimated at about 2 years.
  - Lab testing now exists from **BixeLab** (CEN/TS 18099), **Ingenium**, and **iBeta IAD testing (launched 2026)** (S).
  - Sources: [ISO 25456](https://www.iso.org/standard/90473.html), [Biometric Update EAB 2026-07](https://www.biometricupdate.com/202607/eab-previews-biometric-injection-attack-detection-standardization-developments), [Persona on CEN/TS 18099](https://withpersona.com/blog/what-is-cen-ts-18099-a-guide-to-the-injection-attack-detection-standard/).
- **Deepfake and virtual-camera risk.** iProov reported an injection-attack surge spreading to iOS in 2026 ([Biometric Update 2026-04](https://www.biometricupdate.com/202604/biometric-injection-attack-surge-spreads-to-ios-iproov-report)). **Web flows (desktop browser) are the weakest channel** because virtual cameras are trivial there. Mitigations:
  - Native SDKs with app attestation (Play Integrity / App Attest).
  - Camera-source and virtual-driver detection, and device-integrity signals (root/jailbreak, emulators, hooking frameworks like Frida).
  - Server-side challenge-response (screen-light "flashmark" / color sequences).
  - Detection of session replay (nonce embedded in capture).
  - Treat **"web + desktop + no attestation" as a lower assurance tier**.

### C.2 Recommended thresholds (starting points; calibrate per vendor)

| Control | Recommended operating point | Note |
|---|---|---|
| Face 1:1 (selfie vs doc portrait) | **FMR ≤ 1e-4** (consumer fintech), **≤ 1e-5** for high-risk / Level-4-equivalent accounts. Target FNMR ≤ 2–3%. | Vendor "scores" are not comparable. Require each vendor's FMR/FNMR-vs-threshold curve. |
| Face 1:1 vs government image (INE / Serpro / RENAPER / RENIEC) | Use the **registry's own band or score** (Serpro: baixa / alta / altíssima; INE: % similarity) and map it to policy. **MX CNBV: ≥90% "match"** (S) is in the registry/vendor score scale, so document the mapping for auditors. The Incode INE response returns `recognitionConfidence` on a 0–100 scale (e.g. 91.1), which fits this mapping. | Government photos are old and low-quality, so expect higher FNMR. Route failures to manual review, not auto-reject. |
| PAD (liveness) | Vendor with **≥ iBeta L2** letter on the **same SDK version and platform** you ship. Operate at APCER ≈ 0–1% with BPCER ≤ 5%. | Re-validate on major SDK upgrades. |
| IAD | Require the vendor's **CEN/TS 18099 lab report** (BixeLab/Ingenium/iBeta IAD) for 2027 RFPs. Meanwhile require device attestation plus virtual-camera detection. | — |
| Retry policy | Max 3 liveness attempts per session, plus velocity limits per device/document/CPF/CURP | Stops threshold probing |

### C.3 What the regulations imply technically

- **MX CNBV new Anexo 71 (July 2026)** (S) requires:
  - 1:1 facial against INE/SRE/SAT records at ≥90%.
  - ISO/IEC 30107-3 PAD.
  - NIST-referenced dedup.
  - Encryption in transit and at rest.
  - **Logical segregation** of biometric databases.
  - Irreversible deletion.
  - **No sharing or interoperability across institutions.**
  - **Annual internal-audit review**, and employees must enroll their own biometrics.

  **SaaS implications:**
  - Biometric stores must be **per-tenant (per institution) logical silos with per-tenant KMS keys**.
  - **No cross-tenant face dedup/"blocklist" for bank tenants** unless each tenant runs it only within its own data. A global fraud-face network like unico's model is likely **not allowed** for MX banks.
  - Keep an immutable audit log per verification: score, threshold, registry response, SDK version, PAD result.
- **AR "técnicas biométricas rigurosas"** (UIF; exact text U) in practice means:
  - RENAPER 1:1 face via SID/API plus liveness. SID itself uses active liveness (2 random gestures, V).
  - Capture DNI front and back plus the PDF417 and número de trámite for the vigencia check.
  - Keep RENAPER evidence (score, transaction id).
- **CO SFC (CE 006/2025, Concepto 2025-12-18)** (S):
  - Verify against RNEC/authorized operators plus liveness.
  - **Mandatory non-biometric alternative path** (accessibility).
- **BR:** no single biometric rule for fintechs. BCB Res. 4,893 (cyber) and CMN/BCB KYC rules apply, as does the INSS consignado biometric requirement (Datavalid states compliance, V). LGPD Art. 11 II(g) allows processing **sensitive biometric data without consent for "prevenção à fraude e segurança do titular" in identification/authentication** (U, from memory of the LGPD text). Still give notice.

### C.4 Template storage and privacy

- **Store the minimum.** Keep **templates** (vendor-specific, encrypted, tenant-scoped) for re-authentication. Keep **raw images** (selfie, document) only as **KYC evidence** under the record-retention obligation (MX: 10 years under LFPIORPI/CNBV; BR: 5 years under Lei 9.613; CO: 5 years; AR: 10 years under UIF rules (U); CL: 5 years; PE: 10 years (U)). Use separate encryption keys and access controls for images versus templates.
- **Templates are vendor-locked** (not interoperable), so migrating vendors means re-enrolling or re-extracting from raw images. **This is the main reason to keep raw images under strict controls** (alongside the evidence obligation).
- **MX:** the new **LFPDPPP (DOF 2025-03-20)** treats biometrics as sensitive data that requires **express (and written/electronic-signature) consent** (U, from memory). The supervisory function moved from INAI to the Secretaría Anticorrupción y Buen Gobierno.
- **BR:** LGPD Art. 5 II / Art. 11 treats biometrics as sensitive. A DPIA (RIPD) is expected. International transfer (Art. 33) matters if a vendor processes outside BR, so prefer BR-region processing.
- **CL:** **Ley 21.719 is in full force 2026-12-01**, with biometrics as sensitive data (S).
- **AR:** Ley 25.326 (sensitive data); a new data-protection bill is pending (U).
- **CO:** Ley 1581/2012, with biometrics sensitive and explicit consent required (U).
- **PE:** Ley 29733 + DS 016-2024-JUS (U).
- **Architecture:** a biometric vault service per country cell with per-tenant DEKs (envelope encryption with an AWS KMS CMK per tenant per cell), deletion jobs with cryptographic erasure (destroy the DEK), and no biometric data in Kafka payloads (pass references only).

---

## D. Document verification: LATAM ID machine-readable features

| Document | Barcode / QR | MRZ | Chip (NFC, ICAO 9303) | Notes |
|---|---|---|---|---|
| **MX INE (Credencial para Votar), models E–H (2014+)** | **QR codes on the back.** One links to INE validation data; models G/H have a second, denser QR (U). | **3-line TD1-style "OCR zone" on the back** (`IDMEX` + CIC + … + names). The CIC and identificador ciudadano feed INE checks. | **No chip** | **New INE model in production since 2026-06-01** (S). Both models valid. Document-class templates need updating. |
| **MX passport** | — | TD3 MRZ | **Yes, e-passport since 2021** (U) | NFC chip read gives cryptographic authenticity |
| **BR CNH (physical, 2017+)** | **QR code (signed) on the back**, decodable and validated by Serpro "CNH QRCode" (V) | MRZ on newer models (U) | No | **CNH-e / CDT digital** = PDF/app with QR (validated by Serpro). Hopae added digital CNH verification in 2026 (S). |
| **BR CIN (2022+)** | **QR code**: partial offline read, full read needs gov.br (S) | **TD1 MRZ** (S) | Paper version: no. Polycarbonate chip claims are unconfirmed (U). | CPF is the ID number. Old RG valid until 2032-03-01 (S). |
| **BR passport** | — | TD3 | **Yes** (U) | — |
| **CO cédula digital (2020+)** | **Encrypted QR** carrying biometric and security data (S, Registraduría brochure); also a digital version in the app | **MRZ** (TD1, Andean travel use) (S) | Press mentions a "cryptographic chip", but this is **not confirmed by RNEC** (U) | The QR is encrypted, so decoding requires RNEC keys or a certified vendor. |
| **CO cédula amarilla (old hologram)** | **PDF417** (contains name, number, birth date, sex, RH, plus a fingerprint template; partially obfuscated) (U) | No | No | Still in circulation. Widely decoded by vendors. |
| **AR DNI (2012+ "tarjeta")** | **PDF417 on the front.** `@`-separated fields: `número de trámite @ apellido @ nombre @ sexo @ DNI @ ejemplar @ fecha nacimiento @ fecha emisión @ …`. Older formats differ in order and leading fields (U, from memory). | **TD1 MRZ on the back** | Old: no. **New polycarbonate "DNI electrónico" with contactless chip (eMRTD, ICAO), issued since Feb 2026, optional** (S, argentina.gob.ar). | The número de trámite from the PDF417 is needed for the RENAPER vigencia check. |
| **CL cédula (2013 model)** | QR (links to vigencia) and PDF417 on older models (U) | **TD1 MRZ** | **Yes, contactless chip** (S) | — |
| **CL cédula (IDEMIA, 2024-12-16+)** | QR (U) | TD1 MRZ | **Contactless + contact chip, biometric data, passive/active authentication** (S) | **Best LATAM candidate for NFC chip verification** (BAC/PACE with MRZ/CAN) |
| **PE DNIe 3.0 (April 2025; only version issued since Aug 2025)** | — (U) | **TD1 MRZ** | **Dual-interface chip; contactless NFC readable by phones** (S). ICAO eMRTD compliance is claimed by third parties (U). | Older DNIe 1.0/2.0 had **contact-only** PKI chips. The blue/yellow DNI has a PDF417 barcode (U). |

**NFC chip verification.** It is realistically usable in 2026–27 for **CL cédula**, **PE DNIe 3.0**, the **new AR DNI electrónico** (very low penetration because it is optional) and **all e-passports** (MX, BR, CO, AR, CL, PE).
- **Not** usable for MX INE (no chip) or BR CNH/CIN (no chip, U).
- Chip reading needs the **country CSCA certificates**. Check whether each country publishes to the **ICAO PKD**: CL and PE status U. Without the CSCA, you get chip-clone resistance only through Active/Chip Authentication, not Passive Authentication.
- SDKs: Regula, ReadID/Inverid (Signicat), Innovatrics, Jumio/Onfido NFC modules.

---

## E. Business onboarding (KYB) data vendors and UBO discovery

### E.1 Vendors

All vendor facts in this table are UNVERIFIED this session (prior knowledge) unless marked otherwise. Confirm in RFP.

| Vendor | Countries | Data | Notes |
|---|---|---|---|
| **Serpro Consulta CNPJ "QSA"/"Empresa"** | BR | Official QSA from Receita (partners and administrators, with qualification) | **VERIFIED prices** (§A.2). This is the authoritative source for direct shareholders. |
| **Receita "Dados Abertos CNPJ"** (free monthly dump) | BR | All CNPJs plus QSA (partner CPF masked) | Good for building an internal ownership graph. Monthly refresh lag. |
| **BigDataCorp** | BR (+LATAM) | Company and person datasets, ownership graphs, lawsuits, media | Strong in BR. Pay-per-dataset API. No public prices found. |
| **Neoway (B3)** | BR | Company graph, ESG, economic groups | Enterprise |
| **Serasa Experian** | BR | Credit plus company data, partners | — |
| **Boa Vista (Equifax BR)** | BR | Credit/company | — |
| **Escavador / Jusbrasil** | BR | Lawsuits (adverse media/litigation) | — |
| **Buró de Crédito / Círculo de Crédito** | MX | Business credit reports (PM) | Require customer authorization |
| **Kiban, Moffin, Syntage, Nubarium** | MX | SAT (CSF, opinión de cumplimiento 32-D), RFC, acta constitutiva OCR, SIGER lookups, IMSS | Main MX KYB stack. SIGER is web-only. |
| **Datacrédito (Experian CO), TransUnion CO, Informa Colombia** | CO | RUES-based company data, credit | — |
| **Verifik** | CO (+LATAM) | **RUES complete API** (legal reps, activities, establishments) (V, docs) | — |
| **Nosis, Veraz (Equifax AR)** | AR | CUIT, company, authorities, BCRA debtors, judicial | Nosis is strong for SMEs |
| **Equifax PE (Infocorp), Sentinel** | PE | RUC, SUNARP-derived data, credit | — |
| **Equifax CL / Dicom, Floid, SimpleAPI** | CL | SII, CBR, Diario Oficial publications | — |
| **Dun & Bradstreet (and LATAM partners)** | All | D-U-N-S, corporate linkage (global ultimate parent) | Good for cross-border groups. Thin on SMEs. |
| **Moody's Orbis (BvD)** | All | Ownership/UBO calculated by BvD, strongest for larger companies | Expensive. SME coverage in LATAM is weak. |
| **OpenCorporates** | Partial | Coverage of LATAM registries is limited (BR via CNPJ data; CO, MX limited) | Use only as a supplementary source |
| **Didit KYB** | Multi | "Business registry pricing by country and data tier" (V, pricing page) | — |

### E.2 Realistic automated UBO discovery per country

| Country | Official UBO registry accessible to obligated entities? | What you can automate | What still needs customer declaration and documents |
|---|---|---|---|
| **BR** | No public UBO register. Receita requires UBO info in CNPJ registration (IN RFB 1.863/2018), but it is not published (U). | **Best in LATAM.** Recursive QSA traversal through CNPJ (Serpro QSA/Empresa plus open-data graph) to reach natural persons. Calculation is limited because **QSA has no percentage for S.A.s**, and for Ltdas capital share must come from the contrato social. | Ownership %, foreign parents, S.A. shareholders (not in QSA) |
| **MX** | **No.** The SE registry (Art. 33 Bis LFPIORPI) has guidelines pending. SAT UBO info (CFF 32-B) is not accessible. AML rules in force 2026-11-30, with UBO criteria from 2027-03-01 (V). | RFC/CSF status and legal reps from the acta (OCR). SIGER partner names for some companies (web). | Shareholding structure, UBO declaration (≥25% under SHCP rules; CNBV rules for banks/ITFs also use 25%), actas de asamblea updates |
| **CO** | **No** (the DIAN RUB is reserved to authorities). | RUES: legal reps, status | Shareholders of SAS (not in RUES), UBO declaration |
| **AR** | **No** (IGJ/AFIP UBO data not public) | CUIT padrón, IGJ authorities (CABA) | Shareholders, UBO declaration |
| **CL** | **No** public register. RES (empresa en un día) statutes are public. | RES statutes (partners for SpA/Ltda at incorporation), SII | Current cap table (SpA share transfers are not registered publicly) |
| **PE** | **Registro de Beneficiarios Finales (SUNAT, DL 1372)** exists, but access is restricted to authorities (U) | SUNARP partidas (directors, sometimes partners for SRL/SAC at incorporation), SUNAT RUC | UBO declaration |

**Design implication.** KYB must be **declaration-first with automated corroboration**. Collect the UBO tree from the customer, then corroborate each node against registry data (QSA, RUES, actas OCR) and screen every natural person. Only BR supports meaningful automated graph discovery.

---

## F. Device and fraud signals

All rows are U (prior knowledge) except where noted. Confirm in RFP.

| Vendor | Origin | Strength in LATAM onboarding | Notes |
|---|---|---|---|
| **Incognia** | Founded in BR (Recife), US HQ | **Location-behavior and tamper-resistant device ID.** Strong with BR fintechs, food delivery and banks. Detects account-farming and device reuse across identities. | $31M Series B (Bessemer) (S, latamlist). Mobile SDK (best signal on native apps). |
| **ClearSale** | BR | E-commerce chargeback and fraud scoring, onboarding scores, BR network data | Listed in BR. Strong BR consortium data. |
| **Legiti** | BR | ML fraud scoring for BR e-commerce/fintech | Status and ownership in 2026 not verified |
| **SEON** | HU | Email/phone/IP enrichment (social and digital footprint), device fingerprinting, rules. Popular with LATAM fintechs and iGaming. | Self-serve pricing has historically been public (not re-checked) |
| **Sardine** | US | Device and behavior biometrics, plus consortium | LATAM presence via MX/BR fintechs (U) |
| **Fingerprint (fingerprint.com)** | US | Browser/device ID (Smart Signals: VPN, virtual machine, tampering, **virtual camera** detection is not core) | Good for web flows. Commodity pricing. |
| **LexisNexis ThreatMetrix** | US | Global digital-identity network, widely used by LATAM banks | Enterprise, expensive |
| **BioCatch** | IL | Behavioral biometrics, mule and social-engineering detection. Strong with LATAM banks (BR, MX). | Reports LATAM social-engineering fraud +155% and MX ATO +311% (S, via ecosistemastartup) |
| **unico / idwall / CAF** | BR | Bundle device and identity network signals in BR | — |
| **Telco signals: Telesign, Prove, Twilio Lookup, local MNO KYC APIs (e.g. Claro/TIM/Vivo via Open Gateway "KYC Match", "SIM Swap")** | — | Phone tenure, SIM swap, number-to-identity match | GSMA Open Gateway CAMARA APIs (SIM swap, KYC match) are live with several LATAM MNOs (U) |

### F.1 Brazil Pix-fraud signals (relevant to fintech tenants that are Pix participants)

- **DICT fraud markers ("marcação de fraude" / "fundada suspeita").** Since September 2025, participants **must reject Pix key registration for users recognized as fraudsters**. They also cannot proceed with portability or claims for those users (S, Relatório de Gestão do Pix).
- **Res. BCB 546 (2026-01-22)** restricts the return of user data associated with fraud markings through DICT (S).
- **Res. BCB 587/2026** lets a participant create an infraction notice to mark the CPF/CNPJ of a client involved in a suspected-fraud transaction. The participant must notify the user, allow a review request, and answer within **7 days** (S).
- **MED 2.0** (Res. BCB 493/2025, mandatory from 2026-02-02) adds chain tracing of fraudulent funds. Receivers of transactions with an accepted infraction notice are **marked as fraudsters in DICT** even without a refund request. Legacy "Notificação de Infração" endpoints sunset after 2026-05-10 (S, BCB MED 2.0 deck, Fórum Pix 2026-03-26).
- **Onboarding implication.** For BR tenants that are Pix participants:
  - Expose a hook in the onboarding workflow to query the tenant's DICT fraud markers for the CPF/CNPJ during onboarding (tenant credentials).
  - Treat a positive marker as a hard stop plus EDD.
  - Our SaaS cannot query DICT itself.
- Sources: [BCB MED 2.0](https://www.bcb.gov.br/content/estabilidadefinanceira/pix/MED/MED_2-0_Circuito_Pix-Dia_1.pdf), [Fórum Pix 2026-03-26](https://www.bcb.gov.br/content/estabilidadefinanceira/pix/Forum_Pix_Plenaria/20260326-Forum_Pix.pdf), [Relatório de Gestão do Pix 2026](https://www.bcb.gov.br/content/estabilidadefinanceira/pix/relatorio_de_gestao_pix/relatorio_gestao_pix_2026.pdf), [Cescon Barrieu on Res. 587/2026](https://cesconbarrieu.com.br/resolucao-bcb-no-587-2026-novas-obrigacoes-e-impactos-para-participantes-do-pix/). All read via search summaries, so SECONDARY.

### F.2 Recommended signal stack (onboarding)

1. **Device:** native SDK device ID plus integrity signals (Incognia in BR/MX mobile, or Fingerprint for web). Add **virtual-camera, emulator and hooking detection** from the IDV SDK.
2. **Email/phone risk:** SEON (enrichment) or Telesign/Prove (phone). In BR, use phone-to-CPF match from bureaus (Serasa/BigDataCorp).
3. **Velocity and linkage**, tenant-scoped by default. Opt-in cross-tenant consortium only for non-biometric hashed identifiers, and **never biometrics for MX banks** (Anexo 71 bans sharing).
4. **Country-specific:** BR DICT fraud markers (tenant), MX "Lista de personas bloqueadas" (UIF) via the screening module, AR BCRA central de deudores (Nosis/Veraz).

---

## Per-country recommended integration matrix

Latency is a typical p50/p95 estimate (U unless noted). Cost is a published price where available.

### Mexico (MX)

| Check | Source | Primary | Fallback | Expected latency | Cost signal |
|---|---|---|---|---|---|
| Doc authenticity (INE/passport) | Document OCR, security features, QR/OCR-line parse | Incode | Veridas / Regula (self-host) | 2–5 s | Not public |
| Liveness/PAD | Vendor SDK (≥ iBeta L2, prefer L3) | Incode (L3) | FacePhi / Veridas (L2) | 1–3 s client-side | Bundled |
| Face 1:1 vs **INE** + INE data | INE Servicio de Verificación (convenio) | Incode (direct INE, V) | NA-AT / Veridas | **5–7 s (INE, V)**, p95 >15 s | Not public (per convenio) |
| INE fingerprint (branch) | INE | Incode (V) | Bank's own INE convenio | 5–7 s | Not public |
| CURP | RENAPO | Nubarium/Kiban | Didit | 1–5 s, flaky | **Didit $0.20/answered attempt (V)** |
| RFC / CSF | SAT | Kiban / Syntage | CSF upload + QR verify | 2–10 s | Not public |
| Company (acta, partners) | SIGER/RPC (web) + acta OCR | Kiban / Moffin | Manual review | minutes (async) | Not public |
| UBO | **Customer declaration** (no registry) | — | — | — | — |
| Device | Incognia / Fingerprint | SEON | — | <300 ms | Not public |

### Brazil (BR)

| Check | Source | Primary | Fallback | Latency | Cost signal |
|---|---|---|---|---|---|
| CPF status/name | Receita via **Serpro Consulta CPF** | **Serpro direct** | idwall / BigDataCorp / Didit | <1 s | **R$0.66 → R$0.017/query (V)**; Didit $0.40 (V) |
| Face 1:1 vs gov base | **Serpro Datavalid** | Serpro direct | unico / Incode (Serpro face V) | 2–4 s (Serpro: "4 seconds", S) | **~R$1.50/validation (S)**, tiered table not captured |
| Doc OCR (CNH/CIN/RG) + CNH QR | Vendor + Datavalid CNH QR | unico / idwall | CAF / Regula | 2–5 s | Not public |
| Liveness/PAD + IAD | Vendor SDK | idwall (L2) / unico | Incode (L3) | 1–3 s | Bundled |
| CNPJ + QSA | **Serpro Consulta CNPJ** | Serpro direct | BigDataCorp / open-data dump | <1 s | **QSA R$0.87 → R$0.067; Empresa R$1.17 → R$0.108 (V)** |
| UBO graph | QSA recursion + contrato social | Internal graph on open data + Serpro | BigDataCorp / Neoway | async | — |
| Pix fraud marker | DICT (tenant's credentials) | Tenant hook | — | ms | n/a |
| Device | Incognia / ClearSale | Fingerprint | — | <300 ms | Not public |

### Colombia (CO)

| Check | Source | Primary | Fallback | Latency | Cost signal |
|---|---|---|---|---|---|
| Cédula status/data | RNEC ANI | Truora | Didit / Verifik | 1–5 s | **ANI COP 108.81/record (Res. 1500/2026, S)**; Didit $0.20 (V) |
| Biometric vs ANI (fingerprint/face) | RNEC via **certified operator** | Certified operator (Olimpia/Reconoser/AdoTech, U) | — | 3–10 s | Not public |
| Doc OCR (cédula digital QR / old PDF417) | Vendor | Truora / Incode | Regula | 2–5 s | — |
| Company | RUES | Verifik RUES API | Truora / Informa | 2–10 s | Not public |
| RUT | DIAN (public lookup) / RUT PDF | Verifik | Manual | async | — |
| UBO | **Declaration** (RUB not accessible) | — | — | — | — |

### Argentina (AR)

| Check | Source | Primary | Fallback | Latency | Cost signal |
|---|---|---|---|---|---|
| DNI vigencia (DNI + sex + trámite) | RENAPER SID/API | VU Security | Incode (BYO RENAPER) / Didit | 1–3 s | **ARS 60 → 20/tx (Res. 19/2026, S)**; Didit $0.20 (V) |
| Face 1:1 vs RENAPER | RENAPER SID | VU Security | Incode (BYO credentials, V) | 2–5 s | Facial row not legible (U) |
| CUIT/CUIL | ARCA padrón WS | Nosis | Direct ARCA WS (WSAA certificate) | <2 s | Free (ARCA) |
| Company | IGJ / provincial + Nosis | Nosis | Veraz | async | Not public |

### Chile (CL)

| Check | Source | Primary | Fallback | Latency | Cost signal |
|---|---|---|---|---|---|
| Cédula vigencia | SRCeI public query | Vendor proxy (Truora/Incode) | Didit | 1–5 s | Didit $0.20 (V) |
| Chip-based doc authenticity + face vs chip photo | Cédula NFC chip | Regula / vendor NFC module | Autentia fingerprint vs SRCeI (U) | 5–15 s (user tap) | License |
| RUT / tax status | SII | Floid / SimpleAPI | Manual | 2–10 s | — |
| Company | RES (public) / CBR | Equifax CL / manual | — | async | Per CBR certificate |

### Peru (PE)

| Check | Source | Primary | Fallback | Latency | Cost signal |
|---|---|---|---|---|---|
| DNI data | RENIEC (convenio) | Truora / Didit | Equifax PE | 1–5 s | Didit $0.20 (V) |
| Face 1:1 vs RENIEC | RENIEC facial web service (convenio, PE entity) | FacePhi / Veridas | — | 2–5 s | Facial tariff not published. **Fingerprint: S/1.50 → S/0.14 (V)** |
| DNIe 3.0 chip | NFC | Regula / vendor NFC | — | 5–15 s | License |
| RUC | SUNAT padrón (bulk, free) | Internal bulk load | apis.net.pe | <100 ms (local) | Free |
| Company | SUNARP | Equifax PE / Sentinel | Manual | async | Per copy |

---

## Gaps and open questions (to resolve before design freeze)

1. **CNBV Anexo 71 (DOF 2026-07-01) primary text not read.** Confirm the 90% threshold wording, whether it applies to **remote onboarding** (Arts. 51 Bis 6–8) or only in-person, and whether ITFs/SOFIPOs have equivalent changes.
2. **INE access for a SaaS provider.** INE's convenio list is mostly banks. We need a legal opinion on whether we can resell INE biometric results through a vendor (Incode/NA-AT/Veridas) to non-bank fintech tenants, and on how consent and convenio flow-down work.
3. **RENAPO biometric CURP.** Will RENAPO expose 1:1 face/fingerprint verification to private entities? Nothing found.
4. **Datavalid tiered price table** was not captured (JS-rendered). Request it from Serpro and confirm whether Datavalid now matches against the CIN/TSE base in addition to CNH.
5. **RENAPER facial-validation tariff row** (Res. 19/2026) was not legible. Get the Boletín Oficial annex.
6. **RENIEC facial web service tariff** and whether a non-Peruvian SaaS can sign (requires a PE legal entity, so plan a PE subsidiary or rely on tenants' convenios).
7. **Colombia certified biometric operators list** (RNEC) and their per-match fees. The Res. 1500/2026 full text was not readable via curl.
8. **iBeta status for unico, Truora, AuthenticID**: not found. Request lab reports. **Injection-attack (CEN/TS 18099) reports** for all shortlisted vendors.
9. **Vendor data residency.** No vendor's MX or BR regions were verified this session. RFP must ask for in-country processing for biometric data (BR LGPD transfer and MX CNBV segregation).
10. **NFC chip PKI**: whether CL, PE, AR and MX publish CSCA certificates to the ICAO PKD (needed for passive authentication).
11. **UIF Argentina current text** on non-face-to-face biometric onboarding ("técnicas biométricas rigurosas") was not retrieved.
12. **SAT** has no documented B2B RFC/CSF API. Confirm the e.firma-based bulk validation limits and whether SAT blocks vendor scraping.
13. **SIGER 2.0** has no API. Confirm fees for certified copies and whether bulk/institutional access exists.
14. **Peru UBO registry (SUNAT DL 1372) accessibility** for obligated entities is unconfirmed.
15. Several facts above are tagged **U** (from prior knowledge, especially the document barcode field layouts in §D and the KYB vendor descriptions in §E). Validate them during vendor PoCs.

