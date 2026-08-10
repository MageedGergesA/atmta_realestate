from odoo import _, api, fields, models


class ProjectAnalytic(models.Model):
    """Adds an auto-generated analytic account per project so every downstream
    costed record (cost line, material request, PO, payment certificate) can
    post to a single cost center."""
    _inherit = 'realestate.project'

    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account',
        copy=False, tracking=True,
        help='Cost center for this project. Auto-created on demand.',
    )

    def _get_or_create_analytic_account(self):
        """Return the project's analytic account, creating one if missing.

        Two things changed in M1, both of them silent bugs before:

        * the plan is the **referenced** Real Estate Projects plan, not
          `Plan.search([], limit=1)`. That search returned whichever plan
          sorted first, so on a database with a "Departments" plan every
          project cost centre was filed under Departments.
        * the account takes the **project's** company, not the user's. A
          project in company B used to get a cost centre in company A,
          depending on who happened to click first.

        Accounts that already exist are left exactly where they are. Moving a
        cost centre would move history with it, and history is not ours to
        rewrite — the fix governs the next account created, not the last one.

        The back-link is written as a system write, for the reason Procurement
        M2 proved on the cost code first: a buyer coding a purchase line has
        read-only project access by design, and storing the link raised

            AccessError: You are not allowed to modify 'Real Estate
            Development Project' records

        the first time anybody costed anything against the project. Creating
        the account was already a system act; recording which account was
        created is the other half of the same act, and nothing here lets the
        user change anything else about the project.
        """
        self.ensure_one()
        if self.analytic_account_id:
            return self.analytic_account_id
        plan = self.env['realestate.construction.analytic']._project_plan()
        account = self.env['account.analytic.account'].sudo().create({
            'name': self.display_name,
            'code': self.code or False,
            'plan_id': plan.id,
            'company_id': (self.company_id or self.env.company).id,
        })
        self.sudo().write({'analytic_account_id': account.id})
        return account

    def action_generate_analytic_account(self):
        for rec in self:
            rec._get_or_create_analytic_account()
        return True
