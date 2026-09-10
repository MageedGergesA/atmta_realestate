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
    retention_released = fields.Boolean(
        string='Retention Released', default=False, copy=False, readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

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

    # ----- What this module deliberately does not know -----
    # Milestones, payment certificates and the retention register are
    # `real_estate_construction` models. A contractor here is identity plus
    # what the purchase ledger already says about it. What has been certified,
    # and what is still held, are facts those models own; that module adds
    # them back onto this one.
