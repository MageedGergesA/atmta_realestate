# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Executive",
    'summary': "Cross-domain executive navigation. Owns no business models.",
    'description': """
ATMTA Executive Application
===========================

Navigation only. Zero models, fields, ACL rows, record rules and **zero new
actions** — every entry points at a surface that already exists and is already
tested.

The Suite Overview is the one dashboard this application adds
(``realestate.executive.dashboard`` on ``atmta_dashboard``). It invents no KPI:
occupancy is Rental's own rule, and the instalment and cheque figures use the
domains the Developer and Treasury dashboards already count with. Money is shown
in the company currency only, and every figure opens the records behind it. The
existing Control Tower, receivables and PDC-coverage reports, project list and
feasibility surfaces stay gathered here unchanged.

Absorbs the legacy **Investment** root: four models, 474 lines and zero tests do
not warrant an application of their own.

Every entry inherits the record rules of the model behind it, so two executives
in different companies see different numbers through the same menu — the app
adds no bypass.
""",
    'author': "Atmta", 'license': 'LGPL-3', 'version': '18.0.0.2.0',
    'category': 'Real Estate',
    'depends': [
        'real_estate_investment',
        'real_estate_construction',
        'real_estate_developer',
        'real_estate_checks',
        # Occupancy reuses Rental's own rule.
        'atmta_real_estate',
        'atmta_dashboard',
    ],
    'uninstall_hook': 'uninstall_hook',
    'data': ['views/menus.xml', 'views/dashboard_views.xml', 'data/retire_legacy_navigation.xml'],
    'installable': True, 'application': True, 'auto_install': False,
}
