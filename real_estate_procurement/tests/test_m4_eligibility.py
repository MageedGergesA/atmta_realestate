# -*- coding: utf-8 -*-
"""M4 tests 11–15 and 20–22 — policy, price, history and what M4 must not do.

The service is the subject here rather than the qualification: what it
returns, when it refuses, and everything it leaves alone while doing so.
"""

from datetime import timedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import M3Common, M4Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Policy(M4Common):

    # -- TEST 11 -------------------------------------------------------
    def test_11_optional_policy_changes_nothing_for_a_legacy_vendor(self):
        """The migration-safe default, tested rather than assumed."""
        vendor = self._vendor('Legacy Supplier')
        self._set_vendor_policy('optional')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertTrue(result['eligible'])
        self.assertFalse(result['qualified'],
                         'allowed is not the same as approved')
        self.assertEqual(result['status'], 'no_qualification')
        self.assertFalse(result['blocking_reasons'])

    def test_11_optional_is_the_shipped_default(self):
        fresh = self.env['res.company'].create({'name': 'M4 Fresh Company'})
        self.assertEqual(fresh.procurement_vendor_policy, 'optional')

    def test_11_warn_records_the_gap_and_refuses_nothing(self):
        vendor = self._vendor('Warned Supplier')
        self._set_vendor_policy('warn')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertTrue(result['eligible'])
        self.assertFalse(result['qualified'])
        self.assertTrue(result['warnings'])

    # -- TEST 12 -------------------------------------------------------
    def test_12_required_for_sourcing_refuses_an_invitation(self):
        vendor = self._vendor('Unassessed Supplier')
        product = self._product(price=100.0, vendor=vendor)
        self._set_vendor_policy('required_for_sourcing')
        request = self._request(self.project, [(product, 1.0)])
        request.action_submit()
        request.action_approve()

        with self.assertRaises(UserError) as caught:
            request.action_create_rfqs(vendor)
        self.assertIn('no qualification', str(caught.exception).lower())

    def test_12_a_historic_confirmed_order_is_untouched_by_the_policy(self):
        vendor = self._vendor('Historic Supplier')
        product = self._product(price=100.0, vendor=vendor)
        order = self.PO.create({
            'partner_id': vendor.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_qty': 1.0, 'price_unit': 100.0,
                                   'taxes_id': [(5, 0, 0)]})],
        })
        order.button_confirm()

        self._set_vendor_policy('required_for_award')
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.amount_untaxed, 100.0)

    def test_12_required_for_award_allows_the_invitation(self):
        """The whole point of having two levels."""
        vendor = self._vendor('Invite Now Qualify Later')
        product = self._product(price=100.0, vendor=vendor)
        self._set_vendor_policy('required_for_award')
        request = self._request(self.project, [(product, 1.0)])
        request.action_submit()
        request.action_approve()

        orders = request.action_create_rfqs(vendor)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders.state, 'draft')

    def test_12_an_unclassified_line_is_not_a_way_past_the_gate(self):
        """A requisition mixing a classified and an unclassified line is
        asked both questions.

        Qualified for concrete, and the second line says nothing about what
        it is. Checking only the trade that happens to be named would let the
        vendor through for the scope nobody classified.
        """
        vendor = self._vendor('Concrete Only')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        classified = self._product(price=100.0, vendor=vendor)
        unclassified = self._product(price=100.0, vendor=vendor)

        request = self._request(self.project, [(classified, 1.0)],
                                line_defaults={
                                    'vendor_category_id': self.trade_concrete.id})
        self.RequestLine.create({
            'request_id': request.id, 'product_id': unclassified.id,
            'qty': 1.0, 'uom_id': unclassified.uom_id.id})
        request.invalidate_recordset()
        request.action_submit()
        request.action_approve()

        # The trade-less question finds the concrete qualification, so this
        # particular vendor still passes — what matters is that the question
        # was asked at all.
        request.action_create_rfqs(vendor)

        other = self._vendor('Qualified For Nothing')
        with self.assertRaises(UserError):
            request.action_create_rfqs(other)

    def test_12_a_project_may_override_the_company(self):
        vendor = self._vendor('Project Scoped')
        self._set_vendor_policy('optional')
        self._set_vendor_policy('required_for_sourcing', project=self.project)

        self.assertTrue(self._eligibility(vendor, self.trade_concrete)[
            'eligible'])
        self.assertFalse(self._eligibility(
            vendor, self.trade_concrete, project=self.project)['eligible'])

    # -- TESTS 13 and 14 ----------------------------------------------
    def test_13_a_price_list_is_not_a_qualification(self):
        vendor = self._vendor('Cheap But Unassessed')
        self._product(price=1.0, vendor=vendor)
        self._set_vendor_policy('required_for_sourcing')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['status'], 'no_qualification')

    def test_14_a_qualification_is_not_a_price(self):
        """Eligible with nothing in the catalogue. Procurement must not
        invent a number to fill the gap."""
        vendor = self._vendor('Qualified No Catalogue')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        product = self._product(price=0.0)          # no seller line at all

        self.assertTrue(self._eligibility(
            vendor, self.trade_concrete)['eligible'])
        self.assertFalse(product.seller_ids)

        request = self._request(self.project, [(product, 2.0)])
        request.action_submit()
        request.action_approve()
        orders = request.action_create_rfqs(vendor)
        self.assertEqual(orders.order_line.price_unit, 0.0,
                         'an invented price would be worse than a blank one')

    # -- TEST 15 -------------------------------------------------------
    def test_15_the_answer_depends_on_the_date_it_is_asked_about(self):
        vendor = self._vendor('Suspended Later')
        self._qualify(vendor, self.trade_concrete,
                      effective=self.today - timedelta(days=60),
                      approved_on=self.today - timedelta(days=60))
        self._set_vendor_policy('required_for_sourcing')
        self._restrict(vendor, 'sourcing_suspension', reason='Incident',
                       effective_from=self.today - timedelta(days=5))

        back_then = self._eligibility(vendor, self.trade_concrete,
                                     date=self.today - timedelta(days=30))
        now = self._eligibility(vendor, self.trade_concrete)

        self.assertTrue(back_then['eligible'])
        self.assertFalse(now['eligible'])
        self.assertEqual(now['status'], 'suspended')

    def test_15_a_snapshot_survives_everything_changing_afterwards(self):
        """M4Y — the shape M5 will store on a tender invitation."""
        vendor = self._vendor('Snapshot Vendor')
        qualification = self._qualify(
            vendor, self.trade_concrete,
            effective=self.today - timedelta(days=30),
            expiry=self.today + timedelta(days=30),
            approved_on=self.today - timedelta(days=30))
        self._set_vendor_policy('required_for_sourcing')

        snapshot = self.Eligibility.eligibility_snapshot(
            self._eligibility(vendor, self.trade_concrete))
        self.assertTrue(snapshot['eligible'])
        self.assertEqual(snapshot['qualification_ref'], qualification.name)
        self.assertEqual(snapshot['status_label'], 'Eligible')

        # Now break the present in every way available.
        self._restrict(vendor, 'debarment', reason='Later events')
        qualification._write_engine({'approved_date': self.today})
        self.assertFalse(self._eligibility(
            vendor, self.trade_concrete)['eligible'])

        # The snapshot is a flat dict of primitives; nothing about it moved.
        self.assertTrue(snapshot['eligible'])
        self.assertEqual(snapshot['qualification_ref'], qualification.name)

    def test_the_service_explains_itself_rather_than_returning_a_boolean(self):
        vendor = self._vendor('Explain Me')
        self._set_vendor_policy('required_for_sourcing')
        result = self._eligibility(vendor, self.trade_concrete)
        for key in ('eligible', 'qualified', 'status', 'qualification_id',
                    'valid_from', 'valid_to', 'conditions',
                    'blocking_reasons', 'warnings', 'restriction_ids',
                    'policy', 'date', 'purpose'):
            self.assertIn(key, result)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Sourcing(M4Common):
    """Tests 21 and 22 — what the sourcing screens do and do not do."""

    # -- TEST 22 -------------------------------------------------------
    def test_22_the_pool_shows_every_vendor_with_its_status(self):
        qualified = self._vendor('Qualified Vendor')
        suspended = self._vendor('Suspended Vendor')
        product = self._product(price=100.0, vendor=qualified)
        self.env['product.supplierinfo'].create({
            'partner_id': suspended.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'price': 90.0})
        self._qualify(qualified, self.trade_concrete)
        self._qualify(suspended, self.trade_concrete)
        self._restrict(suspended, 'sourcing_suspension', reason='Incident')
        self._set_vendor_policy('required_for_sourcing')

        request = self._request(
            self.project, [(product, 1.0)],
            line_defaults={'vendor_category_id': self.trade_concrete.id})
        pool = request._sourcing_pool()
        by_partner = {row['partner_id']: row for row in pool}

        self.assertIn(qualified.id, by_partner)
        self.assertIn(suspended.id, by_partner,
                      'hiding them makes "why not X?" unanswerable')
        self.assertTrue(by_partner[qualified.id]['eligible'])
        self.assertFalse(by_partner[suspended.id]['eligible'])
        self.assertEqual(by_partner[suspended.id]['status'], 'suspended')

    def test_22_the_pool_confirms_nothing(self):
        vendor = self._vendor('Pool Vendor')
        product = self._product(price=100.0, vendor=vendor)
        self._qualify(vendor, self.trade_concrete)
        request = self._request(self.project, [(product, 1.0)])
        before = self.PO.search_count([])
        request._sourcing_pool()
        self.assertEqual(self.PO.search_count([]), before)

    def test_22_suggested_suppliers_still_lists_everybody(self):
        """M4 did not put the filter back that M2 removed."""
        eligible = self._vendor('Eligible Seller')
        ineligible = self._vendor('Ineligible Seller')
        product = self._product(price=100.0, vendor=eligible)
        self.env['product.supplierinfo'].create({
            'partner_id': ineligible.id,
            'product_tmpl_id': product.product_tmpl_id.id, 'price': 80.0})
        self._qualify(eligible, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')

        request = self._request(self.project, [(product, 1.0)])
        suggestions = request.line_ids._suggested_suppliers()
        self.assertIn(eligible, suggestions)
        self.assertIn(ineligible, suggestions)

    # -- TEST 21 -------------------------------------------------------
    def test_21_a_draft_rfq_stays_a_draft_rfq(self):
        vendor = self._vendor('RFQ Vendor')
        product = self._product(price=100.0, vendor=vendor)
        self._qualify(vendor, self.trade_concrete)
        request = self._request(self.project, [(product, 1.0)])
        request.action_submit()
        request.action_approve()
        orders = request.action_create_rfqs(vendor)
        self.assertEqual(orders.state, 'draft')

        # Approve another qualification, suspend, expire — the RFQ does not
        # move an inch. Sourcing decisions are M5's.
        self._qualify(vendor, self.trade_electrical)
        self._restrict(vendor, 'award_suspension', reason='Later')
        self.Qualification.expire_due_qualifications()
        orders.invalidate_recordset()
        self.assertEqual(orders.state, 'draft')

    def test_the_trade_is_suggested_from_the_product_but_not_imposed(self):
        categ = self.env['product.category'].create({'name': 'M4 Concrete'})
        self.trade_concrete.product_category_ids = [(6, 0, categ.ids)]
        product = self._product(price=100.0)
        product.categ_id = categ.id

        request = self._request(self.project, [(product, 1.0)])
        line = request.line_ids
        self.assertEqual(line.vendor_category_id, self.trade_concrete)

        line.vendor_category_id = self.trade_electrical
        line.invalidate_recordset()
        self.assertEqual(line.vendor_category_id, self.trade_electrical,
                         "a buyer's choice must not be recomputed away")

    def test_an_ambiguous_product_mapping_suggests_nothing(self):
        categ = self.env['product.category'].create({'name': 'M4 Ambiguous'})
        self.trade_concrete.product_category_ids = [(6, 0, categ.ids)]
        self.trade_electrical.product_category_ids = [(6, 0, categ.ids)]
        product = self._product(price=100.0)
        product.categ_id = categ.id

        request = self._request(self.project, [(product, 1.0)])
        self.assertFalse(request.line_ids.vendor_category_id,
                         'two trades claiming one category is a question, '
                         'not an answer')


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4PurchaseGate(M4Common):
    """M4K at confirmation, and the promise that ordinary purchasing is
    left alone."""

    def _order(self, vendor, product=None, project=None):
        product = product or self._product(price=100.0, vendor=vendor)
        return self.PO.create({
            'partner_id': vendor.id,
            're_project_id': project.id if project else False,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_qty': 1.0, 'price_unit': 100.0,
                                   'taxes_id': [(5, 0, 0)]})],
        })

    def test_required_for_award_refuses_a_project_order(self):
        vendor = self._vendor('Unqualified Supplier')
        self._set_vendor_policy('required_for_award')
        order = self._order(vendor, project=self.project)
        with self.assertRaises(UserError) as caught:
            order.button_confirm()
        self.assertIn('cannot be confirmed', str(caught.exception))
        self.assertEqual(order.state, 'draft')

    def test_a_qualified_vendor_confirms_normally(self):
        vendor = self._vendor('Qualified Supplier')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_award')
        order = self._order(vendor, project=self.project)
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_an_office_purchase_is_not_policed(self):
        """"Do not globally break standard Odoo Purchase", tested.

        An order with no project and no real-estate source is nothing to do
        with this suite, and confirms under the strictest policy available.
        """
        vendor = self._vendor('Stationery Supplier')
        self._set_vendor_policy('required_for_award')
        order = self._order(vendor)
        self.assertFalse(order.is_realestate_po)
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_the_gate_is_server_side_not_a_hidden_button(self):
        """Confirming by RPC, by import or from another module hits it too."""
        vendor = self._vendor('Backdoor Supplier')
        self._set_vendor_policy('required_for_award')
        order = self._order(vendor, project=self.project)
        with self.assertRaises(UserError):
            self.env['purchase.order'].browse(order.id).button_confirm()
        with self.assertRaises(UserError):
            order.with_context(force_confirm=True).button_confirm()
        self.assertEqual(order.state, 'draft')

    def test_a_warning_never_blocks_the_thing_it_warns_about(self):
        """Regression. Under WARN the gate posts a note, and Odoo refuses to
        post on behalf of a user with no email — so the note's failure was
        cancelling the confirmation it was only commenting on."""
        vendor = self._vendor('Unassessed Under Warn')
        self._set_vendor_policy('warn')
        buyer = self.env['res.users'].create({
            'name': 'No Email Buyer',
            'login': 'm4-no-email-%s' % self._next(),
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('purchase.group_purchase_manager').id,
                # Reading the project is M3's governance compute, not M4's
                # gate. Granted here so the test is about the email, which
                # is the thing that used to break.
                self.env.ref('real_estate_developer.group_dev_readonly').id])],
        })
        self.assertFalse(buyer.email)
        # A service line, so confirming does not also try to build a receipt
        # and lazily create the project's stock location — an unrelated path
        # that needs project write rights this test has no business granting.
        order = self._order(vendor, product=self._product(
            price=100.0, vendor=vendor, service=True), project=self.project)

        order.with_user(buyer).button_confirm()

        self.assertEqual(order.state, 'purchase')
        self.assertTrue(order.message_ids.filtered(
            lambda m: 'governance gaps' in (m.body or '')),
            'and the warning is still on the record')

    def test_a_suspended_vendor_cannot_receive_a_project_order(self):
        vendor = self._vendor('Suspended Supplier')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('optional')
        self._restrict(vendor, 'sourcing_suspension', reason='Incident')
        order = self._order(vendor, project=self.project)
        with self.assertRaises(UserError):
            order.button_confirm()

    def test_a_bare_purchase_manager_gets_the_refusal_not_an_access_error(self):
        """Regression. The gate has to answer people who hold nothing.

        A Purchase Manager with no procurement rights cannot read a
        qualification or a restriction — so building the refusal *message*
        from those records used to raise AccessError instead. A governance
        refusal that arrives as "you are not allowed to access" tells the
        person nothing about what to do next.
        """
        vendor = self._vendor('Suspended For A Stranger')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('optional')
        self._restrict(vendor, 'sourcing_suspension',
                       reason='Site safety incident')
        buyer = self.env['res.users'].create({
            'name': 'Bare Purchase Manager',
            'login': 'm4-bare-%s' % self._next(),
            'email': 'bare@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('purchase.group_purchase_manager').id,
                self.env.ref('real_estate_developer.group_dev_readonly').id])],
        })
        order = self._order(vendor, product=self._product(
            price=100.0, vendor=vendor, service=True), project=self.project)

        with self.assertRaises(UserError) as caught:
            order.with_user(buyer).button_confirm()
        message = str(caught.exception)
        self.assertIn('cannot be confirmed', message)
        self.assertIn('Site safety incident', message)
        self.assertNotIn('not allowed to access', message)


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4NoFinancialEffect(M3Common):
    """TEST 20, in more detail than the invariant file.

    Every governance action, against a project with live control numbers.
    """

    def setUp(self):
        super().setUp()
        self.trade = self.env[
            'realestate.procurement.vendor.category'].create({
                'name': 'M4 Trade', 'code': 'M4-T%s' % self._next()})
        self.template = self.env[
            'realestate.procurement.qualification.template'].create({
                'name': 'M4 Template', 'code': 'M4-TPL%s' % self._next(),
                'company_id': self.company.id})

    def test_20_the_whole_governance_lifecycle_moves_no_control_number(self):
        self._budget([(self.concrete, 10_000_000.0)])
        request = self._demand(2_000.0)                  # 2,000,000 reserved
        self.assertEqual(self._reserved(self.project), 2_000_000.0)

        reserved = self._reserved(self.project)
        commitment = self._commitment(self.project)
        actual = self._actual(self.project)

        Qualification = self.env[
            'realestate.procurement.vendor.qualification']
        qualification = Qualification.create({
            'partner_id': self.vendor.id, 'company_id': self.company.id,
            'category_id': self.trade.id, 'template_id': self.template.id,
            'effective_date': self.today,
        })
        for step in ('action_submit', 'action_start_review', 'action_assess',
                     'action_request_approval', 'action_approve'):
            getattr(qualification, step)()
            self.assertEqual(self._reserved(self.project), reserved, step)
            self.assertEqual(self._commitment(self.project), commitment, step)
            self.assertEqual(self._actual(self.project), actual, step)

        restriction = self.env[
            'realestate.procurement.vendor.restriction'].create({
                'partner_id': self.vendor.id, 'company_id': self.company.id,
                'restriction_type': 'sourcing_suspension',
                'reason': 'Test', 'effective_from': self.today})
        restriction.action_activate()
        restriction.action_lift(reason='Test over')
        self.assertEqual(self._reserved(self.project), reserved)
        self.assertEqual(self._commitment(self.project), commitment)
        self.assertEqual(self._actual(self.project), actual)
        self.assertEqual(request.state, 'approved')

    def test_20_a_rejection_moves_nothing_either(self):
        self._budget([(self.concrete, 10_000_000.0)])
        self._demand(1_000.0)
        reserved = self._reserved(self.project)

        Qualification = self.env[
            'realestate.procurement.vendor.qualification']
        qualification = Qualification.create({
            'partner_id': self.vendor.id, 'company_id': self.company.id,
            'category_id': self.trade.id, 'template_id': self.template.id,
            'effective_date': self.today,
        })
        qualification.action_submit()
        qualification.action_reject(reason='Insufficient evidence')
        self.assertEqual(qualification.state, 'rejected')
        self.assertEqual(self._reserved(self.project), reserved)
