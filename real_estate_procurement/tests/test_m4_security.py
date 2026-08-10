# -*- coding: utf-8 -*-
"""M4Z, M4AA, M4AF, M4AB — tests 16, 17, 18 and 19.

Approval authority, confidential evidence, concurrency and what an upgrade
does to a database full of legacy suppliers.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged
from odoo.tools.sql import index_exists

from .common import M4Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Approval(M4Common):
    """TEST 16 — the assessor is not the approver."""

    def test_16_the_assessor_cannot_approve_their_own_assessment(self):
        self.company.procurement_qualification_allow_self_approval = False
        vendor = self._vendor('Self Approved')
        qualification = self.Qualification.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade_concrete.id,
            'template_id': self.template.id, 'effective_date': self.today,
            'assessor_id': self.env.user.id,
        })
        qualification.action_submit()
        qualification.action_start_review()
        qualification.action_assess()
        qualification.action_request_approval()

        with self.assertRaises(UserError):
            qualification.action_approve()
        self.assertEqual(qualification.state, 'pending_approval')
        self.assertFalse(qualification.is_current)

    def test_16_the_rule_is_derived_from_env_user_not_from_the_client(self):
        """No context key, parameter or field can name a different approver."""
        self.company.procurement_qualification_allow_self_approval = False
        vendor = self._vendor('Context Spoof')
        qualification = self.Qualification.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade_concrete.id,
            'template_id': self.template.id, 'effective_date': self.today,
        })
        qualification.action_submit()
        qualification.action_start_review()
        qualification.action_assess()
        qualification.action_request_approval()

        other = self.env['res.users'].create({
            'name': 'Someone Else', 'login': 'm4-other-%s' % self._next(),
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(UserError):
            qualification.with_context(
                approved_by=other.id, default_approver_id=other.id
            ).action_approve()

    def test_16_a_second_person_can_approve(self):
        self.company.procurement_qualification_allow_self_approval = False
        approver = self._governance_user(
            'm4-approver', 'group_procurement_qualification_approver')
        vendor = self._vendor('Properly Approved')
        qualification = self.Qualification.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade_concrete.id,
            'template_id': self.template.id, 'effective_date': self.today,
        })
        qualification.action_submit()
        qualification.action_start_review()
        qualification.action_assess()
        qualification.action_request_approval()

        qualification.with_user(approver).action_approve()
        self.assertEqual(qualification.state, 'approved')
        self.assertEqual(qualification.approver_id, approver)

    def test_16_a_rejection_needs_a_reason_and_keeps_the_evidence(self):
        vendor = self._vendor('Rejected Co')
        template = self._template('With Evidence', requirements=[
            {'name': 'Registration', 'obligation': 'mandatory',
             'requirement_type': 'document', 'code': 'REG'},
        ])
        qualification = self.Qualification.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade_concrete.id,
            'template_id': template.id, 'effective_date': self.today,
        })
        qualification.response_ids.write({'document_number': 'CR-1'})
        qualification.action_submit()

        with self.assertRaises(UserError):
            qualification.action_reject()

        qualification.action_reject(reason='Registration is out of date')
        self.assertEqual(qualification.state, 'rejected')
        self.assertEqual(qualification.result, 'not_qualified')
        self.assertIn('out of date', qualification.rejection_reason)
        self.assertEqual(qualification.response_ids.document_number, 'CR-1')

    def _governance_user(self, login, *groups):
        return self.env['res.users'].create({
            'name': login, 'login': '%s-%s' % (login, self._next()),
            # Odoo refuses to post a tracked message on behalf of a user with
            # no address, and approving a qualification posts one.
            'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id] + [
                self.env.ref('real_estate_procurement.%s' % group).id
                for group in groups])],
        })


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4ConfidentialEvidence(M4Common):
    """TEST 17 — reading a vendor is not reading their bank statements."""

    def setUp(self):
        super().setUp()
        self.confidential_template = self._template(
            'With Financials', requirements=[
                {'name': 'Audited accounts', 'obligation': 'mandatory',
                 'requirement_type': 'document', 'confidential': True,
                 'code': 'FIN'},
                {'name': 'Trade licence', 'obligation': 'mandatory',
                 'requirement_type': 'document', 'code': 'LIC'},
            ])
        self.vendor = self._vendor('Sensitive Co')
        self.qualification = self._qualify(
            self.vendor, self.trade_concrete,
            template=self.confidential_template, approve=False,
            responses={'FIN': {'document_number': 'ACC-2025'},
                       'LIC': {'document_number': 'LIC-2025'}})
        self.financials = self.qualification.response_ids.filtered(
            lambda r: r.requirement_code == 'FIN')
        self.licence = self.qualification.response_ids.filtered(
            lambda r: r.requirement_code == 'LIC')

    def _user(self, login, *groups):
        return self.env['res.users'].create({
            'name': login, 'login': '%s-%s' % (login, self._next()),
            'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id] + [
                self.env.ref('real_estate_procurement.%s' % group).id
                for group in groups])],
        })

    def test_17_a_requester_reads_the_vendor_and_not_the_evidence(self):
        requester = self._user('m4-requester', 'group_procurement_requester')
        # They can read the partner — that is Rule 1 working.
        self.vendor.with_user(requester).read(['name'])
        with self.assertRaises(AccessError):
            self.qualification.response_ids.with_user(requester).read(
                ['document_number'])

    def test_17_a_buyer_sees_the_licence_and_not_the_accounts(self):
        buyer = self._user('m4-buyer', 'group_procurement_user')
        self.licence.with_user(buyer).read(['document_number'])
        with self.assertRaises(AccessError):
            self.financials.with_user(buyer).read(['document_number'])

    def test_17_an_assessor_sees_both(self):
        assessor = self._user(
            'm4-assessor', 'group_procurement_qualification_assessor')
        self.licence.with_user(assessor).read(['document_number'])
        self.financials.with_user(assessor).read(['document_number'])

    def test_17_a_confidential_document_is_not_a_loose_attachment(self):
        """The classic hole: an attachment with no `res_model` is readable by
        anybody who can read attachments at all."""
        attachment = self.env['ir.attachment'].create({
            'name': 'audited-accounts.pdf', 'raw': b'numbers',
        })
        self.financials.write({'attachment_ids': [(6, 0, attachment.ids)]})

        attachment.invalidate_recordset()
        self.assertEqual(attachment.res_model,
                         'realestate.procurement.qualification.response')
        self.assertEqual(attachment.res_id, self.financials.id)

        buyer = self._user('m4-buyer-att', 'group_procurement_user')
        with self.assertRaises(AccessError):
            attachment.with_user(buyer).read(['raw'])

        assessor = self._user(
            'm4-assessor-att', 'group_procurement_qualification_assessor')
        attachment.with_user(assessor).read(['name'])

    def test_17_a_buyer_still_sees_the_eligibility_answer(self):
        """Confidentiality must not make the service useless to a buyer.

        They cannot read the financial evidence; they can still be told
        whether the vendor may be invited, which is the whole point of
        returning a decision rather than the file.
        """
        self._qualify(self.vendor, self.trade_electrical)
        buyer = self._user('m4-buyer-elig', 'group_procurement_user')
        result = self.env['realestate.procurement.vendor.eligibility'
                          ].with_user(buyer).check_vendor_eligibility(
            self.vendor, company=self.company, category=self.trade_electrical)
        self.assertTrue(result['eligible'])


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Concurrency(M4Common):
    """TEST 18 — exactly one current qualification per scope.

    What the harness can and cannot prove is stated plainly: Odoo's test
    framework runs every test inside one transaction on one connection, so a
    genuine two-process race cannot be staged here. What is tested is the
    thing that would decide such a race — the partial unique index — plus the
    lock the approval takes before it reads.
    """

    def test_18_the_database_enforces_one_current_per_scope(self):
        self.assertTrue(index_exists(
            self.env.cr, 'proc_qualification_one_current'),
            'without the index, two committed approvals both win')

    def test_18_a_second_approval_supersedes_rather_than_duplicating(self):
        vendor = self._vendor('Competing Assessments')
        first = self._qualify(vendor, self.trade_concrete)
        second = self._qualify(vendor, self.trade_concrete)

        self.assertTrue(second.is_current)
        self.assertFalse(first.is_current)
        self.assertEqual(first.state, 'superseded')
        current = self.Qualification.search([
            ('partner_id', '=', vendor.id),
            ('category_id', '=', self.trade_concrete.id),
            ('is_current', '=', True)])
        self.assertEqual(len(current), 1)

    def test_18_the_index_refuses_a_forced_duplicate(self):
        """Bypass the workflow entirely and the database still says no."""
        vendor = self._vendor('Forced Duplicate')
        self._qualify(vendor, self.trade_concrete)
        intruder = self.Qualification.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade_concrete.id,
            'template_id': self.template.id, 'effective_date': self.today,
        })
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                intruder._write_engine({'is_current': True})
                self.env.cr.flush()

    def test_18_project_endorsements_do_not_collide_with_the_general_one(self):
        """`COALESCE(project_id, 0)` — NULLs would not collide, and two
        current general qualifications is exactly the collision that matters."""
        vendor = self._vendor('General And Endorsed')
        general = self._qualify(vendor, self.trade_concrete)
        endorsement = self._qualify(vendor, self.trade_concrete,
                                    project=self.project)
        self.assertTrue(general.is_current)
        self.assertTrue(endorsement.is_current)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Migration(M4Common):
    """TEST 19 — an upgrade describes the supplier base and qualifies nobody."""

    def test_19_purchase_history_does_not_become_a_qualification(self):
        vendor = self._vendor('Ten Years Of Orders')
        product = self._product(price=100.0, vendor=vendor)
        order = self.PO.create({
            'partner_id': vendor.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_qty': 5.0, 'price_unit': 100.0,
                                   'taxes_id': [(5, 0, 0)]})],
        })
        order.button_confirm()

        self.env['res.partner']._classify_procurement_vendors(vendor)

        self.assertEqual(vendor.procurement_vendor_class,
                         'has_active_purchase_history')
        self.assertFalse(self.Qualification.search_count(
            [('partner_id', '=', vendor.id)]))
        self.assertEqual(order.state, 'purchase')
        self._set_vendor_policy('required_for_sourcing')
        self.assertFalse(self._eligibility(
            vendor, self.trade_concrete)['qualified'])

    def test_19_a_price_list_alone_is_classified_as_such(self):
        vendor = self._vendor('Quoted Never Ordered')
        self._product(price=100.0, vendor=vendor)
        self.env['res.partner']._classify_procurement_vendors(vendor)
        self.assertEqual(vendor.procurement_vendor_class,
                         'has_current_vendor_pricelist')

    def test_19_a_qualified_vendor_is_classified_as_assessed(self):
        vendor = self._vendor('Already Assessed')
        self._qualify(vendor, self.trade_concrete)
        self.env['res.partner']._classify_procurement_vendors(vendor)
        self.assertEqual(vendor.procurement_vendor_class, 'assessed')

    def test_19_a_strict_company_produces_the_rollout_worklist(self):
        vendor = self._vendor('Needs Work')
        self._product(price=100.0, vendor=vendor)
        self.company.procurement_vendor_policy = 'required_for_sourcing'
        self.env['res.partner']._classify_procurement_vendors(vendor)
        self.assertEqual(vendor.procurement_vendor_class,
                         'needs_qualification')

    def test_19_classification_is_idempotent(self):
        vendor = self._vendor('Classified Twice')
        self._product(price=100.0, vendor=vendor)
        first = self.env['res.partner']._classify_procurement_vendors(vendor)
        label = vendor.procurement_vendor_class
        second = self.env['res.partner']._classify_procurement_vendors(vendor)
        self.assertEqual(first, second)
        self.assertEqual(vendor.procurement_vendor_class, label)
        self.assertFalse(self.env[
            'realestate.procurement.vendor.profile'].search_count(
                [('partner_id', '=', vendor.id)]),
            'classification must not create governance records')

    def test_19_a_profile_is_created_when_somebody_starts_governing(self):
        vendor = self._vendor('Governed On Demand')
        Profile = self.env['realestate.procurement.vendor.profile']
        self.assertFalse(Profile.search_count([('partner_id', '=', vendor.id)]))

        vendor.action_open_vendor_governance()
        self.assertEqual(
            Profile.search_count([('partner_id', '=', vendor.id)]), 1)
        vendor.action_open_vendor_governance()
        self.assertEqual(
            Profile.search_count([('partner_id', '=', vendor.id)]), 1,
            'and only one, however many times the button is pressed')


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Registers(M4Common):
    """M4I and M4AE — the two screens, exercised rather than assumed to work."""

    def test_the_avl_answers_as_of_a_date(self):
        eligible = self._vendor('AVL Eligible')
        suspended = self._vendor('AVL Suspended')
        self._qualify(eligible, self.trade_concrete)
        self._qualify(suspended, self.trade_concrete)
        self._restrict(suspended, 'sourcing_suspension', reason='Incident')
        self._set_vendor_policy('required_for_sourcing')

        report = self.env['realestate.procurement.avl.report'].create({
            'company_id': self.company.id,
            'category_id': self.trade_concrete.id,
            'as_of_date': self.today,
        })
        report.action_compute()
        rows = {line.partner_id: line for line in report.line_ids}

        self.assertTrue(rows[eligible].eligible)
        self.assertTrue(rows[eligible].qualification_id)
        self.assertFalse(rows[suspended].eligible)
        self.assertEqual(rows[suspended].status_label, 'Suspended')
        self.assertTrue(rows[suspended].reason)
        self.assertGreaterEqual(report.excluded_count, 1)

    def test_the_avl_is_derived_and_has_no_manual_toggle(self):
        """M4I — no `is_approved_vendor = True` with nothing behind it."""
        self.assertNotIn(
            'is_approved_vendor', self.env['res.partner']._fields)
        line_fields = self.env[
            'realestate.procurement.avl.report.line']._fields
        self.assertTrue(line_fields['eligible'].readonly)
        self.assertTrue(line_fields['qualification_id'].readonly)

    def test_the_audit_reports_and_does_not_repair(self):
        vendor = self._vendor('Broken Data')
        qualification = self._qualify(
            vendor, self.trade_concrete,
            effective=self.today - timedelta(days=100),
            expiry=self.today - timedelta(days=1),
            approved_on=self.today - timedelta(days=100))
        self.assertTrue(qualification.is_current)

        audit = self.env['realestate.procurement.vendor.audit'].create({
            'company_id': self.company.id})
        audit.action_scan()
        kinds = set(audit.line_ids.mapped('kind'))

        self.assertIn('expired_still_current', kinds)
        qualification.invalidate_recordset()
        self.assertTrue(qualification.is_current,
                        'the audit reports; it does not tidy up')

    def test_the_audit_catches_a_blocking_failure_that_was_approved(self):
        template = self._template('Blocking', requirements=[
            {'name': 'Insurance', 'obligation': 'mandatory',
             'requirement_type': 'boolean', 'blocking': True, 'code': 'INS'},
        ])
        vendor = self._vendor('Approved Anyway')
        qualification = self._qualify(
            vendor, self.trade_concrete, template=template, approve=False,
            responses={'INS': {'value_bool': True}})
        qualification.action_request_approval()
        qualification.action_approve()
        # The evidence is withdrawn after the fact, engine-side.
        qualification.response_ids.with_context(
            re_qualification_engine=True).write({'value_bool': False})

        audit = self.env['realestate.procurement.vendor.audit'].create({
            'company_id': self.company.id})
        audit.action_scan()
        self.assertIn('blocking_failure_but_qualified',
                      set(audit.line_ids.mapped('kind')))
