# -*- coding: utf-8 -*-
"""M7 migration — classify what is awaiting an award, award nothing.

The shortcut here is more tempting than M6's and considerably worse. A finalised
evaluation with a clear rank 1 looks exactly like something that wants an award,
and it would be easy to create one, approve it in the migration's own name and
confirm the winner's order so the module arrives fully populated.

That would forge the one signature this entire milestone exists to require. An
award is a decision two named people took on a stated date; a script cannot take
it, and an authorisation nobody granted is worse than no authorisation, because
somebody will rely on it.

So this script creates **no award, no award line, no allocation, no purchase
order and no commitment**. It confirms nothing. It writes one descriptive label
per sourcing event.

One label is worth naming: `committed_without_award`. A tender whose purchase
orders were confirmed with no award behind them is recorded as exactly that. It
is not given a retrospective award to make the register look tidy — the whole
point of finding it is that somebody has to explain it.

Idempotent: every label is decided from current data, so a second run writes the
same value and creates nothing.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    counts = _classify(env)
    _report(env, counts)


def _classify(env):
    """Label every sourcing event by where it stands on awarding."""
    events = env['realestate.procurement.sourcing.event'].search([])
    if not events:
        _logger.info("M7: no sourcing events to classify.")
        return {}
    counts = {}
    for event in events:
        label = event._classify_award_readiness()
        if event.award_readiness != label:
            event.award_readiness = label
        counts[label] = counts.get(label, 0) + 1
    for label, count in sorted(counts.items()):
        _logger.info("M7 award classification — %s: %s", label, count)
    return counts


def _report(env, counts):
    total = sum(counts.values())
    _logger.info(
        "M7 complete: %s sourcing event(s) classified. "
        "0 awards created, 0 award lines created, 0 allocations created, "
        "0 purchase orders confirmed, 0 reservations converted, "
        "0 commitments created. No bid, tender, evaluation, purchase order, "
        "requisition or reservation was altered. A finalised evaluation is "
        "labelled as awaiting a decision; it is not given one, because an "
        "award nobody authorised is not an authorisation.", total)

    stranded = counts.get('committed_without_award', 0)
    if stranded:
        _logger.warning(
            "M7: %s tender(s) have confirmed purchase orders with no award "
            "behind them. They are labelled `committed_without_award` and "
            "left that way on purpose — a commitment exists that nobody "
            "authorised, and the integrity audit reports it as critical under "
            "`confirmed_tender_order_without_award`. Creating a backdated "
            "award to clear the label would forge the approval.", stranded)
