# -*- coding: utf-8 -*-
"""M4 — the Change Event: something happened that *may* affect the project.

A change event is not a budget change, not a variation, and not a commitment.
It is a record that something occurred — a design revision, an instruction, an
unforeseen condition — together with somebody's estimate of what it might cost.

```
    EVENT (potential)  →  assessed  →  CHANGE ORDER (commercial)
                                          → approved → implemented → baselines
```

The distinction is the whole point of the model. An estimate that could move a
budget would make every site engineer's guess an authorisation, and the first
question anybody asks about a project — "what has actually been approved?" —
would stop having an answer.

So: an event carries **estimated** impacts, and the equations of M2 and M3 do
not read them. What reads them is exposure reporting, which is labelled
potential everywhere it appears.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

EVENT_SOURCES = [
    ('design_change', 'Design Change'),
    ('owner_request', 'Owner Request'),
    ('consultant_instruction', 'Consultant Instruction'),
    ('site_instruction', 'Site Instruction'),
    ('rfi', 'RFI'),
    ('unforeseen_condition', 'Unforeseen Condition'),
    ('authority_requirement', 'Authority Requirement'),
    ('contractor_notice', 'Contractor Notice'),
    ('quantity_change', 'Quantity Change'),
    ('specification_change', 'Specification Change'),
    ('procurement_change', 'Procurement Change'),
    ('delay', 'Delay'),
    ('ncr_corrective_work', 'NCR Corrective Work'),
    # M8 integration. A change event may originate in an issue that a risk
    # became, or in a determined claim. Additive: nothing about M4's own
    # behaviour changes with them.
    ('issue', 'Project Issue'),
    ('claim_determination', 'Claim Determination'),
    ('value_engineering', 'Value Engineering'),
    ('omission', 'Omission'),
    ('addition', 'Addition'),
    ('internal_management', 'Internal / Management'),
    ('other', 'Other'),
]

EVENT_STATES = [
    ('draft', 'Draft'),
    ('identified', 'Identified'),
    ('under_review', 'Under Review'),
    ('pricing', 'Pricing'),
    ('assessed', 'Assessed'),
    ('change_required', 'Change Required'),
    ('no_change', 'No Change'),
    ('rejected', 'Rejected'),
    ('duplicate', 'Duplicate'),
    ('cancelled', 'Cancelled'),
    ('closed', 'Closed'),
]

#: States in which an event's estimate is still live exposure — not yet
#: approved, not yet dismissed.
POTENTIAL_STATES = ('draft', 'identified', 'under_review', 'pricing',
                    'assessed', 'change_required')


class ConstructionChangeEvent(models.Model):
    _name = 'realestate.construction.change.event'
    _description = 'Construction Change Event'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, discovered_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Html()

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', string='Package',
        ondelete='set null', check_company=True,
        domain="[('project_id', '=', project_id)]")
    contractor_id = fields.Many2one(
        'realestate.contractor', ondelete='set null')
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        ondelete='set null', check_company=True)
    location = fields.Char()

    source = fields.Selection(
        EVENT_SOURCES, required=True, default='other', tracking=True,
        index=True)
    source_reference = fields.Char(
        string='Source Document', index='trigram',
        help="The instruction, RFI or notice this came from. Used to warn "
             "about duplicates rather than to merge anything automatically.")
    # M5 will own RFIs; the hook exists now so the link is never invented later.
    source_model = fields.Char(readonly=True)
    source_id = fields.Integer(readonly=True)

    discovered_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True,
        index=True)
    event_date = fields.Date(
        help="When the underlying event happened, which may be earlier than "
             "the day somebody noticed it.")
    raised_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    responsible_id = fields.Many2one('res.users', tracking=True)
    responsible_party = fields.Selection([
        ('owner', 'Owner'),
        ('consultant', 'Consultant'),
        ('contractor', 'Contractor'),
        ('authority', 'Authority'),
        ('shared', 'Shared / Undetermined'),
        ('unknown', 'Not Yet Determined'),
    ], default='unknown', tracking=True)
    priority = fields.Selection([
        ('0', 'Low'), ('1', 'Normal'), ('2', 'High'), ('3', 'Urgent'),
    ], default='1')
    probability = fields.Float(
        string='Probability (%)', default=100.0,
        help="How likely this becomes a real change. Reporting only — an "
             "estimate is never weighted into a control figure.")

    estimated_cost_impact = fields.Monetary(tracking=True)
    estimated_revenue_impact = fields.Monetary(tracking=True)
    estimated_schedule_days = fields.Integer(tracking=True)
    estimated_contingency_impact = fields.Monetary()

    state = fields.Selection(
        EVENT_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    assessment_notes = fields.Html()
    closed_reason = fields.Char()

    change_order_ids = fields.One2many(
        'realestate.construction.change.order', 'change_event_id',
        string='Change Orders')
    change_order_count = fields.Integer(compute='_compute_orders')
    approved_cost_change = fields.Monetary(compute='_compute_orders')
    approved_revenue_change = fields.Monetary(compute='_compute_orders')

    age_days = fields.Integer(compute='_compute_age')
    is_potential = fields.Boolean(compute='_compute_age', store=True)

    _sql_constraints = [
        ('probability_range', 'CHECK(probability >= 0 AND probability <= 100)',
         'Probability is a percentage between 0 and 100.'),
    ]

    @api.depends('change_order_ids.state',
                 'change_order_ids.approved_cost_amount',
                 'change_order_ids.approved_revenue_amount')
    def _compute_orders(self):
        for rec in self:
            orders = rec.change_order_ids
            rec.change_order_count = len(orders)
            live = orders.filtered(
                lambda o: o.state in ('approved', 'implemented', 'closed'))
            rec.approved_cost_change = sum(
                live.mapped('approved_cost_amount'))
            rec.approved_revenue_change = sum(
                live.mapped('approved_revenue_amount'))

    @api.depends('state')
    def _compute_age(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_potential = rec.state in POTENTIAL_STATES
            rec.age_days = (
                (today - rec.discovered_date).days
                if rec.discovered_date else 0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.change.event') or 'CE/NEW'
        events = super().create(vals_list)
        events._warn_possible_duplicates()
        return events

    def _warn_possible_duplicates(self):
        """Warn, never merge.

        Two people recording the same site instruction is common and worth
        flagging. Merging them on a text similarity is not: an event that
        quietly absorbed another would lose somebody's estimate without
        anybody deciding to.
        """
        for rec in self:
            if not rec.source_reference:
                continue
            twins = self.search([
                ('id', '!=', rec.id),
                ('project_id', '=', rec.project_id.id),
                ('source_reference', '=', rec.source_reference),
                ('state', 'not in', ('cancelled', 'rejected', 'duplicate')),
            ], limit=5)
            if twins:
                rec.message_post(body=_(
                    "Possible duplicate of %(refs)s — same project and source "
                    "document. Nothing has been merged; somebody should "
                    "decide.", refs=', '.join(twins.mapped('name'))))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_identify(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft event can be identified."))
            rec.state = 'identified'
        return True

    def action_review(self):
        for rec in self:
            rec._require_state(('identified',), _('reviewed'))
            rec.state = 'under_review'
        return True

    def action_price(self):
        for rec in self:
            rec._require_state(('identified', 'under_review'), _('priced'))
            rec.state = 'pricing'
        return True

    def action_assess(self):
        for rec in self:
            rec._require_state(('under_review', 'pricing'), _('assessed'))
            rec.state = 'assessed'
        return True

    def action_change_required(self):
        for rec in self:
            rec._require_state(('assessed', 'pricing', 'under_review'),
                               _('marked as requiring a change'))
            rec.state = 'change_required'
        return True

    def action_no_change(self):
        for rec in self:
            if rec.change_order_ids.filtered(
                    lambda o: o.state in ('approved', 'implemented')):
                raise UserError(_(
                    "This event already has an approved change order. It "
                    "cannot be closed as 'no change'."))
            rec.state = 'no_change'
        return True

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'
        return True

    def action_mark_duplicate(self):
        for rec in self:
            rec.state = 'duplicate'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.change_order_ids.filtered(
                    lambda o: o.state in ('approved', 'implemented')):
                raise UserError(_(
                    "An event with an implemented change order cannot be "
                    "cancelled."))
            rec.state = 'cancelled'
        return True

    def action_close(self):
        for rec in self:
            rec.state = 'closed'
        return True

    def _require_state(self, states, what):
        self.ensure_one()
        if self.state not in states:
            raise UserError(_(
                "%(name)s cannot be %(what)s from %(state)s.",
                name=self.name, what=what, state=self.state))

    def action_create_change_order(self):
        """Turn an assessed event into a commercial change, ready to price."""
        self.ensure_one()
        if self.state in ('no_change', 'rejected', 'cancelled', 'duplicate'):
            raise UserError(_(
                "This event was closed without a change."))
        order = self.env['realestate.construction.change.order'].create({
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'change_event_id': self.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'title': self.title,
            'reason': self.description,
            'order_type': ('contractor_variation' if self.contractor_id
                           else 'budget_change'),
        })
        if self.cost_code_id and self.estimated_cost_impact:
            self.env['realestate.construction.change.order.line'].create({
                'order_id': order.id,
                'cost_code_id': self.cost_code_id.id,
                'wbs_id': self.wbs_id.id or False,
                'impact_side': ('commitment' if self.contractor_id
                                else 'budget'),
                'estimated_amount': self.estimated_cost_impact,
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.construction.change.order',
            'res_id': order.id,
            'view_mode': 'form',
        }

    # ------------------------------------------------------------------
    @api.model
    def exposure(self, project):
        """Potential and approved, counted separately and never mixed.

        `_read_group` rather than reading every event: a long project has
        thousands, and §58 requires the number and the drilldown to agree.
        """
        potential = self._read_group(
            [('project_id', '=', project.id),
             ('state', 'in', POTENTIAL_STATES)],
            aggregates=['estimated_cost_impact:sum',
                        'estimated_revenue_impact:sum',
                        'estimated_schedule_days:sum', '__count'],
        )
        cost, revenue, days, count = (
            potential[0] if potential else (0.0, 0.0, 0, 0))

        Order = self.env['realestate.construction.change.order']
        approved = Order._read_group(
            [('project_id', '=', project.id),
             ('state', 'in', ('approved', 'implemented', 'closed'))],
            aggregates=['approved_cost_amount:sum',
                        'approved_revenue_amount:sum', '__count'],
        )
        approved_cost, approved_revenue, approved_count = (
            approved[0] if approved else (0.0, 0.0, 0))

        pending = Order.search_count([
            ('project_id', '=', project.id),
            ('state', 'in', ('submitted', 'under_review', 'negotiation',
                             'pending_approval')),
        ])
        return {
            'potential_cost': cost or 0.0,
            'potential_revenue': revenue or 0.0,
            'potential_schedule_days': days or 0,
            'potential_count': count or 0,
            'approved_cost': approved_cost or 0.0,
            'approved_revenue': approved_revenue or 0.0,
            'approved_count': approved_count or 0,
            'pending_approval_count': pending,
            'change_margin': (approved_revenue or 0.0) - (approved_cost or 0.0),
        }

    def action_view_change_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Change Orders'),
            'res_model': 'realestate.construction.change.order',
            'view_mode': 'list,form',
            'domain': [('change_event_id', '=', self.id)],
            'context': {'default_change_event_id': self.id,
                        'default_project_id': self.project_id.id},
        }

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Change event %(name)s is in %(company)s but its project "
                    "is in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))
