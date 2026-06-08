from odoo import _, api, fields, models
from odoo.exceptions import UserError


class _GenerateDocMixin(models.AbstractModel):
    """Shared action: opens the generate-wizard pointed at this record,
    and exposes a smart-button count of generated documents."""
    _name = 'realestate.contract.generate.mixin'
    _description = 'Generate Contract Document Mixin'

    generated_document_count = fields.Integer(
        compute='_compute_generated_document_count',
        string='Documents',
    )

    def _compute_generated_document_count(self):
        Doc = self.env['realestate.contract.document'].sudo()
        for rec in self:
            rec.generated_document_count = Doc.search_count([
                ('target_model_name', '=', rec._name),
                ('target_record_id', '=', rec.id),
            ])

    def action_generate_contract_document(self):
        self.ensure_one()
        Template = self.env['realestate.contract.template']
        if not Template.search_count([
            ('target_model_name', '=', self._name),
            ('active', '=', True),
        ]):
            raise UserError(_(
                "No contract template targets %s yet. "
                "Create one under Real Estate → Configuration → Contract Templates.",
                self._name,
            ))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.contract.generate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'name': _('Generate Contract Document'),
            'context': {
                'default_target_model_name': self._name,
                'default_record_id': self.id,
            },
        }

    def action_open_generated_documents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.contract.document',
            'view_mode': 'list,form',
            'name': _('Generated Documents'),
            'domain': [
                ('target_model_name', '=', self._name),
                ('target_record_id', '=', self.id),
            ],
            'context': {'default_target_model_name': self._name},
            'target': 'current',
        }


class RealestateSaleContract(models.Model):
    _name = 'realestate.sale.contract'
    _inherit = ['realestate.sale.contract', 'realestate.contract.generate.mixin']


class RealestateContract(models.Model):
    _name = 'realestate.contract'
    _inherit = ['realestate.contract', 'realestate.contract.generate.mixin']


class RealestateTransaction(models.Model):
    _name = 'realestate.transaction'
    _inherit = ['realestate.transaction', 'realestate.contract.generate.mixin']


class RealestateContractor(models.Model):
    _name = 'realestate.contractor'
    _inherit = ['realestate.contractor', 'realestate.contract.generate.mixin']
