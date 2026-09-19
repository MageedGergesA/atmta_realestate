"""Deterministic rent proration (Phase 13).

Pure functions, no ORM, so the arithmetic is trivially unit-testable and cannot
drift between the schedule generator, the amendment engine and the termination
settlement -- all three call the same code.

Four conventions are supported:

``actual``
    Charge ``days_occupied / days_in_period``. The honest default.

``thirty_day``
    Treat every month as 30 days (the "30/360" convention used across much of
    MENA commercial leasing). Feb 28 and Jul 31 both bill as 30.

``none``
    Charge the whole period regardless of how much of it the tenant had. Used
    where the lease says rent is due monthly in advance and is not refundable.

``full``
    The mirror image: only whole periods are billed, a partial period is free.
    Used for fit-out or grace conventions written into the contract.

All functions take and return plain ``date`` / ``float`` values.
"""

from datetime import timedelta

#: Days in a notional month under the 30/360 convention.
THIRTY_DAY_MONTH = 30


def _clamp(value, low, high):
    return max(low, min(value, high))


def days_inclusive(start, end):
    """Number of days in a **closed** interval ``[start, end]``.

    A lease running 1 Jan -> 31 Jan covers 31 days, not 30. Every date maths in
    this module uses closed intervals, matching how leases are written.
    """
    if not start or not end or end < start:
        return 0
    return (end - start).days + 1


def _is_month_end(day):
    """Whether ``day`` is the last day of its own month."""
    next_month = day.replace(day=28) + timedelta(days=4)
    return (next_month - timedelta(days=next_month.day)).day == day.day


def thirty_day_count(start, end):
    """Day count between two dates under the 30-day-month convention.

    Both endpoints inclusive, consistent with :func:`days_inclusive`.

    The end-of-month adjustment is what makes the convention actually work for
    rent: a whole February must count as 30, not 28, otherwise a tenant who
    moves in mid-February is charged a different fraction than one who moves in
    mid-March. So a date that is the last day of its month counts as day 30.
    """
    if not start or not end or end < start:
        return 0
    d1 = min(start.day, THIRTY_DAY_MONTH)
    d2 = THIRTY_DAY_MONTH if _is_month_end(end) else min(end.day, THIRTY_DAY_MONTH)
    # +1 for the inclusive end date, mirroring days_inclusive.
    return ((end.year - start.year) * 360
            + (end.month - start.month) * THIRTY_DAY_MONTH
            + (d2 - d1)) + 1


def proration_factor(method, period_start, period_end,
                     occupied_start, occupied_end):
    """Fraction of a billing period that should actually be charged.

    :param method: one of ``actual``, ``thirty_day``, ``none``, ``full``
    :param period_start/period_end: the full billing period (closed interval)
    :param occupied_start/occupied_end: the part the tenant is liable for
    :returns: float in ``[0.0, 1.0]``

    Returns ``0.0`` when the liable window does not intersect the period at
    all, and ``1.0`` when it covers the whole period -- for every method.
    """
    if not period_start or not period_end or period_end < period_start:
        return 0.0

    # Intersect the liable window with the billing period.
    start = max(period_start, occupied_start or period_start)
    end = min(period_end, occupied_end or period_end)
    if end < start:
        return 0.0

    covers_whole_period = (start <= period_start and end >= period_end)

    if method == 'none':
        # Any overlap at all bills the full period.
        return 1.0
    if method == 'full':
        # Only a complete period bills; a partial period is free.
        return 1.0 if covers_whole_period else 0.0
    if covers_whole_period:
        return 1.0

    if method == 'thirty_day':
        occupied = thirty_day_count(start, end)
        total = thirty_day_count(period_start, period_end)
    else:  # 'actual' and any unknown method fall back to the honest default
        occupied = days_inclusive(start, end)
        total = days_inclusive(period_start, period_end)

    if not total:
        return 0.0
    return _clamp(occupied / total, 0.0, 1.0)


def prorate(amount, method, period_start, period_end,
            occupied_start, occupied_end, precision=2):
    """Apply :func:`proration_factor` to a monetary amount."""
    factor = proration_factor(
        method, period_start, period_end, occupied_start, occupied_end)
    return round((amount or 0.0) * factor, precision)


def describe(method, period_start, period_end, occupied_start, occupied_end):
    """Human-readable explanation of a proration decision.

    Written onto the billing obligation so an invoice is always explainable to
    a tenant without anyone re-deriving the maths.
    """
    factor = proration_factor(
        method, period_start, period_end, occupied_start, occupied_end)
    if factor >= 1.0:
        return ''
    if factor <= 0.0:
        return 'Not charged (outside the liable period).'
    start = max(period_start, occupied_start or period_start)
    end = min(period_end, occupied_end or period_end)
    if method == 'thirty_day':
        occupied = thirty_day_count(start, end)
        total = thirty_day_count(period_start, period_end)
        unit = 'day (30-day month)'
    else:
        occupied = days_inclusive(start, end)
        total = days_inclusive(period_start, period_end)
        unit = 'day'
    return 'Prorated %s/%s %ss (%s to %s) = %.4f of the period.' % (
        occupied, total, unit, start, end, factor)
