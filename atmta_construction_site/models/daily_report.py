# -*- coding: utf-8 -*-
"""M6J — the daily site report: the factual record of what happened.

Its value is not the narrative. It is that months later, when somebody claims
three weeks of delay, there is a contemporaneous record of who was on site,
what plant was there, what the weather did and what work was produced — written
on the day, by the people who were there.

So a closed report is **evidence**: it cannot be quietly rewritten. Correcting
one is an amendment with a reason.

### It records; it does not entitle

A delay recorded here is an observation about a day. It is not an extension of
time, not a claim and not a change order — those are M8's and M4's, and each
requires somebody to decide. `test_d_a_daily_report_is_evidence_not_entitlement`
asserts exactly that.

### Manpower has one home

`realestate.construction.labor.log` already records crews, hours and rates and
feeds cost lines. The daily report **references and summarises** those records
rather than keeping a second headcount — two manpower tables would disagree
within a week. At closure the summary is snapshotted, because evidence must not
change when somebody later edits a labour log.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

WEATHER_CONDITIONS = [
    ('clear', 'Clear'), ('cloudy', 'Cloudy'), ('rain', 'Rain'),
    ('storm', 'Storm'), ('wind', 'High Wind'), ('sandstorm', 'Sandstorm'),
    ('fog', 'Fog'), ('extreme_heat', 'Extreme Heat'), ('other', 'Other'),
]

DELAY_CATEGORIES = [
    ('weather', 'Weather'),
    ('material', 'Material Shortage'),
    ('manpower', 'Manpower Shortage'),
    ('equipment', 'Equipment Breakdown'),
    ('access', 'Access / Possession'),
    ('design', 'Design / Information'),
    ('approval', 'Approval / Inspection'),
    ('client', 'Client Instruction'),
    ('utility', 'Utilities / Authority'),
    ('other', 'Other'),
]


class ConstructionDailyReport(models.Model):
    _name = 'realestate.construction.daily.report'
    _description = 'Daily Site Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, report_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    report_date = fields.Date(
        required=True, default=fields.Date.context_today, index=True,
        tracking=True)
    shift = fields.Selection([
        ('day', 'Day'), ('night', 'Night'), ('both', 'Full Day'),
    ], default='day', required=True)

    prepared_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    reviewed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    reviewed_on = fields.Datetime(readonly=True, copy=False)

    weather_condition = fields.Selection(WEATHER_CONDITIONS, default='clear')
    temperature_min = fields.Float()
    temperature_max = fields.Float()
    weather_stopped_work = fields.Boolean()
    weather_notes = fields.Char()
    site_conditions = fields.Char()

    work_line_ids = fields.One2many(
        'realestate.construction.daily.work', 'report_id',
        string='Work Performed')
    equipment_line_ids = fields.One2many(
        'realestate.construction.daily.equipment', 'report_id',
        string='Equipment')
    delivery_line_ids = fields.One2many(
        'realestate.construction.daily.delivery', 'report_id',
        string='Deliveries')
    delay_ids = fields.One2many(
        'realestate.construction.daily.delay', 'report_id',
        string='Delays & Disruptions')

    labor_log_ids = fields.One2many(
        'realestate.construction.labor.log', 'daily_report_id',
        string='Labour Records',
        help="The authoritative manpower records for the day. This report "
             "summarises them; it does not keep a second headcount.")
    total_workers = fields.Integer(compute='_compute_labor', store=True)
    total_labor_hours = fields.Float(compute='_compute_labor', store=True)
    labor_summary_snapshot = fields.Text(
        readonly=True, copy=False,
        help="Frozen at closure. Editing a labour log afterwards must not "
             "change what this day's evidence says.")

    visitors = fields.Text()
    quality_notes = fields.Text()
    safety_notes = fields.Text(
        help="A note, not an HSE module. Safety management is out of scope.")
    overall_notes = fields.Html()
    total_delay_days = fields.Float(compute='_compute_delays', store=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('reviewed', 'Reviewed'),
        ('closed', 'Closed'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    amendment_reason = fields.Char(readonly=True, copy=False)
    amended_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    amended_on = fields.Datetime(readonly=True, copy=False)

    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_daily_report_attachment_rel',
        'report_id', 'attachment_id', string='Photos')

    _sql_constraints = [
        ('date_shift_uniq_per_project',
         'unique(project_id, report_date, shift)',
         'A project already has a daily report for that date and shift.'),
    ]

    @api.depends('labor_log_ids.workers', 'labor_log_ids.total_hours')
    def _compute_labor(self):
        for rec in self:
            rec.total_workers = sum(rec.labor_log_ids.mapped('workers'))
            rec.total_labor_hours = sum(
                rec.labor_log_ids.mapped('total_hours'))

    @api.depends('delay_ids.estimated_days')
    def _compute_delays(self):
        for rec in self:
            rec.total_delay_days = sum(rec.delay_ids.mapped('estimated_days'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.daily.report') or 'DSR/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft report can be submitted."))
            rec.state = 'submitted'
        return True

    def action_review(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only a submitted report can be reviewed."))
            rec.write({'state': 'reviewed',
                       'reviewed_by_id': self.env.user.id,
                       'reviewed_on': fields.Datetime.now()})
        return True

    def action_close(self):
        """Close the day, and freeze the manpower summary behind it."""
        for rec in self:
            if rec.state not in ('submitted', 'reviewed'):
                raise UserError(_(
                    "A report is closed once it has been submitted."))
            rec.with_context(re_daily_closing=True).write({
                'state': 'closed',
                'labor_summary_snapshot': rec._build_labor_snapshot(),
            })
        return True

    def _build_labor_snapshot(self):
        """Trade, contractor, headcount, hours — as they stood at closure."""
        self.ensure_one()
        rows = []
        for log in self.labor_log_ids:
            rows.append('%s | %s | %s workers | %.1f h' % (
                dict(log._fields['trade'].selection).get(log.trade, log.trade),
                log.contractor_id.display_name or '-',
                log.workers, log.total_hours))
        rows.append('TOTAL | %s workers | %.1f h' % (
            self.total_workers, self.total_labor_hours))
        return '\n'.join(rows)

    def action_open_reason_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Amend Daily Report"),
            'res_model': 'realestate.construction.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_mode': 'amend_daily_report',
                        'default_res_id': self.id},
        }

    def action_amend(self, reason=None):
        """Reopen a closed report — deliberately, with a reason on the record."""
        for rec in self:
            if rec.state != 'closed':
                raise UserError(_("Only a closed report needs amending."))
            if not reason:
                raise UserError(_(
                    "Amending a closed site record needs a reason. This is "
                    "the evidence a claim will be argued from."))
            rec.with_context(re_daily_closing=True).write({
                'state': 'submitted',
                'amendment_reason': reason,
                'amended_by_id': self.env.user.id,
                'amended_on': fields.Datetime.now(),
            })
            rec.message_post(body=_("Report amended: %s") % reason)
        return True

    def write(self, vals):
        if self.env.context.get('re_daily_closing'):
            return super().write(vals)
        closed = self.filtered(lambda r: r.state == 'closed')
        if closed and set(vals) - {'state'}:
            raise UserError(_(
                "%(refs)s are closed site records. Amend with a reason rather "
                "than editing the day's evidence.",
                refs=', '.join(closed.mapped('name'))))
        return super().write(vals)

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Daily report %(name)s is in %(company)s but its project "
                    "is in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))


class ConstructionDailyWork(models.Model):
    """Work observed on site. Production reported — not quantity certified."""
    _name = 'realestate.construction.daily.work'
    _description = 'Daily Report — Work Performed'
    _order = 'report_id, sequence, id'

    report_id = fields.Many2one(
        'realestate.construction.daily.report', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='report_id.company_id', store=True, readonly=True)
    project_id = fields.Many2one(
        related='report_id.project_id', store=True, readonly=True, index=True)
    sequence = fields.Integer(default=10)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null')
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', ondelete='set null')
    location = fields.Char()
    contractor_id = fields.Many2one('realestate.contractor')
    trade = fields.Char()
    description = fields.Char(required=True)
    quantity = fields.Float(
        help="Production observed today. Certification is a separate process "
             "with its own controls — this figure never certifies anything.")
    uom_id = fields.Many2one('uom.uom', string='UoM')
    boq_line_id = fields.Many2one(
        'realestate.boq.line', string='BOQ Line',
        help="Reference only. Certified quantity is M7's, through payment "
             "certificates.")
    task_id = fields.Many2one('realestate.construction.task')
    progress_note = fields.Char()


class ConstructionDailyEquipment(models.Model):
    """Plant on site. Minimal by design — this is not fleet management."""
    _name = 'realestate.construction.daily.equipment'
    _description = 'Daily Report — Equipment'
    _order = 'report_id, sequence, id'

    report_id = fields.Many2one(
        'realestate.construction.daily.report', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='report_id.company_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Equipment', required=True)
    equipment_type = fields.Char()
    contractor_id = fields.Many2one('realestate.contractor')
    owner = fields.Selection([
        ('owned', 'Owned'), ('hired', 'Hired'), ('contractor', 'Contractor'),
    ], default='contractor')
    location = fields.Char()
    working_hours = fields.Float()
    idle_hours = fields.Float()
    remarks = fields.Char()


class ConstructionDailyDelivery(models.Model):
    """Material that arrived. Inventory remains the quantity authority."""
    _name = 'realestate.construction.daily.delivery'
    _description = 'Daily Report — Delivery'
    _order = 'report_id, sequence, id'

    report_id = fields.Many2one(
        'realestate.construction.daily.report', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='report_id.company_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    description = fields.Char(required=True)
    product_id = fields.Many2one('product.product')
    partner_id = fields.Many2one('res.partner', string='Supplier')
    purchase_order_id = fields.Many2one('purchase.order')
    picking_id = fields.Many2one(
        'stock.picking', string='Receipt',
        help="Reference to Inventory's receipt. The received quantity is "
             "Inventory's; this line records that it turned up.")
    quantity = fields.Float()
    uom_id = fields.Many2one('uom.uom', string='UoM')
    remarks = fields.Char()


class ConstructionDailyDelay(models.Model):
    """A delay observed on a day.

    Not an extension of time, not a claim, not a change order. M8 may consume
    these as contemporaneous evidence; recording one entitles nobody to
    anything, which is the whole point of a factual record.
    """
    _name = 'realestate.construction.daily.delay'
    _description = 'Daily Report — Delay'
    _order = 'report_id, id'

    report_id = fields.Many2one(
        'realestate.construction.daily.report', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='report_id.company_id', store=True, readonly=True)
    project_id = fields.Many2one(
        related='report_id.project_id', store=True, readonly=True, index=True)
    category = fields.Selection(DELAY_CATEGORIES, required=True,
                                default='other', index=True)
    description = fields.Text(required=True)
    start_datetime = fields.Datetime()
    end_datetime = fields.Datetime()
    estimated_days = fields.Float(
        help="Observed lost time. An observation, not an entitlement.")
    affected_work = fields.Char()
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null')
    responsible_party = fields.Selection([
        ('owner', 'Owner'), ('consultant', 'Consultant'),
        ('contractor', 'Contractor'), ('authority', 'Authority'),
        ('neutral', 'Neutral Event'), ('unknown', 'Not Determined'),
    ], default='unknown',
        help="Somebody's view on the day. Entitlement is decided later, by "
             "people, under the contract.")
    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False)
    delay_event_id = fields.Many2one(
        'realestate.construction.delay.event', index=True,
        help="The delay event this day's record is evidence of. One event "
             "runs for weeks and collects a daily record for each of them — "
             "that is contemporaneous evidence, not three separate delays.")

    def action_create_delay_event(self):
        """Raise the register entry this day's record belongs to.

        It creates a factual event and stops. It issues no notice, opens no
        claim and grants no extension — each of those is somebody's decision,
        made somewhere it can be seen.
        """
        self.ensure_one()
        if self.delay_event_id:
            raise UserError(_(
                "This record already belongs to %s.")
                % self.delay_event_id.name)
        report = self.report_id
        event = self.env['realestate.construction.delay.event'].create({
            'title': self.description[:80] if self.description else _('Delay'),
            'description': self.description,
            'project_id': report.project_id.id,
            'company_id': report.company_id.id,
            'wbs_id': self.wbs_id.id or False,
            'event_type': 'other',
            'start_date': self.start_datetime or fields.Datetime.to_datetime(
                report.report_date),
            'end_date': self.end_datetime or False,
            'is_ongoing': not self.end_datetime,
            'estimated_delay_days': self.estimated_days,
            'discovered_on': report.report_date,
        })
        event.action_open()
        self.delay_event_id = event
        return event

    def action_create_change_event(self):
        """Only if somebody decides this delay has commercial consequences."""
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_("This delay already has a change event."))
        report = self.report_id
        event = self.env['realestate.construction.change.event'].create({
            'title': _("Delay on %(date)s — %(category)s",
                       date=report.report_date,
                       category=dict(DELAY_CATEGORIES).get(self.category)),
            'project_id': report.project_id.id,
            'company_id': report.company_id.id,
            'wbs_id': self.wbs_id.id or False,
            'source': 'delay',
            'source_reference': report.name,
            'source_model': report._name,
            'source_id': report.id,
            'estimated_schedule_days': int(self.estimated_days or 0),
            'description': self.description,
        })
        self.change_event_id = event
        return event
