# -*- coding: utf-8 -*-
"""Phase 3 — the unit release engine.

A property can exist physically, be finished, and still not be sellable,
because the developer has not put it on the market. Today the module has no
such concept: a unit is sellable the moment ``commercial_status = 'available'``,
which conflates "nobody has committed to it" with "we are offering it".

A release batch is the record of a commercial decision — *these* units, from
*this* date, under *these* terms. It is also the audit trail for that decision,
which is why releasing is a state transition on a document rather than a flag
someone flips on a unit.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .commercial_states import RELEASE_STATE, RELEASE_STATE_LIVE


class UnitReleaseBatch(models.Model):
    _name = 'realestate.unit.release.batch'
    _description = 'Unit Release Batch'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'release_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), index='trigram',
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True,
        ondelete='restrict', tracking=True, index=True, check_company=True,
    )
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', ondelete='restrict', tracking=True,
        domain="[('project_id', '=', project_id)]", check_company=True,
        help="Optional. Leave empty for a release that spans phases.",
    )

    state = fields.Selection(
        RELEASE_STATE, default='draft', required=True, tracking=True,
        index=True, copy=False,
    )

    release_date = fields.Date(
        string='Release Date', default=fields.Date.context_today, tracking=True,
        help="The commercial decision date. Reporting only — the window below "
             "is what actually governs sellability.")
    valid_from = fields.Date(
        string='Valid From', tracking=True,
        help="First day these units are offered. Empty means "
             "'from the moment the batch is released'.")
    valid_until = fields.Date(
        string='Valid Until', tracking=True,
        help="Last day these units are offered. Empty means open-ended.")

    property_ids = fields.Many2many(
        'realestate.property', 'realestate_release_batch_property_rel',
        'batch_id', 'property_id', string='Units', check_company=True,
        domain="[('project_id', '=', project_id)]",
    )
    property_count = fields.Integer(compute='_compute_property_count', store=True)

    notes = fields.Html()

    # ---- Approval metadata ----
    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    approved_on = fields.Datetime(string='Approved On', readonly=True, copy=False)
    released_by_id = fields.Many2one('res.users', string='Released By', readonly=True, copy=False)
    released_on = fields.Datetime(string='Released On', readonly=True, copy=False)

    @api.depends('property_ids')
    def _compute_property_count(self):
        for rec in self:
            rec.property_count = len(rec.property_ids)

    @api.constrains('valid_from', 'valid_until')
    def _check_window(self):
        for rec in self:
            if rec.valid_from and rec.valid_until and rec.valid_until < rec.valid_from:
                raise ValidationError(_(
                    "Release '%s': the window ends before it starts."
                ) % rec.display_name)

    @api.constrains('phase_id', 'project_id')
    def _check_phase_belongs_to_project(self):
        for rec in self:
            if rec.phase_id and rec.phase_id.project_id != rec.project_id:
                raise ValidationError(_(
                    "Release '%s': phase '%s' does not belong to project '%s'."
                ) % (rec.display_name, rec.phase_id.display_name,
                     rec.project_id.display_name))

    @api.constrains('property_ids', 'project_id', 'phase_id')
    def _check_properties_in_scope(self):
        """A release may only cover units that are actually in its scope.

        Enforced as a constraint, not only as a view domain: 2D/3D and the API
        write these records too, and a view domain protects neither.
        """
        for rec in self:
            wrong_project = rec.property_ids.filtered(
                lambda p: p.project_id != rec.project_id)
            if wrong_project:
                raise ValidationError(_(
                    "Release '%s' includes units from another project: %s"
                ) % (rec.display_name,
                     ', '.join(wrong_project[:5].mapped('display_name'))))
            if rec.phase_id:
                wrong_phase = rec.property_ids.filtered(
                    lambda p: p.phase_id and p.phase_id != rec.phase_id)
                if wrong_phase:
                    raise ValidationError(_(
                        "Release '%s' is scoped to phase '%s' but includes "
                        "units from another phase: %s"
                    ) % (rec.display_name, rec.phase_id.display_name,
                         ', '.join(wrong_phase[:5].mapped('display_name'))))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.unit.release.batch') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Editability
    # ------------------------------------------------------------------
    def write(self, vals):
        """A released batch's *scope* is frozen.

        Once units are on the market, silently adding or removing them changes
        what is for sale with no record of the decision. Closing or cancelling
        the batch is the supported way to withdraw inventory, and both leave a
        trace.
        """
        frozen = {'property_ids', 'project_id', 'phase_id', 'valid_from'}
        if frozen & set(vals):
            locked = self.filtered(lambda r: r.state in ('released', 'closed'))
            if locked:
                raise UserError(_(
                    "Release %s is already %s; its scope can no longer be "
                    "changed. Close it and issue a new release instead."
                ) % (locked[0].display_name,
                     dict(self._fields['state'].selection)[locked[0].state]))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_approve(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft releases can be approved."))
            if not rec.property_ids:
                raise UserError(_(
                    "Release '%s' has no units. There is nothing to release."
                ) % rec.display_name)
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    def action_release(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_(
                    "Release '%s' must be approved before it can go live."
                ) % rec.display_name)
            rec.write({
                'state': RELEASE_STATE_LIVE,
                'released_by_id': self.env.user.id,
                'released_on': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                "Released %s unit(s) for sale.") % len(rec.property_ids))

    def action_close(self):
        """Withdraw the inventory from the market, keeping the record."""
        for rec in self:
            if rec.state != RELEASE_STATE_LIVE:
                raise UserError(_("Only live releases can be closed."))
            rec.state = 'closed'

    def action_cancel(self):
        for rec in self:
            if rec.state == RELEASE_STATE_LIVE:
                raise UserError(_(
                    "Release '%s' is live. Close it rather than cancelling, so "
                    "the record of what was on the market is preserved."
                ) % rec.display_name)
            rec.state = 'cancelled'

    def action_reset_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'approved'):
                raise UserError(_(
                    "Only cancelled or approved releases can go back to draft."))
            rec.write({
                'state': 'draft',
                'approved_by_id': False,
                'approved_on': False,
            })

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Released Units'),
            'res_model': 'realestate.property',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.property_ids.ids)],
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------
    def _is_live_on(self, date):
        """True when this batch actually offers its units on ``date``."""
        self.ensure_one()
        if self.state != RELEASE_STATE_LIVE:
            return False
        if self.valid_from and date < self.valid_from:
            return False
        if self.valid_until and date > self.valid_until:
            return False
        return True

    @api.model
    def _cron_refresh_release_windows(self):
        """Re-evaluate units whose release window opened or closed.

        ``is_released_for_sale`` is stored, because it is searched and grouped
        on constantly. Stored computed fields do not recompute merely because
        the clock moved, so the boundary days have to be swept explicitly.

        Deliberately narrow: only batches whose window boundary is at or around
        today are touched, not the whole table.
        """
        today = fields.Date.context_today(self)
        batches = self.search([
            ('state', '=', RELEASE_STATE_LIVE),
            '|', ('valid_from', '<=', today), ('valid_until', '<=', today),
        ])
        properties = batches.mapped('property_ids')
        if properties:
            properties._recompute_sale_availability()
        return len(properties)
