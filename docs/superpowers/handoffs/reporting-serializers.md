# Prompt: reporting serializers library (SP-05 head start)

Paste everything below the line into your AI coding agent.

---

You are working on **xolotl**, a multi-tenant KYC/AML compliance platform for Latin America (Mexico first; pilot March 2027). Repository: `git@github.com:OmarIbannez/xolotl.git`, default branch `main`. Stack: Python 3.13 with uv, FastAPI, SQLAlchemy 2.1, Alembic, Postgres 17; a React 19 + Vite console in `console/`.

## Your package

Build the **pure serializer library** that SP-05 (regulatory reporting) will use to produce Mexico's regulatory files. It turns already-prepared report data into bytes and validates them. It has **no database tables, no migrations, no HTTP routes and no jobs**. Those come in SP-05, which is planned later; your library is its head start and must not wait for it.

The regulatory dates make this urgent: Acuerdo 115 is in force on 2026-11-30, and the new aviso XML becomes mandatory on 2027-06-01.

Scope:
1. **Formato 41 `DELIMITED` serializer** (CNBV/UIF layout) for the four report kinds `MX_F41_ROR` (relevant operations, quarterly), `MX_F41_ROI` (unusual), `MX_F41_ROI24` (24-hour, with a legend parameter: `"Lista de Personas Bloqueadas"` for LPB hits, `"Reporte de 24 horas"` otherwise) and `MX_F41_ROIP` (internal worrying operations), **including the empty report** (reporte vacío) for a period with no items.
   - v1 output is ASCII: fold Ñ to N and strip diacritics, with `encoding_confidence: UNVERIFIED` recorded until a SITI QA upload confirms it.
   - Field order, widths, separators, padding, date formats and code-table values come from the official layout. Model the layout as **data** (one definition per report kind and version), not as code branches, because the registry in D14 loads definitions from `report-defs/`.
   - Corrections are `UNSUPPORTED` for Formato 41 (no verified mechanism); do not invent one.
2. **LFPIORPI aviso `XML_XSD` serializer** for `MX_AVISO_AVI` (virtual-asset activity, fr. XVI), covering the **current** schema and the **2027** schema, plus the **informe en ceros** (zero report) and the **modificatorio** (correction of a previously accepted aviso). Validate every output against the official XSD and fail loudly on an invalid document.
3. **Normalizers** shared by both: names, RFC/CURP, addresses, amounts (Decimal only, never float; see ADR on monetary units), dates and times in `America/Mexico_City`, and the character rules.
4. **Golden files**: for each report kind, fixture input data and the expected output file, **byte-identical**. Add **property tests** (hypothesis) for the character rules: the output never contains a non-permitted character, never exceeds field widths, and is stable for the same input.

Out of scope: report runs, approvals, artifacts storage, filing channels, receipts, deadlines, the definition registry tables, the STR-draft API and console pages. Those are SP-05 proper.

## Read first

- `docs/superpowers/specs/2026-10-18-program-architecture-design.md`: §3 architecture and the module table (§3.3), **D14** (one report registry: definition fields, code grammar `MX_F41_ROR`…, serializers `DELIMITED` and `XML_XSD`, encoding, size limits), **D21** (Mexico content: thresholds, legends, ITF and AV packs), §8 testing (golden and property tests), §9 risks (Formato 41 and aviso gaps).
- `docs/superpowers/specs/2026-10-19-sp02-packs-units-calendar-party-design.md` and the code in `src/xolotl/platform/` for conventions: errors are `XolotlError` subclasses with stable codes, enums are UPPERCASE text, monetary amounts are Decimal, Google-style docstrings.
- `docs/superpowers/execution/sp02/agreement.md`: the gates CI enforces and the commit and PR conventions. Ignore its parts about Notion page ids and worktrees under the owner's home; the rest applies to you.
- Notion (if your agent has access): the Xolotl hub has PRD and tech-spec pages for regulatory reporting (capability code **RR**, requirement IDs `FR-RR-*`, `NFR-RR-*`) and design doc DD-03. Trace the requirements you implement by ID in docstrings and the PR body.
- The official sources: the CNBV/UIF Formato 41 layout and instructions, and the SAT/UIF LFPIORPI aviso XSDs (current and 2027) and their user guides. Put the public spec documents you rely on under `report-defs/sources/` with their URL and retrieval date, and record anything you could not confirm as `UNVERIFIED` rather than guessing.

## Where the code goes

- Library: `src/xolotl/modules/reporting/serializers/` (create `src/xolotl/modules/reporting/__init__.py`). It may import `xolotl.platform` only. It must not import `xolotl.modules.party` or anything else in `modules`, nor `xolotl.app`/`xolotl.cli` (import-linter enforces this).
- Layout definitions and XSDs: `report-defs/` at the repository root (for example `report-defs/MX_F41_ROR/v1.yaml`, `report-defs/MX_AVISO_AVI/xsd/…`). If the Docker image must ship them later, SP-05 adds that; do not edit the Dockerfile.
- Tests and goldens: `tests/modules/reporting/serializers/` with goldens under `tests/modules/reporting/serializers/golden/`.
- Input types: plain frozen dataclasses or Pydantic models defined in your package (for example `ReportHeader`, `ReportLine`, `AvisoDocument`). SP-05 maps its database rows onto them, so keep them small, typed and documented.
- New dependencies (for example `lxml` for XSD validation) go in `pyproject.toml` with `uv add`. Keep the lockfile committed. No network access at test time.

## Gates (CI runs `make ci`; it must be green)

- ruff with every rule (line length 88), Google-style docstrings with `Returns:`/`Raises:`/`Yields:`, no `print`, `cast("T", value)` with a string type.
- basedpyright in "all" mode and ty: no `Any` and no unknown types in `src`.
- vulture: no unused code in `src`.
- import-linter contracts.
- pytest with **90% coverage over `src`**. Docker must be running because the suite starts Postgres through testcontainers, even though your tests do not need it.
- Partial runs: `uv run pytest tests/modules/reporting --no-cov -q`. Fix lint with `uv run ruff format src tests && uv run ruff check --fix src tests`. Before each commit, run `make lint types vulture importlint` and your tests; run `make ci` once before marking a PR ready.

## How to deliver

1. **Plan first.** Write a short implementation plan at `docs/superpowers/plans/<date>-reporting-serializers.md`, split into 3 to 5 tasks of a few days each (for example: normalizers and character rules; Formato 41 definitions and serializer with ROR goldens; ROI/ROI24/ROIP and empty reports; aviso XML current schema with XSD validation; 2027 schema, informe en ceros and modificatorio). Open it as its own docs PR.
2. **One PR per task**, on branches `feat/reporting-serializers-<task>` cut from up-to-date `main`. Use test-driven development: write the golden or the failing test first, see it fail, then implement. Use conventional commits (`feat(reporting): …`, `test(reporting): …`) with a plain-prose body. Each PR body has a Summary (what, why, requirement IDs, decisions you took), a Test plan (commands and what they prove) and a list of anything left `UNVERIFIED`.
3. **Merge with a merge commit** (`gh pr merge --merge --delete-branch`) once CI is green and a reviewer, human or a second agent, has read it. Never squash, rebase, force-push or rewrite history on `main`.
4. **Notion:** move the package task to In Progress when you start, add each PR link to it, and set it Done with a closing note when the last PR merges.

## Rules

- MVP footprint: one application container plus Postgres; no S3, Kafka, Redis, Temporal or KMS.
- Build the smallest thing that produces correct, validated files; no speculative abstractions.
- Never put real personal data in fixtures; generate synthetic RFCs/CURPs that pass the validators in `src/xolotl/modules/party/validators` (if present on `main`) or the rules in the spec.
- Decide small ambiguities yourself and record them in the PR. Raise anything that changes the architecture (new tables, new services, new infrastructure) with the product owner, Omar Ibañez, before building it.
