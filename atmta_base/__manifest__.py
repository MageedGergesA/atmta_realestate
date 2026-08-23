# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Base',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Foundation',
    'summary': 'Dependency-neutral technical floor for the ATMTA suite.',
    'description': """
ATMTA Base
==========

The bottom of the ATMTA dependency graph. It owns the suite's module category
anchor and nothing else.

It deliberately ships **no models**. The architecture catalog assigns three
(`contract.type`, `organization.type`, `realestate.account.tools`); each was
audited against the rule that a shared concept belongs here only when it is both
cross-suite and dependency-neutral, and each failed:

* `organization.type` and `contract.type` are single-field lookup tables used by
  exactly one module, `atmta_real_estate`. They are leasing reference data, not
  generic reference data.
* `realestate.account.tools` genuinely is cross-suite -- 28 call sites in four
  modules -- but it drives `account.payment.register`, so hosting it would make
  the foundation depend on `account` and pull the accounting stack under every
  ATMTA module.

See `03_ATMTA_BASE_SCOPE.md` in the Wave 4 evidence package. Re-homing the
accounting helper is a real question for a later wave; it is not this one's,
because this module exists to be the floor.

No models, no menus, no actions, no ACLs, no record rules, no business data.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': ['data/atmta_categories.xml'],
    'application': False,
    'installable': True,
    'auto_install': False,
}
