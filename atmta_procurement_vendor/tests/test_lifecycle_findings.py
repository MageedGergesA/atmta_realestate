# -*- coding: utf-8 -*-
"""Regressions found by the procurement lifecycle run, on vendor governance."""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

ROLE = 'atmta_roles.group_procurement_'


@tagged('post_install', '-at_install', 'atmta_vendor')
class TestVendorLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        cls.today = fields.Date.context_today(env['res.partner'])
        cls.company.procurement_qualification_allow_self_approval = False
        cls.Qualification = env['realestate.procurement.vendor.qualification']
        cls.Restriction = env['realestate.procurement.vendor.restriction']
        cls.Profile = env['realestate.procurement.vendor.profile']
        cls.trade = env['realestate.procurement.vendor.category'].create(
            {'name': 'Findings trade', 'code': 'VLF-TRD'})
        cls.template = env[
            'realestate.procurement.qualification.template'].create({
                'name': 'Findings template', 'code': 'VLF-TPL',
                'company_id': cls.company.id})

        def user(login, *roles):
            return new_test_user(
                env, login=login, email='%s@example.com' % login,
                company_id=cls.company.id,
                groups=','.join(['base.group_user']
                                + [ROLE + role for role in roles]))
        cls.assessor = user('vlf_assessor', 'qualification_assessor')
        cls.approver = user('vlf_approver', 'qualification_approver')
        cls.both = user('vlf_both', 'qualification_assessor',
                        'qualification_approver')
        cls.other_assessor = user('vlf_other', 'qualification_assessor',
                                  'qualification_approver')
        cls.manager = user('vlf_manager', 'manager')

    def _vendor(self, name):
        return self.env['res.partner'].create({'name': name,
                                               'supplier_rank': 1})

    def _pending(self, vendor, created_by, assessed_by):
        qualification = self.Qualification.with_user(created_by).create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade.id, 'template_id': self.template.id,
            'effective_date': self.today,
        })
        qualification = qualification.with_user(assessed_by)
        qualification.action_submit()
        qualification.action_start_review()
        qualification.action_assess()
        qualification.action_request_approval()
        return qualification

    # -- item 6: the qualification approver, holding only that role --------
    def test_qualification_approver_alone_approves(self):
        vendor = self._vendor('Approver Alone Co')
        qualification = self._pending(vendor, self.assessor, self.assessor)
        Form(qualification.with_user(self.approver))
        qualification.with_user(self.approver).action_approve()
        self.assertEqual(qualification.state, 'approved')
        self.assertEqual(qualification.profile_id.governance_status, 'active')

    def test_qualification_approver_alone_opens_and_activates_a_restriction(self):
        vendor = self._vendor('Restricted Alone Co')
        restriction = self.Restriction.with_user(self.approver).create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'restriction_type': 'sourcing_suspension',
            'reason': 'Failed site audit', 'effective_from': self.today,
        })
        form = Form(restriction.with_user(self.approver))
        self.assertEqual(form.open_order_count, 0)
        restriction.with_user(self.approver).action_activate()
        self.assertEqual(restriction.state, 'active')

    # -- item 6: the procurement manager and the AVL -----------------------
    def test_manager_runs_the_approved_vendor_list(self):
        form = Form(self.env['realestate.procurement.avl.report'].with_user(
            self.manager))
        report = form.save()
        report.action_compute()

    # -- item 9: a deactivated vendor can be reactivated -------------------
    def test_a_deactivated_vendor_can_be_reactivated(self):
        profile = self.Profile._get_or_create(self._vendor('Dormant Co'),
                                              self.company)
        profile.action_start_review()
        profile.action_activate()
        profile.action_deactivate()
        self.assertEqual(profile.governance_status, 'inactive')
        profile.action_activate()
        self.assertEqual(profile.governance_status, 'active')

    def test_a_suspended_vendor_still_needs_the_restriction_lifted(self):
        vendor = self._vendor('Suspended Co')
        restriction = self.Restriction.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'restriction_type': 'sourcing_suspension',
            'reason': 'Fraud investigation', 'effective_from': self.today,
        })
        restriction.action_activate()
        profile = restriction.profile_id
        self.assertEqual(profile.governance_status, 'suspended')
        with self.assertRaises(UserError):
            profile.action_activate()

    # -- item 10: separation of duties is about who assessed ---------------
    def test_the_person_who_assessed_cannot_approve(self):
        vendor = self._vendor('Assessed By Both Co')
        # Created by one assessor, assessed by the other.
        qualification = self._pending(vendor, self.assessor, self.both)
        self.assertEqual(qualification.assessor_id, self.both)
        with self.assertRaises(UserError):
            qualification.with_user(self.both).action_approve()
        self.assertEqual(qualification.state, 'pending_approval')

    def test_the_creator_who_did_not_assess_may_approve(self):
        vendor = self._vendor('Created By Other Co')
        qualification = self._pending(vendor, self.other_assessor, self.both)
        qualification.with_user(self.other_assessor).action_approve()
        self.assertEqual(qualification.state, 'approved')
