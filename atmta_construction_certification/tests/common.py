# -*- coding: utf-8 -*-
"""The smallest believable certification fixture.

This module had no tests of its own; the suite's fixtures live in
`real_estate_construction`, which is not installed when this module is tested
alone. These are the few it needs, shaped the same way.
"""
from odoo import fields
from odoo.tests.common import TransactionCase


class CertificationFindingsCommon(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env['res.partner'])
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')
        cls.BOQ = cls.env['realestate.boq']
        cls.BOQLine = cls.env['realestate.boq.line']
        cls.Certificate = cls.env[
            'realestate.construction.payment.certificate']
        cls.CertLine = cls.env[
            'realestate.construction.payment.certificate.line']
        cls.Billing = cls.env['realestate.owner.progress.billing']
        cls.Advance = cls.env['realestate.construction.advance']
        cls.Retention = cls.env['realestate.construction.retention']
        cls._configure_construction_accounts()

    @classmethod
    def _configure_construction_accounts(cls):
        Account = cls.env['account.account']

        def account(code, name, account_type):
            existing = Account.search(
                [('code', '=', code), ('company_ids', 'in', cls.company.id)],
                limit=1)
            return existing or Account.create({
                'code': code, 'name': name, 'account_type': account_type,
                'company_ids': [(6, 0, cls.company.ids)],
            })

        cls.company.write({
            'construction_retention_account_id': account(
                'RE2101', 'Contractor Retention Payable',
                'liability_current').id,
            'construction_advance_account_id': account(
                'RE1301', 'Advances to Contractors', 'asset_current').id,
            'construction_owner_retention_account_id': account(
                'RE1302', 'Owner Retention Receivable', 'asset_current').id,
            'construction_owner_advance_account_id': account(
                'RE2102', 'Owner Advances Received', 'liability_current').id,
        })

    def _next(self):
        type(self)._seq += 1
        return type(self)._seq

    def _project(self):
        seq = self._next()
        return self.env['realestate.project'].create({
            'name': 'Certification Project %d' % seq,
            'code': 'CRT%03d' % seq,
            'company_id': self.company.id,
        })

    def _partner(self, name='Partner'):
        return self.env['res.partner'].create(
            {'name': '%s %d' % (name, self._next())})

    def _contractor(self, retention=0.0):
        seq = self._next()
        partner = self.env['res.partner'].create({
            'name': 'Contractor Vendor %d' % seq, 'supplier_rank': 1})
        return self.env['realestate.contractor'].create({
            'name': 'Contractor %d' % seq, 'partner_id': partner.id,
            'retention_pct': retention})

    def _work_item(self, rate=100.0):
        seq = self._next()
        return self.env['realestate.work.item'].create({
            'name': 'Work Item %d' % seq,
            'code': 'CWI%04d' % seq,
            'uom_id': self.uom_unit.id,
            'default_rate': rate,
        })

    def _boq(self, project, quantities=((100.0, 50.0),), approve=True):
        boq = self.BOQ.create({'project_id': project.id})
        for qty, rate in quantities:
            self.BOQLine.create({
                'boq_id': boq.id,
                'work_item_id': self._work_item(rate).id,
                'quantity': qty,
                'unit_rate': rate,
                'uom_id': self.uom_unit.id,
            })
        if approve:
            boq.action_approve()
        return boq

    def _certificate(self, project, contractor, boq_line=None, qty=0.0,
                     **kwargs):
        vals = {
            'project_id': project.id,
            'contractor_id': contractor.id,
            'period_end': self.today,
            'retention_pct': contractor.retention_pct,
        }
        vals.update(kwargs)
        certificate = self.Certificate.create(vals)
        if boq_line is not None:
            self._cert_line(certificate, boq_line, qty)
        return certificate

    def _cert_line(self, certificate, boq_line, qty):
        return self.CertLine.create({
            'certificate_id': certificate.id,
            'boq_line_id': boq_line.id,
            'qty': qty,
            'unit_rate': boq_line.unit_rate,
        })

    def _owner_billing(self, project, partner, pct, amount=1_000_000.0,
                       retention_pct=0.0):
        return self.Billing.create({
            'project_id': project.id,
            'partner_id': partner.id,
            'contract_value': amount,
            'current_certified_pct': pct,
            'retention_pct': retention_pct,
            'period_end': self.today,
        })
