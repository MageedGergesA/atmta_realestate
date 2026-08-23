"""What a requisition looks like once somebody can go to market with it.

`atmta_procurement_request` owns demand: what was asked for, by whom, against
which project. Deciding which vendors may be enquired with, which trade a line
belongs to, and turning authorised demand into requests for quotation is
solicitation — and solicitation is this module.

Keeping it here is what lets a site capture and approve demand without vendor
governance or a tender process installed at all. Every method below was moved
from the Request module unchanged; only its address changed.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class MaterialRequestSourcing(models.Model):
    _inherit = 'realestate.material.request'


    # ---------- Sourcing ----------
    def action_create_rfqs(self, vendors=None):
        """Prepare a **draft** request for quotation per named vendor.

        This is the change M2 exists to make. The method it replaces created
        purchase orders and called `button_confirm()` on them, so approving a
        requisition and clicking once produced a confirmed order — and a
        Construction commitment — with no enquiry, no comparison and no award
        in between.

        What happens now:

        * vendors must be named. Nothing is chosen from `seller_ids[0]`,
          because whichever supplier row sorts first is not a sourcing
          decision;
        * one draft RFQ is created per vendor, carrying every line;
        * every line carries project, WBS and cost code, so the eventual
          commitment lands in the cost report's own dimension;
        * nothing is confirmed. Confirming is M7's decision to govern.
        """
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_procurement.group_procurement_user'):
            raise AccessError(_(
                "Sourcing is a buyer's job. Raising the requisition and "
                "choosing who is asked to quote for it are deliberately not "
                "the same right."))
        if self.state not in ('approved', 'sourcing', 'partially_ordered'):
            raise UserError(_(
                "Approve %s before sourcing it.") % self.name)
        if not self.line_ids:
            raise UserError(_("There is nothing to source."))

        vendors = vendors or self.env['res.partner']
        if not vendors:
            raise UserError(_(
                "Name the vendors to enquire with. Procurement no longer "
                "picks whichever supplier happens to be listed first — that "
                "was a purchase decision nobody made.%(hint)s",
                hint=(_("\n\nSuggested from the product catalogue: %s")
                      % ', '.join(sorted(set(
                          self.line_ids.product_id.seller_ids.mapped(
                              'partner_id.display_name')))))
                if self.line_ids.product_id.seller_ids else ''))

        self._check_vendors_may_be_invited(vendors)

        created = self.env['purchase.order']
        for vendor in vendors:
            created |= self._create_rfq_for_vendor(vendor)

        if self.state == 'approved':
            self.state = 'sourcing'
        # A partially ordered requisition stays partially ordered while its
        # remainder is enquired about: some of it is committed and the rest is
        # not, and moving the whole thing back to 'sourcing' would lose that.
        self.message_post(body=_(
            "%(count)s request(s) for quotation prepared: %(vendors)s. "
            "Nothing is committed until an order is confirmed.",
            count=len(created),
            vendors=', '.join(created.mapped('partner_id.display_name'))))
        return created

    def _check_vendors_may_be_invited(self, vendors):
        """M4K at the invitation moment — the earliest point worth gating.

        An RFQ commits nothing, so this is not about money. It is about not
        starting a conversation the company has decided it will not finish:
        under REQUIRED TO BE INVITED, asking an unqualified vendor to price
        the work creates an expectation somebody then has to withdraw.

        Under OPTIONAL and WARN nothing is refused. Under WARN the gap is
        posted to the requisition, because a warning nobody can find later is
        not a warning.
        """
        self.ensure_one()
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        trades = list(self.line_ids.mapped('vendor_category_id'))
        # Counted rather than tested for truthiness: `mapped()` on a
        # many2one silently drops the empty ones, so `all(...)` over the
        # result is true whenever *any* line carries a trade and would never
        # notice the mixed case this exists for.
        if len(self.line_ids.filtered('vendor_category_id')) < len(
                self.line_ids):
            # A requisition mixing classified and unclassified lines is asked
            # both questions. Checking only the trades that happen to be
            # named would let a vendor through for the scope nobody classified.
            trades.append(None)
        if not trades:
            trades = [None]
        date = fields.Date.context_today(self)
        refusals, warnings = [], []
        for vendor in vendors:
            for trade in trades:
                outcome = Eligibility.check_vendor_eligibility(
                    vendor, company=self.company_id, category=trade,
                    project=self.project_id, date=date, purpose='sourcing')
                if not outcome['eligible']:
                    refusals += outcome['blocking_reasons']
                elif outcome['blocking_reasons'] or outcome['status'] not in (
                        'eligible', 'eligible_with_conditions'):
                    warnings.append('%s — %s' % (
                        vendor.display_name,
                        '; '.join(outcome['warnings']) or outcome['status']))
        if refusals:
            raise UserError(_(
                "These vendors cannot be invited to quote:\n\n%(reasons)s\n\n"
                "Qualify them, lift the restriction, or change the vendor "
                "policy on %(scope)s.",
                reasons='\n'.join('• %s' % reason
                                  for reason in dict.fromkeys(refusals)),
                scope=self.project_id.display_name
                or self.company_id.display_name))
        if warnings:
            Eligibility.post_governance_note(self, _(
                "Invited with governance gaps recorded:\n%s")
                % '\n'.join('• %s' % warning
                            for warning in dict.fromkeys(warnings)))

    def _sourcing_pool(self, date=None, purpose='sourcing'):
        """Every suggested vendor across the requisition, with its status."""
        self.ensure_one()
        pool = {}
        for line in self.line_ids:
            for outcome in line._sourcing_pool(date=date, purpose=purpose):
                key = (outcome['partner_id'], outcome['category_id'])
                pool.setdefault(key, outcome)
        return sorted(pool.values(),
                      key=lambda r: (not r['eligible'], r['partner_name']))

    def _create_rfq_for_vendor(self, vendor):
        """One draft purchase order, fully coded, for one vendor."""
        self.ensure_one()
        order_lines = []
        for line in self.line_ids:
            order_lines.append((0, 0, self._prepare_rfq_line(line)))
        order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'company_id': self.company_id.id,
            'date_order': fields.Datetime.now(),
            're_project_id': self.project_id.id or False,
            're_source_model': 'realestate.material.request',
            're_source_id': self.id,
            'order_line': order_lines,
        })
        return order

class MaterialRequestLineSourcing(models.Model):
    _inherit = 'realestate.material.request.line'


    # -- M4L — which trade this line is sourced from -------------------
    vendor_category_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Vendor Trade',
        compute='_compute_vendor_category', store=True, readonly=False,
        index=True, ondelete='restrict',
        help="The trade a vendor has to be qualified for to quote this line. "
             "Proposed from the product's category where exactly one trade "
             "claims it, and freely editable — the product tree describes "
             "what the item is, not what a supplier is capable of.")

    @api.depends('product_id')
    def _compute_vendor_category(self):
        Category = self.env['realestate.procurement.vendor.category']
        for line in self:
            if line.vendor_category_id:
                continue          # a buyer's choice is not overwritten
            line.vendor_category_id = Category.suggest_for_product(
                line.product_id)

    def _suggested_suppliers(self):
        """Vendors worth asking — a suggestion, never a decision.

        This replaces `_get_preferred_supplier()`, which returned
        `seller_ids[0]` and was used as the awarded vendor. Whichever supplier
        row sorted first won the order regardless of price, and nothing
        recorded why. Choosing a vendor is a sourcing decision; M5/M6 own it.

        M4 leaves the *membership* of this list exactly as it was — it is the
        catalogue's answer, and filtering ineligible vendors out here would
        make "why wasn't Vendor X suggested?" unanswerable. Eligibility is
        added alongside it by `_sourcing_pool()`.
        """
        self.ensure_one()
        return self.product_id.seller_ids.mapped('partner_id')

    def _sourcing_pool(self, date=None, purpose='sourcing'):
        """M4W — the same suggestions, each with its governance answer.

        ```
            Vendor A   eligible
            Vendor B   qualification expires before the required date
            Vendor C   no qualification for this trade
            Vendor D   suspended
        ```

        Four rows, not one. A screen that showed only Vendor A would be
        making the sourcing decision, which is the exact defect M2 removed
        from this line and M4 is not putting back. Ranking is absent for the
        same reason: qualified is not cheapest, and M6 owns comparison.
        """
        self.ensure_one()
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        return Eligibility.get_eligible_vendors(
            company=self.company_id or self.env.company,
            category=self.vendor_category_id,
            project=self.request_id.project_id,
            date=date or self.required_on_site_date,
            purpose=purpose,
            partners=self._suggested_suppliers(),
        )
