"""Take ownership of what real_estate_procurement used to declare.

The hand-over Waves 6 and 7 established: `(module, name)` is unique on
`ir_model_data`, so this is an UPDATE of one column, run at pre-init because
Odoo installs a dependency before it upgrades the module that depends on it.
The guard accepts `to upgrade` as well as `installed` — treating an upgrade run
as "nothing to take over" is what left duplicate sequences behind in Wave 6.

No business row is read, written or deleted here.
"""
import logging

from odoo.addons.atmta_procurement_request.hooks import hand_over

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_evaluation'

MODELS = ['realestate.procurement.evaluation.plan',
    'realestate.procurement.evaluation.criterion',
    'realestate.procurement.evaluation.round',
    'realestate.procurement.evaluation.assignment',
    'realestate.procurement.evaluation.candidate',
    'realestate.procurement.technical.evaluation',
    'realestate.procurement.technical.evaluation.line',
    'realestate.procurement.commercial.analysis',
    'realestate.procurement.commercial.adjustment',
    'realestate.procurement.leveling.line',
    'realestate.procurement.evaluation.deviation',
    'realestate.procurement.evaluation.audit',
    'realestate.procurement.evaluation.audit.wizard',
    'realestate.procurement.evaluation.audit.finding']
EXTRA_FIELDS = [('realestate.procurement.sourcing.event', 'evaluation_round_ids')]
XMLIDS = ['access_eval_plan_group_evaluation_technical',
    'access_eval_plan_group_evaluation_commercial',
    'access_eval_plan_group_procurement_user',
    'access_eval_plan_group_evaluation_manager',
    'access_eval_criterion_group_evaluation_technical',
    'access_eval_criterion_group_evaluation_commercial',
    'access_eval_criterion_group_procurement_user',
    'access_eval_criterion_group_evaluation_manager',
    'access_eval_round_group_evaluation_technical',
    'access_eval_round_group_evaluation_commercial',
    'access_eval_round_group_procurement_user',
    'access_eval_round_group_evaluation_manager',
    'access_eval_candidate_group_evaluation_technical',
    'access_eval_candidate_group_evaluation_commercial',
    'access_eval_candidate_group_procurement_user',
    'access_eval_candidate_group_evaluation_manager',
    'access_eval_assignment_group_evaluation_technical',
    'access_eval_assignment_group_evaluation_commercial',
    'access_eval_assignment_group_procurement_user',
    'access_eval_assignment_group_evaluation_manager',
    'access_tech_eval_group_evaluation_technical',
    'access_tech_eval_group_evaluation_commercial',
    'access_tech_eval_group_evaluation_manager',
    'access_tech_eval_line_group_evaluation_technical',
    'access_tech_eval_line_group_evaluation_commercial',
    'access_tech_eval_line_group_evaluation_manager',
    'access_comm_analysis_group_evaluation_commercial',
    'access_comm_analysis_group_evaluation_manager',
    'access_comm_adjustment_group_evaluation_commercial',
    'access_comm_adjustment_group_evaluation_manager',
    'access_leveling_line_group_evaluation_commercial',
    'access_leveling_line_group_evaluation_manager',
    'access_eval_deviation_group_evaluation_technical',
    'access_eval_deviation_group_evaluation_commercial',
    'access_eval_deviation_group_evaluation_manager',
    'access_eval_audit_wizard_group_evaluation_manager',
    'access_eval_audit_wizard_group_procurement_manager',
    'access_eval_audit_finding_group_evaluation_manager',
    'access_eval_audit_finding_group_procurement_manager',
    'rule_evaluation_plan_company',
    'rule_evaluation_criterion_company',
    'rule_evaluation_round_company',
    'rule_evaluation_candidate_company',
    'rule_evaluation_assignment_company',
    'rule_technical_evaluation_company',
    'rule_technical_evaluation_line_company',
    'rule_commercial_analysis_company',
    'rule_commercial_adjustment_company',
    'rule_leveling_line_company',
    'rule_evaluation_deviation_company',
    'seq_evaluation_plan',
    'seq_evaluation_round',
    'seq_evaluation_deviation']


def pre_init_hook(env):
    cr = env.cr
    cr.execute("""SELECT 1 FROM ir_module_module
                   WHERE name = %s AND state IN ('installed', 'to upgrade')""",
               (OLD,))
    if not cr.fetchone():
        _logger.info("%s: %s is not installed, nothing to take over.", NEW, OLD)
        return
    moved = hand_over(cr, OLD, NEW, MODELS, EXTRA_FIELDS, XMLIDS)
    _logger.info(
        "%s took ownership from %s: %s model(s), %s field(s), %s other "
        "identifier(s); %s stale duplicate(s) discarded.",
        NEW, OLD, moved['model'], moved['field'], moved['other'],
        moved['discarded'])
