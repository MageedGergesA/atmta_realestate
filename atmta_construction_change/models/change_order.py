# -*- coding: utf-8 -*-
"""M4 — the Change Order: the commercial act, and the only thing that moves a
baseline.

### Amounts are a history, not a field

A change is worth different amounts at different moments, and overwriting one
`amount` destroys the negotiation:

```
    estimated   1,500,000     the site's first view
    quoted      1,900,000     what the contractor asked for
    submitted   1,900,000     what went forward
    assessed    1,650,000     what the QS thought it was worth
    negotiated  1,700,000     what was agreed
    approved    1,700,000     what authority granted
    implemented 1,700,000     what actually moved the baselines
```

Every stage is its own column, on every line, and none of them overwrites
another.

### Approved is not implemented

Approval is authority. Implementation is the transaction that writes budget
and commitment change records. Separating them means an approved change can be
reviewed before it moves anything, and — more importantly — it gives
implementation a single idempotent entry point that cannot run twice.

### What moves, and what does not

```
    budget impact      → budget change records → CURRENT BUDGET
    commitment impact  → commitment change records → CURRENT COMMITMENT
    revenue impact     → owner contract value
    contingency        → a transfer, never a budget increase

    ACTUAL             → never. Only Accounting posts cost.
```
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

ORDER_TYPES = [
    ('contractor_variation', 'Contractor Variation'),
    ('supplier_variation', 'Supplier Variation'),
    ('owner_variation', 'Owner Variation'),
    ('budget_change', 'Budget Change'),
    ('budget_transfer', 'Budget Transfer'),
    ('contingency_drawdown', 'Contingency Drawdown'),
    ('internal_change', 'Internal Change'),
]

ORDER_STATES = [
    ('draft', 'Draft'),
    ('pricing', 'Pricing'),
    ('submitted', 'Submitted'),
    ('under_review', 'Under Review'),
    ('negotiation', 'Negotiation'),
    ('pending_approval', 'Pending Approval'),
    ('approved', 'Approved'),
    ('implemented', 'Implemented'),
    ('closed', 'Closed'),
    ('rejected', 'Rejected'),
    ('withdrawn', 'Withdrawn'),
    ('cancelled', 'Cancelled'),
]

#: Which side of the project a line moves.
IMPACT_SIDES = [
    ('commitment', 'Cost / Commitment'),
    ('budget', 'Authorised Budget'),
    ('revenue', 'Owner Revenue'),
    ('contingency', 'Contingency'),
]

MARKUP_TYPES = [
    ('overhead', 'Overhead'),
    ('profit', 'Profit'),
    ('bond', 'Bond'),
    ('insurance', 'Insurance'),
    ('supervision', 'Supervision'),
    ('contingency', 'Contingency Allowance'),
    ('other', 'Other'),
]


class ConstructionChangeOrder(models.Model):
    _name = 'realestate.construction.change.order'
    _description = 'Construction Change Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    reason = fields.Html()

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
        help="The contract's currency. Control amounts are converted to the "
             "company currency for the cost report; no FX is posted.")
    order_type = fields.Selection(
        ORDER_TYPES, required=True, default='contractor_variation',
        tracking=True, index=True)

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', string='Change Event',
        ondelete='set null', index=True, tracking=True,
        help="Where this came from. Never lost — one event may produce "
             "several orders, and several events may be consolidated into "
             "one.")
    related_event_ids = fields.Many2many(
        'realestate.construction.change.event',
        relation='construction_change_order_event_rel',
        column1='order_id', column2='event_id',
        string='Related Events',
        help="Additional events consolidated into this one order.")

    package_id = fields.Many2one(
        'realestate.construction.contract.package', string='Package',
        ondelete='restrict', check_company=True,
        domain="[('project_id', '=', project_id)]", tracking=True)
    contractor_id = fields.Many2one(
        'realestate.contractor', ondelete='restrict', tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Owner / Customer',
        help="For an owner variation: who is paying for it.")

    line_ids = fields.One2many(
        'realestate.construction.change.order.line', 'order_id',
        string='Impact Lines', copy=True)
    markup_ids = fields.One2many(
        'realestate.construction.change.order.markup', 'order_id',
        string='Markups', copy=True,
        help="Kept separate from the scope. A markup buried in a unit rate "
             "cannot be negotiated, and cannot be audited.")

    # ----- dates, each meaning one thing (§52) -----
    initiated_date = fields.Date(
        default=fields.Date.context_today, tracking=True)
    quote_date = fields.Date(tracking=True)
    submitted_date = fields.Date(tracking=True)
    decision_date = fields.Date(tracking=True)
    effective_date = fields.Date(
        tracking=True,
        help="When the change takes commercial effect. Defaults to the "
             "approval date.")
    implementation_date = fields.Datetime(readonly=True, copy=False)

    # ----- amount stages, none overwriting another (§12) -----
    estimated_amount = fields.Monetary(
        compute='_compute_amounts', store=True)
    contractor_quote_amount = fields.Monetary(tracking=True)
    quotation_reference = fields.Char()
    quotation_validity = fields.Date()
    proposed_amount = fields.Monetary(compute='_compute_amounts', store=True)
    submitted_amount = fields.Monetary(compute='_compute_amounts', store=True)
    assessed_amount = fields.Monetary(compute='_compute_amounts', store=True)
    negotiated_amount = fields.Monetary(compute='_compute_amounts', store=True)
    approved_amount = fields.Monetary(
        compute='_compute_amounts', store=True, tracking=True)

    approved_cost_amount = fields.Monetary(
        compute='_compute_amounts', store=True,
        help="Approved impact on cost/commitment only.")
    approved_budget_amount = fields.Monetary(
        compute='_compute_amounts', store=True)
    approved_revenue_amount = fields.Monetary(
        compute='_compute_amounts', store=True)
    approved_contingency_amount = fields.Monetary(
        compute='_compute_amounts', store=True)
    gross_impact = fields.Monetary(
        compute='_compute_amounts', store=True,
        help="Sum of absolute line impacts. A transfer nets to zero and is "
             "still a decision worth approving, so authority is judged on "
             "this, not on the net.")
    change_margin = fields.Monetary(
        compute='_compute_amounts', store=True,
        help="Approved revenue change − approved cost change. Variation-level "
             "margin, not project profit.")

    markup_total = fields.Monetary(compute='_compute_amounts', store=True)

    # ----- schedule (§30) -----
    estimated_days = fields.Integer(tracking=True)
    submitted_days = fields.Integer(tracking=True)
    approved_days = fields.Integer(
        tracking=True,
        help="Recorded here; M8's EOT workflow decides entitlement and moves "
             "any completion date.")

    state = fields.Selection(
        ORDER_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    revision = fields.Integer(default=0, readonly=True, copy=False)
    revision_ids = fields.One2many(
        'realestate.construction.change.order.revision', 'order_id',
        string='Submission History')

    responsible_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    approval_ids = fields.One2many(
        'realestate.construction.change.approval', 'order_id',
        string='Approvals')
    requires_approval_by = fields.Char(compute='_compute_authority')
    approval_threshold = fields.Monetary(compute='_compute_authority')

    # ----- implementation links, which are what make it idempotent -----
    # The implementation links -- the budget and commitment change records an
    # approved order produces -- are declared by `real_estate_construction`,
    # which owns those models. What is idempotent about implementation is the
    # existence of those records, so the check that reads them is restated up
    # there with the relation it needs.
    is_implemented = fields.Boolean(
        compute='_compute_implemented', store=True)

    notes = fields.Html()
    age_days = fields.Integer(compute='_compute_age')

    # ------------------------------------------------------------------
    @api.depends('line_ids.estimated_amount', 'line_ids.proposed_amount',
                 'line_ids.submitted_amount', 'line_ids.assessed_amount',
                 'line_ids.negotiated_amount', 'line_ids.approved_amount',
                 'line_ids.impact_side', 'markup_ids.amount', 'state')
    def _compute_amounts(self):
        for rec in self:
            lines = rec.line_ids
            rec.markup_total = sum(rec.markup_ids.mapped('amount'))
            rec.estimated_amount = sum(lines.mapped('estimated_amount'))
            rec.proposed_amount = sum(lines.mapped('proposed_amount'))
            rec.submitted_amount = sum(lines.mapped('submitted_amount'))
            rec.assessed_amount = sum(lines.mapped('assessed_amount'))
            rec.negotiated_amount = sum(lines.mapped('negotiated_amount'))
            rec.approved_amount = sum(lines.mapped('approved_amount'))
            rec.gross_impact = sum(abs(l.approved_amount or
                                       l.negotiated_amount or
                                       l.submitted_amount or
                                       l.estimated_amount) for l in lines)

            def side(name):
                return sum(l.approved_amount for l in lines
                           if l.impact_side == name)

            rec.approved_cost_amount = side('commitment')
            rec.approved_budget_amount = side('budget')
            rec.approved_revenue_amount = side('revenue')
            rec.approved_contingency_amount = side('contingency')
            rec.change_margin = (
                rec.approved_revenue_amount - rec.approved_cost_amount)

    @api.depends('state')
    def _compute_implemented(self):
        for rec in self:
            rec.is_implemented = rec.state in ('implemented', 'closed')

    def _authority_rule(self):
        """The approval rule this order falls under, or None.

        Seam. The authority matrix is a `real_estate_construction` model. On
        its own this module states no threshold, which is the truthful answer
        when nothing has defined one; that module overrides it with the rule.
        """
        self.ensure_one()
        return None

    def _compute_authority(self):
        for rec in self:
            rule = rec._authority_rule()
            rec.requires_approval_by = rule['group_name'] if rule else ''
            rec.approval_threshold = rule['threshold'] if rule else 0.0

    def _compute_age(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.age_days = (
                (today - rec.initiated_date).days if rec.initiated_date else 0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.change.order') or 'CO/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_price(self):
        for rec in self:
            rec._require_state(('draft',), _('sent for pricing'))
            rec.state = 'pricing'
        return True

    def action_submit(self):
        for rec in self:
            rec._require_state(('draft', 'pricing', 'negotiation'),
                               _('submitted'))
            if not rec.line_ids:
                raise UserError(_("A change with no impact lines changes "
                                  "nothing."))
            rec._snapshot_revision()
            rec.write({'state': 'submitted',
                       'submitted_date': fields.Date.context_today(rec)})
        return True

    def action_review(self):
        for rec in self:
            rec._require_state(('submitted',), _('reviewed'))
            rec.state = 'under_review'
        return True

    def action_negotiate(self):
        for rec in self:
            rec._require_state(('submitted', 'under_review'), _('negotiated'))
            rec.state = 'negotiation'
        return True

    def action_request_approval(self):
        for rec in self:
            rec._require_state(('submitted', 'under_review', 'negotiation'),
                               _('sent for approval'))
            rec.state = 'pending_approval'
        return True

    def action_approve(self):
        """Grant authority. Nothing moves yet — that is `action_implement`."""
        for rec in self:
            rec._require_state(('pending_approval',), _('approved'))
            rec._check_authority()
            rec._validate_for_approval()
            rec.line_ids._default_approved_amounts()
            rec.write({
                'state': 'approved',
                'decision_date': fields.Date.context_today(rec),
                'effective_date': (rec.effective_date or
                                   fields.Date.context_today(rec)),
            })
            self.env['realestate.construction.change.approval'].create({
                'order_id': rec.id,
                'user_id': self.env.user.id,
                'decision': 'approved',
                'amount_at_decision': rec.approved_amount,
            })
            rec.message_post(body=_(
                "Approved: %(amount)s (gross impact %(gross)s).",
                amount=rec.currency_id.format(rec.approved_amount),
                gross=rec.currency_id.format(rec.gross_impact)))
        return True

    def action_reject(self):
        for rec in self:
            rec._require_state(
                ('submitted', 'under_review', 'negotiation',
                 'pending_approval'), _('rejected'))
            rec.state = 'rejected'
            self.env['realestate.construction.change.approval'].create({
                'order_id': rec.id, 'user_id': self.env.user.id,
                'decision': 'rejected',
                'amount_at_decision': rec.submitted_amount,
            })
        return True

    def action_withdraw(self):
        for rec in self:
            if rec.state in ('implemented', 'closed'):
                raise UserError(_(
                    "An implemented change cannot be withdrawn. Raise a "
                    "reversing change instead."))
            rec.state = 'withdrawn'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in ('implemented', 'closed'):
                raise UserError(_(
                    "An implemented change cannot be cancelled. Raise a "
                    "reversing change instead."))
            rec.state = 'cancelled'
        return True

    def action_close(self):
        for rec in self:
            rec._require_state(('implemented',), _('closed'))
            rec.state = 'closed'
        return True

    def _require_state(self, states, what):
        self.ensure_one()
        if self.state not in states:
            raise UserError(_(
                "%(name)s cannot be %(what)s from %(state)s.",
                name=self.name, what=what, state=self.state))

    def _snapshot_revision(self):
        """Keep what was submitted, every time it is submitted.

        A contractor's second quotation does not erase the first: the pair is
        the negotiation, and the negotiation is what people argue about later.
        """
        self.ensure_one()
        self.env['realestate.construction.change.order.revision'].create({
            'order_id': self.id,
            'revision': self.revision,
            'submitted_amount': self.submitted_amount or self.proposed_amount,
            'submitted_days': self.submitted_days,
            'quotation_reference': self.quotation_reference,
            'notes': _("Submitted %s") % fields.Date.context_today(self),
        })
        self.revision += 1

    # ------------------------------------------------------------------
    # Implementation — the only place baselines move
    # ------------------------------------------------------------------
    def action_implement(self):
        """Apply the approved impact to budget, commitment and revenue.

        **Idempotent.** The guard is not a flag somebody might forget to set:
        implementation creates linked records, and their existence is what
        stops a second run. Clicking twice, or two users clicking at once,
        produces exactly one set of impacts — the advisory lock serialises the
        second caller and the link check then finds the work already done.

        All effects happen in the caller's transaction. If any part fails the
        whole thing unwinds, because a half-applied change — budget moved,
        commitment not — is worse than one that failed cleanly.
        """
        for rec in self:
            if rec.state in ('implemented', 'closed'):
                # Already applied. Skipping is the friendlier half of being
                # idempotent: a second click is a mistake, not an emergency.
                continue
            rec._require_state(('approved',), _('implemented'))
            rec._lock()

            if rec._already_implemented():
                # The lock let a competing caller finish first. Its records
                # exist, so there is nothing left to do.
                rec.message_post(body=_(
                    "Implementation skipped: this change had already been "
                    "applied."))
                continue

            rec._validate_for_implementation()
            rec._apply_budget_impact()
            rec._apply_commitment_impact()
            rec._apply_revenue_impact()
            rec._convert_forecast_anticipations()

            rec.write({
                'state': 'implemented',
                'implementation_date': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                "Implemented. Budget %(budget)s, commitment %(commitment)s, "
                "revenue %(revenue)s.",
                budget=rec.currency_id.format(rec.approved_budget_amount),
                commitment=rec.currency_id.format(rec.approved_cost_amount),
                revenue=rec.currency_id.format(rec.approved_revenue_amount)))
        return True

    def _lock(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (hash('re.construction.change') % 2147483647, self.id))

    def _already_implemented(self):
        """Whether implementation has already produced its records.

        Seam, and the idempotency guard. What makes implementation
        idempotent is the existence of the budget and commitment change
        records, and those are `real_estate_construction` models, so only that
        module can see them. Below it nothing is ever produced, so nothing has
        to be detected, and answering False is the honest reading rather than a
        permissive one.
        """
        self.ensure_one()
        return False

    def _apply_budget_impact(self):
        """Turn approved budget lines into budget change records.

        Seam. Budgets and budget change lines are `real_estate_construction`
        models. Below that module an approved order changes no budget, because
        there is no budget to change; that module supplies the real behaviour,
        including the refusal when a project has no baseline.
        """
        self.ensure_one()
        return True

    def _apply_commitment_impact(self):
        """Turn approved commitment lines into commitment change records.

        Seam. Commitment changes are a `real_estate_construction` model, and
        they sit beside the original commitment rather than inside it.
        """
        self.ensure_one()
        return True

    def _apply_revenue_impact(self):
        """Record the owner-side value of an approved order.

        Seam. Revenue changes are a `real_estate_construction` model. Cost and
        revenue changes are not the same money, which is why they are separate
        records rather than a sign on one.
        """
        self.ensure_one()
        return True

    def _convert_forecast_anticipations(self):
        """Mark anticipations that this order has made real.

        Seam. Forecast adjustments are a `real_estate_construction` model.
        Only draft forecasts are ever touched, and that rule stays with them:
        an approved forecast is history, and rewriting it would destroy the
        record of what was believed at the time.
        """
        self.ensure_one()
        return True

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate_for_approval(self):
        self.ensure_one()
        problems = []
        for line in self.line_ids:
            if not line.cost_code_id:
                problems.append(_("A line has no cost code."))
            if line.impact_side == 'revenue' and not self.partner_id \
                    and self.order_type == 'owner_variation':
                problems.append(_(
                    "An owner variation needs the customer it is billed to."))
        if self.order_type == 'budget_transfer':
            net = sum(self.line_ids.mapped('approved_amount')) or \
                sum(self.line_ids.mapped('negotiated_amount')) or \
                sum(self.line_ids.mapped('submitted_amount'))
            if self.currency_id.compare_amounts(net, 0.0) != 0:
                problems.append(_(
                    "A budget transfer must net to zero — this one moves "
                    "%(net)s, which is an increase wearing a transfer's name.",
                    net=self.currency_id.format(net)))
        if problems:
            raise UserError(_(
                "This change cannot be approved yet:\n\n%s",
                '\n'.join('• %s' % problem for problem in problems)))
        return True

    def _validate_for_implementation(self):
        """§49/§50 — an omission cannot reduce a contract below what is spent."""
        self.ensure_one()
        reductions = self.line_ids.filtered(
            lambda l: l.impact_side == 'commitment' and l.approved_amount < 0)
        if not reductions or not self.package_id:
            return True
        package = self.package_id
        reduction = abs(sum(reductions.mapped('approved_amount')))
        resulting = package.current_contract_value - reduction
        consumed = package._consumed_value()
        if self.currency_id.compare_amounts(resulting, consumed) < 0:
            raise UserError(_(
                "This omission would reduce %(package)s to %(resulting)s, "
                "below the %(consumed)s already certified or invoiced against "
                "it. A contract cannot be worth less than what has been paid "
                "for under it — resolve the certified value first.",
                package=package.display_name,
                resulting=self.currency_id.format(resulting),
                consumed=self.currency_id.format(consumed)))
        return True

    def _check_authority(self):
        """Refuse an approval the approver has no authority for.

        Seam. The authority matrix is a `real_estate_construction` model.
        """
        self.ensure_one()
        return True

    @api.constrains('project_id', 'company_id', 'package_id')
    def _check_company_consistency(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Change %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))
            if rec.package_id and rec.package_id.project_id != rec.project_id:
                raise ValidationError(_(
                    "%(package)s belongs to another project.",
                    package=rec.package_id.display_name))

    def write(self, vals):
        """Implemented impact is history. §40."""
        protected = {'line_ids', 'order_type', 'project_id', 'package_id',
                     'currency_id', 'company_id'}
        if protected & set(vals):
            frozen = self.filtered(
                lambda o: o.state in ('implemented', 'closed'))
            if frozen:
                raise UserError(_(
                    "%(names)s have been implemented. Raise a superseding or "
                    "reversing change rather than editing history.",
                    names=', '.join(frozen.mapped('name'))))
        return super().write(vals)


class ConstructionChangeOrderLine(models.Model):
    """One cost code's share of a change, at every stage it passed through."""
    _name = 'realestate.construction.change.order.line'
    _description = 'Change Order Impact Line'
    _order = 'order_id, id'
    _check_company_auto = True

    order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='cascade', index=True)
    project_id = fields.Many2one(
        related='order_id.project_id', store=True, index=True, readonly=True)
    company_id = fields.Many2one(
        related='order_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='order_id.currency_id', store=True, readonly=True)
    state = fields.Selection(
        related='order_id.state', store=True, readonly=True, index=True)

    impact_side = fields.Selection(
        IMPACT_SIDES, required=True, default='commitment', index=True,
        help="Which side of the project this line moves. A cost increase does "
             "not automatically increase the authorised budget.")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', required=True, index=True,
        ondelete='restrict', check_company=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='restrict',
        check_company=True)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True)
    description = fields.Char()

    # Every stage kept, none overwriting another.
    estimated_amount = fields.Monetary()
    proposed_amount = fields.Monetary()
    submitted_amount = fields.Monetary()
    assessed_amount = fields.Monetary()
    negotiated_amount = fields.Monetary()
    approved_amount = fields.Monetary(
        help="What authority granted. Written at approval from the "
             "negotiated (or submitted, or estimated) figure unless somebody "
             "set it deliberately.")

    schedule_days = fields.Integer()
    notes = fields.Char()

    def _default_approved_amounts(self):
        """At approval, the approved figure defaults to the latest stage.

        Explicitly rather than by compute: once approved it is a decision, and
        a decision should not silently change because somebody edited the
        negotiation afterwards.
        """
        for line in self:
            if line.approved_amount:
                continue
            line.approved_amount = (
                line.negotiated_amount or line.assessed_amount
                or line.submitted_amount or line.proposed_amount
                or line.estimated_amount)

    def _budget_change_type(self):
        self.ensure_one()
        if self.impact_side == 'contingency':
            return 'contingency_drawdown'
        if self.order_id.order_type == 'budget_transfer':
            return 'transfer_in' if self.approved_amount > 0 else 'transfer_out'
        return 'increase' if self.approved_amount >= 0 else 'decrease'

    @api.constrains('cost_code_id', 'project_id')
    def _check_cost_code_project(self):
        for line in self:
            code = line.cost_code_id
            if code.project_id and code.project_id != line.project_id:
                raise ValidationError(_(
                    "Cost code %(code)s is scoped to another project.",
                    code=code.display_name))

    def write(self, vals):
        frozen = self.filtered(
            lambda l: l.order_id.state in ('implemented', 'closed'))
        if frozen and set(vals) - {'notes', 'description'}:
            raise UserError(_(
                "Implemented impact lines are immutable. Raise a correcting "
                "change."))
        return super().write(vals)


class ConstructionChangeOrderMarkup(models.Model):
    """Overhead, profit, bond — priced beside the scope, never inside it."""
    _name = 'realestate.construction.change.order.markup'
    _description = 'Change Order Markup'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    currency_id = fields.Many2one(
        related='order_id.currency_id', readonly=True)
    markup_type = fields.Selection(MARKUP_TYPES, required=True,
                                   default='overhead')
    description = fields.Char()
    computation = fields.Selection([
        ('fixed', 'Fixed Amount'),
        ('percentage', 'Percentage of Scope'),
    ], default='percentage', required=True)
    percentage = fields.Float()
    amount = fields.Monetary(compute='_compute_amount', store=True,
                             readonly=False)

    @api.depends('computation', 'percentage',
                 'order_id.line_ids.negotiated_amount',
                 'order_id.line_ids.submitted_amount',
                 'order_id.line_ids.estimated_amount')
    def _compute_amount(self):
        """The base is read from the impact lines, not from the order's own
        stored totals: depending on one computed field from another leaves the
        result at the mercy of which recomputes first, and a markup that
        silently comes out zero is worse than one that is obviously wrong.
        """
        for rec in self:
            if rec.computation != 'percentage':
                rec.amount = rec.amount or 0.0
                continue
            lines = rec.order_id.line_ids
            base = (sum(lines.mapped('negotiated_amount'))
                    or sum(lines.mapped('submitted_amount'))
                    or sum(lines.mapped('estimated_amount')))
            rec.amount = base * (rec.percentage or 0.0) / 100.0



class ConstructionChangeOrderRevision(models.Model):
    """What was submitted, each time it was submitted."""
    _name = 'realestate.construction.change.order.revision'
    _description = 'Change Order Submission History'
    _order = 'order_id, revision desc, id desc'

    order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='cascade', index=True)
    currency_id = fields.Many2one(
        related='order_id.currency_id', readonly=True)
    revision = fields.Integer(readonly=True)
    submitted_amount = fields.Monetary(readonly=True)
    submitted_days = fields.Integer(readonly=True)
    quotation_reference = fields.Char(readonly=True)
    submitted_on = fields.Datetime(
        default=fields.Datetime.now, readonly=True)
    submitted_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    notes = fields.Char(readonly=True)


class ConstructionChangeApproval(models.Model):
    """Who decided what, when, and on how much."""
    _name = 'realestate.construction.change.approval'
    _description = 'Change Approval'
    _order = 'order_id, id'

    order_id = fields.Many2one(
        'realestate.construction.change.order', required=True,
        ondelete='cascade', index=True)
    currency_id = fields.Many2one(
        related='order_id.currency_id', readonly=True)
    user_id = fields.Many2one('res.users', required=True, readonly=True)
    decision = fields.Selection([
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('returned', 'Returned for Revision'),
    ], required=True, readonly=True)
    decided_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    amount_at_decision = fields.Monetary(readonly=True)
    comment = fields.Char()
