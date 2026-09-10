"""Hand every identifier this module no longer declares to the module that does.

Wave 11 emptied this shell. Every screen, report, wizard, sequence, cron and
seed record it used to declare now lives in the capability that owns the models
behind it, and the purchase-order gate has a module of its own.

The hazard is the one Wave 10 measured, at a much larger scale. When
`atmta_procurement_vendor` loads `views/vendor_governance_views.xml` for the
first time it looks up `atmta_procurement_vendor.view_vendor_profile_form`,
does not find it, and *creates a second view* -- while the identifier pointing
at the original still belongs to this module. This module's data reload then
finds that identifier undeclared and removes it, taking the original record
with it. Every menu, action and product in the catalog is exposed the same way.

Re-pointing the identifiers here, before this module's data loads, closes that
window. `_process_end` only removes identifiers belonging to the module being
loaded, so once a row names the new owner it is left alone, and the new owner
updates the record it already points at instead of creating a rival.

Two groups need it and are handled differently:

* the declared identifiers in MOVED, listed per destination module;
* the metadata Odoo generates for `purchase.order` and `purchase.order.line` --
  the model rows and the `re_*` field rows this module used to declare. Those
  identifiers are named by Odoo, not by us, so they are matched by what they
  point at rather than by a hand-written list.

`(module, name)` is unique on `ir_model_data`, so all of this is an UPDATE of
one column. No business row is read, written or deleted.
"""
import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_procurement'

MOVED = {
    'atmta_procurement_app': [
        'view_project_form_procurement',
    ],
    'atmta_procurement_award': [
        'action_procurement_award',
        'action_report_award',
        'report_award',
        'report_award_document',
        'view_procurement_award_form',
        'view_procurement_award_list',
    ],
    'atmta_procurement_control': [
        'view_material_request_list',
        'view_material_request_form',
        'view_material_request_search',
        'action_material_request',
        'action_approval_register',
        'action_approval_rule',
        'action_control_exception',
        'action_procurement_reservation',
        'view_approval_rule_list',
        'view_approval_step_list',
        'view_approval_step_pivot',
        'view_approval_step_search',
        'view_company_form_procurement',
        'view_control_exception_form',
        'view_control_exception_list',
        'view_control_exception_search',
        'view_procurement_reservation_form',
        'view_procurement_reservation_list',
        'view_procurement_reservation_pivot',
        'view_procurement_reservation_search',
    ],
    'atmta_procurement_core': [
        'action_realestate_catalog',
        'action_realestate_product_categories',
        'cat_construction_civil',
        'cat_construction_finishing',
        'cat_construction_mep',
        'cat_construction_site',
        'cat_construction_structural',
        'cat_fittings_appliances',
        'cat_fittings_furniture',
        'cat_fittings_hvac',
        'cat_fittings_sanitary',
        'cat_fittings_smart_home',
        'cat_re_construction',
        'cat_re_fittings',
        'cat_re_maintenance',
        'cat_re_marketing',
        'cat_re_root',
        'cat_re_services',
        'prod_ac_15ton',
        'prod_ac_2ton',
        'prod_ac_3ton',
        'prod_ac_filter_hepa',
        'prod_ac_filter_std',
        'prod_aggregate_10',
        'prod_aggregate_20',
        'prod_aluminum_window',
        'prod_bathtub',
        'prod_bed_king',
        'prod_bidet_spray',
        'prod_brochures_1000',
        'prod_built_in_oven',
        'prod_business_cards',
        'prod_cable_25',
        'prod_cable_4',
        'prod_cable_6',
        'prod_cctv_dome',
        'prod_cement_board',
        'prod_cement_portland',
        'prod_cement_white',
        'prod_concrete_block_10',
        'prod_concrete_block_15',
        'prod_concrete_block_20',
        'prod_conduit_20',
        'prod_copper_pipe_12',
        'prod_crushed_stone',
        'prod_db_12way',
        'prod_db_24way',
        'prod_detergent_5l',
        'prod_diesel_fuel',
        'prod_dining_table',
        'prod_dishwasher',
        'prod_disinfectant_5l',
        'prod_door_exterior',
        'prod_door_interior',
        'prod_drone_session',
        'prod_flexible_duct',
        'prod_floor_cleaner_5l',
        'prod_floor_drain',
        'prod_floor_plan_render',
        'prod_fridge_300l',
        'prod_granite_slab',
        'prod_gypsum_board',
        'prod_gypsum_powder',
        'prod_hdpe_pipe_50',
        'prod_hollow_block',
        'prod_hvac_diffuser',
        'prod_hvac_grille',
        'prod_insulated_duct',
        'prod_junction_box',
        'prod_led_15w',
        'prod_led_9w',
        'prod_led_driver',
        'prod_lightweight_block',
        'prod_lime_powder',
        'prod_listing_boost',
        'prod_lockbox',
        'prod_marble_slab',
        'prod_microwave',
        'prod_mortar_premix',
        'prod_paint_wall_20l',
        'prod_paint_wall_4l',
        'prod_photo_session',
        'prod_plumbing_tape',
        'prod_primer_20l',
        'prod_pvc_pipe_1',
        'prod_pvc_pipe_12',
        'prod_pvc_pipe_2',
        'prod_pvc_pipe_34',
        'prod_rebar_08',
        'prod_rebar_10',
        'prod_rebar_12',
        'prod_rebar_16',
        'prod_rebar_20',
        'prod_rebar_25',
        'prod_red_brick',
        'prod_safety_helmet',
        'prod_safety_vest',
        'prod_sand',
        'prod_scaffold',
        'prod_sealant',
        'prod_shower_mixer',
        'prod_sink_mixer',
        'prod_site_office_container',
        'prod_site_signage',
        'prod_skirting',
        'prod_smart_doorbell',
        'prod_smart_lock',
        'prod_smart_switch',
        'prod_smart_thermostat',
        'prod_socket',
        'prod_sofa_set',
        'prod_steel_angle',
        'prod_steel_ibeam',
        'prod_steel_plate',
        'prod_svc_cleaning',
        'prod_svc_environmental_study',
        'prod_svc_hvac_pm',
        'prod_svc_legal',
        'prod_svc_pest_control',
        'prod_svc_security_month',
        'prod_svc_subcontract_civil',
        'prod_svc_subcontract_elevator',
        'prod_svc_subcontract_facade',
        'prod_svc_subcontract_finishing',
        'prod_svc_subcontract_general',
        'prod_svc_subcontract_landscape',
        'prod_svc_subcontract_mep',
        'prod_svc_subcontract_structural',
        'prod_svc_topo_survey',
        'prod_switch_1g',
        'prod_switch_2g',
        'prod_tarpaulin',
        'prod_temp_fencing',
        'prod_thermostat',
        'prod_tile_ceramic_30',
        'prod_tile_ceramic_60',
        'prod_tile_porcelain_80',
        'prod_toilet_wc',
        'prod_tv_stand',
        'prod_virtual_tour',
        'prod_wall_putty',
        'prod_wardrobe_3door',
        'prod_wash_basin',
        'prod_washing_machine',
        'prod_water_filter',
        'prod_water_heater_80l',
        'prod_wire_mesh',
        'prod_yard_sign_rent',
        'prod_yard_sign_sale',
        'view_product_template_form_re',
    ],
    'atmta_procurement_evaluation': [
        'action_evaluation_audit_wizard',
        'action_evaluation_plan',
        'action_evaluation_round',
        'action_report_evaluation',
        'action_technical_evaluation',
        'report_evaluation',
        'report_evaluation_document',
        'view_evaluation_audit_wizard_form',
        'view_evaluation_plan_form',
        'view_evaluation_plan_list',
        'view_evaluation_round_form',
        'view_evaluation_round_list',
        'view_technical_evaluation_form',
    ],
    'atmta_procurement_purchase': [
        'action_realestate_purchase_orders',
        'view_purchase_order_form_re',
    ],
    'atmta_procurement_receipt': [
        'action_receipt_inspection',
        'view_picking_form_inspection',
        'view_receipt_inspection_form',
        'view_receipt_inspection_list',
        'view_receipt_inspection_search',
    ],
    'atmta_procurement_request': [
        'action_procurement_plan',
        'action_procurement_plan_line',
        'view_material_request_revise_form',
        'view_material_request_rfq_form',
        'view_procurement_plan_form',
        'view_procurement_plan_line_list',
        'view_procurement_plan_line_pivot',
        'view_procurement_plan_line_search',
        'view_procurement_plan_list',
        'view_procurement_plan_search',
    ],
    'atmta_procurement_sourcing': [
        'action_bid_response',
        'action_sourcing_event',
        'view_bid_response_list',
        'view_sourcing_event_form',
        'view_sourcing_event_list',
        'view_sourcing_event_search',
    ],
    'atmta_procurement_vendor': [
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
    ],
}

# The `purchase.order` extension moved to `atmta_procurement_purchase`. Odoo
# generated these identifiers itself, so they are matched by the model they
# describe rather than by name.
PURCHASE = 'atmta_procurement_purchase'
PURCHASE_MODELS = ['purchase.order', 'purchase.order.line']


def migrate(cr, version):
    if not version:
        return

    total = 0
    for new, xmlids in MOVED.items():
        cr.execute("""
            UPDATE ir_model_data SET module = %s
             WHERE module = %s AND name = ANY(%s)
        """, (new, OLD, list(xmlids)))
        total += cr.rowcount
        _logger.info("Wave 11: %s identifiers handed to %s", cr.rowcount, new)
        if cr.rowcount != len(xmlids):
            cr.execute("""SELECT name FROM ir_model_data
                           WHERE module = %s AND name = ANY(%s)""",
                       (new, list(xmlids)))
            present = {r[0] for r in cr.fetchall()}
            absent = [x for x in xmlids if x not in present]
            if absent:
                _logger.warning(
                    "Wave 11: %s identifiers for %s found under neither module "
                    "(first few: %s)", len(absent), new, absent[:5])

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model m
         WHERE d.module = %s AND d.model = 'ir.model'
           AND m.id = d.res_id AND m.model = ANY(%s)
    """, (PURCHASE, OLD, PURCHASE_MODELS))
    _logger.info("Wave 11: %s ir.model identifiers handed to %s",
                 cr.rowcount, PURCHASE)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields f
         WHERE d.module = %s AND d.model = 'ir.model.fields'
           AND f.id = d.res_id AND f.model = ANY(%s)
    """, (PURCHASE, OLD, PURCHASE_MODELS))
    _logger.info("Wave 11: %s field identifiers handed to %s",
                 cr.rowcount, PURCHASE)

    cr.execute("""
        UPDATE ir_model_data d SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.module = %s AND d.model = 'ir.model.fields.selection'
           AND s.id = d.res_id AND f.model = ANY(%s)
    """, (PURCHASE, OLD, PURCHASE_MODELS))
    _logger.info("Wave 11: %s selection identifiers handed to %s",
                 cr.rowcount, PURCHASE)

    _logger.info("Wave 11: %s declared identifiers re-pointed in total", total)
