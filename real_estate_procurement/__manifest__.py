# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Procurement",
    'summary': "Emptied compatibility shell for the procurement chain.",
    'description': """
Real Estate Procurement (compatibility shell)
=============================================

This module owns nothing. Waves 1 to 11 moved every model, screen, report,
wizard, sequence, cron and seed record it once defined into the capability
that owns them:

* ``atmta_procurement_core`` — policy, product classification, the catalog
* ``atmta_procurement_vendor`` — vendor governance, its crons and sequences
* ``atmta_procurement_request`` — demand: requests, plans, revisions
* ``atmta_procurement_control`` — reservations, approvals, exceptions
* ``atmta_procurement_sourcing`` — sourcing events, invitations, bids
* ``atmta_procurement_evaluation`` — evaluation rounds and the audit
* ``atmta_procurement_award`` — award and its report
* ``atmta_procurement_receipt`` — receipt inspection and three-way match
* ``atmta_procurement_purchase`` — the purchase-order gate

Two things stay, and both are deliberate:

* the ten **legacy procurement groups**, and the bridge that gives each of them
  its canonical twin in ``atmta_roles``. They cannot move: ``atmta_roles``
  already defines five of these ten XML IDs for the canonical roles, so the
  file would collide, and the bridge addresses the legacy groups by bare ID,
  which would silently rebind to the canonical groups in any other module.
  Databases that granted these groups keep working, and the bridge keeps
  translating them.
* the **cross-capability tests**, which exercise the whole chain end to end.
  This module depends on every capability, so it is the one place that can.

It is kept installed rather than uninstalled: uninstalling would cascade-delete
the records its XML IDs still point at. AD-007 — uninstall is not rollback.
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.16.0.0',
    'category': 'Real Estate',
    'depends': [
        'atmta_real_estate',
        # `realestate.project` is defined by real_estate_developer. Every
        # model here points at it, and M2 recorded the missing declaration as
        # a finding; M3 adds fields to that model, so the dependency is now
        # declared rather than relied upon.
        'real_estate_developer',
        'purchase',
        # M5 — native alternative RFQs live here: `purchase.order.group`,
        # `alternative_po_ids` and the compare action. Installing it also
        # brings Odoo's Purchase Agreements (blanket orders and purchase
        # templates). ATMTA does not govern those and makes no claim about
        # them; see IMPLEMENTATION_REPORT.md §"purchase_requisition".
        'purchase_requisition',
        'product',
        'stock',
        # Wave 1 — Vendor Governance was extracted into its own capability
        # module. This dependency is what guarantees the new module loads
        # first and in the same transaction, so the model definitions are
        # never absent and never ambiguously owned. It also keeps the
        # extraction invisible to every consumer: the models are reached
        # through the ORM registry exactly as before.
        'atmta_procurement_vendor',
        # Wave 5 — the procurement domain floor. Listed so it loads before this
        # module, which is what lets the field metadata change owner cleanly.
        'atmta_procurement_core',
        'atmta_procurement_request',
        'atmta_procurement_control',
        'atmta_procurement_sourcing',
        'atmta_procurement_evaluation',
        'atmta_procurement_award',
        'atmta_procurement_receipt',
        # Wave 11 — the purchase-order gate. Last procurement module to
        # load; named here so this shell still pulls the whole chain.
        'atmta_procurement_purchase',
    ],
    'data': [
        # Wave 11 — all that remains. The legacy procurement groups and the
        # bridge that maps them onto the canonical ATMTA roles. They stay here
        # because `atmta_roles` already defines five of these ten XML IDs for
        # the canonical roles, so moving the file would collide; and because
        # the bridge's records address the legacy groups by bare ID, which
        # would silently rebind to the canonical groups in any other module.
        'security/security.xml',
        'security/canonical_role_bridge.xml',
    ],
    # M5 — the sourcing tour. Declared explicitly because a tour that is not
    # in a bundle is not discovered and not run: it would sit in the tree
    # looking like browser coverage while proving nothing. `web.assets_tests`
    # is where `@web_tour` lives, and it is the only bundle M5 needs — the
    # module ships no backend JS of its own and depends on no other ATMTA
    # module publishing one for it.
    'assets': {
        'web.assets_tests': [
            '/real_estate_procurement/static/tests/tours/**/*.js',
        ],
    },
    'application': False,
}
