from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SubcontractPOWizard(models.TransientModel):
    """Build a master subcontract PO for a contractor — one line per milestone in scope."""
    _name = 'realestate.subcontract.po.wizard'
    _description = 'Subcontract PO Wizard'

    contractor_id = fields.Many2one(
        'realestate.contractor', required=True, ondelete='cascade',
    )
    partner_id = fields.Many2one(
        related='contractor_id.partner_id', readonly=True,
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project',
        help='Filter milestones to one project. Leave blank to pick milestones across projects.',
    )
    milestone_ids = fields.Many2many(
        'realestate.construction.milestone',
        relation='subcontract_po_wiz_milestone_rel',
        column1='wizard_id', column2='milestone_id',
        string='Milestones in Scope',
        domain="[('contractor_id', '=', contractor_id), ('project_id', '=?', project_id)]",
    )
    service_product_id = fields.Many2one(
        'product.product', string='Service Product',
        help='Service line product on the PO. Auto-created if blank.',
    )
    expected_start_date = fields.Date(default=fields.Date.context_today)
    notes = fields.Text()

    total_committed = fields.Monetary(
        compute='_compute_totals', help='Sum of selected milestone budgets.',
    )
    currency_id = fields.Many2one(
        related='contractor_id.currency_id', readonly=True,
    )

    @api.depends('milestone_ids')
    def _compute_totals(self):
        for rec in self:
            rec.total_committed = sum(rec.milestone_ids.mapped('budget_amount'))

    @api.onchange('project_id')
    def _onchange_project_id(self):
        # Re-filter selected milestones to the chosen project (if any)
        if self.project_id:
            self.milestone_ids = self.milestone_ids.filtered(
                lambda m: m.project_id == self.project_id
            )

    def action_create_po(self):
        self.ensure_one()
        if not self.milestone_ids:
            raise UserError(_("Pick at least one milestone."))
        if any(m.budget_amount <= 0 for m in self.milestone_ids):
            raise UserError(_(
                "Every selected milestone must have a non-zero Budgeted Cost."
            ))
        if self.contractor_id.contract_po_id and self.contractor_id.contract_po_id.state not in ('cancel',):
            raise UserError(_(
                "This contractor already has a Subcontract PO (%s). "
                "Cancel it before creating a new one, or add lines to it manually."
            ) % self.contractor_id.contract_po_id.name)

        product = self.service_product_id or self.contractor_id._ensure_service_product()
        re_project = self.project_id or self.milestone_ids[:1].project_id

        po_lines = []
        for milestone in self.milestone_ids:
            po_lines.append((0, 0, {
                'product_id': product.id,
                'name': milestone.name,
                'product_qty': 1.0,
                'product_uom': product.uom_po_id.id or product.uom_id.id,
                'price_unit': milestone.budget_amount,
                'date_planned': fields.Datetime.now(),
            }))
        po = self.env['purchase.order'].create({
            'partner_id': self.contractor_id.partner_id.id,
            'date_order': fields.Datetime.now(),
            're_project_id': re_project.id if re_project else False,
            're_source_model': 'realestate.contractor',
            're_source_id': self.contractor_id.id,
            'order_line': po_lines,
        })
        self.contractor_id.contract_po_id = po.id
        # Confirm so it's a real commitment (like material-request POs) — drives
        # the contractor's committed total and lets certificates bill its lines.
        po.button_confirm()

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': po.id,
        }
