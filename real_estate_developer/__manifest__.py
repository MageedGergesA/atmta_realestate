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
    'version': '0.5',
    'category': 'Real Estate',
    'depends': [
        'atmta_property_core',
        'atmta_property_stock',
        'atmta_account_tools',
        # Declares realestate.project / realestate.phase /
        # realestate.project.boundary.point. Listed first because this
        # module extends all three, and because the dependency is what
        # guarantees Project Core is loaded before the migration below
        # hands the model identifiers over to it.
        'atmta_project_core',
        # Sale contracts and installments are sale orders and order lines.
        # `sale` used to arrive through the Rental app; it is declared now that
        # Developer installs without leasing.
        'sale',
        # Sale contracts mirror onto a bridge sale order.
        'atmta_sale_bridge',
        'mail',
        'account',
        'stock',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/developer_rules.xml',
        'data/sequences.xml',
        'data/cron.xml',
        'views/account_payment_term_views.xml',
        'views/project_views.xml',
        'views/phase_views.xml',
        'views/reservation_views.xml',
        'views/sale_contract_views.xml',
        'views/sale_installment_views.xml',
        'views/property_developer_views.xml',
        'views/commercial_views.xml',
        'views/unit_release_views.xml',
        'views/unit_block_views.xml',
        'views/pricing_views.xml',
        'views/payment_plan_views.xml',
        'views/reservation_v2_views.xml',
        'views/contract_v2_views.xml',
        'views/amendment_views.xml',
        # `menus.xml` defines `menu_developer_root`; `report_actions.xml` hangs
        # the Reports submenu off it, so menus must load first. The reverse
        # order happens to survive an *upgrade* (the root menu already exists
        # in the database) but breaks a fresh install.
        'views/menus.xml',
        'views/report_actions.xml',
        'views/res_partner_developer_views.xml',
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
