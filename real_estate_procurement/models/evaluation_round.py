# -*- coding: utf-8 -*-
"""M6 — the evaluation round: candidates, committee, and the staged lifecycle.

```
    DRAFT → TECHNICAL_OPEN → TECHNICAL_FINAL → COMMERCIAL_OPEN
          → COMMERCIAL_FINAL → FINALISED
```

Forwards only. A round that could step backwards would let somebody reopen the
technical stage after seeing prices, which is the one thing staged evaluation
exists to prevent.

### The candidate set is frozen, not queried

At technical opening the round writes down exactly which bids are in it and
why the others are not. Asking "which bids are eligible now?" a year later
would answer with today's data — a bid withdrawn last month would silently
vanish from an evaluation it took part in, and the report would no longer
match the decision.

### What this model will not do

There is no `action_award`, no winner field and no purchase order anywhere in
this file. The round can rank; ranking is an evaluation outcome, not an
authorisation to buy. M7 decides whether rank 1 becomes an award, and until it
exists a tender RFQ still refuses to confirm — `test_a_finalised_evaluation_
still_cannot_confirm_a_tender_rfq` holds that line.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .evaluation_candidate import COMMERCIAL_GROUPS

#: Forward-only lifecycle. The index of a state is how far the round has got.
ROUND_STATES = ['draft', 'technical_open', 'technical_final',
                'commercial_open', 'commercial_final', 'finalised']

#: Who may drive the lifecycle. The Evaluation Manager, because that group's
#: own description says it runs the evaluation, and the Procurement Manager,
#: who oversees the module — the same pair `_user_may_reopen` already trusts.
#:
#: Deliberately **not** every procurement user. The ACL lets a Buyer write on a
#: round so they can prepare one; preparing an evaluation and then opening,
#: staging or closing it are different acts, and the person running the tender
#: should not be able to decide when prices are opened.
LIFECYCLE_GROUPS = (
    'real_estate_procurement.group_evaluation_manager',
    'real_estate_procurement.group_procurement_manager',
)

#: Normalisation is mechanical application of a frozen basis, so the people who
#: analyse the money may do it as well as the people who run the round.
COMMERCIAL_WORK_GROUPS = LIFECYCLE_GROUPS + (
    'real_estate_procurement.group_evaluation_commercial',
)

#: Committee roles. Split deliberately: a buyer running the tender is not
#: automatically a technical evaluator, and a technical evaluator is not
#: automatically allowed to see money.
ROLES = [
    ('technical_evaluator', 'Technical Evaluator'),
    ('technical_lead', 'Technical Lead'),
    ('commercial_evaluator', 'Commercial Evaluator'),
    ('commercial_lead', 'Commercial Lead'),
    ('evaluation_manager', 'Evaluation Manager'),
    ('observer', 'Observer'),
]


class EvaluationRound(models.Model):
    _name = 'realestate.procurement.evaluation.round'
    _description = 'Procurement Evaluation Round'
    _inherit = ['mail.thread']
    _order = 'sourcing_event_id, id'

    name = fields.Char(readonly=True, copy=False, default=lambda s: _('New'))
    sourcing_event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='sourcing_event_id.company_id', store=True, index=True)
    project_id = fields.Many2one(
        related='sourcing_event_id.project_id', store=True, index=True)
    sourcing_version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', readonly=True)
    plan_id = fields.Many2one(
        'realestate.procurement.evaluation.plan', required=True, index=True)
    evaluation_currency_id = fields.Many2one(
        related='plan_id.evaluation_currency_id', store=True)

    round_type = fields.Selection([
        ('initial', 'Initial Evaluation'),
        ('bafo', 'Best and Final Offer'),
    ], required=True, default='initial', readonly=True)
    previous_round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', readonly=True)
    # Who was asked to re-price, and why, is a commercial judgement — under a
    # `selective` policy it names the vendors the buyer wants a better number
    # from. Restricted on the same gate as the candidate outcome.
    shortlist_reason = fields.Text(readonly=True, groups=COMMERCIAL_GROUPS)
    invited_partner_ids = fields.Many2many(
        'res.partner', 'eval_round_bafo_partner_rel', 'round_id', 'partner_id',
        readonly=True, string='BAFO Shortlist', groups=COMMERCIAL_GROUPS)

    state = fields.Selection(
        [(state, state.replace('_', ' ').title()) for state in ROUND_STATES]
        + [('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False)
    result_status = fields.Selection([
        ('pending', 'Pending'),
        ('ranked', 'Ranked'),
        ('tie_requires_decision', 'Tie — Requires a Decision'),
        ('no_responsive_bid', 'No Responsive Bid'),
    ], default='pending', readonly=True, copy=False,
        help="The outcome of the evaluation. None of these values is an "
             "award: they describe what the offers came to, not what the "
             "company has decided to do about it.")

    technical_opened_on = fields.Datetime(readonly=True, copy=False)
    technical_opened_by_id = fields.Many2one('res.users', readonly=True)
    technical_finalised_on = fields.Datetime(readonly=True, copy=False)
    commercial_opened_on = fields.Datetime(readonly=True, copy=False)
    commercial_opened_by_id = fields.Many2one('res.users', readonly=True)
    commercial_finalised_on = fields.Datetime(readonly=True, copy=False)
    finalised_on = fields.Datetime(readonly=True, copy=False)
    finalised_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    reopen_reason = fields.Text(readonly=True, copy=False)

    candidate_ids = fields.One2many(
        'realestate.procurement.evaluation.candidate', 'round_id')
    commercial_candidate_ids = fields.One2many(
        'realestate.procurement.evaluation.candidate', 'round_id',
        domain=[('in_commercial', '=', True)],
        string='Commercial Candidates')
    assignment_ids = fields.One2many(
        'realestate.procurement.evaluation.assignment', 'round_id')
    sheet_ids = fields.One2many(
        'realestate.procurement.technical.evaluation', 'round_id')
    deviation_ids = fields.One2many(
        'realestate.procurement.evaluation.deviation', 'round_id')
    notes = fields.Text()

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.evaluation.round') or _('New')
        return super().create(vals_list)

    def _assert_authority(self, what, groups=LIFECYCLE_GROUPS):
        """Refuse a lifecycle act to somebody who only holds write access.

        The ACL answers "may this account touch the table". It cannot answer
        "may this person open the prices", and those are not the same
        question — which is why every stage transition asks this one too.
        """
        if self.env.su:
            return True
        if any(self.env.user.has_group(xmlid) for xmlid in groups):
            return True
        raise AccessError(_(
            "%(user)s is not entitled to %(what)s on %(name)s. Running an "
            "evaluation is the Evaluation Manager's role; write access to the "
            "record is not the same permission.",
            user=self.env.user.display_name, what=what,
            name=self.display_name))

    def _assert_forward(self, target):
        """Refuse any transition that is not a step forward."""
        self.ensure_one()
        if self.state == 'cancelled':
            raise UserError(_("%s is cancelled.") % self.name)
        current = ROUND_STATES.index(self.state) if self.state in ROUND_STATES \
            else -1
        wanted = ROUND_STATES.index(target)
        if wanted <= current:
            raise UserError(_(
                "%(name)s is already at %(state)s. Evaluation does not step "
                "backwards — reopening the technical stage after prices are "
                "visible is exactly what staging prevents. Use a controlled "
                "reopen with a reason, or a new round.",
                name=self.name, state=self.state))
        return True

    # ------------------------------------------------------------------
    # Technical stage
    # ------------------------------------------------------------------
    def action_open_technical(self):
        """Freeze the candidate set and let scoring begin."""
        for round_ in self:
            round_._assert_authority(_("open technical evaluation"))
            round_._assert_forward('technical_open')
            plan = round_.plan_id
            if plan.state not in ('frozen', 'in_use'):
                raise UserError(_(
                    "%(plan)s is not frozen. Scoring against a basis that can "
                    "still change is not evaluation.", plan=plan.name))
            event = round_.sourcing_event_id
            if event.state != 'closed':
                raise UserError(_(
                    "%s is still open for bidding. Evaluating before the "
                    "deadline would judge the vendors who happened to be "
                    "early.") % event.name)
            round_._freeze_candidates()
            if not round_.candidate_ids.filtered('in_technical'):
                raise UserError(_(
                    "%s has no administratively eligible bid to evaluate.")
                    % event.name)
            plan._engine().write({'state': 'in_use'})
            round_.write({
                'state': 'technical_open',
                'sourcing_version_id': event.current_version_id.id,
                'technical_opened_on': fields.Datetime.now(),
                'technical_opened_by_id': self.env.user.id,
            })
            round_.message_post(body=_(
                "Technical evaluation opened with %s candidate(s).")
                % len(round_.candidate_ids.filtered('in_technical')))
        return True

    def _freeze_candidates(self):
        """Write down who is in this evaluation, and why the others are not."""
        self.ensure_one()
        Candidate = self.env['realestate.procurement.evaluation.candidate']
        event = self.sourcing_event_id
        responses = event.bid_response_ids.filtered(
            lambda r: r.state in ('received', 'superseded'))
        # Read through sudo: the shortlist is a restricted commercial field,
        # and freezing the candidate set must not depend on whether the person
        # who clicked is entitled to *see* it.
        shortlist = self.sudo().invited_partner_ids
        if self.round_type == 'bafo' and shortlist:
            responses = responses.filtered(
                lambda r: r.partner_id in shortlist)

        # One candidate per invitation: the offer under evaluation is the
        # vendor's current one, and superseded revisions are history rather
        # than competing entries.
        by_invitation = {}
        for response in responses:
            current = response.invitation_id.current_response_id
            if current:
                by_invitation[response.invitation_id] = current

        for invitation, response in by_invitation.items():
            reason = False
            included = True
            if response.state != 'received':
                included, reason = False, _("The offer is %s.") % response.state
            elif not response.is_evaluable:
                included = False
                reason = response.completeness_notes or _(
                    "Administratively not evaluable.")
            elif invitation.state in ('declined', 'no_bid', 'withdrawn'):
                included = False
                reason = _("The vendor %s.") % invitation.state
            Candidate.create({
                'round_id': self.id,
                'bid_response_id': response.id,
                'invitation_id': invitation.id,
                'in_technical': included,
                'exclusion_reason': reason,
                'administrative_status': response.administrative_status,
            })
        return True

    def action_finalise_technical(self):
        """Consolidate the sheets and decide responsiveness."""
        for round_ in self:
            round_._assert_authority(_("finalise technical evaluation"))
            round_._assert_forward('technical_final')
            outstanding = round_.sheet_ids.filtered(
                lambda s: s.state == 'draft')
            if outstanding:
                raise UserError(_(
                    "%(count)s evaluation sheet(s) are still in draft. "
                    "Finalising now would consolidate scores nobody has "
                    "submitted.", count=len(outstanding)))
            round_.candidate_ids.filtered('in_technical')._consolidate()
            round_.write({'state': 'technical_final',
                          'technical_finalised_on': fields.Datetime.now()})
            responsive = round_.candidate_ids.filtered(
                lambda c: c.technical_result == 'responsive')
            round_.message_post(body=_(
                "Technical evaluation finalised: %(ok)s responsive of "
                "%(all)s evaluated.",
                ok=len(responsive),
                all=len(round_.candidate_ids.filtered('in_technical'))))
            if not responsive:
                round_.write({'result_status': 'no_responsive_bid'})
        return True

    # ------------------------------------------------------------------
    # Commercial stage
    # ------------------------------------------------------------------
    def action_open_commercial(self):
        """Only technically responsive offers go through."""
        for round_ in self:
            round_._assert_authority(_("open commercial evaluation"))
            round_._assert_forward('commercial_open')
            if round_.state != 'technical_final':
                raise UserError(_(
                    "Commercial evaluation opens after the technical result "
                    "is final. Opening prices first is how a technical "
                    "opinion starts following a number."))
            responsive = round_.candidate_ids.filtered(
                lambda c: c.technical_result == 'responsive')
            responsive.write({'in_commercial': True})
            round_.write({
                'state': 'commercial_open',
                'commercial_opened_on': fields.Datetime.now(),
                'commercial_opened_by_id': self.env.user.id,
            })
            excluded = round_.candidate_ids - responsive
            round_.message_post(body=_(
                "Commercial evaluation opened for %(ok)s offer(s); %(out)s "
                "excluded and named in the record.",
                ok=len(responsive), out=len(excluded)))
        return True

    def action_normalise(self):
        """Convert every offer onto the plan's one basis, and freeze it."""
        Analysis = self.env['realestate.procurement.commercial.analysis']
        for round_ in self:
            round_._assert_authority(_("normalise offers"),
                                     groups=COMMERCIAL_WORK_GROUPS)
            if round_.state not in ('commercial_open', 'commercial_final'):
                raise UserError(_("Commercial evaluation is not open."))
            for candidate in round_.candidate_ids.filtered('in_commercial'):
                if not candidate.analysis_id:
                    Analysis.create({'candidate_id': candidate.id})._normalise()
                else:
                    candidate.analysis_id._normalise()
        return True

    def action_finalise(self):
        """Score, rank, and stop. Ranking is not awarding."""
        for round_ in self:
            round_._assert_authority(_("finalise the evaluation"))
            round_._assert_forward('finalised')
            if round_.state not in ('commercial_open', 'commercial_final'):
                raise UserError(_(
                    "%s has not been through commercial evaluation.")
                    % round_.name)
            candidates = round_.candidate_ids.filtered('in_commercial')
            missing = candidates.filtered(lambda c: not c.analysis_id)
            if missing:
                raise UserError(_(
                    "%s offer(s) have not been normalised.") % len(missing))
            round_._score_financial(candidates)
            round_._rank(candidates)
            round_.write({
                'state': 'finalised',
                'commercial_finalised_on': fields.Datetime.now(),
                'finalised_on': fields.Datetime.now(),
                'finalised_by_id': self.env.user.id,
            })
            round_.message_post(body=_(
                "Evaluation finalised. This is an evaluation result and not "
                "an award: no purchase is authorised by it."))
        return True

    def _score_financial(self, candidates):
        """Turn evaluated costs into financial scores, per the frozen formula."""
        self.ensure_one()
        plan = self.plan_id
        costs = [c.analysis_id.evaluated_cost for c in candidates
                 if c.analysis_id.evaluated_cost]
        lowest = min(costs) if costs else 0.0
        for candidate in candidates:
            analysis = candidate.analysis_id
            if plan.financial_formula == 'lowest_ratio' and \
                    analysis.evaluated_cost:
                score = (lowest / analysis.evaluated_cost) * 100.0
            else:
                score = 0.0
            analysis._engine().write({'financial_score': round(score, 4)})
            combined = 0.0
            if plan.evaluation_method == 'rated_combined':
                combined = (
                    (candidate.technical_score or 0.0)
                    * (plan.technical_weight or 0.0) / 100.0
                    + score * (plan.commercial_weight or 0.0) / 100.0)
            candidate._engine().write({
                'financial_score': round(score, 4),
                'combined_score': round(combined, 4),
            })
        return True

    def _rank(self, candidates):
        """Order the responsive offers, and say so when two are equal.

        The comparison key is the frozen method's own number, rounded to a
        stated precision. Ties are reported, never broken by database id or
        vendor name — that would invent a winner nobody chose.
        """
        self.ensure_one()
        plan = self.plan_id
        if plan.evaluation_method == 'rated_combined':
            def key(candidate):
                return -round(candidate.combined_score or 0.0, 4)
        else:
            def key(candidate):
                return round(candidate.analysis_id.evaluated_cost or 0.0, 2)

        ordered = candidates.sorted(key=key)
        rank, previous_key, position = 0, None, 0
        tied_any = False
        for candidate in ordered:
            position += 1
            current = key(candidate)
            if previous_key is not None and current == previous_key:
                tied = True
                tied_any = True
            else:
                tied = False
                rank = position
            candidate._engine().write({'rank': rank, 'is_tied': tied})
            previous_key = current

        # A tie only matters where it decides something: at the top.
        top = candidates.filtered(lambda c: c.rank == 1)
        if len(top) > 1:
            top._engine().write({'is_tied': True})
            if plan.tie_rule == 'flag':
                self.write({'result_status': 'tie_requires_decision'})
                return True
        self.write({'result_status': 'ranked' if candidates else
                    'no_responsive_bid'})
        return tied_any

    # ------------------------------------------------------------------
    def action_open_bafo(self, reason=None, partners=None):
        """A further round against the same frozen basis."""
        self.ensure_one()
        self._assert_authority(_("open a best and final offer round"))
        plan = self.plan_id
        if plan.bafo_policy == 'not_allowed':
            raise UserError(_(
                "%s does not allow a best and final offer.") % plan.name)
        if self.state != 'finalised':
            raise UserError(_(
                "A best and final offer follows a completed evaluation."))
        if not reason:
            raise UserError(_(
                "A best and final offer needs a recorded reason. Asking "
                "selected vendors to re-price without one is a preference "
                "with no evidence behind it."))
        shortlist = partners
        responsive = self.candidate_ids.filtered(
            lambda c: c.technical_result == 'responsive').partner_id
        if plan.bafo_policy == 'all_responsive':
            shortlist = responsive
        elif not shortlist:
            raise UserError(_("Name the vendors invited to re-offer."))
        else:
            outside = shortlist - responsive
            if outside:
                raise UserError(_(
                    "%s did not pass technical evaluation and cannot be "
                    "invited to re-offer.") % ', '.join(
                        outside.mapped('display_name')))
        round_ = self.create({
            'sourcing_event_id': self.sourcing_event_id.id,
            'plan_id': plan.id,
            'round_type': 'bafo',
            'previous_round_id': self.id,
            'shortlist_reason': reason,
            'invited_partner_ids': [(6, 0, shortlist.ids)],
        })
        self.message_post(body=_(
            "Best and final offer round %(name)s opened for %(vendors)s: "
            "%(reason)s",
            name=round_.name, vendors=', '.join(shortlist.mapped('display_name')),
            reason=reason))
        return round_

    def action_print_evaluation_report(self):
        """The Bid Evaluation Report — for people entitled to its contents.

        Two things guard this document and they guard different halves.
        `groups=` on the template's commercial sections means QWeb never
        renders sections 5 and 6 for anybody outside the commercial roles, so
        the numbers cannot escape through the HTML route either. This method
        is the other half: it refuses to *issue* the report at all, so an
        unauthorised user gets a refusal rather than a quietly gutted document
        they might mistake for the whole evaluation.
        """
        self.ensure_one()
        self._assert_authority(_("issue the evaluation report"),
                               groups=COMMERCIAL_WORK_GROUPS)
        if self.state != 'finalised':
            raise UserError(_(
                "%s is not finalised. An evaluation report issued mid-"
                "evaluation would state a result that has not been reached.")
                % self.name)
        return self.env.ref(
            'real_estate_procurement.action_report_evaluation'
        ).report_action(self)

    def action_cancel(self, reason=None):
        """Abandon a round, on the record, and never a finished one.

        Both guards were missing and both matter. A finalised evaluation is
        the evidence that a competition was judged; cancelling it would erase
        that outcome while leaving the offers behind, and the file would no
        longer say what was decided. And an abandoned evaluation with no
        recorded reason is indistinguishable from one abandoned because its
        result was unwelcome.
        """
        for round_ in self:
            round_._assert_authority(_("cancel an evaluation round"))
            if round_.state == 'finalised':
                raise UserError(_(
                    "%s is finalised. A completed evaluation is evidence that "
                    "the offers were judged; cancel it and the file no longer "
                    "records what was decided. Raise a new round instead.")
                    % round_.name)
            if not reason:
                raise UserError(_(
                    "Cancelling an evaluation needs a reason."))
            round_.write({'state': 'cancelled', 'reopen_reason': reason})
        return True

    # ------------------------------------------------------------------
    # A fixture helper used to live here — `action_open_commercial_after_
    # technical`, which scored every candidate as a pass and drove the round to
    # the commercial stage. It was public, so it was callable over RPC by
    # anybody the ACL let write on a round: one call and every bid in a live
    # tender held a submitted passing sheet nobody had written. It has moved to
    # `tests/common.py::M6Common._advance_to_commercial`, where the browser
    # classes still inherit it and production no longer carries it.

    def _engine(self):
        return self.sudo().with_context(re_evaluation_engine=True)


class EvaluationAssignment(models.Model):
    _name = 'realestate.procurement.evaluation.assignment'
    _description = 'Evaluation Committee Assignment'
    _order = 'round_id, id'

    round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='round_id.company_id', store=True, index=True)
    user_id = fields.Many2one('res.users', required=True, index=True)
    #: Recorded rather than derived. A committee is who evaluated, and that
    #: does not change because somebody left the group afterwards.
    user_name = fields.Char(readonly=True)
    role = fields.Selection(ROLES, required=True)
    assigned_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    assigned_by_id = fields.Many2one(
        'res.users', readonly=True, default=lambda self: self.env.user)

    conflict_status = fields.Selection([
        ('undeclared', 'Not Declared'),
        ('none', 'Declared — No Conflict'),
        ('declared', 'Conflict Declared'),
        ('cleared', 'Conflict Cleared'),
        ('blocked', 'Blocked'),
    ], default='undeclared', required=True)
    declared_on = fields.Datetime(readonly=True)
    declaration = fields.Text()
    cleared_by_id = fields.Many2one('res.users', readonly=True)
    cleared_on = fields.Datetime(readonly=True)

    _sql_constraints = [
        ('member_uniq', 'unique(round_id, user_id, role)',
         'A committee member holds each role once per round.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            record.user_name = record.user_id.display_name
        return records

    def action_declare(self, conflict=False, details=None):
        """No scoring without a declaration on the record.

        A conflict declaration is a personal statement, so it is made by the
        person or by whoever runs the round recording what they were told.
        Anybody with write access declaring "no conflict" on somebody else's
        behalf would put words in their mouth and leave the file saying a
        declaration exists when it does not.
        """
        for assignment in self:
            assignment._assert_may_declare()
            if conflict and not details:
                raise UserError(_(
                    "A declared conflict needs its details."))
            assignment.write({
                'conflict_status': 'declared' if conflict else 'none',
                'declared_on': fields.Datetime.now(),
                'declaration': details,
            })
        return True

    def action_clear_conflict(self, note=None):
        for assignment in self:
            assignment.round_id._assert_authority(
                _("clear a conflict-of-interest declaration"))
            if assignment.conflict_status != 'declared':
                raise UserError(_("Nothing is declared to clear."))
            if assignment.user_id == self.env.user:
                raise UserError(_(
                    "%s declared this conflict. Clearing your own is not a "
                    "review.") % self.env.user.display_name)
            assignment.write({
                'conflict_status': 'cleared',
                'cleared_by_id': self.env.user.id,
                'cleared_on': fields.Datetime.now(),
                'declaration': (assignment.declaration or '')
                + ('\n%s' % note if note else ''),
            })
        return True

    def _assert_may_declare(self):
        self.ensure_one()
        if self.env.su or self.user_id == self.env.user:
            return True
        if any(self.env.user.has_group(xmlid) for xmlid in LIFECYCLE_GROUPS):
            return True
        raise AccessError(_(
            "A conflict-of-interest declaration is %(member)s's own "
            "statement. %(user)s cannot make it for them.",
            member=self.user_id.display_name,
            user=self.env.user.display_name))

    def _may_score(self):
        self.ensure_one()
        return self.conflict_status in ('none', 'cleared')
