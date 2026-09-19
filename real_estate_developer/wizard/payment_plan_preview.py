# -*- coding: utf-8 -*-
"""Phase 12 — schedule preview.

Nobody should activate a ten-year plan without seeing what it actually charges.
The preview generates the real schedule through the real code path — not a
simplified illustration — so what is previewed is what a deal will get.
"""

from odoo import _, api, fields, models


class PaymentPlanPreview(models.TransientModel):
    _name = 'realestate.payment.plan.preview'
    _description = 'Payment Plan Schedule Preview'

    plan_id = fields.Many2one(
        'realestate.payment.plan', required=True, readonly=True)
    currency_id = fields.Many2one(
        related='plan_id.currency_id', readonly=True)
    unit_price = fields.Monetary(
        string='Unit Price', required=True, default=1000000.0)
    booking_amount = fields.Monetary(string='Booking Amount')
    booking_date = fields.Date(
        required=True, default=fields.Date.context_today)
    handover_date = fields.Date(
        help="Required only if the plan has a payment due on handover.")

    line_ids = fields.One2many(
        'realestate.payment.plan.preview.line', 'preview_id', readonly=True)
    total_amount = fields.Monetary(compute='_compute_totals')
    total_percent = fields.Float(compute='_compute_totals')
    payment_count = fields.Integer(compute='_compute_totals')
    last_due_date = fields.Date(compute='_compute_totals')
    balances = fields.Boolean(
        compute='_compute_totals', string='Adds Up',
        help="True when the schedule sums to the unit price exactly, at the "
             "currency's precision.")

    @api.depends('line_ids.amount', 'unit_price')
    def _compute_totals(self):
        for wiz in self:
            lines = wiz.line_ids
            wiz.total_amount = sum(lines.mapped('amount'))
            wiz.payment_count = len(lines)
            wiz.total_percent = (
                wiz.total_amount / wiz.unit_price * 100.0
                if wiz.unit_price else 0.0)
            wiz.last_due_date = max(
                lines.mapped('date_due'), default=False)
            wiz.balances = bool(lines) and wiz.currency_id.compare_amounts(
                wiz.total_amount, wiz.unit_price) == 0

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()
        rows = self.plan_id._generate_schedule(
            total_price=self.unit_price,
            booking_date=self.booking_date,
            handover_date=self.handover_date,
            booking_amount=self.booking_amount,
        )
        # Prove it adds up through the same guard the deal path uses, so a
        # misconfigured plan fails here rather than on a customer's contract.
        self.plan_id._validate_schedule_total(rows, self.unit_price)
        self.env['realestate.payment.plan.preview.line'].create([{
            'preview_id': self.id,
            'sequence': row['sequence'],
            'kind': row['kind'],
            'name': row['name'],
            'percent': row['percent'],
            'amount': row['amount'],
            'date_due': row['date_due'],
            'cumulative_amount': row['cumulative_amount'],
            'cumulative_percent': row['cumulative_percent'],
            'remaining_amount': row['remaining_amount'],
        } for row in rows])
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }


class PaymentPlanPreviewLine(models.TransientModel):
    _name = 'realestate.payment.plan.preview.line'
    _description = 'Payment Plan Preview Line'
    _order = 'sequence, id'

    preview_id = fields.Many2one(
        'realestate.payment.plan.preview', required=True, ondelete='cascade')
    currency_id = fields.Many2one(
        related='preview_id.currency_id', readonly=True)
    sequence = fields.Integer()
    kind = fields.Char()
    name = fields.Char()
    percent = fields.Float(string='%')
    amount = fields.Monetary()
    date_due = fields.Date(string='Due')
    cumulative_amount = fields.Monetary(string='Cumulative')
    cumulative_percent = fields.Float(string='Cumulative %')
    remaining_amount = fields.Monetary(string='Remaining')
