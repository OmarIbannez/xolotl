# Reporting serializers library (SP-05 head start) — Implementation Plan

> Handoff prompt: `docs/superpowers/handoffs/reporting-serializers.md`. Notion task XOL-77 "Reporting serializers library (SP-05 head start)". Branches `feat/reporting-serializers-<task>`, one PR per task, merge commits only.

**Goal:** A pure library that turns already-prepared Mexican report data into the exact bytes the regulator expects and validates them: Formato 41 `DELIMITED` files for `MX_F41_ROR`, `MX_F41_ROI`, `MX_F41_ROI24` and `MX_F41_ROIP` (with the reporte vacío), and LFPIORPI aviso `XML_XSD` files for `MX_AVISO_AVI` (avisos, informe en ceros, modificatorio) validated against the official XSD. No tables, migrations, routes or jobs; SP-05 maps its rows onto this library's input types.

**Architecture:** `src/xolotl/modules/reporting/serializers/` imports `xolotl.platform` only (errors, clock-free). Layouts are data under `report-defs/` (one YAML per report code and version, plus vendored XSDs with SHA-256 and source URL); the code is two serializer families (`DELIMITED`, `XML_XSD`), a closed normalizer catalogue and a small loader. Every serializer is a pure function `(definition, input) -> SerializedReport(parts: tuple[bytes, ...], warnings, encoding_confidence)`, deterministic for the same input.

**Tech stack:** the repository's stack plus `lxml` (XSD 1.0 validation; runtime), `types-lxml` and `hypothesis` (dev). No network at test time: XSDs and source documents are committed.

**Requirements traced:** FR-RR-002 (serializers), FR-RR-003 (normalizers), FR-RR-004 (layout and schema validation layer only), FR-RR-005 (code-table membership for the Formato 41 catalogues printed in the layout), FR-RR-006 (zero reports), FR-RR-009 (aviso modificatorio; Formato 41 `UNSUPPORTED`), FR-RR-018 (Mexico definitions), FR-RR-019 (sources and schema hashes recorded), NFR-RR-004 (byte-exact reproducibility), NFR-RR-005 (two aviso schema versions, the 2027 one gated).

## Sources (committed under `report-defs/sources/`, retrieved 2026-10-19)

| Document | URL | Status |
|---|---|---|
| Formato 41 resolution, compiled (DOF 14-12-2004, reformed to 01-08-2013), Anexos 1–4 | https://www.gob.mx/cms/uploads/attachment/file/618089/Formato_41_Campos_Compilado.pdf (the UIF URL in the research notes now 404s) | VERIFIED |
| `avi.xsd` (current aviso schema, fr. XVI) | https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/xsd/avi.xsd | VERIFIED (downloaded; vendored with its hash in T4, its first user) |
| `ejemplo_avi.xml` | https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/ejemplosxml/ejemplo_avi.xml | VERIFIED |
| Modificatorio instructions | https://www.pld.hacienda.gob.mx/work/models/PLD/documentos/inst_modificatorio.pdf | VERIFIED |
| `avi2.xsd`, `ejemplo_avi2.xml`, `avi2.zip` (2027 schema) | linked from https://sppld.sat.gob.mx/pld/interiores/activos.html | **404 on 2026-10-19** (gap) |

## Decisions (recorded here and in each PR)

1. **Definitions live at `report-defs/<CODE>/v<N>.yaml`** (the handoff's layout), not the `report-defs/MX/F41_ROR.yaml` sketch in program spec §3.2; code and version stay separate fields as D14 says.
2. **Formato 41 record shape.** 41 columns joined by `;`, no trailing separator, records terminated by CRLF, the file ends with CRLF. The layout text ("se terminará con el (;)") is ambiguous on a trailing separator and silent on line endings. Its only examples are fragments taken from the middle of a record (`;JUAN JOSE;MARTINEZ;HERNANDEZ;;;19701024;` opens and closes on a separator), so they settle neither reading. Both are layout parameters (`trailing_separator`, `line_ending`) marked `UNVERIFIED` until a SITI QA upload; switching is a one-line data change plus regenerated goldens.
3. **Formato 41 characters (v1).** Upper case; diacritics stripped and Ñ folded to N; `;`, control characters and anything outside printable ASCII replaced by a space; runs of spaces collapsed and trimmed; fields longer than their width truncated with a `FIELD_TRUNCATED` warning (the layout's own rule). `encoding_confidence: UNVERIFIED` on every Formato 41 output (D14). An RFC containing Ñ (allowed in the RFC alphabet) is therefore folded to N in Formato 41 output, which changes the identifier; this follows D14's ASCII rule and is part of what the SITI QA upload must confirm. Aviso output keeps the Ñ.
4. **Formato 41 widths** are the layout's, including where the research summary differed (column 19 apellido paterno is 30, not 60). Columns 40 and 41 are variable length.
5. **Related rows (ROI, ROI24, ROIP).** Row `00` is the main operation; rows `01..n` repeat columns 1–33 and 40–41 and vary 34–39, as the Anexo 1 preamble says (the column 34 note that only 1–5 need repeating is the looser reading; repeating is accepted by both). One folio per operation, shared by its related rows. ROR never fills 14 or 34–41.
6. **ROI24 legend.** `MX_F41_ROI24` is tipo de reporte 2 with a `legend` parameter (`LISTA_PERSONAS_BLOQUEADAS` or `REPORTE_24_HORAS`); the legend text, upper-cased, is prefixed to column 40 followed by `. `. Placement inside column 40 is `UNVERIFIED`.
7. **Empty report.** Only `MX_F41_ROR` has a zero-report rule (`REQUIRED`); ROI, ROI24 and ROIP refuse an empty input (`ZERO_REPORT_NOT_ALLOWED`). The reporte vacío is one record with columns 1, 2, 4 and 5 filled and the rest empty, `UNVERIFIED` (the resolution requires an empty report but does not print its shape).
8. **Formato 41 corrections are `UNSUPPORTED`** in every definition (`correction_rule: UNSUPPORTED`); no mechanism is invented.
9. **Aviso characters.** Upper case, diacritics stripped but Ñ kept (the XSD patterns allow Ñ, not accented vowels), whitespace collapsed; then the XSD decides. Any XSD violation raises `SchemaValidationError` carrying lxml's error lines; nothing is written that does not validate.
10. **Aviso bytes.** UTF-8, XML declaration, two-space indentation, LF line endings, namespace `http://www.uif.shcp.gob.mx/recepcion/avi` as the default namespace, no `schemaLocation`. Amounts are `Decimal` quantized half-up (`monto` 2 places; `cantidad_activo_virtual` 2–10 places, trailing zeros beyond 2 trimmed); a `float` input is a type error. Date-times are timezone-aware and rendered in `America/Mexico_City`; a naive datetime is rejected.
11. **2 MB split.** An aviso file over 2,000,000 bytes is split into parts at aviso boundaries, each a complete `archivo/informe` for the same month; a single aviso over the cap raises.
12. **Informe en ceros and modificatorio.** The informe en ceros is an `informe` with no `aviso` (optionally `exento`). A modificatorio carries `folio_modificacion` (`AAAA-n`) and `descripcion_modificacion` plus the full aviso; modifying an informe en ceros raises. The once-only and 30-day window checks need filing state and stay in SP-05.
13. **2027 schema gated.** `report-defs/MX_AVISO_AVI/v2.yaml` exists with `valid_from: 2027-06-01`, `xsd: null`; serializing against it raises `SchemaUnavailableError` (`REPORT_SCHEMA_UNAVAILABLE`). When SAT publishes `avi2.xsd`, a follow-up adds the file, its hash, the field mapping and goldens. Nothing is guessed from the DOF field table.
14. **Fixtures** use synthetic people and RFC/CURP values that pass `xolotl.modules.party.identifiers` (checked in a test that imports it from `tests/`, since the library may not).

## Tasks

### T1 — Package skeleton, sources and normalizers (`feat/reporting-serializers-normalizers`)
- `src/xolotl/modules/reporting/{__init__,serializers/__init__}.py`; `errors.py` (`SerializerError(ValidationFailedError)` family with stable codes: `REPORT_FIELD_INVALID`, `ZERO_REPORT_NOT_ALLOWED`, `REPORT_SCHEMA_INVALID`, `REPORT_SCHEMA_UNAVAILABLE`, `REPORT_TOO_LARGE`, `CORRECTION_UNSUPPORTED`).
- `normalizers.py`: `ascii_fold`, `latin_upper_keep_enye`, `clean_text` (forbidden characters, whitespace), `fit` (truncate with warning), `rfc`, `curp` (strip separators, upper case, shape check), `amount` (Decimal, places, half-up, non-negative), `quantity`, `yyyymm`, `yyyymmdd`, `mx_datetime` (aware → `America/Mexico_City`, `YYYYMMDDHHMMSS`), `casfim_key` (`40-002` → `040002`).
- `report-defs/sources/` with the documents above, `SOURCES.yaml` (URL, retrieval date, SHA-256, status).
- Tests: unit tests with the layout's own examples; hypothesis properties: Formato 41 text is printable ASCII without `;`, never longer than its width, idempotent and stable.

### T2 — Formato 41 definitions, loader and DELIMITED serializer with ROR goldens (`feat/reporting-serializers-f41-ror`)
- `definitions.py`: frozen dataclasses for a definition (code, version, regulator, serializer, encoding, encoding_confidence, zero/correction rules, delimiter, line ending, columns with number, name, kind, width, applicability) and a YAML loader with validation.
- `report-defs/MX_F41_ROR/v1.yaml` and the shared catalogues (tipo de operación, instrumento monetario) as code tables.
- `formato41.py`: input types `F41Header`, `F41Subject`, `F41Operation`, `F41RelatedParty`; `serialize_f41(definition, header, operations)`.
- Goldens: ROR with three operations (person, company, foreigner with nationality 0) and the empty ROR. Property: every record has exactly 41 columns; reconciliation (record count equals operations).

### T3 — ROI, ROI24, ROIP (`feat/reporting-serializers-f41-roi`)
- Definitions `MX_F41_ROI`, `MX_F41_ROI24`, `MX_F41_ROIP` v1; related rows; detection date; mandatory 40–41; legend parameter; zero-report refusal.
- Goldens: ROI with two related parties, ROI24 with each legend, ROIP for an employee.

### T4 — Aviso XML, current schema (`feat/reporting-serializers-aviso`)
- Vendored `report-defs/MX_AVISO_AVI/xsd/avi.xsd` (hash checked at load) and `v1.yaml`.
- `aviso.py`: input types mirroring the XSD (`AvisoInforme`, `SujetoObligado`, `Aviso`, person, address, account and the five operation groups); deterministic lxml build; XSD validation; 2 MB split.
- Goldens: a fr. XVI month with compra, venta, intercambio, transferencias and fondos; the official example re-expressed as input validates.

### T5 — Informe en ceros, modificatorio, 2027 slot (`feat/reporting-serializers-aviso-zero-mod`)
- Informe en ceros (with and without `exento`), modificatorio goldens, refusal to modify a zero report, `MX_AVISO_AVI/v2.yaml` gated with `SchemaUnavailableError`, and the version selector by period (`valid_from`/`valid_to`).

## Gates
Each PR: `make lint types vulture importlint`, `uv run pytest tests/modules/reporting --no-cov -q`, then `make ci` before marking ready.
