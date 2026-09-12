"""Take site execution from the monolith that used to declare it.

`atmta_construction_site` is new in Wave 18, so it has no migration history to
run: a module being *installed* never executes `migrations/`. The hand-over
happens here, in `pre_init_hook`, before this module's models are reflected and
its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.boq` here would create a second identifier for the model and for
each of its fields while `real_estate_construction`'s identifiers still point
at the originals, and that module's reload would then drop them -- cascading
through `ir.model` to the tables. On a live project that is every measured
quantity and every daily report on the job.

`certification_line_ids` stays above. It is the relation from a BOQ line to
the certificate lines consuming it, and payment certificates did not move, so
`real_estate_construction` keeps declaring it. A blanket field sweep would take
it and the monolith's next reload would drop the column.

The sequences are `noupdate`, which `_process_end` skips when it cleans up.
Wave 6 lost four sequences to exactly that.

The guard accepts `to upgrade` as well as `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_site'

MODELS = [
    'realestate.boq',
    'realestate.boq.line',
    'realestate.boq.line.variation',
    'realestate.work.item',
    'realestate.work.item.category',
    'realestate.construction.milestone',
    'realestate.construction.task',
    'realestate.construction.cost.line',
    'realestate.construction.daily.report',
    'realestate.construction.daily.work',
    'realestate.construction.daily.delay',
    'realestate.construction.daily.delivery',
    'realestate.construction.daily.equipment',
    'realestate.construction.labor.log',
]

# Declared above this module, on a model it owns.
STAYS_ABOVE = ['certification_line_ids']

# Wave 21 — the screens for these models, moved down from the monolith
# once they were shown to render no field declared above this module.
VIEW_XMLIDS = [
    'action_boq',
    'action_construction_task',
    'action_cost_line',
    'action_labor_log',
    'action_milestone',
    'action_work_item',
    'action_work_item_category',
    'view_boq_form',
    'view_boq_list',
    'view_construction_task_form',
    'view_construction_task_list',
    'view_cost_line_form',
    'view_cost_line_list',
    'view_labor_log_form',
    'view_labor_log_list',
    'view_milestone_form',
    'view_milestone_kanban',
    'view_milestone_list',
    'view_work_item_category_form',
    'view_work_item_category_list',
    'view_work_item_form',
    'view_work_item_list',
]

# Wave 23 — the daily report's own list and form, which had been living in
# the quality view file and are declared here now.
W23_XMLIDS = [
    'action_daily_report',
    'view_daily_report_form',
    'view_daily_report_list',
]

XMLIDS = [
    'access_boq_line_mgr',
    'access_boq_line_user',
    'access_boq_line_variation_manager',
    'access_boq_line_variation_user',
    'access_boq_mgr',
    'access_boq_user',
    'access_construction_daily_delay_manager',
    'access_construction_daily_delay_user',
    'access_construction_daily_delivery_manager',
    'access_construction_daily_delivery_user',
    'access_construction_daily_equipment_manager',
    'access_construction_daily_equipment_user',
    'access_construction_daily_report_manager',
    'access_construction_daily_report_user',
    'access_construction_daily_work_manager',
    'access_construction_daily_work_user',
    'access_cost_line_manager',
    'access_cost_line_user',
    'access_labor_log_mgr',
    'access_labor_log_user',
    'access_milestone_manager',
    'access_milestone_user',
    'access_task_manager',
    'access_task_user',
    'access_work_item_cat_mgr',
    'access_work_item_cat_user',
    'access_work_item_mgr',
    'access_work_item_user',
    'rule_realestate_boq_company',
    'rule_realestate_boq_line_company',
    'rule_realestate_boq_line_manager',
    'rule_realestate_boq_line_variation_company',
    'rule_realestate_boq_manager',
    'rule_realestate_construction_cost_line_manager',
    'rule_realestate_construction_daily_delay_company',
    'rule_realestate_construction_daily_delay_manager',
    'rule_realestate_construction_daily_delivery_company',
    'rule_realestate_construction_daily_equipment_company',
    'rule_realestate_construction_daily_report_company',
    'rule_realestate_construction_daily_report_manager',
    'rule_realestate_construction_daily_work_company',
    'rule_realestate_construction_daily_work_manager',
    'rule_realestate_construction_labor_log_manager',
    'rule_realestate_construction_milestone_manager',
    'rule_realestate_construction_task_manager',
    'seq_boq',
    'seq_construction_daily_report',
    'seq_labor_log',
    'seq_milestone',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 18: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 18: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 18: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 18: %s field identifiers taken (%s left with %s)",
                 cr.rowcount, STAYS_ABOVE, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
           AND f.name <> ALL(%s)
    """, (NEW, OLD, MODELS, STAYS_ABOVE))
    _logger.info("Wave 18: %s selection identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, VIEW_XMLIDS))
    _logger.info("Wave 21: %s view identifiers taken from %s", cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, W23_XMLIDS))
    _logger.info("Wave 23: %s screen identifiers taken from %s", cr.rowcount, OLD)

# Wave 25 -- the project-team and manager rules, moved down from
# `real_estate_construction` once that module stopped declaring
# `construction_member_ids`. The identifiers keep their original names, so the
# hand-over is an UPDATE of the module column and the loader then updates the
# existing rows in place instead of creating second copies.
W25_RULE_XMLIDS = [
    'rule_realestate_boq_line_project_member',
    'rule_realestate_boq_project_member',
    'rule_realestate_construction_cost_line_project_member',
    'rule_realestate_construction_daily_delay_project_member',
    'rule_realestate_construction_daily_report_project_member',
    'rule_realestate_construction_daily_work_project_member',
    'rule_realestate_construction_labor_log_project_member',
    'rule_realestate_construction_milestone_project_member',
    'rule_realestate_construction_task_project_member',
]
