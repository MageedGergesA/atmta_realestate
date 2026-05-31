from odoo import fields, models, api
from odoo.exceptions import UserError


class RealEstateContractUtilityLine(models.Model):
    _name = 'realestate.contract.utility.line'
    _description = 'Contract Utility Line'

    contract_id = fields.Many2one('realestate.contract', string="Contract", ondelete='cascade', required=True)
    name = fields.Char(string="Utility", required=True)
    vendor_id = fields.Many2one(
        'res.partner', string="Utility Provider",
        help="Vendor billed for this utility. Required to post the vendor bill.")
    amount = fields.Monetary(string="Expected Amount", required=True)
    currency_id = fields.Many2one(related='contract_id.currency_id', store=True)
    date = fields.Date(string='Date')
    bill_id = fields.Many2one('account.move', string="Vendor Bill", readonly=True)
    bill_paid = fields.Boolean(string="Paid", compute='_compute_bill_paid', store=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
    ], default='draft', string="Status")

    def action_reset_to_draft_utility(self):
        for line in self:
            line.state = 'draft'
    def action_confirm_utility(self):
        for line in self:
            if line.state != 'draft':
                continue
            if not line.vendor_id:
                raise UserError("Set the Utility Provider before creating the vendor bill.")
            move = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'partner_id': line.vendor_id.id,
                'invoice_date': fields.Date.today(),
                'invoice_line_ids': [(0, 0, {
                    'name': f'Utility - {line.name}',
                    'quantity': 1,
                    'price_unit': line.amount,
                })],
            })
            line.bill_id = move
            line.state = 'confirmed'
            # Post so the cost actually lands on the ledger.
            self.env['realestate.account.tools'].post_moves(move)

    def action_register_payment_utility(self):
        moves = self.mapped('bill_id').filtered(lambda m: m.state == 'posted')
        if not moves:
            raise UserError("There is no posted bill to pay yet.")
        return self.env['realestate.account.tools'].register_payment(moves)

    @api.depends('bill_id.payment_state')
    def _compute_bill_paid(self):
        for line in self:
            line.bill_paid = line.bill_id.payment_state in ('paid', 'in_payment', 'reversed')