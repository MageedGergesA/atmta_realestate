# -*- coding: utf-8 -*-
"""Phase 6 — the premium engine.

Conditions are named, real-estate-specific columns rather than a generic
domain expression. That is a deliberate trade: a domain field would express
strictly more, and no sales administrator would ever be able to configure or
audit it. Everything here can be read off a form and explained to a buyer.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .price_book import CALCULATION_TYPE, PREMIUM_TYPE


class PriceRule(models.Model):
    _name = 'realestate.price.rule'
    _description = 'Price Premium Rule'
    _order = 'price_book_id, sequence, id'
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    price_book_id = fields.Many2one(
        'realestate.price.book', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='price_book_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='price_book_id.currency_id', store=True, readonly=True)

    premium_type = fields.Selection(
        PREMIUM_TYPE, required=True, default='floor', string='Premium')
    calculation_type = fields.Selection(
        CALCULATION_TYPE, required=True, default='percent', string='Calculation')
    value = fields.Float(
        required=True,
        help="A percentage, a fixed amount, or an amount per m² depending on "
             "the calculation type.")

    # ---- Conditions. All are AND-ed; empty means "does not restrict". ----
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase Only', ondelete='cascade',
        check_company=True)
    building_id = fields.Many2one(
        'realestate.property', string='Building Only', ondelete='cascade',
        domain="[('hierarchy_level', 'in', ('building', 'compound'))]",
        check_company=True,
        help="Applies only to units under this building or compound.")
    floor_from = fields.Integer(string='Floor From')
    floor_to = fields.Integer(string='Floor To')
    usage_category = fields.Char(
        string='Usage Category',
        help="Matches the unit's usage category exactly. Empty = any.")
    area_from = fields.Float(string='Area From (m²)')
    area_to = fields.Float(string='Area To (m²)')

    @api.constrains('floor_from', 'floor_to', 'area_from', 'area_to')
    def _check_bands(self):
        for rule in self:
            if rule.floor_to and rule.floor_to < rule.floor_from:
                raise ValidationError(_(
                    "Rule '%s': the floor band ends below where it starts."
                ) % rule.name)
            if rule.area_to and rule.area_to < rule.area_from:
                raise ValidationError(_(
                    "Rule '%s': the area band ends below where it starts."
                ) % rule.name)

    @api.constrains('value', 'calculation_type')
    def _check_value(self):
        for rule in self:
            if rule.calculation_type == 'percent' and abs(rule.value) > 100:
                raise ValidationError(_(
                    "Rule '%s': a premium of %.2f%% of the base price is "
                    "almost certainly a typo."
                ) % (rule.name, rule.value))

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def _applies_to(self, property_record):
        """Whether every condition on this rule holds for the unit."""
        self.ensure_one()
        prop = property_record
        if self.phase_id and prop.phase_id != self.phase_id:
            return False
        if self.building_id and not self._is_under(prop, self.building_id):
            return False
        if self.usage_category and prop.usage_category != self.usage_category:
            return False

        floor = self._floor_of(prop)
        if self.floor_from and (floor is None or floor < self.floor_from):
            return False
        if self.floor_to and (floor is None or floor > self.floor_to):
            return False

        area = prop.area_sqm or 0.0
        if self.area_from and area < self.area_from:
            return False
        if self.area_to and area > self.area_to:
            return False
        return True

    @staticmethod
    def _is_under(property_record, ancestor):
        """Walk the Module 1 hierarchy upward looking for ``ancestor``."""
        node = property_record
        # Bounded walk: the hierarchy is at most compound→building→floor→unit→
        # room, so a handful of steps. The bound also protects against a cycle
        # in bad data rather than hanging.
        for _step in range(10):
            if not node:
                return False
            if node == ancestor:
                return True
            node = node.parent_id
        return False

    @staticmethod
    def _floor_of(property_record):
        """The unit's floor number, or None when it cannot be determined.

        Module 1 models floors as hierarchy nodes, so this reads the parent
        chain. A unit hung directly under its building has no floor node, only
        its own ``floor_number``, and a floor band ignored it; that number is
        used when no floor node answers. Returning None rather than 0 matters:
        0 is the ground floor, not "unknown" -- but an empty ``floor_number``
        also reads 0, so 0 there cannot be told from "not entered" and is not
        trusted.
        """
        own_floor = property_record.floor_number or None
        node = property_record
        for _step in range(10):
            if not node:
                return own_floor
            if node.hierarchy_level == 'floor':
                for source in (node.property_number, node.name):
                    if not source:
                        continue
                    digits = ''.join(
                        ch for ch in str(source) if ch.isdigit() or ch == '-')
                    if digits.strip('-'):
                        try:
                            return int(digits)
                        except ValueError:
                            continue
                return own_floor
            node = node.parent_id
        return own_floor

    def _amount_for(self, property_record, base_price):
        """The money this rule adds to ``base_price`` for the unit."""
        self.ensure_one()
        if self.calculation_type == 'fixed':
            return self.value
        if self.calculation_type == 'percent':
            return base_price * self.value / 100.0
        if self.calculation_type == 'per_sqm':
            return self.value * (property_record.area_sqm or 0.0)
        return 0.0


class PropertyPriceComponent(models.Model):
    """How a unit's current list price was built.

    Kept as records rather than recomputed on demand so the build-up can be
    shown, exported, and copied verbatim into a reservation snapshot. Regenerated
    when a price book is activated; the audit trail of *changes* lives in
    ``realestate.price.history``, not here.
    """
    _name = 'realestate.property.price.component'
    _description = 'Unit Price Component'
    _order = 'property_id, sequence, id'

    property_id = fields.Many2one(
        'realestate.property', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    price_book_id = fields.Many2one(
        'realestate.price.book', ondelete='set null', index=True)
    rule_id = fields.Many2one('realestate.price.rule', ondelete='set null')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    component_type = fields.Selection(
        [('base', 'Base Price')] + PREMIUM_TYPE,
        required=True, default='custom')
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one('res.currency', required=True)


class PriceHistory(models.Model):
    """Phase 7 — every price change a unit has ever had.

    Append-only by construction: there is no workflow that edits or deletes a
    row, and ``unlink`` is blocked. A price a buyer was quoted must remain
    provable years later.
    """
    _name = 'realestate.price.history'
    _description = 'Unit Price History'
    _order = 'effective_date desc, id desc'
    _rec_name = 'property_id'

    property_id = fields.Many2one(
        'realestate.property', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    project_id = fields.Many2one(
        related='property_id.project_id', store=True, readonly=True, index=True)
    price_book_id = fields.Many2one(
        'realestate.price.book', ondelete='set null', index=True)
    rule_id = fields.Many2one('realestate.price.rule', ondelete='set null',
                              string='Source Rule')

    old_price = fields.Monetary(required=True)
    new_price = fields.Monetary(required=True)
    delta = fields.Monetary(compute='_compute_delta', store=True)
    delta_percent = fields.Float(
        compute='_compute_delta', store=True, string='Change %')
    currency_id = fields.Many2one('res.currency', required=True)

    effective_date = fields.Date(
        required=True, index=True, default=fields.Date.context_today)
    changed_by_id = fields.Many2one(
        'res.users', string='Changed By', required=True, readonly=True,
        default=lambda self: self.env.user)
    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True)
    approval_request_id = fields.Many2one(
        'realestate.commercial.approval.request', string='Approval', readonly=True)
    reason = fields.Char()

    @api.depends('old_price', 'new_price')
    def _compute_delta(self):
        for rec in self:
            rec.delta = rec.new_price - rec.old_price
            rec.delta_percent = (
                (rec.delta / rec.old_price * 100.0) if rec.old_price else 0.0)

    #: Fields Odoo itself maintains: stored computes and related-stored
    #: mirrors. Blocking these would break the ORM's own recompute pass, so the
    #: guard below is scoped to the fields a *user* could meaningfully falsify.
    _MAINTAINED_FIELDS = {'delta', 'delta_percent', 'project_id'}

    def write(self, vals):
        if set(vals) - self._MAINTAINED_FIELDS:
            raise UserError(_(
                "Price history is a record of what happened and cannot be "
                "edited. Record a new price change instead."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            "Price history cannot be deleted. A price a buyer was quoted has "
            "to stay provable years later."))
