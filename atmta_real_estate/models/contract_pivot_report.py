from odoo import api, models, fields, tools

class ContractPaymentPivotReport(models.Model):
    _name = 'realestate.report.contract'
    _description = 'Contract Payment Summary Report'
    _auto = False

    contract_id = fields.Many2one('realestate.contract', string="Contract", readonly=True)
    contract_line_id = fields.Many2one('realestate.contract.line', string="Unit", readonly=True)
    partner_id = fields.Many2one('res.partner', string="Customer", readonly=True)
    date_due = fields.Date(string="Due Date", readonly=True)
    amount = fields.Float(string="Amount", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW realestate_report_contract AS (
                SELECT
                    p.id as id,
                    p.contract_id,
                    p.contract_line_id,
                    c.partner_id,
                    p.date_due,
                    p.amount
                FROM realestate_contract_payment p
                LEFT JOIN realestate_contract c ON c.id = p.contract_id
                LEFT JOIN realestate_contract_line cl ON cl.id = p.contract_line_id
            )
        """)
