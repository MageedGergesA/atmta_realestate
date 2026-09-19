# -*- coding: utf-8 -*-
"""M1 / M2 — a project's visual experience is configured, validated, published.

### What was there before

Nine loose fields on `realestate.project` — `maquette_glb`,
`maquette_env_hdr`, `maquette_default_camera`, `maquette_mesh_naming_hint`,
`master_plan_2d`, and their filename companions — with no relationship between
them and no state. Uploading a file *was* the deployment. A half-mapped model
with 131 unmatched meshes was as live as a finished one, because there was
nothing that could be described as "not live yet".

### What replaces it

The same fields, untouched, plus a lifecycle around them:

```
    DRAFT ──▶ VALIDATING ──▶ READY ──▶ PUBLISHED ──▶ ARCHIVED
      ▲            │           │           │
      └────────────┴───────────┴───────────┘   (back to draft on any edit
                                                that invalidates the model)
```

Nothing is deleted or renamed. `maquette_glb` is still `maquette_glb`, and a
project that has never heard of this lifecycle keeps working — it simply reads
`draft`, and `draft` is exactly what an unvalidated upload is.

### Readiness is a number, because "is it ready?" is asked constantly

A percentage nobody has to interpret, computed from the things that actually
stop a gallery working: an asset present, its validation clean, its meshes
mapped, its units priced. It is advisory. **Publication is gated on critical
validation errors, not on the score** — a project can be legitimately at 80%
because a fifth of its inventory is deliberately unreleased.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .visual_states import (
    CAPABILITY_TIER,
    VISUAL_DEFAULT_EXPERIENCE,
    VISUAL_PUBLICATION_LIVE,
    VISUAL_PUBLICATION_STATE,
)


class ProjectVisualConfig(models.Model):
    _inherit = 'realestate.project'

    # ------------------------------------------------------------------
    # What the experience offers
    # ------------------------------------------------------------------
    visual_2d_enabled = fields.Boolean(
        string='2D Master Plan', default=True,
        help="Whether the 2D master plan is offered. Kept separate from "
             "whether an image exists: a project may have a plan uploaded and "
             "deliberately not show it yet.")
    visual_3d_enabled = fields.Boolean(
        string='3D Maquette', default=True)
    visual_public_enabled = fields.Boolean(
        string='Public Embed Allowed', default=False,
        help="Off by default. A project becomes publicly embeddable because "
             "somebody decided it should, not because it happens to have a "
             "file attached.")
    visual_default_experience = fields.Selection(
        VISUAL_DEFAULT_EXPERIENCE, string='Opens With', default='auto',
        required=True,
        help="'Best Available' picks 3D, then 2D, then the unit list — "
             "according to what is published and what the device can render.")
    visual_min_capability = fields.Selection(
        CAPABILITY_TIER, string='Minimum Tier for 3D', default='low',
        required=True,
        help="Devices below this tier are sent straight to 2D rather than "
             "shown a 3D experience that will stutter.")

    # ------------------------------------------------------------------
    # Publication lifecycle
    # ------------------------------------------------------------------
    visual_publication_state = fields.Selection(
        VISUAL_PUBLICATION_STATE, string='Gallery Status', default='draft',
        required=True, tracking=True, index=True, copy=False)
    visual_version = fields.Integer(
        string='Gallery Version', default=1, readonly=True, copy=False,
        help="Incremented on every publish. Used for cache-busting instead of "
             "`write_date`, which invalidated every browser's copy of a "
             "multi-megabyte model whenever anybody edited the project's "
             "phone number.")
    visual_published_on = fields.Datetime(readonly=True, copy=False)
    visual_published_by_id = fields.Many2one(
        'res.users', string='Published By', readonly=True, copy=False)
    visual_last_validated_on = fields.Datetime(readonly=True, copy=False)
    visual_is_live = fields.Boolean(
        compute='_compute_visual_is_live', store=True, index=True,
        help="One field every consumer checks, so 2D, 3D, the embed and the "
             "API cannot disagree about whether a project is live.")

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------
    visual_readiness_score = fields.Integer(
        string='Readiness (%)', compute='_compute_visual_readiness',
        store=True)
    visual_readiness_detail = fields.Text(
        compute='_compute_visual_readiness', store=True)
    visual_unit_count = fields.Integer(
        compute='_compute_visual_readiness', store=True)
    visual_mapped_count = fields.Integer(
        compute='_compute_visual_readiness', store=True)
    visual_unmapped_count = fields.Integer(
        compute='_compute_visual_readiness', store=True)
    visual_priced_count = fields.Integer(
        compute='_compute_visual_readiness', store=True)
    visual_floorplan_count = fields.Integer(
        compute='_compute_visual_readiness', store=True)

    visual_fallback_property_id = fields.Many2one(
        'realestate.property', string='Fallback Master Plan',
        domain="[('project_id', '=', id)]",
        help="The property whose 2D plan stands in when 3D cannot run "
             "(Rule 4). Usually the compound or the master-plan property.")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('visual_publication_state')
    def _compute_visual_is_live(self):
        for rec in self:
            rec.visual_is_live = (
                rec.visual_publication_state in VISUAL_PUBLICATION_LIVE)

    @api.depends('property_ids.maquette_mesh_name',
                 'property_ids.hierarchy_level',
                 'property_ids.floor_plan_image',
                 'property_ids.base_price',
                 'maquette_glb', 'master_plan_2d',
                 'visual_2d_enabled', 'visual_3d_enabled')
    def _compute_visual_readiness(self):
        """A score built from what actually stops a gallery working.

        Weighted by consequence, not by tidiness: an unmapped mesh means a
        customer clicks a building and nothing happens, which is worse than a
        missing floor plan.
        """
        for rec in self:
            units = rec.property_ids.filtered(
                lambda p: p.hierarchy_level == 'unit')
            total = len(units)
            mapped = len(units.filtered('maquette_mesh_name'))
            priced = len(units.filtered(lambda u: u.base_price > 0))
            plans = len(units.filtered('floor_plan_image'))

            rec.visual_unit_count = total
            rec.visual_mapped_count = mapped
            rec.visual_unmapped_count = total - mapped
            rec.visual_priced_count = priced
            rec.visual_floorplan_count = plans

            checks, detail = [], []
            if rec.visual_3d_enabled:
                checks.append((30, bool(rec.maquette_glb)))
                detail.append(_("3D model: %s") % (
                    _('uploaded') if rec.maquette_glb else _('MISSING')))
                checks.append((30, (mapped / total) if total else 0.0))
                detail.append(_("Meshes mapped: %(m)s of %(t)s",
                                m=mapped, t=total))
            if rec.visual_2d_enabled:
                checks.append((15, bool(rec.master_plan_2d)))
                detail.append(_("2D master plan: %s") % (
                    _('uploaded') if rec.master_plan_2d else _('MISSING')))
            checks.append((15, (priced / total) if total else 0.0))
            detail.append(_("Units priced: %(p)s of %(t)s", p=priced, t=total))
            checks.append((10, (plans / total) if total else 0.0))
            detail.append(_("Floor plans: %(f)s of %(t)s", f=plans, t=total))

            available = sum(weight for weight, _v in checks)
            earned = sum(weight * float(value) for weight, value in checks)
            rec.visual_readiness_score = (
                int(round(earned / available * 100)) if available else 0)
            rec.visual_readiness_detail = '\n'.join(detail)

    # ------------------------------------------------------------------
    # Lifecycle actions
    # ------------------------------------------------------------------
    def action_visual_validate(self):
        """Run the asset pipeline and move to READY or back to DRAFT."""
        self._check_visual_publisher()
        Validation = self.env['realestate.visual.validation']
        for rec in self:
            rec.visual_publication_state = 'validating'
            report = Validation._validate_project(rec)
            rec.visual_last_validated_on = fields.Datetime.now()
            rec.visual_publication_state = (
                'draft' if report.has_critical else 'ready')
        return True

    def action_visual_publish(self):
        """Make the experience live. Refused while anything critical stands."""
        self._check_visual_publisher()
        for rec in self:
            if rec.visual_publication_state not in ('ready', 'archived'):
                raise UserError(_(
                    "%(project)s is %(state)s. Validate it first — publishing "
                    "an unvalidated model is how a half-mapped tower ends up "
                    "in front of a customer.",
                    project=rec.display_name,
                    state=dict(VISUAL_PUBLICATION_STATE).get(
                        rec.visual_publication_state)))
            blocking = rec._visual_blocking_issues()
            if blocking:
                raise UserError(_(
                    "%(project)s has %(count)s critical validation "
                    "issue(s):\n\n%(issues)s",
                    project=rec.display_name, count=len(blocking),
                    issues='\n'.join('• %s' % i.message for i in blocking[:10])))
            rec.write({
                'visual_publication_state': 'published',
                'visual_published_on': fields.Datetime.now(),
                'visual_published_by_id': self.env.user.id,
                'visual_version': rec.visual_version + 1,
            })
        return True

    def action_visual_unpublish(self):
        self._check_visual_publisher()
        # Same states the button is offered in. Without this, "unpublishing" a
        # draft or archived gallery marked it READY -- validated -- when it was
        # never validated (or its file changed while archived).
        self._check_visual_state(('published',), _("unpublished"))
        self.write({'visual_publication_state': 'ready'})
        return True

    def action_visual_archive(self):
        self._check_visual_publisher()
        # Archive retires a validated gallery. A draft archived and then
        # published straight from `archived` would skip validation.
        self._check_visual_state(('ready', 'published'), _("archived"))
        self.write({'visual_publication_state': 'archived'})
        return True

    def _check_visual_state(self, allowed, verb):
        states = dict(VISUAL_PUBLICATION_STATE)
        for rec in self:
            if rec.visual_publication_state not in allowed:
                raise UserError(_(
                    "%(project)s is %(state)s and cannot be %(verb)s.",
                    project=rec.display_name,
                    state=states.get(rec.visual_publication_state),
                    verb=verb))

    def action_visual_reset_to_draft(self):
        self._check_visual_publisher()
        self.write({'visual_publication_state': 'draft'})
        return True

    # ------------------------------------------------------------------
    # Rule 4 — what to fall back to
    # ------------------------------------------------------------------
    def _visual_fallback_descriptor(self):
        """The chain the viewer walks when 3D cannot run.

        Sent *with* the unit payload rather than fetched after a failure. By
        the time WebGL has thrown, the client may not be in a position to make
        another round trip cleanly, and a fallback that needs a working page to
        arrive is not a fallback.

        0.4 had none of this: a WebGL failure, a malformed GLB and a missing
        GLB all ended at a red box containing a Three.js exception message.
        """
        self.ensure_one()
        fallback = self.visual_fallback_property_id
        return {
            'has_2d': bool(self.master_plan_2d) and self.visual_2d_enabled,
            'master_plan_url': (
                '/web/image/realestate.project/%s/master_plan_2d' % self.id
                if self.master_plan_2d else False),
            'fallback_property_id': fallback.id if fallback else False,
            'fallback_property_name': (
                fallback.display_name if fallback else ''),
            # Always available, even with no imagery at all: the unit list is
            # a complete navigation path on its own (M25).
            'list_available': True,
            'min_capability': self.visual_min_capability,
            'default_experience': self.visual_default_experience,
        }

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def _check_visual_publisher(self):
        if not self.env.user.has_group(
                'real_estate_maquette.group_visual_publisher'):
            raise UserError(_(
                "Publishing a sales gallery is a Visual Publisher's decision. "
                "Authoring a mapping and putting it in front of a customer are "
                "deliberately different rights."))
        return True

    def _visual_blocking_issues(self):
        self.ensure_one()
        return self.env['realestate.visual.validation.issue'].search([
            ('project_id', '=', self.id),
            ('severity', '=', 'critical'),
            ('resolved', '=', False),
        ])

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------
    def write(self, vals):
        """Replacing the model un-publishes the gallery.

        A published project whose GLB is swapped is publishing a mapping that
        was validated against a different file. Rather than trust that the new
        one happens to match, it goes back to draft and has to be validated
        again — which takes seconds and prevents the failure mode where 131
        meshes silently stop resolving.

        An archived gallery is included: publishing is allowed straight from
        `archived` (a re-launch of what was validated), so a file swapped
        while archived would otherwise go live without ever being validated.
        """
        invalidating = {'maquette_glb', 'master_plan_2d', 'maquette_env_hdr'}
        result = super().write(vals)
        if invalidating.intersection(vals) and not self.env.context.get(
                're_visual_publishing'):
            live = self.filtered(
                lambda p: p.visual_publication_state in (
                    'ready', 'published', 'archived'))
            if live:
                super(ProjectVisualConfig, live).write({
                    'visual_publication_state': 'draft'})
                for rec in live:
                    rec.message_post(body=_(
                        "Gallery returned to draft: the visual asset changed, "
                        "so the existing mapping has not been validated "
                        "against it."))
        return result
