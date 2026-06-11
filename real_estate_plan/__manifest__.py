# -*- coding: utf-8 -*-
{
    'name': "Real Estate — 2D Plan Layer",
    'summary': "Upload a 2D site/floor plan to any property and overlay clickable polygon regions linking to its child properties (buildings, floors, units, villas).",
    'description': """
2D Plan Layer
=============
Upload an image (site plan, building elevation, floor plan, villa lot map)
to any realestate.property. Draw polygon regions on top with the same
"Add region → click vertices → Finish" editor used on the project master plan.

Works at every hierarchy level:
  * Compound / Project    → buildings + villas
  * Building              → floors
  * Floor                 → units
  * Villa                 → rooms (optional)
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '0.3',
    'category': 'Real Estate',
    'depends': ['atmta_real_estate', 'real_estate_maquette', 'real_estate_developer'],
    'data': [
        'security/ir.model.access.csv',
        'views/plan_region_views.xml',
        'views/property_views.xml',
        'views/project_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'real_estate_plan/static/src/js/plan_editor.js',
            'real_estate_plan/static/src/xml/plan_editor.xml',
            'real_estate_plan/static/src/js/plan_viewer.js',
            'real_estate_plan/static/src/xml/plan_viewer.xml',
        ],
    },
    'application': False,
}
