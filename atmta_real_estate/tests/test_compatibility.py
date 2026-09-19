"""Downstream compatibility regressions (Phase 34).

These are the tests that would catch an upgrade quietly breaking one of the
other thirteen ATMTA modules. Each one pins a contract that a downstream module
actually relies on -- established by grepping the suite during the Phase 0
audit, not guessed at.
"""

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestDownstreamCompatibility(LeaseCase):

    # ------------------------------------------------------------------
    # property.state -- read by 9 downstream files
    # ------------------------------------------------------------------
    def test_property_state_still_readable(self):
        self.assertIn(self.unit_a.state,
                      ('available', 'reserved', 'rented', 'sold',
                       'maintenance', 'inactive'))

    def test_property_state_searchable(self):
        """maquette, developer, brokerage, api and portal all search on it."""
        found = self.env['realestate.property'].search(
            [('state', '=', 'available')])
        self.assertIn(self.unit_a, found)

    def test_developer_style_reservation_write_still_works(self):
        """real_estate_developer/reservation.py does exactly this."""
        self.unit_a.state = 'reserved'
        self.assertEqual(self.unit_a.state, 'reserved')
        self.unit_a.state = 'available'
        self.assertEqual(self.unit_a.state, 'available')

    def test_developer_style_sale_write_still_works(self):
        """real_estate_developer/sale_contract.py action_handover."""
        self.unit_a.state = 'sold'
        self.assertEqual(self.unit_a.state, 'sold')
        self.assertEqual(self.unit_a.commercial_status, 'sold')

    def test_mark_for_resale_still_works(self):
        self.unit_a.state = 'sold'
        self.unit_a.action_mark_for_resale()
        self.assertEqual(self.unit_a.state, 'available')
        self.assertEqual(self.unit_a.resale_count, 1)

    def test_check_available_for_new_sale_still_works(self):
        from odoo.exceptions import ValidationError
        self.unit_a.state = 'sold'
        with self.assertRaises(ValidationError):
            self.unit_a._check_available_for_new_sale()

    def test_public_api_state_gate_still_passes(self):
        """real_estate_api gates public visibility on this exact tuple."""
        self.assertIn(self.unit_a.state, ('available', 'reserved', 'sold'))

    # ------------------------------------------------------------------
    # contract.state -- read by api, portal, statement report, dashboard
    # ------------------------------------------------------------------
    def test_contract_state_still_readable(self):
        lease = self.make_lease()
        self.assertEqual(lease.state, 'draft')
        self.activate(lease)
        self.assertEqual(lease.state, 'active')

    def test_rental_status_still_computed(self):
        """rental_property._compute_rental_status reads contract.state."""
        lease = self.make_lease()
        self.activate(lease)
        self.unit_a.invalidate_recordset()
        self.assertEqual(self.unit_a.rental_status, 'rented')

    def test_legacy_contract_actions_survive(self):
        """The pre-upgrade buttons are still callable."""
        lease = self.make_lease()
        for method in ('action_generate_payment_lines', 'action_confirm',
                       'action_activate', 'action_terminate',
                       'action_reset_to_draft'):
            self.assertTrue(hasattr(lease, method),
                            "%s must survive the upgrade" % method)

    def test_a_whole_lease_invoice_makes_the_lease_invoiced(self):
        """The legacy flow bills through `invoice_id`, not the obligations.

        `action_generate_invoices` raises one invoice for the whole lease and
        links nothing else, so `billing_status` never moves. The legacy state
        still has to read `invoiced`, because `action_activate` requires it.
        """
        lease = self.make_lease()
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        lease.action_approve_lease()
        self.assertEqual(lease.state, 'confirmed')

        # A posted customer invoice from a lease on the other unit, attached
        # as this lease's whole-lease invoice. What is under test is the
        # compatibility state, not the sale-order bridge that raises it.
        donor = self.make_lease(prop=self.unit_b, use_billing_engine=True)
        self.activate(donor)
        donor.action_generate_billing_schedule()
        obligation = donor.contract_payment_ids.sorted('date_due')[0]
        obligation._create_invoices()
        self.assertEqual(obligation.move_id.state, 'posted')

        lease.invoice_id = obligation.move_id
        self.assertEqual(lease.billing_status, 'not_started',
                         "the obligations were never invoiced")
        self.assertEqual(lease.state, 'invoiced')

        # The legacy Activate now runs the lifecycle's activation checks, so
        # the lease must be signed first, as it must be for the Activate button.
        lease.action_mark_signed()
        lease.action_activate()
        self.assertEqual(lease.lifecycle_state, 'active')

    # ------------------------------------------------------------------
    # contract.payment -- referenced by real_estate_checks and the API
    # ------------------------------------------------------------------
    def test_obligation_legacy_fields_survive(self):
        lease = self.make_lease(use_billing_engine=True)
        self.activate(lease)
        lease.action_generate_billing_schedule()
        obligation = lease.contract_payment_ids[0]
        for field in ('amount', 'amount_total', 'date_due', 'state',
                      'move_id', 'property_id', 'contract_id',
                      'charge_line_ids', 'hijri_date_due'):
            self.assertIn(field, obligation._fields,
                          "%s is referenced downstream and must survive" % field)

    def test_checks_module_can_still_link_a_rental_payment(self):
        """real_estate_checks/check.py has a m2o to this model."""
        if 'realestate.check' not in self.env:
            self.skipTest('real_estate_checks is not installed')
        field = self.env['realestate.check']._fields.get('rental_payment_id')
        self.assertTrue(field)
        self.assertEqual(field.comodel_name, 'realestate.contract.payment')

    def test_obligation_state_values_unchanged(self):
        """Downstream filters on these exact keys."""
        selection = dict(
            self.env['realestate.contract.payment']._fields['state'].selection)
        for key in ('draft', 'invoiced', 'paid', 'cancelled'):
            self.assertIn(key, selection)

    # ------------------------------------------------------------------
    # Models downstream modules extend
    # ------------------------------------------------------------------
    def test_downstream_extended_models_still_exist(self):
        for model in ('realestate.property', 'realestate.contract',
                      'realestate.contract.payment',
                      'realestate.maintenance.request',
                      'realestate.contract.increment.rule',
                      'realestate.account.tools'):
            self.assertIn(model, self.env,
                          "%s is extended downstream and must survive" % model)

    def test_contract_type_is_now_accessible(self):
        """Bug found in the pre-upgrade review: contract.type had NO ACL row,
        so a non-superuser could not read the lease form's Contract Type."""
        from odoo.tests.common import new_test_user
        user = new_test_user(
            self.env, login='re_compat_ctype',
            groups='base.group_user,atmta_real_estate.group_rental_user',
            company_id=self.company.id)
        user.company_ids = [(4, self.company.id)]
        # Must not raise.
        self.env['contract.type'].with_user(user).search([])

    # ------------------------------------------------------------------
    # Accounting helper contract
    # ------------------------------------------------------------------
    def test_account_tools_api_unchanged(self):
        """Every ATMTA module calls these two methods."""
        tools = self.env['realestate.account.tools']
        self.assertTrue(hasattr(tools, 'post_moves'))
        self.assertTrue(hasattr(tools, 'register_payment'))

    def test_rent_roll_view_is_queryable(self):
        lease = self.make_lease(use_billing_engine=True)
        self.activate(lease)
        rows = self.env['realestate.rent.roll'].search([])
        self.assertTrue(rows, "The rent roll must return rows.")
        leased = rows.filtered(lambda r: r.contract_id == lease)
        self.assertTrue(leased)
        self.assertFalse(leased[0].is_vacant)

    def test_rent_roll_includes_vacant_units(self):
        rows = self.env['realestate.rent.roll'].search([('is_vacant', '=', True)])
        self.assertIn(self.unit_b, rows.mapped('property_id'))

    def test_dashboard_returns_work_and_trends(self):
        """The dashboard's two calls, which its web client depends on."""
        Dashboard = self.env['realestate.rental.dashboard']
        work = Dashboard.get_work('team')
        for key in ('sections', 'scope', 'quick_actions', 'currency_id', 'as_of'):
            self.assertIn(key, work)
        tiles = {tile['key'] for section in work['sections'] for tile in section['tiles']}
        self.assertIn('occupancy_rate', tiles)
        self.assertIn('charts', Dashboard.get_trends())

    def test_every_dashboard_tile_has_a_working_drilldown(self):
        """No fake numbers: each drillable tile opens real records."""
        Dashboard = self.env['realestate.rental.dashboard']
        for section in Dashboard.get_work('team')['sections']:
            for tile in section['tiles']:
                if not tile['drill']:
                    continue
                action = Dashboard.action_drill(tile['key'], 'team')
                self.assertEqual(action['type'], 'ir.actions.act_window')
                self.env[action['res_model']].search(action['domain'], limit=1)
