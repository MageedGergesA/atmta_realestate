# -*- coding: utf-8 -*-
"""M6 migration — classify what is evaluable, evaluate nothing.

The tempting shortcut here is worse than M5's. A closed tender with three
received bids looks exactly like something that wants an evaluation, and it
would be easy to create a plan, score every bid on its price and mark the
cheapest rank 1 so the module arrives fully populated.

That would fabricate the one thing an evaluation file exists to prove: that
declared criteria were frozen before the offers were seen, that named
evaluators scored them, that a committee consolidated the result and that a
technical decision preceded the commercial one. None of it happened. A
migration cannot manufacture judgement, and an evaluation nobody performed is
worse than no evaluation, because somebody will quote it in a dispute.

So this script creates **no plan, no round, no criterion, no assignment, no
sheet, no score, no analysis, no ranking**. It labels closed tenders so a buyer
can see which ones are waiting for an evaluation somebody actually has to do.

Idempotent: every label is decided from current data, so a second run writes
the same value and creates nothing.
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
    """Label every sourcing event by what an evaluation would need next."""
    events = env['realestate.procurement.sourcing.event'].search([])
    if not events:
        _logger.info("M6: no sourcing events to classify.")
        return {}
    counts = {}
    for event in events:
        label = event._classify_evaluation_readiness()
        if event.evaluation_readiness != label:
            event.evaluation_readiness = label
        counts[label] = counts.get(label, 0) + 1
    for label, count in sorted(counts.items()):
        _logger.info("M6 evaluation classification — %s: %s", label, count)
    return counts


def _report(env, counts):
    total = sum(counts.values())
    _logger.info(
        "M6 complete: %s sourcing event(s) classified. "
        "0 evaluation plans created, 0 criteria created, 0 evaluation rounds "
        "created, 0 committee assignments created, 0 technical sheets "
        "created, 0 scores recorded, 0 commercial analyses created, "
        "0 rankings produced. No bid, tender, purchase order, requisition or "
        "reservation was altered. A closed tender is labelled as awaiting "
        "evaluation; it is not given one, because an evaluation nobody "
        "performed is not evidence.", total)
