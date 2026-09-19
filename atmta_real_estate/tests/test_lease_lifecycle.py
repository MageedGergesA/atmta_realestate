"""Lease lifecycle state machine and legacy compatibility (Phase 8)."""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseLifecycle(LeaseCase):

    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------
    def test_full_lifecycle(self):
        self.assertEqual(self.lease.lifecycle_state, 'draft')
        self.lease.action_to_proposal()
        self.assertEqual(self.lease.lifecycle_state, 'proposal')
        self.lease.action_submit_for_approval()
        self.assertEqual(self.lease.lifecycle_state, 'pending_approval')
        self.lease.action_approve_lease()
        self.assertEqual(self.lease.lifecycle_state, 'pending_signature')
        self.assertEqual(self.lease.approved_by_id, self.env.user)
        self.lease.action_mark_signed()
        self.assertEqual(self.lease.signature_status, 'signed')
        self.lease.action_activate_lease()
        self.assertEqual(self.lease.lifecycle_state, 'active')
        self.lease.action_give_notice()
        self.assertEqual(self.lease.lifecycle_state, 'notice')
        self.lease.action_end_lease()
        self.assertEqual(self.lease.lifecycle_state, 'ended')

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def test_illegal_transition_is_rejected(self):
        with self.assertRaises(UserError):
            self.lease._do_transition('ended')

    def test_direct_write_is_validated_too(self):
        """Bypassing the buttons must not bypass the state machine."""
        with self.assertRaises(UserError):
            self.lease.lifecycle_state = 'ended'

    def test_cannot_activate_without_signature(self):
        self.lease.action_to_proposal()
        self.lease.action_submit_for_approval()
        self.lease.action_approve_lease()
        with self.assertRaises(UserError):
            self.lease.action_activate_lease()

    def test_cannot_activate_without_an_allocation(self):
        empty = self.make_lease(prop=False, property_id=False)
        empty.property_line_ids.unlink()
        with self.assertRaises(UserError):
            empty.action_to_proposal()

    def test_proposal_requires_an_allocation(self):
        """`partner_id` is required at the ORM level, so the interesting
        pre-flight check is the one on property allocation."""
        self.lease.property_line_ids.unlink()
        self.lease.property_id = False
        with self.assertRaises(UserError):
            self.lease.action_to_proposal()

    def test_terminal_states_are_terminal(self):
        self.activate(self.lease)
        self.lease.action_end_lease()
        with self.assertRaises(UserError):
            self.lease.action_give_notice()

    # ------------------------------------------------------------------
    # Approval controls (Phase 27)
    # ------------------------------------------------------------------
    def test_approval_requires_rental_manager(self):
        agent = new_test_user(
            self.env, login='re_agent_lifecycle',
            groups='atmta_real_estate.group_rental_agent')
        self.lease.action_to_proposal()
        self.lease.action_submit_for_approval()
        with self.assertRaises(AccessError):
            self.lease.with_user(agent).action_approve_lease()

    def test_self_approval_blocked_when_company_forbids_it(self):
        """Run as a real user: the superuser legitimately bypasses this."""
        manager = new_test_user(
            self.env, login='re_mgr_selfapproval',
            groups='base.group_user,atmta_real_estate.group_rental_manager',
            company_id=self.company.id)
        # mail.thread.message_post reads the company's alias domain, so the
        # user must actually belong to the company it is posting under.
        manager.company_ids = [(4, self.company.id)]
        self.company.re_allow_self_approval = False
        # Create AS the manager so create_uid is genuinely theirs -- writing
        # create_uid afterwards is not the same thing and would not exercise
        # the control.
        lease = self.env['realestate.contract'].with_user(manager).create({
            'partner_id': self.tenant.id,
            'property_id': self.unit_b.id,
            'is_single_property': True,
            'start_date': self.today,
            'end_date': self.today.replace(year=self.today.year + 1),
            'price': 1000.0,
            'company_id': self.company.id,
        })
        self.assertEqual(lease.create_uid, manager)
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        with self.assertRaises(AccessError):
            lease.action_approve_lease()

    def test_self_approval_group_overrides_the_block(self):
        manager = new_test_user(
            self.env, login='re_mgr_selfapproval_ok',
            groups='base.group_user,atmta_real_estate.group_rental_manager,'
                   'atmta_real_estate.group_rental_self_approval',
            company_id=self.company.id)
        manager.company_ids = [(4, self.company.id)]
        self.company.re_allow_self_approval = False
        lease = self.env['realestate.contract'].with_user(manager).create({
            'partner_id': self.tenant.id,
            'property_id': self.parking.id,
            'is_single_property': True,
            'start_date': self.today,
            'end_date': self.today.replace(year=self.today.year + 1),
            'price': 500.0,
            'company_id': self.company.id,
        })
        lease.action_to_proposal()
        lease.action_submit_for_approval()
        lease.action_approve_lease()
        self.assertEqual(lease.lifecycle_state, 'pending_signature')

    def test_company_can_force_the_approval_step(self):
        self.company.re_require_lease_approval = True
        self.lease.action_to_proposal()
        with self.assertRaises(UserError):
            self.lease._do_transition('pending_signature')

    # ------------------------------------------------------------------
    # Legacy bridge
    # ------------------------------------------------------------------
    def test_legacy_state_mirrors_the_lifecycle(self):
        self.assertEqual(self.lease.state, 'draft')
        self.lease.action_to_proposal()
        self.assertEqual(self.lease.state, 'ready')
        self.lease.action_submit_for_approval()
        self.lease.action_approve_lease()
        self.assertEqual(self.lease.state, 'confirmed')
        self.lease.action_mark_signed()
        self.lease.action_activate_lease()
        self.assertEqual(self.lease.state, 'active')

    def test_legacy_state_write_drives_the_lifecycle(self):
        """Old code doing `contract.state = 'active'` must still work."""
        self.lease.action_to_proposal()
        self.lease.action_submit_for_approval()
        self.lease.action_approve_lease()
        self.lease.state = 'active'
        self.assertEqual(self.lease.lifecycle_state, 'active')

    def test_a_plain_user_cannot_write_the_lifecycle_directly(self):
        """The status is reached through the workflow, not by writing it.

        This is the control that matters: the workflow actions check a role
        and the prerequisites for each step, and a bare write went near none
        of them. Superuser stays exempt, so migrations and data loads are
        unaffected; what is closed is the RPC caller.

        The message is asserted, not just the exception. An agent who simply
        could not read the lease would also raise `AccessError`, and a test
        that passed for that reason would prove nothing about the guard.
        """
        agent = new_test_user(
            self.env, login='w26_agent',
            groups='base.group_user,atmta_real_estate.group_rental_agent',
            company_id=self.company.id)
        agent.company_ids = [(4, self.company.id)]
        # Advanced as the test's own user: what is under test is the write,
        # not whether this fixture's agent can read the tenant and property
        # that proposal validation looks at.
        self.lease.action_to_proposal()
        with self.assertRaises(AccessError) as caught:
            self.lease.with_user(agent).write({'lifecycle_state': 'active'})
        self.assertIn('cannot be set by writing to it', str(caught.exception))
        self.lease.invalidate_recordset()
        self.assertEqual(self.lease.lifecycle_state, 'proposal')

    def test_a_lease_cannot_be_created_already_active(self):
        """`create` was the way round a guarded `write`."""
        agent = new_test_user(
            self.env, login='w26_agent2',
            groups='base.group_user,atmta_real_estate.group_rental_agent',
            company_id=self.company.id)
        agent.company_ids = [(4, self.company.id)]
        Lease = self.env['realestate.contract'].with_user(agent)
        with self.assertRaises(AccessError) as caught:
            Lease.create({
                'partner_id': self.tenant.id,
                'property_id': self.unit_a.id,
                'is_single_property': True,
                'is_multi_property': False,
                'start_date': self.today,
                'price': 1000.0,
                'company_id': self.company.id,
                'currency_id': self.currency.id,
                'lifecycle_state': 'active',
            })
        self.assertIn('cannot be created with the status',
                      str(caught.exception))

    def test_chatter_records_every_transition(self):
        before = len(self.lease.message_ids)
        self.lease.action_to_proposal()
        self.assertGreater(len(self.lease.message_ids), before)

    # ------------------------------------------------------------------
    # Allocation mirror
    # ------------------------------------------------------------------
    def test_single_property_lease_mirrors_one_allocation(self):
        allocations = self.allocations_of(self.lease)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations.property_id, self.unit_a)
        self.assertEqual(allocations.origin, 'single')

    def test_changing_the_property_moves_the_allocation(self):
        self.lease.property_id = self.unit_b
        allocations = self.allocations_of(self.lease)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations.property_id, self.unit_b)

    def test_sync_is_idempotent(self):
        self.lease._sync_property_lines()
        self.lease._sync_property_lines()
        self.assertEqual(len(self.allocations_of(self.lease)), 1)
