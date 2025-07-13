from odoo import models, fields, api


class ReservationContractsWiz(models.TransientModel):
    _name = 'realestate.contracts.wizard'

    contract_data = fields.Boolean(string='Show Contract Data', default=True)
    lessor_data = fields.Boolean(string='Show Lessor Data', default=True)
    tenant_data = fields.Boolean(string='Show Tenant Data', default=True)
    tenant_representative_data = fields.Boolean(string='Show Tenant Representative Data', default=True)
    ownership_doc_data = fields.Boolean(string='Show Ownership Document Data', default=True)
    parent_property_data = fields.Boolean(string='Show Parent Property Data', default=True)
    property_data = fields.Boolean(string='Show Property Data', default=True)
    rent_payment_data = fields.Boolean(string='Show Rent Payment Data', default=True)
    # break page
    contract_data_break = fields.Boolean(string='Page Break After Contract Data')
    lessor_data_break = fields.Boolean(string='Page Break After Lessor Data')
    tenant_data_break = fields.Boolean(string='Page Break After Tenant Data')
    tenant_representative_data_break = fields.Boolean(string='Page Break After Tenant Representative')
    ownership_doc_data_break = fields.Boolean(string='Page Break After Ownership Docs')
    parent_property_data_break = fields.Boolean(string='Page Break After Parent Property')
    property_data_break = fields.Boolean(string='Page Break After Property Data')

    def action_print(self):
        self.ensure_one()
        contract_id = self.env['realestate.contract'].search([('id','=',self.env.context.get('active_id'))], limit=1)
        data = {
            'wizard_id': self.id,
            'contract_id': contract_id.id,
            # Section visibility flags
            'contract_data': self.contract_data,
            'lessor_data': self.lessor_data,
            'tenant_data': self.tenant_data,
            'tenant_representative_data': self.tenant_representative_data,
            'ownership_doc_data': self.ownership_doc_data,
            'parent_property_data': self.parent_property_data,
            'property_data': self.property_data,
            'rent_payment_data': self.rent_payment_data,
            # organize section page
            'contract_data_break': self.contract_data_break,
            'lessor_data_break': self.lessor_data_break,
            'tenant_data_break': self.tenant_data_break,
            'tenant_representative_data_break': self.tenant_representative_data_break,
            'ownership_doc_data_break': self.ownership_doc_data_break,
            'parent_property_data_break': self.parent_property_data_break,
            'property_data_break': self.property_data_break,
        }

        # Pass the contract record properly
        return self.env.ref('atmta_real_estate.action_realestate_contract_report').report_action(
            contract_id,  # Pass the record itself, not just ID
            data=data
        )

class RealEstateContractReport(models.AbstractModel):
    _name = 'report.atmta_real_estate.report_realestate_contract_template'

    @api.model
    def _get_report_values(self, docids, data=None):
        # Debug print to check incoming docids
        print(f"Received ------------------------------------------ docids: {docids}")

        # Get the contract record(s)
        docs = self.env['realestate.contract'].browse(docids)

        # Debug print to check browsed records
        print(f"Browsed docs: {docs}")

        if not docs:
            # Fallback - try to get contract from data
            contract_id = data and data.get('contract_id')
            if contract_id:
                docs = self.env['realestate.contract'].browse(contract_id)
                print(f"Fallback docs: {docs}")

        return {
            'doc_ids': docs.ids,
            'doc_model': 'realestate.contract',
            'docs': docs,
            'data': data or {},
            'contract': docs[0] if docs else None,
        }