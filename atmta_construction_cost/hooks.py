"""Take cost and forecast from the monolith that used to declare them.

`atmta_construction_cost` is new in Wave 20, so it has no migration history to
run: a module being *installed* never executes `migrations/`. The hand-over
happens here, in `pre_init_hook`, before this module's models are reflected and
its data is loaded.

Without it the move is destructive on an existing database. Reflecting
`realestate.construction.budget` here would create a second identifier for the
model and for each of its fields while `real_estate_construction`'s identifiers
still point at the originals, and that module's reload would then drop them --
cascading through `ir.model` to the tables. On a live job that is the baseline
budget, every commitment, and every forecast ever approved.

Nothing is excluded this time. Waves 13 to 18 each left a field or two above
because the module owning the other end had not moved yet. By Wave 20 every
one of those ends is below: change orders, certificates, BOQ lines and claims
are all extracted, so this module declares its own relations outright and the
sweep is unconditional.

The sequences are `noupdate`, which `_process_end` skips when it cleans up.
Wave 6 lost four sequences to exactly that.

The guard accepts `to upgrade` as well as `installed`.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_cost'

MODELS = [
    'realestate.construction.budget',
    'realestate.construction.budget.line',
    'realestate.construction.budget.migration',
    'realestate.construction.budget.change.line',
    'realestate.construction.change.authority',
    'realestate.construction.commitment',
    'realestate.construction.commitment.change',
    'realestate.construction.revenue.change',
    'realestate.construction.exposure',
    'realestate.construction.claim.kpi',
    'realestate.construction.cost.sheet',
    'realestate.construction.forecast',
    'realestate.construction.forecast.line',
    'realestate.construction.forecast.adjustment',
    'realestate.construction.cost.report',
    'realestate.construction.cost.report.line',
    'realestate.construction.retention.disclosure',
    'realestate.construction.controls',
    'realestate.construction.risk',
    'realestate.construction.risk.action',
    'realestate.construction.issue',
]

XMLIDS = [
    'access_construction_budget_change_line_manager',
    'access_construction_budget_change_line_user',
    'access_construction_budget_line_manager',
    'access_construction_budget_line_user',
    'access_construction_budget_manager',
    'access_construction_budget_user',
    'access_construction_commitment_change_manager',
    'access_construction_commitment_change_user',
    'access_construction_cost_report_line_user',
    'access_construction_cost_report_user',
    'access_construction_forecast_adj_manager',
    'access_construction_forecast_adj_user',
    'access_construction_forecast_line_manager',
    'access_construction_forecast_line_user',
    'access_construction_forecast_manager',
    'access_construction_forecast_user',
    'access_construction_revenue_change_manager',
    'access_construction_revenue_change_user',
    'access_issue_manager',
    'access_issue_user',
    'access_risk_action_manager',
    'access_risk_action_user',
    'access_risk_manager',
    'access_risk_user',
    'rule_realestate_construction_budget_change_line_company',
    'rule_realestate_construction_budget_company',
    'rule_realestate_construction_budget_line_company',
    'rule_realestate_construction_budget_line_manager',
    'rule_realestate_construction_budget_manager',
    'rule_realestate_construction_commitment_change_company',
    'rule_realestate_construction_forecast_company',
    'rule_realestate_construction_forecast_line_company',
    'rule_realestate_construction_forecast_line_manager',
    'rule_realestate_construction_forecast_manager',
    'rule_realestate_construction_issue_company',
    'rule_realestate_construction_issue_manager',
    'rule_realestate_construction_revenue_change_company',
    'rule_realestate_construction_risk_action_company',
    'rule_realestate_construction_risk_company',
    'rule_realestate_construction_risk_manager',
    'seq_construction_budget',
    'seq_construction_forecast',
    'seq_construction_issue',
    'seq_construction_risk',
]


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("Wave 20: %s is not installed; nothing to take over", OLD)
        return

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 20: %s declared identifiers taken from %s",
                 cr.rowcount, OLD)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 20: %s ir.model identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 20: %s field identifiers taken", cr.rowcount)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
    """, (NEW, OLD, MODELS))
    _logger.info("Wave 20: %s selection identifiers taken", cr.rowcount)
