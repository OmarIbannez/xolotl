"""Capture definitions, built from the policy snapshot (SP-04 spec §9.2, Decision 2).

A capture has no tenant rule version: its threshold, unit, conversion rule,
window and period are the policy's, and the transactions it looks at come from
the trigger map (``triggers.yaml``), keyed by capture code, so a new capture code
of a pack is a YAML row rather than code (ADR-004).

An ``ACCUMULATION`` capture declares one ``ACCUM`` aggregate per relationship over
the operations below its threshold (an operation at or above it is captured on
its own, spec §10.2), so plan 4a's processing job maintains it and the decide
path can read it.

Pure: no database, no web framework (contract ``monitoring-evaluator-is-pure``).
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

import yaml
from pydantic import TypeAdapter, ValidationError

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.captures.enums import CaptureKind
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.platform.policy.resolver import EffectivePolicy
from xolotl.platform.units.quantity import Quantity
from xolotl.platform.units.rules import ConversionRule

TRIGGERS_FILE = Path(__file__).with_name("triggers.yaml")
_FILTER = TypeAdapter(TxnFilter)
_FILTER_FIELDS = frozenset(f.name for f in fields(TxnFilter))
_ROWS = TypeAdapter(dict[str, dict[str, object]])


@dataclass(frozen=True, slots=True)
class CaptureDefinition:
    """One capture of a policy snapshot, with the transactions it looks at."""

    code: str
    kind: CaptureKind
    report_code: str
    threshold: Quantity
    conversion_rule: ConversionRule
    window: str | None
    period: Literal["MONTH", "QUARTER"]
    merge_into: str | None
    on: TxnFilter
    filing: str

    @property
    def unit_key(self) -> str:
        """The ``unit_amounts`` key the threshold compares in (``"UMA/SAME_DAY"``)."""
        return f"{self.threshold.unit}/{self.conversion_rule.value}"

    @property
    def accumulates(self) -> TxnFilter:
        """The operations an ``ACCUMULATION`` group adds up: ``on``, below threshold.

        Returns:
            ``on`` with the band ``amount < threshold`` in the threshold's unit.

        """
        return replace(
            self.on,
            amount_unit_key=self.unit_key,
            amount_gte=None,
            amount_lt=self.threshold.amount,
        )

    def period_key_of(self, txn: EnrichedTransaction) -> str:
        """Name the capture's period that ``txn`` falls in.

        Returns:
            ``txn.period_keys.MONTH`` or ``.QUARTER``, per the capture's period.

        """
        keys = txn.period_keys
        return keys.MONTH if self.period == "MONTH" else keys.QUARTER

    def declarations(self) -> tuple[AggregateDeclaration, ...]:
        """Name the aggregate the capture reads.

        Returns:
            For an ``ACCUMULATION`` capture, its ``ACCUM`` sum in the threshold's
            unit per relationship over ``window``; nothing for the others.

        """
        if self.kind is not CaptureKind.ACCUMULATION or self.window is None:
            return ()
        return (
            AggregateDeclaration(
                name=f"{self.code}_accum",
                function=AggregateFunction.SUM,
                measure=Measure.UNIT_AMOUNT,
                unit_key=self.unit_key,
                key_kind=KeyKind.RELATIONSHIP,
                window_kind=WindowKind.ACCUM,
                window=self.window,
                where=self.accumulates,
            ),
        )


def read_triggers(path: Path = TRIGGERS_FILE) -> Mapping[str, TxnFilter]:
    """Read the trigger map: one ``TxnFilter`` per capture code.

    Returns:
        The filters by capture code.

    Raises:
        ValueError: When the file is not a mapping of codes to filters, or a row
            names a field ``TxnFilter`` lacks or a value it refuses.

    """
    try:
        rows = _ROWS.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as error:
        msg = f"triggers {path.name}: a mapping of capture codes to filters is expected"
        raise ValueError(msg) from error
    found: dict[str, TxnFilter] = {}
    for code, spelled in rows.items():
        unknown = sorted(set(spelled) - _FILTER_FIELDS)
        if unknown:
            msg = f"triggers {path.name}: {code} names unknown fields {unknown}"
            raise ValueError(msg)
        try:
            found[str(code)] = _FILTER.validate_python(spelled)
        except ValidationError as error:
            msg = f"triggers {path.name}: {code} is not a valid filter"
            raise ValueError(msg) from error
    return found


TRIGGERS = read_triggers()


def capture_definitions(
    policy: EffectivePolicy, *, triggers: Mapping[str, TxnFilter] = TRIGGERS
) -> list[CaptureDefinition]:
    """Build one definition per capture of ``policy``.

    Returns:
        The definitions, in the policy's order.

    Raises:
        ValueError: When a capture code has no trigger row (a pack content
            defect: the processing job fails loudly rather than skip it).

    """
    found: list[CaptureDefinition] = []
    for capture in policy.captures:
        on = triggers.get(capture.code)
        if on is None:
            msg = f"capture {capture.code} has no row in the trigger map"
            raise ValueError(msg)
        found.append(
            CaptureDefinition(
                code=capture.code,
                kind=CaptureKind(capture.kind),
                report_code=capture.report,
                threshold=capture.threshold.value,
                conversion_rule=capture.conversion_rule,
                window=None if capture.window is None else capture.window.value,
                period=capture.period.value,
                merge_into=(
                    None if capture.merge_into is None else capture.merge_into.value
                ),
                on=on,
                filing=capture.filing,
            )
        )
    return found
