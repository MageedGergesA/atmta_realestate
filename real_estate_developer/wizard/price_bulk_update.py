# -*- coding: utf-8 -*-
"""Phase 8 — bulk price operations, with a preview that is not optional.

Developers reprice hundreds of units at a time: "Building A up 3%", "add
200,000 to garden-view units", "floors 10-20 up 5%". Doing that with a mass
write is how a project accidentally reprices its entire inventory on a Friday
afternoon.

So the wizard has two steps and they cannot be collapsed: compute a preview of
every affected unit with its old and new price, then apply. The apply step
re-reads the preview lines rather than recomputing, so what is applied is
exactly what was shown.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PriceBulkUpdateWizard(models.TransientModel):
    _name = 'realestate.price.bulk.update'
    _description = 'Bulk Price Update'
    _inherit = ['realestate.commercial.approval.mixin']

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True,
        check_company=True)
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase',
        domain="[('project_id', '=', project_id)]", check_company=True)
    building_id = fields.Many2one(
        'realestate.property', string='Building',
        domain="[('hierarchy_level', 'in', ('building', 'compound'))]",
        check_company=True)
    usage_category = fields.Char(string='Usage Category')
    floor_from = fields.Integer()
    floor_to = fields.Integer()
    only_available = fields.Boolean(
        string='Only Units Available for Sale', default=True,
        help="On by default. Repricing a unit that is already reserved or sold "
             "does not change that deal — its price was snapshotted — so "
             "including it only creates confusing history.")

    adjustment_type = fields.Selection([
        ('percent', 'Percentage'),
        ('amount', 'Fixed Amount'),
        ('per_sqm', 'Amount per m²'),
        ('set', 'Set to Value'),
    ], default='percent', required=True)
    adjustment_value = fields.Float(required=True)
    reason = fields.Char(required=True, help="Recorded on every history row.")
    effective_date = fields.Date(
        required=True, default=fields.Date.context_today)

    state = fields.Selection([
        ('config', 'Configure'),
        ('preview', 'Preview'),
    ], default='config')
    line_ids = fields.One2many(
        'realestate.price.bulk.update.line', 'wizard_id', string='Preview')
    affected_count = fields.Integer(compute='_compute_totals')
    total_delta = fields.Monetary(compute='_compute_totals')
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)

    @api.depends('line_ids.delta')
    def _compute_totals(self):
        for wiz in self:
            wiz.affected_count = len(wiz.line_ids)
            wiz.total_delta = sum(wiz.line_ids.mapped('delta'))

    @api.constrains('adjustment_type', 'adjustment_value')
    def _check_value(self):
        for wiz in self:
            if wiz.adjustment_type == 'percent' and abs(wiz.adjustment_value) > 100:
                raise ValidationError(_(
                    "A %.2f%% bulk adjustment is almost certainly a typo."
                ) % wiz.adjustment_value)
            if wiz.adjustment_type == 'set' and wiz.adjustment_value < 0:
                raise ValidationError(_("A price cannot be negative."))

    # ------------------------------------------------------------------
    def _target_properties(self):
        domain = [
            ('project_id', '=', self.project_id.id),
            ('company_id', '=', self.company_id.id),
            ('hierarchy_level', '=', 'unit'),
        ]
        if self.phase_id:
            domain.append(('phase_id', '=', self.phase_id.id))
        if self.usage_category:
            domain.append(('usage_category', '=', self.usage_category))
        if self.only_available:
            domain.append(('is_available_for_sale', '=', True))
        properties = self.env['realestate.property'].search(domain)

        Rule = self.env['realestate.price.rule']
        if self.building_id:
            properties = properties.filtered(
                lambda p: Rule._is_under(p, self.building_id))
        if self.floor_from or self.floor_to:
            def in_band(prop):
                floor = Rule._floor_of(prop)
                if floor is None:
                    return False
                if self.floor_from and floor < self.floor_from:
                    return False
                if self.floor_to and floor > self.floor_to:
                    return False
                return True
            properties = properties.filtered(in_band)
        return properties

    def _new_price(self, prop):
        old = prop.base_price or 0.0
        if self.adjustment_type == 'percent':
            return old * (1 + self.adjustment_value / 100.0)
        if self.adjustment_type == 'amount':
            return old + self.adjustment_value
        if self.adjustment_type == 'per_sqm':
            return old + self.adjustment_value * (prop.area_sqm or 0.0)
        return self.adjustment_value

    def action_preview(self):
        """Show exactly which units move, and by how much, before anything does."""
        self.ensure_one()
        self.line_ids.unlink()
        properties = self._target_properties()
        if not properties:
            raise UserError(_(
                "No unit matches these criteria, so there is nothing to "
                "reprice. Widen the filters or turn off 'Only Units Available "
                "for Sale'."))
        Line = self.env['realestate.price.bulk.update.line']
        Line.create([{
            'wizard_id': self.id,
            'property_id': prop.id,
            'old_price': prop.base_price or 0.0,
            'new_price': self._new_price(prop),
        } for prop in properties])
        self.state = 'preview'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_apply(self):
        """Apply exactly what the preview showed, atomically, with history."""
        self.ensure_one()
        if self.state != 'preview':
            raise UserError(_(
                "Preview the change before applying it. A blind mass price "
                "write is not supported."))
        if not self.line_ids:
            raise UserError(_("There is nothing to apply."))

        # A bulk reprice is measured on its largest single move, not its
        # average: approving "3% on average" would wave through a 40% outlier.
        largest = max(abs(line.delta_percent) for line in self.line_ids)
        self._require_approval(
            'bulk_price_change', largest, amount=abs(self.total_delta),
            reason=self.reason)

        History = self.env['realestate.price.history']
        history_vals = []
        for line in self.line_ids:
            if line.old_price == line.new_price:
                continue
            history_vals.append({
                'property_id': line.property_id.id,
                'company_id': self.company_id.id,
                'old_price': line.old_price,
                'new_price': line.new_price,
                'effective_date': self.effective_date,
                'currency_id': self.currency_id.id,
                'reason': self.reason,
            })
            line.property_id.base_price = line.new_price
        if history_vals:
            History.create(history_vals)
        return {'type': 'ir.actions.act_window_close'}

    def _approval_project(self):
        return self.project_id


class PriceBulkUpdateLine(models.TransientModel):
    _name = 'realestate.price.bulk.update.line'
    _description = 'Bulk Price Update Preview Line'
    _order = 'property_id'

    wizard_id = fields.Many2one(
        'realestate.price.bulk.update', required=True, ondelete='cascade')
    property_id = fields.Many2one('realestate.property', required=True)
    area_sqm = fields.Float(related='property_id.area_sqm', readonly=True)
    old_price = fields.Monetary(required=True)
    new_price = fields.Monetary(required=True)
    delta = fields.Monetary(compute='_compute_delta', store=True)
    delta_percent = fields.Float(compute='_compute_delta', store=True)
    currency_id = fields.Many2one(
        related='wizard_id.currency_id', readonly=True)

    @api.depends('old_price', 'new_price')
    def _compute_delta(self):
        for line in self:
            line.delta = line.new_price - line.old_price
            line.delta_percent = (
                line.delta / line.old_price * 100.0 if line.old_price else 0.0)
