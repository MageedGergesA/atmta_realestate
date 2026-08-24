# -*- coding: utf-8 -*-
"""What this module owns, and the three things it must never own.

The M8 integration suites stay in `real_estate_procurement`, where their
Construction-backed fixtures live. What is proved here is the structural claim
Wave 9 makes: native Odoo keeps the receipt, the order and the ledger, and this
module adds procurement judgement on top of them without duplicating any of the
three or moving money.
"""
from odoo.tests import TransactionCase, tagged

RECEIPT_MODELS = ('realestate.procurement.receipt.inspection',
                  'realestate.procurement.receipt.inspection.line')
PICKING_FIELDS = ('re_inspection_id', 're_inspection_state', 're_inspection_policy')
#: Names that would mean Receipt had grown its own financial truth.
FORBIDDEN_TRUTH = ('commitment_amount', 'committed_amount', 'actual_amount',
                   'actual_cost', 'reserved_amount', 'posted_amount')


@tagged('post_install', '-at_install', 'atmta_receipt')
class TestReceiptCapability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Inspection = cls.env['realestate.procurement.receipt.inspection']
        cls.Audit = cls.env['realestate.procurement.evaluation.audit']

    def test_it_owns_two_models(self):
        for name in RECEIPT_MODELS:
            self.assertIn(name, self.env)
            self.assertEqual(self.env[name]._original_module,
                             'atmta_procurement_receipt',
                             "%s is declared by another module." % name)

    def test_native_odoo_keeps_what_native_odoo_owns(self):
        """Receipt lays judgement over native records; it replaces none of them."""
        for model, owner in (('stock.picking', 'stock'),
                             ('stock.move', 'stock'),
                             ('purchase.order', 'purchase'),
                             ('purchase.order.line', 'purchase'),
                             ('account.move', 'account'),
                             ('account.move.line', 'account')):
            self.assertEqual(self.env[model]._original_module, owner,
                             "%s is no longer owned by %s" % (model, owner))
        for invented in ('realestate.procurement.purchase.order',
                         'realestate.procurement.receipt',
                         'realestate.procurement.stock.picking',
                         'realestate.procurement.vendor.bill'):
            self.assertNotIn(invented, self.env,
                             "%s duplicates a native model" % invented)

    def test_it_contributes_the_procurement_half_of_a_picking(self):
        picking = self.env['stock.picking']
        for name in PICKING_FIELDS:
            self.assertIn(name, picking._fields)
        self.env.cr.execute("""
            SELECT f.name, d.module FROM ir_model_fields f
              JOIN ir_model_data d ON d.model='ir.model.fields' AND d.res_id=f.id
             WHERE f.model='stock.picking' AND f.name = ANY(%s)""",
                            (list(PICKING_FIELDS),))
        owners = dict(self.env.cr.fetchall())
        self.assertEqual(len(owners), len(PICKING_FIELDS))
        for name, module in owners.items():
            self.assertEqual(module, 'atmta_procurement_receipt',
                             "%s is owned by %s" % (name, module))

    def test_it_adds_no_column_to_inventory_s_table(self):
        """All three picking fields are computed or related — none is stored.

        That is the strongest form of not duplicating native ownership: the
        procurement view of a receipt is derived from the inspection record
        every time it is asked for, so there is no shadow state on Inventory's
        table that could drift from the truth.
        """
        for name in PICKING_FIELDS:
            field = self.env['stock.picking']._fields[name]
            self.assertFalse(
                field.store,
                "%s is stored on stock_picking — Receipt is keeping shadow "
                "state on a table Inventory owns" % name)
        self.env.cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name='stock_picking' AND column_name = ANY(%s)""",
                            (list(PICKING_FIELDS),))
        self.assertEqual(
            [r[0] for r in self.env.cr.fetchall()], [],
            "a procurement column was added to Inventory's table")

    # -- the invariant: Receipt owns no financial truth --------------------
    def test_receipt_grows_no_financial_truth_of_its_own(self):
        for model in RECEIPT_MODELS:
            for name in FORBIDDEN_TRUTH:
                self.assertNotIn(
                    name, self.env[model]._fields,
                    "%s.%s would make Receipt a second source of financial "
                    "truth" % (model, name))

    def test_the_three_way_match_posts_nothing(self):
        """The match is control evidence. Posting stays Accounting's."""
        import inspect
        from odoo.addons.atmta_procurement_receipt.models import vendor_bill
        src = inspect.getsource(vendor_bill)
        self.assertIn('_check_three_way_match', src)
        # it may guard `_post`, but it must not call action_post itself
        self.assertNotIn('action_post()', src,
                         "the match triggers posting, which is Accounting's")

    def test_it_names_no_legacy_group(self):
        import inspect
        from odoo.addons.atmta_procurement_receipt.models import receipt_inspection
        self.assertNotIn('real_estate_procurement.group_',
                         inspect.getsource(receipt_inspection))

    # -- the audit seam ----------------------------------------------------
    def test_it_contributes_the_receiving_audit_checks(self):
        checks = [c.__name__ for c in self.Audit._audit_checks()]
        for name in ('_check_billed_more_than_accepted',
                     '_check_rejected_material_received',
                     '_check_uninspected_receipt_under_policy',
                     '_check_tender_order_without_cost_code'):
            self.assertIn(name, checks)
        self.assertEqual(len(checks), len(set(checks)), "a check is duplicated")

    def test_the_audit_composition_is_complete(self):
        """Evaluation 19 + Award 5 + Receipt 4, when all three are installed."""
        Module = self.env['ir.module.module']
        installed = set(Module.search([
            ('name', 'in', ['atmta_procurement_evaluation',
                            'atmta_procurement_award',
                            'atmta_procurement_receipt']),
            ('state', '=', 'installed')]).mapped('name'))
        checks = [c.__name__ for c in self.Audit._audit_checks()]
        expected = 19 + (5 if 'atmta_procurement_award' in installed else 0) \
            + (4 if 'atmta_procurement_receipt' in installed else 0)
        self.assertEqual(len(checks), expected,
                         "audit composition is %d, expected %d for %s"
                         % (len(checks), expected, sorted(installed)))

    def test_the_inspection_sequence_is_not_duplicated(self):
        n = self.env['ir.sequence'].search_count(
            [('code', '=', 'realestate.procurement.receipt.inspection')])
        self.assertLessEqual(n, 1)

    def test_the_dependency_points_downward_only(self):
        Module = self.env['ir.module.module']
        rec = Module.search([('name', '=', 'atmta_procurement_receipt')])
        deps = rec.dependencies_id.mapped('name')
        self.assertIn('atmta_procurement_evaluation', deps)
        for lower in ('atmta_procurement_evaluation', 'atmta_procurement_award',
                      'atmta_procurement_sourcing'):
            other = Module.search([('name', '=', lower)])
            if other:
                self.assertNotIn('atmta_procurement_receipt',
                                 other.dependencies_id.mapped('name'),
                                 "%s depends upward on Receipt" % lower)
