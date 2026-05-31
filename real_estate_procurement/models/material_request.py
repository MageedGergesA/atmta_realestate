from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MaterialRequest(models.Model):
    """Unified material/service request raised from any real-estate module.

    The source (construction task, handover snag, rental ticket, aftermarket
    ticket) is captured as a polymorphic reference so a single procurement loop
    serves every channel.
    """
    _name = 'realestate.material.request'
    _description = 'Real Estate Material Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, needed_by asc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )

    # ---------- Source ----------
    source_model = fields.Selection(
        selection='_get_source_model_selection',
        string='Source Type',
        help='Which module raised this request.',
    )
    source_ref = fields.Reference(
        selection='_get_source_model_selection',
        string='Source Document',
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', tracking=True,
        help='Auto-derived from the source document when applicable.',
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', tracking=True,
        help='Set for snag/maintenance/aftermarket requests targeting a specific unit.',
    )

    # ---------- Header ----------
    requested_by_id = fields.Many2one(
        'res.users', string='Requested By', required=True, tracking=True,
        default=lambda self: self.env.user,
    )
    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    approval_date = fields.Datetime(string='Approved On', readonly=True, copy=False)
    needed_by = fields.Date(string='Needed By', tracking=True)
    priority = fields.Selection([
        ('0', 'Normal'),
        ('1', 'Urgent'),
    ], default='0', tracking=True,
        help='Urgent requests bypass approval and feed PO due dates with a short lead time.')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('ordered', 'Ordered'),
        ('partial', 'Partially Received'),
        ('received', 'Received'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False)

    # ---------- Lines ----------
    line_ids = fields.One2many(
        'realestate.material.request.line', 'request_id', string='Lines', copy=True,
    )
    line_count = fields.Integer(compute='_compute_line_count')
    estimated_total = fields.Monetary(
        string='Estimated Total', compute='_compute_estimated_total', store=True,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    # ---------- POs ----------
    purchase_order_ids = fields.Many2many(
        'purchase.order',
        string='Purchase Orders',
        compute='_compute_purchase_orders', store=True,
    )
    po_count = fields.Integer(compute='_compute_purchase_orders', store=True)

    notes = fields.Html()

    @api.model
    def _get_source_model_selection(self):
        """Each downstream module appends its own source model here by overriding."""
        return [
            ('realestate.construction.task', 'Construction Task'),
            ('realestate.construction.milestone', 'Construction Milestone'),
        ]

    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends('line_ids.estimated_cost')
    def _compute_estimated_total(self):
        for rec in self:
            rec.estimated_total = sum(rec.line_ids.mapped('estimated_cost'))

    @api.depends('line_ids.po_line_id.order_id')
    def _compute_purchase_orders(self):
        for rec in self:
            pos = rec.line_ids.mapped('po_line_id.order_id')
            rec.purchase_order_ids = pos
            rec.po_count = len(pos)

    @api.onchange('source_ref')
    def _onchange_source_ref(self):
        """Auto-fill project/property from the source document where possible."""
        if not self.source_ref:
            return
        src = self.source_ref
        self.source_model = src._name
        if 'project_id' in src._fields and src.project_id:
            self.project_id = src.project_id
        if 'property_id' in src._fields and src.property_id:
            self.property_id = src.property_id

    @api.constrains('line_ids')
    def _check_lines(self):
        for rec in self:
            if rec.state not in ('draft', 'cancelled') and not rec.line_ids:
                raise ValidationError(_("A material request must have at least one line before submission."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.material.request') or 'MR-NEW'
        return super().create(vals_list)

    # ---------- State actions ----------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft requests can be submitted."))
            if not rec.line_ids:
                raise UserError(_("Add at least one line before submitting."))
            # Urgent requests bypass approval
            if rec.priority == '1':
                rec.state = 'approved'
                rec.approved_by_id = self.env.user
                rec.approval_date = fields.Datetime.now()
            else:
                rec.state = 'submitted'

    def action_approve(self):
        if not self.env.user.has_group('real_estate_procurement.group_procurement_approver'):
            raise UserError(_("You do not have permission to approve material requests."))
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only submitted requests can be approved."))
            rec.state = 'approved'
            rec.approved_by_id = self.env.user
            rec.approval_date = fields.Datetime.now()

    def action_back_to_draft(self):
        for rec in self:
            if rec.state in ('ordered', 'partial', 'received', 'done'):
                raise UserError(_("Cannot return to draft after a PO has been created."))
            rec.state = 'draft'
            rec.approved_by_id = False
            rec.approval_date = False

    def action_cancel(self):
        for rec in self:
            if rec.purchase_order_ids.filtered(lambda po: po.state not in ('draft', 'sent', 'cancel')):
                raise UserError(_(
                    "Cannot cancel — at least one linked PO is already confirmed. "
                    "Cancel the PO first."
                ))
            rec.state = 'cancelled'

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.purchase_order_ids.ids)],
        }

    # ---------- PO creation ----------
    def action_create_purchase_orders(self):
        """Group approved lines by preferred supplier and spawn one PO per supplier.

        Lines without a preferred supplier remain on the request — user must
        pick a supplier and rerun, or override at the PO line level.
        """
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Approve the request before creating purchase orders."))

        lines_by_supplier = {}
        skipped_lines = self.env['realestate.material.request.line']
        for line in self.line_ids:
            if line.po_line_id:
                continue
            supplier = line._get_preferred_supplier()
            if not supplier:
                skipped_lines |= line
                continue
            lines_by_supplier.setdefault(supplier.id, self.env['realestate.material.request.line'])
            lines_by_supplier[supplier.id] |= line

        if not lines_by_supplier:
            raise UserError(_(
                "No preferred supplier set on any product. "
                "Set a supplier on each product's purchase tab, or create POs manually and link them."
            ))

        created_pos = self.env['purchase.order']
        lead_days = 3 if self.priority == '1' else 14
        date_planned = fields.Datetime.now() + fields.timedelta(days=lead_days)

        for supplier_id, lines in lines_by_supplier.items():
            po_lines = []
            for ln in lines:
                po_lines.append((0, 0, {
                    'product_id': ln.product_id.id,
                    'name': ln.description or ln.product_id.display_name,
                    'product_qty': ln.qty,
                    'product_uom': ln.uom_id.id,
                    'price_unit': ln._unit_price(),
                    'date_planned': date_planned,
                    're_material_request_line_id': ln.id,
                }))
            po = self.env['purchase.order'].create({
                'partner_id': supplier_id,
                'date_order': fields.Datetime.now(),
                're_project_id': self.project_id.id if self.project_id else False,
                're_source_model': 'realestate.material.request',
                're_source_id': self.id,
                'order_line': po_lines,
            })
            for ln, po_line in zip(lines, po.order_line):
                ln.po_line_id = po_line.id
            created_pos |= po

        # Confirm so receipts are generated and routed to the project location.
        created_pos.button_confirm()

        self.state = 'ordered'

        if skipped_lines:
            msg = _("Created %d PO(s). %d line(s) skipped (no preferred supplier).") % (
                len(created_pos), len(skipped_lines),
            )
            self.message_post(body=msg)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created_pos.ids)],
        }

    # ---------- Receipt rollup ----------
    @api.depends('line_ids.received_qty', 'line_ids.qty', 'state')
    def _compute_state_from_receipts(self):
        """Not exposed as compute — called by line write hooks."""
        pass

    def _refresh_state_from_lines(self):
        for rec in self:
            if rec.state not in ('ordered', 'partial'):
                continue
            total_qty = sum(rec.line_ids.mapped('qty'))
            received = sum(rec.line_ids.mapped('received_qty'))
            if total_qty <= 0:
                continue
            if received >= total_qty:
                rec.state = 'received'
            elif received > 0:
                rec.state = 'partial'
