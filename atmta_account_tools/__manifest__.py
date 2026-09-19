# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Accounting Helpers',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Foundation',
    'summary': 'Shared posting and payment helpers for the ATMTA suite.',
    'description': """
ATMTA Accounting Helpers
========================

Owns `realestate.account.tools`: post draft moves, and register and reconcile a
payment through Odoo's own wizard. Two methods, one behaviour, for every module
that moves money — rental, developer, brokerage and construction.

It lived in `atmta_real_estate`, which is why Developer, Brokerage and
Construction all depended on the Rental app to invoice anything. `atmta_base`
could not host it either: it drives `account.payment.register`, and the
foundation must not pull the accounting stack under every ATMTA module. Hence
this module — deliberately the smallest thing that can depend on `account`.

Upgrading an existing database hands the model's identifier over to this module
before its own definition loads (`pre_init_hook`), so nothing is recreated and
no reference breaks.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': ['base', 'account'],
    'pre_init_hook': 'pre_init_hook',
    'installable': True,
    'auto_install': False,
}
