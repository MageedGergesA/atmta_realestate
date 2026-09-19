# -*- coding: utf-8 -*-
"""Shared fixtures for the developer sales suite.

The module had no tests at all before 0.3. Everything here builds the smallest
believable commercial setup — a company, a project, a phase and some units —
so individual tests can say what they are actually about.
"""

from odoo.tests.common import TransactionCase


def shown_menu_ids(env, user):
    """Ids of the menus ``user`` actually gets in the web client.

    ``ir.ui.menu._visible_menu_ids`` keeps a menu with an action even when its
    parent section is hidden; the client builds the tree from the root down,
    so such a menu is never on screen. Only menus whose every ancestor is
    visible count.

    A copy of the Rental suite's helper: importing it from
    ``atmta_real_estate.tests`` fails wherever Rental's code is not deployed."""
    raw = env['ir.ui.menu'].with_user(user)._visible_menu_ids()
    ids = set()
    for menu in env['ir.ui.menu'].browse(raw):
        node = menu
        while node and node.id in raw:
            node = node.parent_id
        if not node:
            ids.add(menu.id)
    return ids



def module_installed(env, name):
    """Whether an addon is installed in the database the test runs in."""
    return bool(env['ir.module.module'].sudo().search_count(
        [('name', '=', name), ('state', '=', 'installed')]))


def _spec(env, model, node):
    """The ``web_read`` specification a form view asks for, for one field node.

    A copy of the Rental suite's helper: importing it from
    ``atmta_real_estate.tests`` fails wherever Rental's code is not deployed."""
    field = env[model]._fields.get(node.get('name'))
    if field is None:
        return None
    if field.type in ('one2many', 'many2many'):
        sub = {}
        for child in node.iter('field'):
            if child is node:
                continue
            child_field = env[field.comodel_name]._fields.get(child.get('name'))
            if child_field is not None:
                sub[child.get('name')] = ({'fields': {'display_name': {}}}
                                          if child_field.type == 'many2one' else {})
        return {'fields': sub or {'display_name': {}}}
    if field.type == 'many2one':
        return {'fields': {'display_name': {}}}
    return {}



class DeveloperCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Property = cls.env['realestate.property']
        cls.Project = cls.env['realestate.project']
        cls.Phase = cls.env['realestate.phase']
        cls.Release = cls.env['realestate.unit.release.batch']
        cls.Block = cls.env['realestate.unit.block']

        cls.project = cls.Project.create({
            'name': 'Palm Heights',
            'code': 'PH',
            'company_id': cls.company.id,
        })
        cls.phase = cls.Phase.create({
            'name': 'Phase 1',
            'project_id': cls.project.id,
        })
        cls.units = cls._make_units(cls, count=3)

    def _make_units(self, count=1, prefix='PH-U', phase=None, project=None):
        """Create leaf units attached to the fixture project/phase."""
        project = project if project is not None else self.project
        phase = phase if phase is not None else self.phase
        return self.Property.create([{
            'name': '%s-%03d' % (prefix, i),
            'property_code': '%s-%03d' % (prefix, i),
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'area_sqm': 100.0 + i,
            'company_id': self.company.id if hasattr(self, 'company') else self.env.company.id,
            'project_id': project.id,
            'phase_id': phase.id if phase else False,
        } for i in range(1, count + 1)])

    def _open_project_for_sales(self, project=None, phase=None):
        """Put the project and phase into a selling state."""
        project = project or self.project
        phase = phase if phase is not None else self.phase
        project.commercial_state = 'selling'
        if phase:
            phase.commercial_state = 'selling'

    def _release(self, units, project=None, phase=None, **kwargs):
        """Create, approve and release a batch covering ``units``."""
        vals = {
            'project_id': (project or self.project).id,
            'property_ids': [(6, 0, units.ids)],
        }
        if phase is not None:
            vals['phase_id'] = phase.id
        vals.update(kwargs)
        batch = self.Release.create(vals)
        batch.action_approve()
        batch.action_release()
        return batch
