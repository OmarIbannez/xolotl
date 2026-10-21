# Mexico typology library

One YAML file per monitoring rule template, named after its code
(`CASH_STRUCTURING.yaml` holds `definition.code: CASH_STRUCTURING`). To add a
template, copy the closest file, change the code, title, typology, `packs` and the
definition, start at `version: 1`, run `uv run xolotl rules validate`, and pin its hash
in `tests/golden/monitoring/rules/hashes.yaml` and its sentence in both locales under
`tests/golden/monitoring/rules/render/`. A loaded version never changes: to change a
template, raise its `version`. Each parameter carries the bounds a tenant's version
must stay within (`min`, `max`, `choices`), and `tighten` says the one direction a
tenant may move it (`LOWER` for a threshold that may only go down); a parameter with
neither is free. Quote every decimal (`"0.70"`) and the `"on"` key, which YAML 1.1
would read as `true`.
