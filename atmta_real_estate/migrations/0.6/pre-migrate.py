"""Snapshot the legacy state columns before the schema changes (Phase 34).

``realestate_property.state`` and ``realestate_contract.state`` both stop being
plain stored columns in 0.6 and become **stored computed** bridges. The moment
Odoo applies the new field definitions it marks them for recomputation, and the
recompute would overwrite every existing value from the (default) dimensions --
silently turning a database full of rented and sold units into "available".

So the values are copied into dedicated backup columns here, in pre-migrate,
which runs before any schema work. ``post-migrate.py`` reads them back and maps
them onto the new status dimensions.

The backup columns are deliberately **kept** after the migration rather than
dropped. They cost almost nothing, they make the mapping auditable, and if the
mapping turns out to be wrong for a particular customer's data the original
values are still there to re-derive from. Non-destructive beats tidy.
"""

import logging

_logger = logging.getLogger(__name__)

BACKUPS = (
    ('realestate_property', 'state', 're_legacy_state_backup'),
    ('realestate_contract', 'state', 're_legacy_state_backup'),
    ('realestate_contract_line', 'state', 're_legacy_state_backup'),
)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    if not version:
        # Fresh install -- there is nothing to preserve.
        return

    for table, source, backup in BACKUPS:
        if not _table_exists(cr, table):
            _logger.warning("Table %s missing; skipping state snapshot.", table)
            continue
        if not _column_exists(cr, table, source):
            _logger.warning(
                "Column %s.%s missing; skipping state snapshot.", table, source)
            continue
        if _column_exists(cr, table, backup):
            _logger.info("Snapshot %s.%s already exists; leaving it alone.",
                         table, backup)
            continue

        cr.execute(
            'ALTER TABLE "%s" ADD COLUMN "%s" VARCHAR' % (table, backup))
        cr.execute(
            'UPDATE "%s" SET "%s" = "%s"' % (table, backup, source))
        cr.execute('SELECT COUNT(*) FROM "%s" WHERE "%s" IS NOT NULL'
                   % (table, backup))
        count = cr.fetchone()[0]
        _logger.info("Snapshotted %s rows of %s.%s into %s.",
                     count, table, source, backup)
