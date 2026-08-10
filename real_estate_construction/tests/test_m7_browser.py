# -*- coding: utf-8 -*-
"""M7 — the real-browser gate for the certification screens.

The certificate form changed more than any other screen in this programme: it
now carries a claim, a certificate, a disallowance, retention and advance
recovery. A form that raises on load would enforce none of it, and an ORM test
cannot see that.

Nothing here types a credential — `start_tour` authenticates server-side.
"""

from odoo.tests.common import HttpCase, tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificationBrowser(ConstructionCommon, HttpCase):

    def test_the_certification_screens_open_and_render(self):
        project = self._project()
        contractor = self._contractor(retention=5.0)
        civil = self._cost_code('SUB-CIV-TOUR', 'Civil', 'subcontract')
        self._configure_construction_accounts()

        advance = self._advance(project, contractor, 200_000.0)
        advance.action_confirm()
        advance.action_create_vendor_bill()

        certificate = self._certificate(project, contractor,
                                        amount=400_000.0, retention_pct=5.0,
                                        cost_code=civil)
        certificate.certified_amount = 350_000.0
        certificate.disallowance_reason = 'Blockwork not inspected.'
        certificate.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 50_000.0})]})
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        self.env.ref('base.user_admin').groups_id |= self.env.ref(
            'real_estate_construction.group_construction_manager')
        self.env.flush_all()

        self.start_tour('/odoo', 'construction_certification_tour',
                        login='admin', timeout=180)
