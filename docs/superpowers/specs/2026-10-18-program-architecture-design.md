# xolotl — Program Architecture and Decomposition (Mexico launch)

**Status:** approved on 2026-10-18 (conversational design approval, then written-spec approval).
**Sources:** Notion database "kyc/aml" (25 pages dated 2026-10-17, snapshot in `docs/research/notion-export/`), its five raw research files (`docs/research/raw/`), the adversarial review of that corpus (`docs/research/review-2026-10-18/`), the sentinel codebase (`~/code/sentinel`), and the existing Xolo MCP server (`~/code/xolo`).

**Decisions made by the product owner on 2026-10-18:**
1. Scope is the full Mexico launch (delivery-plan Sprints 0–8), built as sub-projects in sequence, each with its own spec and plan.
2. Infrastructure is Postgres-first behind ports; Kafka, Temporal, OpenSearch, Redis, ClickHouse and KMS arrive later as adapters.
3. The analyst console is adapted from sentinel's React console and delivered incrementally inside each sub-project.
4. First policy pack `MX/ITF-IFPE`; second `MX/LFPIORPI-AVI` (fr. XVI only).
5. One developer working with an AI coding agent; vendor connectors run in sandbox mode.
6. Build order is **moat-first**: reporting is demonstrable before case management and onboarding.
7. Product and package name is **xolotl** (Python package `xolotl`, CLI `xolotl`, environment prefix `XOLOTL_`). The name `xolo` was the first choice but is already the owner's deployed MCP server (`~/code/xolo`), which stays a separate product. The repository directory stays `kycaml` for now.
8. Staff sign in with local email/password plus TOTP per tenant; JWT claims are OIDC-shaped so a tenant identity provider can be added without API changes.
9. Defaults accepted: readable name tokens in a screening-only index table (option a); local-filesystem and S3 object store adapters; a minimal hosted KYB business flow; LLM document extraction off by default; a fresh repository with files copied from sentinel; Spanish-first UI with every string keyed; the Notion export snapshot committed under `docs/research/`.

---

## 1. Purpose and shape of this document

This is the **program-level spec**. It fixes everything every sub-project must agree on: repository shape, module boundaries, ports, the shared core data model, cross-cutting rules, and the sub-project list and order. Each sub-project then gets its own spec and implementation plan written against this one. Nothing here is code.

Two Notion pages are empty (**03 Screening**, **07 Regulatory reporting**). Their designs are reconstructed from the architecture overview §6.4/§6.7, the feature list §4/§8/§9, delivery-plan tasks SC-1..SC-5 and RR-1..RR-4, and the raw research files. They are marked **(derived)** where they appear.

An adversarial review of the research corpus found 102 inconsistencies, gaps and sentinel divergences. The ones that change how we build are resolved in §5 and listed as errata in Appendix A so the Notion pages can be corrected.

---

## 2. Goals and non-goals

**Goals (Mexico launch, one cell, pooled tenants):**
1. A fintech (ITF/IFPE) or a non-financial VASP (LFPIORPI fr. XVI) tenant can be created, assigned policy packs, and is isolated at the database level from every other tenant.
2. Transactions are monitored against typed rules, and every legally reportable event is captured independently of suspicion.
3. Formato 41 files (ROR/ROI/24-hour/ROIP, including empty reports) and LFPIORPI aviso XML (including informe en ceros and modificatorio) are generated, approved, archived and filed via the manual-upload channel with receipts.
4. Every party is screened against UN, OFAC, EU, UK, SAT 69-B and the tenant's private LPB list, re-screened on every list change, with explainable scores.
5. Alerts become cases worked under four-eyes, Mexican committee rules and statutory clocks; LPB hits run the freeze playbook.
6. Every customer carries a versioned, explainable risk rating that drives review dates.
7. Natural persons and companies are onboarded against the tenant's pack (tiers, checks, assurance levels), with sandbox connectors standing in for vendors.
8. A compliance calendar lists every recurring obligation with auto-evidence from filings.
9. Every decision stores the policy snapshot and the rule, model, list and matcher versions used; the audit chain is per tenant and tamper-evident.

**Non-goals for this program:** Brazil or any second cell; real vendor contracts; native mobile SDKs; Kafka, Temporal, OpenSearch, Redis, ClickHouse, KMS (ports exist, adapters later); machine filing to SITI or SPPLD (no API exists; e.firma is never custodied); the EWRA module; ML or LLM features (ports only); cloud IaC.

---

## 3. Architecture overview

### 3.1 Runtime shape

One Python package, one Docker image, two processes:
- `xolotl serve` — FastAPI API plus the built console.
- `xolotl worker` — outbox dispatcher, job runner, workflow step runner, cron schedules.

One Postgres 17 database with row-level security forced on every tenant table, one object store (local directory in development and tests, S3-compatible otherwise), nothing else. The in-memory screening index is rebuilt from Postgres on startup and on list events.

### 3.2 Repository layout

```
kycaml/                              (directory name; product and package are "xolotl")
├── pyproject.toml · uv.lock · Makefile · Dockerfile · docker-compose.yml · .github/workflows/ci.yml
├── policy-packs/                    versioned YAML rulebooks, reviewed like code
│   ├── MX/ITF-IFPE/v1.yaml          first pack
│   ├── MX/LFPIORPI-AVI/v1.yaml      second pack
│   ├── _units/                      seeded effective-dated UMA, UDI and USD_MXN_FIX values
│   ├── _holidays/MX.yaml            national plus CNBV and SAT non-working days
│   └── _jurisdictions.yaml          FATF and tax-haven lists (seed the risk factor tables)
├── report-defs/                     report definitions are data, not code
│   ├── MX/F41_ROR.yaml · F41_ROI.yaml · F41_ROI24.yaml · F41_ROIP.yaml
│   ├── MX/AVISO_AVI.yaml (+ xsd/avi.xsd) · AVISO_AVI_2027.yaml (slot; XSD not yet published)
│   ├── codetables/MX/*.yaml         tipo_operacion, instrumento_monetario, CASFIM, tipo_alerta, …
│   └── golden/                      byte-identical expected files per definition and fixture period
├── src/xolotl/
│   ├── platform/                    shared kernel; imports nothing from modules/
│   │   ├── config · db (tenant session) · ids (uuid7) · clock · errors · explain
│   │   └── tenancy/ auth/ crypto/ audit/ events/ jobs/ policy/ units/ calendar/ storage/ connectors/ api/ notify/
│   ├── modules/                     feature modules; each owns its tables, routers, events, jobs
│   │   └── tenants/ party/ lists/ screening/ monitoring/ reporting/ cases/ risk/ onboarding/ kyb/ obligations/
│   ├── app.py                       composition root: binds adapters to ports, mounts routers, registers subscribers
│   ├── worker.py                    dispatcher, job runner and scheduler in one process (splittable later)
│   ├── cli.py                       serve, worker, db, tenant, packs, lists, reports, screening eval
│   └── migrations/                  one Alembic tree; RLS and audit DDL helpers
├── console/                         React 19, TypeScript, Tailwind, shadcn; forked from sentinel/frontend
├── tests/                           testcontainers Postgres, savepoint sessions, isolation harness, golden tests
└── docs/                            superpowers/specs, superpowers/plans, adr/, research/
```

Layering is enforced with import-linter: `platform` imports nothing from `modules`; module dependencies are acyclic and declared (§3.3). A single package rather than a uv workspace is chosen for a solo developer; the subpackage split mirrors the Notion libs/services split so a workspace split later is mechanical.

### 3.3 Modules and dependency direction

| Module | Responsibility | Depends on |
|---|---|---|
| platform | config, tenant session, auth, crypto, audit, outbox and events, jobs and workflows, policy resolver, units, calendar, object store, connector SDK, API conventions, notifier | — |
| tenants | tenant lifecycle, tenant keys, pack assignments and overrides, staff users and roles, API keys, webhook endpoints, regulatory profile and branch catalog | platform |
| party | party, person, organization, identifiers with validators, addresses, contacts, relationships, documents, customer_relationship, verification_check, retention | platform, tenants |
| lists | shared list sources, versions, entries and deltas (UN, OFAC, EU, UK, SAT 69-B); tenant private lists (LPB) | platform |
| screening | normalization, phonetic key, matcher, ScreeningIndex port, runs, matches, dispositions, whitelist, delta re-screen, party_name_index | platform, party, lists |
| monitoring | canonical transaction, ingest, enrichment, aggregates, rule kinds, rule hits, captures, decide API, simulation, behaviour profile | platform, party, screening (risk optional) |
| reporting | definition registry, code tables, runs, serializers, approvals, artifacts, filing, receipts, corrections, zero reports, STR drafts | platform, party, monitoring (cases and kyb optional) |
| cases | alerts, cases, queues, visibility, clocks, decisions, committee, narratives, regulator requests, freeze playbook | platform, party, screening, monitoring, reporting (risk optional) |
| risk | scorecard models, factor tables, evaluator, assessments, overrides, rescoring | platform, party, screening, monitoring |
| onboarding | applications, journeys, captures, checks, assurance, decision rulesets, review, consent, step-up and refresh | platform, party, screening, risk, cases |
| kyb | KYB applications, declarations, registry snapshots, ownership graph, UBO engine, reconciliation, extraction port | platform, party, onboarding, screening, risk, cases, reporting |
| obligations | obligation definitions, generator, statuses, evidence, governance registries, board pack | platform, reporting, risk, cases |
| console | analyst and compliance-officer UI | all module APIs |

"Optional" dependencies are consumed through events or nullable inputs so that the moat-first order works: monitoring and reporting run before cases and risk exist, and cases backfill from stored hits and matches when they arrive (§7.2).

Modules communicate through domain events (outbox) for reactions and through direct Python calls for synchronous needs (onboarding calls screening and risk in-process). No module reads another module's tables directly except through that module's service functions.

---

## 4. Ports and adapters

| Port | Key methods | MVP adapter | Later adapter |
|---|---|---|---|
| EventBus | `publish(event)` inside the caller's transaction; `subscribe(type, handler, consumer)`; `dispatch_pending()`; `replay(from, types, tenant)` | `outbox` table, in-process dispatcher, `event_consumption` ledger; webhooks are one subscriber | Kafka relay (Debezium or poller), consumer groups |
| JobQueue / WorkflowRunner | `enqueue(kind, payload, run_after, key)`, `claim()`, `complete()`, `fail()`, `schedule(cron)`; `start_workflow()`, `signal()`, `advance()`, `get_run()` | `job`, `job_schedule`, `workflow_run`, `workflow_step`, `workflow_signal` tables; SKIP LOCKED claims with leases; step graphs as pure functions | Temporal (steps become activities, signals map one to one) |
| ScreeningIndex | `index_version()`, `retire_version()`, `index_private()`, `index_customers()`, `candidates(query, scope, top_k)`, `reverse_candidates(changed, tenant)` | in-memory index built from Postgres (shared aliases, per-tenant private and customer-name shards) | OpenSearch (ICU folding, Beider-Morse es/pt, n-grams) or yente shadow |
| ObjectStore | `put()`, `get()`, `presign_put()`, `presign_get()`, `delete_or_shred()`, `list()` | local filesystem (tests and dev) and S3 via boto3 (MinIO in compose) | S3 Object Lock, per-tenant SSE-KMS |
| KeyProvider | `data_key(tenant, purpose)`, `index_key(tenant)`, `create_tenant_keys()`, `rotate()`, `destroy()` | local master key from the environment wrapping per-tenant DEKs stored in `tenant_key` | AWS KMS per-tenant KEK, BYOK |
| UnitValueSource | `fetch(unit_code, from, to)`, `latest_available()` | seeded CSV loader plus CLI importer; HTTP fetcher wired to recorded fixtures | Banxico SIE and INEGI live fetchers on a daily schedule |
| Connector, ConnectorRouter, CredentialVault | `run(req, ctx)` with retry, timeout, circuit breaker, evidence and metering; `route(check_type, country, tenant)`; `get(tenant, connector, mode)` | sandbox mocks with deterministic magic identities for every check type | Incode, Nubarium or Kiban or Didit, NA-AT or Veridas, Incognia; tenant-owned credentials |
| FilingChannel | `prepare(run)`, `submit(package, creds)`, `capture_receipt()`, `status()` | manual-upload channel: file download, filing task with clock, acuse upload and parsing | Siscoaf SOAP (Brazil), Chile automated channel; Mexico stays manual |
| AggregateStore | `add(txn, buckets)`, `read(key, agg, window)`, `calendar_read(key, agg, period)` | Postgres `agg_bucket` rows updated in the ingest transaction | RocksDB or Redis workers, Flink |
| Clock, Notifier | `now()`, `today(tz)`; `notify(tenant, recipients, template, data, channel)` | system clock, frozen clock in tests; in-app notifications plus log | same; email and Slack adapters |

Rule: feature code depends only on these interfaces. Adapter choice is made once in `app.py` and `worker.py` from configuration.

---

## 5. Cross-cutting design decisions

Each decision names the review findings it resolves (Appendix A ids) so the reasoning is traceable.

**D1 — Tenancy and RLS.** Every tenant-scoped table has `tenant_id uuid NOT NULL` as the first column of its primary key and `ENABLE` plus `FORCE ROW LEVEL SECURITY`. Exactly one permissive policy per table (`tenant_isolation`); every additional policy (case visibility, restricted cases) is created `AS RESTRICTIVE` so policies AND rather than OR. The session contract is fixed: `SET LOCAL app.tenant_id, app.actor_type, app.actor_id, app.roles` per transaction, read with `current_setting(name, true)`. The application role owns nothing and has no `BYPASSRLS`; migrations run as a separate owner role. Shared reference tables (policy packs, lists, units, holidays, report definitions, code tables, model and rule templates) have no `tenant_id` column; they, and the few lookups that must work before a tenant context exists (such as `api_key_lookup`, which carries a `tenant_id` pointer but no row security), are listed in a `SHARED_TABLES` registry. The registry lists every table that is not tenant-isolated; the CI isolation harness asserts every other table is isolated (forced RLS, the single permissive policy, two-tenant invisibility) and every registry table has no row security. There are no nullable-`tenant_id` hybrid tables: platform templates are shared tables; tenant copies are tenant tables carrying `template_version_id`. (A-1.0, A-1.1, A-2.3, A-2.20)

**D2 — Composite foreign keys from migration 0001.** Every tenant table's primary key `(tenant_id, id)` is the composite-FK target (a separate `UNIQUE (tenant_id, id)` would duplicate it, and Postgres discards it; a table whose primary key is anything else, such as a partitioned one, declares its own unique constraint); every tenant-to-tenant reference is `FOREIGN KEY (tenant_id, x_id) REFERENCES x (tenant_id, id)`; references to shared tables are single-column. A SQLAlchemy helper generates them so sub-projects cannot drift. (A-1.3, A-2.4, A-2.22)

**D3 — Identifiers.** UUIDv7 for every primary key. Human-readable references (case number, report folio) are per-tenant sequences, never global integers. (A-2.25)

**D4 — Policy packs and snapshots.** Pack code grammar `<CC>/<SECTOR>-<LICENCE>` (`MX/ITF-IFPE`, `MX/LFPIORPI-AVI`). Tables: `policy_pack`; `policy_pack_version(effective_from, config jsonb)` loaded from YAML by a deploy-time command; `tenant_pack_assignment(tenant_id, pack_version_id, product, effective_from, effective_to, approved_by)`; `tenant_override_version(tenant_id, version, overrides, effective_from, approved_by)`; `policy_snapshot(tenant_id, id, content_hash, content)` whose content records the contributing pack version ids and override version. **Every decision table stores `policy_snapshot_id`** with a composite FK, never a bare pack version. Overrides may only tighten; validation is in code with golden tests. Every threshold, deadline and list entry in a pack carries `{value, unit, legal_basis, confidence, verified_as_of}`; the loader refuses `UNVERIFIED` values unless explicitly allowed per environment. Packs distinguish `mandatory_lists` (legal) from `recommended_lists` (default on, tenant-removable). (A-1.2, A-2.0, A-2.1, A-0.3, A-0.18)

**D5 — Monetary units and calendar.** One table, `unit_value(id, unit_code, valid_from, valid_to, value numeric(20,8), quote_ccy, source, fetched_at, superseded_by)`; corrections are new rows. `ConversionRule` includes `SAME_DAY`, `PRIOR_BANKING_DAY` (Banxico FIX for ITF thresholds), `LAST_DAY_PREV_MONTH` (UDI for ITF tier limits), `PREV_MONTH_AVG`, `SEMIANNUAL_SNAPSHOT`. `calendar` is a dependency of `units`. Every converted amount stored anywhere records the `unit_value.id` it used, so captures and tier decisions are reproducible. A missing value raises and blocks; it is never silently reused. (A-1.7, A-2.2, A-0.1)

**D6 — Encryption and the decrypt boundary.** `tenant_key(tenant_id, purpose DATA|INDEX|BIOMETRIC, version, wrapped_dek, kek_ref, created_at, retired_at)`; ciphertexts carry the key version. P1 identifiers and P2 attributes are stored as `value_enc` plus `value_hash = HMAC(index key, normalized value)` for equality and duplicates. The SQLAlchemy type returns an opaque `Encrypted[T]`; plaintext is obtained only through an explicit `reveal()` in service code. A `VIEW_PII` audit event is emitted when revealed values leave the service layer to a human (detail endpoints, exports, document rendering), not for internal processing. Structured logging has a redaction filter and a test asserts no P1 or P2 field reaches logs. (A-1.13, A-1.16)

**D7 — Readable names for matching (data-model open decision resolved as option a).** All natural and legal names (person, organization, counterparty) are encrypted. A tenant-scoped `party_name_index(tenant_id, party_id, name_kind, norm_name, tokens text[], phonetic_keys text[], matcher_version)` holds plaintext normalized tokens only, readable by the screening DB role, excluded from API serialization, with a `pg_trgm` index for candidate blocking and delta re-screening. LPB entries stay encrypted at rest and are decrypted into the per-tenant in-memory shard. Option b (decrypt into memory only) remains possible behind the same port. (A-1.8, A-3.16)

**D8 — Events.** `outbox(seq bigserial, id uuid, tenant_id, type, key, payload, status, attempts, locked_until, last_error, created_at, published_at)` with payloads carrying ids only. The dispatcher claims `PENDING` rows older than one second ordered by `seq` with `FOR UPDATE SKIP LOCKED`, delivers per key in order to in-process subscribers, and records success in `event_consumption(consumer, event_id)` so redelivery is idempotent. Dead letters after N attempts are visible in the console. Webhooks are a subscriber writing `webhook_delivery` rows (HMAC-SHA256 signature and timestamp headers, exponential retry for 24 hours). `replay()` re-dispatches stored events to a named consumer; it is how later modules catch up on earlier events (§7.2). (A-1.12)

**D9 — Jobs and workflows.** `job(id, tenant_id, kind, key, payload, status, attempts, run_after, locked_until, locked_by, last_error)` with `UNIQUE (tenant_id, kind, key)` for idempotent scheduling; `job_schedule` for cron. Workflows are declared as step graphs of pure, idempotent step functions keyed `(run_id, step_id, attempt)` with ids-only payloads; `workflow_run`, `workflow_step`, `workflow_signal` tables; timers are jobs with `run_after`; waits for humans are signals. Contract tests cover crash and restart, duplicate signal and timeout. Application tables carry `run_id uuid NULL`; no Temporal naming. (A-1.11, A-2.13, A-2.21)

**D10 — Audit.** `audit_event(tenant_id, at, id, actor_type, actor_id, action, entity_type, entity_id, detail, prev_hash, hash)` partitioned monthly; append-only trigger rejecting UPDATE, DELETE and TRUNCATE; hash chain **per tenant** with `pg_advisory_xact_lock(hashtext(tenant_id))`; the digest includes tenant_id and actor_type. Postgres is the system of record at tier T1; WORM streaming is a later subscriber. The action vocabulary includes `VIEW_PII`, `VIEW_RESTRICTED_CASE`, `EXPORT`, `CHANGE_POLICY`. `GET /v1/audit/verify` verifies per tenant. Sentinel's digest, trigger and tamper test are ported. (A-1.24, A-3.4, A-3.15)

**D11 — Transactions.** Postgres is the system of record at T1. The schema follows the canonical `Txn`: `external_id`, `occurred_at`, `party_id`, `relationship_id`, `account_id`, `direction`, `rail`, `instrument`, fiat `amount_minor` plus `currency`, crypto `asset_qty numeric(28,10)` plus `asset_code`, `unit_amounts jsonb` holding per unit the value, the `unit_value_id` and the rule, `counterparty_id`, `channel`, `device`, `crypto jsonb`, `fx jsonb`, `purpose_code`, `period_keys jsonb`, `raw`. Partitioned monthly by `occurred_at` with PK `(tenant_id, occurred_at, id)`. Idempotency via an unpartitioned `transaction_ref(tenant_id, external_id, transaction_id, occurred_at)` written in the same transaction. All references to transactions go through link tables carrying `(tenant_id, transaction_id, occurred_at)` with composite FKs (`rule_hit_transaction`, `alert_transaction`, `reportable_event_transaction`, `report_item`); no `uuid[]` columns in the schema (arrays live only in API DTOs). (A-1.4, A-1.5, A-1.6, A-2.5, A-2.6, A-3.18)

**D12 — Aggregates and ordering without a stream processor.** `agg_bucket(tenant_id, key_kind, key, agg_id, bucket_start, value, hll)` rows (hourly for windows up to 7 days, daily up to 6 months, calendar-period accumulators, and the LFPIORPI six-months-from-first accumulator) are updated inside the ingest transaction. Per-customer ordering is guaranteed by processing a customer's events under a per-key job lease. Late events up to 7 days update buckets and re-evaluate the key; older events go to a backfill path that flags affected reports. The rule evaluator is a pure function over (transaction, aggregates, profile, policy) so simulation replays Postgres history through identical code. (A-1.9, A-3.7, A-3.19)

**D13 — Alerts versus captures.** Rule kinds come in two families. ALERT kinds (sentinel's threshold, aggregate, structuring, velocity, high_risk_jurisdiction, profile_deviation, plus `tier_limit`) produce `rule_hit` rows with `dedup_key` and `UNIQUE (tenant_id, rule_version_id, dedup_key)`; the cases module turns hits into alerts. CAPTURE kinds produce `reportable_event` rows (one per transaction per capture, `UNIQUE (tenant_id, capture_code, transaction_id)`, `period_key`, `capture_group_id` for multiple-operation rules, `supersedes_id`, `unit_values`) consumed by reporting. **A capture never opens a case.** Period close reconciles capture rows against the transaction table. (A-1.15, A-2.9, A-2.12, A-3.2)

**D14 — One report registry (derived design for the blank 07 page).** `report_definition(code, version, jurisdiction, regulator, sector, trigger, field_mapping, serializer, channel, deadline_rule, zero_report_rule, correction_rule, encoding, size_limits, valid_from, valid_to)` loaded from `report-defs/`. Code grammar `MX_F41_ROR`, `MX_F41_ROI`, `MX_F41_ROI24`, `MX_F41_ROIP`, `MX_AVISO_AVI`; version is a separate field everywhere. Captures, report runs, obligations and packs all reference this registry. `regulatory_report` has `prepared_by`, `approved_by` with `CHECK (approved_by <> prepared_by)`, `is_zero`, `period_key`, `corrects_report_id`, `correction_count`, `payload_sha256`, `definition_version_id`, and statuses `DRAFT, NEEDS_REGENERATION, PENDING_APPROVAL, APPROVED, FILED, ACKNOWLEDGED, REJECTED, SUPERSEDED`. `report_item` has PK `(tenant_id, report_id, reportable_event_id)` plus `line_no` and `rendered_fields`; STR items reference a case decision instead. `tenant_regulatory_profile` (RFC, CASFIM supervisor and entity keys, SPPLD activity key, platform domain, entidad colegiada), `tenant_branch` (Formato 41 Anexo 3 fields) and versioned code tables exist before any serializer. Formato 41 v1 uses ASCII output (Ñ to N) with `encoding_confidence: UNVERIFIED` until a SITI QA upload confirms; ROR runs are modelled at month granularity under a quarterly obligation until the regulator's expectation is confirmed; Formato 41 corrections are `UNSUPPORTED` with the manual resubmission practice documented, because none is verified. The 24-hour report is `MX_F41_ROI24` with a legend parameter: LPB hits carry "Lista de Personas Bloqueadas", other 24-hour cases carry "Reporte de 24 horas". (A-1.14, A-2.7, A-2.8, A-2.9, A-2.10, A-0.8, A-0.12, A-0.13, A-0.14, A-0.17)

**D15 — Cases.** The case-management design (F06 §2) is canonical: alert states `NEW, IN_TRIAGE, LINKED, ESCALATED, CLOSED_NO_ACTION`; case states `OPEN, INVESTIGATING, PROPOSED_DECISION, APPROVAL, COMMITTEE, DECIDED, REPORT_DRAFTING, REPORT_FILED, ACTION_PENDING, CLOSED`; `visibility TEAM|RESTRICTED|INSIDER` enforced by a RESTRICTIVE policy plus `case_member`; API-key actors never read RESTRICTED cases; `case_clock` replaces `sla_due_at`; outcome is the latest approved `case_decision`; case types include `INTERNAL_CONCERN`; onboarding and KYB manual reviews are alerts with source `ONBOARDING_REVIEW` or `KYB_REVIEW` rather than a separate task table. Committee participation is a pack or tenant setting (`governance.committee_required`, default true for ITF; small ITFs without a committee use officer four-eyes with the same clocks). Clocks are event-driven: `MX_24H` starts when an LPB match is created, not when an analyst flags it. Sentinel's `allowed_actions` mirror and its exhaustive state by role by action test are kept as the acceptance test; the API also returns machine-readable `blocked_reasons`. (A-2.11, A-2.12, A-2.20, A-3.10, A-0.6, A-0.7)

**D16 — Applications.** One status enum for KYC and KYB applications: `IN_PROGRESS, NEEDS_INFO, PENDING_KYC, MANUAL_REVIEW, APPROVED, REJECTED, ABANDONED, EXPIRED`. `onboarding_application.relationship_id` is nullable; `party_id NOT NULL`, `subject_role CUSTOMER|UBO|LEGAL_REP|ATTORNEY` and `parent_kyb_application_id` support KYB sub-applications for people who are not customers. `verification_check` links to either application kind, exactly one set. (A-2.13, A-2.14)

**D17 — Person model.** `person` gains `birth_state`, `occupation_code` (SINCO), `economic_activity_code`, `pep_status NONE|DOMESTIC|FOREIGN|RCA|INTL_ORG` with `pep_since` and `pep_until`, `is_resident`, `clave_elector_enc`; `party_contact(kind EMAIL|PHONE, value_enc, value_hash, verified_check_id)`; `party_address` gains `municipality_code` (INEGI) and `admin_area_code`. PEP cooling-off is per scope in the pack: national one year; foreign and international-organization none. (A-2.15, A-0.5)

**D18 — Lists and screening data (derived design for the blank 03 page).** One `list_code` registry covering shared sources (`UN_CONSOLIDATED`, `OFAC_SDN`, `OFAC_CONS`, `EU_FSF`, `UK_FCDO`, `MX_SAT_69B`) and tenant private lists (`MX_LPB`, internal). `list_entry` carries `source_entry_key` (UN DATAID, OFAC uid, FCDO UniqueID, SAT RFC) with `UNIQUE (version_id, source_entry_key)`, `entry_status` and `attributes` (69-B presunto or definitivo), `content_hash`; ingestion writes `list_entry_delta(version_id, source_entry_key, change, prev_hash, new_hash)`. The whitelist is keyed `(tenant_id, party_id, list_code, source_entry_key, entry_content_hash)`. `screening_subject` abstracts parties and counterparties. `screening_match` stores `band ALERT|LOW_CONFIDENCE|AUTO_DISCARDED|WHITELISTED|BELOW_CUTOFF`, `scoring_config_version`, `auto_reason`; dispositions are an enum. Scores are 0 to 1 with bands per list kind from the pack: sanctions alert at 0.70 or above with a review band from 0.55; PEP at 0.80 or above; an exact national-ID match floors at 0.95; a same-type ID conflict caps at 0.30. (A-2.16, A-2.17, A-2.18, A-3.8, A-3.9)

**D19 — Configuration lifecycles.** One enum `DRAFT, SIMULATED, IN_REVIEW, APPROVED, ACTIVE, RETIRED` for rule versions, risk model versions and journey definitions; `rule_simulation` is a first-class table; `risk_assessment.trigger` includes `MODEL_CHANGE`. (A-2.19)

**D20 — Two API audiences and one enum source.** Every resource has a tenant or machine view (status plus stable reason codes; never screening or rule details) and a staff view (full typed explanations, `allowed_actions`, `blocked_reasons`). Actor types `STAFF` and `API_CLIENT` are in the token. Enum values are UPPERCASE text stored as non-native enums, defined once in Python and emitted to TypeScript for the console. (A-3.6, A-2.24)

**D21 — Mexico regulatory content for pack v1 (to be confirmed by the compliance review before freeze).** ITF values come from the verified ITF catalogue: ROR cash at or above USD 5,000 at FIX of the prior banking day, quarterly, with an empty report; FX cash at or above USD 500 quarterly; international transfers at or above USD 1,000 monthly within 15 business days; VA at or above 7,500 UDI quarterly; ROI 3 business days after the committee, which decides within 60 days of detection; 24-hour ROI; tiers N1 up to 750 UDI per month with balance up to 1,000, N2 up to 3,000 UDI per month, using the UDI of the last day of the previous month; UBO at 25 percent, in force now; retention 10 years. Pack `effective_from` is the platform start date, not 2026-11-30 (that date belongs to the LFPIORPI pack). Mandatory lists `[MX_LPB, UN_CONSOLIDATED]`; recommended `[OFAC_SDN, MX_SAT_69B, EU_FSF, UK_FCDO]`. The N1 journey is the legal minimum fields plus geolocation, device risk and screening at assurance A0; tenants may tighten. FX-cash, international-transfer and VA reports are captured as events and tracked as `MANUAL_UPLOAD` obligations until their layouts are obtained; only Formato 41 and the aviso have serializers. LFPIORPI fr. XVI captures are `AVI_XVI_A` (operation at or above 210 UMA, or six-month accumulation, using the UMA of each operation's date) and `AVI_XVI_B` (fee at or above 4 UMA, no aggregation), with the one-aviso-under-A rule. Biometric retention per artifact kind (reference frame kept as evidence, liveness video short-lived) is recorded as pending legal confirmation. A tenant is one obligated legal entity; assigning both packs to one tenant requires a product-level role so exactly one crypto capture applies. (A-0.0, A-0.1, A-0.2, A-0.3, A-0.4, A-0.9, A-0.10, A-0.11, A-0.15, A-0.16, A-3.0, A-3.1)

**D22 — Reuse policy for sentinel.** Port algorithms and patterns, not schema: normalize and matcher (re-scored per D18), the OFAC CSV parser (fallback and fixtures; SLS enhanced XML primary), identifier validators (plus RFC homoclave and persona-moral RFC, CLABE, CNPJ, CUIT, RUT), the six rule kinds (fed from aggregates), additive factor explanations (inside the scorecard), the audit digest and trigger (per tenant), the four-eyes workflow and `allowed_actions` test, the typed-explanation convention, the API factory and error model, the test harness, the CI gates, the Dockerfile, the console shell and codegen loop. Sentinel's models, migrations, Mexico pack values, static FX table, demo auth and enum values are not carried over. (A-3.x)

From the existing **Xolo MCP server** (`~/code/xolo`, package `xolo` 0.2.0): `providers/banxico.py` (Banxico SIE client for the USD/MXN FIX series SF43718 and the UDI series SP68257, with a live-or-demo source pattern and a TTL cache) becomes the live `UnitValueSource` fetcher; `validators.py` (RFC with the homoclave check digit and persona-moral support, CLABE check digit with the bank catalogue) extends the identifier validators that sentinel lacked; `providers/sat.py` (CFDI status and the EFOS flag) is a candidate KYB and supplier-check connector; the tiered API-key and quota pattern in `accounts/` informs `api_key` scopes and metering. The MCP server stays a separate product named Xolo; the platform is named xolotl so the two packages never collide.

**D23 — Staff and machine authentication.** Staff: local email and password plus TOTP, managed per tenant, roles `ANALYST, SENIOR_ANALYST, MLRO, AUDITOR_RO, ADMIN`; JWTs carry OIDC-shaped claims (`iss`, `sub`, `tenant_id`, `roles`, `actor_type=STAFF`) so an OIDC adapter can issue the same token later. Machines: scoped API keys with `env sandbox|prod` and `actor_type=API_CLIENT`. Both set the D1 session contract.

---

**Addendum 2026-10-18 (after the two late panel proposals).** (a) Sandbox is a tenant property: `tenant.env SANDBOX|PROD`; a sandbox tenant is an ordinary tenant whose connectors route to mocks and whose filings never leave the platform; API keys carry the same `env` as their tenant. (b) `policy_snapshot.content_hash` is computed over canonical JSON (sorted keys, no whitespace, Decimals as strings) so the hash is stable. (c) The repository has no licence file yet; sentinel is MIT, so copying its code is permitted, and the licence of this repository is a product-owner decision. (d) Independent effort estimates for the same scope were 37 and 46 weeks; the re-baseline points after SP-03 and SP-05 stand.

## 6. Shared core model

Owned by the foundations sub-project: `tenant`, `tenant_key`, `tenant_regulatory_profile`, `tenant_branch`, `staff_user`, `staff_credential` (password hash, TOTP secret), `api_key`, `webhook_endpoint`, `webhook_delivery`, `idempotency_key`, `policy_pack`, `policy_pack_version`, `tenant_pack_assignment`, `tenant_override_version`, `policy_snapshot`, `unit_value`, `holiday`, `audit_event` (partitioned), `outbox`, `event_consumption`, `job`, `job_schedule`, `workflow_run`, `workflow_step`, `workflow_signal`, `notification`.

Party core, owned by the party module and built immediately after: `party`, `person`, `organization`, `party_identifier`, `party_contact`, `party_address`, `party_relationship`, `document`, `customer_relationship`, `verification_check`, `party_name_index`.

Full DDL is written in the foundations and party sub-specs against the Notion data model as corrected by §5.

---

## 7. Sub-project decomposition (moat-first)

### 7.1 The sequence

Effort is for one developer working with an AI coding agent and is re-baselined after SP-03 and SP-05. Each sub-project ends with demoable exit criteria, green CI, and its console increment.

| Id | Sub-project | Scope (summary) | Depends on | Est. weeks |
|---|---|---|---|---|
| SP-01 | Platform kernel | repo and toolchain from sentinel; tenancy, RLS and isolation harness; crypto; audit; outbox, dispatcher and webhooks; jobs and workflows; API conventions; staff login with TOTP and API keys; object store; CLI; console shell fork with tenant login and i18n scaffold | — | 3 |
| SP-02 | Policy packs, units, calendar, connector SDK, party core | pack models, loader, resolver, snapshots; `MX/ITF-IFPE` v1 and `MX/LFPIORPI-AVI` v1 with golden tests; `unit_value`, rules and seeds; Mexico holidays and the deadline DSL; connector SDK, sandbox mocks and metering; party tables with encryption and validators; retention; pack viewer and party pages | SP-01 | 2.5 |
| SP-03 | Lists and screening (derived 03) | ingestors for UN, OFAC, EU, UK and SAT 69-B with versions and deltas; private LPB lists; normalization v2, phonetic key, nicknames, IDF; re-scored matcher; in-memory index; runs, matches, dispositions, whitelist; delta and periodic re-screen; evaluation corpus with a CI floor; screening pages | SP-02 | 4 |
| SP-04 | Transaction monitoring and captures | canonical schema; batch and file ingest; enrichment; partitioned transactions and aggregates; rule kinds, versions and assignments; rule hits with evidence exposed through a staff API; captures; decide API and tier limits; simulation; behaviour profile; Mexico typology library; rules and transaction pages | SP-03 | 4.5 |
| SP-05 | Regulatory reporting (derived 07) | definition registry and code tables; runs; Formato 41 serializer for ROR, ROI, ROI24 and ROIP including empty reports; aviso XML with XSD validation, informe en ceros and modificatorio; approvals; artifacts; manual filing and receipts; deadlines; an STR draft API for staff until cases exist; golden files; reporting pages | SP-04 | 5 |
| SP-06 | Case management and analyst console | alerts from screening matches and rule hits with a backfill job; grouping; queues; visibility; clocks; four-eyes decisions; Mexico committee; narratives; regulator requests; freeze playbook; `case.report_requested` creates ROI, ROI24 and ROIP drafts in reporting; inbox and case workspace | SP-05, SP-03 | 3.5 |
| SP-07 | Customer risk scoring | scorecard models, Mexico factor tables, evaluator, assessments and factors, sync API, event triggers from dispositions and behaviour profiles, overrides with four-eyes, simulate and activate rescoring, case-priority and review-date recomputation; risk pages | SP-03, SP-04, SP-06 | 2.5 |
| SP-08 | KYC onboarding (Mexico) | applications, journeys from the pack, step workflow, captures, sandbox checks, assurance, decision rulesets, review via cases, consent, duplicates, step-up and refresh, evidence pack; onboarding pages | SP-07, SP-06 | 4 |
| SP-09 | KYB and ownership graph (Mexico) | KYB workflow, declarations, registry snapshots, effective ownership and UBO engine, reconciliation, powers, KYC invites, extraction port, minimal hosted business flow; UBO output wired into the aviso 2027 definition slot; KYB pages | SP-08 | 3 |
| SP-10 | Compliance calendar and governance | obligation definitions for ITF and AV, generator, statuses, notifications, auto-evidence from filings and reviews, officer, training and policy registries, board pack; calendar pages | SP-05, SP-06, SP-07 | 2 |
| SP-11 | Hardening and pilot readiness | composite-FK audit, PII log scan, dependency and container scans, restore drill, load tests with written adapter gates, index measurements, developer docs and sandbox guide, UAT support, runbooks | SP-10 | 2.5 |

Total about 36.5 weeks of effort. The delivery plan's squad estimate for the same scope is about 85 engineer-weeks; the difference comes from sentinel reuse, no cloud, Kafka, Temporal or OpenSearch build-out, mock vendors and no native SDKs.

### 7.2 What moat-first implies

- **Monitoring before cases.** Rule hits are stored and visible through a staff API; `monitoring.rule_hit.created` events are published. Risk rating and monitoring profile are optional rule inputs (default profile `STD`) until SP-07.
- **Reporting before cases.** Period-driven reports (ROR, FX cash, VA captures, avisos, zero reports) are complete in SP-05. The ROI, ROI24 and ROIP serializers are also complete in SP-05; until SP-06 their drafts are created through a staff STR-draft API (subject, related persons, narrative blocks). SP-06 adds the trigger from case decisions and the LPB legend path from the freeze playbook.
- **Reporting before KYB.** The current aviso's beneficial-owner block is filled from declared `party_relationship` rows; SP-09 wires the computed UBO determination into the 2027 definition slot.
- **Cases catch up by replay.** When SP-06 lands, a one-off job turns existing open screening matches and rule hits into alerts (or the EventBus `replay()` re-dispatches the stored events to the new consumer). From then on alerts are created live.
- **Risk after cases.** Case priority uses the risk rating when present; SP-07 recomputes priorities and review dates on activation.

### 7.3 Milestones and demos

| After | Cumulative effort | Demo |
|---|---|---|
| SP-03 | about 9.5 weeks | Two isolated tenants. Tenant A uploads its LPB; UN, OFAC, EU, UK and SAT 69-B are ingested; a party screened via the API returns an explained hit; a list delta re-screens the base and alerts only changed entries; tenant B sees nothing; per-tenant audit verify is intact. |
| SP-05 | about 19 weeks | **Report-and-file (delivery-plan M4).** Transactions in; captures; quarterly ROR Formato 41 byte-identical to the golden file; an empty quarter yields the reporte vacío; approval by a second person; download; acuse upload. An AV tenant's month of crypto operations yields an XSD-valid aviso; an empty month yields the informe en ceros; a modificatorio is accepted once. |
| SP-06 | about 22.5 weeks | **Detect and investigate (M3).** Alerts land in queues; four-eyes decision; LPB TRUE_MATCH runs the freeze playbook, delivers the freeze webhook, opens the 24-hour report with a live clock and produces the ROI24 draft with the LPB legend. |
| SP-08 | about 29 weeks | **Onboarding (M2).** Document, liveness, RENAPO and INE mocks, screening, risk, decision and webhook in under two minutes in sandbox; manual-review path through cases. |
| SP-11 | about 36.5 weeks | Pilot-ready: load, restore and security evidence; partner integrates from the docs alone. |

### 7.4 Pre-agreed cut list (in order)

Hosted KYB business flow (SP-09); rule simulation UI (SP-04, API stays); board pack (SP-10); risk model simulation UI (SP-07); second-pack obligations beyond fr. XVI.

### 7.5 Ordering alternative that was considered

Table order (kernel, packs and party, screening, risk, cases, onboarding, KYB, monitoring, reporting, calendar) would show the analyst workflow about seven weeks earlier and the onboarding demo about ten weeks earlier, but the reporting differentiator would only appear after about 31.5 weeks. The product owner chose moat-first.

---

## 8. Testing strategy

- **Isolation harness** (SP-01): auto-discovered tables, two tenants, app role, plus a RESTRICTED versus TEAM case scenario and a cross-tenant FK insertion test.
- **Golden tests**: policy packs (fixtures to decisions), unit conversions across holidays and the 1 February UMA change, deadline calendars for 2027, Formato 41 and aviso files byte-identical, risk profiles, typology scenarios, capture periods.
- **Property tests**: risk determinism and monotonicity; ownership sums and cycles; case state machine (no `REPORT_FILED` without an approved decision by a different person); serializer character rules.
- **Contract tests for ports**: outbox exactly-once effects under redelivery, job retry, lease and crash, workflow restart, duplicate signal and timeout, object store, key provider rotation and shred, connector retry, circuit and evidence, filing channel receipt parsing.
- **Screening evaluation corpus** with a CI recall floor per list kind.
- **Tooling**: sentinel's gates (ruff with all rules, ty, basedpyright in all mode, vulture, pytest with 90 percent coverage on a real Postgres via testcontainers and savepoint rollback per test), plus import-linter and the PII-in-logs scan.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Solo capacity against fixed regulatory dates (Acuerdo 115 in force 2026-11-30; new aviso XML mandatory 2027-06-01) | strict exit criteria, the cut list, re-baseline after SP-03 and SP-05 |
| In-memory index growth (PEP sets of about 2 million entities) | MVP lists are sanctions, 69-B, LPB and a bounded PEP set; measured at 500 thousand and 2 million aliases in SP-11; OpenSearch adapter behind the port |
| Postgres as outbox, queue, aggregate store and decide backend at T1 peaks | batch ingest, SKIP LOCKED, partitions, one indexed read on the decide path; load test in SP-11 with written gates to the Redis and Kafka adapters |
| Formato 41 and aviso gaps (encoding, ROR granularity, UIF catalogs, 2027 XSDs, SITI mechanism) | definitions and code tables are data; golden files from public specs; design-partner validation in SITI QA and the SAT test environment |
| Moat-first leaves the analyst workflow until SP-06 | rule hits and STR drafts have staff APIs and pages from SP-04 and SP-05; alerts backfill by replay |
| Home-grown workflow runner bugs | minimal generic runner, pure step graphs, contract tests, Temporal port |
| RLS correctness with pooling and workers | the session hook refuses tenant-less sessions, jobs carry tenant_id, the harness runs in CI, one permissive policy rule |
| Name matching versus encryption | D7, documented in the security pack |
| Mock-only connectors | vendor-agnostic result contracts, recorded-response tests, launch at A3 with INE as an A4 add-on |
| Spanish UI drift | i18n from SP-01, native-speaker review before pilot |

---

## 10. What every sub-project spec must contain

Scope and out-of-scope; the Notion sources and errata it implements; tables (full DDL with D1 and D2 applied) and events it owns; ports it consumes; API endpoints in both audiences (D20); policy-pack fields it reads; golden, property and contract tests; console increment; exit criteria copied from §7.1 and refined; cut-list items.

---

## Appendix A — Research errata to feed back to Notion

| Id | Page(s) | Issue | Resolution here |
|---|---|---|---|
| A-0.0 / A-3.0 / A-3.1 | sentinel mx.yaml; feature list §6 | sentinel's Mexico pack is the bank regime (USD 7,500; USD-cash 4,000 per month rule) | D21: ITF values from the verified catalogue; no USD-cash alert; FX cash is a capture |
| A-0.1 | 10-f00 §5, 15-f05 §4.5 | ITF tier limits use the UDI of the last day of the previous month; only daily UDI modelled | D5: `LAST_DAY_PREV_MONTH` rule |
| A-0.2 | 10-f00 §4.2 | example ITF pack uses 2026-11-30 (an LFPIORPI date) as effective_from | D21 |
| A-0.3 | 10-f00 §4.2, 14-f04 §3.1 | OFAC and SAT 69-B treated as legally mandatory and an OFAC true match as a legal PROHIBITED | D4 and D21: mandatory versus recommended lists; hard rules carry `basis LAW|POLICY` |
| A-0.4 | 10-f00 §4.2 versus 11-f01 §3 | N1 required checks disagree | D21 |
| A-0.5 | 10-f00 §4.2 | PEP cooling-off applied to all PEPs | D17 |
| A-0.6 | 16-f06 §2.2 | committee mandatory for every Mexico ROI; small ITFs may have none | D15 |
| A-0.7 | 16-f06 §2.3 | 24-hour clock starts on a manual flag | D15 |
| A-0.8 | 10-f00 §4.2 | UN hit mapped to the LPB legend | D14 |
| A-0.9 | 03 §14, 10 §4.2, 18 §1 versus 04 §7 | FX-cash, international-transfer and VA reports in scope versus deferred layouts | D21 |
| A-0.10 | 10-f00 §4.1 | two packs on one tenant may double-report crypto | D21 |
| A-0.11 | 15-f05 §5 | fr. XVI fee test and one-aviso rule missing; UMA across 1 February undefined | D21 |
| A-0.12 | research A.2.3 versus 15 and 18 | ROR quarterly versus AAAAMM period column | D14 (confirm with partner) |
| A-0.13 | research A.2.3 versus 03 RR-3 | Formato 41 encoding unverified | D14 |
| A-0.14 | 03 RR-6 | no verified correction mechanism for Formato 41 | D14 |
| A-0.15 | 10-f00 §3 versus 11-f01 §11 | biometric retention contradicts | D21 (pending legal) |
| A-0.16 | 12-f02 summary | "25 percent from March 2027" is the AV date; ITF already in force | D21 |
| A-0.17 | 06 §6.7 | Mexico reports routed via SPPLD only | D14: SITI for financial entities, SPPLD for AV |
| A-0.18 | 10-f00 §4.2 | no provenance per value | D4 |
| A-1.0 / A-2.20 | 16-f06 §3 | permissive case policy ORs with the tenant policy; unset GUCs | D1 |
| A-1.1 / A-2.3 | 15 §6.1, 14 §4 | nullable tenant_id breaks RLS and UNIQUE | D1 |
| A-1.2 / A-2.0 / A-2.1 | 07 §4 and §6 versus 10 §4 | policy_version_id versus snapshot; tenant_policy shape | D4 |
| A-1.3 / A-2.4 | 10 §2.2 versus 07 §9 | composite FKs now versus later; partitioned FK targets | D2, D11 |
| A-1.4 / A-1.5 / A-1.6 / A-2.5 / A-2.6 / A-3.18 | 07 §8, 15 §2 and §3 | transaction system of record, missing columns, idempotency on a partitioned table, crypto amounts | D11 |
| A-1.7 / A-2.2 | 07 §3 versus 10 §5 | two unit tables | D5 |
| A-1.8 / A-3.16 | 07 §5 and §9, 10 §3, 08 §2 | normalized names | D7 |
| A-1.9 / A-3.19 | 06, 08, 15 | stream processor and aggregate state | D12 |
| A-1.10 | 08 §7, 15 §4.4, 10 §8 | Redis load-bearing | ports with Postgres adapters; decide SLO measured in SP-11 |
| A-1.11 / A-2.21 | 11 §5, 12, 18 §3 | Temporal semantics; foundations tables undefined | D9 |
| A-1.12 | 10 §7.1, 07 §8 | outbox ordering; audit system of record | D8, D10 |
| A-1.13 | 07 §4 versus 10 §3 | key hierarchy | D6 |
| A-1.14 / A-2.7 | 07, 10, 15, 16, 18 | report code namespaces | D14 |
| A-1.15 / A-2.9 | 06, 08, 15 | capture placement and lineage | D13, D14 |
| A-1.16 | 10 §3 | transparent decryption versus PII-view audit | D6 |
| A-1.24 / A-3.4 / A-3.15 | sentinel audit.py | global chain | D10 |
| A-2.8 / A-2.10 | 07 §8 | reporting lifecycle columns; tenant regulator identity | D14 |
| A-2.11 / A-3.17 | 07 §8 versus 16 §2 | case state machines | D15 |
| A-2.12 | 07 §8 versus 16 §2.1, 11 §8 | alert enums; review tasks | D15 |
| A-2.13 / A-2.14 | 07 §6, 11, 12 | application statuses; UBO KYC needs a relationship | D16 |
| A-2.15 | 14 §3.1 versus 07 §5 | person attributes missing | D17 |
| A-2.16 / A-2.17 / A-2.18 | 07 §3 and §7, 10 §4.2 | list codes; delta data; match bands | D18 |
| A-2.19 | 14, 15, 11 | config lifecycles | D19 |
| A-2.24 / A-3.6 | sentinel API and enums | audience split; enum convention | D20 |
| A-3.20 | 06 §9 versus 04 versus 21 | build order conflicts | §7 |
