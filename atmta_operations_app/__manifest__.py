# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Property Operations",
    'summary': "Property Operations application navigation. Owns no business models.",
    'description': """
ATMTA Property Operations Application
=====================================

Navigation, plus the Property Operations dashboard (an abstract provider on
``atmta_dashboard``; no stored model, field, ACL row or record rule).

Composes four legacy addons into the one application
`03_TARGET_APP_ARCHITECTURE.md` calls for, without merging their code:
leasing and property from `atmta_real_estate`, handover/snagging/warranty from
`real_estate_handover`, tickets from `real_estate_customer_service`, and the
embed-token admin surface from `real_estate_api`. Handover stops being a root
application and Customer Service stops being buried at depth two.

One new action, `action_v2_my_maintenance`, gives the workspace a personal
queue; every other leaf reuses an existing action.

Deliberately not reproduced: **Contract Units** (contextual — lines of a
contract), **Scheduled Payments** (duplicate destination for
`realestate.contract.payment`, already reached as Billing Obligations),
**Handover Dashboard** (Overview covers it), and
`atmta_real_estate.action_payment_plan` — see the Development app's note and
`17_RISKS_AND_OPEN_DECISIONS.md §1`.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.0.2.0',
    'category': 'Real Estate',
    'depends': [
        'atmta_real_estate',
        'real_estate_handover',
        'real_estate_customer_service',
        'real_estate_api',
        'atmta_dashboard',
    ],
    'uninstall_hook': 'uninstall_hook',
    'demo': [
        'demo/demo.xml',
    ],
    'data': ['data/workspace_actions.xml', 'views/menus.xml', 'views/dashboard_views.xml', 'data/retire_legacy_navigation.xml'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
