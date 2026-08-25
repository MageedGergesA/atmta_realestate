"""Take the vendor security back from the monolith — for databases that have one.

`pre_init_hook` runs when a module is *installed*. Every existing database
already has this module installed, so on those the hook never fires — and
without a hand-over the monolith's data reload would delete the 49 access
records it no longer declares, before this module could claim them. That is not
theoretical: it is what the first attempt at this change did, and it left every
vendor model with zero ACLs.

So the hand-over lives here as well, where an upgrade will reach it. It runs
before this module's own data is loaded, re-points the existing identifiers, and
lets the data load find and update them in place instead of creating rivals.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_vendor'

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


def migrate(cr, version):
    if not version:
        return
    # A name already claimed under this module means a previous run got there
    # first; drop the stale duplicate rather than collide with the unique index.
    cr.execute("""
        DELETE FROM ir_model_data old_row
         USING ir_model_data new_row
         WHERE old_row.module = %s AND new_row.module = %s
           AND old_row.name = new_row.name AND old_row.model = new_row.model
           AND old_row.name = ANY(%s)
    """, (OLD, NEW, XMLIDS))
    discarded = cr.rowcount
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
           AND model IN ('ir.model.access', 'ir.rule')
    """, (NEW, OLD, XMLIDS))
    _logger.info(
        "%s took %s security identifier(s) from %s; %s stale duplicate(s) "
        "discarded.", NEW, cr.rowcount, OLD, discarded)
