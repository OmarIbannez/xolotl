"""Date helpers shared by the parsers."""

from xolotl.modules.screening.lists.types import PartialDate

# A year range wider than this keeps only its two ends.
MAX_RANGE_YEARS = 20


def year_span(start: int, end: int) -> list[PartialDate]:
    """Expand a birth-year range into one partial date per year.

    Returns:
        Each year from the earlier to the later bound, or only the two bounds
        when the range is wider than ``MAX_RANGE_YEARS``.

    """
    first, last = sorted((start, end))
    if last - first > MAX_RANGE_YEARS:
        return [PartialDate(first), PartialDate(last)]
    return [PartialDate(year) for year in range(first, last + 1)]
