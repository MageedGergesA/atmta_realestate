"""Backfill `realestate.property.company_id` from the template it mirrors.

`company_id` on the property is a STORED RELATED to
`product_tmpl_id.company_id`: the delegated `product.template` is the master
and the property column is a materialised copy of it. Rows that existed
before the field was added were never backfilled, so their column sat NULL
while the template underneath carried a company.

That is not cosmetic. The column is stored precisely so it can be read by
record rules and by SQL that never loads the ORM field, and a NULL there
reads as "no company" -- which in a multi-company database means a row that
every company can see. `test_the_column_never_drifts_from_the_template`
asserts the invariant and was failing on 30 rows of this database.

Written as one UPDATE rather than an ORM recompute because that is exactly
what the related field materialises, because it cannot be defeated by a
record rule while it runs, and because it touches only rows that are already
inconsistent. It is idempotent: a second run matches nothing.

Direction is from the template to the property and never the reverse. The
template is the master; copying the other way would invent a company for a
template that deliberately has none.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE realestate_property p
           SET company_id = t.company_id
          FROM product_template t
         WHERE t.id = p.product_tmpl_id
           AND p.company_id IS DISTINCT FROM t.company_id
    """)
    if cr.rowcount:
        _logger.info("Property Core: realigned company_id on %s propert%s "
                     "with the product template each is materialised from.",
                     cr.rowcount, 'y' if cr.rowcount == 1 else 'ies')
