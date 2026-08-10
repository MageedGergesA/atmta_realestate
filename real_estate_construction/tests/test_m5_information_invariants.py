# -*- coding: utf-8 -*-
"""M5 — the four invariants the information-control layer is built around.

Written before the models.

```
    A   an RFI moves no money
    B   a reference is to an exact revision, forever
    C   a superseded submittal revision stays readable
    D   a sent transmittal is evidence, and evidence does not change
```
"""

from odoo.tests import tagged

from .common import ConstructionCommon


@tagged('post_install', '-at_install', 'atmta_construction')
class TestInformationInvariants(ConstructionCommon):

    def setUp(self):
        super().setUp()
        self.Controls = self.env['realestate.construction.controls']
        self.project = self._project()
        self.contractor = self._contractor()
        self.civil = self._cost_code('SUB-CIV', 'Civil', 'subcontract')

    # ------------------------------------------------------------------
    def test_a_an_rfi_moves_no_money(self):
        """An RFI may say "this could cost 2M". The budget does not care."""
        self._baselined(self.project, 10_000_000.0, code=self.civil)
        self._po(self.project, self.contractor, [(self.civil, 4_000_000.0)])
        before = self.Controls.project_totals(self.project)

        rfi = self._rfi(self.project, cost_impact='potential',
                        estimated_cost_impact=2_000_000.0)
        rfi.action_open()
        rfi.write({'official_response': 'Use the thicker section.',
                   'response_author_id': self.env.user.id})
        rfi.action_answer()

        after = self.Controls.project_totals(self.project)
        self.assertEqual(after['current_budget'], before['current_budget'])
        self.assertEqual(after['current_commitment'],
                         before['current_commitment'])
        self.assertEqual(after['actual_cost'], before['actual_cost'])
        self.assertEqual(after['current_budget'], 10_000_000.0)

        # The only thing it may do is start M4's process.
        event = rfi.action_create_change_event()
        rfi.invalidate_recordset()
        self.assertTrue(rfi.change_event_id)
        self.assertEqual(rfi.change_event_id.source, 'rfi')
        self.assertEqual(
            rfi.change_event_id.estimated_cost_impact, 2_000_000.0)
        self.assertEqual(
            self.Controls.project_totals(self.project)['current_budget'],
            10_000_000.0,
            "Creating a change event is not approving one.")

    def test_b_a_reference_is_to_an_exact_revision_forever(self):
        """An RFI raised against Rev B still says Rev B when Rev C lands."""
        document = self._document(self.project, number='A-ARC-DRG-1001')
        rev_b = self._revision(document, code='B', issue=True)

        rfi = self._rfi(self.project, document_revision_ids=[(6, 0, rev_b.ids)])
        rfi.action_open()

        rev_c = self._revision(document, code='C', issue=True)
        document.invalidate_recordset()
        rfi.invalidate_recordset()

        self.assertEqual(document.current_revision_id, rev_c)
        self.assertEqual(
            rfi.document_revision_ids, rev_b,
            "The question was asked about Rev B, and it always was.")
        self.assertEqual(rev_b.state, 'superseded')
        self.assertTrue(rev_b.exists())

    def test_c_a_superseded_submittal_revision_stays_readable(self):
        """Rev 0 revise-and-resubmit, Rev 1 approved, both survive."""
        submittal = self._submittal(self.project, self.contractor)
        rev0 = submittal.current_revision_id
        submittal.action_submit()
        rev0.action_respond('revise_resubmit', 'Section sizes not shown.')

        rev1 = submittal.action_create_revision()
        submittal.action_submit()
        rev1.action_respond('approved', 'Fit for construction.')
        submittal.invalidate_recordset()

        self.assertEqual(rev0.response, 'revise_resubmit')
        self.assertEqual(rev0.revision_code, '0')
        self.assertFalse(rev0.is_current)
        self.assertEqual(rev1.response, 'approved')
        self.assertTrue(rev1.is_current)
        self.assertEqual(submittal.current_revision_id, rev1)
        self.assertEqual(len(submittal.revision_ids), 2)
        self.assertEqual(submittal.state, 'responded')

    def test_d_a_sent_transmittal_is_evidence(self):
        """Sent with Rev B; Rev C later becomes current; the record says B."""
        document = self._document(self.project, number='A-ARC-DRG-1002')
        rev_b = self._revision(document, code='B', issue=True)

        transmittal = self._transmittal(self.project, revisions=rev_b)
        transmittal.action_send()
        line = transmittal.line_ids

        self.assertEqual(line.revision_code, 'B')
        self.assertEqual(line.document_number, 'A-ARC-DRG-1002')

        self._revision(document, code='C', issue=True)
        transmittal.invalidate_recordset()

        self.assertEqual(
            line.revision_code, 'B',
            "A transmittal records what was sent, not what is current.")
        self.assertEqual(line.document_revision_id, rev_b)
