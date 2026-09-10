# -*- coding: utf-8 -*-
"""M7 — the award: the decision M6 deliberately refused to make.

```
    M6 EVALUATES THE BIDS.
    M7 AWARDS THE CONTRACT, AND AWARDING IS WHAT COMMITS.
```

Everything before this milestone was reversible. A plan can be revised, a score
reopened, a ranking recomputed, a tender cancelled — none of it owes anybody a
penny. An award is the first act in the chain that ends with a purchase order
confirming, a reservation converting and a Construction commitment existing, so
it is the first act that has to be authorised rather than merely performed.

### What M7 does not do, on purpose

**It does not compute commitment.** Construction does that, from confirmed
purchase orders, and it has done since M2 (`commitment.py`). Procurement
duplicating the calculation would give the project two numbers that disagree
under exactly the conditions nobody tests.

**It does not convert reservations.** M3 built that
(`purchase.order._convert_reservations`, `reservation._convert`), including the
partial and over-run cases, and it fires inside the same transaction as
`button_confirm()` so a rolled-back confirmation takes the conversion with it.

**It does not confirm purchase orders.** It grants the authorisation that lets
somebody else confirm one. `purchase.order._award_authorisation()` was written
in M5 as a hook with one honest answer — `False` — precisely so that M7 could
fill it without the confirmation boundary changing shape.

So the whole of M7's contribution to the money path is: *a named, approved,
revalidated decision that this vendor, for these lines, at this amount, may be
bought from.* The machinery underneath is already built and already tested.

### Rank 1 is not automatically the award

M6 ends at a ranking and says in as many words that a ranking is not a winner.
M7 honours that: awarding the top-ranked candidate is the ordinary case and
needs no justification, awarding anybody else is permitted but requires a
recorded reason, and awarding a technically non-responsive bid is refused
outright — no reason makes that defensible.

### Revalidation, because time passes between evaluating and buying

An evaluation is a photograph. Between the ranking and the award a vendor's
qualification can expire, a bid's validity can lapse, a budget can move and a
reservation can be released. Approving an award therefore re-asks all four
questions rather than trusting the answers the evaluation recorded, and stores
what it found — so the file says what was true *at the moment of the decision*,
not merely at the moment of the scoring.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

#: Once an award is approved it is a decision, not a draft.
SETTLED_STATES = ('approved', 'issued')

#: Who may approve an award. The Procurement Manager, because committing a
#: project budget is that role's accountability, and the Evaluation Manager is
#: deliberately **not** here: running the evaluation and authorising the spend
#: that follows it are the two halves this programme keeps apart.
APPROVAL_GROUPS = (
    'atmta_roles.group_procurement_manager',
)

#: Who may raise one.
DRAFTING_GROUPS = APPROVAL_GROUPS + (
    'atmta_roles.group_procurement_buyer',
)


class ProcurementAward(models.Model):
    _name = 'realestate.procurement.award'
    _description = 'Procurement Award'
    _inherit = ['mail.thread']
    _order = 'sourcing_event_id, id'

    name = fields.Char(readonly=True, copy=False, default=lambda s: _('New'))
    sourcing_event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True, readonly=True)
    round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', required=True,
        readonly=True, index=True,
        help="The finalised evaluation this award is founded on. An award "
             "with no evaluation behind it is a purchase somebody decided to "
             "make, which is a different document with different controls.")
    plan_id = fields.Many2one(related='round_id.plan_id', store=True)
    company_id = fields.Many2one(
        related='sourcing_event_id.company_id', store=True, index=True)
    project_id = fields.Many2one(
        related='sourcing_event_id.project_id', store=True, index=True)
    currency_id = fields.Many2one(
        related='company_id.currency_id', store=True)

    award_type = fields.Selection([
        ('single', 'Single Award'),
        ('split', 'Split Award — more than one vendor'),
        ('partial', 'Partial Award — part of the tender only'),
    ], required=True, default='single', readonly=True,
        help="Recorded rather than inferred. A split award and a partial "
             "award are different decisions with different consequences for "
             "the demand that was not awarded, and the file should say which "
             "one was taken.")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'Submitted for Approval'),
        ('approved', 'Approved'),
        ('issued', 'Issued'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False)

    line_ids = fields.One2many(
        'realestate.procurement.award.line', 'award_id')
    amount_total = fields.Monetary(
        compute='_compute_amount_total', store=True, currency_field='currency_id')

    #: Maker / checker. Two people, and the record says which was which.
    submitted_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    submitted_on = fields.Datetime(readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    issued_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    issued_on = fields.Datetime(readonly=True, copy=False)
    cancelled_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    cancelled_on = fields.Datetime(readonly=True, copy=False)
    cancellation_reason = fields.Text(readonly=True, copy=False)

    revalidation_note = fields.Text(
        readonly=True, copy=False,
        help="What the revalidation found at the moment of approval. Stored "
             "because an evaluation is a photograph and the award is taken "
             "later: this records what was still true when the decision was "
             "actually made.")
    justification = fields.Text(
        help="Required when the award does not follow the ranking.")
    notes = fields.Text()

    revision = fields.Integer(default=0, readonly=True, copy=False)
    supersedes_id = fields.Many2one(
        'realestate.procurement.award', readonly=True, copy=False)
    revision_reason = fields.Text(readonly=True, copy=False)
    superseded = fields.Boolean(readonly=True, copy=False)

    _sql_constraints = [
        ('round_revision_uniq', 'unique(round_id, revision)',
         'An award revision is raised once per evaluation round.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('line_ids.amount')
    def _compute_amount_total(self):
        for award in self:
            award.amount_total = sum(award.line_ids.mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.award') or _('New')
        awards = super().create(vals_list)
        for award in awards:
            award._check_round_is_finalised()
            award._check_one_live_award_per_round()
        return awards

    def _check_one_live_award_per_round(self):
        """One award in play at a time. Revisions supersede; they do not stack.

        The unique index is on `(round_id, revision)` because a superseded
        award has to stay on the record — it is what was decided before the
        decision changed. What must not happen is two *live* awards against one
        evaluation, each authorising its own purchase orders.
        """
        self.ensure_one()
        others = self.sudo().search([
            ('round_id', '=', self.round_id.id),
            ('id', '!=', self.id),
            ('state', 'not in', ('cancelled',)),
            ('superseded', '=', False),
        ])
        if others:
            raise UserError(_(
                "%(round)s already has a live award (%(other)s, %(state)s). "
                "Revise that one or cancel it — two live awards on one "
                "evaluation would each authorise their own orders.",
                round=self.round_id.name, other=others[:1].name,
                state=others[:1].state))
        return True

    # ------------------------------------------------------------------
    # Authority
    # ------------------------------------------------------------------
    def _assert_authority(self, what, groups=APPROVAL_GROUPS):
        if self.env.su:
            return True
        if any(self.env.user.has_group(xmlid) for xmlid in groups):
            return True
        raise AccessError(_(
            "%(user)s is not entitled to %(what)s. An award commits a "
            "project's budget; write access to the record is not the same "
            "permission.",
            user=self.env.user.display_name, what=what))

    def _check_round_is_finalised(self):
        """No award without a completed evaluation behind it."""
        self.ensure_one()
        round_ = self.round_id.sudo()
        if round_.state != 'finalised':
            raise UserError(_(
                "%(round)s is %(state)s. An award is founded on a completed "
                "evaluation — awarding against one still in progress would "
                "decide the tender before it had been judged.",
                round=round_.name, state=round_.state))
        if round_.sourcing_event_id != self.sourcing_event_id:
            raise UserError(_(
                "%s evaluates a different tender.") % round_.name)
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_submit(self):
        """Put the decision up for approval. Validated before it goes."""
        for award in self:
            award._assert_authority(_("raise an award"), groups=DRAFTING_GROUPS)
            if award.state != 'draft':
                raise UserError(_("%s is not a draft.") % award.name)
            award._validate_lines()
            award.write({
                'state': 'review',
                'submitted_by_id': self.env.user.id,
                'submitted_on': fields.Datetime.now(),
            })
            award.message_post(body=_(
                "Award submitted for approval: %(count)s line(s), %(total)s. "
                "This is a recommendation until somebody else approves it.",
                count=len(award.line_ids),
                total=award.currency_id.format(award.amount_total)))
        return True

    def action_approve(self):
        """Approve — by somebody other than the person who raised it."""
        for award in self:
            award._assert_authority(_("approve an award"))
            if award.state != 'review':
                raise UserError(_(
                    "%s has not been submitted for approval.") % award.name)
            if award.submitted_by_id == self.env.user:
                raise UserError(_(
                    "%s raised this award. Approving your own is not a "
                    "review, and this is the one decision in the chain that "
                    "commits money.") % self.env.user.display_name)
            findings = award._revalidate()
            award.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
                'revalidation_note': findings,
            })
            award.message_post(body=_(
                "Award approved. The named purchase orders may now be "
                "confirmed; confirming them is what creates the commitment."))
        return True

    def action_issue(self):
        """Confirm the authorised orders — the moment money starts being owed.

        One transaction for the whole award, and that is the point. A split
        award across three vendors is one decision; issuing two of the three
        and failing on the last would leave the tender in a state nobody
        decided on, with part of the demand committed and part of it not. If
        any order refuses, the whole issue rolls back and the award stays
        approved and re-issuable.

        The confirmation itself is untouched Odoo plus M3's existing gate:
        `button_confirm()` re-checks governance, eligibility and the approved
        basis, then converts the reservation. Nothing here reimplements any
        of that, and nothing here writes a Construction commitment —
        Construction computes it from the confirmed order, as it always has.
        """
        for award in self:
            award._assert_authority(_("issue an award"))
            if award.state != 'approved':
                raise UserError(_(
                    "%(name)s is %(state)s. Only an approved award may be "
                    "issued.", name=award.name, state=award.state))
            orders = award.line_ids.mapped('purchase_order_id')
            if not orders:
                raise UserError(_(
                    "%s names no purchase order to issue.") % award.name)
            award._assert_demand_is_convertible(orders)
            # Trim the orders to what was actually awarded, while they are
            # still drafts. Without this a split award confirms every vendor's
            # copy of the full tender scope.
            award._apply_awarded_scope()
            pending = orders.filtered(lambda o: o.state in ('draft', 'sent'))
            # `skip_alternative_check` suppresses Odoo's native "shall I
            # cancel the other quotations?" prompt, which otherwise returns a
            # wizard action instead of confirming. That prompt asks the
            # question this award has already answered, formally and with an
            # approver's name on it.
            #
            # It is safe to pass here, and M5 made sure of that: the tender
            # authorisation gate was written to be independent of this flag
            # precisely because the native wizard sets it, and
            # `test_a_finalised_evaluation_still_cannot_confirm_a_tender_rfq`
            # asserts an unauthorised order still refuses *with* it set.
            pending.with_context(skip_alternative_check=True).button_confirm()
            # The offers that were not awarded lost. Leaving them as live
            # quotations against a decided tender invites somebody to confirm
            # one later; the immutable M5 bid evidence is a separate record
            # and is untouched by cancelling the quotation.
            award._cancel_losing_quotations()
            unconfirmed = orders.filtered(
                lambda o: o.state not in ('purchase', 'done'))
            if unconfirmed:
                raise UserError(_(
                    "%(orders)s did not confirm, so nothing in this award has "
                    "been issued. A part-issued award would leave the tender "
                    "in a state nobody decided on.",
                    orders=', '.join(unconfirmed.mapped('name'))))
            award.write({
                'state': 'issued',
                'issued_by_id': self.env.user.id,
                'issued_on': fields.Datetime.now(),
            })
            award.message_post(body=_(
                "Award issued. %(count)s purchase order(s) confirmed; the "
                "reservation has converted and Construction now reads the "
                "commitment from the orders.", count=len(orders)))
        return True

    def _assert_demand_is_convertible(self, orders):
        """Refuse to issue where confirming would count the demand twice.

        Construction reads a commitment from any confirmed order. M3 converts
        the reservation from `re_material_request_line_id` on the order lines.
        If a line carries a tender scope but no demand link, the first happens
        and the second does not: the project ends up holding a reservation
        *and* owing a commitment for the same money.

        A tender line that traces to exactly one requisition line is linked at
        RFQ creation. One that aggregates several cannot be expressed by a
        single many2one, and apportioning it silently is precisely the kind of
        guess that makes two control numbers disagree — so it stops here and
        says so.
        """
        self.ensure_one()
        ambiguous = []
        for line in orders.sudo().mapped('order_line').filtered(
                lambda l: l.re_sourcing_line_id and not l.display_type):
            if line.re_material_request_line_id:
                continue
            allocations = line.re_sourcing_line_id.allocation_ids.filtered(
                'request_line_id')
            if allocations:
                ambiguous.append((line, allocations))
        if ambiguous:
            raise UserError(_(
                "These tender lines draw on more than one approved "
                "requisition, so confirming them would commit the budget "
                "while the reservations went on holding it — the same money "
                "counted twice:\n\n%(lines)s\n\nSplit the tender line, or "
                "award the requisitions separately. It is not apportioned "
                "automatically because a wrong apportionment is invisible.",
                lines='\n'.join(
                    '• %s (%s allocations)' % (line.name, len(allocs))
                    for line, allocs in ambiguous)))
        return True

    def action_print_award(self):
        """The award document — recommendation or decision, per its state."""
        self.ensure_one()
        self._assert_authority(_("issue the award document"),
                               groups=DRAFTING_GROUPS)
        return self.env.ref(
            'atmta_procurement_award.action_report_award').report_action(self)

    def _cancel_losing_quotations(self):
        """Close the tender's unawarded RFQs when the award is issued."""
        self.ensure_one()
        awarded = self.line_ids.sudo().mapped('purchase_order_id')
        siblings = self.sourcing_event_id.sudo().invitation_ids.mapped(
            'purchase_order_id')
        losing = (siblings - awarded).filtered(
            lambda o: o.state in ('draft', 'sent'))
        if losing:
            losing.button_cancel()
            self.message_post(body=_(
                "%(count)s unawarded quotation(s) cancelled: %(names)s. The "
                "submitted bids themselves are M5 evidence and are unchanged.",
                count=len(losing), names=', '.join(losing.mapped('name'))))
        return losing

    def action_new_revision(self, reason=None):
        """Change an approved award by superseding it, never by editing it.

        An approved award is an authorisation with two signatures on it. If the
        decision has to change — a vendor withdraws, a quantity moves — the
        honest record is a new revision that says what changed and why, beside
        the one it replaced. Editing the original would leave a document
        claiming two people approved something they never saw.

        An **issued** award cannot be revised: its orders are confirmed and the
        budget is committed. Cancel the orders — M3 reverses the commitment —
        and raise a fresh award.
        """
        self.ensure_one()
        self._assert_authority(_("revise an award"))
        if self.state == 'issued':
            raise UserError(_(
                "%s is issued and its orders are confirmed. Cancel the orders "
                "to reverse the commitment, then raise a new award — a "
                "revision cannot un-commit money.") % self.name)
        if self.state == 'cancelled':
            raise UserError(_("%s is cancelled.") % self.name)
        if not reason:
            raise UserError(_(
                "A revised award needs a reason. Changing an authorisation "
                "without one is indistinguishable from changing it to suit "
                "the outcome."))
        self._engine().write({'superseded': True, 'state': 'cancelled',
                              'cancellation_reason': reason,
                              'cancelled_by_id': self.env.user.id,
                              'cancelled_on': fields.Datetime.now()})
        # Lines are rebuilt explicitly rather than through `copy()`: Odoo's
        # `One2many` carries `copy = False` by default, so a copied award would
        # arrive with no lines and no scope, and the revision would silently be
        # an empty document. Building them here also keeps the awarded
        # quantities exactly as they were rather than re-deriving them from the
        # bid, which is the whole point of revising rather than starting again.
        copy = self.sudo().create({
            'sourcing_event_id': self.sourcing_event_id.id,
            'round_id': self.round_id.id,
            'award_type': self.award_type,
            'justification': self.justification,
            'notes': self.notes,
            'revision': self.revision + 1,
            'supersedes_id': self.id,
            'revision_reason': reason,
            'line_ids': [(0, 0, {
                'candidate_id': line.candidate_id.id,
                'purchase_order_id': line.purchase_order_id.id,
                'allocation_ids': [(0, 0, {
                    'sourcing_line_id': allocation.sourcing_line_id.id,
                    'bid_line_id': allocation.bid_line_id.id,
                    'quantity': allocation.quantity,
                    'price_unit': allocation.price_unit,
                }) for allocation in line.allocation_ids],
            }) for line in self.line_ids],
        })
        self.message_post(body=_(
            "Superseded by revision %(rev)s: %(reason)s",
            rev=copy.revision, reason=reason))
        return copy

    def action_cancel(self, reason=None):
        """Withdraw an award. Never one that has already been issued."""
        for award in self:
            award._assert_authority(_("cancel an award"))
            if award.state == 'issued':
                raise UserError(_(
                    "%s has been issued and its purchase orders are live. "
                    "Cancel the orders instead — that is what reverses the "
                    "commitment, and M3 already does it.") % award.name)
            if not reason:
                raise UserError(_(
                    "Cancelling an award needs a reason."))
            award.write({
                'state': 'cancelled',
                'cancelled_by_id': self.env.user.id,
                'cancelled_on': fields.Datetime.now(),
                'cancellation_reason': reason,
            })
            award.message_post(body=_("Award cancelled: %s") % reason)
        return True

    # ------------------------------------------------------------------
    def _validate_lines(self):
        """Refuse an award that does not add up, before anybody approves it."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_(
                "%s awards nothing.") % self.name)

        non_responsive = self.line_ids.filtered(
            lambda l: l.candidate_id.sudo().technical_result != 'responsive')
        if non_responsive:
            raise UserError(_(
                "These offers were not technically responsive and cannot be "
                "awarded at any price: %s.\n\nA knockout failure is not "
                "rescuable by a decision — that is what made it a knockout.",
                ) % ', '.join(non_responsive.mapped('partner_name')))

        off_ranking = self.line_ids.filtered(lambda l: not l.is_top_ranked)
        if off_ranking and not self.justification:
            raise UserError(_(
                "%(vendors)s did not rank first, and awarding them needs a "
                "recorded reason.\n\nAwarding away from the ranking is "
                "permitted — the ranking is an evaluation outcome, not an "
                "instruction — but an award file that cannot say why is "
                "indistinguishable from one that had no reason.",
                vendors=', '.join(off_ranking.mapped('partner_name'))))

        vendors = self.line_ids.mapped('partner_id')
        if self.award_type == 'single' and len(vendors) > 1:
            raise UserError(_(
                "%(name)s is recorded as a single award but names %(count)s "
                "vendors. Set the award type to split.",
                name=self.name, count=len(vendors)))
        if self.award_type == 'split' and len(vendors) < 2:
            raise UserError(_(
                "%s is recorded as a split award but names one vendor.")
                % self.name)

        empty = self.line_ids.filtered(
            lambda l: not sum(l.allocation_ids.mapped('quantity')))
        if empty:
            raise UserError(_(
                "%(vendors)s are named on this award but awarded nothing.\n\n"
                "Caught here rather than at issue: an award line with no "
                "quantity would go through approval looking like a decision "
                "and then refuse at the moment somebody expected orders to be "
                "placed. Remove the line, or give it a quantity.",
                vendors=', '.join(empty.mapped('partner_name'))))

        self._check_scope_does_not_exceed_the_tender()
        self._check_award_type_matches_the_scope()
        return True

    def _check_scope_does_not_exceed_the_tender(self):
        """Across every vendor, not just within one.

        The per-allocation constraint stops one vendor being given more of a
        line than was tendered. This is the one that matters for a split: two
        vendors at 600 each against a tendered 1,000 passes every individual
        check and commits 1,200 — 200 of it approved by nobody.
        """
        self.ensure_one()
        allocations = self.line_ids.mapped('allocation_ids')
        over = []
        for sourcing_line in allocations.mapped('sourcing_line_id'):
            tendered = sourcing_line.sudo().quantity or 0.0
            awarded = sum(allocations.filtered(
                lambda a, s=sourcing_line: a.sourcing_line_id == s
            ).mapped('quantity'))
            if awarded - tendered > 0.000001:
                over.append((sourcing_line, awarded, tendered))
        if over:
            raise UserError(_(
                "More is awarded than was tendered:\n\n%(lines)s\n\nThe "
                "authorisation behind this tender does not grow because the "
                "demand was split between vendors.",
                lines='\n'.join(
                    '• %s — awarded %s of %s tendered'
                    % (line.display_name, awarded, tendered)
                    for line, awarded, tendered in over)))
        return True

    def _check_award_type_matches_the_scope(self):
        """A partial award has to actually be partial, and say so."""
        self.ensure_one()
        allocations = self.line_ids.mapped('allocation_ids')
        tender_lines = self.sourcing_event_id.sudo().line_ids
        fully_awarded = True
        for sourcing_line in tender_lines:
            tendered = sourcing_line.quantity or 0.0
            awarded = sum(allocations.filtered(
                lambda a, s=sourcing_line: a.sourcing_line_id == s
            ).mapped('quantity'))
            if tendered - awarded > 0.000001:
                fully_awarded = False
                break
        if not fully_awarded and self.award_type != 'partial':
            raise UserError(_(
                "%(name)s leaves part of the tender unawarded but is recorded "
                "as a %(type)s award.\n\nSet the type to partial. The "
                "difference matters: a partial award leaves demand still "
                "reserved and still needing to be bought, and the file should "
                "say that was the decision rather than an omission.",
                name=self.name, type=self.award_type))
        if fully_awarded and self.award_type == 'partial':
            raise UserError(_(
                "%s awards the whole tender but is recorded as partial.")
                % self.name)
        return True

    def _apply_awarded_scope(self):
        """Write the awarded quantities onto the orders, before they confirm.

        M5 issues every invited vendor an RFQ for the entire tender scope,
        because at invitation time nobody knows who will win what. Confirming
        two of those untouched would commit twice the demand, so the award's
        allocations are pushed onto the orders here — quantities set, and lines
        for scope this vendor was not awarded removed.

        Deliberately before `button_confirm()`: a purchase order line is
        editable while the order is a draft, and M3's conversion reads the
        quantities off the confirmed order. Trimming afterwards would convert
        against numbers that had already been used.
        """
        self.ensure_one()
        for line in self.line_ids:
            order = line.sudo().purchase_order_id
            if not order or order.state not in ('draft', 'sent'):
                continue
            awarded = {a.sourcing_line_id.id: a
                       for a in line.allocation_ids}
            for order_line in order.order_line:
                if order_line.display_type:
                    continue
                allocation = awarded.get(order_line.re_sourcing_line_id.id)
                if not allocation or not allocation.quantity:
                    order_line.unlink()
                    continue
                if order_line.product_qty != allocation.quantity:
                    order_line.product_qty = allocation.quantity
                if allocation.price_unit:
                    order_line.price_unit = allocation.price_unit
            if not order.order_line.filtered(lambda l: not l.display_type):
                raise UserError(_(
                    "%(vendor)s is awarded nothing, so %(order)s would confirm "
                    "empty. Remove the award line instead.",
                    vendor=line.partner_name, order=order.name))
        return True

    def _revalidate(self):
        """Re-ask, at the moment of decision, what the evaluation assumed.

        Every one of these was true when the offers were scored. None of them
        is guaranteed to still be true now, and an award that did not check
        would be committing a budget on the strength of a photograph.
        """
        self.ensure_one()
        findings = []
        for line in self.line_ids:
            findings += line._revalidate()
        note = '\n'.join(findings) if findings else _(
            "Revalidated at approval: vendor eligibility, bid validity, "
            "evaluation basis and reserved capacity all still stood.")
        return note

    def _engine(self):
        return self.sudo().with_context(re_award_engine=True)

    def write(self, vals):
        if not self.env.context.get('re_award_engine'):
            protected = {'line_ids', 'round_id', 'sourcing_event_id',
                         'award_type', 'justification'}
            touched = set(vals) & protected
            for award in self:
                if award.state in SETTLED_STATES and touched:
                    raise UserError(_(
                        "%(name)s was approved on %(when)s. Its substance "
                        "cannot be edited — %(fields)s would change what was "
                        "authorised after it was authorised.",
                        name=award.name, when=award.approved_on,
                        fields=', '.join(sorted(touched))))
        return super().write(vals)

    def unlink(self):
        for award in self:
            if award.state in SETTLED_STATES:
                raise UserError(_(
                    "%s is an approved award and purchase orders were "
                    "authorised against it.") % award.name)
        return super().unlink()


class ProcurementAwardLine(models.Model):
    """One vendor, one share of the tender, one authorised purchase order."""

    _name = 'realestate.procurement.award.line'
    _description = 'Procurement Award Line'
    _order = 'award_id, id'

    award_id = fields.Many2one(
        'realestate.procurement.award', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='award_id.company_id', store=True, index=True)
    project_id = fields.Many2one(
        related='award_id.project_id', store=True, index=True)
    currency_id = fields.Many2one(related='award_id.currency_id', store=True)

    candidate_id = fields.Many2one(
        'realestate.procurement.evaluation.candidate', required=True,
        ondelete='restrict', index=True,
        help="The evaluated offer being awarded. Awarding points at the "
             "evaluation rather than at the vendor, so the file can always "
             "say which judgement the decision rests on.")
    partner_id = fields.Many2one(
        related='candidate_id.partner_id', store=True, index=True,
        string='Vendor')
    partner_name = fields.Char(
        readonly=True, string='Vendor (as awarded)',
        help="Snapshotted for the same reason M6 snapshots it on the "
             "candidate: a vendor renamed afterwards must not restate who was "
             "awarded.")
    bid_response_id = fields.Many2one(
        related='candidate_id.bid_response_id', store=True, readonly=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', readonly=True, index=True,
        help="The order this line authorises. It is the vendor's own tender "
             "RFQ — M5 created one per invitation — not a new document, so "
             "the thing that gets confirmed is the thing that was bid.")

    allocation_ids = fields.One2many(
        'realestate.procurement.award.allocation', 'award_line_id')
    amount = fields.Monetary(
        compute='_compute_amount', store=True, currency_field='currency_id',
        help="The awarded scope priced at the vendor's own bid. Derived rather "
             "than typed: an award amount that did not match the quantities "
             "being awarded would be a number nobody could reconcile against "
             "either the bid or the purchase order.")
    is_top_ranked = fields.Boolean(
        compute='_compute_is_top_ranked', store=True,
        help="Whether this offer held rank 1. Stored so the award file can "
             "show the ranking it followed or departed from without "
             "recomputing it later against data that has moved.")
    rank_awarded = fields.Integer(readonly=True)
    revalidation_note = fields.Text(readonly=True)

    _sql_constraints = [
        ('candidate_uniq', 'unique(award_id, candidate_id)',
         'An offer is awarded once per award.'),
    ]

    @api.depends('candidate_id')
    def _compute_is_top_ranked(self):
        for line in self:
            line.is_top_ranked = line.candidate_id.sudo().rank == 1

    @api.depends('allocation_ids.amount')
    def _compute_amount(self):
        for line in self:
            line.amount = sum(line.allocation_ids.mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            candidate = line.candidate_id.sudo()
            line.partner_name = candidate.partner_name or \
                candidate.partner_id.display_name
            line.rank_awarded = candidate.rank
            if not line.purchase_order_id:
                line.purchase_order_id = \
                    candidate.bid_response_id.invitation_id.purchase_order_id
            if not line.allocation_ids:
                line._take_full_bid_scope()
        return lines

    def _take_full_bid_scope(self):
        """Start from everything the vendor bid, as editable records.

        Written out rather than assumed. A single award for the whole tender is
        the ordinary case, and leaving the scope implicit would mean a split or
        partial award had to be expressed by *removing* something invisible.
        These rows are what the buyer then trims.
        """
        self.ensure_one()
        Allocation = self.env['realestate.procurement.award.allocation']
        response = self.candidate_id.sudo().bid_response_id
        for bid_line in response.line_ids:
            if not bid_line.sourcing_line_id:
                continue
            Allocation.create({
                'award_line_id': self.id,
                'sourcing_line_id': bid_line.sourcing_line_id.id,
                'bid_line_id': bid_line.id,
                'quantity': bid_line.quantity,
                'price_unit': bid_line.price_unit,
            })
        return True

    def _revalidate(self):
        """The four questions, re-asked at approval. Findings, not silence."""
        self.ensure_one()
        notes = []
        candidate = self.candidate_id.sudo()
        response = candidate.bid_response_id
        award = self.award_id

        # 1. The evaluation basis must still be the one that was frozen.
        plan = award.round_id.sudo().plan_id
        if plan.state not in ('frozen', 'in_use', 'superseded'):
            raise UserError(_(
                "%(plan)s is no longer frozen, so the ranking this award "
                "follows was produced against a basis that has since moved.",
                plan=plan.name))

        # 2. Bid validity. A lapsed offer is not a refusal to award — it is a
        #    fact the approver has to have been told.
        today = fields.Date.context_today(self)
        if response.validity_date and response.validity_date < today:
            notes.append(_(
                "%(vendor)s's offer expired on %(date)s and was awarded "
                "anyway; confirm the vendor still stands behind it.",
                vendor=self.partner_name, date=response.validity_date))

        # 3. Vendor eligibility, re-asked with the award purpose — the same
        #    call `button_confirm` makes, but asked before the decision rather
        #    than after it.
        #
        #    The project and company are passed through `sudo()`. Asking
        #    whether a vendor may be awarded on a project is a lookup, and a
        #    Procurement Manager is not automatically granted read access to
        #    `realestate.project` — that lives behind an `atmta_real_estate`
        #    group. Approving an award must not depend on which other module's
        #    groups the approver happens to hold.
        Eligibility = self.env['realestate.procurement.vendor.eligibility']
        outcome = Eligibility.check_vendor_eligibility(
            self.partner_id, company=award.sudo().company_id, category=None,
            project=award.sudo().project_id, date=today, purpose='award')
        if not outcome.get('eligible'):
            raise UserError(_(
                "%(vendor)s is not currently eligible to receive an award:"
                "\n\n%(reasons)s\n\nThe evaluation may have been sound; the "
                "vendor's standing has changed since.",
                vendor=self.partner_name,
                reasons='\n'.join('• %s' % r
                                  for r in outcome.get('blocking_reasons', []))))
        for warning in outcome.get('warnings', []):
            notes.append(_("%(vendor)s: %(warning)s",
                           vendor=self.partner_name, warning=warning))

        # 4. The reserved capacity must still be there to convert.
        #
        #    Sudo the order *before* reading anything off it. `group_
        #    procurement_manager` implies no `purchase.*` group at all, so an
        #    approver who is entitled to authorise the spend may still be
        #    unable to read the quotation — and `order.order_line.sudo()`
        #    sudoes the lines after the one2many has already been fetched as
        #    the user, which is too late to help.
        order = self.sudo().purchase_order_id
        if order:
            request_lines = order.order_line.mapped(
                're_material_request_line_id')
            reservations = request_lines.mapped('reservation_ids').filtered(
                lambda r: r.state == 'reserved')
            if request_lines and not reservations:
                notes.append(_(
                    "%(vendor)s's order has no live reservation behind it; "
                    "confirming it will commit against capacity that is no "
                    "longer held.", vendor=self.partner_name))

        note = '\n'.join(notes)
        self.sudo().revalidation_note = note
        return notes


class ProcurementAwardAllocation(models.Model):
    """How much of one tender line goes to one vendor.

    This is what makes a split or a partial award expressible. M5 issues every
    invited vendor an RFQ for the **whole** tender scope, because at invitation
    time nobody knows who will win what — which means awarding two vendors
    without trimming their orders would commit twice the demand. These rows are
    the trim, and `_apply_awarded_scope()` writes them onto the orders before
    anything confirms.
    """

    _name = 'realestate.procurement.award.allocation'
    _description = 'Procurement Award Allocation'
    _order = 'award_line_id, id'

    award_line_id = fields.Many2one(
        'realestate.procurement.award.line', required=True,
        ondelete='cascade', index=True)
    award_id = fields.Many2one(
        related='award_line_id.award_id', store=True, index=True)
    company_id = fields.Many2one(
        related='award_line_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(
        related='award_line_id.currency_id', store=True)
    partner_name = fields.Char(
        related='award_line_id.partner_name', store=True, readonly=True)

    sourcing_line_id = fields.Many2one(
        'realestate.procurement.sourcing.line', required=True,
        ondelete='restrict', index=True, string='Tender Line')
    bid_line_id = fields.Many2one(
        'realestate.procurement.bid.response.line', readonly=True)
    tender_quantity = fields.Float(
        related='sourcing_line_id.quantity', readonly=True,
        string='Tendered Qty')
    quantity = fields.Float(
        required=True, digits='Product Unit of Measure',
        string='Awarded Qty')
    price_unit = fields.Float(
        readonly=True, digits='Product Price',
        help="The vendor's own bid rate, snapshotted. An award does not "
             "renegotiate a price; a BAFO round does that, and it produces a "
             "new bid revision to award from.")
    amount = fields.Monetary(
        compute='_compute_amount', store=True, currency_field='currency_id')

    _sql_constraints = [
        ('line_uniq', 'unique(award_line_id, sourcing_line_id)',
         'A tender line is awarded to a vendor once per award line.'),
        ('qty_positive', 'CHECK(quantity >= 0)',
         'An awarded quantity cannot be negative.'),
    ]

    @api.depends('quantity', 'price_unit')
    def _compute_amount(self):
        for allocation in self:
            allocation.amount = (allocation.quantity or 0.0) * \
                (allocation.price_unit or 0.0)

    @api.constrains('quantity')
    def _check_not_over_tender(self):
        """One vendor cannot be awarded more of a line than was tendered."""
        for allocation in self:
            tendered = allocation.sourcing_line_id.sudo().quantity or 0.0
            if allocation.quantity - tendered > 0.000001:
                raise ValidationError(_(
                    "%(vendor)s is awarded %(awarded)s of %(line)s, but only "
                    "%(tendered)s was tendered. Nobody approved the "
                    "difference.",
                    vendor=allocation.partner_name or '',
                    awarded=allocation.quantity, tendered=tendered,
                    line=allocation.sourcing_line_id.display_name))
