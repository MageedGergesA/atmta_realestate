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
NEW = 'atmta_procurement_award'

MODELS = ['realestate.procurement.award',
    'realestate.procurement.award.line',
    'realestate.procurement.award.allocation']
EXTRA_FIELDS = [('realestate.procurement.sourcing.event', 'award_ids')]
XMLIDS = ['access_procurement_award_group_procurement_user',
    'access_procurement_award_group_procurement_manager',
    'access_procurement_award_line_group_procurement_user',
    'access_procurement_award_line_group_procurement_manager',
    'access_procurement_award_alloc_group_procurement_user',
    'access_procurement_award_alloc_group_procurement_manager',
    'rule_procurement_award_company',
    'rule_procurement_award_line_company',
    'rule_procurement_award_allocation_company',
    'seq_procurement_award']


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
