"""Which dated unit value a conversion reads (program spec D5)."""

from enum import StrEnum


class ConversionRule(StrEnum):
    SAME_DAY = "SAME_DAY"
    PRIOR_BANKING_DAY = "PRIOR_BANKING_DAY"
    LAST_DAY_PREV_MONTH = "LAST_DAY_PREV_MONTH"
    PREV_MONTH_AVG = "PREV_MONTH_AVG"
    SEMIANNUAL_SNAPSHOT = "SEMIANNUAL_SNAPSHOT"
