# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Handover",
    'summary': "Unit handover, snagging, defect tracking, and warranty management.",
    'description': """
Real Estate Handover
====================
* Handover events tied to sale contracts
* Configurable handover checklist templates
* Snagging issues with severity and contractor assignment
* Warranty start / end tracking
* Unit readiness % computed from construction
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    'depends': ['real_estate_developer', 'real_estate_construction'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/cron.xml',
        'data/checklist_default.xml',
        'views/checklist_template_views.xml',
        'views/handover_views.xml',
        'views/snagging_views.xml',
        'views/warranty_views.xml',
        'views/property_views.xml',
        'views/menus.xml',
        'views/handover_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/real_estate_handover/static/src/scss/handover_dashboard.scss',
            '/real_estate_handover/static/src/js/handover_dashboard.js',
            '/real_estate_handover/static/src/xml/handover_dashboard.xml',
        ],
    },
    'application': True,
}
