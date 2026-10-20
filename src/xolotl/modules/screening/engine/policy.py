"""The policy a screening run resolves (spec Decision 14).

"Today" for policy resolution is the calendar day in Mexico City, whatever the
server's zone.
"""

from datetime import date
from zoneinfo import ZoneInfo

from xolotl.platform import clock
from xolotl.platform.calendar.deadlines import MEXICO_CITY


def today_mx() -> date:
    """Tell the date in Mexico City now.

    Returns:
        The calendar day in ``America/Mexico_City``.

    """
    return clock.now().astimezone(ZoneInfo(MEXICO_CITY)).date()
