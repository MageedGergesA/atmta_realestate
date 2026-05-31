# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Construction",
    'summary': "Construction tracking: milestones, contractors, cost vs budget, progress %.",
    'description': """
Real Estate Construction
========================
Track construction progress for projects and phases:
* Milestones with weights and completion %
* Contractor management
* Cost tracking against project budget
* Auto-computed project/phase progress
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    'depends': ['real_estate_developer', 'account', 'purchase', 'real_estate_procurement', 'stock'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'wizard/subcontract_po_wizard_views.xml',
        'views/contractor_views.xml',
        'views/milestone_views.xml',
        'views/construction_task_views.xml',
        'views/payment_certificate_views.xml',
        'views/cost_line_views.xml',
        'views/project_views.xml',
        'views/menus.xml',
        'views/construction_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/real_estate_construction/static/src/scss/construction_dashboard.scss',
            '/real_estate_construction/static/src/js/construction_dashboard.js',
            '/real_estate_construction/static/src/xml/construction_dashboard.xml',
        ],
    },
    'application': True,
}
