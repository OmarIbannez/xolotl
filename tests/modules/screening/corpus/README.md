# Screening evaluation corpus

The matcher's quality gate (SP-03 spec §4.3, FR-SC-014, NFR-SC-006).

- **Seeds:** every screenable entry of the five trimmed real fixtures under `../fixtures/` (UN, OFAC SDN, EU FSF, UK FCDO, SAT 69-B).
- **True pairs:** `xolotl.modules.screening.matching.evaluation.generate_pairs` turns each seed name into up to `per_entry` perturbed queries: a dropped maternal surname, swapped order, lost or added accents, common letter substitutions, nicknames, initials, particle changes, typos, and truncation at 60 characters, one or two at a time. It uses the seed in `floors.yaml`, so the pairs are the same on every run. `test_corpus.py` pins their count and a hash, so a generator change shows up in review.
- **Hard negatives:** `hard_negatives.csv` holds 520 synthetic, very common Mexican personal and company names. They are built from frequent given names and the surnames in `data/surnames_mx.csv`, and a test checks that none is a fixture name. They are negative by construction rather than labelled by hand. No real customer data is used.
- **Floors and thresholds:** in `floors.yaml`, with the first measurement recorded in its header.

Run `uv run pytest tests/modules/screening/corpus --no-cov -q` for the gate. The report figures come from `evaluate()`; plan 3c adds `xolotl screening eval`.
