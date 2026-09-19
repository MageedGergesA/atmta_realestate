# -*- coding: utf-8 -*-
"""Regressions for the screen sweep of 19 September 2026.

The sweep opened every menu of the suite twice: once as an administrator, and
once as a user holding nothing but the group the menu is offered to.
``Construction/Projects`` and ``Construction/Site/Projects`` -- the same
``realestate.project`` action, reached from two menus -- raised
``AccessError`` on ``realestate.property`` for the Construction User role.

Construction does not own a single field of Property, but every screen that
puts a project in front of a site user names one: the master plan
(``main_property_id``), the visual fallback, and the inventory counters
(``unit_count``, ``available_unit_count``, ``sold_unit_count``) that are
computed from the project's units. A site user who cannot read Property
cannot open the project list at all.

So the floor grants the canonical site role READ on Property, and nothing
else: Construction reads the asset it is building, it never writes it. The
grant is model-wide because an Odoo ACL row has no other shape; the narrowing
that would fit -- only the properties of projects the user is a member of --
would have to read ``project_id`` (declared by the Developer application) and
``construction_member_ids`` (declared here), and no module sits above both.
Property Core's global company rule already bounds what the read returns.

These tests do what the sweep did, and what the web client does when the user
clicks the menu: ``get_views``, then ``web_search_read`` of the fields the
views name. They run it against Property rather than against the project
action, because ``realestate.project`` only carries a property field once the
applications above this floor are installed, and this floor's suite is
deliberately installed without them. The end-to-end screen is what the sweep
covers.

The form view is deliberately not part of it. Property's own form carries the
gallery (``property_Attachment_media_ids`` -> ``property.image``), and that is
a Property application screen no Construction menu opens. What a project
screen reads of a property is its name, its state and how many there are, and
that is what is granted.
"""

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'atmta_v2')
class TestConstructionSiteUserReadsTheProperty(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The sweep's minimal user: the role the Construction menus are
        # offered to, and nothing else. The legacy `group_construction_user`
        # of `real_estate_construction` implies exactly this canonical role.
        cls.site_user = cls.env['res.users'].create({
            'name': 'Sami Site', 'login': 'sweep_construction_site_user',
            'company_id': cls.env.company.id,
            'company_ids': [(6, 0, cls.env.company.ids)],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('atmta_roles.group_construction_site_user').id])],
        })
        cls.property = cls.env['realestate.property'].create({
            'name': 'Site Tower — Plot',
            'company_id': cls.env.company.id,
        })

    def test_a_site_user_reads_the_properties_a_project_screen_names(self):
        """The read every construction project screen makes on Property."""
        model = self.env['realestate.property'].with_user(self.site_user)
        views = model.get_views([(False, 'list'), (False, 'search')])
        names = {node.get('name')
                 for node in etree.fromstring(views['views']['list']['arch']).iter('field')}
        spec = {}
        for name in names:
            field = model._fields.get(name)
            if field is None:
                continue
            spec[name] = ({'fields': {'display_name': {}}}
                          if field.type in ('many2one', 'many2many', 'one2many') else {})
        rows = model.web_search_read([], spec, limit=20)
        ids = [row['id'] for row in rows['records']]
        self.assertIn(self.property.id, ids)
        model.browse(ids[:5]).web_read(spec)

        # The two shapes a project screen actually uses: the master plan
        # many2one, read for its display_name, and the inventory counters,
        # which search the project's units.
        self.assertIn(self.property.id,
                      [r['id'] for r in model.web_search_read(
                          [], {'display_name': {}}, limit=20)['records']])
        self.assertIn(self.property.id, model.search([]).ids)

    def test_a_site_user_never_writes_the_property(self):
        """Read is the whole grant. The asset is not Construction's to change."""
        model = self.env['realestate.property'].with_user(self.site_user)
        self.assertTrue(model.has_access('read'))
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(
                model.has_access(operation),
                "A construction site user may not %s a property." % operation)
        with self.assertRaises(AccessError):
            self.property.with_user(self.site_user).write({'name': 'Renamed by site'})

    def test_the_grant_stops_at_what_a_project_needs(self):
        """Property's own media is not part of any Construction screen.

        Granted because a screen shows it, not because the model is nearby:
        no Construction menu opens a property form, so the gallery stays out.
        """
        self.assertFalse(
            self.env['property.image'].with_user(self.site_user).has_access('read'),
            "Construction was given Property's gallery, which no Construction "
            "screen shows.")
