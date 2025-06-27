# -*- coding: utf-8 -*-
{
    'name': "atmta_real_estate",

    'summary': "Short (1 phrase/line) summary of the module's purpose",

    'description': """Rental Management""",

    'author': "Atmta",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'product', 'google_maps_viewer_widget', 'mail','sale'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/property_type_views.xml',
        'views/property_product_views.xml',
        'views/contract_views.xml',
        'data/contract_sequence.xml',
        'data/maintenance_request_sequence.xml',
        'data/schedule_actions.xml',
        'data/property_status_update.xml',
        'views/payment_plan_views.xml',
        'views/contract_line_views.xml',
        'views/property_image.xml',
        'views/contract_payment_views.xml',
        'views/contract_utility_line.xml',
        'views/contract_increment_rule.xml',
        'views/maintenance_request.xml',
        'views/account_move.xml',
        'views/contract_pivot_report.xml',
        'views/contract_line_pivot.xml',
        'views/menus.xml',
        'reports/property_report.xml',
        'reports/contract_report.xml',
        'reports/contract_payments_report.xml',
        'reports/contract_financial_summary.xml',
    ],
    "assets":
        {
            "web.assets_backend": [
                '/atmta_real_estate/static/src/js/org_chart.js',
                '/atmta_real_estate/static/src/xml/org_chart_template.xml',
                '/atmta_real_estate/static/src/scss/org_template_style.scss',
            ],
            "web.assets_frontend": [
                '/atmta_real_estate/static/src/scss/org_template_style.scss',
            ]
        },
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}
