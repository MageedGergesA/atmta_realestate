# -*- coding: utf-8 -*-
"""Shared fixtures for the PDC treasury suite.

The module had **no tests at all** before 0.2, which is how a headline feature
managed to ship broken on Odoo 18 (`_try_reconcile` dereferenced a field that
Odoo 18 removed) without anyone noticing.

Everything here builds the smallest believable treasury: a company with a bank
journal, a chart of accounts, a buyer, a contract with a real instalment
schedule, and a cheque. The interesting part is `_bank_journal`, which builds
**both** accounting configurations M7 asks for, because the difference between
them is the whole difference between "presented" and "cleared".
"""

from odoo import fields
from odoo.tests.common import TransactionCase

from odoo.addons.real_estate_developer.tests.test_contract import ContractCommon


class ChecksCommon(ContractCommon):
    """Developer's contract fixture, plus a bank and some paper."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Check = cls.env['realestate.check']
        cls.Deposit = cls.env['realestate.check.deposit']
        cls.Bounce = cls.env['realestate.check.bounce']
        cls.Allocation = cls.env['realestate.check.allocation']
        cls.Custody = cls.env['realestate.check.custody']
        cls.Presentation = cls.env['realestate.check.presentation']
        cls.Location = cls.env['realestate.check.location']

        cls.bank = cls.env['res.bank'].create({
            'name': 'National Bank of Testing',
            'bic': 'NBTGEGCX',
        })
        cls.other_bank = cls.env['res.bank'].create({'name': 'Second Bank'})

        cls.safe = cls.Location.create({
            'name': 'Treasury Safe', 'code': 'SAFE', 'kind': 'safe',
            'company_id': cls.company.id,
        })
        cls.bank_location = cls.Location.create({
            'name': 'At Bank', 'code': 'BANK', 'kind': 'bank',
            'company_id': cls.company.id,
        })
        cls.company.check_default_location_id = cls.safe.id

        # Two journals, one per accounting configuration. See `_bank_journal`.
        cls.journal_outstanding = cls._bank_journal(
            cls, 'Bank (Outstanding Receipts)', 'BNKO', outstanding=True)
        cls.journal_direct = cls._bank_journal(
            cls, 'Bank (Direct)', 'BNKD', outstanding=False)
        cls.journal = cls.journal_outstanding

        cls.penalty_product = cls.env['product.product'].create({
            'name': 'Returned Cheque Penalty',
            'type': 'service',
        })
        cls.company.check_bounce_penalty_product_id = cls.penalty_product.id

        # The security enforcement is real (M31), and the test user is OdooBot,
        # who belongs to no Checks group. Granting Treasury Officer here is
        # what lets the functional tests run at all — and the fact that it is
        # NEEDED is itself the proof that the gates are server-side rather
        # than decorative. `test_security_multicompany` asserts the refusals
        # from purpose-built users.
        cls.env.user.groups_id |= cls.env.ref(
            'real_estate_checks.group_checks_treasurer')

    # ==================================================================
    # Accounting configuration (M7)
    # ==================================================================
    def _bank_journal(self, name, code, outstanding=True):
        """Build a bank journal in one of Odoo 18's two payment configurations.

        **Outstanding** — the inbound method posts to an Outstanding Receipts
        account (`asset_current`, reconcilable). A payment stays `in_process`
        until a bank transaction matches its liquidity line, at which point
        Odoo moves it to `paid`. This is the configuration in which
        "presented" and "cleared" are genuinely different states, and it is
        what a real treasury runs.

        **Direct** — the method posts straight into the bank account
        (`asset_cash`). `action_post` marks the payment `paid` immediately;
        there is no separate reconciliation step to wait for. A cheque banked
        this way clears as soon as it is presented, which is correct for that
        configuration and must not be treated as a bug.

        Both are supported and both are tested.
        """
        env = self.env if hasattr(self, 'env') else self.env
        company = self.company if hasattr(self, 'company') else self.env.company
        journal = env['account.journal'].create({
            'name': name,
            'code': code,
            'type': 'bank',
            'company_id': company.id,
        })
        method_line = journal.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_method_id.code == 'manual')[:1]
        if not method_line:
            method_line = journal.inbound_payment_method_line_ids[:1]
        if not method_line:
            return journal

        if outstanding:
            account = env['account.account'].create({
                'name': 'Outstanding Receipts %s' % code,
                'code': 'OR%s' % code,
                'account_type': 'asset_current',
                'reconcile': True,
                'company_ids': [(6, 0, [company.id])],
            })
            method_line.payment_account_id = account.id
        else:
            # Straight into the bank account: `asset_cash`, so `action_post`
            # takes the payment to `paid` with no bank matching step.
            method_line.payment_account_id = journal.default_account_id.id
        return journal

    def _method_line(self, journal):
        line = journal.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_method_id.code == 'manual')[:1]
        return line or journal.inbound_payment_method_line_ids[:1]

    # ==================================================================
    # Cheques
    # ==================================================================
    _next_number = 1000

    def _check(self, amount=100000.0, due_date=None, state='registered',
               partner=None, number=None, journal=None, **kwargs):
        """A registered cheque, due today unless told otherwise."""
        type(self)._next_number += 1
        vals = {
            'partner_id': (partner or self.buyer).id,
            'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
            'bank_id': self.bank.id,
            'account_number': '1234567890',
            'check_number': number or '%09d' % type(self)._next_number,
            'amount': amount,
            'issue_date': fields.Date.context_today(self.env['res.partner']),
            'due_date': due_date or fields.Date.context_today(
                self.env['res.partner']),
            'journal_id': (journal or self.journal).id,
            'state': state,
        }
        vals.update(kwargs)
        return self.Check.create(vals)

    def _deposit(self, checks, journal=None, date=None, confirm=True):
        journal = journal or self.journal
        deposit = self.Deposit.create({
            'company_id': self.company.id,
            'journal_id': journal.id,
            'currency_id': self.company.currency_id.id,
            'deposit_date': date or fields.Date.context_today(
                self.env['res.partner']),
            'payment_method_line_id': self._method_line(journal).id,
            'check_ids': [(6, 0, checks.ids)],
        })
        if confirm:
            deposit.action_confirm()
        return deposit

    # ==================================================================
    # Banking
    # ==================================================================
    def _reconcile_with_bank(self, payment):
        """Simulate the bank transaction arriving and being matched.

        Deliberately goes through `account.bank.statement.line` and Odoo's own
        reconciliation rather than writing `payment.state = 'paid'`. The whole
        point of M8 is that clearance comes from Odoo; a test that faked the
        state would prove nothing.
        """
        move = payment.move_id
        if not move:
            return False
        liquidity = move.line_ids.filtered(
            lambda l: l.account_id == payment.outstanding_account_id)
        if not liquidity:
            return False

        statement_line = self.env['account.bank.statement.line'].create({
            'journal_id': payment.journal_id.id,
            'date': payment.date,
            'payment_ref': 'Cheque clearing %s' % payment.name,
            'partner_id': payment.partner_id.id,
            'amount': payment.amount,
        })
        counterpart = statement_line.move_id.line_ids.filtered(
            lambda l: l.account_id == payment.outstanding_account_id
            or (l.account_id.reconcile and l.id not in liquidity.ids))
        counterpart = counterpart.filtered(lambda l: not l.reconciled)
        if not counterpart:
            # Fall back to the suspense line, which is what an unmatched
            # statement line posts to.
            counterpart = statement_line.move_id.line_ids.filtered(
                lambda l: l.account_id != statement_line.journal_id.default_account_id)
            counterpart.account_id = payment.outstanding_account_id
        (liquidity | counterpart).reconcile()
        payment.invalidate_recordset()
        return statement_line

    def _signed_contract(self, **kwargs):
        """A signed contract with a real instalment schedule.

        `ContractCommon` stops at `_contract()`; the signed variant lives on
        Developer's `ChangeCommon`, which is a test module rather than a
        fixture, so it is rebuilt here from the same two calls.
        """
        contract = self._contract(payment_plan_id=self._plan().id, **kwargs)
        contract.action_sign()
        return contract

    def _rental_contract(self, partner, property_, **kwargs):
        vals = {
            'name': 'RENT-%s' % property_.id,
            'partner_id': partner.id,
            'property_id': property_.id,
            'start_date': fields.Date.context_today(self.env['res.partner']),
            'end_date': fields.Date.add(
                fields.Date.context_today(self.env['res.partner']), years=1),
            'currency_id': self.company.currency_id.id,
        }
        vals.update(kwargs)
        return self.env['realestate.contract'].create(vals)

    def _other_currency(self):
        """A second, active currency. `search` respects `active_test`, and in a
        fresh database only the company currency is active — so an unqualified
        search returns nothing and every 'multi-currency' test silently tests
        nothing."""
        currency = self.env['res.currency'].with_context(
            active_test=False).search(
                [('id', '!=', self.company.currency_id.id)], limit=1)
        currency.active = True
        return currency

    def _invoice_installment(self, installment):
        installment.action_generate_invoice()
        return installment.move_id
