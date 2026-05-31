from odoo import api, fields, models


class CostLine(models.Model):
    _name = 'realestate.construction.cost.line'
    _description = 'Construction Cost Line'
    _order = 'date desc, id desc'

    name = fields.Char(string='Description', required=True)
    project_id = fields.Many2one('realestate.project', string='Project', required=True, ondelete='cascade')
    phase_id = fields.Many2one('realestate.phase', string='Phase', ondelete='set null')
    milestone_id = fields.Many2one('realestate.construction.milestone', string='Milestone', ondelete='set null')
    contractor_id = fields.Many2one('realestate.contractor', string='Contractor')

    date = fields.Date(default=fields.Date.context_today, required=True)
    amount = fields.Monetary(string='Amount', required=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    category = fields.Selection([
        ('material', 'Material'),
        ('labor', 'Labor'),
        ('equipment', 'Equipment'),
        ('subcontract', 'Subcontract'),
        ('permit', 'Permit / Fee'),
        ('other', 'Other'),
    ], default='material')

    vendor_bill_id = fields.Many2one('account.move', string='Vendor Bill',
                                      domain="[('move_type', '=', 'in_invoice')]")
    notes = fields.Text()

    @api.onchange('milestone_id')
    def _onchange_milestone_id(self):
        if self.milestone_id:
            if not self.project_id:
                self.project_id = self.milestone_id.project_id
            if not self.phase_id and self.milestone_id.phase_id:
                self.phase_id = self.milestone_id.phase_id
            if not self.contractor_id and self.milestone_id.contractor_id:
                self.contractor_id = self.milestone_id.contractor_id
