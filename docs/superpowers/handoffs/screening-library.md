# Prompt: screening library — list parsers and name matcher (SP-03 head start)

Paste everything below the line into your AI coding agent.

---

You are working on **xolotl**, a multi-tenant KYC/AML compliance platform for Latin America (Mexico first; pilot March 2027). Repository: `git@github.com:OmarIbannez/xolotl.git`, default branch `main`. Stack: Python 3.13 with uv, FastAPI, SQLAlchemy 2.1, Alembic, Postgres 17; a React 19 + Vite console in `console/`.

## Your package

Build the **pure, database-free core of screening** that SP-03 (lists and screening) is built on. It parses sanctions and watch lists into one normalized record shape, and it scores how well a person or company name matches a list entry, explaining why. It has **no database tables, no migrations, no HTTP routes and no jobs**. The SP-03 design spec (`docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md`, on `main`) adds list versions in Postgres, runs, matches, dispositions, re-screening and the console on top of your library.

**Start by reading the SP-03 spec** if it is on `main` or in an open PR. Where it fixes a name, type or behaviour for these parts, follow it. Where it does not exist yet, follow the program spec below, keep your public interface small, and note your choices so the SP-03 plans can adopt them.

Scope:
1. **List parsers** for the five public lists, each from the official published file format into one `ListEntry` record shape (entry id, list code, entity type PERSON/ORGANIZATION/VESSEL/OTHER, primary name, aliases with their quality, dates and places of birth, nationalities, identifiers, addresses, programmes/reasons, listing and delisting dates, the raw source reference):
   - `UN_CONSOLIDATED`: UN Security Council consolidated list (XML).
   - `OFAC_SDN`: US Treasury OFAC SDN (the advanced XML or the standard XML/CSV; pick one and say why).
   - `EU_FSF`: EU consolidated financial sanctions file (XML).
   - `UK_FCDO`: UK sanctions list (the format the UK publishes now; check which one OFSI/FCDO currently publish, since it changed in 2025–2026).
   - `MX_SAT_69B`: SAT Article 69-B list of taxpayers with presumed simulated operations (CSV), with its statuses (presunto, definitivo, desvirtuado, sentencia favorable).
   - A **delta function**: given two parsed versions of the same list, return added, removed and changed entries with stable entry keys. SP-03's delta re-screen depends on it.
   - Parsers must be streaming or bounded in memory for the large files, tolerate the known quirks of each source, and refuse a malformed file with a clear error rather than half-parse it.
2. **Name normalization v2**: Unicode NFKC, case folding, diacritic folding, punctuation and whitespace, Spanish particles (de, del, la, los, y), company legal-form suffixes (S.A. de C.V., S.A.P.I., S. de R.L., LLC, Ltd, GmbH…), script handling, and token order. Add a **phonetic key** suitable for Spanish and English names, a **nickname/variant table** (for example José/Pepe, Francisco/Paco/Pancho, Guadalupe/Lupe), and **IDF token weights** computed from the list corpus.
3. **Matcher**: score a query name (plus optional birth date, nationality and entity type) against candidates, with the score bands the spec defines. Return an **explanation** (which tokens matched, how: exact, phonetic, nickname, fuzzy, and which attributes agreed or conflicted). Add an **in-memory candidate index** (blocking by phonetic key and rare tokens) so scoring one name against all lists stays fast. Program-spec decision D22 says to port the algorithms from the owner's earlier project "sentinel" (normalize and matcher) but re-score them; if you do not have access to sentinel, build from the spec and say so.
4. **Evaluation corpus and CI floor**: a labelled corpus of query/entry pairs (true matches, near misses, common Mexican names that must not match) under `tests/modules/screening/corpus/`, an evaluation script that reports precision and recall per band, and a test that fails CI if recall on true matches or precision at the alert band drops below the floor you set and record.

Out of scope: storing lists or versions in Postgres, downloading files on a schedule, private LPB list upload, screening runs, matches, dispositions, whitelist, re-screen jobs and console pages. Those are SP-03 proper. Your parsers take a path or a byte stream; they never fetch from the network.

## Read first

- `docs/superpowers/specs/2026-10-18-program-architecture-design.md`: §3 architecture and the module table (§3.3), **D7** (readable names for matching), **D18** (lists and screening data: the `list_code` registry and list versions), **D21** (mandatory lists `MX_LPB`, `UN_CONSOLIDATED`; recommended `OFAC_SDN`, `MX_SAT_69B`, `EU_FSF`, `UK_FCDO`), **D22** (the sentinel reuse policy), §7 SP-03 row and exit criteria, §8 testing.
- The SP-03 spec described above, if present.
- `src/xolotl/platform/` and `src/xolotl/modules/party/` for conventions: errors are `XolotlError` subclasses with stable codes, enums are UPPERCASE text, Google-style docstrings, typed everything.
- `docs/superpowers/execution/sp02/agreement.md`: the gates CI enforces and the commit and PR conventions. Ignore its parts about Notion page ids and worktrees under the owner's home; the rest applies to you.
- Notion (if your agent has access): the Xolotl hub has the screening PRD and tech spec (capability code **SC**, requirement IDs `FR-SC-*`, `NFR-SC-*`) and the related ADRs. Trace the requirements you implement by ID in docstrings and PR bodies.

## Where the code goes

- Library: `src/xolotl/modules/screening/` with subpackages such as `lists/` (parsers, `ListEntry`, delta) and `matching/` (normalize, phonetic, nicknames, idf, index, scorer, explain). Create `src/xolotl/modules/screening/__init__.py`. It may import only `xolotl.platform.errors` and `xolotl.platform.enums`; never `sqlalchemy`, `fastapi`, `xolotl.modules.party`, `xolotl.app` or `xolotl.cli`. The SP-03 spec (§4) fixes this rule and its public interface (`ListEntry`, `parse`, `diff`, `normalize_name`, `phonetic_key`, `MemoryIndex`, `score`, `band_for` and the corpus floors); follow it exactly. If you need party types, define a small input type of your own instead (for example `NameQuery`).
- Fixtures: small, **trimmed real samples** of each official file (a few dozen entries, with their source URL and retrieval date in a README beside them) under `tests/modules/screening/fixtures/`. Do not commit full list files. Never commit real personal data beyond what the public lists themselves publish.
- Nickname and particle tables are data files (YAML or CSV) under `src/xolotl/modules/screening/data/`, loaded once.
- New dependencies (for example `lxml`, `rapidfuzz`, a phonetic library) go in `pyproject.toml` with `uv add`; prefer few and well-maintained. No network access at test time.

## Gates (CI runs `make ci`; it must be green)

- ruff with every rule (line length 88), Google-style docstrings with `Returns:`/`Raises:`/`Yields:`, no `print`, `cast("T", value)` with a string type.
- basedpyright in "all" mode and ty: no `Any` and no unknown types in `src`.
- vulture: no unused code in `src`.
- import-linter contracts.
- pytest with **90% coverage over `src`**. Docker must be running because the suite starts Postgres through testcontainers.
- Partial runs: `uv run pytest tests/modules/screening --no-cov -q`. Fix lint with `uv run ruff format src tests && uv run ruff check --fix src tests`. Before each commit, run `make lint types vulture importlint` and your tests; run `make ci` once before marking a PR ready.

## How to deliver

1. **Plan first.** Write a short implementation plan at `docs/superpowers/plans/<date>-screening-library.md`, split into 4 to 6 tasks of a few days each (for example: `ListEntry`, the UN parser and the delta function; OFAC and EU parsers; UK and SAT 69-B parsers; normalization, phonetic key and nicknames; IDF, index and scorer with explanations; evaluation corpus and the CI floor). Open it as its own docs PR. If the SP-03 spec is merged by then, align task names with it.
2. **One PR per task**, on branches `feat/screening-lib-<task>` cut from up-to-date `main`. Use test-driven development: write the failing test first, see it fail, then implement. Use conventional commits (`feat(screening): …`, `test(screening): …`) with a plain-prose body. Each PR body has a Summary (what, why, requirement IDs, decisions you took) and a Test plan (commands and what they prove).
3. **Merge with a merge commit** (`gh pr merge --merge --delete-branch`) once CI is green and a reviewer, human or a second agent, has read it. Never squash, rebase, force-push or rewrite history on `main`.
4. **Notion:** move the package task to In Progress when you start, add each PR link to it, and set it Done with a closing note when the last PR merges.

## Rules

- MVP footprint: one application container plus Postgres; no OpenSearch, Elasticsearch, Redis or other search service. The in-memory index is the design.
- Build the smallest thing that matches correctly and explains itself; no speculative abstractions.
- Matching quality is a compliance property: a missed true match is worse than an extra alert. Make the evaluation corpus honest, and never tune the floor down to make CI pass.
- Decide small ambiguities yourself and record them in the PR. Raise anything that changes the architecture (new tables, new services, new infrastructure) with the product owner, Omar Ibañez, before building it.
