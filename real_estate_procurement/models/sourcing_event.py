# -*- coding: utf-8 -*-
"""M5 — the Sourcing Event, and the versions and lines it issues.

```
    SOURCING EVENT      what we are taking to market, and under whose authority
    TENDER VERSION      what we asked vendors to quote, frozen at issue
    TENDER LINE         the scope of the ask
    DEMAND ALLOCATION   which authorised demand each line consumes
```

The event is deliberately a first-class model rather than an extension of
Odoo's `purchase.order.group`. That group is comparison plumbing: it holds no
reference, no state, no dates and no audit, its `alternative_po_ids` is a
related view of it, and **it deletes itself the moment it drops to one order**
(`purchase_requisition/models/purchase.py`, `PurchaseOrderGroup.write`). A
tender whose identity vanished because two of three vendors declined would be
a tender nobody could answer questions about afterwards.

### Tendering moves no money

M5's whole financial contribution is a negative. An event consumes demand that
M3 already authorised and that already holds a reservation; it does not
reserve, re-reserve, release or commit anything of its own. A cheap bid does
not release authorisation and an expensive one does not expand it — the market
answering a question is not the company changing its mind. M7 revalidates at
award.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: Once issued, a version is evidence. Everything here refuses to be edited.
ISSUED_STATES = ('issued', 'superseded')

#: States in which a tender is live enough that its basis must not move
#: without an addendum.
PUBLISHED_STATES = ('published', 'closed')


class SourcingEvent(models.Model):
    _name = 'realestate.procurement.sourcing.event'
    _description = 'Procurement Sourcing Event'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        required=True, copy=False, readonly=True, default=lambda self: _('New'),
        index=True)
    title = fields.Char(required=True, tracking=True)
    description = fields.Html()
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', index=True, tracking=True,
        help="The project whose authorised demand this event sources.")
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', store=True)

    buyer_id = fields.Many2one(
        'res.users', string='Buyer', tracking=True,
        default=lambda self: self.env.user)
    team_ids = fields.Many2many(
        'res.users', 'sourcing_event_team_rel', 'event_id', 'user_id',
        string='Sourcing Team',
        help="Internal participants. Confidential bid detail is readable by "
             "the buyer, this team and Procurement Managers.")

    sourcing_method = fields.Selection([
        ('competitive_tender', 'Competitive Tender'),
        ('limited_tender', 'Limited Tender'),
        ('rfq', 'Request for Quotation'),
        ('single_source', 'Single Source'),
        ('direct_source', 'Direct Source'),
        ('framework', 'Framework / Reference Agreement'),
        ('other', 'Other'),
    ], required=True, default='competitive_tender', tracking=True,
        help="How the market is being approached. ATMTA does not implement "
             "public-procurement law: how many invitations or responses a "
             "method requires is company policy, not a property of the "
             "method.")
    procurement_type = fields.Selection([
        ('material', 'Material'),
        ('service', 'Service'),
        ('subcontract', 'Subcontract'),
        ('equipment', 'Equipment'),
        ('mixed', 'Mixed'),
    ], default='material', tracking=True)
    category_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Trade / Category',
        tracking=True,
        help="The trade eligibility is asked about when vendors are invited.")

    # -- Dates. Datetime throughout: a deadline is a moment, not a day. --
    issue_datetime = fields.Datetime(readonly=True, copy=False, tracking=True)
    clarification_deadline = fields.Datetime(tracking=True)
    original_close_datetime = fields.Datetime(
        readonly=True, copy=False, tracking=True,
        help="The closing moment as first published. Never overwritten — an "
             "extension changes the effective close and leaves this alone.")
    close_datetime = fields.Datetime(
        string='Effective Close', required=True, tracking=True)
    anticipated_award_date = fields.Date(tracking=True)
    required_on_site_date = fields.Date(tracking=True)
    actual_closed_datetime = fields.Datetime(
        readonly=True, copy=False, tracking=True,
        help="When bidding was actually closed, which is not always the "
             "moment it was due to.")

    # -- M5.12 / M5.13 — what this event requires of a submission -------
    addendum_ack_policy = fields.Selection([
        ('company', 'Company Default'),
        ('optional', 'Optional'),
        ('required_before_response', 'Required before a response is valid'),
    ], default='company', required=True, string='Addendum Acknowledgement',
        help="Whether a vendor must acknowledge an addendum before their "
             "response counts. Not hard-coded either way: a two-day RFQ for "
             "sand and a nine-month subcontract tender do not need the same "
             "ceremony.")
    require_validity_date = fields.Boolean(
        string='Bid Validity Required',
        help="Administrative requirement only.")
    require_bid_document = fields.Boolean(
        string='Submission Document Required',
        help="Administrative requirement only. What is *in* the document is "
             "M6's business, not this module's.")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'In Review'),
        ('published', 'Published'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False)

    line_ids = fields.One2many(
        'realestate.procurement.sourcing.line', 'event_id', string='Scope')
    allocation_ids = fields.One2many(
        'realestate.procurement.sourcing.demand.allocation', 'event_id',
        string='Authorised Demand')
    version_ids = fields.One2many(
        'realestate.procurement.sourcing.version', 'event_id',
        string='Tender Versions')
    current_version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', copy=False, readonly=True,
        help="The version vendors are quoting against right now.")
    invitation_ids = fields.One2many(
        'realestate.procurement.sourcing.invitation', 'event_id',
        string='Invited Vendors')
    clarification_ids = fields.One2many(
        'realestate.procurement.sourcing.clarification', 'event_id')
    bid_response_ids = fields.One2many(
        'realestate.procurement.bid.response', 'event_id')

    # -- M6 — what an evaluation would need next, described not performed ---
    evaluation_readiness = fields.Selection([
        ('open_not_ready', 'Open — Not Ready to Evaluate'),
        ('closed_unevaluated', 'Closed — Awaiting Evaluation'),
        ('evaluation_candidate', 'Evaluation Candidate'),
        ('legacy_external_evaluation', 'Legacy / Evaluated Outside ATMTA'),
        ('evaluated', 'Evaluated in ATMTA'),
        ('ambiguous', 'Ambiguous'),
    ], readonly=True, copy=False, index=True,
        help="A description of where this tender stands, written by the M6 "
             "upgrade. It is not an evaluation and confers no result: a "
             "closed tender is labelled as awaiting one, never given one.")
    evaluation_round_ids = fields.One2many(
        'realestate.procurement.evaluation.round', 'sourcing_event_id')

    # -- M7 — where this tender stands on awarding, described not performed --
    award_readiness = fields.Selection([
        ('not_evaluated', 'Not Evaluated — Nothing to Award'),
        ('awaiting_award', 'Evaluated — Awaiting an Award Decision'),
        ('award_drafted', 'Award Drafted'),
        ('award_in_review', 'Award Awaiting Approval'),
        ('award_approved', 'Award Approved — Orders Not Yet Confirmed'),
        ('awarded', 'Awarded and Issued'),
        ('committed_without_award', 'Committed Without An Award'),
        ('cancelled', 'Cancelled'),
    ], readonly=True, copy=False, index=True,
        help="A description of where this tender stands on awarding, written "
             "by the M7 upgrade. It confers nothing: a tender with a finished "
             "evaluation is labelled as awaiting a decision, never given one.")
    award_ids = fields.One2many(
        'realestate.procurement.award', 'sourcing_event_id')

    authorised_amount = fields.Monetary(
        compute='_compute_authorised', store=True, currency_field='currency_id',
        help="Sum of the authorised demand this event consumes. It is a "
             "ceiling to compare bids against, not money this event holds.")
    invitation_count = fields.Integer(compute='_compute_counts')
    response_count = fields.Integer(compute='_compute_counts')
    rfq_count = fields.Integer(compute='_compute_counts')
    notes = fields.Text()

    _sql_constraints = [
        ('name_uniq', 'unique(name, company_id)',
         'A sourcing reference must be unique per company.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('allocation_ids.authorised_amount')
    def _compute_authorised(self):
        for event in self:
            event.authorised_amount = sum(
                event.allocation_ids.mapped('authorised_amount'))

    @api.depends('invitation_ids', 'bid_response_ids.state',
                 'invitation_ids.purchase_order_id')
    def _compute_counts(self):
        for event in self:
            event.invitation_count = len(event.invitation_ids)
            event.response_count = len(event.bid_response_ids.filtered(
                lambda r: r.state == 'received'))
            event.rfq_count = len(event.invitation_ids.purchase_order_id)

    @api.constrains('close_datetime', 'clarification_deadline')
    def _check_dates(self):
        for event in self:
            if (event.clarification_deadline and event.close_datetime
                    and event.clarification_deadline > event.close_datetime):
                raise ValidationError(_(
                    "The clarification deadline is after the closing moment, "
                    "so a question could be asked that nobody could answer in "
                    "time to bid."))

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for event in self:
            if (event.project_id and event.project_id.company_id
                    and event.project_id.company_id != event.company_id):
                raise ValidationError(_(
                    "%s belongs to another company.") % event.project_id.display_name)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                company = vals.get('company_id') or self.env.company.id
                vals['name'] = self.env['ir.sequence'].with_company(
                    company).next_by_code(
                        'realestate.procurement.sourcing.event') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Authorised demand
    # ------------------------------------------------------------------
    def action_allocate_request(self, request, quantities=None):
        """Bring one approved requisition into this event.

        `quantities` optionally maps a requisition line to the quantity being
        sourced now, so half a request can go to one tender and half to
        another. Whatever is taken keeps the line's project, WBS and cost code:
        a tender may present one 100-unit line to the market, but 50 of those
        units are still cost code A and 50 are still cost code B, and M7 has to
        be able to get that back.
        """
        self.ensure_one()
        self._assert_editable_basis()
        if request.state not in ('approved', 'ordered', 'partially_ordered'):
            raise UserError(_(
                "%s is not approved demand, so it cannot authorise a tender.")
                % request.display_name)
        if request.company_id != self.company_id:
            raise UserError(_("%s belongs to another company.")
                            % request.display_name)
        if self.project_id and request.project_id != self.project_id:
            raise UserError(_(
                "%s is demand for %s, and this event sources %s.") % (
                    request.display_name,
                    request.project_id.display_name or _('no project'),
                    self.project_id.display_name))

        Allocation = self.env[
            'realestate.procurement.sourcing.demand.allocation']
        created = Allocation
        for line in request.line_ids:
            quantity = (quantities or {}).get(line.id, line.qty)
            if not quantity:
                continue
            remaining = line.qty - Allocation._sourced_quantity(line)
            if quantity > remaining + 1e-6:
                raise UserError(_(
                    "%(line)s has %(remaining)s left to source and this event "
                    "asks for %(asked)s. Sourcing the same demand twice would "
                    "put one requirement in two tenders.",
                    line=line.display_name, remaining=remaining,
                    asked=quantity))
            sourcing_line = self._line_for(line)
            created |= Allocation.create({
                'event_id': self.id,
                'sourcing_line_id': sourcing_line.id,
                'request_id': request.id,
                'request_line_id': line.id,
                'quantity': quantity,
                'authorised_amount': quantity * (line.estimated_unit_cost or 0.0),
            })
        if not created:
            raise UserError(_("%s has nothing left to source.")
                            % request.display_name)
        return created

    def _line_for(self, request_line):
        """One tender line per product/description, quantities accumulated."""
        self.ensure_one()
        Line = self.env['realestate.procurement.sourcing.line']
        label = (request_line.description
                 or request_line.product_id.display_name or _('Scope'))
        existing = self.line_ids.filtered(
            lambda l, rl=request_line, lb=label: (
                l.product_id == rl.product_id
                and (bool(l.product_id) or l.name == lb)))
        if existing:
            line = existing[0]
            line.quantity += request_line.qty
            return line
        return Line.create({
            'event_id': self.id,
            'name': label,
            'product_id': request_line.product_id.id,
            'product_uom_id': request_line.uom_id.id,
            'quantity': request_line.qty,
            'required_date': request_line.required_on_site_date,
        })

    # ------------------------------------------------------------------
    # Invitations
    # ------------------------------------------------------------------
    def action_invite_vendor(self, vendor, contact=None):
        """Invite one vendor: ask M4 first, then create the native RFQ."""
        self.ensure_one()
        if self.state in ('closed', 'cancelled'):
            raise UserError(_(
                "%s is %s. Inviting a vendor now would be inviting them to "
                "something that is over.") % (self.name,
                                              dict(self._fields['state'].selection)[self.state]))
        if self.invitation_ids.filtered(lambda i: i.partner_id == vendor):
            raise UserError(_("%s is already invited to %s.")
                            % (vendor.display_name, self.name))
        return self.env['realestate.procurement.sourcing.invitation'].create({
            'event_id': self.id,
            'partner_id': vendor.id,
            'contact_id': contact.id if contact else False,
        })

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_submit_review(self):
        for event in self:
            if event.state != 'draft':
                raise UserError(_("Only a draft event can be sent for review."))
            event.state = 'review'
        return True

    def action_publish(self):
        """Issue Rev 0 and freeze it.

        Publication is the moment the ask stops being ours to edit quietly.
        Everything after it goes through an addendum, which is a new version
        with a reason and an issuer — not a silent rewrite of what vendors
        already priced.
        """
        for event in self:
            # C1 — two buyers pressing Publish at the same instant must not
            # both mint a Rev 0. The row lock is taken before the state is
            # re-read, so the loser sees `published` and is refused rather
            # than creating a second original issue.
            self.env.cr.execute(
                'SELECT id FROM realestate_procurement_sourcing_event '
                'WHERE id = %s FOR UPDATE', (event.id,))
            event.invalidate_recordset(['state', 'current_version_id'])
            if event.state not in ('draft', 'review'):
                raise UserError(_("%s is already published or closed.")
                                % event.name)
            if not event.line_ids:
                raise UserError(_(
                    "%s has no scope. Publishing an empty tender asks vendors "
                    "to quote nothing.") % event.name)
            if not event.allocation_ids:
                raise UserError(_(
                    "%s consumes no authorised demand. A tender with no "
                    "approved requisition behind it has no authority.")
                    % event.name)
            now = fields.Datetime.now()
            version = event._issue_version(
                reason=_("Original issue."), issued_on=now)
            event.write({
                'state': 'published',
                'issue_datetime': now,
                'original_close_datetime': (event.original_close_datetime
                                            or event.close_datetime),
                'current_version_id': version.id,
            })
            event.invitation_ids.filtered(
                lambda i: i.state == 'draft')._issue(version)
            event.message_post(body=_("Published as %s.") % version.display_name)
        return True

    def action_close(self):
        """Bidding is finished. Nothing here is evaluated or awarded.

        C4 — idempotent. Closing an already-closed tender is a no-op that
        writes nothing and posts nothing: two clicks, or two users, must not
        produce two closing entries in the audit trail with different
        timestamps, because then neither is the moment bidding ended.
        """
        for event in self:
            self.env.cr.execute(
                'SELECT id FROM realestate_procurement_sourcing_event '
                'WHERE id = %s FOR UPDATE', (event.id,))
            event.invalidate_recordset(['state'])
            if event.state == 'closed':
                continue
            if event.state != 'published':
                raise UserError(_("Only a published event can be closed."))
            event.write({'state': 'closed',
                         'actual_closed_datetime': fields.Datetime.now()})
            event.message_post(body=_(
                "Closed with %s response(s). Closing ends bidding; it does "
                "not evaluate or award anything.") % event.response_count)
            # A vendor who never answered is only "no response" once the
            # tender is actually shut. The status is computed, so nothing is
            # written here — but the responses held open by an unacknowledged
            # addendum are re-assessed now that no more can arrive.
            event.bid_response_ids.filtered(
                lambda r: r.state == 'received')._assess_administrative()
        return True

    # ------------------------------------------------------------------
    def _classify_evaluation_readiness(self):
        """Describe this tender's evaluation status. Never create one.

        Read-only by construction: every branch looks at records that already
        exist. Nothing here scores a bid, and a tender that was evaluated
        outside ATMTA is labelled as exactly that rather than being given a
        fabricated internal history.
        """
        self.ensure_one()
        if self.evaluation_round_ids.filtered(
                lambda r: r.state == 'finalised'):
            return 'evaluated'
        if self.evaluation_round_ids:
            return 'evaluation_candidate'
        if self.state in ('draft', 'review', 'published'):
            return 'open_not_ready'
        if self.state == 'cancelled':
            return 'ambiguous'
        # Closed. Is there anything an evaluation could work on?
        evaluable = self.bid_response_ids.filtered(
            lambda r: r.state == 'received' and r.is_evaluable)
        if evaluable:
            return 'closed_unevaluated'
        if self.bid_response_ids:
            return 'legacy_external_evaluation'
        return 'ambiguous'

    def _classify_award_readiness(self):
        """Describe this tender's award status. Never create one.

        Read-only by construction: every branch looks at records that already
        exist. Nothing here awards anything, and a tender whose orders were
        confirmed without an award is labelled as exactly that rather than
        being given a retrospective authorisation — which would be forging the
        signature the control exists to require.
        """
        self.ensure_one()
        if self.state == 'cancelled':
            return 'cancelled'
        awards = self.award_ids.sudo()
        live = awards.filtered(
            lambda a: a.state != 'cancelled' and not a.superseded)
        if live.filtered(lambda a: a.state == 'issued'):
            return 'awarded'
        if live.filtered(lambda a: a.state == 'approved'):
            return 'award_approved'
        if live.filtered(lambda a: a.state == 'review'):
            return 'award_in_review'
        if live.filtered(lambda a: a.state == 'draft'):
            return 'award_drafted'

        # No live award. Did anything commit anyway?
        committed = self.invitation_ids.sudo().mapped(
            'purchase_order_id').filtered(
                lambda o: o.state in ('purchase', 'done'))
        if committed:
            return 'committed_without_award'
        if self.evaluation_round_ids.sudo().filtered(
                lambda r: r.state == 'finalised'):
            return 'awaiting_award'
        return 'not_evaluated'

    def addendum_ack_policy_effective(self):
        """Event override, else company default, else optional."""
        self.ensure_one()
        if self.addendum_ack_policy and self.addendum_ack_policy != 'company':
            return self.addendum_ack_policy
        return (self.company_id.procurement_addendum_ack_policy
                or 'optional')

    def action_cancel(self):
        for event in self:
            event.state = 'cancelled'
            orders = event.invitation_ids.purchase_order_id.filtered(
                lambda o: o.state in ('draft', 'sent'))
            orders.button_cancel()
        return True

    # ------------------------------------------------------------------
    # Versions and addenda
    # ------------------------------------------------------------------
    def _issue_version(self, reason, issued_on=None, close_datetime=None):
        self.ensure_one()
        Version = self.env['realestate.procurement.sourcing.version']
        previous = self.current_version_id
        version = Version.create({
            'event_id': self.id,
            'revision': (previous.revision + 1) if previous else 0,
            'reason': reason,
            'supersedes_id': previous.id if previous else False,
            'effective_close_datetime': close_datetime or self.close_datetime,
        })
        version._issue(issued_on or fields.Datetime.now())
        if previous:
            previous._supersede()
        return version

    def action_issue_addendum(self, reason, close_datetime=None,
                              clarification=None):
        """A material change to a published tender — Rev N+1, never an edit."""
        self.ensure_one()
        if self.state != 'published':
            raise UserError(_(
                "An addendum changes what vendors are quoting. %s is not "
                "published, so there is nothing to amend.") % self.name)
        if not reason:
            raise UserError(_(
                "An addendum without a reason is an unexplained change to a "
                "published basis."))
        # Serialise: two buyers issuing Rev 1 at once must not both succeed.
        self.env.cr.execute(
            'SELECT id FROM realestate_procurement_sourcing_event '
            'WHERE id = %s FOR UPDATE NOWAIT', (self.id,))
        if close_datetime:
            self.close_datetime = close_datetime
        version = self._issue_version(reason=reason,
                                      close_datetime=close_datetime)
        self.current_version_id = version.id
        self.invitation_ids._require_acknowledgement(version)
        if clarification:
            clarification.addendum_version_id = version.id
        self.message_post(body=_("Addendum issued as %(version)s: %(reason)s",
                                 version=version.display_name, reason=reason))
        return version

    # ------------------------------------------------------------------
    def _assert_editable_basis(self):
        self.ensure_one()
        if self.state in PUBLISHED_STATES:
            raise UserError(_(
                "%s is published. Its basis changes by addendum, so that "
                "vendors are told what changed and when.") % self.name)
        if self.state == 'cancelled':
            raise UserError(_("%s is cancelled.") % self.name)


class SourcingVersion(models.Model):
    _name = 'realestate.procurement.sourcing.version'
    _description = 'Tender Version'
    _order = 'event_id, revision desc'

    event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='event_id.company_id', store=True, index=True)
    revision = fields.Integer(required=True, readonly=True)
    reason = fields.Text(readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('superseded', 'Superseded'),
    ], default='draft', required=True, readonly=True, copy=False)

    issued_on = fields.Datetime(readonly=True, copy=False)
    published_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    effective_close_datetime = fields.Datetime(readonly=True)
    supersedes_id = fields.Many2one(
        'realestate.procurement.sourcing.version', readonly=True)

    #: C2 — the database, not the application, is what stops two Rev 1s.
    #: Two transactions can both read "current is Rev 0" before either
    #: commits; only a unique index makes the second one fail.
    _sql_constraints = [
        ('revision_uniq', 'unique(event_id, revision)',
         'A tender revision number is issued once per sourcing event.'),
    ]
    terms_snapshot = fields.Text(
        readonly=True,
        help="What the scope said at the moment this version was issued. "
             "Held as text on purpose: it is evidence, and evidence that "
             "recomputes itself from today's records is not evidence.")

    def _issue(self, issued_on):
        for version in self:
            version._engine().write({
                'state': 'issued',
                'issued_on': issued_on,
                'published_by_id': self.env.user.id,
                'terms_snapshot': version._render_snapshot(),
            })
        return True

    def _supersede(self):
        self._engine().write({'state': 'superseded'})

    def _render_snapshot(self):
        self.ensure_one()
        lines = []
        for line in self.event_id.line_ids:
            lines.append('%s | %s | %s %s | required %s' % (
                line.sequence, line.name, line.quantity,
                line.product_uom_id.display_name or '', line.required_date or '-'))
        return '\n'.join([
            _('Event: %s') % self.event_id.name,
            _('Title: %s') % (self.event_id.title or ''),
            _('Method: %s') % (self.event_id.sourcing_method or ''),
            _('Effective close: %s') % (self.effective_close_datetime or ''),
            _('Scope:'),
        ] + lines)

    def name_get(self):
        return [(v.id, 'Rev %s' % v.revision) for v in self]

    @api.depends('revision')
    def _compute_display_name(self):
        for version in self:
            version.display_name = 'Rev %s' % version.revision

    def write(self, vals):
        """Issued versions are evidence, and evidence does not change."""
        if not self.env.context.get('re_sourcing_engine'):
            for version in self:
                if version.state in ISSUED_STATES:
                    raise UserError(_(
                        "Rev %s was issued on %s. Vendors quoted against it. "
                        "Change the tender by addendum instead.") % (
                            version.revision, version.issued_on))
        return super().write(vals)

    def unlink(self):
        for version in self:
            if version.state in ISSUED_STATES:
                raise UserError(_(
                    "Rev %s was issued and cannot be deleted.")
                    % version.revision)
        return super().unlink()

    def _engine(self):
        """Named, not an override of `sudo()` — see `bid_response._engine`."""
        return self.sudo().with_context(re_sourcing_engine=True)


class SourcingLine(models.Model):
    _name = 'realestate.procurement.sourcing.line'
    _description = 'Tender Line'
    _order = 'event_id, sequence, id'

    event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='event_id.company_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Text(
        required=True,
        help="Free text on purpose: subcontract and service scope routinely "
             "has no product record, and requiring one would push buyers into "
             "inventing catalogue items to run a tender.")
    product_id = fields.Many2one('product.product')
    product_uom_id = fields.Many2one('uom.uom')
    quantity = fields.Float(default=1.0, digits='Product Unit of Measure')
    procurement_type = fields.Selection([
        ('material', 'Material'),
        ('service', 'Service'),
        ('subcontract', 'Subcontract'),
        ('equipment', 'Equipment'),
    ])
    specification = fields.Text()
    required_date = fields.Date()
    allocation_ids = fields.One2many(
        'realestate.procurement.sourcing.demand.allocation',
        'sourcing_line_id')
    notes = fields.Text()

    def write(self, vals):
        if not self.env.context.get('re_sourcing_engine'):
            for line in self:
                if line.event_id.state in PUBLISHED_STATES:
                    line.event_id._assert_editable_basis()
        return super().write(vals)


class SourcingDemandAllocation(models.Model):
    _name = 'realestate.procurement.sourcing.demand.allocation'
    _description = 'Tender Demand Allocation'
    _order = 'event_id, id'

    event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='event_id.company_id', store=True, index=True)
    sourcing_line_id = fields.Many2one(
        'realestate.procurement.sourcing.line', ondelete='cascade', index=True)
    request_id = fields.Many2one(
        'realestate.material.request', required=True, index=True)
    request_line_id = fields.Many2one(
        'realestate.material.request.line', required=True, index=True)
    reservation_id = fields.Many2one(
        'realestate.procurement.reservation',
        compute='_compute_reservation', store=True, index=True,
        help="The reservation this demand already holds. The event reads it; "
             "it never creates, grows or releases one.")

    # Financial lineage — the reason this model exists.
    #
    # WBS and cost code are **not** mirrored here. Construction adds those
    # fields to the requisition line and to the reservation when it is
    # installed, and it is frozen; a related field to a column that may not
    # exist would make this module refuse to load standalone. The lineage is
    # not lost by that: `request_line_id` and `reservation_id` are the
    # authoritative carriers, and `_coding()` reads whatever coding exists at
    # the moment M7 asks. One tender line consolidating fifty units of cost
    # code A and fifty of cost code B keeps two allocation rows, so the split
    # survives the consolidation.
    project_id = fields.Many2one(
        related='request_line_id.project_id', store=True, index=True)
    coding_display = fields.Char(compute='_compute_coding_display')
    quantity = fields.Float(required=True, digits='Product Unit of Measure')
    authorised_amount = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one(related='event_id.currency_id', store=True)

    @api.depends('request_line_id')
    def _compute_reservation(self):
        for allocation in self:
            allocation.reservation_id = allocation.request_line_id.sudo(
            ).reservation_ids.filtered(
                lambda r: r.state == 'reserved')[:1]

    def _compute_coding_display(self):
        for allocation in self:
            allocation.coding_display = ' / '.join(
                part for part in allocation._coding().values() if part) or '-'

    def _coding(self):
        """The financial coding this allocation carries, whatever exists.

        Read rather than mirrored, so that a database without Construction
        answers "project only" instead of failing to load.
        """
        self.ensure_one()
        line = self.request_line_id.sudo()
        coding = {'project': line.project_id.display_name or ''}
        for field in ('wbs_id', 'cost_code_id'):
            if field in line._fields and line[field]:
                coding[field] = line[field].display_name
        return coding

    @api.model
    def _sourced_quantity(self, request_line):
        """How much of a requisition line is already in some live event."""
        groups = self.sudo()._read_group(
            [('request_line_id', '=', request_line.id),
             ('event_id.state', 'not in', ('cancelled',))],
            aggregates=['quantity:sum'])
        return groups[0][0] or 0.0 if groups else 0.0
