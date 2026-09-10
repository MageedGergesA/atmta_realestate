{
    'name': 'ATMTA Procurement — Receipt',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Receipt inspection and the three-way match on vendor bills.',
    'description': """
Procurement receipt
===================

What happened when the goods arrived, and whether the invoice agrees.

**Native Odoo owns the receipt.** `stock.picking`, its moves and its validation
belong to Inventory; `purchase.order` belongs to Purchase; `account.move` and
everything posted belongs to Accounting. Nothing here duplicates any of them,
and no second receipt, order or ledger model is introduced.

What this module owns is the procurement judgement laid over those facts: was
an inspection required, what did the inspector accept and reject, and does the
purchase order, the receipt and the vendor bill tell the same story. The
three-way match is **evidence and control** — it never posts, reverses or
re-values anything.

Receiving goods creates no commitment. Commitment is created once, when the
purchase order is confirmed, and stays derived from confirmed orders.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'atmta_procurement_evaluation',
        'atmta_procurement_control',
        'atmta_procurement_core',
        'atmta_project_core',
        'purchase',
        'stock',
        'account',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/receipt_rules.xml',
        'data/receipt_sequences.xml',
        # Wave 11 — receipt inspection screens moved from the emptied shell.
        'views/receipt_inspection_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
