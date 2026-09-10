"""Take the commercial floor from the monolith that used to declare it.

`atmta_construction_contract` is new in Wave 13, so it has no migration
history to run: a module being *installed* never executes `migrations/`. The
hand-over happens here, in `pre_init_hook`, which fires once, before this
module's models are reflected and its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.contractor` here would create a second identifier for the model and
for each of its fields while `real_estate_construction`'s identifiers still
point at the originals. That module's data reload would then find them
undeclared and remove them, and removing an `ir.model` identifier cascades to
the model row, taking the table with it: every contractor and every contract
package.

Three groups move:

* the four access rules and the one company record rule this module declares;
* the model and field metadata Odoo generates for the two models, matched by
  what they point at because Odoo, not this module, named them;
* nothing else. The fields `real_estate_construction` still declares on these
  models -- the variations, the extensions of time, the claims, the
  milestones, the certificates and the retention totals -- keep belonging to
  it, which is why the field sweep below excludes them by name.

That exclusion is the delicate part. A blanket "every field of these two
models" would take the seam fields too, and the monolith's next data reload
would then drop identifiers it no longer owns, deleting the columns behind the
figures this suite exists to protect.

The guard accepts `to upgrade` as well as `installed`: an operator upgrading
the monolith in the same run has already moved it out of `installed`, and
Wave 6 lost four sequences to exactly that oversight.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_contract'

MODELS = ['realestate.contractor',
          'realestate.construction.contract.package']

# Fields on those two models that `real_estate_construction` still declares.
# They stay with it.
STAYS_ABOVE = [
    'milestone_ids', 'milestone_count',
    'payment_certificate_ids', 'total_certified', 'total_retention_held',
    'commitment_change_ids', 'eot_ids', 'claim_ids',
]

# Fields this module declares on a model it does not own. `purchase.order`
# belongs to Odoo; only this one column moves, and a model-name sweep would
# never find it.
FOREIGN_FIELDS = [
    ('purchase.order', 're_package_id'),
]

XMLIDS = [
    'access_contractor_user',
    'access_contractor_manager',
    'access_construction_package_user',
    'access_construction_package_manager',
    'rule_realestate_construction_contract_package_company',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 13: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 13: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 13: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 13: %s field identifiers taken (%s left with %s)",
                 cr.rowcount, len(STAYS_ABOVE), OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 13: %s selection identifiers taken", cr.rowcount)

    for model, field in FOREIGN_FIELDS:
        cr.execute("""
            UPDATE ir_model_data d SET module = %s
              FROM ir_model_fields f
             WHERE d.module = %s AND d.model = 'ir.model.fields'
               AND f.id = d.res_id AND f.model = %s AND f.name = %s
        """, (NEW, OLD, model, field))
        if cr.rowcount:
            _logger.info("Wave 13: took %s.%s", model, field)
