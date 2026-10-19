# Infrastructure — databases, replicas, sizing, HA/DR
Source: https://app.notion.com/3fde5630e5e3815191c9ddd6987d3598

**Date:** 2026-10-17
**Status:** Draft proposal. Sizing is an **order-of-magnitude starting point** built on the assumptions below; validate with load tests before committing.
**Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e38175aa91cf38caaaf97e"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/>
AWS service names are used as the reference implementation. GCP and Azure equivalents are in §9.
---
## 1. Sizing assumptions (per cell)
<table header-row="true">
<tr>
<td>Tier</td>
<td>When</td>
<td>Tenants</td>
<td>End customers</td>
<td>Transactions/sec (avg → peak)</td>
<td>Onboardings/day</td>
<td>Screening checks/day</td>
</tr>
<tr>
<td>**T1 Launch**</td>
<td>MX cell, year 1</td>
<td>5–20 fintechs</td>
<td>≤ 2 M</td>
<td>50 → 500</td>
<td>≤ 20 k</td>
<td>≤ 2 M (incl. list-update rescreens)</td>
</tr>
<tr>
<td>**T2 Growth**</td>
<td>MX + BR cells</td>
<td>20–80</td>
<td>≤ 20 M</td>
<td>1,000 → 5,000</td>
<td>≤ 200 k</td>
<td>≤ 20 M</td>
</tr>
<tr>
<td>**T3 Scale**</td>
<td>4–6 cells, incl. banks</td>
<td>100+</td>
<td>100 M+</td>
<td>5,000 → 20,000+ (a single large Pix tenant can reach this)</td>
<td>1 M</td>
<td>100 M+</td>
</tr>
</table>
**Rough data volumes.** One monitored transaction is about 1 KB in Postgres including indexes, and about 100–150 bytes compressed in a columnar store.
<table header-row="true">
<tr>
<td>Tier</td>
<td>Transactions/day</td>
<td>Postgres growth</td>
<td>Columnar growth</td>
</tr>
<tr>
<td>T1</td>
<td>\~4.3 M</td>
<td>\~4 GB/day (\~1.5 TB/yr)</td>
<td>\~0.5 GB/day</td>
</tr>
<tr>
<td>T2</td>
<td>\~86 M</td>
<td>\~86 GB/day (\~2.6 TB/month)</td>
<td>\~10 GB/day</td>
</tr>
</table>
At T2, keeping transactions in Postgres is no longer practical. This drives the **storage split in §3**.
---
## 2. Database inventory per cell
Each cell (one country or region) has its own copy of everything below. **No database is shared across cells.**
<table header-row="true">
<tr>
<td>#</td>
<td>Store</td>
<td>Technology</td>
<td>Holds</td>
<td>Why separate</td>
</tr>
<tr>
<td>1</td>
<td>**Core OLTP**</td>
<td>PostgreSQL (Aurora PostgreSQL / RDS)</td>
<td>Tenants, parties, identifiers, relationships, onboarding, verification checks, risk, screening results, alerts, cases, reports, config</td>
<td>System of record. Needs relational integrity and row-level security</td>
</tr>
<tr>
<td>2</td>
<td>**Workflow store**</td>
<td>PostgreSQL (separate cluster) for self-hosted Temporal</td>
<td>Onboarding and reporting workflow state, timers, retries</td>
<td>Very write-heavy, with a different load profile. Keeps workflow churn away from the core database</td>
</tr>
<tr>
<td>3</td>
<td>**Event backbone**</td>
<td>Kafka (Amazon MSK)</td>
<td>Transaction ingest, list updates, alerts, audit events, change-data-capture streams</td>
<td>Decouples ingest from processing. Ordered per customer. Replayable</td>
</tr>
<tr>
<td>4</td>
<td>**Hot state / cache**</td>
<td>Redis/Valkey (ElastiCache, cluster mode)</td>
<td>Rolling aggregates for synchronous decisions, idempotency keys, rate limits, sessions, tenant config cache</td>
<td>Sub-millisecond reads on the payment-decision path</td>
</tr>
<tr>
<td>5</td>
<td>**Stream-processor state**</td>
<td>T1–T2: Python stream workers with local RocksDB + S3 checkpoints (see <mention-page url="https://app.notion.com/p/3fde5630e5e3817b9987f2a49b2bd540"/>). T3: Flink with a RocksDB state backend + S3 checkpoints</td>
<td>Per-customer windows (velocity, structuring, sums in UMA)</td>
<td>Stateful monitoring at volume. Not a database you query</td>
</tr>
<tr>
<td>6</td>
<td>**Screening index**</td>
<td>OpenSearch (or an in-memory matcher service)</td>
<td>Shared lists (OFAC/UN/EU/PEP…), per-tenant private lists (LPB), normalized customer names for rescreening</td>
<td>Fuzzy, phonetic and accent-insensitive search. Postgres is the wrong tool for this</td>
</tr>
<tr>
<td>7</td>
<td>**Analytics / OLAP**</td>
<td>ClickHouse (from T2; optional at T1)</td>
<td>Transaction history, rule backtesting, dashboards, threshold-report aggregation</td>
<td>Fast scans over billions of rows. Cheap compressed storage</td>
</tr>
<tr>
<td>8</td>
<td>**Lakehouse / archive**</td>
<td>S3 + Apache Iceberg (Parquet), queried via Athena or Trino</td>
<td>Full 10-year history: transactions, list snapshots, decisions</td>
<td>Meets the 10-year retention cheaply. Supports regulator look-backs</td>
</tr>
<tr>
<td>9</td>
<td>**Object storage**</td>
<td>S3</td>
<td>Documents, selfies, raw registry responses (evidence), generated report files</td>
<td>Large binary files with per-tenant prefixes and keys</td>
</tr>
<tr>
<td>10</td>
<td>**Audit archive**</td>
<td>S3 with **Object Lock (compliance mode)**  • searchable copy in ClickHouse or OpenSearch</td>
<td>Hash-chained audit events</td>
<td>Write-once storage for the full retention period</td>
</tr>
<tr>
<td>11</td>
<td>**Keys / secrets**</td>
<td>KMS (per-tenant keys) + Secrets Manager or Vault</td>
<td>Tenant data keys, regulator portal credentials and signing certificates, registry API credentials</td>
<td>Crypto-shredding. Tenant-scoped secrets</td>
</tr>
</table>
**Control plane (global, no PII):** one small PostgreSQL cluster (writer + standby) for the tenant registry, billing, policy packs, and the rule and model registry. It replicates **configuration only** to the cells.
**Deliberately not used at first:**
- **A graph database.** Beneficial-owner chains are shallow, and recursive SQL handles them. Add Neptune or Neo4j only when network analytics (mule rings) becomes a product feature.
- **A separate document database.** Postgres JSONB covers the semi-structured data.
---
## 3. Where transactions live
```javascript
        ingest API / bank & PSP feeds
                    │
                    ▼
           Kafka: txn.raw (key = tenant:customer)
                    │
      ┌─────────────┼────────────────┐
      ▼             ▼                ▼
Flink (rules,   ClickHouse       S3/Iceberg
aggregates)     (history,        (10-yr archive)
      │          backtests)
      ├──► Redis (hot aggregates for sync decisions)
      ├──► Kafka: alerts ──► Core OLTP (alert, case)
      └──► Core OLTP: only transactions linked to an alert/case/report are copied in
```
<table header-row="true">
<tr>
<td>Tier</td>
<td>Transactions in core Postgres</td>
</tr>
<tr>
<td>T1</td>
<td>All of them, in monthly partitions, keeping \~13 months hot. That's simplest, and \~1.5 TB/year is fine.</td>
</tr>
<tr>
<td>T2+</td>
<td>**Only** transactions referenced by an alert, case or report. ClickHouse holds the full history online; Iceberg holds the 10-year archive.</td>
</tr>
</table>
The investigation UI reads transaction history from ClickHouse, always filtered by tenant.
---
## 4. Core OLTP topology: writer, replicas, read routing
```javascript
                ┌──────────────── AZ-a ───────────────┐
app (writes) ──►│  WRITER (primary)                   │
                └──────────────┬──────────────────────┘
                               │ storage-level replication (Aurora) / sync standby (RDS Multi-AZ)
           ┌───────────────────┼─────────────────────────┐
           ▼                   ▼                         ▼
  ┌─ AZ-b ──────────┐  ┌─ AZ-c ──────────┐      ┌─ AZ-b/c ─────────────────┐
  │ READER 1         │  │ READER 2         │      │ READER 3 (reporting)      │
  │ failover target  │  │ API GETs, case   │      │ report generation, exports│
  │ + API reads      │  │ UI lists         │      │ CDC → ClickHouse/lake     │
  └─────────────────┘  └─────────────────┘      └──────────────────────────┘
```
**Read-routing rules:**
<table header-row="true">
<tr>
<td>Traffic</td>
<td>Goes to</td>
<td>Why</td>
</tr>
<tr>
<td>All writes and decisions: onboarding approval, match disposition, case actions, report approval</td>
<td>**Writer**</td>
<td>Single source of truth</td>
</tr>
<tr>
<td>A read immediately after the caller's own write (e.g. polling onboarding status right after submit)</td>
<td>**Writer**, or a reader that has caught up to the commit (log sequence number)</td>
<td>Avoids stale reads from replica lag</td>
</tr>
<tr>
<td>Customer and case lists, dashboards, API `GET`s that tolerate \~1 s lag</td>
<td>**Readers 1–2** (load-balanced reader endpoint)</td>
<td>Offloads the writer</td>
</tr>
<tr>
<td>Regulatory report generation, bulk exports, change-data-capture</td>
<td>**Reader 3**, a separate custom endpoint</td>
<td>Heavy scans never hurt interactive latency</td>
</tr>
<tr>
<td>Analytics over transactions</td>
<td>**ClickHouse**, never the OLTP database</td>
<td>Wrong engine for scans</td>
</tr>
</table>
**Connection management:**
- Put PgBouncer (or RDS Proxy) in front of the writer and the reader endpoints.
- Set the tenant per transaction (`SET LOCAL app.tenant_id`) so row-level security works with transaction pooling.
**Starting sizes** (Aurora PostgreSQL, Graviton):
<table header-row="true">
<tr>
<td>Tier</td>
<td>Writer</td>
<td>Readers</td>
<td>Storage</td>
<td>Notes</td>
</tr>
<tr>
<td>T1</td>
<td>db.r7g.2xlarge (8 vCPU / 64 GB)</td>
<td>2 × db.r7g.xlarge + 1 × r7g.xlarge (reporting)</td>
<td>0.5–2 TB</td>
<td>One cluster holds all tenants (pooled)</td>
</tr>
<tr>
<td>T2</td>
<td>db.r7g.8xlarge (32 / 256 GB)</td>
<td>2 × r7g.4xlarge + 1 × r7g.4xlarge (reporting)</td>
<td>2–8 TB</td>
<td>Transactions moved out (§3)</td>
</tr>
<tr>
<td>T3</td>
<td>**Sharded by tenant** (§5)</td>
<td>per shard</td>
<td>per shard</td>
<td>—</td>
</tr>
</table>
---
## 5. Scaling the core database: tenant sharding
A single Postgres writer eventually becomes the bottleneck. **Shard by tenant, never by customer:** a tenant's data always lives together, so tenant isolation, backups and exports stay simple.
```javascript
control plane: tenant_directory
  tenant_id → cell → shard (cluster endpoint) → tier (pooled | dedicated)

cell MX
├── shard mx-pool-01   (Aurora cluster: writer + readers)  ← 40 small fintechs
├── shard mx-pool-02   (Aurora cluster)                    ← 40 small fintechs
└── shard mx-ded-acme  (Aurora cluster, dedicated)         ← 1 large fintech/bank
```
- **Pooled shards:** many small tenants share a cluster, isolated by row-level security and per-tenant keys.
- **Dedicated shards:** large or demanding tenants get their own cluster, which a contract can require.
- **Moving a tenant** between shards: logical replication of that tenant's rows, a short write freeze, then switch the tenant directory entry.
- **Alternative:** Citus (sharded Postgres) with `tenant_id` as the distribution column. It's operationally simpler at very high tenant counts, but less available as a managed service in LATAM regions.
---
## 6. Other stores: topology and starting sizes
<table header-row="true">
<tr>
<td>Store</td>
<td>T1</td>
<td>T2</td>
<td>HA notes</td>
</tr>
<tr>
<td>**Temporal persistence (Postgres)**</td>
<td>db.r7g.large writer + standby</td>
<td>r7g.2xlarge + standby + 1 reader (visibility queries)</td>
<td>Multi-AZ. Workflow payloads go through an encryption codec so **no plaintext PII** sits in Temporal</td>
</tr>
<tr>
<td>**Temporal services**</td>
<td>3 × each service (frontend/history/matching/worker), 4 vCPU</td>
<td>6–12 history pods; size history shards for growth up front (fixed at cluster creation)</td>
<td>Pods spread across 3 availability zones (AZs)</td>
</tr>
<tr>
<td>**Kafka (MSK)**</td>
<td>3 brokers (kafka.m7g.large), one per AZ, replication factor 3, `min.insync.replicas=2`</td>
<td>6–9 brokers (m7g.xlarge–2xlarge), tiered storage</td>
<td>Partition key `tenant_id:customer_id` keeps each customer's events in order. Large tenants get dedicated topics</td>
</tr>
<tr>
<td>**Flink**</td>
<td>1 job manager (HA) + 2–3 task managers (4 vCPU / 16 GB)</td>
<td>6–20 task managers, autoscaled</td>
<td>Checkpoints to S3 every 30–60 s. Exactly-once delivery to alert topics</td>
</tr>
<tr>
<td>**Redis/Valkey**</td>
<td>Cluster mode, 2 shards × (primary + replica), r7g.large</td>
<td>6+ shards, r7g.xlarge</td>
<td>Multi-AZ automatic failover. Treated as **rebuildable** from Flink state and Kafka</td>
</tr>
<tr>
<td>**OpenSearch**</td>
<td>3 data nodes (r6g.large) + 3 dedicated masters, across 3 AZs</td>
<td>6+ data nodes (r6g.2xlarge), UltraWarm for old list versions</td>
<td>One shared list index (read-only, many replicas). Per-tenant indices or document-level filtering for private lists and customer names</td>
</tr>
<tr>
<td>**ClickHouse**</td>
<td>optional (1 node + backup), or skip</td>
<td>3 shards × 2 replicas (16 vCPU / 64–128 GB, gp3/NVMe) or ClickHouse Cloud **if offered in-region**</td>
<td>Partition by `(tenant_id, toYYYYMM(ts))`. Row policies per tenant</td>
</tr>
<tr>
<td>**Control-plane Postgres**</td>
<td>db.r7g.large + standby</td>
<td>same</td>
<td>No PII. Can live in one "home" region</td>
</tr>
</table>
**Compute** runs on Kubernetes (EKS) per cell across 3 AZs, with separate node pools:
- `api`: gateway, public APIs
- `workers`: Temporal workers, connectors, report generators
- `stream`: Flink
- `matching`: the screening matcher, memory-heavy
T1 starts at roughly 10–15 nodes of 8 vCPU / 32 GB in total and autoscales. Burst workloads, such as rescreening the whole customer base after a list update, run as **queued batch jobs** on spot capacity.
---
## 7. Performance targets (design SLOs)
<table header-row="true">
<tr>
<td>Path</td>
<td>Target</td>
</tr>
<tr>
<td>Synchronous transaction risk decision (Pix/SPEI pre-authorization)</td>
<td>p99 ≤ 100 ms inside our boundary (Redis aggregates + in-memory rules; no OLTP reads on this path)</td>
</tr>
<tr>
<td>Screening a single name at onboarding</td>
<td>p95 ≤ 300 ms</td>
</tr>
<tr>
<td>Onboarding decision with registry checks</td>
<td>dominated by external registries (seconds); asynchronous with webhook</td>
</tr>
<tr>
<td>Rescreening the full customer base after a list update</td>
<td>\< 2 h per cell at T2 (Mexican UIF designations require same-day action)</td>
</tr>
<tr>
<td>Alert creation after an asynchronous rule hit</td>
<td>≤ 5 s</td>
</tr>
<tr>
<td>Core API availability</td>
<td>99.95 % per cell</td>
</tr>
</table>
---
## 8. HA, backup and disaster recovery under data-residency constraints
**Within a region:** everything runs across **3 AZs**. Aurora fails over to a reader in under \~60 s; MSK, OpenSearch and ElastiCache are multi-AZ.
**Backups:**
- Aurora: continuous point-in-time recovery (35 days) plus daily snapshots copied to a separate backup account with vault lock.
- S3: versioning plus Object Lock.
- Kafka: fully replicated into S3/Iceberg.
- Restore drills: quarterly.
**The data-residency problem for DR.** Several LATAM countries have **only one region per cloud provider** today:
<table header-row="true">
<tr>
<td>Country</td>
<td>AWS</td>
<td>GCP</td>
<td>Azure</td>
</tr>
<tr>
<td>Mexico</td>
<td>mx-central-1 (Querétaro, 3 AZ, Jan 2025)</td>
<td>Querétaro (Dec 2024)</td>
<td>Mexico Central (Querétaro, 3 AZ)</td>
</tr>
<tr>
<td>Brazil</td>
<td>sa-east-1 (São Paulo)</td>
<td>São Paulo</td>
<td>Brazil South (São Paulo) + Brazil Southeast (Rio)</td>
</tr>
<tr>
<td>Chile</td>
<td>Chile region announced for \~end-2026 ⚠️</td>
<td>Santiago (2021)</td>
<td>Chile Central (Santiago, 3 AZ)</td>
</tr>
<tr>
<td>Colombia</td>
<td>Local Zone (Bogotá) only</td>
<td>Bogotá (2023) ⚠️ verify</td>
<td>reported 2024 ⚠️ verify</td>
</tr>
<tr>
<td>Argentina, Peru</td>
<td>Local Zones only (Buenos Aires, Lima)</td>
<td>—</td>
<td>—</td>
</tr>
</table>
⚠️ Region availability and the services offered in each region change often. Confirm on each provider's "services by region" page before choosing.
**DR options when a client requires in-country data:**
<table header-row="true">
<tr>
<td>Option</td>
<td>How</td>
<td>RPO / RTO</td>
<td>Trade-off</td>
</tr>
<tr>
<td>**A. Multi-AZ only** (default)</td>
<td>3 AZs in one region + offline backups</td>
<td>AZ loss: \~0 / minutes. Region loss: hours–days</td>
<td>Simplest. Accepts the risk of losing the whole region</td>
</tr>
<tr>
<td>**B. Cross-region, out of country**</td>
<td>Aurora Global Database / backup copy to another country (e.g. MX → us-east, BR → us-east)</td>
<td>≤ 1 min / \< 1 h</td>
<td>**Only if contracts and the regulator allow data abroad.** Brazil's outsourcing rules require BCB notification and agreements for foreign processing ⚠️</td>
</tr>
<tr>
<td>**C. Cross-cloud, in country**</td>
<td>Primary on AWS Querétaro, warm standby on GCP or Azure Querétaro (logical replication + S3→GCS sync)</td>
<td>\~5 min / \< 4 h</td>
<td>Keeps data in-country. Also fits the **primary/secondary cloud provider** model in Mexican fintech cloud rules ⚠️. Highest operational cost</td>
</tr>
<tr>
<td>**D. In-country second region** (Brazil only today)</td>
<td>Azure Brazil South → Brazil Southeast</td>
<td>≤ 1 min / \< 1 h</td>
<td>Only available in Brazil</td>
</tr>
</table>
**Recommendation:**
- **T1:** option A, with **encrypted backups also copied in-country to a second provider** (cheap insurance, and the first step toward C).
- **T2+:** offer C or D to bank and large-fintech tenants as a premium "regulated" tier. Model the outsourcing requirements per country in the policy packs.
---
## 9. Cloud service equivalents
<table header-row="true">
<tr>
<td>Function</td>
<td>AWS</td>
<td>GCP</td>
<td>Azure</td>
</tr>
<tr>
<td>Managed Postgres</td>
<td>Aurora PostgreSQL / RDS</td>
<td>AlloyDB / Cloud SQL</td>
<td>Azure Database for PostgreSQL – Flexible</td>
</tr>
<tr>
<td>Kafka</td>
<td>MSK</td>
<td>Managed Service for Apache Kafka / Confluent</td>
<td>Event Hubs (Kafka API) / Confluent</td>
</tr>
<tr>
<td>Redis</td>
<td>ElastiCache (Valkey)</td>
<td>Memorystore</td>
<td>Azure Cache for Redis</td>
</tr>
<tr>
<td>Search</td>
<td>OpenSearch Service</td>
<td>Elastic Cloud on GCP</td>
<td>Elastic on Azure</td>
</tr>
<tr>
<td>Object storage + WORM</td>
<td>S3 + Object Lock</td>
<td>GCS + Bucket Lock</td>
<td>Blob + immutable storage</td>
</tr>
<tr>
<td>Kubernetes</td>
<td>EKS</td>
<td>GKE</td>
<td>AKS</td>
</tr>
<tr>
<td>KMS</td>
<td>KMS</td>
<td>Cloud KMS</td>
<td>Key Vault / Managed HSM</td>
</tr>
<tr>
<td>Flink</td>
<td>Managed Service for Apache Flink</td>
<td>Dataproc / self-managed</td>
<td>HDInsight / self-managed</td>
</tr>
</table>
**Portability rule:** use managed services, but keep the application on standard Postgres, the Kafka API, the S3 API and Kubernetes. That keeps option C (cross-cloud DR) and a dedicated deployment in a bank's own cloud feasible.
---
## 10. Environments
<table header-row="true">
<tr>
<td>Environment</td>
<td>Data</td>
<td>Notes</td>
</tr>
<tr>
<td>**prod** (per cell)</td>
<td>Real</td>
<td>As above</td>
</tr>
<tr>
<td>**sandbox** (per cell, shared)</td>
<td>Synthetic only</td>
<td>Every tenant gets a sandbox tenant. Registry connectors are **mocked** with deterministic test identities (e.g. a CURP that always returns "not found")</td>
</tr>
<tr>
<td>**staging**</td>
<td>Synthetic + anonymized production *shapes*</td>
<td>Release candidate, load tests</td>
</tr>
<tr>
<td>**dev**</td>
<td>Synthetic</td>
<td>Ephemeral per branch</td>
</tr>
</table>
**No production PII ever leaves prod.** Tenants backtest rules on their own production data, in prod, inside their own tenant boundary.
---
## 11. Rough cost (order of magnitude)
These are infrastructure-only estimates for one T1 cell, before external data costs:
<table header-row="true">
<tr>
<td>Item</td>
<td>\~USD / month</td>
</tr>
<tr>
<td>Aurora core (writer + 3 readers) + storage/IO</td>
<td>3–4 k</td>
</tr>
<tr>
<td>Temporal (database + pods)</td>
<td>1–1.5 k</td>
</tr>
<tr>
<td>MSK (3 brokers) + storage</td>
<td>0.6–1 k</td>
</tr>
<tr>
<td>ElastiCache</td>
<td>0.5–0.8 k</td>
</tr>
<tr>
<td>OpenSearch (3 data + 3 masters)</td>
<td>1.5–2 k</td>
</tr>
<tr>
<td>EKS compute (10–15 nodes)</td>
<td>2.5–4 k</td>
</tr>
<tr>
<td>S3, NAT, load balancers, logs, monitoring, backups, KMS</td>
<td>1.5–3 k</td>
</tr>
<tr>
<td>**Total per T1 cell**</td>
<td>**\~11–16 k** (Querétaro and São Paulo region pricing is typically higher than US regions; São Paulo notably so)</td>
</tr>
</table>
**Vendor costs usually exceed infrastructure.** Per-check registry, biometric and liveness fees (INE, RENAPO via vendors, Serpro Datavalid, identity-verification vendors), sanctions and PEP data licences, and adverse-media feeds often **outweigh infrastructure**. That's why the connector layer meters cost per tenant.
---
## 12. Open items
- [ ] Confirm the expected tenants and peak transactions/sec for the first 12 months, to pick T1 or start closer to T2.
- [ ] Verify managed-service availability in mx-central-1 (Aurora PG version, MSK, OpenSearch, Managed Flink) and in São Paulo.
- [ ] Legal: whether a DR copy out of the country is acceptable per country and per client type (Brazil outsourcing rules and the 2025 amendment Res. CMN 5.274; Mexican fintech cloud rules on primary/secondary providers and data residency).
- [ ] Decide on self-hosted Temporal vs. a hosted option with an in-region data plane.
- [ ] Pick the screening engine: OpenSearch with custom analyzers vs. a dedicated in-memory matcher (benchmark false-positive rate on LATAM name samples).
## Sources
- [AWS – Now open: Mexico (Central) Region](https://aws.amazon.com/blogs/aws/now-open-aws-mexico-central-region)
- [Amazon press – AWS launches region in Mexico](https://press.aboutamazon.com/2025/1/aws-launches-infrastructure-region-in-mexico)
- [DCD – AWS launches cloud region in Mexico](https://www.datacenterdynamics.com/en/news/aws-launches-5bn-cloud-region-in-mexico/)
- [DCD – Google launches Mexican cloud region in Querétaro](https://www.datacenterdynamics.com/en/news/google-launches-mexican-cloud-region-in-queretaro/)
- [Microsoft – List of Azure regions](https://learn.microsoft.com/en-us/azure/reliability/regions-list)
- [AWS – Coming soon: South America (Chile) Region](https://aws.amazon.com/blogs/aws/coming-soon-aws-south-america-chile-region/)
- [RCR Wireless – AWS Chile region by end-2026](https://www.rcrwireless.com/20250528/telco-cloud/aws-launch-region-chile)
- [AWS – Local Zones in Latin America](https://aws.amazon.com/blogs/publicsector/aws-announces-local-zones-latin-america/)
- [Res. CMN 4.893/2021 (ANCORD PDF)](https://www.ancord.org.br/wp-content/uploads/2021/03/Resolucao-CMN-n-4.893-de-26_2_2021.pdf)
- [BCB Comunicado 41.782 (okai)](https://okai.com.br/documento/2024-06-24/comunicado-n-41-782)
- [Res. CMN 5.274/2025 (okai)](https://okai.com.br/documento/2025-12-18/resolucao-cmn-n-5-274)
- [White & Case – Secondary fintech regulations for IFPEs](https://www.whitecase.com/sites/default/files/2021-02/secondary-fintech-regulations-related-to-e-money-institutions-ifpes-spanish.pdf)
- [El CEO – ALAI on fintech cloud regulation](https://elceo.com/economia/alai-pide-a-banxico-y-cnbv-replantear-regulacion-sobre-uso-de-la-nube-para-fintech/)
- [LRITF art. 54](https://mley.mx/LRITF/articulo/54/)
