# 🪪 01 — Customer onboarding (KYC)
Source: https://app.notion.com/3fde5630e5e3813385c3c590786261ed

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Depends on:** <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/> · **Calls:** <mention-page url="https://app.notion.com/p/3fde5630e5e3814da2f5ce4e03addb79"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/>
**Evidence:** <mention-page url="https://app.notion.com/p/3fde5630e5e381989aacdee9e75c45db"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e381ad879bdf8c29dfbf98"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38109bb2fd118304e5427"/>
---
## Executive summary
**What it does:** lets a fintech's new customer prove who they are from a phone or browser in a few minutes. It also produces a record the regulator will accept as proof that the fintech knew its customer.
**How it works:**
1. The customer photographs their ID and takes a selfie.
2. We check the ID is genuine and the person is live (not a photo, mask or deepfake), and that the face matches.
3. We **confirm the identity against the government's own records**: Mexico's INE and RENAPO, Brazil's Receita/Serpro, Argentina's RENAPER, and so on.
4. In the background we run sanctions and politically-exposed-person screening, plus a risk score.
5. The system approves, rejects or sends the case to a human reviewer, and records exactly why.
**What makes it LATAM-specific:**
- **Each country's rules decide the steps.** Mexico requires device location and allows lighter "Level 1/2" accounts with fewer checks. Argentina requires "rigorous biometrics" against RENAPER. Colombia requires biometric checks through certified operators, plus a non-biometric alternative.
- **Vendors are pluggable:**
	<table header-row="true">
<tr>
<td>Country</td>
<td>Approach</td>
</tr>
<tr>
<td>Mexico</td>
<td>One vendor with direct INE access</td>
</tr>
<tr>
<td>Brazil</td>
<td>The government's Serpro service directly; it is cheap and openly priced</td>
</tr>
<tr>
<td>Argentina, Peru</td>
<td>The client's own registry agreements, where required</td>
</tr>
	</table>
- **Upgrades as the customer grows.** A customer can start on a low-limit account with minimal checks and "step up" later.
**Cost to the client:** roughly **US\$0.50–2.50 per completed onboarding** in vendor and government fees (country and assurance level dependent), passed through at cost plus margin.
**Build:** \~14 engineer-weeks for v1 (Mexico + Brazil), then \~2–3 weeks per additional country.
**Key risk:** in Mexico, **access to INE biometric checks for non-bank fintechs through a SaaS needs a legal opinion**. INE agreements are mostly with banks.
---
## 1. Requirements
### 1.1 Functional
<table header-row="true">
<tr>
<td>#</td>
<td>Requirement</td>
</tr>
<tr>
<td>O1</td>
<td>Configurable **journeys** per tenant × country × product × tier, generated from policy packs (required fields, checks, thresholds)</td>
</tr>
<tr>
<td>O2</td>
<td>Capture via native SDKs (iOS/Android), web SDK, WhatsApp (phase 2), and **API-only** (tenant captures, we verify)</td>
</tr>
<tr>
<td>O3</td>
<td>Checks: document authenticity + data extraction, liveness/PAD, injection-attack defences, face 1:1 (selfie vs document), **registry data match**, **registry biometric 1:1**, identifier validity (CURP/RFC/CPF/CUIT/RUT/DNI/RUC), device/email/phone risk, duplicate detection, screening, risk score</td>
</tr>
<tr>
<td>O4</td>
<td>**Assurance levels** per check and per application, mapped to legal tiers (§3)</td>
</tr>
<tr>
<td>O5</td>
<td>Decisioning with reason codes: APPROVE / REJECT / MANUAL_REVIEW / NEEDS_INFO</td>
</tr>
<tr>
<td>O6</td>
<td>Manual review console with side-by-side evidence</td>
</tr>
<tr>
<td>O7</td>
<td>**Step-up** (tier upgrade) and **periodic refresh** (re-KYC) reusing prior evidence</td>
</tr>
<tr>
<td>O8</td>
<td>Consent capture per country (biometrics = sensitive data)</td>
</tr>
<tr>
<td>O9</td>
<td>Evidence pack per customer, exportable for regulators/auditors</td>
</tr>
<tr>
<td>O10</td>
<td>Sandbox with deterministic test identities</td>
</tr>
</table>
### 1.2 Country rules that shape the design
<table header-row="true">
<tr>
<td>Country</td>
<td>Rule</td>
<td>Design impact</td>
</tr>
<tr>
<td>MX fintechs</td>
<td>Full data list incl. CURP, CLABE in the customer's name, digitized signature, **device geolocation at opening**; **Level 1 ≤ 750 UDIs/month (balance ≤ 1,000)**, **Level 2 ≤ 3,000 UDIs/month**; data validated against the issuing authority, remote allowed</td>
<td>Geolocation is a mandatory step. Tier-based journeys. CLABE ownership check (§6.6)</td>
</tr>
<tr>
<td>MX banks</td>
<td>Validate CURP vs RENAPO, INE vs INE (CIC/OCR…), passport vs SRE; video conference allowed; **new Level 2 Bis requires liveness for remote opening**; in-person Level 3/4 operations: **facial ≥ 90% vs INE/SRE** (Anexo 71), ISO 30107-3 PAD, **no biometric DB sharing between institutions**</td>
<td>Per-tenant biometric silos; no cross-tenant face dedup for bank tenants</td>
</tr>
<tr>
<td>BR</td>
<td>Identification = name + CPF; qualification = address, income; no relationship before both are done; technology-neutral validation against databases</td>
<td>CPF status gate (Serpro); income/address collection; Datavalid optional by risk</td>
</tr>
<tr>
<td>CO</td>
<td>Remote allowed; biometrics must be verified against RNEC or **certified operators**  • liveness; **must offer a non-biometric alternative**</td>
<td>Alternate journey (e.g. assisted video + documents)</td>
</tr>
<tr>
<td>AR</td>
<td>"Rigorous, storable, auditable, non-manipulable biometric techniques" with anti-spoofing; entity documents a risk analysis; external reviewer gives an opinion</td>
<td>RENAPER SID face 1:1 + liveness; evidence retention; risk analysis template</td>
</tr>
<tr>
<td>CL</td>
<td>Electronic means allowed; due diligence ≥ USD 3,000 occasional</td>
<td>NFC chip read of the cédula as a strong option</td>
</tr>
<tr>
<td>PE</td>
<td>Remote allowed; non-face-to-face = risk factor; RENIEC via convenio (Peru-incorporated entity)</td>
<td>BYO RENIEC convenio; DNIe 3.0 NFC</td>
</tr>
</table>
---
## 2. Architecture
```javascript
┌──────────── Client app ─────────────┐        ┌──────────────────── Cell (e.g. MX) ────────────────────────────┐
│ Tenant backend ──POST /applications─┼──────► │ Onboarding API ── creates Application + Temporal workflow       │
│        │  ◄── session_token ────────┼─────── │                                                                │
│ Our SDK (iOS/Android/Web) ──────────┼──────► │ Capture API (direct upload, presigned to object storage)       │
│   • doc capture (MRZ/QR/PDF417/NFC) │        │   • PAD/IAD evaluation (vendor engine)                         │
│   • selfie + liveness + IAD signals │        │                                                                │
│   • device attestation, geolocation │        │ OnboardingWorkflow (Temporal)                                  │
└─────────────────────────────────────┘        │   journey steps → activities → Connector SDK → vendors/registries│
                                               │   → screening (sync) → risk (sync) → decision engine           │
                                               │   → manual review (signal) → finalize → events/webhooks        │
                                               │                                                                │
                                               │ Biometric vault (per-tenant DEK, separate bucket/role)         │
                                               └────────────────────────────────────────────────────────────────┘
```
### 2.1 Components
<table header-row="true">
<tr>
<td>Component</td>
<td>Responsibility</td>
</tr>
<tr>
<td>**Onboarding API**</td>
<td>Application CRUD, journey resolution, SDK session tokens, webhooks</td>
</tr>
<tr>
<td>**Capture API**</td>
<td>Receives images/videos/NFC dumps from SDKs. Writes to the per-tenant biometric/doc bucket via presigned PUT. Records capture metadata (SDK version, device attestation, camera source)</td>
</tr>
<tr>
<td>**Journey engine**</td>
<td>Turns the effective policy into an ordered step graph with conditions and fallbacks</td>
</tr>
<tr>
<td>**OnboardingWorkflow**</td>
<td>Durable orchestration: retries, timers (abandonment), signals (user submitted / reviewer decided)</td>
</tr>
<tr>
<td>**Connector layer**</td>
<td>Vendor and registry adapters (foundations §9) with routing, fallbacks, cost metering, evidence</td>
</tr>
<tr>
<td>**Decision engine**</td>
<td>Evaluates decision rules over check results → outcome + reason codes</td>
</tr>
<tr>
<td>**Review console**</td>
<td>Manual review queue (shares UI and infrastructure with <mention-page url="https://app.notion.com/p/3fde5630e5e3814783fbe56ba6027f61"/>)</td>
</tr>
</table>
---
## 3. Assurance model
Every check returns an **assurance contribution**. The application's assurance level is the highest level whose requirements are all met.
<table header-row="true">
<tr>
<td>Level</td>
<td>Meaning</td>
<td>Typical composition</td>
</tr>
<tr>
<td>**A0**</td>
<td>Self-declared</td>
<td>Form data only</td>
</tr>
<tr>
<td>**A1**</td>
<td>Document-verified</td>
<td>Authentic document + data extracted + identifier valid (checksum)</td>
</tr>
<tr>
<td>**A2**</td>
<td>Biometric-bound</td>
<td>A1 + liveness (PAD ≥ vendor iBeta L2) + selfie↔document face match</td>
</tr>
<tr>
<td>**A3**</td>
<td>Registry-confirmed</td>
<td>A2 + **registry data match** (RENAPO/Receita/RNEC/RENAPER/SRCeI/RENIEC)</td>
</tr>
<tr>
<td>**A4**</td>
<td>Registry-biometric</td>
<td>A3 + **face (or fingerprint) 1:1 against the government image** (INE, Datavalid, RENAPER SID, RENIEC, RNEC operator) **or** NFC chip passive authentication + face vs chip photo</td>
</tr>
</table>
**Policy packs map legal tiers to the required level:**
<table header-row="true">
<tr>
<td>Tier</td>
<td>Required level</td>
</tr>
<tr>
<td>MX fintech Level 1</td>
<td>A1 (+ geolocation)</td>
</tr>
<tr>
<td>MX fintech Level 2</td>
<td>A2 + CURP registry</td>
</tr>
<tr>
<td>MX full file</td>
<td>A4 (INE) or A3 + video call</td>
</tr>
<tr>
<td>AR remote</td>
<td>A4 (RENAPER SID)</td>
</tr>
<tr>
<td>BR</td>
<td>A3 (CPF regular) by default, A4 (Datavalid) for higher-risk products</td>
</tr>
</table>
**Channel penalties** (from the injection-attack research):
- `WEB` without app attestation caps biometric checks at "A2-web". Policy can require native apps for A4 or add a challenge-response liveness.
- A vendor's **"fallback to scraping"** for registries is **disabled**. If used, it only grants A1 data-match, never A3, so assurance is never silently downgraded.
---
## 4. Journey definitions
```yaml
# generated from policy pack MX/ITF-IFPE v3 + tenant config
journey: mx-wallet-n2
tier: N2
steps:
  - id: consent
    type: CONSENT
    texts: [privacy_notice_mx_lfpdppp_2025, biometric_consent_mx]
  - id: geo
    type: GEOLOCATION
    required: true                    # MX ITF Art. 11
  - id: doc
    type: DOCUMENT_CAPTURE
    accepted: [MX_INE_E, MX_INE_F, MX_INE_G, MX_INE_H, MX_INE_2026, MX_PASSPORT]
    read: [OCR, QR, MRZ]
  - id: selfie
    type: SELFIE_LIVENESS
    mode: PASSIVE
    iad: required
  - id: data
    type: FORM
    fields: [address, occupation_code, email, phone, expected_monthly_volume, source_of_funds]
  - id: checks
    type: PARALLEL
    run:
      - DOC_AUTH
      - FACE_MATCH_DOC
      - { REGISTRY_ID: { source: RENAPO_CURP } }
      - { REGISTRY_BIOMETRIC: { source: INE, when: "doc.class startswith MX_INE and policy.require_a4" } }
      - DEVICE_RISK
      - EMAIL_PHONE_RISK
      - DUPLICATE
  - id: screening
    type: SCREENING                   # sync call to 03
  - id: risk
    type: RISK_SCORE                  # sync call to 04
  - id: decide
    type: DECISION
    ruleset: mx-itf-default@5
fallbacks:
  REGISTRY_BIOMETRIC.INE.unavailable: { action: RETRY_ASYNC, max_wait: PT30M, then: MANUAL_REVIEW }
  FACE_MATCH_DOC.fail: { action: RECAPTURE, max: 2, then: MANUAL_REVIEW }
abandonment: { timeout: P7D, reminders: [PT1H, P1D] }
```
**Journey rules:**
- The journey engine **validates** that the generated journey satisfies the policy, i.e. every required field and check is present for the tier. If not, the tenant config is rejected.
- Tenants can add steps (e.g. a questionnaire) but cannot remove policy-required ones.
---
## 5. Workflow (Temporal)
```python
@workflow.defn
class OnboardingWorkflow:
    @workflow.run
    async def run(self, app_id: UUID) -> Outcome:
        j = await activity(resolve_journey, app_id)                       # policy snapshot frozen here
        await self.wait_for_captures(j, timeout=j.abandonment.timeout)    # signals from Capture API
        results = await gather(*[activity(run_check, app_id, c) for c in j.parallel_checks],
                               return_exceptions=True)
        results = await self.apply_fallbacks(j, results)                  # recapture / async retry / alternate vendor
        scr = await activity(screen_party, app_id)                        # 03 (sync, ≤ 1 s)
        risk = await activity(score_risk, app_id)                         # 04 (sync, ≤ 50 ms)
        outcome = await activity(decide, app_id, j.ruleset)
        if outcome.status == "MANUAL_REVIEW":
            outcome = await workflow.wait_condition(lambda: self.review_decision, timeout=timedelta(days=5))
        await activity(finalize, app_id, outcome)                         # relationship ACTIVE, tier, events, webhook
        return outcome

    @workflow.signal
    def capture_submitted(self, step_id: str, capture_id: UUID): ...
    @workflow.signal
    def review_decided(self, decision: ReviewDecision): ...
```
- **Idempotency:** each `run_check` activity is keyed by `(app_id, check_type, attempt)`. Connector results are stored before the activity returns.
- **Registry outages:** INE (5–7 s nominal, outages common) is called with a 20 s timeout, then up to 30 minutes of async retries with backoff before falling back to manual review. The customer sees "We're confirming your identity; we'll notify you".
- **PII handling:** workflow inputs and outputs carry **IDs only**. Activities load and decrypt data in-worker, and Temporal payloads are encrypted by a codec.
---
## 6. Checks — technical detail
### 6.1 Document authenticity & extraction
- **Vendor-agnostic ****`DocResult`****:**
	- document class (incl. **new INE model 2026** alongside E–H);
	- extracted fields with confidence;
	- **machine-readable cross-checks**: MRZ checksum, QR/PDF417 parse, QR signature (BR CNH);
	- security-feature verdicts, tamper signals;
	- overall `PASS/FAIL/REVIEW`.
- **Our own consistency checks,** independent of the vendor:
	- OCR fields vs MRZ/QR fields;
	- name/DOB vs identifier structure (CURP encodes name initials, DOB, sex, state);
	- expiry; minimum age (policy).
- **NFC chip read** (CL cédula, PE DNIe 3.0, AR new DNI, all e-passports) via Regula or a vendor NFC module:
	- **Passive Authentication** where the country CSCA is available, plus Active/Chip Authentication.
	- The chip photo becomes the reference for the face match, giving **A4 without a registry biometric API**.
### 6.2 Identifier validators (pure functions, no I/O)
<table header-row="true">
<tr>
<td>ID</td>
<td>Validation</td>
</tr>
<tr>
<td>**CURP** (MX)</td>
<td>Regex `^[A-Z]{4}\d{6}[HMX][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[A-Z\d]\d$`; check digit: alphabet `0-9A-NÑO-Z`, `sum(val(c_i) × (18 − i)) for i in 0..16`, `dv = (10 − sum mod 10) mod 10`; cross-check DOB/sex/state vs data</td>
</tr>
<tr>
<td>**RFC** (MX)</td>
<td>12 (PM) / 13 (PF) chars, structure + homoclave check digit (mod 11)</td>
</tr>
<tr>
<td>**CPF** (BR)</td>
<td>11 digits, two mod-11 check digits; reject repeated digits</td>
</tr>
<tr>
<td>**CNPJ** (BR)</td>
<td>14 chars (alphanumeric CNPJ from July 2026 ⚠️ verify), mod-11 check digits</td>
</tr>
<tr>
<td>**CUIT/CUIL** (AR)</td>
<td>11 digits, weights `5,4,3,2,7,6,5,4,3,2`, mod 11</td>
</tr>
<tr>
<td>**RUT/RUN** (CL)</td>
<td>Body + DV, weights cycling `2..7`, mod 11 → `0-9` or `K`</td>
</tr>
<tr>
<td>**NIT** (CO)</td>
<td>DV with weights `3,7,13,17,19,23,29,37,41,43,47,53,59,67,71` (right to left), mod 11</td>
</tr>
<tr>
<td>**DNI/RUC** (PE)</td>
<td>DNI 8 digits; RUC 11 digits with mod-11 check</td>
</tr>
</table>
### 6.3 Liveness, PAD and injection-attack detection (IAD)
- **Vendor requirements:**
	- iBeta **≥ L2** letter on the shipped SDK version and platform (Incode and Sumsub have L3);
	- a **CEN/TS 18099** IAD lab report (required in 2027 RFPs);
	- an operating point near APCER 0–1%, BPCER ≤ 5%.
- **Our own IAD layer:**
	- native app attestation (Play Integrity / App Attest);
	- emulator, root and hooking (Frida) detection from the SDK;
	- virtual-camera detection;
	- a **server nonce** embedded in each capture session;
	- **max 3 liveness attempts per session**, plus velocity limits per device, document and identifier.
### 6.4 Face matching
<table header-row="true">
<tr>
<td>Match</td>
<td>Rule</td>
</tr>
<tr>
<td>**Selfie ↔ document**</td>
<td>Vendor score mapped to calibrated FMR. Policy: FMR ≤ 1e-4 by default, ≤ 1e-5 for high-risk tiers. **We store the vendor score, the threshold and the FMR mapping version**</td>
</tr>
<tr>
<td>**Selfie ↔ registry** (INE / Datavalid / RENAPER / RENIEC)</td>
<td>Use the registry's own scale: INE `%`, Serpro `baixa/alta/altíssima`  • similarity, RENAPER positive/negative + %. **MX banks: ≥ 90% per Anexo 71** (⚠️ secondary source, mapping documented). Failures go to **manual review, not auto-reject** (old government photos → higher false non-match rate)</td>
</tr>
</table>
### 6.5 Registry checks (per country — see the matrix in the research file)
<table header-row="true">
<tr>
<td>Country</td>
<td>Data match (A3)</td>
<td>Biometric (A4)</td>
<td>Access model</td>
</tr>
<tr>
<td>MX</td>
<td>RENAPO CURP (vendor: Nubarium/Kiban/Didit), INE data</td>
<td>**INE face/fingerprint** via Incode (direct, documented) / Veridas / NA-AT</td>
<td>Platform contract via vendor; **BYO convenio** for banks. ⚠️ Legal opinion on resale to non-bank fintechs</td>
</tr>
<tr>
<td>BR</td>
<td>**Serpro Consulta CPF** (direct, R\$0.66→0.017/query)</td>
<td>**Serpro Datavalid** (\~R\$1.50/validation; no deepfake detection itself, so IAD comes from our layer)</td>
<td>Platform contract direct with Serpro</td>
</tr>
<tr>
<td>CO</td>
<td>RNEC ANI data (Truora/Verifik/Didit)</td>
<td>RNEC biometric via **certified operator**</td>
<td>Platform contract with operator</td>
</tr>
<tr>
<td>AR</td>
<td>RENAPER SID vigencia (DNI + sex + número de trámite from PDF417)</td>
<td>**RENAPER SID face**</td>
<td>**BYO convenio** per tenant (Incode requires it) or VU Security</td>
</tr>
<tr>
<td>CL</td>
<td>SRCeI vigencia (public query via vendor)</td>
<td>**NFC chip** (cédula) or Autentia fingerprint</td>
<td>Platform</td>
</tr>
<tr>
<td>PE</td>
<td>RENIEC data</td>
<td>**RENIEC facial WS** or DNIe 3.0 NFC</td>
<td>**BYO convenio** (Peru entity needed)</td>
</tr>
</table>
### 6.6 Account-ownership & contact checks
- **MX CLABE in own name:** a penny-drop SPEI transfer via the tenant, **or** a CEP/banking-API name check. The result is stored as a `verification_check` of type `ACCOUNT_OWNERSHIP`.
- **BR:** Pix key ownership via the **tenant's** DICT access (we cannot query DICT), plus DICT fraud markers for Pix-participant tenants as a hard stop.
- **Email/phone:** OTP verification, plus risk enrichment (SEON / Telesign) and SIM-swap (Open Gateway CAMARA where available).
### 6.7 Device risk
- SDK device ID + integrity signals (Incognia for mobile in BR/MX; Fingerprint for web).
- Velocity: N applications per device per 24 h, per tenant.
- **Cross-tenant device consortium is opt-in, uses hashed identifiers only, never biometrics** (MX Anexo 71 bans biometric sharing).
### 6.8 Duplicate detection (within tenant)
- **Identifier duplicates:** same blind index on CURP/CPF/DNI.
- **Face duplicates:** 1:N search against the tenant's own templates (vendor template or in-house embedding), **within tenant only**. Bank tenants: within their own silo per Anexo 71.
- **Outcome:** link to the existing party, or flag for review (possible synthetic or multiple accounts).
---
## 7. Decision engine
```yaml
ruleset: mx-itf-default
version: 5
rules:                                     # evaluated in order; first terminal outcome wins
  - id: SANCTIONS_HIT
    when: screening.has_potential_match(list_kind=SANCTION)
    then: MANUAL_REVIEW                    # never auto-approve; true match → case/freeze via 03/06
  - id: DOC_FAIL
    when: checks.DOC_AUTH == FAIL
    then: REJECT
    reason: DOC_NOT_AUTHENTIC
  - id: LIVENESS_FAIL
    when: checks.LIVENESS == FAIL and attempts >= 3
    then: REJECT
    reason: LIVENESS_FAILED
  - id: CURP_MISMATCH
    when: checks.REGISTRY_ID.RENAPO == FAIL
    then: MANUAL_REVIEW
    reason: REGISTRY_MISMATCH
  - id: DUP
    when: checks.DUPLICATE.status == MATCH_OTHER_PARTY
    then: MANUAL_REVIEW
    reason: POSSIBLE_DUPLICATE
  - id: HIGH_RISK
    when: risk.rating == HIGH
    then: MANUAL_REVIEW
    reason: EDD_REQUIRED
  - id: PROHIBITED
    when: risk.rating == PROHIBITED
    then: REJECT
    reason: PROHIBITED_RISK
  - id: ASSURANCE_OK
    when: assurance >= tier.required_assurance and missing_fields == []
    then: APPROVE
  - id: DEFAULT
    then: NEEDS_INFO
```
- Reason codes are **stable and documented**. Customer-facing messages are derived per tenant, and **never reveal screening/sanctions reasons** (tipping off).
- Each decision stores the ruleset version, the policy snapshot and every check id it used.
---
## 8. Data model
Extends <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> §6 (`onboarding_application`, `verification_check`, `document`).
```sql
CREATE TABLE journey_definition (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    code          text NOT NULL,                -- 'mx-wallet-n2'
    version       int NOT NULL,
    spec          jsonb NOT NULL,               -- §4
    policy_snapshot_id uuid NOT NULL,           -- policy it was validated against
    status        text NOT NULL,                -- DRAFT, ACTIVE, RETIRED
    UNIQUE (tenant_id, code, version)
);

ALTER TABLE onboarding_application
    ADD COLUMN journey_id          uuid,
    ADD COLUMN tier_requested      text,
    ADD COLUMN tier_granted        text,
    ADD COLUMN assurance_level     text,        -- A0..A4
    ADD COLUMN decision_ruleset    text,
    ADD COLUMN decision_reasons    text[] NOT NULL DEFAULT '{}',
    ADD COLUMN purpose             text NOT NULL DEFAULT 'NEW',   -- NEW, STEP_UP, REFRESH
    ADD COLUMN expires_at          timestamptz;

CREATE TABLE onboarding_step (
    tenant_id       uuid NOT NULL,
    application_id  uuid NOT NULL REFERENCES onboarding_application(id),
    step_id         text NOT NULL,
    status          text NOT NULL,              -- PENDING, IN_PROGRESS, DONE, FAILED, SKIPPED
    attempts        smallint NOT NULL DEFAULT 0,
    started_at      timestamptz, finished_at timestamptz,
    PRIMARY KEY (application_id, step_id)
);

CREATE TABLE capture_session (                   -- one SDK session
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    application_id  uuid NOT NULL REFERENCES onboarding_application(id),
    channel         text NOT NULL,              -- SDK_IOS, SDK_ANDROID, SDK_WEB, API, WHATSAPP
    sdk_version     text,
    attestation     jsonb,                      -- Play Integrity / App Attest verdicts
    device_hash     bytea,
    ip              inet,
    geo_lat numeric(9,6), geo_lon numeric(9,6), geo_accuracy_m int,
    camera_source   text,                       -- PHYSICAL, VIRTUAL_SUSPECTED, UNKNOWN
    nonce           bytea NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL
);

CREATE TABLE capture (                          -- each captured artifact
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    session_id      uuid NOT NULL REFERENCES capture_session(id),
    kind            text NOT NULL,              -- DOC_FRONT, DOC_BACK, SELFIE, LIVENESS_VIDEO, NFC_DUMP, BARCODE_RAW
    storage_key     text NOT NULL,              -- biometric bucket for SELFIE/LIVENESS
    sha256          bytea NOT NULL,
    meta            jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- verification_check (data-model.md) plus:
ALTER TABLE verification_check
    ADD COLUMN assurance_contrib text,          -- A1..A4 contribution
    ADD COLUMN vendor_score      numeric,
    ADD COLUMN threshold         numeric,
    ADD COLUMN calibration_ver   text,          -- score → FMR mapping version
    ADD COLUMN route             text,          -- PRIMARY, FALLBACK_1, …
    ADD COLUMN capture_ids       uuid[];

CREATE TABLE biometric_template (               -- per-tenant silo; never shared
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    party_id      uuid NOT NULL,
    vendor        text NOT NULL,
    template      bytea NOT NULL,               -- encrypted with tenant biometric DEK
    source_capture uuid NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    delete_after  date                          -- policy-driven
);

CREATE TABLE consent_record (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    party_id     uuid,
    application_id uuid,
    purpose      text NOT NULL,                 -- KYC_PROCESSING, BIOMETRIC, REGISTRY_QUERY, MARKETING…
    text_id      text NOT NULL,                 -- versioned consent text
    granted      boolean NOT NULL,
    captured_at  timestamptz NOT NULL,
    evidence     jsonb                          -- ip, session, signature ref
);

CREATE TABLE duplicate_link (
    tenant_id     uuid NOT NULL,
    application_id uuid NOT NULL,
    other_party_id uuid NOT NULL,
    kind          text NOT NULL,                -- IDENTIFIER, FACE, DEVICE, PHONE, EMAIL
    score         numeric,
    resolution    text,                         -- SAME_PERSON, DIFFERENT, FRAUD
    PRIMARY KEY (application_id, other_party_id, kind)
);

CREATE TABLE review_task (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    application_id uuid NOT NULL,
    reasons       text[] NOT NULL,
    assignee_id   uuid,
    status        text NOT NULL,                -- OPEN, DECIDED
    decision      text,                         -- APPROVE, REJECT, NEEDS_INFO
    note          text,
    decided_by    uuid, decided_at timestamptz,
    sla_due_at    timestamptz
);
```
---
## 9. API
<table header-row="true">
<tr>
<td>Method</td>
<td>Path</td>
<td>Purpose</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/onboarding/applications`</td>
<td>Create: `{external_ref, country, product, tier, prefill{…}}` → `{id, session_token, journey}`</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/onboarding/applications/{id}`</td>
<td>Status, steps, outcome, reasons (tenant-safe)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/onboarding/applications/{id}/data`</td>
<td>Submit form fields (API-only integrations)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/onboarding/applications/{id}/captures`</td>
<td>API-only: upload images/NFC dumps (tenant captured)</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/onboarding/applications/{id}/step-up`</td>
<td>Request a tier upgrade</td>
</tr>
<tr>
<td>`POST`</td>
<td>`/v1/customers/{id}/refresh`</td>
<td>Start re-KYC</td>
</tr>
<tr>
<td>`GET`</td>
<td>`/v1/customers/{id}/evidence-pack`</td>
<td>Auditor export (PDF + JSON + artifacts ZIP)</td>
</tr>
<tr>
<td>SDK</td>
<td>`/v1/sdk/session` (`session_token`) → presigned upload URLs, nonce, step config</td>
<td></td>
</tr>
</table>
**Webhooks:**
- `onboarding.application.updated`
- `onboarding.application.decided`
- `onboarding.review.required`
- `account.tier.granted`
---
## 10. Cost model (per completed onboarding, indicative)
<table header-row="true">
<tr>
<td>Country</td>
<td>Components</td>
<td>Indicative cost</td>
</tr>
<tr>
<td>MX (A4 INE)</td>
<td>Doc + liveness (vendor) + INE face + RENAPO + device</td>
<td>\~US\$1.00–2.50 (vendor quotes needed; Didit-style RENAPO ≈ \$0.20)</td>
</tr>
<tr>
<td>BR (A3/A4)</td>
<td>Vendor capture/PAD + Serpro CPF (≈ R\$0.18–0.66) + Datavalid (≈ R\$1.50)</td>
<td>\~US\$0.40–1.00</td>
</tr>
<tr>
<td>AR (A4)</td>
<td>Vendor + RENAPER (ARS 20–60 per vigencia check; face fee TBD)</td>
<td>\~US\$0.50–1.50</td>
</tr>
<tr>
<td>CO / CL / PE</td>
<td>Vendor + registry/operator</td>
<td>\~US\$0.70–2.00</td>
</tr>
</table>
Metered per tenant via `verification_check.cost_minor`, which is used to price tiers and pass through costs.
---
## 11. Privacy & security
- **Biometric vault:**
	- separate bucket, role and per-tenant biometric DEK;
	- templates and raw images held separately;
	- raw images kept as KYC evidence for the retention period (MX/AR/PE 10 years, BR/CO/CL 5 years per sector);
	- templates deleted per policy.
- **No biometrics in Kafka or logs.** Events carry references only.
- **Consent texts** are versioned per country: LFPDPPP 2025 (MX, express consent for biometrics), LGPD Art. 11 (BR), Ley 1581 (CO), Ley 25.326 (AR), **Ley 21.719 (CL from 01-12-2026)**, Ley 29733 + DS 016-2024-JUS (PE).
- **DPIA template per cell.**
---
## 12. Testing
- **Validator unit tests** with official examples, plus fuzzing (CURP/RFC/CPF/CUIT/RUT/NIT).
- **Sandbox magic identities** per country, e.g. a CURP that triggers `REGISTRY_MISMATCH` or INE timeouts.
- **Vendor contract tests** against recorded responses; a nightly smoke test against vendor sandboxes.
- **Attack test pack:** printed photos, screen replays, virtual camera, emulator. These run per release on the SDK and must be blocked.
- **Journey/policy conformance:** a test fails if a generated journey misses a policy-required step.
## 13. Build plan
<table header-row="true">
<tr>
<td>Milestone</td>
<td>Scope</td>
<td>Eng-weeks</td>
</tr>
<tr>
<td>ON-1</td>
<td>Application API, journey engine, Temporal workflow skeleton, decision engine, review queue</td>
<td>3</td>
</tr>
<tr>
<td>ON-2</td>
<td>SDK wrapper over primary vendor (iOS/Android/Web), capture API, biometric vault, IAD layer</td>
<td>3</td>
</tr>
<tr>
<td>ON-3</td>
<td>MX: Incode adapter (doc, liveness, INE), RENAPO, SAT RFC, CLABE check, geolocation, MX journeys</td>
<td>3</td>
</tr>
<tr>
<td>ON-4</td>
<td>BR: Serpro CPF + Datavalid direct, unico/idwall capture adapter, BR journeys</td>
<td>2.5</td>
</tr>
<tr>
<td>ON-5</td>
<td>Step-up, refresh, evidence pack, consent, duplicates, device risk</td>
<td>2.5</td>
</tr>
<tr>
<td>**Total v1 (MX + BR)**</td>
<td></td>
<td>**≈ 14**</td>
</tr>
<tr>
<td>Per extra country</td>
<td>AR (VU/RENAPER BYO), CO (operator), CL (NFC), PE (RENIEC BYO)</td>
<td>2–3 each</td>
</tr>
</table>
## 14. Open questions
1. **Legal:** can a SaaS (via Incode/Veridas/NA-AT) supply INE biometric verification to **non-bank** fintech tenants? Is consent flow-down sufficient?
2. Does CNBV Anexo 71 (≥ 90%) also apply to **remote** onboarding for banks, fintechs and SOFIPOs? (Primary DOF text not yet read.)
3. Vendor data residency in MX/BR for biometric processing (RFP requirement).
4. Peru: create a Peruvian entity for a RENIEC convenio, or rely on tenants' convenios?
5. Colombia: shortlist RNEC-certified biometric operators and their fees.
