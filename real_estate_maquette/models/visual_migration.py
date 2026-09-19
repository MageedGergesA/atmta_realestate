# -*- coding: utf-8 -*-
"""M31 — bringing existing galleries across without making anybody remap.

### The decision this had to get right

Before this release there was no publication state. Every project with a GLB
attached was, in effect, live. Introducing a lifecycle whose default is `draft`
would therefore switch off every existing customer's gallery on upgrade — safe,
but destructive in the way that gets an upgrade rolled back on a Monday morning.

So the migration classifies instead:

```
    every project with a visual asset
        │
        ├── validates clean            → legacy_valid → PUBLISHED
        │                                (it was working; it keeps working)
        ├── validates with warnings    → legacy_valid → PUBLISHED
        │                                (warnings never blocked anything before)
        ├── has critical issues        → needs_review → DRAFT + report
        │                                (it was already broken; now somebody knows)
        └── has no asset at all        → no_assets   → DRAFT
```

A project that was working keeps working. A project that was quietly broken —
a mesh mapped to a name the model does not contain, so a customer clicked and
nothing happened — stops being live, and the reason is written down.

**Nothing is remapped, renamed or deleted.** Every `maquette_mesh_name`,
polygon, camera default, floor row and uploaded file is left exactly as it is.
The migration only reads them and records a verdict.

### Ambiguity is reported, never resolved

A duplicate mapping that arrived through a SQL import is a decision about
somebody's inventory, not a data-cleanliness problem. It is classified and left
alone, in the same way Module 4's commission migration refused to guess an
agent's pay.
"""

import base64
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

MIGRATION_OUTCOME = [
    ('legacy_valid', 'Legacy — Valid, Kept Live'),
    ('needs_review', 'Needs Review — Taken Offline'),
    ('no_assets', 'No Visual Assets'),
    ('already_migrated', 'Already Migrated'),
]


class VisualMigrationLog(models.Model):
    """What the migration decided about one project, and why."""
    _name = 'realestate.visual.migration'
    _description = 'Visual Gallery Migration Record'
    _order = 'migrated_on desc, id desc'
    _rec_name = 'project_id'

    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='project_id.company_id', store=True, index=True, readonly=True)
    outcome = fields.Selection(
        MIGRATION_OUTCOME, required=True, readonly=True, index=True)
    note = fields.Char(readonly=True)

    had_glb = fields.Boolean(readonly=True)
    had_master_plan = fields.Boolean(readonly=True)
    mesh_mappings = fields.Integer(readonly=True)
    plan_regions = fields.Integer(readonly=True)
    building_regions = fields.Integer(readonly=True)
    floor_rows = fields.Integer(readonly=True)
    had_camera = fields.Boolean(readonly=True)

    resulting_state = fields.Char(readonly=True)
    validation_id = fields.Many2one(
        'realestate.visual.validation', readonly=True, ondelete='set null')
    critical_count = fields.Integer(readonly=True)
    warning_count = fields.Integer(readonly=True)

    migrated_on = fields.Datetime(
        default=fields.Datetime.now, readonly=True, index=True)
    migrated_by_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)


class VisualMigrationRunner(models.AbstractModel):
    """Classify first. Publish only what already worked."""
    _name = 'realestate.visual.migration.runner'
    _description = 'Visual Gallery Migration Runner'

    @api.model
    def audit(self, projects=None):
        """Report what would happen, changing nothing."""
        projects = projects if projects is not None else self._candidates()
        return [self._audit_one(p) for p in projects]

    @api.model
    def _candidates(self):
        """Projects that have never been through this migration."""
        migrated = self.env['realestate.visual.migration'].sudo().search([])
        return self.env['realestate.project'].sudo().search([
            ('id', 'not in', migrated.mapped('project_id').ids),
        ])

    @api.model
    def _inventory(self, project):
        """Everything the migration must preserve, counted before it runs."""
        project = project.sudo()
        units = project.property_ids.filtered(
            lambda p: p.hierarchy_level == 'unit')
        return {
            'had_glb': bool(project.maquette_glb),
            'had_master_plan': bool(project.master_plan_2d),
            'had_camera': bool(project.maquette_default_camera),
            'mesh_mappings': len(units.filtered('maquette_mesh_name')),
            'building_regions': self.env['realestate.building.region'].sudo(
                ).search_count([('project_id', '=', project.id)]),
            # 2D Plan depends on this module, so its regions exist only when
            # it is installed too.
            'plan_regions': self.env['realestate.plan.region'].sudo(
                ).search_count([
                    ('parent_property_id', 'in', project.property_ids.ids)])
            if 'realestate.plan.region' in self.env else 0,
            'floor_rows': self.env['realestate.building.floor'].sudo(
                ).search_count([('project_id', '=', project.id)]),
        }

    @api.model
    def _audit_one(self, project):
        inventory = self._inventory(project)
        row = dict(inventory, project_id=project.id,
                   project=project.display_name)

        # Already handled: left completely alone.
        #
        # `run(projects)` accepts an explicit recordset, so it can reach a
        # project `_candidates()` would have filtered out. Without this, a
        # second run reads a *published* project as "not a legacy_valid
        # outcome" and takes it offline — the migration undoing its own work,
        # which is exactly what the retry/idempotency gate exists to catch.
        if (project.visual_publication_state != 'draft'
                or self.env['realestate.visual.migration'].sudo().search_count(
                    [('project_id', '=', project.id)])):
            row.update(outcome='already_migrated', already_handled=True,
                       note=_("Already migrated; left untouched."))
            return row

        if not (inventory['had_glb'] or inventory['had_master_plan']):
            row.update(outcome='no_assets', note=_(
                "No 3D model and no master plan — nothing to bring across."))
            return row

        return row

    @api.model
    def run(self, projects=None):
        """Validate each project and settle its publication state.

        Read-then-decide: the validation report is produced first, and the
        publication state follows from it. A project is never published because
        the migration assumed it was fine.
        """
        rows = self.audit(projects)
        Log = self.env['realestate.visual.migration'].sudo()
        Project = self.env['realestate.project'].sudo()
        applied = []

        for row in rows:
            if row.get('already_handled'):
                continue
            project = Project.browse(row['project_id'])
            outcome = row.get('outcome')
            report = None
            critical = warning = 0

            if outcome is None:
                report = self.env[
                    'realestate.visual.validation']._validate_project(project)
                critical = report.critical_count
                warning = report.warning_count
                if report.has_critical:
                    outcome = 'needs_review'
                    note = _(
                        "%(count)s critical issue(s) found. The gallery was "
                        "already broken in this way; it is now offline and "
                        "reported rather than silently failing in front of a "
                        "customer.", count=critical)
                else:
                    outcome = 'legacy_valid'
                    note = _(
                        "Validated clean%(warn)s. It was live before this "
                        "release and stays live.",
                        warn=(_(" with %s warning(s)") % warning
                              if warning else ''))
            else:
                note = row.get('note') or ''

            # A project that worked keeps working; anything else stays in
            # draft until a human has looked at it.
            new_state = ('published' if outcome == 'legacy_valid' else 'draft')
            project.with_context(re_visual_publishing=True).write({
                'visual_publication_state': new_state,
                'visual_last_validated_on': fields.Datetime.now(),
            })
            if new_state == 'published':
                project.with_context(re_visual_publishing=True).write({
                    'visual_published_on': fields.Datetime.now(),
                    'visual_published_by_id': self.env.user.id,
                })

            Log.create({
                'project_id': project.id,
                'outcome': outcome,
                'note': note,
                'had_glb': row['had_glb'],
                'had_master_plan': row['had_master_plan'],
                'had_camera': row['had_camera'],
                'mesh_mappings': row['mesh_mappings'],
                'plan_regions': row['plan_regions'],
                'building_regions': row['building_regions'],
                'floor_rows': row['floor_rows'],
                'resulting_state': new_state,
                'validation_id': report.id if report else False,
                'critical_count': critical,
                'warning_count': warning,
            })
            applied.append((project, outcome))

        return applied

    # ==================================================================
    # M7 — completing the migration, one mapping at a time
    # ==================================================================
    #: What a single legacy mapping turned out to be.
    #:
    #: The project-level outcomes above answer "may this gallery be live". These
    #: answer a different and finer question — "will *this* click work" — and a
    #: project can be perfectly publishable while a handful of its mappings
    #: point at nothing. Both answers are needed to call a migration complete.
    MAPPING_CLASSIFICATION = (
        'valid',             # matched, and validated since the upgrade
        'legacy_valid',      # matched, carried over from before the upgrade
        'needs_validation',  # matched, but nothing has checked it yet
        'unmatched',         # points at a mesh/resource that is not there
        'duplicate',         # two records claim the same target
        'missing_resource',  # the asset it depends on does not exist at all
    )

    @api.model
    def classify_mappings(self, projects=None):
        """Classify every legacy mesh mapping and plan region.

        Returns one row per mapping, never a summary: a count of "12 unmatched"
        tells somebody there is work to do, and a list of which twelve tells
        them how to do it.

        Read-only by construction. Nothing here writes, publishes or repairs —
        deciding that a mapping pointing at a deleted mesh should be cleared is
        a commercial decision about somebody's model, and this reports it so a
        human can make it.
        """
        Project = self.env['realestate.project'].sudo()
        if projects is None:
            projects = Project.search([])
        else:
            projects = projects.sudo()

        Validation = self.env['realestate.visual.validation']
        rows = []
        for project in projects:
            gltf = None
            if project.maquette_glb:
                try:
                    # `_parse_glb` answers (gltf, issues); the issues are the
                    # validator's business, the node names are ours.
                    gltf, _issues = Validation._parse_glb(
                        base64.b64decode(project.maquette_glb))
                except Exception:
                    # An unparseable model is not "no model": every mapping on
                    # it is unusable for a different reason, and saying so is
                    # the point of the run.
                    _logger.warning(
                        "Could not parse the model of %s while classifying "
                        "mappings", project.display_name, exc_info=True)
                    gltf = None
            rows.extend(self._classify_mesh_mappings(project, gltf))
            rows.extend(self._classify_plan_regions(project))
        return rows

    @api.model
    def _mapping_row(self, project, kind, record, target, classification,
                     reason):
        return {
            'project_id': project.id,
            'project_name': project.display_name,
            'kind': kind,
            'record_model': record._name if record else False,
            'record_id': record.id if record else False,
            'record_name': record.display_name if record else '',
            'target': target or '',
            'classification': classification,
            'reason': reason,
        }

    @api.model
    def _validated_since_upgrade(self, project):
        """Has anything checked this project since the gallery was upgraded?

        `visual_last_validated_on` is only ever written by the new validation
        service, so its absence means exactly one thing: no version of this
        code has ever looked at these mappings.
        """
        return bool(project.visual_last_validated_on)

    @api.model
    def _classify_mesh_mappings(self, project, gltf):
        units = project.property_ids.filtered(
            lambda p: p.hierarchy_level == 'unit' and p.maquette_mesh_name)
        if not units:
            return []

        if not gltf:
            reason = (_("The project has no 3D model, so no mesh mapping on "
                        "it can resolve.")
                      if not project.maquette_glb
                      else _("The project's 3D model could not be read, so no "
                             "mesh mapping on it can resolve."))
            return [self._mapping_row(
                project, 'mesh', unit, unit.maquette_mesh_name,
                'missing_resource', reason) for unit in units]

        mesh_names = set()
        for node in (gltf.get('nodes') or []):
            if node.get('name'):
                mesh_names.add(node['name'])
        for mesh in (gltf.get('meshes') or []):
            if mesh.get('name'):
                mesh_names.add(mesh['name'])

        claimants = {}
        for unit in units:
            claimants.setdefault(unit.maquette_mesh_name, []).append(unit)

        validated = self._validated_since_upgrade(project)
        rows = []
        for unit in units:
            target = unit.maquette_mesh_name
            others = [u for u in claimants[target] if u.id != unit.id]
            if others:
                rows.append(self._mapping_row(
                    project, 'mesh', unit, target, 'duplicate',
                    _("Mesh '%(mesh)s' is also claimed by %(other)s. A click "
                      "would be ambiguous.",
                      mesh=target,
                      other=', '.join(u.display_name for u in others))))
            elif target not in mesh_names:
                rows.append(self._mapping_row(
                    project, 'mesh', unit, target, 'unmatched',
                    _("No mesh named '%s' exists in the uploaded model.")
                    % target))
            elif not validated:
                rows.append(self._mapping_row(
                    project, 'mesh', unit, target, 'needs_validation',
                    _("The mesh exists, but this project has not been "
                      "validated since the gallery was upgraded.")))
            elif project.visual_publication_state == 'published':
                rows.append(self._mapping_row(
                    project, 'mesh', unit, target, 'legacy_valid',
                    _("Matched, and live before this release.")))
            else:
                rows.append(self._mapping_row(
                    project, 'mesh', unit, target, 'valid',
                    _("Matched against the current model.")))
        return rows

    @api.model
    def _classify_plan_regions(self, project):
        regions = self.env['realestate.building.region'].sudo().search(
            [('project_id', '=', project.id)])
        if not regions:
            return []

        validated = self._validated_since_upgrade(project)
        # `property_id` is required and NOT NULL with `ondelete='cascade'`, so
        # a region pointing at nothing — or at a deleted unit — is a state this
        # schema cannot hold. It is not classified because it cannot occur.
        claimed = {}
        for region in regions:
            claimed.setdefault(region.property_id.id, []).append(region)

        rows = []
        for region in regions:
            target = (region.property_id.display_name
                      if region.property_id else '')
            if not project.master_plan_2d:
                rows.append(self._mapping_row(
                    project, 'region', region, target, 'missing_resource',
                    _("The project has no 2D master plan, so this region has "
                      "nothing to sit on.")))
            elif len(claimed.get(region.property_id.id, [])) > 1:
                rows.append(self._mapping_row(
                    project, 'region', region, target, 'duplicate',
                    _("%(count)s regions point at %(unit)s. A click would be "
                      "ambiguous.",
                      count=len(claimed[region.property_id.id]),
                      unit=target)))
            elif not region.polygon.strip():
                # Empty rather than absent: `polygon` is NOT NULL, so the only
                # broken shape the database permits is a blank one — which an
                # import produces and which draws nothing.
                rows.append(self._mapping_row(
                    project, 'region', region, target, 'unmatched',
                    _("The region has no polygon, so it has no clickable "
                      "area.")))
            elif not validated:
                rows.append(self._mapping_row(
                    project, 'region', region, target, 'needs_validation',
                    _("The region resolves, but this project has not been "
                      "validated since the gallery was upgraded.")))
            elif project.visual_publication_state == 'published':
                rows.append(self._mapping_row(
                    project, 'region', region, target, 'legacy_valid',
                    _("Resolves, and live before this release.")))
            else:
                rows.append(self._mapping_row(
                    project, 'region', region, target, 'valid',
                    _("Resolves against the current plan.")))
        return rows

    @api.model
    def classification_summary(self, projects=None):
        """The same run, counted — for a report line rather than a work list."""
        summary = {name: 0 for name in self.MAPPING_CLASSIFICATION}
        for row in self.classify_mappings(projects):
            summary[row['classification']] += 1
        return summary
