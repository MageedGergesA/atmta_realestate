# -*- coding: utf-8 -*-
"""Adopt `realestate.account.tools` from `atmta_real_estate`.

The model moved here so the modules that only needed two accounting helpers
stop depending on the Rental app. On a database where Rental already declared
it, the identifier is handed over before this module's own definition loads:
`ir_model_data` carries UNIQUE(module, name), so without this the load would
either collide or leave a stale row for `_process_end` to delete along with the
record.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = 'atmta_real_estate'
NEW = 'atmta_account_tools'
XMLID = 'model_realestate_account_tools'


def pre_init_hook(env):
    env.cr.execute("""
        UPDATE ir_model_data
           SET module = %s
         WHERE module = %s AND model = 'ir.model' AND name = %s
           AND NOT EXISTS (
               SELECT 1 FROM ir_model_data existing
                WHERE existing.module = %s AND existing.name = %s)
    """, (NEW, OLD, XMLID, NEW, XMLID))
    if env.cr.rowcount:
        _logger.info("ATMTA: %s.%s handed over to %s.", OLD, XMLID, NEW)
