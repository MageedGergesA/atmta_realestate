"""What a property IS commercially -- apartment, villa, office, retail, ...

Deliberately orthogonal to ``hierarchy_level``: a unit may be an apartment or a
shop. Seeded from the property type when the user has not set it.

This lived in ``atmta_real_estate``. It is a plain classification of the unit,
and Developer's price rules and promotions match on it, so it belongs with the
model. Rental keeps what it derives from usage (``is_leasable``, and the usages
that are never leased on their own).
"""

from odoo import api, fields, models


#: Commercial usage. Deliberately orthogonal to ``hierarchy_level``.
USAGE_CATEGORIES = [
    ('apartment', 'Apartment'),
    ('villa', 'Villa'),
    ('office', 'Office'),
    ('retail', 'Retail'),
    ('warehouse', 'Warehouse'),
    ('parking', 'Parking'),
    ('storage', 'Storage'),
    ('hotel_room', 'Hotel Room'),
    ('land_plot', 'Land Plot'),
    ('common_area', 'Common Area'),
    ('other', 'Other'),
]


class PropertyUsageCategory(models.Model):
    _inherit = 'realestate.property'

    usage_category = fields.Selection(
        USAGE_CATEGORIES, string='Usage', index=True, tracking=True,
        compute='_compute_usage_category', store=True, readonly=False,
        help="What the property IS commercially. Independent of where it sits "
             "in the hierarchy -- a unit may be an apartment or a shop.",
    )

    @api.depends('property_type_id', 'property_type_id.usage_category',
                 'hierarchy_level')
    def _compute_usage_category(self):
        """Seed usage from the property type when the user has not set it.

        Never derived from ``hierarchy_level`` -- that is exactly the coupling
        this classification removes.
        """
        for rec in self:
            if rec.usage_category:
                continue
            rec.usage_category = rec.property_type_id.usage_category or False


class PropertyTypeUsageCategory(models.Model):
    """Give the property-type catalogue a usage classification so
    ``usage_category`` can be seeded without re-keying every property."""
    _inherit = 'property.type'

    usage_category = fields.Selection(
        USAGE_CATEGORIES, string='Usage Category',
        help="Default commercial usage for properties of this type.",
    )
