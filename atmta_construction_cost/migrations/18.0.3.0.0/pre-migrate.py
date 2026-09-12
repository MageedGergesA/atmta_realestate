"""Re-point the budget screens that came down in Wave 24.

`budget_views.xml` renders a budget, the contract package beside it and the
cost report over both. It lived in `real_estate_construction` because it spans
two capabilities; this module depends on contract already, so it can hold the
file whole -- no split, no inherited-view indirection.

On a database where this module is already installed `pre_init_hook` does not
fire: a hook runs once, at install. Wave 21 measured what that costs -- the
records are deleted and recreated with new database ids while every total still
matches, orphaning anything layered on them.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

from odoo.addons.atmta_construction_cost.hooks import W24_XMLIDS

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_cost'


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, W24_XMLIDS))
    _logger.info("Wave 24: %s screen identifiers re-pointed to %s",
                 cr.rowcount, NEW)
