import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.atmta_procurement_core.models.procurement_policy import (
    RECEIPT_INSPECTION_POLICY,
)

_logger = logging.getLogger(__name__)

#: Purchase states in which an order is a commitment as far as Construction is
#: concerned. Kept here so the gate and the conversion agree about when money
#: starts being owed.
COMMITTED_STATES = ('purchase', 'done')


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    re_project_id = fields.Many2one(
        'realestate.project', string='Real Estate Project',
        help='Project this PO is allocated to. Drives committed/budget rollups.',
    )
    re_source_model = fields.Selection([
        ('realestate.material.request', 'Material Request'),
        ('realestate.contractor', 'Subcontract'),
        ('realestate.listing', 'Listing'),
        ('realestate.project', 'Project (Direct)'),
    ], string='RE Source Type')
    re_source_id = fields.Integer(string='RE Source ID', index=True, help='Polymorphic ID into the source record.')

    # -- M5 sourcing ---------------------------------------------------
    re_sourcing_event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', string='Sourcing Event',
        index=True, copy=False, ondelete='set null',
        help="The tender this RFQ answers. Its presence is what makes the "
             "confirmation gate apply.")
    re_sourcing_invitation_id = fields.Many2one(
        'realestate.procurement.sourcing.invitation', string='Invitation',
        index=True, copy=False, ondelete='set null')
    re_sourcing_class = fields.Selection([
        ('standalone_rfq', 'Standalone RFQ'),
        ('native_alternative_group', 'Native Alternative Group'),
        ('legacy_direct_po', 'Legacy Direct Purchase Order'),
        ('open_approved_demand_with_rfq', 'Open Approved Demand with RFQ'),
        ('purchase_agreement_native', 'Native Purchase Agreement'),
        ('atmta_tender', 'ATMTA Sourcing Event'),
        ('ambiguous', 'Ambiguous'),
    ], string='Sourcing Classification', index=True, copy=False,
        help="What M5's migration made of this order. A description of what "
             "is already there — never a claim that a formal tender process "
             "happened.")

    @api.model
    def _classify_sourcing_history(self, orders=None):
        """M5 migration — describe the purchase history, invent nothing.

        A legacy RFQ is a legacy RFQ. It has no tender, no issued version, no
        invitation, no eligibility snapshot at invitation, no deadline and no
        bid receipt time, and writing those into the register so the module
        looked "adopted" would fabricate a competitive process that never
        took place. So every branch here only reads.
        """
        Order = orders if orders is not None else self.search([])
        counts = {}
        alternatives = 'purchase_group_id' in self._fields
        agreements = 'requisition_id' in self._fields
        for order in Order:
            if order.re_sourcing_event_id:
                label = 'atmta_tender'
            elif agreements and order.requisition_id:
                label = 'purchase_agreement_native'
            elif alternatives and order.purchase_group_id:
                label = 'native_alternative_group'
            elif order.state in ('purchase', 'done'):
                label = 'legacy_direct_po'
            elif order.state in ('draft', 'sent'):
                requisitions = order.order_line.re_material_request_id
                approved = requisitions.filtered(
                    lambda r: r.state in ('approved', 'sourcing',
                                          'partially_ordered'))
                label = ('open_approved_demand_with_rfq' if approved
                         else 'standalone_rfq')
            else:
                label = 'ambiguous'
            if order.re_sourcing_class != label:
                order.re_sourcing_class = label
            counts[label] = counts.get(label, 0) + 1
        return counts
    re_source_display = fields.Char(string='RE Source', compute='_compute_re_source_display')
    is_realestate_po = fields.Boolean(
        compute='_compute_is_realestate_po', store=True,
        help='True when the PO is linked to any real-estate project or source.',
    )

    # -- M3K governance ------------------------------------------------
    re_requisition_ids = fields.Many2many(
        'realestate.material.request', string='Requisitions',
        compute='_compute_re_requisitions',
        help="Every requisition this order sources. Many, because one order "
             "may consolidate several requests — which is exactly why the "
             "authorisation check is per line and not per order.")
    re_governance = fields.Selection([
        ('optional', 'Optional'),
        ('controlled', 'Controlled'),
        ('required', 'Required'),
    ], compute='_compute_re_governance',
        string='Procurement Governance',
        help="The policy this order will be judged against when somebody "
             "confirms it — the project's, or the company's where the project "
             "does not override it.")
    re_exception_id = fields.Many2one(
        'realestate.procurement.control.exception',
        string='Purchase Exception', copy=False, ondelete='set null',
        help="The authorised reason this order may commit without a "
             "requisition behind it.")

    re_governance_status = fields.Selection([
        ('linked', 'From Requisition'),
        ('direct', 'Direct Purchase'),
        ('not_project', 'Not Project Coded'),
    ], compute='_compute_re_governance_status', store=True, index=True,
        string='Procurement Source',
        help="M3AA's LEGACY_DIRECT_PO classification, kept live rather than "
             "computed once at migration: a project order raised outside "
             "Procurement is worth being able to find at any time, not only "
             "on the day of an upgrade. Existing confirmed orders keep this "
             "label and nothing else — none of them was cancelled, reversed "
             "or given a reservation.")

    @api.depends('re_project_id', 'order_line.re_material_request_line_id')
    def _compute_re_governance_status(self):
        for rec in self:
            if not rec._is_project_coded():
                rec.re_governance_status = 'not_project'
            elif rec.order_line.re_material_request_line_id:
                rec.re_governance_status = 'linked'
            else:
                rec.re_governance_status = 'direct'

    @api.depends('re_project_id', 're_source_model', 're_source_id')
    def _compute_is_realestate_po(self):
        for rec in self:
            rec.is_realestate_po = bool(rec.re_project_id or (rec.re_source_model and rec.re_source_id))

    def _compute_re_requisitions(self):
        for rec in self:
            rec.re_requisition_ids = rec.order_line.re_material_request_id

    def _compute_re_governance(self):
        Control = self.env['realestate.procurement.control']
        for rec in self:
            rec.re_governance = Control.po_governance_for(
                rec.re_project_id, rec.company_id)

    # ------------------------------------------------------------------
    # The confirmation boundary — M3K
    # ------------------------------------------------------------------
    def button_confirm(self):
        """Confirming is what commits. Everything M3 controls happens here.

        This is the only moment in the whole chain where a Construction budget
        starts being consumed by an obligation, so it is the only place a gate
        is worth putting. It sits on the server side of the button on purpose:
        Phase 0's finding was not that the button was visible to the wrong
        people, it was that RPC, imports, scheduled actions and any other
        module calling `button_confirm()` all reached commitment without
        passing anything at all.

        Standard Odoo purchasing is deliberately left intact. Nothing here
        replaces `purchase.order`, changes how receipts are generated or
        touches vendor bills — an authorised order confirms exactly as it
        always did. What is added is the question asked immediately before.
        """
        gated = self.filtered(lambda order: order.state in ('draft', 'sent'))
        for order in gated:
            # M5 first: an RFQ that belongs to a live tender has no business
            # reaching any of the other checks, because the answer is no
            # whatever they say.
            order._check_tender_authorisation()
            order._check_procurement_governance()
            order._check_vendor_eligibility()
            order._revalidate_against_approved_basis()
        res = super().button_confirm()
        # After, not before: commitment exists once Odoo says the order is
        # confirmed, and the reservation must stop consuming capacity at the
        # same instant. Both happen in this transaction — if the confirmation
        # rolls back the conversion goes with it, and if the conversion fails
        # the confirmation does too. There is no correct half of this.
        for order in gated:
            order._convert_reservations()
        requests = self.order_line.re_material_request_id
        if requests:
            requests.invalidate_recordset()
            requests._refresh_state_after_ordering()
        return res

    def button_cancel(self):
        """Cancelling releases what confirming committed.

        Construction reads commitment from confirmed orders, so a cancelled
        order stops being one immediately. Without this the demand would sit
        in neither control stage: not reserved, not committed, and absent from
        availability while still needing to be bought.
        """
        Reservation = self.env['realestate.procurement.reservation']
        res = super().button_cancel()
        for order in self:
            Reservation._reverse_conversions(
                order.order_line, _("%s was cancelled.") % order.name)
        return res

    # ------------------------------------------------------------------
    # The tender boundary — M5
    # ------------------------------------------------------------------
    def _check_tender_authorisation(self):
        """A sourcing RFQ cannot become a commitment before an award exists.

        Deliberately independent of Odoo's own alternative-RFQ warning. That
        warning is Purchase UX: it *asks* whether to cancel the other
        quotations, it appears only while live alternatives exist, and it is
        switched off wholesale by `skip_alternative_check` in the context —
        which the native wizard itself sets when it confirms. A governance
        control that the same key disabled would be decoration, and a tender
        with one remaining vendor would have no control at all.

        The award authorisation this looks for is M7's and does not exist yet,
        so during M5 the answer is always no. The check is written as a lookup
        rather than a hard refusal so that M7 supplies the authorisation
        without this method changing shape.
        """
        self.ensure_one()
        invitation = self.re_sourcing_invitation_id
        event = self.re_sourcing_event_id
        if not event and not invitation:
            return True
        if event.state == 'cancelled':
            raise UserError(_(
                "%(order)s belongs to %(event)s, which was cancelled.",
                order=self.display_name, event=event.name))
        if self._award_authorisation():
            return True
        raise UserError(_(
            "%(order)s is %(vendor)s's response to %(event)s, and sourcing "
            "does not commit money.\n\n"
            "Confirming it would turn a quotation into a Construction "
            "commitment without anybody having decided who won. The award "
            "decision is a separate authorised act (M7); until it exists for "
            "this tender, no tender RFQ confirms — including through the "
            "native alternative-RFQ warning.",
            order=self.display_name,
            vendor=self.partner_id.display_name,
            event=event.name or ''))

    def _award_authorisation(self):
        """M7 fills the hook M5 cut. One approved award, naming this order.

        M5 wrote this as a lookup returning `False` so that M7 could supply
        the authorisation without the confirmation boundary changing shape,
        and that is exactly what happens here: the method still answers one
        question and `_check_tender_authorisation` above is untouched.

        What counts is an award **line** that names this order, on an award
        that has been approved by somebody other than whoever raised it. A
        draft award authorises nothing, a submitted one authorises nothing,
        and a cancelled one stops authorising the moment it is cancelled.

        Read through `sudo()` on purpose. Whether a purchase is authorised is
        a fact about the order, not a privilege of the person confirming it —
        a buyer with no access to the award register would otherwise get
        "no award exists" when one does, which is the wrong answer to a
        question about governance.
        """
        self.ensure_one()
        line = self.env['realestate.procurement.award.line'].sudo().search([
            ('purchase_order_id', '=', self.id),
            ('award_id.state', 'in', ('approved', 'issued')),
        ], limit=1)
        return bool(line)

    # ------------------------------------------------------------------
    def _is_project_coded(self):
        """Is this a purchase the project-control system is entitled to judge?

        Narrow on purpose. An office laptop bought by the same company through
        the same Purchase app is nobody's construction commitment, and gating
        it because the module happens to be installed would make procurement
        governance something people route around rather than use.
        """
        self.ensure_one()
        if self.re_project_id:
            return True
        if self.order_line.re_material_request_line_id:
            return True
        if 're_cost_code_id' in self.env['purchase.order.line']._fields:
            return bool(self.order_line.filtered('re_cost_code_id'))
        return False

    def _check_procurement_governance(self):
        """Refuse to commit a governed project purchase nobody authorised."""
        self.ensure_one()
        governance = self.re_governance
        if governance == 'optional' or not self._is_project_coded():
            return True

        unlinked = self.order_line.filtered(
            lambda line: not line.re_material_request_line_id
            and not line.display_type)
        requests = self.order_line.re_material_request_id
        unapproved = requests.filtered(
            lambda req: req.state not in ('approved', 'sourcing',
                                          'partially_ordered', 'ordered',
                                          'partial', 'received', 'done'))
        if unapproved:
            raise UserError(_(
                "%(order)s sources %(refs)s, which %(state)s. An order cannot "
                "commit a project's budget ahead of the approval that "
                "authorised the demand.",
                order=self.name, refs=', '.join(unapproved.mapped('name')),
                state=_("has not been approved") if len(unapproved) == 1
                else _("have not been approved")))

        if unlinked:
            exception = self._authorised_purchase_exception()
            if governance == 'required':
                raise UserError(_(
                    "%(order)s is coded to %(project)s, which requires every "
                    "project purchase to come from an approved requisition. "
                    "%(count)s line(s) have none.\n\nRaise a requisition, or "
                    "change the project's purchase governance if direct "
                    "buying is genuinely the policy here.",
                    order=self.name,
                    project=self.re_project_id.display_name or _('a project'),
                    count=len(unlinked)))
            if not exception:
                raise UserError(_(
                    "%(order)s is coded to %(project)s, which allows direct "
                    "purchase only with an authorised exception. %(count)s "
                    "line(s) have no requisition behind them.\n\nRequest a "
                    "direct-purchase exception; a procurement manager decides "
                    "it, and the decision stays on the record.",
                    order=self.name,
                    project=self.re_project_id.display_name or _('a project'),
                    count=len(unlinked)))

        self._check_coding_completeness()
        return True

    def _check_vendor_eligibility(self):
        """M4K at the award moment — the last line, and today the only one.

        Confirming is where a vendor stops being a candidate and starts being
        the supplier, so it is where REQUIRED TO RECEIVE THE ORDER bites.
        When M7 introduces a formal award the check moves to that decision
        and this one stays here as the backstop, because RPC, imports and
        other modules all reach `button_confirm()` and none of them passes
        through an award screen.

        Scope is `is_realestate_po` and nothing wider. Office stationery, IT
        subscriptions and every other purchase this suite has no opinion
        about confirm exactly as standard Odoo confirms them — M4 is not a
        licence to police the whole purchase journal.
        """
        self.ensure_one()
        if not self.is_realestate_po:
            return True
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        date = fields.Date.context_today(self)
        trades = self._order_vendor_categories()
        refusals, warnings = [], []
        for trade in trades:
            outcome = Eligibility.check_vendor_eligibility(
                self.partner_id, company=self.company_id, category=trade,
                project=self.re_project_id, date=date, purpose='award')
            if not outcome['eligible']:
                refusals += outcome['blocking_reasons']
            elif outcome['warnings']:
                warnings += outcome['warnings']
        if refusals:
            raise UserError(_(
                "%(order)s cannot be confirmed:\n\n%(reasons)s\n\nThe vendor "
                "policy in force is %(policy)s. Qualify the vendor, lift the "
                "restriction, or change the policy — nothing here is "
                "overridden by confirming again.",
                order=self.name,
                reasons='\n'.join('• %s' % reason
                                  for reason in dict.fromkeys(refusals)),
                policy=Eligibility.vendor_policy_for(
                    self.re_project_id, self.company_id)))
        if warnings:
            Eligibility.post_governance_note(self, _(
                "Confirmed with vendor governance gaps recorded:\n%s")
                % '\n'.join('• %s' % warning
                            for warning in dict.fromkeys(warnings)))
        return True

    def _order_vendor_categories(self):
        """The trades this order buys.

        From the requisition lines where there are any — the buyer said what
        they were sourcing. From the product categories otherwise. An order
        that maps to no trade at all is checked once with no trade, which
        asks "is this vendor qualified for anything here", and that is the
        right question for a lump-sum service line.
        """
        self.ensure_one()
        Category = self.env['realestate.procurement.vendor.category'].sudo()
        trades = self.order_line.re_material_request_line_id.mapped(
            'vendor_category_id')
        if not trades:
            suggested = set()
            for line in self.order_line:
                suggested |= set(
                    Category.suggest_for_product(line.product_id).ids)
            trades = Category.browse(sorted(suggested))
        # Elevated for the same reason `suggest_for_product` is: the person
        # confirming may hold nothing but Odoo's Purchase Manager, and which
        # question the gate asks about them cannot depend on their being
        # allowed to read the trade list.
        questions = list(trades.sudo())
        sourced = self.order_line.filtered(lambda l: not l.display_type)
        if not questions or len(
                sourced.re_material_request_line_id.filtered(
                    'vendor_category_id')) < len(sourced):
            # Mixed or unclassified scope is also asked the trade-less
            # question, so a vendor cannot arrive through the lines nobody
            # classified.
            questions.append(None)
        return questions

    def _check_coding_completeness(self):
        """A governed commitment that cannot be classified is not governed.

        Money committed against a project with no cost code lands under
        Unassigned on the cost report, where it is real, visible and
        attributable to nothing. Under a governed policy that is a refusal;
        under an optional one it stays a warning, because plenty of legitimate
        historic orders look like that and this is not the milestone that
        rewrites them.
        """
        self.ensure_one()
        POLine = self.env['purchase.order.line']
        if 're_cost_code_id' not in POLine._fields:
            return True
        uncoded = self.order_line.filtered(
            lambda line: not line.display_type and not line.re_cost_code_id)
        if uncoded:
            raise UserError(_(
                "%(order)s has %(count)s line(s) with no cost code. A "
                "governed project purchase has to say what kind of money it "
                "is, or the commitment reaches the cost report as "
                "Unassigned.", order=self.name, count=len(uncoded)))
        return True

    def _authorised_purchase_exception(self):
        self.ensure_one()
        if self.re_exception_id.state == 'approved' \
                and self.re_exception_id.exception_type == 'direct_purchase':
            return self.re_exception_id
        return self.env['realestate.procurement.control.exception'].search([
            ('purchase_order_id', '=', self.id),
            ('exception_type', '=', 'direct_purchase'),
            ('state', '=', 'approved'),
        ], limit=1)

    # ------------------------------------------------------------------
    # Amount revalidation — M3L / M3M
    # ------------------------------------------------------------------
    def _revalidate_against_approved_basis(self):
        """An approval covers an amount, not a requisition number.

        Reservation 3,000,000 and a purchase order of 3,500,000 is not a
        rounding difference: the extra 500,000 has been approved by nobody,
        and letting it through because the requisition it came from was
        approved would make the approval a formality attached to a document
        rather than to a sum of money.

        The tolerance is configuration and defaults to zero. There is no
        hard-coded 5% or 10% anywhere in this file, because whichever number
        were chosen would be somebody's policy adopted silently.
        """
        self.ensure_one()
        company = self.company_id
        pct = company.procurement_amount_tolerance_pct or 0.0
        flat = company.procurement_amount_tolerance_amount or 0.0
        Control = self.env['realestate.procurement.control']

        for line, amount in self._ordered_amount_by_request_line().items():
            # The gate has to read control records for a user whose only
            # right is to confirm a purchase order. Reading them is not the
            # same as being allowed to edit them, and this is the narrowest
            # place to say so.
            reservation = line.sudo().reservation_ids.filtered(
                lambda r: r.state == 'reserved')[:1]
            if not reservation:
                continue
            allowance = max(reservation.amount_reserved * pct / 100.0, flat)
            basis = reservation.amount_active + allowance
            if amount <= basis + 0.01:
                continue
            if self._authorised_delta_exception(amount):
                continue
            raise UserError(_(
                "%(order)s commits %(amount)s against demand approved at "
                "%(basis)s.%(tolerance)s\n\nRevise and re-approve the "
                "requisition, or have the difference authorised as an "
                "exception — the extra has not been approved by anybody yet.",
                order=self.name,
                amount=Control.format_control_amount(amount,
                                                     company.currency_id),
                basis=Control.format_control_amount(
                    reservation.amount_active, company.currency_id),
                tolerance=(_(" The configured tolerance of %s does not cover "
                             "the difference.")
                           % Control.format_control_amount(
                               allowance, company.currency_id))
                if allowance else ''))
        return True

    def _authorised_delta_exception(self, amount):
        self.ensure_one()
        return self.env['realestate.procurement.control.exception'].search([
            ('purchase_order_id', '=', self.id),
            ('exception_type', '=', 'amount_delta'),
            ('state', '=', 'approved'),
            ('requested_amount', '>=', amount - 0.01),
        ], limit=1)

    def _ordered_amount_by_request_line(self):
        """`{requisition line: control amount on this order}`.

        Grouped rather than per purchase line, because one requisition line
        can appear twice on the same order and the control question is about
        the total.
        """
        self.ensure_one()
        amounts = {}
        for line in self.order_line:
            request_line = line.re_material_request_line_id
            if not request_line:
                continue
            amounts[request_line] = amounts.get(request_line, 0.0) \
                + line._re_control_amount()
        return amounts

    def _convert_reservations(self):
        """Reservation → commitment, once, for this order's demand.

        Three amounts are in play and they are routinely different:

        ```
            reserved   what the approval authorised
            ordered    what this order actually commits
            remaining  what is left of the demand afterwards
        ```

        Converting `min(reserved, ordered)` handles the ordinary case and the
        over-run alike. Where an order comes in **under** the reservation and
        the demand is fully ordered, the difference is released rather than
        left holding capacity for something nobody is going to buy — that
        300,000 belongs back in the project's availability the moment the
        order is confirmed, not at the next month-end review.
        """
        self.ensure_one()
        for request_line, amount in \
                self._ordered_amount_by_request_line().items():
            reservation = request_line.sudo().reservation_ids.filtered(
                lambda r: r.state == 'reserved')[:1]
            if not reservation:
                continue
            qty = sum(self.order_line.filtered(
                lambda line: line.re_material_request_line_id == request_line
            ).mapped('product_qty'))
            reservation._convert(min(amount, reservation.amount_active),
                                 self.order_line.filtered(
                                     lambda line:
                                     line.re_material_request_line_id
                                     == request_line)[:1], qty=qty)
            request_line.invalidate_recordset(['ordered_qty', 'remaining_qty'])
            if request_line.remaining_qty <= 0 and reservation.amount_active:
                reservation._release(_(
                    "%(order)s ordered the whole line at %(amount)s, below "
                    "the %(reserved)s reserved. The difference is released "
                    "rather than held against demand that no longer exists.",
                    order=self.name,
                    amount=self.env['realestate.procurement.control'
                                    ].format_control_amount(
                        amount, self.company_id.currency_id),
                    reserved=self.env['realestate.procurement.control'
                                      ].format_control_amount(
                        reservation.amount_reserved,
                        self.company_id.currency_id)))
        return True

    def action_request_direct_purchase_exception(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Direct Purchase Exception'),
            'res_model': 'realestate.procurement.purchase.exception',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _get_re_project_location(self):
        """The project's receipt location — resolved without asking the buyer
        to edit the project.

        This is the **M7 PO Confirmation Integration Gate**, deferred in §10
        and reproduced by `TestM7ConfirmationIntegrationGate` before it was
        touched.

        `realestate.project._get_stock_location()` creates the location on
        first use and then writes the id back onto the project — and that
        write is not `sudo()`, while the `create` above it is. Confirming a
        project-coded purchase order reaches it through
        `purchase.order.line._prepare_stock_moves()`, so a user Odoo says may
        confirm a purchase order was being asked for write access to a
        `realestate.project` record, and got an `AccessError` from inside
        stock-move preparation that had nothing to do with purchasing.

        It never fired before because every test that confirmed a project
        order did so as a procurement manager or through `sudo()`. M7 makes it
        fire for real: an awarded tender order is confirmed by whoever holds
        the award authority, which is not the same person.

        Two things are separated here. Reading the location is a lookup and
        stays a lookup. Provisioning one is an administrative act on master
        data, so it runs as the system and says so in the log rather than
        happening silently inside a confirmation. The fix lives in this module
        because `real_estate_developer` owns that method and is not M7's to
        edit.
        """
        self.ensure_one()
        project = self.re_project_id
        if not project or not hasattr(project, '_get_stock_location'):
            return False
        # Everything past this point works on the sudo'd project — including
        # the log line. `display_name` is a read like any other, and reading
        # it off the user's own recordset was the same AccessError in a
        # quieter costume.
        project_sudo = project.sudo()
        existing = project_sudo.stock_location_id
        if existing:
            return existing
        location = project_sudo._get_stock_location() or False
        if location:
            _logger.info(
                "Provisioned stock location %s for project %s while confirming "
                "%s. Creating it is an administrative act; it is recorded "
                "rather than done silently.",
                location.display_name, project_sudo.display_name,
                self.sudo().display_name)
        return location

    def _compute_re_source_display(self):
        for rec in self:
            if not rec.re_source_model or not rec.re_source_id:
                rec.re_source_display = ''
                continue
            try:
                src = self.env[rec.re_source_model].browse(rec.re_source_id).exists()
                rec.re_source_display = src.display_name if src else _('Missing source')
            except KeyError:
                rec.re_source_display = _('Unknown source')


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # Wave 6 — `re_material_request_line_id` and `re_material_request_id` are
    # demand linkage, so they are declared by atmta_procurement_request and
    # reached here through the registry exactly as before.
    re_sourcing_line_id = fields.Many2one(
        'realestate.procurement.sourcing.line', string='Tender Line',
        ondelete='set null', index=True, copy=False,
        help="The tender scope line this RFQ line was raised from. Used to "
             "line a bid snapshot up against the ask; the snapshot never "
             "reads its values from here.")

    def _re_control_amount(self, date=None):
        """This line's tax-exclusive amount in company currency.

        `price_subtotal`, so a 15% VAT line contributes 1,000,000 and not
        1,150,000 — the same tax-exclusive basis Construction's budget and
        commitment are stated in, and the same one the reservation used.
        """
        self.ensure_one()
        order = self.order_id
        company = order.company_id or self.env.company
        source = order.currency_id or company.currency_id
        amount = self.price_subtotal or 0.0
        if source == company.currency_id:
            return amount
        return source._convert(
            amount, company.currency_id, company,
            date or (order.date_order and order.date_order.date())
            or fields.Date.context_today(self))

    def _prepare_stock_moves(self, picking):
        """Route project-scoped POs to the project's stock location at
        move-prepare time (safer than rewriting destinations after confirm)."""
        vals_list = super()._prepare_stock_moves(picking)
        location = self.order_id._get_re_project_location()
        if location:
            for vals in vals_list:
                vals['location_dest_id'] = location.id
        return vals_list


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    re_inspection_id = fields.Many2one(
        'realestate.procurement.receipt.inspection', string='Inspection',
        compute='_compute_re_inspection', search='_search_re_inspection',
        help="The inspection sheet for this receipt, if one has been opened.")
    re_inspection_state = fields.Selection(
        related='re_inspection_id.state', string='Inspection Result')
    re_inspection_policy = fields.Selection(
        RECEIPT_INSPECTION_POLICY, compute='_compute_re_inspection',
        string='Inspection Policy')

    def _compute_re_inspection(self):
        """Read as the system: a storekeeper is not a project reader.

        The M7 Confirmation Gate was exactly this mistake one model along —
        a compute that only ever wanted a selection field raising an
        `AccessError` at somebody holding the right to do the thing.
        """
        Inspection = self.env['realestate.procurement.receipt.inspection']
        Control = self.env['realestate.procurement.control']
        found = {
            inspection.picking_id.id: inspection
            for inspection in Inspection.sudo().search(
                [('picking_id', 'in', self.ids)])
        } if self.ids else {}
        for picking in self:
            inspection = found.get(picking.id)
            picking.re_inspection_id = inspection.id if inspection else False
            picking.re_inspection_policy = Control.receipt_inspection_for(
                picking._re_inspection_project(), picking.company_id)

    def _search_re_inspection(self, operator, value):
        inspections = self.env[
            'realestate.procurement.receipt.inspection'].sudo().search(
                [('id', operator, value)])
        return [('id', 'in', inspections.picking_id.ids)]

    def _re_inspection_project(self):
        """The project this delivery is for, or nothing.

        Nothing is a legitimate answer: plenty of receipts are not against a
        project at all, and inspection is a project control.
        """
        self.ensure_one()
        order = self.sudo().move_ids.purchase_line_id.order_id[:1]
        if order and 're_project_id' in order._fields:
            return order.re_project_id
        return self.env['realestate.project']

    def _re_inspection_required(self):
        """Whether this particular receipt is under the inspection policy."""
        self.ensure_one()
        if self.picking_type_id.code != 'incoming':
            return 'off'
        if not self._re_inspection_project():
            return 'off'
        return self.env['realestate.procurement.control'
                        ].receipt_inspection_for(
                            self._re_inspection_project(), self.company_id)

    def action_open_inspection(self):
        """Open — creating if needed — this receipt's inspection sheet."""
        self.ensure_one()
        inspection = self.env[
            'realestate.procurement.receipt.inspection']._for_picking(self)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.procurement.receipt.inspection',
            'res_id': inspection.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def button_validate(self):
        """Refuse an uninspected receipt where the project requires one.

        The refusal sits on `button_validate()` rather than `_action_done()`
        because this is a decision a person is making at a screen, and the
        message has to reach them while they can still act on it. `_action_done`
        runs for backorders, scrap and internal transfers too, where the
        message would arrive attached to something nobody chose to do.
        """
        for picking in self:
            policy = picking._re_inspection_required()
            if policy == 'off':
                continue
            inspection = picking.re_inspection_id
            done = inspection and inspection.state in (
                'passed', 'partial', 'failed')
            if done:
                continue
            if policy == 'warn':
                picking.message_post(body=_(
                    "Validated without a material inspection. The project's "
                    "policy records the gap rather than refusing it."))
                _logger.info(
                    "Receipt %s validated with no inspection under a warn "
                    "policy.", picking.name)
                continue
            raise UserError(_(
                "%(picking)s delivers material to %(project)s, which requires "
                "inspection before a receipt is validated.\n\nOpen the "
                "inspection, record what arrived and what of it is fit to "
                "use, then validate. Rejecting material here is not a "
                "judgement about the vendor — it is a statement about this "
                "load.",
                picking=picking.name,
                project=picking._re_inspection_project().display_name))
        self._apply_inspection_outcome()
        return super().button_validate()

    def _apply_inspection_outcome(self):
        """What was accepted is what enters stock.

        Until this runs, an inspection is a document beside the receipt saying
        half the load was cracked while the receipt books all of it in — which
        is worse than no inspection at all, because it produces a record that
        looks like a control and changes nothing. The rejected quantity never
        enters stock, so it never reaches `qty_received`, so it never rolls up
        to the requisition and is never billable.

        Only quantities are touched, and only downwards. Nothing here creates a
        return, a claim or a debit note: what happens to rejected material is a
        commercial conversation, and inventing a stock move for it would be
        this module deciding an outcome that is not its to decide.
        """
        for picking in self:
            inspection = picking.re_inspection_id
            if not inspection or inspection.state not in ('partial', 'failed'):
                continue
            if not inspection.accepted_qty:
                raise UserError(_(
                    "%(picking)s was inspected and nothing on it was "
                    "accepted.\n\nValidating would book rejected material "
                    "into stock. Cancel the receipt, or arrange the return "
                    "with the vendor — either way it is a decision somebody "
                    "makes, not one this screen makes for them.",
                    picking=picking.name))
            by_move = {
                line.move_id.id: line.accepted_qty
                for line in inspection.sudo().line_ids
            }
            for move in picking.move_ids:
                if move.id in by_move:
                    move.quantity = by_move[move.id]
            picking.message_post(body=_(
                "Inspection %(name)s: %(accepted)s accepted, %(rejected)s "
                "rejected. The rejected quantity is not received and is not "
                "billable.",
                name=inspection.name,
                accepted=inspection.accepted_qty,
                rejected=inspection.rejected_qty))

    def _action_done(self):
        """Roll receipts up to the requisition when a receipt is validated.

        This used to happen inside a compute on the requisition line, which
        meant the request's state depended on when the ORM happened to
        invalidate a cache. Inventory validating a receipt is the actual
        event, so that is where the rollup is triggered from.
        """
        res = super()._action_done()
        requests = self.move_ids.purchase_line_id.re_material_request_id
        if requests:
            requests.invalidate_recordset()
            requests._refresh_state_from_lines()
        return res
