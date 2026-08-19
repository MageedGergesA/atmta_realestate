# -*- coding: utf-8 -*-
"""M8 migration — describe what arrived before the controls existed.

The tempting shortcut this time is quieter than M7's and would do more damage.
Every historical receipt could be given an inspection, marked fully accepted and
dated to the day it was validated, so the register arrives complete and the M8
audit reports nothing.

That would state, on the record, that somebody inspected material nobody
inspected. An inspection is a person saying *I looked at this and it was fit to
use*. A script cannot say it, and a sheet signed by nobody is worse than a gap,
because the gap is honest and somebody will rely on the sheet.

Likewise the cost coding. M8 carries a cost code from the demand onto tender
RFQ lines, and it would be easy to walk the confirmed orders and code them
retrospectively. That would move real committed money onto codes nobody chose,
change the cost report for every project overnight, and give no clue afterwards
which figures were a buyer's decision and which were this script's guess.

So this migration creates **no inspection, no inspection line and no cost
code**, and writes nothing to a purchase order, a stock move or a bill. It
counts, it logs, and it leaves the work for the audit to name and a person to
fix.

Idempotent by construction: it only reads.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    # -- Receipts that were never inspected ----------------------------
    #
    # Expected to be all of them. The policy ships `off`, so nothing before
    # this upgrade was ever asked for an inspection, and reporting the number
    # is the honest way to say what switching the policy on will surface.
    receipts = env['stock.picking'].search_count([
        ('state', '=', 'done'),
        ('picking_type_id.code', '=', 'incoming'),
    ])

    # -- Tender lines carrying a project and no cost code --------------
    #
    # The two M8 defects, counted as they stand. Their commitment sits under
    # Unassigned on the cost report and no bill against them can become actual
    # cost until somebody codes them.
    uncoded = 0
    POLine = env['purchase.order.line']
    if 're_cost_code_id' in POLine._fields:
        uncoded = POLine.search_count([
            ('order_id.re_sourcing_event_id', '!=', False),
            ('order_id.re_project_id', '!=', False),
            ('order_id.state', 'in', ('purchase', 'done')),
            ('re_cost_code_id', '=', False),
            ('display_type', '=', False),
        ])

    # -- Purchase lines already billed beyond what was received --------
    #
    # The three-way match refuses these from now on. Any that exist were
    # posted before the gate did, and no migration can un-post a ledger entry.
    overbilled = 0
    for line in POLine.search([('order_id.re_project_id', '!=', False),
                               ('order_id.state', 'in', ('purchase', 'done'))]):
        if (line.qty_invoiced or 0.0) - (line.qty_received or 0.0) > 0.000001:
            overbilled += 1

    _logger.info(
        "M8 migration (descriptive only, nothing created): "
        "%s validated receipt(s) carry no inspection; "
        "%s confirmed tender line(s) carry a project and no cost code; "
        "%s purchase line(s) are already billed beyond what was received. "
        "Run the procurement integrity audit for the records themselves.",
        receipts, uncoded, overbilled)
