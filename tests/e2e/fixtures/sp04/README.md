# SP-04 exit fixtures

`tests/e2e/test_sp04_exit.py` runs the SP-04 exit demo (spec §19, plan 4c C11) over
committed files only. Each JSON file holds the items a client posts to
`POST /v1/transactions` (`decide_n1.json`: the one body of `POST /v1/transactions/decide`).
Unit values are plan 4b B7's `tests/modules/monitoring/fixtures/unit_values_2026.yaml`
(UDI, UMA and `USD_MXN_FIX` for July to December 2026, no FIX on 12 and 13 December);
the lists are the screening library's OFAC and UN samples under
`tests/modules/screening/fixtures/`.

- **`batch_accumulation.json`**: five MXN 4,000 crypto operations of `CRYPTO-001`
  (34.10 UMA each at the 2026 UMA of 117.31), 30 November to 4 December 2026. Spec §19
  accumulates from July, but the shipped `MX/LFPIORPI-AVI` v1 is in force only from
  30 November 2026 (plan 4b Open question 14, kept by the product owner), so the
  accumulation starts that day: "`AVI_XVI_A` captures the whole group" with one event in
  period `2026-11` and the rest in `2026-12`. Sent on 4 December, as it happens: an
  operation received more than `XOLOTL_MONITORING_LATE_DAYS` (7) after it occurred is
  held for a backfill.
- **`batch_week.json`**, the week of 7 to 13 December 2026, sent on Monday the 14th:
  - two SPEI deposits of MXN 3,010.70 (350 UDI at the UDI of 30 November, 8.602) of
    `EMONEY-N1`, so the N1 customer is at 700 of its 750 UDI month to date;
  - three cash deposits of `EMONEY-001` on the 8th, 9th and 10th at USD 4,000, 4,200 and
    4,500 at the prior banking day's FIX (70 to 100 % of USD 5,000): `CASH_STRUCTURING`;
  - an international transfer of `EMONEY-001` on the 9th, USD 2,000 by `SWIFT` to a
    counterparty in `US`: `ITF_INTL_TRANSFER`;
  - the December crypto operation of `CRYPTO-001` on the 11th, MXN 5,000 (42.62 UMA)
    with a MXN 50 fee, which takes the group to 213.11 UMA, past 210;
  - a SPEI transfer whose counterparty is a perturbed OFAC entry ("Rodrigo Alarcón
    Palomarez" for uid 57118, ALARCON PALOMARES, Rodrigo: an accent added, the second
    surname misspelt);
  - a cash deposit of MXN 100,000 on Saturday 12 December, USD 5,493.75 at Friday the
    11th's FIX (18.2025): `ITF_ROR_CASH`, across the weekend (plan 4c Deviation 14);
  - a crypto operation of `EMONEY-001` to OFAC uid 45314's digital-currency address
    (VALERIAN LABS): an exact wallet match;
  - a small cash deposit on Monday 14 December, outside the week, whose prior banking
    day is the 11th too.
- **`late_crypto.json`**: a MXN 5,000 crypto operation of `CRYPTO-001` dated 10
  December and sent on 12 December, after the 11th's was processed: the group crosses a
  day earlier, so it is superseded and its operations captured again in a new group.
- **`decide_n1.json`**: a SPEI deposit of MXN 860.20 (100 UDI) of `EMONEY-N1` asked for on
  14 December, which takes the month to 800 UDI, past the N1 limit of 750:
  `BLOCK` with `TIER_LIMIT`.
