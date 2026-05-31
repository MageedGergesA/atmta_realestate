from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class UnitReservation(models.Model):
    """Hold-and-book workflow for developer inventory."""
    _name = 'realestate.unit.reservation'
    _description = 'Unit Reservation / Booking'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    property_id = fields.Many2one(
        'realestate.property', string='Unit', required=True, ondelete='restrict', tracking=True,
    )
    project_id = fields.Many2one(related='property_id.project_id', store=True, readonly=True)
    phase_id = fields.Many2one(related='property_id.phase_id', store=True, readonly=True)

    partner_id = fields.Many2one('res.partner', string='Buyer', required=True, tracking=True)
    agent_id = fields.Many2one('res.users', string='Sales Agent', default=lambda self: self.env.user, tracking=True)

    state = fields.Selection([
        ('hold', 'On Hold'),
        ('booked', 'Booked'),
        ('confirmed', 'Confirmed (Sale)'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ], default='hold', tracking=True, required=True)

    hold_started_at = fields.Datetime(string='Hold Started', default=fields.Datetime.now, tracking=True)
    hold_expiry_at = fields.Datetime(
        string='Hold Expires At',
        compute='_compute_hold_expiry', store=True, readonly=False,
    )
    hold_duration_hours = fields.Float(string='Hold Duration (hours)', default=72.0)

    booking_fee = fields.Monetary(string='Booking Fee', tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    proposed_price = fields.Monetary(string='Proposed Sale Price', tracking=True)
    property_base_price = fields.Monetary(
        string='Property Base Price', related='property_id.base_price', readonly=True,
        help='Developer / valuation reference price on the property record.',
    )
    proposed_vs_base = fields.Float(
        string='vs Base (%)', compute='_compute_proposed_vs_base', readonly=True,
        help='How the proposed sale price deviates from the property base price.',
    )

    @api.depends('proposed_price', 'property_base_price')
    def _compute_proposed_vs_base(self):
        for rec in self:
            if rec.property_base_price:
                rec.proposed_vs_base = (rec.proposed_price - rec.property_base_price) / rec.property_base_price
            else:
                rec.proposed_vs_base = 0.0

    payment_term_id = fields.Many2one('account.payment.term', string='Payment Plan / Terms', tracking=True)

    sale_contract_id = fields.Many2one('realestate.sale.contract', string='Sale Contract', readonly=True, copy=False)
    notes = fields.Html()

    @api.depends('hold_started_at', 'hold_duration_hours')
    def _compute_hold_expiry(self):
        for rec in self:
            if rec.hold_started_at and rec.hold_duration_hours:
                rec.hold_expiry_at = rec.hold_started_at + timedelta(hours=rec.hold_duration_hours)
            else:
                rec.hold_expiry_at = False

    @api.model_create_multi
    def create(self, vals_list):
        # Block reservations on sold / maintenance / inactive properties
        prop_ids = [v.get('property_id') for v in vals_list if v.get('property_id')]
        if prop_ids:
            self.env['realestate.property'].browse(prop_ids)._check_available_for_new_sale()
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.unit.reservation') or _('New')
        records = super().create(vals_list)
        # Mark the property as reserved
        for rec in records:
            if rec.property_id and rec.state in ('hold', 'booked') and rec.property_id.state == 'available':
                rec.property_id.state = 'reserved'
        return records

    def action_confirm_booking(self):
        for rec in self:
            if rec.state != 'hold':
                raise UserError(_("Only on-hold reservations can be confirmed to a booking."))
            if not rec.booking_fee:
                raise UserError(_("Set a booking fee before confirming."))
            rec.state = 'booked'

    def action_create_sale_contract(self):
        """Promote a booking into a full sale contract."""
        for rec in self:
            if rec.state != 'booked':
                raise UserError(_("Reservation must be in Booked state before creating a sale contract."))
            if rec.sale_contract_id:
                return rec._action_open_sale_contract()
            if not rec.proposed_price:
                raise UserError(_("Set a proposed sale price first."))
            contract = self.env['realestate.sale.contract'].create({
                'partner_id': rec.partner_id.id,
                'property_id': rec.property_id.id,
                'sale_price': rec.proposed_price,
                'payment_term_id': rec.payment_term_id.id,
                'reservation_id': rec.id,
                'agent_id': rec.agent_id.id,
            })
            rec.sale_contract_id = contract.id
            rec.state = 'confirmed'
            return rec._action_open_sale_contract()

    def _action_open_sale_contract(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sale Contract'),
            'res_model': 'realestate.sale.contract',
            'view_mode': 'form',
            'res_id': self.sale_contract_id.id,
        }

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
            # Free the property if no other live reservation
            if rec.property_id.state == 'reserved':
                others = rec.property_id.reservation_ids.filtered(
                    lambda r: r.id != rec.id and r.state in ('hold', 'booked')
                )
                if not others:
                    rec.property_id.state = 'available'

    def action_extend_hold(self):
        for rec in self:
            if rec.state != 'hold':
                raise UserError(_("Only on-hold reservations can be extended."))
            rec.hold_duration_hours += 24.0

    @api.model
    def cron_expire_holds(self):
        now = fields.Datetime.now()
        to_expire = self.search([('state', '=', 'hold'), ('hold_expiry_at', '<', now)])
        for rec in to_expire:
            rec.state = 'expired'
            others = rec.property_id.reservation_ids.filtered(
                lambda r: r.id != rec.id and r.state in ('hold', 'booked')
            )
            if not others and rec.property_id.state == 'reserved':
                rec.property_id.state = 'available'
