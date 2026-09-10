# -*- coding: utf-8 -*-
"""M1 — the one place analytic distributions are built.

Every PO line, vendor bill line and customer invoice line Construction creates
must be traceable to a project *and* a cost code. That is one rule, so it lives
in one place: `distribution_for(project, cost_code)`.

### The two defects this replaces

`project_analytic._get_or_create_analytic_account()` shipped with:

```python
plan = Plan.search([], limit=1) or Plan.create({'name': _('Real Estate')})
... 'company_id': self.env.company.id,
```

- the plan was **whichever one sorted first** — on a database with a
  "Departments" plan, project cost centres were filed under Departments;
- the account took **the user's** company rather than the project's, so a
  project in company B got a cost centre in company A.

Both are fixed here, and the project plan is now a real, referenced record
(`analytic_plan_re_projects`) rather than a lucky search result. Existing
projects keep the analytic account they already have — the fix changes how the
*next* one is created, never where past postings went.
"""

from odoo import _, api, models

PROJECT_PLAN_XMLID = 'atmta_construction_core.analytic_plan_re_projects'
COST_CODE_PLAN_XMLID = 'atmta_construction_core.analytic_plan_cost_codes'


class ConstructionAnalytic(models.AbstractModel):
    """Analytic plumbing for project controls."""
    _name = 'realestate.construction.analytic'
    _description = 'Construction Analytic Integration'

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------
    @api.model
    def _plan(self, xmlid, name):
        """A referenced plan, created once if the data file has not run yet."""
        plan = self.env.ref(xmlid, raise_if_not_found=False)
        if plan:
            return plan.sudo()
        # Only reachable in a database where this module's data was removed.
        return self.env['account.analytic.plan'].sudo().create({'name': name})

    @api.model
    def _project_plan(self):
        return self._plan(PROJECT_PLAN_XMLID, _('Real Estate Projects'))

    @api.model
    def _cost_code_plan(self):
        return self._plan(COST_CODE_PLAN_XMLID, _('Construction Cost Codes'))

    #: Analytic lines that represent **project cost**.
    #:
    #: Tax is excluded, and it has to be excluded explicitly: a tax move line
    #: inherits its base line's analytic distribution, so VAT arrives in the
    #: analytic ledger even with `account.tax.analytic` switched off. Left in,
    #: every actual would be inflated by the tax rate — the mirror image of the
    #: commitment distortion Phase 0 found, and just as invisible.
    #:
    #: `move_line_id = False` is kept: a manual analytic entry has no move line
    #: and is not a tax line either.
    UNTAXED = ['|', ('move_line_id', '=', False),
               ('move_line_id.tax_line_id', '=', False)]

    # ------------------------------------------------------------------
    # Distributions
    # ------------------------------------------------------------------
    @api.model
    def distribution_for(self, project, cost_code=None):
        """The analytic distribution for a costed line.

        Returns Odoo's own format: **one key**, the account ids joined by
        commas, at 100%.

        ```
            {"7,42": 100.0}     ← one posting, coded on two plans
            NOT {"7": 100.0, "42": 100.0}
        ```

        The difference is not cosmetic and cost a bug during M2. Two separate
        keys are two separate distributions: Odoo writes **two** analytic
        lines, each carrying one plan's column and each for the full amount,
        which double-counts the cost and leaves neither line able to say both
        which project and which cost code it belongs to. One comma-joined key
        is one line with both columns set, which is what
        `actual_by_cost_code()` reads back.

        A missing cost code is not an error. Plenty of legitimate postings (a
        project-level fee, a legacy record) know their project and nothing
        finer, and refusing them would push people back to booking costs with
        no analytic at all — which is how the ledger stopped being able to
        answer questions in the first place.
        """
        account_ids = []
        if project:
            account = project._get_or_create_analytic_account()
            if account:
                account_ids.append(account.id)
        if cost_code:
            account = cost_code._get_or_create_analytic_account()
            if account:
                account_ids.append(account.id)
        if not account_ids:
            return False
        return {','.join(str(account_id) for account_id in account_ids): 100.0}

    @api.model
    def actual_domain(self, project, cost_codes=None, date_from=None,
                      date_to=None):
        """The domain that defines **actual cost** for project controls.

        This is the definition Rule 1 demands: actual cost is not a number
        Construction keeps, it is what the ledger says was spent on this
        project's analytic account. Everything that reports an actual — the
        cost report, the dashboard, EAC — asks through here, so there is one
        answer and one place to correct it.

        Analytic lines carry **one column per plan** (`account_id` for the root
        plan, `x_plan<id>_id` for the others), which is why the column names
        are asked of the plans rather than written down here: they depend on
        the plan ids in the database this runs on. `auto_account_id` looks like
        the obvious field and is not — it is computed and not stored, so it
        cannot be searched.
        """
        account = project.analytic_account_id
        if not account:
            # No cost centre means nothing was ever posted against it. An empty
            # domain would match the whole ledger, which is the opposite of the
            # truth, so answer with a domain that matches nothing.
            return [('id', '=', 0)]

        domain = list(self.UNTAXED) + [
            (self._project_plan()._column_name(), '=', account.id)]
        if cost_codes:
            code_accounts = cost_codes._analytic_accounts()
            if not code_accounts:
                return [('id', '=', 0)]
            domain += [
                (self._cost_code_plan()._column_name(), 'in', code_accounts.ids)
            ]
        if date_from:
            domain += [('date', '>=', date_from)]
        if date_to:
            domain += [('date', '<=', date_to)]
        return domain

    @api.model
    def actual_by_cost_code(self, project, date_from=None, date_to=None):
        """Actual cost per cost code, aggregated by the database.

        `_read_group`, not a search-and-sum: M46 assumes hundreds of thousands
        of cost transactions, and the difference between the two is the
        difference between a dashboard that opens and one that times out.

        Returns `{cost_code_id: amount}` with the sign flipped — analytic lines
        carry expenditure as negative, and a cost report that showed costs as
        negative numbers would be read wrong by every person who opened it.
        """
        account = project.analytic_account_id
        if not account:
            return {}
        column = self._cost_code_plan()._column_name()
        domain = list(self.UNTAXED) + [
            (self._project_plan()._column_name(), '=', account.id)]
        if date_from:
            domain += [('date', '>=', date_from)]
        if date_to:
            domain += [('date', '<=', date_to)]

        groups = self.env['account.analytic.line'].sudo()._read_group(
            domain, groupby=[column], aggregates=['amount:sum'])

        by_account = {
            account_rec.id: -total
            for account_rec, total in groups if account_rec
        }
        if not by_account:
            return {}
        codes = self.env['realestate.construction.cost.code'].sudo().search(
            [('analytic_account_id', 'in', list(by_account))])
        return {
            code.id: by_account.get(code.analytic_account_id.id, 0.0)
            for code in codes
        }
