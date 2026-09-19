# -*- coding: utf-8 -*-
"""Phases 6 & 7 on the unit — the price build-up and its history.

``base_price`` already existed on the property and is read by the public API and
the map, so it is kept as the unit's current headline price. What is new is that
it is now *explained*: the components say how it was reached, and the history
says how it got there.
"""

from odoo import _, api, fields, models


class PropertyPricing(models.Model):
    _inherit = 'realestate.property'

    price_component_ids = fields.One2many(
        'realestate.property.price.component', 'property_id',
        string='Price Build-up')
    price_history_ids = fields.One2many(
        'realestate.price.history', 'property_id', string='Price History')
    price_history_count = fields.Integer(compute='_compute_price_counts')

    active_price_book_id = fields.Many2one(
        'realestate.price.book', string='Price Book',
        compute='_compute_active_price_book',
        help="The book that currently governs this unit's price.")

    premium_total = fields.Monetary(
        compute='_compute_price_breakdown', string='Premiums')
    list_price_developer = fields.Monetary(
        compute='_compute_price_breakdown', string='List Price',
        help="Base plus premiums, before any promotion or discount. Equal to "
             "base_price whenever a price book governs the unit.")

    def _compute_price_counts(self):
        counts = {}
        if self.ids:
            groups = self.env['realestate.price.history']._read_group(
                [('property_id', 'in', self.ids)],
                groupby=['property_id'], aggregates=['__count'])
            counts = {prop.id: count for prop, count in groups}
        for rec in self:
            rec.price_history_count = counts.get(rec.id, 0)

    @api.depends('project_id', 'phase_id', 'company_id')
    def _compute_active_price_book(self):
        Book = self.env['realestate.price.book']
        for rec in self:
            rec.active_price_book_id = (
                Book._find_active(rec) if rec.project_id else False)

    @api.depends('price_component_ids.amount', 'base_price')
    def _compute_price_breakdown(self):
        for rec in self:
            components = rec.price_component_ids
            if components:
                base = sum(
                    c.amount for c in components if c.component_type == 'base')
                premiums = sum(
                    c.amount for c in components if c.component_type != 'base')
                rec.premium_total = premiums
                rec.list_price_developer = base + premiums
            else:
                # No book has been activated for this unit yet, so base_price is
                # whatever was entered by hand. Reporting it as the list price
                # is honest; inventing a zero premium breakdown would not be.
                rec.premium_total = 0.0
                rec.list_price_developer = rec.base_price

    def action_view_price_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Price History — %s') % self.display_name,
            'res_model': 'realestate.price.history',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'target': 'current',
        }

    def action_view_price_components(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Price Build-up — %s') % self.display_name,
            'res_model': 'realestate.property.price.component',
            'views': [(False, 'list')],
            'view_mode': 'list',
            'domain': [('property_id', '=', self.id)],
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Public vs internal pricing (Phase 40)
    # ------------------------------------------------------------------
    def _public_price(self):
        """The only price that may be published outside the company.

        The Phase 0 audit found ``base_price`` — the internal number every
        discount is measured against — being served from an ``auth='public'``
        API endpoint. This method exists so public callers have a correct thing
        to ask for, and returns 0.0 rather than a real figure for a unit that is
        not on the market: an unreleased unit has no public price, and guessing
        one leaks the pricing of inventory that has not launched.
        """
        self.ensure_one()
        if not self.is_available_for_sale:
            return 0.0
        return self.list_price_developer
