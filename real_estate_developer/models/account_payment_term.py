from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Payment-term lines are day-based; map a recurrence to an approximate day step.
_INTERVAL_DAYS = {'monthly': 30, 'quarterly': 90, 'semiannual': 180, 'annual': 365}


class AccountPaymentTerm(models.Model):
    """Real-estate friendly, fully dynamic generator on top of Odoo payment
    terms. Define the plan as any sequence of recurring *segments* — each a
    block of N installments at a given %/interval, anchored to the plan start
    or chained after the previous block — then expand them into payment-term
    lines that always total 100%."""
    _inherit = 'account.payment.term'

    re_is_realestate = fields.Boolean(string='Real Estate Plan', default=False)
    re_segment_ids = fields.One2many(
        'realestate.payment.term.segment', 'payment_term_id', string='Schedule Segments',
        help="Sequence of recurring blocks. 'Generate Schedule' expands them "
             "into payment-term lines summing to 100%.")
    re_total_pct = fields.Float(string='Fixed % in Segments', compute='_compute_re_total_pct')

    # ---- Discount ----
    re_discount_enabled = fields.Boolean(string='Apply Discount')
    re_discount_type = fields.Selection(
        [('percent', '% of unit price'), ('fixed', 'Fixed amount')],
        string='Discount Type', default='percent',
    )
    re_discount_value = fields.Float(string='Discount Value')
    re_discount_label = fields.Char(string='Discount Label', default='Promotional Discount')

    # ---- Maintenance ----
    re_maintenance_enabled = fields.Boolean(string='Include Maintenance')
    re_maintenance_product_id = fields.Many2one(
        'product.product', string='Maintenance Product',
        domain="[('type', '=', 'service')]",
        help="Service product used as the maintenance line. If empty, a generic "
             "Real Estate Maintenance product is created on demand.",
    )
    re_maintenance_amount_type = fields.Selection(
        [('fixed', 'Fixed amount per period'),
         ('percent', '% of unit price per period')],
        string='Maintenance Amount', default='fixed',
    )
    re_maintenance_amount = fields.Float(string='Fixed Amount / period')
    re_maintenance_pct = fields.Float(string='% / period')
    re_maintenance_period = fields.Selection(
        [('annual', 'Annual'), ('monthly', 'Monthly'), ('one_time', 'One-time')],
        string='Period', default='annual',
    )
    re_maintenance_periods = fields.Integer(string='Periods', default=1,
        help="How many periods to bill up-front (e.g. 3 = 3 years of annual maintenance).")

    @api.depends('re_segment_ids.value_pct', 're_segment_ids.occurrences', 're_segment_ids.pct_mode')
    def _compute_re_total_pct(self):
        for term in self:
            term.re_total_pct = sum(
                (s.value_pct or 0.0) * max(s.occurrences or 1, 1)
                for s in term.re_segment_ids if s.pct_mode == 'percent')

    # ------------------------------------------------------------------
    # Maintenance / discount extras applied to a sale order
    # ------------------------------------------------------------------
    def _re_maintenance_product(self):
        """Return the maintenance service product, creating a default one on
        first use so admins don't have to set up a product before they can
        configure maintenance."""
        self.ensure_one()
        if self.re_maintenance_product_id:
            return self.re_maintenance_product_id
        Product = self.env['product.product']
        prod = Product.search([
            ('default_code', '=', 'RE-MAINT'),
        ], limit=1)
        if not prod:
            prod = Product.create({
                'name': 'Real Estate Maintenance',
                'default_code': 'RE-MAINT',
                'type': 'service',
                'invoice_policy': 'order',
                'sale_ok': True,
                'purchase_ok': False,
            })
        return prod

    def re_apply_extras_to_order(self, sale_order, base_amount):
        """Append discount + maintenance lines to a sale order whose main
        line is `base_amount`. Returns the new lines created.

        Discount → single negative line on the order.
        Maintenance → one line per period (count = re_maintenance_periods).
        The payment term then splits the *total* across its segment lines.
        """
        self.ensure_one()
        if not sale_order:
            return self.env['sale.order.line']
        SaleLine = self.env['sale.order.line']
        created = SaleLine
        # Discount
        if self.re_discount_enabled:
            amt = (base_amount * (self.re_discount_value or 0.0) / 100.0
                   if self.re_discount_type == 'percent'
                   else (self.re_discount_value or 0.0))
            if amt > 0:
                created |= SaleLine.create({
                    'order_id': sale_order.id,
                    'name': self.re_discount_label or _('Discount'),
                    'product_uom_qty': 1,
                    'price_unit': -amt,
                    'product_id': self._re_maintenance_product().id,
                    # use the same generic product so we don't need a separate
                    # "discount" product; the negative price + label tell the story.
                })
        # Maintenance
        if self.re_maintenance_enabled:
            per = (base_amount * (self.re_maintenance_pct or 0.0) / 100.0
                   if self.re_maintenance_amount_type == 'percent'
                   else (self.re_maintenance_amount or 0.0))
            count = max(self.re_maintenance_periods or 1, 1)
            if self.re_maintenance_period == 'one_time':
                count = 1
            product = self._re_maintenance_product()
            period_label = dict(self._fields['re_maintenance_period'].selection)[
                self.re_maintenance_period]
            for i in range(count):
                if per > 0:
                    created |= SaleLine.create({
                        'order_id': sale_order.id,
                        'product_id': product.id,
                        'name': _('Maintenance — %s #%s') % (period_label, i + 1),
                        'product_uom_qty': 1,
                        'price_unit': per,
                    })
        return created

    def action_re_generate_lines(self):
        """Expand the segments into payment-term percent lines (total = 100%)."""
        for term in self:
            segs = term.re_segment_ids.sorted('sequence')
            if not segs:
                raise UserError(_("Add at least one schedule segment first."))

            fixed_total = sum((s.value_pct or 0.0) * max(s.occurrences or 1, 1)
                              for s in segs if s.pct_mode == 'percent')
            remainder_total = round(100.0 - fixed_total, 6)
            n_remainder = sum(max(s.occurrences or 1, 1)
                              for s in segs if s.pct_mode == 'remainder')
            if remainder_total < -0.0001:
                raise UserError(_("Segment percentages exceed 100%%."))
            if remainder_total > 0.0001 and n_remainder == 0:
                raise UserError(_(
                    "Percentages add up to %.2f%%. Add a 'Remainder' segment for "
                    "the leftover %.2f%%, or adjust the values.") % (fixed_total, remainder_total))
            remainder_each = round(remainder_total / n_remainder, 6) if n_remainder else 0.0

            line_vals = []
            running_day = 0
            total = 0.0
            first = True
            for seg in segs:
                count = max(seg.occurrences or 1, 1)
                step = _INTERVAL_DAYS.get(seg.interval, 30)
                off = (seg.offset_value or 0) * (30 if seg.offset_unit == 'months' else 1)
                if first or seg.anchor == 'start':
                    start_day = off
                else:
                    start_day = running_day + (off or step)
                per = remainder_each if seg.pct_mode == 'remainder' else (seg.value_pct or 0.0)
                for i in range(count):
                    day = start_day + step * i
                    line_vals.append({
                        'value': 'percent', 'value_amount': round(per, 6),
                        'delay_type': 'days_after', 'nb_days': day,
                    })
                    total += per
                    running_day = day
                first = False

            # Absorb rounding on the last line so the total is exactly 100%.
            if line_vals:
                line_vals[-1]['value_amount'] = round(
                    line_vals[-1]['value_amount'] + (100.0 - total), 6)
            term.line_ids = [(5, 0, 0)] + [(0, 0, v) for v in line_vals]
            term.re_is_realestate = True
        return True


class PaymentTermSegment(models.Model):
    _name = 'realestate.payment.term.segment'
    _description = 'Real Estate Payment Schedule Segment'
    _order = 'sequence, id'

    payment_term_id = fields.Many2one('account.payment.term', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Label', default='Segment')
    pct_mode = fields.Selection([
        ('percent', '% per installment'),
        ('remainder', 'Remainder (to 100%)'),
    ], string='Amount', default='percent', required=True)
    value_pct = fields.Float(string='% each', help="Percent for each installment in this segment.")
    occurrences = fields.Integer(string='Count', default=1, required=True,
                                 help="Number of installments in this segment.")
    interval = fields.Selection([
        ('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
        ('semiannual', 'Semi-annual'), ('annual', 'Annual'),
    ], string='Interval', default='monthly',
        help="Spacing between installments in this segment. Ignored when Count is 1.")
    anchor = fields.Selection([
        ('start', 'From plan start'),
        ('prev', 'After previous segment'),
    ], string='Starts', default='prev', required=True)
    offset_value = fields.Integer(string='Offset')
    offset_unit = fields.Selection([('days', 'Days'), ('months', 'Months')],
                                   string='Offset Unit', default='months')
