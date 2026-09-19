# -*- coding: utf-8 -*-
"""M5 — draw a typical floor once, use it on every floor that repeats it.

### The problem

A 30-storey tower with four units per floor needs 120 polygons drawn by hand,
and floors 3 to 28 are usually the same shape. Redrawing an identical layout
twenty-six times is the kind of work that gets abandoned half-finished, which
is how a gallery ends up with mapped floors and unmapped ones.

### The shape

```
    TEMPLATE  "Typical Floor T01"
      ├── slot A   polygon
      ├── slot B   polygon        ← geometry only. No price, no availability,
      ├── slot C   polygon           no reservation, no buyer, no contract.
      └── slot D   polygon

    FLOOR 03 → T01     slot A → property A-301   ┐
    FLOOR 04 → T01     slot A → property A-401   │  each physical unit
    FLOOR 05 → T01     slot A → property A-501   ┘  stays its own record
```

The template says *where the shapes are*. The per-floor mapping says *which
unit each shape is on this floor*. A unit is never owned by a template, and
deleting a template cannot delete a property.

### What a template must never own

Price, availability, reservation, buyer, contract, or any Developer commercial
state. It holds polygons and labels. Everything else is asked of the physical
`realestate.property`, exactly as it is everywhere else in this module — and a
constraint enforces that a slot's polygon is valid geometry, nothing more.

### Why the mapping is explicit rather than inferred

It is tempting to match slot "A" to whichever unit's code ends in "01". That
works until a floor is renumbered, a unit is merged, or a tower skips the
thirteenth floor — and then a customer clicks a shape and gets somebody else's
apartment. Every slot-to-unit link is a row somebody created, and validation
reports the ones that are missing rather than guessing them.
"""

import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class VisualFloorTemplate(models.Model):
    """Reusable floor geometry. Geometry, and nothing else."""
    _name = 'realestate.visual.floor.template'
    _description = 'Typical Floor Template'
    _order = 'project_id, name'
    _check_company_auto = True

    name = fields.Char(required=True)
    code = fields.Char(string='Template Code')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', required=True, index=True, ondelete='cascade')

    plan_image = fields.Image(
        string='Floor Plan Background',
        help="The drawing the polygons sit on. Optional — a template can carry "
             "geometry for a floor whose background comes from the building.")
    plan_image_filename = fields.Char()

    slot_ids = fields.One2many(
        'realestate.visual.floor.template.slot', 'template_id',
        string='Slots')
    slot_count = fields.Integer(compute='_compute_usage', store=True)
    floor_ids = fields.One2many(
        'realestate.building.floor', 'visual_template_id', string='Floors')
    floor_count = fields.Integer(compute='_compute_usage', store=True)
    notes = fields.Html()

    _sql_constraints = [
        ('code_unique_per_project', 'unique(code, project_id)',
         'A template code must be unique within its project.'),
    ]

    @api.depends('slot_ids', 'floor_ids')
    def _compute_usage(self):
        for rec in self:
            rec.slot_count = len(rec.slot_ids)
            rec.floor_count = len(rec.floor_ids)

    @api.constrains('project_id', 'company_id')
    def _check_company(self):
        for rec in self:
            if (rec.project_id.company_id
                    and rec.project_id.company_id != rec.company_id):
                raise ValidationError(_(
                    "Template %(name)s is in %(own)s but its project belongs "
                    "to %(other)s.", name=rec.name,
                    own=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    # ------------------------------------------------------------------
    # Building a template from a floor that is already drawn
    # ------------------------------------------------------------------
    @api.model
    def create_from_floor(self, floor, name=None, code=None):
        """Lift an existing floor's polygons into a reusable template.

        The usual way a template comes into being: somebody has already drawn
        floor 3 by hand, and floors 4 to 28 are the same. The polygons are
        **copied**, not moved — floor 3 keeps working exactly as it did, and if
        the template turns out to be wrong nothing has been lost.

        Slot codes are taken from the source unit's code where one exists, so
        the labels mean something to whoever drew them.
        """
        floor = floor.sudo()
        if not floor.building_id:
            raise UserError(_("That floor has no building."))
        # The polygons belong to 2D Plan, which depends on this module and so
        # may not be installed.
        if 'realestate.plan.region' not in self.env:
            raise UserError(_(
                "Install the 2D Plan application to lift a drawn floor into a "
                "template."))
        regions = self.env['realestate.plan.region'].sudo().search([
            ('parent_property_id', '=', floor.building_id.id),
            ('target_property_id', 'in', floor.unit_ids.ids),
        ])
        if not regions:
            raise UserError(_(
                "Floor %s has no polygons to lift into a template. Draw them "
                "once, then create the template from it."
            ) % floor.display_name)

        template = self.create({
            'name': name or _('Typical Floor — %s') % floor.display_name,
            'code': code or False,
            'project_id': floor.project_id.id,
            'company_id': floor.building_id.company_id.id or self.env.company.id,
        })
        Slot = self.env['realestate.visual.floor.template.slot']
        used = set()
        for index, region in enumerate(regions, start=1):
            code = self._slot_code_from_unit(
                region.target_property_id, floor.floor_number)
            if not code or code in used:
                code = _('Slot %s', index)
            used.add(code)
            Slot.create({
                'template_id': template.id,
                'sequence': index * 10,
                'code': code,
                'label': region.label or region.target_property_id.name,
                'polygon': region.polygon,
                'color': region.color,
            })
        return template

    @api.model
    def _slot_code_from_unit(self, unit, floor_number):
        """The unit's position on its floor, read from its code.

        A slot is a position, repeated on every floor, so its code must not
        carry the building or the floor. The last segment of the unit code is
        taken ('VB-101' → '101') and, when it starts with the floor number and
        leaves at least two digits, the floor is dropped ('101' on floor 1 →
        '01'). The previous rule kept the last four characters, which gave
        codes such as '-101'. Returns '' when the unit has no usable code.
        """
        segments = [s for s in re.split(r'[-/_.\s]+', unit.property_code or '')
                    if s]
        if not segments:
            return ''
        tail = segments[-1]
        prefix = str(floor_number) if floor_number else ''
        rest = tail[len(prefix):]
        if prefix and tail.startswith(prefix) and len(rest) >= 2 \
                and rest.isdigit():
            return rest
        return tail

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate_assignments(self):
        """Report every floor using this template and what is missing.

        Reports rather than repairs: an unmapped slot is a decision about which
        apartment a shape represents, and guessing it puts somebody else's
        apartment in front of a customer.
        """
        self.ensure_one()
        report = []
        for floor in self.floor_ids:
            mapped = {m.slot_id.id: m for m in floor.visual_slot_map_ids}
            missing = [s for s in self.slot_ids if s.id not in mapped]
            wrong_floor = [
                m for m in floor.visual_slot_map_ids
                if m.property_id and m.property_id not in floor.unit_ids]
            report.append({
                'floor_id': floor.id,
                'floor': floor.display_name,
                'slots': len(self.slot_ids),
                'mapped': len(mapped),
                'missing': [s.code for s in missing],
                'wrong_floor': [m.property_id.display_name
                                for m in wrong_floor],
                'complete': not missing and not wrong_floor,
            })
        return report

    def action_validate(self):
        self.ensure_one()
        report = self.validate_assignments()
        incomplete = [r for r in report if not r['complete']]
        if not incomplete:
            message = _("All %s floor(s) are fully mapped.") % len(report)
        else:
            message = _(
                "%(bad)s of %(total)s floor(s) are incomplete:\n\n%(detail)s",
                bad=len(incomplete), total=len(report),
                detail='\n'.join(
                    '• %s — missing %s%s' % (
                        r['floor'], ', '.join(r['missing']) or _('nothing'),
                        (_('; wrong floor: %s') % ', '.join(r['wrong_floor'])
                         if r['wrong_floor'] else ''))
                    for r in incomplete))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': _('Template Validation'), 'message': message,
                       'sticky': bool(incomplete)},
        }

    # ------------------------------------------------------------------
    # Safe removal
    # ------------------------------------------------------------------
    def unlink(self):
        """Refuse to delete a template that floors are using.

        Deleting it would leave those floors with no geometry and no
        explanation. Detaching is a deliberate act, and this makes somebody
        perform it.
        """
        in_use = self.filtered('floor_ids')
        if in_use:
            raise UserError(_(
                "%(names)s %(verb)s still assigned to %(count)s floor(s). "
                "Detach the floors first — deleting the template would leave "
                "them with no geometry and nothing to say why.",
                names=', '.join(in_use.mapped('name')),
                verb=_('is') if len(in_use) == 1 else _('are'),
                count=sum(len(t.floor_ids) for t in in_use)))
        return super().unlink()


class VisualFloorTemplateSlot(models.Model):
    """One shape on a typical floor. A shape, not an apartment."""
    _name = 'realestate.visual.floor.template.slot'
    _description = 'Typical Floor Slot'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one(
        'realestate.visual.floor.template', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='template_id.company_id', store=True, index=True,
        readonly=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(
        required=True,
        help="The label a salesperson uses for this position — A, B, "
             "Corner-1. Not a property code: the same slot is a different "
             "apartment on every floor.")
    label = fields.Char(string='Hover Label')
    color = fields.Char(default='#3b82f6')
    polygon = fields.Char(
        required=True,
        help='JSON list of [x%, y%] points, exactly as `realestate.plan.region`'
             ' stores them, so one renderer draws both.')

    _sql_constraints = [
        ('code_unique_per_template', 'unique(template_id, code)',
         'Slot codes must be unique within a template.'),
    ]

    @api.constrains('polygon')
    def _check_polygon(self):
        """The same geometry rules `realestate.plan.region` enforces.

        Deliberately duplicated rather than shared: a template slot is not a
        region and must not inherit its parent/target constraints, but the
        *geometry* has to be identical or one renderer cannot draw both.
        """
        for rec in self:
            try:
                points = json.loads(rec.polygon or '[]')
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    _("Polygon must be valid JSON: %s") % exc) from exc
            if not isinstance(points, list) or len(points) < 3:
                raise ValidationError(_("A slot needs at least 3 vertices."))
            for point in points:
                if (not isinstance(point, (list, tuple)) or len(point) != 2
                        or not all(isinstance(c, (int, float))
                                   for c in point)):
                    raise ValidationError(_(
                        "Each vertex must be [x, y] with numeric coordinates."))
                if not (0 <= point[0] <= 100 and 0 <= point[1] <= 100):
                    raise ValidationError(_(
                        "Vertex coordinates must be percentages in [0, 100]."))


class VisualFloorSlotMap(models.Model):
    """Which apartment a slot is, on one particular floor."""
    _name = 'realestate.visual.floor.slot.map'
    _description = 'Floor Slot Mapping'
    _order = 'floor_id, slot_id'

    floor_id = fields.Many2one(
        'realestate.building.floor', required=True, ondelete='cascade',
        index=True)
    slot_id = fields.Many2one(
        'realestate.visual.floor.template.slot', required=True,
        ondelete='cascade', index=True)
    property_id = fields.Many2one(
        'realestate.property', string='Unit', required=True,
        ondelete='cascade', index=True,
        help="The physical unit this shape represents on this floor. The "
             "authority for its price, availability and everything else "
             "remains the property itself.")
    company_id = fields.Many2one(
        related='floor_id.company_id', store=True, index=True, readonly=True)

    _sql_constraints = [
        ('slot_unique_per_floor', 'unique(floor_id, slot_id)',
         'A slot can only be mapped once per floor.'),
        ('property_unique_per_floor', 'unique(floor_id, property_id)',
         'A unit can only occupy one slot on a floor.'),
    ]

    @api.constrains('property_id', 'floor_id')
    def _check_unit_is_on_this_floor(self):
        """A shape on floor 4 must not point at a unit on floor 7.

        The most damaging mistake this model can make is putting somebody
        else's apartment behind a click, so it is refused rather than reported.
        """
        for rec in self:
            if rec.property_id not in rec.floor_id.unit_ids:
                raise ValidationError(_(
                    "%(unit)s is not on %(floor)s, so it cannot be mapped to "
                    "one of its shapes.",
                    unit=rec.property_id.display_name,
                    floor=rec.floor_id.display_name))

    @api.constrains('slot_id', 'floor_id')
    def _check_slot_belongs_to_the_floors_template(self):
        for rec in self:
            template = rec.floor_id.visual_template_id
            if template and rec.slot_id.template_id != template:
                raise ValidationError(_(
                    "Slot %(slot)s belongs to a different template than the "
                    "one assigned to %(floor)s.",
                    slot=rec.slot_id.code,
                    floor=rec.floor_id.display_name))


class BuildingFloorTemplate(models.Model):
    """A floor may borrow its geometry from a template."""
    _inherit = 'realestate.building.floor'

    visual_template_id = fields.Many2one(
        'realestate.visual.floor.template', string='Floor Template',
        index=True, ondelete='set null',
        domain="[('project_id', '=', project_id)]",
        help="Reuse a typical floor's geometry here. The units stay their own "
             "records; only the shapes are shared.")
    visual_slot_map_ids = fields.One2many(
        'realestate.visual.floor.slot.map', 'floor_id',
        string='Slot Mapping')
    visual_mapping_complete = fields.Boolean(
        compute='_compute_visual_mapping', store=True)
    visual_unmapped_slots = fields.Integer(
        compute='_compute_visual_mapping', store=True)

    @api.depends('visual_template_id', 'visual_template_id.slot_ids',
                 'visual_slot_map_ids')
    def _compute_visual_mapping(self):
        for rec in self:
            slots = rec.visual_template_id.slot_ids if rec.visual_template_id \
                else self.env['realestate.visual.floor.template.slot']
            mapped = rec.visual_slot_map_ids.mapped('slot_id')
            rec.visual_unmapped_slots = len(slots - mapped)
            rec.visual_mapping_complete = bool(
                slots and not rec.visual_unmapped_slots)

    def visual_regions(self):
        """The polygons to draw for this floor, resolved to real units.

        The one method the renderer calls. A floor with no template returns
        nothing here and the existing per-property `realestate.plan.region`
        path continues to serve it — templates are additive, and no floor that
        works today stops working.
        """
        self.ensure_one()
        if not self.visual_template_id:
            return []
        mapping = {m.slot_id.id: m.property_id
                   for m in self.visual_slot_map_ids}
        regions = []
        for slot in self.visual_template_id.slot_ids:
            unit = mapping.get(slot.id)
            regions.append({
                'slot_code': slot.code,
                'label': slot.label or (unit.display_name if unit else
                                        slot.code),
                'color': slot.color or '#3b82f6',
                'polygon': slot.polygon,
                'property_id': unit.id if unit else False,
                'mapped': bool(unit),
            })
        return regions

    def action_detach_template(self):
        """Stop using a template, keeping the floor and its units intact."""
        for rec in self:
            rec.visual_slot_map_ids.unlink()
            rec.visual_template_id = False
        return True
