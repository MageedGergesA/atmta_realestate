{
    'name': 'ATMTA Procurement — Evaluation',
    'version': '18.0.2.0.0',
    'category': 'Real Estate/Procurement',
    'summary': 'Evaluation plans, criteria, rounds, candidates, technical and '
               'commercial scoring, integrity audit.',
    'description': """
Procurement evaluation
======================

How received bids are compared, and the record of who compared them.

**Evaluation moves no money.** It produces scores, an evaluated cost, a rank
and a recommendation — analytical values, every one. The budget reservation is
untouched, Construction commitment stays where it was, and nothing here
authorises a purchase. Authorisation is ``atmta_procurement_award``.

Technical and commercial evaluation are separated on purpose: the commercial
outcome is restricted at field level, so a technical evaluator cannot read,
filter, sort or aggregate their way to the ranking.

Depends on ``atmta_procurement_sourcing`` and extends the sourcing event with
the evaluation half. The event itself knows nothing about this module.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': ['atmta_procurement_sourcing'],
    'data': [
        'security/ir.model.access.csv',
        'security/evaluation_rules.xml',
        'data/evaluation_sequences.xml',
        # Wave 11 — evaluation screens, the audit wizard and the evaluation report,
        # moved from the emptied shell. The report action is declared here now,
        # which removes this module's upward `env.ref` into the shell.
        'views/evaluation_views.xml',
        'wizard/evaluation_audit_wizard_views.xml',
        'report/evaluation_report.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
