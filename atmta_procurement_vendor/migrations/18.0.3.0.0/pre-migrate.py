"""Take the identifiers Wave 11 moved into this module.

`real_estate_procurement` declared these until Wave 11 emptied it. This module
declares them now, so this runs before its data loads: without it Odoo would
look up each identifier under this module's name, not find it, and create a
second record beside the one the shell's identifier still points at. The shell
would then drop its identifier and take the original with it.

Re-pointing the rows first means the load finds them and updates the records
already there. `_process_end` only removes identifiers belonging to the module
being loaded, so once a row names this module the shell leaves it alone.

It runs here rather than in the shell because Odoo loads a dependency before
the module that depends on it: by the time the shell is processed this module
has already loaded its data, and the duplicates would exist.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

CONTROL = 'atmta_procurement_control'
# The restriction-lift wizard: declared by Control, but its only field points
# at `realestate.procurement.vendor.restriction`, which this module owns.
# Wave 11 moved the model here, so these two rows move with it. Claimed here
# rather than released by Control because Odoo may load this module first.
CONTROL_XMLIDS = [
    'model_realestate_procurement_restriction_lift',
    'access_restriction_lift_approver',
]

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_vendor'
XMLIDS = [
    # Odoo generates one `ir.actions.server` per `ir.cron`, named after it.
    # The shell declared the crons, so it owns these too; they are listed
    # explicitly because nothing in the XML names them. Measured: without
    # them three rows stay behind, and the shell's next data reload would
    # drop the server actions the crons run.
    'cron_expire_qualifications_ir_actions_server',
    'cron_expire_restrictions_ir_actions_server',
    'cron_classify_vendors_ir_actions_server',
    'action_avl_report',
    'action_qualification_area',
    'action_qualification_expiring',
    'action_qualification_pending',
    'action_qualification_reject',
    'action_qualification_template',
    'action_realestate_vendor_tags',
    'action_realestate_vendors',
    'action_restriction_lift',
    'action_vendor_audit',
    'action_vendor_category',
    'action_vendor_profile',
    'action_vendor_qualification',
    'action_vendor_restriction',
    'action_vendors_unassessed',
    'cron_classify_vendors',
    'cron_expire_qualifications',
    'cron_expire_restrictions',
    'qual_area_certifications',
    'qual_area_commercial',
    'qual_area_delivery',
    'qual_area_experience',
    'qual_area_financial',
    'qual_area_hse',
    'qual_area_infosec',
    'qual_area_insurance',
    'qual_area_legal',
    'qual_area_other',
    'qual_area_quality',
    'qual_area_references',
    'qual_area_sustainability',
    'qual_area_technical',
    'seq_vendor_qualification',
    'seq_vendor_restriction',
    'tag_vendor_consultant',
    'tag_vendor_contractor',
    'tag_vendor_landowner',
    'tag_vendor_marketing',
    'tag_vendor_material_supplier',
    'tag_vendor_service',
    'tag_vendor_utility',
    'view_avl_report_form',
    'view_partner_procurement_governance',
    'view_qualification_area_list',
    'view_qualification_reject_form',
    'view_qualification_template_form',
    'view_qualification_template_list',
    'view_restriction_lift_form',
    'view_vendor_audit_form',
    'view_vendor_category_form',
    'view_vendor_category_list',
    'view_vendor_profile_form',
    'view_vendor_profile_list',
    'view_vendor_profile_search',
    'view_vendor_qualification_form',
    'view_vendor_qualification_list',
    'view_vendor_qualification_search',
    'view_vendor_restriction_form',
    'view_vendor_restriction_list',
    'view_vendor_restriction_search',
]


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, OLD, XMLIDS))
    _logger.info("Wave 11: %s identifiers taken from %s by %s",
                 cr.rowcount, OLD, NEW)

    cr.execute("""
        UPDATE ir_model_data SET module = %s
         WHERE module = %s AND name = ANY(%s)
    """, (NEW, CONTROL, CONTROL_XMLIDS))
    _logger.info("Wave 11: %s restriction-lift identifiers taken from %s",
                 cr.rowcount, CONTROL)
