{
    'name': 'ATMTA Procurement — Sourcing',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Sourcing events, versions, invitations, clarifications, '
               'bid responses.',
    'description': """
Procurement sourcing
====================

Competition. Turning authorised demand into an enquiry, putting it to named
vendors, and keeping an honest record of what each of them answered.

**Sourcing moves no money.** Publishing a tender, inviting a vendor, raising a
request for quotation and receiving a bid change no financial position: the
budget reservation stays exactly where Control put it, Construction's
commitment stays at zero until an order is confirmed, and a bid price is an
offer, never an authority.

Requests for quotation are native Odoo purchase orders. This module does not
introduce a second RFQ model; it records the enterprise sourcing identity
alongside them, and keeps an immutable bid snapshot because the operational
purchase order line can be — and is — mutated by native comparison.

Depends on ``atmta_procurement_request`` and extends the requisition with the
solicitation half: which trade a line belongs to, which vendors may be
enquired with, and how demand becomes an RFQ. The requisition itself knows
nothing about this module.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'atmta_procurement_request',
        'atmta_procurement_vendor',
        'atmta_procurement_core',
        'purchase_requisition',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/sourcing_rules.xml',
        'data/sourcing_sequences.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
