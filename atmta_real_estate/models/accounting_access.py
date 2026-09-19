"""Whether the current user may post accounting entries.

Rental roles grant no accounting rights on purpose (see
``security/leasing_groups.xml``): invoicing a lease, receiving or refunding a
deposit, settling a termination and billing a utility post Odoo invoices,
credit notes and payments, which Odoo reserves for its Invoicing group.

A button's ``groups`` attribute means "any of these groups", so it cannot say
"a Rental role *and* Invoicing". Money buttons are therefore hidden with
``invisible="... or not can_post_accounting"``. This only decides what is shown:
the access rules and the server-side role checks are unchanged, and a user
without Invoicing rights is still refused if the method is called directly.
"""

from odoo import api, fields, models

INVOICING_GROUP = 'account.group_account_invoice'


class RentalAccountingAccessMixin(models.AbstractModel):
    _name = 'realestate.accounting.access.mixin'
    _description = 'Rental: can the current user post accounting entries'

    can_post_accounting = fields.Boolean(
        compute='_compute_can_post_accounting',
        help="Whether you hold Odoo Invoicing rights, which receiving, refunding, "
             "invoicing and settling require.")

    @api.depends_context('uid')
    def _compute_can_post_accounting(self):
        allowed = self.env.user.has_group(INVOICING_GROUP)
        for record in self:
            record.can_post_accounting = allowed


class ContractAccountingAccess(models.Model):
    _name = 'realestate.contract'
    _inherit = ['realestate.contract', 'realestate.accounting.access.mixin']


class DepositAccountingAccess(models.Model):
    _name = 'realestate.contract.deposit'
    _inherit = ['realestate.contract.deposit', 'realestate.accounting.access.mixin']


class TerminationAccountingAccess(models.Model):
    _name = 'realestate.contract.termination'
    _inherit = ['realestate.contract.termination', 'realestate.accounting.access.mixin']


class UtilityLineAccountingAccess(models.Model):
    _name = 'realestate.contract.utility.line'
    _inherit = ['realestate.contract.utility.line', 'realestate.accounting.access.mixin']
