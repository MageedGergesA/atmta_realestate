from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


#: Contract states `realestate.sale.contract.action_handover` accepts. A
#: handover can only be scheduled for, and only hands over, a contract in one.
HANDOVER_CONTRACT_STATES = ('signed', 'active', 'financially_cleared')


class Handover(models.Model):
    _name = 'realestate.handover'
    _description = 'Property Handover'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_date desc, id desc'

    name = fields.Char(string='Reference', copy=False, required=True, readonly=True, default=lambda self: _('New'))
    sale_contract_id = fields.Many2one(
        'realestate.sale.contract', string='Sale Contract', required=True, ondelete='cascade', tracking=True,
        domain=[('state', 'in', HANDOVER_CONTRACT_STATES)])
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

    @api.constrains('sale_contract_id')
    def _check_contract_is_handover_able(self):
        # Only a live contract can be handed over: a draft one has no buyer
        # commitment yet, and completing its handover used to transfer the
        # unit anyway. Checked when the contract is chosen, not afterwards --
        # completion itself moves the contract on to 'handed_over'.
        for rec in self:
            if rec.sale_contract_id.state not in HANDOVER_CONTRACT_STATES:
                raise ValidationError(_(
                    "Contract %(contract)s is %(state)s. A handover can only be scheduled "
                    "for a signed, active or financially cleared contract.",
                    contract=rec.sale_contract_id.display_name, state=rec.sale_contract_id.state))

    @api.constrains('sale_contract_id', 'state')
    def _check_one_handover_per_contract(self):
        # A unit is handed over once. A cancelled handover does not count, so
        # a missed appointment can be rescheduled.
        for rec in self.filtered(lambda h: h.state != 'cancelled'):
            others = self.search_count([
                ('id', '!=', rec.id),
                ('sale_contract_id', '=', rec.sale_contract_id.id),
                ('state', '!=', 'cancelled'),
            ])
            if others:
                raise ValidationError(_(
                    "Contract %s already has a handover. Cancel it before scheduling another.",
                    rec.sale_contract_id.display_name))

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
            if rec.state != 'inspection':
                raise UserError(_("Snagging can only be marked during inspection."))
            rec.state = 'snagging'

    def action_complete(self):
        for rec in self:
            if rec.state not in ('inspection', 'snagging'):
                raise UserError(_("A handover can only be completed after inspection."))
            if rec.open_snagging_count:
                raise UserError(_("Resolve all open snagging issues before completing the handover."))
            # open_snagging_count stops at in-progress work. A fix the
            # contractor reports (resolved) or one QA turned down (rejected)
            # is not a defect cleared either: only a verified fix is.
            unverified = rec.snagging_issue_ids.filtered(lambda i: i.state != 'verified')
            if unverified:
                raise UserError(_(
                    "Every snagging issue must be verified before completing the handover. "
                    "Not verified: %s", ', '.join(unverified.mapped('name'))))
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
                # Calendar months: 30-day months made a 12-month warranty 5 days short.
                'end_date': start + relativedelta(months=rec.warranty_period_months),
                'period_months': rec.warranty_period_months,
            })
            rec.warranty_id = warranty.id
            # Mark sale contract handed over. Every state the contract's own
            # action_handover accepts, not only 'signed': an activated or
            # financially cleared contract stayed live after its handover.
            if rec.sale_contract_id.state in HANDOVER_CONTRACT_STATES:
                rec.sale_contract_id.action_handover()

    def action_cancel(self):
        for rec in self:
            if rec.state in ('completed', 'cancelled'):
                raise UserError(_("A completed or cancelled handover cannot be cancelled."))
            rec.state = 'cancelled'
