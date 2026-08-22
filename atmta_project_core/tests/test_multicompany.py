# -*- coding: utf-8 -*-
"""Company isolation, asserted without the Development application present.

The point of these tests is not that isolation works — it did before Wave 2, for
two of the three models. It is that isolation is now **this module's property**,
holding on its own terms, so that everything built on Project Core inherits a
boundary rather than a hole.

They are written to pass whether or not `real_estate_developer` is installed.
Nothing here references a Development model, field or group; the access rights
needed to exercise a record rule are granted by the test itself, which is also
the point — the isolation must not depend on *which* ACL somebody hands out.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

CORE_MODELS = (
    'realestate.project',
    'realestate.phase',
    'realestate.project.boundary.point',
)


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave2')
class ProjectCoreCompanyCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env['res.company']
        cls.company_a = Company.create({'name': 'W2 Company A'})
        cls.company_b = Company.create({'name': 'W2 Company B'})

        Project = cls.env['realestate.project']
        cls.project_a = Project.create(
            {'name': 'W2 A Project', 'company_id': cls.company_a.id})
        cls.project_b = Project.create(
            {'name': 'W2 B Project', 'company_id': cls.company_b.id})

        Phase = cls.env['realestate.phase']
        cls.phase_a = Phase.create({'name': 'A-1', 'project_id': cls.project_a.id})
        cls.phase_b = Phase.create({'name': 'B-1', 'project_id': cls.project_b.id})

        Point = cls.env['realestate.project.boundary.point']
        cls.point_a = Point.create(
            {'project_id': cls.project_a.id, 'latitude': 1.0, 'longitude': 2.0})
        cls.point_b = Point.create(
            {'project_id': cls.project_b.id, 'latitude': 3.0, 'longitude': 4.0})

        # Access rights are granted here rather than taken from a Development
        # group, so these tests say nothing about who *should* reach a project —
        # only that whoever does cannot cross a company doing it.
        for model in CORE_MODELS:
            cls.env['ir.model.access'].create({
                'name': 'w2 test access %s' % model,
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


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave2')
class TestProjectCoreCompanyOwnership(ProjectCoreCompanyCommon):
    """Where company truth lives, and where it deliberately does not."""

    def test_project_owns_its_company(self):
        field = self.env['realestate.project']._fields['company_id']
        self.assertIn('atmta_project_core', field._modules or (),
                      "Project Core must declare company_id itself.")
        self.assertTrue(field.required, "company_id must stay required.")
        self.assertFalse(field.related,
                         "The project's company is authoritative, not derived.")

    def test_phase_derives_its_company_and_does_not_duplicate_it(self):
        field = self.env['realestate.phase']._fields['company_id']
        self.assertIn('atmta_project_core', field._modules or ())
        self.assertEqual(field.related, 'project_id.company_id',
                         "A phase must take its company from its project.")
        self.assertTrue(field.store, "The rule filters on it; keep it stored.")
        self.assertEqual(self.phase_a.company_id, self.company_a)

    def test_moving_a_phase_moves_its_company_with_it(self):
        """The derived value is the whole reason Phase needs no column of its
        own: it cannot drift out of step with the project."""
        self.phase_a.project_id = self.project_b
        self.phase_a.flush_recordset()
        self.assertEqual(self.phase_a.company_id, self.company_b)

    def test_boundary_point_has_no_company_column(self):
        """Asserted, not assumed. A second copy of company truth is somewhere
        for the two answers to disagree; the rule reaches through project_id
        instead."""
        self.assertNotIn(
            'company_id', self.env['realestate.project.boundary.point']._fields,
            "A plot corner belongs to whoever owns the project. Adding a "
            "company column here would duplicate that truth for symmetry.")

    def test_no_core_relation_can_hold_a_foreign_company(self):
        """The `check_company` audit, expressed as a test rather than a claim.

        `_check_company_auto` was deliberately NOT set. Every relational field
        on the three core models either points at a comodel with no company at
        all (res.country, res.country.state, res.currency, boundary point), or
        derives its company from the project itself (phase, boundary point), or
        is shared master data that Odoo treats as company-neutral (res.partner,
        res.users). There is nothing left for a company check to compare, so
        adding one would restrict behaviour that works today without closing
        anything.
        """
        offenders = []
        for model in CORE_MODELS:
            for name, field in self.env[model]._fields.items():
                if 'atmta_project_core' not in (field._modules or ()):
                    continue
                comodel = getattr(field, 'comodel_name', None)
                if not comodel or comodel not in self.env:
                    continue
                if name == 'company_id':
                    continue
                comodel_fields = self.env[comodel]._fields
                if 'company_id' not in comodel_fields:
                    continue
                # A comodel that carries a company AND is not derived from this
                # project would need check_company. Phase and boundary point
                # both derive theirs, so they are exempt.
                if comodel in CORE_MODELS:
                    continue
                if comodel in ('res.partner', 'res.users'):
                    continue  # company-neutral shared master data
                offenders.append('%s.%s -> %s' % (model, name, comodel))
        self.assertFalse(
            offenders,
            "These core relations point at company-bearing models and are "
            "neither derived nor shared master data, so the check_company "
            "audit needs redoing: %s" % offenders)


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave2')
class TestProjectCoreCompanyIsolation(ProjectCoreCompanyCommon):
    """A, B, and A+B — on all three models."""

    def test_company_a_sees_only_company_a(self):
        user = self._user('w2.only.a', [self.company_a])
        self.assertIn(self.project_a, self.env['realestate.project'].with_user(user).search([]))
        self.assertNotIn(self.project_b, self.env['realestate.project'].with_user(user).search([]))
        self.assertIn(self.phase_a, self.env['realestate.phase'].with_user(user).search([]))
        self.assertNotIn(self.phase_b, self.env['realestate.phase'].with_user(user).search([]))

    def test_company_b_sees_only_company_b(self):
        user = self._user('w2.only.b', [self.company_b])
        projects = self.env['realestate.project'].with_user(user).search([])
        self.assertIn(self.project_b, projects)
        self.assertNotIn(self.project_a, projects)

    def test_a_boundary_point_does_not_leak_another_companys_plot(self):
        """The gap Wave 2 found. Before the core rule existed, a user confined
        to company A could read every one of company B's boundary points —
        latitude and longitude included, which is the surveyed outline of
        another company's land."""
        user = self._user('w2.plot.a', [self.company_a])
        Point = self.env['realestate.project.boundary.point'].with_user(user)

        visible = Point.search([])
        self.assertIn(self.point_a, visible)
        self.assertNotIn(self.point_b, visible)

        with self.assertRaises(AccessError):
            self.point_b.with_user(user).read(['latitude', 'longitude'])

    def test_a_user_in_both_companies_sees_both(self):
        user = self._user('w2.both', [self.company_a, self.company_b])
        projects = self.env['realestate.project'].with_user(user).search([])
        self.assertIn(self.project_a, projects)
        self.assertIn(self.project_b, projects)
        points = self.env['realestate.project.boundary.point'].with_user(user).search([])
        self.assertIn(self.point_a, points)
        self.assertIn(self.point_b, points)

    def test_a_company_less_project_is_impossible(self):
        """The domains do not admit `company_id = False`, so a company-less row
        would be invisible to everyone rather than visible to everyone. The
        required field is what guarantees none can exist."""
        with self.assertRaises(Exception):
            self.env['realestate.project'].create(
                {'name': 'W2 Orphan', 'company_id': False})


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave2')
class TestIsolationCannotBeBypassedByAnotherRole(ProjectCoreCompanyCommon):
    """The reason the rules are global rather than attached to a group.

    Odoo ANDs global rules together, but ORs the rules belonging to the groups a
    user holds. A company rule attached to a group can therefore be *widened* by
    granting that user a second group carrying a more permissive rule on the
    same model. Company isolation must not be defeatable by handing somebody an
    extra business role.
    """

    def test_the_core_rules_are_global(self):
        for model, xmlid in (
                ('realestate.project', 'atmta_project_core.rule_project_company'),
                ('realestate.phase', 'atmta_project_core.rule_phase_company'),
                ('realestate.project.boundary.point',
                 'atmta_project_core.rule_project_boundary_point_company')):
            rule = self.env.ref(xmlid, raise_if_not_found=False)
            self.assertTrue(rule, "%s is missing." % xmlid)
            # `global` is computed as `not groups` (ir_rule.py:53-55), so the
            # absence of a group is what makes a rule global. Assert both.
            self.assertFalse(rule.groups, "%s must not be scoped to a group." % xmlid)
            self.assertTrue(rule['global'], "%s must be global." % xmlid)
            self.assertEqual(rule.model_id.model, model)

    def test_adding_a_permissive_group_rule_does_not_widen_the_company(self):
        """Simulate exactly the mistake: a business role shipping a see-all rule
        on the same model. The global company rule must still hold."""
        wide_group = self.env['res.groups'].create({'name': 'W2 Wide Role'})
        self.env['ir.rule'].create({
            'name': 'W2 sees every project',
            'model_id': self.env['ir.model']._get_id('realestate.project'),
            'domain_force': "[(1, '=', 1)]",
            'groups': [(6, 0, wide_group.ids)],
        })
        user = self._user('w2.combined', [self.company_a])
        user.write({'groups_id': [(4, wide_group.id)]})

        projects = self.env['realestate.project'].with_user(user).search([])
        self.assertIn(self.project_a, projects)
        self.assertNotIn(
            self.project_b, projects,
            "A second business role widened company isolation. The company "
            "rule must be global so it is ANDed with, not ORed against, "
            "whatever a role grants.")
