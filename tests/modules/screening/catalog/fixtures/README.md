# Catalog fixtures

- `un_v2.xml`: the screening library's trimmed UN consolidated list
  (`tests/modules/screening/fixtures/un/consolidated.xml`, see its README) as a
  second version, with exactly three changes so ingestion tests see one of each:
  - **added:** one synthetic individual, `DATAID 9999001` ("ZEPHYRINO QUALMSBY", a
    made-up name, reference `KPi.999`);
  - **changed:** `DATAID 6908052`, its alias `Al-Kimawi` now reads
    `Al-Kimawi Al-Majid`;
  - **removed:** `DATAID 6908048`.
  - `dateGenerated` moves one day later (`2026-10-20T23:00:03.500Z`). Everything
    else is byte-for-byte the source fixture.
