"""0.10 pre-migration: remove the legacy unit line model and the pivot reports.

Rental re-architecture, dead-code batch. Odoo's upgrade utility
(``odoo.upgrade.util``, whose ``remove_model`` does this in one call) is not
installed on this server, so the removal is explicit and verified after the
upgrade.

While the legacy table still exists, this step:

* records every legacy unit line in ``re_legacy_contract_line_backup``,
  together with the allocation that mirrors it; line-level price rules and
  payment plans in ``re_legacy_contract_line_rule_backup``; and billing
  obligations that named a line in ``re_legacy_payment_line_backup``. The
  backup tables are kept for audit, as the 0.6 and 0.7 snapshots are;
* turns allocations that mirrored a legacy line into ordinary allocations;
* gives an obligation that named its unit only through a legacy line that unit
  on the obligation itself;
* drops the SQL views of the two retired pivot reports, which read the legacy
  table.

``post-migrate.py`` then recreates any unmirrored legacy line as an allocation
and drops the legacy tables. Odoo deletes the removed models' metadata (models,
fields, access rows, views, actions and XML IDs) at the end of the update,
because nothing loads it any more; it does not drop the table of a model that
is missing from the registry, which is why the tables are dropped explicitly.
"""

import logging

_logger = logging.getLogger(__name__)

LINE_TABLE = 'realestate_contract_line'
LINE_BACKUP = 're_legacy_contract_line_backup'
RULE_BACKUP = 're_legacy_contract_line_rule_backup'
PAYMENT_BACKUP = 're_legacy_payment_line_backup'
LINE_RELATION_TABLES = (
    ('rel_contract_line_increment_rule_inc', 'increase rule'),
    ('rel_contract_line_increment_rule_disc', 'discount rule'),
    ('rel_contract_line_payment_plan', 'payment plan'),
)
PIVOT_VIEWS = ('realestate_report_contract', 'realestate_report_contract_line')


def _table_kind(cr, name):
    cr.execute("SELECT table_type FROM information_schema.tables WHERE table_name = %s",
               (name,))
    row = cr.fetchone()
    return row and row[0]


def _columns(cr, table):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s",
               (table,))
    return {name for (name,) in cr.fetchall()}


def _backup_lines(cr):
    if _table_kind(cr, LINE_BACKUP):
        _logger.info("Legacy unit lines: backup already recorded.")
        return
    mirror = ("(SELECT min(a.id) FROM realestate_contract_property_line a "
              "WHERE a.legacy_line_id = l.id)"
              if 'legacy_line_id' in _columns(cr, 'realestate_contract_property_line')
              else "NULL::integer")
    cr.execute("""
        CREATE TABLE {backup} AS
        SELECT l.id, l.contract_id, l.property_id, l.price, l.start_date,
               l.end_date, l.state, l.notes, {mirror} AS allocation_id
          FROM {table} l
    """.format(backup=LINE_BACKUP, table=LINE_TABLE, mirror=mirror))
    cr.execute("SELECT count(*), count(allocation_id) FROM %s" % LINE_BACKUP)
    total, mirrored = cr.fetchone()
    _logger.info("Legacy unit lines: %s recorded, %s already mirrored by an allocation.",
                 total, mirrored)


def _backup_line_relations(cr):
    if _table_kind(cr, RULE_BACKUP):
        return
    cr.execute("CREATE TABLE %s (contract_line_id integer, related_id integer, kind varchar)"
               % RULE_BACKUP)
    for table, kind in LINE_RELATION_TABLES:
        if not _table_kind(cr, table):
            continue
        other = sorted(_columns(cr, table) - {'contract_line_id'})
        if len(other) != 1:
            _logger.warning("Legacy unit lines: %s has unexpected columns %s; not recorded.",
                            table, other)
            continue
        cr.execute("INSERT INTO {backup} SELECT contract_line_id, {other}, %s FROM {table}"
                   .format(backup=RULE_BACKUP, other=other[0], table=table), (kind,))
        if cr.rowcount:
            _logger.warning(
                "Legacy unit lines: %s %s link(s) on legacy lines are not carried "
                "over (line-level pricing was only applied by the retired schedule "
                "generator); recorded in %s.", cr.rowcount, kind, RULE_BACKUP)


def _carry_obligation_units(cr):
    if 'contract_line_id' not in _columns(cr, 'realestate_contract_payment'):
        return
    if not _table_kind(cr, PAYMENT_BACKUP):
        cr.execute("""
            CREATE TABLE {backup} AS
            SELECT p.id AS payment_id, p.contract_line_id, p.property_id
              FROM realestate_contract_payment p
             WHERE p.contract_line_id IS NOT NULL
        """.format(backup=PAYMENT_BACKUP))
    cr.execute("""
        UPDATE realestate_contract_payment p
           SET property_id = l.property_id
          FROM {table} l
         WHERE p.contract_line_id = l.id
           AND p.property_id IS NULL
    """.format(table=LINE_TABLE))
    _logger.info("Legacy unit lines: %s billing obligation(s) now name their unit directly.",
                 cr.rowcount)


def _release_mirrors(cr):
    # Odoo runs every applicable pre-migration before it creates new tables, so
    # a database upgraded from before 0.6 has no allocation table yet. No
    # allocation can mirror a legacy line there, and there is nothing to release.
    if 'origin' not in _columns(cr, 'realestate_contract_property_line'):
        _logger.info("Legacy unit lines: no allocation table yet; no mirrors to release.")
        return
    cr.execute("UPDATE realestate_contract_property_line SET origin = 'manual' "
               "WHERE origin = 'legacy_line'")
    _logger.info("Legacy unit lines: %s mirroring allocation(s) are now ordinary allocations.",
                 cr.rowcount)


def _drop_pivot_views(cr):
    for view in PIVOT_VIEWS:
        if _table_kind(cr, view) == 'VIEW':
            cr.execute('DROP VIEW "%s"' % view)
            _logger.info("Retired pivot report: dropped SQL view %s.", view)


def migrate(cr, version):
    if not version:
        return
    _drop_pivot_views(cr)
    if not _table_kind(cr, LINE_TABLE):
        _logger.info("Legacy unit lines: table already gone.")
        return
    _backup_lines(cr)
    _backup_line_relations(cr)
    _carry_obligation_units(cr)
    _release_mirrors(cr)
