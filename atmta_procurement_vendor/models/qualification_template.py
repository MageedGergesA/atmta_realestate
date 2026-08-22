# -*- coding: utf-8 -*-
"""M4B–M4D — what we ask a vendor, grouped, weighted and versioned.

Three models, in increasing specificity:

```
    AREA          a heading — Legal, Financial, HSE, Technical …
    REQUIREMENT   one question inside an area, on one template
    TEMPLATE      the questionnaire for one kind of supplier
```

### Why the template is versioned rather than edited

A qualification is an assertion about a moment: *given these questions, this
vendor passed*. If the questions can be edited afterwards, that assertion
quietly changes meaning — a vendor approved under a template that did not ask
for insurance appears, a year later, to have been approved with insurance on
file. So a template that has been used by anything past draft is frozen, and
`action_new_version()` produces the next one. Old assessments keep pointing at
the version they were actually assessed under, and every requirement is
additionally **snapshotted onto the response**, so even the frozen template is
not load-bearing for reading history.

### Why obligation and blocking are two fields

`mandatory` says the question must be answered. `blocking` says failing it
ends the matter regardless of the total score. They are different powers and
conflating them is how an 85%-scoring vendor with no valid insurance gets
approved.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: What kind of answer a requirement takes.
REQUIREMENT_TYPE = [
    ('boolean', 'Yes / No'),
    ('document', 'Document'),
    ('date', 'Date / Expiry'),
    ('number', 'Numeric Value'),
    ('selection', 'Selection'),
    ('score', 'Score'),
    ('text', 'Text Evidence'),
]

#: How much authority the answer carries.
OBLIGATION = [
    ('mandatory', 'Mandatory'),
    ('scored', 'Scored'),
    ('informational', 'Informational'),
]

TEMPLATE_STATE = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('obsolete', 'Obsolete'),
]


class QualificationArea(models.Model):
    _name = 'realestate.procurement.qualification.area'
    _description = 'Vendor Qualification Area'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company',
        help="Empty means every company may use this heading.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(translate=True)
    category_ids = fields.Many2many(
        'realestate.procurement.vendor.category',
        relation='procurement_qual_area_trade_rel',
        column1='area_id', column2='trade_id',
        string='Applies To Trades',
        help="Empty means the heading is relevant to any trade. This is "
             "guidance for whoever builds a template — it grants nothing.")
    confidential = fields.Boolean(
        string='Confidential Evidence',
        help="Answers and documents under this heading are restricted to "
             "assessors, approvers and managers. Financial statements and "
             "reference checks are the usual reasons.")

    _sql_constraints = [
        ('code_company_uniq', 'unique(code, company_id)',
         'An area code must be unique within its company.'),
    ]


class QualificationTemplate(models.Model):
    _name = 'realestate.procurement.qualification.template'
    _description = 'Vendor Qualification Template'
    _order = 'name, version desc'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    version = fields.Integer(default=1, required=True, readonly=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    state = fields.Selection(TEMPLATE_STATE, default='draft', required=True,
                             readonly=True, copy=False)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(translate=True)

    category_ids = fields.Many2many(
        'realestate.procurement.vendor.category',
        relation='procurement_qual_template_trade_rel',
        column1='template_id', column2='trade_id',
        string='Trades',
        help="The trades this questionnaire is written for. Empty means it "
             "may be used for any of them.")
    partner_category_ids = fields.Many2many(
        'res.partner.category',
        relation='procurement_qual_template_partner_categ_rel',
        column1='template_id', column2='categ_id',
        string='Vendor Types',
        help="Optional narrowing by the partner tags this suite already uses "
             "(Contractor, Material Supplier, Consultant …).")

    requirement_ids = fields.One2many(
        'realestate.procurement.qualification.requirement', 'template_id',
        string='Requirements', copy=True)
    requirement_count = fields.Integer(compute='_compute_counts')
    qualification_ids = fields.One2many(
        'realestate.procurement.vendor.qualification', 'template_id',
        string='Assessments')
    qualification_count = fields.Integer(compute='_compute_counts')

    scoring_enabled = fields.Boolean(
        string='Scored',
        help="Off means pass/fail: mandatory requirements decide the result "
             "and no percentage is computed. Not every supplier needs a "
             "number.")
    min_score = fields.Float(
        string='Minimum Score (%)', default=70.0,
        help="The total a scored assessment must reach. Blocking "
             "requirements are checked separately and outrank it.")
    validity_months = fields.Integer(
        string='Default Validity (months)', default=12,
        help="Used to propose an expiry date. Zero proposes none — an "
             "assessment with no expiry is a deliberate choice, not an "
             "accident of configuration.")

    previous_version_id = fields.Many2one(
        'realestate.procurement.qualification.template',
        string='Previous Version', readonly=True, copy=False)
    next_version_id = fields.Many2one(
        'realestate.procurement.qualification.template',
        string='Next Version', readonly=True, copy=False)

    _sql_constraints = [
        ('code_version_uniq', 'unique(code, version, company_id)',
         'A template code and version must be unique within the company.'),
    ]

    @api.depends('requirement_ids', 'qualification_ids')
    def _compute_counts(self):
        for rec in self:
            rec.requirement_count = len(rec.requirement_ids)
            rec.qualification_count = len(rec.qualification_ids)

    @api.constrains('min_score')
    def _check_min_score(self):
        for rec in self:
            if not 0.0 <= rec.min_score <= 100.0:
                raise ValidationError(_(
                    "A minimum score is a percentage between 0 and 100."))

    # ------------------------------------------------------------------
    def _is_frozen(self):
        """Has this version been used for anything that must keep its meaning?"""
        self.ensure_one()
        return bool(self.qualification_ids.filtered(
            lambda q: q.state != 'draft'))

    def action_activate(self):
        for rec in self:
            if not rec.requirement_ids:
                raise UserError(_(
                    "%s asks nothing. Activate it once it has at least one "
                    "requirement, or leave it in draft.") % rec.display_name)
            rec.state = 'active'

    def action_obsolete(self):
        self.state = 'obsolete'

    def action_new_version(self):
        """Copy forward. The old version keeps saying what it said."""
        self.ensure_one()
        if self.next_version_id:
            raise UserError(_(
                "%s already has a later version (%s).") % (
                    self.display_name, self.next_version_id.display_name))
        new = self.copy({
            'version': self.version + 1,
            'state': 'draft',
            'previous_version_id': self.id,
        })
        self.next_version_id = new.id
        self.state = 'obsolete'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': new.id,
            'view_mode': 'form',
        }

    def copy(self, default=None):
        default = dict(default or {})
        default.setdefault('code', self.code)
        default.setdefault('version', self.version + 1)
        default.setdefault('state', 'draft')
        return super().copy(default)

    def write(self, vals):
        """A used template may be renamed and retired, never re-asked.

        Everything that changes what the questionnaire *means* is refused
        once an assessment past draft exists. `action_new_version()` is the
        supported way forward and it is one click.
        """
        meaning = {'requirement_ids', 'scoring_enabled', 'min_score',
                   'category_ids', 'partner_category_ids'}
        if meaning & set(vals) and not self.env.context.get(
                're_template_versioning'):
            for rec in self:
                if rec._is_frozen():
                    raise UserError(_(
                        "%(name)s has been used for %(count)s assessment(s), "
                        "so changing what it asks would change what those "
                        "assessments meant. Use New Version — the existing "
                        "ones keep pointing at this one.",
                        name=rec.display_name,
                        count=len(rec.qualification_ids.filtered(
                            lambda q: q.state != 'draft'))))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.qualification_ids:
                raise UserError(_(
                    "%s has assessments behind it. Archive it instead — "
                    "deleting it would leave them describing a questionnaire "
                    "that no longer exists.") % rec.display_name)
        return super().unlink()


class QualificationRequirement(models.Model):
    _name = 'realestate.procurement.qualification.requirement'
    _description = 'Vendor Qualification Requirement'
    _order = 'sequence, id'

    template_id = fields.Many2one(
        'realestate.procurement.qualification.template', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='template_id.company_id', store=True, index=True)
    area_id = fields.Many2one(
        'realestate.procurement.qualification.area', string='Area',
        ondelete='restrict')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True, string='Requirement')
    code = fields.Char(
        help="Optional stable key. Useful when a later template version wants "
             "to be comparable with an earlier one.")
    description = fields.Text(translate=True, string='Guidance')

    requirement_type = fields.Selection(
        REQUIREMENT_TYPE, default='boolean', required=True, string='Answer Type')
    selection_options = fields.Char(
        string='Options',
        help="Comma-separated, for a Selection requirement.")
    obligation = fields.Selection(
        OBLIGATION, default='mandatory', required=True,
        help="Mandatory must be answered and passed. Scored contributes to "
             "the total. Informational is evidence and nothing else.")
    blocking = fields.Boolean(
        help="Failing this ends the assessment as Not Qualified whatever the "
             "score says. Insurance and manufacturer authorisation are the "
             "usual cases.")
    expiry_sensitive = fields.Boolean(
        string='Expires',
        help="The document or date behind this requirement stops being valid "
             "on its own expiry, independently of the assessment's. Only "
             "requirements marked here are re-checked at eligibility time.")
    confidential = fields.Boolean(
        compute='_compute_confidential', store=True, readonly=False,
        help="Restricts the answer and its documents to assessors and above. "
             "Defaults to whatever the area says and may be overridden.")

    weight = fields.Float(
        default=1.0,
        help="Relative weight within the total. Only used when the template "
             "is scored.")
    max_score = fields.Float(
        default=10.0, string='Maximum Score',
        help="The score a perfect answer earns.")
    threshold = fields.Float(
        string='Minimum Value',
        help="For numeric requirements: the value the vendor must reach — "
             "years of experience, turnover, crew size.")

    @api.depends('area_id.confidential')
    def _compute_confidential(self):
        for rec in self:
            rec.confidential = rec.area_id.confidential

    @api.constrains('obligation', 'blocking')
    def _check_blocking_is_decidable(self):
        for rec in self:
            if rec.blocking and rec.obligation == 'informational':
                raise ValidationError(_(
                    "'%s' is informational, so it has no pass or fail to "
                    "block on. Make it mandatory or scored first.")
                    % rec.name)

    @api.constrains('requirement_type', 'selection_options')
    def _check_selection_options(self):
        for rec in self:
            if rec.requirement_type == 'selection' and not (
                    rec.selection_options or '').strip():
                raise ValidationError(_(
                    "'%s' is a selection with nothing to select.") % rec.name)

    @api.constrains('max_score', 'weight')
    def _check_scoring_numbers(self):
        for rec in self:
            if rec.max_score < 0 or rec.weight < 0:
                raise ValidationError(_(
                    "Scores and weights cannot be negative."))

    def _frozen_guard(self, verb):
        """The template's freeze, enforced where the rows actually live.

        Guarding only `template.write()` leaves the obvious way round it
        open: edit or delete the requirement records directly and a frozen
        questionnaire quietly changes what it asked. The context key is the
        versioning machinery's own door, and it is the only one.
        """
        if self.env.context.get('re_template_versioning'):
            return
        for rec in self:
            if rec.template_id._is_frozen():
                raise UserError(_(
                    "'%(name)s' belongs to %(template)s, which has been used "
                    "for assessments. It cannot be %(verb)s — take a new "
                    "version of the template instead.",
                    name=rec.name, template=rec.template_id.display_name,
                    verb=verb))

    def write(self, vals):
        self._frozen_guard(_('changed'))
        return super().write(vals)

    def unlink(self):
        self._frozen_guard(_('removed'))
        return super().unlink()

    def _snapshot(self):
        """The values a response copies so it can be read without the template."""
        self.ensure_one()
        return {
            'requirement_id': self.id,
            'requirement_name': self.name,
            'requirement_code': self.code or '',
            'requirement_type': self.requirement_type,
            'obligation': self.obligation,
            'blocking': self.blocking,
            'expiry_sensitive': self.expiry_sensitive,
            'confidential': self.confidential,
            'weight': self.weight,
            'max_score': self.max_score,
            'threshold': self.threshold,
            'selection_options': self.selection_options,
            'area_id': self.area_id.id,
            'area_name': self.area_id.name or '',
            'sequence': self.sequence,
        }
