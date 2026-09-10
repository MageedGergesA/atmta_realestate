"""Take the purchase-order bridge from the shell that used to declare it.

`atmta_procurement_purchase` is new in Wave 11, so it has no migration history
to run: a module that is being *installed* never executes `migrations/`. The
hand-over therefore happens here, in `pre_init_hook`, which fires once, before
this module's models are reflected and its data is loaded.

What moves is the metadata for the `re_*` columns on `purchase.order` and
`purchase.order.line`, plus the two form views. `real_estate_procurement`
declared all of it until this module took the code. If the rows were left
alone, reflecting these fields would create a second identifier for each while
the shell's identifier still pointed at the original — and the shell's data
reload would then drop the original, cascading through `ir.model.fields` to the
columns themselves.

The identifiers Odoo generates (`model_*`, `field_*`, `selection__*`) are
matched by what they point at rather than by name, because Odoo, not this
module, chose those names. The two declared views are matched by name.

The guard accepts `to upgrade` as well as `installed`: an operator upgrading
the shell in the same run has already moved it out of `installed`, and Wave 6
lost four sequences to exactly that oversight.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_purchase'
MODELS = ['purchase.order', 'purchase.order.line']
XMLIDS = [
    'action_realestate_purchase_orders',
    'view_purchase_order_form_re',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 11: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 11: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 11: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 11: %s field identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 11: %s selection identifiers taken", cr.rowcount)
