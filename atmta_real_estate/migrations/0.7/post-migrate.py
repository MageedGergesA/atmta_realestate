"""0.7 post-migration: finish moving leases off legacy pricing configuration.

Two independent steps, each safe to re-run:

1. **Billing frequency from payment plans.** ``pre-migrate.py`` translated
   Rental's legacy payment plans into billing frequencies and stored the result in
   ``realestate_contract.re_legacy_plan_frequency`` before the schema changed.
   By now ``billing_frequency`` exists on every database, so the translation is
   applied here. The snapshot column is kept for audit, as the 0.6 state
   snapshots are.

2. **Legacy price rules to escalations and incentives.** Only the retired legacy
   schedule generator applied ``realestate.contract.increment.rule``. Each open
   lease's increases become rent escalations and its discounts become incentives
   where that reproduces the rent the generator billed; everything else is
   flagged on the lease and in the log. The logic lives on the lease
   (``_convert_legacy_price_rules``) so it is tested like any other code.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

SNAPSHOT_COLUMN = 're_legacy_plan_frequency'


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _apply_plan_frequencies(cr):
    if not (_column_exists(cr, 'realestate_contract', SNAPSHOT_COLUMN)
            and _column_exists(cr, 'realestate_contract', 'billing_frequency')):
        _logger.info("Payment plans: no recorded billing frequencies to apply.")
        return
    cr.execute("""
        UPDATE realestate_contract
           SET billing_frequency = %s
         WHERE %s IS NOT NULL
           AND billing_frequency IS DISTINCT FROM %s
    """ % (SNAPSHOT_COLUMN, SNAPSHOT_COLUMN, SNAPSHOT_COLUMN))
    changed = cr.rowcount
    cr.execute("SELECT count(*) FROM realestate_contract WHERE %s IS NOT NULL"
               % SNAPSHOT_COLUMN)
    recorded = cr.fetchone()[0]
    _logger.info(
        "Payment plans: %s lease(s) had a frequency recorded from their plan; "
        "%s billing frequenc(ies) changed to match it.", recorded, changed)


def _convert_legacy_price_rules(cr):
    env = api.Environment(cr, SUPERUSER_ID, {})
    leases = env['realestate.contract'].search([
        '|', ('increment_rule_ids', '!=', False), ('discount_rule_ids', '!=', False)])
    if not leases:
        _logger.info("Legacy price rules: no lease uses them.")
        return
    report = leases._convert_legacy_price_rules()
    _logger.info(
        "Legacy price rules: %s escalation(s) and %s incentive(s) created across "
        "%s lease(s) that used them.",
        report['escalations'], report['incentives'], len(leases))
    for lease, reasons in report['skipped'].items():
        _logger.info("Legacy price rules: skipped on %s: %s", lease, ' | '.join(reasons))
    for lease, reasons in report['flagged'].items():
        _logger.warning(
            "Legacy price rules: %s needs manual review (noted on the lease): %s",
            lease, ' | '.join(reasons))


def migrate(cr, version):
    if not version:
        return
    _apply_plan_frequencies(cr)
    _convert_legacy_price_rules(cr)
