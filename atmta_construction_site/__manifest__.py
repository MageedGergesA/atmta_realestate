# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Site',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'BOQ, milestones, tasks, daily reports, labour, cost lines.',
    'description': """
ATMTA Construction Site
=======================

What was planned, what was built, and what it consumed.

* **Bill of quantities** — the measured scope: work items by category, BOQ
  lines with quantity and rate, and the variations that authorise more.
* **Milestones and tasks** — the plan the work is held against.
* **Daily reports** — the site record: work done, labour and equipment
  present, deliveries received, delays suffered. This is the evidence a claim
  is later built from.
* **Labour log** and **cost lines** — what the work consumed.

This is measurement, not money. A BOQ line knows its authorised quantity and
how much of it remains; what it does **not** know is what has been certified
against it, because a payment certificate is the instrument that turns a
measured quantity into an amount owed, and certificates stay in
``real_estate_construction``. The line asks through a seam, and the rule stays
stated once up there: only certificates that reached certified, invoiced or
paid count. Over-certification stays visible rather than floored at zero.

The BOQ's optional bridge into procurement is unchanged and still guarded on
the registry, so a database without the procurement chain simply does not
offer to raise a material request from a BOQ line.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # A daily report that records a delay raises a delay event, and the
        # daily-delay row points at it. Delay events belong to claims, so the
        # dependency is real and is declared rather than guessed. It is one
        # way only: nothing in claims reaches back into site measurement.
        'atmta_construction_claims',
        # Packages and contractors: whose scope is being measured.
        'atmta_construction_contract',
        # WBS and cost codes: how every line is coded.
        'atmta_construction_core',
        'atmta_project_core',
        # Deliveries arrive on stock pickings; consumption reaches the ledger.
        'stock',
        'account',
        'purchase',
        'mail',
        'atmta_roles',
    ],
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_site_rules.xml',
        # Wave 21 — this module's own screens, moved down from
        # `real_estate_construction`. They render only models it owns and
        # no field declared above it, which is what made the move safe.
        'views/milestone_views.xml',
        'views/construction_task_views.xml',
        'views/boq_views.xml',
        'views/cost_line_views.xml',
        'views/labor_log_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
