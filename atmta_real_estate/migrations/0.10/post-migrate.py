"""0.10 post-migration: finish removing the legacy unit line model.

``pre-migrate.py`` recorded every legacy unit line before the model left the
registry. Here, with the allocation model loaded:

1. every recorded line that no allocation mirrors is carried over as an
   allocation (``realestate.contract._restore_legacy_unit_lines``); lines that
   cannot be carried over are logged and stay in the backup table;
2. the legacy tables are dropped. Odoo removes the models' metadata at the end
   of the update, but it does not drop the table of a model missing from the
   registry, nor the relation tables of its many2many fields.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

LINE_BACKUP = 're_legacy_contract_line_backup'
LEGACY_TABLES = (
    'rel_contract_line_increment_rule_inc',
    'rel_contract_line_increment_rule_disc',
    'rel_contract_line_payment_plan',
    'realestate_contract_line',
)
#: Foreign keys Odoo recorded for the removed fields that pointed at the line.
LEGACY_FOREIGN_KEY_SUFFIXES = ('_contract_line_id_fkey', '_legacy_line_id_fkey')


def _table_exists(cr, name):
    cr.execute("SELECT to_regclass(%s)", (name,))
    return cr.fetchone()[0] is not None


def _restore_unmirrored_lines(env):
    cr = env.cr
    if not _table_exists(cr, LINE_BACKUP):
        return
    cr.execute("""
        SELECT id, contract_id, property_id, price, start_date, end_date,
               state, notes, allocation_id
          FROM %s ORDER BY id
    """ % LINE_BACKUP)
    rows = cr.dictfetchall()
    if not rows:
        _logger.info("Legacy unit lines: none recorded, nothing to carry over.")
        return
    report = env['realestate.contract']._restore_legacy_unit_lines(rows)
    _logger.info(
        "Legacy unit lines: %s recorded; %s carried over as allocations, %s already "
        "allocated, %s skipped, %s could not be carried over.",
        len(rows), len(report['created']), len(report['already_allocated']),
        len(report['skipped']), len(report['failed']))
    for label, reason in report['skipped']:
        _logger.info("Legacy unit lines: skipped %s: %s", label, reason)
    for label, reason in report['failed']:
        _logger.warning(
            "Legacy unit lines: %s needs manual review (kept in %s): %s",
            label, LINE_BACKUP, reason)


def _drop_legacy_tables(cr):
    for table in LEGACY_TABLES:
        if _table_exists(cr, table):
            cr.execute('DROP TABLE "%s" CASCADE' % table)
            _logger.info("Legacy unit lines: dropped table %s.", table)
    cr.execute("DELETE FROM ir_model_relation WHERE name IN %s", (LEGACY_TABLES[:3],))
    cr.execute("""
        DELETE FROM ir_model_constraint
         WHERE type = 'f' AND (name LIKE %s OR name LIKE %s)
    """, tuple('%' + suffix for suffix in LEGACY_FOREIGN_KEY_SUFFIXES))


#: Date-derived fields that are now computed for today instead of stored.
NO_LONGER_STORED = (
    ('realestate_contract_payment', 'days_overdue'),
    ('realestate_contract_payment', 'overdue_bucket'),
    ('realestate_contract', 'days_to_expiry'),
    ('realestate_unit_turn', 'vacant_days'),
    ('realestate_contract_party', 'is_active_party'),
)


def _drop_stale_date_columns(cr):
    """Their last stored values were already out of date; nothing reads them."""
    for table, column in NO_LONGER_STORED:
        cr.execute('ALTER TABLE "%s" DROP COLUMN IF EXISTS "%s"' % (table, column))


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _restore_unmirrored_lines(env)
    _drop_legacy_tables(cr)
    _drop_stale_date_columns(cr)
    # The one-off recompute of date-based status is in end-migrate.py: it must
    # run after every module is loaded (see there).
