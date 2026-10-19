# 05 — Transaction monitoring
Source: https://app.notion.com/3fde5630e5e3817b9987f2a49b2bd540

**Date:** 2026-10-17 · **Status:** Technical design draft
**Index:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a1b4a4cd165a3aea66"/> · **Depends on:** <mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/> · **Feeds:** <mention-page url="https://app.notion.com/p/3fde5630e5e3814783fbe56ba6027f61"/>, <mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/>
---
## Executive summary
**What it does:** watches every payment that the client sends us and does three things.
1. **Stops obvious fraud in real time.** For instant payments (Pix, SPEI) it answers "allow / review / block" in under 0.1 seconds.
2. **Flags suspicious patterns** for investigation: splitting cash to stay under limits, money-mule accounts, sudden spikes, high-risk countries, crypto off-ramps.
3. **Captures every transaction the law says must be reported** regardless of suspicion: cash above the threshold, international transfers, crypto. This feeds the reporting engine automatically.
**How it works:**
- Transactions arrive by API or file and are converted into one standard format.
- Amounts are converted into local legal units (UMA, SMVM, UF, …).
- Each transaction is checked against **rules written in a simple configuration language**. Compliance teams can read, test and tune the rules without engineers.
- Running totals per customer (last day / week / month / 6 months) are kept up to date continuously.
**Why it matters:**
- Monitoring is mandatory in every country.
- Mexico's non-financial businesses must have **automated** monitoring by **June 2027**.
- Regulators now ask for proof that rules work. Our **rule simulator** replays history to show what a rule would have caught.
**Build:** \~14 engineer-weeks for v1 (rules, aggregates, real-time decisions, reportable-event capture, simulator). Machine-learning and network analytics come later.
---
## 1. Requirements
<table header-row="true">
<tr>
<td>#</td>
<td>Requirement</td>
<td>Source / note</td>
</tr>
<tr>
<td>T1</td>
<td>Ingest transactions from tenants (API, batch file, streaming) in a canonical schema; idempotent</td>
<td>—</td>
</tr>
<tr>
<td>T2</td>
<td>Synchronous pre-authorization decision for instant payments, p99 ≤ 100 ms inside our boundary</td>
<td>Pix/SPEI fraud</td>
</tr>
<tr>
<td>T3</td>
<td>Asynchronous rules over windowed aggregates (1 h … 6 months) per customer, account, counterparty, device</td>
<td>Structuring, velocity</td>
</tr>
<tr>
<td>T4</td>
<td>**Reportable-event capture** per policy pack (threshold reports), with the correct unit conversion and aggregation windows</td>
<td>MX Formato 41 ROR, MX non-financial avisos (6-month accumulation), CO cash (monthly multiple), AR cash/international transfers, CL cash, PE RO, BR Art. 49</td>
</tr>
<tr>
<td>T5</td>
<td>Account-tier limit enforcement (MX UDI levels, AR inclusion caps, PE simplified e-money)</td>
<td>Policy packs</td>
</tr>
<tr>
<td>T6</td>
<td>Country typology library, tenant-tunable</td>
<td>BR Carta Circular 4.001, MX UIF guides, CL UAF red flags, CO UIAF typologies</td>
</tr>
<tr>
<td>T7</td>
<td>Rule versioning, simulation/backtesting, effectiveness metrics</td>
<td>GAFILAT 5th round</td>
</tr>
<tr>
<td>T8</td>
<td>Alert dedup/suppression, prioritisation, evidence capture</td>
<td>Analyst efficiency</td>
</tr>
<tr>
<td>T9</td>
<td>MX non-financial businesses: automated monitoring mechanisms by 01-06-2027 (consolidated ops, accumulation, risk model, alerts for high risk/PEP/LPB/high-risk jurisdictions, cash)</td>
<td>Acuerdo 115/2026 Art. 41</td>
</tr>
<tr>
<td>T10</td>
<td>BR Pix: MED 2.0 return flows and precautionary blocks (hooks)</td>
<td>BCB, mandatory 02-02-2026</td>
</tr>
</table>
---
## 2. Architecture
```javascript
tenant systems
  │ POST /v1/transactions (batch ≤ 1,000)  │ POST /v1/transactions/decide (sync)  │ SFTP/S3 file drop
  ▼                                        ▼                                       ▼
┌──────────────────────────── Ingest API ──────────────────────────────────────────────┐
│ validate (Pydantic) → idempotency (tenant, external_id) → enrich-lite → Kafka txn.raw │
└──────────────────────────────────────────────────────────────────────────────────────┘
         │                                    │ sync path
         ▼                                    ▼
  ┌──────────────┐  txn.enriched   ┌──────────────────────────┐
  │ Enricher     │───────────────►│ Monitoring workers        │◄── rule set (versioned, per tenant)
  │ units, FX,   │                 │ (keyed by tenant:customer)│
  │ counterparty,│                 │  • update aggregates      │──► Redis (hot aggregates for sync)
  │ profile, risk│                 │  • evaluate async rules   │──► alerts.events → Cases
  └──────────────┘                 │  • reportable events      │──► reportable.events → Reporting
                                   └──────────────────────────┘
         │
         └──► ClickHouse (history, backtests)  ──► S3/Iceberg (10-year archive)

Sync decision: Decision API ─ reads Redis aggregates + customer profile cache ─ evaluates SYNC rules ─ ALLOW/REVIEW/BLOCK
```
### 2.1 Stream processing technology (revision to the infrastructure doc)
- **T1–T2:** **Python stream workers** (Kafka consumer groups, partitioned by `tenant_id:customer_id`) with **local RocksDB state** (via `rocksdict`) checkpointed to S3, plus Redis for the sync path.
	- One language across the platform.
	- Rules are evaluated by the same Python rule engine used in simulation, so **simulation equals production**.
	- Scales horizontally by partition count. Target: \~3–5k events/s per 16-vCPU worker pool.
- **T3:** move aggregate maintenance to **Apache Flink** (keyed state, exactly-once) if volume requires it, keeping the rule engine as a Python UDF or a compiled representation.
- **Decision gate:** sustained \> 15k events/s per cell, or state \> 200 GB per worker.
---
## 3. Canonical transaction schema
```python
class Txn(BaseModel):
    external_id: str                       # tenant's id → idempotency
    occurred_at: AwareDatetime
    customer_ref: str                      # tenant's customer external_ref
    account_ref: str
    direction: Literal["IN", "OUT"]
    rail: Literal["SPEI","PIX","CVU","CBU","PSE","BRE_B","ACH","WIRE_INTL","CARD","CASH","CHEQUE","CRYPTO","FX","INTERNAL"]
    instrument: Literal["CASH","TRANSFER","CARD","CHEQUE","CRYPTO","OTHER"]   # maps to MX "instrumento monetario"
    amount: Decimal; currency: str        # ISO 4217; crypto: asset code in `crypto`
    counterparty: Counterparty | None     # name, bank/ISPB/VASP, account (CLABE/Pix key/CBU/IBAN/wallet), country
    channel: Literal["APP","WEB","API","BRANCH","AGENT","ATM","POS"] | None
    device: DeviceCtx | None              # fingerprint, ip, geo
    crypto: CryptoCtx | None              # asset, network, tx_hash, wallet_type HOSTED|SELF_HOSTED, travel_rule_ref
    fx: FxCtx | None                      # cash foreign currency in/out (BR Art.49 IV, MX USD cash)
    purpose_code: str | None
    raw: dict = {}
```
**Enrichment adds:**
- `amount_usd`, `amount_local`, and amounts in the **threshold units of the tenant's packs** (`amount_uma`, `amount_udi`, `amount_smvm`, …), computed with each unit's conversion rule (see foundations §5).
- The resolved `party_id`, `relationship_id`, current risk rating, monitoring profile and account tier.
- Counterparty resolution: hash the account identifier, link to a known party, and look up counterparty screening status.
- Calendar fields: business day and the reporting periods each pack cares about (month, quarter, semester).
---
## 4. Rule engine
### 4.1 Rule definition language (YAML, compiled to Python AST)
```yaml
id: MX-STRUCT-CASH-01
name: Cash deposits structured below the relevant-operation threshold
version: 3
mode: ASYNC                         # SYNC | ASYNC | BOTH
scope: { tenant_packs: [MX/ITF-*, MX/BANK], segments: [PF, PM] }
on: { direction: IN, instrument: CASH }
let:
  thr: unit("USD", 5000)                                   # pack threshold (ITF)
  near: sum(amount_usd, window=7d, where={instrument: CASH, direction: IN, amount_usd: {gte: 0.7*thr, lt: thr}})
  n_near: count(window=7d, where={instrument: CASH, direction: IN, amount_usd: {gte: 0.7*thr, lt: thr}})
when: n_near >= 3 and near >= thr
severity: { base: MEDIUM, raise_if: { customer.risk: HIGH } }
dedup: { key: [customer], cooldown: 7d }
evidence: { transactions: window(7d, where={instrument: CASH, direction: IN}), aggregates: [near, n_near] }
typology: [STRUCTURING]
tags: [cash, mx]
```
**Built-in aggregate functions** (all declared up front, so the engine knows which state to maintain):
<table header-row="true">
<tr>
<td>Function</td>
<td>Example</td>
</tr>
<tr>
<td>`sum`, `count`, `avg`, `max`, `min`</td>
<td>`sum(field, window, where)`</td>
</tr>
<tr>
<td>`distinct_count(field, window, where)`</td>
<td>distinct counterparties, devices, IPs, countries</td>
</tr>
<tr>
<td>`ratio_to_profile(metric)`</td>
<td>actual vs declared/expected monthly volume</td>
</tr>
<tr>
<td>`zscore(metric, baseline=90d)`</td>
<td>deviation from the customer's own history</td>
</tr>
<tr>
<td>`peer_percentile(metric, segment)`</td>
<td>vs peer group (phase 2)</td>
</tr>
<tr>
<td>`first_seen(field)`</td>
<td>new counterparty, new device, new country</td>
</tr>
<tr>
<td>`time_since(event)`</td>
<td>account age, last login change, last KYC update</td>
</tr>
<tr>
<td>`in_list(field, list)`</td>
<td>high-risk countries, sanctioned wallets</td>
</tr>
</table>
**Where-filters:** equality, ranges, sets, `in_list`.
**Windows:** `1h, 24h, 7d, 30d, calendar_month, calendar_quarter, 6m_from_first` (the last one is MX LFPIORPI accumulation).
### 4.2 State: bucketed sliding windows
- Each declared aggregate has **time buckets** per key: 1-hour buckets for windows ≤ 7 days, 1-day buckets for windows ≤ 6 months. Long windows sum the buckets, giving O(buckets).
- Distinct counts use HyperLogLog sketches per bucket, plus exact sets for small cardinalities (\< 64).
- **Calendar windows** (month/quarter) are separate accumulators keyed by period, because reports are calendar-based.
- **State size:**
	- Per customer, roughly (#aggregates × #buckets × 16 B).
	- Example: 40 aggregates × (168 hourly + 180 daily) × 16 B ≈ 220 KB worst case, but sparse in practice (\~5–20 KB).
	- 2M customers ≈ 10–40 GB → RocksDB on local NVMe per worker.
- **Late or out-of-order events:** accepted up to 7 days late. Buckets are updated and **rules re-evaluated for the affected key**. Older events go to a backfill path that adjusts calendar accumulators and flags affected reports for review.
### 4.3 Evaluation flow (async)
```javascript
for each txn.enriched (ordered per key):
    state = load(keys = [customer, account, counterparty_hash, device])
    update aggregates (only those declared by active rules for this tenant)
    for rule in active_rules(tenant, txn):            # pre-indexed by `on` filters
        ctx = RuleContext(txn, state, profile, policy)
        if rule.when(ctx):
            if not dedup_suppressed(rule, ctx): emit Alert(rule_version_id, evidence…)
    for capture in reportable_captures(tenant, txn):  # §5
        emit ReportableEvent(...)
    write-through hot aggregates for SYNC rules to Redis (TTL = longest window)
```
### 4.4 Sync decision path
- `POST /v1/transactions/decide` accepts a `Txn` (with `external_id`) and returns:
	```json
{"decision":"ALLOW|REVIEW|BLOCK","reasons":[{"rule":"PIX-MULE-NEWACC-02","severity":"HIGH"}],"decision_id":"…"}
	```
- Only rules marked `SYNC`/`BOTH` run. Inputs come from Redis aggregates (≤ 1 network round trip, pipelined), the customer profile cache (risk rating, tier, PEP flag, screening status) and the counterparty cache.
- **Latency budget (100 ms):**
	<table header-row="true">
<tr>
<td>Step</td>
<td>Budget</td>
</tr>
<tr>
<td>Auth + validation</td>
<td>10 ms</td>
</tr>
<tr>
<td>Redis</td>
<td>10 ms</td>
</tr>
<tr>
<td>Rule evaluation</td>
<td>10 ms</td>
</tr>
<tr>
<td>Decision log write (async, via outbox to Kafka)</td>
<td>5 ms</td>
</tr>
<tr>
<td>Headroom</td>
<td>the rest</td>
</tr>
	</table>
- **Fail-open vs fail-closed** is tenant-configurable per rail (default: fail-open with `REVIEW` flag + alert).
- The decision is stored (`txn_decision`) and the transaction later flows through the async path. The two are linked by `external_id`.
### 4.5 Tier limit enforcement
- Sync rule template `TIER-LIMIT` per pack: e.g. MX fintech Level 1, monthly operations ≤ 750 UDIs and balance ≤ 1,000 UDIs, using a calendar-month accumulator in UDIs.
- Results: `BLOCK` (hard limit) or `REVIEW`, plus a webhook `account.tier.limit_reached` so the client can prompt the customer to upgrade KYC.
---
## 5. Reportable-event capture (threshold reports)
This is separate from suspicious alerts. **No human judgement is involved; it is a legal capture.**
```yaml
capture: MX_ITF_ROR_CASH
pack: MX/ITF-*
on: { instrument: CASH }
when: amount_usd_fix >= 5000              # USD at Banxico FIX of prior banking day
period: calendar_quarter
report: MX_FORMATO41_ROR
fields: { tipo_operacion: map(direction, {IN: "01", OUT: "02"}), instrumento: "01", … }
---
capture: MX_LFPIORPI_AVI_XVI_A
pack: MX/LFPIORPI-AVI
on: { rail: CRYPTO }
when: amount_uma >= 210 or acc_6m_from_first(amount_uma) >= 210    # 6-month accumulation (RCG Art. 19)
period: calendar_month
report: MX_AVISO_AVI
---
capture: CO_SFC_EFECTIVO_MULTIPLE
pack: CO/SFC-*
on: { instrument: CASH }
when: calendar_month_sum(amount_cop, key=customer) >= 50_000_000 or amount_cop >= 10_000_000
period: calendar_month
report: CO_SFC_ANEXO2
---
capture: BR_ART49_SELF_CUSTODY
pack: BR/BCB-*
on: { rail: CRYPTO, crypto.wallet_type: SELF_HOSTED }
when: amount_usd_ptax >= 10000
period: event                               # next business day (Oct–Dec 2026 batch → first business day of Jan 2027)
report: BR_SISCOAF_COE
```
- **Output:** a `reportable_event` row (period, report code, transaction refs, computed fields). The reporting engine (<mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/>) aggregates these into report files.
- **Multiple-operation rules:** when a monthly aggregate crosses the threshold, **all underlying transactions of the period** are captured (CO multiple cash; PE multiple RO). A capture re-emits on late events, and the report is marked `NEEDS_REGENERATION` if already drafted.
---
## 6. Data model
### 6.1 Postgres (system of record for rules, alerts, decisions)
```sql
CREATE TABLE monitoring_rule (
    id         uuid PRIMARY KEY,
    tenant_id  uuid,                         -- NULL = platform library rule
    code       text NOT NULL,                -- 'MX-STRUCT-CASH-01'
    kind       text NOT NULL,                -- ALERT | CAPTURE | LIMIT
    UNIQUE (tenant_id, code)
);

CREATE TABLE monitoring_rule_version (
    id              uuid PRIMARY KEY,
    rule_id         uuid NOT NULL REFERENCES monitoring_rule(id),
    version         int NOT NULL,
    definition      jsonb NOT NULL,          -- parsed YAML (§4.1)
    compiled_hash   bytea NOT NULL,
    status          text NOT NULL,           -- DRAFT, SIMULATED, APPROVED, ACTIVE, RETIRED
    simulation_id   uuid,                    -- last backtest run
    approved_by     uuid, approved_at timestamptz,
    effective_from  timestamptz,
    UNIQUE (rule_id, version)
);

CREATE TABLE rule_assignment (               -- which rule versions run for which tenant/segment
    tenant_id        uuid NOT NULL,
    rule_version_id  uuid NOT NULL REFERENCES monitoring_rule_version(id),
    segment_filter   jsonb NOT NULL DEFAULT '{}',   -- e.g. {"risk":["HIGH"],"product":["WALLET"]}
    params_override  jsonb NOT NULL DEFAULT '{}',   -- tenant tuning (thresholds may only tighten for CAPTURE)
    active           boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, rule_version_id)
);

-- alert table as in data-model.md, plus:
CREATE TABLE alert_evidence (
    alert_id        uuid NOT NULL REFERENCES alert(id),
    tenant_id       uuid NOT NULL,
    aggregates      jsonb NOT NULL,          -- values of the `let` variables at firing time
    transaction_ids uuid[] NOT NULL,
    window_start    timestamptz, window_end timestamptz,
    PRIMARY KEY (alert_id)
);

CREATE TABLE txn_decision (                  -- sync decisions
    tenant_id      uuid NOT NULL,
    id             uuid NOT NULL,
    external_id    text NOT NULL,
    decided_at     timestamptz NOT NULL,
    decision       text NOT NULL,            -- ALLOW, REVIEW, BLOCK
    reasons        jsonb NOT NULL,
    latency_ms     smallint NOT NULL,
    ruleset_hash   bytea NOT NULL,
    PRIMARY KEY (tenant_id, decided_at, id)
) PARTITION BY RANGE (decided_at);

CREATE TABLE reportable_event (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    capture_code    text NOT NULL,           -- 'MX_ITF_ROR_CASH'
    report_code     text NOT NULL,           -- 'MX_FORMATO41_ROR'
    period_key      text NOT NULL,           -- '2026Q4', '2026-11', 'EVENT'
    party_id        uuid,
    transaction_ids uuid[] NOT NULL,
    fields          jsonb NOT NULL,          -- pre-mapped report fields (codes, amounts in required units)
    unit_values     jsonb NOT NULL,          -- which UMA/FIX/TRM values were used (reproducibility)
    status          text NOT NULL DEFAULT 'CAPTURED',   -- CAPTURED, INCLUDED, VOIDED
    captured_at     timestamptz NOT NULL,
    UNIQUE (tenant_id, capture_code, period_key, transaction_ids)
);

CREATE TABLE behaviour_profile (             -- monthly snapshot per relationship (feeds risk)
    tenant_id        uuid NOT NULL,
    relationship_id  uuid NOT NULL,
    month            date NOT NULL,
    metrics          jsonb NOT NULL,         -- inflow/outflow sums, counts, cash share, intl share, distinct cps…
    declared_profile jsonb,                  -- from onboarding questionnaire (expected volume, purpose)
    PRIMARY KEY (tenant_id, relationship_id, month)
);
```
### 6.2 ClickHouse (history & simulation)
```sql
CREATE TABLE txn (
    tenant_id UUID, occurred_at DateTime64(3, 'UTC'), id UUID,
    customer_id UUID, account_id UUID, direction Enum8('IN'=1,'OUT'=2),
    rail LowCardinality(String), instrument LowCardinality(String),
    amount Decimal(20,4), currency LowCardinality(String),
    amount_usd Decimal(20,4), amount_local Decimal(20,4), units Map(String, Decimal(20,6)),
    counterparty_hash FixedString(32), counterparty_country LowCardinality(String),
    channel LowCardinality(String), device_hash FixedString(32), geo_cell String,
    crypto_wallet_type LowCardinality(String), risk_rating LowCardinality(String)
) ENGINE = ReplicatedMergeTree
PARTITION BY (toYYYYMM(occurred_at))
ORDER BY (tenant_id, customer_id, occurred_at)
SETTINGS index_granularity = 8192;
-- Row policy: CREATE ROW POLICY tenant ON txn USING tenant_id = getSetting('SQL_tenant_id') TO app_role;
```
---
## 7. Simulation / backtesting
1. The analyst edits a rule (console YAML editor with schema hints) → **Simulate** over a time range, e.g. the last 6 months.
2. A Temporal workflow `SimulateRule` streams the tenant's history from ClickHouse **in event-time order per key** through the *same* Python engine, in isolated state (no side effects).
3. Output:
	- number of alerts, alerts per week, customers hit;
	- **overlap** with existing alerts and with past cases/STRs (true-positive proxy: hits on customers who later had an STR or exit);
	- a sample of hits for review.
4. Results are stored in `rule_simulation`; approval requires a simulation that ran within the last 30 days.
**Effectiveness metrics** (feed the EWRA in <mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/> and the GAFILAT-style evidence):
<table header-row="true">
<tr>
<td>Per rule</td>
<td>Overall</td>
</tr>
<tr>
<td>Alert volume</td>
<td>Coverage of the typology library</td>
</tr>
<tr>
<td>Alert-to-case %</td>
<td>Time from transaction to alert</td>
</tr>
<tr>
<td>Case-to-STR %</td>
<td></td>
</tr>
<tr>
<td>Average handling time</td>
<td></td>
</tr>
</table>
---
## 8. Country typology library (initial set)
<table header-row="true">
<tr>
<td>Typology</td>
<td>MX</td>
<td>BR</td>
<td>CO</td>
<td>AR</td>
<td>CL</td>
<td>PE</td>
</tr>
<tr>
<td>Cash structuring below reporting thresholds</td>
<td>✓ (USD 5k/7.5k)</td>
<td>✓ (R\$ 50k)</td>
<td>✓ (COP 10M)</td>
<td>✓ (40 SMVM)</td>
<td>✓ (USD 10k)</td>
<td>✓ (US\$10k)</td>
</tr>
<tr>
<td>Rapid in-out (pass-through / mule)</td>
<td>✓</td>
<td>✓ Pix</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>New account high velocity (first 30 days)</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>Many-to-one funnel (many senders → one account)</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>Declared vs actual volume mismatch</td>
<td>✓</td>
<td>✓ (financial capacity)</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>High-risk jurisdiction transfers</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>Crypto off-ramp after fiat in / self-custody outflows</td>
<td>✓</td>
<td>✓ (Res. 591)</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>Parliamentary-amendment funds cash (IN BCB 732/2026)</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>Betting operator flows (unauthorised operators, Res. BCB 590/2026)</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>Border/frontier zone activity</td>
<td>✓</td>
<td>✓</td>
<td></td>
<td>✓ (Triple Frontier)</td>
<td></td>
<td></td>
</tr>
<tr>
<td>Round-amount FX / remittance layering</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
<tr>
<td>Dormant account reactivation</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
</tr>
</table>
Each typology is a rule template with **parameters per pack**. Tenants tune parameters within allowed bounds.
---
## 9. Phase 2: ML & network analytics
- **Unsupervised anomaly scores** (isolation forest / autoencoder on monthly behaviour vectors) as a rule input: `anomaly_score >= 0.9`.
- **Graph analytics** (shared counterparties, devices, IPs; community detection for mule rings) run daily in batch over ClickHouse. Results become `network_signal` features and network alerts.
- **Supervised alert-scoring model** to prioritise the alert queue (not to suppress alerts until validated with regulators).
---
## 10. Non-functional
<table header-row="true">
<tr>
<td>Item</td>
<td>Target</td>
</tr>
<tr>
<td>Sync decision</td>
<td>p99 ≤ 100 ms; availability 99.95%</td>
</tr>
<tr>
<td>Async alert latency</td>
<td>≤ 5 s p95 from ingest</td>
</tr>
<tr>
<td>Ingest throughput</td>
<td>T1 500 tx/s peak; T2 5k tx/s; horizontally scalable via partitions</td>
</tr>
<tr>
<td>Exactly-once alerts</td>
<td>Dedup by `(rule_version, key, window_bucket)`  • idempotent alert insert</td>
</tr>
<tr>
<td>Replays</td>
<td>Any period can be replayed for a tenant (simulation or recovery) without side effects</td>
</tr>
</table>
---
## 11. Testing
- **Rule DSL compiler tests**, plus **golden scenario tests per typology**: a synthetic transaction stream → expected alerts.
- **Capture tests per report:** a period's transactions → expected reportable events, with unit-conversion edge cases (UMA year change on 1 Feb; SMVM semester snapshot; FIX holiday roll-back).
- **Load tests:** sustained 2× T-tier peak; sync decision latency under load.
- **Chaos:** worker crash mid-batch → state restored from checkpoint, no duplicate alerts.
## 12. Build plan
<table header-row="true">
<tr>
<td>Milestone</td>
<td>Scope</td>
<td>Eng-weeks</td>
</tr>
<tr>
<td>TM-1</td>
<td>Canonical schema, ingest API (batch + file), enrichment (units, party resolution), Kafka topics</td>
<td>3</td>
</tr>
<tr>
<td>TM-2</td>
<td>Rule DSL + compiler + bucketed state (RocksDB) + async evaluation + alerts with evidence</td>
<td>4</td>
</tr>
<tr>
<td>TM-3</td>
<td>Reportable-event capture + MX captures (fintech/bank/non-financial)</td>
<td>2</td>
</tr>
<tr>
<td>TM-4</td>
<td>Sync decision API + Redis aggregates + tier limits</td>
<td>2.5</td>
</tr>
<tr>
<td>TM-5</td>
<td>ClickHouse history + simulation workflow + effectiveness metrics</td>
<td>2.5</td>
</tr>
<tr>
<td>**Total v1**</td>
<td></td>
<td>**≈ 14**</td>
</tr>
<tr>
<td>TM-6 (phase 2)</td>
<td>Pix MED 2.0 hooks, ML anomaly, graph analytics</td>
<td>6–8</td>
</tr>
</table>
## 13. Open questions
- Do tenants send **all** transactions, or only those above a floor? Recommendation: all; reports and structuring rules need sub-threshold activity.
- Balance data: tier rules need balances. Do tenants send balance snapshots or do we derive balances from flows? Recommendation: optional daily balance feed.
- Default fail-open vs fail-closed per rail. This needs a product decision with design partners.
