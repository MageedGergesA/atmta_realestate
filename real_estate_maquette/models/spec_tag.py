from odoo import fields, models


class SpecTag(models.Model):
    """Reusable finishing / specification chip shown on building pages
    (e.g. "Textured paint", "Stone cladding", "Glass railing")."""
    _name = 'realestate.spec.tag'
    _description = 'Building Specification Tag'
    _order = 'name'

    name = fields.Char(required=True)
    color = fields.Integer(default=0, help="Kanban / badge color (0-11).")
    icon = fields.Char(help="Optional FontAwesome class, e.g. 'fa-paint-brush'.")
    notes = fields.Char()
