# -*- coding: utf-8 -*-
"""M4 migration — describe the supplier population, qualify nobody.

The one dishonest thing this script could do is decide that a vendor with
eight years of purchase orders must be qualified. Prior commercial activity is
evidence that somebody once decided to buy from them; it is not a governance
decision, it has no assessor, no date, no evidence and no expiry, and writing
it into the qualification register would create exactly the fiction the
register exists to prevent. So:

1. **Existing companies are set to OPTIONAL**, whatever the code's default for
   a *new* company is. Nothing that was legal on Friday is refused on Monday.
   Turning control on is a policy act with a worklist behind it.
2. **No qualification, profile, AVL entry or restriction is created.**
   Profiles are made one at a time when somebody starts governing a vendor;
   4,000 empty governance records saying nothing is not a migration outcome.
3. **The supplier population is classified**, so a manager can see the size of
   the job before deciding to start it.

Idempotent. Every branch is decided from current data, so a second run
overwrites each classification with the same value and creates nothing.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    _permissive_vendor_policy(env)
    counts = _classify(env)
    _report(env, counts)


def _permissive_vendor_policy(env):
    """Confirm the upgrade refuses nothing, and say so rather than assume it.

    The shipped default for a new company is already OPTIONAL, and Odoo fills
    a new required column with the field default on every existing row — so
    unlike M3, there is nothing here that needs forcing. What there is worth
    doing is checking: a company that came out of the upgrade on a strict
    policy would start refusing purchase orders on Monday, and the difference
    between "we chose the permissive default" and "we assumed it" is one
    query.

    Anything already configured is left alone. Re-running this must not undo
    a policy somebody deliberately switched on after the first run.
    """
    companies = env['res.company'].search([])
    blank = companies.filtered(lambda c: not c.procurement_vendor_policy)
    if blank:
        blank.write({'procurement_vendor_policy': 'optional'})
    strict = companies.filtered(
        lambda c: c.procurement_vendor_policy in (
            'required_for_sourcing', 'required_for_award'))
    _logger.info(
        "M4: %s company(ies) on the permissive default, %s configured to "
        "require qualification, %s had no value and were set to optional.",
        len(companies) - len(strict) - len(blank), len(strict), len(blank))
    if strict:
        _logger.warning(
            "M4: %s already require vendor qualification. Existing suppliers "
            "with no assessment will be refused at %s. This was not set by "
            "the upgrade.",
            ', '.join(strict.mapped('name')),
            'invitation or confirmation')

    projects = env['realestate.project'].search([])
    blank_projects = projects.filtered(
        lambda p: not p.procurement_vendor_policy)
    if blank_projects:
        blank_projects.write({'procurement_vendor_policy': 'company'})


def _classify(env):
    """M4AB — what this database already knows about its suppliers."""
    partners = env['res.partner'].search([('supplier_rank', '>', 0)])
    if not partners:
        _logger.info("M4: no supplier partners to classify.")
        return {}
    counts = env['res.partner']._classify_procurement_vendors(partners)
    for label, count in sorted(counts.items()):
        _logger.info("M4 vendor classification — %s: %s", label, count)
    return counts


def _report(env, counts):
    """State plainly what was and was not done.

    A migration log that only lists what it changed leaves the reader to
    assume the rest happened silently. The most important line here is the
    one about the records that were deliberately not created.
    """
    total = sum(counts.values())
    _logger.info(
        "M4 complete: %s supplier(s) classified. "
        "0 qualifications created, 0 governance profiles created, "
        "0 approved-vendor-list entries created, 0 restrictions created. "
        "No purchase order, vendor bill or requisition was altered. "
        "Vendor qualification is OPTIONAL everywhere — the rollout worklist "
        "is Procurement → Vendor Governance → Vendors Not Assessed.", total)
