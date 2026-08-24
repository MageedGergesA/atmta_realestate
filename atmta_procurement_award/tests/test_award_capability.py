# -*- coding: utf-8 -*-
"""What Award owns, and the financial line it must not cross.

The M7 integration suites stay in `real_estate_procurement`, where their
Construction-backed fixtures live. What is proved here is the structural claim
Wave 8 makes: Award is authorisation evidence, it owns no financial truth of
its own, and the purchase order remains the thing that creates an obligation.
"""
import inspect

from odoo.tests import TransactionCase, tagged

AWARD_MODELS = ('realestate.procurement.award',
                'realestate.procurement.award.line',
                'realestate.procurement.award.allocation')
#: Names that would mean Award had grown its own commitment truth.
FORBIDDEN_TRUTH = ('commitment_amount', 'committed_amount', 'commitment_total',
                   'actual_amount', 'reserved_amount')


@tagged('post_install', '-at_install', 'atmta_award')
class TestAwardCapability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Award = cls.env['realestate.procurement.award']
        cls.AwardLine = cls.env['realestate.procurement.award.line']

    def test_it_owns_three_models(self):
        for name in AWARD_MODELS:
            self.assertIn(name, self.env)
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_award',
                             "%s is declared by another module." % name)

    def test_it_depends_on_evaluation_and_evaluation_does_not_depend_on_it(self):
        Module = self.env['ir.module.module']
        aw = Module.search([('name', '=', 'atmta_procurement_award')])
        ev = Module.search([('name', '=', 'atmta_procurement_evaluation')])
        self.assertIn('atmta_procurement_evaluation',
                      aw.dependencies_id.mapped('name'))
        self.assertNotIn('atmta_procurement_award',
                         ev.dependencies_id.mapped('name'),
                         "the Evaluation/Award cycle is back")

    def test_it_contributes_the_award_half_of_the_sourcing_event(self):
        event = self.env['realestate.procurement.sourcing.event']
        self.assertIn('award_ids', event._fields)
        self.env.cr.execute("""
            SELECT d.module FROM ir_model_fields f
              JOIN ir_model_data d ON d.model='ir.model.fields' AND d.res_id=f.id
             WHERE f.model='realestate.procurement.sourcing.event'
               AND f.name='award_ids'""")
        row = self.env.cr.fetchone()
        self.assertTrue(row)
        self.assertEqual(row[0], 'atmta_procurement_award')

    # -- the invariant: Award owns no financial truth ----------------------
    def test_award_grows_no_commitment_field_of_its_own(self):
        """Construction's commitment is derived from confirmed orders. A stored
        commitment on the award would be a second source for one number, and
        the two would disagree the first time anything changed."""
        for model in AWARD_MODELS:
            for name in FORBIDDEN_TRUTH:
                self.assertNotIn(
                    name, self.env[model]._fields,
                    "%s.%s would make Award a second source of financial "
                    "truth" % (model, name))

    def test_the_purchase_order_is_still_native(self):
        self.assertNotIn('realestate.procurement.purchase.order', self.env)
        self.assertEqual(self.env['purchase.order']._original_module, 'purchase')
        self.assertEqual(self.env['purchase.order.line']._original_module,
                         'purchase')

    def test_the_award_line_points_at_a_native_order(self):
        field = self.AwardLine._fields.get('purchase_order_id')
        self.assertIsNotNone(field)
        self.assertEqual(field.comodel_name, 'purchase.order')

    # -- authorisation is server-side --------------------------------------
    def _award_source(self):
        """The declaring module's source, not the registry's composed class.

        `type(self.Award)` is assembled at runtime and has no file, so
        `inspect.getsource` on it raises. The question here is what this module
        wrote, so read what this module wrote.
        """
        from odoo.addons.atmta_procurement_award.models import procurement_award
        return inspect.getsource(procurement_award)

    def test_maker_checker_is_enforced_in_python_not_in_a_view(self):
        src = self._award_source()
        self.assertIn('action_approve', src)
        self.assertTrue(
            any(tok in src for tok in ('create_uid', 'submitted_by', 'raised_by',
                                       'requested_by')),
            "nothing in the award compares the approver against its author")

    def test_it_names_no_legacy_group(self):
        """AD-011: an extracted capability must not reach into the monolith
        for a group XML ID."""
        src = self._award_source()
        self.assertNotIn('real_estate_procurement.group_', src)

    def test_it_contributes_its_own_audit_checks(self):
        checks = [c.__name__ for c in
                  self.env['realestate.procurement.evaluation.audit']._audit_checks()]
        for name in ('_check_award_surface',
                     '_check_confirmed_tender_order_without_award',
                     '_check_award_approved_by_its_author',
                     '_check_award_over_tender'):
            self.assertIn(name, checks)

    def test_the_award_sequence_is_not_duplicated(self):
        n = self.env['ir.sequence'].search_count(
            [('code', '=', 'realestate.procurement.award')])
        self.assertLessEqual(n, 1)
