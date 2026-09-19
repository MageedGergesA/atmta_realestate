# -*- coding: utf-8 -*-
"""M5 — one asset package per repeated layout, not one per unit.

### The audit, first, as the brief requires

The suite already has `property.type`. It is **not** a unit type in the sense
this feature needs:

```python
class PropertyType(models.Model):
    _name = 'property.type'
    name = fields.Char(...)
    description = fields.Text(...)
```

Two fields, no project, no geometry. It is a *category* — "Apartment",
"Villa", "Duplex" — and it is consumed as one: Brokerage's matching filters on
`re_property_type_ids`, the public API publishes `property_type`, contract
lines relate to it. One `property.type` covers thousands of units across every
project in the database.

Hanging a floor plan and an interior model off "Apartment" would therefore be
wrong in the most basic way: layout B2 in Palm Heights and layout B2 in a
different tower are different buildings that happen to share a category.

`realestate.contract.template` and `realestate.handover.checklist.template`
were also checked. Both are document/process templates with no visual content.

So there is no existing model for "this layout, with this floor plan and this
interior". This is the minimum one, and it is **visual only**.

### What it deliberately does not own

```
    realestate.visual.unit.type          realestate.property
    ─────────────────────────────        ────────────────────────
    floor plan image / PDF               price
    interior GLB                         availability
    marketing renders                    reservation
    spec tags                            buyer / contract
    default gallery                      commercial state
                                         EVERYTHING commercial
```

It carries no price, no availability, no reservation, no buyer, no contract and
no Developer commercial state. It is presentation content, and the physical
property remains the only authority for everything else — Rule 1, unchanged.

It **references** `property.type` rather than replacing it, so the existing
taxonomy keeps working and nothing has two answers for "what kind of thing is
this".

### Resolution, and why the binary is not copied

```
    unit.floor_plan_image          set → use it            (per-unit override)
                                 unset → type's asset      (shared, stored once)
```

A 400-unit tower with four layouts stores four interior models, not four
hundred. Overriding a single unit stores one more, on that unit — never a copy
of the shared one.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class VisualUnitType(models.Model):
    """A reusable visual asset package for a repeated layout."""
    _name = 'realestate.visual.unit.type'
    _description = 'Visual Unit Type'
    _inherit = ['mail.thread']
    _order = 'project_id, code, name'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        string='Type Code', tracking=True,
        help="The developer's own name for the layout — B2, 3BR-Corner. This "
             "is what a salesperson says out loud.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', string='Project', index=True,
        ondelete='cascade',
        help="Layouts are usually project-specific. Leaving it empty makes the "
             "package available across the company, which is right for a "
             "developer that repeats a layout between towers.")

    property_type_id = fields.Many2one(
        'property.type', string='Category',
        help="The existing category taxonomy — Apartment, Villa. Referenced, "
             "not replaced: this model is a layout's asset package, and "
             "`property.type` remains what the API, Brokerage matching and "
             "contract lines mean by 'type'.")

    # ------------------------------------------------------------------
    # The assets — presentation content, nothing else
    # ------------------------------------------------------------------
    floor_plan_image = fields.Binary(
        string='Floor Plan (Image)', attachment=True)
    floor_plan_image_filename = fields.Char()
    floor_plan_pdf = fields.Binary(
        string='Floor Plan (PDF)', attachment=True)
    floor_plan_pdf_filename = fields.Char()
    interior_glb = fields.Binary(
        string='3D Interior (.glb)', attachment=True)
    interior_glb_filename = fields.Char()
    render_ids = fields.Many2many(
        'ir.attachment', 're_visual_unit_type_render_rel',
        'type_id', 'attachment_id', string='Marketing Renders')
    spec_tag_ids = fields.Many2many(
        'realestate.spec.tag', 're_visual_unit_type_spec_rel',
        'type_id', 'tag_id', string='Specifications')
    description = fields.Html(string='Marketing Description')

    # ------------------------------------------------------------------
    # Provenance — a shared asset changing is not a commercial change
    # ------------------------------------------------------------------
    version = fields.Integer(
        default=1, readonly=True, copy=False,
        help="Bumped whenever a shared asset is replaced. Every unit using "
             "this package shows the new one, so the version is how somebody "
             "answers 'which floor plan did the customer actually see?'.")
    assets_updated_on = fields.Datetime(readonly=True, copy=False)
    assets_updated_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False)

    property_ids = fields.One2many(
        'realestate.property', 'visual_unit_type_id', string='Units')
    property_count = fields.Integer(compute='_compute_property_count')
    override_count = fields.Integer(
        compute='_compute_property_count',
        help="Units that carry their own asset instead of this package's.")

    _sql_constraints = [
        ('code_unique_per_project',
         'unique(code, project_id, company_id)',
         'A layout code must be unique within its project.'),
    ]

    def _compute_property_count(self):
        Property = self.env['realestate.property']
        totals = dict(Property._read_group(
            [('visual_unit_type_id', 'in', self.ids)],
            groupby=['visual_unit_type_id'], aggregates=['__count']))
        for rec in self:
            rec.property_count = totals.get(rec, 0)
            rec.override_count = len(rec.property_ids.filtered(
                lambda p: p.floor_plan_image or p.interior_glb))

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if (rec.project_id and rec.project_id.company_id
                    and rec.project_id.company_id != rec.company_id):
                raise ValidationError(_(
                    "Layout %(name)s is in %(own)s but its project belongs to "
                    "%(other)s.", name=rec.name,
                    own=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    # ------------------------------------------------------------------
    # Asset replacement
    # ------------------------------------------------------------------
    #: Replacing one of these changes what every unit in the package shows.
    SHARED_ASSETS = ('floor_plan_image', 'floor_plan_pdf', 'interior_glb')

    def write(self, vals):
        """Bump the version when a shared asset is replaced.

        A shared asset is presentation content, so replacing it is allowed and
        needs no approval — but it silently changes what hundreds of units
        display, and "which floor plan did the customer see in March?" has to
        remain answerable. Nothing commercial is touched: the units' prices,
        availability and contracts are not in this model at all.
        """
        result = super().write(vals)
        if set(self.SHARED_ASSETS).intersection(vals):
            for rec in self:
                super(VisualUnitType, rec).write({
                    'version': rec.version + 1,
                    'assets_updated_on': fields.Datetime.now(),
                    'assets_updated_by_id': self.env.user.id,
                })
                rec.message_post(body=_(
                    "Shared visual assets replaced — version %(version)s. "
                    "%(count)s unit(s) now display the new content; no "
                    "commercial data was changed.",
                    version=rec.version, count=rec.property_count))
        return result

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Units — %s') % self.display_name,
            'res_model': 'realestate.property',
            'view_mode': 'list,form',
            'domain': [('visual_unit_type_id', '=', self.id)],
        }


class PropertyVisualUnitType(models.Model):
    """Resolution: the unit's own asset wins, else the package's."""
    _inherit = 'realestate.property'

    visual_unit_type_id = fields.Many2one(
        'realestate.visual.unit.type', string='Layout', index=True,
        ondelete='set null',
        help="The shared visual package for this layout. Anything set on the "
             "unit itself overrides it.")

    #: `_effective` fields resolve override → package. They are computed and
    #: **not stored**: storing them would copy a large binary onto every unit,
    #: which is the duplication this feature exists to remove.
    floor_plan_image_effective = fields.Binary(
        compute='_compute_visual_effective_assets')
    floor_plan_pdf_effective = fields.Binary(
        compute='_compute_visual_effective_assets')
    interior_glb_effective = fields.Binary(
        compute='_compute_visual_effective_assets')
    visual_assets_from_type = fields.Boolean(
        compute='_compute_visual_effective_assets',
        help="True when this unit is showing its layout's shared assets "
             "rather than its own.")

    @api.depends('floor_plan_image', 'floor_plan_pdf', 'interior_glb',
                 'visual_unit_type_id',
                 'visual_unit_type_id.floor_plan_image',
                 'visual_unit_type_id.floor_plan_pdf',
                 'visual_unit_type_id.interior_glb')
    def _compute_visual_effective_assets(self):
        for rec in self:
            layout = rec.visual_unit_type_id
            rec.floor_plan_image_effective = (
                rec.floor_plan_image or (layout.floor_plan_image
                                         if layout else False))
            rec.floor_plan_pdf_effective = (
                rec.floor_plan_pdf or (layout.floor_plan_pdf
                                       if layout else False))
            rec.interior_glb_effective = (
                rec.interior_glb or (layout.interior_glb if layout else False))
            rec.visual_assets_from_type = bool(
                layout and not (rec.floor_plan_image or rec.interior_glb))

    @api.depends('floor_plan_image', 'visual_unit_type_id',
                 'visual_unit_type_id.floor_plan_image')
    def _compute_has_floor_plan_effective(self):
        """A unit inheriting its layout's plan has a floor plan.

        0.4 answered this from `floor_plan_image` alone, so a unit whose plan
        came from its layout reported that it had none and the viewer hid the
        button.
        """
        for rec in self:
            rec.has_floor_plan_effective = bool(
                rec.floor_plan_image
                or (rec.visual_unit_type_id
                    and rec.visual_unit_type_id.floor_plan_image))

    def action_clear_visual_override(self):
        """Drop this unit's own assets and fall back to its layout's."""
        for rec in self:
            if not rec.visual_unit_type_id:
                raise ValidationError(_(
                    "%s has no layout to fall back to.") % rec.display_name)
            rec.write({'floor_plan_image': False, 'floor_plan_pdf': False,
                       'interior_glb': False})
        return True
