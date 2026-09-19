# -*- coding: utf-8 -*-
"""M7 — dashboard correctness, cross-module compatibility, performance.

The dashboard tests are about *correctness under multi-company*, which is what
the audit found missing: not one query carried a company clause, so every KPI
counted every company's inventory and money.

The compatibility tests pin the shape each downstream module actually reads.
They are deliberately about field and method existence plus behaviour, not
about those modules' own logic — that is their suite's job.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .test_contract import ContractCommon


@tagged('post_install', '-at_install', 'atmta_developer')
class TestDashboardScoping(ContractCommon):
    """Phase 41 — the dashboard must follow the company switcher."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['realestate.developer.dashboard']
        cls.company_b = cls.env['res.company'].create({'name': 'Dash Co B'})
        cls.project_b = cls.Project.create({
            'name': 'Other Developer', 'code': 'ODV',
            'company_id': cls.company_b.id,
            'commercial_state': 'selling',
        })

    def _units_in_b(self, count=4):
        return self.Property.create([{
            'name': 'ODV-U-%03d' % i,
            'property_code': 'ODV-U-%03d' % i,
            'hierarchy_level': 'unit', 'usage_category': 'apartment',
            'area_sqm': 90.0, 'company_id': self.company_b.id,
            'project_id': self.project_b.id,
        } for i in range(1, count + 1)])

    def test_kpis_are_scoped_to_the_active_companies(self):
        """The audit's finding: no query carried a company clause."""
        self._units_in_b(4)
        own = self.Dashboard.with_company(self.company).with_context(
            allowed_company_ids=[self.company.id]).get_data()
        other = self.Dashboard.with_company(self.company_b).with_context(
            allowed_company_ids=[self.company_b.id]).get_data()

        self.assertEqual(other['kpis']['total_units'], 4)
        self.assertNotEqual(own['kpis']['total_units'],
                            other['kpis']['total_units'])
        self.assertGreaterEqual(own['kpis']['total_units'], 3)

    def test_both_companies_together_aggregate(self):
        self._units_in_b(4)
        both = self.Dashboard.with_context(
            allowed_company_ids=[self.company.id, self.company_b.id]).get_data()
        own = self.Dashboard.with_context(
            allowed_company_ids=[self.company.id]).get_data()
        self.assertEqual(
            both['kpis']['total_units'], own['kpis']['total_units'] + 4)

    def test_contract_value_is_company_scoped(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        own = self.Dashboard.with_context(
            allowed_company_ids=[self.company.id]).get_data()
        other = self.Dashboard.with_context(
            allowed_company_ids=[self.company_b.id]).get_data()
        self.assertGreaterEqual(own['kpis']['total_contracted'], 1000000.0)
        self.assertEqual(other['kpis']['total_contracted'], 0.0)

    def test_collected_this_month_reads_real_payments(self):
        """`paid_mtd` summed a state nothing could reach (audit §4.1)."""
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        first = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        first.action_generate_invoice()
        invoice = first.move_id
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': invoice.partner_id.id,
            'amount': invoice.amount_total,
            'currency_id': invoice.currency_id.id,
            'company_id': invoice.company_id.id,
            'date': fields.Date.context_today(invoice),
        })
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda l: l.account_id.account_type == 'asset_receivable'
            and not l.reconciled)
        lines.reconcile()

        data = self.Dashboard.with_context(
            allowed_company_ids=[self.company.id]).get_data()
        self.assertGreater(
            data['kpis']['paid_mtd'], 0,
            "collections this month must reflect money actually received")

    def test_velocity_returns_six_months_even_when_empty(self):
        data = self.Dashboard.get_data()
        self.assertEqual(len(data['velocity']['labels']), 6)
        self.assertEqual(len(data['velocity']['count']), 6)
        self.assertEqual(len(data['velocity']['revenue']), 6)

    def test_payload_shape_is_unchanged(self):
        """The existing OWL front end reads these keys."""
        data = self.Dashboard.get_data()
        for key in ('kpis', 'proj_states', 'unit_states', 'velocity',
                    'top_projects', 'upcoming_collections', 'overdue_amount',
                    'overdue_count', 'map_projs', 'expiring_reservations',
                    'upcoming_installments', 'recent_contracts', 'currency'):
            self.assertIn(key, data, "the front end reads %r" % key)
        for key in ('active_projects', 'construction_projects', 'total_units',
                    'sold_units', 'available_units', 'sales_progress',
                    'active_reservations', 'signed_contracts', 'paid_mtd',
                    'total_contracted'):
            self.assertIn(key, data['kpis'])

    def test_overdue_reads_residuals_not_a_state_guess(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        first = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        first.action_generate_invoice()
        first.date_due = fields.Date.context_today(first) - timedelta(days=30)
        first.invalidate_recordset()

        data = self.Dashboard.with_context(
            allowed_company_ids=[self.company.id]).get_data()
        self.assertGreater(data['overdue_amount'], 0)
        self.assertGreaterEqual(data['overdue_count'], 1)

    def test_available_units_uses_the_availability_engine(self):
        """Not the legacy `state` field, which knows nothing about release."""
        data = self.Dashboard.with_context(
            allowed_company_ids=[self.company.id]).get_data()
        engine_count = self.Property.search_count([
            ('company_id', '=', self.company.id),
            ('project_id', '!=', False),
            ('is_available_for_sale', '=', True),
        ])
        self.assertEqual(data['kpis']['available_units'], engine_count)


@tagged('post_install', '-at_install', 'atmta_developer')
class TestDownstreamCompatibility(ContractCommon):
    """Phase 12 — the contracts five other modules depend on."""

    def test_api_and_portal_read_these_contract_fields(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        for field in ('name', 'partner_id', 'property_id', 'project_id',
                      'sale_price', 'state', 'installment_ids',
                      'balance_due', 'paid_amount', 'progress'):
            self.assertIn(field, self.Contract._fields,
                          "real_estate_api / portal reads contract.%s" % field)

    def test_api_reads_these_installment_fields(self):
        for field in ('sale_contract_id', 'date_due', 'amount', 'state',
                      'kind', 'move_id', 'partner_id', 'property_id'):
            self.assertIn(field, self.Installment._fields,
                          "real_estate_api reads installment.%s" % field)

    def test_contract_template_can_reach_the_commercial_terms(self):
        """Placeholders need the price breakdown, schedule and parties."""
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        for field in ('snapshot_base_price', 'snapshot_premium_total',
                      'snapshot_list_price', 'discount_amount',
                      'promotion_amount', 'party_ids', 'installment_ids',
                      'property_line_ids', 'amendment_ids',
                      'payment_plan_id'):
            self.assertIn(field, self.Contract._fields,
                          "contract template placeholders need contract.%s"
                          % field)
        self.assertTrue(contract.party_ids)
        self.assertTrue(contract.installment_ids)

    def test_maquette_and_plan_reach_reservations_through_create(self):
        """2D/3D open the reservation form, so `create` is the only path.

        That is what makes the release, pricing and locking rules impossible to
        bypass from 3D — there is no second creation route to protect.
        """
        Reservation = self.env['realestate.unit.reservation']
        self.assertTrue(hasattr(Reservation, 'create'))
        self._open_project_for_sales()
        self._release(self.units[1])
        buyer = self.env['res.partner'].create({'name': '3D Buyer'})
        res = Reservation.create({
            'property_id': self.units[1].id, 'partner_id': buyer.id})
        self.assertEqual(res.state, 'hold')
        self.assertTrue(res.snapshot_taken_on,
                        "a reservation made from 3D is snapshotted like any "
                        "other")

    def test_construction_and_investment_read_project_state(self):
        """`state` is the construction dimension and must be untouched."""
        values = dict(self.Project._fields['state'].selection)
        for expected in ('planning', 'construction', 'marketing', 'handover',
                         'completed', 'cancelled'):
            self.assertIn(expected, values)

    def test_checks_can_still_find_its_anchor_fields(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        self.assertTrue(contract.installment_ids)
        self.assertTrue(all(i.date_due for i in contract.installment_ids))
        self.assertTrue(all(i.amount > 0 for i in contract.installment_ids))


@tagged('post_install', '-at_install', 'atmta_developer')
class TestPerformanceShape(ContractCommon):
    """Phase 48 — the indexes the hot queries need.

    Not a benchmark: a timing assertion in CI is a flaky test. This asserts the
    *shape* — that the columns the crons, the dashboard and the receivables
    report filter on are indexed.
    """

    def test_hot_columns_are_indexed(self):
        expectations = [
            ('realestate.unit.reservation', 'company_id'),
            ('realestate.unit.reservation', 'project_id'),
            ('realestate.sale.contract', 'company_id'),
            ('realestate.sale.contract', 'project_id'),
            ('realestate.sale.installment', 'company_id'),
            ('realestate.sale.installment', 'date_due'),
            ('realestate.sale.installment', 'project_id'),
            ('realestate.property', 'is_available_for_sale'),
            ('realestate.property', 'is_released_for_sale'),
            ('realestate.price.history', 'effective_date'),
            ('realestate.sale.contract.amendment', 'contract_id'),
        ]
        for model_name, field_name in expectations:
            field = self.env[model_name]._fields[field_name]
            self.assertTrue(
                field.index,
                "%s.%s is filtered on by a cron, the dashboard or a report "
                "and needs an index" % (model_name, field_name))

    def test_the_double_booking_index_exists(self):
        self.env.cr.execute("""
            SELECT indexdef FROM pg_indexes
            WHERE indexname = 'realestate_reservation_one_live_per_property'
        """)
        row = self.env.cr.fetchone()
        self.assertTrue(
            row, "the partial unique index is what makes double booking "
                 "structurally impossible")
        self.assertIn('UNIQUE', row[0])

    def test_dashboard_does_not_load_recordsets_to_count(self):
        """A behavioural proxy: the query count must not scale with the data.

        Ten more contracts must not cost ten more queries.
        """
        Dashboard = self.env['realestate.developer.dashboard']
        # One released unit per contract: signing refuses a unit that is not on
        # the market or already sold. Created before the baseline is measured,
        # so the baseline and the growth see the same inventory.
        units = self._make_units(count=10, prefix='PERF-U')
        self._release(units)
        Dashboard.get_data()          # warm caches
        self.env.flush_all()

        before = self.env.cr.sql_log_count if hasattr(
            self.env.cr, 'sql_log_count') else None
        if before is None:
            self.skipTest('cursor does not expose a query counter')

        Dashboard.get_data()
        baseline = self.env.cr.sql_log_count - before

        for index in range(10):
            contract = self._contract(
                payment_plan_id=self._plan(name='Perf %s' % index).id,
                property_id=units[index].id)
            contract.action_sign()
        self.env.flush_all()

        before = self.env.cr.sql_log_count
        Dashboard.get_data()
        after_growth = self.env.cr.sql_log_count - before
        self.assertLessEqual(
            after_growth, baseline + 5,
            "the dashboard's query count grew with the data, which means "
            "something is looping over records instead of grouping in SQL")
