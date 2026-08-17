# -*- coding: utf-8 -*-
"""M6 — the evaluation plan: the basis, frozen before anybody scores.

```
    M6 EVALUATES THE BIDS.
    M6 DOES NOT AWARD THE CONTRACT.
```

The plan holds every decision that must exist *before* the offers are looked
at: which method, in which currency, at which rate date, against which
criteria, weighted how, with what threshold, and what happens on a tie.

### Why freezing is the whole point

A weighting changed from 20% to 35% after the scores are visible is not a
methodology correction, it is choosing the winner and writing the rule
afterwards. Nothing in this file lets that happen quietly: once the plan is
frozen its criteria, weights, formula, currency and rate basis refuse to be
written, and a genuine change of mind produces a **new revision** with its own
reason and author while the old one is superseded rather than edited.

### Nothing here is a default anybody should inherit silently

There is no universal 70/30, no universal threshold, no universal financial
formula. Those are procurement policy, they differ per company and per tender,
and a module that shipped one as a default would be handing every buyer a
methodology they never chose. The plan therefore requires them and validates
that they reconcile before it will freeze.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

#: States in which the basis is settled and may no longer be edited.
SETTLED_STATES = ('frozen', 'in_use', 'superseded')

#: Settling or revising the basis decides how every offer will be judged. The
#: ACL lets a Buyer write on a plan so they can draft one; committing that
#: draft as *the* methodology is the Evaluation Manager's act.
LIFECYCLE_GROUPS = (
    'real_estate_procurement.group_evaluation_manager',
    'real_estate_procurement.group_procurement_manager',
)

#: Fields that *are* the methodology. Writing any of them after freeze is what
#: this model exists to prevent.
BASIS_FIELDS = {
    'evaluation_method', 'evaluation_currency_id', 'rate_date_basis',
    'rate_date', 'technical_threshold', 'technical_weight',
    'commercial_weight', 'financial_formula', 'committee_mode',
    'allow_partial_bids', 'bafo_policy', 'tie_rule', 'criterion_ids',
    'sourcing_event_id', 'sourcing_version_id',
}


class EvaluationPlan(models.Model):
    _name = 'realestate.procurement.evaluation.plan'
    _description = 'Procurement Evaluation Plan'
    _inherit = ['mail.thread']
    _order = 'sourcing_event_id, revision desc'

    name = fields.Char(readonly=True, copy=False, default=lambda s: _('New'))
    sourcing_event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    sourcing_version_id = fields.Many2one(
        'realestate.procurement.sourcing.version',
        string='Declared With Tender Version',
        help="The issued tender version this basis was declared with, where "
             "company policy requires criteria to have been disclosed. Left "
             "empty for a tender issued before the plan existed — a legacy "
             "gap that is recorded rather than back-dated.")
    company_id = fields.Many2one(
        related='sourcing_event_id.company_id', store=True, index=True)
    revision = fields.Integer(default=0, readonly=True)
    supersedes_id = fields.Many2one(
        'realestate.procurement.evaluation.plan', readonly=True)
    revision_reason = fields.Text(readonly=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'In Review'),
        ('frozen', 'Frozen'),
        ('in_use', 'In Use'),
        ('superseded', 'Superseded'),
    ], default='draft', required=True, tracking=True, copy=False)
    frozen_on = fields.Datetime(readonly=True, copy=False)
    frozen_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    evaluation_method = fields.Selection([
        ('pass_fail_lowest', 'Pass/Fail, then lowest evaluated cost'),
        ('rated_combined', 'Rated technical and commercial, combined score'),
    ], required=True, default='pass_fail_lowest', tracking=True)

    evaluation_currency_id = fields.Many2one(
        'res.currency', required=True, string='Evaluation Currency',
        default=lambda self: self.env.company.currency_id,
        help="Every offer is compared in this currency. Frozen with the plan "
             "so that a later change of company currency cannot re-rank a "
             "finished evaluation.")
    rate_date_basis = fields.Selection([
        ('tender_close', 'Tender closing date'),
        ('issue_date', 'Tender issue date'),
        ('fixed', 'A fixed date'),
    ], required=True, default='tender_close',
        help="Which day's exchange rate every offer is converted at. One day "
             "for all of them: converting each bid at the date it happened to "
             "arrive would rank vendors on the currency market rather than on "
             "their offers.")
    rate_date = fields.Date(
        help="Used when the basis is a fixed date.")

    technical_threshold = fields.Float(
        string='Technical Threshold (%)', default=0.0,
        help="The minimum weighted technical score a bid must reach to be "
             "technically responsive. Zero means no threshold — which is a "
             "decision, not an absence of one.")
    technical_weight = fields.Float(string='Technical Weight (%)')
    commercial_weight = fields.Float(string='Commercial Weight (%)')
    financial_formula = fields.Selection([
        ('lowest_ratio', 'Lowest evaluated cost ÷ this cost × 100'),
        ('lowest_cost_only', 'Lowest evaluated cost wins (no financial score)'),
    ], required=True, default='lowest_cost_only',
        help="How an evaluated cost becomes a score. The lowest-ratio method "
             "is common but it is not a law of procurement, so it is chosen "
             "here rather than assumed.")
    committee_mode = fields.Selection([
        ('average', 'Average of submitted evaluator scores'),
        ('consensus', 'Committee consensus, recorded separately'),
    ], required=True, default='average')
    tie_rule = fields.Selection([
        ('flag', 'Flag the tie for a decision'),
        ('lowest_cost', 'Lowest evaluated cost breaks the tie'),
    ], required=True, default='flag',
        help="What to do when two offers evaluate identically. Defaults to "
             "flagging: breaking a tie by database id or vendor name invents "
             "a winner nobody chose.")
    allow_partial_bids = fields.Boolean(
        help="Whether an offer missing tender lines can still be evaluated.")
    bafo_policy = fields.Selection([
        ('not_allowed', 'Not allowed'),
        ('all_responsive', 'All technically responsive bidders'),
        ('selective', 'Selective, with a recorded reason'),
    ], required=True, default='not_allowed')

    criterion_ids = fields.One2many(
        'realestate.procurement.evaluation.criterion', 'plan_id')
    round_ids = fields.One2many(
        'realestate.procurement.evaluation.round', 'plan_id')
    rated_weight_total = fields.Float(compute='_compute_weight_totals')
    notes = fields.Text()

    _sql_constraints = [
        ('revision_uniq', 'unique(sourcing_event_id, revision)',
         'An evaluation plan revision is issued once per sourcing event.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('criterion_ids.weight', 'criterion_ids.criterion_type')
    def _compute_weight_totals(self):
        for plan in self:
            plan.rated_weight_total = sum(plan.criterion_ids.filtered(
                lambda c: c.criterion_type == 'rated').mapped('weight'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.evaluation.plan') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_freeze(self):
        """Settle the basis. Everything is validated before it is settled."""
        for plan in self:
            plan._assert_authority(_("freeze an evaluation basis"))
            if plan.state in SETTLED_STATES:
                raise UserError(_("%s is already frozen.") % plan.name)
            plan._validate_basis()
            plan.write({
                'state': 'frozen',
                'frozen_on': fields.Datetime.now(),
                'frozen_by_id': self.env.user.id,
            })
            plan.message_post(body=_(
                "Evaluation basis frozen: %(method)s, %(currency)s at the "
                "%(basis)s rate.",
                method=dict(plan._fields['evaluation_method'].selection)[
                    plan.evaluation_method],
                currency=plan.evaluation_currency_id.name,
                basis=dict(plan._fields['rate_date_basis'].selection)[
                    plan.rate_date_basis]))
        return True

    def _validate_basis(self):
        """Refuse a plan that does not add up. Never silently normalise it."""
        self.ensure_one()
        if not self.criterion_ids:
            raise UserError(_(
                "%s has no criteria. An evaluation with no declared basis is "
                "an opinion.") % self.name)
        rated = self.criterion_ids.filtered(
            lambda c: c.criterion_type == 'rated')

        if self.evaluation_method == 'rated_combined':
            total = (self.technical_weight or 0.0) + \
                (self.commercial_weight or 0.0)
            if abs(total - 100.0) > 0.001:
                raise UserError(_(
                    "Technical %(tech)s%% and commercial %(comm)s%% come to "
                    "%(total)s%%. They must come to 100%%: scaling them for "
                    "you would be choosing a methodology on your behalf.",
                    tech=self.technical_weight, comm=self.commercial_weight,
                    total=total))
            if not rated:
                raise UserError(_(
                    "A combined score needs rated technical criteria."))

        if rated:
            weight_total = sum(rated.mapped('weight'))
            if abs(weight_total - 100.0) > 0.001:
                raise UserError(_(
                    "The rated criteria weights come to %(total)s%%, not "
                    "100%%. Correct them — they will not be normalised "
                    "silently, because a criterion nobody meant to weight at "
                    "12.5%% would then decide the tender.",
                    total=weight_total))
            zero = rated.filtered(lambda c: not c.max_score)
            if zero:
                raise UserError(_(
                    "These rated criteria have no maximum score, so nothing "
                    "can be scored against them: %s")
                    % ', '.join(zero.mapped('name')))

        if self.rate_date_basis == 'fixed' and not self.rate_date:
            raise UserError(_(
                "The rate basis is a fixed date and no date is set."))
        if not self.evaluation_currency_id:
            raise UserError(_("An evaluation currency is required."))
        return True

    def action_new_revision(self, reason=None):
        """Change the basis by superseding it, never by editing it."""
        self.ensure_one()
        self._assert_authority(_("revise an evaluation basis"))
        if not reason:
            raise UserError(_(
                "A revised evaluation basis needs a reason. Changing the "
                "rules without one is indistinguishable from changing them to "
                "suit the offers."))
        if self.round_ids.filtered(
                lambda r: r.state not in ('draft', 'cancelled')):
            raise UserError(_(
                "%s is already being evaluated against. Cancel or complete "
                "the round before revising the basis — rewriting the rules "
                "mid-evaluation is what the freeze exists to prevent.")
                % self.name)
        copy = self.copy({
            'revision': self.revision + 1,
            'state': 'draft',
            'supersedes_id': self.id,
            'revision_reason': reason,
            'frozen_on': False,
            'frozen_by_id': False,
            'name': _('New'),
        })
        self.write({'state': 'superseded'})
        self.message_post(body=_("Superseded by revision %(rev)s: %(reason)s",
                                 rev=copy.revision, reason=reason))
        return copy

    # ------------------------------------------------------------------
    def write(self, vals):
        if not self.env.context.get('re_evaluation_engine'):
            touched = set(vals) & BASIS_FIELDS
            for plan in self:
                if plan.state in SETTLED_STATES and touched:
                    raise UserError(_(
                        "%(name)s was frozen on %(when)s. Its basis cannot be "
                        "edited — %(fields)s would change how offers already "
                        "under evaluation are judged. Raise a revision "
                        "instead.",
                        name=plan.name, when=plan.frozen_on,
                        fields=', '.join(sorted(touched))))
        return super().write(vals)

    def unlink(self):
        for plan in self:
            if plan.state in SETTLED_STATES:
                raise UserError(_(
                    "%s is a frozen evaluation basis and offers were judged "
                    "against it.") % plan.name)
        return super().unlink()

    def _assert_authority(self, what):
        if self.env.su:
            return True
        if any(self.env.user.has_group(xmlid) for xmlid in LIFECYCLE_GROUPS):
            return True
        raise AccessError(_(
            "%(user)s is not entitled to %(what)s. The methodology decides "
            "how every offer is judged; write access to the draft is not the "
            "same permission.",
            user=self.env.user.display_name, what=what))

    def _engine(self):
        return self.sudo().with_context(re_evaluation_engine=True)

    def rate_date_for(self):
        """The single day every offer in this plan is converted at."""
        self.ensure_one()
        event = self.sourcing_event_id
        if self.rate_date_basis == 'fixed':
            return self.rate_date
        if self.rate_date_basis == 'issue_date':
            return (event.issue_datetime or fields.Datetime.now()).date()
        closed = event.actual_closed_datetime or event.close_datetime
        return (closed or fields.Datetime.now()).date()


class EvaluationCriterion(models.Model):
    _name = 'realestate.procurement.evaluation.criterion'
    _description = 'Evaluation Criterion'
    _order = 'plan_id, sequence, id'

    plan_id = fields.Many2one(
        'realestate.procurement.evaluation.plan', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='plan_id.company_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    description = fields.Text()
    category = fields.Char(
        help="Free grouping — methodology, capability, programme, quality. "
             "Deliberately not a fixed list: the categories that matter "
             "differ between a pump package and a curtain-wall subcontract.")
    criterion_type = fields.Selection([
        ('mandatory', 'Mandatory / Knockout'),
        ('rated', 'Rated'),
    ], required=True, default='rated',
        help="A mandatory criterion is pass or fail and carries no weight: "
             "failing it makes the bid non-responsive whatever else it "
             "scores. A rated criterion contributes its weighted score.")
    evidence_expected = fields.Text(
        help="What a bidder was told to submit against this. Recorded so an "
             "evaluator marks the absence of evidence rather than inferring "
             "it.")
    max_score = fields.Float(default=10.0)
    weight = fields.Float(
        help="Percentage of the technical component. Rated criteria must "
             "come to 100% before the plan will freeze.")
    minimum_score = fields.Float(
        string='Minimum Score',
        help="An optional per-criterion floor. A bid below it is "
             "non-responsive even if its total passes the overall threshold.")
    scoring_guidance = fields.Text(
        help="The score bands this criterion is judged by, in the plan's own "
             "words. No band set is shipped as a default: 5 = 'meets "
             "requirement' is one organisation's convention, not a fact.")

    @api.constrains('criterion_type', 'weight', 'max_score')
    def _check_shape(self):
        for criterion in self:
            if criterion.criterion_type == 'mandatory' and criterion.weight:
                raise ValidationError(_(
                    "%s is a mandatory criterion, so it cannot carry a "
                    "weight. Passing it is not worth points; failing it ends "
                    "the bid.") % criterion.name)
            if criterion.weight < 0 or criterion.max_score < 0:
                raise ValidationError(_(
                    "A negative weight or maximum score on %s.")
                    % criterion.name)

    def write(self, vals):
        if not self.env.context.get('re_evaluation_engine'):
            for criterion in self:
                if criterion.plan_id.state in SETTLED_STATES:
                    raise UserError(_(
                        "%(name)s belongs to a frozen evaluation basis. "
                        "Changing it now would change how offers already "
                        "being scored are judged.", name=criterion.name))
        return super().write(vals)

    def unlink(self):
        for criterion in self:
            if criterion.plan_id.state in SETTLED_STATES:
                raise UserError(_(
                    "%s belongs to a frozen evaluation basis.")
                    % criterion.name)
        return super().unlink()
