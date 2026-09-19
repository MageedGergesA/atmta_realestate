# -*- coding: utf-8 -*-
"""M9 — presentation attempts.

0.1 assumed a cheque goes to the bank once: `check.deposit_id` is a single
`Many2one`, so re-presenting a bounced cheque either overwrote the record of
the first attempt or was impossible. Both are wrong. A cheque that bounces on
insufficient funds in March and clears on the second presentation in April has
*two* bank events, and the March one does not stop having happened.

An attempt is the join between a cheque and one trip to the bank. It carries
its own outcome, so the cheque's `state` can stay a simple summary while the
history stays complete.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .check_states import PRESENTATION_OPEN, PRESENTATION_STATE


class CheckPresentation(models.Model):
    _name = 'realestate.check.presentation'
    _description = 'Cheque Presentation Attempt'
    _order = 'check_id, attempt'
    _check_company_auto = True
    _rec_name = 'display_reference'

    check_id = fields.Many2one(
        'realestate.check', string='Cheque', required=True, index=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        related='check_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='check_id.currency_id', store=True, readonly=True)
    partner_id = fields.Many2one(
        related='check_id.partner_id', store=True, readonly=True,
        string='Drawer')

    attempt = fields.Integer(
        required=True, default=1, readonly=True,
        help="1 for the first trip to the bank, 2 for the re-presentation "
             "after a bounce, and so on.")
    display_reference = fields.Char(
        compute='_compute_display_reference', store=True)

    deposit_id = fields.Many2one(
        'realestate.check.deposit', string='Deposit Slip', index=True,
        ondelete='set null', check_company=True)
    journal_id = fields.Many2one(
        'account.journal', string='Bank Journal', check_company=True)
    presented_date = fields.Date(
        required=True, default=fields.Date.context_today, index=True)
    amount = fields.Monetary(required=True)

    state = fields.Selection(
        PRESENTATION_STATE, default='presented', required=True, index=True)
    cleared_date = fields.Date(readonly=True)
    bounced_date = fields.Date(readonly=True)
    bounce_id = fields.Many2one(
        'realestate.check.bounce', string='Bounce', readonly=True,
        ondelete='set null')
    payment_id = fields.Many2one(
        'account.payment', string='Payment', readonly=True, check_company=True,
        help="The accounting payment raised for this particular attempt. A "
             "re-presentation raises its own, because the first one was "
             "reversed when the cheque bounced.")
    note = fields.Char()

    _sql_constraints = [
        ('presentation_attempt_uniq', 'unique(check_id, attempt)',
         'A cheque cannot have two presentation attempts with the same number.'),
        ('presentation_amount_positive', 'CHECK (amount > 0)',
         'A presentation must be for a positive amount.'),
    ]

    @api.depends('check_id.name', 'attempt')
    def _compute_display_reference(self):
        for rec in self:
            rec.display_reference = '%s / #%s' % (
                rec.check_id.name or '?', rec.attempt)

    @api.constrains('check_id', 'state')
    def _check_single_open_attempt(self):
        """A cheque is at one bank at a time."""
        for rec in self:
            if rec.state not in PRESENTATION_OPEN:
                continue
            others = self.search([
                ('check_id', '=', rec.check_id.id),
                ('id', '!=', rec.id),
                ('state', 'in', list(PRESENTATION_OPEN)),
            ], limit=1)
            if others:
                raise ValidationError(_(
                    "Cheque %(check)s already has an open presentation "
                    "(attempt %(attempt)s). A cheque can only be at one bank "
                    "at a time — resolve that attempt first.",
                    check=rec.check_id.name, attempt=others.attempt))

    def unlink(self):
        settled = self.filtered(lambda p: p.state in ('cleared', 'bounced'))
        if settled:
            raise UserError(_(
                "Presentation attempts that reached the bank cannot be "
                "deleted: %s."
            ) % ', '.join(settled.mapped('display_reference')))
        return super().unlink()

    @api.model
    def open_attempt(self, check, deposit=None, journal=None, date=None,
                     amount=None):
        """Start a new attempt for a cheque. The only way one is created.

        The attempt number is derived from what already exists rather than
        passed in, so a re-presentation cannot accidentally reuse a number and
        overwrite history.
        """
        check.ensure_one()
        last = self.search([('check_id', '=', check.id)],
                           order='attempt desc', limit=1)
        return self.create({
            'check_id': check.id,
            'attempt': (last.attempt if last else 0) + 1,
            'deposit_id': deposit.id if deposit else False,
            'journal_id': (journal or (deposit.journal_id if deposit else False)
                           or check.journal_id).id or False,
            'presented_date': date or fields.Date.context_today(check),
            'amount': amount if amount is not None else check.amount,
            'state': 'presented',
        })
