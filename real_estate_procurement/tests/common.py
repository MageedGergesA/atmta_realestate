# -*- coding: utf-8 -*-
"""Fixtures for the Procurement suite.

Deliberately built on the same shapes the Construction suite uses, because the
whole point of Procurement's financial tests is to compare what Procurement
does against what Construction says — and two different fixture vocabularies
would make that comparison meaningless.
"""

from odoo import fields
from odoo.tests.common import TransactionCase


class ProcurementCommon(TransactionCase):
    """Base fixtures.

    Construction is **not** a dependency of this module — it depends on
    Procurement, not the other way round. The financial integration tests
    therefore skip when Construction is absent rather than forcing a
    dependency that would invert the architecture.
    """

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.today = fields.Date.context_today(cls.env['res.partner'])
        cls.Request = cls.env['realestate.material.request']
        cls.RequestLine = cls.env['realestate.material.request.line']
        cls.PO = cls.env['purchase.order']
        cls.POLine = cls.env['purchase.order.line']
        cls.Project = cls.env['realestate.project']
        # The fixture user both raises and approves, which several
        # maker/checker rules refuse by design. They are lifted here so
        # ordinary fixtures can build approved records; the governance tests
        # assert the refusals explicitly, so the rules stay real.
        cls.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_approval', 'True')
        cls.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.self_approval_limit', '1000000000')
        # M3's own maker/checker rule, lifted for the same reason and in the
        # same place. `test_m3_approval.py` turns it back off and asserts the
        # refusal, so the rule is never only theoretical.
        cls.company.write({
            'procurement_allow_self_approval': True,
            'procurement_self_approval_limit': 1_000_000_000.0,
        })
        approver = cls.env.ref(
            'real_estate_procurement.group_procurement_manager',
            raise_if_not_found=False)
        if approver:
            cls.env.user.groups_id |= approver

        cls.has_construction = 'realestate.construction.commitment' in cls.env
        if cls.has_construction:
            cls.Commitment = cls.env['realestate.construction.commitment']
            cls.Controls = cls.env['realestate.construction.controls']
            cls.Analytic = cls.env['realestate.construction.analytic']
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')

    def _next(self):
        type(self)._seq += 1
        return type(self)._seq

    # ------------------------------------------------------------------
    def _project(self, budget=10_000_000.0, **kwargs):
        seq = self._next()
        vals = {
            'name': 'Procurement Project %d' % seq,
            'code': 'PRC%03d' % seq,
            'company_id': self.company.id,
            'expected_budget': budget,
        }
        vals.update(kwargs)
        return self.Project.create(vals)

    def _require_construction(self):
        if not self.has_construction:
            self.skipTest('real_estate_construction is not installed')

    def _cost_code(self, code=None, name='Works', category='material'):
        self._require_construction()
        return self.env['realestate.construction.cost.code'].create({
            'code': code or 'PRC-%04d' % self._next(),
            'name': name,
            'category': category,
        })

    def _vendor(self, name=None):
        return self.env['res.partner'].create({
            'name': name or 'Vendor %d' % self._next(),
            'supplier_rank': 1,
        })

    def _product(self, price=100.0, vendor=None, service=False):
        """A purchasable product, optionally with a preferred seller."""
        vals = {
            'name': 'Item %d' % self._next(),
            'type': 'service' if service else 'consu',
            'purchase_ok': True,
            'standard_price': price,
            'uom_id': self.uom_unit.id,
            'uom_po_id': self.uom_unit.id,
        }
        if not service:
            vals['is_construction_material'] = True
        product = self.env['product.product'].create(vals)
        if vendor:
            self.env['product.supplierinfo'].create({
                'partner_id': vendor.id,
                'product_tmpl_id': product.product_tmpl_id.id,
                'price': price,
            })
        return product

    def _wbs(self, project, code='A.1', name=None, **kwargs):
        self._require_construction()
        vals = {
            'project_id': project.id,
            'code': code,
            'name': name or 'Node %s' % code,
        }
        vals.update(kwargs)
        return self.env['realestate.construction.wbs'].create(vals)

    def _request(self, project=None, lines=(), priority='0',
                 line_defaults=None, **kwargs):
        """`lines` is a list of (product, qty) pairs."""
        vals = {
            'project_id': project.id if project else False,
            'priority': priority,
            'requested_by_id': self.env.user.id,
        }
        vals.update(kwargs)
        request = self.Request.create(vals)
        for product, qty in lines:
            line_vals = {
                'request_id': request.id,
                'product_id': product.id,
                'qty': qty,
                'uom_id': product.uom_id.id,
            }
            line_vals.update(line_defaults or {})
            self.RequestLine.create(line_vals)
        request.invalidate_recordset()
        return request

    def _baselined_budget(self, project, amount, code):
        """A Construction budget baseline, so budget figures are authoritative."""
        self._require_construction()
        budget = self.env['realestate.construction.budget'].create({
            'project_id': project.id,
            'company_id': self.company.id,
            'line_ids': [(0, 0, {
                'cost_code_id': code.id,
                'description': 'Works',
                'amount_mode': 'lumpsum',
                'original_amount': amount,
            })],
        })
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()
        return budget

    def _confirmed_po(self, project, vendor, lines, confirm=True, package=None):
        """`lines` is a list of (cost_code_or_None, untaxed_amount)."""
        product = self._product(price=1.0, vendor=vendor)
        order_lines = []
        for code, amount in lines:
            order_lines.append((0, 0, {
                'product_id': product.id,
                'name': code.display_name if code else 'Uncoded',
                'product_qty': 1.0,
                'price_unit': amount,
                'taxes_id': [(5, 0, 0)],
                're_cost_code_id': code.id if code else False,
            }))
        po = self.PO.create({
            'partner_id': vendor.id,
            're_project_id': project.id,
            're_package_id': package.id if package else False,
            'order_line': order_lines,
        })
        if confirm:
            po.button_confirm()
        return po

    # ------------------------------------------------------------------
    # M3 — control position fixtures
    # ------------------------------------------------------------------
    def _set_budget_policy(self, policy, project=None):
        """Set the budget-control policy on the company, or one project."""
        if project is not None:
            project.procurement_budget_policy = policy
        else:
            self.company.procurement_budget_policy = policy

    def _set_po_governance(self, policy, project=None):
        if project is not None:
            project.procurement_po_governance = policy
        else:
            self.company.procurement_po_governance = policy

    def _reserved(self, project, cost_code=None):
        """Active reservation against a project, or one of its cost codes."""
        domain = [('project_id', '=', project.id), ('state', '=', 'reserved')]
        if cost_code is not None:
            domain.append(('cost_code_id', '=', cost_code.id))
        return sum(self.env['realestate.procurement.reservation'].search(
            domain).mapped('amount_active'))

    def _position(self, cost_code=None, project=None):
        return self.env[
            'realestate.procurement.control'
        ].get_procurement_control_position(project or self.project, cost_code)

    def _purchase_user(self, login, *extra_groups):
        """Somebody with native Purchase rights and nothing procurement-side.

        The point of the direct-PO tests is exactly this person: Odoo says
        they may confirm a purchase order, and ATMTA has to decide separately
        whether they may commit a project budget.
        """
        groups = ['base.group_user', 'purchase.group_purchase_manager']
        groups += list(extra_groups)
        user = self.env['res.users'].create({
            'name': login, 'login': login, 'email': '%s@example.com' % login,
            'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [self.env.ref(g).id for g in groups])],
        })
        return user

    def _commitment(self, project):
        self._require_construction()
        return sum(
            self.Commitment.current_commitment_by_cost_code(project).values())

    def _confirm(self, order):
        """Confirm an order with tax stripped, as the control basis is net."""
        order.order_line.taxes_id = [(5, 0, 0)]
        order.button_confirm()
        order.invalidate_recordset()
        return order

    def _actual(self, project):
        self._require_construction()
        return sum(self.Analytic.actual_by_cost_code(project).values())


class M3Common(ProcurementCommon):
    """A project with a real budget, and demand that is fully coded.

    Every M3 test needs the same three things before it can say anything:
    a baselined Construction budget (or there is no position to measure
    against), a cost code (or the position is unknown by design), and a
    product with a price (or the amount is unknown, which is also by design).
    Building them once keeps each test about the one thing it is testing.
    """

    def setUp(self):
        super().setUp()
        self._require_construction()
        self.project = self._project()
        self.wbs = self._wbs(self.project, 'A.1')
        self.concrete = self._cost_code('M3-C%s' % self._next(), 'Concrete',
                                        'material')
        self.electrical = self._cost_code('M3-E%s' % self._next(),
                                          'Electrical', 'material')
        self.vendor = self._vendor()
        self.product = self._product(price=1_000.0, vendor=self.vendor)

    def _budget(self, amounts):
        """`amounts` is a list of (cost_code, amount) — one baseline."""
        budget = self.env['realestate.construction.budget'].create({
            'project_id': self.project.id,
            'company_id': self.company.id,
            'line_ids': [(0, 0, {
                'cost_code_id': code.id,
                'description': code.name,
                'amount_mode': 'lumpsum',
                'original_amount': amount,
            }) for code, amount in amounts],
        })
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()
        return budget

    def _demand(self, qty, code=None, unit=1_000.0, approve=True, **kwargs):
        """An approved requisition for `qty * unit` against one cost code."""
        request = self._request(
            self.project, [(self.product, qty)],
            line_defaults={'cost_code_id': (code or self.concrete).id,
                           'wbs_id': self.wbs.id,
                           'estimated_unit_cost': unit},
            **kwargs)
        request.action_submit()
        if approve and request.state == 'submitted':
            request.action_approve()
        return request

    def _reservation(self, request):
        return request.reservation_ids.filtered(
            lambda r: r.state == 'reserved')
