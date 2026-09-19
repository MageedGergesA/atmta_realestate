# -*- coding: utf-8 -*-
"""Regressions for what the construction lifecycle run found in quality."""
from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestQualityFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Inspection = cls.env['realestate.construction.inspection']
        cls.Observation = cls.env[
            'realestate.construction.quality.observation']
        cls.NCR = cls.env['realestate.construction.ncr']
        cls.project = cls.env['realestate.project'].create({
            'name': 'Quality Project', 'code': 'QLT001',
            'company_id': cls.env.company.id})

    def _button_context(self, model, view_xmlid, button):
        """The context the form's button actually sends."""
        view = self.env.ref(view_xmlid)
        arch = etree.fromstring(
            self.env[model].get_view(view.id, 'form')['arch'])
        node, = arch.xpath("//button[@name='%s']" % button)
        return safe_eval(node.get('context') or '{}')

    def _rejected_inspection(self):
        inspection = self.Inspection.create({'project_id': self.project.id})
        inspection.action_start()
        inspection.action_record_result('rejected')
        return inspection

    def test_raise_reinspection_opens_the_reinspection_once(self):
        inspection = self._rejected_inspection()
        context = self._button_context(
            self.Inspection._name,
            'atmta_construction_quality.view_inspection_form',
            'action_create_reinspection')
        clicked = inspection.with_context(**context)

        action = clicked.action_create_reinspection()
        self.assertIsInstance(action, dict)
        self.assertEqual(action['res_model'], self.Inspection._name)
        again = clicked.action_create_reinspection()
        self.assertEqual(again['res_id'], action['res_id'])
        self.assertEqual(len(inspection.reinspection_ids), 1)

    def test_verification_inspection_opens_the_reinspection_once(self):
        inspection = self._rejected_inspection()
        ncr = self.NCR.create({
            'title': 'NCR', 'description': 'Out of tolerance.',
            'project_id': self.project.id,
            'source_inspection_id': inspection.id})
        # A reinspection already raised from the inspection is the one the
        # NCR verifies with, not a second visit to the same wall.
        existing = inspection.action_create_reinspection()
        context = self._button_context(
            self.NCR._name, 'atmta_construction_quality.view_ncr_form',
            'action_create_reinspection')
        clicked = ncr.with_context(**context)

        action = clicked.action_create_reinspection()
        self.assertIsInstance(action, dict)
        self.assertEqual(action['res_id'], existing.id)
        self.assertEqual(clicked.action_create_reinspection()['res_id'],
                         existing.id)
        self.assertEqual(len(inspection.reinspection_ids), 1)

    def test_a_closed_observation_does_not_step_back(self):
        observation = self.Observation.create({
            'description': 'Sealant missing.', 'project_id': self.project.id})
        observation.action_require_action()
        observation.corrective_action = 'Sealant applied.'
        observation.action_ready_for_verification()
        observation.action_verify()
        self.assertEqual(observation.state, 'closed')

        with self.assertRaises(UserError):
            observation.action_require_action()
        with self.assertRaises(UserError):
            observation.action_ready_for_verification()
        self.assertEqual(observation.state, 'closed')
