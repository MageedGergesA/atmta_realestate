# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Treasury",
    'summary': "Treasury application navigation. Owns no business models.",
    'description': """
ATMTA Treasury Application
==========================

Navigation only. Zero models, fields, ACL rows, record rules and new actions.

This is the one place in Wave 0 where the application count goes **up**.
`03_TARGET_APP_ARCHITECTURE.md` promotes Treasury out of the 22-menu sub-tree it
occupies at depth 2-3 inside "Real Estate" today — a system with its own four
role tiers, five scheduled actions, 16 models and 325 tests, serving rental and
sale contracts alike, and therefore wrongly filed under Leasing.

Group gating mirrors the legacy menus exactly, using the existing
`real_estate_checks` groups.
""",
    'author': "Atmta", 'license': 'LGPL-3', 'version': '18.0.0.2.0',
    'category': 'Real Estate',
    'depends': ['real_estate_checks'],
    'uninstall_hook': 'uninstall_hook',
    'data': ['views/menus.xml', 'data/retire_legacy_navigation.xml'],
    'installable': True, 'application': True, 'auto_install': False,
}
