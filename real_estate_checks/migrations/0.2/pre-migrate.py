# -*- coding: utf-8 -*-
"""M35 — pre-migration: look before touching anything.

This module has never been migrated in place: 0.1 shipped with no
`migrations/` directory at all, so this is the first upgrade script it has ever
run. Production databases hold live cheques, deposit slips and bounce records
with ids that Developer, the portal and Accounting all reference.

Nothing here changes data. It **reports**, so that a data problem surfaces as a
log line a human can act on rather than as a failed `ALTER TABLE` halfway
through an upgrade. The one write it performs is adding new columns with
conservative defaults, which is additive and reversible.
"""

import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _report_identity_duplicates(cr):
    """The new identity key adds `account_number`. Say what would collide.

    The unique index itself is created by `RealEstateCheck.init()`, which
    performs the same probe and skips creation if it finds anything. This runs
    first purely so the warning appears at the top of the upgrade log, where an
    operator will see it, rather than buried in the model-loading phase.
    """
    cr.execute("""
        SELECT array_agg(name) AS refs, count(*) AS n
          FROM realestate_check
         WHERE state IN ('draft','registered','deposited','cleared','bounced')
         GROUP BY company_id, partner_id, bank_id,
                  COALESCE(account_number, ''), COALESCE(check_number, '')
        HAVING count(*) > 1
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info("real_estate_checks 0.2: no duplicate cheque identities.")
        return
    _logger.error(
        "real_estate_checks 0.2: %s group(s) of live cheques share an identity "
        "(company, drawer, bank, account, number). NOTHING HAS BEEN CHANGED — "
        "the uniqueness index will be skipped and retried on the next upgrade. "
        "Resolve these:\n%s",
        len(rows),
        '\n'.join('  - %s' % ', '.join(refs) for refs, _n in rows[:50]))


def _report_company_inconsistencies(cr):
    """M29 introduces company constraints and record rules.

    A row whose company disagrees with its contract's or its journal's would
    start raising on the first write, or disappear behind a record rule. Both
    are worse than being told about it now. This never reassigns a company:
    guessing which one is right is not a migration's business.
    """
    checks = [
        ("cheque vs sale contract", """
            SELECT c.name, c.company_id, sc.company_id
              FROM realestate_check c
              JOIN realestate_sale_contract sc ON sc.id = c.sale_contract_id
             WHERE c.company_id IS DISTINCT FROM sc.company_id
             LIMIT 50"""),
        ("cheque vs deposit slip", """
            SELECT c.name, c.company_id, d.company_id
              FROM realestate_check c
              JOIN realestate_check_deposit d ON d.id = c.deposit_id
             WHERE c.company_id IS DISTINCT FROM d.company_id
             LIMIT 50"""),
        ("deposit slip vs bank journal", """
            SELECT d.name, d.company_id, j.company_id
              FROM realestate_check_deposit d
              JOIN account_journal j ON j.id = d.journal_id
             WHERE d.company_id IS DISTINCT FROM j.company_id
             LIMIT 50"""),
    ]
    for label, query in checks:
        cr.execute(query)
        rows = cr.fetchall()
        if rows:
            _logger.error(
                "real_estate_checks 0.2: %s row(s) with a company mismatch "
                "(%s). Nothing reassigned — a migration must not guess which "
                "company is correct. Fix these before relying on multi-company "
                "isolation:\n%s",
                len(rows), label,
                '\n'.join('  - %s (%s vs %s)' % row for row in rows))
    cr.execute("SELECT count(*) FROM realestate_check WHERE company_id IS NULL")
    orphans = cr.fetchone()[0]
    if orphans:
        _logger.error(
            "real_estate_checks 0.2: %s cheque(s) have no company. The new "
            "record rules do NOT admit `company_id = False`, so these will be "
            "invisible until a company is set. Left untouched deliberately.",
            orphans)


def _report_legacy_cleared(cr):
    """Cheques 0.1 marked cleared without any bank evidence.

    0.2 derives clearance from Odoo's reconciliation. Historical rows keep
    their `cleared` state — rewriting settled history would be far worse than
    an imprecise past — but the operator should know how many of them rest on
    a button press rather than a bank match.
    """
    cr.execute("""
        SELECT count(*) FROM realestate_check
         WHERE state = 'cleared' AND payment_id IS NULL
    """)
    no_payment = cr.fetchone()[0]
    cr.execute("SELECT count(*) FROM realestate_check WHERE state = 'cleared'")
    total = cr.fetchone()[0]
    if total:
        _logger.warning(
            "real_estate_checks 0.2: %s of %s cleared cheque(s) carry no "
            "accounting payment. In 0.1 'cleared' meant a treasurer pressed a "
            "button; from 0.2 it means Odoo reconciled a payment. These rows "
            "are left EXACTLY as they are — historical states are not "
            "rewritten — but they are not evidence of bank clearance.",
            no_payment, total)


def _add_columns(cr):
    """Add the new columns up front with safe defaults.

    Odoo would create them anyway, but doing it here lets each one get a
    deliberate default in a single pass rather than a per-row ORM write on a
    100,000-cheque table.
    """
    additions = [
        ('realestate_check', 'active', 'boolean', 'true'),
        ('realestate_check', 'received_date', 'date', 'create_date::date'),
        ('realestate_check', 'presented_date', 'date', None),
        ('realestate_check', 'cleared_date', 'date', None),
        ('realestate_check', 'bounce_date', 'date', None),
        ('realestate_check', 'cancelled_date', 'date', None),
        ('realestate_check', 'allocated_amount', 'numeric', '0'),
        ('realestate_check', 'unapplied_amount', 'numeric', '0'),
        ('realestate_check', 'allocation_count', 'integer', '0'),
        ('realestate_check', 'presentation_count', 'integer', '0'),
        ('realestate_check', 'bounce_count', 'integer', '0'),
        ('realestate_check', 'replacement_generation', 'integer', '0'),
        ('realestate_check', 'is_bank_matched', 'boolean', 'false'),
    ]
    for table, column, sql_type, default in additions:
        if _column_exists(cr, table, column):
            continue
        cr.execute('ALTER TABLE %s ADD COLUMN "%s" %s' % (table, column, sql_type))
        if default:
            cr.execute('UPDATE %s SET "%s" = %s WHERE "%s" IS NULL'
                       % (table, column, default, column))
        _logger.info("real_estate_checks 0.2: added %s.%s", table, column)

    # `deposit_date` was a stored RELATED field on `deposit_id.deposit_date`,
    # so cancelling a slip erased when the cheque had been presented. It
    # becomes a plain stored Date holding the same values, which Odoo migrates
    # in place without any action here — but the presentation date is seeded
    # from it so the new field is not blank for historical rows.
    if _column_exists(cr, 'realestate_check', 'presented_date'):
        cr.execute("""
            UPDATE realestate_check
               SET presented_date = deposit_date
             WHERE presented_date IS NULL AND deposit_date IS NOT NULL
        """)


def _drop_legacy_constraint(cr):
    """0.1's blanket uniqueness constraint is replaced by a partial index.

    Odoo drops constraints removed from `_sql_constraints` by itself, but only
    on a clean pass; dropping it explicitly here means the new index is never
    blocked by the old rule during the same upgrade.
    """
    cr.execute("""
        ALTER TABLE realestate_check
        DROP CONSTRAINT IF EXISTS realestate_check_check_number_bank_uniq
    """)


def migrate(cr, version):
    if not version:
        return
    _logger.info("real_estate_checks: pre-migration to 0.2 starting "
                 "(from %s). Reporting only; no data is modified.", version)
    _report_identity_duplicates(cr)
    _report_company_inconsistencies(cr)
    _report_legacy_cleared(cr)
    _add_columns(cr)
    _drop_legacy_constraint(cr)
    _logger.info("real_estate_checks: pre-migration to 0.2 complete.")
