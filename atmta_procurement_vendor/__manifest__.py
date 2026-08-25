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

This module needs `realestate.project` and nothing else from outside its own
domain: `realestate.procurement.vendor.restriction.project_id`,
`realestate.procurement.vendor.qualification.project_id` and its condition
many2many, plus the wizard field that feeds them. Wave 1 had to reach that model
through `real_estate_developer`, which meant Vendor Governance dragged in the
entire Development/Sales application to store a project reference.

Wave 2 gave `realestate.project` its own module, so the dependency now points at
`atmta_project_core` and the Development dependency is gone. Verified rather
than assumed: the module carries no data files, references no Development
group and no Development XML ID.

It must never depend on Sourcing, Evaluation, Award or Receipt: Vendor
Governance is consumed by those capabilities, not the other way round.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.2.0.0',
    'category': 'Real Estate',
    'depends': [
        'base',
        'mail',
        'product',
        'purchase',
        'atmta_project_core',
        'atmta_roles',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/vendor_rules.xml',
    ],
    'installable': True,
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'auto_install': False,
}
