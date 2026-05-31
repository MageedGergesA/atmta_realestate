from odoo import models, fields, tools


class ContractLineReport(models.Model):
    _name = 'realestate.report.contract.line'
    _description = 'Contract Line Summary Report'
    _auto = False

    contract_id = fields.Many2one('realestate.contract', string="Contract", readonly=True)
    contract_line_id = fields.Many2one('realestate.contract.line', string="Contract Unit", readonly=True)
    partner_id = fields.Many2one('res.partner', string="Customer", readonly=True)
    property_id = fields.Many2one('realestate.property', string="Property", readonly=True)
    start_date = fields.Date(string="Start Date", readonly=True)
    end_date = fields.Date(string="End Date", readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ready', 'Ready'),
        ('confirmed', 'Confirmed'),
        ('invoiced', 'Invoiced'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('terminated', 'Terminated'),
    ], string="Status", readonly=True)
    price = fields.Float(string="Base Rent", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW realestate_report_contract_line AS (
                SELECT
                    l.id as id,
                    l.contract_id,
                    l.id as contract_line_id,
                    c.partner_id,
                    l.property_id,
                    l.start_date,
                    l.end_date,
                    l.state,
                    l.price
                FROM realestate_contract_line l
                JOIN realestate_contract c ON c.id = l.contract_id
            )
        """)
