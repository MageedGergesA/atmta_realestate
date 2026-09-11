"""Re-point what came down in Wave 23.

Wave 14 and Wave 15 left the change-event link in `real_estate_construction`,
because change events were still declared there. Wave 16 gave them their own
module and Wave 23 declared the dependency, so the link -- and the screens that
render it -- finally sit beside the models they belong to.

On a database where this module is already installed `pre_init_hook` does not
fire: a hook runs once, at install. Only a migration runs on an upgrade. Wave
21 proved what happens without one -- the records are deleted and recreated
with new database ids, every total still matches, and anything layered on the
old rows is silently orphaned.

Re-pointing first means the load finds the rows and updates them in place. This
module is a dependency of `real_estate_construction`, so it upgrades first and
the window never opens.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

from odoo.addons.atmta_construction_site.hooks import W23_XMLIDS

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_site'


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, W23_XMLIDS))
    _logger.info("Wave 23: %s screen identifiers re-pointed to %s",
                 cr.rowcount, NEW)
