"""The lease lifecycle driven through its forms, as the web client drives it.

Records built with ``create()`` never go through the unsaved records a form
builds while the user edits it. A compute or onchange that cannot handle those
passes every such test and fails in the browser -- adding a second rent
escalation on the lease form raised ``TypeError: '<' not supported between
instances of 'NewId' and 'NewId'``. Every step here goes through
``odoo.tests.Form`` on the real views, so what a field's visibility, required
flag or onchange would stop in the UI stops here too.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests import Form, tagged

from .common import LeaseCase

LEASE_FORM = 'atmta_real_estate.view_realestate_contract_form'


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestLeaseFormCycle(LeaseCase):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _lease_form(self, record=None):
        return Form(record or self.env['realestate.contract'], view=LEASE_FORM)

    def _new_lease_form(self, unit=None, start=None, months=24, rent=1000.0):
        start = start or self.today
        form = self._lease_form()
        form.partner_id = self.tenant
        form.property_id = unit or self.unit_a
        form.start_date = start
        form.end_date = start + relativedelta(months=months, days=-1)
        form.price = rent
        return form

    def _add_escalation(self, form, when, kind='percentage', value=5.0):
        with form.escalation_rule_ids.new() as line:
            line.effective_date = when
            line.escalation_type = kind
            if kind == 'percentage':
                line.percentage = value
            elif kind == 'fixed':
                line.fixed_amount = value
            else:
                line.scheduled_amount = value

    def _active_lease(self, unit=None, start=None, months=12):
        lease = self._new_lease_form(unit=unit, start=start, months=months).save()
        return self.activate(lease)

    # ------------------------------------------------------------------
    # Lines added on the lease form
    # ------------------------------------------------------------------
    def test_escalations_added_on_a_new_lease(self):
        start = self.today
        form = self._new_lease_form(start=start)
        self._add_escalation(form, start + relativedelta(years=1), 'percentage', 5.0)
        self._add_escalation(form, start + relativedelta(months=18), 'fixed', 100.0)
        lease = form.save()

        rules = lease.escalation_rule_ids.sorted('effective_date')
        self.assertEqual(rules.mapped('base_amount'), [1000.0, 1050.0])
        self.assertEqual(rules.mapped('resulting_amount'), [1050.0, 1150.0])

    def test_an_escalation_added_to_a_saved_lease_rechains_the_rent(self):
        start = self.today
        form = self._new_lease_form(start=start)
        self._add_escalation(form, start + relativedelta(years=1), 'percentage', 5.0)
        lease = form.save()

        form = self._lease_form(lease)
        self._add_escalation(form, start + relativedelta(months=6), 'percentage', 3.0)
        form.save()

        rules = lease.escalation_rule_ids.sorted('effective_date')
        self.assertEqual(len(rules), 2)
        self.assertAlmostEqual(rules[0].resulting_amount, 1030.0, places=2)
        self.assertAlmostEqual(rules[1].base_amount, 1030.0, places=2)
        self.assertAlmostEqual(rules[1].resulting_amount, 1081.5, places=2)

    def test_adding_a_line_while_another_escalation_takes_effect_today(self):
        """The browser's case: "Add a line" opens a row with no date yet.

        The rent chain sorts rules by (date, sequence, id), counting a missing
        date as today. The new row therefore ties with a rule effective today,
        and both are unsaved records on the form, so the tie fell through to
        comparing their NewIds.
        """
        start = self.today - relativedelta(months=6)
        form = self._new_lease_form(start=start)
        self._add_escalation(form, self.today, 'percentage', 5.0)
        lease = form.save()

        form = self._lease_form(lease)
        with form.escalation_rule_ids.new() as line:
            # Onchange has already run for the undated row at this point.
            line.effective_date = self.today + relativedelta(months=3)
            line.percentage = 2.0
        form.save()

        rules = lease.escalation_rule_ids.sorted('effective_date')
        self.assertEqual(len(rules), 2)
        self.assertAlmostEqual(rules[0].resulting_amount, 1050.0, places=2)
        self.assertAlmostEqual(rules[1].resulting_amount, 1071.0, places=2)

    def test_charges_incentives_and_parties_added_on_the_form(self):
        start = self.today
        form = self._new_lease_form(start=start)
        with form.charge_rule_ids.new() as charge:
            charge.name = 'Service charge'
            charge.amount = 50.0
        with form.incentive_ids.new() as incentive:
            incentive.name = 'Launch discount'
            incentive.incentive_type = 'percent_discount'
            incentive.start_date = start
            incentive.end_date = start + relativedelta(months=2, days=-1)
            incentive.percentage = 10.0
        with form.party_ids.new() as party:
            party.partner_id = self.co_tenant
            party.role = 'co_tenant'
        lease = form.save()

        self.assertEqual(lease.charge_rule_ids.name, 'Service charge')
        self.assertEqual(lease.incentive_ids.percentage, 10.0)
        self.assertIn(self.co_tenant, lease.party_ids.partner_id)

    def test_a_several_unit_lease_built_on_the_form(self):
        start = self.today
        form = self._lease_form()
        form.partner_id = self.tenant
        form.start_date = start
        form.end_date = start + relativedelta(years=1, days=-1)
        form.price = 2000.0
        form.is_multi_property = True
        for unit, rent in ((self.unit_a, 1200.0), (self.unit_b, 800.0)):
            with form.property_line_ids.new() as line:
                line.property_id = unit
                line.start_date = start
                line.allocated_rent = rent
        lease = form.save()

        self.assertEqual(set(self.allocations_of(lease).property_id.ids),
                         {self.unit_a.id, self.unit_b.id})

    # ------------------------------------------------------------------
    # The documents around a live lease, through their own forms
    # ------------------------------------------------------------------
    def test_amendment_form(self):
        lease = self._active_lease()
        lease.action_generate_billing_schedule()
        form = Form(self.env['realestate.contract.amendment'].with_context(
            default_contract_id=lease.id))
        form.amendment_type = 'rent_change'
        form.effective_date = self.today + relativedelta(months=6)
        form.new_rent = 1100.0
        form.reason = 'Mid-term review'
        amendment = form.save()
        amendment.action_propose()
        amendment.action_approve()
        amendment.action_sign()
        amendment.action_apply()
        self.assertEqual(amendment.state, 'applied')

    def test_renewal_form(self):
        start = self.today - relativedelta(months=11)
        lease = self._active_lease(start=start, months=12)
        action = lease.action_start_renewal()
        form = Form(self.env['realestate.contract.renewal'].with_context(
            **action.get('context', {})))
        form.proposed_start_date = lease.end_date + relativedelta(days=1)
        form.proposed_end_date = lease.end_date + relativedelta(years=1)
        form.proposed_rent = 1080.0
        renewal = form.save()
        renewal.action_propose()
        renewal.action_approve()
        renewal.action_accept()
        renewal.action_create_renewal_lease()
        self.assertEqual(renewal.state, 'renewed')
        self.assertTrue(renewal.new_contract_id)

    def test_termination_form(self):
        lease = self._active_lease()
        form = Form(self.env['realestate.contract.termination'].with_context(
            default_contract_id=lease.id))
        form.requested_end_date = self.today + relativedelta(months=1)
        form.effective_date = self.today + relativedelta(months=1)
        form.reason = 'tenant_notice'
        termination = form.save()
        termination.action_give_notice()
        self.assertEqual(lease.lifecycle_state, 'notice')

    def test_move_in_and_move_out_forms(self):
        lease = self._active_lease()
        form = Form(self.env['realestate.move.in'].with_context(default_contract_id=lease.id))
        form.property_id = self.unit_a
        form.scheduled_date = self.today
        form.tenant_acknowledged = True
        move_in = form.save()
        move_in.action_complete()
        self.assertEqual(move_in.state, 'completed')

        form = Form(self.env['realestate.move.out'].with_context(default_contract_id=lease.id))
        form.property_id = self.unit_a
        form.scheduled_date = self.today
        form.tenant_acknowledged = True
        move_out = form.save()
        move_out.action_start_inspection()
        move_out.action_complete()
        self.assertEqual(move_out.state, 'completed')

    def test_deposit_form(self):
        lease = self._active_lease()
        form = Form(self.env['realestate.contract.deposit'].with_context(
            default_contract_id=lease.id))
        form.requested_amount = 2000.0
        deposit = form.save()
        deposit.action_request()
        self.assertEqual(deposit.contract_id, lease)
