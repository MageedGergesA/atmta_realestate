# -*- coding: utf-8 -*-
"""M6 — the real-browser gate for the quality and site screens.

The server tests prove the rules hold. This proves the screens those rules are
reached through actually open: a form that raises on load enforces nothing, and
an ORM test cannot see it.

Nothing here types a credential — `start_tour` authenticates server-side.
"""

from odoo.tests.common import HttpCase, tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestQualityBrowser(ConstructionCommon, HttpCase):

    def test_the_quality_screens_open_and_render(self):
        project = self._project()
        contractor = self._contractor()

        itp = self._itp(project, contractor, points=(('hold',),))
        itp.title = 'Tour ITP'
        itp.item_ids[0].write({
            'activity': 'Concrete pour',
            'description': 'Concrete pour',
            'acceptance_criteria': 'Slump 75 ± 25 mm',
        })
        itp.action_submit()
        itp.action_approve()
        itp.action_activate()

        inspection = self._inspection(
            project, contractor, itp_id=itp.id, itp_item_id=itp.item_ids[0].id)
        inspection.action_start()
        inspection.write({'checklist_line_ids': [(0, 0, {
            'name': 'Slump',
            'item_type': 'measurement',
            'acceptance_criteria': '75 ± 25 mm',
            'minimum_value': 50.0,
            'maximum_value': 100.0,
            'measured_value': 130.0,
            'passed': 'fail',
            'disposition': 'ncr',
            'disposition_reason': 'Load rejected at the gate.',
        })]})
        inspection.action_record_result('rejected')

        ncr = self._ncr(project, contractor, source_inspection_id=inspection.id)
        ncr.write({'title': 'Tour NCR',
                   'cost_impact': 'potential',
                   'estimated_rework_cost': 42_000.0})

        report = self._daily_report(project)
        self.env['realestate.construction.labor.log'].create({
            'project_id': project.id,
            'contractor_id': contractor.id,
            'date': self.today,
            'trade': 'mason',
            'workers': 5,
            'hours': 8.0,
            'daily_report_id': report.id,
        })

        self.env.ref('base.user_admin').groups_id |= self.env.ref(
            'real_estate_construction.group_construction_manager')
        self.env.flush_all()

        self.start_tour('/odoo', 'construction_quality_tour',
                        login='admin', timeout=180)
