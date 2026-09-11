# -*- coding: utf-8 -*-
"""M2 — classifying the three legacy budgets.

Phase 0 found three numbers called "budget", never reconciled:

```
    project.expected_budget      a scalar on the project (Developer)
    Σ milestone.budget_amount    planning allocation
    boq.total_amount             a valuation of quantities
```

None is promoted silently. This service **reports** what each project has and
what could be baselined from it; a human decides, exactly as the commission
migration in Module 4 did. Nothing here writes a budget.

```
    DETERMINISTIC   one source, or several that agree → safe to propose
    AMBIGUOUS       sources disagree materially       → a person must choose
    NO_SOURCE       nothing to derive a budget from
    LEGACY_ONLY     already baselined; legacy values kept for reference
```

"Materially" is a tolerance, not an opinion: sources within the company
currency's rounding of each other are treated as agreeing.
"""

from odoo import _, api, models
from odoo.exceptions import UserError

TOLERANCE_PCT = 1.0


class ConstructionBudgetMigration(models.AbstractModel):
    _name = 'realestate.construction.budget.migration'
    _description = 'Legacy Budget Classification'

    @api.model
    def classify(self, projects=None):
        """One row per project. Read-only, and re-runnable.

        Idempotent by construction: it writes nothing, so running it twice
        produces the same answer twice. The proposal it returns is acted on by
        `create_draft_budget()`, which is a separate, deliberate act.
        """
        Project = self.env['realestate.project'].sudo()
        Budget = self.env['realestate.construction.budget'].sudo()
        BOQ = self.env['realestate.boq'].sudo()
        if projects is None:
            projects = Project.search([])

        rows = []
        for project in projects:
            expected = project.expected_budget or 0.0
            milestones = sum(project.milestone_ids.mapped('budget_amount'))
            boqs = BOQ.search([
                ('project_id', '=', project.id),
                ('state', 'in', ('approved', 'locked')),
            ])
            boq_total = sum(boqs.mapped('total_amount'))
            baseline = Budget.current_for(project)

            sources = {
                'expected_budget': expected,
                'milestone_total': milestones,
                'boq_total': boq_total,
            }
            present = {name: value for name, value in sources.items() if value}

            if baseline:
                status, recommended = 'legacy_only', 'baseline'
            elif not present:
                status, recommended = 'no_source', False
            elif len(present) == 1:
                status, recommended = 'deterministic', next(iter(present))
            elif self._sources_agree(present.values()):
                status, recommended = 'deterministic', self._preferred(present)
            else:
                status, recommended = 'ambiguous', False

            rows.append({
                'project_id': project.id,
                'project_name': project.display_name,
                'company_id': project.company_id.id,
                'expected_budget': expected,
                'milestone_total': milestones,
                'boq_total': boq_total,
                'boq_count': len(boqs),
                'spread': (max(present.values()) - min(present.values())
                           if present else 0.0),
                'status': status,
                'recommended_source': recommended,
                'baseline_id': baseline.id if baseline else False,
            })
        return rows

    @api.model
    def _sources_agree(self, values):
        values = list(values)
        largest = max(values)
        if not largest:
            return True
        return (max(values) - min(values)) / largest * 100.0 <= TOLERANCE_PCT

    @api.model
    def _preferred(self, present):
        """When sources agree, prefer the one with the most structure.

        A BOQ carries quantities, rates and work items; milestones carry an
        allocation; `expected_budget` carries a single number. More structure
        means a better budget, and when they agree the choice costs nothing.
        """
        for name in ('boq_total', 'milestone_total', 'expected_budget'):
            if present.get(name):
                return name
        return False

    @api.model
    def summary(self, projects=None):
        counts = {'deterministic': 0, 'ambiguous': 0, 'no_source': 0,
                  'legacy_only': 0}
        for row in self.classify(projects):
            counts[row['status']] += 1
        return counts

    # ------------------------------------------------------------------
    @api.model
    def create_draft_budget_from_boq(self, boq):
        """§8 — an approved BOQ becomes a **draft** budget, never a baseline.

        Lines without a cost code are reported rather than guessed: inventing a
        classification for somebody's quantities is precisely the silent
        decision this whole milestone exists to prevent.
        """
        Budget = self.env['realestate.construction.budget']
        if boq.state not in ('approved', 'locked'):
            raise UserError(_(
                "Only an approved BOQ can seed a budget. A draft BOQ is a "
                "working document."))

        unmapped = boq.line_ids.filtered(lambda l: not l.cost_code_id)
        lines = []
        for line in boq.line_ids - unmapped:
            lines.append((0, 0, {
                'wbs_id': line.wbs_id.id or False,
                'cost_code_id': line.cost_code_id.id,
                'description': line.description or line.work_item_id.name,
                'amount_mode': 'quantity',
                'quantity': line.quantity,
                'uom_id': line.uom_id.id,
                'unit_rate': line.unit_rate,
            }))
        budget = Budget.create({
            'project_id': boq.project_id.id,
            'company_id': (boq.project_id.company_id or self.env.company).id,
            'currency_id': boq.currency_id.id,
            'source': 'boq',
            'source_boq_id': boq.id,
            'line_ids': lines,
        })
        if unmapped:
            budget.message_post(body=_(
                "%(count)s BOQ line(s) had no cost code and were not carried "
                "over: %(items)s. Map them on the BOQ and regenerate — a "
                "budget line nobody can classify cannot be controlled.",
                count=len(unmapped),
                items=', '.join(unmapped.mapped(
                    lambda l: l.work_item_id.display_name)[:10])))
        return budget
