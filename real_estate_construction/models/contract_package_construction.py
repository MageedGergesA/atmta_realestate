# -*- coding: utf-8 -*-
"""Wave 13 — the half of a contract package that only construction can know.

`realestate.construction.contract.package` moved to
`atmta_construction_contract`, below every construction capability, so that
documents, quality and site operations can point at it. What could not move is
everything derived from models that live up here: commitment changes,
extensions of time, claims, the commitment precedence rule and payment
certificates.

The floor declares those fields and computes them through seams that answer
neutrally. This module supplies the relations and overrides the seams with the
real arithmetic, and re-states the `@api.depends` so the stored figures still
recompute when their sources move. The arithmetic is unchanged; only the module
that states it.
"""
from odoo import api, fields, models

from .claim import OPEN_CLAIM_STATES


class ContractPackageConstruction(models.Model):
    _inherit = 'realestate.construction.contract.package'

    commitment_change_ids = fields.One2many(
        'realestate.construction.commitment.change', 'package_id',
        string='Approved Variations', readonly=True)
    eot_ids = fields.One2many(
        'realestate.construction.eot', 'package_id', readonly=True)
    claim_ids = fields.One2many(
        'realestate.construction.claim', 'package_id', readonly=True)

    # ----- Variations -----
    def _variation_amount(self):
        self.ensure_one()
        live = self.commitment_change_ids.filtered(
            lambda c: c.change_order_id.state in ('implemented', 'closed'))
        return sum(live.mapped('amount'))

    @api.depends('commitment_change_ids.amount',
                 'commitment_change_ids.change_order_id.state')
    def _compute_variations(self):
        return super()._compute_variations()

    # ----- Extensions of time -----
    def _eot_day_totals(self):
        """Approved days move the contract date. Claimed days are reported.

        Claimed days are counted from the extensions that exist, plus the
        claims that ask for time and have not yet produced one — otherwise a
        claim for sixty days would be invisible until somebody remembered to
        raise an EOT record for it.
        """
        self.ensure_one()
        implemented = self.eot_ids.filtered(
            lambda e: e.state == 'implemented')
        approved = sum(implemented.mapped('determined_days'))
        pending = self.eot_ids.filtered(
            lambda e: e.state not in ('implemented', 'rejected',
                                      'withdrawn', 'superseded'))
        claimed = sum(pending.mapped('claimed_days'))
        claims_without_eot = self.claim_ids.filtered(
            lambda c: c.claimed_days and c.state in OPEN_CLAIM_STATES
            and not c.eot_ids)
        claimed += sum(claims_without_eot.mapped('claimed_days'))
        return approved, claimed

    @api.depends('eot_ids.state', 'eot_ids.determined_days',
                 'eot_ids.claimed_days', 'claim_ids.state',
                 'claim_ids.claimed_days')
    def _compute_eot_days(self):
        return super()._compute_eot_days()

    # ----- Commitment -----
    def _package_commitment(self):
        """The precedence rule, stated once, in `commitment.py`.

        A package that has purchase orders is represented by its purchase
        orders and contributes nothing of its own. That is the rule that keeps
        an 8M package with an 8M order from reading as 16M, and it belongs to
        the model that owns commitment.
        """
        self.ensure_one()
        return self.env['realestate.construction.commitment']._package_commitment(self)

    # ----- Consumed value -----
    def _consumed_value(self):
        """What has already been certified or invoiced under this package."""
        self.ensure_one()
        certificates = self.env[
            'realestate.construction.payment.certificate'].sudo().search([
                ('project_id', '=', self.project_id.id),
                ('contractor_id', '=', self.contractor_id.id),
                ('state', 'in', ('certified', 'invoiced', 'paid')),
            ])
        return sum(certificates.mapped('gross_amount'))
