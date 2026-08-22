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

Deliberately conservative. `03_TARGET_APP_ARCHITECTURE.md` gives Executive a
Financial Position and a Portfolio view, but Wave 0 invents no KPI, aggregates
no figure and creates no dashboard. What is here is the existing Control Tower,
the existing receivables and PDC-coverage reports, the existing project list and
the existing feasibility surfaces, gathered in one place for the audience that
reads them. Anything more waits until the reporting capability is extracted and
has a test baseline.

Absorbs the legacy **Investment** root: four models, 474 lines and zero tests do
not warrant an application of their own.

Every entry inherits the record rules of the model behind it, so two executives
in different companies see different numbers through the same menu — the app
adds no bypass.
""",
    'author': "Atmta", 'license': 'LGPL-3', 'version': '18.0.0.1.0',
    'category': 'Real Estate',
    'depends': [
        'real_estate_investment',
        'real_estate_construction',
        'real_estate_developer',
        'real_estate_checks',
        'atmta_v2_pilot',
    ],
    'data': ['views/menus.xml'],
    'installable': True, 'application': True, 'auto_install': False,
}
