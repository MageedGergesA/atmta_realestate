from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Contractor(models.Model):
    _name = 'realestate.contractor'
    _description = 'Construction Contractor'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(string='Contractor', required=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', required=True, ondelete='restrict')
    specialization = fields.Selection([
        ('civil', 'Civil / Structure'),
        ('mep', 'MEP (Mechanical/Electrical/Plumbing)'),
        ('finishing', 'Finishing'),
        ('landscape', 'Landscape'),
        ('facade', 'Facade / Cladding'),
        ('hvac', 'HVAC'),
        ('elevator', 'Elevator'),
        ('general', 'General Contractor'),
        ('other', 'Other'),
    ], default='general', required=True)
    license_number = fields.Char(string='License #')
    rating = fields.Selection([
        ('a', 'A'),
        ('b', 'B'),
        ('c', 'C'),
    ], default='b')
    contact_email = fields.Char(related='partner_id.email', readonly=False)
    contact_phone = fields.Char(related='partner_id.phone', readonly=False)
    notes = fields.Html()
    active = fields.Boolean(default=True)

    milestone_ids = fields.One2many('realestate.construction.milestone', 'contractor_id', string='Milestones')
    milestone_count = fields.Integer(compute='_compute_milestone_count')

    # ----- Procurement linkage -----
    service_product_id = fields.Many2one(
        'product.product', string='Subcontract Service Product',
        help='Service product used as the line item on subcontract POs. '
             'Auto-created from the contractor name + specialization if left blank.',
    )
    contract_po_id = fields.Many2one(
        'purchase.order', string='Subcontract PO', copy=False,
        help='Master subcontract purchase order — one line per milestone in scope.',
    )
    all_po_ids = fields.One2many(
        'purchase.order', compute='_compute_all_pos', string='All POs',
    )
    po_count = fields.Integer(compute='_compute_all_pos')
    bill_count = fields.Integer(compute='_compute_bill_totals')

    total_po_committed = fields.Monetary(
        string='Committed', compute='_compute_bill_totals', store=False,
        help='Sum of confirmed subcontract PO totals for this contractor.',
    )
    total_billed = fields.Monetary(
        string='Billed', compute='_compute_bill_totals', store=False,
        help='Sum of validated vendor bills.',
    )
    total_paid = fields.Monetary(
        string='Paid', compute='_compute_bill_totals', store=False,
    )
    outstanding_payable = fields.Monetary(
        string='Outstanding', compute='_compute_bill_totals', store=False,
        help='Billed amount not yet paid.',
    )

    # ----- Retention -----
    retention_pct = fields.Float(
        string='Default Retention (%)', default=5.0,
        help='Default % withheld from every payment certificate issued to this contractor. '
             'Released as a single payment after handover.',
    )
    payment_certificate_ids = fields.One2many(
        'realestate.construction.payment.certificate', 'contractor_id',
        string='Payment Certificates',
    )
    total_certified = fields.Monetary(
        string='Total Certified', compute='_compute_retention_totals', store=True,
        help='Sum of gross amounts across all certified+ certificates.',
    )
    total_retention_held = fields.Monetary(
        string='Retention Held', compute='_compute_retention_totals', store=True,
        help='Cumulative retention not yet released.',
    )
    retention_released = fields.Boolean(
        string='Retention Released', default=False, copy=False, readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    def _compute_milestone_count(self):
        for rec in self:
            rec.milestone_count = len(rec.milestone_ids)

    def _compute_all_pos(self):
        PO = self.env['purchase.order']
        for rec in self:
            pos = PO.search([('partner_id', '=', rec.partner_id.id)]) if rec.partner_id else PO
            rec.all_po_ids = pos
            rec.po_count = len(pos)

    def _compute_bill_totals(self):
        Move = self.env['account.move']
        for rec in self:
            committed = sum(rec.all_po_ids.filtered(
                lambda po: po.state in ('purchase', 'done')
            ).mapped('amount_total'))
            bills = Move.search([
                ('partner_id', '=', rec.partner_id.id),
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
            ]) if rec.partner_id else Move
            billed = sum(bills.mapped('amount_total_signed'))
            paid = sum(bills.filtered(lambda b: b.payment_state in ('paid', 'in_payment')).mapped('amount_total_signed'))
            rec.total_po_committed = committed
            rec.total_billed = billed
            rec.total_paid = paid
            rec.outstanding_payable = max(billed - paid, 0.0)
            rec.bill_count = len(bills)

    # ----- Auto-create service product -----
    def _ensure_service_product(self):
        """Create a per-contractor subcontract service product if not set."""
        self.ensure_one()
        if self.service_product_id:
            return self.service_product_id
        spec_label = dict(self._fields['specialization'].selection).get(self.specialization, '')
        name = _('Subcontract — %s (%s)') % (self.name, spec_label) if spec_label else _('Subcontract — %s') % self.name
        services_cat = self.env.ref('atmta_procurement_core.cat_re_services', raise_if_not_found=False)
        product = self.env['product.product'].create({
            'name': name,
            'type': 'service',
            'purchase_ok': True,
            'sale_ok': False,
            'categ_id': services_cat.id if services_cat else self.env.ref('product.product_category_all').id,
        })
        self.service_product_id = product.id
        return product

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.partner_id.id)],
            'context': {'default_partner_id': self.partner_id.id},
        }

    def action_view_bills(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Bills'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', '=', self.partner_id.id),
                ('move_type', '=', 'in_invoice'),
            ],
        }

    def action_create_subcontract_po(self):
        """Open the subcontract PO wizard pre-filled with this contractor."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Subcontract PO'),
            'res_model': 'realestate.subcontract.po.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contractor_id': self.id},
        }

    @api.depends('payment_certificate_ids.state',
                 'payment_certificate_ids.gross_amount',
                 'payment_certificate_ids.retention_amount')
    def _compute_retention_totals(self):
        """Held is what the register says, not a re-derivation.

        The old computation summed retention off the certificates and zeroed
        it on a boolean, so a partial release could not be represented at all
        and a released amount kept showing as held until somebody flipped the
        flag. M7 reads the movements.
        """
        Retention = self.env['realestate.construction.retention']
        for rec in self:
            paid_or_billed = rec.payment_certificate_ids.filtered(
                lambda c: c.state in ('certified', 'invoiced', 'paid')
            )
            rec.total_certified = sum(paid_or_billed.mapped('gross_amount'))
            groups = Retention._read_group(
                [('contractor_id', '=', rec.id), ('side', '=', 'contractor')],
                aggregates=['signed_amount:sum'])
            registered = (groups[0][0] if groups else 0.0) or 0.0
            legacy = sum(paid_or_billed.filtered(
                lambda c: not c.retention_posted_correctly).mapped(
                    'retention_amount'))
            rec.total_retention_held = registered + (
                0.0 if rec.retention_released else legacy)

    def action_release_retention(self):
        """Open a retention release for this contractor.

        Releasing used to post a bill for everything held everywhere and flip
        a boolean. Retention is held per project and released in stages, so
        the button now opens the document that says which stage, on which
        project, for how much — and that document checks the register.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Retention Release'),
            'res_model': 'realestate.construction.retention.release',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contractor_id': self.id,
                        'default_side': 'contractor'},
        }

    def _action_release_retention_legacy(self):
        """The pre-M7 blanket release. Kept only so the migration can
        describe what it replaced; nothing calls it."""
        for rec in self:
            if rec.retention_released:
                raise UserError(_("Retention has already been released for '%s'.") % rec.name)
            if rec.total_retention_held <= 0:
                raise UserError(_("Nothing to release — held retention is zero."))
            bill = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'partner_id': rec.partner_id.id,
                'invoice_date': fields.Date.context_today(rec),
                'ref': _('Retention release — %s') % rec.name,
                'invoice_line_ids': [(0, 0, {
                    'name': _('Retention release for %s (across %d certificates)') % (
                        rec.name,
                        len(rec.payment_certificate_ids.filtered(
                            lambda c: c.state in ('certified', 'invoiced', 'paid')
                        )),
                    ),
                    'quantity': 1,
                    'price_unit': rec.total_retention_held,
                })],
            })
            rec.retention_released = True
            # Post so the retention payable lands on the ledger.
            self.env['realestate.account.tools'].post_moves(bill)
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'view_mode': 'form',
                'res_id': bill.id,
            }
