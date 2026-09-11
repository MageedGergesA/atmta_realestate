# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Claims',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'Delay events, notices, extensions of time, and claims.',
    'description': """
ATMTA Construction Claims
=========================

The contractual clock, and the money that hangs off it.

* **Delay event** — what happened, when it started, when it ended.
* **Notice** — telling the other side, inside the period this contract allows.
  The period is per contract and never assumed: 28 days is FIDIC's number, not
  everybody's.
* **Extension of time** — the relief claimed and the days determined.
* **Claim** — the money, with cost lines, evidence, submissions, and the
  determination that answers it.

This module sits above document control, quality and change, because a claim
cites all of them: the RFI that asked the question, the NCR that recorded the
defect, the change event that was opened, and the drawing revision the whole
argument refers to. Those are its dependencies, not its seams.

It sits *below* daily reporting, which is still part of
``real_estate_construction``. A delay event is evidenced by the daily reports
that recorded it, so the relation and the two things the claim chronology
needs from it are seams that module answers. A delay event that no daily
report recorded contributes nothing to the chronology, which is what it would
contribute anyway.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # RFIs and drawing revisions a claim cites.
        'atmta_construction_documents',
        # NCRs a claim cites.
        'atmta_construction_quality',
        # Change events and the change order a determination may produce.
        'atmta_construction_change',
        # Packages and contractors: whose contract the clock belongs to.
        'atmta_construction_contract',
        'atmta_construction_core',
        'atmta_project_core',
        'mail',
        'atmta_roles',
    ],
    # No views. The claims screens are loaded by `real_estate_construction`.
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_claims_rules.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
