# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Procurement: Vendor Governance",
    'summary': "Authoritative owner of the Vendor Governance models.",
    'description': """
ATMTA Procurement — Vendor Governance
=====================================

Wave 1 of the ATMTA V2 programme: the first real capability extraction. This
module becomes the **authoritative technical owner** of the fifteen Vendor
Governance models listed in `05_MODEL_TO_MODULE_MAP.csv` — vendor profiles and
trades, the qualification template and its requirements, assessments and their
responses and conditions, restrictions, the eligibility service, and the AVL and
audit wizards.

**Nothing about the business changes.** Every `_name` is byte-for-byte what it
was, every SQL table keeps its name and its rows, every record keeps its id and
its create_date. What changes is which module Python defines the models in.

What this module deliberately does **not** carry
------------------------------------------------

Security stays behind, and the reason is a sequencing constraint rather than a
preference. All 38 ACL rows and 2 of the 11 record rules on these models gate on
five procurement groups — `group_procurement_user`, `_manager`,
`_qualification_assessor`, `_qualification_approver`, `_requester` — which are
shared by Sourcing, Evaluation, Award and Receipt and are declared in
`real_estate_procurement`. Moving them here would make Vendor Governance the
owner of the whole domain's role hierarchy; depending on
`real_estate_procurement` to reach them would invert the extraction. The
architecture's answer is `atmta_roles` / `atmta_procurement_core`, and neither
exists yet.

Views, actions, menus, sequences, crons and data likewise stay, which has the
happy consequence that **no view, action or menu XML-ID changes** and the
committed Wave 0 navigation keeps working untouched.

Transitional dependency
-----------------------

`real_estate_developer` is depended on for exactly one field —
`realestate.procurement.vendor.restriction.project_id`, a many2one to
`realestate.project`. When Wave 2 extracts `atmta_project_core` this dependency
re-points there and the developer dependency disappears.

It must never depend on Sourcing, Evaluation, Award or Receipt: Vendor
Governance is consumed by those capabilities, not the other way round.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.1.0.0',
    'category': 'Real Estate',
    'depends': [
        'base',
        'mail',
        'product',
        'purchase',
        'real_estate_developer',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
