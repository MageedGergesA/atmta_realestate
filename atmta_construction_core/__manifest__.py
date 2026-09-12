# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Core',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'The construction floor: WBS, cost codes, analytic wiring, accounts.',
    'description': """
ATMTA Construction Core
=======================

The floor of the Construction domain. It owns the structure every capability
above it codes against, and nothing that holds a value:

* **WBS** — the work-breakdown tree a project is divided into, optionally
  tied to a phase.
* **Cost codes** — the cost-classification tree, each able to carry its own
  analytic account, with the contingency flag.
* **Analytic wiring** — the abstract service that builds an analytic
  distribution for a project and cost code, and reads actuals back out.
* **Company accounts** — the four balance-sheet accounts construction posts
  retention and advances to, on the company and in Settings.

Measured at extraction: zero Monetary fields, and no reference to any other
construction model. Cost structure is read by thirty-odd files across the
monolith; it reads none of them back. That is what makes this a floor.

What it deliberately does **not** carry: contract packages, contractors, and
everything that derives an amount from them. Those are the commercial spine
and stay in ``real_estate_construction`` until a wave is chartered to move
financial logic.

Access is granted to the canonical ATMTA roles. The legacy construction
groups keep working through the bridge that ``real_estate_construction``
declares, exactly as Wave 6 did for procurement.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # `realestate.project` — every WBS node and cost code belongs to one.
        'atmta_project_core',
        # `realestate.phase` — one optional Many2one on the WBS node. This is
        # the only reason the floor knows the developer application exists.
        'real_estate_developer',
        # `account.account` for the company accounts, and the analytic plan,
        # account and line the distribution helper builds and reads.
        'account',
        # Canonical roles. The floor names these rather than the legacy
        # construction groups, which are declared by the module above it.
        'atmta_roles',
    ],
    'data': [
        # The two analytic plans the distribution helper names. They moved
        # with it: a floor that reaches up for a data record is not a floor.
        'data/analytic_plans.xml',
        'security/ir.model.access.csv',
        'security/construction_core_rules.xml',
        'views/cost_structure_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
