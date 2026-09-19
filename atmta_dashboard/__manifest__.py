# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Dashboard",
    'summary': "One dashboard screen for every ATMTA application.",
    'description': """
ATMTA Dashboard
===============

The screen, not the numbers. An application that wants a dashboard inherits
``atmta.dashboard.provider`` and declares its sections, tiles, charts and quick
actions; this module renders them with one client action (tag
``atmta_dashboard``, the provider model in the action's ``params``).

Every figure is counted by the provider under the user's own access rights and
record rules, and a tile the user cannot read is left out rather than shown as
zero. A tile opens exactly the records it counted.

It depends on ``web`` only, so Customer Service, Procurement, Property
Operations and Executive can each carry a dashboard without depending on one
another.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.1.0.0',
    'category': 'Real Estate',
    'depends': ['web'],
    'data': [],
    'assets': {
        'web.assets_backend': [
            'atmta_dashboard/static/src/scss/atmta_dashboard.scss',
            'atmta_dashboard/static/src/js/atmta_dashboard.js',
            'atmta_dashboard/static/src/xml/atmta_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
}
