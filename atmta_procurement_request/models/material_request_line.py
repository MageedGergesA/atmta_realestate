from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError



class MaterialRequestLine(models.Model):
    _name = 'realestate.material.request.line'
    _description = 'Real Estate Material Request Line'
    _order = 'request_id, id'

    request_id = fields.Many2one(
        'realestate.material.request', required=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='Product',
        domain="['|', '|', '|', '|', "
               "('is_construction_material', '=', True), "
               "('is_property_fitting', '=', True), "
               "('is_marketing_asset', '=', True), "
               "('is_maintenance_consumable', '=', True), "
               "('type', '=', 'service')]",
    )
    description = fields.Char(
        string='Description',
        help="Required when no product is given — a service, a subcontract "
             "scope or a conceptual line has no catalogue entry.")
    estimated_unit_cost = fields.Monetary(
        help="The requester's or buyer's estimate, tax-exclusive. Used for "
             "control comparison and for the approval basis; it is not a "
             "price anybody has quoted.")
    qty = fields.Float(string='Quantity', required=True, default=1.0)
    uom_id = fields.Many2one(
        'uom.uom', string='UoM',
        compute='_compute_uom', store=True, readonly=False,
        help="Required with a product, because Odoo's own quantity semantics "
             "are. A lump-sum subcontract scope has no unit, and inventing "
             "one for it would put a fictional quantity on the enquiry.")
    unit_price_hint = fields.Float(
        string='Last Price',
        compute='_compute_price_hint',
        help='Latest seller price for hint only; the PO carries the binding price.',
    )
    estimated_cost = fields.Monetary(
        string='Estimated', compute='_compute_estimated_cost', store=True,
    )
    currency_id = fields.Many2one(
        related='request_id.currency_id', store=True, readonly=True,
    )

    # ------------------------------------------------------------------
    # Construction coding — M2.
    #
    # WBS says *where in the works*, cost code says *what kind of money*.
    # They are separate questions and Construction owns both models; nothing
    # here re-implements them. The line is authoritative rather than the
    # header, because one requisition routinely spans several codes and
    # stamping a header code onto every line is how uncoded money reaches a
    # cost report wearing somebody else's classification.
    # ------------------------------------------------------------------
    project_id = fields.Many2one(
        related='request_id.project_id', store=True, readonly=True,
        index=True)
    company_id = fields.Many2one(
        related='request_id.company_id', store=True, readonly=True,
        index=True)
    # The WBS and cost-code fields themselves are added by
    # `real_estate_construction`, which owns both models. Construction depends
    # on Procurement, so the foreign keys cannot point the other way — see
    # `_prepare_rfq_line()` for the hook that carries them onto the purchase
    # order line.

    required_on_site_date = fields.Date(
        help="When the works need it on site. Deliberately not the RFQ "
             "deadline, not the order date and not the vendor's promised "
             "arrival — those are different dates and conflating them is how "
             "a late delivery looks on time.")

    po_line_ids = fields.One2many(
        'purchase.order.line', 're_material_request_line_id',
        string='Purchase Lines', readonly=True,
        help="One requisition line may be enquired from several vendors and "
             "eventually split across several orders. The relation is "
             "one-to-many from the outset so that a future split award does "
             "not need a migration.")
    po_line_count = fields.Integer(compute='_compute_po_lines', store=True)
    ordered_qty = fields.Float(compute='_compute_po_lines', store=True,
                               help="Quantity on confirmed orders.")
    remaining_qty = fields.Float(compute='_compute_po_lines', store=True,
                                 help="Requested minus ordered.")

    po_line_id = fields.Many2one(
        'purchase.order.line', string='PO Line', readonly=True, copy=False,
        help="DEPRECATED (M2) — the first purchase line raised from this "
             "requisition line. Kept so existing records and reports keep "
             "working; `po_line_ids` is authoritative.",
    )
    received_qty = fields.Float(
        string='Received', compute='_compute_received', store=True,
    )

    # -- Technical detail. A buyer cannot source what nobody described.
    substitution_allowed = fields.Boolean(
        string='Substitution Allowed', default=False,
        help="Whether an equivalent may be offered. False means the vendor "
             "must quote what is asked for — and a buyer who substitutes "
             "anyway is making an engineering decision.")
    preferred_brand = fields.Char(
        help="A preference, and only that. Naming a brand does not name a "
             "vendor and does not award anything.")
    specification_ref = fields.Char(
        string='Specification',
        help="The specification clause, drawing or datasheet the line is "
             "bought against. A reference, not a copy.")
    notes = fields.Char()

    def _control_cost_code_id(self):
        self.ensure_one()
        return self.cost_code_id.id if 'cost_code_id' in self._fields else False

    def _control_wbs_id(self):
        self.ensure_one()
        return self.wbs_id.id if 'wbs_id' in self._fields else False

    def _control_amount(self, date=None):
        """This line's tax-exclusive amount, in company currency.

        Rule 3, and the reason the estimate rather than a tax-inclusive figure
        is used: Construction's budget and commitment are both tax-exclusive,
        so a reservation that included VAT would overstate every position by
        the tax rate and would do it invisibly.

        Rule V: converted at a documented rate on a documented date, because a
        USD request measured against an EGP budget is not a measurement.
        """
        self.ensure_one()
        request = self.request_id
        company = request.company_id or self.env.company
        source = request.currency_id or company.currency_id
        amount = self.estimated_cost or 0.0
        if source == company.currency_id:
            return amount
        return source._convert(amount, company.currency_id, company,
                               date or fields.Date.context_today(self))

    def unlink(self):
        """Demand that is holding capacity cannot be deleted out from under it.

        Whether anything is held is a control question, so the check is a
        seam. With no control installed nothing is held and nothing blocks.
        """
        self._check_unlink_allowed()
        return super().unlink()

    # ------------------------------------------------------------------
    #: Same reasoning as on the request: after submission these describe what
    #: was approved. `wbs_id` and `cost_code_id` are named here even though
    #: Construction owns them — a set that only guards the fields this module
    #: happens to define would guard the wrong half.
    _APPROVED_BASIS_FIELDS = {
        'qty', 'uom_id', 'product_id', 'description', 'estimated_unit_cost',
        'required_on_site_date', 'wbs_id', 'cost_code_id',
    }

    def write(self, vals):
        touched = self._APPROVED_BASIS_FIELDS & set(vals)
        if touched:
            self.mapped('request_id')._check_basis_is_still_open(
                {field: vals[field] for field in touched},
                self._APPROVED_BASIS_FIELDS)
        return super().write(vals)

    @api.depends('product_id')
    def _compute_uom(self):
        for ln in self:
            if ln.product_id and not ln.uom_id:
                ln.uom_id = ln.product_id.uom_id

    @api.depends('product_id', 'qty')
    def _compute_price_hint(self):
        for ln in self:
            if not ln.product_id:
                ln.unit_price_hint = 0.0
                continue
            seller = ln.product_id.seller_ids[:1]
            ln.unit_price_hint = seller.price if seller else ln.product_id.standard_price

    @api.depends('unit_price_hint', 'qty', 'estimated_unit_cost')
    def _compute_estimated_cost(self):
        """A stated estimate wins over a catalogue hint.

        Phase 0 found this silently falling through to `standard_price` and
        then to zero — and a zero estimate cleared every approval bracket.
        `estimate_is_known` now says which of those happened.
        """
        for ln in self:
            unit = ln.estimated_unit_cost or ln.unit_price_hint or 0.0
            ln.estimated_cost = unit * (ln.qty or 0.0)
            ln.estimate_is_known = bool(ln.estimated_unit_cost
                                        or ln.unit_price_hint)

    estimate_is_known = fields.Boolean(
        compute='_compute_estimated_cost', store=True,
        help="False when neither an estimate nor a catalogue price exists. "
             "The amount is then unknown, not zero — an approval matrix must "
             "read this rather than trusting a zero.")

    @api.depends('po_line_ids.qty_received')
    def _compute_received(self):
        """Read what Inventory says. Nothing is written from here.

        This used to call `request_id._refresh_state_from_lines()` from inside
        the compute, so the request's state depended on when the cache
        happened to be invalidated. The rollup now runs from the request's own
        stored compute instead.
        """
        for ln in self:
            ln.received_qty = sum(ln.po_line_ids.mapped('qty_received'))

    @api.depends('po_line_ids.product_qty', 'po_line_ids.state', 'qty')
    def _compute_po_lines(self):
        for ln in self:
            ln.po_line_count = len(ln.po_line_ids)
            ordered = ln.po_line_ids.filtered(
                lambda pol: pol.state in ('purchase', 'done'))
            ln.ordered_qty = sum(ordered.mapped('product_qty'))
            ln.remaining_qty = max(0.0, (ln.qty or 0.0) - ln.ordered_qty)



    @api.constrains('product_id', 'description')
    def _check_something_is_being_asked_for(self):
        for ln in self:
            if not ln.product_id and not ln.description:
                raise ValidationError(_(
                    "A requisition line needs either a product or a written "
                    "scope. A line that says nothing cannot be sourced."))

    @api.constrains('product_id', 'uom_id')
    def _check_uom_where_a_product_demands_one(self):
        for ln in self:
            if ln.product_id and not ln.uom_id:
                raise ValidationError(_(
                    "%s is a catalogue product, so the line needs a unit of "
                    "measure.") % ln.product_id.display_name)

    @api.constrains('qty')
    def _check_qty(self):
        for ln in self:
            if ln.qty <= 0:
                raise ValidationError(_("Quantity must be greater than zero."))

    def _unit_price(self):
        """A starting price for an enquiry, not a negotiated one.

        An RFQ carries a price so the vendor has something to respond to. What
        comes back is the vendor's number, and that is the one that matters.
        """
        self.ensure_one()
        if self.estimated_unit_cost:
            return self.estimated_unit_cost
        seller = self.product_id.seller_ids[:1]
        return seller.price if seller else self.product_id.standard_price

    # ------------------------------------------------------------------
    # Control seam — Wave 6 / AD-008
    # ------------------------------------------------------------------
    def _check_unlink_allowed(self):
        """Refuse deletion of demand that is holding capacity.

        Nothing holds capacity without `atmta_procurement_control`, so the
        Request-only answer is that every line may go.
        """
        return True
