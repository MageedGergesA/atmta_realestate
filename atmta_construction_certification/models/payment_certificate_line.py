from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PaymentCertificateLine(models.Model):
    """Per-BOQ-line certification detail on a payment certificate.

    Optional. Adds `certificate.line_ids` — when set, gross_amount is derived
    from Σ(line.qty × line.rate) rather than from the top-level
    `current_certified_pct`. This is the quantity-based mode used for real
    contractor billing (matches KamahTech's BOQ-driven certification).
    """
    _name = 'realestate.construction.payment.certificate.line'
    _description = 'Payment Certificate Line (BOQ Detail)'
    _order = 'certificate_id, sequence, id'

    certificate_id = fields.Many2one(
        'realestate.construction.payment.certificate',
        required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    boq_line_id = fields.Many2one(
        'realestate.boq.line', string='BOQ Line', required=True, ondelete='restrict',
    )
    work_item_id = fields.Many2one(
        related='boq_line_id.work_item_id', store=True, readonly=True,
    )
    description = fields.Char(help='Overrides work item name on the vendor bill.')
    qty = fields.Float(string='Qty Certified', required=True)
    unit_rate = fields.Monetary(
        string='Rate', required=True,
        compute='_compute_rate', store=True, readonly=False,
    )
    amount = fields.Monetary(
        string='Amount', compute='_compute_amount', store=True,
    )
    remaining_qty = fields.Float(
        string='Remaining Qty on BOQ Line',
        related='boq_line_id.remaining_qty', readonly=True,
    )
    currency_id = fields.Many2one(
        related='certificate_id.currency_id', store=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='certificate_id.company_id', store=True, readonly=True,
        index=True,
    )

    @api.depends('boq_line_id')
    def _compute_rate(self):
        for ln in self:
            if ln.boq_line_id and not ln.unit_rate:
                ln.unit_rate = ln.boq_line_id.unit_rate

    @api.depends('qty', 'unit_rate')
    def _compute_amount(self):
        for ln in self:
            ln.amount = ln.qty * ln.unit_rate

    @api.constrains('qty')
    def _check_qty(self):
        for ln in self:
            if ln.qty < 0:
                raise ValidationError(_("Certified quantity cannot be negative."))
            # Warn (block) if over-certifying — user must adjust the BOQ first.
            if ln.qty > ln.boq_line_id.remaining_qty + ln.qty * 0:
                # remaining_qty is post-computed excluding this line; be lenient
                # if editing an existing certification line.
                if ln.qty > ln.boq_line_id.quantity - ln.boq_line_id.certified_qty + \
                            sum(l.qty for l in ln.boq_line_id.certification_line_ids if l.id == ln.id):
                    raise ValidationError(_(
                        "Cannot certify %(q)s on '%(w)s' — only %(r)s remaining on the BOQ.",
                        q=ln.qty, w=ln.work_item_id.display_name,
                        r=ln.boq_line_id.remaining_qty,
                    ))
