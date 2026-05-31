from datetime import timedelta

from odoo import api, fields, models


class ContractPaymentInvoicing(models.Model):
    _inherit = 'realestate.contract.payment'

    @api.model
    def cron_auto_invoice_due_payments(self):
        """Deprecated: rentals now use one invoice whose payment term splits the
        receivable into the rent schedule, so there is nothing to auto-invoice
        per payment. Kept as a no-op (the cron record references it)."""
        return
        # --- legacy per-payment invoicing (disabled) ---
        lead_days = int(self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_rental.auto_invoice_lead_days', '7'))
        upper = fields.Date.today() + timedelta(days=lead_days)
        payments = self.search([
            ('move_id', '=', False),
            ('state', '=', 'draft'),
            ('date_due', '<=', upper),
            ('contract_id.state', 'in', ('active', 'confirmed', 'invoiced')),
        ])
        if not payments:
            return

        Invoice = self.env['account.move']
        invoices_vals = []
        for payment in payments:
            prop = payment.contract_line_id.property_id if payment.contract_line_id else payment.contract_id.property_id
            if not prop or not prop.product_variant_id:
                continue
            account_id = prop.categ_id.property_account_income_categ_id.id or False
            invoices_vals.append({
                'move_type': 'out_invoice',
                'partner_id': payment.contract_id.partner_id.id,
                'contract_id': payment.contract_id.id,
                'invoice_date': payment.date_due,
                'payment_reference': payment.name,
                'invoice_line_ids': payment._get_invoice_line_commands(prop, account_id),
            })
        if not invoices_vals:
            return
        invoices = Invoice.create(invoices_vals)
        inv_map = {inv.payment_reference: inv for inv in invoices}
        for payment in payments:
            inv = inv_map.get(payment.name)
            if inv:
                payment.move_id = inv.id  # state is computed from the move
        # Post so the cron actually lands invoices on the ledger.
        self.env['realestate.account.tools'].post_moves(invoices)
