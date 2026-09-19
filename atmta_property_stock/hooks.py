# -*- coding: utf-8 -*-
"""Adopt the real-estate stock locations from `atmta_real_estate`.

The tree moved here so Developer stops depending on the Rental app for the root
location. On a database that already has them, the identifiers are handed over
before this module's data loads: the records are then updated in place instead
of a second `Real Estate` tree being created next to the one holding the quants.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = 'atmta_real_estate'
NEW = 'atmta_property_stock'
XMLIDS = (
    'stock_location_re_root',
    'stock_location_re_unassigned',
    'stock_location_re_reserved',
    'stock_location_re_sold',
)


def pre_init_hook(env):
    env.cr.execute("""
        UPDATE ir_model_data
           SET module = %s
         WHERE module = %s AND model = 'stock.location' AND name = ANY(%s)
           AND NOT EXISTS (
               SELECT 1 FROM ir_model_data existing
                WHERE existing.module = %s AND existing.name = ir_model_data.name)
    """, (NEW, OLD, list(XMLIDS), NEW))
    if env.cr.rowcount:
        _logger.info("ATMTA: %s real-estate stock location(s) handed over to %s.",
                     env.cr.rowcount, NEW)


def post_init_hook(env):
    """Place quants for any units that already exist when this module installs.

    On a database where units were created before the lifecycle was available,
    they are not storable yet and hold no quant; this brings them into step.
    Safe on an empty database and safe to repeat."""
    env['realestate.property']._backfill_unit_stock()
