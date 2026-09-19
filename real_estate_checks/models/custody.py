# -*- coding: utf-8 -*-
"""M4 — physical custody.

A PDC portfolio is a stack of paper worth tens of millions sitting in a drawer.
Who is holding it, and where, is an audit question, and the answer has to be a
history rather than a field someone can type over.

So `check.custodian_id` and `check.location_id` are `readonly=True` mirrors of
the last movement recorded here. There is no way to change them except by
recording a movement, and movements are never edited or deleted.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .check_states import CUSTODY_REASON


class CheckCustody(models.Model):
    _name = 'realestate.check.custody'
    _description = 'Cheque Custody Movement'
    _order = 'date desc, id desc'
    _check_company_auto = True

    check_id = fields.Many2one(
        'realestate.check', string='Cheque', required=True, index=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        related='check_id.company_id', store=True, index=True, readonly=True)

    from_custodian_id = fields.Many2one('res.users', string='From Custodian')
    to_custodian_id = fields.Many2one('res.users', string='To Custodian')
    from_location_id = fields.Many2one(
        'realestate.check.location', string='From Location', check_company=True)
    to_location_id = fields.Many2one(
        'realestate.check.location', string='To Location', check_company=True)

    handed_over_by_id = fields.Many2one(
        'res.users', string='Handed Over By', required=True,
        default=lambda self: self.env.user,
        help="Who performed the handover. Recorded separately from the "
             "custodians so a manager moving paper on someone's behalf is "
             "visible.")
    received_by_id = fields.Many2one('res.users', string='Received By')

    date = fields.Datetime(
        required=True, default=fields.Datetime.now, index=True)
    reason = fields.Selection(CUSTODY_REASON, required=True, default='transfer')
    note = fields.Char()

    # Denormalised for the register views — a custody log is read far more
    # often than it is written, and joining to the cheque for every row on a
    # 100,000-row report is exactly the pattern M33 forbids.
    partner_id = fields.Many2one(
        related='check_id.partner_id', store=True, readonly=True,
        string='Drawer')
    amount = fields.Monetary(
        related='check_id.amount', store=True, readonly=True)
    currency_id = fields.Many2one(
        related='check_id.currency_id', store=True, readonly=True)

    @api.constrains('from_location_id', 'to_location_id', 'check_id')
    def _check_movement_is_a_movement(self):
        for rec in self:
            if rec.reason in ('receipt', 'opening'):
                continue
            if (rec.from_location_id == rec.to_location_id
                    and rec.from_custodian_id == rec.to_custodian_id):
                raise ValidationError(_(
                    "This movement does not move anything: same custodian, "
                    "same location."))

    @api.model_create_multi
    def create(self, vals_list):
        movements = super().create(vals_list)
        movements._apply_to_check()
        return movements

    def write(self, vals):
        """History is append-only.

        Only the free-text note may be corrected. Anything else would rewrite
        a chain-of-custody record after the fact, which is the one thing a
        chain of custody must not permit.
        """
        allowed = {'note', 'received_by_id'}
        forbidden = set(vals) - allowed
        if forbidden:
            raise UserError(_(
                "A custody movement is a historical record and cannot be "
                "edited (%s). Record a new movement instead."
            ) % ', '.join(sorted(forbidden)))
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            "Custody movements cannot be deleted. They are the audit trail for "
            "physical instruments."))

    def _apply_to_check(self):
        """Push the latest movement onto the cheque's mirror fields."""
        for check in self.mapped('check_id'):
            latest = self.env['realestate.check.custody'].search(
                [('check_id', '=', check.id)], order='date desc, id desc',
                limit=1)
            if not latest:
                continue
            check.with_context(skip_custody_guard=True).write({
                'custodian_id': latest.to_custodian_id.id or False,
                'location_id': latest.to_location_id.id or False,
            })

    @api.model
    def transfer(self, checks, to_custodian=None, to_location=None,
                 reason='transfer', note=None, date=None):
        """The one way custody changes. Used by the wizard, the deposit and the
        bounce return, so every route leaves the same trail.

        `None` means "leave this as it is"; `False` means "there is no longer
        one" — a cheque at the bank or handed back to its drawer has no
        custodian, and conflating that with "unchanged" produced a movement
        that moved nothing.
        """
        if not checks:
            return self.browse()
        vals_list = []
        for check in checks:
            if to_custodian is None:
                new_custodian = check.custodian_id.id or False
            else:
                new_custodian = to_custodian.id if to_custodian else False
            if to_location is None:
                new_location = check.location_id.id or False
            else:
                new_location = to_location.id if to_location else False
            vals_list.append({
                'check_id': check.id,
                'from_custodian_id': check.custodian_id.id or False,
                'from_location_id': check.location_id.id or False,
                'to_custodian_id': new_custodian,
                'to_location_id': new_location,
                'received_by_id': new_custodian,
                'reason': reason,
                'note': note,
                'date': date or fields.Datetime.now(),
            })
        return self.sudo().create(vals_list)
