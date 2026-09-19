"""0.9 post-migration: units are allocations.

Rental re-architecture, batch 3. A unit's rental status is now computed from its
lease allocations instead of legacy line and lease states. Odoo does not
recompute a stored field when only its dependencies change, so it is recomputed
here for every unit. The two retired legacy line-status jobs are removed by the
module update itself.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Property = env['realestate.property'].with_context(active_test=False)
    units = Property.search([])
    before = dict(zip(units.ids, units.mapped('rental_status')))
    env.add_to_compute(Property._fields['rental_status'], units)
    env.flush_all()
    changed = units.filtered(lambda unit: unit.rental_status != before[unit.id])
    _logger.info(
        "Units as allocations: rental status recomputed for %s unit(s); "
        "%s changed.", len(units), len(changed))
    for unit in changed:
        _logger.info("Units as allocations: %s is now %s (was %s).",
                     unit.display_name, unit.rental_status, before[unit.id])
