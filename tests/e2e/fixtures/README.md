# SP-03 exit fixtures

`tests/e2e/test_sp03_exit.py` runs the SP-03 exit demo over committed files only:

- **Shared lists**, the screening library's trimmed samples under
  `tests/modules/screening/fixtures/` (read-only, owned by the library; see each
  folder's README): `un/consolidated.xml` (`UN_CONSOLIDATED`),
  `ofac/sdn_enhanced.xml` (`OFAC_SDN`), `eu/full_sanctions_list_1_1.xml` (`EU_FSF`),
  `uk/UK-Sanctions-List.xml` (`UK_FCDO`) and `sat69b/Listado_Completo_69-B.csv`
  (`MX_SAT_69B`).
- **The UN second version**, plan 3a's
  `tests/modules/screening/catalog/fixtures/un_v2.xml`: the same UN list with one
  entry added (`9999001`, a made-up name), one changed (`6908052`, an alias) and one
  removed (`6908048`); see its README.
- **`lpb_tenant_a.csv`**, tenant A's `MX_LPB` upload in our template (spec §7): four
  rows with fictional names, valid RFC and CURP shapes (check digits included), one
  legal person, one `nombre_completo` row and one row with an alias. Row
  `LPB-A-001` is the one the test perturbs (second surname dropped, accents lost)
  and row `LPB-A-004` is the one tenant B's party copies exactly.
