"""Take ownership of the vendor security real_estate_procurement still declared.

Wave 1 moved the vendor governance models out of the monolith and left their
access rules behind. Nine waves later this module still shipped no security at
all — every one of its fifteen models was reachable only because the monolith
happened to grant it. Wave 10 closes that.

The hand-over is the one Waves 6-9 established: `(module, name)` is unique on
`ir_model_data`, so this is an UPDATE of one column, run at pre-init because
Odoo installs a dependency before it upgrades the module that depends on it.
The guard accepts `to upgrade` as well as `installed`.

No business row is read, written or deleted here.
"""
import logging

from odoo.addons.atmta_procurement_request.hooks import hand_over

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_vendor'

MODELS = []
EXTRA_FIELDS = []
XMLIDS = ['access_vendor_category_user',
    'access_vendor_category_buyer',
    'access_vendor_category_mgr',
    'access_qual_area_buyer',
    'access_qual_area_assessor',
    'access_qual_area_mgr',
    'access_qual_template_buyer',
    'access_qual_template_assessor',
    'access_qual_template_mgr',
    'access_qual_requirement_buyer',
    'access_qual_requirement_assessor',
    'access_qual_requirement_mgr',
    'access_vendor_profile_requester',
    'access_vendor_profile_buyer',
    'access_vendor_profile_assessor',
    'access_vendor_profile_mgr',
    'access_vendor_qualification_buyer',
    'access_vendor_qualification_assessor',
    'access_vendor_qualification_approver',
    'access_vendor_qualification_mgr',
    'access_qual_response_assessor',
    'access_qual_response_approver',
    'access_qual_response_buyer',
    'access_qual_response_mgr',
    'access_qual_condition_buyer',
    'access_qual_condition_assessor',
    'access_qual_condition_mgr',
    'access_vendor_restriction_buyer',
    'access_vendor_restriction_assessor',
    'access_vendor_restriction_approver',
    'access_vendor_restriction_mgr',
    'access_avl_report_buyer',
    'access_avl_report_line_buyer',
    'access_vendor_audit_assessor',
    'access_vendor_audit_line_assessor',
    'access_vendor_audit_mgr',
    'access_vendor_audit_line_mgr',
    'access_qual_reject_approver',
    'rule_vendor_profile_company',
    'rule_vendor_qualification_company',
    'rule_qualification_response_company',
    'rule_qualification_condition_company',
    'rule_vendor_restriction_company',
    'rule_qualification_template_company',
    'rule_qualification_requirement_company',
    'rule_qualification_area_company',
    'rule_vendor_category_company',
    'rule_qualification_response_not_confidential',
    'rule_qualification_response_assessor']


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
        "%s took ownership from %s: %s security identifier(s); "
        "%s stale duplicate(s) discarded.",
        NEW, OLD, moved['other'], moved['discarded'])
