"""Company-level leasing configuration (Phases 13, 14, 27, 28, 30).

Every setting that can legitimately differ per legal entity lives here rather
than in ``ir.config_parameter``, so a multi-company database can run an Egyptian
entity on 30-day proration and a UAE entity on actual-days without them
fighting.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PRORATION_METHODS = [
    ('actual', 'Actual Calendar Days'),
    ('thirty_day', '30-Day Month Convention'),
    ('none', 'No Proration (charge full period)'),
    ('full', 'Full Billing Period Only (no partial periods)'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ---------------- Workflow controls ----------------
    re_require_lease_approval = fields.Boolean(
        string='Require Lease Approval',
        help="When enabled, a lease must pass through Pending Approval before "
             "it can go to signature. Off by default so existing one-click "
             "confirm flows keep working.",
    )
    re_allow_self_approval = fields.Boolean(
        string='Allow Self-Approval',
        help="When disabled (default), a user cannot approve a lease they "
             "created. The 'Allow Self-Approval' security group overrides this "
             "for individual users.",
    )

    # ---------------- Billing ----------------
    re_proration_method = fields.Selection(
        PRORATION_METHODS, string='Rent Proration Method', default='actual',
        required=True,
        help="How partial billing periods (mid-month move-in, mid-month "
             "termination, amendments taking effect mid-period) are charged.",
    )
    re_rent_product_id = fields.Many2one(
        'product.product', string='Default Rent Product',
        domain="[('type', '=', 'service')]",
        help="Service product used on rent invoice lines when the property "
             "itself carries no invoiceable product.",
    )
    re_billing_lead_days = fields.Integer(
        string='Invoice Lead Days', default=7,
        help="How many days before its due date a billing obligation is "
             "invoiced by the automatic billing cron.",
    )
    re_auto_bill = fields.Boolean(
        string='Automatic Rent Invoicing',
        help="Let the scheduled action invoice due obligations automatically.",
    )

    # ---------------- Deposits ----------------
    re_deposit_account_id = fields.Many2one(
        'account.account', string='Security Deposit Account',
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current')), "
               "('reconcile', '=', True)]",
        help="Liability account holding tenant security deposits. A deposit is "
             "money you owe back -- it must never post to a revenue account. It "
             "must allow reconciliation, so each receipt can be matched with its "
             "refund.",
    )
    re_deposit_journal_id = fields.Many2one(
        'account.journal', string='Deposit Journal',
        domain="[('type', 'in', ('bank', 'cash'))]",
    )
    re_deposit_forfeit_income_account_id = fields.Many2one(
        'account.account', string='Forfeited Deposit Income Account',
        domain="[('account_type', '=', 'income_other')]",
        help="Where a forfeited deposit is recognised as income. Only used at "
             "the moment of forfeiture.",
    )

    # ---------------- Renewals ----------------
    re_renewal_reminder_days = fields.Char(
        string='Renewal Reminder Windows', default='180,120,90,60,30',
        help="Comma-separated day counts before lease expiry at which a "
             "renewal activity is raised.",
    )
    re_auto_expire_leases = fields.Boolean(
        string='Auto-Expire Leases', default=True,
        help="Let the scheduled action move fully-settled leases to Ended "
             "after their term.",
    )
    re_notice_period_days = fields.Integer(
        string='Default Notice Period (days)', default=60,
    )

    @api.constrains('re_renewal_reminder_days')
    def _check_renewal_reminder_days(self):
        for company in self:
            for token in (company.re_renewal_reminder_days or '').split(','):
                token = token.strip()
                if not token:
                    continue
                if not token.isdigit():
                    raise ValidationError(_(
                        "Renewal reminder windows must be a comma-separated "
                        "list of whole days (got %r).", token))

    def _renewal_reminder_windows(self):
        """Parsed, de-duplicated, descending reminder windows."""
        self.ensure_one()
        days = set()
        for token in (self.re_renewal_reminder_days or '').split(','):
            token = token.strip()
            if token.isdigit():
                days.add(int(token))
        return sorted(days, reverse=True)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    re_require_lease_approval = fields.Boolean(
        related='company_id.re_require_lease_approval', readonly=False)
    re_allow_self_approval = fields.Boolean(
        related='company_id.re_allow_self_approval', readonly=False)
    re_proration_method = fields.Selection(
        related='company_id.re_proration_method', readonly=False)
    re_rent_product_id = fields.Many2one(
        related='company_id.re_rent_product_id', readonly=False)
    re_billing_lead_days = fields.Integer(
        related='company_id.re_billing_lead_days', readonly=False)
    re_auto_bill = fields.Boolean(
        related='company_id.re_auto_bill', readonly=False)
    # A related field does not carry the company field's domain into the
    # Settings form, which then offered every account and journal.
    re_deposit_account_id = fields.Many2one(
        related='company_id.re_deposit_account_id', readonly=False,
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current')), "
               "('reconcile', '=', True)]")
    re_deposit_journal_id = fields.Many2one(
        related='company_id.re_deposit_journal_id', readonly=False,
        domain="[('type', 'in', ('bank', 'cash'))]")
    re_deposit_forfeit_income_account_id = fields.Many2one(
        related='company_id.re_deposit_forfeit_income_account_id', readonly=False,
        domain="[('account_type', '=', 'income_other')]")
    re_renewal_reminder_days = fields.Char(
        related='company_id.re_renewal_reminder_days', readonly=False)
    re_auto_expire_leases = fields.Boolean(
        related='company_id.re_auto_expire_leases', readonly=False)
    re_notice_period_days = fields.Integer(
        related='company_id.re_notice_period_days', readonly=False)
