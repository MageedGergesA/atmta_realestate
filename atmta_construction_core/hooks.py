"""Take the floor from the monolith that used to declare it.

`atmta_construction_core` is new in Wave 12, so it has no migration history to
run: a module being *installed* never executes `migrations/`. The hand-over
therefore happens here, in `pre_init_hook`, which fires once, before this
module's models are reflected and its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.wbs` here would create a second identifier for the
model and for each of its fields, while `real_estate_construction`'s
identifiers still point at the originals. That module's data reload would then
find them undeclared and remove them -- and removing an `ir.model` identifier
cascades to the model row, taking the table and every WBS node with it.

Three groups move:

* the identifiers this module now declares by name -- the views, the window
  action, the four access rules and the two company record rules;
* the model and field metadata Odoo generates for the two concrete models,
  matched by what they point at because Odoo, not this module, named them;
* the four company account fields, which live on `res.company` and
  `res.config.settings` and so cannot be matched by model name alone.

The guard accepts `to upgrade` as well as `installed`: an operator upgrading
the monolith in the same run has already moved it out of `installed`, and
Wave 6 lost four sequences to exactly that oversight.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_core'

MODELS = ['realestate.construction.wbs', 'realestate.construction.cost.code']

# Fields this module declares on models it does not own. `res.company` and
# `res.config.settings` belong to base; only these four columns move.
FOREIGN_FIELDS = [
    ('res.company', 'construction_retention_account_id'),
    ('res.company', 'construction_advance_account_id'),
    ('res.company', 'construction_owner_retention_account_id'),
    ('res.company', 'construction_owner_advance_account_id'),
    ('res.config.settings', 'construction_retention_account_id'),
    ('res.config.settings', 'construction_advance_account_id'),
    ('res.config.settings', 'construction_owner_retention_account_id'),
    ('res.config.settings', 'construction_owner_advance_account_id'),
]

XMLIDS = [
    # The two analytic plans. They are `noupdate` records, which
    # `_process_end` skips when cleaning up -- Wave 6 lost four sequences to
    # exactly that. They must be handed over by name or the floor creates a
    # second pair and every existing cost code keeps pointing at the first.
    'analytic_plan_re_projects',
    'analytic_plan_cost_codes',
    'action_construction_cost_code',
    'action_construction_wbs',
    'view_construction_cost_code_form',
    'view_construction_cost_code_list',
    'view_construction_wbs_form',
    'view_construction_wbs_list',
    'access_construction_wbs_user',
    'access_construction_wbs_manager',
    'access_construction_cost_code_user',
    'access_construction_cost_code_manager',
    'rule_realestate_construction_wbs_company',
    'rule_realestate_construction_cost_code_company',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 12: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 12: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 12: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 12: %s field identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 12: %s selection identifiers taken", cr.rowcount)

    for model, field in FOREIGN_FIELDS:
        cr.execute("""
            UPDATE ir_model_data d SET module = %s
              FROM ir_model_fields f
             WHERE d.module = %s AND d.model = 'ir.model.fields'
               AND f.id = d.res_id AND f.model = %s AND f.name = %s
        """, (NEW, OLD, model, field))
        if cr.rowcount:
            _logger.info("Wave 12: took %s.%s", model, field)

# Wave 25 -- the project-team and manager rules, moved down from
# `real_estate_construction` once that module stopped declaring
# `construction_member_ids`. The identifiers keep their original names, so the
# hand-over is an UPDATE of the module column and the loader then updates the
# existing rows in place instead of creating second copies.
W25_RULE_XMLIDS = [
    'rule_realestate_construction_wbs_manager',
    'rule_realestate_construction_wbs_project_member',
]
