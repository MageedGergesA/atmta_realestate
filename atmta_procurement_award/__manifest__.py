{
    'name': 'ATMTA Procurement — Award',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Award, award lines and allocations; issuing the purchase order.',
    'description': """
Procurement award
=================

The formal decision that a named vendor has won, and the authority that flows
from it.

Award is **evidence of authorisation**, not a source of financial truth. It
grows no commitment field of its own: Construction's commitment stays derived
from confirmed purchase orders, the reservation stays Control's, and the ledger
stays Accounting's. Award records who decided, on what basis, and when.

Approving an award moves no money. Issuing it does — because issuing
orchestrates the confirmation of the selected native purchase order, and that
confirmation is what creates the obligation. The purchase order remains the
source of truth; the award is why it was allowed to confirm.

Depends on ``atmta_procurement_evaluation``. Extends the sourcing event with
the award half, and the procurement integrity audit with the award checks.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        'atmta_procurement_evaluation',
        'atmta_procurement_control',
        'purchase',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/award_rules.xml',
        'data/award_sequences.xml',
        # Wave 11 — award screens and the award report, moved from the emptied
        # shell. Declaring the report here removes the upward `env.ref`.
        'views/award_views.xml',
        'report/award_report.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
