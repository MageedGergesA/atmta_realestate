"""0.12 post-migration: drop the withdrawn Hijri columns.

``hijri_date_due`` and ``hijri_date_due_deadline`` were stored computed Char
fields on the billing obligation. They were the module's only reason to depend
on the ``hijridate`` package, and that dependency stopped Rental installing at
all on a host without it. Both fields are gone; Odoo does not drop the columns
of a removed stored field, so they are dropped here.

The dates they held were derived from ``date_due``, which is untouched, so
nothing is lost that cannot be recomputed. Only these two columns are dropped.
"""

import logging

_logger = logging.getLogger(__name__)

COLUMNS = ('hijri_date_due', 'hijri_date_due_deadline')
TABLE = 'realestate_contract_payment'


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = %s AND column_name IN %s
    """, (TABLE, COLUMNS))
    present = [row[0] for row in cr.fetchall()]
    for column in present:
        cr.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (TABLE, column))
    _logger.info("0.12: dropped %s from %s", present or 'nothing', TABLE)
