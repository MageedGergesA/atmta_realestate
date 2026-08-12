# -*- coding: utf-8 -*-
"""M5 — invitations, and the native RFQ each one creates.

An invitation is the join between a vendor and a tender, and it carries the
one fact nothing else can reconstruct later: **whether that vendor was allowed
to take part on the day they were asked**.

```
    eligibility_*            what M4 answered at invitation, frozen
    current_eligibility_*    what M4 answers today, live
```

Both are true at once. A vendor eligible on 1 August and suspended on 20 August
was legitimately invited, and is legitimately refusable now; a system that kept
only one of those either rewrites history or hides a suspension.

The RFQ itself is a native `purchase.order`. M5 creates one per invited vendor
and joins them through Odoo's own alternative-RFQ group so that the native
compare view works, rather than reimplementing comparison.
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: What M4 answers when it refuses. Kept here so the refusal message can name
#: the reason instead of saying "not eligible".
BLOCKING_STATUSES = ('suspended', 'governance_inactive', 'not_qualified',
                     'expired', 'document_expired', 'endorsement_required',
                     'pending_information', 'no_qualification')


class SourcingInvitation(models.Model):
    _name = 'realestate.procurement.sourcing.invitation'
    _description = 'Tender Vendor Invitation'
    _inherit = ['mail.thread']
    _order = 'event_id, id'
    _rec_name = 'partner_id'

    event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='event_id.company_id', store=True, index=True)
    project_id = fields.Many2one(related='event_id.project_id', store=True)
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', required=True, index=True,
        domain="[('supplier_rank', '>', 0)]")
    contact_id = fields.Many2one('res.partner', string='Contact')
    version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', string='Issued Version',
        readonly=True, copy=False,
        help="The tender version this vendor was invited against.")

    invited_on = fields.Datetime(readonly=True, copy=False, tracking=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', string='RFQ', readonly=True, copy=False, index=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('invited', 'Invited'),
        ('acknowledged', 'Acknowledged'),
        ('responded', 'Responded'),
        ('declined', 'Declined'),
        ('no_bid', 'No Bid'),
        ('no_response', 'No Response'),
        ('withdrawn', 'Withdrawn'),
    ], default='draft', required=True, tracking=True, copy=False)

    # -- The snapshot. Written once, at invitation. --------------------
    eligibility_status = fields.Char(readonly=True, copy=False)
    eligibility_eligible = fields.Boolean(readonly=True, copy=False)
    eligibility_qualified = fields.Boolean(readonly=True, copy=False)
    eligibility_checked_on = fields.Date(readonly=True, copy=False)
    eligibility_policy = fields.Char(readonly=True, copy=False)
    qualification_id = fields.Many2one(
        'realestate.procurement.vendor.qualification', readonly=True,
        copy=False, ondelete='set null')
    qualification_ref = fields.Char(readonly=True, copy=False)
    qualification_valid_from = fields.Date(readonly=True, copy=False)
    qualification_valid_to = fields.Date(readonly=True, copy=False)
    eligibility_conditions = fields.Text(readonly=True, copy=False)
    eligibility_payload = fields.Text(
        readonly=True, copy=False,
        help="The complete M4 answer as it was given, kept verbatim. A "
             "summary can be re-derived from it; it cannot be re-derived from "
             "a summary.")

    # -- Live, for the buyer looking at the screen today ----------------
    current_eligibility_status = fields.Char(
        compute='_compute_current_eligibility')
    current_eligibility_eligible = fields.Boolean(
        compute='_compute_current_eligibility')
    eligibility_changed = fields.Boolean(
        compute='_compute_current_eligibility',
        help="The vendor's standing has moved since they were invited.")

    response_status = fields.Selection([
        ('awaiting', 'Awaiting Response'),
        ('responded', 'Responded'),
        ('declined', 'Declined'),
        ('no_bid', 'No Bid Returned'),
        ('no_response', 'No Response'),
        ('withdrawn', 'Withdrawn'),
    ], compute='_compute_response_status',
        help="Derived, and deliberately not the same field as `state`: "
             "`no_response` is only true once the deadline has actually "
             "passed. Before it, silence is a vendor still thinking.")
    resubmission_required = fields.Boolean(
        readonly=True, copy=False,
        help="An addendum changed the basis after this vendor responded. "
             "Their existing bid stands as submitted against the old version; "
             "a new revision is what answers the new one.")
    decline_reason_category = fields.Selection([
        ('capacity', 'Capacity'),
        ('delivery', 'Delivery / Programme'),
        ('specification', 'Specification'),
        ('commercial_terms', 'Commercial Terms'),
        ('qualification', 'Qualification'),
        ('conflict', 'Conflict of Interest'),
        ('no_interest', 'No Interest'),
        ('other', 'Other'),
    ], copy=False,
        help="Why the vendor will not bid. Recorded for the tender file "
             "only: vendor performance is M9 and nothing here feeds it.")
    decline_narrative = fields.Text(copy=False)
    recorded_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    acknowledgement_ids = fields.One2many(
        'realestate.procurement.sourcing.acknowledgement', 'invitation_id')
    acknowledged_on = fields.Datetime(readonly=True, copy=False)
    acknowledged_version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', readonly=True, copy=False)
    acknowledgement_outstanding = fields.Boolean(
        compute='_compute_acknowledgement_outstanding', store=True)
    declined_on = fields.Datetime(readonly=True, copy=False)
    no_bid_reason = fields.Text(copy=False)
    response_ids = fields.One2many(
        'realestate.procurement.bid.response', 'invitation_id')
    current_response_id = fields.Many2one(
        'realestate.procurement.bid.response', copy=False, readonly=True)
    notes = fields.Text()

    _sql_constraints = [
        ('vendor_uniq', 'unique(event_id, partner_id)',
         'A vendor is invited to a sourcing event once.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('partner_id', 'event_id.category_id', 'event_id.project_id')
    def _compute_current_eligibility(self):
        Eligibility = self.env[
            'realestate.procurement.vendor.eligibility']
        for invitation in self:
            if not invitation.partner_id:
                invitation.current_eligibility_status = False
                invitation.current_eligibility_eligible = False
                invitation.eligibility_changed = False
                continue
            outcome = Eligibility.check_vendor_eligibility(
                invitation.partner_id, company=invitation.company_id,
                category=invitation.event_id.category_id,
                project=invitation.event_id.project_id, purpose='sourcing')
            invitation.current_eligibility_status = outcome['status']
            invitation.current_eligibility_eligible = outcome['eligible']
            invitation.eligibility_changed = bool(
                invitation.eligibility_status
                and invitation.eligibility_status != outcome['status'])

    @api.depends('state', 'event_id.close_datetime', 'event_id.state',
                 'current_response_id')
    def _compute_response_status(self):
        now = fields.Datetime.now()
        for invitation in self:
            event = invitation.event_id
            closed = bool(event.close_datetime and now > event.close_datetime)
            if invitation.state == 'withdrawn':
                status = 'withdrawn'
            elif invitation.state == 'declined':
                status = 'declined'
            elif invitation.state == 'no_bid':
                status = 'no_bid'
            elif invitation.current_response_id:
                status = 'responded'
            elif closed or event.state == 'closed':
                status = 'no_response'
            else:
                status = 'awaiting'
            invitation.response_status = status

    def _unacknowledged_versions(self):
        """Issued addenda this vendor has not acknowledged.

        Rev 0 is excluded: acknowledging the original issue is what responding
        to it means. Only later versions — the ones that changed the basis
        after the vendor was already looking at it — need saying out loud.
        """
        self.ensure_one()
        acknowledged = self.acknowledgement_ids.version_id
        return self.event_id.version_ids.filtered(
            lambda v: v.state in ('issued', 'superseded')
            and v.revision > 0
            and v not in acknowledged)

    @api.depends('acknowledged_version_id', 'event_id.current_version_id',
                 'state')
    def _compute_acknowledgement_outstanding(self):
        for invitation in self:
            current = invitation.event_id.current_version_id
            invitation.acknowledgement_outstanding = bool(
                current and invitation.state not in
                ('draft', 'declined', 'withdrawn')
                and invitation.acknowledged_version_id != current)

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Ask M4 before the vendor exists on this tender at all.

        The check is here rather than in the action so that an invitation
        created over RPC, by an import or by another module answers the same
        question. A refused invitation leaves nothing behind — no record, no
        RFQ — because a governance refusal that still created the artefact
        would be a warning wearing a refusal's clothes.
        """
        invitations = super().create(vals_list)
        for invitation in invitations:
            invitation._snapshot_eligibility()
            invitation._assert_may_participate()
            invitation._create_rfq()
        return invitations

    def _snapshot_eligibility(self):
        self.ensure_one()
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        outcome = Eligibility.check_vendor_eligibility(
            self.partner_id, company=self.company_id,
            category=self.event_id.category_id,
            project=self.event_id.project_id, purpose='sourcing')
        snapshot = Eligibility.eligibility_snapshot(outcome)
        conditions = '\n'.join(
            '%s: %s' % (c['type'], c['name']) for c in snapshot['conditions'])
        self.sudo().write({
            'eligibility_status': snapshot['status'],
            'eligibility_eligible': snapshot['eligible'],
            'eligibility_qualified': snapshot['qualified'],
            'eligibility_checked_on': snapshot['as_of'],
            'eligibility_policy': snapshot['policy'],
            'qualification_id': snapshot['qualification_id'] or False,
            'qualification_ref': snapshot['qualification_ref'],
            'qualification_valid_from': snapshot['valid_from'] or False,
            'qualification_valid_to': snapshot['valid_to'] or False,
            'eligibility_conditions': conditions,
            'eligibility_payload': json.dumps(snapshot, default=str,
                                              sort_keys=True, indent=1),
        })
        return snapshot

    def _assert_may_participate(self):
        self.ensure_one()
        if self.eligibility_eligible:
            return True
        payload = json.loads(self.eligibility_payload or '{}')
        reasons = payload.get('blocking_reasons') or []
        raise UserError(_(
            "%(vendor)s cannot be invited to %(event)s.\n\n%(why)s\n\n"
            "Vendor qualification policy is %(policy)s. Lift the restriction "
            "or qualify the vendor — an invitation is a governance act, not a "
            "convenience.",
            vendor=self.partner_id.display_name,
            event=self.event_id.name,
            why='\n'.join('- %s' % reason for reason in reasons)
            or _("- Not eligible (%s).") % self.eligibility_status,
            policy=self.eligibility_policy or 'optional'))

    # ------------------------------------------------------------------
    def _create_rfq(self):
        """One native RFQ, joined into the event's native alternative group.

        `origin_po_id` in the context is Odoo's own hook: `purchase.order`
        `create()` in `purchase_requisition` reads it and either joins the
        origin's `purchase.order.group` or creates one. Using it means the
        group is built the way the native compare view expects rather than by
        us writing to a technical model directly.
        """
        self.ensure_one()
        event = self.event_id
        siblings = event.invitation_ids.purchase_order_id.filtered(
            lambda o: o.state in ('draft', 'sent'))
        order_vals = {
            'partner_id': self.partner_id.id,
            'company_id': event.company_id.id,
            'origin': event.name,
            're_project_id': event.project_id.id,
            're_sourcing_event_id': event.id,
            're_sourcing_invitation_id': self.id,
            'order_line': [(0, 0, values) for values in self._rfq_line_values()],
        }
        order = self.env['purchase.order'].with_company(event.company_id)
        if siblings:
            order = order.with_context(origin_po_id=siblings[0].id)
        self.sudo().purchase_order_id = order.create(order_vals).id
        return self.purchase_order_id

    def _rfq_line_values(self):
        """RFQ lines from the tender scope, priced at nothing.

        Quantity comes from the tender line and the price is left at zero: the
        vendor's number is the vendor's to give. Seeding a price from
        `seller_ids` would put our guess in the vendor's mouth, which is the
        M2 finding that removed `seller_ids[0]` from sourcing in the first
        place.
        """
        self.ensure_one()
        values = []
        for line in self.event_id.line_ids:
            product = line.product_id
            values.append({
                'product_id': product.id or False,
                'name': line.name,
                'product_qty': line.quantity,
                'product_uom': (line.product_uom_id.id
                                or product.uom_po_id.id
                                or product.uom_id.id),
                'price_unit': 0.0,
                'date_planned': fields.Datetime.now(),
                're_sourcing_line_id': line.id,
            })
        if not values:
            raise UserError(_(
                "%s has no scope, so there is nothing to ask a vendor to "
                "quote.") % self.event_id.name)
        return values

    # ------------------------------------------------------------------
    def _issue(self, version):
        """Publication ties every draft invitation to the issued version."""
        for invitation in self:
            invitation.sudo().write({
                'state': 'invited',
                'version_id': version.id,
                'invited_on': fields.Datetime.now(),
            })
        return True

    def _require_acknowledgement(self, version):
        """An addendum invalidates the acknowledgement of the old basis.

        A vendor who already answered is marked `resubmission_required`. Their
        existing bid is **not** touched, copied or duplicated: publishing a new
        tender version is us changing the question, not them changing their
        answer, and inventing a Rev 1 bid nobody submitted would be a
        fabrication sitting in the evidence file.
        """
        live = self.filtered(lambda i: i.state not in ('declined', 'withdrawn'))
        live.sudo().write({'version_id': version.id})
        responded = live.filtered('current_response_id')
        responded.sudo().write({'resubmission_required': True})
        for invitation in responded:
            invitation.current_response_id._assess_administrative()
        return True

    def action_acknowledge(self, version=None, method='buyer_recorded',
                           notes=None, attachments=None):
        """Record that the vendor confirmed receipt of a tender version.

        There is no vendor portal in M5, so this is a buyer recording
        evidence, and the model says so: `method` names how the confirmation
        arrived and `recorded_by_id` names who entered it. What it never does
        is claim the vendor pressed a button. `future_portal` is in the list
        precisely so that the day a portal exists, portal acknowledgements are
        distinguishable from the ones a buyer typed in.
        """
        Ack = self.env['realestate.procurement.sourcing.acknowledgement']
        for invitation in self:
            target = version or invitation.event_id.current_version_id
            if not target:
                raise UserError(_("%s is not published yet.")
                                % invitation.event_id.name)
            existing = invitation.acknowledgement_ids.filtered(
                lambda a, v=target: a.version_id == v)
            if not existing:
                Ack.sudo().create({
                    'invitation_id': invitation.id,
                    'version_id': target.id,
                    'acknowledged_on': fields.Datetime.now(),
                    'recorded_by_id': self.env.user.id,
                    'method': method,
                    'notes': notes,
                    'attachment_ids': [(6, 0, attachments.ids)]
                    if attachments else False,
                })
            invitation.sudo().write({
                'acknowledged_on': fields.Datetime.now(),
                'acknowledged_version_id': target.id,
                'state': ('acknowledged' if invitation.state == 'invited'
                          else invitation.state),
            })
            invitation.message_post(body=_(
                "Acknowledgement of %(version)s recorded by %(user)s "
                "(%(method)s).",
                version=target.display_name,
                user=self.env.user.display_name, method=method))
            # An acknowledged addendum can make a held response evaluable.
            invitation.current_response_id._assess_administrative()
        return True

    def action_decline(self, reason=None, category=None, narrative=None):
        """The vendor will not bid. That is information, not a zero.

        Declining creates no bid record and no zero-value offer. Nought is a
        commercial statement — it says the vendor will do the work for nothing
        — and manufacturing one to make a tender look complete puts that
        statement in their mouth.
        """
        for invitation in self:
            invitation.sudo().write({
                'state': 'declined',
                'declined_on': fields.Datetime.now(),
                'recorded_by_id': self.env.user.id,
                'decline_reason_category': category,
                'decline_narrative': narrative,
                'no_bid_reason': reason or invitation.no_bid_reason,
            })
            order = invitation.purchase_order_id
            if order.state in ('draft', 'sent'):
                order.button_cancel()
        return True

    def action_no_bid(self, reason=None, category=None, narrative=None):
        """A formal no-bid return: an answer, and still not a price."""
        for invitation in self:
            invitation.sudo().write({
                'state': 'no_bid',
                'recorded_by_id': self.env.user.id,
                'decline_reason_category': category,
                'decline_narrative': narrative,
                'no_bid_reason': reason or invitation.no_bid_reason,
            })
        return True

    def action_record_bid(self, received_datetime=None, vendor_reference=None,
                          validity_date=None, attachments=None,
                          late_reason=None):
        """Snapshot what this vendor submitted. See `bid_response.py`."""
        self.ensure_one()
        return self.env['realestate.procurement.bid.response']._record(
            self, received_datetime=received_datetime,
            vendor_reference=vendor_reference, validity_date=validity_date,
            attachments=attachments, late_reason=late_reason)


class SourcingAcknowledgement(models.Model):
    """One vendor confirming they have the current basis — with its evidence.

    A separate record rather than a flag, because "acknowledged" is really
    four facts: which version, when, how we know, and who wrote it down. A
    boolean on the invitation could answer none of them a year later, and an
    addendum nobody can prove was acknowledged is an addendum that will be
    argued about.
    """

    _name = 'realestate.procurement.sourcing.acknowledgement'
    _description = 'Tender Addendum Acknowledgement'
    _order = 'invitation_id, id desc'

    invitation_id = fields.Many2one(
        'realestate.procurement.sourcing.invitation', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='invitation_id.company_id', store=True, index=True)
    event_id = fields.Many2one(
        related='invitation_id.event_id', store=True, index=True)
    partner_id = fields.Many2one(
        related='invitation_id.partner_id', store=True, index=True)
    version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', required=True, index=True,
        ondelete='cascade')
    acknowledged_on = fields.Datetime(required=True, readonly=True)
    recorded_by_id = fields.Many2one('res.users', required=True, readonly=True)
    method = fields.Selection([
        ('email_received', 'Email Received from Vendor'),
        ('signed_document', 'Signed Document'),
        ('buyer_recorded', 'Recorded by Buyer'),
        ('future_portal', 'Vendor Portal'),
        ('other', 'Other'),
    ], required=True, default='buyer_recorded',
        help="How the confirmation reached us. ATMTA has no vendor portal in "
             "M5, so nothing here should be read as the vendor having "
             "clicked anything — `future_portal` exists so that portal "
             "acknowledgements stay distinguishable when one arrives.")
    notes = fields.Text()
    attachment_ids = fields.Many2many(
        'ir.attachment', 'sourcing_ack_attachment_rel', 'ack_id',
        'attachment_id', string='Evidence')

    _sql_constraints = [
        ('version_uniq', 'unique(invitation_id, version_id)',
         'A vendor acknowledges a tender version once.'),
    ]
