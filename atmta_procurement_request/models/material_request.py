import hashlib
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError



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
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account',
        compute='_compute_analytic', store=True, readonly=False,
        help='Cost center for spawned POs. Defaults to the project\'s analytic account.',
    )

    @api.depends('project_id')
    def _compute_analytic(self):
        for rec in self:
            if rec.project_id and 'analytic_account_id' in rec.project_id._fields \
                    and rec.project_id.analytic_account_id and not rec.analytic_account_id:
                rec.analytic_account_id = rec.project_id.analytic_account_id

    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
    )

    procurement_type = fields.Selection([
        ('material', 'Material'),
        ('service', 'Service'),
        ('subcontract', 'Subcontract'),
        ('equipment', 'Equipment'),
        ('other', 'Other'),
    ], default='material', required=True, tracking=True, index=True,
        help="A service does not arrive on a pallet and a subcontract is not "
             "bought from a catalogue. Pushing all four through identical "
             "stock expectations is how a service request waits forever for a "
             "receipt that will never exist.")

    #: Where the need came from. Recorded as a classification plus an exact
    #: relation, because 'it came from the BOQ' is only useful if the BOQ line
    #: can be opened.
    source_type = fields.Selection([
        ('procurement_plan', 'Procurement Plan'),
        ('construction', 'Construction Works'),
        ('boq', 'BOQ'),
        ('change_order', 'Approved Change Order'),
        ('site_request', 'Site Request'),
        ('inventory', 'Inventory Replenishment'),
        ('maintenance', 'Maintenance'),
        ('manual', 'Manual'),
        ('other', 'Other'),
    ], default='manual', index=True, tracking=True)
    plan_line_id = fields.Many2one(
        'realestate.procurement.plan.line', string='Plan Line',
        ondelete='set null', index=True, check_company=True)
    plan_id = fields.Many2one(
        related='plan_line_id.plan_id', store=True, readonly=True)
    # `change_order_id` and `boq_line_id` are added by
    # `real_estate_construction` for the same dependency reason as the line
    # coding: Construction depends on Procurement, not the reverse.

    buyer_id = fields.Many2one(
        'res.users', string='Responsible Buyer', tracking=True,
        help="Who is sourcing it. Not who asked for it.")
    justification = fields.Text(
        help="Why the project needs it. The sentence an approver reads.")
    request_date = fields.Date(
        default=fields.Date.context_today, tracking=True,
        help="When it was raised. Distinct from the date it is needed and "
             "from the date anything was ordered.")

    # ---------- Revision history (M2H) ----------
    revision = fields.Integer(
        default=0, readonly=True, copy=False, tracking=True,
        help="Bumped whenever an approved or sourced basis is reopened.")
    revision_ids = fields.One2many(
        'realestate.material.request.revision', 'request_id',
        string='Revision History', readonly=True)
    revision_count = fields.Integer(compute='_compute_revision_count')

    #: Migration classification (M2Q). Set by `_classify_procurement_legacy()`
    #: and never used to change a record — only to say what is known about it.
    legacy_status = fields.Selection([
        ('valid', 'Valid'),
        ('needs_project', 'Needs Project'),
        ('needs_wbs', 'Needs WBS'),
        ('needs_cost_code', 'Needs Cost Code'),
        ('legacy_auto_confirmed', 'Legacy Auto-Confirmed'),
        ('linked_rfq_po', 'Linked Purchase Document'),
        ('ambiguous', 'Ambiguous'),
    ], readonly=True, copy=False, index=True, string='Migration Status',
        help="What the M2 migration could determine about this record. It is "
             "a finding, not an instruction, and nothing was rewritten to "
             "make a record fit a classification.")

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
    ], default='0', tracking=True, index=True,
        help="Operational urgency, and nothing else. M3 removed the Phase 0 "
             "behaviour where marking a request urgent promoted it straight "
             "to approved — a field the requester controls decided whether "
             "the requester's own request needed approving. Urgency can add "
             "an emergency approver and shorten a lead time; it has never "
             "been able to authorise money.")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        # M2 — beginning to source is not the same as having ordered. The old
        # workflow had no room between the two, which is why one click could
        # turn authorised demand into a commitment.
        ('sourcing', 'Sourcing'),
        # M3P — one confirmed order against a three-line requisition is not
        # "ordered". The distinction matters because the remainder is still
        # reserved and still has to be bought.
        ('partially_ordered', 'Partially Ordered'),
        ('ordered', 'Ordered'),
        ('partial', 'Partially Received'),
        ('received', 'Received'),
        ('done', 'Done'),
        # M3H — Phase 0 could only express refusal by putting the request back
        # to draft, which reads exactly like the requester changing their mind.
        ('rejected', 'Rejected'),
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

    #: Header coding is a default for new lines and nothing more. The line
    #: value is what reaches the purchase order.
    lines_missing_cost_code = fields.Integer(
        compute='_compute_coding_coverage', store=True)
    lines_missing_wbs = fields.Integer(
        compute='_compute_coding_coverage', store=True)
    is_fully_coded = fields.Boolean(
        compute='_compute_coding_coverage', store=True,
        help="Every line carries a cost code. Uncoded lines are not blocked "
             "here — they are counted, and shown as Unassigned wherever the "
             "money appears.")

    @api.depends('line_ids')
    def _compute_coding_coverage(self):
        """Coverage of whatever coding the installed modules provide.

        Procurement itself has no cost codes — Construction owns them and adds
        the fields. When Construction is absent there is nothing to be missing,
        which is why this reads the field rather than assuming it.
        """
        has_code = 'cost_code_id' in self.env[
            'realestate.material.request.line']._fields
        has_wbs = 'wbs_id' in self.env[
            'realestate.material.request.line']._fields
        for rec in self:
            rec.lines_missing_cost_code = len(rec.line_ids.filtered(
                lambda l: not l.cost_code_id)) if has_code else 0
            rec.lines_missing_wbs = len(rec.line_ids.filtered(
                lambda l: not l.wbs_id)) if has_wbs else 0
            rec.is_fully_coded = bool(rec.line_ids) and \
                not rec.lines_missing_cost_code
            if not has_code:
                rec.coding_status = 'n/a'
            elif not rec.line_ids or \
                    rec.lines_missing_cost_code == len(rec.line_ids):
                rec.coding_status = 'unassigned'
            elif rec.lines_missing_cost_code:
                rec.coding_status = 'partial'
            else:
                rec.coding_status = 'coded'

    coding_status = fields.Selection([
        ('coded', 'Coded'),
        ('partial', 'Partly Coded'),
        ('unassigned', 'Unassigned'),
        ('n/a', 'No Coding Installed'),
    ], compute='_compute_coding_coverage', store=True, index=True,
        help="Shown rather than enforced. A request the company allows to go "
             "ahead uncoded still has to appear somewhere — as Unassigned, "
             "not as an absence.")

    data_quality_warnings = fields.Text(
        compute='_compute_data_quality',
        help="Deterministic findings, in plain words. Nothing here blocks the "
             "request; it says what a reader would otherwise have to work "
             "out for themselves.")
    has_data_quality_warning = fields.Boolean(
        compute='_compute_data_quality', store=True, index=True)

    @api.depends('project_id', 'state', 'needed_by', 'line_ids.qty',
                 'line_ids.uom_id', 'line_ids.estimate_is_known',
                 'line_ids.required_on_site_date', 'lines_missing_cost_code',
                 'lines_missing_wbs', 'line_ids.po_line_ids',
                 'approved_by_id')
    def _compute_data_quality(self):
        for rec in self:
            findings = rec._data_quality_findings()
            rec.data_quality_warnings = '\n'.join(findings)
            rec.has_data_quality_warning = bool(findings)

    def _data_quality_findings(self):
        """Every finding this milestone can state without guessing."""
        self.ensure_one()
        findings = []
        if not self.project_id:
            findings.append(_("No project — the demand belongs to nobody's "
                              "cost report."))
        if self.lines_missing_cost_code:
            findings.append(_(
                "%s line(s) carry no cost code and will appear under "
                "Unassigned.") % self.lines_missing_cost_code)
        if self.lines_missing_wbs:
            findings.append(_("%s line(s) carry no WBS.")
                            % self.lines_missing_wbs)
        unknown = len(self.line_ids.filtered(
            lambda ln: not ln.estimate_is_known))
        if unknown:
            findings.append(_(
                "%s line(s) have no estimate. The amount is unknown, which is "
                "not the same as zero.") % unknown)
        missing_uom = len(self.line_ids.filtered(lambda ln: not ln.uom_id))
        if missing_uom:
            findings.append(_("%s line(s) have no unit of measure.")
                            % missing_uom)
        if self.state not in ('draft', 'cancelled', 'rejected'):
            undated = self.line_ids.filtered(
                lambda ln: not ln.required_on_site_date)
            if undated and not self.needed_by:
                findings.append(_(
                    "%s line(s) say when nothing is needed on site.")
                    % len(undated))
        findings.extend(self._coding_propagation_findings())
        findings.extend(self._control_findings())
        return findings

    def _coding_propagation_findings(self):
        """Lines whose coding did not reach the purchase document.

        Silence here would be the worst outcome of all: a correctly coded
        requisition whose order quietly lost the code, reported as fine.
        """
        self.ensure_one()
        if 'cost_code_id' not in self.env[
                'realestate.material.request.line']._fields:
            return []
        lost = self.env['realestate.material.request.line']
        for line in self.line_ids.filtered('cost_code_id'):
            if line.po_line_ids.filtered(
                    lambda pol: pol.re_cost_code_id != line.cost_code_id):
                lost |= line
        if not lost:
            return []
        return [_("%s purchase line(s) did not keep the requisition's cost "
                  "code.") % len(lost)]

    def _compute_revision_count(self):
        for rec in self:
            rec.revision_count = len(rec.revision_ids)

    notes = fields.Html()

    def _control_amount(self):
        """The request's tax-exclusive control amount, in company currency."""
        self.ensure_one()
        return sum(line._control_amount() for line in self.line_ids)
    submitted_on = fields.Datetime(readonly=True, copy=False)

    def _basis_hash(self):
        """A fingerprint of what is being approved.

        Not a security device — a change detector. An approval covers a
        specific commercial basis, and M3's snapshot requirement is only
        meaningful if the system can tell that the basis moved. The
        alternative, comparing field by field at decision time, drifts the
        first time somebody adds a field and forgets the comparison.
        """
        self.ensure_one()
        parts = [
            self.project_id.id, self.company_id.id, self.currency_id.id,
            self.procurement_type, self.revision,
        ]
        for line in self.line_ids.sorted('id'):
            parts.extend([
                line.id, line.product_id.id, line.description or '',
                round(line.qty or 0.0, 6),
                round(line.estimated_cost or 0.0, 2),
                line._control_cost_code_id(),
                line._control_wbs_id(),
            ])
        return hashlib.sha256(
            '|'.join(str(part) for part in parts).encode()).hexdigest()

    def _check_basis_still_matches(self, step):
        """Refuse a decision taken against a basis that has since changed."""
        self.ensure_one()
        if not step.basis_hash:
            return
        if step.basis_hash != self._basis_hash():
            raise UserError(_(
                "%s has changed since this approval step was raised. The "
                "step was created against a different set of lines, "
                "quantities or amounts, and approving it now would record "
                "agreement to something nobody read. Resubmit it.")
                % self.name)

    @api.model
    def _get_source_model_selection(self):
        """Each downstream module appends its own source model here by overriding."""
        options = [
            ('realestate.construction.task', 'Construction Task'),
            ('realestate.construction.milestone', 'Construction Milestone'),
        ]
        for name, label in [
            ('realestate.handover.defect', 'Handover Defect'),
            ('realestate.contract', 'Rental Contract'),
            ('realestate.customer.ticket', 'Customer Service Ticket'),
        ]:
            if name in self.env.registry:
                options.append((name, label))
        return options

    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends('line_ids.estimated_cost')
    def _compute_estimated_total(self):
        for rec in self:
            rec.estimated_total = sum(rec.line_ids.mapped('estimated_cost'))

    @api.depends('line_ids.po_line_ids.order_id')
    def _compute_purchase_orders(self):
        """Every order raised from this requisition, not just the first.

        The old version walked `po_line_id`, a single link that made one
        requisition line mean one purchase line for ever. Sourcing asks
        several vendors, and a later split award puts one line on more than
        one order, so the relation has to be one-to-many.
        """
        for rec in self:
            orders = rec.line_ids.mapped('po_line_ids.order_id')
            rec.purchase_order_ids = orders
            rec.po_count = len(orders)

    rfq_count = fields.Integer(compute='_compute_order_stages', store=True)
    ordered_count = fields.Integer(compute='_compute_order_stages', store=True)

    @api.depends('purchase_order_ids.state')
    def _compute_order_stages(self):
        """Enquiries and orders counted apart, because they mean different
        things: one is a question, the other is money."""
        for rec in self:
            rec.rfq_count = len(rec.purchase_order_ids.filtered(
                lambda po: po.state in ('draft', 'sent')))
            rec.ordered_count = len(rec.purchase_order_ids.filtered(
                lambda po: po.state in ('purchase', 'done')))

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

    # ------------------------------------------------------------------
    # Controlled edit and revision — M2G / M2H
    # ------------------------------------------------------------------
    #: Fields that make up the basis somebody approved. Changing any of them
    #: changes what was agreed, so after submission they move only through a
    #: revision.
    _APPROVED_BASIS_FIELDS = {
        'project_id', 'procurement_type', 'needed_by', 'priority',
        'plan_line_id',
    }
    _OPEN_STATES = ('draft', 'cancelled')

    def write(self, vals):
        self._check_basis_is_still_open(vals, self._APPROVED_BASIS_FIELDS)
        return super().write(vals)

    def _check_basis_is_still_open(self, vals, controlled):
        """Refuse a silent rewrite of an approved basis.

        Not a permission check — a buyer with every right in the system still
        should not be able to turn an approved 100 into 150 without the change
        being visible. The revision path exists precisely so that it is.
        """
        if self.env.context.get('re_procurement_revision'):
            return
        touched = controlled & set(vals)
        if not touched:
            return
        locked = self.filtered(lambda r: r.state not in self._OPEN_STATES)
        if not locked:
            return
        raise UserError(_(
            "%(refs)s were already submitted, so %(fields)s is part of an "
            "approved basis.\n\nReturn the request to draft, or revise it "
            "with a reason — either way the change stays visible.",
            refs=', '.join(locked.mapped('name')),
            fields=', '.join(sorted(touched))))

    def action_revise(self, reason=None):
        """Reopen an approved or sourced request, keeping what it used to say.

        The record itself is reused rather than copied: it is the same demand,
        at a later revision. What must not be lost is the basis, and that is
        written to an immutable snapshot before anything changes.
        """
        self.ensure_one()
        if self.state in ('draft', 'cancelled'):
            raise UserError(_(
                "%s is already open for editing.") % self.name)
        if self.state in ('ordered', 'partially_ordered', 'partial',
                          'received', 'done'):
            raise UserError(_(
                "%s has confirmed orders against it. Changing ordered demand "
                "is a purchasing change, not a requisition edit.") % self.name)
        if not reason or not str(reason).strip():
            raise UserError(_(
                "Say why the approved basis is changing. A revision without a "
                "reason is indistinguishable from an overwrite."))

        snapshot = self.env[
            'realestate.material.request.revision']._snapshot(self, reason)
        # M3 — the old reservation authorised the old basis. Releasing it here
        # rather than leaving it to be adjusted later is what stops a revised
        # requisition from holding capacity for demand nobody approved.
        self._release_reservations(_(
            "Superseded by revision %s: %s") % (self.revision + 1, reason))
        self.with_context(re_procurement_revision=True).write({
            'revision': self.revision + 1,
            'state': 'draft',
            'approved_by_id': False,
            'approval_date': False,
        })
        self._cancel_pending_approvals()
        self.message_post(body=_(
            "Revision %(rev)s. Basis at revision %(prev)s kept: %(reason)s",
            rev=self.revision, prev=snapshot.revision, reason=reason))
        return snapshot

    def action_open_rfq_wizard(self):
        """Button target — a button cannot carry a vendor list."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Requests for Quotation'),
            'res_model': 'realestate.material.request.rfq',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_open_revision_wizard(self):
        """Button target — the reason has to be typed, so it needs a form."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revise Requisition'),
            'res_model': 'realestate.material.request.revise',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    # ------------------------------------------------------------------
    # Migration classification — M2Q
    # ------------------------------------------------------------------
    def _classify_procurement_legacy(self, uncoded_ids=None,
                                     unassigned_wbs_ids=None):
        """Say what is known about each existing record. Change nothing else.

        The migration does not manufacture procurement plans, does not invent
        cost codes and does not touch a single purchase document. Where a
        legacy request already produced a confirmed order, that order stays
        confirmed and its commitment stays exactly where Construction put it.

        `uncoded_ids` and `unassigned_wbs_ids` exist for one caller: the
        migration script. Construction adds the coding fields and Construction
        loads *after* Procurement, so at migration time those fields are not in
        the registry — asking the ORM there returns "no missing codes" for
        every record, which reads as a clean bill of health for a database
        where nothing is coded at all. The migration therefore determines the
        answer in SQL and passes it in. Left as None, the classification reads
        the fields normally, which is right everywhere else.
        """
        Line = self.env['realestate.material.request.line']
        has_code = 'cost_code_id' in Line._fields
        has_wbs = 'wbs_id' in Line._fields
        for rec in self:
            uncoded = (rec.id in uncoded_ids if uncoded_ids is not None
                       else (has_code and bool(rec.line_ids.filtered(
                           lambda ln: not ln.cost_code_id))))
            no_wbs = (rec.id in unassigned_wbs_ids
                      if unassigned_wbs_ids is not None
                      else (has_wbs and bool(rec.line_ids.filtered(
                          lambda ln: not ln.wbs_id))))
            rec.legacy_status = rec._legacy_classification(uncoded, no_wbs)
        return True

    # ------------------------------------------------------------------
    # Migration classification — M3AA
    # ------------------------------------------------------------------
    governance_status = fields.Selection([
        ('legacy_confirmed_po', 'Already Committed'),
        ('sourcing_draft_rfq', 'Sourcing — Draft RFQ'),
        ('approved_unordered', 'Approved, Not Ordered'),
        ('not_applicable', 'No Live Demand'),
    ], readonly=True, copy=False, index=True, string='Control Classification',
        help="What M3 could determine about this record at upgrade time. A "
             "finding, not an instruction: nothing was reserved, released, "
             "confirmed or rewritten to make a record fit one of these.")
    legacy_urgent_bypass = fields.Boolean(
        readonly=True, copy=False, string='Legacy Urgent Bypass',
        help="Approved with no approval step, on a request marked urgent — "
             "the signature of the Phase 0 behaviour M3 removed. Kept as "
             "evidence and deliberately not undone: the demand was authorised "
             "under the rules in force at the time, and retroactively "
             "un-approving it would be rewriting history to flatter the new "
             "version.")
    legacy_self_approved = fields.Boolean(
        readonly=True, copy=False, string='Legacy Self-Approval',
        help="Raised and approved by the same person. Same reasoning: "
             "recorded, audited, not reversed.")

    def _classify_procurement_governance(self):
        """Say where each existing requisition stands, and change nothing.

        The lifecycle classification and the two evidence flags are separate
        on purpose. "Already committed" and "self-approved" are not
        alternatives — a request can easily be both, and forcing them into one
        selection would mean losing whichever the precedence order happened to
        rank second.
        """
        for rec in self:
            confirmed = rec.line_ids.po_line_ids.filtered(
                lambda pol: pol.state in ('purchase', 'done'))
            drafts = rec.line_ids.po_line_ids.filtered(
                lambda pol: pol.state in ('draft', 'sent'))
            if confirmed or rec.line_ids.filtered('po_line_id'):
                status = 'legacy_confirmed_po'
            elif drafts:
                status = 'sourcing_draft_rfq'
            elif rec.state in ('approved', 'sourcing'):
                status = 'approved_unordered'
            else:
                status = 'not_applicable'
            rec.governance_status = status
            rec.legacy_urgent_bypass = bool(
                rec.priority == '1' and rec.approval_date
                and not rec._has_approval_snapshot())
            rec.legacy_self_approved = bool(
                rec.approved_by_id
                and rec.approved_by_id == rec.requested_by_id)
        return True

    def _legacy_classification(self, is_uncoded, has_no_wbs):
        """Read the lines, never the stored aggregates.

        The first version of this asked `lines_missing_cost_code`, and during
        the migration that stored compute is still NULL — so an uncoded legacy
        request came back as `valid`. Unknown read as zero, which is the exact
        mistake this classification exists to stop other people making.
        """
        self.ensure_one()
        if not self.line_ids:
            return 'ambiguous'
        if not self.project_id:
            return 'needs_project'
        if self.line_ids.filtered('po_line_id'):
            # `po_line_id` was only ever written by the one-click flow that
            # created and confirmed an order in the same breath. Nothing
            # since M2 writes it, so its presence dates the record exactly.
            # This outranks a missing cost code deliberately: that money has
            # already been committed under Unassigned, and coding the
            # requisition now would not move it.
            return 'legacy_auto_confirmed'
        if self.line_ids.po_line_ids:
            return 'linked_rfq_po'
        if is_uncoded:
            return 'needs_cost_code'
        if has_no_wbs:
            return 'needs_wbs'
        return 'valid'

    @api.constrains('line_ids')
    def _check_lines(self):
        for rec in self:
            if rec.state not in ('draft', 'cancelled') and not rec.line_ids:
                raise ValidationError(_("A material request must have at least one line before submission."))

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        """One procurement document, one company.

        Project → plan → request → lines → purchase order has to stay inside a
        single company: the analytic accounts, the budget and the eventual
        vendor bill all belong to one set of books.
        """
        for rec in self:
            project_company = rec.project_id.company_id
            if project_company and project_company != rec.company_id:
                raise ValidationError(_(
                    "%(ref)s is in %(company)s but %(project)s belongs to "
                    "%(other)s.", ref=rec.name,
                    company=rec.company_id.display_name,
                    project=rec.project_id.display_name,
                    other=project_company.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.material.request') or 'MR-NEW'
        records = super().create(vals_list)
        # Auto-fill source_model + project/property from source_ref for
        # RPC/programmatic creates where onchange doesn't fire.
        for rec in records:
            if rec.source_ref and not rec.source_model:
                src = rec.source_ref
                vals_write = {'source_model': src._name}
                if not rec.project_id and 'project_id' in src._fields and src.project_id:
                    vals_write['project_id'] = src.project_id.id
                if not rec.property_id and 'property_id' in src._fields and src.property_id:
                    vals_write['property_id'] = src.property_id.id
                rec.write(vals_write)
        return records

    # ---------- State actions ----------
    def action_submit(self):
        """Send the requisition for approval. Nothing is approved here.

        M3J: the urgent branch is gone. It used to promote the request
        straight past every control, which meant a requester could authorise
        their own demand by ticking a box they owned. Urgency is now a
        matching dimension in the approval matrix — it can require an
        emergency approver, and there is no configuration that lets it require
        nobody.
        """
        for rec in self:
            if rec.state not in ('draft', 'rejected'):
                raise UserError(_(
                    "Only a draft or rejected requisition can be submitted. "
                    "%(name)s is %(state)s.", name=rec.name,
                    state=dict(rec._fields['state'].selection).get(
                        rec.state, rec.state)))
            if not rec.line_ids:
                raise UserError(_("Add at least one line before submitting."))
            rec.submitted_on = fields.Datetime.now()
            rec.state = 'submitted'
            rec._on_submitted()
            if rec.priority == '1':
                rec.message_post(body=_(
                    "Submitted as urgent. Urgency shortens lead times and "
                    "raises visibility; it does not approve anything."))

    def action_back_to_draft(self):
        for rec in self:
            if rec.state in ('ordered', 'partially_ordered', 'partial',
                             'received', 'done'):
                raise UserError(_("Cannot return to draft after a PO has been created."))
            rec._release_reservations(_("Returned to draft."))
            rec.state = 'draft'
            rec.approved_by_id = False
            rec.approval_date = False
            rec._cancel_pending_approvals()

    def action_cancel(self):
        for rec in self:
            if rec.purchase_order_ids.filtered(lambda po: po.state not in ('draft', 'sent', 'cancel')):
                raise UserError(_(
                    "Cannot cancel — at least one linked PO is already confirmed. "
                    "Cancel the PO first."
                ))
            rec._release_reservations(_("Requisition cancelled."))
            rec.state = 'cancelled'

    def _prepare_rfq_line(self, line):
        """Values for one RFQ line, carrying the control dimensions.

        This is the extension point for project control. Construction
        overrides it to add the WBS and the cost code, and its own
        `purchase.order.line.create()` then builds the analytic distribution.
        The distribution format is deliberately not written here: Construction
        owns it, having discovered the hard way that two separate plan keys
        create two analytic lines each at full amount.
        """
        self.ensure_one()
        values = {
            'name': line.description or line.product_id.display_name or _(
                'Requested item'),
            'product_qty': line.qty,
            'price_unit': line._unit_price(),
            'date_planned': self._rfq_date_planned(line),
            're_material_request_line_id': line.id,
        }
        if line.product_id:
            values['product_id'] = line.product_id.id
            values['product_uom'] = line.uom_id.id or line.product_id.uom_id.id
        return values

    def _rfq_date_planned(self, line):
        """When the vendor is asked to deliver.

        The line's required-on-site date when it has one. Otherwise the
        header's needed-by date. Otherwise today — an enquiry needs a date,
        and inventing a lead time the project never stated would be worse.
        """
        self.ensure_one()
        target = line.required_on_site_date or self.needed_by
        if target:
            return fields.Datetime.to_datetime(target)
        return fields.Datetime.now()

    def action_create_purchase_orders(self):
        """DEPRECATED (M2). Kept so existing actions and integrations resolve.

        Its old behaviour — create orders from `seller_ids[0]` and confirm
        them immediately — is exactly what this milestone removed. It now
        refuses rather than silently doing something different from what its
        name promises, because a method called "create purchase orders" that
        quietly creates enquiries instead would be its own kind of lie.
        """
        self.ensure_one()
        raise UserError(_(
            "Creating and confirming purchase orders directly from a "
            "requisition is no longer how sourcing works.\n\n"
            "Use Create RFQs and name the vendors to enquire with. The "
            "resulting quotations are confirmed deliberately, once a vendor "
            "has been chosen — that confirmation is what commits the "
            "project's budget."))

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('id', 'in', self.purchase_order_ids.ids)],
        }

    # ---------- Receipt rollup ----------
    #
    # `_compute_state_from_receipts` used to sit here: an `@api.depends` over a
    # body of `pass`, named by no field as a compute, and so never once
    # executed. Removed in M8. It was harmless and actively misleading — the
    # next person to touch receipt state would have found it, believed the
    # rollup ran there, and edited a method that does nothing. The rollup runs
    # from `_refresh_state_from_lines()` below, triggered by
    # `stock.picking._action_done()`, which is the event that actually happens.

    def _refresh_state_after_ordering(self):
        """Somebody confirmed an order against this request.

        Called from `purchase.order.button_confirm()` — Procurement never
        confirms anything itself. M3P splits the outcome in two: a requisition
        whose demand is only partly on confirmed orders is *partially
        ordered*, and the difference is not cosmetic, because the remainder is
        still reserved and still has to be bought by somebody.
        """
        for rec in self:
            if rec.state not in ('approved', 'sourcing', 'partially_ordered'):
                continue
            ordered = sum(rec.line_ids.mapped('ordered_qty'))
            if not ordered:
                continue
            outstanding = any(line.remaining_qty > 0 for line in rec.line_ids)
            rec.with_context(re_procurement_revision=True).state = (
                'partially_ordered' if outstanding else 'ordered')

    def _refresh_state_from_lines(self):
        """Move a sourced request along as its orders are received.

        Called from the request rather than from a line compute, so the state
        no longer depends on when the cache happened to be invalidated.
        """
        for rec in self:
            if rec.state not in ('ordered', 'partially_ordered', 'partial'):
                continue
            total_qty = sum(rec.line_ids.mapped('qty'))
            received = sum(rec.line_ids.mapped('received_qty'))
            if total_qty <= 0:
                continue
            if received >= total_qty:
                rec.state = 'received'
            elif received > 0:
                rec.state = 'partial'

    # ------------------------------------------------------------------
    # Control seams — Wave 6 / AD-008
    #
    # A requisition is demand. Whether that demand consumes purchasing
    # capacity, and who has to authorise it, are control decisions, and they
    # live in `atmta_procurement_control`. Request declares the seams and
    # does nothing at them; Control installs the behaviour by overriding
    # them. That is what keeps the dependency pointing one way.
    # ------------------------------------------------------------------
    def action_create_rfqs(self, vendors=None):
        """Go to market with this demand.

        Solicitation is `atmta_procurement_sourcing`, which overrides this and
        does the work. Without it a requisition can still be raised, approved
        and revised — it simply has nowhere to go, and saying so plainly beats
        an AttributeError from a button.
        """
        raise UserError(_(
            "Requests for quotation need the procurement sourcing capability "
            "(atmta_procurement_sourcing), which is not installed."))

    def _control_findings(self):
        """What the control records say. Nothing, with no control installed."""
        self.ensure_one()
        return []

    def _release_reservations(self, reason):
        """Stop consuming capacity. No capacity is consumed without Control."""
        return True

    def _cancel_pending_approvals(self):
        """Withdraw undecided approvals. There are none without Control."""
        return True

    def _on_submitted(self):
        """Called once per record at submission, after the state is written.

        Control reads the budget position here, records the status the
        approvers will be answering, and generates the approval snapshot.
        """
        return True

    def _has_approval_snapshot(self):
        """Did the approval matrix produce steps for this requisition?"""
        self.ensure_one()
        return False
