# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Change',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'Change events and change orders: notice, pricing, authority.',
    'description': """
ATMTA Construction Change
=========================

The two documents that separate noticing a change from authorising one.

* **Change event** — somebody saw something that may change the contract. It
  is not a budget change, not a variation and not a commitment, and keeping it
  that way is the point: an event that moved budget would make every site
  engineer's guess an authorisation.
* **Change order** — the instrument. Lines coded by impact side (commitment,
  budget, contingency, revenue), markup, revisions, and the approvals it had
  to clear before it could be implemented.

The workflow lives here in full: price, submit, assess, negotiate, approve,
implement. What an approved order *produces* does not:

* budget change records, against a baselined budget
* commitment change records, beside the original commitment
* revenue change records, because owner-side value is not the same money
* forecast anticipations marked as having become real
* the authority matrix an approval is checked against

Every one of those is a ``real_estate_construction`` model, so each is a seam
this module calls and that module answers. Below it, an approved order changes
no budget, which is the truthful answer when there is no budget to change.

Implementation stays idempotent for the same reason it always was: what stops
a second run is the existence of the linked records, and `_already_implemented`
is how the workflow asks that question of a module it cannot see.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # Packages and contractors: what a change is raised against.
        'atmta_construction_contract',
        # WBS and cost codes: how every line is coded.
        'atmta_construction_core',
        'atmta_project_core',
        'mail',
        'atmta_roles',
    ],
    # No views. The change screens are loaded by `real_estate_construction`,
    # which declares the implementation links they show.
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_change_rules.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
