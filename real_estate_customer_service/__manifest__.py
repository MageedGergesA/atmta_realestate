# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Customer Service",
    'summary': "Standalone tickets / complaints for owners and tenants, "
               "independent of handover snagging.",
    'description': """
Real Estate — Customer Service
==============================
Post-handover support workflow: catalog of ticket categories, SLA-aware
lifecycle (new -> assigned -> in progress -> resolved -> closed), owner/
tenant portal-friendly, chatter for the full conversation history.

Kept separate from handover snagging on purpose:
* Handover = one-shot punch-list before ownership transfers
* Customer Service = ongoing issue reporting after handover
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.2',
    'category': 'Real Estate',
    'depends': [
        # A ticket is about a property and, optionally, a project; neither
        # needs the leasing application.
        'atmta_property_core',
        'atmta_project_core',
        'mail',
        # The dashboard screen; the figures are computed here.
        'atmta_dashboard',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/categories.xml',
        'data/sla_cron.xml',
        'views/ticket_views.xml',
        'views/category_views.xml',
        'views/menus.xml',
        'views/dashboard_views.xml',
    ],
    'application': True,
}
