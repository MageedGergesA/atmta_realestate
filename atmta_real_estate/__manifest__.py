# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Rental (Standalone)",
    'summary': "Self-contained real estate rental: properties, contracts, payments, dashboard, map.",
    'description': """
Real Estate Rental (Standalone)
================================
Self-contained module — no dependency on other real-estate modules.

* Property catalog with hierarchy (Compound / Building / Floor / Unit / Room)
* Properties map dashboard (Leaflet)
* Rental contracts (single-unit + multi-unit)
* Payment plans + increment / discount rules
* Auto-generated payment schedules (with Hijri dates)
* Utility line tracking per contract
* Rental history audit trail
* Security deposit lifecycle
* Auto-invoicing cron
* Maintenance ticket workflow
* Real-time rental dashboard (KPIs, charts, tenants)
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.3',
    'category': 'Real Estate',
    'depends': [
        'base',
        'product',
        'mail',
        'sale',
        'sale_stock',
        'account',
        'stock',
        'web_hierarchy',
    ],
    'data': [
        # security (must load first)
        'security/security.xml',
        'security/ir.model.access.csv',
        # stock locations for the unit-inventory lifecycle
        'data/stock_locations.xml',
        # sequences + crons
        'data/property_sequence.xml',
        'data/maintenance_request_sequence.xml',
        'data/contract_sequence.xml',
        'data/payment_schedule_sequence.xml',
        'data/schedule_actions.xml',
        'data/single_multi_contract_param.xml',
        'data/contract_line_expiry_cron.xml',
        'data/auto_invoice_cron.xml',
        # base property + maintenance views
        'views/property_type_views.xml',
        'views/property_views.xml',
        'views/property_image.xml',
        'views/maintenance_request.xml',
        'views/properties_map_dashboard.xml',
        # rental views
        'wizard/realestate_contracts_wiz.xml',
        'views/contract_views.xml',
        'views/contract_deposit_views.xml',
        'views/payment_plan_views.xml',
        'views/contract_line_views.xml',
        'views/contract_payment_views.xml',
        'views/contract_utility_line.xml',
        'views/contract_increment_rule.xml',
        'views/account_move.xml',
        'views/res_partner.xml',
        'views/property_rental_history.xml',
        'views/rental_property_views.xml',
        'views/rental_dashboard_views.xml',
        # menu last
        'views/menus.xml',
        # reports
        'reports/property_report.xml',
        'reports/real_estate_contract.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/atmta_real_estate/static/src/lib/leaflet/leaflet.css',
            '/atmta_real_estate/static/src/lib/leaflet/markercluster/MarkerCluster.css',
            '/atmta_real_estate/static/src/lib/leaflet/markercluster/MarkerCluster.Default.css',
            '/atmta_real_estate/static/src/lib/leaflet/leaflet.js',
            '/atmta_real_estate/static/src/lib/leaflet/markercluster/leaflet.markercluster.js',
            '/atmta_real_estate/static/src/lib/chartjs/chart.umd.js',
            '/atmta_real_estate/static/src/scss/property_map.scss',
            '/atmta_real_estate/static/src/scss/properties_map_dashboard.scss',
            '/atmta_real_estate/static/src/scss/rental_dashboard.scss',
            '/atmta_real_estate/static/src/js/property_map.js',
            '/atmta_real_estate/static/src/js/properties_map_dashboard.js',
            '/atmta_real_estate/static/src/js/rental_dashboard.js',
            '/atmta_real_estate/static/src/xml/property_map.xml',
            '/atmta_real_estate/static/src/xml/properties_map_dashboard.xml',
            '/atmta_real_estate/static/src/xml/rental_dashboard.xml',
        ],
    },
    'demo': [
        'demo/demo.xml',
    ],
    'application': True,
    'post_init_hook': 'post_init_hook',
}
