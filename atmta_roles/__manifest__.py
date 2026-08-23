# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Roles',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Foundation',
    'summary': 'Suite-wide canonical role identities. No permissions.',
    'description': """
ATMTA Roles
===========

The stable role-identity layer for the whole suite: `ATMTA User` and the domain
role tiers beneath it, and nothing else.

Per `12_ROLE_ARCHITECTURE.md` section 4 -- *"atmta_roles owns only the
suite-level hierarchy ... It defines no ACL and no record rule"* -- every group
here is **permission-neutral**. No ACL row, no record rule, no menu, no model.
They exist so that a capability module can name a role without depending on the
monolith that happens to define that role today.

What this module deliberately does NOT do in this wave:

* it does not re-home any existing group, so no `res.groups` id, no user
  membership, no ACL reference and no record rule moves;
* it does not make any legacy group imply a canonical one. That mapping edits
  security XML in twelve business modules, eighteen of whose groups live in
  files belonging to other unpublished sessions, and it is not needed to
  establish the dependency floor. It belongs to the wave that rebinds
  capability ACLs.

The result is additive: nothing any existing user can do changes.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': ['atmta_base'],
    'data': ['security/atmta_roles.xml'],
    'application': False,
    'installable': True,
    'auto_install': False,
}
