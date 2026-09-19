# -*- coding: utf-8 -*-
"""M19 — PDC coverage on the contract.

0.1's four statistics are kept verbatim (`check_count`, `check_amount_total`,
`check_amount_cleared`, `check_amount_bounced`) because the contract form
displays them and anything could be reading them.

What is added is the distinction 0.1 left dangerously implicit.
`check_amount_total` is the face value of paper received. It is not revenue, it
is not collection, and it is not cash. Every new field here is named so that it
cannot be mistaken for money, and the labels say so on the form.
"""

from odoo import _, api, fields, models


class SaleContract(models.Model):
    _inherit = 'realestate.sale.contract'

    check_ids = fields.One2many(
        'realestate.check', 'sale_contract_id', string='Checks',
    )
    check_count = fields.Integer(compute='_compute_check_stats')
    check_amount_total = fields.Monetary(
        string='Cheques Received (Face Value)', compute='_compute_check_stats',
        help="Total face value of cheques received against this contract. "
             "PAPER, not cash — see 'Cleared' for money the bank has actually "
             "honoured.")
    check_amount_cleared = fields.Monetary(
        string='Cheques Cleared', compute='_compute_check_stats',
        help="Face value of cheques the bank has honoured. This is the only "
             "figure here that corresponds to money.")
    check_amount_bounced = fields.Monetary(
        string='Cheques Bounced', compute='_compute_check_stats')

    # ---------- M19: coverage, explicitly labelled ----------
    check_amount_on_hand = fields.Monetary(
        string='Cheques On Hand', compute='_compute_check_stats',
        help="Held physically, not yet presented.")
    check_amount_at_bank = fields.Monetary(
        string='Cheques At Bank', compute='_compute_check_stats',
        help="Presented, outcome not yet known.")
    future_obligation_amount = fields.Monetary(
        string='Future Obligations', compute='_compute_check_coverage',
        help="Outstanding, uncancelled instalments.")
    pdc_secured_amount = fields.Monetary(
        string='Secured by PDCs', compute='_compute_check_coverage',
        help="Of those obligations, how much has a live cheque behind it. "
             "Securing an obligation is not collecting it.")
    pdc_unsecured_amount = fields.Monetary(
        string='Unsecured', compute='_compute_check_coverage')
    pdc_coverage_percent = fields.Float(
        string='PDC Coverage %', compute='_compute_check_coverage',
        help="Secured ÷ future obligations. An operational insight into how "
             "much of the remaining schedule is backed by paper — NOT a "
             "collection or revenue figure.")

    @api.depends('check_ids.amount', 'check_ids.state')
    def _compute_check_stats(self):
        # `sudo` on the read only. A salesperson with no Checks group opens
        # contracts all day; the treasury model existing must not turn that
        # into an AccessError. Only aggregate amounts are exposed here — no
        # cheque number, drawer account or treasury note.
        for rec in self.sudo():
            checks = rec.check_ids
            rec.check_count = len(checks)
            rec.check_amount_total = sum(checks.mapped('amount'))
            rec.check_amount_cleared = sum(
                c.amount for c in checks if c.state == 'cleared')
            rec.check_amount_bounced = sum(
                c.amount for c in checks if c.state == 'bounced')
            rec.check_amount_on_hand = sum(
                c.amount for c in checks if c.state in ('draft', 'registered'))
            rec.check_amount_at_bank = sum(
                c.amount for c in checks
                if c.state in ('deposited', 'in_clearing'))

    @api.depends('installment_ids.current_amount', 'installment_ids.paid_amount',
                 'installment_ids.is_cancelled',
                 'installment_ids.secured_by_checks_amount')
    def _compute_check_coverage(self):
        for rec in self.sudo():
            live = rec.installment_ids.filtered(lambda i: not i.is_cancelled)
            currency = rec.currency_id or rec.company_id.currency_id
            future = sum(
                max((i.current_amount or 0.0) - (i.paid_amount or 0.0), 0.0)
                for i in live)
            secured = sum(live.mapped('secured_by_checks_amount'))
            # Cannot secure more than is owed, even if a consolidated cheque
            # over-covers one line.
            secured = min(secured, future)
            rec.future_obligation_amount = currency.round(future)
            rec.pdc_secured_amount = currency.round(secured)
            rec.pdc_unsecured_amount = currency.round(max(future - secured, 0.0))
            rec.pdc_coverage_percent = (secured / future * 100.0) if future else 0.0

    def action_bulk_create_checks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bulk Create Checks'),
            'res_model': 'realestate.check.bulk.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_sale_contract_id': self.id},
        }

    def action_view_checks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Checks'),
            'res_model': 'realestate.check',
            'view_mode': 'list,form',
            'domain': ['|', ('sale_contract_id', '=', self.id),
                       ('sale_installment_id', 'in', self.installment_ids.ids)],
            'context': {'default_sale_contract_id': self.id,
                        'default_partner_id': self.partner_id.id},
        }

    def action_print_pdc_acknowledgement(self):
        """M24 — a cheque receipt, deliberately not called a payment receipt."""
        self.ensure_one()
        return self.env.ref(
            'real_estate_checks.action_report_pdc_acknowledgement'
        ).report_action(self)


class RentalContractChecks(models.Model):
    """M16 — parity for Rental. Same statistics, same honest labels."""
    _inherit = 'realestate.contract'

    check_ids = fields.One2many(
        'realestate.check', 'rental_contract_id', string='Cheques')
    check_count = fields.Integer(compute='_compute_check_stats')
    check_amount_total = fields.Monetary(
        string='Cheques Received (Face Value)', compute='_compute_check_stats')
    check_amount_cleared = fields.Monetary(
        string='Cheques Cleared', compute='_compute_check_stats')
    check_amount_bounced = fields.Monetary(
        string='Cheques Bounced', compute='_compute_check_stats')

    @api.depends('check_ids.amount', 'check_ids.state')
    def _compute_check_stats(self):
        # `sudo` on the read only — see the note on the sale contract above.
        for rec in self.sudo():
            checks = rec.check_ids
            rec.check_count = len(checks)
            rec.check_amount_total = sum(checks.mapped('amount'))
            rec.check_amount_cleared = sum(
                c.amount for c in checks if c.state == 'cleared')
            rec.check_amount_bounced = sum(
                c.amount for c in checks if c.state == 'bounced')

    def action_view_checks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cheques'),
            'res_model': 'realestate.check',
            'view_mode': 'list,form',
            'domain': [('rental_contract_id', '=', self.id)],
            'context': {'default_rental_contract_id': self.id,
                        'default_partner_id': self.partner_id.id},
        }
