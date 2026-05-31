from odoo import api, fields, models


class RealEstateAccountTools(models.AbstractModel):
    """Shared accounting helpers for the whole real-estate suite.

    Centralises the two operations every money flow needs so behaviour stays
    consistent across rental, developer, construction and brokerage:
      * post draft moves (customer invoices / vendor bills)
      * register and reconcile a real payment via the standard wizard
    Every other module transitively depends on ``atmta_real_estate`` so they
    can call these through ``self.env['realestate.account.tools']``.
    """
    _name = 'realestate.account.tools'
    _description = 'Real Estate Accounting Helpers'

    @api.model
    def post_moves(self, moves):
        """Post any *draft* moves that carry lines. No-op for the rest.

        Returns the same recordset for chaining."""
        if not moves:
            return moves
        to_post = moves.filtered(lambda m: m.state == 'draft' and m.line_ids)
        if to_post:
            to_post.action_post()
        return moves

    @api.model
    def register_payment(self, moves, payment_date=None, journal=None):
        """Register and reconcile a payment for posted, unpaid moves using the
        standard ``account.payment.register`` wizard (handles journal defaults,
        partner grouping and reconciliation).

        Returns the created ``account.payment`` recordset (empty if there was
        nothing to pay)."""
        payable = moves.filtered(
            lambda m: m.state == 'posted' and m.payment_state in ('not_paid', 'partial'))
        if not payable:
            return self.env['account.payment']
        wizard_vals = {}
        if payment_date:
            wizard_vals['payment_date'] = payment_date
        if journal:
            wizard_vals['journal_id'] = journal.id
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=payable.ids,
        ).create(wizard_vals)
        return wizard._create_payments()
