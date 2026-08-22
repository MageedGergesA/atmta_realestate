# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Developer",
    'summary': "Project lifecycle: phases, inventory, reservations, installment plans, sales contracts.",
    'description': """
Real Estate Developer
=====================
Manages off-plan and finished-property developer sales:

* Projects with phases
* Reservation / booking workflow with hold expiry
* Installment plan templates (down + recurring + balloon + grace)
* Sales contracts with auto-generated installment schedules
* Per-installment invoicing
* Cancellation + transfer + refund workflows
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.4',
    'category': 'Real Estate',
    'depends': [
        # Declares realestate.project / realestate.phase /
        # realestate.project.boundary.point. Listed first because this
        # module extends all three, and because the dependency is what
        # guarantees Project Core is loaded before the migration below
        # hands the model identifiers over to it.
        'atmta_project_core',
        'atmta_real_estate',
        'mail',
        'account',
        'stock',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/cron.xml',
        'views/account_payment_term_views.xml',
        'views/project_views.xml',
        'views/phase_views.xml',
        'views/reservation_views.xml',
        'views/sale_contract_views.xml',
        'views/sale_installment_views.xml',
        'views/property_developer_views.xml',
        'views/res_partner_developer_views.xml',
        'views/menus.xml',
        'views/developer_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/real_estate_developer/static/src/scss/developer_dashboard.scss',
            '/real_estate_developer/static/src/scss/project_boundary_widget.scss',
            '/real_estate_developer/static/src/js/developer_dashboard.js',
            '/real_estate_developer/static/src/js/project_boundary_widget.js',
            '/real_estate_developer/static/src/xml/developer_dashboard.xml',
            '/real_estate_developer/static/src/xml/project_boundary_widget.xml',
        ],
    },
    'demo': [
        'demo/demo.xml',
    ],
    'application': True,
}
