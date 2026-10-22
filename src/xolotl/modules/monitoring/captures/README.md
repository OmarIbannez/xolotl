# Captures

A capture records that a transaction must appear in a regulatory report for a
period (SP-04 spec §10). The captures are the policy snapshot's, evaluated by
`CapturesHook` after the rules hook, inside each processed row's transaction.

- `definitions.py` builds a `CaptureDefinition` per capture of the snapshot, with
  the trigger filter from `triggers.yaml` (pure; import contract
  `monitoring-evaluator-is-pure`).
- `fields.py` pre-maps the report fields through `field_maps/<REPORT_CODE>.yaml`
  and records the unit values.
- `events.py` evaluates a transaction (`capture_transaction`); `record.py` writes
  events and opens or reopens periods; `groups.py` keeps accumulation groups;
  `supersede.py` rebuilds a group a late operation changes.

## For reporting (SP-05)

- One row per transaction and capture: supersession captures an event again in
  place. `monitoring.reportable_event_superseded.v1` carries `superseded_by_id`
  equal to the event's own id when it moved to a new group (a
  `reportable_event_captured` follows), and null when it was retired: its
  transaction is in no crossed group any more and is not reportable for the
  capture. An `INCLUDED` event captured again goes back to `CAPTURED`, so a
  report must record the events it took in its own report lines.
- `fields.unmapped` names the fields the report's map has no code for (written
  as null).

## Adding a capture code

1. Add the capture to the pack (`policy-packs/<COUNTRY>/<PACK>/vN.yaml`, a new
   pack version): code, kind, threshold, conversion rule, window, period, report,
   filing and, if one report covers it with another, `merge_into`.
2. Add a row for the code to `triggers.yaml`: a `TxnFilter` naming the
   transactions it looks at. A code without a row fails processing loudly.
3. If the report is new, add `field_maps/<REPORT_CODE>.yaml`: how the report spells
   the operation type (`"<DIRECTION>/<INSTRUMENT>"`) and the instrument, and the
   code table of each field that has one under `report-defs/codetables/`. A test
   checks that every code exists in its table.
4. Add a golden under `tests/golden/monitoring/captures/<CODE>/` (`units.yaml`,
   `transactions.yaml`, `expected.yaml`).
