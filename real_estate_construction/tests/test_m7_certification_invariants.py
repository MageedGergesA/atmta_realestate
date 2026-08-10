# -*- coding: utf-8 -*-
"""M7 — the invariants, written before the models that must satisfy them.

Every test here states a rule about money that must remain true no matter what
the certification screens are later persuaded to do. They are deliberately
blunt: each one is the failure a project accountant would find months later,
written down while it is still cheap.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificationInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Analytic = self.env['realestate.construction.analytic']
        self.project = self._project()
        self.contractor = self._contractor(retention=5.0)
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self._configure_construction_accounts()

    def _actual(self):
        return sum(self.Analytic.actual_by_cost_code(self.project).values())

    def _retention_account_balance(self, contractor):
        account = self.company.construction_retention_account_id
        lines = self.env['account.move.line'].search([
            ('account_id', '=', account.id),
            ('partner_id', '=', contractor.partner_id.id),
            ('parent_state', '=', 'posted'),
        ])
        return -sum(lines.mapped('balance'))

    # -- A ------------------------------------------------------------------
    def test_a_retention_is_a_liability_not_a_discount_on_cost(self):
        """The work cost what it cost. Retention is money owed, not money saved.

        This is the Phase 0 defect stated as an assertion: a 100,000
        certificate with 5% retention must post 100,000 of cost and 5,000 of
        liability — not 95,000 of cost and silence.
        """
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        bill = certificate.vendor_bill_id
        self.assertEqual(bill.state, 'posted')
        self.assertEqual(bill.amount_total, 95_000.0)

        actual = self._actual()
        self.assertEqual(
            actual, 100_000.0,
            "Actual cost must be the value of the work, gross of retention.")

        held = self._retention_account_balance(self.contractor)
        self.assertEqual(held, 5_000.0,
                         "Retention withheld must sit in a liability account.")

    # -- B ------------------------------------------------------------------
    def test_b_retention_cannot_be_withheld_without_somewhere_to_put_it(self):
        """No silent fallback. An unconfigured account refuses the posting."""
        self.company.construction_retention_account_id = False
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0)
        certificate.action_certify()
        with self.assertRaises(UserError):
            certificate.action_create_vendor_bill()
        self.assertFalse(certificate.vendor_bill_id,
                         "Nothing may post when the liability has no home.")

    # -- C ------------------------------------------------------------------
    def test_c_releasing_retention_never_creates_cost(self):
        """Release moves a liability to a payable. It is not new expenditure."""
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        cost_before = self._actual()
        self.assertEqual(cost_before, 100_000.0)

        release = self._retention_release(self.project, self.contractor,
                                          amount=2_500.0)
        release.action_confirm()

        self.assertEqual(self._actual(), cost_before,
                         "Releasing retention is not a second cost event.")
        self.assertEqual(self._retention_account_balance(self.contractor),
                         2_500.0)

    # -- D ------------------------------------------------------------------
    def test_d_more_retention_cannot_be_released_than_was_held(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        release = self._retention_release(self.project, self.contractor,
                                          amount=9_000.0)
        with self.assertRaises(UserError):
            release.action_confirm()

    # -- E ------------------------------------------------------------------
    def test_e_an_advance_is_an_asset_until_it_is_recovered(self):
        """Paying an advance is not paying for work."""
        advance = self._advance(self.project, self.contractor, 200_000.0)
        advance.action_confirm()
        advance.action_create_vendor_bill()

        self.assertEqual(
            self._actual(), 0.0,
            "An advance buys nothing yet. It is not project cost.")
        self.assertFalse(
            any(advance.vendor_bill_id.line_ids.mapped(
                'analytic_distribution')),
            "An advance with a cost code would be spending that never "
            "happened, filed against work that was never done.")
        self.assertEqual(advance.outstanding_amount, 200_000.0)

    # -- F ------------------------------------------------------------------
    def test_f_recovery_cannot_exceed_what_was_advanced(self):
        advance = self._advance(self.project, self.contractor, 100_000.0)
        advance.action_confirm()
        advance.action_create_vendor_bill()

        certificate = self._certificate(self.project, self.contractor,
                                        amount=500_000.0, retention_pct=0.0)
        certificate.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 150_000.0})]})
        with self.assertRaises(UserError):
            certificate.action_certify()

    # -- G ------------------------------------------------------------------
    def test_g_the_same_work_cannot_be_certified_twice(self):
        """Cumulative certification is answered by the database, not a form."""
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        first = self._certificate(self.project, self.contractor,
                                  amount=600_000.0, package=package)
        first.action_certify()

        second = self._certificate(self.project, self.contractor,
                                   amount=600_000.0, package=package)
        with self.assertRaises(UserError):
            second.action_certify()

    # -- H ------------------------------------------------------------------
    def test_h_a_certified_amount_is_never_rewritten(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        certificate.action_certify()
        with self.assertRaises(UserError):
            certificate.certified_amount = 250_000.0

    # -- I ------------------------------------------------------------------
    def test_i_what_was_applied_for_survives_what_was_certified(self):
        """The contractor's claim is evidence. Certifying less does not erase it."""
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        certificate.certified_amount = 80_000.0
        certificate.action_certify()

        self.assertEqual(certificate.applied_amount, 100_000.0)
        self.assertEqual(certificate.certified_amount, 80_000.0)
        self.assertEqual(certificate.disallowed_amount, 20_000.0)

    # -- J ------------------------------------------------------------------
    def test_j_certified_cost_reaches_the_cost_code_it_was_coded_to(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=0.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        by_code = self.env[
            'realestate.construction.analytic'].actual_by_cost_code(
                self.project)
        self.assertEqual(by_code.get(self.civil.id), 100_000.0,
                         "A certificate is the biggest cost document on the "
                         "job. It must land in the cost report's dimension.")
