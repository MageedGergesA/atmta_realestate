"""Availability engine (Phase 4).

Availability used to be a *manual* value baked into ``state``: somebody had to
remember to flip a unit back to ``available`` when a lease ended, and several
modules flipped it on each other's behalf. That is not a source of truth, it is
a hope.

Here availability is **derived** from the records that actually determine it:

* blocking lease allocations (today and in the future)
* maintenance / facilities blocks
* the commercial pipeline (sold, reserved, unreleased, blocked)
* construction readiness
* archival (``active``)
* whether the record is a leasable leaf at all
* an explicit, audited manager override

``next_available_date`` -- previously a free-text date somebody typed -- is now
computed from the last blocking allocation.

The override exists because leasing is a human business and operations
sometimes has to say "I know, do it anyway". It is deliberately expensive to
use: it records who, when and why, and it posts to the chatter.
"""

import logging
from datetime import timedelta

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .property_status import CONSTRUCTION_NOT_READY

_logger = logging.getLogger(__name__)

OVERRIDE_MODES = [
    ('none', 'No Override'),
    ('force_available', 'Force Available'),
    ('force_unavailable', 'Force Unavailable'),
]

#: Commercial statuses that stop a unit being leased.
COMMERCIAL_BLOCKERS = ('unreleased', 'sold', 'blocked')


class PropertyAvailability(models.Model):
    _inherit = 'realestate.property'

    # ------------------------------------------------------------------
    # Manual availability window
    # ------------------------------------------------------------------
    available_from = fields.Date(
        string='Available From', tracking=True, index=True,
        help="Earliest date this property may be leased. Leave empty for "
             "'immediately'.",
    )
    available_until = fields.Date(
        string='Available Until', tracking=True,
        help="Latest date this property may be leased -- e.g. a unit being "
             "withdrawn from the rental pool at year end.",
    )

    # ------------------------------------------------------------------
    # Derived availability
    # ------------------------------------------------------------------
    next_available_date = fields.Date(
        string='Next Available Date',
        compute='_compute_availability', store=True, index=True, readonly=True,
        help="Day after the last blocking lease allocation ends. Empty when "
             "the property is available now, or when a blocking allocation is "
             "open-ended.",
    )
    is_available_for_lease = fields.Boolean(
        string='Available to Lease',
        compute='_compute_availability', store=True, index=True, readonly=True,
    )
    availability_reason = fields.Char(
        string='Availability Reason',
        compute='_compute_availability', store=True, readonly=True,
        help="Why the property is (not) available. Always populated so nobody "
             "has to guess.",
    )
    blocking_contract_id = fields.Many2one(
        'realestate.contract', string='Blocking Lease',
        compute='_compute_availability', store=True, readonly=True,
    )

    # ------------------------------------------------------------------
    # Audited override
    # ------------------------------------------------------------------
    availability_override = fields.Selection(
        OVERRIDE_MODES, string='Availability Override', default='none',
        required=True, tracking=True, copy=False,
    )
    availability_override_reason = fields.Text(
        string='Override Reason', tracking=True, copy=False,
    )
    availability_override_uid = fields.Many2one(
        'res.users', string='Override By', readonly=True, copy=False,
    )
    availability_override_date = fields.Datetime(
        string='Override On', readonly=True, copy=False,
    )

    # ==================================================================
    # Compute
    # ==================================================================
    @api.depends(
        'active', 'is_leasable', 'commercial_status', 'maintenance_status',
        'construction_status', 'available_from', 'available_until',
        'availability_override',
        'property_line_ids.is_blocking', 'property_line_ids.end_date',
        'property_line_ids.start_date', 'property_line_ids.contract_id',
    )
    def _compute_availability(self):
        """One pass, no per-record searches (Phase 32).

        Blocking allocations for the whole recordset are fetched in a single
        query and bucketed in Python.
        """
        today = fields.Date.context_today(self)
        blockers = self._fetch_blocking_allocations(today)

        for rec in self:
            reason, blocking = rec._availability_verdict(today, blockers.get(rec.id, []))
            rec.blocking_contract_id = blocking.contract_id.id if blocking else False
            rec.next_available_date = rec._next_free_date(blockers.get(rec.id, []))

            if rec.availability_override == 'force_available':
                rec.is_available_for_lease = True
                rec.availability_reason = _("Forced available by override. %s") % (
                    rec.availability_override_reason or '')
            elif rec.availability_override == 'force_unavailable':
                rec.is_available_for_lease = False
                rec.availability_reason = _("Forced unavailable by override. %s") % (
                    rec.availability_override_reason or '')
            else:
                rec.is_available_for_lease = not reason
                rec.availability_reason = reason or _("Available")

    def _fetch_blocking_allocations(self, today):
        """Return ``{property_id: [allocation, ...]}`` for current+future holds."""
        if not self.ids:
            return {}
        # sudo(): whether a unit is free is a fact about the unit, not about
        # the reader. Someone who can see a property but not its leases must
        # still see it as unavailable -- otherwise they would be shown a
        # vacant unit that is in fact let, and could double-book it.
        allocations = self.env['realestate.contract.property.line'].sudo().search([
            ('property_id', 'in', self.ids),
            ('is_blocking', '=', True),
            '|', ('end_date', '=', False), ('end_date', '>=', today),
        ], order='start_date')
        grouped = {}
        for alloc in allocations:
            grouped.setdefault(alloc.property_id.id, []).append(alloc)
        return grouped

    def _availability_verdict(self, today, allocations):
        """Return ``(reason_or_None, blocking_allocation_or_None)``.

        Ordered by how hard the blocker is, so the message names the most
        fundamental reason rather than an incidental one.
        """
        self.ensure_one()
        if not self.active:
            return _("Property is archived."), None
        if not self.is_leasable:
            return _("Not a leasable unit (%s).") % (
                dict(self._fields['hierarchy_level'].selection).get(
                    self.hierarchy_level, self.hierarchy_level)), None
        if self.commercial_status in COMMERCIAL_BLOCKERS:
            return _("Commercial status is '%s'.") % (
                dict(self._fields['commercial_status'].selection).get(
                    self.commercial_status)), None
        if self.maintenance_status != 'normal':
            return _("Maintenance status is '%s'.") % (
                dict(self._fields['maintenance_status'].selection).get(
                    self.maintenance_status)), None
        if self._construction_blocks_leasing():
            return _("Construction status is '%s'.") % (
                dict(self._fields['construction_status'].selection).get(
                    self.construction_status)), None
        if self.available_from and self.available_from > today:
            return _("Not released for lease until %s.") % self.available_from, None
        if self.available_until and self.available_until < today:
            return _("Withdrawn from the rental pool since %s.") % self.available_until, None

        current = [a for a in allocations
                   if a.start_date <= today and (not a.end_date or a.end_date >= today)]
        if current:
            alloc = current[0]
            return _("Leased to %(tenant)s until %(end)s.",
                     tenant=alloc.contract_id.partner_id.display_name or _('a tenant'),
                     end=alloc.end_date or _('further notice')), alloc
        return None, None

    def _construction_blocks_leasing(self):
        """Whether construction readiness should stop this unit being let.

        Only meaningful for units that are part of a development. When
        ``real_estate_developer`` is installed it computes
        ``construction_status`` from the phase/project state and falls back to
        ``'planning'`` -- so a standalone rental unit that was never part of a
        project would otherwise be permanently unavailable, which is wrong: an
        existing building the company simply owns and lets has no construction
        phase and needs none.

        So construction only blocks when the property is genuinely attached to a
        project or phase.
        """
        self.ensure_one()
        if self.construction_status not in CONSTRUCTION_NOT_READY:
            return False
        in_development = any(
            field in self._fields and self[field]
            for field in ('project_id', 'phase_id')
        )
        return bool(in_development)

    def _next_free_date(self, allocations):
        """Day after the last blocking allocation ends.

        ``False`` when nothing blocks, or when a blocking allocation is
        open-ended (in which case there is no knowable free date -- we do not
        invent one).
        """
        self.ensure_one()
        if not allocations:
            return False
        if any(not a.end_date for a in allocations):
            return False
        last_end = max(a.end_date for a in allocations)
        return last_end + timedelta(days=1)

    # ==================================================================
    # Override control
    # ==================================================================
    def write(self, vals):
        """Stamp and authorise availability overrides."""
        if 'availability_override' in vals or 'availability_override_reason' in vals:
            self._assert_can_override()
            if vals.get('availability_override', 'none') != 'none':
                if not (vals.get('availability_override_reason')
                        or all(r.availability_override_reason for r in self)):
                    raise UserError(_(
                        "An availability override requires a written reason."))
                vals = dict(
                    vals,
                    availability_override_uid=self.env.user.id,
                    availability_override_date=fields.Datetime.now(),
                )
        res = super().write(vals)
        if 'availability_override' in vals:
            self._log_override()
        return res

    def _assert_can_override(self):
        """Overriding availability is a management decision, not a data edit."""
        if self.env.su or self.env.user.has_group(
                'atmta_real_estate.group_property_manager'):
            return
        raise AccessError(_(
            "Overriding property availability requires the Property Manager "
            "permission."))

    def _log_override(self):
        labels = dict(OVERRIDE_MODES)
        for rec in self:
            rec.message_post(body=Markup(_(
                "Availability override set to <b>%(mode)s</b> by %(user)s.<br/>"
                "Reason: %(reason)s")) % {
                    'mode': labels.get(rec.availability_override, rec.availability_override),
                    'user': self.env.user.display_name,
                    'reason': rec.availability_override_reason or _('(none given)'),
                })

    def action_clear_availability_override(self):
        self._assert_can_override()
        return self.write({
            'availability_override': 'none',
            'availability_override_reason': False,
            'availability_override_uid': False,
            'availability_override_date': False,
        })

    def action_new_lease(self):
        """Open a draft lease for this unit, with the unit and company filled in."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Lease'),
            'res_model': 'realestate.contract',
            'views': [(False, 'form')],
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_property_id': self.id,
                'default_company_id': (self.company_id or self.env.company).id,
                'default_is_single_property': True,
                'default_is_multi_property': False,
            },
        }

    @api.constrains('available_from', 'available_until')
    def _check_availability_window(self):
        for rec in self:
            if (rec.available_from and rec.available_until
                    and rec.available_from > rec.available_until):
                raise ValidationError(_(
                    "'Available From' must be on or before 'Available Until' "
                    "on %s.", rec.display_name))

    # ==================================================================
    # Scheduled recomputation
    # ==================================================================
    @api.model
    def _cron_recompute_availability(self, batch_size=None):
        """Entry point of the daily "Rental: refresh date-based status" job.

        Availability is one of several stored values that change when a date
        merely passes; ``realestate.calendar.refresh`` refreshes all of them.
        The method keeps its name because the scheduled action, which installed
        databases hold as ``noupdate`` data, calls it.
        """
        return self.env['realestate.calendar.refresh']._cron_refresh()
