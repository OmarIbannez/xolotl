# File ingest fixtures (plan 4a, task A5)

Synthetic data only: every name, reference and amount is made up.

- `deposits.csv`, `deposits_semicolon_bom.csv`, `deposits.jsonl`, `deposits.xlsx`: the
  same four transactions of customer `cust-1`. The CSV quotes the commas inside names;
  the second CSV is semicolon-delimited, fully quoted and starts with a UTF-8 BOM; the
  JSON Lines file nests `counterparty` and `raw` and writes amounts as numbers; the
  XLSX keeps amounts as numbers on its first sheet. The column `sucursal` is not a
  field of the canonical transaction, so it lands in `raw`.
- `bad_rows.csv`: ten rows of `cust-1`. Row 4 has an amount that is not a number, row 7
  (`bad-dup`) repeats a transaction the tests ingest first, and row 8 names a customer
  that does not exist: 7 accepted, 1 duplicate, 2 rejected.

An XLSX date cell has no zone: the reader takes it as `America/Mexico_City` time
(`MEXICO_CITY` of `xolotl.platform.calendar`), so a cell `2026-01-15 10:00` is
`2026-01-15T10:00:00-06:00`. The `deposits.xlsx` fixture keeps `occurred_at` as text
with its offset.

They were written with `openpyxl` and the `csv` module; edit the four `deposits`
files together so they keep spelling the same rows.
