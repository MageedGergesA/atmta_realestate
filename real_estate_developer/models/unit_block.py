# -*- coding: utf-8 -*-
"""Phase 4 — commercial blocks.

Developers withhold units for a dozen ordinary reasons: a show flat, a legal
dispute, a unit promised to a VIP, a pricing review. Today the only way to
express that is to set the property's commercial status to something that stops
sales — which destroys the previous status, records no reason, and leaves no
history once it is lifted.

A block is therefore its own dated record. The property's ``commercial_status``
is left alone; ``is_available_for_sale`` consults live blocks instead. Lifting a
block restores availability without having to remember what the status was
before, and every block a unit has ever had stays queryable.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .commercial_states import BLOCK_REASON


class UnitBlock(models.Model):
    _name = 'realestate.unit.block'
    _description = 'Unit Commercial Block'
    _inherit = ['mail.thread']
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    property_id = fields.Many2one(
        'realestate.property', string='Unit', required=True,
        ondelete='cascade', index=True, tracking=True, check_company=True,
    )
    project_id = fields.Many2one(
        related='property_id.project_id', store=True, readonly=True, index=True)
    phase_id = fields.Many2one(
        related='property_id.phase_id', store=True, readonly=True)

    reason = fields.Selection(
        BLOCK_REASON, string='Reason', required=True, tracking=True)
    notes = fields.Text(string='Details')

    date_start = fields.Date(
        string='From', required=True, default=fields.Date.context_today,
        tracking=True)
    date_end = fields.Date(
        string='Until', tracking=True,
        help="Leave empty for an indefinite block. An indefinite block is "
             "lifted explicitly, not by the calendar.")

    #: Whether the block is still in force. Odoo treats ``active`` specially:
    #: archived records disappear from every search by default, which is
    #: exactly right for a lifted block — it stays on the record for audit but
    #: no longer participates in availability.
    active = fields.Boolean(default=True)

    requested_by_id = fields.Many2one(
        'res.users', string='Requested By', default=lambda self: self.env.user,
        tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    approved_on = fields.Datetime(string='Approved On', readonly=True, copy=False)
    lifted_by_id = fields.Many2one('res.users', string='Lifted By', readonly=True, copy=False)
    lifted_on = fields.Datetime(string='Lifted On', readonly=True, copy=False)
    lift_reason = fields.Char(string='Lift Reason')

    @api.depends('property_id', 'reason')
    def _compute_name(self):
        labels = dict(self._fields['reason'].selection)
        for rec in self:
            rec.name = '%s — %s' % (
                rec.property_id.display_name or _('Unit'),
                labels.get(rec.reason, _('Block')))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_(
                    "Block on '%s' ends before it starts."
                ) % rec.property_id.display_name)

    @api.model_create_multi
    def create(self, vals_list):
        blocks = super().create(vals_list)
        blocks.mapped('property_id')._recompute_sale_availability()
        for block in blocks:
            block.property_id.message_post(body=_(
                "Commercially blocked: %s") % dict(
                    self._fields['reason'].selection).get(block.reason, ''))
        return blocks

    def write(self, vals):
        before = self.mapped('property_id')
        res = super().write(vals)
        (before | self.mapped('property_id'))._recompute_sale_availability()
        return res

    def unlink(self):
        """Blocks are archived, never deleted.

        A deleted block is a decision that cannot be explained afterwards, and
        cheque and contract disputes turn on exactly this kind of record.
        """
        raise UserError(_(
            "Commercial blocks cannot be deleted. Lift the block instead — it "
            "stays on the unit's history and stops affecting availability."))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_lift(self, reason=None):
        for rec in self:
            if not rec.active:
                raise UserError(_("This block has already been lifted."))
            rec.write({
                'active': False,
                'lifted_by_id': self.env.user.id,
                'lifted_on': fields.Datetime.now(),
                'lift_reason': reason or rec.lift_reason,
            })
            rec.property_id.message_post(body=_("Commercial block lifted."))

    def action_approve(self):
        for rec in self:
            rec.write({
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    def _is_in_force_on(self, date):
        self.ensure_one()
        if not self.active:
            return False
        if self.date_start and date < self.date_start:
            return False
        if self.date_end and date > self.date_end:
            return False
        return True

    @api.model
    def _cron_refresh_block_windows(self):
        """Sweep units whose block window opened or closed today.

        Same reasoning as the release cron: a stored availability flag does not
        change just because the date did.
        """
        today = fields.Date.context_today(self)
        blocks = self.search([
            '|', ('date_start', '<=', today), ('date_end', '<=', today),
        ])
        properties = blocks.mapped('property_id')
        if properties:
            properties._recompute_sale_availability()
        return len(properties)
