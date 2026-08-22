# -*- coding: utf-8 -*-
{
    'name': "ATMTA — Development & Sales",
    'summary': "Development & Sales application navigation. Owns no business models.",
    'description': """
ATMTA Development & Sales Application
=====================================

Navigation only. Zero models, fields, ACL rows, record rules and new actions —
every leaf reuses an existing action.

Absorbs the legacy **Project Plans** root: `03_TARGET_APP_ARCHITECTURE.md`
classifies Visual as contextual, not an application, so the 3D maquette, 2D
master plan and gallery appear here as a Sales Gallery section and the five
authoring/config menus move under Configuration.

`realestate.project` is **not** extracted and its owner is unchanged. This shell
only presents it.

Deliberately not reproduced: **Price History** and **Bulk Price Update**
(contextual — buttons on the Price Book), **Reservation Report** and
**Receivables Schedule** under Reports (redundant with the Reservations and
Receivables destinations), and `atmta_real_estate.action_payment_plan` — the
second, leasing-side action on the two-owner `realestate.payment.plan` model.
Surfacing both would present one table as two concepts; see
`17_RISKS_AND_OPEN_DECISIONS.md §1`.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.0.1.0',
    'category': 'Real Estate',
    'depends': [
        'real_estate_developer',
        'real_estate_maquette',
        'real_estate_contract_template',
        'atmta_v2_pilot',
    ],
    'data': ['views/menus.xml'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
