# -*- coding: utf-8 -*-
{
    'name': "Real Estate — Procurement",
    'summary': "Shared procurement infrastructure for the real-estate suite.",
    'description': """
Real Estate Procurement
=======================
Cross-module supply-chain foundation used by Construction, Handover, Rental and Aftermarket:

* Procurement Plan (demand, not money) with revisions that keep what was planned
* Requisition V2 — line-level project/WBS/cost-code coding, controlled edits, revision history
* Sourcing that prepares draft RFQs for vendors a buyer names, and commits nothing
* Budget control — approved demand reserves purchasing capacity; confirming an order converts
  the reservation into Construction commitment, never both at once
* Configurable budget policy (none / warn / approval required / block) and project purchase
  governance (optional / controlled / required), per company with a project override
* Approval matrix with snapshotted authority, maker/checker, rejection with reasons
* Approval and reservation registers, and an exception register for every override
* Vendor governance — qualification per company, trade and project, with templates,
  evidence, conditions, expiry, reassessment and dated suspensions
* One eligibility service answering "may this vendor take part, and why not" as of a
  given date, consumed by the sourcing pool and by purchase-order confirmation
* Vendor tags (contractor, material supplier, service vendor, marketing, consultant, utility, landowner)
* Real-estate product flags (construction material, property fitting, marketing asset, maintenance consumable)
* Filtered procurement menus (POs, vendors, catalog) scoped to real-estate activity
* Starter catalog (~150 products across civil, structural, MEP, finishing, fittings, marketing, services)
""",
    'author': "Atmta",
    'license': 'LGPL-3',
    'version': '18.0.14.0.0',
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
    ],
    'data': [
        'security/security.xml',
        'security/canonical_role_bridge.xml',
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/cron.xml',
        'data/partner_categories.xml',
        'data/product_categories.xml',
        'data/qualification_areas.xml',
        'data/products_seed.xml',
        'views/approval_views.xml',
        'views/procurement_control_views.xml',
        # Wizard actions first: the governance views put them on buttons, and
        # an action referenced before it exists is a load-order error, not a
        # runtime one.
        'wizard/vendor_governance_wizard_views.xml',
        'views/vendor_governance_views.xml',
        'views/sourcing_views.xml',
        'views/evaluation_views.xml',
        # The evaluation wizard action, and the report action, both before
        # `views/menus.xml` — the menu references the first and the round form
        # binds the second.
        'wizard/evaluation_audit_wizard_views.xml',
        'report/evaluation_report.xml',
        'views/procurement_plan_views.xml',
        'wizard/procurement_wizard_views.xml',
        'views/material_request_views.xml',
        'views/purchase_order_views.xml',
        'views/product_views.xml',
        'views/partner_views.xml',
        'views/menus.xml',
        # After menus.xml: the award menu hangs off `menu_evaluation`, and a
        # parent referenced before it exists is a load-order error.
        'views/award_views.xml',
        'report/award_report.xml',
        # M8 — receipt inspection. After menus.xml for the same reason
        # the award views are: it hangs off `menu_procurement_operations`.
        'views/receipt_inspection_views.xml',
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
    'application': True,
}
