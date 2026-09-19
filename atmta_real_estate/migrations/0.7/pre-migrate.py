"""Payment plans leave Rental: record each lease's plan as a billing frequency.

``realestate.payment.plan`` was declared twice under one name: by Rental (a
four-field "pay every N days/weeks/months/years" rule) and by
``real_estate_developer`` (the off-plan payment plan with lines, approval and
versioning). The developer class replaces Rental's in the registry, so both
Payment Plans menus errored and the legacy schedule generator read fields that
no longer existed on the model it got. Decision 3 of the rental re-architecture
(13 September 2026): Rental stops declaring the model; Development & Sales owns
it.

Rental's only use of a plan was the legacy schedule generator, which is retired
in the same release. What a lease's plan *meant* -- how often rent is billed --
is kept by translating it into the lease's ``billing_frequency``, which is what
the billing engine uses.

This script runs before the new schema is applied, while Rental's plan columns
and relation table still exist. It records the translation in a snapshot column
on ``realestate_contract``; ``post-migrate.py`` applies it once
``billing_frequency`` is guaranteed to exist.

Rules, in the spirit of the 0.6 migration:

* **Nothing is invented.** Billing frequencies are monthly, quarterly,
  semi-annual and annual. A plan maps only when it bills every 1, 3, 6 or 12
  months, or every year. Day and week plans, other intervals, and leases whose
  plans resolve to more than one frequency are left untouched.
* **Ambiguity is reported, loudly.** Every lease that could not be translated is
  logged with its id.
* **Nothing is deleted here.** The snapshot column is kept afterwards, so the
  translation stays auditable.
"""

import logging

_logger = logging.getLogger(__name__)

SNAPSHOT_COLUMN = 're_legacy_plan_frequency'

#: (unit, interval) -> billing frequency. Anything not listed has no equivalent.
PLAN_TO_FREQUENCY = {
    ('month', 1): 'monthly',
    ('month', 3): 'quarterly',
    ('month', 6): 'semiannual',
    ('month', 12): 'annual',
    ('year', 1): 'annual',
}


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return bool(cr.fetchone()[0])


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    if not (_table_exists(cr, 'realestate_payment_plan')
            and _table_exists(cr, 'rel_contract_payment_plan')
            and _table_exists(cr, 'realestate_contract')):
        _logger.info("Payment plans: no lease-plan links to translate.")
        return
    for column in ('unit', 'interval'):
        if not _column_exists(cr, 'realestate_payment_plan', column):
            _logger.info(
                "Payment plans: Rental's plan columns are already gone; "
                "nothing to translate.")
            return

    if not _column_exists(cr, 'realestate_contract', SNAPSHOT_COLUMN):
        cr.execute('ALTER TABLE realestate_contract ADD COLUMN %s VARCHAR'
                   % SNAPSHOT_COLUMN)

    cr.execute("""
        SELECT r.contract_id,
               array_agg(p.unit ORDER BY p.id),
               array_agg(p.interval ORDER BY p.id)
          FROM rel_contract_payment_plan r
          JOIN realestate_payment_plan p ON p.id = r.contract_payment_plan_id
      GROUP BY r.contract_id
    """)
    translated = {}
    unmapped = []
    ambiguous = []
    for contract_id, units, intervals in cr.fetchall():
        frequencies = {PLAN_TO_FREQUENCY.get((unit, interval))
                       for unit, interval in zip(units, intervals)}
        if None in frequencies:
            unmapped.append(contract_id)
        elif len(frequencies) > 1:
            ambiguous.append(contract_id)
        else:
            translated.setdefault(frequencies.pop(), []).append(contract_id)

    for frequency, ids in translated.items():
        cr.execute(
            "UPDATE realestate_contract SET %s = %%s WHERE id = ANY(%%s)"
            % SNAPSHOT_COLUMN, (frequency, ids))
        _logger.info("Payment plans: %s lease(s) recorded as billed %s.",
                     len(ids), frequency)

    if unmapped:
        _logger.warning(
            "Payment plans: %s lease(s) use a plan with no billing-frequency "
            "equivalent (day or week units, or an interval other than 1, 3, 6 or "
            "12 months); their billing frequency is left unchanged and must be "
            "set by hand. Lease ids: %s", len(unmapped), unmapped[:100])
    if ambiguous:
        _logger.warning(
            "Payment plans: %s lease(s) combine plans with different frequencies; "
            "their billing frequency is left unchanged and must be set by hand. "
            "Lease ids: %s", len(ambiguous), ambiguous[:100])
