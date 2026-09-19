# -*- coding: utf-8 -*-
"""Findings of the operations lifecycle run, each pinned by a test.

Every test here failed before its fix, for the reason its docstring gives.
"""

import unittest
from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestTicketLifecycleFindings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Ticket = cls.env['realestate.customer.ticket']
        Category = cls.env['realestate.customer.ticket.category']
        cls.cat8 = Category.create({'name': 'Plumbing (8h)', 'default_sla_hours': 8})
        cls.cat72 = Category.create({'name': 'Noise (72h)', 'default_sla_hours': 72})
        cls.cat4 = Category.create({'name': 'Security (4h)', 'default_sla_hours': 4})
        cls.agent = new_test_user(cls.env, login='cs_findings_agent',
                                  groups='base.group_user,real_estate_customer_service.group_service_agent')
        cls.reporter = cls.env['res.partner'].create({'name': 'Findings Reporter'})

    def _ticket(self, **vals):
        values = {'title': 'Ticket', 'partner_id': self.reporter.id}
        values.update(vals)
        return self.Ticket.create(values)

    # 5 ------------------------------------------------------------------
    def _form_create(self, **vals):
        """What the form does: onchange on a new record, then create() with the
        values it derived.

        ``odoo.tests.Form`` cannot be used here: without Procurement the view's
        ``material_request_id`` field has no model and ``web_read`` fails."""
        draft = self.Ticket.new(dict(vals, partner_id=self.reporter.id))
        values = dict(vals, partner_id=self.reporter.id, sla_deadline=draft.sla_deadline)
        return self.Ticket.create(values)

    def test_a_ticket_created_through_the_form_gets_its_deadline(self):
        """The compute needed create_date, which a form has not got: it saved False."""
        before = fields.Datetime.now().replace(microsecond=0)
        ticket = self._form_create(title='Leak under sink', category_id=self.cat8.id)
        self.assertTrue(ticket.sla_deadline)
        after = fields.Datetime.now() + timedelta(seconds=1)
        self.assertGreaterEqual(ticket.sla_deadline, before + timedelta(hours=8))
        self.assertLessEqual(ticket.sla_deadline, after + timedelta(hours=8))
        self.assertFalse(ticket.sla_deadline_manual)

    def test_a_ticket_created_in_code_gets_its_deadline(self):
        ticket = self._ticket(category_id=self.cat8.id)
        self.assertEqual(ticket.sla_deadline, ticket.create_date + timedelta(hours=8))

    # 6 ------------------------------------------------------------------
    def test_changing_category_rederives_the_deadline(self):
        """The compute skipped any ticket that already had a deadline."""
        ticket = self._ticket(category_id=self.cat72.id)
        ticket.category_id = self.cat4
        self.assertEqual(ticket.sla_deadline, ticket.create_date + timedelta(hours=4))
        # The same as the form saves it: the category with the deadline its
        # onchange derived.
        draft = ticket.new(origin=ticket)
        draft.category_id = self.cat8
        ticket.write({'category_id': self.cat8.id, 'sla_deadline': draft.sla_deadline})
        self.assertFalse(ticket.sla_deadline_manual)
        self.assertEqual(ticket.sla_deadline, ticket.create_date + timedelta(hours=8))

    def test_writing_back_the_derived_deadline_does_not_freeze_it(self):
        """A write that re-states the deadline the category already implies is
        not somebody choosing a date: an import, an RPC or a portal that sends
        the field back with its own value was marked as a manual override, and
        the ticket then kept a 72h deadline inside a 4h category -- an SLA
        reported as met when it was missed."""
        ticket = self._ticket(category_id=self.cat72.id)
        ticket.write({'sla_deadline': ticket.create_date + timedelta(hours=72)})
        self.assertFalse(ticket.sla_deadline_manual)
        ticket.category_id = self.cat4
        self.assertEqual(ticket.sla_deadline, ticket.create_date + timedelta(hours=4))

    def test_a_manually_set_deadline_survives_a_category_change(self):
        ticket = self._ticket(category_id=self.cat72.id)
        agreed = fields.Datetime.now().replace(microsecond=0) + timedelta(days=10)
        ticket.sla_deadline = agreed
        self.assertTrue(ticket.sla_deadline_manual)
        ticket.category_id = self.cat4
        self.assertEqual(ticket.sla_deadline, agreed)

    # 15b / U3 -----------------------------------------------------------
    def test_a_resolved_ticket_is_not_cancelled_as_resolved(self):
        """Cancel was offered on a resolved ticket and kept resolved_on."""
        ticket = self._ticket(assignee_id=self.agent.id, state='assigned')
        ticket.action_start()
        ticket.action_resolve()
        self.assertTrue(ticket.resolved_on)
        with self.assertRaises(UserError):
            ticket.action_cancel()
        ticket.action_reopen()
        ticket.action_cancel()
        self.assertEqual(ticket.state, 'cancelled')
        self.assertFalse(ticket.resolved_on)

    def test_cancel_clears_the_resolution_stamp(self):
        ticket = self._ticket(state='in_progress', resolved_on=fields.Datetime.now())
        ticket.action_cancel()
        self.assertFalse(ticket.resolved_on)

    def test_cancel_button_follows_the_method(self):
        arch = self.Ticket.get_view(
            self.env.ref('real_estate_customer_service.view_ticket_form').id, 'form')['arch']
        button = etree.fromstring(arch).xpath("//button[@name='action_cancel']")[0]
        for state in ('resolved', 'closed', 'cancelled'):
            self.assertTrue(safe_eval(button.get('invisible'), {'state': state}), state)
        for state in ('new', 'assigned', 'in_progress', 'waiting'):
            self.assertFalse(safe_eval(button.get('invisible'), {'state': state}), state)

    # U3 -----------------------------------------------------------------
    def test_resolve_and_wait_need_a_ticket_being_worked(self):
        """action_resolve / action_wait_customer accepted any state."""
        closed = self._ticket(state='closed')
        with self.assertRaises(UserError):
            closed.action_resolve()
        new = self._ticket()
        with self.assertRaises(UserError):
            new.action_resolve()
        with self.assertRaises(UserError):
            new.action_wait_customer()
        cancelled = self._ticket(state='cancelled')
        with self.assertRaises(UserError):
            cancelled.action_wait_customer()

    def test_reopen_without_assignee_goes_back_to_new(self):
        """Reopen always said 'assigned', even with nobody assigned."""
        ticket = self._ticket(state='resolved', resolved_on=fields.Datetime.now())
        ticket.action_reopen()
        self.assertEqual(ticket.state, 'new')
        assigned = self._ticket(state='resolved', assignee_id=self.agent.id)
        assigned.action_reopen()
        self.assertEqual(assigned.state, 'assigned')

    # 16 -----------------------------------------------------------------
    def test_material_request_needs_procurement_rights(self):
        """The button showed for agents who cannot create a material request."""
        if 'realestate.material.request' not in self.env.registry:
            raise unittest.SkipTest('Procurement is not installed in this database.')
        MR = self.env['realestate.material.request']
        self.assertFalse(MR.with_user(self.agent).has_access('create'))
        ticket = self._ticket(title='Needs a part').with_user(self.agent)
        self.assertFalse(ticket.material_request_available)
        with self.assertRaises(UserError):
            ticket.action_create_material_request()
        self.assertTrue(self._ticket(title='Admin part').material_request_available)
