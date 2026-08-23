# -*- coding: utf-8 -*-
"""M5 — bid responses: immutable evidence, taken from a mutable RFQ.

The native RFQ stays operational and editable, and that is correct — a buyer
re-prices, re-quantifies and compares on it all day. What it cannot be is the
record of what a vendor submitted.

M5.0 established this from installed source rather than from caution:

```python
    # purchase_requisition/models/purchase.py
    def action_choose(self):
        order_lines = (self.order_id | self.order_id.alternative_po_ids).mapped('order_line')
        order_lines = order_lines.filtered(lambda l: l.product_qty and ...)
        return order_lines.action_clear_quantities()      # write({'product_qty': 0})
```

Choosing one line in Odoo's compare view writes `product_qty = 0` on every
competing alternative line. If "what Vendor B bid" were read back from
`purchase.order.line`, a buyer comparing offers would silently rewrite Vendor B
into having bid nothing — and the tender file would say so afterwards, with a
straight face.

So receipt takes a snapshot. Once `received`, the response and its lines refuse
to be written.

### The deadline a bid is judged against — M5.7

Lateness is measured against **the version the bid answers**, never against
whatever deadline the event currently shows. A bid submitted on 9 August under
a Rev 0 closing on the 10th does not become early because a later addendum
moved the close to the 12th, and it does not become late because a subsequent
one moved it earlier. The deadline in force at submission is copied onto the
response and every later question is answered from that copy.

Boundary rule, stated once and tested at the instant:

```
    received <= effective close   ON TIME
    received >  effective close   LATE
```

Inclusive, because a deadline of 12:00 in a tender document means submissions
are accepted *up to* 12:00. PostgreSQL stores `timestamp` to microseconds and
Odoo truncates to the second on write, so the boundary is tested at the
precision that actually survives storage rather than at an imaginary one.

### Late is administrative — M5.8

A late bid is late. It is not technically non-compliant, not disqualified on
merit and not worse value; M6 judges merit and has not run yet. The three
policies decide whether a late submission may become *evaluable*, and every
one of them records what happened rather than quietly reclassifying it.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: States in which a response is evidence rather than a draft.
SEALED_STATES = ('received', 'superseded', 'withdrawn')

#: Bookkeeping the engine may still write after sealing. Everything else is
#: refused: these change what the record *is*, never what the vendor said.
BOOKKEEPING = {'state', 'previous_response_id', 'superseded_by_id',
               'withdrawn_on', 'withdrawal_reason', 'withdrawn_by_id',
               'withdrawn_before_close', 'administrative_status',
               'is_evaluable', 'completeness_notes', 'late_reason',
               'exception_approved_by_id', 'exception_approved_on',
               'exception_reason', 'message_ids', 'message_follower_ids',
               'activity_ids'}


class BidResponse(models.Model):
    _name = 'realestate.procurement.bid.response'
    _description = 'Tender Bid Response'
    _inherit = ['mail.thread']
    _order = 'invitation_id, revision desc'

    name = fields.Char(readonly=True, copy=False, default=lambda s: _('New'))
    event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True, readonly=True)
    company_id = fields.Many2one(
        related='event_id.company_id', store=True, index=True)
    invitation_id = fields.Many2one(
        'realestate.procurement.sourcing.invitation', required=True,
        ondelete='cascade', index=True, readonly=True)
    partner_id = fields.Many2one(
        related='invitation_id.partner_id', store=True, index=True,
        string='Vendor')
    version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', readonly=True,
        string='Tender Version',
        help="Which version of the ask this offer answers. A bid revision and "
             "a tender revision are different numbers and are kept apart.")
    purchase_order_id = fields.Many2one(
        'purchase.order', readonly=True, string='Source RFQ',
        help="Where the snapshot was taken from. A reference for audit — the "
             "amounts below never follow it afterwards.")

    revision = fields.Integer(readonly=True, default=0, string='Bid Revision')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('received', 'Received'),
        ('superseded', 'Superseded'),
        ('withdrawn', 'Withdrawn'),
    ], default='draft', required=True, readonly=True, copy=False,
        tracking=True)
    previous_response_id = fields.Many2one(
        'realestate.procurement.bid.response', readonly=True)
    superseded_by_id = fields.Many2one(
        'realestate.procurement.bid.response', readonly=True)

    received_datetime = fields.Datetime(readonly=True, copy=False)
    recorded_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    vendor_reference = fields.Char(readonly=True, copy=False)
    currency_id = fields.Many2one('res.currency', readonly=True)
    amount_untaxed = fields.Monetary(
        readonly=True, currency_field='currency_id',
        string='Submitted Amount (raw)',
        help="Exactly what the vendor submitted, in the currency they "
             "submitted it. Not normalised, not converted and not comparable "
             "across currencies — normalisation is M6.")
    validity_date = fields.Date(readonly=True)

    # -- M5.7 / M5.8 — lateness, as it was judged at receipt ------------
    deadline_applied = fields.Datetime(
        readonly=True, copy=False, string='Deadline Applied',
        help="The effective close of the tender version this bid answers, "
             "copied at receipt. Later addenda do not move it.")
    is_late = fields.Boolean(readonly=True, copy=False)
    lateness_seconds = fields.Integer(readonly=True, copy=False)
    policy_applied = fields.Selection([
        ('reject', 'Reject'),
        ('exception_required', 'Manager Exception Required'),
        ('allow_with_warning', 'Accept and Flag'),
    ], readonly=True, copy=False,
        help="The late-bid policy in force when this was received. Kept so "
             "the decision stays explainable after the policy changes.")
    exception_required = fields.Boolean(readonly=True, copy=False)
    exception_approved_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False)
    exception_approved_on = fields.Datetime(readonly=True, copy=False)
    exception_reason = fields.Text(copy=False)
    late_reason = fields.Text(copy=False)

    # -- M5.13 — administrative standing, never technical ---------------
    administrative_status = fields.Selection([
        ('pending_review', 'Pending Review'),
        ('complete', 'Administratively Complete'),
        ('incomplete', 'Incomplete'),
        ('late_exception_pending', 'Late — Exception Pending'),
        ('late_rejected', 'Late — Rejected by Policy'),
        ('resubmission_required', 'Resubmission Required'),
        ('withdrawn', 'Withdrawn'),
    ], default='pending_review', copy=False, tracking=True, readonly=True,
        help="Administrative standing only. Nothing here says whether the "
             "offer is any good: no technical result, no commercial ranking "
             "and no recommendation. That is M6.")
    is_evaluable = fields.Boolean(
        readonly=True, copy=False,
        help="May M6 evaluate this submission at all. A false value is an "
             "administrative statement — late and unexcused, superseded, "
             "withdrawn or answering a superseded basis — never a judgement "
             "about the offer.")
    completeness_notes = fields.Text(readonly=True, copy=False)
    above_authorisation = fields.Boolean(
        compute='_compute_above_authorisation', store=True,
        help="Submitted more than the authorised demand behind this tender. "
             "Flagged, never accommodated: the authorisation does not grow "
             "because the market is expensive.")

    line_ids = fields.One2many(
        'realestate.procurement.bid.response.line', 'response_id',
        readonly=True)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'bid_response_attachment_rel', 'response_id',
        'attachment_id', string='Bid Documents')
    attachment_count = fields.Integer(compute='_compute_attachment_count')
    withdrawn_on = fields.Datetime(readonly=True, copy=False)
    withdrawn_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    withdrawn_before_close = fields.Boolean(readonly=True, copy=False)
    withdrawal_reason = fields.Text(copy=False)
    notes = fields.Text()

    _sql_constraints = [
        ('revision_uniq', 'unique(invitation_id, revision)',
         'A bid revision number is used once per invitation.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('amount_untaxed', 'event_id.authorised_amount')
    def _compute_above_authorisation(self):
        for response in self:
            authorised = response.event_id.authorised_amount
            response.above_authorisation = bool(
                authorised and response.amount_untaxed > authorised)

    def _compute_attachment_count(self):
        for response in self:
            response.attachment_count = len(response.attachment_ids)

    # ------------------------------------------------------------------
    # Receipt
    # ------------------------------------------------------------------
    @api.model
    def _record(self, invitation, received_datetime=None,
                vendor_reference=None, validity_date=None, attachments=None,
                late_reason=None):
        """Seal what the vendor submitted, at the moment it was submitted."""
        event = invitation.event_id
        if event.state not in ('published', 'closed'):
            raise UserError(_(
                "%s is not published, so nothing can be submitted against "
                "it.") % event.name)
        order = invitation.purchase_order_id
        if not order:
            raise UserError(_("%s has no RFQ to take a submission from.")
                            % invitation.partner_id.display_name)
        if invitation.state == 'declined':
            raise UserError(_(
                "%s declined this tender. Recording a bid for them would "
                "contradict their own answer.")
                % invitation.partner_id.display_name)

        # C3/C6 — one authoritative current revision, and one exact version.
        # The invitation row is locked before the revision number is read, so
        # two concurrent recordings cannot both decide they are Rev 1, and the
        # version is captured inside the same lock so a bid cannot end up with
        # one version's lines and another's deadline.
        self.env.cr.execute(
            'SELECT id FROM realestate_procurement_sourcing_invitation '
            'WHERE id = %s FOR UPDATE', (invitation.id,))
        invitation.invalidate_recordset(
            ['current_response_id', 'version_id', 'state'])

        received = received_datetime or fields.Datetime.now()
        if isinstance(received, str):
            received = fields.Datetime.to_datetime(received)
        previous = invitation.current_response_id
        version = invitation.version_id or event.current_version_id
        deadline = (version.effective_close_datetime
                    or event.close_datetime)
        # M5.7 — inclusive boundary. `<=` is on time.
        late = bool(deadline and received > deadline)
        lateness = int((received - deadline).total_seconds()) if late else 0
        policy = event.company_id.procurement_late_bid_policy or 'reject'

        response = self.sudo().create({
            'name': self.env['ir.sequence'].with_company(
                event.company_id).next_by_code(
                    'realestate.procurement.bid.response') or _('New'),
            'event_id': event.id,
            'invitation_id': invitation.id,
            'version_id': version.id,
            'purchase_order_id': order.id,
            'revision': (previous.revision + 1) if previous else 0,
            'previous_response_id': previous.id if previous else False,
            'received_datetime': received,
            'recorded_by_id': self.env.user.id,
            'vendor_reference': vendor_reference,
            'currency_id': order.currency_id.id,
            'amount_untaxed': order.amount_untaxed,
            'validity_date': validity_date or False,
            'deadline_applied': deadline,
            'is_late': late,
            'lateness_seconds': lateness,
            'policy_applied': policy if late else False,
            'late_reason': late_reason,
            'line_ids': [(0, 0, values)
                         for values in self._line_values(invitation, order)],
        })
        if attachments:
            response._attach(attachments)
        response._engine().write({'state': 'received'})
        response._assess_administrative()
        if previous:
            previous._engine().write({'state': 'superseded',
                                      'superseded_by_id': response.id})
        invitation.sudo().write({'current_response_id': response.id,
                                 'state': 'responded',
                                 'resubmission_required': False})
        response.message_post(body=_(
            "Bid Rev %(rev)s recorded at %(when)s against %(version)s.",
            rev=response.revision, when=received,
            version=version.display_name or ''))
        # Hand back a plain handle. Returning the engine's own recordset
        # would give every caller a record that quietly ignores the seal.
        return response.with_context(re_bid_engine=False)

    @api.model
    def _line_values(self, invitation, order):
        values = []
        for line in order.order_line.filtered(lambda l: not l.display_type):
            sourcing_line = line.re_sourcing_line_id
            values.append({
                'sourcing_line_id': sourcing_line.id if sourcing_line else False,
                'purchase_order_line_id': line.id,
                'name': line.name,
                'product_id': line.product_id.id,
                'quantity': line.product_qty,
                'product_uom_id': line.product_uom.id,
                'price_unit': line.price_unit,
                'discount': getattr(line, 'discount', 0.0),
                'amount_untaxed': line.price_subtotal,
                'promised_date': line.date_planned,
            })
        return values

    def _attach(self, attachments):
        """Bind documents to the response, and to its access rules.

        Odoo decides attachment access from `res_model`/`res_id`, so an
        attachment left floating is readable by anyone who can guess its id.
        Stamping it onto the response means the bid's own record rules — which
        do not admit Requesters at all — govern the document too.
        """
        self.ensure_one()
        attachments.sudo().write({
            'res_model': self._name,
            'res_id': self.id,
        })
        self._engine().write({'attachment_ids': [(6, 0, attachments.ids)]})
        return True

    # ------------------------------------------------------------------
    # M5.13 — administrative completeness
    # ------------------------------------------------------------------
    def _assess_administrative(self):
        """Administrative standing, and whether M6 may look at it.

        Everything checked here is procedural: does a bid exist, does it
        answer the current basis, was it in time, were mandatory addenda
        acknowledged, is required paperwork present. Nothing inspects the
        offer.
        """
        for response in self:
            notes = []
            status = 'complete'
            evaluable = True
            event = response.event_id
            invitation = response.invitation_id

            if response.state == 'withdrawn':
                status, evaluable = 'withdrawn', False
                notes.append(_("Withdrawn on %s.") % response.withdrawn_on)
            elif response.state == 'superseded':
                evaluable = False
                notes.append(_("Superseded by a later revision."))

            if response.is_late and evaluable:
                policy = response.policy_applied
                if policy == 'reject':
                    status, evaluable = 'late_rejected', False
                    notes.append(_(
                        "Received %(secs)s second(s) after the deadline. "
                        "Company policy does not accept late submissions, so "
                        "this is kept as evidence and is not evaluable.",
                        secs=response.lateness_seconds))
                elif policy == 'exception_required' \
                        and not response.exception_approved_on:
                    status, evaluable = 'late_exception_pending', False
                    response._engine().write({'exception_required': True})
                    notes.append(_(
                        "Received after the deadline. A Procurement Manager "
                        "must accept it by exception before it can be "
                        "evaluated."))
                else:
                    notes.append(_("Received after the deadline and accepted "
                                   "under policy. It stays marked late."))

            # M5.12 — a mandatory addendum that was never acknowledged.
            if evaluable and event.addendum_ack_policy_effective() == \
                    'required_before_response':
                outstanding = invitation._unacknowledged_versions()
                if outstanding:
                    status, evaluable = 'incomplete', False
                    notes.append(_(
                        "Acknowledgement of %s has not been recorded, and "
                        "company policy requires it before a response is "
                        "valid.") % ', '.join(
                            outstanding.mapped('display_name')))

            if evaluable and response.version_id != event.current_version_id:
                status, evaluable = 'resubmission_required', False
                notes.append(_(
                    "Answers %(had)s; the tender is now at %(now)s. The offer "
                    "stands as submitted — a new revision is needed against "
                    "the current basis.",
                    had=response.version_id.display_name or '-',
                    now=event.current_version_id.display_name or '-'))

            if evaluable and event.require_validity_date \
                    and not response.validity_date:
                status, evaluable = 'incomplete', False
                notes.append(_("No bid validity date was supplied."))

            if evaluable and event.require_bid_document \
                    and not response.attachment_ids:
                status, evaluable = 'incomplete', False
                notes.append(_("No submission document was attached."))

            response._engine().write({
                'administrative_status': status,
                'is_evaluable': evaluable,
                'completeness_notes': '\n'.join(notes) or _(
                    "Administratively complete. This says nothing about the "
                    "merits of the offer."),
            })
        return True

    def action_approve_late_exception(self, reason=None):
        """Accept a late submission. A named person, on the record."""
        for response in self:
            if not response.is_late:
                raise UserError(_("%s was not late.") % response.display_name)
            if not self.env.user.has_group(
                    'real_estate_procurement.group_procurement_manager'):
                raise UserError(_(
                    "Accepting a late bid is a Procurement Manager's "
                    "decision."))
            if response.recorded_by_id == self.env.user \
                    and not response.company_id.\
                    procurement_allow_self_late_exception:
                raise UserError(_(
                    "%(user)s recorded this submission. Approving your own "
                    "late exception is off by default — the point of the "
                    "exception is that somebody else agreed.",
                    user=self.env.user.display_name))
            response._engine().write({
                'exception_approved_by_id': self.env.user.id,
                'exception_approved_on': fields.Datetime.now(),
                'exception_reason': reason,
            })
            response._assess_administrative()
            response.message_post(body=_(
                "Late submission accepted by %(user)s: %(reason)s",
                user=self.env.user.display_name, reason=reason or ''))
        return True

    # ------------------------------------------------------------------
    def action_withdraw(self, reason=None):
        """The vendor pulls the offer. The offer is still what it was.

        Nothing is deleted: header, lines, attachments, timestamps and the
        revision chain all stay. What changes is that the invitation stops
        having a current response — deliberately, rather than reviving the
        superseded revision underneath. A quotation the vendor replaced is not
        an offer they are still making, and promoting it silently would put a
        price in their mouth that they withdrew.
        """
        for response in self:
            if response.state != 'received':
                raise UserError(_("Only a received bid can be withdrawn."))
            now = fields.Datetime.now()
            # "Before close" means bidding was still open when the vendor
            # pulled out. A closed tender settles that outright — comparing
            # clocks alone would call a withdrawal "before close" when it
            # landed in the same second as the close, which is precisely the
            # case somebody would later argue about.
            event = response.event_id
            deadline = response.deadline_applied
            before = (event.state != 'closed'
                      and (not deadline or now <= deadline))
            response._engine().write({
                'state': 'withdrawn',
                'withdrawn_on': now,
                'withdrawn_by_id': self.env.user.id,
                'withdrawal_reason': reason,
                'withdrawn_before_close': before,
            })
            response._assess_administrative()
            response.invitation_id.sudo().write({
                'state': 'withdrawn',
                'current_response_id': False,
            })
            response.message_post(body=_("Withdrawn: %s") % (reason or ''))
        return True

    # ------------------------------------------------------------------
    def write(self, vals):
        if not self.env.context.get('re_bid_engine'):
            touched = set(vals) - BOOKKEEPING
            for response in self:
                if response.state in SEALED_STATES and touched:
                    raise UserError(_(
                        "%(name)s is what %(vendor)s submitted at %(when)s. "
                        "It cannot be edited — record a revision instead, so "
                        "that both what was offered and what replaced it "
                        "survive.",
                        name=response.display_name,
                        vendor=response.partner_id.display_name,
                        when=response.received_datetime))
        return super().write(vals)

    def unlink(self):
        for response in self:
            if response.state in SEALED_STATES:
                raise UserError(_(
                    "%s is submitted evidence. Withdraw it — deleting it "
                    "would make the tender file unexplainable.")
                    % response.display_name)
        return super().unlink()

    def _engine(self):
        """The only handle allowed to write a sealed response.

        Deliberately a named method rather than an override of `sudo()`.
        Hanging the flag on `sudo()` would mean every unrelated elevated read
        in the system — a compute, a report, another module — silently
        acquired the right to rewrite submitted evidence. This was a real
        defect during M5 and the test that caught it is
        `test_d_a_received_bid_refuses_to_be_edited`.
        """
        return self.sudo().with_context(re_bid_engine=True)

    @api.depends('name', 'revision', 'partner_id')
    def _compute_display_name(self):
        for response in self:
            response.display_name = '%s — %s Rev %s' % (
                response.name or '', response.partner_id.display_name or '',
                response.revision)


class BidResponseLine(models.Model):
    _name = 'realestate.procurement.bid.response.line'
    _description = 'Tender Bid Response Line'
    _order = 'response_id, id'

    response_id = fields.Many2one(
        'realestate.procurement.bid.response', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='response_id.company_id', store=True, index=True)
    partner_id = fields.Many2one(related='response_id.partner_id', store=True)
    sourcing_line_id = fields.Many2one(
        'realestate.procurement.sourcing.line', readonly=True, index=True)
    purchase_order_line_id = fields.Many2one(
        'purchase.order.line', readonly=True,
        help="Audit reference only. Nothing here is computed from it.")

    #: Snapshot columns. Plain stored values, never related and never
    #: computed: a related field would follow the RFQ, which is the entire
    #: failure mode this model exists to prevent.
    name = fields.Text(readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    quantity = fields.Float(readonly=True, digits='Product Unit of Measure')
    product_uom_id = fields.Many2one('uom.uom', readonly=True)
    price_unit = fields.Float(readonly=True, digits='Product Price')
    discount = fields.Float(readonly=True)
    amount_untaxed = fields.Monetary(
        readonly=True, currency_field='currency_id')
    currency_id = fields.Many2one(
        related='response_id.currency_id', store=True)
    promised_date = fields.Datetime(readonly=True)
    deviation_notes = fields.Text()

    def write(self, vals):
        if not self.env.context.get('re_bid_engine'):
            touched = set(vals) - {'deviation_notes'}
            for line in self:
                if line.response_id.state in SEALED_STATES and touched:
                    raise UserError(_(
                        "This line is part of %s, which is submitted "
                        "evidence.") % line.response_id.display_name)
        return super().write(vals)

    def _engine(self):
        return self.sudo().with_context(re_bid_engine=True)
