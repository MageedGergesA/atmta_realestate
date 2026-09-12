# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Cost',
    'version': '18.0.4.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'Budget, commitment, forecast, cost reporting, risk.',
    'description': """
ATMTA Construction Cost
=======================

What the job was authorised to spend, what it has committed, and what it now
expects to cost.

* **Budget** and budget lines — the baseline. It is never rewritten. An
  approved change produces **budget change records** beside it, so every unit
  of variance points at the change order that authorised it.
* **Commitment** and **commitment changes** — what is contracted, and what a
  change did to it. A package with purchase orders is represented by its
  orders and contributes nothing of its own; that rule lives here, stated
  once, and is what keeps an 8M package with an 8M order from reading as 16M.
* **Revenue change** — owner-side value, kept apart because cost and revenue
  are not the same money.
* **Forecast**, forecast lines and **forecast adjustments** — the current
  expectation and what moved it. An approved forecast is history and is never
  rewritten, even once an anticipation inside it has become real.
* **Cost reports**, **cost sheets** and **retention disclosure** — how it is
  read back.
* **Project controls**, **risks**, **risk actions**, **issues** and
  **exposure** — what might still move it.
* **Change authority** — the matrix an approval is checked against.

This is the last cluster in the construction domain that owns data. What
remains in ``real_estate_construction`` reads every module beneath it and owns
nothing: the control tower, the integrity audit, the dashboard, and the
data-control exceptions.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # Certificates and retention: what has actually been paid against the
        # forecast, and the disclosure that reports it.
        'atmta_construction_certification',
        # BOQ lines and cost lines: what the budget is measured against.
        'atmta_construction_site',
        # Change orders: what authorises a budget or commitment change.
        'atmta_construction_change',
        # Claims and delay: risk that has materialised.
        'atmta_construction_claims',
        'atmta_construction_contract',
        'atmta_construction_core',
        'atmta_project_core',
        'account',
        'purchase',
        'mail',
        'atmta_roles',
    ],
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_cost_rules.xml',
        # Wave 21 — this module's own screens, moved down from
        # `real_estate_construction`. They render only models it owns and
        # no field declared above it, which is what made the move safe.
        'views/forecast_views.xml',
        # Wave 24 — the budget, contract-package and cost-report screens.
        # They render a package alongside a budget, and this module already
        # depends on contract, so the file moves whole rather than splitting.
        'views/budget_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
