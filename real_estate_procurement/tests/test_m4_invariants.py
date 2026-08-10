# -*- coding: utf-8 -*-
"""M4 — the five things vendor governance must never get wrong.

These were written before a single model, because each one names a way the
feature could be built that would look finished and be worthless:

```
    A   a vendor with a price list is treated as an approved vendor
    B   one approval is read as approval for everything they sell
    C   a suspension is filed and then quietly outranked by an old approval
    D   an expiry date is decoration and today's status rewrites history
    E   governing a vendor moves money
```

Everything else in M4 is detail around these.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import M3Common, M4Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4Invariants(M4Common):

    # -- A -------------------------------------------------------------
    def test_a_a_vendor_price_is_not_a_qualification(self):
        """Having quoted us a price is not the same as being allowed to sell.

        The catalogue knows what a vendor charges. That is commercial
        information Odoo has always held and M4 does not touch it. Whether we
        may buy from them is a governance decision somebody has to make, and
        nobody has made it here.
        """
        vendor = self._vendor('Priced But Unassessed')
        # Real commercial data: a supplier line with a real price.
        self._product(price=250.0, vendor=vendor)
        self._set_vendor_policy('required_for_sourcing')

        result = self._eligibility(vendor, self.trade_concrete)

        self.assertFalse(result['eligible'])
        self.assertFalse(result['qualified'])
        self.assertEqual(result['status'], 'no_qualification')
        self.assertFalse(result['qualification_id'])
        self.assertTrue(result['blocking_reasons'])

    def test_a_price_data_still_exists_and_is_untouched(self):
        """M4 answers "may we?", never "how much?" — and does not delete the
        answer to the second question while answering the first."""
        vendor = self._vendor('Priced But Unassessed')
        product = self._product(price=250.0, vendor=vendor)
        self._set_vendor_policy('required_for_sourcing')
        self._eligibility(vendor, self.trade_concrete)

        seller = product.seller_ids.filtered(
            lambda s: s.partner_id == vendor)
        self.assertEqual(len(seller), 1)
        self.assertEqual(seller.price, 250.0)

    # -- B -------------------------------------------------------------
    def test_b_qualification_is_category_specific(self):
        """Qualified for concrete is not qualified for switchgear.

        A single `approved` flag on the partner cannot express this, which is
        why there isn't one. The decision is keyed on the trade it was made
        about.
        """
        vendor = self._vendor('Concrete Co')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')

        concrete = self._eligibility(vendor, self.trade_concrete)
        electrical = self._eligibility(vendor, self.trade_electrical)

        self.assertTrue(concrete['eligible'])
        self.assertTrue(concrete['qualified'])
        self.assertEqual(concrete['status'], 'eligible')

        self.assertFalse(electrical['eligible'])
        self.assertFalse(electrical['qualified'])
        self.assertEqual(electrical['status'], 'no_qualification')

    def test_b_the_concrete_decision_is_not_widened_by_a_second_trade(self):
        """Adding an electrical qualification must not retroactively make the
        concrete one mean something broader, or vice versa."""
        vendor = self._vendor('Two Trades')
        concrete_q = self._qualify(vendor, self.trade_concrete)
        electrical_q = self._qualify(vendor, self.trade_electrical)
        self.assertNotEqual(concrete_q, electrical_q)
        self._set_vendor_policy('required_for_sourcing')

        for trade, qualification in ((self.trade_concrete, concrete_q),
                                     (self.trade_electrical, electrical_q)):
            result = self._eligibility(vendor, trade)
            self.assertTrue(result['eligible'])
            self.assertEqual(result['qualification_id'], qualification.id)

    # -- C -------------------------------------------------------------
    def test_c_suspension_overrides_a_valid_qualification(self):
        """The newest management decision wins, and the old one is still on
        file saying what the assessor concluded."""
        vendor = self._vendor('Suspended Co')
        qualification = self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('required_for_sourcing')
        self.assertTrue(self._eligibility(vendor, self.trade_concrete)['eligible'])

        restriction = self._restrict(vendor, 'sourcing_suspension',
                                     reason='Site safety incident')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['status'], 'suspended')
        self.assertIn(restriction.id, result['restriction_ids'])
        self.assertTrue(result['blocking_reasons'])

        # The assessment is untouched. It said what it said.
        self.assertTrue(qualification.exists())
        self.assertEqual(qualification.state, 'approved')
        self.assertEqual(qualification.result, 'qualified')
        self.assertTrue(qualification.is_current)
        # And the service still names it, so the screen can show both facts.
        self.assertEqual(result['qualification_id'], qualification.id)

    def test_c_suspension_blocks_even_under_a_permissive_policy(self):
        """A company that went to the trouble of suspending a vendor meant it.

        Absence of qualification is tolerated under OPTIONAL because nobody
        has decided yet. A suspension is a decision.
        """
        vendor = self._vendor('Suspended Co')
        self._qualify(vendor, self.trade_concrete)
        self._set_vendor_policy('optional')
        self._restrict(vendor, 'sourcing_suspension', reason='Fraud review')

        result = self._eligibility(vendor, self.trade_concrete)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['status'], 'suspended')

    # -- D -------------------------------------------------------------
    def test_d_expiry_ends_eligibility_without_ending_history(self):
        """Rule 6 in one test.

        A tender issued in March must still be able to say the vendor was
        eligible in March, after the certificate expired in June.
        """
        vendor = self._vendor('Expiring Co')
        effective = self.today - timedelta(days=90)
        expiry = self.today - timedelta(days=1)
        qualification = self._qualify(
            vendor, self.trade_concrete,
            effective=effective, expiry=expiry,
            approved_on=effective)
        self._set_vendor_policy('required_for_sourcing')

        today_result = self._eligibility(vendor, self.trade_concrete)
        self.assertFalse(today_result['eligible'])
        self.assertEqual(today_result['status'], 'expired')

        back_then = self._eligibility(vendor, self.trade_concrete,
                                      date=self.today - timedelta(days=30))
        self.assertTrue(back_then['eligible'])
        self.assertEqual(back_then['status'], 'eligible')
        self.assertEqual(back_then['qualification_id'], qualification.id)
        self.assertEqual(back_then['valid_to'], expiry)

    def test_d_eligibility_never_predates_the_approval(self):
        """A qualification approved today did not make anybody eligible last
        year, however early its effective date is typed."""
        vendor = self._vendor('Backdated Co')
        self._qualify(vendor, self.trade_concrete,
                      effective=self.today - timedelta(days=365))
        self._set_vendor_policy('required_for_sourcing')

        result = self._eligibility(vendor, self.trade_concrete,
                                   date=self.today - timedelta(days=200))
        self.assertFalse(result['eligible'])
        self.assertEqual(result['status'], 'no_qualification')

    def test_d_expiry_is_a_date_boundary_not_a_flag(self):
        """Valid on the last day, not valid on the next one."""
        vendor = self._vendor('Boundary Co')
        expiry = self.today + timedelta(days=30)
        self._qualify(vendor, self.trade_concrete, expiry=expiry,
                      approved_on=self.today - timedelta(days=1))
        self._set_vendor_policy('required_for_sourcing')

        self.assertTrue(self._eligibility(
            vendor, self.trade_concrete, date=expiry)['eligible'])
        self.assertFalse(self._eligibility(
            vendor, self.trade_concrete,
            date=expiry + timedelta(days=1))['eligible'])


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM4FinancialIsolation(M3Common):
    """E — vendor governance moves no money.

    M3 owns the only two things in this system that represent an obligation:
    a reservation and a Construction commitment. M4 must be able to approve,
    condition, suspend and expire vendors all day without either number
    moving by a piastre.
    """

    def setUp(self):
        super().setUp()
        self.trade = self.env['realestate.procurement.vendor.category'].create({
            'name': 'Concrete Works', 'code': 'M4E-CONC'})
        self.template = self.env[
            'realestate.procurement.qualification.template'].create({
                'name': 'Concrete Supplier', 'code': 'M4E-TPL',
                'company_id': self.company.id})

    def _qualification_for(self, vendor):
        Qualification = self.env['realestate.procurement.vendor.qualification']
        qualification = Qualification.create({
            'partner_id': vendor.id,
            'company_id': self.company.id,
            'category_id': self.trade.id,
            'template_id': self.template.id,
            # Backdated so the expiry half of this test has somewhere to put
            # an expiry date: a qualification cannot expire before it starts,
            # and the database says so.
            'effective_date': fields.Date.context_today(
                self.env.user) - timedelta(days=30),
        })
        qualification.action_submit()
        qualification.action_start_review()
        qualification.action_assess()
        qualification.action_request_approval()
        return qualification

    def test_e_governing_a_vendor_moves_no_money(self):
        self._budget([(self.concrete, 10_000_000.0)])
        request = self._demand(3_000.0)          # 3,000,000 approved demand
        self._confirm(request.action_create_rfqs(self.vendor))

        reserved = self._reserved(self.project)
        commitment = self._commitment(self.project)
        actual = self._actual(self.project)
        self.assertEqual(commitment, 3_000_000.0,
                         'fixture must produce a real commitment to protect')

        qualification = self._qualification_for(self.vendor)
        qualification.action_approve()
        self.assertEqual(self._reserved(self.project), reserved)
        self.assertEqual(self._commitment(self.project), commitment)
        self.assertEqual(self._actual(self.project), actual)

        self.env['realestate.procurement.vendor.restriction'].create({
            'partner_id': self.vendor.id,
            'company_id': self.company.id,
            'restriction_type': 'sourcing_suspension',
            'reason': 'Quality failure on another project',
            'effective_from': fields.Date.context_today(self.env.user),
        }).action_activate()
        self.assertEqual(self._reserved(self.project), reserved)
        self.assertEqual(self._commitment(self.project), commitment)
        self.assertEqual(self._actual(self.project), actual)

        qualification.expiry_date = fields.Date.context_today(
            self.env.user) - timedelta(days=1)
        self.env['realestate.procurement.vendor.qualification'
                 ].expire_due_qualifications()
        self.assertEqual(qualification.state, 'expired')
        self.assertEqual(self._reserved(self.project), reserved)
        self.assertEqual(self._commitment(self.project), commitment)
        self.assertEqual(self._actual(self.project), actual)

    def test_e_governance_writes_no_accounting_entries(self):
        """Not "the totals happen to agree" — no journal item exists at all."""
        Move = self.env['account.move.line']
        Reservation = self.env['realestate.procurement.reservation']
        vendor = self._vendor('Governed Only')
        before = Move.search_count([('partner_id', '=', vendor.id)])
        reservations_before = Reservation.search_count([])

        qualification = self._qualification_for(vendor)
        qualification.action_approve()
        self.env['realestate.procurement.vendor.restriction'].create({
            'partner_id': vendor.id,
            'company_id': self.company.id,
            'restriction_type': 'award_suspension',
            'reason': 'Pending investigation',
            'effective_from': fields.Date.context_today(self.env.user),
        }).action_activate()

        self.assertEqual(
            Move.search_count([('partner_id', '=', vendor.id)]), before)
        self.assertEqual(Reservation.search_count([]), reservations_before,
                         'governing a vendor created a reservation')
