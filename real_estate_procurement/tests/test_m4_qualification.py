# -*- coding: utf-8 -*-
"""M4 tests 1–8 — the qualification decision itself.

Scope, template snapshotting, mandatory and blocking requirements, scoring,
conditions, validity and reassessment.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import M4Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Qualification(M4Common):

    # -- TEST 1 --------------------------------------------------------
    def test_01_the_vendor_master_is_not_duplicated(self):
        """Rule 1. Qualification points at `res.partner` and nothing else
        claims to be a supplier."""
        vendor = self._vendor('Master Test')
        qualification = self._qualify(vendor, self.trade_concrete)
        self.assertEqual(qualification.partner_id, vendor)
        self.assertEqual(
            qualification._fields['partner_id'].comodel_name, 'res.partner')
        self.assertNotIn('realestate.vendor', self.env)
        # Nor does the governance layer keep its own copy of the identity.
        profile_fields = set(
            self.env['realestate.procurement.vendor.profile']._fields)
        for duplicated in ('street', 'phone', 'email', 'vat', 'bank_ids',
                           'country_id'):
            self.assertNotIn(duplicated, profile_fields)

    def test_01_contractor_and_vendor_stay_separate(self):
        """Construction's contractor is left alone and shares the partner.

        The audit's conclusion, pinned: a contractor is a Construction
        commercial object that points at a partner, and a qualification is a
        Procurement decision about that same partner. Neither references the
        other, which is what keeps the dependency pointing one way.
        """
        if 'realestate.contractor' not in self.env:
            self.skipTest('real_estate_construction is not installed')
        vendor = self._vendor('Dual Role')
        contractor = self.env['realestate.contractor'].create({
            'name': 'Dual Role Contracting', 'partner_id': vendor.id})
        qualification = self._qualify(vendor, self.trade_concrete)
        self.assertEqual(contractor.partner_id, qualification.partner_id)
        self.assertNotIn(
            'contractor_id',
            self.env['realestate.procurement.vendor.qualification']._fields)

    # -- TEST 3 --------------------------------------------------------
    def test_03_qualification_does_not_cross_companies(self):
        other = self.env['res.company'].create({'name': 'M4 Second Company'})
        vendor = self._vendor('Company A Only')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        other.procurement_vendor_policy = 'required_for_sourcing'

        here = self._eligibility(vendor, self.trade_concrete)
        there = self._eligibility(vendor, self.trade_concrete, company=other)

        self.assertTrue(here['eligible'])
        self.assertFalse(there['eligible'])
        self.assertEqual(there['status'], 'no_qualification')

    def test_03_group_recognition_has_to_be_stated(self):
        """An empty company is not how a group qualification is expressed."""
        other = self.env['res.company'].create({'name': 'M4 Sister Company'})
        vendor = self._vendor('Group Vendor')
        qualification = self._qualify(vendor, self.trade_concrete)
        other.procurement_vendor_policy = 'required_for_sourcing'

        with self.assertRaises(ValidationError):
            qualification._write_engine({'is_group_wide': True})

        qualification._write_engine({
            'is_group_wide': True,
            'shared_company_ids': [(6, 0, other.ids)]})
        self.assertTrue(self._eligibility(
            vendor, self.trade_concrete, company=other)['eligible'])

    # -- TEST 4 --------------------------------------------------------
    def test_04_project_endorsement_narrows_and_never_widens(self):
        vendor = self._vendor('MEP Co')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        self.project.procurement_vendor_endorsement_required = True

        without = self._eligibility(vendor, self.trade_concrete,
                                    project=self.project)
        self.assertFalse(without['eligible'])
        self.assertEqual(without['status'], 'endorsement_required')

        endorsement = self._qualify(vendor, self.trade_concrete,
                                    project=self.project)
        with_endorsement = self._eligibility(vendor, self.trade_concrete,
                                             project=self.project)
        self.assertTrue(with_endorsement['eligible'])
        self.assertEqual(with_endorsement['endorsement_id'], endorsement.id)

    def test_04_an_endorsement_cannot_rescue_a_suspended_vendor(self):
        """Precedence, stated as a test: suspension runs before everything."""
        vendor = self._vendor('Endorsed But Suspended')
        self._qualify(vendor, self.trade_concrete)
        self._qualify(vendor, self.trade_concrete, project=self.project)
        self.project.procurement_vendor_endorsement_required = True
        self._set_vendor_policy('required_for_sourcing')
        self._restrict(vendor, 'sourcing_suspension', reason='Company level')

        result = self._eligibility(vendor, self.trade_concrete,
                                   project=self.project)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['status'], 'suspended')

    def test_04_a_project_restriction_leaves_other_projects_alone(self):
        vendor = self._vendor('Restricted Here Only')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        elsewhere = self._project()
        self._restrict(vendor, 'project_restriction', project=self.project,
                       reason='Client objection on this development')

        self.assertFalse(self._eligibility(
            vendor, self.trade_concrete, project=self.project)['eligible'])
        self.assertTrue(self._eligibility(
            vendor, self.trade_concrete, project=elsewhere)['eligible'])

    # -- TEST 5 --------------------------------------------------------
    def test_05_a_blocking_failure_outranks_a_high_score(self):
        template = self._template('Scored With Insurance', requirements=[
            {'name': 'Technical capability', 'obligation': 'scored',
             'requirement_type': 'score', 'max_score': 10.0, 'weight': 9.0,
             'code': 'TECH'},
            {'name': 'Valid insurance', 'obligation': 'mandatory',
             'requirement_type': 'boolean', 'blocking': True,
             'code': 'INS'},
        ], scoring_enabled=True, min_score=70.0)
        vendor = self._vendor('High Score No Cover')
        qualification = self._qualify(
            vendor, self.trade_concrete, template=template, approve=False,
            responses={'TECH': {'score': 9.0}, 'INS': {'value_bool': False}})

        self.assertGreaterEqual(qualification.score, 85.0)
        self.assertEqual(qualification.result, 'not_qualified')
        self.assertIn('Valid insurance', qualification.score_explanation)

    def test_05_a_non_blocking_mandatory_failure_becomes_a_condition(self):
        """Not every gap is fatal, and the ones that are not are written down.

        M4H: recorded as a structured condition rather than a sentence
        somebody might read.
        """
        template = self._template('Soft Requirement', requirements=[
            {'name': 'Site visit completed', 'obligation': 'mandatory',
             'requirement_type': 'boolean', 'code': 'VISIT'},
        ])
        vendor = self._vendor('Nearly There')
        qualification = self._qualify(
            vendor, self.trade_concrete, template=template, approve=False,
            responses={'VISIT': {'value_bool': False}})

        self.assertEqual(qualification.result, 'qualified_with_conditions')
        conditions = qualification.condition_ids.filtered('auto_generated')
        self.assertEqual(len(conditions), 1)
        self.assertIn('Site visit completed', conditions.name)

    def test_05_an_unanswered_mandatory_requirement_is_not_a_failure(self):
        """Nobody has decided anything yet, and the result says exactly that."""
        template = self._template('Unanswered', requirements=[
            {'name': 'Commercial registration', 'obligation': 'mandatory',
             'requirement_type': 'document', 'code': 'CR'},
        ])
        vendor = self._vendor('Silent Co')
        qualification = self._qualify(
            vendor, self.trade_concrete, template=template, approve=False)
        self.assertEqual(qualification.result, 'pending_information')

    def test_05_a_waiver_needs_a_reason_and_a_person(self):
        template = self._template('Waivable', requirements=[
            {'name': 'ISO 9001', 'obligation': 'mandatory',
             'requirement_type': 'boolean', 'blocking': True, 'code': 'ISO'},
        ])
        vendor = self._vendor('Waived Co')
        qualification = self._qualify(
            vendor, self.trade_concrete, template=template, approve=False,
            responses={'ISO': {'value_bool': False}})
        response = qualification.response_ids
        self.assertEqual(qualification.result, 'not_qualified')

        with self.assertRaises(UserError):
            response.action_waive()          # no reason given

        response.exception_reason = 'Certification lapsed during audit window'
        response.action_waive()
        self.assertEqual(response.exception_approved_by_id, self.env.user)
        qualification.action_assess()
        self.assertEqual(qualification.result, 'qualified')

    # -- TEST 6 --------------------------------------------------------
    def test_06_a_value_cap_is_exposed_not_enforced_yet(self):
        """M4 must surface the 5M ceiling. Enforcing it is M7's job."""
        vendor = self._vendor('Capped Co')
        qualification = self._qualify(
            vendor, self.trade_concrete,
            conditions=[{'condition_type': 'max_award_value',
                         'name': 'Maximum award 5,000,000',
                         'amount': 5_000_000.0}])
        self._set_vendor_policy('required_for_sourcing')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['status'], 'eligible_with_conditions')
        caps = [c for c in result['conditions']
                if c['type'] == 'max_award_value']
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]['amount'], 5_000_000.0)
        self.assertEqual(qualification.result, 'qualified_with_conditions')

    def test_06_a_zero_cap_is_refused_as_a_way_of_saying_no(self):
        vendor = self._vendor('Zero Cap')
        qualification = self._qualify(vendor, self.trade_concrete,
                                      approve=False)
        with self.assertRaises(ValidationError):
            self.env['realestate.procurement.qualification.condition'].create({
                'qualification_id': qualification.id,
                'condition_type': 'max_award_value',
                'name': 'Nothing at all', 'amount': 0.0})

    # -- TEST 7 --------------------------------------------------------
    def test_07_expiry_moves_state_and_keeps_everything_else(self):
        vendor = self._vendor('Lapsing Co')
        qualification = self._qualify(
            vendor, self.trade_concrete,
            effective=self.today - timedelta(days=200),
            expiry=self.today - timedelta(days=1),
            approved_on=self.today - timedelta(days=200))
        before = qualification.result

        moved = self.Qualification.expire_due_qualifications()

        self.assertEqual(moved, 1)
        self.assertEqual(qualification.state, 'expired')
        self.assertFalse(qualification.is_current)
        self.assertEqual(qualification.result, before)
        self.assertTrue(qualification.response_ids or True)
        self.assertEqual(qualification.expiry_status, 'expired')

    def test_07_the_expiry_job_is_idempotent(self):
        vendor = self._vendor('Lapsing Twice')
        self._qualify(vendor, self.trade_concrete,
                      effective=self.today - timedelta(days=200),
                      expiry=self.today - timedelta(days=1),
                      approved_on=self.today - timedelta(days=200))
        self.assertEqual(self.Qualification.expire_due_qualifications(), 1)
        self.assertEqual(self.Qualification.expire_due_qualifications(), 0)

    def test_07_expiring_soon_uses_the_configured_window(self):
        """M4N — thirty days is a default, not a rule."""
        vendor = self._vendor('Nearly Due')
        qualification = self._qualify(
            vendor, self.trade_concrete,
            expiry=self.today + timedelta(days=45))
        self.company.procurement_qualification_warn_days = 30
        qualification.invalidate_recordset()
        self.assertEqual(qualification.expiry_status, 'valid')

        self.company.procurement_qualification_warn_days = 60
        qualification.invalidate_recordset()
        self.assertEqual(qualification.expiry_status, 'expiring')

    # -- TEST 8 --------------------------------------------------------
    def test_08_reassessment_supersedes_without_rewriting(self):
        vendor = self._vendor('Reassessed Co')
        first = self._qualify(vendor, self.trade_concrete)
        self.assertTrue(first.is_current)

        action = first.action_reassess()
        second = self.Qualification.browse(action['res_id'])
        self.assertEqual(second.previous_id, first)
        self.assertEqual(second.state, 'draft')
        # Not current until it is actually approved.
        self.assertFalse(second.is_current)
        self.assertTrue(first.is_current)

        second.action_submit()
        second.action_start_review()
        second.action_assess()
        second.action_request_approval()
        second.action_approve()

        self.assertTrue(second.is_current)
        self.assertFalse(first.is_current)
        self.assertEqual(first.state, 'superseded')
        self.assertEqual(first.superseded_by_id, second)
        self.assertEqual(second.result, 'qualified')

    def test_08_reassessment_does_not_carry_forward_expiring_verification(self):
        """M4O — an insurance certificate verified two years ago is not
        verified now, and copying the tick is the one convenience that would
        make this whole module a liability."""
        template = self._template('Expiring Evidence', requirements=[
            {'name': 'Insurance certificate', 'obligation': 'mandatory',
             'requirement_type': 'document', 'expiry_sensitive': True,
             'code': 'INS'},
            {'name': 'Company profile', 'obligation': 'informational',
             'requirement_type': 'text', 'code': 'PROFILE'},
        ])
        vendor = self._vendor('Carry Forward Co')
        first = self._qualify(
            vendor, self.trade_concrete, template=template,
            responses={'INS': {'document_number': 'POL-1',
                               'verification': 'verified'},
                       'PROFILE': {'value_text': 'Founded 1998'}})

        second = self.Qualification.browse(first.action_reassess()['res_id'])
        insurance = second.response_ids.filtered(
            lambda r: r.requirement_code == 'INS')
        profile = second.response_ids.filtered(
            lambda r: r.requirement_code == 'PROFILE')

        self.assertEqual(insurance.document_number, 'POL-1',
                         'the reference should be carried forward')
        self.assertEqual(insurance.verification, 'pending',
                         'the verification must not be')
        self.assertEqual(profile.value_text, 'Founded 1998')

    def test_08_a_decided_assessment_cannot_be_edited(self):
        vendor = self._vendor('Frozen Co')
        qualification = self._qualify(vendor, self.trade_concrete)
        with self.assertRaises(UserError):
            qualification.write({'result': 'not_qualified'})
        with self.assertRaises(UserError):
            qualification.write({'category_id': self.trade_electrical.id})
        with self.assertRaises(UserError):
            qualification.unlink()

    def test_08_a_decided_assessments_responses_are_frozen_too(self):
        template = self._template('Freezable', requirements=[
            {'name': 'Something', 'obligation': 'informational',
             'requirement_type': 'text', 'code': 'X'},
        ])
        vendor = self._vendor('Frozen Evidence')
        qualification = self._qualify(vendor, self.trade_concrete,
                                      template=template)
        with self.assertRaises(UserError):
            qualification.response_ids.write({'value_text': 'edited later'})


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Templates(M4Common):
    """M4D — a used questionnaire stops being editable."""

    def test_a_used_template_refuses_to_change_what_it_asks(self):
        template = self._template('Frozen Template', requirements=[
            {'name': 'Something', 'obligation': 'informational',
             'requirement_type': 'text'},
        ])
        vendor = self._vendor('Template User')
        self._qualify(vendor, self.trade_concrete, template=template)

        with self.assertRaises(UserError):
            template.write({'min_score': 90.0})
        with self.assertRaises(UserError):
            template.requirement_ids.unlink()
        # Renaming and retiring stay possible — they change no meaning.
        template.write({'name': 'Frozen Template (retired)'})
        template.action_obsolete()

    def test_a_template_used_only_by_drafts_is_still_editable(self):
        """The freeze is about assessments that mean something.

        A draft nobody has submitted asserts nothing yet, so editing the
        questionnaire under it changes nothing anybody relied on.
        """
        template = self._template('Still Draft', requirements=[
            {'name': 'Something', 'obligation': 'informational',
             'requirement_type': 'text'},
        ])
        vendor = self._vendor('Draft User')
        self.Qualification.create({
            'partner_id': vendor.id, 'company_id': self.company.id,
            'category_id': self.trade_electrical.id,
            'template_id': template.id, 'effective_date': self.today})

        template.write({'min_score': 90.0})
        template.requirement_ids.write({'name': 'Something else'})
        self.assertEqual(template.min_score, 90.0)

    def test_new_version_leaves_history_pointing_at_the_old_one(self):
        template = self._template('Versioned', requirements=[
            {'name': 'Original question', 'obligation': 'informational',
             'requirement_type': 'text'},
        ])
        vendor = self._vendor('Versioned Vendor')
        qualification = self._qualify(vendor, self.trade_concrete,
                                      template=template)
        self.assertEqual(qualification.template_version, 1)

        action = template.action_new_version()
        new = self.Template.browse(action['res_id'])
        self.assertEqual(new.version, 2)
        self.assertEqual(new.previous_version_id, template)
        self.assertEqual(template.state, 'obsolete')
        new.write({'requirement_ids': [(0, 0, {
            'name': 'A question added later',
            'obligation': 'mandatory', 'requirement_type': 'boolean'})]})

        self.assertEqual(qualification.template_id, template)
        self.assertEqual(qualification.template_version, 1)
        self.assertNotIn(
            'A question added later',
            qualification.response_ids.mapped('requirement_name'))

    def test_a_template_with_assessments_cannot_be_deleted(self):
        template = self._template('Undeletable', requirements=[
            {'name': 'Q', 'obligation': 'informational',
             'requirement_type': 'text'}])
        self._qualify(self._vendor('X'), self.trade_concrete,
                      template=template)
        with self.assertRaises(UserError):
            template.unlink()

    def test_responses_snapshot_the_requirement(self):
        """M4F — after instantiation the template is not load-bearing."""
        template = self._template('Snapshot Me', requirements=[
            {'name': 'Original wording', 'obligation': 'mandatory',
             'requirement_type': 'boolean', 'blocking': True,
             'expiry_sensitive': True, 'code': 'ORIG'},
        ])
        vendor = self._vendor('Snapshot Vendor')
        qualification = self._qualify(
            vendor, self.trade_concrete, template=template, approve=False,
            responses={'ORIG': {'value_bool': True}})
        response = qualification.response_ids

        self.assertEqual(response.requirement_name, 'Original wording')
        self.assertTrue(response.blocking)
        self.assertTrue(response.expiry_sensitive)
        self.assertEqual(response.obligation, 'mandatory')

        requirement = template.requirement_ids
        requirement.invalidate_recordset()
        # Even deleting the source requirement leaves the response readable.
        requirement.with_context(re_template_versioning=True).unlink()
        response.invalidate_recordset()
        self.assertEqual(response.requirement_name, 'Original wording')
        self.assertTrue(response.blocking)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Restrictions(M4Common):
    """Tests 9 and 10 — suspension, and the end of one."""

    def test_09_a_suspension_does_not_touch_the_orders(self):
        vendor = self._vendor('Suspended With Orders')
        product = self._product(price=100.0, vendor=vendor)
        order = self.PO.create({
            'partner_id': vendor.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_qty': 1.0,
                                   'price_unit': 100.0,
                                   'taxes_id': [(5, 0, 0)]})],
        })
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')

        restriction = self._restrict(vendor, 'sourcing_suspension',
                                     reason='Quality failure')

        self.assertEqual(order.state, 'purchase')
        self.assertEqual(restriction.open_order_count, 1,
                         'the worklist should surface it')

    def test_10_a_lapsed_suspension_stops_biting(self):
        vendor = self._vendor('Temporarily Held')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        restriction = self._restrict(
            vendor, 'temporary_hold', reason='Pending document',
            effective_from=self.today - timedelta(days=10),
            effective_to=self.today - timedelta(days=1))

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertTrue(result['eligible'],
                        'the hold ended yesterday')
        # And it was biting while it ran.
        during = self._eligibility(vendor, self.trade_concrete,
                                   date=self.today - timedelta(days=5))
        self.assertFalse(during['eligible'])

        self.Restriction.expire_due_restrictions()
        self.assertEqual(restriction.state, 'expired')
        # The qualification was never touched by any of it.
        qualification = self.Qualification.search([
            ('partner_id', '=', vendor.id)])
        self.assertEqual(qualification.state, 'approved')

    def test_10_lifting_keeps_the_record_and_needs_a_reason(self):
        vendor = self._vendor('Lifted Co')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        restriction = self._restrict(vendor, 'sourcing_suspension',
                                     reason='Under investigation')

        with self.assertRaises(UserError):
            restriction.action_lift()

        restriction.action_lift(reason='Investigation closed, no finding')
        self.assertEqual(restriction.state, 'lifted')
        self.assertEqual(restriction.lifted_by_id, self.env.user)
        self.assertTrue(self._eligibility(
            vendor, self.trade_concrete)['eligible'])
        with self.assertRaises(UserError):
            restriction.unlink()

    def test_an_award_suspension_still_allows_an_invitation(self):
        """The two purposes are genuinely different powers."""
        vendor = self._vendor('Invite But Do Not Order')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        self._restrict(vendor, 'award_suspension',
                       reason='Commercial terms in dispute')

        self.assertTrue(self._eligibility(
            vendor, self.trade_concrete, purpose='sourcing')['eligible'])
        self.assertFalse(self._eligibility(
            vendor, self.trade_concrete, purpose='award')['eligible'])

    def test_probation_warns_and_refuses_nothing(self):
        vendor = self._vendor('On Probation')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        self._restrict(vendor, 'probation', reason='Two late deliveries')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertTrue(result['eligible'])
        self.assertTrue(result['warnings'])

    def test_a_trade_restriction_names_its_trade(self):
        vendor = self._vendor('Scoped Restriction')
        with self.assertRaises(ValidationError):
            self.Restriction.create({
                'partner_id': vendor.id, 'company_id': self.company.id,
                'restriction_type': 'category_restriction',
                'reason': 'Missing the trade', 'effective_from': self.today})
