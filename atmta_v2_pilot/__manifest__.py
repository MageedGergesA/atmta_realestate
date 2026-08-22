# -*- coding: utf-8 -*-
{
    'name': "ATMTA V2 — Navigation Pilot",
    'summary': "Visibility flag for the ATMTA V2 application shells. Grants no data access.",
    'description': """
ATMTA V2 Navigation Pilot
=========================

Wave 0 of the ATMTA V2 programme builds the target application navigation
*alongside* the existing 14-module navigation rather than replacing it. This
module carries the single group that decides who sees the new roots.

**This group grants no access to anything.**

It has no `ir.model.access` row, no `ir.rule`, and no `implied_ids`. Adding a
user to it changes exactly one thing: the seven V2 application roots become
visible to them. Every record they can read, write, create or delete before
joining is precisely what they can read, write, create or delete afterwards —
which is asserted, not merely intended, by the Wave 0 security evidence.

Navigation hiding is not security. The V2 menus reach the same actions, on the
same models, behind the same ACLs and the same record rules as the legacy menus
that remain in place beside them.

Temporary by design: when the V2 navigation is accepted and the legacy roots are
retired in a later, separate step, this module and its group are removed.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.0.1.0',
    'category': 'Real Estate',
    'depends': ['base'],
    'data': [
        'security/pilot_group.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
