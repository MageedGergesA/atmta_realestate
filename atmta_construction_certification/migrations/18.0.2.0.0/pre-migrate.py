"""Re-point the screens that came down in Wave 21.

The views and window actions for this module's models used to be declared by
`real_estate_construction`. They are declared here now, and on a database where
this module is already installed `pre_init_hook` does not fire -- a hook runs
once, at install. Only a migration runs on an upgrade.

Without it the move still "works" and still counts right, which is exactly what
makes it dangerous: this module looks its identifiers up under its own name,
does not find them, and creates *new* view records; the monolith's data reload
then drops the identifiers it still owns and deletes the originals. Measured
before this script existed: the BOQ form view came back with a different
database id. Anything that pointed at the old row -- a customer's inherited
view, a saved filter, an attachment -- would have been pointing at a deleted
record.

Re-pointing first means the load finds the rows and updates them in place, so
the view keeps its id and everything layered on it survives. This module is a
dependency of `real_estate_construction`, so it upgrades first and the window
never opens.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

from odoo.addons.atmta_construction_certification.hooks import VIEW_XMLIDS

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_certification'


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, VIEW_XMLIDS))
    _logger.info("Wave 21: %s screen identifiers re-pointed from %s to %s",
                 cr.rowcount, OLD, NEW)
