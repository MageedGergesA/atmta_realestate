# -*- coding: utf-8 -*-
"""Wave 13 — the half of a contractor that only construction can know.

`realestate.contractor` moved to `atmta_construction_contract`, which sits
below every construction capability so that documents, quality and site
operations can point at it. What could not move is everything derived from
models that live up here: milestones, payment certificates and the retention
register.

The floor declares the fields and computes them through seams that answer
neutrally. This module supplies the relations those seams need and overrides
them with the real arithmetic. Nothing about the arithmetic changed; only the
module that states it.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ContractorConstruction(models.Model):
    _inherit = 'realestate.contractor'

    milestone_ids = fields.One2many(
        'realestate.construction.milestone', 'contractor_id',
        string='Milestones')
    milestone_count = fields.Integer(compute='_compute_milestone_count')

    payment_certificate_ids = fields.One2many(
        'realestate.construction.payment.certificate', 'contractor_id',
        string='Payment Certificates',
    )
    total_certified = fields.Monetary(
        string='Total Certified', compute='_compute_retention_totals',
        store=True,
        help='Sum of gross amounts across all certified+ certificates.',
    )
    total_retention_held = fields.Monetary(
        string='Retention Held', compute='_compute_retention_totals',
        store=True,
        help='Cumulative retention not yet released.',
    )

    def _compute_milestone_count(self):
        for rec in self:
            rec.milestone_count = len(rec.milestone_ids)

    @api.depends('payment_certificate_ids.state',
                 'payment_certificate_ids.gross_amount',
                 'payment_certificate_ids.retention_amount')
    def _compute_retention_totals(self):
        """Held is what the register says, not a re-derivation.

        The old computation summed retention off the certificates and zeroed
        it on a boolean, so a partial release could not be represented at all
        and a released amount kept showing as held until somebody flipped the
        flag. M7 reads the movements.
        """
        Retention = self.env['realestate.construction.retention']
        for rec in self:
            paid_or_billed = rec.payment_certificate_ids.filtered(
                lambda c: c.state in ('certified', 'invoiced', 'paid')
            )
            rec.total_certified = sum(paid_or_billed.mapped('gross_amount'))
            groups = Retention._read_group(
                [('contractor_id', '=', rec.id), ('side', '=', 'contractor')],
                aggregates=['signed_amount:sum'])
            registered = (groups[0][0] if groups else 0.0) or 0.0
            legacy = sum(paid_or_billed.filtered(
                lambda c: not c.retention_posted_correctly).mapped(
                    'retention_amount'))
            rec.total_retention_held = registered + (
                0.0 if rec.retention_released else legacy)

    def action_create_subcontract_po(self):
        """Open the subcontract PO wizard pre-filled with this contractor."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Subcontract PO'),
            'res_model': 'realestate.subcontract.po.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contractor_id': self.id},
        }

    def action_release_retention(self):
        """Open a retention release for this contractor.

        Releasing used to post a bill for everything held everywhere and flip
        a boolean. Retention is held per project and released in stages, so
        the button now opens the document that says which stage, on which
        project, for how much — and that document checks the register.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Retention Release'),
            'res_model': 'realestate.construction.retention.release',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contractor_id': self.id,
                        'default_side': 'contractor'},
        }

    def _action_release_retention_legacy(self):
        """The pre-M7 blanket release. Kept only so the migration can
        describe what it replaced; nothing calls it."""
        for rec in self:
            if rec.retention_released:
                raise UserError(_("Retention has already been released for '%s'.") % rec.name)
            if rec.total_retention_held <= 0:
                raise UserError(_("Nothing to release — held retention is zero."))
            bill = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'partner_id': rec.partner_id.id,
                'invoice_date': fields.Date.context_today(rec),
                'ref': _('Retention release — %s') % rec.name,
                'invoice_line_ids': [(0, 0, {
                    'name': _('Retention release for %s (across %d certificates)') % (
                        rec.name,
                        len(rec.payment_certificate_ids.filtered(
                            lambda c: c.state in ('certified', 'invoiced', 'paid')
                        )),
                    ),
                    'quantity': 1,
                    'price_unit': rec.total_retention_held,
                })],
            })
            rec.retention_released = True
            # Post so the retention payable lands on the ledger.
            self.env['realestate.account.tools'].post_moves(bill)
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'view_mode': 'form',
                'res_id': bill.id,
            }
