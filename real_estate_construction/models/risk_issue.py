# -*- coding: utf-8 -*-
"""Risks and issues — what might happen, and what has.

A risk is forward-looking uncertainty. An issue is a problem that is already
happening. When a risk comes true it does not *become* an issue and disappear:
the risk stays, marked materialised, with a snapshot of what it said at the
moment it came true. That snapshot is the only honest answer to "did we see
this coming?", and deleting the risk destroys it.

Neither a risk nor an issue moves money. Where a cost consequence follows, a
change event is created and M4 owns what happens next.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

RISK_CATEGORIES = [
    ('design', 'Design'),
    ('procurement', 'Procurement'),
    ('construction', 'Construction'),
    ('commercial', 'Commercial'),
    ('financial', 'Financial'),
    ('schedule', 'Schedule'),
    ('quality', 'Quality'),
    ('authority', 'Authority / Permitting'),
    ('stakeholder', 'Stakeholder'),
    ('interface', 'Interface'),
    ('market', 'Market'),
    ('currency', 'Currency'),
    ('supply_chain', 'Supply Chain'),
    ('environmental', 'Environmental'),
    ('other', 'Other'),
]

RESPONSE_STRATEGIES = [
    ('avoid', 'Avoid'),
    ('mitigate', 'Mitigate'),
    ('transfer', 'Transfer'),
    ('accept', 'Accept'),
    ('exploit', 'Exploit (Opportunity)'),
    ('other', 'Other'),
]

ISSUE_CATEGORIES = [
    ('design_coordination', 'Design Coordination'),
    ('authority', 'Authority Approval'),
    ('utility', 'Utility / Obstruction'),
    ('access', 'Access'),
    ('supplier', 'Supplier Failure'),
    ('commercial', 'Commercial Blocker'),
    ('schedule', 'Schedule Constraint'),
    ('interface', 'Site Interface'),
    ('resource', 'Resource'),
    ('quality', 'Quality (see NCR)'),
    ('other', 'Other'),
]


class ConstructionRisk(models.Model):
    _name = 'realestate.construction.risk'
    _description = 'Project Risk'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, inherent_score desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Text()
    cause = fields.Text(help="Why it might happen.")
    consequence = fields.Text(help="What it would do if it did.")

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', ondelete='set null', index=True)
    category = fields.Selection(RISK_CATEGORIES, required=True,
                                default='other', index=True, tracking=True)

    identified_on = fields.Date(default=fields.Date.context_today, index=True)
    identified_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user)
    owner_id = fields.Many2one('res.users', string='Risk Owner', tracking=True)

    # -- Assessment. Scales are configurable; the matrix is not this
    # -- module's opinion to hold.
    probability = fields.Integer(
        default=3, tracking=True,
        help="On the company's configured scale (1–5 by default).")
    cost_impact_score = fields.Integer(default=3, tracking=True)
    schedule_impact_score = fields.Integer(default=3, tracking=True)
    quality_impact_score = fields.Integer(default=1)
    overall_impact = fields.Integer(
        compute='_compute_scores', store=True,
        help="The worst of the impact dimensions, because a risk that is "
             "catastrophic for time and trivial for cost is a severe risk.")
    inherent_score = fields.Integer(
        compute='_compute_scores', store=True, index=True,
        help="probability × overall impact. A sortable number, not a "
             "quantification — multiplying ordinal scales is a convention, "
             "and `management_rating` exists for when it misleads.")
    management_rating = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
        ('critical', 'Critical'),
    ], tracking=True,
        help="An explicit human rating, which overrides the arithmetic on "
             "the register when set.")
    severity = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
        ('critical', 'Critical'),
    ], compute='_compute_scores', store=True, index=True)

    # -- Response ----------------------------------------------------------
    response_strategy = fields.Selection(RESPONSE_STRATEGIES, tracking=True)
    mitigation_plan = fields.Text()
    mitigation_owner_id = fields.Many2one('res.users')
    mitigation_due_date = fields.Date(index=True)
    action_ids = fields.One2many(
        'realestate.construction.risk.action', 'risk_id', string='Actions')
    open_action_count = fields.Integer(compute='_compute_actions')
    overdue_action_count = fields.Integer(compute='_compute_actions',
                                          store=True)
    has_mitigation = fields.Boolean(compute='_compute_actions', store=True)

    residual_probability = fields.Integer(tracking=True)
    residual_impact = fields.Integer(tracking=True)
    residual_score = fields.Integer(compute='_compute_scores', store=True)

    trigger = fields.Char(help="What would tell us it is happening.")
    review_date = fields.Date(index=True)

    # -- Cost exposure -----------------------------------------------------
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)
    cost_exposure_min = fields.Monetary()
    cost_exposure_likely = fields.Monetary(string='Cost Exposure')
    cost_exposure_max = fields.Monetary()
    schedule_exposure_days = fields.Float()
    include_in_forecast = fields.Boolean(
        tracking=True,
        help="A cost controller's explicit decision. Nothing adds a risk to "
             "the forecast on its own — a probability-weighted number that "
             "wanders into an approved EAC is a forecast nobody signed.")
    forecast_line_id = fields.Many2one(
        'realestate.construction.forecast.line', readonly=True, copy=False)

    state = fields.Selection([
        ('identified', 'Identified'),
        ('assessed', 'Assessed'),
        ('response_planned', 'Response Planned'),
        ('monitoring', 'Monitoring'),
        ('materialised', 'Materialised'),
        ('closed', 'Closed'),
    ], default='identified', required=True, tracking=True, copy=False,
        index=True)

    issue_id = fields.Many2one(
        'realestate.construction.issue', readonly=True, copy=False,
        help="The issue this risk became, when it came true.")
    materialised_on = fields.Date(readonly=True, copy=False)
    materialised_snapshot = fields.Text(
        readonly=True, copy=False,
        help="What the risk said at the moment it came true — the answer to "
             "'did we see this coming?'.")
    change_event_ids = fields.Many2many(
        'realestate.construction.change.event', 'risk_change_event_rel',
        'risk_id', 'change_event_id', string='Change Events')
    closure_reason = fields.Text()
    attachment_ids = fields.Many2many('ir.attachment',
                                      'risk_attachment_rel',
                                      'risk_id', 'attachment_id')

    # ------------------------------------------------------------------
    @api.depends('probability', 'cost_impact_score', 'schedule_impact_score',
                 'quality_impact_score', 'residual_probability',
                 'residual_impact', 'management_rating')
    def _compute_scores(self):
        for rec in self:
            rec.overall_impact = max(rec.cost_impact_score or 0,
                                     rec.schedule_impact_score or 0,
                                     rec.quality_impact_score or 0)
            rec.inherent_score = (rec.probability or 0) * rec.overall_impact
            rec.residual_score = ((rec.residual_probability or 0)
                                  * (rec.residual_impact or 0))
            if rec.management_rating:
                rec.severity = rec.management_rating
            elif rec.inherent_score >= 15:
                rec.severity = 'critical'
            elif rec.inherent_score >= 9:
                rec.severity = 'high'
            elif rec.inherent_score >= 4:
                rec.severity = 'medium'
            else:
                rec.severity = 'low'

    @api.depends('action_ids.state', 'action_ids.due_date',
                 'mitigation_plan')
    def _compute_actions(self):
        today = fields.Date.context_today(self)
        for rec in self:
            open_actions = rec.action_ids.filtered(
                lambda a: a.state not in ('done', 'cancelled'))
            rec.open_action_count = len(open_actions)
            rec.overdue_action_count = len(open_actions.filtered(
                lambda a: a.due_date and a.due_date < today))
            rec.has_mitigation = bool(rec.mitigation_plan or rec.action_ids)

    @api.constrains('probability', 'cost_impact_score',
                    'schedule_impact_score')
    def _check_scale(self):
        maximum = self._scale_maximum()
        for rec in self:
            for value, label in ((rec.probability, _('probability')),
                                 (rec.cost_impact_score, _('cost impact')),
                                 (rec.schedule_impact_score,
                                  _('schedule impact'))):
                if value and not 1 <= value <= maximum:
                    raise ValidationError(_(
                        "%(label)s must sit on the configured 1–%(max)s "
                        "scale.", label=label, max=maximum))

    @api.model
    def _scale_maximum(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.risk_scale_maximum', '5'))

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Risk %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.risk') or 'RSK/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_assess(self):
        for rec in self:
            rec.state = 'assessed'
        return True

    def action_plan_response(self):
        for rec in self:
            if not rec.response_strategy:
                raise UserError(_(
                    "Choose how %s is being responded to.") % rec.name)
            if rec.response_strategy in ('mitigate', 'avoid') and \
                    not rec.has_mitigation:
                raise UserError(_(
                    "A mitigation strategy with no plan and no actions is a "
                    "hope. Record what will actually be done."))
            rec.state = 'response_planned'
        return True

    def action_monitor(self):
        for rec in self:
            rec.state = 'monitoring'
        return True

    def action_close(self, reason=None):
        for rec in self:
            if rec.state == 'materialised':
                raise UserError(_(
                    "%s came true. Close the issue it became; the risk stays "
                    "as the record that it was foreseen.") % rec.name)
            rec.write({'state': 'closed',
                       'closure_reason': reason or rec.closure_reason})
        return True

    def action_materialise(self, reason=None, issue_values=None):
        """The risk came true. Keep it, and open an issue.

        Deliberately not a conversion: a converted risk cannot answer whether
        the project saw the problem coming, and that is the question a risk
        register exists to answer.
        """
        self.ensure_one()
        if self.issue_id:
            raise UserError(_(
                "%(name)s already materialised into %(issue)s.",
                name=self.name, issue=self.issue_id.name))
        snapshot = json.dumps({
            'state': self.state,
            'probability': self.probability,
            'overall_impact': self.overall_impact,
            'inherent_score': self.inherent_score,
            'severity': self.severity,
            'response_strategy': self.response_strategy,
            'mitigation_plan': self.mitigation_plan or '',
            'open_actions': self.open_action_count,
            'cost_exposure_likely': self.cost_exposure_likely,
            'schedule_exposure_days': self.schedule_exposure_days,
            'materialised_reason': reason or '',
        }, indent=2, sort_keys=True)
        values = {
            'title': self.title,
            'description': self.description,
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'category': 'other',
            'source_risk_id': self.id,
            'owner_id': self.owner_id.id or False,
            'currency_id': self.currency_id.id,
            'estimated_cost_impact': self.cost_exposure_likely,
        }
        values.update(issue_values or {})
        issue = self.env['realestate.construction.issue'].create(values)
        self.write({
            'state': 'materialised',
            'issue_id': issue.id,
            'materialised_on': fields.Date.context_today(self),
            'materialised_snapshot': snapshot,
        })
        self.message_post(body=_(
            "Materialised into %(issue)s. %(reason)s",
            issue=issue.name, reason=reason or ''))
        return issue

    def action_include_in_forecast(self):
        """A cost controller's explicit act, recorded as one."""
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_construction.group_construction_manager'):
            raise UserError(_(
                "Putting a risk into the forecast is a cost controller's "
                "decision."))
        self.include_in_forecast = True
        self.message_post(body=_(
            "%(user)s included this risk in forecast exposure at "
            "%(amount)s.", user=self.env.user.display_name,
            amount=self.currency_id.format(self.cost_exposure_likely)))
        return True


class ConstructionRiskAction(models.Model):
    _name = 'realestate.construction.risk.action'
    _description = 'Risk Mitigation Action'
    _order = 'risk_id, due_date, id'
    _check_company_auto = True

    risk_id = fields.Many2one(
        'realestate.construction.risk', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='risk_id.company_id', store=True, readonly=True, index=True)
    name = fields.Char(string='Action', required=True)
    owner_id = fields.Many2one('res.users')
    due_date = fields.Date(index=True)
    completed_on = fields.Date(readonly=True)
    state = fields.Selection([
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='open', required=True, index=True)
    effectiveness = fields.Selection([
        ('effective', 'Effective'),
        ('partial', 'Partially Effective'),
        ('ineffective', 'Ineffective'),
        ('unknown', 'Not Yet Known'),
    ], default='unknown',
        help="Recorded after the fact. A mitigation nobody reviewed is a "
             "mitigation nobody knows worked.")
    is_overdue = fields.Boolean(compute='_compute_overdue')
    notes = fields.Text()

    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                rec.due_date and rec.due_date < today
                and rec.state not in ('done', 'cancelled'))

    def action_done(self):
        for rec in self:
            rec.write({'state': 'done',
                       'completed_on': fields.Date.context_today(rec)})
        return True


class ConstructionIssue(models.Model):
    _name = 'realestate.construction.issue'
    _description = 'Project Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, priority desc, opened_on desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Text()

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    category = fields.Selection(ISSUE_CATEGORIES, required=True,
                                default='other', index=True, tracking=True)

    #: Quality non-conformance keeps its own specialised process. An issue may
    #: point at an NCR; it does not replace one.
    source_risk_id = fields.Many2one(
        'realestate.construction.risk', readonly=True, index=True)
    source_rfi_id = fields.Many2one('realestate.construction.rfi')
    source_ncr_id = fields.Many2one('realestate.construction.ncr')
    source_delay_event_id = fields.Many2one(
        'realestate.construction.delay.event')

    opened_on = fields.Date(default=fields.Date.context_today, index=True,
                            required=True)
    owner_id = fields.Many2one('res.users', tracking=True)
    responsible_party = fields.Char()
    priority = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
        ('critical', 'Critical'),
    ], default='medium', required=True, tracking=True, index=True)
    impact = fields.Text()
    due_date = fields.Date(index=True, tracking=True)
    is_overdue = fields.Boolean(compute='_compute_overdue', store=True,
                                index=True)
    age_days = fields.Integer(compute='_compute_overdue')
    escalation_level = fields.Selection([
        ('none', 'None'),
        ('project_manager', 'Project Manager'),
        ('construction_manager', 'Construction Manager'),
        ('executive', 'Executive'),
    ], default='none', tracking=True)
    escalated_on = fields.Date(readonly=True, copy=False)

    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)
    estimated_cost_impact = fields.Monetary(
        help="What it might cost. Not a cost, not a commitment, and not "
             "authorisation for either.")

    state = fields.Selection([
        ('open', 'Open'),
        ('actioning', 'Actioning'),
        ('blocked', 'Blocked'),
        ('ready_to_close', 'Ready to Close'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='open', required=True, tracking=True, copy=False, index=True)
    resolution = fields.Text()
    resolved_on = fields.Date(readonly=True, copy=False)

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False)
    claim_id = fields.Many2one('realestate.construction.claim')
    delay_event_id = fields.Many2one(
        'realestate.construction.delay.event', readonly=True, copy=False)
    attachment_ids = fields.Many2many('ir.attachment',
                                      'issue_attachment_rel',
                                      'issue_id', 'attachment_id')

    @api.depends('due_date', 'state', 'opened_on')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                rec.due_date and rec.due_date < today
                and rec.state not in ('closed', 'cancelled'))
            rec.age_days = (today - rec.opened_on).days if rec.opened_on else 0

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Issue %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.issue') or 'ISS/NEW'
        return super().create(vals_list)

    def action_start(self):
        for rec in self:
            rec.state = 'actioning'
        return True

    def action_block(self, reason=None):
        for rec in self:
            rec.write({'state': 'blocked',
                       'resolution': reason or rec.resolution})
        return True

    def action_ready_to_close(self):
        for rec in self:
            if not rec.resolution:
                raise UserError(_(
                    "Record what resolved %s.") % rec.name)
            rec.state = 'ready_to_close'
        return True

    def action_close(self):
        """An issue closes when it is resolved, not when its date passes."""
        for rec in self:
            if rec.state in ('closed', 'cancelled'):
                continue
            if not rec.resolution:
                raise UserError(_(
                    "%s has no recorded resolution. A due date passing is "
                    "not a resolution.") % rec.name)
            rec.write({'state': 'closed',
                       'resolved_on': fields.Date.context_today(rec)})
        return True

    def action_escalate(self, level='project_manager'):
        for rec in self:
            rec.write({'escalation_level': level,
                       'escalated_on': fields.Date.context_today(rec)})
            rec.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Escalated issue %s") % rec.name,
                user_id=rec.owner_id.id or self.env.user.id)
        return True

    def action_create_change_event(self):
        """Hand the commercial consequence to M4. Nothing is authorised here."""
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_(
                "%s already has a change event.") % self.name)
        event = self.env['realestate.construction.change.event'].create({
            'title': _("Issue %(ref)s — %(title)s",
                       ref=self.name, title=self.title),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'package_id': self.package_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'source': 'issue',
            'source_reference': self.name,
            'source_model': self._name,
            'source_id': self.id,
            'estimated_cost_impact': self.estimated_cost_impact,
            'description': self.description,
        })
        self.change_event_id = event
        if self.source_risk_id:
            self.source_risk_id.change_event_ids |= event
        return event

    def action_create_delay_event(self):
        self.ensure_one()
        if self.delay_event_id:
            raise UserError(_("%s already has a delay event.") % self.name)
        event = self.env['realestate.construction.delay.event'].create({
            'title': self.title,
            'description': self.description,
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'event_type': 'other',
            'start_date': fields.Datetime.now(),
            'currency_id': self.currency_id.id,
        })
        self.delay_event_id = event
        return event
