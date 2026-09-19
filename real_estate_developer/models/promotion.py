# -*- coding: utf-8 -*-
"""Phase 9 — commercial promotions.

A promotion is a *campaign*: approved once, applied many times, valid for a
period, sometimes capped. A discretionary discount is a *negotiation*: unique to
one deal and subject to the authority matrix.

They are modelled separately on purpose. Folding them together is how a
developer ends up unable to answer "how much did the Eid campaign cost us?",
because campaign money and negotiated money are in the same column.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .commercial_states import PROMOTION_RELEASING_STATES

PROMOTION_STATE = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]


class Promotion(models.Model):
    _name = 'realestate.promotion'
    _description = 'Commercial Promotion'
    _inherit = ['mail.thread', 'mail.activity.mixin',
                'realestate.commercial.approval.mixin']
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True, index='trigram')
    code = fields.Char(copy=False, help="Optional campaign code for reporting.")
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', string='Project', ondelete='cascade',
        index=True, tracking=True, check_company=True,
        help="Leave empty to run across every project in the company.")
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', ondelete='cascade',
        domain="[('project_id', '=', project_id)]", check_company=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    state = fields.Selection(
        PROMOTION_STATE, default='draft', required=True, tracking=True, index=True)

    date_start = fields.Date(required=True, tracking=True,
                             default=fields.Date.context_today)
    date_end = fields.Date(tracking=True,
                           help="Empty means the promotion runs until cancelled.")

    discount_type = fields.Selection([
        ('percent', '% of List Price'),
        ('fixed', 'Fixed Amount'),
    ], default='percent', required=True, tracking=True)
    discount_value = fields.Float(required=True, tracking=True)

    # ---- Eligibility ----
    property_ids = fields.Many2many(
        'realestate.property', 'realestate_promotion_property_rel',
        'promotion_id', 'property_id', string='Eligible Units',
        check_company=True,
        help="Leave empty to cover every unit in the project/phase scope.")
    usage_category = fields.Char(
        string='Usage Category', help="Empty = any usage category.")

    # The deals claiming this campaign. Declared so `use_count` can depend on
    # them: it is stored, and a stored count that only watched `max_uses`
    # was computed once, at zero, and never again.
    reservation_ids = fields.One2many(
        'realestate.unit.reservation', 'promotion_id', string='Reservations')

    max_uses = fields.Integer(
        string='Maximum Uses',
        help="Zero means unlimited. A limited-units campaign sets this.")
    use_count = fields.Integer(
        compute='_compute_use_count', store=True, string='Used')
    remaining_uses = fields.Integer(compute='_compute_use_count')

    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    notes = fields.Html()

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for promo in self:
            if promo.date_end and promo.date_end < promo.date_start:
                raise ValidationError(_(
                    "Promotion '%s' ends before it starts.") % promo.name)

    @api.constrains('discount_value', 'discount_type')
    def _check_value(self):
        for promo in self:
            if promo.discount_value <= 0:
                raise ValidationError(_(
                    "Promotion '%s': the discount must be positive.") % promo.name)
            if promo.discount_type == 'percent' and promo.discount_value > 100:
                raise ValidationError(_(
                    "Promotion '%s': a discount of more than 100%% would pay "
                    "the buyer.") % promo.name)

    @api.constrains('max_uses')
    def _check_max_uses(self):
        for promo in self:
            if promo.max_uses < 0:
                raise ValidationError(_(
                    "Promotion '%s': maximum uses cannot be negative.") % promo.name)

    @api.depends('max_uses', 'reservation_ids.state',
                 'reservation_ids.promotion_id')
    def _compute_use_count(self):
        """How many live deals currently claim this promotion.

        Counted from the deals themselves rather than kept as a counter,
        because a counter and a cancellation eventually disagree.
        """
        for promo in self:
            promo.use_count = 0
            promo.remaining_uses = promo.max_uses
        if not self.ids:
            return
        Reservation = self.env['realestate.unit.reservation']
        if 'promotion_id' not in Reservation._fields:
            return
        groups = Reservation._read_group(
            [('promotion_id', 'in', self.ids),
             ('state', 'not in', PROMOTION_RELEASING_STATES)],
            groupby=['promotion_id'], aggregates=['__count'])
        for promo, count in groups:
            if promo in self:
                promo.use_count = count
                promo.remaining_uses = (
                    max(promo.max_uses - count, 0) if promo.max_uses else 0)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        self.filtered(lambda p: p.state == 'draft').write(
            {'state': 'pending_approval'})

    def action_approve(self):
        for promo in self:
            if promo.state != 'pending_approval':
                raise UserError(_(
                    "Promotion '%s' is not awaiting approval.") % promo.name)
            # A campaign discount is still a discount: it goes through the same
            # authority matrix as a negotiated one, measured on its percentage.
            value = promo.discount_value if promo.discount_type == 'percent' else 0.0
            promo._require_approval('discount', value,
                                    amount=promo.discount_value)
            promo.write({
                'state': 'active',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    def _approval_needed(self):
        """Approving a campaign is measured as `action_approve` measures it."""
        self.ensure_one()
        if self.state != 'pending_approval':
            return None
        value = self.discount_value if self.discount_type == 'percent' else 0.0
        return ('discount', value, self.discount_value,
                _('Promotion %s') % self.display_name)

    def action_expire(self):
        self.write({'state': 'expired'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    # ------------------------------------------------------------------
    # Eligibility and amount
    # ------------------------------------------------------------------
    def _is_live_on(self, date):
        self.ensure_one()
        if self.state != 'active':
            return False
        if self.date_start and date < self.date_start:
            return False
        if self.date_end and date > self.date_end:
            return False
        if self.max_uses and self.use_count >= self.max_uses:
            return False
        return True

    def _covers(self, property_record):
        """Whether this promotion is available for a given unit."""
        self.ensure_one()
        if self.project_id and property_record.project_id != self.project_id:
            return False
        if self.phase_id and property_record.phase_id != self.phase_id:
            return False
        if self.property_ids and property_record not in self.property_ids:
            return False
        if (self.usage_category
                and property_record.usage_category != self.usage_category):
            return False
        return True

    def _amount_for(self, list_price):
        """The money this promotion takes off ``list_price``."""
        self.ensure_one()
        if self.discount_type == 'percent':
            return list_price * self.discount_value / 100.0
        return min(self.discount_value, list_price)

    @api.model
    def _eligible_for(self, property_record, date=None):
        """Every live promotion a unit qualifies for today."""
        date = date or fields.Date.context_today(self)
        candidates = self.search([
            ('state', '=', 'active'),
            ('company_id', '=', property_record.company_id.id),
        ])
        return candidates.filtered(
            lambda p: p._is_live_on(date) and p._covers(property_record))

    @api.model
    def _cron_expire_promotions(self):
        """Close promotions whose window has passed."""
        today = fields.Date.context_today(self)
        stale = self.search([
            ('state', '=', 'active'),
            ('date_end', '!=', False),
            ('date_end', '<', today),
        ])
        stale.write({'state': 'expired'})
        return len(stale)
