# Screening library (SP-03 head start) — Implementation Plan

> Handoff prompt: `docs/superpowers/handoffs/screening-library.md`. Notion task "Screening library: list parsers and name matcher (SP-03 head start)". Branches `feat/screening-lib-<task>`, one PR per task, merge commits only.

**Goal:** The pure, database-free core of screening that SP-03 builds on: parsers for `UN_CONSOLIDATED`, `OFAC_SDN`, `EU_FSF`, `UK_FCDO` and `MX_SAT_69B` into one `ListEntry` shape, the version `diff`, name normalization v2 with a phonetic key, nickname table and IDF weights, an in-memory candidate index, an explaining scorer with bands, and an evaluation corpus whose floors fail CI. No tables, migrations, routes, jobs or network access.

**Spec:** `docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md` §4 fixes the public interface (`ListEntry` and its value types, `ParsedList`, `parse`, `ListFormatError`, `ListDelta`, `diff`, `MATCHER_VERSION`, `normalize_name`, `phonetic_key`, `NameQuery`, `blocking_plan`, `IdfTable`, `MemoryIndex`, `score`, `ScoringConfig`, `MatchExplanation`, `band_for`, the corpus floors). This plan follows it name for name; any change is agreed in the PR that makes it and reflected in the spec.

**Architecture:** `src/xolotl/modules/screening/` with `lists/` (types, one module per source, `diff`), `matching/` (normalize, phonetic, nicknames, idf, index, scorer, bands, evaluation) and `data/` (YAML and CSV tables loaded once). Both subpackages import only the standard library, their third-party string and parsing libraries, `xolotl.platform.errors` and `xolotl.platform.enums`; `matching` may import `lists`, never the reverse. Two import-linter contracts enforce it (T1).

**Tech stack:** the repository's stack plus `rapidfuzz` (runtime, Jaro-Winkler; T4). XML is read with `defusedxml.ElementTree.iterparse`, already a dependency. Dev: `hypothesis` if not already present.

**Requirements traced:** FR-SC-002 (lists, versions with diffs: parsers and `diff`), FR-SC-004 (regional matching, identifier and birth-date tie-breakers), FR-SC-005 (bands; thresholds come from the policy), FR-SC-006 (typed explanation), FR-SC-014 (evaluation and CI gate), FR-SC-015 (wallet addresses kept as identifiers), NFR-SC-006 (retrieval recall of the memory index), NFR-SC-007 (deterministic `canonical_json`, `content_hash`, `fingerprint`, `config_hash`). TS-03 §6 (69-B identifier-only) and §10 (corpus size).

## Decisions (recorded here and in each PR)

1. **XML through `defusedxml.ElementTree.iterparse`**, not `lxml`: streaming with each record element cleared after use keeps memory bounded on the 100+ MB OFAC file, it refuses entity expansion and external DTDs, it is typed in the standard library, and it is already a runtime dependency. If a source's file needs XPath or schema validation, the PR that meets it raises `lxml`.
2. **Sentinel is available** (`~/code/sentinel`, MIT). Ported: the particle, honorific and legal-suffix sets, the OFAC CSV parser (remarks regexes for DOB, nationality and identifiers; Windows-1252; `-0-` nulls), the OFAC country-name table, and the "an identifier match is definitive" rule. Not ported: its pydantic models, its 0–100 token-set score and its thresholds; the scorer is rebuilt on token alignment with IDF weights and re-scored on the corpus (D22).
3. **`OFAC_SDN` dispatch sniffs the format.** `parse("OFAC_SDN", data)` sniffs the first bytes: `<` (after an optional BOM and whitespace) selects the enhanced XML parser (`ofac-sls-xml/1`); the zip magic `PK` selects the legacy CSV parser (`ofac-csv/1`), which reads `sdn.csv` and, when present, `alt.csv` from the archive; anything else is a `ListFormatError`. Every other code dispatches on the code only.
4. **Canonical JSON.** Keys sorted, no whitespace, UTF-8, tuples as arrays, enums as their values, `date` as ISO `YYYY-MM-DD`, `PartialDate` as `{"day","month","year"}` with nulls kept, every field present including defaults. `from_json` is its inverse; a hypothesis round-trip test fixes it.
5. **Country names to alpha-2** through `data/countries.csv` (ISO name, the spellings the five sources use, and historic names such as Burma); an unmapped country name is dropped from `nationalities` with a standard-library `logging` warning naming the spelling (refusing the whole list over one new spelling would stop ingestion, which is worse than a missing qualifier), and a test asserts every country name in every fixture maps, so the table grows with the sources. Sources that publish ISO codes (EU, UK, OFAC enhanced) use them directly.
6. **Duplicates and malformed files.** A duplicate `entry_key`, a missing mandatory element (name, key), an unparseable date or a truncated file raises `ListFormatError` naming the source, parser version and position (element index or CSV line), never the record's contents. A parser never returns a partial list.
7. **Parser versions:** `un-xml/1`, `ofac-sls-xml/1`, `ofac-csv/1`, `eu-fsf-xml/1`, `uk-sl-xml/1`, `sat-69b-csv/1`.
8. **Phonetic key is hand-rolled**, not a library: published phonetic libraries are English-only (Metaphone, Soundex) and break Spanish equivalences (b/v, ll/y, silent h, c/s/z before e/i, j/g before e/i, qu/k/c, x as /s/ or /j/ in Mexican names). The key is a small ordered rule table over the folded token, documented next to the code with worked examples; it is stable across patch releases of `MATCHER_VERSION`.
9. **The perturbation generator and `evaluate` live in `src`** (`matching/evaluation.py`) so `xolotl screening eval` (plan 3c) reuses them; seeds, hard negatives, generated-pair snapshot hash and `floors.yaml` live under `tests/modules/screening/corpus/`. Generation is seeded and deterministic; the test asserts the pair count and a hash of the generated set so a generator change is visible in review.
10. **Hard negatives are synthetic**: combinations of the most frequent Mexican given names and surnames (`data/surnames_mx.csv`, `data/given_names_mx.csv`, from public INEGI/RENAPO frequency tables, sources recorded in the file header) checked not to equal any fixture entry. No real customer data anywhere.
11. **`MATCHER_VERSION` starts at `"2.0.0"`** (sentinel's matcher being 1.x). Any change to normalization, phonetic rules, tables or scoring parameters bumps it in the same PR.

## Tasks

### T1 — Package, `ListEntry`, `diff` and the UN parser (`feat/screening-lib-un`)
- `src/xolotl/modules/screening/{__init__,lists/__init__}.py`; `lists/types.py` with `ListCode`, `EntityType`, `NameQuality`, `IdType`, `PartialDate`, `EntryName`, `EntryIdentifier`, `EntryAddress`, `ListEntry` (`canonical_json`, `content_hash`, `from_json`), `ParsedList`; `lists/errors.py` with `ListFormatError(ValidationFailedError)` code `LIST_FORMAT_INVALID`; `lists/parse.py` (`parse` dispatcher); `lists/diff.py` (`ListDelta`, `diff`).
- `lists/un.py`: streaming parser of the Security Council consolidated XML (individuals and entities, aliases with quality "Good"/"Low" → `STRONG`/`WEAK`, dates with `TYPE_OF_DATE` precision and ranges, places of birth, nationalities by name, documents → identifiers, `REFERENCE_NUMBER` into `programs`, `LISTED_ON`, `DATAID` as `entry_key`, `dateGenerated` as `published_at`).
- `data/countries.csv` and its loader.
- `.importlinter`: two contracts (lists/matching purity; `lists` never imports `matching`).
- Fixture: trimmed UN XML (about 30 entries, individuals and entities, Arabic-script and low-quality aliases, a range date) with `README.md` (URL, retrieval date, what was trimmed).
- Tests: parser golden (canonical JSON of the parsed fixture, diff printed on failure); malformed files refused (truncated XML, duplicate `DATAID`, missing name); `diff` cases (added, changed, removed, unchanged, screenable→non-screenable is `CHANGED`); canonical JSON round-trip and hash stability (hypothesis).

### T2 — OFAC and EU parsers (`feat/screening-lib-ofac-eu`)
- `lists/ofac_xml.py` (Sanctions List Service SDN enhanced XML: reference values resolved once from the file's reference section, then entities streamed; names with script and quality, features → birth dates with precision, places, nationalities, gender; identity documents and digital-currency addresses → `IdType`; programs; `uid` as `entry_key`; vessels with IMO and aircraft kept with their entity type).
- `lists/ofac_csv.py` (sentinel port; fallback and fixtures).
- `lists/eu.py` (FSF consolidated XML 1.1: `sanctionEntity`, `nameAlias` with `strong`, `birthdate` with `circa`/year-only, `citizenship`, `identification`, `regulation` programmes, `logicalId` as key).
- Fixtures and READMEs for each; the OFAC fixture includes a digital-currency address and a vessel (FR-SC-015).
- Tests: goldens, malformed-file refusals, the CSV and XML parsers agreeing on names, keys and birth years for the same entries.

### T3 — UK and SAT 69-B parsers (`feat/screening-lib-uk-sat`)
- `lists/uk.py`: FCDO UK Sanctions List XML (the single UK list since 28 January 2026; the OFSI consolidated list is closed). Unique ID as `entry_key`; names with non-Latin script; regime as programme. Field mapping checked against the FCDO "Format guide for the UK Sanctions List" and recorded in the README.
- `lists/sat69b.py`: `Listado_Completo_69-B.csv`, Windows-1252, preamble skipped up to the header row; RFC key and identifier; entity type from RFC length (12 organization, 13 person; other lengths refused); `status` from "Situación del contribuyente" (`PRESUNTO`, `DEFINITIVO`, `DESVIRTUADO`, `SENTENCIA_FAVORABLE`), `screenable=False` for the last two; publication dates into `listed_on`.
- Fixtures and READMEs (the 69-B sample covers all four statuses).
- Tests: goldens, refusals (unknown status, bad RFC length, header missing, wrong encoding producing replacement characters).

### T4 — Normalization v2, phonetic key and nicknames (`feat/screening-lib-normalize`)
- `matching/normalize.py`: `MATCHER_VERSION`, `TokenRole`, `Token`, `NormalizedName`, `normalize_name` (NFKC, case fold, diacritic fold with ñ→n, punctuation and whitespace, abbreviation dots dropped first so `S.A. de C.V.` stays recognisable, particles and honorifics tagged, legal forms recognised as multi-token sequences and canonicalised, given and surname roles from the structured fields or OFAC-style `SURNAME, Given`, non-Latin scripts transliterated only when a deterministic table exists, otherwise kept and flagged `UNKNOWN`).
- `matching/phonetic.py`: `phonetic_key` (Decision 8).
- `matching/nicknames.py` with `data/nicknames_es.yaml` (José/Pepe, Francisco/Paco/Pancho, Guadalupe/Lupe, Jesús/Chucho, Ignacio/Nacho, Concepción/Conchita/Concha, Refugio/Cuca, Enrique/Quique, Manuel/Manolo, Rosario/Chayo, Alfonso/Poncho, Eduardo/Lalo, Gonzalo/Chalo, Dolores/Lola, Mercedes/Meche, Roberto/Beto, Alberto/Beto, and English equivalents such as William/Bill), `data/particles.yaml`, `data/legal_forms.yaml`.
- Tests: table-driven normalization cases per rule; phonetic equivalence classes (both directions) and known non-equivalences; nickname symmetry; data files load once.

### T5 — IDF, index, scorer, explanations and bands (`feat/screening-lib-matcher`)
- `matching/query.py` (`QueryIdentifier`, `NameQuery`, `fingerprint`), `matching/idf.py` (`IdfTable.build` over list names plus `data/surnames_mx.csv`; unseen tokens get the maximum weight), `matching/index.py` (`blocking_plan`, `IndexedEntry`, `Candidate`, `MemoryIndex`: identifier map, phonetic-key postings, rare-token and common-pair blocking, IDF-overlap ranking to `top_k`), `matching/scorer.py` (`ScoringConfig`, `TokenAlignment`, `QualifierResult`, `RuleApplied`, `MatchExplanation.to_json`, `MatchScore`, `score`), `matching/bands.py` (`Band`, `BandThresholds`, `band_for`).
- Scoring per spec §4.2: best alias pair; token alignment by exact, nickname, phonetic, initial, then Jaro-Winkler; IDF weights with surnames above given names; particles and legal forms weightless; a dropped second surname tolerated; qualifiers additive within a bounded total; `ID_MATCH_FLOOR`, `ID_CONFLICT_CAP`, `ENTITY_TYPE_MISMATCH`, `IDENTIFIER_ONLY_LIST` (69-B: RFC equality 1.0, no RFC capped just below `alert`, different RFC capped at `id_conflict_cap`); scores quantized to 4 places.
- Tests: explanation goldens for representative pairs (exact, dropped maternal surname, swapped order, nickname, initials, organization with legal form, identifier floor, identifier conflict, entity mismatch, 69-B each branch); `band_for` table including the `AUTO_DISCARDED` rule; index returns identifier hits beyond `top_k`; a non-gating `perf` test at 50,000 synthetic entries records p95 latency.

### T6 — Evaluation corpus and the CI floor (`feat/screening-lib-corpus`)
- `matching/evaluation.py`: deterministic perturbation generator (dropped maternal surname, order swap, accent loss, letter substitutions, nicknames, initials, particle changes, typos, truncation at 60 characters), `EvaluationReport` and `evaluate(corpus)` reporting per list kind recall at `LOW_CONFIDENCE`-or-above, precision at `ALERT`, retrieval recall at the production `top_k`, and alerts per 1,000 hard negatives.
- `tests/modules/screening/corpus/`: seeds from the fixtures, at least 20,000 generated pairs, at least 500 hand-labelled hard negatives, `floors.yaml` (sanctions recall ≥ 0.98, retrieval recall ≥ 0.995, precision at `ALERT` from the first honest measurement, recorded with the measured value in the PR).
- Tests: the floor test that fails CI below any floor; generator determinism.

## Order and parallelism
T1 first (every other task imports its types). T2 and T3 depend on T1 only; T4 depends on T1 only; T5 on T4; T6 on T2, T3 and T5. SP-03 plan 3a can start against T1 alone.

## Gates
Each PR: `make lint types vulture importlint`, `uv run pytest tests/modules/screening --no-cov -q`, then `make ci` before marking ready. Coverage over `src` stays at or above 90%.
