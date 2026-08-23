"""Take ownership of the control records real_estate_procurement declared.

The same hand-over the demand module performs, for the other half: budget
reservations and their conversions, the approval matrix and its snapshots,
control exceptions, the two exception wizards, the release wizard, the
restriction lift, the sequences that number them and the job that expires
them. See `atmta_procurement_request/hooks.py` for why this runs pre-init.

The control-facing fields this module adds to `realestate.material.request`
are handed over too. They are stored columns on a table this module does not
own, and they stay on that table: only the declaring module changes.
"""
import logging

from odoo.addons.atmta_procurement_request.hooks import hand_over

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_control'

MODELS = [
    'realestate.procurement.approval.rule',
    'realestate.procurement.approval.step',
    'realestate.procurement.budget.exception',
    'realestate.procurement.control',
    'realestate.procurement.control.exception',
    'realestate.procurement.purchase.exception',
    'realestate.procurement.reservation',
    'realestate.procurement.reservation.conversion',
    'realestate.procurement.reservation.release',
    'realestate.procurement.restriction.lift',
]
# The control half of the requisition. The requisition itself belongs to
# atmta_procurement_request; these columns on it belong here.
EXTRA_FIELDS = [
    ('realestate.material.request', name) for name in (
        'reservation_ids', 'reserved_amount', 'converted_amount',
        'reservation_status', 'approval_step_ids', 'all_approvals_done',
        'current_step_id', 'waiting_since', 'days_waiting',
        'control_status', 'control_note', 'approval_control_status',
    )
] + [
    ('realestate.material.request.line', 'reservation_ids'),
    ('realestate.material.request.line', 'reserved_amount'),
]
XMLIDS = [
    # access rules
    'access_approval_rule_user', 'access_approval_rule_mgr',
    'access_approval_step_user', 'access_approval_step_mgr',
    'access_approval_rule_requester', 'access_approval_step_requester',
    'access_reservation_user', 'access_reservation_approver',
    'access_reservation_manager', 'access_reservation_conv_user',
    'access_reservation_conv_approver', 'access_reservation_conv_manager',
    'access_control_exception_requester', 'access_control_exception_user',
    'access_control_exception_approver', 'access_control_exception_manager',
    'access_wizard_budget_exception', 'access_wizard_purchase_exception',
    'access_wizard_reservation_release', 'access_restriction_lift_approver',
    # record rules
    'rule_reservation_company', 'rule_reservation_conversion_company',
    'rule_control_exception_company', 'rule_approval_step_company',
    'rule_approval_rule_company',
    # sequences and the expiry job — all noupdate
    'seq_procurement_reservation', 'seq_procurement_control_exception',
    'cron_expire_reservations',
]


def pre_init_hook(env):
    cr = env.cr
    # 'to upgrade' matters as much as 'installed': on an upgrade run Odoo has
    # already moved the old module out of 'installed' by the time this hook
    # fires, and treating that as "nothing to take over" is what leaves a
    # second sequence behind for every code the old module owned.
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
