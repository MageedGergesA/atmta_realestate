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
    'version': '0.1',
    'category': 'Real Estate',
    'depends': [
        'atmta_real_estate',
        'real_estate_developer',
        'mail',
        'account',
    ],
    'data': [
        # security
        'security/security.xml',
        'security/ir.model.access.csv',
        # data
        'data/sequences.xml',
        'data/cron.xml',
        # views
        'views/marketing_channel_views.xml',
        'views/lost_reason_views.xml',
        'views/listing_views.xml',
        'views/lead_views.xml',
        'views/viewing_views.xml',
        'views/offer_views.xml',
        'views/transaction_views.xml',
        'views/property_views.xml',
        'views/res_users_views.xml',
        'views/menus.xml',
        'views/brokerage_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/real_estate_brokerage/static/src/scss/brokerage_dashboard.scss',
            '/real_estate_brokerage/static/src/js/brokerage_dashboard.js',
            '/real_estate_brokerage/static/src/xml/brokerage_dashboard.xml',
        ],
    },
    'application': True,
}
