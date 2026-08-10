from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class WorkItemCategory(models.Model):
    """Two-level taxonomy of construction work items (Civil > Excavation,
    MEP > HVAC, etc.). Kept simple: name + parent."""
    _name = 'realestate.work.item.category'
    _description = 'Work Item Category'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'parent_path, name'

    name = fields.Char(required=True)
    code = fields.Char(help='Short code for BOQ display (e.g. CIV-EXC-001).')
    parent_id = fields.Many2one('realestate.work.item.category', ondelete='cascade')
    parent_path = fields.Char(index=True)
    active = fields.Boolean(default=True)


class WorkItem(models.Model):
    """Catalog of billable work items — the finest billable unit of construction
    work. A BOQ line references one work item + quantity + rate."""
    _name = 'realestate.work.item'
    _description = 'Construction Work Item'
    _order = 'category_id, code, id'

    name = fields.Char(required=True)
    code = fields.Char(help='Short code (e.g. CIV-EXC-BULK).')
    category_id = fields.Many2one('realestate.work.item.category', ondelete='restrict')
    uom_id = fields.Many2one('uom.uom', string='Unit of Measure', required=True)
    default_rate = fields.Monetary(
        string='Default Rate', help='Reference unit rate. Individual BOQ lines can override.',
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Work item code must be unique.'),
    ]


BOQ_STATES = [
    ('draft', 'Draft'),
    ('approved', 'Approved'),
    ('locked', 'Locked'),
    ('cancelled', 'Cancelled'),
]


class BOQ(models.Model):
    """Bill of Quantities for a project or milestone.

    A BOQ is the engineering budget. Its lines are the source of truth for
    'what work is planned' and, once approved, for how much a contractor can
    certify on a payment certificate.
    """
    _name = 'realestate.boq'
    _description = 'Bill of Quantities'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, id desc'

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'),
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True, tracking=True,
        ondelete='cascade', index=True,
    )
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', tracking=True, ondelete='set null',
        domain="[('project_id', '=', project_id)]",
    )
    milestone_id = fields.Many2one(
        'realestate.construction.milestone', string='Milestone',
        tracking=True, ondelete='set null', index=True,
        domain="[('project_id', '=', project_id)]",
    )
    contractor_id = fields.Many2one(
        'realestate.contractor', string='Contractor', tracking=True,
        help='Scope owner. Leave blank for a design-side BOQ not yet awarded.',
    )
    revision = fields.Integer(default=1, tracking=True)

    line_ids = fields.One2many('realestate.boq.line', 'boq_id', string='Lines', copy=True)
    line_count = fields.Integer(compute='_compute_totals')
    total_amount = fields.Monetary(compute='_compute_totals', store=True)
    total_certified = fields.Monetary(compute='_compute_certified', store=True)
    certified_pct = fields.Float(
        string='Certified (%)', compute='_compute_certified', store=True,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    state = fields.Selection(
        BOQ_STATES, default='draft', tracking=True, required=True, copy=False,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    supersedes_id = fields.Many2one('realestate.boq', readonly=True, copy=False)
    superseded_by_id = fields.Many2one(
        'realestate.boq', readonly=True, copy=False)
    notes = fields.Html()

    #: Fields that describe *what was agreed*. Everything else is commentary.
    _AGREED_FIELDS = {'project_id', 'phase_id', 'milestone_id',
                      'contractor_id', 'currency_id'}

    @api.depends('line_ids.line_amount')
    def _compute_totals(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.total_amount = sum(rec.line_ids.mapped('line_amount'))

    @api.depends(
        'line_ids.certified_amount', 'line_ids.line_amount',
    )
    def _compute_certified(self):
        for rec in self:
            rec.total_certified = sum(rec.line_ids.mapped('certified_amount'))
            rec.certified_pct = (
                (rec.total_certified / rec.total_amount * 100.0)
                if rec.total_amount else 0.0
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.boq') or 'BOQ/NEW'
        return super().create(vals_list)

    def write(self, vals):
        """An approved BOQ is what the contract says. It is revised, not edited.

        Phase 0 found no guard at all: the quantity a contractor may certify
        against could be changed after approval, by anyone, leaving nothing
        behind that said it had been.
        """
        if self.env.context.get('re_boq_revision'):
            return super().write(vals)
        touched = self._AGREED_FIELDS & set(vals)
        if touched:
            frozen = self.filtered(lambda b: b.state in ('approved', 'locked'))
            if frozen:
                raise UserError(_(
                    "%(refs)s are approved. Create a revision rather than "
                    "editing the quantities work is certified against.",
                    refs=', '.join(frozen.mapped('name'))))
        return super().write(vals)

    # ---------- Actions ----------
    def action_create_revision(self):
        """Copy the BOQ forward, leaving the approved one intact."""
        self.ensure_one()
        if self.state not in ('approved', 'locked'):
            raise UserError(_(
                "A draft BOQ is edited directly. Revisions are for what was "
                "already agreed."))
        revision = self.with_context(re_boq_revision=True).copy({
            'name': self.name,
            'revision': self.revision + 1,
            'state': 'draft',
            'supersedes_id': self.id,
        })
        self.with_context(re_boq_revision=True).superseded_by_id = revision.id
        return revision

    def action_approve(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft BOQs can be approved."))
            if not rec.line_ids:
                raise UserError(_("Add at least one line before approving."))
            rec.state = 'approved'

    def action_lock(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_("Only approved BOQs can be locked."))
            rec.state = 'locked'

    def action_reset_to_draft(self):
        for rec in self:
            if any(l.certified_qty > 0 for l in rec.line_ids):
                raise UserError(_(
                    "Cannot reset a BOQ that already has certified quantities."
                ))
            rec.state = 'draft'

    def action_cancel(self):
        for rec in self:
            if any(l.certified_qty > 0 for l in rec.line_ids):
                raise UserError(_(
                    "Cannot cancel a BOQ that already has certified quantities."
                ))
            rec.state = 'cancelled'

    def action_create_material_request(self):
        """Spawn a material request from all BOQ lines whose work item is
        linked to a product. Skips lines without a product mapping."""
        self.ensure_one()
        if 'realestate.material.request' not in self.env.registry:
            raise UserError(_("Install real_estate_procurement to use this."))
        MR = self.env['realestate.material.request']
        MRL = self.env['realestate.material.request.line']
        lines_with_product = self.line_ids.filtered(lambda l: l.product_id)
        if not lines_with_product:
            raise UserError(_(
                "No BOQ line has a linked product. Map products on your work items first."
            ))
        request = MR.create({
            'project_id': self.project_id.id,
            'notes': f'<p>Auto-generated from BOQ {self.name}</p>',
        })
        for l in lines_with_product:
            MRL.create({
                'request_id': request.id,
                'product_id': l.product_id.id,
                'description': l.description or l.work_item_id.name,
                'qty': l.quantity,
                'uom_id': l.uom_id.id,
            })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Material Request'),
            'res_model': 'realestate.material.request',
            'res_id': request.id,
            'view_mode': 'form',
        }


class BOQLine(models.Model):
    _name = 'realestate.boq.line'
    _description = 'BOQ Line'
    _order = 'boq_id, sequence, id'

    boq_id = fields.Many2one('realestate.boq', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    work_item_id = fields.Many2one(
        'realestate.work.item', string='Work Item', required=True,
    )
    description = fields.Char(help='Overrides work item name when set.')
    product_id = fields.Many2one(
        'product.product', string='Material Product',
        help='Optional. When set, this line can feed the material request loop.',
    )
    quantity = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one(
        'uom.uom', string='UoM', required=True,
        compute='_compute_uom_rate', store=True, readonly=False,
    )
    unit_rate = fields.Monetary(
        string='Unit Rate', required=True, default=0.0,
        compute='_compute_uom_rate', store=True, readonly=False,
    )
    line_amount = fields.Monetary(
        string='Amount', compute='_compute_line_amount', store=True,
    )

    #: What the contract now allows, as opposed to what it originally said.
    variation_ids = fields.One2many(
        'realestate.boq.line.variation', 'boq_line_id', readonly=True)
    variation_quantity = fields.Float(
        compute='_compute_authorised', store=True,
        help="Quantity added or removed by approved change orders.")
    authorised_quantity = fields.Float(
        compute='_compute_authorised', store=True,
        help="Original quantity plus approved variations. This — not the "
             "original quantity — is what may be certified.")

    certified_qty = fields.Float(
        string='Certified Qty', compute='_compute_certified', store=True,
    )
    certified_amount = fields.Monetary(
        string='Certified Amount', compute='_compute_certified', store=True,
    )
    remaining_qty = fields.Float(
        string='Remaining Qty', compute='_compute_certified', store=True,
    )
    is_over_certified = fields.Boolean(
        compute='_compute_certified', store=True,
        help="More has been certified than is authorised. Phase 0 hid this by "
             "flooring the remaining quantity at zero.")
    certification_line_ids = fields.One2many(
        'realestate.construction.payment.certificate.line',
        'boq_line_id',
        string='Certification Lines',
    )

    currency_id = fields.Many2one(
        related='boq_id.currency_id', store=True, readonly=True,
    )
    project_id = fields.Many2one(
        related='boq_id.project_id', store=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        related='boq_id.company_id', store=True, readonly=True, index=True,
    )

    # M2 — the coding that lets a BOQ seed a budget and lets certified work be
    # compared with what was budgeted for it. Optional: a legacy BOQ has
    # neither, and inventing them is exactly what the migration refuses to do.
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', index=True,
        ondelete='restrict',
        domain="[('project_id', '=', project_id)]",
    )
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code', index=True,
        ondelete='restrict',
    )

    @api.depends('work_item_id')
    def _compute_uom_rate(self):
        for ln in self:
            if ln.work_item_id and not ln.uom_id:
                ln.uom_id = ln.work_item_id.uom_id
            if ln.work_item_id and not ln.unit_rate:
                ln.unit_rate = ln.work_item_id.default_rate

    @api.depends('quantity', 'unit_rate')
    def _compute_line_amount(self):
        for ln in self:
            ln.line_amount = ln.quantity * ln.unit_rate

    @api.depends('quantity', 'variation_ids.quantity_delta',
                 'variation_ids.change_order_id.state')
    def _compute_authorised(self):
        for ln in self:
            approved = ln.variation_ids.filtered(
                lambda v: v.change_order_id.state in ('approved', 'implemented'))
            ln.variation_quantity = sum(approved.mapped('quantity_delta'))
            ln.authorised_quantity = (ln.quantity or 0.0) + ln.variation_quantity

    @api.depends(
        'certification_line_ids.qty',
        'certification_line_ids.amount',
        'certification_line_ids.certificate_id.state',
        'authorised_quantity',
    )
    def _compute_certified(self):
        for ln in self:
            live = ln.certification_line_ids.filtered(
                lambda cl: cl.certificate_id.state in ('certified', 'invoiced', 'paid')
            )
            ln.certified_qty = sum(live.mapped('qty'))
            ln.certified_amount = sum(live.mapped('amount'))
            ln.remaining_qty = max(0.0, ln.authorised_quantity - ln.certified_qty)
            ln.is_over_certified = ln.certified_qty > ln.authorised_quantity + 1e-6

    @api.constrains('quantity', 'unit_rate')
    def _check_positive(self):
        for ln in self:
            if ln.quantity < 0 or ln.unit_rate < 0:
                raise ValidationError(_("Quantity and rate must be non-negative."))

    def write(self, vals):
        """The agreed quantity and rate move by variation, never by typing."""
        if self.env.context.get('re_boq_revision'):
            return super().write(vals)
        if {'quantity', 'unit_rate', 'work_item_id'} & set(vals):
            frozen = self.filtered(
                lambda l: l.boq_id.state in ('approved', 'locked'))
            if frozen:
                raise UserError(_(
                    "%(items)s belong to an approved BOQ. Raise a change "
                    "order and apply a variation — an approved quantity that "
                    "can be retyped is not a contract.",
                    items=', '.join(
                        frozen.mapped(lambda l: l.display_name or '?'))))
        return super().write(vals)

    def apply_variation(self, quantity_delta, change_order, reason=None,
                        new_rate=None):
        """Move the authorised quantity, on the authority of a change order.

        Deliberately not a write to `quantity`: the original quantity is the
        contract, and the difference between it and what is authorised today
        is the number the final account is built from.
        """
        self.ensure_one()
        if not change_order:
            raise UserError(_(
                "A quantity moves on somebody's authority. Name the change "
                "order."))
        if change_order.state not in ('approved', 'implemented'):
            raise UserError(_(
                "%(order)s is %(state)s. An unapproved change order does not "
                "authorise a quantity.",
                order=change_order.display_name, state=change_order.state))
        if not quantity_delta:
            raise UserError(_("A variation of nothing is not a variation."))
        variation = self.env['realestate.boq.line.variation'].create({
            'boq_line_id': self.id,
            'change_order_id': change_order.id,
            'quantity_delta': quantity_delta,
            'new_rate': new_rate or 0.0,
            'reason': reason,
        })
        self.invalidate_recordset(['variation_quantity', 'authorised_quantity',
                                   'remaining_qty', 'is_over_certified'])
        return variation


class BOQLineVariation(models.Model):
    """One approved change to one BOQ line's authorised quantity."""
    _name = 'realestate.boq.line.variation'
    _description = 'BOQ Line Variation'
    _order = 'boq_line_id, id'

    boq_line_id = fields.Many2one(
        'realestate.boq.line', required=True, ondelete='cascade', index=True)
    change_order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='restrict', index=True)
    quantity_delta = fields.Float(
        required=True,
        help="Positive adds authorised quantity, negative omits it.")
    new_rate = fields.Monetary(
        help="A re-rated variation. Zero means the original rate stands.")
    reason = fields.Char()
    currency_id = fields.Many2one(
        related='boq_line_id.currency_id', readonly=True)
    company_id = fields.Many2one(
        related='boq_line_id.boq_id.company_id', store=True, readonly=True)

    def write(self, vals):
        raise UserError(_(
            "A variation is what a change order authorised. Raise another "
            "one rather than editing it."))
