# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Construction",
    'summary': "Construction application navigation. Owns no business models.",
    'description': """
ATMTA Construction Application
==============================

Navigation only. **Zero** models, fields, ACL rows or record rules, and zero
Python changes to `real_estate_construction`, which stays frozen at its 563-test
baseline.

The 49-menu legacy surface is recomposed into the target sections from
`10_TARGET_MENU_TREE.md` §3. Section gating uses the EXISTING Construction
groups exactly as the architecture specifies: Cost and BOQ are addressed to
Cost Control / QS, Commercial and Claims to the Commercial / Claims role, and
`group_construction_manager` implies both — so a manager loses nothing.

Three legacy destinations are deliberately not reproduced:

* **Legacy Dashboard (deprecated)** — classified LEGACY / REMOVE. Reachable
  from the legacy menu, which is untouched.
* **Inspections** and **Revision Register** — classified REDUNDANT: both models
  are reached from their parent (Inspection Requests, Document Register).

Transitional dependency on `real_estate_construction`; re-points at the
Construction capability modules in Wave 6.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.0.1.0',
    'category': 'Real Estate',
    'depends': [
        'real_estate_construction',
        'atmta_v2_pilot',
    ],
    'data': [
        'data/workspace_actions.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
