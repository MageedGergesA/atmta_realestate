# -*- coding: utf-8 -*-
"""Extensions of time — the only thing that moves a contract completion date.

The original completion date is what the parties agreed. It never moves. What
moves is the *current* date, and only when an extension has been determined by
somebody with authority and then implemented as a deliberate act.

Claimed days are not approved days, and a determined extension that nobody
implemented has not changed the contract yet.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ConstructionEOT(models.Model):
    _name = 'realestate.construction.eot'
    _description = 'Extension of Time'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'package_id, determination_date desc, id desc'
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
    package_id = fields.Many2one(
        'realestate.construction.contract.package', required=True,
        ondelete='cascade', check_company=True, index=True, tracking=True,
        domain="[('project_id', '=', project_id)]")
    claim_id = fields.Many2one(
        'realestate.construction.claim', ondelete='set null', index=True,
        check_company=True)
    delay_event_ids = fields.Many2many(
        'realestate.construction.delay.event', 'eot_delay_event_rel',
        'eot_id', 'event_id', string='Delay Events')

    #: Snapshotted at implementation, so the record shows what it moved from.
    original_completion_date = fields.Date(readonly=True, copy=False)
    completion_date_before = fields.Date(
        readonly=True, copy=False,
        help="The current completion date immediately before this extension "
             "was implemented.")
    completion_date_after = fields.Date(readonly=True, copy=False)

    claimed_days = fields.Float(tracking=True)
    assessed_days = fields.Float(
        tracking=True,
        groups='atmta_roles.group_construction_commercial_manager')
    determined_days = fields.Float(
        tracking=True,
        help="Granted days. Only these move the current completion date, and "
             "only once implemented.")
    effective_date = fields.Date(tracking=True)
    determination_date = fields.Date(tracking=True)
    authority_id = fields.Many2one('res.users', tracking=True)
    reason = fields.Html()

    analysis_method = fields.Selection([
        ('time_impact', 'Time Impact Analysis'),
        ('impacted_as_planned', 'Impacted As-Planned'),
        ('windows', 'Windows Analysis'),
        ('as_planned_vs_as_built', 'As-Planned vs As-Built'),
        ('retrospective', 'Retrospective Longest Path'),
        ('narrative', 'Schedule Narrative'),
        ('manual', 'Manual Assessment'),
        ('external', 'External Scheduler Determination'),
    ], help="Stored as a reference to how the conclusion was reached. ATMTA "
            "does not run delay analysis and does not schedule.")
    analysis_document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Analysis Document')
    schedule_reference = fields.Char(string='Programme Revision')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('assessed', 'Assessed'),
        ('determined', 'Determined'),
        ('implemented', 'Implemented'),
        ('rejected', 'Rejected'),
        ('withdrawn', 'Withdrawn'),
        ('superseded', 'Superseded'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    supersedes_id = fields.Many2one(
        'realestate.construction.eot', readonly=True, copy=False)
    superseded_by_id = fields.Many2one(
        'realestate.construction.eot', readonly=True, copy=False)
    is_correction = fields.Boolean(
        readonly=True, copy=False,
        help="A correcting record. Corrections may carry negative days; the "
             "original stays exactly as it was determined.")

    document_revision_id = fields.Many2one(
        'realestate.construction.document.revision',
        string='Determination Document')
    transmittal_id = fields.Many2one('realestate.construction.transmittal')
    notes = fields.Html()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.eot') or 'EOT/NEW'
        return super().create(vals_list)

    @api.constrains('determined_days', 'is_correction')
    def _check_days(self):
        for rec in self:
            if rec.determined_days < 0 and not rec.is_correction:
                raise ValidationError(_(
                    "Negative days belong on a correction record, so the "
                    "history shows an extension was reduced rather than "
                    "quietly shrinking one that was already granted."))

    @api.constrains('project_id', 'company_id', 'package_id')
    def _check_company_consistency(self):
        for rec in self:
            if rec.package_id.project_id != rec.project_id:
                raise ValidationError(_(
                    "%s extends a package on another project.") % rec.name)
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "EOT %(name)s is in %(company)s but its project is in "
                    "%(other)s.", name=rec.name,
                    company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    def write(self, vals):
        protected = {'determined_days', 'claimed_days', 'package_id',
                     'effective_date', 'determination_date'}
        if protected & set(vals) and not self.env.context.get('re_eot'):
            locked = self.filtered(lambda e: e.state == 'implemented')
            if locked:
                raise UserError(_(
                    "%(refs)s have been implemented and the contract date "
                    "reflects them. Issue a correcting extension rather than "
                    "editing one that already moved a completion date.",
                    refs=', '.join(locked.mapped('name'))))
        return super().write(vals)

    # ------------------------------------------------------------------
    def action_assess(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is past assessment.") % rec.name)
            rec.state = 'assessed'
        return True

    def action_determine(self):
        """Grant days. The contract date does not move yet."""
        for rec in self:
            if rec.state not in ('draft', 'assessed'):
                raise UserError(_(
                    "%s is not open for determination.") % rec.name)
            if not rec.reason:
                raise UserError(_(
                    "Record why the extension was granted or refused."))
            rec.write({
                'state': 'determined',
                'determination_date': rec.determination_date
                or fields.Date.context_today(rec),
                'authority_id': rec.authority_id.id or self.env.user.id,
            })
        return True

    def action_implement(self):
        """Move the current completion date. Idempotent, and audited."""
        for rec in self:
            if rec.state == 'implemented':
                continue
            if rec.state != 'determined':
                raise UserError(_(
                    "%s has not been determined.") % rec.name)
            package = rec.package_id
            if not package.original_completion_date:
                raise UserError(_(
                    "%(package)s has no original completion date. An "
                    "extension extends something; award the package first.",
                    package=package.display_name))
            self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)',
                                (package.id, 0))
            before = package.current_completion_date
            rec.write({
                'state': 'implemented',
                'original_completion_date': package.original_completion_date,
                'completion_date_before': before,
                'effective_date': rec.effective_date
                or fields.Date.context_today(rec),
            })
            # The package recomputes its current date from the implemented
            # extensions. Nothing here writes a date onto the contract.
            package.invalidate_recordset(
                ['approved_eot_days', 'current_completion_date'])
            rec.completion_date_after = package.current_completion_date
            package.message_post(body=_(
                "%(ref)s implemented: %(days)s day(s). Completion moves from "
                "%(before)s to %(after)s. Original remains %(original)s.",
                ref=rec.name, days=rec.determined_days, before=before,
                after=package.current_completion_date,
                original=package.original_completion_date))
        return True

    def action_reject(self):
        for rec in self:
            if rec.state == 'implemented':
                raise UserError(_(
                    "%s has already moved a contract date.") % rec.name)
            rec.state = 'rejected'
        return True

    def action_create_correction(self, days, reason=None):
        """Reduce or increase an implemented extension, on the record."""
        self.ensure_one()
        if self.state != 'implemented':
            raise UserError(_(
                "Corrections apply to extensions that were implemented."))
        correction = self.create({
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id,
            'claim_id': self.claim_id.id or False,
            'determined_days': days,
            'is_correction': True,
            'supersedes_id': self.id,
            'reason': reason or _("Correction of %s.") % self.name,
            'authority_id': self.env.user.id,
        })
        self.with_context(re_eot=True).superseded_by_id = correction.id
        return correction
