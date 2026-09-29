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

# Wave 17 — claims moved to `atmta_construction_claims`, which this module
# depends on. The constant is imported across the module boundary rather than
# restated, so the two cannot drift apart.
from odoo.addons.atmta_construction_claims.models.claim import OPEN_CLAIM_STATES


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

        Read as the system, because the two fields this feeds do not agree on
        how to read: `approved_eot_days` is stored (so Odoo computes it with
        sudo) and `claimed_eot_days` is not, and the registry says as much at
        start-up. The unsudoed half then raised AccessError on 'Extension of
        Time' for every site and cost reader, which is how a Control Tower
        Risk panel and Health card turned into an error banner for the people
        the tower is mostly for. Only two day counts leave this method; the
        claim's money and its negotiating position stay behind their own
        group, as `_claims` and the field groups already arrange.
        """
        self.ensure_one()
        package = self.sudo()
        implemented = package.eot_ids.filtered(
            lambda e: e.state == 'implemented')
        approved = sum(implemented.mapped('determined_days'))
        pending = package.eot_ids.filtered(
            lambda e: e.state not in ('implemented', 'rejected',
                                      'withdrawn', 'superseded'))
        claimed = sum(pending.mapped('claimed_days'))
        claims_without_eot = package.claim_ids.filtered(
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
        """What has already been certified or invoiced under this package.

        Scoped to the package. Summing by project and contractor refused an
        omission on a contractor's second package with what was certified
        under the first — a certificate draws down the package it names, the
        same rule the certificate's own cumulative ceiling applies.

        Certificates raised without a package (project-level, or from before
        packages existed) cannot be attributed to one package. They are still
        counted against every package of that contractor on the project,
        deliberately: an omission that might take a contract below value
        certified under it is refused, not assumed safe.
        """
        self.ensure_one()
        certificates = self.env[
            'realestate.construction.payment.certificate'].sudo().search([
                ('contractor_id', '=', self.contractor_id.id),
                ('state', 'in', ('certified', 'invoiced', 'paid')),
                '|', ('package_id', '=', self.id),
                '&', ('project_id', '=', self.project_id.id),
                ('package_id', '=', False),
            ])
        return sum(certificates.mapped('gross_amount'))
