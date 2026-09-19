# -*- coding: utf-8 -*-
"""M13 — replacing a cheque, with a lineage that survives.

0.1 had `replacement_check_id` and `replaces_check_id` as `readonly` fields
that nothing ever wrote, and a `replaced` state nothing ever reached. The whole
feature was declared and absent.

The chain this builds is meant to be reconstructible years later:

    CHK-A  bounced   → replaced by
    CHK-B  bounced   → replaced by
    CHK-C  cleared

Every link is explicit, `root_check_id` points all three at CHK-A, and the
allocations move forward with the paper so the obligation stays covered exactly
once.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.check_states import CHECK_ON_HAND


class ReplaceCheckWizard(models.TransientModel):
    _name = 'realestate.check.replace.wizard'
    _description = 'Replace a Cheque'

    original_check_id = fields.Many2one(
        'realestate.check', string='Cheque Being Replaced', required=True,
        ondelete='cascade')
    bounce_id = fields.Many2one(
        'realestate.check.bounce', string='Bounce', ondelete='cascade')
    company_id = fields.Many2one(
        related='original_check_id.company_id', readonly=True)
    partner_id = fields.Many2one(
        related='original_check_id.partner_id', readonly=True,
        string='Original Drawer')
    currency_id = fields.Many2one(
        related='original_check_id.currency_id', readonly=True)
    original_amount = fields.Monetary(
        related='original_check_id.amount', readonly=True)
    original_state = fields.Selection(
        related='original_check_id.state', readonly=True)

    # ---------- the new instrument ----------
    new_partner_id = fields.Many2one(
        'res.partner', string='Drawer', required=True,
        help="Usually the same buyer, but a replacement may legitimately be "
             "drawn by a guarantor or a company account.")
    check_number = fields.Char(string='New Cheque #', required=True)
    bank_id = fields.Many2one('res.bank', string='Drawer Bank', required=True)
    branch = fields.Char()
    account_number = fields.Char(string='Drawer Account')
    amount = fields.Monetary(required=True)
    issue_date = fields.Date(default=fields.Date.context_today, required=True)
    due_date = fields.Date(required=True)
    notes = fields.Text()

    carry_allocations = fields.Boolean(
        string='Carry Allocations Forward', default=True,
        help="Move the original's obligations onto the new cheque. Switch off "
             "only when the replacement covers something different — in which "
             "case allocate it by hand afterwards.")
    return_original = fields.Boolean(
        string='Return Original to Customer', default=False,
        help="Tick when the physical paper goes back to the drawer. Otherwise "
             "it is retained and marked replaced.")

    @api.onchange('original_check_id')
    def _onchange_original(self):
        for wiz in self:
            original = wiz.original_check_id
            if not original:
                continue
            wiz.new_partner_id = original.partner_id
            wiz.bank_id = original.bank_id
            wiz.branch = original.branch
            wiz.account_number = original.account_number
            # Default to the amount still genuinely owed through this
            # instrument, not blindly the face value: if part of the original
            # was somehow settled, replacing the whole thing would duplicate it.
            wiz.amount = original.allocated_amount or original.amount

    def action_replace(self):
        self.ensure_one()
        Check = self.env['realestate.check']
        Check._assert_group('real_estate_checks.group_checks_treasurer',
                            _('replace a cheque'))
        original = self.original_check_id

        # ---- M13's constraints ----
        if original.state == 'cleared':
            raise UserError(_(
                "Cheque %s has cleared. A cleared instrument is not replaced — "
                "the money arrived. If a correction is needed, that is a "
                "refund or a credit note in Accounting.") % original.name)
        if original.state == 'replaced':
            raise UserError(_(
                "Cheque %(name)s has already been replaced by %(other)s.",
                name=original.name,
                other=original.replacement_check_id.display_name))
        if original.state in ('cancelled', 'returned'):
            raise UserError(_(
                "Cheque %(name)s is %(state)s. Register a new cheque rather "
                "than a replacement — there is no live instrument to replace.",
                name=original.name, state=original.state))
        if original.state in ('deposited', 'in_clearing'):
            raise UserError(_(
                "Cheque %s is at the bank and its outcome is unknown. Wait for "
                "clearance or record the bounce before replacing it — "
                "otherwise the same obligation would be covered twice."
            ) % original.name)

        currency = original.currency_id
        allocations = original.allocation_ids.filtered(
            lambda a: a.state == 'active')
        allocated = sum(allocations.mapped('allocated_amount'))
        if self.carry_allocations and currency.compare_amounts(
                self.amount, allocated) < 0:
            raise UserError(_(
                "The replacement is for %(new)s but %(old)s of obligations "
                "would be carried onto it. Reduce what it carries, or raise "
                "the amount.",
                new=currency.format(self.amount),
                old=currency.format(allocated)))

        replacement = Check.create({
            'company_id': original.company_id.id,
            'partner_id': self.new_partner_id.id,
            'currency_id': currency.id,
            'check_number': self.check_number,
            'bank_id': self.bank_id.id,
            'branch': self.branch,
            'account_number': self.account_number,
            'amount': self.amount,
            'issue_date': self.issue_date,
            'received_date': fields.Date.context_today(self),
            'due_date': self.due_date,
            'sale_contract_id': original.sale_contract_id.id or False,
            'rental_contract_id': original.rental_contract_id.id or False,
            'journal_id': original.journal_id.id or False,
            'replaces_check_id': original.id,
            'notes': self.notes,
            'state': 'registered',
        })

        if self.carry_allocations and allocations:
            # Move, not copy — and cancel FIRST.
            #
            # Raising the new allocations before releasing the old ones makes
            # the obligation momentarily covered twice, which the obligation
            # -side invariant correctly refuses. Copying rather than moving is
            # exactly the trap M13 warns about, and doing it in the wrong order
            # produces the same double-cover by accident.
            carried = [{
                'check_id': replacement.id,
                'sale_installment_id': allocation.sale_installment_id.id or False,
                'rental_payment_id': allocation.rental_payment_id.id or False,
                'allocated_amount': allocation.allocated_amount,
                'state': 'active',
                'note': _('Carried from replaced cheque %s.') % original.name,
            } for allocation in allocations]
            allocations.action_cancel(
                reason=_('Replaced by cheque %s.') % replacement.name)
            self.env['realestate.check.allocation'].create(carried)

        original.write({
            'state': 'replaced',
            'replacement_check_id': replacement.id,
        })
        if self.return_original:
            self.env['realestate.check.custody'].transfer(
                original, to_custodian=False, to_location=False,
                reason='return_to_customer',
                note=_('Handed back on replacement by %s.') % replacement.name)

        if self.bounce_id:
            self.bounce_id.write({
                'resolution': 'replaced',
                'resolved_date': fields.Date.context_today(self),
                'replacement_check_id': replacement.id,
            })
        # Opened from the cheque form rather than the bounce, the wizard has no
        # `bounce_id`, and the bounce the replacement answers stayed pending.
        original._resolve_pending_bounces('replaced', replacement=replacement)

        original.message_post(body=_(
            "Replaced by cheque %(new)s (%(number)s), %(amount)s due %(due)s.",
            new=replacement.name, number=replacement.check_number,
            amount=currency.format(replacement.amount),
            due=replacement.due_date))
        replacement.message_post(body=_(
            "Replaces cheque %(old)s (%(number)s). Generation %(gen)s of the "
            "chain that began with %(root)s.",
            old=original.name, number=original.check_number,
            gen=replacement.replacement_generation,
            root=replacement.root_check_id.name))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Replacement Cheque'),
            'res_model': 'realestate.check',
            'res_id': replacement.id,
            'view_mode': 'form',
        }
