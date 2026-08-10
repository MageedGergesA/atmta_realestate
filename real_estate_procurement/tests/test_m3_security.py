# -*- coding: utf-8 -*-
"""M3W/M3X/M3Y — who may do what to a control record, proved with real users.

Two separate questions, and M3 keeps them apart:

```
    ACCESS      may this person read or change this record
    AUTHORITY   may this person take this decision
```

Access rights answer the first. They cannot answer the second, because in a
small company almost everybody is in the approver group — which is exactly why
the maker/checker rule reads identity rather than membership.

Buttons answer neither. Every check asserted here is on the server, reached by
calling the method directly, because that is how RPC, imports and other
modules arrive.
"""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import M3Common


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM3Security(M3Common):

    def setUp(self):
        super().setUp()
        self._budget([(self.concrete, 10_000_000.0)])
        self.Reservation = self.env['realestate.procurement.reservation']
        self.Exception_ = self.env[
            'realestate.procurement.control.exception']

    def _user(self, name, *groups):
        login = '%s.m3sec.%d' % (name.lower(), self._next())
        return self.env['res.users'].create({
            'name': name, 'login': login,
            'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [self.env.ref(xmlid).id for xmlid in (
                'base.group_user', 'real_estate_developer.group_dev_readonly',
                'real_estate_construction.group_construction_user') + groups
            ])],
        })

    # -- Requester ------------------------------------------------------
    def test_a_requester_cannot_create_a_reservation_by_hand(self):
        """Reserving is what approval does. It is not a record you write."""
        requester = self._user(
            'Requester', 'real_estate_procurement.group_procurement_requester')
        request = self._demand(1_000.0)

        with self.assertRaises(AccessError):
            self.Reservation.with_user(requester).create({
                'company_id': self.company.id,
                'project_id': self.project.id,
                'request_id': request.id,
                'amount_reserved': 5_000_000.0,
                'state': 'reserved',
            })

    def test_a_requester_cannot_approve_or_release(self):
        requester = self._user(
            'Requester', 'real_estate_procurement.group_procurement_requester')
        request = self._demand(1_000.0, approve=False)

        with self.assertRaises(UserError):
            request.with_user(requester).action_approve()

        reservation = self._demand(500.0).reservation_ids
        with self.assertRaises(AccessError):
            reservation.with_user(requester)._release('Because I said so.')

    # -- Buyer ----------------------------------------------------------
    def test_a_buyer_sources_but_does_not_authorise(self):
        buyer = self._user(
            'Buyer', 'real_estate_procurement.group_procurement_user',
            'purchase.group_purchase_user')
        request = self._demand(1_000.0, approve=False)

        with self.assertRaises(UserError):
            request.with_user(buyer).action_approve()

        request.action_approve()
        request.with_user(buyer).action_create_rfqs(vendors=self.vendor)
        self.assertEqual(len(request.purchase_order_ids), 1)

    def test_a_buyer_cannot_grant_a_budget_exception(self):
        buyer = self._user(
            'Buyer', 'real_estate_procurement.group_procurement_user')
        exception = self.Exception_.create({
            'exception_type': 'over_budget',
            'company_id': self.company.id,
            'project_id': self.project.id,
            'requested_amount': 1_000_000.0,
            'reason': 'Needed sooner than the budget allows.',
        })

        with self.assertRaises(UserError):
            exception.with_user(buyer).action_approve()
        self.assertEqual(exception.state, 'requested')

    def test_activation_and_release_are_manager_decisions(self):
        approver = self._user(
            'Approver', 'real_estate_procurement.group_procurement_approver')
        request = self._demand(1_000.0)
        reservation = request.reservation_ids

        wizard = self.env['realestate.procurement.reservation.release'].create(
            {'reservation_id': reservation.id, 'reason': 'Changed our mind.'})
        with self.assertRaises(UserError):
            wizard.with_user(approver).action_release()
        self.assertEqual(reservation.state, 'reserved')

        wizard.action_release()
        self.assertEqual(reservation.state, 'released')

    def test_an_exception_cannot_be_approved_by_whoever_asked_for_it(self):
        self.company.procurement_allow_self_approval = False
        manager = self._user(
            'Manager', 'real_estate_procurement.group_procurement_manager')
        exception = self.Exception_.with_user(manager).create({
            'exception_type': 'over_budget',
            'company_id': self.company.id,
            'project_id': self.project.id,
            'requested_amount': 1_000_000.0,
            'reason': 'Urgent pour.',
        })

        with self.assertRaises(UserError):
            exception.with_user(manager).action_approve()

        other = self._user(
            'Other Manager',
            'real_estate_procurement.group_procurement_manager')
        exception.with_user(other).action_approve()
        self.assertEqual(exception.approved_by_id, other)

    def test_a_decided_exception_is_not_deletable(self):
        exception = self.Exception_.create({
            'exception_type': 'over_budget',
            'company_id': self.company.id,
            'project_id': self.project.id,
            'requested_amount': 1_000.0,
            'reason': 'Recorded.',
            'state': 'noted',
        })
        with self.assertRaises(UserError):
            exception.unlink()

    # -- M3W multi-company ----------------------------------------------
    def test_a_reservation_cannot_cross_a_company_boundary(self):
        other_company = self.env['res.company'].create({'name': 'M3 Other'})
        request = self._demand(1_000.0)

        with self.assertRaises(ValidationError):
            request.reservation_ids.with_context(
                re_reservation_engine=True).write(
                    {'company_id': other_company.id})

    def test_an_exception_cannot_authorise_across_companies(self):
        other_company = self.env['res.company'].create({'name': 'M3 Other 2'})
        with self.assertRaises(ValidationError):
            self.Exception_.create({
                'exception_type': 'over_budget',
                'company_id': other_company.id,
                'project_id': self.project.id,
                'requested_amount': 1_000.0,
                'reason': 'Wrong books.',
            })

    def test_control_records_are_company_scoped_by_record_rule(self):
        for model in ('realestate.procurement.reservation',
                      'realestate.procurement.reservation.conversion',
                      'realestate.procurement.control.exception',
                      'realestate.procurement.approval.step'):
            rules = self.env['ir.rule'].search([
                ('model_id.model', '=', model)])
            self.assertTrue(
                any('company' in rule.domain_force and not rule.groups
                    for rule in rules),
                "%s has no global company rule, so one company's control "
                "records are visible in another's." % model)

    # -- M3X project access ---------------------------------------------
    def test_approval_authority_does_not_open_every_project(self):
        """Being able to approve is not being able to see everything.

        Procurement's groups grant no project access of their own — reading
        the developer's projects and Construction's cost codes are other
        modules' rights to give. An approver who has not been given them
        cannot browse projects sideways through a procurement role.
        """
        approver = self.env['res.users'].create({
            'name': 'Bare Approver',
            'login': 'bare.approver.%d' % self._next(),
            'email': 'bare@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'real_estate_procurement.group_procurement_approver').id,
            ])],
        })

        with self.assertRaises(AccessError):
            self.env['realestate.project'].with_user(approver).search([])
