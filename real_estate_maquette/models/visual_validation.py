# -*- coding: utf-8 -*-
"""M2 / M3 — uploading a file is not a deployment.

### The problem

`maquette_glb` is a `fields.Binary` with no size limit, no format check and no
mapping check. The only validation that has ever happened is a customer opening
the viewer and seeing nothing, or a browser console error nobody was watching.
The audit found no triangle count, no texture budget, no file size and no
unmatched-mesh report anywhere in either module.

### What this does

Parses the GLB **server-side** — the container header and the JSON chunk, which
is all that is needed to count meshes, materials, textures and images and to
read every node name — and cross-checks the result against the project's
inventory.

```
    GLB binary
        │  container: magic "glTF", version, declared length
        │  chunk 0:   JSON  ── meshes, materials, textures, images, nodes
        │  chunk 1:   BIN   ── not parsed; size recorded
        ▼
    ┌─────────────────────────────────────────────┐
    │  GLB meshes          2,846                  │
    │  Properties          2,720                  │
    │  Matched             2,694                  │
    │  Unmatched meshes      131  ← warning       │
    │  Properties w/o mesh     5  ← warning       │
    │  Duplicate mappings      0                  │
    │  Critical errors         0  ← publication   │
    └─────────────────────────────────────────────┘
```

No Python glTF dependency is added. The GLB container format is a 12-byte
header and length-prefixed chunks; parsing that much of it is thirty lines and
avoids putting a third-party parser in the dependency chain of a module that
has to install on a customer's server.

### Severity decides what blocks

Only `critical` stops publication. 131 unmatched meshes is a warning — a
building may legitimately contain lift shafts, landscaping and cars that are
not units. A GLB that is not a GLB is critical. The distinction is what makes
the gate usable; a validator that blocks on everything gets switched off.
"""

import base64
import json
import logging
import struct

from odoo import _, api, fields, models

from .visual_states import VALIDATION_BLOCKING, VALIDATION_SEVERITY

_logger = logging.getLogger(__name__)

#: glTF container constants, from the specification.
GLB_MAGIC = 0x46546C67          # 'glTF' little-endian
GLB_CHUNK_JSON = 0x4E4F534A     # 'JSON'
GLB_CHUNK_BIN = 0x004E4942      # 'BIN\0'
GLB_HEADER_SIZE = 12
GLB_CHUNK_HEADER_SIZE = 8

#: Advisory budgets. Deliberately generous — these produce warnings, and the
#: point is to notice a 400 MB upload, not to police a well-made 40 MB one.
BUDGET_FILE_BYTES = 120 * 1024 * 1024
BUDGET_MESH_COUNT = 20000
BUDGET_MATERIAL_COUNT = 500
BUDGET_TEXTURE_COUNT = 300
BUDGET_TEXTURE_PIXELS = 4096 * 4096

#: Extensions the bundled loader can actually decode. `KHR_draco_mesh_compression`
#: is listed because DRACOLoader is wired up — but see the CDN finding in the
#: audit, which is why a Draco asset raises a warning rather than passing
#: silently.
SUPPORTED_EXTENSIONS = {
    'KHR_materials_unlit', 'KHR_texture_transform', 'KHR_lights_punctual',
    'KHR_materials_emissive_strength', 'KHR_materials_specular',
    'KHR_materials_ior', 'KHR_materials_clearcoat', 'KHR_materials_sheen',
    'KHR_materials_transmission', 'KHR_materials_volume',
    'KHR_draco_mesh_compression', 'KHR_mesh_quantization',
    'KHR_texture_basisu', 'EXT_meshopt_compression',
}

#: Extensions the bundled r160 loader has no decoder for at all.
UNDECODABLE_EXTENSIONS = {
    'KHR_texture_basisu': 'KTX2Loader is not bundled',
    'EXT_meshopt_compression': 'MeshoptDecoder is not bundled',
}


class VisualValidationIssue(models.Model):
    """One finding. Kept as rows rather than a blob so they can be listed,
    filtered, counted and resolved individually."""
    _name = 'realestate.visual.validation.issue'
    _description = 'Visual Asset Validation Issue'
    _order = 'severity, id'

    report_id = fields.Many2one(
        'realestate.visual.validation', required=True, ondelete='cascade',
        index=True)
    project_id = fields.Many2one(
        related='report_id.project_id', store=True, index=True, readonly=True)
    company_id = fields.Many2one(
        related='report_id.company_id', store=True, index=True, readonly=True)

    severity = fields.Selection(
        VALIDATION_SEVERITY, required=True, index=True)
    code = fields.Char(required=True, index=True)
    message = fields.Char(required=True)
    subject = fields.Char(
        help="The mesh name or property code the finding is about.")
    property_id = fields.Many2one(
        'realestate.property', ondelete='cascade', index=True)
    resolved = fields.Boolean(
        index=True,
        help="Set when a re-validation no longer finds the issue. Rows are "
             "kept rather than deleted so 'this was broken last Tuesday' "
             "remains answerable.")


class VisualValidation(models.Model):
    """One validation run against one project's assets."""
    _name = 'realestate.visual.validation'
    _description = 'Visual Asset Validation Report'
    _order = 'create_date desc, id desc'
    _rec_name = 'project_id'

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='project_id.company_id', store=True, index=True, readonly=True)
    validated_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    validated_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)

    # -- what the file is --
    file_bytes = fields.Integer(string='File Size (bytes)', readonly=True)
    gltf_version = fields.Char(readonly=True)
    generator = fields.Char(readonly=True)
    mesh_count = fields.Integer(readonly=True)
    node_count = fields.Integer(readonly=True)
    material_count = fields.Integer(readonly=True)
    texture_count = fields.Integer(readonly=True)
    image_count = fields.Integer(readonly=True)
    extensions_used = fields.Char(readonly=True)

    # -- how it maps --
    property_count = fields.Integer(readonly=True)
    matched_count = fields.Integer(readonly=True)
    unmatched_mesh_count = fields.Integer(readonly=True)
    property_without_mesh_count = fields.Integer(readonly=True)
    duplicate_mapping_count = fields.Integer(readonly=True)

    issue_ids = fields.One2many(
        'realestate.visual.validation.issue', 'report_id')
    critical_count = fields.Integer(compute='_compute_counts', store=True)
    warning_count = fields.Integer(compute='_compute_counts', store=True)
    has_critical = fields.Boolean(compute='_compute_counts', store=True)

    @api.depends('issue_ids.severity', 'issue_ids.resolved')
    def _compute_counts(self):
        for rec in self:
            live = rec.issue_ids.filtered(lambda i: not i.resolved)
            rec.critical_count = len(
                live.filtered(lambda i: i.severity == 'critical'))
            rec.warning_count = len(
                live.filtered(lambda i: i.severity == 'warning'))
            rec.has_critical = bool(rec.critical_count)

    # ==================================================================
    # Running a validation
    # ==================================================================
    @api.model
    def _validate_project(self, project):
        """Validate a project's assets and mapping. Returns the report."""
        report = self.create({'project_id': project.id})
        issues = []

        gltf = None
        if project.maquette_glb:
            raw = base64.b64decode(project.maquette_glb)
            report.file_bytes = len(raw)
            gltf, parse_issues = self._parse_glb(raw)
            issues.extend(parse_issues)
            if gltf is not None:
                issues.extend(report._record_asset_facts(gltf, len(raw)))
        elif project.visual_3d_enabled:
            issues.append(('critical', 'no_glb', _(
                "3D is enabled for this project but no model has been "
                "uploaded."), None, None))

        if project.visual_2d_enabled and not project.master_plan_2d:
            issues.append(('warning', 'no_master_plan', _(
                "2D is enabled but no master plan image has been uploaded."),
                None, None))

        issues.extend(report._validate_mapping(project, gltf))

        Issue = self.env['realestate.visual.validation.issue']
        for severity, code, message, subject, prop in issues:
            Issue.create({
                'report_id': report.id,
                'severity': severity,
                'code': code,
                'message': message,
                'subject': subject,
                'property_id': prop.id if prop else False,
            })

        # Close out anything an earlier run raised that this one did not: the
        # issue list should describe the asset as it is now, while still
        # keeping the history.
        live_codes = {(c, s) for _sev, c, _m, s, _p in issues}
        stale = Issue.search([
            ('project_id', '=', project.id),
            ('report_id', '!=', report.id),
            ('resolved', '=', False),
        ])
        stale.filtered(
            lambda i: (i.code, i.subject) not in live_codes
        ).write({'resolved': True})

        return report

    # ------------------------------------------------------------------
    # GLB container parsing
    # ------------------------------------------------------------------
    @api.model
    def _parse_glb(self, raw):
        """Read the glTF JSON chunk out of a GLB. Returns (dict|None, issues).

        Only the container and the JSON chunk are read. The binary chunk is
        never decoded — its size is recorded and that is all the validator
        needs.
        """
        issues = []
        if len(raw) < GLB_HEADER_SIZE:
            return None, [('critical', 'not_glb', _(
                "The uploaded file is %s bytes — too short to be a GLB."
            ) % len(raw), None, None)]

        magic, version, declared = struct.unpack('<III', raw[:GLB_HEADER_SIZE])
        if magic != GLB_MAGIC:
            return None, [('critical', 'not_glb', _(
                "The uploaded file is not a binary glTF: its header does not "
                "start with 'glTF'. A .gltf JSON file or a .zip renamed to "
                ".glb will fail here."), None, None)]
        if version != 2:
            issues.append(('critical', 'gltf_version', _(
                "GLB container version %s; the bundled loader reads version 2."
            ) % version, None, None))
        if declared != len(raw):
            issues.append(('warning', 'length_mismatch', _(
                "The file declares %(declared)s bytes but is %(actual)s. It "
                "may be truncated.",
                declared=declared, actual=len(raw)), None, None))

        offset = GLB_HEADER_SIZE
        json_chunk = None
        while offset + GLB_CHUNK_HEADER_SIZE <= len(raw):
            length, kind = struct.unpack(
                '<II', raw[offset:offset + GLB_CHUNK_HEADER_SIZE])
            start = offset + GLB_CHUNK_HEADER_SIZE
            end = start + length
            if end > len(raw):
                issues.append(('critical', 'truncated_chunk', _(
                    "A chunk claims %s bytes past the end of the file."
                ) % length, None, None))
                break
            if kind == GLB_CHUNK_JSON and json_chunk is None:
                json_chunk = raw[start:end]
            offset = end + (-end % 4)   # chunks are 4-byte aligned

        if json_chunk is None:
            return None, issues + [('critical', 'no_json_chunk', _(
                "The GLB contains no JSON chunk, so it describes nothing."),
                None, None)]

        try:
            gltf = json.loads(json_chunk.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, issues + [('critical', 'bad_json', _(
                "The GLB's JSON chunk could not be read: %s") % exc,
                None, None)]

        return gltf, issues

    def _record_asset_facts(self, gltf, size_bytes):
        """Store what the asset is, and flag what the loader cannot handle."""
        self.ensure_one()
        asset = gltf.get('asset') or {}
        meshes = gltf.get('meshes') or []
        nodes = gltf.get('nodes') or []
        used = gltf.get('extensionsUsed') or []
        required = gltf.get('extensionsRequired') or []

        self.write({
            'gltf_version': asset.get('version') or '',
            'generator': (asset.get('generator') or '')[:120],
            'mesh_count': len(meshes),
            'node_count': len(nodes),
            'material_count': len(gltf.get('materials') or []),
            'texture_count': len(gltf.get('textures') or []),
            'image_count': len(gltf.get('images') or []),
            'extensions_used': ', '.join(sorted(used))[:250],
        })

        issues = []
        if size_bytes > BUDGET_FILE_BYTES:
            issues.append(('warning', 'file_size', _(
                "The model is %(mb)s MB. Anything past %(budget)s MB will be a "
                "slow first load on a normal connection.",
                mb=round(size_bytes / 1048576, 1),
                budget=BUDGET_FILE_BYTES // 1048576), None, None))
        if len(meshes) > BUDGET_MESH_COUNT:
            issues.append(('warning', 'mesh_count', _(
                "%s meshes. Draw-call count is the usual cause of a stuttering "
                "gallery.") % len(meshes), None, None))
        if len(gltf.get('materials') or []) > BUDGET_MATERIAL_COUNT:
            issues.append(('warning', 'material_count', _(
                "%s materials.") % len(gltf.get('materials')), None, None))
        if len(gltf.get('textures') or []) > BUDGET_TEXTURE_COUNT:
            issues.append(('warning', 'texture_count', _(
                "%s textures.") % len(gltf.get('textures')), None, None))

        for ext in required:
            if ext in UNDECODABLE_EXTENSIONS:
                issues.append(('critical', 'undecodable_extension', _(
                    "The model requires %(ext)s, which this deployment cannot "
                    "decode (%(why)s). It will fail to load in the browser.",
                    ext=ext, why=UNDECODABLE_EXTENSIONS[ext]), ext, None))
            elif ext not in SUPPORTED_EXTENSIONS:
                issues.append(('warning', 'unknown_extension', _(
                    "The model requires the extension %s, which is not in the "
                    "known-supported list. It may render incorrectly.") % ext,
                    ext, None))

        draco = self.env['realestate.visual.assets'].draco_issue(used)
        if draco:
            issues.append(draco)

        return issues

    # ------------------------------------------------------------------
    # Mapping QA (M3)
    # ------------------------------------------------------------------
    def _validate_mapping(self, project, gltf):
        """Cross-check the model's node names against the project's units."""
        self.ensure_one()
        issues = []
        units = project.property_ids.filtered(
            lambda p: p.hierarchy_level == 'unit')

        mesh_names = set()
        if gltf:
            for node in (gltf.get('nodes') or []):
                if node.get('name'):
                    mesh_names.add(node['name'])
            for mesh in (gltf.get('meshes') or []):
                if mesh.get('name'):
                    mesh_names.add(mesh['name'])

        mapped = {}
        duplicates = []
        for unit in units:
            name = unit.maquette_mesh_name
            if not name:
                continue
            if name in mapped:
                duplicates.append((name, mapped[name], unit))
            else:
                mapped[name] = unit

        matched = {n for n in mapped if n in mesh_names} if gltf else set()
        unmatched_meshes = (mesh_names - set(mapped)) if gltf else set()
        missing_mesh = units.filtered(lambda u: not u.maquette_mesh_name)

        self.write({
            'property_count': len(units),
            'matched_count': len(matched),
            'unmatched_mesh_count': len(unmatched_meshes),
            'property_without_mesh_count': len(missing_mesh),
            'duplicate_mapping_count': len(duplicates),
        })

        for name, first, second in duplicates:
            issues.append(('critical', 'duplicate_mapping', _(
                "Mesh '%(mesh)s' is claimed by both %(a)s and %(b)s. A click "
                "would be ambiguous.",
                mesh=name, a=first.display_name, b=second.display_name),
                name, second))

        if gltf:
            for unit in units.filtered('maquette_mesh_name'):
                if unit.maquette_mesh_name not in mesh_names:
                    issues.append(('critical', 'mesh_not_in_model', _(
                        "%(unit)s is mapped to mesh '%(mesh)s', which does not "
                        "exist in the uploaded model. Clicking it does "
                        "nothing.",
                        unit=unit.display_name,
                        mesh=unit.maquette_mesh_name),
                        unit.maquette_mesh_name, unit))

        # Advisory. A model legitimately contains lift shafts, landscaping and
        # parked cars that are not units, so an unmatched mesh is information,
        # not a fault.
        if unmatched_meshes:
            issues.append(('warning', 'unmatched_meshes', _(
                "%s mesh(es) in the model are not mapped to any unit."
            ) % len(unmatched_meshes), None, None))
        if missing_mesh:
            issues.append(('warning', 'units_without_mesh', _(
                "%s unit(s) have no mesh, so they cannot be found in 3D."
            ) % len(missing_mesh), None, None))

        unpriced = units.filtered(lambda u: not u.base_price)
        if unpriced:
            issues.append(('warning', 'units_without_price', _(
                "%s unit(s) have no price."
            ) % len(unpriced), None, None))

        orphans = units.filtered(lambda u: not u.parent_id)
        if orphans:
            issues.append(('warning', 'units_without_parent', _(
                "%s unit(s) have no parent, so building/floor navigation "
                "cannot reach them."
            ) % len(orphans), None, None))

        return issues

    # ------------------------------------------------------------------
    def action_view_issues(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Validation Issues — %s') % self.project_id.display_name,
            'res_model': 'realestate.visual.validation.issue',
            'view_mode': 'list,form',
            'domain': [('report_id', '=', self.id)],
        }
