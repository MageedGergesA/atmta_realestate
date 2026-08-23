# -*- coding: utf-8 -*-
"""The procurement plan — what a project expects to buy, and when.

A plan is **demand**, not money. It does not reserve budget, does not commit
anything and does not appear in any Construction control figure. Its job is to
answer questions a requisition is far too late to answer: what has to be
ordered this quarter, what has a nine-month lead time and therefore needed
sourcing to start last month, and which packages nobody has begun.

Its financial neutrality is not a convention — it is asserted by
`test_a_plan_is_demand_and_never_money`, because a planning document that
quietly moved a budget figure would be the most dangerous kind of forecast.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PROCUREMENT_TYPES = [
    ('material', 'Material'),
    ('service', 'Service'),
    ('subcontract', 'Subcontract'),
    ('equipment', 'Equipment'),
    ('other', 'Other'),
]

PROCUREMENT_STRATEGIES = [
    ('competitive_tender', 'Competitive Tender'),
    ('limited_tender', 'Limited Tender'),
    ('rfq', 'Request for Quotation'),
    ('framework', 'Framework / Blanket'),
    ('single_source', 'Single Source'),
    ('direct', 'Direct Purchase'),
    ('undecided', 'Not Yet Decided'),
]


class ProcurementPlan(models.Model):
    _name = 'realestate.procurement.plan'
    _description = 'Procurement Plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, date_from desc, revision desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)

    date_from = fields.Date(string='Period From', tracking=True)
    date_to = fields.Date(string='Period To', tracking=True)

    revision = fields.Integer(default=0, readonly=True, copy=False,
                              tracking=True)
    supersedes_id = fields.Many2one(
        'realestate.procurement.plan', readonly=True, copy=False)
    superseded_by_id = fields.Many2one(
        'realestate.procurement.plan', readonly=True, copy=False)
    is_current = fields.Boolean(
        compute='_compute_is_current', store=True, index=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('approved', 'Approved'),
        ('active', 'Active'),
        ('superseded', 'Superseded'),
        ('closed', 'Closed'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    prepared_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    reviewed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)

    line_ids = fields.One2many(
        'realestate.procurement.plan.line', 'plan_id', string='Planned Items',
        copy=True)
    line_count = fields.Integer(compute='_compute_totals', store=True)
    long_lead_count = fields.Integer(compute='_compute_totals', store=True)
    estimated_total = fields.Monetary(
        compute='_compute_totals', store=True,
        help="Tax-exclusive planning estimate. Comparable with Construction "
             "control figures, and authoritative for none of them.")
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    notes = fields.Html()

    _sql_constraints = [
        ('revision_uniq', 'unique(project_id, name, revision)',
         'That revision of this plan already exists.'),
    ]

    # ------------------------------------------------------------------
    @api.depends('state')
    def _compute_is_current(self):
        for rec in self:
            rec.is_current = rec.state in ('approved', 'active')

    @api.depends('line_ids.estimated_amount', 'line_ids.is_long_lead')
    def _compute_totals(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.long_lead_count = len(rec.line_ids.filtered('is_long_lead'))
            rec.estimated_total = sum(rec.line_ids.mapped('estimated_amount'))

    @api.constrains('date_from', 'date_to')
    def _check_period(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_("The period ends before it starts."))

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Plan %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.plan') or 'PLAN/NEW'
        return super().create(vals_list)

    def write(self, vals):
        """An approved plan is what the project agreed to procure.

        It is revised, not edited: the dates and quantities in Rev 0 are what
        the schedule was built from, and rewriting them destroys the only
        record of what was expected when.
        """
        protected = {'line_ids', 'date_from', 'date_to', 'project_id'}
        if protected & set(vals) and not self.env.context.get('re_plan_revision'):
            frozen = self.filtered(
                lambda p: p.state in ('approved', 'active', 'superseded'))
            if frozen:
                raise UserError(_(
                    "%(refs)s are approved. Create a revision rather than "
                    "rewriting what the project planned to buy.",
                    refs=', '.join(frozen.mapped('name'))))
        return super().write(vals)

    # ------------------------------------------------------------------
    def action_review(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is not a draft plan.") % rec.name)
            if not rec.line_ids:
                raise UserError(_("An empty plan plans nothing."))
            rec.write({'state': 'review',
                       'reviewed_by_id': self.env.user.id})
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != 'review':
                raise UserError(_("%s is not under review.") % rec.name)
            rec.write({'state': 'approved',
                       'approved_by_id': self.env.user.id,
                       'approved_on': fields.Datetime.now()})
        return True

    def action_activate(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_("Approve %s first.") % rec.name)
            rec.state = 'active'
        return True

    def action_create_revision(self):
        """Copy the plan forward, leaving the approved one intact."""
        self.ensure_one()
        if self.state not in ('approved', 'active'):
            raise UserError(_(
                "A draft plan is edited directly. Revisions are for what was "
                "already agreed."))
        revision = self.with_context(re_plan_revision=True).copy({
            'name': self.name,
            'revision': self.revision + 1,
            'state': 'draft',
            'supersedes_id': self.id,
            'approved_by_id': False,
            'approved_on': False,
            'reviewed_by_id': False,
        })
        self.with_context(re_plan_revision=True).write({
            'state': 'superseded', 'superseded_by_id': revision.id})
        return revision

    def action_close(self):
        for rec in self:
            rec.state = 'closed'
        return True


class ProcurementPlanLine(models.Model):
    _name = 'realestate.procurement.plan.line'
    _description = 'Procurement Plan Line'
    _order = 'plan_id, required_on_site_date, sequence, id'
    _check_company_auto = True

    plan_id = fields.Many2one(
        'realestate.procurement.plan', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='plan_id.company_id', store=True, readonly=True, index=True)
    project_id = fields.Many2one(
        related='plan_id.project_id', store=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        related='plan_id.currency_id', readonly=True)
    sequence = fields.Integer(default=10)

    # WBS and cost code are added by `real_estate_construction`, which owns
    # both models and depends on this one.

    procurement_type = fields.Selection(
        PROCUREMENT_TYPES, required=True, default='material', index=True)
    product_id = fields.Many2one(
        'product.product', string='Product',
        help="Optional. A subcontract package or a conceptual line has no "
             "catalogue entry, and forcing one would invent a product.")
    category_id = fields.Many2one('product.category', string='Category')
    description = fields.Char(required=True)

    quantity = fields.Float(default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UoM')
    estimated_unit_cost = fields.Monetary()
    estimated_amount = fields.Monetary(
        compute='_compute_amount', store=True, readonly=False,
        help="Tax-exclusive planning estimate.")

    # -- Dates. Four different questions, four different fields.
    required_on_site_date = fields.Date(
        index=True, help="When the works need it in place.")
    lead_time_days = fields.Integer(
        help="Total procurement-to-site lead time, where it is known. Left "
             "empty when it is not — see `has_lead_time`.")
    has_lead_time = fields.Boolean(compute='_compute_dates', store=True)
    target_award_date = fields.Date(
        compute='_compute_dates', store=True, readonly=False,
        help="Required on site minus the lead time. Empty when the lead time "
             "is unknown: an unknown lead time is not a lead time of zero, "
             "and defaulting it would put every long-lead item comfortably "
             "on schedule.")
    target_rfq_date = fields.Date(
        compute='_compute_dates', store=True, readonly=False)
    rfq_lead_days = fields.Integer(
        default=21,
        help="Days allowed between issuing the enquiry and awarding it.")

    is_long_lead = fields.Boolean(
        compute='_compute_long_lead', store=True, readonly=False,
        help="Set explicitly, or derived when the lead time exceeds the "
             "configured threshold. No number is hard-coded here — the "
             "threshold is company configuration.")
    strategy = fields.Selection(
        PROCUREMENT_STRATEGIES, default='undecided',
        string='Procurement Strategy')
    buyer_id = fields.Many2one('res.users', string='Responsible Buyer')

    state = fields.Selection([
        ('planned', 'Planned'),
        ('requested', 'Requisition Raised'),
        ('sourcing', 'Sourcing'),
        ('ordered', 'Ordered'),
        ('cancelled', 'Cancelled'),
    ], default='planned', required=True, index=True)
    request_ids = fields.One2many(
        'realestate.material.request', 'plan_line_id', readonly=True)
    request_count = fields.Integer(compute='_compute_requests')
    notes = fields.Char()

    # ------------------------------------------------------------------
    @api.depends('quantity', 'estimated_unit_cost')
    def _compute_amount(self):
        for line in self:
            if line.quantity and line.estimated_unit_cost:
                line.estimated_amount = line.quantity * line.estimated_unit_cost
            else:
                line.estimated_amount = line.estimated_amount or 0.0

    @api.depends('required_on_site_date', 'lead_time_days', 'rfq_lead_days')
    def _compute_dates(self):
        for line in self:
            line.has_lead_time = bool(line.lead_time_days)
            if not (line.required_on_site_date and line.lead_time_days):
                # Unknown, and left unknown on purpose.
                line.target_award_date = False
                line.target_rfq_date = False
                continue
            award = line.required_on_site_date - relativedelta(
                days=int(line.lead_time_days))
            line.target_award_date = award
            line.target_rfq_date = award - relativedelta(
                days=int(line.rfq_lead_days or 0))

    @api.depends('lead_time_days')
    def _compute_long_lead(self):
        threshold = self._long_lead_threshold()
        for line in self:
            if line.lead_time_days and threshold:
                line.is_long_lead = line.lead_time_days >= threshold
            else:
                line.is_long_lead = line.is_long_lead or False

    @api.model
    def _long_lead_threshold(self):
        """Days beyond which an item counts as long lead. Configuration."""
        value = self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_procurement.long_lead_threshold_days', '90')
        try:
            return int(value)
        except (TypeError, ValueError):
            return 90

    def _compute_requests(self):
        for line in self:
            line.request_count = len(line.request_ids)

    def action_create_requisition(self):
        """Turn one planned item into a requisition. Still no money moves."""
        self.ensure_one()
        request = self.env['realestate.material.request'].create({
            'company_id': self.company_id.id,
            'project_id': self.project_id.id,
            'procurement_type': self.procurement_type,
            'source_type': 'procurement_plan',
            'plan_line_id': self.id,
            'buyer_id': self.buyer_id.id or False,
            'needed_by': self.required_on_site_date,
            'line_ids': [(0, 0, self._prepare_requisition_line_values())],
        })
        self.state = 'requested'
        return request

    def _prepare_requisition_line_values(self):
        """Values for the requisition line. Construction adds the coding."""
        self.ensure_one()
        return {
                'product_id': self.product_id.id or False,
                'description': self.description,
                'qty': self.quantity or 1.0,
                'uom_id': (self.uom_id.id or
                           (self.product_id.uom_id.id
                            if self.product_id else False)),
                'estimated_unit_cost': self.estimated_unit_cost,
            'required_on_site_date': self.required_on_site_date,
        }
