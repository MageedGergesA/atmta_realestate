# -*- coding: utf-8 -*-

from . import controllers
from . import models
from . import wizard


def post_init_hook(env):
    """Align property_code sequence after install (in case demo data loaded)
    and place stock quants for any units created during install."""
    env['realestate.property']._sync_property_code_sequence()
    env['realestate.property']._backfill_unit_stock()