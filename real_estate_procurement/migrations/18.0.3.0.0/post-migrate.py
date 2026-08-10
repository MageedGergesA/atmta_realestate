# -*- coding: utf-8 -*-
"""M3 migration — classify, configure conservatively, reserve nothing.

The temptation on an upgrade like this is to switch the control system on: walk
every approved requisition, create reservations, and let people discover on
Monday that half their projects are suddenly out of budget. That would be a
software update making an operational decision, and the decision is not the
software's to make.

So this script does three things:

1. **Classify** every existing requisition against M3's vocabulary, so a
   manager can see what the control system would say about the demand that
   already exists.
2. **Set existing companies to the permissive policies** — warn and optional —
   whatever the code's defaults for a *new* company are. An upgrade must not
   start refusing approvals and purchase-order confirmations that were legal
   the day before.
3. **Reserve nothing.** Historic confirmed purchase orders are already
   Construction commitment and must never receive a reservation on top: that is
   the double-count this milestone exists to prevent, and doing it during a
   migration would be doing it invisibly. Approved-but-unordered demand is
   listed for a manager to activate deliberately, through
   `Activate Procurement Reservations`.

Idempotent throughout. Running it twice creates no second classification, no
duplicate reservation and no second exception, and changes no financial total.
"""

import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1 — policy. Explicitly written rather than left to the field defaults,
    # because a default only applies to rows created after it exists and these
    # companies already exist. Written only where the column is still null, so
    # a re-run cannot undo a policy somebody has since chosen deliberately.
    cr.execute("""
        UPDATE res_company
           SET procurement_budget_policy = 'warn'
         WHERE procurement_budget_policy IS NULL
    """)
    budget_set = cr.rowcount
    cr.execute("""
        UPDATE res_company
           SET procurement_po_governance = 'optional'
         WHERE procurement_po_governance IS NULL
    """)
    governance_set = cr.rowcount
    cr.execute("""
        UPDATE realestate_project
           SET procurement_budget_policy = 'company'
         WHERE procurement_budget_policy IS NULL
    """)
    cr.execute("""
        UPDATE realestate_project
           SET procurement_po_governance = 'company'
         WHERE procurement_po_governance IS NULL
    """)
    if budget_set or governance_set:
        _logger.info(
            "M3: %s company/companies default to a Warn budget policy and %s "
            "to Optional purchase governance. Existing behaviour is "
            "unchanged; enabling control is a deliberate act.",
            budget_set, governance_set)

    # 2 — the approval steps predate the decision field. A step that was
    # approved says so in `approved`; everything else was still waiting.
    if _column_exists(cr, 'realestate_procurement_approval_step', 'approved'):
        cr.execute("""
            UPDATE realestate_procurement_approval_step
               SET decision = CASE WHEN approved THEN 'approved'
                                   ELSE 'pending' END
             WHERE decision IS NULL
        """)
        _logger.info("M3: carried %s approval step(s) onto the decision "
                     "field.", cr.rowcount)
        # The authority snapshot cannot be reconstructed for historic steps —
        # the rule may have been edited since. Copy what is still true and
        # leave the rest empty rather than inventing a basis that would then
        # be quoted as evidence.
        cr.execute("""
            UPDATE realestate_procurement_approval_step step
               SET rule_name = rule.name,
                   group_id = rule.group_id
              FROM realestate_procurement_approval_rule rule
             WHERE step.rule_id = rule.id
               AND step.rule_name IS NULL
        """)

    # 3 — classification. Read through the ORM so the rules live in one place.
    requests = env['realestate.material.request'].with_context(
        active_test=False).search([])
    if requests:
        requests._classify_procurement_governance()
        summary = {}
        for request in requests:
            key = request.governance_status or 'unclassified'
            summary[key] = summary.get(key, 0) + 1
        _logger.info(
            "M3: classified %s requisition(s): %s", len(requests),
            ', '.join('%s=%s' % item for item in sorted(summary.items())))
        candidates = requests.filtered(
            lambda req: req.governance_status == 'approved_unordered')
        if candidates:
            _logger.info(
                "M3: %s approved requisition(s) are candidates for "
                "reservation. None was created. Run Activate Procurement "
                "Reservations once the list has been reviewed.",
                len(candidates))
