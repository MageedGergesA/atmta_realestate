from odoo import models, fields

class ClinicLabIntegration(models.Model):
    _name = 'clinic.lab.integration'
    _description = 'Clinic Lab/Radiology Integration (HL7/FHIR Stub)'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    integration_type = fields.Selection([
        ('hl7', 'HL7'),
        ('fhir', 'FHIR')
    ], string='Type')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('error', 'Error')
    ], string='Status')
    last_sync = fields.Datetime(string='Last Sync')

class ClinicPharmacyIntegration(models.Model):
    _name = 'clinic.pharmacy.integration'
    _description = 'Clinic Pharmacy Integration (Stub)'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('error', 'Error')
    ], string='Status')
    last_sync = fields.Datetime(string='Last Sync')

class ClinicWearableIntegration(models.Model):
    _name = 'clinic.wearable.integration'
    _description = 'Clinic Wearable Integration (Stub)'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    device_type = fields.Char(string='Device Type')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('error', 'Error')
    ], string='Status')
    last_sync = fields.Datetime(string='Last Sync')

class ClinicAccountingIntegration(models.Model):
    _name = 'clinic.accounting.integration'
    _description = 'Clinic Accounting Integration (Stub)'
    _rec_name = 'name'

    name = fields.Char(string='Name')
    system = fields.Selection([
        ('odoo', 'Odoo'),
        ('quickbooks', 'QuickBooks'),
        ('xero', 'Xero')
    ], string='System')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('error', 'Error')
    ], string='Status')
    last_sync = fields.Datetime(string='Last Sync') 