# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Customer Portal",
    'summary': "Customer self-service: sale contracts, installments, handover defects.",
    'description': """
Real Estate Customer Portal
===========================
Customers (buyers) can:
* View their sale contracts and progress
* See the installment schedule with status
* Track handover events and warranty
* Submit snagging issues
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.1',
    'category': 'Real Estate',
    'depends': [
        'real_estate_developer',
        'real_estate_handover',
        'real_estate_maquette',
        'portal',
        'website',
        'crm',
        'utm',
    ],
    'data': [
        'views/portal_templates.xml',
        'views/public_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            '/real_estate_portal/static/src/scss/public_portal.scss',
            '/real_estate_portal/static/src/js/portal_viewers.js',
        ],
    },
    'application': False,
}
