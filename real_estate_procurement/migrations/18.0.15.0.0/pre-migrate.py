"""Hand the vendor security to the module that owns the vendor models.

Wave 1 moved vendor governance out of this module and left its access rules
behind. Wave 10 moves them, and this script is what makes that safe on a
database that already exists.

The hazard is specific and was demonstrated before it was fixed. This module's
access CSV no longer declares those 49 records, so its data reload removes the
identifiers it still owns — and `atmta_procurement_vendor` is a *dependency*,
which Odoo does not upgrade just because this module is upgrading. An operator
running `-u real_estate_procurement` alone would therefore delete every vendor
access rule and create nothing in their place. Measured: vendor-model ACLs went
from 38 to 0.

Re-pointing the identifiers here, before this module's data loads, closes that
window: `_process_end` only removes identifiers belonging to the module being
loaded, so once these belong to the vendor module they are left alone. The
vendor module then updates them in place whenever it is next upgraded.

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
    cr.execute("SELECT 1 FROM ir_module_module WHERE name = %s AND state = 'installed'",
               (NEW,))
    if not cr.fetchone():
        _logger.info("%s is not installed; leaving the security where it is.", NEW)
        return
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
        "handed %s vendor security identifier(s) to %s; %s stale duplicate(s) "
        "discarded.", cr.rowcount, NEW, discarded)
