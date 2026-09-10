# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Procurement — Purchase',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'The purchase-order gate: what a confirmed order must satisfy.',
    'description': """
Procurement purchase
====================

Native ``purchase.order`` is the source of financial truth. This module is the
gate in front of it: the checks a real-estate order must pass before it may
confirm, and the consequences that follow once it does.

It owns no model of its own. It extends ``purchase.order`` and
``purchase.order.line`` with the real-estate coding (project, source, sourcing
class), and with the authorisations that reach across capabilities:

* tender authorisation, from Sourcing
* award authorisation, from Award
* budget and project-purchase governance, from Control
* vendor eligibility as of a date, from Vendor
* coding completeness against approved demand, from Request

Confirming an order converts Control's reservation into commitment, never both
at once. Cancelling releases it.

This is deliberately a separate capability rather than a member of any one of
them. Every check belongs to a different capability, so placing the gate inside
one would invert the dependency of the other four. It therefore sits above them
all, and is the last procurement module to load.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # The five capabilities the gate reaches into. `award` carries
        # evaluation, sourcing, request, control and core transitively; it is
        # named here because the gate calls the award authorisation directly.
        'atmta_procurement_award',
        'atmta_procurement_vendor',
        # Not referenced by name, but the gate decides what receiving will see
        # (`_prepare_stock_moves`, the project stock location). Listed so this
        # module loads after receipt, making it the last procurement module in.
        'atmta_procurement_receipt',
        'purchase',
        'stock',
    ],
    'data': [
        'views/purchase_order_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
