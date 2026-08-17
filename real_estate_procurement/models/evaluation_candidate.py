# -*- coding: utf-8 -*-
"""M6 — one bid inside one round, and the sheets that judge it.

The candidate is where a submitted offer meets an evaluation. It carries the
technical outcome, the commercial outcome and the rank — and it carries them
*per round*, because the same bid evaluated under a revised basis is a
different judgement and must not overwrite the first one.

### Price blindness is structural, not cosmetic

`realestate.procurement.technical.evaluation` has no monetary field at all —
no price, no amount, no cost, no currency. A test enumerates its fields and
fails if one ever appears. That is the difference between an evaluator who
cannot see the money and one who merely has it hidden in a view.

The honest limit, stated here and in the report: M5 keeps commercial data in
native Odoo purchase structures, so this is **controlled role segregation, not
cryptographic sealed bidding**. A user with sweeping native Purchase
administration rights can still read a `purchase.order`. What M6 guarantees is
that nothing *it* builds hands price to a technical evaluator, and that its own
commercial records refuse them outright.

### The candidate is where that guarantee nearly leaked

A read-only audit found the outcome fields on this model — `financial_score`,
`combined_score`, `rank`, `is_tied` and the `analysis_id` pointer — carrying no
field-level restriction while the ACL grants a Technical Evaluator read on the
record. Under the `lowest_ratio` formula the financial score is a monotonic
function of evaluated cost, so those fields disclose the **complete commercial
ordering** without ever showing a price. It is inert while a first round is
being scored (they are all zero until finalisation) and live the moment a BAFO
or a second round is evaluated by the same committee.

Closing it takes four things, because a field restriction alone is not enough:

1. `groups=` on the five fields, so `read`, `search_read` and `fields_get`
   refuse them.
2. A domain guard — Odoo's expression engine does **not** consult field groups,
   so `search([('rank', '=', 1)])` would otherwise answer honestly.
3. An order guard, and this is the one that is easy to miss: `_order` sorts by
   `rank`. A technical evaluator calling `search([])` would have received the
   vendors already arranged in the order the money put them in, having read
   nothing at all.
4. A `_read_group` guard, so the same question cannot be asked as an aggregate.

The gate is `group_procurement_user`, and that is deliberate rather than
convenient: every commercially-entitled role implies it (Commercial Evaluator
directly, Evaluation Manager and Procurement Manager transitively) while
`group_evaluation_technical` implies nothing at all. `group_evaluation_commercial`
is named alongside it so the intent survives someone later editing that
implication.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

#: Once a sheet is submitted it is that evaluator's opinion, on the record.
SUBMITTED_STATES = ('submitted', 'consolidated')

#: Everything on the candidate that describes the *commercial* outcome. Read,
#: filtered on, ordered by or aggregated, any one of them reveals the ranking.
COMMERCIAL_FIELDS = frozenset({
    'analysis_id', 'financial_score', 'combined_score', 'rank', 'is_tied',
})

#: Groups entitled to the commercial outcome. OR semantics, and redundant only
#: for as long as `group_evaluation_commercial` keeps implying the first.
COMMERCIAL_GROUPS = (
    'real_estate_procurement.group_procurement_user,'
    'real_estate_procurement.group_evaluation_commercial'
)

#: The order served to everybody else. `_order` sorts by `rank`, which *is* the
#: commercial answer; this one carries no outcome in it.
TECHNICAL_ORDER = 'round_id, id'


class EvaluationCandidate(models.Model):
    _name = 'realestate.procurement.evaluation.candidate'
    _description = 'Evaluation Candidate'
    #: Deliberately not `rank`. Two separate reasons, and the second is the
    #: one that bites:
    #:
    #: 1. `_order` *is* the ranking. A technical evaluator calling search([])
    #:    would have been handed the vendors in the order the money put them
    #:    in, having read nothing at all.
    #: 2. Odoo 18 resolves ORDER BY through `_order_field_to_sql`, which calls
    #:    `check_field_access_rights` — and an ordering clause chains through
    #:    many2ones. With `rank` restricted and still named here, reading an
    #:    unrelated `sheet.line_ids` raised AccessError three models away,
    #:    because line → sheet → candidate → rank.
    #:
    #: Anything that genuinely wants rank order asks for it: the report sorts
    #: explicitly, and a commercially entitled user may pass `order='rank'`.
    _order = 'round_id, id'
    _rec_name = 'partner_id'

    round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='round_id.company_id', store=True, index=True)
    project_id = fields.Many2one(
        related='round_id.project_id', store=True, index=True)
    plan_id = fields.Many2one(related='round_id.plan_id', store=True)
    bid_response_id = fields.Many2one(
        'realestate.procurement.bid.response', required=True, readonly=True,
        index=True)
    invitation_id = fields.Many2one(
        'realestate.procurement.sourcing.invitation', readonly=True)
    partner_id = fields.Many2one(
        related='bid_response_id.partner_id', store=True, index=True,
        string='Vendor')
    partner_name = fields.Char(
        readonly=True, string='Vendor (as evaluated)',
        help="The vendor's name at the moment this candidate was entered into "
             "the evaluation.\n\n"
             "Snapshotted rather than read live, and `partner_id` is a related "
             "field all the way back to `res.partner`: a vendor renamed after "
             "the event — a merger, a change of legal name — would otherwise "
             "silently restate who the committee actually evaluated, on a "
             "document whose whole purpose is to say exactly that.")

    #: Candidate-set evidence, written once at technical opening.
    in_technical = fields.Boolean(readonly=True, default=True)
    in_commercial = fields.Boolean(readonly=True)
    exclusion_reason = fields.Text(readonly=True)
    administrative_status = fields.Char(readonly=True)

    technical_score = fields.Float(readonly=True, string='Technical Score')
    technical_result = fields.Selection([
        ('pending', 'Pending'),
        ('responsive', 'Technically Responsive'),
        ('non_responsive', 'Technically Non-Responsive'),
        ('not_evaluated', 'Not Evaluated'),
        ('withdrawn', 'Withdrawn'),
    ], default='pending', readonly=True)
    technical_result_reason = fields.Text(readonly=True)
    score_spread = fields.Float(
        readonly=True,
        help="The gap between the highest and lowest evaluator on this bid. "
             "Published rather than averaged away: two evaluators at 9 and 3 "
             "are not a 6, they are a disagreement the committee should see.")
    consensus_note = fields.Text(readonly=True)

    # -- The commercial outcome. Restricted; see the module docstring. --------
    analysis_id = fields.Many2one(
        'realestate.procurement.commercial.analysis', readonly=True,
        copy=False, groups=COMMERCIAL_GROUPS)
    financial_score = fields.Float(readonly=True, groups=COMMERCIAL_GROUPS)
    combined_score = fields.Float(readonly=True, groups=COMMERCIAL_GROUPS)
    rank = fields.Integer(readonly=True, copy=False, groups=COMMERCIAL_GROUPS)
    is_tied = fields.Boolean(readonly=True, copy=False,
                             groups=COMMERCIAL_GROUPS)

    sheet_ids = fields.One2many(
        'realestate.procurement.technical.evaluation', 'candidate_id')

    _sql_constraints = [
        ('bid_uniq', 'unique(round_id, bid_response_id)',
         'A bid appears once in an evaluation round.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        candidates = super().create(vals_list)
        for candidate in candidates:
            # Written here rather than in `_freeze_candidates`, so that every
            # candidate carries the name whatever route created it. No
            # fallback to the live partner anywhere: a blank here must read as
            # blank, not quietly borrow today's answer.
            candidate.partner_name = candidate.partner_id.display_name
        return candidates

    # ------------------------------------------------------------------
    # Commercial confidentiality beyond the field restriction
    # ------------------------------------------------------------------
    def _may_read_commercial(self):
        """Whether this user is entitled to the commercial outcome."""
        if self.env.su:
            return True
        user = self.env.user
        return any(user.has_group(xmlid)
                   for xmlid in COMMERCIAL_GROUPS.split(','))

    @api.model
    def _commercial_terms(self, *specs):
        """The restricted field names appearing anywhere in `specs`.

        Accepts domain leaves, groupby/aggregate descriptions and order
        clauses alike, because every one of them is a way of asking the same
        question. A dotted path, a `:sum` aggregate and a `desc` suffix all
        reduce to their leading field name.
        """
        found = set()

        def note(token):
            name = str(token).strip().split(' ')[0].split(':')[0].split('.')[0]
            if name in COMMERCIAL_FIELDS:
                found.add(name)

        for spec in specs:
            if not spec:
                continue
            if isinstance(spec, str):
                for token in spec.split(','):
                    note(token)
                continue
            for item in spec:
                if isinstance(item, (list, tuple)) and len(item) == 3:
                    note(item[0])
                elif isinstance(item, str):
                    note(item)
        return found

    def _refuse_commercial_terms(self, *specs):
        found = self._commercial_terms(*specs)
        if found:
            raise AccessError(_(
                "The commercial outcome of this evaluation (%(fields)s) is "
                "not available to this account.\n\n"
                "Filtering, sorting or grouping by them answers the same "
                "question as reading them: which offer came first. Technical "
                "evaluation is kept blind to that on purpose.",
                fields=', '.join(sorted(found))))
        return True

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        if not self._may_read_commercial():
            # `search()` resolves `order or self._order` before it reaches
            # here, so an unstated order arrives looking explicit. Treat the
            # model's own default as unstated and serve the neutral one;
            # refuse only what a caller actually asked for.
            if not order or order == self._order:
                order = TECHNICAL_ORDER
            self._refuse_commercial_terms(domain, order)
        return super()._search(domain, offset=offset, limit=limit, order=order)

    @api.model
    def _read_group(self, domain, groupby=(), aggregates=(), having=(),
                    offset=0, limit=None, order=None):
        if not self._may_read_commercial():
            self._refuse_commercial_terms(
                domain, groupby, aggregates, having, order)
        return super()._read_group(
            domain, groupby=groupby, aggregates=aggregates, having=having,
            offset=offset, limit=limit, order=order)

    # ------------------------------------------------------------------
    def _consolidate(self):
        """Turn the submitted sheets into one technical outcome."""
        for candidate in self:
            sheets = candidate.sheet_ids.filtered(
                lambda s: s.state in SUBMITTED_STATES)
            if not sheets:
                candidate._engine().write({
                    'technical_result': 'not_evaluated',
                    'technical_result_reason': _("No evaluator submitted a "
                                                 "sheet for this bid."),
                })
                continue

            knockout = sheets.line_ids.filtered(
                lambda l: l.criterion_type == 'mandatory'
                and l.result == 'fail')
            scores = sheets.mapped('weighted_total')
            plan = candidate.round_id.plan_id
            if plan.committee_mode == 'consensus':
                consensus = sheets.filtered('is_consensus')
                score = consensus[:1].weighted_total if consensus else \
                    (sum(scores) / len(scores))
            else:
                score = sum(scores) / len(scores)

            values = {
                'technical_score': round(score, 4),
                'score_spread': round(max(scores) - min(scores), 4)
                if len(scores) > 1 else 0.0,
            }
            if knockout:
                values.update({
                    'technical_result': 'non_responsive',
                    'technical_result_reason': _(
                        "Failed mandatory criteria: %s")
                    % ', '.join(sorted(set(knockout.mapped('criterion_name')))),
                })
            elif plan.technical_threshold and score < plan.technical_threshold:
                values.update({
                    'technical_result': 'non_responsive',
                    'technical_result_reason': _(
                        "Technical score %(score)s is below the declared "
                        "threshold of %(threshold)s.",
                        score=round(score, 2),
                        threshold=plan.technical_threshold),
                })
            else:
                below = sheets.line_ids.filtered(
                    lambda l: l.criterion_type == 'rated'
                    and l.minimum_score and l.score < l.minimum_score)
                if below:
                    values.update({
                        'technical_result': 'non_responsive',
                        'technical_result_reason': _(
                            "Below the declared minimum on: %s")
                        % ', '.join(sorted(set(below.mapped('criterion_name')))),
                    })
                else:
                    values['technical_result'] = 'responsive'
            candidate._engine().write(values)
        return True

    def _engine(self):
        return self.sudo().with_context(re_evaluation_engine=True)

    def write(self, vals):
        if not self.env.context.get('re_evaluation_engine'):
            for candidate in self:
                if candidate.round_id.state == 'finalised':
                    raise UserError(_(
                        "%s belongs to a finalised evaluation. Reopen the "
                        "round with a reason if it genuinely has to change.")
                        % candidate.display_name)
        return super().write(vals)


class TechnicalEvaluation(models.Model):
    """One evaluator's sheet for one bid. No money anywhere in it."""

    _name = 'realestate.procurement.technical.evaluation'
    _description = 'Technical Evaluation Sheet'
    _order = 'round_id, candidate_id, id'

    round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='round_id.company_id', store=True, index=True)
    candidate_id = fields.Many2one(
        'realestate.procurement.evaluation.candidate', required=True,
        ondelete='cascade', index=True)
    partner_id = fields.Many2one(
        related='candidate_id.partner_id', store=True, string='Vendor')
    evaluator_id = fields.Many2one(
        'res.users', required=True, index=True,
        default=lambda self: self.env.user)
    evaluator_name = fields.Char(readonly=True)
    is_consensus = fields.Boolean(
        readonly=True, default=False,
        help="A committee consensus sheet. It sits beside the individual "
             "sheets and never replaces them.\n\n"
             "The explicit default matters: an unwritten boolean is stored as "
             "NULL, and PostgreSQL does not consider two NULLs equal — so the "
             "unique index below silently permitted duplicate sheets until "
             "`test_c3_an_evaluator_cannot_hold_two_sheets_for_one_bid` "
             "showed that it did.")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('consolidated', 'Consolidated'),
        ('reopened', 'Reopened'),
    ], default='draft', required=True, readonly=True, copy=False)
    submitted_on = fields.Datetime(readonly=True, copy=False)
    reopen_reason = fields.Text(readonly=True, copy=False)
    reopened_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    line_ids = fields.One2many(
        'realestate.procurement.technical.evaluation.line', 'sheet_id')
    weighted_total = fields.Float(readonly=True)
    knockout_failed = fields.Boolean(readonly=True)
    overall_comment = fields.Text()

    _sql_constraints = [
        ('sheet_uniq', 'unique(candidate_id, evaluator_id, is_consensus)',
         'An evaluator has one sheet per bid in a round.'),
    ]

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        sheets = super().create(vals_list)
        for sheet in sheets:
            sheet.evaluator_name = sheet.evaluator_id.display_name
            sheet._build_lines()
            sheet._assert_may_score()
        return sheets

    def _build_lines(self):
        """Snapshot the criteria as they stand, once, onto the sheet.

        Copied rather than related: recomputing a historical score from
        today's weight would silently restate what an evaluator concluded.
        """
        self.ensure_one()
        Line = self.env['realestate.procurement.technical.evaluation.line']
        for criterion in self.round_id.plan_id.criterion_ids:
            Line.create({
                'sheet_id': self.id,
                'criterion_id': criterion.id,
                'criterion_name': criterion.name,
                'criterion_type': criterion.criterion_type,
                'sequence': criterion.sequence,
                'weight': criterion.weight,
                'max_score': criterion.max_score,
                'minimum_score': criterion.minimum_score,
            })
        return True

    def _assert_may_score(self):
        """A declaration first, and no scoring for an unresolved conflict."""
        self.ensure_one()
        assignment = self.round_id.assignment_ids.filtered(
            lambda a: a.user_id == self.evaluator_id
            and a.role in ('technical_evaluator', 'technical_lead'))
        if not assignment:
            # An evaluation manager driving the round is allowed; anybody
            # else needs an assignment.
            manager = self.round_id.assignment_ids.filtered(
                lambda a: a.user_id == self.evaluator_id
                and a.role == 'evaluation_manager')
            if not manager and not (
                    self.env.user.has_group(
                        'real_estate_procurement.group_evaluation_manager')
                    or self.env.user.has_group(
                        'real_estate_procurement.group_procurement_manager')):
                raise UserError(_(
                    "%s is not on the evaluation committee for this round.")
                    % self.evaluator_id.display_name)
            return True
        if not assignment[:1]._may_score():
            raise UserError(_(
                "%(user)s has an unresolved conflict-of-interest declaration "
                "on this round, so their scores cannot be recorded.",
                user=self.evaluator_id.display_name))
        return True

    def action_submit(self):
        """Submit a sheet — as the evaluator whose opinion it is.

        `_assert_may_score` guards *creation*. Submission was left open, so
        anybody the ACL let write on sheets could submit somebody else's
        draft: the record would then carry a named evaluator's signature under
        scores they had not finished, and `_consolidate` would count it.
        """
        for sheet in self:
            sheet._assert_may_submit()
            if sheet.state in SUBMITTED_STATES:
                raise UserError(_("This sheet is already submitted."))
            unscored = sheet.line_ids.filtered(
                lambda l: (l.criterion_type == 'mandatory'
                           and l.result == 'not_evaluated')
                or (l.criterion_type == 'rated' and l.score is False))
            if unscored:
                raise UserError(_(
                    "These criteria have no result yet: %s")
                    % ', '.join(unscored.mapped('criterion_name')))
            missing_rationale = sheet.line_ids.filtered(
                lambda l: l.criterion_type == 'mandatory'
                and l.result == 'fail' and not l.rationale)
            if missing_rationale:
                raise UserError(_(
                    "A mandatory failure ends a vendor's bid, so it needs a "
                    "reason: %s")
                    % ', '.join(missing_rationale.mapped('criterion_name')))
            weighted = sum(
                (line.score / line.max_score) * line.weight
                for line in sheet.line_ids
                if line.criterion_type == 'rated' and line.max_score)
            sheet._engine().write({
                'state': 'submitted',
                'submitted_on': fields.Datetime.now(),
                'weighted_total': round(weighted, 4),
                'knockout_failed': bool(sheet.line_ids.filtered(
                    lambda l: l.criterion_type == 'mandatory'
                    and l.result == 'fail')),
            })
        return True

    def action_reopen(self, reason=None):
        """A correction, on the record, by somebody entitled to ask for it."""
        for sheet in self:
            if not reason:
                raise UserError(_(
                    "Reopening a submitted evaluation needs a reason."))
            if not sheet._user_may_reopen():
                raise UserError(_(
                    "Reopening a submitted evaluation is an Evaluation "
                    "Manager's decision."))
            sheet._engine().write({
                'state': 'reopened',
                'reopen_reason': reason,
                'reopened_by_id': self.env.user.id,
            })
            sheet.round_id.message_post(body=_(
                "%(evaluator)s's sheet for %(vendor)s reopened by "
                "%(user)s: %(reason)s",
                evaluator=sheet.evaluator_name, vendor=sheet.partner_id.display_name,
                user=self.env.user.display_name, reason=reason))
        return True

    def _assert_may_submit(self):
        self.ensure_one()
        if self.env.su or self.evaluator_id == self.env.user:
            return True
        if self._user_may_reopen():
            return True
        raise AccessError(_(
            "This sheet is %(evaluator)s's evaluation. %(user)s cannot submit "
            "it on their behalf.",
            evaluator=self.evaluator_name or self.evaluator_id.display_name,
            user=self.env.user.display_name))

    def _user_may_reopen(self):
        """Who may reopen a submitted sheet.

        The Evaluation Manager, because running the evaluation lifecycle is
        that role's job — and the Procurement Manager, who oversees the whole
        module. Deliberately not the buying Buyer: the person running the
        tender should not be able to send a score back for a second attempt.
        """
        self.ensure_one()
        return (self.env.user.has_group(
            'real_estate_procurement.group_evaluation_manager')
            or self.env.user.has_group(
                'real_estate_procurement.group_procurement_manager'))

    def write(self, vals):
        if not self.env.context.get('re_evaluation_engine'):
            for sheet in self:
                if sheet.state in SUBMITTED_STATES:
                    raise UserError(_(
                        "%(evaluator)s submitted this evaluation on "
                        "%(when)s. It is their opinion on the record — "
                        "reopen it with a reason instead of rewriting it.",
                        evaluator=sheet.evaluator_name,
                        when=sheet.submitted_on))
        return super().write(vals)

    def _engine(self):
        return self.sudo().with_context(re_evaluation_engine=True)


class TechnicalEvaluationLine(models.Model):
    _name = 'realestate.procurement.technical.evaluation.line'
    _description = 'Technical Evaluation Line'
    _order = 'sheet_id, sequence, id'

    sheet_id = fields.Many2one(
        'realestate.procurement.technical.evaluation', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='sheet_id.company_id', store=True, index=True)
    criterion_id = fields.Many2one(
        'realestate.procurement.evaluation.criterion', readonly=True)

    #: Snapshot of the criterion at scoring time. Plain columns on purpose.
    criterion_name = fields.Char(readonly=True)
    criterion_type = fields.Selection([
        ('mandatory', 'Mandatory / Knockout'),
        ('rated', 'Rated'),
    ], readonly=True)
    sequence = fields.Integer(readonly=True)
    weight = fields.Float(readonly=True)
    max_score = fields.Float(readonly=True)
    minimum_score = fields.Float(readonly=True)

    result = fields.Selection([
        ('not_evaluated', 'Not Evaluated'),
        ('pass', 'Pass'),
        ('fail', 'Fail'),
    ], default='not_evaluated')
    score = fields.Float()
    rationale = fields.Text()
    evidence_reference = fields.Char(
        help="Where in the submission the evaluator found the evidence. "
             "Referenced, never copied — the submission is the evidence.")
    deviation_note = fields.Text()

    @api.constrains('score', 'max_score')
    def _check_score(self):
        for line in self:
            if line.criterion_type == 'rated' and line.score and (
                    line.score < 0 or line.score > line.max_score):
                raise UserError(_(
                    "%(name)s is scored out of %(max)s.",
                    name=line.criterion_name, max=line.max_score))

    def write(self, vals):
        if not self.env.context.get('re_evaluation_engine'):
            for line in self:
                if line.sheet_id.state in SUBMITTED_STATES:
                    raise UserError(_(
                        "This line belongs to a submitted evaluation sheet."))
        return super().write(vals)

    def _engine(self):
        return self.sudo().with_context(re_evaluation_engine=True)
