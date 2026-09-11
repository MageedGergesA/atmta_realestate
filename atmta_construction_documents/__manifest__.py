# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Documents',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'Document control: drawings, submittals, transmittals, RFIs.',
    'description': """
ATMTA Construction Documents
============================

The written record of a project, and the audit trail of who has seen it.

* **Documents and revisions** — the controlled register: drawing or
  specification, its discipline, its confidentiality, and every revision with
  the status it was issued at.
* **Submittals** — what a contractor sends for approval, grouped into
  packages, with the review cycle and the revisions a rejection produces.
* **Transmittals** — what was formally issued to whom, and when.
* **RFIs** — the questions asked against the contract, their official answers,
  and the cost and schedule impact somebody estimated.

Document control sits below the commercial capabilities because a document is
filed *against* a contract, not derived from one. It reads the package, the
contractor, the WBS and the cost code, all of which are below it already.

One link points the other way and therefore is not declared here: a submittal
or an RFI may raise a **change event**, and change events are a
``real_estate_construction`` model. The field and the action that creates it
are added onto these models from up there. Both were always deliberate
actions somebody takes, and they still are.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # A submittal or an RFI may raise a change event. One way only:
        # change reads nothing here.
        'atmta_construction_change',
        # Packages and contractors: what a document is filed against.
        'atmta_construction_contract',
        # WBS and cost codes: what it is coded to.
        'atmta_construction_core',
        # `realestate.project`.
        'atmta_project_core',
        # Attachments carry the files; chatter carries the correspondence.
        'mail',
        # Canonical roles. The legacy construction groups are declared above
        # this module and bridged there.
        'atmta_roles',
    ],
    # No views. The document screens show `change_event_id` and the action
    # that creates it, both declared by `real_estate_construction`, so they are
    # loaded by that module -- the lowest one that has both halves. Splitting a
    # nine-view file into xpath fragments to move it would have been tidier on
    # paper and riskier in fact.
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_documents_rules.xml',
        # Wave 23 — the document screens, which could come down once the
        # change-event link did.
        'views/information_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
