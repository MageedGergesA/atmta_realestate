"""0.10 end-migration: recompute every stored date-derived value once.

Values stored before this version were computed on the day their record last
changed, so any of them can be stale. This recomputes them all from today; from
here the daily "Rental: refresh date-based status" job keeps them current by
refreshing boundaries only.

It runs at the end of the update, not in ``post-migrate.py``: a post-migration
runs while this module loads, before the modules that extend the property.
Availability reads fields those modules add -- ``real_estate_developer``'s
``project_id`` and ``phase_id`` decide whether a unit in planning is blocked --
so recomputing earlier stored the answer for a registry without them and marked
units of a development in planning available. End-migrations run once every
module is loaded.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('atmta_real_estate.cron_recompute_availability',
                   raise_if_not_found=False)
    if cron:
        cron.name = 'Rental: refresh date-based status'
    leases = env['realestate.calendar.refresh']._refresh_everything()
    _logger.info("Date-based status: recomputed for every unit, allocation, "
                 "escalation and %s lease(s).", leases)
