# -*- coding: utf-8 -*-
"""M8 — material inspection at the point of receipt.

```
    A RECEIPT SAYS THE LORRY ARRIVED.
    AN INSPECTION SAYS WHAT ON IT IS FIT TO USE.
```

Everything before this milestone treated *delivered* and *accepted* as the same
event. They are not, and the gap between them is where money is lost: material
booked in, billed, paid for and then found unusable is a cost the project has
already absorbed by the time anybody writes it down.

**What this model does not do.** It does not hold quality criteria, method
statements or checkpoints. Construction owns the Inspection & Test Plan, with
disciplines, acceptance criteria and hold points, and building a second one
here would be the commitment mistake of M7 repeated in a different currency —
two structures describing the same thing, disagreeing under exactly the
conditions nobody tests. `_quality_reference_values()` is the extension point
where an ITP is attached; see §112 for why that field is not declared here.

**Rejection is a quantity, not a flag.** Half a load can be sound and half
cracked, and a boolean would force the storekeeper to lie in one direction or
the other. What is accepted is what is received; what is rejected never enters
stock, never rolls up to the requisition, and is never billable.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: Who may record an inspection result. Deliberately not the buyer: the person
#: who chose the vendor is not the person who should certify that the vendor's
#: material is acceptable. That separation is the entire point of an
#: inspection, and a system that lets one user do both has recorded a signature
#: rather than a control.
INSPECTION_GROUPS = (
    'atmta_roles.group_procurement_material_inspector,'
    'atmta_roles.group_procurement_manager'
)


class ReceiptInspection(models.Model):
    _name = 'realestate.procurement.receipt.inspection'
    _description = 'Material Receipt Inspection'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, readonly=True,
                       default=lambda self: _('New'))
    picking_id = fields.Many2one(
        'stock.picking', string='Receipt', required=True, ondelete='cascade',
        index=True, readonly=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Purchase Order',
        compute='_compute_source', store=True, index=True)
    project_id = fields.Many2one(
        'realestate.project', string='Project',
        compute='_compute_source', store=True, index=True)
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', compute='_compute_source', store=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)

    state = fields.Selection([
        ('draft', 'To Inspect'),
        ('passed', 'Accepted'),
        ('partial', 'Partially Accepted'),
        ('failed', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    line_ids = fields.One2many(
        'realestate.procurement.receipt.inspection.line', 'inspection_id',
        string='Inspected Items')

    inspector_id = fields.Many2one(
        'res.users', string='Inspected By', readonly=True, copy=False,
        tracking=True)
    inspected_on = fields.Datetime(readonly=True, copy=False, tracking=True)
    conclusion = fields.Text(
        help="What was found. Required when anything is rejected — a "
             "rejection nobody can explain is a dispute waiting to happen, "
             "and the vendor is entitled to the reason.")

    accepted_qty = fields.Float(compute='_compute_totals', store=True,
                                digits='Product Unit of Measure')
    rejected_qty = fields.Float(compute='_compute_totals', store=True,
                                digits='Product Unit of Measure')
    received_qty = fields.Float(compute='_compute_totals', store=True,
                                digits='Product Unit of Measure')

    _sql_constraints = [
        ('picking_uniq', 'unique(picking_id)',
         'A receipt has one inspection. Record the outcome on the existing '
         'one rather than opening a second view of the same delivery.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('picking_id')
    def _compute_source(self):
        """Where this delivery came from, read as the system rather than the user.

        An inspector is a site role. Expecting one to hold read access to
        purchase orders and projects would make the control unusable by the
        only people who can perform it.
        """
        for rec in self:
            picking = rec.picking_id.sudo()
            order = picking.move_ids.purchase_line_id.order_id[:1]
            rec.purchase_order_id = order.id or False
            rec.partner_id = picking.partner_id.id or False
            rec.project_id = (
                order.re_project_id.id
                if order and 're_project_id' in order._fields else False)

    @api.depends('line_ids.accepted_qty', 'line_ids.rejected_qty',
                 'line_ids.received_qty')
    def _compute_totals(self):
        for rec in self:
            rec.accepted_qty = sum(rec.line_ids.mapped('accepted_qty'))
            rec.rejected_qty = sum(rec.line_ids.mapped('rejected_qty'))
            rec.received_qty = sum(rec.line_ids.mapped('received_qty'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.receipt.inspection') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Building the sheet
    # ------------------------------------------------------------------
    @api.model
    def _for_picking(self, picking, create=True):
        """The inspection for one receipt, made if it does not exist yet.

        Built from the moves rather than from the purchase order, because what
        turned up is what is being inspected. A short delivery is inspected for
        what arrived; the shortfall is a delivery question, not a quality one.
        """
        existing = self.sudo().search([('picking_id', '=', picking.id)],
                                      limit=1)
        if existing or not create:
            return existing
        lines = []
        for move in picking.move_ids:
            quantity = move.product_uom_qty
            if not quantity:
                continue
            lines.append((0, 0, {
                'move_id': move.id,
                'product_id': move.product_id.id,
                'received_qty': quantity,
                # Nothing is accepted by default. A sheet that arrived
                # pre-accepted would be signed without being read, which is
                # the failure mode this whole model exists to prevent.
                'accepted_qty': 0.0,
            }))
        return self.sudo().create({
            'picking_id': picking.id,
            'company_id': picking.company_id.id,
            'line_ids': lines,
        })

    # ------------------------------------------------------------------
    # Recording the outcome
    # ------------------------------------------------------------------
    def _assert_may_inspect(self):
        self.ensure_one()
        if self.env.su:
            return
        groups = INSPECTION_GROUPS.split(',')
        if not any(self.env.user.has_group(group) for group in groups):
            raise UserError(_(
                "Recording an inspection result is an inspector's decision. "
                "%s does not hold that authority.") % self.env.user.name)

    def action_record(self):
        """Close the inspection on what the lines say.

        The state is derived, never chosen. Letting somebody mark a sheet
        "Accepted" while its lines reject half the load would put the summary
        and the detail in disagreement, and the summary is what the next person
        reads.
        """
        for rec in self:
            rec._assert_may_inspect()
            if rec.state != 'draft':
                raise UserError(_(
                    "%(name)s is already %(state)s.",
                    name=rec.name, state=rec.state))
            if not rec.line_ids:
                raise UserError(_(
                    "%s inspects nothing. An empty sheet is not a pass.")
                    % rec.name)
            if rec.rejected_qty and not (rec.conclusion or '').strip():
                raise UserError(_(
                    "%s rejects material without saying why. The vendor is "
                    "entitled to the reason, and so is whoever settles the "
                    "dispute.") % rec.name)
            unexplained = rec.line_ids._unexplained_rejections()
            if unexplained:
                raise UserError(_(
                    "%(name)s rejects %(items)s with no finding recorded "
                    "against them. What was wrong with an item is the one "
                    "thing its line has to say.",
                    name=rec.name,
                    items=', '.join(
                        unexplained.product_id.mapped('display_name'))))
            rec.write({
                'state': rec._derive_state(),
                'inspector_id': rec.env.user.id,
                'inspected_on': fields.Datetime.now(),
            })
        return True

    def _derive_state(self):
        self.ensure_one()
        if not self.accepted_qty:
            return 'failed'
        if self.rejected_qty:
            return 'partial'
        return 'passed'

    def action_cancel(self, reason=None):
        for rec in self:
            rec._assert_may_inspect()
            if rec.state != 'draft':
                raise UserError(_(
                    "%s has already been recorded. An inspection result is "
                    "not withdrawn; a re-inspection is a new receipt.")
                    % rec.name)
            rec.state = 'cancelled'
            if reason:
                rec.message_post(body=_('Cancelled: %s') % reason)
        return True

    def _quality_reference_values(self):
        """Extension point for a quality plan — M8, and empty on purpose.

        Construction owns the Inspection & Test Plan. A first-class relation
        to it belongs on this model, and cannot be declared here: Procurement
        does not depend on Construction — the dependency runs the other way,
        which is how `re_cost_code_id` reaches `purchase.order.line`. The field
        is Construction's to add, and Construction is frozen. §112 records the
        constraint rather than working around it with a reference field that
        would name a model this module cannot see.
        """
        self.ensure_one()
        return {}


class ReceiptInspectionLine(models.Model):
    _name = 'realestate.procurement.receipt.inspection.line'
    _description = 'Material Receipt Inspection Line'
    _order = 'inspection_id, id'

    inspection_id = fields.Many2one(
        'realestate.procurement.receipt.inspection', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='inspection_id.company_id', store=True, index=True)
    move_id = fields.Many2one(
        'stock.move', string='Stock Move', required=True, ondelete='cascade',
        index=True, readonly=True)
    product_id = fields.Many2one('product.product', required=True,
                                 readonly=True)
    received_qty = fields.Float(
        string='Delivered', readonly=True, digits='Product Unit of Measure',
        help="What arrived. Not what was ordered — a short delivery is a "
             "delivery question, and this sheet is about condition.")
    accepted_qty = fields.Float(
        string='Accepted', digits='Product Unit of Measure',
        help="What may be used. This is what enters stock and what the "
             "requisition counts as received.")
    rejected_qty = fields.Float(
        compute='_compute_rejected', store=True,
        digits='Product Unit of Measure',
        help="Derived. Rejecting is what is left over after accepting, so the "
             "two can never be recorded as disagreeing.")
    reason = fields.Char(
        string='Finding',
        help="Why this item was rejected — damaged, wrong grade, no "
             "certificate, out of tolerance.")

    _sql_constraints = [
        ('move_uniq', 'unique(inspection_id, move_id)',
         'One line per delivered item.'),
        ('accepted_not_negative', 'CHECK (accepted_qty >= 0)',
         'An accepted quantity below zero is not a rejection, it is a typo.'),
    ]

    @api.depends('received_qty', 'accepted_qty')
    def _compute_rejected(self):
        for line in self:
            line.rejected_qty = max(
                0.0, (line.received_qty or 0.0) - (line.accepted_qty or 0.0))

    @api.constrains('accepted_qty', 'received_qty')
    def _check_accepted_within_delivered(self):
        """Nobody accepts more than turned up.

        Without this, a mistyped acceptance would push a quantity into stock
        that no lorry ever carried, and the stock ledger would carry the error
        long after the sheet was forgotten.
        """
        for line in self:
            if (line.accepted_qty or 0.0) - (line.received_qty or 0.0) > 1e-6:
                raise ValidationError(_(
                    "%(product)s: %(accepted)s accepted against %(received)s "
                    "delivered. Accepting more than arrived puts stock into "
                    "the system that nobody delivered.",
                    product=line.product_id.display_name,
                    accepted=line.accepted_qty,
                    received=line.received_qty))

    def _unexplained_rejections(self):
        """Lines rejecting material with no finding written against them.

        Deliberately **not** an `@api.constrains`. A sheet opens with nothing
        accepted — that is the point, so it cannot be signed without being read
        — which means every line is fully rejected the moment it is created. A
        constraint would refuse to open the sheet at all, and the first version
        of this model did exactly that. The requirement belongs at the moment
        the inspector commits to the result, which is `action_record()`.
        """
        return self.filtered(
            lambda line: line.rejected_qty > 1e-6
            and not (line.reason or '').strip())
