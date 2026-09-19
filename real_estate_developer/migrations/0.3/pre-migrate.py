# -*- coding: utf-8 -*-
"""0.2 → 0.3 — commercial foundation, executed BEFORE the schema changes.

Two things must happen before Odoo touches the tables:

1. ``realestate.project.company_id`` becomes ``required``. Odoo adds the column
   and then applies NOT NULL; with existing rows and no default that fails the
   upgrade outright. The column is therefore created and back-filled here.

2. A ``UNIQUE(company_id, code)`` constraint is added to projects. If a
   database already contains duplicate codes the constraint cannot be created
   and the whole upgrade aborts with a Postgres error that says nothing about
   what to fix. Duplicates are detected here and reported by name.

Nothing is deleted and nothing is invented: the only value that can be assigned
to a project that never had a company is the database's main company, and that
choice is logged so it can be reviewed.
"""

import logging

_logger = logging.getLogger(__name__)


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", ('public.%s' % table,))
    return cr.fetchone()[0] is not None


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _main_company_id(cr):
    """The lowest-id active company — Odoo's own notion of the main company."""
    cr.execute("SELECT id FROM res_company ORDER BY id LIMIT 1")
    row = cr.fetchone()
    return row[0] if row else None


def migrate(cr, version):
    if not version:
        return

    company_id = _main_company_id(cr)
    if not company_id:
        _logger.warning("post-migrate: no company found; skipping backfill")
        return

    # ---- 1. Back-fill project.company_id ------------------------------
    if _table_exists(cr, 'realestate_project'):
        if not _column_exists(cr, 'realestate_project', 'company_id'):
            cr.execute("ALTER TABLE realestate_project ADD COLUMN company_id integer")
        cr.execute("""
            UPDATE realestate_project SET company_id = %s WHERE company_id IS NULL
        """, (company_id,))
        _logger.info(
            "0.3 pre-migrate: assigned %s project(s) to company id %s. "
            "Review this if the database serves more than one developer entity.",
            cr.rowcount, company_id)

        # ---- 2. Report duplicate project codes ------------------------
        cr.execute("""
            SELECT code, array_agg(id ORDER BY id), count(*)
            FROM realestate_project
            WHERE code IS NOT NULL
            GROUP BY company_id, code
            HAVING count(*) > 1
        """)
        duplicates = cr.fetchall()
        if duplicates:
            for code, ids, count in duplicates:
                _logger.error(
                    "0.3 pre-migrate: project code %r is used by %s projects "
                    "(ids %s). UNIQUE(company_id, code) cannot be created until "
                    "these are made distinct.", code, count, ids)
            raise Exception(
                "real_estate_developer 0.3 cannot upgrade: %s duplicate project "
                "code(s) found. See the ERROR lines above for the exact codes "
                "and record ids, make them unique, then re-run the upgrade."
                % len(duplicates))

    # ---- 3. Duplicate live reservations block the new unique index -----
    #
    # Phase 16 adds a partial unique index making two live holds on one unit
    # impossible. Because the old code had no lock, a production database may
    # already contain the situation the index forbids -- and CREATE UNIQUE
    # INDEX would fail with a Postgres message naming neither the unit nor the
    # reservations. They are reported here instead, by reference.
    if _table_exists(cr, 'realestate_unit_reservation'):
        cr.execute("""
            SELECT property_id, array_agg(id ORDER BY id), count(*)
            FROM realestate_unit_reservation
            WHERE state IN ('hold', 'booked')
            GROUP BY property_id
            HAVING count(*) > 1
        """)
        clashes = cr.fetchall()
        if clashes:
            for property_id, ids, count in clashes:
                cr.execute(
                    "SELECT name FROM realestate_unit_reservation WHERE id = ANY(%s)",
                    (ids,))
                names = [row[0] for row in cr.fetchall()]
                _logger.error(
                    "0.3 pre-migrate: property %s already has %s live "
                    "reservations (%s). These are double bookings that the old "
                    "code allowed; cancel or expire all but one before "
                    "upgrading.", property_id, count, ', '.join(names))
            raise Exception(
                "real_estate_developer 0.3 cannot upgrade: %s unit(s) hold "
                "more than one live reservation. See the ERROR lines above for "
                "the units and reservation references. Resolve each double "
                "booking, then re-run the upgrade -- the new index exists "
                "precisely so this cannot recur." % len(clashes))

    # ---- 4. Company columns that are related-stored --------------------
    # These are `related=..., store=True`, so Odoo will compute them, but on a
    # large table it does so row by row. Seeding the obvious value first turns
    # that into a no-op update.
    for table, source_join in (
        ('realestate_unit_reservation',
         'realestate_property p JOIN product_template t ON t.id = p.product_tmpl_id'),
        ('realestate_sale_contract',
         'realestate_property p JOIN product_template t ON t.id = p.product_tmpl_id'),
    ):
        if not _table_exists(cr, table):
            continue
        if not _column_exists(cr, table, 'company_id'):
            cr.execute("ALTER TABLE %s ADD COLUMN company_id integer" % table)
        cr.execute("""
            UPDATE {table} r
               SET company_id = t.company_id
              FROM {join}
             WHERE p.id = r.property_id AND r.company_id IS NULL
        """.format(table=table, join=source_join))
        # A property whose product template has no company (Odoo allows that)
        # leaves the deal company-less, which the new record rules would hide
        # from everyone. Fall back to the main company rather than strand it.
        cr.execute(
            "UPDATE %s SET company_id = %%s WHERE company_id IS NULL" % table,
            (company_id,))
