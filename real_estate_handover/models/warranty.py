from odoo import _, api, fields, models


class Warranty(models.Model):
    _name = 'realestate.warranty'
    _description = 'Property Warranty'
    _inherit = ['mail.thread']
    _order = 'end_date desc, id desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    sale_contract_id = fields.Many2one('realestate.sale.contract', string='Sale Contract', required=True, ondelete='restrict')
    property_id = fields.Many2one(related='sale_contract_id.property_id', store=True, readonly=True)
    partner_id = fields.Many2one(related='sale_contract_id.partner_id', store=True, readonly=True, string='Buyer')

    period_months = fields.Integer(default=12, required=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    end_date = fields.Date(required=True, tracking=True)

    coverage_notes = fields.Html(string='What’s Covered')
    exclusion_notes = fields.Html(string='Exclusions')

    state = fields.Selection([
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ], compute='_compute_state', store=True)

    @api.depends('start_date', 'end_date')
    def _compute_state(self):
        today = fields.Date.today()
        for rec in self:
            if not rec.start_date or not rec.end_date:
                rec.state = 'active'
            elif today > rec.end_date:
                rec.state = 'expired'
            else:
                rec.state = 'active'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.warranty') or _('New')
        return super().create(vals_list)
