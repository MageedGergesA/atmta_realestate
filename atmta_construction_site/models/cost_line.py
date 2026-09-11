from odoo import _, api, fields, models


class CostLine(models.Model):
    _name = 'realestate.construction.cost.line'
    _description = 'Construction Cost Line'
    _order = 'date desc, id desc'

    name = fields.Char(string='Description', required=True)
    project_id = fields.Many2one('realestate.project', string='Project', required=True, ondelete='cascade')
    phase_id = fields.Many2one('realestate.phase', string='Phase', ondelete='set null')
    milestone_id = fields.Many2one('realestate.construction.milestone', string='Milestone', ondelete='set null')
    contractor_id = fields.Many2one('realestate.contractor', string='Contractor')

    date = fields.Date(default=fields.Date.context_today, required=True)
    amount = fields.Monetary(string='Amount', required=True)
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account',
        compute='_compute_analytic', store=True, readonly=False,
        help='Cost center this line posts to. Defaults to the project\'s analytic account.',
    )

    @api.depends('project_id')
    def _compute_analytic(self):
        for rec in self:
            if rec.project_id and rec.project_id.analytic_account_id and not rec.analytic_account_id:
                rec.analytic_account_id = rec.project_id.analytic_account_id
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    category = fields.Selection([
        ('material', 'Material'),
        ('labor', 'Labor'),
        ('equipment', 'Equipment'),
        ('subcontract', 'Subcontract'),
        ('permit', 'Permit / Fee'),
        ('other', 'Other'),
    ], default='material')

    vendor_bill_id = fields.Many2one('account.move', string='Vendor Bill',
                                      domain="[('move_type', '=', 'in_invoice')]")

    # ------------------------------------------------------------------
    # M2 — what a cost line is, now that actual cost comes from the ledger
    # ------------------------------------------------------------------
    #
    # Phase 0 found `project.actual_cost` summing these lines while the posted
    # vendor bills sat in the ledger unread — two "actual costs", neither
    # reconciled, and a cost controller who diligently kept both in step was
    # double-counting by definition.
    #
    # The lines are **not deleted**. They hold real work: labour accruals,
    # estimates, informational costs and years of legacy entries. What changes
    # is that each one now says what it is, and only one classification is
    # allowed to behave like accounting.
    cost_basis = fields.Selection([
        ('ledger_backed', 'Ledger-Backed (posted in Accounting)'),
        ('accrual', 'Accrual (not yet posted)'),
        ('estimate', 'Estimate / Informational'),
        ('legacy', 'Legacy — Needs Classification'),
    ], string='Cost Basis', default='legacy', required=True, index=True,
        tracking=False,
        help="What kind of number this is.\n\n"
             "• Ledger-backed: the money is already in Accounting, so this row "
             "is a reference and must never be added to actual cost again.\n"
             "• Accrual: real cost incurred with no accounting document yet — "
             "reported beside ledger actuals, never inside them.\n"
             "• Estimate: informational only.\n"
             "• Legacy: classified by nobody yet, and excluded from control "
             "totals until a human decides.",
    )
    counts_as_actual = fields.Boolean(
        compute='_compute_counts_as_actual', store=True,
        help="False for everything the ledger already knows about.",
    )

    @api.depends('cost_basis', 'vendor_bill_id.state')
    def _compute_counts_as_actual(self):
        """Only a declared accrual adds to the control view of cost.

        Ledger-backed lines are excluded because the analytic ledger already
        carries them; estimates and unclassified legacy rows are excluded
        because they are not costs anybody has incurred.
        """
        for rec in self:
            rec.counts_as_actual = rec.cost_basis == 'accrual'

    @api.onchange('vendor_bill_id')
    def _onchange_vendor_bill_basis(self):
        """A line that names a posted bill is ledger-backed, by definition."""
        for rec in self:
            if rec.vendor_bill_id and rec.vendor_bill_id.state == 'posted':
                rec.cost_basis = 'ledger_backed'

    notes = fields.Text()

    @api.onchange('milestone_id')
    def _onchange_milestone_id(self):
        if self.milestone_id:
            if not self.project_id:
                self.project_id = self.milestone_id.project_id
            if not self.phase_id and self.milestone_id.phase_id:
                self.phase_id = self.milestone_id.phase_id
            if not self.contractor_id and self.milestone_id.contractor_id:
                self.contractor_id = self.milestone_id.contractor_id
