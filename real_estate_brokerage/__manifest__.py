# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Sales & Brokerage",
    'summary': "Property sales lifecycle: listings, leads, viewings, offers, transactions, commissions.",
    'description': """
Real Estate Sales & Brokerage
=============================
Counterpart to the rental module. Manages the for-sale lifecycle of properties:

* Listings with state machine (draft / active / under_offer / sold / withdrawn / expired)
* Buyer leads with preferences and matching to listings
* Viewing scheduling with calendar integration
* Offer management with accept / reject
* Sale transactions with milestones (deposit / contract / closing)
* Commission tracking with multi-party splits
* Supports both in-house sales and third-party brokerage
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.4',
    'category': 'Real Estate',
    'depends': [
        'atmta_property_core',
        'atmta_account_tools',
        'real_estate_developer',
        # A transaction confirms a sale order. `sale` used to arrive through the
        # Rental app; it is declared now that Brokerage installs without leasing.
        'sale',
        # In-house transactions mirror onto a bridge sale order.
        'atmta_sale_bridge',
        # `crm` is the customer spine. The Phase 0 audit found that the
        # website, the public API and the customer portal already created and
        # read `crm.lead`, while Brokerage 0.1's own `realestate.lead` had no
        # consumer outside this module. Depending on CRM is what lets the two
        # pipelines become one — and it is also what unblocks the project/team
        # scoping Module 2 deferred for exactly this reason.
        'crm',
        'mail',
        'account',
    ],
    'data': [
        # security
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/record_rules.xml',
        # data
        'data/sequences.xml',
        'data/crm_stages.xml',
        'data/cron.xml',
        # views
        'views/marketing_channel_views.xml',
        'views/lost_reason_views.xml',
        'views/listing_views.xml',
        'views/lead_views.xml',
        'views/crm_lead_views.xml',
        'views/broker_views.xml',
        'views/viewing_views.xml',
        'views/offer_views.xml',
        'views/transaction_views.xml',
        # After the 0.1 views it extends: `listing_v2_views` inherits the
        # offer, transaction and viewing forms, which do not exist yet on a
        # fresh install if it loads with the listing views.
        'views/listing_v2_views.xml',
        'views/property_views.xml',
        'views/res_users_views.xml',
        'views/menus.xml',
        'views/brokerage_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_tests': [
            '/real_estate_brokerage/static/tests/tours/brokerage_tour.js',
        ],
        'web.assets_backend': [
            '/real_estate_brokerage/static/src/scss/brokerage_dashboard.scss',
            '/real_estate_brokerage/static/src/js/brokerage_dashboard.js',
            '/real_estate_brokerage/static/src/xml/brokerage_dashboard.xml',
        ],
    },
    'application': True,
}
