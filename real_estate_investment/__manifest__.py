# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Investment Analysis",
    'summary': "Feasibility studies with NPV / IRR / DCF and scenario analysis.",
    'description': """
Real Estate Investment Analysis
===============================
* Feasibility studies tied to projects
* Periodic cash flow tracking (inflows / outflows)
* Auto-computed NPV, IRR, payback period
* Scenario analysis (best / expected / worst case)
* Sensitivity to discount rate, cost overruns, sales velocity
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    # Investment appraises a project; it does not sell units. Wave 2 gave
    # `realestate.project` its own module, and this addon referenced
    # nothing else from the Development application -- no model, no group,
    # no view, no menu parent, no XML ID -- so the dependency now points at
    # the core it actually needs.
    'depends': ['atmta_project_core'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'views/feasibility_views.xml',
        'views/scenario_views.xml',
        'views/menus.xml',
        'views/investment_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/real_estate_investment/static/src/scss/investment_dashboard.scss',
            '/real_estate_investment/static/src/js/investment_dashboard.js',
            '/real_estate_investment/static/src/xml/investment_dashboard.xml',
        ],
    },
    'application': True,
}
