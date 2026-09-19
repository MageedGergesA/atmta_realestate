"""Enterprise property identification (Phase 3) and usage taxonomy (Phase 1).

Two things happen here.

**1. Hierarchy is decoupled from usage.**
``hierarchy_level`` answers "where does this sit in the tree" (compound /
building / floor / unit / room). ``usage_category`` answers "what is it
commercially" (apartment / office / warehouse / parking ...). Neither may be
inferred from the other -- a *unit* can be an apartment or a retail shop, and a
*parking* space can be a unit or a whole floor.

**2. Identifiers get scoped uniqueness.**
The pre-upgrade model declared ``UNIQUE(property_code)`` globally. In a
multi-company database two legal entities may legitimately both run a
"A-101" coding scheme, so that constraint is relaxed to be per-company.
Relaxing a uniqueness constraint can never invalidate existing rows, so the
migration is safe in both directions.

Fields that already existed under a different name are **not** duplicated:

======================= =====================================================
Requested name          Resolution
======================= =====================================================
property_code           already existed -- kept as-is
unit_number             alias of the existing ``property_number`` (related,
                        no second column)
building_code           new (the existing ``building_no`` is a postal address
                        component, not a developer code)
external_reference      new
legacy_reference        new
developer_unit_code     new
plot_number             new
registration_number     new
deed_number             new
======================= =====================================================
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: ``usage_category`` itself (and its selection) belongs to
#: ``atmta_property_core``; Rental only derives leasability from it.

#: Usage categories that are never leased on their own and must be excluded
#: from occupancy statistics and the rent roll.
NON_LEASABLE_USAGES = ('common_area',)


class PropertyIdentity(models.Model):
    _inherit = 'realestate.property'

    # `company_id` is NOT declared here any more. A property belongs to a
    # company before it is leased, so the field and the global isolation rule
    # that filters on it belong to `atmta_property_core` -- otherwise Property
    # Core could not be installed multi-company-correct without this module.
    # The company-scoped uniqueness constraints below still rely on it.

    # ------------------------------------------------------------------
    # Phase 1 -- usage is not hierarchy
    # ------------------------------------------------------------------
    is_leasable = fields.Boolean(
        string='Leasable', compute='_compute_is_leasable', store=True, index=True,
        help="Whether this record can ever carry a lease. False for structural "
             "levels (compound/building/floor) and for common areas.",
    )

    @api.depends('hierarchy_level', 'usage_category')
    def _compute_is_leasable(self):
        for rec in self:
            rec.is_leasable = (
                rec.hierarchy_level in ('unit', 'room')
                and rec.usage_category not in NON_LEASABLE_USAGES
            )

    # ------------------------------------------------------------------
    # Phase 3 -- identifiers
    # ------------------------------------------------------------------
    unit_number = fields.Char(
        related='property_number', string='Unit Number', readonly=False,
        help="Alias of the pre-existing 'Property Number' field. Provided so "
             "integrations can use the conventional name without a second "
             "column being stored.",
    )
    building_code = fields.Char(
        string='Building Code', index='trigram', tracking=True,
        help="Developer's code for the building (e.g. 'B4'). Distinct from the "
             "postal 'Building Number' under Location.",
    )
    plot_number = fields.Char(string='Plot Number', tracking=True)
    developer_unit_code = fields.Char(
        string='Developer Unit Code', index='trigram', tracking=True,
        help="Unit code as issued by the developer, when it differs from the "
             "internal property code.",
    )
    external_reference = fields.Char(
        string='External Reference', index='trigram', copy=False, tracking=True,
        help="Stable identifier in a third-party system (portal, CRM, legacy "
             "ERP). Unique per company when set.",
    )
    legacy_reference = fields.Char(
        string='Legacy Reference', index='trigram', copy=False,
        help="Identifier carried over from a migrated system. Informational -- "
             "not enforced unique, because legacy data is often dirty.",
    )
    registration_number = fields.Char(
        string='Registration Number', tracking=True, copy=False,
        help="Government property registration / municipality number.",
    )
    deed_number = fields.Char(
        string='Title Deed Number', tracking=True, copy=False,
    )
    deed_date = fields.Date(string='Title Deed Date', tracking=True)

    # ------------------------------------------------------------------
    # Scoped uniqueness
    # ------------------------------------------------------------------
    _sql_constraints = [
        # Replaces the pre-upgrade global UNIQUE(property_code). Relaxing a
        # constraint can never invalidate stored rows, so no data migration is
        # required.
        ('property_code_company_uniq',
         'UNIQUE(company_id, property_code)',
         'Property Code must be unique within a company.'),
    ]

    def init(self):
        """Partial unique indexes.

        Expressed as raw indexes rather than ``_sql_constraints`` because they
        must ignore NULL *and* empty-string values -- a plain UNIQUE would let
        many rows share ''.
        """
        super_init = getattr(super(), 'init', None)
        if super_init:
            super_init()
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                realestate_property_external_ref_company_uniq
            ON realestate_property (company_id, external_reference)
            WHERE external_reference IS NOT NULL AND external_reference <> ''
        """)
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                realestate_property_unit_in_parent_uniq
            ON realestate_property (company_id, parent_id, property_number)
            WHERE property_number IS NOT NULL AND property_number <> ''
              AND parent_id IS NOT NULL
        """)
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                realestate_property_deed_company_uniq
            ON realestate_property (company_id, deed_number)
            WHERE deed_number IS NOT NULL AND deed_number <> ''
        """)

    @api.constrains('property_code', 'company_id')
    def _check_unique_code(self):
        """Company-scoped replacement for the base global check.

        Overrides ``_base_property._check_unique_code`` (same method name), so
        the global variant no longer runs.
        """
        for rec in self:
            if not rec.property_code or rec.property_code == _('New'):
                continue
            clash = self.search([
                ('property_code', '=', rec.property_code),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "Property Code '%(code)s' already exists in company "
                    "'%(company)s' (used by %(other)s).",
                    code=rec.property_code,
                    company=rec.company_id.display_name,
                    other=clash.display_name,
                ))

    @api.constrains('hierarchy_level', 'usage_category')
    def _check_usage_vs_hierarchy(self):
        """Guard the one genuine incompatibility.

        A land plot cannot sit *inside* a building, and a room cannot be a land
        plot. Everything else is deliberately permitted -- retail on a floor,
        parking as a whole building, and so on.
        """
        for rec in self:
            if rec.usage_category == 'land_plot' and rec.hierarchy_level in ('floor', 'room'):
                raise ValidationError(_(
                    "A land plot cannot be a %s.",
                    dict(rec._fields['hierarchy_level'].selection).get(
                        rec.hierarchy_level, rec.hierarchy_level),
                ))
