# -*- coding: utf-8 -*-
"""M7 — certificates, retention, advances and owner billing."""

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ConstructionCommon


class CertificationCommon(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.Analytic = self.env['realestate.construction.analytic']
        self.Retention = self.env['realestate.construction.retention']
        self.Advance = self.env['realestate.construction.advance']
        self.project = self._project()
        self.contractor = self._contractor(retention=5.0)
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')
        self._configure_construction_accounts()

    def _actual(self, project=None):
        return sum(self.Analytic.actual_by_cost_code(
            project or self.project).values())


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificateClaimAndCertificate(CertificationCommon):

    def test_certified_defaults_to_what_was_applied_for(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        self.assertEqual(certificate.certified_amount, 100_000.0)
        self.assertEqual(certificate.disallowed_amount, 0.0)

    def test_more_cannot_be_certified_than_was_claimed(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        with self.assertRaises(ValidationError):
            certificate.certified_amount = 120_000.0

    def test_an_application_is_a_separate_act_from_a_certificate(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        certificate.action_submit()
        self.assertEqual(certificate.state, 'submitted')
        certificate.action_certify()
        self.assertEqual(certificate.state, 'certified')
        self.assertEqual(certificate.certified_by_id, self.env.user)
        self.assertTrue(certificate.certified_on)

    def test_a_rejected_application_is_kept(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        certificate.action_submit()
        certificate.action_reject()
        self.assertEqual(certificate.state, 'rejected')
        self.assertEqual(certificate.applied_amount, 100_000.0)

    def test_the_legacy_percentage_still_produces_an_amount(self):
        certificate = self._certificate(
            self.project, self.contractor, pct=40.0,
            contract_value=250_000.0)
        self.assertEqual(certificate.applied_amount, 100_000.0)
        self.assertEqual(certificate.gross_amount, 100_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificateCeiling(CertificationCommon):

    def test_cumulative_is_read_from_the_database_not_the_form(self):
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        first = self._certificate(self.project, self.contractor,
                                  amount=400_000.0, package=package)
        first.action_certify()

        second = self._certificate(self.project, self.contractor,
                                   amount=300_000.0, package=package)
        self.assertEqual(second.previous_certified_amount, 400_000.0,
                         "Nobody typed this. The register answered it.")
        self.assertEqual(second.cumulative_certified_amount, 700_000.0)

    def test_certifying_beyond_the_contract_needs_a_variation(self):
        package = self._package(self.project, self.contractor,
                                value=1_000_000.0, award=True)
        first = self._certificate(self.project, self.contractor,
                                  amount=900_000.0, package=package)
        first.action_certify()
        second = self._certificate(self.project, self.contractor,
                                   amount=200_000.0, package=package)
        with self.assertRaises(UserError):
            second.action_certify()

    def test_without_a_contract_base_there_is_no_invented_ceiling(self):
        """`has_authorised_base` is False — not a ceiling of zero."""
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        self.assertFalse(certificate.has_authorised_base)
        certificate.action_certify()
        self.assertEqual(certificate.state, 'certified')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRetention(CertificationCommon):

    def _certified_bill(self, amount=100_000.0, retention_pct=5.0):
        certificate = self._certificate(
            self.project, self.contractor, amount=amount,
            retention_pct=retention_pct, cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        return certificate

    def test_the_bill_holds_retention_in_a_liability_account(self):
        certificate = self._certified_bill()
        bill = certificate.vendor_bill_id
        account = self.company.construction_retention_account_id

        retention_lines = bill.line_ids.filtered(
            lambda l: l.account_id == account)
        self.assertTrue(retention_lines)
        self.assertEqual(sum(retention_lines.mapped('balance')), -5_000.0,
                         "A credit of 5,000 — money owed, not money saved.")
        self.assertEqual(self._actual(), 100_000.0)

    def test_retention_never_reaches_the_cost_report(self):
        certificate = self._certified_bill()
        account = self.company.construction_retention_account_id
        retention_lines = certificate.vendor_bill_id.line_ids.filtered(
            lambda l: l.account_id == account)
        self.assertFalse(
            any(retention_lines.mapped('analytic_distribution')),
            "A balance-sheet movement with a cost code would understate cost.")

    def test_the_register_records_what_was_withheld(self):
        certificate = self._certified_bill()
        movement = certificate.retention_movement_id
        self.assertEqual(movement.movement_type, 'hold')
        self.assertEqual(movement.amount, 5_000.0)
        self.assertEqual(movement.move_id, certificate.vendor_bill_id)
        self.assertEqual(
            self.Retention.balance(project=self.project,
                                   contractor=self.contractor), 5_000.0)

    def test_a_release_is_a_document_with_a_stage_and_a_reason(self):
        self._certified_bill()
        release = self._retention_release(
            self.project, self.contractor, amount=2_500.0,
            stage='practical_completion', reason='Handover certificate issued.')
        release.action_confirm()

        self.assertEqual(release.state, 'confirmed')
        self.assertTrue(release.vendor_bill_id)
        self.assertEqual(
            self.Retention.balance(project=self.project,
                                   contractor=self.contractor), 2_500.0)

    def test_releasing_retention_is_not_a_second_cost(self):
        self._certified_bill()
        before = self._actual()
        release = self._retention_release(self.project, self.contractor,
                                          amount=2_500.0)
        release.action_confirm()
        self.assertEqual(self._actual(), before)

    def test_more_cannot_be_released_than_is_held(self):
        self._certified_bill()
        release = self._retention_release(self.project, self.contractor,
                                          amount=8_000.0)
        with self.assertRaises(UserError):
            release.action_confirm()

    def test_a_confirmed_release_is_not_cancelled_behind_the_ledger(self):
        self._certified_bill()
        release = self._retention_release(self.project, self.contractor,
                                          amount=1_000.0)
        release.action_confirm()
        with self.assertRaises(UserError):
            release.action_cancel()

    def test_the_register_is_not_edited(self):
        certificate = self._certified_bill()
        with self.assertRaises(UserError):
            certificate.retention_movement_id.amount = 1.0

    def test_retention_without_a_configured_account_refuses_to_post(self):
        self.company.construction_retention_account_id = False
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0)
        certificate.action_certify()
        with self.assertRaises(UserError):
            certificate.action_create_vendor_bill()
        self.assertFalse(certificate.vendor_bill_id)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestAdvances(CertificationCommon):

    def _paid_advance(self, amount=200_000.0):
        advance = self._advance(self.project, self.contractor, amount)
        advance.action_confirm()
        advance.action_create_vendor_bill()
        return advance

    def test_an_advance_is_not_project_cost(self):
        advance = self._paid_advance()
        self.assertEqual(self._actual(), 0.0)
        self.assertEqual(advance.outstanding_amount, 200_000.0)
        self.assertEqual(advance.state, 'paid')

    def test_the_advance_lands_in_the_asset_account(self):
        advance = self._paid_advance()
        account = self.company.construction_advance_account_id
        lines = advance.vendor_bill_id.line_ids.filtered(
            lambda l: l.account_id == account)
        self.assertEqual(sum(lines.mapped('balance')), 200_000.0)

    def test_recovery_reduces_what_is_outstanding(self):
        advance = self._paid_advance(100_000.0)
        certificate = self._certificate(self.project, self.contractor,
                                        amount=300_000.0, retention_pct=0.0,
                                        cost_code=self.civil)
        certificate.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 60_000.0})]})
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        advance.invalidate_recordset()
        self.assertEqual(advance.recovered_amount, 60_000.0)
        self.assertEqual(advance.outstanding_amount, 40_000.0)
        self.assertEqual(certificate.net_payable, 240_000.0)
        self.assertEqual(self._actual(), 300_000.0,
                         "Recovering an advance does not reduce the cost of "
                         "the work it was advanced against.")

    def test_recovery_cannot_exceed_the_advance(self):
        advance = self._paid_advance(100_000.0)
        certificate = self._certificate(self.project, self.contractor,
                                        amount=300_000.0, retention_pct=0.0)
        certificate.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 150_000.0})]})
        with self.assertRaises(UserError):
            certificate.action_certify()

    def test_recovery_across_two_certificates_stops_at_the_advance(self):
        advance = self._paid_advance(100_000.0)
        first = self._certificate(self.project, self.contractor,
                                  amount=300_000.0, retention_pct=0.0)
        first.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 70_000.0})]})
        first.action_certify()

        second = self._certificate(self.project, self.contractor,
                                   amount=300_000.0, retention_pct=0.0)
        second.write({'recovery_line_ids': [(0, 0, {
            'advance_id': advance.id, 'amount': 40_000.0})]})
        with self.assertRaises(UserError):
            second.action_certify()

    def test_an_advance_cannot_be_recovered_from_another_contractor(self):
        advance = self._paid_advance(100_000.0)
        other = self._contractor()
        certificate = self._certificate(self.project, other, amount=200_000.0)
        with self.assertRaises(ValidationError):
            certificate.write({'recovery_line_ids': [(0, 0, {
                'advance_id': advance.id, 'amount': 10_000.0})]})

    def test_a_billed_advance_is_not_edited(self):
        advance = self._paid_advance(100_000.0)
        with self.assertRaises(UserError):
            advance.amount = 500_000.0


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificateImmutability(CertificationCommon):

    def test_a_certified_amount_cannot_be_rewritten(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        certificate.action_certify()
        with self.assertRaises(UserError):
            certificate.certified_amount = 250_000.0

    def test_the_disallowance_survives_certification(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0)
        certificate.certified_amount = 80_000.0
        certificate.disallowance_reason = 'Blockwork not inspected.'
        certificate.action_certify()

        self.assertEqual(certificate.applied_amount, 100_000.0)
        self.assertEqual(certificate.certified_amount, 80_000.0)
        self.assertEqual(certificate.disallowed_amount, 20_000.0)

    def test_a_certificate_does_not_pay_itself(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=0.0)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        with self.assertRaises(UserError):
            certificate.action_mark_paid()
        self.assertEqual(certificate.state, 'invoiced')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificateCostDimension(CertificationCommon):

    def test_certified_cost_reaches_its_cost_code(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=0.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        by_code = self.Analytic.actual_by_cost_code(self.project)
        self.assertEqual(by_code.get(self.civil.id), 100_000.0)

    def test_the_certificate_does_not_move_budget_or_commitment(self):
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 3_000_000.0)])
        before = self.Controls.project_totals(self.project)

        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        after = self.Controls.project_totals(self.project)

        self.assertEqual(after['current_budget'], before['current_budget'])
        self.assertEqual(after['current_commitment'],
                         before['current_commitment'])
        self.assertEqual(after['actual_cost'], before['actual_cost'] + 100_000.0)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestBOQAuthorisedQuantity(CertificationCommon):

    def test_the_authorised_quantity_starts_at_the_boq_quantity(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        self.assertEqual(line.authorised_quantity, 100.0)

    def test_certifying_beyond_the_authorised_quantity_is_refused(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        certificate = self._certificate(
            self.project, self.contractor, boq_line=line, qty=120.0,
            contract_value=10_000_000.0)
        with self.assertRaises(UserError):
            certificate.action_certify()

    def test_two_certificates_cannot_certify_the_same_quantity(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        first = self._certificate(self.project, self.contractor,
                                  boq_line=line, qty=100.0,
                                  contract_value=10_000_000.0)
        second = self._certificate(self.project, self.contractor,
                                   boq_line=line, qty=100.0,
                                   contract_value=10_000_000.0)
        first.action_certify()
        with self.assertRaises(UserError):
            second.action_certify()

    def test_an_approved_variation_raises_the_authorised_quantity(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        order = self._change_order(
            self.project, lines=[(self.civil, 'commitment', 50_000.0)])
        self._approve_and_implement(order)
        line.apply_variation(50.0, order, reason='Extra retaining wall.')

        line.invalidate_recordset()
        self.assertEqual(line.authorised_quantity, 150.0)
        self.assertEqual(line.quantity, 100.0,
                         "The original quantity is history, not a running "
                         "total.")

        certificate = self._certificate(self.project, self.contractor,
                                        boq_line=line, qty=140.0,
                                        contract_value=10_000_000.0)
        certificate.action_certify()
        self.assertEqual(certificate.state, 'certified')

    def test_a_variation_needs_an_approved_change_order(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        draft = self._change_order(
            self.project, lines=[(self.civil, 'commitment', 50_000.0)])
        with self.assertRaises(UserError):
            line.apply_variation(50.0, draft, reason='Not approved yet.')

    def test_an_approved_boq_is_revised_not_edited(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        with self.assertRaises(UserError):
            boq.line_ids[0].quantity = 500.0

    def test_over_certification_is_visible_rather_than_floored(self):
        boq = self._boq(self.project, quantities=((100.0, 1_000.0),))
        line = boq.line_ids[0]
        certificate = self._certificate(self.project, self.contractor,
                                        boq_line=line, qty=100.0,
                                        contract_value=10_000_000.0)
        certificate.action_certify()
        line.invalidate_recordset()
        self.assertEqual(line.remaining_qty, 0.0)
        self.assertFalse(line.is_over_certified)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestOwnerBilling(CertificationCommon):

    def test_owner_retention_is_an_asset_not_lost_revenue(self):
        billing = self._owner_billing(self.project, amount=500_000.0,
                                      retention_pct=10.0)
        billing.action_certify()
        billing.action_create_customer_invoice()

        invoice = billing.customer_invoice_id
        account = self.company.construction_owner_retention_account_id
        lines = invoice.line_ids.filtered(lambda l: l.account_id == account)
        self.assertEqual(sum(lines.mapped('balance')), 50_000.0,
                         "Retention the owner holds is still ours to collect.")
        self.assertEqual(invoice.amount_total, 450_000.0)

    def test_owner_retention_without_an_account_refuses_to_post(self):
        self.company.construction_owner_retention_account_id = False
        billing = self._owner_billing(self.project, amount=500_000.0,
                                      retention_pct=10.0)
        billing.action_certify()
        with self.assertRaises(UserError):
            billing.action_create_customer_invoice()
        self.assertFalse(billing.customer_invoice_id)

    def test_owner_billing_carries_a_company(self):
        billing = self._owner_billing(self.project, amount=100_000.0)
        self.assertEqual(billing.company_id, self.company)


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRetentionDisclosure(CertificationCommon):

    def test_a_correctly_posted_certificate_is_no_longer_disclosed(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()

        Disclosure = self.env['realestate.construction.retention.disclosure']
        self.assertEqual(Disclosure.understatement_for(self.project), 0.0)
        self.assertFalse(Disclosure.warning_for(self.project))

    def test_a_legacy_certificate_is_still_disclosed(self):
        certificate = self._certificate(self.project, self.contractor,
                                        amount=100_000.0, retention_pct=5.0,
                                        cost_code=self.civil)
        certificate.action_certify()
        certificate.action_create_vendor_bill()
        # Simulate a pre-M7 posting: the bill exists, the liability does not.
        certificate.sudo().write({'retention_posted_correctly': False})

        Disclosure = self.env['realestate.construction.retention.disclosure']
        self.assertEqual(Disclosure.understatement_for(self.project), 5_000.0)
        self.assertTrue(Disclosure.warning_for(self.project))


@tagged('post_install', '-at_install', 'atmta_construction')
class TestRemainingBOQForecast(CertificationCommon):
    """M3 withheld this method until M7 made certified quantities trustworthy."""

    def setUp(self):
        super().setUp()
        self._baselined(self.project, 10_000_000.0, code=self.civil)

    def _line_for(self, forecast, cost_code):
        return forecast.line_ids.filtered(
            lambda l: l.cost_code_id == cost_code)[:1]

    def _boq_on_civil(self, quantity=100.0, rate=1_000.0, unpriced=False):
        boq = self._boq(self.project, quantities=(), approve=False)
        self.BOQLine.create({
            'boq_id': boq.id,
            'work_item_id': self._work_item(rate).id,
            'quantity': quantity,
            'unit_rate': rate,
            'uom_id': self.uom_unit.id,
            'cost_code_id': self.civil.id,
        })
        if unpriced:
            # Set after creation: the work item's default rate fills an empty
            # rate on create, which would quietly price the very line the test
            # needs unpriced.
            boq.line_ids[0].unit_rate = 0.0
        boq.action_approve()
        return boq

    def test_the_method_is_no_longer_disabled(self):
        from odoo.addons.real_estate_construction.models.forecast import (
            DISABLED_METHODS)
        self.assertEqual(DISABLED_METHODS, {})

    def test_it_forecasts_the_uncertified_authorised_quantity(self):
        boq = self._boq_on_civil(quantity=100.0, rate=1_000.0)
        certificate = self._certificate(
            self.project, self.contractor, boq_line=boq.line_ids[0], qty=30.0,
            contract_value=10_000_000.0)
        certificate.action_certify()

        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.method = 'remaining_boq'

        self.assertTrue(line.has_etc)
        self.assertEqual(line.etc_amount, 70_000.0)

    def test_an_approved_variation_reaches_the_forecast(self):
        boq = self._boq_on_civil(quantity=100.0, rate=1_000.0)
        order = self._change_order(
            self.project, lines=[(self.civil, 'commitment', 50_000.0)])
        self._approve_and_implement(order)
        boq.line_ids[0].apply_variation(50.0, order, reason='Extra wall.')

        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.method = 'remaining_boq'

        self.assertEqual(line.etc_amount, 150_000.0)

    def test_an_unpriced_boq_line_refuses_to_answer(self):
        self._boq_on_civil(quantity=100.0, unpriced=True)

        forecast = self._forecast(self.project)
        line = self._line_for(forecast, self.civil)
        line.method = 'remaining_boq'

        self.assertFalse(line.has_etc)
        self.assertEqual(line.forecast_status, 'insufficient_data')
        self.assertIn('no rate', line.status_reason or '')


@tagged('post_install', '-at_install', 'atmta_construction')
class TestCertificateUpgrade(CertificationCommon):
    """The fields M7 adds must not rewrite what pre-M7 certificates said."""

    def test_a_pre_m7_certificate_keeps_its_gross_amount(self):
        certificate = self._certificate(
            self.project, self.contractor, contract_value=1_000_000.0,
            pct=10.0)
        certificate.action_certify()
        self.assertEqual(certificate.gross_amount, 100_000.0)

        # Reproduce the upgrade: the new columns are empty and recomputed
        # against a record that is already certified.
        self.env.cr.execute(
            "UPDATE realestate_construction_payment_certificate "
            "SET applied_amount = NULL, certified_amount = NULL WHERE id = %s",
            (certificate.id,))
        certificate.invalidate_recordset()
        certificate.modified(['contract_value'])
        self.env['realestate.construction.payment.certificate']._recompute_model(
            ['applied_amount', 'certified_amount', 'gross_amount'])
        certificate.invalidate_recordset()

        self.assertEqual(certificate.applied_amount, 100_000.0)
        self.assertEqual(certificate.certified_amount, 100_000.0)
        self.assertEqual(certificate.gross_amount, 100_000.0)

    def test_a_pre_m7_certificate_is_still_disclosed_as_mis_posted(self):
        certificate = self._certificate(
            self.project, self.contractor, contract_value=1_000_000.0,
            pct=10.0)
        self.assertFalse(
            certificate.retention_posted_correctly,
            "Nothing back-dates a posting that was never made. The register "
            "is not seeded with movements the ledger never saw.")
