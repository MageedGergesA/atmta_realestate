from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Handover(models.Model):
    _name = 'realestate.handover'
    _description = 'Property Handover'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_date desc, id desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    sale_contract_id = fields.Many2one('realestate.sale.contract', string='Sale Contract', required=True, ondelete='cascade', tracking=True)
    property_id = fields.Many2one(related='sale_contract_id.property_id', store=True, readonly=True)
    partner_id = fields.Many2one(related='sale_contract_id.partner_id', store=True, readonly=True, string='Buyer')
    project_id = fields.Many2one(related='property_id.project_id', store=True, readonly=True)
    phase_id = fields.Many2one(related='property_id.phase_id', store=True, readonly=True)

    template_id = fields.Many2one('realestate.handover.checklist.template', string='Checklist Template')
    checklist_item_ids = fields.One2many('realestate.handover.checklist.item', 'handover_id', string='Checklist')
    checklist_done_count = fields.Integer(compute='_compute_progress', store=True)
    checklist_total_count = fields.Integer(compute='_compute_progress', store=True)
    checklist_progress = fields.Float(string='Checklist Complete (%)', compute='_compute_progress', store=True)

    snagging_issue_ids = fields.One2many('realestate.snagging.issue', 'handover_id', string='Snagging Issues')
    open_snagging_count = fields.Integer(compute='_compute_snagging_counts', store=True)
    total_snagging_count = fields.Integer(compute='_compute_snagging_counts', store=True)

    scheduled_date = fields.Datetime(string='Scheduled', tracking=True)
    actual_date = fields.Datetime(string='Completed On', tracking=True)
    state = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('inspection', 'In Inspection'),
        ('snagging', 'Snagging Open'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], default='scheduled', tracking=True, required=True)

    warranty_period_months = fields.Integer(default=12, string='Warranty Period (months)')
    warranty_id = fields.Many2one('realestate.warranty', readonly=True, copy=False)
    notes = fields.Html()

    @api.depends('checklist_item_ids.status')
    def _compute_progress(self):
        for rec in self:
            total = len(rec.checklist_item_ids)
            done = len(rec.checklist_item_ids.filtered(lambda i: i.status == 'done'))
            rec.checklist_total_count = total
            rec.checklist_done_count = done
            rec.checklist_progress = (done / total * 100.0) if total else 0.0

    @api.depends('snagging_issue_ids.state')
    def _compute_snagging_counts(self):
        for rec in self:
            rec.total_snagging_count = len(rec.snagging_issue_ids)
            rec.open_snagging_count = len(rec.snagging_issue_ids.filtered(
                lambda i: i.state in ('open', 'assigned', 'in_progress')
            ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.handover') or _('New')
        return super().create(vals_list)

    def action_load_template_items(self):
        for rec in self:
            if not rec.template_id:
                raise UserError(_("Pick a checklist template first."))
            if rec.checklist_item_ids:
                raise UserError(_("Checklist already populated. Clear it manually before reloading."))
            items = [(0, 0, {
                'sequence': t.sequence,
                'name': t.name,
                'category': t.category,
                'description': t.description,
            }) for t in rec.template_id.item_ids]
            rec.checklist_item_ids = items

    def action_start_inspection(self):
        for rec in self:
            if rec.state != 'scheduled':
                raise UserError(_("Inspection can only start from a Scheduled handover."))
            rec.state = 'inspection'

    def action_mark_snagging(self):
        for rec in self:
            rec.state = 'snagging'

    def action_complete(self):
        for rec in self:
            if rec.open_snagging_count:
                raise UserError(_("Resolve all open snagging issues before completing the handover."))
            unfinished = rec.checklist_item_ids.filtered(lambda i: i.status not in ('done', 'skipped'))
            if unfinished:
                raise UserError(_("All checklist items must be completed or skipped before completion."))
            rec.write({'state': 'completed', 'actual_date': fields.Datetime.now()})
            # Create warranty
            start = fields.Date.today()
            warranty = self.env['realestate.warranty'].create({
                'sale_contract_id': rec.sale_contract_id.id,
                'property_id': rec.property_id.id,
                'start_date': start,
                'end_date': start + timedelta(days=30 * rec.warranty_period_months),
                'period_months': rec.warranty_period_months,
            })
            rec.warranty_id = warranty.id
            # Mark sale contract handed over
            if rec.sale_contract_id.state == 'signed':
                rec.sale_contract_id.action_handover()

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
