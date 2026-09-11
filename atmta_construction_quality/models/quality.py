# -*- coding: utf-8 -*-
"""M6 — quality: plans, inspections, observations and non-conformances.

```
    ITP (what must be checked, and by whom)
      └ INSPECTION REQUEST   "ready for you on Tuesday"
          └ INSPECTION       what was actually found
              ├ accepted / accepted with comments / rejected
              ├ OBSERVATION  a minor defect somebody must fix
              └ NCR          a formal non-conformance with a disposition
                  └ CHANGE EVENT (M4) — only if there is commercial impact
```

### Four things this module refuses to do

**A failed check is not automatically an NCR.** Most failures are corrected on
the spot or raised as an observation. Auto-generating a formal non-conformance
for every missing sealant buries the ones that matter, and an NCR register
nobody reads is worse than none.

**An NCR is not a change order.** It may cause rework, and rework may cost
money — but the money moves through M4's approval, never from a quality record.

**An inspection result is not progress.** Passing a hold point releases work;
it does not certify a quantity. M7 owns certification, and this module offers
`is_quality_released()` for it to consult.

**A quality record does not chase the drawing.** An inspection performed
against Rev B says Rev B forever, exactly as an RFI does.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

DISCIPLINES = [
    ('architectural', 'Architectural'), ('structural', 'Structural'),
    ('civil', 'Civil'), ('mechanical', 'Mechanical'),
    ('electrical', 'Electrical'), ('plumbing', 'Plumbing'),
    ('hvac', 'HVAC'), ('landscape', 'Landscape'),
    ('interior', 'Interior'), ('other', 'Other'),
]

#: The control level of an ITP checkpoint.
INSPECTION_POINTS = [
    ('hold', 'Hold Point'),
    ('witness', 'Witness Point'),
    ('review', 'Review Point'),
    ('surveillance', 'Surveillance'),
    ('normal', 'Normal Inspection'),
]

INSPECTION_TYPES = [
    ('work', 'Work Inspection'),
    ('material', 'Material Inspection'),
    ('installation', 'Installation'),
    ('test', 'Test'),
    ('pre_pour', 'Pre-Pour'),
    ('final', 'Final Inspection'),
    ('other', 'Other'),
]

#: Richer than a boolean, because construction quality is.
INSPECTION_RESULTS = [
    ('accepted', 'Accepted'),
    ('accepted_with_comments', 'Accepted with Comments'),
    ('rejected', 'Rejected'),
    ('reinspection_required', 'Reinspection Required'),
    ('not_applicable', 'Not Applicable'),
    ('cancelled', 'Cancelled'),
]

#: Results that let the work proceed.
PASSING_RESULTS = ('accepted', 'accepted_with_comments')

NCR_DISPOSITIONS = [
    ('rework', 'Rework'),
    ('repair', 'Repair'),
    ('replace', 'Replace'),
    ('reject', 'Reject / Remove'),
    ('use_as_is', 'Use As Is'),
    ('concession', 'Concession'),
    ('redesign', 'Redesign'),
    ('other', 'Other'),
]

ROOT_CAUSES = [
    ('workmanship', 'Workmanship'), ('material', 'Material'),
    ('design', 'Design'), ('method', 'Method'),
    ('supervision', 'Supervision'), ('coordination', 'Coordination'),
    ('supplier', 'Supplier'), ('handling', 'Storage / Handling'),
    ('documentation', 'Documentation'), ('equipment', 'Equipment'),
    ('other', 'Other'),
]

IMPACT_CLASSIFICATION = [
    ('unknown', 'Unknown'),
    ('none', 'None'),
    ('potential', 'Potential'),
    ('managed', 'Managed by Change Management'),
]

SEVERITIES = [
    ('minor', 'Minor'), ('major', 'Major'), ('critical', 'Critical'),
]

# =====================================================================
# ITP
# =====================================================================
class ConstructionITP(models.Model):
    """Inspection & Test Plan — what must be checked, when, and against what.

    Revisions are formal: an inspection records the ITP revision **in force
    when it was performed**, so amending the plan next year cannot rewrite what
    last year's inspection was measured against.
    """
    _name = 'realestate.construction.itp'
    _description = 'Inspection & Test Plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, name, revision desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    contractor_id = fields.Many2one('realestate.contractor')
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    discipline = fields.Selection(DISCIPLINES, default='other', index=True)
    activity = fields.Char(help="The work this plan governs.")
    specification_section = fields.Char()

    method_statement_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Method Statement', ondelete='restrict')
    document_revision_ids = fields.Many2many(
        'realestate.construction.document.revision',
        'construction_itp_document_rel', 'itp_id', 'revision_id',
        string='Reference Revisions')
    submittal_id = fields.Many2one('realestate.construction.submittal')

    revision = fields.Integer(default=0, readonly=True, copy=False,
                              tracking=True)
    supersedes_id = fields.Many2one(
        'realestate.construction.itp', readonly=True, copy=False)
    superseded_by_id = fields.Many2one(
        'realestate.construction.itp', readonly=True, copy=False)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('approved', 'Approved'),
        ('active', 'Active'),
        ('superseded', 'Superseded'),
        ('closed', 'Closed'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    effective_date = fields.Date(tracking=True)
    prepared_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user)
    reviewed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    item_ids = fields.One2many(
        'realestate.construction.itp.item', 'itp_id', string='Checkpoints',
        copy=True)
    item_count = fields.Integer(compute='_compute_items', store=True)
    hold_point_count = fields.Integer(compute='_compute_items', store=True)
    notes = fields.Html()

    @api.depends('item_ids.inspection_point')
    def _compute_items(self):
        for rec in self:
            rec.item_count = len(rec.item_ids)
            rec.hold_point_count = len(rec.item_ids.filtered(
                lambda i: i.inspection_point == 'hold'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.itp') or 'ITP/NEW'
        return super().create(vals_list)

    def init(self):
        """One active revision of an ITP at a time, in the database."""
        super().init()
        self._cr.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_class
                               WHERE relname = 'construction_itp_one_active') THEN
                    CREATE UNIQUE INDEX construction_itp_one_active
                        ON realestate_construction_itp (project_id, name)
                     WHERE state = 'active';
                END IF;
            END $$;
        """)

    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft ITP can be submitted."))
            if not rec.item_ids:
                raise UserError(_(
                    "An inspection plan with no checkpoints plans nothing."))
            rec.write({'state': 'review',
                       'reviewed_by_id': self.env.user.id})
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != 'review':
                raise UserError(_("Only an ITP under review is approved."))
            rec.write({'state': 'approved',
                       'approved_by_id': self.env.user.id})
        return True

    def action_activate(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_("Only an approved ITP becomes active."))
            rec._lock()
            existing = self.search([
                ('project_id', '=', rec.project_id.id),
                ('name', '=', rec.name),
                ('state', '=', 'active'),
                ('id', '!=', rec.id),
            ], limit=1)
            if existing:
                raise UserError(_(
                    "%(name)s revision %(rev)s is already active. Supersede "
                    "it rather than activating a second one.",
                    name=existing.name, rev=existing.revision))
            rec.write({'state': 'active',
                       'effective_date': (rec.effective_date or
                                          fields.Date.context_today(rec))})
        return True

    def action_create_revision(self):
        """A new revision, carrying the checkpoints, leaving history alone."""
        self.ensure_one()
        if self.state != 'active':
            raise UserError(_("Only the active revision can be revised."))
        revision = self.copy({
            'revision': self.revision + 1,
            'state': 'draft',
            'supersedes_id': self.id,
            'approved_by_id': False,
            'reviewed_by_id': False,
            'effective_date': False,
        })
        revision.name = self.name
        return revision

    def action_supersede(self):
        self.ensure_one()
        successor = self.search([
            ('supersedes_id', '=', self.id),
            ('state', '=', 'approved'),
        ], limit=1)
        if not successor:
            raise UserError(_(
                "Approve the new revision before retiring this one."))
        self._lock()
        self.write({'state': 'superseded', 'superseded_by_id': successor.id})
        successor.action_activate()
        return True

    def _lock(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (hash('re.construction.itp') % 2147483647, self.project_id.id))

    def write(self, vals):
        """An approved plan's checkpoints are what inspections cite."""
        protected = {'item_ids', 'project_id', 'company_id', 'revision'}
        if protected & set(vals):
            frozen = self.filtered(
                lambda i: i.state in ('active', 'superseded', 'closed'))
            if frozen and not self.env.context.get('re_itp_superseding'):
                raise UserError(_(
                    "%(refs)s are in force or historical. Create a revision "
                    "rather than editing the plan inspections were performed "
                    "against.", refs=', '.join(frozen.mapped('name'))))
        return super().write(vals)

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "ITP %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

class ConstructionITPItem(models.Model):
    """One checkpoint in a plan."""
    _name = 'realestate.construction.itp.item'
    _description = 'ITP Checkpoint'
    _order = 'itp_id, sequence, id'

    itp_id = fields.Many2one(
        'realestate.construction.itp', required=True, ondelete='cascade',
        index=True)
    company_id = fields.Many2one(
        related='itp_id.company_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    activity = fields.Char(required=True)
    description = fields.Char(string='Inspection / Test')
    acceptance_criteria = fields.Text()
    reference = fields.Char(string='Specification Reference')

    inspection_point = fields.Selection(
        INSPECTION_POINTS, default='normal', required=True, index=True,
        help="Hold points stop the work until released. Witness points give "
             "the reviewer the opportunity to attend.")
    work_release_required = fields.Boolean(
        compute='_compute_release', store=True,
        help="True for hold points: downstream work needs an accepted "
             "inspection before it may proceed.")
    frequency = fields.Char(help="Each pour, every 500m², weekly…")
    responsible_contractor_id = fields.Many2one('realestate.contractor')
    inspector_id = fields.Many2one('res.users')
    reviewer_partner_id = fields.Many2one(
        'res.partner', string='Consultant / Client')
    record_required = fields.Char(help="The record this checkpoint produces.")
    certificate_required = fields.Boolean()
    checklist_template_id = fields.Many2one(
        'realestate.construction.checklist.template')
    notes = fields.Char()

    @api.depends('inspection_point')
    def _compute_release(self):
        for rec in self:
            rec.work_release_required = rec.inspection_point == 'hold'

# =====================================================================
# Checklist templates
# =====================================================================
class ConstructionChecklistTemplate(models.Model):
    """A reusable checklist. Instantiating copies its lines, so editing the
    template next year cannot change a closed inspection."""
    _name = 'realestate.construction.checklist.template'
    _description = 'Quality Checklist Template'
    _order = 'name'

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, index=True)
    inspection_type = fields.Selection(INSPECTION_TYPES, default='work')
    discipline = fields.Selection(DISCIPLINES, default='other')
    description = fields.Text()
    line_ids = fields.One2many(
        'realestate.construction.checklist.template.line', 'template_id',
        string='Items', copy=True)
    active = fields.Boolean(default=True)

class ConstructionChecklistTemplateLine(models.Model):
    _name = 'realestate.construction.checklist.template.line'
    _description = 'Checklist Template Item'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one(
        'realestate.construction.checklist.template', required=True,
        ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    item_type = fields.Selection([
        ('pass_fail', 'Pass / Fail'),
        ('yes_no', 'Yes / No'),
        ('measurement', 'Measurement'),
        ('number', 'Number'),
        ('text', 'Text'),
        ('selection', 'Selection'),
        ('date', 'Date'),
        ('photo', 'Photo Required'),
        ('attachment', 'Attachment Required'),
    ], default='pass_fail', required=True)
    acceptance_criteria = fields.Char()
    minimum_value = fields.Float()
    maximum_value = fields.Float()
    target_value = fields.Float()
    uom_id = fields.Many2one('uom.uom', string='UoM')
    selection_values = fields.Char(help="Comma-separated options.")
    is_mandatory = fields.Boolean(default=True)
    reference = fields.Char()

# =====================================================================
# Inspection request  →  inspection
# =====================================================================
class ConstructionInspectionRequest(models.Model):
    """"The work is ready — please come and look at it."

    Deliberately a separate record from the inspection itself: the request is
    the contractor's notice with its lead time, and the inspection is what the
    inspector found. Merging them would lose the request that was never
    attended, which is exactly the thing people argue about.

    Not to be confused with M5's RFI, which asks a question about the design.
    """
    _name = 'realestate.construction.inspection.request'
    _description = 'Inspection Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, required_datetime, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    contractor_id = fields.Many2one('realestate.contractor', tracking=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True, domain="[('project_id', '=', project_id)]")
    location = fields.Char()
    discipline = fields.Selection(DISCIPLINES, default='other', index=True)

    itp_id = fields.Many2one('realestate.construction.itp')
    itp_item_id = fields.Many2one(
        'realestate.construction.itp.item',
        domain="[('itp_id', '=', itp_id)]")
    inspection_type = fields.Selection(
        INSPECTION_TYPES, default='work', required=True, index=True)

    requested_datetime = fields.Datetime(
        default=fields.Datetime.now, required=True)
    required_datetime = fields.Datetime(
        required=True, index=True,
        help="When the contractor wants the inspection to happen.")
    lead_time_hours = fields.Float(
        compute='_compute_lead_time', store=True,
        help="Notice given. The required minimum is configuration, not a "
             "number baked into the code.")
    lead_time_shortfall = fields.Boolean(
        compute='_compute_lead_time', store=True,
        help="Short notice. Reported rather than blocked — sites work.")

    requested_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user)
    inspector_id = fields.Many2one('res.users', string='Assigned Inspector',
                                   tracking=True)
    reviewer_partner_id = fields.Many2one('res.partner',
                                          string='Consultant / Client')

    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision', ondelete='restrict')
    submittal_id = fields.Many2one('realestate.construction.submittal')
    product_id = fields.Many2one('product.product', string='Material')
    purchase_order_id = fields.Many2one('purchase.order')
    quantity = fields.Float()
    uom_id = fields.Many2one('uom.uom', string='UoM')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('requested', 'Requested'),
        ('scheduled', 'Scheduled'),
        ('in_progress', 'Inspection in Progress'),
        ('responded', 'Responded'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
        ('rejected_request', 'Request Rejected'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    inspection_ids = fields.One2many(
        'realestate.construction.inspection', 'request_id',
        string='Inspections')
    inspection_count = fields.Integer(compute='_compute_inspections')
    notes = fields.Text()
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_ir_attachment_rel', 'request_id',
        'attachment_id', string='Attachments')

    @api.depends('requested_datetime', 'required_datetime')
    def _compute_lead_time(self):
        minimum = float(self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.inspection_lead_time_hours', '24'))
        for rec in self:
            if not (rec.requested_datetime and rec.required_datetime):
                rec.lead_time_hours = 0.0
                rec.lead_time_shortfall = False
                continue
            delta = rec.required_datetime - rec.requested_datetime
            rec.lead_time_hours = delta.total_seconds() / 3600.0
            rec.lead_time_shortfall = rec.lead_time_hours < minimum

    def _compute_inspections(self):
        for rec in self:
            rec.inspection_count = len(rec.inspection_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.inspection.request') or 'IR/NEW'
        return super().create(vals_list)

    def action_request(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft request can be raised."))
            rec.state = 'requested'
            if rec.lead_time_shortfall:
                rec.message_post(body=_(
                    "Short notice: %(hours).1f hours given.",
                    hours=rec.lead_time_hours))
        return True

    def action_schedule(self):
        for rec in self:
            if rec.state not in ('requested',):
                raise UserError(_("Only a raised request can be scheduled."))
            rec.state = 'scheduled'
        return True

    def action_reject_request(self):
        for rec in self:
            if rec.state in ('responded', 'closed'):
                raise UserError(_(
                    "An inspection has already been performed."))
            rec.state = 'rejected_request'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.inspection_ids.filtered('result'):
                raise UserError(_(
                    "An inspection has been performed against this request."))
            rec.state = 'cancelled'
        return True

    def action_create_inspection(self):
        """The inspection that answers this request."""
        self.ensure_one()
        if self.state in ('cancelled', 'rejected_request'):
            raise UserError(_("This request is closed."))
        inspection = self.env['realestate.construction.inspection'].create({
            'request_id': self.id,
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'location': self.location,
            'discipline': self.discipline,
            'inspection_type': self.inspection_type,
            'itp_id': self.itp_id.id or False,
            'itp_item_id': self.itp_item_id.id or False,
            'itp_revision': self.itp_id.revision,
            'document_revision_id': self.document_revision_id.id or False,
            'submittal_id': self.submittal_id.id or False,
            'inspector_id': self.inspector_id.id or self.env.user.id,
        })
        self.state = 'in_progress'
        return inspection

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Inspection request %(name)s is in %(company)s but its "
                    "project is in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

class ConstructionInspection(models.Model):
    """What the inspector actually found, and against what."""
    _name = 'realestate.construction.inspection'
    _description = 'Quality Inspection'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, inspection_datetime desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    request_id = fields.Many2one(
        'realestate.construction.inspection.request', ondelete='set null',
        index=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True)
    contractor_id = fields.Many2one('realestate.contractor')
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True)
    location = fields.Char()
    discipline = fields.Selection(DISCIPLINES, default='other', index=True)
    inspection_type = fields.Selection(
        INSPECTION_TYPES, default='work', required=True, index=True)

    itp_id = fields.Many2one('realestate.construction.itp', index=True)
    itp_item_id = fields.Many2one('realestate.construction.itp.item')
    itp_revision = fields.Integer(
        readonly=True,
        help="The plan revision in force when this inspection happened. "
             "Recorded, not looked up, so revising the plan later cannot "
             "rewrite what this was measured against.")
    inspection_point = fields.Selection(
        related='itp_item_id.inspection_point', store=True, readonly=True)

    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision', ondelete='restrict',
        index=True,
        help="The exact revision inspected against. It stays this revision "
             "when a newer one is issued.")
    submittal_id = fields.Many2one('realestate.construction.submittal')
    specification_reference = fields.Char()

    inspector_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, tracking=True)
    witness_partner_id = fields.Many2one(
        'res.partner', string='Client / Consultant Representative')
    inspection_datetime = fields.Datetime(
        default=fields.Datetime.now, index=True, tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('responded', 'Responded'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    result = fields.Selection(
        INSPECTION_RESULTS, readonly=True, copy=False, tracking=True,
        index=True)
    result_datetime = fields.Datetime(readonly=True, copy=False)
    comments = fields.Text()
    follow_up_required = fields.Boolean()

    checklist_line_ids = fields.One2many(
        'realestate.construction.inspection.line', 'inspection_id',
        string='Checklist')
    failed_line_count = fields.Integer(compute='_compute_lines', store=True)

    parent_inspection_id = fields.Many2one(
        'realestate.construction.inspection', string='Reinspection Of',
        readonly=True, index=True)
    reinspection_ids = fields.One2many(
        'realestate.construction.inspection', 'parent_inspection_id',
        string='Reinspections')
    reinspection_sequence = fields.Integer(default=0, readonly=True)
    reinspection_reason = fields.Char()
    has_reinspection = fields.Boolean(compute='_compute_chain', store=True)
    is_first_inspection = fields.Boolean(compute='_compute_chain', store=True)

    observation_ids = fields.One2many(
        'realestate.construction.quality.observation', 'inspection_id',
        string='Observations')
    ncr_ids = fields.One2many(
        'realestate.construction.ncr', 'source_inspection_id', string='NCRs')

    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_inspection_attachment_rel',
        'inspection_id', 'attachment_id', string='Photos & Evidence')

    @api.depends('checklist_line_ids.passed')
    def _compute_lines(self):
        for rec in self:
            rec.failed_line_count = len(rec.checklist_line_ids.filtered(
                lambda l: l.passed == 'fail'))

    @api.depends('reinspection_ids', 'parent_inspection_id')
    def _compute_chain(self):
        for rec in self:
            rec.has_reinspection = bool(rec.reinspection_ids)
            rec.is_first_inspection = not rec.parent_inspection_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.inspection') or 'INS/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_start(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("This inspection has already started."))
            rec.state = 'in_progress'
            if rec.itp_item_id and not rec.checklist_line_ids:
                rec._instantiate_checklist()
        return True

    def _instantiate_checklist(self):
        """Copy the template's items onto this inspection.

        Copied, not referenced: the company's template will be edited next
        year, and a closed inspection must keep the criteria it was actually
        judged against.
        """
        self.ensure_one()
        template = self.itp_item_id.checklist_template_id
        if not template:
            return
        Line = self.env['realestate.construction.inspection.line']
        Line.create([{
            'inspection_id': self.id,
            'sequence': line.sequence,
            'name': line.name,
            'item_type': line.item_type,
            'acceptance_criteria': line.acceptance_criteria,
            'minimum_value': line.minimum_value,
            'maximum_value': line.maximum_value,
            'target_value': line.target_value,
            'uom_id': line.uom_id.id,
            'is_mandatory': line.is_mandatory,
            'reference': line.reference,
        } for line in template.line_ids])

    def action_record_result(self, result, comments=None):
        """Record what was found. The result is then evidence."""
        self.ensure_one()
        if self.result:
            raise UserError(_(
                "%s already has a result. Raise a reinspection instead.")
                % self.name)
        if result not in dict(INSPECTION_RESULTS):
            raise UserError(_("Unknown inspection result: %s") % result)
        if self.state not in ('draft', 'in_progress'):
            raise UserError(_("%s is not open for a result.") % self.name)
        mandatory_blank = self.checklist_line_ids.filtered(
            lambda l: l.is_mandatory and not l.passed)
        if mandatory_blank and result in PASSING_RESULTS:
            raise UserError(_(
                "%(count)s mandatory checklist item(s) have no answer. An "
                "inspection cannot be accepted on unanswered checks.",
                count=len(mandatory_blank)))
        # Every failure must say what was done about it. Checked here rather
        # than as a constraint on the line: the line does not change when the
        # inspection is signed off, so a line-level constraint would never run
        # at the only moment that matters.
        undisposed = self.checklist_line_ids.filtered(
            lambda l: l.passed == 'fail' and not l.disposition)
        if undisposed:
            raise UserError(_(
                "%(items)s failed with no disposition recorded. A failure "
                "nobody resolved is the one that reaches the client.",
                items=', '.join(undisposed.mapped('name'))))
        self.write({
            'result': result,
            'result_datetime': fields.Datetime.now(),
            'comments': comments or self.comments,
            'state': 'responded',
        })
        if self.request_id:
            self.request_id.state = 'responded'
        return True

    # -- form buttons: a button cannot pass an argument, so each result
    # -- gets its own named door into the one method that records it.
    def action_accept(self):
        return self.action_record_result('accepted')

    def action_accept_with_comments(self):
        return self.action_record_result('accepted_with_comments')

    def action_reject_result(self):
        return self.action_record_result('rejected')

    def action_require_reinspection(self):
        return self.action_record_result('reinspection_required')

    def action_not_applicable(self):
        return self.action_record_result('not_applicable')

    def action_close(self):
        for rec in self:
            if not rec.result:
                raise UserError(_(
                    "An inspection closes on a result, not on a wish."))
            rec.state = 'closed'
            if rec.request_id and not rec.request_id.inspection_ids.filtered(
                    lambda i: i.state not in ('closed', 'cancelled')):
                rec.request_id.state = 'closed'
        return True

    def action_create_reinspection(self):
        """A fresh inspection. The failure it follows stays exactly as it is."""
        self.ensure_one()
        if self.result in PASSING_RESULTS:
            raise UserError(_(
                "%s was accepted — there is nothing to reinspect.") % self.name)
        if not self.result:
            raise UserError(_(
                "Record this inspection's result before reinspecting."))
        root = self.parent_inspection_id or self
        siblings = self.search_count([('parent_inspection_id', '=', root.id)])
        return self.create({
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'request_id': self.request_id.id or False,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'location': self.location,
            'discipline': self.discipline,
            'inspection_type': self.inspection_type,
            'itp_id': self.itp_id.id or False,
            'itp_item_id': self.itp_item_id.id or False,
            'itp_revision': self.itp_revision,
            'document_revision_id': self.document_revision_id.id or False,
            'submittal_id': self.submittal_id.id or False,
            'inspector_id': self.inspector_id.id,
            'parent_inspection_id': root.id,
            'reinspection_sequence': siblings + 1,
            'reinspection_reason': _("Reinspection after %s") % self.name,
        })

    def action_create_observation(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.construction.quality.observation',
            'view_mode': 'form',
            'context': {
                'default_inspection_id': self.id,
                'default_project_id': self.project_id.id,
                'default_contractor_id': self.contractor_id.id,
                'default_wbs_id': self.wbs_id.id,
                'default_location': self.location,
            },
        }

    def action_create_ncr(self):
        """Deliberate. Not every rejection deserves a formal non-conformance."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.construction.ncr',
            'view_mode': 'form',
            'context': {
                'default_source_inspection_id': self.id,
                'default_project_id': self.project_id.id,
                'default_package_id': self.package_id.id,
                'default_contractor_id': self.contractor_id.id,
                'default_wbs_id': self.wbs_id.id,
                'default_location': self.location,
                'default_document_revision_id': self.document_revision_id.id,
            },
        }

    def write(self, vals):
        """A recorded result is evidence."""
        protected = {'result', 'document_revision_id', 'itp_revision',
                     'inspection_datetime', 'checklist_line_ids'}
        if protected & set(vals):
            recorded = self.filtered('result')
            if recorded and not self.env.context.get('re_inspection_recording'):
                raise UserError(_(
                    "%(refs)s have been carried out and recorded. Raise a "
                    "reinspection rather than rewriting what was found.",
                    refs=', '.join(recorded.mapped('name'))))
        return super().write(vals)

    # ------------------------------------------------------------------
    @api.model
    def is_quality_released(self, wbs=None, itp_item=None, project=None):
        """Has the hold point been released?

        The service M7's certification will consult. Quality release is **not**
        payment certification and this returns a fact, not a permission: it is
        for the certification workflow to decide what to do with it.
        """
        domain = [('inspection_point', '=', 'hold'),
                  ('result', 'in', list(PASSING_RESULTS))]
        if project:
            domain.append(('project_id', '=', project.id))
        if wbs:
            domain.append(('wbs_id', '=', wbs.id))
        if itp_item:
            domain.append(('itp_item_id', '=', itp_item.id))
        return bool(self.search_count(domain))

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Inspection %(name)s is in %(company)s but its project is "
                    "in %(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

class ConstructionInspectionLine(models.Model):
    """One checked item, and what it measured."""
    _name = 'realestate.construction.inspection.line'
    _description = 'Inspection Checklist Result'
    _order = 'inspection_id, sequence, id'

    inspection_id = fields.Many2one(
        'realestate.construction.inspection', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='inspection_id.company_id', store=True, readonly=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    item_type = fields.Selection([
        ('pass_fail', 'Pass / Fail'), ('yes_no', 'Yes / No'),
        ('measurement', 'Measurement'), ('number', 'Number'),
        ('text', 'Text'), ('selection', 'Selection'), ('date', 'Date'),
        ('photo', 'Photo Required'), ('attachment', 'Attachment Required'),
    ], default='pass_fail', required=True)
    acceptance_criteria = fields.Char()
    reference = fields.Char()
    is_mandatory = fields.Boolean(default=True)

    passed = fields.Selection([
        ('pass', 'Pass'), ('fail', 'Fail'), ('na', 'Not Applicable'),
    ], index=True)
    measured_value = fields.Float()
    minimum_value = fields.Float()
    maximum_value = fields.Float()
    target_value = fields.Float()
    uom_id = fields.Many2one('uom.uom', string='UoM')
    text_value = fields.Char()
    date_value = fields.Date()
    within_tolerance = fields.Boolean(compute='_compute_tolerance', store=True)

    disposition = fields.Selection([
        ('corrected', 'Corrected on the Spot'),
        ('observation', 'Observation Raised'),
        ('ncr', 'NCR Raised'),
        ('accepted', 'Accepted with Comment'),
        ('none', 'No Action — Reason Recorded'),
    ], help="What was done about a failure. A failure with no disposition is "
            "a failure nobody resolved.")
    disposition_reason = fields.Char()
    comments = fields.Char()
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_inspection_line_attachment_rel',
        'line_id', 'attachment_id', string='Evidence')

    @api.depends('measured_value', 'minimum_value', 'maximum_value',
                 'item_type')
    def _compute_tolerance(self):
        for rec in self:
            if rec.item_type != 'measurement':
                rec.within_tolerance = True
                continue
            value = rec.measured_value
            low = rec.minimum_value
            high = rec.maximum_value
            rec.within_tolerance = (
                (not low or value >= low) and (not high or value <= high))

    @api.constrains('passed', 'disposition')
    def _check_failure_has_a_disposition(self):
        """Guards edits made *after* an inspection was signed off.

        The main enforcement is in `inspection.action_record_result()`, where
        the sign-off happens; an inspector filling a sheet in any order must
        not be interrupted mid-way. This catches somebody clearing a
        disposition on a recorded inspection afterwards.
        """
        for rec in self:
            if rec.passed == 'fail' and rec.inspection_id.result \
                    and not rec.disposition:
                raise ValidationError(_(
                    "'%s' failed and has no disposition. A failure nobody "
                    "resolved is the one that reaches the client.") % rec.name)

# =====================================================================
# Observation  →  NCR
# =====================================================================
class ConstructionQualityObservation(models.Model):
    """A defect worth fixing but not worth a formal non-conformance.

    Missing sealant, a damaged finish, incomplete protection. Most quality
    findings are these, and giving them their own record is what keeps the NCR
    register meaningful.
    """
    _name = 'realestate.construction.quality.observation'
    _description = 'Quality Observation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, due_date, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    description = fields.Text(required=True)
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    inspection_id = fields.Many2one(
        'realestate.construction.inspection', ondelete='set null', index=True)
    contractor_id = fields.Many2one('realestate.contractor')
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True)
    location = fields.Char()
    severity = fields.Selection(SEVERITIES, default='minor', required=True,
                                index=True)

    assigned_to_id = fields.Many2one('res.users', tracking=True)
    due_date = fields.Date(index=True)
    corrective_action = fields.Text()
    completed_date = fields.Date(readonly=True, copy=False)
    verified_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    verified_date = fields.Date(readonly=True, copy=False)

    state = fields.Selection([
        ('open', 'Open'),
        ('action_required', 'Action Required'),
        ('ready_for_verification', 'Ready for Verification'),
        ('closed', 'Closed'),
        ('void', 'Void'),
    ], default='open', required=True, tracking=True, copy=False, index=True)
    ncr_id = fields.Many2one(
        'realestate.construction.ncr', readonly=True, copy=False,
        help="Set when this observation was escalated. The observation stays "
             "exactly as it was — escalating is not converting.")
    is_overdue = fields.Boolean(compute='_compute_overdue', store=True,
                                index=True)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_observation_attachment_rel',
        'observation_id', 'attachment_id', string='Photos')

    @api.depends('due_date', 'state')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                rec.due_date and rec.due_date < today
                and rec.state not in ('closed', 'void'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.quality.observation') or 'OBS/NEW'
        return super().create(vals_list)

    def action_require_action(self):
        for rec in self:
            rec.state = 'action_required'
        return True

    def action_ready_for_verification(self):
        for rec in self:
            if not rec.corrective_action:
                raise UserError(_(
                    "Say what was done before asking somebody to verify it."))
            rec.write({'state': 'ready_for_verification',
                       'completed_date': fields.Date.context_today(rec)})
        return True

    def action_verify(self):
        for rec in self:
            if rec.state != 'ready_for_verification':
                raise UserError(_(
                    "Corrective work is verified once it is complete."))
            rec.write({
                'state': 'closed',
                'verified_by_id': self.env.user.id,
                'verified_date': fields.Date.context_today(rec),
            })
        return True

    def action_void(self):
        for rec in self:
            if rec.state == 'closed':
                raise UserError(_("A closed observation is part of the record."))
            rec.state = 'void'
        return True

    def action_open_reason_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Escalate to NCR'),
            'res_model': 'realestate.construction.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_mode': 'escalate_observation', 'default_res_id': self.id},
        }

    def action_escalate_to_ncr(self, reason=None):
        """Raise a formal non-conformance from this observation.

        The observation is kept and linked, not consumed: the fact that
        somebody first judged this minor is part of the history.
        """
        self.ensure_one()
        if self.ncr_id:
            raise UserError(_("%s has already been escalated.") % self.name)
        ncr = self.env['realestate.construction.ncr'].create({
            'title': _("Escalated from %s") % self.name,
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'location': self.location,
            'severity': self.severity,
            'description': self.description,
            'source': 'observation',
            'source_inspection_id': self.inspection_id.id or False,
            'source_observation_id': self.id,
            'escalation_reason': reason,
        })
        self.ncr_id = ncr
        return ncr

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_("Observation and project companies "
                                        "disagree."))

class ConstructionNCR(models.Model):
    """A formal non-conformance: what was wrong, what is being done, who checked.

    An NCR never moves money. Where there is commercial impact it produces a
    **change event** and M4 takes it from there — which is why
    `estimated_rework_cost` is called exposure and not cost.
    """
    _name = 'realestate.construction.ncr'
    _description = 'Non-Conformance Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, discovery_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='NCR Number', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True)
    description = fields.Text(required=True)

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    package_id = fields.Many2one(
        'realestate.construction.contract.package', ondelete='set null',
        check_company=True)
    contractor_id = fields.Many2one('realestate.contractor', tracking=True)
    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', ondelete='set null',
        check_company=True)
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', ondelete='set null',
        check_company=True)
    location = fields.Char()
    discipline = fields.Selection(DISCIPLINES, default='other', index=True)

    source = fields.Selection([
        ('inspection', 'Inspection'),
        ('observation', 'Escalated Observation'),
        ('audit', 'Audit'),
        ('test', 'Test Result'),
        ('client', 'Client / Consultant'),
        ('other', 'Other'),
    ], default='inspection', required=True, index=True)
    source_inspection_id = fields.Many2one(
        'realestate.construction.inspection', ondelete='set null', index=True)
    source_observation_id = fields.Many2one(
        'realestate.construction.quality.observation', ondelete='set null')
    escalation_reason = fields.Char()

    requirement_violated = fields.Char(
        help="The clause, drawing note or specification that was not met.")
    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision', ondelete='restrict',
        help="The exact revision the requirement comes from.")
    specification_reference = fields.Char()

    discovery_date = fields.Date(
        default=fields.Date.context_today, required=True, index=True,
        tracking=True)
    raised_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user)
    assigned_to_id = fields.Many2one('res.users', tracking=True)
    severity = fields.Selection(SEVERITIES, default='major', required=True,
                                index=True, tracking=True)

    immediate_action = fields.Text()
    root_cause_category = fields.Selection(ROOT_CAUSES)
    root_cause = fields.Text()
    proposed_disposition = fields.Selection(NCR_DISPOSITIONS, tracking=True)
    disposition_approved_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False)
    disposition_approved_on = fields.Datetime(readonly=True, copy=False)
    corrective_action = fields.Text()
    preventive_action = fields.Text()

    target_completion_date = fields.Date(index=True)
    actual_completion_date = fields.Date(readonly=True, copy=False)
    verification_method = fields.Char()
    verified_by_id = fields.Many2one('res.users', readonly=True, copy=False,
                                     tracking=True)
    verified_date = fields.Date(readonly=True, copy=False)
    closure_date = fields.Date(readonly=True, copy=False)
    closure_time_days = fields.Integer(compute='_compute_closure',
                                       store=True)

    reopened_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    reopened_date = fields.Date(readonly=True, copy=False)
    reopen_reason = fields.Char(readonly=True, copy=False)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('under_investigation', 'Under Investigation'),
        ('disposition_proposed', 'Disposition Proposed'),
        ('corrective_action', 'Corrective Action'),
        ('ready_for_verification', 'Ready for Verification'),
        ('verified', 'Verified'),
        ('closed', 'Closed'),
        ('reopened', 'Reopened'),
        ('rejected', 'Rejected'),
        ('void', 'Void'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    cost_impact = fields.Selection(
        IMPACT_CLASSIFICATION, default='unknown', required=True, tracking=True)
    schedule_impact = fields.Selection(
        IMPACT_CLASSIFICATION, default='unknown', required=True)
    estimated_rework_cost = fields.Monetary(
        help="Quality exposure — an estimate of putting it right. Not actual "
             "cost: only Accounting posts that, and only M4 authorises a "
             "budget for it.")
    estimated_delay_days = fields.Integer()
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    reinspection_id = fields.Many2one(
        'realestate.construction.inspection', readonly=True, copy=False,
        string='Verification Inspection')
    is_overdue = fields.Boolean(compute='_compute_overdue', store=True,
                                index=True)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'construction_ncr_attachment_rel', 'ncr_id',
        'attachment_id', string='Evidence')

    OPEN_STATES = ('draft', 'open', 'under_investigation',
                   'disposition_proposed', 'corrective_action',
                   'ready_for_verification', 'reopened')

    @api.depends('discovery_date', 'closure_date')
    def _compute_closure(self):
        for rec in self:
            rec.closure_time_days = (
                (rec.closure_date - rec.discovery_date).days
                if rec.closure_date and rec.discovery_date else 0)

    @api.depends('target_completion_date', 'state')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                rec.target_completion_date
                and rec.target_completion_date < today
                and rec.state in rec.OPEN_STATES)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.ncr') or 'NCR/NEW'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_open(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft NCR can be opened."))
            rec.state = 'open'
        return True

    def action_investigate(self):
        for rec in self:
            if rec.state not in ('open', 'reopened'):
                raise UserError(_("Only an open NCR is investigated."))
            rec.state = 'under_investigation'
        return True

    def action_propose_disposition(self):
        for rec in self:
            if not rec.proposed_disposition:
                raise UserError(_(
                    "Propose what should be done about it."))
            if not rec.root_cause_category:
                raise UserError(_(
                    "A disposition without a root cause fixes this one and "
                    "nothing else."))
            rec._check_disposition_authority()
            rec.state = 'disposition_proposed'
        return True

    def action_approve_disposition(self):
        """Approving 'use as is' is deliberately not a formality."""
        for rec in self:
            if rec.state != 'disposition_proposed':
                raise UserError(_("There is no disposition to approve."))
            rec._check_disposition_authority(approving=True)
            rec.write({
                'state': 'corrective_action',
                'disposition_approved_by_id': self.env.user.id,
                'disposition_approved_on': fields.Datetime.now(),
            })
        return True

    def _check_disposition_authority(self, approving=False):
        """`use_as_is` and `concession` accept non-conforming work permanently.

        They need a QA/QC manager, and — unless configured otherwise — somebody
        other than the person who proposed it.
        """
        self.ensure_one()
        if self.proposed_disposition not in ('use_as_is', 'concession'):
            return True
        # Wave 15 — the canonical role. The legacy construction groups are
        # declared above this module; the Wave 12 bridge gives every legacy
        # manager this role, so the same people pass the check.
        if not self.env.user.has_group(
                'atmta_roles.group_construction_manager'):
            raise UserError(_(
                "Accepting non-conforming work as-is is a manager's decision, "
                "not a workflow step."))
        if approving:
            allow_self = self.env['ir.config_parameter'].sudo().get_param(
                'real_estate_construction.allow_self_approval', 'False')
            if allow_self not in ('True', 'true', '1') and \
                    self.create_uid == self.env.user and \
                    self.severity == 'critical':
                raise UserError(_(
                    "A critical non-conformance is not accepted as-is by the "
                    "person who raised it."))
        return True

    def action_ready_for_verification(self):
        for rec in self:
            if rec.state != 'corrective_action':
                raise UserError(_(
                    "Corrective action comes before verification."))
            if not rec.corrective_action:
                raise UserError(_(
                    "Record what was actually done before asking for "
                    "verification."))
            rec.write({'state': 'ready_for_verification',
                       'actual_completion_date':
                           fields.Date.context_today(rec)})
        return True

    def action_verify(self):
        """Verification is a separate act by a separate person."""
        for rec in self:
            if rec.state != 'ready_for_verification':
                raise UserError(_(
                    "There is nothing to verify yet."))
            allow_self = self.env['ir.config_parameter'].sudo().get_param(
                'real_estate_construction.allow_self_approval', 'False')
            if allow_self not in ('True', 'true', '1') \
                    and rec.severity in ('major', 'critical') \
                    and rec.assigned_to_id == self.env.user:
                raise UserError(_(
                    "The person who carried out the corrective work does not "
                    "verify their own %(severity)s non-conformance.",
                    severity=rec.severity))
            rec.write({
                'state': 'verified',
                'verified_by_id': self.env.user.id,
                'verified_date': fields.Date.context_today(rec),
            })
        return True

    def action_close(self):
        for rec in self:
            if rec.state != 'verified':
                raise UserError(_(
                    "An NCR closes after verification, not instead of it."))
            rec.write({'state': 'closed',
                       'closure_date': fields.Date.context_today(rec)})
        return True

    def action_reopen(self, reason=None):
        for rec in self:
            if rec.state not in ('closed', 'verified'):
                raise UserError(_("Only a closed NCR reopens."))
            if not reason:
                raise UserError(_(
                    "Reopening a closed non-conformance needs a reason — the "
                    "previous closure is evidence somebody signed."))
            rec.write({
                'state': 'reopened',
                'reopened_by_id': self.env.user.id,
                'reopened_date': fields.Date.context_today(rec),
                'reopen_reason': reason,
            })
        return True

    def action_open_reason_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reopen Non-Conformance'),
            'res_model': 'realestate.construction.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_mode': 'reopen_ncr', 'default_res_id': self.id},
        }

    def action_reject(self):
        for rec in self:
            if rec.state in ('closed', 'verified'):
                raise UserError(_("A verified NCR is part of the record."))
            rec.state = 'rejected'
        return True

    def action_void(self):
        for rec in self:
            if rec.state in ('closed', 'verified'):
                raise UserError(_("A verified NCR cannot be voided."))
            rec.state = 'void'
        return True

    def action_create_reinspection(self):
        """Verify the corrective work by inspecting it."""
        self.ensure_one()
        if self.reinspection_id:
            return self.reinspection_id
        source = self.source_inspection_id
        inspection = (source.action_create_reinspection() if source
                      else self.env['realestate.construction.inspection'].create({
                          'project_id': self.project_id.id,
                          'company_id': self.company_id.id,
                          'contractor_id': self.contractor_id.id or False,
                          'wbs_id': self.wbs_id.id or False,
                          'location': self.location,
                          'discipline': self.discipline,
                      }))
        self.reinspection_id = inspection
        return inspection

    def write(self, vals):
        """Closed evidence is not edited."""
        protected = {'description', 'root_cause', 'corrective_action',
                     'proposed_disposition', 'requirement_violated',
                     'severity', 'discovery_date'}
        if protected & set(vals):
            closed = self.filtered(lambda n: n.state in ('closed', 'verified'))
            if closed:
                raise UserError(_(
                    "%(refs)s have been verified or closed. Reopen with a "
                    "reason rather than editing the record somebody signed.",
                    refs=', '.join(closed.mapped('name'))))
        return super().write(vals)

    @api.model
    def quality_exposure(self, project):
        """Potential rework cost, for M3 to consider — never to consume.

        A cost controller may choose to put this into a forecast adjustment.
        Nothing here does it for them: an estimate that inserted itself into an
        approved ETC would be a quality record moving the forecast.
        """
        groups = self._read_group(
            [('project_id', '=', project.id),
             ('state', 'in', list(self.OPEN_STATES)),
             ('cost_impact', 'in', ('potential', 'unknown'))],
            aggregates=['estimated_rework_cost:sum', '__count'])
        amount, count = groups[0] if groups else (0.0, 0)
        return {'potential_rework_cost': amount or 0.0,
                'open_ncr_count': count or 0}

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "NCR %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))
