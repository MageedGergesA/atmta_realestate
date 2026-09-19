# -*- coding: utf-8 -*-
"""Regressions for what the construction lifecycle run found in certification."""
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form, tagged
from odoo.tools.safe_eval import safe_eval

from .common import CertificationFindingsCommon


@tagged('post_install', '-at_install')
class TestBoqRevisionCertification(CertificationFindingsCommon):
    """B1 — a BOQ revision must not reopen quantity already certified."""

    def test_a_revision_does_not_let_the_same_work_be_certified_twice(self):
        project = self._project()
        contractor = self._contractor()
        boq = self._boq(project, quantities=((2_000.0, 10.0),))
        line = boq.line_ids
        first = self._certificate(project, contractor, boq_line=line,
                                  qty=1_500.0)
        first.action_certify()

        revision = boq.action_create_revision()
        revision.action_approve()
        rev_line = revision.line_ids
        self.assertEqual(rev_line.work_item_id, line.work_item_id)

        # The approved revision retires the BOQ it replaced.
        late = self._certificate(project, contractor, boq_line=line,
                                 qty=100.0)
        with self.assertRaises(UserError):
            late.action_certify()

        # And the revision remembers what its predecessor already paid for.
        whole_again = self._certificate(project, contractor,
                                        boq_line=rev_line, qty=2_000.0)
        with self.assertRaises(UserError):
            whole_again.action_certify()

        remainder = self._certificate(project, contractor,
                                      boq_line=rev_line, qty=500.0)
        remainder.action_certify()
        self.assertEqual(remainder.state, 'certified')

    def test_a_revision_is_told_apart_from_the_boq_it_replaces(self):
        boq = self._boq(self._project())
        revision = boq.action_create_revision()
        self.assertNotEqual(revision.display_name, boq.display_name)

    def test_only_the_current_revision_is_revised(self):
        boq = self._boq(self._project())
        revision = boq.action_create_revision()
        revision.action_approve()
        with self.assertRaises(UserError):
            boq.action_create_revision()


@tagged('post_install', '-at_install')
class TestOwnerBillingFindings(CertificationFindingsCommon):

    def test_b2_previous_percentage_comes_from_the_same_project_and_customer(
            self):
        project, elsewhere = self._project(), self._project()
        customer, other_customer = self._partner('Owner'), self._partner('Owner')

        self._owner_billing(elsewhere, other_customer, 30.0).action_certify()
        self._owner_billing(project, other_customer, 20.0).action_certify()

        billing = self._owner_billing(project, customer, 10.0)
        billing.action_certify()
        self.assertEqual(billing.previous_certified_pct, 0.0)

        following = self._owner_billing(project, customer, 15.0)
        following.action_certify()
        self.assertEqual(following.previous_certified_pct, 10.0)

    def test_b4_an_invoiced_billing_is_not_cancelled_from_under_its_invoice(
            self):
        project = self._project()
        customer = self._partner('Owner')
        billing = self._owner_billing(project, customer, 30.0,
                                      retention_pct=10.0)
        billing.action_certify()
        billing.action_create_customer_invoice()
        invoice = billing.customer_invoice_id
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(self.Retention.balance(project=project, side='owner'),
                         30_000.0)

        with self.assertRaises(UserError):
            billing.action_cancel()
        self.assertEqual(billing.state, 'invoiced')

        # Once the invoice is cancelled nothing was withheld after all, and
        # the register says so.
        invoice.button_cancel()
        billing.action_cancel()
        self.assertEqual(billing.state, 'cancelled')
        self.assertEqual(self.Retention.balance(project=project, side='owner'),
                         0.0)

    def test_ux_sale_contract_availability_is_known_before_the_first_save(
            self):
        """What the web client asks for a new billing: an onchange with no
        values and the form's field spec."""
        Billing = self.Billing
        view = Billing.get_view(False, 'form')
        spec = {node.get('name'): {}
                for node in etree.fromstring(view['arch']).iter('field')
                if not any(p.tag == 'field' for p in node.iterancestors())
                and node.get('name') != 'sale_contract_id'}
        with patch.object(type(Billing), '_sale_contracts_installed',
                          lambda self: True):
            result = Billing.onchange({}, [], spec)
        self.assertTrue(result['value'].get('sale_contract_available'))


@tagged('post_install', '-at_install')
class TestCertificateFindings(CertificationFindingsCommon):

    def test_b6_on_the_form_too(self):
        project = self._project()
        contractor = self._contractor()
        boq = self._boq(project, quantities=((100.0, 10.0), (100.0, 20.0),
                                             (100.0, 30.0)))
        first, second, third = boq.line_ids
        with Form(self.Certificate) as form:
            form.project_id = project
            form.contractor_id = contractor
            for boq_line in (first, second):
                with form.line_ids.new() as line:
                    line.boq_line_id = boq_line
                    line.qty = 10.0
            self.assertEqual(form.applied_amount, 300.0)
            self.assertEqual(form.certified_amount, 300.0)

            form.certified_amount = 250.0
            form.disallowance_reason = 'Not inspected.'
            with form.line_ids.new() as line:
                line.boq_line_id = third
                line.qty = 10.0
            self.assertEqual(form.applied_amount, 600.0)
            self.assertEqual(form.certified_amount, 250.0)

    def test_ux_cancel_is_not_offered_where_it_is_always_refused(self):
        view = self.env.ref(
            'atmta_construction_certification.view_payment_certificate_form')
        arch = etree.fromstring(
            self.Certificate.get_view(view.id, 'form')['arch'])
        button, = arch.xpath("//button[@name='action_cancel']")
        invisible = button.get('invisible')
        posted = {'state': 'invoiced', 'vendor_bill_state': 'posted'}
        draft = {'state': 'draft', 'vendor_bill_state': False}
        self.assertTrue(safe_eval(invisible, posted))
        self.assertFalse(safe_eval(invisible, draft))


@tagged('post_install', '-at_install')
class TestAdvanceSides(CertificationFindingsCommon):

    def test_b10_outstanding_advances_are_read_one_side_at_a_time(self):
        project = self._project()
        contractor = self._contractor()
        self.Advance.create({
            'project_id': project.id, 'contractor_id': contractor.id,
            'amount': 30_000.0}).action_confirm()
        self.Advance.create({
            'project_id': project.id, 'side': 'owner',
            'partner_id': self._partner('Owner').id,
            'amount': 100_000.0}).action_confirm()

        self.assertEqual(self.Advance.outstanding_for(project), 30_000.0)
        self.assertEqual(
            self.Advance.outstanding_for(project, side='owner'), 100_000.0)
