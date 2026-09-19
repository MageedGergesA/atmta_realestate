# -*- coding: utf-8 -*-
"""M35 — post-migration: give existing rows the new structures they need.

Three things, all of them additive:

1. **Allocations.** Every legacy cheque naming one obligation gets exactly one
   allocation for its full face value. Only where the link is unambiguous —
   nothing is inferred, and no amount is duplicated.
2. **Opening custody.** Every live cheque gets one custody row so the history
   starts somewhere honest rather than at a blank field. The reason is
   `opening`, dated from the cheque's own creation: a placeholder that says it
   is a placeholder, not an invented handover.
3. **Configuration.** The bounce penalty product setting is pointed at the
   variant of the template 0.1 shipped, which fixes the `product.template` id
   being written into a `product.product` field.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _migrate_allocations(env):
    """One allocation per legacy cheque, only where it is deterministic."""
    Check = env['realestate.check']
    Allocation = env['realestate.check.allocation']

    candidates = Check.with_context(active_test=False).search([
        '|', ('sale_installment_id', '!=', False),
             ('rental_payment_id', '!=', False),
    ])
    already = set(Allocation.search([
        ('check_id', 'in', candidates.ids)]).mapped('check_id').ids)

    vals_list, ambiguous = [], []
    for check in candidates:
        if check.id in already:
            continue
        if check.sale_installment_id and check.rental_payment_id:
            # Both set is not a shape 0.1 could produce through its own UI, so
            # it means something hand-edited it. Report; do not guess.
            ambiguous.append(check.name)
            continue
        vals_list.append({
            'check_id': check.id,
            'sale_installment_id': check.sale_installment_id.id or False,
            'rental_payment_id': check.rental_payment_id.id or False,
            'allocated_amount': check.amount,
            # A cancelled or replaced cheque secures nothing, so its historical
            # link is recorded as a cancelled allocation rather than a live one.
            'state': ('cancelled'
                      if check.state in ('cancelled', 'returned', 'replaced')
                      else 'active'),
            'note': 'Migrated from 0.1 single-obligation link.',
        })

    if vals_list:
        Allocation.with_context(skip_allocation_mirror=True).create(vals_list)
        _logger.info("real_estate_checks 0.2: created %s allocation(s) from "
                     "legacy cheque links.", len(vals_list))
    if ambiguous:
        _logger.error(
            "real_estate_checks 0.2: %s cheque(s) name BOTH a sale instalment "
            "and a rental payment, which is not a shape 0.1 could produce. No "
            "allocation was created for them — allocate them by hand:\n%s",
            len(ambiguous), '\n'.join('  - %s' % n for n in ambiguous[:50]))


def _seed_custody(env):
    """One opening movement per live cheque."""
    Check = env['realestate.check']
    Custody = env['realestate.check.custody']

    live = Check.with_context(active_test=False).search([
        ('state', 'not in', ('cancelled', 'returned', 'replaced'))])
    already = set(Custody.search([
        ('check_id', 'in', live.ids)]).mapped('check_id').ids)

    vals_list = []
    for check in live:
        if check.id in already:
            continue
        location = check.company_id.check_default_location_id
        vals_list.append({
            'check_id': check.id,
            'to_custodian_id': check.create_uid.id,
            'to_location_id': location.id or False,
            'reason': 'opening',
            'date': check.create_date,
            'handed_over_by_id': check.create_uid.id,
            'note': 'Opening balance recorded during the 0.2 upgrade. '
                    'No handover is implied — custody history begins here.',
        })
    if vals_list:
        Custody.create(vals_list)
        _logger.info("real_estate_checks 0.2: seeded %s opening custody "
                     "record(s).", len(vals_list))


def _seed_presentations(env):
    """A presentation attempt for every cheque that reached a bank.

    Attempt 1 for each, reconstructed from the cheque's own deposit link and
    state. This is the only place where history is inferred, and it infers
    nothing that is not already recorded on the cheque itself.
    """
    Check = env['realestate.check']
    Presentation = env['realestate.check.presentation']

    presented = Check.with_context(active_test=False).search([
        ('deposit_id', '!=', False)])
    already = set(Presentation.search([
        ('check_id', 'in', presented.ids)]).mapped('check_id').ids)

    state_map = {
        'deposited': 'presented',
        'in_clearing': 'clearing',
        'cleared': 'cleared',
        'bounced': 'bounced',
    }
    vals_list = []
    for check in presented:
        if check.id in already:
            continue
        vals_list.append({
            'check_id': check.id,
            'attempt': 1,
            'deposit_id': check.deposit_id.id,
            'journal_id': (check.journal_id or check.deposit_id.journal_id).id or False,
            'presented_date': (check.presented_date or check.deposit_date
                               or check.deposit_id.deposit_date),
            'amount': check.amount,
            'state': state_map.get(check.state, 'cancelled'),
            'cleared_date': check.cleared_date,
            'bounced_date': check.bounce_date,
            'payment_id': check.payment_id.id or False,
            'note': 'Reconstructed during the 0.2 upgrade from the cheque\'s '
                    'own deposit link.',
        })
    if vals_list:
        Presentation.create(vals_list)
        _logger.info("real_estate_checks 0.2: reconstructed %s presentation "
                     "attempt(s).", len(vals_list))


def _configure_products(env):
    """Point the company settings at the right *variant*.

    0.1 declared `product_check_bounce_penalty` as a `product.template` and
    then wrote its id straight into `account.move.line.product_id`, which is a
    `product.product`. The template record is kept (changing the model behind a
    live xml id would orphan production references); the configuration now
    holds its variant, which is what an invoice line actually needs.
    """
    penalty = env.ref('real_estate_checks.product_check_bounce_penalty',
                      raise_if_not_found=False)
    charge = env.ref('real_estate_checks.product_check_bank_charge',
                     raise_if_not_found=False)
    for company in env['res.company'].search([]):
        vals = {}
        if penalty and not company.check_bounce_penalty_product_id:
            vals['check_bounce_penalty_product_id'] = penalty.product_variant_id.id
        if charge and not company.check_bank_charge_product_id:
            vals['check_bank_charge_product_id'] = charge.product_variant_id.id
        if vals:
            company.write(vals)


def _seed_locations(env):
    """A default safe and a bank location per company, if none exist."""
    Location = env['realestate.check.location']
    for company in env['res.company'].search([]):
        if Location.search_count([('company_id', '=', company.id)]):
            continue
        safe = Location.create({
            'name': 'Treasury Safe',
            'code': 'SAFE',
            'kind': 'safe',
            'company_id': company.id,
        })
        Location.create({
            'name': 'At Bank',
            'code': 'BANK',
            'kind': 'bank',
            'company_id': company.id,
        })
        if not company.check_default_location_id:
            company.check_default_location_id = safe.id


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info("real_estate_checks: post-migration to 0.2 starting.")
    _seed_locations(env)
    _configure_products(env)
    _migrate_allocations(env)
    _seed_custody(env)
    _seed_presentations(env)
    _logger.info("real_estate_checks: post-migration to 0.2 complete.")
