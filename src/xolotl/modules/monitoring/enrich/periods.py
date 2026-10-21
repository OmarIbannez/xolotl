"""The Mexico City periods a transaction falls in (SP-04 spec §7 step 4)."""

from datetime import datetime

from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.txn.schema import PeriodKeys
from xolotl.platform.calendar import Holidays, is_business_day


def period_keys_for(occurred_at: datetime, *, holidays: Holidays) -> PeriodKeys:
    """Compute the day, month, quarter and banking-day flag of an instant.

    Args:
        occurred_at: An aware instant.
        holidays: The ``BANXICO`` holidays of Mexico.

    Returns:
        The keys, all in Mexico City (``2025-12-31``, ``2025-12``, ``2025-Q4``).

    """
    day = policy_date(occurred_at)
    return PeriodKeys(
        DAY=day.isoformat(),
        MONTH=f"{day.year}-{day.month:02d}",
        QUARTER=f"{day.year}-Q{(day.month - 1) // 3 + 1}",
        BUSINESS_DAY=is_business_day(day, holidays=holidays),
    )
