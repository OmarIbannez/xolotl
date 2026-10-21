"""One hand-built rule document per kind, shared by the definition and render tests."""

import copy
from typing import Any

from pydantic import JsonValue

from xolotl.modules.monitoring.rules.definitions import RuleDefinition, RuleKind

_USD = "USD/PRIOR_BANKING_DAY"


def _sum(name: str, window: str, **where: JsonValue) -> dict[str, JsonValue]:
    return {
        "name": name,
        "function": "SUM",
        "measure": "UNIT_AMOUNT",
        "unit_key": _USD,
        "key_kind": "RELATIONSHIP",
        "window_kind": "DURATION",
        "window": window,
        "where": dict(where),
    }


_DOCUMENTS: dict[RuleKind, dict[str, JsonValue]] = {
    RuleKind.THRESHOLD: {
        "code": "SELF_CUSTODY_OUTFLOW",
        "family": "ALERT",
        "kind": "THRESHOLD",
        "title": "Self-custody outflow",
        "typology": ["CRYPTO"],
        "on": {"direction": ["OUT"], "instrument": ["CRYPTO"], "self_custody": True},
        "unit_key": _USD,
        "params": {"threshold": {"value": "1000", "min": "500", "unit": "USD"}},
        "severity": "MEDIUM",
    },
    RuleKind.STRUCTURING: {
        "code": "CASH_STRUCTURING",
        "family": "ALERT",
        "kind": "STRUCTURING",
        "title": "Cash structuring",
        "typology": ["STRUCTURING"],
        "on": {"direction": ["IN"], "instrument": ["CASH"]},
        "unit_key": _USD,
        "params": {
            "threshold": {
                "value": "5000",
                "min": "1000",
                "tighten": "LOWER",
                "unit": "USD",
            },
            "near_ratio": {"value": "0.70", "min": "0.50", "max": "0.95"},
            "window": {"value": "P7D"},
            "min_count": {"value": 3, "min": 2},
        },
        "aggregates": [
            {
                **_sum("near_sum", "P7D", direction=["IN"], instrument=["CASH"]),
                "where": {
                    "direction": ["IN"],
                    "instrument": ["CASH"],
                    "amount_unit_key": _USD,
                    "amount_gte": "3500",
                    "amount_lt": "5000",
                },
            }
        ],
        "severity": "MEDIUM",
        "dedup": {"window": "P7D"},
        "evidence": {"transactions_window": "P7D", "aggregates": ["near_sum"]},
    },
    RuleKind.AGGREGATE: {
        "code": "RAPID_PASS_THROUGH",
        "family": "ALERT",
        "kind": "AGGREGATE",
        "title": "Rapid pass-through",
        "typology": ["MULE"],
        "unit_key": _USD,
        "params": {"min_in": {"value": "2000", "min": "500", "unit": "USD"}},
        "aggregates": [
            _sum("in_sum", "P2D", direction=["IN"]),
            _sum("out_sum", "P2D", direction=["OUT"]),
        ],
        "conditions": [
            {"aggregate": "in_sum", "op": "GTE", "threshold": "min_in"},
            {"aggregate": "out_sum", "op": "GTE", "ratio_of": "in_sum", "ratio": "0.9"},
        ],
        "severity": "MEDIUM",
        "dedup": {"window": "P2D"},
    },
    RuleKind.VELOCITY: {
        "code": "NEW_ACCOUNT_VELOCITY",
        "family": "ALERT",
        "kind": "VELOCITY",
        "title": "New account velocity",
        "typology": ["VELOCITY"],
        "unit_key": _USD,
        "params": {
            "window": {"value": "P1D"},
            "count": {"value": 10, "min": 3},
            "sum": {"value": "3000", "unit": "USD"},
            "new_account_days": {"value": 30, "max": 90},
        },
        "severity": "MEDIUM",
    },
    RuleKind.HIGH_RISK_JURISDICTION: {
        "code": "HIGH_RISK_JURISDICTION_TRANSFER",
        "family": "ALERT",
        "kind": "HIGH_RISK_JURISDICTION",
        "title": "High-risk jurisdiction transfer",
        "typology": ["JURISDICTION"],
        "on": {"instrument": ["TRANSFER"]},
        "unit_key": _USD,
        "params": {
            "threshold": {"value": "1000", "unit": "USD"},
            "lists": {"value": "FATF_BLACK,FATF_GREY"},
        },
        "severity": "MEDIUM",
    },
    RuleKind.PROFILE_DEVIATION: {
        "code": "DECLARED_VS_ACTUAL_VOLUME",
        "family": "ALERT",
        "kind": "PROFILE_DEVIATION",
        "title": "Declared versus actual volume",
        "typology": ["PROFILE"],
        "on": {"direction": ["IN"]},
        "params": {
            "ratio": {"value": "2.0", "min": "1.2", "max": "5.0"},
            "history_ratio": {"value": "3.0"},
        },
        "severity": "MEDIUM",
        "dedup": {"window": "MONTH"},
    },
    RuleKind.TIER_LIMIT: {
        "code": "TIER_N1_MONTHLY_OPS",
        "family": "ALERT",
        "kind": "TIER_LIMIT",
        "title": "Tier N1 monthly operations",
        "typology": ["LIMIT"],
        "on": {"direction": ["IN"]},
        "params": {"outcome": {"value": "BLOCK", "choices": ["BLOCK", "REVIEW"]}},
        "aggregates": [
            {
                "name": "month_in",
                "function": "SUM",
                "measure": "UNIT_AMOUNT",
                "unit_key": "UDI/LAST_DAY_PREV_MONTH",
                "key_kind": "RELATIONSHIP",
                "window_kind": "MONTH",
                "window": "MONTH",
                "where": {"direction": ["IN"]},
            }
        ],
        "severity": "HIGH",
        "sync": True,
        "dedup": {"window": "MONTH", "cooldown": "P0D"},
    },
}


def document(kind: RuleKind) -> dict[str, Any]:
    """Copy the kind's document so a test may edit it.

    Returns:
        A deep copy.

    """
    return copy.deepcopy(_DOCUMENTS[kind])


def definition(kind: RuleKind) -> RuleDefinition:
    return RuleDefinition.model_validate(document(kind))
