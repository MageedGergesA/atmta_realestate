"""0.11 post-migration: recompute every lease's stored money totals.

``total_scheduled``, ``total_paid`` and ``balance_due`` of a lease billed per
period summed base rent and never counted a payment, so a paid lease showed
nothing paid and its whole term still due. The compute now reads each billing
obligation's own invoice figures. Stored values do not recompute themselves when
a compute changes, so every lease is recomputed once here.

A termination's ``outstanding_balance`` (and the ``net_settlement`` built on it)
also counted periods after its effective date; those are recomputed too. Only
these display figures are written; no obligation, invoice or payment is touched.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

FIELDS = ('total_scheduled', 'total_paid', 'balance_due')
TERMINATION_FIELDS = ('outstanding_balance', 'deposit_held', 'net_settlement')


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False})
    leases = env['realestate.contract'].search([])
    for name in FIELDS:
        env.add_to_compute(leases._fields[name], leases)
    leases.flush_recordset(list(FIELDS))
    _logger.info("0.11: recomputed lease totals on %s lease(s)", len(leases))

    terminations = env['realestate.contract.termination'].search([])
    for name in TERMINATION_FIELDS:
        env.add_to_compute(terminations._fields[name], terminations)
    terminations.flush_recordset(list(TERMINATION_FIELDS))
    _logger.info("0.11: recomputed settlement figures on %s termination(s)",
                 len(terminations))
