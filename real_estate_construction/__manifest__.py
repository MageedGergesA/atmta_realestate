# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Construction",
    'summary': "Enterprise construction project controls: WBS, budget baselines, commitments, ledger-backed actual, forecast/EAC, change control, BOQ certification, retention and advances, RFI/submittals/document control, QA/QC, claims and EOT, risk and issues, and the Construction Control Tower.",
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
    # 1.0.1 — M3 added one additive view file (procurement_coding_views.xml).
    # The bump exists so an existing database actually loads it on upgrade; no
    # Construction model, record, formula or test changes with it.
    'version': '1.0.1',
    'category': 'Real Estate',
    'depends': ['real_estate_developer', 'account', 'purchase', 'real_estate_procurement', 'stock'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/construction_rules.xml',
        'data/sequences.xml',
        'data/analytic_plans.xml',
        'wizard/subcontract_po_wizard_views.xml',
        'views/cost_structure_views.xml',
        'views/budget_views.xml',
        'views/forecast_views.xml',
        'views/change_views.xml',
        'views/information_views.xml',
        'views/quality_views.xml',
        'views/certification_views.xml',
        'views/claims_views.xml',
        'views/contractor_views.xml',
        'views/milestone_views.xml',
        'views/construction_task_views.xml',
        'views/boq_views.xml',
        'views/payment_certificate_views.xml',
        'views/cost_line_views.xml',
        'views/project_views.xml',
        'views/menus.xml',
        'views/owner_progress_billing_views.xml',
        'views/labor_log_views.xml',
        'views/construction_dashboard_views.xml',
        # Additive: Construction's coding fields on Procurement's documents.
        'views/procurement_coding_views.xml',
    ],
    'assets': {
        # Browser tours need @web_tour, which lives in the tests bundle.
        'web.assets_tests': [
            '/real_estate_construction/static/tests/tours/**/*.js',
        ],
        'web.assets_unit_tests': [
            '/real_estate_construction/static/tests/**/*.test.js',
        ],
        'web.assets_backend': [
            '/real_estate_construction/static/src/scss/construction_dashboard.scss',
            '/real_estate_construction/static/src/scss/control_tower.scss',
            '/real_estate_construction/static/src/js/control_tower/control_tower.js',
            '/real_estate_construction/static/src/xml/control_tower.xml',
            '/real_estate_construction/static/src/js/construction_dashboard.js',
            '/real_estate_construction/static/src/xml/construction_dashboard.xml',
        ],
    },
    'application': True,
}
