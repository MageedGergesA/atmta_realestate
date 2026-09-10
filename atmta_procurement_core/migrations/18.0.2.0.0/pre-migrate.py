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

OLD = 'real_estate_procurement'
NEW = 'atmta_procurement_core'
XMLIDS = [
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
