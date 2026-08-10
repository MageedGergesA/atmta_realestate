# -*- coding: utf-8 -*-
"""M2 migration — classify what exists. Change nothing that means anything.

Three jobs, and deliberately no fourth:

1. carry the deprecated single `po_line_id` link into the one-to-many
   relation, so a legacy request's purchase history keeps showing up now that
   the new relation is what the views and computes read;
2. classify every existing requisition, so the gaps M2 introduced a vocabulary
   for are visible rather than assumed;
3. leave every purchase document exactly as it is.

What this script does **not** do: manufacture procurement plans for historical
requests, assign a default cost code to uncoded lines, revert a confirmed order
to an enquiry, or remove a commitment Construction has already recorded. A
migration that invented any of those would be rewriting commercial history to
make the new model look tidy.
"""

import logging

_logger = logging.getLogger(__name__)


def _requests_missing(cr, column):
    """Requests with at least one line lacking `column`, determined in SQL.

    Construction owns the coding fields and loads *after* Procurement, so at
    this point in the upgrade they are not in the registry — and on the very
    first upgrade the columns do not exist in the database either, because
    Construction has not created them yet. Both cases mean the same thing:
    nothing is coded. Asking the ORM instead would answer "no lines are
    missing a cost code", which is true only in the sense that the concept
    does not exist yet, and would file an entirely uncoded database as clean.
    """
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'realestate_material_request_line'
           AND column_name = %s
    """, (column,))
    if not cr.fetchone():
        # The column is about to be added, so every existing line is missing
        # it. Every request with lines qualifies.
        cr.execute("SELECT DISTINCT request_id "
                   "FROM realestate_material_request_line")
        return {row[0] for row in cr.fetchall()}
    cr.execute("""
        SELECT DISTINCT request_id
          FROM realestate_material_request_line
         WHERE %s IS NULL
    """ % column)
    return {row[0] for row in cr.fetchall()}


def migrate(cr, version):
    if not version:
        return

    # 1 — the legacy link. `po_line_id` was the only outcome field the old
    # one-click flow wrote. `po_line_ids` is now authoritative and is derived
    # from the purchase line's own back-reference, so any legacy line whose
    # purchase line lost that reference would silently disappear from the
    # request. Restore it where it is unambiguous.
    cr.execute("""
        UPDATE purchase_order_line pol
           SET re_material_request_line_id = mrl.id
          FROM realestate_material_request_line mrl
         WHERE mrl.po_line_id = pol.id
           AND pol.re_material_request_line_id IS NULL
    """)
    relinked = cr.rowcount
    if relinked:
        _logger.info("M2: re-linked %s legacy purchase line(s) to their "
                     "requisition line.", relinked)

    # 2 — classification. Read through the ORM: the rules for what counts as
    # coded live in Python and must not be restated in SQL, where they would
    # drift the first time either changes.
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    requests = env['realestate.material.request'].with_context(
        active_test=False).search([])
    if not requests:
        return
    requests._classify_procurement_legacy(
        uncoded_ids=_requests_missing(cr, 'cost_code_id'),
        unassigned_wbs_ids=_requests_missing(cr, 'wbs_id'))

    summary = {}
    for request in requests:
        summary[request.legacy_status] = summary.get(
            request.legacy_status, 0) + 1
    _logger.info("M2: classified %s requisition(s): %s", len(requests),
                 ', '.join('%s=%s' % item for item in sorted(summary.items())))
