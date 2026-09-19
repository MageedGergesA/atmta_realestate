# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Brokerage",
    'summary': "Brokerage application navigation. Owns no business models.",
    'description': """
ATMTA Brokerage Application
===========================

Navigation only. Zero models, fields, ACL rows, record rules and new actions.

Deliberately not reproduced: **Matches & Shortlists** and **Agents Needing
Review** (contextual — reached from the lead and the agent), **Legacy Leads**
and **Commission Share Migration** (one-shot migration surfaces, classified
LEGACY). All four remain reachable from the legacy Brokerage menu.
""",
    'author': "Atmta", 'license': 'LGPL-3', 'version': '18.0.0.2.0',
    'category': 'Real Estate',
    'depends': ['real_estate_brokerage'],
    'uninstall_hook': 'uninstall_hook',
    'data': ['views/menus.xml', 'data/retire_legacy_navigation.xml'],
    'installable': True, 'application': True, 'auto_install': False,
}
