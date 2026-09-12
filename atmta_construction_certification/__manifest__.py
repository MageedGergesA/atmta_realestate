# -*- coding: utf-8 -*-
{
    'name': 'ATMTA Construction — Certification',
    'version': '18.0.3.0.0',
    'category': 'Real Estate/Construction',
    'summary': 'Payment certificates, retention, advances, owner billing.',
    'description': """
ATMTA Construction Certification
================================

Where measured work becomes money owed.

* **Payment certificate** — how much of the measured work is payable this
  period, line by line against the bill of quantities.
* **Retention** and **retention release** — what is withheld, and what is
  given back. Held is what the register says, not a re-derivation: retention
  is held per project and released in stages, so a partial release is a
  document rather than a flag.
* **Advance** and **advance recovery** — paid up front, recovered across
  certificates.
* **Owner progress billing** and its **deductions** — the other side of the
  same period.

This module contains the one genuinely irreducible pair in the construction
domain. A certificate withholds retention; a retention release reads the
certificates it came from. Every other cluster in this suite decomposed; these
two do not, and they are declared together rather than pretending otherwise.

It depends on ``atmta_construction_site``, because a certificate line consumes
a BOQ line. That is also why the relation Wave 18 had to leave in the monolith
comes here: the module that owns the certificate line is the one that should
say which BOQ line it consumes.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': [
        # A certificate line consumes a BOQ line and pays a milestone.
        'atmta_construction_site',
        # Packages and contractors: whose certificate it is.
        'atmta_construction_contract',
        # The four company accounts retention and advances post to.
        'atmta_construction_core',
        'atmta_project_core',
        'account',
        'purchase',
        'mail',
        'atmta_roles',
    ],
    'data': [
        'data/sequences.xml',
        'security/ir.model.access.csv',
        'security/construction_certification_rules.xml',
        # Wave 21 — this module's own screens, moved down from
        # `real_estate_construction`. They render only models it owns and
        # no field declared above it, which is what made the move safe.
        'views/certification_views.xml',
        'views/payment_certificate_views.xml',
        'views/owner_progress_billing_views.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
}
