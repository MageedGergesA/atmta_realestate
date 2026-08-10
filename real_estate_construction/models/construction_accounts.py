# -*- coding: utf-8 -*-
"""Where retention and advances live on the chart of accounts.

M7 fixes the Phase 0 defect in which retention was posted as a negative
expense line — money the project had spent, quietly un-spent. Retention is a
liability and an advance is an asset, and both belong in accounts somebody in
finance chose. Construction does not invent them, and it does not guess:
posting to the wrong account is worse than refusing to post.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = 'res.company'

    construction_retention_account_id = fields.Many2one(
        'account.account', string='Contractor Retention Account',
        domain="[('account_type', 'in', "
               "['liability_current', 'liability_non_current'])]",
        help="Liability account holding retention withheld from contractor "
             "payment certificates until it is released.")
    construction_advance_account_id = fields.Many2one(
        'account.account', string='Contractor Advance Account',
        domain="[('account_type', 'in', ['asset_current', 'asset_non_current'])]",
        help="Asset account holding advances paid to contractors until they "
             "are recovered from certificates.")
    construction_owner_retention_account_id = fields.Many2one(
        'account.account', string='Owner Retention Receivable',
        domain="[('account_type', 'in', ['asset_current', 'asset_non_current'])]",
        help="Asset account holding retention the owner withholds from our "
             "own progress billing until it is released to us.")
    construction_owner_advance_account_id = fields.Many2one(
        'account.account', string='Owner Advance Received',
        domain="[('account_type', 'in', "
               "['liability_current', 'liability_non_current'])]",
        help="Liability account holding advances received from the owner "
             "until they are recovered against progress billing.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    construction_retention_account_id = fields.Many2one(
        related='company_id.construction_retention_account_id', readonly=False)
    construction_advance_account_id = fields.Many2one(
        related='company_id.construction_advance_account_id', readonly=False)
    construction_owner_retention_account_id = fields.Many2one(
        related='company_id.construction_owner_retention_account_id',
        readonly=False)
    construction_owner_advance_account_id = fields.Many2one(
        related='company_id.construction_owner_advance_account_id',
        readonly=False)


class ConstructionAccounts(models.AbstractModel):
    """Resolve a construction control account, or refuse.

    There is no fallback here on purpose. A silent fallback to the expense
    account is precisely the defect being fixed: it produces entries that post
    cleanly, reconcile to nothing, and are only discovered when somebody asks
    where the retention went.
    """
    _name = 'realestate.construction.accounts'
    _description = 'Construction Control Accounts'

    #: field -> (label, where to set it)
    _ACCOUNTS = {
        'construction_retention_account_id': _('Contractor Retention Account'),
        'construction_advance_account_id': _('Contractor Advance Account'),
        'construction_owner_retention_account_id':
            _('Owner Retention Receivable'),
        'construction_owner_advance_account_id': _('Owner Advance Received'),
    }

    @api.model
    def get(self, field, company=None):
        company = company or self.env.company
        account = company[field]
        if not account:
            raise UserError(_(
                "%(label)s is not configured for %(company)s.\n\n"
                "Set it in Settings → Accounting → Construction. Nothing is "
                "posted until it is: a control balance in the wrong account "
                "is harder to find than one that was never written.",
                label=self._ACCOUNTS.get(field, field),
                company=company.display_name))
        # Odoo 18 accounts are shared across companies through `company_ids`.
        if account.company_ids and company not in account.company_ids:
            raise UserError(_(
                "%(account)s is not available to %(company)s.",
                account=account.display_name,
                company=company.display_name))
        return account

    @api.model
    def retention_account(self, company=None):
        return self.get('construction_retention_account_id', company)

    @api.model
    def advance_account(self, company=None):
        return self.get('construction_advance_account_id', company)

    @api.model
    def owner_retention_account(self, company=None):
        return self.get('construction_owner_retention_account_id', company)

    @api.model
    def owner_advance_account(self, company=None):
        return self.get('construction_owner_advance_account_id', company)
