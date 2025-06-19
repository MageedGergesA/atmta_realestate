from odoo import models, fields

class ClinicDashboard(models.Model):
    _name = 'clinic.dashboard'
    _description = 'Clinic Dashboard (Analytics)'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    dashboard_type = fields.Selection([
        ('financial', 'Financial'),
        ('clinical', 'Clinical'),
        ('satisfaction', 'Patient Satisfaction')
    ], string='Type')
    data = fields.Text(string='Data (JSON)')

class ClinicReport(models.Model):
    _name = 'clinic.report'
    _description = 'Clinic Custom Report'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    report_type = fields.Selection([
        ('custom', 'Custom'),
        ('scheduled', 'Scheduled')
    ], string='Type')
    data = fields.Text(string='Data (JSON)')
    export_format = fields.Selection([
        ('excel', 'Excel'),
        ('pdf', 'PDF')
    ], string='Export Format')

class ClinicPredictiveAnalytics(models.Model):
    _name = 'clinic.predictive.analytics'
    _description = 'Clinic Predictive Analytics (Stub)'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    description = fields.Text(string='Description')
    result = fields.Text(string='Result (JSON)') 