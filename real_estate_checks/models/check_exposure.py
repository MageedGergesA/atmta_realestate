# -*- coding: utf-8 -*-
"""M15 — one authoritative API for commercial changes.

Developer's contract-change workflows need to know what physical instruments
are in play before they move a schedule. In 0.1 they found out by running their
own `search` against Checks' internals:

```python
checks = self.env['realestate.check'].search(
    [('sale_installment_id', 'in', target.ids)])
settled_states = ('deposited', 'cleared', 'collected', 'endorsed')
```

which was wrong in four ways: it missed cheques linked to the contract rather
than to a specific instalment; it matched two states (`collected`, `endorsed`)
that have never existed; it treated a bounced cheque as harmless; and it was
never called at all from cancellation or buyer transfer.

Rather than patch Developer, Checks answers the question itself. Developer's
`_blocking_checks` / `_assert_no_blocking_checks` are **overridden here**, so
its existing call sites get the correct answer without changing, and the four
workflows that lacked a guard get one by override too. Developer stays exactly
as frozen as it was.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .check_states import (
    CHECK_BLOCKING_EXPOSURE,
    CHECK_ON_HAND,
    CHECK_SETTLED,
)


class SaleContractCheckExposure(models.Model):
    _inherit = 'realestate.sale.contract'

    # ==================================================================
    # The public API (M15)
    # ==================================================================
    def get_check_exposure(self, installments=None):
        """Everything a commercial workflow needs to decide safely.

        Returns a plain dict — not a recordset — so a caller can log it, show
        it in a wizard, or serialise it to an API without knowing anything
        about this module's models.

        `installments`: restrict to cheques allocated to those obligations.
        `None` means the whole contract, including cheques attached to the
        contract with no instalment named.
        """
        self.ensure_one()
        checks = self._contract_checks(installments)
        currency = self.currency_id or self.company_id.currency_id

        by_state = {}
        for check in checks:
            by_state.setdefault(check.state, self.env['realestate.check'])
            by_state[check.state] |= check

        def _summary(records):
            return {
                'count': len(records),
                'amount': currency.round(sum(records.mapped('amount'))),
                'checks': [{
                    'id': c.id,
                    'reference': c.name,
                    'check_number': c.check_number,
                    'bank': c.bank_id.display_name,
                    'due_date': c.due_date,
                    'amount': c.amount,
                    'state': c.state,
                    'accounting_state': c.accounting_state,
                    'allocated_amount': c.allocated_amount,
                    'installment_id': c.sale_installment_id.id or False,
                    'invoice_ids': c._invoices_to_settle().ids,
                    'payment_id': c.payment_id.id or False,
                } for c in records],
            }

        on_hand = checks.filtered(lambda c: c.state in CHECK_ON_HAND)
        at_bank = checks.filtered(
            lambda c: c.state in ('deposited', 'in_clearing'))
        bounced = checks.filtered(lambda c: c.state == 'bounced')
        cleared = checks.filtered(lambda c: c.state in CHECK_SETTLED)
        blocking = checks.filtered(lambda c: c.state in CHECK_BLOCKING_EXPOSURE)

        return {
            'contract': self.name,
            'currency_id': currency.id,
            'total_count': len(checks),
            'total_amount': currency.round(sum(checks.mapped('amount'))),
            # The five buckets a commercial decision actually turns on.
            'on_hand': _summary(on_hand),
            'at_bank': _summary(at_bank),
            'bounced': _summary(bounced),
            'cleared': _summary(cleared),
            'closed': _summary(checks.filtered(
                lambda c: c.state in ('cancelled', 'returned', 'replaced'))),
            # The single yes/no the workflows care about.
            'blocking_count': len(blocking),
            'blocking_amount': currency.round(sum(blocking.mapped('amount'))),
            'can_change_schedule': not blocking,
            'by_state': {state: {'count': len(recs),
                                 'amount': currency.round(
                                     sum(recs.mapped('amount')))}
                         for state, recs in by_state.items()},
        }

    def get_locked_allocations(self, installments=None):
        """Allocations that must not be moved.

        An allocation is locked once its cheque has left the building: the bank
        is processing it, or already processed it. Re-pointing such an
        allocation at a different obligation would change what a real payment
        settles, after the fact.
        """
        self.ensure_one()
        checks = self._contract_checks(installments)
        locked_checks = checks.filtered(
            lambda c: c.state in CHECK_BLOCKING_EXPOSURE + CHECK_SETTLED)
        return locked_checks.allocation_ids.filtered(
            lambda a: a.state == 'active')

    def assert_schedule_can_change(self, installments=None, operation=None):
        """Raise unless the schedule may be restructured underneath.

        The one gate every commercial change goes through.
        """
        self.ensure_one()
        blocking = self._blocking_checks(installments)
        if not blocking:
            return True
        currency = self.currency_id or self.company_id.currency_id
        lines = []
        for check in blocking[:10]:
            lines.append(_(
                "- %(ref)s no. %(number)s, %(amount)s, due %(due)s — "
                "%(state)s",
                ref=check.name, number=check.check_number,
                amount=currency.format(check.amount), due=check.due_date,
                state=dict(check._fields['state'].selection)[check.state]))
        more = (_("\n… and %s more.") % (len(blocking) - 10)
                if len(blocking) > 10 else '')
        raise UserError(_(
            "%(operation)s cannot proceed on contract %(contract)s: "
            "%(count)s cheque(s) totalling %(total)s are at the bank or "
            "unresolved after a bounce.\n\n%(list)s%(more)s\n\n"
            "Resolve them in Treasury first — wait for clearance, record the "
            "bounce, or authorise a replacement. Restructuring underneath a "
            "presented cheque would corrupt a financial instrument.",
            operation=operation or _("This change"), contract=self.name,
            count=len(blocking), total=currency.format(
                sum(blocking.mapped('amount'))),
            list='\n'.join(lines), more=more))

    def _contract_checks(self, installments=None):
        """Every cheque bearing on this contract.

        0.1's Developer-side search looked only at `sale_installment_id`, so a
        cheque attached to the contract itself — which the model has always
        permitted — was invisible to every guard. Both routes are searched, and
        so are the allocations, which is the only way to see a cheque split
        across several instalments.
        """
        self.ensure_one()
        Check = self.env['realestate.check']
        contract_level = Check.search([
            ('sale_contract_id', '=', self.id),
            ('allocation_ids', '=', False),
        ])
        if installments is None:
            return Check.search(['|',
                                 ('sale_contract_id', '=', self.id),
                                 ('sale_installment_id', 'in',
                                  self.installment_ids.ids)])

        # A cheque attached to the contract but allocated to nothing is
        # UNSCOPED exposure, so it bears on every instalment. Excluding it when
        # the caller narrows to a subset would reopen the exact hole 0.1's
        # Developer-side search had: a cheque nobody can say what it pays is
        # the most dangerous one to restructure underneath, not the least.
        if not installments:
            return contract_level
        allocations = self.env['realestate.check.allocation'].search([
            ('sale_installment_id', 'in', installments.ids),
            ('state', '=', 'active'),
        ])
        direct = Check.search([('sale_installment_id', 'in', installments.ids)])
        return direct | allocations.mapped('check_id') | contract_level

    # ==================================================================
    # Developer's late-bound hooks — answered properly
    # ==================================================================
    def _blocking_checks(self, installments=None):
        """Override of Developer's guard.

        Developer defines this so its workflows keep running when Checks is
        absent. When Checks *is* installed, this is the real implementation.

        Differences from Developer's placeholder, all of them corrections:

        * cheques linked at contract level are included;
        * `collected` and `endorsed` — two states that have never existed —
          are gone;
        * `in_clearing` is included, because it did not exist in 0.1;
        * **`bounced` is blocking.** A bounce leaves either a payment awaiting
          reversal or a receivable that has just been restored; either way the
          money position is in flux and a restructuring on top of it would be
          built on a number that is about to change.
        """
        self.ensure_one()
        checks = self._contract_checks(installments)
        return checks.filtered(lambda c: c.state in CHECK_BLOCKING_EXPOSURE)

    def _assert_no_blocking_checks(self, installments=None):
        """Override of Developer's assertion, with a usable message."""
        self.ensure_one()
        return self.assert_schedule_can_change(installments)

    # ==================================================================
    # The four workflows Developer never guarded (M15)
    # ==================================================================
    def _apply_cancellation(self, amendment, reason_note=None,
                            penalty_amount=0.0, forfeit_amount=0.0,
                            release_property=True, terminate=False):
        """Do not finish a cancellation while PDC exposure is unresolved.

        Cheques on hand must be handed back or cancelled; cheques at the bank
        must be resolved. Cleared cheques stay as historical payment — the
        money arrived and cancelling the contract does not un-arrive it.
        """
        self.ensure_one()
        self._assert_checks_resolved_for_close(_("Cancelling this contract"))
        return super()._apply_cancellation(
            amendment, reason_note=reason_note, penalty_amount=penalty_amount,
            forfeit_amount=forfeit_amount, release_property=release_property,
            terminate=terminate)

    def _apply_buyer_change(self, amendment):
        """M15 — never rewrite the drawer on a historical instrument.

        A cheque is drawn on a named person's account. Transferring the
        contract to a new buyer does not retro-actively make the new buyer the
        drawer of paper the old buyer signed, and cheques already at the bank
        will be honoured — or not — against the *old* buyer's account
        regardless of what this database says.

        So: cleared and presented cheques keep their drawer, untouched. Future
        cheques on hand are surfaced, because somebody has to physically
        collect replacements from the incoming buyer.
        """
        self.ensure_one()
        old_partner = self.partner_id
        exposure = self.get_check_exposure()
        if exposure['at_bank']['count'] or exposure['bounced']['count']:
            raise UserError(_(
                "Contract %(contract)s cannot be transferred to a new buyer "
                "while %(count)s cheque(s) drawn by %(buyer)s are at the bank "
                "or unresolved after a bounce.\n\n"
                "Those instruments will be honoured — or returned — against "
                "the current buyer's account whatever this record says. "
                "Resolve them first.",
                contract=self.name,
                count=exposure['at_bank']['count'] + exposure['bounced']['count'],
                buyer=old_partner.display_name))

        res = super()._apply_buyer_change(amendment)

        on_hand = self._contract_checks().filtered(
            lambda c: c.state in CHECK_ON_HAND)
        if on_hand:
            self.message_post(body=_(
                "%(count)s uncashed cheque(s) totalling %(total)s are still "
                "drawn by %(old)s and remain linked to this contract. Their "
                "drawer has deliberately NOT been rewritten — historical "
                "instrument ownership is a fact.\n\n"
                "Treasury must return them to %(old)s and collect replacement "
                "cheques from %(new)s:\n%(list)s",
                count=len(on_hand),
                total=(self.currency_id or self.company_id.currency_id).format(
                    sum(on_hand.mapped('amount'))),
                old=old_partner.display_name,
                new=self.partner_id.display_name,
                list='\n'.join('- %s (%s) due %s' % (
                    c.name, c.check_number, c.due_date) for c in on_hand[:20])))
        return res

    def _apply_unit_swap(self, amendment):
        """M15 — historical cleared cheques stay with their original obligation.

        Developer already re-cuts the open balance; what it must not do is
        re-point instruments that have already been honoured. Since cleared
        cheques keep their allocations and only open instalments are
        re-scheduled, that holds — but the blocking guard is asserted here as
        well so a presented cheque cannot slip through the swap path.
        """
        self.ensure_one()
        self.assert_schedule_can_change(
            self._open_installments(), operation=_("A unit swap"))
        return super()._apply_unit_swap(amendment)

    def _apply_settlement(self, amendment, discount_amount=0.0,
                          fee_amount=0.0, settlement_date=None):
        """M15 — settlement must state what happens to the paper."""
        self.ensure_one()
        self.assert_schedule_can_change(
            self._open_installments(), operation=_("An early settlement"))
        res = super()._apply_settlement(
            amendment, discount_amount=discount_amount, fee_amount=fee_amount,
            settlement_date=settlement_date)
        on_hand = self._contract_checks().filtered(
            lambda c: c.state in CHECK_ON_HAND)
        if on_hand:
            self.message_post(body=_(
                "%(count)s post-dated cheque(s) totalling %(total)s are still "
                "held against this contract and are now unallocated by the "
                "settlement. Return or cancel them before closing:\n%(list)s",
                count=len(on_hand),
                total=(self.currency_id or self.company_id.currency_id).format(
                    sum(on_hand.mapped('amount'))),
                list='\n'.join('- %s (%s) due %s' % (
                    c.name, c.check_number, c.due_date) for c in on_hand[:20])))
        return res

    def _assert_checks_resolved_for_close(self, operation):
        """Nothing may close over live physical exposure."""
        self.ensure_one()
        exposure = self.get_check_exposure()
        currency = self.currency_id or self.company_id.currency_id
        problems = []
        if exposure['at_bank']['count']:
            problems.append(_(
                "%(count)s cheque(s) totalling %(total)s are at the bank and "
                "their outcome is unknown.",
                count=exposure['at_bank']['count'],
                total=currency.format(exposure['at_bank']['amount'])))
        if exposure['bounced']['count']:
            problems.append(_(
                "%(count)s bounced cheque(s) totalling %(total)s have no "
                "resolution.",
                count=exposure['bounced']['count'],
                total=currency.format(exposure['bounced']['amount'])))
        if exposure['on_hand']['count']:
            problems.append(_(
                "%(count)s cheque(s) totalling %(total)s are physically held "
                "and must be returned to the customer or cancelled.",
                count=exposure['on_hand']['count'],
                total=currency.format(exposure['on_hand']['amount'])))
        if not problems:
            return True
        raise UserError(_(
            "%(operation)s cannot complete while physical cheque exposure is "
            "unresolved:\n\n%(list)s\n\n"
            "Cleared cheques are historical payment and stay as they are. "
            "Everything else has to be settled in Treasury first — use "
            "'Return to Customer' or 'Cancel' on each instrument.",
            operation=operation, list='\n'.join('- %s' % p for p in problems)))

    # ==================================================================
    # Settlement / cancellation previews get the paper position too
    # ==================================================================
    def _settlement_preview(self, settlement_date=None, discount_amount=0.0,
                            fee_amount=0.0):
        """M15 — the user must see what instruments are involved.

        Developer computes the money. This adds the paper, so a settlement
        quote never omits the twenty cheques someone will have to physically
        hand back.
        """
        preview = super()._settlement_preview(
            settlement_date=settlement_date, discount_amount=discount_amount,
            fee_amount=fee_amount)
        preview['check_exposure'] = self.get_check_exposure()
        return preview

    def _cancellation_preview(self, penalty_amount=0.0, forfeit_amount=0.0):
        preview = super()._cancellation_preview(
            penalty_amount=penalty_amount, forfeit_amount=forfeit_amount)
        preview['check_exposure'] = self.get_check_exposure()
        return preview
