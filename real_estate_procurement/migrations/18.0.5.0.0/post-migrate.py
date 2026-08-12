# -*- coding: utf-8 -*-
"""M5 migration — classify the purchase history, manufacture no tender.

The tempting thing this script could do is look at an existing alternative-RFQ
group, decide it was obviously a tender, and create a Sourcing Event with three
invitations and three bid responses so the module looks fully adopted on day
one. Everything about that would be false. There was no issue date, no
published version, no deadline, no eligibility decision at invitation and no
recorded moment of receipt; the amounts on those RFQ lines are today's working
numbers, not what anybody submitted. A tender file assembled out of guesses is
worse than an empty one, because somebody will quote it.

So:

1. **Nothing is created.** No sourcing event, no version, no invitation, no bid
   response, no acknowledgement.
2. **Nothing existing is altered.** No purchase order, RFQ, requisition,
   reservation, qualification or vendor bill is touched. The only column
   written anywhere is the new `re_sourcing_class` description.
3. **The population is described**, so a buyer can see what is there before
   deciding what — if anything — to adopt into a formal event by hand.

Idempotent: every branch is decided from current data, so a second run writes
the same label and creates nothing.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    _default_policies(env)
    counts = _classify(env)
    _report(env, counts)


def _default_policies(env):
    """Existing companies get the permissive end of every new switch.

    Same rule as M3 and M4: nothing that was legal on Friday is refused on
    Monday because an upgrade ran. Late-bid handling defaults to
    `exception_required` for *new* companies, but an upgrade must not start
    holding submissions for a manager who has never been told they are now in
    the loop — so existing companies are moved to `allow_with_warning`, which
    records the lateness and refuses nothing.
    """
    companies = env['res.company'].search([])
    blank_late = companies.filtered(lambda c: not c.procurement_late_bid_policy)
    if blank_late:
        blank_late.write({'procurement_late_bid_policy': 'allow_with_warning'})
    blank_ack = companies.filtered(
        lambda c: not c.procurement_addendum_ack_policy)
    if blank_ack:
        blank_ack.write({'procurement_addendum_ack_policy': 'optional'})
    _logger.info(
        "M5: %s company(ies) set to allow-and-flag late submissions, %s set "
        "to optional addendum acknowledgement. Neither refuses anything; both "
        "are recorded.", len(blank_late), len(blank_ack))


def _classify(env):
    """Describe every purchase order, adopt none of them."""
    orders = env['purchase.order'].search([])
    if not orders:
        _logger.info("M5: no purchase orders to classify.")
        return {}
    counts = env['purchase.order']._classify_sourcing_history(orders)
    for label, count in sorted(counts.items()):
        _logger.info("M5 sourcing classification — %s: %s", label, count)
    return counts


def _report(env, counts):
    total = sum(counts.values())
    groups = 0
    if 'purchase_group_id' in env['purchase.order']._fields:
        groups = env['purchase.order.group'].search_count([])
    agreements = 0
    if 'purchase.requisition' in env:
        agreements = env['purchase.requisition'].search_count([])
    _logger.info(
        "M5 complete: %s purchase order(s) classified, %s native alternative "
        "group(s) and %s native purchase agreement(s) detected. "
        "0 sourcing events created, 0 tender versions created, "
        "0 invitations created, 0 bid responses created, "
        "0 acknowledgements created. No purchase order, RFQ, requisition, "
        "reservation or qualification was altered. Native alternative groups "
        "and Purchase Agreements remain native and are not ATMTA tenders; "
        "adopting one is a deliberate act, not an upgrade side effect.",
        total, groups, agreements)
