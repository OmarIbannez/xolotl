# 02 — Business onboarding (KYB & UBO)
Source: https://app.notion.com/3fde5630e5e381afbeb3c7f4bf0fdbf5

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Depends on:** <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e3813385c3c590786261ed"/> (for individuals behind the company) · **Calls:** <mention-page url="https://app.notion.com/p/3fde5630e5e3814da2f5ce4e03addb79"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/>
**Evidence:** <mention-page url="https://app.notion.com/p/3fde5630e5e381989aacdee9e75c45db"/> · regulatory research in `../research/raw/`
---
## Executive summary
**What it does:** onboards **companies** as customers. It answers the three questions every regulator asks:
1. Is this company real and in good standing?
2. Who can act for it (legal representatives, holders of powers of attorney)?
3. **Who ultimately owns or controls it** (the "beneficial owners")?
**How it works:**
- The client company **declares its ownership tree** in a guided form or by API.
- We **corroborate** that tree against official registries and the company's founding documents, which we read with OCR.
- Every person in the tree is screened. Legal representatives and beneficial owners go through identity verification.
- Effective ownership through chains of companies is **calculated automatically** and compared with each country's legal threshold:
<table header-row="true">
<tr>
<td>Country</td>
<td>Threshold</td>
</tr>
<tr>
<td>Mexico</td>
<td>25%</td>
</tr>
<tr>
<td>Argentina, Chile, Peru</td>
<td>10%</td>
</tr>
<tr>
<td>Colombia</td>
<td>5%</td>
</tr>
<tr>
<td>Brazil</td>
<td>Risk-based, up to 25%</td>
</tr>
</table>
**Why it's declaration-first:**
- **No LATAM country lets private companies query its official beneficial-owner registry.** Mexico's isn't live yet; Colombia's and Peru's are restricted to authorities.
- **Brazil is the exception where we can discover ownership automatically.** We walk the public tax-registry partner lists (Receita "QSA") company by company.
- So for most countries the product's job is to **collect, check and document**.
**Why it matters:**
- Beneficial ownership is where LATAM countries score worst in international evaluations, and it's a focus of the 2025–2028 reforms. Mexico's new rules (the 25% threshold and the control test) apply from March 2027.
- Fintechs that serve SMEs and merchants need this to be fast, because a manual process kills conversion.
**Build:** \~10 engineer-weeks for v1 (Mexico + Brazil).
---
## 1. Requirements
<table header-row="true">
<tr>
<td>#</td>
<td>Requirement</td>
<td>Source</td>
</tr>
<tr>
<td>B1</td>
<td>Collect company data per country (MX fintech list: name, purpose, nationality, RFC + homoclave, **e.firma serial**, address, incorporation date, administrators/representatives, CLABE in the company's name, deed, CSF, proof of address, powers of attorney)</td>
<td>MX ITF DCG Art. 11 II; BR Circ. 3.978 (name + CNPJ, then qualification: head office, revenue); CO, AR, CL, PE equivalents</td>
</tr>
<tr>
<td>B2</td>
<td>Verify existence/status in registries (SAT/SIGER, Receita/Serpro CNPJ, RUES/DIAN, ARCA/IGJ, SII/RES, SUNAT/SUNARP)</td>
<td>Country rules</td>
</tr>
<tr>
<td>B3</td>
<td>Identify legal representatives and attorneys, with powers and validity</td>
<td>All</td>
</tr>
<tr>
<td>B4</td>
<td>**Beneficial owners**: ownership ≥ threshold (direct + indirect), **control by other means**, fallback senior managing official; **a legal person cannot be the beneficial owner**; trusts: settlor, trustee, beneficiaries, any controller</td>
<td>MX LFPIORPI 2025 / RCG (from 01-03-2027), AR Res. 112/2021, CL Circ. 62, PE DL 1372, CO ET 631-5, BR Circ. 3.978</td>
</tr>
<tr>
<td>B5</td>
<td>Verify beneficial owners' and representatives' identity (KYC flow from 01, lighter tier if policy allows)</td>
<td>All</td>
</tr>
<tr>
<td>B6</td>
<td>Screen company, representatives, beneficial owners, significant shareholders, directors</td>
<td>All</td>
</tr>
<tr>
<td>B7</td>
<td>Track changes: AR client must notify beneficial-owner changes within 30 days; CL: delay \> 40 business days in providing beneficial-owner info is a red flag; periodic refresh by risk</td>
<td>AR, CL</td>
</tr>
<tr>
<td>B8</td>
<td>Exemptions: listed companies (AR, BR, MX), certain public entities, regulated funds (BR Res. 119)</td>
<td>Policy packs</td>
</tr>
<tr>
<td>B9</td>
<td>MX non-financial avisos need a beneficial-owner block (`beneficiario_controlador`) and **`total_fig_juridicas`** (legal arrangements before the beneficial owner) from June 2027</td>
<td>MX formats DOF 24-09-2026</td>
</tr>
</table>
---
## 2. Architecture & flow
```javascript
Tenant / business user
  │ 1. create KYB application (CNPJ / RFC / NIT / CUIT / RUT / RUC)
  ▼
KYB API ──► KybWorkflow (Temporal)
  2. Registry lookup (connector): status, name, address, activity, (BR: QSA partners)
  3. Prefill form; request documents (deed / contrato social / estatuto, powers, CSF, proof of address)
  4. Document AI: OCR + field extraction (shareholders, capital, representatives, powers, dates)
  5. Ownership declaration (guided tree builder) ──► reconcile with registry + documents → discrepancies
  6. Graph engine: effective ownership, control tests, beneficial owners per policy
  7. Natural persons → KYC sub-applications (01) for reps + beneficial owners (light or full tier)
  8. Screening (03) of all entities + persons; Risk (04) for the company
  9. Decision engine → APPROVE / REVIEW / NEEDS_INFO / REJECT  ─► relationship ACTIVE, monitoring
```
### 2.1 Country connectors & corroboration sources (from research)
<table header-row="true">
<tr>
<td>Country</td>
<td>Status / basic</td>
<td>Ownership / reps</td>
<td>Documents</td>
<td>Beneficial-owner registry access</td>
</tr>
<tr>
<td>**BR**</td>
<td>**Serpro Consulta CNPJ** (Básica R\$0.66→0.025; QSA R\$0.87→0.067; Empresa R\$1.17→0.108)</td>
<td>**QSA recursion** (partners and administrators; **no % for S.A.s**; Ltda % from the contrato social); Receita open-data dump for the internal graph</td>
<td>Contrato social / estatuto (OCR); Juntas certidões (per state)</td>
<td>None public, so this is **automated discovery + declaration**</td>
</tr>
<tr>
<td>**MX**</td>
<td>SAT RFC validity + **CSF upload (QR → SAT validation URL)**; SAT 69-B check</td>
<td>**Acta constitutiva / asambleas OCR**; SIGER (web only, no API) for folio and partners when available</td>
<td>Acta, poderes, CSF, proof of address</td>
<td>Secretaría de Economía registry **not operational**; SAT beneficial-owner data not accessible, so **declaration**</td>
</tr>
<tr>
<td>**CO**</td>
<td>RUES via Verifik/Truora; DIAN RUT (PDF + verification code)</td>
<td>RUES legal reps; SAS shareholders **not** in RUES</td>
<td>Certificado de Existencia y Representación Legal (CERL), estatutos</td>
<td>DIAN RUB **reserved**, so declaration</td>
</tr>
<tr>
<td>**AR**</td>
<td>ARCA padrón (`ws_sr_padron_a5/a13` via WSAA certificate) / Nosis</td>
<td>IGJ (CABA) authorities; provincial registries</td>
<td>Estatuto, actas, poderes</td>
<td>Not accessible, so declaration</td>
</tr>
<tr>
<td>**CL**</td>
<td>SII (situación tributaria) via Floid/SimpleAPI</td>
<td>RES statutes (simplified-regime companies); CBR certificates</td>
<td>Estatutos, escritura</td>
<td>None, so declaration (+ UAF beneficial-owner form)</td>
</tr>
<tr>
<td>**PE**</td>
<td>SUNAT RUC padrón (**free daily bulk** → local lookup)</td>
<td>SUNARP partida (directors, sometimes partners)</td>
<td>Escritura, vigencia de poder</td>
<td>SUNAT DL 1372 registry restricted, so declaration</td>
</tr>
</table>
---
## 3. Ownership & control engine
### 3.1 Graph model
- **Nodes** = parties (persons, organizations, trusts/arrangements). **Edges** = `party_relationship` rows: SHAREHOLDER with %, CONTROLLER with basis, LEGAL_REP, ATTORNEY, DIRECTOR, TRUST_\* roles.
- Every edge records **provenance**: DECLARED, REGISTRY (with connector check id), DOCUMENT (document id + page), or ANALYST. It also records a validity window.
### 3.2 Effective ownership algorithm
```python
def effective_ownership(org_id, as_of, max_depth=10) -> dict[PartyId, Decimal]:
    """Sum of products of % along all simple paths from each natural person to org_id."""
    result = defaultdict(Decimal)
    stack = [(org_id, Decimal(1), frozenset([org_id]), 0)]
    while stack:
        node, factor, seen, depth = stack.pop()
        for edge in shareholders_of(node, as_of):              # edges into `node`
            f = factor * edge.pct / 100
            if edge.owner in seen:                              # cycle (cross-holdings)
                flag_cycle(org_id, edge); continue
            if is_natural_person(edge.owner):
                result[edge.owner] += f
            elif depth + 1 < max_depth:
                stack.append((edge.owner, f, seen | {edge.owner}, depth + 1))
            else:
                flag_depth_exceeded(org_id, edge)
    return result
```
- **Cycles** (cross-holdings) are flagged for analyst review, not solved automatically. For simple cycles, a matrix method (I − A)⁻¹ is a later option.
- **Unknown %** (e.g. BR S.A. partners without %) produces an `UNKNOWN_PCT` gap and blocks auto-approval. The tenant must supply the % from the share register or the estatuto.
- **Bearer shares / nominee arrangements** are flagged as high risk; the declaration asks about them explicitly.
### 3.3 Beneficial-owner determination (policy-driven)
```javascript
1. owners = {p : effective_ownership(p) ≥ policy.ubo.ownership_threshold_pct}
2. controllers = persons with CONTROLLER edges (voting agreements, board appointment rights, contracts,
                 family groups acting jointly — e.g. CL joint-action pacts)
3. ubos = owners ∪ controllers
4. if ubos == ∅: ubos = {senior managing official(s)}  basis = SENIOR_MANAGER_FALLBACK
5. trusts: settlor, trustee(s), protector, beneficiaries (≥ threshold or class), any natural person with effective control
6. exemptions: listed company / regulated entity (policy) → record exemption basis, still identify reps
```
- The result is saved as a `ubo_determination` with the policy version. Mexico's non-financial reporting uses `basis` and `total_fig_juridicas` (count of legal entities/trusts on the path).
### 3.4 Reconciliation
Each declared edge is compared to registry and document evidence:
<table header-row="true">
<tr>
<td>Situation</td>
<td>Outcome</td>
</tr>
<tr>
<td>Declared partner is in the QSA/acta</td>
<td>`CORROBORATED`</td>
</tr>
<tr>
<td>Declared partner is not in the registry</td>
<td>`UNCORROBORATED` (allowed if the source can't show it, e.g. CO SAS shareholders, CL SpA transfers)</td>
</tr>
<tr>
<td>Registry or document shows a partner who wasn't declared</td>
<td>**`UNDECLARED_PARTY`** (blocking; must be explained)</td>
</tr>
<tr>
<td>Declared % differs from the document</td>
<td>`PCT_MISMATCH` (tolerance ±1 point)</td>
</tr>
<tr>
<td>Registry shows a representative not declared, or vice versa</td>
<td>`REP_MISMATCH`</td>
</tr>
</table>
Discrepancies become tasks for the business user (NEEDS_INFO) or the analyst.
---
## 4. Document AI
<table header-row="true">
<tr>
<td>Document</td>
<td>Extraction targets</td>
</tr>
<tr>
<td>MX acta constitutiva / asamblea</td>
<td>Company name, folio mercantil, notary + deed number/date, capital and **shareholders with shares/%**, administrators, **poderes** (type: actos de dominio / administración / pleitos y cobranzas / títulos de crédito; limits)</td>
</tr>
<tr>
<td>MX CSF (Constancia de Situación Fiscal)</td>
<td>RFC, name, regime, address, status; **QR → SAT validation URL** fetched and compared</td>
</tr>
<tr>
<td>BR contrato social / estatuto</td>
<td>Partners and **quotas** (Ltda), administrators, capital, object</td>
</tr>
<tr>
<td>CO Certificado de Existencia y Representación Legal</td>
<td>Legal reps, powers, capital (for some types)</td>
</tr>
<tr>
<td>Powers of attorney (all)</td>
<td>Grantor, attorney, scope, validity, notary</td>
</tr>
</table>
**Approach:**
- A vendor OCR or layout model (Textract/Document AI class) plus an **LLM structured extraction** into a strict Pydantic schema. This is phase 1 for MX actas, where variability is high.
- **Human-in-the-loop:** every extracted field carries a confidence and a page/bounding-box reference, and low-confidence fields go to review.
- **Residency:** extraction models must run in-region (or self-hosted) for documents containing PII. ⚠️ Vendor region check required.
---
## 5. Decision rules (example, MX)
```yaml
ruleset: mx-kyb-default
version: 2
rules:
  - { id: STATUS_INACTIVE, when: registry.status not in [ACTIVE], then: REJECT, reason: COMPANY_NOT_ACTIVE }
  - { id: EFOS, when: screening.has(list=MX_SAT_69B, status in [PRESUNTO, DEFINITIVO]), then: MANUAL_REVIEW, reason: SAT_69B }
  - { id: SANCTIONS, when: screening.has_potential_match(topic=sanction, any_party=true), then: MANUAL_REVIEW }
  - { id: UNDECLARED, when: reconciliation.has(UNDECLARED_PARTY), then: NEEDS_INFO }
  - { id: PCT_UNKNOWN, when: ubo.gaps contains UNKNOWN_PCT, then: NEEDS_INFO }
  - { id: UBO_KYC, when: any(ubo.kyc_status != APPROVED), then: PENDING }
  - { id: REPS_KYC, when: any(rep.kyc_status != APPROVED), then: PENDING }
  - { id: POWERS, when: not signer_has_powers(actos_de_administracion), then: NEEDS_INFO, reason: INSUFFICIENT_POWERS }
  - { id: HIGH_RISK, when: risk.rating == HIGH, then: MANUAL_REVIEW, reason: EDD_REQUIRED }
  - { id: OK, when: all_required_present, then: APPROVE }
```
---
## 6. Data model
Builds on `party`, `organization`, `party_relationship`, `ubo_determination`, `document` (<mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> §5).
```sql
CREATE TABLE kyb_application (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    relationship_id   uuid NOT NULL,              -- customer_relationship of the company
    organization_id   uuid NOT NULL,              -- party (organization)
    policy_version_id uuid NOT NULL,
    workflow_id       text NOT NULL,
    status            text NOT NULL,              -- IN_PROGRESS, NEEDS_INFO, PENDING_KYC, MANUAL_REVIEW, APPROVED, REJECTED
    purpose           text NOT NULL DEFAULT 'NEW',-- NEW, REFRESH, CHANGE_NOTIFIED
    decision_reasons  text[] NOT NULL DEFAULT '{}',
    decided_by        text, decided_at timestamptz
);

ALTER TABLE party_relationship
    ADD COLUMN provenance        text NOT NULL DEFAULT 'DECLARED',   -- DECLARED, REGISTRY, DOCUMENT, ANALYST
    ADD COLUMN evidence_check_id uuid,                               -- verification_check (registry)
    ADD COLUMN evidence_doc_id   uuid,
    ADD COLUMN evidence_locator  jsonb,                              -- {page, bbox}
    ADD COLUMN corroboration     text,                               -- CORROBORATED, UNCORROBORATED, CONTRADICTED
    ADD COLUMN shares            numeric,                            -- raw share count where known
    ADD COLUMN share_class       text,
    ADD COLUMN powers            jsonb;                              -- for LEGAL_REP / ATTORNEY: scope, limits, validity

CREATE TABLE registry_snapshot (                                     -- what the registry said, when
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    organization_id uuid NOT NULL,
    source        text NOT NULL,                  -- SERPRO_CNPJ_QSA, SAT_CSF, RUES, ARCA_PADRON_A5, SII, SUNAT_RUC …
    check_id      uuid NOT NULL,                  -- verification_check
    data          jsonb NOT NULL,                 -- normalized (status, address, activities, partners[], reps[])
    fetched_at    timestamptz NOT NULL
);

CREATE TABLE ownership_declaration (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    kyb_application_id uuid NOT NULL REFERENCES kyb_application(id),
    declared_by     text NOT NULL,                -- business user identity (person party id + signature)
    tree            jsonb NOT NULL,               -- as submitted (nodes/edges)
    signed_at       timestamptz NOT NULL,
    signature_ref   text                          -- e-signature evidence
);

CREATE TABLE reconciliation_issue (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    kyb_application_id uuid NOT NULL REFERENCES kyb_application(id),
    kind            text NOT NULL,                -- UNDECLARED_PARTY, PCT_MISMATCH, REP_MISMATCH, UNKNOWN_PCT, CYCLE, DEPTH_EXCEEDED, BEARER_SHARES
    details         jsonb NOT NULL,
    blocking        boolean NOT NULL,
    status          text NOT NULL,                -- OPEN, EXPLAINED, RESOLVED, WAIVED
    resolution_note text,
    resolved_by     text
);

CREATE TABLE document_extraction (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    document_id   uuid NOT NULL REFERENCES document(id),
    model         text NOT NULL,                  -- extractor + version
    schema_code   text NOT NULL,                  -- 'MX_ACTA_V1', 'BR_CONTRATO_SOCIAL_V1'
    fields        jsonb NOT NULL,                 -- [{name, value, confidence, page, bbox}]
    reviewed_by   uuid, reviewed_at timestamptz
);

ALTER TABLE ubo_determination
    ADD COLUMN gaps               text[] NOT NULL DEFAULT '{}',
    ADD COLUMN total_fig_juridicas jsonb,         -- per UBO: count of legal entities/trusts on the path (MX)
    ADD COLUMN exemption          text;           -- LISTED, REGULATED_ENTITY, PUBLIC_ENTITY, NULL
```
---
## 7. API
<table header-row="true">
<tr>
<td>Method</td>
<td>Path</td>
<td>Purpose</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/kyb/applications`</td>
<td>Create: `{external_ref, country, tax_id, product}` → prefilled registry data + required documents list</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/kyb/applications/{id}/documents`</td>
<td>Upload deed / statutes / powers / CSF</td>
</tr>
<tr>
<td>`PUT`</td>
<td>`/v1/kyb/applications/{id}/ownership`</td>
<td>Submit the ownership tree (nodes: persons/entities/trusts; edges: % / control / roles)</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/kyb/applications/{id}`</td>
<td>Status, reconciliation issues, beneficial owners, pending KYC links</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/kyb/applications/{id}/issues/{issue}/explain`</td>
<td>Business user's explanation for a discrepancy</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/businesses/{id}/ownership-changes`</td>
<td>Notify a change (AR 30-day obligation) → refresh workflow</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/businesses/{id}/ubo`</td>
<td>Current beneficial-owner determination + graph (for tenant UI)</td>
</tr>
</table>
**Hosted flow:** a **business onboarding web app** (white-label) for the client's business users, covering the tree builder, document upload and invitations to beneficial owners/representatives so each completes their own KYC from their phone.
**Webhooks:**
- `kyb.application.updated`
- `kyb.application.decided`
- `kyb.ubo.changed`
- `kyb.kyc_invite.sent`
---
## 8. Periodic refresh & monitoring
- **Refresh schedule** from risk (04) and country rules. On refresh: re-pull the registry snapshot, diff it, and re-reconcile.
- **BR continuous monitoring:** monthly diff of the Receita open-data QSA for all corporate customers. Partner changes open a refresh.
- **Registry status monitoring:** CNPJ "baixada/inapta", SAT 69-B listing, RUES cancellation → alert.
## 9. Non-functional
<table header-row="true">
<tr>
<td>Item</td>
<td>Target</td>
</tr>
<tr>
<td>Registry lookup</td>
<td>p95 \< 3 s (Serpro \< 1 s); async for slow sources (SIGER, Juntas, CBR)</td>
</tr>
<tr>
<td>Graph computation</td>
<td>\< 200 ms for ≤ 200 nodes</td>
</tr>
<tr>
<td>Straight-through rate (BR, simple Ltda with 2 partners)</td>
<td>≥ 60% auto-approved (target, validate with design partner)</td>
</tr>
</table>
## 10. Testing
- **Ownership algorithm property tests:** sums ≤ 100% per level; cycle detection; indirect chains (A owns 50% of B, which owns 60% of C → A has 30% of C).
- **Policy golden tests per country:** same structure → different beneficial owners (e.g. CO 5% vs MX 25%).
- **Document extraction eval set:** 100 annotated MX actas and BR contratos; field-level precision/recall.
## 11. Build plan
<table header-row="true">
<tr>
<td>Milestone</td>
<td>Scope</td>
<td>Eng-weeks</td>
</tr>
<tr>
<td>KB-1</td>
<td>KYB application + workflow, ownership graph model, effective-ownership + beneficial-owner engine, reconciliation</td>
<td>3</td>
</tr>
<tr>
<td>KB-2</td>
<td>BR: Serpro CNPJ/QSA connectors, QSA recursion, open-data graph loader</td>
<td>2</td>
</tr>
<tr>
<td>KB-3</td>
<td>MX: CSF QR validation, SAT 69-B, acta extraction (LLM + review UI), powers model</td>
<td>3</td>
</tr>
<tr>
<td>KB-4</td>
<td>Hosted business web app (tree builder, invites to KYC), decision rules, webhooks</td>
<td>2</td>
</tr>
<tr>
<td>**Total v1 (MX + BR)**</td>
<td></td>
<td>**≈ 10**</td>
</tr>
<tr>
<td>Per extra country</td>
<td>CO (RUES), AR (ARCA/IGJ), CL (SII/RES), PE (SUNAT bulk/SUNARP)</td>
<td>1.5–2 each</td>
</tr>
</table>
## 12. Open questions
1. SIGER (MX) has no API. Is a licensed aggregator (Kiban/Moffin) acceptable, or do we rely on actas only?
2. LLM document extraction hosting with in-region residency (MX/BR).
3. Default for "uncorroborated but not contradicted" edges in countries without registry visibility: accept with declaration signature (proposal) vs require documents always.
4. When Mexico's Secretaría de Economía beneficial-owner registry goes live, will obligated entities get query access? Monitor.
