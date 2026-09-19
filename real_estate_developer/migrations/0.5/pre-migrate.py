# -*- coding: utf-8 -*-
"""0.4 → 0.5 — keep every instalment's amount through the obligation split.

Up to 0.4, ``realestate.sale.installment.amount`` was a plain stored field and
the only record of what each instalment is for. 0.5 splits it into
``original_amount`` (what was signed) and ``adjustment_amount`` (approved
changes), and makes ``amount`` a stored mirror of their sum through the
``current_amount`` compute.

Left to the ORM, the two new columns are created empty and ``current_amount``
is computed for every row, and the compute writes ``amount`` as well: every
existing instalment would become an obligation for zero, and the next invoicing
run would bill it for zero.

The columns are therefore created here, before the ORM sees the table, and
filled from the amount the database already holds. With ``current_amount``
already present and filled, the ORM has nothing to recompute and ``amount`` is
never touched.

Only empty values are filled, so a database that already carries the columns,
such as one installed from 0.5 code before its version was raised, is left as
it is.
"""

import logging

_logger = logging.getLogger(__name__)

TABLE = 'realestate_sale_installment'


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", ('public.%s' % table,))
    return cr.fetchone()[0] is not None


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version or not _table_exists(cr, TABLE):
        return

    for column in ('original_amount', 'adjustment_amount', 'current_amount'):
        if not _column_exists(cr, TABLE, column):
            cr.execute('ALTER TABLE %s ADD COLUMN %s numeric' % (TABLE, column))

    cr.execute("""
        UPDATE realestate_sale_installment
           SET original_amount = amount
         WHERE original_amount IS NULL AND amount IS NOT NULL
    """)
    originals = cr.rowcount
    cr.execute("""
        UPDATE realestate_sale_installment
           SET adjustment_amount = 0
         WHERE adjustment_amount IS NULL
    """)
    cr.execute("""
        UPDATE realestate_sale_installment
           SET current_amount = COALESCE(original_amount, 0) + adjustment_amount
         WHERE current_amount IS NULL
    """)
    currents = cr.rowcount

    cr.execute("""
        SELECT count(*) FROM realestate_sale_installment
         WHERE amount IS NOT NULL AND current_amount <> amount
    """)
    mismatched = cr.fetchone()[0]
    _logger.info(
        "real_estate_developer 0.5: %s instalment(s) given their original "
        "amount, %s their current amount; %s already differ from the stored "
        "amount through adjustments.", originals, currents, mismatched)
