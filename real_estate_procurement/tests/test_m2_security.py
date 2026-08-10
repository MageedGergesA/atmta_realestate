# -*- coding: utf-8 -*-
"""M2 — who may do what, proved with real users rather than menu visibility.

Phase 0 recorded a cross-module finding: setting the correct `re_cost_code_id`
as an ordinary purchasing user raised an AccessError out of the Construction
integration. M2 is the milestone that makes cost-code propagation mandatory, so
the finding has to be settled here — and settled the right way round. A buyer
must be able to *reference* an existing cost code. A buyer must not be able to
create, edit or delete one.
"""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install', 'atmta_procurement')
class TestM2Security(ProcurementCommon):

    def setUp(self):
        super().setUp()
        self._require_construction()
        self.project = self._project()
        self.wbs = self._wbs(self.project, 'S.1')
        self.code = self._cost_code('M2S-CONC', 'Concrete', 'material')
        self.vendor = self._vendor()
        self.CostCode = self.env['realestate.construction.cost.code']

    def _user(self, name, groups):
        login = '%s.%d' % (name.lower().replace(' ', '.'), self._next())
        return self.env['res.users'].create({
            'name': name,
            'login': login,
            # Chatter refuses to post for an author with no address, and
            # sourcing posts a note. A real user always has one.
            'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref(xmlid).id for xmlid in groups])],
        })

    def _buyer(self):
        """A legitimate buyer: sources, purchases, reads the project structure.

        Nothing here grants cost-code administration — Construction's user
        group is read-only on both the cost code and the WBS.
        """
        return self._user('Buyer', [
            'base.group_user',
            'real_estate_procurement.group_procurement_user',
            'purchase.group_purchase_user',
            # Read-only on both sides of the project structure. Procurement's
            # own groups deliberately grant neither: reading the developer's
            # projects and reading Construction's cost codes are other
            # modules' rights to give, and a procurement role that silently
            # conferred them would be exactly the kind of quiet escalation
            # M2K forbids.
            'real_estate_developer.group_dev_readonly',
            'real_estate_construction.group_construction_user',
        ])

    def _approved_request(self):
        product = self._product(price=1_000.0, vendor=self.vendor)
        request = self._request(
            self.project, [(product, 2_000.0)],
            line_defaults={'cost_code_id': self.code.id,
                           'wbs_id': self.wbs.id})
        request.action_submit()
        request.action_approve()
        return request

    # -- TEST 10 --------------------------------------------------------
    def test_10_a_buyer_may_reference_a_cost_code_when_sourcing(self):
        """The cross-module contract: reference is not administration."""
        buyer = self._buyer()
        request = self._approved_request()
        self.assertFalse(
            self.code.analytic_account_id,
            "The analytic account is made on first use — which is exactly "
            "when the permission question arises.")

        request.with_user(buyer).action_create_rfqs(vendors=self.vendor)

        line = request.purchase_order_ids.order_line
        self.assertEqual(line.re_cost_code_id, self.code)
        self.assertEqual(line.re_wbs_id, self.wbs)
        self.assertTrue(line.analytic_distribution)

    def test_10b_a_buyer_may_not_administer_the_cost_code_master(self):
        buyer = self._buyer()
        CostCode = self.CostCode.with_user(buyer)

        self.assertTrue(CostCode.browse(self.code.id).code,
                        "Referencing means being able to read it.")
        with self.assertRaises(AccessError):
            CostCode.create({'code': 'BUYER-MADE', 'name': 'Invented'})
        with self.assertRaises(AccessError):
            CostCode.browse(self.code.id).write({'name': 'Renamed'})
        with self.assertRaises(AccessError):
            CostCode.browse(self.code.id).unlink()

    def test_10c_the_wbs_master_is_equally_out_of_reach(self):
        buyer = self._buyer()
        Wbs = self.env['realestate.construction.wbs'].with_user(buyer)
        self.assertTrue(Wbs.browse(self.wbs.id).code)
        with self.assertRaises(AccessError):
            Wbs.browse(self.wbs.id).write({'name': 'Renamed'})

    # -- TEST 11 --------------------------------------------------------
    def test_11_a_request_cannot_cross_company_boundaries(self):
        """A genuine two-company user, not a permissions accident.

        The user below is allowed in both companies. What is refused is not
        reading the other company's project — it is building one procurement
        document out of two companies' records.
        """
        other_company = self.env['res.company'].create(
            {'name': 'Other Co %d' % self._next()})
        self.env.user.company_ids = [(4, other_company.id)]
        foreign_project = self.Project.create({
            'name': 'Foreign Project',
            'code': 'FGN%03d' % self._next(),
            'company_id': other_company.id,
        })
        product = self._product(price=100.0)
        with self.assertRaises(ValidationError):
            self._request(foreign_project, [(product, 1.0)])

    def test_11b_a_plan_stays_inside_one_company(self):
        other_company = self.env['res.company'].create(
            {'name': 'Other Co %d' % self._next()})
        self.env.user.company_ids = [(4, other_company.id)]
        foreign_project = self.Project.create({
            'name': 'Foreign Project',
            'code': 'FGN%03d' % self._next(),
            'company_id': other_company.id,
        })
        # `ValidationError` derives from `UserError`, and `check_company`
        # raises the latter — either refusal is the right one.
        with self.assertRaises(UserError):
            self.env['realestate.procurement.plan'].create({
                'title': 'Cross-company plan',
                'company_id': self.company.id,
                'project_id': foreign_project.id,
            })

    # -- M2J — project access -------------------------------------------
    def test_20_project_team_membership_governs_procurement_records(self):
        """Procurement follows Construction's project access. It invents none.

        Construction says a project with a named team is visible to that team.
        Procurement does not keep a second list of allowed projects on the
        user — it asks the same question of the same field.
        """
        buyer = self._buyer()
        insider = self._buyer()
        restricted = self._project()
        restricted.construction_member_ids = [(6, 0, [insider.id])]
        product = self._product(price=100.0)

        with self.assertRaises(AccessError):
            self._request_as(buyer, restricted, product)

        request = self._request_as(insider, restricted, product)
        self.assertEqual(request.project_id, restricted)

    def test_20b_an_open_project_stays_open(self):
        buyer = self._buyer()
        product = self._product(price=100.0)
        self.assertFalse(self.project.construction_member_ids)
        request = self._request_as(buyer, self.project, product)
        self.assertEqual(request.project_id, self.project)

    def _request_as(self, user, project, product):
        request = self.Request.with_user(user).create({
            'project_id': project.id,
            'requested_by_id': user.id,
        })
        self.RequestLine.with_user(user).create({
            'request_id': request.id,
            'product_id': product.id,
            'qty': 1.0,
            'uom_id': product.uom_id.id,
        })
        return request

    # -- Requester vs buyer ----------------------------------------------
    def test_21_a_requester_does_not_need_purchasing_rights(self):
        """Asking for materials is not the same as being able to buy them."""
        requester = self._user('Requester', [
            'base.group_user',
            'real_estate_procurement.group_procurement_requester',
            'real_estate_developer.group_dev_readonly',
        ])
        product = self._product(price=100.0)
        request = self._request_as(requester, self.project, product)
        self.assertEqual(request.state, 'draft')
        request.with_user(requester).action_submit()
        self.assertEqual(request.state, 'submitted')

        with self.assertRaises(AccessError):
            self.PO.with_user(requester).create({'partner_id': self.vendor.id})

    def test_21b_a_requester_may_not_source_their_own_request(self):
        requester = self._user('Requester', [
            'base.group_user',
            'real_estate_procurement.group_procurement_requester',
            'real_estate_developer.group_dev_readonly',
        ])
        request = self._approved_request()
        with self.assertRaises(AccessError):
            request.with_user(requester).action_create_rfqs(
                vendors=self.vendor)
