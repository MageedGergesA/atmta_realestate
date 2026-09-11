# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Quality',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'Inspection and test plans, inspections, observations, NCRs.',
    'description': """
ATMTA Construction Quality
==========================

Whether the work was built to the specification, and what happened when it
was not.

* **Inspection and test plans** with their items: what has to be checked, at
  which stage, against which document.
* **Checklist templates** and their lines: the reusable form an inspection is
  carried out against.
* **Inspection requests** and the **inspections** that answer them, line by
  line.
* **Observations**: something seen on site that is not yet a formal finding,
  and can be escalated into one.
* **NCRs**: the formal non-conformance, its disposition, and the corrective
  work it demands.

Quality sits below the commercial capabilities. An inspection is raised
against a package, coded to a WBS node and a cost code, and refers to a
document revision or a submittal — all of which are already beneath it.

Two things point the other way and are therefore declared above, in
``real_estate_construction``:

* an NCR may raise a **change event**, which is where corrective work becomes
  money. It was always an action somebody takes, never automatic;
* the reason wizard's **amend a daily report** mode. Daily reports are still
  up there, so that module registers the mode through the seam this one
  exposes. A mode nothing has registered is refused with a sentence rather
  than a traceback.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # Packages and contractors: what an inspection is raised against.
        'atmta_construction_contract',
        # Document revisions and submittals: what it is checked against.
        'atmta_construction_documents',
        # WBS and cost codes: what it is coded to.
        'atmta_construction_core',
        'atmta_project_core',
        'mail',
        'atmta_roles',
    ],
    # No views. The quality screens render `change_event_id` and the action
    # beside it, and the same file carries two daily-report views, so they are
    # loaded by `real_estate_construction` -- the lowest module that has every
    # half. Wave 11 and Wave 14 made the same call for the same reason.
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_quality_rules.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
