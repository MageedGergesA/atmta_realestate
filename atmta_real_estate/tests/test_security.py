"""Security groups, record rules and approval controls (Phase 27)."""

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeasingSecurity(LeaseCase):

    def setUp(self):
        super().setUp()
        self.lease = self.make_lease(rent=1000.0)

    def _user(self, login, group):
        user = new_test_user(
            self.env, login=login, groups='base.group_user,%s' % group,
            company_id=self.company.id)
        user.company_ids = [(4, self.company.id)]
        return user

    # ------------------------------------------------------------------
    # Group hierarchy
    # ------------------------------------------------------------------
    def test_group_hierarchy_is_cumulative(self):
        manager = self._user('re_sec_mgr', 'atmta_real_estate.group_rental_manager')
        for group in ('group_property_manager', 'group_rental_agent',
                      'group_rental_user'):
            self.assertTrue(
                manager.has_group('atmta_real_estate.%s' % group),
                "Rental Manager must imply %s" % group)

    def test_rental_user_does_not_imply_manager(self):
        user = self._user('re_sec_user', 'atmta_real_estate.group_rental_user')
        self.assertFalse(
            user.has_group('atmta_real_estate.group_rental_manager'))

    def test_leasing_roles_are_internal_users(self):
        user = self._user('re_sec_internal', 'atmta_real_estate.group_rental_user')
        self.assertTrue(user.has_group('base.group_user'))

    def test_no_leasing_role_grants_accounting_management(self):
        """Rental staff must not silently become accountants."""
        manager = self._user('re_sec_noacct',
                             'atmta_real_estate.group_rental_manager')
        self.assertFalse(manager.has_group('account.group_account_manager'))

    # ------------------------------------------------------------------
    # ACLs
    # ------------------------------------------------------------------
    def test_rental_user_cannot_create_a_lease_allocation(self):
        user = self._user('re_sec_ro', 'atmta_real_estate.group_rental_user')
        with self.assertRaises(AccessError):
            self.env['realestate.contract.property.line'].with_user(user).create({
                'contract_id': self.lease.id,
                'property_id': self.unit_b.id,
                'start_date': self.today,
            })

    def test_agent_can_create_a_lease_allocation(self):
        user = self._user('re_sec_agent', 'atmta_real_estate.group_rental_agent')
        line = self.env['realestate.contract.property.line'].with_user(user).create({
            'contract_id': self.lease.id,
            'property_id': self.unit_b.id,
            'start_date': self.today,
            'end_date': self.lease.end_date,
        })
        self.assertTrue(line)

    def test_agent_cannot_write_escalations(self):
        """Rent is a commercial decision, not an administrative one."""
        user = self._user('re_sec_agent_esc',
                          'atmta_real_estate.group_rental_agent')
        with self.assertRaises(AccessError):
            self.env['realestate.rent.escalation.rule'].with_user(user).create({
                'contract_id': self.lease.id,
                'effective_date': self.lease.start_date.replace(
                    year=self.lease.start_date.year + 1),
                'escalation_type': 'percentage',
                'percentage': 10.0,
            })

    def test_manager_can_write_escalations(self):
        user = self._user('re_sec_mgr_esc',
                          'atmta_real_estate.group_rental_manager')
        rule = self.env['realestate.rent.escalation.rule'].with_user(user).create({
            'contract_id': self.lease.id,
            'effective_date': self.lease.start_date.replace(
                year=self.lease.start_date.year + 1) - __import__(
                    'datetime').timedelta(days=1),
            'escalation_type': 'percentage',
            'percentage': 10.0,
        })
        self.assertTrue(rule)

    def test_agent_cannot_delete_a_deposit(self):
        user = self._user('re_sec_agent_dep',
                          'atmta_real_estate.group_rental_agent')
        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': self.lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 500.0,
        })
        with self.assertRaises(AccessError):
            deposit.with_user(user).unlink()

    # ------------------------------------------------------------------
    # Server-side workflow gates -- "the button was hidden" is not security
    # ------------------------------------------------------------------
    def test_activation_is_gated_server_side(self):
        agent = self._user('re_sec_agent_act',
                           'atmta_real_estate.group_rental_agent')
        self.activate(self.lease)
        other = self.make_lease(prop=self.unit_b)
        other.action_to_proposal()
        other.action_submit_for_approval()
        other.action_approve_lease()
        other.action_mark_signed()
        with self.assertRaises(AccessError):
            other.with_user(agent).action_activate_lease()

    def test_termination_settlement_is_gated_server_side(self):
        pm = self._user('re_sec_pm_term',
                        'atmta_real_estate.group_property_manager')
        self.activate(self.lease)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': self.lease.id,
            'requested_end_date': self.lease.end_date,
            'effective_date': self.lease.end_date,
            'reason': 'expiry',
        })
        termination.action_give_notice()
        with self.assertRaises(AccessError):
            termination.with_user(pm).action_approve()

    def test_availability_override_is_gated_server_side(self):
        agent = self._user('re_sec_agent_ovr',
                           'atmta_real_estate.group_rental_agent')
        with self.assertRaises(AccessError):
            self.unit_a.with_user(agent).write({
                'availability_override': 'force_available',
                'availability_override_reason': 'No.',
            })

    # ------------------------------------------------------------------
    # Record rules exist
    # ------------------------------------------------------------------
    def test_multi_company_rules_are_installed(self):
        """The suite had zero record rules before this upgrade."""
        rules = self.env['ir.rule'].search([
            ('model_id.model', 'in', (
                'realestate.property', 'realestate.contract',
                'realestate.contract.property.line',
                'realestate.contract.payment',
                'realestate.contract.deposit')),
        ])
        models_covered = set(rules.mapped('model_id.model'))
        self.assertEqual(len(models_covered), 5, "Every core leasing model "
                                                 "must carry a company rule.")
