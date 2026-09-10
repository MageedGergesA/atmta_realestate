# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Contract',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'The commercial floor: contractors and contract packages.',
    'description': """
ATMTA Construction Contract
===========================

A **contractor** is who the work is bought from. A **contract package** is the
commercial agreement with one of them for one scope: Main Civil, MEP, Facade,
Fit-Out.

These two models sit below every construction capability because every
capability points at them. Measured before the move: 25 inbound files for the
package and 20 for the contractor, and the references are Many2one fields, not
behaviour. Seams can stand in for a method call across a module boundary; they
cannot stand in for a comodel, because a Many2one may not point at a model that
is defined above it. Extracting documents, quality or site operations therefore
required moving these two first.

What lives here is what a contract is on its own terms: who, what scope, what
was agreed, which purchase orders carry it, what has been billed and paid
against the vendor ledger.

What does **not** live here is everything the rest of construction derives:

* approved variations, from commitment changes
* extensions of time and claimed days, from EOT records and claims
* the commitment precedence rule, from ``commitment.py``
* certified totals and retention held, from payment certificates and the
  retention register
* milestones

Those fields are declared here and computed through seams that answer
neutrally, so this module is truthful on its own: with nothing above it, a
package has no variations and a contractor has certified nothing.
``real_estate_construction`` supplies the relations and overrides every seam
with the arithmetic it already had. No amount is calculated differently; the
module that states the calculation changed.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # WBS and cost codes: a package names the scope it covers.
        'atmta_construction_core',
        # `realestate.project` — every package belongs to one.
        'atmta_project_core',
        # Purchase orders carry the commitment; vendor bills carry what has
        # been billed and paid. Both are read directly here.
        'purchase',
        'account',
        # Chatter on both models.
        'mail',
        # Canonical roles. The legacy construction groups are declared above
        # this module and bridged there.
        'atmta_roles',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/construction_contract_rules.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
