"""The pre-mapped report fields and unit values of a reportable event (spec §10.3).

``fields`` is what a report line needs that does not come from the party: dates,
codes, amounts, the account reference and the counterparty's country, never a
name, an account number or a wallet address (PRV-501); SP-05 fills the parties
from their rows with ``VIEW_PII`` at render time. The codes come from one field
map per report (``field_maps/<report_code>.yaml``), mapped through the code tables
under ``report-defs/codetables/MX`` where the report's definition needs codes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, UnitAmount
from xolotl.platform.units import Conversion

FIELD_MAPS_DIR = Path(__file__).with_name("field_maps")
BRANCH = "0000"


class _FieldMapDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    report_code: str
    codetables: dict[str, str]
    operation_type: dict[str, str]
    instrument: dict[str, str]


@dataclass(frozen=True, slots=True)
class FieldMap:
    """How one report spells a transaction's operation type and instrument.

    ``operation_type`` is keyed ``"<DIRECTION>/<INSTRUMENT>"``, ``instrument`` by
    instrument; ``codetables`` names the code table of each field that has one (a
    field without one is written as it is).
    """

    report_code: str
    operation_type: Mapping[str, str]
    instrument: Mapping[str, str]
    codetables: Mapping[str, str]


@cache
def read_field_map(report_code: str) -> FieldMap:
    """Read the field map of ``report_code``, once per process.

    Returns:
        The map.

    Raises:
        ValueError: When the report has no field map, or its file is malformed
            or names another report.

    """
    path = FIELD_MAPS_DIR / f"{report_code}.yaml"
    if not path.is_file():
        msg = f"report {report_code} has no field map"
        raise ValueError(msg)
    try:
        document = _FieldMapDocument.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
    except ValidationError as error:
        msg = f"field map {path.name} is malformed"
        raise ValueError(msg) from error
    if document.report_code != report_code:
        msg = f"field map {path.name} names report {document.report_code}"
        raise ValueError(msg)
    return FieldMap(
        report_code=document.report_code,
        operation_type=document.operation_type,
        instrument=document.instrument,
        codetables=document.codetables,
    )


def _money(minor: int | None, currency: str) -> dict[str, JsonValue] | None:
    return None if minor is None else {"minor": minor, "currency": currency}


def _crypto(txn: EnrichedTransaction) -> dict[str, JsonValue] | None:
    if txn.crypto is None:
        return None
    # Only the network: the context's address is the counterparty's wallet.
    network = txn.crypto.get("network")
    return {
        "network": network if isinstance(network, str) else None,
        "asset_code": txn.asset_code,
        "asset_qty": None if txn.asset_qty is None else str(txn.asset_qty),
    }


def event_fields(
    txn: EnrichedTransaction,
    account_ref: str,
    fmap: FieldMap,
    *,
    merged: Sequence[str] = (),
) -> dict[str, JsonValue]:
    """Pre-map the report fields of one captured transaction.

    The counterparty's institution and account hash are not on the enriched
    transaction, so they are written as None for SP-05 to fill from the
    counterparty row.

    Returns:
        The fields: codes, amounts, ids and the period keys, nothing personal.

    """
    operation = f"{txn.direction.value}/{txn.instrument.value}"
    counterparty: dict[str, JsonValue] | None = None
    if txn.counterparty_id is not None:
        counterparty = {
            "country": txn.counterparty_country,
            "institution": None,
            "account_hash": None,
        }
    return {
        "operation_date": policy_date(txn.occurred_at).isoformat(),
        "operation_type": fmap.operation_type.get(operation),
        "instrument": fmap.instrument.get(txn.instrument.value),
        "amount": _money(txn.amount_minor, txn.currency),
        "fee": _money(txn.fee_minor, txn.currency),
        "account_ref": account_ref,
        "branch": BRANCH,
        "locality": None,
        "counterparty": counterparty,
        "crypto": _crypto(txn),
        "merged_captures": list(merged),
        "period_keys": {
            "MONTH": txn.period_keys.MONTH,
            "QUARTER": txn.period_keys.QUARTER,
        },
    }


def _side(found: UnitAmount, unit: str) -> dict[str, JsonValue]:
    return {
        "amount": str(found.amount),
        "unit": unit,
        "unit_value_id": found.unit_value_id,
        "resolved_date": found.resolved_date.isoformat(),
    }


def unit_values(
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    converted: Conversion,
    *,
    fee: UnitAmount | None = None,
) -> dict[str, JsonValue]:
    """Record the threshold, its peso value and the amount compared with it.

    Args:
        definition: The capture that fired.
        txn: The captured transaction.
        converted: The threshold in pesos under the capture's rule (for the
            record; the comparison is in the threshold's unit, Deviation 8).
        fee: The fee in the threshold's unit, for a ``FEE_THRESHOLD`` capture.

    Returns:
        ``threshold``, ``converted``, ``transaction`` (the ``unit_amounts``
        entry) and ``fee``.

    """
    unit = definition.threshold.unit
    found = txn.unit_amounts.get(definition.unit_key)
    return {
        "threshold": {"amount": _plain(definition.threshold.amount), "unit": unit},
        "converted": {
            "amount": str(converted.amount),
            "currency": converted.currency,
            "unit_value_id": converted.unit_value_id,
            "rule": converted.rule.value,
            "resolved_date": converted.resolved_date.isoformat(),
        },
        "transaction": None if found is None else _side(found, unit),
        "fee": None if fee is None else _side(fee, unit),
    }


def _plain(amount: Decimal) -> str:
    return format(amount.normalize(), "f")
