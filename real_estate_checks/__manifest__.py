# -*- coding: utf-8 -*-
{
    'name': "Real Estate — PDC Treasury & Cheque Collection",
    'summary': "Post-dated cheque lifecycle for MENA property sales and rentals: "
               "custody, allocation, maturity, deposit batches, Odoo payments, "
               "bank reconciliation, bounce and replacement.",
    'description': """
Real Estate — PDC Treasury & Cheque Collection Engine
=====================================================
Built for MENA real-estate finance, where a sale settles in 20–60 post-dated
cheques and the paper itself is an asset that has to be tracked, guarded,
presented and chased.

The one rule everything follows: **a cheque is not money.**

    RECEIPT → CUSTODY → ALLOCATION → MATURITY → PRESENTATION
            → ODOO PAYMENT → BANK RECONCILIATION → CLEARED

with the exception paths a real treasury needs:

    BOUNCE → RE-PRESENT
    BOUNCE → REPLACEMENT (with a full lineage)
    BOUNCE → CANCELLATION / FOLLOW-UP

What this module owns
---------------------
* the physical instrument, its custody history and its presentation attempts
* what each cheque is meant to pay (one cheque to many obligations, and many
  cheques to one obligation)
* treasury maturity, forecasting and the deposit workbench
* the deposit batch you hand to a teller

What Odoo owns, and this module never duplicates
------------------------------------------------
* ``account.move`` — the invoice
* ``account.payment`` — the money
* reconciliation and bank statements — whether the money arrived
* ``account.batch.payment`` — the accounting batch, when Enterprise is present

A cheque becomes **Cleared** when Odoo's reconciliation says so, and by no
other route. Nothing here writes ``payment_state``, zeroes a residual, deletes
a payment or edits a posted entry.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.2',
    'category': 'Real Estate',
    'depends': [
        'atmta_dashboard',
        'atmta_real_estate',
        'real_estate_developer',
        'account',
    ],
    # `account_batch_payment` is Odoo Enterprise (OEEL-1) and this module is
    # LGPL-3, so it is deliberately NOT a dependency. The integration is
    # late-bound: when the model is in the registry a deposit's payments are
    # grouped into one batch so a single bank line reconciles them; when it is
    # not, each payment reconciles individually and nothing else changes.
    'demo': [
        'demo/demo.xml',
    ],
    'data': [
        # security → data → views → menus
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/cron.xml',
        'views/check_views.xml',
        'views/deposit_views.xml',
        'views/bounce_views.xml',
        'views/treasury_views.xml',
        'views/config_views.xml',
        'views/dashboard_views.xml',
        'views/sale_contract_views.xml',
        'wizard/bulk_check_wizard_views.xml',
        'wizard/deposit_wizard_views.xml',
        'wizard/bounce_wizard_views.xml',
        'report/deposit_slip.xml',
        'report/pdc_acknowledgement.xml',
        'views/report_actions.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Chart.js is NOT bundled here. The dashboard lazy-loads Odoo's own
            # 'web.chartjs_lib' via loadBundle(), the same pattern Module 1
            # settled on in 0.4 after its vendored duplicate was removed. That
            # guarantees one version and keeps the backend bundle small.
            '/real_estate_checks/static/src/scss/check_dashboard.scss',
            '/real_estate_checks/static/src/js/check_dashboard.js',
            '/real_estate_checks/static/src/js/treasury_overview.js',
            '/real_estate_checks/static/src/xml/check_dashboard.xml',
            '/real_estate_checks/static/src/xml/treasury_overview.xml',
        ],
        'web.assets_tests': [
            '/real_estate_checks/static/tests/tours/treasury_dashboard_tour.js',
            '/real_estate_checks/static/tests/tours/treasury_workflow_tour.js',
        ],
    },
    'application': True,
}
