# SP-02c — Party core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform its first business entity: parties (people and organisations) with encrypted names and identifiers, validated national identifiers, blind-indexed exact search and deduplication, contacts, addresses, ownership edges, documents in the object store, customer relationships whose closure starts the retention clock from the effective policy, a legal hold, a daily purge job and CLI, staff and machine API views with explicit reveal, `party.*` events, and the console pages that create, find and reveal a party.

**Architecture:** `src/xolotl/modules/party/` depends on the platform only (crypto, audit, events, jobs, storage, policy from plan 2a, connectors from plan 2b); `modules/tenants` never imports it (the sweep's job kind is a string literal there). Every PII column is `EncryptedStr`/`EncryptedJson` and equality goes through `blind_index` tags computed from the validator's canonical value. Only the staff reveal endpoint and the document download decrypt for a person, each recording `VIEW_PII`; list, detail and machine views decrypt for processing only and return masks (`****1234`, initials). Writes are service functions that audit and publish ids-only events in the caller's transaction. The retention sweep is a job handler the composition root registers with the object store; the CLI runs the same sweep under a platform context narrowed to one tenant.

**Tech Stack:** the SP-01 stack as it stands on `main` (1c2047e) plus plans 2a and 2b merged. No new dependencies: goldens are JSON (stdlib), retention arithmetic is plan 2a's `add_iso_duration`, uploads are raw bodies (no `python-multipart`).

**Spec:** `docs/superpowers/specs/2026-10-19-sp02-packs-units-calendar-party-design.md` §1 (the 2c bullet), §3 (`RETENTION_PURGE_CRON`), §4.2 (`party`, `person`, `organization`, `party_identifier`, `party_contact`, `party_address`, `party_relationship`, `document`, `customer_relationship`, `verification_check` and the composite FKs of the paragraph after the DDL), §9 (party core and the party API), §10 (`party purge-due`), §11 (party pages), §12 (the `tests/modules/party/*` row and the console line about a party), §13. Program spec: D6 (decrypt boundary, `VIEW_PII` only when a human sees a value), D7 (names encrypted; the `party_name_index` is SP-03's), D16 (`verification_check` minimal, no application link yet), D17 (person fields, `party_contact`, `party_address` codes), D20 (staff and machine views), D22 (validators ported from sentinel's `rules/identity.py` and Xolo's `validators.py`).

## Global Constraints

- Repository `/Users/omaribannez/code/xolotl`; every task is one PR from `main`; `make ci` green at the end of every task (`make lint types vulture importlint`, then `make test`: ruff ALL/preview/88, basedpyright "all", ty, vulture 80, import-linter, pytest 90 % branch coverage against testcontainers Postgres 17), and the console gates (`npm run typecheck && npm run lint && npm test && npm run build`, plus the Playwright smoke) whenever `console/` changes.
- Migration `src/xolotl/migrations/versions/0008_party.py`: `revision = "0008_party"`, `down_revision = "0007_connectors"`; this plan's only migration; `"0008_party"` is appended to `CHAIN` in `tests/platform/test_migration_chain.py` (plan 2a's file). Both `policy_snapshot (tenant_id, id)` (2a) and `connector_call (tenant_id, id)` (2b) exist when C2 starts.
- Package `src/xolotl/modules/party/`: `__init__.py` (empty), `enums.py`, `identifiers.py`, `models.py`, `schemas.py`, `errors.py`, `masking.py`, `events.py`, `service.py`, `documents.py`, `customer.py`, `retention.py`, `routes.py`, `routes_documents.py`, `routes_relationships.py`. Tests under `tests/modules/party/` (with `__init__.py`), goldens under `tests/golden/identifiers/`.
- Import direction (`.importlinter`): `xolotl.platform` imports nothing from `xolotl.modules`; `modules/party` imports `xolotl.platform.*` (including `xolotl.platform.policy` and `xolotl.platform.connectors`) and nothing from `modules/tenants`; `modules/tenants` never imports `modules/party`; only `xolotl.app` and `xolotl.cli` import the party package's registration and sweep.
- Ten tenant tables exactly as spec §4.2, each `class X(TenantTableMixin, Base)` with `tenant_table_args`, composite FKs through `tenant_fk` (or a named `ForeignKeyConstraint` where the convention's name would exceed 63 bytes), `enable_rls("<table>")` in the migration, one isolation factory each in `tests/isolation/factories.py` registered in `register_all()`, the model module imported in `src/xolotl/models.py` (`party_models` in `__all__`). `SHARED_TABLES` is untouched.
- Encrypted columns (`EncryptedStr` unless noted; `sa.LargeBinary()` in the migration): `person.given_names`, `first_surname`, `second_surname`, `birth_date` (ISO `YYYY-MM-DD` text), `clave_elector`; `organization.legal_name`; `party_identifier.value`; `party_contact.value`; `party_address.address` (`EncryptedJson`); `document.extracted`. Blind-index columns (`LargeBinary`, 32 bytes): `party_identifier.value_hash` with kind `id_type.value.lower()`, `party_contact.value_hash` with kind `email` or `phone`. Every tag is computed from the validator's canonical value; never from raw input.
- Setting `retention_purge_cron: str = "0 3 * * *"` (`XOLOTL_RETENTION_PURGE_CRON`) in `src/xolotl/platform/config.py`, validated by plan 2a A1's `_check_cron` (its `@field_validator` decorator gains the field name); listed in `.env.example`, the README configuration table and the `model_fields_set` assertion of `tests/platform/test_config.py::test_env_example_is_a_valid_configuration`.
- Job kind `party.retention_sweep` (`RETENTION_SWEEP_KIND` in `retention.py`); per-tenant schedule named `party.retention_sweep`, `cron = get_settings().retention_purge_cron`, `tz = "UTC"`, `payload = {}`, declared by one more explicit `queue.schedule(...)` call in `schedule_platform_jobs` (`src/xolotl/modules/tenants/service.py`, through the module constant `RETENTION_SWEEP_SCHEDULE = "party.retention_sweep"`: a literal, so tenants never imports party); the three pinned schedule tests (map-api trap T5) are updated in the same task (C5). The handler is registered in `wire_platform` through `register_party(object_store=store)`, placed right after the `store = build_object_store(settings)` line plan 2b's B3b hoists there (so C5 needs plan 2a's A5 and plan 2b's B3b merged).
- Problem codes introduced: `IDENTIFIER_EXISTS` (409, `IdentifierExistsError(DomainError)`), `IDENTIFIER_INVALID` (422, `IdentifierInvalidError(ValidationFailedError)`, detail `"<ID_TYPE>: <REASON>"`, never the value). Reused: `NOT_FOUND`, `CONFLICT`, `VALIDATION_FAILED`, `ROLE_FORBIDDEN`, `SCOPE_MISSING`, `OBJECT_CORRUPT`, `IDEMPOTENCY_*`.
- Audit actions (entity type is the table name, ids as `str(uuid)`, no names or values in `detail`): `party.created`, `party.updated`, `party.identifier_added`, `party.contact_added`, `party.address_added`, `party.relationship_added`, `document.uploaded`, `customer_relationship.opened`, `customer_relationship.closed`, `party.legal_hold_set`, `party.purged`, and `VIEW_PII` (`detail={"group": ..., "fields": [...]}`, plus `"count"` for collections) from the reveal endpoint and the document download only. `record(...)` is called last in each service function, before `publish`.
- Events, `source = "xolotl.party"`, `subject = "party/<id>"`, `key = str(party_id)`, data ids and codes only: `party.created.v1`, `party.updated.v1`, `party.identifier_added.v1`, `party.relationship_opened.v1`, `party.relationship_closed.v1` (envelope `policy_snapshot_id` set), `party.purged.v1`.
- API: routers from `v1_router` only; `POST` needs `Idempotency-Key` (`PATCH` does not); machines use scopes `parties:read` and `parties:write`; staff-only routes declare no scope (`/pii`, document content); legal hold is `roles=(Role.MLRO,)`; lists paginate with `paginate(..., ts_col=Party.created_at, id_col=Party.id, ...)`; handler names are unique operation ids.
- Documents: `MAX_DOCUMENT_BYTES = 25 * 1024 * 1024`; `ALLOWED_CONTENT_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})`; object key `documents/<party_id>/<document_id>`; `put` before the row, `delete` best-effort when the flush fails; purge collects refs, commits, then deletes. Local filesystem store only (no S3).
- Masks: `mask_identifier(value)` = `mask_tail(value)` from plan 2a's `xolotl.platform.text` (`"****" + value[-4:]`, `"****"` when the value has four characters or fewer); `initials("Ana María", "Pérez", "Gómez")` = `"A.M.P.G."`. Plaintext never appears in list responses, machine responses, logs, audit details, outbox payloads, job payloads or `last_error`; a route test scans `caplog` and `everything_stored(admin_engine)` with `leaked(...)`.
- Console: Spanish-first keyed strings in both catalogues, `schema.d.ts` regenerated against the merged API in every console task, `ACCESS.parties` / `ACCESS.partiesWrite` / `ACCESS.legalHold`, PII only through `useMutation` with `gcTime: 0` (never a `useQuery`, never storage), identifier search submitted explicitly (no per-keystroke request).
- Commit trailers: every commit ends with the `Co-Authored-By` and `Claude-Session` lines used on this branch.

## Deviations from the sub-spec (decided here)

- Model and migration column names drop the `_enc` suffix (`given_names`, `value`, `address`, `extracted`, ...): the kernel precedent is `staff_credential.totp_secret` (migration 0004 renamed `totp_secret_enc`); the type (`bytea` holding `Encrypted`) is what the DDL means.
- `person.given_names`, `person.first_surname` and `organization.legal_name` are nullable (spec: `NOT NULL`): the spec's purge "nulls encrypted fields" and a NOT NULL column cannot be nulled; `create_party` requires them. The purge deletes `party_identifier`, `party_contact`, `party_address` and `document` rows instead of nulling them (their encrypted value is the whole row, and the identifier's `(id_type, value_hash)` must be freed for a returning customer), and the `party_relationship` edges of the purged party; `customer_relationship` and `verification_check` rows stay (ids, statuses and references only).
- `char(2)` columns are `String(2)` (as `tenant.country_code`); `person.nationalities` is `ARRAY(String(2))` with `default=list` and no server default (an array server default has no precedent under `compare_server_default=True`).
- Four composite FKs get explicit names because the convention's expansion exceeds Postgres's 63-byte identifier limit: `fk_verification_check_policy_snapshot`, `fk_verification_check_connector_call`, `fk_party_identifier_verified_check`, `fk_party_contact_verified_check`.
- `verification_check` is the minimal table only: spec §9 lists no check-writing service or route and D16's "links to either application kind, exactly one set" arrives with SP-08's `onboarding_application`, so no service function writes it in SP-02 (the isolation factory is its only writer) and its composite FKs are pinned in `test_migration_0008.py`; `party_identifier.verified_check_id` and `party_contact.verified_check_id` exist with their FKs but nothing writes them either. `check_type` and `mode` use plan 2b's `CheckType` and `ConnectorMode` enums; `result` is this module's `VerificationResult` (`PASS`, `FAIL`, `INCONCLUSIVE`).
- The reveal is a separate `GET /v1/parties/{party_id}/pii` (staff only) and `GET /v1/parties/{party_id}` returns masked values to both audiences (the staff shape embeds the sub-collections, the machine shape is the list item): a reveal inside the detail would run on every cached refresh and, as a POST, replay plaintext without a new audit row (map C trap 4); spec §11 itself has the console call "reveal endpoints" from explicit "Revelar" buttons. One `VIEW_PII` is recorded per non-empty field group per request (`profile`, `identifiers`, `contacts`, `addresses`); the document download records one with group `document`.
- Any staff role may reveal (the spec's "revealed by an analyst"; `AUDITOR_RO` included, it is a read); machines never; the legal hold is MLRO only, through `POST /v1/parties/{party_id}/legal-hold` with body `{"legal_hold": true|false}`, idempotent (unchanged = no audit).
- Validator failures are structured: sentinel's free-text findings become `IdentifierReason` codes (`FORMAT`, `CHECK_DIGIT`, `DATE`, `STATE_CODE`, `PREFIX`, `REPEATED_DIGITS`, `BIRTH_DATE_MISMATCH`); the service raises `IdentifierInvalidError("RFC: CHECK_DIGIT")`. Sentinel's SSN validator and `mask` are not carried over (not in the spec's list). `IdentifierType.INE` accepts a 9-digit CIC or a 13-digit OCR (digits and length only; the format authority is spec §13's open item). The passport check digit is verified only when the value is given as the 10-character MRZ field (9 characters, `<`-padded, plus the ICAO 7-3-1 digit); the stored canonical value is the number without padding. Two-digit RFC years pivot on `clock.now().year % 100` (Xolo's rule); CURP uses its century marker.
- Document refusals are 422 `VALIDATION_FAILED` with the details `"document too large"`, `"unsupported document content type"`, `"empty document"` (no new 413 code); there is no filename column, the download is named `<document_id>.<pdf|jpg|png>`; `doc_type` vocabulary: `IDENTITY`, `PROOF_OF_ADDRESS`, `CONSTITUTIVE_ACT`, `POWER_OF_ATTORNEY`, `TAX_CERTIFICATE`, `OTHER`.
- Retention arithmetic: `retention_until = add_iso_duration(ended_at.date(), effective.retention.kyc_after_relationship.value)` with plan 2a's `xolotl.platform.policy.durations.add_iso_duration` (years and months with the day clamped to the target month's length, then days; `ValueError` for anything outside `P[nY][nM][nD]`): this plan parses no duration itself. `effective = effective_policy(session, on=ended_at.date(), product=relationship.product)` and the decision is pinned by `snapshot_policy`; a later closure only moves `retention_until` forward; a tenant with no assignment covering that date cannot close a relationship: plan 2a's `NotFoundError` with code `POLICY_NOT_ASSIGNED` propagates, so `POST /v1/relationships/{id}/close` answers 404 `POLICY_NOT_ASSIGNED` and the relationship stays open. "Past `retention_until`" means `retention_until < today` with `today = clock.now().date()` (UTC; a day's skew is immaterial against a years-long period). A party whose `retention_until` is NULL (never a customer) is never purged by the sweep. `open_relationship` resolves `effective_policy(s, on=started.date(), product=body.product)` as well (the result is discarded, nothing is snapshotted), so a tenant whose assignments do not cover the relationship's product on its start date fails at open with the same 404 `POLICY_NOT_ASSIGNED` instead of opening a relationship it could never close: under plan 2a's D21 rule a tenant-wide assignment never overlaps a per-product one and `product=None` sees only tenant-wide assignments, so on a tenant assigned per product a relationship must name its product. `product` stays optional and plan 2a's `no-product-sees-only-tenant-wide-assignments` golden is unchanged.
- `customer_relationship` opens as `ACTIVE` (`PENDING`, `RESTRICTED`, `BLOCKED` have no transition route in SP-02); closing an `EXITED` relationship is a no-op; the sweep's schedule runs in `tz="UTC"` (03:00 UTC = 21:00 Mexico City).
- `party.updated` / `party.updated.v1` carry the sorted list of changed field names, never values; names, birth date, clave de elector, legal name and identifiers are not patchable (a correction is a later merge flow).
- The staff detail embeds identifiers, contacts, addresses, relationships, documents and customer relationships (a party holds a handful of each), so there are no list routes for sub-resources; `GET /v1/parties` filters `external_ref` through `customer_relationship`.
- Service inputs are the Pydantic request models of `schemas.py` taken whole (`create_party(s, body: PartyCreateRequest)`; the spec's `PartyCreate`), because ruff's `max-args = 8` forbids a keyword per column.
- `IdentifierType` and every other party vocabulary live in `enums.py`, created by C1 (so C2's models import them); `IdentifierError`/`NormalizedIdentifier` live in `identifiers.py`.
- The cron validator is shared: plan 2a A1's `_check_cron` (`@field_validator`, message from `info.field_name`) validates `retention_purge_cron` too; C2 adds the field name to its decorator and writes no validator of its own, so A1 is a precondition of C2.
- `masking.mask_identifier` delegates to `mask_tail` from `xolotl.platform.text` (plan 2a A1's shared helper: `"****" + value[-keep:]`, `"****"` when `len(value) <= keep`) instead of masking by itself; the RFC validator with its check digit stays in `identifiers.py` (Xolo's `_RFC_RE`, not the platform's `RFC_SHAPE`, which checks the shape alone).

## Review Focus

1. **Spelling-insensitive deduplication.** `" pega-760510-xy0 "` and `PEGA760510XY0` must produce one blind-index tag; the second `create_party` must raise 409 `IDENTIFIER_EXISTS` whose `detail` is the existing party id for a `STAFF` actor and the default sentence for an `API_CLIENT`; a failed create leaves no party row. Tests: C3 `test_identifier_spellings_collapse_to_one_blind_index_and_a_duplicate_is_refused`, C6 `test_a_duplicate_identifier_is_a_409_with_the_party_id_for_staff_only`.
2. **No plaintext leaves the service layer by accident.** After a create, a masked detail, a machine read and a reveal, `everything_stored(admin_engine)` and the DEBUG log capture (including `sqlalchemy.engine`) contain none of the names, the RFC, the CURP, the email or the street; outbox payloads and audit details hold ids only. Test: C6 `test_no_plaintext_is_stored_or_logged`, C3 `test_a_person_is_created_with_encrypted_names_and_validated_identifiers`.
3. **Audit precision of reveals.** One `VIEW_PII` row per non-empty field group per `/pii` request and one per document download, none from the list, the masked detail or the machine view; the chain verifies afterwards. Tests: C3 `test_the_staff_view_is_masked_and_the_reveal_records_one_view_pii_per_group`, C6 `test_the_detail_is_masked_for_everyone_and_the_reveal_audits_view_pii`, C7 `test_the_download_records_view_pii_on_the_document`.
4. **The sweep purges exactly the due parties.** Legal hold, an open (non-`EXITED`) relationship, `retention_until == today` and `retention_until IS NULL` each block the purge; a purged party is gone from identifier search, its object-store files are deleted after the commit, and a second run (at-least-once delivery) purges nothing and audits nothing. Test: C5 `test_the_sweep_purges_due_parties_and_nothing_else`, `test_a_second_run_changes_nothing`.
5. **Retention arithmetic and tenant isolation.** `P10Y` from 2027-03-01 gives 2037-03-01 through a real closure, `P1M` from 2026-01-31 gives 2026-02-28 (plan 2a's `add_iso_duration`, pinned here as the contract this plan relies on), a later closure never moves `retention_until` back; a party of tenant A is 404 for tenant B and B's search by the same RFC finds nothing (different INDEX key). Tests: C4 `test_retention_arithmetic_is_plan_2as_add_iso_duration`, `test_closing_computes_retention_from_the_effective_policy_snapshot`, `test_closing_twice_is_a_no_op_and_an_earlier_closure_never_moves_retention_back`; C6 `test_another_tenant_sees_nothing`.

## File structure

```
src/xolotl/modules/party/
  __init__.py                 empty
  enums.py                    PartyType, PartyStatus, PepStatus, IdentifierType, ContactKind, AddressType, RelationshipType, DocumentType, CustomerRelationshipStatus, VerificationResult (C1)
  identifiers.py              IdentifierReason, NormalizedIdentifier, IdentifierError, normalize, validate, check-digit helpers, BANKS, CURP_STATES (C1)
  models.py                   the ten ORM tables (C2)
  errors.py                   IdentifierExistsError, IdentifierInvalidError (C3)
  schemas.py                  request and response models (C3; C4 adds the update, party-relationship, customer-relationship and legal-hold inputs)
  masking.py                  mask_identifier, initials (C3)
  events.py                   EVENT_SOURCE, event type constants, publish_party_event (C3)
  service.py                  create/get party, identifiers, contacts, addresses, queries, summaries, staff view, reveal (C3); update_party, add_relationship (C4)
  documents.py                upload_document, download_document, document_ref, limits (C4)
  customer.py                 OPEN_STATUSES, open_relationship, close_relationship, set_legal_hold (C4)
  retention.py                RETENTION_SWEEP_KIND, due_parties, purge_party, RetentionSweeper, register_party (C5)
  routes.py                   /v1/parties: create, list, detail, pii, identifiers, contacts, addresses (C6); patch, relationships (C7)
  routes_documents.py         /v1/parties/{id}/documents, .../content, /v1/parties/{id}/legal-hold (C7)
  routes_relationships.py     /v1/relationships, /v1/relationships/{id}/close (C7)
src/xolotl/migrations/versions/0008_party.py              (C2)
src/xolotl/models.py                                     + party_models (C2)
src/xolotl/platform/config.py, .env.example, README.md   + retention_purge_cron (C2)
src/xolotl/modules/tenants/service.py                    + party.retention_sweep schedule (C5)
src/xolotl/app.py                                        + register_party in wire_platform (C5); + the three routers (C6, C7)
src/xolotl/cli.py                                        + party_app, party purge-due (C5)
tests/golden/identifiers/{CURP,RFC,CLABE,INE,CPF,CNPJ,CUIT,RUT,NIT,CEDULA_CO,DNI_PE,PASSPORT}.json   (C1)
tests/modules/party/__init__.py, test_identifiers.py (C1), test_migration_0008.py (C2), conftest.py (C3; C4 and C7 append), test_masking.py, test_service.py (C3; C4 appends),
  test_documents.py, test_customer.py (C4), test_retention.py (C5), route_support.py, test_routes.py (C6; C7 appends),
  test_document_routes.py, test_relationship_routes.py (C7)
tests/isolation/factories.py (+10), tests/platform/test_config.py (+), tests/platform/test_migration_chain.py (+) (C2); tests/platform/test_errors.py (+2 rows) (C3)
tests/platform/test_worker.py, tests/modules/tenants/test_kernel_events.py, tests/test_cli.py (+) (C5)
console/src/api/{schema.d.ts (regenerated), types.ts}, auth/{access.ts, access.test.ts, guards.test.tsx}, components/layout/nav.ts, App.tsx,
  i18n/{es-MX.json, en.json, catalogue.test.ts}, e2e/smoke.spec.ts (C8)
console/src/features/parties/{queries.ts, queries.test.tsx, form.ts, form.test.ts, PartyStatusBadge.tsx, IdentifierSearch.tsx, PartiesPage.tsx, PartiesPage.test.tsx,
  CreatePartyPage.tsx, CreatePartyPage.test.tsx, PartyPage.tsx, PartyPage.test.tsx, RevealCard.tsx, LegalHoldCard.tsx} (C8)
console/src/features/parties/{sub-forms.ts, sub-forms.test.ts, PartyCollections.tsx, PartyCollections.test.tsx, EditPartyDialog.tsx, AddIdentifierDialog.tsx, AddContactDialog.tsx,
  AddAddressDialog.tsx, AddRelationshipDialog.tsx, DocumentsCard.tsx, CustomerRelationshipsCard.tsx, dialogs.test.tsx, DocumentsCard.test.tsx, CustomerRelationshipsCard.test.tsx} (C9)
```

---

### Task C1: Identifier validators and the golden corpus

Pure Python; no database. Ports sentinel's `rules/identity.py` and Xolo's `validators.py` into one module with structured results, and copies their sample corpus (plus the types they lack) into JSON goldens.

**Files:**
- Create: `src/xolotl/modules/party/__init__.py` (empty), `src/xolotl/modules/party/enums.py`, `src/xolotl/modules/party/identifiers.py`
- Create: `tests/golden/identifiers/CURP.json`, `RFC.json`, `CLABE.json`, `INE.json`, `CPF.json`, `CNPJ.json`, `CUIT.json`, `RUT.json`, `NIT.json`, `CEDULA_CO.json`, `DNI_PE.json`, `PASSPORT.json`
- Test: `tests/modules/party/__init__.py` (empty), `tests/modules/party/test_identifiers.py`

**Interfaces:**

Consumes: `xolotl.platform.clock.now()` (the RFC two-digit-year pivot), `StrEnum` conventions of `src/xolotl/platform/enums.py` (UPPERCASE values, at most 32 characters). Nothing from sentinel or Xolo is imported: the functions are copied.

Produces:

```python
# src/xolotl/modules/party/enums.py  (every member's value equals its name)
class PartyType(StrEnum): PERSON; ORGANIZATION
class PartyStatus(StrEnum): ACTIVE; MERGED; PURGED
class PepStatus(StrEnum): NONE; DOMESTIC; FOREIGN; RCA; INTL_ORG
class IdentifierType(StrEnum): CURP; RFC; CLABE; INE; CPF; CNPJ; CUIT; RUT; NIT; CEDULA_CO; DNI_PE; PASSPORT
class ContactKind(StrEnum): EMAIL; PHONE
class AddressType(StrEnum): RESIDENTIAL; FISCAL; OPERATIONAL
class RelationshipType(StrEnum): SHAREHOLDER; DIRECTOR; LEGAL_REP; ATTORNEY; CONTROLLER
class DocumentType(StrEnum): IDENTITY; PROOF_OF_ADDRESS; CONSTITUTIVE_ACT; POWER_OF_ATTORNEY; TAX_CERTIFICATE; OTHER
class CustomerRelationshipStatus(StrEnum): PENDING; ACTIVE; RESTRICTED; BLOCKED; EXITED
class VerificationResult(StrEnum): PASS; FAIL; INCONCLUSIVE

# src/xolotl/modules/party/identifiers.py
class IdentifierReason(StrEnum): FORMAT; CHECK_DIGIT; DATE; STATE_CODE; PREFIX; REPEATED_DIGITS; BIRTH_DATE_MISMATCH

@dataclass(frozen=True, slots=True)
class NormalizedIdentifier:
    id_type: IdentifierType
    value: str                                   # canonical form: what is encrypted and blind-indexed
    attributes: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class IdentifierError:
    id_type: IdentifierType
    reason: IdentifierReason

def normalize(id_type: IdentifierType, raw: str) -> str
def validate(id_type: IdentifierType, raw: str, *, birth_date: date | None = None) -> NormalizedIdentifier | IdentifierError
def curp_check_digit(first_17: str) -> int           # sentinel, verbatim
def rfc_check_digit(body: str) -> str                 # Xolo's algorithm over the RFC without its last character (11 or 12 chars, right-justified to 12 with a space)
def clabe_check_digit(first_17: str) -> str           # Xolo, weights 3-7-1
def cpf_check_digits(first_9: str) -> str             # sentinel, verbatim
def cnpj_check_digits(first_12: str) -> str           # weights 5,4,3,2,9,8,7,6,5,4,3,2 then 6,5,4,3,2,9,8,7,6,5,4,3,2; digit = 0 when remainder < 2 else 11 - remainder
def cuit_check_digit(first_10: str) -> int | None     # weights 5,4,3,2,7,6,5,4,3,2; 11 - remainder; 11 -> 0; 10 -> None (no digit exists)
def rut_check_digit(body: str) -> str                 # weights 2..7 cycling from the right, 11 - remainder; 11 -> "0", 10 -> "K"
def nit_check_digit(body: str) -> int                 # sentinel, verbatim (weights 3,7,13,17,19,23,29,37,41,43,47,53,59,67,71; remainder < 2 kept, else 11 - remainder)
def icao_check_digit(field: str) -> int               # weights 7,3,1; "<" = 0, digits as is, A..Z = 10..35
BANKS: Mapping[str, str]                              # Xolo's 24-entry catalogue, verbatim ("002": "BANAMEX", ..., "722": "MERCADO PAGO W")
CURP_STATES: frozenset[str]                           # sentinel's 33 codes (AS ... ZS, NE)
```

Rules, per type (normalisation → format → checks → attributes; the first failing check is the reason):

| Type | `normalize` | Format (after normalising) | Checks in order | `attributes` |
|---|---|---|---|---|
| `CURP` | NFKC, strip, upper, drop spaces and hyphens | sentinel's `_CURP` regex (18 chars: 4 letters with a vowel second, `yymmdd`, `[HMX]`, state, 3 consonants, century marker, digit) | `STATE_CODE` (not in `CURP_STATES`), `DATE` (embedded date not real; century 1900s for a digit marker, 2000s for a letter), `CHECK_DIGIT` (`curp_check_digit`), `BIRTH_DATE_MISMATCH` (when `birth_date` is given and differs) | `birth_date` (ISO), `sex` (`H`/`M`/`X`), `state` |
| `RFC` | NFKC, strip, upper, drop spaces and hyphens; keeps `Ñ` and `&` | Xolo's `_RFC_RE` (3 or 4 letters/`Ñ`/`&`, `yymmdd`, 2 alphanumerics, check char `[A\d]`) | `DATE` (`yymmdd` not a real date; century pivot `clock.now().year % 100`), `CHECK_DIGIT` (`rfc_check_digit`, skipped for `XAXX010101000` and `XEXX010101000`), `BIRTH_DATE_MISMATCH` (persona física only, when `birth_date` is given) | `person_type` (`FISICA` for 13 chars, `MORAL` for 12), `registered_on` (ISO), `generic` (`PUBLICO_EN_GENERAL` / `EXTRANJERO`) only for the two generic RFCs |
| `CLABE` | strip, drop spaces and hyphens | `^\d{18}$` | `CHECK_DIGIT` (`clabe_check_digit`) | `bank_code` (first 3 digits); `bank` only when the code is in `BANKS` (an unknown bank is valid, as in Xolo) |
| `INE` | strip, drop spaces and hyphens | `^\d{9}$` or `^\d{13}$` | none | `format`: `CIC` (9) or `OCR` (13) |
| `CPF` | strip, drop `.` and `-` | `^\d{11}$` | `REPEATED_DIGITS` (all equal), `CHECK_DIGIT` (`cpf_check_digits`) | none |
| `CNPJ` | strip, drop `.`, `/`, `-` | `^\d{14}$` | `REPEATED_DIGITS`, `CHECK_DIGIT` (`cnpj_check_digits`) | none |
| `CUIT` | strip, drop `-` | `^\d{11}$` | `PREFIX` (first two digits not in `{20, 23, 24, 27, 30, 33, 34}`), `CHECK_DIGIT` (`cuit_check_digit`, `None` counts as a mismatch) | none |
| `RUT` | strip, upper, drop `.` and `-` | `^\d{1,8}[0-9K]$` | `CHECK_DIGIT` (`rut_check_digit` over all but the last char) | none |
| `NIT` | strip, drop `.` and `-` | `^\d{10}$` | `CHECK_DIGIT` (`nit_check_digit` over the first 9) | none |
| `CEDULA_CO` | strip, drop `.` | `^[1-9]\d{5,9}$` (sentinel) | none | none |
| `DNI_PE` | strip, drop spaces | `^\d{8}$` | none | none |
| `PASSPORT` | NFKC, strip, upper, drop spaces and hyphens; keeps `<` | `^[A-Z0-9]{6,9}$` (no check), or exactly 10 chars `^[A-Z0-9<]{9}\d$` (MRZ field plus check digit) | `CHECK_DIGIT` (`icao_check_digit` of the first 9 must equal the tenth); canonical value = the first 9 with `<` stripped | none |

A blank or whitespace-only input is `FORMAT` for every type. `normalize` never raises. The canonical value is what `validate` returns in `value` and what later tasks encrypt and blind-index (`blind_index(id_type.value.lower(), canonical, ...)`: `rfc` keeps `Ñ`/`&`, every other kind keeps `A-Z0-9`, which is what these canonical forms contain).

Golden file schema (`tests/golden/identifiers/<ID_TYPE>.json`, one per `IdentifierType` member, stem = the member's value):

```json
{
  "id_type": "RFC",
  "valid":   [{"input": "<raw>", "canonical": "<value>", "attributes": {"<k>": "<v>"}, "birth_date": "YYYY-MM-DD"}],
  "invalid": [{"input": "<raw>", "reason": "<IdentifierReason>", "birth_date": "YYYY-MM-DD"}]
}
```

`attributes` defaults to `{}` and `birth_date` is optional (passed to `validate` when present). Exact contents (check digits verified by computation; the real ones come from the SAT's published test RFCs, OFAC-published CURPs, Receita Federal's own CNPJ, AFIP's CUIT, DIAN's NIT and ICAO Doc 9303's specimen):

- `CURP.json` valid: `CAQR521024HSLRNF06` → `{birth_date: 1952-10-24, sex: H, state: SL}` (also with `"birth_date": "1952-10-24"`); `TOFJ601019HSLRLV08` → `{1960-10-19, H, SL}`; `" caqr521024hslrnf06 "` → canonical `CAQR521024HSLRNF06`; `PEGA050101HDFRRNA5` → `{2005-01-01, H, DF}`; `PEGA760510MDFRMN03` → `{1976-05-10, M, DF}`; `GOMA900101MDFRRR06` → `{1990-01-01, M, DF}`; `LOPJ850315HJCPRN09` → `{1985-03-15, H, JC}`; `MAGE001231MNLRRSA3` → `{2000-12-31, M, NL}`; `SAGC800101HJCNMR02` → `{1980-01-01, H, JC}`. Invalid: `XYZ` FORMAT; `""` FORMAT; `CAQR521024HZZRNF06` STATE_CODE; `CAQR521324HSLRNF06` DATE; `CAQR521024HSLRNF07` CHECK_DIGIT; `CAQR521024HSLRNF06` with `birth_date` `1952-10-25` BIRTH_DATE_MISMATCH.
- `RFC.json` valid: `" eku-900317-3c9 "` → `EKU9003173C9` `{person_type: MORAL, registered_on: 1990-03-17}`; `CACX7605101P8` `{FISICA, 1976-05-10}` (also with `"birth_date": "1976-05-10"`); `IIA040805DZ4` `{MORAL, 2004-08-05}`; `MOFY900516NL1` `{FISICA, 1990-05-16}`; `XAXX010101000` `{FISICA, 2001-01-01, generic: PUBLICO_EN_GENERAL}`; `XEXX010101000` `{FISICA, 2001-01-01, generic: EXTRANJERO}`; `ñ&a900101ab8` → `Ñ&A900101AB8` `{MORAL, 1990-01-01}`; `PEGA760510XY0` `{FISICA, 1976-05-10}`; `ACM0101017A0` `{MORAL, 2001-01-01}`; `SAGC800101AB8` `{FISICA, 1980-01-01}`. Invalid: `EKU9003173C1` CHECK_DIGIT; `EKU9013173C9` DATE; `""`, `ABC`, `1234567890123`, `EKU9003173C9X` FORMAT; `CACX7605101P8` with `birth_date` `1976-05-11` BIRTH_DATE_MISMATCH.
- `CLABE.json` valid: `002010077777777771` `{bank_code: 002, bank: BANAMEX}`; `646180157000000004` `{646, STP}`; `"032 180 00011835971 9"` → `032180000118359719` `{bank_code: 032}`; `012180001234567899` `{012, BBVA MEXICO}`; `072180009876543213` `{072, BANORTE}`. Invalid: `002010077777777772` CHECK_DIGIT; `""`, `12345`, `00201007777777777A` FORMAT.
- `INE.json` valid: `123456789` `{format: CIC}`; `"1234 5678 90123"` → `1234567890123` `{format: OCR}`. Invalid: `12345678`, `12345678901234`, `ABCDEFGHI` FORMAT.
- `CPF.json` valid: `529.982.247-25` → `52998224725`; `52998224725`; `123.456.789-09` → `12345678909`. Invalid: `529.982.247-26` CHECK_DIGIT; `111.111.111-11` REPEATED_DIGITS; `1234` FORMAT.
- `CNPJ.json` valid: `00.394.460/0058-87` → `00394460005887`; `11.222.333/0001-81` → `11222333000181`; `12345678000195`. Invalid: `00.394.460/0058-88` CHECK_DIGIT; `11.111.111/1111-11` REPEATED_DIGITS; `123` FORMAT.
- `CUIT.json` valid: `33-69345023-9` → `33693450239`; `20-12345678-6` → `20123456786`; `27123456780`; `30-71234567-1` → `30712345671`; `20-31234567-7` → `20312345677`. Invalid: `20-12345678-7` CHECK_DIGIT; `99-12345678-0` PREFIX; `2012345678` FORMAT.
- `RUT.json` valid: `12.345.678-5` → `123456785`; `76.086.428-5` → `760864285`; `9.999.999-3` → `99999993`; `7000000-8` → `70000008`; `11111111-1` → `111111111`; `6-k` → `6K`. Invalid: `12.345.678-6` CHECK_DIGIT; `12.345.678-K` CHECK_DIGIT; `ABC` FORMAT; `1` FORMAT.
- `NIT.json` valid: `800.197.268-4` → `8001972684`; `900123456-8` → `9001234568`; `830.114.921-1` → `8301149211`. Invalid: `800197268-5` CHECK_DIGIT; `12-3` FORMAT.
- `CEDULA_CO.json` valid: `52.741.963` → `52741963`; `1020304050`; `123456`. Invalid: `0123`, `12345`, `01234567` FORMAT.
- `DNI_PE.json` valid: `12345678`; `"0123 4567"` → `01234567`. Invalid: `1234567`, `123456789`, `1234567A` FORMAT.
- `PASSPORT.json` valid: `L898902C36` → `L898902C3`; `G123456786` → `G12345678`; `AB123456<4` → `AB123456`; `C1234567<0` → `C1234567`; `AB1234567`; `" g12345 "` → `G12345`. Invalid: `L898902C37` CHECK_DIGIT; `AB12` FORMAT; `AB12345678901` FORMAT; `ab-12!` FORMAT.

- [ ] **Step 1: Write the failing tests** (`tests/modules/party/test_identifiers.py`):

```python
CORPUS_DIR = Path(__file__).parents[2] / "golden" / "identifiers"

def _load() -> list[tuple[IdentifierType, dict[str, object], bool]]:   # (type, entry, valid) over every file
    ...

@pytest.mark.parametrize(("id_type", "entry"), VALID, ids=[...])
def test_valid_samples_normalise_to_their_canonical_value(id_type, entry):
    result = validate(id_type, entry["input"], birth_date=_date(entry.get("birth_date")))
    assert isinstance(result, NormalizedIdentifier), result
    assert result.value == entry["canonical"]
    assert result.attributes == entry.get("attributes", {})

@pytest.mark.parametrize(("id_type", "entry"), INVALID, ids=[...])
def test_invalid_samples_name_their_reason(id_type, entry):
    assert validate(id_type, entry["input"], birth_date=_date(entry.get("birth_date"))) == IdentifierError(id_type, IdentifierReason(entry["reason"]))

def test_every_identifier_type_has_a_corpus():
    assert {p.stem for p in CORPUS_DIR.glob("*.json")} == {t.value for t in IdentifierType}

@pytest.mark.parametrize(("id_type", "entry"), VALID, ids=[...])
def test_normalising_a_canonical_value_changes_nothing(id_type, entry):
    assert normalize(id_type, entry["canonical"]) == entry["canonical"]
    assert validate(id_type, entry["canonical"]).value == entry["canonical"]

def test_check_digit_helpers_agree_with_published_numbers():
    assert curp_check_digit("CAQR521024HSLRNF0") == 6
    assert rfc_check_digit("EKU9003173C") == "9"
    assert rfc_check_digit("ACM0101017A") == "0"
    assert clabe_check_digit("00201007777777777") == "1"
    assert cpf_check_digits("529982247") == "25"
    assert cnpj_check_digits("003944600058") == "87"
    assert cuit_check_digit("3369345023") == 9
    assert cuit_check_digit("2012345678") == 6
    assert rut_check_digit("12345678") == "5"
    assert rut_check_digit("6") == "K"
    assert nit_check_digit("800197268") == 4
    assert icao_check_digit("L898902C3") == 6

def test_rfc_check_digit_ten_is_written_a():          # Xolo's test, over bodies "AAA0101{dd}AB"
    bodies = [f"AAA0101{day:02d}AB" for day in range(1, 29)]
    with_a = [b for b in bodies if rfc_check_digit(b) == "A"]
    assert with_a
    assert all(isinstance(validate(IdentifierType.RFC, b + "A"), NormalizedIdentifier) for b in with_a)

def test_the_error_carries_no_value():
    assert "EKU9003173C1" not in repr(validate(IdentifierType.RFC, "EKU9003173C1"))
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_identifiers.py --no-cov` → `ModuleNotFoundError: No module named 'xolotl.modules.party'`.
- [ ] **Step 3: Implement.** `enums.py` as listed. `identifiers.py`: copy from sentinel `_CURP` (as `_CURP_RE`), `_CURP_STATES` (as `CURP_STATES`), `_CURP_ALPHABET`, `curp_check_digit`, `_two_digit_year`, `_embedded_dob`, `cpf_check_digits`, the CPF rules, `_CEDULA` (as `_CEDULA_RE`), `_NIT` logic, `_NIT_WEIGHTS`, `nit_check_digit`; drop `DocumentCheck`, `validate_document`, `mask`, the SSN code and `sentinel.rules.packs.DocumentType`. Copy from Xolo `_RFC_RE`, `_RFC_ALPHABET`, `_RFC_WEIGHTED_LEN`, `_COMPANY_PREFIX_LEN`, `_MOD`, `_TEN`, `_GENERIC_RFCS` (values become `PUBLICO_EN_GENERAL` / `EXTRANJERO`), `rfc_check_digit` (taking the body), `_registration_date` (with `clock.now()` instead of `datetime.now(UTC)`), `_CLABE_RE`, `_CLABE_WEIGHTS`, `BANKS`, `clabe_check_digit`; drop `RfcResult`, `ClabeResult`, `validate_rfc`, `validate_clabe` and their free-text `problems`. Write the CNPJ, CUIT, RUT, INE, DNI PE and passport checks from the table. Structure: one `_check_<type>(canonical: str, birth_date: date | None) -> NormalizedIdentifier | IdentifierError` per type in a `_CHECKS: Mapping[IdentifierType, Callable[...]]` table, a `_NORMALISERS` table of `(drop_chars, keep_case)` rules, and `validate` = normalise, blank → `FORMAT`, dispatch. Module constants for every magic number (`_CURP_LENGTH = 18`, `_MRZ_FIELD_LEN = 9`, `_CUIT_PREFIXES = frozenset({"20", "23", "24", "27", "30", "33", "34"})`, ...). Write the twelve JSON files with the values above (two-space indentation, keys in the order `id_type`, `valid`, `invalid`).
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/modules/party/test_identifiers.py --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): identifier validators with the golden corpus`.

---

### Task C2: Migration 0008, the ten models, isolation factories and the retention setting

Depends on C1 (enums), plan 2a's A1 (the `_check_cron` validator in `src/xolotl/platform/config.py` and the `.env.example` and README rows this task extends) and A2 (`policy_snapshot`, `tests/platform/test_migration_chain.py`, revision `0006_policy_units_calendar`), and plan 2b (`connector_call`, revision `0007_connectors`, `CheckType`, `ConnectorMode`).

**Files:**
- Create: `src/xolotl/modules/party/models.py`, `src/xolotl/migrations/versions/0008_party.py`
- Modify: `src/xolotl/models.py` (import `xolotl.modules.party.models as party_models`, add `"party_models"` to the sorted `__all__`), `src/xolotl/platform/config.py` (`retention_purge_cron`, named in plan 2a A1's `_check_cron` decorator), `.env.example` (`XOLOTL_RETENTION_PURGE_CRON=0 3 * * *` with a comment "daily party retention sweep, five-field cron, UTC"), `README.md` (row `| \`XOLOTL_RETENTION_PURGE_CRON\` | \`0 3 * * *\` | When the daily party retention sweep runs (UTC). |`), `tests/isolation/factories.py` (ten factories, registered), `tests/platform/test_migration_chain.py` (append `"0008_party"` to `CHAIN`), `tests/platform/test_config.py` (new tests; add `"retention_purge_cron"` to the `model_fields_set` assertion of `test_env_example_is_a_valid_configuration`)
- Test: `tests/modules/party/test_migration_0008.py`

**Interfaces:**

Consumes: `TenantTableMixin`, `tenant_table_args`, `tenant_fk`, `enable_rls` (`xolotl.platform.tenancy.ddl`); `Base` (`xolotl.platform.db`); `enum_column` (`xolotl.platform.sqltypes`); `Encrypted`, `EncryptedStr`, `EncryptedJson`, `encrypt`, `encrypt_json` (`xolotl.platform.crypto.encrypted`); `blind_index` (`xolotl.platform.crypto.blind_index`); `get_key_provider` (`xolotl.platform.crypto.provider`); `uuid7` (`xolotl.platform.ids`); `clock.now`; `CheckType`, `ConnectorMode` (`xolotl.platform.connectors`); `register_isolation_factory`; `migrate.alembic_config`, `migrate.upgrade` and the `database` fixture pattern of `tests/platform/test_migration_0005.py`; `make_settings` and `_clean_environment` of `tests/platform/test_config.py`; `make_tenant`, `system_ctx` (`tests.helpers`), `tenant_session` and `sqlalchemy.exc.IntegrityError` for the FK test.

Produces (`src/xolotl/modules/party/models.py`; every class `X(TenantTableMixin, Base)` with `__tablename__: str` and `__table_args__: tuple[SchemaItem, ...] = tenant_table_args(...)`; `Mapped[T | None]` is the only nullability marker; enum columns `mapped_column(enum_column(E))`; `created_at`/`uploaded_at` `DateTime(timezone=True), server_default=text("now()")`):

| Model / table | Columns beyond `tenant_id`, `id` | Table args |
|---|---|---|
| `Party` / `party` | `party_type: PartyType`; `country_code: str` `String(2)`; `status: PartyStatus` (`default=PartyStatus.ACTIVE, server_default="ACTIVE"`); `retention_until: date \| None` `Date`; `legal_hold: bool` (`default=False, server_default=text("false")`); `created_at: datetime`; `updated_at: datetime` (`server_default=text("now()"), onupdate=clock.now`) | none |
| `Person` / `person` | `given_names: Encrypted[str] \| None`; `first_surname: Encrypted[str] \| None`; `second_surname: Encrypted[str] \| None`; `birth_date: Encrypted[str] \| None`; `birth_country: str \| None` `String(2)`; `birth_state: str \| None` `Text`; `nationalities: list[str]` `ARRAY(String(2)), default=list`; `gender: str \| None`; `occupation_code: str \| None`; `economic_activity_code: str \| None`; `is_resident: bool \| None`; `pep_status: PepStatus` (`default=PepStatus.NONE, server_default="NONE"`); `pep_since: date \| None`; `pep_until: date \| None`; `clave_elector: Encrypted[str] \| None` | `tenant_fk("id", "party")` |
| `Organization` / `organization` | `legal_name: Encrypted[str] \| None`; `trade_name: str \| None`; `legal_form: str \| None`; `incorporation_date: date \| None`; `industry_code: str \| None`; `registry_ref: str \| None`; `country_of_incorporation: str \| None` `String(2)` | `tenant_fk("id", "party")` |
| `VerificationCheck` / `verification_check` | `party_id: UUID \| None`; `check_type: CheckType`; `provider: str`; `mode: ConnectorMode`; `result: VerificationResult`; `details: dict[str, JsonValue] \| None` `JSONB(none_as_null=True)`; `evidence_key: str \| None`; `connector_call_id: UUID \| None`; `policy_snapshot_id: UUID \| None`; `created_at` | `tenant_fk("party_id", "party")`, `ForeignKeyConstraint(["tenant_id", "policy_snapshot_id"], ["policy_snapshot.tenant_id", "policy_snapshot.id"], name="fk_verification_check_policy_snapshot")`, `ForeignKeyConstraint(["tenant_id", "connector_call_id"], ["connector_call.tenant_id", "connector_call.id"], name="fk_verification_check_connector_call")` |
| `PartyIdentifier` / `party_identifier` | `party_id: UUID`; `id_type: IdentifierType`; `country_code: str` `String(2)`; `value: Encrypted[str]`; `value_hash: bytes`; `verified_check_id: UUID \| None`; `valid_from: date \| None`; `valid_to: date \| None`; `created_at` | `tenant_fk("party_id", "party")`, `ForeignKeyConstraint(["tenant_id", "verified_check_id"], ["verification_check.tenant_id", "verification_check.id"], name="fk_party_identifier_verified_check")`, `UniqueConstraint("tenant_id", "id_type", "value_hash")` |
| `PartyContact` / `party_contact` | `party_id: UUID`; `kind: ContactKind`; `value: Encrypted[str]`; `value_hash: bytes`; `verified_check_id: UUID \| None`; `is_primary: bool` (`default=False, server_default=text("false")`); `created_at` | `tenant_fk("party_id", "party")`, `ForeignKeyConstraint([... "verified_check_id"], [...], name="fk_party_contact_verified_check")`, `UniqueConstraint("tenant_id", "party_id", "kind", "value_hash")` |
| `PartyAddress` / `party_address` | `party_id: UUID`; `address_type: AddressType`; `address: Encrypted[dict[str, object]]` `EncryptedJson`; `country_code: str` `String(2)`; `admin_area_code: str \| None`; `municipality_code: str \| None`; `postal_code: str \| None`; `valid_from: date`; `valid_to: date \| None` | `tenant_fk("party_id", "party")` |
| `PartyRelationship` / `party_relationship` | `from_party_id: UUID`; `to_party_id: UUID`; `rel_type: RelationshipType`; `ownership_pct: Decimal \| None` `Numeric(7, 4)`; `control_basis: str \| None`; `valid_from: date`; `valid_to: date \| None`; `source_doc_id: UUID \| None` (no FK, as the spec) | `tenant_fk("from_party_id", "party")`, `tenant_fk("to_party_id", "party")`, `CheckConstraint("from_party_id <> to_party_id", name="from_to_differ")` |
| `Document` / `document` | `party_id: UUID`; `doc_type: DocumentType`; `storage_key: str`; `sha256: bytes`; `size: int` `Integer`; `content_type: str`; `extracted: Encrypted[str] \| None`; `uploaded_by: str`; `uploaded_at: datetime` | `tenant_fk("party_id", "party")` |
| `CustomerRelationship` / `customer_relationship` | `party_id: UUID`; `external_ref: str`; `product: str \| None`; `status: CustomerRelationshipStatus`; `account_tier: str \| None`; `next_review_at: date \| None`; `started_at: datetime \| None`; `ended_at: datetime \| None`; `created_at` | `tenant_fk("party_id", "party")`, `UniqueConstraint("tenant_id", "external_ref")` |

Migration `0008_party.py` (docstring `"""party core: parties, people, organisations, identifiers, contacts, addresses, relationships, documents, customer relationships and verification checks\n\nRevision ID: 0008_party\nRevises: 0007_connectors\nCreate Date: 2026-10-19\n\n"""`): `upgrade()` creates, in this order, `party`, `person`, `organization`, `verification_check`, `party_identifier`, `party_contact`, `party_address`, `party_relationship`, `document`, `customer_relationship`, each `_ = op.create_table(...)` followed by `enable_rls("<table>")`; enum columns `sa.String(length=32)` (defaults `server_default="ACTIVE"`, `"NONE"`); encrypted and hash columns `sa.LargeBinary()`; `nationalities` `postgresql.ARRAY(sa.String(length=2)), nullable=False`; `details` `postgresql.JSONB(), nullable=True`; `ownership_pct` `sa.Numeric(precision=7, scale=4)`; every constraint named: `pk_<table>`, `fk_person_tenant_id_id_party`, `fk_organization_tenant_id_id_party`, `fk_verification_check_tenant_id_party_id_party`, `fk_verification_check_policy_snapshot`, `fk_verification_check_connector_call`, `fk_party_identifier_tenant_id_party_id_party`, `fk_party_identifier_verified_check`, `uq_party_identifier_tenant_id_id_type_value_hash`, `fk_party_contact_tenant_id_party_id_party`, `fk_party_contact_verified_check`, `uq_party_contact_tenant_id_party_id_kind_value_hash`, `fk_party_address_tenant_id_party_id_party`, `fk_party_relationship_tenant_id_from_party_id_party`, `fk_party_relationship_tenant_id_to_party_id_party`, `ck_party_relationship_from_to_differ`, `fk_document_tenant_id_party_id_party`, `fk_customer_relationship_tenant_id_party_id_party`, `uq_customer_relationship_tenant_id_external_ref`. `downgrade()` drops the ten tables in reverse order.

Isolation factories (`tests/isolation/factories.py`): `_party(session, tenant_id) -> Party` (calls `get_key_provider().create_tenant_keys(tenant_id, session=session)`, adds a `PERSON` party with `country_code="MX"`, flushes); `party`, `person` (`given_names=encrypt("Ana", ...)`, `first_surname=encrypt("Pérez", ...)`), `organization` (`legal_name=encrypt("Acme SA de CV", ...)`), `verification_check` (`check_type=CheckType.RFC_VALIDATION`, `provider="sat_rfc"`, `mode=ConnectorMode.SANDBOX`, `result=VerificationResult.PASS`, nullable references `None`), `party_identifier` (`id_type=IdentifierType.RFC`, `country_code="MX"`, `value=encrypt("CACX7605101P8", ...)`, `value_hash=blind_index("rfc", "CACX7605101P8", tenant_id=tenant_id, session=session)`), `party_contact` (`EMAIL`, `encrypt("ana@acme.mx")`, `blind_index("email", ...)`), `party_address` (`RESIDENTIAL`, `encrypt_json({"street": "Reforma 1"}, ...)`, `country_code="MX"`, `valid_from=date(2026, 1, 1)`), `party_relationship` (two parties, `SHAREHOLDER`, `ownership_pct=Decimal("51.0000")`, `valid_from=date(2026, 1, 1)`), `document` (`IDENTITY`, `storage_key=f"documents/{party.id}/{uuid7()}"`, `sha256=bytes(32)`, `size=1`, `content_type="application/pdf"`, `uploaded_by="staff:test"`), `customer_relationship` (`external_ref=f"ref-{uuid7().hex[:8]}"`, `status=CustomerRelationshipStatus.ACTIVE`); `register_all()` gains the ten `register_isolation_factory("<table>", <function>)` lines.

Setting (`config.py`): `retention_purge_cron: str = "0 3 * * *"` with the comment "Daily party retention sweep (five-field cron, evaluated in UTC)"; validation: add `"retention_purge_cron"` to the decorator of plan 2a A1's `_check_cron`, so `@field_validator("units_fetch_cron")` becomes `@field_validator("units_fetch_cron", "retention_purge_cron")`; that validator already checks `croniter.croniter.is_valid(value)` and builds its message from `info.field_name` (`f"XOLOTL_{info.field_name.upper()} must be a five-field cron expression"`), so a bad value fails with `XOLOTL_RETENTION_PURGE_CRON must be a five-field cron expression` and this task writes no validator of its own.

- [ ] **Step 1: Write the failing tests.**

`tests/modules/party/test_migration_0008.py` (copy the `database` fixture and `go_down` helper pattern of `tests/platform/test_migration_0005.py`):

```python
PARTY_TABLES = ["party", "person", "organization", "verification_check", "party_identifier", "party_contact",
                "party_address", "party_relationship", "document", "customer_relationship"]

def test_0008_follows_0007():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_revision("0008_party").down_revision == "0007_connectors"

def test_0008_round_trips(database, admin_engine):
    go_down("0007_connectors")
    assert not set(PARTY_TABLES) & set(inspect(admin_engine).get_table_names())
    migrate.upgrade()
    names = set(inspect(admin_engine).get_table_names())
    assert set(PARTY_TABLES) <= names
    fks = {fk["name"] for fk in inspect(admin_engine).get_foreign_keys("verification_check")}
    assert {"fk_verification_check_policy_snapshot", "fk_verification_check_connector_call"} <= fks

def test_a_check_with_an_unknown_connector_call_is_refused_by_the_foreign_key(engine):
    tenant_id = make_tenant("acme")
    with pytest.raises(IntegrityError, match="fk_verification_check_connector_call"), tenant_session(system_ctx(tenant_id)) as s:
        s.add(VerificationCheck(tenant_id=tenant_id, check_type=CheckType.RFC_VALIDATION, provider="sat_rfc", mode=ConnectorMode.SANDBOX,
                                result=VerificationResult.PASS, connector_call_id=uuid7()))
        s.flush()
```

`pytest.raises` is the outermost context manager (the session exits through the error and rolls back; nested the other way round `tenant_session` would try to commit a failed transaction and raise `PendingRollbackError`); every database test of this plan that expects an exception from inside a session is written this way (C3's test preamble).

`tests/platform/test_config.py` (append; module already uses `_clean_environment`):

```python
def test_retention_purge_cron_defaults_and_reads_its_variable(monkeypatch):
    assert make_settings().retention_purge_cron == "0 3 * * *"
    monkeypatch.setenv("XOLOTL_RETENTION_PURGE_CRON", "30 2 * * *")
    assert make_settings().retention_purge_cron == "30 2 * * *"

def test_retention_purge_cron_must_be_a_cron_expression(monkeypatch):
    monkeypatch.setenv("XOLOTL_RETENTION_PURGE_CRON", "every night")
    with pytest.raises(ValidationError, match="RETENTION_PURGE_CRON"):
        _ = make_settings()
```

and `"retention_purge_cron"` joins the set in `test_env_example_is_a_valid_configuration`. The isolation harness, `tests/platform/test_schema_sync.py` and `tests/platform/test_alembic_env.py` are the other gates (no new test code).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_migration_0008.py tests/platform/test_config.py -k "0008 or retention" --no-cov` → `ImportError`/`ValidationError` (`retention_purge_cron` unknown); `uv run pytest tests/isolation tests/platform/test_schema_sync.py --no-cov` passes only because the tables do not exist yet.
- [ ] **Step 3: Implement** the models, the migration, the registration in `src/xolotl/models.py`, the ten factories, the setting with `.env.example` and README rows, the chain append.
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/modules/party/test_migration_0008.py tests/platform/test_config.py tests/platform/test_schema_sync.py tests/platform/test_alembic_env.py tests/platform/test_migration_chain.py tests/isolation --no-cov` (expect `test_every_tenant_table_has_a_factory` and the behavioural harness green for the ten tables), then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): migration 0008 with the party tables, factories and the retention setting`.

---

### Task C3: Party service: people, organisations, identifiers, contacts, addresses, views and reveal

Depends on C2 and on plan 2a A1's `xolotl.platform.text` (`mask_tail`, which `masking.mask_identifier` delegates to). Service functions only (no routes); every write audits and publishes in the caller's transaction.

**Files:**
- Create: `src/xolotl/modules/party/errors.py`, `schemas.py`, `masking.py`, `events.py`, `service.py`
- Modify: `tests/platform/test_errors.py` (two rows)
- Test: `tests/modules/party/conftest.py` (sample builders, outbox readers, `RolledBackError`), `tests/modules/party/test_masking.py`, `tests/modules/party/test_service.py`

**Interfaces:**

Consumes: the C2 models and C1 `validate`/`normalize`/`IdentifierType`; `bound_context` (`xolotl.platform.db`), `ActorType` (`xolotl.platform.enums`), `encrypt`, `encrypt_json`, `Encrypted.reveal(session=...)`, `blind_index`, `record`, `AuditEvent`, `VIEW_PII` (`xolotl.platform.audit`), `Event`, `publish` (`xolotl.platform.events`), `mask_tail` (`xolotl.platform.text`, plan 2a A1), `uuid7`, `clock.now`, `DomainError`, `NotFoundError`, `ValidationFailedError` (`xolotl.platform.errors`); test helpers `new_tenant`, `staff_session`, `tenant_session`, `system_ctx`, `audit_events`, `everything_stored`, `leaked` (`tests.helpers`); `events_of`/`of_type`/`payloads_of` and the `RolledBackError` class (copied from `tests/modules/tenants/test_kernel_events.py` into `tests/modules/party/conftest.py` as plain functions and a class: a test module is never imported from another).

Produces:

```python
# errors.py
class IdentifierExistsError(DomainError):
    default_code: ClassVar[str] = "IDENTIFIER_EXISTS"
    default_detail: ClassVar[str] = "a party with this identifier already exists"
class IdentifierInvalidError(ValidationFailedError):
    default_code: ClassVar[str] = "IDENTIFIER_INVALID"
    default_detail: ClassVar[str] = "identifier is not valid"

# masking.py
MASK_KEEP = 4
def mask_identifier(value: str, *, keep: int = MASK_KEEP) -> str    # mask_tail(value, keep=keep) from xolotl.platform.text (plan 2a A1): "****" + value[-keep:], "****" when len(value) <= keep
def initials(*names: str) -> str                                    # first letter of every whitespace-separated word, upper, each followed by "."; "" for no words

# events.py
EVENT_SOURCE = "xolotl.party"
PARTY_CREATED = "party.created.v1"; PARTY_UPDATED = "party.updated.v1"; IDENTIFIER_ADDED = "party.identifier_added.v1"
RELATIONSHIP_OPENED = "party.relationship_opened.v1"; RELATIONSHIP_CLOSED = "party.relationship_closed.v1"; PARTY_PURGED = "party.purged.v1"
def publish_party_event(s: Session, event_type: str, *, entity: str, entity_id: UUID, data: dict[str, JsonValue], policy_snapshot_id: UUID | None = None) -> Event
    # the tenants module's _publish, verbatim, plus policy_snapshot_id on the envelope

# schemas.py (Pydantic; PII fields carry Field(json_schema_extra={"pii": True}); no Encrypted anywhere)
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
class IdentifierInput(BaseModel): id_type: IdentifierType; value: str (1..64, pii); country_code: CountryCode = "MX"; valid_from: date | None = None; valid_to: date | None = None
class PersonInput(BaseModel): given_names: str (1..200, pii); first_surname: str (1..100, pii); second_surname: str | None (≤100, pii) = None; birth_date: date | None (pii) = None; birth_country: CountryCode | None = None; birth_state: str | None (≤64) = None; nationalities: list[CountryCode] = []; gender: str | None (≤16) = None; occupation_code: str | None (≤16) = None; economic_activity_code: str | None (≤16) = None; is_resident: bool | None = None; pep_status: PepStatus = PepStatus.NONE; pep_since: date | None = None; pep_until: date | None = None; clave_elector: str | None (≤20, pii) = None
class OrganizationInput(BaseModel): legal_name: str (1..300, pii); trade_name: str | None (≤300) = None; legal_form: str | None (≤64) = None; incorporation_date: date | None = None; industry_code: str | None (≤16) = None; registry_ref: str | None (≤64) = None; country_of_incorporation: CountryCode | None = None
class PartyCreateRequest(BaseModel): party_type: PartyType; country_code: CountryCode = "MX"; person: PersonInput | None = None; organization: OrganizationInput | None = None; identifiers: list[IdentifierInput] = Field(default_factory=list, max_length=10)
    # @model_validator(mode="after"): PERSON requires person and forbids organization, ORGANIZATION the reverse (ValueError "person is required for a PERSON party" / "organization is required for an ORGANIZATION party")
class ContactInput(BaseModel): kind: ContactKind; value: str (1..254, pii); is_primary: bool = False
class AddressLines(BaseModel): street: str (1..200, pii); ext_number: str | None (≤20, pii) = None; int_number: str | None (≤20, pii) = None; neighbourhood: str | None (≤120, pii) = None; city: str | None (≤120, pii) = None
class AddressInput(BaseModel): address_type: AddressType; lines: AddressLines; country_code: CountryCode = "MX"; admin_area_code: str | None (≤8) = None; municipality_code: str | None (≤8) = None; postal_code: str | None (≤10) = None; valid_from: date | None = None; valid_to: date | None = None
class MaskedIdentifier(BaseModel): id: UUID; id_type: IdentifierType; country_code: str; masked_value: str; valid_from: date | None; valid_to: date | None
class PartyResponse(BaseModel): id: UUID; party_type: PartyType; country_code: str; status: PartyStatus; display_name: str | None; identifiers: list[MaskedIdentifier]; legal_hold: bool; retention_until: date | None; created_at: datetime; updated_at: datetime
class PersonAttributes(BaseModel): birth_country: str | None; birth_state: str | None; nationalities: list[str]; gender: str | None; occupation_code: str | None; economic_activity_code: str | None; is_resident: bool | None; pep_status: PepStatus; pep_since: date | None; pep_until: date | None   (from_attributes=True)
class OrganizationAttributes(BaseModel): trade_name, legal_form, incorporation_date, industry_code, registry_ref, country_of_incorporation   (from_attributes=True)
class MaskedContact(BaseModel): id: UUID; kind: ContactKind; masked_value: str; is_primary: bool; created_at: datetime
class AddressResponse(BaseModel): id: UUID; address_type: AddressType; country_code: str; admin_area_code: str | None; municipality_code: str | None; postal_code: str | None; valid_from: date; valid_to: date | None   (from_attributes=True; lines only through the reveal)
class PartyRelationshipResponse(BaseModel): id, from_party_id, to_party_id, rel_type, ownership_pct: Decimal | None, control_basis, valid_from, valid_to, source_doc_id   (from_attributes=True; read by staff_view here, written by C4's add_relationship)
class DocumentResponse(BaseModel): id: UUID; party_id: UUID; doc_type: DocumentType; sha256: str (hex); size: int; content_type: str; uploaded_by: str; uploaded_at: datetime
class CustomerRelationshipResponse(BaseModel): id, party_id, external_ref, product, status: CustomerRelationshipStatus, account_tier, next_review_at, started_at, ended_at, created_at   (from_attributes=True)
class PartyStaffResponse(PartyResponse): person: PersonAttributes | None; organization: OrganizationAttributes | None; contacts: list[MaskedContact]; addresses: list[AddressResponse]; relationships: list[PartyRelationshipResponse]; documents: list[DocumentResponse]; customer_relationships: list[CustomerRelationshipResponse]
class RevealedIdentifier(BaseModel): id: UUID; id_type: IdentifierType; value: str
class RevealedContact(BaseModel): id: UUID; kind: ContactKind; value: str
class RevealedAddress(BaseModel): id: UUID; address_type: AddressType; lines: AddressLines
class PartyPiiResponse(BaseModel): party_id: UUID; given_names: str | None; first_surname: str | None; second_surname: str | None; birth_date: date | None; clave_elector: str | None; legal_name: str | None; identifiers: list[RevealedIdentifier]; contacts: list[RevealedContact]; addresses: list[RevealedAddress]

# service.py
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\d{8,15}$")           # applied after stripping spaces, hyphens, parentheses, dots and one leading "+"
def create_party(s: Session, body: PartyCreateRequest) -> Party
def get_party(s: Session, party_id: UUID, *, for_update: bool = False) -> Party           # NotFoundError (another tenant's row is invisible under RLS, hence 404)
def add_identifier(s: Session, party_id: UUID, body: IdentifierInput) -> PartyIdentifier
def add_contact(s: Session, party_id: UUID, body: ContactInput) -> PartyContact
def add_address(s: Session, party_id: UUID, body: AddressInput) -> PartyAddress
# update_party and add_relationship (ownership edges) are C4's additions to this module
def parties_query(s: Session, *, status: PartyStatus | None = None, external_ref: str | None = None, id_type: IdentifierType | None = None, value: str | None = None) -> Select[tuple[Party]]
def summaries(s: Session, parties: Sequence[Party]) -> list[PartyResponse]              # one query per subtype table and one for identifiers, not N+1
def summary(s: Session, party: Party) -> PartyResponse
def staff_view(s: Session, party_id: UUID) -> PartyStaffResponse
def reveal_pii(s: Session, party_id: UUID) -> PartyPiiResponse
def document_response(row: Document) -> DocumentResponse                                 # sha256 as hex
def masked_identifier(s: Session, row: PartyIdentifier) -> MaskedIdentifier               # mask_identifier(row.value.reveal(session=s)), no audit
def masked_contact(s: Session, row: PartyContact) -> MaskedContact
```

Semantics the signatures leave open:

- `create_party`: `tenant_id = bound_context(s).require_tenant_id()`; `Party(tenant_id=..., party_type=..., country_code=...)`, `s.add`, `s.flush()`; the subtype row with every encrypted field sealed explicitly (`encrypt(text, tenant_id=tenant_id, session=s)`, `birth_date.isoformat()`); each identifier through the private `_add_identifier(s, party, body)` (validation, dedup, row; no audit, no event); then one `record(s, AuditEvent(action="party.created", entity_type="party", entity_id=str(party.id), detail={"party_type": ..., "identifier_ids": [...]}))` and one `publish_party_event(s, PARTY_CREATED, entity="party", entity_id=party.id, data={"party_id": ..., "party_type": ..., "identifier_ids": [...]})`.
- `_add_identifier`: `result = validate(body.id_type, body.value, birth_date=_birth_date_of(s, party))` (the person's `birth_date.reveal(session=s)` when the party is a person with one, else `None`); an `IdentifierError` → `IdentifierInvalidError(f"{result.id_type.value}: {result.reason.value}")`; `tag = blind_index(body.id_type.value.lower(), result.value, tenant_id=tenant_id, session=s)`; `select(PartyIdentifier).where(PartyIdentifier.id_type == body.id_type, PartyIdentifier.value_hash == tag)` → a row means `IdentifierExistsError(str(existing.party_id))` when `bound_context(s).actor_type is ActorType.STAFF`, else `IdentifierExistsError()`; `PartyIdentifier(..., value=encrypt(result.value, ...), value_hash=tag)`; a concurrent duplicate raising `IntegrityError` at flush is left to the 500 handler (not caught).
- `add_identifier` = `_add_identifier` on `get_party(...)` plus `record("party.identifier_added", entity_type="party_identifier", entity_id=str(row.id), detail={"party_id": ..., "id_type": ...})` and `publish_party_event(IDENTIFIER_ADDED, entity="party", entity_id=party.id, data={"party_id", "identifier_id", "id_type"})`.
- `add_contact`: EMAIL → `canonical = value.strip().lower()`, must match `EMAIL_RE`; PHONE → strip `[\s().-]`, drop one leading `+`, must match `PHONE_RE`; else `ValidationFailedError("invalid email")` / `("invalid phone")`; `tag = blind_index("email"|"phone", canonical, ...)`; an existing `(party_id, kind, tag)` row → `DomainError("contact already exists")`; `is_primary=True` clears `is_primary` on the party's other contacts of that kind; audit `party.contact_added` (`detail={"party_id", "kind"}`).
- `add_address`: `valid_from = body.valid_from or clock.now().date()`; `address=encrypt_json(body.lines.model_dump(mode="json", exclude_none=True), tenant_id=..., session=s)`; audit `party.address_added` (`detail={"party_id", "address_type"}`).
- `parties_query`: `select(Party)`; `status` → `where(Party.status == status)`; `external_ref` → `where(Party.id.in_(select(CustomerRelationship.party_id).where(CustomerRelationship.external_ref == external_ref)))`; `id_type` and `value` both given → `validate` (invalid → `IdentifierInvalidError`), tag, `where(Party.id.in_(select(PartyIdentifier.party_id).where(PartyIdentifier.id_type == id_type, PartyIdentifier.value_hash == tag)))`; exactly one of the pair → `ValidationFailedError("id_type and value go together")`.
- `summaries`/`summary`: `display_name = initials(given, first, second or "")` for a person, `initials(legal_name)` for an organisation (values through `.reveal(session=s)`, no audit), `None` when `status is PartyStatus.PURGED` (encrypted columns are `None` then); identifiers as `MaskedIdentifier(masked_value=mask_identifier(row.value.reveal(session=s)))`.
- `staff_view`: `summary` plus `PersonAttributes`/`OrganizationAttributes` (plain columns only), contacts masked the same way, `addresses` (plain columns), `relationships` (rows where `from_party_id == id` or `to_party_id == id`), `documents`, `customer_relationships`. Every sub-collection, here and in `summaries` and `reveal_pii`, is ordered by `(created_at, id)` where the table has `created_at` (identifiers, contacts, customer relationships), by `(uploaded_at, id)` for documents and by `(valid_from, id)` for addresses and relationships: insertion order, since uuid7 ids are strictly increasing within a process.
- `reveal_pii`: `get_party`; groups in this order and only when non-empty: `profile` (person: `given_names`, `first_surname`, `second_surname`, `birth_date`, `clave_elector` that are not `None`; organisation: `legal_name`), `identifiers`, `contacts`, `addresses`; for each group `_ = record(s, AuditEvent(action=VIEW_PII, entity_type="party", entity_id=str(party_id), detail=...))` with `detail={"group": "profile", "fields": [<names revealed>]}` or `{"group": "identifiers", "fields": ["value"], "count": n}` (`contacts` likewise; `addresses` with `"fields": ["lines"]`), then `.reveal(session=s)` each value; `birth_date` parsed back with `date.fromisoformat`; `lines` as `AddressLines.model_validate(row.address.reveal(session=s))`.

`tests/modules/party/conftest.py` (C3; C4 appends fixtures) exports the builders used by every later test: `ANA = PartyCreateRequest(party_type=PartyType.PERSON, person=PersonInput(given_names="Ana María", first_surname="Pérez", second_surname="Gómez", birth_date=date(1976, 5, 10)), identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value="pega-760510-xy0"), IdentifierInput(id_type=IdentifierType.CURP, value="PEGA760510MDFRMN03")])`, `CARLOS` (`"Carlos"`, `"Sánchez"`, `"Gómez"`, born `1980-01-01`, RFC `SAGC800101AB8`, CURP `SAGC800101HJCNMR02`), `ACME = PartyCreateRequest(party_type=PartyType.ORGANIZATION, organization=OrganizationInput(legal_name="Acme Servicios SA de CV", trade_name="Acme"), identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value="ACM0101017A0")])`, the outbox readers `events_of(tenant_id)`, `of_type(tenant_id, event_type)`, `payloads_of(tenant_id)`, and `class RolledBackError(Exception)` for the rollback idiom (defined here: `tests/modules/tenants/test_kernel_events.py` is a test module and is never imported).

- [ ] **Step 1: Write the failing tests.**

`test_masking.py` (no database): `mask_identifier("PEGA760510XY0") == "****0XY0"`, `mask_identifier("1234") == "****"`, `mask_identifier("ana@acme.mx") == "****e.mx"`, `initials("Ana María", "Pérez", "Gómez") == "A.M.P.G."`, `initials("Acme Servicios SA de CV") == "A.S.S.D.C."`, `initials("") == ""`, `initials("  ana  ", "") == "A."`.

`test_service.py` (every test opens `with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:` unless it says otherwise; `tenant` is the conftest fixture). Whenever a test expects an exception from inside a session, `pytest.raises` is the outermost context manager, `with pytest.raises(...) as refused, staff_session(...) as s:`, as map-api's rollback idiom has it: nested the other way round the session swallows the error, exits normally and commits whatever the service flushed before raising (a half-created party, or a failed transaction that then raises `PendingRollbackError` on commit). This rule holds for every database test of this plan:

```python
def test_a_person_is_created_with_encrypted_names_and_validated_identifiers(engine, tenant, admin_engine):
    with staff_session(...) as s:
        party = service.create_party(s, ANA)
        person = s.get_one(Person, (tenant.tenant_id, party.id))
        assert isinstance(person.given_names, Encrypted)
        ids = s.scalars(select(PartyIdentifier).where(PartyIdentifier.party_id == party.id)).all()
        assert sorted(i.id_type for i in ids) == [IdentifierType.CURP, IdentifierType.RFC]
        assert all(len(i.value_hash) == 32 for i in ids)
    assert audit_events(tenant.tenant_id)[-1].action == "party.created"
    (made,) = of_type(tenant.tenant_id, "party.created.v1")
    assert made.data == {"party_id": str(party.id), "party_type": "PERSON", "identifier_ids": [str(i.id) for i in ids]}
    assert made.key == str(party.id) and made.subject == f"party/{party.id}"
    for secret in ("Ana María", "Pérez", "PEGA760510XY0", "PEGA760510MDFRMN03", "1976-05-10"):
        assert leaked(everything_stored(admin_engine), secret.encode()) == []
        assert secret not in payloads_of(tenant.tenant_id)

def test_identifier_spellings_collapse_to_one_blind_index_and_a_duplicate_is_refused(engine, tenant):
    with staff_session(...) as s:
        first = service.create_party(s, ANA)
    again = ANA.model_copy(update={"identifiers": [IdentifierInput(id_type=IdentifierType.RFC, value=" PEGA 760510 xy0 ")]})
    with pytest.raises(IdentifierExistsError) as refused, staff_session(...) as s:
        _ = service.create_party(s, again)
    assert (refused.value.code, refused.value.detail) == ("IDENTIFIER_EXISTS", str(first.id))
    machine = TenantContext(tenant_id=tenant.tenant_id, actor_type=ActorType.API_CLIENT, actor_id="key:1")
    with pytest.raises(IdentifierExistsError) as refused, tenant_session(machine) as s:
        _ = service.create_party(s, again)
    assert refused.value.detail == "a party with this identifier already exists"
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalar(select(func.count()).select_from(Party)) == 1

def test_an_invalid_identifier_is_refused_with_its_reason_and_without_its_value(engine, tenant):
    bad = ANA.model_copy(update={"identifiers": [IdentifierInput(id_type=IdentifierType.RFC, value="EKU9003173C1")]})
    with pytest.raises(IdentifierInvalidError) as refused, staff_session(...) as s:
        _ = service.create_party(s, bad)
    assert (refused.value.code, refused.value.detail) == ("IDENTIFIER_INVALID", "RFC: CHECK_DIGIT")
    assert "EKU9003173C1" not in str(refused.value)

def test_a_curp_must_agree_with_the_declared_birth_date(engine, tenant):
    # ANA lists the RFC first and the RFC check would raise "RFC: BIRTH_DATE_MISMATCH" before the CURP is reached, so the request carries the CURP alone:
    # assert ANA.person is not None; request = ANA.model_copy(update={"person": ANA.person.model_copy(update={"birth_date": date(1976, 5, 11)}),
    #     "identifiers": [IdentifierInput(id_type=IdentifierType.CURP, value="PEGA760510MDFRMN03")]})
    # with pytest.raises(IdentifierInvalidError) as refused, staff_session(...) as s: _ = service.create_party(s, request)
    # refused.value.detail == "CURP: BIRTH_DATE_MISMATCH"; no party row afterwards

def test_search_by_identifier_uses_the_blind_index_and_never_decrypts(engine, tenant):
    # after create_party(ANA): parties_query(id_type=RFC, value="pega 760510 xy0") -> [party]; value="EKU9003173C9" -> []; value="EKU9003173C1" -> IdentifierInvalidError;
    # id_type without value -> ValidationFailedError("id_type and value go together"); no VIEW_PII row exists afterwards

def test_an_organisation_is_summarised_with_initials_and_masked_identifiers(engine, tenant):
    # create_party(ACME); summary(...): display_name == "A.S.S.D.C.", identifiers[0].masked_value == "****17A0", party_type ORGANIZATION

def test_contacts_normalise_and_deduplicate(engine, tenant):
    # add_contact EMAIL " Ana@Acme.MX " then "ana@acme.mx" -> DomainError("contact already exists") (pytest.raises outermost, its own session); PHONE "+52 (55) 1234-5678" ->
    # [c.value for c in reveal_pii(...).contacts] == ["ana@acme.mx", "525512345678"] (ordered by (created_at, id): the email was added first); masked "****e.mx", "****5678"; "not-an-email" -> ValidationFailedError

def test_addresses_keep_lines_encrypted_and_codes_in_clear(engine, tenant, admin_engine):
    # add_address(RESIDENTIAL, lines street="Reforma 222", postal_code="06600", admin_area_code="09", municipality_code="015"); staff_view(...).addresses[0].postal_code == "06600"; leaked(everything_stored(admin_engine), b"Reforma 222") == []; reveal_pii(...).addresses[0].lines.street == "Reforma 222"

def test_the_staff_view_is_masked_and_the_reveal_records_one_view_pii_per_group(engine, tenant):
    # view = staff_view(ana.id): display_name "A.M.P.G.", identifiers masked, person.pep_status NONE, no "Ana" in view.model_dump_json()
    # pii = reveal_pii(ana.id): given_names "Ana María", birth_date date(1976, 5, 10), identifiers values {"PEGA760510XY0", "PEGA760510MDFRMN03"}
    # [(e.action, e.detail) for e in audit_events(...) if e.action == VIEW_PII] == [
    #   ("VIEW_PII", {"group": "profile", "fields": ["given_names", "first_surname", "second_surname", "birth_date"]}),
    #   ("VIEW_PII", {"group": "identifiers", "fields": ["value"], "count": 2})]
    # entity_type == "party", entity_id == str(ana.id); verify_chain(s).valid

def test_a_party_of_another_tenant_is_not_found(engine, tenant):
    # other = new_tenant("beta", "admin@beta.mx"); get_party under other -> NotFoundError; parties_query by ANA's RFC under other -> []

def test_a_failed_create_leaves_nothing_behind(engine, tenant):
    # with pytest.raises(RolledBackError), staff_session(...) as s: _ = service.create_party(s, ANA); raise RolledBackError   (conftest's class)
    # afterwards: events_of(tenant.tenant_id) == [], no party row, no audit "party.created"
```

`tests/platform/test_errors.py`: append `(IdentifierExistsError, 409, "IDENTIFIER_EXISTS")` and `(IdentifierInvalidError, 422, "IDENTIFIER_INVALID")` to the parametrised `(cls, status, code)` list of `test_error_status_and_default_code` (imported from `xolotl.modules.party.errors`, as plan 2a appends its own codes).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_masking.py tests/modules/party/test_service.py tests/platform/test_errors.py --no-cov` → `ImportError` on `xolotl.modules.party.service` and `xolotl.modules.party.errors`.
- [ ] **Step 3: Implement** the five modules per the interfaces and semantics above. Keep `service.py` under mccabe 10 per function (`_add_identifier`, `_person_lines`, `_masked_identifiers`, `_group_reveal` helpers); tag every PII schema field with `json_schema_extra={"pii": True}`; never log a value.
- [ ] **Step 4: Run to verify pass.** The three test files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): party service with encrypted names, blind-indexed identifiers, contacts and addresses`.

---

### Task C4: Documents, customer relationships with policy retention, legal hold, party updates and relationship edges

Depends on C3 and on plan 2a's Task A7 merged: `xolotl.platform.policy` (`assign_pack`, `effective_policy`, `snapshot_policy`; A6's `add_iso_duration`, `install_pack` and both MX packs on disk come with it) and the test helper `tests/platform/policy_support.py::install_both_packs`, which A7 creates. Also owns the two lighter service functions C3 would otherwise carry, `update_party` and `add_relationship` (ownership edges), with their schemas and tests; C7 mounts their routes.

**Files:**
- Create: `src/xolotl/modules/party/documents.py`, `src/xolotl/modules/party/customer.py`
- Modify: `src/xolotl/modules/party/schemas.py` (`PersonUpdate`, `OrganizationUpdate`, `PartyUpdateRequest`, `PartyRelationshipInput`, `RelationshipOpenRequest`, `RelationshipCloseRequest`, `LegalHoldRequest`), `src/xolotl/modules/party/service.py` (`update_party`, `add_relationship`), `tests/modules/party/conftest.py` (`local_store`, `itf_pack`)
- Test: `tests/modules/party/test_documents.py`, `tests/modules/party/test_customer.py`, `tests/modules/party/test_service.py` (append two tests)

**Interfaces:**

Consumes: `ObjectStore`, `ObjectRef`, `ObjectIntegrityError` (`xolotl.platform.storage`), `LocalFsObjectStore` (`xolotl.platform.storage.local`); from `xolotl.platform.policy` (plan 2a): `effective_policy(session, *, on: date, product: str | None) -> EffectivePolicy` (called by `open_relationship`, which discards the result, and by `close_relationship`), `snapshot_policy(session, effective: EffectivePolicy) -> UUID`, `add_iso_duration(day: date, text: str) -> date` (years and months with the day clamped to the month's length, then days; `ValueError` otherwise, `"P"` included), `assign_pack(session, *, pack_version_id, product, effective_from, approved_by) -> UUID`; `install_both_packs(admin_engine) -> dict[str, UUID]` (`tests/platform/policy_support.py`, plan 2a A7: both MX packs through an owner session with `allow_unverified=True`, version ids keyed by pack code, `"MX/ITF-IFPE"` and `"MX/LFPIORPI-AVI"`). Plan 2a's `EffectivePolicy(PolicyBody)` keeps `retention: RetentionPolicy` with `kyc_after_relationship: Provenanced[IsoDuration]`, so the duration string is `effective.retention.kyc_after_relationship.value`, and `effective_policy` raises `NotFoundError("no policy pack assigned for that date and product", code="POLICY_NOT_ASSIGNED")` when no assignment covers the date. Also `NotFoundError`, `DomainError`, `ValidationFailedError` (`xolotl.platform.errors`); C3's `get_party`, `publish_party_event`, `PARTY_UPDATED`, `document_response`, `PartyRelationshipResponse`, the conftest builders.

Produces:

```python
# documents.py
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"application/pdf", "image/jpeg", "image/png"})
EXTENSIONS: Mapping[str, str] = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
def storage_key(party_id: UUID, document_id: UUID) -> str                        # f"documents/{party_id}/{document_id}"
def upload_document(s: Session, store: ObjectStore, *, party_id: UUID, doc_type: DocumentType, content_type: str, data: bytes) -> Document
def document_ref(row: Document) -> ObjectRef                                      # ObjectRef(row.tenant_id, row.storage_key, row.sha256, row.size, row.content_type)
def download_document(s: Session, store: ObjectStore, *, party_id: UUID, document_id: UUID) -> tuple[Document, bytes]   # records VIEW_PII first
def download_filename(row: Document) -> str                                       # f"{row.id}.{EXTENSIONS[row.content_type]}"

# customer.py
OPEN_STATUSES: frozenset[CustomerRelationshipStatus] = frozenset({PENDING, ACTIVE, RESTRICTED, BLOCKED})   # C5's sweep builds its EXISTS clause from it; no has_open_relationship helper (it would have no production caller)
def open_relationship(s: Session, body: RelationshipOpenRequest) -> CustomerRelationship
def close_relationship(s: Session, relationship_id: UUID, *, ended_at: datetime | None = None) -> CustomerRelationship
def set_legal_hold(s: Session, party_id: UUID, *, legal_hold: bool) -> Party

# service.py additions
def update_party(s: Session, party_id: UUID, body: PartyUpdateRequest) -> Party
def add_relationship(s: Session, from_party_id: UUID, body: PartyRelationshipInput) -> PartyRelationship

# schemas.py additions
class PersonUpdate(BaseModel): birth_country, birth_state, nationalities, gender, occupation_code, economic_activity_code, is_resident, pep_status, pep_since, pep_until — the PersonInput types, every one optional with default None
class OrganizationUpdate(BaseModel): trade_name, legal_form, incorporation_date, industry_code, registry_ref, country_of_incorporation — optional, default None
class PartyUpdateRequest(BaseModel): person: PersonUpdate | None = None; organization: OrganizationUpdate | None = None
class PartyRelationshipInput(BaseModel): to_party_id: UUID; rel_type: RelationshipType; ownership_pct: Decimal | None = Field(default=None, gt=0, le=100, decimal_places=4); control_basis: str | None (≤200) = None; valid_from: date | None = None; valid_to: date | None = None; source_doc_id: UUID | None = None
class RelationshipOpenRequest(BaseModel): party_id: UUID; external_ref: str (1..100); product: str | None (≤32) = None; account_tier: str | None (≤32) = None; started_at: datetime | None = None
class RelationshipCloseRequest(BaseModel): ended_at: datetime | None = None
class LegalHoldRequest(BaseModel): legal_hold: bool
```

Semantics:

- `upload_document`: `get_party` (404); `len(data) == 0` → `ValidationFailedError("empty document")`; `len(data) > MAX_DOCUMENT_BYTES` → `ValidationFailedError("document too large")`; `content_type not in ALLOWED_CONTENT_TYPES` → `ValidationFailedError("unsupported document content type")`; `document_id = uuid7()`; `ref = store.put(tenant_id, storage_key(party_id, document_id), data, content_type=content_type)`; `Document(tenant_id, id=document_id, party_id, doc_type, storage_key=ref.key, sha256=ref.sha256, size=ref.size, content_type, uploaded_by=bound_context(s).actor_id)`; `s.add`; `try: s.flush() except Exception: store.delete(ref); raise`; audit `document.uploaded` (`entity_type="document"`, `detail={"party_id": ..., "doc_type": ..., "size": ref.size}`).
- `download_document`: `get_party`, `s.get(Document, (tenant_id, document_id))` with `row.party_id == party_id` else `NotFoundError`; `_ = record(s, AuditEvent(action=VIEW_PII, entity_type="document", entity_id=str(row.id), detail={"group": "document", "fields": ["content"]}))`; `return row, store.get(document_ref(row))` (an `ObjectIntegrityError` propagates as 500 `OBJECT_CORRUPT`).
- `open_relationship`: `party = get_party(s, body.party_id)`; `party.status is PartyStatus.PURGED` → `DomainError("party is purged")`; an existing `external_ref` → `DomainError("external_ref already exists")`; `started = body.started_at or clock.now()` (a naive `started_at` → `ValidationFailedError("started_at must carry a timezone")`); `_ = effective_policy(s, on=started.date(), product=body.product)` (its `POLICY_NOT_ASSIGNED` error propagates untouched and nothing is snapshotted: a tenant whose assignments do not cover `body.product` on that date fails at open with 404 `POLICY_NOT_ASSIGNED` rather than opening a relationship it could never close; `product=None` sees only tenant-wide assignments, plan 2a's D21 rule); `status=ACTIVE`, `started_at=started`; audit `customer_relationship.opened` (`entity_type="customer_relationship"`, `detail={"party_id": ...}`); publish `RELATIONSHIP_OPENED` (`entity="party"`, `entity_id=party.id`, `data={"relationship_id", "party_id"}`).
- `close_relationship`: `s.get(CustomerRelationship, (tenant_id, relationship_id), with_for_update=True)` else `NotFoundError`; `status is EXITED` → return unchanged; `ended = ended_at or clock.now()` (naive → 422 as above); `effective = effective_policy(s, on=ended.date(), product=row.product)` (its `POLICY_NOT_ASSIGNED` error propagates untouched); `snapshot_id = snapshot_policy(s, effective)`; `until = add_iso_duration(ended.date(), effective.retention.kyc_after_relationship.value)`; `party = get_party(s, row.party_id, for_update=True)`; `party.retention_until = until if party.retention_until is None else max(party.retention_until, until)`; `row.status = EXITED`, `row.ended_at = ended`; audit `customer_relationship.closed` (`detail={"party_id": ..., "retention_until": until.isoformat(), "policy_snapshot_id": str(snapshot_id)}`); publish `RELATIONSHIP_CLOSED` (`data={"relationship_id", "party_id", "retention_until": until.isoformat()}`, `policy_snapshot_id=snapshot_id`).
- `set_legal_hold`: `get_party(..., for_update=True)`; unchanged → return; else set, `updated_at = clock.now()`, audit `party.legal_hold_set` (`detail={"legal_hold": legal_hold}`); no event (the spec lists none).
- `update_party`: `get_party(..., for_update=True)`; `body.person` on an organisation (or the reverse) → `ValidationFailedError("attributes do not match the party type")`; apply `model_dump(exclude_unset=True)` of the matching update; no field changed → return without audit or event; else `party.updated_at = clock.now()`, audit `party.updated` (`detail={"fields": sorted(changed)}`), publish `PARTY_UPDATED` (`data={"party_id", "fields"}`).
- `add_relationship`: `from` through `get_party` (404); `to` must exist (`ValidationFailedError("unknown to_party_id")`); `from_party_id == to_party_id` → `ValidationFailedError("a party cannot relate to itself")`; `ownership_pct` with a `rel_type` other than `SHAREHOLDER` → `ValidationFailedError("ownership_pct applies to SHAREHOLDER only")`; `valid_from` defaults to today; audit `party.relationship_added` (`detail={"from_party_id", "to_party_id", "rel_type"}`); no event (the spec lists none for ownership edges).

`tests/modules/party/conftest.py` gains `local_store(tmp_path) -> LocalFsObjectStore(tmp_path / "objects")` and `itf_pack(tenant, admin_engine) -> UUID` (the assignment id): `version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]` (plan 2a A7's helper in `tests/platform/policy_support.py`, which installs both MX packs through an owner session: this plan loads no pack itself); then `assign_pack(s, pack_version_id=version_id, product=None, effective_from=date(2026, 1, 1), approved_by=tenant.admin_user_id)` inside `staff_session(tenant.tenant_id, tenant.admin_user_id)`.

- [ ] **Step 1: Write the failing tests** (`pytest.raises` outermost wherever an exception is expected from inside a session, as C3's preamble says).

`test_documents.py` (`local_store`, `tenant`, `admin_engine`; `PDF = b"%PDF-1.4 hello"`):

```python
def test_an_upload_stores_the_bytes_under_the_party_and_records_its_digest(engine, tenant, local_store, tmp_path):
    # row.sha256 == hashlib.sha256(PDF).digest(); row.size == len(PDF); row.storage_key == f"documents/{party.id}/{row.id}"
    # (tmp_path / "objects" / str(tenant.tenant_id) / row.storage_key).read_bytes() == PDF
    # audit_events(...)[-1]: action "document.uploaded", detail {"party_id": str(party.id), "doc_type": "IDENTITY", "size": len(PDF)}
def test_refusals_leave_no_file_and_no_row(engine, tenant, local_store, monkeypatch, tmp_path):
    # monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 16); PDF * 2 -> ValidationFailedError("document too large")
    # content_type "text/plain" -> "unsupported document content type"; b"" -> "empty document"
    # no file under tmp_path / "objects" / str(tenant_id); select(Document) is empty
def test_a_flush_failure_deletes_the_stored_object(engine, tenant, local_store, monkeypatch, tmp_path):
    # the party is created in the same session, so get_party hits the identity map and no autoflush runs the patched flush before the put
    puts: list[ObjectRef] = []
    real_put = local_store.put
    def recording_put(*args, **kwargs) -> ObjectRef:
        ref = real_put(*args, **kwargs); puts.append(ref); return ref
    monkeypatch.setattr(local_store, "put", recording_put)
    with pytest.raises(RuntimeError, match="flush refused"), staff_session(...) as s:
        party = service.create_party(s, ANA)
        real_flush = s.flush
        def refuse_after_the_put(*args, **kwargs) -> None:
            if puts:
                raise RuntimeError("flush refused")
            real_flush(*args, **kwargs)
        monkeypatch.setattr(s, "flush", refuse_after_the_put)
        _ = documents.upload_document(s, local_store, party_id=party.id, doc_type=DocumentType.IDENTITY, content_type="application/pdf", data=PDF)
    assert len(puts) == 1                                                                   # the object existed before the failure
    assert not (tmp_path / "objects" / str(tenant.tenant_id) / puts[0].key).exists()       # and the best-effort delete removed it
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalars(select(Document)).all() == []
def test_the_download_returns_the_bytes_and_records_view_pii_on_the_document(engine, tenant, local_store):
    # (row, data) == (..., PDF); the VIEW_PII row: entity_type "document", entity_id str(row.id), detail {"group": "document", "fields": ["content"]}; download_filename(row) == f"{row.id}.pdf"
def test_a_tampered_object_is_refused(engine, tenant, local_store, tmp_path):
    # overwrite the file with b"other"; download_document -> ObjectIntegrityError (pytest.raises outermost)
def test_a_document_of_another_party_is_not_found(engine, tenant, local_store):
```

`test_customer.py` (`tenant`; `itf_pack` wherever a relationship is opened through `open_relationship` or retention is computed, since the open resolves the effective policy; `T_END = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)`):

```python
def test_retention_arithmetic_is_plan_2as_add_iso_duration():            # no database: the contract close_relationship relies on, pinned once here
    assert add_iso_duration(date(2027, 3, 1), "P10Y") == date(2037, 3, 1)
    assert add_iso_duration(date(2026, 1, 31), "P1M") == date(2026, 2, 28)
def test_opening_a_relationship_audits_and_publishes_ids_only(engine, tenant, itf_pack):
    # status ACTIVE, started_at set, audit "customer_relationship.opened" with detail {"party_id": ...}, event party.relationship_opened.v1 data == {"relationship_id": ..., "party_id": ...}; assert "ref-1" not in payloads_of(tenant.tenant_id)
def test_external_ref_is_unique_and_a_purged_party_cannot_open(engine, tenant, itf_pack):
    # second open with "ref-1" -> DomainError("external_ref already exists"); party.status = PURGED (set directly) -> DomainError("party is purged")
def test_opening_without_a_pack_assignment_covering_the_product_fails(engine, tenant, admin_engine):
    # no itf_pack. Nothing assigned: `with pytest.raises(NotFoundError) as refused, staff_session(...) as s: _ = open_relationship(s, RelationshipOpenRequest(party_id=ana.id, external_ref="ref-1"))`,
    # refused.value.code == "POLICY_NOT_ASSIGNED". Then the ITF pack assigned to one product only: `version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]`, then in a staff session
    # `assign_pack(s, pack_version_id=version_id, product="EMONEY", effective_from=date(2026, 1, 1), approved_by=tenant.admin_user_id)`; opening with product=None -> the same
    # POLICY_NOT_ASSIGNED (plan 2a's D21: no product sees only tenant-wide assignments); with product="EMONEY" and the same "ref-1" -> ACTIVE (the refused opens left no row)
    # afterwards exactly one customer_relationship row and one "customer_relationship.opened" audit row
def test_closing_computes_retention_from_the_effective_policy_snapshot(engine, tenant, itf_pack):
    # close_relationship(rel.id, ended_at=T_END): status EXITED, ended_at == T_END; party.retention_until == date(2037, 3, 1)
    # (closed,) = of_type(..., "party.relationship_closed.v1"): closed.policy_snapshot_id is a UUID present in policy_snapshot (select by (tenant_id, id)); closed.data == {"relationship_id": ..., "party_id": ..., "retention_until": "2037-03-01"}
    # audit detail == {"party_id": ..., "retention_until": "2037-03-01", "policy_snapshot_id": str(closed.policy_snapshot_id)}
def test_closing_twice_is_a_no_op_and_an_earlier_closure_never_moves_retention_back(engine, tenant, itf_pack):
    # close again -> same row, audit count unchanged; a second relationship closed at T_END - 1 year leaves retention_until == date(2037, 3, 1)
def test_closing_without_a_pack_assignment_fails(engine, tenant):
    # the row is seeded directly with the C2 model, `s.add(CustomerRelationship(tenant_id=tenant.tenant_id, party_id=ana.id, external_ref="ref-1", status=CustomerRelationshipStatus.ACTIVE))`, in one session
    # (open_relationship would itself refuse with POLICY_NOT_ASSIGNED, pinned above); then `with pytest.raises(NotFoundError) as refused, staff_session(...) as s: _ = close_relationship(s, rel.id, ended_at=T_END)`
    # refused.value.code == "POLICY_NOT_ASSIGNED" (plan 2a's error, so C7's close route answers 404 POLICY_NOT_ASSIGNED); in a new session the relationship is still ACTIVE and party.retention_until is None
def test_legal_hold_toggles_once_per_change(engine, tenant):
    # set True -> legal_hold True, audit {"legal_hold": True}; set True again -> no new audit; set False -> audit {"legal_hold": False}
```

`test_service.py` (append; the two tests C3 leaves to this task):

```python
def test_relationships_validate_the_pair_and_ownership(engine, tenant):
    # ana, acme; add_relationship(ana.id, PartyRelationshipInput(to_party_id=acme.id, rel_type=SHAREHOLDER, ownership_pct=Decimal("51.5"))) ok and visible in staff_view(acme.id).relationships;
    # each refusal in its own session with pytest.raises outermost: DIRECTOR with ownership_pct -> ValidationFailedError; to_party_id=ana.id from ana -> ValidationFailedError; to_party_id=uuid7() -> ValidationFailedError;
    # audit "party.relationship_added" with detail {"from_party_id": str(ana.id), "to_party_id": str(acme.id), "rel_type": "SHAREHOLDER"}

def test_update_changes_only_attributes_and_audits_the_field_names(engine, tenant):
    # update_party(ana.id, PartyUpdateRequest(person=PersonUpdate(pep_status=DOMESTIC, is_resident=True))): audit detail {"fields": ["is_resident", "pep_status"]}, event party.updated.v1 data {"party_id", "fields": [...]};
    # the same update again -> no new audit row; PartyUpdateRequest(organization=OrganizationUpdate(trade_name="x")) on ANA -> ValidationFailedError
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_documents.py tests/modules/party/test_customer.py tests/modules/party/test_service.py --no-cov` → `ImportError` (`xolotl.modules.party.documents`, `customer`; `PartyUpdateRequest`, `update_party`).
- [ ] **Step 3: Implement** the two modules, the schema additions and the two service functions.
- [ ] **Step 4: Run to verify pass.** The three files, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): documents, customer relationships with policy retention, legal hold, party updates and relationship edges`.

---

### Task C5: Retention sweep job, schedule and `party purge-due`

Depends on C4, and on plan 2a's A5 and plan 2b's B3b being merged: A5 adds the `units.fetch` call to `schedule_platform_jobs` and the `units.fetch` rows to the pinned schedule tests this task extends (A5 runs in parallel with A7, which C4 needs, so it can still be open when C4 is done); B3b hoists `store = build_object_store(settings)` in `wire_platform`, the variable `register_party` reuses. Touches `src/xolotl/app.py` (`wire_platform`) and the pinned schedule tests.

**Files:**
- Create: `src/xolotl/modules/party/retention.py`
- Modify: `src/xolotl/app.py` (`register_party(object_store=store)` in `wire_platform`), `src/xolotl/cli.py` (`party_app`, `purge-due`), `src/xolotl/modules/tenants/service.py` (`RETENTION_SWEEP_SCHEDULE`, one more `queue.schedule` call), `tests/platform/test_worker.py`, `tests/modules/tenants/test_kernel_events.py`, `tests/test_cli.py` (append)
- Test: `tests/modules/party/test_retention.py`

**Interfaces:**

Consumes: `register_job_handler`, `job_handler`, `Job`, `PostgresJobQueue`, `JobRunner`, `JobStatus` (`xolotl.platform.jobs`; tests build the runner directly, `JobRunner(queue, get_sessionmaker(), worker_id="w-1", kinds=[RETENTION_SWEEP_KIND])`, as `tests/platform/test_idempotency_purge.py` does: `make_runner` is a local helper of `tests/platform/test_jobs.py`, not of `jobs_support.py`); `tests/platform/jobs_support.py` (`enqueue`, `fetch_job`, `fetch_schedule`); `next_slot` (`xolotl.platform.jobs.queue`); `build_worker`, `wire_platform` (`xolotl.app`; plan 2b's B3b already builds the store once there, `store = build_object_store(settings)`, for the connector router); `schedule_platform_jobs` (`xolotl.modules.tenants.service`; plan 2a's A5 keeps its `for kind in TENANT_JOBS` loop and appends one explicit `queue.schedule(..., name=UNITS_FETCH_KIND, cron=get_settings().units_fetch_cron, tz=UNITS_FETCH_TZ, ...)` call: there is no settings-driven list of pairs to join); CLI helpers `_cli_actor`, `_cli_errors`, `_resolve_tenant`, `_ensure_key_provider`, `tenant_session(TenantContext.platform(actor, tenant_id=...))`; C4's `OPEN_STATUSES` (`customer.py`) and `document_ref`; C3's `publish_party_event`, `PARTY_PURGED`.

Produces:

```python
# retention.py
RETENTION_SWEEP_KIND = "party.retention_sweep"
PURGE_BATCH = 100

@dataclass(frozen=True, slots=True)
class PurgeResult:
    party_id: UUID
    documents: tuple[ObjectRef, ...]

def due_parties(s: Session, *, today: date, limit: int = PURGE_BATCH) -> list[Party]
    # select(Party).where(Party.status == ACTIVE, Party.retention_until < today, Party.legal_hold.is_(False),
    #   ~exists(select(CustomerRelationship.id).where(CustomerRelationship.party_id == Party.id, CustomerRelationship.status.in_(OPEN_STATUSES))))
    #   .order_by(Party.id).limit(limit).with_for_update(skip_locked=True)
def purge_party(s: Session, party: Party) -> PurgeResult
    # person: given_names, first_surname, second_surname, birth_date, clave_elector = None; organization: legal_name = None;
    # delete party_identifier, party_contact, party_address, party_relationship (from or to) and document rows of the party (collecting document_ref of each);
    # party.status = PURGED, updated_at = clock.now(); record "party.purged" (detail {"documents": n, "identifiers": n}); publish PARTY_PURGED (data {"party_id"})

class RetentionSweeper:
    def __init__(self, object_store: ObjectStore) -> None
    def sweep(self, session: Session, *, today: date, dry_run: bool = False) -> int
        # dry_run: return the count of the same predicate (select(func.count()) over due_parties' where clause, no lock, no limit) and change nothing;
        # else loop: batch = due_parties(...); purge each; session.commit(); delete every collected ObjectRef (a failure is logged at WARNING with the key, never raised); stop when the batch is shorter than PURGE_BATCH; return the count
    def handle(self, session: Session, _job: Job) -> None       # self.sweep(session, today=clock.now().date())

def register_party(*, object_store: ObjectStore) -> None
    # register_job_handler(RETENTION_SWEEP_KIND, RetentionSweeper(object_store).handle)
```

`wire_platform` (`src/xolotl/app.py`): plan 2b's B3b already hoisted `store = build_object_store(settings)` above `PlatformServices(...)` and passes `object_store=store`; add `register_party(object_store=store)` right after that `store = build_object_store(settings)` line, reusing the variable (not next to `register_job_handler(PURGE_KIND, purge_expired_keys)`, which precedes the hoisted line, where `store` does not exist yet), and build no second store.

Schedule (`src/xolotl/modules/tenants/service.py`): a module constant `RETENTION_SWEEP_SCHEDULE = "party.retention_sweep"` (a literal: tenants never imports party) and, in `schedule_platform_jobs` after plan 2a's `units.fetch` call, a second explicit call `_ = queue.schedule(s, name=RETENTION_SWEEP_SCHEDULE, cron=get_settings().retention_purge_cron, tz="UTC", kind=RETENTION_SWEEP_SCHEDULE, payload={})`.

CLI (`src/xolotl/cli.py`): `party_app = typer.Typer(no_args_is_help=True, help="Party retention")`, `app.add_typer(party_app, name="party")`;

```python
@party_app.command("purge-due")
def party_purge_due(*, tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
                    dry_run: Annotated[bool, typer.Option("--dry-run", help="Count the parties due for purge without changing anything")] = False) -> None:
    """Purge the tenant's parties whose retention period has ended (no legal hold, no open relationship)."""
    # actor = _cli_actor(); with _cli_errors(): tenant_id, slug = _resolve_tenant(actor, tenant); _ensure_key_provider();
    # store = build_object_store(get_settings()); with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
    #   count = RetentionSweeper(store).sweep(s, today=clock.now().date(), dry_run=dry_run)
    # typer.echo(f"{count} parties of tenant {slug} due for purge" if dry_run else f"purged {count} parties of tenant {slug}")
```

Pinned tests to update (map-api trap T5), inserting the row `("party.retention_sweep", "0 3 * * *", "UTC", "party.retention_sweep", {}, True)` in name order (`idempotency.purge`, `party.retention_sweep`, `units.fetch`, `workflow.sweep`):

- `tests/modules/tenants/test_kernel_events.py::test_creating_a_tenant_schedules_the_platforms_hourly_jobs`: the expected list gains the row; the `slots` assertion holds for hourly rows only, so exclude the `party.retention_sweep` row from the `{row.next_run_at for row in schedules} <= slots` comparison the way plan 2a excludes `units.fetch`, and add `sweep = next(row for row in schedules if row.name == "party.retention_sweep")` with `assert sweep.next_run_at in {next_slot("0 3 * * *", "UTC", after=m) for m in (before, after)}` (a daily 03:00 UTC slot is never the next full hour).
- `::test_every_tenant_can_be_given_the_platforms_schedules_again`: `names == ["idempotency.purge", "party.retention_sweep", "units.fetch", "workflow.sweep"]`.
- `tests/platform/test_worker.py::PLATFORM_SCHEDULES`: the five-tuple row `("party.retention_sweep", "0 3 * * *", "UTC", "party.retention_sweep", True)` in name order; `::test_a_new_tenants_hourly_jobs_run_through_the_worker`: `jobs` gains `"party.retention_sweep": JobStatus.DONE` (the sweep on an empty tenant is `DONE` with nothing purged).

- [ ] **Step 1: Write the failing tests.**

`tests/modules/party/test_retention.py` (`T0 = datetime(2036, 10, 20, 3, 0, tzinfo=UTC)` patched into `clock.now`; a helper `_customer(s, body, *, retention_until, legal_hold=False) -> Party` creates the party, adds an `EXITED` `customer_relationship` row directly with the C2 model (not through `open_relationship`, which would answer `POLICY_NOT_ASSIGNED`: this module assigns no pack) and sets `retention_until`; ACME's second `ACTIVE` relationship is added the same way):

```python
def test_the_sweep_purges_due_parties_and_nothing_else(engine, tenant, local_store, tmp_path, monkeypatch, admin_engine):
    # due: ANA with retention_until 2036-10-19 and one uploaded document; held: CARLOS retention 2036-10-19 + legal_hold; open: ACME retention 2036-10-19 + a second ACTIVE relationship;
    # today_party: retention_until == 2036-10-20; never: a party with retention_until None
    # sweep(...) == 1; ANA: status PURGED, person.given_names is None, no identifier/contact/address/document rows, file gone from tmp_path;
    # parties_query(id_type=RFC, value="PEGA760510XY0") -> []; the others untouched (status ACTIVE, rows intact)
    # audit: exactly one "party.purged" with detail {"documents": 1, "identifiers": 2}; (purged,) = of_type(..., "party.purged.v1"); purged.data == {"party_id": str(ana.id)}
    # leaked(everything_stored(admin_engine), b"Ana Mar\xc3\xada") == []
def test_a_second_run_changes_nothing(...)            # sweep again -> 0; audit "party.purged" count still 1
def test_a_dry_run_counts_without_purging(...)         # sweep(dry_run=True) == 1; ANA still ACTIVE; no audit row
def test_the_job_runs_through_the_registered_handler(engine, tenant, local_store, monkeypatch):
    # register_party(object_store=local_store); queue = PostgresJobQueue(get_sessionmaker()); job_id = enqueue(queue, tenant.tenant_id, kind=RETENTION_SWEEP_KIND)
    # assert JobRunner(queue, get_sessionmaker(), worker_id="w-1", kinds=[RETENTION_SWEEP_KIND]).run_once() == 1
    # fetch_job(tenant.tenant_id, job_id).status is JobStatus.DONE; ANA purged
def test_the_platform_registers_the_handler_and_schedules_the_sweep(engine):
    # wire_platform(get_sessionmaker(), get_settings()); job_handler(RETENTION_SWEEP_KIND) raises no KeyError
    # legacy = make_tenant("legacy"); build_worker().run(max_ticks=0); fetch_schedule(legacy, "party.retention_sweep") has cron "0 3 * * *", tz "UTC", kind "party.retention_sweep", enabled
def test_the_schedule_names_the_handlers_kind():       # from xolotl.modules.tenants import service as tenants_service; assert tenants_service.RETENTION_SWEEP_SCHEDULE == RETENTION_SWEEP_KIND
```

`tests/test_cli.py` (append, before the reset test; import `new_tenant` and `staff_session` from `tests.helpers`; this file does not patch `clock.now`, so the due date lies in the real past):

- Setup: `store = LocalFsObjectStore(tmp_path / "objects")`; `created = new_tenant()` (slug `acme`; `create_tenant` gives it its keys); a `PartyCreateRequest` built in the test (`PERSON`, `given_names="Ana"`, `first_surname="Pérez"`, no identifiers; the party conftest is not imported here); `with staff_session(created.tenant_id, created.admin_user_id) as s: party = service.create_party(s, request); _ = documents.upload_document(s, store, party_id=party.id, doc_type=DocumentType.IDENTITY, content_type="application/pdf", data=b"%PDF-1.4 cli"); party.retention_until = date(2020, 1, 1)` (set directly, with no customer relationship: due under the real clock); `monkeypatch.setattr("xolotl.cli.get_settings", lambda: get_settings().model_copy(update={"object_store_root": tmp_path / "objects"}))` (the idiom of `test_serve_says_whether_it_can_write_the_object_store`).
- `runner.invoke(app, ["party", "purge-due", "--tenant", "acme", "--dry-run"])` → exit 0, `result.stdout == "1 parties of tenant acme due for purge\n"`, the party still `ACTIVE`; without `--dry-run` → exit 0, `"purged 1 parties of tenant acme\n"`, the party `PURGED` and the file gone from `tmp_path / "objects"`; `--tenant nope` → exit 1, `result.stdout == ""` and `result.stderr == "error: NOT_FOUND: no tenant 'nope'\n"` (`_cli_errors` echoes with `err=True`, so the line is asserted on `result.stderr` as `test_keys_rotate_rejects_what_it_cannot_act_on` does); `party purge-due --help` works without a database (exit 0, the text names `--dry-run`).

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_retention.py tests/test_cli.py -k "purge or sweep or schedule" --no-cov` → `ImportError`/`KeyError`; the three pinned schedule tests still pass (nothing declared yet).
- [ ] **Step 3: Implement** the sweep, the registration, the schedule, the CLI, and update the pinned tests in the same commit.
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/modules/party/test_retention.py tests/test_cli.py tests/platform/test_worker.py tests/modules/tenants/test_kernel_events.py --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): retention sweep job, schedule and party purge-due`.

---

### Task C6: Party routes: staff and machine views, identifier search, sub-resources

Depends on C3 only (C4 and C6 can run in parallel). Touches `src/xolotl/app.py` (the router list).

**Files:**
- Create: `src/xolotl/modules/party/routes.py`
- Modify: `src/xolotl/app.py` (`from xolotl.modules.party import routes as party_routes`; `party_routes.router` after `routes_webhooks.router`, before `audit_routes.router`)
- Test: `tests/modules/party/route_support.py` (header helpers; C7 reuses them), `tests/modules/party/test_routes.py`

**Interfaces:**

Consumes: `v1_router`, `require_access`, `SessionDep`, `Actor`, `Role`, `Page`, `PageDep`, `paginate` (as `routes_staff.py` uses them); C3's service, schemas and errors; C2's `CustomerRelationship` and `CustomerRelationshipStatus` (the `external_ref` filter test seeds a row directly: C4 is not a dependency); test helpers `first_login`, `staff_session`, `service.create_staff` / `service.create_api_key` of `xolotl.modules.tenants.service` (keys with `scopes=["parties:read"]` / `["parties:write"]`), `audit_events`, `everything_stored`, `leaked`, `payloads_of`.

Produces (`routes.py`; `router = v1_router(prefix="/parties", tags=["parties"])`):

```python
PartyReader = Annotated[Actor, Depends(require_access(scope="parties:read"))]                        # staff of any role, or a key with parties:read
PartyWriter = Annotated[Actor, Depends(require_access(scope="parties:write", write=True))]
PartyStaff = Annotated[Actor, Depends(require_access())]                                            # staff only: every API client is refused (ROLE_FORBIDDEN "staff only")

@router.post("", status_code=201)   def create_party(body: PartyCreateRequest, _writer: PartyWriter, session: SessionDep) -> PartyResponse
@router.get("")                     def list_parties(_reader: PartyReader, session: SessionDep, page: PageDep, status: PartyStatus | None = None,
                                        external_ref: Annotated[str | None, Query(max_length=100)] = None, id_type: IdentifierType | None = None,
                                        value: Annotated[str | None, Query(max_length=64)] = None) -> Page[PartyResponse]
@router.get("/{party_id}")          def get_party(party_id: UUID, actor: PartyReader, session: SessionDep) -> PartyStaffResponse | PartyResponse
                                        # actor.is_staff -> service.staff_view(...); else service.summary(session, service.get_party(session, party_id))
@router.get("/{party_id}/pii")      def reveal_party_pii(party_id: UUID, _staff: PartyStaff, session: SessionDep) -> PartyPiiResponse
@router.post("/{party_id}/identifiers", status_code=201)   def add_party_identifier(party_id: UUID, body: IdentifierInput, _writer: PartyWriter, session: SessionDep) -> MaskedIdentifier
@router.post("/{party_id}/contacts", status_code=201)      def add_party_contact(party_id: UUID, body: ContactInput, _writer: PartyWriter, session: SessionDep) -> MaskedContact
@router.post("/{party_id}/addresses", status_code=201)     def add_party_address(party_id: UUID, body: AddressInput, _writer: PartyWriter, session: SessionDep) -> AddressResponse
# PATCH /{party_id} and POST /{party_id}/relationships join this router in C7 (their service functions are C4's)
```

`list_parties` builds `service.parties_query(session, status=..., external_ref=..., id_type=..., value=...)` and paginates with `ts_col=Party.created_at, id_col=Party.id, key=lambda row: (row.created_at, row.id)`, returning `Page(items=service.summaries(session, rows), next_cursor=next_cursor)`. Routes contain no business rule: `create_party` returns `service.summary(session, service.create_party(session, body))`; the sub-resource POSTs return `service.masked_identifier(session, row)`, `service.masked_contact(session, row)`, `AddressResponse.model_validate(row)`.

`tests/modules/party/route_support.py`:

```python
ANALYST_PASSWORD = "an-analyst-password-of-length"
def staff_headers(client, tenant, *, email: str, roles: tuple[Role, ...]) -> dict[str, str]   # create_staff in staff_session(tenant.tenant_id, tenant.admin_user_id), then first_login
def analyst_headers(client, tenant) -> dict[str, str]                                          # roles=(Role.ANALYST,), email "ana.analyst@acme.mx"
def mlro_headers(client, tenant) -> dict[str, str]
def machine_headers(tenant, *scopes: str) -> dict[str, str]                                    # create_api_key(s, name="ingest", scopes=list(scopes)) in staff_session; {"Authorization": f"Bearer {issued.key}"}
def post(client, headers, path, body, key) -> Response                                        # client.post(path, json=body, headers={**headers, "Idempotency-Key": key})
ANA_BODY, CARLOS_BODY, ACME_BODY = ANA.model_dump(mode="json"), ...
```

- [ ] **Step 1: Write the failing tests** (`tests/modules/party/test_routes.py`; `client`, `tenant`, `admin_headers` fixtures):

```python
def test_staff_creates_a_person_and_finds_it_by_rfc_without_decryption(client, tenant, admin_headers):
    created = post(client, admin_headers, "/v1/parties", ANA_BODY, "p1")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["display_name"] == "A.M.P.G."
    assert {i["id_type"]: i["masked_value"] for i in body["identifiers"]} == {"RFC": "****0XY0", "CURP": "****MN03"}
    assert "PEGA760510XY0" not in created.text
    found = client.get("/v1/parties", params={"id_type": "RFC", "value": " pega-760510-xy0 "}, headers=admin_headers)
    assert [p["id"] for p in found.json()["items"]] == [body["id"]]
    assert client.get("/v1/parties", params={"id_type": "RFC", "value": "EKU9003173C9"}, headers=admin_headers).json()["items"] == []
    assert client.get("/v1/parties", params={"id_type": "RFC"}, headers=admin_headers).status_code == 422
    bad = client.get("/v1/parties", params={"id_type": "RFC", "value": "EKU9003173C1"}, headers=admin_headers)
    assert (bad.status_code, bad.json()["code"], bad.json()["detail"]) == (422, "IDENTIFIER_INVALID", "RFC: CHECK_DIGIT")
    assert [e.action for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"] == []

def test_a_duplicate_identifier_is_a_409_with_the_party_id_for_staff_only(client, tenant, admin_headers):
    first = post(client, admin_headers, "/v1/parties", ANA_BODY, "p1").json()["id"]
    again = post(client, admin_headers, "/v1/parties", ANA_BODY, "p2")
    assert (again.status_code, again.json()["code"], again.json()["detail"]) == (409, "IDENTIFIER_EXISTS", first)
    machine = machine_headers(tenant, "parties:write")
    refused = post(client, machine, "/v1/parties", ANA_BODY, "m1")
    assert (refused.status_code, refused.json()["code"]) == (409, "IDENTIFIER_EXISTS")
    assert refused.json()["detail"] == "a party with this identifier already exists"

def test_the_detail_is_masked_for_everyone_and_the_reveal_audits_view_pii(client, tenant, admin_headers):
    party_id = post(...ANA_BODY...).json()["id"]
    analyst = analyst_headers(client, tenant)
    staff = client.get(f"/v1/parties/{party_id}", headers=analyst).json()
    assert staff["person"]["pep_status"] == "NONE" and staff["contacts"] == [] and "Ana" not in json.dumps(staff)
    machine = machine_headers(tenant, "parties:read")
    seen = client.get(f"/v1/parties/{party_id}", headers=machine).json()
    assert "person" not in seen and "contacts" not in seen and seen["display_name"] == "A.M.P.G."
    assert client.get(f"/v1/parties/{party_id}/pii", headers=machine).json()["code"] == "ROLE_FORBIDDEN"
    assert [e for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"] == []
    pii = client.get(f"/v1/parties/{party_id}/pii", headers=analyst)
    assert pii.status_code == 200 and pii.json()["given_names"] == "Ana María"
    assert {i["value"] for i in pii.json()["identifiers"]} == {"PEGA760510XY0", "PEGA760510MDFRMN03"}
    views = [e for e in audit_events(tenant.tenant_id) if e.action == "VIEW_PII"]
    assert [(e.entity_type, e.entity_id, e.detail["group"]) for e in views] == [("party", party_id, "profile"), ("party", party_id, "identifiers")]
    assert views[0].actor_type is ActorType.STAFF and views[0].actor_id != str(tenant.admin_user_id)
    listed = client.get("/v1/audit", params={"action": "VIEW_PII"}, headers=admin_headers).json()["items"]
    assert len(listed) == 2
    assert client.get("/v1/audit/verify", headers=admin_headers).json()["valid"] is True

def test_scopes_gate_machines(client, tenant):
    reader, writer = machine_headers(tenant, "parties:read"), machine_headers(tenant, "parties:write")
    assert client.get("/v1/parties", headers=reader).status_code == 200
    refused = post(client, reader, "/v1/parties", ACME_BODY, "m1")
    assert (refused.status_code, refused.json()["code"]) == (403, "SCOPE_MISSING")
    assert post(client, writer, "/v1/parties", ACME_BODY, "m2").status_code == 201
    assert client.get("/v1/parties", headers=writer).status_code == 403     # parties:write does not read

def test_filters_and_cursor_pagination(client, tenant, admin_headers):
    # three parties (ANA, CARLOS, ACME); limit=2 -> 2 items and a next_cursor; the cursor page -> the remaining one; status=ACTIVE -> 3; status=PURGED -> 0;
    # the external_ref filter is seeded with the C2 model directly (C4's open_relationship is not merged when this task runs):
    #   with staff_session(...) as s: s.add(CustomerRelationship(tenant_id=tenant.tenant_id, party_id=acme, external_ref="ref-1", status=CustomerRelationshipStatus.ACTIVE)); s.flush()
    # external_ref=ref-1 -> [acme]; external_ref=ref-9 -> []

def test_sub_resources_are_added_and_shown_masked(client, tenant, admin_headers):
    # POST identifiers {"id_type": "CLABE", "value": "002010077777777771"} -> 201 masked "****7771"; a second time -> 409 IDENTIFIER_EXISTS
    # POST contacts {"kind": "EMAIL", "value": "Ana@Acme.MX"} -> 201 masked "****e.mx"; POST addresses {...} -> 201 with postal_code
    # GET detail: 3 identifiers, 1 contact, 1 address, relationships == []; machine detail still has no contacts key

def test_another_tenant_sees_nothing(client, tenant, admin_headers):
    # party created in acme; other = new_tenant("beta", "admin@beta.mx"); beta_headers = first_login(client, "beta", other.admin_email, other.admin_password, ADMIN_PASSWORD)
    # GET /v1/parties/{id} -> 404; GET /v1/parties?id_type=RFC&value=PEGA760510XY0 -> []; GET /v1/parties -> []

def test_every_post_needs_an_idempotency_key_and_replays(client, tenant, admin_headers):
    # POST without the header -> 400 IDEMPOTENCY_KEY_REQUIRED; the same key twice -> second has Idempotent-Replay: true and one party exists

def test_no_plaintext_is_stored_or_logged(client, tenant, admin_headers, admin_engine, caplog):
    caplog.set_level(logging.DEBUG); caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    given = f"Zaira{uuid4().hex[:6].title()}"
    body = {**ANA_BODY, "person": {**ANA_BODY["person"], "given_names": given}}
    created = post(client, admin_headers, "/v1/parties", body, "p1"); assert created.status_code == 201
    party_id = created.json()["id"]
    _ = client.get(f"/v1/parties/{party_id}", headers=admin_headers)
    _ = client.get(f"/v1/parties/{party_id}", headers=machine_headers(tenant, "parties:read"))
    _ = client.get(f"/v1/parties/{party_id}/pii", headers=admin_headers)
    assert "Row (" in caplog.text
    for secret in (given, "Pérez", "PEGA760510XY0", "PEGA760510MDFRMN03"):
        assert leaked(caplog.text, secret.encode()) == []
        assert leaked(everything_stored(admin_engine), secret.encode()) == []
        assert secret not in payloads_of(tenant.tenant_id)
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_routes.py --no-cov` → 404 `NOT_FOUND` on every call (router not mounted); `tests/platform/test_principal.py::test_every_v1_route_declares_an_access_rule` and `tests/platform/test_api_conventions.py` must stay green once mounted.
- [ ] **Step 3: Implement** `routes.py` and the registration in `app.py`.
- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/modules/party/test_routes.py tests/platform/test_principal.py tests/platform/test_api_conventions.py --no-cov`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): party routes with staff and machine views`.

---

### Task C7: Document, legal-hold, customer-relationship, update and party-relationship routes

Depends on C4 and C6. Touches `src/xolotl/app.py` (two more routers) and C6's `routes.py` (the two handlers whose service functions C4 added).

**Files:**
- Create: `src/xolotl/modules/party/routes_documents.py`, `src/xolotl/modules/party/routes_relationships.py`
- Modify: `src/xolotl/modules/party/routes.py` (`update_party`, `add_party_relationship`), `src/xolotl/app.py` (`from xolotl.modules.party import routes_documents as party_document_routes, routes_relationships as party_relationship_routes`; both routers after `party_routes.router`), `tests/modules/party/conftest.py` (`object_store` fixture), `tests/modules/party/test_routes.py` (append two tests)
- Test: `tests/modules/party/test_document_routes.py`, `tests/modules/party/test_relationship_routes.py`

**Interfaces:**

Consumes: `app_state(request).object_store` (`xolotl.platform.api.app`): `AppState.object_store` is the route's source of truth; `build_app` fills it with the same store it puts on `PlatformServices` (`test_worker.py` pins `state.object_store is state.services.object_store`), but `PlatformServices` is a frozen dataclass the worker's sweep reads, not what a route reads, so a test swaps the `AppState` field and the route sees it. Also `fastapi.Request`, `fastapi.responses.Response`, `starlette.concurrency.run_in_threadpool`; C4's `upload_document`, `download_document`, `download_filename`, `open_relationship`, `close_relationship`, `set_legal_hold`, `update_party`, `add_relationship`, `PartyUpdateRequest`, `PartyRelationshipInput`; C3's `document_response`, `PartyRelationshipResponse`, `summary`; C6's access aliases (import `PartyReader`, `PartyWriter`, `PartyStaff` from `routes.py`) and `route_support.py`.

Produces:

```python
# routes.py additions (C6's router)
@router.patch("/{party_id}")        def update_party(party_id: UUID, body: PartyUpdateRequest, _writer: PartyWriter, session: SessionDep) -> PartyResponse
                                        # service.summary(session, service.update_party(session, party_id, body)); no Idempotency-Key (PATCH)
@router.post("/{party_id}/relationships", status_code=201) def add_party_relationship(party_id: UUID, body: PartyRelationshipInput, _writer: PartyWriter, session: SessionDep) -> PartyRelationshipResponse
                                        # PartyRelationshipResponse.model_validate(service.add_relationship(session, party_id, body))

# routes_documents.py: router = v1_router(prefix="/parties", tags=["documents"])
LegalHoldWriter = Annotated[Actor, Depends(require_access(roles=(Role.MLRO,), write=True))]
def object_store(request: Request) -> ObjectStore:
    # store = app_state(request).object_store; if store is None: raise RuntimeError("the application was built without an object store")   (the app_services idiom; the guard is what basedpyright needs)
    # return store
StoreDep = Annotated[ObjectStore, Depends(object_store)]

@router.post("/{party_id}/documents", status_code=201,
             openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload_party_document(party_id: UUID, request: Request, doc_type: Annotated[DocumentType, Query()], _writer: PartyWriter, session: SessionDep, store: StoreDep) -> DocumentResponse
    # data = await request.body(); content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    # row = await run_in_threadpool(upload_document, session, store, party_id=party_id, doc_type=doc_type, content_type=content_type, data=data); return document_response(row)
@router.get("/{party_id}/documents/{document_id}/content", responses={200: {"content": {"application/octet-stream": {}}}})
def download_party_document(party_id: UUID, document_id: UUID, _staff: PartyStaff, session: SessionDep, store: StoreDep) -> Response
    # row, data = download_document(...); Response(content=data, media_type=row.content_type, headers={"Content-Disposition": f'attachment; filename="{download_filename(row)}"'})
@router.post("/{party_id}/legal-hold")
def set_party_legal_hold(party_id: UUID, body: LegalHoldRequest, _mlro: LegalHoldWriter, session: SessionDep) -> PartyResponse

# routes_relationships.py: router = v1_router(prefix="/relationships", tags=["relationships"])
@router.post("", status_code=201)
def open_customer_relationship(body: RelationshipOpenRequest, _writer: PartyWriter, session: SessionDep) -> CustomerRelationshipResponse
    # a tenant whose assignments do not cover body.product on the start date answers 404 POLICY_NOT_ASSIGNED (plan 2a's NotFoundError, not caught here)
@router.post("/{relationship_id}/close")
def close_customer_relationship(relationship_id: UUID, body: RelationshipCloseRequest, _writer: PartyWriter, session: SessionDep) -> CustomerRelationshipResponse
    # a tenant with no pack assignment covering ended_at answers 404 POLICY_NOT_ASSIGNED (plan 2a's NotFoundError, not caught here)
```

`tests/modules/party/conftest.py` gains the fixture `object_store(app, tmp_path) -> LocalFsObjectStore`: `store = LocalFsObjectStore(tmp_path / "objects")`; `app.state.xolotl.object_store = store`; return it. It depends on the `app` fixture that `client` shares, so the swap precedes the first request whatever the order of a test's arguments; `services.object_store` is left as built (no route reads it).

- [ ] **Step 1: Write the failing tests.**

`test_document_routes.py` (`object_store`, `client`, `tenant`, `admin_headers`; `PDF = b"%PDF-1.4 smoke"`; upload = `client.post(f"/v1/parties/{party_id}/documents", params={"doc_type": "IDENTITY"}, content=PDF, headers={**headers, "Content-Type": "application/pdf", "Idempotency-Key": key})`):

```python
def test_an_upload_is_stored_with_its_digest_and_listed_in_the_staff_detail(...):
    # 201; body["sha256"] == hashlib.sha256(PDF).hexdigest(); body["size"] == len(PDF); body["content_type"] == "application/pdf"
    # file at tmp_path / "objects" / str(tenant_id) / f"documents/{party_id}/{body['id']}"; GET detail: documents == [body]
    # machine with parties:write may upload (201); the same key and bytes again -> Idempotent-Replay: true and one row
def test_refusals_are_422(client, ..., monkeypatch):
    # monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 8) -> 422 VALIDATION_FAILED "document too large"; "text/plain" -> "unsupported document content type"; b"" -> "empty document"; no file written
def test_the_download_records_view_pii_on_the_document(...):
    # GET .../content as admin: 200, content == PDF, headers content-type application/pdf, content-disposition attachment; filename="<id>.pdf"
    # audit: one VIEW_PII with entity_type "document", entity_id == document id, detail {"group": "document", "fields": ["content"]}
    # machine (parties:read) -> 403 ROLE_FORBIDDEN; another tenant's admin -> 404
def test_a_corrupted_object_is_a_500_object_corrupt(...):
    # overwrite the file; GET content -> (500, "OBJECT_CORRUPT") with TestClient(app, raise_server_exceptions=False) not needed: the XolotlError is handled
def test_legal_hold_is_mlro_only(client, tenant, admin_headers):
    # mlro_headers: POST legal-hold {"legal_hold": true} -> 200, body["legal_hold"] is True; admin -> (403, "ROLE_FORBIDDEN"); machine parties:write -> (403, "ROLE_FORBIDDEN")
    # audit "party.legal_hold_set" detail {"legal_hold": True} exactly once after two identical MLRO calls (second with a new key)
```

`test_relationship_routes.py` (`itf_pack` wherever a relationship is opened or closed; `machine_headers(tenant, "parties:write")`):

```python
def test_a_machine_opens_and_closes_a_relationship_and_retention_appears_on_the_party(client, tenant, itf_pack):
    # POST /v1/relationships {"party_id": ..., "external_ref": "ref-1", "product": None} -> 201 status ACTIVE
    # POST /v1/relationships/{id}/close {"ended_at": "2027-03-01T12:00:00Z"} -> 200 status EXITED, ended_at
    # GET /v1/parties/{id} -> retention_until == "2037-03-01"; GET /v1/parties?external_ref=ref-1 -> [party]
def test_a_duplicate_external_ref_is_a_409(client, tenant, itf_pack)        # (409, "CONFLICT")
def test_opening_without_a_pack_covering_the_product_is_a_404_policy_not_assigned(client, tenant)   # no itf_pack: POST /v1/relationships {"party_id": ..., "external_ref": "ref-1", "product": "EMONEY"} -> (404, "POLICY_NOT_ASSIGNED"); GET /v1/parties?external_ref=ref-1 -> [] (no row was written)
def test_closing_without_a_pack_is_a_404_policy_not_assigned(client, tenant)   # no itf_pack: the row is seeded directly with the C2 model in a staff_session (the open route would refuse); close -> (404, "POLICY_NOT_ASSIGNED"); the relationship stays ACTIVE
def test_scopes_and_unknown_ids(client, tenant, itf_pack)   # parties:read -> 403 SCOPE_MISSING on open; close of uuid7() -> 404 NOT_FOUND; close with a naive ended_at -> 422 (on a relationship opened under itf_pack)
```

`test_routes.py` (append; the two routes C6 leaves to this task):

```python
def test_patch_updates_attributes_without_an_idempotency_key(client, tenant, admin_headers):
    # PATCH /v1/parties/{id} {"person": {"pep_status": "DOMESTIC"}} with admin_headers only -> 200; the staff detail shows person.pep_status DOMESTIC; audit_events(...)[-1].action == "party.updated"
    # PATCH {"organization": {"trade_name": "x"}} on a person -> 422 VALIDATION_FAILED; machine parties:read -> 403 SCOPE_MISSING

def test_party_relationships_are_added_and_shown(client, tenant, admin_headers):
    # POST /v1/parties/{ana}/relationships {"to_party_id": acme, "rel_type": "SHAREHOLDER", "ownership_pct": "51.5"} -> 201, from_party_id == ana, Decimal(body["ownership_pct"]) == Decimal("51.5")
    # GET /v1/parties/{acme} (staff): relationships == [that row]; {"rel_type": "DIRECTOR", "ownership_pct": "10"} -> 422 VALIDATION_FAILED; to_party_id == ana from ana -> 422; machine detail has no relationships key
```

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/modules/party/test_document_routes.py tests/modules/party/test_relationship_routes.py tests/modules/party/test_routes.py --no-cov` → 404s (and 405 on the PATCH) for the new routes; C6's tests stay green.
- [ ] **Step 3: Implement** the two routers, the two handlers in `routes.py`, the fixture, and mount the routers.
- [ ] **Step 4: Run to verify pass.** The three files plus `tests/platform/test_principal.py tests/platform/test_api_conventions.py`, then `make lint types vulture importlint`, then `make test`.
- [ ] **Step 5: Commit** `feat(party): document, legal-hold, customer-relationship, update and party-relationship routes`.

---

### Task C8: Console: parties list, identifier search, create, detail with explicit reveal, legal hold, smoke step

Depends on C6 (the routes it calls) and C7 (`legal-hold`; regenerate `schema.d.ts` after both merged). Console only; the backend is not touched. Plan 2a's A9 and A10 and plan 2b's B6 also append to `console/src/components/layout/nav.ts`, `console/src/App.tsx`, both catalogues and `console/src/i18n/catalogue.test.ts`, and every console task regenerates `console/src/api/schema.d.ts`: whichever console task merges later rebases those files (the edits are additive and nav and route order is immaterial) and regenerates `schema.d.ts` against the merged API instead of merging it.

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerated), `console/src/api/types.ts`, `console/src/auth/access.ts`, `console/src/auth/access.test.ts`, `console/src/auth/guards.test.tsx`, `console/src/components/layout/nav.ts`, `console/src/App.tsx`, `console/src/i18n/es-MX.json`, `console/src/i18n/en.json`, `console/src/i18n/catalogue.test.ts`, `console/e2e/smoke.spec.ts`
- Create: `console/src/features/parties/queries.ts`, `queries.test.tsx`, `form.ts`, `form.test.ts`, `PartyStatusBadge.tsx`, `IdentifierSearch.tsx`, `PartiesPage.tsx`, `PartiesPage.test.tsx`, `CreatePartyPage.tsx`, `CreatePartyPage.test.tsx`, `PartyPage.tsx`, `PartyPage.test.tsx`, `RevealCard.tsx`, `LegalHoldCard.tsx`

**Interfaces:**

Consumes: `api`, `unwrap`, `unwrapReplayable`, `Replayable` (`@/api/client`), `useIdempotentMutation` (`@/api/use-idempotent-mutation`), `fieldProblems` (`@/api/field-errors`), `hasProblemCode` (`@/api/errors`), `useAuth`, `ACCESS`, `hasAnyRole`, `RequireRole`, `PAGE_SIZE` (`@/api/queries`), `PageHeader`, `DataTable`, `Column`, `CursorPager`, `DateTimeCell`, `ErrorBlock`, `EmptyBlock`, `KeyValues`, `ConfirmDialog`, `Badge`, `Field`, `FormAlert`, `Select`, `Input`, `Button`, `Card*`, `msgKey`, `describeError`, the test harnesses `renderWithProviders`/`fakeApi` (`respond`, `refuse`, `networkFailure`, `sentKeys`) and `renderApp`/`fake-network` (`json`, `problem`), `es-MX.json` in the smoke.

Produces:

- `types.ts`: `PartyResponse = Schemas['PartyResponse']`, `PartyStaffResponse`, `PartyPiiResponse`, `PartyCreateRequest`, `PartyUpdateRequest`, `PartyPage = Schemas['Page_PartyResponse_']`, `PartyType`, `PartyStatus`, `IdentifierType`, `MaskedIdentifier`, `LegalHoldRequest`, `PersonInput`, `OrganizationInput`, `IdentifierInput`; a type guard `isStaffView(p: PartyStaffResponse | PartyResponse): p is PartyStaffResponse` (`'contacts' in p`) in `features/parties/queries.ts`.
- `access.ts`: `parties: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']`, `partiesWrite: ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'ADMIN']`, `legalHold: ['MLRO']`; `access.test.ts` adds the three columns to `allowed(role)` (`AUDITOR_RO` → `parties: true, partiesWrite: false, legalHold: false`; `MLRO` → all true; `ANALYST` → `parties: true, partiesWrite: true, legalHold: false`; `ADMIN` → `legalHold: false`); `guards.test.tsx` adds the `/parties` area to its tables.
- `nav.ts`: `labelKey` union gains `'nav.parties'`; `{ to: '/parties', labelKey: 'nav.parties', icon: Contact, roles: ACCESS.parties }` after the home item (`Contact` from `lucide-react`).
- `App.tsx`: inside the `AppShell` children a `RequireRole roles={ACCESS.parties}` group with `{ path: '/parties', lazy: PartiesPage }`, `{ path: '/parties/new', lazy: CreatePartyPage }`, `{ path: '/parties/:partyId', lazy: PartyPage }` (lazy imports as the existing pages).
- `queries.ts`: `const PARTIES = 'parties'`; `interface PartyFilters { status?: PartyStatus; external_ref?: string; id_type?: IdentifierType; value?: string }`; `useParties(filters: PartyFilters, cursor?: string)` (`useQuery`, key `[PARTIES, user?.tenant_id, cursor ?? null, filters]`, `keepPreviousData`, `enabled` as the staff hook); `useParty(partyId: string)` (`useQuery` of `GET /parties/{party_id}`, key `[PARTIES, 'one', tenant, partyId]`; the data is the masked union, narrowed with `isStaffView`); `useRevealParty(partyId)` = `useMutation({ mutationFn: () => unwrap(api.GET('/parties/{party_id}/pii', { params: { path: { party_id: partyId } } })), gcTime: 0 })`; `useCreateParty()` = `useIdempotentMutation<PartyCreateRequest, Replayable<PartyResponse>>` with `gcTime: 0`, invalidating `[PARTIES]`; `useSetLegalHold(partyId)` = `useIdempotentMutation<LegalHoldRequest, Replayable<PartyResponse>>` posting `/parties/{party_id}/legal-hold`, invalidating `[PARTIES]`.
- `form.ts`: `partySchema = z.discriminatedUnion('partyType', [person, organization])` where `person` has `partyType: z.literal('PERSON')`, `givenNames` (trim, 1..200, `msgKey('required')`/`msgKey('tooLong')`), `firstSurname` (1..100), `secondSurname` (≤100 or `''`), `birthDate` (`''` or `YYYY-MM-DD`, `msgKey('dateFormat')`), `countryCode` (`^[A-Z]{2}$`, default `'MX'`), `identifierType` (`IdentifierType` union, default `'RFC'`), `identifierValue` (trim, 1..64, `msgKey('required')`); `organization` has `legalName` (1..300), `tradeName` (≤300 or `''`) plus the same country and identifier fields; `type PartyFormValues = z.input<typeof partySchema>`; `toPartyRequest(values: z.output<typeof partySchema>): PartyCreateRequest` (empty strings → omitted, `birthDate` → `birth_date`); `FORM_FIELD_OF = { given_names: 'givenNames', first_surname: 'firstSurname', second_surname: 'secondSurname', birth_date: 'birthDate', legal_name: 'legalName', trade_name: 'tradeName', country_code: 'countryCode' } as const`. `validation.required` and `validation.tooLong` already exist in both catalogues (`fieldProblems` maps `string_too_long` to `tooLong`; do not redefine them); `validation.dateFormat` is added by plan 2a's Task A10 (the units and calendar pages; add it here only if A10 has not merged yet); the keys new to this task are `validation.countryCode` and `validation.identifierValue`.
- `PartiesPage`: header with the `parties.new` button (hidden without `ACCESS.partiesWrite`); `IdentifierSearch` (a `role="search"` form: `Select` of `IdentifierType` labelled `parties.search.idType`, `Input` labelled `parties.search.value` with `autoComplete="off"`, submit `parties.search.submit`, clear `parties.search.clear`; submitting sets `{ id_type, value }` and resets the cursor; nothing is sent per keystroke); `DataTable` columns `name` (link to `/parties/${id}` showing `display_name` or `parties.table.noName`), `type` (`t(\`parties.type.${party_type}\`)`), `identifiers` (masked values with their type, monospace), `status` (`PartyStatusBadge`), `createdAt`; `CursorPager`; empty states `parties.empty` and `parties.emptyFiltered`; error state with retry.
- `CreatePartyPage`: a `Card` form with a `PERSON`/`ORGANIZATION` toggle, the fields above, `Field` errors from `fieldProblems(error, [...] as const)` mapped by `FORM_FIELD_OF`, `FormAlert` with `whenStatus` for `IDENTIFIER_EXISTS` (`parties.create.identifierExists`) and `IDENTIFIER_INVALID` (`parties.create.identifierInvalid`); on success `navigate(\`/parties/${data.id}\`)` and a `toast.success(t('parties.create.done'))`.
- `PartyPage`: `useParams` guard → `PartyDetail`; `KeyValues` of the masked summary (type, status badge, country, identifiers masked, `retention_until` via `useFormat().date`, legal hold); `RevealCard` (`hasAnyRole` over `ACCESS.parties`; button `parties.reveal.button` → `reveal.mutate()`; while `reveal.isSuccess` shows given names, surnames, birth date, legal name, identifier values and contact values inside an `aria-live="polite"` container with the notice `parties.reveal.notice` (that the view was audited), a `CopyButton` per identifier, and `parties.reveal.hide` calling `reveal.reset()`); `LegalHoldCard` visible only for `ACCESS.legalHold` with a `ConfirmDialog` (`parties.legalHold.set` / `.clear`, `.confirmSet` / `.confirmClear`). The sub-collections (identifiers, contacts, addresses, relationships, documents, customer relationships) are not rendered by this task: C9 adds `PartyCollections` with their tabs and actions under the summary; here the page is the masked summary (which lists the identifiers), the reveal and the legal hold.
- i18n (both catalogues, same tree): `nav.parties`; `parties.title`, `.description`, `.new`, `.empty`, `.emptyFiltered`, `.table.{label,name,noName,type,identifiers,status,createdAt}`, `.type.{PERSON,ORGANIZATION}`, `.status.{ACTIVE,MERGED,PURGED}`, `.identifierType.{CURP,RFC,CLABE,INE,CPF,CNPJ,CUIT,RUT,NIT,CEDULA_CO,DNI_PE,PASSPORT}`, `.search.{idType,value,submit,clear}`, `.create.{title,partyType,givenNames,firstSurname,secondSurname,birthDate,legalName,tradeName,countryCode,identifierType,identifierValue,submit,submitting,done,identifierExists,identifierInvalid}`, `.detail.{title,summary,type,status,country,identifiers,retentionUntil,legalHold,legalHoldOn,legalHoldOff}`, `.reveal.{title,button,hide,running,notice,givenNames,surnames,birthDate,legalName,identifiers,contacts,addresses}`, `.legalHold.{title,set,clear,confirmSet,confirmClear,pending}`; `validation.{countryCode,identifierValue}` (and `validation.dateFormat` only when plan 2a's A10 has not added it); `errors.IDENTIFIER_EXISTS`, `errors.IDENTIFIER_INVALID`, `errors.OBJECT_CORRUPT`. `catalogue.test.ts`: the three codes join `codes`; `parties.identifierType.CURP`, `.RFC`, `.CLABE`, `.INE`, `.CPF`, `.CNPJ`, `.CUIT`, `.RUT`, `.NIT` (the acronym is the text in both languages) join `SAME_IN_BOTH`; every other key must differ between the catalogues.
- Smoke (`console/e2e/smoke.spec.ts`), one `test.step('a party is created, listed masked and its RFC revealed with an audit row', ...)` inserted after the storage step and before the audit deep-link step:

```ts
const RFC = 'CACX7605101P8'
await page.getByRole('link', { name: es.nav.parties, exact: true }).click()
await page.getByRole('button', { name: es.parties.new, exact: true }).click()
await page.getByLabel(es.parties.create.givenNames, { exact: true }).fill('Ana María')
await page.getByLabel(es.parties.create.firstSurname, { exact: true }).fill('Pérez')
await page.getByLabel(es.parties.create.secondSurname, { exact: true }).fill('Gómez')
await page.getByLabel(es.parties.create.birthDate, { exact: true }).fill('1976-05-10')
await page.getByLabel(es.parties.create.identifierValue, { exact: true }).fill(RFC)      // the type defaults to RFC
await page.getByRole('button', { name: es.parties.create.submit, exact: true }).click()
await expect(page.getByRole('heading', { name: es.parties.detail.title, level: 1 })).toBeVisible()
await expect(page.locator('body')).toContainText('****01P8')
await expect(page.locator('body')).not.toContainText(RFC)
await page.getByRole('button', { name: es.parties.reveal.button, exact: true }).click()
await expect(page.locator('body')).toContainText(RFC)
await expect(page.locator('body')).toContainText(es.parties.reveal.notice)
const token = await page.evaluate(() => window.sessionStorage.getItem('xolotl.token'))
const audit = await page.request.get('/v1/audit?action=VIEW_PII', { headers: { Authorization: `Bearer ${token ?? ''}` } })
expect(((await audit.json()) as { items: { action: string }[] }).items.length).toBeGreaterThan(0)
```

- [ ] **Step 1: Regenerate the types and write the failing tests.** From the repository root: `uv run python -c "import json; from xolotl.app import build_app; print(json.dumps(build_app().openapi()))" > console/openapi.json && cd console && npm run gen:api:file`; commit the new `schema.d.ts`. Tests (vitest, assertions through `esMX`): `form.test.ts` (`toPartyRequest` maps a person with an empty second surname to a request without `second_surname`, `birthDate` to `birth_date`, the identifier to `identifiers: [{ id_type, value, country_code }]`; an organisation maps `legalName` to `organization.legal_name`; `'1976-5-1'` fails `dateFormat`); `queries.test.tsx` (`useRevealParty` calls `GET /parties/{party_id}/pii` and its options carry `gcTime: 0`; `useParties` sends `limit: 20` and the filters); `PartiesPage.test.tsx` (`renderWithProviders`: loading `aria-busy`, empty, rows with masked identifiers and a link to `/parties/p1`, failed load with `esMX.errors.INTERNAL` and `'req-test'` then retry, a search that sends exactly one request with `id_type` and `value` after submit and none while typing, paging `[undefined, 'cursor-2']`, the `new` button hidden for `AUDITOR_RO`, "text never markup" with `'<img src=x onerror="window.__pwned=1">'` as `display_name`); `CreatePartyPage.test.tsx` (submits the request shape with an `Idempotency-Key`; a 409 `IDENTIFIER_EXISTS` shows `esMX.parties.create.identifierExists`; a 422 `IDENTIFIER_INVALID` shows `esMX.parties.create.identifierInvalid`; a network failure then a retry reuse one key, `sentKeys()[1] === sentKeys()[0]`; the submit button is disabled while pending); `PartyPage.test.tsx` (`renderApp` at `/parties/p1` with `GET /v1/parties/p1` → a staff view and `GET /v1/parties/p1/pii` → plaintext: the masked value shows and the plaintext does not; clicking `esMX.parties.reveal.button` shows the plaintext and the notice; `esMX.parties.reveal.hide` removes it and `document.body.textContent` no longer contains it; `queryClient.getMutationCache().getAll()` is empty afterwards; the legal-hold card renders for `MLRO` only and posts `{ legal_hold: true }` after the confirm; `AUDITOR_RO` sees the page and no legal-hold card); `access.test.ts` and `guards.test.tsx` extended.
- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck` fails on the missing feature modules; `npx vitest run src/features/parties src/auth` fails.
- [ ] **Step 3: Implement** the files above following `StaffPage.tsx`, `CreateStaffDialog.tsx`, `features/audit/AuditPage.tsx` and `features/security/queries.ts`.
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; then the smoke: `docker compose up -d --build --wait`, `docker compose run --rm -T api xolotl tenant create --slug ci-smoke --legal-name "CI SA de CV" --admin-email admin@ci.mx` (its one-time password into `E2E_ADMIN_PASSWORD`), `npm run test:e2e`. Backend `make ci` is unaffected (no Python change).
- [ ] **Step 5: Commit** `feat(console): party list, search, create and detail with reveal`.

---

### Task C9: Console: detail collections, edit, identifiers, contacts, addresses, relationships, documents and customer relationships

Depends on C7 and C8. Console only. The nine-task cap of this plan keeps it one task; if the cap moves, split it into C9a (`PartyCollections` and the five dialogs) and C9b (documents and customer relationships).

**Files:**
- Modify: `console/src/api/schema.d.ts` (regenerate again if C7 merged after C8's generation), `console/src/api/types.ts` (`IdentifierInput`, `ContactInput`, `AddressInput`, `AddressLines`, `PartyRelationshipInput`, `DocumentResponse`, `DocumentType`, `CustomerRelationshipResponse`, `RelationshipOpenRequest`, `RelationshipCloseRequest`, `MaskedContact`, `AddressResponse`, `PartyRelationshipResponse`), `console/src/features/parties/queries.ts`, `console/src/features/parties/PartyPage.tsx` (mounts `PartyCollections` under the summary and the edit button), `console/src/i18n/es-MX.json`, `en.json`, `catalogue.test.ts` (`SAME_IN_BOTH` additions)
- Create: `console/src/features/parties/sub-forms.ts`, `sub-forms.test.ts`, `PartyCollections.tsx`, `PartyCollections.test.tsx`, `EditPartyDialog.tsx`, `AddIdentifierDialog.tsx`, `AddContactDialog.tsx`, `AddAddressDialog.tsx`, `AddRelationshipDialog.tsx`, `DocumentsCard.tsx`, `CustomerRelationshipsCard.tsx`, `dialogs.test.tsx`, `DocumentsCard.test.tsx`, `CustomerRelationshipsCard.test.tsx`

**Interfaces:**

Produces:

- `queries.ts` additions (all invalidate `[PARTIES]` on success; mutations that return PII set `gcTime: 0`): `useUpdateParty(partyId)` (`useMutation`, `PATCH /parties/{party_id}`, no idempotency key); `useAddIdentifier(partyId)`, `useAddContact(partyId)`, `useAddAddress(partyId)`, `useAddRelationship(partyId)` (`useIdempotentMutation` posting the sub-resource); `useUploadDocument(partyId)` = `useIdempotentMutation<{ file: File; docType: DocumentType; name: string; size: number; lastModified: number }, Replayable<DocumentResponse>>` posting `api.POST('/parties/{party_id}/documents', { params: { path: { party_id }, query: { doc_type } }, body: file as unknown as string, bodySerializer: (body) => body as unknown as BodyInit, headers: { 'Content-Type': file.type, 'Idempotency-Key': key } })` (the one commented cast; the `name`/`size`/`lastModified` fields make two files two submissions); `downloadDocument(partyId, documentId, filename)` = `api.GET('/parties/{party_id}/documents/{document_id}/content', { params, parseAs: 'blob' })` then `URL.createObjectURL`, an `<a download>` click and `URL.revokeObjectURL`; `useOpenRelationship()`, `useCloseRelationship()` (`useIdempotentMutation` on `/relationships` and `/relationships/{relationship_id}/close`).
- `PartyCollections.tsx`: `Tabs` (`parties.detail.tabs.*`) over the staff view, one read-only table per collection: identifiers (type, masked value, country, valid from/to), contacts (kind, masked value, primary), addresses (type, country, codes, postal code, valid from/to; the lines only through the reveal), relationships (direction `parties.relationships.direction.*`, the other party's id as a link, type, ownership, valid from/to), plus the `DocumentsCard` and `CustomerRelationshipsCard` tabs; each tab's add button (hidden without `ACCESS.partiesWrite`) opens its dialog; an empty collection shows `parties.<collection>.empty`.
- `sub-forms.ts`: zod schemas `identifierSchema` (`idType`, `value` 1..64, `countryCode` default `'MX'`), `contactSchema` (`kind`, `value` 1..254, `isPrimary`), `addressSchema` (`addressType`, `street` 1..200, `extNumber`, `intNumber`, `neighbourhood`, `city`, `countryCode`, `adminAreaCode`, `municipalityCode`, `postalCode`, `validFrom`), `relationshipSchema` (`toPartyId` uuid, `relType`, `ownershipPct` `''` or a decimal 0 < x ≤ 100 with at most 4 decimals, never required, refused with `msgKey('ownership')` when `relType` is not `SHAREHOLDER`; `controlBasis`, `validFrom`), `editPersonSchema` / `editOrganizationSchema` (the patchable attributes), `customerSchema` (`partyId`, `externalRef` 1..100, `product`, `accountTier`); the `to*Request` mappers and `FORM_FIELD_OF` tables; `MAX_DOCUMENT_BYTES = 25 * 1024 * 1024` and `ALLOWED_DOCUMENT_TYPES = ['application/pdf', 'image/jpeg', 'image/png'] as const` with `validation.fileTooLarge` / `validation.fileType` keys.
- Dialogs follow `CreateStaffDialog.tsx` (not dismissible while submitting, server field errors through `fieldProblems`, `FormAlert`); `AddIdentifierDialog` maps `IDENTIFIER_EXISTS` and `IDENTIFIER_INVALID` as the create page does; `DocumentsCard` lists the staff view's documents (type, size via `useFormat().number`, uploaded at, a download button) and holds the upload form (`<input type="file" accept="application/pdf,image/jpeg,image/png">`, a `DocumentType` `Select`, client-side size and type checks before any request); `CustomerRelationshipsCard` lists them with `parties.customer.status.<status>` badges, an open form (external ref, product, tier) and a close `ConfirmDialog`; `EditPartyDialog` edits the attributes (person: PEP status, since/until, residency, nationalities, occupation and activity codes, birth country and state, gender; organisation: trade name, legal form, incorporation date, industry code, registry ref, country) and submits a `PartyUpdateRequest` of only the changed fields. Write actions are hidden without `ACCESS.partiesWrite`.
- i18n: `parties.detail.tabs.{identifiers,contacts,addresses,relationships,documents,customer}`, `parties.edit.*`, `parties.identifiers.*` (`add`, `type`, `value`, `country`, `validFrom`, `validTo`, `added`, `empty`), `parties.contacts.*` (`add`, `kind`, `kind.EMAIL`, `kind.PHONE`, `value`, `primary`, `added`, `empty`), `parties.addresses.*` (`add`, `type`, `type.{RESIDENTIAL,FISCAL,OPERATIONAL}`, `street`, `extNumber`, `intNumber`, `neighbourhood`, `city`, `country`, `adminArea`, `municipality`, `postalCode`, `validFrom`, `added`, `empty`), `parties.relationships.*` (`add`, `to`, `type`, `type.{SHAREHOLDER,DIRECTOR,LEGAL_REP,ATTORNEY,CONTROLLER}`, `ownership`, `controlBasis`, `validFrom`, `added`, `from`, `direction.{outgoing,incoming}`, `empty`), `parties.documents.*` (`title`, `upload`, `type`, `type.{IDENTITY,PROOF_OF_ADDRESS,CONSTITUTIVE_ACT,POWER_OF_ATTORNEY,TAX_CERTIFICATE,OTHER}`, `file`, `size`, `uploadedAt`, `download`, `uploaded`, `empty`), `parties.customer.*` (`title`, `open`, `externalRef`, `product`, `tier`, `status.{PENDING,ACTIVE,RESTRICTED,BLOCKED,EXITED}`, `startedAt`, `endedAt`, `close`, `confirmClose`, `closed`, `empty`), `validation.{fileTooLarge,fileType,ownership,uuid}`.

- [ ] **Step 1: Regenerate the types if needed and write the failing tests.** Two harnesses exist and are not interchangeable: `renderWithProviders` with the `fakeApi` of `src/test/fake-api.ts` (`GET` and `POST` `vi.fn` mocks only, `respond`/`refuse`/`sentKeys`), and `renderApp`, whose return value carries the `FakeApi` of `src/test/fake-network.ts` (`installFakeApi` behind it; `calls(route)` and `queries(route)` are its methods, keyed `'<METHOD> /v1/...'`, any method). `sub-forms.test.ts` (each mapper's request shape; the ownership rule; the file checks); `PartyCollections.test.tsx` (`renderApp` at `/parties/p1` with a staff view holding one row per collection: each tab lists its row with masked values only, the add buttons are hidden for `AUDITOR_RO`, an empty collection shows its `empty` text); `dialogs.test.tsx` (`renderWithProviders`: the four add dialogs post their body with an `Idempotency-Key`, are not dismissible while pending, show `esMX.errors.VALIDATION_FAILED`/field errors on a 422, and `AddIdentifierDialog` shows `esMX.parties.create.identifierExists` on a 409); `DocumentsCard.test.tsx` (`const { fakeApi, user } = renderApp(<DocumentsCard ... />, { routes: { 'POST /v1/parties/p1/documents': () => json(doc, { status: 201 }), ... } })`: a chosen 3-byte PDF is sent as the raw body with `Content-Type: application/pdf` and `doc_type` in the query, asserted through `fakeApi.calls('POST /v1/parties/p1/documents')` and `fakeApi.queries('POST /v1/parties/p1/documents')`; a file over the limit never reaches the network (`calls(...)` stays empty) and shows `esMX.validation.fileTooLarge`; download requests `/v1/parties/p1/documents/d1/content`); `CustomerRelationshipsCard.test.tsx` (open posts `{ party_id: 'p1', external_ref, product }`; close posts to `/v1/relationships/r1/close` after the confirm; statuses render their badge text); `PartyPage.test.tsx` (append, through `renderApp` with a `'PATCH /v1/parties/p1'` route, so `src/test/fake-api.ts` needs no `PATCH` mock: `EditPartyDialog` sends a `PATCH` with only the changed field and no `Idempotency-Key`).
- [ ] **Step 2: Run to verify failure.** `cd console && npm run typecheck` (missing modules), `npx vitest run src/features/parties`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass.** `cd console && npm run typecheck && npm run lint && npm test && npm run build`; the smoke of C8 still passes (`npm run test:e2e` against the compose stack with a fresh tenant).
- [ ] **Step 5: Commit** `feat(console): party identifiers, contacts, addresses, relationships, documents and customer relationships`.

---

## Parallelism

- C1 → C2 → C3 are sequential (C2's models import C1's enums; C3 needs the tables).
- After C3: **C4 ∥ C6** (disjoint files: C4 creates `documents.py` and `customer.py` and appends to `schemas.py`, `service.py`, `test_service.py` and `tests/modules/party/conftest.py`; C6 creates `routes.py`, `route_support.py`, `test_routes.py` and edits `src/xolotl/app.py`; C6's tests reach only C2 models and C3 services, never a C4 name).
- C5 after C4; C7 after C4 and C6. C5 and C7 both edit `src/xolotl/app.py` (C5 `wire_platform`, C7 the router list), so they run one after the other in either order.
- **C8 ∥ C5** and **C8 ∥ C7** once C6 and C7 are merged for C8's codegen (C8 touches `console/` only); if C8 starts before C7 merges, it generates its types from C6's API and C9 regenerates them.
- C9 after C7 and C8.
- No task of this plan may run before plan 2b's `0007_connectors` and plan 2a's `0006_policy_units_calendar` are on `main` (C2's migration references both), except C1. C2 also needs plan 2a's A1 (the `_check_cron` validator its setting joins), C4 needs plan 2a's A7 (`xolotl.platform.policy` and `tests/platform/policy_support.py::install_both_packs`), and C5 needs plan 2a's A5 (the `units.fetch` schedule call and its rows in the pinned schedule tests) and plan 2b's B3b (the hoisted store in `wire_platform`) merged before it starts.
- **Across plans.** The three SP-02 plans append to the same files: `src/xolotl/app.py` (A5, B3b and C5 in `wire_platform`; A8, B5, C6 and C7 all mount routers "after `routes_webhooks.router`"), `src/xolotl/models.py`, `src/xolotl/platform/enums.py` (A2, B1), `src/xolotl/platform/config.py` (A1, B1, C2), `src/xolotl/modules/tenants/service.py` (A5, C5), `src/xolotl/cli.py`, `tests/isolation/factories.py`, `tests/platform/test_migration_chain.py`, `tests/platform/test_config.py`, `tests/platform/test_errors.py`, `tests/test_cli.py`, `tests/platform/test_worker.py` and `tests/modules/tenants/test_kernel_events.py` (A5, C5), `.env.example`, `README.md`, `render.yaml` and `tests/test_render_blueprint.py` (A1, B1), and in the console `console/src/components/layout/nav.ts` (A9 and B6 both "after the audit entry", C8 after the home item), `console/src/App.tsx`, `console/src/auth/access.ts` and its tests, both catalogues, `console/src/i18n/catalogue.test.ts`, `console/src/api/schema.d.ts` and `console/e2e/smoke.spec.ts` (A9, C8). Every one of these edits is additive (a new row, line, import, router, nav item or key beside the existing ones), so whichever task merges second rebases its branch onto `main` and re-applies its own lines; router mount order and nav order are immaterial (no route overlaps and the tests pin membership, not position); `console/src/api/schema.d.ts` is never merged by hand but regenerated against the merged API (`npm run gen:api:file`) by the task that merges later.

## Exit criteria

Spec §12 row `tests/modules/party/*` and §13, refined for this plan:

- `make ci` green after every task; the console gates and the Playwright smoke green after C8 and C9.
- Validators: every `IdentifierType` has a golden file and the corpus passes (C1); `make ci` runs it.
- A party with a valid CURP and RFC is created through `POST /v1/parties`, found by `GET /v1/parties?id_type=RFC&value=` through the blind index without decrypting anything (no `VIEW_PII` row), revealed by an `ANALYST` through `GET /v1/parties/{id}/pii` with one `VIEW_PII` audit row per field group, and is a 404 for another tenant whose search by the same RFC finds nothing (C6).
- The machine view (`parties:read`) returns masked identifiers and initials and never the plaintext; the dedup refusal carries the existing party id only for staff (C6).
- Every new table passes the RLS harness and the schema-sync gate (C2).
- A document is stored in the local object store under `documents/<party_id>/<document_id>` with its sha256 and size recorded, refused over 25 MB or outside PDF/JPEG/PNG, downloaded only by staff with a `VIEW_PII` row (C4, C7).
- Closing a customer relationship sets `retention_until` from the effective policy snapshot (`P10Y` of the ITF pack) and publishes `party.relationship_closed.v1` with the snapshot id; the sweep purges a party whose period ended unless it is on legal hold or still has an open relationship, deletes its documents, audits `party.purged` and publishes `party.purged.v1`; `xolotl party purge-due --tenant <slug> [--dry-run]` does the same by hand (C4, C5).
- `party.created.v1`, `party.updated.v1`, `party.identifier_added.v1`, `party.relationship_opened.v1`, `party.relationship_closed.v1`, `party.purged.v1` are published with ids only (C3–C5).
- Console: a party is created, listed masked, and its RFC revealed with a `VIEW_PII` audit row in the smoke (C8); identifiers, contacts, addresses, relationships, documents and customer relationships are managed from the detail page (C9).

## Open questions for the product owner

1. **Parties that never became customers.** A prospect with no `customer_relationship` has `retention_until = NULL` and is never purged by the sweep (the spec ties retention to the relationship's end). Should a separate period apply to never-onboarded parties, or is that SP-08's onboarding concern?
2. **Closing without a policy.** A tenant with no pack assignment covering the closing date cannot close a relationship: `POST /v1/relationships/{id}/close` answers 404 `POLICY_NOT_ASSIGNED` (plan 2a's error) and the relationship stays `ACTIVE`. Is a platform default retention preferable to blocking the exit? The same check now runs at open: `POST /v1/relationships` answers 404 `POLICY_NOT_ASSIGNED` when no assignment covers the relationship's `product` on its start date, and on a tenant assigned per product (plan 2a's D21: a tenant-wide assignment never overlaps a per-product one, and `product=None` sees only tenant-wide assignments) a relationship without a product cannot be opened at all. Should `product` become required, or should a product-less relationship resolve to the strictest merge of every assignment in force (a change to plan 2a's resolver and its golden)?
3. **Reveal and legal-hold roles.** This plan lets every staff role reveal PII (including `AUDITOR_RO`) and reserves the legal hold to `MLRO`. Confirm, or name the roles.
4. **What a purge keeps.** The purge nulls the person's and organisation's encrypted columns, deletes identifiers, contacts, addresses, documents and relationship edges, and keeps `customer_relationship` (external references, statuses, dates) and `verification_check` rows as the non-PII record. Confirm that external references may outlive the purge.
