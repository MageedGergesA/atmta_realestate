# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Rental (Standalone)",
    'summary': "Leasing: contracts, payments, occupancy, dashboard and map.",
    'description': """
Real Estate Rental
==================
The leasing application.

It is no longer self-contained, and deliberately so. The property model and its
screens belong to `atmta_property_core`, the four stock locations a unit's quant
moves between to `atmta_property_stock`, and the two accounting helpers to
`atmta_account_tools`. Those moved out so Developer, Brokerage and Construction
could reach them without installing the whole leasing application. What is left
here is leasing itself.

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
    'version': '0.11',
    'category': 'Real Estate',
    'depends': [
        'atmta_account_tools',
        # Declares realestate.property, property.type, property.usage and
        # property.image. Listed first because this module extends all of
        # them, and because the dependency is what guarantees Property Core
        # is loaded before the migration hands the identifiers over to it.
        'atmta_property_core',
        # Owns the four stock locations a unit's quant moves between.
        # They were this module's data records, which is why Developer
        # needed the whole Rental app just to find the root location.
        'atmta_property_stock',
        # Bridge orders on sale.order (deal -> sale order, no deliveries). Shared
        # with Developer and Brokerage, so it lives in its own module.
        'atmta_sale_bridge',
        'base',
        'product',
        'mail',
        'sale',
        'sale_stock',
        'account',
        'stock',
        'web_hierarchy',
    ],
    'external_dependencies': {
        'python': ['hijridate'],
    },
    'data': [
        # security (must load first)
        'security/security.xml',
        'security/leasing_groups.xml',
        'security/ir.model.access.csv',
        'security/leasing_rules.xml',
        # sequences + crons
        'data/maintenance_request_sequence.xml',
        'data/contract_sequence.xml',
        'data/payment_schedule_sequence.xml',
        'data/schedule_actions.xml',
        'data/single_multi_contract_param.xml',
        'data/auto_invoice_cron.xml',
        # enterprise leasing foundation (v0.6)
        'data/leasing_sequences.xml',
        'data/leasing_crons.xml',
        # base property + maintenance views
        'views/property_type_views.xml',
        # The base property views live in atmta_property_core; this file holds
        # the Rental action, and the one below adds what only Rental can show.
        'views/property_views.xml',
        'views/property_views_rental.xml',
        'views/property_image.xml',
        'views/maintenance_request.xml',
        'views/properties_map_dashboard.xml',
        # rental views
        'wizard/realestate_contracts_wiz.xml',
        'views/contract_views.xml',
        'views/contract_deposit_views.xml',
        'views/contract_payment_views.xml',
        'views/contract_utility_line.xml',
        'views/contract_increment_rule.xml',
        'views/account_move.xml',
        'views/res_partner.xml',
        'views/property_rental_history.xml',
        'views/rental_property_views.xml',
        'views/rental_dashboard_views.xml',
        # enterprise leasing foundation (v0.6)
        'views/lease_operations_views.xml',
        # Inherits the deposit form defined just above.
        'wizard/deposit_settlement_wizard.xml',
        'views/lease_lifecycle_views.xml',
        'views/move_views.xml',
        'views/rent_roll_views.xml',
        'views/property_enterprise_views.xml',
        'views/contract_enterprise_views.xml',
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
            # Chart.js is NOT bundled here. The dashboard lazy-loads Odoo's
            # own 'web.chartjs_lib' (v4.4.1) via loadBundle(), the same way
            # the graph view and gauge field do. That avoids shipping a
            # duplicate copy in assets_backend and guarantees one version.
            '/atmta_real_estate/static/src/scss/property_map.scss',
            '/atmta_real_estate/static/src/scss/properties_map_dashboard.scss',
            '/atmta_real_estate/static/src/js/map_tiles.js',
            '/atmta_real_estate/static/src/js/property_map.js',
            '/atmta_real_estate/static/src/js/properties_map_dashboard.js',
            '/atmta_real_estate/static/src/xml/property_map.xml',
            '/atmta_real_estate/static/src/xml/properties_map_dashboard.xml',
            # ---- Rental Dashboard (the only implementation) ----
            # The first-generation dashboard, which registered the same
            # "realestate.rental_dashboard" action key, was removed in 0.10.
            '/atmta_real_estate/static/src/scss/dashboard/rental_dashboard.scss',
            '/atmta_real_estate/static/src/js/dashboard/dashboard_schema.js',
            '/atmta_real_estate/static/src/js/dashboard/kpi_card.js',
            '/atmta_real_estate/static/src/js/dashboard/dashboard_chart.js',
            '/atmta_real_estate/static/src/js/dashboard/dashboard_map.js',
            '/atmta_real_estate/static/src/js/dashboard/rental_dashboard.js',
            '/atmta_real_estate/static/src/xml/dashboard/rental_dashboard.xml',
        ],
        # HOOT unit tests. Kept to *.test.js so the tour below is not pulled
        # into the unit-test bundle (it needs the tour service, which the HOOT
        # bundle does not load).
        'web.assets_unit_tests': [
            '/atmta_real_estate/static/tests/**/*.test.js',
        ],
        # Integration tour — needs @web_tour, which lives in the tests bundle.
        'web.assets_tests': [
            '/atmta_real_estate/static/tests/tours/**/*.js',
        ],
    },
    'demo': [
        'demo/demo.xml',
    ],
    'application': True,
    'post_init_hook': 'post_init_hook',
}
