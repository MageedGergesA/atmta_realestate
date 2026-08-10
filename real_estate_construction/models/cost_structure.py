# -*- coding: utf-8 -*-
"""M1 — the cost breakdown structure.

### Two hierarchies, not one

The brief is explicit that scope and cost category must not be mixed in one
field, and the reason is that they answer different questions:

```
    WBS        "where in the works is this?"     03 Concrete → 03.02 Slabs
    COST CODE  "what kind of money is this?"     labour / material / subcontract
```

A slab has labour and concrete in it; labour appears in slabs and in masonry.
Neither is a parent of the other, and a single tree that tried to be both would
force every project to choose which question it could answer.

So: `realestate.construction.wbs` is a per-project tree of scope, and
`realestate.construction.cost.code` is a catalogue of cost categories. Budget,
commitment and actual are all addressed at the **intersection** of the two.

### Why the cost code is a catalogue and the WBS is not

Scope is inherently per project — nobody's "03.02 Ground Floor Slabs" means
anything on another site. Cost codes are the opposite: an organisation wants
"SUB-CIV — Civil Subcontract" to mean the same thing on every project, or it
cannot compare them.

That difference is also what keeps the analytic architecture affordable. A
catalogue of ~100 cost codes needs ~100 analytic accounts *in total*; one
account per cost code per project would need 100 × every project, forever. The
brief warns against creating hundreds of analytic accounts without testing
scale, and this is how that warning is answered: two plans, crossed at posting
time, rather than one plan multiplied out.

```
    Analytic Plan "Real Estate Projects"    → one account per project
    Analytic Plan "Construction Cost Codes" → one account per cost code

    a posting carries one from each:
        {project_account: 100%, cost_code_account: 100%}
```

Odoo validates distributions per plan, so both can be 100% at once, and the
ledger becomes cross-tabbable by project × cost code without a second
accounting dimension of our own — which is what Rule 4 requires.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: The cost categories money can fall into. Deliberately *not* a scope concept.
COST_CATEGORY = [
    ('labor', 'Labour'),
    ('material', 'Material'),
    ('equipment', 'Equipment'),
    ('subcontract', 'Subcontract'),
    ('professional', 'Professional Fees'),
    ('permit', 'Permits & Authorities'),
    ('overhead', 'Overhead & Preliminaries'),
    ('contingency', 'Contingency'),
    ('other', 'Other'),
]

#: XML ids of the two analytic plans this module relies on.
PROJECT_PLAN_XMLID = 'real_estate_construction.analytic_plan_re_projects'
COST_CODE_PLAN_XMLID = 'real_estate_construction.analytic_plan_cost_codes'


class ConstructionWBS(models.Model):
    """Where in the works — a per-project scope tree.

    Depth is not fixed: a villa compound may stop at two levels and a tower may
    want four. What is fixed is that a WBS node belongs to exactly one project
    and cannot be re-parented under another one, because a cost already booked
    against it would silently move projects if it could.
    """
    _name = 'realestate.construction.wbs'
    _description = 'Construction WBS Node'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'complete_code, id'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Short code, unique among siblings — '03', '02'. The full path "
             "is built from these.",
    )
    complete_code = fields.Char(
        string='WBS Code', compute='_compute_complete_code', store=True,
        index=True, recursive=True,
        help="Dotted path from the root: 03.02.01.",
    )
    display_name = fields.Char(compute='_compute_display_name', store=False)

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        'res.company', related='project_id.company_id',
        store=True, index=True, readonly=True,
    )
    phase_id = fields.Many2one(
        'realestate.phase', ondelete='set null',
        domain="[('project_id', '=', project_id)]",
        help="Optional. A WBS node may belong to a phase of the project.",
    )
    parent_id = fields.Many2one(
        'realestate.construction.wbs', string='Parent',
        ondelete='cascade', index=True,
        domain="[('project_id', '=', project_id), ('id', '!=', id)]",
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many(
        'realestate.construction.wbs', 'parent_id', string='Children')
    sequence = fields.Integer(default=10)
    level = fields.Integer(compute='_compute_level', store=True, recursive=True)
    is_leaf = fields.Boolean(compute='_compute_is_leaf', store=True)

    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_uniq_per_parent',
         'unique(project_id, parent_id, code)',
         'A WBS code must be unique among its siblings on the same project.'),
    ]

    @api.depends('code', 'parent_id.complete_code')
    def _compute_complete_code(self):
        for rec in self:
            parent_code = rec.parent_id.complete_code
            rec.complete_code = (
                '%s.%s' % (parent_code, rec.code or '')
                if parent_code else (rec.code or ''))

    @api.depends('complete_code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = ' '.join(
                part for part in (rec.complete_code, rec.name) if part)

    @api.depends('parent_id.level')
    def _compute_level(self):
        for rec in self:
            rec.level = (rec.parent_id.level + 1) if rec.parent_id else 0

    @api.depends('child_ids')
    def _compute_is_leaf(self):
        for rec in self:
            rec.is_leaf = not rec.child_ids

    @api.constrains('parent_id')
    def _check_parent_recursion(self):
        # `_has_cycle()` since 18.0; `_check_recursion()` is deprecated.
        if self._has_cycle():
            raise ValidationError(_(
                "A WBS node cannot be its own ancestor."))

    @api.constrains('parent_id', 'project_id')
    def _check_parent_same_project(self):
        """A node and its parent belong to the same project, always.

        Without this a cost booked under 03.02 could change project by having
        its parent re-pointed, which is the sort of thing nobody notices until
        a cost report is wrong and no record says why.
        """
        for rec in self:
            if rec.parent_id and rec.parent_id.project_id != rec.project_id:
                raise ValidationError(_(
                    "WBS node '%(child)s' belongs to %(child_project)s but its "
                    "parent belongs to %(parent_project)s.",
                    child=rec.name,
                    child_project=rec.project_id.display_name,
                    parent_project=rec.parent_id.project_id.display_name))


class ConstructionCostCode(models.Model):
    """What kind of money — a catalogue shared across projects.

    Shared on purpose: a cost code that means something different on each
    project cannot be used to compare projects, which is most of the reason to
    have cost codes at all. Where a project genuinely needs one of its own,
    `project_id` may be set and the code is then scoped to it.
    """
    _name = 'realestate.construction.cost.code'
    _description = 'Construction Cost Code'
    _order = 'code, id'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    display_name = fields.Char(compute='_compute_display_name', store=False)

    category = fields.Selection(
        COST_CATEGORY, required=True, default='other', index=True,
        help="The kind of money. Scope lives on the WBS, not here.",
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    project_id = fields.Many2one(
        'realestate.project', ondelete='cascade', index=True,
        check_company=True,
        help="Leave empty for a catalogue code available to every project. "
             "Set it only for a code that genuinely belongs to one project.",
    )
    parent_id = fields.Many2one(
        'realestate.construction.cost.code', string='Roll-up',
        ondelete='restrict',
        help="Optional grouping for reporting — 'SUB' over 'SUB-CIV', "
             "'SUB-MEP'. Budgets and costs are booked on the leaf.",
    )
    child_ids = fields.One2many(
        'realestate.construction.cost.code', 'parent_id', string='Sub-codes')

    is_contingency = fields.Boolean(
        help="Money set aside rather than allocated to scope. Drawing on it is "
             "a decision that is recorded, not a budget line that quietly "
             "grows.",
    )
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account',
        copy=False, ondelete='restrict', check_company=True,
        help="The cost-code side of every posting. Created on demand in the "
             "Construction Cost Codes plan.",
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

    def init(self):
        """Uniqueness that survives NULLs.

        `unique(company_id, project_id, code)` looks right and is not: Postgres
        treats NULLs as distinct, so every catalogue code — the ones with no
        project — escaped it, and 'SUB-CIV' could be created any number of
        times. Two partial indexes say what was actually meant.
        """
        super().init()
        self._cr.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class WHERE relname =
                        'construction_cost_code_catalogue_uniq_u') THEN
                    CREATE UNIQUE INDEX construction_cost_code_catalogue_uniq_u
                        ON realestate_construction_cost_code (company_id, code)
                     WHERE project_id IS NULL;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class WHERE relname =
                        'construction_cost_code_project_uniq_u') THEN
                    CREATE UNIQUE INDEX construction_cost_code_project_uniq_u
                        ON realestate_construction_cost_code
                           (company_id, project_id, code)
                     WHERE project_id IS NOT NULL;
                END IF;
            END $$;
        """)

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = ' — '.join(
                part for part in (rec.code, rec.name) if part)

    @api.constrains('parent_id')
    def _check_parent_recursion(self):
        if self._has_cycle():
            raise ValidationError(_("A cost code cannot roll up into itself."))

    @api.constrains('project_id', 'company_id')
    def _check_project_company(self):
        for rec in self:
            if rec.project_id and rec.project_id.company_id \
                    and rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Cost code %(code)s belongs to %(company)s but is scoped "
                    "to a project in %(project_company)s.",
                    code=rec.code, company=rec.company_id.display_name,
                    project_company=rec.project_id.company_id.display_name))

    # ------------------------------------------------------------------
    # Analytic
    # ------------------------------------------------------------------
    def _get_or_create_analytic_account(self):
        """The cost-code analytic account, made on demand.

        On demand rather than on create: a catalogue of codes that a company
        never books against should not litter the chart of analytic accounts.
        """
        self.ensure_one()
        if self.analytic_account_id:
            return self.analytic_account_id
        plan = self.env['realestate.construction.analytic']._cost_code_plan()
        account = self.env['account.analytic.account'].sudo().create({
            'name': self.display_name,
            'code': self.code,
            'plan_id': plan.id,
            'company_id': self.company_id.id,
        })
        self.analytic_account_id = account
        return account

    def _analytic_accounts(self):
        """The analytic accounts for this set of codes, made if missing."""
        accounts = self.env['account.analytic.account']
        for rec in self:
            accounts |= rec._get_or_create_analytic_account()
        return accounts

    def action_generate_analytic_account(self):
        for rec in self:
            rec._get_or_create_analytic_account()
        return True
