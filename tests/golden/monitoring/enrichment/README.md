# Enrichment goldens

`cases.yaml` pins what `xolotl.modules.monitoring.enrich` computes (SP-04 spec §7,
plan 4a task A6): the unit amounts of a transaction keyed `"<unit>/<rule>"` with the
`unit_value` row each one read, the parking of a transaction whose value is missing,
and the Mexico City period keys. `tests/modules/monitoring/enrich/test_golden.py`
runs every case over `tests/golden/units/values.yaml` (fixture values, not published
ones) and the `BANXICO` holidays of `policy-packs/_holidays/MX.yaml`, with the ITF
pack assigned to `EMONEY` and the AVI pack to `CRYPTO`, and prints a unified diff of
the expected and actual JSON when a case fails.

A `units` operation takes its `(unit, rule)` pairs from the policy of its `product`
on its date, or from `pairs` when no pack is in force on that date (the AVI pack
starts on 2026-11-30) or the fixture lacks the other values the policy would read.
A USD amount is brought to pesos at the prior banking day's FIX first (plan 4a
Deviation 6) and every other unit is converted from those pesos.

To add a case, append it with a new `id`; amounts are strings with the places the
conversion gives (two for a currency, six for a unit).
