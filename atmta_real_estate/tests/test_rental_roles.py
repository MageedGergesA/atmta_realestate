"""Rental roles replace the legacy Real Estate groups (rental re-architecture, decision 4)."""

from odoo.addons.base.models.res_users import name_selection_groups
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestRentalRoles(LeaseCase):

    def setUp(self):
        super().setUp()
        self.colleague = self._user('re_roles_colleague')
        self.others_lease = self.make_lease(user_id=self.colleague.id)

    def _group(self, name):
        return self.env.ref('atmta_real_estate.' + name)

    def _user(self, login, *groups, base='base.group_user'):
        user = new_test_user(
            self.env, login=login,
            groups=','.join((base,) + tuple('atmta_real_estate.' + g for g in groups)),
            company_id=self.company.id)
        user.company_ids = [(4, self.company.id)]
        return user

    def _sees(self, user, lease):
        return bool(self.env['realestate.contract'].with_user(user).search(
            [('id', '=', lease.id)]))

    def _role_choice(self):
        category = self.env.ref('atmta_real_estate.module_category_real_estate')
        return next(entry for entry in self.env['res.groups'].get_groups_by_application()
                    if entry[0] == category)

    def _lease_values(self):
        return {
            'partner_id': self.tenant.id,
            'property_id': self.unit_b.id,
            'is_single_property': True,
            'is_multi_property': False,
            'start_date': self.today,
            'end_date': self.others_lease.end_date,
            'price': 900.0,
            'company_id': self.company.id,
            'currency_id': self.currency.id,
        }

    # ------------------------------------------------------------------
    # Mapping legacy users
    # ------------------------------------------------------------------
    def test_legacy_read_only_becomes_rental_user_seeing_every_lease(self):
        user = self._user('re_roles_ro', 'group_realestate_readonly')
        self.assertTrue(self._sees(user, self.others_lease))

        report = user._map_legacy_rental_roles()

        self.assertIn(self._group('group_rental_user'), user.groups_id)
        self.assertIn(self._group('group_rental_all_portfolios'), user.groups_id)
        self.assertNotIn(self._group('group_rental_agent'), user.groups_id)
        self.assertEqual(report['rental_user'], user)
        self.assertTrue(self._sees(user, self.others_lease),
                        "A migrated user must not lose sight of a lease.")

    def test_legacy_user_becomes_agent_and_can_save_a_lease_with_a_unit(self):
        user = self._user('re_roles_user', 'group_realestate_user')
        with self.assertRaises(AccessError):
            self.env['realestate.contract'].with_user(user).create(self._lease_values())

        report = user._map_legacy_rental_roles()

        self.assertIn(self._group('group_rental_agent'), user.groups_id)
        self.assertIn(self._group('group_rental_all_portfolios'), user.groups_id)
        self.assertEqual(report['rental_agent'], user)
        lease = self.env['realestate.contract'].with_user(user).create(self._lease_values())
        self.assertEqual(self.allocations_of(lease).property_id, self.unit_b)
        self.assertTrue(self._sees(user, self.others_lease))

    def test_legacy_manager_becomes_rental_manager_without_the_flag(self):
        user = self._user('re_roles_mgr', 'group_realestate_manager')

        report = user._map_legacy_rental_roles()

        self.assertIn(self._group('group_rental_manager'), user.groups_id)
        self.assertNotIn(self._group('group_rental_all_portfolios'), user.groups_id)
        self.assertEqual(report['rental_manager'], user)
        self.assertFalse(report['all_portfolios'])
        self.assertTrue(self._sees(user, self.others_lease))

    def test_a_user_already_narrowed_to_their_portfolio_is_not_widened(self):
        user = self._user('re_roles_narrow', 'group_rental_user', 'group_realestate_user')
        self.assertFalse(self._sees(user, self.others_lease))

        user._map_legacy_rental_roles()

        self.assertIn(self._group('group_rental_agent'), user.groups_id)
        self.assertNotIn(self._group('group_rental_all_portfolios'), user.groups_id)
        self.assertFalse(self._sees(user, self.others_lease))

    def test_mapping_again_changes_nothing(self):
        users = (self._user('re_roles_again_ro', 'group_realestate_readonly')
                 | self._user('re_roles_again_mgr', 'group_realestate_manager'))
        users._map_legacy_rental_roles()
        groups_after_first_run = {user: user.groups_id for user in users}

        report = users._map_legacy_rental_roles()

        self.assertFalse(any(report.values()))
        for user in users:
            self.assertEqual(user.groups_id, groups_after_first_run[user])

    def test_rental_role_holders_are_left_alone(self):
        users = (self._user('re_roles_agent', 'group_rental_agent')
                 | self._user('re_roles_pm', 'group_property_manager'))
        report = users._map_legacy_rental_roles()
        self.assertFalse(any(report.values()))

    def test_portal_users_are_never_made_internal(self):
        user = self._user('re_roles_portal', 'group_realestate_readonly',
                          base='base.group_portal')

        report = user._map_legacy_rental_roles()

        self.assertTrue(user.share)
        self.assertNotIn(self._group('group_rental_user'), user.groups_id)
        self.assertEqual(report['external'], user)

    # ------------------------------------------------------------------
    # See All Portfolios
    # ------------------------------------------------------------------
    def test_all_portfolios_widens_a_rental_user_but_not_across_companies(self):
        company_b = self.env['res.company'].create({'name': 'Roles Other Estate Co'})
        unit_other = self.env['realestate.property'].create({
            'name': 'Roles Other Co Unit',
            'property_code': 'ROLES-OTH-001',
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 80.0,
            'company_id': company_b.id,
        })
        other_company_lease = self.make_lease(prop=unit_other, company_id=company_b.id)
        user = self._user('re_roles_flag', 'group_rental_user')
        self.assertFalse(self._sees(user, self.others_lease))

        user.groups_id = [(4, self._group('group_rental_all_portfolios').id)]

        self.assertTrue(self._sees(user, self.others_lease))
        self.assertFalse(self._sees(user, other_company_lease))

    def test_all_portfolios_alone_grants_no_access(self):
        user = self._user('re_roles_flag_only', 'group_rental_all_portfolios')
        with self.assertRaises(AccessError):
            self.env['realestate.contract'].with_user(user).search([])

    # ------------------------------------------------------------------
    # The user form
    # ------------------------------------------------------------------
    def test_legacy_groups_are_hidden_and_the_roles_are_one_choice(self):
        hidden = self.env.ref('base.module_category_hidden')
        for name in ('group_realestate_readonly', 'group_realestate_user',
                     'group_realestate_manager'):
            self.assertEqual(self._group(name).category_id, hidden)
        _category, kind, groups, _section = self._role_choice()
        self.assertEqual(kind, 'selection')
        self.assertEqual(groups.mapped(lambda g: g.get_external_id()[g.id]), [
            'atmta_real_estate.group_rental_user',
            'atmta_real_estate.group_rental_agent',
            'atmta_real_estate.group_property_manager',
            'atmta_real_estate.group_rental_manager',
        ])

    def test_demoting_a_rental_manager_on_the_form_removes_hidden_manager_rights(self):
        user = self._user('re_roles_demote', 'group_rental_manager')
        self.assertIn(self._group('group_realestate_manager'), user.groups_id)
        _category, _kind, groups, _section = self._role_choice()

        user.write({name_selection_groups(groups.ids): self._group('group_rental_agent').id})

        self.assertNotIn(self._group('group_rental_manager'), user.groups_id)
        self.assertNotIn(self._group('group_realestate_manager'), user.groups_id)
        self.assertIn(self._group('group_realestate_user'), user.groups_id,
                      "Leasing Agent still implies the legacy User alias.")

    def test_removing_the_role_removes_every_legacy_alias(self):
        user = self._user('re_roles_remove', 'group_rental_agent')
        _category, _kind, groups, _section = self._role_choice()

        user.write({name_selection_groups(groups.ids): False})

        legacy = (self._group('group_realestate_readonly')
                  | self._group('group_realestate_user')
                  | self._group('group_realestate_manager'))
        self.assertFalse(user.groups_id & legacy)

    def test_a_legacy_group_other_modules_give_survives_unrelated_changes(self):
        """Other modules' tests and data still grant a legacy group directly."""
        user = self._user('re_roles_direct', 'group_realestate_user')

        user.groups_id = [(4, self._group('group_rental_self_approval').id)]

        self.assertIn(self._group('group_realestate_user'), user.groups_id)
