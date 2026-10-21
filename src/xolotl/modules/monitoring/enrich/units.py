"""A transaction's amount in every threshold unit of its policy (spec §7 step 3).

The conversion path is one rule for monitoring (plan 4a Deviation 6,
reconciliation finding 8): the local leg first (a peso amount is itself under
``MXN/SAME_DAY``; a dollar amount becomes pesos at the prior banking day's FIX
under ``MXN/PRIOR_BANKING_DAY``), then every other unit from those pesos. A pair
whose unit is the transaction's own currency is the identity and reads no value.
A value that is not there yet parks the transaction instead of borrowing another
day's (program spec D5).
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from xolotl.modules.monitoring.enums import ParkReason
from xolotl.modules.monitoring.txn.schema import UnitAmount
from xolotl.platform.calendar import Holidays
from xolotl.platform.policy import EffectivePolicy
from xolotl.platform.units import (
    UNIT_OF,
    ConversionRule,
    Quantity,
    UnitService,
    UnitValueMissingError,
    resolve_date,
)

LOCAL_CCY = "MXN"
# The typology library compares in dollars at the prior banking day's FIX.
LIBRARY_PAIR = ("USD", ConversionRule.PRIOR_BANKING_DAY)


class ParkTransactionError(Exception):
    """The transaction cannot be enriched yet; the processing job parks it."""

    def __init__(
        self,
        reason: ParkReason,
        unit_code: str | None = None,
        *,
        needed_on: date | None = None,
    ) -> None:
        """Record why, and which unit value on which date is awaited."""
        super().__init__(reason.value)
        self.reason: ParkReason = reason
        self.unit_code: str | None = unit_code
        self.needed_on: date | None = needed_on


def unit_key(unit: str, rule: ConversionRule) -> str:
    """Name one unit under one rule, as ``unit_amounts`` keys it.

    Returns:
        ``"<unit>/<rule>"``, for example ``"UDI/SAME_DAY"``.

    """
    return f"{unit}/{rule.value}"


def unit_pairs(policy: EffectivePolicy) -> frozenset[tuple[str, ConversionRule]]:
    """List the ``(unit, rule)`` pairs a policy's thresholds are written in.

    Returns:
        Every capture threshold's unit with its conversion rule, every tier
        limit's unit with ``units.tier_limit_rule`` (when the policy sets one),
        and the typology library's ``USD`` at the prior banking day.

    """
    pairs = {
        (capture.threshold.value.unit, capture.conversion_rule)
        for capture in policy.captures
    }
    tier_rule = policy.units.tier_limit_rule
    if tier_rule is not None:
        limits = (
            limit
            for tier in policy.kyc.tiers
            for limit in (tier.limits.monthly_ops, tier.limits.max_balance)
            if limit is not None
        )
        pairs.update((limit.value.unit, tier_rule.value) for limit in limits)
    pairs.add(LIBRARY_PAIR)
    return frozenset(pairs)


def unit_amounts_for(
    units: UnitService,
    *,
    amount: Decimal,
    currency: str,
    on: date,
    pairs: Iterable[tuple[str, ConversionRule]],
    holidays: Holidays,
) -> dict[str, UnitAmount]:
    """Convert an amount into every pair, recording the value each one read.

    A missing value raises ``ParkTransactionError`` (``UNIT_VALUE_MISSING``) with the
    unit code and date of the first one: the local leg first, then the pairs in
    sorted order.

    Args:
        units: The unit service of the caller's session.
        amount: The amount in ``currency``.
        currency: ``MXN`` or ``USD`` (the schema accepts no other).
        on: The policy date of the transaction.
        pairs: The ``(unit, rule)`` pairs to compute.
        holidays: The ``BANXICO`` holidays, to name the date a missing value
            is needed on.

    Returns:
        The local leg and one ``UnitAmount`` per pair, keyed by ``unit_key``.

    Raises:
        ParkTransactionError: ``UNIT_VALUE_MISSING`` when a unit value the
            conversion needs is not loaded yet.
        ValueError: For a currency other than ``MXN`` and ``USD``.

    """
    found: dict[str, UnitAmount] = {}
    if currency == LOCAL_CCY:
        local = amount
        found[unit_key(LOCAL_CCY, ConversionRule.SAME_DAY)] = UnitAmount(
            amount=amount, unit_value_id=None, resolved_date=on
        )
    elif currency == "USD":
        rule = ConversionRule.PRIOR_BANKING_DAY
        try:
            leg = units.threshold_in_ccy(
                Quantity(amount=amount, unit="USD"), LOCAL_CCY, on, rule
            )
        except UnitValueMissingError as missing:
            raise ParkTransactionError(
                ParkReason.UNIT_VALUE_MISSING,
                UNIT_OF.get("USD", "USD"),
                needed_on=resolve_date(rule, on, holidays=holidays),
            ) from missing
        local = leg.amount
        found[unit_key(LOCAL_CCY, rule)] = UnitAmount(
            amount=leg.amount,
            unit_value_id=leg.unit_value_id,
            resolved_date=leg.resolved_date,
        )
    else:
        msg = f"unsupported currency {currency}"
        raise ValueError(msg)
    for unit, rule in sorted(pairs):
        if unit == currency:
            found[unit_key(unit, rule)] = UnitAmount(
                amount=amount, unit_value_id=None, resolved_date=on
            )
            continue
        try:
            conversion = units.to_unit(local, LOCAL_CCY, unit, on, rule)
        except UnitValueMissingError as missing:
            raise ParkTransactionError(
                ParkReason.UNIT_VALUE_MISSING,
                UNIT_OF.get(unit, unit),
                needed_on=resolve_date(rule, on, holidays=holidays),
            ) from missing
        found[unit_key(unit, rule)] = UnitAmount(
            amount=conversion.amount,
            unit_value_id=conversion.unit_value_id,
            resolved_date=conversion.resolved_date,
        )
    return found
