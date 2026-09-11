"""Take change control from the monolith that used to declare it.

`atmta_construction_change` is new in Wave 16, so it has no migration history
to run: a module being *installed* never executes `migrations/`. The hand-over
happens here, in `pre_init_hook`, before this module's models are reflected and
its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.change.order` here would create a second identifier
for the model and for each of its fields while `real_estate_construction`'s
identifiers still point at the originals, and that module's data reload would
then drop them -- cascading through `ir.model` to the tables, taking every
change event and every priced, approved change order with them.

What stays above is named in STAYS_ABOVE: the implementation links an approved
order produces, and the forecast adjustments a change event collects. Those
belong to models `real_estate_construction` owns, so it keeps declaring them.
A blanket sweep would take them, and the monolith's next reload would drop the
columns behind the budget and commitment records.

The sequences are `noupdate`, which `_process_end` skips when it cleans up.
Wave 6 lost four sequences to exactly that.

The guard accepts `to upgrade` as well as `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_change'

MODELS = [
    'realestate.construction.change.event',
    'realestate.construction.change.order',
    'realestate.construction.change.order.line',
    'realestate.construction.change.order.markup',
    'realestate.construction.change.order.revision',
    'realestate.construction.change.approval',
]

# Declared above this module, on models it owns.
STAYS_ABOVE = [
    'budget_change_ids',
    'commitment_change_ids',
    'forecast_adjustment_ids',
]

XMLIDS = [
    'access_construction_change_approval_manager',
    'access_construction_change_approval_user',
    'access_construction_change_event_manager',
    'access_construction_change_event_user',
    'access_construction_change_order_line_manager',
    'access_construction_change_order_line_user',
    'access_construction_change_order_manager',
    'access_construction_change_order_markup_manager',
    'access_construction_change_order_markup_user',
    'access_construction_change_order_revision_manager',
    'access_construction_change_order_revision_user',
    'access_construction_change_order_user',
    'rule_realestate_construction_change_event_company',
    'rule_realestate_construction_change_event_manager',
    'rule_realestate_construction_change_order_company',
    'rule_realestate_construction_change_order_line_company',
    'rule_realestate_construction_change_order_manager',
    'seq_construction_change_event',
    'seq_construction_change_order',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 16: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 16: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 16: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 16: %s field identifiers taken (%s left with %s)",
                 cr.rowcount, STAYS_ABOVE, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 16: %s selection identifiers taken", cr.rowcount)
