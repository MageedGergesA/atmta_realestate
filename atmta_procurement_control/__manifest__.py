{
    'name': 'ATMTA Procurement — Pre-commitment Control',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Budget reservations, conversions, approval rules and steps, '
               'control exceptions.',
    'description': """
Procurement pre-commitment control
==================================

The half of procurement that decides whether approved demand may consume a
project's purchasing capacity, and who has to say so.

A **reservation** is a soft commitment: it consumes budget headroom before any
purchase order exists, and it reverses on expiry, release or conversion. It is
not a Construction commitment and it is not an accounting entry — those appear
only when an order is confirmed, and exactly once.

This module depends on ``atmta_procurement_request`` and extends the
requisition with the reverse links and the derived figures. The requisition
itself knows nothing about this module, which is what keeps the dependency
acyclic. Wave 6 / AD-008.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'atmta_procurement_request',
        'atmta_procurement_core',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/control_rules.xml',
        'data/control_sequences.xml',
        'data/control_cron.xml',
        # Wave 11 — approval and control screens moved from the emptied shell.
        'views/approval_views.xml',
        'views/procurement_control_views.xml',
        # Wave 11 — the requisition screens. They belong to demand, but they
        # render nine columns this module declares on the request
        # (reservations, approval steps, control status), so Control is the
        # lowest module that can validate them.
        'views/material_request_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
