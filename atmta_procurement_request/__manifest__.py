{
    'name': 'ATMTA Procurement — Demand',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Procurement plans and material requisitions with their '
               'revision trail.',
    'description': """
Procurement demand
==================

What was asked for, by whom, against which project, and how it changed.

This module owns demand and nothing else. It does not know what a budget
reservation or an approval step is — those are control decisions and they
belong to ``atmta_procurement_control``, which depends on this module and
extends the requisition with the control-facing half.

That direction is the point. Demand can be captured, edited, revised and read
without any control machinery installed; control cannot exist without demand
to control. Wave 6 / AD-008.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'atmta_procurement_core',
        'atmta_roles',
        'atmta_project_core',
        'atmta_property_core',
        'atmta_procurement_vendor',
        'purchase',
        'analytic',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/request_rules.xml',
        'data/request_sequences.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
