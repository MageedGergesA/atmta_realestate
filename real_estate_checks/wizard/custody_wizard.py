# -*- coding: utf-8 -*-
"""M4 — the only way custody changes.

`check.custodian_id` and `check.location_id` are `readonly=True` on the model
and stay that way. Moving paper is an act with a date, a giver and a receiver,
and this wizard is what records it. Batch-capable because Treasury moves
cheques by the drawer-full, not one at a time.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.check_states import CHECK_AT_BANK


class CustodyWizard(models.TransientModel):
    _name = 'realestate.check.custody.wizard'
    _description = 'Transfer Cheque Custody'

    check_ids = fields.Many2many(
        'realestate.check', string='Cheques', required=True)
    company_id = fields.Many2one(
        'res.company', compute='_compute_company', store=True)
    to_custodian_id = fields.Many2one('res.users', string='To Custodian')
    to_location_id = fields.Many2one(
        'realestate.check.location', string='To Location',
        domain="[('company_id', '=', company_id)]")
    reason = fields.Selection(
        [('transfer', 'Internal Transfer'),
         ('legal', 'Handed to Legal'),
         ('archive', 'Archived')],
        required=True, default='transfer',
        help="Movements to and from the bank are recorded by the deposit and "
             "the bounce, and returning a cheque to a customer is its own "
             "action — so they are not offered here.")
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    note = fields.Char()

    check_count = fields.Integer(compute='_compute_company')
    total_amount = fields.Monetary(compute='_compute_company')
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_company')

    @api.depends('check_ids')
    def _compute_company(self):
        for wiz in self:
            companies = wiz.check_ids.mapped('company_id')
            wiz.company_id = companies[:1]
            wiz.check_count = len(wiz.check_ids)
            wiz.currency_id = wiz.check_ids.mapped('currency_id')[:1]
            wiz.total_amount = sum(wiz.check_ids.mapped('amount'))

    def action_transfer(self):
        self.ensure_one()
        self.env['realestate.check']._assert_group(
            'real_estate_checks.group_checks_treasurer',
            _('transfer cheque custody'))
        if not (self.to_custodian_id or self.to_location_id):
            raise UserError(_(
                "Name a custodian, a location, or both — otherwise nothing "
                "has moved."))
        companies = self.check_ids.mapped('company_id')
        if len(companies) > 1:
            raise UserError(_(
                "These cheques belong to %s different companies. Move them "
                "one company at a time.") % len(companies))
        if self.to_location_id and self.to_location_id.company_id != companies:
            raise UserError(_(
                "Location %(location)s belongs to %(other)s, not %(own)s.",
                location=self.to_location_id.display_name,
                other=self.to_location_id.company_id.display_name,
                own=companies.display_name))

        at_bank = self.check_ids.filtered(lambda c: c.state in CHECK_AT_BANK)
        if at_bank:
            raise UserError(_(
                "%(count)s cheque(s) are at the bank and are not in your "
                "custody to move: %(refs)s",
                count=len(at_bank),
                refs=', '.join(at_bank.mapped('name')[:10])))

        self.env['realestate.check.custody'].transfer(
            self.check_ids, to_custodian=self.to_custodian_id or None,
            to_location=self.to_location_id or None, reason=self.reason,
            note=self.note, date=self.date)
        return {'type': 'ir.actions.act_window_close'}
