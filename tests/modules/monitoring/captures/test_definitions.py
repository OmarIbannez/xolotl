"""Capture definitions from the policy snapshot and the trigger map (plan 4b B10)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.platform.policy_support import both_packs
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateFunction,
    Measure,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.captures.definitions import (
    TRIGGERS,
    capture_definitions,
    read_triggers,
)
from xolotl.modules.monitoring.captures.enums import CaptureKind
from xolotl.modules.monitoring.txn.schema import Instrument, KeyKind, Rail
from xolotl.platform.policy import EffectivePolicy


def pack_policy(code: str) -> EffectivePolicy:
    """Build the effective policy of one shipped pack, as a snapshot holds it.

    Returns:
        The policy, dated 2026-12-10.

    """
    pack = both_packs()[code]
    return EffectivePolicy(
        units=pack.units,
        kyc=pack.kyc,
        ubo=pack.ubo,
        screening=pack.screening,
        captures=pack.captures,
        reports=pack.reports,
        retention=pack.retention,
        governance=pack.governance,
        on=date(2026, 12, 10),
        product=None,
        country=pack.country,
        pack_codes=[pack.pack],
        pack_version_ids=[],
        override_version=None,
    )


def test_every_capture_of_both_packs_has_a_trigger():
    itf = {d.code: d for d in capture_definitions(pack_policy("MX/ITF-IFPE"))}
    avi = {d.code: d for d in capture_definitions(pack_policy("MX/LFPIORPI-AVI"))}

    assert set(itf) == {
        "ITF_ROR_CASH",
        "ITF_FX_CASH",
        "ITF_INTL_TRANSFER",
        "ITF_VA_OPS",
    }
    assert set(avi) == {"AVI_XVI_A", "AVI_XVI_B"}
    assert itf["ITF_ROR_CASH"].on == TxnFilter(instrument=frozenset({Instrument.CASH}))
    assert itf["ITF_FX_CASH"].on == TxnFilter(
        instrument=frozenset({Instrument.FX}), rail=frozenset({Rail.CASH})
    )
    assert itf["ITF_INTL_TRANSFER"].on == TxnFilter(
        instrument=frozenset({Instrument.TRANSFER}), cross_border=True
    )
    crypto = TxnFilter(instrument=frozenset({Instrument.CRYPTO}))
    assert itf["ITF_VA_OPS"].on == crypto
    assert avi["AVI_XVI_A"].on == crypto
    assert avi["AVI_XVI_B"].on == crypto
    assert {code: d.unit_key for code, d in (itf | avi).items()} == {
        "ITF_ROR_CASH": "USD/PRIOR_BANKING_DAY",
        "ITF_FX_CASH": "USD/PRIOR_BANKING_DAY",
        "ITF_INTL_TRANSFER": "USD/PRIOR_BANKING_DAY",
        "ITF_VA_OPS": "UDI/SAME_DAY",
        "AVI_XVI_A": "UMA/SAME_DAY",
        "AVI_XVI_B": "UMA/SAME_DAY",
    }
    assert itf["ITF_ROR_CASH"].period == "QUARTER"
    assert itf["ITF_INTL_TRANSFER"].period == "MONTH"
    assert itf["ITF_ROR_CASH"].report_code == "MX_F41_ROR"
    assert avi["AVI_XVI_A"].kind is CaptureKind.ACCUMULATION
    assert avi["AVI_XVI_A"].window == "P6M"
    assert avi["AVI_XVI_B"].kind is CaptureKind.FEE_THRESHOLD
    assert avi["AVI_XVI_B"].merge_into == "AVI_XVI_A"
    assert avi["AVI_XVI_A"].merge_into is None
    assert avi["AVI_XVI_A"].threshold.amount == Decimal(210)


def test_an_accumulation_capture_declares_its_accumulator():
    avi = {d.code: d for d in capture_definitions(pack_policy("MX/LFPIORPI-AVI"))}
    itf = capture_definitions(pack_policy("MX/ITF-IFPE"))

    (declaration,) = avi["AVI_XVI_A"].declarations()
    assert declaration.name == "AVI_XVI_A_accum"
    assert declaration.function is AggregateFunction.SUM
    assert declaration.measure is Measure.UNIT_AMOUNT
    assert declaration.unit_key == "UMA/SAME_DAY"
    assert declaration.key_kind is KeyKind.RELATIONSHIP
    assert declaration.window_kind is WindowKind.ACCUM
    assert declaration.window == "P6M"
    # Only operations below the threshold accumulate: one at or above it is
    # captured on its own (spec §10.2).
    assert declaration.where.instrument == frozenset({Instrument.CRYPTO})
    assert declaration.where.amount_unit_key == "UMA/SAME_DAY"
    assert declaration.where.amount_lt == Decimal(210)
    declaration.validate()
    assert avi["AVI_XVI_B"].declarations() == ()
    assert all(d.declarations() == () for d in itf)


def test_a_capture_without_a_trigger_row_is_a_load_error(tmp_path: Path):
    triggers = tmp_path / "triggers.yaml"
    _ = triggers.write_text("ITF_ROR_CASH: {instrument: [CASH]}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ITF_FX_CASH"):
        _ = capture_definitions(
            pack_policy("MX/ITF-IFPE"), triggers=read_triggers(triggers)
        )


@pytest.mark.parametrize(
    "content",
    [
        "ITF_ROR_CASH: {instrument: [CHEQUES]}\n",
        "ITF_ROR_CASH: {instrumentos: [CASH]}\n",
        "ITF_ROR_CASH: [CASH]\n",
        "- ITF_ROR_CASH\n",
    ],
)
def test_a_malformed_trigger_row_is_refused(tmp_path: Path, content: str):
    triggers = tmp_path / "triggers.yaml"
    _ = triggers.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="triggers"):
        _ = read_triggers(triggers)


def test_the_shipped_trigger_map_names_the_six_codes():
    assert set(TRIGGERS) == {
        "ITF_ROR_CASH",
        "ITF_FX_CASH",
        "ITF_INTL_TRANSFER",
        "ITF_VA_OPS",
        "AVI_XVI_A",
        "AVI_XVI_B",
    }
