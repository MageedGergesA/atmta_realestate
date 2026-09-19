# -*- coding: utf-8 -*-
"""Shared fixtures for the Interactive Sales Gallery.

Both visual modules had **zero tests** before this upgrade, which is how a
`base.group_user,1,1,1,1` ACL on production polygon mappings shipped, and how
the viewer came to colour units from a field that knows nothing about whether
they have been released.

Everything here builds the smallest believable gallery: a company, a selling
project with a released unit, a GLB whose meshes are known because this file
wrote them, and the roles the audit found missing.
"""

import base64
import json
import struct

from odoo.tests.common import TransactionCase

#: glTF container constants — the same ones the validator reads.
GLB_MAGIC = 0x46546C67
GLB_CHUNK_JSON = 0x4E4F534A
GLB_CHUNK_BIN = 0x004E4942


def build_glb(mesh_names=(), *, version=2, extensions_required=(),
              extensions_used=(), materials=0, textures=0, images=0,
              padding_bytes=0, generator='ATMTA test fixture'):
    """A real, minimal, parseable GLB with the meshes you asked for.

    Building one rather than committing a binary fixture means a test can say
    exactly which meshes exist, and the validator is exercised against the
    genuine container format rather than a mock.
    """
    gltf = {
        'asset': {'version': '2.0', 'generator': generator},
        'scene': 0,
        'scenes': [{'nodes': list(range(len(mesh_names)))}],
        'nodes': [{'name': n, 'mesh': i} for i, n in enumerate(mesh_names)],
        'meshes': [{'name': n, 'primitives': []} for n in mesh_names],
    }
    if materials:
        gltf['materials'] = [{'name': 'M%d' % i} for i in range(materials)]
    if textures:
        gltf['textures'] = [{'source': 0} for _i in range(textures)]
    if images:
        gltf['images'] = [{'uri': 'i%d.png' % i} for i in range(images)]
    if extensions_used:
        gltf['extensionsUsed'] = list(extensions_used)
    if extensions_required:
        gltf['extensionsRequired'] = list(extensions_required)
        gltf.setdefault('extensionsUsed', []).extend(extensions_required)

    json_bytes = json.dumps(gltf).encode('utf-8')
    json_bytes += b' ' * (-len(json_bytes) % 4)          # 4-byte aligned
    bin_bytes = b'\x00' * (padding_bytes + (-padding_bytes % 4))

    body = struct.pack('<II', len(json_bytes), GLB_CHUNK_JSON) + json_bytes
    if bin_bytes:
        body += struct.pack('<II', len(bin_bytes), GLB_CHUNK_BIN) + bin_bytes

    total = 12 + len(body)
    return struct.pack('<III', GLB_MAGIC, version, total) + body


class VisualCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Project = cls.env['realestate.project']
        cls.Property = cls.env['realestate.property']
        cls.Validation = cls.env['realestate.visual.validation']
        cls.Issue = cls.env['realestate.visual.validation.issue']
        cls.Commercial = cls.env['realestate.visual.commercial']
        # 2D Plan depends on this module; its model exists only when it is
        # installed too.
        cls.PlanRegion = cls.env.get('realestate.plan.region')
        cls.BuildingRegion = cls.env['realestate.building.region']

        # The test author publishes galleries. Without the group every
        # lifecycle test would be a test of the permission check rather than
        # of the behaviour behind it.
        cls.env.user.groups_id |= cls.env.ref(
            'real_estate_maquette.group_visual_manager')

        cls.property_type = cls.env['property.type'].create(
            {'name': 'Gallery Apartment'})

    # ------------------------------------------------------------------
    # Optional neighbours
    #
    # CRM, Brokerage and the Portal are not dependencies of this module, but
    # some tests exercise the integration with them. Those tests skip when the
    # neighbour is genuinely absent (maquette installed alone) and run in full
    # whenever it is installed.
    # ------------------------------------------------------------------
    def _skip_without_models(self, *model_names):
        missing = [name for name in model_names if name not in self.env]
        if missing:
            self.skipTest("Needs %s, which is not installed."
                          % ', '.join(missing))

    def _skip_without_module(self, module_name):
        installed = self.env['ir.module.module'].sudo().search_count([
            ('name', '=', module_name), ('state', '=', 'installed')])
        if not installed:
            self.skipTest("Needs the %s module, which is not installed."
                          % module_name)

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------
    _user_seq = 0

    def _visual_user(self, *groups, **kwargs):
        type(self)._user_seq += 1
        seq = type(self)._user_seq
        group_ids = [self.env.ref('base.group_user').id]
        group_ids += [self.env.ref(g).id for g in groups]
        vals = {
            'name': 'Visual User %d' % seq,
            'login': 'visual.user.%d@test.example' % seq,
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, group_ids)],
        }
        vals.update(kwargs)
        return self.env['res.users'].with_context(
            no_reset_password=True).create(vals)

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------
    _project_seq = 0

    def _project(self, **kwargs):
        type(self)._project_seq += 1
        seq = type(self)._project_seq
        vals = {
            'name': 'Gallery Project %d' % seq,
            'code': 'GAL%02d' % seq,
            'company_id': self.company.id,
            'commercial_state': 'selling',
        }
        vals.update(kwargs)
        project = self.Project.create(vals)
        self._phase_for(project)
        return project

    def _phase_for(self, project):
        if not hasattr(self, '_phases'):
            self._phases = {}
        if project.id not in self._phases:
            self._phases[project.id] = self.env['realestate.phase'].create({
                'name': 'Phase 1', 'project_id': project.id,
                'commercial_state': 'selling',
            })
        return self._phases[project.id]

    _unit_seq = 0

    def _unit(self, project, released=True, price=1000000.0, mesh=None,
              **kwargs):
        type(self)._unit_seq += 1
        seq = type(self)._unit_seq
        phase = self._phase_for(project)
        vals = {
            'name': 'GAL-U-%03d' % seq,
            'property_code': 'GAL-U-%03d' % seq,
            'hierarchy_level': 'unit',
            'usage_category': 'apartment',
            'property_type_id': self.property_type.id,
            'area_sqm': 120.0,
            'bedroom_count': 3,
            'bathroom_count': 2,
            'floor_number': 3,
            'company_id': self.company.id,
            'project_id': project.id,
            'phase_id': phase.id,
            'base_price': price,
        }
        if mesh is not None:
            vals['maquette_mesh_name'] = mesh
        vals.update(kwargs)
        unit = self.Property.create(vals)
        if released:
            self._release(project, unit)
        return unit

    def _release(self, project, units):
        batch = self.env['realestate.unit.release.batch'].create({
            'project_id': project.id,
            'phase_id': self._phase_for(project).id,
            'property_ids': [(6, 0, units.ids)],
        })
        batch.action_approve()
        batch.action_release()
        return batch

    def _building(self, project, **kwargs):
        type(self)._unit_seq += 1
        seq = type(self)._unit_seq
        vals = {
            'name': 'GAL-B-%03d' % seq,
            'property_code': 'GAL-B-%03d' % seq,
            'hierarchy_level': 'building',
            'company_id': self.company.id,
            'project_id': project.id,
        }
        vals.update(kwargs)
        return self.Property.create(vals)

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------
    def _attach_glb(self, project, mesh_names=(), **kwargs):
        project.write({
            'maquette_glb': base64.b64encode(
                build_glb(mesh_names, **kwargs)),
            'maquette_glb_filename': 'model.glb',
        })
        return project

    def _attach_master_plan(self, project):
        # A 1×1 PNG is a valid image and enough for "an image exists".
        project.write({
            'master_plan_2d': base64.b64encode(_ONE_PIXEL_PNG),
            'master_plan_2d_filename': 'plan.png',
        })
        return project

    def _ready_project(self, mesh_names=('U1',), units=1):
        """A project that validates clean and can be published."""
        project = self._project()
        made = self.Property.browse()
        for i in range(units):
            made |= self._unit(project, mesh=mesh_names[i])
        self._attach_glb(project, mesh_names)
        self._attach_master_plan(project)
        project.action_visual_validate()
        return project, made


#: The smallest valid PNG: 1×1, transparent.
_ONE_PIXEL_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    b'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
