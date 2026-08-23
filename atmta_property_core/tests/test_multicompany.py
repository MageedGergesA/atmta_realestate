# -*- coding: utf-8 -*-
"""Company isolation for Property, asserted without the leasing app present.

The rule these tests describe is not the same shape as the Project one, and the
difference is deliberate. ``realestate.project.company_id`` is required, so its
rule can refuse company-less rows outright. ``realestate.property.company_id``
is a stored related of the delegated ``product.template``, and a template with
no company is Odoo's ordinary way of saying "shared" — most of the estate in a
real database is company-less. So this rule admits ``company_id = False``, and
these tests pin that as intended behaviour rather than letting a later tidy-up
"fix" it into hiding most of the estate from everybody.
"""

from odoo.tests import TransactionCase, tagged

CORE_MODELS = ('realestate.property', 'property.type', 'property.usage', 'property.image')


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave3')
class PropertyCoreCompanyCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env['res.company']
        cls.company_a = Company.create({'name': 'W3 Company A'})
        cls.company_b = Company.create({'name': 'W3 Company B'})

        Property = cls.env['realestate.property']
        cls.prop_a = Property.create({'name': 'W3 A Unit', 'company_id': cls.company_a.id})
        cls.prop_b = Property.create({'name': 'W3 B Unit', 'company_id': cls.company_b.id})
        cls.prop_shared = Property.create({'name': 'W3 Shared Unit'})
        cls.prop_shared.company_id = False
        cls.env.flush_all()

        # Access rights granted here rather than taken from a leasing group, so
        # the tests say nothing about who should reach a property — only that
        # whoever does cannot cross a company doing it.
        for model in CORE_MODELS:
            cls.env['ir.model.access'].create({
                'name': 'w3 test access %s' % model,
                'model_id': cls.env['ir.model']._get_id(model),
                'group_id': cls.env.ref('base.group_user').id,
                'perm_read': True, 'perm_write': True,
                'perm_create': True, 'perm_unlink': True,
            })

    def _user(self, login, companies):
        user = self.env['res.users'].create({
            'name': login, 'login': login,
            'company_id': companies[0].id,
            'company_ids': [(6, 0, [c.id for c in companies])],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        return user.with_context(allowed_company_ids=[c.id for c in companies])


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave3')
class TestPropertyCoreCompanyOwnership(PropertyCoreCompanyCommon):

    def test_property_core_owns_company_id(self):
        field = self.env['realestate.property']._fields['company_id']
        self.assertIn('atmta_property_core', field._modules or (),
                      "Property Core must declare company_id itself.")
        self.assertEqual(field.related, 'product_tmpl_id.company_id',
                         "Company is materialised from the delegated product.")
        self.assertTrue(field.store, "The rule filters on it; keep it stored.")

    def test_the_company_reaches_the_delegated_template(self):
        """The field is a writable related, so the column and the product it
        mirrors must agree. If they drifted apart the rule would filter on a
        stale value.

        Asserted on creation rather than by reassigning an existing property:
        Odoo's `stock` refuses to move a product between companies while
        quantities of it exist, and `property_stock.py` gives every property
        quants. That refusal is core behaviour worth keeping, not something for
        this test to work around.
        """
        self.assertEqual(self.prop_a.product_tmpl_id.company_id, self.company_a)
        self.assertEqual(self.prop_b.product_tmpl_id.company_id, self.company_b)
        self.assertFalse(
            self.env['realestate.property']._fields['company_id'].readonly,
            "company_id must stay writable; the leasing module's uniqueness "
            "constraints are scoped by it.")

    def test_the_column_never_drifts_from_the_template(self):
        self.env.cr.execute("""
            SELECT count(*) FROM realestate_property p
            JOIN product_template t ON t.id = p.product_tmpl_id
            WHERE COALESCE(p.company_id, 0) <> COALESCE(t.company_id, 0)""")
        self.assertEqual(self.env.cr.fetchone()[0], 0,
                         "A property's company is out of step with the product "
                         "template it is materialised from.")

    def test_the_rule_is_global_and_owned_by_core(self):
        rule = self.env.ref('atmta_property_core.rule_property_company',
                            raise_if_not_found=False)
        self.assertTrue(rule, "The property company rule is missing.")
        self.assertFalse(rule.groups, "It must not be scoped to a group.")
        self.assertTrue(rule['global'], "It must be global.")
        self.assertEqual(rule.model_id.model, 'realestate.property')


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave3')
class TestPropertyCoreCompanyIsolation(PropertyCoreCompanyCommon):

    def test_company_a_sees_its_own_and_the_shared_but_not_company_b(self):
        user = self._user('w3.only.a', [self.company_a])
        visible = self.env['realestate.property'].with_user(user).search([])
        self.assertIn(self.prop_a, visible)
        self.assertIn(self.prop_shared, visible,
                      "A company-less property is shared, not hidden.")
        self.assertNotIn(self.prop_b, visible)

    def test_company_b_sees_its_own_and_the_shared_but_not_company_a(self):
        user = self._user('w3.only.b', [self.company_b])
        visible = self.env['realestate.property'].with_user(user).search([])
        self.assertIn(self.prop_b, visible)
        self.assertIn(self.prop_shared, visible)
        self.assertNotIn(self.prop_a, visible)

    def test_a_user_in_both_companies_sees_both(self):
        user = self._user('w3.both', [self.company_a, self.company_b])
        visible = self.env['realestate.property'].with_user(user).search([])
        for rec in (self.prop_a, self.prop_b, self.prop_shared):
            self.assertIn(rec, visible)

    def test_a_company_less_property_is_deliberately_visible(self):
        """Pinned on purpose. The Project rule excludes company-less rows; this
        one must not, because property company comes from a delegated product
        template and a template with no company means shared."""
        rule = self.env.ref('atmta_property_core.rule_property_company')
        self.assertIn("('company_id', '=', False)", rule.domain_force,
                      "The rule stopped admitting shared, company-less "
                      "properties. Most of a real estate is company-less; "
                      "excluding them hides it from everybody.")


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave3')
class TestIsolationCannotBeBypassedByAnotherRole(PropertyCoreCompanyCommon):

    def test_adding_a_permissive_group_rule_does_not_widen_the_company(self):
        wide_group = self.env['res.groups'].create({'name': 'W3 Wide Role'})
        self.env['ir.rule'].create({
            'name': 'W3 sees every property',
            'model_id': self.env['ir.model']._get_id('realestate.property'),
            'domain_force': "[(1, '=', 1)]",
            'groups': [(6, 0, wide_group.ids)],
        })
        user = self._user('w3.combined', [self.company_a])
        user.write({'groups_id': [(4, wide_group.id)]})

        visible = self.env['realestate.property'].with_user(user).search([])
        self.assertIn(self.prop_a, visible)
        self.assertNotIn(
            self.prop_b, visible,
            "A second business role widened company isolation. The company "
            "rule must be global so it is ANDed with, not ORed against, "
            "whatever a role grants.")
