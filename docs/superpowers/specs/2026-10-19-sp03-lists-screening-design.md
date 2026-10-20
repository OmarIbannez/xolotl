# SP-03 — Lists and screening — Sub-project design

**Parent spec:** `docs/superpowers/specs/2026-10-18-program-architecture-design.md` (D1, D2, D4, D6, D7, D8, D9, D10, D18, D20, D21, D22; ports §4: `ScreeningIndex`, `ObjectStore`, `KeyProvider`, `EventBus`, `JobQueue`, `Clock`, `Notifier`; SP-03 row of §7.1 and the SP-03 demo of §7.3).
**Product sources (Notion, Xolotl Docs):** PRD-03 Screening (`FR-SC-001`..`018`, `NFR-SC-001`..`007`), TS-03 Screening (`DR-301`..`308`, `INT-301`..`305`, `SEC-301`..`302`, `PRV-301`..`303`, `OPS-301`..`304`), ADR-013 Name-matching engine and indices, ADR-011 Encryption, key hierarchy and searchable fields, ADR-022 MVP runtime profile, DD-00 Console shell. Tasks XOL-18 (list ingestion), XOL-19 (name matcher), XOL-25 (delta and periodic re-screen, private lists), XOL-75 (this spec), and the "Screening library" task (parallel package, §4).
**Status:** draft 2026-10-19 for the product owner's review. Builds on SP-01 and SP-02 as merged on `main` (migrations 0001 to 0008; party core from plan 2c).

## 1. Scope

In scope:
- **Screening library** (a bounded, database-free component built in parallel by a separate engineer, §4): parsers for `UN_CONSOLIDATED`, `OFAC_SDN`, `EU_FSF`, `UK_FCDO` and `MX_SAT_69B` into one `ListEntry` shape; the version delta function; normalization v2, phonetic key, nickname table and IDF weights; the in-memory candidate index; the explaining scorer and bands; the evaluation corpus with a CI floor. This spec fixes its public interface; it does not re-plan it.
- **List catalog and ingestion**: the shared `list_source` registry, versioned snapshots with per-entry history and deltas, scheduled fetchers with conditional requests, a mass-removal guard, freshness and stale alerts, offline file loading from the CLI, tenant subscriptions derived from the effective policy (mandatory always on, recommended tenant-removable).
- **Private lists**: tenant-supplied `MX_LPB` and internal lists uploaded as CSV or XLSX, validated with row-level errors, encrypted per tenant, versioned with deltas, and screened only against that tenant's subjects.
- **Screening engine**: `party_name_index` (D7), screening subjects (parties and ad hoc payloads; counterparties reserved for SP-04), runs with the exact list set, matcher version, scoring configuration and policy snapshot used, hits with stored explanations, deduplicated matches, four-eyes dispositions, a whitelist keyed on entry content, and a per-subject screening status.
- **Re-screening**: on party data change, on shared list deltas (only added and changed entries are re-screened), on a new private-list version (the whole base against that version), delisting re-evaluation with a suggested disposition, periodic full re-screen, and re-indexing when the matcher version changes.
- **API, events, CLI and console**: both audiences (D20) with tipping-off protection (`FR-SC-013`), staff-only event types that webhooks can never carry, `xolotl lists …` and `xolotl screening …` commands, and the screening pages (lists, private lists, match queue, match detail with explanation, party screening tab, ad hoc check, evaluation).

Out of scope (later sub-projects or phases): the licensed global sanctions and PEP database and any PEP list ingestion (§18 Q1; private lists of kind `PEP` work in SP-03); Argentina RePET and Brazil CGU lists (country kits after Mexico); counterparty screening and its synchronous cache (`FR-SC-012`, `DR-308`, SP-04, which adds subject kind `COUNTERPARTY`); crypto-wallet matching (`FR-SC-015`: parsers keep OFAC digital-currency addresses as identifiers; exact matching lands with SP-04 counterparties); the freeze playbook, alerts, queues and the 24-hour clock (`FR-SC-008`, SP-06, which consumes `screening.true_match_confirmed.v1`); PEP status, cooling-off expiry review tasks (`FR-SC-009`, `DR-307`; review tasks are alerts per D15, so SP-06; the party's declared `pep_status` from SP-02 is passed to the matcher as a qualifier only); the shadow second-opinion matcher (`FR-SC-017`, before pilot, SP-11); LLM triage (`FR-SC-018`, not in MVP); adverse media (phase 2); OpenSearch (the `ScreeningIndex` port stays ready).

### 1.1 What depends on the party tables

| Part | Needs party tables? | Can start |
|---|---|---|
| Screening library: parsers, `ListEntry`, delta, normalization, phonetic key, nicknames, IDF, in-memory index, scorer, bands, evaluation corpus (§4) | **No** (no database at all) | now, in parallel |
| List catalog, ingestion, freshness, subscriptions, private lists, lists API and pages (§5.1, §5.2 first block, §6, §7) | **No** | now (needs the library's `ListEntry`, `parse` and `diff`; can begin against the UN parser alone) |
| `party_name_index`, subjects, runs, matches, dispositions, whitelist, re-screening, screening API and pages (§5.2 second block, §8 to §12) | **Yes** (reads parties through `modules.party` service functions; composite FKs to `party`) | after plan 2c, which is merged on `main` as of today except its last console task |

## 2. Toolchain and conventions

- **Module layout.** The program spec's separate `lists` module (§3.3) is folded into `screening` (Decision 1): the library's parsers live under `screening/lists/`, and a `lists` module that imported them would make `lists` and `screening` depend on each other. Nothing outside screening depended on `lists` alone.

  ```
  src/xolotl/modules/screening/
  ├── lists/        library: ListEntry, parsers per source, diff            (pure, §4.1)
  ├── matching/     library: normalize, phonetic, nicknames, idf, index, score, bands (pure, §4.2)
  ├── data/         library data files: nicknames, particles, legal forms, surname frequencies
  ├── catalog/      list_source, list_version, list_entry, deltas, fetchers, ingest, subscriptions, private lists
  ├── engine/       index holder, subject builder, screen service, runs, hits, matches, status
  ├── review/       dispositions, whitelist, allowed_actions
  ├── rescreen/     data-change, list-delta, private-list, periodic and reindex jobs; party subscribers
  ├── routes_lists.py · routes_private_lists.py · routes_screening.py · schemas.py · events.py · errors.py
  └── sources.yaml  shared list catalog seed (codes, URLs, formats, cadence)
  ```
- **Import-linter contracts (added).** `screening.lists` and `screening.matching` import only the standard library, their third-party parsing and string libraries, and `xolotl.platform.errors`/`xolotl.platform.enums`; they never import `sqlalchemy`, `fastapi`, `xolotl.modules.party` or another screening subpackage except `lists` ← `matching` (the scorer reads `ListEntry`). `screening.catalog` must not import `xolotl.modules.party` (it is party-free by contract, §1.1). `screening.engine`, `screening.review` and `screening.rescreen` may import `xolotl.modules.party` service functions only. The program spec §3.3 row becomes `screening: platform, party` (the `lists` row is removed).
- **Dependencies.** The library chooses its own (expected `lxml` for streaming XML and `rapidfuzz` for string similarity; recorded in its plan). SP-03 adds runtime `openpyxl` (read-only XLSX for private lists) and `pg_trgm` (Postgres extension, created by migration 0009 as the owner role).
- **Migrations.** `0009_lists.py` (plan 3a: shared list tables, subscriptions, private lists) and `0010_screening.py` (plan 3b: name index, subjects, runs, hits, matches, dispositions, whitelist, batches). Serialised as usual: one migration per plan.
- All SP-01 and SP-02 conventions bind: RLS and isolation factories, `tenant_fk`, `v1_router`, `require_access`, idempotency keys on POSTs, `Encrypted` fields with `reveal()` only in service code, `VIEW_PII` when a human sees revealed values, audit actions, ids-only event payloads, UPPERCASE non-native enums emitted to TypeScript, Google-style docstrings, golden tests that print a diff.

## 3. Configuration (prefix `XOLOTL_`)

| Key | Default | Purpose |
|---|---|---|
| `LISTS_FETCH_ENABLED` | `true` in `prod`, `false` otherwise | network fetching of shared lists; dev, test and CI load files with `xolotl lists load-file` |
| `LISTS_POLL_CRON` | `* * * * *` | per-tenant `lists.poll` job (UTC): claims due sources, catches the tenant up on new versions, checks staleness |
| `LISTS_FETCH_TIMEOUT_SECONDS` | `120` | per HTTP request |
| `LISTS_MAX_FILE_MB` | `300` | refuse larger downloads |
| `LISTS_MAX_REMOVAL_RATIO` | `0.20` | a new version removing more than this share of entries is refused (mass-removal guard, §6.3) |
| `PRIVATE_LIST_MAX_MB` / `PRIVATE_LIST_MAX_ROWS` | `20` / `100000` | upload limits |
| `SCREENING_INDEX_REFRESH_SECONDS` | `30` | how often a process checks for newer active list versions |
| `SCREENING_PRIVATE_SHARDS` | `64` | per-process LRU of tenant private-list shards |
| `SCREENING_DELTA_BATCH` | `200` | delta entries per `screening.list_delta` job |
| `SCREENING_RESCREEN_BATCH` | `500` | parties per periodic or private-list re-screen job |
| `SCREENING_PERIODIC_CRON` | `0 2 1 * *` | monthly full re-screen per tenant (`America/Mexico_City`; §18 Q4) |

## 4. The screening library (bounded component, parallel package)

Built and owned by the "Screening library" package (prompt `docs/superpowers/handoffs/screening-library.md`), under `src/xolotl/modules/screening/{lists,matching,data}/` with fixtures and corpus under `tests/modules/screening/`. No tables, routes or jobs. SP-03's database-backed plans consume exactly the interface below; any change to it is agreed in the library's PR and reflected here. Field names are normative; internal helpers are the library's choice.

### 4.1 Lists: `ListEntry`, parsing, delta

```python
class ListCode(StrEnum):   # shared sources; private codes are MX_LPB and INTERNAL_<SLUG> (strings, §7)
    UN_CONSOLIDATED = "UN_CONSOLIDATED"; OFAC_SDN = "OFAC_SDN"; EU_FSF = "EU_FSF"; UK_FCDO = "UK_FCDO"; MX_SAT_69B = "MX_SAT_69B"
class EntityType(StrEnum): PERSON = "PERSON"; ORGANIZATION = "ORGANIZATION"; VESSEL = "VESSEL"; AIRCRAFT = "AIRCRAFT"; OTHER = "OTHER"
class NameQuality(StrEnum): PRIMARY = "PRIMARY"; STRONG = "STRONG"; WEAK = "WEAK"     # WEAK = low-quality a.k.a.
class IdType(StrEnum):     # normalized identifier kinds the scorer compares
    RFC = "RFC"; CURP = "CURP"; PASSPORT = "PASSPORT"; NATIONAL_ID = "NATIONAL_ID"; TAX_ID = "TAX_ID"
    REGISTRATION = "REGISTRATION"; IMO = "IMO"; DIGITAL_CURRENCY_ADDRESS = "DIGITAL_CURRENCY_ADDRESS"; OTHER = "OTHER"

@dataclass(frozen=True, slots=True)
class PartialDate: year: int; month: int | None = None; day: int | None = None        # precision is what is set
@dataclass(frozen=True, slots=True)
class EntryName:
    full: str                                   # as published (original script kept)
    quality: NameQuality
    given: str | None = None; surname: str | None = None                # when the source separates them
    script: str | None = None                   # ISO 15924 when known (Latn, Cyrl, Arab)
@dataclass(frozen=True, slots=True)
class EntryIdentifier:
    id_type: IdType; value: str                 # value as published
    normalized: str                             # upper-case alphanumerics per id_type (the scorer compares this);
                                                # DIGITAL_CURRENCY_ADDRESS keeps its case (addresses are case-sensitive)
    country: str | None = None                  # ISO 3166-1 alpha-2
    label: str | None = None                    # source label, e.g. "Cedula No.", "R.F.C."; network for wallets
@dataclass(frozen=True, slots=True)
class EntryAddress: country: str | None; text: str | None

@dataclass(frozen=True, slots=True)
class ListEntry:
    list_code: str
    entry_key: str                              # stable per source: UN DATAID, OFAC uid, EU logicalId, UK unique id, SAT RFC
    entity_type: EntityType
    primary_name: EntryName
    aliases: tuple[EntryName, ...] = ()
    birth_dates: tuple[PartialDate, ...] = ()
    birth_places: tuple[str, ...] = ()
    nationalities: tuple[str, ...] = ()         # alpha-2
    gender: str | None = None                   # MALE, FEMALE, None
    identifiers: tuple[EntryIdentifier, ...] = ()
    addresses: tuple[EntryAddress, ...] = ()
    programs: tuple[str, ...] = ()              # regimes, UN reference numbers, 69-B "supuesto"
    listed_on: date | None = None
    delisted_on: date | None = None
    status: str | None = None                   # MX_SAT_69B: PRESUNTO, DEFINITIVO, DESVIRTUADO, SENTENCIA_FAVORABLE
    screenable: bool = True                     # False for 69-B DESVIRTUADO and SENTENCIA_FAVORABLE (kept, never hit)
    source_ref: str = ""                        # human pointer into the source file (e.g. "DATAID 6908555")
    def canonical_json(self) -> bytes: ...      # sorted keys, no whitespace, tuples as arrays; excludes nothing
    def content_hash(self) -> bytes: ...        # sha256(canonical_json()) — the whitelist and delta key (D18)
    @classmethod
    def from_json(cls, data: Mapping[str, JsonValue]) -> "ListEntry": ...   # round-trips canonical_json

@dataclass(frozen=True, slots=True)
class ParsedList:
    list_code: str; parser_version: str         # e.g. "un-xml/1"; bumped when output for the same file changes
    published_at: datetime | None; source_version: str | None      # from the file header when present
    entries: tuple[ListEntry, ...]              # entry_key unique within the list (duplicates refused)

def parse(list_code: str, data: BinaryIO) -> ParsedList          # dispatches on list_code; never fetches
class ListFormatError(XolotlError): status = 422; default_code = "LIST_FORMAT_INVALID"   # detail names the source and position, never a whole record

@dataclass(frozen=True, slots=True)
class ListDelta:
    added: tuple[str, ...]; changed: tuple[str, ...]; removed: tuple[str, ...]   # entry keys, sorted
    unchanged: int
def diff(previous: Mapping[str, bytes], current: Iterable[ListEntry]) -> ListDelta
    # previous: entry_key -> content_hash of the prior version (read from Postgres, no reparse);
    # a screenable -> non-screenable transition (69-B DESVIRTUADO) is CHANGED; the engine treats it as a delisting
```

Source formats fixed by this spec (the library confirms each against the current publication and records the URL and date beside its trimmed fixture):
- `UN_CONSOLIDATED`: the Security Council consolidated list XML.
- `OFAC_SDN`: the Sanctions List Service **SDN enhanced XML** (D22: enhanced XML primary; the legacy CSV parser is kept for fixtures and as a fallback format), because it carries structured identifiers, dates with precision and digital-currency addresses that the CSV flattens.
- `EU_FSF`: the EU Financial Sanctions Files consolidated XML (version 1.1 schema).
- `UK_FCDO`: the FCDO **UK Sanctions List** XML (OFSI's separate consolidated list was withdrawn in early 2026; the parser targets the FCDO file).
- `MX_SAT_69B`: SAT's `Listado_Completo_69-B.csv` (Windows-1252, preamble lines before the header); `entry_key` is the RFC, `entity_type` is inferred from RFC length (12 organization, 13 person), `status` from the "Situación del contribuyente" column. The file has one row per proceeding, so an RFC can appear on several rows: they merge into one entry with the most severe status (`DEFINITIVO` > `PRESUNTO` > `SENTENCIA_FAVORABLE` > `DESVIRTUADO`), screenable while any proceeding keeps it listed; court-suppressed `XXXXXXXXXXXX` rows are skipped (library T3).

### 4.2 Matching: query, normalization, index, score, bands

```python
MATCHER_VERSION: str                            # "2.1.0" as tuned by the corpus (library T6); stored on every run and on party_name_index rows

class TokenRole(StrEnum): GIVEN = "GIVEN"; SURNAME = "SURNAME"; PARTICLE = "PARTICLE"; LEGAL_FORM = "LEGAL_FORM"; GENERIC = "GENERIC"; UNKNOWN = "UNKNOWN"
@dataclass(frozen=True, slots=True)
class Token: text: str; folded: str; phonetic: str; role: TokenRole
@dataclass(frozen=True, slots=True)
class NormalizedName:
    folded: str                                 # NFKC, case- and diacritic-folded, punctuation removed, single spaces, particles kept in text
    tokens: tuple[Token, ...]                   # particles and legal forms are tokens with their role (ignored by scoring weights)
    legal_form: str | None                      # canonical form, e.g. SA_DE_CV, SAPI, S_DE_RL, LLC, LTD, GMBH
def normalize_name(text: str, *, entity_type: EntityType, given: str | None = None, surname: str | None = None) -> NormalizedName
def phonetic_key(token: str) -> str             # Spanish- and English-aware; stable across MATCHER_VERSION patch releases

@dataclass(frozen=True, slots=True)
class QueryIdentifier: id_type: IdType; normalized: str; country: str | None = None
@dataclass(frozen=True, slots=True)
class NameQuery:
    entity_type: EntityType                     # PERSON or ORGANIZATION
    names: tuple[str, ...]                      # first = legal name; then trade name or other known names
    given_names: str | None = None; first_surname: str | None = None; second_surname: str | None = None
    birth_date: date | None = None; gender: str | None = None
    nationalities: tuple[str, ...] = (); countries: tuple[str, ...] = ()   # residence and incorporation countries
    identifiers: tuple[QueryIdentifier, ...] = ()
    def fingerprint(self) -> bytes: ...         # sha256 of canonical JSON; stored on the run for replay

@dataclass(frozen=True, slots=True)
class BlockingPlan: key_sets: tuple[tuple[str, ...], ...]  # a subject is a candidate if its keys contain ANY one set
def blocking_plan(name: NormalizedName, idf: IdfTable) -> BlockingPlan   # rare token alone, or two common tokens together
def token_keys(token: Token) -> frozenset[str]   # phonetic key, its one-letter deletions, "p:"/"s:" 3-letter prefix and suffix
def name_keys(name: NormalizedName) -> frozenset[str]   # union of token_keys: what party_name_index.phonetic_keys stores
    # (library T6: blocking keys are not only Token.phonetic values; a single key held by over 1,000 entries never retrieves alone)

class IdfTable:                                 # token weights from the list corpus plus data/surnames_mx.csv
    @classmethod
    def build(cls, entries: Iterable[ListEntry]) -> "IdfTable": ...
    def weight(self, folded: str) -> float: ...

@dataclass(frozen=True, slots=True)
class IndexedEntry: entry: ListEntry; ref: UUID; shard: str     # ref = list_entry.id or private_list_entry.id; shard = list code
@dataclass(frozen=True, slots=True)
class Candidate: indexed: IndexedEntry; via: Literal["IDENTIFIER", "NAME"]
class MemoryIndex:
    @classmethod
    def build(cls, entries: Iterable[IndexedEntry], *, idf: IdfTable) -> "MemoryIndex": ...   # immutable once built
    def candidates(self, query: NameQuery, *, shards: Collection[str], top_k: int) -> list[Candidate]: ...
        # exact identifier hits first (always returned, beyond top_k), then name candidates by blocking and IDF overlap

class MatchKind(StrEnum): EXACT = "EXACT"; PHONETIC = "PHONETIC"; NICKNAME = "NICKNAME"; INITIAL = "INITIAL"; FUZZY = "FUZZY"
class QualifierOutcome(StrEnum): MATCH = "MATCH"; PARTIAL = "PARTIAL"; CONFLICT = "CONFLICT"; ABSENT = "ABSENT"
@dataclass(frozen=True, slots=True)
class TokenAlignment: query: str; entry: str; kind: MatchKind; similarity: float; weight: float; role: TokenRole
@dataclass(frozen=True, slots=True)
class QualifierResult: attribute: Literal["BIRTH_DATE", "NATIONALITY", "COUNTRY", "GENDER", "ENTITY_TYPE", "IDENTIFIER"]; outcome: QualifierOutcome; adjustment: float; detail: str   # detail masks identifiers (****1234)
@dataclass(frozen=True, slots=True)
class RuleApplied: code: Literal["ID_MATCH_FLOOR", "ID_CONFLICT_CAP", "IDENTIFIER_ONLY_LIST", "ENTITY_TYPE_MISMATCH"]; detail: str
@dataclass(frozen=True, slots=True)
class MatchExplanation:
    query_name: str; entry_name: str; entry_name_quality: NameQuality   # best-aligned pair (folded forms)
    name_score: float
    alignment: tuple[TokenAlignment, ...]; unmatched_query: tuple[str, ...]; unmatched_entry: tuple[str, ...]
    qualifiers: tuple[QualifierResult, ...]; rules: tuple[RuleApplied, ...]
    def to_json(self) -> dict[str, JsonValue]: ...                       # stored as screening_hit.features (§5.2)

@dataclass(frozen=True, slots=True)
class ScoringConfig:                            # matcher parameters; bands are NOT here (they come from the policy)
    identifier_only_lists: frozenset[str] = frozenset({"MX_SAT_69B"})
    id_match_floor: Decimal = Decimal("0.95"); id_conflict_cap: Decimal = Decimal("0.30")   # from the effective policy
    def config_hash(self) -> bytes: ...
@dataclass(frozen=True, slots=True)
class MatchScore: score: Decimal; explanation: MatchExplanation      # score in [0, 1], 4 decimal places
def score(query: NameQuery, entry: ListEntry, *, idf: IdfTable, config: ScoringConfig) -> MatchScore

class Band(StrEnum): ALERT = "ALERT"; LOW_CONFIDENCE = "LOW_CONFIDENCE"; AUTO_DISCARDED = "AUTO_DISCARDED"; BELOW_CUTOFF = "BELOW_CUTOFF"
@dataclass(frozen=True, slots=True)
class BandThresholds: alert: Decimal; review: Decimal
def band_for(result: MatchScore, thresholds: BandThresholds) -> Band
    # IDENTIFIER_ONLY_LIST without ID_MATCH_FLOOR -> LOW_CONFIDENCE at most (the alert threshold is policy, so the cap lives here);
    # ID_CONFLICT_CAP applied and the uncapped name score would have reached `review` -> AUTO_DISCARDED (logged as a hit);
    # otherwise score >= alert -> ALERT; >= review -> LOW_CONFIDENCE; else BELOW_CUTOFF
```

Scoring rules the library implements (values tuned by the corpus, then frozen per `MATCHER_VERSION`): token alignment with Jaro-Winkler similarity, phonetic equality, nickname equivalence (`data/nicknames_es.yaml`: José/Pepe, Francisco/Paco/Pancho, Guadalupe/Lupe, …) and initials; IDF token weights with family names above given names; particles (`de`, `del`, `la`, `los`, `y`) and legal forms carry no weight; a dropped second surname is tolerated; qualifiers adjust additively (birth date by precision, nationality and country, gender) within a bounded total; identifier hard rules: same `id_type` and same `normalized` value floors the score at `id_match_floor`, same type and a different value caps it at `id_conflict_cap`; `ENTITY_TYPE_MISMATCH` (person against organization) scores 0. For identifier-only lists (`MX_SAT_69B`, `TS-03 §6`), an RFC equality scores 1.0; without an RFC on the subject the name score is capped just below `alert` so the hit can reach `LOW_CONFIDENCE` at most; a different RFC caps at `id_conflict_cap`.

### 4.3 Evaluation corpus and the CI floor

Corpus under `tests/modules/screening/corpus/`: seed entries from the trimmed fixtures, a deterministic perturbation generator (dropped maternal surname, order swap, accent loss, common letter substitutions, nicknames, initials, particle changes, typos, truncation at 60 characters) producing at least 20,000 labelled pairs (`TS-03 §10`), and at least 500 hand-labelled hard negatives (very common Mexican names that must not alert). `xolotl screening eval` (library function `evaluate(corpus) -> EvaluationReport`, CLI in 3c) reports per list kind: recall at `LOW_CONFIDENCE`-or-above, precision at `ALERT`, candidate retrieval recall at the production `top_k`, and alerts per 1,000 hard negatives. Floors in `corpus/floors.yaml`: sanctions recall **≥ 0.98** (`FR-SC-004`), retrieval recall **≥ 0.995** (`NFR-SC-006`), precision at `ALERT` set by the library from its first honest measurement. CI fails below a floor; a floor may only be raised in a library PR, and lowering one needs the product owner's written approval in the PR. Library interface (T6, `matching/evaluation.py`): `load_corpus(root, floors) -> Corpus` (`entries`, `pairs: tuple[CorpusPair]` with `query: NameQuery`, `list_code`, `entry_key`, `perturbations`, `hard_negatives`, `entry_for(pair)`), `generate_pairs(entries, *, per_entry, seed)`, `perturb`, `load_floors(path) -> Floors`, `evaluate(corpus, *, thresholds, config=None, top_k=50) -> EvaluationReport` (`to_json()` with `matcher_version`), and `check_floors(report, floors) -> list[FloorFailure]`. The hard negatives are synthetic (frequent given names and the `data/surnames_mx.csv` surnames), negative by construction rather than hand-labelled. Non-gating latency tests carry the `perf` pytest marker, deselected by default (`uv run pytest -m perf --no-cov`).

## 5. Tables

### 5.1 Shared reference tables (no `tenant_id`; added to `SHARED_TABLES`; migration 0009)

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;                    -- used by 0010

CREATE TABLE list_source (code text PRIMARY KEY,           -- UN_CONSOLIDATED, OFAC_SDN, EU_FSF, UK_FCDO, MX_SAT_69B
  list_kind text NOT NULL,                                 -- SANCTION, FISCAL
  title text NOT NULL, jurisdiction text NOT NULL,         -- UN, US, EU, GB, MX
  format text NOT NULL,                                    -- UN_XML, OFAC_ENHANCED_XML, EU_FSF_XML, UK_SL_XML, SAT_69B_CSV
  url text NOT NULL, poll_interval interval NOT NULL,      -- UN 15 min, OFAC 30 min, EU and UK 60 min, SAT 24 h (INT-304)
  enabled boolean NOT NULL DEFAULT true,
  next_poll_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz, lease_holder text,
  etag text, last_modified text,                           -- conditional request validators
  last_attempt_at timestamptz, last_success_at timestamptz, last_change_at timestamptz,
  last_error text, consecutive_failures int NOT NULL DEFAULT 0);
-- seeded and updated from screening/sources.yaml by `xolotl lists seed` (owner role, idempotent; also run by `xolotl db upgrade`)
-- app role: SELECT; UPDATE (next_poll_at, lease_until, lease_holder, etag, last_modified, last_attempt_at, last_success_at,
--   last_change_at, last_error, consecutive_failures) as a column grant

CREATE TABLE list_version (id uuid PRIMARY KEY, list_code text NOT NULL REFERENCES list_source(code),
  seq int NOT NULL,                                        -- 1, 2, … per list; the order tenants catch up in
  status text NOT NULL,                                    -- ACTIVE, SUPERSEDED
  published_at timestamptz, source_version text, parser_version text NOT NULL,
  fetched_at timestamptz NOT NULL, loaded_by text NOT NULL,          -- 'job:<id>' or 'cli:<user>'
  file_sha256 bytea NOT NULL, file_size bigint NOT NULL, storage_key text NOT NULL,
  entry_count int NOT NULL, added int NOT NULL, changed int NOT NULL, removed int NOT NULL,
  previous_version_id uuid REFERENCES list_version(id),
  UNIQUE (list_code, seq), UNIQUE (list_code, file_sha256));
CREATE UNIQUE INDEX ux_list_version_active ON list_version (list_code) WHERE status = 'ACTIVE';
-- app role: SELECT, INSERT; UPDATE (status)

CREATE TABLE list_entry (id uuid PRIMARY KEY, list_code text NOT NULL REFERENCES list_source(code),
  source_entry_key text NOT NULL, content_hash bytea NOT NULL,
  entity_type text NOT NULL, primary_name text NOT NULL, screenable boolean NOT NULL,
  entry jsonb NOT NULL,                                    -- ListEntry.canonical_json(); immutable
  from_seq int NOT NULL, to_seq int,                       -- in versions with from_seq <= seq < to_seq; NULL = in the active version
  UNIQUE (list_code, source_entry_key, from_seq));
CREATE UNIQUE INDEX ux_list_entry_current ON list_entry (list_code, source_entry_key) WHERE to_seq IS NULL;
-- app role: SELECT, INSERT; UPDATE (to_seq)

CREATE TABLE list_entry_delta (version_id uuid NOT NULL REFERENCES list_version(id), source_entry_key text NOT NULL,
  change text NOT NULL,                                    -- ADDED, CHANGED, REMOVED
  prev_entry_id uuid REFERENCES list_entry(id), new_entry_id uuid REFERENCES list_entry(id),
  PRIMARY KEY (version_id, source_entry_key),
  CHECK ((change = 'ADDED'   AND prev_entry_id IS NULL     AND new_entry_id IS NOT NULL)
      OR (change = 'CHANGED' AND prev_entry_id IS NOT NULL AND new_entry_id IS NOT NULL)
      OR (change = 'REMOVED' AND prev_entry_id IS NOT NULL AND new_entry_id IS NULL)));
-- app role: SELECT, INSERT
```

Entries are stored once per content, not once per version (Decision 3): an unchanged entry keeps its row across versions; a change closes the old row (`to_seq`) and inserts a new one. The members of version `n` are the rows with `from_seq <= n AND (to_seq IS NULL OR to_seq > n)`. A match always points at an immutable `list_entry` row, so it can be replayed (`NFR-SC-007`, `DR-304`). Raw files go to the object store under the reserved shared namespace `SHARED_NAMESPACE = UUID(int=0)` at `lists/<code>/<version_id>.<ext>` (Decision 4).

### 5.2 Tenant tables (forced RLS, PK `(tenant_id, id)`, isolation factories)

Migration 0009 (party-free):

```sql
CREATE TABLE tenant_list_subscription (tenant_id uuid NOT NULL, id uuid NOT NULL,
  list_code text NOT NULL REFERENCES list_source(code),
  requirement text NOT NULL,                               -- MANDATORY, RECOMMENDED (from the effective policy)
  enabled boolean NOT NULL DEFAULT true,
  last_seq int NOT NULL DEFAULT 0,                         -- versions up to this one have been handed to re-screening
  stale_notified_at timestamptz, changed_by text, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, list_code),
  CHECK (requirement <> 'MANDATORY' OR enabled));

CREATE TABLE private_list (tenant_id uuid NOT NULL, id uuid NOT NULL,
  list_code text NOT NULL,                                 -- MX_LPB or INTERNAL_<SLUG>
  list_kind text NOT NULL,                                 -- BLOCKED (MX_LPB), SANCTION, PEP, INTERNAL
  title text NOT NULL, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, list_code),
  CHECK (list_code = 'MX_LPB' AND list_kind = 'BLOCKED' OR list_code LIKE 'INTERNAL\_%' AND list_kind <> 'BLOCKED'));

CREATE TABLE private_list_version (tenant_id uuid NOT NULL, id uuid NOT NULL, list_id uuid NOT NULL,
  seq int NOT NULL, status text NOT NULL,                  -- ACTIVE, SUPERSEDED
  uploaded_by text NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(),
  file_sha256 bytea NOT NULL, file_size int NOT NULL, content_type text NOT NULL,
  storage_key text NOT NULL,                               -- the upload sealed with the tenant DATA key, then stored
  entry_count int NOT NULL, added int NOT NULL, changed int NOT NULL, removed int NOT NULL,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, list_id, seq),
  FOREIGN KEY (tenant_id, list_id) REFERENCES private_list (tenant_id, id));
CREATE UNIQUE INDEX ux_private_list_version_active ON private_list_version (tenant_id, list_id) WHERE status = 'ACTIVE';

CREATE TABLE private_list_entry (tenant_id uuid NOT NULL, id uuid NOT NULL, list_id uuid NOT NULL,
  source_entry_key text NOT NULL,                          -- the file's clave column, else a keyed tag of name + birth date
  content_tag bytea NOT NULL,                              -- HMAC(tenant INDEX key, canonical JSON): no dictionary attack on names
  entity_type text NOT NULL,
  entry_enc bytea NOT NULL,                                -- EncryptedJson of ListEntry.canonical_json()
  from_seq int NOT NULL, to_seq int,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, list_id, source_entry_key, from_seq),
  FOREIGN KEY (tenant_id, list_id) REFERENCES private_list (tenant_id, id));
CREATE UNIQUE INDEX ux_private_list_entry_current ON private_list_entry (tenant_id, list_id, source_entry_key) WHERE to_seq IS NULL;

CREATE TABLE private_list_entry_delta (tenant_id uuid NOT NULL, version_id uuid NOT NULL, source_entry_key text NOT NULL,
  change text NOT NULL, prev_entry_id uuid, new_entry_id uuid,
  PRIMARY KEY (tenant_id, version_id, source_entry_key),
  FOREIGN KEY (tenant_id, version_id) REFERENCES private_list_version (tenant_id, id),
  FOREIGN KEY (tenant_id, prev_entry_id) REFERENCES private_list_entry (tenant_id, id),
  FOREIGN KEY (tenant_id, new_entry_id) REFERENCES private_list_entry (tenant_id, id));   -- same CHECK as list_entry_delta
```

Migration 0010 (needs `party`):

```sql
CREATE TABLE party_name_index (tenant_id uuid NOT NULL, id uuid NOT NULL, party_id uuid NOT NULL,
  name_kind text NOT NULL,                                 -- LEGAL (person full name or legal name), TRADE
  norm_name text NOT NULL, tokens text[] NOT NULL, phonetic_keys text[] NOT NULL,
  matcher_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, party_id, name_kind),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id));
CREATE INDEX ix_pni_phonetic ON party_name_index USING gin (phonetic_keys);
CREATE INDEX ix_pni_trgm ON party_name_index USING gin (norm_name gin_trgm_ops);
-- plaintext normalized tokens only (D7, PRV-301); never serialized by any route; RLS like every tenant table

CREATE TABLE screening_subject (tenant_id uuid NOT NULL, id uuid NOT NULL,
  kind text NOT NULL,                                      -- PARTY, AD_HOC (COUNTERPARTY reserved for SP-04)
  party_id uuid, input_enc bytea,                          -- AD_HOC: EncryptedJson of the NameQuery input
  status text NOT NULL DEFAULT 'CLEAR',                    -- CLEAR, PENDING_REVIEW, TRUE_MATCH
  last_screened_at timestamptz, last_run_id uuid, retention_until date,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, party_id),
  FOREIGN KEY (tenant_id, party_id) REFERENCES party (tenant_id, id),
  CHECK ((kind = 'PARTY' AND party_id IS NOT NULL AND input_enc IS NULL)
      OR (kind = 'AD_HOC' AND party_id IS NULL AND input_enc IS NOT NULL)));

CREATE TABLE screening_list_set (tenant_id uuid NOT NULL, id uuid NOT NULL, content_hash bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, content_hash));      -- the exact list versions a run used, shared by runs
CREATE TABLE screening_list_set_member (tenant_id uuid NOT NULL, list_set_id uuid NOT NULL, list_code text NOT NULL,
  list_version_id uuid REFERENCES list_version(id), private_list_version_id uuid,
  PRIMARY KEY (tenant_id, list_set_id, list_code),
  FOREIGN KEY (tenant_id, list_set_id) REFERENCES screening_list_set (tenant_id, id),
  FOREIGN KEY (tenant_id, private_list_version_id) REFERENCES private_list_version (tenant_id, id),
  CHECK ((list_version_id IS NULL) <> (private_list_version_id IS NULL)));

CREATE TABLE screening_run (tenant_id uuid NOT NULL, id uuid NOT NULL, subject_id uuid NOT NULL,
  mode text NOT NULL,          -- AD_HOC, NEW_SUBJECT, DATA_CHANGE, LIST_DELTA, PRIVATE_LIST, PERIODIC, REINDEX (ONBOARDING, COUNTERPARTY reserved)
  trigger_type text NOT NULL,  -- API, CONSOLE, EVENT, JOB, CLI
  trigger_ref text,            -- request id, event id, job id or batch id
  list_set_id uuid NOT NULL, policy_snapshot_id uuid NOT NULL,
  matcher_version text NOT NULL, scoring_config_hash bytea NOT NULL, query_fingerprint bytea NOT NULL,
  restricted_to_delta boolean NOT NULL DEFAULT false,      -- LIST_DELTA and PRIVATE_LIST runs score only the batch's entries
  candidate_count int NOT NULL, hit_count int NOT NULL, new_match_count int NOT NULL,
  outcome text NOT NULL,                                   -- subject status after the run: CLEAR, PENDING_REVIEW, TRUE_MATCH
  latency_ms int NOT NULL, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  batch_id uuid, run_id uuid,                              -- run_id: workflow run (D9), unused in SP-03
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, subject_id) REFERENCES screening_subject (tenant_id, id),
  FOREIGN KEY (tenant_id, list_set_id) REFERENCES screening_list_set (tenant_id, id),
  FOREIGN KEY (tenant_id, policy_snapshot_id) REFERENCES policy_snapshot (tenant_id, id));
CREATE INDEX ix_screening_run_subject ON screening_run (tenant_id, subject_id, created_at DESC);

CREATE TABLE screening_match (tenant_id uuid NOT NULL, id uuid NOT NULL, subject_id uuid NOT NULL,
  list_code text NOT NULL, list_kind text NOT NULL,        -- SANCTION, FISCAL, BLOCKED, PEP, INTERNAL
  list_entry_id uuid REFERENCES list_entry(id), private_list_entry_id uuid,
  source_entry_key text NOT NULL, entry_content_hash bytea NOT NULL,   -- content_hash, or content_tag for private entries
  band text NOT NULL, score numeric(5,4) NOT NULL,         -- at creation: ALERT or LOW_CONFIDENCE
  origin_run_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'OPEN',                     -- OPEN, ESCALATED, FALSE_POSITIVE, TRUE_MATCH, DELISTED, SUPERSEDED
  suggested_disposition text,                              -- DELISTED when the entry is removed or becomes non-screenable
  superseded_by_match_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, subject_id, list_code, source_entry_key, entry_content_hash),   -- one finding per subject and entry content
  FOREIGN KEY (tenant_id, subject_id) REFERENCES screening_subject (tenant_id, id),
  FOREIGN KEY (tenant_id, private_list_entry_id) REFERENCES private_list_entry (tenant_id, id),
  FOREIGN KEY (tenant_id, origin_run_id) REFERENCES screening_run (tenant_id, id),
  FOREIGN KEY (tenant_id, superseded_by_match_id) REFERENCES screening_match (tenant_id, id),
  CHECK ((list_entry_id IS NULL) <> (private_list_entry_id IS NULL)));
CREATE INDEX ix_screening_match_queue ON screening_match (tenant_id, status, band, created_at);

CREATE TABLE screening_hit (tenant_id uuid NOT NULL, id uuid NOT NULL, run_id uuid NOT NULL,
  list_code text NOT NULL, list_entry_id uuid REFERENCES list_entry(id), private_list_entry_id uuid,
  source_entry_key text NOT NULL, entry_content_hash bytea NOT NULL,
  score numeric(5,4) NOT NULL,
  band text NOT NULL,                                      -- ALERT, LOW_CONFIDENCE, AUTO_DISCARDED, WHITELISTED (BELOW_CUTOFF is counted, not stored)
  auto_reason text,                                        -- ID_CONFLICT_CAP, WHITELIST
  features jsonb NOT NULL,                                 -- MatchExplanation.to_json() + thresholds and list kind (§8.4)
  match_id uuid,                                           -- the finding this hit raised or re-confirmed
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES screening_run (tenant_id, id),
  FOREIGN KEY (tenant_id, private_list_entry_id) REFERENCES private_list_entry (tenant_id, id),
  FOREIGN KEY (tenant_id, match_id) REFERENCES screening_match (tenant_id, id),
  CHECK ((list_entry_id IS NULL) <> (private_list_entry_id IS NULL)));

CREATE TABLE match_disposition (tenant_id uuid NOT NULL, id uuid NOT NULL, match_id uuid NOT NULL,
  decision text NOT NULL,                                  -- FALSE_POSITIVE, POSSIBLE_TRUE_MATCH, TRUE_MATCH, DELISTED, SUPERSEDED
  rationale_enc bytea NOT NULL,                            -- EncryptedStr, at least 10 characters
  decided_by_type text NOT NULL,                           -- STAFF, SYSTEM (SUPERSEDED only)
  decided_by uuid,                                         -- staff_user.id; NULL for SYSTEM
  prior_disposition_id uuid,                               -- the escalation a TRUE_MATCH or second-review decision answers
  policy_snapshot_id uuid NOT NULL, decided_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, match_id) REFERENCES screening_match (tenant_id, id),
  FOREIGN KEY (tenant_id, decided_by) REFERENCES staff_user (tenant_id, id),
  FOREIGN KEY (tenant_id, prior_disposition_id) REFERENCES match_disposition (tenant_id, id),
  FOREIGN KEY (tenant_id, policy_snapshot_id) REFERENCES policy_snapshot (tenant_id, id),
  CHECK ((decided_by_type = 'STAFF') = (decided_by IS NOT NULL)),
  CHECK (decision <> 'TRUE_MATCH' OR prior_disposition_id IS NOT NULL));
-- append-only (APPEND_ONLY_TABLES, grant SELECT, INSERT); constraint trigger match_disposition_four_eyes rejects a row whose
-- decided_by equals the prior disposition's decided_by (DR-305: two distinct people, enforced in the database too)

CREATE TABLE screening_whitelist (tenant_id uuid NOT NULL, id uuid NOT NULL, subject_id uuid NOT NULL,
  list_code text NOT NULL, source_entry_key text NOT NULL, entry_content_hash bytea NOT NULL,
  disposition_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz, revoked_by uuid,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, subject_id, list_code, source_entry_key, entry_content_hash),
  FOREIGN KEY (tenant_id, subject_id) REFERENCES screening_subject (tenant_id, id),
  FOREIGN KEY (tenant_id, disposition_id) REFERENCES match_disposition (tenant_id, id),
  FOREIGN KEY (tenant_id, revoked_by) REFERENCES staff_user (tenant_id, id));

CREATE TABLE screening_batch (tenant_id uuid NOT NULL, id uuid NOT NULL,
  kind text NOT NULL,                                      -- LIST_DELTA, PRIVATE_LIST, PERIODIC, REINDEX
  ref text NOT NULL,                                       -- list_version id, private_list_version id, or the period/matcher version
  status text NOT NULL,                                    -- RUNNING, COMPLETED, FAILED
  total int, done int NOT NULL DEFAULT 0, runs int NOT NULL DEFAULT 0, new_matches int NOT NULL DEFAULT 0,
  cursor text, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, last_error text,
  PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, kind, ref));
```

The whitelist is keyed on the subject rather than the party (D18 says party): a subject is one-to-one with a party, and ad hoc subjects need the same suppression. `screening_hit` keeps every non-trivial score of every run, so "was this customer screened against list X on date Y, and what happened?" (PRD-03 §3, auditor) is one query: runs for the subject, their list set members, their hits.

## 6. List ingestion

### 6.1 Catalog and subscriptions

- `screening/sources.yaml` holds one record per shared list (code, kind, title, jurisdiction, format, URL, poll interval). `xolotl lists seed` upserts `list_source` rows (owner role); `xolotl db upgrade` runs it after migrating, so a URL change ships as a YAML change.
- Subscriptions are reconciled by every `lists.poll` run against `effective_policy(on=today, product=None).screening`: each shared code in `mandatory_lists` gets `requirement = MANDATORY` and is forced on; each code in `recommended_lists` gets `RECOMMENDED`, on by default; codes in neither are deleted. `MX_LPB` in `mandatory_lists` is a private list: when the tenant has no active `MX_LPB` version, the poll raises an in-app notification `lists.lpb_missing` to ADMIN and MLRO once a day.
- `PATCH /v1/lists/subscriptions/{code}` (ADMIN) toggles a recommended list; disabling a mandatory one is 409 `LIST_MANDATORY`. Audited `lists.subscription_changed` (D4: recommended lists are tenant-removable; overrides stay tighten-only, so this is a separate setting rather than an override).

### 6.2 Fetching (shared, one fetch per interval across all tenants)

Jobs are tenant-scoped (SP-01), so the shared fetch rides on every tenant's `lists.poll` job, as `units.fetch` does in SP-02, with a lease making it happen once:
1. In a short transaction: `UPDATE list_source SET lease_until = now() + 10 min, lease_holder = :job WHERE code = (SELECT code FROM list_source WHERE enabled AND next_poll_at <= now() AND (lease_until IS NULL OR lease_until < now()) ORDER BY next_poll_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *`. At most one source per job run, so a slow download never delays the tenant's catch-up.
2. Outside any transaction: `ListFetcher.fetch(source, etag, last_modified) -> FetchResult(not_modified | bytes, etag, last_modified)` via `httpx` with conditional headers, redirects followed, `LISTS_FETCH_TIMEOUT_SECONDS`, the size cap, and the raw bytes hashed. `HttpListFetcher` is the adapter; tests use `respx` and recorded fixtures. When `LISTS_FETCH_ENABLED` is false the claim step is skipped entirely.
3. Not modified, or a body whose sha256 equals the active version's: record `last_attempt_at`, `last_success_at`, clear the error, `next_poll_at = now() + poll_interval`. Freshness counts this as success (`FR-SC-003`).
4. Otherwise ingest (§6.3). Any error (network, `ListFormatError`, guard refusal) sets `last_error` (code and short reason only), increments `consecutive_failures`, backs `next_poll_at` off (`min(poll_interval * 2^failures, 6 h)`) and keeps the previous version active (`TS-03 §7`).

### 6.3 Ingesting a file (shared by the poll job and `xolotl lists load-file`)

`ingest_file(session, code, data, *, loaded_by, fetched_at, accept_drop=False) -> IngestReport`:
1. `parse(code, BytesIO(data))` (library). Refused files never produce a version.
2. Read the active version's current entries (`entry_key -> content_hash`) and `diff()` them (library).
3. **Mass-removal guard:** `removed / previous entry_count > LISTS_MAX_REMOVAL_RATIO` refuses with `LIST_MASS_REMOVAL` unless `accept_drop` (CLI only, audited in the log with the operator). A truncated download must never delist a fifth of a sanctions list.
4. Store the raw file (`SHARED_NAMESPACE`, `lists/<code>/<version_id>.<ext>`), then in one transaction: insert `list_version` (`seq = previous + 1`, `ACTIVE`, counts), set the previous one `SUPERSEDED`, insert `list_entry` rows for added and changed entries (`from_seq = seq`), set `to_seq = seq` on the rows of changed and removed entries, insert `list_entry_delta` rows, update `list_source` freshness and `last_change_at`. Concurrent ingestion of one list is impossible (lease plus `UNIQUE (list_code, seq)`).
5. The first version of a list is all `ADDED`; it is not re-screened as a delta (no tenant has subscribed to a version that did not exist): tenants start from `last_seq = 0` and the first catch-up enqueues a full re-screen batch (`PERIODIC` kind with ref `initial:<code>`) instead of a 20,000-entry delta.

### 6.4 Tenant catch-up and freshness

In the same `lists.poll` run, for each enabled subscription, versions with `seq > last_seq` are handed over in order: one `screening_batch(kind=LIST_DELTA, ref=version_id)` and a `screening.list_delta` job per version (key `list_delta:<version_id>`), then `last_seq` advances and `lists.version_published.v1` is published, all in one transaction. With the default cron, a change reaches every tenant's re-screen within about a minute of ingestion (`NFR-SC-002`, `NFR-SC-003` measured in SP-11). A source whose `last_success_at` is older than twice its `poll_interval` is **stale**: the poll notifies ADMIN and MLRO (`lists.source_stale`) once per stale episode (`stale_notified_at`), and clears the flag when it is fresh again (`FR-SC-003`, `OPS-301`).

## 7. Private lists (`MX_LPB` and internal lists)

- `POST /v1/private-lists` (MLRO or ADMIN; API scope `private_lists:write`) creates the list (`MX_LPB` at most once per tenant).
- `POST /v1/private-lists/{id}/versions` (multipart, same access) uploads a CSV (UTF-8, comma or semicolon) or XLSX (first sheet) with the template columns `clave, tipo (PF|PM), nombre_completo | nombres + primer_apellido + segundo_apellido | razon_social, alias (separated by "|"), fecha_nacimiento (AAAA, AAAA-MM or AAAA-MM-DD), rfc, curp, nacionalidad, otro_identificador, fecha_alta, observaciones`. The whole file is validated first: any bad row rejects the upload with 422 `PRIVATE_LIST_INVALID` and a row-level error list (`row`, `column`, `code`) that never echoes cell values; there is no partial version (`TS-03 §7`). Identifiers are normalized with the party validators' rules; an invalid RFC or CURP is a row error. Rows map to `ListEntry` (list kind from the list; `entry_key` from `clave`, or a keyed tag of the normalized name and birth date when blank).
- Versioning mirrors §6.3 (diff against the active version's keys and tags, entries with `from_seq`/`to_seq`, a delta table), without the mass-removal guard (a confidential list may legitimately shrink). The upload, sealed with the tenant DATA key, is stored under `private-lists/<list_id>/<version_id>`. Audited `private_list.version_uploaded` (counts only). The new version publishes `private_list.version_activated.v1` (staff-only) and starts a `PRIVATE_LIST` batch: the **whole** party base re-screened against the new version (`FR-SC-010`; findings already open are deduplicated, so only new content alerts).
- Reading entries (`GET /v1/private-lists/{id}/entries`, MLRO and ADMIN only) decrypts them and audits `private_list.entries_viewed` plus `VIEW_PII`; exports are not offered in SP-03.
- Isolation: entries are tenant rows under RLS, decrypted only into that tenant's in-memory shard (§8.1); the isolation harness plus a dedicated test proves tenant A's `MX_LPB` never produces a hit for tenant B (`PRV-302`).

## 8. Screening engine

### 8.1 Index holder (the `ScreeningIndex` port, in-memory adapter)

`ScreeningIndexHolder` (one per process, API and worker) wraps the library's `MemoryIndex`:
- **Shared shard**: built from the active version of every `list_source` (screenable rows only, `IdfTable.build` over them) at start-up in a background thread and swapped atomically when `SCREENING_INDEX_REFRESH_SECONDS` finds a newer active `seq`; it records the version id per list it holds. A screen that needs a version the holder has not loaded (a delta job for a version ingested a second ago) forces a synchronous refresh first.
- **Private shards**: built lazily per tenant from its active private versions inside that tenant's session (decrypting `entry_enc`), keyed by `(tenant_id, private_list_version_ids)`, evicted LRU beyond `SCREENING_PRIVATE_SHARDS`; a newer active version invalidates the shard on the next screen. A private shard is only ever consulted with the session's own tenant id.
- **Reverse candidates** (`reverse_candidates(changed, tenant)` in the program port) are not in memory: they are the Postgres blocking query of §10.2 over `party_name_index`. The in-memory customer shard of the program spec is not built (Decision 2).

### 8.2 Subjects and queries

- `subject_for_party(session, party_id) -> ScreeningSubject` creates the `PARTY` subject on first use. `query_for_party(session, party_id) -> NameQuery` reads the party through `modules.party` service functions and decrypts names, birth date and identifier values internally (no `VIEW_PII`: no human sees them): persons give `given_names`, `first_surname`, `second_surname`, `birth_date`, `gender`, `nationalities`, `birth_country` and identifiers (`RFC`, `CURP`, `PASSPORT`); organizations give the legal name, `trade_name`, `country_of_incorporation` and the RFC.
- `AD_HOC` subjects come from `POST /v1/screening/checks` with an inline subject; the input is stored encrypted for reproducibility and gets `retention_until` from the effective policy's `retention.kyc_after_relationship` counted from the run.
- `index_party_names(session, party_id)` upserts `party_name_index` rows (`LEGAL`, and `TRADE` when present) with the library's `normalize_name` output and `MATCHER_VERSION`.

### 8.3 `screen(session, subject, *, mode, trigger, lists=None, only_entries=None) -> RunResult`

1. `effective = effective_policy(on=today_mx, product=None)`; `snapshot_id = snapshot_policy(effective)`. Bands per list kind from `effective.screening.bands`: `SANCTION`, `BLOCKED` and `INTERNAL` use `sanctions_alert`/`sanctions_review`; `PEP` uses `pep_alert` and `pep_alert − 0.15` for review (Decision 6); `FISCAL` uses the sanctions bands with the identifier-only rule. `ScoringConfig` takes `id_match_floor` and `id_conflict_cap` from the same bands.
2. Lists: enabled subscriptions plus the tenant's active private lists, narrowed by `lists` (staff ad hoc only). The versions the holder actually uses become a `screening_list_set` (upserted by content hash).
3. Candidates: `MemoryIndex.candidates(query, shards, top_k)` with `top_k` 50 for sanctions-like shards and 25 for `PEP` (`TS-03 §5`); for `only_entries` (delta and private-list batches) a throwaway `MemoryIndex` built from just those entries replaces the holder, so reverse and forward screening score through identical code.
4. Score each candidate (`score`), band it (`band_for`), then: a whitelisted `(subject, list_code, entry_key, content_hash)` becomes a `WHITELISTED` hit with `auto_reason = WHITELIST` and no finding (`FR-SC-007`: suppressed and logged); `AUTO_DISCARDED` is stored as a hit only; `ALERT` and `LOW_CONFIDENCE` insert a `screening_match` with `ON CONFLICT DO NOTHING` on the finding key, then the hit links to the new or existing finding. A new finding for an entry whose older open finding (same subject, list, key, different hash) is still `OPEN` or `ESCALATED` closes that older one as `SUPERSEDED` with a `SYSTEM` disposition pointing at the new one.
5. Recompute the subject status: `TRUE_MATCH` if any finding is `TRUE_MATCH`; else `PENDING_REVIEW` if any is `OPEN` or `ESCALATED`; else `CLEAR`. Write the run (counts, latency, fingerprint, matcher version, config hash), update the subject, publish events (§11), return.

Budget: one name against the shared shard well under 300 ms p95 (`NFR-SC-001`) on the MVP profile; a non-gating `perf` test at 50,000 entries records it, and SP-11 measures it under load.

### 8.4 Stored explanation (`screening_hit.features`)

`MatchExplanation.to_json()` plus `{"v": 1, "list_kind", "thresholds": {"alert", "review"}, "matcher_version", "config_hash", "entry_ref": {"list_code", "entry_key", "list_entry_id" | "private_list_entry_id", "list_version_seq"}}`. Query-side tokens are the normalized plaintext tokens (the same exposure as `party_name_index`, D7); identifiers are masked. This is what the console renders (`FR-SC-006`): aligned tokens with their kind and weight, qualifiers, rules applied, score, band, thresholds, list version and configuration.

Replay (`NFR-SC-007`): `xolotl screening replay <run_id> --tenant` rebuilds the subject's query, compares its fingerprint with the stored one (a changed subject is reported, not re-scored), re-scores the stored hits against their immutable entry rows with the stored matcher version (refusing when the running `MATCHER_VERSION` differs) and diffs scores and bands; the replay test runs it over a fixture tenant in CI.

## 9. Dispositions and whitelist

- `POST /v1/screening/matches/{id}/dispositions` (staff only, Idempotency-Key) with `{decision, rationale}`. Allowed transitions:

| From | Decision | Who | Result |
|---|---|---|---|
| `OPEN` | `FALSE_POSITIVE` | ANALYST, SENIOR_ANALYST, MLRO | `FALSE_POSITIVE`; whitelist row for the exact entry content |
| `OPEN` | `POSSIBLE_TRUE_MATCH` | ANALYST, SENIOR_ANALYST, MLRO | `ESCALATED` |
| `ESCALATED` | `TRUE_MATCH` | SENIOR_ANALYST or MLRO, **not** the escalating user | `TRUE_MATCH`; `screening.true_match_confirmed.v1` |
| `ESCALATED` | `FALSE_POSITIVE` | SENIOR_ANALYST or MLRO, not the escalating user | `FALSE_POSITIVE`; whitelist row |
| `OPEN`, `ESCALATED` with `suggested_disposition = DELISTED` | `DELISTED` | SENIOR_ANALYST or MLRO | `DELISTED` (`FR-SC-011`) |
| `TRUE_MATCH` | — | — | terminal in SP-03; lifting a freeze is SP-06's legal process, never automatic |

- Violations are 409 `DISPOSITION_NOT_ALLOWED` with machine-readable `blocked_reasons` (`SAME_REVIEWER`, `ROLE_REQUIRED`, `STATE`, `NOT_DELISTED`); the staff match view returns `allowed_actions` and `blocked_reasons` for the current user (D15, D20). An exhaustive state × role × same-or-different-user × decision test is the acceptance test, mirroring sentinel's.
- Every disposition is a `match_disposition` row (append-only, four-eyes trigger), audited `screening.match_dispositioned`, and publishes `screening.match_dispositioned.v1`. AUDITOR_RO reads everything and writes nothing; API keys never disposition.
- Whitelist: suppresses future hits only while the entry's content hash is unchanged, so a changed entry re-alerts (`FR-SC-007`, `DR-306`). `DELETE /v1/screening/whitelist/{id}` (MLRO) revokes it (`revoked_at`, audited `screening.whitelist_revoked`); the next run may alert again.

## 10. Re-screening

### 10.1 Party data change (`FR-SC-001`)

Consumer `screening` subscribes to `party.created.v1`, `party.updated.v1`, `party.identifier_added.v1` and `party.purged.v1`. In the dispatcher's transaction it re-indexes the party's names and enqueues `screening.screen_party` (key `screen_party:<party_id>:<event_id>`, mode `NEW_SUBJECT` for created, else `DATA_CHANGE`). The job skips when a run for the subject started after the event's time (a synchronous check already covered it). `party.purged.v1` deletes the party's `party_name_index` rows and redacts the query-side tokens in its hits' `features` (scores, bands and entry references stay as the compliance record).

### 10.2 Shared list delta (`FR-SC-003`, exit criterion "alerts only changed entries")

`screening.list_delta` (payload `batch_id`, `version_id`, `offset`) takes `SCREENING_DELTA_BATCH` `ADDED` and `CHANGED` delta rows (screenable new rows only), then:
1. **Blocking in Postgres** under the tenant's session: for each entry, the library's `blocking_plan` over each of its names gives key sets; candidates are `SELECT party_id FROM party_name_index WHERE phonetic_keys @> ANY-of(:key_sets) OR similarity(norm_name, :folded) >= 0.4`, plus, for each identifier of type `RFC`, `CURP` or `PASSPORT`, the party found through `modules.party`'s blind-index lookup. The union is capped per entry (5,000 parties; above that the entry is logged, its name candidates are screened by the next periodic run, and its identifier matches are still screened in the delta). With the full `name_keys` stored, common single keys make the cap reachable at tens of thousands of parties per tenant; a reverse-side stop-key rule is the follow-up (product owner, 2026-10-20).
2. For each candidate party: `screen(…, mode=LIST_DELTA, only_entries=<the batch's entries the party was blocked on>)`.
3. Enqueue the next offset, or complete the batch.

`CHANGED` entries whose new row is non-screenable (69-B `DESVIRTUADO` or `SENTENCIA_FAVORABLE`) and `REMOVED` entries are not screened: every finding of the tenant on that `(list_code, entry_key)` in `OPEN`, `ESCALATED` or `TRUE_MATCH` gets `suggested_disposition = DELISTED` and publishes `screening.match_delisting_suggested.v1`; only a person closes it (`FR-SC-011`).

Retrieval recall of this blocking is part of the evaluation (§16): the corpus's true pairs, loaded as parties, must be found by the Postgres query at least as well as by the memory index (≥ 0.995, `NFR-SC-006`).

### 10.3 Private-list version (`FR-SC-010`)

`screening.private_list_rescreen` (payload `batch_id`, `private_list_version_id`, `after_party_id`) walks the tenant's parties in id order, `SCREENING_RESCREEN_BATCH` at a time, screening each against the version's screenable entries (`mode=PRIVATE_LIST`, `only_entries`). Removed and non-screenable private entries get the delisting suggestion as in §10.2.

### 10.4 Periodic and re-index (`FR-SC-001`, `NFR-SC-004`)

`screening.periodic_rescreen` on `SCREENING_PERIODIC_CRON` per tenant creates a `PERIODIC` batch (ref `YYYY-MM`) and walks every party (`mode=PERIODIC`) against the full current list set. At worker start, `ensure_screening_index` compares `party_name_index.matcher_version` with `MATCHER_VERSION`; on a difference it enqueues `screening.reindex` (batch `REINDEX`, ref the version), which re-indexes and re-screens every party (`mode=REINDEX`). Progress of every batch is visible (`GET /v1/screening/batches`, `TS-03 §7`).

## 11. Events, webhooks and audit

| Event | Audience | Data (ids only) |
|---|---|---|
| `lists.version_published.v1` | tenant (webhook-safe) | `list_code`, `version_id`, `seq`, `added`, `changed`, `removed` |
| `private_list.version_activated.v1` | **staff-only** | `private_list_id`, `version_id` |
| `screening.run_completed.v1` | tenant (webhook-safe) | `run_id`, `subject_id`, `party_id`, `mode`, `outcome` |
| `screening.status_changed.v1` | tenant (webhook-safe) | `subject_id`, `party_id`, `status` (`INT-305`) |
| `screening.match_created.v1` | **staff-only** | `match_id`, `subject_id`, `list_kind`, `band` |
| `screening.match_dispositioned.v1` | **staff-only** | `match_id`, `decision`, `disposition_id` |
| `screening.true_match_confirmed.v1` | **staff-only** | `match_id`, `subject_id`, `party_id`, `list_code`, `on_true_match` action from the snapshot (SP-06 runs the playbook) |
| `screening.match_delisting_suggested.v1` | **staff-only** | `match_id`, `list_code` |

**Staff-only event types (platform change, `FR-SC-013`, `PRV-303`).** `xolotl.platform.events` gains `register_staff_only(event_type)` (called by the screening module at composition). The webhooks consumer never fans a staff-only type out, wildcard subscriptions included, and webhook endpoint create and update refuse such types with 422 `EVENT_TYPE_NOT_DELIVERABLE`. A test subscribes a wildcard endpoint and asserts no `screening.match_*` delivery row is ever written. In-process consumers (SP-06 cases) still receive them.

**Audit actions:** `lists.subscription_changed`, `private_list.created`, `private_list.version_uploaded`, `private_list.entries_viewed`, `screening.check_requested` (ad hoc only, input never in detail), `screening.match_dispositioned`, `screening.whitelist_revoked`, plus `VIEW_PII` when the staff match view or a private-list view reveals personal values. Shared ingestion is not tenant activity and is not in any tenant's chain; `list_version` (with the raw file's sha256 and `loaded_by`) is its provenance record.

## 12. API

Staff view = full typed explanation; tenant (API client) view = status and stable codes only, never list details (`FR-SC-013`, `SEC-301`, D20).

| Method and path | Access | Notes |
|---|---|---|
| `GET /v1/lists` | staff; scope `lists:read` | per subscribed list: code, kind, requirement, enabled, active version seq, published and fetched times, entry count, `stale` (`FR-SC-002`, `FR-SC-003`) |
| `GET /v1/lists/{code}/versions` · `GET /v1/lists/{code}/versions/{id}/delta` | staff | cursor pages; delta rows with entry primary names (public data) |
| `GET /v1/lists/{code}/entries/{entry_id}` | staff | the `ListEntry` JSON |
| `PATCH /v1/lists/subscriptions/{code}` | ADMIN | `{enabled}`; mandatory refused |
| `POST /v1/private-lists` · `GET /v1/private-lists` | MLRO, ADMIN; scope `private_lists:write` / `private_lists:read` | create, list (no entries) |
| `POST /v1/private-lists/{id}/versions` · `GET /v1/private-lists/{id}/versions` | MLRO, ADMIN; scope `private_lists:write` | upload (multipart), version history with counts |
| `GET /v1/private-lists/{id}/entries` | MLRO, ADMIN (staff only) | decrypted, audited |
| `POST /v1/screening/checks` | staff; scope `screening:write` | `{party_id}` or `{subject: {type, names…, birth_date, identifiers, countries}}`, optional `lists` (staff only); synchronous; client gets `{run_id, subject_id, status, screened_at}`; staff also get `hits[]` with explanations |
| `GET /v1/screening/runs/{id}` | staff; scope `screening:read` | client: status fields; staff: list set, versions, matcher, snapshot, hits |
| `GET /v1/screening/parties/{party_id}` | staff; scope `screening:read` | status, last screened, lists covered (codes), open finding count; staff also get findings (`TS-03` `GET /v1/customers/{id}/screening`) |
| `GET /v1/screening/matches` · `GET /v1/screening/matches/{id}` | staff | queue filters `status`, `band`, `list_code`, `list_kind`, age; detail with subject (revealed name, `VIEW_PII`), entry, origin and latest hit explanations, dispositions, `allowed_actions`, `blocked_reasons` |
| `POST /v1/screening/matches/{id}/dispositions` | staff (§9) | Idempotency-Key |
| `DELETE /v1/screening/whitelist/{id}` | MLRO | revoke |
| `GET /v1/screening/batches` | staff | progress of re-screen batches |
| `GET /v1/screening/evaluation` | staff | latest evaluation report shipped with the build (`screening/data/evaluation.json`, written by `xolotl screening eval --write`) per list kind (`FR-SC-014`, `OPS-304`) |

Errors: `LIST_FORMAT_INVALID`, `LIST_MASS_REMOVAL`, `LIST_MANDATORY`, `PRIVATE_LIST_INVALID`, `PRIVATE_LIST_EXISTS`, `DISPOSITION_NOT_ALLOWED`, `SCREENING_INDEX_UNAVAILABLE` (503 while the shared shard has never loaded), `EVENT_TYPE_NOT_DELIVERABLE`.

## 13. CLI

`lists seed`, `lists status`, `lists fetch <code>` (one immediate fetch, prod or with `--force-network`), `lists load-file <code> <path> [--accept-drop]` (offline ingestion; the dev, CI and demo path), `lists show <code> [--version]`, `screening check --tenant <slug> --party <id>`, `screening replay <run_id> --tenant <slug>`, `screening rescreen --tenant <slug> [--kind PERIODIC]`, `screening eval [--write]`.

## 14. Console increments (Spanish-first, keyed strings; DD-00 navigation)

- **Reglas › Listas**: sources with freshness badge (fresh, stale, failing with last error code), active version, counts; version history with the delta (added, changed, removed entries side by side); subscription toggles for recommended lists (ADMIN).
- **Reglas › Listas privadas**: LPB and internal lists; upload with the template download and the row-error report; version history; entry viewer behind an explicit "Revelar" (MLRO, ADMIN).
- **Coincidencias** (screening queue): findings filtered by status, band, list and age, `ALERT` before `LOW_CONFIDENCE`; badges for `suggested_disposition`.
- **Detalle de coincidencia**: subject versus entry side by side; aligned-token table (query token, entry token, kind, similarity, weight); qualifiers with outcome and adjustment; rules applied; score against the thresholds; list, version and matcher; hit history across runs; disposition timeline; action buttons from `allowed_actions` with `blocked_reasons` as tooltips; rationale required.
- **Cliente › Screening tab** on the party detail: status, last screened, lists covered, runs, findings, "Revisar ahora".
- **Consulta ad hoc**: the check form for persons and organizations with the staff result.
- **Evaluación**: the evaluation report per list kind with floors.
- No customer-facing string mentions screening, lists or suspicion (DD-00 tipping-off rule).

## 15. Requirement traceability

| Requirement | Design | Test |
|---|---|---|
| FR-SC-001 modes, recorded per run | §8.3, §10; `screening_run.mode`, `trigger_*`, `list_set_id`, `matcher_version`, `scoring_config_hash` | one test per mode; run reproducibility fields asserted |
| FR-SC-002 lists, cadence, freshness, versions with diffs | §4.1, §5.1, §6 (UN, OFAC, EU, UK, 69-B; LPB §7) | parser goldens (library); ingest and delta tests; RePET, CGU, PEP data deferred (§1) |
| FR-SC-003 polling, same-day delta, stale alert | §6.2 to §6.4, §10.2 | stale notification test; delta reaches a tenant in one poll |
| FR-SC-004 regional matching, ID and DOB tie-breakers | §4.2 | corpus floor (library) |
| FR-SC-005 bands with floors, tighten only | §8.3 bands from the effective policy; SP-02 overrides already tighten-only | a looser override is refused (SP-02 golden); band selection per list kind test |
| FR-SC-006 explainability, replay | §8.4 | explanation JSON golden; replay test |
| FR-SC-007 dispositions, four-eyes, content-keyed whitelist, suppression logged | §9; `match_disposition` trigger; `screening_whitelist`; `WHITELISTED` hits | state × role × user matrix; whitelist re-alerts on content change |
| FR-SC-008 freeze playbooks | `screening.true_match_confirmed.v1` carries the pack's `on_true_match`; SP-06 executes | event payload test; drill in SP-06 |
| FR-SC-009 PEP | qualifier only; status, expiry tasks in SP-06/SP-07 | — (deferred) |
| FR-SC-010 private lists, isolated, encrypted, full re-screen | §5.2, §7, §10.3 | isolation test; audit rows; full re-screen test |
| FR-SC-011 delisting suggestion, no auto close or unfreeze | §10.2; §9 `DELISTED` | removal and 69-B status change tests |
| FR-SC-012 counterparty cache | deferred to SP-04 (`COUNTERPARTY` subject kind reserved) | — |
| FR-SC-013 tipping-off | §11 staff-only types; §12 client views | wildcard webhook test; client view schema test |
| FR-SC-014 continuous evaluation, CI gate | §4.3; `GET /v1/screening/evaluation` | CI floor; endpoint test |
| FR-SC-015 wallets | identifiers kept by parsers; matching in SP-04 | parser golden includes an address |
| FR-SC-016, 017, 018 | not in SP-03 (§1) | — |
| NFR-SC-001 latency | §8.1, §8.3 | `perf` test (recorded, not gating); SP-11 gate |
| NFR-SC-002 freshness | §6.2 cadence | freshness fields test |
| NFR-SC-003, 004 delta and periodic throughput | §10.2, §10.4 batches | synthetic 10,000-party `perf` run; SP-11 at one million |
| NFR-SC-005 availability | MVP profile (ADR-022) | — |
| NFR-SC-006 retrieval recall | §4.3 (memory index) and §10.2 (Postgres blocking) | both measured on the corpus |
| NFR-SC-007 reproducibility | §5.1 immutable entries, §8.4 | replay test |
| DR-301..306, DR-308 | §5 (cluster ids across sources: Decision 7) | isolation harness |
| PRV-301..303, SEC-301..302 | §5.2 name index, §7, §11, §12 | harness, isolation, webhook tests |
| OPS-301..304 | `lists.poll` freshness, run latency, batch progress, evaluation report | — |

## 16. Test matrix and exit criteria

| Suite | Proves |
|---|---|
| `tests/modules/screening/lists`, `matching`, `corpus` (library) | parser goldens on trimmed real samples; malformed files refused; `diff` cases; normalization, phonetic and nickname tables; scorer explanations; bands; corpus floors |
| `tests/modules/screening/catalog/*` | seed is idempotent; lease gives one fetch across two tenants' jobs; not-modified and same-hash keep the version and refresh freshness; ingest writes version, entries (`from_seq`/`to_seq`), deltas atomically; mass-removal guard; first version starts an initial batch, later ones a delta batch; catch-up publishes once per version; stale notification once per episode; subscriptions follow the effective policy; mandatory cannot be disabled; private upload row errors, versioning and encryption at rest (no plaintext name in `private_list_entry`) |
| `tests/modules/screening/engine/*` | name index matches the library; ad hoc and party checks; list set dedup; whitelist suppression logged; finding dedup under concurrent runs; superseding on entry change; subject status; tenant B never sees tenant A's private hits; replay |
| `tests/modules/screening/review/*` | the exhaustive disposition matrix; the four-eyes trigger refuses a same-user row inserted directly in SQL; whitelist revoke |
| `tests/modules/screening/rescreen/*` | party events re-index and re-screen; purge redacts; list delta screens only added and changed entries and alerts only on them; removal and 69-B status change suggest `DELISTED`; private-list full re-screen; periodic and reindex batches resume from their cursor after a crash; Postgres blocking retrieval recall on the corpus |
| platform | staff-only event types never reach webhooks, wildcard included; endpoint creation refuses them |
| isolation harness | every new tenant table auto-discovered; the four shared tables registered |
| console | typecheck, lint, unit tests; Playwright: upload an LPB file, screen a party, open the match, see aligned tokens, escalate and confirm with a second user |

**Exit criteria** (program spec §7.3, refined): `make ci` green with the corpus floors met; two tenants exist; `xolotl lists load-file` (or a live fetch in prod) ingests UN, OFAC SDN, EU, UK and SAT 69-B and `GET /v1/lists` shows each fresh with its version; tenant A uploads an `MX_LPB` file; a party in tenant A whose name is a perturbed LPB or OFAC entry is screened via `POST /v1/screening/checks` and the staff response shows an `ALERT` hit with aligned tokens, qualifiers, list version and matcher version, while the API-client response shows only `PENDING_REVIEW`; loading the next UN fixture version (one added, one changed, one removed entry) re-screens tenant A's base and creates findings only for the added and changed entries, and suggests `DELISTED` on the removed one; tenant B sees none of tenant A's lists, runs or findings and its own parties never hit tenant A's LPB; a wildcard webhook endpoint received no `screening.match_*` event; `GET /v1/audit/verify` is intact for both tenants. Scripted as `tests/e2e/test_sp03_exit.py` over committed fixtures.

## 17. Decisions

1. **`lists` folded into `screening`** (program spec §3.3 amended): the library's parsers live in `screening/lists/` by agreement with the parallel package, and a separate `lists` module consuming them would create a cycle. Party-freedom of the catalog is kept by an import-linter contract instead of a module boundary.
2. **Forward screening in memory, reverse screening in Postgres.** The program spec puts every shard in memory; ADR-013's MVP note puts retrieval in Postgres. Lists (tens of thousands of entries) are screened in memory, which is fast and keeps private lists out of SQL; the customer side (unbounded) is blocked with `pg_trgm` and phonetic-key GIN indexes on `party_name_index`. Both use the library's normalizer and blocking plan, and both are measured on the corpus.
3. **Entries stored per content, not per version** (D18 had `UNIQUE (version_id, source_entry_key)`): `from_seq`/`to_seq` intervals avoid copying 20,000 unchanged OFAC rows per version while keeping every matched entry immutable. The delta table is as in D18.
4. **Shared raw files** go to the object store under the nil-UUID namespace, the port's tenant argument being a namespace; plan 3a checks the local adapter accepts it.
5. **Shared fetch rides on tenant jobs** with a lease on `list_source`, like `units.fetch`; no tenant-less job type is added to the kernel. With no tenants nothing is fetched, which is harmless.
6. **PEP review band** is `pep_alert − 0.15` (0.65 by default) rather than a new pack field, to leave the SP-02 pack schema and goldens untouched; a pack field can replace it later.
7. **Cross-source duplicates** (`DR-301` cluster id) are not linked in SP-03: findings stay per list entry, as an analyst must see each listing. Linking is a console nicety for later.
8. **Disposition names** follow PRD-03 and TS-03 (`FALSE_POSITIVE`, `POSSIBLE_TRUE_MATCH`, `TRUE_MATCH`) plus `DELISTED` and `SUPERSEDED`; the D18 bands are kept, with `REVIEW` called `LOW_CONFIDENCE` as in D18.
9. **The whitelist and findings key on the screening subject**, one-to-one with a party, so ad hoc subjects behave the same.
10. **A false positive needs one person; a true match needs two** (`FR-SC-007` asks four-eyes for true matches only); a second-review clearance of an escalated finding also needs a different person.
11. **Disposition rationale is encrypted** (free text may contain names); match features hold normalized tokens in plaintext like `party_name_index`.
12. **69-B is identifier-only for alerts**; a name-only resemblance can reach `LOW_CONFIDENCE` when the subject has no RFC (§4.2). `DESVIRTUADO` and `SENTENCIA_FAVORABLE` rows are kept but never hit, and a transition to them is handled as a delisting.
13. **Mass-removal guard** at 20 % per version, overridable only from the CLI.
14. **A run uses product `None`** in the effective policy (strictest merge across the tenant's products), because screening is per party, not per product.

## 18. Open questions for the product owner (each with the default this spec builds)

1. **Licensed global sanctions and PEP database** (PRD-03 v1 lists it; licence class for multi-tenant use is open). *Default:* not in SP-03; public lists plus private lists of kind `PEP`; the `list_source` catalog takes a licensed feed later as one more source and parser.
2. **Match details for API clients** (PRD-03 §13, TS-03 §11). *Default:* status only (`CLEAR`, `PENDING_REVIEW`, `TRUE_MATCH`), no per-tenant switch in SP-03.
3. **Private-list template.** The UIF distributes the LPB in its own layout. *Default:* our CSV/XLSX template (§7) that the compliance officer fills from the UIF file; a direct parser for the UIF layout once a sample is available.
4. **Periodic full re-screen cadence.** *Default:* monthly for every tenant (`SCREENING_PERIODIC_CRON`), plus every list delta and data change; a pack field (`screening.periodic_rescreen`) if a regulator or partner expects weekly.
5. **69-B name-only resemblance** for organizations without an RFC on file. *Default:* `LOW_CONFIDENCE` at most, never `ALERT` (§4.2).
6. **`OFAC_CONS` (non-SDN consolidated)**. *Default:* not ingested (not in either pack); one catalog row and the same parser when wanted.
7. **Domestic PEPs as a high-risk floor** (PRD-03 §13). *Default:* out of SP-03; decided with risk scoring in SP-07.

## 19. Plan split

| Plan | Contents | Depends on | Can start before 2c finishes? |
|---|---|---|---|
| **Screening library** (parallel package, its own plan and PRs; not re-planned here) | §4: parsers, `ListEntry`, `diff`, normalization v2, phonetic key, nicknames, IDF, `MemoryIndex`, `score`, `band_for`, evaluation corpus and floors | none | **yes** (starting now) |
| **3a — List catalog and private lists** | migration 0009; `sources.yaml`, `lists seed`; fetcher port and HTTP adapter; `lists.poll` (lease, ingest, guard, catch-up, staleness, subscriptions); `lists load-file/fetch/status/show`; private lists upload, validation, encryption, versions; lists and private-list API; Listas and Listas privadas pages | library `ListEntry`, `parse`, `diff` (UN first) | **yes** (party-free by contract) |
| **3b — Screening engine and review** | migration 0010; index holder; `party_name_index`; subjects and queries; `screen()`; runs, list sets, hits, findings, status; dispositions with the four-eyes trigger, whitelist; staff-only event types and the webhook guard; checks, runs, matches, dispositions, parties API; party event subscribers (index and data-change screen); replay; match queue, match detail, party screening tab, ad hoc pages | library matcher, 3a, party core (2c) | the index holder and ad hoc checks could, but the plan as a whole waits for 2c, which is merged except its last console task |
| **3c — Re-screening, evaluation and exit** | list-delta job with Postgres blocking, delisting suggestions, private-list full re-screen, periodic and reindex batches, purge redaction, batches API, `screening eval`/`rescreen` CLI and the evaluation endpoint and page, perf tests, `tests/e2e/test_sp03_exit.py` | 3b | no |

Estimate: library about 1.5 weeks in parallel; 3a 1.5, 3b 1.5, 3c 1 week on the main line, which fits the program's 4 weeks with the library off the critical path.

## 20. Cut list (in order, if SP-03 runs long)

Evaluation console page (the endpoint and CLI stay); private-list XLSX (CSV stays); run detail page (the match detail stays); whitelist revoke endpoint (MLRO can escalate a fresh finding instead); the `perf` tests (SP-11 measures).
