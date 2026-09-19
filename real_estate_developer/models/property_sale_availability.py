# -*- coding: utf-8 -*-
"""Phase 3 — ``is_available_for_sale``, the authoritative sale-eligibility answer.

Before this, "can I sell this unit?" was answered by
``commercial_status == 'available'``, which only says that nobody has committed
to it yet. It says nothing about whether the developer has put it on the
market, whether it is blocked, or whether its project is even selling.

This module now answers the question once, in one place, and everything —
the reservation engine, the inventory matrix, the public API, 2D/3D — reads
that answer instead of re-deriving its own. A stale second opinion about
whether a unit is for sale is how a unit gets sold twice.

Every negative answer also carries a *reason*, because "not available" with no
explanation generates a support ticket every time.
"""

from odoo import _, api, fields, models

from .commercial_states import (
    COMMERCIAL_STATE_SELLING,
    COMMITTED_COMMERCIAL_STATUS,
    RELEASE_STATE_LIVE,
    UNAVAILABLE_REASON,
)


class PropertySaleAvailability(models.Model):
    _inherit = 'realestate.property'

    release_batch_ids = fields.Many2many(
        'realestate.unit.release.batch', 'realestate_release_batch_property_rel',
        'property_id', 'batch_id', string='Release Batches',
    )
    block_ids = fields.One2many(
        'realestate.unit.block', 'property_id', string='Commercial Blocks')

    is_released_for_sale = fields.Boolean(
        string='Released for Sale', compute='_compute_sale_availability',
        store=True, index=True,
        help="True when a live release batch currently offers this unit.",
    )
    active_block_id = fields.Many2one(
        'realestate.unit.block', string='Active Block',
        compute='_compute_sale_availability', store=True,
    )
    is_available_for_sale = fields.Boolean(
        string='Available for Sale', compute='_compute_sale_availability',
        store=True, index=True,
        help="Authoritative answer to 'can a buyer take this unit today?'. "
             "Combines release, commercial commitment, blocks, maintenance and "
             "the project/phase sales status.",
    )
    sale_unavailable_reason = fields.Selection(
        UNAVAILABLE_REASON, string='Why Not Available',
        compute='_compute_sale_availability', store=True,
    )

    # ------------------------------------------------------------------
    # The engine
    # ------------------------------------------------------------------
    @api.depends(
        'active', 'hierarchy_level', 'commercial_status', 'maintenance_status',
        'project_id', 'project_id.commercial_state',
        'phase_id', 'phase_id.commercial_state',
        'release_batch_ids', 'release_batch_ids.state',
        'release_batch_ids.valid_from', 'release_batch_ids.valid_until',
        'block_ids', 'block_ids.active',
        'block_ids.date_start', 'block_ids.date_end',
    )
    def _compute_sale_availability(self):
        today = fields.Date.context_today(self)
        for rec in self:
            live_batch = rec.release_batch_ids.filtered(
                lambda b: b.state == RELEASE_STATE_LIVE and b._is_live_on(today))
            block = rec.block_ids.filtered(lambda b: b._is_in_force_on(today))[:1]

            rec.is_released_for_sale = bool(live_batch)
            rec.active_block_id = block
            reason = rec._sale_unavailable_reason(today, live_batch, block)
            rec.sale_unavailable_reason = reason
            rec.is_available_for_sale = not reason

    def _sale_unavailable_reason(self, today, live_batch, block):
        """Return the first reason this unit cannot be sold, or ``False``.

        Ordered from the most structural to the most transient, so the reason
        shown is the one a user can actually do something about.
        """
        self.ensure_one()
        if not self.active:
            return 'archived'
        # Only leaf units are sold. Selling a whole floor or building is a
        # different transaction and is not what this engine models.
        if self.hierarchy_level != 'unit':
            return 'not_a_unit'
        if not self.project_id:
            return 'no_project'
        if self.project_id.commercial_state not in COMMERCIAL_STATE_SELLING:
            return 'project_not_selling'
        if (self.phase_id
                and self.phase_id.commercial_state not in COMMERCIAL_STATE_SELLING):
            return 'phase_not_selling'
        if not live_batch:
            # Distinguish "never released" from "released, but the window has
            # closed" — they call for different actions.
            has_release = bool(self.release_batch_ids.filtered(
                lambda b: b.state in (RELEASE_STATE_LIVE, 'closed')))
            return 'release_window' if has_release else 'not_released'
        if block:
            return 'blocked'
        if self.maintenance_status in ('maintenance', 'blocked'):
            return 'maintenance'
        if self.commercial_status in COMMITTED_COMMERCIAL_STATUS:
            return 'committed'
        return False

    def _recompute_sale_availability(self):
        """Force a recompute after a change the ORM cannot see.

        Needed for time-based transitions (a release window opening, a block
        expiring), which no write triggers.
        """
        self.invalidate_recordset([
            'is_released_for_sale', 'active_block_id',
            'is_available_for_sale', 'sale_unavailable_reason',
        ])
        self.modified([
            'release_batch_ids', 'block_ids', 'commercial_status',
        ])
        return True

    # ------------------------------------------------------------------
    # Guards used by the sales workflow
    # ------------------------------------------------------------------
    def _check_available_for_sale(self):
        """Raise a specific, actionable error for the first blocked unit.

        This is the developer-sales counterpart to Module 1's
        ``_check_available_for_new_sale()``, which only knows about the legacy
        ``sold``/``maintenance``/``inactive`` states. Module 1 is frozen and is
        also used by Brokerage, so the richer check lives here rather than
        being pushed into it.
        """
        from odoo.exceptions import ValidationError
        messages = {
            'archived': _("Unit '%s' is archived."),
            'not_a_unit': _("'%s' is not a sellable unit."),
            'no_project': _("Unit '%s' is not assigned to a project."),
            'project_not_selling': _("Project of unit '%s' is not open for sales."),
            'phase_not_selling': _("Phase of unit '%s' is not open for sales."),
            'not_released': _("Unit '%s' has not been released for sale."),
            'release_window': _("Unit '%s' is outside its release window."),
            'blocked': _("Unit '%s' is commercially blocked."),
            'maintenance': _("Unit '%s' is under maintenance."),
            'committed': _("Unit '%s' is already held, reserved or sold."),
        }
        for rec in self:
            if rec.is_available_for_sale:
                continue
            template = messages.get(
                rec.sale_unavailable_reason, _("Unit '%s' is not available for sale."))
            raise ValidationError(template % rec.display_name)

    def action_view_blocks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Commercial Blocks'),
            'res_model': 'realestate.unit.block',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {
                'default_property_id': self.id,
                'active_test': False,
            },
            'target': 'current',
        }
