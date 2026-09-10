# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Procurement",
    'summary': "Procurement application navigation. Owns no business models.",
    'description': """
ATMTA Procurement Application
=============================

Navigation only. This module defines **zero business models**, zero fields, zero
ACL rows and zero record rules. It composes the capability that already lives in
`real_estate_procurement` behind the target menu tree from
`10_TARGET_MENU_TREE.md`.

Every leaf reuses an existing `ir.actions.act_window`, with one exception —
`action_v2_my_approvals`, a personal queue whose domain is
`[('approver_user_id','=',uid),('decision','=','pending')]`. No existing action
carried that domain, and a personal queue is the one thing a workspace has to
have. It reuses the existing views of `realestate.procurement.approval.step`.

Legacy `real_estate_procurement` navigation is untouched and remains available.

Transitional dependency: this shell depends on `real_estate_procurement` today.
When Waves 4 and 1 extract the procurement capabilities, this manifest re-points
at `atmta_procurement_award` and `atmta_procurement_receipt` per
`04_TARGET_MODULE_CATALOG.md`. See CONFLICT_001 in the Wave 0 evidence.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.0.3.0',
    'category': 'Real Estate',
    'depends': ['real_estate_procurement', 'atmta_v2_pilot', 'atmta_roles'],
    'data': [
        'data/workspace_actions.xml',
        # Wave 11 — procurement policy on the project form; see the file header.
        'views/project_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
