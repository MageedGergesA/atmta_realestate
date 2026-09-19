"""Keep stored values that depend on today's date current.

Most date-derived values are computed for today when read and never stored:
days overdue and the ageing bucket, days to expiry, whether a party is active,
a unit turn's vacant days. A few must stay stored, because Odoo groups,
aggregates or cascades on them:

* allocation occupancy -- and through it property occupancy, the legacy
  property state and lease occupancy -- changes on the allocation's start date,
  the day after its end date and its move-out date;
* property availability changes when an allocation starts or ends and when the
  unit's availability window opens or closes;
* the lease expiry bucket changes 180, 90, 60 and 30 days before the end date
  and the day after it;
* the lease collection status changes the day after an outstanding
  obligation's due date;
* the lease escalation summary and current rent change on an escalation's
  effective date.

Nothing writes to a record when a date merely passes. This job finds the
records whose boundary fell since it last ran, from the date columns
themselves, and recomputes only those: a daily run touches the leases and units
whose day it is, not the whole estate, and a run after a gap covers the gap.

"Today" is the cron user's date, as every stored compute uses the date of
whoever triggers it.
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

from .lease_states import CLOSED_LIFECYCLE

_logger = logging.getLogger(__name__)

LAST_RUN_PARAM = 'atmta_real_estate.calendar_refresh_date'
#: Days before a lease's end date at which it moves to the next expiry bucket.
EXPIRY_BOUNDARIES = (180, 90, 60, 30)


def _after_until(field, after, until):
    """``after < field <= until``."""
    return ['&', (field, '>', after), (field, '<=', until)]


def _from_until(field, start, until):
    """``start <= field <= until``."""
    return ['&', (field, '>=', start), (field, '<=', until)]


class RentalCalendarRefresh(models.AbstractModel):
    _name = 'realestate.calendar.refresh'
    _description = "Rental: refresh values that depend on today's date"

    @api.model
    def _cron_refresh(self):
        """Refresh everything whose boundary fell since the last run."""
        Param = self.env['ir.config_parameter'].sudo()
        today = fields.Date.context_today(self)
        last_run = fields.Date.to_date(Param.get_param(LAST_RUN_PARAM) or False)
        since = last_run or today - timedelta(days=1)
        if since >= today:
            return {}
        counts = self._refresh_window(since, today)
        Param.set_param(LAST_RUN_PARAM, fields.Date.to_string(today))
        return counts

    @api.model
    def _refresh_window(self, since, today):
        """Recompute the stored values whose boundary is a day in ``(since, today]``."""
        env = self.env(su=True)
        yesterday = today - timedelta(days=1)
        Allocation = env['realestate.contract.property.line']
        Property = env['realestate.property'].with_context(active_test=False)
        Contract = env['realestate.contract']
        Obligation = env['realestate.contract.payment']
        Escalation = env['realestate.rent.escalation.rule']

        # Occupancy: starts occupying on its start date, stops the day after
        # its end date, is vacated on its move-out date. Marking the start
        # date modified recomputes occupancy and everything built on it.
        allocations = Allocation.search(
            ['|', '|']
            + _after_until('start_date', since, today)
            + _from_until('end_date', since, yesterday)
            + _after_until('move_out_date', since, today))
        allocations.modified(['start_date'])

        units = Property.search(
            ['|']
            + _after_until('available_from', since, today)
            + _from_until('available_until', since, yesterday))
        units.modified(['available_from'])

        expiry = _from_until('end_date', since, yesterday)
        for days in EXPIRY_BOUNDARIES:
            expiry = ['|'] + expiry + _after_until(
                'end_date', since + timedelta(days=days), today + timedelta(days=days))
        leases = Contract.search(
            expiry + [('lifecycle_state', 'not in', list(CLOSED_LIFECYCLE))])
        env.add_to_compute(Contract._fields['expiry_bucket'], leases)

        overdue = Obligation.search(
            Obligation._outstanding_domain() + _from_until('date_due', since, yesterday))
        env.add_to_compute(Contract._fields['payment_status'], overdue.contract_id)

        escalations = Escalation.search(_after_until('effective_date', since, today))
        escalations.modified(['effective_date'])

        env.flush_all()
        counts = {
            'allocations': len(allocations),
            'units': len(units),
            'expiry_leases': len(leases),
            'overdue_leases': len(overdue.contract_id),
            'escalations': len(escalations),
        }
        _logger.info("Rental date-based status refreshed for %s to %s: %s",
                     since, today, counts)
        return counts

    @api.model
    def _refresh_everything(self):
        """Recompute every stored date-derived value, then mark today as done.

        For upgrades and repairs; the daily job refreshes only boundaries.
        """
        env = self.env(su=True)
        Contract = env['realestate.contract'].with_context(active_test=False)
        env['realestate.contract.property.line'].search([]).modified(['start_date'])
        env['realestate.property'].with_context(active_test=False).search([]).modified(
            ['available_from'])
        env['realestate.rent.escalation.rule'].search([]).modified(['effective_date'])
        leases = Contract.search([])
        env.add_to_compute(Contract._fields['expiry_bucket'], leases)
        env.add_to_compute(Contract._fields['payment_status'], leases)
        env.flush_all()
        env['ir.config_parameter'].set_param(
            LAST_RUN_PARAM, fields.Date.to_string(fields.Date.context_today(self)))
        return len(leases)
