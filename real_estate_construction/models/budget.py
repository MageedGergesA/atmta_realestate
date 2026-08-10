# -*- coding: utf-8 -*-
"""M2 — the budget baseline.

### What this model is for

Phase 0 found three numbers called "budget" — `project.expected_budget`,
`Σ milestone.budget_amount` and `boq.total_amount` — none of them authoritative
and none reconciled to the others. This model is the answer to "what is the
budget", and it is the only answer:

```
    ORIGINAL BUDGET  +  APPROVED BUDGET CHANGES  =  CURRENT BUDGET
```

The three legacy numbers are **preserved and reclassified**, never overwritten
and never silently forced to agree — see `budget_migration.py`.

### Why the original is a separate number rather than an edit

A baseline that can be edited is not a baseline. `original_amount` is frozen at
baseline and `approved_change_amount` accumulates beside it, so the question
"what did we approve at the start, and what has been approved since" always has
an answer. M4 owns the workflow that produces changes; M2 owns the arithmetic
they land in, which is why `approved_change_amount` exists here and is zero
until M4 fills it.

### Financial basis

**Untaxed, always.** A budget is compared against commitments and actuals, and
those are read untaxed too, because a 15% VAT on a purchase order is not 15%
more building. Tax remains Accounting's business and is untouched.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

BUDGET_STATES = [
    ('draft', 'Draft'),
    ('review', 'Under Review'),
    ('approved', 'Approved'),
    ('baselined', 'Baselined'),
    ('superseded', 'Superseded'),
    ('cancelled', 'Cancelled'),
]

#: States in which the money on a budget may still be edited.
EDITABLE_STATES = ('draft', 'review')

#: Fields that carry money or scope. Frozen once a budget is baselined.
FROZEN_LINE_FIELDS = {
    'wbs_id', 'cost_code_id', 'quantity', 'unit_rate', 'original_amount',
    'amount_mode', 'uom_id', 'budget_id',
}
FROZEN_HEADER_FIELDS = {'project_id', 'company_id', 'currency_id', 'version'}


class ConstructionBudget(models.Model):
    """One approved statement of what a project is authorised to spend."""
    _name = 'realestate.construction.budget'
    _description = 'Construction Budget'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, version desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram',
    )
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True, tracking=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
        help="Budgets are held in one currency. Foreign-currency commitments "
             "are converted for control comparison, never re-accounted.",
    )
    version = fields.Integer(
        default=1, readonly=True, copy=False, tracking=True,
        help="Baselines are numbered. Revision 2 supersedes revision 1; "
             "revision 1 is still readable, which is the point.",
    )
    source = fields.Selection([
        ('manual', 'Prepared Manually'),
        ('boq', 'Generated from a BOQ'),
        ('legacy', 'Derived from Legacy Data'),
    ], default='manual', required=True, tracking=True)
    source_boq_id = fields.Many2one(
        'realestate.boq', string='Source BOQ', readonly=True, copy=False,
        help="The BOQ this budget was drafted from. A BOQ valuation is not "
             "automatically an approved budget — somebody reviewed it.",
    )
    superseded_by_id = fields.Many2one(
        'realestate.construction.budget', readonly=True, copy=False)
    supersedes_id = fields.Many2one(
        'realestate.construction.budget', readonly=True, copy=False)

    state = fields.Selection(
        BUDGET_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True,
    )
    baseline_date = fields.Datetime(readonly=True, copy=False, tracking=True)
    reviewed_by_id = fields.Many2one(
        'res.users', string='Reviewed By', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False,
        tracking=True)
    approved_on = fields.Datetime(readonly=True, copy=False)

    line_ids = fields.One2many(
        'realestate.construction.budget.line', 'budget_id',
        string='Budget Lines', copy=True)
    line_count = fields.Integer(compute='_compute_totals')

    original_amount = fields.Monetary(
        string='Original Budget', compute='_compute_totals', store=True,
        help="The sum of the baselined lines. Frozen once baselined.",
    )
    approved_change_amount = fields.Monetary(
        string='Approved Budget Changes', compute='_compute_totals',
        store=True,
        help="Approved changes since baseline. M4's change control writes "
             "these; until then they are zero, which is the truth.",
    )
    current_amount = fields.Monetary(
        string='Current Budget', compute='_compute_totals', store=True)
    contingency_amount = fields.Monetary(
        string='Contingency', compute='_compute_totals', store=True,
        help="The part of the budget held against cost codes marked as "
             "contingency — money set aside rather than allocated to scope.",
    )
    notes = fields.Html()

    _sql_constraints = [
        ('version_uniq_per_project',
         'unique(project_id, version)',
         'A project cannot have two budgets with the same version number.'),
    ]

    # ------------------------------------------------------------------
    def init(self):
        """At most one baselined budget per project, enforced by the database.

        A Python check would be a race: two people baselining two revisions at
        the same moment would both read "no baseline yet" and both write one.
        The partial unique index cannot be raced, and `action_baseline()` also
        takes an advisory lock so the loser gets a sentence rather than a
        constraint traceback.
        """
        super().init()
        self._cr.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_class
                               WHERE relname = 'construction_budget_one_baseline') THEN
                    CREATE UNIQUE INDEX construction_budget_one_baseline
                        ON realestate_construction_budget (project_id)
                     WHERE state = 'baselined';
                END IF;
            END $$;
        """)

    @api.depends('line_ids.original_amount', 'line_ids.approved_change_amount',
                 'line_ids.is_contingency')
    def _compute_totals(self):
        for rec in self:
            lines = rec.line_ids
            rec.line_count = len(lines)
            rec.original_amount = sum(lines.mapped('original_amount'))
            rec.approved_change_amount = sum(
                lines.mapped('approved_change_amount'))
            rec.current_amount = (
                rec.original_amount + rec.approved_change_amount)
            rec.contingency_amount = sum(
                line.original_amount + line.approved_change_amount
                for line in lines if line.is_contingency)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.budget') or 'BUD/NEW'
            # Version numbers are assigned, not typed. A project may carry
            # several drafts at once — an alternative proposal, a revision in
            # preparation — and they must not collide on version 1.
            if not vals.get('version') and vals.get('project_id'):
                last = self.search(
                    [('project_id', '=', vals['project_id'])],
                    order='version desc', limit=1)
                vals['version'] = (last.version + 1) if last else 1
        return super().create(vals_list)

    def write(self, vals):
        """A baselined budget does not change its mind.

        Rule 3: approved financial history is represented by revisions and
        change orders, never by editing the approved record. State transitions
        and the bookkeeping around them are still allowed, because superseding
        a baseline *is* the sanctioned way to replace it.
        """
        protected = FROZEN_HEADER_FIELDS & set(vals)
        if protected:
            frozen = self.filtered(
                lambda b: b.state in ('baselined', 'superseded'))
            if frozen:
                raise UserError(_(
                    "%(fields)s cannot change on a baselined budget "
                    "(%(names)s). Create a revision instead — the approved "
                    "figure is the record of a decision, not a draft.",
                    fields=', '.join(sorted(protected)),
                    names=', '.join(frozen.mapped('name'))))
        return super().write(vals)

    def unlink(self):
        if any(rec.state in ('baselined', 'superseded') for rec in self):
            raise UserError(_(
                "A baselined budget cannot be deleted. Supersede it with a "
                "revision; the history is the reason it exists."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft budget can be submitted."))
            if not rec.line_ids:
                raise UserError(_(
                    "A budget with no lines authorises nothing."))
            rec.state = 'review'
            rec.reviewed_by_id = self.env.user
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != 'review':
                raise UserError(_(
                    "Only a budget under review can be approved."))
            rec._check_approver()
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })
        return True

    def action_baseline(self):
        """Make this the project's authoritative current budget.

        The advisory lock is on the *project*, not the budget: the thing being
        protected is "this project has exactly one baseline", and two different
        budget records racing to become it is precisely the case a row lock on
        either one would miss.
        """
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_(
                    "Only an approved budget can be baselined."))
            rec._lock_project()

            existing = self.search([
                ('project_id', '=', rec.project_id.id),
                ('state', '=', 'baselined'),
                ('id', '!=', rec.id),
            ], limit=1)
            if existing:
                raise UserError(_(
                    "%(project)s is already baselined on %(budget)s. Supersede "
                    "that baseline rather than adding a second one — two "
                    "current budgets is the same as none.",
                    project=rec.project_id.display_name,
                    budget=existing.name))

            rec.write({
                'state': 'baselined',
                'baseline_date': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                "Baselined: original budget %(amount)s.",
                amount=rec.currency_id.format(rec.original_amount)))
        return True

    def action_create_revision(self):
        """A new draft carrying this baseline's lines, ready to be changed."""
        self.ensure_one()
        if self.state != 'baselined':
            raise UserError(_(
                "Only the current baseline can be revised."))
        revision = self.copy({
            'name': _('New'),
            'version': self.version + 1,
            'state': 'draft',
            'supersedes_id': self.id,
            'baseline_date': False,
            'approved_by_id': False,
            'approved_on': False,
            'reviewed_by_id': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': revision.id,
            'view_mode': 'form',
        }

    def action_supersede(self):
        """Retire this baseline in favour of an approved revision."""
        self.ensure_one()
        if self.state != 'baselined':
            raise UserError(_("Only a baseline can be superseded."))
        successor = self.search([
            ('supersedes_id', '=', self.id),
            ('state', '=', 'approved'),
        ], limit=1)
        if not successor:
            raise UserError(_(
                "Approve the revision first. A baseline is not withdrawn "
                "until there is something to put in its place."))
        self._lock_project()
        self.write({'state': 'superseded', 'superseded_by_id': successor.id})
        successor.action_baseline()
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in ('baselined', 'superseded'):
                raise UserError(_(
                    "A baselined budget cannot be cancelled — it is what the "
                    "project was authorised to spend."))
            rec.state = 'cancelled'
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('review', 'approved', 'cancelled'):
                raise UserError(_(
                    "A baselined budget cannot be reopened."))
            rec.write({'state': 'draft', 'approved_by_id': False,
                       'approved_on': False})
        return True

    # ------------------------------------------------------------------
    def _lock_project(self):
        """Serialise baseline changes for one project.

        `pg_advisory_xact_lock` releases with the transaction, so a crash
        cannot leave a project permanently unbaselinable. The same pattern
        Developer uses for reservations, for the same reason.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (hash('re.construction.budget') % 2147483647, self.project_id.id))

    def _check_approver(self):
        """Approval is a separate authority from preparation.

        Maker/checker: whoever prepared a budget should not be the person who
        approves it. Configurable, because a two-person company cannot obey it
        and would otherwise be unable to baseline anything at all.
        """
        self.ensure_one()
        allow_self = self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_construction.allow_self_approval', 'False')
        if allow_self in ('True', 'true', '1'):
            return True
        if self.create_uid == self.env.user and not self.env.user.has_group(
                'real_estate_construction.group_construction_manager'):
            raise UserError(_(
                "A budget is approved by somebody other than the person who "
                "prepared it. Ask a Commercial Manager, or set "
                "`real_estate_construction.allow_self_approval` if this "
                "company genuinely has one person doing both."))
        return True

    # ------------------------------------------------------------------
    @api.constrains('project_id', 'company_id')
    def _check_company_matches_project(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Budget %(name)s is in %(company)s but %(project)s belongs "
                    "to %(project_company)s.",
                    name=rec.name, company=rec.company_id.display_name,
                    project=rec.project_id.display_name,
                    project_company=rec.project_id.company_id.display_name))

    # ------------------------------------------------------------------
    @api.model
    def current_for(self, project):
        """The project's authoritative budget, or an empty recordset."""
        return self.search([
            ('project_id', '=', project.id),
            ('state', '=', 'baselined'),
        ], limit=1)


class ConstructionBudgetLine(models.Model):
    """One authorised amount, addressed at (WBS × cost code)."""
    _name = 'realestate.construction.budget.line'
    _description = 'Construction Budget Line'
    _order = 'budget_id, wbs_id, cost_code_id, id'
    _check_company_auto = True

    budget_id = fields.Many2one(
        'realestate.construction.budget', required=True, ondelete='cascade',
        index=True)
    project_id = fields.Many2one(
        related='budget_id.project_id', store=True, index=True, readonly=True)
    company_id = fields.Many2one(
        related='budget_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='budget_id.currency_id', store=True, readonly=True)
    state = fields.Selection(
        related='budget_id.state', store=True, readonly=True, index=True)

    wbs_id = fields.Many2one(
        'realestate.construction.wbs', string='WBS', index=True,
        ondelete='restrict', check_company=True,
        domain="[('project_id', '=', project_id)]",
        help="Where in the works. Optional for a project-level allowance.",
    )
    cost_code_id = fields.Many2one(
        'realestate.construction.cost.code', string='Cost Code',
        required=True, index=True, ondelete='restrict', check_company=True,
        help="What kind of money. Required — an amount nobody can classify "
             "cannot be controlled.",
    )
    is_contingency = fields.Boolean(
        related='cost_code_id.is_contingency', store=True, readonly=True)
    description = fields.Char()

    amount_mode = fields.Selection([
        ('quantity', 'Quantity × Rate'),
        ('lumpsum', 'Lump Sum'),
    ], default='quantity', required=True,
        help="Not every cost has a sensible quantity. Forcing one produces "
             "a rate of 1 × the amount, which teaches nobody anything.",
    )
    quantity = fields.Float(default=0.0)
    uom_id = fields.Many2one('uom.uom', string='UoM')
    unit_rate = fields.Monetary(default=0.0)
    original_amount = fields.Monetary(
        string='Original Amount', compute='_compute_original_amount',
        store=True, readonly=False,
        help="Quantity × rate in quantity mode; typed directly for a lump "
             "sum.",
    )
    # No `default=` on purpose: a default on an editable computed field means
    # every `create()` supplies a value, so the compute never runs and every
    # quantity line lands as zero.
    change_line_ids = fields.One2many(
        'realestate.construction.budget.change.line', 'budget_line_id',
        string='Approved Changes', readonly=True)
    approved_change_amount = fields.Monetary(
        string='Approved Changes', compute='_compute_approved_change',
        store=True, readonly=True, copy=False,
        help="The sum of implemented budget changes against this cost code. "
             "A derived number, not a typed one — every unit of it points at "
             "the change order that authorised it.",
    )

    @api.depends('change_line_ids.amount')
    def _compute_approved_change(self):
        for line in self:
            line.approved_change_amount = sum(
                line.change_line_ids.mapped('amount'))
    current_amount = fields.Monetary(
        string='Current Amount', compute='_compute_current_amount',
        store=True)
    notes = fields.Char()

    @api.depends('amount_mode', 'quantity', 'unit_rate')
    def _compute_original_amount(self):
        for line in self:
            if line.amount_mode == 'quantity':
                line.original_amount = line.quantity * line.unit_rate
            else:
                # Lump sum: whatever was typed, including nothing yet.
                line.original_amount = line.original_amount or 0.0

    @api.depends('original_amount', 'approved_change_amount')
    def _compute_current_amount(self):
        for line in self:
            line.current_amount = (
                line.original_amount + line.approved_change_amount)

    def write(self, vals):
        protected = FROZEN_LINE_FIELDS & set(vals)
        if protected:
            frozen = self.filtered(
                lambda l: l.budget_id.state in ('baselined', 'superseded'))
            if frozen:
                raise UserError(_(
                    "The money on a baselined budget cannot be edited. "
                    "Create a revision, or record an approved change — "
                    "'change 5M to 6M' on an approved line leaves no record "
                    "that it was ever 5M."))
        return super().write(vals)

    def unlink(self):
        if any(l.budget_id.state in ('baselined', 'superseded') for l in self):
            raise UserError(_(
                "A line cannot be removed from a baselined budget."))
        return super().unlink()

    @api.constrains('wbs_id', 'project_id')
    def _check_wbs_project(self):
        for line in self:
            if line.wbs_id and line.wbs_id.project_id != line.project_id:
                raise ValidationError(_(
                    "WBS %(wbs)s belongs to another project.",
                    wbs=line.wbs_id.display_name))

    @api.constrains('cost_code_id', 'project_id')
    def _check_cost_code_project(self):
        for line in self:
            code = line.cost_code_id
            if code.project_id and code.project_id != line.project_id:
                raise ValidationError(_(
                    "Cost code %(code)s is scoped to another project.",
                    code=code.display_name))
