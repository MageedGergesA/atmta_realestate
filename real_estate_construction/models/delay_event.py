# -*- coding: utf-8 -*-
"""Delay events — what happened, recorded while it is still happening.

A delay event is a **fact**: access was restricted, information was late, the
plant did not arrive. It is not an entitlement, not a claim and not a day of
extension. Whether it excuses anything depends on the contract, the governing
law and somebody's determination — none of which live in a Selection field.

The register exists because contemporaneous records win claims and
reconstructed ones do not.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: Descriptions of what happened. Deliberately **not** a liability taxonomy:
#: `weather` does not mean excusable, `contractor_delay` does not mean
#: culpable. Both of those are conclusions, and conclusions are not typed in
#: by whoever opened the record.
DELAY_EVENT_TYPES = [
    ('late_information', 'Late Information'),
    ('design_change', 'Design Change'),
    ('access_delay', 'Access / Possession'),
    ('late_approval', 'Late Approval'),
    ('material_delay', 'Material Delay'),
    ('equipment_delay', 'Equipment Delay'),
    ('labor_shortage', 'Labour Shortage'),
    ('authority_delay', 'Authority / Permit'),
    ('weather', 'Weather'),
    ('unforeseen_condition', 'Unforeseen Condition'),
    ('suspension', 'Suspension'),
    ('rework', 'Rework'),
    ('contractor_delay', 'Contractor Performance'),
    ('employer_delay', 'Employer Act or Omission'),
    ('consultant_delay', 'Consultant'),
    ('third_party', 'Third Party'),
    ('utility', 'Utility'),
    ('procurement', 'Procurement'),
    ('interface', 'Interface / Coordination'),
    ('force_majeure', 'Force Majeure / Exceptional Event'),
    ('other', 'Other'),
]

CAUSE_CATEGORIES = [
    ('employer', 'Employer / Client'),
    ('contractor', 'Contractor'),
    ('consultant', 'Consultant / Engineer'),
    ('authority', 'Authority'),
    ('third_party', 'Third Party'),
    ('neutral', 'Neutral Event'),
    ('concurrent', 'Concurrent / Mixed'),
    ('undetermined', 'Not Yet Assessed'),
]


class ConstructionDelayEvent(models.Model):
    _name = 'realestate.construction.delay.event'
    _description = 'Delay Event'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, start_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Text(
        help="What happened, in the words of the people who were there.")

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, index=True,
        domain="[('project_id', '=', project_id)]")
    contractor_id = fields.Many2one('realestate.contractor', index=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    location = fields.Char()

    event_type = fields.Selection(
        DELAY_EVENT_TYPES, required=True, default='other', index=True,
        tracking=True,
        help="What kind of event it was. Not who is liable for it.")
    cause_category = fields.Selection(
        CAUSE_CATEGORIES, default='undetermined', index=True, tracking=True,
        help="Who is assessed to have caused it, once somebody has assessed "
             "it. `Not Yet Assessed` is the honest default.")
    responsible_party = fields.Char(
        help="Named party, where one has been identified.")

    start_date = fields.Datetime(required=True, index=True, tracking=True)
    end_date = fields.Datetime(index=True, tracking=True)
    is_ongoing = fields.Boolean(
        default=True, tracking=True,
        help="A delay can run for months. An end date is not required while "
             "it is still running.")
    duration_days = fields.Float(
        compute='_compute_duration', store=True,
        help="Elapsed calendar days of the event itself. This is not an "
             "entitlement to that many days of extension.")

    discovered_on = fields.Date(index=True)
    notified_on = fields.Date(
        index=True, help="When notice was actually given, if it was.")
    raised_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    assigned_to_id = fields.Many2one('res.users', tracking=True)

    affected_work = fields.Char()
    schedule_activity_ref = fields.Char(
        string='Schedule Activity ID',
        help="Activity ID in the external programme (P6, MS Project). ATMTA "
             "does not schedule; it records which activity was affected.")
    schedule_reference = fields.Char(
        string='Programme Revision',
        help="Which revision of the programme the activity reference belongs "
             "to.")

    estimated_delay_days = fields.Float(
        tracking=True,
        help="Somebody's estimate of the time impact. An estimate, on a "
             "factual record — not a determination.")
    estimated_cost_impact = fields.Monetary(tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('monitoring', 'Monitoring'),
        ('ended', 'Ended'),
        ('assessed', 'Assessed'),
        ('closed', 'Closed'),
        ('void', 'Void'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    # -- Relations to the records that prove it happened -------------------
    daily_delay_ids = fields.One2many(
        'realestate.construction.daily.delay', 'delay_event_id',
        string='Daily Report Records', readonly=True,
        help="The daily site reports that recorded this event while it was "
             "happening. Evidence, referenced — never copied.")
    daily_record_count = fields.Integer(compute='_compute_counts')
    rfi_ids = fields.Many2many(
        'realestate.construction.rfi', 'delay_event_rfi_rel',
        'event_id', 'rfi_id', string='Related RFIs')
    ncr_ids = fields.Many2many(
        'realestate.construction.ncr', 'delay_event_ncr_rel',
        'event_id', 'ncr_id', string='Related NCRs')
    change_event_ids = fields.Many2many(
        'realestate.construction.change.event', 'delay_event_change_rel',
        'event_id', 'change_event_id', string='Related Change Events')
    claim_ids = fields.Many2many(
        'realestate.construction.claim', 'claim_delay_event_rel',
        'event_id', 'claim_id', string='Claims', readonly=True)
    claim_count = fields.Integer(compute='_compute_counts')
    notice_ids = fields.One2many(
        'realestate.construction.notice', 'delay_event_id', readonly=True)

    notes = fields.Html()

    # ------------------------------------------------------------------
    @api.depends('start_date', 'end_date', 'is_ongoing')
    def _compute_duration(self):
        now = fields.Datetime.now()
        for rec in self:
            if not rec.start_date:
                rec.duration_days = 0.0
                continue
            end = rec.end_date or (now if rec.is_ongoing else False)
            rec.duration_days = (
                (end - rec.start_date).total_seconds() / 86400.0
                if end else 0.0)

    @api.depends('daily_delay_ids', 'claim_ids')
    def _compute_counts(self):
        for rec in self:
            rec.daily_record_count = len(rec.daily_delay_ids)
            rec.claim_count = len(rec.claim_ids)

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for rec in self:
            if rec.end_date and rec.start_date and rec.end_date < rec.start_date:
                raise ValidationError(_(
                    "%s ended before it started.") % rec.name)

    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Delay event %(name)s is in %(company)s but its project "
                    "is in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.delay.event') or 'DEL/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_open(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is already open.") % rec.name)
            rec.write({'state': 'open',
                       'discovered_on': rec.discovered_on
                       or fields.Date.context_today(rec)})
        return True

    def action_monitor(self):
        for rec in self:
            if rec.state not in ('open', 'monitoring'):
                raise UserError(_("Only an open event is monitored."))
            rec.state = 'monitoring'
        return True

    def action_end(self, end_date=None):
        for rec in self:
            if rec.state not in ('open', 'monitoring'):
                raise UserError(_("%s is not running.") % rec.name)
            rec.write({
                'state': 'ended',
                'is_ongoing': False,
                'end_date': end_date or rec.end_date or fields.Datetime.now(),
            })
        return True

    def action_assess(self):
        for rec in self:
            if rec.state not in ('ended', 'monitoring', 'open'):
                raise UserError(_("%s cannot be assessed yet.") % rec.name)
            if rec.cause_category == 'undetermined':
                raise UserError(_(
                    "Record who is assessed to have caused %s before marking "
                    "it assessed. `Not Yet Assessed` and an assessment are "
                    "different things.") % rec.name)
            rec.state = 'assessed'
        return True

    def action_close(self):
        for rec in self:
            if rec.state in ('draft', 'void'):
                raise UserError(_("%s has nothing to close.") % rec.name)
            rec.write({'state': 'closed', 'is_ongoing': False})
        return True

    def action_void(self):
        for rec in self:
            if rec.claim_ids:
                raise UserError(_(
                    "%s is relied on by a claim. Close it with a reason "
                    "rather than voiding the record the claim points at.")
                    % rec.name)
            rec.state = 'void'
        return True

    def action_create_notice(self):
        """Open a notice for this event. It does not issue one."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Notice'),
            'res_model': 'realestate.construction.notice',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_project_id': self.project_id.id,
                'default_package_id': self.package_id.id,
                'default_delay_event_id': self.id,
                'default_awareness_date': self.discovered_on,
                'default_trigger_date': self.start_date
                and self.start_date.date() or False,
            },
        }

    def action_create_claim(self):
        """Open a claim built on this event. Creating it grants nothing."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Claim'),
            'res_model': 'realestate.construction.claim',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_project_id': self.project_id.id,
                'default_package_id': self.package_id.id,
                'default_contractor_id': self.contractor_id.id,
                'default_delay_event_ids': [(6, 0, self.ids)],
                'default_title': self.title,
            },
        }
