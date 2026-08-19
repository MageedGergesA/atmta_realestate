# -*- coding: utf-8 -*-
"""M8 — the four integrity checks, and what the migration refuses to do.

A gate refuses the next bad act. An audit finds the ones already in the
database. Every M8 check is reachable by a record that predates the milestone,
which is the whole point: switching a control on does not clean up what
happened before it, and pretending otherwise is how an audit becomes decoration.

The migration tests are assertions about **absence**. The tempting shortcut was
to give every historical receipt an inspection marked fully accepted, so the
register arrives complete and this audit reports nothing. That would state on
the record that somebody inspected material nobody inspected.
"""

from odoo.tests import tagged

from .test_m8_inspection import M8InspectionCommon


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8IntegrityChecks(M8InspectionCommon):

    def setUp(self):
        super().setUp()
        self.Audit = self.env['realestate.procurement.evaluation.audit']

    def _keys(self, severity=None):
        report = self.Audit.run(company=self.company)
        return {f['key'] for f in report['findings']
                if severity is None or f['severity'] == severity}

    def test_a_clean_receiving_chain_raises_nothing(self):
        """The control has to be quiet when nothing is wrong, or it is noise."""
        award, order, picking = self._delivered_order()
        self._set_inspection('required')
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty
        sheet.action_record()
        picking.button_validate()

        critical = self._keys('critical')

        for key in ('billed_over_accepted', 'rejected_material_received',
                    'tender_order_uncoded'):
            self.assertNotIn(key, critical)

    def test_rejected_material_shown_as_received_is_found(self):
        """The sheet and the ledger disagreeing about one delivery."""
        award, order, picking = self._delivered_order()
        sheet = self.Inspection._for_picking(picking)
        for line in sheet.line_ids:
            line.accepted_qty = line.received_qty / 2.0
            line.reason = 'Damaged'
        sheet.conclusion = 'Half damaged.'
        # Validate first, record the rejection afterwards — which is exactly
        # how a receipt processed before M8 and inspected later looks.
        picking.button_validate()
        sheet.action_record()

        self.assertIn('rejected_material_received', self._keys('critical'))

    def test_an_uncoded_tender_order_is_found_below_critical(self):
        """Medium, not critical: most will predate M8."""
        award, order, picking = self._delivered_order()
        lines = order.order_line.filtered(lambda l: not l.display_type)
        if 're_cost_code_id' not in lines._fields:
            self.skipTest("Construction is not installed.")
        lines.re_cost_code_id = False

        self.assertIn('tender_order_uncoded', self._keys('medium'))

    def test_an_uninspected_receipt_under_policy_is_found(self):
        award, order, picking = self._delivered_order()
        picking.button_validate()
        self._set_inspection('required')

        self.assertIn('uninspected_receipt', self._keys('low'))

    def test_an_uninspected_receipt_is_not_reported_where_nobody_asked(self):
        """Off is off. An audit that scolds a site for a policy it never set
        is an audit people learn to ignore."""
        award, order, picking = self._delivered_order()
        picking.button_validate()
        self._set_inspection('off')

        self.assertNotIn('uninspected_receipt', self._keys())


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8MigrationCreatesNothing(M8InspectionCommon):
    """What the migration must never do, asserted rather than promised."""

    def _run_migration(self):
        from odoo.modules.migration import load_script
        script = load_script(
            'real_estate_procurement/migrations/18.0.8.0.0/post-migrate.py',
            'post-migrate')
        script.migrate(self.env.cr, '18.0.7.0.0')

    def test_the_migration_invents_no_inspection(self):
        award, order, picking = self._delivered_order()
        picking.button_validate()
        before = self.env[
            'realestate.procurement.receipt.inspection'].search_count([])

        self._run_migration()

        self.assertEqual(
            self.env['realestate.procurement.receipt.inspection'
                     ].search_count([]), before,
            "The migration signed an inspection nobody performed.")

    def test_the_migration_codes_nothing_retrospectively(self):
        award, order, picking = self._delivered_order()
        lines = order.order_line.filtered(lambda l: not l.display_type)
        if 're_cost_code_id' not in lines._fields:
            self.skipTest("Construction is not installed.")
        lines.re_cost_code_id = False
        before = lines.mapped('re_cost_code_id')

        self._run_migration()
        lines.invalidate_recordset()

        self.assertEqual(
            lines.mapped('re_cost_code_id'), before,
            "The migration moved committed money onto a cost code nobody "
            "chose, and left no way to tell which figures were a buyer's "
            "decision and which were a script's guess.")

    def test_running_it_twice_changes_nothing(self):
        award, order, picking = self._delivered_order()
        picking.button_validate()
        Inspection = self.env['realestate.procurement.receipt.inspection']

        self._run_migration()
        first = Inspection.search_count([])
        self._run_migration()

        self.assertEqual(Inspection.search_count([]), first)
